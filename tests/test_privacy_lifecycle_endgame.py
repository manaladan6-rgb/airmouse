"""Task 2-a — UNIFIED HOME + REAL PRIVACY LIFECYCLE (endgame tests).

Proves, end to end, that:

1. ``airmouse.paths`` is the ONE authoritative resolver and is dynamic
   (``$AIRMOUSE_HOME`` may be set after import).
2. ``memory_reset`` / ``memory_delete`` / ``memory_export`` cover the
   REAL user-learning artifacts (intelligence jsons + model.bin,
   calibration.json, gaze_calibration.json, gestures.json, macros/,
   lecture.md) — not just the five named stores — with honest backups
   under ``<home>/backups/lifecycle-<epoch>/``.
3. ``deletion_verifies()`` proves nothing learnable remains.
4. ``PRIVACY_MANIFEST`` inventories every artifact honestly.
5. The migrated modules (intelligence plugin, calibration, macros,
   gestures registry, gaze calibration) all write under the override.
6. Store corruption recovery (checksum → quarantine) still works.

Offline, headless, deterministic.  Every test isolates storage with
monkeypatch.setenv("AIRMOUSE_HOME", tmp_path); the real ~/.airmouse is
never touched.  No network calls are made.
"""

from __future__ import annotations

import base64
import glob
import json
import os

import numpy as np
import pytest

from airmouse import calibration, gaze_calibration, macros, paths, persistence
from airmouse import privacy
from airmouse.gesture_registry import (CustomGestureMapping, GestureRegistry,
                                       IntentType)
from airmouse.interfaces import GazeSample
from airmouse.intelligence.plugin import IntelligencePlugin

# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------

_REQUIRED_MANIFEST_NAMES = {
    "twin_store", "vocabulary_store", "skills_store", "workflows_store",
    "preferences_store",
    "intelligence_memory", "intelligence_vocabulary",
    "intelligence_workflows", "intelligence_selftune",
    "intelligence_model",
    "hand_calibration", "gaze_calibration", "custom_gestures", "macros",
    "lecture_notes",
}

# the NON-store learning artifacts the reset/delete artifact section covers
_REQUIRED_ARTIFACT_NAMES = _REQUIRED_MANIFEST_NAMES - {
    "twin_store", "vocabulary_store", "skills_store", "workflows_store",
    "preferences_store",
}

