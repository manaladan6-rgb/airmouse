# AirMouse v16.5.0 — CLI Reference (extended in v19)

Regenerated **completely** from the live argument parser
(`airmouse_pkg/airmouse/__main__.py`, the `build_parser`/`main()` section)
and cross-checked by running every command in a headless sandbox. Every
flag below is registered exactly as listed — nothing is invented. The
executable truth is always `airmouse --help`; this document adds the
exit codes and `command_arg` semantics the help text does not show.

Measured parser facts (this tree): **34 subcommand choices**, **56
visible flags + 1 hidden CI flag** (57 `add_argument` flags + 2
positionals = 59 registrations — v16.5 added four subcommands, no new
flags), help banner currently printed as
`AirMouse v16.5.0 — Adaptive Human-Computer Intelligence Edition` (the
version string in `__init__.py` / `pyproject.toml` is bumped to 16.5.0
by step 1 of the release process — every capability documented here is
independent of that string).

**v19 update (measured on the v19 tree):** the parser now registers
**41 subcommand choices** and **66 visible flags + 1 hidden CI flag**
(67 `add_argument` flags + 2 positionals = 69 registrations). v19 adds
five subcommands (`wizard`, `reset`, `repair`, `uninstall`, `update`)
and two flags (`--advanced`, `--aip`). Rows 37–41 below are the v19
additions; every earlier row is preserved as regenerated.

```bash
airmouse [FLAGS] [SUBCOMMAND] [COMMAND_ARG]
python -m airmouse [FLAGS] [SUBCOMMAND] [COMMAND_ARG]   # equivalent, no install needed
```

---

## Positional arguments

### 1. `command` — info/diagnostic subcommand (prints and exits)

Exactly these 34 choices (parser `choices=`, in parser order):

| # | Command | One-line effect |
|---|---|---|
| 1 | `voice-status` | which offline ASR providers are actually installed |
| 2 | `gestures` | built-in + custom gesture mappings |
| 3 | `commands` | the 75-command v10 grammar by namespace |
| 4 | `browser` | CDP browser bridge status on :9222 |
| 5 | `offline-test` | 18-check network-isolated full-stack selftest |
| 6 | `diagnostics` | version + ASR providers + offline selftest + optional deps |
| 7 | `intelligence` | adaptive-intelligence plugin status |
| 8 | `memory` | memory lifecycle (`command_arg`: `status\|export\|reset\|delete`) or learned patterns |
| 9 | `vocabulary` | learned personal vocabulary + corrections |
| 10 | `workflows` | discovered/approved workflows |
| 11 | `self-test` | 15-component honest report (PASS / OPTIONAL / HARDWARE) |
| 12 | `status` | platform banner: protocol, hierarchy, license tier |
| 13 | `capabilities` | v19: user-facing capability registry with evidence labels (`--json` machine view; `--aip` preserves the AIP DISCOVER agent view) |
| 14 | `observe` | one observation from the deterministic simulator |
| 15 | `world` | temporal world model explain + predict_state |
| 16 | `twin` | Personal Interaction Twin status (facts/observations) |
| 17 | `skills` | personal skill library listing |
| 18 | `agents` | multi-agent registry listing |
| 19 | `permissions` | the §15 permission key set + decision vocabulary |
| 20 | `tasks` | task engine listing (destructive steps need approval) |
| 21 | `protocol` | AIP version, concepts and schema names |
| 22 | `benchmark` | local performance spot-check (twin/world/tasks timings) |
| 23 | `setup` | guided setup wizard (11 steps, consent-gated) |
| 24 | `doctor` | 12-section / 41-component health report; v19 adds the human interaction panel + `--advanced` (verbose alias) |
| 25 | `test` | guided test laboratory (`--guided` = interactive) |
| 26 | `verify` | 12 automated checks + 5 physical ACTION_REQUIRED items |
| 27 | `privacy` | local-first privacy report (+ storage manifest + PERSONALIZATION summary) |
| 28 | `academy` | Gesture Academy — 11-lesson curriculum |
| 29 | `gesture-lab` | Gesture Lab — live observatory (dry-run spine) |
| 30 | `profile` | gesture interaction profiles (8 presets) |
| 31 | `teach` | interactive teacher — plan + tour per track (v16.5) |
| 32 | `learn` | all academies at once: voice · gaze · gestures · fusion (v16.5) |
| 33 | `transcribe` | live text transcription session (v16.5; REPL) |
| 34 | `voice-setup` | zero-compiler local ASR bootstrap (v16.5.3) |
| 35 | `voice-benchmark` | deterministic local ASR benchmark (v16.5.3) |
| 36 | `help-me` | capability answers from real data; ask free-text questions (v16.5) |
| 37 | `wizard` | 60-second interaction wizard — six probes + honest verdict (v19) |
| 38 | `reset` | lifecycle reset — `command_arg`: `config\|models\|all` (also `--config\|--models\|--all`); asks first, explains what (v19) |
| 39 | `repair` | safe local fixes: home structure, config (backup → repair → validate), model re-verify (corrupted → auto-removed), setup marker; installs nothing (v19) |
| 40 | `uninstall` | remove the AirMouse home after a content summary + explicit confirmation; prints the exact pip command for the package (v19) |
| 41 | `update` | check PyPI for a newer release — explicit-only (the invocation IS the consent; `--check` is the conventional spelling), never downloads or installs (v19) |

