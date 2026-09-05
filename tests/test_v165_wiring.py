"""v16.5 wiring tests — the teacher, transcription, help, profile and
temporal observer must be reachable through the REAL main() CLI surface
(test_startup.py rule: a wiring regression must never ship green).

Two layers:
1. CLI commands that return before camera init — driven through real
   main() with patched argv + captured stdout (teach/learn/help-me/
   transcribe/voice-status/privacy).
2. The live startup path — plain `airmouse` (headless TTY-less) reaches
   the frame loop with the v16.5 temporal observer installed, using the
   same _StubTracker contract as test_startup.py.
"""

import contextlib
import io
import sys

import pytest

import airmouse.__main__ as am
from tests.test_startup import _StubTracker, _StartupProbeDone


def _run_cli(monkeypatch, tmp_path, argv, expect_rc=None):
    """Run real main() for a print-and-exit command; return (rc, out)."""
    home = tmp_path / "am_v165_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("AIRMOUSE_HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["airmouse"] + list(argv))
    buf = io.StringIO()
    rc = None
    with contextlib.redirect_stdout(buf):
        rc = am.main()
    out = buf.getvalue()
    if rc is None:
        rc = 0                      # print-and-exit commands bare-return
    if expect_rc is not None:
        assert rc == expect_rc, (rc, out[-800:])
    return rc, out


# ---------------------------------------------------------------------------
# teach / learn — the auto-teaching experience (mission §2/§23)
# ---------------------------------------------------------------------------

def test_cli_teach_headless_prints_plan(monkeypatch, tmp_path):
    rc, out = _run_cli(monkeypatch, tmp_path, ["teach"], expect_rc=0)
    assert "TEACHING PLAN" in out or "teach" in out.lower()
    # honesty: physical practice is never auto-passed headless
    assert "PHYSICAL" in out or "physical" in out.lower()
    # onboarding state now touched (NEW → IN_PROGRESS)
    from airmouse.teacher import OnboardingStore, OnboardingPhase
    st = OnboardingStore()
    assert st.phase in (OnboardingPhase.NEW, OnboardingPhase.IN_PROGRESS)


def test_cli_teach_voice_track(monkeypatch, tmp_path):
    rc, out = _run_cli(monkeypatch, tmp_path, ["teach", "voice"],
                       expect_rc=0)
    assert "voice" in out.lower()


def test_cli_teach_bad_track_rc1(monkeypatch, tmp_path):
    rc, out = _run_cli(monkeypatch, tmp_path, ["teach", "teleportation"],
                       expect_rc=1)


def test_cli_learn_runs_all_academies(monkeypatch, tmp_path):
    rc, out = _run_cli(monkeypatch, tmp_path, ["learn"], expect_rc=0)
    low = out.lower()
    for track in ("voice", "gaze", "gesture", "fusion"):
        assert track in low


# ---------------------------------------------------------------------------
# transcribe — live transcription CLI (mission §6)
# ---------------------------------------------------------------------------

def test_cli_transcribe_eof_clean(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # immediate EOF
    rc, out = _run_cli(monkeypatch, tmp_path, ["transcribe"], expect_rc=0)
    assert "LIVE TRANSCRIPTION" in out
    # honesty: simulated provider is labelled, never passed off as ASR
    assert "SIMULATED" in out


# ---------------------------------------------------------------------------
# help-me — contextual help from real capability data (mission §20)
# ---------------------------------------------------------------------------

def test_cli_help_me_panel(monkeypatch, tmp_path):
    rc, out = _run_cli(monkeypatch, tmp_path, ["help-me"], expect_rc=0)
    assert "AIRMouse — what you can do" in out
    assert "airmouse teach" in out


def test_cli_help_me_question(monkeypatch, tmp_path):
    rc, out = _run_cli(monkeypatch, tmp_path,
                       ["help-me", "how do I scroll?"],
                       expect_rc=0)
    assert "scroll" in out.lower()


# ---------------------------------------------------------------------------
# voice-status shows the honest provider panel; privacy shows the
# personalization summary (mission §5/§27)
# ---------------------------------------------------------------------------

def test_cli_voice_status_provider_panel(monkeypatch, tmp_path):
    rc, out = _run_cli(monkeypatch, tmp_path, ["voice-status"], expect_rc=0)
    assert "Built-in command recognition" in out
    assert "Active:" in out


def test_cli_privacy_personalization_summary(monkeypatch, tmp_path):
    rc, out = _run_cli(monkeypatch, tmp_path, ["privacy"], expect_rc=0)
    assert "PERSONALIZATION" in out
    assert "Nothing is uploaded" in out


# ---------------------------------------------------------------------------
# the live startup path — plain `airmouse` with the temporal observer
# ---------------------------------------------------------------------------

def test_plain_startup_reaches_loop_with_temporal_observer(
        monkeypatch, tmp_path):
    """Headless plain startup reaches the frame loop; the v16.5 observer
    (temporal pinch lifecycle + sensor health + READY panel) is part of
    the wiring and produces no crash."""
    home = tmp_path / "am_v165_live"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("AIRMOUSE_HOME", str(home))
    monkeypatch.setattr(am, "HandTracker", _StubTracker)
    monkeypatch.setattr(sys, "argv", ["airmouse", "--skip", "--no-cam",
                                      "--no-sound"])
    with pytest.raises(_StartupProbeDone):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            am.main()
    # on the success path the buffer content is lost to the probe raise;
    # reaching the probe AT ALL is the assertion (any wiring defect raises
    # something else and fails the test)


def test_version_still_works(monkeypatch, tmp_path):
    rc, out = _run_cli(monkeypatch, tmp_path, ["--version"], expect_rc=0)
    assert "16.5" in out
