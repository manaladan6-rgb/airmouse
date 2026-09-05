"""Task 2-c — TEMPORAL GESTURE INTELLIGENCE tests (v16.5, mission §10–§14).

Covers airmouse/temporal.py:
  1. Sample + TrajectoryBuffer feature math (velocity/acceleration/direction
     on hand-built trajectories — exact numbers asserted).
  2. PinchLifecycle full transition graph: start→hold→release, start→move→
     release (drag), vertical scroll direction + signed amounts, double
     pinch, grace-period resume, exit hysteresis, debounce, confidence
     gating, tracking-loss release.
  3. TemporalRecognizer: HOLD / MOTION / SEQUENCE detections, composition
     mapping (select_target / drag / scroll / activate_gaze_target), honest
     "NOT MAPPED" for unmapped motion and for two-hand ROTATE/DRAG/HOLD,
     real zoom_ticks semantics for two-hand ZOOM (incl. a live
     TwoHandGestureRecognizer integration case).
  4. CompositionResolver: voice+gaze merge, gaze+pinch, voice+gesture
     dedupe (exactly one proposal), documented priority on conflict,
     estop pass-through guard, proposal-only output contract.
  5. Robustness toolkit contracts: hysteresis band, debounce interval,
     suppression frames/cooldown, recovery state machine, watchdog
     stale/lost, EWMA health score monotonic degradation.
  6. NO-PARALLEL-DISPATCH guard: temporal.py source must contain no input
     automation imports/calls (regex on source, per mission architecture
     rule) and must carry the prediction-vs-execution docstring contract.

All tests are deterministic: time is injected everywhere (no sleeps),
inputs are synthetic Sample factories.
"""

import math
import re
from pathlib import Path

import pytest

from airmouse.temporal import (
    PINCH_EVENTS,
    PINCH_LABEL,
    POINTING_LABEL,
    CameraWatchdog,
    Composition,
    CompositionResolver,
    Debouncer,
    FalsePositiveSuppressor,
    HoldDetection,
    Hysteresis,
    MotionDetection,
    PinchLifecycle,
    Sample,
    SensorHealthScore,
    SequenceDetection,
    TemporalRecognizer,
    TrajectoryBuffer,
    TrackingRecovery,
)

DT = 1.0 / 30.0  # one frame at 30 fps
T0 = 100.0

MODULE_PATH = (Path(__file__).resolve().parents[1]
               / "airmouse_pkg" / "airmouse" / "temporal.py")

EVENT_KEYS = {"event", "confidence", "duration_s", "displacement",
              "dominant_direction"}


# ═══════════════════════════════════════════════════════════════════════════
# Synthetic sample factories
# ═══════════════════════════════════════════════════════════════════════════

def _sample(t, label, conf=0.9, pos=None, handed="Right"):
    """One Sample; ``pos`` becomes a 1-landmark tuple (centroid == pos)."""
    lms = ((pos[0], pos[1]),) if pos is not None else None
    return Sample(t=t, landmarks=lms, label=label, confidence=conf,
                  handedness=handed)


def _line(n, start, step, label=POINTING_LABEL, conf=0.9, t0=T0, dt=DT,
          handed="Right"):
    """n samples marching in a straight line: pos_k = start + k*step."""
    return [_sample(t0 + k * dt, label, conf,
                    (start[0] + k * step[0], start[1] + k * step[1]),
                    handed)
            for k in range(n)]


def _static(n, pos, label=PINCH_LABEL, conf=0.9, t0=T0, dt=DT, handed="Right"):
    return _line(n, pos, (0.0, 0.0), label, conf, t0, dt, handed)


def _hand(cx, cy, pinch=0.02, spread=0.05):
    """21 (x, y, z) tuples; thumb tip (4) / index tip (8) exactly `pinch` apart."""
    pts = []
    itip = (cx + spread * 0.5, cy - spread * 0.5)
    ttip = (itip[0] - pinch, itip[1])
    pts.append((cx, cy + spread, 0.0))                                  # wrist
    for k in (1, 2, 3):                                                # thumb
        pts.append((cx + spread * 0.1 * k,
                    cy + spread * 0.3 - spread * 0.25 * k, 0.0))
    pts.append((ttip[0], ttip[1], 0.0))                                # 4
    pts.extend([(cx - spread * 0.2, cy - spread * 0.1, 0.0),           # 5-7
                (cx, cy - spread * 0.3, 0.0),
                (cx + spread * 0.25, cy - spread * 0.4, 0.0)])
    pts.append((itip[0], itip[1], 0.0))                                # 8
    for dx in (-0.1, 0.15, 0.4):                                       # 9-20
        for k in range(4):
            pts.append((cx + dx * spread, cy - spread * (0.5 + 0.18 * k), 0.0))
    assert len(pts) == 21
    return pts


def _hand_dict(cx, cy, label="Right", pinch=0.02):
    pts = _hand(cx, cy, pinch)
    return {"landmarks": pts, "index_pos": (pts[8][0], pts[8][1]),
            "pinch_distance": pinch, "handedness": label,
            "handedness_score": 0.95, "is_left_user": label == "Left"}


# ═══════════════════════════════════════════════════════════════════════════
# 1) Sample + TrajectoryBuffer features math
# ═══════════════════════════════════════════════════════════════════════════

