"""Task 2-c — TWO-HAND FOUNDATION tests.

Covers:
  1. tracker.py contract extensions (mocked mediapipe, headless-safe):
     - max_hands validation (1|2) and num_hands wiring,
     - "hands" key always present (incl. _empty_result),
     - per-hand entry shape + mirrored-input handedness convention.
  2. two_hand.py TwoHandGestureRecognizer: full synthetic scenarios
     (engage/grace/reset, zoom/rotate/drag, deadzones, mutual exclusion,
     cooldown, confidence bounds, determinism, <1ms/update throughput).
  3. config.two_hand: default False + TOML save/load round-trip.

Synthetic landmark factories build plausible 21-point hands as lists of
(x, y, z) tuples (the documented landmark format); index tip (8) and
thumb tip (4) sit exactly ``pinch`` apart.  Both hands share one shape,
so per-hand centroid offsets are identical and center-distance ratios
match centroid-distance ratios exactly.
"""

import math
import time
import types

import cv2
import numpy as np
import pytest

from airmouse import config as config_mod
from airmouse import tracker as tracker_mod
from airmouse.config import Config
from airmouse.tracker import HandTracker
from airmouse.two_hand import (
    TWO_HAND_DRAG,
    TWO_HAND_HOLD,
    TWO_HAND_ROTATE,
    TWO_HAND_ZOOM,
    TwoHandGestureRecognizer,
)

DT = 1.0 / 30.0  # one frame at 30 fps
T0 = 100.0

_ENTRY_KEYS = {"landmarks", "index_pos", "pinch_distance",
               "handedness", "handedness_score", "is_left_user"}
_REPORT_KEYS = {"active", "gesture", "scale", "angle_delta_deg",
                "centroid_delta", "confidence", "handedness"}


# ═══════════════════════════════════════════════════════════════════════════
# Synthetic two-hand factories (21-landmark (x, y, z) tuple hands)
# ═══════════════════════════════════════════════════════════════════════════

def _hand_points(cx, cy, pinch=0.02, spread=0.05):
    """Plausible left/right-agnostic hand around (cx, cy).

    21 (x, y, z) tuples: wrist at 0, thumb tip at 4, index tip at 8.
    Thumb tip and index tip are exactly ``pinch`` apart (horizontally).
    """
    itip = (cx + spread * 0.5, cy - spread * 0.5)
    ttip = (itip[0] - pinch, itip[1])
    pts = [(cx, cy + spread, 0.0)]                                   # 0 wrist
    pts += [(cx + spread * 0.1 * k, cy + spread * 0.3 - spread * 0.25 * k, 0.0)
            for k in (1, 2, 3)]                                      # 1-3 thumb
    pts.append((ttip[0], ttip[1], 0.0))                              # 4 thumb tip
    pts += [(cx - spread * 0.2, cy - spread * 0.1, 0.0),             # 5-7 index
            (cx, cy - spread * 0.3, 0.0),
            (cx + spread * 0.25, cy - spread * 0.4, 0.0)]
    pts.append((itip[0], itip[1], 0.0))                              # 8 index tip
    for dx in (-0.1, 0.15, 0.4):                                     # 9-20 m/r/p
        for k in range(4):
            pts.append((cx + dx * spread, cy - spread * (0.5 + 0.18 * k), 0.0))
    assert len(pts) == 21
    return pts


def _hand_dict(cx, cy, label, pinch=0.02, spread=0.05):
    pts = _hand_points(cx, cy, pinch, spread)
    return {
        "landmarks": pts,                       # tuple format — supported
        "index_pos": (pts[8][0], pts[8][1]),
        "pinch_distance": pinch,
        "handedness": label,
        "handedness_score": 0.95,
        "is_left_user": (label == "Left"),
    }


def two_hands(lc=(0.3, 0.5), rc=(0.7, 0.5), pinch=0.02):
    return [_hand_dict(lc[0], lc[1], "Left", pinch),
            _hand_dict(rc[0], rc[1], "Right", pinch)]


def one_hand(cx=0.3, cy=0.5, pinch=0.02):
    return [_hand_dict(cx, cy, "Left", pinch)]


