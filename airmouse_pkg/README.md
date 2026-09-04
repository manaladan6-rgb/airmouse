# AirMouse v15.1.0 — UNIVERSAL HUMAN + AI INTERACTION PLATFORM

Voice + hand + gaze + RF + keyboard/mouse + screen + browser → one
**event bus** → fusion 2.0 → world model → intent → action →
verification → **learning**. Fully offline-capable: no LLM, no cloud
AI, no network required for any control path — a **personal model**
that learns your patterns locally, with prediction that can advise but
never execute — plus (v12→v15) a **temporal world model, goals,
tasks, skills, self-healing recovery, universal target resolution, the
AIP 1.0 agent protocol, SDKs for Python and JavaScript, multi-agent
infrastructure, a global permission hierarchy, DO IT WITH ME,
one-choice onboarding, transparent licensing, a marketplace
foundation, a deterministic simulator and failure injection** — and
(v15.1.0) a **hardened release surface**: `airmouse setup`,
`airmouse doctor`, `airmouse test --guided`, `airmouse verify`,
`airmouse privacy` and the `airmouse memory` lifecycle.
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
12-test guided laboratory for validating hardware on YOUR machine, and
a local memory lifecycle (export / reset / delete) that you control.

## Why it exists

- **Access and comfort** — full computer control without reaching for
  the mouse: for accessibility, for presenters, for messy desks, for
  RSI sufferers.
- **Privacy** — control paths that work with the network cable pulled
  out. Learning stores *patterns, not content*, on your disk, and you
  can export or delete it.
- **One honest pipeline for humans AND agents** — an AI agent that
  wants to click a button goes through the same world model, safety
  gates, permission engine and verification as you do. There is no
  agent bypass pathway, and E-STOP always wins.
- **Honesty over marketing** — every subsystem is labelled
  AUTOMATED-VERIFIED / SIMULATION-VERIFIED / PHYSICAL-NOT-TESTED, and
  hardware checks can never auto-pass.

## Features

- **Multimodal input:** voice commands + dictation, 14 webcam hand
  gestures, webcam gaze with calibration, screen perception, browser
  bridge, RF abstraction (no hardware required), keyboard/mouse.
- **Deterministic offline grammar:** 75 commands across 10 namespaces;
  no cloud, no LLM on the control path.
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
- **Agent platform:** AIP 1.0 protocol, Python SDK, stdlib-only
  standalone agent-core, dependency-free JS/TS SDK, multi-agent
  registry with exclusive resource leases, permission engine with the
  hierarchy E-STOP > HUMAN OVERRIDE > SAFETY POLICY > PERMISSION >
  AGENT > PREDICTION.
- **DO IT WITH ME:** you state a goal, AirMouse proposes a plan, you
  approve / edit / pause / stop / change direction.
- **v15.1.0 release surface:** `airmouse setup` (consent-gated
  wizard), `airmouse doctor [--verbose|--json]` (exit codes 0 READY /
  1 PARTIAL / 2 BLOCKED), `airmouse test` + `airmouse test --guided`
  (12-test laboratory), `airmouse verify` (automated checks + a
  physical ACTION_REQUIRED list), `airmouse privacy`, `airmouse memory
  status|export|reset|delete`, and a first-run menu (plain `airmouse`
  on a fresh machine, TTY only).
- **Interaction modes:** teacher / student / office / meeting /
  research + 8 accessibility profiles with modality fallback.
- **Crash-safe local persistence:** atomic writes, schema versioning,
  checksum with corruption quarantine and recovery.

## Architecture

