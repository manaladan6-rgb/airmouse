"""
airmouse.actions — v8 Action Engine ⚡
======================================

Turns an :class:`airmouse.interfaces.Intent` into a bounded
:class:`airmouse.interfaces.ActionPlan` and executes it through an
executor, behind the safety gate and with precondition checking,
retry-on-exception and per-status statistics.

Pipeline for :meth:`ActionEngine.execute`
-----------------------------------------
1. SAFETY GATE first — ``safety.approve_intent(intent)`` (a shim Intent is
   built when the plan carries none).  Not allowed → ``BLOCKED`` report
   with the safety reason; the executor is never touched.
2. PRECONDITIONS — action != NONE, point present + within bounds for
   pointer actions, scroll amount an int in [-100, 100], TYPE text a str
   of 1..500 chars, HOTKEY 1..4 non-empty key names, ZOOM needs
   ``direction in {in, out}`` or an int ``ticks``.  Violations → ``FAILED``
   without calling the executor.
3. EXECUTION — dispatched to the executor with timing; on an executor
   EXCEPTION the attempt is retried up to ``plan.max_retries`` (never on
   BLOCKED).  A synchronous call that exceeds ``plan.timeout`` is reported
   as ``TIMEOUT``.
4. REPORT — ``ActionReport(status=SUCCESS/FAILED, latency, attempts,
   observation=...)``; ``observation["pointer"]`` is filled for pointer
   actions (or from a dict returned by the executor).

Executors
---------
* :class:`PynputExecutor` — real pynput mouse/keyboard; pynput is imported
  lazily INSIDE each method; any import/creation failure sets
  ``available=False`` and every method returns False (never raises).
* :class:`MockExecutor` — deterministic test double recording
  ``(method_name, args)`` tuples with ``fail_for`` / ``results`` hooks.

House style: heavy deps lazy, graceful degradation, headless importable,
deterministic when ``now`` is injected.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import math
import time
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:  # package-relative (normal import path)
    from .interfaces import (
        ActionPlan,
        ActionReport,
        ActionStatus,
        ActionType,
        Intent,
        IntentType,
        Modality,
        SafetyDecision,
        now_ts,
    )
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (
        ActionPlan,
        ActionReport,
        ActionStatus,
        ActionType,
        Intent,
        IntentType,
        Modality,
        SafetyDecision,
        now_ts,
    )

try:  # v10 specialized executors' contracts (§10)
    from .system_actions import (DESTRUCTIVE_FILE_OPS,
                                 DESTRUCTIVE_SYSTEM_OPS, FILE_OPS,
                                 SYSTEM_OPS, validate_url)
except ImportError:  # pragma: no cover
    from airmouse.system_actions import (DESTRUCTIVE_FILE_OPS,
                                         DESTRUCTIVE_SYSTEM_OPS, FILE_OPS,
                                         SYSTEM_OPS, validate_url)

#: allowlist for generic browser operations (§12/§26)
BROWSER_OP_ALLOW = frozenset({
    "click", "focus", "type", "navigate", "new_tab", "close_tab",
    "switch_tab", "back", "forward", "refresh", "scroll", "search",
})

__all__ = [
    "DEFAULT_ACTION_CONFIG",
    "INTENT_TO_ACTION",
    "HOTKEY_DEFAULTS",
    "CLICK_ACTIONS",
    "POINTER_ACTIONS",
    "SCROLL_AMOUNT_LIMIT",
    "TYPE_TEXT_MAX",
    "ActionEngine",
    "PynputExecutor",
    "MockExecutor",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Documented configuration defaults.
DEFAULT_ACTION_CONFIG: Dict[str, Any] = {
    "plan_timeout": 2.0,       # seconds before an execution counts as TIMEOUT
    "max_retries": 1,          # retries on executor exception
    "zoom_ticks": 3,           # ticks per zoom direction when unspecified
    "drag_duration": 0.4,      # seconds for drag completion
}

#: IntentType -> ActionType resolution table.
INTENT_TO_ACTION: Dict[IntentType, ActionType] = {
    IntentType.CLICK: ActionType.CLICK,
    IntentType.DOUBLE_CLICK: ActionType.DOUBLE_CLICK,
    IntentType.RIGHT_CLICK: ActionType.RIGHT_CLICK,
    IntentType.MIDDLE_CLICK: ActionType.MIDDLE_CLICK,
    # click-equivalent semantics
    IntentType.OPEN: ActionType.CLICK,
    IntentType.SELECT: ActionType.SELECT,
    IntentType.PLAY: ActionType.CLICK,
    IntentType.CONFIRM: ActionType.CLICK,
    IntentType.DROP: ActionType.CLICK,     # release-at-point == click
    # pointer
    IntentType.MOVE: ActionType.MOVE,
    IntentType.DRAG: ActionType.DRAG,
    # wheel / zoom
    IntentType.SCROLL: ActionType.SCROLL,
    IntentType.ZOOM: ActionType.ZOOM,
    # text + keys
    IntentType.TYPE: ActionType.TYPE,
    IntentType.HOTKEY: ActionType.HOTKEY,
    IntentType.KEY_PRESS: ActionType.KEY_PRESS,
    IntentType.COPY: ActionType.HOTKEY,
    IntentType.PASTE: ActionType.HOTKEY,
    IntentType.UNDO: ActionType.HOTKEY,
    IntentType.REDO: ActionType.HOTKEY,
    IntentType.CLOSE: ActionType.HOTKEY,
    IntentType.MINIMIZE: ActionType.HOTKEY,
    IntentType.MAXIMIZE: ActionType.HOTKEY,
    IntentType.RESTORE: ActionType.HOTKEY,
    IntentType.SWITCH_WINDOW: ActionType.HOTKEY,
    IntentType.SNAP: ActionType.HOTKEY,
    IntentType.NEW_TAB: ActionType.HOTKEY,
    IntentType.CLOSE_TAB: ActionType.HOTKEY,
    IntentType.SWITCH_TAB: ActionType.HOTKEY,
    IntentType.REFRESH: ActionType.HOTKEY,
    IntentType.FOCUS: ActionType.HOTKEY,
    IntentType.BACK: ActionType.HOTKEY,
    IntentType.FORWARD: ActionType.HOTKEY,
    IntentType.CANCEL: ActionType.KEY_PRESS,
    # v10 system / file / browser / media families (§10)
    IntentType.VOLUME: ActionType.SYSTEM_OPERATION,
    IntentType.BRIGHTNESS: ActionType.SYSTEM_OPERATION,
    IntentType.BLUETOOTH: ActionType.SYSTEM_OPERATION,
    IntentType.LOCK: ActionType.SYSTEM_OPERATION,
    IntentType.SLEEP: ActionType.SYSTEM_OPERATION,
    IntentType.SHUTDOWN: ActionType.SYSTEM_OPERATION,
    IntentType.RESTART: ActionType.SYSTEM_OPERATION,
    IntentType.MEDIA: ActionType.SYSTEM_OPERATION,
    IntentType.SYSTEM_OP: ActionType.SYSTEM_OPERATION,
    IntentType.FILE_OP: ActionType.FILE_OPERATION,
    IntentType.OPEN_URL: ActionType.OPEN_URL,
    IntentType.NAVIGATE: ActionType.NAVIGATE,
    IntentType.BROWSER_OP: ActionType.BROWSER_OPERATION,
    # REPEAT is handled specially in plan(); EMERGENCY_STOP/PAUSE/... have no
    # direct computer action (the safety/agent layers own them).
}

#: Context-neutral default hotkeys per intent (used when params lack keys).
HOTKEY_DEFAULTS: Dict[IntentType, List[str]] = {
    IntentType.COPY: ["ctrl", "c"],
    IntentType.PASTE: ["ctrl", "v"],
    IntentType.UNDO: ["ctrl", "z"],
    IntentType.REDO: ["ctrl", "y"],
    IntentType.CLOSE: ["alt", "f4"],
    IntentType.MINIMIZE: ["win", "down"],
    IntentType.MAXIMIZE: ["win", "up"],
    IntentType.RESTORE: ["win", "down"],
    IntentType.SWITCH_WINDOW: ["alt", "tab"],
    IntentType.SNAP: ["win", "left"],        # direction param overrides
    IntentType.NEW_TAB: ["ctrl", "t"],
    IntentType.CLOSE_TAB: ["ctrl", "w"],
    IntentType.REFRESH: ["f5"],
    IntentType.FOCUS: ["alt", "tab"],
    IntentType.BACK: ["alt", "left"],
    IntentType.FORWARD: ["alt", "right"],
    # SELECT_ALL is SELECT with what=all; keys come from params in plan()
}

#: context-neutral defaults for KEY_PRESS-family intents (cursor moves…)
KEY_DEFAULTS: Dict[str, List[str]] = {
    "page_up": ["pageup"],
    "page_down": ["pagedown"],
    "home": ["home"],
    "end": ["end"],
}

#: Actions that act on a screen point.
POINTER_ACTIONS: set = {
    ActionType.CLICK,
    ActionType.DOUBLE_CLICK,
    ActionType.RIGHT_CLICK,
    ActionType.MIDDLE_CLICK,
    ActionType.MOVE,
}

CLICK_ACTIONS: set = POINTER_ACTIONS - {ActionType.MOVE}

#: Precondition bounds.
SCROLL_AMOUNT_LIMIT: int = 100
TYPE_TEXT_MAX: int = 500

_ACTION_TO_INTENT: Dict[ActionType, IntentType] = {
    v: k for k, v in INTENT_TO_ACTION.items() if k not in (
        IntentType.OPEN, IntentType.SELECT, IntentType.PLAY,
        IntentType.CONFIRM, IntentType.DROP, IntentType.COPY,
        IntentType.PASTE, IntentType.CLOSE, IntentType.MINIMIZE,
        IntentType.MAXIMIZE, IntentType.SWITCH_WINDOW, IntentType.BACK,
        IntentType.FORWARD, IntentType.CANCEL,
    )
}


def _clamp_xy(x: float, y: float, w: int, h: int) -> Tuple[float, float]:
    """Clamp a point into [0, w-1] × [0, h-1] (pure)."""
    cw = max(1, int(w)) - 1
    ch = max(1, int(h)) - 1
    return (min(max(float(x), 0.0), float(cw)), min(max(float(y), 0.0), float(ch)))


# ---------------------------------------------------------------------------
# ActionEngine
# ---------------------------------------------------------------------------


class ActionEngine:
    """Plans and executes actions from intents (safety-gated).

    Args:
        executor: object implementing the ``ActionExecutor`` protocol
            (:class:`PynputExecutor`, :class:`MockExecutor`, ...).
        safety: object exposing ``approve_intent(intent, now=None) ->
            SafetyDecision`` (e.g. :class:`airmouse.safety.SafetySystem`).
    """

    def __init__(
        self,
        executor: Any = None,
        safety: Any = None,
        config: Optional[Dict[str, Any]] = None,
        system_executor: Any = None,
        file_executor: Any = None,
        browser_executor: Any = None,
    ) -> None:
        cfg = dict(DEFAULT_ACTION_CONFIG)
        cfg.update(config or {})
        self.executor = executor
        self.safety = safety
        # v10 specialized executors (§10) — optional; missing ones make the
        # corresponding action family fail gracefully (never crash).
        self.system_executor = system_executor
        self.file_executor = file_executor
        self.browser_executor = browser_executor
        self.plan_timeout: float = float(cfg["plan_timeout"])
        self.max_retries: int = max(0, int(cfg["max_retries"]))
        self.zoom_ticks: int = int(cfg["zoom_ticks"])
        self.drag_duration: float = float(cfg["drag_duration"])
        self.screen_w: int = 0   # 0 = bounds checking disabled
        self.screen_h: int = 0
        self.stats: Dict[ActionStatus, int] = {s: 0 for s in ActionStatus}
        self._last_plan: Optional[ActionPlan] = None

    # -- configuration -------------------------------------------------------

    def set_bounds(self, w: int, h: int) -> None:
        """Set the screen bounds used for point preconditions (0 = no check)."""
        self.screen_w = max(0, int(w))
        self.screen_h = max(0, int(h))
        setter = getattr(self.executor, "set_bounds", None)
        if callable(setter):
            try:
                setter(int(w), int(h))
            except Exception:
                pass

    def reset_stats(self) -> None:
        """Zero the per-:class:`ActionStatus` counters."""
        self.stats = {s: 0 for s in ActionStatus}

    # -- planning --------------------------------------------------------------

    def plan(self, intent: Optional[Intent], now: Optional[float] = None) -> ActionPlan:
        """Resolve an intent into an executable :class:`ActionPlan`.

        ``REPEAT`` re-plans the last concrete plan; with no history a NONE
        plan is returned (which fails preconditions gracefully).
        """
        now = float(now if now is not None else now_ts())
        if intent is None:
            intent = Intent(timestamp=now)
        if intent.type == IntentType.REPEAT:
            last = self._last_plan
            if last is not None:
                rep = replace(
                    last,
                    intent=intent,
                    requires_confirmation=intent.requires_confirmation
                    or last.requires_confirmation,
                )
                self._last_plan = rep
                return rep
            return ActionPlan(
                action=ActionType.NONE, intent=intent, timeout=self.plan_timeout
            )

        action = INTENT_TO_ACTION.get(intent.type, ActionType.NONE)
        params = dict(intent.params or {})
        point = intent.target_point

        if action == ActionType.HOTKEY and not params.get("keys"):
            params["keys"] = list(HOTKEY_DEFAULTS.get(intent.type, []))
        if action == ActionType.KEY_PRESS and not params.get("keys"):
            params["keys"] = ["esc"]   # KEY_PRESS shares the HOTKEY list param
        if action == ActionType.SCROLL and params.get("amount") is None:
            params["amount"] = 3
        if action == ActionType.ZOOM:
            params.setdefault("direction", params.get("direction"))
        if action == ActionType.DRAG:
            params.setdefault("duration", self.drag_duration)

        # ── v10 param normalization (§10) ─────────────────────────────
        params, action = self._normalize_v10_params(
            intent.type, action, params)

        expected = self._build_expected(action, point, params)
        # destructive families require confirmation (§18)
        sensitive = bool(intent.requires_confirmation)
        if action is ActionType.SYSTEM_OPERATION and \
                params.get("op") in DESTRUCTIVE_SYSTEM_OPS:
            sensitive = True
        if action is ActionType.FILE_OPERATION and \
                params.get("op") in DESTRUCTIVE_FILE_OPS:
            sensitive = True
        if intent.type is IntentType.CLOSE_TAB:
            sensitive = True
        planv = ActionPlan(
            action=action,
            point=point,
            target=intent.target,
            params=params,
            expected=expected,
            timeout=float(intent.params.get("timeout", self.plan_timeout))
            if isinstance(intent.params, dict)
            else self.plan_timeout,
            max_retries=max(0, int(getattr(intent, "params", {}).get("max_retries", self.max_retries)))
            if isinstance(getattr(intent, "params", None), dict)
            else self.max_retries,
            requires_confirmation=sensitive,
            intent=intent,
        )
        if action != ActionType.NONE:
            self._last_plan = planv
        return planv

    @staticmethod
    def _build_expected(
        action: ActionType,
        point: Optional[Tuple[float, float]],
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build the verification expectation dict for a plan (pure)."""
        if action in CLICK_ACTIONS and point is not None:
            return {"type": "click", "point": point}
        if action == ActionType.MOVE and point is not None:
            return {"type": "pointer", "point": point}
        if action == ActionType.DRAG:
            end = params.get("end")
            if end is not None:
                return {"type": "pointer", "point": (end[0], end[1])}
            return {}
        if action == ActionType.SCROLL:
            return {"type": "scroll", "delta_min": 1}
        if action == ActionType.ZOOM:
            return {"type": "zoom", "delta_min": 0.01}
        if action in (ActionType.HOTKEY, ActionType.TYPE, ActionType.KEY_PRESS):
            return {"type": "key"}
        return {}

    @staticmethod
    def _normalize_v10_params(intent_type: IntentType, action: ActionType,
                              params: Dict[str, Any]
                              ) -> Tuple[Dict[str, Any], ActionType]:
        """v10 (§10): normalize params per action family (pure).

        May REMAP the action when a family resolves to a simpler
        primitive (e.g. SELECT-all → HOTKEY ctrl+a; NAVIGATE page
        targets → KEY_PRESS).  Never raises.
        """
        try:
            if intent_type is IntentType.SELECT and \
                    str(params.get("what", "")) == "all":
                params["keys"] = ["ctrl", "a"]
                return params, ActionType.HOTKEY
            if intent_type is IntentType.SNAP and \
                not params.get("keys"):
                params["keys"] = ["win",
                                  str(params.get("direction", "left"))]
                return params, ActionType.HOTKEY
            if intent_type is IntentType.SWITCH_TAB and \
                    not params.get("keys"):
                idx = params.get("index") or params.get("entities", {}).get("number")
                try:
                    n = int(idx)
                except Exception:
                    n = 1
                n = max(1, min(8, n))
                params["keys"] = ["ctrl", str(n)]
                return params, ActionType.HOTKEY
            if intent_type is IntentType.NAVIGATE:
                target = str(params.get("target", "") or "")
                if target in KEY_DEFAULTS:
                    params["keys"] = list(KEY_DEFAULTS[target])
                    return params, ActionType.KEY_PRESS
                return params, ActionType.NAVIGATE
            if action is ActionType.SYSTEM_OPERATION:
                op = params.get("op")
                if not op:
                    op_map = {
                        IntentType.VOLUME: {
                            "up": "volume_up", "down": "volume_down",
                            "mute": "mute", "unmute": "unmute"}.get(
                            str(params.get("direction", "up")), "volume_up"),
                        IntentType.BRIGHTNESS: "brightness_up"
                        if str(params.get("direction", "up")) == "up"
                        else "brightness_down",
                        IntentType.BLUETOOTH: "bluetooth_on"
                        if str(params.get("state", "on")) == "on"
                        else "bluetooth_off",
                        IntentType.LOCK: "lock",
                        IntentType.SLEEP: "sleep",
                        IntentType.SHUTDOWN: "shutdown",
                        IntentType.RESTART: "restart",
                        IntentType.MEDIA: {
                            "play": "media_play", "pause": "media_pause",
                            "next": "media_next",
                            "previous": "media_previous"}.get(
                            str(params.get("action", "play")), "media_play"),
                    }
                    op = op_map.get(intent_type)
                params["op"] = str(op or "")
                return params, action
            if action is ActionType.FILE_OPERATION and not params.get("op"):
                params["op"] = "open"
                return params, action
            if intent_type is IntentType.OPEN_URL and params.get("url"):
                ok, cleaned = validate_url(params.get("url"))
                params["url"] = cleaned
                if not ok:
                    params["url_invalid"] = True
                return params, action
            return params, action
        except Exception:
            return params, action

    # -- execution --------------------------------------------------------------

    def execute(
        self, plan: Optional[ActionPlan], now: Optional[float] = None
    ) -> ActionReport:
        """Safety-gate, precondition-check and execute ``plan``.

        Never raises in normal operation: executor exceptions become
        retries and finally a FAILED report.
        """
        report = ActionReport(timestamp=float(now if now is not None else now_ts()))
        if plan is None:
            report.status = ActionStatus.FAILED
            report.message = "no_plan"
            self.stats[ActionStatus.FAILED] += 1
            return report
        report.plan = plan

        # 1. safety gate first — never execute without permission.
        if self.safety is not None:
            intent = plan.intent
            if intent is None:
                intent = self._shim_intent(plan)
            try:
                decision = self.safety.approve_intent(intent, now=now)
            except Exception as exc:  # a broken safety layer blocks, not enables
                report.status = ActionStatus.BLOCKED
                report.message = f"safety_error:{exc}"
                self.stats[ActionStatus.BLOCKED] += 1
                return report
            if decision is None or not getattr(decision, "allowed", False):
                report.status = ActionStatus.BLOCKED
                report.message = getattr(decision, "reason", "") or "blocked"
                self.stats[ActionStatus.BLOCKED] += 1
                return report

        # 2. preconditions.
        problem = self._check_preconditions(plan)
        if problem:
            report.status = ActionStatus.FAILED
            report.message = problem
            self.stats[ActionStatus.FAILED] += 1
            return report

        # 3. execute with retry-on-exception.
        attempts = 0
        t0 = time.perf_counter()
        message = ""
        while attempts <= max(0, int(plan.max_retries)):
            attempts += 1
            try:
                result = self._dispatch(plan)
            except Exception as exc:
                message = f"executor_error:{exc}"
                continue
            if result:
                latency = time.perf_counter() - t0
                if latency > float(plan.timeout):
                    report.status = ActionStatus.TIMEOUT
                    report.message = "execution_exceeded_timeout"
                    report.latency = latency
                    report.attempts = attempts
                    self.stats[ActionStatus.TIMEOUT] += 1
                    return report
                report.status = ActionStatus.SUCCESS
                report.latency = latency
                report.attempts = attempts
                report.observation = self._observation(plan, result)
                self.stats[ActionStatus.SUCCESS] += 1
                return report
            message = message or "executor_returned_false"
            break  # falsy result: report failure, no retry (retry is for exceptions)
        report.status = ActionStatus.FAILED
        report.message = message
        report.latency = time.perf_counter() - t0
        report.attempts = attempts
        self.stats[ActionStatus.FAILED] += 1
        return report

    def execute_intent(
        self, intent: Intent, now: Optional[float] = None
    ) -> ActionReport:
        """Convenience: plan + execute in one call."""
        return self.execute(self.plan(intent, now=now), now=now)

    # -- internals --------------------------------------------------------------

    @staticmethod
    def _shim_intent(plan: ActionPlan) -> Intent:
        """Build a safety-gate shim Intent for a bare plan."""
        return Intent(
            type=_ACTION_TO_INTENT.get(plan.action, IntentType.NONE),
            target=plan.target,
            point=plan.target_point,
            params=dict(plan.params),
            confidence=1.0,  # an explicitly-built plan is user-authorized
            sources=Modality.NONE,
        )

    def _check_preconditions(self, plan: ActionPlan) -> str:
        """Return "" when the plan is executable, else a failure reason."""
        action = plan.action
        if action == ActionType.NONE:
            return "no_action"
        if action in POINTER_ACTIONS:
            pt = plan.target_point
            if pt is None:
                return "missing_point"
            if not all(math.isfinite(float(v)) for v in pt):
                return "invalid_point"
            if self.screen_w > 0 and self.screen_h > 0:
                if not (0 <= pt[0] <= self.screen_w and 0 <= pt[1] <= self.screen_h):
                    return "point_out_of_bounds"
        if action == ActionType.SCROLL:
            amount = plan.params.get("amount")
            if amount is None:
                return "missing_scroll_amount"
            if isinstance(amount, bool) or not isinstance(amount, int):
                return "scroll_amount_not_int"
            if not -SCROLL_AMOUNT_LIMIT <= amount <= SCROLL_AMOUNT_LIMIT:
                return "scroll_amount_out_of_range"
        if action == ActionType.TYPE:
            text = plan.params.get("text")
            if not isinstance(text, str) or not (1 <= len(text) <= TYPE_TEXT_MAX):
                return "invalid_text"
        if action in (ActionType.HOTKEY, ActionType.KEY_PRESS):
            keys = plan.params.get("keys")
            if not isinstance(keys, (list, tuple)) or not (1 <= len(keys) <= 4):
                return "invalid_hotkey"
            if any(not isinstance(k, str) or not k.strip() for k in keys):
                return "invalid_hotkey"
        if action == ActionType.ZOOM:
            direction = plan.params.get("direction")
            ticks = plan.params.get("ticks")
            ok_dir = isinstance(direction, str) and direction in ("in", "out")
            ok_ticks = isinstance(ticks, int) and not isinstance(ticks, bool)
            if not (ok_dir or ok_ticks):
                return "invalid_zoom"
        if action == ActionType.DRAG:
            if plan.target_point is None or plan.params.get("end") is None:
                return "missing_drag_endpoints"
        # ── v10 preconditions (§10) ──────────────────────────────────
        if action == ActionType.SYSTEM_OPERATION:
            op = plan.params.get("op")
            if not isinstance(op, str) or op not in SYSTEM_OPS:
                return "system_op_not_allowed"
        if action == ActionType.FILE_OPERATION:
            op = plan.params.get("op")
            if not isinstance(op, str) or op not in FILE_OPS:
                return "file_op_not_allowed"
        if action == ActionType.OPEN_URL:
            if plan.params.get("url_invalid") or not plan.params.get("url"):
                return "invalid_url"
        if action == ActionType.NAVIGATE:
            has_url = bool(plan.params.get("url")) and \
                not plan.params.get("url_invalid")
            has_query = bool(plan.params.get("query"))
            has_keys = bool(plan.params.get("keys"))
            if not (has_url or has_query or has_keys):
                return "invalid_navigate"
        if action == ActionType.BROWSER_OPERATION:
            op = plan.params.get("op")
            if not isinstance(op, str) or op not in BROWSER_OP_ALLOW:
                return "browser_op_not_allowed"
        return ""

    def _dispatch(self, plan: ActionPlan) -> Any:
        """Call the right executor method (raises on executor failure)."""
        ex = self.executor
        if ex is None:
            raise RuntimeError("no_executor")
        a = plan.action
        params = plan.params
        pt = plan.target_point
        if a == ActionType.CLICK:
            return ex.click(pt[0], pt[1])
        if a == ActionType.DOUBLE_CLICK:
            return ex.double_click(pt[0], pt[1])
        if a == ActionType.RIGHT_CLICK:
            return ex.right_click(pt[0], pt[1])
        if a == ActionType.MIDDLE_CLICK:
            return ex.middle_click(pt[0], pt[1])
        if a == ActionType.MOVE:
            return ex.move(pt[0], pt[1])
        if a == ActionType.SCROLL:
            return ex.scroll(int(params["amount"]))
        if a == ActionType.ZOOM:
            ticks = params.get("ticks")
            if not isinstance(ticks, int) or isinstance(ticks, bool):
                ticks = self.zoom_ticks * (1 if params.get("direction") != "out" else -1)
            zoomer = getattr(ex, "zoom", None)
            if callable(zoomer):
                return zoomer(int(ticks))
            return ex.scroll(int(ticks))  # ctrl semantics live in the executor
        if a == ActionType.TYPE:
            return ex.type_text(str(params["text"]))
        if a == ActionType.HOTKEY:
            return ex.hotkey(*[str(k) for k in params["keys"]])
        if a == ActionType.KEY_PRESS:
            return ex.hotkey(*[str(k) for k in params["keys"]])
        if a == ActionType.DRAG:
            end = params["end"]
            return ex.drag(pt[0], pt[1], end[0], end[1],
                           float(params.get("duration", self.drag_duration)))
        # ── v10 dispatch (§10) ─────────────────────────────────────
        if a in (ActionType.SELECT,):
            if str(params.get("what", "")) == "all":
                return ex.hotkey("ctrl", "a")
            if pt is None:
                raise RuntimeError("missing_point")
            return ex.click(pt[0], pt[1])
        if a is ActionType.SYSTEM_OPERATION:
            sex = self.system_executor
            if sex is None:
                raise RuntimeError("no_system_executor")
            result = sex.execute(str(params.get("op", "")), params)
            return {"system_op": str(params.get("op", "")),
                    "ok": bool(getattr(result, "ok", False)),
                    "message": str(getattr(result, "message", ""))}
        if a is ActionType.FILE_OPERATION:
            fex = self.file_executor
            if fex is None:
                raise RuntimeError("no_file_executor")
            result = fex.execute(str(params.get("op", "")), params)
            return {"file_op": str(params.get("op", "")),
                    "ok": bool(getattr(result, "ok", False)),
                    "message": str(getattr(result, "message", ""))}
        if a in (ActionType.OPEN_URL, ActionType.NAVIGATE,
                 ActionType.BROWSER_OPERATION):
            bex = self.browser_executor
            url = str(params.get("url", "") or "")
            query = str(params.get("query", "") or "")
            op = str(params.get("op", "") or "")
            if bex is not None:
                if a is ActionType.OPEN_URL or (a is ActionType.NAVIGATE
                                                and url):
                    result = bex.perform("navigate", {"url": url})
                elif a is ActionType.NAVIGATE and query:
                    result = bex.perform("search", {"query": query})
                else:
                    result = bex.perform(op, dict(params))
                return {"browser_op": op or "navigate",
                        **(dict(result) if isinstance(result, dict) else
                           {"ok": bool(result)})}
            # deterministic fallback without a bridge: focus the address
            # bar and type (safe, no shell, no eval)
            if url or query:
                text = url or query
                r1 = ex.hotkey("ctrl", "l")
                r2 = ex.type_text(text)
                r3 = ex.hotkey("enter")
                return {"browser_op": "navigate", "ok": bool(r1 and r2 and r3)}
            raise RuntimeError("no_browser_executor")
        raise RuntimeError(f"unsupported_action:{a}")

    @staticmethod
    def _observation(plan: ActionPlan, result: Any) -> Dict[str, Any]:
        """Build the report observation (executor dict wins, else pointer)."""
        if isinstance(result, dict):
            return dict(result)
        if plan.action in POINTER_ACTIONS:
            pt = plan.target_point
            return {"pointer": (float(pt[0]), float(pt[1]))}
        return {}


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


