"""Tests for airmouse.gaze_filter (v6): outlier rejection, adaptive smoothing,
saccade snap, statistics.  Strictly deterministic — fixed seeds, scripted
timestamps, no hardware."""

from __future__ import annotations

import math

import numpy as np
import pytest

from airmouse.gaze_filter import GazeFilterPipeline
from airmouse.interfaces import GazeSample


def feed(pipe: GazeFilterPipeline, xs, ys, conf=0.9, fps=30.0):
    """Feed parallel sequences; returns list of (x, y) filtered outputs."""
    out = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        s = GazeSample(x=float(x), y=float(y), confidence=float(conf),
                       timestamp=i / fps)
        o = pipe.apply(s)
        out.append((o.x, o.y))
    return out


class TestJitterReduction:
    def test_noisy_fixation_smoothed_at_least_2x(self):
        # Constant target + gaussian noise (seed 42): filtered std must be
        # at least 2x smaller than raw std on BOTH axes.
        rng = np.random.default_rng(42)
        n = 300
        noise = rng.normal(0.0, 0.015, size=(n, 2))
        xs = 0.5 + noise[:, 0]
        ys = 0.5 + noise[:, 1]
        pipe = GazeFilterPipeline()
        out = feed(pipe, xs, ys, conf=0.9)
        raw_std_x = float(np.std(xs))
        raw_std_y = float(np.std(ys))
        filt_x = np.array([p[0] for p in out])
        filt_y = np.array([p[1] for p in out])
        fx = raw_std_x / float(np.std(filt_x))
        fy = raw_std_y / float(np.std(filt_y))
        assert fx >= 2.0, f"x-axis jitter reduction only {fx:.2f}x"
        assert fy >= 2.0, f"y-axis jitter reduction only {fy:.2f}x"
        # and the mean stays on target
        assert abs(float(np.mean(filt_x)) - 0.5) < 0.01
        assert abs(float(np.mean(filt_y)) - 0.5) < 0.01


class TestOutlierRejection:
    def test_spike_rejected_output_stable(self):
        # Settle at 0.5 with sub-gate confidence, inject one huge spike:
        # output must move < 0.02 from its pre-spike value.
        pipe = GazeFilterPipeline()
        n_settle = 30
        out = feed(pipe, [0.5] * n_settle, [0.5] * n_settle, conf=0.7)
        pre = out[-1]
        spike = pipe.apply(GazeSample(x=0.95, y=0.5, confidence=0.7,
                                      timestamp=n_settle / 30.0))
        assert abs(spike.x - pre[0]) < 0.02
        assert abs(spike.y - pre[1]) < 0.02
        assert pipe.stats["rejected_count"] == 1

    def test_high_confidence_jump_bypasses_rejection(self):
        # Documented behaviour: confidence >= gf_high_confidence accepts the
        # jump (deliberate glance) — no rejection counted, output moves.
        pipe = GazeFilterPipeline()
        feed(pipe, [0.5] * 10, [0.5] * 10, conf=0.9)
        before = pipe.stats["rejected_count"]
        o = pipe.apply(GazeSample(x=0.9, y=0.5, confidence=0.95, timestamp=10 / 30.0))
        assert pipe.stats["rejected_count"] == before
        assert o.x > 0.55  # moved toward the confident new point

    def test_non_finite_held(self):
        pipe = GazeFilterPipeline()
        feed(pipe, [0.5] * 5, [0.5] * 5, conf=0.9)
        o = pipe.apply(GazeSample(x=float("nan"), y=0.5, confidence=0.9,
                                  timestamp=5 / 30.0))
        assert o.x == pytest.approx(0.5, abs=1e-9)
        assert pipe.stats["rejected_count"] == 1


