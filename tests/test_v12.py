"""AirMouse v12.0 tests — Personal Interaction Twin (§2) +
Temporal Interaction World Model (§3).

Run from the repo root:  pytest tests/test_v12.py -q
"""

import copy
import json

import pytest

from airmouse.world_model_temporal import (ComputerState, HumanState,
                                           MismatchRecord, SensorHealth,
                                           StateDiff, TaskState,
                                           TemporalWorldModel, WorldSnapshot)

twin_mod = pytest.importorskip("airmouse.intelligence.twin")
from airmouse.intelligence.twin import (  # noqa: E402
    FactSource, PersonalInteractionTwin, TWIN_FORMAT_VERSION, TwinCategory)


# ═════════════════════════════════════════════════════════════════════════════
# §2 — Personal Interaction Twin
# ═════════════════════════════════════════════════════════════════════════════

class TestTwinLearning:
    def test_learn_creates_fact_with_full_schema(self):
        t = PersonalInteractionTwin()
        f = t.learn(TwinCategory.MODALITY_PREFERENCE, "click", "gaze",
                    source=FactSource.GESTURE, confidence=0.7,
                    context={"app": "chrome"})
        assert f is not None
        d = f.to_dict()
        for field in ("source", "confidence", "context", "ts", "wall",
                      "frequency", "success_rate", "provenance"):
            assert field in d, field
        assert d["source"] == "gesture"
        assert d["frequency"] == 1

    def test_repeated_observations_increase_frequency_blend_confidence(self):
        t = PersonalInteractionTwin()
        t.learn(TwinCategory.COMMAND_PREFERENCE, "open_browser",
                "open browser", confidence=0.5)
        f2 = t.learn(TwinCategory.COMMAND_PREFERENCE, "open_browser",
                     "open browser", confidence=0.9)
        assert f2.frequency == 2
        assert 0.5 < f2.confidence <= 0.9

    def test_preferred_modality(self):
        t = PersonalInteractionTwin()
        assert t.preferred_modality("click") is None
        for _ in range(3):
            t.learn(TwinCategory.MODALITY_PREFERENCE, "click", "voice",
                    confidence=0.8)
        assert t.preferred_modality("click") == "voice"

    def test_top_commands_deterministic(self):
        t = PersonalInteractionTwin()
        for _ in range(4):
            t.learn(TwinCategory.COMMAND_PREFERENCE, "screenshot",
                    "take screenshot", confidence=0.6)
        for _ in range(2):
            t.learn(TwinCategory.COMMAND_PREFERENCE, "mute",
                    "mute audio", confidence=0.6)
        tops = t.top_commands(limit=5)
        assert tops[0] == ("screenshot", 4)
        assert ("mute", 2) in tops

    def test_record_outcome_updates_success_rate(self):
        t = PersonalInteractionTwin()
        t.learn(TwinCategory.COMMAND_PREFERENCE, "open_browser",
                "open browser")
        t.record_outcome(TwinCategory.COMMAND_PREFERENCE, "open_browser",
                         True)
        t.record_outcome(TwinCategory.COMMAND_PREFERENCE, "open_browser",
                         True)
        t.record_outcome(TwinCategory.COMMAND_PREFERENCE, "open_browser",
                         False)
        fact = t.get(TwinCategory.COMMAND_PREFERENCE, "open_browser")
        assert fact["successes"] == 2 and fact["failures"] == 1
        assert abs(fact["success_rate"] - 2 / 3) < 0.01

    def test_outcome_for_unknown_fact_is_false(self):
        t = PersonalInteractionTwin()
        assert t.record_outcome("command_preference", "nope", True) is False