def rotated_positions(deg):
    """Baseline centers (0.3,0.5)/(0.7,0.5) rotated by ``deg`` degrees
    clockwise (screen convention, y-down) about the shared midpoint."""
    mid = (0.5, 0.5)
    r = 0.2
    v = (2.0 * r * math.cos(math.radians(deg)),
         2.0 * r * math.sin(math.radians(deg)))
    return (mid[0] - v[0] / 2.0, mid[1] - v[1] / 2.0), \
           (mid[0] + v[0] / 2.0, mid[1] + v[1] / 2.0)


def engage(rec, lc=(0.3, 0.5), rc=(0.7, 0.5), pinch=0.02, t0=T0):
    outs = []
    for i in range(rec.engage_frames):
        outs.append(rec.update(two_hands(lc, rc, pinch), t0 + i * DT))
    return outs


# ═══════════════════════════════════════════════════════════════════════════
# tracker.py — max_hands validation + wiring (mocked mediapipe/camera)
# ═══════════════════════════════════════════════════════════════════════════

def test_init_rejects_invalid_max_hands():
    """Validation runs before any model download / camera open."""
    for bad in (0, 3, -1, 2.5, "2", 7):
        with pytest.raises(ValueError):
            HandTracker(camera_index=0, max_hands=bad)
    with pytest.raises(ValueError):
        HandTracker(camera_index=0, max_num_hands=3)   # legacy kwarg, too


def test_init_wires_num_hands(monkeypatch):
    monkeypatch.setattr(tracker_mod, "_ensure_model", lambda: "/tmp/fake.task")

    captured = {}

    def fake_create(options):
        captured["options"] = options
        return types.SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(tracker_mod.mp.tasks.vision.HandLandmarker,
                        "create_from_options", staticmethod(fake_create))

    class _Cap:
        def set(self, *a):
            return True

        def get(self, prop):
            return {cv2.CAP_PROP_FRAME_WIDTH: 640.0,
                    cv2.CAP_PROP_FRAME_HEIGHT: 480.0,
                    cv2.CAP_PROP_FPS: 30.0}.get(prop, 0.0)

        def read(self):
            return False, None

        def release(self):
            pass

    monkeypatch.setattr(tracker_mod.cv2, "VideoCapture", lambda idx: _Cap())

    t = HandTracker(camera_index=0, max_hands=2)
    assert captured["options"].num_hands == 2
    assert t.max_hands == 2

    HandTracker(camera_index=0)                       # default: unchanged
    assert captured["options"].num_hands == 1

    HandTracker(camera_index=0, max_num_hands=2)      # legacy kwarg honored
    assert captured["options"].num_hands == 2


def test_empty_result_contract():
    t = object.__new__(HandTracker)                    # headless, no __init__
    r = t._empty_result()
    # legacy keys unchanged ...
    assert r["hand_found"] is False
    assert r["landmarks"] is None
    assert r["index_pos"] is None
    assert r["pinch_distance"] == 1.0
    assert r["frame"] is None
    # ... plus the always-present v15.2 key
    assert r["hands"] == []
    sentinel = object()
    assert t._empty_result(frame=sentinel)["frame"] is sentinel


# ── read() contract via faked mediapipe result (no real inference) ─────────

class _FakeDetector:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def detect_for_video(self, mp_image, timestamp_ms):
        self.calls.append(timestamp_ms)
        return self._result


def _fake_result(n_hands=2, labels=("Right", "Left"), scores=(0.91, 0.84)):
    pts = [_hand_points(0.3, 0.5), _hand_points(0.7, 0.5)][:n_hands]
    hands = [[types.SimpleNamespace(x=p[0], y=p[1], z=p[2]) for p in h]
             for h in pts]
    handedness = [[types.SimpleNamespace(category_name=label, score=score,
                                         index=0, display_name="")]
                  for label, score in zip(labels[:n_hands], scores[:n_hands])]
    return types.SimpleNamespace(hand_landmarks=hands,
                                 handedness=handedness,
                                 hand_world_landmarks=[])


def _bare_tracker(result, max_hands=2):
    t = object.__new__(HandTracker)                    # skip hardware __init__
    t.cap = types.SimpleNamespace(
        read=lambda: (True, np.zeros((48, 64, 3), np.uint8)))
    t.detector = _FakeDetector(result)
    t.frame_timestamp_ms = 0
    t.last_frame = None
    t.max_hands = max_hands
    return t


@pytest.fixture
def fake_mp_image(monkeypatch):
    """mp.Image dlopens libGLESv2 (unavailable headless) — stub it."""
    def _fake_image(*, image_format=None, data=None):
        return types.SimpleNamespace(image_format=image_format, data=data)
    monkeypatch.setattr(tracker_mod.mp, "Image", _fake_image)


