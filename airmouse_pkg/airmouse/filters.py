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
    KalmanFilter1D — constant-velocity Kalman filter for 1D signals (v5.0)
    KalmanFilter2D — two independent KalmanFilter1D for (x, y) signals (v5.0)
    HybridOneEuroKalman — adaptive One Euro + Kalman fusion filter (v5.0)
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


# ===========================================================================
# v5.0.0 — Kalman filtering & One Euro / Kalman hybrid fusion
# ===========================================================================

class KalmanFilter1D:
    """1D constant-velocity Kalman filter (state: [position, velocity]).

    Signal model — constant velocity corrupted by white-noise acceleration:

        predict:   x⁻ = F x            F = [[1, dt], [0, 1]]
                   P⁻ = F P Fᵀ + Q     Q = q · [[dt³/3, dt²/2],
                                                 [dt²/2,  dt  ]]
        update     (position-only measurement z with variance r, H = [1, 0]):
                   S  = P⁻[0,0] + r                    (innovation variance)
                   K  = P⁻[:,0] / S                    (2x1 Kalman gain)
                   x  = x⁻ + K · (z − x⁻[0])           (posterior state)
                   P  = (I−KH) P⁻ (I−KH)ᵀ + K r Kᵀ     (Joseph form — keeps P
                                                        symmetric positive
                                                        definite)

    The initial velocity variance is deliberately wide (9.0 ≈ ±3 units/s at
    1σ in normalized screen coordinates) so the filter acquires the true
    hand velocity within a handful of frames instead of lagging behind it
    for seconds; the position prior variance is the measurement variance r.

    Params:
        process_noise (q): acceleration variance driving Q. Lower = trust
            the constant-velocity model more → smoother but laggier to real
            direction changes. Higher = snappier but noisier.
        measurement_noise (r): sensor variance. Lower = trust the sensor
            more → snappier tracking, more jitter passes through.
        initial_velocity: velocity assumed after construction/reset (units/s).

    Notes:
        * filter(z, dt=None): dt=None treats each call as one 30 fps frame.
          dt is clamped to [1e-4, 0.5] s for numerical safety.
        * The first filter() call after construction/reset() *initializes*
          the state from that measurement (no predict/update) — the filter
          snaps to the hand instead of gliding in from (0, 0).
    """

    __slots__ = ("_q", "_r", "_v0", "_x", "_P", "_initialized", "_frame_count")

    _DT_DEFAULT = 1.0 / 30.0
    _DT_MIN = 1e-4
    _DT_MAX = 0.5
    # Wide "velocity unknown" prior (variance): sigma_v = 3 units/s.
    _P0_VEL_VAR = 9.0

    def __init__(self, process_noise: float = 1e-2, measurement_noise: float = 1e-1,
                 initial_velocity: float = 0.0):
        self._q = float(process_noise)
        self._r = float(measurement_noise)
        self._v0 = float(initial_velocity)
        self._frame_count = 0
        self._x = np.array([0.0, self._v0])
        self._P = self._make_p0()
        self._initialized = False

    # -- internals ---------------------------------------------------------

    def _make_p0(self) -> np.ndarray:
        """Initial covariance: position known to ~sqrt(r), velocity unknown."""
        return np.diag([self._r, self._P0_VEL_VAR])

    def _process_cov(self, dt: float) -> np.ndarray:
        """Discrete process noise for continuous white-noise acceleration
        (CWNA) with power spectral density q: the velocity random-walk term
        q*dt keeps the velocity channel responsive over long horizons."""
        dt2 = dt * dt
        return self._q * np.array([
            [dt2 * dt / 3.0, dt2 / 2.0],
            [dt2 / 2.0,      dt],
        ])

    def _predict(self, dt: float) -> None:
        """Advance state and covariance through the prediction step."""
        F = np.array([[1.0, dt], [0.0, 1.0]])
        self._x = F @ self._x
        self._P = F @ self._P @ F.T + self._process_cov(dt)

    # -- public API --------------------------------------------------------

    def filter(self, x: float, dt: float = None) -> float:
        """Predict + update with measurement x; returns posterior position."""
        if dt is None:
            self._frame_count += 1
            dt = self._DT_DEFAULT
        dt = min(max(float(dt), self._DT_MIN), self._DT_MAX)

        z = float(x)
        if not self._initialized:
            # Snap to the first measurement; velocity stays at the prior.
            self._x = np.array([z, self._v0])
            self._P = self._make_p0()
            self._initialized = True
            return z

        self._predict(dt)
        # Update with position-only measurement (H = [1, 0]).
        S = self._P[0, 0] + self._r
        K = self._P[:, 0] / S                      # 2x1 Kalman gain
        innovation = z - self._x[0]
        self._x = self._x + K * innovation
        # Joseph-form covariance update (numerically stable, symmetric PSD).
        A = np.array([[1.0 - K[0], 0.0],
                      [-K[1],      1.0]])
        self._P = A @ self._P @ A.T + self._r * np.outer(K, K)
        self._P = 0.5 * (self._P + self._P.T)      # kill fp asymmetry
        return float(self._x[0])

    def predict_only(self, dt: float) -> float:
        """Advance the state with the prediction step ONLY (no measurement).

        Useful for short lookahead / coasting when measurements drop out.
        NOTE: the step is *committed* — state and covariance move forward and
        the covariance grows (a prediction carries no new information), so a
        burst of predict_only() calls makes the filter trust the next real
        measurement more. Returns the predicted position.
        """
        dt = min(max(float(dt), self._DT_MIN), self._DT_MAX)
        if not self._initialized:
            return float(self._x[0])
        self._predict(dt)
        return float(self._x[0])

    def reset(self, x: float = 0.0):
        """Forget all state. `x` seeds the reported position; the next
        filter() call re-initializes from its own measurement (snap)."""
        self._x = np.array([float(x), self._v0])
        self._P = self._make_p0()
        self._initialized = False
        self._frame_count = 0

    @property
    def position(self) -> float:
        """Current posterior position estimate (read-only)."""
        return float(self._x[0])

    @property
    def velocity(self) -> float:
        """Current posterior velocity estimate in units/s (read-only)."""
        return float(self._x[1])


