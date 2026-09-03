"""
airmouse.gaze — v6 GAZE subsystem: webcam face/eye sensing, gaze estimation
and eye events.

Pipeline position in the v9 architecture::

    FaceMeshTracker ─► GazeEstimator ─► GazeFilterPipeline (gaze_filter)
                                         GazeCalibration   (gaze_calibration)
                      BlinkClassifier ─┐
                      DwellDetector  ──┤
                                       ▼
                    GazeEngine  ─►  GazeState  (consumed by fusion v7)

DESIGN RULES (house style)
--------------------------
1.  Heavy dependencies (cv2, mediapipe) are imported LAZILY inside methods /
    constructors — this module imports headless with zero hardware present.
2.  Graceful degradation: when mediapipe or the camera is missing every
    object still constructs, reports ``available == False`` (tracker) or
    zero-confidence results (estimator), and NEVER raises during normal
    operation.
3.  All pure logic (estimator, blink, dwell) is deterministic and works on
    injected landmarks — NO camera is needed to test any of it.  Synthetic
    landmark frames can be built from :class:`LandmarkPoint` /
    :class:`LandmarkFrame` (see tests/test_gaze.py).
4.  Timestamps are ``time.perf_counter()`` style floats (seconds).  When a
    caller omits a timestamp an internal 30 fps frame clock is used so that
    simulations stay fully deterministic.

COORDINATE CONVENTIONS
----------------------
* Landmarks: MediaPipe FaceMesh normalized coordinates, x right / y down in
  [0, 1], z depth (unused).  ``refine_landmarks=True`` adds the 10 iris
  landmarks 468-477 (right iris center 468, left iris center 473).
* Raw gaze: normalized FRAME space [0, 1]^2 with (0.5, 0.5) = frame centre
  (see ``interfaces.GazeSample``).  Screen targets are PIXELS.

Direction convention (IMPORTANT, asserted in tests): the per-eye normalized
offset (gx, gy) is the iris-centre displacement from the eye-corner midpoint
divided by the eye width.  ``gx > 0`` means BOTH irises are displaced toward
image-right, ``gy > 0`` toward image-bottom.  When the user looks sideways
each iris slides toward the outer corner of its eye; with the app's standard
mirrored (selfie) preview image-right == the user's screen-right, so
``gx > 0`` ≈ "looking toward screen-right".  The gaze point is mapped into
frame space as ``x = 0.5 + gx * gaze_range_x`` (same for y).

Classes:
    LandmarkPoint   — lightweight .x/.y/.z landmark record
    LandmarkFrame   — list-like container of 478 LandmarkPoints
    FaceMeshTracker — mediapipe FaceMesh wrapper (probe + never raises)
    GazeEstimator   — landmarks -> GazeSample (gaze point, EAR, head gate)
    BlinkClassifier — eye-open/close tick stream -> blink/wink events
    DwellDetector   — filtered gaze points -> fixation/dwell events
    GazeEngine      — orchestrator: everything above -> GazeState

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Iterable, Iterator, List, Optional, Tuple

import numpy as np

from .interfaces import GazeEventKind, GazeSample, GazeState, now_ts
from .gaze_filter import GazeFilterPipeline
from .gaze_calibration import GazeCalibration

__all__ = [
    "LandmarkPoint",
    "LandmarkFrame",
    "FaceMeshTracker",
    "GazeEstimator",
    "BlinkClassifier",
    "DwellDetector",
    "GazeEngine",
]

# Event kinds suppressed by the engine's low-confidence action gate.
_SUPPRESSED_LOW_CONF = frozenset({
    GazeEventKind.DWELL,
    GazeEventKind.BLINK,
    GazeEventKind.LONG_BLINK,
    GazeEventKind.DOUBLE_BLINK,
    GazeEventKind.WINK_LEFT,
    GazeEventKind.WINK_RIGHT,
})

_DT_DEFAULT = 1.0 / 30.0
_DT_MIN = 1e-3
_DT_MAX = 0.5


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp *v* into the closed interval [lo, hi]."""
    return lo if v < lo else hi if v > hi else v


def _finite(*vals: float) -> bool:
    """True when every value is a finite float."""
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


# ─────────────────────────────────────────────────────────────────────────────
# Landmark containers
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class LandmarkPoint:
    """One normalized face landmark (MediaPipe FaceMesh coordinates)."""

    x: float
    y: float
    z: float = 0.0


