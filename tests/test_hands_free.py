"""Tests for airmouse.hands_free (v9 hands-free mode controller).

Deterministic + headless: real NL/fusion/intent engines, injected
timestamps, minimal fakes for safety/action/screen/fusion where the spec
calls for dependency injection.
"""
from __future__ import annotations

from airmouse.interfaces import (
    ActionReport,
    ActionStatus,
    FusionDecision,
    FusionMode,
    GazeEventKind,
    GazeState,
    Intent,
    IntentType,
    Modality,
    SafetyDecision,
    SafetyLevel,
    ScreenTarget,
    ScreenTargetType,
)
from airmouse.hands_free import (
    HandsFreeController,
    debounce_key,
    run_intent_through_safety,
)

BUTTON = ScreenTarget(id="btn", type=ScreenTargetType.BUTTON,
                      bbox=(900, 500, 120, 50), text="Submit button",
                      confidence=0.9, actionable=True)


# ---------------------------------------------------------------------------
# Fakes (minimal contract implementations)
# ---------------------------------------------------------------------------

class FakeSafety:
    """Approves everything until tripped; records calls."""

    def __init__(self):
        self.tripped = []
        self.latched = False
        self.confirmed = 0
        self.requests = []

    @property
    def level(self):
        return SafetyLevel.EMERGENCY if self.latched else SafetyLevel.NORMAL

    def approve_intent(self, intent, now=None):
        if self.latched:
            return SafetyDecision(allowed=False, reason="emergency_stop",
                                  level=SafetyLevel.EMERGENCY)
        return SafetyDecision(allowed=True, reason="ok")

    def trip(self, reason=""):
        self.tripped.append(reason)
        self.latched = True

    def reset(self):
        self.latched = False

    def request_confirmation(self, intent, now=None):
        self.requests.append(intent)
        return True

    def confirm(self, source="voice"):
        self.confirmed += 1
        return True


class FakeActionEngine:
    """Returns SUCCESS reports; records executed intents."""

    def __init__(self):
        self.executed = []

    def execute_intent(self, intent, now=None):
        self.executed.append(intent)
        return ActionReport(status=ActionStatus.SUCCESS, message="fake",
                            latency=0.001, attempts=1)


class FakeIntentEngine:
    """Replays a pre-queued intent list on each process()."""

    def __init__(self, queue=None):
        self.queue = list(queue or [])
        self.cancelled = 0
        self.marked = []

    def process(self, decision, utterance="", now=None):
        out, self.queue = self.queue, []
        return out

    def submit_gesture(self, *a, **k):
        pass

    def cancel_pending(self):
        self.cancelled += 1

    def mark_sensitive(self, intent, text=""):
        self.marked.append(intent)
        return True


class FakeFusion:
    """Returns a canned decision; records feeder calls."""

    def __init__(self, decision=None):
        self._decision = decision or FusionDecision()
        self.gaze_calls = []
        self.voice_calls = []
        self.mode = FusionMode.HANDS_FREE

    def update_gaze(self, point, target, confidence, timestamp=None):
        self.gaze_calls.append((point, target, confidence))

    def update_voice(self, text, command=None, confidence=0.0, timestamp=None):
        self.voice_calls.append((text, command, confidence))

    def update(self, now=None):
        return self._decision


class FakeScreen:
    screen_w = 1920
    screen_h = 1080
    last_query = None

    def target_at(self, px, py):
        type(self).last_query = (px, py)
        return BUTTON if (900 <= px <= 1020 and 500 <= py <= 550) else None


def gaze_state(conf=0.9, dwell=False, events=None, screen_valid=False):
    return GazeState(x=0.5, y=0.5, screen_x=960, screen_y=525,
                     screen_valid=screen_valid, confidence=conf,
                     dwell_fired=dwell, events=list(events or []))


