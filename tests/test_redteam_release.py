"""AirMouse v15.1.0 — §17 security RED-TEAM release audit (runtime).

Adversarial, deterministic, offline test battery against the whole
package with special focus on the NEW v15.1 modules:

    persistence.py   setup_wizard.py   guided_test.py
    capabilities.py  doctor.py         verify.py
    user_errors.py   cli_menu.py       (AIP / permission regressions)

What this file proves (per §17 mission):

1.  CLI surface fuzz — hostile argv (unicode, 100 KB args, "--" args,
    weird memory subcommands) never escape main() with a traceback;
    exit codes stay in {0, 1, 2}.
2.  Persistence adversarial — path traversal rejected, symlink escape
    contained, corrupt/garbage/BOM/binary/5 MB store files fail closed
    to ``{}`` + quarantine, read-only directories yield failure states.
3.  Setup adversarial — hostile AIRMOUSE_HOME (a file, unwritable)
    completes with FAILED/ACTION_REQUIRED statuses, never raises.
4.  Doctor/capabilities adversarial — crashing / garbage / giant
    detectors degrade to FAILED rows; output stays bounded.
5.  Guided-test adversarial — hostile answers bounded; dying output
    stream and EOF input fail closed instead of crashing.
6.  user_errors adversarial — format-string braces are never
    interpolated, secrets redacted from every field, 1 MB payloads
    bounded, ANSI/control characters stripped.
7.  AIP/permission regression probes — hostile envelope batch fails
    closed; ALLOW_ONCE exhaustion fails closed; destructive keys
    denied by default.
8.  Concurrency — two threads hammering one store never corrupt it.
9.  No-network guarantee — the release modules work with every socket
    construction blocked (no accidental network in release paths).
10. Env-leak — a planted secret env var never appears in doctor or
    privacy output.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import threading

import pytest

from airmouse import (
    aip as aip_mod,
    capabilities,
    doctor as doctor_mod,
    guided_test,
    persistence,
    privacy,
    setup_wizard,
    user_errors,
    verify as verify_mod,
)
from airmouse.guided_test import GuidedTestRunner
from airmouse.permissions import AgentPermissionEngine, Decision

# ═══════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Fresh isolated AIRMOUSE_HOME (and HOME for legacy expanduser paths)."""
    h = tmp_path / "airmouse-home"
    h.mkdir()
    monkeypatch.setenv("AIRMOUSE_HOME", str(h))
    monkeypatch.setenv("HOME", str(tmp_path))
    return h


def _cli(argv):
    """Run airmouse main() in-process under run_cli_guarded.

    Returns (exit_code, stdout, stderr).  SystemExit (argparse errors)
    is captured, so nothing escapes; the caller asserts graceful codes.
    """
    import airmouse.__main__ as cli_main

    out, err = io.StringIO(), io.StringIO()
    old_argv, old_out, old_err = sys.argv, sys.stdout, sys.stderr
    sys.argv = ["airmouse"] + list(argv)
    sys.stdout, sys.stderr = out, err
    try:
        try:
            code = user_errors.run_cli_guarded(cli_main.main)
        except SystemExit as exc:  # argparse error paths
            code = exc.code
            if code is None:
                code = 0
            elif not isinstance(code, int):
                code = 1
    finally:
        sys.argv, sys.stdout, sys.stderr = old_argv, old_out, old_err
    return code, out.getvalue(), err.getvalue()


def _write_store_bytes(store, payload: bytes) -> None:
    persistence.ensure_dirs()
    with open(store.path, "wb") as f:
        f.write(payload)


def _envelope_bytes(data: dict, schema_version: int = 1) -> bytes:
    """A VALID store envelope (correct checksum) as raw bytes."""
    obj = {"schema_version": schema_version,
           "checksum": persistence._checksum(data),
           "saved_at": "2025-01-01T00:00:00+00:00",
           "data": data}
    return json.dumps(obj).encode("utf-8")


def _corrupt_copies(store) -> int:
    import glob
    return len(glob.glob(store.path + ".corrupt-*"))


# ═══════════════════════════════════════════════════════════════════════
# 1. CLI surface fuzz (in-process)
# ═══════════════════════════════════════════════════════════════════════


