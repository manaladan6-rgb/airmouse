# Capability Matrix — AirMouse v16.5.0

**Every capability below carries EXACTLY ONE status tag**, from this
vocabulary (mission §35):

| Tag | Meaning |
|---|---|
| **REAL** | code + automated tests exist and the behaviour works as documented (live-on-camera behaviour of the same code is listed separately as PHYSICAL TEST REQUIRED) |
| **SIMULATED** | an honest, labelled simulation — it proves the software, never the hardware; always announced as such at runtime |
| **OPTIONAL** | needs an install or hardware the user provides; availability is detected and reported honestly |
| **PHYSICAL TEST REQUIRED** | nothing has touched real hardware; validation is handed to the user via `WINDOWS_REAL_WORLD_TEST.md` + `airmouse test --guided` and can never auto-pass |
| **NOT AVAILABLE** | not implemented or not wired — never described as working |

**Measurement caveat (read first):** every measured output below was
captured on a **Linux headless sandbox** (no display, no webcam, no
microphone, no Chrome) by running the real commands on the real
v16.5.0 release-candidate tree. It is NOT a Windows measurement.
Physical hardware behaviour (webcam frames, microphone audio, real
hand tracking, gaze, browser automation against live Chrome, the
first-run tour and academy lessons with real sensors) was NOT TESTED
and cannot be auto-verified.

Environment measured: Linux x86_64, Python 3.12.14, numpy 2.2.6,
opencv-python 5.0.0, mediapipe 0.10.35, pynput 1.8.2 (present, no
display), SpeechRecognition 3.17.0 (sphinx backend importable; no ASR
engine package installed), pytest suite **1889 passed / 2 skipped /
0 failed** (skips are honest: no Chrome binary, no tesseract). Storage
isolated via `AIRMOUSE_HOME=/tmp/am-doc165`.

Reproduce with:

```
cd airmouse_pkg
AIRMOUSE_HOME=/tmp/am-doc165 python3 -m airmouse doctor --json
AIRMOUSE_HOME=/tmp/am-doc165 python3 -m airmouse verify
AIRMOUSE_HOME=/tmp/am-doc165 python3 -m airmouse teach
AIRMOUSE_HOME=/tmp/am-doc165 python3 -m airmouse voice-status
AIRMOUSE_HOME=/tmp/am-doc165 python3 -m airmouse help-me "how do I scroll?"
AIRMOUSE_HOME=/tmp/am-doc165 python3 -m airmouse academy
AIRMOUSE_HOME=/tmp/am-doc165 python3 -m airmouse profile list
```

---

## 1. Capability table

