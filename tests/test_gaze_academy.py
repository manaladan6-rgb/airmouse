"""
tests.test_gaze_academy — v16.5 GAZE ACADEMY test battery.

Honesty is load-bearing: this suite runs 100% headless (no camera in the
sandbox) and asserts the honesty contract itself — nothing physical is
ever auto-passed, the headless plan says so in plain words, simulated
runs are always labeled SIMULATED, and only REAL (scripted camera)
samples can pass a live lesson with ``simulated: False``.

Isolation: every disk-touching test points ``$AIRMOUSE_HOME`` at a fresh
tmp dir (monkeypatch); the real ~/.airmouse is never touched.
"""

from __future__ import annotations

import io
import json
import os

import pytest

from airmouse import gaze_academy as ga
from airmouse import paths

# ---------------------------------------------------------------------------
# helpers — synthetic sample builders (explicit timestamps, no sleeps)
# ---------------------------------------------------------------------------

CENTRE = {"x": 0.5, "y": 0.5, "r": 0.1}          # GAZE_DEFAULT_REGION


def sample(t, x=0.5, y=0.5, eye_closed=False, conf=0.9, hand=None):
    d = {"t": t, "x": x, "y": y, "eye_closed": eye_closed,
         "confidence": conf}
    if hand is not None:
        d["hand_confirmed"] = hand
    return d


def far(t, **kw):
    """A sample clearly OUTSIDE the centre region."""
    return sample(t, x=0.05, y=0.05, **kw)


def stream(ts, **kw):
    return [sample(t, **kw) for t in ts]


# ---------------------------------------------------------------------------
# 1. curriculum — the exact five mission lessons, honestly specified
# ---------------------------------------------------------------------------

EXPECTED_IDS = ["l1_acquire", "l2_fixation", "l3_dwell", "l4_blink",
                "l5_eye_assist"]
EXPECTED_TITLES = {
    "l1_acquire": "Look at the circle.",
    "l2_fixation": "Look at the target. Hold your gaze.",
    "l3_dwell": "Look → hold → activate",
    "l4_blink": "Look at target. Blink.",
    "l5_eye_assist": "Look at target. Use your hand to confirm.",
}
EXPECTED_METRICS = {
    "l1_acquire": ["acquisition_time_s", "stability", "confidence",
                   "jitter"],
    "l2_fixation": ["hold_duration_s", "drift"],
    "l3_dwell": ["dwell_threshold_s", "activations"],
    "l4_blink": ["blink_detected", "gaze_lock_during_blink"],
    "l5_eye_assist": ["gaze_target_locked", "confirm_observed"],
}


def test_curriculum_is_exact_five_mission_lessons():
    assert ga.lesson_ids() == EXPECTED_IDS
    assert [l["id"] for l in ga.GAZE_LESSONS] == EXPECTED_IDS
    for lesson in ga.GAZE_LESSONS:
        assert lesson["title"] == EXPECTED_TITLES[lesson["id"]]
        assert lesson["metrics"] == EXPECTED_METRICS[lesson["id"]]


def test_lessons_are_honest_and_gentle():
    for lesson in ga.GAZE_LESSONS:
        # physical honesty: every lesson needs a camera and eyes
        assert "camera + eyes" in lesson["requires"], lesson["id"]
        assert lesson["instruction"].strip()
        assert lesson["success_criteria"], lesson["id"]
        assert lesson["success_criteria_text"].strip()
        # 2-3 gentle tips, never blaming
        assert 2 <= len(lesson["tips"]) <= 3, lesson["id"]
        for tip in lesson["tips"]:
            assert tip.strip()
        # l5 teaches the Eye Assist two-step (eyes select, hand confirms)
    l5 = ga._LESSON_BY_ID["l5_eye_assist"]
    assert "hand" in l5["requires"]
    assert "CONFIRM" in l5["instruction"].upper()


