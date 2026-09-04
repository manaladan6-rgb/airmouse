"""Tests for v15.1 hardening: persistence + setup wizard + privacy report.

Deterministic, offline, headless.  Every test isolates storage with
monkeypatch.setenv("AIRMOUSE_HOME", tmp_path).  No test may touch the
real ~/.airmouse, and no test may allow a real pip subprocess.
"""

from __future__ import annotations

import glob
import io
import os
import sys
import types

import pytest

from airmouse import persistence, privacy, setup_wizard
from airmouse.setup_wizard import SetupReport, SetupStep

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolated AIRMOUSE_HOME pointing at a fresh temp dir."""
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def quiet_out():
    return io.StringIO()


def _checksum(data) -> str:
    return persistence._checksum(data)


def _write_envelope(path, version, data):
    persistence.atomic_write_json(path, {
        "schema_version": version,
        "checksum": _checksum(data),
        "saved_at": "2025-01-01T00:00:00+00:00",
        "data": data,
    })


# ---------------------------------------------------------------------------
# atomic writes + JSON IO
# ---------------------------------------------------------------------------


def test_atomic_write_bytes_roundtrip(home, tmp_path):
    target = tmp_path / "blob.bin"
    payload = b"\x00\x01airmouse\xff" * 33
    persistence.atomic_write_bytes(str(target), payload)
    with open(target, "rb") as f:
        assert f.read() == payload


def test_atomic_write_json_roundtrip_unicode_sorted(home, tmp_path):
    target = tmp_path / "obj.json"
    obj = {"z": 1, "a": {"ü": "naïve 手"}, "m": [1, 2]}
    persistence.atomic_write_json(str(target), obj)
    raw = target.read_text(encoding="utf-8")
    assert "naïve 手" in raw                      # ensure_ascii=False
    assert raw.index('"a"') < raw.index('"m"') < raw.index('"z"')
    assert persistence.read_json(str(target)) == obj


def test_read_json_missing_raises_filenotfounderror(home, tmp_path):
    with pytest.raises(FileNotFoundError):
        persistence.read_json(str(tmp_path / "nope.json"))


def test_read_json_undecodable_raises_valueerror(home, tmp_path):
    target = tmp_path / "bad.json"
    target.write_bytes(b"{definitely not json")
    with pytest.raises(ValueError):
        persistence.read_json(str(target))


def test_read_json_non_dict_raises_valueerror(home, tmp_path):
    target = tmp_path / "list.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError):
        persistence.read_json(str(target))


def test_atomic_write_overwrites_garbage_and_leaves_no_temp(home, tmp_path):
    target = tmp_path / "obj.json"
    target.write_bytes(b"stale partial garbage" * 500)
    persistence.atomic_write_json(str(target), {"clean": True})
    assert persistence.read_json(str(target)) == {"clean": True}
    leftovers = glob.glob(str(tmp_path / ".airmouse-tmp-*"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# home directory + path safety
# ---------------------------------------------------------------------------


def test_airmouse_home_env_override(home, tmp_path):
    assert persistence.airmouse_home() == str(tmp_path)


def test_airmouse_home_default_when_unset(monkeypatch):
    monkeypatch.delenv("AIRMOUSE_HOME", raising=False)
    assert persistence.airmouse_home() == os.path.join(
        os.path.expanduser("~"), ".airmouse")


def test_ensure_dirs_creates_all_subdirs_and_is_idempotent(home):
    returned = persistence.ensure_dirs()
    assert returned == str(home)
    for sub in persistence.SUBDIR_NAMES:
        assert (home / sub).is_dir()
    # second call must be a no-op, not an error
    assert persistence.ensure_dirs() == str(home)


def test_all_store_names_creatable_and_paths_under_home(home):
    persistence.ensure_dirs()
    for name in persistence.STORE_NAMES:
        store = persistence.get_store(name)
        assert store.path.startswith(str(home) + os.sep)
        assert store.path.startswith(os.path.join(str(home), "memory"))
        assert store.path.endswith(name + ".json")


def test_get_store_unknown_name_raises_valueerror(home):
    for bad in ("../evil", "..", "a/b", "sub\\dir", "", "\x00bad"):
        with pytest.raises(ValueError):
            persistence.get_store(bad)
    with pytest.raises(ValueError):
        persistence.get_store("not-a-store")


# ---------------------------------------------------------------------------
# PersistentStore: save / load / checksum / corruption
# ---------------------------------------------------------------------------


def test_store_save_load_roundtrip(home):
    store = persistence.get_store("twin")
    store.save({"facts": [{"id": "f1"}], "count": 1})
    assert persistence.get_store("twin").load() == {
        "facts": [{"id": "f1"}], "count": 1}


def test_store_envelope_shape(home):
    store = persistence.get_store("vocabulary")
    data = {"words": ["alpha"]}
    store.save(data)
    envelope = persistence.read_json(store.path)
    assert set(envelope) >= {"schema_version", "checksum", "saved_at",
                             "data"}
    assert envelope["schema_version"] == persistence.SCHEMA_VERSION
    assert envelope["checksum"] == _checksum(data)
    assert envelope["data"] == data
    assert "T" in envelope["saved_at"]          # iso8601 timestamp


def test_tampered_file_returns_empty_and_quarantines(home):
    store = persistence.get_store("twin")
    store.save({"k": [1, 2, 3]})
    envelope = persistence.read_json(store.path)
    envelope["data"]["k"] = [9, 9, 9]           # valid JSON, stale checksum
    persistence.atomic_write_json(store.path, envelope)

    assert store.load() == {}
    assert store.last_corruption is not None
    assert "checksum" in store.last_corruption["reason"]
    corrupt = glob.glob(store.path + ".corrupt-*")
    assert len(corrupt) == 1                    # original preserved as copy
    assert not os.path.exists(store.path)       # moved out of the way
    assert persistence.read_json(corrupt[0])["data"]["k"] == [9, 9, 9]


def test_recovery_after_corruption(home):
    store = persistence.get_store("skills")
    store.save({"ok": True})
    envelope = persistence.read_json(store.path)
    envelope["data"]["ok"] = "tampered"
    persistence.atomic_write_json(store.path, envelope)
    assert store.load() == {}                   # fails closed
    store.save({"ok": True, "v": 2})            # second save recovers
    assert store.load() == {"ok": True, "v": 2}
    assert store.last_corruption is None


def test_undecodable_file_quarantined(home):
    store = persistence.get_store("workflows")
    store.save({"a": 1})
    with open(store.path, "wb") as f:
        f.write(b"\xff\xfe not json at all")
    assert store.load() == {}
    assert "undecodable" in store.last_corruption["reason"]
    assert len(glob.glob(store.path + ".corrupt-*")) == 1


def test_corrupt_copies_pruned_to_newest_3(home):
    store = persistence.get_store("preferences")
    store.save({"v": 1})
    # five pre-existing quarantined copies with distinct, increasing mtimes
    stamps = [1700000000 + i for i in range(5)]
    for t in stamps:
        p = f"{store.path}.corrupt-{t}"
        with open(p, "wb") as f:
            f.write(b"old garbage")
        os.utime(p, (t, t))
    # tamper + load triggers a sixth quarantine, then pruning to 3
    with open(store.path, "wb") as f:
        f.write(b"broken")
    assert store.load() == {}
    remaining = sorted(glob.glob(store.path + ".corrupt-*"))
    assert len(remaining) == 3
    # the newest quarantined copy (from this load) must survive
    assert any(int(p.rsplit("-", 1)[1]) > stamps[-1] for p in remaining)
    for t in stamps[:2]:                        # oldest two pruned
        assert not any(p.endswith(f"corrupt-{t}") for p in remaining)


def test_migration_applied_and_resaved(home):
    store = persistence.PersistentStore(
        "twin", schema_version=2,
        migrations={1: lambda d: {"bumped": d["v"] + 1}})
    _write_envelope(store.path, 1, {"v": 1})
    assert store.load() == {"bumped": 2}
    envelope = persistence.read_json(store.path)
    assert envelope["schema_version"] == 2      # re-saved at current version
    assert envelope["checksum"] == _checksum({"bumped": 2})
    assert envelope["data"] == {"bumped": 2}


def test_missing_migration_fails_closed(home):
    store = persistence.PersistentStore("twin", schema_version=2)
    _write_envelope(store.path, 1, {"v": 1})
    assert store.load() == {}
    assert "missing migration" in store.last_corruption["reason"]
    assert persistence.read_json(store.path)["schema_version"] == 1


def test_newer_schema_returns_empty_with_reason(home):
    store = persistence.get_store("twin")
    _write_envelope(store.path, persistence.SCHEMA_VERSION + 5, {"v": 1})
    assert store.load() == {}
    assert store.last_corruption["reason"].startswith("newer schema")


def test_store_status_fields(home):
    store = persistence.get_store("twin")
    missing = store.status()
    assert missing["exists"] is False
    assert missing["size_bytes"] == 0
    assert missing["records"] == 0
    store.save({"a": 1, "b": 2})
    status = store.status()
    assert status["name"] == "twin"
    assert status["exists"] is True
    assert status["size_bytes"] > 0
    assert status["schema_version"] == persistence.SCHEMA_VERSION
    assert status["checksum_ok"] is True
    assert status["records"] == 2
    assert status["mtime_iso"] is not None
    assert status["corrupted_last_load"] is False


# ---------------------------------------------------------------------------
# export / reset / delete + memory facades
# ---------------------------------------------------------------------------


def test_export_to_portable_copy(home, tmp_path):
    store = persistence.get_store("skills")
    store.save({"skill": "wave"})
    target = tmp_path / "skills-export.json"
    out = store.export_to(str(target))
    assert os.path.abspath(out) == str(target)
    payload = persistence.read_json(out)
    assert payload["data"] == {"skill": "wave"}
    assert payload["schema_version"] == persistence.SCHEMA_VERSION


def test_export_refuses_overwrite_unless_explicit(home, tmp_path):
    store = persistence.get_store("skills")
    target = tmp_path / "export.json"
    target.write_text('{"precious": true}', encoding="utf-8")
    with pytest.raises(FileExistsError):
        store.export_to(str(target))
    store.export_to(str(target), overwrite=True)
    assert persistence.read_json(str(target))["name"] == "skills"


def test_export_rejects_dotdot_path(home, tmp_path):
    store = persistence.get_store("skills")
    with pytest.raises(ValueError):
        store.export_to(str(tmp_path / "sub" / ".." / "escape.json"))
    with pytest.raises(ValueError):
        persistence.memory_export(str(tmp_path / "x" / ".." / "y.json"))


def test_reset_backs_up_and_clears(home):
    store = persistence.get_store("vocabulary")
    store.save({"words": ["alpha", "beta"]})
    result = store.reset()
    assert result["cleared"] is True
    assert result["backup"] is not None
    backup = result["backup"]
    assert backup.startswith(os.path.join(str(home), "backups") + os.sep)
    # the backup preserves the original envelope bytes
    assert persistence.read_json(backup)["data"] == {"words": ["alpha",
                                                               "beta"]}
    assert store.load() == {}                   # cleared
    assert store.status()["records"] == 0


def test_delete_removes_store_and_corrupt_leftovers_keeps_backups(home):
    store = persistence.get_store("twin")
    store.save({"x": 1})
    backup_path = os.path.join(str(home), "backups", "twin-999.json")
    persistence.atomic_write_json(backup_path, {"backup": True})
    with open(store.path + ".corrupt-123", "wb") as f:
        f.write(b"junk")
    assert store.delete() is True
    assert not os.path.exists(store.path)
    assert glob.glob(store.path + ".corrupt-*") == []
    assert os.path.exists(backup_path)          # backups deliberately kept
    assert store.delete() is False              # already gone


def test_memory_status_shape(home):
    persistence.ensure_dirs()
    for name in persistence.STORE_NAMES:
        persistence.get_store(name).save({})
    status = persistence.memory_status()
    assert status["home"] == str(home)
    assert set(status["stores"]) == set(persistence.STORE_NAMES)
    for name, info in status["stores"].items():
        assert info["name"] == name
        assert info["exists"] is True           # storage step created them
        assert "checksum_ok" in info and "records" in info


def test_memory_export_bundles_all_stores(home, tmp_path):
    persistence.get_store("twin").save({"t": 1})
    persistence.get_store("skills").save({"s": 2})
    target = tmp_path / "bundle.json"
    result = persistence.memory_export(str(target))
    assert result["path"] == str(target)
    assert sorted(result["stores"]) == sorted(persistence.STORE_NAMES)
    bundle = persistence.read_json(str(target))
    assert bundle["format"] == "airmouse-memory-export"
    assert bundle["stores"]["twin"]["data"] == {"t": 1}
    assert bundle["stores"]["skills"]["data"] == {"s": 2}
    assert bundle["stores"]["vocabulary"]["data"] == {}
    with pytest.raises(FileExistsError):
        persistence.memory_export(str(target))  # no silent overwrite


def test_memory_reset_and_delete_summaries(home):
    persistence.get_store("twin").save({"t": 1})
    persistence.get_store("workflows").save({"w": [1, 2]})
    reset = persistence.memory_reset()
    assert reset["backups_kept"] is True
    assert reset["stores"]["twin"]["cleared"] is True
    assert reset["stores"]["twin"]["backup"] is not None
    assert persistence.get_store("twin").load() == {}
    backups = glob.glob(os.path.join(str(home), "backups", "*.json"))
    assert len(backups) >= 2

    deleted = persistence.memory_delete()
    assert deleted["backups_kept"] is True
    assert "NOT deleted" in deleted["note"] or "kept" in deleted["note"]
    assert all(v["deleted"] for v in deleted["stores"].values())
    assert persistence.memory_status()["stores"]["twin"]["exists"] is False
    assert len(glob.glob(os.path.join(str(home), "backups", "*.json"))) >= 2


# ---------------------------------------------------------------------------
# privacy_report()
# ---------------------------------------------------------------------------


def test_privacy_report_shape(home):
    report = privacy.privacy_report()
    for key in ("telemetry_state", "network_state", "storage",
                "learned_data", "model_state", "controls"):
        assert key in report
    assert report["network_state"]["posture"] == "local-only"
    assert report["network_state"]["cloud"] is False


def test_telemetry_default_off(home):
    """Assert against the REAL config default — no config file present."""
    from airmouse import config as airmouse_config
    assert airmouse_config.Config.telemetry_enabled is False  # code default
    assert not os.path.exists(os.path.join(str(home), "config.toml"))
    report = privacy.privacy_report()
    telemetry = report["telemetry_state"]
    assert telemetry["enabled"] is False        # actual reported value
    assert telemetry["default_in_code"] is True


def test_privacy_report_storage_learned_and_controls(home):
    persistence.get_store("twin").save({"fact": 1})
    report = privacy.privacy_report()
    assert report["storage"]["home"] == str(home)
    learned = report["learned_data"]
    assert learned["content_included"] is False
    assert learned["stores"]["twin"] == {"exists": True, "records": 1}
    assert learned["stores"]["vocabulary"]["records"] == 0
    assert "airmouse memory status" in report["controls"]
    assert "airmouse privacy" in report["controls"]
    # store content must never leak into the report: only exists/counts
    assert all(set(v) <= {"exists", "records"}
               for v in learned["stores"].values())


# ---------------------------------------------------------------------------
# setup wizard — report primitives
# ---------------------------------------------------------------------------


def test_setup_report_remaining_and_format():
    report = SetupReport(steps=[
        SetupStep("a", "A", "DONE", "fine"),
        SetupStep("b", "B", "SKIPPED", "meh", "run this"),
        SetupStep("c", "C", "ACTION_REQUIRED", "hardware"),
        SetupStep("d", "D", "FAILED", "boom", "fix it"),
    ])
    assert [s.id for s in report.remaining()] == ["b", "c", "d"]
    text = report.format()
    assert "What remains to test" in text
    assert "FAILED" in text and "run this" in text


def test_run_setup_step_ids_fixed(home, quiet_out):
    report = setup_wizard.run_setup(interactive=False, out=quiet_out)
    assert tuple(s.id for s in report.steps) == setup_wizard.STEP_IDS


# ---------------------------------------------------------------------------
# setup wizard — full flow (non-interactive)
# ---------------------------------------------------------------------------


def test_run_setup_full_flow_noninteractive(home, quiet_out):
    report = setup_wizard.run_setup(interactive=False, out=quiet_out)
    by_id = {s.id: s for s in report.steps}

    assert by_id["env"].status == "DONE"
    assert by_id["storage"].status == "DONE"
    assert str(home) in by_id["storage"].detail
    assert by_id["config"].status == "DONE"
    assert by_id["browsers"].status == "DONE"   # simulated bridge fallback
    assert by_id["marker"].status == "DONE"
    assert by_id["cameras"].status in ("DONE", "ACTION_REQUIRED")
    assert by_id["microphones"].status in ("DONE", "ACTION_REQUIRED",
                                           "SKIPPED")
    assert by_id["permissions"].status in ("DONE", "ACTION_REQUIRED")
    assert by_id["asr"].status in ("DONE", "SKIPPED")

    # storage initialised all five stores
    for name in persistence.STORE_NAMES:
        assert persistence.get_store(name).exists()

    # marker written + exposed via setup_complete()
    assert (home / ".setup_complete").exists()
    assert setup_wizard.setup_complete() is True

    formatted = report.format()
    assert "What remains to test" in formatted
    assert "Traceback" not in formatted
    out_text = quiet_out.getvalue()
    assert "Checking Python" in out_text        # plain-language progress


def test_run_setup_creates_default_config_never_overwrites(home, quiet_out):
    setup_wizard.run_setup(interactive=False, out=quiet_out)
    cfg = home / "config.toml"
    assert cfg.exists()                         # created inside AIRMOUSE_HOME
    cfg.write_text('# my custom edit\n[voice]\nenabled = false\n',
                   encoding="utf-8")
    report = setup_wizard.run_setup(interactive=False, out=quiet_out)
    step = report.get("config")
    assert step.status == "DONE"
    assert "not modified" in step.detail
    assert "# my custom edit" in cfg.read_text(encoding="utf-8")


def test_setup_complete_roundtrip(home):
    assert setup_wizard.setup_complete() is False
    path = setup_wizard.mark_setup_complete()
    assert os.path.exists(path)
    assert setup_wizard.setup_complete() is True
    payload = persistence.read_json(path)
    assert "marked_at" in payload               # atomic JSON, not partial


# ---------------------------------------------------------------------------
# setup wizard — dependency consent contract
# ---------------------------------------------------------------------------


@pytest.fixture()
def forbid_subprocess(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called")
    monkeypatch.setattr(setup_wizard.subprocess, "run", _boom)


def test_no_install_without_consent(home, quiet_out, forbid_subprocess,
                                    monkeypatch):
    monkeypatch.setattr(setup_wizard, "_missing_required_deps",
                        lambda: ["numpy"])
    report = setup_wizard.run_setup(interactive=False, out=quiet_out)
    step = report.get("deps")
    assert step.status == "SKIPPED"
    assert "consent" in step.detail
    assert "pip install" in step.remediation
    assert "--disable-pip-version-check" in step.remediation


def test_install_ok_true_uses_exact_pip_argv_and_verifies(home, quiet_out,
                                                          monkeypatch):
    calls = []

    def fake_run(cmd, timeout=None):
        calls.append((list(cmd), timeout))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(setup_wizard, "_missing_required_deps",
                        lambda: ["numpy"])  # fake missing dep
    monkeypatch.setattr(setup_wizard.subprocess, "run", fake_run)

    report = setup_wizard.run_setup(interactive=False, install_ok=True,
                                    out=quiet_out)
    assert len(calls) == 1
    cmd, timeout = calls[0]
    assert cmd == [sys.executable, "-m", "pip", "install",
                   "--disable-pip-version-check", "numpy"]
    assert timeout == 600
    # verification ran and honestly failed (nothing was really installed)
    step = report.get("deps")
    assert step.status == "FAILED"
    assert "still missing" in step.detail
    assert "pip install" in step.remediation


def test_interactive_prompt_accepted(home, monkeypatch):
    calls = []

    def fake_run(cmd, timeout=None):
        calls.append(list(cmd))
        return types.SimpleNamespace(returncode=0)

    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return "Y"

    monkeypatch.setattr(setup_wizard, "_missing_required_deps",
                        lambda: ["numpy"])
    monkeypatch.setattr(setup_wizard.subprocess, "run", fake_run)
    setup_wizard.run_setup(interactive=True, install_ok=None,
                           out=io.StringIO(), input_fn=fake_input)
    assert prompts == ["Install missing required packages? [Y/N] "]
    assert calls == [[sys.executable, "-m", "pip", "install",
                      "--disable-pip-version-check", "numpy"]]


def test_interactive_prompt_declined(home, forbid_subprocess, monkeypatch):
    monkeypatch.setattr(setup_wizard, "_missing_required_deps",
                        lambda: ["numpy"])
    report = setup_wizard.run_setup(
        interactive=True, install_ok=None, out=io.StringIO(),
        input_fn=lambda prompt="": "n")
    step = report.get("deps")
    assert step.status == "SKIPPED"
    assert "pip install" in step.remediation


def test_assume_yes_skips_prompt(home, monkeypatch):
    calls = []

    def fake_run(cmd, timeout=None):
        calls.append(list(cmd))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(setup_wizard, "_missing_required_deps",
                        lambda: ["numpy"])
    monkeypatch.setattr(setup_wizard.subprocess, "run", fake_run)

    def exploding_input(prompt=""):
        raise AssertionError("assume_yes must not prompt")

    setup_wizard.run_setup(interactive=True, assume_yes=True,
                           out=io.StringIO(), input_fn=exploding_input)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# setup wizard — hostile environments must never raise
# ---------------------------------------------------------------------------


def test_cv2_import_failure_still_completes(home, quiet_out, monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)   # forces ImportError
    report = setup_wizard.run_setup(interactive=False, out=quiet_out)
    cameras = report.get("cameras")
    assert cameras.status == "ACTION_REQUIRED"
    assert "opencv" in (cameras.detail + cameras.remediation).lower()
    assert "Traceback" not in quiet_out.getvalue()
    assert report.get("marker").status == "DONE"    # flow completed anyway


def test_run_setup_never_raises_when_smoke_probe_breaks(home, quiet_out,
                                                        monkeypatch):
    def broken_self_test(intelligence=True):
        raise RuntimeError("simulated total breakdown")

    import airmouse.selftest as selftest_module
    monkeypatch.setattr(selftest_module, "run_self_test", broken_self_test)
    report = setup_wizard.run_setup(interactive=False, out=quiet_out)
    smoke = report.get("smoke")
    assert smoke.status == "FAILED"
    assert "simulated total breakdown" in smoke.detail
    assert setup_wizard.setup_complete() is True    # marker still written


def test_deps_step_failure_reported_not_raised(home, monkeypatch):
    def fake_run(cmd, timeout=None):
        raise OSError("pip exploded")

    monkeypatch.setattr(setup_wizard, "_missing_required_deps",
                        lambda: ["numpy"])
    monkeypatch.setattr(setup_wizard.subprocess, "run", fake_run)
    report = setup_wizard.run_setup(interactive=False, install_ok=True,
                                    out=io.StringIO())
    step = report.get("deps")
    assert step.status == "FAILED"
    assert "pip install" in step.remediation


def test_importlib_util_submodule_is_imported_explicitly():
    """Regression guard: bare ``import importlib`` does NOT load the
    importlib.util submodule, which silently broke the find_spec probes
    in fresh processes (found in manual review).  Under pytest the bug
    is masked because pytest itself imports importlib.util, so this is
    checked statically."""
    import inspect
    src = inspect.getsource(setup_wizard)
    assert "import importlib.util" in src
