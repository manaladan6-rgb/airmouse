"""
Settings GUI + System Tray — tkinter-based settings window with system tray icon.

Features:
  - Sensitivity sliders (power, scale, deadzone)
  - Physics tuning (mass, damping, stiffness range)
  - Gesture confirm frames
  - Audio toggle
  - Auto-start toggle
  - Camera selector
  - Monitor selector
  - System tray icon with right-click menu

Uses only tkinter (stdlib, zero deps).
"""

import os
import sys
import threading
import subprocess

from .config import Config, CONFIG_PATH, CONFIG_DIR


def _ensure_config():
    """Make sure config file exists."""
    config = Config()
    config.load()
    if not os.path.exists(CONFIG_PATH):
        config.save_defaults()
    return config


class SettingsWindow:
    """Tkinter settings window for AirMouse configuration."""

    def __init__(self, on_apply=None):
        self.config = _ensure_config()
        self.on_apply = on_apply
        self._root = None
        self._vars = {}

    def show(self):
        """Show the settings window (non-blocking)."""
        # Run in a thread so it doesn't block the main loop
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        """Create and run the tkinter window."""
        import tkinter as tk
        from tkinter import ttk

        self._root = tk.Tk()
        self._root.title("AirMouse Settings")
        self._root.geometry("520x620")
        self._root.resizable(False, False)
        self._root.configure(bg="#1a1a2e")

        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Title.TLabel', font=('Segoe UI', 14, 'bold'),
                        foreground='#e94560', background='#1a1a2e')
        style.configure('Section.TLabel', font=('Segoe UI', 11, 'bold'),
                        foreground='#0f3460', background='#16213e')
        style.configure('Info.TLabel', font=('Segoe UI', 9),
                        foreground='#a8a8a8', background='#16213e')
        style.configure('TScale', background='#16213e')
        style.configure('TCheckbutton', background='#16213e', foreground='#e0e0e0')
        style.configure('TButton', font=('Segoe UI', 10, 'bold'))

        # Main container
        main = ttk.Frame(self._root, padding=15)
        main.pack(fill=tk.BOTH, expand=True)

        # Title
        ttk.Label(main, text="AirMouse Settings", style='Title.TLabel').pack(anchor=tk.W, pady=(0, 10))

        # ─── Sensitivity Section ───
        self._section(main, "Sensitivity")
        self._slider(main, "exp_power", "Exponential Power", 0.2, 1.5, 0.05,
                     self.config.exp_power, "Lower = Iron Man (tiny finger = big cursor)")
        self._slider(main, "exp_scale", "Sensitivity Scale", 0.5, 6.0, 0.1,
                     self.config.exp_scale, "Overall sensitivity multiplier")
        self._slider(main, "deadzone", "Deadzone", 0.001, 0.05, 0.001,
                     self.config.deadzone, "Ignore movements smaller than this")

        # ─── Physics Section ───
        self._section(main, "Physics")
        self._slider(main, "damping_ratio", "Damping Ratio", 0.5, 1.2, 0.05,
                     self.config.damping_ratio, "1.0 = critically damped, <1 = bouncy")
        self._slider(main, "stiffness_min", "Min Stiffness", 50, 300, 10,
                     self.config.stiffness_min, "Slow movement stiffness (precision)")
        self._slider(main, "stiffness_max", "Max Stiffness", 200, 800, 10,
                     self.config.stiffness_max, "Fast movement stiffness (responsiveness)")

        # ─── Gesture Section ───
        self._section(main, "Gesture Detection")
        self._slider(main, "confirm_frames", "Confirm Frames", 2, 10, 1,
                     self.config.gesture_confirm_frames, "Frames to confirm movement gestures")
        self._slider(main, "action_confirm_frames", "Action Confirm Frames", 3, 10, 1,
                     self.config.gesture_action_confirm_frames, "Frames to confirm action gestures (clicks)")

        # ─── Toggles Section ───
        self._section(main, "Options")

        toggle_frame = ttk.Frame(main)
        toggle_frame.pack(fill=tk.X, pady=5)

        # Audio toggle
        audio_var = tk.BooleanVar(value=self.config.audio_enabled)
        self._vars["audio_enabled"] = audio_var
        ttk.Checkbutton(toggle_frame, text="Audio feedback", variable=audio_var).pack(anchor=tk.W)

        # Auto-start toggle
        auto_var = tk.BooleanVar(value=False)
        self._vars["autostart"] = auto_var
        try:
            from .autostart import is_auto_start_enabled
            auto_var.set(is_auto_start_enabled())
        except Exception:
            pass
        ttk.Checkbutton(toggle_frame, text="Start on boot", variable=auto_var).pack(anchor=tk.W)

        # Show camera toggle
        cam_var = tk.BooleanVar(value=self.config.show_camera)
        self._vars["show_camera"] = cam_var
        ttk.Checkbutton(toggle_frame, text="Show camera window", variable=cam_var).pack(anchor=tk.W)

        # ─── Monitor Selection ───
        self._section(main, "Monitor")
        monitor_frame = ttk.Frame(main)
        monitor_frame.pack(fill=tk.X, pady=5)

        try:
            from .display import enumerate_displays
            displays = enumerate_displays()
            monitor_names = [f"{d.index}: {d.width}x{d.height} {'[PRIMARY]' if d.is_primary else ''}" for d in displays]
            monitor_var = tk.StringVar(value=monitor_names[0] if monitor_names else "Primary")
            self._vars["monitor"] = monitor_var
            if len(monitor_names) > 1:
                ttk.OptionMenu(monitor_frame, monitor_var, monitor_names[0], *monitor_names).pack(fill=tk.X)
            else:
                ttk.Label(monitor_frame, text=f"  {monitor_names[0]}", style='Info.TLabel').pack(anchor=tk.W)
        except Exception:
            ttk.Label(monitor_frame, text="  Primary display", style='Info.TLabel').pack(anchor=tk.W)

        # ─── Buttons ───
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(15, 0))

        ttk.Button(btn_frame, text="Apply & Save", command=self._apply).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(btn_frame, text="Reset Defaults", command=self._reset).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(btn_frame, text="Open Config File", command=self._open_config).pack(side=tk.LEFT)

        self._root.mainloop()

    def _section(self, parent, title):
        """Create a section header."""
        import tkinter as tk
        from tkinter import ttk
        sep = ttk.Separator(parent, orient=tk.HORIZONTAL)
        sep.pack(fill=tk.X, pady=(10, 2))
        ttk.Label(parent, text=title, style='Section.TLabel').pack(anchor=tk.W, pady=(0, 5))

    def _slider(self, parent, key, label, min_val, max_val, step, default, hint=""):
        """Create a labeled slider."""
        import tkinter as tk
        from tkinter import ttk

        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=2)

        lbl = ttk.Label(frame, text=label, style='Info.TLabel')
        lbl.pack(anchor=tk.W)

        var = tk.DoubleVar(value=default)
        self._vars[key] = var

        slider_frame = ttk.Frame(frame)
        slider_frame.pack(fill=tk.X)

        scale = ttk.Scale(slider_frame, from_=min_val, to=max_val,
                          variable=var, orient=tk.HORIZONTAL)
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        # Value display
        val_label = ttk.Label(slider_frame, text=f"{default:.3f}", style='Info.TLabel', width=8)
        val_label.pack(side=tk.RIGHT)

        def _update_label(*args):
            v = var.get()
            if step >= 1:
                v = round(v)
                var.set(v)
                val_label.config(text=f"{int(v)}")
            else:
                val_label.config(text=f"{v:.3f}")
        var.trace_add('write', _update_label)

        if hint:
            ttk.Label(frame, text=f"  {hint}", style='Info.TLabel').pack(anchor=tk.W)

    def _apply(self):
        """Apply settings and save to config."""
        # Update config from vars
        for key, var in self._vars.items():
            if key == "audio_enabled":
                self.config.audio_enabled = var.get()
            elif key == "show_camera":
                self.config.show_camera = var.get()
            elif key == "autostart":
                try:
                    from .autostart import enable_auto_start, disable_auto_start
                    if var.get():
                        enable_auto_start()
                    else:
                        disable_auto_start()
                except Exception:
                    pass
            elif key == "monitor":
                pass  # Handled differently
            elif hasattr(self.config, key):
                setattr(self.config, key, var.get())

        self.config.save_defaults()
        print("  Settings saved!")

        if self.on_apply:
            self.on_apply(self.config)

    def _reset(self):
        """Reset to defaults."""
        defaults = Config()
        self.config = defaults
        self.config.save_defaults()
        if self._root:
            self._root.destroy()
        self.show()
        print("  Settings reset to defaults!")

    def _open_config(self):
        """Open config file in default editor."""
        if sys.platform == "win32":
            os.startfile(CONFIG_PATH)
        elif sys.platform == "darwin":
            subprocess.run(["open", CONFIG_PATH])
        else:
            subprocess.run(["xdg-open", CONFIG_PATH])