class TestResponsiveness:
    def test_ramp_lag_bounded(self):
        # 0.5 units/s ramp: steady-state lag must stay ≈ 2 frames worth.
        speed = 0.5
        fps = 30.0
        dt = 1.0 / fps
        n = 60
        xs = [0.4 + speed * dt * i for i in range(n)]
        ys = [0.5] * n
        pipe = GazeFilterPipeline()
        out = feed(pipe, xs, ys, conf=0.9)
        # measure lag over the settled second half
        lags = [abs(out[i][0] - xs[i]) for i in range(n // 2, n)]
        max_lag = max(lags)
        assert max_lag <= 2.5 * speed * dt, f"lag {max_lag:.4f} > 2.5 frames"
        # raw passes through the same points — the filter really smoothed lag
        assert max_lag < 0.05

    def test_saccade_snap_fast(self):
        # Step of 0.2 (below gf_max_jump, above gf_snap_dist): the saccade
        # snap must reach the new target within ~3 frames.
        pipe = GazeFilterPipeline()
        out = feed(pipe, [0.3] * 30, [0.5] * 30, conf=0.9)
        xs = [0.5] * 5
        out2 = feed(pipe, xs, [0.5] * 5, conf=0.9)
        assert abs(out2[2][0] - 0.5) < 0.02, f"still lagging after 3 frames: {out2[2][0]:.4f}"
        assert abs(out2[-1][0] - 0.5) < 1e-9

    def test_velocity_and_alpha_exposed(self):
        pipe = GazeFilterPipeline()
        feed(pipe, [0.5] * 10, [0.5] * 10, conf=0.9)
        assert 0.0 < pipe.last_alpha <= 1.0
        assert pipe.velocity >= 0.0
        # fast motion raises the adaptive alpha
        a_still = pipe.last_alpha
        pipe2 = GazeFilterPipeline()
        feed(pipe2, [0.3 + 0.02 * i for i in range(40)], [0.5] * 40, conf=0.9)
        assert pipe2.last_alpha > a_still


class TestStats:
    def test_stats_counters_sane(self):
        rng = np.random.default_rng(7)
        n = 100
        xs = 0.5 + rng.normal(0, 0.01, n)
        ys = 0.5 + rng.normal(0, 0.01, n)
        pipe = GazeFilterPipeline()
        feed(pipe, xs, ys, conf=0.9)
        st = pipe.stats
        assert st["samples"] == n
        assert st["accepted_count"] == n
        assert st["rejected_count"] == 0
        assert st["filtered_jitter"] < st["raw_jitter"]  # smoothing visible
        assert st["lag_ms"] > 0.0 and math.isfinite(st["lag_ms"])

    def test_reset_stats_keeps_filter(self):
        pipe = GazeFilterPipeline()
        feed(pipe, [0.5] * 20, [0.5] * 20, conf=0.9)
        pipe.reset_stats()
        assert pipe.stats["samples"] == 0
        assert pipe.stats["raw_jitter"] == 0.0
        assert pipe.stats["rejected_count"] == 0
        # filter state preserved: next output continues smoothly at 0.5
        o = pipe.apply(GazeSample(x=0.5, y=0.5, confidence=0.9, timestamp=1.0))
        assert o.x == pytest.approx(0.5, abs=1e-9)

    def test_reset_full(self):
        pipe = GazeFilterPipeline()
        feed(pipe, [0.7] * 20, [0.3] * 20, conf=0.9)
        pipe.reset()
        o = pipe.apply(GazeSample(x=0.2, y=0.8, confidence=0.9, timestamp=0.0))
        # after reset the first sample snaps straight to the input
        assert o.x == pytest.approx(0.2, abs=1e-12)
        assert o.y == pytest.approx(0.8, abs=1e-12)

    def test_scalar_filter_alias(self):
        pipe = GazeFilterPipeline()
        x, y = pipe.filter(0.5, 0.5, confidence=0.9, timestamp=0.0)
        assert x == pytest.approx(0.5, abs=1e-12)
        assert y == pytest.approx(0.5, abs=1e-12)

    def test_does_not_mutate_input(self):
        pipe = GazeFilterPipeline()
        s = GazeSample(x=0.8, y=0.2, confidence=0.9, timestamp=0.0)
        pipe.apply(s)
        assert s.x == 0.8 and s.y == 0.2
