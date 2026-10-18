"""airmouse.estop_hotkey — GLOBAL E-STOP keyboard hotkeys (v19.0.3 R3b2)
=======================================================================

Arms the session-global emergency-stop hotkeys on the CENTRAL safety
gate (``airmouse.safety_gate``):

    Ctrl+Alt+E   →  ``gate.trip_estop("global hotkey Ctrl+Alt+E")``
    Ctrl+Alt+R   →  ``gate.reset_estop()`` (the reset channel)

These are armed EVERY session by default (``__main__`` calls
:func:`arm_estop_hotkeys` right after ``bind_session()``) and MUST work
without the camera preview and without debug mode: the only resource
needed is a global keyboard listener, never the tracker, a display
preview window or any debug flag.

HONESTY CONTRACT
----------------
* If pynput cannot be imported (headless box, no X server, unsupported
  platform) or the listener thread cannot be started, the arm call
  returns ``available=False`` with the exact ``unavailable_reason`` —
  it NEVER pretends the hotkeys are live when they are not.
* Listener/callback exceptions are swallowed (an E-STOP channel must
  never crash the session) but recorded and visible in ``status()``.
* ``stop()`` is bounded and idempotent and NEVER joins the listener
  thread: pynput's own stop protocol shuts the thread down; a hanging
  listener must not hang the session teardown.

Status shape (truthful in every field)::

    {"available": bool,
     "unavailable_reason": str,      # "" when available
     "stop": callable,               # always present; no-op when unavailable
     "trip_hotkey": "Ctrl+Alt+E",
     "reset_hotkey": "Ctrl+Alt+R",
     "trip_reason": "global hotkey Ctrl+Alt+E",
     "errors": [...],                # live list of swallowed errors
     "status": callable -> dict}     # fresh snapshot incl. listener state

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "arm_estop_hotkeys",
    "TRIP_HOTKEY",
    "RESET_HOTKEY",
    "TRIP_REASON",
]

TRIP_HOTKEY = "Ctrl+Alt+E"
RESET_HOTKEY = "Ctrl+Alt+R"
TRIP_REASON = "global hotkey Ctrl+Alt+E"

#: bounded wait (seconds) — the hotkey channel may never hang a session
_START_GRACE_S = 0.25      # bounded wait before judging listener liveness

#: ctrl+<letter> control codes some platforms deliver as ``key.char``
_CTRL_LETTER_CODES = {"e": "\x05", "r": "\x12"}


def _load_pynput() -> Any:
    """Import pynput.keyboard.  Raises honestly when unavailable.

    Headless machines (no X server) and unsupported platforms raise
    ImportError here — that is the TRUTHFUL "hotkeys unavailable"
    signal, never a reason to fake a listener.
    """
    from pynput import keyboard          # noqa: F401  (may raise ImportError)
    return keyboard


def _key_matches(key: Any, group: Any, names: tuple) -> bool:
    """True when ``key`` is one of ``group.<name>`` (never raises)."""
    if key is None or group is None:
        return False
    for name in names:
        try:
            if key is getattr(group, name):
                return True
        except Exception:
            continue
    return False


def _key_letter(key: Any) -> str:
    """Best-effort letter for a KeyCode ('e'/'r'), else "" (never raises).

    Handles the three real-world encodings: a plain character, the
    Ctrl+letter control code (Ctrl+E → '\\x05') and a virtual-key code
    (Windows vk 0x45 = 'E').
    """
    try:
        ch = getattr(key, "char", None)
        if isinstance(ch, str) and ch:
            low = ch.lower()
            for letter, ctrl_code in _CTRL_LETTER_CODES.items():
                if low == letter or low == ctrl_code:
                    return letter
            return ""
        vk = getattr(key, "vk", None)
        if isinstance(vk, int):
            # ASCII uppercase codes for E (69) and R (82)
            if vk == 69:
                return "e"
            if vk == 82:
                return "r"
    except Exception:
        return ""
    return ""


class _HotkeyArm:
    """One armed hotkey channel: listener + central-gate callbacks."""

    def __init__(self, gate: Any, kb: Any) -> None:
        self._gate = gate
        self._kb = kb
        self._lock = threading.Lock()
        self._errors: List[str] = []
        self._ctrl_down = False
        self._alt_down = False
        self._stopped = False
        self._listener: Any = None
        self._listener_factory: Optional[Callable] = None

    # -- error bookkeeping (swallowed, but reported) ---------------------
    def _record(self, message: str) -> None:
        with self._lock:
            self._errors.append(f"{time.time():.3f}: {message}")
            del self._errors[:-64]          # bounded history

    def _safe(self, what: str, fn: Callable, *args: Any) -> Any:
        try:
            return fn(*args)
        except Exception as exc:
            self._record(f"{what} failed: {exc}")
            return None

    # -- keyboard callbacks ------------------------------------------------
    def _on_press(self, key: Any) -> None:
        try:
            if _key_matches(key, self._kb.Key, ("ctrl", "ctrl_l", "ctrl_r")):
                self._ctrl_down = True
                return
            if _key_matches(key, self._kb.Key, ("alt", "alt_l", "alt_r",
                                                "alt_gr")):
                self._alt_down = True
                return
            if not (self._ctrl_down and self._alt_down):
                return
            letter = _key_letter(key)
            if letter == "e":
                # trip with the documented reason (idempotent latch:
                # repeated presses keep the FIRST reason and never
                # re-fire release callbacks — enforced by the gate)
                self._safe("trip_estop",
                           lambda: self._gate.trip_estop(TRIP_REASON))
            elif letter == "r":
                self._safe("reset_estop", self._gate.reset_estop)
        except Exception as exc:            # never break the listener
            self._record(f"on_press error: {exc}")

    def _on_release(self, key: Any) -> None:
        try:
            if _key_matches(key, self._kb.Key, ("ctrl", "ctrl_l", "ctrl_r")):
                self._ctrl_down = False
            elif _key_matches(key, self._kb.Key,
                              ("alt", "alt_l", "alt_r", "alt_gr")):
                self._alt_down = False
        except Exception as exc:
            self._record(f"on_release error: {exc}")

    # -- lifecycle ----------------------------------------------------------
    def start(self, start_grace: float) -> Optional[str]:
        """Start the listener.  Returns None on success, else the honest
        unavailability reason."""
        try:
            self._listener = self._kb.Listener(
                on_press=self._on_press, on_release=self._on_release)
        except Exception as exc:
            self._listener = None
            return f"listener construction failed: {exc}"
        try:
            self._listener.start()
        except Exception as exc:
            self._listener = None
            return f"listener start failed: {exc}"
        # bounded liveness check — a listener that dies immediately
        # (headless X, permission problems) is reported, never faked
        deadline = time.monotonic() + max(0.0, float(start_grace))
        while time.monotonic() < deadline:
            try:
                if not self._listener.is_alive():
                    reason = "listener thread died immediately"
                    self._listener = None
                    return reason
            except Exception as exc:
                self._listener = None
                return f"listener liveness unknown: {exc}"
            time.sleep(0.02)
        try:
            if not self._listener.is_alive():
                self._listener = None
                return "listener thread died immediately"
        except Exception as exc:
            self._listener = None
            return f"listener liveness unknown: {exc}"
        return None

    def stop(self) -> None:
        """Bounded, idempotent stop.  NEVER joins the listener thread."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            listener = self._listener
            self._listener = None
        if listener is None:
            return
        # pynput's stop() flags the thread and wakes it via its own
        # protocol; we deliberately do NOT join it — a wedged listener
        # must never hang the session teardown.  The call itself is
        # bounded by pynput's design (no join inside stop()).
        self._safe("listener.stop", listener.stop)

    # -- status ----------------------------------------------------------
    def snapshot(self, available: bool, unavailable_reason: str,
                 _depth: int = 0) -> Dict[str, Any]:
        with self._lock:
            errors = list(self._errors)
            listener = self._listener
            stopped = self._stopped
        alive: Optional[bool] = None
        if listener is not None:
            try:
                alive = bool(listener.is_alive())
            except Exception:
                alive = None
        snap = {
            "available": bool(available and not stopped),
            "unavailable_reason": "" if available and not stopped
            else str(unavailable_reason or ("hotkeys stopped" if stopped
                                            else "unknown")),
            "stop": self.stop,
            "trip_hotkey": TRIP_HOTKEY,
            "reset_hotkey": RESET_HOTKEY,
            "trip_reason": TRIP_REASON,
            "errors": errors,
            "listener_alive": alive,
        }
        if _depth == 0:
            snap["status"] = (lambda: self.snapshot(
                available, unavailable_reason, _depth=1))
        return snap


