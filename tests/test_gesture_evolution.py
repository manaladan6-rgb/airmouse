"""Gesture engine evolution tests — Task 2-b (v15.2).

Covers the new detectors + emitters added to airmouse.gestures:

  - new poses THUMBS_DOWN / FOUR / FIVE (+ FOUR/FIVE vs PALM precedence)
  - vertical swipes (SwipeDetector) + horizontal/vertical mutual exclusion
  - TrajectoryDetector  (CIRCLE_CW / CIRCLE_CCW)
  - DepthDetector       (PUSH / PULL via z-velocity + area-velocity)
  - OscillationDetector (SHAKE vs WAVE disambiguation)
  - GestureStateMachine pinch events (PINCH_HOLD / PINCH_RELEASE /
    DOUBLE_PINCH) + plain PINCH tap regression
  - registry backward-compat for the new labels

Everything is deterministic and hardware-free: synthetic 21-landmark
hands built from MediaPipe-style .x/.y/.z attribute objects, with
geometry matched to the hysteresis bands in airmouse.gestures:

  - extended finger: collinear MCP→PIP→DIP→TIP chain (PIP angle ≈ 180°,
    above the 160° "up" threshold)
  - curled finger:   folded chain (PIP angle ≈ 30°, below the 145°
    "down" threshold)
  - extended thumb:  CMC-MCP-IP angle > 150° (up- and down-pointing
    variants)
  - folded thumb:    CMC-MCP-IP angle ≈ 130°

Image coordinates: x right, y DOWN (MediaPipe convention), so "up the
image" = smaller y.
"""
from __future__ import annotations

import math
import time

import numpy as np
import pytest

from airmouse.gestures import (
    Gesture,
    GESTURE_INFO,
    MOTION_GESTURE_INFO,
    OscillationDetector,
    DepthDetector,
    GestureStateMachine,
    SwipeDetector,
    TrajectoryDetector,
    recognize_gesture,
    reset_finger_state,
)
from airmouse.gesture_registry import GestureRegistry, Gestures


# ── synthetic landmark factory ───────────────────────────────────────────

class LM:
    """Minimal MediaPipe-style landmark (x, y, z attribute access)."""
    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


# wrist + finger MCP anchors (image coords, y down)
_WRIST = (0.50, 0.78)
_MCPS = {
    "index": (0.44, 0.60),
    "middle": (0.50, 0.59),
    "ring": (0.56, 0.60),
    "pinky": (0.62, 0.62),
}


def _extended(mcp):
    """Straight vertical finger: PIP angle = 180° (extended)."""
    mx, my = mcp
    return [(mx, my), (mx, my - 0.08), (mx, my - 0.14), (mx, my - 0.19)]


def _curled(mcp):
    """Folded finger: PIP angle ≈ 30° (well under the 145° down threshold)."""
    mx, my = mcp
    pip = (mx, my - 0.05)
    dip = (pip[0] + 0.018, pip[1] + 0.032)
    tip = (dip[0] - 0.004, dip[1] + 0.030)
    return [(mx, my), pip, dip, tip]


def _thumb_up():
    """Straight thumb pointing UP (CMC-MCP-IP ≈ 161°, tip above IP/MCP)."""
    return [(0.42, 0.70), (0.395, 0.655), (0.385, 0.60), (0.38, 0.545)]


def _thumb_down():
    """Straight thumb pointing DOWN (CMC-MCP-IP ≈ 158°, tip below IP/MCP)."""
    return [(0.43, 0.60), (0.40, 0.66), (0.395, 0.725), (0.39, 0.78)]


def _thumb_folded():
    """Thumb folded across the palm (CMC-MCP-IP ≈ 130° → not "up")."""
    return [(0.42, 0.70), (0.40, 0.66), (0.37, 0.652), (0.36, 0.647)]


def _thumb_pinch(index_tip):
    """Thumb reaching out to touch the index tip."""
    tx, ty = index_tip
    return [(tx - 0.03, ty + 0.12), (tx - 0.02, ty + 0.075),
            (tx - 0.01, ty + 0.035), (tx, ty)]