class LandmarkFrame:
    """Lightweight list-like container of :class:`LandmarkPoint`.

    Supports ``len()``, indexing, iteration and safe ``get(idx)`` access.
    Index semantics match MediaPipe FaceMesh with ``refine_landmarks=True``
    (468 base landmarks + 10 iris landmarks 468-477).  Holes (``None``
    entries) are allowed so synthetic/test frames can populate only the
    landmarks they care about.
    """

    __slots__ = ("_points",)

    def __init__(self, points: Optional[Iterable[Optional[LandmarkPoint]]] = None):
        self._points: List[Optional[LandmarkPoint]] = list(points or [])

    def __len__(self) -> int:
        return len(self._points)

    def __getitem__(self, idx: int) -> Optional[LandmarkPoint]:
        return self._points[idx]

    def __iter__(self) -> Iterator[Optional[LandmarkPoint]]:
        return iter(self._points)

    def get(self, idx: int) -> Optional[LandmarkPoint]:
        """Safe accessor — returns None when out of range or missing."""
        if 0 <= idx < len(self._points):
            return self._points[idx]
        return None

    @property
    def points(self) -> List[Optional[LandmarkPoint]]:
        return self._points


def _pt(landmarks: Any, idx: int) -> Optional[Any]:
    """Fetch landmark *idx* from any list-like container; None when absent.

    Works with :class:`LandmarkFrame`, plain lists and raw mediapipe landmark
    lists (anything supporting ``len()`` + indexing with .x/.y attributes).
    Never raises on malformed input.
    """
    try:
        n = len(landmarks)
    except TypeError:
        return None
    if idx < 0 or idx >= n:
        return None
    lm = landmarks[idx]
    if lm is None:
        return None
    try:
        float(lm.x)
        float(lm.y)
    except (TypeError, ValueError, AttributeError):
        return None
    return lm


# ─────────────────────────────────────────────────────────────────────────────
# FaceMeshTracker
# ─────────────────────────────────────────────────────────────────────────────