def make_controller(**kwargs):
    """Real NL + fusion + intent engine; fake safety + action engine."""
    safety = kwargs.pop("safety", None) or FakeSafety()
    action = kwargs.pop("action_engine", None) or FakeActionEngine()
    screen = kwargs.pop("screen", None)
    if screen is None:
        screen = FakeScreen()
    cfg = {"min_gaze_confidence": 0.55}
    cfg.update(kwargs.pop("config", {}) or {})
    hf = HandsFreeController(config=cfg, safety=safety, action_engine=action,
                             screen=screen, **kwargs)
    return hf, safety, action


# ---------------------------------------------------------------------------
# Graceful degradation / result shape
# ---------------------------------------------------------------------------

def test_tick_with_nothing_returns_full_dict():
    hf, _, _ = make_controller()
    out = hf.tick(None, "", now=1.0)
    assert set(out) >= {"decision", "intents", "reports", "estop", "mode"}
    assert out["mode"] == "hands_free"
    assert out["intents"] == [] and out["reports"] == []
    assert out["estop"] is False


def test_tick_all_default_deps_constructed():
    hf = HandsFreeController()
    out = hf.tick(None, "", now=0.0)
    assert isinstance(out["decision"], FusionDecision)


def test_escape_hint_mentions_esc_and_contract():
    hf = HandsFreeController()
    hint = hf.escape_hint
    assert "esc" in hint.lower()
    assert "never blocks" in hint


def test_gaze_point_from_screen_valid_state():
    hf, _, _ = make_controller()
    hf.tick(gaze_state(screen_valid=True), "", now=1.0)
    assert FakeScreen.last_query == (960, 525)


def test_gaze_normalized_scaled_to_screen():
    hf, _, _ = make_controller()
    gs = GazeState(x=0.5, y=0.5, confidence=0.9, screen_valid=False)
    hf.tick(gs, "", now=1.0)
    assert FakeScreen.last_query == (960.0, 540.0)


def test_voice_fed_into_fusion_with_command():
    fusion = FakeFusion()
    hf = HandsFreeController(config={}, fusion=fusion,
                             intent_engine=FakeIntentEngine(),
                             safety=FakeSafety(), action_engine=FakeActionEngine())
    hf.tick(None, "click that", now=1.0)
    assert fusion.voice_calls and fusion.voice_calls[0][1] == "click"


# ---------------------------------------------------------------------------
# Dwell confirm
# ---------------------------------------------------------------------------

def test_dwell_confirm_clicks_through_safety():
    hf, safety, action = make_controller()
    out = hf.tick(gaze_state(dwell=True), "", now=1.0)
    assert len(out["reports"]) == 1
    assert out["reports"][0].status is ActionStatus.SUCCESS
    assert action.executed[0].type is IntentType.CLICK
    assert action.executed[0].target is BUTTON


def test_dwell_confirm_disabled():
    hf, _, action = make_controller(config={"dwell_confirm": False})
    out = hf.tick(gaze_state(dwell=True), "", now=1.0)
    assert out["reports"] == [] and action.executed == []


def test_dwell_low_confidence_no_click():
    hf, _, action = make_controller()
    out = hf.tick(gaze_state(conf=0.4, dwell=True), "", now=1.0)
    assert out["reports"] == [] and action.executed == []


def test_dwell_without_semantic_target_clicks_at_point():
    # decision.has_target covers raw points too: with no semantic target the
    # dwell click fires at the raw gaze point (gaze supplies the coordinate;
    # nothing is invented by the NL/screen layers).
    class NoScreen(FakeScreen):
        def target_at(self, px, py):
            return None
    hf, _, action = make_controller(screen=NoScreen())
    hf.tick(gaze_state(dwell=True), "", now=1.0)
    assert action.executed and action.executed[0].type is IntentType.CLICK
    assert action.executed[0].target is None
    assert action.executed[0].point == (960.0, 540.0)


# ---------------------------------------------------------------------------
# Blink click (OFF by default) + long-blink e-stop
# ---------------------------------------------------------------------------

def test_blink_click_disabled_by_default():
    hf, _, action = make_controller()
    hf.tick(gaze_state(events=[GazeEventKind.BLINK,
                               GazeEventKind.DOUBLE_BLINK]), "", now=1.0)
    assert action.executed == []