# manifest entries the lifecycle must KEEP (settings / third-party model /
# markers / backup+export areas — see privacy.PRIVACY_MANIFEST)
_KEEP_NAMES = {"config", "hand_landmarker_model", "tutorial_done",
               "backups", "exports"}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolated AIRMOUSE_HOME pointing at a fresh temp dir."""
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    return tmp_path


def _write(home: str, rel: str, data: bytes) -> str:
    """Write bytes under the (override) home; return the path."""
    p = os.path.join(home, rel)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _create_all_learning_artifacts(home) -> dict:
    """Seed fake content at EVERY learning-artifact location.

    Returns {home-relative path: bytes written} for the raw files
    (store envelopes are written via the store API instead).
    """
    h = str(home)
    raw = {
        "intelligence/memory.json":
            b'{"patterns": {"action:click": 3}}',
        "intelligence/vocabulary.json":
            b'{"terms": {"naive": 2}}',
        "intelligence/workflows.json":
            b'{"sequences": []}',
        "intelligence/selftune.json":
            b'{"tuned": {}}',
        "intelligence/model.bin":
            b"\x00AMMODEL\x01" + b"\xab" * 128,
        "calibration.json":
            b'{"samples": 90, "min": [0.1, 0.2]}',
        "gaze_calibration.json":
            b'{"version": 2, "matrix": [[1, 0, 0], [0, 1, 0]]}',
        "gestures.json":
            b'{"version": 1, "mappings": []}',
        "macros/jiggle.json":
            b'{"events": [{"t": 0.0, "event": "click"}]}',
        "macros/zoom.json":
            b'{"events": [{"t": 0.1, "event": "zoom"}]}',
        "lecture.md":
            b"# lecture\nverbatim user notes live here\n",
        # v16.5 artifacts (privacy manifest: academy_progress /
        # onboarding_state / personal_profile — all user_learning=True,
        # so the lifecycle must delete them)
        "academy_progress.json":
            b'{"move": {"passes": 2, "attempts": 3}}',
        "profile/onboarding.json":
            b'{"phase": "IN_PROGRESS", "voice": false, "gaze": false}',
        "profile/interaction.json":
            b'{"frequent_intents": {"left_click": 12}}',
        "profile/voice.json":
            b'{"commands": {"click": 9}, "aliases": {}}',
        "profile/gaze.json":
            b'{"dwell": 0.8, "quality": 0.91}',
        "profile/gestures.json":
            b'{"preferred": ["pinch", "peace"]}',
        "profile/preferences.json":
            b'{"hint_level": "compact"}',
    }
    for rel, data in raw.items():
        _write(h, rel, data)
    # 1. the five named stores (written through the real store API)
    for i, name in enumerate(persistence.STORE_NAMES):
        persistence.get_store(name).save({"seed": i, "user": True})
    return raw


def _create_keep_artifacts(home) -> dict:
    """Seed the manifest entries the lifecycle must NOT touch."""
    h = str(home)
    return {
        "config.toml": _write(h, "config.toml", b"[input]\ndwell = 0.8\n"),
        "hand_landmarker.task": _write(
            h, "hand_landmarker.task", b"\x00\x01third-party-model"),
        "tutorial_done": _write(h, "tutorial_done", b""),
        # v16.5: user-saved transcripts are user-owned copies (not
        # learning data) — the lifecycle must leave them alone
        "transcripts/session-1.json": _write(
            h, "transcripts/session-1.json",
            b'{"segments": [{"text": "saved by the user"}]}'),
    }


def _backup_copies(home, rel: str) -> list:
    pattern = os.path.join(str(home), "backups", "lifecycle-*", rel)
    return glob.glob(pattern)


# ---------------------------------------------------------------------------
# 1. paths.py — the one authoritative resolver
# ---------------------------------------------------------------------------


def test_paths_api_contract(home):
    """Exact public API the coordinator's integration code imports."""
    h = str(home)
    assert callable(paths.airmouse_home) and callable(paths.ensure_home)
    assert paths.airmouse_home() == h
    assert paths.intelligence_dir() == os.path.join(h, "intelligence")
    assert paths.calibration_file() == os.path.join(h, "calibration.json")
    assert paths.gaze_calibration_file() == \
        os.path.join(h, "gaze_calibration.json")
    assert paths.gestures_file() == os.path.join(h, "gestures.json")
    assert paths.macros_dir() == os.path.join(h, "macros")
    # matches the real tracker.py location: <home>/hand_landmarker.task
    assert paths.model_file() == os.path.join(h, "hand_landmarker.task")
    assert paths.lecture_file() == os.path.join(h, "lecture.md")
    assert paths.tutorial_done_file() == os.path.join(h, "tutorial_done")
    assert paths.config_file() == os.path.join(h, "config.toml")
    for name in ("airmouse_home", "ensure_home", "intelligence_dir",
                 "calibration_file", "gaze_calibration_file",
                 "gestures_file", "macros_dir", "model_file",
                 "lecture_file", "tutorial_done_file", "config_file"):
        assert hasattr(paths, name), name


def test_paths_resolve_dynamically_after_import(tmp_path, monkeypatch):
    """AIRMOUSE_HOME may be set AFTER import — every call re-resolves."""
    monkeypatch.delenv("AIRMOUSE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.airmouse_home() == os.path.join(str(tmp_path), ".airmouse")
    assert paths.macros_dir() == \
        os.path.join(str(tmp_path), ".airmouse", "macros")

    override = str(tmp_path / "override")
    monkeypatch.setenv("AIRMOUSE_HOME", override)
    assert paths.airmouse_home() == override          # absolute
    # every resolver follows the override live
    assert paths.calibration_file().startswith(override)
    assert paths.intelligence_dir().startswith(override)
    assert persistence.airmouse_home() == override    # persistence delegates
    assert macros._macro_dir() == os.path.join(override, "macros")
    assert calibration._calibration_path() == \
        os.path.join(override, "calibration.json")
    assert gaze_calibration._default_gaze_path() == \
        os.path.join(override, "gaze_calibration.json")
    # ~-expansion + strip semantics preserved
    monkeypatch.setenv("AIRMOUSE_HOME", " ~/relative ")
    assert paths.airmouse_home() == \
        os.path.join(str(tmp_path), "relative")
    # empty value falls back to ~/.airmouse
    monkeypatch.setenv("AIRMOUSE_HOME", "   ")
    assert paths.airmouse_home() == os.path.join(str(tmp_path), ".airmouse")


def test_ensure_home_creates_but_resolvers_do_not(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path / "fresh"))
    target = str(tmp_path / "fresh")
    assert not os.path.exists(target)
    paths.intelligence_dir()
    paths.calibration_file()
    paths.macros_dir()
    assert not os.path.exists(target)          # resolvers never mkdir
    assert paths.ensure_home() == target
    assert os.path.isdir(target)
    paths.ensure_home()                        # idempotent
    assert os.path.isdir(target)


