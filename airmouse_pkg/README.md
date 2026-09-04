# AirMouse v15.0.0 — UNIVERSAL HUMAN + AI INTERACTION PLATFORM

Voice + hand + gaze + RF + keyboard/mouse + screen + browser → one
**event bus** → fusion 2.0 → world model → intent → action →
verification → **learning**. Fully offline-capable: no LLM, no cloud
AI, no network required for any control path — a **personal model**
that learns your patterns locally, with prediction that can advise but
never execute — and now (v12→v15) a **temporal world model, goals,
tasks, skills, self-healing recovery, universal target resolution, the
AIP 1.0 agent protocol, SDKs for Python and JavaScript, multi-agent
infrastructure, a global permission hierarchy, DO IT WITH ME,
one-choice onboarding, transparent licensing, a marketplace
foundation, a deterministic simulator and failure injection**.
Humans and AI agents go through the SAME pipeline — agents get no
bypass pathway.

![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue)
![Platform](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macos-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)
![Offline](https://img.shields.io/badge/offline-capable-success)
![Local](https://img.shields.io/badge/cloud-structurally%20impossible-critical)

```
  🎤 VOICE   🖐 HAND   👁 GAZE   📡 RF   ⌨ KB/MOUSE   🖥 SCREEN   🌐 BROWSER
     └────────┴────────┴────────┴─────────┴─────────────┴───────────┘
                                   │  normalized Events
                                   ▼
              ╔══════════ EVENT BUS (local, bounded) ══════════╗
              ╚═════════════════════╦══════════════════════════╝
                        ▼                        ▼
              FUSION 2.0 (9 signals,      WORLD MODEL (app/window/
              conflict resolver, ask      targets/gaze/recent action/
              don't guess)                mode + likely intent)
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

## What's new in v12→v15

One release arc, seven milestone versions. Every row is additive —
everything from earlier versions is preserved below.

| Version | Adds | Core modules / artifacts |
|---|---|---|
| **v12.0.0** | **Personal Interaction Twin** (14 fact categories; every fact carries source/confidence/context/timestamp/frequency/success_rate/provenance; learn/forget/decay/correct/export/import/reset/inspect/explain; optional by contract; secrets scrubbed fail-closed) + **Temporal Interaction World Model** foundation | `intelligence/twin/` |
| **v12.5.0** | **Temporal world model**: frozen HUMAN/COMPUTER/TASK sections; observe/snapshot/diff/history/transitions/explain/predict_state; expected-vs-observed mismatch detection; bounded history 128 | `world_model_temporal.py` |
| **v13.0.0** | **Goals** (COMMAND→INTENT→TASK→GOAL deterministic parser; optional labelled interpreter adapter that can never downgrade risk or enable execution; PREDICTION ≠ PERMISSION ≠ EXECUTION) + **Tasks** (bounded TaskEngine, DAG dependencies, human approval gates for destructive steps, bounded retries, checkpoints with honest state-only rollback) | `goals.py`, `tasks.py` |
| **v13.5.0** | **Skills**: InteractionCompression — sequence clustering by action-template signature that ignores coordinates; ≥3 repetitions + confidence threshold + notification + preview + approval, NEVER silent; PersonalSkillLibrary — versioned/editable/revocable/exportable | `skills.py` |
| **v14.0.0** | **Recovery 2** (PRECONDITION→EXECUTE→OBSERVE→VERIFY→RECOVER; 7 strategies; 14 failure diagnoses; bounded rounds; safety gate before every execution; permission-denied never retries; malformed fails closed) + **Universal target resolution** (7-provider chain accessibility→DOM→semantic-API→OCR→vision→geometry→coordinate; coordinates only behind an explicit flag) | `recovery2.py`, `target_resolver.py` |
| **v14.5.0** | **AIP 1.0 protocol** (18 conversation message types + STATUS; 12 strict fail-closed JSON schemas; same-major negotiation; 256 KB caps) + **Agent SDKs** + **standalone agent-core** (stdlib-only, lazy, never imports airmouse) + **JS/TS SDK** (dependency-free) | `aip.py`, `agent_sdk.py`, `agent-core/`, `agent-sdk-js/` |
| **v15.0.0** | **Multi-agent registry** (identity/priority/capabilities/budgets/audit; exclusive resource leases; deterministic conflict resolution — holder keeps until release/expiry, priority never steals; handoff; data-only messaging; human override; emergency_stop_all) · **Permission engine** (E-STOP > HUMAN OVERRIDE > SAFETY POLICY > PERMISSION > AGENT > PREDICTION; granular keys; ALLOW/DENY/ASK/ALLOW_ONCE/ALLOW_SESSION/ALLOW_PATTERN; ASK without human == NO) · **DO IT WITH ME** (goal→proposal→START/EDIT/PAUSE/STOP/CHANGE_DIRECTION→progress→corrections) · **one-choice onboarding** + accessibility posture for 8 modes · **transparent local licensing** (FREE = complete core) · **marketplace foundation** (fail-closed manifests, NO code-execution path) · **deterministic simulator** · **12-class failure injection** · **six-question explainability** · **v15 CLI subcommands** · **v15 HUD badges** (`AGENT:` `TASK:` `CONFIRM?` `RECOVER:`) | `agents.py`, `permissions.py`, `ditm.py`, `onboarding.py`, `licensing.py`, `marketplace.py`, `simulator.py`, `failure_injection.py`, `explain.py`, `__main__.py` |

Full map with chapter references: `docs/V15_ARCHITECTURE.md`. Protocol:
`docs/AIP_SPEC.md`. SDKs: `docs/AGENT_SDK.md`. Multi-agent:
`docs/MULTI_AGENT.md`. Skills + marketplace: `docs/SKILLS.md`.
Quickstart: `docs/DEVELOPER_GUIDE.md`. User guide: `docs/USER_GUIDE.md`.

### The 5-line agent SDK (§10)

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
standalone `airmouse-agent-core` runtime (stdlib-only) and the
dependency-free JS SDK.

## What's New in v11.5.0

| Subsystem | What it does |
|---|---|
| **🧠 Personal intelligence plugin** | Optional `airmouse/intelligence/` subpackage: n-gram LM, action Markov, command + time-of-day habits, emoji model, personalization weights — packed into a versioned quantized artifact (`AIMM` format). **Never raises**: 8 lifecycle states (available/disabled/unavailable/corrupted/incompatible/out_of_memory/privacy_paused/learning_paused) |
| **📓 Interaction memory** | Patterns, not private content — `PatternRecord` schema with fail-closed sensitive-data scrubbing (passwords/tokens/credentials refused outright, token-like blobs redacted). Bounded at 5,000 patterns |
| **🔤 Personal vocabulary** | Learned terms + corrections ("Hydra Link" → "HydraLink"), applied in dictation & capitalization; validated import/export |
| **🔮 Explainable prediction** | `Prediction(kind, value, confidence, reason, alternatives)` — "You often follow this with *type* (75 actions observed)". **PREDICTION ≠ EXECUTION**, enforced at 5 layers |
| **🎚 Bounded self-tuning** | 10 tunables (confirm frames, dwell time, voice confidence…) adapt only within hard min/max bands and only after min-sample gates |
| **⚙️ Workflow discovery** | Repeated 3–8-step action sequences (≥ 3 repetitions) become *suggestions*; nothing runs without approval, preview-first, destructive steps confirmed every run |
| **🎙️ Live transcription** | Streaming partials/finals pipeline: mic → VAD → streaming ASR → stabilization → punctuation → capitalization → personal vocabulary → final. Bounded history, txt/json/md export, search, WER evaluator |
| **⌨️ Voice typing** | DICTATION/COMMAND/HYBRID with spoken punctuation, edit commands ("delete last word", "replace X with Y", "capitalize that", undo/redo), text prediction, rate-limited emoji suggestions (30 s cooldown, learns preferences) |
| **🖱 Universal text control** | 16 text ops (TYPE/SELECT/DELETE/REPLACE/COPY/PASTE/UNDO/REDO/CUT/MOVE/CAPITALIZE/LOWERCASE/UPPERCASE/FORMAT/NEW_LINE/NEW_PARAGRAPH) — keyboard fallback, never coordinate-dependent |
| **🌍 World model** | Bounded snapshot (application/window/targets/gaze/text field/recent action+command/mode) + explainable likely intent (destructive never surfaced) |
| **🗣 Contextual commands** | 12 deictic families — "click that", "close it", "save that"… resolved against gaze/selection/context with a deterministic confidence model; **low confidence ⇒ ASK, never guess** |
| **🎓 Six interaction modes** | `--teacher --student --office --meeting --research` (+ developer): phrase tables, lecture/meeting timelines, study timer, verbatim source capture, structured meeting summaries |
| **♿ Accessibility profiles** | 8 profiles + custom chains with modality fallback resolution — no single sensor is a mandatory point of failure |
| **🔀 Fusion 2.0** | 9 weighted signals (voice .30 / gesture .25 / gaze .25 / personal history .10 / keyboard, browser, app context, recent action, prediction .05 each); conflicts scale confidence down and force confirmation; RF-extended protocol prep (presence/motion/direction/range/velocity — honest no-hardware default) |
| **🛡 Privacy dashboard** | Learning/memory/history/vocabulary/workflow flags + delete/reset/clear/export/import; telemetry **OFF by default**; cloud **structurally impossible** (no cloud code path exists) |
| **🩺 Honest self-test** | `airmouse self-test` — 15 components, PASS/FAIL/**OPTIONAL**/**HARDWARE** statuses so missing hardware never masquerades as failure |

**Everything from v10 and earlier is preserved** — see below.

## Install

```bash
pip install airmouse-15.0.0-py3-none-any.whl

# optional extras:
pip install "airmouse[voice]"   # SpeechRecognition + pyaudio (v5 mic voice control)
pip install "airmouse[tts]"     # spoken confirmations (pyttsx3)
pip install "airmouse[ocr]"     # screen OCR targets (opt-in, off by default)

# optional offline ASR engines (any one enables real mic → text offline):
pip install pocketsphinx        # or: pip install vosk   or: pip install openai-whisper
```

Requirements: Python 3.9+. Webcam optional (hand/gaze), microphone
optional (voice), RF hardware optional. **Everything else is
stdlib.** All processing is local; the intelligence plugin is
stdlib-only too.

## Quick start

```bash
airmouse                                        # v5 hand+gesture experience (unchanged)
airmouse --intelligence                         # + personal intelligence (default on)
airmouse --dictation                            # voice typing with formatting + edit commands
airmouse --transcribe                           # live transcription session
airmouse --teacher                              # teach: slides + lecture timeline
airmouse --student                              # study: notes + timer + sources
airmouse --meeting --transcribe                 # meeting capture + structured summary
airmouse --offline                              # hard-offline mode (blocks cloud features)
airmouse --offline --voice-mode hybrid --gesture --browser --intelligence  # everything

# v11.5 info & diagnostics:
airmouse intelligence                           # plugin status: model size vs budget, patterns…
airmouse memory                                 # top learned interaction patterns
airmouse vocabulary                             # learned terms + corrections
airmouse workflows                              # approved workflows
airmouse self-test                              # 15-component honest health report

# v10 subcommands (unchanged):
airmouse commands | gestures | voice-status | browser | offline-test | diagnostics
```

Legacy flags all still work: `--voice --gaze --fusion --hands-free
--assist --gaze-calibrate --trackpad --calibrate --record NAME --play
NAME --macros --monitor N --precision …` (full list:
`docs/CLI_REFERENCE.md`).

## Interaction modes in one line each

```bash
airmouse --teacher   # "next slide", "start lecture", "mark important", "export transcript"
airmouse --student   # "take a note", "start study session", "save this source"
airmouse --office    # "start meeting", "capture task …"        (shares the meeting session)
airmouse --meeting   # "add action item", "add decision", "export transcript"
airmouse --research  # "save this source", "take a note"       (verbatim capture, never fabricates)
```

Slide control uses **generic hotkeys**, so it works with PowerPoint,
Keynote, LibreOffice, browser slides and PDF viewers. Meeting
summaries are structured from **your** markers — speaker
identification is not claimed anywhere. Mode guides:
`docs/TEACHER_GUIDE.md`, `docs/STUDENT_GUIDE.md`,
`docs/OFFICE_GUIDE.md`.

## Privacy

- **Local-first:** no cloud AI anywhere. The `cloud` privacy flag is
  structurally impossible to enable — there is no cloud code path.
- **Telemetry OFF by default**; nothing phones home.
- **Learning stores patterns, not content:** passwords, tokens,
  credentials and credential-shaped input are refused outright
  (fail-closed scrubbing); token-like blobs are redacted.
- The model is yours: `delete_learned_data()`,
  `reset_model_personalization()`, `clear_interaction_history()`,
  `export_profile()` / `import_profile()` (validated + scrubbed on
  import). Privacy mode pauses learning and history instantly.
- Transcript history is bounded, off-switchable, and exportable.
- Details: `docs/PRIVACY.md` · security posture: `docs/SECURITY.md`.

## Honest verification status

- **No physical hardware was available in the build environment.** All
  v11.5 behavior is **simulation-verified** via deterministic
  simulators (786 tests). Webcam/mic/gaze/RF paths, real-Chrome CDP,
  and real offline-ASR engines are **hardware-unverified**.
- The transcription pipeline is verified through the simulated
  streaming provider and transcript injection; vosk/whisper/
  pocketsphinx adapters exist but the engines are **not installed**
  in the sandbox — availability is always reported honestly
  (`airmouse voice-status`, `airmouse self-test`).
- The personal model **ships empty** (a few KB) and is not a neural
  LLM; its suggestions are explainable statistics with bounded
  confidence.
- Full table: `VERIFICATION_REPORT.md` §SIMULATION vs PHYSICAL.

## Verification methodology

```bash
python -m pytest tests/ -q        # 786 tests
airmouse offline-test             # 18/18 checks, networking truly blocked
airmouse self-test                # 13 pass · 1 optional · 1 hardware · 0 fail
```

786 passed, 0 failed, 0 skipped (Python 3.12, Linux sandbox):
**630** pre-existing v10 tests (all preserved) + **156** v11.5 tests.
Performance budgets (§34) pass with large headroom — e.g. prediction
≈ 0.004 ms (< 50 ms), memory record ≈ 0.005 ms (< 10 ms), fusion
≈ 0.008 ms (< 20 ms), model load ≈ 3.4 ms (< 500 ms) in this sandbox.
Deep dive: `VERIFICATION_REPORT.md`, `docs/V11_5_ARCHITECTURE.md`,
`docs/INTELLIGENCE_GUIDE.md`, `docs/TRANSCRIPTION_GUIDE.md`.

## Preserved from earlier versions

- **v10** — event bus (14 event kinds), 75-command offline grammar,
  offline voice (VAD/wake-word/COMMAND-DICTATION-HYBRID), context
  engine with deictic resolution, gesture registry + custom sequences,
  RF abstraction, 16+8 allowlisted system/file ops (argv-only), 3-layer
  browser control with verification, 8 hands-free combos with sensor
  degradation, `--offline` + 13→18-check network-isolated selftest
- **v9** — multimodal fusion, webcam gaze + calibration + filtering,
  screen understanding, intent/action/verification/recovery, safety
  system, macros v2, NL control, InteractionAgent
- **v5** — 14 hand gestures, hybrid One Euro + Kalman filter, 30-phrase
  voice control + TURBO, pinch-zoom, adaptive calibration, macros, HUD
- **v4/v3** — trackpad mode, direct/ironman tracking, precision mode,
  media keys, multi-monitor, autostart, settings GUI

## License

MIT — see `LICENSE`.
