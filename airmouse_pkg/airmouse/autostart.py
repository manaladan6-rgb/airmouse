"""
Auto-Start Manager — Configure AirMouse to start on boot.

Windows:  Registry key in HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
Linux:    ~/.config/autostart/airmouse.desktop
macOS:    ~/Library/LaunchAgents/com.airmouse.plist

v19.0.3 R3d rebuild (OPERATION AIRLOCK): the generated entry must carry a
USABLE launch command.  The old defect wrote ``Exec=<python> --skip`` —
an interpreter flag with no program, which started NOTHING.  The entry
now launches the REAL CLI::

    <python> -m airmouse --skip

(the ``--skip`` only suppresses the first-run tutorial), and
:func:`verify_launch_command` honestly confirms the launch command is
usable: the interpreter path exists, the command actually starts the
airmouse module, and ``import airmouse`` resolves in this environment.
"""

import importlib.util
import os
import sys

_LAUNCH_MODULE = "airmouse"
_LAUNCH_TUTORIAL_FLAG = "--skip"

__all__ = [
    "launch_args", "launch_command", "build_desktop_entry",
    "build_plist", "build_windows_value", "verify_launch_command",
    "is_auto_start_enabled", "enable_auto_start", "disable_auto_start",
]


def _get_executable():
    """Get the path to the airmouse executable."""
    # If running from a PyInstaller bundle
    if getattr(sys, 'frozen', False):
        return sys.executable
    # If running as a Python package
    return sys.executable


def _quote(arg: str) -> str:
    """Quote one launch argument (double quotes — valid for .desktop
    ``Exec=``, the Windows ``Run`` key and a macOS ``sh -c`` string)."""
    arg = str(arg)
    if arg and not any(ch in arg for ch in ' \t"\''):
        return arg
    return '"' + arg.replace('"', '\\"') + '"'


def launch_args(executable: str = "") -> list:
    """The REAL CLI launch argument vector.

    Frozen bundle: the bundle IS the airmouse CLI →
    ``[bundle, "--skip"]``.  Source/venv install:
    ``[python, "-m", "airmouse", "--skip"]`` — this actually starts the
    program (the old defect ``<python> --skip`` passed an interpreter
    flag only and ran nothing).
    """
    exe = executable or _get_executable()
    if getattr(sys, "frozen", False):
        return [exe, _LAUNCH_TUTORIAL_FLAG]
    return [exe, "-m", _LAUNCH_MODULE, _LAUNCH_TUTORIAL_FLAG]


def launch_command(executable: str = "") -> str:
    """One-string shell form of :func:`launch_args` (executable quoted)."""
    args = launch_args(executable)
    if not args:
        return ""
    return " ".join([_quote(args[0])] + list(args[1:]))


def build_desktop_entry(executable: str = "") -> str:
    """The Linux ``airmouse.desktop`` CONTENT (pure — writes nothing)."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=AirMouse\n"
        "Comment=AirMouse - Gesture-controlled cursor\n"
        "Exec=%s\n" % launch_command(executable) +
        "Icon=airmouse\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def build_windows_value(executable: str = "") -> str:
    """The Windows ``Run`` registry value (pure — writes nothing)."""
    return launch_command(executable)


def build_plist(executable: str = "") -> str:
    """The macOS LaunchAgent plist CONTENT (pure — writes nothing)."""
    args_xml = "\n".join(
        "        <string>%s</string>" % a for a in launch_args(executable))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.airmouse</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>RunAtLoad</key><true/>
</dict>
</plist>"""


def _first_token(cmd: str) -> str:
    """The interpreter/executable token of a launch command string."""
    c = str(cmd or "").strip()
    if not c:
        return ""
    if c.startswith('"'):
        end = c.find('"', 1)
        return c[1:end] if end > 0 else ""
    return c.split(" ", 1)[0]


def verify_launch_command(cmd: str = "") -> dict:
    """Confirm a launch command is USABLE — honest, local, no side effects.

    Checks (all must hold for ``ok``):

    - ``interpreter``: the executable path exists on disk;
    - ``references_module``: the command actually starts the airmouse
      CLI (``-m airmouse`` — or a frozen bundle, which IS the CLI);
      the old defect ``<python> --skip`` fails this check;
    - ``module_importable``: ``import airmouse`` resolves in THIS
      environment (importlib.util.find_spec — never executed, just
      located).

    Returns ``{"ok", "launch", "interpreter", "references_module",
    "module_importable", "frozen", "reason"}``.  Never raises.
    """
    launch = str(cmd) if cmd else launch_command()
    exe = _first_token(launch)
    interpreter_ok = bool(exe) and os.path.isfile(exe)
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        references = exe != ""     # the bundle itself is the CLI
    else:
        references = ("-m %s" % _LAUNCH_MODULE) in launch
    try:
        spec = importlib.util.find_spec(_LAUNCH_MODULE)
        importable = spec is not None
    except Exception:
        importable = False
    ok = bool(interpreter_ok and references and importable)
    reason = ""
    if not interpreter_ok:
        reason = "interpreter path not found: %r" % exe
    elif not references:
        reason = ("launch command does not start the airmouse CLI "
                  "(no '-m airmouse')" if not frozen else
                  "no frozen bundle executable")
    elif not importable:
        reason = ("the 'airmouse' package is not importable in this "
                  "environment")
    return {
        "ok": ok,
        "launch": launch,
        "interpreter": bool(interpreter_ok),
        "references_module": bool(references),
        "module_importable": bool(importable),
        "frozen": frozen,
        "reason": reason,
    }


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
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_WRITE,
        )
        winreg.SetValueEx(key, "AirMouse", 0, winreg.REG_SZ,
                          build_windows_value())
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
        with open(_LINUX_DESKTOP, "w") as f:
            f.write(build_desktop_entry())
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
        with open(_MACOS_PLIST, "w") as f:
            f.write(build_plist())
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
