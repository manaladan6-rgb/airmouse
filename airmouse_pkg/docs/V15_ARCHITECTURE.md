# AirMouse v15.0.0 — Architecture (v12.0 → v15.0)

Companion documents: `README.md` (overview), `docs/AIP_SPEC.md` (protocol),
`docs/AGENT_SDK.md` (agent SDKs), `docs/MULTI_AGENT.md` (§12–§14),
`docs/SKILLS.md` (§6 + §20), `docs/DEVELOPER_GUIDE.md` (quickstart),
`docs/USER_GUIDE.md` (§16/§17/§33). The v10 and v11.5 architecture
documents are preserved alongside (`V10_ARCHITECTURE.md`,
`V11_5_ARCHITECTURE.md`).

**Verification status:** every v12→v15 subsystem documented here is
**SIMULATION-VERIFIED** (deterministic tests, the §26 simulator, in-process
and stdio AIP). Webcam, microphone, gaze hardware, RF hardware, real-Chrome
CDP and live third-party ASR remain **NOT PHYSICALLY VERIFIED** — the same
honest status as v11.5.

---

## 1. The shared interaction pipeline

v12→v15 turns AirMouse from a human-interaction engine into a
**Universal Human + AI Interaction Platform**. Humans and AI agents go
through the SAME pipeline; agents get no separate pathway:

```
            ┌────────────────────────── PERCEPTION ──────────────────────────┐
            │  voice · hand · gaze · keyboard/mouse · screen · browser · RF  │
            │  (v11.5 modalities, unchanged — SIMULATION-VERIFIED ONLY)      │
            └───────────────────────────────┬────────────────────────────────┘
                                            ▼
   ┌────────────────── WORLD MODEL (temporal, v12.5 §3) ───────────────────┐
   │  HUMAN / COMPUTER / TASK sections · transitions + causality ·         │
   │  expected-vs-observed mismatch detection · bounded history (128)      │
   └───────────────────────────────┬───────────────────────────────────────┘
                                   ▼
   ┌────────────────── INTENT (v13 §4 goals hierarchy) ────────────────────┐
   │  COMMAND → INTENT → TASK → GOAL · deterministic parser · optional     │
   │  labelled interpreter adapter · PREDICTION ≠ PERMISSION ≠ EXECUTION   │
   └───────────────────────────────┬───────────────────────────────────────┘
                                   ▼
   ┌────────────────── TASK (v13 §5 TaskEngine) ───────────────────────────┐
   │  bounded tasks · DAG dependencies · human approval gates · bounded    │
   │  retries · checkpoints with honest STATE-ONLY rollback · audit ring   │
   └───────────────────────────────┬───────────────────────────────────────┘
                                   ▼
   ┌────────────────── PERMISSION (v15 §14/§15) ───────────────────────────┐
   │  E-STOP > HUMAN OVERRIDE > SAFETY POLICY > PERMISSION > AGENT >       │
   │  PREDICTION · granular keys · ALLOW/DENY/ASK/ALLOW_ONCE/              │
   │  ALLOW_SESSION/ALLOW_PATTERN · ASK without a human == NO              │
   └───────────────────────────────┬───────────────────────────────────────┘
                                   ▼
   ┌────────────────── ACTION (shared executors + §8 target resolver) ─────┐
   │  accessibility → DOM → semantic-API → OCR → vision → geometry →       │
   │  coordinate (coordinates only behind an explicit flag)                │
   └───────────────────────────────┬───────────────────────────────────────┘
                                   ▼
   ┌────────────────── VERIFICATION (evidence, not optimism) ──────────────┐
   │  expected vs observed per step · verifier contracts · AIP VERIFY      │
   └───────────────────────────────┬───────────────────────────────────────┘
                                   ▼
   ┌────────────────── RECOVERY (v14 §7) ──────────────────────────────────┐
   │  PRECONDITION → EXECUTE → OBSERVE → VERIFY → RECOVER · 7 strategies · │
   │  14 failure diagnoses · bounded rounds · safety gate before every     │
   │  execution · permission-denied NEVER retries · malformed fails closed │
   └───────────────────────────────┬───────────────────────────────────────┘
                                   ▼
   ┌────────────────── LEARNING (v12 §2 Personal Interaction Twin) ────────┐
   │  OPTIONAL by contract · patterns not content · every fact carries     │
   │  source/confidence/context/timestamp/frequency/success_rate/          │
   │  provenance · learn/forget/decay/correct/export/import/reset          │
   └────────────────────────────────────────────────────────────────────────┘
```