def test_blink_click_opt_in():
    hf, _, action = make_controller(config={"blink_confirm": True})
    hf.tick(gaze_state(events=[GazeEventKind.DOUBLE_BLINK]), "", now=1.0)
    assert action.executed and action.executed[0].type is IntentType.CLICK


def test_long_blink_trips_estop():
    hf, safety, _ = make_controller()
    out = hf.tick(gaze_state(events=[GazeEventKind.LONG_BLINK]), "", now=1.0)
    assert safety.tripped == ["long_blink"]
    assert out["estop"] is True


def test_long_blink_low_confidence_does_not_trip():
    hf, safety, _ = make_controller()
    out = hf.tick(gaze_state(conf=0.3, events=[GazeEventKind.LONG_BLINK]),
                  "", now=1.0)
    assert safety.tripped == [] and out["estop"] is False


def test_long_blink_estop_disabled():
    hf, safety, _ = make_controller(config={"long_blink_estop": False})
    out = hf.tick(gaze_state(events=[GazeEventKind.LONG_BLINK]), "", now=1.0)
    assert safety.tripped == [] and out["estop"] is False  # noqa: F841


# ---------------------------------------------------------------------------
# Voice → intent pipeline (real NL + real fusion + real intent engine)
# ---------------------------------------------------------------------------

def test_click_that_with_gaze_target_executes():
    hf, _, action = make_controller()
    out = hf.tick(gaze_state(), "click that", now=1.0)
    assert any(i.type is IntentType.CLICK for i in out["intents"])
    assert out["reports"] and out["reports"][0].status is ActionStatus.SUCCESS
    assert action.executed[0].type is IntentType.CLICK


def test_scroll_magnitude_reaches_action():
    hf, _, action = make_controller()
    hf.tick(None, "scroll down a lot", now=1.0)
    assert action.executed[0].type is IntentType.SCROLL
    assert action.executed[0].params["amount"] == -8


def test_stop_everything_trips_safety():
    hf, safety, _ = make_controller()
    out = hf.tick(None, "stop everything", now=1.0)
    assert safety.latched is True
    assert out["estop"] is True
    assert out["reports"][0].status is ActionStatus.SUCCESS


def test_cancel_cancels_pending():
    engine = FakeIntentEngine()
    hf = HandsFreeController(config={}, fusion=FakeFusion(),
                             intent_engine=engine, safety=FakeSafety(),
                             action_engine=FakeActionEngine())
    out = hf.tick(None, "cancel", now=1.0)
    assert engine.cancelled == 1
    assert out["reports"][0].status is ActionStatus.CANCELLED


def test_estop_blocks_subsequent_intents():
    hf, safety, action = make_controller()
    hf.tick(gaze_state(), "click that", now=1.0)
    safety.latched = True
    out = hf.tick(gaze_state(), "click it", now=5.0)
    assert out["reports"]
    assert all(r.status is ActionStatus.BLOCKED for r in out["reports"])
    assert len(action.executed) == 1  # only the pre-estop click ran


def test_nl_dedup_suppresses_repeat_command():
    hf, _, action = make_controller()
    hf.tick(gaze_state(), "click that", now=1.0)
    out2 = hf.tick(gaze_state(), "click that", now=1.5)   # inside 1.2 s window
    assert out2["nlu"] is None
    assert len(action.executed) == 1


def test_command_debounce_suppresses_same_command():
    hf, _, action = make_controller()
    hf.tick(gaze_state(), "click that", now=1.0)
    hf.tick(gaze_state(), "click it", now=1.3)   # different text → parses,
    # but identical (type,target) command within 0.6 s → debounced
    assert len(action.executed) == 1


def test_command_debounce_window_allows_refire():
    hf, _, action = make_controller()
    hf.tick(gaze_state(), "click that", now=1.0)
    hf.tick(gaze_state(), "click it", now=2.0)   # 1.0 s later → allowed
    assert len(action.executed) == 2


# ---------------------------------------------------------------------------
# Full dependency injection
# ---------------------------------------------------------------------------

