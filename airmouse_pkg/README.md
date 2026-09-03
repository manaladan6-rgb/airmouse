# AirMouse v5.0.0 — VOICE + KALMAN Edition

Control your mouse with hand gestures **and your voice** using your webcam. No hardware needed — just a camera, a microphone (optional), and your hand.

![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue)
![Platform](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macos-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

## What's New in v5.0.0

- **Voice Control** — 30 voice commands ("click", "scroll up", "zoom in", "freeze", "play macro"...) with three sensitivity modes, including **TURBO (MAD) mode**: nonstop listening, ultra-fuzzy matching, rapid-fire cooldowns
- **Hybrid One Euro + Kalman Filter** — a constant-velocity Kalman channel is fused with the One Euro filter: rock-solid cursor lock when your hand is still, One Euro responsiveness when it moves. Monte-Carlo verified: 2.2x jitter reduction at rest, zero added lag
- **Pinch-to-Zoom (frontier gesture)** — quick pinch = click, **hold pinch + move hand up/down = Ctrl+wheel zoom** (works in browsers, maps, editors)
- **Adaptive Calibration** — AirMouse now *learns your hand*: your reach box, your tremor, your speed — and remaps + auto-tunes the filter live. Persisted across sessions
- **Macro Recorder** — record click/scroll/zoom sequences and replay them anywhere: `airmouse --record my_macro`, replay with `--play my_macro` or voice "play macro"
- **Single-File Simple Mode** — `airmouse_simple.py`: the whole experience in one standalone file

## Features (all versions)

- **14 Hand Gestures** — point, pinch, peace, palm, fist, thumbs up, three, pinky, gun, rock, shaka, OK, ring, six
- **2 Swipe Gestures** — swipe left/right for browser back/forward
- **Direct Tracking** — 1:1 finger-to-screen mapping
- **Trackpad Mode** — tap=click, hold=drag, 2-finger=scroll
- **Scroll / Drag / Volume / Brightness** gesture modes
- **Multi-Monitor**, **Precision Mode**, **Auto-Start**

## Install

```bash
pip install airmouse-5.0.0-py3-none-any.whl

# optional — voice control extras:
pip install "airmouse[voice]"   # SpeechRecognition + pyaudio
pip install "airmouse[tts]"     # spoken confirmations (pyttsx3)
```

## Run

```bash
airmouse              # first run shows tutorial
airmouse --skip       # skip tutorial
airmouse --voice      # enable voice commands
airmouse --voice-mode turbo   # MAD mode: nonstop listening, ultra-fuzzy
airmouse --no-kalman  # pure One Euro filter (v4.1 feel)
airmouse --no-zoom    # disable pinch-to-zoom
airmouse --no-calibration     # disable adaptive calibration
airmouse --calibrate  # guided 8s calibration sweep on startup
airmouse --record greeting    # record a macro this session
airmouse --play greeting      # replay it on startup
airmouse --macros     # list saved macros
airmouse --trackpad   # trackpad mode (pinch-hold=drag, 2-finger=scroll)
airmouse_simple.py    # single-file simple mode (same flags)
```

## Voice Commands

Say any of these while AirMouse runs (works even when your hand is out of frame):

| Command | Phrase examples |
|---|---|
| Click | "click", "left click", "tap", "select" |
| Right click | "right click", "context" |
| Double click | "double click", "open" |
| Scroll | "scroll up"/"down", "up"/"down" |
| Zoom | "zoom in", "zoom out", "zoom mode" |
| Drag | "drag", "grab", "hold" |
| Freeze / Resume | "freeze", "stop" / "unfreeze", "resume", "go" |
| Precision | "precision", "sniper", "accurate" |
| Calibrate | "calibrate", "recalibrate" |
| Macros | "start recording", "stop recording", "play macro" |
| Volume / Media | "volume up", "volume down", "mute", "next", "previous", "play" |
| Windows | "minimize", "close", "switch window", "show desktop" |
| Screenshot | "screenshot", "capture screen" |
| Quit | "quit", "exit", "goodbye" |

**Sensitivity modes:** `normal` (wake-word-ish, conservative) · `high` (balanced, default) · `turbo` (**MAD**: nonstop listening, 0.3s cooldown, 0.45 fuzzy threshold — it hears everything and fires fast)

## Gestures

| #  | Gesture   | Action                |
|----|-----------|-----------------------|
| 1  | Point     | Move cursor           |
| 2  | Pinch     | Left click — **HOLD + move = ZOOM** |
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
| `d` | Debug view      |
| `r` | Recalibrate     |
| `s` | Sound toggle    |
| `p` | Precision mode  |
| `t` | Tutorial        |
| `h` | Help            |
| `v` | Voice on/off    |
| `k` | Kalman hybrid on/off |
| `z` | Pinch-zoom on/off |
| `m` | Macro record on/off |

## The Hybrid Filter (v5.0)

The cursor pipeline now fuses two filters per frame:

```
raw hand → [One Euro ⊕ Kalman] → dead zone → screen
                ▲        ▲
        responsive │        │ rock-solid
     (fast motion) │        │ (hand still)
                  blend by speed
```

- Hand **still** → Kalman weight 0.85 → jitter lock (output noise 0.009 std from 0.02 input noise)
- Hand **moving** → One Euro takes over → zero perceptible lag
- The Kalman's own velocity channel drives the blend (a still hand reads ~0 speed — no noise floor)

Configure in `~/.airmouse/config.toml` under `[kalman]`: fusion mode (`adaptive` / `kalman` / `one_euro` / `average`), process noise, measurement noise, speed crossover.

## Configuration

Everything lives in `~/.airmouse/config.toml` (created on first run): `[voice]`, `[kalman]`, `[zoom]`, `[calibration]`, plus all the v4 gesture/physics sections.

Adaptive calibration state: `~/.airmouse/calibration.json` · Macros: `~/.airmouse/macros/*.json`

## License

MIT
