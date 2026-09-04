"""
airmouse.persistence — local-only storage layer (v15.1 hardening).

PRIVACY MODEL (the contract this module implements)
---------------------------------------------------
1. LOCAL-ONLY.  Everything AirMouse learns lives under one directory:
   ``$AIRMOUSE_HOME`` if that environment variable is set (non-empty),
   otherwise ``~/.airmouse``.  There is no cloud path, no sync, no
   upload, and no network code in this module.  Storage is plain JSON.

2. METADATA-PREFERRED.  Stores keep counters, timestamps and learned
   parameters — not raw transcripts or content.  Interaction memory
   (airmouse.intelligence.memory) hard-scrubs content before anything
   is written; this module never adds content back.

3. USER-CONTROLLED.  The user owns the data and gets honest, complete
   lifecycle commands:

       airmouse memory status              # what exists, sizes, health
       airmouse memory export <path>       # portable JSON bundle out
       airmouse memory reset               # per-store backup + clear
       airmouse memory delete              # remove stores (backups kept)
       airmouse privacy                    # one-call privacy report

Crash safety: every write is atomic — data goes to a temporary file in
the destination directory, is flushed + fsynced, then os.replace()d
into place; the directory is fsynced too where the OS allows it.  A
crash can never leave a partial JSON file.  Integrity: each store
envelope carries a SHA-256 checksum of its data; a mismatched checksum
quarantines the file (renamed to ``<name>.json.corrupt-<epoch>``, the
newest 3 kept) and yields an empty store instead of trusting bad data.
Fail-closed, always.

Path safety: every store path is derived from airmouse_home() and
validated to stay inside it; store names containing separators, ``..``
or null bytes are rejected.  Export targets are user-chosen, so they
may live anywhere — but they are normalized (os.path.abspath) and any
``..`` component is rejected; existing files are only overwritten when
``overwrite=True`` is passed explicitly.

Compatibility note: ``airmouse.config`` pins its own paths to
``~/.airmouse`` at import time and is read-only for this release.  The
:func:`config_path_scope` shim temporarily rebinds those two module
constants when AIRMOUSE_HOME is overridden, so the config file follows
the active home directory without modifying config.py.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import contextlib
import datetime
import glob
import hashlib
import json
import os
import tempfile
import time
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

__all__ = [
    "SCHEMA_VERSION", "STORE_NAMES", "SUBDIR_NAMES",
    "airmouse_home", "ensure_dirs", "atomic_write_bytes",
    "atomic_write_json", "read_json", "PersistentStore",
    "get_store", "all_stores", "memory_status", "memory_export",
    "memory_reset", "memory_delete", "config_file_for_home",
    "config_path_scope",
]

#: bumped when the store envelope format changes
SCHEMA_VERSION = 1

#: environment variable that overrides the home directory
_HOME_ENV = "AIRMOUSE_HOME"

#: subdirectories created under the home directory
SUBDIR_NAMES = ("config", "memory", "skills", "workflows",
                "backups", "exports", "logs")

#: the named stores exposed to the CLI
STORE_NAMES = ("twin", "vocabulary", "skills", "workflows", "preferences")

#: how many quarantined corrupt copies to keep per store
_CORRUPT_KEEP = 3


# ---------------------------------------------------------------------------
# home directory + path safety
# ---------------------------------------------------------------------------

def airmouse_home() -> str:
    """AirMouse home directory.

    ``$AIRMOUSE_HOME`` (non-empty) wins; otherwise
    ``~/.airmouse``.  The result is absolute and ``~``-expanded.
    """
    raw = os.environ.get(_HOME_ENV, "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    return os.path.join(os.path.expanduser("~"), ".airmouse")


def _safe_name(name: str, what: str = "name") -> str:
    """Validate a single path component (no separators, no traversal)."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"invalid store {what}: must be a non-empty string")
    if ("\x00" in name or os.sep in name or "/" in name or "\\" in name
            or name in (".", "..") or os.path.basename(name) != name):
        raise ValueError(f"invalid store {what}: {name!r} "
                         f"(path separators and '..' are not allowed)")
    return name


def _under_home(subdir: str, filename: str) -> str:
    """Absolute path <home>/<subdir>/<filename>, validated to stay inside."""
    subdir = _safe_name(subdir, "subdir")
    filename = _safe_name(filename, "filename")
    home = os.path.abspath(airmouse_home())
    path = os.path.abspath(os.path.join(home, subdir, filename))
    if path != home and not path.startswith(home + os.sep):
        raise ValueError(f"path escapes airmouse home: {path}")
    return path