class TestTrajectoryBufferFeatures:
    def test_empty_buffer_safe_defaults(self):
        f = TrajectoryBuffer().features()
        assert f["duration"] == 0.0 and f["path_length"] == 0.0
        assert f["displacement"] == 0.0 and f["mean_velocity"] == 0.0
        assert f["max_velocity"] == 0.0 and f["mean_acceleration"] == 0.0
        assert f["dominant_direction"] == "none"
        assert f["start_label"] == "" and f["end_label"] == ""
        assert f["label_changes"] == [] and f["handedness_stability"] == 0.0

    def test_features_single_sample(self):
        buf = TrajectoryBuffer()
        buf.append(_sample(T0, PINCH_LABEL, 0.8, (0.5, 0.5)))
        f = buf.features()
        assert f["duration"] == 0.0 and f["path_length"] == 0.0
        assert f["start_label"] == PINCH_LABEL
        assert f["end_label"] == PINCH_LABEL
        assert f["dominant_direction"] == "none"

    def test_straight_line_numbers(self):
        buf = TrajectoryBuffer()
        for s in _line(4, (0.0, 0.0), (0.01, 0.0)):   # 3 steps of 0.01 right
            buf.append(s)
        f = buf.features()
        assert f["duration"] == pytest.approx(3 * DT)
        assert f["path_length"] == pytest.approx(0.03)
        assert f["displacement"] == pytest.approx(0.03)
        assert f["mean_velocity"] == pytest.approx(0.3)     # 0.03 / 0.1 s
        assert f["max_velocity"] == pytest.approx(0.3)      # 0.01 / DT
        assert f["mean_acceleration"] == pytest.approx(0.0)  # constant speed
        assert f["dominant_direction"] == "right"
        assert f["handedness_stability"] == pytest.approx(1.0)

    def test_acceleration_from_speeding_up(self):
        buf = TrajectoryBuffer()
        # speeds: 0.01 then 0.02 per frame → v: 0.3, 0.6 → |Δv|/dt = 9.0
        pts = [(0.0, 0.0), (0.01, 0.0), (0.03, 0.0)]
        for k, p in enumerate(pts):
            buf.append(_sample(T0 + k * DT, POINTING_LABEL, 0.9, p))
        f = buf.features()
        assert f["path_length"] == pytest.approx(0.03)
        assert f["max_velocity"] == pytest.approx(0.6)
        assert f["mean_acceleration"] == pytest.approx(9.0)

    def test_dominant_direction_all_axes_and_deadzone(self):
        def direction_for(step):
            buf = TrajectoryBuffer(direction_deadzone=0.02)
            for s in _line(4, (0.5, 0.5), step):
                buf.append(s)
            return buf.features()["dominant_direction"]

        assert direction_for((0.03, 0.0)) == "right"
        assert direction_for((-0.03, 0.0)) == "left"
        assert direction_for((0.0, -0.03)) == "up"      # image y grows DOWN
        assert direction_for((0.0, 0.03)) == "down"
        assert direction_for((0.01, 0.0)) == "none"     # inside deadzone
        assert direction_for((0.0, 0.0)) == "none"

    def test_diagonal_prefers_dominant_axis(self):
        buf = TrajectoryBuffer(direction_deadzone=0.02)
        for s in _line(4, (0.5, 0.5), (0.03, 0.01)):   # dx > dy
            buf.append(s)
        assert buf.features()["dominant_direction"] == "right"

    def test_window_limits_features(self):
        buf = TrajectoryBuffer()
        for k in range(61):                              # 2 s of samples
            buf.append(_sample(T0 + k * DT, POINTING_LABEL, 0.9,
                               (0.5 + 0.001 * k, 0.5)))
        f = buf.features(window_s=0.5)
        assert f["n_samples"] == 16                      # t >= T0+1.5 s
        assert f["duration"] == pytest.approx(0.5)

    def test_label_changes_and_boundaries(self):
        buf = TrajectoryBuffer()
        for s in _static(3, (0.5, 0.5), POINTING_LABEL, t0=T0):
            buf.append(s)
        for s in _static(3, (0.5, 0.5), PINCH_LABEL, t0=T0 + 3 * DT):
            buf.append(s)
        f = buf.features()
        assert f["start_label"] == POINTING_LABEL
        assert f["end_label"] == PINCH_LABEL
        assert f["label_changes"] == [(T0 + 3 * DT, POINTING_LABEL, PINCH_LABEL)]

    def test_handedness_stability(self):
        buf = TrajectoryBuffer()
        labels = ["Right", "Right", "Right", "Right", "Left", "Left"]
        for k, h in enumerate(labels):
            buf.append(_sample(T0 + k * DT, PINCH_LABEL, 0.9, (0.5, 0.5), h))
        assert buf.features()["handedness_stability"] == pytest.approx(4.0 / 6.0)

    def test_handedness_unknown_scores_zero(self):
        buf = TrajectoryBuffer()
        for k in range(4):
            buf.append(_sample(T0 + k * DT, PINCH_LABEL, 0.9, (0.5, 0.5), ""))
        assert buf.features()["handedness_stability"] == 0.0

    def test_landmarks_absent_is_safe(self):
        buf = TrajectoryBuffer()
        for k in range(3):
            buf.append(_sample(T0 + k * DT, "fist", 0.9, None))
        f = buf.features()
        assert f["duration"] == pytest.approx(2 * DT)
        assert f["path_length"] == 0.0 and f["displacement"] == 0.0
        assert f["dominant_direction"] == "none"

    def test_zero_dt_guards_finite_output(self):
        buf = TrajectoryBuffer()
        buf.append(_sample(T0, POINTING_LABEL, 0.9, (0.5, 0.5)))
        buf.append(_sample(T0, POINTING_LABEL, 0.9, (0.6, 0.5)))   # dt == 0
        buf.append(_sample(T0 + DT, POINTING_LABEL, 0.9, (0.6, 0.5)))
        f = buf.features()
        for key in ("duration", "path_length", "displacement",
                    "mean_velocity", "max_velocity", "mean_acceleration"):
            assert math.isfinite(f[key])
        assert f["max_velocity"] == 0.0   # the zero-dt step is skipped
        assert f["mean_acceleration"] == 0.0

    def test_maxlen_respected_oldest_dropped(self):
        buf = TrajectoryBuffer(maxlen=240)
        for k in range(300):
            buf.append(_sample(T0 + k * DT, POINTING_LABEL, 0.9, (0.5, 0.5)))
        assert len(buf) == 240
        assert buf.samples()[0].t == pytest.approx(T0 + 60 * DT)
        assert buf.last().t == pytest.approx(T0 + 299 * DT)
        assert buf.features()["n_samples"] == 240

    def test_clear(self):
        buf = TrajectoryBuffer()
        buf.append(_sample(T0, PINCH_LABEL))
        buf.clear()
        assert len(buf) == 0 and buf.last() is None
        assert buf.features()["n_samples"] == 0

    def test_sample_defaults_and_position(self):
        s = Sample(t=1.0)
        assert s.landmarks is None and s.label == "none"
        assert s.confidence == 0.0 and s.handedness == ""
        assert s.position is None
        p = Sample(t=1.0, landmarks=((0.0, 0.0), (1.0, 3.0)))
        assert p.position == pytest.approx((0.5, 1.5))
        with pytest.raises(Exception):
            s.t = 2.0        # frozen dataclass


