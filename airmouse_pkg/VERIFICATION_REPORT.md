# AirMouse v15.0.0 — Final Verification Report (v12.0 → v15.0)

Date: 2025-09-05 (build environment: Linux sandbox, Python 3.12.14)
Scope: v11.5.0 → v15.0.0 "Universal Human + AI Interaction Platform"
per mission spec (7 milestone versions: v12.0.0, v12.5.0, v13.0.0,
v13.5.0, v14.0.0, v14.5.0, v15.0.0). Companion documents:
`README.md`, `docs/V15_ARCHITECTURE.md`, `docs/AIP_SPEC.md`,
`docs/AGENT_SDK.md`, `docs/MULTI_AGENT.md`, `docs/SKILLS.md`,
`docs/DEVELOPER_GUIDE.md`, `docs/USER_GUIDE.md`. The full v11.5 and
v10 reports are preserved below.

---

## V1. Tests — exact numbers

```
Baseline before v12 : 786 passed, 0 failed, 0 skipped   (v11.5.0 tag)
Final v15 suite     : 1056 passed, 0 failed, 0 skipped  (Python 3.12.14, ~12 s)
```

New tests by file (measured, `pytest --collect-only`):

| File | New tests |
|---|---:|
| `tests/test_v12.py` | 39 |
| `tests/test_v13.py` | 40 |
| `tests/test_v13_5.py` | 19 |
| `tests/test_v14.py` | 33 |
| `tests/test_v14_5.py` | 34 |
| `tests/test_v15.py` | 75 |
| `tests/test_hardening_v15.py` | 30 |
| **Total new** | **270** |

**786 preserved** v10/v11.5 tests stayed green throughout; the
milestone tags **v12.0.0, v13.0.0, v13.5.0, v14.0.0, v14.5.0** were
pushed with green suites at each commit (825 → 865 → 884 → 917 → 951 →
1026 → 1056).

## V2. SIMULATION vs PHYSICAL — the honest table

| Subsystem | Status |
|---|---|
| Personal Interaction Twin (v12) | **SIMULATION-VERIFIED** |
| Temporal world model (v12.5) | **SIMULATION-VERIFIED** |
| Goals + TaskEngine (v13) | **SIMULATION-VERIFIED** |
| Skills + library (v13.5) | **SIMULATION-VERIFIED** |
| Recovery engine + target resolver (v14) | **SIMULATION-VERIFIED** |
| AIP protocol + SDKs + agent-core (v14.5) | **SIMULATION-VERIFIED** — protocol level, in-process AND stdio transports |
| Multi-agent registry + permissions + DiTM + onboarding + licensing + marketplace + simulator + failure injection + explainability + CLI + HUD (v15) | **SIMULATION-VERIFIED** |
| ALL v12–v15 subsystems, aggregate | **SIMULATION-VERIFIED ONLY — NOT PHYSICALLY VERIFIED** |
| Webcam / microphone / gaze / RF / real-Chrome-CDP / live-ASR | **NOT PHYSICALLY VERIFIED** (unchanged since v11.5) |

The §26 simulator IS the computer model for all v12→v15 verification:
deterministic (same script → same final state), bounded, no real
display. No physical hardware claim is made anywhere in the v12→v15
arc.

## V3. Security audit results (§23 + §30, executable in
`tests/test_hardening_v15.py`)

* **No `shell=True` / `eval(` / `exec(` / `os.system` / `pickle.load`
  / `yaml.load`** anywhere in the package.
* `subprocess` usage is **argv-list only** (no shell).
* **Zero network code in all 17 new v12→v15 modules** — urllib /
  requests / raw-socket connects / httpx asserted absent.
* **11 parser surfaces fuzzed with 23 hostile strings each** (§30):
  AIP message parser + action schema, goal parser, SDK execute path,
  marketplace manifests, twin import, skill import, task engine,
  permission engine, agent registry, target resolver, workflow
  importer — SQL injection, command substitution, path traversal, XSS,
  credential shapes, control characters, 5000-char blobs, wrong types.
  No crashes, no execution, no coercion: everything fails closed.