def test_persistence_home_is_the_paths_resolver(home, monkeypatch):
    """ONE resolution: persistence.airmouse_home() == paths.airmouse_home()."""
    assert persistence.airmouse_home() == paths.airmouse_home()
    other = str(home / "elsewhere")
    monkeypatch.setenv("AIRMOUSE_HOME", other)
    assert persistence.airmouse_home() == paths.airmouse_home() == other


# ---------------------------------------------------------------------------
# 2. PRIVACY MANIFEST
# ---------------------------------------------------------------------------


def test_manifest_covers_all_learning_artifacts(home):
    rows = privacy.privacy_manifest()
    assert len(rows) >= 10
    names = {r["name"] for r in rows}
    assert _REQUIRED_MANIFEST_NAMES <= names
    for row in rows:
        # every entry carries the honest metadata contract
        for key in ("name", "purpose", "path", "kind", "data_type",
                    "created_by", "read_by", "deleted_by", "exported_by",
                    "user_learning", "exists"):
            assert key in row, (row["name"], key)
        assert isinstance(row["data_type"], str) and row["data_type"]
        assert isinstance(row["user_learning"], bool)
        assert isinstance(row["exists"], bool)


def test_manifest_paths_live_under_override_and_resolve(home):
    h = str(home)
    _create_all_learning_artifacts(home)
    _create_keep_artifacts(home)
    rows = privacy.privacy_manifest()
    assert len(rows) >= 10
    for row in rows:
        # no resolve errors and no raising anywhere
        assert "resolve_error" not in row, row
        assert isinstance(row["path"], str) and row["path"]
        assert row["path"].startswith(h), row   # AIRMOUSE_HOME honored
    resolved = {r["name"]: r for r in rows}
    must_exist = (_KEEP_NAMES & {"config", "hand_landmarker_model",
                                 "tutorial_done"}) | \
        {"twin_store", "vocabulary_store", "skills_store",
         "workflows_store", "preferences_store"}
    for name in must_exist:
        assert resolved[name]["exists"] is True, name
    # backup/export AREAS may legitimately not exist yet — they still
    # resolve without raising
    for name in ("backups", "exports"):
        assert isinstance(resolved[name]["exists"], bool), name
    assert resolved["intelligence_model"]["exists"] is True
    assert resolved["intelligence_model"]["size_bytes"] > 0
    assert resolved["macros"]["kind"] == "dir"
    assert resolved["macros"]["entries"] == 2
    assert resolved["hand_calibration"]["exists"] is True
    assert resolved["custom_gestures"]["exists"] is True


def test_manifest_gestures_honours_env_override(home, monkeypatch):
    """The registry persists to $AIRMOUSE_GESTURES when set — the manifest
    must reflect that REAL location (honesty about where data lives)."""
    env_path = str(home / "custom" / "my-gestures.json")
    monkeypatch.setenv("AIRMOUSE_GESTURES", env_path)
    row = {r["name"]: r for r in privacy.privacy_manifest()}["custom_gestures"]
    assert row["path"] == env_path
    monkeypatch.delenv("AIRMOUSE_GESTURES")
    row = {r["name"]: r for r in privacy.privacy_manifest()}["custom_gestures"]
    assert row["path"] == paths.gestures_file()


def test_manifest_never_raises_with_hostile_env(home, monkeypatch):
    """Home pointing at a FILE must not break manifest resolution."""
    blocker = home / "not-a-dir"
    blocker.write_text("i am a file", encoding="utf-8")
    monkeypatch.setenv("AIRMOUSE_HOME", str(blocker))
    rows = privacy.privacy_manifest()
    assert rows
    for row in rows:
        assert isinstance(row["exists"], bool)


# ---------------------------------------------------------------------------
# 3. memory_reset — REAL artifacts cleared + backed up
# ---------------------------------------------------------------------------


