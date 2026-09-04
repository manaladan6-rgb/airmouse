"""One execution spine for every gesture-triggered action (v16 endgame).

SENSE → RECOGNIZE → CONTEXT → INTENT → RISK → PERMISSION → ACTION ENGINE
→ OBSERVE → VERIFY → RECOVER

Audit findings #4/#7/#20 (AUDIT_REPORT_v15.1.0.md): the legacy v5 loop
dispatched actions with raw pynput/keyboard calls directly from the main
loop — Alt+F4 fired on an "OK" misclassification with nothing but a
0.25 s timestamp check between the classifier and the window manager.

GestureActionRouter is the single authoritative gate EVERY gesture-
triggered action now passes through:

    E-STOP check            (dominant — first, every call)
    confidence gate         (configurable per risk class)
    risk classification     (SAFE / CAUTION / DESTRUCTIVE)
    destructive policy      (refused unless explicitly enabled)
    rate limit              (backstop interval per action)
    dispatch                (mouse / keyboard / zoom)
    observation             (bounded action record for HUD + learning)

Cursor MOVEMENT (mouse.move_to and the scroll/zoom analog axes) is
continuous control, not a discrete action — it stays on the live loop
but still passes through `gate_continuous()` for the E-STOP check.
Human override and E-STOP remain dominant: when tripped, nothing
dispatches, movement freezes, and only an explicit reset clears it.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

__all__ = ["GestureActionRouter", "RISK_CLASSES"]


# Risk classification for every gesture-dispatchable intent.
# SAFE      — reversible, expected side-effect only (clicks, scroll, drag)
# CAUTION   — disruptive but recoverable (navigation, window shuffle, volume)
# DESTRUCTIVE — destroys user state or focus (Alt+F4, macro replay of inputs)
RISK_CLASSES: Dict[str, str] = {
    # mouse
    "left_click": "SAFE",
    "right_click": "SAFE",
    "double_click": "SAFE",
    "middle_click": "SAFE",
    "start_drag": "SAFE",
    "stop_drag": "SAFE",
    "scroll": "SAFE",
    "zoom": "SAFE",
    # keyboard
    "browser_back": "CAUTION",
    "browser_forward": "CAUTION",
    "minimize_window": "CAUTION",
    "show_desktop": "CAUTION",
    "task_switch": "CAUTION",
    "volume_up": "CAUTION",
    "volume_down": "CAUTION",
    "volume_mute": "CAUTION",
    "brightness_up": "CAUTION",
    "brightness_down": "CAUTION",
    # destructive-class: a gesture must NEVER fire these merely because a
    # classifier matched (audit §6/§7). Refused by default.
    "close_window": "DESTRUCTIVE",   # Alt+F4
    "macro_play": "DESTRUCTIVE",     # replays recorded input events
}

# Confidence floors per risk class (overridable via constructor).
DEFAULT_MIN_CONFIDENCE = {"SAFE": 0.45, "CAUTION": 0.60, "DESTRUCTIVE": 1.1}
# (>1.0 for DESTRUCTIVE = unreachable by confidence alone; policy gate only)

# Keyboard action name per intent (dispatched through _safe_kb_action-style
# guarded getattr on the KeyboardActions instance).
_KB_INTENTS = {
    "browser_back": "browser_back",
    "browser_forward": "browser_forward",
    "minimize_window": "minimize_window",
    "show_desktop": "show_desktop",
    "task_switch": "switch_window",
    "close_window": "close_window",
    "volume_up": "volume_up",
    "volume_down": "volume_down",
    "volume_mute": "volume_mute",
    "brightness_up": "brightness_up",
    "brightness_down": "brightness_down",
}

_ACTION_HISTORY_MAX = 32


class GestureActionRouter:
    """Single authoritative gate for gesture-triggered actions."""

    def __init__(self,
                 mouse: Any = None,
                 kb_getter: Optional[Callable[[], Any]] = None,
                 zoom_fn: Optional[Callable[[int], None]] = None,
                 min_confidence: Optional[Dict[str, float]] = None,
                 allow_destructive: bool = False,
                 min_action_interval_s: float = 0.12,
                 scroll_backstop_interval_s: float = 0.05) -> None:
        self.mouse = mouse
        self.kb_getter = kb_getter
        self.zoom_fn = zoom_fn
        self.min_confidence = dict(DEFAULT_MIN_CONFIDENCE)
        if min_confidence:
            self.min_confidence.update(
                {k: float(v) for k, v in min_confidence.items()})
        self.allow_destructive = bool(allow_destructive)
        self.min_action_interval_s = max(0.0, float(min_action_interval_s))
        self.scroll_backstop_interval_s = max(
            0.0, float(scroll_backstop_interval_s))

        self._lock = threading.RLock()
        self._estop_reason: Optional[str] = None
        self._last_dispatch: Dict[str, float] = {}
        self._history: list = []
        self.last_refusal: str = ""

    # ── E-STOP (dominant) ────────────────────────────────────────────
    def trip_estop(self, reason: str = "emergency stop") -> None:
        with self._lock:
            self._estop_reason = str(reason or "emergency stop")

    def reset_estop(self) -> bool:
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

    # ── gates ────────────────────────────────────────────────────────
    def classify(self, intent: str) -> str:
        return RISK_CLASSES.get(str(intent), "CAUTION")

    def gate_continuous(self, now: Optional[float] = None) -> bool:
        """Continuous-control gate (cursor/scroll axes): E-STOP only."""
        return not self.estopped

    def can_execute(self, intent: str, confidence: float = 1.0,
                    now: Optional[float] = None) -> (bool, str):
        """Full gate evaluation without dispatching. Returns (ok, reason)."""
        now = float(now if now is not None else time.perf_counter())
        intent = str(intent)
        risk = self.classify(intent)
        with self._lock:
            if self._estop_reason is not None:
                return False, f"estop: {self._estop_reason}"
        conf = float(confidence)
        if conf < self.min_confidence.get(risk, 1.0):
            self.last_refusal = (
                f"low_confidence ({conf:.2f} < "
                f"{self.min_confidence.get(risk, 1.0):.2f} for {risk})")
            return False, self.last_refusal
        if risk == "DESTRUCTIVE" and not self.allow_destructive:
            self.last_refusal = (
                "destructive_action_blocked_by_policy "
                "(enable with config gesture_allow_destructive = true)")
            return False, self.last_refusal
        interval = (self.scroll_backstop_interval_s
                    if intent in ("scroll", "zoom")
                    else self.min_action_interval_s)
        last = self._last_dispatch.get(intent)
        if last is not None and (now - last) < interval:
            self.last_refusal = "rate_limit"
            return False, self.last_refusal
        return True, "ok"

    # ── dispatch ─────────────────────────────────────────────────────
    def dispatch(self, intent: str, confidence: float = 1.0,
                 now: Optional[float] = None,
                 amount: int = 0) -> Dict[str, Any]:
        """Gate + execute one discrete action. NEVER raises.

        Returns {"executed": bool, "reason": str, "risk": str}.
        """
        now = float(now if now is not None else time.perf_counter())
        intent = str(intent)
        ok, reason = self.can_execute(intent, confidence, now)
        risk = self.classify(intent)
        if not ok:
            return {"executed": False, "reason": reason, "risk": risk}
        try:
            done = self._execute(intent, amount)
        except Exception as exc:  # never crash the loop from the spine
            done = False
            reason = f"dispatch_error: {exc}"
        if done:
            with self._lock:
                self._last_dispatch[intent] = now
                self._history.append(
                    {"intent": intent, "risk": risk, "confidence": round(
                        float(confidence), 3), "ts": now})
                if len(self._history) > _ACTION_HISTORY_MAX:
                    del self._history[:-_ACTION_HISTORY_MAX]
        return {"executed": bool(done), "reason": reason, "risk": risk}

    def _execute(self, intent: str, amount: int) -> bool:
        if intent == "zoom":
            if self.zoom_fn is None:
                return False
            self.zoom_fn(int(amount))
            return True
        if intent in ("scroll",):
            if self.mouse is None:
                return False
            self.mouse.scroll(int(amount))
            return True
        if intent == "left_click":
            self.mouse.left_click()
            return True
        if intent == "right_click":
            self.mouse.right_click()
            return True
        if intent == "double_click":
            self.mouse.double_click()
            return True
        if intent == "middle_click":
            self.mouse.mouse.click(self.mouse._button.middle, 1)
            return True
        if intent == "start_drag":
            self.mouse.start_drag()
            return True
        if intent == "stop_drag":
            self.mouse.stop_drag()
            return True
        kb_name = _KB_INTENTS.get(intent)
        if kb_name is not None:
            kb = self.kb_getter() if callable(self.kb_getter) else None
            if kb is None:
                return False
            getattr(kb, kb_name)()
            return True
        return False

    # ── observation ──────────────────────────────────────────────────
    def history(self, limit: int = 8) -> list:
        with self._lock:
            return list(self._history[-int(limit):])

    def last_action(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._history[-1] if self._history else None