* **Telemetry default defect found + fixed**: the legacy v9
  perf-report flag collided with the §21 privacy telemetry flag;
  `telemetry_enabled` is now authoritatively False
  (`test_telemetry_structurally_off`).
* Permission hierarchy (E-STOP > HUMAN OVERRIDE > SAFETY POLICY >
  PERMISSION > AGENT > PREDICTION) holds under adversarial agents;
  `permission_denied` never retries; malformed requests fail closed.

## V4. Performance budgets (all enforced in
`tests/test_hardening_v15.py`)

| Operation | Budget |
|---|---|
| Twin learn | < 10 ms |
| World-model observe | < 10 ms |
| Task create | < 10 ms |
| AIP message parse | < 5 ms |
| Goal/intent parse | < 50 ms |
| Target resolve | < 20 ms |
| SDK execute (in-process) | < 50 ms |
| Recovery loop round | < 20 ms |
| agent-core import | < 200 ms |

## V5. Scope note

Only the numbers in sections V1–V4 are claimed. Component self-test,
offline selftest, packaging and v11.5/v10 details are unchanged from
the reports below.

---

# AirMouse v11.5.0 — Final Verification Report

Date: 2025-09-05 (build environment: Linux sandbox, Python 3.12)
Scope: v10.0.0 → v11.5.0 "Adaptive Human-Computer Intelligence" per
mission spec. Companion documents: `README.md` (user guide),
`docs/V11_5_ARCHITECTURE.md` (deep dive), `docs/SECURITY.md`,
`docs/PRIVACY.md`, `CHANGELOG.md` (history). The full v10 report is
preserved below.

---

## 1. Implementation summary

v11.5 adds 17 new modules (9 in the `intelligence/` subpackage) and
extends 4 existing ones. Line counts are real `wc -l` values on the
final tree (docstrings included). Package total: **58 Python modules /
29,646 LOC** (v10: 41 modules / 22,781 LOC ⇒ net growth ≈ +6,900
lines; `browser_extension/` assets unchanged at 235 lines).

| Module | LOC | Status | Contents |
|---|---:|---|---|
| `intelligence/model.py` | 858 | NEW | PersonalInteractionModel: NGram+ActionMarkov+Command+Emoji+FeatureWeights; AIMM artifact (magic+version); ~30 MB capacity budget; quantized; deterministic pruning |
| `intelligence/plugin.py` | 499 | NEW | IntelligencePlugin facade — never raises; 8 lifecycle states; atomic artifact persistence; validated export/import profile |
| `intelligence/workflows.py` | 500 | NEW | WorkflowDiscovery (approval-gated), WorkflowStore, WorkflowRunner (preview + destructive confirmations), ProactiveAssistant, Suggestion |
| `intelligence/memory.py` | 373 | NEW | InteractionMemory + PatternRecord schema; fail-closed sensitive-data scrubbing (is_sensitive/scrub_pattern); bounded 5,000 |
| `intelligence/personalization.py` | 349 | NEW | Gesture/Gaze/Voice profiles incl. alias learning; PersonalizationEngine |
| `intelligence/vocabulary.py` | 310 | NEW | PersonalVocabulary terms + corrections; validated import/export |
| `intelligence/prediction.py` | 271 | NEW | Predictor → explainable Prediction; EMOJI_KEYWORDS baseline |
| `intelligence/selftune.py` | 187 | NEW | SelfTuner; 10 TUNABLES with hard bands + min-samples gates |
| `intelligence/__init__.py` | 98 | NEW | IntelligenceState (8 states); AIMM magic/version/capacity constants |
| `transcription.py` | 624 | NEW | LiveTranscriptionEngine streaming pipeline; adapters; punctuation/caps/numbers/vocab chain; history/export/search; wer(); metrics |
| `modes.py` | 832 | NEW | ModeController + 6 mode phrase tables; PresentationController (generic hotkeys); Timeline/StudyTimer/Notes/SourceCapture; MeetingMode; AccessibilityProfiles (8 + custom) |
| `dictation_text.py` | 343 | NEW | VoiceTypingEngine (modes, spoken formatting, edit commands, undo/redo); TextPredictor; EmojiSuggester (30 s cooldown) |
| `fusion2.py` | 291 | NEW | FusionEngine2 (9 signals), ConflictResolver, executable policy, RFExtendedProvider + RFNoHardware |
| `selftest.py` | 233 | NEW | 15-component self-test with PASS/FAIL/OPTIONAL/HARDWARE |
| `world_model.py` | 265 | NEW | WorldModel bounded snapshot + likely intent; ContextualCommandResolver (12 families) |
| `text_control.py` | 250 | NEW | TextController — 16 TextOps, keyboard fallback, coordinate-independent |
| `privacy.py` | 193 | NEW | PrivacyDashboard/PrivacyFlags/ConnectionState; delete/reset/clear/export/import |
| `__main__.py` | 2,164 | EXTENDED | v11.5 flags + 5 subcommands + HUD badges AI:/MODE:/SUG:/transcript |
| `agent.py` | 1,045 | EXTENDED | guarded intelligence/world_model/fusion2/modes wiring; `_learn_from_report` learning events |
| `config.py` | 593 | EXTENDED | 16 v11.5 TOML sections (backward compatible) |
| `offline.py` | 423 | EXTENDED | offline selftest 13 → 18 checks |