_POSE_FINGERS = {
    # pose: (index, middle, ring, pinky) → True=extended, False=curled
    "five": (True, True, True, True),
    "four": (True, True, True, True),
    "palm_mixed": (True, False, True, True),   # 3 fingers + thumb → PALM
    "three": (True, True, True, False),
    "peace": (True, True, False, False),
    "point": (True, False, False, False),
    "fist": (False, False, False, False),
    "thumbs_up": (False, False, False, False),
    "thumbs_down": (False, False, False, False),
    "pinch": (True, False, False, False),
}

_POSE_THUMB = {
    "five": _thumb_up,
    "four": _thumb_folded,
    "palm_mixed": _thumb_up,
    "three": _thumb_folded,
    "peace": _thumb_folded,
    "point": _thumb_folded,
    "fist": _thumb_folded,
    "thumbs_up": _thumb_up,
    "thumbs_down": _thumb_down,
    "pinch": None,  # built after the index chain (needs its tip)
}


def make_hand(pose="five", jitter=0.0, seed=0):
    """Build a 21-landmark synthetic hand for `pose` (deterministic)."""
    rng = np.random.default_rng(seed)
    pts = [_WRIST]                                   # 0 wrist
    for name in ("thumb", "index", "middle", "ring", "pinky"):
        if name == "thumb":
            if pose == "pinch":
                idx_chain = _extended(_MCPS["index"])
                pts.extend(_thumb_pinch(idx_chain[3]))
            else:
                pts.extend(_POSE_THUMB[pose]())
        else:
            chain = _extended(_MCPS[name]) if _POSE_FINGERS[pose][
                list(_MCPS).index(name)] else _curled(_MCPS[name])
            pts.extend(chain)
    assert len(pts) == 21, len(pts)
    if jitter > 0:
        noise = rng.normal(0.0, jitter, (len(pts), 2))
        pts = [(x + dx, y + dy) for (x, y), (dx, dy) in zip(pts, noise)]
    return [LM(x, y) for (x, y) in pts]


def recognize(pose, jitter=0.0, seed=0, pinch_threshold=0.07):
    """Reset hysteresis (as the live loop does on hand loss) + classify."""
    reset_finger_state()
    return recognize_gesture(make_hand(pose, jitter=jitter, seed=seed),
                             pinch_threshold=pinch_threshold)


def scale_hand(landmarks, factor, origin=(0.5, 0.65)):
    """Scale a hand about an origin (simulates depth via apparent size)."""
    ox, oy = origin
    out = []
    for lm in landmarks:
        out.append(LM(ox + (lm.x - ox) * factor, oy + (lm.y - oy) * factor,
                      lm.z))
    return out


def set_z(landmarks, z):
    """Return a copy of the hand with every landmark z set to `z`."""
    return [LM(lm.x, lm.y, z) for lm in landmarks]


# ── 1. new Gesture members + docs ─────────────────────────────────────────

def test_new_gesture_member_names():
    assert Gesture.THUMBS_DOWN == "thumbs_down"
    assert Gesture.FOUR == "four"
    assert Gesture.FIVE == "five"
    assert Gesture.SWIPE_UP == "swipe_up"
    assert Gesture.SWIPE_DOWN == "swipe_down"
    assert Gesture.CIRCLE_CW == "circle_cw"
    assert Gesture.CIRCLE_CCW == "circle_ccw"
    assert Gesture.PUSH == "push"
    assert Gesture.PULL == "pull"
    assert Gesture.SHAKE == "shake"
    assert Gesture.WAVE == "wave"
    # pinch-event labels align exactly with the registry vocabulary
    assert Gesture.PINCH_HOLD == Gestures.PINCH_HOLD == "pinch_hold"
    assert Gesture.PINCH_RELEASE == Gestures.PINCH_RELEASE == "pinch_release"
    assert Gesture.DOUBLE_PINCH == Gestures.DOUBLE_PINCH == "double_pinch"


