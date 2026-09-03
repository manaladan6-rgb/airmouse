"""
Physics Engine v3.2 — Direct Tracking + Spring-Damper + Adaptive + Momentum + Edge Gravity
+ Dual-Stage Jitter + Velocity Prediction + Position Smoothing + Acceleration Limiting

Two tracking modes:

  DIRECT (default v3.2):
    Finger position maps 1:1 to screen position — IMMEDIATE.
    Single responsive EMA (α=0.55) + noise gate + direct map + pixel deadzone.
    No cascading lag. No spring. Cursor is AT your finger, not chasing it.

  IRONMAN (legacy v3.1):
    FINGER-RELATIVE tracking — cursor driven by delta from home position.
    Tiny finger moves -> large cursor jumps (exponential curve).
    Cursor drifts toward center when hand is still.

Physics stack (DIRECT mode):
    1. Single responsive EMA  — kills camera noise (α=0.55, ~33ms lag)
    2. Noise gate             — ignore micro-movements (like mouse sensor LOD)
    3. Direct map             — finger [0..1] -> screen [0..W, 0..H] — IMMEDIATE
    4. Pixel deadzone         — don't move if < 1.5px changed

Physics stack (IRONMAN mode):
    1. Dual-stage jitter   — micro-tremor kill + macro smooth
    2. Home calibration    — establish rest position, track delta (stability-gated)
    3. Exponential curve   — tiny finger moves -> big cursor moves (with soft deadzone)
    4. Velocity predictor  — Kalman-like lookahead to reduce perceived latency
    5. Adaptive spring     — k changes with speed (slow=precise, fast=snappy)
    6. Acceleration limit  — prevent sudden jerks
    7. Spring-damper       — Hooke's Law + viscous damping (frame-compensated)
    8. Momentum throw      — flick -> cursor keeps gliding
    9. Position smoother   — final EMA pass for silky cursor output
    10. Edge gravity       — soft pull toward screen edges
    11. Screen clamp       — never go off-screen
"""

import numpy as np
from collections import deque


class DualStageJitterFilter:
    """Two-stage low-pass filter: micro-tremor kill + macro smoothing.

    Stage 1 (micro): high alpha = fast response, kills tiny tremor
    Stage 2 (macro): low alpha = slow drift, smooths out jitter bumps

    This gives responsiveness AND smoothness simultaneously.
    """

    def __init__(self, micro_alpha=0.45, macro_alpha=0.25):
        self.micro_alpha = micro_alpha
        self.macro_alpha = macro_alpha
        self._micro = None
        self._macro = None

    def filter(self, raw):
        """Apply dual-stage EMA filter."""
        # Stage 1: micro-tremor suppression (responsive)
        if self._micro is None:
            self._micro = raw.copy()
        else:
            self._micro = self.micro_alpha * raw + (1.0 - self.micro_alpha) * self._micro

        # Stage 2: macro smoothing (silky)
        if self._macro is None:
            self._macro = self._micro.copy()
        else:
            self._macro = self.macro_alpha * self._micro + (1.0 - self.macro_alpha) * self._macro

        return self._macro.copy()

    def reset(self):
        self._micro = None
        self._macro = None


class JitterFilter:
    """Backward-compatible single-stage filter (wraps DualStage with macro_alpha=1)."""

    def __init__(self, alpha=0.3):
        self.alpha = alpha
        self.smoothed = None

    def filter(self, raw):
        if self.smoothed is None:
            self.smoothed = raw.copy()
        else:
            self.smoothed = self.alpha * raw + (1.0 - self.alpha) * self.smoothed
        return self.smoothed.copy()

    def reset(self):
        self.smoothed = None


