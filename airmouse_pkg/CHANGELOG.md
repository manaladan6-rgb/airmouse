# Changelog

## v11.5.0 — ADAPTIVE HUMAN-COMPUTER INTELLIGENCE

The defining evolution: the deterministic v10 interaction core gains an
OPTIONAL personal-intelligence layer — a compact local model (few KB
fresh, ~30 MB hard budget) plus bounded memory, vocabulary,
self-tuning, workflow discovery, live transcription, voice typing,
universal text control, a world model, six interaction modes,
accessibility profiles, Fusion 2.0, and a privacy dashboard.
No LLM, no cloud, no network; PREDICTION ≠ EXECUTION everywhere.

### Added — Intelligence subpackage (`airmouse/intelligence/`, optional plugin)
- `model.py`: `PersonalInteractionModel` — `NGramModel` (order 3,
  backoff, capped counts) + `ActionMarkov` + `CommandModel`
  (frequency + time-of-day) + `EmojiModel` (context tags) +
  `FeatureWeights`; quantized 8-bit packed artifact with magic
  `AIMM` + format version; hard capacity budget
  (`MODEL_CAPACITY_BYTES_DEFAULT` ≈ 30 MB, growth refused beyond it,
  `capacity_hits` counter); deterministic pruning; `save/load/stats`
- `memory.py`: `InteractionMemory` + `PatternRecord` (pattern/
  frequency/confidence/last_seen/context/success_rate/correction_count/
  preferred_action); **fail-closed sensitive-data scrubbing**
  (`is_sensitive` / `scrub_pattern`) — passwords/tokens/credentials
  refused outright, URL creds and token-like blobs redacted to
  `[redacted-*]` placeholders; bounded 5,000 patterns; validated
  import/export
- `vocabulary.py`: `PersonalVocabulary` — terms (20k cap) +
  corrections (5k cap) with `apply_corrections` (e.g. "Hydra Link" →
  "HydraLink"), validated import/export
- `prediction.py`: `Predictor` → explainable `Prediction`
  (kind/value/confidence/reason/alternatives ≤ 8); deterministic
  `EMOJI_KEYWORDS` baseline map; `predict_next_action/command`,
  `complete_text/phrase_completions`, `suggest_emoji`,
  `predict_target`
- `selftune.py`: `SelfTuner` — `TUNABLES` (10 parameters) with hard
  min/max bands, steps, `min_samples` gates, EMA stats; `apply()`
  refuses out-of-band values
- `personalization.py`: `GestureProfile` / `GazeProfile` /
  `VoiceProfile` (frequent commands, false activations, **alias
  learning** e.g. "launch browser" → "open browser"); bounded samples
- `workflows.py`: `WorkflowDiscovery` (rolling window, 3–8-step
  patterns, ≥ 3 repetitions, **suggestions only — nothing auto-created**);
  `WorkflowStore` (≤ 200 workflows × 24 steps, step names validated
  `^[a-z0-9][a-z0-9_-]{0,63}$`); `WorkflowRunner` (preview-first,
  destructive workflows require prior preview, destructive steps
  require per-run confirmation, conditions checked);
  `ProactiveAssistant` + `Suggestion` (can never execute;
  destructive-looking suggestions suppressed; `prepare()` refuses
  URL/path/command-like resources)
- `plugin.py`: `IntelligencePlugin` facade — the ONLY object the core
  touches; **never raises**; `IntelligenceState` = available/disabled/
  unavailable/corrupted/incompatible/out_of_memory/privacy_paused/
  learning_paused; artifact persistence in `~/.airmouse/intelligence/`
  (atomic writes); `export_profile`/`import_profile` validated
  per-section and scrubbed; `delete_learned_data`/
  `reset_personalization`/`clear_history`; `pause_learning`/
  `set_privacy_mode`

### Added — Voice & text
- `transcription.py`: `LiveTranscriptionEngine` — streaming
  partials/finals pipeline (mic → preprocess → `EnergyVAD` → streaming
  ASR → partial → stabilization → punctuation → capitalization →
  personal vocabulary → final); `StreamingProviderAdapter` (wraps v10
  batch providers with a deterministic partial stream) +
  `SimulatedStreamingProvider` (always available); `apply_spoken_
  punctuation` (30+ phrase table), `insert_discourse_commas`
  (deterministic heuristic), `capitalize_text`, `spell_numbers`,
  `wer()` evaluator; bounded history (≤ 500 segments, ≤ 200k buffer) +
  txt/json/md export (≤ 8 MB) + search; pause/resume/stop; callbacks;
  metrics; honest provider availability in `status()`
