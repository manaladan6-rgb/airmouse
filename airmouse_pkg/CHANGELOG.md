# Changelog

## v15.1.1 — P0 STARTUP REPAIR

Foundation repair release: fixes the three P0 defects found by the
full-spectrum audit (airmouse_pkg/docs/AUDIT_REPORT_v15.1.0.md). No
features removed; behavior otherwise unchanged.

- FIX (P0): `airmouse --voice` crashed at startup with UnboundLocalError
  (voice_engine10 read at __main__.py:1214 before its assignment). The
  v10 offline engine handle is now declared before the v5 fallback
  block, and when the v10 engine activates a running v5 cloud engine is
  stopped — exactly one voice owner. The live microphone path works
  again.
- FIX (P0): the v9 gesture-ownership gate was inert (overwritten by the
  state-machine update) and crashed under `--fusion --gaze`
  (read-before-assignment). Ownership is now applied AFTER the state
  machine: in hands-free/gaze/assist modes hand gestures no longer fire
  direct mouse actions while the agent owns interaction.
- FIX (P0, CLI honesty): `airmouse memory reset/delete` now surface
  per-store failures (cleared=False / errors) and exit 1 instead of
  printing unconditional success.
- FIX: two latent NameError crashes — `Sequence` was undefined in
  modes.py:589 and target_resolver.py:146 (would crash those paths on
  first execution).
- TEST: new tests/test_startup.py — drives the REAL main() through 8
  documented startup flag combinations with stubbed hardware (bare,
  --voice, --gaze, --fusion, --voice --fusion, --gaze --fusion,
  --hands-free, --voice-mode command), plus voice-initialization and
  voice-ownership handoff assertions. Startup wiring regressions can no
  longer ship green. Suite: 1323 passed / 0 failed.

## v15.1.0 — HARDENED RELEASE (of v15.0.0)

A hardening release: **no architecture changes** — everything from
v15.0.0 and earlier is preserved unchanged. v15.1.0 adds the release
quality surface the platform needed: a guided setup wizard, an honest
health doctor, a 12-test guided laboratory, an automated verification
command, a privacy report, a user-controlled memory lifecycle, a
first-run menu, user-grade error messages, and a crash-safe
persistence layer — all verified by 180 new tests including a
red-team suite and pinned performance budgets. Full suite:
**1056 → 1236 passed, 0 failed**.

### Added — release surface (new CLI commands, all fail-closed)

- `airmouse setup` — guided wizard with 11 fixed steps (environment,
  core packages, local storage, configuration, cameras, microphones,
  browsers, optional voice extras, keyboard/mouse access, smoke test,
  finish marker). Consent-gated: the only install command it ever
  runs is `python -m pip install <package>`, and **only after an
  interactive Y/N**; non-interactive runs never install. Ends with a
  plain-language "What remains to test (needs you + hardware)"
  section and writes the `<home>/.setup_complete` marker
  (`setup_wizard.py`).
- `airmouse doctor [--verbose|--json]` — 12-section, 41-component
  health report built on a full capability detection pass
  (`capabilities.py`): SYSTEM, PYTHON, AIRMOUSE, CAMERA, MICROPHONE,
  SPEECH, INPUT, BROWSER, INTELLIGENCE, AGENT, OFFLINE, SAFETY.
  Every non-READY component ships a plain "Fix:" remediation.
  Overall verdict `[READY FOR TESTING]` / `[PARTIAL — …]` /
  `[BLOCKED]` with **exit codes 0 / 1 / 2**. Missing hardware is
  reported HARDWARE, optional extras NOT_INSTALLED, headless input
  UNAVAILABLE — statuses never masquerade as failures.