def test_read_two_hands_contract(fake_mp_image):
    t = _bare_tracker(_fake_result(n_hands=2))
    r = t.read()
    assert r["hand_found"] is True
    assert len(r["hands"]) == 2
    for entry in r["hands"]:
        assert set(entry.keys()) == _ENTRY_KEYS
        assert isinstance(entry["pinch_distance"], float)
        assert len(entry["landmarks"]) == 21
    # raw labels + mirrored-input convention (see tracker docstring)
    assert r["hands"][0]["handedness"] == "Right"
    assert r["hands"][0]["is_left_user"] is False
    assert r["hands"][0]["handedness_score"] == pytest.approx(0.91)
    assert r["hands"][1]["handedness"] == "Left"
    assert r["hands"][1]["is_left_user"] is True
    # legacy single-hand keys = primary hand = hands[0] (unchanged behavior)
    assert r["landmarks"] is r["hands"][0]["landmarks"]
    assert np.allclose(r["index_pos"], r["hands"][0]["index_pos"])
    assert r["pinch_distance"] == pytest.approx(0.02)
    assert r["frame"] is not None
    # attribute access (.x/.y/.z) preserved for the gesture engine
    assert isinstance(r["landmarks"][8].x, float)


def test_read_single_hand_and_no_hand(fake_mp_image):
    t = _bare_tracker(_fake_result(n_hands=1))
    r = t.read()
    assert r["hand_found"] is True
    assert len(r["hands"]) == 1
    assert r["landmarks"] is r["hands"][0]["landmarks"]

    t2 = _bare_tracker(types.SimpleNamespace(
        hand_landmarks=[], handedness=[], hand_world_landmarks=[]))
    r2 = t2.read()
    assert r2["hand_found"] is False
    assert r2["hands"] == []
    assert r2["landmarks"] is None

    # camera loss -> same empty contract
    t3 = object.__new__(HandTracker)
    t3.cap = types.SimpleNamespace(read=lambda: (False, None))
    r3 = t3.read()
    assert r3["hands"] == [] and r3["hand_found"] is False


def test_handedness_mirrored_input_convention(fake_mp_image):
    """MediaPipe docs: handedness assumes MIRRORED (selfie) input; the
    tracker flips the frame, so the raw label already IS the user's true
    hand -> is_left_user = (raw_label == 'Left')."""
    t = _bare_tracker(_fake_result(n_hands=2, labels=("Left", "Right")))
    r = t.read()
    assert r["hands"][0]["is_left_user"] is True     # raw "Left"
    assert r["hands"][1]["is_left_user"] is False    # raw "Right"
    # missing handedness degrades to "Unknown" without crashing
    t2 = _bare_tracker(types.SimpleNamespace(
        hand_landmarks=[[types.SimpleNamespace(x=0.1, y=0.1, z=0.0)] * 21],
        handedness=[], hand_world_landmarks=[]))
    r2 = t2.read()
    assert r2["hands"][0]["handedness"] == "Unknown"
    assert r2["hands"][0]["handedness_score"] == 0.0
    assert r2["hands"][0]["is_left_user"] is False


# ═══════════════════════════════════════════════════════════════════════════
# two_hand.py — report contract
# ═══════════════════════════════════════════════════════════════════════════

def test_report_contract_on_every_frame():
    rec = TwoHandGestureRecognizer()
    script = [None, [], one_hand(), two_hands(), two_hands(), two_hands(),
              two_hands(), two_hands()]
    outs = []
    for i, hands in enumerate(script):
        outs.append(rec.update(hands, T0 + i * DT))
    for o in outs:
        assert set(o.keys()) == _REPORT_KEYS
    for o in outs[:3]:  # fully inactive frames: neutral, no stale geometry
        assert o["active"] is False and o["gesture"] is None
        assert o["scale"] == 1.0 and o["angle_delta_deg"] == 0.0
        assert o["centroid_delta"] is None and o["confidence"] == 0.0
    for o in outs[3:6]:  # engage counting frames: inactive but not confidence-0
        assert o["active"] is False and o["gesture"] is None
        assert o["scale"] == 1.0 and o["angle_delta_deg"] == 0.0
        assert o["centroid_delta"] is None
        assert 0.0 < o["confidence"] <= 1.0
    assert outs[6]["active"] is True          # 4th consecutive -> engaged
    assert outs[6]["gesture"] == TWO_HAND_HOLD
    assert outs[-1]["active"] is True
    assert outs[-1]["gesture"] == TWO_HAND_HOLD


