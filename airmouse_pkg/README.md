# AirMouse v10.0.0 — UNIVERSAL OFFLINE INTERACTION ENGINE

Voice + hand + gaze + RF + keyboard/mouse + screen + browser → one
**event bus** → fusion → context → intent → action → verification →
recovery. **Fully offline-capable: no LLM, no cloud AI, no network
required for any control path.**

![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue)
![Platform](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macos-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)
![Offline](https://img.shields.io/badge/offline-capable-success)

```
  🎤 VOICE   🖐 HAND   👁 GAZE   📡 RF   ⌨ KB/MOUSE   🖥 SCREEN   🌐 BROWSER
     └────────┴────────┴────────┴─────────┴─────────────┴───────────┘
                                   │  normalized Events
                                   ▼
              ╔══════════ EVENT BUS (local, bounded) ══════════╗
              ╚═════════════════════╦══════════════════════════╝
                        ▼                        ▼
                 MULTIMODAL FUSION        CONTEXT ENGINE
                 (arbitration,           (app/window/browser/
                  confirmations)          gaze target, "that/this")
                        └───────────┬───────────┘
                                    ▼
                             INTENT ENGINE  (52 intent types)
                                    ▼
        ┌────────────── SAFETY (e-stop · rate limits · gates · ──────────────┐
        │                 sensitive-action confirmations)                    │
        │  ▼                                                                 │
        │  OFFLINE GATE ──► ACTION ENGINE (32 action types, allowlisted      │
        │                 executors) ──► VERIFICATION ──► RECOVERY           │
        └────────────────────────────────────────────────────────────────────┘
```

The **safety system** gates every action; the **offline gate** blocks
network-dependent features while every local path keeps running.

## What's New in v10.0.0

| Subsystem | What it does |
|---|---|
| **🚌 Event bus** | Every modality publishes normalized `Event`s (14 kinds); `EventBus` with bounded per-subscriber queues (drop-oldest, producers never block), history ring, stats. Pure in-process — works with networking disabled |
| **📖 Command grammar** | 75 deterministic voice commands in 10 namespaces (engine/mouse/system/window/application/files/text/navigation/media/browser). Template grammar with `<slot>` entities, literal-specificity + namespace-priority resolution, ambiguity flags. NO LLM, NO probabilistic parsing |
| **🎙️ Offline voice** | Pluggable local ASR (`Simulated` / `PocketSphinx` / `Vosk` / `Whisper` providers, auto-detected), energy VAD with hysteresis, wake-word gate, COMMAND / DICTATION / HYBRID modes, dictation buffer with commit markers |
| **🧭 Context engine** | Focused app/window, browser state (tab/URL), gaze target with 2 s TTL, selection, recent action — powers deictic resolution: *"click that"*, *"close it"*, *"open this"* |
| **🤟 Gesture registry** | Full vocabulary: v5 superset + `pinch_hold` / `pinch_release` / `double_pinch` / `grab` / `grab_move` / `circular_cw` / `circular_ccw`. Built-in + **user-defined sequence gestures** (JSON), deterministic sequence matcher, double-pinch synthesis |
| **📡 RF sensing** | Optional `RFProvider` abstraction + simulated/dummy providers + event-bus bridge. **Never required** — idles cleanly when no RF hardware exists; system degrades via the combo ladder |
| **🖥️ System + file actions** | 16 system ops (volume/media/lock/sleep/power/brightness/bluetooth) + 8 file ops. Shell-free argv-only subprocess, allowlisted root directories, filename sanitizing, URL scheme validation (http/https/file only) |
| **⚡ Universal actions** | Canonical vocabulary: 52 intent types → 32 action types, v10 param normalization, destructive-op confirmation flags, executor injection (pointer/keyboard/system/file/browser) |
| **🌐 Browser control** | Local browser control in 3 layers: transport (deterministic simulated bridge / guarded Chrome DevTools Protocol / localhost MV3-extension bridge on 127.0.0.1:17843), semantics (*"click the login button"*, *"switch to the youtube tab"*, *"search for …"*), verification (before/after state diff). Page content is data, never commands |
| **🔌 Offline mode** | `--offline` engages a runtime gate that blocks cloud ASR/TTS, CDP, updates, telemetry — while the full local stack keeps working. `airmouse offline-test` runs the entire pipeline 13/13 checks with networking **really** disabled at socket level |
| **🙌 Hands-free combos** | 8 named sensor combos (`voice_only` … `full_fusion`); sensor-health tracker downgrades to the largest alive subset automatically and recovers when sensors return |
| **🛡️ Safety (extended)** | Sensitive types now include SHUTDOWN/RESTART/LOCK/SLEEP/CLOSE_TAB; FILE_OP/SYSTEM_OP get param-level destructive refinement (delete = destructive, create = not) |

**Everything from v5–v9 is preserved** — see below.

## Preserved from earlier versions

- **v9** — multimodal fusion (6 modes), webcam gaze tracking + calibration + filtering, screen understanding (accessibility → OCR *(opt-in)* → geometry), intent engine with confidence propagation, action engine + verification + recovery ladder, safety system (e-stop, rate limiter, confirmations), semantic macros v2, natural language ("scroll down a little"), hands-free mode, InteractionAgent with telemetry
- **v5** — 14 hand gestures + swipes, hybrid One Euro + Kalman cursor filter, 30-phrase voice control (incl. TURBO mode), pinch-to-zoom, adaptive calibration, macro recording, HUD, single-file `airmouse_simple.py`
- **v4/v3** — trackpad mode, direct/ironman tracking, precision mode, media keys, multi-monitor, autostart, settings GUI

All 497 pre-existing v9 tests still pass unchanged (regression gate).

## Install

```bash
pip install airmouse-10.0.0-py3-none-any.whl

# optional extras:
pip install "airmouse[voice]"   # SpeechRecognition + pyaudio (v5 mic voice control)
pip install "airmouse[tts]"     # spoken confirmations (pyttsx3)
pip install "airmouse[ocr]"     # screen OCR targets (opt-in, off by default)

# optional offline ASR engines (any one enables real mic → text offline):
pip install pocketsphinx        # or: pip install vosk   or: pip install openai-whisper
```

Requirements: Python 3.9+. Webcam optional (hand/gaze), microphone
optional (voice), RF hardware optional (RF modality). **Everything else
is stdlib.** All processing is local.

## Quick start

```bash
airmouse                                        # v5 hand+gesture experience (unchanged)
airmouse --voice-mode hybrid                    # offline voice: commands + dictation
airmouse --offline                              # hard-offline mode (blocks cloud features)
airmouse --browser                              # + semantic browser control
airmouse --browser --browser-bridge             # + localhost extension bridge (:17843)
airmouse --rf                                   # + RF modality (idles without hardware)
airmouse --gesture                              # + full gesture registry (custom sequences)
airmouse --offline --voice-mode hybrid --gesture --browser   # everything, offline

# info & diagnostics subcommands:
airmouse commands                               # print all 75 commands by namespace
airmouse gestures                               # print the gesture registry
airmouse voice-status                           # which offline ASR providers are available
airmouse browser                                # browser bridge/controller status
airmouse offline-test                           # 13-check full-stack selftest, network truly blocked
airmouse diagnostics                            # event bus / modality / sensor report
```

Legacy flags all still work: `--voice --gaze --fusion --hands-free
--assist --gaze-calibrate --trackpad --calibrate --record NAME --play
NAME --macros --monitor N --precision …`

## Voice commands

75 commands, 10 namespaces, deterministic grammar (exact match → 1.00
confidence; ambiguous → flagged 0.72; fuzzy tolerance 0.62–0.85; below
0.62 → no match). Sensitive commands are flagged for the safety layer's
confirmation gate.

| Namespace | Examples |
|---|---|
| engine | "stop everything", "switch to hands-free", "record macro", "quit" |
| mouse | "click", "double click", "right click", "scroll up" |
| system | "volume up", "mute", "lock screen", "shutdown" *(confirm)* |
| window | "minimize window", "maximize", "snap left" |
| application | "open firefox", "close this app" |
| files | "open file notes.txt", "delete file old.txt" *(confirm)* |
| text | "select all", "undo", "delete word" |
| navigation | "scroll down", "go to top", "page down" |
| media | "play music", "next track" |
| browser | "new tab", "close tab" *(confirm)*, "go to youtube.com" |

Dictation mode commits buffered speech on markers ("commit", "new
paragraph", "submit text", "end dictation") or terminal punctuation;
hybrid mode tries command grammar first and falls back to
dictation when the utterance looks like prose. Deictic commands
("click that", "close it") resolve against the context engine's gaze
target / selection / recent action — never an invented coordinate.

**Full list:** `airmouse commands`.

## Gestures

The registry contains the complete vocabulary — built-in hand gestures
plus the v10 extensions — and maps each to its intent:

- **Built-in (v5 superset):** pointing, peace, three, palm, fist,
  pinch, thumbs up, gun, swipe left/right/up/down
- **v10 set:** `pinch_hold`, `pinch_release`, `double_pinch`
  (synthesized from two pinches within 0.6 s), `grab`, `grab_move`,
  `circular_cw`, `circular_ccw`, directional motion
- **Custom sequence gestures** — define your own multi-step patterns in
  JSON and they flow through the same intent/safety pipeline:

```json
{
  "name": "air_delete",
  "pattern": ["fist", "swipe_left", "pinch_release"],
  "intent": "hotkey",
  "params": {"keys": ["ctrl", "backspace"]}
}
```

Saved at `~/.airmouse/gestures.json` (override with the
`AIRMOUSE_GESTURES` env var); loaded automatically at startup.
Print the live registry with `airmouse gestures`.

## Browser control

Three layers, all local:

1. **Transport** — pick one:
   - **Simulated bridge** (default, deterministic): great for tests and offline demos.
   - **CDP bridge**: Chrome/Edge via the DevTools Protocol (stdlib-only implementation; guarded — start Chrome with `--remote-debugging-port`, set `browser_cdp_port`).
   - **Extension bridge**: run `airmouse --browser --browser-bridge` and load the shipped MV3 extension from `airmouse/browser_extension/` (see its README). It POSTs page metadata (roles, text, normalized bounding boxes, focus; password fields masked) to `http://127.0.0.1:17843/state` — **localhost-only**, data only, never executed.

2. **Semantics** — the resolver matches a fixed template grammar against the element map:
   > "click the login button" · "switch to the youtube tab" · "search for air mouse" · "type hello into the search box" · "go back" · "refresh" · "open github.com in a new tab"

3. **Verification** — every browser action is checked with a before/after state diff (`passed` / `failed` / `unknown`), mirroring the v8 verification ladder.

**Security stance:** page content is **untrusted data**. A page whose
text says "shut down the computer" can only ever be *clicked as a
button* if you say so — page text can never become a command, and the
CDP adapter evaluates only fixed snippets built from our parameters.

## Offline mode

```bash
airmouse --offline          # runtime gate: cloud ASR/TTS, CDP, updates, telemetry blocked
airmouse offline-test       # end-to-end proof
```

`offline-test` runs the **entire stack** — voice grammar → intent,
voice → context → intent, gesture registry with a custom sequence, RF
bridge, simulated browser (bridge + semantic resolution + execution +
verification), fusion → intent → action → verify pipeline, event bus,
offline gate semantics — inside `network_isolation()`, which really
monkeypatches `socket.connect`/`create_connection` to raise. A final
check proves a real connection attempt is refused. **13/13 checks.**

## Safety

- **Emergency stop:** ESC key, long blink (v9), "stop everything" (voice) — latches until reset (`[x]`)
- **Rate limiting:** sliding-window actions/sec cap + click cooldowns
- **Sensitive actions** (close, paste, hotkey, **shutdown, restart, lock, sleep, close tab**, destructive file/system ops) require an explicit spoken/typed confirmation before execution — flagged at intent level *and* refined at param level (e.g. `delete file` is destructive, `create file` is not)
- **Confidence gates:** low-confidence gaze/voice can move nothing but attention
- **Sensor loss:** auto-downgrade to the largest alive sensor combo; SAFE_MODE after `stream_loss_grace`; automatic recovery
- **File/system ops:** argv-only subprocess (no shell), allowlisted base roots, traversal refused, filename sanitizing, URL schemes restricted to http/https/file

## Configuration

`~/.airmouse/config.toml` — all previous sections (`[direct] [one_euro]
[physics] [ironman] [jitter] [gestures] [camera] [audio] [ui] [voice]
[kalman] [zoom] [calibration] [v9]`) plus the v10 set:

```toml
[v10]
offline = false                # TRUE OFFLINE: block network-dependent features
voice_mode10 = "hybrid"        # command | dictation | hybrid
voice_command_min_confidence = 0.62
wake_word_required = false
dictation_max_chars = 2000
browser_enabled = true         # local browser bridge control
browser_bridge_port = 17843    # localhost-only extension endpoint
browser_cdp_port = 9222        # Chrome --remote-debugging-port
gesture_registry_enabled = true
rf_enabled = true              # RF modality (idles without hardware)
rf_min_confidence = 0.5
telemetry_enabled = true       # LOCAL perf report on shutdown
```

## Privacy

- **Local-first:** no cloud AI anywhere in the control path. Camera
  frames, eye/face landmarks, audio, screen captures, and page metadata
  never leave your machine.
- Voice text is processed by the deterministic local grammar; if you
  install an offline ASR engine (PocketSphinx/Vosk/Whisper) transcription
  runs on-device. The optional v5 `SpeechRecognition` extra can use a
  cloud recognizer — it is bypassed when `offline = true`.
- Screen OCR remains **opt-in** (`screen_ocr_enabled = true`).
- The browser extension talks only to `127.0.0.1:17843` and masks
  password fields.
- No telemetry leaves the process; the shutdown report is printed locally.

## Hardware requirements

| Modality | Hardware | Required? |
|---|---|---|
| Hand gestures | webcam (MediaPipe) | optional |
| Gaze | webcam (MediaPipe FaceMesh) | optional |
| Voice | microphone + (optional) an offline ASR engine | optional — transcript injection / simulation otherwise |
| RF sensing | RF hardware implementing `RFProvider` | **never required** — idles without |
| Browser | Chrome/Edge for CDP or the MV3 extension | optional — simulated bridge otherwise |
| Screen | any display | for screen targets/geometry |

## Limitations & honest verification status

- **No physical hardware was available in the build environment.** All
  v10 behavior is **simulation-verified** via deterministic simulators.
  Camera/eye-tracking/mic/audio-device paths on real hardware are
  **hardware-unverified**.
- The CDP browser bridge is guarded and stdlib-only; against a real
  Chrome instance it is **unverified** (tests cover graceful
  unavailability only). The MV3 extension source ships and is
  lint-reviewed, but **loading it in a real browser is unverified**.
- PocketSphinx/Vosk/Whisper code is present with guarded imports and
  runtime auto-detection; none are installed in the build sandbox, so
  the **transcript-injection path is what is verified** (not live ASR).
- Webcam gaze accuracy (~1–3°) is a targeting aid, not pixel-precise —
  confirmation patterns make it safe (v9 behavior, unchanged).
- `airmouse_simple.py` remains the v5 single-file experience.

## Verification methodology

```bash
python -m pytest tests/ -q        # 630 tests
airmouse offline-test             # 13/13 checks with networking truly blocked
```

630 passed, 0 failed, 0 skipped (Python 3.12.14, Linux sandbox):
**497** pre-existing v9 tests (all preserved) + **19** browser tests +
**114** v10 tests. New suites cover grammar/intents, voice modes/VAD/
wake-word/dictation, event bus, context engine, gesture registry +
sequences, RF degradation, system/file executors (allowlists, traversal,
URL validation, dry-run), safety confirmations, hands-free combos,
offline isolation, browser bridge/semantics/verification, full fusion
pipelines, and performance budgets (grammar 50 utterances < 0.5 s,
1000 bus events < 0.5 s, 1000 context resolves < 0.2 s — all far under
budget in measurement). Full details: `VERIFICATION_REPORT.md`;
architecture deep-dive: `docs/V10_ARCHITECTURE.md`.

## License

MIT — see `LICENSE`.