```
  VOICE   HAND   GAZE   RF   KB/MOUSE   SCREEN   BROWSER
     └────────┴────────┴────────┴─────────┴───────────┘
                                   │  normalized Events
                                   ▼
              ╔══════════ EVENT BUS (local, bounded) ══════════╗
              ╚═════════════════════╦══════════════════════════╝
                        ▼                        ▼
              FUSION 2.0 (9 signals,      WORLD MODEL (temporal:
              conflict resolver, ask      app/window/targets/gaze/
              don't guess)                recent action + intent)
                        └───────────┬───────────┘
                                    ▼
          CONTEXTUAL RESOLVER · GRAMMAR · PERSONAL PREDICTION (data only)
                                    ▼
        ┌────────────── SAFETY (e-stop · rate limits · gates · ──────────────┐
        │                 destructive confirmations)                         │
        │  ▼                                                                 │
        │  OFFLINE GATE ──► ACTION ENGINE ──► VERIFICATION ──► LEARNING      │
        │                 (32 action types,   expected vs     verified       │
        │                  allowlisted        observed        events →        │
        │                  executors) +                       personal model │
        │                  RECOVERY                           (bounded)      │
        └────────────────────────────────────────────────────────────────────┘
```

The intelligence is **MODEL + MEMORY + CONTEXT + RULES + FUSION +
WORLD MODEL + VERIFICATION + LEARNING** — a compact local model with a
~30 MB capacity budget, quantized and versioned, that starts at a few
KB on a fresh install and grows with your usage. **It is not a neural
LLM**, and predictions are data for you and the intent layer — never
executions.

### Version-by-version (v12 → v15), all preserved in v15.1.0

| Version | Adds | Core modules / artifacts |
|---|---|---|
| **v12.0.0** | **Personal Interaction Twin** (14 fact categories; every fact carries source/confidence/context/timestamp/frequency/success_rate/provenance; learn/forget/decay/correct/export/import/reset/inspect/explain; optional by contract; secrets scrubbed fail-closed) + **Temporal Interaction World Model** foundation | `intelligence/twin/` |
| **v12.5.0** | **Temporal world model**: frozen HUMAN/COMPUTER/TASK sections; observe/snapshot/diff/history/transitions/explain/predict_state; expected-vs-observed mismatch detection; bounded history 128 | `world_model_temporal.py` |
| **v13.0.0** | **Goals** (COMMAND→INTENT→TASK→GOAL deterministic parser; optional labelled interpreter adapter that can never downgrade risk or enable execution) + **Tasks** (bounded TaskEngine, DAG dependencies, human approval gates, bounded retries, honest state-only rollback) | `goals.py`, `tasks.py` |
| **v13.5.0** | **Skills**: InteractionCompression (≥3 repetitions + confidence + notification + preview + approval, NEVER silent) + PersonalSkillLibrary (versioned/editable/revocable/exportable) | `skills.py` |
| **v14.0.0** | **Recovery 2** (PRECONDITION→EXECUTE→OBSERVE→VERIFY→RECOVER; 7 strategies; 14 failure diagnoses; bounded rounds) + **Universal target resolution** (7-provider chain) | `recovery2.py`, `target_resolver.py` |
| **v14.5.0** | **AIP 1.0 protocol** (18 conversation message types + STATUS; 12 strict fail-closed JSON schemas; same-major negotiation; 256 KB caps) + **Agent SDKs** + **standalone agent-core** (stdlib-only) + **JS/TS SDK** | `aip.py`, `agent_sdk.py`, `agent-core/`, `agent-sdk-js/` |
| **v15.0.0** | **Multi-agent registry** (identity/priority/capabilities/budgets/audit; exclusive resource leases; deterministic conflict resolution) · **Permission engine** (granular keys; ALLOW/DENY/ASK/ALLOW_ONCE/ALLOW_SESSION/ALLOW_PATTERN; ASK without human == NO) · **DO IT WITH ME** · one-choice onboarding + 8 accessibility modes · transparent local licensing (FREE = complete core) · marketplace foundation (fail-closed manifests, NO code-execution path) · deterministic simulator · 12-class failure injection · six-question explainability · v15 CLI subcommands · v15 HUD badges | `agents.py`, `permissions.py`, `ditm.py`, `onboarding.py`, `licensing.py`, `marketplace.py`, `simulator.py`, `failure_injection.py`, `explain.py`, `__main__.py` |
| **v15.1.0** | **Hardening release of v15.0.0** — no architecture changes; adds the release surface: `setup` (consent-gated wizard), `doctor` (--verbose/--json, exit 0/1/2), `test` + `test --guided` (12-test laboratory), `verify`, `privacy`, `memory status|export|reset|delete`, first-run menu, crash-safe `persistence.py`, user-grade error messages with token redaction, CLI performance budgets, red-team test suite. Tests 1056 → **1236** | `persistence.py`, `setup_wizard.py`, `capabilities.py`, `doctor.py`, `verify.py`, `guided_test.py`, `user_errors.py`, `cli_menu.py` |