class PynputExecutor:
    """Real executor over pynput — lazy import, graceful degradation.

    pynput is imported INSIDE each method (house rule).  If the import or
    controller creation fails (headless box, missing display), ``available``
    becomes False and every method returns False without raising.
    Coordinates are clamped to the configured bounds.
    """

    # pynput Key attribute aliases (resolved lazily against keyboard.Key).
    _KEY_ALIASES: Dict[str, str] = {
        "ctrl": "ctrl", "control": "ctrl", "ctrl_r": "ctrl_r",
        "alt": "alt", "alt_r": "alt_r", "altgr": "alt_gr",
        "shift": "shift", "shift_r": "shift_r",
        "win": "cmd", "cmd": "cmd", "super": "cmd", "meta": "cmd",
        "esc": "esc", "escape": "esc", "tab": "tab", "enter": "enter",
        "return": "enter", "space": "space", "backspace": "backspace",
        "delete": "delete", "del": "delete", "home": "home", "end": "end",
        "page_up": "page_up", "pageup": "page_up", "page_down": "page_down",
        "pagedown": "page_down", "up": "up", "down": "down", "left": "left",
        "right": "right", "menu": "menu", "insert": "insert",
        "caps_lock": "caps_lock",
    }
    for _i in range(1, 13):
        _KEY_ALIASES[f"f{_i}"] = f"f{_i}"

    def __init__(self, screen_w: int = 1920, screen_h: int = 1080) -> None:
        self.screen_w = int(screen_w)
        self.screen_h = int(screen_h)
        self._mouse: Any = None
        self._keyboard: Any = None
        self.available: bool = self._probe()

    # -- lifecycle -----------------------------------------------------------

    def set_bounds(self, w: int, h: int) -> None:
        """Update the clamping bounds."""
        self.screen_w = max(1, int(w))
        self.screen_h = max(1, int(h))

    def _probe(self) -> bool:
        """One-shot availability probe at construction time (never raises)."""
        try:
            self._ensure_mouse()
            self._ensure()
            return self._mouse is not None and self._keyboard is not None
        except Exception:
            return False

    def _ensure(self) -> bool:
        """Ensure BOTH pynput controllers exist; False when unavailable."""
        if not self._ensure_mouse():
            return False
        if self._keyboard is not None:
            return True
        try:  # lazy, per-method import (house rule)
            import pynput  # noqa: F401  (import guards availability)
            from pynput import keyboard as _kb

            self._keyboard = _kb.Controller()
            self.available = True
            return True
        except Exception:
            self.available = False
            return False

    def _ensure_mouse(self) -> bool:
        """Ensure the mouse controller exists (mouse-only operations)."""
        if self._mouse is not None:
            return True
        try:  # lazy, per-method import (house rule)
            from pynput import mouse as _mouse

            self._mouse = _mouse.Controller()
            return True
        except Exception:
            self.available = False
            return False

    # -- actions ---------------------------------------------------------------

    def click(self, x: float, y: float) -> bool:
        if not self._ensure_mouse():
            return False
        try:
            x, y = _clamp_xy(x, y, self.screen_w, self.screen_h)
            self._mouse.position = (x, y)
            self._mouse.click(pynput_button_left())
            return True
        except Exception:
            return False

    def double_click(self, x: float, y: float) -> bool:
        if not self._ensure_mouse():
            return False
        try:
            x, y = _clamp_xy(x, y, self.screen_w, self.screen_h)
            self._mouse.position = (x, y)
            self._mouse.click(pynput_button_left(), 2)
            return True
        except Exception:
            return False

    def right_click(self, x: float, y: float) -> bool:
        if not self._ensure_mouse():
            return False
        try:
            x, y = _clamp_xy(x, y, self.screen_w, self.screen_h)
            self._mouse.position = (x, y)
            self._mouse.click(pynput_button_right())
            return True
        except Exception:
            return False

    def middle_click(self, x: float, y: float) -> bool:
        if not self._ensure_mouse():
            return False
        try:
            x, y = _clamp_xy(x, y, self.screen_w, self.screen_h)
            self._mouse.position = (x, y)
            self._mouse.click(pynput_button_middle())
            return True
        except Exception:
            return False

    def move(self, x: float, y: float) -> bool:
        if not self._ensure_mouse():
            return False
        try:
            x, y = _clamp_xy(x, y, self.screen_w, self.screen_h)
            self._mouse.position = (x, y)
            return True
        except Exception:
            return False

    def scroll(self, amount: int) -> bool:
        if not self._ensure_mouse():
            return False
        try:
            self._mouse.scroll(0, int(amount))
            return True
        except Exception:
            return False

    def zoom(self, ticks: int) -> bool:
        """Ctrl + scroll (pinch-zoom semantics); False when unavailable."""
        if not self._ensure():
            return False
        try:
            from pynput.keyboard import Key

            ctrl = Key.ctrl
            try:
                ctrl = self._resolve_key("ctrl") or Key.ctrl
            except Exception:
                pass
            self._keyboard.press(ctrl)
            try:
                self._mouse.scroll(0, int(ticks))
            finally:
                self._keyboard.release(ctrl)
            return True
        except Exception:
            return False

    def type_text(self, text: str) -> bool:
        if not self._ensure():
            return False
        try:
            self._keyboard.type(str(text))
            return True
        except Exception:
            return False

    def hotkey(self, *keys: Any) -> bool:
        """Press a hotkey.  Accepts ``hotkey(*keys)`` (engine convention) or
        ``hotkey([keys...])`` (sequence convention)."""
        if len(keys) == 1 and isinstance(keys[0], (list, tuple)):
            keys = tuple(keys[0])
        if not self._ensure():
            return False
        resolved = []
        try:
            for name in keys:
                key = self._resolve_key(str(name))
                if key is None:
                    return False
                resolved.append(key)
            for key in resolved:
                self._keyboard.press(key)
            for key in reversed(resolved):
                self._keyboard.release(key)
            return True
        except Exception:
            return False

    def drag(self, x0: float, y0: float, x1: float, y1: float,
             duration: float = 0.4) -> bool:
        """Press → 8 interpolated move chunks (0.02 s sleeps) → release."""
        if not self._ensure_mouse():
            return False
        try:
            button = pynput_button_left()

            x0, y0 = _clamp_xy(x0, y0, self.screen_w, self.screen_h)
            x1, y1 = _clamp_xy(x1, y1, self.screen_w, self.screen_h)
            self._mouse.position = (x0, y0)
            self._mouse.press(button)
            try:
                interval = max(0.005, min(0.02, float(duration) / 8.0))
                for i in range(1, 9):
                    t = i / 8.0
                    self._mouse.position = (
                        x0 + (x1 - x0) * t,
                        y0 + (y1 - y0) * t,
                    )
                    time.sleep(interval)
            finally:
                self._mouse.release(button)
            return True
        except Exception:
            return False

    # -- helpers ---------------------------------------------------------------

    def _resolve_key(self, name: str) -> Any:
        """Map a key name to a pynput Key or KeyCode (None when unknown)."""
        from pynput import keyboard as _kb

        alias = self._KEY_ALIASES.get(str(name).strip().lower())
        if alias:
            return getattr(_kb.Key, alias, None)
        raw = str(name)
        if len(raw) == 1:
            return _kb.KeyCode.from_char(raw)
        return None