### 2. `command_arg` — optional argument, meaning depends on `command`

| Command | `command_arg` semantics | Other values |
|---|---|---|
| `memory` | `status` / `export` / `reset` / `delete` (exact strings; case-insensitive) | any other value (or omitted) prints the learned interaction-pattern list instead |
| `academy` | a lesson id (`move`, `click`, `double_click`, `right_click`, `drag`, `scroll`, `zoom`, `gaze`, `voice`, `two_hand`, `sequences`) or `all` (also omitted = all) | unknown id → prints the valid ids, **exit 1** |
| `gesture-lab` | an integer number of seconds (e.g. `20`) bounds the session | omitted / non-numeric → runs until Ctrl-C |
| `profile` | a profile name (`accessibility`, `creative`, `default`, `developer`, `gaming`, `hands_free`, `media`, `presentation`) | omitted or `list` lists profiles; unknown name → **exit 1** |
| `doctor` | `verbose`, `-v` or `--verbose` also trigger verbose output | ignored otherwise |
| `test` | `guided` or `--guided` also triggers the interactive lab | ignored otherwise |
| `teach` | a track: `all` (default), `voice`, `gaze`, `gesture`, `fusion` or `resume` (exact strings; case-insensitive) | unknown track → prints `teach track must be: all \| voice \| gaze \| gesture \| fusion \| resume`, **exit 1** |
| `help-me` | a free-text question (e.g. `"how do I scroll?"`); keyword-matched against 8 real topics (overview, scroll, click, drag, zoom, teach, gesture, broken) | omitted / unmatched → the overview capability panel (`broken` / "why didn't that work?" style questions get the gate-by-gate debug answer) |
| `transcribe` | flags only: `--text` (force TEXT PRACTICE), `--mic N` (input device index; omitted = system default), `--provider NAME` (pin the local ASR engine: pocketsphinx \| vosk \| whisper), `--offline` (force the deterministic test provider). A positional value is ignored — the session is the interface. | — |
| `voice-setup` | flags only: `--check-only` / `--dry-run` (inspect + plan, installs NOTHING), `--provider NAME` (prefer an engine), `--repair` (re-verify + fix an existing installation), `--yes` (skip the interactive consent prompt — scripted installs), `--verbose` (per-engine wheel detail). | — |
| `reset` | `command_arg`: `config` / `models` / `all` (also accepted as `--config` / `--models` / `--all`; omitted = `config`). `--yes` skips the interactive confirmation (scripted use); anything else prints the usage line and exits 2. | — |
| `update` | performs the check on ANY invocation (no separate usage mode); the conventional spelling `airmouse update --check` resolves `--check` through argparse prefix-matching of the registered `--check-only` flag — harmless and ignored. | — |
| `voice-benchmark` | flag only: `--wav <file>` (supply REAL speech to measure real transcription latency + words/minute; without it, timing-only — no accuracy claims). | — |
| `learn` | not used (always runs all academies) | — |
| everything else | not used | — |

---

## Flags (complete, in parser order)

### Session basics (v3–v4, preserved)

| Flag | Effect |
|---|---|
| `--skip` | skip tutorial |
| `--tutorial` | force tutorial |
| `--no-cam` | hide camera window (also forces `academy`/`gesture-lab` into headless plan mode) |
| `--no-sound` | disable audio feedback |
| `--cam CAM` | camera index (int) |
| `--power POWER` | exponent curve power (float) |
| `--scale SCALE` | sensitivity scale (float) |
| `--precision` | start in precision mode |
| `--mode {direct,ironman}` | tracking mode: direct (1:1) or ironman (exponential) |
| `--trackpad` | trackpad mode: tap=click, hold=drag, 2-finger=scroll |
| `--monitor MONITOR` | monitor index (0=primary) |
| `--list-monitors` | list monitors and exit |
| `--autostart {on,off}` | enable/disable auto-start |
| `--settings` | open the tkinter settings GUI |

### v5 — voice + macros (preserved)