class KalmanFilter2D:
    """2D constant-velocity Kalman filter — two independent KalmanFilter1D.

    X and Y are filtered independently (the constant-velocity model and the
    noise parameters are applied per axis, which is appropriate for
    normalized webcam coordinates whose axes behave similarly).

    Params mirror KalmanFilter1D: process_noise (q), measurement_noise (r),
    initial_velocity.
    """

    __slots__ = ("_kx", "_ky")

    def __init__(self, process_noise: float = 1e-2, measurement_noise: float = 1e-1,
                 initial_velocity: float = 0.0):
        self._kx = KalmanFilter1D(process_noise, measurement_noise, initial_velocity)
        self._ky = KalmanFilter1D(process_noise, measurement_noise, initial_velocity)

    def filter(self, x: float, y: float, dt: float = None) -> tuple:
        """Filter one (x, y) sample; returns the (x, y) posterior estimate."""
        return self._kx.filter(float(x), dt), self._ky.filter(float(y), dt)

    def filter_np(self, xy: np.ndarray, dt: float = None) -> np.ndarray:
        """Filter a 2-vector; returns np.array([x, y])."""
        fx, fy = self.filter(float(xy[0]), float(xy[1]), dt)
        return np.array([fx, fy])

    def reset(self):
        """Reset both axes."""
        self._kx.reset()
        self._ky.reset()

    @property
    def position(self) -> np.ndarray:
        """Current posterior position [x, y] (read-only)."""
        return np.array([self._kx.position, self._ky.position])

    @property
    def velocity(self) -> np.ndarray:
        """Current posterior velocity [vx, vy] in units/s (read-only)."""
        return np.array([self._kx.velocity, self._ky.velocity])