def test_memory_reset_clears_every_learning_artifact(home):
    seeded = _create_all_learning_artifacts(home)
    keep = _create_keep_artifacts(home)
    assert persistence.deletion_verifies()["clean"] is False  # dirty start

    result = persistence.memory_reset()

    # -- existing 5-store contract still holds -----------------------------
    assert result["backups_kept"] is True
    assert set(result["stores"]) == set(persistence.STORE_NAMES)
    for name, entry in result["stores"].items():
        assert entry["cleared"] is True, name
        assert entry["backup"], name
        assert persistence.get_store(name).load() == {}
    flat_backups = glob.glob(os.path.join(str(home), "backups", "*.json"))
    assert len(flat_backups) >= len(persistence.STORE_NAMES)

    # -- NEW artifacts section: every learning artifact cleared ------------
    artifacts = {a["name"]: a for a in result["artifacts"]}
    assert _REQUIRED_ARTIFACT_NAMES <= set(artifacts)
    assert artifacts["intelligence_memory"]["cleared"] is True
    assert artifacts["intelligence_model"]["cleared"] is True
    assert artifacts["hand_calibration"]["cleared"] is True
    assert artifacts["gaze_calibration"]["cleared"] is True
    assert artifacts["custom_gestures"]["cleared"] is True
    assert artifacts["macros"]["cleared"] is True
    assert artifacts["lecture_notes"]["cleared"] is True
    for entry in artifacts.values():
        assert entry["cleared"] is True, entry
        assert "error" not in entry, entry

    # -- files really gone from the live home -------------------------------
    for rel in seeded:
        assert not os.path.exists(os.path.join(str(home), rel)), rel
    assert os.path.isdir(os.path.join(str(home), "macros"))  # dir kept, empty
    assert glob.glob(os.path.join(str(home), "macros", "*.json")) == []

    # -- backups exist and hold byte-identical copies ------------------------
    assert result["artifacts_backup_dir"].startswith(
        os.path.join(str(home), "backups", "lifecycle-"))
    for rel in ("intelligence/model.bin", "intelligence/memory.json",
                "calibration.json", "gaze_calibration.json",
                "gestures.json", "macros/jiggle.json", "macros/zoom.json",
                "lecture.md"):
        copies = _backup_copies(home, rel)
        assert copies, rel
        assert _read(copies[0]) == seeded[rel]

    # -- non-learning entries are untouched ----------------------------------
    for rel, path in keep.items():
        assert os.path.exists(path), rel

    # -- post-reset verification is clean ------------------------------------
    verdict = persistence.deletion_verifies()
    assert verdict["clean"] is True, verdict["remaining"]
    assert verdict["checked"] >= 15


def test_memory_reset_with_nothing_to_clear_is_honest(home):
    persistence.ensure_dirs()
    result = persistence.memory_reset()
    assert set(result["stores"]) == set(persistence.STORE_NAMES)
    assert all(e["cleared"] for e in result["stores"].values())
    assert result["artifacts"]
    for entry in result["artifacts"]:
        assert entry["existed"] is False
        assert entry["cleared"] is True
        assert "error" not in entry
    assert persistence.deletion_verifies()["clean"] is True


# ---------------------------------------------------------------------------
# 4. memory_delete — artifacts removed, backups kept
# ---------------------------------------------------------------------------


def test_memory_delete_removes_artifacts_and_keeps_backups(home):
    seeded = _create_all_learning_artifacts(home)
    keep = _create_keep_artifacts(home)
    pre_backup = _write(str(home), "backups/manual-keep.json",
                        b'{"i": "was here before"}')

    result = persistence.memory_delete()

    assert result["backups_kept"] is True
    assert all(v["deleted"] for v in result["stores"].values())
    artifacts = {a["name"]: a for a in result["artifacts"]}
    for name, entry in artifacts.items():
        assert entry["deleted"] is True, entry
        assert "error" not in entry, entry
    for rel in seeded:
        assert not os.path.exists(os.path.join(str(home), rel)), rel

    # backups (flat + manual) survive untouched
    assert os.path.exists(pre_backup)
    assert _read(pre_backup) == b'{"i": "was here before"}'
    assert glob.glob(os.path.join(str(home), "backups", "*.json"))

    # non-learning entries untouched
    for rel, path in keep.items():
        assert os.path.exists(path), rel

    verdict = persistence.deletion_verifies()
    assert verdict["clean"] is True, verdict["remaining"]

    # deleting again is honestly a no-op (mirrors store behaviour)
    again = persistence.memory_delete()
    assert all(v["deleted"] is False for v in again["stores"].values())
    assert all(a["deleted"] is False for a in again["artifacts"])