def _safe_export_path(path: str) -> str:
    """Normalize a user-chosen export path; reject '..' components.

    Exports are the one deliberate exception to "everything under the
    home directory" — the user picks where the bundle goes — but the
    path is still validated (no traversal components) and normalized.
    The raw string is checked BEFORE os.path.abspath collapses any
    ``x/..`` pairs away, so traversal attempts are rejected outright.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("export path must be a non-empty string")
    raw_parts = [p for p in path.replace("\\", "/").split("/") if p]
    if ".." in raw_parts:
        raise ValueError(f"export path must not contain '..': {path!r}")
    target = os.path.abspath(os.path.expanduser(path))
    return target


def ensure_dirs() -> str:
    """Create the home directory + all subdirectories.  Idempotent."""
    home = airmouse_home()
    os.makedirs(home, exist_ok=True)
    for sub in SUBDIR_NAMES:
        os.makedirs(os.path.join(home, sub), exist_ok=True)
    return home


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _iso_from_ts(ts: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(
            ts, datetime.timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# atomic writes / JSON IO
# ---------------------------------------------------------------------------

def _fsync_dir(directory: str) -> None:
    """fsync a directory so the rename itself is durable (best effort)."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return  # e.g. Windows — directory fsync unsupported
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Write bytes atomically: temp file in the same dir, fsync, replace.

    A crash at any point leaves either the old file or the new file —
    never a partial one.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".airmouse-tmp-",
                               suffix=".part", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(directory)


def atomic_write_json(path: str, obj: Any) -> None:
    """Write JSON atomically (UTF-8, ensure_ascii=False, sort_keys=True)."""
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1)
    atomic_write_bytes(path, text.encode("utf-8"))


def read_json(path: str) -> dict:
    """Read a JSON file and return the dict.

    Raises FileNotFoundError for a missing file and ValueError for
    undecodable or non-dict JSON (fail-closed, never partial data).
    """
    with open(path, "rb") as f:
        raw = f.read()
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"undecodable JSON in {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError(f"expected a JSON object in {path}, "
                         f"got {type(obj).__name__}")
    return obj


