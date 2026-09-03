"""
airmouse.gaze_filter — v6 gaze filtering pipeline.

Takes the raw per-tick :class:`interfaces.GazeSample` stream from
``airmouse.gaze.GazeEstimator`` and produces a smooth, sane, low-latency
gaze point suitable for dwell detection and cursor mapping.

Pipeline stages (per sample):

1.  OUTLIER REJECTION — a sample that jumps more than ``gf_max_jump``
    (normalized units) from the last accepted point is treated as noise and
    replaced by the last accepted point (the output "holds"), UNLESS the
    sample's confidence ≥ ``gf_high_confidence`` (a confident jump is
    accepted as a deliberate glance).  Non-finite input is always rejected.

2.  CONFIDENCE- AND VELOCITY-ADAPTIVE EMA SMOOTHING —
    ``alpha = alpha_base * (0.5 + 0.5*confidence) + vel_boost * speed_factor``
    clamped to [``gf_alpha_min``, 1.0].
    * confident + still  → small alpha → strong smoothing (jitter lock),
    * low confidence     → alpha scales down (trust the model, not the noise),
    * fast motion        → ``speed_factor`` (from the FILTERED velocity
      channel, NOT the raw delta — raw deltas have a noise floor that would
      keep alpha pinned high; same lesson as the v5 Kalman fusion) lifts
      alpha → responsive, bounded lag.

3.  SACCADE SNAP — if the raw-vs-filtered distance exceeds ``gf_snap_dist``
    for ``gf_snap_frames`` consecutive accepted frames the filter snaps to
    the raw point (a real glance/saccade must not lag behind).  This keeps
    step responses at ~2 frames while noise (a few sigma at most, never two
    consecutive frames beyond the snap distance) never triggers it.

Quantitative hooks for tests/HUD: ``stats`` dict (raw_jitter,
filtered_jitter, lag_ms, rejected_count, accepted_count, samples),
``reset_stats()``, exposed ``velocity`` (filtered velocity EMA, units/s)
and ``last_alpha``.

The pipeline is STRICTLY deterministic and headless: no wall-clock reads,
no hardware, numpy-free core (pure ``math``) — the same input sequence
always yields the same output sequence.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import math
from typing import Dict, Optional

from .interfaces import GazeSample, now_ts

__all__ = ["GazeFilterPipeline"]

_DT_DEFAULT = 1.0 / 30.0
_DT_MIN = 1e-3
_DT_MAX = 0.5
_VEL_EMA_ALPHA = 0.25  # velocity EMA responsiveness


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


class GazeFilterPipeline:
    """Filter chain for the gaze point (see module docstring).

    Config keys (``gf_`` namespace, all optional):
        gf_max_jump        (0.25) outlier jump threshold, normalized units
        gf_high_confidence (0.85) confidence that overrides rejection
        gf_alpha_base      (0.22) EMA alpha at full confidence, zero speed
        gf_vel_boost       (0.30) alpha added at full speed_factor
        gf_speed_ref       (1.0)  filtered speed (units/s) → speed_factor 1
        gf_alpha_min       (0.08) alpha floor (never fully freeze)
        gf_snap_dist       (0.12) raw-vs-filtered distance that starts the
                                  saccade-snap streak
        gf_snap_frames     (2)    consecutive far frames before snapping
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.max_jump = float(cfg.get("gf_max_jump", 0.25))
        self.high_confidence = float(cfg.get("gf_high_confidence", 0.85))
        self.alpha_base = float(cfg.get("gf_alpha_base", 0.22))
        self.vel_boost = float(cfg.get("gf_vel_boost", 0.30))
        self.speed_ref = float(cfg.get("gf_speed_ref", 1.0))
        self.alpha_min = float(cfg.get("gf_alpha_min", 0.08))
        self.snap_dist = float(cfg.get("gf_snap_dist", 0.12))
        self.snap_frames = max(1, int(cfg.get("gf_snap_frames", 2)))

        # filter state
        self._x: Optional[float] = None
        self._y: Optional[float] = None
        self._gx: Optional[float] = None   # last good (accepted) input
        self._gy: Optional[float] = None
        self._px: Optional[float] = None   # previous accepted input
        self._py: Optional[float] = None
        self._fout_x: Optional[float] = None  # previous filtered output
        self._fout_y: Optional[float] = None
        self._vel: float = 0.0
        self._t_prev: Optional[float] = None
        self._frame_count = 0
        self._streak = 0

        # exposed diagnostics
        self.velocity: float = 0.0
        self.last_alpha: float = 0.0
        self.stats: Dict[str, float] = {
            "raw_jitter": 0.0,       # mean |Δ raw| per frame (2D magnitude)
            "filtered_jitter": 0.0,  # mean |Δ filtered| per frame
            "lag_ms": 0.0,           # EMA of theoretical EMA group delay
            "rejected_count": 0,
            "accepted_count": 0,
            "samples": 0,
        }
        self._raw_sum = 0.0
        self._filt_sum = 0.0
        self._lag_sum = 0.0
        self._stat_n = 0
        self._pair_n = 0
        self._prev_raw: Optional[tuple] = None
        self._prev_out: Optional[tuple] = None

    # ── timing ────────────────────────────────────────────────────────────

    def _resolve_dt(self, ts: Optional[float]) -> float:
        """Deterministic dt: explicit increasing timestamps win, else a
        fixed 30 fps frame clock (keeps simulations reproducible)."""
        if ts is not None and self._t_prev is not None and ts > self._t_prev:
            dt = ts - self._t_prev
            self._t_prev = ts
            return _clamp(dt, _DT_MIN, _DT_MAX)
        self._t_prev = ts
        self._frame_count += 1
        return _DT_DEFAULT

    # ── stats ─────────────────────────────────────────────────────────────

    def _publish_stats(self) -> None:
        n = max(self._stat_n, 1)
        self.stats["raw_jitter"] = self._raw_sum / max(self._pair_n, 1)
        self.stats["filtered_jitter"] = self._filt_sum / max(self._pair_n, 1)
        self.stats["lag_ms"] = self._lag_sum / n
        self.stats["samples"] = self._stat_n

    def reset_stats(self) -> None:
        """Zero the statistics accumulators (filter state is preserved)."""
        self.stats = {
            "raw_jitter": 0.0,
            "filtered_jitter": 0.0,
            "lag_ms": 0.0,
            "rejected_count": 0,
            "accepted_count": 0,
            "samples": 0,
        }
        self._raw_sum = self._filt_sum = self._lag_sum = 0.0
        self._stat_n = self._pair_n = 0
        self._prev_raw = self._prev_out = None

    def reset(self) -> None:
        """Forget all filter state AND statistics (fresh-start)."""
        self._x = self._y = None
        self._gx = self._gy = None
        self._px = self._py = None
        self._fout_x = self._fout_y = None
        self._vel = 0.0
        self.velocity = 0.0
        self.last_alpha = 0.0
        self._t_prev = None
        self._frame_count = 0
        self._streak = 0
        self.reset_stats()

    # ── main entry ────────────────────────────────────────────────────────

    def apply(self, sample: GazeSample, timestamp: Optional[float] = None) -> GazeSample:
        """Filter one sample; returns a NEW GazeSample with smoothed x/y.

        Confidence and eye flags are passed through untouched.  Never
        raises on bad input — garbage is rejected and the output holds.
        """
        ts = float(timestamp) if timestamp is not None else sample.timestamp
        dt = self._resolve_dt(ts)
        try:
            conf = _clamp(float(sample.confidence), 0.0, 1.0)
        except (TypeError, ValueError):
            conf = 0.0

        # ── stage 1: outlier rejection (keep last-good on jump) ──────────
        try:
            x = float(sample.x)
            y = float(sample.y)
        except (TypeError, ValueError):
            x = y = float("nan")
        if not (math.isfinite(x) and math.isfinite(y)):
            self.stats["rejected_count"] += 1
            if self._gx is None:
                x = y = 0.5
            else:
                x, y = self._gx, self._gy
        elif self._gx is None:
            self._gx, self._gy = x, y
        else:
            jump = math.hypot(x - self._gx, y - self._gy)
            if jump > self.max_jump and conf < self.high_confidence:
                self.stats["rejected_count"] += 1
                x, y = self._gx, self._gy  # hold last good value
            else:
                self._gx, self._gy = x, y

        # ── stage 2: adaptive-confidence EMA with saccade snap ───────────
        sf = _clamp(self._vel / self.speed_ref, 0.0, 1.0) if self.speed_ref > 0 else 1.0
        alpha = _clamp(self.alpha_base * (0.5 + 0.5 * conf) + self.vel_boost * sf,
                       self.alpha_min, 1.0)
        self.last_alpha = alpha
        # saccade-snap streak uses the PRE-update residual: a real glance
        # stays far from the filter for ≥ snap_frames consecutive frames,
        # while noise (≤ ~3σ, never twice in a row) never arms it.
        if self._x is not None:
            far = math.hypot(x - self._x, y - self._y) > self.snap_dist
            self._streak = self._streak + 1 if far else 0
        else:
            self._streak = 0
        if self._x is None:
            # snap to the very first accepted point (no cold-start glide)
            self._x, self._y = x, y
        elif self._streak >= self.snap_frames:
            self._x, self._y = x, y          # saccade: jump to the raw point
            self._streak = 0
        else:
            self._x += alpha * (x - self._x)
            self._y += alpha * (y - self._y)

        # velocity from the FILTERED output channel (noise-free-ish; using
        # raw deltas would put a noise floor on "speed" and keep alpha high)
        if self._fout_x is not None:
            inst = math.hypot(self._x - self._fout_x, self._y - self._fout_y) / dt
            self._vel += _VEL_EMA_ALPHA * (inst - self._vel)
        self._fout_x, self._fout_y = self._x, self._y
        self.velocity = self._vel

        # ── stage 3: statistics ──────────────────────────────────────────
        if self._prev_raw is not None:
            self._raw_sum += math.hypot(x - self._prev_raw[0], y - self._prev_raw[1])
            self._pair_n += 1
        if self._prev_out is not None:
            self._filt_sum += math.hypot(self._x - self._prev_out[0],
                                         self._y - self._prev_out[1])
        self._lag_sum += ((1.0 - alpha) / alpha) * dt * 1000.0
        self._stat_n += 1
        self.stats["accepted_count"] += 1
        self._prev_raw = (x, y)
        self._prev_out = (self._x, self._y)
        self._publish_stats()

        return GazeSample(x=self._x, y=self._y, confidence=sample.confidence,
                          eye_open_l=sample.eye_open_l, eye_open_r=sample.eye_open_r,
                          ear_l=sample.ear_l, ear_r=sample.ear_r,
                          head_dx=sample.head_dx, head_dy=sample.head_dy,
                          timestamp=sample.timestamp)

    # alias mirroring the v5 house style (HybridOneEuroKalman.filter_np)
    def filter(self, x: float, y: float, confidence: float = 1.0,
               timestamp: Optional[float] = None) -> tuple:
        """Convenience scalar API: filter a bare (x, y) point."""
        kwargs: Dict[str, float] = {"x": float(x), "y": float(y),
                                    "confidence": float(confidence)}
        if timestamp is not None:
            kwargs["timestamp"] = float(timestamp)
        out = self.apply(GazeSample(**kwargs), timestamp)
        return out.x, out.y
