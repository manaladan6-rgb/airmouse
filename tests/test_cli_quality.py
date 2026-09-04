"""v15.1.0 hardening — CLI quality tests: user-grade errors + first-run menu.

Covers airmouse.user_errors (§13 error shape, sanitization, factories,
run_cli_guarded) and airmouse.cli_menu (§9 menu, first-run marker,
should_show_menu / run_menu). Deterministic: tmp_path homes via
AIRMOUSE_HOME, scripted stdin, monkeypatched TTY. No network, no hardware.
"""

from __future__ import annotations

import io
import os
import sys

import pytest

from airmouse.cli_menu import (
    MENU_ITEMS,
    handle_choice,
    is_first_run,
    marker_path,
    mark_setup_complete,
    render_menu,
    run_menu,
    should_show_menu,
)
from airmouse.user_errors import (
    AirMouseUserError,
    camera_not_found,
    config_corrupt,
    dependency_missing,
    display_unavailable,
    format_user_error,
    microphone_not_found,
    permission_denied,
    run_cli_guarded,
    sanitize_message,
)

# ═══ fixtures ═════════════════════════════════════════════════════════════


@pytest.fixture()
def fresh_home(tmp_path, monkeypatch):
    """Point AIRMOUSE_HOME at a not-yet-existing dir under tmp_path."""
    home = tmp_path / "airmouse-home"
    monkeypatch.setenv("AIRMOUSE_HOME", str(home))
    return str(home)


# ═══ format_user_error — §13 shape ════════════════════════════════════════


def test_user_error_str_and_full_shape():
    err = AirMouseUserError("start voice input", "No microphone was found.",
                            ["Plug in a mic", "Check sound settings"])
    assert str(err) == "start voice input"
    lines = format_user_error(err).splitlines()
    assert lines[0] == "AirMouse could not start voice input."
    assert lines[2] == "Reason:"
    assert lines[3] == "No microphone was found."
    assert lines[5] == "Fix:"
    assert lines[6] == "1. Plug in a mic"
    assert lines[7] == "2. Check sound settings"
    assert lines[9] == "Run:"
    assert lines[11] == "airmouse doctor"
    assert "Hint:" not in "\n".join(lines)


def test_user_error_hint_placement():
    err = AirMouseUserError("t", "r", ["f"], hint="Do the thing.")
    lines = format_user_error(err).splitlines()
    assert "Hint: Do the thing." in lines
    i = lines.index("Hint: Do the thing.")
    assert lines[i + 1] == ""
    assert lines[i + 2] == "Run:"
    assert lines[i + 4] == "airmouse doctor"


def test_generic_exception_shape_no_leak():
    out = format_user_error(RuntimeError("boom secret detail"))
    lines = out.splitlines()
    assert lines[0] == "AirMouse could not complete this action."
    assert "An unexpected internal error occurred." in lines
    assert "1. Re-run the command." in lines
    assert "2. If it persists, run: airmouse doctor" in lines
    assert "3. Report the issue with the debug output: airmouse --debug ..." in lines
    assert "boom secret detail" not in out  # raw exception text never leaks
    assert "Traceback" not in out


def test_debug_true_appends_traceback_outside_handler():
    err = RuntimeError("boom")
    out = format_user_error(err, debug=True)
    assert "Traceback (most recent call last)" in out
    assert "RuntimeError: boom" in out


def test_debug_false_no_traceback():
    assert "Traceback" not in format_user_error(RuntimeError("boom"), debug=False)


def test_debug_true_uses_active_traceback():
    err = ValueError("active boom")
    try:
        raise err
    except ValueError:
        out = format_user_error(err, debug=True)
    assert "Traceback (most recent call last)" in out
    assert "ValueError: active boom" in out


# ═══ sanitize_message ═════════════════════════════════════════════════════


def test_sanitize_home_prefix_and_standalone():
    home = os.path.expanduser("~")
    mid = sanitize_message(f"cannot read {home}/.airmouse/config.toml today")
    assert home not in mid
    assert "~/.airmouse/config.toml" in mid
    solo = sanitize_message(f"denied: {home}")
    assert solo == "denied: ~"
    # a longer path that merely starts the same must NOT be folded
    assert sanitize_message(f"{home}eta/file") == f"{home}eta/file"


