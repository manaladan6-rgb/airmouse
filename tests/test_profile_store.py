"""Task 2-e — tests for airmouse.profile_store (v16.5 Personal
Interaction Profile + §16 learning-loop bookkeeping).

Covers: defaults on missing files, corrupted-file fail-closed recovery,
atomic observe/save, bounds (512-key counter trim keeping highest
counts, last-32 list trim, ratio clamps, 64-char string truncation),
lifecycle-compatible reset with honest backups, export flows,
learn_event routing (modality -> store, kind -> category, unverified
namespace), the mission §27 personalization summary, and the
LearningLoop ring/proposals/approve/expire/adapt machinery with the
hard rule PREDICTION ≠ EXECUTION.

Offline, headless, deterministic.  Every test isolates storage with
monkeypatch.setenv("AIRMOUSE_HOME", tmp_path); the real ~/.airmouse is
never touched.  No network calls are made.
"""

from __future__ import annotations

import glob
import json
import os
import threading
import time

import pytest

from airmouse import paths, profile_store
from airmouse.profile_store import (
    DEFAULTS, PROFILE_FILES, STAGES, UNVERIFIED_PREFIX,
    is_unverified_category, LearningLoop, PersonalProfile, ProfileStore,
)

# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolated AIRMOUSE_HOME pointing at a fresh temp dir."""
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    return tmp_path


def _read_json(path: str):
    with open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def _write_bytes(path: str, data: bytes) -> str:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


# ---------------------------------------------------------------------------
# ProfileStore — defaults / load fail-closed
# ---------------------------------------------------------------------------

_INTERACTION_DEFAULTS = {
    "schema_version": 1,
    "frequent_intents": {},
    "confidence_preferences": {},
    "workflows": {},
    "corrections": {},
    "total_observations": 0,
    "updated_at": None,
}
_VOICE_DEFAULTS = {
    "schema_version": 1,
    "command_counts": {},
    "phrase_aliases": {},
    "dictation_stats": {"sessions": 0, "chars": 0},
    "updated_at": None,
}
_GESTURES_DEFAULTS = {
    "schema_version": 1,
    "gesture_counts": {},
    "preferred_confidence": None,
    "temporal_events": {},
    "updated_at": None,
}
_PREFERENCES_DEFAULTS = {
    "schema_version": 1,
    "hint_level": "normal",
    "preferred_modalities": [],
    "teach_reminders": True,
    "updated_at": None,
}


def test_missing_file_returns_defaults_and_writes_nothing(home):
    store = ProfileStore("interaction")
    assert store.load() == _INTERACTION_DEFAULTS
    assert store.corrupted_last_load is False
    assert not os.path.exists(store.file)          # load never writes
    assert store.file == paths.profile_interaction_file()


def test_defaults_for_all_four_stores(home):
    assert ProfileStore("voice").load() == _VOICE_DEFAULTS
    assert ProfileStore("gestures").load() == _GESTURES_DEFAULTS
    assert ProfileStore("preferences").load() == _PREFERENCES_DEFAULTS
    for name, default in (("interaction", _INTERACTION_DEFAULTS),
                          ("voice", _VOICE_DEFAULTS),
                          ("gestures", _GESTURES_DEFAULTS),
                          ("preferences", _PREFERENCES_DEFAULTS)):
        assert DEFAULTS[name] == default            # module schema constant


def test_defaults_are_fresh_copies(home):
    store = ProfileStore("interaction")
    d1 = store.defaults()
    d1["frequent_intents"]["leak"] = 1
    d1["total_observations"] = 99
    assert store.defaults()["frequent_intents"] == {}
    assert store.defaults()["total_observations"] == 0


def test_corrupted_json_falls_back_with_flag(home):
    store = ProfileStore("interaction")
    _write_bytes(store.file, b"\x00\xff{{{ not json at all")
    assert store.load() == _INTERACTION_DEFAULTS
    assert store.corrupted_last_load is True


def test_non_dict_json_flagged_corrupt(home):
    store = ProfileStore("voice")
    _write_bytes(store.file, b"[1, 2, 3]")
    assert store.load() == _VOICE_DEFAULTS
    assert store.corrupted_last_load is True


def test_directory_in_place_of_file_flagged_not_raising(home):
    store = ProfileStore("gestures")
    os.makedirs(store.file)                    # a dir where the file goes
    assert store.load() == _GESTURES_DEFAULTS  # must not raise
    assert store.corrupted_last_load is True