# ═══════════════════════════════════════════════════════════════════════════
# 2) PinchLifecycle — full transition graph
# ═══════════════════════════════════════════════════════════════════════════

def _lifecycle(**kw):
    defaults = dict(enter_frames=2, exit_frames=3, hold_s=0.5,
                    move_deadzone=0.02, tap_max_s=0.35,
                    tap_max_displacement=0.02, double_pinch_window=0.6,
                    min_event_interval_s=0.05, grace_s=0.35)
    defaults.update(kw)
    return PinchLifecycle(**defaults)


class TestPinchLifecycle:
    def test_no_event_before_enter_frames(self):
        lc = _lifecycle()
        assert lc.update(_sample(T0, PINCH_LABEL, 0.9, (0.5, 0.5))) is None
        assert lc.engaged is False

    def test_low_confidence_never_enters(self):
        lc = _lifecycle()
        for k in range(10):
            ev = lc.update(_sample(T0 + k * DT, PINCH_LABEL, 0.5, (0.5, 0.5)))
            assert ev is None
        assert lc.engaged is False

    def test_enter_streak_must_be_consecutive(self):
        lc = _lifecycle()
        t = T0
        assert lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))) is None
        t += DT
        assert lc.update(_sample(t, POINTING_LABEL, 0.9, (0.5, 0.5))) is None
        t += DT
        assert lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))) is None
        t += DT
        ev = lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5)))
        assert ev is not None and ev["event"] == "pinch_start"

    def test_start_hold_release_graph(self):
        lc = _lifecycle()
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        start = lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5)))
        assert start["event"] == "pinch_start"
        start_t = t
        events = []
        for k in range(1, 20):
            t = start_t + k * DT
            ev = lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5)))
            if ev:
                events.append(ev)
        holds = [e for e in events if e["event"] == "pinch_hold"]
        assert len(holds) == 1                       # exactly once per hold
        assert holds[0]["duration_s"] >= 0.5
        assert holds[0]["dominant_direction"] == "none"
        # more frames → no duplicate hold
        assert lc.update(_sample(t + DT, PINCH_LABEL, 0.9, (0.5, 0.5))) is None
        # release: 3 consecutive non-pinch frames
        rel = None
        for k in range(3):
            rel = lc.update(_sample(t + (k + 2) * DT, POINTING_LABEL, 0.9,
                                    (0.5, 0.5)))
        assert rel is not None and rel["event"] == "pinch_release"
        assert rel["proposal"] is None               # long, unmoved: no click
        assert rel["duration_s"] == pytest.approx(t + 4 * DT - start_t)
        assert lc.engaged is False

    def test_event_dict_contract(self):
        lc = _lifecycle()
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        ev = lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5)))
        assert EVENT_KEYS <= set(ev)
        assert ev["event"] in PINCH_EVENTS
        assert 0.0 <= ev["confidence"] <= 1.0

    def test_quick_tap_is_left_click(self):
        lc = _lifecycle()
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        rel = None
        for k in range(3):
            rel = lc.update(_sample(t + k * DT, POINTING_LABEL, 0.9,
                                    (0.5, 0.5)))
        assert rel["event"] == "pinch_release"
        assert rel["proposal"] == "left_click"
        assert rel["duration_s"] < 0.35

    def test_double_pinch_two_quick_taps(self):
        lc = _lifecycle()
        t = T0
        # tap 1
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        for k in range(3):
            ev = lc.update(_sample(t + k * DT, POINTING_LABEL, 0.9, (0.5, 0.5)))
        assert ev["proposal"] == "left_click"
        t += 3 * DT
        # tap 2 within 0.6 s
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        ev = None
        for k in range(3):
            ev = lc.update(_sample(t + k * DT, POINTING_LABEL, 0.9, (0.5, 0.5)))
        assert ev["event"] == "double_pinch"
        assert ev["proposal"] == "double_click"

    def test_no_double_pinch_when_taps_far_apart(self):
        lc = _lifecycle()
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        for k in range(3):
            lc.update(_sample(t + k * DT, POINTING_LABEL, 0.9, (0.5, 0.5)))
        t += 3 * DT + 1.0                            # outside 0.6 s window
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        ev = None
        for k in range(3):
            ev = lc.update(_sample(t + k * DT, POINTING_LABEL, 0.9, (0.5, 0.5)))
        assert ev["event"] == "pinch_release"
        assert ev["proposal"] == "left_click"        # plain tap, not double

    def test_horizontal_move_is_drag(self):
        lc = _lifecycle()
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        ev = None
        for k in range(1, 5):                        # march right 0.01/frame
            ev = lc.update(_sample(t + k * DT, PINCH_LABEL, 0.9,
                                   (0.5 + 0.01 * k, 0.5)))
        assert ev["event"] == "pinch_move"
        assert ev["proposal"] == "drag"
        assert ev["dominant_direction"] == "right"
        assert ev["displacement"] > 0.02
        # release after movement closes the drag
        rel = None
        for k in range(3):
            rel = lc.update(_sample(t + 5 * DT + k * DT, POINTING_LABEL,
                                    0.9, (0.54, 0.5)))
        assert rel["event"] == "pinch_release"
        assert rel["proposal"] == "stop_drag"
        assert rel["dominant_direction"] == "right"

    def test_vertical_move_is_scroll_up_positive(self):
        lc = _lifecycle(scroll_tick=0.125)
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        ev = None
        for k in range(1, 4):                        # march UP 0.125/frame
            ev = lc.update(_sample(t + k * DT, PINCH_LABEL, 0.9,
                                   (0.5, 0.5 - 0.125 * k)))
        assert ev["event"] == "pinch_move"
        assert ev["proposal"] == "scroll"
        assert ev["dominant_direction"] == "up"
        assert ev["amount"] >= 1                     # up = positive ticks
        # release after a scroll episode proposes nothing extra
        rel = None
        for k in range(3):
            rel = lc.update(_sample(t + 4 * DT + k * DT, POINTING_LABEL,
                                    0.9, (0.5, 0.125)))
        assert rel["event"] == "pinch_release"
        assert rel["proposal"] is None

    def test_vertical_move_down_is_negative_scroll(self):
        lc = _lifecycle(scroll_tick=0.125)
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        ev = None
        for k in range(1, 4):                        # march DOWN 0.125/frame
            ev = lc.update(_sample(t + k * DT, PINCH_LABEL, 0.9,
                                   (0.5, 0.5 + 0.125 * k)))
        assert ev["proposal"] == "scroll"
        assert ev["dominant_direction"] == "down"
        assert ev["amount"] <= -1

    def test_move_events_stream_throttled(self):
        lc = _lifecycle()
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        emits = []
        for k in range(1, 8):                        # 0.01/frame to the right
            ev = lc.update(_sample(t + k * DT, PINCH_LABEL, 0.9,
                                   (0.5 + 0.01 * k, 0.5)))
            if ev and ev["event"] == "pinch_move":
                emits.append(ev)
        assert len(emits) >= 2                       # streaming works
        gaps = [b["duration_s"] - a["duration_s"] for a, b in
                zip(emits, emits[1:])]
        assert all(g >= DT for g in gaps)            # never same-frame spam

    def test_debounce_suppresses_rapid_reemits(self):
        lc = _lifecycle(min_event_interval_s=0.05)
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        first = lc.update(_sample(t + DT, PINCH_LABEL, 0.9, (0.55, 0.5)))
        assert first is not None and first["event"] == "pinch_move"
        # another crossing one frame later → inside debounce window
        second = lc.update(_sample(t + 2 * DT, PINCH_LABEL, 0.9, (0.58, 0.5)))
        assert second is None                        # SUPPRESSED
        # past the interval → passes again
        third = lc.update(_sample(t + 4 * DT, PINCH_LABEL, 0.9, (0.60, 0.5)))
        assert third is not None and third["event"] == "pinch_move"

    def test_release_is_never_debounced(self):
        lc = _lifecycle(min_event_interval_s=0.05)
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.55, 0.5)))   # move emit
        rel = lc.update(_sample(t + DT, POINTING_LABEL, 0.9, (0.55, 0.5)))
        # only 1 exit frame so far → no release yet
        assert rel is None
        rel = None
        for k in range(2):
            rel = lc.update(_sample(t + (k + 2) * DT, POINTING_LABEL, 0.9,
                                    (0.55, 0.5)))
        assert rel is not None and rel["event"] == "pinch_release"

    def test_exit_hysteresis_prevents_flicker(self):
        lc = _lifecycle()
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        # confidence in the enter/exit band (0.45..0.65): state KEPT
        for k in range(5):
            ev = lc.update(_sample(t + k * DT, PINCH_LABEL, 0.55, (0.5, 0.5)))
            assert ev is None                        # short: no hold yet either
        assert lc.engaged is True
        # strong confidence again → still the same episode
        t += 5 * DT
        ev = lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5)))
        assert ev is None or ev["event"] == "pinch_hold"
        # fewer than exit_frames weak frames → no release
        for k in range(2):
            assert lc.update(_sample(t + (k + 1) * DT, PINCH_LABEL, 0.2,
                                     (0.5, 0.5))) is None
        assert lc.engaged is True
        # third weak frame → release
        rel = lc.update(_sample(t + 3 * DT, PINCH_LABEL, 0.2, (0.5, 0.5)))
        assert rel is not None and rel["event"] == "pinch_release"

    def test_other_pose_exits_after_exit_frames(self):
        lc = _lifecycle()
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        ev = None
        for k in range(2):                           # 2 frames of palm
            ev = lc.update(_sample(t + k * DT, "palm", 0.9, (0.5, 0.5)))
        assert ev is None and lc.engaged is True
        rel = lc.update(_sample(t + 2 * DT, "palm", 0.9, (0.5, 0.5)))
        assert rel["event"] == "pinch_release"

    def test_grace_period_resume(self):
        lc = _lifecycle(grace_s=0.35)
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        start = lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5)))
        start_t = t
        t += DT
        # one dropped frame (None sample) inside grace → silence, state kept
        assert lc.update(None, now=t) is None
        assert lc.engaged is True
        # a "none"-label frame inside grace → also silence
        t += DT
        assert lc.update(_sample(t, "none", 0.0, None)) is None
        assert lc.engaged is True
        # resume: same episode continues (no new pinch_start), and a later
        # release reports the FULL episode duration from the original start
        t += DT
        assert lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))) is None
        rel = None
        for k in range(3):
            rel = lc.update(_sample(t + (k + 1) * DT, POINTING_LABEL, 0.9,
                                    (0.5, 0.5)))
        assert rel["event"] == "pinch_release"
        assert rel["duration_s"] == pytest.approx(t + 3 * DT - start_t, rel=1e-6)
        assert rel["reason"] == "normal"

    def test_grace_expiry_releases_and_resets(self):
        lc = _lifecycle(grace_s=0.2)
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.55, 0.5)))   # moved → drag
        # tracking lost for longer than grace
        ev = None
        for k in range(10):                          # 10 frames ≈ 0.33 s
            e = lc.update(_sample(t + (k + 1) * DT, "none", 0.0, None))
            if e is not None:
                ev = e                               # capture the release
        assert ev is not None and ev["event"] == "pinch_release"
        assert ev["reason"] == "tracking_lost"
        assert lc.engaged is False
        # fresh episode starts cleanly afterwards
        t += 12 * DT
        assert lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))) is None
        ev = lc.update(_sample(t + DT, PINCH_LABEL, 0.9, (0.5, 0.5)))
        assert ev is not None and ev["event"] == "pinch_start"

    def test_missing_sample_without_history_is_safe(self):
        lc = _lifecycle()
        assert lc.update(None, now=T0) is None
        assert lc.update(None) is None               # totally degenerate call
        assert lc.engaged is False

    def test_reset(self):
        lc = _lifecycle()
        t = T0
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5))); t += DT
        lc.update(_sample(t, PINCH_LABEL, 0.9, (0.5, 0.5)))
        lc.reset()
        assert lc.engaged is False
        assert lc.update(_sample(t + DT, PINCH_LABEL, 0.9, (0.5, 0.5))) is None


