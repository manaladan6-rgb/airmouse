"""
Mouse Controller — drives the OS cursor using pynput.

Supports:
- Smooth cursor movement to any screen position
- Left click / right click
- Click-and-drag
- Scroll
"""

import time
from pynput.mouse import Controller, Button


class MouseController:
    """Thin wrapper around pynput.mouse.Controller."""

    def __init__(self):
        self.mouse = Controller()
        self._dragging = False

    def move_to(self, x: float, y: float):
        """Move cursor to absolute screen position."""
        self.mouse.position = (int(x), int(y))

    def move_relative(self, dx: float, dy: float):
        """Move cursor by a relative offset."""
        self.mouse.move(int(dx), int(dy))

    def left_click(self):
        """Single left click at current position."""
        self.mouse.click(Button.left, 1)

    def right_click(self):
        """Single right click at current position."""
        self.mouse.click(Button.right, 1)

    def double_click(self):
        """Double left click at current position."""
        self.mouse.click(Button.left, 2)

    def press_left(self):
        """Press and hold left button (for drag)."""
        if not self._dragging:
            self.mouse.press(Button.left)
            self._dragging = True

    def release_left(self):
        """Release left button."""
        if self._dragging:
            self.mouse.release(Button.left)
            self._dragging = False

    @property
    def is_dragging(self) -> bool:
        return self._dragging

    def scroll(self, clicks: int = -1):
        """Scroll. Negative = scroll up, Positive = scroll down."""
        self.mouse.scroll(0, clicks)

    @property
    def position(self) -> tuple[int, int]:
        return self.mouse.position