- `airmouse test` (non-interactive lab) and `airmouse test --guided`
  (interactive 12-test laboratory: installation, camera, mouse, gaze,
  voice, dictation, intelligence, browser, agent, multi-agent,
  recovery, offline). Physical tests **can NEVER auto-pass** — they
  need a human at the desk and report ACTION_REQUIRED; simulation
  results are labelled `[SIMULATION]`. Exit code 0 unless a test
  FAILs (`guided_test.py`).
- `airmouse verify` — 10 real automated checks (core import, voice
  determinism, intelligence roundtrip, safety gates, 18-check offline
  selftest, simulated browser, permission deny-by-default, lease
  conflict refusal, AIP malformed-envelope rejection, packaging
  version match) + the 5-item PHYSICAL list, always ACTION_REQUIRED,
  ending with the `airmouse test --guided` pointer (`verify.py`).
- `airmouse privacy` — local-first report: telemetry state (OFF by
  default, verified against the code default), network posture,
  storage inventory, learned-data summary (content never included),
  model state, and the full control list; `--json` supported.
- `airmouse memory status|export|reset|delete` — user-controlled
  lifecycle over the five local stores (twin, vocabulary, skills,
  workflows, preferences): status inventory, `export --to <path>`
  (local file only), reset (backs up, then clears) and delete (files
  removed, backups kept). Reset/delete are consent-gated and
  fail-closed on non-TTY.
- **First-run menu** — plain `airmouse` on a fresh machine offers the
  10-option menu (setup / doctor / guided test / verify / privacy /
  memory / info / start / exit); TTY-only, suppressed once setup
  completes (`cli_menu.py`).

### Added — persistence + CLI quality

- `persistence.py` — crash-safe local persistence: atomic writes
  (temp file + fsync + `os.replace`), schema-versioned envelopes with
  SHA-256 checksums, corruption quarantine (corrupt store renamed to
  `<name>.json.corrupt-<epoch>`, newest 3 kept) and clean recovery to
  empty, ascending migrations, `AIRMOUSE_HOME` environment override
  for the storage home, and the memory lifecycle facades behind it.
- `user_errors.py` — user-grade error messages (title / reason /
  numbered fixes / hint, always ending with the `airmouse doctor`
  pointer), a guarded CLI wrapper (no tracebacks on screen by
  default; `--debug` prints a **redacted** traceback), and message
  sanitisation that strips GitHub/API/bearer-style tokens, env secret
  assignments and home-path prefixes.
- CLI performance budgets pinned in `tests/
  test_release_perf.py` (measured on the Linux sandbox, then pinned
  with headroom: `--version` ≈ 1.4-1.5 s measured / 6.0 s budget,
  `doctor` ≈ 2.6 s / 12.0 s, `verify` ≈ 1.6 s / 8.0 s, `test` ≈ 1.6 s
  / 8.0 s).
- Red-team suite: token-leak scans, exception-echo checks, consent
  gating (no subprocess without consent), path-traversal rejection in
  persistence/export, checksum tampering, fail-closed non-interactive
  confirmations.

### Fixed — release-quality defects found during hardening

- Stale packaging metadata: `pyproject.toml` description still said
  v10; the `--help` banner still said "v5.0.0" — both now derive from
  `__version__` (15.1.0). `airmouse verify` now checks that the
  pyproject version and the package version match.
- KeyboardInterrupt during CLI startup crashed with a traceback —
  now handled by the guarded CLI (exit 130).
- Doctor verdict wording: a report with WARNINGs but no FAILED items
  now correctly says "[PARTIAL — review WARNING items]".
- Config defect (v15 hardening pass): a legacy v9 performance-report
  flag could collide with the privacy telemetry setting — telemetry
  now stays authoritatively OFF (default in code, test-enforced).

### Tests

- 1056 (v15.0.0 baseline) → **1236 passed / 0 failed / 0 skipped**
  (+180): +33 CLI quality (`test_cli_quality.py`), +46
  setup/persistence/privacy (`test_setup_persistence.py`), +42
  doctor/capabilities/verify (`test_doctor.py`), +54 guided-test
  laboratory (`test_guided_test.py`), +5 release performance budgets
  (`test_release_perf.py`). Hardening/red-team assertions
  (token-leak scans, consent gating, fail-closed confirmations) are
  built into these suites. All 1056 pre-existing tests preserved
  green.

