"""
Physics Engine — Spring-Damper System for Natural Cursor Motion

Instead of snapping the cursor to the raw hand position, we simulate a
second-order spring-damper system:

    F_spring  = -k * (x - x_target)       # Hooke's Law
    F_damping = -c * v                     # Viscous damping
    a = (F_spring + F_damping) / m         # Newton's 2nd Law

This gives the cursor MASS, MOMENTUM, and natural deceleration.
The cursor overshoots slightly and settles — just like a real physical object.

Tuning guide:
- k (spring stiffness):  Higher → cursor tracks hand faster.  Too high = snappy/jittery.
- c (damping):           Higher → less overshoot.  Too high = sluggish.  Critically damped = c = 2*sqrt(k*m)
- m (mass):              Higher → more momentum, slower response.  Too high = floaty.
"""

import numpy as np


class JitterFilter:
    """Low-pass filter to kill hand tremor / high-frequency noise.

    Uses exponential moving average:
        smoothed = alpha * raw + (1 - alpha) * prev_smoothed

    Alpha closer to 1.0 → less smoothing (more responsive, more jitter)
    Alpha closer to 0.0 → more smoothing (less jitter, more lag)
    """

    def __init__(self, alpha: float = 0.35):
        self.alpha = alpha
        self.smoothed: np.ndarray | None = None

    def filter(self, raw: np.ndarray) -> np.ndarray:
        if self.smoothed is None:
            self.smoothed = raw.copy()
        else:
            self.smoothed = self.alpha * raw + (1.0 - self.alpha) * self.smoothed
        return self.smoothed.copy()

    def reset(self):
        self.smoothed = None


class SpringDamper:
    """2D Spring-Damper physics for cursor movement.

    Each axis (x, y) is an independent 1D spring-damper:
        a = (-k * displacement - c * velocity) / m

    The system naturally produces:
    - Smooth acceleration toward target
    - Slight overshoot when hand moves fast
    - Natural deceleration and settling
    """

    def __init__(
        self,
        mass: float = 1.0,
        stiffness: float = 180.0,
        damping: float = 24.0,
    ):
        self.mass = mass
        self.stiffness = stiffness
        self.damping = damping

        # State vectors [x, y]
        self.position: np.ndarray = np.zeros(2, dtype=np.float64)
        self.velocity: np.ndarray = np.zeros(2, dtype=np.float64)

    def update(self, target: np.ndarray, dt: float) -> np.ndarray:
        """Advance physics by dt seconds toward target position.

        Args:
            target: Where the hand/finger is pointing (screen coords).
            dt:     Time step in seconds.

        Returns:
            New cursor position after physics step.
        """
        displacement = self.position - target
        force = -self.stiffness * displacement - self.damping * self.velocity
        acceleration = force / self.mass

        # Semi-implicit Euler integration (more stable than explicit)
        self.velocity += acceleration * dt
        self.position += self.velocity * dt

        return self.position.copy()

    def is_settled(self, threshold: float = 0.5) -> bool:
        """Check if the cursor has essentially stopped moving."""
        speed = np.linalg.norm(self.velocity)
        return speed < threshold

    def reset(self, position: np.ndarray | None = None):
        self.velocity = np.zeros(2, dtype=np.float64)
        if position is not None:
            self.position = position.copy()
        else:
            self.position = np.zeros(2, dtype=np.float64)


class VelocityTracker:
    """Tracks raw hand velocity for gesture detection (e.g. flick to click)."""

    def __init__(self, window: int = 5):
        self.window = window
        self.history: list[np.ndarray] = []

    def update(self, position: np.ndarray) -> np.ndarray:
        self.history.append(position.copy())
        if len(self.history) > self.window:
            self.history.pop(0)

        if len(self.history) < 2:
            return np.zeros(2)

        # Average velocity over the window
        deltas = [
            self.history[i] - self.history[i - 1]
            for i in range(1, len(self.history))
        ]
        return np.mean(deltas, axis=0)

    def reset(self):
        self.history = []
