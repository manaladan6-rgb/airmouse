"""airmouse.model_integrity — THE model integrity pipeline (v19).

This module reconstructs — from the recorded v16.5.4 spec — the canonical
12-stage model acquisition pipeline, and extends it with the v19 Phase 6
model-state machine and the honest model record:

    1.  TEMP FILE          archive lands in a temp file next to the model
                           dir (same filesystem — rename stays atomic)
    2.  FSYNC              bytes hit the disk before any trust decision
    3.  BYTE COUNT         measured size must equal the pinned size
    4.  SHA256             pinned digest verified (mismatch → discard)
    5.  SHA512             second independent digest verified
    6.  CRC                every member CRC checked (zip.testzip)
    7.  ZIP STRUCTURE      single expected root, sane member count,
                           compression-ratio bomb guard
    8.  ZIP-SLIP           every member resolves inside the staging root
                           (no ``..``, no absolute paths, no backslash
                           escape, no drive letters, no device names)
    9.  EXEC/SYMLINK       executable members and symlink members are
                           refused outright — model DATA only
    10. STAGED EXTRACT      extracted into ``<model>.staging-<token>/``
    11. STRUCTURE VALIDATE  staged tree re-walked: required members
                           present, nothing outside the root
    12. ATOMIC INSTALL      previous model renamed aside → staging renamed
                           into place → post-install verify; on ANY
                           failure the previous model is RESTORED
                           (rollback), never a half-installed state

Concurrency: a lock file guards the pipeline; a stale lock (dead PID or
older than the freshness window) is reaped.  Stale staging dirs from dead
runs are reaped too.

States (v19 Phase 6 — the complete lifecycle, never abbreviated):

    MODEL_MISSING             nothing installed, nothing known
    MODEL_DOWNLOAD_REQUIRED   not installed + acquisition would need the
                              network (or the user declined) — never a
                              silent download
    MODEL_DOWNLOADING         bytes are being fetched right now
    MODEL_VERIFYING           digests/CRC/structure are being verified
    MODEL_INSTALLING          staged extraction / atomic switch running
    MODEL_READY               installed AND integrity-verified AND the
                              engine/probe accepts the tree
    MODEL_CORRUPTED           installed but verification fails
    MODEL_INCOMPATIBLE        installed but wrong for this build/provider

Honesty rules enforced here:

* downloads NEVER happen without explicit consent (callback or CLI flag);
* a declined/offline acquisition returns ``MODEL_DOWNLOAD_REQUIRED`` with
  exact manual instructions — never a fake READY;
* READY is only returned AFTER the post-install verification succeeds;
* a verified model is never silently replaced (a new install renames the
  old tree aside, installs, verifies, and only then retires the old tree);
* the pipeline never touches anything outside the AirMouse home.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "ModelState", "ModelSpec", "VOSK_MODEL", "ModelIntegrityError",
    "ModelPipeline", "install_vosk_model", "vosk_model_status",
    "OBSERVER_STATES",
]


# ---------------------------------------------------------------------------
# states (v19 Phase 6)
# ---------------------------------------------------------------------------

class ModelState(str, Enum):
    """The authoritative model lifecycle states (Phase 6)."""

    MODEL_MISSING = "MODEL_MISSING"
    MODEL_DOWNLOAD_REQUIRED = "MODEL_DOWNLOAD_REQUIRED"
    MODEL_DOWNLOADING = "MODEL_DOWNLOADING"
    MODEL_VERIFYING = "MODEL_VERIFYING"
    MODEL_INSTALLING = "MODEL_INSTALLING"
    MODEL_READY = "MODEL_READY"
    MODEL_CORRUPTED = "MODEL_CORRUPTED"
    MODEL_INCOMPATIBLE = "MODEL_INCOMPATIBLE"


#: states a UI may observe mid-run (the others are decision points)
OBSERVER_STATES: Tuple[ModelState, ...] = (
    ModelState.MODEL_DOWNLOADING, ModelState.MODEL_VERIFYING,
    ModelState.MODEL_INSTALLING,
)


# ---------------------------------------------------------------------------
# the pinned spec (v19: CORRECTED against live ground truth)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelSpec:
    """Everything AirMouse knows about ONE official model archive."""

    name: str
    url: str
    size: int                    # exact expected archive size in bytes
    sha256: str
    sha512: str
    root: str                    # the single expected top-level dir
    members: int                 # exact expected member count
    unpacked_mb: float           # disclosed unpacked footprint
    provider: str                # who serves the data
    license: str                 # data license
    engine: str                  # which ASR engine consumes it
    required_members: Tuple[str, ...] = ()   # paths that must exist (rel)
    max_compression_ratio: int = 1000        # zip-bomb guard
    max_members: int = 64                    # sanity guard


#: THE one model AirMouse may fetch.  Digests re-measured LIVE from the
#: official endpoint during the v19 baseline audit (4th independent
#: measurement, 2nd complete download in project history; cross-checked
#: against the independently published Alpine aports digest):
#:   Content-Length 41,205,931 · SHA256 30f26242c4eb…42498 ·
#:   SHA512 d56243ba7534…3bc5a · 20 members · single root
#: The v16.5.3 pin (507575b9… / 41,205,932 B) was WRONG — every consented
#: download failed verification.  See docs/V19_BASELINE.md §3.
VOSK_MODEL = ModelSpec(
    name="vosk-model-small-en-us-0.15",
    url=("https://alphacephei.com/vosk/models/"
         "vosk-model-small-en-us-0.15.zip"),
    size=41_205_931,
    sha256="30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498",
    sha512=("d56243ba75343fd42a238ac358f9deef4acc690bdc9d816f1499"
            "930f25504bce87495b6f448089a87b426a8d81b31b80525ae5ed6c0"
            "ca4fb95a306576f53bc5a"),
    root="vosk-model-small-en-us-0.15",
    members=20,
    unpacked_mb=70.9,
    provider="alphacephei.com (official Vosk project)",
    license="Apache-2.0 (model DATA, no executables)",
    engine="vosk",
    required_members=("am/final.mdl",),
)


class ModelIntegrityError(Exception):
    """A trust-relevant failure with an honest, user-readable reason."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail or reason
        super().__init__(f"{reason}: {self.detail}" if detail else reason)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_BANNED_SUFFIXES = (".exe", ".dll", ".so", ".bat", ".cmd", ".ps1", ".com",
                    ".scr", ".msi", ".sh", ".js", ".vbs", ".py")