## v15.0.0 — UNIVERSAL HUMAN + AI INTERACTION PLATFORM (v12.0 → v15.0)

The defining evolution: the deterministic interaction core opens a
DOCUMENTED, PERMISSION-AWARE boundary for AI agents — AIP 1.0, Python +
JS SDKs, a standalone stdlib-only agent core, multi-agent
infrastructure under a global control hierarchy — plus goals, tasks,
skills, self-healing recovery, universal target resolution, DO IT WITH
ME, one-choice onboarding, transparent local licensing, a marketplace
foundation, a deterministic simulator, failure injection and
six-question explainability. Humans and agents share ONE interaction
layer; agents get no bypass pathway.

### Added — v12 Personal Interaction Twin (`intelligence/twin/`)
- `twin.py`: `PersonalInteractionTwin` — **14 fact categories**
  (preference, habit, gesture_pattern, gaze_behavior, voice_vocabulary,
  command_preference, application_preference, workflow_preference,
  confirmation_behavior, correction_behavior, timing_pattern,
  successful_action, failed_action, modality_preference); every fact
  carries **source / confidence / context / timestamp / frequency /
  success_rate / provenance** (bounded evidence list, ≤ 8 entries);
  lifecycle `learn` → `record_outcome` → `decay` (30-day half-life,
  forget below 0.05) → `forget`/`correct` → `export`/`import` →
  `reset` → `query`/`explain`; bounded 2,000 facts; secrets scrubbed
  fail-closed (GitHub-PAT/API-key/private-key/card-shaped values
  refused); **optional by contract** — the core never imports it at
  module scope; never raises through the public boundary

### Added — v12.5 temporal world model
- `world_model_temporal.py`: `TemporalWorldModel` — frozen
  `HumanState` / `ComputerState` / `TaskState` snapshot sections;
  `observe()` (partial merge + cause + cause_confidence),
  `snapshot()`/`previous()`/`history()`, `diff()` (dotted paths,
  from→to), `transitions()`, `mismatches()` + `expect()` (expected-vs-
  observed detection), `explain()`, `predict_state()` (**PREDICTION
  ONLY — never a permission**); bounded history **128**; snapshots out
  are immutable deep copies

### Added — v13 goals + tasks
- `goals.py`: `GoalHierarchyParser` — deterministic
  **COMMAND → INTENT → TASK → GOAL** classification with risk tables
  (none/low/medium/high/destructive), required permissions and
  required confirmations per objective; optional `interpreter`
  adapter for low-confidence results, output labelled
  `parsed_by="intelligence_adapter"`, bounded to the same levels, can
  NEVER downgrade risk or set `execution_allowed` (always `False`);
  **PREDICTION ≠ PERMISSION ≠ EXECUTION**
- `tasks.py`: `TaskEngine` — bounded (100 tasks × 64 steps × 8
  dependencies/step), 10 task states, structured `TaskStep`
  (objective/action/target/preconditions/expected/verification/risk/
  permission/timeout ≤ 600 s/bounded `RetryPolicy` ≤ 3 attempts),
  DAG `ready_steps`/`begin_step`/`complete_step`/
  `record_verification`/`retry_step`, **destructive steps require
  explicit human approval** (`PENDING_APPROVAL` until `approve()`),
  `checkpoint()`/`rollback()` restore task/step STATE only (never
  external side effects), bounded audit ring (200)