| Flag | Effect |
|---|---|
| `--voice` | enable voice commands (SpeechRecognition + pyaudio) |
| `--voice-mode {normal,high,turbo,command,dictation,hybrid}` | v5 sensitivities (normal/high/turbo) or v10 modes (command/dictation/hybrid) |
| `--mic MIC` | microphone index (default: system default) |
| `--no-kalman` | disable hybrid One Euro + Kalman filter (pure One Euro) |
| `--no-zoom` | disable pinch-to-zoom gesture |
| `--no-calibration` | disable adaptive calibration |
| `--calibrate` | run guided 8 s calibration sweep on startup |
| `--record NAME` | record a macro this session |
| `--play NAME` | replay macro NAME on startup (spine class: DESTRUCTIVE — refused unless `gesture_allow_destructive = true`) |
| `--macros` | list saved macros and exit |

### v9 — multimodal (preserved)

| Flag | Effect |
|---|---|
| `--gaze` | enable webcam gaze/eye tracking |
| `--no-gaze` | disable gaze even if enabled in config |
| `--gaze-calibrate` | guided gaze→screen calibration, save, exit |
| `--fusion` | FUSION mode: gaze targets + hand confirms + voice intents |
| `--hands-free` | HANDS-FREE mode: eyes target, voice commands, dwell confirm |
| `--assist` | ASSIST mode: multimodal observation, actions need confirmation |
| `--interaction {hand,gaze,voice,fusion,hands-free,assist}` | explicit interaction mode (overrides `--fusion`/`--hands-free`/`--assist`) |
| `--no-voice` | disable voice control |
| `--version` | print version and exit |

### v16 — agent wire server (new)

| Flag | Effect |
|---|---|
| `--aip-stdio` | serve the AIP JSON-lines protocol on stdin/stdout (agent-core `stdio://` / agent-sdk-js `StdioTransport`). Runs before any camera/mediapipe init. Default endpoint is **simulated** and honestly labels every result; EXECUTE is fail-closed until permissions are granted. |
| `--aip-real` | with `--aip-stdio`: route agent EXECUTE through the real, permission-gated `ActionEngine` (`PynputExecutor`). If the real executor cannot be built the wrapper prints a notice and falls back to simulated — it never exits 2. |

> **Strict-client note (measured):** the `--aip-stdio` CLI wrapper prints
> one human-readable banner line to stdout before the JSON-lines loop
> starts. `agent-core`'s `stdio://` client and `agent-sdk-js`
> `StdioTransport` parse the **first** stdout line as JSON, so point them
> at the module entry `python -m airmouse.aip_stdio` instead — it emits
> nothing but reply lines. Both entries speak the identical envelope.

### v16 — browser last mile (new)

| Flag | Effect |
|---|---|
| `--launch-browser` | discover and launch Chrome/Chromium/Edge with `--remote-debugging-port` and connect browser control to it (isolated throwaway user-data-dir by default; reuses whatever already answers on the port) |
| `--browser-port BROWSER_PORT` | CDP port for `--launch-browser` (int, default 9222) |

### v10 — offline engine (preserved)

| Flag | Effect |
|---|---|
| `--offline` | TRUE OFFLINE mode: block network features, local ASR/grammar only |
| `--browser` | enable local browser bridge control (semantic page targets) |
| `--browser-bridge` | start the localhost browser-bridge server (extension endpoint) |
| `--gesture` | enable the gesture registry (custom gesture mappings from `gestures.json`) |
| `--rf` | enable the RF-sensing modality (optional hardware; idles without it) |

### v11.5 — intelligence + modes (preserved)

| Flag | Effect |
|---|---|
| `--intelligence` | enable the adaptive intelligence plugin (local + offline) |
| `--no-intelligence` | disable the adaptive intelligence plugin |
| `--dictation` | voice typing / dictation formatting session |
| `--transcribe` | live transcription session (streaming, local) |
| `--teacher` | teacher mode |
| `--student` | student mode |
| `--office` | office mode |
| `--meeting` | meeting mode |
| `--research` | research mode |

### v15.1 → v16 — release flags (preserved)

| Flag | Effect |
|---|---|
| `--guided` | `test`: run the interactive guided laboratory |
| `--verbose` | `doctor`: show per-section detail and fixes |
| `--repair-config` | `setup` (v16.5.1): safely repair a broken/legacy-encoded config — backup → repair → validate → report; never destroys the original |
| `--json` | `doctor`/`privacy`: machine-readable output |
| `--to TO` | `memory export`: destination file path |
| `--debug` | show technical details (redacted traceback) when something fails |

### v19 — lifecycle + capability flags (new)

