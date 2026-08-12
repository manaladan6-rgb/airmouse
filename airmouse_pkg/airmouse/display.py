"""
Multi-Monitor Support — Enumerate and manage multiple displays.

Windows:  ctypes EnumDisplayMonitors
Linux:    xrandr command
macOS:    subprocess system_profiler or NSScreen
"""

import sys
import subprocess


class DisplayInfo:
    """Information about a single display/monitor."""
    def __init__(self, index=0, x=0, y=0, width=1920, height=1080,
                 is_primary=True, name="Primary"):
        self.index = index
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.is_primary = is_primary
        self.name = name

    @property
    def center(self):
        return (self.x + self.width // 2, self.y + self.height // 2)

    def __repr__(self):
        return (f"Display({self.index}: {self.width}x{self.height} "
                f"at ({self.x},{self.y}) {'[PRIMARY]' if self.is_primary else ''})")


def enumerate_displays():
    """Enumerate all connected displays.

    Returns:
        list[DisplayInfo]: List of display info objects.
    """
    if sys.platform == "win32":
        return _enum_windows()
    elif sys.platform == "linux":
        return _enum_linux()
    elif sys.platform == "darwin":
        return _enum_macos()
    else:
        return _enum_fallback()


def _enum_windows():
    """Windows: Use ctypes EnumDisplayMonitors."""
    try:
        import ctypes
        from ctypes import wintypes

        displays = []
        MonitorEnumProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            wintypes.HMONITOR, wintypes.HDC,
            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
        )

        def _callback(hMonitor, hdcMonitor, lprcMonitor, dwParam):
            rect = lprcMonitor.contents
            info = DisplayInfo(
                index=len(displays),
                x=rect.left,
                y=rect.top,
                width=rect.right - rect.left,
                height=rect.bottom - rect.top,
                is_primary=(rect.left == 0 and rect.top == 0),
                name=f"Monitor {len(displays) + 1}",
            )
            displays.append(info)
            return True

        user32 = ctypes.windll.user32
        user32.EnumDisplayMonitors(
            None, None,
            MonitorEnumProc(_callback), 0,
        )
        if displays:
            return displays
    except Exception:
        pass
    return _enum_fallback()


def _enum_linux():
    """Linux: Use xrandr to get display info."""
    try:
        result = subprocess.run(
            ["xrandr", "--query"],
            capture_output=True, text=True, timeout=3,
        )
        displays = []
        for line in result.stdout.splitlines():
            if " connected" in line:
                parts = line.split()
                name = parts[0]
                # Parse resolution and position
                for i, p in enumerate(parts):
                    if "+" in p and "x" in p:
                        # Format: 1920x1080+0+0
                        res_pos = p
                        break
                else:
                    continue
                res_part, x_part, y_part = res_pos.replace("+", "+").split("+")
                w, h = res_part.split("x")
                x_off = int(x_part) if x_part else 0
                y_off = int(y_part) if y_part else 0
                info = DisplayInfo(
                    index=len(displays),
                    x=x_off, y=y_off,
                    width=int(w), height=int(h),
                    is_primary=(x_off == 0 and y_off == 0),
                    name=name,
                )
                displays.append(info)
        if displays:
            return displays
    except Exception:
        pass
    return _enum_fallback()


def _enum_macos():
    """macOS: Use system_profiler to get display info."""
    try:
        result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True, text=True, timeout=5,
        )
        import json
        data = json.loads(result.stdout)
        displays = []
        for i, d in enumerate(data.get("SPDisplaysDataType", [])):
            for j, disp in enumerate(d.get("spdisplays_ndrvs", [])):
                res = disp.get("_spdisplays_resolution", "1920 x 1080")
                w, h = res.split(" x ")
                info = DisplayInfo(
                    index=len(displays),
                    x=0, y=0,
                    width=int(w), height=int(h),
                    is_primary=(j == 0),
                    name=disp.get("_name", f"Display {j + 1}"),
                )
                displays.append(info)
        if displays:
            return displays
    except Exception:
        pass
    return _enum_fallback()


def _enum_fallback():
    """Fallback: Return primary display using screen size detection."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        if w > 0 and h > 0:
            return [DisplayInfo(0, 0, 0, w, h, True, "Primary")]
    except Exception:
        pass
    try:
        import tkinter as tk
        root = tk.Tk()
        w, h = root.winfo_screenwidth(), root.winfo_screenheight()
        root.destroy()
        if w > 0 and h > 0:
            return [DisplayInfo(0, 0, 0, w, h, True, "Primary")]
    except Exception:
        pass
    return [DisplayInfo(0, 0, 0, 1920, 1080, True, "Primary")]


def get_primary_display():
    """Get the primary display info."""
    for d in enumerate_displays():
        if d.is_primary:
            return d
    return DisplayInfo(0, 0, 0, 1920, 1080, True, "Primary")