### Added — v13.5 skills
- `skills.py`: `InteractionCompression` — sequence clustering by
  **action-template signature** (action names + target KINDS;
  coordinates/values deliberately ignored); a skill proposal requires
  **≥ 3 repetitions + avg confidence ≥ 0.6 + user notification +
  preview + approval — NEVER silent**; `PersonalSkillLibrary` —
  100 skills × 24 steps, versioned `edit()`, enable/disable,
  `record_use`, validated export/import, revoke; targets are
  semantic/accessibility/DOM/OCR/visual, raw **coordinates only as an
  explicit flagged fallback** (`coordinate_fallback`, default False)

### Added — v14 recovery + target resolution
- `recovery2.py`: `RecoveryEngine` — full loop
  **PRECONDITION → EXECUTE → OBSERVE → VERIFY → RECOVER** on top of
  (never replacing) the v10 RecoveryManager; 7 strategies
  (retry / reobserve / retarget / alternate_modality /
  alternate_semantic_target / alternate_execution / request_human) +
  safe GIVE_UP; **14 failure diagnoses** with deterministic ladders;
  hard round cap (6) + bounded retries; **safety gate consulted before
  every execution**; no silent privilege escalation; destructive
  recovery needs a confirm hook (absent ⇒ no destructive recovery);
  every round appends an explainable trace entry
- `target_resolver.py`: `UniversalTargetResolver` — one resolver for
  humans AND agents; 7-provider chain **accessibility → DOM →
  semantic_app_api → OCR → vision → geometry → coordinate**;
  the coordinate link runs only behind the explicit
  `allow_coordinate_fallback` flag; `resolve_target()` /
  `explain_target()` / `verify_target()` with per-provider attempts

### Added — v14.5 AIP protocol + SDKs + agent core
- `aip.py`: **AirMouse Interaction Protocol (AIP) 1.0** — concepts
  DISCOVER / OBSERVE / TARGET / REQUEST / AUTHORIZE / EXECUTE /
  VERIFY / RESULT; **18 conversation message types + STATUS**; **12
  JSON schemas** (capability, observation, target, intent, action,
  task, permission, confirmation, verification, error, recovery,
  result) validated by a small strict validator — **unknown fields
  REJECTED, fail-closed**; version negotiation (same-major required);
  deterministic capability discovery; **256 KB** message caps;
  envelope `aip_version/type/id/agent_id/request_id/ts/payload`
- `agent_sdk.py`: `AirMouse` SDK (§10) —
  `connect/capabilities/observe/targets/execute/verify/task/stop/
  status` over an in-process `AipEndpoint` backed by the shared
  engines; **permission gate on every execute**; without a gate the
  endpoint fails closed to ASK ⇒ denied; `stop` revokes agent control
- `agent-core/` (standalone `airmouse-agent-core` 1.0.0, §11):
  stdlib-only, lazy, **never imports airmouse** (≈ 10 KB source,
  3 files), AIP over in-process handler / `.send()` transport /
  **stdio JSON-lines** (lazy subprocess); `AipError` for protocol
  errors; `airmouse-agent --version` smoke CLI
- `agent-sdk-js/` (`airmouse-agent` 1.0.0, §11): **dependency-free**
  JS/TS SDK with the identical primitive surface
  (`InProcessTransport` / `StdioTransport`, Promises, `AipError`)

### Added — v15 agents, permissions, experience, platform, testability
- `agents.py`: `AgentRegistry` (§12) — identity / priority (1..9) /
  capabilities / budgets / audit for up to 32 agents; **exclusive
  resource leases** (TTL 30 s default ≤ 300 s); deterministic conflict
  resolution — **the holder keeps the resource until release or
  expiry; priority NEVER steals a live lease** (challenger waits);
  `handoff` (release + reacquire + notify); agent-to-agent messages
  are **DATA only** (info/handoff/result/question — never executed,
  never parsed as instructions); human override (`suspend_agent` /
  `stop_agent`) and `emergency_stop_all()` (stops every agent,
  releases every lease, latches permission E-STOP)