| # | Capability | Status | Evidence / how to validate |
|---|---|---|---|
| 1 | CLI parser: 34 subcommands, 56 visible flags + 1 hidden CI flag, `command_arg` semantics per command | **REAL** | regenerated `docs/CLI_REFERENCE.md`; `airmouse --help`; every exit code measured |
| 2 | Config system (TOML round-trip; v16 keys `two_hand`, `gesture_min_confidence_*`, `gesture_allow_destructive=false`, `gesture_sequences`, `selftune_apply`) | **REAL** | `config.py` + profile round-trip tests; `airmouse profile accessibility` wrote a real config.toml in the sandbox |
| 3 | ONE storage home (`paths.py`: `$AIRMOUSE_HOME` else `~/.airmouse`, resolved fresh per call, no split-brain) | **REAL** | `paths.py`; manifest + memory CLI all honor it (measured) |
| 4 | Crash-safe persistence (5 stores; atomic writes, schema versioning, checksum quarantine + recovery) | **REAL** | `persistence.py` + test suite |
| 5 | Memory lifecycle `status` / `export` / `reset` / `delete` incl. real learning artifacts + `deletion_verifies()` | **REAL** | `persistence.py`, `__main__.py` memory branch; artifact coverage: intelligence/*, calibration, gestures, macros, lecture.md |
| 6 | Privacy report + **storage manifest (24 declared artifacts)** + PERSONALIZATION summary | **REAL** | `privacy.py` `PRIVACY_MANIFEST` = 24 entries (counted live: was 20, +`academy_progress`, `onboarding_state`, `personal_profile`, `transcript_sessions`); `airmouse privacy` prints each with purpose + exists flag and ends `Nothing is uploaded.` |
| 7 | Doctor — 41 components, 12 sections, plain fixes, `--json` | **REAL** | measured on this tree: READY 32 / OPTIONAL 6 / HARDWARE 2 / WARNING 1 / FAILED 0 → `[PARTIAL — review WARNING items]`; the 1 WARNING is the honest `Hand tracking model — not downloaded yet` row (this reset sandbox never fetched the 8 MB model — the exact documented behaviour since v15.1.0; with the model present the verdict is `[READY FOR TESTING]`); exit 0/1/2 |
| 8 | Verify — 12 automated checks + 5 physical ACTION_REQUIRED rows | **REAL** | measured run: 12/12 PASS (v16.5 adds Teacher + Temporal; Temporal ≈293 µs/frame), physical never auto-pass |
| 9 | Setup wizard — 11 steps, consent-gated install, marker file | **REAL** | `setup_wizard.py` + tests; measured non-interactive run |
| 10 | Guided test laboratory (12 tests; `[SIMULATION]` labels; physical never auto-pass) | **REAL** | measured: 7/7 simulation PASS, 0/5 hardware, exit 0 |
| 11 | Self-test — 15 components, honest PASS/OPTIONAL/HARDWARE | **REAL** | measured: 13 pass, 1 optional (RealLocalASR), 1 hardware (Camera), 0 fail |
| 12 | First-run menu — exactly 10 items `[1] Setup [2] Doctor [3] Guided Test [4] Start AirMouse [5] Voice [6] Intelligence [7] Agent [8] Offline Test [9] Safety [0] Help`, TTY-only | **REAL** | `cli_menu.py:41-52` `MENU_ITEMS` (fixed in this release; README now matches) |
| 13 | Static hand-pose recognition — 17 poses (pointing…five), angle hysteresis, per-finger confidence | **REAL** | `gestures.py` `recognize_gesture`; deterministic geometry tests on synthetic landmarks |
| 14 | Motion detectors — 4 swipes (L/R/U/D, dominant-axis exclusivity), 2 circles (CW/CCW), push/pull (depth), shake, wave | **REAL** | `gestures.py` detectors + tests; wired in the live loop (swipes→back/forward/scroll, circles/push/pull→zoom, shake→cancel, wave→attention cue) |
| 15 | Pinch lifecycle events — pinch_hold / pinch_release / double_pinch emitted by the state machine | **REAL** | state machine + live drain in `__main__.py` (`gsm.poll_pinch_events()`); double_pinch → spine `double_click` |
| 16 | Execution spine — estop > confidence > risk class (SAFE/CAUTION/DESTRUCTIVE) > policy > rate limit > dispatch, bounded history | **REAL** | `gesture_spine.py` `GestureActionRouter`; every legacy dispatch migrated |
| 17 | Destructive policy — `close_window` (Alt+F4) and `macro_play` refused by default (`gesture_allow_destructive=false`); DESTRUCTIVE confidence floor 1.1 = unreachable | **REAL** | spine tests + Gesture Lab demo (measured refusal text `destructive_action_blocked_by_policy`) |
| 18 | Confidence gating with config floors (SAFE 0.45 / CAUTION 0.60) | **REAL** | spine tests; lab honors the same floors |
| 19 | E-stop keybindings — `[ESC]` trips spine + agent estop, `[x]` resets | **REAL** | `__main__.py` key handler (`key == 27` → `spine.trip_estop`, `ord('x')` → `reset_estop`) |
| 20 | Gesture→action wiring: clicks, drag, scroll, volume/brightness, window ops through the spine | **REAL** | dispatch code + dry-run stub tests; **live OS effects on a real desktop are row 46** |
| 21 | Custom sequence registry fed by real hands (`--gesture` + `gesture_sequences=true`; SAFE intents through the spine) | **REAL** | `__main__.py` feed path (`audit #9` fix); `airmouse gestures` lists mappings |
| 22 | Default OS action for thumbs_down / four / five | **NOT AVAILABLE** | recognized + HUD-visible only (by policy); user-mappable via the registry |
| 23 | Two-hand recognizer — HOLD / ZOOM / ROTATE / DRAG (pure-geometry state machine, hysteresis + grace) | **REAL** | `two_hand.py` + tracker `max_hands=2` + synthetic-hand tests |
| 24 | Two-hand ZOOM → real ctrl+wheel ticks | **REAL** | `__main__.py` zoom path (`zoom_scroll(_ticks)` through the continuous gate) |
| 25 | Two-hand ROTATE / DRAG → OS actions | **NOT AVAILABLE** | detected (report dict carries them) but no OS action is wired — honestly stated |
| 26 | Single-hand actions freeze while two-hand is engaged (one owner) | **REAL** | ownership gate in the live loop |
| 27 | Gesture Academy CLI — 11 lessons (7 core + 4 advanced teach-only), headless full plan, resumable progress, unknown-id exit 1 | **REAL** | `academy.py`; measured headless run prints `PHYSICAL PRACTICE REQUIRED` per core lesson and never auto-passes |
| 28 | Academy LIVE lessons (camera + display, hold-to-pass bar, `[SPACE]` skip = no credit) | **PHYSICAL TEST REQUIRED** | needs camera + hand + display; nothing has touched real hardware |
| 29 | Gesture Lab — dry-run observatory (REAL `GestureActionRouter` wired to stubs; destructive refusal always demonstrable; ~5 Hz readout; headless explanation) | **REAL** | `gesture_lab.py`; measured headless output shows executed pinch + blocked close_window |
| 30 | Gesture Lab live camera readout | **PHYSICAL TEST REQUIRED** | needs a real webcam + display |
| 31 | Interaction profiles — 8 presets, 12-key whitelist, NaN/out-of-range/unknown refused fail-closed | **REAL** | `gesture_profiles.py` + 38-test module; measured `profile accessibility` → 12 settings written |
| 32 | Cursor pipelines (direct / ironman / trackpad; One Euro + Kalman; adaptive calibration) | **REAL** | code + filter/calibration tests; live behaviour = row 46 |
| 33 | 75-command offline grammar across 10 namespaces | **REAL** | counted live from `airmouse commands`: 75 commands / 10 namespaces |
| 34 | Dictation formatting engine (spoken punctuation + edit commands, text-side) | **REAL** | `dictation_text.py` + tests |
| 35 | Streaming transcription engine (text-side partials/finals) | **REAL** | `transcription` paths + tests |
| 36 | Simulated ASR provider (deterministic, offline) | **SIMULATED** | `voice-status` reports it as such; never passes itself off as real ASR |
| 37 | Local ASR engines (pocketsphinx / vosk / whisper) | **OPTIONAL** | adapters exist, engines not bundled; `voice-status` measured: all False in sandbox |
| 38 | Live microphone voice control (`--voice`, `--voice-mode`) | **PHYSICAL TEST REQUIRED** | needs mic + a real audio stack; never verified on hardware |
| 39 | TTS spoken confirmations (pyttsx3) | **OPTIONAL** | extra; no audio device in sandbox |
| 40 | Gaze pipeline + calibration math (`--gaze`, `--gaze-calibrate`) | **REAL** | code + tests (filters, affine calibration, dwell logic) |
| 41 | Live webcam gaze control / gaze clicks | **PHYSICAL TEST REQUIRED** | needs a face, a camera and a display |
| 42 | Fusion / hands-free / assist modes (v9) live | **PHYSICAL TEST REQUIRED** | wiring tested at startup (test_startup.py drives real main()); real sensor behaviour untested |
| 43 | Simulated browser bridge (deterministic, offline) | **SIMULATED** | `SimulatedBrowserBridge`; verify labels it |
| 44 | CDP bridge + `launch_browser` (Chrome/Chromium/Edge discovery, isolated user-data-dir, loopback-only ws pinning, readiness probe, port reuse; `--launch-browser`/`--browser-port`) | **REAL** | `browser.py` + endgame tests; measured honest `browser_not_found` / `available: False` in sandbox |
| 45 | Browser control against LIVE Chrome/Edge (real pages, real clicks) | **PHYSICAL TEST REQUIRED** | no browser in sandbox; also true for the MV3 extension bridge (127.0.0.1:17843) once loaded in a real browser |
| 46 | Live OS control on a real desktop (cursor moves, real clicks/keys, two-hand zoom on screen, macro replay) | **PHYSICAL TEST REQUIRED** | pynput present but no display in sandbox; the single biggest untested area |
| 47 | AIP 1.0 protocol — 18 message types, 12 strict fail-closed schemas, 256 KB caps, same-major negotiation | **REAL** | `aip.py` + validator tests |
| 48 | AIP stdio wire server — module entry `python -m airmouse.aip_stdio` (JSON-lines, 256 KiB line cap, error lines for junk, timeout guard, flushed pipe-safe replies) | **REAL** | measured end-to-end: agent-core client connect/capabilities/discover round-trip → protocol 1.0 |
| 49 | `airmouse --aip-stdio` CLI wrapper (simulated endpoint by default, honestly labelled; `--aip-real` builds the real ActionEngine, falls back honestly) | **REAL** | measured; caveat documented: one banner line precedes the JSON loop → strict first-line-JSON clients must use the module entry |
| 50 | EXECUTE over the wire WITHOUT `--aip-real` | **SIMULATED** | results labelled `simulated: true`; measured `permission_denied` fail-closed with no grants |
| 51 | EXECUTE over the wire WITH `--aip-real` (permission-gated `ActionEngine`, ALLOW_ONCE single-use, budgets enforced) | **REAL** | gating + budgets + engine payload tests; real OS effects = row 46 |
| 52 | Permission engine (E-STOP > HUMAN OVERRIDE > SAFETY POLICY > PERMISSION > AGENT > PREDICTION; ASK fails closed; ALLOW_ONCE = exactly one use) | **REAL** | `permissions.py` + red-team tests; measured `permission_denied` over the wire |
| 53 | Multi-agent registry (leases, conflict resolution, `emergency_stop_all`) | **REAL** | `agents.py` + lease/e-stop tests (in-process) |
| 54 | SDKs: Python `agent_sdk`, dependency-free `agent-sdk-js`, stdlib-only `agent-core` | **REAL** | imports + round-trip tests; agent-core ↔ aip_stdio measured end-to-end |
| 55 | Personal Interaction Twin (14 fact categories, learn/forget/correct/export) | **REAL** | `intelligence/twin` + tests |
| 56 | Temporal world model (snapshots, transitions, mismatch detection) | **REAL** | `world_model_temporal.py` + tests |
| 57 | Goals / tasks / skills (deterministic parsing, approval gates, versioned skills) | **REAL** | `goals.py`, `tasks.py`, `skills.py` + tests |
| 58 | Recovery engine (7 strategies, 14 diagnoses, bounded rounds) + universal target resolver (7 providers) | **REAL** | recovery2 + resolver tests (failure drills are injected, i.e. simulated scenarios over real code) |
| 59 | Personalization loop — `observe_gesture` fed from the live loop (bounded, learning-gated inside the plugin) | **REAL** | `__main__.py` observe call; plugin gates on `learning_active` |
| 60 | Automatic self-tuning application (`selftune_apply`) | **NOT AVAILABLE** | config key reserved (default false); no shipped code path applies tuner proposals automatically — application happens only via explicit selftune-bundle import |
| 61 | Deterministic simulator + 12-class failure injection | **SIMULATED** | `simulator.py`, `failure_injection.py` — honest by contract |
| 62 | DO IT WITH ME (goal → plan → approve/edit/pause/stop) | **REAL** | `ditm.py` + tests |
| 63 | OfflineGate + `offline-test` (18 checks, networking really blocked) | **REAL** | measured 18/18 in sandbox |
| 64 | RF sensing hardware | **NOT AVAILABLE** | abstraction + simulated provider only; no hardware claimed (`--rf` idles honestly) |
| 65 | HUD overlays / on-screen session display | **PHYSICAL TEST REQUIRED** | needs display + camera; headless CI asserts wiring only |
| 66 | Macro record / replay live (`--record` / `--play`) | **PHYSICAL TEST REQUIRED** | recorder code + refusal policy tested (row 17); live replay untested |
| 67 | PyInstaller Windows bundles (`build.py --windows`) | **PHYSICAL TEST REQUIRED** | build path exists, model download step documented; no bundle executed on real Windows |
| 68 | Settings GUI (`--settings`, tkinter) + autostart management (`--autostart on/off`) | **OPTIONAL** | needs a desktop session; Windows autostart path untested on real Windows |
| 69 | Cloud / telemetry integrations | **NOT AVAILABLE** | none exist by design; OfflineGate structurally blocks them; nothing to test |
| 70 | v15 one-choice onboarding interview at startup | **NOT AVAILABLE** | `onboarding.py` exists but has zero runtime imports — the v16.5 TEACHER (`teach` + the TTY-gated first-run offer) is the shipped onboarding; the old module stays unwired |
| 71 | Teacher CLI — `teach [all\|voice\|gaze\|gesture\|fusion\|resume]` + `learn`: per-track plans (5 tracks / 11 lessons), self-diagnostic hardware panel, adaptive practice (strong skill skipped, weak skill gentle repeat), physical lessons NEVER auto-passed | **REAL** | `teacher.py`; measured: `teach` / `teach voice` / `teach gesture` / `teach resume` all print the honest plan + progress panel and exit 0; `teach nope` → `teach track must be: all \| voice \| gaze \| gesture \| fusion \| resume`, exit 1; headless marks nothing complete |
| 72 | Teacher LIVE sessions (interactive tour, camera/mic practice, fusion challenges with real sensors) | **PHYSICAL TEST REQUIRED** | needs a real TTY + webcam + microphone; headless sandbox only ever sees the plan; validation via WINDOWS_REAL_WORLD_TEST.md step 16a |
| 73 | First-run auto-teaching offer (plain `airmouse` on a TTY: NEW → 3–5 min tour offer, IN_PROGRESS → `Continue your training?`; decline/EOF/non-TTY always proceeds; `config.teach_auto`) | **REAL** | `__main__.py` hook (TTY-gated via `stdin.isatty`) + `teacher.maybe_prompt_teach`; startup-probe tests drive real main() headless and are never blocked; decline paths tested |
| 74 | Onboarding persistence + resume — `<home>/profile/onboarding.json` (schema v1: phase, tracks, sessions), ladder NEW → IN_PROGRESS → VOICE_COMPLETE → GAZE_COMPLETE → GESTURE_COMPLETE → FUSION_COMPLETE → COMPLETE | **REAL** | measured: file created with `"phase": "IN_PROGRESS"` after one headless `teach`; corrupted file measured live → phase NEW + `corrupted_last_load: True` |
| 75 | Voice Academy — 4 levels (l1 basic commands, l2 natural language via the REAL grammar matcher, l3 dictation with spoken punctuation verified by the REAL `VoiceTypingEngine` + clean retries, l4 personal voice learning via the REAL local-only `VoiceProfile` alias learning) | **REAL** | `voice_academy.py` (`VOICE_LESSONS`, `match_phrase`, `format_dictation`, `apply_spoken_punctuation`, `resolve_voice`) + module tests; text-side mechanics only |
| 76 | Voice Academy MICROPHONE practice (speak the phrases; L1 pass requires hearing you) | **PHYSICAL TEST REQUIRED** | needs a real microphone; sandbox has none (voice lessons can alternatively be completed by typing the phrase in an interactive session) |
| 77 | Built-in voice command grammar — 30-phrase deterministic offline grammar (counted live from `voice_control.COMMANDS`), fully offline, wake-word respected | **REAL** | `voice determinism` verify check + offline tests; `airmouse commands` prints the 75-command registry / 10 namespaces it feeds |
| 78 | Pluggable local ASR engines — Vosk / Whisper (faster_whisper) / PocketSphinx, guarded imports, auto-detected when installed, preference vosk > whisper > pocketsphinx | **OPTIONAL** | engines NOT bundled; measured: no engine package installed → `voice-status` panel shows all `○` + `Full speech recognition is not installed.`; detection itself is tested |
| 79 | ASR auto-install / network fetch of engines or models by AirMouse | **NOT AVAILABLE** | `voice_stack.install_guidance()` is TEXT ONLY (pip commands shown, never run); no subprocess, no network call in the install path |
| 80 | `airmouse transcribe` — live transcription session (REPL pause/resume/save [json]/clear/status/stop/quit; segments with timestamp + confidence; bounded history 500; explicit save ONLY to `<home>/transcripts/`; text only, never audio; EOF ends cleanly) | **REAL** | `transcribe_session.py` + `offline_voice` streaming engine; measured full REPL run: segment `[12:24:40] 95% "Hello world, this is a test"`, `history: 2/500 segments`, `save` → `<home>/transcripts/transcript-<ts>.txt` / `.json`; no file written without save |
| 81 | Simulated streaming ASR provider (deterministic offline stand-in when no engine is installed) | **SIMULATED** | measured banner on every session: `provider: simulated_stream` + `⚠ SIMULATED provider — install a local ASR engine for real transcription`; never passed off as real recognition |
| 82 | Temporal recognizer — `temporal.py`: `TrajectoryBuffer` features (velocity/acceleration/direction/handedness), `PinchLifecycle` START/HOLD/MOVE/RELEASE with hysteresis + debounce + false-positive suppression + tracking grace, `TemporalRecognizer` sequences + compositions; ≈293 µs/frame recognize+features | **REAL** | `temporal.py` + module tests; measured by the `airmouse verify` Temporal check (293.4 µs/frame, direction right, lifecycle engaged) |
| 83 | Temporal observer in the LIVE app — HUD badges `TMP:` (pinch lifecycle / proposal) and `SENSOR:` (health degraded/poor); feeds the local profile learning loop | **REAL** | `__main__.py` observer (PinchLifecycle + TrajectoryBuffer + SensorHealthScore fed every frame) + badge renderer + startup-probe tests; on-screen look = row 65 |
| 84 | Temporal OS dispatch for new compositions (a proposal executing a NEW OS action) | **NOT AVAILABLE** | the observer PROPOSES ONLY — `gesture_spine` remains the sole dispatcher; no parallel execution path exists (enforced by source-scan tests); documented in-app and in `help-me` |
| 85 | `CompositionResolver` — merges voice + gaze + gesture into ONE intent proposal | **REAL** | `temporal.py` + tests; proposals only (row 84 is the honest boundary) |
| 86 | Robustness toolkit — `Hysteresis`, `Debouncer`, `FalsePositiveSuppressor`, `TrackingRecovery`, `CameraWatchdog`, `SensorHealthScore` | **REAL** | `temporal.py` + module tests; `SensorHealthScore` drives the live `SENSOR:` HUD badge |
| 87 | Gaze Academy — 5 lessons (l1_acquire, l2_fixation, l3_dwell, l4_blink, l5_eye_assist) with REAL metric functions (acquisition, jitter, stability, fixation hold, dwell verification, blink events/gaze-lock, drift); headless → honest plan, nothing passed | **REAL** | `gaze_academy.py` (68 tests) + teacher integration; measured headless: plan + `PHYSICAL PRACTICE REQUIRED`, nothing marked complete |
| 88 | Gaze Academy LIVE lessons (camera + your eyes; passes only from real gaze samples) | **PHYSICAL TEST REQUIRED** | needs webcam + face + calibrated gaze; simulated dry-runs are always labelled SIMULATED and never pass as real |
| 89 | Gaze personalization — GazeLearner at `<home>/profile/gaze.json`: bounded clamps (dwell [0.3, 2.0] s etc.), EMA learning gated on VERIFIED observations, simulated observations flagged and never used for suggestions, suggestions are proposals (never auto-applied), local-only | **REAL** | `gaze_academy.py` + tests (roundtrip, clamps, corruption recovery, suggestion gating) |
| 90 | Personal Interaction Profile — `<home>/profile/{interaction,voice,gestures,preferences}.json`: bounded, content-free (counters/parameters only), atomic writes, corruption fail-closed, reset with backup | **REAL** | `profile_store.py` + 63 tests; measured `airmouse privacy` lists them under the manifested `profile/` dir |
| 91 | LearningLoop — 11 bounded stages, ring buffer, proposals that NEVER execute, single explicit approve path, `adapt()` converts approved proposals into profile observations (PREDICTION ≠ EXECUTION) | **REAL** | `profile_store.py` + tests incl. approval gate; the verify Teacher check asserts the approval gate end-to-end |
| 92 | `airmouse help-me` — answers from REAL capability data (22-row gesture map aligned with `gestures.py` + spine risk classes, the 30-phrase grammar, honest destructive + two-hand notes, gate-level debug answer) | **REAL** | `help_registry.py` + tests; measured: overview panel, `"how do I scroll?"` → 3 real scroll paths, `"why didn't that work?"` → e-stop → confidence → policy → rate-limit chain |
| 93 | Zero-learning-curve READY panel — post-startup `AIRMouse READY` (Hands/Voice/Gaze/Learning ✓/○ + `say "help" anytime` + teach reminder); display-only, `config.ready_panel` default true | **REAL** | `__main__.py` panel + config keys (both v16.5 keys measured in `config.py` defaults); on-screen HUD look = row 65 |
| 94 | New config keys `teach_auto` / `ready_panel` (both default true, written to config.toml `[v10]`) | **REAL** | `config.py` defaults + `save_defaults` lines; round-trip tests |

---

## 2. Measured output — `airmouse doctor` (plain, this tree)

```
AIRMouse Doctor
===========================

READY:       32
OPTIONAL:    6
HARDWARE:    2
WARNING:     1
FAILED:      0

Overall:
[PARTIAL — review WARNING items]
```

41 components across 12 sections. Non-READY rows are honest headless
truth: pynput UNAVAILABLE (no display) ×2, voice extras NOT_INSTALLED
×2, absent Chrome/Edge, mock executors, webcam + microphone HARDWARE.
The single WARNING is `Hand tracking model — not downloaded yet`
(this sandbox lost the previously-downloaded model in the environment
reset; v16.0.0's measurement on the same machine with the model
present was READY 32 / OPTIONAL 7 / WARNING 0 → `[READY FOR
TESTING]` — the OPTIONAL delta is the Speech row, which now reports
`Transcription providers READY — local ASR: pocketsphinx` because the
SpeechRecognition sphinx backend wrapper is importable; the v16.5
`voice-status` engine-level panel still reports the engines honestly,
see row 78).

## 3. Measured output — v16 gesture surface (this tree)

```
$ airmouse profile list
  gesture profiles: accessibility, creative, default, developer, gaming, hands_free, media, presentation
  apply one:  airmouse profile <name>

$ airmouse profile accessibility
  profile 'accessibility' applied: 12 settings -> /tmp/am-2g/config.toml

$ airmouse profile nope                       ; echo rc=$?
  unknown profile 'nope' — available profiles: accessibility, creative, default, developer, gaming, hands_free, media, presentation
rc=1

$ airmouse academy bogus-lesson               ; echo rc=$?
  unknown academy lesson 'bogus-lesson' — valid lesson ids: move, click, double_click, right_click, drag, scroll, zoom, gaze, voice, two_hand, sequences
rc=1

$ airmouse gesture-lab --no-cam   (excerpt)
HAND DETECTED : yes
GESTURE       : ok
CONFIDENCE    : 91%
MODE          : classic
TWO-HAND      : off
LAST ACTION   : close_window (attempted)
RESULT        : blocked: destructive_action_blocked_by_policy (a gesture must never close windows)

$ python -m airmouse.aip_stdio   (EXECUTE with no grants)
{"type": "error", "payload": {"code": "permission_denied",
 "message": "permission 'mouse.click' decision 'ask': no rule; default ASK fails closed", ...}}
```

## 4. Measured output — `airmouse verify` (this tree)

12/12 automated checks PASS — Core import; voice determinism,
intelligence roundtrip, safety gates + e-stop + hierarchy, offline
18/18, simulated browser, permission deny-by-default, lease conflict,
AIP validator, packaging match (16.5.0), **Teacher** (onboarding
ladder + honest grading + help + profile store + learning-loop
approval gate + transcribe session), **Temporal** (recognize+features
293.4 µs/frame, direction right, lifecycle engaged); 5 physical rows
ACTION_REQUIRED (webcam, microphone, hand tracking, gaze, real
browser). Exit 0.

Measured v16.5 headless runs (excerpts):

```
$ airmouse teach                (headless; rc 0)
  Headless run: nothing was marked complete — physical lessons are NEVER auto-passed.
  PHYSICAL PRACTICE REQUIRED — needs camera/microphone; never auto-passed.

$ airmouse teach nope           (rc 1)
  teach track must be: all | voice | gaze | gesture | fusion | resume

$ airmouse transcribe < /dev/null   (rc 0)
  provider: simulated_stream
  ⚠ SIMULATED provider — install a local ASR engine for real transcription (see: airmouse voice-status)
  session ended cleanly (no audio was ever stored)

$ airmouse help-me "how do I scroll?"   (rc 0)
Scrolling, three ways:
  1. PINCH + HOLD, then move your hand up/down (vertical pinch = scroll).
  2. SWIPE UP / SWIPE DOWN — a fast vertical hand sweep.
  3. Say "scroll up" or "scroll down" (voice).
```

## 5. Measured performance (same sandbox, unchanged budgets)

| Command | Wall time (measured, v16.5.0 tree) | Pinned budget (`tests/test_release_perf.py`) |
|---|---:|---:|
| `python -m airmouse --version` | ≈ 0.8 s | 6.0 s |
| `python -m airmouse doctor` | ≈ 1.0 s | 12.0 s |
| `python -m airmouse verify` | ≈ 1.5 s | 8.0 s |
| `python -m airmouse test` | ≈ 0.9 s | 8.0 s |

Temporal recognizer (measured inside `airmouse verify`):
recognize + features ≈ 293 µs/frame (budget: trivial at any realistic
frame rate; the check itself asserts the measurement happens and the
lifecycle engages).

## 6. Status tally (this document, rows 1–94)

| Status | Count |
|---|---:|
| REAL | 64 |
| SIMULATED | 5 |
| OPTIONAL | 4 |
| PHYSICAL TEST REQUIRED | 13 |
| NOT AVAILABLE | 8 |
| **Total** | **94** |

- PHYSICAL TEST REQUIRED rows: 28 (academy live lessons), 30 (lab
  live readout), 38 (mic voice control), 41 (live gaze), 42 (fusion
  modes live), 45 (browser against live Chrome/Edge), 46 (live OS
  control on a real desktop), 65 (HUD overlays), 66 (macro
  record/replay live), 67 (PyInstaller Windows bundles), 72 (teacher
  live sessions with sensors), 76 (Voice Academy microphone
  practice), 88 (Gaze Academy live lessons).
- NOT AVAILABLE rows: 22 (default actions for thumbs_down/four/five),
  25 (two-hand rotate/drag OS actions), 60 (automatic
  `selftune_apply`), 64 (RF hardware), 69 (cloud/telemetry
  integrations), 70 (v15 one-choice onboarding interview), 79 (ASR
  auto-install — guidance is text only), 84 (temporal OS dispatch for
  new compositions — proposals only).
- OPTIONAL rows: 37 (local ASR engines), 39 (TTS), 68 (settings GUI +
  autostart management), 78 (pluggable local ASR engine
  auto-detection).
- SIMULATED rows: 36 (simulated ASR provider), 43 (simulated browser
  bridge), 50 (AIP EXECUTE without `--aip-real`), 61 (deterministic
  simulator + failure injection), 81 (simulated streaming ASR
  provider in `transcribe`).

## 7. Honesty summary

- AUTOMATED VERIFICATION PASS: **1889 tests** (2 honest skips),
  verify 12/12, offline 18/18, doctor 41 components.
- SIMULATION PASS: guided lab 7/7 simulation tests; simulated browser
  bridge; simulated ASR provider; simulated streaming transcription
  provider (banner-labelled every session); deterministic simulator +
  failure injection; AIP EXECUTE without `--aip-real` (labelled
  `simulated`).
- PHYSICAL HARDWARE NOT TESTED: webcam, microphone, real hand
  tracking (single- and two-hand), real gaze, real browser CDP,
  real OS input automation, live HUD, macro replay, PyInstaller
  Windows bundles, the first-run tour and academy lessons with real
  sensors, live ASR engines (vosk/whisper) — ACTION_REQUIRED by
  design.
- NOT AVAILABLE (never described as working): two-hand rotate/drag OS
  actions, temporal OS dispatch for new compositions (proposals
  only), default actions for thumbs_down/four/five, automatic
  `selftune_apply`, ASR auto-install, RF hardware, cloud/telemetry
  integrations, the v15 one-choice onboarding interview.