- `dictation_text.py`: `VoiceTypingEngine` (COMMAND/DICTATION/HYBRID;
  spoken formatting; 16 edit commands incl. "delete last word",
  "scratch that", "replace X with Y", "capitalize that", undo/redo);
  `TextPredictor` (context-aware word/phrase completion);
  `EmojiSuggester` (30 s cooldown, ≤ 3 suggestions, learns
  preferences)
- `text_control.py`: `TextController` — 16 `TextOp`s (TYPE/SELECT/
  DELETE/REPLACE/COPY/PASTE/UNDO/REDO/CUT/MOVE/CAPITALIZE/LOWERCASE/
  UPPERCASE/FORMAT/NEW_LINE/NEW_PARAGRAPH), keyboard fallback,
  never coordinate-dependent; pluggable `TextExecutor`

### Added — Context, modes, fusion, privacy
- `world_model.py`: `WorldModel` — bounded snapshot (application/
  window/visible targets/gaze target/text field/recent action+command/
  mode + explainable likely intent; destructive intents never
  surfaced); `ContextualCommandResolver` — 12 deictic families
  (15 utterances: "click that"…"save that") with a deterministic
  confidence model; **low confidence ⇒ `requires_confirmation`
  (ASK, don't guess)**
- `modes.py`: `ModeController` + `MODE_REGISTRY` phrase tables for
  **teacher / student / office / meeting / research / developer**;
  `PresentationController` via GENERIC hotkeys (works with
  PowerPoint/Keynote/LibreOffice/browser slides); `TimelineSession`,
  `StudyTimer`, `NotesStore`, `SourceCapture` (verbatim, never
  fabricates), `MeetingMode` structured summary (transcript/timeline/
  important/action items/decisions/questions — **no speaker-ID
  claims**), `AccessibilityProfiles` (8 profiles + custom chains,
  fallback via v10 `SensorHealth.alive()`), `DeveloperMode`
- `fusion2.py`: `FusionEngine2` — 9 weighted signals (voice .30,
  gesture .25, gaze .25, personal history .10, keyboard/browser/
  application context/recent action/prediction .05 each);
  `ConflictResolver` (conflicts scale confidence ×0.5 and force
  confirmation; prediction never outranks observation);
  `FusedIntentCandidate.executable` (requires a non-prediction
  signal); `RFExtendedProvider` protocol (presence/motion/
  gesture_classification/direction/range/velocity) + `RFNoHardware`
  honest default
- `privacy.py`: `PrivacyDashboard` — flags (learning/memory/
  transcription_history/vocabulary_learning/workflow_learning ON;
  **telemetry OFF by default; cloud structurally impossible** —
  `__post_init__` forces it off and `set("cloud")` refuses);
  OFFLINE/ONLINE/PRIVACY connection states; `delete_learned_data`/
  `reset_model_personalization`/`clear_interaction_history`/
  `export_profile`/`import_profile`; bounded local audit log
- `selftest.py`: `run_self_test`/`format_self_test` — 15 components
  with PASS/FAIL/**OPTIONAL**/​**HARDWARE** statuses (RealLocalASR =
  OPTIONAL, Camera = HARDWARE — missing hardware is never a failure)

### Changed
- `agent.py`: guarded intelligence wiring (`intelligence`,
  `world_model`, `fusion2`, `modes`, `text_controller`,
  `voice_typing`, `transcription` overrides); `_learn_from_report` —
  verified actions become learning events (model + memory + workflow
  discovery), guarded, never raises
- `__main__.py`: flags `--intelligence/--no-intelligence/
  --dictation/--transcribe/--teacher/--student/--office/--meeting/
  --research`; subcommands `intelligence/memory/vocabulary/workflows/
  self-test` (v10 subcommands preserved); HUD badges `AI:` `MODE:`
  `SUG:` + transcript caption
- `config.py`: v11.5 TOML keys — `[intelligence] [learning] [memory]
  [transcription] [dictation] [prediction] [emoji] [teacher] [student]
  [office] [meeting] [research] [developer] [accessibility]
  [workflow] [privacy]` — fully backward compatible
- `offline.py`: `run_offline_selftest` extended **13 → 18 checks**
  (adds `intelligence_offline`, `memory_offline`, `vocabulary_offline`,
  `transcription_offline`, `fusion2_offline`) — the adaptive
  intelligence is proven to learn and predict under REAL socket-level
  network isolation
- version 10.0.0 → 11.5.0 (package, CLI banner "Adaptive
  Human-Computer Intelligence Edition")

### Security
- Re-audited for v11.5: **no `shell=True` / `eval` / `exec` /
  `os.system` anywhere** in the package (58 modules); all subprocess
  usage remains argv-only with timeouts
- New validation surfaces: workflow step names must match
  `^[a-z0-9][a-z0-9_-]{0,63}$` (shell fragments/SQL/traversal
  rejected); memory/vocabulary/profile imports validated + scrubbed
  per row (malformed or credential-shaped entries skipped, never
  trusted); model artifacts bounds-checked with magic+version
  (corrupt/incompatible ⇒ plugin states, never crashes); typed text
  capped at 4,000 chars; dictation buffer ≤ 100k chars
- PREDICTION ≠ EXECUTION enforced at 5 layers (data types → assistant
  → world model → `executable` policy → v10 safety gate); destructive
  suggestions suppressed, destructive workflow steps confirmed per run
- §43 malicious-input suite: SQL injection, command substitution,
  path traversal, binary junk, 10k-char strings, XSS, credential
  shapes driven through every v11.5 parser — no exceptions, no
  execution, no secret persistence
- Privacy scrubbing is fail-closed: `password=hunter2`-shaped input
  never reaches the learned store (asserted by test)

### Fixed (bugs found during development)
- n-gram packed stream u32/u64 alignment
- dictation segment bookkeeping on "replace"
- undo/redo stack ordering (no self-push)
- mode dispatch routing for shared phrases ("mark important" now
  routes to the ACTIVE mode's controller)
- `_learn_from_report` variable shadowing

### Verification
- **786 tests passed / 0 failed / 0 skipped** (`pytest tests/ -q`,
  Python 3.12, Linux sandbox) = 630 preserved v10 tests + **156** new
  v11.5 tests (`tests/test_v115.py`, 20 sections incl. §43 security,
  §44 resource limits, §34 budgets, §46 integration simulations)
- `airmouse offline-test`: **18/18** checks with networking disabled
  at socket level; `airmouse self-test`: 15 components — 13 pass,
  1 optional (RealLocalASR), 1 hardware (Camera), 0 fail
- Performance (this environment; budgets asserted in §34 tests):
  prediction ≈ 0.004 ms (< 50 ms), emoji suggestion ≈ 0.024 ms
  (< 50 ms), memory record ≈ 0.005 ms (< 10 ms), transcription tick
  ≈ 0.001 ms (< 100 ms), fusion ≈ 0.008 ms (< 20 ms), event-bus
  publish ≈ 0.002 ms, model load ≈ 3.4 ms (< 500 ms) — all ≥ 100×
  headroom
- **Honest hardware status:** no physical webcam/mic/RF in the build
  environment — everything is SIMULATION-VERIFIED on deterministic
  simulators. Real-hardware camera/mic/gaze/RF, real-Chrome CDP, and
  live PocketSphinx/Vosk/Whisper ASR are HARDWARE-UNVERIFIED; the
  simulated streaming provider and transcript-injection path are what
  is verified. No physical verification is claimed
- The personal model ships EMPTY (fresh install ≈ 0.1 KB; a
  200-sentence training run produces a 17 KB artifact) — no
  pre-trained weights, no neural LLM, honestly documented

## v10.0.0 — UNIVERSAL OFFLINE INTERACTION ENGINE

The defining evolution: every interaction modality (voice, hand, gaze,
RF, keyboard/mouse, screen, browser) unified over one local event bus,
with deterministic offline command understanding, universal actions,
true offline operation, and honest simulation-first verification.
No LLM, no cloud AI, no network required.

### Added — Event Bus (foundations)
- `eventbus.py`: `EventBus` / `Subscriber` / `MultiSubscriber` —
  in-process pub/sub over normalized `interfaces.Event` objects;
  bounded per-subscriber queues with drop-oldest (producers never
  block), history ring buffer, publish/poll stats; deterministic
  (`now`-injectable); works with networking disabled
- `interfaces.py` extended: `Event` + `EventKind` (14 kinds),
  `VoiceMode`, `CommandNamespace` (10), `ContextState`; `Modality`
  gains RF + BROWSER (8 modalities); `IntentType` grows to 52 members,
  `ActionType` to 32

### Added — Offline Voice + Command Grammar
- `voice_commands.py`: deterministic template grammar + registry —
  **75 commands across 10 namespaces** (engine/mouse/system/window/
  application/files/text/navigation/media/browser) with `<slot>`
  entities (direction/app/name/query/url/target/number/text/what),
  verb-synonym groups, literal-specificity + namespace-priority
  resolution, ambiguity flags, calibrated confidence (exact 1.00 /
  ambiguous 0.72 / fuzzy 0.62–0.85), sensitive-command flags for the
  safety layer. Pure function, fully offline
- `offline_voice.py`: complete local voice subsystem —
  `OfflineSpeechProvider` protocol with `SimulatedSpeechProvider`,
  `PocketSphinxProvider`, `VoskProvider`, `WhisperProvider` (guarded
  imports, `detect_providers()` auto-detection); `EnergyVAD` with
  hysteresis; `WakeWordGate`; `DictationBuffer` with commit markers;
  `voice_match_to_intent` (context-aware deictic resolution);
  `OfflineVoiceEngine` with COMMAND / DICTATION / HYBRID modes

### Added — Context, Gestures, RF
- `context.py` (v10 engine): focused app/window, browser state
  (active tab/URL), gaze target with 2 s TTL, selection, recent-action
  history, `snapshot()` independent copies, deictic resolution
  ("click that" / "close it" / "open this")
- `gesture_registry.py`: formal registry for every gesture — built-in
  v5 superset + `pinch_hold` / `pinch_release` / `double_pinch` /
  `grab` / `grab_move` / `circular_cw` / `circular_ccw` / directional
  motion; user-defined `CustomGestureMapping` sequence patterns with
  JSON persistence (`~/.airmouse/gestures.json`, `AIRMOUSE_GESTURES`
  env override); deterministic sequence matcher; double-pinch synthesis
- `rf.py`: optional RF-sensing modality — `RFProvider` protocol,
  `SimulatedRFProvider` (deterministic scripted), `DummyRFProvider`
  (documents degradation), `RFBridge` → event bus + gesture registry.
  Hardware is NEVER mandatory: with no provider the bridge idles and
  the system downgrades via the combo ladder

### Added — Universal Actions, Browser, Offline
- `system_actions.py`: 16 `SYSTEM_OPS` (volume/media/lock/sleep/
  shutdown/restart/brightness/bluetooth) + 8 `FILE_OPS`; **shell-free
  argv-only subprocess**; allowlisted base roots (traversal refused);
  `sanitize_file_name`; `validate_url` (http/https/file only); mock
  executors for deterministic tests
- `actions.py` (extended): canonical §10 action vocabulary (52 intents
  → 32 actions), v10 param normalization, destructive-op confirmation
  flags, executor injection (system/file/browser alongside
  pynput/mock)
- `browser.py` (§11–§13, 3 layers): `BrowserBridge` protocol →
  `SimulatedBrowserBridge` (deterministic, per-tab history) /
  `CDPBrowserBridge` (guarded, stdlib-only urllib + minimal RFC 6455
  websocket client); `BrowserTargetMapper` + `SemanticBrowserResolver`
  (fixed template grammar only — page text is DATA, never commands);
  `BrowserActionVerifier` (before/after state diff → passed/failed/
  unknown); `BrowserController` wiring bus + context + offline gate
- `browser_bridge.py`: `BrowserBridgeServer` — **localhost-only**
  (127.0.0.1:17843) HTTP endpoint for the shipped MV3 browser
  extension (`browser_extension/`: manifest + background.js 1 s
  active-tab poll + static content.js collector; password fields
  masked; payload ≤ 256 KB; never dynamic code)
- `offline.py`: `OfflineGate` (blocks cloud_asr/cloud_tts/browser_cdp/
  software_update/telemetry_upload when engaged);
  `network_isolation()` context manager — REAL socket-level blocking
  (monkeypatches `connect`/`connect_ex`/`create_connection`, loopback
  passthrough by default, strict `block_localhost=True` mode);
  `run_offline_selftest()` — 13-check end-to-end exercise of the full
  stack with networking truly disabled
- `hands_free.py` (extended): 8 named sensor combos (`voice_only`,
  `gaze_voice`, `voice_hand`, `gaze_hand`, `voice_gaze_hand`,
  `rf_voice`, `rf_gaze`, `full_fusion`), `SensorHealth` freshness
  tracker, `effective_combo()` — deterministic largest-alive-subset
  downgrade ladder with automatic recovery

### Changed
- `safety.py`: `SENSITIVE_TYPES` extended with SHUTDOWN / RESTART /
  LOCK / SLEEP / CLOSE_TAB; FILE_OP / SYSTEM_OP intents are sensitive
  only when their params are destructive (param-level refinement)
- `agent.py`: `inject_intent()` (externally resolved intents from
  offline voice / gesture registry / RF / browser pass the SAME safety
  gate), `poll_events()` (drains optional producers), context engine
  integration, v10 executor overrides
- `__main__.py`: v10 CLI — `--offline --browser --browser-bridge
  --gesture --rf`, `--voice-mode command|dictation|hybrid`, subcommands
  `voice-status / gestures / commands / browser / offline-test /
  diagnostics`; HUD badges `V10` / `CMD` / `RF` / `BROWSER` / `VER`
- `config.py`: new `[v10]` TOML section (13 keys, privacy-safe
  defaults)
- version 9.0.0 → 10.0.0 (package, CLI, banner)

### Security
- System/file executors: no `shell=True` anywhere — every subprocess is
  an argv list; file operations rooted in an explicit allowlist of base
  directories; traversal and outside-root paths refused; filenames
  sanitized; URL schemes restricted to http/https/file
- Browser: page content treated as untrusted data (never executed);
  CDP adapter evaluates only fixed snippets built from our parameters;
  bridge server hard-bound to 127.0.0.1; oversized (>256 KB) or invalid
  payloads rejected
- Offline gate can hard-block cloud ASR/TTS/CDP/updates/telemetry;
  `offline-test` proves the stack works with sockets actually refused
- Destructive ops (shutdown/restart/lock/sleep/close_tab/destructive
  file-system ops) require explicit confirmation; e-stop, rate limiter
  and confidence gates unchanged and intact

### Fixed (bugs found by the new v10 test suite)
- `offline.py` `network_isolation()`: the refusal handler was assigned
  to the unbound `socket.socket.connect`, so it received the socket
  instance as its first argument and treated it as the address — the
  documented loopback passthrough could never fire and every connect
  (including 127.0.0.1) was refused. Fixed by unpacking the real
  `(sock, addr)` arguments; loopback stays available by default as
  documented
- `context.py` `ContextEngine.snapshot()`: returned the live internal
  state object instead of an independent copy (despite its docstring),
  so mutating a "snapshot" mutated engine state. Now returns a proper
  dataclass copy

### Verification
- **630 tests passed / 0 failed / 0 skipped / 0 xfailed**
  (`pytest tests/ -q`, Python 3.12.14, Linux sandbox) = 497 preserved
  v9 tests + 19 new browser tests + 114 new v10 tests
- Performance (this environment, budgets enforced by tests): grammar
  50 utterances < 0.5 s, 30 voice transcripts < 0.5 s, 1000 bus events
  < 0.5 s, 1000 context resolves < 0.2 s — measured at ~5 ms / ~3 ms /
  ~2 ms / ~4 ms respectively; v9 perf numbers remain valid
- `airmouse offline-test`: 13/13 checks with networking disabled at
  socket level
- **Honest hardware status:** no physical webcam/mic/RF hardware in
  the build environment — everything is SIMULATION-VERIFIED via
  deterministic simulators. Real-hardware camera/eye/mic paths,
  CDP against a real Chrome, loading the MV3 extension, and live
  PocketSphinx/Vosk/Whisper ASR are HARDWARE-UNVERIFIED (code is
  guarded and auto-detecting). No physical verification is claimed

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