# ═══════════════════════════════════════════════════════════════════════════
# 3) TemporalRecognizer — detections, sequences, compositions
# ═══════════════════════════════════════════════════════════════════════════

def _buffer_of(samples, maxlen=240):
    buf = TrajectoryBuffer(maxlen=maxlen)
    for s in samples:
        buf.append(s)
    return buf


class TestTemporalRecognizerDetections:
    def test_hold_detection(self):
        rec = TemporalRecognizer()
        buf = _buffer_of(_static(30, (0.5, 0.5), PINCH_LABEL))   # ~0.97 s
        holds = [d for d in rec.recognize(buf) if isinstance(d, HoldDetection)]
        assert len(holds) == 1
        assert holds[0].label == PINCH_LABEL
        assert holds[0].duration_s == pytest.approx(29 * DT)
        assert holds[0].mean_confidence == pytest.approx(0.9)

    def test_no_hold_below_minimum(self):
        rec = TemporalRecognizer()
        buf = _buffer_of(_static(6, (0.5, 0.5), PINCH_LABEL))    # ~0.17 s
        assert not any(isinstance(d, HoldDetection)
                       for d in rec.recognize(buf))

    def test_motion_detection_numbers(self):
        rec = TemporalRecognizer()
        buf = _buffer_of(_line(10, (0.3, 0.5), (0.01, 0.0)))     # 0.09 right
        motions = [d for d in rec.recognize(buf)
                   if isinstance(d, MotionDetection)]
        assert len(motions) == 1
        m = motions[0]
        assert m.direction == "right"
        assert m.path_length == pytest.approx(0.09)
        assert m.mean_velocity == pytest.approx(0.09 / (9 * DT))
        assert m.duration_s == pytest.approx(9 * DT)
        assert m.os_action == "NOT MAPPED"           # honest until composed

    def test_jitter_is_neither_motion_nor_hold(self):
        rec = TemporalRecognizer()
        buf = _buffer_of(_line(10, (0.5, 0.5), (0.002, 0.0)))    # sub-still
        out = rec.recognize(buf)
        assert not any(isinstance(d, (MotionDetection, HoldDetection))
                       for d in out)

    def test_sequence_pointing_to_pinch(self):
        rec = TemporalRecognizer()
        samples = _static(10, (0.5, 0.5), POINTING_LABEL) \
            + _static(10, (0.5, 0.5), PINCH_LABEL, t0=T0 + 10 * DT)
        seqs = [d for d in rec.recognize(_buffer_of(samples))
                if isinstance(d, SequenceDetection)]
        assert len(seqs) == 1
        assert seqs[0].labels == (POINTING_LABEL, PINCH_LABEL)
        assert seqs[0].gaps == pytest.approx((DT,))

    def test_no_sequence_when_gap_too_large(self):
        rec = TemporalRecognizer()
        samples = _static(10, (0.5, 0.5), POINTING_LABEL) \
            + _static(10, (0.5, 0.5), PINCH_LABEL, t0=T0 + 2.0)
        seqs = [d for d in rec.recognize(_buffer_of(samples))
                if isinstance(d, SequenceDetection)]
        assert seqs == []

    def test_recognize_empty_buffer(self):
        assert TemporalRecognizer().recognize(TrajectoryBuffer()) == []

    def test_recognize_never_mutates_buffer(self):
        buf = _buffer_of(_static(10, (0.5, 0.5), PINCH_LABEL))
        n = len(buf)
        TemporalRecognizer().recognize(buf)
        assert len(buf) == n