def test_corrupt_flag_clears_on_recovery(home):
    store = ProfileStore("interaction")
    _write_bytes(store.file, b"garbage{")
    assert store.corrupted_last_load is False   # flag describes LAST load
    store.load()
    assert store.corrupted_last_load is True
    os.remove(store.file)
    store.load()
    assert store.corrupted_last_load is False
    _write_bytes(store.file, json.dumps(
        {"schema_version": 1, "frequent_intents": {"a": 2}}).encode())
    store.load()
    assert store.corrupted_last_load is False


def test_partial_document_merges_over_defaults(home):
    store = ProfileStore("voice")
    _write_bytes(store.file, json.dumps({
        "schema_version": 1,
        "command_counts": {"open browser": 4},
        # wrong-typed fields fall back to defaults:
        "dictation_stats": "oops",
        "schema_bogus": {"nonsense": 1},    # unknown dict -> kept (bounded)
    }).encode())
    data = store.load()
    assert data["command_counts"] == {"open browser": 4}
    assert data["dictation_stats"] == {"sessions": 0, "chars": 0}
    assert data["phrase_aliases"] == {}
    assert data["schema_bogus"] == {"nonsense": 1}


def test_unknown_store_name_rejected(home):
    with pytest.raises(ValueError):
        ProfileStore("diary")
    with pytest.raises(ValueError):
        ProfileStore("")


# ---------------------------------------------------------------------------
# ProfileStore — observe / top / summary
# ---------------------------------------------------------------------------


def test_observe_writes_file_and_increments(home):
    store = ProfileStore("interaction")
    assert store.observe("frequent_intents", "open_browser") is True
    assert os.path.exists(store.file)
    on_disk = _read_json(store.file)
    assert on_disk["frequent_intents"]["open_browser"] == 1
    assert on_disk["total_observations"] == 1
    assert on_disk["updated_at"]
    assert store.corrupted_last_load is False


def test_observe_increments_repeat_calls(home):
    store = ProfileStore("interaction")
    for _ in range(3):
        assert store.observe("frequent_intents", "copy") is True
    data = store.load()
    assert data["frequent_intents"]["copy"] == 3
    assert data["total_observations"] == 3


def test_observe_accepts_fractional_and_unix_now(home):
    store = ProfileStore("gestures")
    assert store.observe("gesture_counts", "pinch", value=0.5,
                         now=1_700_000_000) is True
    data = store.load()
    assert data["gesture_counts"]["pinch"] == 0.5
    assert data["updated_at"].startswith("2023-11-14T22:13:20")


def test_observe_rejects_invalid_input(home):
    store = ProfileStore("interaction")
    assert store.observe("", "key") is False
    assert store.observe("frequent_intents", "") is False
    assert store.observe("frequent_intents", "key", value=-1) is False
    assert store.observe("frequent_intents", "key", value="lots") is False
    assert store.observe("frequent_intents", "key", value=float("nan")) is False
    assert store.observe(None, "key") is False
    assert not os.path.exists(store.file)


def test_observe_refuses_non_counter_field(home):
    store = ProfileStore("preferences")
    assert store.observe("hint_level", "quiet") is False   # a string field
    assert store.load()["hint_level"] == "normal"


def test_top_ordering_limit_and_ties(home):
    store = ProfileStore("interaction")
    store.observe("frequent_intents", "alpha", value=5)
    store.observe("frequent_intents", "beta", value=9)
    store.observe("frequent_intents", "gamma", value=5)
    store.observe("frequent_intents", "delta", value=1)
    assert store.top("frequent_intents", k=3) == [
        ("beta", 9), ("alpha", 5), ("gamma", 5)]
    assert store.top("frequent_intents") == [
        ("beta", 9), ("alpha", 5), ("gamma", 5), ("delta", 1)]


def test_top_missing_or_malformed_category(home):
    store = ProfileStore("interaction")
    assert store.top("frequent_intents") == []
    assert store.top("total_observations") == []   # not a dict
    assert store.top("nope", k=0) == []


def test_store_summary_is_privacy_safe(home):
    store = ProfileStore("interaction")
    store.observe("frequent_intents", "open_browser")
    store.observe("frequent_intents", "copy_paste")
    store.observe("corrections", "fix")
    summary = store.summary()
    assert summary["file"] == "interaction"
    assert summary["records"] == 3
    assert summary["categories"] == ["confidence_preferences",
                                     "corrections", "frequent_intents",
                                     "workflows"]
    assert summary["updated_at"] is not None
    assert summary["corrupted_last_load"] is False
    # no learned content may leak through the summary:
    assert "open_browser" not in json.dumps(summary)
    assert "copy_paste" not in json.dumps(summary)


def test_summary_on_missing_file(home):
    assert ProfileStore("preferences").summary() == {
        "file": "preferences", "records": 0, "categories": [],
        "updated_at": None, "corrupted_last_load": False,
    }


# ---------------------------------------------------------------------------
# ProfileStore — bounds
# ---------------------------------------------------------------------------