All v11.5 modules are stdlib-only, import headless, and run with
networking disabled (proven by the 18-check offline selftest).

## 2. Tests — exact numbers

```
786 passed, 0 failed, 0 skipped            (pytest tests/ -q, 11.0 s)
```

Breakdown: **630 preserved v10/v9 tests (unchanged) + 156 new v11.5
tests (`tests/test_v115.py`, 1,790 lines, 20 sections)**. The v11.5
suite covers: §5 model (capacity/corruption/quantization), §6 memory +
scrubbing, §7 vocabulary, prediction explainability, self-tuning
bands, personalization, workflows (discovery/store/runner gates),
§4 plugin contract (disabled/corrupted/incompatible/out-of-memory/
privacy-paused), §8 transcription, §9 voice typing, §10/§11 text
prediction + emoji, §12 text control, §13/§14 world model +
contextual commands, §17–23 modes, §22 accessibility chains, §24
Fusion 2.0 (all combos + conflicts), §31 offline under real network
isolation, §32 privacy dashboard, §43 malicious input, §44 resource
limits, §34 performance budgets, §45 self-test, §46 final integration
simulations (voice→learning loop + teacher/student/office/developer/
workflow role scenarios).

## 3. Offline verification (§31) — 18/18

`run_offline_selftest()` re-run for this report: **18/18 PASS**.
The 13 v10 checks (voice grammar/command/dictation/context, custom
gesture, RF bridge, simulated browser ×3, fusion pipeline, event bus,
offline gate, final network-refused proof) are joined by 5 new checks
under the same REAL socket-level isolation
(`network_isolation()` monkeypatches `connect`/`connect_ex`/
`create_connection` to raise; loopback passthrough by default):

| # | Check | What it proves |
|---|---|---|
| 13 | `intelligence_offline` | plugin loads to `available`; learns click-after-open_app; predicts offline |
| 14 | `memory_offline` | pattern recording + scrubbing works network-free |
| 15 | `vocabulary_offline` | term/correction learning + application offline |
| 16 | `transcription_offline` | simulated streaming produces a finalized segment offline |
| 17 | `fusion2_offline` | 9-signal fusion reaches an executable click offline |
| 18 | `network_actually_blocked` | a real `socket.create_connection(("example.com", 80))` is refused |

## 4. Component self-test (§45)

`airmouse self-test` — 15 components, re-run for this report:

