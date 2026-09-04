# AirMouse v9.0.0 — MULTIMODAL INTELLIGENCE EDITION

Control your computer with your **eyes, hands, and voice** — one webcam, one microphone, zero extra hardware. AirMouse v9 evolves the gesture mouse into a **multimodal personal-computer interaction system**: perception → fusion → screen understanding → intent → action → verification → recovery.

![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue)
![Platform](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macos-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

```
          HUMAN
            │
   ┌────────┼────────┐
   ▼        ▼        ▼
  👁️       🖐️       🎤
 GAZE      HAND     VOICE
   │        │        │
   └────────┼────────┘
            ▼
    MULTIMODAL FUSION
            ▼
      SCREEN MODEL → CONTEXT → INTENT ENGINE
            ▼
      ACTION PLANNER → ACTION ENGINE → COMPUTER
            ▼
      OBSERVER → VERIFICATION ──► SUCCESS / RECOVERY
```

## What's New in v9.0.0

| Subsystem | What it does |
|---|---|
| **👁️ Gaze (v6)** | Webcam iris/eye tracking (MediaPipe FaceMesh + refined iris landmarks): gaze direction, confidence scoring, blink / long-blink / double-blink / wink detection, fixation + dwell detection, dedicated filtering pipeline (outlier rejection + confidence-weighted adaptive smoothing, ~2.5x jitter reduction), affine gaze→screen calibration with quality grading and persistence |
| **🔀 Fusion (v7)** | Multimodal arbitration across gaze 👁 / hand 🖐 / voice 🎤 / mouse 🖱 / keyboard ⌨ / screen 🖥 — six interaction modes (`hand`, `gaze`, `voice`, `fusion`, `hands-free`, `assist`), per-mode priority matrix, staleness handling, conflict resolution, confirmation patterns (`LOOK → TARGET → PINCH → CLICK`, `LOOK → "click"`) |
| **🖥️ Screen understanding (v7)** | Layered providers (accessibility → OCR *(opt-in)* → geometry) merged into a unified target model — semantic targets (`"Submit button"`, active window) with a guaranteed coordinate fallback |
| **🧠 Intent engine (v8)** | Turns multimodal signals into structured intents (click / open / scroll / zoom / close / move / hotkey …) with confidence propagation, uncertain-gaze click suppression, and sensitive-action marking |
| **⚡ Action engine + verification (v8)** | Every action: preconditions → execution → observation → expected-vs-observed comparison → SUCCESS/FAILURE, with a bounded recovery ladder (retry → adjusted retry → notify). Blocked/failed actions are never blindly repeated |
| **🛡️ Safety system (v8)** | Emergency stop (ESC / long-blink / "stop everything"), sliding-window action rate limiter, click cooldowns, confidence gates, sensitive-action voice confirmations, safe mode, camera/mic-loss auto-downgrade |
| **📝 Semantic macros (v8)** | Macro format v2: `LOOK_FOR / WAIT_UNTIL / CLICK / TYPE / SCROLL / VERIFY / IF / RETRY / STOP` — legacy timestamp macros still replay unchanged |
| **🗣️ Natural language (v9)** | Beyond fixed commands: *"click that"*, *"scroll down a little"*, *"close this window"*, *"move that to the left"*, *"zoom in a lot"* — "that/this" resolves to your current gaze target, never an invented coordinate. All parsing is local |
| **🙌 Hands-free mode (v9)** | Eyes target, voice commands, dwell/blink confirm — full interaction without hand gestures. ESC is always an immediate escape |

**Everything from v5.0.0 is still here:** 14 hand gestures, swipe navigation, hybrid One Euro + Kalman cursor filter, 30-phrase voice control (incl. TURBO/MAD mode), pinch-to-zoom, adaptive calibration, macro recording, HUD, single-file simple mode.

## Install

```bash
pip install airmouse-9.0.0-py3-none-any.whl

# optional extras:
pip install "airmouse[voice]"   # SpeechRecognition + pyaudio (voice control)
pip install "airmouse[tts]"     # spoken confirmations (pyttsx3)
pip install "airmouse[ocr]"     # screen OCR targets (opt-in, off by default)
```

Requirements: Python 3.9+, webcam (any), microphone optional. All processing is **local** — camera frames, eye images, audio and screenshots are never transmitted by default. (Note: the optional Google-based speech recognizer in the voice extras sends audio to Google when enabled — that upstream behavior is documented by SpeechRecognition and can be disabled by not installing voice extras.)

## Quick start

```bash
airmouse                                   # v5 hand+gesture experience (unchanged)
airmouse --voice                           # + voice commands
airmouse --gaze                            # + eye tracking (run calibration first!)
airmouse --fusion                          # FUSION: gaze targets, hand confirms, voice intents
airmouse --hands-free                      # eyes target, voice commands, dwell confirm
airmouse --assist                          # observe everything, confirm every action
airmouse --gaze-calibrate                  # guided 9-point gaze calibration (save & exit)
airmouse --interaction gaze                # pick a mode explicitly
airmouse --version                         # 9.0.0
```

Legacy flags all still work: `--mode direct|ironman`, `--trackpad`, `--no-kalman`, `--no-zoom`, `--calibrate`, `--record NAME`, `--play NAME`, `--macros`, `--monitor`, `--precision`, …

## Gaze control

1. **Calibrate** (once): `airmouse --gaze-calibrate` — look at 9 dots; the fit is quality-graded (`good/fair/poor`), saved to `~/.airmouse/gaze_calibration.json`, and re-loadable. Recalibrate any time; delete the file to reset.
2. **Targeting**: your gaze point is filtered (outlier-rejected, confidence-weighted) and mapped onto semantic screen targets when possible.
3. **Confirmation** (never raw gaze alone):
   - `fusion` mode — look at a target, pinch (or say "click")
   - `hands-free` mode — hold your gaze (dwell-click, ~1 s) or say "click that"
   - long blink (~1.2 s) = **emergency stop** (configurable)
4. **Safety**: gaze below the confidence floor (default 0.55) can move nothing but attention. Blink-click is **off by default** because accidental blinks are real.

## Interaction modes

| Mode | Target | Confirm | Intent |
|---|---|---|---|
| `hand` | finger (v5) | gestures | gestures |
| `gaze` | eyes | dwell / voice | voice |
| `voice` | last stable target | — | voice |
| `fusion` | eyes (hand moves cursor) | pinch near target | voice + gestures |
| `hands-free` | eyes | dwell / blink / voice | voice |
| `assist` | any modality (observed) | explicit confirmation only | voice |

Switch by CLI, config (`[v9] fusion_mode`), hotkey **[f]** (cycles), or voice.

## Voice & natural language

30 fixed commands still work (`click`, `right click`, `scroll up`, `zoom in`, `freeze`, `precision`, `record`, `play macro`, `quit` …) — plus v9 natural phrasing resolved against your gaze target and screen context:

> "Click that." · "Open this." · "Scroll down a little." · "Zoom in a lot." · "Close this window." · "Move that to the left." · "Go back." · "Select this." · "Play this." · "Repeat that." · "Stop." · "Stop everything."

## Macros

```bash
airmouse --record demo        # legacy timestamped macro (clicks/scrolls/zooms)
airmouse --play demo          # replay
```

v2 semantic macros (JSON in `~/.airmouse/macros/`) add steps with verification:

```json
{"version": 2, "name": "accept_cookies",
 "steps": [
   {"op": "look_for",   "params": {"text": "Accept"}},
   {"op": "wait_until", "params": {"seconds": 0.4}},
   {"op": "click"},
   {"op": "verify",     "params": {"expected": {"type": "present"}}}
 ]}
```

## HUD

Badges: gesture, `FUSION:mode`, `GAZE:confidence%`, semantic `TARGET`, current `INTENT`, last `ACTION` outcome, `E-STOP`, plus the v5 set (`VOICE/KALMAN/ZOOM/REC/CAL`). Hotkeys: `[g]` gaze, `[f]` mode, `[x]` e-stop reset, `ESC` emergency stop, `[v/k/z/m]` v5 toggles.

## Configuration

`~/.airmouse/config.toml` — sections: `[direct] [one_euro] [physics] [ironman] [jitter] [gestures] [camera] [audio] [ui] [voice] [kalman] [zoom] [calibration]` and the v9 set:

```toml
[v9]
fusion_mode = "hand"          # hand | gaze | voice | fusion | hands_free | assist
gaze_enabled = false
gaze_min_confidence = 0.55    # gaze never acts below this
gaze_dwell_time = 1.0
gaze_blink_click = false      # off: accident-prone
gaze_long_blink_estop = true
screen_refresh_interval = 0.5
screen_ocr_enabled = false    # privacy: OCR is opt-in only
intent_min_confidence = 0.35
action_timeout = 2.0
action_max_retries = 1
safety_level = "normal"       # normal | careful | safe
max_actions_per_sec = 8
min_click_interval = 0.15
confirmation_timeout = 5.0
stream_loss_grace = 2.0
macro_max_steps = 200
telemetry_enabled = true      # perf report on shutdown
```

## Privacy

- Camera frames, eye/face landmarks, screen captures and audio stay **on your machine** by default.
- Screen OCR target detection is **disabled by default** and opt-in (`screen_ocr_enabled = true` or `pip install airmouse[ocr]`).
- No telemetry leaves the process; the shutdown "performance report" is printed locally.
- See the voice note above for the optional SpeechRecognition extra.

## Testing & verification

```bash
python -m pytest tests/ -q          # 497 tests
```

The suite covers unit tests for every new module, integration tests for `gaze→target→action`, `hand→target→action`, `voice→intent→action`, fusion combinations, failure tests (camera loss, face lost, low confidence, conflicting modalities, e-stop, stream-loss downgrade), v5 regression tests, performance benchmarks, and a deterministic end-to-end simulation (`simulated user → gaze target → pinch → click → screen state → verifier → success`).

**Hardware verification status:** all tests are deterministic simulations. MediaPipe FaceMesh is smoke-verified to load and run in this environment (blank-frame processing), and the calibration workflow is verified end-to-end via `--gaze-calibrate --gaze-sim` (simulated eye, quality "good", ~4 px residual). **Physical webcam/eye-tracking behavior is hardware-unverified** in this build environment — run `--gaze-calibrate` on real hardware and expect to tune `gaze_min_confidence` per user.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Gaze cursor jittery | Recalibrate (`--gaze-calibrate`); raise `gaze_min_confidence`; check lighting |
| Gaze clicks feel random | That's why blink-click is off — use pinch/voice confirmation; raise confidence floor |
| Camera lost mid-session | Safety auto-downgrades to SAFE_MODE after `stream_loss_grace`; actions resume when camera returns |
| "needs_confirmation" blocks | Sensitive action (close/paste/hotkey…) — say "confirm" or re-issue |
| E-stop latched | Press `[x]` or ESC again, or restart |
| Voice silent | `pip install SpeechRecognition pyaudio`, run with `--voice`, check mic index |
| Gaze calibration poor | Sit 50–80 cm, steady head, even lighting; 9-point works best |

## Known limitations

- Webcam gaze accuracy (~1–3°) is inherently coarser than a mouse — it's a **targeting** aid, not pixel-precise pointing; confirmation patterns make this safe.
- Head pose must stay reasonably frontal; large head rotation lowers confidence (by design).
- Accessibility/DOM providers are platform-dependent best-effort; the geometry fallback always works.
- `airmouse_simple.py` remains the v5 single-file experience (v9 subsystems require the package).
- Physical-hardware claims are explicitly **unverified** in this build environment (see Testing above).

## License

MIT — see `LICENSE`.