class TestTemporalRecognizerCompositions:
    def test_composition_select_target(self):
        rec = TemporalRecognizer()
        samples = _static(10, (0.5, 0.5), POINTING_LABEL) \
            + _static(6, (0.5, 0.5), PINCH_LABEL, t0=T0 + 10 * DT)
        comps = [c for c in rec.recognize(_buffer_of(samples))
                 if isinstance(c, Composition)]
        assert [c.name for c in comps] == ["select_target"]
        assert comps[0].os_action == "left_click"
        assert comps[0].details["spine"] == ["left_click"]
        assert comps[0].details["sequence"] == (POINTING_LABEL, PINCH_LABEL)

    def test_composition_drag(self):
        rec = TemporalRecognizer()
        samples = _static(6, (0.5, 0.5), POINTING_LABEL) \
            + _line(9, (0.5, 0.5), (0.01, 0.0), PINCH_LABEL, t0=T0 + 6 * DT)
        comps = [c for c in rec.recognize(_buffer_of(samples))
                 if isinstance(c, Composition)]
        assert [c.name for c in comps] == ["drag"]
        assert comps[0].os_action == "start_drag/stop_drag"
        assert comps[0].details["spine"] == ["start_drag", "stop_drag"]
        assert comps[0].details["direction"] == "right"
        assert comps[0].details["displacement"] == pytest.approx(0.08)

    def test_composition_scroll_up_amount(self):
        rec = TemporalRecognizer(scroll_tick=0.125)
        # exact binary steps: 4 × 0.0625 = 0.25 up → +2 ticks
        buf = _buffer_of(_line(5, (0.5, 0.5), (0.0, -0.0625), PINCH_LABEL))
        comps = [c for c in rec.recognize(buf) if isinstance(c, Composition)]
        assert [c.name for c in comps] == ["scroll"]
        assert comps[0].os_action == "scroll"
        assert comps[0].details["direction"] == "up"
        assert comps[0].details["amount"] == 2

    def test_composition_scroll_down_negative(self):
        rec = TemporalRecognizer(scroll_tick=0.125)
        buf = _buffer_of(_line(5, (0.5, 0.5), (0.0, 0.0625), PINCH_LABEL))
        comps = [c for c in rec.recognize(buf) if isinstance(c, Composition)]
        assert comps[0].name == "scroll"
        assert comps[0].details["direction"] == "down"
        assert comps[0].details["amount"] == -2

    def test_composition_activate_gaze_target(self):
        rec = TemporalRecognizer()
        buf = _buffer_of(_static(6, (0.5, 0.5), PINCH_LABEL))
        comps = [c for c in rec.recognize(buf, gaze_target="btn-ok")
                 if isinstance(c, Composition)]
        assert [c.name for c in comps] == ["activate_gaze_target"]
        assert comps[0].os_action == "left_click"
        assert comps[0].details["target"] == "btn-ok"

    def test_gaze_subsumes_select_target_single_proposal(self):
        rec = TemporalRecognizer()
        samples = _static(10, (0.5, 0.5), POINTING_LABEL) \
            + _static(6, (0.5, 0.5), PINCH_LABEL, t0=T0 + 10 * DT)
        comps = [c for c in rec.recognize(_buffer_of(samples),
                                          gaze_target="btn")
                 if isinstance(c, Composition)]
        assert [c.name for c in comps] == ["activate_gaze_target"]

    def test_no_composition_from_gaze_alone(self):
        rec = TemporalRecognizer()
        buf = _buffer_of(_static(10, (0.5, 0.5), POINTING_LABEL))
        comps = [c for c in rec.recognize(buf, gaze_target="btn")
                 if isinstance(c, Composition)]
        assert comps == []

    def test_unmapped_pinch_hold_is_honest(self):
        rec = TemporalRecognizer()
        buf = _buffer_of(_static(30, (0.5, 0.5), PINCH_LABEL))
        out = rec.recognize(buf)
        assert any(isinstance(d, HoldDetection) and d.os_action == "NOT MAPPED"
                   for d in out)
        assert not any(isinstance(d, Composition) for d in out)


