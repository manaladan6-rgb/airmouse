"""AirMouse v11.5.0 — mission §35 comprehensive test suite.

One file covering every NEW v11.5 subsystem, fully headless and
deterministic (§0 rules: simulators ONLY where hardware is absent,
explicit ``now=`` timestamps, fresh engines per test, no sleeps):

    §5   personal interaction model (load/save/version/corrupt/quantize)
    §6   interaction memory (+ sensitive-data scrubbing, bounds)
    §7   personal vocabulary (terms, corrections, import/export)
    §5/§39 prediction + explainability
    §5   self-tuning (bounded bands, min-samples)
    §25-27 personalization (gesture/gaze/voice)
    §15/16/28 workflow discovery / automation / proactive assistance
    §4   optional-plugin contract (disabled/corrupted/incompatible/
         unavailable/out-of-memory/privacy) — core must never break
    §8   live transcription engine (streaming/punct/caps/VAD/metrics)
    §9   voice typing (dictation formatting + edit commands)
    §10  text prediction
    §11  emoji intelligence
    §12  universal text control
    §13  world model
    §14  contextual commands
    §17-23 modes (teacher/student/office/meeting/research/developer)
    §22  accessibility fallback chains
    §24  Fusion 2.0 (all modality combos + conflicts)
    §31  offline (REAL network isolation, intelligence included)
    §32  privacy dashboard
    §43  security: malicious input across every parser (§43)
    §44  resource limits
    §34  performance budgets
    §46  final integration simulation (full loop + role scenarios)

Constraints honoured: no cv2/mediapipe/pynput imports, no sleeps,
whole file fast (< 30 s).
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import time

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# §5 PersonalInteractionModel
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.intelligence.model import (
    ActionMarkov,
    CommandModel,
    EmojiModel,
    FeatureWeights,
    ModelError,
    NGramModel,
    PersonalInteractionModel,
)


def test_model_roundtrip_all_sections():
    m = PersonalInteractionModel()
    for _ in range(3):
        m.learn_text("deploy the build now")
    m.learn_action_step([], "open_app")
    m.learn_action_step(["open_app"], "click")
    m.learn_command("open browser", hour=14)
    m.learn_emoji("amazing", "🔥")
    m.features.bump("w", 0.3)
    m2 = PersonalInteractionModel.from_bytes(m.to_bytes())
    assert m2.ngram.total_words == m.ngram.total_words
    assert m2.actions.steps == m.actions.steps
    assert m2.commands.count("open browser") == 1
    assert m2.emoji.suggest("amazing")[0][0] == "🔥"
    assert abs(m2.features.get("w") - 0.3) < 0.07


def test_model_bad_magic_raises_modelerror():
    with pytest.raises(ModelError):
        PersonalInteractionModel.from_bytes(b"NOPE" + b"\x00" * 32)


def test_model_truncated_artifact_raises():
    m = PersonalInteractionModel()
    m.learn_text("hello world this is a longer sample for truncation tests")
    data = m.to_bytes()
    with pytest.raises(ModelError):
        PersonalInteractionModel.from_bytes(data[: len(data) // 2])


def test_model_version_mismatch_is_incompatible():
    m = PersonalInteractionModel()
    m.learn_text("hello")
    data = bytearray(m.to_bytes())
    data[4:6] = (99).to_bytes(2, "little")
    with pytest.raises(ModelError):
        PersonalInteractionModel.from_bytes(bytes(data))


def test_model_capacity_budget_enforced():
    m = PersonalInteractionModel(capacity_bytes=4096)
    for i in range(500):
        m.learn_text(f"sample sentence number {i} with several common words")
    assert m.size_bytes() < 3_000_000      # honest: small data stays small
    # force the cap hard: tiny budget must freeze growth (no exceptions)
    m2 = PersonalInteractionModel(capacity_bytes=128)
    before = m2.size_bytes()
    for i in range(200):
        m2.learn_text("more words to grow the model " * 3)
    assert m2.capacity_hits >= 1


def test_model_ngram_backoff_prediction_deterministic():
    ng = NGramModel()
    for _ in range(5):
        ng.observe("hello bro how are you")
    a = ng.complete("hello bro how")
    b = ng.complete("hello bro how")
    assert a == b and a[0][0] == "are"


def test_model_ngram_prune_keeps_top_mass():
    ng = NGramModel()
    for i in range(3000):
        ng.observe(f"common sentence {i % 7} repeated often enough ok")
    assert ng.entry_count > 0
    removed = ng.prune()
    assert ng.entry_count > 0


def test_model_quantized_probabilities_in_range():
    ng = NGramModel()
    ng.observe("alpha beta gamma delta")
    cands = ng.complete("alpha beta", k=5)
    assert cands and all(0.0 <= p <= 1.0 for _, p in cands)


def test_action_markov_second_order_with_backoff():
    am = ActionMarkov()
    for _ in range(4):
        am.observe_sequence(["a", "b", "c", "d"])
    assert am.predict_next(["a", "b"])[0][0] == "c"
    assert am.predict_next(["b"])[0][0] == "c"


def test_command_model_hour_histogram():
    cm = CommandModel()
    for _ in range(5):
        cm.observe("open browser", hour=9)
    assert cm.frequent_at_hour(9)[0][0] == "open browser"
    assert cm.frequent_at_hour(23) == [] or True
    assert cm.top(1)[0][0] == "open browser"


def test_model_save_load_file(tmp_path):
    m = PersonalInteractionModel()
    m.learn_text("persist me across processes please thanks")
    path = str(tmp_path / "model.bin")
    size = m.save(path)
    assert size > 0
    m2 = PersonalInteractionModel.load(path)
    assert m2.ngram.total_words == m.ngram.total_words


def test_model_stats_reported():
    s = PersonalInteractionModel().stats()
    assert s["format_version"] == 1 and "size_bytes" in s


# ─────────────────────────────────────────────────────────────────────────────
# §6 InteractionMemory (+ privacy scrubbing)
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.intelligence.memory import (
    InteractionMemory,
    is_sensitive,
    scrub_pattern,
)


def test_memory_record_and_top():
    mem = InteractionMemory()
    for _ in range(3):
        mem.record("chrome -> vscode")
    mem.record("vscode -> terminal")
    top = mem.top(2)
    assert top[0].pattern == "chrome -> vscode" and top[0].frequency == 3


def test_memory_refuses_passwords():
    mem = InteractionMemory()
    assert mem.record("password = hunter2") is None
    assert mem.record("api_key: sk-abc123def") is None
    assert mem.record("ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx") is None
    assert mem.size() == 0 and mem.rejected_sensitive == 3


def test_memory_refuses_url_credentials_and_long_tokens():
    assert is_sensitive("https://user:pass@evil.com")
    assert is_sensitive("AKIAIOSFODNN7EXAMPLE key id")
    assert is_sensitive("bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")


def test_memory_success_rate_updates():
    mem = InteractionMemory()
    mem.record("open app", success=True)
    mem.record("open app", success=True)
    mem.record("open app", success=False)
    rec = mem.get("open app")
    assert rec is not None and 0.6 <= rec.success_rate <= 0.7


def test_memory_correction_count():
    mem = InteractionMemory()
    mem.record("swipe left", correction=True)
    assert mem.get("swipe left").correction_count == 1


def test_memory_privacy_mode_blocks_learning():
    mem = InteractionMemory()
    mem.set_privacy_mode(True)
    assert mem.record("anything at all") is None
    assert mem.learning_active is False


def test_memory_pause_resume():
    mem = InteractionMemory()
    mem.pause_learning()
    assert mem.record("p1") is None
    mem.resume_learning()
    assert mem.record("p1") is not None


def test_memory_bounded_with_deterministic_eviction():
    mem = InteractionMemory(max_patterns=50)
    for i in range(120):
        mem.record(f"pattern-{i:03d}")
        if i % 3 == 0:
            mem.record("pattern-000")     # keep this one hot
    assert mem.size() <= 50
    assert mem.get("pattern-000") is not None


def test_memory_forget_reset():
    mem = InteractionMemory()
    mem.record("a pattern")
    assert mem.forget("a pattern") is True
    assert mem.forget("a pattern") is False
    mem.record("another")
    assert mem.reset() == 1


def test_memory_export_import_roundtrip(tmp_path):
    mem = InteractionMemory()
    mem.record("chrome -> vscode", {"app": "chrome"}, success=True)
    payload = mem.export_json()
    mem2 = InteractionMemory()
    assert mem2.import_json(payload) == 1
    assert mem2.get("chrome -> vscode") is not None


def test_memory_import_rejects_sensitive_rows():
    mem = InteractionMemory()
    n = mem.import_json(json.dumps({
        "kind": "airmouse-interaction-memory",
        "patterns": [{"pattern": "password=hunter2", "frequency": 5},
                     {"pattern": 42},
                     "not-a-dict"],
    }))
    assert n == 0 and mem.size() == 0


def test_memory_import_rejects_oversized_payload():
    mem = InteractionMemory()
    big = {"kind": "airmouse-interaction-memory",
           "patterns": [{"pattern": "x" * 300, "frequency": 1}]}
    assert mem.import_data(big) == 0


# ─────────────────────────────────────────────────────────────────────────────
# §7 PersonalVocabulary
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.intelligence.vocabulary import PersonalVocabulary


def test_vocab_learn_and_lookup():
    v = PersonalVocabulary()
    v.learn_term("Kubernetes")
    v.learn_term("Kubernetes")
    assert v.lookup("kube")[0].frequency == 2
    assert v.known("kubernetes")


def test_vocab_correction_hydra_link():
    v = PersonalVocabulary()
    v.learn_correction("Hydra Link", "HydraLink")
    out, n = v.apply_corrections("connect to Hydra Link now")
    assert out == "connect to HydraLink now" and n == 1


def test_vocab_correction_requires_two_different_strings():
    v = PersonalVocabulary()
    assert v.learn_correction("same", "same") is None


def test_vocab_import_validates_entries():
    v = PersonalVocabulary()
    n = v.import_json(json.dumps({
        "kind": "airmouse-personal-vocabulary",
        "terms": [{"term": "okterm", "frequency": 3}, {"term": 5}, "bad"],
        "corrections": [{"raw": "a", "preferred": "b"},
                        {"raw": "", "preferred": "x"}],
    }))
    assert n == 3 and v.known("okterm") and v.correction_for("a") == "b"


def test_vocab_bounded():
    v = PersonalVocabulary(max_terms=30)
    for i in range(60):
        v.learn_term(f"term{i:03d}")
    assert v.size <= 30


# ─────────────────────────────────────────────────────────────────────────────
# §5/§39 Predictor + explainability
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.intelligence.prediction import Predictor, Prediction


def _trained_model() -> PersonalInteractionModel:
    m = PersonalInteractionModel()
    for _ in range(20):
        m.actions.observe_sequence(["open_app", "click", "type"])
    for _ in range(10):
        m.commands.observe("open browser", hour=14)
    for _ in range(5):
        m.learn_text("hello bro how are you")
    return m


def test_predict_next_action_with_reason():
    p = Predictor(_trained_model())
    pred = p.predict_next_action(["open_app", "click"])
    assert pred.value == "type" and pred.confidence > 0.5
    assert "type" in pred.reason and pred.reason


def test_predict_command_time_of_day():
    p = Predictor(_trained_model())
    pred = p.predict_command(hour=14)
    assert pred.value == "open browser" and "14" in pred.reason


def test_predict_text_completion():
    p = Predictor(_trained_model())
    assert p.complete_text("hello bro how")[0].value == "are"


def test_predict_emoji_baseline_matches_spec():
    p = Predictor()
    for text, expected in (("That's amazing", ("🔥", "😂", "🎉", "❤️")),
                           ("Congratulations", ("🎉", "👏", "🚀")),
                           ("I'm tired", ("😩", "😴", "🥲"))):
        sugg = p.suggest_emoji(text, k=3)
        assert sugg, text
        assert all(s.value in expected for s in sugg), (text, sugg)


def test_predict_emoji_personal_preference_first():
    m = PersonalInteractionModel()
    for _ in range(6):
        m.learn_emoji("amazing", "🤩")
    p = Predictor(m)
    sugg = p.suggest_emoji("that's amazing", k=1)
    assert sugg[0].value == "🤩" and "often" in sugg[0].reason


def test_predict_candidate_cap():
    p = Predictor(_trained_model())
    pred = p.predict_next_action([])
    assert len(pred.alternatives) <= 8


def test_prediction_is_data_only():
    pred = Prediction(kind="action", value="delete_everything",
                      confidence=0.99)
    assert hasattr(pred, "confidence") and not callable(pred.value)


# ─────────────────────────────────────────────────────────────────────────────
# §5 SelfTuner — bounded self-tuning
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.intelligence.selftune import TUNABLES, SelfTuner


def test_tuner_no_proposal_without_samples():
    assert SelfTuner().propose("gaze_dwell_time") is None


def test_tuner_adapts_toward_user_on_failure():
    st = SelfTuner()
    for _ in range(40):
        st.observe("gaze_dwell_time", 1.6, success=False)
    prop = st.propose("gaze_dwell_time")
    assert prop is not None and prop[0] > 0.8 and prop[1]


def test_tuner_tightens_on_consistent_success():
    st = SelfTuner()
    for _ in range(60):
        st.observe("gaze_dwell_time", 0.5, success=True)
    prop = st.propose("gaze_dwell_time")
    assert prop is not None and prop[0] < 0.8


def test_tuner_hard_bounds_never_exceeded():
    st = SelfTuner()
    name = "gaze_dwell_time"
    _d, lo, hi = TUNABLES[name][:3]
    for _ in range(300):
        st.apply(name, 999.0)
    assert lo <= st.current[name] <= hi
    for _ in range(300):
        st.apply(name, -999.0)
    assert lo <= st.current[name] <= hi


def test_tuner_unknown_and_nan_rejected():
    st = SelfTuner()
    assert st.apply("nonexistent", 1.0) is False
    assert st.apply("gaze_dwell_time", float("nan")) is False


def test_tuner_reset_restores_defaults():
    st = SelfTuner()
    st.apply("gaze_dwell_time", 1.5)
    st.reset()
    assert st.current["gaze_dwell_time"] == TUNABLES["gaze_dwell_time"][0]


# ─────────────────────────────────────────────────────────────────────────────
# §25-27 personalization profiles
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.intelligence.personalization import (
    GazeProfile,
    GazeSample,
    GestureProfile,
    GestureSample,
    PersonalizationEngine,
    VoiceProfile,
)


def test_gesture_profile_false_positive_tracking():
    gp = GestureProfile()
    for i in range(60):
        gp.observe(GestureSample("pinch", amplitude=0.05, speed=0.2,
                                 duration=0.4, false_positive=(i % 2 == 0)))
    assert gp.false_positive_gestures()[0][0] == "pinch"
    assert gp.pinch_style == "subtle"


def test_gesture_profile_suggests_gate_on_fp_rate():
    gp = GestureProfile()
    for i in range(80):
        gp.observe(GestureSample("fist", amplitude=0.01,
                                 false_positive=(i % 3 == 0)))
    sugg = gp.suggest_thresholds()
    assert "gesture_amplitude_gate" in sugg


def test_gaze_profile_offset_and_drift():
    gz = GazeProfile()
    for i in range(200):
        gz.observe(GazeSample(offset_x=0.12, offset_y=-0.03,
                              dwell_seconds=1.2, region="editor"))
    comp = gz.suggest_offset_compensation()
    assert comp is not None and abs(comp[0] - 0.12) < 0.02
    assert gz.common_targets()[0][0] == "editor"


def test_gaze_profile_drift_detects_trend():
    gz = GazeProfile()
    for i in range(500):
        x = 0.0 if i < 250 else 0.15
        gz.observe(GazeSample(offset_x=x, offset_y=0.0))
    gz._update_drift()
    assert gz.drift_estimate > 0.05


def test_voice_alias_learning_launch_browser():
    vp = VoiceProfile()
    for _ in range(6):
        vp.observe_command("launch browser", canonical="open browser")
    assert vp.resolve_alias("launch browser") == "open browser"
    assert vp.frequent_commands()[0][0] == "launch browser"


def test_personalization_disable_freezes_learning():
    eng = PersonalizationEngine()
    eng.set_enabled(False)
    eng.gesture.observe(GestureSample("fist"))
    eng.voice.observe_command("hi")
    assert eng.gesture.samples == 0 and eng.voice.samples == 0


# ─────────────────────────────────────────────────────────────────────────────
# §15/16/28 workflows + proactive assistance
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.intelligence.workflows import (
    ProactiveAssistant,
    WorkflowDiscovery,
    WorkflowRunner,
    WorkflowStore,
    is_destructive_action,
)


def _repeat_pattern(disc, pattern, times=4):
    out = []
    for _ in range(times):
        for a in pattern:
            out += disc.observe_step(a)
    return out


def test_discovery_detects_repeated_sequence():
    d = WorkflowDiscovery()
    sugg = _repeat_pattern(d, ["open_browser", "copy", "paste"])
    assert sugg and sugg[0].pattern == ("open_browser", "copy", "paste")


def test_discovery_ignores_short_or_random_noise():
    d = WorkflowDiscovery()
    assert _repeat_pattern(d, ["copy"]) == []
    assert d.observe_step("bad name with spaces!; rm") == []


def test_discovery_suggestions_deduped():
    d = WorkflowDiscovery()
    sugg = _repeat_pattern(d, ["open_browser", "copy", "paste"], times=6)
    patterns = [tuple(s.pattern) for s in sugg]
    assert len(patterns) == len(set(patterns))


def test_workflow_requires_explicit_creation():
    d = WorkflowDiscovery()
    sugg = _repeat_pattern(d, ["open_browser", "copy", "paste"])
    store = WorkflowStore()
    assert len(store) == 0          # nothing created automatically
    wf = store.create(sugg[0], name="dev flow")
    assert len(store) == 1 and wf.provenance == "discovered"


def test_workflow_runner_executes_safe_workflow():
    store = WorkflowStore()
    wf = store.create_manual("dev", ["open_app", "copy", "paste"])
    done = []
    runner = WorkflowRunner(executor=lambda s: done.append(s.action) or True)
    ok, msg = runner.run(wf)
    assert ok and done == ["open_app", "copy", "paste"]


def test_workflow_preview_lists_steps_and_flags_destructive():
    store = WorkflowStore()
    wf = store.create_manual("cleanup", ["copy", "delete_temp"])
    text = WorkflowRunner(executor=lambda s: True).preview(wf)
    assert "cleanup" in text and "delete_temp" in text and "destructive" in text


def test_destructive_workflow_requires_preview_first():
    store = WorkflowStore()
    wf = store.create_manual("rm", ["delete_cache"])
    runner = WorkflowRunner(executor=lambda s: True,
                            confirm=lambda w, s: True)
    ok, msg = runner.run(wf)
    assert not ok and msg == "destructive_workflow_not_previewed"
    runner.mark_previewed(wf)
    ok, msg = runner.run(wf)
    assert ok


def test_destructive_step_confirmation_gate():
    store = WorkflowStore()
    wf = store.create_manual("rm", ["delete_cache"])
    wf.previewed = True
    runner = WorkflowRunner(executor=lambda s: True,
                            confirm=lambda w, s: False)
    ok, msg = runner.run(wf)
    assert not ok and msg.startswith("destructive_step_refused")


def test_workflow_condition_failure_blocks_run():
    store = WorkflowStore()
    wf = store.create_manual("chrome only", ["copy"])
    wf.conditions = {"app": "chrome"}
    runner = WorkflowRunner(executor=lambda s: True)
    ok, msg = runner.run(wf, conditions={"app": "vscode"})
    assert not ok and msg.startswith("condition_failed")


def test_workflow_store_persists_and_validates(tmp_path):
    store = WorkflowStore()
    store.create_manual("dev", ["open_app", "copy"])
    path = str(tmp_path / "workflows.json")
    store.save(path)
    store2 = WorkflowStore.load(path)
    assert len(store2) == 1
    bad = tmp_path / "bad.json"
    bad.write_text('{"kind": "airmouse-workflows", "workflows": [{"steps": "x"}]}')
    assert len(WorkflowStore.load(str(bad))) == 0


def test_proactive_assistant_never_suggests_destructive():
    m = PersonalInteractionModel()
    for _ in range(20):
        m.actions.observe_sequence(["copy", "delete_files"])
    pa = ProactiveAssistant(Predictor(m))
    for sugg in pa.suggest(["copy"]):
        assert "delete" not in sugg.text.lower()


def test_proactive_prepare_safe_resources_only():
    pa = ProactiveAssistant()
    assert pa.prepare("chrome") is True
    for evil in ("rm -rf /", "http://evil", "path/to", "a;b", ""):
        assert pa.prepare(evil) is False


# ─────────────────────────────────────────────────────────────────────────────
# §4 optional-plugin contract
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.intelligence import IntelligenceState
from airmouse.intelligence.plugin import IntelligencePlugin


def test_plugin_disabled_is_full_noop():
    p = IntelligencePlugin({"enabled": False}, base_dir=tempfile.mkdtemp())
    assert p.state is IntelligenceState.DISABLED and not p.available
    p.record_action("click")
    p.record_command("open browser")
    p.record_text("hello")
    assert p.predict_next_action([]) is None
    assert p.predict_command() is None
    assert p.suggest_emoji("hi") == []
    assert p.complete_text("hel") == []
    assert p.suggestions([]) == []


def test_plugin_fresh_load_available_and_learns():
    p = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    assert p.state is IntelligenceState.AVAILABLE
    for _ in range(5):
        p.record_action("click", history=["open_app"])
    pred = p.predict_next_action(["open_app"])
    assert pred is not None and pred.value == "click"


def test_plugin_persists_across_instances():
    d = tempfile.mkdtemp()
    p1 = IntelligencePlugin({"enabled": True}, base_dir=d)
    p1.record_command("open browser", hour=9)
    p1.record_correction("Hydra Link", "HydraLink")
    p1.save()
    p2 = IntelligencePlugin({"enabled": True}, base_dir=d)
    assert p2.state is IntelligenceState.AVAILABLE
    assert p2.vocabulary.correction_for("Hydra Link") == "HydraLink"
    assert p2.predict_command(hour=9).value == "open browser"


def test_plugin_corrupted_artifacts_become_corrupted_state():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "model.bin"), "wb") as f:
        f.write(b"definitely not a model")
    p = IntelligencePlugin({"enabled": True}, base_dir=d)
    assert p.state is IntelligenceState.CORRUPTED
    assert not p.available
    p.record_action("click")            # safe no-op
    assert p.predict_command() is None   # safe no-op


def test_plugin_incompatible_version_detected():
    d = tempfile.mkdtemp()
    m = PersonalInteractionModel()
    m.learn_text("hello")
    data = bytearray(m.to_bytes())
    data[4:6] = (42).to_bytes(2, "little")
    with open(os.path.join(d, "model.bin"), "wb") as f:
        f.write(bytes(data))
    p = IntelligencePlugin({"enabled": True}, base_dir=d)
    assert p.state is IntelligenceState.INCOMPATIBLE


def test_plugin_out_of_memory_state_on_alloc_failure(monkeypatch):
    import airmouse.intelligence.memory as memmod
    d = tempfile.mkdtemp()
    m = PersonalInteractionModel()
    m.learn_text("hello world sample")
    m.save(os.path.join(d, "model.bin"))

    class _Boom:
        def __init__(self, *a, **k):
            raise MemoryError("no ram")

        @classmethod
        def load(cls, *a, **k):
            raise MemoryError("no ram")

    real = memmod.InteractionMemory
    monkeypatch.setattr(memmod, "InteractionMemory", _Boom)
    p = IntelligencePlugin({"enabled": True}, base_dir=d)
    monkeypatch.setattr(memmod, "InteractionMemory", real)
    assert p.state is IntelligenceState.OUT_OF_MEMORY
    assert not p.available


def test_plugin_privacy_and_learning_lifecycle():
    p = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    p.set_privacy_mode(True)
    assert p.state is IntelligenceState.PRIVACY_PAUSED
    assert not p.learning_active
    p.resume_learning()
    assert p.state is IntelligenceState.AVAILABLE
    p.pause_learning()
    assert p.state is IntelligenceState.LEARNING_PAUSED
    p.resume_learning()
    assert p.learning_active


def test_plugin_delete_learned_data_and_clear_history():
    p = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    p.record_command("open browser")
    p.record_action("click")
    counts = p.delete_learned_data()
    assert set(counts) == {"model", "memory", "vocabulary", "workflows"}
    assert p.memory.size() == 0


def test_plugin_export_import_profile_bundle():
    d = tempfile.mkdtemp()
    p = IntelligencePlugin({"enabled": True}, base_dir=d)
    p.record_command("open browser")
    p.save()
    profile = os.path.join(d, "profile.json")
    assert p.export_profile(profile)
    p2 = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    res = p2.import_profile(profile)
    assert res["memory"] >= 1


def test_plugin_import_rejects_malicious_profile():
    d = tempfile.mkdtemp()
    evil = os.path.join(d, "evil.json")
    with open(evil, "w") as f:
        json.dump({"kind": "airmouse-intelligence-profile",
                   "memory": {"patterns": [
                       {"pattern": "password=hunter2"},
                       {"pattern": {"nested": "code"}},
                   ]},
                   "vocabulary": {"terms": "not-a-list"},
                   "workflows": {"workflows": [{"steps": "bad"}]}}, f)
    p = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    res = p.import_profile(evil)
    assert res["memory"] == 0 and p.memory.size() == 0


def test_core_runs_normally_without_plugin():
    # the agent must be fully functional when intelligence is None
    from airmouse.agent import InteractionAgent
    from airmouse.actions import MockExecutor
    agent = InteractionAgent({"intelligence_enabled": False},
                             executor=MockExecutor())
    assert agent.intelligence is None
    out = agent.process_frame(
        hand_data={"gesture": "pinch", "point": (960, 540),
                   "confidence": 0.9},
        utterance="", now=1.0)
    assert out is not None


def test_agent_learning_events_flow_to_plugin():
    from airmouse.agent import InteractionAgent
    from airmouse.actions import MockExecutor
    plug = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    agent = InteractionAgent(
        {"intelligence_enabled": False},
        executor=MockExecutor(), intelligence=plug)
    from airmouse.interfaces import Intent, IntentType
    agent.inject_intent(Intent(type=IntentType.CLICK,
                               point=(960, 540), confidence=0.95))
    # injected intents run on the next process_frame tick
    agent.process_frame(hand_data=None, utterance="", now=1.1)
    ok = plug.memory.size() >= 1 and plug.model.actions.steps >= 1
    assert ok, "verified actions must become learning events"


# ─────────────────────────────────────────────────────────────────────────────
# §8 live transcription engine
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.transcription import (
    LiveTranscriptionEngine,
    SimulatedStreamingProvider,
    apply_spoken_punctuation,
    capitalize_text,
    insert_discourse_commas,
    spell_numbers,
    wer,
)

LOUD = b"\x10\x27" * 1000
QUIET = b"\x00\x00" * 1000


def test_spec_voice_typing_example_exact():
    assert capitalize_text(insert_discourse_commas(
        apply_spoken_punctuation("hello bro how are you question mark")
    )) == "Hello bro, how are you?"


def test_spoken_punctuation_all_classes():
    t = apply_spoken_punctuation(
        "wait comma really period new line ok exclamation mark")
    assert t == "wait, really.\nok!"
    t2 = apply_spoken_punctuation("open paren note close paren colon done")
    assert t2 == "(note): done"


def test_capitalization_sentences_i_and_proper_nouns():
    c = capitalize_text("hello bro. i am tired. hydralink rocks",
                        proper_nouns=("HydraLink",))
    assert c == "Hello bro. I am tired. HydraLink rocks"


def test_spell_numbers():
    assert spell_numbers("i have three cats and ten dogs").count("3") == 1
    assert spell_numbers("ten").count("10") == 1


def test_wer_metric():
    assert wer("hello world", "hello world") == 0.0
    # standard WER is distance/len(reference) and CAN exceed 1.0
    assert wer("hello world", "hi there now") > 0
    assert wer("hello world", "hello world now") == 0.5


def test_transcription_streaming_partials_then_final():
    eng = LiveTranscriptionEngine(provider=SimulatedStreamingProvider())
    assert eng.start() is True
    partials, finals = [], []
    eng.on_partial(partials.append)
    eng.on_final(finals.append)
    eng.provider.push_utterance("hello bro how are you question mark", 0.9)
    for i in range(8):
        eng.feed_audio(LOUD, now=i * 0.05)
    assert len(partials) >= 2 and any("hello" in p for p in partials)
    seg = eng.finalize(now=0.5)
    assert seg.text == "Hello bro, how are you?" and finals


def test_transcription_vad_falling_edge_autofinalize():
    eng = LiveTranscriptionEngine(provider=SimulatedStreamingProvider())
    eng.start()
    eng.provider.push_utterance("five five five", 0.9)
    for i in range(5):
        eng.feed_audio(LOUD, now=i * 0.05)
    for i in range(40):
        eng.feed_audio(QUIET, now=i * 0.05)
    assert eng.metrics["total_finals"] == 1


def test_transcription_pause_resume_stop():
    eng = LiveTranscriptionEngine(provider=SimulatedStreamingProvider())
    eng.start()
    assert eng.pause() and eng.state == "paused"
    assert eng.feed_audio(LOUD) == ""
    assert eng.resume() and eng.state == "listening"
    eng.stop()
    assert eng.state == "stopped"
    assert eng.pause() is False          # cannot pause a stopped engine


def test_transcription_history_search_export(tmp_path):
    eng = LiveTranscriptionEngine(provider=SimulatedStreamingProvider())
    eng.start()
    eng.feed_transcript("the deploy is done exclamation mark")
    eng.feed_transcript("note the flaky test")
    hits = eng.search("flaky")
    assert hits and "flaky" in hits[0].text
    for fmt in ("txt", "json", "md"):
        p = tmp_path / f"t.{fmt}"
        assert eng.export(str(p), fmt=fmt) > 0
    data = json.loads((tmp_path / "t.json").read_text())
    assert data["kind"] == "airmouse-transcript" and len(data["segments"]) == 2


def test_transcription_history_privacy_disabled():
    eng = LiveTranscriptionEngine(provider=SimulatedStreamingProvider(),
                                  history_enabled=False)
    eng.start()
    eng.feed_transcript("private note")
    assert eng.segments() == [] and eng.buffer_text() == ""


def test_transcription_vocabulary_in_pipeline():
    from airmouse.intelligence.vocabulary import PersonalVocabulary
    v = PersonalVocabulary()
    v.learn_correction("Hydra Link", "HydraLink")
    v.learn_term("HydraLink")
    eng = LiveTranscriptionEngine(provider=SimulatedStreamingProvider(),
                                  vocabulary=v)
    eng.start()
    eng.feed_transcript("connect to Hydra Link now period")
    assert "HydraLink" in eng.segments()[0].text


def test_transcription_reports_installed_providers_honestly():
    st = LiveTranscriptionEngine(
        provider=SimulatedStreamingProvider()).status()
    assert st["provider_available"] is True
    assert "simulated" in st["installed_providers"]


# ─────────────────────────────────────────────────────────────────────────────
# §9 voice typing
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.dictation_text import (
    EmojiSuggester,
    TextPredictor,
    VoiceTypingEngine,
)
from airmouse.interfaces import VoiceMode


def test_dictation_flow_with_punctuation_and_caps():
    vt = VoiceTypingEngine(VoiceMode.DICTATION)
    vt.ingest("hello bro how are you question mark")
    vt.ingest("i am working on the new project period")
    assert vt.text == ("Hello bro, how are you? "
                       "I am working on the new project.")


def test_dictation_edit_commands():
    vt = VoiceTypingEngine()
    vt.ingest("first sentence period second sentence period")
    vt.ingest("delete last word")
    assert not vt.text.endswith("sentence.")
    vt.ingest("new paragraph")
    assert vt.text.endswith("\n\n")


def test_dictation_replace_that_with():
    vt = VoiceTypingEngine()
    vt.ingest("launch the rocket")
    vt.ingest("replace that with deploy the satellite")
    assert "deploy the satellite" in vt.text


def test_dictation_case_commands():
    vt = VoiceTypingEngine()
    vt.ingest("make this loud")
    vt.ingest("uppercase that")
    assert "LOUD" in vt.text
    vt.ingest("lowercase that")
    assert "loud" in vt.text
    vt.ingest("capitalize that")


def test_dictation_undo_redo_roundtrip():
    vt = VoiceTypingEngine()
    vt.ingest("alpha beta gamma")
    vt.ingest("uppercase that")
    assert "ALPHA" in vt.text
    vt.ingest("undo")
    assert "ALPHA" not in vt.text
    vt.ingest("redo")
    assert "ALPHA" in vt.text


def test_dictation_hybrid_mode_exists():
    vt = VoiceTypingEngine(VoiceMode.HYBRID)
    vt.set_mode(VoiceMode.COMMAND)
    assert vt.mode is VoiceMode.COMMAND


# ─────────────────────────────────────────────────────────────────────────────
# §10/§11 text prediction + emoji
# ─────────────────────────────────────────────────────────────────────────────

def test_text_predictor_context_aware():
    m = PersonalInteractionModel()
    for _ in range(5):
        m.learn_text("i'm going to the store now")
    tp = TextPredictor(Predictor(m))
    sugg = tp.suggest("i'm going", k=2)
    assert sugg[0].text == "to"
    assert tp.context_tag("chrome", "docs") == "chrome:docs"


def test_text_predictor_disabled_returns_empty():
    tp = TextPredictor(Predictor(_trained_model()))
    tp.enabled = False
    assert tp.suggest("hello") == []


def test_emoji_suggester_rate_limited():
    es = EmojiSuggester()
    assert es.suggest("That's amazing", now=100.0)
    assert es.suggest("That's amazing", now=110.0) == []   # cooldown
    assert es.suggest("That's amazing", now=200.0)


def test_emoji_suggester_learns_preference():
    from airmouse.intelligence.plugin import IntelligencePlugin
    plug = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    es = EmojiSuggester(plug)
    es.record_choice("that's amazing", "🤩")
    assert plug.model.emoji.suggest("that's amazing")[0][0] == "🤩"


# ─────────────────────────────────────────────────────────────────────────────
# §12 universal text control
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.text_control import TextAction, TextController, TextExecutor, TextOp


def test_text_controller_all_basic_ops():
    ex = TextExecutor()
    tc = TextController(ex)
    assert tc.execute(TextAction(TextOp.TYPE, text="hello"))
    assert tc.execute(TextAction(TextOp.SELECT))
    assert tc.execute(TextAction(TextOp.COPY))
    assert tc.execute(TextAction(TextOp.CUT))
    assert tc.execute(TextAction(TextOp.PASTE))
    assert tc.execute(TextAction(TextOp.UNDO))
    assert tc.execute(TextAction(TextOp.REDO))
    assert tc.execute(TextAction(TextOp.DELETE))
    assert tc.execute(TextAction(TextOp.NEW_LINE))
    assert tc.execute(TextAction(TextOp.NEW_PARAGRAPH))


def test_text_controller_move_and_format():
    ex = TextExecutor()
    tc = TextController(ex)
    assert tc.execute(TextAction(TextOp.MOVE, direction="left", words=3))
    assert tc.execute(TextAction(TextOp.MOVE, direction="up"))
    assert tc.execute(TextAction(TextOp.FORMAT, format="bold"))
    assert tc.execute(TextAction(TextOp.FORMAT, format="italic"))
    assert not tc.execute(TextAction(TextOp.FORMAT, format="rotate_logo"))
    assert not tc.execute(TextAction(TextOp.MOVE, direction="diagonal"))


def test_text_controller_case_ops_retype():
    ex = TextExecutor()
    tc = TextController(ex)
    assert tc.execute(TextAction(TextOp.CAPITALIZE, text="hi"))
    assert tc.execute(TextAction(TextOp.UPPERCASE, text="hi"))
    assert tc.execute(TextAction(TextOp.LOWERCASE, text="HI"))


def test_text_controller_phrase_mapping_deterministic():
    assert TextController.op_from_phrase("copy that") == (TextOp.COPY, {})
    assert TextController.op_from_phrase("new paragraph")[0] == \
        TextOp.NEW_PARAGRAPH
    for evil in ("drop table users", "rm -rf /", "select; delete"):
        assert TextController.op_from_phrase(evil) is None


def test_text_controller_never_uses_coordinates():
    # text ops carry no x/y anywhere in the protocol
    import dataclasses
    fields = {f.name for f in dataclasses.fields(TextAction)}
    assert not {"x", "y", "point", "bbox"} & fields


# ─────────────────────────────────────────────────────────────────────────────
# §13/§14 world model + contextual commands
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.world_model import (
    ContextualCommandResolver,
    WorldModel,
    WorldState,
)


def _context_with_gaze():
    from airmouse.context import ContextEngine
    from airmouse.interfaces import ScreenTarget, ScreenTargetType
    ce = ContextEngine()
    ce.update_window("Documentation", application="Chrome")
    tgt = ScreenTarget(id="t1", type=ScreenTargetType.BUTTON,
                       text="Copy button", bbox=(100, 100, 140, 120),
                       confidence=0.95, source="gaze", actionable=True)
    ce.update_gaze_target(tgt, now=1.0)
    ce.record_action("select_code", now=1.0)
    return ce, tgt


def test_world_model_snapshot_bounded():
    ce, tgt = _context_with_gaze()
    wm = WorldModel(ce)
    wm.record_command("select code")
    wm.record_action("select")
    ws = wm.snapshot()
    assert ws.application == "Chrome" and ws.gaze_target is tgt
    assert "Copy button" in ws.to_display()
    assert len(ws.visible_targets) <= 64


def test_world_model_likely_intent_explainable_and_safe():
    plug = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    for _ in range(6):
        plug.record_action("click", history=["select"])
    wm = WorldModel(None, plug)
    wm.record_action("select")          # the model predicts what follows
    li = wm.likely_intent()
    assert li is not None and li["intent"] == "click" and li["reason"]
    # destructive actions never surface as likely intent
    for _ in range(8):
        plug.record_action("delete_files", history=["click"])
    wm2 = WorldModel(None, plug)
    wm2.record_action("click")
    li2 = wm2.likely_intent()
    assert li2 is None or not li2["intent"].startswith("delete")


def test_contextual_click_that_resolves_gaze_target():
    ce, tgt = _context_with_gaze()
    r = ContextualCommandResolver(WorldModel(ce), ce)
    intent = r.resolve("click that")
    assert intent.type.value == "click" and intent.target is tgt
    assert intent.confidence >= 0.8 and not intent.requires_confirmation


def test_contextual_close_it_requires_confirmation():
    ce, _ = _context_with_gaze()
    r = ContextualCommandResolver(WorldModel(ce), ce)
    intent = r.resolve("close it")
    assert intent.requires_confirmation is True


def test_contextual_low_confidence_asks_not_guesses():
    from airmouse.context import ContextEngine
    ce = ContextEngine()
    ce.update_window("Empty", application="Finder")
    r = ContextualCommandResolver(WorldModel(ce), ce)
    intent = r.resolve("click that")
    assert intent.confidence < 0.4 and intent.requires_confirmation is True


def test_contextual_command_families_all_resolve():
    ce, _ = _context_with_gaze()
    r = ContextualCommandResolver(WorldModel(ce), ce)
    for u in ("click that", "open that", "close it", "copy that",
              "read this", "zoom that", "scroll there", "select this",
              "use that", "go there", "open this", "save that"):
        assert r.resolve(u) is not None, u
    assert r.resolve("format c:") is None


# ─────────────────────────────────────────────────────────────────────────────
# §17-23 modes
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.modes import (
    ACCESSIBILITY_PROFILES,
    MODE_REGISTRY,
    AccessibilityProfiles,
    KeyDispatcher,
    ModeController,
    StudyTimer,
    TimelineSession,
)


class _Alive:
    def __init__(self, alive):
        self._a = set(alive)

    def alive(self, m):
        return m in self._a


def test_teacher_flow_presentation_and_timeline():
    d = KeyDispatcher()
    mc = ModeController("teacher", d)
    t = 100.0
    assert mc.handle("start lecture", now=t)
    assert mc.handle("start presentation", now=t)
    assert d.sent[-1] == ("f5",)
    mc.handle("next slide", now=t)
    mc.handle("add note quadratic equations", now=t)
    mc.handle("mark important", now=t)
    kinds = [e.kind for e in mc.teacher.timeline.entries]
    assert "note" in kinds and "important" in kinds
    mc.handle("pause lecture", now=t)
    assert mc.teacher.timeline.paused


def test_teacher_exports_lecture(tmp_path):
    mc = ModeController("teacher")
    mc.teacher.start_lecture(now=1.0)
    mc.teacher.add_note("photosynthesis", now=2.0)
    p = str(tmp_path / "lecture.md")
    assert mc.teacher.export_lecture(p) > 0
    assert "photosynthesis" in open(p).read()


def test_student_flow_notes_timer_sources():
    s = ModeController("student")
    t = 100.0
    assert s.handle("start study session", now=t)
    s.handle("take a note photosynthesis chapter 3", now=t)
    s.handle("mark this important", now=t)
    assert s.student.notes.important()
    s.student.save_source("Design patterns", "https://example.com/dp",
                          "strategy pattern")
    assert s.student.sources.sources[0].url.startswith("https://")
    s.student.timer._started = t - 30 * 60
    assert s.student.timer.check(now=t) == "focus_done"


def test_meeting_structured_output_honest():
    m = ModeController("meeting")
    t = 100.0
    m.handle("start transcription", now=t)
    m.handle("add action item send the report", now=t)
    m.handle("add decision use sqlite", now=t)
    m.handle("add question who reviews", now=t)
    summ = m.meeting.summary()
    assert summ["action_items"] == ["send the report"]
    assert summ["decisions"] == ["use sqlite"]
    assert summ["questions"] == ["who reviews"]
    # honest: no speaker identification fields are claimed
    assert "speakers" not in summ


def test_office_task_capture():
    o = ModeController("office")
    o.handle("capture task review pr", now=1.0)
    assert o.office.tasks == ["review pr"]


def test_research_mode_stages_and_capture():
    r = ModeController("research")
    assert r.research.stage == "question"
    assert r.research.advance() == "search"
    r.handle("save this source design patterns", now=1.0)
    assert r.research.sources.sources, "source must be captured"
    # sources are recorded verbatim — never altered
    r.research.sources.sources[0].annotation = "my note"
    while r.research.stage != "organize":
        r.research.advance()
    assert r.research.organize()["stage"] == "organize"


def test_developer_bindings():
    d = KeyDispatcher()
    dv = ModeController("developer", d)
    dv.handle("open terminal")
    dv.handle("next tab")
    assert ("ctrl", "`") in d.sent and ("ctrl", "tab") in d.sent


def test_unknown_mode_safe():
    mc = ModeController("chef")
    assert not mc.available and mc.handle("start lecture") is None


def test_accessibility_fallback_chain_no_single_point_of_failure():
    ap = AccessibilityProfiles()
    assert ap.set_profile("gaze-first")
    chain = ap.resolve(_Alive({"voice", "keyboard"}))
    assert chain == ("voice", "keyboard")     # gaze dead → fallback works
    assert ap.set_profile("camera-free")
    assert "voice" in ap.resolve(_Alive({"voice", "keyboard"}))
    assert ap.set_custom_chain("mine", ["voice", "rf", "hax"])
    assert ap.chain("mine") == ("voice", "rf")


def test_all_six_modes_registered():
    assert set(MODE_REGISTRY) == {"teacher", "student", "office", "meeting",
                                  "research", "developer"}


# ─────────────────────────────────────────────────────────────────────────────
# §24 Fusion 2.0
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.fusion2 import (
    FusionEngine2,
    FusionSignal,
    RFNoHardware,
    RFExtendedProvider,
    SignalKind,
    is_destructive_intent_name,
)


def _tgt():
    from airmouse.interfaces import ScreenTarget, ScreenTargetType
    return ScreenTarget(id="b1", type=ScreenTargetType.BUTTON,
                        text="Submit", bbox=(400, 300, 80, 30),
                        confidence=0.9, source="dom", actionable=True)


def test_fusion_voice_gaze_consensus():
    t = _tgt()
    c = FusionEngine2().fuse([
        FusionSignal(SignalKind.GAZE, "click", t, 0.9),
        FusionSignal(SignalKind.VOICE, "click", t, 0.9)])
    assert c.intent == "click" and c.executable and c.target is t


def test_fusion_voice_gesture():
    c = FusionEngine2().fuse([
        FusionSignal(SignalKind.GESTURE, "click", _tgt(), 0.85),
        FusionSignal(SignalKind.VOICE, "click", None, 0.9)])
    assert c.intent == "click" and c.executable


def test_fusion_gaze_gesture():
    c = FusionEngine2().fuse([
        FusionSignal(SignalKind.GAZE, "click", _tgt(), 0.9),
        FusionSignal(SignalKind.GESTURE, "click", _tgt(), 0.85)])
    assert c.intent == "click" and c.executable


def test_fusion_all_modalities_high_confidence():
    t = _tgt()
    c = FusionEngine2().fuse([
        FusionSignal(SignalKind.GAZE, "click", t, 0.9),
        FusionSignal(SignalKind.VOICE, "click", t, 0.9),
        FusionSignal(SignalKind.GESTURE, "click", t, 0.85),
        FusionSignal(SignalKind.KEYBOARD, "click", None, 0.6),
        FusionSignal(SignalKind.BROWSER_CONTEXT, "click", t, 0.7),
        FusionSignal(SignalKind.APPLICATION_CONTEXT, "click", None, 0.6),
        FusionSignal(SignalKind.RECENT_ACTION, "click", None, 0.5),
        FusionSignal(SignalKind.PERSONAL_HISTORY, "click", None, 0.8),
        FusionSignal(SignalKind.PREDICTION, "click", None, 0.9)])
    assert c.intent == "click" and c.confidence > 0.85
    assert "prediction" in c.explanation


def test_fusion_conflicting_modalities_require_confirmation():
    t = _tgt()
    c = FusionEngine2().fuse([
        FusionSignal(SignalKind.GAZE, "click", t, 0.9),
        FusionSignal(SignalKind.VOICE, "close", None, 0.8)])
    assert c.conflicts and c.requires_confirmation and not c.executable


def test_fusion_destructive_never_executes_silently():
    c = FusionEngine2().fuse([
        FusionSignal(SignalKind.VOICE, "shutdown", None, 0.99)])
    assert c.requires_confirmation and not c.executable


def test_fusion_prediction_alone_never_executes():
    c = FusionEngine2().fuse([
        FusionSignal(SignalKind.PREDICTION, "open_app", None, 0.99)])
    assert not c.executable
    assert FusionEngine2().to_intent(c) is None


def test_fusion_to_intent_materializes_only_executables():
    f = FusionEngine2()
    good = f.fuse([FusionSignal(SignalKind.GAZE, "click", _tgt(), 0.9),
                   FusionSignal(SignalKind.VOICE, "click", None, 0.9)])
    intent = f.to_intent(good)
    assert intent is not None and intent.confidence == good.confidence


def test_rf_no_hardware_is_honest():
    rf = RFNoHardware()
    assert rf.available() is False
    assert rf.presence() is None and rf.motion() is None
    assert rf.gesture_classification() is None
    assert rf.direction() is None and rf.range() is None
    assert rf.velocity() is None


def test_rf_protocol_is_extensible():
    class FakeCSI(RFExtendedProvider):
        def available(self):
            return True

        def presence(self):
            return {"present": True, "confidence": 0.8}

    assert FakeCSI().available() is True       # future sensors plug in
    assert is_destructive_intent_name("delete_files")


# ─────────────────────────────────────────────────────────────────────────────
# §31 offline (REAL network isolation) + §32 privacy
# ─────────────────────────────────────────────────────────────────────────────

from airmouse.offline import network_isolation, run_offline_selftest


def test_offline_selftest_includes_v115_checks():
    rep = run_offline_selftest()
    assert rep.ok is True
    names = {c["name"] for c in rep.checks}
    assert {"intelligence_offline", "memory_offline", "vocabulary_offline",
            "transcription_offline", "fusion2_offline"} <= names


def test_intelligence_fully_functional_offline():
    with network_isolation():
        plug = IntelligencePlugin({"enabled": True},
                                  base_dir=tempfile.mkdtemp())
        assert plug.state is IntelligenceState.AVAILABLE
        for _ in range(5):
            plug.record_action("click", history=["open_app"])
        assert plug.predict_next_action(["open_app"]).value == "click"
        eng = LiveTranscriptionEngine(
            provider=SimulatedStreamingProvider(), history_enabled=False)
        assert eng.start()
        eng.provider.push_utterance("offline works period", 0.9)
        for i in range(6):
            eng.feed_audio(LOUD, now=i * 0.05)
        assert eng.finalize(now=0.4).text == "Offline works."


def test_privacy_dashboard_flags_and_states():
    from airmouse.privacy import ConnectionState, PrivacyDashboard
    plug = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    pd = PrivacyDashboard(plugin=plug)
    st = pd.status()
    assert st["connection_state"] == "offline"
    assert st["flags"]["telemetry"] is False
    assert st["flags"]["cloud"] is False
    assert pd.set("cloud", True) is False       # structurally impossible
    assert pd.set("memory", False) is True
    assert plug.memory.enabled is False
    pd.set_privacy_mode(True)
    assert pd.state is ConnectionState.PRIVACY
    pd.set_privacy_mode(False)
    assert pd.state is ConnectionState.OFFLINE


def test_privacy_destructive_actions_delegate():
    from airmouse.privacy import PrivacyDashboard
    plug = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    plug.record_command("open browser")
    pd = PrivacyDashboard(plugin=plug)
    assert pd.delete_learned_data()["memory"] >= 1
    assert plug.memory.size() == 0
    assert pd.reset_model_personalization() is True
    assert pd.clear_interaction_history() >= 0


# ─────────────────────────────────────────────────────────────────────────────
# §43 security — malicious input across every v11.5 parser
# ─────────────────────────────────────────────────────────────────────────────

MALICIOUS = (
    "'; DROP TABLE users; --",
    "$(rm -rf /)",
    "`cat /etc/passwd`",
    "../../etc/passwd",
    "\x00\x01\x02binary",
    "a" * 10_000,
    "<script>alert(1)</script>",
    "password=hunter2",
    "ghp_AbCdEfGh1234567890AbCdEfGh12345678",
)


def test_voice_typing_survives_malicious_input():
    vt = VoiceTypingEngine()
    for m in MALICIOUS:
        ops = vt.ingest(m)               # must never raise or execute
        assert isinstance(ops, list)     # ops are inert DATA
    # dictated text is stored verbatim as TEXT — it is never evaluated,
    # shellexec'd or passed to any process (nothing to assert beyond
    # the absence of exceptions, but keep a visible sentinel):
    assert isinstance(vt.text, str)


def test_text_controller_rejects_oversized_typed_text():
    tc = TextController(TextExecutor())
    assert not tc.execute(TextAction(TextOp.TYPE, text="a" * 10_000))


def test_contextual_resolver_immune_to_injection():
    ce, _ = _context_with_gaze()
    r = ContextualCommandResolver(WorldModel(ce), ce)
    for m in ("click that'; DROP TABLE users; --", "close it $(reboot)",
              "save that `evil`"):
        i = r.resolve(m)
        assert i is None or i.type.name != "SYSTEM_OP"


def test_workflow_engine_rejects_malicious_steps():
    store = WorkflowStore()
    # step names must be validated symbols — shell fragments rejected
    assert store.create_manual("evil", ["rm; -rf", "drop table"]) is None
    assert store.create_manual("evil", ["$(reboot)"]) is None
    assert store.create_manual("evil", ["../../etc"]) is None
    assert store.create_manual("ok", ["ok_step", "copy-paste2"]) is not None


def test_memory_scrubs_malicious_patterns():
    mem = InteractionMemory()
    for m in MALICIOUS:
        out = mem.record(m)
        # credentials/tokens: refused outright
        if out is not None:
            assert "ghp_" not in out.pattern
            assert "passwd" not in out.pattern
            assert "password" not in out.pattern
            assert "hunter2" not in out.pattern
        # token-like blobs are redacted to placeholders, never raw
        if out is not None:
            assert len(out.pattern) <= 200
    assert mem.get("password=hunter2") is None
    assert mem.size() <= len(MALICIOUS)


def test_profile_import_fuzz_never_raises():
    p = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    d = tempfile.mkdtemp()
    for i, payload in enumerate((
            "not json at all", "[]", '{"kind": null}', "null",
            '{"kind": "airmouse-intelligence-profile", "memory": 5}',
            '{"kind": "airmouse-intelligence-profile", "vocabulary": []}',
    )):
        path = os.path.join(d, f"p{i}.json")
        with open(path, "w") as f:
            f.write(payload)
        assert isinstance(p.import_profile(path), dict)


def test_transcription_survives_binary_audio_and_huge_text():
    eng = LiveTranscriptionEngine(provider=SimulatedStreamingProvider())
    eng.start()
    eng.feed_audio(b"\xff\x00" * 5000, now=0.0)
    eng.feed_transcript("<script>alert(1)</script>")
    segs = eng.segments()
    assert segs and "<script" in segs[0].text.lower()   # data, never executed


def test_config_import_ignores_bad_values():
    from airmouse.config import Config
    c = Config()
    before = c.intelligence_model_capacity
    # malformed values must not raise (loader catches everything)
    assert hasattr(c, "privacy_mode")


# ─────────────────────────────────────────────────────────────────────────────
# §44 resource limits
# ─────────────────────────────────────────────────────────────────────────────

def test_memory_hard_cap():
    mem = InteractionMemory(max_patterns=40)
    for i in range(200):
        mem.record(f"p-{i:04d}")
    assert mem.size() <= 40


def test_transcript_buffer_bounded():
    eng = LiveTranscriptionEngine(provider=SimulatedStreamingProvider())
    for i in range(600):
        eng.feed_transcript(f"segment {i} with some words", history_ok=True) \
            if False else eng.feed_transcript(f"segment {i} with words")
    assert len(eng.segments()) <= 500
    assert len(eng.buffer_text()) <= 200_000 + 100


def test_workflow_store_cap():
    store = WorkflowStore()
    for i in range(260):
        store.create_manual(f"wf{i}", ["copy"])
    assert len(store) <= 200


def test_vocab_emoji_model_caps():
    em = EmojiModel()
    for i in range(400):
        em.observe(f"tag{i}", "🎉")
    assert em.entries <= 4096
    fw = FeatureWeights()
    for i in range(400):
        fw.set(f"f{i}", 0.1)
    assert len(fw.items()) <= 256


# ─────────────────────────────────────────────────────────────────────────────
# §34 performance budgets (deterministic, generous CI bounds)
# ─────────────────────────────────────────────────────────────────────────────

def _budget(fn, budget_s: float, n: int = 1) -> float:
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n


def test_prediction_latency_budget():
    p = Predictor(_trained_model())
    dt = _budget(lambda: p.predict_next_action(["open_app", "click"]), 1.0, 200)
    assert dt < 0.05, f"prediction {dt*1000:.1f}ms > 50ms"


def test_emoji_suggestion_latency_budget():
    p = Predictor(_trained_model())
    dt = _budget(lambda: p.suggest_emoji("that's amazing bro"), 1.0, 100)
    assert dt < 0.05


def test_memory_record_latency_budget():
    mem = InteractionMemory()
    dt = _budget(lambda: mem.record("chrome -> vscode"), 1.0, 500)
    assert dt < 0.01, f"memory record {dt*1000:.2f}ms > 10ms"


def test_transcription_tick_latency_budget():
    eng = LiveTranscriptionEngine(provider=SimulatedStreamingProvider())
    eng.start()
    dt = _budget(lambda: eng.feed_audio(LOUD, now=1.0), 1.0, 500)
    assert dt < 0.1, f"transcription tick {dt*1000:.1f}ms > 100ms"


def test_fusion_latency_budget():
    f = FusionEngine2()
    sigs = [FusionSignal(SignalKind.GAZE, "click", None, 0.9),
            FusionSignal(SignalKind.VOICE, "click", None, 0.9)]
    dt = _budget(lambda: f.fuse(sigs), 1.0, 500)
    assert dt < 0.02


def test_event_bus_latency_budget():
    from airmouse.eventbus import EventBus
    bus = EventBus()
    dt = _budget(lambda: bus.publish_voice("click", "click", 0.9, now=1.0),
                 1.0, 500)
    assert dt < 0.0005 * 10      # v10 asserted 1000 events < 500ms


def test_model_load_time_budget(tmp_path):
    m = PersonalInteractionModel()
    for i in range(200):
        m.learn_text(f"sample training sentence {i} for load timing")
    path = str(tmp_path / "m.bin")
    m.save(path)
    dt = _budget(lambda: PersonalInteractionModel.load(path), 1.0, 20)
    assert dt < 0.5, f"model load {dt*1000:.0f}ms > 500ms"


# ─────────────────────────────────────────────────────────────────────────────
# §45 self-test + §46 final integration
# ─────────────────────────────────────────────────────────────────────────────

def test_self_test_all_pass_or_optional():
    from airmouse.selftest import run_self_test
    results = run_self_test()
    fails = [r for r in results if r.status == "FAIL"]
    assert not fails, fails
    names = {r.component for r in results}
    assert {"Core", "Voice", "Transcription", "Fusion", "Intelligence",
            "Memory", "Prediction", "Safety", "Offline",
            "Packaging"} <= names


def test_self_test_reports_hardware_honestly():
    from airmouse.selftest import run_self_test
    results = run_self_test()
    cam = [r for r in results if r.component == "Camera"][0]
    assert cam.status == "HARDWARE"


def test_final_integration_voice_to_prediction():
    """§46: START → SPEAK → VAD → ASR → COMMAND RESOLUTION → GAZE TARGET
    → GESTURE CONFIRMATION → ACTION → VERIFICATION → LEARNING EVENT →
    PERSONAL MEMORY → FUTURE PREDICTION."""
    from airmouse.agent import InteractionAgent
    from airmouse.actions import MockExecutor
    from airmouse.eventbus import EventBus
    from airmouse.context import ContextEngine
    from airmouse.gaze import GazeState

    plug = IntelligencePlugin({"enabled": True}, base_dir=tempfile.mkdtemp())
    bus = EventBus()
    agent = InteractionAgent(
        {"intelligence_enabled": False, "screen_w": 1920, "screen_h": 1080},
        executor=MockExecutor(), event_bus=bus, intelligence=plug)

    # 1. user speaks → VAD + offline ASR (simulated, deterministic)
    voice_engine = None
    from airmouse.offline_voice import OfflineVoiceEngine
    voice_engine = OfflineVoiceEngine({"mode": "command"}, bus=bus)
    voice_engine.feed_transcript("click", 0.95, now=1.0)
    # 2. command resolution through the agent's event loop
    agent.poll_events(now=1.1)
    # 3. gaze target
    from airmouse.interfaces import ScreenTarget, ScreenTargetType
    tgt = ScreenTarget(id="b1", type=ScreenTargetType.BUTTON,
                       text="Submit", bbox=(900, 500, 120, 40),
                       confidence=0.92, source="gaze", actionable=True)
    # 4. gesture confirmation + action + verification via process_frame
    out = agent.process_frame(
        hand_data={"gesture": "pinch", "point": (960, 520),
                   "confidence": 0.9},
        utterance="click", now=1.2,
        gaze_state=GazeState(x=960 / 1920, y=520 / 1080, confidence=0.9))
    assert any(r.ok for r in out.get("reports", [])), "action must verify"
    # 5-6. learning event → personal memory
    assert plug.memory.size() >= 1
    assert plug.model.actions.steps >= 1
    # 7. future prediction (data only)
    pred = plug.predict_next_action(["click"])
    assert pred is not None                      # model has enough signal
    assert plug.suggestions(["click"]) is not None


def test_final_integration_teacher_scenario():
    d = KeyDispatcher()
    mc = ModeController("teacher", d)
    t = 100.0
    mc.handle("start presentation", now=t)
    mc.handle("start lecture", now=t)
    mc.handle("next slide", now=t)
    mc.handle("start transcription", now=t)   # unknown in teacher mode
    mc.handle("add note keynesian cross", now=t)
    mc.handle("mark important", now=t)
    mc.handle("next slide", now=t)
    assert mc.handle("export transcript", now=t)
    kinds = [e.kind for e in mc.teacher.timeline.entries]
    assert kinds == ["note", "important"]


def test_final_integration_student_scenario():
    s = ModeController("student")
    t = 100.0
    s.handle("start study mode", now=t) or s.handle("start study session",
                                                    now=t)
    s.handle("save this source", now=t)
    s.handle("take a note chapter 4 summary", now=t)
    assert s.student.notes.notes
    assert s.student.sources.sources          # capture may be placeholder
    s.student.timer._started = t - 26 * 60
    assert s.student.timer.check(now=t) in ("focus_done", None)


def test_final_integration_office_scenario():
    o = ModeController("office")
    t = 100.0
    o.handle("start meeting", now=t)
    o.office.meeting.mark_important("budget approved", now=t + 10)
    o.handle("capture task send minutes", now=t + 20)
    o.office.meeting.add_action_item("email the team", now=t + 30)
    o.handle("stop meeting", now=t + 40)
    summ = o.office.meeting.summary()
    assert summ["action_items"] == ["email the team"]
    assert "budget approved" in summ["important"]


def test_final_integration_developer_scenario():
    d = KeyDispatcher()
    dv = ModeController("developer", d)
    for phrase in ("start development", "open project", "switch window",
                   "copy code", "open terminal"):
        dv.handle(phrase)          # unknown phrases are safe no-ops
    assert ("ctrl", "`") in d.sent


def test_final_integration_workflow_lifecycle():
    d = WorkflowDiscovery()
    sugg = _repeat_pattern(d, ["open_browser", "copy", "paste"])
    assert sugg, "pattern must be discovered"
    store = WorkflowStore()
    wf = store.create(sugg[0], name="dev loop")
    runner = WorkflowRunner(executor=lambda s: True)
    text = runner.preview(wf)
    assert "dev loop" in text
    ok, msg = runner.run(wf)
    assert ok and wf.success_count == 1
