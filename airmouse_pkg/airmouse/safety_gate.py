"""Central safety gate — one authoritative policy for every action route.

Why this module exists (v19.0.3, OPERATION FINAL FORM / AIRLOCK):

Before this gate an emergency stop did not necessarily block every
consequential action path (browser voice side-door, AIP agent EXECUTE,
PEACE scroll, macro replay steps could each decide on their own).  This
module is the single process-wide decision point:

1. E-STOP latch      -- latched by hotkeys, the ``airmouse estop`` CLI
                        (cross-process flag file) or in-process calls.
                        While latched, NOTHING consequential passes.
2. shutdown state    -- no NEW consequential actions during teardown.
3. backend availability -- a route that knows its backend died must
                        declare it; fresh actions for that backend stop.
4. confirmation      -- sensitive classes need an explicit confirmation.
5. unknown classes   -- fail closed (refused as unclassified).

``authorize`` NEVER raises and NEVER defaults to allow.  Routes call it
immediately before dispatching a consequential action; in-flight action
steps re-check it (cooperative cancellation).
"""

from __future__ import annotations

import os
import threading
from typing import Callable, Dict, List, Optional, Tuple

#: Every action class a route may present.  Anything NOT listed here is
#: refused (fail-closed) -- adding a class is a deliberate decision.
ACTION_CLASSES = (
    "gesture",          # webcam hand-gesture spine (clicks, drags, ...)
    "scroll_action",    # PEACE scroll and other gesture-driven scrolling
    "keyboard_action",  # synthetic keypresses / hotkeys
    "voice_action",     # voice-grammar-resolved actions
    "browser_action",   # semantic-browser bridge actions (click/type/...)
    "macro_action",     # macro replay steps
    "agent_action",     # AIP / hands-free agent EXECUTE
)

#: Classes that callers must confirm with the user before dispatching
#: when the gate says so (mirrors safety.SENSITIVE_TYPES intent).
_CONFIRM_CLASSES = frozenset({"agent_action"})


