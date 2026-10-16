#!/bin/bash
# AirMouse Build Script — Creates platform-specific bundles
#
# Usage:
#   python build.py              # Build for current platform
#   python build.py --windows    # Build Windows .exe
#   python build.py --linux      # Build Linux binary
#   python build.py --all        # Build all platforms (requires appropriate envs)

import os
import sys
import subprocess
import shutil

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PKG_DIR, "bundle")


def _install_pyinstaller():
    """Ensure PyInstaller is available (CONSENT-GATED, v19.0.2).

    The old code silently pip-installed PyInstaller — a build helper must
    never mutate the user's environment without asking.
    """
    try:
        import PyInstaller
        print("  PyInstaller found")
        return True
    except ImportError:
        pass
    if os.environ.get("AIRMOUSE_NO_BUILD_INSTALL", "").strip() not in ("", "0"):
        print("  !! PyInstaller missing and AIRMOUSE_NO_BUILD_INSTALL=1 "
              "— install it manually: pip install pyinstaller")
        return False
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            answer = input("  PyInstaller is required to bundle. Install "
                           "it now? [y/N] ").strip().lower()
        else:
            answer = ""  # non-interactive: never install silently
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in ("y", "yes"):
        print("  !! PyInstaller not installed — bundling aborted "
              "(run: pip install pyinstaller)")
        return False
    print("  Installing PyInstaller...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"],
                   check=True)
    return True


def _ensure_model():
    """Ensure the hand model for bundling (v19.0.2 — hardened path).

    Delegates to the hardened tracker download (pinned URL, SHA-256
    verification, atomic install, consent, kill-switch) instead of the
    old bare urlretrieve of a mutable "latest" URL.
    """
    try:
        from airmouse.tracker import _ensure_model as _hardened
        return _hardened()
    except Exception:
        # fall back to the resolved path if the model is already present
        from airmouse.tracker import _resolve_model_path
        p = _resolve_model_path()
        if os.path.exists(p):
            print(f"  Model already exists: {p}")
            return p
        raise


def build_current_platform():
    """Build for the current platform."""
    if not _install_pyinstaller():
        raise SystemExit(2)
    _ensure_model()

    os.makedirs(DIST_DIR, exist_ok=True)

    print()
    print(f"  Building AirMouse for {sys.platform}...")
    print()

    # PyInstaller command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "AirMouse",
        "--noconfirm",
        "--clean",
        "--onedir",  # onedir is faster startup than onefile
        "--windowed",  # No console window (GUI mode)
        "--distpath", DIST_DIR,
        "--workpath", os.path.join(PKG_DIR, "build_pyinstaller"),
        "--specpath", PKG_DIR,
        # Hidden imports
        "--hidden-import", "airmouse",
        "--hidden-import", "airmouse.__main__",
        "--hidden-import", "airmouse.physics",
        "--hidden-import", "airmouse.gestures",
        "--hidden-import", "airmouse.tracker",
        "--hidden-import", "airmouse.mouse_controller",
        "--hidden-import", "airmouse.keyboard",
        "--hidden-import", "airmouse.audio",
        "--hidden-import", "airmouse.config",
        "--hidden-import", "airmouse.tutorial",
        "--hidden-import", "airmouse.display",
        "--hidden-import", "airmouse.autostart",
        "--hidden-import", "airmouse.settings_gui",
        "--hidden-import", "mediapipe",
        "--hidden-import", "mediapipe.tasks",
        "--hidden-import", "mediapipe.tasks.vision",
        "--hidden-import", "pynput",
        "--hidden-import", "pynput.keyboard",
        "--hidden-import", "pynput.mouse",
        "--hidden-import", "numpy",
        "--hidden-import", "cv2",
        # Add model file
        "--add-data", f"{os.path.join(os.path.expanduser('~'), '.airmouse', 'hand_landmarker.task')}{os.pathsep}.airmouse",
        # Entry point
        os.path.join(PKG_DIR, "airmouse_launcher.py"),
    ]

    # Platform-specific options
    if sys.platform == "win32":
        # Windows: .exe with icon
        icon_path = os.path.join(PKG_DIR, "airmouse_icon.ico")
        if os.path.exists(icon_path):
            cmd.extend(["--icon", icon_path])
    elif sys.platform == "darwin":
        # macOS: .app bundle
        cmd.extend(["--osx-bundle-identifier", "com.airmouse.app"])

    print(f"  Running: {' '.join(cmd[:5])}...")
    print()

    result = subprocess.run(cmd, cwd=PKG_DIR)

    if result.returncode == 0:
        print()
        print(f"  Build SUCCESS! Output in: {DIST_DIR}")
        # List output
        if os.path.exists(DIST_DIR):
            for item in os.listdir(DIST_DIR):
                path = os.path.join(DIST_DIR, item)
                if os.path.isdir(path):
                    size = sum(
                        os.path.getsize(os.path.join(dp, f))
                        for dp, dn, fn in os.walk(path)
                        for f in fn
                    ) / (1024 * 1024)
                    print(f"    {item}/ ({size:.1f} MB)")
                else:
                    size = os.path.getsize(path) / (1024 * 1024)
                    print(f"    {item} ({size:.1f} MB)")
    else:
        print()
        print(f"  Build FAILED with exit code {result.returncode}")

    return result.returncode


def main():
    print()
    print("  +==================================================+")
    print("  |     AirMouse Bundle Builder                       |")
    print("  |     Creates platform-specific binaries             |")
    print("  +==================================================+")
    print()

    if "--help" in sys.argv:
        print("  Usage:")
        print("    python build.py          Build for current platform")
        print("    python build.py --help   Show this help")
        print()
        return

    build_current_platform()


if __name__ == "__main__":
    main()