class HomePosition:
    """Tracks the 'home' position where fingers rest.

    In Iron Man mode, the cursor is driven by DELTA from home,
    not absolute position. This means your hand can be anywhere —
    only finger movements matter.

    v3.1 improvements:
    - Stability-gated calibration: only calibrates when hand is stable
    - Faster initial lock, slower drift when moving
    - Configurable drift rates for moving vs still
    """

    def __init__(self, drift_rate=0.02, drift_rate_moving=0.005,
                 stability_window=5, stability_threshold=0.008):
        self.home = None
        self.drift_rate = drift_rate            # When hand is still
        self.drift_rate_moving = drift_rate_moving  # When hand is moving (slower drift)
        self.is_calibrated = False
        self.stability_window = stability_window
        self.stability_threshold = stability_threshold
        self._pos_history = deque(maxlen=stability_window)

    def calibrate(self, current_pos):
        """Set home to current position."""
        self.home = current_pos.copy()
        self.is_calibrated = True
        self._pos_history.clear()

    def get_delta(self, current_pos):
        """Get finger displacement from home position.

        Returns:
            delta: np.ndarray — displacement from home [dx, dy]
        """
        if not self.is_calibrated:
            self.calibrate(current_pos)
            return np.zeros(2)

        delta = current_pos - self.home

        # Track position history for stability measurement
        self._pos_history.append(current_pos.copy())

        # Choose drift rate based on movement speed
        if len(self._pos_history) >= 2:
            recent_speed = np.linalg.norm(
                self._pos_history[-1] - self._pos_history[-2]
            )
            if recent_speed > self.stability_threshold:
                rate = self.drift_rate_moving  # Slow drift when moving
            else:
                rate = self.drift_rate  # Normal drift when still
        else:
            rate = self.drift_rate

        self.home += rate * delta

        return delta

    def is_stable(self):
        """Check if the hand position has been stable recently."""
        if len(self._pos_history) < self.stability_window:
            return False
        positions = list(self._pos_history)
        for i in range(1, len(positions)):
            if np.linalg.norm(positions[i] - positions[i-1]) > self.stability_threshold:
                return False
        return True

    def reset(self):
        self.home = None
        self.is_calibrated = False
        self._pos_history.clear()


class ExponentialCurve:
    """Maps linear finger delta to exponential cursor movement.

    Iron Man feel: tiny finger movements -> large cursor jumps.
    Uses: sign(x) * |x|^power * scale

    v3.1: Smooth deadzone transition (cubic easing instead of hard cut)
    """

    def __init__(self, power=0.6, scale=3.0):
        self.power = power
        self.scale = scale

    def map(self, delta):
        """Apply exponential sensitivity curve."""
        mapped = np.sign(delta) * np.abs(delta) ** self.power * self.scale
        return mapped

    def map_with_deadzone(self, delta, deadzone=0.01):
        """Apply curve with soft deadzone (cubic fade-in, no hard cut)."""
        result = np.zeros_like(delta)
        for i in range(len(delta)):
            ad = abs(delta[i])
            if ad < deadzone * 0.5:
                # Full deadzone — zero output
                result[i] = 0.0
            elif ad < deadzone:
                # Transition zone — cubic ease-in from 0 to full
                t = (ad - deadzone * 0.5) / (deadzone * 0.5)
                blend = t * t * (3.0 - 2.0 * t)  # Smoothstep
                adjusted = delta[i] - np.sign(delta[i]) * deadzone * 0.5
                full_val = np.sign(adjusted) * abs(adjusted) ** self.power * self.scale
                result[i] = full_val * blend
            else:
                # Full sensitivity
                adjusted = delta[i] - np.sign(delta[i]) * deadzone
                result[i] = np.sign(adjusted) * abs(adjusted) ** self.power * self.scale
        return result