class TestCLISurfaceFuzz:
    """Hostile argv must never escape main() with a traceback."""

    def test_unknown_command_clean_argparse_error(self):
        code, out, err = _cli(["frobnicate"])
        assert code in (0, 1, 2)
        assert "Traceback" not in out and "Traceback" not in err

    def test_unicode_command_rejected_cleanly(self):
        code, out, err = _cli(["статус", "ᚠᚢᚦ", "--json"])
        assert code in (0, 1, 2)
        assert "Traceback" not in out + err

    def test_100kb_command_arg_never_crashes(self):
        code, out, err = _cli(["x" * 100_000])
        assert code in (0, 1, 2)
        assert "Traceback" not in out + err
        assert "MemoryError" not in out + err

    @pytest.mark.parametrize("argv", [
        ["--Evil-Flag"],
        ["--", "--Evil-Flag"],
        ["--", "memory", "status"],       # valid: must actually run
        ["memory", "-status"],
        ["memory", "--to"],
        ["memory", "---"],
    ], ids=lambda a: " ".join(x[:18] for x in a))
    def test_flag_style_args_clean(self, argv, home):
        code, out, err = _cli(argv)
        assert code in (0, 1, 2)
        assert "Traceback" not in out + err
        if argv == ["--", "memory", "status"]:
            assert code == 0 and "local memory stores" in out

    @pytest.mark.parametrize("arg", [
        "", "STATUS", "Status ", "status; rm -rf /", "status|export",
        "\x00", "éùᚠ𝔘", "x" * 100_000, "export --to /etc/passwd",
        "RESET", "delete-all", "help", "../etc", "status\x1b[31m",
    ])
    def test_memory_weird_command_args(self, arg, home):
        """`airmouse memory <weird>` — lifecycle verbs dispatch, anything
        else degrades to the legacy memory view; never a traceback."""
        code, out, err = _cli(["memory", arg])
        assert code in (0, 1, 2)
        assert "Traceback" not in out + err

    def test_memory_status_output_bounded_and_honest(self, home):
        code, out, err = _cli(["memory", "status"])
        assert code == 0
        assert len(out) < 50_000
        assert "local-only" in out or "local memory stores" in out

    def test_doctor_json_tolerates_oversized_flags(self, monkeypatch, home):
        from airmouse.capabilities import (
            CapabilityReport, Component, ComponentState)
        fake = CapabilityReport(components=[
            Component("Probe", "SYSTEM", ComponentState.READY, "ok")])
        monkeypatch.setattr(capabilities, "detect_all",
                            lambda quick=False: fake)
        code, out, err = _cli(
            ["doctor", "--json", "--verbose", "--debug",
             "--to", "A" * 10_000, "garbage-position"])
        assert code == 0
        parsed = json.loads(out)          # machine output stays JSON
        assert parsed["overall"] in ("READY FOR TESTING", "PARTIAL",
                                     "BLOCKED")

    def test_export_error_is_friendly_no_traceback(self, home):
        persistence.ensure_dirs()
        target = home / "exists.json"
        target.write_text("{}", encoding="utf-8")
        code, out, err = _cli(["memory", "export", "--to", str(target)])
        assert code == 1
        assert "AirMouse could not export your local memory." in err
        assert "Fix:" in err and "Traceback" not in err

    def test_export_cli_to_absolute_path_works(self, home, tmp_path):
        persistence.ensure_dirs()
        target = tmp_path / "bundle.json"
        code, out, err = _cli(["memory", "export", "--to", str(target)])
        assert code == 0
        blob = json.loads(target.read_text(encoding="utf-8"))
        assert blob["format"] == "airmouse-memory-export"
        assert "nothing is sent anywhere" in out

    def test_export_cli_rejects_traversal(self, home):
        code, out, err = _cli(
            ["memory", "export", "--to", "../../etc/airmouse-evil.json"])
        assert code == 1
        assert "Traceback" not in out + err
        assert not os.path.exists("/etc/airmouse-evil.json")


# ═══════════════════════════════════════════════════════════════════════
# 2. persistence adversarial
# ═══════════════════════════════════════════════════════════════════════


