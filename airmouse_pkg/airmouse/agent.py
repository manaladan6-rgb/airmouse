"""
airmouse.agent — v9 InteractionAgent 🤖 (pipeline orchestrator)
===============================================================

THE v9 object: wires every subsystem (gaze → screen perception → fusion →
NL → intent → safety → actions → verification → recovery → macros →
hands-free) behind ONE deterministic per-frame entry point
:meth:`InteractionAgent.process_frame`, plus rolling :class:`Telemetry`.

Construction is lazy and fully injectable — every subsystem can be
replaced via ``overrides`` (``safety=``, ``screen=``, ``fusion=``,
``intent_engine=``, ``action_engine=``, ``executor=``, ``verifier=``,
``recovery=``, ``gaze=``, ``nl=``, ``hands_free=``, ``program_runner=``,
``screen_providers=``, ``context_resolver=``), so the whole pipeline runs
headless with fakes.  With NO overrides nothing heavy is imported until a
real action actually executes (the ``PynputExecutor`` is created lazily on
first execute; an engine without an executor degrades to FAILED reports,
never exceptions).

Per-frame pipeline (``process_frame``)
--------------------------------------
1. gaze — frame/landmarks (+ ``gaze_enabled``) → ``GazeEngine.update`` →
   :class:`GazeState` (a pre-computed ``gaze_state`` skips the engine —
   simulation-friendly);
2. hand — ``hand_data`` dict (``gesture``/``point``/``confidence`` like the
   v5 main loop) → fusion HAND + ``intent_engine.submit_gesture``;
3. screen — throttled ``ScreenPerceptionEngine.update`` → semantic target
   under the gaze point → fusion GAZE;
4. voice — utterance → :class:`NLController` (+ fusion VOICE);
5. ``fusion.update(now)`` → decision (fusion latency measured);
6. ``intent_engine.process(decision, utterance)`` (+ v9 NL intents);
7. per intent: safety gate → action engine → verification (screen-model
   observe fn) → bounded recovery on failure — all counted in telemetry;
8. result dict ``{"decision", "intents", "reports", "gaze_state",
   "screen_model", "context", "nlu", "mode"}``.

Safety invariants
-----------------
* The agent must NOT auto-click from low-confidence gaze: a click-class
  intent carrying the GAZE source is dropped when the CURRENT gaze
  confidence is below ``min_gaze_confidence`` (defence in depth on top of
  the safety + intent gates).
* Uncertain-gaze / sensitive-intent / e-stop behaviour all flow through
  :class:`SafetySystem` — confirmation-gated intents arm the flow and are
  retried automatically after a successful CONFIRM intent.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

try:  # package-relative (normal import path)
    from .interfaces import (
        ActionReport,
        ActionStatus,
        AppContext,
        FusionMode,
        GazeState,
        Intent,
        IntentType,
        Modality,
        ScreenModel,
        TelemetryStats,
        now_ts,
    )
    from .nl_control import NLController, nlu_to_intent
    from .hands_free import (
        HandsFreeController,
        debounce_key,
        run_intent_through_safety,
    )
    from .fusion import MultimodalFusion
    from .intent import IntentEngine
    from .actions import ActionEngine
    from .verification import ActionVerifier, RecoveryManager
    from .safety import SafetySystem
    from . import context as _context
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (
        ActionReport,
        ActionStatus,
        AppContext,
        FusionMode,
        GazeState,
        Intent,
        IntentType,
        Modality,
        ScreenModel,
        TelemetryStats,
        now_ts,
    )
    from airmouse.nl_control import NLController, nlu_to_intent
    from airmouse.hands_free import (
        HandsFreeController,
        debounce_key,
        run_intent_through_safety,
    )
    from airmouse.fusion import MultimodalFusion
    from airmouse.intent import IntentEngine
    from airmouse.actions import ActionEngine
    from airmouse.verification import ActionVerifier, RecoveryManager
    from airmouse.safety import SafetySystem
    from airmouse import context as _context

__all__ = ["AgentConfig", "Telemetry", "InteractionAgent"]

CLICK_CLASS = {
    IntentType.CLICK, IntentType.DOUBLE_CLICK,
    IntentType.RIGHT_CLICK, IntentType.MIDDLE_CLICK,
}


# ---------------------------------------------------------------------------
# AgentConfig
# ---------------------------------------------------------------------------


@dataclass
class AgentConfig:
    """Mirrors the v9 config sections (all overridable per constructor).

    ``config`` dict keys map 1:1 onto these fields.
    """

    gaze_enabled: bool = True
    hand_enabled: bool = True
    voice_enabled: bool = True
    mode: str = "fusion"                 # FusionMode value / name
    safety_level: str = "normal"         # SafetyLevel value
    perf_window: int = 120               # telemetry rolling window (samples)
    screen_w: int = 1920
    screen_h: int = 1080
    screen_refresh: float = 0.5          # s — screen model cache interval
    verify_actions: bool = True          # post-action verification on/off
    lazy_executor: bool = True           # PynputExecutor on first real execute
    nl_dedup_window: float = 1.2         # s — NLController dedup
    dwell_confirm: bool = True           # hands-free dwell click
    blink_confirm: bool = False          # hands-free blink click (OFF: safety)
    long_blink_estop: bool = True        # hands-free long-blink e-stop
    command_debounce: float = 0.6        # s — identical command suppression
    min_gaze_confidence: float = 0.55    # gaze click gate (defence in depth)
    # forwarded subsections
    gaze_config: Dict[str, Any] = field(default_factory=dict)
    fusion_config: Dict[str, Any] = field(default_factory=dict)
    intent_config: Dict[str, Any] = field(default_factory=dict)
    safety_config: Dict[str, Any] = field(default_factory=dict)
    action_config: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_mapping(data: Optional[Dict[str, Any]]) -> "AgentConfig":
        """Build a config from a dict (unknown keys ignored)."""
        cfg = AgentConfig()
        if not data:
            return cfg
        for key, value in dict(data).items():
            if hasattr(cfg, key):
                try:
                    setattr(cfg, key, value)
                except Exception:
                    pass
        return cfg


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


class Telemetry:
    """Rolling-window performance + behaviour statistics (thread-safe).

    ``record(event, value)`` routes by event name:

    * metric events (``fps_gaze``, ``fps_hand``, ``fps_camera``,
      ``latency_gaze_ms``, ``latency_cursor_ms``, ``latency_fusion_ms``,
      ``latency_voice_ms``, ``latency_action_ms``) append to a bounded
      deque (``window`` samples); the properties expose an EMA over it;
    * counter events (``actions_total``, ``actions_success``,
      ``actions_failed``, ``actions_blocked``, ``recoveries``,
      ``estops``, ...) increment integer counters (``value`` adds).
    """

    METRIC_EVENTS = frozenset({
        "fps_gaze", "fps_hand", "fps_camera",
        "latency_gaze_ms", "latency_cursor_ms", "latency_fusion_ms",
        "latency_voice_ms", "latency_action_ms",
    })
    COUNTER_EVENTS = frozenset({
        "actions_total", "actions_success", "actions_failed",
        "actions_blocked", "recoveries", "estops",
    })
    _EMA_ALPHA: float = 0.3

    def __init__(self, window: int = 120) -> None:
        self.window = max(8, int(window))
        self._metrics: Dict[str, List[float]] = {}
        self._counters: Dict[str, int] = {k: 0 for k in self.COUNTER_EVENTS}
        self._lock = threading.RLock()

    # -- recording ------------------------------------------------------------

    def record(self, event: str, value: float = 1.0) -> None:
        """Record one metric sample or counter increment (never raises)."""
        try:
            name = str(event)
            with self._lock:
                if name in self.COUNTER_EVENTS:
                    self._counters[name] = self._counters.get(name, 0) + int(value)
                else:
                    buf = self._metrics.setdefault(name, [])
                    buf.append(float(value))
                    if len(buf) > self.window:
                        del buf[: len(buf) - self.window]
        except Exception:
            pass

    def _ema(self, name: str) -> float:
        """EMA over the rolling buffer (deterministic, 0.0 when empty)."""
        with self._lock:
            buf = self._metrics.get(name) or []
            if not buf:
                return 0.0
            ema = float(buf[0])
            alpha = self._EMA_ALPHA
            for sample in buf[1:]:
                ema = alpha * float(sample) + (1.0 - alpha) * ema
            return ema

    # -- metric properties -------------------------------------------------------

    @property
    def fps_gaze(self) -> float:
        return self._ema("fps_gaze")

    @property
    def fps_hand(self) -> float:
        return self._ema("fps_hand")

    @property
    def fps_camera(self) -> float:
        return self._ema("fps_camera")

    @property
    def latency_gaze_ms(self) -> float:
        return self._ema("latency_gaze_ms")

    @property
    def latency_cursor_ms(self) -> float:
        return self._ema("latency_cursor_ms")

    @property
    def latency_fusion_ms(self) -> float:
        return self._ema("latency_fusion_ms")

    @property
    def latency_voice_ms(self) -> float:
        return self._ema("latency_voice_ms")

    @property
    def latency_action_ms(self) -> float:
        return self._ema("latency_action_ms")

    @property
    def counters(self) -> Dict[str, int]:
        """Copy of the behaviour counters."""
        with self._lock:
            return dict(self._counters)

    # -- snapshot / reset -----------------------------------------------------------

    def snapshot(self) -> TelemetryStats:
        """One :class:`interfaces.TelemetryStats` snapshot."""
        c = self.counters
        return TelemetryStats(
            fps_camera=self.fps_camera,
            fps_gaze=self.fps_gaze,
            fps_hand=self.fps_hand,
            latency_gaze_ms=self.latency_gaze_ms,
            latency_cursor_ms=self.latency_cursor_ms,
            latency_fusion_ms=self.latency_fusion_ms,
            latency_voice_ms=self.latency_voice_ms,
            latency_action_ms=self.latency_action_ms,
            actions_total=c.get("actions_total", 0),
            actions_success=c.get("actions_success", 0),
            actions_failed=c.get("actions_failed", 0),
            actions_blocked=c.get("actions_blocked", 0),
            recoveries=c.get("recoveries", 0),
            estop_count=c.get("estops", 0),
        )

    def reset(self) -> None:
        """Zero every metric buffer and counter."""
        with self._lock:
            self._metrics.clear()
            self._counters = {k: 0 for k in self.COUNTER_EVENTS}


# ---------------------------------------------------------------------------
# InteractionAgent
# ---------------------------------------------------------------------------


class InteractionAgent:
    """The v9 orchestrator: every modality, one deterministic pipeline.

    Example::

        agent = InteractionAgent({"mode": "fusion"},
                                 executor=MockExecutor(),
                                 screen_providers=[FakeProvider()])
        out = agent.process_frame(
            hand_data={"gesture": "pinch", "point": (960, 540), "confidence": 0.9},
            utterance="click that", now=1.0)
        out["reports"][0].ok  # -> True
    """

    _COMPONENT_OVERRIDES = frozenset({
        "safety", "screen", "screen_providers", "context_resolver", "fusion",
        "intent_engine", "action_engine", "executor", "verifier", "recovery",
        "gaze", "nl", "hands_free", "program_runner",
        # v10: universal-offline-interaction-engine components
        "event_bus", "context_engine", "browser", "gesture_registry", "rf",
        "voice_engine", "system_executor", "file_executor",
        "browser_executor",
    })

    def __init__(self,
                 config: Optional[Dict[str, Any]] = None,
                 **overrides: Any) -> None:
        if isinstance(config, AgentConfig):
            self.config = config
        else:
            self.config = AgentConfig.from_mapping(config)
        cfg = self.config

        # -- safety ---------------------------------------------------------
        self.safety = overrides.get("safety") or SafetySystem(
            {**cfg.safety_config, "level": cfg.safety_level})
        # -- screen perception ----------------------------------------------
        resolver = overrides.get("context_resolver", _context.detect_app_context)
        self.screen = overrides.get("screen")
        if self.screen is None:
            from .screen_perception import ScreenPerceptionEngine
            self.screen = ScreenPerceptionEngine(
                cfg.screen_w, cfg.screen_h,
                config={"refresh_interval": cfg.screen_refresh},
                providers=overrides.get("screen_providers"),
                context_resolver=resolver,
            )
        # -- fusion / intent ---------------------------------------------------
        self.fusion = overrides.get("fusion") or MultimodalFusion(
            mode=cfg.mode, config=cfg.fusion_config)
        self.intent_engine = overrides.get("intent_engine") or \
            IntentEngine(cfg.intent_config)
        # -- actions (safety is gated by the AGENT, not double-gated) ----------
        self.action_engine = overrides.get("action_engine") or ActionEngine(
            executor=overrides.get("executor"), safety=None,
            config=cfg.action_config,
            system_executor=overrides.get("system_executor"),
            file_executor=overrides.get("file_executor"),
            browser_executor=overrides.get("browser_executor"))
        self.action_engine.set_bounds(cfg.screen_w, cfg.screen_h)
        # -- verification + recovery ---------------------------------------------
        self.verifier = overrides.get("verifier") or ActionVerifier()
        self.recovery = overrides.get("recovery") or RecoveryManager()
        # -- gaze --------------------------------------------------------------
        self.gaze = overrides.get("gaze")
        if self.gaze is None and cfg.gaze_enabled:
            from .gaze import GazeEngine
            self.gaze = GazeEngine(cfg.gaze_config)
        # -- NL -------------------------------------------------------------------
        self.nl = overrides.get("nl") or NLController(
            {"dedup_window": cfg.nl_dedup_window})
        # -- v10 components (all optional; None = feature off) -----------------
        self.event_bus = overrides.get("event_bus")
        self.context_engine = overrides.get("context_engine")
        if self.context_engine is None:
            try:
                from .context import ContextEngine
                self.context_engine = ContextEngine()
            except Exception:
                self.context_engine = None
        self.gesture_registry = overrides.get("gesture_registry")
        self.rf_bridge = overrides.get("rf")
        self.browser = overrides.get("browser")
        self.voice_engine = overrides.get("voice_engine")
        self._injected: List[Intent] = []
        # -- telemetry ------------------------------------------------------------
        self.telemetry = Telemetry(cfg.perf_window)
        # -- hands-free controller (built now when starting in that mode) --------
        self.hands_free: Optional[HandsFreeController] = overrides.get("hands_free")
        if self.hands_free is None and self.fusion.mode is FusionMode.HANDS_FREE:
            self.hands_free = self._build_hands_free()
        # -- macros -------------------------------------------------------------
        self._program_runner = overrides.get("program_runner")
        self._macro_recorder: Any = None
        # -- internals ------------------------------------------------------------
        self._last_pending_retry: Optional[Intent] = None
        self._lazy_executor_tried: bool = False
        self._last_gaze_wall: Optional[float] = None
        self._closed: bool = False
        self._lock = threading.RLock()

    # -- construction helpers ------------------------------------------------------

    def _build_hands_free(self) -> HandsFreeController:
        """Build the hands-free controller over the SHARED subsystems."""
        cfg = self.config
        return HandsFreeController(
            {
                "dwell_confirm": cfg.dwell_confirm,
                "blink_confirm": cfg.blink_confirm,
                "long_blink_estop": cfg.long_blink_estop,
                "min_gaze_confidence": cfg.min_gaze_confidence,
                "command_debounce": cfg.command_debounce,
            },
            fusion=self.fusion,
            intent_engine=self.intent_engine,
            action_engine=self.action_engine,
            safety=self.safety,
            screen=self.screen,
            nl=self.nl,
        )

    def _ensure_executor(self) -> Any:
        """Create the real PynputExecutor lazily (once, best-effort)."""
        if self.action_engine.executor is not None:
            return self.action_engine.executor
        if not self.config.lazy_executor or self._lazy_executor_tried:
            return None
        self._lazy_executor_tried = True
        try:
            from .actions import PynputExecutor
            executor = PynputExecutor(self.config.screen_w, self.config.screen_h)
            self.action_engine.executor = executor
            return executor
        except Exception:
            return None

    # -- mode / safety API ---------------------------------------------------------

    def set_mode(self, mode: Any) -> bool:
        """Switch the fusion mode (rate-limited by the fusion engine).

        Toggles the hands-free controller: it is created on the first
        switch into ``HANDS_FREE`` and simply unused in other modes.
        """
        ok = bool(self.fusion.set_mode(mode))
        if self.fusion.mode is FusionMode.HANDS_FREE and self.hands_free is None:
            self.hands_free = self._build_hands_free()
        return ok

    def trip_estop(self, reason: str = "agent_api") -> None:
        """Latch the safety e-stop (blocks every intent until reset)."""
        self.safety.trip(reason)
        self.telemetry.record("estops", 1)

    def reset_estop(self) -> None:
        """Manual e-stack reset passthrough (keyboard / 'confirm reset')."""
        self.safety.reset()

    def set_safety_level(self, level: Any) -> None:
        """Set the safety posture (``SafetyLevel`` or its value)."""
        self.safety.set_level(level)
        try:
            self.config.safety_level = str(getattr(level, "value", level))
        except Exception:
            pass

    # -- v10 external intent injection ---------------------------------------------

    def inject_intent(self, intent: Intent) -> bool:
        """Queue an EXTERNALLY resolved intent (offline voice, gesture
        registry, RF bridge) for execution on the next tick.

        The intent flows through the same safety gate, action engine,
        verification and recovery as every other intent.  Thread-safe.
        """
        if intent is None:
            return False
        with self._lock:
            self._injected.append(intent)
        return True

    def poll_events(self, now: Optional[float] = None) -> int:
        """Drain the optional v10 producers (voice engine, RF bridge,
        browser controller) and convert their events into injected
        intents.  Returns the number of intents queued.

        Deterministic given (events, now); every producer is optional
        and every failure is swallowed (modality independence, §16).
        """
        now = float(now if now is not None else now_ts())
        queued = 0
        # offline voice engine → intents
        ve = self.voice_engine
        if ve is not None:
            try:
                while True:
                    ev = ve.poll()
                    if ev is None:
                        break
                    payload = getattr(ev, "payload", {}) or {}
                    intent = payload.get("intent")
                    if intent is not None:
                        if self.inject_intent(intent):
                            queued += 1
                    elif payload.get("text") and not payload.get("unmatched"):
                        # committed dictation → TYPE intent (§5)
                        d = Intent(type=IntentType.TYPE,
                                   params={"text": str(payload["text"])[:500]},
                                   confidence=float(getattr(ev, "confidence", 0.5) or 0.5),
                                   sources=Modality.VOICE,
                                   utterance=str(payload["text"]),
                                   timestamp=now)
                        if self.inject_intent(d):
                            queued += 1
            except Exception:
                pass
        # RF bridge → gesture registry → intents
        rf = self.rf_bridge
        if rf is not None and rf.available():
            try:
                for ev, rf_event in rf.poll(now):
                    label = str((getattr(ev, "payload", {}) or {}).get("label", ""))
                    if not label:
                        continue
                    intent = None
                    reg = self.gesture_registry
                    if reg is not None:
                        _, intent = reg.feed(
                            label, confidence=float(getattr(ev, "confidence", 0.5)),
                            now=now)
                    else:
                        intent = Intent(
                            type=IntentType.SWITCH_WINDOW
                            if label in ("swipe_left", "swipe_right")
                            else IntentType.SCROLL,
                            params={"direction": label.replace("swipe_", "")},
                            confidence=float(getattr(ev, "confidence", 0.5)),
                            sources=Modality.RF, utterance="rf:" + label,
                            timestamp=now)
                    if intent is not None and self.inject_intent(intent):
                        queued += 1
            except Exception:
                pass
        # browser controller → context + screen targets
        br = self.browser
        if br is not None:
            try:
                br.poll(now)
            except Exception:
                pass
        return queued

    # -- the per-frame pipeline -------------------------------------------------------

    def process_frame(self,
                      frame: Any = None,
                      hand_data: Optional[Dict[str, Any]] = None,
                      utterance: str = "",
                      now: Optional[float] = None,
                      gaze_state: Optional[GazeState] = None) -> Dict[str, Any]:
        """Run ONE full pipeline step.  Fully headless-safe: every input is
        optional and ``frame=None, hand_data=None, utterance=""`` is a
        valid idle tick.  Deterministic when ``now`` is supplied.
        """
        t0 = float(now if now is not None else now_ts())
        cfg = self.config

        # 0. v10 producers: drain voice/RF/browser events into intents (§3)
        try:
            self.poll_events(t0)
        except Exception:
            pass

        # 1. gaze ----------------------------------------------------------------
        gstate = gaze_state
        if gstate is None and self.gaze is not None and cfg.gaze_enabled \
                and frame is not None:
            tg0 = time.perf_counter()
            gstate = self.gaze.update(frame, t0)
            wall = time.perf_counter()
            if self._last_gaze_wall is not None:
                dt = wall - self._last_gaze_wall
                if dt > 1e-6:
                    self.telemetry.record("fps_gaze", 1.0 / dt)
            self._last_gaze_wall = wall
            self.telemetry.record("latency_gaze_ms", (wall - tg0) * 1000.0)

        # hands-free mode: the controller owns fusion/intent/action.
        if self.fusion.mode is FusionMode.HANDS_FREE and self.hands_free is not None:
            return self._process_hands_free(gstate, utterance, t0)

        # 2. hand ----------------------------------------------------------------
        if hand_data and cfg.hand_enabled:
            point = hand_data.get("point")
            gesture = str(hand_data.get("gesture", "") or "")
            conf = float(hand_data.get("confidence", 0.5) or 0.0)
            try:
                self.fusion.update_hand(point, gesture, conf, t0)
            except Exception:
                pass
            if gesture:
                try:
                    self.intent_engine.submit_gesture(gesture, point, conf, t0)
                except Exception:
                    pass

        # 3. screen perception + gaze → fusion -----------------------------------
        model: Optional[ScreenModel] = None
        if self.screen is not None:
            try:
                model = self.screen.update(t0)
            except Exception:
                model = None
        # v10 context engine: window + gaze target bookkeeping (§8)
        ctx = self.context_engine
        if ctx is not None:
            try:
                ctx.update_screen(cfg.screen_w, cfg.screen_h)
                if model is not None and getattr(model, "active_window_title", ""):
                    ctx.update_window(model.active_window_title, now=t0)
                ctx.set_mode(getattr(self.fusion.mode, "value", "fusion"),
                             now=t0)
            except Exception:
                pass
        if gstate is not None:
            conf = float(getattr(gstate, "confidence", 0.0) or 0.0)
            if bool(getattr(gstate, "screen_valid", False)):
                point = (float(getattr(gstate, "screen_x", 0.0)),
                         float(getattr(gstate, "screen_y", 0.0)))
            else:
                sw = int(getattr(model, "screen_w", cfg.screen_w) or cfg.screen_w)
                sh = int(getattr(model, "screen_h", cfg.screen_h) or cfg.screen_h)
                point = (float(getattr(gstate, "x", 0.5)) * sw,
                         float(getattr(gstate, "y", 0.5)) * sh)
            target = None
            if model is not None:
                try:
                    target = model.target_at(*point)
                except Exception:
                    target = None
            try:
                self.fusion.update_gaze(point, target, conf, t0)
            except Exception:
                pass
            if ctx is not None:
                try:
                    ctx.update_gaze_target(target, now=t0)
                except Exception:
                    pass

        # 4. voice ------------------------------------------------------------------
        nlu = None
        if utterance and cfg.voice_enabled:
            nlu = self.nl.feed(utterance, t0)
            command = ""
            if nlu is not None:
                command = nlu.intent.value if nlu.is_command else nlu.fallback_command
            try:
                self.fusion.update_voice(utterance, command or None,
                                         nlu.confidence if nlu else 0.0, t0)
            except Exception:
                pass

        # 5. arbitrate -----------------------------------------------------------------
        tf0 = time.perf_counter()
        decision = self.fusion.update(t0)
        self.telemetry.record("latency_fusion_ms", (time.perf_counter() - tf0) * 1000.0)

        # 6. intents — a v9 NL match suppresses the v8 phrase resolver for
        #    this utterance (blank utterance + decision copy w/o utterance).
        nl_matched = nlu is not None and nlu.is_command
        engine_utterance = "" if nl_matched else str(utterance or "")
        engine_decision = replace(decision, utterance="") if nl_matched else decision
        intents: List[Intent] = list(
            self.intent_engine.process(engine_decision, engine_utterance,
                                       now=t0) or [])
        if nlu is not None and nlu.is_command and nlu.intent is not IntentType.NONE:
            synth = nlu_to_intent(nlu, decision, utterance, now=t0)
            if synth is not None and not any(i.type is synth.type for i in intents):
                try:
                    self.intent_engine.mark_sensitive(synth, text=utterance)
                except Exception:
                    pass
                intents.append(synth)
        # v10: externally injected intents (offline voice, gesture
        # registry sequences, RF) join the pipeline behind a lock swap.
        try:
            with self._lock:
                injected, self._injected = self._injected, []
            if injected:
                intents.extend(injected)
        except Exception:
            pass

        # 7. gate → execute → verify → recover -----------------------------------------
        reports = self._run_intents(intents, gstate, t0)

        # v10: fold the last successful action into the context engine (§8)
        ctx = self.context_engine
        if ctx is not None:
            try:
                for rep in reports:
                    if getattr(rep, "ok", False):
                        plan = getattr(rep, "plan", None)
                        ctx.record_action(
                            getattr(getattr(plan, "action", None), "value", ""),
                            getattr(plan, "target", None), now=t0)
                        break
            except Exception:
                pass

        return {
            "decision": decision,
            "intents": intents,
            "reports": reports,
            "gaze_state": gstate,
            "screen_model": model,
            "context": getattr(model, "context", AppContext.UNKNOWN)
            if model is not None else AppContext.UNKNOWN,
            "nlu": nlu,
            "mode": getattr(self.fusion.mode, "value", "fusion"),
        }

    # -- intent execution ------------------------------------------------------------

    def _gaze_confident(self, gaze_state: Optional[GazeState]) -> bool:
        """True when the current gaze confidence clears the agent gate."""
        conf = float(getattr(gaze_state, "confidence", 0.0) or 0.0)
        return conf >= self.config.min_gaze_confidence

    def _run_intents(self,
                     intents: List[Intent],
                     gaze_state: Optional[GazeState],
                     t0: float) -> List[ActionReport]:
        """Gate, execute, verify and recover a list of intents (in order)."""
        reports: List[ActionReport] = []
        gaze_ok = self._gaze_confident(gaze_state)
        for intent in intents:
            if intent is None:
                continue
            # command debounce (identical command within command_debounce s)
            if not self._allow(debounce_key(intent), t0):
                continue
            # defence in depth: uncertain gaze never clicks (agent-level gate
            # on top of the safety + intent-engine gaze gates).
            if (not gaze_ok
                    and intent.type in CLICK_CLASS
                    and (getattr(intent, "sources", Modality.NONE) & Modality.GAZE)):
                blocked = ActionReport(timestamp=t0)
                blocked.status = ActionStatus.BLOCKED
                blocked.message = "low_gaze_confidence"
                reports.append(blocked)
                self.telemetry.record("actions_blocked", 1)
                continue

            before = len(reports)
            reports.extend(self._execute_intent(intent, t0))
            if len(reports) == before:      # debounced away — nothing recorded
                continue

            # confirmation retry: a successful CONFIRM re-arms the last
            # confirmation-gated intent so "close this window" → "confirm"
            # executes the original action on the confirming frame.
            last = reports[-1]
            if (intent.type is IntentType.CONFIRM
                    and last.ok and self._last_pending_retry is not None):
                retry = self._last_pending_retry
                self._last_pending_retry = None
                reports.extend(self._execute_intent(retry, t0 + 0.25))
        return reports

    def _allow(self, key: str, now: float) -> bool:
        """Command-debounce gate (mirrors hands-free; shared key space)."""
        if not key:
            return True
        cache = self.__dict__.setdefault("_agent_debounce", {})
        last = cache.get(key)
        if last is not None and (now - last) < self.config.command_debounce:
            return False
        cache[key] = now
        return True

    def _execute_intent(self, intent: Intent, t0: float) -> List[ActionReport]:
        """One intent → [reports]: gate, execute, verify, recover."""
        reports: List[ActionReport] = []
        self.telemetry.record("actions_total", 1)

        if intent.type not in (IntentType.EMERGENCY_STOP, IntentType.CANCEL,
                               IntentType.CONFIRM):
            # first real execute lazily materializes the PynputExecutor
            self._ensure_executor()
        report = run_intent_through_safety(
            intent, self.safety, self.action_engine,
            intent_engine=self.intent_engine, now=t0)
        reports.append(report)
        self._record_report(report)
        if intent.type is IntentType.EMERGENCY_STOP and report.ok:
            self.telemetry.record("estops", 1)

        if (report.status is ActionStatus.BLOCKED
                and report.message == "needs_confirmation"):
            self._last_pending_retry = intent
            return reports

        # verification (screen-model observation around the action point).
        verification = None
        plan = getattr(report, "plan", None)
        if (self.config.verify_actions and report.ok and plan is not None
                and getattr(plan, "expected", None)
                and getattr(plan, "target_point", None) is not None
                and self.screen is not None):
            verification = self.verifier.verify(plan, report,
                                                self._make_observe_fn(t0))
            if getattr(verification, "status", None).value == "failed":
                strategy, new_plan = self.recovery.handle(plan, report, verification)
                reports.extend(self._recover(strategy, new_plan, t0))
                return reports

        # execution failure / timeout → bounded recovery.
        if report.status in (ActionStatus.FAILED, ActionStatus.TIMEOUT) \
                and plan is not None:
            strategy, new_plan = self.recovery.handle(plan, report, None)
            reports.extend(self._recover(strategy, new_plan, t0))
        return reports

    def _recover(self,
                 strategy: Any,
                 new_plan: Optional[Any],
                 t0: float) -> List[ActionReport]:
        """Execute a recovery plan (safety-gated like any other action)."""
        if new_plan is None:
            return []
        self.telemetry.record("recoveries", 1)
        # recovery attempts are spaced by at least one click interval so the
        # click-cooldown gate never eats a legitimate retry (deterministic).
        intent = getattr(new_plan, "intent", None)
        if intent is None:
            return []
        self.telemetry.record("actions_total", 1)
        report = run_intent_through_safety(
            intent, self.safety, self.action_engine,
            intent_engine=self.intent_engine, now=t0 + 0.25)
        self._record_report(report)
        report.recovery = strategy
        return [report]

    def _record_report(self, report: ActionReport) -> None:
        """Fold one report into telemetry counters."""
        status = getattr(report, "status", ActionStatus.FAILED)
        if status is ActionStatus.SUCCESS:
            self.telemetry.record("actions_success", 1)
        elif status is ActionStatus.BLOCKED:
            self.telemetry.record("actions_blocked", 1)
        elif status in (ActionStatus.FAILED, ActionStatus.TIMEOUT):
            self.telemetry.record("actions_failed", 1)
        latency = float(getattr(report, "latency", 0.0) or 0.0)
        if latency > 0.0:
            self.telemetry.record("latency_action_ms", latency * 1000.0)

    def _make_observe_fn(self, t0: float):
        """Build the verification observer over the screen model.

        Forces a fresh screen model (post-action state) and reports target
        presence around the point + the pointer position itself.
        """
        screen = self.screen

        def observe(point):
            try:
                model = screen.update(t0, force=True)
            except Exception:
                return {}
            present = None
            if point is not None:
                try:
                    hit = model.target_at(*point) or model.nearest(*point)
                except Exception:
                    hit = None
                present = hit is not None
            return {"present": present, "pointer": point}

        return observe

    # -- hands-free delegation ---------------------------------------------------------

    def _process_hands_free(self,
                            gaze_state: Optional[GazeState],
                            utterance: str,
                            t0: float) -> Dict[str, Any]:
        """HANDS_FREE mode: the controller runs fusion/intent/action."""
        out = self.hands_free.tick(gaze_state, utterance, now=t0)
        for report in out.get("reports", []):
            self.telemetry.record("actions_total", 1)
            self._record_report(report)
            if (getattr(report, "status", None) is ActionStatus.BLOCKED
                    and getattr(report, "message", "") == "needs_confirmation"):
                intents = out.get("intents", [])
                if intents:
                    self._last_pending_retry = intents[-1]
        try:
            model = self.screen.update(t0) if self.screen is not None else None
        except Exception:
            model = None
        return {
            "decision": out.get("decision"),
            "intents": out.get("intents", []),
            "reports": out.get("reports", []),
            "gaze_state": gaze_state,
            "screen_model": model,
            "context": getattr(model, "context", AppContext.UNKNOWN)
            if model is not None else AppContext.UNKNOWN,
            "nlu": out.get("nlu"),
            "mode": out.get("mode", "hands_free"),
            "estop": bool(out.get("estop")),
        }

    # -- macros -------------------------------------------------------------

    def run_macro_program(self, name_or_path: str,
                          stop_check: Any = None) -> Dict[str, Any]:
        """Load + run a macro program (v2 semantic or legacy v1) — thin
        passthrough to :class:`macros.ProgramRunner`."""
        from . import macros as _macros
        program = _macros.load_program(name_or_path)
        runner = self._program_runner
        if runner is None:
            executor = self._ensure_executor()
            if executor is None:
                return {"steps_total": 0, "steps_executed": 0, "verified": 0,
                        "failed": 0, "aborted": True, "reason": "no_executor",
                        "duration": 0.0}
            runner = _macros.ProgramRunner(
                executor=executor, safety=self.safety,
                screen_provider=self.screen, verifier=self.verifier)
            self._program_runner = runner
        return runner.run(program, stop_check=stop_check)

    def record_legacy_macro(self, event: str, **params: Any) -> None:
        """Record one legacy macro event (v5 recorder passthrough)."""
        if self._macro_recorder is None:
            from .macros import MacroRecorder
            self._macro_recorder = MacroRecorder()
        self._macro_recorder.record(event, **params)

    def start_macro_recording(self, name: str) -> bool:
        """Start the legacy macro recorder."""
        if self._macro_recorder is None:
            from .macros import MacroRecorder
            self._macro_recorder = MacroRecorder()
        return bool(self._macro_recorder.start(name))

    def stop_macro_recording(self) -> str:
        """Stop + save the legacy macro recording; returns the path."""
        if self._macro_recorder is None:
            return ""
        path = self._macro_recorder.save()
        self._macro_recorder.stop()
        return path

    # -- lifecycle ------------------------------------------------------------

    @property
    def mode(self) -> str:
        """Current fusion mode value."""
        return getattr(self.fusion.mode, "value", "fusion")

    def shutdown(self) -> None:
        """Idempotent shutdown: closes the gaze engine's heavy resources."""
        if self._closed:
            return
        self._closed = True
        tracker = getattr(self.gaze, "tracker", None) if self.gaze else None
        close = getattr(tracker, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