Full map with chapter references: `docs/V15_ARCHITECTURE.md`. Protocol:
`docs/AIP_SPEC.md`. SDKs: `docs/AGENT_SDK.md`. Multi-agent:
`docs/MULTI_AGENT.md`. Skills + marketplace: `docs/SKILLS.md`.
Quickstart: `docs/DEVELOPER_GUIDE.md`. User guide: `docs/USER_GUIDE.md`.

## Installation

Quickstart (Windows CMD, PowerShell, macOS Terminal or Linux shell):

```
python -m pip install airmouse
airmouse setup
airmouse doctor
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
6. `airmouse test --guided` — the 12-test laboratory.
7. For browser control, start Chrome or Edge with
   `--remote-debugging-port=9222`.

## Hardware requirements

| Component | Required? | Used for |
|---|---|---|
| Python 3.9+ | required | everything |
| Display + keyboard/mouse | required to *control* the machine | pynput input automation (works out of the box on Windows; Linux needs a display server; macOS needs Accessibility permission) |
| Webcam | optional | hand tracking (14 gestures), gaze |
| Microphone | optional | voice commands, dictation, transcription (needs the `airmouse[voice]` extra + optionally a local ASR engine) |
| Chrome or Edge | optional | real browser control via CDP on port 9222 (a simulated browser bridge always works offline) |
| RF hardware | optional | RF modality is abstracted; there is no required hardware and none is claimed |

Everything else is stdlib. All processing is local; the intelligence
plugin is stdlib-only too.

## First-run guide

- On a **fresh machine**, running plain `airmouse` (in a terminal)
  shows a 10-option menu (setup, doctor, guided test, verify,
  privacy, memory, info commands, start AirMouse, exit). The menu
  appears only when stdout is a TTY and setup was not completed
  before; after `airmouse setup` finishes it will not nag you again.
- When AirMouse itself starts, onboarding asks for **one choice** —
  voice / hands / eyes / keyboard / automatic / all — and you are
  usable immediately; everything else is learned progressively and
  stays changeable.
- Useful first-week commands:

```
airmouse doctor            # health + plain fixes (exit code 0/1/2)
airmouse self-test         # 15-component honest report (PASS/OPTIONAL/HARDWARE)
airmouse verify            # automated checks + physical ACTION_REQUIRED list
airmouse test --guided     # 12-test laboratory (interactive)
airmouse privacy           # telemetry/network/storage/controls report
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

## Voice usage

```
python -m pip install "airmouse[voice]"    # once: SpeechRecognition + pyaudio
airmouse --voice                           # mic voice control (30+ phrase grammar)
airmouse --voice-mode command              # commands only
airmouse --dictation                       # voice typing: spoken punctuation + edit commands
airmouse --voice-mode hybrid               # commands + dictation together
airmouse --transcribe                      # live transcription session (partials/finals)
airmouse voice-status                      # which providers/engines are actually available
```

- Dictation understands spoken punctuation and edit commands such as
  "delete last word", "replace X with Y", "capitalize that",
  undo/redo; personal vocabulary corrections are applied.