| Flag | Effect |
|---|---|
| `--advanced` | `doctor`: technical diagnostics — alias of `--verbose` (also accepted as the `command_arg` `advanced`); `doctor --advanced` is the Diagnostics menu item |
| `--aip` | `capabilities`: show the AIP agent-protocol view (the pre-v19 DISCOVER list) instead of the default user-facing registry |

(`--yes`, registered in v16.5.3 for `voice-setup`, is reused by `reset`
and `uninstall` as the scripted-confirmation bypass.)

### Hidden (registered, suppressed from `--help`)

| Flag | Effect |
|---|---|
| `--gaze-sim` | simulated gaze calibration for CI only (`argparse.SUPPRESS` — not part of the public surface) |

---

## Subcommand reference (exit codes measured)

### Getting started

| Command | Prints / does | Exit codes |
|---|---|---|
| `setup` | 11-step consent-gated wizard; never installs without an interactive Y; writes `<home>/.setup_complete`. With `--repair-config`: backup → repair → validate → report (v16.5.1) | 0 (also on honest hardware rows) |
| `doctor` | 12 sections / 43 components; every WARNING/FAILED row explains WHAT / WHY / FIX (v16.5.1); hardware rows carry `[CAPABILITY-STATE]` labels; verdict `[READY FOR TESTING]` / `[PARTIAL — …]` / `[BLOCKED]`; `--json` supported | 0 READY · 1 PARTIAL · 2 BLOCKED |
| `test` | 12-test laboratory; physical tests NEVER auto-pass (ACTION_REQUIRED), simulations labelled `[SIMULATION]`; `--guided` = interactive | 0 unless a test FAILs · 1 on FAIL |
| `verify` | 12 automated checks (import, voice determinism, intelligence roundtrip, safety gates, offline 18/18, simulated browser, permission deny-by-default, lease conflict, AIP validator, packaging match, **Teacher** — onboarding ladder + honest grading + help + profile store + learning-loop approval gate + transcribe session, **Temporal** — recognizer timing ≈293 µs/frame) + 5 physical ACTION_REQUIRED rows | 0 · 1 on any automated FAIL |
| `privacy` | telemetry/network/model state, store inventory, controls, the **storage manifest — all 24 on-disk artifacts** with purpose + location + exists flag, and the **PERSONALIZATION summary** (learned-counters only, ends `Nothing is uploaded.`); `--json` supported | 0 |

### v16 gesture surface (new)

| Command | Prints / does | Exit codes |
|---|---|---|
| `academy [lesson\|all]` | 11-lesson Gesture Academy. With a camera + display: live hold-to-pass lessons (detected gesture, confidence, progress bar, `[SPACE]` skip = no credit, `[q]` quit), resumable via `academy_progress.json`. Headless or `--no-cam`: prints the full plan with per-lesson status `PHYSICAL PRACTICE REQUIRED` (7 core lessons: move 2.0 s, click 1.5 s, double_click 1.5 s, right_click 1.5 s, drag 2.0 s, scroll 2.0 s, zoom 2.5 s) and `not verifiable in this run (never auto-passed)` for the 4 advanced teach-only lessons (gaze, voice, two_hand, sequences). A lesson is **never auto-passed** without a camera. | 0 (incl. honest camera degrade) · 1 unknown lesson id |
| `gesture-lab [seconds]` | Gesture Lab — live observatory. Builds a REAL `GestureActionRouter` whose executor is a dry-run stub (nothing real can fire, destructive policy forced OFF) and prints HAND / GESTURE / CONFIDENCE / MODE / TWO-HAND / LAST ACTION / RESULT at ~5 Hz so you can watch the confidence and policy gates refuse actions. `seconds` (integer) bounds the run; otherwise Ctrl-C ends it. Headless: prints the observatory explanation + two exact example readouts (an executed pinch click and a blocked OK-gesture `close_window`). | 0 (always — a readout, not a test) |
| `profile <name\|list>` | Lists or applies one of 8 curated interaction profiles through config.py's own load/save mechanism. Unknown/out-of-range values are refused fail-closed; the profile whitelist structurally cannot touch destructive/telemetry/offline/safety flags. | 0 applied or listed · 1 unknown profile |

Profiles and what they set (12-key whitelist: tracking_mode, deadzone,
exp_power, exp_scale, gesture_confirm_frames, gesture_action_confirm_frames,
gesture_min_confidence_safe, gesture_min_confidence_caution, audio_enabled,
adaptive_calibration, two_hand, position_smooth_alpha):

| Profile | Character |
|---|---|
| `default` | explicit factory mirror of the config.py class defaults |
| `accessibility` | larger deadzone (0.02), higher confirm frames (6/8), floors 0.55/0.70, audio ON, adaptive calibration ON |
| `presentation` | floors 0.60/0.75, confirm 4/6, audio OFF |
| `gaming` | confirm 2/2, smoothing 0.90, adaptive calibration OFF (no mid-match drift) |
| `hands_free` | `two_hand = true`, confirm 5/7, audio ON |
| `developer` / `media` / `creative` | per-mode tracking/confirm tuning (see `config.py` defaults the profile copies) |