def test_existing_gesture_members_unchanged():
    # the pre-existing vocabulary must not move
    for name, val in [("POINTING", "pointing"), ("PEACE", "peace"),
                      ("THREE", "three"), ("PALM", "palm"),
                      ("FIST", "fist"), ("PINCH", "pinch"),
                      ("THUMBS_UP", "thumbs_up"), ("PINKY", "pinky"),
                      ("GUN", "gun"), ("ROCK", "rock"), ("SHAKA", "shaka"),
                      ("OK", "ok"), ("RING", "ring"), ("SIX", "six"),
                      ("SWIPE_LEFT", "swipe_left"),
                      ("SWIPE_RIGHT", "swipe_right"), ("NONE", "none")]:
        assert getattr(Gesture, name) == val


def test_gesture_info_documents_new_poses():
    for g in (Gesture.THUMBS_DOWN, Gesture.FOUR, Gesture.FIVE):
        assert g in GESTURE_INFO
        assert GESTURE_INFO[g]["name"] and GESTURE_INFO[g]["desc"]
    for g in (Gesture.SWIPE_UP, Gesture.SWIPE_DOWN, Gesture.CIRCLE_CW,
              Gesture.CIRCLE_CCW, Gesture.PUSH, Gesture.PULL,
              Gesture.SHAKE, Gesture.WAVE):
        assert g in MOTION_GESTURE_INFO


# ── 2. new poses ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("seed,jit", [(0, 0.0), (1, 0.001), (2, 0.002)])
def test_thumbs_down_positive(seed, jit):
    r = recognize("thumbs_down", jitter=jit, seed=seed)
    assert r["gesture"] == Gesture.THUMBS_DOWN


def test_thumbs_down_negatives():
    # thumb straight UP must stay THUMBS_UP (near-miss on direction)
    assert recognize("thumbs_up")["gesture"] == Gesture.THUMBS_UP
    # fist (thumb folded) must NOT be thumbs_down
    assert recognize("fist")["gesture"] == Gesture.FIST


@pytest.mark.parametrize("seed,jit", [(0, 0.0), (1, 0.001), (2, 0.002)])
def test_four_positive(seed, jit):
    r = recognize("four", jitter=jit, seed=seed)
    assert r["gesture"] == Gesture.FOUR
    assert not r["thumb_extended"]      # folded thumb (numpy bool)


@pytest.mark.parametrize("seed,jit", [(0, 0.0), (1, 0.001), (2, 0.002)])
def test_five_positive(seed, jit):
    r = recognize("five", jitter=jit, seed=seed)
    assert r["gesture"] == Gesture.FIVE
    assert r["thumb_extended"]          # thumb included (numpy bool)


def test_four_five_palm_precedence():
    # most specific pose wins: all-5 → FIVE (not PALM/FOUR)
    assert recognize("five")["gesture"] == Gesture.FIVE
    # 4 fingers + folded thumb → FOUR (not PALM/FIVE)
    assert recognize("four")["gesture"] == Gesture.FOUR
    # mixed 4-finger+thumb combination → PALM (unchanged catch-all)
    assert recognize("palm_mixed")["gesture"] == Gesture.PALM
    # 3 fingers + folded thumb → THREE (existing behavior preserved)
    assert recognize("three")["gesture"] == Gesture.THREE


def test_four_five_negatives():
    # near-misses: FIVE must not fire for a thumbless open hand and
    # FOUR must not fire when the thumb is up
    assert recognize("five")["gesture"] != Gesture.FOUR
    assert recognize("four")["gesture"] != Gesture.FIVE
    # pinch must still win over any open-finger pose (top precedence)
    assert recognize("pinch")["gesture"] == Gesture.PINCH


def test_existing_poses_unchanged():
    assert recognize("point")["gesture"] == Gesture.POINTING
    assert recognize("peace")["gesture"] == Gesture.PEACE
    assert recognize("fist")["gesture"] == Gesture.FIST
    assert recognize("thumbs_up")["gesture"] == Gesture.THUMBS_UP


@pytest.mark.parametrize("pose", ["five", "four", "thumbs_down", "pinch",
                                  "fist", "palm_mixed", "point", "three"])
def test_confidence_bounds(pose):
    r = recognize(pose)
    assert 0.0 <= r["confidence"] <= 1.0


# ── 3. vertical swipes (SwipeDetector) ────────────────────────────────────