def test_bounds_trims_counter_keys_to_512_keeping_highest(home):
    store = ProfileStore("interaction")
    data = store.defaults()
    data["frequent_intents"] = {f"key{i:03d}": i for i in range(600)}
    assert store.save(data) is True
    kept = store.load()["frequent_intents"]
    assert len(kept) == 512
    assert "key599" in kept and kept["key599"] == 599   # highest kept
    assert "key088" in kept                             # 512th highest
    assert "key087" not in kept                         # lowest dropped


def test_bounds_generic_dict_capped(home):
    store = ProfileStore("voice")
    data = store.defaults()
    data["phrase_aliases"] = {f"alias{i}": f"canon{i}" for i in range(600)}
    assert store.save(data) is True
    aliases = store.load()["phrase_aliases"]
    assert len(aliases) == 512                  # non-counter: first kept
    assert aliases["alias0"] == "canon0"
    assert "alias599" not in aliases


def test_bounds_trims_list_to_last_32(home):
    store = ProfileStore("preferences")
    data = store.defaults()
    data["preferred_modalities"] = [f"m{i:02d}" for i in range(40)]
    assert store.save(data) is True
    modalities = store.load()["preferred_modalities"]
    assert len(modalities) == 32                # LAST 32 kept
    assert modalities[0] == "m08"
    assert modalities[-1] == "m39"


def test_bounds_clamps_ratio_fields(home):
    interaction = ProfileStore("interaction")
    data = interaction.defaults()
    data["confidence_preferences"] = {"high": 1.7, "low": -0.5, "ok": 0.42}
    assert interaction.save(data) is True
    prefs = interaction.load()["confidence_preferences"]
    assert prefs == {"high": 1.0, "low": 0.0, "ok": 0.42}

    gestures = ProfileStore("gestures")
    data = gestures.defaults()
    data["preferred_confidence"] = 2.5
    assert gestures.save(data) is True
    assert gestures.load()["preferred_confidence"] == 1.0
    data["preferred_confidence"] = -3.0
    assert gestures.save(data) is True
    assert gestures.load()["preferred_confidence"] == 0.0


def test_bounds_truncates_long_strings_and_keys(home):
    preferences = ProfileStore("preferences")
    data = preferences.defaults()
    data["hint_level"] = "h" * 100
    assert preferences.save(data) is True
    assert preferences.load()["hint_level"] == "h" * 64

    interaction = ProfileStore("interaction")
    data = interaction.defaults()
    data["frequent_intents"] = {"k" * 100: 1}
    assert interaction.save(data) is True
    keys = list(interaction.load()["frequent_intents"])
    assert len(keys) == 1 and len(keys[0]) == 64
    assert keys[0] == "k" * 64


def test_bounds_never_mutates_input(home):
    store = ProfileStore("interaction")
    data = {"frequent_intents": {f"k{i}": i for i in range(600)}}
    store.bounds(data)
    assert len(data["frequent_intents"]) == 600


def test_save_rejects_non_dict(home):
    store = ProfileStore("interaction")
    assert store.save("nope") is False
    assert store.save(None) is False
    assert store.save([1, 2]) is False
    assert store.load() == _INTERACTION_DEFAULTS


def test_save_wrong_typed_fields_guarded_on_load(home):
    """save() writes JSON-safe bounded data; load() type-guards it."""
    store = ProfileStore("interaction")
    assert store.save({"frequent_intents": "not-a-dict"}) is True
    on_disk = _read_json(store.file)
    assert on_disk["frequent_intents"] == "not-a-dict"
    # ...but a wrong-typed known field can never be trusted back in:
    assert store.load()["frequent_intents"] == {}


def test_save_fail_closed_when_profile_dir_blocked(home):
    os.makedirs(str(home), exist_ok=True)
    with open(paths.profile_dir(), "w") as f:   # profile/ is now a FILE
        f.write("blocker")
    store = ProfileStore("interaction")
    assert store.save({"frequent_intents": {"x": 1}}) is False
    assert store.observe("frequent_intents", "x") is False
    assert store.load() == _INTERACTION_DEFAULTS   # never raises


# ---------------------------------------------------------------------------
# ProfileStore — reset lifecycle (backup-then-defaults)
# ---------------------------------------------------------------------------


def test_reset_backs_up_then_restores_defaults(home):
    store = ProfileStore("interaction")
    store.observe("frequent_intents", "open_browser")
    store.observe("frequent_intents", "open_browser")
    before = open(store.file, "rb").read()

    result = store.reset(backup=True)

    assert result["name"] == "interaction"
    assert result["file"] == store.file
    assert result["backed_up"] is True
    assert result["cleared"] is True
    backup_path = result["backup_path"]
    backups = glob.glob(os.path.join(str(home), "backups",
                                     "profile-interaction-*.json"))
    assert backup_path in backups and len(backups) == 1
    assert open(backup_path, "rb").read() == before      # byte-copy backup
    assert store.load() == _INTERACTION_DEFAULTS
    assert store.corrupted_last_load is False


