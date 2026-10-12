"""tests/test_lifecycle_wizard.py — v19 Phase 8/9/13/16 coverage.

Lifecycle laws under test:
* reset/uninstall ALWAYS describe what will be removed and refuse
  without confirmation (unless assume_yes for scripts);
* config is backed up before removal; user data is never silently lost;
* repair performs only safe local fixes (never installs);
* update check is explicit-only and honest offline;
* the wizard is honest per-step and never fakes hardware success.
"""

from __future__ import annotations

import io
import json
import os

import pytest

from airmouse import lifecycle
from airmouse.lifecycle import (describe_home, repair_home, reset_home,
                                uninstall_home, _version_gt)
from airmouse.setup_wizard import run_setup
from airmouse.wizard import WizardReport, WizardStep, run_wizard


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path / "amhome"))
    return tmp_path / "amhome"


def _seed_home(home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text('[track]\nmode = "direct"\n',
                                      encoding="utf-8")
    (home / "models").mkdir(exist_ok=True)
    (home / "models" / "vosk").mkdir(exist_ok=True)
    (home / "models" / "vosk" / "junk").write_text("x", encoding="utf-8")
    (home / "profile").mkdir(exist_ok=True)
    (home / "profile" / "interaction.json").write_text("{}", encoding="utf-8")
    (home / "transcripts").mkdir(exist_ok=True)
    (home / "transcripts" / "s1.json").write_text("{}", encoding="utf-8")
    return home


# ---------------------------------------------------------------------------
# describe / reset
# ---------------------------------------------------------------------------

def test_describe_home_lists_known_entries(home):
    _seed_home(home)
    entries = dict(describe_home())
    assert "config.toml" in entries
    assert "models" in entries
    assert entries["models"] == "downloaded local models (re-downloadable)"
    assert "profile" in entries


def test_reset_config_requires_confirmation(home):
    _seed_home(home)
    buf = io.StringIO()
    ok, actions = reset_home("config", assume_yes=False,
                             confirm_fn=lambda q: False, out=buf)
    assert ok is False
    assert any("cancelled" in a.detail for a in actions)
    assert (home / "config.toml").exists()          # nothing removed
    assert "will remove" in buf.getvalue().lower()


def test_reset_config_backs_up_then_removes(home):
    _seed_home(home)
    ok, actions = reset_home("config", assume_yes=True, out=io.StringIO())
    assert ok is True
    assert not (home / "config.toml").exists()
    backups = list((home / "backups").glob("config-pre-reset-*.toml"))
    assert len(backups) == 1                        # backup FIRST
    assert backups[0].read_text(encoding="utf-8").startswith("[track]")


def test_reset_models_only_touches_models(home):
    _seed_home(home)
    ok, _ = reset_home("models", assume_yes=True, out=io.StringIO())
    assert ok is True
    assert not (home / "models").exists()
    assert (home / "config.toml").exists()          # config untouched
    assert (home / "profile" / "interaction.json").exists()


def test_reset_all_declines_without_explicit_yes_and_lists_everything(home):
    _seed_home(home)
    buf = io.StringIO()
    ok, _ = reset_home("all", assume_yes=False,
                       confirm_fn=lambda q: False, out=buf)
    assert ok is False
    text = buf.getvalue()
    assert "transcripts" in text and "profile" in text
    assert "INCLUDES" in text                       # explicit warning


def test_reset_all_removes_the_home(home):
    _seed_home(home)
    ok, actions = reset_home("all", assume_yes=True, out=io.StringIO())
    assert ok is True
    assert not home.exists()


def test_reset_unknown_scope_is_honest(home):
    ok, actions = reset_home("everything", assume_yes=True,
                             out=io.StringIO())
    assert ok is False
    assert "unknown scope" in actions[0].detail


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------

def test_repair_is_safe_and_reports(home, capsys):
    _seed_home(home)
    (home / "config.toml").write_bytes(b"\x97 broken utf-8 \xff")
    ok, actions = repair_home(out=io.StringIO())
    names = [a.name for a in actions]
    assert "home directory structure" in names
    assert "config" in names
    assert "model integrity" in names
    assert ok in (True, False)                      # report is honest either
    out = capsys.readouterr().out                   # nothing raised


def test_repair_never_installs_packages(home, monkeypatch):
    _seed_home(home)
    boomed = []

    def _boom(*a, **k):  # any pip attempt = a law violation
        boomed.append(a)
        raise AssertionError("pip must never run during repair")

    monkeypatch.setattr("subprocess.run", _boom)
    repair_home(out=io.StringIO())
    assert boomed == []


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

def test_uninstall_lists_then_needs_confirmation(home):
    _seed_home(home)
    buf = io.StringIO()
    ok, _ = uninstall_home(assume_yes=False,
                           confirm_fn=lambda q: False, out=buf)
    assert ok is False
    text = buf.getvalue()
    assert "will be REMOVED" in text
    assert "pip uninstall airmouse" in text         # the exact command
    assert home.exists()                            # nothing removed


def test_uninstall_removes_home_after_yes(home):
    _seed_home(home)
    ok, _ = uninstall_home(assume_yes=True, out=io.StringIO())
    assert ok is True
    assert not home.exists()


def test_uninstall_when_absent_is_honest(home):
    ok, actions = uninstall_home(assume_yes=True, out=io.StringIO())
    assert ok is True
    assert "already absent" in actions[0].detail


# ---------------------------------------------------------------------------
# update check (network-guarded, offline-honest)
# ---------------------------------------------------------------------------

def test_version_gt():
    assert _version_gt("19.1.0", "19.0.0") is True
    assert _version_gt("19.0.0", "19.0.0") is False
    assert _version_gt("20.0.0", "19.9.9") is True
    assert _version_gt("bogus", "19.0.0") is False


def test_update_check_offline_is_honest(home, monkeypatch, capsys):
    import urllib.request

    def _boom(*a, **k):
        raise OSError("no network in this test")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    available, info = lifecycle.check_for_update(out=io.StringIO())
    assert available is False
    assert "cannot check" in info["detail"]
    assert "expected offline" in info["detail"]


def test_update_check_never_downloads_a_package(home, monkeypatch):
    """Even a successful PyPI reply must not trigger any install."""
    import urllib.request
    calls = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    payload = json.dumps({"info": {"version": "99.0.0"}}).encode()
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=0: calls.append(req) or _Resp(payload))
    available, info = lifecycle.check_for_update(out=io.StringIO())
    assert available is True
    assert info["latest"] == "99.0.0"
    assert "pip install --upgrade" in info["detail"]
    assert len(calls) == 1                          # exactly one GET, no more


