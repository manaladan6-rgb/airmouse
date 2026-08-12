"""
Auto-Start Manager — Configure AirMouse to start on boot.

Windows:  Registry key in HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
Linux:    ~/.config/autostart/airmouse.desktop
macOS:    ~/Library/LaunchAgents/com.airmouse.plist
"""

import os
import sys


def _get_executable():
    """Get the path to the airmouse executable."""
    # If running from a PyInstaller bundle
    if getattr(sys, 'frozen', False):
        return sys.executable
    # If running as a Python package
    return sys.executable


def is_auto_start_enabled():
    """Check if AirMouse is configured to start on boot."""
    if sys.platform == "win32":
        return _is_enabled_windows()
    elif sys.platform == "linux":
        return _is_enabled_linux()
    elif sys.platform == "darwin":
        return _is_enabled_macos()
    return False


def enable_auto_start():
    """Enable auto-start on boot."""
    if sys.platform == "win32":
        return _enable_windows()
    elif sys.platform == "linux":
        return _enable_linux()
    elif sys.platform == "darwin":
        return _enable_macos()
    return False


def disable_auto_start():
    """Disable auto-start on boot."""
    if sys.platform == "win32":
        return _disable_windows()
    elif sys.platform == "linux":
        return _disable_linux()
    elif sys.platform == "darwin":
        return _disable_macos()
    return False


# ─── Windows ───

def _is_enabled_windows():
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ,
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AirMouse")
            winreg.CloseKey(key)
            return bool(value)
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except Exception:
        return False


def _enable_windows():
    try:
        import winreg
        exe = _get_executable()
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_WRITE,
        )
        winreg.SetValueEx(key, "AirMouse", 0, winreg.REG_SZ, f'"{exe}" --skip')
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def _disable_windows():
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_WRITE,
        )
        try:
            winreg.DeleteValue(key, "AirMouse")
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


# ─── Linux ───

_LINUX_DESKTOP = os.path.expanduser("~/.config/autostart/airmouse.desktop")


def _is_enabled_linux():
    return os.path.exists(_LINUX_DESKTOP)


def _enable_linux():
    try:
        os.makedirs(os.path.dirname(_LINUX_DESKTOP), exist_ok=True)
        exe = _get_executable()
        content = f"""[Desktop Entry]
Type=Application
Name=AirMouse
Comment=AirMouse - Gesture-controlled cursor
Exec={exe} --skip
Icon=airmouse
Terminal=false
Categories=Utility;
X-GNOME-Autostart-enabled=true
"""
        with open(_LINUX_DESKTOP, "w") as f:
            f.write(content)
        return True
    except Exception:
        return False


def _disable_linux():
    try:
        if os.path.exists(_LINUX_DESKTOP):
            os.unlink(_LINUX_DESKTOP)
        return True
    except Exception:
        return False


# ─── macOS ───

_MACOS_PLIST = os.path.expanduser("~/Library/LaunchAgents/com.airmouse.plist")


def _is_enabled_macos():
    return os.path.exists(_MACOS_PLIST)


def _enable_macos():
    try:
        os.makedirs(os.path.dirname(_MACOS_PLIST), exist_ok=True)
        exe = _get_executable()
        content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.airmouse</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>--skip</string>
    </array>
    <key>RunAtLoad</key><true/>
</dict>
</plist>"""
        with open(_MACOS_PLIST, "w") as f:
            f.write(content)
        return True
    except Exception:
        return False


def _disable_macos():
    try:
        if os.path.exists(_MACOS_PLIST):
            os.unlink(_MACOS_PLIST)
        return True
    except Exception:
        return False