class VelocityPredictor:
    """Kalman-like velocity prediction to reduce perceived input latency.

    Predicts where the finger will be slightly ahead based on velocity.
    This compensates for the ~30ms pipeline delay (camera + processing + display).

    The prediction is gentle — it only activates during sustained movement,
    not during tremor or direction changes.
    """

    def __init__(self, prediction_factor=0.15, max_correction=0.02):
        self.prediction_factor = prediction_factor
        self.max_correction = max_correction
        self._prev_pos = None
        self._velocity = np.zeros(2)

    def predict(self, current_pos):
        """Return predicted position (slightly ahead of current)."""
        if self._prev_pos is None:
            self._prev_pos = current_pos.copy()
            return current_pos.copy()

        raw_velocity = current_pos - self._prev_pos

        # Smooth velocity estimate (EMA)
        self._velocity = 0.4 * raw_velocity + 0.6 * self._velocity

        # Only predict during sustained movement (not tremor)
        speed = np.linalg.norm(self._velocity)
        if speed < 0.003:  # Too slow — no prediction
            self._prev_pos = current_pos.copy()
            return current_pos.copy()

        # Predicted position
        correction = self._velocity * self.prediction_factor

        # Clamp correction to prevent overshooting
        corr_norm = np.linalg.norm(correction)
        if corr_norm > self.max_correction:
            correction = correction * (self.max_correction / corr_norm)

        predicted = current_pos + correction
        self._prev_pos = current_pos.copy()
        return predicted

    def reset(self):
        self._prev_pos = None
        self._velocity = np.zeros(2)


class AdaptiveSpringDamper:
    """Spring-damper with speed-adaptive stiffness + acceleration limiting.

    v3.1 improvements:
    - Frame-time compensation for consistent physics across frame rates
    - Acceleration limiting prevents sudden jerks
    - Softer low-speed damping for better precision feel
    - Smoother stiffness transition (exponential ease)
    """

    def __init__(self, mass=0.8, stiffness_min=120.0, stiffness_max=400.0,
                 damping_ratio=0.85, speed_threshold=200.0,
                 max_accel=50000.0, stiffness_smoothing=0.3):
        self.mass = mass
        self.stiffness_min = stiffness_min
        self.stiffness_max = stiffness_max
        self.damping_ratio = damping_ratio
        self.speed_threshold = speed_threshold
        self.max_accel = max_accel
        self.stiffness_smoothing = stiffness_smoothing

        self.position = np.zeros(2, dtype=np.float64)
        self.velocity = np.zeros(2, dtype=np.float64)
        self.acceleration = np.zeros(2, dtype=np.float64)
        self.current_stiffness = stiffness_min
        self._prev_stiffness = stiffness_min

    def update(self, target, dt):
        """Update spring-damper with frame-time compensation."""
        speed = np.linalg.norm(self.velocity)

        # Adaptive stiffness with smooth transition
        speed_factor = min(speed / self.speed_threshold, 1.0)
        target_k = self.stiffness_min + (self.stiffness_max - self.stiffness_min) * speed_factor
        # Smooth stiffness transition (no sudden jumps)
        s = self.stiffness_smoothing
        k = s * target_k + (1.0 - s) * self._prev_stiffness
        self._prev_stiffness = k
        self.current_stiffness = k

        # Damping: slightly over-damped at low speed for precision, under-damped at high
        if speed < 50:
            effective_ratio = min(self.damping_ratio + 0.15, 1.1)  # Over-damped = no overshoot
        else:
            effective_ratio = self.damping_ratio

        c = effective_ratio * 2.0 * np.sqrt(k * self.mass)

        # Spring-damper force
        displacement = self.position - target
        force = -k * displacement - c * self.velocity

        # Frame-time compensated acceleration
        raw_accel = force / self.mass
        # Limit acceleration to prevent jerks
        accel_norm = np.linalg.norm(raw_accel)
        if accel_norm > self.max_accel:
            raw_accel = raw_accel * (self.max_accel / accel_norm)
        self.acceleration = raw_accel

        # Semi-implicit Euler with dt clamping
        dt = min(dt, 0.05)  # Max 50ms step (20fps minimum)
        self.velocity += self.acceleration * dt
        self.position += self.velocity * dt

        return self.position.copy()

    def is_settled(self, threshold=0.5):
        return np.linalg.norm(self.velocity) < threshold

    def reset(self, position=None):
        self.velocity = np.zeros(2, dtype=np.float64)
        self.acceleration = np.zeros(2, dtype=np.float64)
        if position is not None:
            self.position = position.copy()
        else:
            self.position = np.zeros(2, dtype=np.float64)
        self.current_stiffness = self.stiffness_min
        self._prev_stiffness = self.stiffness_min


