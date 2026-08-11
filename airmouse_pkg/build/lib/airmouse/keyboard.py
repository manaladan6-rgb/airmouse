"""
Keyboard Actions — Execute shortcuts and key combos for gestures.

Supports: window management, volume, brightness, media controls,
          task switching, screenshots, undo/redo, and more.
"""

import threading
import subprocess
import sys


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
            try:
                for k in keys:
                    self._keyboard.press(k)
                for k in reversed(keys):
                    self._keyboard.release(k)
            except Exception:
                pass

        threading.Thread(target=_do, daemon=True).start()

    def _press_key(self, key):
        """Press and release a single key."""
        if self._keyboard is None:
            return

        def _do():
            try:
                self._keyboard.press(key)
                self._keyboard.release(key)
            except Exception:
                pass

        threading.Thread(target=_do, daemon=True).start()

    # ─── Window Management ───

    def minimize_window(self):
        """Win+Down = minimize window."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.cmd, self._key_module.down])

    def maximize_window(self):
        """Win+Up = maximize/restore window."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.cmd, self._key_module.up])

    def close_window(self):
        """Alt+F4 = close window."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.alt, self._key_module.f4])

    def switch_window(self):
        """Alt+Tab = task switcher."""
        if self._key_module is None:
            return
        # Press Alt+Tab (don't release Alt immediately so switcher stays open)
        def _do():
            try:
                self._keyboard.press(self._key_module.alt)
                self._keyboard.press(self._key_module.tab)
                self._keyboard.release(self._key_module.tab)
                # Release Alt after a short delay so switcher shows
                import time
                time.sleep(0.15)
                self._keyboard.release(self._key_module.alt)
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    def show_desktop(self):
        """Win+D = toggle show desktop."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.cmd, 'd'])

    # ─── Browser / Navigation ───

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

    # ─── Volume ───

    def volume_up(self):
        """Volume up key."""
        if self._key_module is None:
            return
        self._press_key(self._key_module.volume_up)

    def volume_down(self):
        """Volume down key."""
        if self._key_module is None:
            return
        self._press_key(self._key_module.volume_down)

    def volume_mute(self):
        """Mute toggle."""
        if self._key_module is None:
            return
        self._press_key(self._key_module.volume_mute)

    # ─── Brightness (OS-specific) ───

    def brightness_up(self):
        """Increase screen brightness."""
        if sys.platform == "win32":
            # On Windows, use PowerShell with WMI
            def _do():
                try:
                    # Try using wmi - increment brightness by 10%
                    result = subprocess.run(
                        ["powershell", "-Command",
                         "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, [int]([math]::Min(100, (Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness + 10)))"],
                        capture_output=True, timeout=3
                    )
                except Exception:
                    pass
            threading.Thread(target=_do, daemon=True).start()
        else:
            # Linux: use xdotool or brightnessctl
            def _do():
                try:
                    subprocess.run(["brightnessctl", "set", "10%+"],
                                   capture_output=True, timeout=2)
                except Exception:
                    pass
            threading.Thread(target=_do, daemon=True).start()

    def brightness_down(self):
        """Decrease screen brightness."""
        if sys.platform == "win32":
            def _do():
                try:
                    result = subprocess.run(
                        ["powershell", "-Command",
                         "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, [int]([math]::Max(0, (Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness - 10)))"],
                        capture_output=True, timeout=3
                    )
                except Exception:
                    pass
            threading.Thread(target=_do, daemon=True).start()
        else:
            def _do():
                try:
                    subprocess.run(["brightnessctl", "set", "10%-"],
                                   capture_output=True, timeout=2)
                except Exception:
                    pass
            threading.Thread(target=_do, daemon=True).start()

    # ─── Media Controls ───

    def media_play_pause(self):
        """Play/Pause media."""
        if self._key_module is None:
            return
        self._press_key(self._key_module.media_play_pause)

    def media_next(self):
        """Next track."""
        if self._key_module is None:
            return
        self._press_key(self._key_module.media_next)

    def media_prev(self):
        """Previous track."""
        if self._key_module is None:
            return
        self._press_key(self._key_module.media_previous)

    # ─── Screenshot ───

    def screenshot(self):
        """Win+Shift+S = screenshot snip (Windows)."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.cmd, self._key_module.shift, 's'])

    # ─── Undo / Redo ───

    def undo(self):
        """Ctrl+Z = undo."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.ctrl, 'z'])

    def redo(self):
        """Ctrl+Y = redo."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.ctrl, 'y'])

    # ─── Misc ───

    def refresh_page(self):
        """F5 = refresh."""
        if self._key_module is None:
            return
        self._press_key(self._key_module.f5)

    def new_tab(self):
        """Ctrl+T = new tab."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.ctrl, 't'])

    def close_tab(self):
        """Ctrl+W = close tab."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.ctrl, 'w'])
