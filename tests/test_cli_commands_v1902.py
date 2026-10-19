"""v19.0.2 CLI command tests — logs, config, live diagnostics, exit codes.

Every test here runs the REAL CLI (subprocess) or the real modules, so
these are integration tests, not unit mocks.
"""

import json
import os
import subprocess
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cli(*args, env_extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(_REPO, "airmouse_pkg")
    env.setdefault("AIRMOUSE_HOME", os.path.join(_REPO, ".tmp-airmouse-home"))
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "airmouse", *args],
        capture_output=True, text=True, env=env, timeout=120,
        cwd=_REPO)


def test_version_still_fast_and_correct():
    r = _cli("--version")
    assert r.returncode == 0
    assert "AirMouse v" in r.stdout


def test_version_does_not_import_cv2():
    """v19.0.2 — --version must not pay the heavy OpenCV import."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(_REPO, "airmouse_pkg")
    code = ("import sys, airmouse.__main__;"
            "assert 'cv2' not in sys.modules, 'cv2 imported eagerly';"
            "assert 'mediapipe' not in sys.modules")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env, cwd=_REPO, timeout=120)
    assert r.returncode == 0, r.stderr


def test_logs_command_empty_state():
    r = _cli("logs")
    assert r.returncode == 0
    out = (r.stdout or "") + (r.stderr or "")
    assert ("No command-log entries" in out) or ("Recent command outcomes" in out)


def test_logs_json_shape():
    r = _cli("logs", "10", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout.strip() or "[]")
    assert isinstance(data, list)
    for rec in data:
        assert {"t", "cmd", "rc"} <= set(rec.keys())


def test_logs_rejects_non_numeric_n():
    r = _cli("logs", "not-a-number")
    assert r.returncode == 2
    assert "usage" in ((r.stdout or "") + (r.stderr or ""))


def test_config_path_prints_toml_path():
    r = _cli("config", "path")
    assert r.returncode == 0
    assert r.stdout.strip().endswith("config.toml")


def test_config_validate_clean_home(tmp_path):
    r = _cli("config", "validate",
             env_extra={"AIRMOUSE_HOME": str(tmp_path)})
    out = (r.stdout or "") + (r.stderr or "")
    # no config file in a fresh home → the validator says so (or clean)
    assert r.returncode in (0, 1)
    assert ("not found" in out) or ("parses cleanly" in out)


def test_config_validate_unknown_section_fails(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[diretc]\nmincutoff = 1.0\n', encoding="utf-8")
    r = _cli("config", "validate",
             env_extra={"AIRMOUSE_HOME": str(tmp_path)})
    assert r.returncode == 1
    assert "unknown section" in ((r.stdout or "") + (r.stderr or ""))


def test_config_validate_bad_toml_fails(tmp_path):
    (tmp_path / "config.toml").write_bytes(b"\xff\xfe\x00broken")
    r = _cli("config", "validate",
             env_extra={"AIRMOUSE_HOME": str(tmp_path)})
    assert r.returncode == 1


def test_config_validate_json_machine_readable(tmp_path):
    r = _cli("config", "validate", "--json",
             env_extra={"AIRMOUSE_HOME": str(tmp_path)})
    assert r.returncode in (0, 1)
    data = json.loads(r.stdout.strip())
    assert {"path", "errors", "warnings"} <= set(data.keys())


def test_diagnostics_live_sim_honest_labels():
    """SIM mode must label itself SIMULATED and never claim OS movement."""
    r = _cli("diagnostics", "--live-sim", "--live-seconds", "0.8")
    assert r.returncode == 0
    out = (r.stdout or "") + (r.stderr or "")
    assert "SIMULATED" in out
    assert "OS cursor was NOT moved" in out
    assert "PHYSICAL_TEST_REQUIRED" in out
    assert "frames processed" in out


def test_diagnostics_live_sim_columns_present():
    r = _cli("diagnostics", "--live-sim", "--live-seconds", "0.5")
    out = (r.stdout or "") + (r.stderr or "")
    for col in ("raw(x,y)", "filtered(px)", "vel(px/s)", "state"):
        assert col in out, f"missing telemetry column {col}"


def test_diagnostics_live_real_without_camera_fails_honestly():
    """Headless sandbox: real camera path must fail with a clear FIX."""
    r = _cli("diagnostics", "--live", "--live-seconds", "0.5")
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode in (0, 1)
    if r.returncode == 1:
        assert ("unavailable" in out) and ("live-sim" in out)


def test_self_test_exit_code_honest():
    r = _cli("self-test")
    out = (r.stdout or "") + (r.stderr or "")
    # the deterministic self-test passes in CI; a FAIL row must mean rc 1
    if "FAIL" in out:
        assert r.returncode == 1
    else:
        assert r.returncode == 0


def test_command_choices_include_logs_and_config():
    from airmouse.__main__ import _COMMAND_CHOICES
    assert "logs" in _COMMAND_CHOICES
    assert "config" in _COMMAND_CHOICES


def test_cli_log_records_command_outcomes(tmp_path):
    """Running a CLI command appends to the bounded command log."""
    from airmouse import cli_log
    cli_log.clear()
    assert cli_log.log_command("test-cmd", 0, msg="hello") is True
    recs = cli_log.read_recent(5)
    assert recs and recs[-1]["cmd"] == "test-cmd"
    assert recs[-1]["msg"] == "hello"
    cli_log.clear()
    assert cli_log.read_recent(5) == []