def test_reset_without_backup_makes_no_backup(home):
    store = ProfileStore("voice")
    store.observe("command_counts", "open browser")
    result = store.reset(backup=False)
    assert result["backed_up"] is False
    assert "backup_path" not in result
    assert result["cleared"] is True
    assert not os.path.exists(os.path.join(str(home), "backups"))
    assert store.load() == _VOICE_DEFAULTS


def test_reset_on_missing_file_is_honest(home):
    store = ProfileStore("gestures")
    result = store.reset(backup=True)
    assert result["backed_up"] is False
    assert "backup_path" not in result
    assert result["cleared"] is True
    assert store.load() == _GESTURES_DEFAULTS
    assert not glob.glob(os.path.join(str(home), "backups", "*.json"))


def test_export_payload_shape(home):
    store = ProfileStore("voice")
    store.observe("command_counts", "open browser")
    payload = store.export_payload()
    assert set(payload) == {"name", "schema_version", "data"}
    assert payload["name"] == "voice"
    assert payload["schema_version"] == 1
    assert payload["data"]["command_counts"] == {"open browser": 1}


# ---------------------------------------------------------------------------
# AIRMOUSE_HOME dynamism
# ---------------------------------------------------------------------------


def test_airmouse_home_change_redirects_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path / "h1"))
    store = ProfileStore("voice")
    assert store.observe("command_counts", "hello") is True
    file1 = paths.profile_voice_file()
    assert file1 == os.path.join(str(tmp_path / "h1"), "profile", "voice.json")
    assert os.path.exists(file1)

    # SAME store object, env changed AFTER import/creation:
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path / "h2"))
    file2 = paths.profile_voice_file()
    assert file2 != file1
    assert store.file == file2
    assert store.observe("command_counts", "hello") is True
    assert os.path.exists(file1) and os.path.exists(file2)
    # the two homes hold two independent documents:
    assert _read_json(file1)["command_counts"]["hello"] == 1
    assert _read_json(file2)["command_counts"]["hello"] == 1
    assert store.load()["command_counts"] == {"hello": 1}


# ---------------------------------------------------------------------------
# PersonalProfile — facade
# ---------------------------------------------------------------------------


def test_personal_profile_lazy_stores(home):
    profile = PersonalProfile()
    assert profile.interaction is profile.interaction      # lazily cached
    assert isinstance(profile.voice, ProfileStore)
    assert isinstance(profile.gestures, ProfileStore)
    assert isinstance(profile.preferences, ProfileStore)
    assert set(profile.stores()) == {"interaction", "voice", "gestures",
                                     "preferences"}
    assert profile.gestures.name == "gestures"


def test_personal_profile_summary_across_stores(home):
    profile = PersonalProfile()
    assert profile.learn_event({"modality": "voice", "kind": "command",
                                "key": "open browser",
                                "verified": True}) == 1
    profile.interaction.observe("corrections", "fix")
    summary = profile.summary()
    assert set(summary["stores"]) == {"interaction", "voice", "gestures",
                                      "preferences"}
    # voice: 1 learned command + the 2 dictation-stat counters
    assert summary["stores"]["voice"]["records"] == 3
    assert summary["stores"]["interaction"]["records"] == 1
    assert summary["stores"]["gestures"]["records"] == 0
    assert summary["total_records"] == 4
    assert summary["profile_dir"] == paths.profile_dir()
    # privacy: no learned keys anywhere in the summary
    assert "open browser" not in json.dumps(summary)


def test_personalization_summary_counts_and_privacy_line(home):
    profile = PersonalProfile()
    for key in ("pinch", "fist"):
        assert profile.learn_event({"modality": "gesture", "kind": "intent",
                                    "key": key, "verified": True}) == 1
    for key in ("open browser", "copy", "undo"):
        profile.learn_event({"modality": "voice", "kind": "command",
                             "key": key, "verified": True})
    profile.interaction.observe("workflows", "morning")
    profile.interaction.observe("workflows", "focus")
    assert profile.personalization_summary() == (
        "PERSONALIZATION\n"
        "Gestures learned: 2\n"
        "Voice patterns: 3\n"
        "Gaze calibration: Incomplete\n"
        "Workflows: 2\n"
        "Nothing is uploaded.")