class PositionSmoother:
    """Final EMA pass on cursor position for silky-smooth output.

    Applied AFTER all physics — this is the last stage before
    the OS cursor is moved. It removes any remaining micro-jitter
    without adding perceptible latency.

    Uses a very high alpha (0.7-0.85) so it's responsive but smooth.
    """

    def __init__(self, alpha=0.75):
        self.alpha = alpha
        self._smoothed = None

    def smooth(self, position):
        """Apply final position smoothing."""
        if self._smoothed is None:
            self._smoothed = position.copy()
        else:
            self._smoothed = self.alpha * position + (1.0 - self.alpha) * self._smoothed
        return self._smoothed.copy()

    def reset(self, position=None):
        self._smoothed = position.copy() if position is not None else None


class MomentumThrow:
    """Detects flick gestures and applies decaying momentum.

    v3.1 improvements:
    - Direction-aware friction (maintains direction better)
    - Smoother activation/deactivation
    - Better flick detection (velocity spike, not just speed)
    """

    def __init__(self, friction=0.92, min_speed=800.0, max_momentum=2000.0):
        self.friction = friction
        self.min_speed = min_speed
        self.max_momentum = max_momentum
        self.momentum = np.zeros(2)
        self.is_active = False
        self._was_visible = True

    def update(self, velocity, hand_visible, dt):
        """Update momentum state."""
        speed = np.linalg.norm(velocity)

        if hand_visible and speed > self.min_speed:
            # Hand moving fast -> accumulate momentum
            self.momentum = velocity * 0.3
            # Clamp momentum
            m_norm = np.linalg.norm(self.momentum)
            if m_norm > self.max_momentum:
                self.momentum = self.momentum * (self.max_momentum / m_norm)
            self.is_active = True
        elif not hand_visible and self._was_visible and speed > self.min_speed * 0.5:
            # Hand just disappeared while moving -> throw!
            self.momentum = velocity * 0.5
            self.is_active = True
        elif self.is_active:
            # Apply friction decay
            self.momentum *= self.friction
            if np.linalg.norm(self.momentum) < 1.0:
                self.momentum = np.zeros(2)
                self.is_active = False

        self._was_visible = hand_visible
        return self.momentum * dt

    def reset(self):
        self.momentum = np.zeros(2)
        self.is_active = False
        self._was_visible = True


class EdgeGravity:
    """Soft magnetic pull toward screen edges and corners.

    v3.1: Smoother quadratic falloff, configurable per-edge.
    """

    def __init__(self, strength=15.0, edge_zone=0.08):
        self.strength = strength
        self.edge_zone = edge_zone

    def apply(self, position, screen_w, screen_h):
        """Apply edge gravity force."""
        offset = np.zeros(2)

        nx = position[0] / screen_w
        ny = position[1] / screen_h
        ez = self.edge_zone
        s = self.strength

        if nx < ez:
            offset[0] -= s * ((ez - nx) / ez) ** 2
        if nx > (1.0 - ez):
            offset[0] += s * ((nx - (1.0 - ez)) / ez) ** 2
        if ny < ez:
            offset[1] -= s * ((ez - ny) / ez) ** 2
        if ny > (1.0 - ez):
            offset[1] += s * ((ny - (1.0 - ez)) / ez) ** 2

        return offset