```
Core/Voice/Transcription/Gesture/Gaze/Browser/Fusion/Intelligence/
Memory/Prediction/Safety/Offline/Packaging ... PASS
RealLocalASR ... OPTIONAL (vosk/whisper/pocketsphinx not installed)
Camera ......... HARDWARE (webcam unavailable in headless environment)
RESULT: PASS (13 pass, 1 optional, 1 hardware, 0 fail)
```

OPTIONAL/HARDWARE are honest statuses — missing dependencies or
hardware never masquerade as failures.

## 5. Performance (§34; this environment, budgets asserted by tests)

Re-measured for this report with the exact §34 workloads (single run;
budgets asserted in `tests/test_v115.py` — all 8 budget tests pass):

| Metric | Measured | Budget |
|---|---|---|
| Prediction (`predict_next_action`) | ≈ 0.004 ms | < 50 ms |
| Emoji suggestion | ≈ 0.024 ms | < 50 ms |
| Memory record (`InteractionMemory.record`) | ≈ 0.005 ms | < 10 ms |
| Transcription tick (`feed_audio`) | ≈ 0.001 ms | < 100 ms |
| Fusion (`FusionEngine2.fuse`) | ≈ 0.008 ms | < 20 ms |
| Event bus publish | ≈ 0.002 ms | < 5 ms (test bound) |
| Model load (200-sentence artifact, 17 KB) | ≈ 3.4 ms | < 500 ms |

Model size honesty: fresh install ≈ 0.1 KB
(`airmouse intelligence` output); a 200-sentence training run
produces a **17,296-byte** artifact. The ~30 MB figure is the hard
capacity budget, not the shipped size.

v10/v9 performance numbers remain valid (v10: grammar 50 utterances
≈ 5 ms, 30 voice transcripts ≈ 3 ms, 1000 bus events ≈ 2 ms, 1000
context resolves ≈ 4 ms).

## 6. SIMULATION vs PHYSICAL hardware status — EXPLICIT

**No physical webcam, microphone, or RF hardware exists in the build
environment. Nothing below claims physical verification.**

| Area | Status | What is verified |
|---|---|---|
| All v11.5 logic (intelligence, memory, vocabulary, prediction, self-tune, workflows, transcription pipeline, dictation, text control, world model, modes, fusion2, privacy, selftest) | **SIMULATION-VERIFIED** | deterministic simulators + injected timestamps; 156 new tests, 630 preserved |
| Webcam / camera paths (hand, gaze) | **NOT PHYSICALLY VERIFIED** | no camera in sandbox; Camera = HARDWARE in self-test |
| Microphone / audio-device paths | **NOT PHYSICALLY VERIFIED** | VAD/pipeline verified with synthetic buffers; no mic in sandbox |
| Gaze behavior on real eyes | **NOT PHYSICALLY VERIFIED** | v9 targeting-aid caveat unchanged |
| RF (any hardware, incl. RFExtendedProvider protocols) | **NOT PHYSICALLY VERIFIED** | `RFNoHardware` honest default; simulated providers only |
| Real Chrome/Edge via CDP | **NOT PHYSICALLY VERIFIED** | v10 guarded implementation unchanged; graceful-unavailability tested |
| PocketSphinx / Vosk / Whisper ASR | **NOT PHYSICALLY VERIFIED** (engines not installed) | adapters present with guarded imports; **simulated streaming provider + transcript injection are the verified paths**; `detect_providers()` reports honestly |

Recommended first real-machine checks: `airmouse self-test`,
`airmouse voice-status`, `airmouse --gaze-calibrate`,
`airmouse --teacher`, `airmouse offline-test`.

## 7. Security / privacy audit (v11.5 additions)

- Grep-verified: **no `shell=True`, `eval(`, `exec(`, `os.system`**
  anywhere in the 58 package modules; subprocess remains argv-only
  with timeouts (the one `--shell` string is xdotool's own output
  flag, not a shell).
- New validation surfaces (workflow step regex, import scrubbing,
  artifact bounds, length caps) enumerated in `docs/SECURITY.md` §3.
