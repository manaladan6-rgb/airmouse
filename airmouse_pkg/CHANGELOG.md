# Changelog

## v9.0.0 — MULTIMODAL INTELLIGENCE EDITION

The defining evolution: from gesture/voice mouse to a multimodal
computer-interaction platform combining eyes, hands, voice, screen
understanding, intent, automation, verification and recovery.

### Added — v6 GAZE
- `gaze.py`: FaceMeshTracker (mediapipe refined iris landmarks 468–477),
  GazeEstimator (iris-offset gaze, EAR eye state, head-pose gating,
  confidence), BlinkClassifier (blink / long blink / double blink / wink
  with confidence gating), DwellDetector (fixation hysteresis + one-shot
  dwell), GazeEngine orchestrator with FACE_FOUND/LOST events
- `gaze_filter.py`: GazeFilterPipeline — outlier rejection,
  confidence-weighted adaptive temporal smoothing (~2.5× jitter
  reduction, bounded lag), quantitative stats hooks
- `gaze_calibration.py`: 9/5/4/1-point workflow, two-pass 5σ-clipped MAD
  outlier gate, least-squares affine fit, px residual quality grading
  (good/fair/poor/incomplete), atomic JSON v2 persistence,
  bit-stable map() across save/load
- `--gaze-calibrate` guided flow + `--gaze-sim` deterministic simulation

### Added — v7 FUSION + SCREEN UNDERSTANDING
- `fusion.py`: MultimodalFusion — 6-modality arbitration with per-mode
  priority matrix (HAND/GAZE/VOICE/FUSION/HANDS_FREE/ASSIST), staleness
  expiry, conflict records, `hand:pinch` / `voice:click` confirmation
  patterns, rate-limited mode switching
- `screen_perception.py`: layered providers (accessibility → OCR
  *(opt-in, off by default)* → geometry) merged into a unified
  ScreenModel with dedupe, AppContext resolution hook, semantic target
  descriptions, guaranteed coordinate fallback

### Added — v8 INTENT / ACTION / VERIFICATION / SAFETY
- `intent.py`: IntentEngine — multimodal decisions + NL utterances +
  gesture queue → structured Intents; confidence decay; uncertain-gaze
  click suppression; sensitive-action marking; per-tick cap with
  carry-over; EMERGENCY_STOP always passes
- `actions.py`: ActionEngine (plan → preconditions → execute → report,
  bounded retries), PynputExecutor (lazy, headless-safe), MockExecutor
- `verification.py`: ActionVerifier (per-expected-type checkers with
  similarity scoring), RecoveryManager (retry → adjusted → notify,
  never auto-retries sensitive/blocked actions)
- `safety.py`: SafetySystem — e-stop latch, SAFE_MODE whitelist,
  confidence gates (incl. dedicated gaze gate), sliding-window rate
  limiter, click cooldowns, one-shot confirmation flow (monotonic
  expiry), camera/mic-loss auto-downgrade + restore
- `context.py`: AppContext detection (browser/editor/terminal/video/…)
  + ContextProfile action adaptations
- `macros.py` v2: semantic programs (LOOK_FOR/WAIT_UNTIL/CLICK/TYPE/
  SCROLL/HOTKEY/VERIFY/IF/RETRY/STOP) with max-steps guard, safety
  integration, legacy v1 replay fully preserved

### Added — v9 AGENT
- `nl_control.py`: local natural-language parsing ("click that",
  "scroll down a little", "close this window", …) with target_ref
  resolution contract and legacy fallback
- `hands_free.py`: HandsFreeController — gaze+voice+dwell/blink ticks
  through the full safety pipeline; blink-click OFF by default;
  long-blink e-stop
- `agent.py`: InteractionAgent — full pipeline orchestrator with
  Telemetry (fps/latency/counters) and shutdown reporting

### Changed
- `__main__.py`: v9 CLI (--gaze --no-gaze --gaze-calibrate --fusion
  --hands-free --assist --interaction --no-voice --version), agent
  wiring (confirmed hand gestures become fusion confirmations; fusion
  without gaze falls back to v5 gestures), v9 HUD badges, hotkeys
  [g]/[f]/[x] + ESC e-stop, telemetry shutdown report
- `config.py`: new `[v9]` section (19 keys, privacy-safe defaults)
- version 5.0.0 → 9.0.0 (package, CLI, banner)

### Preserved (v5 regression suite green)
14 gestures, swipes, DirectTracker + hybrid One Euro + Kalman, 30 voice
commands + turbo mode, pinch-to-zoom, adaptive calibration, v1 macros,
HUD, `airmouse_simple.py`, wheel packaging.

### Verification
- 497 tests passed / 0 failed (unit, integration, failure modes,
  regression, performance, E2E simulation)
- Performance (this environment): hand filter 0.040 ms/call; gaze
  filter+map 0.011 ms; full agent tick 0.032 ms; NL parse 0.0055 ms
- Hardware-unverified: physical webcam gaze behaviour (deterministic
  simulation + FaceMesh smoke only)

## v5.0.0 — VOICE + KALMAN EDITION
- Voice control: 30 commands, normal/high/turbo (MAD) sensitivity
- Hybrid One Euro + Kalman fusion cursor filter
- Pinch-to-zoom, adaptive calibration, macro recorder, single-file mode

## v4.2.0 — TRACKPAD EDITION
- Trackpad feel: tap=click, hold=drag, 2-finger scroll

## v4.1.0 / v4.0.0
- Pure-accuracy direct tracking; One Euro filter; precision mode