class LightJitterFilter:
    """Single-stage light EMA filter for direct tracking mode.

    Only kills micro-tremor — does NOT add heavy smoothing.
    High alpha (0.7-0.85) = very responsive, just removes camera noise.
    This is the right amount of filtering for 1:1 tracking.
    """

    def __init__(self, alpha=0.75):
        self.alpha = alpha
        self._smoothed = None

    def filter(self, raw):
        """Apply light EMA filter."""
        if self._smoothed is None:
            self._smoothed = raw.copy()
        else:
            self._smoothed = self.alpha * raw + (1.0 - self.alpha) * self._smoothed
        return self._smoothed.copy()

    def reset(self):
        self._smoothed = None


class DirectTracker:
    """Direct finger-to-screen mapping — v4.1 GOD-TIER edition.

    Pure accuracy. No complications.
    One Euro Filter + dead zone. That's it. And it's perfect.

    The One Euro Filter adapts to speed automatically:
      - Hand still → heavy smoothing → cursor is LOCKED (no drift)
      - Hand moving → light smoothing → cursor is GLUED to finger

    Plus an auto-precision layer: when the hand slows down, we engage
    a tighter dead zone so the cursor becomes pixel-perfect for targeting.

    Pipeline:
    1. One Euro Filter (adaptive) — the only filter you need
    2. Dead zone — if movement is below threshold, cursor FREEZES
    3. Direct map — finger [0..1] → screen [0..W, 0..H]
    4. Pixel dead zone — don't emit sub-pixel changes

    Result: cursor goes EXACTLY where your finger is. No lag. No jitter.
    """

    def __init__(self, screen_w, screen_h,
                 jitter_alpha=0.55,         # legacy compat
                 spring_alpha=0.55,         # legacy compat
                 smooth_alpha=0.55,         # legacy compat
                 movement_threshold=0.003,  # tight — cursor only moves on real intent
                 pixel_deadzone=1.0,        # 1px — pixel-perfect
                 mirror_x=False,
                 one_euro_mincutoff=1.2,    # Hz — lower = smoother at rest
                 one_euro_beta=1.5,         # higher = more responsive at speed
                 one_euro_dcutoff=1.0,
                 prediction_factor=0.0,     # OFF — prediction adds complications
                 use_hybrid=True,           # v5.0: One Euro + Kalman fusion
                 hybrid_process_noise=1.0,
                 hybrid_measurement_noise=0.05,
                 hybrid_fusion="adaptive",
                 hybrid_speed_ref=0.15):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.mirror_x = mirror_x

        # THE filter — One Euro. Nothing else needed.
        from .filters import OneEuroFilter2D
        self.one_euro = OneEuroFilter2D(
            mincutoff=one_euro_mincutoff,
            beta=one_euro_beta,
            dcutoff=one_euro_dcutoff,
        )

        # v5.0 — Hybrid One Euro + Kalman fusion filter.
        # Kalman dominates when the hand is still (rock-solid lock),
        # One Euro dominates at speed (responsiveness). Drop-in upgrade.
        self.use_hybrid = bool(use_hybrid)
        self.hybrid = None
        if self.use_hybrid:
            from .filters import HybridOneEuroKalman
            self.hybrid = HybridOneEuroKalman(
                mincutoff=one_euro_mincutoff,
                beta=one_euro_beta,
                dcutoff=one_euro_dcutoff,
                kalman_process_noise=hybrid_process_noise,
                kalman_measurement_noise=hybrid_measurement_noise,
                fusion=hybrid_fusion,
                speed_ref=hybrid_speed_ref,
            )

        # Legacy compat attributes
        self.jitter_x = LightJitterFilter(alpha=jitter_alpha)
        self.jitter_y = LightJitterFilter(alpha=jitter_alpha)
        self.spring_alpha = spring_alpha
        self.smoother = PositionSmoother(alpha=smooth_alpha)

        # Dead zone — if filtered movement is below this, cursor FREEZES
        # This is what makes it feel accurate — no drift when you're aiming
        self.movement_threshold = movement_threshold
        self._last_accepted_pos = None

        # Pixel dead zone — don't emit sub-pixel changes to OS
        self.pixel_deadzone = pixel_deadzone
        self._last_output_pos = None

        # Velocity tracking (for audio whoosh + auto-precision)
        self._prev_pos = None
        self.velocity = np.zeros(2)

        # Normalized filtered position (for scroll/volume/brightness)
        self._filtered_normalized = None

        # Current screen position
        self._screen_pos = None

        # Frame counter (for One Euro timing)
        self._frame_count = 0

        # Auto-precision: when hand slows down, tighten the dead zone
        self._auto_precision = False
        self._speed_ema = 0.0

    def update(self, finger_pos, dt):
        """Map finger position to screen cursor position.

        Pipeline: raw → One Euro → dead zone → DIRECT map → pixel gate

        Args:
            finger_pos: np.array([x, y]) in normalized coords [0..1]
            dt: frame time in seconds

        Returns:
            cursor_pos: np.array([x, y]) in screen pixels
        """
        self._frame_count += 1
        timestamp = self._frame_count / 30.0

        # Stage 1: Adaptive filter — hybrid (One Euro ⊕ Kalman) or pure One Euro
        if self.use_hybrid and self.hybrid is not None:
            filtered = self.hybrid.filter_np(finger_pos, timestamp)
        else:
            filtered = self.one_euro.filter_np(finger_pos, timestamp)

        # Track speed for auto-precision
        if self._prev_pos is not None:
            inst_speed = np.linalg.norm(filtered - self._prev_pos)
            self._speed_ema = 0.2 * inst_speed + 0.8 * self._speed_ema
        self._prev_pos = filtered.copy()

        # Stage 2: Dead zone — if movement is below threshold, cursor FREEZES
        # This is the key to accuracy — no drift when aiming
        effective_threshold = self.movement_threshold
        if self._auto_precision and self._speed_ema < 0.005:
            # Hand is slow — tighten the dead zone for pixel-perfect targeting
            effective_threshold = self.movement_threshold * 2.0

        if self._last_accepted_pos is not None:
            delta = np.linalg.norm(filtered - self._last_accepted_pos)
            if delta < effective_threshold:
                # Below dead zone — cursor FREEZES
                filtered = self._last_accepted_pos.copy()
            else:
                self._last_accepted_pos = filtered.copy()
        else:
            self._last_accepted_pos = filtered.copy()

        # Save normalized position for scroll/volume/brightness
        self._filtered_normalized = filtered.copy()

        # Stage 3: DIRECT map — 1:1, IMMEDIATE
        if self.mirror_x:
            screen_x = (1.0 - filtered[0]) * self.screen_w
        else:
            screen_x = filtered[0] * self.screen_w
        screen_y = filtered[1] * self.screen_h

        screen_x = np.clip(screen_x, 0, self.screen_w)
        screen_y = np.clip(screen_y, 0, self.screen_h)
        cursor_pos = np.array([screen_x, screen_y])

        # Stage 4: Pixel dead zone — prevent sub-pixel jitter
        if self._last_output_pos is not None:
            output_delta = np.linalg.norm(cursor_pos - self._last_output_pos)
            if output_delta < self.pixel_deadzone:
                cursor_pos = self._last_output_pos.copy()

        self._last_output_pos = cursor_pos.copy()
        self._screen_pos = cursor_pos.copy()

        # Track velocity (for audio feedback)
        if self._prev_pos is not None and dt > 0:
            self.velocity = (cursor_pos - self._last_output_pos) / dt if False else np.zeros(2)
        # Use filtered velocity instead (cleaner)
        self.velocity = np.array([self._speed_ema * self.screen_w / dt if dt > 0 else 0,
                                   self._speed_ema * self.screen_h / dt if dt > 0 else 0])

        return cursor_pos

    def reset(self, center=None):
        """Reset tracker state."""
        self.one_euro.reset()
        if self.hybrid is not None:
            self.hybrid.reset()
        self._last_accepted_pos = None
        self._last_output_pos = None
        self._prev_pos = None
        self._speed_ema = 0.0
        self.velocity = np.zeros(2)
        self._filtered_normalized = None
        self._screen_pos = None
        self._frame_count = 0
        if center is not None:
            self.smoother.reset(center)
        else:
            self.smoother.reset()

    @property
    def position(self):
        """Current screen position in pixels."""
        return self._screen_pos if self._screen_pos is not None else np.array([self.screen_w / 2, self.screen_h / 2])

    @property
    def filtered_normalized(self):
        """Normalized filtered position (0-1 range) after One Euro + dead zone."""
        return self._filtered_normalized if self._filtered_normalized is not None else np.array([0.5, 0.5])

    @property
    def speed(self):
        """Current hand speed (normalized units per frame)."""
        return self._speed_ema

    def set_precision_mode(self, enabled: bool):
        """Switch between normal and precision filtering on the fly."""
        from .filters import OneEuroFilter2D, HybridOneEuroKalman
        if enabled:
            # Precision: very smooth at rest, still responsive at speed
            self.one_euro = OneEuroFilter2D(mincutoff=0.5, beta=0.5, dcutoff=1.0)
            if self.hybrid is not None:
                self.hybrid = HybridOneEuroKalman(
                    mincutoff=0.5, beta=0.5, dcutoff=1.0,
                    kalman_process_noise=getattr(self, '_hybrid_q', 1.0),
                    kalman_measurement_noise=getattr(self, '_hybrid_r', 0.05),
                    fusion=getattr(self, '_hybrid_fusion', 'adaptive'),
                    speed_ref=getattr(self, '_hybrid_speed_ref', 0.15),
                )
            self.movement_threshold = 0.006
            self.pixel_deadzone = 2.0
            self._auto_precision = False  # manual precision mode
        else:
            # Normal: balanced accuracy + responsiveness
            self.one_euro = OneEuroFilter2D(mincutoff=1.2, beta=1.5, dcutoff=1.0)
            if self.hybrid is not None:
                self.hybrid = HybridOneEuroKalman(
                    mincutoff=1.2, beta=1.5, dcutoff=1.0,
                    kalman_process_noise=getattr(self, '_hybrid_q', 1.0),
                    kalman_measurement_noise=getattr(self, '_hybrid_r', 0.05),
                    fusion=getattr(self, '_hybrid_fusion', 'adaptive'),
                    speed_ref=getattr(self, '_hybrid_speed_ref', 0.15),
                )
            self.movement_threshold = 0.003
            self.pixel_deadzone = 1.0
            self._auto_precision = True  # re-enable auto-precision

    def toggle_hybrid(self, enabled: bool):
        """v5.0: switch between hybrid (One Euro ⊕ Kalman) and pure One Euro
        on the fly — used by the [k] hotkey and voice 'precision'/'kalman'."""
        self.use_hybrid = bool(enabled)

    def tune_filters(self, mincutoff: float = None, beta: float = None):
        """v5.0: live-tune the One Euro params from adaptive calibration.
        Applies to BOTH arms (pure One Euro and the hybrid's One Euro side)."""
        from .filters import OneEuroFilter2D
        mc = mincutoff if mincutoff is not None else self.one_euro._fx.mincutoff
        bt = beta if beta is not None else self.one_euro._fx.beta
        self.one_euro = OneEuroFilter2D(mincutoff=mc, beta=bt, dcutoff=1.0)
        if self.hybrid is not None:
            self.hybrid.one_euro = OneEuroFilter2D(mincutoff=mc, beta=bt, dcutoff=1.0)