class TestTwoHandMapping:
    REPORT = {"active": True, "scale": 1.0, "angle_delta_deg": 0.0,
              "centroid_delta": (0.0, 0.0), "confidence": 0.9,
              "handedness": ("Left", "Right")}

    def _report(self, gesture, **kw):
        r = dict(self.REPORT)
        r["gesture"] = gesture
        r.update(kw)
        return r

    def test_zoom_is_real(self):
        out = TemporalRecognizer().recognize_two_hand(
            self._report("TWO_HAND_ZOOM", scale=1.15))
        assert out["detected"] == "ZOOM"
        assert out["os_action"] == "zoom_ticks"
        assert out["amount"] == 3                    # 0.15 / 0.05 per tick
        assert out["confidence"] == pytest.approx(0.9)

    def test_zoom_out_negative_amount(self):
        out = TemporalRecognizer().recognize_two_hand(
            self._report("TWO_HAND_ZOOM", scale=0.9))
        assert out["detected"] == "ZOOM"
        assert out["amount"] == -2

    def test_rotate_honest_not_mapped(self):
        out = TemporalRecognizer().recognize_two_hand(
            self._report("TWO_HAND_ROTATE", angle_delta_deg=25.0))
        assert out["detected"] == "ROTATE"
        assert out["os_action"] == "NOT MAPPED"
        assert out["amount"] == pytest.approx(25.0)

    def test_drag_honest_not_mapped(self):
        out = TemporalRecognizer().recognize_two_hand(
            self._report("TWO_HAND_DRAG", centroid_delta=(0.03, 0.04)))
        assert out["detected"] == "DRAG"
        assert out["os_action"] == "NOT MAPPED"
        assert out["amount"] == pytest.approx(0.05)

    def test_hold_detected_not_mapped(self):
        out = TemporalRecognizer().recognize_two_hand(
            self._report("TWO_HAND_HOLD"))
        assert out["detected"] == "HOLD"
        assert out["os_action"] == "NOT MAPPED"

    def test_counting_and_none(self):
        rec = TemporalRecognizer()
        counting = rec.recognize_two_hand(
            self._report(None, active=False, confidence=0.5))
        assert counting["detected"] == "COUNTING"
        none = rec.recognize_two_hand(
            self._report(None, active=False, confidence=0.0))
        assert none["detected"] == "NONE"
        assert none["os_action"] == "NOT MAPPED"

    def test_malformed_report_is_safe(self):
        out = TemporalRecognizer().recognize_two_hand({})
        assert out["detected"] == "NONE"
        for key in ("detected", "os_action", "amount"):
            assert key in out

    def test_live_two_hand_integration_zoom(self):
        """Real TwoHandGestureRecognizer report → zoom_ticks mapping."""
        from airmouse.two_hand import TwoHandGestureRecognizer
        eng = TwoHandGestureRecognizer()
        t = T0
        report = None
        for k in range(4):                           # engage (4 frames)
            report = eng.update(
                [_hand_dict(0.35, 0.5, "Left"), _hand_dict(0.65, 0.5, "Right")],
                t + k * DT)
        assert report["gesture"] == "TWO_HAND_HOLD"
        assert TemporalRecognizer().recognize_two_hand(
            report)["detected"] == "HOLD"
        # spread hands apart → ZOOM (scale ≈ 0.44/0.30)
        report = eng.update(
            [_hand_dict(0.28, 0.5, "Left"), _hand_dict(0.72, 0.5, "Right")],
            t + 5 * DT)
        assert report["gesture"] == "TWO_HAND_ZOOM"
        out = TemporalRecognizer().recognize_two_hand(report)
        assert out["detected"] == "ZOOM"
        assert out["os_action"] == "zoom_ticks"
        assert out["amount"] == int(round((report["scale"] - 1.0) / 0.05))
        assert out["amount"] >= 5                    # clearly zooming IN


# ═══════════════════════════════════════════════════════════════════════════
# 4) CompositionResolver — multimodal unifier
# ═══════════════════════════════════════════════════════════════════════════

