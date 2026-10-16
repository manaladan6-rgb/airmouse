"""
airmouse.cli_log — v19.0.2 §`airmouse logs`: the CLI command-outcome log.

What this is (and deliberately is NOT)
--------------------------------------
This is the COMMAND-OUTCOME log: a small, bounded, UTC-timestamped
record of what the user ran from the CLI (``airmouse <cmd>``) and what
happened (exit code, human-readable message).  It is the storage
backend for the ``airmouse logs`` inspection command.

It is deliberately SEPARATE from cursor telemetry: the runtime loop
produces thousands of high-frequency pointer events per second; this
store only ever sees discrete command completions (a handful per
session).  Never point cursor/perception telemetry at this module —
it would defeat the rotation bound in seconds.

Design rules
------------
1.  BOUNDED.  JSON Lines at ``$AIRMOUSE_HOME/cli_log.jsonl`` (v19.0.2
    added no ``logs/`` directory helper in :mod:`airmouse.paths`, so
    the store lives at the home root beside the other data files —
    ``calibration.json``, ``config.toml``, … — and therefore follows
    the ``AIRMOUSE_HOME`` environment variable automatically through
    :func:`airmouse.paths.airmouse_home`).  When the live file exceeds
    1 MiB it is rotated to ``cli_log.jsonl.1`` (overwriting the
    previous generation); at most one rotation generation ever exists,
    so disk use is hard-capped at ~2 MiB.
2.  UTC, HONEST TIMESTAMPS.  Every record carries both a wall-clock
    ISO-8601 UTC timestamp (``t``) and ``time.monotonic()`` seconds
    (``mono``) so ordering and duration questions are both answerable.
3.  INJECTION-SAFE.  Command output is hostile: ANSI escape sequences
    (CSI ``\\x1b[…m``, OSC ``\\x1b]…``/BEL/ST) and control characters
    (everything C0/C1 except newline) are stripped before storage, and
    messages are capped at 2000 characters — so a misbehaving tool
    cannot smuggle terminal escapes into the log that ``airmouse
    logs`` later prints back to the user's terminal.
4.  THREAD-SAFE.  One lock guards every append/read/clear; the
    module-level convenience functions share a singleton store.
5.  NEVER RAISES.  Logging is best-effort diagnostics: any I/O failure
    returns ``False`` / ``[]`` / ``"[]"`` instead of breaking the very
    command that was being logged.
6.  STDLIB ONLY.  No third-party dependencies; Python >= 3.9.

Record shape (one JSON object per line)::

    {"t": "2026-01-01T12:00:00.000Z", "mono": 12345.678,
     "cmd": "doctor", "rc": 0, "level": "info", "msg": "all checks passed"}

Quick usage
-----------

    from airmouse import cli_log

    cli_log.log_command("doctor", rc, msg="3 warnings")   # best-effort
    recs = cli_log.read_recent(50)                        # oldest → newest
    print(cli_log.read_recent_json(50))                   # compact JSON
    cli_log.clear()                                       # user-directed wipe

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

try:  # package-relative (normal import path)
    from . import paths
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse import paths

__all__ = [
    "CliLog",
    "log_command", "read_recent", "read_recent_json", "clear", "path",
    "FILE_NAME", "ROTATION_SUFFIX", "MAX_BYTES", "MSG_MAX_CHARS",
    "CMD_MAX_CHARS", "LEVELS", "READ_MAX",
]

#: live log file, at the AirMouse home root (follows $AIRMOUSE_HOME)
FILE_NAME = "cli_log.jsonl"

#: single rotation generation suffix (overwritten on every rotation)
ROTATION_SUFFIX = ".1"

#: rotate the live file once it exceeds 1 MiB (bounded disk: ~2 MiB max)
MAX_BYTES = 1024 * 1024

#: hard cap on a stored message (injection-safety + line-size bound)
MSG_MAX_CHARS = 2000

#: hard cap on a stored command name
CMD_MAX_CHARS = 200

#: the only accepted log levels (anything else is coerced to "info")
LEVELS = ("info", "warn", "error")

#: hard cap on how many records read_recent will ever return
READ_MAX = 1000


# ---------------------------------------------------------------------------
# sanitization (injection safety — hostile text never reaches the file)
# ---------------------------------------------------------------------------

# OSC: ESC ] payload terminated by BEL, ST (ESC \) — payload may not
# itself contain ESC/BEL; an unterminated OSC consumes to end of string.
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b\x9c]*(?:\x07|\x1b\\)?")

# CSI: ESC [ params intermediates final-byte (e.g. \x1b[31m, \x1b[2J).
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9:;<=>?]*[ -/]*[@-~]")

# leftover two-character ESC sequences (\x1b7, \x1bM, \x1bc, …).
_ANSI_ESC_RE = re.compile(r"\x1b[@-_]")

# remaining control characters: all C0 except newline (\x0a), DEL, C1.
_CTRL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")


def _sanitize(text) -> str:
    """Strip ANSI escapes + control chars (newline kept); never raises."""
    try:
        s = str(text)
    except Exception:
        return ""
    s = _ANSI_OSC_RE.sub("", s)
    s = _ANSI_CSI_RE.sub("", s)
    s = _ANSI_ESC_RE.sub("", s)
    s = _CTRL_RE.sub("", s)
    return s


def _coerce_rc(rc) -> int:
    """Best-effort int exit code; unknown values become -1."""
    try:
        return int(rc)
    except Exception:
        return -1


def _coerce_level(level) -> str:
    """Whitelist the level field — a free-text level can never be stored."""
    try:
        lv = str(level).strip().lower()
    except Exception:
        return "info"
    return lv if lv in LEVELS else "info"


def _coerce_mono(mono) -> float:
    try:
        return float(mono)
    except Exception:
        return 0.0


def _utc_now_iso() -> str:
    """ISO-8601 UTC wall-clock timestamp, e.g. 2026-01-01T12:00:00.000Z."""
    return datetime.now(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _parse_record(raw: str) -> Optional[dict]:
    """One JSON line → canonical record dict (None if unusable).

    A line must at least identify itself as a command outcome (``t``,
    ``cmd``, ``rc`` present) — anything less is treated as corruption
    and skipped rather than served to ``airmouse logs``.
    """
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if "t" not in obj or "cmd" not in obj or "rc" not in obj:
        return None
    return {
        "t": str(obj.get("t", "")),
        "mono": _coerce_mono(obj.get("mono")),
        "cmd": str(obj.get("cmd", "")),
        "rc": _coerce_rc(obj.get("rc")),
        "level": _coerce_level(obj.get("level")),
        "msg": str(obj.get("msg", "")),
    }


# ---------------------------------------------------------------------------
# CliLog
# ---------------------------------------------------------------------------


class CliLog:
    """Bounded, rotating, UTC-timestamped command-outcome log store.

    One instance is one logical store; paths are re-resolved on EVERY
    call via :mod:`airmouse.paths` (so ``AIRMOUSE_HOME`` changes are
    always honored — the same dynamic rule the rest of the package
    follows).  All methods are thread-safe and none ever raises.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # -- paths (dynamic; never cached) ---------------------------------------

    def _paths(self) -> Tuple[str, str]:
        """(current_file, rotated_file), anchored at the current home."""
        cur = os.path.join(paths.airmouse_home(), FILE_NAME)
        return cur, cur + ROTATION_SUFFIX

    def path(self) -> str:
        """Current (live) log file path as a string."""
        try:
            return self._paths()[0]
        except Exception:  # pragma: no cover - paths cannot realistically raise
            return os.path.join(FILE_NAME)

    def rotated_path(self) -> str:
        """Rotation generation path (``cli_log.jsonl.1``) as a string."""
        return self.path() + ROTATION_SUFFIX

    # -- append ----------------------------------------------------------------

    def log_command(self, cmd, rc, level: str = "info", msg: str = "") -> bool:
        """Append one command outcome.  Best-effort: False on any I/O failure.

        ``msg``/``cmd`` are sanitized (ANSI + control chars stripped,
        length-capped) before storage; ``rc`` is coerced to int; ``level``
        is whitelisted to info/warn/error.
        """
        record = {
            "t": _utc_now_iso(),
            "mono": float(time.monotonic()),
            "cmd": _sanitize(cmd)[:CMD_MAX_CHARS],
            "rc": _coerce_rc(rc),
            "level": _coerce_level(level),
            "msg": _sanitize(msg)[:MSG_MAX_CHARS],
        }
        line = json.dumps(record, ensure_ascii=False,
                          separators=(",", ":")) + "\n"
        try:
            with self._lock:
                cur, rot = self._paths()
                os.makedirs(paths.airmouse_home(), exist_ok=True)
                try:
                    if os.path.getsize(cur) > MAX_BYTES:
                        os.replace(cur, rot)  # overwrites the old generation
                except FileNotFoundError:
                    pass  # nothing to rotate yet
                with open(cur, "a", encoding="utf-8", errors="replace") as f:
                    f.write(line)
            return True
        except Exception:
            return False

    #: readable alias — appending is exactly what log_command does
    append = log_command

    # -- read --------------------------------------------------------------------

    def read_recent(self, n: int = 50) -> List[dict]:
        """Last ``n`` records, oldest → newest.  Reads BOTH files.

        The rotation generation (``.1``) is older than the live file, so
        it is read first.  Corrupt/partial lines are skipped.  ``n`` is
        clamped into [1, 1000].  Never raises; I/O failure → ``[]``.
        """
        try:
            n = int(n)
        except Exception:
            n = 50
        n = max(1, min(READ_MAX, n))
        try:
            with self._lock:
                cur, rot = self._paths()
                lines = _read_file_lines(rot) + _read_file_lines(cur)
        except Exception:
            return []
        records: List[dict] = []
        for line in lines:
            rec = _parse_record(line)
            if rec is not None:
                records.append(rec)
        return records[-n:]

    def read_recent_json(self, n: int = 50) -> str:
        """Compact JSON array string of :meth:`read_recent`.  Never raises."""
        try:
            return json.dumps(self.read_recent(n), ensure_ascii=False,
                              separators=(",", ":"))
        except Exception:
            return "[]"

    # -- clear -------------------------------------------------------------------

    def clear(self) -> None:
        """Delete both files (best-effort; missing files are fine)."""
        try:
            with self._lock:
                cur, rot = self._paths()
                for p in (cur, rot):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        except Exception:
            return


def _read_file_lines(path: str) -> List[str]:
    """All non-empty lines of a JSONL file (missing/unreadable → [])."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [ln for ln in f.read().splitlines() if ln.strip()]
    except OSError:
        return []


# ---------------------------------------------------------------------------
# module-level singleton (the API the CLI wires into `airmouse logs`)
# ---------------------------------------------------------------------------

_SINGLETON = CliLog()


def log_command(cmd, rc, level: str = "info", msg: str = "") -> bool:
    """Convenience: append one command outcome via the shared store."""
    return _SINGLETON.log_command(cmd, rc, level=level, msg=msg)


def read_recent(n: int = 50) -> List[dict]:
    """Convenience: last ``n`` records, oldest → newest (both files)."""
    return _SINGLETON.read_recent(n)


def read_recent_json(n: int = 50) -> str:
    """Convenience: compact JSON array string of the recent records."""
    return _SINGLETON.read_recent_json(n)


def clear() -> None:
    """Convenience: delete both log files (best-effort)."""
    _SINGLETON.clear()


def path() -> str:
    """Convenience: current (live) log file path string."""
    return _SINGLETON.path()