def test_gesture_constants_match_spec():
    assert set(TwoHandGestureRecognizer.GESTURES) == {
        "TWO_HAND_HOLD", "TWO_HAND_ZOOM", "TWO_HAND_ROTATE", "TWO_HAND_DRAG"}


def test_update_handles_none_empty_and_empty_landmarks():
    rec = TwoHandGestureRecognizer()
    now = T0
    cases = [
        (None, ()),
        ([], ()),
        ([{"landmarks": []}, {"landmarks": []}], ("Unknown", "Unknown")),
    ]
    for hands, expected_labels in cases:
        o = rec.update(hands, now)
        now += DT
        assert o["active"] is False and o["gesture"] is None
        assert o["scale"] == 1.0 and o["confidence"] == 0.0
        assert o["handedness"] == expected_labels


# ═══════════════════════════════════════════════════════════════════════════
# two_hand.py — engage / hysteresis / grace / reset / cooldown
# ═══════════════════════════════════════════════════════════════════════════

def test_engage_requires_consecutive_frames():
    rec = TwoHandGestureRecognizer(engage_frames=4)
    outs = []
    for i in range(3):
        outs.append(rec.update(two_hands(), T0 + i * DT))
    assert all(o["active"] is False for o in outs)      # 3 < 4
    outs.append(rec.update(two_hands(), T0 + 3 * DT))
    assert outs[-1]["active"] is True                   # 4th consecutive
    assert rec.engaged is True


def test_engage_requires_both_pinched():
    rec = TwoHandGestureRecognizer()
    for i in range(10):
        o = rec.update(two_hands(rc=(0.7, 0.5), pinch=0.30), T0 + i * DT)
        assert o["active"] is False
    assert rec.engaged is False


def test_candidate_streak_resets_on_miss():
    rec = TwoHandGestureRecognizer(engage_frames=4)
    t = T0
    for _ in range(3):                       # counting 3 ...
        rec.update(two_hands(), t); t += DT
    rec.update([], t); t += DT               # ... broken by one miss frame
    o = rec.update(two_hands(), t)           # streak restarts at 1
    assert o["active"] is False
    for _ in range(2):
        rec.update(two_hands(), t); t += DT
    o = rec.update(two_hands(), t)           # 4th consecutive -> engage
    assert o["active"] is True


def test_grace_within_limit_resumes_same_baseline():
    rec = TwoHandGestureRecognizer()          # grace_frames=5
    engage(rec)
    t = T0 + rec.engage_frames * DT
    for i in range(3):                        # transient single-hand loss
        o = rec.update(one_hand(), t + i * DT)
        assert o["active"] is False and o["gesture"] is None
        assert o["scale"] == 1.0 and o["confidence"] == 0.0
    assert rec.engaged is True                # retained through grace
    o = rec.update(two_hands(), t + 3 * DT)   # hands return at baseline
    assert o["active"] is True
    assert o["gesture"] == TWO_HAND_HOLD
    assert o["scale"] == pytest.approx(1.0, abs=1e-9)   # SAME baseline


def test_loss_beyond_grace_fully_resets_and_recaptures_baseline():
    rec = TwoHandGestureRecognizer(cooldown_s=0.0)   # isolate baseline logic
    engage(rec)
    t = T0 + rec.engage_frames * DT
    for i in range(rec.grace_frames + 1):     # 6 misses > grace 5
        rec.update(one_hand(), t + i * DT)
    assert rec.engaged is False
    # re-present at a NEW distance (0.5): stale baseline (0.4) would read
    # scale 1.25 (ZOOM); a fresh baseline must read scale ~1.0 (HOLD).
    t2 = t + (rec.grace_frames + 2) * DT
    first = rec.update(two_hands(lc=(0.25, 0.5), rc=(0.75, 0.5)), t2)
    assert first["active"] is False           # engage counting restarted
    outs = []
    for i in range(rec.engage_frames):
        outs.append(rec.update(two_hands(lc=(0.25, 0.5), rc=(0.75, 0.5)),
                               t2 + (i + 1) * DT))
    assert outs[-1]["active"] is True
    assert outs[-1]["gesture"] == TWO_HAND_HOLD
    assert outs[-1]["scale"] == pytest.approx(1.0, abs=1e-9)


