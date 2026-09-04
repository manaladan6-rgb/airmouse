"""Tests for airmouse.intent (v8 Intent Engine) — deterministic + headless."""
from __future__ import annotations

import pytest

from airmouse.interfaces import (
    FusionDecision,
    IntentType,
    Modality,
    ScreenTarget,
    ScreenTargetType,
)
from airmouse.intent import (
    GESTURE_TO_INTENT,
    IntentEngine,
    match_phrase,
    normalize_text,
)


def make_target(x=100.0, y=80.0, w=40.0, h=20.0, text="OK"):
    return ScreenTarget(
        id="t1", type=ScreenTargetType.BUTTON, bbox=(x, y, w, h),
        text=text, confidence=0.9, actionable=True,
    )


def decision(conf=0.9, target=None, point=None, confirmations=None, utterance=""):
    return FusionDecision(
        point=point,
        target=target,
        confidence=conf,
        confirmations=list(confirmations or []),
        utterance=utterance,
    )


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_normalize_text_strips_punctuation():
    assert normalize_text("  Please, CLICK  Now! ") == "please click now"


def test_match_phrase_longest_wins():
    assert match_phrase("please double click now")[0] is IntentType.DOUBLE_CLICK
    assert match_phrase("right click that")[0] is IntentType.RIGHT_CLICK
    assert match_phrase("click")[0] is IntentType.CLICK


def test_match_phrase_scroll_params():
    assert match_phrase("scroll up")[1] == {"amount": 3}
    assert match_phrase("scroll down")[1] == {"amount": -3}
    assert match_phrase("scroll a bit")[1] == {"amount": 3}


def test_match_phrase_zoom_direction():
    assert match_phrase("zoom in") == (IntentType.ZOOM, {"direction": "in"})
    assert match_phrase("zoom out") == (IntentType.ZOOM, {"direction": "out"})


def test_match_phrase_empty_is_none():
    assert match_phrase("") is None
    assert match_phrase("   ") is None
    assert match_phrase("gibberish nothing") is None


def test_phrase_table_complete():
    expected = {
        "click", "double click", "right click", "open", "close", "scroll",
        "scroll up", "scroll down", "zoom in", "zoom out", "go back",
        "forward", "select", "minimize", "maximize", "switch window",
        "copy", "paste", "confirm", "cancel", "stop",
    }
    assert expected.issubset(set(GESTURE_TO_INTENT) | set(
        __import__("airmouse.intent", fromlist=["PHRASE_TO_INTENT"]).PHRASE_TO_INTENT
    ))


# ── hand/voice-confirmed click ────────────────────────────────────────────────

def test_hand_confirmed_click_sources_and_type():
    eng = IntentEngine()
    tgt = make_target()
    out = eng.process(decision(0.8, target=tgt, confirmations=["hand:pinch"]),
                      now=100.0)
    assert len(out) == 1 and out[0].type is IntentType.CLICK
    assert out[0].sources == (Modality.GAZE | Modality.HAND)
    assert out[0].point == tgt.center


def test_voice_confirmed_click_sources():
    eng = IntentEngine()
    out = eng.process(decision(0.8, point=(30, 40), confirmations=["voice:click"]),
                      now=100.0)
    assert out[0].type is IntentType.CLICK
    assert out[0].sources == (Modality.GAZE | Modality.VOICE)


def test_confirmation_without_target_emits_nothing():
    eng = IntentEngine()
    out = eng.process(decision(0.9, point=None, confirmations=["hand:pinch"]),
                      now=100.0)
    assert out == []


# ── voice-driven resolution ───────────────────────────────────────────────────

@pytest.mark.parametrize("utterance,itype", [
    ("click", IntentType.CLICK),
    ("double click", IntentType.DOUBLE_CLICK),
    ("right click", IntentType.RIGHT_CLICK),
    ("open it", IntentType.OPEN),
    ("close it", IntentType.CLOSE),
    ("go back", IntentType.BACK),
    ("forward", IntentType.FORWARD),
    ("select this", IntentType.SELECT),
    ("minimize", IntentType.MINIMIZE),
    ("maximize", IntentType.MAXIMIZE),
    ("switch window", IntentType.SWITCH_WINDOW),
    ("copy", IntentType.COPY),
    ("paste", IntentType.PASTE),
    ("confirm", IntentType.CONFIRM),
    ("cancel", IntentType.CANCEL),
    ("stop", IntentType.CANCEL),
])
def test_voice_phrase_table(utterance, itype):
    eng = IntentEngine()
    out = eng.process(decision(0.0), utterance=utterance, now=100.0)
    assert out and out[0].type is itype