def _run_swipe(deltas, threshold=0.03, t0=10.0, dt=1 / 30.0):
    """Feed a delta path [(dx, dy), ...] to a fresh SwipeDetector."""
    det = SwipeDetector(speed_threshold=threshold)
    pos = np.array([0.5, 0.5], dtype=float)
    prev = None
    results = []
    for i, (dx, dy) in enumerate(deltas):
        new = pos + np.array([dx, dy])
        results.append(det.update(new, prev, t0 + i * dt))
        prev = pos
        pos = new
    return results


def test_swipe_up():
    results = _run_swipe([(0.0, -0.05)] * 8)
    assert Gesture.SWIPE_UP in results
    assert Gesture.SWIPE_DOWN not in results
    assert Gesture.SWIPE_LEFT not in results
    assert Gesture.SWIPE_RIGHT not in results


def test_swipe_down():
    results = _run_swipe([(0.0, 0.05)] * 8)
    assert Gesture.SWIPE_DOWN in results
    assert Gesture.SWIPE_UP not in results
    assert Gesture.SWIPE_LEFT not in results
    assert Gesture.SWIPE_RIGHT not in results


def test_swipe_horizontal_still_works():
    # v4.0 behavior of the extended class is preserved
    assert Gesture.SWIPE_RIGHT in _run_swipe([(0.05, 0.0)] * 8)
    assert Gesture.SWIPE_LEFT in _run_swipe([(-0.05, 0.0)] * 8)


def test_swipe_mutual_exclusion_dominant_axis():
    # both axes over threshold: ONLY the dominant axis may fire, and at
    # most one swipe per frame — never a horizontal AND a vertical.
    res = _run_swipe([(0.05, -0.04)] * 8)                 # x dominant
    fired = [g for g in res if g != Gesture.NONE]
    assert fired, "expected a swipe to fire"
    assert set(fired) == {Gesture.SWIPE_RIGHT}
    assert Gesture.SWIPE_UP not in fired and Gesture.SWIPE_DOWN not in fired

    res = _run_swipe([(0.04, 0.06)] * 8)                  # y dominant
    fired = [g for g in res if g != Gesture.NONE]
    assert set(fired) == {Gesture.SWIPE_DOWN}
    assert Gesture.SWIPE_LEFT not in fired and Gesture.SWIPE_RIGHT not in fired


def test_swipe_vertical_near_miss():
    # slow vertical drift below threshold must NOT fire
    res = _run_swipe([(0.0, 0.01)] * 10)
    assert all(g == Gesture.NONE for g in res)


def test_swipe_updown_cooldown():
    det = SwipeDetector(speed_threshold=0.03, cooldown=0.5)
    t0 = 10.0
    pos = np.array([0.5, 0.9])
    prev = None
    fired = []
    for i in range(30):
        new = pos - np.array([0.0, 0.05])   # constant upward motion
        fired.append(det.update(new, prev, t0 + i / 30.0))
        prev = pos
        pos = new
    ups = [g for g in fired if g == Gesture.SWIPE_UP]
    assert len(ups) >= 1
    # after each fire the 0.5s cooldown must suppress immediate re-fires
    idx = fired.index(Gesture.SWIPE_UP)
    assert all(g == Gesture.NONE for g in fired[idx + 1:idx + 15])


# ── 4. TrajectoryDetector (circles) ───────────────────────────────────────

def _circle(cx=0.5, cy=0.5, r=0.12, start=0.0, sweep=360.0, steps=16,
            cw=True, t0=10.0, dt=1 / 30.0, jitter=0.0, seed=0):
    rng = np.random.default_rng(seed)
    det = TrajectoryDetector()
    results = []
    fire_conf = 0.0
    for i in range(steps + 1):
        ang = math.radians(start + sweep * i / steps) * (1 if cw else -1)
        x = cx + r * math.cos(ang)
        y = cy + r * math.sin(ang)          # image coords: +angle = CW
        if jitter:
            x += rng.normal(0, jitter)
            y += rng.normal(0, jitter)
        g = det.update((x, y), t0 + i * dt)
        if g != Gesture.NONE:
            fire_conf = det.last_confidence
        results.append(g)
    return det, results, fire_conf