Around this pipeline sit the v15 platform layers:

* **Agent boundary (§9–§11):** AIP 1.0 protocol, Python SDK, standalone
  `airmouse-agent-core` runtime, dependency-free JS/TS SDK.
* **Multi-agent infrastructure (§12–§14):** registry, leases, conflict
  rules, handoff, data-only messaging, human override, e-stop.
* **Experience (§16–§18):** DO IT WITH ME, one-choice onboarding,
  accessibility-as-architecture.
* **Platform (§19–§20):** transparent local licensing, skill marketplace
  foundation (manifests are DATA).
* **Testability (§24–§27):** six-question explainability, deterministic
  simulator, 12-class failure injection.
* **Surfaces (§32–§33):** v15 CLI subcommands, v15 HUD badges.

---

## 2. Module map (v12 → v15)

Line counts are real `wc -l` values on the final tree.

| Module | LOC | Chapter | What it is |
|---|---:|---|---|
| `intelligence/twin/` (twin.py 643 + \_\_init\_\_ 24) | 667 | v12 §2 | **PersonalInteractionTwin** — 14 fact categories; every fact carries source/confidence/context/timestamp/frequency/success_rate/provenance; learn/forget/decay/correct/export/import/reset/inspect/explain; optional by contract; secrets scrubbed fail-closed |
| `world_model_temporal.py` | 455 | v12.5 §3 | **TemporalInteractionWorldModel** — frozen `HumanState`/`ComputerState`/`TaskState` sections; observe/snapshot/diff/history/transitions/explain/predict_state; expected-vs-observed mismatch detection; bounded history 128 |
| `goals.py` | 361 | v13 §4 | **COMMAND→INTENT→TASK→GOAL** deterministic parser; optional validated interpreter adapter that can NEVER downgrade risk or enable execution (`execution_allowed` is always `False`); PREDICTION ≠ PERMISSION ≠ EXECUTION |
| `tasks.py` | 568 | v13 §5 | **TaskEngine** — bounded (100 tasks × 64 steps), DAG dependencies, human approval gates for destructive steps, bounded retry policies, checkpoints with honest state-only rollback, audit ring |
| `skills.py` | 551 | v13.5 §6 | **InteractionCompression** (sequence clustering by action-template signature that ignores coordinates; ≥3 repetitions + confidence ≥0.6 + notification + preview + approval — NEVER silent) + **PersonalSkillLibrary** (versioned/editable/revocable/exportable) |
| `recovery2.py` | 496 | v14 §7 | **RecoveryEngine** — PRECONDITION→EXECUTE→OBSERVE→VERIFY→RECOVER; 7 strategies (retry/reobserve/retarget/alternate-modality/alternate-semantic-target/alternate-execution/request-human); 14 failure diagnoses; bounded rounds (≤6); safety gate before every execution; permission-denied never retries; malformed fails closed |
| `target_resolver.py` | 329 | v14 §8 | **UniversalTargetResolver** — 7-provider chain accessibility→DOM→semantic-API→OCR→vision→geometry→coordinate; coordinates only behind `allow_coordinate_fallback`; resolve/explain/verify |
| `aip.py` | 489 | v14.5 §9 | **AIP protocol 1.0** — DISCOVER/OBSERVE/TARGET/REQUEST/AUTHORIZE/EXECUTE/VERIFY/RESULT; 12 JSON schemas with a strict fail-closed validator rejecting unknown fields; version negotiation (same-major); capability discovery; 256 KB size caps |
| `agent_sdk.py` | 402 | §10 | **AirMouse SDK** — connect/capabilities/observe/targets/execute/verify/task/stop/status over an in-process AIP endpoint; permission gate on every execute; fails closed to ASK without an engine |
| `agent-core/` (outside the wheel) | 251 | §11 | **airmouse-agent-core** standalone package — stdlib-only, lazy, never imports airmouse, ≈10 KB source, AIP over in-process/stdio transports |
| `agent-sdk-js/` (outside the wheel) | 215 | §11 | **airmouse-agent.js** — dependency-free JS/TS SDK with the same primitives |
| `agents.py` | 418 | §12 | **AgentRegistry** — identity/priority/capabilities/budgets/audit; exclusive resource leases; deterministic conflict resolution (holder keeps until release/expiry; priority never steals); handoff; data-only messaging; human override; emergency_stop_all |
| `permissions.py` | 305 | §14/§15 | **AgentPermissionEngine** — global hierarchy E-STOP>HUMAN OVERRIDE>SAFETY POLICY>PERMISSION>AGENT>PREDICTION; granular keys `observe.screen` … `destructive.action`; ALLOW/DENY/ASK/ALLOW_ONCE/ALLOW_SESSION/ALLOW_PATTERN; ASK without human == NO |
| `ditm.py` | 304 | §16 | **DO IT WITH ME** — goal→proposal {OBJECTIVE, PLAN, SOURCES, CURRENT STATE, RISKS, REQUIRED ACTIONS, APPROVAL STATE}→START/EDIT/PAUSE/STOP/CHANGE_DIRECTION→progress→corrections |
| `onboarding.py` | 167 | §17/§18 | **One-choice progressive onboarding** (voice/hands/eyes/keyboard/automatic/all) + accessibility posture for 8 modes as architectural flags |
| `licensing.py` | 163 | §19 | **Transparent local licensing** — FREE tier = complete core (`CORE_FEATURES`); higher tiers add, never subtract; revocable; no dark patterns; no phone-home |
| `marketplace.py` | 243 | §20 | **SkillManifest** validation fail-closed (unknown fields rejected, semver, risk levels); install/enable/disable/update/rollback/remove/inspect; high/destructive risk requires explicit human trust; NO code-execution path — manifests are DATA |
| `explain.py` | 122 | §24 | Six question traces: why-predicted / why-target / why-confirmation / why-failed / why-recovery / which-preference; sensitive-data-free |
| `simulator.py` | 341 | §26 | **Deterministic virtual computer** — windows/tabs/buttons/forms/files/clipboard/navigation/UI-changes/failure modes; same script → same final state |
| `failure_injection.py` | 251 | §27 | 12 failure classes; OBSERVE→DIAGNOSE→RECOVER→VERIFY or safe stop; permission/malformed/conflict classes never retry |
| `__main__.py` (v15 part) | — | §32/§33 | v15 CLI subcommands `status/capabilities/observe/world/twin/skills/agents/permissions/tasks/protocol/benchmark`; HUD badges `AGENT:` `TASK:` `CONFIRM?` `RECOVER:` |

