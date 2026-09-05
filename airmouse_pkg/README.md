# AirMouse v16.5.0 — UNIVERSAL HUMAN + AI INTERACTION PLATFORM

Voice + hand + gaze + RF + keyboard/mouse + screen + browser → one
**event bus** → fusion → world model → intent → **one execution spine**
(estop → confidence → risk class → policy → rate limit) → action →
verification → **learning**. Fully offline-capable: no LLM, no cloud
AI, no network required for any control path — a **personal model**
that learns your patterns locally, with prediction that can advise but
never execute — plus a **temporal world model, goals, tasks, skills,
self-healing recovery, universal target resolution, the AIP 1.0 agent
protocol with a real stdio wire server, SDKs for Python and JavaScript,
multi-agent infrastructure, a global permission hierarchy, transparent
licensing, a deterministic simulator and failure injection** — and a
**gesture-first release surface**: 17 hand poses + 10 motion gestures +
3 pinch events + 4 two-hand classes behind a single safety spine, the
11-lesson Gesture Academy, the Gesture Lab observatory, 8 interaction
profiles, a browser launcher, and a hard privacy lifecycle over a
**single unified home** — and, new in v16.5, **Adaptive Multimodal
Intelligence**: a teacher that trains you in voice, gaze, gestures and
fusion (a physical skill is NEVER auto-passed), an honest voice stack
(deterministic command grammar is not ASR — and it says so), live
text transcription, `help-me` answers built from real capability data,
a temporal gesture observer that PROPOSES but never executes, and a
local-first personal interaction profile you can inspect and erase.

Humans and AI agents go through the SAME pipeline — agents get no
bypass pathway.

