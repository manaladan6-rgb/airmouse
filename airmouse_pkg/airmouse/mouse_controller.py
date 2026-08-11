"""
Mouse Controller — pynput wrapper with drag, scroll, and bounds safety.
"""

from pynput.mouse import Controller, Button

MAX_COORD = 2**15 - 1


class MouseController:
    def __init__(self, screen_w=1920, screen_h=1080):
        self.mouse = Controller()
        self._dragging = False
        self.screen_w = screen_w
        self.screen_h = screen_h

    def _clamp(self, x, y):
        x = max(0, min(int(x), min(self.screen_w, MAX_COORD)))
        y = max(0, min(int(y), min(self.screen_h, MAX_COORD)))
        return x, y

    def move_to(self, x, y):
        self.mouse.position = self._clamp(x, y)

    def left_click(self):
        self.mouse.click(Button.left, 1)

    def right_click(self):
        self.mouse.click(Button.right, 1)

    def double_click(self):
        self.mouse.click(Button.left, 2)

    def start_drag(self):
        if not self._dragging:
            self.mouse.press(Button.left)
            self._dragging = True

    def stop_drag(self):
        if self._dragging:
            self.mouse.release(Button.left)
            self._dragging = False

    @property
    def is_dragging(self):
        return self._dragging

    def scroll(self, amount):
        """Positive = scroll down, Negative = scroll up."""
        self.mouse.scroll(0, int(amount))

    @property
    def position(self):
        return self.mouse.position