def test_gaze_academy_plan_returns_copies_and_filters():
    plan = ga.gaze_academy_plan()
    assert [l["id"] for l in plan] == EXPECTED_IDS
    plan[0]["title"] = "MUTATED"
    plan.pop()
    assert ga.GAZE_LESSONS[0]["title"] == EXPECTED_TITLES["l1_acquire"]
    assert len(ga.GAZE_LESSONS) == 5
    single = ga.gaze_academy_plan("l3_dwell")
    assert [l["id"] for l in single] == ["l3_dwell"]
    assert ga.gaze_academy_plan("nope") == []
    assert ga.gaze_academy_plan(None) == ga.gaze_academy_plan("all")


def test_sample_schema_is_documented_for_the_teacher_module():
    for field in ("t", "x", "y", "eye_closed", "confidence",
                  "hand_confirmed"):
        assert field in ga.GAZE_SAMPLE_SCHEMA
        assert isinstance(ga.GAZE_SAMPLE_SCHEMA[field], str)
        assert ga.GAZE_SAMPLE_SCHEMA[field].strip()
    # the default target region is exported too
    assert ga.gaze_in_region({"x": 0.5, "y": 0.5}, ga.GAZE_DEFAULT_REGION)


def test_bounds_dict_exported():
    assert ga.BOUNDS["preferred_dwell_s"] == (0.3, 2.0)
    for key in ("calibration_quality", "jitter", "acquisition_s"):
        lo, hi = ga.BOUNDS[key]
        assert lo < hi
    assert ga.BOUNDS["dominant_regions_max"] == 8


# ---------------------------------------------------------------------------
# 2. metric math (synthetic, deterministic)
# ---------------------------------------------------------------------------

def test_acquisition_time_enters_and_stays():
    # outside until t=1.0, then inside and staying
    samples = [far(0.0), far(0.5),
               sample(1.0), sample(1.6), sample(2.2)]
    # proof moment = t_enter + min_hold − t_start = 1.0 + 0.5 − 0.0
    assert ga.acquisition_time(samples, CENTRE, min_hold=0.5) == \
        pytest.approx(1.5)


def test_acquisition_time_bounce_out_returns_none():
    # enters at 1.0 but leaves at 1.4 (< min_hold 0.5), never returns
    samples = [far(0.0), sample(1.0), sample(1.25), far(1.4), far(3.0)]
    assert ga.acquisition_time(samples, CENTRE, min_hold=0.5) is None


def test_acquisition_time_now_fn_extends_trailing_hold():
    # trailing run: inside from 1.8, recording ends at 2.0
    samples = [far(0.0), sample(1.8), sample(2.0)]
    # without an external clock the trailing evidence (0.2 s) is too short
    assert ga.acquisition_time(samples, CENTRE, min_hold=0.5) is None
    # with now_fn reaching 2.4 the hold (2.4-1.8=0.6 ≥ 0.5) is credited
    got = ga.acquisition_time(samples, CENTRE, min_hold=0.5,
                              now_fn=lambda: 2.4)
    assert got == pytest.approx(1.8 + 0.5 - 0.0)


def test_acquisition_time_now_fn_too_early_returns_none():
    samples = [far(0.0), sample(1.8), sample(2.0)]
    assert ga.acquisition_time(samples, CENTRE, min_hold=0.5,
                               now_fn=lambda: 2.2) is None


def test_jitter_zero_for_constant_point():
    samples = stream([0.0, 0.2, 0.4, 0.6, 0.8])     # parked on the centre
    assert ga.jitter_score(samples) == pytest.approx(0.0)


def test_jitter_positive_for_noisy_points():
    samples = []
    for i, t in enumerate([0.0, 0.2, 0.4, 0.6]):
        x = 0.5 if i % 2 == 0 else 0.52             # 0.02 px jumps
        samples.append(sample(t, x=x))
    assert ga.jitter_score(samples) == pytest.approx(0.02)


def test_fixation_hold_longest_run():
    samples = [far(0.0),
               sample(1.0), sample(1.8), far(2.0),      # run A: 0.8 s
               sample(2.5), sample(3.0), sample(3.5),
               sample(4.0), far(5.0)]                   # run B: 1.5 s
    assert ga.fixation_hold(samples, CENTRE) == pytest.approx(1.5)