def test_voice_target_from_decision():
    eng = IntentEngine()
    tgt = make_target()
    out = eng.process(decision(0.0, target=tgt), utterance="click", now=1.0)
    assert out[0].target is tgt


def test_voice_targetless_confidence_decay():
    eng = IntentEngine()
    out = eng.process(decision(0.0), utterance="click", now=1.0)
    # voice base 0.8 × 0.6 target-less decay
    assert out[0].confidence == pytest.approx(0.48)


def test_voice_base_confidence_when_fusion_silent():
    eng = IntentEngine()
    out = eng.process(decision(0.0, point=(5, 5)), utterance="click", now=1.0)
    assert out[0].confidence == pytest.approx(0.8)


def test_voice_uses_decision_confidence_when_present():
    eng = IntentEngine()
    out = eng.process(decision(0.7, point=(5, 5)), utterance="click", now=1.0)
    assert out[0].confidence == pytest.approx(0.7)


def test_sensitivity_of_close_and_paste():
    eng = IntentEngine()
    for utt in ("close", "paste"):
        out = eng.process(decision(0.0, point=(1, 1)), utterance=utt, now=1.0)
        assert out[0].requires_confirmation is True


def test_mark_sensitive_type_long_text():
    eng = IntentEngine()
    from airmouse.interfaces import Intent
    short = Intent(type=IntentType.TYPE, params={"text": "hi"})
    long = Intent(type=IntentType.TYPE, params={"text": "x" * 41})
    assert eng.mark_sensitive(short) is False
    assert eng.mark_sensitive(long) is True and long.requires_confirmation


# ── gates + drops ──────────────────────────────────────────────────────────────

def test_low_confidence_intent_dropped():
    eng = IntentEngine()
    out = eng.process(decision(0.2, point=(1, 1), confirmations=["hand:pinch"]),
                      now=1.0)
    assert out == []
    assert eng.dropped_count == 1
    assert eng.stats["dropped_low_confidence"] == 1


def test_gaze_uncertain_click_dropped():
    eng = IntentEngine()
    out = eng.process(decision(0.45, point=(1, 1), confirmations=["hand:pinch"]),
                      now=1.0)  # above min 0.35, below gaze 0.55
    assert out == []
    assert eng.stats["dropped_gaze_uncertain"] == 1


def test_cap_max_intents_per_tick():
    eng = IntentEngine(config={"max_intents_per_tick": 1})
    eng.submit_gesture("pinch", point=(1, 1), confidence=0.9, timestamp=0.9)
    eng.submit_gesture("peace", point=(2, 2), confidence=0.9, timestamp=0.9)
    out = eng.process(decision(0.0), now=1.0)
    assert len(out) == 1 and out[0].type is IntentType.CLICK
    assert eng.stats["dropped_capped"] == 1  # peace stayed queued (not dropped)


def test_cap_blocked_gesture_retried_next_tick():
    eng = IntentEngine(config={"max_intents_per_tick": 1})
    eng.submit_gesture("pinch", point=(1, 1), confidence=0.9, timestamp=0.9)
    eng.submit_gesture("peace", point=(2, 2), confidence=0.9, timestamp=0.9)
    eng.process(decision(0.0), now=1.0)
    out = eng.process(decision(0.0), now=1.1)  # no fresh cap usage
    assert len(out) == 1 and out[0].type is IntentType.RIGHT_CLICK


# ── emergency stop ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("utterance", ["emergency stop", "stop everything",
                                       "please EMERGENCY STOP now"])