def test_personalization_summary_gaze_complete_when_seeded(home):
    profile = PersonalProfile()
    _write_bytes(paths.gaze_calibration_file(),
                 b'{"version": 2, "matrix": [[1, 0, 0], [0, 1, 0]]}')
    assert "Gaze calibration: Complete" in profile.personalization_summary()
    assert "Nothing is uploaded." in profile.personalization_summary()


def test_personalization_summary_gaze_unknown_when_check_fails(
        home, monkeypatch):
    profile = PersonalProfile()

    def _boom():
        raise OSError("cannot stat")

    monkeypatch.setattr(paths, "gaze_calibration_file", _boom)
    assert "Gaze calibration: Unknown" in profile.personalization_summary()


def test_export_all_respects_dest_path(home, tmp_path):
    profile = PersonalProfile()
    profile.learn_event({"modality": "voice", "kind": "command",
                         "key": "open browser", "verified": True})
    profile.gestures.observe("gesture_counts", "pinch")
    dest = tmp_path / "my-profile-bundle.json"
    assert profile.export_all(str(dest)) is True
    bundle = _read_json(str(dest))
    assert bundle["format"] == "airmouse-profile-export"
    assert set(bundle["stores"]) == {"interaction", "voice", "gestures",
                                     "preferences"}
    assert bundle["stores"]["voice"]["data"]["command_counts"] == {
        "open browser": 1}
    assert bundle["stores"]["gestures"]["data"]["gesture_counts"] == {
        "pinch": 1}


def test_export_all_defaults_to_exports_dir(home):
    profile = PersonalProfile()
    profile.preferences.save({"hint_level": "quiet"})
    assert profile.export_all() is True
    exports = glob.glob(os.path.join(paths.exports_dir(), "*.json"))
    assert len(exports) == 1
    bundle = _read_json(exports[0])
    assert set(bundle["stores"]) == set(PROFILE_FILES)
    assert bundle["stores"]["preferences"]["data"]["hint_level"] == "quiet"


def test_export_all_never_raises(home, tmp_path):
    profile = PersonalProfile()
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    assert profile.export_all(str(blocker / "nested" / "out.json")) is False


def test_reset_all_results_and_defaults(home):
    profile = PersonalProfile()
    profile.learn_event({"modality": "voice", "kind": "command",
                         "key": "open browser", "verified": True})
    profile.gestures.observe("gesture_counts", "pinch")
    profile.interaction.observe("frequent_intents", "open_browser")
    profile.preferences.save({"hint_level": "quiet"})

    results = profile.reset_all(backup=True)

    assert set(results) == {"interaction", "voice", "gestures", "preferences"}
    for name, result in results.items():
        assert result["name"] == name
        assert result["backed_up"] is True and result["cleared"] is True
    assert profile.voice.load() == _VOICE_DEFAULTS
    assert profile.gestures.load() == _GESTURES_DEFAULTS
    assert profile.interaction.load() == _INTERACTION_DEFAULTS
    assert profile.preferences.load() == _PREFERENCES_DEFAULTS
    backups = glob.glob(os.path.join(str(home), "backups",
                                     "profile-*.json"))
    assert len(backups) == 4


# ---------------------------------------------------------------------------
# learn_event routing
# ---------------------------------------------------------------------------


def test_learn_event_routes_by_modality(home):
    profile = PersonalProfile()
    assert profile.learn_event({"modality": "gesture", "kind": "temporal",
                                "key": "pinch>swipe_left",
                                "verified": True}) == 1
    assert profile.learn_event({"modality": "gesture", "kind": "intent",
                                "key": "pinch", "verified": True}) == 1
    assert profile.learn_event({"modality": "voice", "kind": "command",
                                "key": "open browser",
                                "verified": True}) == 1
    assert profile.learn_event({"modality": "voice", "kind": "phrase",
                                "key": "open the thing",
                                "verified": True}) == 1
    assert profile.learn_event({"modality": "gaze", "kind": "intent",
                                "key": "toolbar", "verified": True}) == 1
    assert profile.learn_event({"modality": "fusion", "kind": "correction",
                                "key": "wrong_target",
                                "verified": True}) == 1

    g = _read_json(paths.profile_gestures_file())
    assert g["temporal_events"] == {"pinch>swipe_left": 1}
    assert g["gesture_counts"] == {"pinch": 1}     # kind fallback for gestures
    v = _read_json(paths.profile_voice_file())
    assert v["command_counts"] == {"open browser": 1}
    assert v["phrase_counts"] == {"open the thing": 1}
    i = _read_json(paths.profile_interaction_file())
    assert i["frequent_intents"] == {"toolbar": 1}
    assert i["corrections"] == {"wrong_target": 1}
    assert not os.path.exists(paths.profile_preferences_file())


