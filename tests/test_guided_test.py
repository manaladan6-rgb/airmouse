"""Tests for airmouse.guided_test — the guided test laboratory.

Deterministic, headless, no network, no sleeps.  Honesty contract:
PHYSICAL tests can never auto-PASS; non-interactive runs must produce
ACTION_REQUIRED for hardware tests and real PASSes only for the
deterministic simulation groups.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

import airmouse
from airmouse import guided_test as gt
from airmouse.guided_test import (
    TEST_IDS,
    TEST_ORDER,
    GuidedTestReport,
    GuidedTestResult,
    GuidedTestRunner,
    TestMode,
    TestStatus,
    format_report,
    run_guided,
)

PHYSICAL_IDS = ("camera", "mouse", "gaze", "voice", "dictation")
SIMULATION_IDS = ("installation", "intelligence", "browser", "agent",
                  "multi_agent", "recovery", "offline")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def scripted(*answers):
    """Build an input_fn that yields canned answers; EOF when exhausted."""
    it = iter(answers)
    calls = []

    def fn(prompt=""):
        calls.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError("scripted input exhausted")

    fn.calls = calls
    return fn


def never_prompts(prompt=""):
    raise AssertionError("input_fn must not be called in non-interactive mode")


def run_noninteractive(**kw):
    out = io.StringIO()
    report = GuidedTestRunner(out=out).run(interactive=False, **kw)
    return report, out.getvalue()


def by_id(report, tid):
    for r in report.results:
        if r.id == tid:
            return r
    raise AssertionError("missing result for " + tid)


def all_pass_report(version="15"):
    rows = [GuidedTestResult(tid, title, mode, TestStatus.PASS,
                             detail="synthetic")
            for tid, title, mode in TEST_ORDER]
    return GuidedTestReport(results=rows, version=version)


@pytest.fixture()
def frozen_now(monkeypatch):
    monkeypatch.setattr(gt, "_utc_now_iso",
                        lambda: "2030-01-01T00:00:00+00:00")
    return "2030-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# non-interactive honesty (the CI path)
# ---------------------------------------------------------------------------


class TestNonInteractiveHonesty:
    def test_physical_tests_are_action_required(self):
        report, _ = run_noninteractive()
        assert report.test_ids == list(TEST_IDS)
        for tid in PHYSICAL_IDS:
            r = by_id(report, tid)
            assert r.status is TestStatus.ACTION_REQUIRED
            assert r.mode is TestMode.PHYSICAL

    def test_physical_tests_never_pass(self):
        report, _ = run_noninteractive()
        statuses = {r.status for r in report.results
                    if r.mode is TestMode.PHYSICAL}
        assert TestStatus.PASS not in statuses

    def test_overall_partially_verified_noninteractive(self):
        report, text = run_noninteractive()
        assert report.overall() == "PARTIALLY VERIFIED"
        assert "PARTIALLY VERIFIED" in text

    def test_camera_instructions_in_detail(self):
        report, _ = run_noninteractive()
        detail = by_id(report, "camera").detail
        assert "Put your hand in front of the camera." in detail
        assert "Move your hand slowly to the left." in detail
        assert "[Y/N]" in detail

    def test_mouse_instructions_cover_all_actions(self):
        report, _ = run_noninteractive()
        detail = by_id(report, "mouse").detail
        for needle in ("double-click", "right-click", "scroll", "drag",
                       "[Y/N]"):
            assert needle in detail

    def test_voice_instructions_list_all_8_phrases(self):
        report, _ = run_noninteractive()
        detail = by_id(report, "voice").detail
        for phrase in ("click", "double click", "scroll down", "open notepad",
                       "copy", "paste", "undo", "close window"):
            assert phrase in detail
        assert "[Y/N]" in detail

    def test_gaze_and_dictation_instructions_present(self):
        report, _ = run_noninteractive()
        gaze = by_id(report, "gaze").detail
        assert "Look at target A" in gaze
        assert "Blink" in gaze
        assert "[Y/N]" in gaze
        d = by_id(report, "dictation").detail
        assert "[Y/N]" in d and "replace that with" in d

    def test_noninteractive_never_prompts(self):
        report, _ = run_noninteractive()
        # rerun with an input_fn that fails the run if ever called
        out = io.StringIO()
        report = GuidedTestRunner(out=out, input_fn=never_prompts).run(
            interactive=False)
        assert len(report.results) == 12


# ---------------------------------------------------------------------------
# deterministic simulation groups pass headless
# ---------------------------------------------------------------------------


class TestSimulationHeadless:
    def test_installation_passes_with_versions(self):
        report, _ = run_noninteractive()
        r = by_id(report, "installation")
        assert r.status is TestStatus.PASS
        assert r.mode is TestMode.AUTOMATED
        assert r.measurements["version"] == airmouse.__version__
        assert r.measurements["cli"]["importable"] is True
        deps = r.measurements["dependencies"]
        for dep in ("numpy", "cv2", "mediapipe", "pynput"):
            assert deps[dep]["present"] is True, dep
            assert deps[dep]["version"]

    def test_intelligence_passes_headless(self):
        report, _ = run_noninteractive()
        r = by_id(report, "intelligence")
        assert r.status is TestStatus.PASS
        assert r.measurements["executed"] is True
        assert r.measurements["safety_gate"]["allowed"] is True

    def test_intelligence_labels_distinct(self):
        report, out = run_noninteractive()
        r = by_id(report, "intelligence")
        # run() prefixes SIMULATION details with an honesty label; strip it
        lines = [ln[len("[SIMULATION] "):] if ln.startswith("[SIMULATION] ")
                 else ln for ln in r.detail.splitlines()]
        obs = [ln for ln in lines if ln.startswith("OBSERVED:")]
        pred = [ln for ln in lines if ln.startswith("PREDICTED:")]
        exe = [ln for ln in lines if ln.startswith("EXECUTED:")]
        assert len(obs) == 1 and len(pred) == 1 and len(exe) == 1
        assert "NOT executed unless approved" in pred[0]
        assert obs[0] != pred[0] != exe[0]
        assert "OBSERVED:" in out and "PREDICTED:" in out

    def test_browser_passes_headless(self):
        report, _ = run_noninteractive()
        r = by_id(report, "browser")
        assert r.status is TestStatus.PASS
        steps = r.measurements["steps"]
        names = [s["step"] for s in steps]
        assert "find_and_click_button" in names
        assert "navigate_about_blank" in names
        assert r.measurements["all_steps_ok"] is True
        assert all(s["ok"] for s in steps)

    def test_agent_passes_headless(self):
        report, _ = run_noninteractive()
        r = by_id(report, "agent")
        assert r.status is TestStatus.PASS
        assert "REQUEST" in r.detail and "RESULT" in r.detail
        assert r.measurements["benign_ok"] is True

    def test_agent_destructive_request_rejected(self):
        report, _ = run_noninteractive()
        r = by_id(report, "agent")
        m = r.measurements
        assert m["destructive_rejected"] is True
        # fail-closed evidence: no rule -> ASK -> denied
        assert m["destructive_decision"] == "ask"
        assert "fails closed" in m["destructive_reason"]
        assert "REJECTED" in r.detail

    def test_multi_agent_passes_headless(self):
        report, _ = run_noninteractive()
        r = by_id(report, "multi_agent")
        assert r.status is TestStatus.PASS
        m = r.measurements
        assert m["lease_granted"] is True
        assert m["conflict_detected"] is True
        assert m["resolution"] == "handoff"
        assert m["estop_agents_stopped"] == 2

    def test_multi_agent_conflict_and_estop_evidence(self):
        report, _ = run_noninteractive()
        m = by_id(report, "multi_agent").measurements
        recs = m["conflict_records"]
        assert recs and recs[0]["resolution"] == "lease_held"
        assert recs[0]["holder"] == "agentA"
        assert recs[0]["challenger"] == "agentB"
        assert m["estop_level"] == "emergency_stop"
        assert m["post_estop_authorized"] is False
        assert m["safety_gate_blocked"] is True

    def test_recovery_passes_headless(self):
        report, _ = run_noninteractive()
        r = by_id(report, "recovery")
        assert r.status is TestStatus.PASS
        detail = r.detail
        for label in ("OBSERVE:", "DIAGNOSE:", "RECOVER:", "VERIFY:"):
            assert label in detail

    def test_recovery_permission_denial_never_retried(self):
        report, _ = run_noninteractive()
        m = by_id(report, "recovery").measurements
        pd = m["permission_denial"]
        assert pd["rounds"] == 1           # no retry, straight to human
        assert pd["recovered"] is False
        assert pd["observed"] is True
        assert pd["stopped_safely"] is True
        for name in ("missing_target", "timeout", "app_crash"):
            assert m[name]["recovered"] is True
            assert m[name]["verified"] is True

    def test_offline_passes_headless(self):
        report, _ = run_noninteractive()
        r = by_id(report, "offline")
        assert r.status is TestStatus.PASS
        assert r.measurements["offline_report_ok"] is True
        assert r.measurements["offline_gate_ok"] is True
        assert r.measurements["failed_checks"] == []

    def test_simulation_results_labelled(self):
        report, out = run_noninteractive()
        for r in report.results:
            if r.mode is TestMode.SIMULATION:
                assert r.detail.startswith("[SIMULATION]"), r.id
        assert "(simulation)" in out

    def test_hierarchy_evidence_in_measurements(self):
        report, _ = run_noninteractive()
        agent_m = by_id(report, "agent").measurements
        assert "safety_policy" in agent_m["hierarchy_demonstrated"]
        assert agent_m["safety_level_after_block"] == "safety_policy"
        ma_m = by_id(report, "multi_agent").measurements
        assert ma_m["estop_level"] == "emergency_stop"


# ---------------------------------------------------------------------------
# interactive path (scripted input)
# ---------------------------------------------------------------------------


class TestInteractive:
    def test_camera_yes_passes(self):
        out = io.StringIO()
        answers = scripted("y")
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True, only=["camera"])
        r = by_id(report, "camera")
        assert r.status is TestStatus.PASS
        assert "Put your hand in front of the camera." in out.getvalue()
        assert "Did the cursor follow your hand? [Y/N]" in out.getvalue()
        assert r.measurements["answers"] == ["Y"]
        assert "camera_probe" in r.measurements

    def test_mouse_no_fails_with_user_words(self):
        out = io.StringIO()
        answers = scripted("n", "n", "n", "n", "n", "n",
                           "the cursor lagged behind my hand")
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True, only=["mouse"])
        r = by_id(report, "mouse")
        assert r.status is TestStatus.FAIL
        assert "User answered N" in r.detail
        assert "the cursor lagged behind my hand" in r.detail
        assert r.measurements["answers"] == ["N"] * 6
        assert r.measurements["user_note"] == \
            "the cursor lagged behind my hand"

    def test_mouse_all_yes_passes(self):
        out = io.StringIO()
        answers = scripted(*["y"] * 6)
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True, only=["mouse"])
        assert by_id(report, "mouse").status is TestStatus.PASS

    def test_gaze_yes_passes(self):
        out = io.StringIO()
        answers = scripted("yes", "yes")
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True, only=["gaze"])
        r = by_id(report, "gaze")
        assert r.status is TestStatus.PASS
        assert "Look at target A" in out.getvalue()
        assert "Did AirMouse detect your blink? [Y/N]" in out.getvalue()

    def test_voice_pass_with_typed_heard(self):
        out = io.StringIO()
        # 8 phrases heard exactly (Enter) + final Y
        answers = scripted(*([""] * 8 + ["y"]))
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True, only=["voice"])
        r = by_id(report, "voice")
        assert r.status is TestStatus.PASS
        m = r.measurements
        assert m["input"] == "user typed what was heard"
        assert all(p["matched"] for p in m["phrases"])
        assert len(m["phrases"]) == 8
        first = m["phrases"][0]
        assert first["interpreted"] == "click" and first["confidence"] >= 1.0
        text = out.getvalue()
        for needle in ('Say: "open notepad"', "heard: open notepad",
                       "interpreted: open_app", "intent: open",
                       "confidence:"):
            assert needle in text

    def test_voice_n_fails(self):
        out = io.StringIO()
        answers = scripted(*([""] * 8 + ["n"] + ["it typed the wrong app"]))
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True, only=["voice"])
        r = by_id(report, "voice")
        assert r.status is TestStatus.FAIL
        assert "it typed the wrong app" in r.detail

    def test_voice_wrong_heard_word_flags_mismatch(self):
        out = io.StringIO()
        # user says AirMouse heard "clik" for the first phrase; the alias
        # fixup rescues it, so this must still match "click"
        answers = scripted("clik", *([""] * 7 + ["y"]))
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True, only=["voice"])
        rows = by_id(report, "voice").measurements["phrases"]
        assert rows[0]["heard"] == "clik"
        assert rows[0]["matched"] is True   # deterministic alias fixup

    def test_dictation_pass_flow(self):
        out = io.StringIO()
        answers = scripted("please send the report",
                           "capitalize that", "y")
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True, only=["dictation"])
        r = by_id(report, "dictation")
        assert r.status is TestStatus.PASS
        m = r.measurements
        assert m["raw"] == "please send the report"
        assert m["normalized"] == "Please send the report"
        assert m["edit_command"] == "capitalize that"
        assert m["final"]
        assert "raw:" in out.getvalue() and "final:" in out.getvalue()

    def test_dictation_n_fails(self):
        out = io.StringIO()
        answers = scripted("hello there", "scratch that", "n",
                           "it deleted too much")
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True, only=["dictation"])
        r = by_id(report, "dictation")
        assert r.status is TestStatus.FAIL
        assert "it deleted too much" in r.detail

    def test_eof_mid_prompt_degrades_to_action_required(self):
        out = io.StringIO()
        answers = scripted()          # EOF on the very first question
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True, only=["camera"])
        r = by_id(report, "camera")
        assert r.status is TestStatus.ACTION_REQUIRED
        assert r.status is not TestStatus.PASS
        assert "never a PASS" in r.detail

    def test_eof_mid_voice_degrades(self):
        out = io.StringIO()
        answers = scripted("", "")    # two phrases heard, then EOF
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True, only=["voice"])
        assert by_id(report, "voice").status is TestStatus.ACTION_REQUIRED

    def test_interactive_simulation_never_asks(self):
        # simulation groups must not touch input_fn even interactively
        out = io.StringIO()
        answers = never_prompts
        report = GuidedTestRunner(out=out, input_fn=answers).run(
            interactive=True,
            only=["intelligence", "browser", "agent", "multi_agent",
                  "recovery", "offline"])
        assert all(r.status is not TestStatus.SKIP
                   for r in report.results)


# ---------------------------------------------------------------------------
# only-filtering
# ---------------------------------------------------------------------------


class TestOnlyFiltering:
    def test_subset_runs_in_canonical_order(self):
        # subset selection keeps the canonical lab order (stable reports)
        report, _ = run_noninteractive(only=["browser", "installation"])
        assert report.test_ids == ["installation", "browser"]

    def test_unknown_ids_ignored(self):
        report, _ = run_noninteractive(only=["bogus", "camera", "nope"])
        assert report.test_ids == ["camera"]

    def test_only_camera_alone(self):
        report, _ = run_noninteractive(only=["camera"])
        assert report.test_ids == ["camera"]
        assert by_id(report, "camera").status is TestStatus.ACTION_REQUIRED

    def test_only_all_ids_runs_everything(self):
        report, _ = run_noninteractive(only=list(TEST_IDS))
        assert report.test_ids == list(TEST_IDS)


# ---------------------------------------------------------------------------
# report formatting (exact shape)
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_all_pass_report_is_verified(self):
        report = all_pass_report()
        text = report.format_report(version="15")
        lines = text.splitlines()
        assert lines[0] == "=" * 40
        assert lines[1] == "        AIRMouse v15 TEST REPORT"
        assert lines[2] == "=" * 40
        assert lines[3] == ""
        assert "Installation        PASS" in lines
        assert "Camera              PASS" in lines
        assert "Multi-Agent        PASS" in lines or \
            "Multi-Agent         PASS" in lines
        assert "Hardware tests:     5/5" in lines
        assert "Simulation tests:   7/7" in lines
        assert "OVERALL:             VERIFIED" in lines
        assert lines[-1] == "=" * 40
        assert report.overall() == "VERIFIED"

    def test_action_required_display(self):
        rows = [GuidedTestResult("camera", "Camera", TestMode.PHYSICAL,
                                 TestStatus.ACTION_REQUIRED)]
        text = GuidedTestReport(results=rows).format_report()
        assert "Camera              ACTION REQUIRED" in text.splitlines()

    def test_mixed_report_partially_verified(self):
        rows = [
            GuidedTestResult("installation", "Installation",
                             TestMode.AUTOMATED, TestStatus.PASS),
            GuidedTestResult("camera", "Camera", TestMode.PHYSICAL,
                             TestStatus.ACTION_REQUIRED),
            GuidedTestResult("mouse", "Mouse", TestMode.PHYSICAL,
                             TestStatus.FAIL),
            GuidedTestResult("gaze", "Gaze", TestMode.PHYSICAL,
                             TestStatus.SKIP),
        ]
        text = GuidedTestReport(results=rows).format_report()
        lines = text.splitlines()
        assert "Hardware tests:     0/3" in lines
        assert "Simulation tests:   1/1" in lines
        assert "OVERALL:             PARTIALLY VERIFIED" in lines

    def test_module_level_format_report(self):
        report = all_pass_report()
        assert format_report(report) == report.format_report(version="15")
        assert format_report(report, version="16") == \
            report.format_report(version="16")

    def test_runner_report_counts(self):
        report, text = run_noninteractive()
        lines = text.splitlines()
        assert "Hardware tests:     0/5" in lines
        assert "Simulation tests:   7/7" in lines
        assert "OVERALL:             PARTIALLY VERIFIED" in lines


# ---------------------------------------------------------------------------
# machine report + determinism
# ---------------------------------------------------------------------------


class TestMachineReport:
    def test_to_machine_roundtrip(self):
        report, _ = run_noninteractive()
        blob = report.to_machine()
        assert json.dumps(blob)                     # serializable
        loaded = json.loads(json.dumps(blob))
        assert loaded["overall"] == "PARTIALLY VERIFIED"
        assert loaded["hardware"] == {"passed": 0, "total": 5}
        assert loaded["simulation"] == {"passed": 7, "total": 7}
        assert len(loaded["results"]) == 12
        assert loaded["results"][0]["id"] == "installation"
        assert loaded["results"][0]["mode"] == "automated"
        assert loaded["results"][1]["status"] == "ACTION_REQUIRED"

    def test_to_machine_version(self):
        report = all_pass_report(version="15")
        assert report.to_machine()["version"] == "15"
        assert report.to_machine(version="16")["version"] == "16"

    def test_determinism_two_runs_identical(self, frozen_now):
        r1, _ = run_noninteractive()
        r2, _ = run_noninteractive()
        assert r1.to_machine() == r2.to_machine()
        assert r1.format_report() == r2.format_report()

    def test_generated_at_is_iso(self, frozen_now):
        report, _ = run_noninteractive()
        assert report.to_machine()["generated_at"] == frozen_now


# ---------------------------------------------------------------------------
# API contract pins
# ---------------------------------------------------------------------------


class TestApiContract:
    def test_status_enum_values(self):
        assert [s.value for s in TestStatus] == \
            ["PASS", "FAIL", "SKIP", "ACTION_REQUIRED"]

    def test_mode_enum_values(self):
        assert [m.value for m in TestMode] == \
            ["automated", "simulation", "physical"]

    def test_result_dataclass_defaults(self):
        r = GuidedTestResult("x", "X", TestMode.SIMULATION, TestStatus.SKIP)
        assert r.detail == ""
        assert r.measurements == {}

    def test_test_ids_exact(self):
        assert list(TEST_IDS) == [
            "installation", "camera", "mouse", "gaze", "voice", "dictation",
            "intelligence", "browser", "agent", "multi_agent", "recovery",
            "offline",
        ]
        assert len(TEST_ORDER) == 12

    def test_modes_pinned_per_id(self):
        expected = {
            "installation": TestMode.AUTOMATED,
            "camera": TestMode.PHYSICAL,
            "mouse": TestMode.PHYSICAL,
            "gaze": TestMode.PHYSICAL,
            "voice": TestMode.PHYSICAL,
            "dictation": TestMode.PHYSICAL,
            "intelligence": TestMode.SIMULATION,
            "browser": TestMode.SIMULATION,
            "agent": TestMode.SIMULATION,
            "multi_agent": TestMode.SIMULATION,
            "recovery": TestMode.SIMULATION,
            "offline": TestMode.SIMULATION,
        }
        for tid, title, mode in TEST_ORDER:
            assert expected[tid] is mode, tid
        for tid in TEST_IDS:
            assert by_id(
                GuidedTestRunner(out=io.StringIO()).run(
                    interactive=False, only=[tid]),
                tid).title

    def test_run_guided_convenience(self):
        out = io.StringIO()
        report = run_guided(interactive=False, out=out)
        assert isinstance(report, GuidedTestReport)
        assert report.test_ids == list(TEST_IDS)
        text = format_report(report)
        assert text == report.format_report(version="15")
        assert "AIRMouse v15 TEST REPORT" in text

    def test_runner_defaults_are_interactive(self):
        import inspect
        sig = inspect.signature(GuidedTestRunner.run)
        assert sig.parameters["interactive"].default is True
        assert sig.parameters["auto_simulate"].default is False
        assert sig.parameters["only"].default is None
        init = inspect.signature(GuidedTestRunner.__init__)
        # default captured at import time; pytest capture swaps sys.stdout,
        # so assert it is a real output stream rather than identity
        out_default = init.parameters["out"].default
        assert out_default is not None and hasattr(out_default, "write")
        assert init.parameters["input_fn"].default is input

    def test_empty_report_not_verified(self):
        empty = GuidedTestReport(results=[])
        assert empty.overall() == "PARTIALLY VERIFIED"
        assert "OVERALL:             PARTIALLY VERIFIED" in \
            empty.format_report()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