def test_sanitize_tokens():
    tok_ghp = "ghp_" + "a" * 36
    tok_gho = "gho_" + "b" * 36
    tok_pat = "github_pat_" + "c1" * 20
    tok_sk = "sk-proj-" + "d" * 30
    tok_slack = "xoxb-" + "e" * 24
    text = " ".join([tok_ghp, tok_gho, tok_pat, tok_sk, tok_slack])
    out = sanitize_message(text)
    for tok in (tok_ghp, tok_gho, tok_pat, tok_sk, tok_slack):
        assert tok not in out
    assert "<redacted>" in out
    # "task-..." must not be mistaken for an sk- key
    assert "task-refactor" in sanitize_message("plan task-refactor ok")


def test_sanitize_bearer_header():
    out = sanitize_message("Authorization: Bearer " + "z" * 45)
    assert "z" * 45 not in out
    assert "<redacted>" in out


def test_sanitize_env_pairs():
    cases = [
        ("API_KEY=abc123def", "abc123def"),
        ("MY_API_KEY=xyz99", "xyz99"),
        ("token=tok123", "tok123"),
        ("AUTH_TOKEN=tok456", "tok456"),
        ("SECRET=hunter2", "hunter2"),
        ("password=pw123456", "pw123456"),
    ]
    for pair, secret in cases:
        out = sanitize_message("cfg " + pair + " done")
        assert secret not in out, pair
        assert "<redacted>" in out, pair
    assert "API_KEY=" in sanitize_message("cfg API_KEY=abc123def done")


def test_sanitize_truncates_at_500():
    out = sanitize_message("x" * 900)
    assert len(out) == 500
    assert out.endswith("...")
    assert out.startswith("x" * 400)


def test_format_sanitizes_every_channel():
    home = os.path.expanduser("~")
    tok = "ghp_" + "9" * 36
    err = AirMouseUserError(
        title=f"run {home}/tool",
        reason=f"bad token {tok}",
        fixes=[f"delete {tok} cache", "ok step"],
        hint=f"see {tok}",
    )
    out = format_user_error(err)
    assert home not in out
    assert tok not in out
    assert "<redacted>" in out


def test_debug_traceback_sanitized():
    tok = "ghp_" + "7" * 36
    out = format_user_error(RuntimeError(f"leak {tok}"), debug=True)
    assert tok not in out
    assert "Traceback (most recent call last)" in out


# ═══ factory helpers ══════════════════════════════════════════════════════


def test_factories_wellformed():
    errs = [
        microphone_not_found(),
        camera_not_found(),
        display_unavailable(),
        dependency_missing("vosk"),
        config_corrupt(os.path.join("some", "config.toml")),
        permission_denied("access the microphone"),
    ]
    for err in errs:
        assert isinstance(err, AirMouseUserError)
        assert isinstance(err.fixes, tuple)
        assert err.title and err.reason
        assert len(err.fixes) >= 1
        assert all(isinstance(f, str) and f for f in err.fixes)
    assert "airmouse setup" in " ".join(config_corrupt("/x/y.toml").fixes)
    perm = " ".join(permission_denied("access the camera").fixes)
    assert "airmouse doctor" in perm
    assert "Privacy" in perm or "permission" in perm.lower()


def test_dependency_missing_pip_command_plain():
    err = dependency_missing("vosk")
    assert "python -m pip install vosk" in " ".join(err.fixes)


def test_dependency_missing_pip_command_extra():
    err = dependency_missing("pyaudio", "voice")
    assert 'python -m pip install "airmouse[voice]"' in " ".join(err.fixes)


# ═══ run_cli_guarded ══════════════════════════════════════════════════════


def test_guarded_returns_main_fn_value():
    assert run_cli_guarded(lambda: 7) == 7
    assert run_cli_guarded(lambda: 0) == 0
    assert run_cli_guarded(lambda: None) == 0  # None normalizes to success


def test_guarded_user_error_exit_1_friendly(capsys):
    def boom():
        raise AirMouseUserError("start voice input", "No microphone.",
                                ["Plug in a mic"])

    assert run_cli_guarded(boom) == 1
    err = capsys.readouterr().err
    assert "Reason:" in err
    assert "airmouse doctor" in err
    assert "Traceback" not in err


def test_guarded_keyboardinterrupt_130(capsys):
    def boom():
        raise KeyboardInterrupt()

    assert run_cli_guarded(boom) == 130
    assert "Interrupted." in capsys.readouterr().err


def test_guarded_runtimeerror_no_raw_leak(capsys):
    def boom():
        raise RuntimeError("internal-secret-xyz")

    assert run_cli_guarded(boom) == 1
    err = capsys.readouterr().err
    assert "internal-secret-xyz" not in err
    assert "Traceback" not in err
    assert "An unexpected internal error occurred." in err


def test_guarded_systemexit_passthrough():
    def boom():
        raise SystemExit(3)

    with pytest.raises(SystemExit):
        run_cli_guarded(boom)