def test_learn_event_kind_fallbacks(home):
    profile = PersonalProfile()
    # a "command" kind observed via the gesture modality cannot go to the
    # voice store; it falls back to the gesture modality's own category:
    assert profile.learn_event({"modality": "gesture", "kind": "command",
                                "key": "pinch", "verified": True}) == 1
    assert profile.learn_event({"modality": "fusion", "kind": "command",
                                "key": "x", "verified": True}) == 1
    g = profile.gestures.load()
    assert g["gesture_counts"] == {"pinch": 1}
    i = profile.interaction.load()
    assert i["frequent_intents"] == {"x": 1}     # fusion falls back too
    assert "command_counts" not in g and "command_counts" not in i


def test_learn_event_unverified_goes_to_unverified_namespace(home):
    profile = PersonalProfile()
    assert profile.learn_event({"modality": "voice", "kind": "command",
                                "key": "maybe open browser"}) == 1  # no flag
    assert profile.learn_event({"modality": "voice", "kind": "command",
                                "key": "maybe open browser",
                                "verified": False}) == 2
    assert profile.learn_event({"modality": "gesture", "kind": "temporal",
                                "key": "maybe>swipe",
                                "verified": False}) == 1
    v = profile.voice.load()
    assert v["unverified.command_counts"] == {"maybe open browser": 2}
    assert v["command_counts"] == {}           # verified namespace untouched
    g = profile.gestures.load()
    assert g["unverified.temporal_events"] == {"maybe>swipe": 1}
    assert g["temporal_events"] == {}
    assert is_unverified_category("unverified.command_counts") is True
    assert is_unverified_category("command_counts") is False
    # unverified data is NEVER used for preference suggestions:
    assert profile.voice.top("command_counts") == []
    assert profile.voice.top("unverified.command_counts") == [
        ("maybe open browser", 2)]


def test_learn_event_invalid_events_are_ignored(home):
    profile = PersonalProfile()
    assert profile.learn_event({"modality": "smell", "kind": "intent",
                                "key": "x"}) == 0
    assert profile.learn_event({"modality": "voice", "kind": "dance",
                                "key": "x"}) == 0
    assert profile.learn_event({"modality": "voice", "kind": "intent",
                                "key": "   "}) == 0
    assert profile.learn_event({"modality": "voice", "kind": "intent",
                                "key": "x", "value": "lots"}) == 0
    assert profile.learn_event({"modality": "voice", "kind": "intent",
                                "key": "x", "value": -2}) == 0
    assert profile.learn_event("not a dict") == 0
    assert profile.learn_event({}) == 0
    for name in PROFILE_FILES:                  # nothing was written
        assert not os.path.exists(PROFILE_FILES[name]())


def test_learn_event_returns_running_count(home):
    profile = PersonalProfile()
    event = {"modality": "gaze", "kind": "intent", "key": "toolbar",
             "verified": True}
    assert profile.learn_event(event) == 1
    assert profile.learn_event(event) == 2
    assert profile.learn_event(dict(event, value=3)) == 5


# ---------------------------------------------------------------------------
# LearningLoop — ring buffer + PREDICTION ≠ EXECUTION machinery
# ---------------------------------------------------------------------------


def test_loop_full_stage_sequence_recorded(home):
    loop = LearningLoop()
    for stage in STAGES:
        loop.record(stage, detail="ok")
    stats = loop.stats()
    assert stats["events"] == len(STAGES)
    for stage in STAGES:
        assert stats["stages"][stage] == 1
    events = loop.events()
    assert [e["stage"] for e in events] == list(STAGES)
    assert events[0]["detail"] == "ok"


def test_loop_record_rejects_unknown_stage(home):
    loop = LearningLoop()
    with pytest.raises(ValueError):
        loop.record("TELEPORT")
    with pytest.raises(ValueError):
        loop.record("sense")        # case-sensitive stage names
    assert loop.stats()["events"] == 0


def test_loop_ring_buffer_bounded(home):
    loop = LearningLoop()
    for i in range(1000):
        loop.record("SENSE", i=i)
    events = loop.events()
    assert len(events) == 256                 # default ring size
    assert loop.stats()["events"] == 256
    assert events[-1]["i"] == 999             # newest kept
    assert events[0]["i"] == 744              # oldest kept


def test_loop_custom_ring_size_clamped(home):
    assert len(LearningLoop(ring_size=4).events()) == 0  # min-ring clamp
    loop = LearningLoop(ring_size=16)
    for i in range(50):
        loop.record("LEARN", i=i)
    assert len(loop.events()) == 16