def test_circle_cw_positive():
    det, results, fire_conf = _circle(cw=True)
    assert Gesture.CIRCLE_CW in results
    assert Gesture.CIRCLE_CCW not in results
    assert 0.0 <= fire_conf <= 1.0
    assert fire_conf > 0.5              # clean circle → high circularity fit


def test_circle_ccw_positive():
    det, results, _conf = _circle(cw=False)
    assert Gesture.CIRCLE_CCW in results
    assert Gesture.CIRCLE_CW not in results


def test_circle_direction_is_symmetric_and_opposite():
    _, cw_res, _ = _circle(cw=True, seed=3)
    _, ccw_res, _ = _circle(cw=False, seed=3)
    assert Gesture.CIRCLE_CW in cw_res and Gesture.CIRCLE_CW not in ccw_res
    assert Gesture.CIRCLE_CCW in ccw_res and Gesture.CIRCLE_CCW not in cw_res


def test_circle_negatives():
    # 180° arc — not enough angular travel
    _, res, _ = _circle(sweep=180.0, steps=12)
    assert all(g == Gesture.NONE for g in res)
    # straight line — no rotation
    det = TrajectoryDetector()
    line = [det.update((0.3 + 0.4 * i / 12.0, 0.5), 10.0 + i / 30.0)
            for i in range(13)]
    assert all(g == Gesture.NONE for g in line)
    # jitter blob with sub-min_radius displacement — noise, not a circle
    _, res, _ = _circle(r=0.008, jitter=0.002, seed=5)
    assert all(g == Gesture.NONE for g in res)
    # implausibly huge radius
    _, res, _ = _circle(r=0.6)
    assert all(g == Gesture.NONE for g in res)


def test_circle_cooldown_and_buffer_clear():
    det = TrajectoryDetector(cooldown=1.5)

    def feed_circle(t0):
        out = []
        for i in range(17):
            a = math.radians(360.0 * i / 16)
            out.append(det.update((0.5 + 0.12 * math.cos(a),
                                   0.5 + 0.12 * math.sin(a)),
                                  t0 + i / 30.0))
        return out

    assert Gesture.CIRCLE_CW in feed_circle(10.0)      # fires
    assert all(g == Gesture.NONE
               for g in feed_circle(10.6))             # inside cooldown
    assert Gesture.CIRCLE_CW in feed_circle(11.7)      # after cooldown


# ── 5. DepthDetector (push / pull) ────────────────────────────────────────

def _feed_depth(det, t0, frames, dt=1 / 30.0):
    results = []
    for i, lm in enumerate(frames):
        results.append(det.update(lm, t0 + i * dt))
    return results


def test_push_via_area_growth():
    det = DepthDetector()
    frames = [scale_hand(make_hand("five"), 1.0 + 0.06 * i) for i in range(6)]
    res = _feed_depth(det, 10.0, frames)
    assert Gesture.PUSH in res
    assert Gesture.PULL not in res
    assert 0.0 <= det.last_confidence <= 1.0


def test_pull_via_area_shrink():
    det = DepthDetector()
    frames = [scale_hand(make_hand("five"), 1.0 - 0.06 * i) for i in range(6)]
    res = _feed_depth(det, 10.0, frames)
    assert Gesture.PULL in res
    assert Gesture.PUSH not in res


def test_push_pull_via_z_velocity():
    # z decreasing = approaching camera → PUSH (z-only signal, flat area)
    det = DepthDetector()
    frames = [set_z(make_hand("five"), 0.5 - 0.04 * i) for i in range(6)]
    assert Gesture.PUSH in _feed_depth(det, 10.0, frames)
    # z increasing = retreating → PULL
    det = DepthDetector()
    frames = [set_z(make_hand("five"), 0.5 + 0.04 * i) for i in range(6)]
    assert Gesture.PULL in _feed_depth(det, 10.0, frames)