class TestTwinCorrectionAndForgetting:
    def test_correction_boosts_confidence_and_is_tracked(self):
        t = PersonalInteractionTwin()
        t.learn(TwinCategory.VOICE_VOCABULARY, "app_name", "crome",
                confidence=0.4)
        f = t.correct(TwinCategory.VOICE_VOCABULARY, "app_name", "chrome")
        assert f.value == "chrome"
        assert f.confidence >= 0.9
        assert t.status()["corrections"] == 1
        corr = t.query(category=TwinCategory.CORRECTION_BEHAVIOR.value)
        assert len(corr) == 1

    def test_forget_single_fact(self):
        t = PersonalInteractionTwin()
        t.learn("habit", "morning_check", "email")
        assert t.forget("habit", "morning_check") is True
        assert t.get("habit", "morning_check") is None
        assert t.forget("habit", "morning_check") is False

    def test_decay_forgots_low_confidence_old_facts(self):
        t = PersonalInteractionTwin(decay_half_life_hours=0.0001,
                                    min_confidence=0.5)
        t.learn("habit", "weak", "rare", confidence=0.3)
        t.learn("habit", "strong", "daily", confidence=0.99)
        forgotten = t.decay()
        assert forgotten == 1
        assert t.get("habit", "weak") is None
        assert t.get("habit", "strong") is not None

    def test_reset_all_and_per_category(self):
        t = PersonalInteractionTwin()
        t.learn("habit", "a", "x")
        t.learn("preference", "b", "y")
        assert t.reset("habit") == 1
        assert len(t.query(category="preference")) == 1
        assert t.reset() == 1
        assert t.status()["facts"] == 0


class TestTwinBoundsAndSafety:
    def test_secrets_rejected_fail_closed(self):
        t = PersonalInteractionTwin()
        assert t.learn("preference", "k1",
                       "my password is hunter2") is None
        assert t.learn("preference", "k2",
                       "ghp_AbCdefGhIjKlMnOpQrStUvWx1234567890") is None
        assert t.learn("preference", "k3", "sk-abcdefghijklmnop1234") is None
        assert t.learn("preference", "password", "x") is None  # hint in key
        assert t.status()["rejected_inputs"] == 4
        assert t.status()["facts"] == 0

    def test_oversized_and_invalid_inputs_rejected(self):
        t = PersonalInteractionTwin()
        assert t.learn("preference", "k", "x" * 500) is None
        assert t.learn("preference", "k" * 200, "x") is None
        assert t.learn("not_a_category", "k", "x") is None
        assert t.learn("preference", "BAD KEY!", "x") is None
        assert t.learn(None, None, None) is None
        assert t.status()["facts"] == 0

    def test_never_raises_on_garbage(self):
        t = PersonalInteractionTwin()
        for bad in (object(), 12345, [], {}, set(), b"bytes"):
            assert t.learn(bad, bad, bad) is None
        assert t.query(category=object()) == []
        assert t.get(object(), object()) is None

    def test_capacity_eviction_bounded(self):
        t = PersonalInteractionTwin(max_facts=64)
        for i in range(80):
            t.learn("preference", f"k{i:03d}", f"v{i}")
        assert t.status()["facts"] <= 64
        assert t.status()["forgotten"] >= 16

    def test_disabled_twin_is_noop(self):
        t = PersonalInteractionTwin(enabled=False)
        assert t.learn("preference", "k", "v") is None
        assert t.record_outcome("preference", "k", True) is False
        assert t.decay() == 0
        assert t.status()["facts"] == 0

    def test_provenance_bounded(self):
        t = PersonalInteractionTwin()
        for _ in range(20):
            t.learn("preference", "k", "v", confidence=0.5)
        fact = t.get("preference", "k")
        assert len(fact["provenance"]) <= 8


class TestTwinPersistence:
    def test_export_import_roundtrip(self):
        t = PersonalInteractionTwin()
        t.learn("preference", "editor", "vscode", confidence=0.8)
        t.record_outcome("preference", "editor", True)
        data = t.export()
        assert data["format"] == "airmouse-twin"
        assert data["version"] == TWIN_FORMAT_VERSION
        blob = json.dumps(data, sort_keys=True)
        t2 = PersonalInteractionTwin()
        imported, rejected = t2.import_data(json.loads(blob))
        assert imported == 1 and rejected == 0
        got = t2.get("preference", "editor")
        assert got["value"] == "vscode"
        assert got["successes"] == 1

    def test_import_rejects_wrong_format_and_version(self):
        t = PersonalInteractionTwin()
        assert t.import_data({"format": "other"}) == (0, 1)
        assert t.import_data({"format": "airmouse-twin",
                              "version": 999}) == (0, 1)
        assert t.import_data("not a dict") == (0, 1)
        assert t.import_data({"format": "airmouse-twin", "version": 1,
                              "facts": "nope"}) == (0, 1)

    def test_import_sanitizes_secret_values(self):
        t = PersonalInteractionTwin()
        imp, rej = t.import_data({
            "format": "airmouse-twin", "version": 1,
            "facts": [
                {"category": "preference", "key": "ok", "value": "fine"},
                {"category": "preference", "key": "bad",
                 "value": "ghp_AbCdefGhIjKlMnOpQrStUvWx"},
            ]})
        assert imp == 1 and rej == 1
        assert t.get("preference", "bad") is None

    def test_export_is_deterministic(self):
        """Same facts -> same structural export (timestamps legitimately
        vary; ordering and content must not)."""

        def skeleton(twin):
            rows = []
            for f in twin.export()["facts"]:
                f = dict(f)
                f.pop("ts"), f.pop("wall")
                f["provenance"] = [{k: v for k, v in p.items()
                                    if k not in ("ts", "wall")}
                                   for p in f["provenance"]]
                rows.append(f)
            return rows

        t = PersonalInteractionTwin()
        t.learn("preference", "b", "2")
        t.learn("preference", "a", "1")
        t2 = PersonalInteractionTwin()
        t2.learn("preference", "a", "1")
        t2.learn("preference", "b", "2")
        assert skeleton(t) == skeleton(t2)
        # and ordering is sorted by fact_id regardless of insertion order
        ids = [f["fact_id"] for f in t.export()["facts"]]
        assert ids == sorted(ids)


