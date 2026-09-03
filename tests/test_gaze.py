"""Tests for airmouse.gaze (v6): estimator, blink, dwell, engine, FaceMesh smoke.

Fully deterministic — synthetic landmarks + scripted timestamps only, no
camera, no network.  One optional smoke test exercises the real mediapipe
FaceMesh on a black frame and skips only when mediapipe is unavailable.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from airmouse.gaze import (
    BlinkClassifier,
    DwellDetector,
    FaceMeshTracker,
    GazeEngine,
    GazeEstimator,
    LandmarkFrame,
    LandmarkPoint,
)
from airmouse.gaze_calibration import GazeCalibration, run_point_calibration
from airmouse.interfaces import GazeEventKind, GazeSample


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic landmark builder (reused by the engine tests)
# ─────────────────────────────────────────────────────────────────────────────

def make_landmarks(gaze_gx: float = 0.0, gaze_gy: float = 0.0,
                   eye_open_l: bool = True, eye_open_r: bool = True,
                   head_dx: float = 0.0, head_dy: float = 0.0,
                   cx: float = 0.5, cy: float = 0.5, face_w: float = 0.35,
                   ear: float = 0.32) -> LandmarkFrame:
    """Build a FaceMesh-indexed landmark frame with chosen gaze/eye/head state.

    Geometry: face oval (10/152/234/454) centred at (cx, cy); eyes at
    ±0.22·face_w horizontally, ear height = 0.32 (open) or 0.05 (closed);
    iris 468/473 offset from eye-centre by (gaze_gx, gaze_gy)·eye_width;
    nose tip 1 offset from face centre by (head_dx·face_w, head_dy·face_h).
    """
    fw = face_w
    fh = 1.35 * fw
    eye_w = 0.26 * fw
    ear_r = ear if eye_open_r else 0.05
    ear_l = ear if eye_open_l else 0.05
    er = (cx - 0.22 * fw, cy - 0.10 * fh)   # right eye centre
    el = (cx + 0.22 * fw, cy - 0.10 * fh)   # left eye centre
    pts: dict = {
        10: (cx, cy - fh / 2),
        152: (cx, cy + fh / 2),
        234: (cx - fw / 2, cy),
        454: (cx + fw / 2, cy),
        1: (cx + head_dx * fw, cy + head_dy * fh),
        33: (er[0] - eye_w / 2, er[1]),
        133: (er[0] + eye_w / 2, er[1]),
        159: (er[0], er[1] - ear_r * eye_w / 2),
        145: (er[0], er[1] + ear_r * eye_w / 2),
        468: (er[0] + gaze_gx * eye_w, er[1] + gaze_gy * eye_w),
        362: (el[0] - eye_w / 2, el[1]),
        263: (el[0] + eye_w / 2, el[1]),
        386: (el[0], el[1] - ear_l * eye_w / 2),
        374: (el[0], el[1] + ear_l * eye_w / 2),
        473: (el[0] + gaze_gx * eye_w, el[1] + gaze_gy * eye_w),
    }
    lst: list = [None] * 478
    for i, (x, y) in pts.items():
        lst[i] = LandmarkPoint(float(x), float(y), 0.0)
    return LandmarkFrame(lst)


# ─────────────────────────────────────────────────────────────────────────────
# GazeEstimator
# ─────────────────────────────────────────────────────────────────────────────

class TestGazeEstimator:
    def test_centered_gaze(self):
        est = GazeEstimator()
        s = est.estimate(make_landmarks(), timestamp=1.0)
        assert s.x == pytest.approx(0.5, abs=1e-9)
        assert s.y == pytest.approx(0.5, abs=1e-9)
        assert s.confidence >= 0.5
        assert s.eye_open_l and s.eye_open_r
        assert 0.25 <= s.ear_l <= 0.45  # sane open-eye EAR

    def test_gaze_direction_convention(self):
        # Documented convention: gx > 0 ⇒ iris displaced toward image-right
        # ⇒ mapped gaze point x > 0.5 (mirrored preview = screen-right).
        est = GazeEstimator()
        right = est.estimate(make_landmarks(gaze_gx=0.3), timestamp=1.0)
        left = est.estimate(make_landmarks(gaze_gx=-0.3), timestamp=1.0)
        assert right.x > 0.5 > left.x
        assert right.x == pytest.approx(0.5 + 0.3 * est.gaze_range_x, abs=1e-9)
        down = est.estimate(make_landmarks(gaze_gy=0.3), timestamp=1.0)
        up = est.estimate(make_landmarks(gaze_gy=-0.3), timestamp=1.0)
        assert down.y > 0.5 > up.y

    def test_closed_eye_ear(self):
        est = GazeEstimator()
        s = est.estimate(make_landmarks(eye_open_l=False, eye_open_r=True),
                         timestamp=1.0)
        assert not s.eye_open_l
        assert s.eye_open_r
        assert s.ear_l < est.open_threshold <= s.ear_r

    def test_head_turn_reduces_confidence(self):
        est = GazeEstimator()
        centered = est.estimate(make_landmarks(), timestamp=1.0)
        turned = est.estimate(make_landmarks(head_dx=0.45), timestamp=1.0)
        assert turned.head_dx == pytest.approx(0.45, abs=1e-9)
        assert turned.confidence < centered.confidence
        assert turned.confidence < 0.55  # below the action gate

    def test_none_landmarks_zero_confidence(self):
        est = GazeEstimator()
        s = est.estimate(None, timestamp=1.0)
        assert s.confidence == 0.0
        assert s.x == 0.5 and s.y == 0.5

    def test_tiny_face_reduces_confidence(self):
        est = GazeEstimator()
        big = est.estimate(make_landmarks(face_w=0.35), timestamp=1.0)
        tiny = est.estimate(make_landmarks(face_w=0.06), timestamp=1.0)
        assert tiny.confidence < big.confidence


# ─────────────────────────────────────────────────────────────────────────────
# BlinkClassifier
# ─────────────────────────────────────────────────────────────────────────────

def run_blink_script(blk: BlinkClassifier, script):
    """script = list of (t, eye_open_l, eye_open_r, conf); returns all events."""
    events = []
    for t, ol, orr, conf in script:
        events.extend(blk.update(ol, orr, conf, timestamp=t))
    return events


class TestBlinkClassifier:
    def _blk(self) -> BlinkClassifier:
        return BlinkClassifier({"blink_min_confidence": 0.5})

    def test_normal_blink(self):
        blk = self._blk()
        script = [(0.01 * i, True, True, 0.9) for i in range(10)]          # open 0–0.09
        script += [(0.10 + 0.01 * i, False, False, 0.9) for i in range(15)]  # closed 0.10–0.24
        script += [(0.25, True, True, 0.9)]
        events = run_blink_script(blk, script)
        assert events == [GazeEventKind.BLINK]

    def test_long_blink(self):
        blk = self._blk()
        script = [(0.01 * i, True, True, 0.9) for i in range(10)]
        script += [(1.00 + 0.01 * i, False, False, 0.9) for i in range(80)]  # 0.8 s closed
        script += [(1.80, True, True, 0.9)]
        events = run_blink_script(blk, script)
        assert events == [GazeEventKind.LONG_BLINK]  # emitted once, no plain BLINK

    def test_double_blink(self):
        blk = self._blk()
        script = []
        script += [(0.10 + 0.01 * i, False, False, 0.9) for i in range(15)]
        script += [(0.25, True, True, 0.9)]                                # BLINK
        script += [(0.35 + 0.01 * i, False, False, 0.9) for i in range(15)]
        script += [(0.50, True, True, 0.9)]                                # within 0.5 s
        events = run_blink_script(blk, script)
        assert events == [GazeEventKind.BLINK, GazeEventKind.DOUBLE_BLINK]

    def test_winks(self):
        blk = self._blk()
        # left eye closed 0.5 s while right stays open → WINK_LEFT
        script = [(2.00 + 0.01 * i, False, True, 0.9) for i in range(50)]
        script += [(2.50, True, True, 0.9)]
        assert run_blink_script(blk, script) == [GazeEventKind.WINK_LEFT]
        blk2 = self._blk()
        script = [(3.00 + 0.01 * i, True, False, 0.9) for i in range(50)]
        script += [(3.50, True, True, 0.9)]
        assert run_blink_script(blk2, script) == [GazeEventKind.WINK_RIGHT]

    def test_low_confidence_never_emits(self):
        blk = self._blk()
        script = [(0.01 * i, True, True, 0.9) for i in range(10)]
        script += [(0.10 + 0.01 * i, False, False, 0.3) for i in range(30)]  # low conf
        script += [(0.40, True, True, 0.9)]
        assert run_blink_script(blk, script) == []

    def test_brief_noise_below_40ms_ignored(self):
        blk = self._blk()
        script = [(0.01 * i, True, True, 0.9) for i in range(10)]
        script += [(0.10 + 0.01 * i, False, False, 0.9) for i in range(3)]  # 30 ms
        script += [(0.13, True, True, 0.9)]
        assert run_blink_script(blk, script) == []


# ─────────────────────────────────────────────────────────────────────────────
# DwellDetector
# ─────────────────────────────────────────────────────────────────────────────

class TestDwellDetector:
    def _feed(self, dwl: DwellDetector, cx: float, n: int, t0: float = 0.0):
        """Feed n ticks of alternating ±0.0005 jitter around (cx, 0.5)."""
        events = []
        for i in range(n):
            jx = 0.0005 if i % 2 == 0 else -0.0005
            events.extend(dwl.update(cx + jx, 0.5, 0.9, timestamp=t0 + i / 30.0))
        return events

    def test_fixation_then_dwell(self):
        dwl = DwellDetector()
        events = self._feed(dwl, 0.5, 60)
        assert GazeEventKind.FIXATION_START in events
        assert GazeEventKind.DWELL in events
        assert events.index(GazeEventKind.FIXATION_START) < events.index(GazeEventKind.DWELL)
        assert dwl.fixation and dwl.dwell_fired
        assert dwl.fixation_duration > 0.5

    def test_no_refire_until_exit(self):
        dwl = DwellDetector()
        self._feed(dwl, 0.5, 60)          # fixation + dwell
        assert dwl.dwell_fired
        more = self._feed(dwl, 0.5, 60, t0=2.0)   # keep staring
        assert GazeEventKind.DWELL not in more    # no re-fire while inside

    def test_exit_resets_and_refires(self):
        dwl = DwellDetector()
        self._feed(dwl, 0.5, 60)
        # jump far beyond the dwell radius → FIXATION_END, dwell re-arms
        jump = dwl.update(0.56, 0.5, 0.9, timestamp=2.0)
        assert GazeEventKind.FIXATION_END in jump
        assert not dwl.dwell_fired
        rest = self._feed(dwl, 0.56, 60, t0=2.033)
        assert GazeEventKind.FIXATION_START in rest
        assert GazeEventKind.DWELL in rest        # re-fired after exit+re-enter

    def test_low_confidence_breaks_fixation(self):
        dwl = DwellDetector()
        self._feed(dwl, 0.5, 40)
        assert dwl.fixation
        ev = dwl.update(0.5, 0.5, 0.1, timestamp=1.5)
        assert GazeEventKind.FIXATION_END in ev
        assert not dwl.fixation


# ─────────────────────────────────────────────────────────────────────────────
# GazeEngine (end-to-end, simulated)
# ─────────────────────────────────────────────────────────────────────────────

def _calibrate_identity(cal: GazeCalibration, n_per_point: int = 8) -> None:
    """Fit an identity mapping: gaze (x,y) → screen (x·1920, y·1080)."""
    t = 0.0
    for target in cal.begin():
        for _ in range(n_per_point):
            cal.add_sample(target, GazeSample(x=target[0], y=target[1],
                                              confidence=0.95, timestamp=t))
            t += 1 / 30.0
    cal.finish(screen_w=1920, screen_h=1080)   # fit the identity matrix


class TestGazeEngine:
    def _make_engine(self, config=None) -> GazeEngine:
        eng = GazeEngine(config)
        cal = GazeCalibration(path=None, config=(config or {}))
        _calibrate_identity(cal)
        eng.set_calibration(cal)
        return eng

    def test_end_to_end_screen_mapping_and_dwell(self):
        eng = self._make_engine()
        rx = eng.estimator.gaze_range_x
        gx = (0.6 - 0.5) / rx        # gaze offset that lands raw x on 0.6
        lm = make_landmarks(gaze_gx=gx, gaze_gy=0.0)
        all_events = []
        for i in range(60):          # 2 s @ 30 fps
            st = eng.update(lm)
            all_events.extend(st.events)
        assert st.confidence >= 0.5
        assert st.screen_valid
        assert st.screen_x == pytest.approx(0.6 * 1920, abs=25)
        assert st.screen_y == pytest.approx(0.5 * 1080, abs=25)
        assert GazeEventKind.FACE_FOUND in all_events
        assert GazeEventKind.FIXATION_START in all_events
        assert GazeEventKind.DWELL in all_events
        assert st.fixation and st.dwell_fired

    def test_face_lost_transition(self):
        eng = self._make_engine()
        lm = make_landmarks()
        for _ in range(5):
            eng.update(lm)
        st = eng.update(None)
        assert GazeEventKind.FACE_LOST in st.events
        assert st.confidence == 0.0
        assert not st.screen_valid

    def test_low_confidence_suppresses_dwell(self):
        eng = GazeEngine({"gaze_min_action_confidence": 0.99})
        cal = GazeCalibration(path=None)
        _calibrate_identity(cal)
        eng.set_calibration(cal)
        lm = make_landmarks()
        seen = []
        for _ in range(60):
            seen.extend(eng.update(lm).events)
        assert GazeEventKind.DWELL not in seen       # suppressed by the gate
        assert GazeEventKind.BLINK not in seen
        assert GazeEventKind.FIXATION_START in seen  # bookkeeping kept
        # and the detector itself did fire internally (gate, not detector, stripped)
        assert eng.dwell.dwell_fired

    def test_head_turn_suppresses_dwell(self):
        eng = self._make_engine()
        lm = make_landmarks(head_dx=0.45)            # confidence ≈ 0.27
        seen = []
        for _ in range(90):
            seen.extend(eng.update(lm).events)
        assert GazeEventKind.DWELL not in seen

    def test_apply_filter_only(self):
        eng = self._make_engine()
        out = eng.apply_filter_only(GazeSample(x=0.6, y=0.4, confidence=0.9,
                                               timestamp=0.5))
        assert isinstance(out, GazeSample)
        assert out.x == pytest.approx(0.6)          # first sample snaps
        out2 = eng.apply_filter_only(GazeSample(x=0.2, y=0.4, confidence=0.9,
                                                timestamp=0.533))
        assert 0.2 < out2.x < 0.6                    # EMA moved partially
        assert out2.confidence == pytest.approx(0.9)

    def test_blink_event_flows_through_engine(self):
        eng = self._make_engine()
        events = []
        t = 0.0
        for _ in range(10):                       # open
            events.extend(eng.update(make_landmarks(), timestamp=t).events)
            t += 0.01
        for _ in range(15):                       # both eyes closed 0.15 s
            events.extend(eng.update(make_landmarks(eye_open_l=False,
                                                    eye_open_r=False),
                                     timestamp=t).events)
            t += 0.01
        events.extend(eng.update(make_landmarks(), timestamp=t).events)
        assert GazeEventKind.BLINK in events


# ─────────────────────────────────────────────────────────────────────────────
# FaceMeshTracker smoke (real mediapipe, camera-free)
# ─────────────────────────────────────────────────────────────────────────────

class TestFaceMeshSmoke:
    def test_facemesh_construct_process_close(self):
        pytest.importorskip("mediapipe")
        tr = FaceMeshTracker()
        assert tr.available is True
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        out = tr.process(frame, timestamp=1.0)   # black frame → no face
        assert out is None                        # and no exception
        tr.close()
        tr.close()                                # idempotent