- `permissions.py`: `AgentPermissionEngine` (§14/§15) — global
  hierarchy **E-STOP > HUMAN OVERRIDE > SAFETY POLICY > PERMISSION >
  AGENT > PREDICTION** (nothing may reorder it); 12 granular keys
  (`observe.screen` … `destructive.action`); decisions
  **ALLOW / DENY / ASK / ALLOW_ONCE / ALLOW_SESSION / ALLOW_PATTERN**;
  **ASK without a human == NO** and no-rule defaults to ASK (fail
  closed); deterministic most-specific-wins rule matching;
  `explain_decision()` §24 trace
- `ditm.py`: **DO IT WITH ME** (§16) — goal → structured proposal
  {OBJECTIVE, PLAN, SOURCES, CURRENT STATE, RISKS, REQUIRED ACTIONS,
  APPROVAL STATE} → user verbs **START / EDIT PLAN / PAUSE / STOP /
  CHANGE DIRECTION** → verified execution with progress reporting →
  corrections learned (Twin optional); destructive plans stay
  PENDING_APPROVAL inside the TaskEngine
- `onboarding.py`: **one-choice progressive onboarding** (§17:
  voice / hands / eyes / keyboard / automatic / all → minimal safe
  profile, usable immediately, preferences learned quietly) +
  **§18 accessibility posture** for 8 modes (voice-only, gesture-only,
  gaze-only, keyboard-only, switch-access, hybrid, hands-free,
  low-mobility) with configurable confirmation per mode and
  large-UI / high-contrast / reduced-motion as architectural flags
- `licensing.py`: **transparent local licensing** (§19) — tiers
  FREE / PRO / DEVELOPER / ENTERPRISE / SDK / MARKETPLACE / HARDWARE;
  **FREE tier = the complete local core** (21 `CORE_FEATURES`);
  higher tiers add, never subtract; revocable; inspectable state with
  `local_only: True`, `phones_home: False`, `dark_patterns: "none"`;
  no phone-home code exists
- `marketplace.py`: skill marketplace foundation (§20) —
  `SkillManifest` validation **fail-closed** (unknown fields rejected,
  semver versions, bounded permission lists, risk levels
  none/low/medium/high/destructive); install / enable / disable /
  update (strict semver supersession; risk escalation requires
  reinstall) / rollback / remove / inspect; **high/destructive risk
  requires explicit human trust** (`trusted_by_human`); **NO
  code-execution path — manifests are DATA** (only pre-vetted action
  names from the unified vocabulary)
- `simulator.py`: **deterministic virtual computer** (§26) — windows /
  tabs / buttons / forms / files / clipboard / navigation /
  UI changes / failure modes; same script → same final state; also the
  developers' "fake computer environment" (§25)
- `failure_injection.py`: **12 failure classes** (§27: missing_target,
  moved_button, closed_window, stale_dom, ocr_failure,
  accessibility_failure, network_failure, permission_denial, timeout,
  app_crash, agent_conflict, malformed_request) driven through the
  recovery loop — OBSERVE → DIAGNOSE → RECOVER → VERIFY or safe stop;
  permission / conflict / malformed classes never retry
- `explain.py`: **six question traces** (§24) — why-predicted,
  why-target, why-confirmation, why-failed, why-recovery,
  which-preference — structured, bounded and **sensitive-data-free**
  (categorical labels and lengths, never content), composable via
  `decision_trace()`
- `__main__.py`: **v15 subcommands** (§32)
  `status / capabilities / observe / world / twin / skills / agents /
  permissions / tasks / protocol / benchmark` (print + exit, all
  local); **v15 HUD badges** (§33) `AGENT:` (an AI agent is
  controlling the computer), `TASK:`, `CONFIRM?`, `RECOVER:`
- Tests: `test_v12.py` (39), `test_v13.py` (40), `test_v13_5.py` (19),
  `test_v14.py` (33), `test_v14_5.py` (34), `test_v15.py` (75),
  `test_hardening_v15.py` (30) — **270 new tests**, 786 preserved