class TestTwinExplainability:
    def test_explain_known_and_unknown(self):
        t = PersonalInteractionTwin()
        t.learn("modality_preference", "click", "gaze",
                source=FactSource.GAZE, confidence=0.7)
        t.learn("modality_preference", "click", "gaze",
                source=FactSource.VOICE, confidence=0.8)
        e = t.explain("modality_preference", "click")
        assert e["known"] is True
        assert e["frequency"] == 2
        assert len(e["because"]) >= 1
        assert "voice" in e["because"][-1]      # latest evidence wins
        assert any("gaze" in b for b in e["because"])  # earlier kept
        e2 = t.explain("modality_preference", "nope")
        assert e2["known"] is False


# ═════════════════════════════════════════════════════════════════════════════
# §3 — Temporal Interaction World Model
# ═════════════════════════════════════════════════════════════════════════════

class TestTemporalWorldObservation:
    def test_observe_creates_snapshot_with_sections(self):
        w = TemporalWorldModel()
        snap = w.observe(
            human={"mode": "voice", "interaction_modality": "voice",
                   "current_intent": "click", "intent_confidence": 0.8,
                   "sensor_health": "ok"},
            computer={"active_application": "chrome",
                      "active_window": "Research — Google Chrome",
                      "browser": "chrome",
                      "tabs": ["AI research", "papers"],
                      "visible_ui_targets": ["Submit", "Cancel"]},
            task={"objective": "research AI", "phase": "execute",
                  "progress": 0.4},
            cause="voice_command", cause_confidence=0.9)
        assert snap.sequence == 1
        assert snap.human.mode == "voice"
        assert snap.computer.active_application == "chrome"
        assert snap.task.objective == "research AI"
        assert snap.cause == "voice_command"

    def test_partial_observe_persists_fields(self):
        w = TemporalWorldModel()
        w.observe(computer={"active_application": "vscode"})
        snap = w.observe(human={"mode": "gaze"}, cause="mode_switch")
        assert snap.computer.active_application == "vscode"
        assert snap.human.mode == "gaze"
        assert snap.sequence == 2

    def test_recent_action_ring_bounded(self):
        w = TemporalWorldModel()
        for i in range(24):
            w.observe(task={"recent_action": f"action_{i}"})
        snap = w.snapshot()
        assert len(snap.task.recent_actions) <= 16
        assert snap.task.recent_actions[-1] == "action_23"

    def test_strings_clipped_bounded(self):
        w = TemporalWorldModel()
        snap = w.observe(computer={
            "active_application": "x" * 9999,
            "tabs": [f"tab{i}" for i in range(100)],
        })
        assert len(snap.computer.active_application) <= 160
        assert len(snap.computer.tabs) <= 32

    def test_snapshot_is_frozen(self):
        w = TemporalWorldModel()
        snap = w.observe(computer={"active_application": "vscode"})
        with pytest.raises(Exception):
            snap.sequence = 99  # frozen dataclass raises on assignment