class FaceMeshTracker:
    """Webcam face landmark source wrapping ``mediapipe.solutions.face_mesh``.

    The mediapipe import is attempted lazily in the constructor probe; when
    it fails the tracker still constructs with ``available == False`` and
    :meth:`process` simply returns None forever (no exception escapes).

    Config keys (all optional, ``fm_`` namespace):
        fm_static (bool, False)           static_image_mode
        fm_max_faces (int, 1)             max_num_faces
        fm_refine (bool, True)            refine_landmarks (adds iris 468-477)
        fm_min_detection_confidence (0.5)
        fm_min_tracking_confidence (0.5)
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.config = cfg
        self.static = bool(cfg.get("fm_static", False))
        self.max_num_faces = int(cfg.get("fm_max_faces", 1))
        self.refine_landmarks = bool(cfg.get("fm_refine", True))
        self.min_detection_confidence = float(cfg.get("fm_min_detection_confidence", 0.5))
        self.min_tracking_confidence = float(cfg.get("fm_min_tracking_confidence", 0.5))
        self.available: bool = False
        self.last_timestamp: Optional[float] = None
        self._mesh: Any = None
        self._mesh_failed = False
        try:  # constructor probe — heavy dep stays OUT of module scope
            import mediapipe as _mp  # noqa: F401
            self.available = True
        except Exception:
            self.available = False

    def _ensure_mesh(self) -> Any:
        """Create the FaceMesh on first use; returns None when impossible."""
        if self._mesh is not None or self._mesh_failed or not self.available:
            return self._mesh
        try:
            import mediapipe as mp

            self._mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=self.static,
                max_num_faces=self.max_num_faces,
                refine_landmarks=self.refine_landmarks,
                min_detection_confidence=self.min_detection_confidence,
                min_tracking_confidence=self.min_tracking_confidence,
            )
        except Exception:
            self._mesh = None
            self._mesh_failed = True
            self.available = False
        return self._mesh

    def process(self, bgr_frame: Any, timestamp: Optional[float] = None) -> Optional[LandmarkFrame]:
        """Run FaceMesh on one BGR frame.

        Returns a :class:`LandmarkFrame` for the first detected face or None
        when no face / no mediapipe / any internal error.  Never raises.
        """
        self.last_timestamp = float(timestamp) if timestamp is not None else None
        mesh = self._ensure_mesh()
        if mesh is None or bgr_frame is None:
            return None
        try:
            frame = self._to_rgb(bgr_frame)
            result = mesh.process(frame)
            faces = getattr(result, "multi_face_landmarks", None) if result else None
            if not faces:
                return None
            lm = faces[0]
            pts = [LandmarkPoint(float(p.x), float(p.y), float(p.z)) for p in lm.landmark]
            if len(pts) < 468:  # refuse degenerate output (no iris refs)
                return None
            return LandmarkFrame(pts)
        except Exception:
            return None

    @staticmethod
    def _to_rgb(frame: Any) -> Any:
        """Best-effort BGR→RGB conversion; passes through without cv2."""
        try:
            import cv2

            return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        except Exception:
            return frame

    def close(self) -> None:
        """Release the underlying FaceMesh (idempotent)."""
        if self._mesh is not None:
            try:
                self._mesh.close()
            except Exception:
                pass
        self._mesh = None


# ─────────────────────────────────────────────────────────────────────────────
# GazeEstimator
# ─────────────────────────────────────────────────────────────────────────────


class GazeEstimator:
    """Landmarks → :class:`GazeSample` (gaze point, EAR, head gate, confidence).

    Landmark index constants (MediaPipe FaceMesh, refine_landmarks=True):
        iris centers : 468 (right eye), 473 (left eye)
        right eye    : 33 lateral (outer) corner, 133 medial (inner) corner
        left eye     : 362 medial corner, 263 lateral corner
        eyelids      : right 159 (top) / 145 (bottom); left 386 / 374
        nose tip     : 1
        face oval    : 10 (forehead), 152 (chin), 234 (left), 454 (right)

    Per eye the normalized offset (gx, gy) is the iris-centre displacement
    from the eye-corner midpoint divided by the eye width; the two eyes are
    averaged.  Direction convention: gx > 0 ⇒ irises displaced toward
    image-right (user's screen-right under the mirrored preview), gy > 0 ⇒
    toward image-bottom.  Mapping into frame space:
        x = 0.5 + gx * gaze_range_x
        y = 0.5 + gy * gaze_range_y

    EAR (eye aspect ratio) per eye = eyelid separation / eye width; the eye
    counts as open when EAR > open_threshold (default 0.18, tunable).

    Head-pose gating: the nose-tip offset from the face-oval centre divided
    by face size yields head_dx / head_dy; turning beyond the configured
    thresholds progressively reduces confidence (gaze geometry degrades with
    head yaw, so a turned head must not produce confident clicks).

    This class produces NO events — see BlinkClassifier / DwellDetector.
    """

    # iris centers (refine_landmarks=True)
    IRIS_RIGHT: int = 468
    IRIS_LEFT: int = 473
    # eye corners
    EYE_R_OUTER: int = 33    # lateral / temporal corner, right eye
    EYE_R_INNER: int = 133   # medial / nasal corner, right eye
    EYE_L_INNER: int = 362   # medial corner, left eye
    EYE_L_OUTER: int = 263   # lateral corner, left eye
    # eyelids
    LID_R_TOP: int = 159
    LID_R_BOTTOM: int = 145
    LID_L_TOP: int = 386
    LID_L_BOTTOM: int = 374
    # nose + face oval reference points
    NOSE_TIP: int = 1
    FOREHEAD: int = 10
    CHIN: int = 152
    FACE_LEFT: int = 234
    FACE_RIGHT: int = 454

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.open_threshold = float(cfg.get("gaze_open_threshold", 0.18))
        self.gaze_range_x = float(cfg.get("gaze_range_x", 0.35))
        self.gaze_range_y = float(cfg.get("gaze_range_y", 0.30))
        self.head_yaw_max = float(cfg.get("gaze_head_yaw", 0.20))
        self.head_pitch_max = float(cfg.get("gaze_head_pitch", 0.16))
        self.face_min_width = float(cfg.get("gaze_face_min_width", 0.08))
        self.face_ref_width = float(cfg.get("gaze_face_ref_width", 0.25))

    # -- per-eye geometry ----------------------------------------------------

    def _eye_metrics(self, landmarks: Any, right: bool) -> Optional[Tuple[float, float, float]]:
        """(gx, gy, EAR) for one eye, or None when landmarks are missing."""
        if right:
            c_out, c_in = self.EYE_R_OUTER, self.EYE_R_INNER
            lid_t, lid_b, iris = self.LID_R_TOP, self.LID_R_BOTTOM, self.IRIS_RIGHT
        else:
            c_out, c_in = self.EYE_L_OUTER, self.EYE_L_INNER
            lid_t, lid_b, iris = self.LID_L_TOP, self.LID_L_BOTTOM, self.IRIS_LEFT
        p_out = _pt(landmarks, c_out)
        p_in = _pt(landmarks, c_in)
        p_t = _pt(landmarks, lid_t)
        p_b = _pt(landmarks, lid_b)
        p_i = _pt(landmarks, iris)
        if not (p_out and p_in and p_t and p_b and p_i):
            return None
        width = math.hypot(p_in.x - p_out.x, p_in.y - p_out.y)
        if width < 1e-6:
            return None
        mid_x = 0.5 * (p_out.x + p_in.x)
        mid_y = 0.5 * (p_out.y + p_in.y)
        gx = (p_i.x - mid_x) / width
        gy = (p_i.y - mid_y) / width
        ear = math.hypot(p_t.x - p_b.x, p_t.y - p_b.y) / width
        return gx, gy, ear

    def _head_score(self, head_dx: float, head_dy: float) -> float:
        """1.0 inside the pose window, decaying smoothly beyond it."""
        score = 1.0
        for v, t in ((abs(head_dx), self.head_yaw_max),
                     (abs(head_dy), self.head_pitch_max)):
            if v <= t or t <= 0.0:
                continue
            excess = (v - t) / t
            score = min(score, 1.0 / (1.0 + 2.0 * excess))
        return score

    # -- main entry ----------------------------------------------------------

    def estimate(self, landmarks: Any, timestamp: Optional[float] = None) -> GazeSample:
        """Estimate one gaze sample from a landmark frame.

        Missing face / missing geometry degrades gracefully to a
        zero- (or low-) confidence sample; never raises.
        """
        ts = float(timestamp) if timestamp is not None else now_ts()
        if landmarks is None:
            return GazeSample(x=0.5, y=0.5, confidence=0.0, eye_open_l=True,
                              eye_open_r=True, ear_l=1.0, ear_r=1.0,
                              head_dx=0.0, head_dy=0.0, timestamp=ts)

        p_l = _pt(landmarks, self.FACE_LEFT)
        p_r = _pt(landmarks, self.FACE_RIGHT)
        p_t = _pt(landmarks, self.FOREHEAD)
        p_b = _pt(landmarks, self.CHIN)
        p_n = _pt(landmarks, self.NOSE_TIP)

        face_w = 0.0
        face_h = 0.0
        face_cx, face_cy = 0.0, 0.0
        if p_l and p_r:
            face_w = abs(p_r.x - p_l.x)
            face_cx = 0.5 * (p_l.x + p_r.x)
        if p_t and p_b:
            face_h = abs(p_b.y - p_t.y)
            face_cy = 0.5 * (p_t.y + p_b.y)
        head_dx = 0.0
        head_dy = 0.0
        if p_n and face_w > 1e-6:
            head_dx = (p_n.x - face_cx) / face_w
        if p_n and face_h > 1e-6:
            head_dy = (p_n.y - face_cy) / face_h

        m_r = self._eye_metrics(landmarks, right=True)
        m_l = self._eye_metrics(landmarks, right=False)
        ear_r = m_r[2] if m_r else 1.0
        ear_l = m_l[2] if m_l else 1.0
        eye_open_r = bool(ear_r > self.open_threshold)
        eye_open_l = bool(ear_l > self.open_threshold)

        if m_r and m_l:
            gx = 0.5 * (m_r[0] + m_l[0])
            gy = 0.5 * (m_r[1] + m_l[1])
            gaze_ok = True
        elif m_r or m_l:
            m = m_r if m_r else m_l
            gx, gy = m[0], m[1]
            gaze_ok = True
        else:
            gx, gy, gaze_ok = 0.0, 0.0, False

        x = _clamp(0.5 + gx * self.gaze_range_x, 0.0, 1.0)
        y = _clamp(0.5 + gy * self.gaze_range_y, 0.0, 1.0)

        # confidence: face size × eye state × head pose × gaze availability
        conf = 0.95
        if face_w > 1e-6:
            span = max(self.face_ref_width - self.face_min_width, 1e-6)
            size_score = _clamp((face_w - self.face_min_width) / span, 0.0, 1.0)
        else:
            size_score = 0.3  # no face oval → cannot judge distance
        conf *= size_score
        if eye_open_l and eye_open_r:
            conf *= 1.0
        elif eye_open_l or eye_open_r:
            conf *= 0.8   # one eye open — normal mid-blink, still usable
        else:
            conf *= 0.7   # both closed — expected during blinks, keep flow
        conf *= self._head_score(head_dx, head_dy)
        if not gaze_ok:
            conf *= 0.4
        conf = _clamp(conf, 0.0, 1.0)

        return GazeSample(x=x, y=y, confidence=conf, eye_open_l=eye_open_l,
                          eye_open_r=eye_open_r, ear_l=ear_l, ear_r=ear_r,
                          head_dx=head_dx, head_dy=head_dy, timestamp=ts)


# ─────────────────────────────────────────────────────────────────────────────
# BlinkClassifier
# ─────────────────────────────────────────────────────────────────────────────


class _ClosureEpisode:
    """One continuous any-eye-closed episode (both eyes usually involved)."""

    __slots__ = ("onset", "l_onset", "r_onset", "l_any", "r_any", "reported")

    def __init__(self, onset: float):
        self.onset = onset
        self.l_onset: Optional[float] = None
        self.r_onset: Optional[float] = None
        self.l_any = False
        self.r_any = False
        self.reported = False


class BlinkClassifier:
    """Per-tick eye states → blink / long-blink / double-blink / wink events.

    Timing model (all tunable via config, ``blink_`` namespace):
        min_closed_s   (0.04) closure must last at least this to count
        blink_max_s    (0.40) upper bound of a "normal" blink
        long_blink_s   (0.60) closures longer than this are intentional
        double_window_s(0.50) a normal blink within this window of the
                              previous one becomes DOUBLE_BLINK
        wink_min_s     (0.35) one-eye closure ≥ this with the other eye open
                              throughout → WINK_LEFT / WINK_RIGHT
        min_confidence (0.50) ticks below this are DISCARDED (state resets)
                              — no event can ever be produced from a
                              low-confidence sample.

    Semantics:
        * ONE event per closure episode.  Both eyes participating →
          BLINK (≤ blink_max_s), LONG_BLINK (> long_blink_s, emitted early
          while still closed for responsiveness).  Only one eye ever closed
          → WINK_* when ≥ wink_min_s, else a plain BLINK.
        * LONG_BLINK / wink closures never arm the double-blink chain;
          only normal blinks do.
        * WINK_LEFT means the LEFT eye closed while the right stayed open.
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.min_closed_s = float(cfg.get("blink_min_closed_s", 0.04))
        self.blink_max_s = float(cfg.get("blink_max_s", 0.40))
        self.long_blink_s = float(cfg.get("blink_long_s", 0.60))
        self.double_window_s = float(cfg.get("blink_double_window_s", 0.50))
        self.wink_min_s = float(cfg.get("blink_wink_min_s", 0.35))
        self.min_confidence = float(cfg.get("blink_min_confidence", 0.50))
        self._episode: Optional[_ClosureEpisode] = None
        self._last_blink_end = -1e9
        self._t_prev: Optional[float] = None
        self._frame_count = 0
        self.history: "deque[Tuple[float, GazeEventKind]]" = deque(maxlen=64)

    def _frame_time(self) -> float:
        """Deterministic fallback clock (30 fps) when no timestamp given."""
        self._frame_count += 1
        return self._frame_count / 30.0

    def reset(self) -> None:
        """Forget any in-progress closure (e.g. after face loss)."""
        self._episode = None

    def update(self, eye_open_l: bool, eye_open_r: bool, confidence: float,
               timestamp: Optional[float] = None) -> List[GazeEventKind]:
        """Feed one tick; returns the events produced by this tick (0 or 1)."""
        if timestamp is not None:
            t = float(timestamp)
        elif self._t_prev is not None:
            t = self._frame_time()
        else:
            t = self._frame_time()
        self._t_prev = t

        if confidence < self.min_confidence:
            # Never emit from low-confidence data; drop partial state.
            self._episode = None
            return []

        l_closed = not eye_open_l
        r_closed = not eye_open_r
        events: List[GazeEventKind] = []

        if l_closed or r_closed:
            ep = self._episode
            if ep is None:
                ep = _ClosureEpisode(onset=t)
                self._episode = ep
            if l_closed:
                ep.l_any = True
                if ep.l_onset is None:
                    ep.l_onset = t
            if r_closed:
                ep.r_any = True
                if ep.r_onset is None:
                    ep.r_onset = t
            # Early LONG_BLINK while both eyes are still closed.
            if (l_closed and r_closed and not ep.reported
                    and (t - ep.onset) > self.long_blink_s):
                events.append(GazeEventKind.LONG_BLINK)
                ep.reported = True
                self.history.append((t, GazeEventKind.LONG_BLINK))
        else:
            ep = self._episode
            if ep is not None:
                self._episode = None
                if not ep.reported:
                    events.extend(self._classify_reopen(ep, t))
        return events

    def _classify_reopen(self, ep: _ClosureEpisode, t: float) -> List[GazeEventKind]:
        """Emit the single event for a finished closure episode."""
        dur = t - ep.onset
        out: List[GazeEventKind] = []
        if ep.l_any and ep.r_any:
            # two-eye closure → blink / long blink
            if dur > self.long_blink_s:
                out.append(GazeEventKind.LONG_BLINK)
            elif dur >= self.min_closed_s:
                if (t - self._last_blink_end) <= self.double_window_s:
                    out.append(GazeEventKind.DOUBLE_BLINK)
                else:
                    out.append(GazeEventKind.BLINK)
                self._last_blink_end = t
        else:
            # single-eye closure → wink when held long enough
            onset = ep.l_onset if ep.l_any else ep.r_onset
            one_dur = (t - onset) if onset is not None else dur
            if one_dur >= self.wink_min_s:
                out.append(GazeEventKind.WINK_LEFT if ep.l_any
                           else GazeEventKind.WINK_RIGHT)
            elif one_dur >= self.min_closed_s:
                out.append(GazeEventKind.BLINK)
        for e in out:
            self.history.append((t, e))
        return out


# ─────────────────────────────────────────────────────────────────────────────
# DwellDetector
# ─────────────────────────────────────────────────────────────────────────────


class DwellDetector:
    """Filtered gaze points → fixation + dwell events.

    A fixation starts when the gaze velocity (EMA-smoothed, normalized
    units/s) stays below ``dwell_fix_velocity`` for ``dwell_fix_min_s``
    (FIXATION_START emitted once).  While fixated, when the point remains
    within ``dwell_radius`` of the fixation anchor for ``dwell_time_s`` a
    DWELL event fires ONCE.  DWELL re-arms only after the point leaves the
    dwell radius (slow drift counts as leaving; the anchor then re-centres
    on the current point).  Fast motion (velocity ≥ threshold) or a
    confidence drop ends the fixation (FIXATION_END).

    Config keys (``dwell_`` namespace):
        dwell_fix_velocity (0.08) fixation velocity threshold, units/s
        dwell_fix_min_s    (0.15) stillness required before fixation starts
        dwell_radius       (0.045) dwell anchor radius, normalized units
        dwell_time_s       (0.80) time inside radius before DWELL fires
        dwell_min_confidence (0.30) below this the tracker state resets
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.fix_velocity_thresh = float(cfg.get("dwell_fix_velocity", 0.08))
        self.fix_min_s = float(cfg.get("dwell_fix_min_s", 0.15))
        self.dwell_radius = float(cfg.get("dwell_radius", 0.045))
        self.dwell_time_s = float(cfg.get("dwell_time_s", 0.80))
        self.min_confidence = float(cfg.get("dwell_min_confidence", 0.30))
        self.velocity: float = 0.0
        self._vel_ema_alpha = 0.3
        self._t_prev: Optional[float] = None
        self._t_last: Optional[float] = None
        self._frame_count = 0
        self._px: Optional[float] = None
        self._py: Optional[float] = None
        self._fixation = False
        self._fix_start: Optional[float] = None
        self._still_accum = 0.0
        self._anchor: Optional[Tuple[float, float]] = None
        self._dwell_start: Optional[float] = None
        self._dwell_fired = False

    def _frame_time(self) -> float:
        self._frame_count += 1
        return self._frame_count / 30.0

    def _hard_reset(self) -> None:
        """Drop all motion state (fixation over, dwell re-armed)."""
        self._fixation = False
        self._fix_start = None
        self._still_accum = 0.0
        self._anchor = None
        self._dwell_start = None
        self._dwell_fired = False
        self._px = None
        self._py = None
        self.velocity = 0.0

    def reset(self) -> None:
        """Full reset including clocks."""
        self._t_prev = None
        self._t_last = None
        self._frame_count = 0
        self._hard_reset()

    @property
    def fixation(self) -> bool:
        """True while a fixation is active."""
        return self._fixation

    @property
    def fixation_duration(self) -> float:
        """Seconds since the current fixation started (0 when not fixating)."""
        if self._fixation and self._fix_start is not None and self._t_prev is not None:
            return max(0.0, self._t_prev - self._fix_start)
        return 0.0

    @property
    def dwell_fired(self) -> bool:
        """True after DWELL fired for the current dwell (until re-armed)."""
        return self._dwell_fired

    def update(self, x: float, y: float, confidence: float,
               timestamp: Optional[float] = None) -> List[GazeEventKind]:
        """Feed one filtered gaze point; returns events produced this tick."""
        if timestamp is not None:
            t = float(timestamp)
        else:
            t = self._frame_time()
        self._t_prev = t
        events: List[GazeEventKind] = []

        try:
            fx, fy = float(x), float(y)
        except (TypeError, ValueError):
            fx, fy = float("nan"), float("nan")
        if confidence < self.min_confidence or not (math.isfinite(fx) and math.isfinite(fy)):
            if self._fixation:
                events.append(GazeEventKind.FIXATION_END)
            self._hard_reset()
            return events

        if self._t_last is not None and t > self._t_last:
            raw_dt = t - self._t_last
        else:
            raw_dt = _DT_DEFAULT
        dt = _clamp(raw_dt, _DT_MIN, _DT_MAX)
        self._t_last = t

        if self._px is not None:
            inst = math.hypot(fx - self._px, fy - self._py) / dt
            self.velocity += self._vel_ema_alpha * (inst - self.velocity)
        self._px, self._py = fx, fy

        still = self.velocity < self.fix_velocity_thresh
        if still:
            self._still_accum += dt
            if not self._fixation and self._still_accum >= self.fix_min_s:
                self._fixation = True
                self._fix_start = t - self._still_accum
                self._anchor = (fx, fy)
                self._dwell_start = t
                events.append(GazeEventKind.FIXATION_START)
        else:
            self._still_accum = 0.0
            if self._fixation:
                self._fixation = False
                self._dwell_fired = False
                self._anchor = None
                self._dwell_start = None
                events.append(GazeEventKind.FIXATION_END)

        if self._fixation:
            if self._anchor is None:
                self._anchor = (fx, fy)
                self._dwell_start = t
            d = math.hypot(fx - self._anchor[0], fy - self._anchor[1])
            if d <= self.dwell_radius:
                if self._dwell_start is None:
                    self._dwell_start = t
                if (not self._dwell_fired
                        and (t - self._dwell_start) >= self.dwell_time_s):
                    self._dwell_fired = True
                    events.append(GazeEventKind.DWELL)
            else:
                # Drifted out of the dwell radius: re-centre the anchor here,
                # restart the dwell timer and re-arm (DWELL may fire again).
                self._anchor = (fx, fy)
                self._dwell_start = t
                self._dwell_fired = False
        return events


# ─────────────────────────────────────────────────────────────────────────────
# GazeEngine
# ─────────────────────────────────────────────────────────────────────────────


class GazeEngine:
    """v6 gaze orchestrator: sense → estimate → filter → calibrate → events.

    ``update(frame_or_landmarks, timestamp)`` accepts
        * a raw BGR ``numpy`` frame  → run FaceMeshTracker first,
        * ``None``                   → face lost,
        * any landmark container     → skip sensing (simulation/tests).

    Returns a :class:`interfaces.GazeState` every tick:

        * ``x, y``            filtered normalized gaze point
        * ``screen_x/y``      pixel target when a calibration is fitted
                              (``screen_valid=True``), else 0/False
        * ``confidence``      raw estimator confidence
        * ``events``          FACE_FOUND/FACE_LOST + blink events + dwell
                              events, merged in that order
        * ``eye_open_l/r``    raw blink info — preserved even when the
                              action gate suppresses blink events

    Low-confidence action gate: when ``confidence < gaze_min_action_confidence``
    (default 0.55) DWELL and blink-action events are stripped from
    ``events`` (fixation/face bookkeeping events are kept).

    Config keys (``gaze_`` namespace; sub-components read their own
    ``fm_*/gf_*/blink_*/dwell_*/gc_*`` keys from the same dict):
        gaze_open_threshold   (0.18)  EAR open/closed threshold
        gaze_range_x/y        (0.35/0.30) gaze offset→frame mapping range
        gaze_head_yaw/pitch   (0.20/0.16) head-pose gating thresholds
        gaze_min_action_confidence (0.55) event suppression gate
        gaze_filter_enabled   (True)  attach GazeFilterPipeline
        gaze_calibration_path (None)  when set, load calibration from disk
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = dict(config or {})
        self.config = cfg
        self.tracker = FaceMeshTracker(cfg)
        self.estimator = GazeEstimator(cfg)
        self.blink = BlinkClassifier(cfg)
        self.dwell = DwellDetector(cfg)
        self.filter_pipeline: Optional[GazeFilterPipeline] = (
            GazeFilterPipeline(cfg) if bool(cfg.get("gaze_filter_enabled", True)) else None
        )
        cal_path = cfg.get("gaze_calibration_path")
        self._calibration = GazeCalibration(config=cfg, path=cal_path)
        if cal_path:
            self._calibration.load()
        self.min_action_confidence = float(cfg.get("gaze_min_action_confidence", 0.55))
        self._face_present = False
        self._frame_idx = 0
        self._last_x = 0.5
        self._last_y = 0.5
        self.last_state: Optional[GazeState] = None

    # -- calibration ---------------------------------------------------------

    @property
    def calibration(self) -> GazeCalibration:
        """The attached :class:`GazeCalibration` (always present, may be unfitted)."""
        return self._calibration

    def set_calibration(self, calibration: Optional[GazeCalibration]) -> None:
        """Attach a (possibly externally fitted) calibration."""
        if calibration is not None:
            self._calibration = calibration

    # -- helpers -------------------------------------------------------------

    def _next_ts(self) -> float:
        self._frame_idx += 1
        return self._frame_idx / 30.0

    def apply_filter_only(self, sample: GazeSample) -> GazeSample:
        """Run only the filter pipeline on a sample (no events, no mapping)."""
        if self.filter_pipeline is not None:
            return self.filter_pipeline.apply(sample)
        return replace(sample)

    # -- main entry ----------------------------------------------------------

    def update(self, frame_or_landmarks: Any = None,
               timestamp: Optional[float] = None) -> GazeState:
        """Advance the whole gaze pipeline by one tick; returns GazeState."""
        ts = float(timestamp) if timestamp is not None else self._next_ts()

        landmarks: Any = None
        if isinstance(frame_or_landmarks, np.ndarray):
            landmarks = self.tracker.process(frame_or_landmarks, ts)
        elif frame_or_landmarks is None:
            landmarks = None
        else:
            landmarks = frame_or_landmarks

        if landmarks is None:
            events: List[GazeEventKind] = []
            if self._face_present:
                events.append(GazeEventKind.FACE_LOST)
            self._face_present = False
            self.blink.reset()
            # confidence 0 → dwell detector breaks fixation cleanly
            events.extend(self.dwell.update(self._last_x, self._last_y, 0.0, ts))
            st = GazeState(x=self._last_x, y=self._last_y, screen_x=0.0,
                           screen_y=0.0, screen_valid=False, confidence=0.0,
                           fixation=False, fixation_duration=0.0,
                           dwell_fired=False, events=events, eye_open_l=True,
                           eye_open_r=True, timestamp=ts)
            self.last_state = st
            return st

        events = []
        if not self._face_present:
            events.append(GazeEventKind.FACE_FOUND)
        self._face_present = True

        raw = self.estimator.estimate(landmarks, ts)
        filtered = (self.filter_pipeline.apply(raw, ts)
                    if self.filter_pipeline is not None else raw)
        self._last_x, self._last_y = filtered.x, filtered.y

        events.extend(self.blink.update(raw.eye_open_l, raw.eye_open_r,
                                        raw.confidence, ts))
        events.extend(self.dwell.update(filtered.x, filtered.y,
                                        raw.confidence, ts))

        if raw.confidence < self.min_action_confidence:
            events = [e for e in events if e not in _SUPPRESSED_LOW_CONF]

        sx = sy = 0.0
        screen_valid = False
        mapped = self._calibration.map(filtered.x, filtered.y)
        if mapped is not None:
            sx, sy = mapped
            screen_valid = True

        st = GazeState(x=filtered.x, y=filtered.y, screen_x=sx, screen_y=sy,
                       screen_valid=screen_valid, confidence=raw.confidence,
                       fixation=self.dwell.fixation,
                       fixation_duration=self.dwell.fixation_duration,
                       dwell_fired=self.dwell.dwell_fired, events=events,
                       eye_open_l=raw.eye_open_l, eye_open_r=raw.eye_open_r,
                       timestamp=ts)
        self.last_state = st
        return st