class TestCompositionResolver:
    def setup_method(self):
        self.r = CompositionResolver()

    def test_voice_plus_gaze_targets_gazed_object(self):
        out = self.r.resolve(voice_intent="click", gaze_target="btn-1")
        assert out["intent"] == "left_click"
        assert out["target"] == "btn-1"
        assert out["sources"] == ["voice", "gaze"]
        assert out["requires"] == "spine_dispatch"

    def test_gaze_plus_pinch_activates_target(self):
        ev = {"event": "pinch_release", "proposal": "left_click",
              "confidence": 0.9}
        out = self.r.resolve(gaze_target="btn-1", gesture_event=ev)
        assert out["intent"] == "activate_gaze_target"
        assert out["target"] == "btn-1"
        assert out["sources"] == ["gaze", "gesture"]
        assert out["requires"] == "spine_dispatch"

    def test_voice_gesture_same_action_dedupes_to_one(self):
        ev = {"event": "pinch_release", "proposal": "left_click"}
        out = self.r.resolve(voice_intent="click", gesture_event=ev)
        assert isinstance(out, dict)                 # EXACTLY ONE proposal
        assert out["intent"] == "left_click"
        assert out["sources"] == ["voice", "gesture"]
        assert "superseded" not in out               # merged, not conflicted

    def test_conflicting_actions_gesture_wins(self):
        ev = {"event": "pinch_release", "proposal": "left_click"}
        out = self.r.resolve(voice_intent="scroll up", gesture_event=ev)
        assert out["intent"] == "left_click"         # gesture > voice
        assert out["resolved_by"].startswith("priority:")
        assert out["superseded"] == [{"intent": "scroll",
                                      "sources": ["voice"]}]

    def test_confirmed_voice_wins_conflict(self):
        ev = {"event": "pinch_release", "proposal": "left_click"}
        out = self.r.resolve(voice_intent="scroll up", gesture_event=ev,
                             context={"confirmed": True})
        assert out["intent"] == "scroll"
        assert "left_click" in [s["intent"] for s in out["superseded"]]

    def test_voice_gaze_gesture_merge_to_one_confirmation(self):
        ev = {"event": "pinch_release", "proposal": "left_click"}
        out = self.r.resolve(voice_intent="click", gaze_target="btn-1",
                             gesture_event=ev)
        assert out["intent"] == "activate_gaze_target"
        assert out["target"] == "btn-1"
        assert out["sources"] == ["voice", "gaze", "gesture"]

    def test_estop_context_returns_none(self):
        ev = {"event": "pinch_release", "proposal": "left_click"}
        assert self.r.resolve(voice_intent="click", gaze_target="b",
                              gesture_event=ev,
                              context={"estopped": True}) is None
        assert self.r.resolve(voice_intent="click",
                              context={"estop": True}) is None

    def test_no_actionable_inputs_returns_none(self):
        assert self.r.resolve() is None
        assert self.r.resolve(gaze_target="btn-1") is None   # gaze ≠ action
        assert self.r.resolve(voice_intent="",
                              gesture_event={"event": "pinch_start"}) is None

    def test_gesture_only_passthrough(self):
        out = self.r.resolve(gesture_event="pinch")
        assert out["intent"] == "left_click"
        assert out["sources"] == ["gesture"]
        assert "target" not in out

    def test_voice_alias_and_params(self):
        out = self.r.resolve(voice_intent={"intent": "double click",
                                           "params": {"x": 1}})
        assert out["intent"] == "double_click"
        assert out["params"] == {"x": 1}

    def test_double_pinch_merge(self):
        out = self.r.resolve(voice_intent="double click",
                             gesture_event="double_pinch")
        assert out["intent"] == "double_click"
        assert sorted(out["sources"]) == ["gesture", "voice"]

    def test_proposal_is_prediction_only(self):
        out = self.r.resolve(voice_intent="click", gaze_target="b",
                             gesture_event="pinch")
        assert out["requires"] == "spine_dispatch"
        assert "executed" not in out                 # nothing executed here
        assert all(not callable(v) for v in out.values())


# ═══════════════════════════════════════════════════════════════════════════
# 5) Robustness toolkit contracts
# ═══════════════════════════════════════════════════════════════════════════

