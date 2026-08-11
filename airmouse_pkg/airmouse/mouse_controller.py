"""
Mouse Controller — drives the OS cursor using pynput.

All position values are clamped to screen bounds and converted to
safe 32-bit ints to prevent Windows ctypes overflow errors.
"""

from pynput.mouse import Controller, Button


# Windows SetCursorPos expects signed 32-bit int.
# We clamp to a safe range to prevent overflow.
MAX_COORD = 2**15 - 1  # 32767 — more than any screen resolution


class MouseController:
    """Thin wrapper around pynput.mouse.Controller with bounds safety."""

    def __init__(self, screen_w=1920, screen_h=1080):
        self.mouse = Controller()
        self._dragging = False
        self.screen_w = screen_w
        self.screen_h = screen_h

    def _clamp(self, x, y):
        """Clamp coordinates to screen bounds and safe int range."""
        x = max(0, min(int(x), min(self.screen_w, MAX_COORD)))
        y = max(0, min(int(y), min(self.screen_h, MAX_COORD)))
        return x, y

    def move_to(self, x, y):
        """Move cursor to absolute screen position (clamped)."""
        self.mouse.position = self._clamp(x, y)

    def move_relative(self, dx, dy):
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
    def is_dragging(self):
        return self._dragging

    def scroll(self, clicks=-1):
        """Scroll. Negative = scroll up, Positive = scroll down."""
        self.mouse.scroll(0, clicks)

    @property
    def position(self):
        return self.mouse.position