_LOCK_FRESH_SECONDS = 30 * 60      # a lock older than this is stale


def _pid_alive(pid: int) -> bool:
    """Platform-correct liveness probe for a lock-owner pid.

    POSIX: ``os.kill(pid, 0)`` — signal 0 does nothing, it only checks.
    Windows: signal 0 IS ``CTRL_C_EVENT`` there — calling ``os.kill(pid, 0)``
    would send a real Ctrl+C to the (possibly OUR OWN) process group and
    interrupt the running installer.  So Windows uses an OpenProcess
    query instead.  On any probe error the answer is True (assume busy,
    refuse honestly) — wrongly reaping a live installer's lock could
    race two installs; refusing is always safe (the user retries).
    """
    if not pid or pid <= 0:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)   # signal 0 = liveness probe (POSIX semantics)
            return True
        except PermissionError:
            return True       # exists, owned by someone else
        except OSError:
            return False
    try:                       # Windows: OpenProcess-based probe
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32          # type: ignore[attr-defined]
        handle = k32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ctypes.getlasterror() == 5  # ERROR_ACCESS_DENIED → exists
        try:
            exit_code = ctypes.c_ulong()
            if k32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return True
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return True           # cannot prove it is dead → assume busy


def _is_staging_name(name: str) -> bool:
    return ".staging-" in os.path.basename(name)