- **Offline ASR engines** (pocketsphinx / vosk / whisper) are optional
  and are NOT bundled — without one, mic transcription needs the
  simulated provider used in tests, and availability is always
  reported honestly by `airmouse voice-status` and `airmouse doctor`.
- If Windows blocks the microphone: Settings → Privacy & security →
  Microphone → allow desktop apps.

## Gesture usage

```
airmouse                # v5 hand+gesture experience: webcam hand tracking → cursor
airmouse gestures       # list the 14 built-in gestures + any custom sequences
airmouse --trackpad     # trackpad mode: tap=click, hold=drag, 2-finger=scroll
airmouse --calibrate    # guided 8s calibration sweep on startup
airmouse --precision    # precision mode
```

Pinch = drag/click, fist = right-click, two fingers = scroll, pinch
spread = zoom, plus macros (`--record NAME`, `--play NAME`,
`--macros`). The hybrid One Euro + Kalman filter smooths jitter
(`--no-kalman` disables). Camera permission is required the first
time (Windows Settings → Privacy & security → Camera).

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
- **Personal vocabulary + workflow discovery + bounded self-tuning.**

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

## AIP

The **Agent Interaction Protocol 1.0** (`aip.py`) is the wire format
between agents and AirMouse: DISCOVER / OBSERVE / TARGET / REQUEST /
AUTHORIZE / EXECUTE / VERIFY / RESULT + STATUS (18 conversation
message types), 12 strict fail-closed JSON schemas (unknown fields
rejected), same-major version negotiation, 256 KB size caps.
Inspect locally with `airmouse protocol`; spec:
`docs/AIP_SPEC.md`.

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
- **Local-only storage** in `~/.airmouse` (override with the
  `AIRMOUSE_HOME` environment variable). Crash-safe persistence:
  atomic writes, schema versioning, checksum with corruption
  quarantine + recovery (corrupt files are renamed, newest 3 kept).
- **Learning stores patterns, not content:** passwords, tokens,
  credentials and credential-shaped input are refused outright;
  token-like blobs redacted.
- **You control the data:**

```
airmouse privacy                        # full local-first report (also: --json)
airmouse memory status                  # what exists, record counts
airmouse memory export --to mycopy.json # export (local file only)
airmouse memory reset                   # back up, then clear all stores
airmouse memory delete                  # delete store files (backups kept)
```

Details: `docs/PRIVACY.md` · security posture: `docs/SECURITY.md`.

## Safety

- Global hierarchy: **E-STOP > HUMAN OVERRIDE > SAFETY POLICY >
  PERMISSION > AGENT > PREDICTION**. The e-stop hotkey (`x` / ESC
  path) suspends agents instantly and releases their leases.
- Destructive operations require explicit confirmation — every run,
  even inside an approved plan.
- Rate limits, gates and allowlisted executors (32 action types;
  system/file ops are argv-only allowlists).
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
| Browser commands do nothing | Chrome/Edge not started with CDP | start Chrome with `--remote-debugging-port=9222` (see WINDOWS_REAL_WORLD_TEST.md) |
| Hand tracking model missing | first-run download not done | run AirMouse once online; the doctor states the expected model path |
| Need full detail for a bug report | — | re-run the failing command with `--debug` (redacted traceback) |

## Limitations

Honest, non-negotiable:

- **Physical hardware was NOT tested in the build environment** (a
  headless Linux sandbox): webcam, microphone, real hand tracking,
  real gaze, real browser automation and any Windows-specific runtime
  behaviour are **PHYSICAL HARDWARE NOT TESTED**. The validation
  procedure is handed to you: `WINDOWS_REAL_WORLD_TEST.md` +
  `airmouse test --guided`.
- Offline ASR engines (pocketsphinx/vosk/whisper) are adapters, not
  bundled engines; availability is always reported honestly.
- RF modality: the abstraction and simulated bridge exist; **no RF
  hardware is implemented or claimed**.