The v12→v15 code adds ≈ 6,600 lines across 18 new modules (17 of them
audited for network code plus the twin package), excluding the
standalone `agent-core`/`agent-sdk-js` packages. Everything is
stdlib-only, import-headless, and runs with networking disabled.

---

## 3. v12 §2 — Personal Interaction Twin (`intelligence/twin/`)

* **14 fact categories** (`TwinCategory`): preference, habit,
  gesture_pattern, gaze_behavior, voice_vocabulary, command_preference,
  application_preference, workflow_preference, confirmation_behavior,
  correction_behavior, timing_pattern, successful_action, failed_action,
  modality_preference.
* **Every fact carries its evidence** (§2 schema): `source`
  (`FactSource`: voice/gesture/gaze/keyboard/mouse/screen/browser/agent/
  user_explicit/system/imported), `confidence` (0..1, EMA-updated),
  bounded `context` dict, `timestamp` (monotonic + wall clock),
  `frequency`, `success_rate` (0..1 or None), and a bounded
  `provenance` evidence list (≤ 8 entries per fact).
* **Lifecycle:** `learn` → `record_outcome` → `decay` (30-day half-life,
  facts under confidence 0.05 are forgotten) → `forget`/`correct` →
  `export`/`export_json`/`import_data` → `reset` → `query`/`get`/
  `preferred_modality`/`top_commands` (inspect) → `explain`.
