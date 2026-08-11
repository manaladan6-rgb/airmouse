"""
Keyboard Actions — Execute shortcuts and key combos for gestures.

Uses pynput.keyboard for cross-platform key simulation.
"""

import threading


class KeyboardActions:
    """Execute keyboard shortcuts triggered by gestures."""

    def __init__(self):
        self._keyboard = None
        self._key_module = None
        self._init()

    def _init(self):
        try:
            from pynput.keyboard import Controller, Key
            self._keyboard = Controller()
            self._key_module = Key
        except Exception:
            pass

    def _press_combo(self, keys):
        """Press and release a key combination in a thread."""
        if self._keyboard is None:
            return

        def _do():
            for k in keys:
                self._keyboard.press(k)
            for k in reversed(keys):
                self._keyboard.release(k)

        threading.Thread(target=_do, daemon=True).start()

    def minimize_window(self):
        """Win+Down = minimize window."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.cmd, self._key_module.down])

    def browser_back(self):
        """Alt+Left = browser back."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.alt, self._key_module.left])

    def browser_forward(self):
        """Alt+Right = browser forward."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.alt, self._key_module.right])

    def volume_up(self):
        """Volume up key."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.volume_up])

    def volume_down(self):
        """Volume down key."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.volume_down])

    def screenshot(self):
        """Win+Shift+S = screenshot (Windows)."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.cmd, self._key_module.shift, 's'])