def test_cooldown_blocks_reengage_after_full_reset():
    rec = TwoHandGestureRecognizer()          # cooldown_s=0.5
    engage(rec)
    t_reset = T0 + rec.engage_frames * DT
    for i in range(rec.grace_frames + 1):     # vanish beyond grace -> reset
        assert rec.update(one_hand(), t_reset + i * DT)["active"] is False
    assert rec.engaged is False
    # cooldown_until mirrors recognizer arithmetic (6th miss at i=5)
    cooldown_until = t_reset + rec.grace_frames * DT + rec.cooldown_s
    t0 = t_reset + (rec.grace_frames + 1) * DT
    actives = []
    for i in range(1, 41):
        o = rec.update(two_hands(), t0 + i * DT)
        if o["active"]:
            actives.append(i)
    allowed = [i for i in range(1, 41) if (t0 + i * DT) >= cooldown_until]
    first_expected = allowed[0] + rec.engage_frames - 1
    assert actives and actives[0] == first_expected


def test_cooldown_not_armed_without_engagement():
    rec = TwoHandGestureRecognizer(cooldown_s=10.0)
    rec.update(two_hands(), T0)               # 1 counting frame, never engaged
    for i in range(8):
        rec.update([], T0 + (i + 1) * DT)     # hands vanish, no reset happened
    o = rec.update(two_hands(), T0 + 9 * DT)
    assert o["active"] is False
    for i in range(3):                        # counting resumes immediately
        o = rec.update(two_hands(), T0 + (10 + i) * DT)
    assert o["active"] is True                # no 10s lockout was applied


def test_reset_clears_everything():
    rec = TwoHandGestureRecognizer()
    engage(rec)
    rec.reset()
    assert rec.engaged is False
    o = rec.update(two_hands(), T0 + 99 * DT)
    assert o["active"] is False               # counting from scratch
    outs = engage(rec, t0=T0 + 100.0)
    assert outs[-1]["active"] is True         # and cooldown was NOT armed


def test_more_than_two_hands_uses_first_two():
    rec = TwoHandGestureRecognizer()
    triple = two_hands() + [two_hands()[0]]
    for i in range(rec.engage_frames):
        o = rec.update(triple, T0 + i * DT)
    assert o["active"] is True


def test_pinch_fallback_from_tuple_landmarks():
    """No pinch_distance key: recognizer derives it from tips 4/8 and
    accepts the plain (x, y, z) tuple landmark format."""
    rec = TwoHandGestureRecognizer()
    lms = [_hand_dict(0.3, 0.5, "Left"), _hand_dict(0.7, 0.5, "Right")]
    for h in lms:
        del h["pinch_distance"]               # force the fallback path
    outs = engage_from_lists(rec, lms)
    assert outs[-1]["active"] is True


def engage_from_lists(rec, lists, t0=T0):
    outs = []
    for i in range(rec.engage_frames):
        outs.append(rec.update(lists, t0 + i * DT))
    return outs


# ═══════════════════════════════════════════════════════════════════════════
# two_hand.py — ZOOM / ROTATE / DRAG geometry + sign conventions
# ═══════════════════════════════════════════════════════════════════════════

def test_zoom_out_beyond_deadzone_scale_gt_1():
    rec = TwoHandGestureRecognizer()
    engage(rec)                               # baseline centers 0.3 / 0.7
    o = rec.update(two_hands(rc=(0.74, 0.5)), T0 + 10 * DT)
    assert o["active"] is True and o["gesture"] == TWO_HAND_ZOOM
    assert o["scale"] == pytest.approx(0.44 / 0.40, rel=1e-9)   # > 1 = apart
    o = rec.update(two_hands(rc=(0.78, 0.5)), T0 + 11 * DT)
    assert o["scale"] == pytest.approx(0.48 / 0.40, rel=1e-9)   # monotonic


def test_zoom_in_scale_lt_1():
    rec = TwoHandGestureRecognizer()
    engage(rec)
    o = rec.update(two_hands(rc=(0.66, 0.5)), T0 + 10 * DT)
    assert o["gesture"] == TWO_HAND_ZOOM
    assert o["scale"] == pytest.approx(0.36 / 0.40, rel=1e-9)   # < 1 = together