def pynput_button_left() -> Any:
    """Lazy pynput Button.left accessor."""
    from pynput.mouse import Button

    return Button.left


def pynput_button_right() -> Any:
    from pynput.mouse import Button

    return Button.right


def pynput_button_middle() -> Any:
    from pynput.mouse import Button

    return Button.middle


class MockExecutor:
    """Deterministic test double implementing the full executor protocol.

    Records every call as a ``(method_name, args)`` tuple in ``record``.
    ``fail_for`` is a set of method names that raise RuntimeError;
    ``results`` maps method names to custom return values (a dict result is
    adopted as the report observation).
    """

    def __init__(
        self,
        record: Optional[List[Tuple[str, tuple]]] = None,
        screen_w: int = 1920,
        screen_h: int = 1080,
    ) -> None:
        self.record: List[Tuple[str, tuple]] = (
            record if record is not None else []
        )
        self.fail_for: set = set()
        self.results: Dict[str, Any] = {}
        self.screen_w = int(screen_w)
        self.screen_h = int(screen_h)

    def set_bounds(self, w: int, h: int) -> None:
        self.screen_w = max(1, int(w))
        self.screen_h = max(1, int(h))

    def _do(self, name: str, *args: Any) -> Any:
        self.record.append((name, args))
        if name in self.fail_for:
            raise RuntimeError(f"mock failure: {name}")
        return self.results.get(name, True)

    def click(self, x: float, y: float) -> bool:
        return self._do("click", x, y)

    def double_click(self, x: float, y: float) -> bool:
        return self._do("double_click", x, y)

    def right_click(self, x: float, y: float) -> bool:
        return self._do("right_click", x, y)

    def middle_click(self, x: float, y: float) -> bool:
        return self._do("middle_click", x, y)

    def move(self, x: float, y: float) -> bool:
        return self._do("move", x, y)

    def scroll(self, amount: int) -> bool:
        return self._do("scroll", amount)

    def zoom(self, ticks: int) -> bool:
        return self._do("zoom", ticks)

    def type_text(self, text: str) -> bool:
        return self._do("type_text", text)

    def hotkey(self, *keys: Any) -> bool:
        """Record a hotkey.  Accepts ``hotkey(*keys)`` (engine convention)
        or ``hotkey([keys...])`` (sequence convention) — both normalize to
        a flat ("k1", "k2", ...) record tuple."""
        if len(keys) == 1 and isinstance(keys[0], (list, tuple)):
            keys = tuple(keys[0])
        return self._do("hotkey", *[str(k) for k in keys])

    def drag(self, x0: float, y0: float, x1: float, y1: float,
             duration: float = 0.4) -> bool:
        return self._do("drag", x0, y0, x1, y1, duration)
