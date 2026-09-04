"""
Calibration v5.0 — Adaptive Calibration that learns the user's hand.

Watches the stream of normalized index-tip positions coming from the
tracker and slowly learns the *box* the user's hand actually moves in.
Positions are then remapped from that personal box onto the full
[0..1] range, so a user who only sweeps their hand through a small
region of the camera frame still gets full-screen cursor travel.

How it works
------------
* Per axis, min/max statistics start at 0.5 and *expand* toward every
  observed value, so the learned box always covers the live sample
  (the current position is never clipped mid-flight).
* Stale extremes decay away: every frame each bound relaxes slightly
  toward the box centre (rate ``1 - decay``), so a one-frame glitch or
  an extreme reached long ago is gradually forgotten instead of
  permanently shrinking the usable resolution.
* ``remap()`` pads the learned box with a 10% soft margin and clips
  the result into [0..1] — the output is always finite and in range.
* Until ``min_samples`` frames have been observed (or while
  ``enabled`` is False) ``update()`` passes the position through
  untouched — no cursor jumps before there is enough data.

Motion profiling (used to auto-tune the One Euro filter):

* ``speed_ema`` — EMA of per-frame speed, in normalized units/frame.
* ``tremor``    — EMA of the residual (high-pass) motion observed while
  the hand is nearly still, i.e. how much the finger jitters when the
  user is trying to hold it steady.

Statistics persist to ``~/.airmouse/calibration.json`` using an atomic
write and are reloaded automatically on construction.  Every method is
guarded: a missing/corrupt file or an unusable environment never
raises, it just degrades to pass-through behaviour.

Classes:
    AdaptiveCalibration — online learner + remapper for hand positions

Functions:
    get_default_calibration — process-wide lazily-created singleton
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from typing import Dict, Optional

import numpy as np

CALIBRATION_PATH = os.path.join(os.path.expanduser("~"), ".airmouse", "calibration.json")

#: import-time default, kept so a runtime override of CALIBRATION_PATH
#: (tests / embedders) can be detected by :func:`_calibration_path`
_DEFAULT_CALIBRATION_PATH = CALIBRATION_PATH


def _calibration_path() -> str:
    """ACTIVE calibration file path (dynamic; one resolution).

    An explicit runtime override of the module constant
    ``CALIBRATION_PATH`` (≠ the import-time default) still wins — that
    keeps existing monkeypatch-style embedders working — otherwise the
    authoritative ``airmouse.paths.calibration_file()`` is used, so
    ``$AIRMOUSE_HOME`` set after import is always honored.
    """
    override = globals().get("CALIBRATION_PATH")
    try:
        if override and os.path.abspath(str(override)) != \
                os.path.abspath(_DEFAULT_CALIBRATION_PATH):
            return str(override)
    except Exception:
        pass
    try:
        from . import paths
        return paths.calibration_file()
    except Exception:
        return _DEFAULT_CALIBRATION_PATH

_CALIBRATION_VERSION = 1
_SOFT_MARGIN = 0.10      # total soft margin around the learned box (5% per side)
_EPS = 1.0e-6            # minimum box width before remap degenerates
_CENTER = 0.5            # neutral value every statistic starts from


class AdaptiveCalibration:
    """Learns the user's reach box and remaps positions to the full range.

    Feed normalized index-tip positions (``np.array([x, y])`` in [0..1])
    into :meth:`update` once per frame; it returns the remapped position
    ready for cursor control.
    """

    def __init__(self, enabled: bool = True, decay: float = 0.999,
                 min_samples: int = 45, save_every: int = 300):
        """
        Args:
            enabled: master switch; when False positions pass through and
                the reach box is not learned (motion profiling continues).
            decay: per-frame retention of the observed min/max bounds.
                Each frame every bound relaxes toward the box centre by
                ``(1 - decay)`` — with the default 0.999 a stale extreme
                takes ~1000 frames to fade.  Expansion toward observed
                values is immediate, so the box always covers live input.
            min_samples: samples required before remapping kicks in.
            save_every: auto-save stats to disk every N learned samples
                (0 disables autosave).
        """
        self.enabled = bool(enabled)
        self.decay = float(decay)
        self.min_samples = max(1, int(min_samples))
        self.save_every = int(save_every)
        self.reset()
        self.load()  # pick up previously learned stats (guarded, best effort)

    # ------------------------------------------------------------------
    # Core stream API
    # ------------------------------------------------------------------
    def update(self, pos: np.ndarray) -> np.ndarray:
        """Feed one normalized hand position; get the remapped one back.

        Learns the reach box from ``pos``, updates the speed / tremor
        motion profile, occasionally auto-saves, and returns the position
        remapped from the learned box to the full [0..1] range.  While
        ``not is_ready`` or ``enabled is False`` the input is returned
        unchanged (pass-through — no jumps before enough data).
        Invalid (non-finite) input is passed through untouched and never
        poisons the statistics.
        """
        p = self._as_xy(pos)
        if p is None:
            return pos
        self._track_motion(p)
        if self.enabled:
            self._learn(p)
            self._samples += 1
            if self.save_every > 0 and self._samples % self.save_every == 0:
                self.save()  # guarded best-effort autosave
        return self.remap(p)

    def remap(self, pos: np.ndarray) -> np.ndarray:
        """Map ``pos`` from the observed [mn, mx] box to full [0..1].

        A 10% soft margin (5% per side) is padded around the learned box
        so freshly observed extremes do not clip instantly.  The result
        is clipped into [0..1]; a degenerate (zero-width) axis maps to
        0.5.  Pass-through while not ready or disabled.
        """
        p = self._as_xy(pos)
        if p is None:
            return np.asarray(pos, dtype=float)
        if (not self.enabled) or (not self.is_ready):
            return p
        out = np.empty(2, dtype=float)
        for i in range(2):
            mn = float(self._mn[i])
            mx = float(self._mx[i])
            width = mx - mn
            if width < _EPS:
                out[i] = _CENTER
                continue
            pad = _SOFT_MARGIN * width * 0.5
            lo = mn - pad
            hi = mx + pad
            out[i] = (float(p[i]) - lo) / (hi - lo)
        return np.clip(out, 0.0, 1.0)

    # ------------------------------------------------------------------
    # Filter auto-tuning
    # ------------------------------------------------------------------
    def suggested_filter_params(self) -> Dict[str, float]:
        """Suggest One Euro filter params from the learned motion profile.

        Pure deterministic logic:

        * high ``tremor``  -> lower ``mincutoff`` (down to 0.9) for more
          low-speed smoothing to absorb the shake;
        * high ``speed_ema`` -> raise ``beta`` (up to 2.2) so fast users
          keep low lag.

        Base defaults are ``mincutoff=1.2`` / ``beta=1.5``.
        """
        base_mincutoff = 1.2
        base_beta = 1.5
        # tremor ~0.002/frame is a calm hand; >=0.015 is genuinely shaky
        k_tremor = min(max((self._tremor - 0.003) / 0.012, 0.0), 1.0)
        mincutoff = base_mincutoff - 0.3 * k_tremor           # -> 0.9
        # speed_ema >= 0.15 units/frame means a fast, confident user
        k_speed = min(max(self._speed_ema / 0.15, 0.0), 1.0)
        beta = base_beta + 0.7 * k_speed                      # -> 2.2
        return {"mincutoff": round(mincutoff, 3), "beta": round(beta, 3)}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save(self) -> bool:
        """Atomically write stats to the active calibration path. Never raises."""
        try:
            path = _calibration_path()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            payload = {
                "version": _CALIBRATION_VERSION,
                "saved": datetime.now().isoformat(timespec="seconds"),
                "samples": int(self._samples),
                "min": [float(self._mn[0]), float(self._mn[1])],
                "max": [float(self._mx[0]), float(self._mx[1])],
                "speed_ema": float(self._speed_ema),
                "tremor": float(self._tremor),
            }
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, path)  # atomic on POSIX & Windows
            return True
        except Exception:
            return False

    def load(self) -> bool:
        """Load stats from the active calibration path. True on success."""
        try:
            with open(_calibration_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return False
            mn_raw, mx_raw = data.get("min"), data.get("max")
            if not (isinstance(mn_raw, (list, tuple)) and isinstance(mx_raw, (list, tuple))):
                return False
            if len(mn_raw) < 2 or len(mx_raw) < 2:
                return False
            mn = [float(mn_raw[0]), float(mn_raw[1])]
            mx = [float(mx_raw[0]), float(mx_raw[1])]
            for i in range(2):
                if not (math.isfinite(mn[i]) and math.isfinite(mx[i])):
                    return False
                if mn[i] > mx[i]:
                    mn[i], mx[i] = mx[i], mn[i]
            self._mn = mn
            self._mx = mx
            self._samples = int(data.get("samples") or 0)
            self._speed_ema = float(data.get("speed_ema") or 0.0)
            self._tremor = float(data.get("tremor") or 0.0)
            self._prev = None
            return True
        except Exception:
            return False

    def reset(self) -> None:
        """Forget everything learned so far and start over."""
        self._mn = [_CENTER, _CENTER]
        self._mx = [_CENTER, _CENTER]
        self._samples = 0
        self._speed_ema = 0.0
        self._tremor = 0.0
        self._prev = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def is_ready(self) -> bool:
        """True once enough samples have been observed to remap safely."""
        return self._samples >= self.min_samples

    @property
    def coverage(self) -> float:
        """Area of the observed box in [0..1] (how much frame is used)."""
        w = max(0.0, self._mx[0] - self._mn[0]) * max(0.0, self._mx[1] - self._mn[1])
        return float(min(max(w, 0.0), 1.0))

    @property
    def speed_ema(self) -> float:
        """EMA of per-frame speed in normalized units/frame."""
        return float(self._speed_ema)

    @property
    def tremor(self) -> float:
        """EMA of high-pass motion while the hand is nearly still."""
        return float(self._tremor)

    @property
    def samples(self) -> int:
        """Number of learned samples since the last reset."""
        return int(self._samples)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _learn(self, p: np.ndarray) -> None:
        """Soft-decay then expand the per-axis [min, max] reach box."""
        forget = min(max(1.0 - self.decay, 0.0), 0.2)
        for i in range(2):
            v = float(p[i])
            mn = self._mn[i]
            mx = self._mx[i]
            mid = 0.5 * (mn + mx)
            # decay: stale extremes slowly relax toward the box centre
            mn += (mid - mn) * forget
            mx += (mid - mx) * forget
            # expand: the box always covers the current observation
            if v < mn:
                mn = v
            if v > mx:
                mx = v
            self._mn[i] = mn
            self._mx[i] = mx

    def _track_motion(self, p: np.ndarray) -> None:
        """Update the speed EMA and the stillness tremor estimate."""
        if self._prev is not None:
            d = float(np.linalg.norm(p - self._prev))
        else:
            d = 0.0
        self._prev = p.copy()
        alpha = 0.10  # speed EMA responsiveness
        self._speed_ema = (1.0 - alpha) * self._speed_ema + alpha * d
        # "nearly still": below an absolute floor and not far above the
        # running average speed — residual motion there is tremor/jitter.
        still_threshold = max(0.01, 3.0 * self._speed_ema)
        if d < still_threshold:
            t_alpha = 0.05
            self._tremor = (1.0 - t_alpha) * self._tremor + t_alpha * d

    @staticmethod
    def _as_xy(pos) -> "Optional[np.ndarray]":
        """Coerce input to a finite 2-vector, or None if unusable."""
        try:
            arr = np.asarray(pos, dtype=float).reshape(-1)
        except Exception:
            return None
        if arr.size < 2 or not np.all(np.isfinite(arr[:2])):
            return None
        return np.array([arr[0], arr[1]], dtype=float)


# Module-level singleton -------------------------------------------------
_default_calibration: "Optional[AdaptiveCalibration]" = None


def get_default_calibration() -> AdaptiveCalibration:
    """Return the process-wide AdaptiveCalibration singleton (lazy, cached).

    The instance loads any persisted stats from CALIBRATION_PATH on
    creation, so learned calibration survives restarts.
    """
    global _default_calibration
    if _default_calibration is None:
        _default_calibration = AdaptiveCalibration()
    return _default_calibration
