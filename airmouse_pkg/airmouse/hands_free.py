"""
airmouse.hands_free — v9 Hands-Free Mode Controller 🙌🚫🖱️
==========================================================

One deterministic ``tick()`` that runs the FULL v9 interaction pipeline
without a hand: **gaze targets → voice commands → dwell/blink confirm →
intent → safety → action**, plus the long-blink emergency-stop channel.

Wiring contract
---------------
    controller = HandsFreeController(config, fusion=..., screen=...)
    while running:
        out = controller.tick(gaze_state, utterance_text, now)
        # out = {"decision", "intents", "reports", "estop", "mode", "nlu"}

* Every dependency is optional — ``None`` creates a sensible internal
  default, so tests can inject fakes for any subset.
* All clock reads come from the injected ``now`` argument (deterministic).
* The controller NEVER blocks or swallows the physical ESC key — the main
  loop (wiring layer) watches ESC itself.  See :attr:`escape_hint`.
* The e-stop latch is a :class:`SafetySystem` state: it blocks every
  subsequent intent until manually reset (keyboard, or the voice flow
  "confirm reset" → :meth:`SafetySystem.reset`).

Pipeline (per tick, in order)
-----------------------------
0. long-blink e-stop check FIRST — a ``LONG_BLINK`` gaze event at
   confidence ≥ ``min_gaze_confidence`` trips the safety e-stop before any
   action executes this frame (strictly safer than checking last).
1. gaze point + semantic screen target → fusion (GAZE modality).
2. utterance → :class:`NLController` → fusion (VOICE modality).
3. ``fusion.update(now)`` → :class:`FusionDecision`.
4. ``intent_engine.process(decision, utterance)`` (+ v9 NL intents
   synthesized from the NLU result; the v8 phrase resolver is suppressed
   for utterances a v9 pattern already matched, so nothing double-fires).
5. per intent: ``safety.approve_intent`` → allowed →
   ``action_engine.execute_intent`` → report (blocked intents produce a
   ``BLOCKED`` report; confirmation-gated ones arm the safety flow).
6. dwell confirm: ``gaze_state.dwell_fired`` + a decision target → a
   synthesized CLICK at that target, executed THROUGH safety.
7. blink confirm (OFF by default — accidental blinks must never click):
   a ``DOUBLE_BLINK`` at sufficient confidence acts like a click.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

try:  # package-relative (normal import path)
    from .interfaces import (
        ActionReport,
        ActionStatus,
        FusionMode,
        GazeEventKind,
        Intent,
        IntentType,
        Modality,
        ScreenTarget,
        now_ts,
    )
    from .nl_control import NLController, nlu_to_intent
    from .fusion import MultimodalFusion
    from .intent import IntentEngine
    from .actions import ActionEngine
    from .safety import SafetySystem
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (
        ActionReport,
        ActionStatus,
        FusionMode,
        GazeEventKind,
        Intent,
        IntentType,
        Modality,
        ScreenTarget,
        now_ts,
    )
    from airmouse.nl_control import NLController, nlu_to_intent
    from airmouse.fusion import MultimodalFusion
    from airmouse.intent import IntentEngine
    from airmouse.actions import ActionEngine
    from airmouse.safety import SafetySystem

__all__ = [
    "DEFAULT_HANDS_FREE_CONFIG",
    "run_intent_through_safety",
    "debounce_key",
    "HandsFreeController",
]

# ---------------------------------------------------------------------------
# Constants + shared pipeline helpers
# ---------------------------------------------------------------------------

#: Documented configuration defaults (see :class:`HandsFreeController`).
DEFAULT_HANDS_FREE_CONFIG: Dict[str, Any] = {
    "dwell_confirm": True,          # sustained gaze fixation clicks
    "blink_confirm": False,         # blink click OFF by default (safety)
    "long_blink_estop": True,       # long blink 1.2 s+ trips the e-stop
    "escape_note": "esc",           # documented physical escape key
    "min_gaze_confidence": 0.55,    # below this: no dwell/blink/estop action
    "command_debounce": 0.6,        # s — identical commands suppressed
}

#: Upper bound for the params["repeat"] multiplier (anti-runaway).
MAX_REPEAT: int = 4


def run_intent_through_safety(intent: Intent,
                              safety: Any,
                              action_engine: Any,
                              intent_engine: Any = None,
                              now: Optional[float] = None) -> ActionReport:
    """Take ONE intent from gate to report (shared by hands-free + agent).

    Special intent types are resolved without the action engine:

    * ``EMERGENCY_STOP`` → ``safety.trip(...)`` (confidence forced to 1.0 by
      the NL layer; the report is SUCCESS with message ``estop_latched``).
    * ``CANCEL`` → pending intents are cancelled; report CANCELLED.
    * ``CONFIRM`` → ``safety.confirm("voice")``; SUCCESS when something was
      actually confirmed, FAILED ("nothing_to_confirm") otherwise.

    Everything else runs the ordered gate: ``safety.approve_intent`` →
    allowed → ``action_engine.execute_intent``.  A refusal with
    ``requires_confirmation`` arms the safety confirmation flow
    (``request_confirmation``) and returns a BLOCKED report.

    ``params["repeat"]`` (from "scroll down twice") executes the SAME
    intent up to :data:`MAX_REPEAT` times and returns the last report.
    Never raises.
    """
    now = float(now if now is not None else now_ts())
    report = ActionReport(timestamp=now)
    itype = getattr(intent, "type", IntentType.NONE)

    if itype is IntentType.EMERGENCY_STOP:
        try:
            safety.trip(reason=str(getattr(intent, "utterance", "") or "voice_emergency"))
        except Exception:
            pass
        report.status = ActionStatus.SUCCESS
        report.message = "estop_latched"
        return report

    if itype is IntentType.CANCEL:
        if intent_engine is not None:
            try:
                intent_engine.cancel_pending()
            except Exception:
                pass
        report.status = ActionStatus.CANCELLED
        report.message = "cancelled"
        return report

    if itype is IntentType.CONFIRM:
        try:
            ok = bool(safety.confirm(source="voice"))
        except Exception:
            ok = False
        report.status = ActionStatus.SUCCESS if ok else ActionStatus.FAILED
        report.message = "confirmed" if ok else "nothing_to_confirm"
        return report

    try:
        decision = safety.approve_intent(intent, now=now)
    except Exception as exc:  # a broken safety layer blocks, never enables
        report.status = ActionStatus.BLOCKED
        report.message = f"safety_error:{exc}"
        return report

    if decision is None or not getattr(decision, "allowed", False):
        report.status = ActionStatus.BLOCKED
        report.message = getattr(decision, "reason", "") or "blocked"
        if decision is not None and getattr(decision, "requires_confirmation", False):
            try:
                safety.request_confirmation(intent, now=now)
            except Exception:
                pass
            report.message = "needs_confirmation"
        return report

    params = getattr(intent, "params", None)
    repeat = 1
    if isinstance(params, dict):
        try:
            repeat = int(params.get("repeat", 1) or 1)
        except Exception:
            repeat = 1
    repeat = max(1, min(repeat, MAX_REPEAT))

    last: Optional[ActionReport] = None
    for _ in range(repeat):
        try:
            last = action_engine.execute_intent(intent, now=now)
        except Exception as exc:
            last = ActionReport(timestamp=now)
            last.status = ActionStatus.FAILED
            last.message = f"engine_error:{exc}"
        if last is not None and getattr(last, "plan", None) is None:
            last.plan = getattr(action_engine, "_last_plan", None)
    if last is not None:
        return last
    report.status = ActionStatus.FAILED
    report.message = "no_action_engine"
    return report


def debounce_key(intent: Intent) -> str:
    """Stable identity of a command for debouncing (pure).

    Same intent type + same target + same params within
    ``command_debounce`` seconds → suppressed.
    """
    itype = getattr(getattr(intent, "type", None), "value", "none")
    params = getattr(intent, "params", None) or {}
    try:
        ps = ",".join(f"{k}={params[k]!r}" for k in sorted(params))
    except Exception:
        ps = ""
    target = getattr(intent, "target", None)
    if target is not None:
        tgt = str(getattr(target, "id", "") or getattr(target, "text", ""))
    else:
        pt = getattr(intent, "point", None) or getattr(intent, "target_point", None)
        tgt = ""
        if callable(pt):
            try:
                pt = pt()
            except Exception:
                pt = None
        if pt is not None:
            try:
                tgt = "pt:{},{}".format(round(float(pt[0])), round(float(pt[1])))
            except Exception:
                tgt = ""
    return f"{itype}|{tgt}|{ps}"


# ---------------------------------------------------------------------------
# HandsFreeController
# ---------------------------------------------------------------------------


class HandsFreeController:
    """The v9 hands-free interaction loop, one deterministic :meth:`tick`.

    Args:
        config: optional dict overriding :data:`DEFAULT_HANDS_FREE_CONFIG`
            keys (``dwell_confirm``, ``blink_confirm``, ``long_blink_estop``,
            ``escape_note``, ``min_gaze_confidence``, ``command_debounce``)
            plus optional nested dicts ``fusion`` / ``intent`` / ``safety`` /
            ``action`` / ``nl`` forwarded to the internal defaults.
        fusion / intent_engine / action_engine / safety / screen / nl:
            optional pre-built dependencies (dependency injection for
            tests).  ``None`` creates the real v7/v8/v9 defaults.

    Emergency escape contract
    -------------------------
    The physical **ESC** key is monitored by the main loop (wiring layer) —
    hands-free NEVER blocks, grabs or delays it.  Once tripped, the e-stop
    latch blocks every intent until it is manually reset (keyboard or the
    voice flow "confirm reset").  :attr:`escape_hint` returns the
    human-readable hint for the HUD.
    """

    DEFAULT_CONFIG: Dict[str, Any] = dict(DEFAULT_HANDS_FREE_CONFIG)

    def __init__(self,
                 config: Optional[Dict[str, Any]] = None,
                 fusion: Any = None,
                 intent_engine: Any = None,
                 action_engine: Any = None,
                 safety: Any = None,
                 screen: Any = None,
                 nl: Any = None) -> None:
        cfg = dict(self.DEFAULT_CONFIG)
        cfg.update(config or {})
        self.config: Dict[str, Any] = cfg
        self.dwell_confirm: bool = bool(cfg["dwell_confirm"])
        self.blink_confirm: bool = bool(cfg["blink_confirm"])
        self.long_blink_estop: bool = bool(cfg["long_blink_estop"])
        self.escape_note: str = str(cfg["escape_note"])
        self.min_gaze_confidence: float = float(cfg["min_gaze_confidence"])
        self.command_debounce: float = float(cfg["command_debounce"])

        self.nl = nl if nl is not None else NLController(cfg.get("nl"))
        self.fusion = fusion if fusion is not None else MultimodalFusion(
            mode=FusionMode.HANDS_FREE, config=cfg.get("fusion"))
        self.intent_engine = intent_engine if intent_engine is not None \
            else IntentEngine(cfg.get("intent"))
        self.safety = safety if safety is not None else SafetySystem(cfg.get("safety"))
        self.action_engine = action_engine if action_engine is not None \
            else ActionEngine(executor=None, safety=None, config=cfg.get("action"))
        self.screen = screen   # optional ScreenPerceptionEngine-like object

        self._last_fire: Dict[str, float] = {}
        self._last_point: Optional[Tuple[float, float]] = None

    # -- properties -------------------------------------------------------------

    @property
    def escape_hint(self) -> str:
        """Human-readable emergency-escape hint (HUD-ready)."""
        return (
            f"press ESC ('{self.escape_note}') at any time — hands-free never "
            f"blocks the physical escape key; the e-stop latch requires a "
            f"manual reset (keyboard or voice 'confirm reset')"
        )

    @property
    def mode(self) -> str:
        """Current fusion mode value (``"hands_free"``)."""
        try:
            return self.fusion.mode.value
        except Exception:
            return "hands_free"

    # -- helpers -----------------------------------------------------------------

    def _screen_size(self) -> Tuple[int, int]:
        """Best-effort screen size from the injected screen engine."""
        return (int(getattr(self.screen, "screen_w", 1920) or 1920),
                int(getattr(self.screen, "screen_h", 1080) or 1080))

    def _gaze_point(self, gaze_state: Any) -> Optional[Tuple[float, float]]:
        """Screen-pixel gaze point from a GazeState-like object (pure)."""
        if gaze_state is None:
            return None
        if bool(getattr(gaze_state, "screen_valid", False)):
            return (float(getattr(gaze_state, "screen_x", 0.0)),
                    float(getattr(gaze_state, "screen_y", 0.0)))
        x = getattr(gaze_state, "x", None)
        y = getattr(gaze_state, "y", None)
        if x is None or y is None:
            return None
        sw, sh = self._screen_size()
        return (float(x) * sw, float(y) * sh)

    def _allow(self, key: str, now: float) -> bool:
        """Command debounce: True when ``key`` may fire now (records it)."""
        if not key:
            return True
        last = self._last_fire.get(key)
        if last is not None and (now - last) < self.command_debounce:
            return False
        self._last_fire[key] = now
        return True

    def _execute(self,
                 intent: Intent,
                 now: float,
                 reports: List[ActionReport]) -> None:
        """Debounce-gate then run one intent through the safety pipeline."""
        if not self._allow(debounce_key(intent), now):
            return
        reports.append(
            run_intent_through_safety(
                intent, self.safety, self.action_engine,
                intent_engine=self.intent_engine, now=now,
            )
        )

    # -- the tick ------------------------------------------------------------------

    def tick(self,
             gaze_state: Any = None,
             utterance_text: str = "",
             now: Optional[float] = None) -> Dict[str, Any]:
        """Advance the hands-free pipeline ONE deterministic step.

        All inputs are optional; with nothing fresh the tick is a no-op
        that still returns the full result dict (graceful degradation).
        """
        now = float(now if now is not None else now_ts())
        events = list(getattr(gaze_state, "events", None) or [])
        gaze_conf = float(getattr(gaze_state, "confidence", 0.0) or 0.0)
        confident_gaze = gaze_conf >= self.min_gaze_confidence

        # 0. long-blink e-stop — evaluated BEFORE anything executes.
        estop = False
        if (self.long_blink_estop and confident_gaze
                and GazeEventKind.LONG_BLINK in events):
            try:
                self.safety.trip("long_blink")
            except Exception:
                pass
            estop = True

        # 1. gaze → fusion (point + semantic target from the screen model).
        point = self._gaze_point(gaze_state)
        self._last_point = point
        target: Optional[ScreenTarget] = None
        if point is not None and self.screen is not None:
            try:
                target = self.screen.target_at(*point)
            except Exception:
                target = None
        if gaze_state is not None:
            self.fusion.update_gaze(point, target, gaze_conf, now)

        # 2. utterance → NLController → fusion VOICE.
        nlu = self.nl.feed(utterance_text, now) if utterance_text else None
        command = ""
        if nlu is not None:
            command = nlu.intent.value if nlu.is_command else nlu.fallback_command
        if utterance_text:
            self.fusion.update_voice(utterance_text, command or None,
                                     nlu.confidence if nlu else 0.0, now)

        # 3. arbitrate.
        decision = self.fusion.update(now)

        # 4. intents — when a v9 NL pattern matched, the v8 phrase resolver
        #    is suppressed for this utterance (blank utterance + a decision
        #    copy without ``utterance``) so nothing double-resolves.
        nl_matched = nlu is not None and nlu.is_command
        engine_utterance = "" if nl_matched else str(utterance_text or "")
        engine_decision = replace(decision, utterance="") if nl_matched else decision
        intents: List[Intent] = list(
            self.intent_engine.process(engine_decision, engine_utterance,
                                       now=now) or [])
        if nlu is not None and nlu.is_command and nlu.intent is not IntentType.NONE:
            synth = nlu_to_intent(nlu, decision, utterance_text, now=now)
            if synth is not None and not any(i.type is synth.type for i in intents):
                try:
                    self.intent_engine.mark_sensitive(synth, text=utterance_text)
                except Exception:
                    pass
                intents.append(synth)

        # 7 (evaluated pre-execution): blink confirm (OFF by default).
        if (self.blink_confirm and confident_gaze and decision.has_target
                and GazeEventKind.DOUBLE_BLINK in events
                and not any(i.type is IntentType.CLICK for i in intents)):
            blink_intent = Intent(
                type=IntentType.CLICK,
                target=decision.target,
                point=decision.target_point(),
                confidence=max(decision.confidence, gaze_conf),
                sources=Modality.GAZE,
                utterance="double_blink",
                timestamp=now,
            )
            intents.append(blink_intent)

        # 5. gate + execute every intent.
        reports: List[ActionReport] = []
        for intent in intents:
            self._execute(intent, now, reports)

        # 6. dwell confirm — sustained fixation + a locked target → CLICK,
        #    always routed THROUGH the safety gate.
        if (self.dwell_confirm and confident_gaze
                and bool(getattr(gaze_state, "dwell_fired", False))
                and decision.has_target):
            dwell_intent = Intent(
                type=IntentType.CLICK,
                target=decision.target,
                point=decision.target_point(),
                confidence=max(decision.confidence, gaze_conf),
                sources=Modality.GAZE,
                utterance="dwell",
                timestamp=now,
            )
            self._execute(dwell_intent, now, reports)

        return {
            "decision": decision,
            "intents": intents,
            "reports": reports,
            "estop": estop or self._safety_latched(),
            "mode": self.mode,
            "nlu": nlu,
        }

    def _safety_latched(self) -> bool:
        """True when the safety system is in the EMERGENCY (e-stop) level."""
        level = getattr(self.safety, "level", None)
        return getattr(level, "name", "") == "EMERGENCY"

    # -- lifecycle -----------------------------------------------------------------

    def reset(self) -> None:
        """Clear dedup/debounce state (does NOT reset the safety latch)."""
        self._last_fire.clear()
        self.nl.reset()