def test_emergency_stop_always_emitted(utterance):
    eng = IntentEngine()
    out = eng.process(decision(0.9, point=(3, 3)), utterance=utterance, now=5.0)
    assert len(out) == 1 and out[0].type is IntentType.EMERGENCY_STOP
    assert out[0].confidence == 1.0
    assert eng.pop_intent(now=5.0).type is IntentType.EMERGENCY_STOP


def test_emergency_stop_bypasses_cap():
    eng = IntentEngine(config={"max_intents_per_tick": 1})
    eng.submit_gesture("pinch", point=(1, 1), confidence=0.9, timestamp=4.9)
    out1 = eng.process(decision(0.0), now=5.0)           # cap consumed by pinch
    assert out1[0].type is IntentType.CLICK
    out2 = eng.process(decision(0.0), utterance="emergency stop", now=5.1)
    assert out2[0].type is IntentType.EMERGENCY_STOP     # bypasses cap


def test_emergency_from_decision_utterance():
    eng = IntentEngine()
    out = eng.process(decision(0.0, utterance="stop everything"), now=1.0)
    assert out[0].type is IntentType.EMERGENCY_STOP


# ── gesture-direct ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("gesture,itype", [
    ("pinch", IntentType.CLICK),
    ("peace", IntentType.RIGHT_CLICK),
    ("thumb", IntentType.DOUBLE_CLICK),
])
def test_gesture_direct_mapping(gesture, itype):
    eng = IntentEngine()
    eng.submit_gesture(gesture, point=(11, 22), confidence=0.9, timestamp=0.9)
    out = eng.process(decision(0.0), now=1.0)
    assert out and out[0].type is itype
    assert out[0].point == (11, 22)
    assert out[0].sources == Modality.HAND


def test_gesture_expired_not_consumed():
    eng = IntentEngine(config={"intent_max_age": 0.5})
    eng.submit_gesture("pinch", point=(1, 1), confidence=0.9, timestamp=0.0)
    out = eng.process(decision(0.0), now=10.0)  # gesture way too old
    assert out == []
    assert eng.stats["dropped_expired"] == 1


def test_unknown_gesture_ignored():
    eng = IntentEngine()
    eng.submit_gesture("fist", point=(1, 1), confidence=0.9, timestamp=0.9)
    assert eng.process(decision(0.0), now=1.0) == []


# ── queue lifecycle ────────────────────────────────────────────────────────────

def test_pop_intent_fifo_then_none():
    eng = IntentEngine()
    eng.submit_gesture("pinch", point=(1, 1), confidence=0.9, timestamp=0.9)
    eng.submit_gesture("peace", point=(2, 2), confidence=0.9, timestamp=0.95)
    eng.process(decision(0.0), now=1.0)
    eng.process(decision(0.0), now=1.05)
    first = eng.pop_intent(now=1.1)
    second = eng.pop_intent(now=1.1)
    assert first.point == (1, 1) and second.point == (2, 2)
    assert eng.pop_intent(now=1.1) is None


def test_pop_intent_expires_old_entries():
    eng = IntentEngine(config={"intent_max_age": 1.0})
    eng.submit_gesture("pinch", point=(1, 1), confidence=0.9, timestamp=0.0)
    eng.process(decision(0.0), now=0.5)
    assert eng.pop_intent(now=5.0) is None  # aged out of the queue
    assert eng.dropped_count >= 1


def test_cancel_pending_clears_queue():
    eng = IntentEngine()
    eng.submit_gesture("pinch", point=(1, 1), confidence=0.9, timestamp=0.9)
    eng.process(decision(0.0), now=1.0)
    assert eng.pending_count == 1
    eng.cancel_pending()
    assert eng.pending_count == 0
    assert eng.pop_intent(now=1.0) is None


def test_determinism_same_inputs_same_outputs():
    outs = []
    for _ in range(2):
        eng = IntentEngine()
        eng.submit_gesture("pinch", point=(7, 8), confidence=0.9, timestamp=0.9)
        outs.append(eng.process(decision(0.0), now=1.0))
    a, b = outs
    assert [(i.type, i.point, i.confidence, i.sources) for i in a] == \
           [(i.type, i.point, i.confidence, i.sources) for i in b]


def test_none_decision_is_safe():
    eng = IntentEngine()
    assert eng.process(None, utterance="", now=1.0) == []
