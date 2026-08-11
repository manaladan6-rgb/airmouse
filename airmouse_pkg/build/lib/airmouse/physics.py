"""
Iron Man Physics Engine — Spring-Damper + Adaptive + Momentum + Edge Gravity

Key design: FINGER-RELATIVE tracking, not hand-absolute.
Your hand stays still. Only finger movements drive the cursor.

Physics stack:
    1. Jitter filter       — kills hand tremor
    2. Home calibration    — establish rest position, track delta
    3. Exponential curve   — tiny finger moves → big cursor moves
    4. Adaptive spring     — k changes with speed (slow=precise, fast=snappy)
    5. Spring-damper       — Hooke's Law + viscous damping
    6. Momentum throw      — flick → cursor keeps gliding
    7. Edge gravity        — soft pull toward screen edges
    8. Screen clamp        — never go off-screen
"""

import numpy as np


class JitterFilter:
    """Low-pass exponential filter for tremor suppression."""

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

    Home auto-calibrates:
    - On first detection, sets home to current finger position
    - Slowly drifts toward current position (tracks hand drift)
    - Reset with 'r' key or fist gesture
    """

    def __init__(self, drift_rate=0.02):
        self.home = None           # The rest position [x, y]
        self.drift_rate = drift_rate  # How fast home follows slow drift
        self.is_calibrated = False

    def calibrate(self, current_pos):
        """Set home to current position."""
        self.home = current_pos.copy()
        self.is_calibrated = True

    def get_delta(self, current_pos):
        """Get finger displacement from home position.

        Returns:
            delta: np.ndarray — displacement from home [dx, dy]
        """
        if not self.is_calibrated:
            self.calibrate(current_pos)
            return np.zeros(2)

        delta = current_pos - self.home

        # Slowly drift home toward current (tracks natural hand drift)
        self.home += self.drift_rate * delta

        return delta

    def reset(self):
        self.home = None
        self.is_calibrated = False


class ExponentialCurve:
    """Maps linear finger delta to exponential cursor movement.

    Iron Man feel: tiny finger movements → large cursor jumps.
    Uses: sign(x) * |x|^power * scale

    Power < 1.0 = more amplification for small movements (Iron Man feel)
    Power = 1.0 = linear (normal)
    Power > 1.0 = less amplification (precision mode)
    """

    def __init__(self, power=0.6, scale=3.0):
        self.power = power
        self.scale = scale

    def map(self, delta):
        """Apply exponential sensitivity curve."""
        # sign(x) * |x|^power * scale
        mapped = np.sign(delta) * np.abs(delta) ** self.power * self.scale
        return mapped

    def map_with_deadzone(self, delta, deadzone=0.01):
        """Apply curve with deadzone (ignore tiny movements)."""
        result = np.zeros_like(delta)
        for i in range(len(delta)):
            if abs(delta[i]) < deadzone:
                result[i] = 0.0
            else:
                # Re-center after deadzone
                adjusted = delta[i] - np.sign(delta[i]) * deadzone
                result[i] = np.sign(adjusted) * abs(adjusted) ** self.power * self.scale
        return result


class AdaptiveSpringDamper:
    """Spring-damper with speed-adaptive stiffness.

    Slow movement  → low k  → precise, smooth cursor
    Fast movement  → high k → snappy, responsive cursor

    This gives the best of both worlds: pixel-perfect precision
    when you're being careful, and instant response when you flick.
    """

    def __init__(self, mass=0.8, stiffness_min=120.0, stiffness_max=400.0,
                 damping_ratio=0.85, speed_threshold=200.0):
        self.mass = mass
        self.stiffness_min = stiffness_min
        self.stiffness_max = stiffness_max
        self.damping_ratio = damping_ratio  # 1.0 = critically damped
        self.speed_threshold = speed_threshold

        self.position = np.zeros(2, dtype=np.float64)
        self.velocity = np.zeros(2, dtype=np.float64)
        self.current_stiffness = stiffness_min

    def update(self, target, dt):
        speed = np.linalg.norm(self.velocity)

        # Adaptive stiffness: ramp up with speed
        speed_factor = min(speed / self.speed_threshold, 1.0)
        k = self.stiffness_min + (self.stiffness_max - self.stiffness_min) * speed_factor
        self.current_stiffness = k

        # Damping = ratio * critical damping (2 * sqrt(k * m))
        c = self.damping_ratio * 2.0 * np.sqrt(k * self.mass)

        # Spring-damper force
        displacement = self.position - target
        force = -k * displacement - c * self.velocity
        acceleration = force / self.mass

        # Semi-implicit Euler
        self.velocity += acceleration * dt
        self.position += self.velocity * dt

        return self.position.copy()

    def is_settled(self, threshold=0.5):
        return np.linalg.norm(self.velocity) < threshold

    def reset(self, position=None):
        self.velocity = np.zeros(2, dtype=np.float64)
        if position is not None:
            self.position = position.copy()
        else:
            self.position = np.zeros(2, dtype=np.float64)


class MomentumThrow:
    """Detects flick gestures and applies decaying momentum.

    When you flick your finger fast and release (fist or stop),
    the cursor keeps gliding with exponential decay — like
    throwing a puck on ice.

    friction: 0.0 = infinite slide, 1.0 = instant stop
    """

    def __init__(self, friction=0.92, min_speed=800.0):
        self.friction = friction
        self.min_speed = min_speed
        self.momentum = np.zeros(2)
        self.is_active = False

    def update(self, velocity, hand_visible, dt):
        """Update momentum state.

        Args:
            velocity: Current cursor velocity (from spring-damper)
            hand_visible: Whether hand is still detected
            dt: Time step

        Returns:
            momentum_offset: np.ndarray to add to cursor position
        """
        speed = np.linalg.norm(velocity)

        if hand_visible and speed > self.min_speed:
            # Hand moving fast → accumulate momentum
            self.momentum = velocity * 0.3  # Fraction of velocity
            self.is_active = True
        elif not hand_visible and self.is_active:
            # Hand disappeared while moving → throw!
            self.is_active = True
        elif self.is_active:
            # Apply friction decay
            self.momentum *= self.friction
            if np.linalg.norm(self.momentum) < 1.0:
                self.momentum = np.zeros(2)
                self.is_active = False

        return self.momentum * dt

    def reset(self):
        self.momentum = np.zeros(2)
        self.is_active = False


class EdgeGravity:
    """Soft magnetic pull toward screen edges and corners.

    Makes it easier to hit close/minimize/maximize buttons.
    The pull is gentle — it only activates near edges.

    strength: 0.0 = no gravity, higher = stronger pull
    edge_zone: fraction of screen that has gravity (e.g. 0.08 = 8%)
    """

    def __init__(self, strength=15.0, edge_zone=0.08):
        self.strength = strength
        self.edge_zone = edge_zone

    def apply(self, position, screen_w, screen_h):
        """Apply edge gravity force.

        Returns:
            offset: np.ndarray — position offset from gravity
        """
        offset = np.zeros(2)

        # Normalize position to [0, 1]
        nx = position[0] / screen_w
        ny = position[1] / screen_h
        ez = self.edge_zone
        s = self.strength

        # Left edge
        if nx < ez:
            offset[0] -= s * ((ez - nx) / ez) ** 2
        # Right edge
        if nx > (1.0 - ez):
            offset[0] += s * ((nx - (1.0 - ez)) / ez) ** 2
        # Top edge
        if ny < ez:
            offset[1] -= s * ((ez - ny) / ez) ** 2
        # Bottom edge
        if ny > (1.0 - ez):
            offset[1] += s * ((ny - (1.0 - ez)) / ez) ** 2

        return offset
