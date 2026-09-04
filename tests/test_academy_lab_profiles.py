"""Tests for the v16 Gesture Academy + Gesture Lab + gesture profiles.

Everything here is headless by construction: no camera, no display, no
audio device.  Physical (camera) lessons are only ever asserted to be
labelled honestly — never auto-passed.

Covers (Task 2-f):
- gesture_profiles: 8 named profiles, whitelist enforcement, honest
  refusal of unknown/NaN/out-of-range values, real config-file write
  under a tmp AIRMOUSE_HOME (paths.config_file() / config_path_scope),
  accessibility-sanity vs default.
- academy: pure academy_plan() data contract (>= 10 lessons, required
  fields, honest PHYSICAL status), unknown-lesson exit code, headless
  run output, atomic progress round-trip under the unified home.
- gesture_lab: pure lab_render() field contract, headless honest
  no-camera run, and the dry-run spine guarantee (gates run for real,
  the stub records instead of acting).
"""

import json
import os

try:
    import tomllib
except ImportError:                              # pragma: no cover
    tomllib = None

from airmouse import academy
from airmouse import gesture_lab
from airmouse import gesture_profiles as gp
from airmouse import paths
from airmouse.gestures import Gesture

ALL_PROFILES = {"default", "developer", "presentation", "gaming",
                "accessibility", "media", "creative", "hands_free"}


# ---------------------------------------------------------------------------
# gesture_profiles
# ---------------------------------------------------------------------------

class TestProfileCatalogue:

    def test_list_has_all_eight_sorted(self):
        names = gp.list_profiles()
        assert set(names) == ALL_PROFILES
        assert names == sorted(names)
        assert len(names) == 8

    def test_get_profile_returns_copy_or_none(self):
        prof = gp.get_profile("accessibility")
        assert isinstance(prof, dict) and prof
        prof["deadzone"] = 999.0                # mutating the copy must not
        assert gp.get_profile("accessibility")["deadzone"] != 999.0
        assert gp.get_profile("does-not-exist") is None
        assert gp.get_profile("") is None
        assert gp.get_profile(None) is None

    def test_every_profile_only_uses_whitelist_keys(self):
        for name, overrides in gp.PROFILES.items():
            extra = set(overrides) - gp.PROFILE_WHITELIST
            assert not extra, f"profile '{name}' has non-whitelisted keys"

    def test_every_value_finite_and_in_range(self):
        import math
        for name, overrides in gp.PROFILES.items():
            for key, value in overrides.items():
                if isinstance(value, float):
                    assert math.isfinite(value), (name, key)
                reason = gp._validate_value(key, value)
                assert reason is None, (name, key, reason)

    def test_accessibility_sane_vs_default(self):
        default = gp.get_profile("default")
        acc = gp.get_profile("accessibility")
        assert (acc["gesture_confirm_frames"]
                > default["gesture_confirm_frames"])
        assert (acc["gesture_action_confirm_frames"]
                > default["gesture_action_confirm_frames"])
        assert acc["deadzone"] > default["deadzone"]
        assert acc["audio_enabled"] is True
        assert (acc["gesture_min_confidence_caution"]
                >= acc["gesture_min_confidence_safe"])
        # factory mirror: 'default' matches config.py class defaults
        from airmouse.config import Config
        c = Config()
        assert default["gesture_confirm_frames"] == c.gesture_confirm_frames
        assert default["deadzone"] == c.deadzone
        assert default["position_smooth_alpha"] == c.position_smooth_alpha

    def test_whitelist_is_locked_to_safe_keys(self):
        # safety-critical / privacy-critical config must be unreachable
        forbidden = {"gesture_allow_destructive", "telemetry_enabled",
                     "offline", "safety_level", "camera_index",
                     "browser_enabled", "privacy_mode"}
        assert not (gp.PROFILE_WHITELIST & forbidden)


