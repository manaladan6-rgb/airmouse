"""
Mouse Controller — pynput wrapper with drag, scroll, and bounds safety.

Lazy-imports pynput to allow headless/testing environments.
"""

MAX_COORD = 2**15 - 1


class MouseController:
    def __init__(self, screen_w=1920, screen_h=1080):
        self.mouse = None
        self._button = None
        self._dragging = False
        self.screen_w = screen_w
        self.screen_h = screen_h
        self._init()

    def _init(self):
        try:
            from pynput.mouse import Controller, Button
            self.mouse = Controller()
            self._button = Button
        except Exception:
            pass

    def _clamp(self, x, y):
        x = max(0, min(int(x), min(self.screen_w, MAX_COORD)))
        y = max(0, min(int(y), min(self.screen_h, MAX_COORD)))
        return x, y

    def move_to(self, x, y):
        if self.mouse is None: return
        self.mouse.position = self._clamp(x, y)

    def left_click(self):
        if self.mouse is None: return
        self.mouse.click(self._button.left, 1)

    def right_click(self):
        if self.mouse is None: return
        self.mouse.click(self._button.right, 1)

    def double_click(self):
        if self.mouse is None: return
        self.mouse.click(self._button.left, 2)

    def start_drag(self):
        if self.mouse is None: return
        if not self._dragging:
            self.mouse.press(self._button.left)
            self._dragging = True

    def stop_drag(self):
        if self.mouse is None: return
        if self._dragging:
            self.mouse.release(self._button.left)
            self._dragging = False

    @property
    def is_dragging(self):
        return self._dragging

    def scroll(self, amount):
        """Positive = scroll down, Negative = scroll up."""
        if self.mouse is None: return
        self.mouse.scroll(0, int(amount))

    @property
    def position(self):
        if self.mouse is None: return (0, 0)
        return self.mouse.position
