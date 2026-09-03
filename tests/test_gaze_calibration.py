"""Tests for airmouse.gaze_calibration (v6): workflow, affine fit, quality,
persistence.  Deterministic; persistence uses ONLY tmp_path — the real
~/.airmouse directory is never touched."""

from __future__ import annotations

import json

import numpy as np
import pytest

from airmouse.gaze_calibration import GazeCalibration, run_point_calibration
from airmouse.interfaces import GazeSample

# Ground-truth affine model used across the fit tests:
#   screen_px = M @ [gx, gy, 1]  (deliberately non-axis-aligned)
M = np.array([[1800.0, 40.0, 80.0],
              [60.0, 950.0, 40.0]])
NOISE_SIGMA = 0.0015   # gaze-space noise → ~3 px residual at 1920×1080


def _sampler(rng):
    """Inverse-model sampler: for a target (normalized screen) return the
    GazeSample the eye would produce so that M @ gaze lands on the target."""
    Minv = np.linalg.inv(M[:2, :2])
    b = M[:, 2]

    def fn(target):
        tx, ty = target
        px = np.array([tx * 1920.0, ty * 1080.0])
        g = Minv @ (px - b)
        g = g + rng.normal(0.0, NOISE_SIGMA, size=2)
        return GazeSample(x=float(g[0]), y=float(g[1]), confidence=0.95,
                          timestamp=0.0)
    return fn


def _fit(rng=None, outlier=False, n_per_point=12):
    cal = GazeCalibration(n_points=9, path="/tmp/unused-gaze-cal.json")
    fn = _sampler(rng or np.random.default_rng(42))
    pts = cal.begin()
    for t in pts:
        for _ in range(n_per_point):
            cal.add_sample(t, fn(t))
    if outlier:
        # one grossly wrong sample on the first target (+0.3 in gx)
        bad = GazeSample(x=pts[0][0] + 0.30, y=pts[0][1], confidence=0.95,
                         timestamp=99.0)
        cal.add_sample(pts[0], bad)
    q = cal.finish(screen_w=1920, screen_h=1080)
    return cal, q


class TestGrid:
    def test_begin_9_point_grid(self):
        cal = GazeCalibration(n_points=9)
        pts = cal.begin()
        assert len(pts) == 9
        m = cal.margin
        xs = sorted({p[0] for p in pts})
        ys = sorted({p[1] for p in pts})
        assert xs == pytest.approx([m, 0.5, 1 - m])
        assert ys == pytest.approx([m, 0.5, 1 - m])
        assert (0.5, 0.5) in pts

    def test_begin_5_point_cross(self):
        cal = GazeCalibration(n_points=5)
        pts = cal.begin()
        assert len(pts) == 5
        m = cal.margin
        assert (0.5, 0.5) in pts
        assert (m, 0.5) in pts and (1 - m, 0.5) in pts
        assert (0.5, m) in pts and (0.5, 1 - m) in pts

    def test_samples_collected(self):
        cal = GazeCalibration(n_points=5, path="/tmp/unused-gaze-cal.json")
        cal.begin()
        s = GazeSample(x=0.5, y=0.5, confidence=0.9, timestamp=0.0)
        cal.add_sample((0.5, 0.5), s)
        cal.add_sample((0.5, 0.5), s)
        assert cal.samples_collected == 2