class TestPersistenceAdversarial:
    """Stores must fail closed under every hostile file/path input."""

    @pytest.mark.parametrize("bad", [
        "../../etc/evil.json",
        "exports/../../evil.json",
        "..\\etc\\evil.json",              # windows-style separators
        "/etc/../etc/airmouse-overwrite.json/x/..",
    ])
    def test_export_rejects_traversal_paths(self, home, bad):
        with pytest.raises(ValueError, match=r"\.\."):
            persistence.memory_export(bad)
        with pytest.raises(ValueError, match=r"\.\."):
            persistence.get_store("twin").export_to(bad)

    def test_export_null_byte_path_fails_closed(self, home):
        with pytest.raises((ValueError, OSError)):
            persistence.memory_export("\x00/etc/evil.json")

    def test_export_allows_absolute_user_path_documented(self, home, tmp_path):
        persistence.ensure_dirs()
        target = tmp_path / "abs-export.json"
        res = persistence.memory_export(str(target))
        assert res["bytes"] > 0 and os.path.exists(res["path"])

    def test_truncated_json_store_quarantines(self, home):
        store = persistence.PersistentStore("twin")
        store.save({"keep": "me"})
        with open(store.path, "r+b") as f:
            f.truncate(len(_envelope_bytes({"keep": "me"})) // 2)
        assert store.load() == {}
        assert store.last_corruption and "undecodable" in \
            store.last_corruption["reason"]
        assert _corrupt_copies(store) == 1

    def test_5mb_garbage_store_quarantines(self, home):
        store = persistence.PersistentStore("skills")
        _write_store_bytes(store, b"\xde\xad\xbe\xef" * (5 * 1024 * 1024 // 4))
        assert store.load() == {}
        assert _corrupt_copies(store) == 1
        assert store.last_corruption["reason"] == "undecodable: ValueError"

    def test_utf8_bom_store_quarantines(self, home):
        store = persistence.PersistentStore("vocabulary")
        _write_store_bytes(store, b"\xef\xbb\xbf" + _envelope_bytes({"a": 1}))
        assert store.load() == {}
        assert _corrupt_copies(store) == 1

    @pytest.mark.parametrize("junk", [b"", b"\x00\x01\x02\xff" * 64,
                                      b"null", b"[1,2,3]", b'"a string"'])
    def test_binary_and_empty_and_wrong_type_fail_closed(self, home, junk):
        store = persistence.PersistentStore("preferences")
        _write_store_bytes(store, junk)
        assert store.load() == {}
        assert _corrupt_copies(store) >= 1

    def test_checksum_tamper_detected(self, home):
        store = persistence.PersistentStore("workflows")
        obj = json.loads(_envelope_bytes({"steps": 3}).decode("utf-8"))
        obj["data"]["steps"] = 99                  # tamper behind the seal
        _write_store_bytes(store, json.dumps(obj).encode("utf-8"))
        assert store.load() == {}
        assert "checksum" in store.last_corruption["reason"]

    def test_symlink_escape_from_home_is_refused(self, home, tmp_path):
        """A symlink planted over a store file must never make AirMouse
        read data that lives outside the home (v15.1 fix, documented)."""
        store = persistence.PersistentStore("twin")
        persistence.ensure_dirs()
        outside = tmp_path / "exfil.json"
        persistence.atomic_write_bytes(outside, _envelope_bytes(
            {"stolen": "yes"}))
        os.symlink(outside, store.path)
        assert store.load() == {}                      # fail closed
        assert "escape" in store.last_corruption["reason"]
        assert json.loads(outside.read_text())["data"] == {"stolen": "yes"}
        store.save({"local": True})                    # replaces the link
        assert not os.path.islink(store.path)
        assert json.loads(outside.read_text())["data"] == {"stolen": "yes"}
        assert store.load() == {"local": True}

    def test_symlink_inside_home_still_loads(self, home):
        """Containment must not over-block: links within home are fine."""
        persistence.ensure_dirs()
        real = home / "memory" / "real-data.json"
        persistence.atomic_write_bytes(real, _envelope_bytes({"ok": 1}))
        store = persistence.PersistentStore("skills")
        os.symlink(real, store.path)
        assert store.load() == {"ok": 1}
        assert store.last_corruption is None

    def test_reset_reports_failure_when_write_fails(self, home, monkeypatch):
        store = persistence.PersistentStore("preferences")
        store.save({"precious": 1})

        def boom(*a, **kw):
            raise PermissionError("read-only filesystem")

        monkeypatch.setattr(persistence, "atomic_write_json", boom)
        res = store.reset()                      # must NOT raise
        assert res["cleared"] is False
        assert res["error"] == "PermissionError"
        assert "backup" in res
        assert store.load() == {"precious": 1}   # data untouched by failure

    def test_readonly_memory_dir_reset_delete_fail_closed(self, home):
        if hasattr(os, "getuid") and os.getuid() == 0:
            pytest.skip("root ignores directory permissions")
        persistence.ensure_dirs()
        store = persistence.PersistentStore("twin")
        store.save({"keep": 1})
        memdir = home / "memory"
        os.chmod(memdir, 0o500)
        try:
            res = store.reset()
            assert res["cleared"] is False and "error" in res
            assert store.delete() is False
            assert store.load() == {"keep": 1}
        finally:
            os.chmod(memdir, 0o700)

    def test_memory_reset_facade_never_raises_on_fs_failure(
            self, home, monkeypatch):
        persistence.ensure_dirs()

        def boom(*a, **kw):
            raise PermissionError("nope")

        monkeypatch.setattr(persistence, "atomic_write_json", boom)
        res = persistence.memory_reset()
        assert set(res["stores"]) == set(persistence.STORE_NAMES)
        assert all(s["cleared"] is False for s in res["stores"].values())


# ═══════════════════════════════════════════════════════════════════════
# 3. setup adversarial
# ═══════════════════════════════════════════════════════════════════════


class TestSetupAdversarial:
    """run_setup completes with honest FAILED/ACTION_REQUIRED rows."""

    def test_home_pointing_at_a_file_never_raises(self, tmp_path, monkeypatch):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("i am a file", encoding="utf-8")
        monkeypatch.setenv("AIRMOUSE_HOME", str(blocker))
        monkeypatch.setenv("HOME", str(tmp_path))
        rep = setup_wizard.run_setup(interactive=False, out=io.StringIO())
        assert len(rep.steps) == 11
        statuses = {s.id: s.status for s in rep.steps}
        assert statuses["storage"] == "FAILED"
        assert statuses["marker"] == "FAILED"

    def test_home_inside_a_file_never_raises(self, tmp_path, monkeypatch):
        blocker = tmp_path / "denied"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("AIRMOUSE_HOME", str(blocker / "deeper"))
        monkeypatch.setenv("HOME", str(tmp_path))
        rep = setup_wizard.run_setup(interactive=False, out=io.StringIO())
        assert len(rep.steps) == 11
        assert any(s.status == "FAILED" for s in rep.steps)

    def test_readonly_home_completes_with_failed_steps(self, tmp_path,
                                                       monkeypatch):
        if hasattr(os, "getuid") and os.getuid() == 0:
            pytest.skip("root ignores directory permissions")
        h = tmp_path / "locked-home"
        h.mkdir()
        (h / "memory").mkdir()
        monkeypatch.setenv("AIRMOUSE_HOME", str(h))
        monkeypatch.setenv("HOME", str(tmp_path))
        os.chmod(h, 0o500)
        try:
            rep = setup_wizard.run_setup(interactive=False,
                                         out=io.StringIO())
        finally:
            os.chmod(h, 0o700)
        statuses = {s.id: s.status for s in rep.steps}
        assert statuses["storage"] == "FAILED"
        assert statuses["marker"] == "FAILED"
        assert len(rep.steps) == 11


# ═══════════════════════════════════════════════════════════════════════
# 4. doctor / capabilities adversarial
# ═══════════════════════════════════════════════════════════════════════


class TestDoctorCapabilitiesAdversarial:
    """detect_all is fail-closed against hostile detector behaviour."""

    def test_crashing_detector_yields_failed_row(self, monkeypatch):
        def boom(quick):
            raise RuntimeError("detector exploded")

        monkeypatch.setattr(capabilities, "_detect_system", boom)
        rep = capabilities.detect_all(quick=True)
        rows = [c for c in rep.components if c.category == "SYSTEM"]
        assert rows and all(
            capabilities.ComponentState(c.state)
            is capabilities.ComponentState.FAILED for c in rows)
        assert any("detector crashed" in c.detail for c in rows)
        json.dumps(rep.to_machine())     # machine report stays serializable

    @pytest.mark.parametrize("garbage", [None, 42, "garbage", {"a": 1},
                                         [42, 43], "A" * 10_000_000],
                             ids=["none", "int", "str", "dict",
                                  "bad-list", "giant-str"])
    def test_garbage_detector_yields_failed_row(self, monkeypatch, garbage):
        monkeypatch.setattr(capabilities, "_detect_python",
                            lambda quick: garbage)
        rep = capabilities.detect_all(quick=True)
        rows = [c for c in rep.components if c.category == "PYTHON"]
        assert rows and all(
            capabilities.ComponentState(c.state)
            is capabilities.ComponentState.FAILED for c in rows)
        # the hostile payload itself must never be echoed into the report
        blob = json.dumps(rep.to_machine())
        assert len(blob) < 1_000_000
        if isinstance(garbage, str) and len(garbage) > 1000:
            assert garbage[:100] not in blob

    def test_giant_detector_output_keeps_report_bounded(self, monkeypatch):
        monkeypatch.setattr(capabilities, "_detect_browser",
                            lambda quick: "B" * 10_000_000)
        rep = capabilities.detect_all(quick=True)
        human = rep.format()
        assert len(human) < 1_000_000
        assert len(json.dumps(rep.to_machine())) < 1_000_000
        assert "Capability" in human

    def test_doctor_report_from_real_detection_is_bounded(self):
        doc = doctor_mod.run_doctor()
        text = doctor_mod.format_doctor_report(doc, verbose=True)
        assert len(text) < 1_000_000
        assert doc.summary_counts()["FAILED"] >= 0


# ═══════════════════════════════════════════════════════════════════════
# 5. guided-test adversarial
# ═══════════════════════════════════════════════════════════════════════


class TestGuidedAdversarial:
    """The laboratory survives hostile answers and broken output."""

    @pytest.mark.parametrize("answer", [
        "y" * 10_000,
        "n" * 100_000,
        "даᚠ𝕏 intentional գրեל answer " * 500,
        "\x00\tweird\x1b[31mANSI",
    ], ids=["y-10k", "n-100k", "unicode-5k", "control-chars"])
    def test_hostile_answers_bounded(self, home, answer):
        out = io.StringIO()
        runner = GuidedTestRunner(out=out,
                                  input_fn=lambda prompt, a=answer: a)
        report = runner.run(interactive=True, auto_simulate=False)
        assert len(report.results) == 12
        # the whole machine report stays bounded even under hostile input
        cap = guided_test._ANSWER_MAX_CHARS
        for r in report.results:
            assert len(r.detail) < 5_000          # recorded answers bounded
            for key, v in (r.measurements or {}).items():
                if isinstance(v, str):
                    # recorded ANSWERS are hard-capped; engine-DERIVED
                    # text (e.g. dictation normalization) may expand but
                    # stays a small multiple of the cap
                    limit = cap if key in ("raw", "user_note", "input",
                                           "note") else 4 * cap
                    assert len(v) <= limit, (r.id, key, len(v))
                elif isinstance(v, list):
                    for row in v:
                        texts = row.values() if isinstance(row, dict) \
                            else [row]
                        for rv in texts:
                            if isinstance(rv, str):
                                assert len(rv) <= cap
        assert len(json.dumps(report.to_machine())) < 200_000
        assert len(out.getvalue()) < 500_000      # narration stays sane

    def test_dying_output_stream_never_crashes_lab(self, home):
        class _DyingOut:
            def __init__(self):
                self.n = 0

            def write(self, s):
                self.n += 1
                if self.n > 3:
                    raise OSError("pipe closed")
                return len(s)

            def flush(self):
                pass

        runner = GuidedTestRunner(out=_DyingOut(),
                                  input_fn=lambda p: "n")
        report = runner.run(interactive=True, auto_simulate=False)
        assert len(report.results) == 12

    def test_eof_input_fails_closed_never_pass(self, home):
        def _eof(prompt):
            raise EOFError()

        runner = GuidedTestRunner(out=io.StringIO(), input_fn=_eof)
        report = runner.run(interactive=True, auto_simulate=False)
        for r in report.results:
            if r.mode is guided_test.TestMode.PHYSICAL:
                assert r.status.value != "PASS"


# ═══════════════════════════════════════════════════════════════════════
# 6. user_errors adversarial
# ═══════════════════════════════════════════════════════════════════════


class TestUserErrorsAdversarial:
    TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"   # 28-token shape

    def test_format_braces_never_interpolated(self):
        payload = "{0.__class__} {err.__init__.__globals__} {self.reason}"
        err = user_errors.AirMouseUserError(payload, payload, [payload],
                                            payload)
        out = user_errors.format_user_error(err)
        assert payload in out                       # verbatim, not .format()
        assert "<class" not in out                  # nothing was resolved
        assert "builtins" not in out

    def test_secrets_redacted_from_every_field(self):
        t = self.TOKEN
        err = user_errors.AirMouseUserError(
            f"export {t}",
            f"reason {t} sk-live-abc123def456 Bearer jwt.payload.sig",
            [f"fix MY_API_KEY=hunter2 {t}"],
            f"hint {t}")
        out = user_errors.format_user_error(err)
        assert t not in out
        assert "hunter2" not in out
        assert "sk-live-abc123def456" not in out
        assert "jwt.payload.sig" not in out
        debug = user_errors.format_user_error(err, debug=True)
        assert t not in debug and "hunter2" not in debug

    def test_1mb_reason_bounded_and_secret_free(self):
        big = "A" * 1_000_000 + " ghp_" + "z" * 30
        err = user_errors.AirMouseUserError("t", big, [big], None)
        out = user_errors.format_user_error(err)
        assert len(out) < 5_000
        assert "ghp_" not in out and "z" * 30 not in out

    def test_control_chars_and_ansi_stripped_newlines_kept(self):
        err = user_errors.AirMouseUserError(
            "t\x00itle",
            "line1\x1b[31mred\x1b[0m\rinjected\nline2\tkept",
            ["f\x07"], None)
        out = user_errors.format_user_error(err)
        assert "\x00" not in out and "\x1b" not in out
        assert "\r" not in out and "\x07" not in out
        assert "line1" in out and "line2\tkept" in out   # \n, \t survive


# ═══════════════════════════════════════════════════════════════════════
# 7. AIP / permission regression probes (v15 security re-checks)
# ═══════════════════════════════════════════════════════════════════════


class TestAipPermissionProbes:
    HOSTILE_ENVELOPES = [
        "", " ", "null", "[]", "123", "true", "False", '"hello"', "{}",
        "\x00", "a" * 100_000, "NaN",
        '{"aip_version": "999.9", "type": "hello", "id": "v"}',
        '{"aip_version": "1.0", "type": "DROP TABLE agents", "id": "s"}',
        '{"aip_version": "1.0", "type": "hello", "id": "\u202e"}',
        '{"aip_version": "1.0", "type": "hello", "id": "x", '
        '"extra_field": 1}',
        '{"aip_version": "1.0", "type": "hello", "id": "x", '
        '"payload": "string-not-object"}',
        '{"aip_version": "1.0", "type": "execute", "id": "x", '
        '"payload": {"action": "click", "params": '
        '{"cmd": "; rm -rf / && curl http://evil.invalid"}}}',
        '{"aip_version": ["1.0"], "type": "hello", "id": "x"}',
        '{"aip_version": "1.0", "type": "hello", "id": 123}',
    ]

    def test_hostile_envelope_batch_fails_closed(self):
        assert len(self.HOSTILE_ENVELOPES) == 20
        for raw in self.HOSTILE_ENVELOPES:
            msg, errs = aip_mod.parse_message(raw)      # must never raise
            assert isinstance(errs, list)
            if msg is not None:
                assert errs == []                       # parse ⇒ valid
                assert msg.version == aip_mod.AIP_VERSION

    def test_allow_once_exhaustion_fails_closed(self):
        eng = AgentPermissionEngine()
        # documented footgun: default uses=-1 is ALREADY exhausted —
        # the critical property is that it fails CLOSED, never open
        assert eng.grant("agent-a", "clipboard.write",
                         Decision.ALLOW_ONCE) is True
        d = eng.check("agent-a", "clipboard.write")
        assert d.allowed is False
        eng2 = AgentPermissionEngine()
        eng2.grant("agent-a", "clipboard.write", Decision.ALLOW_ONCE,
                   uses=1)
        assert eng2.check("agent-a", "clipboard.write").allowed is True
        d2 = eng2.check("agent-a", "clipboard.write")
        assert d2.allowed is False                      # exhausted ⇒ closed

    def test_destructive_key_denied_by_default(self):
        eng = AgentPermissionEngine()
        for key in ("system.shutdown", "files.delete_all",
                    "network.send_data"):
            d = eng.check("agent-x", key, risky=True)
            assert d.allowed is False
        # an unrelated grant must not leak onto destructive keys
        eng.grant("agent-x", "clipboard.write", Decision.ALLOW_SESSION)
        assert eng.check("agent-x", "system.shutdown",
                         risky=True).allowed is False
        # and EMERGENCY STOP denies everything, always
        eng.emergency_stop()
        assert eng.check("agent-x", "clipboard.write").allowed is False


# ═══════════════════════════════════════════════════════════════════════
# 8. concurrency
# ═══════════════════════════════════════════════════════════════════════


class TestConcurrency:
    def test_two_threads_hammering_one_store(self, home):
        persistence.ensure_dirs()
        store = persistence.PersistentStore("twin")
        errors = []

        def worker(offset: int) -> None:
            try:
                for i in range(100):
                    store.save({"i": offset + i * 2})
                    data = store.load()
                    if data == {}:
                        # only acceptable with an honest corruption flag
                        assert store.last_corruption is not None
                    else:
                        assert isinstance(data.get("i"), int)
            except Exception as exc:  # noqa: BLE001 — collected, asserted
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(0,)),
                   threading.Thread(target=worker, args=(1,))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert errors == []
        final = store.load()
        assert final == {} and store.last_corruption is not None \
            or isinstance(final.get("i"), int)


# ═══════════════════════════════════════════════════════════════════════
# 9. no-network guarantee
# ═══════════════════════════════════════════════════════════════════════


class TestNoNetworkGuarantee:
    def test_release_modules_function_with_all_sockets_blocked(
            self, monkeypatch, home):
        """Every v15.1 release path works with socket construction
        disabled — proving there is no accidental network dependency."""

        def _boom(*a, **kw):
            raise OSError("network disabled by red-team guard")

        class _NoSocket:
            def __init__(self, *a, **kw):
                raise OSError("socket blocked by red-team guard")

        monkeypatch.setattr(socket, "socket", _NoSocket)
        monkeypatch.setattr(socket, "create_connection", _boom)

        # persistence: save / load / export / status
        persistence.ensure_dirs()
        store = persistence.PersistentStore("twin")
        store.save({"k": 1})
        assert store.load() == {"k": 1}
        target = store.export_to(str(home / "export.json"))
        assert os.path.exists(target)
        assert persistence.memory_status()["home"] == str(home)

        # capabilities + doctor (quick: no device probes)
        rep = capabilities.detect_all(quick=True)
        assert isinstance(rep.to_machine(), dict)
        doc = doctor_mod.run_doctor()
        assert doc.sections

        # guided laboratory (non-interactive, hardware honestly deferred)
        runner = GuidedTestRunner(out=io.StringIO(), input_fn=None)
        gtest = runner.run(interactive=False, auto_simulate=True)
        assert len(gtest.results) == 12

        # setup wizard (non-interactive, nothing installed)
        srep = setup_wizard.run_setup(interactive=False, out=io.StringIO())
        assert len(srep.steps) == 11

        # verify (runs the full offline self-test under the guard too)
        vrep = verify_mod.run_verify()
        assert len(vrep.automated) == 10
        assert len(vrep.physical) == 5
        assert all(i.status == verify_mod.ACTION_REQUIRED
                   for i in vrep.physical)


# ═══════════════════════════════════════════════════════════════════════
# 10. env-leak scan at runtime
# ═══════════════════════════════════════════════════════════════════════


class TestEnvLeakScan:
    SECRET = "ghp_supersecretvalue0123456789abcd"

    def test_planted_secret_never_reaches_cli_or_report_output(
            self, monkeypatch, home):
        monkeypatch.setenv("AIRMOUSE_TEST_SECRET", self.SECRET)

        code, out, err = _cli(["doctor", "--json"])
        assert code == 0
        assert self.SECRET not in out + err

        code2, out2, err2 = _cli(["privacy", "--json"])
        assert code2 == 0
        assert self.SECRET not in out2 + err2

        code3, out3, err3 = _cli(["memory", "status"])
        assert code3 == 0
        assert self.SECRET not in out3 + err3

        pr = privacy.privacy_report()
        assert self.SECRET not in json.dumps(pr, default=str)
