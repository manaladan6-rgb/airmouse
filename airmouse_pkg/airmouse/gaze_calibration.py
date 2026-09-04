"""
airmouse.gaze_calibration — v6 gaze→screen calibration workflow + persistence.

Maps raw gaze coordinates (normalized frame space, see interfaces.GazeSample)
to desktop PIXELS via a per-user affine fit collected from a target grid.

Workflow::

    gc = GazeCalibration(n_points=9, path="~/.airmouse/gaze_calibration.json")
    targets = gc.begin()                    # normalized screen grid to look at
    for t in targets:
        for _ in range(12):
            gc.add_sample(t, gaze_sample_from_estimator_while_looking_at_t)
    quality = gc.finish(screen_w=1920, screen_h=1080)
    px = gc.map(gx, gy)                     # (x, y) pixels or None

    gc.save() / gc.load() / gc.reset()      # persistence (atomic JSON v2)

Fitting
-------
Per collected target the samples are cleaned with a median + MAD outlier
gate (drop |v − median| > 2.5·1.4826·MAD on either axis; fewer than
``gc_min_kept`` surviving samples marks the target incomplete).  Surviving
(target_px, gaze) pairs feed a least-squares AFFINE fit::

    [sx, sy]ᵀ = A @ [gx, gy, 1]ᵀ        (A is 2×3, numpy lstsq)

Quality: mean/max residual in PIXELS over all kept samples,
per-target mean residuals, and a status of

    good | fair | poor      by mean residual (≤ 40 px / ≤ 100 px / worse)
    incomplete              any target below min kept samples, or the fit
                            itself was impossible / degenerate

``is_reliable(min_quality="fair")`` gates downstream automation.

Persistence: JSON v2 (version, created, n_points, matrix, quality, screen
size) written ATOMICALLY (tmp file + ``os.replace``).  ``load()`` returns
False on ANY problem (missing file, corrupt JSON, wrong version) — it never
raises.  The default path is ``~/.airmouse/gaze_calibration.json`` but the
constructor accepts ``path=``; tests MUST pass a tmp path and never touch
the real HOME directory.

Helper: :func:`run_point_calibration` drives a full synthetic calibration
from a sampler callable (used by tests and the guided setup flow).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .interfaces import GazeSample

__all__ = ["GazeCalibration", "run_point_calibration", "GAZE_CALIBRATION_PATH"]

GAZE_CALIBRATION_PATH = os.path.join(os.path.expanduser("~"), ".airmouse",
                                     "gaze_calibration.json")

#: import-time default, kept so a runtime override of GAZE_CALIBRATION_PATH
#: (tests / embedders) can be detected by :func:`_default_gaze_path`
_DEFAULT_GAZE_PATH = GAZE_CALIBRATION_PATH


def _default_gaze_path() -> str:
    """ACTIVE default gaze-calibration file path (dynamic; one resolution).

    An explicit runtime override of the module constant
    ``GAZE_CALIBRATION_PATH`` (≠ the import-time default) still wins,
    otherwise the authoritative
    ``airmouse.paths.gaze_calibration_file()`` is used — so
    ``$AIRMOUSE_HOME`` set after import is always honored.
    """
    override = globals().get("GAZE_CALIBRATION_PATH")
    try:
        if override and os.path.abspath(str(override)) != \
                os.path.abspath(_DEFAULT_GAZE_PATH):
            return str(override)
    except Exception:
        pass
    try:
        from . import paths
        return paths.gaze_calibration_file()
    except Exception:
        return _DEFAULT_GAZE_PATH

_TARGET_SNAP_TOL = 0.05   # max distance for add_sample() to snap to grid
_MIN_FIT_POINTS = 4       # minimum kept samples for a meaningful affine fit
_GOOD_RESIDUAL_PX = 40.0
_FAIR_RESIDUAL_PX = 100.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


class GazeCalibration:
    """Gaze→screen affine calibration with persistence (see module docs).

    Args:
        n_points: grid size — 9 (3×3), 5 (cross), 4 (corners), 1 (centre);
            other values fall back to a compact generic grid.
        config: optional dict, keys (``gc_`` namespace):
            gc_margin (0.08)        screen-edge inset for target grids
            gc_max_mad_sigma (2.5)  MAD outlier gate (in sigmas)
            gc_min_kept (3)         samples per target required
            gc_screen_w (1920), gc_screen_h (1080) — default screen size
        path: persistence path override (default: the active
            gaze-calibration path from ``airmouse.paths``).
            Pass a tmp path in tests.
    """

    QUALITY_ORDER: Dict[str, int] = {"incomplete": 0, "poor": 1, "fair": 2, "good": 3}

    def __init__(self, n_points: int = 9, config: Optional[dict] = None,
                 path: Optional[str] = None):
        cfg = config or {}
        self.n_points = max(1, int(n_points))
        self.margin = float(cfg.get("gc_margin", 0.08))
        self.max_mad_sigma = float(cfg.get("gc_max_mad_sigma", 2.5))
        self.min_kept = max(1, int(cfg.get("gc_min_kept", 3)))
        self.screen_w = int(cfg.get("gc_screen_w", 1920))
        self.screen_h = int(cfg.get("gc_screen_h", 1080))
        self.path: str = str(path) if path else _default_gaze_path()
        self._grid: List[Tuple[float, float]] = []
        self._records: List[Dict[str, float]] = []
        self._order: List[Tuple[float, float]] = []
        self._counts: Dict[Tuple[float, float], int] = {}
        self._matrix: Optional[np.ndarray] = None   # 2×3, screen = M @ [gx,gy,1]
        self._quality: Dict[str, Any] = {}
        self._sw = self.screen_w
        self._sh = self.screen_h

    # ── workflow ──────────────────────────────────────────────────────────

    def begin(self) -> List[Tuple[float, float]]:
        """Return the normalized-screen target grid to collect against.

        Edge coordinates are inset by ``gc_margin`` so corner targets stay
        fully visible.  Resets previously collected samples (a new session).
        """
        m = _clamp(self.margin, 0.0, 0.45)
        n = self.n_points
        if n <= 1:
            pts = [(0.5, 0.5)]
        elif n == 4:
            pts = [(m, m), (1 - m, m), (m, 1 - m), (1 - m, 1 - m)]
        elif n == 5:
            pts = [(0.5, 0.5), (m, 0.5), (1 - m, 0.5), (0.5, m), (0.5, 1 - m)]
        elif n == 9:
            pts = [(x, y) for y in (m, 0.5, 1 - m) for x in (m, 0.5, 1 - m)]
        else:
            side = max(1, int(math.ceil(math.sqrt(n))))
            if side > 1:
                vals = [m + (1.0 - 2.0 * m) * i / (side - 1) for i in range(side)]
            else:
                vals = [0.5]
            pts = [(x, y) for y in vals for x in vals][:n]
        self._grid = list(pts)
        self._records = []
        self._order = []
        self._counts = {}
        return list(self._grid)

    def add_sample(self, target: Sequence[float], sample: GazeSample) -> None:
        """Record one (target, gaze) observation.

        ``target`` is a normalized-screen (x, y) point from :meth:`begin`
        (snapped to the nearest grid point within tolerance).  The sample's
        raw gaze (x, y) is stored together with timestamp and confidence.
        """
        tx, ty = float(target[0]), float(target[1])
        if self._grid:
            best = min(self._grid,
                       key=lambda p: (p[0] - tx) ** 2 + (p[1] - ty) ** 2)
            if (best[0] - tx) ** 2 + (best[1] - ty) ** 2 <= _TARGET_SNAP_TOL ** 2:
                tx, ty = best
        key = (round(tx, 4), round(ty, 4))
        if key not in self._counts:
            self._order.append(key)
            self._counts[key] = 0
        self._counts[key] += 1
        try:
            gx = float(sample.x)
            gy = float(sample.y)
        except (TypeError, ValueError, AttributeError):
            return  # never poison the dataset with garbage
        self._records.append({
            "key": key, "gx": gx, "gy": gy,
            "t": float(sample.timestamp), "conf": float(sample.confidence),
        })

    # ── fitting ───────────────────────────────────────────────────────────

    def _mad_keep_mask(self, arr: np.ndarray) -> np.ndarray:
        """Boolean mask of values within the outlier gate of the median.

        Two-pass robust gate:  primary threshold is
        ``max_mad_sigma·(1.4826·MAD)``; because MAD badly under-estimates the
        spread for small samples (n ≤ ~15) a refinement floor of
        ``5.0·σ`` — computed from the MAD-surviving samples only — is
        combined via max() so legitimate gaussian-tail samples are not
        falsely rejected (5σ ≈ never fires on clean data, the classic
        sigma-clipping bound) while gross outliers (≫ 5σ, e.g. the user
        glancing at another target) still are.
        """
        med = float(np.median(arr))
        mad = float(np.median(np.abs(arr - med)))
        sigma = 1.4826 * mad
        thresh = max(self.max_mad_sigma * sigma, 1e-6)
        keep = np.abs(arr - med) <= thresh
        if keep.all() or not keep.any():
            return keep
        # refinement pass: floor from the clean subset
        clean = arr[keep]
        sigma_clean = float(np.std(clean))
        thresh = max(thresh, 5.0 * sigma_clean)
        return np.abs(arr - med) <= thresh

    def finish(self, screen_w: Optional[int] = None,
               screen_h: Optional[int] = None) -> Dict[str, Any]:
        """Clean, fit and grade the collected samples; returns the quality dict.

        Residuals are in PIXELS on the (config- or kwarg-provided) screen
        size.  Never raises; an unusable dataset yields status
        ``"incomplete"`` with no matrix (``map()`` then returns None).
        """
        sw = int(screen_w) if screen_w else self.screen_w
        sh = int(screen_h) if screen_h else self.screen_h
        self._sw, self._sh = sw, sh

        per_target: Dict[str, Optional[float]] = {}
        kept_rows: List[Tuple[float, float, float, float]] = []  # gx,gy,sx,sy
        groups: List[Tuple[str, int]] = []                       # (label, rows)
        samples_used = 0
        samples_rejected = 0
        incomplete = False

        for key in self._order:
            recs = [r for r in self._records if r["key"] == key]
            if not recs:
                continue
            label = f"{key[0]:.3f},{key[1]:.3f}"
            gx = np.array([r["gx"] for r in recs], dtype=float)
            gy = np.array([r["gy"] for r in recs], dtype=float)
            keep = self._mad_keep_mask(gx) & self._mad_keep_mask(gy)
            n_kept = int(keep.sum())
            samples_rejected += len(recs) - n_kept
            if n_kept < self.min_kept:
                incomplete = True
                per_target[label] = None
                continue
            rows = [(float(gx[i]), float(gy[i]),
                     key[0] * sw, key[1] * sh)
                    for i in range(len(recs)) if keep[i]]
            groups.append((label, len(rows)))
            kept_rows.extend(rows)
            samples_used += n_kept

        mean_res: Optional[float] = None
        max_res: Optional[float] = None
        matrix: Optional[np.ndarray] = None

        if len(kept_rows) >= _MIN_FIT_POINTS:
            M = np.array([[r[0], r[1], 1.0] for r in kept_rows], dtype=float)
            rhs = np.array([[r[2], r[3]] for r in kept_rows], dtype=float)
            try:
                X, _, rank, _ = np.linalg.lstsq(M, rhs, rcond=None)
                if rank >= 3 and np.all(np.isfinite(X)):
                    # canonical C-contiguous layout so that map() results are
                    # bit-identical before and after save()/load() (matmul on
                    # a transposed VIEW can differ 1 ULP from a contiguous one)
                    matrix = np.ascontiguousarray(X.T)  # 2×3
                    pred = M @ X
                    res = np.hypot(pred[:, 0] - rhs[:, 0], pred[:, 1] - rhs[:, 1])
                    mean_res = float(res.mean())
                    max_res = float(res.max())
                    # per-target mean residuals (walk groups in order)
                    idx = 0
                    per_target = {}
                    for label, count in groups:
                        seg = res[idx:idx + count]
                        per_target[label] = float(seg.mean()) if count else None
                        idx += count
            except np.linalg.LinAlgError:
                matrix = None
        if matrix is None:
            incomplete = True

        if matrix is None or incomplete:
            status = "incomplete"
        elif mean_res is not None and mean_res <= _GOOD_RESIDUAL_PX:
            status = "good"
        elif mean_res is not None and mean_res <= _FAIR_RESIDUAL_PX:
            status = "fair"
        else:
            status = "poor"

        self._matrix = matrix
        self._quality = {
            "status": status,
            "mean_residual_px": mean_res,
            "max_residual_px": max_res,
            "per_target_residuals": per_target,
            "samples_used": samples_used,
            "samples_rejected": samples_rejected,
            "screen_w": sw,
            "screen_h": sh,
            "n_points": self.n_points,
        }
        return dict(self._quality)

    # ── mapping ───────────────────────────────────────────────────────────

    def map(self, gx: float, gy: float) -> Optional[Tuple[float, float]]:
        """Raw gaze → desktop pixels, clamped to the screen; None when unfitted."""
        if self._matrix is None:
            return None
        try:
            g = np.array([float(gx), float(gy), 1.0], dtype=float)
        except (TypeError, ValueError):
            return None
        if not np.all(np.isfinite(g)):
            return None
        p = self._matrix @ g
        return (_clamp(float(p[0]), 0.0, float(self._sw)),
                _clamp(float(p[1]), 0.0, float(self._sh)))

    def map_normalized(self, gx: float, gy: float) -> Optional[Tuple[float, float]]:
        """Raw gaze → normalized screen [0,1]² (clamped); None when unfitted."""
        p = self.map(gx, gy)
        if p is None:
            return None
        return (_clamp(p[0] / max(self._sw, 1), 0.0, 1.0),
                _clamp(p[1] / max(self._sh, 1), 0.0, 1.0))

    # ── quality gate ──────────────────────────────────────────────────────

    @property
    def quality(self) -> Dict[str, Any]:
        """The last :meth:`finish` quality report (empty before finishing)."""
        return dict(self._quality)

    @property
    def is_calibrated(self) -> bool:
        return self._matrix is not None

    @property
    def samples_collected(self) -> int:
        """Total raw samples stored via :meth:`add_sample` since begin()."""
        return len(self._records)

    def is_reliable(self, min_quality: str = "fair") -> bool:
        """True when fitted AND quality rank ≥ min_quality (incomplete never is)."""
        if self._matrix is None:
            return False
        status = str(self._quality.get("status", "incomplete"))
        if status == "incomplete":
            return False
        return (self.QUALITY_ORDER.get(status, 0)
                >= self.QUALITY_ORDER.get(min_quality, 2))

    # ── persistence ───────────────────────────────────────────────────────

    def save(self, path: Optional[str] = None) -> bool:
        """Atomically persist the fitted calibration as JSON v2.

        Returns True on success; NEVER raises (returns False on any I/O or
        serialization problem).  Requires a fitted matrix.
        """
        if self._matrix is None:
            return False
        p = str(path) if path else self.path
        payload = {
            "version": 2,
            "created": datetime.now().isoformat(timespec="seconds"),
            "n_points": int(self.n_points),
            "screen_w": int(self._sw),
            "screen_h": int(self._sh),
            "matrix": [[float(c) for c in row] for row in self._matrix],
            "quality": self._quality,
        }
        try:
            d = os.path.dirname(p)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, p)  # atomic on POSIX
            return True
        except Exception:
            return False

    def load(self, path: Optional[str] = None) -> bool:
        """Load a saved calibration; True on success, False on ANY problem.

        Corrupt JSON, wrong version, bad matrix shape or a missing file all
        simply return False (existing state is untouched) — never raises.
        """
        p = str(path) if path else self.path
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if int(data.get("version", 0)) != 2:
                return False
            m = np.array(data["matrix"], dtype=float)
            if m.shape != (2, 3) or not np.all(np.isfinite(m)):
                return False
            q = data.get("quality")
            self._matrix = m
            self._quality = dict(q) if isinstance(q, dict) else {}
            self._sw = int(data.get("screen_w", self.screen_w))
            self._sh = int(data.get("screen_h", self.screen_h))
            self.n_points = int(data.get("n_points", self.n_points))
            return True
        except Exception:
            return False

    def reset(self) -> None:
        """Clear samples, fitted matrix and quality (paths/config kept)."""
        self._records = []
        self._order = []
        self._counts = {}
        self._matrix = None
        self._quality = {}


def run_point_calibration(gc: GazeCalibration,
                          sample_fn: Callable[[Tuple[float, float]], GazeSample],
                          points: Optional[Sequence[Tuple[float, float]]] = None,
                          samples_per_point: int = 12,
                          screen_w: Optional[int] = None,
                          screen_h: Optional[int] = None) -> Dict[str, Any]:
    """Drive a complete calibration session against a sampler callable.

    ``sample_fn(target_xy)`` must return the GazeSample the estimator
    produces while the user (or a simulation) looks at that target.  Uses
    ``gc.begin()`` targets unless ``points`` is given, collects
    ``samples_per_point`` samples per target, then returns ``gc.finish()``.
    """
    targets = list(points) if points is not None else gc.begin()
    for t in targets:
        for _ in range(max(1, int(samples_per_point))):
            gc.add_sample(t, sample_fn(t))
    return gc.finish(screen_w=screen_w, screen_h=screen_h)