class TestTemporalWorldTime:
    def test_previous_and_history(self):
        w = TemporalWorldModel()
        w.observe(human={"mode": "hand"})
        w.observe(human={"mode": "voice"})
        w.observe(human={"mode": "gaze"})
        assert w.previous().human.mode == "voice"
        hist = w.history(limit=3)
        assert [s.human.mode for s in hist] == ["hand", "voice", "gaze"]
        assert len(w.history(limit=100)) == 3

    def test_history_bounded(self):
        w = TemporalWorldModel(max_history=8)
        for i in range(30):
            w.observe(cause=f"c{i}")
        assert len(w.history(limit=100)) == 8
        assert w.snapshot().sequence == 30

    def test_transitions_carry_causality(self):
        w = TemporalWorldModel()
        w.observe(cause="voice_command", cause_confidence=0.9)
        w.observe(cause="agent_action", cause_confidence=0.7)
        tr = w.transitions()
        assert tr[-1]["cause"] == "agent_action"
        assert tr[-1]["cause_confidence"] == 0.7

    def test_diff_structured(self):
        w = TemporalWorldModel()
        w.observe(computer={"active_application": "vscode"})
        w.observe(computer={"active_application": "chrome"})
        d = w.diff()
        assert "computer.active_application" in d.changed
        det = d.to_dict()
        assert det["details"]["computer.active_application"]["from"] == "vscode"
        assert det["details"]["computer.active_application"]["to"] == "chrome"

    def test_diff_defaults_to_previous_current(self):
        w = TemporalWorldModel()
        w.observe(human={"mode": "hand"})
        w.observe(human={"mode": "fusion"})
        d = w.diff()
        assert "human.mode" in d.changed


class TestTemporalWorldExpectation:
    def test_expect_then_match_no_mismatch(self):
        w = TemporalWorldModel()
        w.observe(task={"phase": "execute"})
        w.expect("verify")
        snap = w.observe(task={"phase": "verify"}, cause="planner")
        assert snap.mismatch is False
        assert w.mismatches() == []

    def test_expect_then_drift_records_mismatch(self):
        w = TemporalWorldModel()
        w.observe(task={"phase": "execute"})
        w.expect("verify")
        snap = w.observe(task={"phase": "execute"}, cause="app_froze")
        assert snap.mismatch is True
        ms = w.mismatches()
        assert len(ms) == 1
        assert ms[0].expected == "verify"
        assert ms[0].observed == "execute"
        assert ms[0].cause == "app_froze"

    def test_mismatch_ring_bounded(self):
        w = TemporalWorldModel()
        for i in range(200):
            w.expect(f"state_{i}")
            w.observe(task={"phase": "different"})
        assert len(w.mismatches(limit=200)) <= 100


class TestTemporalWorldExplainPredict:
    def test_explain_latest_transition(self):
        w = TemporalWorldModel()
        w.observe(computer={"active_application": "vscode"},
                  cause="startup")
        w.observe(human={"mode": "voice"}, cause="wake_word")
        e = w.explain()
        assert e["cause"] == "wake_word"
        assert "human.mode" in e["changed_fields"]
        assert e["active_application"] == "vscode"

    def test_predict_state_is_prediction_not_permission(self):
        w = TemporalWorldModel()
        w.observe(task={"phase": "execute",
                        "expected_next_state": "verify"})
        p = w.predict_state()
        assert p["prediction"] is True
        assert p["permission"] is False
        assert p["task_phase"] == "verify"

    def test_predict_without_expectation_persists(self):
        w = TemporalWorldModel()
        w.observe(task={"phase": "execute"})
        p = w.predict_state()
        assert p["task_phase"] == "execute"
        assert p["confidence"] <= 0.6


class TestWorldModelIntegrationContract:
    def test_coexists_with_v115_world_model(self):
        """The temporal model extends, never replaces, the v11.5 model."""
        from airmouse.world_model import WorldModel as LegacyWorldModel
        legacy = LegacyWorldModel(context_engine=None)
        temporal = TemporalWorldModel()
        assert legacy is not None and temporal is not None
        # independent operation
        temporal.observe(computer={"active_application": "chrome"})
        assert temporal.snapshot().computer.active_application == "chrome"

    def test_frozen_sections_immutable(self):
        for cls in (HumanState, ComputerState, TaskState, WorldSnapshot,
                    StateDiff, MismatchRecord):
            inst = cls()
            with pytest.raises(Exception):
                inst.timestamp = 1.0  # any attribute assignment must raise


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