def test_injected_fake_fusion_and_intent_engine():
    intent = Intent(type=IntentType.SELECT, confidence=0.9,
                    sources=Modality.VOICE, timestamp=1.0)
    fusion = FakeFusion(FusionDecision(target=BUTTON, confidence=0.9))
    engine = FakeIntentEngine(queue=[intent])
    safety, action = FakeSafety(), FakeActionEngine()
    hf = HandsFreeController(config={}, fusion=fusion, intent_engine=engine,
                             safety=safety, action_engine=action)
    out = hf.tick(None, "", now=1.0)
    assert out["intents"] == [intent]
    assert out["reports"][0].status is ActionStatus.SUCCESS
    assert action.executed == [intent]


def test_injected_decision_without_target_no_dwell_click():
    fusion = FakeFusion(FusionDecision(confidence=0.9))   # no target/point
    hf = HandsFreeController(config={}, fusion=fusion,
                             intent_engine=FakeIntentEngine(),
                             safety=FakeSafety(), action_engine=FakeActionEngine())
    out = hf.tick(gaze_state(dwell=True), "", now=1.0)
    assert out["reports"] == []


# ---------------------------------------------------------------------------
# run_intent_through_safety units
# ---------------------------------------------------------------------------

def test_run_intent_repeat_param_executes_twice():
    action = FakeActionEngine()
    intent = Intent(type=IntentType.SCROLL, confidence=0.9,
                    params={"amount": 3, "repeat": 2}, timestamp=0.0)
    report = run_intent_through_safety(intent, FakeSafety(), action, now=1.0)
    assert len(action.executed) == 2
    assert report.status is ActionStatus.SUCCESS


def test_run_intent_repeat_capped():
    action = FakeActionEngine()
    intent = Intent(type=IntentType.SCROLL, confidence=0.9,
                    params={"amount": 3, "repeat": 99}, timestamp=0.0)
    run_intent_through_safety(intent, FakeSafety(), action, now=1.0)
    assert len(action.executed) <= 4


def test_run_intent_confirm_with_nothing_pending():
    class NoConfirmSafety(FakeSafety):
        def confirm(self, source="voice"):
            return False
    intent = Intent(type=IntentType.CONFIRM, confidence=1.0, timestamp=0.0)
    report = run_intent_through_safety(intent, NoConfirmSafety(),
                                       FakeActionEngine(), now=1.0)
    assert report.status is ActionStatus.FAILED
    assert report.message == "nothing_to_confirm"


def test_run_intent_blocked_arms_confirmation():
    class ConfirmSafety(FakeSafety):
        def approve_intent(self, intent, now=None):
            return SafetyDecision(allowed=False, reason="needs_confirmation",
                                  requires_confirmation=True)
    safety = ConfirmSafety()
    intent = Intent(type=IntentType.CLOSE, confidence=0.9, timestamp=0.0)
    report = run_intent_through_safety(intent, safety, FakeActionEngine(),
                                       now=1.0)
    assert report.status is ActionStatus.BLOCKED
    assert report.message == "needs_confirmation"
    assert safety.requests == [intent]


def test_run_intent_broken_safety_blocks_not_enables():
    class BoomSafety:
        def approve_intent(self, intent, now=None):
            raise RuntimeError("boom")
    intent = Intent(type=IntentType.CLICK, confidence=0.9, timestamp=0.0)
    report = run_intent_through_safety(intent, BoomSafety(), FakeActionEngine(),
                                       now=1.0)
    assert report.status is ActionStatus.BLOCKED
    assert "safety_error" in report.message


# ---------------------------------------------------------------------------
# debounce_key
# ---------------------------------------------------------------------------

def test_debounce_key_stable_for_same_intent():
    a = Intent(type=IntentType.CLICK, point=(960, 525), confidence=0.9)
    b = Intent(type=IntentType.CLICK, point=(960, 525), confidence=0.5)
    assert debounce_key(a) == debounce_key(b)


def test_debounce_key_differs_for_different_params():
    a = Intent(type=IntentType.SCROLL, params={"amount": 3})
    b = Intent(type=IntentType.SCROLL, params={"amount": 8})
    assert debounce_key(a) != debounce_key(b)
