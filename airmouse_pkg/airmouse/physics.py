"""
Physics Engine — Spring-Damper System for Natural Cursor Motion

    F_spring  = -k * (x - x_target)       # Hooke's Law
    F_damping = -c * v                     # Viscous damping
    a = (F_spring + F_damping) / m         # Newton's 2nd Law
"""

import numpy as np


class JitterFilter:
    """Low-pass filter to kill hand tremor / high-frequency noise.

    smoothed = alpha * raw + (1 - alpha) * prev_smoothed
    """

    def __init__(self, alpha: float = 0.35):
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


class SpringDamper:
    """2D Spring-Damper physics for cursor movement.

    a = (-k * displacement - c * velocity) / m
    """

    def __init__(self, mass=1.0, stiffness=180.0, damping=24.0):
        self.mass = mass
        self.stiffness = stiffness
        self.damping = damping
        self.position = np.zeros(2, dtype=np.float64)
        self.velocity = np.zeros(2, dtype=np.float64)

    def update(self, target, dt):
        displacement = self.position - target
        force = -self.stiffness * displacement - self.damping * self.velocity
        acceleration = force / self.mass
        self.velocity += acceleration * dt
        self.position += self.velocity * dt
        return self.position.copy()

    def is_settled(self, threshold=0.5):
        speed = np.linalg.norm(self.velocity)
        return speed < threshold

    def reset(self, position=None):
        self.velocity = np.zeros(2, dtype=np.float64)
        if position is not None:
            self.position = position.copy()
        else:
            self.position = np.zeros(2, dtype=np.float64)


class VelocityTracker:
    """Tracks raw hand velocity for gesture detection."""

    def __init__(self, window=5):
        self.window = window
        self.history = []

    def update(self, position):
        self.history.append(position.copy())
        if len(self.history) > self.window:
            self.history.pop(0)
        if len(self.history) < 2:
            return np.zeros(2)
        deltas = [
            self.history[i] - self.history[i - 1]
            for i in range(1, len(self.history))
        ]
        return np.mean(deltas, axis=0)

    def reset(self):
        self.history = []