def test_fixation_hold_empty_and_never_in_region():
    assert ga.fixation_hold([], CENTRE) is None
    assert ga.fixation_hold("garbage", CENTRE) is None
    # valid samples that never enter the region → 0.0 (not None)
    assert ga.fixation_hold([far(0.0), far(1.0)], CENTRE) == 0.0


def test_dwell_verified_exactly_threshold_counts():
    # continuous in-region evidence from 0.0 to exactly 1.0
    assert ga.dwell_verified(stream([0.0, 0.5, 1.0]), CENTRE, 1.0) is True


def test_dwell_verified_just_under_fails():
    assert ga.dwell_verified(stream([0.0, 0.5, 0.999]), CENTRE, 1.0) is False
    # broken runs do not accumulate across the gap
    samples = [sample(0.0), far(0.4), sample(0.8), far(1.2), sample(1.6)]
    assert ga.dwell_verified(samples, CENTRE, 1.0) is False


def test_stability_ratio_fraction_of_time():
    samples = [far(0.0), sample(1.0), sample(2.0), sample(3.0), far(4.0)]
    # inside for 2.0 s of a 4.0 s recording
    assert ga.stability_ratio(samples, CENTRE, min_hold=0.0) == \
        pytest.approx(0.5)


def test_stability_ratio_min_hold_filters_short_runs():
    samples = [far(0.0), sample(1.0), far(1.2),        # 0.2 s run (ignored)
               sample(2.0), sample(4.0), far(5.0)]     # 2.0 s run counts
    assert ga.stability_ratio(samples, CENTRE, min_hold=0.5) == \
        pytest.approx(2.0 / 5.0)
    assert ga.stability_ratio([], CENTRE, 0.0) == 0.0


def test_blink_events_onset_offset_pairs():
    samples = [
        sample(0.9, eye_closed=False),
        sample(1.0, eye_closed=True),
        sample(1.1, eye_closed=True),
        sample(1.2, eye_closed=False),
        sample(2.0, eye_closed=True),
        sample(2.15, eye_closed=False),
    ]
    assert ga.blink_events(samples) == [(1.0, 1.2), (2.0, 2.15)]


def test_blink_events_unterminated_episode_ignored():
    # still closed at the end of the recording — no end is fabricated
    samples = [sample(0.0, eye_closed=False), sample(1.0, eye_closed=True),
               sample(1.3, eye_closed=True)]
    assert ga.blink_events(samples) == []
    assert ga.blink_events([]) == []


def test_blink_events_ear_proxy():
    samples = [{"t": 0.5, "ear": 0.30}, {"t": 0.9, "ear": 0.05},
               {"t": 1.2, "ear": 0.28}]
    assert ga.blink_events(samples) == [(0.9, 1.2)]


def test_blink_gaze_lock_true_and_false():
    steady = [sample(0.9), sample(1.0, eye_closed=True),
              sample(1.2)]
    assert ga.blink_gaze_lock(steady, CENTRE) is True
    # gaze jumps off-target right after the blink → not locked
    jumper = [sample(0.9), sample(1.0, eye_closed=True), far(1.2)]
    assert ga.blink_gaze_lock(jumper, CENTRE) is False
    # no blink at all → False
    assert ga.blink_gaze_lock(stream([0.0, 0.5]), CENTRE) is False


def test_drift_and_mean_confidence():
    parked = stream([0.0, 0.5, 1.0])                    # dead on centre
    assert ga.drift_score(parked, CENTRE) == pytest.approx(0.0)
    assert ga.mean_confidence(parked) == pytest.approx(0.9)
    off = [sample(0.0, x=0.55, y=0.55)]                 # ~0.0707 off-centre
    assert ga.drift_score(off, CENTRE) == pytest.approx(0.070710678, rel=1e-5)
    assert ga.mean_confidence([{"t": 0.0, "x": None, "y": None,
                                "confidence": 0.9}]) == 0.0


def test_activation_runs_counts_threshold_crossings():
    samples = [sample(0.0), far(1.0), sample(2.0), sample(3.0), far(3.5)]
    # runs: 0.0→0.0 (0.0 s) and 2.0→3.0 (1.0 s)
    assert ga.activation_runs(samples, CENTRE, 0.8) == 1
    assert ga.activation_runs(samples, CENTRE, 0.3) == 1
    assert ga.activation_runs([], CENTRE, 0.8) == 0