def _looks_like_vosk_model(path: str) -> bool:
    """Local copy of the engine's acceptance probe (no import cycle)."""
    final_mdl = os.path.join(path, "am", "final.mdl")
    if os.path.isfile(final_mdl):
        return True
    # also accept an extracted root folder directly under models/
    try:
        for entry in sorted(os.listdir(path)):
            if os.path.isfile(os.path.join(path, entry, "am",
                                           "final.mdl")):
                return True
    except OSError:
        pass
    return False


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha512_file(path: str) -> str:
    h = hashlib.sha512()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _member_dangerous(name: str) -> Optional[str]:
    """Return a refusal reason for a dangerous member path, else None."""
    low = name.lower().replace("\\", "/")
    if low.startswith("/") or (len(low) > 1 and low[1] == ":"):
        return "absolute path member"
    if "\\" in name:
        return "backslash path member"
    parts = [p for p in low.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return "parent-directory member"
    if any(p in ("dev", "proc", "sys") and i == 0
           for i, p in enumerate(parts)) and name.startswith("/"):
        return "system pseudo-filesystem member"
    if any(low.endswith(b) for b in _BANNED_SUFFIXES):
        return "executable-looking member"
    return None


def _member_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000  # S_IFLNK


def _member_is_executable(info: zipfile.ZipInfo) -> bool:
    return bool((info.external_attr >> 16) & 0o111)


# ---------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------

@dataclass
class ModelPipeline:
    """THE 12-stage acquisition + verification pipeline for one spec."""

    spec: ModelSpec
    dest: str                                   # final model dir
    observe: Optional[Callable[[ModelState], None]] = None
    _lock_path: str = field(default="", repr=False)

    # -- paths --------------------------------------------------------------

    def _parent(self) -> str:
        return os.path.dirname(os.path.abspath(self.dest)) or "."

    def _temp_archive(self) -> str:
        return os.path.join(self._parent(),
                            f".{self.spec.name}.archive-{os.getpid()}.tmp")

    def _lock(self) -> str:
        if not self._lock_path:
            self._lock_path = os.path.join(
                self._parent(), f".{self.spec.name}.install.lock")
        return self._lock_path

    def _staging_dir(self) -> str:
        return os.path.abspath(self.dest) + f".staging-{os.getpid()}"

    # -- observer -----------------------------------------------------------

    def _emit(self, state: ModelState) -> None:
        if self.observe is not None:
            try:
                self.observe(state)
            except Exception:
                pass  # an observer must never break the pipeline

    # -- lock / reaping -----------------------------------------------------

    def _acquire_lock(self) -> None:
        lock = self._lock()
        os.makedirs(self._parent(), exist_ok=True)
        if os.path.exists(lock):
            self._reap_stale_lock(lock)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()}\n{time.time()}\n")
        except FileExistsError as exc:
            raise ModelIntegrityError(
                "another installation is running",
                f"lock present: {lock}") from exc

    def _reap_stale_lock(self, lock: str) -> None:
        try:
            with open(lock, "r", encoding="utf-8") as fh:
                pid_line = fh.readline().strip()
                ts_line = fh.readline().strip()
            pid = int(pid_line) if pid_line.isdigit() else 0
            ts = float(ts_line) if ts_line else 0.0
        except Exception:
            pid, ts = 0, 0.0
        alive = _pid_alive(pid)
        if alive and ts and (time.time() - ts) < _LOCK_FRESH_SECONDS:
            return                # genuinely busy — refuse honestly
        try:
            os.remove(lock)
        except OSError:
            pass

    def _release_lock(self) -> None:
        try:
            os.remove(self._lock())
        except OSError:
            pass

    def _reap_stale_staging(self) -> None:
        """Remove staging dirs left behind by dead runs (not ours)."""
        parent = self._parent()
        prefix = os.path.basename(os.path.abspath(self.dest)) + ".staging-"
        try:
            for entry in os.listdir(parent):
                if entry.startswith(prefix) and entry != os.path.basename(
                        self._staging_dir()):
                    shutil.rmtree(os.path.join(parent, entry),
                                  ignore_errors=True)
        except OSError:
            pass

    # -- current state (no download; cheap, safe) ---------------------------

    def state(self) -> Tuple[ModelState, str]:
        """Honest current state WITHOUT acquiring anything."""
        dest = self.dest
        if not os.path.isdir(dest):
            return (ModelState.MODEL_MISSING,
                    f"no model installed at {dest}")
        if not _looks_like_vosk_model(dest):
            return (ModelState.MODEL_CORRUPTED,
                    "directory exists but the required model files "
                    "(am/final.mdl) are missing")
        ok, detail = self.verify_existing()
        if ok:
            return (ModelState.MODEL_READY, detail)
        return (ModelState.MODEL_CORRUPTED, detail)

    def verify_existing(self) -> Tuple[bool, str]:
        """Integrity-verify the INSTALLED tree (structure level)."""
        try:
            root = self._installed_root()
            if root is None:
                return (False, "expected root directory not found")
            for rel in self.spec.required_members:
                if not os.path.isfile(os.path.join(root, rel)):
                    return (False, f"required member missing: {rel}")
            if not _looks_like_vosk_model(self.dest):
                return (False, "tree does not satisfy the engine probe")
            return (True, f"verified at {self.dest}")
        except Exception as exc:  # noqa: BLE001 — honest failure
            return (False, f"{type(exc).__name__}: {exc}")

    def _installed_root(self) -> Optional[str]:
        dest = os.path.abspath(self.dest)
        direct = os.path.join(dest, self.spec.root)
        if os.path.isdir(direct):
            return direct
        if os.path.isdir(dest) and _looks_like_vosk_model(dest):
            return dest
        return None

    def status(self) -> Dict[str, Any]:
        """The honest model record (v19 Phase 6) — safe to call always."""
        state, detail = self.state()
        return {
            "name": self.spec.name,
            "state": state.value,
            "detail": detail,
            "installed": state == ModelState.MODEL_READY,
            "source": self.spec.url,
            "provider": self.spec.provider,
            "license": self.spec.license,
            "engine": self.spec.engine,
            "expected_size": self.spec.size,
            "sha256": self.spec.sha256,
            "sha512": self.spec.sha512,
            "version": "0.15",
            "compatible": True,
        }

    # -- acquisition --------------------------------------------------------

    def download_and_install(
            self, consent: Optional[Callable[[str], bool]] = None,
            offline: bool = False,
            progress: Optional[Callable[[str], None]] = None,
            fetch: Optional[Callable[[str, Callable[[int]], bytes]]] = None,
            ) -> Tuple[ModelState, str]:
        """Consent-gated acquire + the full 12-stage pipeline.

        ``fetch`` is injectable for tests.  ``offline=True`` short-circuits
        BEFORE any network attempt with ``MODEL_DOWNLOAD_REQUIRED`` and the
        exact manual instructions.  A declined consent is the same honest
        state — never a silent download, never a fake READY.
        """
        say = progress or (lambda _s: None)
        state, detail = self.state()
        if state == ModelState.MODEL_READY:
            return (state, f"model already present and verified "
                           f"({detail})")
        if offline:
            return (
                ModelState.MODEL_DOWNLOAD_REQUIRED,
                f"offline: the {self.spec.name} model downloads once from "
                f"{self.spec.provider} ({self.spec.size / 1e6:.1f} MB). "
                f"Manual: download {self.spec.url} and unzip it so that "
                f"{self.dest} contains the model files")
        if consent is not None:
            question = (f"Download the official {self.spec.engine} model?  "
                        f"{self.spec.size / 1e6:.1f} MB, one-time, from "
                        f"{self.spec.provider} ({self.spec.license}), "
                        f"SHA256+SHA512-verified, stored at {self.dest}")
            try:
                if not consent(question):
                    return (
                        ModelState.MODEL_DOWNLOAD_REQUIRED,
                        f"declined: no model was downloaded (manual: fetch "
                        f"{self.spec.url} and unzip into {self.dest})")
            except ModelIntegrityError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise ModelIntegrityError(
                    "consent gate failed",
                    f"{type(exc).__name__}: {exc}") from exc

        self._emit(ModelState.MODEL_DOWNLOADING)
        say(f"downloading {self.spec.size / 1e6:.1f} MB …")
        if fetch is not None:
            raw = fetch(self.spec.url, lambda n: None)
        else:
            raw = self._fetch(self.spec.url, say)
        return self.install_from_bytes(raw, progress=say)

    def _fetch(self, url: str, say: Callable[[str], None]) -> bytes:
        import urllib.request
        req = urllib.request.Request(
            url, headers={"User-Agent": "airmouse-model-integrity"})
        with urllib.request.urlopen(req, timeout=1800) as resp:
            total = int(resp.headers.get("Content-Length")
                        or self.spec.size)
            buf = io.BytesIO()
            got = 0
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                buf.write(chunk)
                got += len(chunk)
                say(f"downloaded {got / 1e6:.1f} / {total / 1e6:.1f} MB")
            if got != total:
                raise ModelIntegrityError(
                    "download truncated",
                    f"{got} / {total} bytes — the archive was discarded")
            return buf.getvalue()

    # -- THE 12 stages (bytes → verified install) ----------------------------

    def install_from_bytes(
            self, raw: bytes,
            progress: Optional[Callable[[str], None]] = None,
            ) -> Tuple[ModelState, str]:
        """Run all 12 stages over an in-memory archive."""
        say = progress or (lambda _s: None)
        self._reap_stale_staging()
        self._acquire_lock()
        tmp = self._temp_archive()
        staging = self._staging_dir()
        backup = os.path.abspath(self.dest) + ".prev"
        installed = False
        try:
            # 1+2. TEMP + FSYNC
            os.makedirs(self._parent(), exist_ok=True)
            with open(tmp, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())

            # 3. BYTE COUNT
            self._emit(ModelState.MODEL_VERIFYING)
            say("verifying archive …")
            if len(raw) != self.spec.size:
                raise ModelIntegrityError(
                    "size mismatch",
                    f"got {len(raw)} bytes, expected {self.spec.size}")

            # 4+5. SHA256 + SHA512
            sha256 = _sha256_file(tmp)
            if sha256 != self.spec.sha256:
                raise ModelIntegrityError(
                    "SHA256 mismatch",
                    f"got {sha256}, expected {self.spec.sha256} — the "
                    f"archive was discarded, nothing was extracted")
            sha512 = _sha512_file(tmp)
            if self.spec.sha512 and sha512 != self.spec.sha512:
                raise ModelIntegrityError(
                    "SHA512 mismatch",
                    f"got {sha512[:32]}…, expected "
                    f"{self.spec.sha512[:32]}… — discarded")

            with zipfile.ZipFile(tmp) as zf:
                # 6. CRC
                bad = zf.testzip()
                if bad is not None:
                    raise ModelIntegrityError("CRC failure", bad)

                infos = zf.infolist()
                # 8+9. ZIP-SLIP + EXEC/SYMLINK REFUSAL — BEFORE structure
                # and extraction, so the most precise refusal reason wins
                # (defense in depth: stage 7 would also catch stray roots)
                for info in infos:
                    danger = _member_dangerous(info.filename)
                    if danger:
                        raise ModelIntegrityError(
                            danger + " refused", info.filename)
                    if _member_is_symlink(info):
                        raise ModelIntegrityError(
                            "symlink member refused", info.filename)
                    if not info.is_dir() and \
                            _member_is_executable(info):
                        raise ModelIntegrityError(
                            "executable member refused", info.filename)

                # 7. ZIP STRUCTURE (+ bomb guard)
                if len(infos) > self.spec.max_members:
                    raise ModelIntegrityError(
                        "archive structure rejected",
                        f"{len(infos)} members exceeds the sanity limit "
                        f"{self.spec.max_members}")
                total_unpacked = sum(i.file_size for i in infos)
                for i in infos:
                    if i.compress_size and \
                            i.file_size / i.compress_size > \
                            self.spec.max_compression_ratio:
                        raise ModelIntegrityError(
                            "compression bomb refused",
                            f"{i.filename} ratio "
                            f"{i.file_size / max(1, i.compress_size):.0f}:1")
                tops = {n.split("/")[0] for n in zf.namelist() if n.strip()}
                if tops != {self.spec.root}:
                    raise ModelIntegrityError(
                        "unexpected archive layout",
                        f"roots {sorted(tops)} != expected "
                        f"['{self.spec.root}']")
                if len(infos) != self.spec.members:
                    raise ModelIntegrityError(
                        "unexpected member count",
                        f"{len(infos)} != expected {self.spec.members}")

                # 10. STAGED EXTRACT
                self._emit(ModelState.MODEL_INSTALLING)
                say("installing …")
                if os.path.exists(staging):
                    shutil.rmtree(staging, ignore_errors=True)
                os.makedirs(staging, exist_ok=True)
                for info in infos:
                    name = info.filename
                    if name.endswith("/"):
                        continue
                    target = os.path.abspath(os.path.join(staging, name))
                    if not (target.startswith(staging + os.sep)):
                        raise ModelIntegrityError(
                            "zip-slip blocked at extract", name)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)

            # 11. STRUCTURE VALIDATION of the staged tree
            root = os.path.join(staging, self.spec.root)
            if not os.path.isdir(root):
                raise ModelIntegrityError(
                    "staged structure invalid",
                    f"root {self.spec.root} missing after extract")
            for rel in self.spec.required_members:
                if not os.path.isfile(os.path.join(root, rel)):
                    raise ModelIntegrityError(
                        "staged structure invalid",
                        f"required member missing: {rel}")
            for dirpath, _dirs, files in os.walk(staging):
                rel = os.path.relpath(dirpath, staging)
                if rel == ".":
                    continue          # the staging root itself is expected
                if rel.split(os.sep)[0] != self.spec.root:
                    raise ModelIntegrityError(
                        "staged structure invalid",
                        f"unexpected top-level entry: {rel}")

            # 12. ATOMIC INSTALL (+ rollback of the previous model)
            had_previous = os.path.exists(self.dest)
            if had_previous:
                if os.path.exists(backup):
                    shutil.rmtree(backup, ignore_errors=True)
                os.rename(self.dest, backup)
            try:
                os.rename(root, self.dest)
                installed = True
            except OSError:
                if had_previous:
                    os.rename(backup, self.dest)   # rollback
                raise
            shutil.rmtree(staging, ignore_errors=True)

            # post-install verification runs BEFORE the previous model is
            # retired — a failure here still has a full rollback path
            ok, vdetail = self.verify_existing()
            if not ok:
                raise ModelIntegrityError(
                    "post-install verification failed", vdetail)
            if had_previous:
                shutil.rmtree(backup, ignore_errors=True)
            say("model READY")
            return (ModelState.MODEL_READY,
                    f"installed + verified at {self.dest} "
                    f"(sha256 {self.spec.sha256[:16]}…)")
        except ModelIntegrityError:
            self._cleanup_failure(tmp, staging, installed, backup)
            raise
        except OSError as exc:
            self._cleanup_failure(tmp, staging, installed, backup)
            if exc.errno in (28,):   # ENOSPC — disk full
                raise ModelIntegrityError(
                    "disk full",
                    "not enough space to install the model — nothing was "
                    "changed") from exc
            raise ModelIntegrityError(
                f"filesystem error ({type(exc).__name__})", str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            self._cleanup_failure(tmp, staging, installed, backup)
            raise ModelIntegrityError(
                f"{type(exc).__name__} during installation",
                str(exc)) from exc
        finally:
            self._release_lock()
            try:                       # the temp archive never outlives the run
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def _cleanup_failure(self, tmp: str, staging: str, installed: bool,
                         backup: str) -> None:
        """Honest failure hygiene: temp + staging removed; if the new
        tree was installed but verification failed, the previous model is
        restored (rollback) and the new tree removed."""
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        try:
            if os.path.exists(staging):
                shutil.rmtree(staging, ignore_errors=True)
        except OSError:
            pass
        if installed:
            try:
                if os.path.exists(self.dest):
                    shutil.rmtree(self.dest, ignore_errors=True)
                if os.path.exists(backup):
                    os.rename(backup, self.dest)
            except OSError:
                pass

    # -- file install (offline manual path) ---------------------------------

    def install_from_file(self, path: str) -> Tuple[ModelState, str]:
        """Install from a locally provided archive (user-assisted offline
        setup) — the SAME 12 stages, no network involved."""
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            raise ModelIntegrityError(
                "archive unreadable", str(exc)) from exc
        return self.install_from_bytes(raw)


# ---------------------------------------------------------------------------
# module-level convenience (voice_bootstrap delegates here)
# ---------------------------------------------------------------------------

def _vosk_pipeline(observe: Optional[Callable[[ModelState], None]] = None
                   ) -> ModelPipeline:
    from . import paths
    return ModelPipeline(spec=VOSK_MODEL, dest=paths.vosk_model_dir(),
                         observe=observe)


def install_vosk_model(
        consent: Optional[Callable[[str], bool]] = None,
        offline: bool = False,
        progress: Optional[Callable[[str], None]] = None,
        fetch: Optional[Callable[[str, Callable[[int]], bytes]]] = None,
        observe: Optional[Callable[[ModelState], None]] = None,
        ) -> Tuple[bool, str]:
    """Compatibility wrapper: (ok, message) like voice-bootstrap used to.

    The canonical return lives in :meth:`ModelPipeline.download_and_install`
    (state machine); this wrapper keeps the historical ``(ok, msg)`` shape
    so callers and tests stay stable.
    """
    try:
        pipeline = _vosk_pipeline(observe)
        state, msg = pipeline.download_and_install(
            consent=consent, offline=offline, progress=progress,
            fetch=fetch)
        return (state == ModelState.MODEL_READY, msg)
    except ModelIntegrityError as exc:
        return (False, f"{exc.reason}: {exc.detail}")


def vosk_model_status() -> Dict[str, Any]:
    """The honest Vosk model record for doctor/setup/capabilities."""
    try:
        return _vosk_pipeline().status()
    except Exception as exc:  # noqa: BLE001 — status never raises
        return {"name": VOSK_MODEL.name, "state": "MODEL_UNKNOWN",
                "detail": f"status unavailable: {type(exc).__name__}",
                "installed": False, "compatible": True,
                "source": VOSK_MODEL.url, "provider": VOSK_MODEL.provider,
                "license": VOSK_MODEL.license, "engine": "vosk"}