def test_zoom_deadzone_does_not_fire():
    rec = TwoHandGestureRecognizer()          # zoom_deadzone=0.03
    engage(rec)
    o = rec.update(two_hands(rc=(0.71, 0.5)), T0 + 10 * DT)   # scale 1.025
    assert o["active"] is True
    assert o["gesture"] == TWO_HAND_HOLD
    assert o["scale"] == pytest.approx(1.025, rel=1e-9)         # still reported


def test_rotate_cw_is_positive_degrees():
    """Sign convention: positive angle_delta_deg = clockwise as displayed
    (mirrored on-screen view, y-down image coordinates)."""
    rec = TwoHandGestureRecognizer()
    lc0, rc0 = rotated_positions(0.0)
    engage(rec, lc0, rc0)
    lc1, rc1 = rotated_positions(20.0)        # hands turn clockwise
    o = rec.update(two_hands(lc1, rc1), T0 + 10 * DT)
    assert o["gesture"] == TWO_HAND_ROTATE
    assert o["angle_delta_deg"] == pytest.approx(20.0, abs=1e-6)
    assert o["scale"] == pytest.approx(1.0, rel=1e-9)  # pure rotation


def test_rotate_ccw_is_negative_degrees():
    rec = TwoHandGestureRecognizer()
    lc0, rc0 = rotated_positions(0.0)
    engage(rec, lc0, rc0)
    lc1, rc1 = rotated_positions(-20.0)
    o = rec.update(two_hands(lc1, rc1), T0 + 10 * DT)
    assert o["gesture"] == TWO_HAND_ROTATE
    assert o["angle_delta_deg"] == pytest.approx(-20.0, abs=1e-6)


def test_rotate_wraps_across_180():
    rec = TwoHandGestureRecognizer()
    lc0, rc0 = rotated_positions(170.0)
    engage(rec, lc0, rc0)
    lc1, rc1 = rotated_positions(-170.0)      # +190 deg == wrapped +20
    o = rec.update(two_hands(lc1, rc1), T0 + 10 * DT)
    assert o["gesture"] == TWO_HAND_ROTATE
    assert o["angle_delta_deg"] == pytest.approx(20.0, abs=1e-6)
    assert -180.0 < o["angle_delta_deg"] <= 180.0


def test_rotate_deadzone_does_not_fire():
    rec = TwoHandGestureRecognizer()          # rotate_deadzone_deg=8
    lc0, rc0 = rotated_positions(0.0)
    engage(rec, lc0, rc0)
    lc1, rc1 = rotated_positions(5.0)
    o = rec.update(two_hands(lc1, rc1), T0 + 10 * DT)
    assert o["gesture"] == TWO_HAND_HOLD
    assert o["angle_delta_deg"] == pytest.approx(5.0, abs=1e-6)


def test_drag_translation():
    rec = TwoHandGestureRecognizer()
    engage(rec)
    o = rec.update(two_hands(lc=(0.35, 0.5), rc=(0.75, 0.5)), T0 + 10 * DT)
    assert o["gesture"] == TWO_HAND_DRAG
    assert o["centroid_delta"] == pytest.approx((0.05, 0.0), abs=1e-9)
    assert o["scale"] == pytest.approx(1.0, rel=1e-9)      # pure translation
    assert o["angle_delta_deg"] == pytest.approx(0.0, abs=1e-9)
    o = rec.update(two_hands(lc=(0.33, 0.52), rc=(0.73, 0.52)), T0 + 11 * DT)
    assert o["gesture"] == TWO_HAND_DRAG
    assert o["centroid_delta"] == pytest.approx((0.03, 0.02), abs=1e-9)


def test_drag_deadzone_does_not_fire():
    rec = TwoHandGestureRecognizer()          # drag_deadzone=0.02
    engage(rec)
    o = rec.update(two_hands(lc=(0.305, 0.5), rc=(0.705, 0.5)), T0 + 10 * DT)
    assert o["gesture"] == TWO_HAND_HOLD
    assert o["centroid_delta"] == pytest.approx((0.005, 0.0), abs=1e-9)


