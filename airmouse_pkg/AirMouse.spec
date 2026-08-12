# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['/home/z/my-project/airmouse_pkg/airmouse_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('/home/z/.airmouse/hand_landmarker.task', '.airmouse')],
    hiddenimports=['airmouse', 'airmouse.__main__', 'airmouse.physics', 'airmouse.gestures', 'airmouse.tracker', 'airmouse.mouse_controller', 'airmouse.keyboard', 'airmouse.audio', 'airmouse.config', 'airmouse.tutorial', 'airmouse.display', 'airmouse.autostart', 'airmouse.settings_gui', 'mediapipe', 'mediapipe.tasks', 'mediapipe.tasks.vision', 'pynput', 'pynput.keyboard', 'pynput.mouse', 'numpy', 'cv2'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AirMouse',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AirMouse',
)
