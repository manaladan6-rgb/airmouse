"""
AirMouse Launcher — Entry point for PyInstaller bundle.

This is the script that PyInstaller compiles into the .exe / binary.
It ensures the MediaPipe model is available and launches AirMouse.
"""

import os
import sys


def _ensure_model():
    """Make sure the MediaPipe model file exists (download if needed)."""
    model_dir = os.path.join(os.path.expanduser("~"), ".airmouse")
    model_path = os.path.join(model_dir, "hand_landmarker.task")

    if os.path.exists(model_path):
        return model_path

    # Check if it's bundled alongside the exe
    if getattr(sys, 'frozen', False):
        bundle_path = os.path.join(os.path.dirname(sys.executable), '.airmouse', 'hand_landmarker.task')
        if os.path.exists(bundle_path):
            os.makedirs(model_dir, exist_ok=True)
            import shutil
            shutil.copy2(bundle_path, model_path)
            return model_path

    # Download it
    os.makedirs(model_dir, exist_ok=True)
    url = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
    print("  Downloading hand tracking model (first run only)...")
    try:
        import urllib.request
        urllib.request.urlretrieve(url, model_path)
        print(f"  Model saved to {model_path}")
    except Exception as e:
        print(f"  Warning: Could not download model: {e}")
        print(f"  Download manually from: {url}")
        print(f"  Save to: {model_path}")

    return model_path


def main():
    """Launch AirMouse."""
    _ensure_model()

    from airmouse.__main__ import main as airmouse_main
    airmouse_main()


if __name__ == "__main__":
    main()