class TestFitQuality:
    def test_good_fit_and_residuals(self):
        cal, q = _fit()
        assert q["status"] == "good"
        assert q["mean_residual_px"] < 10.0     # ~3 px for this noise level
        assert q["max_residual_px"] < 25.0
        assert q["samples_used"] >= 100
        assert len(q["per_target_residuals"]) == 9
        assert cal.is_calibrated and cal.is_reliable("fair")
        assert cal.is_reliable("good")

    def test_affine_matrix_recovered(self):
        cal, q = _fit()
        A = cal._matrix
        assert A is not None and A.shape == (2, 3)
        assert np.allclose(A, M, atol=2.0)      # lstsq recovers the model
        # (tolerance matches the ~0.8 statistical recovery error of the
        #  affine entries given NOISE_SIGMA gaze noise over 108 samples)

    def test_map_roundtrip_heldout(self):
        cal, _ = _fit()
        g = (0.37, 0.61)                        # NOT a grid target
        px = cal.map(*g)
        truth = M @ np.array([g[0], g[1], 1.0])
        assert px is not None
        assert px[0] == pytest.approx(float(truth[0]), abs=15.0)
        assert px[1] == pytest.approx(float(truth[1]), abs=15.0)

    def test_map_clamps_to_screen(self):
        cal, _ = _fit()
        far = cal.map(-5.0, 5.0)
        assert far == (0.0, 1080.0)

    def test_map_normalized(self):
        cal, _ = _fit()
        nx, ny = cal.map_normalized(0.37, 0.61)
        truth = M @ np.array([0.37, 0.61, 1.0])
        assert nx == pytest.approx(truth[0] / 1920.0, abs=0.01)
        assert ny == pytest.approx(truth[1] / 1080.0, abs=0.01)
        assert 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0

    def test_map_before_calibration_none(self):
        cal = GazeCalibration(path="/tmp/unused-gaze-cal.json")
        assert cal.map(0.5, 0.5) is None
        assert cal.map_normalized(0.5, 0.5) is None
        assert not cal.is_reliable()

    def test_outlier_rejected(self):
        cal, q = _fit(outlier=True)
        assert q["samples_rejected"] >= 1       # the +0.3 sample was dropped
        assert q["status"] == "good"            # and the fit stayed clean
        assert q["mean_residual_px"] < 10.0

    def test_incomplete_when_target_starved(self):
        cal = GazeCalibration(n_points=9, path="/tmp/unused-gaze-cal.json")
        fn = _sampler(np.random.default_rng(5))
        pts = cal.begin()
        for t in pts:
            n = 2 if t == pts[4] else 8        # one target gets < min_kept
            for _ in range(n):
                cal.add_sample(t, fn(t))
        q = cal.finish()
        assert q["status"] == "incomplete"
        assert not cal.is_reliable("fair")
        assert q["samples_used"] > 0            # the rest still fitted best-effort

    def test_empty_finish_is_incomplete(self):
        cal = GazeCalibration(path="/tmp/unused-gaze-cal.json")
        q = cal.finish()
        assert q["status"] == "incomplete"
        assert q["samples_used"] == 0
        assert cal.map(0.5, 0.5) is None


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        p = tmp_path / "gaze_cal.json"
        cal, _ = _fit()
        assert cal.save(path=str(p)) is True
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["version"] == 2
        assert data["n_points"] == 9

        cal2 = GazeCalibration(path=str(p))
        assert cal2.load() is True
        assert np.allclose(cal2._matrix, cal._matrix)
        assert cal2.map(0.37, 0.61) == cal.map(0.37, 0.61)
        assert cal2.is_reliable("fair")

    def test_corrupt_json_load_false(self, tmp_path):
        p = tmp_path / "broken.json"
        p.write_text("{ this is not json {{{")
        cal = GazeCalibration(path=str(p))
        assert cal.load() is False
        assert cal.map(0.5, 0.5) is None

    def test_missing_file_load_false(self, tmp_path):
        cal = GazeCalibration(path=str(tmp_path / "nope.json"))
        assert cal.load() is False

    def test_wrong_version_load_false(self, tmp_path):
        p = tmp_path / "v1.json"
        p.write_text(json.dumps({"version": 1, "matrix": [[1, 0, 0], [0, 1, 0]]}))
        cal = GazeCalibration(path=str(p))
        assert cal.load() is False

    def test_save_without_fit_false(self, tmp_path):
        cal = GazeCalibration(path=str(tmp_path / "never.json"))
        assert cal.save() is False

    def test_reset_clears(self, tmp_path):
        cal, _ = _fit()
        assert cal.is_calibrated
        cal.reset()
        assert not cal.is_calibrated
        assert cal.samples_collected == 0
        assert cal.map(0.5, 0.5) is None
        assert cal.quality == {}


class TestRunPointCalibration:
    def test_helper_drives_full_session(self):
        cal = GazeCalibration(n_points=9, path="/tmp/unused-gaze-cal.json")
        q = run_point_calibration(cal, _sampler(np.random.default_rng(11)),
                                  samples_per_point=10)
        assert q["status"] == "good"
        assert q["samples_used"] == 90
        assert cal.is_reliable("fair")

    def test_helper_explicit_points(self):
        cal = GazeCalibration(n_points=5, path="/tmp/unused-gaze-cal.json")
        pts = [(0.1, 0.1), (0.9, 0.1), (0.5, 0.5), (0.1, 0.9), (0.9, 0.9)]
        q = run_point_calibration(cal, _sampler(np.random.default_rng(12)),
                                  points=pts, samples_per_point=6)
        assert q["status"] == "good"
        assert cal.samples_collected == 30
