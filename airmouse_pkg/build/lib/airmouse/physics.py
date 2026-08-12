"""
Physics Engine v3.2 — Direct Tracking + Spring-Damper + Adaptive + Momentum + Edge Gravity
+ Dual-Stage Jitter + Velocity Prediction + Position Smoothing + Acceleration Limiting

Two tracking modes:

  DIRECT (default v3.2):
    Finger position maps 1:1 to screen position.
    Cursor follows your finger exactly — what you see is what you get.
    Light jitter suppression + stiff spring = accurate + smooth.

  IRONMAN (legacy v3.1):
    FINGER-RELATIVE tracking — cursor driven by delta from home position.
    Tiny finger moves -> large cursor jumps (exponential curve).
    Cursor drifts toward center when hand is still.

Physics stack (DIRECT mode):
    1. Light jitter filter  — micro-tremor kill only (no heavy macro smoothing)
    2. Direct map           — finger [0..1] -> screen [0..W, 0..H]
    3. Adaptive spring      — very stiff (k=600-1200) for near-instant tracking
    4. Acceleration limit   — prevent sudden jerks
    5. Position smoother    — light final EMA (alpha=0.85)
    6. Screen clamp         — never go off-screen

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
    """Direct finger-to-screen mapping that feels like a hardware mouse.

    Smooth, slow, sweet — no shaking, no jitter, no drift.

    Pipeline:
    1. Heavy EMA jitter filter — kills camera noise at the source
    2. Movement threshold (noise gate) — if hand barely moved, cursor stays still
       (just like a physical mouse sensor — needs real movement to register)
    3. Smooth EMA spring — lazy following, feels like dragging through honey
    4. Final position smoother — removes last pixel of jitter
    5. Sub-pixel deadzone — if output hasn't moved 2+ pixels, don't move OS cursor

    The result: cursor that glides like a real mouse, not a shaky webcam tracker.
    """

    def __init__(self, screen_w, screen_h,
                 jitter_alpha=0.35,
                 spring_alpha=0.30,
                 smooth_alpha=0.70,
                 movement_threshold=0.008,
                 pixel_deadzone=2.0,
                 mirror_x=False):
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.mirror_x = mirror_x

        # Heavy jitter filter — kills camera noise before it reaches the cursor
        # alpha=0.35 means: 35% new input + 65% old state per frame
        # This creates a very smooth, lazy response that swallows tremor
        self.jitter_x = LightJitterFilter(alpha=jitter_alpha)
        self.jitter_y = LightJitterFilter(alpha=jitter_alpha)

        # Smooth EMA spring — lazy, buttery following
        # alpha=0.30 means: 30% new target + 70% old position per frame
        # Feels like moving through honey — smooth and controlled
        self.spring_alpha = spring_alpha
        self._spring_pos = None

        # Final position smoother — last line of defense against jitter
        self.smoother = PositionSmoother(alpha=smooth_alpha)

        # Movement threshold (noise gate) in normalized coords
        # If the filtered finger position hasn't moved this much, ignore it
        # This simulates the physical click of a real mouse sensor
        self.movement_threshold = movement_threshold
        self._last_accepted_pos = None  # Last position that passed the gate

        # Pixel deadzone — if output hasn't moved this many pixels, don't update OS cursor
        # Prevents sub-pixel jitter from reaching the screen
        self.pixel_deadzone = pixel_deadzone
        self._last_output_pos = None  # Last position sent to OS

        # Velocity tracking (for audio whoosh)
        self._prev_pos = None
        self.velocity = np.zeros(2)

    def update(self, finger_pos, dt):
        """Map finger position to screen cursor position.

        Pipeline: raw → jitter filter → noise gate → screen map → spring → smooth → pixel gate

        Args:
            finger_pos: np.array([x, y]) in normalized coords [0..1]
            dt: frame time in seconds

        Returns:
            cursor_pos: np.array([x, y]) in screen pixels, or None if below pixel deadzone
        """
        # Stage 1: Heavy jitter filter — swallow camera noise
        fx = self.jitter_x.filter(np.array([finger_pos[0]]))[0]
        fy = self.jitter_y.filter(np.array([finger_pos[1]]))[0]
        filtered = np.array([fx, fy])

        # Stage 2: Movement threshold (noise gate)
        # If hand barely moved, feed the last accepted position instead
        # This is what makes it feel like a real mouse — no micro-tremor gets through
        if self._last_accepted_pos is not None:
            delta = np.linalg.norm(filtered - self._last_accepted_pos)
            if delta < self.movement_threshold:
                # Hand is essentially still — use last accepted position
                filtered = self._last_accepted_pos.copy()
            else:
                # Real movement detected — accept and update
                self._last_accepted_pos = filtered.copy()
        else:
            self._last_accepted_pos = filtered.copy()

        # Map to screen coordinates
        if self.mirror_x:
            screen_x = (1.0 - filtered[0]) * self.screen_w
        else:
            screen_x = filtered[0] * self.screen_w
        screen_y = filtered[1] * self.screen_h
        target = np.array([screen_x, screen_y])

        # Stage 3: Smooth EMA spring — lazy, buttery following
        if self._spring_pos is None:
            self._spring_pos = target.copy()
        else:
            self._spring_pos = (self.spring_alpha * target +
                                (1.0 - self.spring_alpha) * self._spring_pos)

        # Clamp to screen
        cursor_pos = self._spring_pos.copy()
        cursor_pos[0] = np.clip(cursor_pos[0], 0, self.screen_w)
        cursor_pos[1] = np.clip(cursor_pos[1], 0, self.screen_h)

        # Stage 4: Final position smoother
        smoothed = self.smoother.smooth(cursor_pos)

        # Stage 5: Pixel deadzone — if output hasn't moved enough, don't update cursor
        # This prevents sub-pixel jitter from reaching the OS
        if self._last_output_pos is not None:
            output_delta = np.linalg.norm(smoothed - self._last_output_pos)
            if output_delta < self.pixel_deadzone:
                # Below pixel deadzone — return last output position (cursor stays still)
                smoothed = self._last_output_pos.copy()

        self._last_output_pos = smoothed.copy()

        # Track velocity for audio feedback
        if self._prev_pos is not None and dt > 0:
            self.velocity = (smoothed - self._prev_pos) / dt
        self._prev_pos = smoothed.copy()

        return smoothed

    def reset(self, center=None):
        """Reset tracker state."""
        self.jitter_x.reset()
        self.jitter_y.reset()
        self._spring_pos = None
        self._last_accepted_pos = None
        self._last_output_pos = None
        self._prev_pos = None
        self.velocity = np.zeros(2)
        if center is not None:
            self.smoother.reset(center)
        else:
            self.smoother.reset()

    @property
    def position(self):
        return self._spring_pos if self._spring_pos is not None else np.array([self.screen_w / 2, self.screen_h / 2])