class CentralSafetyGate:
    """Process-wide safety policy consulted by every action route."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._estop_reason: Optional[str] = None
        self._shutdown = False
        self._backends: Dict[str, bool] = {}
        self._require_confirmation: Dict[str, bool] = {}
        self._trip_callbacks: List[Callable[[str], None]] = []
        self._trip_count = 0
        self._last_refusal: str = ""

    # ── E-STOP ───────────────────────────────────────────────────────
    def trip_estop(self, reason: str = "emergency stop") -> None:
        """Latch the emergency stop (idempotent; keeps the FIRST reason).

        Registered release callbacks run once per latch transition
        (unlatched -> latched), OUTSIDE the lock, exceptions swallowed:
        a release must never be blocked by a broken callback.
        """
        first = False
        with self._lock:
            if self._estop_reason is None:
                self._estop_reason = str(reason or "emergency stop")
                self._trip_count += 1
                first = True
            cbs = list(self._trip_callbacks)
        if first:
            for cb in cbs:
                try:
                    cb(str(reason))
                except Exception:
                    pass  # release callbacks must never break the latch

    def reset_estop(self) -> bool:
        """Clear the latch.  Returns True when a latch was actually set."""
        with self._lock:
            had = self._estop_reason is not None
            self._estop_reason = None
            return had

    @property
    def estopped(self) -> bool:
        with self._lock:
            return self._estop_reason is not None

    def estop_reason(self) -> Optional[str]:
        with self._lock:
            return self._estop_reason

    @property
    def trip_count(self) -> int:
        with self._lock:
            return self._trip_count

    # ── shutdown ─────────────────────────────────────────────────────
    def mark_shutdown(self, reason: str = "shutdown") -> None:
        """Enter the shutdown state: no NEW consequential action passes.

        Idempotent.  Does NOT latch the E-STOP (reset semantics stay
        clean); ``authorize()`` refuses while the flag is set.
        """
        with self._lock:
            self._shutdown = True

    @property
    def shutting_down(self) -> bool:
        with self._lock:
            return self._shutdown

    # ── backend availability ─────────────────────────────────────────
    def set_backend_available(self, name: str, available: bool) -> None:
        """Declare a control backend (mouse/keyboard/bridge/...) usable.

        Unknown backends default to AVAILABLE; a route that knows its
        backend is down must declare it -- the gate then refuses fresh
        actions for that backend only.
        """
        with self._lock:
            self._backends[str(name or "default")] = bool(available)

    def backend_available(self, name: str) -> bool:
        with self._lock:
            return self._backends.get(str(name or "default"), True)

    # ── confirmation requirements ────────────────────────────────────
    def set_confirmation_required(self, action_class: str,
                                  required: bool) -> None:
        with self._lock:
            self._require_confirmation[str(action_class)] = bool(required)

    def confirmation_required(self, action_class: str) -> bool:
        with self._lock:
            if str(action_class) in self._require_confirmation:
                return self._require_confirmation[str(action_class)]
            return str(action_class) in _CONFIRM_CLASSES

    # ── trip callbacks ───────────────────────────────────────────────
    def on_trip(self, callback: Callable[[str], None]) -> None:
        """Register a release callback invoked on every fresh latch."""
        with self._lock:
            if callable(callback) and callback not in self._trip_callbacks:
                self._trip_callbacks.append(callback)

    # ── the decision ─────────────────────────────────────────────────
    def authorize(self, action_class: str = "gesture", *,
                  backend: str = "default",
                  confirmed: bool = False) -> Tuple[bool, str]:
        """Should a NEW consequential action of this class run NOW?

        Returns ``(True, "ok")`` or ``(False, reason)``.  Never raises.
        Order (fail-closed):
          1. E-STOP latched
          2. shutdown in progress
          3. backend declared unavailable
          4. confirmation required but not presented
          5. unknown action class -> DESTRUCTIVE default: refuse
        """
        try:
            cls = str(action_class or "gesture")
            with self._lock:
                if self._estop_reason is not None:
                    self._last_refusal = f"estop: {self._estop_reason}"
                    return False, self._last_refusal
                if self._shutdown:
                    self._last_refusal = "shutdown: no new actions"
                    return False, self._last_refusal
                if not self._backends.get(backend, True):
                    self._last_refusal = f"backend_unavailable: {backend}"
                    return False, self._last_refusal
                need_conf = self._require_confirmation.get(
                    cls, cls in _CONFIRM_CLASSES)
            if need_conf and not confirmed:
                self._last_refusal = (
                    f"confirmation_required: {cls}")
                return False, self._last_refusal
            if cls not in ACTION_CLASSES:
                self._last_refusal = f"unclassified_action: {cls}"
                return False, self._last_refusal
            return True, "ok"
        except Exception as exc:  # a broken gate blocks, never enables
            return False, f"gate_error: {exc}"

    def last_refusal(self) -> str:
        with self._lock:
            return self._last_refusal


# ── process-wide singleton ──────────────────────────────────────────
_GATE_LOCK = threading.Lock()
_GATE: Optional[CentralSafetyGate] = None


def get_gate() -> CentralSafetyGate:
    """Return the process-wide gate (created on first call)."""
    global _GATE
    if _GATE is None:
        with _GATE_LOCK:
            if _GATE is None:
                _GATE = CentralSafetyGate()
    return _GATE


def reset_gate_for_tests() -> CentralSafetyGate:
    """Install a FRESH gate and return it (test isolation helper)."""
    global _GATE
    with _GATE_LOCK:
        _GATE = CentralSafetyGate()
    return _GATE


# ── cross-process E-STOP flag file ──────────────────────────────────
# `airmouse estop trip` (a separate process) writes this file; a RUNNING
# AirMouse session polls it every frame and trips its central gate.
# The flag NEVER auto-resets: only `airmouse estop reset` (or an armed
# in-session reset channel) clears it.  That is deliberate -- a
# panicked user should not have the stop silently undone.

ESTOP_FLAG_FILENAME = "estop"
_ESTOP_REASON_PREFIX = "reason: "


def _airmouse_home() -> str:  # pragma: no cover - trivial env indirection
    return os.environ.get("AIRMOUSE_HOME") or os.path.expanduser(
        "~/.airmouse")


def estop_flag_path() -> str:
    """Path of the cross-process E-STOP flag file."""
    return os.path.join(_airmouse_home(), ESTOP_FLAG_FILENAME)


def estop_flag_present() -> bool:
    """True when the cross-process E-STOP flag file exists."""
    try:
        return os.path.isfile(estop_flag_path())
    except Exception:
        return True  # unreadable home -> fail CLOSED


def estop_flag_reason() -> str:
    """Human-readable reason stored in the flag file (may be '')."""
    try:
        with open(estop_flag_path(), "r", encoding="utf-8",
                  errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(_ESTOP_REASON_PREFIX):
                    return line[len(_ESTOP_REASON_PREFIX):]
    except Exception:
        pass
    return ""


def write_estop_flag(reason: str = "estop requested via CLI"
                     " (airmouse estop)") -> str:
    """Create the flag file atomically.  Returns the flag path."""
    path = estop_flag_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(f"{_ESTOP_REASON_PREFIX}{reason or 'emergency stop'}\n")
        os.replace(tmp, path)  # atomic on POSIX and Windows
    except Exception:
        # Last resort: a non-atomic write still beats no stop.
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"{_ESTOP_REASON_PREFIX}{reason}\n")
        except Exception:
            pass
    return path


def clear_estop_flag() -> bool:
    """Remove the flag file.  Returns True when a flag was removed."""
    try:
        path = estop_flag_path()
        if os.path.isfile(path):
            os.remove(path)
            return True
    except Exception:
        pass
    return False


# ── session binding ─────────────────────────────────────────────────
class _SessionGate:
    """Delegate-forever view of the process-wide gate for one session.

    Attribute access is resolved against the CURRENT singleton on every
    use, so replacing the singleton (tests) can never strand a session
    with a stale gate object.  The session gets one extra behaviour on
    top of the raw gate: ``poll_estop_flag()`` folds the cross-process
    CLI flag into the latch (called once per frame by the session loop).
    """

    def __init__(self, flag_poll: bool = True) -> None:
        self._flag_poll = bool(flag_poll)
        self._last_flag_reason: str = ""

    @property
    def _gate(self) -> CentralSafetyGate:
        return get_gate()

    def poll_estop_flag(self) -> bool:
        """Fold the cross-process E-STOP flag into the latch (per frame).

        Returns True when the gate is (or just became) estopped.  The
        flag file is never cleared here -- only ``airmouse estop reset``
        or an armed reset channel may clear it.
        """
        if self._flag_poll and estop_flag_present():
            reason = estop_flag_reason() or (
                "estop requested via CLI (airmouse estop)")
            if not self._gate.estopped:
                self._gate.trip_estop(reason)
            self._last_flag_reason = reason
        return self._gate.estopped

    @property
    def flag_reason(self) -> str:
        return self._last_flag_reason

    # -- delegate everything else, forever ---------------------------
    def __getattr__(self, name: str):
        return getattr(get_gate(), name)


def bind_session(gate: Optional[CentralSafetyGate] = None, *,
                 flag_poll: bool = True) -> _SessionGate:
    """Bind a session to the process-wide gate.

    The returned object delegates to the CURRENT singleton forever
    (rebinding the singleton never strands the session) and adds the
    per-frame ``poll_estop_flag()`` fold.  Library and test code that
    calls ``get_gate()`` directly keeps its exact semantics.
    """
    return _SessionGate(flag_poll=flag_poll)
