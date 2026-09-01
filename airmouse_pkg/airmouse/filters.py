"""
Filters v4.0 — Professional-grade cursor filtering.

The One Euro Filter is the industry standard for cursor / pointer tracking.
Published by Casiez, Daniel, & Roussel (CHI 2012), it adapts its cutoff
frequency to the speed of the input — heavy smoothing at slow speeds
(no jitter when still), light smoothing at fast speeds (no lag when moving).

This single filter beats any cascade of fixed EMAs:
  - EMAs trade off lag vs. jitter — you can't have both.
  - One Euro gives you both, for free, by adapting.

Reference: https://gery.casiez.net/1euro/

Classes:
    LowPassFilter — single-pole EMA primitive (used by OneEuro)
    OneEuroFilter — adaptive filter for 1D signal (use 2 for x/y)
    OneEuroFilter2D — convenience wrapper for 2D (x,y) signals
"""

import math
import numpy as np
from collections import deque


def _smooth_alpha(cutoff_hz: float, dt: float) -> float:
    """Compute EMA alpha from cutoff frequency and sample period.

    alpha = 1 / (1 + tau/dt), where tau = 1 / (2*pi*fc)
    """
    if dt <= 0:
        return 1.0
    tau = 1.0 / (2.0 * math.pi * cutoff_hz)
    return 1.0 / (1.0 + tau / dt)


class LowPassFilter:
    """Single-pole EMA — the building block of OneEuroFilter.

    smoothed = alpha * x + (1 - alpha) * smoothed_prev
    """

    __slots__ = ("alpha", "_smoothed", "_has_prev")

    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha
        self._smoothed = 0.0
        self._has_prev = False

    def filter(self, x: float) -> float:
        if not self._has_prev:
            self._smoothed = x
            self._has_prev = True
        else:
            self._smoothed = self.alpha * x + (1.0 - self.alpha) * self._smoothed
        return self._smoothed

    def reset(self):
        self._smoothed = 0.0
        self._has_prev = False


class OneEuroFilter:
    """Adaptive 1D filter — smooth when slow, responsive when fast.

    Params:
        mincutoff:  cutoff frequency at zero speed (Hz). Lower = smoother at rest.
        beta:        speed coefficient. Higher = more responsive at speed.
        dcutoff:     cutoff for the derivative filter (Hz). Usually 1.0.

    Typical values for cursor tracking at 30fps:
        mincutoff = 1.0, beta = 0.5, dcutoff = 1.0  (smooth, slightly laggy)
        mincutoff = 1.5, beta = 1.0, dcutoff = 1.0  (balanced — recommended)
        mincutoff = 3.0, beta = 1.5, dcutoff = 1.0  (responsive, less smooth)
    """

    __slots__ = ("mincutoff", "beta", "dcutoff", "_x", "_dx", "_last_time")

    def __init__(self, mincutoff: float = 1.5, beta: float = 1.0, dcutoff: float = 1.0):
        self.mincutoff = mincutoff
        self.beta = beta
        self.dcutoff = dcutoff
        self._x = LowPassFilter(alpha=1.0)
        self._dx = LowPassFilter(alpha=1.0)
        self._last_time = -1.0

    def filter(self, x: float, timestamp: float) -> float:
        """Filter a sample.

        Args:
            x: sample value
            timestamp: seconds since start (monotonic). If first call, just pass t=0.
        """
        if self._last_time == -1.0:
            dt = 1.0 / 30.0  # assume 30fps on first frame
        else:
            dt = max(timestamp - self._last_time, 1e-4)
        self._last_time = timestamp

        # Estimate derivative (speed) of the signal
        prev_x = self._x._smoothed if self._x._has_prev else x
        dx = (x - prev_x) / dt
        dx_smoothed = self._dx.filter_with_alpha(dx, _smooth_alpha(self.dcutoff, dt))

        # Adaptive cutoff: increases with speed
        cutoff = self.mincutoff + self.beta * abs(dx_smoothed)
        alpha = _smooth_alpha(cutoff, dt)
        return self._x.filter_with_alpha(x, alpha)

    def reset(self):
        self._x.reset()
        self._dx.reset()
        self._last_time = -1.0


# Add filter_with_alpha to LowPassFilter (allows per-call alpha)
def _lp_filter_with_alpha(self, x: float, alpha: float) -> float:
    self.alpha = alpha
    return self.filter(x)


LowPassFilter.filter_with_alpha = _lp_filter_with_alpha


class OneEuroFilter2D:
    """Convenience wrapper for filtering 2D (x, y) signals with One Euro.

    Two independent One Euro filters (one per axis), shared timestamp.
    """

    __slots__ = ("_fx", "_fy", "_t0", "_frame_count")

    def __init__(self, mincutoff: float = 1.5, beta: float = 1.0, dcutoff: float = 1.0):
        self._fx = OneEuroFilter(mincutoff=mincutoff, beta=beta, dcutoff=dcutoff)
        self._fy = OneEuroFilter(mincutoff=mincutoff, beta=beta, dcutoff=dcutoff)
        self._t0 = -1.0
        self._frame_count = 0

    def filter(self, x: float, y: float, timestamp: float = None) -> tuple:
        """Filter 2D sample. If timestamp is None, uses internal frame counter."""
        if timestamp is None:
            self._frame_count += 1
            timestamp = self._frame_count / 30.0  # assume 30fps
        return self._fx.filter(x, timestamp), self._fy.filter(y, timestamp)

    def filter_np(self, xy: np.ndarray, timestamp: float = None) -> np.ndarray:
        """Filter 2D numpy array."""
        fx, fy = self.filter(float(xy[0]), float(xy[1]), timestamp)
        return np.array([fx, fy])

    def reset(self):
        self._fx.reset()
        self._fy.reset()
        self._frame_count = 0