* **Bounded:** ≤ 2,000 facts, values ≤ 120 chars, context ≤ 6 entries,
  exports ≤ 8 MB; weakest-fact eviction beyond capacity.
* **OPTIONAL BY CONTRACT:** the core never imports the twin at module
  scope and works when it is absent, disabled, corrupted or reset.
* **Secrets scrubbed fail-closed:** password/token/credential-shaped
  keys and GitHub-PAT / API-key / private-key / card-shaped values are
  refused outright; free text is rejected or truncated (patterns, never
  content). `explain()` returns confidence/frequency/provenance without
  sensitive data.

## 4. v12.5 §3 — Temporal world model (`world_model_temporal.py`)

* Three **frozen** snapshot sections: `HumanState` (mode, attention
  proxy, current intent + confidence, interaction modality, sensor
  health), `ComputerState` (active app/window, browser, tabs, files,
  clipboard policy marker, notifications, devices, visible UI targets),
  `TaskState` (objective, phase, progress, recent actions, blockers,
  expected next state).
* `observe()` merges partial observations — unsupplied fields persist;
  every observation records a `cause` + `cause_confidence` (transition
  causality metadata).
* `snapshot()`/`previous()`/`history()` return frozen snapshots (deep
  copies out; internal state never exposed); `diff()` returns the
  changed dotted paths with from/to values; `transitions()` lists the
  causal chain; `mismatches()` lists expected-vs-observed records
  (`expect()` sets the task-level expectation planners want to see).
* `explain()` summarises sequence/history/mismatches deterministically;
  `predict_state()` is **PREDICTION ONLY** — data for intent/safety
  layers, never a permission.
* Bounded: 128 snapshots retained; all string/list fields clipped.

## 5. v13 §4 — Goal hierarchy (`goals.py`)

* Deterministic pattern tables classify an utterance into
  **COMMAND → INTENT → TASK → GOAL** (first match wins; bounded
  utterance ≤ 300 chars).
* Every `Objective` exposes: level, name, confidence, context,
  `proposed_plan` (bounded, PROPOSAL ONLY), `risk`
  (none/low/medium/high/destructive from deterministic token tables),
  `required_permissions`, `required_confirmations`, and
  `execution_allowed = False` **always**.
* The optional `interpreter` adapter may re-classify low-confidence
  results; its output is labelled `parsed_by="intelligence_adapter"`
  and is bounded to the same levels — it can never downgrade risk or
  mark anything execution-allowed.
* Risk drives permissions + confirmations: destructive →
  `destructive.action` + `confirm_destructive`; sensitive →
  `sensitive.operation` + `confirm_sensitive`.

## 6. v13 §5 — Task engine (`tasks.py`)

* `TaskEngine`: create / decompose / `add_dependency` (DAG, ≤ 8 deps per
  step) / `ready_steps` / `begin_step` / `complete_step` /
  `record_verification` / `retry_step` / pause / resume / cancel /
  progress / audit.
* `TaskStep` = objective · action · target · preconditions · expected
  result · verification · risk · permission · timeout (≤ 600 s) ·
  bounded `RetryPolicy` (≤ 3 attempts, ≤ 60 s backoff,
  recover-with-human flag).
* **Destructive steps cannot run without explicit human approval** —
  the task stays `PENDING_APPROVAL` until `approve()` is called by the
  human.