def test_mutual_exclusion_dominant_delta_wins():
    """Zoom XOR rotate XOR drag: the largest deadzone-normalized delta
    picks the single reported gesture (ties: ZOOM > ROTATE > DRAG)."""
    mid = (0.5, 0.5)

    def combined(scale, deg, shift):
        v = (0.4 * scale * math.cos(math.radians(deg)),
             0.4 * scale * math.sin(math.radians(deg)))
        m = (mid[0] + shift[0], mid[1] + shift[1])
        return (m[0] - v[0] / 2, m[1] - v[1] / 2), (m[0] + v[0] / 2, m[1] + v[1] / 2)

    cases = [
        # (scale, deg, shift, expected dominant)
        (1.10, 20.0, (0.05, 0.0), TWO_HAND_ZOOM),   # scores 3.33 / 2.5 / 2.5
        (1.04, 30.0, (0.05, 0.0), TWO_HAND_ROTATE),  # scores 1.33 / 3.75 / 2.5
        (1.02, 5.0, (0.05, 0.0), TWO_HAND_DRAG),     # scores 0.67 / 0.63 / 2.5
    ]
    for scale, deg, shift, expected in cases:
        rec = TwoHandGestureRecognizer()
        lc0, rc0 = rotated_positions(0.0)
        engage(rec, lc0, rc0)
        lc1, rc1 = combined(scale, deg, shift)
        o = rec.update(two_hands(lc1, rc1), T0 + 10 * DT)
        assert o["active"] is True
        assert o["gesture"] == expected          # exactly one winner
        assert o["gesture"] != TWO_HAND_HOLD


# ═══════════════════════════════════════════════════════════════════════════
# two_hand.py — confidence, determinism, throughput
# ═══════════════════════════════════════════════════════════════════════════

def test_confidence_bounds_and_semantics():
    rec = TwoHandGestureRecognizer(pinch_threshold=0.12)
    outs = run_mixed_script(rec)
    for o in outs:
        assert 0.0 <= o["confidence"] <= 1.0
        if o["active"]:
            assert o["confidence"] > 0.0
    # engaged with pinch 0.02/0.12 -> pinch confidence 1 - 1/6
    engaged = [o for o in outs if o["active"]]
    assert engaged
    assert engaged[0]["confidence"] == pytest.approx(1.0 * (1 - 0.02 / 0.12),
                                                     rel=1e-6)
    # fully closed pinch -> confidence 1.0
    rec2 = TwoHandGestureRecognizer(pinch_threshold=0.12)
    outs2 = engage(rec2, pinch=0.0)
    assert outs2[-1]["confidence"] == pytest.approx(1.0)


def run_mixed_script(rec):
    script = ([None, [], one_hand()]
              + [two_hands()] * 6
              + [one_hand()] * 3
              + [two_hands(rc=(0.78, 0.5))] * 2       # zoom out
              + [two_hands()] * 2
              + [two_hands(rc=(0.74, 0.5), pinch=0.30)] * 2   # pose broken
              + [two_hands()] * 8)
    outs = []
    for i, hands in enumerate(script):
        outs.append(rec.update(hands, T0 + i * DT))
    return outs


def test_determinism_same_script_same_outputs():
    outs_a = run_mixed_script(TwoHandGestureRecognizer())
    outs_b = run_mixed_script(TwoHandGestureRecognizer())
    assert outs_a == outs_b


def test_update_throughput_under_1ms():
    rec = TwoHandGestureRecognizer()
    engage(rec)
    hands = two_hands()
    t = T0 + 50.0
    n = 200
    start = time.perf_counter()
    for i in range(n):
        t += DT
        rec.update(hands, t)
    per_update = (time.perf_counter() - start) / n
    assert per_update < 0.001, (
        f"update() averaged {per_update * 1000:.3f} ms (>1 ms budget)")


# ═══════════════════════════════════════════════════════════════════════════
# config.two_hand
# ═══════════════════════════════════════════════════════════════════════════

def test_config_two_hand_default_false():
    assert Config().two_hand is False


def test_config_two_hand_save_roundtrip(tmp_path, monkeypatch):
    path = str(tmp_path / "config.toml")
    cfg = Config()
    cfg.two_hand = True
    monkeypatch.setattr(config_mod, "CONFIG_PATH", path, raising=False)
    cfg.save_defaults()
    text = open(path).read()
    assert "[v10]" in text
    assert "two_hand = true" in text          # saved next to the v10 flags
    cfg2 = Config()
    cfg2.load()
    assert cfg2.two_hand is True


def test_config_two_hand_absent_key_keeps_default(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[v10]\noffline = true\n')
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(path), raising=False)
    cfg = Config()
    cfg.load()
    assert cfg.offline is True
    assert cfg.two_hand is False              # missing key -> default