def test_depth_negatives():
    # static hand with tiny jitter → no event
    det = DepthDetector()
    rng = np.random.default_rng(11)
    frames = []
    for i in range(8):
        lm = scale_hand(make_hand("five"), 1.0 + rng.normal(0, 0.001))
        frames.append(set_z(lm, 0.5 + rng.normal(0, 0.002)))
    res = _feed_depth(det, 10.0, frames)
    assert all(g == Gesture.NONE for g in res)
    # slow approach below threshold → no event
    det = DepthDetector()
    frames = [scale_hand(make_hand("five"), 1.0 + 0.01 * i) for i in range(8)]
    res = _feed_depth(det, 10.0, frames)
    assert all(g == Gesture.NONE for g in res)


def test_depth_cooldown():
    det = DepthDetector()
    t0 = 10.0
    frames = [scale_hand(make_hand("five"), 1.0 + 0.06 * i) for i in range(6)]
    res = _feed_depth(det, t0, frames)
    assert res[-1] == Gesture.PUSH or Gesture.PUSH in res
    # continuing to grow within the cooldown window → suppressed
    more = [scale_hand(make_hand("five"), 1.4 + 0.06 * i) for i in range(6)]
    res = _feed_depth(det, t0 + 0.2, more)
    assert all(g == Gesture.NONE for g in res)


# ── 6. OscillationDetector (shake / wave) ────────────────────────────────

def _oscillate(det, amp=0.05, frames=14, open_palm=False, t0=10.0,
               dt=1 / 30.0, x0=0.5):
    results = []
    for i in range(frames):
        x = x0 + (amp if i % 2 == 0 else -amp)
        results.append(det.update((x, 0.5), t0 + i * dt, open_palm=open_palm))
    return results


def test_shake_positive_fist():
    det = OscillationDetector()
    res = _oscillate(det, open_palm=False)
    assert Gesture.SHAKE in res
    assert Gesture.WAVE not in res          # mutual exclusion
    assert 0.0 <= det.last_confidence <= 1.0


def test_wave_positive_open_palm():
    det = OscillationDetector()
    res = _oscillate(det, open_palm=True)
    assert Gesture.WAVE in res
    assert Gesture.SHAKE not in res          # mutual exclusion


def test_shake_vs_wave_disambiguation():
    # palm held for the whole window → WAVE
    det = OscillationDetector()
    res = _oscillate(det, open_palm=True, frames=14)
    assert Gesture.WAVE in res
    assert Gesture.SHAKE not in res
    # palm only during a short low-amplitude lead-in (3 of 6 samples in
    # the window at the first eligible evaluation = 50% < 60%) → the
    # oscillation counts as a SHAKE, not a WAVE
    det = OscillationDetector()
    results = []
    for i in range(8):
        if i < 3:                       # lead-in: palm, tiny drift
            x = 0.5 + 0.005 * i
            open_palm = True
        else:                           # strong oscillation, fist
            x = 0.51 + (0.05 if i % 2 else -0.05)
            open_palm = False
        results.append(det.update((x, 0.5), 10.0 + i / 30.0,
                                  open_palm=open_palm))
    assert results[:6] == [Gesture.NONE] * 6     # not eligible before i=6
    assert results[6] == Gesture.SHAKE           # 43% palm < 60% threshold
    assert Gesture.WAVE not in results


def test_oscillation_negatives():
    # tiny amplitude, many reversals → below amplitude threshold
    det = OscillationDetector(amplitude_threshold=0.03)
    res = _oscillate(det, amp=0.01, frames=14)
    assert all(g == Gesture.NONE for g in res)
    # one zig-zag = 2 reversals < 3 → below frequency threshold
    det = OscillationDetector()
    seq = [(0.5, 0.5), (0.6, 0.5), (0.4, 0.5), (0.5, 0.5), (0.5, 0.5),
           (0.5, 0.5)]
    res = [det.update(p, 10.0 + i / 30.0) for i, p in enumerate(seq)]
    assert all(g == Gesture.NONE for g in res)


def test_oscillation_cooldown():
    det = OscillationDetector()
    first = _oscillate(det, open_palm=False, t0=10.0)
    assert Gesture.SHAKE in first
    second = _oscillate(det, open_palm=False, t0=10.0 + 0.3)   # inside 1.2s
    assert all(g == Gesture.NONE for g in second)
    third = _oscillate(det, open_palm=False, t0=10.0 + 1.4)    # after cooldown
    assert Gesture.SHAKE in third