class HybridOneEuroKalman:
    """Adaptive One Euro + Kalman fusion — the v5.0 flagship cursor filter.

    Every sample runs through BOTH a OneEuroFilter2D (fast, speed-adaptive,
    slightly noisy) and a KalmanFilter2D (ultra smooth, explicitly models
    velocity). The two estimates are blended per axis:

        out = w_k · kalman_out + (1 − w_k) · one_euro_out

    with a speed-dependent Kalman weight:

        speed_factor = min(1, speed_ema / speed_ref)
        w_k = max_kalman_weight − speed_factor · (2·max_kalman_weight − 1)

    * Hand slow / still → speed_factor → 0 → w_k → max_kalman_weight (0.85):
      the Kalman dominates and the cursor is rock-solid (jitter-free lock).
    * Hand moving fast  → speed_factor → 1 → w_k → 1 − max_kalman_weight
      (0.15): the One Euro dominates and the cursor stays responsive (the
      Kalman's velocity-model lag is downweighted exactly when it would hurt).

    speed_ema is an EMA (alpha = 0.25) of the instantaneous hand speed taken
    from the KALMAN's posterior velocity channel (noise-free — a still hand
    reads ~0; the One Euro delta instead has a ~0.3 units/s noise floor that
    would keep the fusion stuck in fast mode).
    speed_ref is the crossover scale: at speed_ema ≥ speed_ref the fast-motion
    mix is fully reached (One Euro weight ≥ ~85%).

    Fusion modes:
        "adaptive"  — speed-dependent blend described above (default).
        "kalman"    — pure Kalman output (max smoothness, some lag).
        "one_euro"  — pure One Euro output (A/B testing baseline).
        "average"   — fixed 50/50 blend.

    Timestamp handling matches OneEuroFilter2D: timestamp=None uses an
    internal frame counter at 30 fps; otherwise dt = clamp(t − t_prev,
    1e-4, 0.5) s is shared by both inner filters.

    HUD/debug attributes: speed_ema (smoothed hand speed, units/s) and
    last_kalman_weight (w_k used for the most recent sample).
    """

    __slots__ = ("fusion", "speed_ref", "max_kalman_weight",
                 "one_euro", "kalman", "speed_ema", "last_kalman_weight",
                 "_speed_alpha", "_prev_oe", "_t0", "_frame_count")

    _SPEED_EMA_ALPHA = 0.25
    _FUSION_MODES = ("adaptive", "kalman", "one_euro", "average")

    def __init__(self, mincutoff: float = 1.2, beta: float = 1.5, dcutoff: float = 1.0,
                 kalman_process_noise: float = 1.0,
                 kalman_measurement_noise: float = 5e-2,
                 fusion: str = "adaptive", speed_ref: float = 0.15,
                 max_kalman_weight: float = 0.85):
        if fusion not in self._FUSION_MODES:
            raise ValueError(
                f"fusion must be one of {self._FUSION_MODES}, got {fusion!r}")
        self.fusion = fusion
        self.speed_ref = float(speed_ref)
        self.max_kalman_weight = min(max(float(max_kalman_weight), 0.0), 1.0)
        self.one_euro = OneEuroFilter2D(mincutoff=mincutoff, beta=beta, dcutoff=dcutoff)
        self.kalman = KalmanFilter2D(kalman_process_noise, kalman_measurement_noise)
        self.speed_ema = 0.0
        self.last_kalman_weight = 0.0
        self._speed_alpha = self._SPEED_EMA_ALPHA
        self._prev_oe = None
        self._t0 = -1.0
        self._frame_count = 0

    def filter(self, x: float, y: float, timestamp: float = None) -> tuple:
        """Filter one (x, y) sample; returns the fused (x, y) estimate."""
        if timestamp is None:
            self._frame_count += 1
            timestamp = self._frame_count / 30.0  # assume 30 fps
        if self._t0 < 0.0:
            dt = 1.0 / 30.0                       # first frame
        else:
            dt = min(max(timestamp - self._t0, 1e-4), 0.5)
        self._t0 = timestamp

        # Run BOTH filters so either path can take over instantly.
        oe = self.one_euro.filter(float(x), float(y), timestamp)
        ka = self.kalman.filter(float(x), float(y), dt)

        # Hand speed from the KALMAN posterior velocity estimate (units/s),
        # EMA-smoothed. The Kalman's velocity channel is noise-free enough
        # that a still hand reads ~0 units/s. (Using the One Euro delta
        # instead puts a noise-driven floor of ~0.3 units/s on "speed",
        # which would keep the fusion stuck in fast mode and let jitter
        # through — exactly what this filter exists to prevent.)
        kv = self.kalman.velocity
        inst_speed = float(np.hypot(kv[0], kv[1]))
        self._prev_oe = oe
        a = self._speed_alpha
        self.speed_ema += a * (inst_speed - self.speed_ema)

        if self.fusion == "kalman":
            self.last_kalman_weight = 1.0
            out = ka
        elif self.fusion == "one_euro":
            self.last_kalman_weight = 0.0
            out = oe
        elif self.fusion == "average":
            self.last_kalman_weight = 0.5
            out = (0.5 * (ka[0] + oe[0]), 0.5 * (ka[1] + oe[1]))
        else:  # "adaptive"
            sf = min(1.0, self.speed_ema / self.speed_ref) if self.speed_ref > 0.0 else 1.0
            lo = 1.0 - self.max_kalman_weight
            w_k = self.max_kalman_weight - sf * (self.max_kalman_weight - lo)
            self.last_kalman_weight = w_k
            out = (w_k * ka[0] + (1.0 - w_k) * oe[0],
                   w_k * ka[1] + (1.0 - w_k) * oe[1])
        return out

    def filter_np(self, xy: np.ndarray, timestamp: float = None) -> np.ndarray:
        """Filter a 2-vector; returns np.array([x, y])."""
        fx, fy = self.filter(float(xy[0]), float(xy[1]), timestamp)
        return np.array([fx, fy])

    def reset(self):
        """Reset both inner filters and the speed EMA."""
        self.one_euro.reset()
        self.kalman.reset()
        self.speed_ema = 0.0
        self.last_kalman_weight = 0.0
        self._prev_oe = None
        self._t0 = -1.0
        self._frame_count = 0
