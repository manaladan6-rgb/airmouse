"""airmouse.fileio — THE one explicit text-encoding contract (v16.5.1).

Windows reality (v16.5.0 hotfix §1): a text file written with plain
``open(path, "w")`` uses the *process default* encoding — cp1252 on a
real Windows machine — while UTF-8-only readers (``tomllib``, JSON
parsers) later fail with::

    'utf-8' codec can't decode byte 0x97 in position 30: invalid start byte

(``0x97`` is exactly the cp1252 byte for an em dash, which the generated
``config.toml`` template puts at position 30 of its first header line.)

THE CONTRACT (AirMouse-owned text configuration/state files):

1.  Every READ of an AirMouse-owned text file goes through
    :func:`read_text` — strict UTF-8, never the process default.
2.  Every WRITE goes through :func:`write_text_atomic` — UTF-8 without
    BOM, atomic (tmp file + ``os.replace``), never the process default.
3.  A file that cannot be decoded as UTF-8 is NEVER silently replaced
    by defaults: :func:`read_text` raises :class:`TextDecodingError`
    carrying the path, byte position and the exact fix command, and
    :func:`probe` gives doctor/repair an honest diagnosis.
4.  Repair (:func:`decode_best_effort`) can *recover* legacy cp1252 /
    latin-1 content — but only an explicit ``airmouse setup
    --repair-config`` run ever rewrites the file, always after a
    timestamped backup, and always with a full report.

Non-goals: this module does NOT handle AirMouse's binary artifacts
(``model.bin``, ``.task`` files) and never touches user files outside
the AirMouse home.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional, Tuple

__all__ = [
    "TEXT_ENCODING", "FIX_COMMAND",
    "TextDecodingError",
    "read_text", "write_text_atomic", "read_json", "write_json_atomic",
    "decode_best_effort", "probe",
]

#: The one encoding for every AirMouse-owned text file.  UTF-8, no BOM.
TEXT_ENCODING = "utf-8"

#: The exact remediation surfaced by errors, doctor and repair reports.
FIX_COMMAND = "airmouse setup --repair-config"

#: Legacy Windows/ISO decodings, tried IN ORDER by repair only.
_RECOVERY_ENCODINGS: Tuple[str, ...] = ("utf-8-sig", "cp1252", "latin-1")


class TextDecodingError(UnicodeDecodeError):
    """A strict UTF-8 read failed — surfaced, never silently swallowed.

    Carries the file ``path``, the failing ``byte_position`` /
    ``byte_value`` and the canonical ``fix`` command.  Subclasses
    :class:`UnicodeDecodeError` so historic ``except UnicodeDecodeError``
    handlers keep working.
    """

    def __init__(self, path: str, position: int, byte_value: int,
                 reason: str = "invalid start byte") -> None:
        super().__init__(TEXT_ENCODING, bytes([byte_value]), 0, 1, reason)
        self.path = str(path)
        self.byte_position = int(position)
        self.byte_value = int(byte_value) & 0xFF
        self.fix = FIX_COMMAND

    def __str__(self) -> str:  # pragma: no cover - trivial
        return (f"'{TEXT_ENCODING}' codec can't decode byte "
                f"0x{self.byte_value:02x} in position {self.byte_position}: "
                f"{self.reason} — file: {self.path} (fix: {self.fix})")


# ---------------------------------------------------------------------------
# strict contract primitives
# ---------------------------------------------------------------------------

def read_text(path: str) -> str:
    """Read ``path`` as strict UTF-8 (the encoding contract).

    Raises :class:`TextDecodingError` with full context on decode
    failure (never falls back to the process default encoding, never
    returns mojibake).  ``FileNotFoundError``/``IsADirectoryError``
    propagate untouched — callers decide what "missing" means.
    """
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return raw.decode(TEXT_ENCODING)
    except UnicodeDecodeError as exc:
        raise TextDecodingError(path, exc.start,
                                raw[exc.start] if exc.start < len(raw) else 0,
                                exc.reason) from None


def replace_atomic(src: str, dst: str, *, attempts: int = 6,
                   delay: float = 0.02) -> None:
    """``os.replace`` with a short retry on Windows sharing violations.

    On POSIX a rename over an open file just works.  On Windows a file
    held open by another thread/process (antivirus, indexer, a second
    AirMouse thread mid-read) makes ``os.replace`` raise
    ``PermissionError`` — a brief bounded retry resolves the common
    races without ever masking a genuine failure (the last error still
    propagates).
    """
    last_exc: Optional[BaseException] = None
    for i in range(max(1, attempts)):
        try:
            os.replace(src, str(dst))
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(delay * (i + 1))     # linear backoff, bounded
    assert last_exc is not None
    raise last_exc


def write_text_atomic(path: str, text: str) -> None:
    """Write ``text`` as UTF-8 (no BOM), atomically, contract-guaranteed.

    tmp file in the same directory + :func:`replace_atomic` — a crash
    mid-write can never leave a truncated config behind.  Creates the
    parent directory when missing.
    """
    d = os.path.dirname(str(path))
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(text.encode(TEXT_ENCODING))
        f.flush()
        os.fsync(f.fileno())
    replace_atomic(tmp, str(path))


def read_json(path: str) -> Any:
    """``json.loads`` over :func:`read_text` (strict UTF-8 contract)."""
    return json.loads(read_text(path))


def write_json_atomic(path: str, obj: Any, *, indent: int = 2) -> None:
    """Serialize ``obj`` (``ensure_ascii=False``) and write it UTF-8.

    ``ensure_ascii=False`` keeps human-readable non-ASCII content (e.g.
    Nigerian/European names in learned profiles) as real UTF-8 instead
    of ``\\uXXXX`` escapes; the on-disk bytes are UTF-8 either way.
    """
    write_text_atomic(path, json.dumps(obj, indent=indent, ensure_ascii=False))


# ---------------------------------------------------------------------------
# diagnosis + repair (used by doctor and `setup --repair-config` ONLY)
# ---------------------------------------------------------------------------

def probe(path: str) -> Dict[str, Any]:
    """Honest, never-raising diagnosis of a text file's encoding state.

    Returns::

        {"exists": bool, "size": int,
         "utf8_ok": bool, "bom": bool,
         "decoded_as": Optional[str],   # 'utf-8' | 'utf-8-sig' | 'cp1252' | 'latin-1'
         "bad_position": Optional[int], "bad_byte": Optional[str],
         "error": Optional[str]}
    """
    info: Dict[str, Any] = {"exists": False, "size": 0, "utf8_ok": False,
                            "bom": False, "decoded_as": None,
                            "bad_position": None, "bad_byte": None,
                            "error": None}
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        info["error"] = "file not found"
        return info
    except OSError as exc:
        info["error"] = f"unreadable: {exc}"
        return info
    info["exists"] = True
    info["size"] = len(raw)
    info["bom"] = raw.startswith(b"\xef\xbb\xbf")
    try:
        raw.decode(TEXT_ENCODING)
        info["utf8_ok"] = True
        info["decoded_as"] = TEXT_ENCODING
        return info
    except UnicodeDecodeError as exc:
        info["bad_position"] = exc.start
        info["bad_byte"] = f"0x{raw[exc.start]:02x}" \
            if exc.start < len(raw) else None
        info["error"] = f"{exc.reason} at byte {exc.start}"
    for enc in _RECOVERY_ENCODINGS:
        try:
            raw.decode(enc)
            info["decoded_as"] = enc
            break
        except (UnicodeDecodeError, LookupError):
            continue
    return info


def decode_best_effort(raw: bytes) -> Tuple[str, str, Optional[int]]:
    """Decode legacy bytes for REPAIR purposes only.

    Tries strict UTF-8 first (the contract), then BOM-aware UTF-8,
    then the legacy Windows cp1252 and finally latin-1 (which decodes
    any byte sequence).  Returns ``(text, encoding_used, bad_position)``
    where ``bad_position`` is ``None`` on clean UTF-8 and the failing
    byte offset otherwise.

    NOTE: decoding with cp1252/latin-1 *never throws away data* — but it
    is only ever invoked by the explicit repair flow, which backs the
    original file up first and reports the encoding it recovered from.
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        # A UTF-8 BOM is valid UTF-8 but poisons TOML/JSON parsers with a
        # leading U+FEFF — strip it and report the recovery honestly.
        return raw.decode("utf-8-sig"), "utf-8-sig", None
    try:
        return raw.decode(TEXT_ENCODING), TEXT_ENCODING, None
    except UnicodeDecodeError:
        pass
    for enc in _RECOVERY_ENCODINGS:
        try:
            return raw.decode(enc), enc, None
        except UnicodeDecodeError:
            continue
    # latin-1 decoded, so this is unreachable in practice; kept honest
    raise TextDecodingError("<bytes>", 0, raw[0] if raw else 0, "undecodable")