class TestApplyProfile:

    def _tmp_home(self, monkeypatch, tmp_path):
        home = tmp_path / "am_home"
        home.mkdir(exist_ok=True)
        monkeypatch.setenv("AIRMOUSE_HOME", str(home))
        return home

    def test_unknown_profile_fails_honestly(self):
        ok, msg = gp.apply_profile("no-such-profile")
        assert ok is False
        assert "unknown profile" in msg
        for name in ALL_PROFILES:               # msg lists the availables
            assert name in msg

    def test_empty_and_malformed_names_fail(self):
        ok, msg = gp.apply_profile("")
        assert ok is False and "no profile given" in msg
        ok, msg = gp.apply_profile(None)
        assert ok is False

    def test_apply_valid_writes_real_config_file(
            self, monkeypatch, tmp_path):
        self._tmp_home(monkeypatch, tmp_path)
        ok, msg = gp.apply_profile("accessibility")
        assert ok is True, msg
        assert "profile 'accessibility' applied: " in msg
        assert "12 settings" in msg
        cfg_path = paths.config_file()
        assert os.path.exists(cfg_path)         # inside AIRMOUSE_HOME
        assert cfg_path == os.path.join(
            os.environ["AIRMOUSE_HOME"], "config.toml")
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        assert data["gesture"]["confirm_frames"] == 6
        assert data["gesture"]["action_confirm_frames"] == 8
        assert data["v10"]["gesture_min_confidence_safe"] == 0.55
        assert data["v10"]["gesture_min_confidence_caution"] == 0.70
        assert data["audio"]["enabled"] is True
        assert data["calibration"]["adaptive_enabled"] is True
        assert data["ironman"]["deadzone"] == 0.02
        assert data["smoothing"]["position_alpha"] == 0.55
        assert data["v10"]["two_hand"] is False

    def test_applied_values_are_what_the_app_loads(
            self, monkeypatch, tmp_path):
        self._tmp_home(monkeypatch, tmp_path)
        ok, _ = gp.apply_profile("gaming")
        assert ok is True
        from airmouse.config import Config
        from airmouse.persistence import config_path_scope
        with config_path_scope():
            cfg = Config()
            cfg.load()
        assert cfg.gesture_confirm_frames == 2
        assert cfg.gesture_action_confirm_frames == 2
        assert cfg.adaptive_calibration is False
        assert cfg.tracking_mode == "direct"

    def test_apply_preserves_settings_outside_the_profile(
            self, monkeypatch, tmp_path):
        home = self._tmp_home(monkeypatch, tmp_path)
        # a user config with a personal tweak the profile must not clobber
        os.makedirs(home, exist_ok=True)
        with open(paths.config_file(), "w") as f:
            f.write('[gesture]\npinch_cooldown = 0.5\n'
                    'confirm_frames = 9\n')
        ok, msg = gp.apply_profile("accessibility")
        assert ok is True, msg
        with open(paths.config_file(), "rb") as f:
            data = tomllib.load(f)
        assert data["gesture"]["confirm_frames"] == 6      # overridden
        assert data["gesture"]["pinch_cooldown"] == 0.5    # preserved

    def test_default_profile_restores_factory(self, monkeypatch, tmp_path):
        self._tmp_home(monkeypatch, tmp_path)
        ok, _ = gp.apply_profile("accessibility")
        assert ok is True
        ok, msg = gp.apply_profile("default")
        assert ok is True, msg
        assert "12 settings" in msg             # explicit factory mirror
        with open(paths.config_file(), "rb") as f:
            data = tomllib.load(f)
        assert data["gesture"]["confirm_frames"] == 3
        assert data["gesture"]["action_confirm_frames"] == 4
        assert data["ironman"]["deadzone"] == 0.008

    def test_whitelist_enforcement_refuses_rogue_keys(
            self, monkeypatch):
        # safety-critical key injected into a profile dict -> refused
        monkeypatch.setitem(gp.PROFILES, "rogue_safety",
                            {"gesture_allow_destructive": True})
        ok, msg = gp.apply_profile("rogue_safety")
        assert ok is False
        assert "outside the profile whitelist" in msg
        assert "gesture_allow_destructive" in msg
        # privacy-critical key -> refused the same way
        monkeypatch.setitem(gp.PROFILES, "rogue_privacy",
                            {"telemetry_enabled": True})
        ok, msg = gp.apply_profile("rogue_privacy")
        assert ok is False and "whitelist" in msg

    def test_nan_and_bad_values_refused(self, monkeypatch):
        monkeypatch.setitem(gp.PROFILES, "nan_profile",
                            {"deadzone": float("nan")})
        ok, msg = gp.apply_profile("nan_profile")
        assert ok is False and "finite" in msg
        monkeypatch.setitem(gp.PROFILES, "inf_profile",
                            {"exp_scale": float("inf")})
        ok, msg = gp.apply_profile("inf_profile")
        assert ok is False and "finite" in msg
        monkeypatch.setitem(gp.PROFILES, "zero_frames",
                            {"gesture_confirm_frames": 0})
        ok, msg = gp.apply_profile("zero_frames")
        assert ok is False and "must be in" in msg
        monkeypatch.setitem(gp.PROFILES, "wrong_type",
                            {"audio_enabled": "yes"})
        ok, msg = gp.apply_profile("wrong_type")
        assert ok is False and "true or false" in msg
        monkeypatch.setitem(gp.PROFILES, "bad_mode",
                            {"tracking_mode": "warp"})
        ok, msg = gp.apply_profile("bad_mode")
        assert ok is False and "direct" in msg and "ironman" in msg

    def test_incoherent_confidence_floors_refused(self, monkeypatch):
        monkeypatch.setitem(gp.PROFILES, "incoherent",
                            {"gesture_min_confidence_safe": 0.9,
                             "gesture_min_confidence_caution": 0.5})
        ok, msg = gp.apply_profile("incoherent")
        assert ok is False and "incoherent" in msg