def test_region_tuple_and_dict_equivalent():
    samples = stream([0.0, 0.5, 1.0])
    as_dict = ga.fixation_hold(samples, {"x": 0.5, "y": 0.5, "r": 0.1})
    as_tuple = ga.fixation_hold(samples, (0.5, 0.5, 0.1))
    assert as_dict == as_tuple == pytest.approx(1.0)
    # garbage region spec falls back to the centre circle
    assert ga.fixation_hold(samples, {"nope": 1}) == as_dict


# ---------------------------------------------------------------------------
# 3. empty / garbage input safety — never raise, honest defaults
# ---------------------------------------------------------------------------

GARBAGE_INPUTS = [
    [],
    (),
    [None],
    [{}],
    [42, "string", 3.14],
    [{"t": "not-a-number"}],
    [{"t": 1.0, "x": "bad", "y": None, "confidence": "worse"}],
    [{"t": float("nan"), "x": 0.5, "y": 0.5}],
    [{"t": 1.0, "x": float("inf"), "y": 0.5, "confidence": 0.9}],
    ["garbage"],
]


@pytest.mark.parametrize("bad", GARBAGE_INPUTS)
def test_metrics_safe_on_empty_and_garbage(bad):
    assert ga.acquisition_time(bad, CENTRE) is None
    assert ga.jitter_score(bad) == 0.0
    assert ga.stability_ratio(bad, CENTRE, 0.5) == 0.0
    assert ga.fixation_hold(bad, CENTRE) is None
    assert ga.dwell_verified(bad, CENTRE, 1.0) is False
    assert ga.blink_events(bad) == []
    assert ga.blink_gaze_lock(bad, CENTRE) is False
    assert ga.drift_score(bad, CENTRE) == 0.0
    assert ga.mean_confidence(bad) == 0.0
    assert ga.activation_runs(bad, CENTRE, 0.8) == 0
    assert ga.gaze_in_region(bad, CENTRE) is False


@pytest.mark.parametrize("lid", EXPECTED_IDS)
def test_lesson_metrics_and_grading_garbage_safe(lid):
    for bad in GARBAGE_INPUTS:
        m = ga.lesson_metrics(lid, bad)
        assert isinstance(m, dict)
        assert ga.lesson_passed(lid, m) is False
    assert ga.lesson_metrics("unknown_lesson", stream([0.0])) == {}
    assert ga.lesson_passed("unknown_lesson", {"hold_duration_s": 99}) is False
    assert ga.lesson_passed("l2_fixation", None) is False


# ---------------------------------------------------------------------------
# 4. per-lesson metrics + grading
# ---------------------------------------------------------------------------

def test_all_declared_metrics_are_produced_by_lesson_metrics():
    rich = stream([0.0, 0.3, 0.6, 0.9, 1.2], hand=True) \
        + [sample(1.3, eye_closed=True), sample(1.4)]
    for lesson in ga.GAZE_LESSONS:
        m = ga.lesson_metrics(lesson["id"], rich)
        for name in lesson["metrics"]:
            assert name in m, (lesson["id"], name)


def test_lesson_passed_criteria_boundary():
    # l2: exactly 1.0 s holds, 0.9 s does not
    assert ga.lesson_passed("l2_fixation",
                            {"hold_duration_s": 1.0}) is True
    assert ga.lesson_passed("l2_fixation",
                            {"hold_duration_s": 0.9}) is False
    # l4 needs the lock too — a blink without gaze lock never passes
    assert ga.lesson_passed("l4_blink", {"blink_detected": 1,
                                         "gaze_lock_during_blink": False}) \
        is False
    assert ga.lesson_passed("l4_blink", {"blink_detected": 1,
                                         "gaze_lock_during_blink": True}) \
        is True
    # l5 needs BOTH the lock and the confirm
    assert ga.lesson_passed("l5_eye_assist",
                            {"gaze_target_locked": True,
                             "confirm_observed": False}) is False


# ---------------------------------------------------------------------------
# 5. GazeLearner — bounded local personalization
# ---------------------------------------------------------------------------