class TestRobustnessToolkit:
    def test_hysteresis_band(self):
        h = Hysteresis(enter=0.7, exit=0.4)
        assert h.update(0.5) is False
        assert h.update(0.75) is True                # crosses enter
        assert h.update(0.5) is True                 # band keeps state
        assert h.update(0.41) is True
        assert h.update(0.39) is False               # below exit
        assert h.update(0.65) is False               # band ≠ enter from below
        assert h.update(0.70) is True

    def test_hysteresis_validation(self):
        with pytest.raises(ValueError):
            Hysteresis(enter=0.4, exit=0.7)

    def test_debouncer_interval_and_auto_arm(self):
        d = Debouncer(min_interval_s=0.5)
        assert d.ready(0.0) is True                  # auto-armed
        assert d.ready(0.2) is False
        assert d.ready(0.49) is False
        assert d.ready(0.5) is True
        assert d.ready(0.6) is False
        d.rearm()
        assert d.ready(0.61) is True

    def test_suppressor_frames_then_cooldown(self):
        s = FalsePositiveSuppressor(min_confidence=0.6, min_frames=3,
                                    cooldown_s=1.0)
        assert s.feed(0.7, 0.0) is False
        assert s.feed(0.7, 0.1) is False
        assert s.feed(0.7, 0.2) is True              # confirmed on frame 3
        assert s.feed(0.7, 0.3) is False             # cooldown lockout
        assert s.feed(0.7, 0.5) is False
        assert s.feed(0.7, 1.0) is False             # still inside cooldown
        assert s.feed(0.7, 1.2) is True              # sustained → reconfirms

    def test_suppressor_low_confidence_resets_streak(self):
        s = FalsePositiveSuppressor(min_confidence=0.6, min_frames=3,
                                    cooldown_s=0.0)
        assert s.feed(0.7, 0.0) is False
        assert s.feed(0.1, 0.1) is False             # streak broken
        assert s.feed(0.7, 0.2) is False
        assert s.feed(0.7, 0.3) is False
        assert s.feed(0.7, 0.4) is True              # needs 3 again

    def test_recovery_state_machine_and_events(self):
        tr = TrackingRecovery(grace_s=0.3)
        assert tr.on_frame(True, 0.0) == "ok"
        assert tr.on_frame(False, 0.1) == "recovering"
        assert tr.on_frame(False, 0.25) == "recovering"
        assert tr.pop_event() is None                # no event spam yet
        assert tr.on_frame(False, 0.4) == "lost"     # past grace
        assert tr.pop_event() == "lost"
        assert tr.pop_event() is None                # one-shot
        assert tr.on_frame(False, 0.5) == "lost"     # state repeats…
        assert tr.pop_event() is None                # …event does not
        assert tr.on_frame(True, 0.6) == "ok"
        assert tr.pop_event() == "recovered"
        assert tr.pop_event() is None

    def test_recovery_never_seen_ok_goes_lost(self):
        tr = TrackingRecovery(grace_s=0.3)
        assert tr.on_frame(False, 1.0) == "lost"
        assert tr.pop_event() == "lost"

    def test_watchdog_health_states(self):
        w = CameraWatchdog(max_stale_s=1.0)
        assert w.health(T0) == "lost"                # before first frame
        assert w.on_frame(T0) == "healthy"
        assert w.health(T0 + 0.5) == "healthy"
        assert w.health(T0 + 1.5) == "stale"
        assert w.health(T0 + 3.5) == "lost"
        assert w.on_frame(T0 + 4.0) == "healthy"     # frame recovers it

    def test_watchdog_custom_lost_threshold(self):
        w = CameraWatchdog(max_stale_s=1.0, lost_after_s=1.5)
        w.on_frame(0.0)
        assert w.health(1.2) == "stale"
        assert w.health(1.6) == "lost"

    def test_health_score_monotonic_degradation(self):
        hs = SensorHealthScore(window=10)
        assert hs.rating() == "good"
        prev = hs.score
        for _ in range(10):
            cur = hs.feed(False)
            assert cur < prev                        # strictly decreasing
            prev = cur
        assert hs.rating() == "poor"
        # standard EWMA factor alpha = 2/(window+1) → (1-alpha)^10
        alpha = 2.0 / 11.0
        assert hs.score == pytest.approx((1.0 - alpha) ** 10)

    def test_health_score_rating_thresholds(self):
        hs = SensorHealthScore(window=10)
        for _ in range(4):                           # 0.9^4 ≈ 0.656
            hs.feed(False)
        assert hs.rating() == "degraded"

    def test_health_score_low_confidence_reduces(self):
        hs = SensorHealthScore(window=10)
        before = hs.score
        after = hs.feed(True, 0.1)                   # kept but uncertain
        assert after < before

    def test_health_score_recovers_with_good_frames(self):
        hs = SensorHealthScore(window=10)
        for _ in range(8):
            hs.feed(False)
        assert hs.rating() == "poor"
        for _ in range(30):
            hs.feed(True, 1.0)
        assert hs.rating() == "good"


# ═══════════════════════════════════════════════════════════════════════════
# 6) Architecture guards + module contract
# ═══════════════════════════════════════════════════════════════════════════

class TestArchitectureGuards:
    def test_no_parallel_dispatch_imports_or_calls(self):
        """temporal.py must not import or call input-automation / OS
        dispatch machinery — prediction never equals execution."""
        src = MODULE_PATH.read_text(encoding="utf-8")
        # hard words must not appear anywhere at all
        for word in ("pynput", "ctypes", "subprocess"):
            assert not re.search(rf"\b{word}\b", src), word
        # no os import / os.system-style calls
        assert not re.search(r"^\s*import\s+os\b", src, re.M)
        assert not re.search(r"^\s*from\s+os\b", src, re.M)
        assert not re.search(r"\bos\.system\s*\(", src)
        # no input automation modules imported, nor attribute-called
        assert not re.search(r"^\s*(import|from)\s+(keyboard|mouse)\b",
                             src, re.M)
        assert not re.search(r"(?<![A-Za-z_])(keyboard|mouse)\s*\.", src)
        # no subprocess-ish escapes
        assert not re.search(r"\b(exec|eval|popen|system)\s*\(", src)

    def test_module_docstring_dispatch_contract(self):
        import airmouse.temporal as tm
        doc = " ".join((tm.__doc__ or "").split())   # unwrap line breaks
        assert "All outputs are intents/proposals." in doc
        assert ("Dispatch happens ONLY through "
                "airmouse.gesture_spine.GestureActionRouter / airmouse.intent."
                ) in doc
        assert ("This module performs no OS actions and imports no input "
                "automation libraries.") in doc

    def test_public_api_surface(self):
        import airmouse.temporal as tm
        for name in ("Sample", "TrajectoryBuffer", "PinchLifecycle",
                     "TemporalRecognizer", "CompositionResolver",
                     "Hysteresis", "Debouncer", "FalsePositiveSuppressor",
                     "TrackingRecovery", "CameraWatchdog", "SensorHealthScore"):
            assert hasattr(tm, name) and name in tm.__all__
        assert set(PINCH_EVENTS) == {
            "pinch_start", "pinch_hold", "pinch_move", "pinch_release",
            "double_pinch"}

    def test_label_constants_align_with_gestures_module(self):
        from airmouse.gestures import Gesture
        assert PINCH_LABEL == Gesture.PINCH
        assert POINTING_LABEL == Gesture.POINTING

    def test_composition_table_documents_spine_vocabulary(self):
        from airmouse.temporal import COMPOSITION_TABLE
        assert COMPOSITION_TABLE["pointing->pinch"] == "select_target"
        assert COMPOSITION_TABLE["pinch+vertical"] == "scroll"
        assert COMPOSITION_TABLE["two_hand_distance"] == "zoom"
        assert COMPOSITION_TABLE["gaze+pinch"] == "activate_gaze_target"