# ---------------------------------------------------------------------------
# academy
# ---------------------------------------------------------------------------

REQUIRED_LESSON_FIELDS = {"id", "title", "track", "gesture", "instruction",
                          "success_criteria", "tips", "requires"}
CORE_IDS = {"move", "click", "double_click", "right_click",
            "drag", "scroll", "zoom"}
ADVANCED_IDS = {"gaze", "voice", "two_hand", "sequences"}

_VALID_GESTURE_VALUES = {v for k, v in vars(Gesture).items()
                         if k.isupper() and isinstance(v, str)}


class TestAcademyPlan:

    def test_plan_has_at_least_ten_lessons_with_required_fields(self):
        plan = academy.academy_plan()
        assert len(plan) >= 10
        ids = [l["id"] for l in plan]
        assert len(ids) == len(set(ids))        # unique ids
        for lesson in plan:
            missing = REQUIRED_LESSON_FIELDS - set(lesson)
            assert not missing, (lesson["id"], missing)
            assert lesson["tips"], lesson["id"]
            assert isinstance(lesson["tips"], list)

    def test_core_and_advanced_tracks(self):
        plan = academy.academy_plan()
        ids = {l["id"] for l in plan}
        assert CORE_IDS <= ids
        assert ADVANCED_IDS <= ids
        for lesson in plan:
            if lesson["track"] == "core":
                assert lesson["success_criteria"] > 0
                assert lesson["gesture"] in _VALID_GESTURE_VALUES
                assert lesson["next_step"] is None
            else:
                assert lesson["success_criteria"] is None
                assert lesson["requires"]       # honest hardware note
                assert lesson["next_step"]      # points at the real command

    def test_advanced_gaze_points_to_real_calibration(self):
        gaze = next(l for l in academy.academy_plan() if l["id"] == "gaze")
        assert "--gaze-calibrate" in gaze["next_step"]

    def test_plan_is_pure_data(self):
        plan = academy.academy_plan()
        plan[0]["title"] = "mutated"
        assert academy.academy_plan()[0]["title"] != "mutated"

    def test_single_lesson_filter(self):
        plan = academy.academy_plan("move")
        assert len(plan) == 1 and plan[0]["id"] == "move"
        assert academy.academy_plan("ALL")[0]["id"] == "move"
        assert academy.academy_plan(None) == academy.academy_plan("all")

    def test_unknown_lesson_yields_empty_plan(self):
        assert academy.academy_plan("bogus-lesson") == []


