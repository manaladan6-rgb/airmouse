"""
Keyboard Actions v3.2 — Execute shortcuts and key combos for gestures.

Supports: window management, volume, brightness, media controls,
          task switching, screenshots, undo/redo, and more.

v3.2: Full cross-platform media key support:
  - Windows: ctypes SendInput (VK codes)
  - Linux:   xdotool or dbus-send (PulseAudio)
  - macOS:   osascript (AppleScript)
"""

import threading
import subprocess
import sys


# ─── Windows Media Key Codes (Virtual Key Codes) ───
VK_VOLUME_UP = 0xAF
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_MUTE = 0xAD
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1


def _win_send_key(vk_code):
    """Send a key press+release using Windows SendInput via ctypes."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_ulonglong),
            ]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_ulonglong),
            ]

        class INPUT_UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]

        down = INPUT()
        down.type = INPUT_KEYBOARD
        down.union.ki.wVk = vk_code

        up = INPUT()
        up.type = INPUT_KEYBOARD
        up.union.ki.wVk = vk_code
        up.union.ki.dwFlags = KEYEVENTF_KEYUP

        SendInput = ctypes.windll.user32.SendInput
        SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        SendInput.restype = ctypes.c_uint

        inputs = (INPUT * 2)(down, up)
        SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))
    except Exception:
        pass


def _linux_media_key(key_name):
    """Send media key on Linux using xdotool or dbus."""
    # Try xdotool first (most common)
    xdotool_keys = {
        "volume_up": "XF86AudioRaiseVolume",
        "volume_down": "XF86AudioLowerVolume",
        "volume_mute": "XF86AudioMute",
        "play_pause": "XF86AudioPlay",
        "next": "XF86AudioNext",
        "prev": "XF86AudioPrev",
    }
    xk = xdotool_keys.get(key_name)
    if xk:
        try:
            subprocess.run(
                ["xdotool", "key", xk],
                capture_output=True, timeout=2,
            )
            return
        except Exception:
            pass

    # Fallback: dbus-send to PulseAudio
    dbus_actions = {
        "volume_up": "org.PulseAudio.Server.Lookup1 volume-up",
        "volume_down": "org.PulseAudio.Server.Lookup1 volume-down",
        "volume_mute": "org.PulseAudio.Server.Lookup1 mute",
    }
    # Fallback: pactl for volume
    pactl_actions = {
        "volume_up": ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+5%"],
        "volume_down": ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-5%"],
        "volume_mute": ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
    }
    cmd = pactl_actions.get(key_name)
    if cmd:
        try:
            subprocess.run(cmd, capture_output=True, timeout=2)
        except Exception:
            pass


def _macos_media_key(key_name):
    """Send media key on macOS using osascript or shortcut."""
    # Use osascript for volume
    if key_name == "volume_up":
        try:
            subprocess.run(
                ["osascript", "-e", "set volume output volume (output volume of (get volume settings) + 10)"],
                capture_output=True, timeout=2,
            )
        except Exception:
            pass
    elif key_name == "volume_down":
        try:
            subprocess.run(
                ["osascript", "-e", "set volume output volume (output volume of (get volume settings) - 10)"],
                capture_output=True, timeout=2,
            )
        except Exception:
            pass
    elif key_name == "volume_mute":
        try:
            subprocess.run(
                ["osascript", "-e", "set volume output muted true"],
                capture_output=True, timeout=2,
            )
        except Exception:
            pass
    else:
        # For play_pause, next, prev — use shortcut CLI or Hammerspoon
        key_map = {
            "play_pause": "play",
            "next": "fastforward",
            "prev": "rewind",
        }
        apple_key = key_map.get(key_name)
        if apple_key:
            try:
                subprocess.run(
                    ["osascript", "-e", f'tell application "System Events" to key code {{"{apple_key}"}}'],
                    capture_output=True, timeout=2,
                )
            except Exception:
                pass


def _send_media_key(key_name):
    """Cross-platform media key dispatch."""
    if sys.platform == "win32":
        vk_map = {
            "volume_up": VK_VOLUME_UP,
            "volume_down": VK_VOLUME_DOWN,
            "volume_mute": VK_VOLUME_MUTE,
            "play_pause": VK_MEDIA_PLAY_PAUSE,
            "next": VK_MEDIA_NEXT,
            "prev": VK_MEDIA_PREV,
        }
        vk = vk_map.get(key_name)
        if vk:
            _win_send_key(vk)
    elif sys.platform == "linux":
        _linux_media_key(key_name)
    elif sys.platform == "darwin":
        _macos_media_key(key_name)


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
        if self._key_module is None: return
        self._press_combo([self._key_module.cmd, self._key_module.down])

    def maximize_window(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.cmd, self._key_module.up])

    def close_window(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.alt, self._key_module.f4])

    def switch_window(self):
        if self._key_module is None: return
        def _do():
            try:
                self._keyboard.press(self._key_module.alt)
                self._keyboard.press(self._key_module.tab)
                self._keyboard.release(self._key_module.tab)
                import time; time.sleep(0.15)
                self._keyboard.release(self._key_module.alt)
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    def show_desktop(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.cmd, 'd'])

    # ─── Browser / Navigation ───

    def browser_back(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.alt, self._key_module.left])

    def browser_forward(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.alt, self._key_module.right])

    # ─── Volume (Cross-Platform) ───

    def volume_up(self):
        def _do(): _send_media_key("volume_up")
        threading.Thread(target=_do, daemon=True).start()

    def volume_down(self):
        def _do(): _send_media_key("volume_down")
        threading.Thread(target=_do, daemon=True).start()

    def volume_mute(self):
        def _do(): _send_media_key("volume_mute")
        threading.Thread(target=_do, daemon=True).start()

    # ─── Brightness (OS-specific) ───

    def brightness_up(self):
        if sys.platform == "win32":
            def _do():
                try:
                    subprocess.run(
                        ["powershell", "-Command",
                         "$m = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness; "
                         "$c = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods; "
                         "if($m -and $c){$c.WmiSetBrightness(1, [int]([math]::Min(100, $m.CurrentBrightness + 10)))}"],
                        capture_output=True, timeout=3)
                except Exception: pass
            threading.Thread(target=_do, daemon=True).start()
        elif sys.platform == "linux":
            def _do():
                try:
                    subprocess.run(["brightnessctl", "set", "10%+"], capture_output=True, timeout=2)
                except Exception: pass
            threading.Thread(target=_do, daemon=True).start()
        elif sys.platform == "darwin":
            def _do():
                try:
                    subprocess.run(["osascript", "-e", "tell application \"System Events\" to key code 144"],
                                   capture_output=True, timeout=2)
                except Exception: pass
            threading.Thread(target=_do, daemon=True).start()

    def brightness_down(self):
        if sys.platform == "win32":
            def _do():
                try:
                    subprocess.run(
                        ["powershell", "-Command",
                         "$m = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness; "
                         "$c = Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods; "
                         "if($m -and $c){$c.WmiSetBrightness(1, [int]([math]::Max(0, $m.CurrentBrightness - 10)))}"],
                        capture_output=True, timeout=3)
                except Exception: pass
            threading.Thread(target=_do, daemon=True).start()
        elif sys.platform == "linux":
            def _do():
                try:
                    subprocess.run(["brightnessctl", "set", "10%-"], capture_output=True, timeout=2)
                except Exception: pass
            threading.Thread(target=_do, daemon=True).start()
        elif sys.platform == "darwin":
            def _do():
                try:
                    subprocess.run(["osascript", "-e", "tell application \"System Events\" to key code 145"],
                                   capture_output=True, timeout=2)
                except Exception: pass
            threading.Thread(target=_do, daemon=True).start()

    # ─── Media Controls (Cross-Platform) ───

    def media_play_pause(self):
        def _do(): _send_media_key("play_pause")
        threading.Thread(target=_do, daemon=True).start()

    def media_next(self):
        def _do(): _send_media_key("next")
        threading.Thread(target=_do, daemon=True).start()

    def media_prev(self):
        def _do(): _send_media_key("prev")
        threading.Thread(target=_do, daemon=True).start()

    # ─── Screenshot ───

    def screenshot(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.cmd, self._key_module.shift, 's'])

    # ─── Clipboard ───

    def copy(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.ctrl, 'c'])

    def paste(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.ctrl, 'v'])

    def cut(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.ctrl, 'x'])

    def select_all(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.ctrl, 'a'])

    # ─── Undo / Redo ───

    def undo(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.ctrl, 'z'])

    def redo(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.ctrl, 'y'])

    # ─── Misc ───

    def refresh_page(self):
        if self._key_module is None: return
        self._press_key(self._key_module.f5)

    def new_tab(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.ctrl, 't'])

    def close_tab(self):
        if self._key_module is None: return
        self._press_combo([self._key_module.ctrl, 'w'])

    def alt_tab(self):
        self.switch_window()