def test_deletion_verifies_reports_dirty_state_honestly(home):
    _create_all_learning_artifacts(home)
    verdict = persistence.deletion_verifies()
    assert verdict["clean"] is False
    remaining_paths = {r["path"] for r in verdict["remaining"]}
    assert os.path.join(str(home), "intelligence", "model.bin") \
        in remaining_paths
    assert os.path.join(str(home), "calibration.json") in remaining_paths
    assert os.path.join(str(home), "lecture.md") in remaining_paths
    # every store with records is reported
    store_hits = [r for r in verdict["remaining"] if r["kind"] == "store"]
    assert len(store_hits) == len(persistence.STORE_NAMES)
    # a quarantined corrupt store copy also counts as remaining data
    persistence.get_store("twin").save({"x": 1})
    corrupt = persistence.get_store("twin").path + ".corrupt-123"
    _write(str(home), os.path.relpath(corrupt, str(home)), b"garbage")
    verdict = persistence.deletion_verifies()
    assert any(r["kind"] == "store" and r["path"] == corrupt
               for r in verdict["remaining"])


# ---------------------------------------------------------------------------
# 5. memory_export — stores + artifacts bundled
# ---------------------------------------------------------------------------


def test_memory_export_bundles_stores_and_artifacts(home, tmp_path):
    seeded = _create_all_learning_artifacts(home)
    target = tmp_path / "bundle.json"

    result = persistence.memory_export(str(target))

    # existing store bundle contract still holds
    assert result["path"] == str(target)
    assert sorted(result["stores"]) == sorted(persistence.STORE_NAMES)
    bundle = persistence.read_json(str(target))
    assert bundle["format"] == "airmouse-memory-export"
    assert bundle["stores"]["twin"]["data"] == {"seed": 0, "user": True}

    # NEW: artifacts bundled, count reported, nothing redacted away
    assert result["artifacts"] >= 10          # learning artifact entries
    assert result["artifacts_captured"] >= 11  # model.bin + jsons + macros…
    by_path = {a["path"]: a for a in bundle["artifacts"]}
    model_entry = by_path[os.path.join(str(home), "intelligence/model.bin")]
    assert model_entry["encoding"] == "base64"
    assert base64.b64decode(model_entry["data"]) == \
        seeded["intelligence/model.bin"]
    macro_entry = by_path[os.path.join(str(home), "macros/jiggle.json")]
    assert base64.b64decode(macro_entry["data"]) == seeded["macros/jiggle.json"]
    assert by_path[os.path.join(str(home), "lecture.md")]["exists"] is True


def test_memory_export_with_missing_artifacts_reports_them(home, tmp_path):
    persistence.ensure_dirs()
    target = tmp_path / "thin.json"
    result = persistence.memory_export(str(target))
    bundle = persistence.read_json(str(target))
    missing = [a for a in bundle["artifacts"] if not a["exists"]]
    assert missing, "absent artifacts must be reported, not silently dropped"
    assert result["artifacts_captured"] == 0


# ---------------------------------------------------------------------------
# 6. AIRMOUSE_HOME override end-to-end via the MIGRATED modules
# ---------------------------------------------------------------------------


def test_intelligence_plugin_saves_and_loads_under_override(home):
    plugin = IntelligencePlugin()
    assert plugin.available is True
    assert plugin.base_dir == paths.intelligence_dir()
    plugin.record_action("click")
    plugin.record_command("open browser")
    plugin.record_text("hello there")
    saved = plugin.save()
    assert saved.get("model", 0) > 0
    h = str(home)
    for rel in ("intelligence/model.bin", "intelligence/memory.json",
                "intelligence/vocabulary.json", "intelligence/workflows.json",
                "intelligence/selftune.json"):
        assert os.path.exists(os.path.join(h, rel)), rel

    # a fresh plugin (same process, same env) round-trips from the override
    plugin2 = IntelligencePlugin()
    assert plugin2.available is True
    assert plugin2.memory is not None and plugin2.memory.size() >= 1