### Changed
- `pyproject.toml` / `__init__.py` / CLI banner: version 11.5.0 →
  **15.0.0** ("Universal Human + AI Interaction Platform")
- `config.py`: v9 perf-report flag decoupled from the privacy telemetry
  flag (see Fixed); all v11.5 sections preserved backward-compatibly
- Docs: `docs/V15_ARCHITECTURE.md`, `docs/AIP_SPEC.md`,
  `docs/AGENT_SDK.md`, `docs/MULTI_AGENT.md`, `docs/SKILLS.md`,
  `docs/DEVELOPER_GUIDE.md`, `docs/USER_GUIDE.md` (new); README +
  VERIFICATION_REPORT updated (prior content preserved)

### Security
- **§23 executable audit** (`tests/test_hardening_v15.py`): no
  `shell=True` / `eval(` / `exec(` / `os.system` / `pickle.load` /
  `yaml.load` anywhere in the package; subprocess argv-list only;
  **zero network code in all 17 new v12→v15 modules** (urllib /
  requests / raw sockets / httpx asserted absent)
- **§30 adversarial fuzz**: 11 parser surfaces (AIP message parser +
  action schema, goal parser, SDK execute, marketplace manifests, twin
  import, skill import, task engine, permission engine, agent
  registry, target resolver, workflow importer) each driven with 23
  hostile strings — SQL injection, command substitution, path
  traversal, XSS, credential shapes, control chars, 5000-char blobs,
  wrong types — no crashes, no execution, no coercion (fail-closed)
- **AIP protocol hardening**: unknown envelope fields, unknown types,
  wrong major versions and unknown schema fields are REJECTED, never
  coerced; 256 KB caps enforced both directions; hostile strings in
  `params` are inert data (no field reaches execution); the endpoint
  fails closed to ASK==NO without a permission gate
- **Permission hierarchy holds under adversarial agents**: e-stop,
  human override, safety blocks, permission boundaries and destructive
  confirmations cannot be reordered or bypassed by any agent; denied
  leases/conflicts are audited, never preempted
- **Telemetry default defect found and fixed** (see Fixed); privacy
  lifecycle re-verified (twin inspect/forget/export/reset; skills
  revocable; everything local, no upload path)

### Fixed
- **telemetry_default defect**: the legacy v9 `[v9] telemetry_enabled`
  key (a LOCAL perf-report flag) collided with the §21 privacy
  telemetry flag and could silently enable the privacy-sensitive
  setting; `perf_report_enabled` now reads its own key first and
  `telemetry_enabled` stays authoritatively **False** (asserted by
  `test_telemetry_structurally_off`)
- v9→v11.5 regression: none — all 786 preserved tests stayed green at
  every milestone commit

### Verification
- **1056 tests passed / 0 failed / 0 skipped** (`pytest tests/ -q`,
  Python 3.12.14, ≈ 12 s) = 786 preserved + **270 new** across the
  v12→v15 suites; milestone tags **v12.0.0, v13.0.0, v13.5.0,
  v14.0.0, v14.5.0** pushed with green suites at each commit
  (825 → 865 → 884 → 917 → 951 → 1026 → 1056)
- Performance budgets enforced in `tests/test_hardening_v15.py`:
  twin learn < 10 ms, world observe < 10 ms, task create < 10 ms, AIP
  parse < 5 ms, goal parse < 50 ms, target resolve < 20 ms, SDK
  execute < 50 ms in-process, recovery round < 20 ms, agent-core
  import < 200 ms
- **SIMULATION-VERIFIED ONLY**: all v12→v15 subsystems verified in
  simulation (deterministic §26 simulator, in-process + stdio AIP);
  webcam / microphone / gaze / RF / real-Chrome-CDP / live-ASR remain
  **NOT PHYSICALLY VERIFIED** (unchanged since v11.5)

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
