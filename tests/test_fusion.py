"""Tests for airmouse.fusion — MultimodalFusion v7 arbitration.

Deterministic: every feeder call and update() receives explicit timestamps,
so no test depends on wall-clock timing.  Headless: no camera, mic, or
display is required.
"""
from __future__ import annotations

import threading

import pytest

from airmouse.interfaces import (
    FusionEvent,
    FusionMode,
    Modality,
    ScreenTarget,
    ScreenTargetType,
    now_ts,
)
from airmouse.fusion import (
    CLICK_CLASS_COMMANDS,
    CONFIRM_BOOST,
    DEFAULT_CONFIG,
    PRIORITY_WEIGHTS,
    MultimodalFusion,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_target(tid="btn:submit", bbox=(460.0, 380.0, 80.0, 40.0),
                text="Submit", conf=0.9, actionable=True,
                ttype=ScreenTargetType.BUTTON) -> ScreenTarget:
    """Deterministic semantic target for tests."""
    return ScreenTarget(id=tid, type=ttype, bbox=tuple(bbox), text=text,
                        confidence=conf, application="TestApp",
                        actionable=actionable, source="accessibility",
                        timestamp=0.0)


# ---------------------------------------------------------------------------
# Priority matrix + config documentation
# ---------------------------------------------------------------------------

class TestPriorityMatrix:
    def test_documented_weights(self):
        assert PRIORITY_WEIGHTS[FusionMode.HAND][Modality.HAND] == 1.0
        assert PRIORITY_WEIGHTS[FusionMode.HAND][Modality.GAZE] == 0.4
        assert PRIORITY_WEIGHTS[FusionMode.GAZE][Modality.GAZE] == 1.0
        assert PRIORITY_WEIGHTS[FusionMode.GAZE][Modality.HAND] == 0.3
        assert PRIORITY_WEIGHTS[FusionMode.FUSION][Modality.GAZE] == 1.0
        assert PRIORITY_WEIGHTS[FusionMode.FUSION][Modality.HAND] == 0.85
        assert PRIORITY_WEIGHTS[FusionMode.FUSION][Modality.VOICE] == 0.95
        assert PRIORITY_WEIGHTS[FusionMode.VOICE][Modality.VOICE] == 1.0
        assert PRIORITY_WEIGHTS[FusionMode.HANDS_FREE][Modality.GAZE] == 1.0
        assert PRIORITY_WEIGHTS[FusionMode.HANDS_FREE][Modality.VOICE] == 1.0
        assert PRIORITY_WEIGHTS[FusionMode.HANDS_FREE][Modality.HAND] == 0.0
        for mode in FusionMode:
            assert mode in PRIORITY_WEIGHTS
            assert len(PRIORITY_WEIGHTS[mode]) >= 5

    def test_documented_config_defaults(self):
        fusion = MultimodalFusion()
        assert fusion.config["stale_after"] == 0.8
        assert fusion.config["confirm_window"] == 0.9
        assert fusion.config["confirm_radius_px"] == 140
        assert fusion.config["mode_switch_min_interval"] == 0.35
        assert fusion.config["min_combined_confidence"] == 0.35
        assert fusion.config["voice_intent_window"] == 1.5
        assert fusion.mode is FusionMode.FUSION

    def test_config_override_and_constants(self):
        fusion = MultimodalFusion(config={"stale_after": 5.0, "bogus": 1})
        assert fusion.config["stale_after"] == 5.0
        assert DEFAULT_CONFIG["stale_after"] == 0.8  # defaults untouched
        assert "click" in CLICK_CLASS_COMMANDS
        assert CONFIRM_BOOST == 0.25


# ---------------------------------------------------------------------------
# Winner selection per mode
# ---------------------------------------------------------------------------

class TestWinnerSelection:
    def test_gaze_only_gaze_wins_with_semantic_target(self):
        fusion = MultimodalFusion()
        target = make_target()
        fusion.update_gaze((500.0, 400.0), target, 0.9, timestamp=1.0)
        decision = fusion.update(now=1.05)
        assert decision.mode is FusionMode.FUSION
        assert decision.target is target
        assert decision.point is None                 # semantic target preferred
        assert decision.target_point() == (500.0, 400.0)
        assert decision.confidence == pytest.approx(0.9)
        assert bool(decision.contributing & Modality.GAZE)
        assert decision.confirmations == []
        assert decision.has_target

    def test_hand_only_in_hand_mode_hand_wins(self):
        fusion = MultimodalFusion(mode=FusionMode.HAND)
        fusion.update_hand((800.0, 300.0), "point", 0.85, timestamp=1.0)
        decision = fusion.update(now=1.05)
        assert decision.point == (800.0, 300.0)
        assert decision.target is None
        assert decision.confidence == pytest.approx(0.85)
        assert bool(decision.contributing & Modality.HAND)
        assert not bool(decision.contributing & Modality.GAZE)

    def test_point_only_gaze_yields_raw_pixel_point(self):
        fusion = MultimodalFusion()
        fusion.update_gaze((123.0, 45.0), None, 0.9, timestamp=1.0)
        decision = fusion.update(now=1.05)
        assert decision.target is None
        assert decision.point == (123.0, 45.0)
        assert decision.target_point() == (123.0, 45.0)

    def test_semantic_target_preferred_over_raw_point(self):
        fusion = MultimodalFusion()
        target = make_target(bbox=(100.0, 100.0, 80.0, 40.0))
        fusion.update_gaze((110.0, 110.0), target, 0.9, timestamp=1.0)
        decision = fusion.update(now=1.05)
        assert decision.target is target
        assert decision.point is None
        assert decision.target_point() == (140.0, 120.0)  # target center

    def test_stale_gaze_treated_absent(self):
        fusion = MultimodalFusion()
        fusion.update_gaze((500.0, 400.0), make_target(), 0.9, timestamp=1.0)
        decision = fusion.update(now=1.0 + 0.8 + 0.05)  # > stale_after
        assert decision.target is None
        assert decision.point is None
        assert decision.confidence == 0.0
        assert not decision.has_target

    def test_stale_after_override_extends_freshness(self):
        fusion = MultimodalFusion(config={"stale_after": 5.0})
        fusion.update_gaze((10.0, 20.0), None, 0.9, timestamp=1.0)
        decision = fusion.update(now=3.0)  # stale for default, fresh at 5.0
        assert decision.point == (10.0, 20.0)


# ---------------------------------------------------------------------------
# Confirmation patterns
# ---------------------------------------------------------------------------

class TestConfirmations:
    def test_fusion_gaze_plus_nearby_pinch_confirms(self):
        fusion = MultimodalFusion()  # FUSION mode
        fusion.update_gaze((400.0, 300.0), None, 0.5, timestamp=1.0)
        fusion.update_hand((420.0, 310.0), "pinch", 0.3, timestamp=1.1)
        decision = fusion.update(now=1.15)
        assert decision.confirmations == ["hand:pinch"]
        # combined = max(0.5, 0.3) + 0.25 boost
        assert decision.confidence == pytest.approx(0.75)
        assert decision.point == (400.0, 300.0)  # gaze leads in FUSION

    def test_fusion_pinch_boost_caps_at_one(self):
        fusion = MultimodalFusion()
        fusion.update_gaze((400.0, 300.0), None, 0.9, timestamp=1.0)
        fusion.update_hand((410.0, 305.0), "pinch", 0.7, timestamp=1.1)
        decision = fusion.update(now=1.15)
        assert decision.confirmations == ["hand:pinch"]
        assert decision.confidence == pytest.approx(1.0)  # 0.9 + 0.25 capped

    def test_voice_click_within_window_confirms(self):
        fusion = MultimodalFusion()
        fusion.update_gaze((400.0, 300.0), None, 0.9, timestamp=1.0)
        fusion.update_voice("click please", "click", 0.95, timestamp=1.2)
        decision = fusion.update(now=1.3)
        assert "voice:click" in decision.confirmations
        assert decision.confidence == pytest.approx(1.0)
        assert decision.utterance == "click please"
        assert decision.point == (400.0, 300.0)

    def test_voice_non_click_command_does_not_confirm(self):
        fusion = MultimodalFusion()
        fusion.update_gaze((400.0, 300.0), None, 0.9, timestamp=1.0)
        fusion.update_voice("scroll down", "scroll_down", 0.95, timestamp=1.2)
        decision = fusion.update(now=1.3)
        assert decision.confirmations == []
        assert decision.utterance == "scroll down"

    def test_pinch_outside_radius_does_not_confirm(self):
        fusion = MultimodalFusion()
        fusion.update_gaze((100.0, 100.0), None, 0.9, timestamp=1.0)
        fusion.update_hand((600.0, 600.0), "pinch", 0.9, timestamp=1.1)
        decision = fusion.update(now=1.15)
        assert decision.confirmations == []
        assert decision.point == (100.0, 100.0)

    def test_pinch_outside_confirm_window_does_not_confirm(self):
        fusion = MultimodalFusion()
        fusion.update_hand((410.0, 305.0), "pinch", 0.9, timestamp=1.0)
        fusion.update_gaze((400.0, 300.0), None, 0.9, timestamp=1.9)  # fresh
        decision = fusion.update(now=1.95)  # pinch age 0.95 > confirm_window
        assert decision.confirmations == []
        assert decision.confidence == pytest.approx(0.9)

    def test_duplicate_pinch_events_dedupe_to_one_label(self):
        fusion = MultimodalFusion()
        fusion.update_gaze((400.0, 300.0), None, 0.9, timestamp=1.0)
        fusion.update_hand((405.0, 302.0), "pinch", 0.9, timestamp=1.05)
        fusion.update_hand((410.0, 305.0), "pinch", 0.9, timestamp=1.10)
        decision = fusion.update(now=1.15)
        assert decision.confirmations.count("hand:pinch") == 1
        assert decision.confidence == pytest.approx(1.0)  # single boost only


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------

class TestConflicts:
    def test_conflicting_modalities_higher_score_wins(self):
        fusion = MultimodalFusion(mode=FusionMode.HAND)
        fusion.update_hand((900.0, 500.0), "point", 0.9, timestamp=1.0)  # 0.90
        fusion.update_gaze((70.0, 70.0),
                           make_target(tid="t:far", bbox=(50.0, 50.0, 40.0, 40.0),
                                       text="Far", ttype=ScreenTargetType.UNKNOWN),
                           0.8, timestamp=1.0)                            # 0.32
        decision = fusion.update(now=1.05)
        assert decision.point == (900.0, 500.0)     # hand wins on score
        conflict = fusion.last_conflict
        assert conflict is not None
        assert conflict["winner"] == "hand"
        assert "gaze" in conflict["scores"]
        assert conflict["losers"][0]["modality"] == "gaze"

    def test_agreeing_modalities_do_not_conflict(self):
        fusion = MultimodalFusion(mode=FusionMode.HAND)
        fusion.update_hand((900.0, 500.0), "point", 0.9, timestamp=1.0)
        fusion.update_gaze((880.0, 490.0), None, 0.9, timestamp=1.0)  # close
        fusion.update(now=1.05)
        assert fusion.last_conflict is None

    def test_clear_conflict_after_reset(self):
        fusion = MultimodalFusion(mode=FusionMode.HAND)
        fusion.update_hand((900.0, 500.0), "point", 0.9, timestamp=1.0)
        fusion.update_gaze((70.0, 70.0), None, 0.9, timestamp=1.0)
        fusion.update(now=1.05)
        assert fusion.last_conflict is not None
        fusion.reset()
        assert fusion.last_conflict is None


# ---------------------------------------------------------------------------
# Mode system
# ---------------------------------------------------------------------------

class TestModeSystem:
    def test_set_mode_rate_limit(self):
        fusion = MultimodalFusion()
        assert fusion.set_mode(FusionMode.GAZE, now=10.0) is True
        assert fusion.set_mode(FusionMode.VOICE, now=10.1) is False  # too soon
        assert fusion.mode is FusionMode.GAZE
        assert fusion.set_mode("voice", now=10.36) is True           # 0.36 s later
        assert fusion.mode is FusionMode.VOICE

    def test_set_mode_accepts_strings_and_rejects_unknown(self):
        fusion = MultimodalFusion()
        assert fusion.set_mode("gaze", now=1.0) is True
        assert fusion.mode is FusionMode.GAZE
        assert fusion.set_mode("HANDS_FREE", now=2.0) is True
        assert fusion.mode is FusionMode.HANDS_FREE
        assert fusion.set_mode("hands-free", now=3.0) is True
        assert fusion.mode is FusionMode.HANDS_FREE
        assert fusion.set_mode("bogus_mode") is False
        assert fusion.mode is FusionMode.HANDS_FREE  # unchanged
        assert fusion.set_mode(None) is False

    def test_same_mode_is_successful_noop(self):
        fusion = MultimodalFusion()
        assert fusion.set_mode("gaze", now=1.0) is True
        assert fusion.set_mode("gaze", now=1.01) is True  # no rate-limit trip
        assert fusion.mode is FusionMode.GAZE

    def test_assist_mode_no_auto_confirmation(self):
        fusion = MultimodalFusion(mode=FusionMode.ASSIST)
        fusion.update_gaze((300.0, 300.0), None, 0.8, timestamp=1.0)
        fusion.update_hand((310.0, 310.0), "pinch", 0.9, timestamp=1.1)
        decision = fusion.update(now=1.15)
        assert decision is not None
        assert decision.mode is FusionMode.ASSIST
        assert decision.confirmations == []          # no auto-confirmation
        assert decision.confidence == pytest.approx(0.9)  # raw observation only
        assert decision.has_target                   # observation is reported

    def test_hands_free_ignores_hand_entirely(self):
        fusion = MultimodalFusion(mode=FusionMode.HANDS_FREE)
        fusion.update_hand((500.0, 500.0), "pinch", 0.95, timestamp=1.0)
        fusion.update_gaze((200.0, 200.0), None, 0.9, timestamp=1.0)
        decision = fusion.update(now=1.05)
        assert decision.confirmations == []          # hand cannot confirm
        assert decision.point == (200.0, 200.0)      # gaze wins unopposed
        assert not bool(decision.contributing & Modality.HAND)
        assert decision.confidence == pytest.approx(0.9)

    def test_voice_mode_locks_last_stable_target(self):
        fusion = MultimodalFusion(mode=FusionMode.VOICE)
        target = make_target(tid="w:main", bbox=(280.0, 280.0, 80.0, 40.0),
                             text="Main", ttype=ScreenTargetType.UNKNOWN)
        fusion.update_gaze((300.0, 300.0), target, 0.9, timestamp=1.0)
        fusion.update_voice("click it", "click", 0.95, timestamp=1.2)
        decision = fusion.update(now=1.25)
        assert decision.target is target
        assert "voice:click" in decision.confirmations
        assert decision.utterance == "click it"

    def test_voice_mode_without_command_falls_back_to_arbitration(self):
        fusion = MultimodalFusion(mode=FusionMode.VOICE)
        fusion.update_voice("hello there", None, 0.9, timestamp=1.0)
        fusion.update_mouse((700.0, 700.0), timestamp=1.0)
        decision = fusion.update(now=1.05)
        assert decision.utterance == "hello there"
        assert decision.target is None  # mouse score 0.3 < min gate 0.35


# ---------------------------------------------------------------------------
# submit() API, windows, gates, reset
# ---------------------------------------------------------------------------

class TestSubmitAndState:
    def test_submit_event_equivalent_to_feeder(self):
        fusion = MultimodalFusion()
        ok = fusion.submit(FusionEvent(modality=Modality.GAZE, kind="target",
                                       payload={"point": (100.0, 100.0),
                                                "target": None},
                                       confidence=0.8, timestamp=1.0))
        assert ok is True
        decision = fusion.update(now=1.05)
        assert decision.point == (100.0, 100.0)
        assert decision.confidence == pytest.approx(0.8)

        fusion.submit(FusionEvent(modality=Modality.HAND, kind="pinch",
                                  payload={"point": (110.0, 105.0)},
                                  confidence=0.9, timestamp=1.1))
        decision2 = fusion.update(now=1.15)
        assert "hand:pinch" in decision2.confirmations

    def test_submit_rejects_malformed_event(self):
        fusion = MultimodalFusion()
        assert fusion.submit(None) is False
        assert fusion.submit("not-an-event") is False

    def test_event_window_prunes_old_events(self):
        fusion = MultimodalFusion()
        fusion.submit(FusionEvent(modality=Modality.MOUSE, kind="move",
                                  payload={"point": (0.0, 0.0)},
                                  confidence=1.0, timestamp=0.0))
        fusion.submit(FusionEvent(modality=Modality.MOUSE, kind="move",
                                  payload={"point": (1.0, 1.0)},
                                  confidence=1.0, timestamp=3.5))
        assert fusion.snapshot()["events"] == 1  # 3.5 - 3.0 window cutoff

    def test_min_combined_confidence_gate(self):
        fusion = MultimodalFusion()
        fusion.update_gaze((50.0, 50.0), None, 0.2, timestamp=1.0)  # < 0.35
        decision = fusion.update(now=1.05)
        assert decision.confidence == pytest.approx(0.2)
        assert not decision.has_target                    # no weak lock
        assert bool(decision.contributing & Modality.GAZE)

    def test_mouse_and_keyboard_contribute(self):
        fusion = MultimodalFusion(mode=FusionMode.GAZE)
        fusion.update_mouse((700.0, 700.0), timestamp=1.0)
        fusion.update_keyboard("k", timestamp=1.0)
        decision = fusion.update(now=1.05)
        assert bool(decision.contributing & Modality.MOUSE)
        assert bool(decision.contributing & Modality.KEYBOARD)
        assert not bool(decision.contributing & Modality.GAZE)
        # Mouse is the only target candidate and carries confidence 1.0,
        # so it locks the point even at low priority weight (0.2).
        assert decision.point == (700.0, 700.0)
        assert decision.confidence == pytest.approx(1.0)

    def test_low_confidence_mouse_is_gated(self):
        fusion = MultimodalFusion(mode=FusionMode.GAZE)
        fusion.submit(FusionEvent(modality=Modality.MOUSE, kind="move",
                                  payload={"point": (700.0, 700.0),
                                           "confidence": 0.2},
                                  confidence=0.2, timestamp=1.0))
        decision = fusion.update(now=1.05)
        assert decision.confidence == pytest.approx(0.2)
        assert not decision.has_target  # below min_combined_confidence

    def test_reset_clears_everything(self):
        fusion = MultimodalFusion()
        fusion.update_gaze((500.0, 400.0), make_target(), 0.9, timestamp=1.0)
        fusion.update(now=1.05)
        assert fusion.last_decision is not None
        fusion.reset()
        assert fusion.last_decision is None
        assert fusion.last_conflict is None
        assert fusion.snapshot()["events"] == 0
        decision = fusion.update(now=2.0)
        assert decision.confidence == 0.0
        assert not decision.has_target


# ---------------------------------------------------------------------------
# Robustness / threading
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_thread_safety_smoke(self):
        fusion = MultimodalFusion()
        n_events, n_threads = 250, 2

        def worker(offset):
            for i in range(n_events):
                fusion.submit(FusionEvent(
                    modality=Modality.MOUSE, kind="move",
                    payload={"point": (float(i), float(offset))},
                    confidence=1.0, timestamp=now_ts()))

        threads = [threading.Thread(target=worker, args=(1000.0 * t,))
                   for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert fusion.snapshot()["events"] == n_events * n_threads
        decision = fusion.update(now=now_ts() + 0.001)  # no exception
        assert decision is not None
        assert isinstance(decision.confidence, float)

    def test_empty_state_update_returns_safe_decision(self):
        fusion = MultimodalFusion()
        decision = fusion.update(now=42.0)
        assert decision.mode is FusionMode.FUSION
        assert decision.confidence == 0.0
        assert not decision.has_target
        assert decision.confirmations == []
        assert decision.utterance == ""
        assert fusion.last_decision is decision