def _checksum(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# PersistentStore
# ---------------------------------------------------------------------------

class PersistentStore:
    """One named JSON store under ``<home>/<subdir>/<name>.json``.

    The on-disk envelope is::

        {"schema_version": N,
         "checksum": "<sha256 of canonical data JSON>",
         "saved_at": "<iso8601 utc>",
         "data": {...}}

    Checksum mismatch / undecodable JSON / malformed envelope: the file
    is quarantined as ``<name>.json.corrupt-<epoch>`` (newest
    ``_CORRUPT_KEEP`` copies kept) and ``load()`` returns ``{}`` with
    the reason recorded on ``self.last_corruption`` — fail-closed.
    """

    def __init__(self, name: str, schema_version: int = SCHEMA_VERSION,
                 migrations: Optional[Dict[int, Callable[[dict], dict]]] = None,
                 subdir: str = "memory") -> None:
        self.name = _safe_name(name)
        self.schema_version = int(schema_version)
        self.migrations: Dict[int, Callable[[dict], dict]] = dict(
            migrations or {})
        self.subdir = _safe_name(subdir, "subdir")
        #: reason + path of the last corrupt load (None when healthy)
        self.last_corruption: Optional[Dict[str, str]] = None

    # -- paths ------------------------------------------------------------

    @property
    def path(self) -> str:
        """Validated absolute path of this store's file."""
        return _under_home(self.subdir, self.name + ".json")

    def exists(self) -> bool:
        return os.path.exists(self.path)

    # -- write ------------------------------------------------------------

    def save(self, data: dict) -> None:
        """Atomically persist ``data`` wrapped in a versioned envelope."""
        if not isinstance(data, dict):
            raise TypeError("PersistentStore.save expects a dict")
        envelope = {
            "schema_version": self.schema_version,
            "checksum": _checksum(data),
            "saved_at": _utcnow_iso(),
            "data": data,
        }
        atomic_write_json(self.path, envelope)
        self.last_corruption = None

    # -- read -------------------------------------------------------------

    def load(self) -> dict:
        """Load the store; corrupt/newer-schema files yield ``{}``."""
        path = self.path
        if not os.path.exists(path):
            self.last_corruption = None
            return {}
        try:
            envelope = read_json(path)
        except (OSError, ValueError) as exc:
            return self._quarantine(path, f"undecodable: "
                                          f"{type(exc).__name__}")
        if not isinstance(envelope, dict) or \
                not isinstance(envelope.get("schema_version"), int) or \
                not isinstance(envelope.get("checksum"), str):
            return self._quarantine(path, "malformed envelope")
        version = envelope["schema_version"]
        data = envelope.get("data")

        if version > self.schema_version:
            self.last_corruption = {"path": path,
                                    "reason": "newer schema "
                                              f"(file v{version} > "
                                              f"store v{self.schema_version})"}
            return {}

        if _checksum(data) != envelope["checksum"]:
            return self._quarantine(path, "checksum mismatch")

        if version < self.schema_version:
            migrated = self._migrate(path, data, version)
            if migrated is None:
                return {}  # _migrate recorded the corruption reason
            self.save(migrated)
            return migrated

        self.last_corruption = None
        return data if isinstance(data, dict) else {}

    def _migrate(self, path: str, data: Any, from_version: int) -> Optional[dict]:
        """Apply migrations ascending; None means fail-closed corruption."""
        current = data if isinstance(data, dict) else {}
        for version in range(from_version, self.schema_version):
            fn = self.migrations.get(version)
            if fn is None:
                self.last_corruption = {
                    "path": path,
                    "reason": f"missing migration v{version}->v{version + 1}"}
                return None
            try:
                migrated = fn(current)
            except Exception as exc:
                self.last_corruption = {
                    "path": path,
                    "reason": f"migration v{version} failed: "
                              f"{type(exc).__name__}: {exc}"}
                return None
            if not isinstance(migrated, dict):
                self.last_corruption = {
                    "path": path,
                    "reason": f"migration v{version} returned "
                              f"{type(migrated).__name__}, expected dict"}
                return None
            current = migrated
        return current

    # -- corruption handling ------------------------------------------------

    def _quarantine(self, original: str, reason: str) -> dict:
        """Rename the bad file to .corrupt-<epoch> and return {}."""
        stamp = int(time.time())
        base = f"{original}.corrupt-{stamp}"
        target, n = base, 1
        while os.path.exists(target):
            n += 1
            target = f"{base}-{n}"
        try:
            os.replace(original, target)  # same dir → atomic rename
        except OSError:
            self.last_corruption = {"path": original, "reason": reason}
            return {}
        self._prune_corrupt()
        self.last_corruption = {"path": target, "original": original,
                                "reason": reason}
        return {}

    def _prune_corrupt(self) -> None:
        pattern = self.path + ".corrupt-*"
        copies = glob.glob(pattern)
        copies.sort(key=lambda p: (os.path.getmtime(p), p), reverse=True)
        for stale in copies[_CORRUPT_KEEP:]:
            try:
                os.unlink(stale)
            except OSError:
                pass

    # -- lifecycle ------------------------------------------------------------

    def export_to(self, path: str, overwrite: bool = False) -> str:
        """Portable JSON copy (data + schema_version) to a user path.

        Fails with FileExistsError unless ``overwrite=True``.
        """
        target = _safe_export_path(path)
        if os.path.exists(target) and not overwrite:
            raise FileExistsError(
                f"refusing to overwrite existing export: {target} "
                f"(pass overwrite=True)")
        payload = {"format": "airmouse-store-export", "name": self.name,
                   "schema_version": self.schema_version,
                   "data": self.load()}
        atomic_write_json(target, payload)
        return target

    def reset(self) -> dict:
        """Back up the current file under <home>/backups/, then clear."""
        backup: Optional[str] = None
        if os.path.exists(self.path):
            try:
                with open(self.path, "rb") as f:
                    raw = f.read()
                stamp = int(time.time())
                backups_dir = os.path.join(ensure_dirs(), "backups")
                target = os.path.join(backups_dir,
                                      f"{self.name}-{stamp}.json")
                n = 1
                candidate = target
                while os.path.exists(candidate):
                    n += 1
                    candidate = os.path.join(
                        backups_dir, f"{self.name}-{stamp}-{n}.json")
                atomic_write_bytes(candidate, raw)
                backup = candidate
            except OSError:
                backup = None
        self.save({})
        return {"name": self.name, "backup": backup, "cleared": True}

    def delete(self) -> bool:
        """Remove the store file + any quarantined copies.

        Backups under <home>/backups/ are deliberately kept.
        """
        removed = False
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
                removed = True
        except OSError:
            return removed
        for leftover in glob.glob(self.path + ".corrupt-*"):
            try:
                os.remove(leftover)
                removed = True
            except OSError:
                pass
        self.last_corruption = None
        return removed

    # -- status ------------------------------------------------------------

    def status(self) -> dict:
        """Honest health report for this store (no content leaked)."""
        path = self.path
        info: Dict[str, Any] = {
            "name": self.name, "path": path, "exists": False,
            "size_bytes": 0, "schema_version": None, "checksum_ok": None,
            "corrupted_last_load": self.last_corruption is not None,
            "mtime_iso": None, "records": 0,
        }
        if not os.path.exists(path):
            return info
        try:
            stat = os.stat(path)
        except OSError:
            return info
        info["exists"] = True
        info["size_bytes"] = int(stat.st_size)
        info["mtime_iso"] = _iso_from_ts(stat.st_mtime)
        try:
            envelope = read_json(path)
        except (OSError, ValueError):
            info["checksum_ok"] = False
            return info
        if not isinstance(envelope, dict):
            info["checksum_ok"] = False
            return info
        version = envelope.get("schema_version")
        info["schema_version"] = version if isinstance(version, int) else None
        data = envelope.get("data")
        info["records"] = len(data) if isinstance(data, dict) else 0
        info["checksum_ok"] = (isinstance(envelope.get("checksum"), str)
                               and envelope["checksum"] == _checksum(data))
        return info


# ---------------------------------------------------------------------------
# named stores (lazy singletons)
# ---------------------------------------------------------------------------

_STORE_CACHE: Dict[Tuple[str, str], PersistentStore] = {}


def get_store(name: str) -> PersistentStore:
    """The named store (one instance per home+name).  ValueError if unknown."""
    if name not in STORE_NAMES:
        raise ValueError(f"unknown store {name!r}; "
                         f"valid stores: {', '.join(STORE_NAMES)}")
    key = (airmouse_home(), name)
    store = _STORE_CACHE.get(key)
    if store is None:
        store = PersistentStore(name, schema_version=SCHEMA_VERSION)
        _STORE_CACHE[key] = store
    return store


def all_stores() -> Dict[str, PersistentStore]:
    """All named stores as a dict (lazy singletons)."""
    return {name: get_store(name) for name in STORE_NAMES}


# ---------------------------------------------------------------------------
# CLI facade commands
# ---------------------------------------------------------------------------

def memory_status() -> dict:
    """`airmouse memory status` — home + per-store health, no content."""
    return {"home": airmouse_home(),
            "stores": {name: store.status()
                       for name, store in all_stores().items()}}


def memory_export(path: str, overwrite: bool = False) -> dict:
    """`airmouse memory export <path>` — bundle every store into one JSON.

    The bundle contains data + schema_version per store and nothing is
    sent anywhere; it lands where the user points it.
    """
    target = _safe_export_path(path)
    if os.path.exists(target) and not overwrite:
        raise FileExistsError(
            f"refusing to overwrite existing export: {target} "
            f"(pass overwrite=True)")
    stores: Dict[str, Any] = {}
    for name in STORE_NAMES:
        store = get_store(name)
        stores[name] = {"schema_version": store.schema_version,
                        "data": store.load()}
    payload = {"format": "airmouse-memory-export",
               "schema_version": SCHEMA_VERSION,
               "exported_at": _utcnow_iso(),
               "home": airmouse_home(), "stores": stores}
    atomic_write_json(target, payload)
    return {"path": target, "stores": list(stores),
            "bytes": int(os.path.getsize(target))}


def memory_reset() -> dict:
    """`airmouse memory reset` — per-store backup + clear.

    Backups land in <home>/backups/ and are kept.
    """
    stores = {}
    for name, store in all_stores().items():
        result = store.reset()
        stores[name] = {"backup": result["backup"],
                        "cleared": result["cleared"]}
    return {"home": airmouse_home(), "backups_kept": True,
            "note": "pre-reset backups preserved under "
                    "<home>/backups/", "stores": stores}


def memory_delete() -> dict:
    """`airmouse memory delete` — remove store files (backups are KEPT)."""
    stores = {}
    for name, store in all_stores().items():
        stores[name] = {"deleted": store.delete()}
    return {"home": airmouse_home(), "backups_kept": True,
            "note": "store files removed; backups under <home>/backups/ "
                    "were NOT deleted — remove them yourself if you "
                    "want everything gone", "stores": stores}


# ---------------------------------------------------------------------------
# config-path compatibility shim (airmouse.config is read-only for us)
# ---------------------------------------------------------------------------

def config_file_for_home() -> str:
    """The config.toml path that matches airmouse_home()."""
    return os.path.join(airmouse_home(), "config.toml")


@contextlib.contextmanager
def config_path_scope() -> Iterator[str]:
    """Point ``airmouse.config`` at the AIRMOUSE_HOME config file.

    ``airmouse.config`` computes CONFIG_DIR/CONFIG_PATH from ``~`` at
    import time.  When AIRMOUSE_HOME overrides the home directory, this
    scope temporarily rebinds those two module constants so
    ``Config().save_defaults()`` / ``Config().load()`` operate on the
    active home's config file — without modifying config.py.  The
    original values are always restored.
    """
    target = config_file_for_home()
    default_home = os.path.join(os.path.expanduser("~"), ".airmouse")
    if os.path.abspath(airmouse_home()) == os.path.abspath(default_home):
        yield target
        return
    from . import config as _config
    old_dir, old_path = _config.CONFIG_DIR, _config.CONFIG_PATH
    _config.CONFIG_DIR = airmouse_home()
    _config.CONFIG_PATH = target
    try:
        yield target
    finally:
        _config.CONFIG_DIR = old_dir
        _config.CONFIG_PATH = old_path