def test_loop_propose_creates_pending_proposal(home):
    loop = LearningLoop()
    pid = loop.propose({"modality": "voice", "kind": "command",
                        "key": "open browser"})
    assert isinstance(pid, str) and pid
    stats = loop.stats()
    assert stats["proposals_pending"] == 1
    assert stats["proposals_approved"] == 0
    (proposal,) = loop.proposals()
    assert proposal["id"] == pid
    assert proposal["status"] == "pending"
    assert proposal["suggestion"] == {"modality": "voice",
                                      "kind": "command",
                                      "key": "open browser"}
    assert proposal["approved_by"] is None
    # the ring is only fed by record(): propose() must not double-count
    assert loop.stats()["stages"]["PROPOSE"] == 0


def test_loop_approve_wrong_id_and_double_approve(home):
    loop = LearningLoop()
    assert loop.approve("no-such-id") is False
    pid = loop.propose({"modality": "gaze", "kind": "intent", "key": "t"})
    assert loop.approve(pid) is True
    assert loop.approve(pid) is False          # no longer pending
    assert loop.approve("") is False
    stats = loop.stats()
    assert stats["proposals_approved"] == 1
    assert stats["proposals_pending"] == 0
    (proposal,) = loop.proposals()
    assert proposal["status"] == "approved"
    assert proposal["approved_by"] == "user"
    assert proposal["approved_at"] is not None


def test_loop_expire_removes_only_stale_pending(home, monkeypatch):
    loop = LearningLoop()
    monkeypatch.setattr(profile_store.time, "time", lambda: 1000.0)
    stale = loop.propose({"modality": "voice", "kind": "command",
                          "key": "stale"})
    monkeypatch.setattr(profile_store.time, "time", lambda: 2000.0)
    fresh = loop.propose({"modality": "voice", "kind": "command",
                          "key": "fresh"})
    # threshold = now - max_age = 1501 - 300 = 1201:
    assert loop.expire_proposals(max_age_s=300, now=1501.0) == 1
    stats = loop.stats()
    assert stats["proposals_expired"] == 1
    assert stats["proposals_pending"] == 1
    statuses = {p["id"]: p["status"] for p in loop.proposals()}
    assert statuses == {stale: "expired", fresh: "pending"}
    # an expired proposal can never be approved afterwards:
    assert loop.approve(stale) is False
    assert loop.expire_proposals(max_age_s=300, now=1900.0) == 0


def test_loop_adapt_requires_explicit_approval(home):
    profile = PersonalProfile()
    loop = LearningLoop()
    loop.propose({"modality": "gesture", "kind": "temporal",
                  "key": "pinch>swipe_left"})
    assert loop.adapt(profile) == []           # pending: never applied
    assert profile.gestures.load() == {
        "schema_version": 1, "gesture_counts": {},
        "preferred_confidence": None, "temporal_events": {},
        "updated_at": None}
    assert loop.stats()["proposals_applied"] == 0


def test_loop_adapt_applies_only_approved_proposal(home):
    profile = PersonalProfile()
    loop = LearningLoop()
    pid = loop.propose({"modality": "gesture", "kind": "temporal",
                        "key": "pinch>swipe_left", "value": 2})
    loop.propose({"modality": "voice", "kind": "command",
                  "key": "open browser"})
    assert loop.approve(pid) is True
    applied = loop.adapt(profile)
    assert applied == [pid]
    data = profile.gestures.load()
    assert data["temporal_events"] == {"pinch>swipe_left": 2}
    assert data["gesture_counts"] == {}        # routed by kind: temporal
    stats = loop.stats()
    assert stats["proposals_applied"] == 1
    assert stats["proposals_pending"] == 1     # the unapproved one waits
    proposal = [p for p in loop.proposals() if p["id"] == pid][0]
    assert proposal["status"] == "applied"
    assert proposal["applied_at"] is not None
    assert proposal["routed"] == 2


def test_loop_adapt_max_updates_oldest_first(home):
    profile = PersonalProfile()
    loop = LearningLoop()
    pids = [loop.propose({"modality": "voice", "kind": "command",
                          "key": f"cmd{i}"}) for i in range(3)]
    for pid in pids:
        assert loop.approve(pid) is True
    applied = loop.adapt(profile, max_updates=2)
    assert applied == pids[:2]                 # OLDEST approved first
    counts = profile.voice.load()["command_counts"]
    assert counts == {"cmd0": 1, "cmd1": 1}
    statuses = {p["id"]: p["status"] for p in loop.proposals()}
    assert statuses == {pids[0]: "applied", pids[1]: "applied",
                        pids[2]: "approved"}
    assert loop.adapt(profile) == [pids[2]]
    assert profile.voice.load()["command_counts"]["cmd2"] == 1


