# AirMouse PyInstaller Specification
# Build: pyinstaller airmouse.spec

import os
import sys

# Model file path (bundled into the exe)
MODEL_DIR = os.path.join(os.path.expanduser("~"), ".airmouse")
MODEL_FILE = os.path.join(MODEL_DIR, "hand_landmarker.task")

block_cipher = None

a = Analysis(
    ['airmouse_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Bundle the MediaPipe model if it exists
        (MODEL_FILE, '.airmouse') if os.path.exists(MODEL_FILE) else ('', ''),
        # Bundle README
        ('README.txt', '.'),
    ],
    hiddenimports=[
        'airmouse',
        'airmouse.__main__',
        'airmouse.physics',
        'airmouse.gestures',
        'airmouse.tracker',
        'airmouse.mouse_controller',
        'airmouse.keyboard',
        'airmouse.audio',
        'airmouse.config',
        'airmouse.tutorial',
        'airmouse.display',
        'airmouse.autostart',
        'airmouse.settings_gui',
        'mediapipe',
        'mediapipe.tasks',
        'mediapipe.tasks.vision',
        'pynput',
        'pynput.keyboard',
        'pynput.mouse',
        'numpy',
        'cv2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AirMouse',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI mode (no console window)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='airmouse_icon.ico' if os.path.exists('airmouse_icon.ico') else None,
)
