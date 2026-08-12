"""
Keyboard Actions v3.1 — Execute shortcuts and key combos for gestures.

Supports: window management, volume, brightness, media controls,
          task switching, screenshots, undo/redo, and more.

v3.1 improvements:
  - More robust ctypes SendInput with proper INPUT struct sizing
  - More keyboard actions (copy, paste, cut, select all)
  - Better error handling

Volume/media keys use Windows ctypes SendInput (pynput doesn't have them).
"""

import threading
import subprocess
import sys
import struct


# ─── Windows Media Key Codes (Virtual Key Codes) ───
VK_VOLUME_UP = 0xAF
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_MUTE = 0xAD
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1


def _win_send_key(vk_code):
    """Send a key press+release using Windows SendInput via ctypes.

    This works for media keys that pynput doesn't support.
    Uses proper INPUT structure with union padding for x64 compatibility.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_ulonglong),
            ]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_ulonglong),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [
                ("uMsg", ctypes.c_ulong),
                ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort),
            ]

        # Use a union for the input data
        class INPUT_UNION(ctypes.Union):
            _fields_ = [
                ("ki", KEYBDINPUT),
                ("mi", MOUSEINPUT),
                ("hi", HARDWAREINPUT),
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [
                ("type", ctypes.c_ulong),
                ("union", INPUT_UNION),
            ]

        # Key down
        down = INPUT()
        down.type = INPUT_KEYBOARD
        down.union.ki.wVk = vk_code
        down.union.ki.wScan = 0
        down.union.ki.dwFlags = 0
        down.union.ki.time = 0
        down.union.ki.dwExtraInfo = 0

        # Key up
        up = INPUT()
        up.type = INPUT_KEYBOARD
        up.union.ki.wVk = vk_code
        up.union.ki.wScan = 0
        up.union.ki.dwFlags = KEYEVENTF_KEYUP
        up.union.ki.time = 0
        up.union.ki.dwExtraInfo = 0

        SendInput = ctypes.windll.user32.SendInput
        SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        SendInput.restype = ctypes.c_uint

        inputs = (INPUT * 2)(down, up)
        result = SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))
    except Exception:
        pass


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
        def _do():
            try:
                self._keyboard.press(self._key_module.alt)
                self._keyboard.press(self._key_module.tab)
                self._keyboard.release(self._key_module.tab)
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

    # ─── Volume (Windows ctypes SendInput) ───

    def volume_up(self):
        """Volume up — uses Windows SendInput."""
        def _do():
            _win_send_key(VK_VOLUME_UP)
        threading.Thread(target=_do, daemon=True).start()

    def volume_down(self):
        """Volume down — uses Windows SendInput."""
        def _do():
            _win_send_key(VK_VOLUME_DOWN)
        threading.Thread(target=_do, daemon=True).start()

    def volume_mute(self):
        """Mute toggle — uses Windows SendInput."""
        def _do():
            _win_send_key(VK_VOLUME_MUTE)
        threading.Thread(target=_do, daemon=True).start()

    # ─── Brightness (OS-specific) ───

    def brightness_up(self):
        """Increase screen brightness."""
        if sys.platform == "win32":
            def _do():
                try:
                    subprocess.run(
                        ["powershell", "-Command",
                         "$m = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness; "
                         "$c = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods; "
                         "if($m -and $c){$c.WmiSetBrightness(1, [int]([math]::Min(100, $m.CurrentBrightness + 10)))}"],
                        capture_output=True, timeout=3
                    )
                except Exception:
                    pass
            threading.Thread(target=_do, daemon=True).start()
        else:
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
                    subprocess.run(
                        ["powershell", "-Command",
                         "$m = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness; "
                         "$c = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods; "
                         "if($m -and $c){$c.WmiSetBrightness(1, [int]([math]::Max(0, $m.CurrentBrightness - 10)))}"],
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

    # ─── Media Controls (Windows ctypes SendInput) ───

    def media_play_pause(self):
        """Play/Pause media — uses Windows SendInput."""
        def _do():
            _win_send_key(VK_MEDIA_PLAY_PAUSE)
        threading.Thread(target=_do, daemon=True).start()

    def media_next(self):
        """Next track — uses Windows SendInput."""
        def _do():
            _win_send_key(VK_MEDIA_NEXT)
        threading.Thread(target=_do, daemon=True).start()

    def media_prev(self):
        """Previous track — uses Windows SendInput."""
        def _do():
            _win_send_key(VK_MEDIA_PREV)
        threading.Thread(target=_do, daemon=True).start()

    # ─── Screenshot ───

    def screenshot(self):
        """Win+Shift+S = screenshot snip (Windows)."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.cmd, self._key_module.shift, 's'])

    # ─── Clipboard ───

    def copy(self):
        """Ctrl+C = copy."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.ctrl, 'c'])

    def paste(self):
        """Ctrl+V = paste."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.ctrl, 'v'])

    def cut(self):
        """Ctrl+X = cut."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.ctrl, 'x'])

    def select_all(self):
        """Ctrl+A = select all."""
        if self._key_module is None:
            return
        self._press_combo([self._key_module.ctrl, 'a'])

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

    def alt_tab(self):
        """Alt+Tab = switch window (alias for switch_window)."""
        self.switch_window()