![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue)
![Platform](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macos-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)
![Offline](https://img.shields.io/badge/offline-capable-success)
![Local](https://img.shields.io/badge/cloud-structurally%20impossible-critical)

## What AirMouse is

AirMouse is a desktop application (Windows / Linux / macOS, Python
3.9+) that lets you control your computer with your **voice, hands
(webcam hand tracking), eyes (webcam gaze), keyboard/mouse, screen and
browser** — and that lets AI agents do the same through the same
deterministic, permission-gated pipeline. Everything runs **locally**:
there is no cloud AI code path, no account, no telemetry by default.
It ships with a guided setup wizard, an honest health doctor, a
12-test guided laboratory for validating hardware on YOUR machine, a
Gesture Academy that teaches every gesture, a 3–5 minute interactive
teacher (`airmouse teach`) that trains every modality, and a local
memory lifecycle (export / reset / delete) that you control.

## Why it exists

- **Access and comfort** — full computer control without reaching for
  the mouse: for accessibility, for presenters, for messy desks, for
  RSI sufferers.
- **Privacy** — control paths that work with the network cable pulled
  out. Learning stores *patterns, not content*, on your disk, and you
  can export or delete every one of the 24 declared artifacts.
- **One honest pipeline for humans AND agents** — an AI agent that
  wants to click a button goes through the same world model, safety
  gates, permission engine and verification as you do. There is no
  agent bypass pathway, and E-STOP always wins.
- **Honesty over marketing** — every capability in
  `docs/CAPABILITY_MATRIX.md` carries exactly one status: REAL /
  SIMULATED / OPTIONAL / PHYSICAL TEST REQUIRED / NOT AVAILABLE, and
  hardware checks can never auto-pass.

## v16.5 — Adaptive Multimodal Intelligence (new)

- **A teacher, not a manual:** `airmouse teach
  [all|voice|gaze|gesture|fusion|resume]` prints a personalized plan
  per track and, in an interactive terminal, walks you through it.
  `airmouse learn` covers all academies at once. On a fresh machine,
  plain `airmouse` (TTY only) offers the 3–5 minute tour; if you
  stopped halfway it asks "Continue your training?"; **"Skip for
  now" always works** — the prompt never appears without a TTY and
  never traps you. Progress is persisted in
  `<home>/profile/onboarding.json` and climbs a honest ladder:
  NEW → IN_PROGRESS → VOICE_COMPLETE → GAZE_COMPLETE →
  GESTURE_COMPLETE → FUSION_COMPLETE → COMPLETE. A corrupted state
  file fail-safes back to NEW (with a flag), and the teacher prints a
  self-diagnostic hardware panel plus adaptive practice: a skill you
  have mastered is skipped, a weak one gets a gentle repeat.
- **Physical lessons are never auto-passed.** Headless, `teach`
  prints the exact same plan with every physical lesson marked
  `PHYSICAL PRACTICE REQUIRED — needs camera/microphone; never
  auto-passed` and marks NOTHING complete. Only live sensors can
  pass a live lesson.
- **The voice stack is honest:** `airmouse voice-status` now shows a
  per-machine provider panel — ✓ Built-in command recognition (always
  real: a deterministic offline grammar), ○ Local ASR / ○ Whisper /
  ○ Vosk / ○ Microphone, one `Active:` line, and the plain sentence
  *"Command recognition still works — it is deterministic grammar,
  not ASR."* Optional local ASR engines (Vosk / Whisper /
  PocketSphinx) are auto-detected the moment you pip-install them —
  AirMouse never installs anything for you.
- **Live transcription:** `airmouse transcribe` runs a local session
  (REPL: `pause | resume | save [json] | clear | status | stop |
  quit`). Every segment carries a timestamp + confidence; history is
  bounded (500 segments); saving is EXPLICIT and lands only under
  `<home>/transcripts/` — text only, audio is never stored. Without
  an ASR engine the session runs on a deterministic provider that is
  banner-labelled `⚠ SIMULATED provider` — never passed off as real
  recognition.
- **`airmouse help-me [question]`** answers from real capability
  data — the same gesture map `gestures.py` and the spine use (22
  rows, including the honest note that two-hand rotate/drag are
  detected but NOT MAPPED to OS actions), the shipped 30-phrase
  voice grammar, and gate-level debugging ("why didn't that work?"
  walks the e-stop → confidence → policy → rate-limit chain).
- **Gesture temporal intelligence (observer only):** a deterministic
  temporal recognizer (`temporal.py`) watches the same frame stream
  the live app already sees — trajectory features
  (velocity/acceleration/direction/handedness), a pinch lifecycle
  START/HOLD/MOVE/RELEASE with hysteresis, debouncing, false-positive
  suppression and tracking grace — and merges voice + gaze + gesture
  into ONE intent proposal. In the app it appears as HUD badges
  `TMP:` (pinch lifecycle / proposal) and `SENSOR:` (health
  degraded/poor). **It PROPOSES — the spine disposes:** the gesture
  spine remains the sole dispatcher and no proposal executes an OS
  action (enforced by source-scan tests). A robustness toolkit
  (Hysteresis, Debouncer, FalsePositiveSuppressor, TrackingRecovery,
  CameraWatchdog, SensorHealthScore) ships alongside; the recognizer
  costs ≈293 µs/frame as measured by `airmouse verify`.
- **A local-first personal profile:**
  `<home>/profile/{interaction,voice,gestures,preferences}.json`
  store bounded, content-free learning (counters/parameters only —
  never raw audio, video or typed content). The LearningLoop enforces
  PREDICTION ≠ EXECUTION: a prediction is a proposal you explicitly
  approve before the profile adapts. `airmouse privacy` now ends with
  a PERSONALIZATION summary — *"Nothing is uploaded."* — and the
  privacy manifest grew to **24 declared artifacts** (adding
  `academy_progress`, `onboarding_state`, `personal_profile`,
  `transcript_sessions`), all covered by the memory reset / delete /
  export lifecycle (user-learning artifacts are backed up then
  deleted; transcripts are user-owned keeps).
- **Zero-learning-curve startup:** after startup the app prints an
  `AIRMouse READY` panel (Hands / Voice / Gaze / Learning ✓/○, the
  tip `say "help" anytime`, and a `airmouse teach` reminder while
  training is incomplete). It is display-only and gated by
  `config.ready_panel` (default true; `config.teach_auto` gates the
  first-run teaching offer, also default true).

## Features

- **Multimodal input:** voice commands + dictation, webcam hand
  tracking (**17 static poses + 10 motion gestures + 3 pinch events**,
  plus **4 two-hand classes** — HOLD / ZOOM / ROTATE / DRAG), webcam
  gaze with calibration, screen perception, browser bridge, RF
  abstraction (no hardware required), keyboard/mouse.
- **One execution spine** (`gesture_spine.py`): every gesture action
  passes **estop > confidence gate > risk class (SAFE / CAUTION /
  DESTRUCTIVE) > destructive policy > rate limit > dispatch**.
  OK-gesture Alt+F4 and macro replay are REFUSED by default;
  `[ESC]` trips the spine e-stop, `[x]` resets it.
- **Deterministic offline grammar:** 75 commands across 10 namespaces
  (30-phrase spoken grammar); no cloud, no LLM on the control path.
- **Gesture-first learning tools:** `airmouse academy` (11 lessons,
  never auto-passes a physical skill), `airmouse gesture-lab`
  (dry-run observatory that shows the safety gates refusing actions
  live), `airmouse profile` (8 curated presets, whitelist-locked).
- **Teaching + voice surface (v16.5):** `airmouse teach` /
  `airmouse learn` (multi-track teacher with persisted, resumable
  progress), the Voice Academy (4 levels inside `teach`/`learn`:
  basic commands, natural language, dictation with spoken
  punctuation, personal voice learning — local only),
  `airmouse transcribe` (live text session), `airmouse help-me`
  (capability answers), `airmouse voice-status` (honest provider
  panel).
- **Intelligence (optional, local):** Personal Interaction Twin (14
  fact categories), temporal world model, explainable prediction
  (PREDICTION ≠ EXECUTION), personal vocabulary, workflow discovery,
  bounded self-tuning. Learns patterns, never content; secrets are
  refused fail-closed.
- **Goals / tasks / skills:** COMMAND→INTENT→TASK→GOAL parsing,
  bounded task engine with human approval gates, skills learned only
  with notification + preview + approval.
- **Self-healing:** recovery engine (7 strategies, 14 failure
  diagnoses, bounded rounds), universal target resolution
  (accessibility→DOM→semantic-API→OCR→vision→geometry→coordinate).
- **Agent platform:** AIP 1.0 protocol, **real stdio wire server**
  (`airmouse --aip-stdio`, simulated by default, `--aip-real` routes
  through the permission-gated ActionEngine), Python SDK, stdlib-only
  standalone agent-core, dependency-free JS/TS SDK, multi-agent
  registry with exclusive resource leases, permission engine with the
  hierarchy E-STOP > HUMAN OVERRIDE > SAFETY POLICY > PERMISSION >
  AGENT > PREDICTION, agent budgets enforced.
- **Browser last mile:** `airmouse --launch-browser` discovers
  Chrome/Chromium/Edge, launches with CDP on an isolated throwaway
  profile, and pins every websocket to loopback.
- **Privacy lifecycle:** `airmouse privacy` prints the full storage
  manifest (24 artifacts) plus a PERSONALIZATION summary ending
  "Nothing is uploaded."; `airmouse memory reset|delete` clear real
  learning artifacts (intelligence stores, calibration, custom
  gestures, macros, lecture notes, academy/onboarding/profile
  progress) and verify the deletion; transcripts you explicitly
  saved are user-owned keeps you delete yourself.
- **v15.1 release surface (preserved):** `airmouse setup` (consent-
  gated wizard), `airmouse doctor [--verbose|--json]` (exit codes 0
  READY / 1 PARTIAL / 2 BLOCKED), `airmouse test` + `airmouse test
  --guided` (12-test laboratory), `airmouse verify`, `airmouse
  privacy`, `airmouse memory status|export|reset|delete`, and a
  first-run menu (plain `airmouse` on a fresh machine, TTY only).
- **Crash-safe local persistence:** atomic writes, schema versioning,
  checksum with corruption quarantine and recovery; ONE home resolved
  by `paths.py` (`$AIRMOUSE_HOME` or `~/.airmouse`).

## Architecture

```
  VOICE   HAND   GAZE   RF   KB/MOUSE   SCREEN   BROWSER
     └────────┴────────┴────────┴─────────┴───────────┘
                                   │  normalized Events
                                   ▼
              ╔══════════ EVENT BUS (local, bounded) ══════════╗
              ╚═════════════════════╦══════════════════════════╝
                        ▼                        ▼
              FUSION (authoritative      WORLD MODEL (temporal:
              engine; conflict resolver, app/window/targets/gaze/
              ask don't guess)           recent action + intent)
                        └───────────┬───────────┘
                                    ▼
          CONTEXTUAL RESOLVER · GRAMMAR · PERSONAL PREDICTION (data only)
                                    ▼
        ┌────── EXECUTION SPINE (estop · confidence · risk class · ─────┐
        │              destructive policy · rate limit)                 │
        │  ▼                                                            │
        │  OFFLINE GATE ──► ACTION ENGINE ──► VERIFICATION ──► LEARNING │
        │                 (32 action types,   expected vs     verified  │
        │                  allowlisted        observed        events →  │
        │                  executors) +                       personal  │
        │                  RECOVERY                           model     │
        └───────────────────────────────────────────────────────────────┘
```

The intelligence is **MODEL + MEMORY + CONTEXT + RULES + FUSION +
WORLD MODEL + VERIFICATION + LEARNING** — a compact local model with a
~30 MB capacity budget, quantized and versioned, that starts at a few
KB on a fresh install and grows with your usage. **It is not a neural
LLM**, and predictions are data for you and the intent layer — never
executions.

### Version-by-version (v12 → v16.5), all preserved in v16.5.0

| Version | Adds | Core modules / artifacts |
|---|---|---|
| **v12.0.0** | **Personal Interaction Twin** (14 fact categories; every fact carries source/confidence/context/timestamp/frequency/success_rate/provenance; learn/forget/decay/correct/export/import/reset/inspect/explain; optional by contract; secrets scrubbed fail-closed) + **Temporal Interaction World Model** foundation | `intelligence/twin/` |
| **v12.5.0** | **Temporal world model**: frozen HUMAN/COMPUTER/TASK sections; observe/snapshot/diff/history/transitions/explain/predict_state; expected-vs-observed mismatch detection; bounded history 128 | `world_model_temporal.py` |
| **v13.0.0** | **Goals** (COMMAND→INTENT→TASK→GOAL deterministic parser; optional labelled interpreter adapter that can never downgrade risk or enable execution) + **Tasks** (bounded TaskEngine, DAG dependencies, human approval gates, bounded retries, honest state-only rollback) | `goals.py`, `tasks.py` |
| **v13.5.0** | **Skills**: InteractionCompression (≥3 repetitions + confidence + notification + preview + approval, NEVER silent) + PersonalSkillLibrary (versioned/editable/revocable/exportable) | `skills.py` |
| **v14.0.0** | **Recovery 2** (PRECONDITION→EXECUTE→OBSERVE→VERIFY→RECOVER; 7 strategies; 14 failure diagnoses; bounded rounds) + **Universal target resolution** (7-provider chain) | `recovery2.py`, `target_resolver.py` |
| **v14.5.0** | **AIP 1.0 protocol** (18 conversation message types + STATUS; 12 strict fail-closed JSON schemas; same-major negotiation; 256 KB caps) + **Agent SDKs** + **standalone agent-core** (stdlib-only) + **JS/TS SDK** | `aip.py`, `agent_sdk.py`, `agent-core/`, `agent-sdk-js/` |
| **v15.0.0** | **Multi-agent registry** (identity/priority/capabilities/budgets/audit; exclusive resource leases; deterministic conflict resolution) · **Permission engine** (granular keys; ALLOW/DENY/ASK/ALLOW_ONCE/ALLOW_SESSION/ALLOW_PATTERN; ASK without human == NO) · **DO IT WITH ME** · one-choice onboarding module + 8 accessibility modes · transparent local licensing (FREE = complete core) · marketplace foundation (fail-closed manifests, NO code-execution path) · deterministic simulator · 12-class failure injection · six-question explainability · v15 CLI subcommands · v15 HUD badges | `agents.py`, `permissions.py`, `ditm.py`, `onboarding.py`, `licensing.py`, `marketplace.py`, `simulator.py`, `failure_injection.py`, `explain.py`, `__main__.py` |
| **v15.1.0** | **Hardening release of v15.0.0** — no architecture changes; adds the release surface: `setup` (consent-gated wizard), `doctor` (--verbose/--json, exit 0/1/2), `test` + `test --guided` (12-test laboratory), `verify`, `privacy`, `memory status\|export\|reset\|delete`, first-run menu, crash-safe `persistence.py`, user-grade error messages with token redaction, CLI performance budgets, red-team test suite. Tests 1056 → **1236** | `persistence.py`, `setup_wizard.py`, `capabilities.py`, `doctor.py`, `verify.py`, `guided_test.py`, `user_errors.py`, `cli_menu.py` |
| **v15.2.0** | **One execution spine + gesture evolution + agent/browser last mile**: `gesture_spine.py` gates EVERY gesture action (estop > confidence > SAFE/CAUTION/DESTRUCTIVE > policy > rate limit; Alt+F4 + macro replay refused by default); new gestures (thumbs_down, four, five, swipe up/down, circle cw/ccw, push/pull, shake, wave) + pinch_hold/pinch_release/double_pinch events; two-hand foundation (`two_hand.py`, tracker `max_hands=2`, zoom = real ctrl+wheel); gesture registry fed by hands; personalization loop closed (`observe_gesture`); fusion2 removed from shipped wiring; `--aip-stdio` wire server + ALLOW_ONCE + budget fixes; browser launcher + loopback ws pinning; unified home `paths.py`; privacy manifest + real reset/delete/export + `deletion_verifies()`; Academy + Lab + 8 profiles; suite 1236 → **1556** | `gesture_spine.py`, `gestures.py`, `two_hand.py`, `tracker.py`, `aip_stdio.py`, `agent_sdk.py`, `permissions.py`, `agents.py`, `browser.py`, `paths.py`, `privacy.py`, `persistence.py`, `academy.py`, `gesture_lab.py`, `gesture_profiles.py` |
| **v16.0.0** | **Documentation reset + release hygiene**: `docs/CLI_REFERENCE.md` regenerated from the live parser (was stale v11.5 and corrupted), `docs/CAPABILITY_MATRIX.md` rebuilt with one status per capability (REAL / SIMULATED / OPTIONAL / PHYSICAL TEST REQUIRED / NOT AVAILABLE), README first-run-menu description corrected to the real 10-item menu, unwired-feature claims removed, `WINDOWS_REAL_WORLD_TEST.md` extended with Academy / Lab / agent E-stop drill / profile / two-hand steps, CHANGELOG + VERIFICATION_REPORT refreshed (suite **1556 passed / 2 skipped / 0 failed**). No behaviour change vs v15.2.0. | `README.md`, `docs/CLI_REFERENCE.md`, `docs/CAPABILITY_MATRIX.md`, `CHANGELOG.md`, `VERIFICATION_REPORT.md`, `WINDOWS_REAL_WORLD_TEST.md` |
| **v16.5.0** | **Adaptive Multimodal Intelligence** (this release): `teacher.py` (multi-track `teach`/`learn` with the persisted NEW→…→COMPLETE onboarding ladder in `profile/onboarding.json`, TTY-gated first-run offer that can never trap you, self-diagnostic panel, adaptive practice, nothing physical ever auto-passed) · Voice Academy (4 levels: grammar, natural language, dictation formatting, personal voice learning) + gaze personalization from the 5-lesson Gaze Academy · `voice_stack.py` honest provider panel + pluggable local ASR (Vosk/Whisper/PocketSphinx, OPTIONAL) + `transcribe` live session (bounded history, explicit text-only saves to `<home>/transcripts/`) · `temporal.py` deterministic gesture recognizer + pinch lifecycle + composition proposals + robustness toolkit, running in the live app as a PROPOSAL-ONLY observer with `TMP:`/`SENSOR:` HUD badges (spine stays the sole dispatcher) · `profile_store.py` personal interaction profile + LearningLoop (PREDICTION ≠ EXECUTION) · `help_registry.py` real-data `help-me` · privacy manifest 20 → **24** entries · READY panel + `teach_auto`/`ready_panel` config keys · `verify` grows to 12 automated checks. Suite **1889 passed / 2 skipped / 0 failed**. | `teacher.py`, `voice_academy.py`, `voice_stack.py`, `transcribe_session.py`, `temporal.py`, `gaze_academy.py`, `profile_store.py`, `help_registry.py`, `__main__.py`, `verify.py`, `config.py`, `privacy.py` |

Full map with chapter references: `docs/V15_ARCHITECTURE.md`. Protocol:
`docs/AIP_SPEC.md`. SDKs: `docs/AGENT_SDK.md`. Multi-agent:
`docs/MULTI_AGENT.md`. Skills + marketplace: `docs/SKILLS.md`.
CLI surface: `docs/CLI_REFERENCE.md`.
Quickstart: `docs/DEVELOPER_GUIDE.md`. User guide: `docs/USER_GUIDE.md`.

## Installation

Quickstart (Windows CMD, PowerShell, macOS Terminal or Linux shell):

```
python -m pip install airmouse
airmouse setup
airmouse doctor
airmouse teach      # 3–5 min interactive tour (headless = honest plan)
airmouse test --guided
```

- `setup` checks your system and (only with your consent, in an
  interactive terminal) installs missing core packages.
- `doctor` prints a component-by-component health report with plain
  fixes; it exits 0 READY / 1 PARTIAL / 2 BLOCKED.
- `test --guided` walks you through the 12-test hardware laboratory on
  YOUR machine.

Optional extras:

```
python -m pip install "airmouse[voice]"   # SpeechRecognition + pyaudio (mic voice control)
python -m pip install "airmouse[tts]"     # spoken confirmations (pyttsx3)
python -m pip install "airmouse[ocr]"     # screen OCR targets (opt-in, off by default)
python -m pip install "airmouse[sound]"   # sounddevice microphone probing

# optional offline ASR engines (any one enables real mic → text offline):
python -m pip install pocketsphinx        # or: vosk, or: openai-whisper
```

Install from source (this repository):

```
cd airmouse_pkg
python -m pip install .
```

If `airmouse` is not on your PATH, use `python -m airmouse <command>`
everywhere in this README.

## Automatic setup

`airmouse setup` runs 11 fixed steps (environment, core packages,
local storage, configuration, cameras, microphones, browsers, optional
voice extras, keyboard/mouse access, smoke test, finish marker):

- In an **interactive** terminal it asks once:
  `Install missing required packages? [Y/N]` — nothing is installed
  without consent, and the only install command it ever runs is
  `python -m pip install <package>`.
- **Non-interactively it never installs** — it probes and reports.
- It always ends with a report and a plain-language
  "What remains to test (needs you + hardware)" section.
- Completion writes the marker `<home>/.setup_complete`.

Real run captured on a headless Linux sandbox (no camera, no mic, no
display — hardware rows honestly show ACTION_REQUIRED):

```
AirMouse setup report
============================================================
 1. [DONE           ] Environment — Python 3.12.14 on Linux x86_64; core deps 4/4 present
 2. [DONE           ] Core packages — all 4 core dependencies present
 3. [DONE           ] Local storage — storage ready; initialised stores: twin, vocabulary, skills, workflows, preferences
 4. [DONE           ] Configuration — created default config
 5. [ACTION_REQUIRED] Camera — no camera detected at index 0
      fix: connect a webcam (or enable camera access in your OS privacy settings)
 6. [ACTION_REQUIRED] Microphone — no microphone detected
      fix: connect/enable a microphone in your OS settings
 9. [ACTION_REQUIRED] Keyboard/mouse control — input automation not available here (ImportError) — expected on headless machines; this is NOT an error
      fix: on Windows it works out of the box — just run `airmouse`
11. [DONE           ] Finish — setup marker written
```

## Windows setup

Full step-by-step guide for a normal Windows 10/11 user, with a
six-field block per step (COMMAND / WHAT TO DO / WHAT SHOULD HAPPEN /
PASS / FAIL / FIX): **`WINDOWS_REAL_WORLD_TEST.md`** in the repository
root. Short version:

1. Open Command Prompt (Start → type `cmd`).
2. `python -m pip install airmouse`
3. `airmouse setup` — answer Y only if you want it to install missing
   packages.
4. `airmouse doctor` — read the report; follow any "Fix:" lines.
5. Allow **Camera** and **Microphone** access for desktop apps:
   Windows Settings → Privacy & security → Camera / Microphone.
6. `airmouse teach` — the 3–5 minute tour (or `airmouse test
   --guided` — the 12-test laboratory).
7. `airmouse academy` — learn the gestures; `airmouse gesture-lab` —
   watch the safety gates work before you trust them.
8. For browser control, either start Chrome or Edge with
   `--remote-debugging-port=9222` yourself, or let AirMouse do it:
   `airmouse --launch-browser`.

## Hardware requirements

| Component | Required? | Used for |
|---|---|---|
| Python 3.9+ | required | everything |
| Display + keyboard/mouse | required to *control* the machine | pynput input automation (works out of the box on Windows; Linux needs a display server; macOS needs Accessibility permission) |
| Webcam | optional | hand tracking (17 poses + motion + pinch events), two-hand geometry (2 hands), gaze |
| Microphone | optional | voice commands, dictation, transcription (needs the `airmouse[voice]` extra + optionally a local ASR engine) |
| Chrome or Edge | optional | real browser control via CDP on port 9222 (auto-launched by `--launch-browser`, or start it yourself with the debug flag; a simulated browser bridge always works offline) |
| RF hardware | optional | RF modality is abstracted; there is no required hardware and none is claimed |

Everything else is stdlib. All processing is local; the intelligence
plugin is stdlib-only too.

## First-run guide

- On a **fresh machine**, running plain `airmouse` (in a terminal)
  shows the first-run menu — exactly these 10 items, in this order
  (`cli_menu.py`): `[1] Setup`, `[2] Doctor`, `[3] Guided Test`,
  `[4] Start AirMouse`, `[5] Voice`, `[6] Intelligence`, `[7] Agent`,
  `[8] Offline Test`, `[9] Safety`, `[0] Help`. There are no verify,
  privacy, memory or exit entries. The menu appears only when stdout
  is a TTY and setup was not completed before; after `airmouse setup`
  finishes it will not nag you again.
- **v16.5 first-run teaching:** on a fresh machine, plain `airmouse`
  in an interactive terminal offers the 3–5 minute tour; if your
  training is IN_PROGRESS it asks `Continue your training? [Y/n]`.
  Answering no, pressing EOF, or running without a TTY simply starts
  the app — **"Skip for now" always works** and the prompt never
  blocks a headless run. Your ladder lives in
  `<home>/profile/onboarding.json`. (The old v15.0.0 one-choice
  onboarding interview module remains unwired and NOT AVAILABLE.)
- Useful first-week commands:

```
airmouse teach             # 3–5 min interactive tour (headless = honest plan)
airmouse learn             # all academies: voice · gaze · gestures · fusion
airmouse help-me           # what can I do? (or: help-me "how do I scroll?")
airmouse doctor            # health + plain fixes (exit code 0/1/2)
airmouse self-test         # 15-component honest report (PASS/OPTIONAL/HARDWARE)
airmouse verify            # 12 automated checks + physical ACTION_REQUIRED list
airmouse test --guided     # 12-test laboratory (interactive)
airmouse academy           # learn every gesture (headless = full plan)
airmouse gesture-lab 20    # watch the safety gates work (dry-run, 20 s)
airmouse transcribe        # live text transcription session (text only)
airmouse voice-status      # honest voice provider panel
airmouse privacy           # telemetry/network/storage report + 24-artifact manifest
airmouse memory status     # local store inventory
```

## Guided testing

`airmouse test` is the **non-interactive** laboratory; `airmouse test
--guided` is the **interactive** 12-test laboratory (installation,
camera, mouse, gaze, voice, dictation, intelligence, browser, agent,
multi-agent, recovery, offline). Physical tests can NEVER auto-pass —
they need a human at the desk; simulation results are labelled
`[SIMULATION]`. Exit code is 0 unless a test FAILs.

Real run on the headless build sandbox (7/7 simulation tests PASS; all
5 physical tests honestly report ACTION_REQUIRED):

```
========================================
        AIRMouse v15 TEST REPORT
========================================
Installation        PASS
Camera              ACTION REQUIRED
Mouse               ACTION REQUIRED
Gaze                ACTION REQUIRED
Voice               ACTION REQUIRED
Dictation           ACTION REQUIRED
Intelligence        PASS
Browser             PASS
Agent               PASS
Multi-Agent         PASS
Recovery            PASS
Offline             PASS

Hardware tests:     0/5
Simulation tests:   7/7

OVERALL:             PARTIALLY VERIFIED
========================================
```

**Physical hardware validation is performed by YOU** via
`airmouse test --guided` on your machine — the step-by-step Windows
procedure is `WINDOWS_REAL_WORLD_TEST.md` in the repository root.

## Gesture usage

```
airmouse                     # hand+gesture experience: webcam hand tracking → cursor
airmouse gestures            # built-in gesture mappings + any custom sequences
airmouse --trackpad          # trackpad mode: tap=click, hold=drag, 2-finger=scroll
airmouse --calibrate         # guided 8s calibration sweep on startup
airmouse --precision         # precision mode
airmouse academy [lesson]    # Gesture Academy: 11 lessons, live or full plan
airmouse gesture-lab [sec]   # Gesture Lab: dry-run observatory, watch the gates
airmouse profile <name>      # apply one of 8 interaction profiles
airmouse profile list        # …or list them
```

While you practise, the v16.5 temporal observer adds two HUD badges:
`TMP:` shows the pinch lifecycle state (START/HOLD/MOVE/RELEASE) and
any intent proposal it wants to make, `SENSOR:` appears only when
camera health degrades (bad lighting, dropped frames). Both are
DISPLAY-ONLY — proposals never execute; the execution spine remains
the sole dispatcher.

The gesture vocabulary (counted from the code, `gestures.py`):

- **17 static poses** — pointing, pinch, peace, palm, fist, thumbs_up,
  three, pinky, gun, rock, shaka, ok, ring, six, thumbs_down, four,
  five.
- **10 motion gestures** — swipe left/right/up/down, circle cw/ccw,
  push, pull, shake, wave.
- **3 pinch events** — pinch_hold (drag start), pinch_release (drop),
  double_pinch (double click).
- **4 two-hand classes** (behind `config.two_hand` or `airmouse
  profile hands_free`) — TWO_HAND_HOLD, TWO_HAND_ZOOM, TWO_HAND_ROTATE,
  TWO_HAND_DRAG. Zoom drives a real ctrl+wheel; rotate/drag are
  detected but not yet wired to an OS action.

What the spine does with them (every action, every time):

- Pinch = click (tap) / drag (hold), peace = right-click, palm = drag,
  fist = FREEZE cursor (not right-click — peace is right-click),
  thumbs_up = double click, pinky = middle click, three = scroll mode,
  shaka = volume mode, ring = brightness mode, gun = show desktop,
  rock = minimize, six = task switcher, swipes = browser back/forward
  + scroll up/down, circles/push/pull = zoom, shake = cancel
  (drop drag / unfreeze), wave = attention cue only.
- **OK-gesture (Alt+F4) and macro replay are refused by default**
  (`close_window`/`macro_play` are DESTRUCTIVE class; enable only with
  `gesture_allow_destructive = true` in config — and think twice).
- Confidence floors: SAFE 0.45, CAUTION 0.60 (configurable via
  `gesture_min_confidence_safe` / `gesture_min_confidence_caution`);
  below the floor the action is refused, not guessed.
- thumbs_down / four / five are recognized and HUD-visible but have no
  default OS action — map them yourself via the gesture registry
  (`airmouse --gesture`, `~/.airmouse/gestures.json`) if you want.
- While two-hand geometry is engaged, single-hand actions freeze —
  exactly one owner acts.
- The hybrid One Euro + Kalman filter smooths jitter (`--no-kalman`
  disables). Camera permission is required the first time (Windows
  Settings → Privacy & security → Camera).

## Gaze usage

```
airmouse --gaze                # webcam gaze/eye tracking
airmouse --gaze-calibrate      # guided gaze→screen calibration, saves, exits
airmouse --hands-free          # eyes target, voice commands, dwell confirm
airmouse --fusion              # gaze targets + hand confirms + voice intents
```

Gaze confidence is fused with other signals; **low confidence ⇒ ASK,
never guess** (dwell or confirm explicitly). Recalibrate after moving
the webcam or changing resolution.

## Intelligence

Optional, local, stdlib-only (`airmouse --intelligence`, on by
default where available):

- **Personal Interaction Twin** — 14 fact categories; every fact
  carries source/confidence/context/timestamp/frequency/success_rate/
  provenance; learn/forget/decay/correct/export/import/reset.
- **Temporal world model** — frozen Human/Computer/Task snapshots,
  transitions, expected-vs-observed mismatch detection.
- **Explainable prediction** — "You often follow this with *type*
  (75 actions observed)"; PREDICTION ≠ EXECUTION, enforced at 5
  layers.
- **Personal vocabulary + workflow discovery + a closed
  personalization loop**: every confirmed gesture is fed to the
  local personalization model (bounded, learning-gated inside the
  plugin). Self-tuning proposals exist; applying them is a deliberate
  act, not automatic.

```
airmouse intelligence        # model status: size vs budget, lifecycle state
airmouse memory              # top learned interaction patterns
airmouse vocabulary          # learned terms + corrections
airmouse workflows           # approved workflows
airmouse twin                # inspect the twin's learned facts
airmouse skills              # versioned personal skill library
```

## Agent SDK

```python
from airmouse.agent_sdk import AirMouse

air = AirMouse()
air.connect()
air.capabilities()
air.observe()
air.execute(intent="open my research project", verify=True)
```

Every `execute` crosses the core's permission/confirmation gates — an
agent has NO way to bypass them. The same conversation works from the
standalone `airmouse-agent-core` runtime (stdlib-only, never imports
airmouse) and the dependency-free `agent-sdk-js` JavaScript/TypeScript
SDK. Docs: `docs/AGENT_SDK.md`.

## AIP + the stdio wire server

The **Agent Interaction Protocol 1.0** (`aip.py`) is the wire format
between agents and AirMouse: DISCOVER / OBSERVE / TARGET / REQUEST /
AUTHORIZE / EXECUTE / VERIFY / RESULT + STATUS (18 conversation
message types), 12 strict fail-closed JSON schemas (unknown fields
rejected), same-major version negotiation, 256 KB size caps.
Inspect locally with `airmouse protocol`; spec:
`docs/AIP_SPEC.md`.

v16 ships the missing last mile — a real transport:

```
airmouse --aip-stdio                # AIP JSON-lines on stdin/stdout (simulated endpoint)
airmouse --aip-real --aip-stdio     # EXECUTE routed through the real permission-gated ActionEngine
python -m airmouse.aip_stdio        # module entry: nothing but reply lines (for strict clients)
```

- The default endpoint is **simulated** and every result is honestly
  labelled `simulated: true/false`; EXECUTE is fail-closed until an
  explicit permission grant exists (no grants + ASK = NO).
- `--aip-real` builds the real `ActionEngine` (pynput executor) —
  still permission-gated, still fail-closed; if the executor cannot be
  built the server says so and falls back to simulated.
- Permissions carry the §15 semantics: ALLOW / DENY / ASK /
  ALLOW_ONCE (exactly one use) / ALLOW_SESSION / ALLOW_PATTERN; agent
  budgets (`max_actions`, `max_actions_per_minute`) are enforced.
- Strict JSON-lines clients (agent-core `stdio://`,
  agent-sdk-js `StdioTransport`) should target
  `python -m airmouse.aip_stdio`; the `--aip-stdio` CLI wrapper prints
  one banner line before the JSON loop (details in
  `docs/CLI_REFERENCE.md`).

## Multi-agent operation

Multiple agents can be registered with identity, priority,
capabilities and budgets. **Exclusive resource leases** prevent two
agents grabbing the mouse: the holder keeps the lease until release or
expiry, priority never steals, handoff is explicit, messaging is
data-only, and `emergency_stop_all` stops every agent and every gate
instantly. The permission hierarchy guarantees the human wins:
**E-STOP > HUMAN OVERRIDE > SAFETY POLICY > PERMISSION > AGENT >
PREDICTION**; `ASK` without a human present is `NO`.

```
airmouse agents           # registered agents
airmouse permissions      # permission decisions
airmouse status           # who/what is active
```

Docs: `docs/MULTI_AGENT.md`. HUD badge `AGENT:` always shows when an
agent is controlling the computer.

## Offline mode

```
airmouse --offline            # hard-offline mode (blocks cloud-capable features)
airmouse offline-test         # 18-check network-isolated selftest
```

The `OfflineGate` blocks `cloud_asr`, `cloud_tts`, `browser_cdp`,
`software_update` and `telemetry_upload` while the local grammar,
intelligence and memory keep working. The full offline selftest passes
18/18 with networking truly blocked. There is no cloud code path to
enable.

## Privacy

- **Telemetry OFF by default** (default hard-coded OFF in the config
  module; verified by tests) — nothing phones home.
- **Local-only storage in ONE home**: `~/.airmouse` (override with the
  `AIRMOUSE_HOME` environment variable). Every artifact AirMouse can
  create is resolved by `paths.py` — no split-brain homes. Crash-safe
  persistence: atomic writes, schema versioning, checksum with
  corruption quarantine + recovery (corrupt files are renamed, newest
  3 kept).
- **Learning stores patterns, not content:** passwords, tokens,
  credentials and credential-shaped input are refused outright;
  token-like blobs redacted. The v16.5 personal profile files under
  `<home>/profile/` hold bounded counters and parameters only — no
  raw audio, no video, no typed content, and transcripts exist only
  if YOU save them.
- **The full storage manifest is printed by `airmouse privacy`** —
  all 24 declared artifacts (config, user-learning stores/files
  including the v16.5 `academy_progress`, `onboarding_state`,
  `personal_profile` and `transcript_sessions`, the third-party hand
  model, the tutorial marker, backups, exports) with purpose, data
  type, location and live existence flags — followed by a
  PERSONALIZATION summary that ends with **"Nothing is uploaded."**
- **You control the data:**

```
airmouse privacy                        # full local-first report (+ 24-artifact manifest)
airmouse memory status                  # what exists, record counts
airmouse memory export --to mycopy.json # export (local file only)
airmouse memory reset                   # back up, then clear stores + learning artifacts
airmouse memory delete                  # delete store files + artifacts (backups kept)
```

Reset/delete cover the REAL artifacts, not just the five stores:
`intelligence/*` (memory, vocabulary, workflows, selftune,
model.bin), hand + gaze calibration, custom gestures, macros,
lecture notes, academy/onboarding/profile progress (user-learning
artifacts are backed up to `<home>/backups/` then deleted) — and
`deletion_verifies()` re-scans the home afterwards and reports CLEAN
(backups are deliberately excluded). Transcript sessions you saved
are user-owned keeps: they are listed in the manifest and you delete
them yourself (`<home>/transcripts/`).

Details: `docs/PRIVACY.md` · security posture: `docs/SECURITY.md`.

## Safety

- Global hierarchy: **E-STOP > HUMAN OVERRIDE > SAFETY POLICY >
  PERMISSION > AGENT > PREDICTION**. `[ESC]` trips the spine e-stop
  AND the agent e-stop instantly (movement freezes, agents lose
  control); `[x]` resets.
- **The execution spine classifies every gesture action** (SAFE /
  CAUTION / DESTRUCTIVE) and refuses DESTRUCTIVE by default — a
  misclassified "OK" can no longer Alt+F4 your window.
- Destructive operations require explicit confirmation — every run,
  even inside an approved plan.
- Rate limits (0.12 s per discrete action, 0.05 s scroll/zoom
  backstop), confidence floors, gates and allowlisted executors
  (32 action types; system/file ops are argv-only allowlists).
- Permission-denied failures are **never retried** by the recovery
  engine; malformed input fails closed.
- HUD badges keep you informed: `AGENT:`, `TASK:`, `CONFIRM?`,
  `RECOVER:` — if you ever see `AGENT:` and did not start an agent,
  press the e-stop.

## Troubleshooting

`airmouse doctor` is always the first step — it reports components
with plain-language "Fix:" lines and exits 0 READY / 1 PARTIAL /
2 BLOCKED.

| Symptom | Likely cause | Fix |
|---|---|---|
| `'airmouse' is not recognized` | Scripts dir not on PATH | use `python -m airmouse ...` everywhere |
| Camera rows say ACTION_REQUIRED | webcam unplugged, or Windows camera privacy | Settings → Privacy & security → Camera → allow desktop apps; then `airmouse test --guided` |
| Microphone rows say ACTION_REQUIRED | mic privacy or missing `airmouse[voice]` | Settings → Privacy & security → Microphone; `python -m pip install "airmouse[voice]"` |
| `pynput ... not supported` (Linux) | no display server / Wayland restrictions | run under X11, or use the `AIRMOUSE_*` diagnostics only |
| Config reported corrupt | crash during a write | backup is kept automatically; run `airmouse setup` to recreate defaults |
| `memory export` fails to write | path not writable / exists | `airmouse memory export --to <other-path>` |
| Browser commands do nothing | Chrome/Edge not started with CDP | run `airmouse --launch-browser`, or start Chrome with `--remote-debugging-port=9222` (see WINDOWS_REAL_WORLD_TEST.md) |
| Agent client gets "no endpoint answered status" against `--aip-stdio` | the CLI wrapper prints a banner line before the JSON loop | point strict clients at `python -m airmouse.aip_stdio` (see docs/CLI_REFERENCE.md) |
| Hand tracking model missing | first-run download not done | run AirMouse once online; the doctor states the expected model path |
| Need full detail for a bug report | — | re-run the failing command with `--debug` (redacted traceback) |

## Limitations

Honest, non-negotiable:

- **Physical hardware was NOT tested in the build environment** (a
  headless Linux sandbox): webcam, microphone, real hand tracking,
  real gaze, real browser automation and any Windows-specific runtime
  behaviour are **PHYSICAL TEST REQUIRED**. The validation procedure
  is handed to you: `WINDOWS_REAL_WORLD_TEST.md` +
  `airmouse test --guided`.
- Two-hand ROTATE and DRAG are detected (real geometry, tested) but
  **not wired to any OS action** — only two-hand ZOOM drives a real
  ctrl+wheel today. `airmouse help-me` and the teacher say so too.
- The v16.5 temporal observer PROPOSES ONLY — its composition
  proposals do not dispatch OS actions, and no parallel execution
  path exists (enforced by source-scan tests). The gesture spine
  remains the sole dispatcher.
- The one-choice onboarding interview (v15.0.0) is still **not wired
  into startup**; the v16.5 teacher (`airmouse teach` + the TTY-gated
  first-run offer) is the shipped onboarding, and the old module
  remains NOT AVAILABLE.
- `selftune_apply` is a reserved opt-in config flag; no shipped code
  path applies self-tuning proposals automatically (application
  happens only when you explicitly import a selftune bundle).
- Offline ASR engines (pocketsphinx/vosk/whisper) are adapters, not
  bundled engines; availability is always reported honestly, and
  AirMouse never installs one for you (`voice-status` shows the exact
  pip commands; you run them yourself). Command recognition is a
  deterministic grammar — it is NOT full speech recognition, and the
  voice panel says so in plain text.
- Transcription without an installed ASR engine runs on a
  deterministic simulated provider that is banner-labelled at every
  session start; sessions store text only, and only when you say
  `save`.
- RF modality: the abstraction and simulated bridge exist; **no RF
  hardware is implemented or claimed**.
- The personal model is statistics, not a neural LLM; predictions are
  bounded, explainable and never executed.
- Meeting summaries are structured from your markers — speaker
  identification is not claimed.
- Screen OCR targets require the optional `airmouse[ocr]` extra and
  are opt-in.
- fusion2 was removed from the shipped wiring (audit finding); it
  remains in the tree as an optional library and is not advertised.

## Verification status

- **1889 automated tests passed, 2 skipped (honest headless skips: no
  Chrome binary and no tesseract stack in the sandbox), 0 failed.**
  Physical hardware validation is performed by YOU via `airmouse test
  --guided` on your machine — see `WINDOWS_REAL_WORLD_TEST.md`.
- Full suite: `python -m pytest tests/ -q` → **1889 passed, 2 skipped,
  0 failed** (Python 3.12, Linux sandbox; measured on the v16.5.0
  release-candidate tree).
- `airmouse verify` (headless sandbox): 12/12 automated checks PASS
  (v16.5 adds the Teacher and Temporal checks — the Temporal check
  measured ≈293 µs/frame recognize+features); 5 physical checks
  honestly report ACTION_REQUIRED.
- `airmouse doctor` (headless sandbox): READY 32 / OPTIONAL 7 /
  HARDWARE 2 / WARNING 0 / FAILED 0 → `[READY FOR TESTING]` (41
  components across 12 sections).
- Capability-by-capability status with real captured output:
  `docs/CAPABILITY_MATRIX.md` — every capability carries exactly one
  tag: REAL / SIMULATED / OPTIONAL / PHYSICAL TEST REQUIRED /
  NOT AVAILABLE. Full report: `VERIFICATION_REPORT.md`.

## Development

```
git clone <repo>
cd airmouse_pkg && python -m pip install -e .
python -m pytest tests/ -q          # from the repo root: 1889 tests
```

- Layout: `airmouse_pkg/airmouse/` (core + `intelligence/` subpackage),
  `tests/` (repo root), `agent-core/` (stdlib-only standalone agent),
  `agent-sdk-js/` (JS/TS SDK), `docs/` (guides + architecture).
- House rules: the core stays stdlib-only and import-headless; every
  public boundary fails closed; optional subsystems are optional by
  contract; secrets are scrubbed, never echoed; error messages never
  leak tokens (`--debug` shows a redacted traceback); nothing is
  documented as available unless it is wired — see the capability
  status tags in `docs/CAPABILITY_MATRIX.md`.
- Key docs: `docs/DEVELOPER_GUIDE.md`, `docs/V15_ARCHITECTURE.md`,
  `docs/CLI_REFERENCE.md`, `docs/SKILLS.md`, `docs/AIP_SPEC.md`.

## Packaging

- `airmouse_pkg/pyproject.toml` defines the wheel: console script
  `airmouse`, extras `voice` / `tts` / `ocr` / `sound`,
  `requires-python >= 3.9`, MIT license.
- Build: `python -m pip wheel .` (or `python -m build`) from
  `airmouse_pkg/` — the wheel version must equal `__init__.py`
  (`airmouse verify` checks the match).
- `build.py` builds PyInstaller bundles (`--windows` / `--linux` /
  `--all`); it downloads the MediaPipe hand-landmarker model at build
  time for bundling (network needed only for that build step). The
  resulting Windows bundles are **PHYSICAL TEST REQUIRED** — none has
  been executed on real Windows hardware.
- The hand-tracking model is NOT bundled in the wheel; on first run
  AirMouse fetches it once (see Limitations for the offline-first-run
  consequence).

## Release process

1. Set the version in `airmouse_pkg/airmouse/__init__.py` and
   `airmouse_pkg/pyproject.toml` (they must match — `airmouse verify`
   checks this).
2. Full suite green: `python -m pytest tests/ -q` (v16.5.0
   release candidate: 1889 passed / 2 skipped / 0 failed).
3. `airmouse verify` — all automated checks PASS; physical items stay
   honestly ACTION_REQUIRED.
4. Build wheel + sdist; record checksums.
5. Update `CHANGELOG.md` and `VERIFICATION_REPORT.md` (new section at
   top; history preserved below; honesty labels maintained).
6. Tag `vX.Y.Z` (existing tags are never retagged), publish the GitHub
   release with the wheel, checksums and the verification summary.

---

License: MIT — see `LICENSE`.