- §43 hostile-corpus suite passes (SQLi, command substitution,
  traversal, binary junk, XSS, credential shapes → no exceptions, no
  execution, no secret persistence).
- Privacy: telemetry OFF by default; `cloud` flag structurally
  impossible (forced False in `PrivacyFlags.__post_init__`; `set()`
  refuses it); fail-closed scrubbing keeps credential-shaped input out
  of the learned store; bounded local audit log.

## 8. Regression gate & packaging

- All **630** pre-existing v10/v9 tests pass **unchanged**; v11.5
  additions are confined to `tests/test_v115.py`. Product-bug fixes
  found during development are listed in CHANGELOG "Fixed".
- `pyproject.toml` at **11.5.0**; `airmouse --version` →
  `AirMouse v11.5.0 — Adaptive Human-Computer Intelligence Edition`;
  all v11.5 flags/subcommands registered (`--help` inspection).
- Wheel build/tag/release remains a release step from this tree.

## 9. Known limitations (honest)

- **Simulation-first:** every claim above is simulation-verified;
  physical camera/mic/gaze/RF, real-Chrome CDP, and live offline-ASR
  engines are **not physically verified** (§6). Real-machine threshold
  tuning is expected (the bounded self-tuner is the intended
  mechanism).
- The personal model is a bounded statistical system, **not** a neural
  LLM; it ships empty and only knows what it observed on your machine.
- Punctuation/capitalization are deterministic heuristics, not an AI
  punctuation model (see `docs/TRANSCRIPTION_GUIDE.md` §4).
- Meeting summaries contain only what the user marked; **no speaker
  identification** is claimed or implemented.
- Presentation control uses generic hotkeys — a few actions are
  app-dependent (documented in `docs/TEACHER_GUIDE.md`).