# ── 7. pinch events via GestureStateMachine ───────────────────────────────

def _feed_sm(sm, labels, t0=10.0, dt=1 / 30.0, hand_stable=True):
    outs = []
    for i, lab in enumerate(labels):
        outs.append(sm.update(lab, now=t0 + i * dt, hand_stable=hand_stable))
    return outs


def test_pinch_tap_click_regression():
    """A plain pinch tap must confirm PINCH exactly like v4 did and must
    NOT emit hold/release events."""
    sm = GestureStateMachine()
    outs = _feed_sm(sm, [Gesture.PINCH] * 6 + [Gesture.NONE])
    assert Gesture.PINCH in outs            # confirmed mid-tap (click works)
    assert outs[-1] == Gesture.NONE
    assert sm.poll_pinch_events() == []     # tap ≠ hold/release/double


def test_pinch_hold_fires_once_then_release():
    sm = GestureStateMachine()              # default hold = 15 frames
    labels = [Gesture.PINCH] * 20 + [Gesture.NONE]
    _feed_sm(sm, labels)
    events = sm.poll_pinch_events()
    assert events.count(Gesture.PINCH_HOLD) == 1
    assert Gesture.PINCH_RELEASE in events
    assert Gesture.DOUBLE_PINCH not in events
    # holding longer must not re-emit
    _feed_sm(sm, [Gesture.PINCH] * 10)
    assert sm.poll_pinch_events() == []


def test_pinch_hold_threshold_parameter():
    sm = GestureStateMachine(pinch_hold_frames=5)
    _feed_sm(sm, [Gesture.PINCH] * 8 + [Gesture.NONE])
    events = sm.poll_pinch_events()
    assert events[0] == Gesture.PINCH_HOLD
    assert Gesture.PINCH_RELEASE in events


def test_pinch_hold_vs_tap_disambiguation():
    # 14 confirmed pinch frames (< hold threshold) → a TAP: no hold/release
    sm = GestureStateMachine()
    _feed_sm(sm, [Gesture.PINCH] * 14 + [Gesture.NONE])
    assert sm.poll_pinch_events() == []
    # after a HOLD, the release must not register a tap → a following tap
    # cannot become a DOUBLE_PINCH
    sm = GestureStateMachine()
    _feed_sm(sm, [Gesture.PINCH] * 20 + [Gesture.NONE])          # hold
    hold_events = sm.poll_pinch_events()
    assert Gesture.PINCH_HOLD in hold_events
    _feed_sm(sm, [Gesture.PINCH] * 5 + [Gesture.NONE], t0=11.0)  # quick tap
    assert Gesture.DOUBLE_PINCH not in sm.poll_pinch_events()


def test_double_pinch_within_window():
    sm = GestureStateMachine()
    _feed_sm(sm, [Gesture.PINCH] * 5 + [Gesture.NONE], t0=10.0)   # tap 1
    assert sm.poll_pinch_events() == []
    _feed_sm(sm, [Gesture.PINCH] * 5 + [Gesture.NONE], t0=10.3)   # tap 2
    events = sm.poll_pinch_events()
    assert Gesture.DOUBLE_PINCH in events


def test_double_pinch_window_edges():
    # tap 1 release lands at t0 + 5/30 = 10.1667.
    # gap 0.59s → tap 2 release at 10.7567 → double fires
    sm = GestureStateMachine()
    _feed_sm(sm, [Gesture.PINCH] * 5 + [Gesture.NONE], t0=10.0)
    _feed_sm(sm, [Gesture.PINCH] * 5 + [Gesture.NONE], t0=10.590)
    assert Gesture.DOUBLE_PINCH in sm.poll_pinch_events()
    # gap 0.61s → tap 2 release at 10.7767 → outside the 0.6s window
    sm = GestureStateMachine()
    _feed_sm(sm, [Gesture.PINCH] * 5 + [Gesture.NONE], t0=10.0)
    _feed_sm(sm, [Gesture.PINCH] * 5 + [Gesture.NONE], t0=10.610)
    assert Gesture.DOUBLE_PINCH not in sm.poll_pinch_events()