### v16.5 — teacher + voice + help (new)

| Command | Prints / does | Exit codes |
|---|---|---|
| `teach [track]` | The teacher. Prints the track plan (per lesson: do this / demo / pass when / status) and, on a TTY, runs the interactive tour. Tracks: `all` (5 tracks — Voice, Eyes, Hands, Multimodal, Personalization; 11 lessons), `voice`, `gaze`, `gesture`, `fusion`, `resume`. Headless (non-TTY stdin): prints the plan with every physical lesson marked `PHYSICAL PRACTICE REQUIRED — needs camera/microphone; never auto-passed`, marks NOTHING complete, and ends with the learning-progress panel (`Sessions: n • Phase: …`). First-run state is persisted to `<home>/profile/onboarding.json` (ladder NEW → IN_PROGRESS → VOICE_COMPLETE → GAZE_COMPLETE → GESTURE_COMPLETE → FUSION_COMPLETE → COMPLETE; a corrupted file fail-safes to NEW with a `corrupted_last_load` flag). The teacher also prints a self-diagnostic hardware panel and adapts practice (a strong skill is skipped, a weak one gets a gentle repeat). Plain `airmouse` on a TTY offers the tour automatically (`config.teach_auto`, default true); decline/EOF/non-TTY always proceeds — headless is NEVER blocked. | 0 (plan or measured session, incl. honest headless degrade) · 1 unknown track |
| `learn` | Same teacher, all academies at once (identical to `teach all`): the plan covers the Gesture Academy (delegates to `airmouse academy`), the Voice Academy (4 levels — basic commands, natural language, dictation with spoken punctuation, personal voice learning), the Gaze Academy (5 lessons — acquire, fixation, dwell, blink, eye-assist), fusion challenges and the personalization acknowledgment. Headless = honest plan, nothing auto-passed. | 0 |
| `transcribe` | A live local transcription session over the AUTHORITATIVE provider registry (v16.5.2). Auto mode: with a real local ASR engine AND a microphone → `provider: pocketsphinx (local ASR engine)` + `mode: MIC LIVE` (sounddevice-first capture → VAD → local ASR; a bare Enter records one spoken utterance); with an engine but no mic → text-input mode (typed lines through the real pipeline, labelled); with no usable engine → `provider: simulated_stream` + a LOUD `⚠ SIMULATED provider` banner that lists WHY each engine was rejected (`pocketsphinx: INITIALIZATION FAILED — <reason>` or `NOT INSTALLED`) + the FIX. `--text` forces TEXT PRACTICE (explicitly NOT microphone validation); `--offline` forces the test provider and says so. Confidence is shown as `conf: unavailable` when the engine supplies none — never invented. REPL: `pause \| resume \| save [json] \| clear \| status \| stop \| quit` — any other text is a spoken utterance. `save` writes ONLY on request, ONLY to `<home>/transcripts/transcript-<ts>.txt` (or `.json`), text only, never audio; EOF ends the session cleanly. | 0 (also on clean EOF; the summary line reports the session honestly) |
| `help-me [question]` | Capability answers built from REAL data: the overview panel lists the hand gestures (22 rows, aligned with `gestures.py` + the spine's risk classes, including `[refused by default]` on the OK/Alt+F4 row and the honest two-hand note `Rotation and two-hand drag are DETECTED but their OS action is NOT MAPPED yet`), the 30-phrase offline voice grammar (with `It is NOT full speech recognition` note), gaze, the teacher commands and health commands. With a question, keywords route to a topic answer: `"how do I scroll?"` → the 3 real scroll paths; `"why didn't that work?"` → the gate-by-gate debug chain (e-stop → confidence floors 0.45/0.60 → destructive policy → 0.12 s rate limit → gesture mapping). | 0 |
| `voice-status` (v16.5.2 §8) | THE authoritative voice debugging view — ONE call to the same registry doctor and transcribe use. Sections: `MICROPHONE` (✓ detected / devices n / selected device / backend sounddevice\|pyaudio / sample rate / channels, or ○ not detected + FIX), `ASR PROVIDERS` (per engine: installed, importable, model, initialization PASS\|FAIL\|DEFERRED\|MODEL MISSING\|NOT RUN, runtime READY\|FAILED\|NO, role ACTIVE\|FALLBACK\|OPTIONAL\|DISABLED; `--verbose` adds the exact failure reason + probe time), `TRANSCRIPTION` (real microphone: AVAILABLE/NOT AVAILABLE, provider, mode MIC LIVE\|TEXT INPUT, offline: YES, audio storage: NONE, network: NOT REQUIRED). With no usable engine the install hint is BOOTSTRAP-AWARE (v16.5.3): it recommends `airmouse voice-setup` — the zero-compiler path — or states the `NATIVE_ASR_PACKAGE_UNAVAILABLE` diagnosis when no prebuilt wheel exists for this interpreter. | 0 |
| `voice-setup` (v16.5.3 §8/§9; v19 pin correction) | The ZERO-COMPILER local voice bootstrap. Inspects microphone + registry + wheel compatibility (static verified matrix: PocketSphinx 5.1.1 = cp311/cp313 only, NO cp312 anywhere; vosk 0.3.45 = py3-none wheels on win_amd64/win32/manylinux — native lib bundled), picks the deterministic lightweight path, installs via `python -m pip install --only-binary :all:` (a source build is structurally impossible — NMake/MSVC can never run), fetches the ONE official lightweight model (vosk small en-US, **41.2 MB = 41,205,931 B — v19 CORRECTED pin: the v16.5.3 pin `507575b9…` / 41,205,932 B was WRONG and broke every download; v19 verifies SHA256 `30f26242…42498` AND SHA512 `d56243ba…3bc5a` through the 12-stage model-integrity pipeline: fsync → byte count → digests → CRC → zip structure → zip-slip → exec/symlink refusal → staged extract → validate → ATOMIC install with rollback; consent-gated, Zip-Slip-safe, executable-member refusal) when the chosen engine needs one, verifies the engine initialization + microphone, runs a deterministic recognition smoke test, and reports `VOICE READY` (Provider/Mode/Network/Audio lines) or the honest `VOICE SETUP COULD NOT COMPLETE` panel with reason + next step. `--check-only`/`--dry-run` never install; consent is asked interactively (or `--yes`). | 0 READY · 1 could-not-complete |

### Memory lifecycle

| Command | Prints / does | Exit codes |
|---|---|---|
| `memory status` | per-store inventory (empty/ok/CORRUPT, schema version, record count) for twin, vocabulary, skills, workflows, preferences | 0 |
| `memory export [--to PATH]` | bundles the 5 stores + real learning artifacts (intelligence jsons + model.bin, calibration, gestures, macros, lecture notes) into one local JSON; default destination `<home>/exports/airmouse-memory-<ts>.json` | 0 · user error surfaced with fixes |
| `memory reset` | consent-gated (non-TTY = cancelled); backs up to `<home>/backups/`, clears stores **and** learning artifacts, then runs `deletion_verifies()` and prints `verification: CLEAN` | 0 · 1 if any store/artifact failed to clear |
| `memory delete` | consent-gated; removes store files + artifacts (backups kept) | 0 · 1 on incomplete delete |
| `memory` (no arg) | top learned interaction patterns (frequency / success rate / corrections) | 0 |

### v11.5 + v15 info commands

| Command | Prints | Exit |
|---|---|---|
| `intelligence` | plugin state, learning/privacy flags, model size vs ~30 MB budget, memory patterns, vocabulary terms, workflows | 0 |
| `vocabulary` | learned terms + correction pairs | 0 |
| `workflows` | discovered workflows (steps, success/failure, destructive flag) | 0 |
| `self-test` | 15 components (Core, Voice, Transcription, RealLocalASR, Gesture, Gaze, Camera, Browser, Fusion, Intelligence, Memory, Prediction, Safety, Offline, Packaging) → PASS / OPTIONAL / HARDWARE / FAIL | 0 |
| `status` | version banner, protocol, permission hierarchy, license tier | 0 |
| `capabilities` | v19 default: the user-facing registry — four interaction rows with state, FIX lines and `[REALITY/EPISTEMIC]` evidence labels, the voice-model record (state/license/source), best available mode + readiness (`--json` = machine view; `--aip` = the AIP DISCOVER rows `[+/-] name (kind) perm=…`) | 0 |
| `observe` | one observation from the deterministic simulator (labelled simulated) | 0 |
| `world` | temporal world model explain + predict_state | 0 |
| `twin` | twin facts/observations/corrections/errors | 0 |
| `skills` | skill library listing (proposal + approval required) | 0 |
| `agents` | registry listing (priority, state) | 0 |
| `permissions` | permission keys + decision vocabulary (ASK fails closed) | 0 |
| `tasks` | task engine listing | 0 |
| `protocol` | AIP version + message concepts + schema names | 0 |
| `benchmark` | twin/world/task import + construct + loop timings | 0 |

### v19 — launcher, wizard + lifecycle (new)

Plain `airmouse` (no args, first run, TTY) shows the **v19 launcher** —
not a camera window: `[1] Start AirMouse  [2] Test My Hardware
  [3] Setup  [4] Learn Gestures  [5] Settings  [6] Diagnostics
(advanced)  [7] Privacy  [0] Help` plus an honest status panel (dots
probed live via the quick capability registry: `●` READY, `◐` WARNING,
`○` OPTIONAL, `·` absent, `✗` failed, `?` probe unavailable; verdict
line `System Ready` or `System: <MODE>`). After `airmouse setup` writes
the marker, plain `airmouse` starts the app directly. EOF/abort simply
proceeds — the menu never blocks the app.

| Command | Prints / does | Exit codes |
|---|---|---|
| `wizard` | Six teaching steps (camera → hand → gesture → voice → input → verdict); each step PASSES only when its probe succeeded on THIS machine; missing hardware = ACTION_REQUIRED with the exact fix. Verdict: READY (camera+gesture+input all pass) / PARTIAL (something passed) / BLOCKED (nothing). | 0 READY · 1 PARTIAL · 2 BLOCKED |
| `reset [--config\|--models\|--all]` | Prints exactly what will be removed (per-entry human descriptions; `--all` adds total MB and the profile/transcripts/macros warning), asks `y` (`--yes` for scripts). `--config` backs the config up into `backups/` first; `--models` removes re-downloadable models; `--all` removes the whole home. | 0 done · 1 cancelled/failed · 2 unknown scope |
| `repair` | Safe local fixes only — home structure, config repair (backup → repair → validate), model re-verification (a `MODEL_CORRUPTED` tree is auto-removed with a note to re-run `voice-setup`), setup marker. Never installs anything; pip is never run (red-team tested). | 0 all repaired · 1 some repairs failed |
| `uninstall` | Content summary first (every home entry + total MB + the exact `pip uninstall` command), explicit `y` confirmation, then removes `~/.airmouse`. | 0 removed · 1 cancelled/failed |
| `update --check` | Reads the PyPI JSON API on ANY invocation (the invocation IS the consent; `--check` resolves via argparse prefix-matching of `--check-only` and is ignored), compares numerically against the installed version, prints installed/latest + the upgrade command — NEVER downloads or installs. Offline: an honest "cannot check right now" answer. | 0 (always) |

### v10 diagnostics

| Command | Prints | Exit codes |
|---|---|---|
| `voice-status` | v16.5.2 §8 sections: MICROPHONE (detection detail) + ASR PROVIDERS (per-engine honest rows; `--verbose` adds reasons + probe times) + TRANSCRIPTION verdict (offline: YES, audio storage: NONE, network: NOT REQUIRED) + the bootstrap-aware install hint when no engine is usable | 0 · 1 renderer failure |
| `voice-benchmark` | v16.5.3 §22 — deterministic local benchmark: provider + engine startup, first-result latency, average utterance latency (scripted PCM), CPU time, peak RAM, disclosed wheel/model sizes. Real-speech metrics (latency, words/minute) ONLY with `--wav`. Never claims accuracy without samples. | 0 · 1 no usable engine |
| `gestures` | built-in gesture→intent mappings + custom mappings from `gestures.json` | 0 |
| `commands` | the 75-command grammar across 10 namespaces (engine, mouse, system, window, application, files, text, navigation, media, browser) | 0 |
| `browser` | `CDP bridge on :9222 -> available: True/False` + start instructions | 0 |
| `offline-test` | full-stack selftest with networking really blocked (18 checks) | 0 all pass · 1 any fail |
| `diagnostics` | version, ASR providers, offline selftest summary, optional-dep probe (cv2/mediapipe/pynput/speech_recognition/vosk/whisper) | 0 · 1 if offline selftest fails |

---

## Exit-code summary

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `doctor` | READY | PARTIAL | BLOCKED |
| `test` | no FAIL | a test FAILed | — |
| `verify` | no automated FAIL | automated FAIL | — |
| `academy` | ok / honest headless plan | unknown lesson id | — |
| `teach` | plan / measured session (incl. honest headless) | unknown track | — |
| `profile` | applied / listed | unknown profile | — |
| `memory reset/delete` | complete | INCOMPLETE (per-store errors printed) | — |
| `offline-test`, `diagnostics` | selftest OK | selftest failed | — |
| `wizard` | READY | PARTIAL | BLOCKED |
| `reset` | scope removed | cancelled/failed | usage |
| `repair`, `uninstall` | complete | cancelled/failed | — |
| `update --check` | always 0 (result is the printed verdict) | — | — |
| everything else | 0 | — | — |

---

## Environment variables

| Variable | Meaning |
|---|---|
| `AIRMOUSE_HOME` | THE storage home override — resolved fresh on every call by `paths.py`; wins over `~/.airmouse` for config, stores, artifacts, backups, exports |
| `AIRMOUSE_AIP_SIMULATOR=1` | `python -m airmouse.aip_stdio`: force the simulated endpoint |
| `AIRMOUSE_AIP_REAL=1` | `python -m airmouse.aip_stdio`: require an in-process executor (injected via `AIRMOUSE_AIP_ENGINE`); exits 2 with a stderr explanation when absent |
| `AIRMOUSE_AIP_ENGINE=module:attr` | loader for a real ActionEngine for the module entry |

---

## v16.5 storage notes (this tree, measured)

| Location | Written by | Content |
|---|---|---|
| `<home>/profile/onboarding.json` | `teach` / `learn` / the first-run offer | onboarding ladder phase, per-track completion, session count (schema_version 1). Corrupted file → treated as NEW + `corrupted_last_load` flag. |
| `<home>/profile/{interaction,voice,gestures,preferences}.json` | the live app + LearningLoop | bounded, content-free learning (counters/parameters only); unverified observations go under an `unverified.` namespace and are never used for suggestions |
| `<home>/transcripts/transcript-<ts>.txt\|.json` | `transcribe` — ONLY when you type `save` | the text transcript you explicitly saved (text only; audio is never stored); listed in the privacy manifest as `transcript_sessions` and left intact by `memory reset/delete` (user-owned keeps — delete the folder yourself) |
| `<home>/profile/gaze.json` | Gaze Academy + GazeLearner | bounded local gaze personalization (dwell clamped to [0.3, 2.0] s etc.); suggestions are proposals, never auto-applied |

---

## v16/v16.5 configuration keys (config.toml `[v10]` section)

| Key | Default | Meaning |
|---|---|---|
| `two_hand` | `false` | two-hand tracking (tracker `max_hands=2` + `TwoHandGestureRecognizer`); needs camera + mediapipe multi-hand |
| `gesture_min_confidence_safe` | `0.45` | confidence floor for SAFE spine actions |
| `gesture_min_confidence_caution` | `0.60` | confidence floor for CAUTION spine actions |
| `gesture_allow_destructive` | `false` | NEVER true by default — Alt+F4 (`close_window`) and macro replay stay refused |
| `gesture_sequences` | `true` | feed confirmed hand gestures to the custom-sequence registry |
| `selftune_apply` | `false` | opt-in self-tuning application flag (reserved; see CAPABILITY_MATRIX — no shipped code path applies tuner proposals automatically) |
| `teach_auto` | `true` | v16.5 — offer the first-run teaching tour when plain `airmouse` runs on a TTY (decline always proceeds; headless is never blocked) |
| `ready_panel` | `true` | v16.5 — print the zero-learning-curve `AIRMouse READY` panel after startup (display-only: Hands/Voice/Gaze/Learning state + `say "help" anytime` + teach reminder) |

---

## Typical combinations

```bash
airmouse                                          # v5 hand+gesture experience (execution spine gates every action)
airmouse teach                                    # 3–5 min interactive tour (headless = honest plan)
airmouse teach voice                              # one track: all|voice|gaze|gesture|fusion|resume
airmouse learn                                    # all academies at once
airmouse transcribe                               # live text session (save [json] writes to <home>/transcripts/)
airmouse help-me "how do I scroll?"               # capability answers from real data
airmouse academy                                  # Gesture Academy — full plan headless, live with a camera
airmouse academy move                             # practice one lesson
airmouse gesture-lab 20                           # 20 s dry-run observatory (watch gates refuse actions)
airmouse profile accessibility                    # apply the accessibility preset (12 settings)
airmouse profile hands_free                       # preset that enables two_hand
airmouse --trackpad --calibrate                   # trackpad feel + guided calibration
airmouse --aip-stdio                              # AIP wire server (simulated, honestly labelled)
airmouse --aip-real --aip-stdio                   # real permission-gated ActionEngine behind the wire
airmouse --launch-browser                         # discover + launch Chrome/Edge with CDP + connect
airmouse --offline --voice-mode hybrid --gesture  # hard-offline session with registry
airmouse self-test                                # 15-component honest report
airmouse offline-test                             # prove the offline story (18/18)
airmouse memory status                            # local store inventory
airmouse wizard                                   # 60-second interaction wizard (v19)
airmouse capabilities                             # user-facing registry + evidence labels (v19)
airmouse capabilities --aip                       # the AIP agent-protocol view (preserved)
airmouse doctor --advanced                        # full technical report (v19 alias)
airmouse repair                                   # safe local fixes — installs nothing (v19)
airmouse reset --models                           # remove re-downloadable models (asks first, v19)
airmouse update --check                           # explicit-only PyPI check, never installs (v19)
```