- The personal model is statistics, not a neural LLM; predictions are
  bounded, explainable and never executed.
- Meeting summaries are structured from your markers — speaker
  identification is not claimed.
- Screen OCR targets require the optional `airmouse[ocr]` extra and
  are opt-in.

## Verification status

- **1236 automated tests green; simulation suite green; physical
  hardware validation is performed by YOU via `airmouse test --guided`
  on your machine — see `WINDOWS_REAL_WORLD_TEST.md`.**
- Full suite: `python -m pytest tests/ -q` → **1236 passed, 0 failed**
  (Python 3.12, Linux sandbox; 1056 at the v15.0.0 baseline + 180 new
  hardening tests).
- `airmouse verify` (headless sandbox): 10/10 automated checks PASS;
  5 physical checks honestly report ACTION_REQUIRED.
- `airmouse doctor` (headless sandbox): READY 32 / OPTIONAL 7 /
  HARDWARE 2 / WARNING 0 / FAILED 0 → `[READY FOR TESTING]`.
- Measured CLI budgets (same sandbox): `--version` ≈ 1.5 s, `doctor`
  ≈ 2.6 s, `verify` ≈ 1.6 s, `test` ≈ 1.6 s (pinned budgets in
  `tests/test_release_perf.py`: 6 / 12 / 8 / 8 s).
- Capability-by-capability status with real captured output:
  `docs/CAPABILITY_MATRIX.md`. Full report: `VERIFICATION_REPORT.md`.

## Development

```
git clone <repo>
cd airmouse_pkg && python -m pip install -e .
python -m pytest tests/ -q          # from the repo root: 1236 tests
```

- Layout: `airmouse_pkg/airmouse/` (core + `intelligence/` subpackage),
  `tests/` (repo root), `agent-core/` (stdlib-only standalone agent),
  `agent-sdk-js/` (JS/TS SDK), `docs/` (guides + architecture).
- House rules: the core stays stdlib-only and import-headless; every
  public boundary fails closed; optional subsystems are optional by
  contract; secrets are scrubbed, never echoed; error messages never
  leak tokens (`--debug` shows a redacted traceback).
- Key docs: `docs/DEVELOPER_GUIDE.md`, `docs/V15_ARCHITECTURE.md`,
  `docs/CLI_REFERENCE.md`, `docs/SKILLS.md`, `docs/AIP_SPEC.md`.

## Packaging

- `airmouse_pkg/pyproject.toml` defines the wheel: console script
  `airmouse`, extras `voice` / `tts` / `ocr` / `sound`,
  `requires-python >= 3.9`, MIT license.
- Build: `python -m pip wheel .` (or `python -m build`) from
  `airmouse_pkg/` — produces `airmouse-15.1.0-py3-none-any.whl`.
- `build.py` builds PyInstaller bundles (`--windows` / `--linux` /
  `--all`); it downloads the MediaPipe hand-landmarker model at build
  time for bundling (network needed only for that build step).
- The hand-tracking model is NOT bundled in the wheel; on first run
  AirMouse fetches it once (see Limitations for the offline-first-run
  consequence).

## Release process

1. Set the version in `airmouse_pkg/airmouse/__init__.py` and
   `airmouse_pkg/pyproject.toml` (they must match — `airmouse verify`
   checks this).
2. Full suite green: `python -m pytest tests/ -q` (v15.1.0: 1236/0).
3. `airmouse verify` — all automated checks PASS; physical items stay
   honestly ACTION_REQUIRED.
4. Build wheel + sdist; record checksums.
5. Update `CHANGELOG.md` and `VERIFICATION_REPORT.md` (new section at
   top; history preserved below; honesty labels maintained).
6. Tag `vX.Y.Z` (existing tags are never retagged), publish the GitHub
   release with the wheel, checksums and the verification summary.

---

License: MIT — see `LICENSE`.