def test_pinch_noise_single_frame_never_taps():
    """Raw 1-frame pinch noise must not confirm (action gating) and hence
    must not register taps — the confirm machinery protects the events."""
    sm = GestureStateMachine()
    labels = [Gesture.PINCH, Gesture.NONE] * 6
    outs = _feed_sm(sm, labels)
    assert sm.poll_pinch_events() == []
    assert all(g != Gesture.PINCH for g in outs)


def test_state_machine_new_gesture_classification():
    # FIVE/FOUR behave as movement gestures, THUMBS_DOWN as an action
    sm = GestureStateMachine()
    assert Gesture.FIVE in GestureStateMachine.MOVEMENT_GESTURES
    assert Gesture.FOUR in GestureStateMachine.MOVEMENT_GESTURES
    assert Gesture.THUMBS_DOWN in GestureStateMachine.ACTION_GESTURES
    outs = _feed_sm(sm, [Gesture.FIVE] * 5)
    assert outs[-1] == Gesture.FIVE
    sm.reset()
    # feed at a LATER t0: reset() deliberately keeps the monotonic
    # transition-cooldown timestamp (v4 behavior), so confirmation right
    # after a reset uses fresh wall-clock times.
    outs = _feed_sm(sm, [Gesture.THUMBS_DOWN] * 6, t0=12.0)
    assert outs[-1] == Gesture.THUMBS_DOWN


# ── 8. registry backward compatibility ────────────────────────────────────

def test_registry_maps_new_palm_family_labels():
    reg = GestureRegistry()
    intent = reg.map_gesture(Gestures.FIVE, confidence=0.9)
    assert intent is not None and intent.type.value == "drag"
    intent = reg.map_gesture(Gestures.FOUR, confidence=0.9)
    assert intent is not None and intent.type.value == "drag"
    intent = reg.map_gesture(Gestures.SHAKE, confidence=0.9)
    assert intent is not None and intent.type.value == "cancel"


def test_registry_accepts_new_labels_without_crash():
    reg = GestureRegistry()
    for label in ("thumbs_down", "push", "pull", "wave", "swipe_up",
                  "swipe_down", "circle_cw", "circle_ccw"):
        event, intent = reg.feed(label, point=(0.5, 0.5), confidence=0.9,
                                 now=1.0)
        assert event.payload["gesture"] == label


def test_registry_double_pinch_synthesis_unchanged():
    reg = GestureRegistry()
    _, i1 = reg.feed(Gestures.PINCH, confidence=0.9, now=1.0)
    _, i2 = reg.feed(Gestures.PINCH, confidence=0.9, now=1.2)
    assert i1 is not None and i1.type.value == "click"
    assert i2 is not None and i2.type.value == "double_click"
    # outside the window: plain click again
    _, i3 = reg.feed(Gestures.PINCH, confidence=0.9, now=2.5)
    assert i3 is not None and i3.type.value == "click"


# ── 9. performance smoke ──────────────────────────────────────────────────

def test_recognize_performance_smoke():
    """< 1 ms per recognize call on a batch of 200 (plus detectors)."""
    hands = [make_hand(p, jitter=0.001, seed=i)
             for i, p in enumerate(["five", "four", "thumbs_down", "pinch",
                                    "fist", "point"])]
    swipe = SwipeDetector(speed_threshold=0.03)
    circle = TrajectoryDetector()
    depth = DepthDetector()
    osc = OscillationDetector()
    sm = GestureStateMachine()
    n = 200
    t_start = time.perf_counter()
    for i in range(n):
        lm = hands[i % len(hands)]
        reset_finger_state()
        res = recognize_gesture(lm)
        assert res["gesture"] != ""
        pos = res["index_pos"]
        t = 10.0 + i / 30.0
        swipe.update(pos, pos - 0.01, t)
        circle.update(pos, t)
        depth.update(lm, t)
        osc.update(pos, t, open_palm=res["fingers_up"] >= 4)
        sm.update(res["gesture"], now=t, hand_stable=True)
        sm.poll_pinch_events()
    elapsed_per_call = (time.perf_counter() - t_start) / n
    assert elapsed_per_call < 0.001, elapsed_per_call