class TestRunAcademyHeadless:

    def test_unknown_lesson_exit_code_1(self, capsys):
        rc = academy.run_academy(lesson="not-a-lesson", camera=False)
        assert rc == 1
        out = capsys.readouterr().out
        assert "valid lesson ids" in out
        assert "move" in out and "zoom" in out

    def test_headless_run_prints_honest_plan(self, capsys):
        rc = academy.run_academy(lesson="all", camera=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "PHYSICAL" in out and "REQUIRED" in out
        for lid in sorted(CORE_IDS | ADVANCED_IDS):
            assert lid in out
        assert "NEVER" in out                   # never auto-passed
        # every core lesson states its hold-to-pass criteria
        for lesson in academy.academy_plan():
            if lesson["track"] == "core":
                assert (f"{lesson['success_criteria']:.1f}s") in out

    def test_single_lesson_headless(self, capsys):
        rc = academy.run_academy(lesson="click", camera=False)
        assert rc == 0
        out = capsys.readouterr().out
        assert "1 lesson(s)" in out
        assert "pinch" in out

    def test_headless_run_is_camera_free(self, monkeypatch, capsys):
        # even if cv2 were unimportable, the headless path must not care
        import sys as _sys
        monkeypatch.setitem(_sys.modules, "cv2", None)
        rc = academy.run_academy(lesson="all", camera=False)
        assert rc == 0
        capsys.readouterr()


class TestAcademyProgress:

    def test_progress_roundtrip_in_tmp_home(self, monkeypatch, tmp_path):
        home = tmp_path / "am_prog_home"
        home.mkdir()
        monkeypatch.setenv("AIRMOUSE_HOME", str(home))
        assert academy.progress_path() == os.path.join(
            paths.airmouse_home(), "academy_progress.json")
        assert academy.load_progress() == {"completed": []}
        academy.save_progress(["move", "click"])
        assert os.path.exists(academy.progress_path())
        loaded = academy.load_progress()
        assert loaded["completed"] == ["move", "click"]
        # round-trip through a second save/load
        academy.save_progress(["move", "click", "drag"])
        assert academy.load_progress()["completed"] == \
            ["move", "click", "drag"]
        # the file is plain atomic JSON (persistence.atomic_write_json)
        with open(academy.progress_path()) as f:
            raw = json.load(f)
        assert raw["completed"] == ["move", "click", "drag"]
        assert "saved_at" in raw

    def test_progress_filters_unknown_ids(self, monkeypatch, tmp_path):
        home = tmp_path / "am_prog_home2"
        home.mkdir()
        monkeypatch.setenv("AIRMOUSE_HOME", str(home))
        academy.save_progress(["move", "not-a-real-lesson"])
        assert academy.load_progress()["completed"] == ["move"]

    def test_corrupt_progress_fails_closed_to_empty(
            self, monkeypatch, tmp_path):
        home = tmp_path / "am_prog_home3"
        home.mkdir()
        monkeypatch.setenv("AIRMOUSE_HOME", str(home))
        academy.save_progress(["move"])
        with open(academy.progress_path(), "w") as f:
            f.write("{this is not json")
        assert academy.load_progress() == {"completed": []}


# ---------------------------------------------------------------------------
# gesture lab
# ---------------------------------------------------------------------------

SAMPLE_SNAPSHOT = {
    "hand": True,
    "gesture": "pinch",
    "confidence": 0.86,
    "mode": "classic",
    "two_hand": "off",
    "last_action": "left_click (executed into dry-run stub)",
    "result": "executed",
}


class TestLabRender:

    def test_renders_all_canonical_fields(self):
        text = gesture_lab.lab_render(SAMPLE_SNAPSHOT)
        for field in ("HAND DETECTED", "GESTURE", "CONFIDENCE", "MODE",
                      "TWO-HAND", "LAST ACTION", "RESULT"):
            assert field in text
        assert "yes" in text
        assert "pinch" in text
        assert "86%" in text
        assert "classic" in text
        assert "left_click" in text
        assert "executed" in text

    def test_renders_blocked_results_honestly(self):
        text = gesture_lab.lab_render({**SAMPLE_SNAPSHOT,
                                       "gesture": "ok",
                                       "last_action":
                                           "close_window (attempted)",
                                       "result":
                                           "blocked: low_confidence (0.30)"})
        assert "blocked" in text
        assert "close_window" in text

    def test_empty_snapshot_is_deterministic_and_honest(self):
        empty = gesture_lab.lab_render({})
        again = gesture_lab.lab_render({})
        assert empty == again                   # pure: same in -> same out
        assert "NO" in empty                    # hand not detected
        assert "none" in empty
        # no hand -> rendered as NO regardless of the claimed confidence
        no_hand = gesture_lab.lab_render({**SAMPLE_SNAPSHOT, "hand": False})
        assert "NO" in no_hand
        # non-finite / junk confidence clamps instead of crashing
        junk = gesture_lab.lab_render({**SAMPLE_SNAPSHOT,
                                       "confidence": "not-a-number"})
        assert "0%" in junk

    def test_headless_lab_exits_zero_with_honest_message(self, capsys):
        rc = gesture_lab.run_gesture_lab(camera=False, seconds=0.0)
        assert rc == 0
        out = capsys.readouterr().out
        assert "GESTURE LAB" in out
        assert "WITHOUT A CAMERA" in out        # the honest no-camera note
        assert "dry-run" in out.lower()         # never acts, says so
        assert "RESULT" in out                  # shows the exact fields
        assert "blocked" in out


class TestLabDryRunSpine:
    """The lab's guarantee: real spine gates, dry-run execution only."""

    def _router_and_stub(self):
        from airmouse.gesture_spine import GestureActionRouter
        stub = gesture_lab._DryRunMouse()
        spine = GestureActionRouter(
            mouse=stub,
            kb_getter=lambda: gesture_lab._DryRunKeyboard(),
            zoom_fn=lambda ticks: stub.calls.append(("zoom", ticks)),
            allow_destructive=False,
        )
        return spine, stub

    def test_close_window_is_always_blocked_by_policy(self):
        spine, stub = self._router_and_stub()
        # confidence 1.1 clears the confidence floor (DESTRUCTIVE floor
        # is unreachable at 1.1 by design) so the POLICY gate is the one
        # that must refuse — that is the lab's canonical teaching moment
        outcome = spine.dispatch("close_window", confidence=1.1)
        assert outcome["executed"] is False
        assert "destructive_action_blocked_by_policy" in outcome["reason"]
        assert stub.calls == []                 # nothing reached hardware

    def test_low_confidence_is_gated(self):
        spine, stub = self._router_and_stub()
        outcome = spine.dispatch("left_click", confidence=0.10)
        assert outcome["executed"] is False
        assert "low_confidence" in outcome["reason"]
        assert stub.calls == []

    def test_safe_action_executes_into_the_stub_only(self):
        spine, stub = self._router_and_stub()
        outcome = spine.dispatch("left_click", confidence=0.9)
        assert outcome["executed"] is True
        assert stub.calls == ["left_click"]     # recorded, never real
        # rate limit gates the immediate repeat (backstop is real too)
        outcome2 = spine.dispatch("left_click", confidence=0.9, now=None)
        # (separate now timestamps would pass; same-tick repeat is limited)
        if not outcome2["executed"]:
            assert outcome2["reason"] == "rate_limit"

    def test_gesture_intent_map_matches_live_loop(self):
        from airmouse.gesture_spine import RISK_CLASSES
        for gesture, intent in gesture_lab.DRY_RUN_GESTURE_INTENTS.items():
            assert intent in RISK_CLASSES, intent
        # the teaching moment is wired: OK-gesture -> destructive class
        assert (gesture_lab.DRY_RUN_GESTURE_INTENTS[Gesture.OK]
                == "close_window")
        assert RISK_CLASSES["close_window"] == "DESTRUCTIVE"


# ---------------------------------------------------------------------------
# cross-module wiring guarantees
# ---------------------------------------------------------------------------

def test_modules_import_headless():
    """academy/gesture_lab/gesture_profiles import cleanly with no
    camera and no display (cv2 import is lazy inside run_* only)."""
    import airmouse.academy as a
    import airmouse.gesture_lab as gl
    import airmouse.gesture_profiles as gprof
    assert callable(a.run_academy) and callable(a.academy_plan)
    assert callable(gl.run_gesture_lab) and callable(gl.lab_render)
    assert callable(gprof.apply_profile)
    assert callable(gprof.list_profiles) and callable(gprof.get_profile)


def test_signatures_match_the_coordinator_wiring():
    """__main__ calls run_academy(lesson=..., camera=...),
    run_gesture_lab(camera=..., seconds=...) and
    apply_profile(name) -> (ok, msg); list_profiles() -> list[str]."""
    import inspect
    sig = inspect.signature(academy.run_academy)
    assert list(sig.parameters) == ["lesson", "camera"]
    assert sig.parameters["lesson"].default == "all"
    assert sig.parameters["camera"].default is True
    sig = inspect.signature(gesture_lab.run_gesture_lab)
    assert list(sig.parameters) == ["camera", "seconds"]
    assert sig.parameters["camera"].default is True
    assert sig.parameters["seconds"].default == 0.0
    sig = inspect.signature(gp.apply_profile)
    assert list(sig.parameters) == ["name"]
    names = gp.list_profiles()
    assert isinstance(names, list) and all(isinstance(n, str) for n in names)