def test_loop_adapt_ignores_pending_and_expired(home, monkeypatch):
    profile = PersonalProfile()
    loop = LearningLoop()
    monkeypatch.setattr(profile_store.time, "time", lambda: 1000.0)
    expired = loop.propose({"modality": "voice", "kind": "command",
                            "key": "expired-cmd"})
    monkeypatch.setattr(profile_store.time, "time", lambda: 5000.0)
    pending = loop.propose({"modality": "voice", "kind": "command",
                            "key": "pending-cmd"})
    # threshold = 1002 - 1 = 1001 -> only the 1000s proposal expires:
    assert loop.expire_proposals(max_age_s=1, now=1002.0) == 1
    approved = loop.propose({"modality": "voice", "kind": "command",
                             "key": "approved-cmd"})
    assert loop.approve(approved) is True
    assert loop.adapt(profile) == [approved]
    counts = profile.voice.load()["command_counts"]
    assert counts == {"approved-cmd": 1}
    statuses = {p["id"]: p["status"] for p in loop.proposals()}
    assert statuses == {pending: "pending", expired: "expired",
                        approved: "applied"}


def test_loop_adapt_unusable_profile_is_safe(home):
    loop = LearningLoop()
    pid = loop.propose({"modality": "voice", "kind": "command",
                        "key": "k"})
    assert loop.approve(pid) is True
    assert loop.adapt(None) == []              # never raises
    assert loop.adapt(object()) == []
    assert loop.stats()["proposals_applied"] == 0


def test_loop_stats_last_adapt_at(home):
    profile = PersonalProfile()
    loop = LearningLoop()
    assert loop.stats()["last_adapt_at"] is None
    pid = loop.propose({"modality": "voice", "kind": "command", "key": "k"})
    loop.approve(pid)
    loop.adapt(profile)
    last = loop.stats()["last_adapt_at"]
    assert isinstance(last, float) and last <= time.time()


def test_loop_proposals_bounded(home):
    loop = LearningLoop()
    for i in range(LearningLoop.MAX_PROPOSALS + 50):
        loop.propose({"modality": "voice", "kind": "command",
                      "key": f"k{i}"})
    assert len(loop.proposals()) == LearningLoop.MAX_PROPOSALS
    assert loop.stats()["proposals_total"] == LearningLoop.MAX_PROPOSALS


def test_loop_suggestion_stored_bounded(home):
    loop = LearningLoop()
    loop.propose({"modality": "voice", "kind": "command", "key": "x" * 500,
                  "note": "n" * 500, "extra": 123})
    (proposal,) = loop.proposals()
    sug = proposal["suggestion"]
    assert sug["key"] == "x" * 128             # bounded copy
    assert sug["note"] == "n" * 128
    assert sug["extra"] == 123


# ---------------------------------------------------------------------------
# concurrency smoke
# ---------------------------------------------------------------------------


def test_concurrent_record_and_observe(home):
    store = ProfileStore("interaction")
    loop = LearningLoop()
    errors = []
    workers = 8
    per_thread = 100
    barrier = threading.Barrier(workers)

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=5)
            if idx % 2 == 0:
                for j in range(per_thread):
                    loop.record(STAGES[j % len(STAGES)], worker=idx, seq=j)
            else:
                for j in range(per_thread):
                    if not store.observe("frequent_intents",
                                         f"key{idx}-{j % 5}"):
                        errors.append(AssertionError("observe refused"))
        except Exception as exc:               # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors
    assert not any(t.is_alive() for t in threads)
    # 4 observer threads x 100 observations, lock-serialized: consistent
    data = store.load()
    assert data["total_observations"] == 4 * per_thread
    assert len(data["frequent_intents"]) == 4 * 5
    assert sum(data["frequent_intents"].values()) == 4 * per_thread
    # 4 recorder threads x 100 events -> bounded by the ring
    stats = loop.stats()
    assert stats["events"] == 256
    assert sum(stats["stages"].values()) == 256


# ---------------------------------------------------------------------------
# privacy invariants
# ---------------------------------------------------------------------------


def test_profile_files_live_under_manifested_profile_dir(home):
    profile = PersonalProfile()
    profile.learn_event({"modality": "voice", "kind": "command",
                         "key": "k", "verified": True})
    from airmouse import privacy
    names = {entry["name"] for entry in privacy.PRIVACY_MANIFEST}
    assert "personal_profile" in names
    for name, resolver in PROFILE_FILES.items():
        path = resolver()
        assert path.startswith(paths.profile_dir() + os.sep)
        assert os.path.dirname(path) == paths.profile_dir()
        if os.path.exists(path):
            # documents stay small, JSON objects, content-free counters
            raw = open(path, "rb").read()
            assert len(raw) < 64 * 1024
            doc = json.loads(raw.decode("utf-8"))
            assert isinstance(doc, dict)