def test_calibration_module_learns_and_persists_under_override(home):
    cal = calibration.AdaptiveCalibration()
    for i in range(60):
        x = 0.2 + (i % 50) / 100.0
        y = 0.3 + ((i * 7) % 40) / 100.0
        cal.update(np.array([x, y]))
    assert cal.save() is True
    target = os.path.join(str(home), "calibration.json")
    assert os.path.exists(target)
    with open(target, "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["samples"] == 60

    fresh = calibration.AdaptiveCalibration()
    assert fresh.samples == 60
    assert fresh.is_ready
    assert fresh.load() is True


def test_macros_module_records_and_persists_under_override(home):
    rec = macros.MacroRecorder()
    assert rec.start("jiggle") is True
    rec.record("click")
    rec.record("scroll", amount=3)
    rec.stop()
    path = rec.save()
    assert path == os.path.join(str(home), "macros", "jiggle.json")
    assert os.path.exists(path)
    assert macros.list_macros() == ["jiggle"]

    player = macros.MacroPlayer(lambda event, params: None)
    loaded = player.load("jiggle")
    assert loaded["name"] == "jiggle"

    # MACRO_DIR monkeypatch-style overrides still win (backwards compat)
    alt = os.path.join(str(home), "alt")
    old = macros.MACRO_DIR
    macros.MACRO_DIR = alt
    try:
        assert macros.list_macros() == []
    finally:
        macros.MACRO_DIR = old
    assert macros.list_macros() == ["jiggle"]   # restored to paths resolution


def test_gestures_registry_persists_under_override(home):
    registry = GestureRegistry()
    assert registry.define(CustomGestureMapping(
        name="air_delete", pattern=["fist", "swipe_left", "pinch_release"],
        intent=IntentType.HOTKEY, params={"keys": ["ctrl", "backspace"]}))
    # explicit authoritative path (registry default env handling is
    # outside this task's file ownership)
    assert registry.save(paths.gestures_file()) is True
    target = os.path.join(str(home), "gestures.json")
    assert os.path.exists(target)

    fresh = GestureRegistry()
    assert fresh.load(paths.gestures_file()) == 1
    assert "air_delete" in fresh.custom


def test_gaze_calibration_module_fits_and_persists_under_override(home):
    gc = gaze_calibration.GazeCalibration(n_points=9)
    assert gc.path == paths.gaze_calibration_file()

    def sampler(target):
        return GazeSample(x=target[0] + 0.001, y=target[1] - 0.001,
                          confidence=0.95)

    quality = gaze_calibration.run_point_calibration(
        gc, sampler, samples_per_point=12)
    assert quality["status"] in ("good", "fair")
    assert gc.save() is True
    target_file = os.path.join(str(home), "gaze_calibration.json")
    assert os.path.exists(target_file)

    fresh = gaze_calibration.GazeCalibration()   # default → paths resolution
    assert fresh.load() is True
    assert fresh.is_calibrated is True
    assert fresh.map(0.5, 0.5) is not None


# ---------------------------------------------------------------------------
# 7. corruption recovery still works after the refactor
# ---------------------------------------------------------------------------


def test_store_checksum_tamper_still_quarantines(home):
    store = persistence.get_store("vocabulary")
    store.save({"word": 1})
    good = store.load()
    assert good == {"word": 1}

    # tamper: flip the payload so the checksum no longer matches
    with open(store.path, "r", encoding="utf-8") as f:
        envelope = json.load(f)
    envelope["data"] = {"word": 999}
    persistence.atomic_write_json(store.path, envelope)

    assert store.load() == {}                    # fail-closed
    assert store.last_corruption is not None
    assert store.last_corruption["reason"] == "checksum mismatch"
    quarantined = glob.glob(store.path + ".corrupt-*")
    assert quarantined, "corrupt file must be quarantined"

    # recovery: the next save is healthy again
    store.save({"word": 2})
    assert store.load() == {"word": 2}
    assert store.last_corruption is None


def test_store_undecodable_json_still_quarantines(home):
    store = persistence.get_store("skills")
    store.save({"skill": 1})
    with open(store.path, "wb") as f:
        f.write(b"{not json at all")
    assert store.load() == {}
    assert store.last_corruption is not None
    assert glob.glob(store.path + ".corrupt-*")


# ---------------------------------------------------------------------------
# 8. privacy_report stays coherent with the manifest
# ---------------------------------------------------------------------------


def test_privacy_report_storage_uses_unified_home(home):
    report = privacy.privacy_report()
    assert report["storage"]["home"] == str(home)
    assert report["network_state"]["cloud"] is False
    # the learned-data section reports the intelligence dir under override
    assert report["learned_data"]["intelligence_artifacts_dir"] is False
    _create_all_learning_artifacts(home)
    report = privacy.privacy_report()
    assert report["learned_data"]["intelligence_artifacts_dir"] is True
