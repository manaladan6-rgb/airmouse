# AirMouse v3.2.0 — Direct Tracking Edition

Control your mouse with hand gestures using your webcam. No hardware needed — just a camera and your hand.

![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue)
![Platform](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macos-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **14 Hand Gestures** — point, pinch, peace, palm, fist, thumbs up, three, pinky, gun, rock, shaka, OK, ring, six
- **2 Swipe Gestures** — swipe left/right for browser back/forward
- **Direct Tracking** — 1:1 finger-to-screen mapping, instant response (~33ms lag)
- **Scroll Mode** — three-finger gesture for smooth scrolling
- **Drag Mode** — palm gesture to grab and drag
- **Volume & Brightness** — shaka / ring gestures with up-down motion
- **Multi-Monitor** — pick which display to use
- **Precision Mode** — press `P` for smoother control
- **Auto-Start** — launch on boot (optional)

## Install

```bash
pip install airmouse-3.2.0-py3-none-any.whl
```

## Run

```bash
airmouse              # first run shows tutorial
airmouse --skip       # skip tutorial
airmouse --tutorial   # force tutorial
airmouse --mode direct   # 1:1 finger-to-screen (default)
airmouse --mode ironman  # exponential finger-relative (legacy)
```

## Gestures

| #  | Gesture   | Action                |
|----|-----------|-----------------------|
| 1  | Point     | Move cursor           |
| 2  | Pinch     | Left click            |
| 3  | Peace     | Right click           |
| 4  | Palm      | Drag mode             |
| 5  | Fist      | Freeze cursor         |
| 6  | Thumbs Up | Double click          |
| 7  | Three     | Scroll mode           |
| 8  | Pinky     | Middle click          |
| 9  | Gun       | Snap to center        |
| 10 | Rock      | Minimize window       |
| 11 | Shaka     | Volume mode           |
| 12 | OK        | Close window          |
| 13 | Ring      | Brightness mode       |
| 14 | Six       | Task switcher         |

**Swipe:** Fast left = browser back, Fast right = browser forward

## Keyboard Shortcuts (in camera window)

| Key | Action          |
|-----|-----------------|
| `q` | Quit            |
| `d` | Toggle debug    |
| `r` | Recalibrate     |
| `s` | Sound toggle    |
| `p` | Precision mode  |
| `t` | Tutorial        |
| `h` | Help            |

## Configuration

Edit `~/.airmouse/config.toml` to tune:
- Tracking mode (direct / ironman)
- Jitter filter, spring, smoothing
- Gesture thresholds
- Camera index and confidence

## How It Works

### Direct Mode (default, v3.2)
1. **Single EMA filter** (α=0.55) — kills camera noise (~33ms lag, below human perception)
2. **Noise gate** — ignore micro-movements (like mouse sensor LOD)
3. **Direct 1:1 map** — finger position → screen position, immediately
4. **Pixel deadzone** — prevent sub-pixel jitter

No cascading lag, no spring chasing — cursor is AT your finger.

### Ironman Mode (legacy, v3.1)
Finger-relative tracking with exponential curve, spring-damper physics, momentum throw, and edge gravity. For the stylized "Iron Man" feel.

## Dependencies

- mediapipe >= 0.10.9
- opencv-python >= 4.8.0
- pynput >= 1.7.6
- numpy >= 1.24.0
- sounddevice >= 0.5.0 (optional, for audio feedback)

## Build

```bash
python -m build          # builds wheel + sdist
python build.py          # builds standalone binary (PyInstaller)
```

## License

MIT