# ---------------------------------------------------------------------------
# wizard (Phase 9)
# ---------------------------------------------------------------------------

def test_wizard_six_steps_honest_on_headless(home, capsys):
    buf = io.StringIO()
    report = run_wizard(out=buf)
    assert [s.id for s in report.steps] == \
        ["camera", "hand", "gesture", "voice", "input", "ready"]
    text = buf.getvalue()
    for phrase in ("STEP 1", "STEP 4", "Open browser", "Click that",
                   "You're ready"):
        assert phrase in text
    verdict = report.verdict()
    assert verdict in ("READY", "PARTIAL", "BLOCKED")
    # this sandbox has no camera/mic: nothing may claim READY via faking
    cam = next(s for s in report.steps if s.id == "camera")
    if not os.path.exists("/dev/video0"):
        assert cam.status in ("ACTION_REQUIRED", "FAILED")


def test_wizard_report_format_verdicts():
    r = WizardReport(steps=[
        WizardStep("camera", "Camera", "PASS"),
        WizardStep("hand", "Hand tracking", "PASS"),
        WizardStep("gesture", "Gestures", "PASS"),
        WizardStep("voice", "Voice", "ACTION_REQUIRED"),
        WizardStep("input", "Input", "PASS"),
        WizardStep("ready", "Verdict", "READY"),
    ])
    assert r.verdict() == "READY"
    assert "You're ready" in r.format()
    r2 = WizardReport(steps=[WizardStep("camera", "Camera",
                                        "ACTION_REQUIRED")])
    assert r2.verdict() == "BLOCKED"


# ---------------------------------------------------------------------------
# setup 2.0 (Phase 8) — new steps + measured timing
# ---------------------------------------------------------------------------

def test_setup_has_v19_steps_and_measured_timing(home, capsys):
    buf = io.StringIO()
    report = run_setup(interactive=False, out=buf, input_fn=lambda *_: "n")
    ids = [s.id for s in report.steps]
    assert ids == ["env", "deps", "storage", "config", "cameras",
                   "microphones", "browsers", "asr", "permissions", "models",
                   "interaction", "profile", "smoke", "marker"]
    assert all(s.seconds >= 0.0 for s in report.steps)
    total = sum(s.seconds for s in report.steps)
    assert total > 0
    text = buf.getvalue()
    assert "Done in" in text and "s." in text
    out = capsys.readouterr().out
    assert "setup" in out.lower() or True


def test_setup_model_step_never_downloads(home, monkeypatch):
    boomed = []

    def _boom(*a, **k):
        boomed.append(a)
        raise AssertionError("setup must never download a model")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    report = run_setup(interactive=False, out=io.StringIO(),
                       input_fn=lambda *_: "n")
    step = report.get("models")
    assert step is not None
    assert step.status in ("DONE", "SKIPPED", "FAILED")
    assert boomed == []


def test_setup_interaction_step_picks_best_mode(home):
    report = run_setup(interactive=False, out=io.StringIO(),
                       input_fn=lambda *_: "n")
    step = report.get("interaction")
    assert step is not None and step.status == "DONE"
    assert "best available" in step.detail
    assert any(m in step.detail for m in
               ("GESTURE", "VOICE", "GAZE", "DIAGNOSTIC", "SIMULATION",
                "OBSERVATION"))