* `checkpoint()`/`rollback()` restore **task/step STATE only** — they
  never undo external side effects (that is Recovery's job, honestly).
* Bounded: 100 tasks × 64 steps, 20 checkpoints, 200 audit entries.

## 7. v13.5 §6 — Skills (`skills.py`)

See `docs/SKILLS.md` for the full pipeline and marketplace trust
boundaries. Core rule: a skill exists only after repeated behavior
(≥ 3 identical sequences) + confidence ≥ 0.6 + semantic clustering by
action-template signature (action names + target KINDS, deliberately
ignoring coordinates) + user notification + preview + approval —
**never silent**. Skills are versioned, inspectable, editable,
exportable, importable, permission-aware, revocable, and never depend
on screen coordinates (coordinates are an explicit, flagged fallback
only).

## 8. v14 §7 — Recovery engine (`recovery2.py`)

* Loop: **PRECONDITION → EXECUTE → OBSERVE → VERIFY → RECOVER**,
  execution-agnostic (callers supply executor/observer callbacks).
* Strategy ladder: RETRY → REOBSERVE → RETARGET → ALTERNATE_MODALITY →
  ALTERNATE_SEMANTIC_TARGET → ALTERNATE_EXECUTION → REQUEST_HUMAN
  (+ GIVE_UP as the safe stop).
* 14 `FailureKind` diagnoses (target_missing, target_moved,
  window_closed, stale_dom, ocr_failed, accessibility_failed,
  network_failed, permission_denied, timeout, app_crash,
  agent_conflict, malformed_request, unknown, none) map deterministically
  to per-diagnosis ladders.
* Hard rules: hard round cap (≤ 6) + bounded retries (≤ 4); the
  **safety gate is consulted before every execution**; no silent
  privilege escalation; destructive recovery requires a confirmation
  hook (absent hook ⇒ no destructive recovery); **permission-denied
  never retries** (ladder is REQUEST_HUMAN only); **malformed requests
  fail closed** (GIVE_UP); every round appends an explainable trace
  entry (`RecoveryTrace`, §24).

## 9. v14 §8 — Universal target resolver (`target_resolver.py`)

* One resolver for every consumer (human voice, agent SDK, skills,
  tasks, macros). Callers say WHAT (`TargetRequest`: description, kind,
  value, app, browser hints); the resolver decides HOW.
* Chain: **accessibility → DOM → semantic_app_api → OCR → vision →
  geometry → coordinate**. The coordinate link runs only when the
  request sets `allow_coordinate_fallback=True` (explicit flag, §6/§8).
* `resolve_target()` walks the chain deterministically and records
  per-provider attempts; `explain_target()` renders the trace without
  sensitive data; `verify_target()` re-checks a resolved target against
  the expectation.

## 10. v14.5 §9–§11 — Protocol and SDKs

See `docs/AIP_SPEC.md` (wire format, all 18 conversation message types
+ STATUS, the 12 payload schemas, negotiation, size caps, fail-closed
rules, transports) and `docs/AGENT_SDK.md` (Python SDK, standalone
`airmouse-agent-core`, JS/TS SDK, what the SDK hides and what it cannot
do).

## 11. v15 §12–§14 — Multi-agent infrastructure

See `docs/MULTI_AGENT.md`. Summary: `AgentRegistry` gives every agent
identity, priority (1 highest … 9), capabilities, per-minute budgets,
state and an audit trail; resources (mouse, `window:doc1`, clipboard,
…) are held under **exclusive leases** with TTL; conflicting actions are
refused while a lease is held — **the holder keeps the resource until
release or expiry and priority never steals a live lease**; handoff is
release + reacquire + notify; agent messages are DATA (info/handoff/
result/question), never executed, never parsed as instructions; humans
override (suspend/stop), `emergency_stop_all()` stops every agent,
releases every lease and latches the permission e-stop.

## 12. v15 §14/§15 — Permission engine (`permissions.py`)

* Global control hierarchy — nothing may reorder it:
  **EMERGENCY STOP > HUMAN OVERRIDE > SAFETY POLICY > PERMISSION >
  AGENT > PREDICTION.**
* 12 granular permission keys: `observe.screen`, `read.accessibility`,
  `read.clipboard`, `type.text`, `mouse.click`, `browser.navigate`,
  `file.read`, `file.write`, `application.launch`, `application.close`,
  `system.operation`, `destructive.action`.
* Decisions: ALLOW, DENY, ASK, ALLOW_ONCE (use-counted),
  ALLOW_SESSION, ALLOW_PATTERN (fnmatch). **ASK without a human answer
  is NO** — fail closed; no rule at all also fails closed to ASK.
* Rule matching is deterministic (exact agent+key > agent+wildcard >
  wildcard-agent exact > wildcard). `explain_decision()` returns a
  §24-style trace with the active hierarchy level and the "because"
  chain.

## 13. v15 §16–§20 — Experience and platform layers

* **DO IT WITH ME (§16, `ditm.py`)** — see `docs/USER_GUIDE.md`. The
  structured proposal is {OBJECTIVE, PLAN, SOURCES, CURRENT STATE,
  RISKS, REQUIRED ACTIONS, APPROVAL STATE}; the user verbs are
  START / EDIT PLAN / PAUSE / STOP / CHANGE DIRECTION; corrections are
  learned (Twin optional); nothing runs before approval and destructive
  plans stay PENDING_APPROVAL inside the TaskEngine.
* **Onboarding (§17, `onboarding.py`)** — one choice
  (voice/hands/eyes/keyboard/automatic/all) yields a small safe
  starting profile and is immediately usable; preferences are learned
  progressively, quietly and boundedly.
* **Accessibility (§18, `onboarding.py`)** — 8 modes (voice-only,
  gesture-only, gaze-only, keyboard-only, switch-access, hybrid,
  hands-free, low-mobility); every mode maps to ≥ 1 modality and a
  configurable confirmation path; large-UI / high-contrast /
  reduced-motion are architectural flags, not a theme.
* **Licensing (§19, `licensing.py`)** — tiers FREE / PRO / DEVELOPER /
  ENTERPRISE / SDK / MARKETPLACE / HARDWARE; **FREE = the complete
  local core** (21 `CORE_FEATURES`); higher tiers add, never subtract;
  state is inspectable, local, revocable, with `phones_home: False` and
  `dark_patterns: "none"` in the transparency surface.
* **Marketplace (§20, `marketplace.py`)** — see `docs/SKILLS.md`.

## 14. v15 §24–§27 — Observability and testability

* **Explainability (§24, `explain.py`)** — six questions, structured
  and bounded traces: `explain_prediction`, `explain_target_choice`,
  `explain_confirmation`, `explain_failure`, `explain_recovery`,
  `explain_preference_influence`, composable via `decision_trace()`;
  values are categorical labels or lengths, never content
  (`sensitive_data: False`).
* **Simulator (§26, `simulator.py`)** — the deterministic virtual
  computer: windows, tabs/pages/buttons/forms, text, files, clipboard,
  navigation, UI changes (`change_ui`), app crashes, and a
  `fail_mode` injection hook. Same script → same final state. It is
  also the "fake computer environment" for developers (§25) and THE
  computer model for all v12→v15 verification.
* **Failure injection (§27, `failure_injection.py`)** — 12 classes
  (missing_target, moved_button, closed_window, stale_dom,
  ocr_failure, accessibility_failure, network_failure,
  permission_denial, timeout, app_crash, agent_conflict,
  malformed_request). Each scenario verifies OBSERVE→DIAGNOSE→RECOVER→
  VERIFY **or** a safe stop; permission/malformed/conflict classes
  never retry (they resolve through the human).

## 15. v15 §32–§33 — CLI and HUD surfaces

* **CLI (§32)** — `airmouse status | capabilities | observe | world |
  twin | skills | agents | permissions | tasks | protocol | benchmark`
  — all print-and-exit, all local, all fast. `status` prints the
  platform banner, protocol and the §14 hierarchy; `capabilities`
  prints the AIP discovery list; `observe` prints a simulated-computer
  snapshot (no hardware claimed); `benchmark` is a local spot-check.
* **HUD (§33)** — four v15 badges alongside the v9/v11.5 badges:
  `AGENT:` (an AI agent is controlling the computer — the user must
  ALWAYS know), `TASK:` (task/verification progress), `CONFIRM?`
  (a confirmation is pending), `RECOVER:` (recovery active).

---

## 16. Honest scope

* All v12→v15 subsystems are **SIMULATION-VERIFIED** through 270 new
  tests (786 preserved) plus the deterministic simulator and failure
  injection suite.
* The simulator **is** the computer model (§26): deterministic, bounded,
  no real display. AIP is verified at protocol level in-process and over
  stdio JSON-lines.
* Webcam / microphone / gaze hardware / RF / real-Chrome-CDP / live-ASR
  remain **NOT PHYSICALLY VERIFIED** (unchanged since v11.5).
* No network code exists in any of the 17 new v12→v15 modules
  (asserted by an executable audit test).