def show_settings(on_apply=None):
    """Show the AirMouse settings window."""
    window = SettingsWindow(on_apply=on_apply)
    window.show()
    return window


def run_with_tray(main_func):
    """Run AirMouse with system tray icon.

    Falls back to running without tray if tray libraries aren't available.
    """
    try:
        import pystray
        from PIL import Image, ImageDraw
        _run_with_tray(main_func, pystray, Image, ImageDraw)
    except ImportError:
        # No tray library — just run normally
        print("  (System tray not available — install pystray + Pillow for tray icon)")
        main_func()


def _run_with_tray(main_func, pystray, Image, ImageDraw):
    """Internal: Run with pystray system tray."""

    def _create_icon():
        """Create a simple tray icon."""
        img = Image.new('RGB', (64, 64), color=(26, 26, 46))
        dc = ImageDraw.Draw(img)
        # Draw a simple hand cursor icon
        dc.ellipse([20, 10, 44, 34], fill=(233, 69, 96), outline=(255, 255, 255))
        dc.line([32, 34, 32, 55], fill=(233, 69, 96), width=3)
        return img

    def _on_settings(icon, item):
        show_settings()

    def _on_quit(icon, item):
        icon.stop()
        os._exit(0)

    icon = pystray.Icon(
        "airmouse",
        _create_icon(),
        "AirMouse",
        menu=pystray.Menu(
            pystray.MenuItem("Settings", _on_settings),
            pystray.MenuItem("Quit", _on_quit),
        ),
    )

    # Run main loop in a thread
    main_thread = threading.Thread(target=main_func, daemon=True)
    main_thread.start()

    # Run tray icon (blocking)
    icon.run()
