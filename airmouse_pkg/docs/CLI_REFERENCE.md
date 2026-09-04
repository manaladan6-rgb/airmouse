# AirMouse v11.5 — CLI Reference

Verified against `airmouse/__main__.py` (v11.5.0). Every flag below is
registered exactly as listed; older flags remain functional
(`--help` in the package is the executable truth).

```bash
airmouse [FLAGS] [SUBCOMMAND]
python -m airmouse [FLAGS] [SUBCOMMAND]   # equivalent without install
```

## Version

| Flag | Effect |
|---|---|
| `--version` | prints `AirMouse v11.5.0 — Adaptive Human-Computer Intelligence Edition` and exits |

## v11.5 flags

| Flag | Effect |
|---|---|
| `--intelligence` | enable the adaptive-intelligence plugin (also default per config) |
| `--no-intelligence` | run the session with the plugin fully off (core unaffected) |
| `--dictation` | voice-typing session (spoken punctuation + edit commands) |
| `--transcribe` | live transcription session (streaming partials/finals, HUD caption) |
| `--teacher` | teacher mode: presentation control + lecture timeline |
| `--student` | student mode: notes + study timer + sources |
| `--office` | office mode: meeting control + task capture |
| `--meeting` | meeting mode: transcription + structured summary |
| `--research` | research mode: QUESTION→…→ORGANIZE assistance |

## v10 flags (preserved)

| Flag | Effect |
|---|---|
| `--offline` | engage the offline gate (cloud ASR/TTS, CDP, updates, telemetry blocked) |
| `--browser` | local browser bridge control (simulated/CDP transport) |
| `--browser-bridge` | localhost MV3-extension bridge on 127.0.0.1:17843 |
| `--gesture` | full gesture registry incl. custom sequence gestures |
| `--rf` | RF modality (idles cleanly without hardware) |
| `--voice-mode {normal,high,turbo,command,dictation,hybrid}` | v5 sensitivity or v10 voice mode |

## v9 flags (preserved)

| Flag | Effect |
|---|---|
| `--gaze` / `--no-gaze` | enable/disable webcam gaze |
| `--gaze-calibrate` | guided gaze→screen calibration, save, exit |
| `--fusion` | FUSION mode (gaze targets + hand confirms + voice) |
| `--hands-free` | HANDS-FREE mode (eyes target, voice commands, dwell) |
| `--assist` | ASSIST mode (observation, actions need confirmation) |
| `--interaction {hand,gaze,voice,fusion,hands-free,assist}` | explicit v9 interaction mode |
| `--no-voice` | disable voice control |

## v5 / v3–v4 flags (preserved)

| Flag | Effect |
|---|---|
| `--voice` | v5 voice commands (SpeechRecognition + pyaudio) |
| `--mic N` | microphone index |
| `--no-kalman` / `--no-zoom` / `--no-calibration` | disable v5 features |
| `--calibrate` | guided 8 s hand calibration sweep |
| `--record NAME` / `--play NAME` / `--macros` | macro recorder / replay / list |
| `--trackpad` | trackpad feel (tap=click, hold=drag) |
| `--monitor N` / `--list-monitors` | monitor selection |
| `--settings` | open the settings GUI |
| `--autostart {on,off}` | manage auto-start |
| `--precision` | start in precision mode |
| `--mode {direct,ironman}` | v4 tracking mode |
| `--cam N` / `--no-cam` / `--no-sound` | camera index / overlays / audio |
| `--power P` / `--scale S` | sensitivity curve tuning |
| `--skip` / `--tutorial` | tutorial control |

## Subcommands (print and exit)

| Subcommand | Prints |
|---|---|
| `intelligence` | plugin status — state, learning/privacy flags, model size vs the ~30 MB budget, memory patterns, vocabulary terms, workflows |
| `memory` | top learned interaction patterns (frequency / success rate / corrections) |
| `vocabulary` | learned terms + corrections (raw → preferred) |
| `workflows` | approved workflows (steps, success/failure counts, destructive flag) |
| `self-test` | 15-component report — PASS / FAIL / OPTIONAL / HARDWARE per subsystem |
| `commands` | the 75-command v10 grammar by namespace |
| `gestures` | built-in + custom gesture mappings |
| `voice-status` | which offline ASR providers are actually installed |
| `browser` | browser bridge/controller status |
| `offline-test` | 18-check full-stack selftest with networking really blocked |
| `diagnostics` | event bus / modality / sensor report |

## Typical combinations

```bash
airmouse                                         # v5 hand+gesture experience
airmouse --intelligence                          # + adaptive intelligence (default via config)
airmouse --dictation --intelligence              # voice typing + personal formatting
airmouse --transcribe --offline                  # local-only live transcription
airmouse --teacher --voice-mode hybrid           # teach hands-free
airmouse --meeting --transcribe --offline        # meeting capture, hard-offline
airmouse --student --dictation                   # study session with voice notes
airmouse --research --browser                    # research navigation w/ browser control
airmouse --offline --voice-mode hybrid --gesture --browser --intelligence   # everything
airmouse self-test                               # honest health report
airmouse offline-test                            # prove the offline story (18/18)
```

## Configuration

All flags have TOML equivalents in `~/.airmouse/config.toml`. The
v11.5 sections (backward compatible — all previous sections intact):
`[intelligence] [learning] [memory] [transcription] [dictation]
[prediction] [emoji] [teacher] [student] [office] [meeting]
[research] [developer] [accessibility] [workflow] [privacy]`.
Key defaults are documented in `docs/V11_5_ARCHITECTURE.md` and
`docs/PRIVACY.md`.

## HUD badges (during a session)

| Badge | Meaning |
|---|---|
| `AI:…` | intelligence plugin state (e.g. ON / PRIVACY / OFF) |
| `MODE:…` | active v11.5 interaction mode (TEACHER / MEETING / …) |
| `SUG:…` | latest proactive suggestion |
| `"…"` | latest transcript caption (2 s) |
| `V10 CMD RF BROWSER VER` | v10 badges (unchanged) |