@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Fresh AIRMOUSE_HOME — the real ~/.airmouse is never touched."""
    h = tmp_path / "airmouse-home"
    monkeypatch.setenv("AIRMOUSE_HOME", str(h))
    return h


def test_learner_roundtrip_via_airmouse_home(home):
    learner = ga.GazeLearner()
    assert learner.record_lesson(
        "l3_dwell", {"dwell_threshold_s": 0.8, "activations": 2,
                     "longest_hold_s": 0.9, "passed": True},
        verified=True) is True
    learner.record_usage(dwell_used_s=0.7, region="browser_tabs")
    learner.record_usage(dwell_used_s=0.8)
    assert learner.save() is True
    expected = home / "profile" / "gaze.json"
    assert expected.is_file()
    with open(expected, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["schema_version"] == 1
    for key in ("calibration_quality", "preferred_dwell_s", "jitter",
                "acquisition_s", "dominant_regions", "updated_at"):
        assert key in on_disk

    again = ga.GazeLearner()
    snap = again.snapshot()
    # EMA over the two verified dwell observations: 0.9 then 0.7 → 0.8
    assert snap["preferred_dwell_s"] == pytest.approx(0.8)
    assert snap["dominant_regions"] == ["browser_tabs"]
    assert snap["verified_lessons"]["l3_dwell"]["attempts"] == 1
    assert snap["verified_lessons"]["l3_dwell"]["passes"] == 1
    assert snap["updated_at"]
    # and the suggestion survives the roundtrip
    assert "preferred_dwell_s" in again.suggest()


def test_learner_dwell_bounds_high(home):
    learner = ga.GazeLearner()
    learner.record_usage(dwell_used_s=5.0)
    assert learner.snapshot()["preferred_dwell_s"] == pytest.approx(2.0)


def test_learner_dwell_bounds_low(home):
    learner = ga.GazeLearner()
    learner.record_usage(dwell_used_s=-1)
    assert learner.snapshot()["preferred_dwell_s"] == pytest.approx(0.3)


def test_learner_other_float_params_clamped(home):
    learner = ga.GazeLearner()
    learner.record_lesson("l1_acquire",
                          {"acquisition_time_s": 999.0, "jitter": -3.0},
                          verified=True)
    snap = learner.snapshot()
    assert snap["acquisition_s"] == pytest.approx(ga.BOUNDS["acquisition_s"][1])
    assert snap["jitter"] == pytest.approx(ga.BOUNDS["jitter"][0])
    assert learner.record_calibration(7.0) is True
    assert snap is not learner.snapshot()          # snapshot is a copy
    assert learner.snapshot()["calibration_quality"] == pytest.approx(1.0)
    assert learner.record_calibration(None) is False


def test_learner_region_list_trimmed_to_eight(home):
    learner = ga.GazeLearner()
    for i in range(12):
        learner.record_usage(region=f"r{i:02d}")
    snap = learner.snapshot()
    assert len(snap["dominant_regions"]) == ga.BOUNDS["dominant_regions_max"]
    assert "r00" in snap["dominant_regions"]
    assert "r08" not in snap["dominant_regions"]
    # counts keep ranking honest: a twice-seen region outranks singles
    learner.record_usage(region="r09")
    learner.record_usage(region="r09")
    assert learner.snapshot()["dominant_regions"][0] == "r09"


def test_learner_corrupt_file_defaults_and_flag(home):
    gaze_json = home / "profile" / "gaze.json"
    gaze_json.parent.mkdir(parents=True)
    gaze_json.write_text("{ this is not json", encoding="utf-8")
    learner = ga.GazeLearner()
    assert learner.corrupted_last_load is True
    snap = learner.snapshot()
    assert snap["preferred_dwell_s"] is None
    assert snap["dominant_regions"] == []
    assert snap["verified_lessons"] == {}
    # a *valid but wrong-typed* store also degrades to defaults
    gaze_json.write_text("[1, 2, 3]", encoding="utf-8")
    learner2 = ga.GazeLearner()
    assert learner2.corrupted_last_load is True
    assert learner2.snapshot()["preferred_dwell_s"] is None
    # missing file is NOT corruption
    gaze_json.unlink()
    learner3 = ga.GazeLearner()
    assert learner3.corrupted_last_load is False


def test_learner_corrupt_then_save_recovers(home):
    gaze_json = home / "profile" / "gaze.json"
    gaze_json.parent.mkdir(parents=True)
    gaze_json.write_text("garbage", encoding="utf-8")
    learner = ga.GazeLearner()
    assert learner.corrupted_last_load is True
    learner.record_usage(dwell_used_s=0.8)
    assert learner.save() is True                  # overwrites the junk
    fresh = ga.GazeLearner()
    assert fresh.corrupted_last_load is False
    assert fresh.snapshot()["preferred_dwell_s"] == pytest.approx(0.8)


def test_suggestions_change_with_verified_observations(home):
    learner = ga.GazeLearner()
    assert "preferred_dwell_s" not in learner.suggest()
    for hold in (0.7, 0.7, 0.7):
        learner.record_lesson("l3_dwell",
                              {"dwell_threshold_s": 0.8, "activations": 1,
                               "longest_hold_s": hold, "passed": True},
                              verified=True)
    s = learner.suggest()
    assert s["preferred_dwell_s"] == pytest.approx(0.7)
    assert "dwell" in s["reason"]
    # §17: a proposal never applies itself — snapshot unchanged
    assert learner.snapshot()["preferred_dwell_s"] == pytest.approx(0.7)


def test_suggestions_ignore_simulated_observations(home):
    learner = ga.GazeLearner()
    for _ in range(5):
        learner.record_lesson("l3_dwell",
                              {"dwell_threshold_s": 0.8, "activations": 3,
                               "longest_hold_s": 5.0, "passed": True},
                              verified=False)      # SIMULATED
    s = learner.suggest()
    assert "preferred_dwell_s" not in s            # never from simulated
    assert learner.snapshot()["preferred_dwell_s"] is None
    assert learner.snapshot()["simulated_lessons"]["l3_dwell"]["attempts"] == 5
    assert learner.snapshot()["simulated_lessons"]["l3_dwell"][
        "simulated"] is True
    assert "reason" in s


def test_save_false_when_home_is_a_file(home):
    home.mkdir(parents=True, exist_ok=True)
    blocker = home / "not-a-dir"
    blocker.write_text("i am a file", encoding="utf-8")
    os.environ["AIRMOUSE_HOME"] = str(blocker)     # resolved dynamically
    try:
        learner = ga.GazeLearner()                 # load fails gracefully
        assert learner.corrupted_last_load is False   # missing, not corrupt
        learner.record_usage(dwell_used_s=0.8)
        assert learner.save() is False             # never raises
    finally:
        os.environ["AIRMOUSE_HOME"] = str(home)
    assert ga.GazeLearner().save() is True         # home restored


def test_record_lesson_unknown_id_false(home):
    learner = ga.GazeLearner()
    assert learner.record_lesson("not_a_lesson", {"anything": 1}) is False
    assert learner.record_lesson(None, None) is False
    assert learner.snapshot()["verified_lessons"] == {}


# ---------------------------------------------------------------------------
# 6. run_gaze_academy — headless honesty
# ---------------------------------------------------------------------------

HONEST_LINE = ("PHYSICAL PRACTICE REQUIRED — needs camera + eyes. "
               "I can teach you the concepts now. Physical camera "
               "lessons will begin when a webcam is available.")


def test_headless_honest_plan():
    res = ga.run_gaze_academy()
    assert res["physical_required"] is True
    assert res["completed"] is False
    assert res["rc"] == 0
    assert res["simulated"] is False
    assert sorted(res["lessons"]) == sorted(EXPECTED_IDS)
    for lid, entry in res["lessons"].items():
        assert entry["passed"] is False
        assert entry["metrics"] == {}
        assert entry["simulated"] is False
    joined = "\n".join(res["output"])
    assert HONEST_LINE in joined
    # every lesson rendered with what it will measure + the real
    # calibration pointer
    assert "acquisition_time_s, stability, confidence, jitter" in joined
    assert "airmouse --gaze-calibrate" in joined
    assert "never auto-passed" in joined


def test_headless_writes_no_stdout_and_no_files(home, capsys, tmp_path):
    before = sorted(os.listdir(home)) if home.is_dir() else []
    res = ga.run_gaze_academy()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    after = sorted(os.listdir(home)) if home.is_dir() else []
    assert before == after                          # runner is disk-free
    assert res["rc"] == 0


def test_headless_unknown_lesson_rc1():
    res = ga.run_gaze_academy(lesson="not_a_lesson")
    assert res["rc"] == 1
    assert res["lessons"] == {}
    assert res["completed"] is False
    assert res["physical_required"] is True
    joined = "\n".join(res["output"])
    assert "not_a_lesson" in joined and HONEST_LINE in joined


def test_headless_single_lesson_selection():
    res = ga.run_gaze_academy(lesson="l3_dwell")
    assert list(res["lessons"]) == ["l3_dwell"]
    assert res["lessons"]["l3_dwell"]["passed"] is False
    assert res["physical_required"] is True
    joined = "\n".join(res["output"])
    assert EXPECTED_TITLES["l3_dwell"] in joined
    assert "activations" in joined


def test_headless_with_input_fn_never_blocks():
    calls = []

    res = ga.run_gaze_academy(lesson="l1_acquire",
                              input_fn=lambda msg: calls.append(msg))
    assert res["physical_required"] is True
    # headless mode never prompts at all
    assert calls == []


# ---------------------------------------------------------------------------
# 7. run_gaze_academy — labeled simulated dry-run
# ---------------------------------------------------------------------------

def test_simulated_all_lessons_pass_labeled():
    res = ga.run_gaze_academy(simulated=True)
    assert res["simulated"] is True
    assert res["physical_required"] is False
    assert res["completed"] is True
    assert res["rc"] == 0
    for lid, entry in res["lessons"].items():
        assert entry["passed"] is True, lid
        assert entry["simulated"] is True, lid
        assert entry["metrics"], lid                # real numbers computed
    joined = "\n".join(res["output"])
    assert "SIMULATED" in joined
    assert joined.count("SIMULATED") >= 5           # every lesson labeled
    assert "not physical performance" in joined


def test_simulated_l4_and_l5_metric_details():
    res = ga.run_gaze_academy(simulated=True)
    l4 = res["lessons"]["l4_blink"]["metrics"]
    assert l4["blink_detected"] == 1
    assert l4["gaze_lock_during_blink"] is True
    l5 = res["lessons"]["l5_eye_assist"]["metrics"]
    assert l5["gaze_target_locked"] is True
    assert l5["confirm_observed"] is True
    l1 = res["lessons"]["l1_acquire"]["metrics"]
    assert l1["acquisition_time_s"] is not None
    assert l1["acquisition_time_s"] <= 3.0


def test_simulated_single_lesson():
    res = ga.run_gaze_academy(lesson="l2_fixation", simulated=True)
    assert list(res["lessons"]) == ["l2_fixation"]
    assert res["lessons"]["l2_fixation"]["passed"] is True
    assert res["lessons"]["l2_fixation"]["simulated"] is True
    assert res["completed"] is True


# ---------------------------------------------------------------------------
# 8. run_gaze_academy — live path with scripted REAL samples
# ---------------------------------------------------------------------------

def scripted_source(samples):
    """A fake camera: yields the scripted samples then ends the stream."""
    it = iter(list(samples) + [None])
    return lambda: next(it)


def test_live_l2_passes_from_scripted_real_samples(home):
    samples = stream([i * 0.1 for i in range(15)])   # 1.4 s steady hold
    res = ga.run_gaze_academy(lesson="l2_fixation", camera=True,
                              gaze_source=scripted_source(samples))
    entry = res["lessons"]["l2_fixation"]
    assert entry["passed"] is True
    assert entry["simulated"] is False               # REAL, sensor-verified
    # the loop stops measuring at the FIRST moment criteria are met:
    # the hold proves 1.0 s at t=1.0 and the lesson passes right there
    assert entry["metrics"]["hold_duration_s"] == pytest.approx(1.0)
    assert res["completed"] is True
    assert res["physical_required"] is False
    assert res["simulated"] is False
    joined = "\n".join(res["output"])
    assert "PASSED — criteria met from REAL camera samples" in joined
    assert "SIMULATED" not in joined


def test_live_all_five_lessons_pass_from_scripted_real_samples(home):
    # l1: wander in from the left at t=0.6, then stay
    l1 = [sample(t, x=0.1 if t < 0.6 else 0.5)
          for t in [round(i * 0.1, 3) for i in range(21)]]
    # l2: steady 1.4 s hold
    l2 = stream([i * 0.1 for i in range(15)])
    # l3: 2.0 s hold ≥ the 0.8 s dwell threshold → 1 activation
    l3 = stream([i * 0.1 for i in range(21)])
    # l4: gaze locked on target, one blink at t=1.0
    l4 = [sample(t, eye_closed=abs(t - 1.0) < 0.05)
          for t in [round(i * 0.1, 3) for i in range(17)]]
    # l5: gaze locked + confirm gesture from t=1.2
    l5 = [sample(t, hand=(t >= 1.2))
          for t in [round(i * 0.1, 3) for i in range(21)]]
    streams = {"l1_acquire": l1, "l2_fixation": l2, "l3_dwell": l3,
               "l4_blink": l4, "l5_eye_assist": l5}
    res = ga.run_gaze_academy(camera=True,
                              gaze_source=scripted_source(
                                  [s for lid in EXPECTED_IDS
                                   for s in streams[lid]]))
    for lid, entry in res["lessons"].items():
        assert entry["passed"] is True, (lid, entry["metrics"])
        assert entry["simulated"] is False
    assert res["completed"] is True


def test_live_source_none_never_passes(home):
    res = ga.run_gaze_academy(lesson="l2_fixation", camera=True,
                              gaze_source=scripted_source([]))
    entry = res["lessons"]["l2_fixation"]
    assert entry["passed"] is False                  # never auto-passed
    assert entry["simulated"] is False
    assert entry["metrics"]["hold_duration_s"] == 0.0
    assert res["completed"] is False
    joined = "\n".join(res["output"])
    assert "no gaze samples arrived" in joined
    assert "never auto-passed" in joined


def test_live_garbage_source_never_crashes_or_passes(home):
    junk = [{"nope": 1}, "string", 42, {"t": "bad", "x": {}}, None]
    res = ga.run_gaze_academy(lesson="l2_fixation", camera=True,
                              gaze_source=scripted_source(junk))
    entry = res["lessons"]["l2_fixation"]
    assert entry["passed"] is False
    assert entry["metrics"]["hold_duration_s"] == 0.0


def test_live_crashing_source_never_crashes_runner(home):
    def bad_source():
        raise RuntimeError("camera exploded")

    res = ga.run_gaze_academy(lesson="l2_fixation", camera=True,
                              gaze_source=bad_source)
    assert res["lessons"]["l2_fixation"]["passed"] is False
    assert res["rc"] == 0


def test_live_gaze_source_without_camera_stays_headless(home):
    """camera truthy AND gaze_source callable is the live gate — a source
    without a camera claim degrades to the honest plan."""
    res = ga.run_gaze_academy(lesson="l2_fixation",
                              gaze_source=scripted_source(stream([0.0])))
    assert res["physical_required"] is True
    assert res["lessons"]["l2_fixation"]["passed"] is False


def test_live_input_fn_prompted_and_out_sink_filled(home):
    calls = []
    sink = io.StringIO()
    samples = stream([i * 0.1 for i in range(15)])
    res = ga.run_gaze_academy(lesson="l2_fixation", out=sink,
                              input_fn=calls.append, camera=True,
                              gaze_source=scripted_source(samples))
    assert len(calls) == 1 and "l2_fixation" in calls[0]
    assert sink.getvalue().strip()
    assert res["output"]                             # also returned as data


def test_unknown_lesson_lists_valid_ids():
    res = ga.run_gaze_academy(lesson="zzz")
    joined = "\n".join(res["output"])
    for lid in EXPECTED_IDS:
        assert lid in joined