def arm_estop_hotkeys(gate: Any = None, *,
                      keyboard_module: Optional[Any] = None,
                      start_grace: float = _START_GRACE_S) -> Dict[str, Any]:
    """Arm the global E-STOP hotkeys on ``gate`` (default: the central gate).

    Returns the honest status dict documented in the module docstring.
    The ``stop`` callable is ALWAYS present (a no-op when the hotkeys
    could not be armed) so session teardown never needs a special case.
    Never raises.

    Args:
        gate: the safety gate (``bind_session()`` handle or the central
            gate itself); ``None`` → the process-wide central gate.
        keyboard_module: test/CI injection point for a deterministic
            pynput stand-in (``None`` → real ``pynput.keyboard``).
        start_grace: bounded seconds to wait before judging whether the
            listener thread actually stayed alive.
    """
    if gate is None:
        try:
            from .safety_gate import get_gate
            gate = get_gate()
        except Exception as exc:
            return {"available": False,
                    "unavailable_reason": f"gate unavailable: {exc}",
                    "stop": (lambda: None), "trip_hotkey": TRIP_HOTKEY,
                    "reset_hotkey": RESET_HOTKEY, "trip_reason": TRIP_REASON,
                    "errors": [], "status": (lambda: {})}

    try:
        kb = keyboard_module if keyboard_module is not None else _load_pynput()
    except Exception as exc:
        reason = f"pynput unavailable: {exc}"
        return {"available": False, "unavailable_reason": reason,
                "stop": (lambda: None), "trip_hotkey": TRIP_HOTKEY,
                "reset_hotkey": RESET_HOTKEY, "trip_reason": TRIP_REASON,
                "errors": [], "status": (lambda: {})}

    arm = _HotkeyArm(gate, kb)
    failure = arm.start(float(start_grace))
    if failure is not None:
        arm.stop()
        return {"available": False, "unavailable_reason": failure,
                "stop": arm.stop, "trip_hotkey": TRIP_HOTKEY,
                "reset_hotkey": RESET_HOTKEY, "trip_reason": TRIP_REASON,
                "errors": arm.snapshot(False, failure)["errors"],
                "status": (lambda: arm.snapshot(False, failure))}

    status = arm.snapshot(True, "")
    return status
