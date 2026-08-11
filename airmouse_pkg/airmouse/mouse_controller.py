"""
Mouse Controller — drives the OS cursor using pynput.
"""

from pynput.mouse import Controller, Button


class MouseController:
    """Thin wrapper around pynput.mouse.Controller."""

    def __init__(self):
        self.mouse = Controller()
        self._dragging = False

    def move_to(self, x, y):
        self.mouse.position = (int(x), int(y))

    def move_relative(self, dx, dy):
        self.mouse.move(int(dx), int(dy))

    def left_click(self):
        self.mouse.click(Button.left, 1)

    def right_click(self):
        self.mouse.click(Button.right, 1)

    def double_click(self):
        self.mouse.click(Button.left, 2)

    def press_left(self):
        if not self._dragging:
            self.mouse.press(Button.left)
            self._dragging = True

    def release_left(self):
        if self._dragging:
            self.mouse.release(Button.left)
            self._dragging = False

    @property
    def is_dragging(self):
        return self._dragging

    def scroll(self, clicks=-1):
        self.mouse.scroll(0, clicks)

    @property
    def position(self):
        return self.mouse.position