- Learned artifacts under `~/.airmouse/intelligence/` are plaintext
  JSON/binary on local disk (scrubbed + bounded; disk encryption is
  the OS's job).

---

# AirMouse v10.0.0 — Final Verification Report (preserved)

Date: 2025-09-04 (build environment: Linux sandbox, Python 3.12.14)
Scope: v9.0.0 → v10.0.0 "Universal Offline Interaction Engine" per
mission spec. Companion documents: `README.md` (user guide),
`docs/V10_ARCHITECTURE.md` (deep dive), `CHANGELOG.md` (history).

---

## 1. Implementation summary

v10 adds 9 new modules and extends 8 existing ones. Line counts are
real `wc -l` values on the final tree (docstrings included).

| Module | LOC | Status | Contents |
|---|---:|---|---|
| `eventbus.py` | 323 | NEW | EventBus/Subscriber/MultiSubscriber, bounded queues (drop-oldest), history ring, stats |
| `voice_commands.py` | 540 | NEW | 75-command deterministic grammar, 10 namespaces, `<slot>` entities, ambiguity flags |
| `offline_voice.py` | 905 | NEW | OfflineSpeechProvider protocol + 4 providers, EnergyVAD hysteresis, wake-word gate, dictation buffer, COMMAND/DICTATION/HYBRID |
| `gesture_registry.py` | 424 | NEW | Full gesture vocabulary (v5 superset + v10 set), user-defined JSON sequence mappings, sequence matcher, double-pinch synthesis |
| `rf.py` | 228 | NEW | RFProvider protocol, Simulated/Dummy providers, RFBridge → bus; optional hardware, idles without |
| `system_actions.py` | 643 | NEW | 16 SYSTEM_OPS + 8 FILE_OPS, argv-only subprocess, allowlisted roots, sanitize_file_name, validate_url |
| `browser.py` | 1,948 | NEW | Bridge protocol + Simulated/CDP transports, target mapper, semantic resolver, action verifier, controller |
| `browser_bridge.py` | 331 | NEW | Localhost-only bridge server (127.0.0.1:17843) + MV3 extension round-trip helper |
| `offline.py` | 367 | NEW | OfflineGate, network_isolation (real socket blocking), run_offline_selftest (13 checks) |
| `browser_extension/` | 308 | NEW | MV3 extension source: manifest.json (14), background.js (101), content.js (120), README.md (73) |
| `interfaces.py` | 890 | EXTENDED | Event/EventKind(14)/VoiceMode/CommandNamespace(10)/ContextState; Modality +RF+BROWSER; IntentType 52, ActionType 32 |
| `actions.py` | 1,063 | EXTENDED | Canonical §10 vocabulary, v10 param normalization, destructive-op flags, executor injection |
| `context.py` | 364 | EXTENDED | v10 ContextEngine (browser state, gaze TTL 2 s, selection, recent action, deictic resolution) |
| `safety.py` | 487 | EXTENDED | SENSITIVE_TYPES + SHUTDOWN/RESTART/LOCK/SLEEP/CLOSE_TAB; param-level FILE_OP/SYSTEM_OP refinement |
| `hands_free.py` | 585 | EXTENDED | 8 named combos, SensorHealth, largest-alive-subset downgrade ladder |
| `agent.py` | 986 | EXTENDED | inject_intent, poll_events, context integration, v10 executor overrides |
| `__main__.py` | 1,981 | EXTENDED | v10 flags/subcommands, full wiring, HUD badges V10/CMD/RF/BROWSER/VER |
| `config.py` | 502 | EXTENDED | `[v10]` TOML section (13 keys) |

Package total: **22,781 lines** across 41 modules under `airmouse/`
(v9 report: 15,881 lines) — net v10 growth ≈ 6,900 lines.
All v10 core modules are stdlib-only and import with networking
disabled.

## 2. Tests — exact numbers

```
630 passed, 0 failed, 0 skipped, 0 xfailed   (pytest tests/ -q, 10.3 s)
```

Breakdown: **497 pre-existing v9 tests (all preserved, unchanged) +
133 new = 19 browser tests (`tests/test_browser.py`) + 114 v10 tests
(`tests/test_v10.py`)**.

New suite coverage:
- `tests/test_browser.py` (19): simulated bridge state/click/verify
  cycle, ScreenTarget mapping, target-map finders, deterministic
  semantic resolver for the §12 utterance set, action verifier,
  controller execute+verify pipeline incl. bus/context/offline-gate
  wiring, CDP never-raise/unavailable behaviour, localhost bridge
  server POST→GET round-trip
- `tests/test_v10.py` (114, 14 sections): command grammar + §7 intent
  mapping; OfflineVoiceEngine modes/VAD/wake-word/dedup/dictation/
  hybrid/provider detection; EventBus publish/poll/history/stats;
  ContextEngine incl. deictic resolution + snapshot isolation;
  GestureRegistry incl. sequences/wildcard/save-load/override; RF
  degradation; system+file executors incl. sanitize/validate_url/
  root-refusal/traversal/dry-run; SafetySystem §18 confirmations;
  hands-free combos; offline §17 incl. the real `run_offline_selftest`
  + `network_isolation`; browser verifier/resolver extras; fusion
  pipeline via InteractionAgent injection/voice/RF-confirm-retry;
  performance budgets §20/§21

All new tests are deterministic (explicit `now=` timestamps), headless
(no cv2/mediapipe/pynput), and network-free except the §17 isolation
tests, which *block* networking as the behavior under test.

## 3. Offline verification (§17)

**Methodology.** `offline.network_isolation()` monkeypatches
`socket.socket.connect`, `socket.socket.connect_ex` and
`socket.create_connection` to raise `_NetworkBlockedError` for the
duration of its body (originals restored on exit). Loopback stays
available by default (the 127.0.0.1-only browser bridge must keep
working offline); `block_localhost=True` gives the strictest mode.
Every selftest check therefore runs with network syscalls **really
refused** — any hidden network call fails loudly. A final check
attempts a real outbound connection and asserts it is refused.

`run_offline_selftest()` — **13/13 passed** (re-run for this report):

| # | Check | What it proves |
|---|---|---|
| 1 | `voice_grammar` | "open firefox" → deterministic OPEN intent, no network |
| 2 | `voice_command_mode` | engine COMMAND mode: "volume up" → VOLUME intent |
| 3 | `voice_dictation` | DICTATION mode commits buffered text ("hello there") |
| 4 | `voice_context` | "click that" resolves against the context engine's gaze target |
| 5 | `gesture_custom` | user-defined 3-step sequence fires a HOTKEY intent |
| 6 | `rf_bridge` | simulated RF provider → bus events (rf_gesture) |
| 7 | `browser_bridge` | simulated bridge produces element state offline |
| 8 | `browser_semantic` | "click the login button" resolves to click + element |
| 9 | `browser_execute_verify` | execute + before/after verification = passed |
| 10 | `fusion_pipeline` | full InteractionAgent frame: fusion → intent → action executed |
| 11 | `event_bus` | publish/poll stats flow correctly |
| 12 | `offline_gate` | engaged gate blocks `cloud_asr`, passes `local_grammar` |
| 13 | `network_actually_blocked` | a real `socket.create_connection(("example.com", 80))` is refused |

`airmouse offline-test` runs the same selftest from the CLI.

## 4. Performance (this environment; budgets enforced by tests)

| Metric | Measured | Budget |
|---|---|---|
| Command grammar, 50 utterances | ~5 ms | < 0.5 s |
| OfflineVoiceEngine, 30 transcripts | ~3 ms | < 0.5 s |
| EventBus, 1000 publishes | ~2 ms | < 0.5 s |
| ContextEngine, 1000 resolves | ~4 ms | < 0.2 s |

Measured with wall-clock timing around the exact test-suite workloads
(single run, this sandbox; budgets are asserted in
`tests/test_v10.py` — all pass with ≥ 50× headroom).

v9 performance numbers remain valid and unchanged (hand filter
0.040 ms/call, gaze filter+map 0.011 ms, full agent tick 0.032 ms,
NL parse 0.0055 ms — see the v9 report sections preserved in git).

## 5. Security / privacy audit summary

- **No `shell=True` anywhere** (re-scanned for v10): all subprocess
  calls are fixed-argv lists with timeouts; user-derived strings are
  passed as arguments, never interpolated into a shell line.
- **Allowlists enforced:** 16 SYSTEM_OPS + 8 FILE_OPS are the only
  system/file operations that execute; file paths must resolve inside
  allowlisted base roots (traversal and outside-root paths refused);
  `sanitize_file_name` strips hostile components; `validate_url`
  accepts http/https/file schemes only.
- **Browser untrusted-content policy:** page-derived text is DATA used
  only for matching against our fixed template grammar — it can never
  become a command; the CDP adapter evaluates only fixed JavaScript
  snippets built from our own parameters (JSON-encoded); the bridge
  server parses payloads with `json.loads` and stores them as plain
  dicts; oversized (> 256 KB) or invalid payloads rejected (413/400);
  the server is hard-bound to 127.0.0.1 (never 0.0.0.0).
- **Offline gate** can hard-block cloud ASR/TTS/CDP/update/telemetry
  paths; §3 proves the stack runs with sockets actually refused.
- **Safety intact:** e-stop latch, sliding-window rate limiter, click
  cooldowns, confidence gates, one-shot confirmation flow, and the
  no-auto-retry rule for sensitive/blocked actions are all covered by
  both the preserved v8/v9 suites and the new §18 tests; destructive
  ops (shutdown/restart/lock/sleep/close_tab/destructive
  FILE_OP/SYSTEM_OP params) require explicit confirmation.
- No `eval`/`exec`; no secrets/tokens in tracked files (scanned);
  OCR remains opt-in; no outbound telemetry. The extension masks
  password fields and only talks to 127.0.0.1:17843.

## 6. Hardware verification status — EXPLICIT

**No physical webcam, microphone, or RF hardware exists in the build
environment. Nothing below claims physical verification.**

| Area | Status | What is verified |
|---|---|---|
| All v10 logic | **SIMULATION-VERIFIED** | deterministic simulators: scripted transcripts, scripted RF events, simulated browser bridge with per-tab history, simulated providers — 630 tests |
| Camera / eye-tracking paths on real hardware | **HARDWARE-UNVERIFIED** | MediaPipe FaceMesh smoke (blank frame) from v9 remains true; no camera in sandbox |
| Microphone / audio-device paths on real hardware | **HARDWARE-UNVERIFIED** | VAD/wake-word/dictation logic verified with synthetic buffers; no mic in sandbox |
| PocketSphinx / Vosk / Whisper offline ASR | **CODE PRESENT, UNVERIFIED LIVE** | guarded imports + `detect_providers()` auto-detection; in the sandbox none are installed (`pocketsphinx/vosk/whisper: False`), so the **transcript-injection path is what is verified** |
| CDP bridge against a real Chrome/Edge | **UNVERIFIED** | guarded, stdlib-only implementation; tests cover graceful unavailability only |
| MV3 extension loaded in a real browser | **UNVERIFIED** | source ships and is lint-reviewed; bridge-server round-trip verified with a plain HTTP client |
| RF hardware | **UNVERIFIED** | provider protocol + simulated/dummy providers verified; hardware idles cleanly by design |

Recommended first real-machine checks: `airmouse voice-status`
(which ASR engines are detected), `airmouse --gaze-calibrate`,
`airmouse --browser` with a CDP-enabled Chrome, and
`airmouse offline-test`.

## 7. Packaging

- `pyproject.toml` at version **10.0.0**; package metadata, entry
  point (`airmouse …`) and long-description unchanged in shape; the
  wheel now additionally ships the 9 new v10 modules and the
  `browser_extension/` source.
- Module inventory verified: 41 modules under `airmouse/`, all
  importable headless with networking disabled (stdlib-only core).
- `airmouse --version` → `10.0.0 — Universal Offline Interaction
  Edition`; all v10 flags and subcommands registered
  (`--help` inspection).
- `browser_extension/` ships with its own README (unpacked-load
  instructions for chrome://extensions).
- Wheel build/release (tag, GitHub Release) remains a release step,
  to be performed from a tree with this report.

## 8. Regression gate

- All **497** pre-existing v9 tests pass **unchanged** — zero
  modifications to existing test files; v10 additions are confined to
  two new files (`tests/test_browser.py`, `tests/test_v10.py`).
- v5 behaviors preserved and re-proven by the regression suite: 14
  gestures + swipes, hybrid One Euro + Kalman, 30 voice commands +
  turbo, pinch-zoom, adaptive calibration, v1 macros, HUD.
- v9 behaviors preserved: fusion modes, gaze stack, screen
  perception, intent/action/verification/safety, macros v2, NL
  control, hands-free, agent E2E, failure modes (camera loss, face
  lost, mic loss → SAFE_MODE), performance benchmarks.
- Product-bug fixes (see CHANGELOG "Fixed") were made *after* the new
  suite exposed them; their tests moved from xfail to passing, giving
  the final 630/0/0/0.

## 9. Known limitations (honest)

- **Simulation-first:** every claim above is simulation-verified;
  physical webcam/mic/RF behavior, live offline-ASR engines, real-Chrome
  CDP, and the MV3 extension in a real browser are **unverified**
  (§6). The code is guarded and degrades gracefully, but real-hardware
  tuning (e.g. `gaze_min_confidence`, VAD thresholds) is expected.
- Webcam gaze accuracy (~1–3°) remains a targeting aid (v9 design,
  unchanged); confirmation patterns compensate.
- Accessibility/DOM providers remain platform-dependent best-effort;
  the geometry fallback always works.
- The command grammar is intentionally a fixed template grammar (75
  commands) — not free-form NLU; the v9 NL pattern table remains the
  second local layer. No LLM, by design.
- RF sensing requires third-party hardware implementing the provider
  protocol; without it the modality simply does not exist.
- `airmouse_simple.py` intentionally remains the v5 single-file mode.