# ═══ menu definition / rendering / choice ═════════════════════════════════


def test_menu_items_exact_spec():
    assert MENU_ITEMS == [
        ("1", "Setup", ["setup"]),
        ("2", "Doctor", ["doctor"]),
        ("3", "Guided Test", ["test", "--guided"]),
        ("4", "Start AirMouse", []),
        ("5", "Voice", ["--voice"]),
        ("6", "Intelligence", ["intelligence"]),
        ("7", "Agent", ["agents"]),
        ("8", "Offline Test", ["offline-test"]),
        ("9", "Safety", ["self-test"]),
        ("0", "Help", ["--help"]),
    ]


def test_render_menu_exact_shape():
    lines = render_menu("15").splitlines()
    assert lines[0] == "AIRMouse v15"
    assert lines[1] == "Human + AI Computer Interaction"
    assert lines[3:13] == [f"[{k}] {lbl}" for k, lbl, _a in MENU_ITEMS]
    assert lines[13] == ""
    assert lines[14] == "Choose an option:"
    assert render_menu("15.1.0").startswith("AIRMouse v15.1.0\n")


def test_handle_choice_mappings():
    assert handle_choice("2") == ["doctor"]
    assert handle_choice(" 3 ") == ["test", "--guided"]
    assert handle_choice("4") == []
    assert handle_choice("4") is not None  # Start is VALID, distinct from None
    assert handle_choice("x") is None
    assert handle_choice("") is None
    assert handle_choice("   ") is None
    assert handle_choice("10") is None


def test_handle_choice_returns_copy():
    got = handle_choice("1")
    assert got == ["setup"]
    got.append("--mutated")
    assert handle_choice("1") == ["setup"]
    assert MENU_ITEMS[0][2] == ["setup"]


# ═══ first-run marker ═════════════════════════════════════════════════════


def test_marker_path_and_first_run_fresh_home(fresh_home):
    assert marker_path() == os.path.join(fresh_home, ".setup_complete")
    assert is_first_run() is True


def test_mark_setup_complete_roundtrip(fresh_home):
    assert mark_setup_complete() is True
    assert is_first_run() is False
    with open(marker_path(), "r", encoding="utf-8") as fh:
        assert fh.read().strip()


def test_mark_fails_on_unwritable_home(tmp_path, monkeypatch):
    blocker = tmp_path / "blocked"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    monkeypatch.setenv("AIRMOUSE_HOME", str(blocker))
    assert mark_setup_complete() is False  # marker under a file path fails
    assert is_first_run() is True          # and we stay on "first run"


# ═══ should_show_menu / run_menu ══════════════════════════════════════════


def test_should_show_menu_matrix(tmp_path, monkeypatch):
    home1 = str(tmp_path / "h1")
    monkeypatch.setenv("AIRMOUSE_HOME", home1)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    assert should_show_menu(argv=[]) is True          # fresh + tty + no args
    assert should_show_menu(argv=["doctor"]) is False  # args present
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert should_show_menu(argv=[]) is False          # not a TTY
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert mark_setup_complete() is True
    assert should_show_menu(argv=[]) is False          # marker now present

    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path / "h2"))
    monkeypatch.setattr(sys, "argv", ["airmouse"])
    assert should_show_menu() is True                  # default = sys.argv[1:]
    monkeypatch.setattr(sys, "argv", ["airmouse", "--voice"])
    assert should_show_menu() is False


def test_run_menu_pick_doctor():
    buf = io.StringIO()
    inputs = iter(["2"])
    got = run_menu("15", out=buf, input_fn=lambda: next(inputs))
    assert got == ["doctor"]
    assert "Choose an option:" in buf.getvalue()
    assert "[2] Doctor" in buf.getvalue()


def test_run_menu_invalid_then_valid_reprints():
    buf = io.StringIO()
    inputs = iter(["z", "1"])
    got = run_menu("15", out=buf, input_fn=lambda: next(inputs))
    assert got == ["setup"]
    assert buf.getvalue().count("Choose an option:") == 2  # menu reprinted


def test_run_menu_eof_returns_none():
    buf = io.StringIO()

    def _eof():
        raise EOFError()

    assert run_menu("15", out=buf, input_fn=_eof) is None


def test_run_menu_gives_up_after_three_invalid():
    buf = io.StringIO()
    inputs = iter(["a", "b", "c"])
    got = run_menu("15", out=buf, input_fn=lambda: next(inputs))
    assert got is None
    assert buf.getvalue().count("Choose an option:") == 3
