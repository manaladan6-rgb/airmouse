# AirMouse v11.5.0 — Architecture ("Adaptive Human-Computer Intelligence")

This document describes the v11.5 loop, the intelligence plugin
architecture, Fusion 2.0, the world model, the transcription pipeline,
and the enforcement points that keep **PREDICTION ≠ EXECUTION**.

Everything in v11.5 is **local, offline, stdlib-only, bounded, and
optional**. The adaptive-intelligence layer is a plugin: the v10 core
runs normally when it is absent, disabled, corrupted, incompatible, out
of memory, or paused for privacy. Nothing here is a neural LLM and no
cloud is involved — the intelligence comes from

    MODEL + MEMORY + CONTEXT + RULES + FUSION + WORLD MODEL +
    VERIFICATION + LEARNING

each of which is small, inspectable, and deterministic.

---

## 1. The §3 interaction loop

v11.5 closes a learning loop around the v10 pipeline:

```
        ┌──────────────────────────────────────────────────────────────┐
        │                                                              │
        ▼   8. LEARN (verified actions → learning events →             │
   ┌─────────┐      personal model + memory + vocabulary,              │
   │OBSERVE  │      bounded, privacy-scrubbed)                         │
   │RESULT   │◄────────────────────────────────────────────────┐       │
   └────┬────┘                                                 │       │
        │ 7. VERIFY (expected vs observed; browser before/     │       │
        │    after diff; success/failure recorded)             │       │
        ▼                                                      │       │
   ┌─────────┐   6. EXECUTE (only after the v10 SAFETY gate;   │       │
   │ EXECUTE │      destructive ops still require explicit      │       │
   └────┬────┘      confirmation)                             │       │
        │                                                     │       │
        ▼                                                     │       │
   ┌──────────────┐  5. REQUEST / CONFIRM (conflicts, low       │       │
   │CONFIRM (ASK) │     confidence, destructive ⇒ ASK,          │       │
   └────┬─────────┘     never guess)                           │       │
        │                                                     │       │
        ▼                                                     │       │
   ┌──────────────┐                                           │       │
   │ PREDICT (4)  │ ── suggestions are DATA for the HUD/       │       │
   └────┬─────────┘    intent layer; never executed          │       │
        │                                                     │       │
        ▼                                                     │       │
   ┌──────────────┐                                           │       │
   │ UNDERSTAND(3)│  fusion2 weighted consensus · world model │       │
   └────┬─────────┘  · contextual resolver · grammar          │       │
        │                                                     │       │
        ▼                                                     │       │
   ┌──────────────┐                                           │       │
   │OBSERVE (1,2) │  sensors + context + world model snapshot │───────┘
   └──────────────┘
```

Concretely, one verified action flows like this:

1. **OBSERVE** — sensors produce `Event`s (voice/gesture/gaze/…);
   `WorldModel` keeps a bounded snapshot (application, window, targets,
   gaze target, text field, recent action/command, mode).
2. **UNDERSTAND** — `FusionEngine2` fuses 9 weighted signals into one
   explainable `FusedIntentCandidate`; the `ContextualCommandResolver`
   resolves deictic phrases ("click that") against the world model.
3. **PREDICT** — the optional `Predictor` produces *data-only*
   suggestions (`Prediction`: kind/value/confidence/reason/
   alternatives) from the personal model; `ProactiveAssistant` turns
   them into `Suggestion` objects. Predictions never execute.
4. **REQUEST / CONFIRM** — conflicts, low confidence, destructive
   intent, or a missing non-prediction signal ⇒ `requires_confirmation`
   (the assistant **ASKS**; it does not guess).
5. **EXECUTE** — only candidates that pass `FusedIntentCandidate.
   executable` materialize into a v10 `Intent`, which still passes the
   v10 `SafetySystem` gate (e-stop, rate limits, confirmations).
6. **OBSERVE RESULT** — the action engine's verification observer
   reports the post-action state.
7. **VERIFY** — expected vs observed; browser actions use a
   before/after state diff (`passed`/`failed`/`unknown`).
8. **LEARN** — `InteractionAgent._learn_from_report` turns **verified**
   actions into learning events (`record_action`), updating the
   personal model (ActionMarkov), interaction memory, workflow
   discovery, and the world model's recent-action ring. Learning is a
   guarded no-op: it must never break the action pipeline.

---

## 2. Module map (real `wc -l` on the v11.5 tree)

Package: **58 Python modules**, **29,646 LOC** total (v10: 41 modules /
22,781 LOC ⇒ net growth ≈ +6,900 lines). The `browser_extension/`
assets add 235 lines.

### New in v11.5 — `airmouse/intelligence/` subpackage (9 modules, 3,445 LOC)

| Module | LOC | Responsibility |
|---|---:|---|
| `intelligence/__init__.py` | 98 | `IntelligenceState` (8 lifecycle states), `MODEL_FORMAT_MAGIC = b"AIMM"`, `MODEL_FORMAT_VERSION = 1`, `MODEL_CAPACITY_BYTES_DEFAULT = 30 MB`; lazy PEP 562 exports |
| `intelligence/model.py` | 858 | `PersonalInteractionModel` = `NGramModel` (order-3, backoff, capped counts) + `ActionMarkov` + `CommandModel` (time-of-day habits) + `EmojiModel` (context tags) + `FeatureWeights`; quantized 8-bit packed artifact, hard capacity budget with deterministic pruning, `save()/load()/stats()` |
| `intelligence/memory.py` | 373 | `InteractionMemory` + `PatternRecord` (pattern/frequency/confidence/last_seen/context/success_rate/correction_count/preferred_action); `is_sensitive` / `scrub_pattern` fail-closed scrubbing; bounded 5,000 patterns |
| `intelligence/vocabulary.py` | 310 | `PersonalVocabulary` — terms (20k cap) + corrections (5k cap), `apply_corrections`, validated import/export |
| `intelligence/prediction.py` | 271 | `Predictor` → `Prediction` (kind/value/confidence/reason/alternatives ≤ 8 candidates); `EMOJI_KEYWORDS` deterministic baseline map |
| `intelligence/selftune.py` | 187 | `SelfTuner` + `TUNABLES` (10 parameters, hard min/max bands, min-samples gates, EMA stats) |
| `intelligence/personalization.py` | 349 | `GestureProfile` / `GazeProfile` / `VoiceProfile` (alias learning, e.g. "launch browser" → "open browser"), `PersonalizationEngine` |
| `intelligence/workflows.py` | 500 | `WorkflowDiscovery` (3–8-step patterns, ≥3 repetitions, approval-gated), `WorkflowStore` (200 cap), `WorkflowRunner` (preview + destructive confirmation gates), `ProactiveAssistant`, `Suggestion` |
| `intelligence/plugin.py` | 499 | `IntelligencePlugin` facade — never raises, artifact persistence in `~/.airmouse/intelligence/`, validated `export_profile`/`import_profile` |

### New in v11.5 — top-level modules

| Module | LOC | Responsibility |
|---|---:|---|
| `transcription.py` | 624 | `LiveTranscriptionEngine` — streaming partials/finals pipeline, VAD auto-finalize, spoken punctuation, discourse commas, capitalization, number spelling, vocabulary pipeline, bounded history + txt/json/md export + search, `wer()` evaluator, metrics |
| `dictation_text.py` | 343 | `VoiceTypingEngine` (COMMAND/DICTATION/HYBRID, spoken formatting, 16 edit commands, undo/redo), `TextPredictor`, `EmojiSuggester` (30 s cooldown) |
| `text_control.py` | 250 | `TextController` — 16 `TextOp`s, keyboard fallback, never coordinate-dependent |
| `world_model.py` | 265 | `WorldModel` (bounded snapshot + explainable likely intent), `ContextualCommandResolver` (12 deictic families, ask-don't-guess confidence model) |
| `modes.py` | 832 | `ModeController` + `MODE_REGISTRY` (teacher/student/office/meeting/research/developer phrase tables), `PresentationController` (generic hotkeys), `TimelineSession`, `StudyTimer`, `NotesStore`, `SourceCapture`, `MeetingMode`, `AccessibilityProfiles` (8 profiles + custom chains), `DeveloperMode` |
| `fusion2.py` | 291 | `FusionEngine2` (9 signals), `ConflictResolver`, `FusedIntentCandidate.executable`, `RFExtendedProvider` protocol + `RFNoHardware` honest default |
| `privacy.py` | 193 | `PrivacyDashboard`, `PrivacyFlags` (telemetry/cloud OFF), `ConnectionState` (OFFLINE/ONLINE/PRIVACY) |
| `selftest.py` | 233 | `run_self_test` / `format_self_test` — 15 components, PASS/FAIL/OPTIONAL/HARDWARE |

### Extended in v11.5

| Module | LOC | v11.5 changes |
|---|---:|---|
| `agent.py` | 1,045 | guarded `intelligence`/`world_model`/`fusion2`/`modes` wiring; `_learn_from_report` (verified actions → learning events, never raises); new `overrides` keys: `intelligence`, `text_controller`, `voice_typing`, `transcription`, `fusion2`, `world_model`, `modes` |
| `__main__.py` | 2,164 | flags `--intelligence/--no-intelligence/--dictation/--transcribe/--teacher/--student/--office/--meeting/--research`; subcommands `intelligence/memory/vocabulary/workflows/self-test`; HUD badges `AI:` `MODE:` `SUG:` + transcript caption |
| `config.py` | 593 | v11.5 TOML keys: `[intelligence] [learning] [memory] [transcription] [dictation] [prediction] [emoji] [teacher] [student] [office] [meeting] [research] [developer] [accessibility] [workflow] [privacy]` — backward compatible |
| `offline.py` | 423 | `run_offline_selftest` extended 13 → **18 checks** (adds `intelligence_offline`, `memory_offline`, `vocabulary_offline`, `transcription_offline`, `fusion2_offline`) under real socket-level network isolation |

---

## 3. Optional-plugin architecture

The intelligence subpackage is a **plugin**, not a dependency:

* the core never imports `airmouse.intelligence` at module scope;
* the only object the core touches is `IntelligencePlugin`;
* every entry point catches all exceptions and degrades to a
  documented no-op — **the facade never raises**.

### 3.1 Lifecycle states

`IntelligenceState` (8 states — every non-AVAILABLE state means
"intelligence is not contributing" and the core keeps working):

| State | Set when |
|---|---|
| `AVAILABLE` | all artifacts loaded (or fresh) |
| `DISABLED` | config/user disabled the plugin |
| `UNAVAILABLE` | imports failed (subpackage missing) |
| `CORRUPTED` | a stored artifact failed to load |
| `INCOMPATIBLE` | artifact version mismatch (`ModelError`) |
| `OUT_OF_MEMORY` | `MemoryError` during load/record |
| `PRIVACY_PAUSED` | privacy mode paused learning |
| `LEARNING_PAUSED` | temporary learning pause |

### 3.2 Plugin contract

1. Constructor and `load()` never raise; failures become states.
2. Learning can be paused (`pause_learning`, privacy mode) or wiped
   (`delete_learned_data`) by the user at any time.
3. Learning stores **patterns, not private content** — the memory
   scrubber refuses credential-shaped input (fail-closed).
4. All stores are bounded (hard limits, deterministic pruning).
5. `PREDICTION != EXECUTION` — nothing in the plugin executes actions.
6. Artifacts live in `~/.airmouse/intelligence/`:
   `model.bin` (AIMM binary), `memory.json`, `vocabulary.json`,
   `workflows.json`, `selftune.json`.

---

## 4. Fusion 2.0 — signals and weights

`fusion2.SIGNAL_WEIGHTS` (personal-history and prediction weights are
multiplicatively scaled by the self-tuner's bounded `history_weight` /
`prediction_weight`):

| Signal | Weight | Typical source |
|---|---:|---|
| `voice` | 0.30 | offline ASR transcript → grammar/NL/resolver |
| `gesture` | 0.25 | gesture registry / v5 FSM |
| `gaze` | 0.25 | gaze engine target |
| `personal_history` | 0.10 | learned action/command statistics |
| `keyboard` | 0.05 | keyboard monitor |
| `browser_context` | 0.05 | browser bridge state |
| `application_context` | 0.05 | focused app/window context |
| `recent_action` | 0.05 | last verified action |
| `prediction` | 0.05 | personal-model forecast (see §6) |

`fuse()` performs a weighted vote per intent name, picks the best
(target = highest-weighted signal that carried one), then applies the
`ConflictResolver`:

* two real signals (confidence ≥ 0.5) naming different intents ⇒
  conflict; fused confidence × `CONFLICT_PENALTY` (0.5);
* any conflict involving a destructive intent ⇒ `requires_confirmation`;
* **a prediction never outranks a real observation** — `executable`
  requires at least one NON-prediction signal;
* `requires_confirmation` is also forced when the winner is
  destructive or below `min_confidence` (default 0.45).

`FusedIntentCandidate.executable` is the single policy point:
`not conflicts and not requires_confirmation and has_real_signal`.
`FusionEngine2.to_intent()` materializes a v10 `Intent` **only** when
`executable` is true — otherwise it returns `None`.

---

## 5. World model + contextual commands

`WorldModel` wraps the v10 `ContextEngine` with a bounded snapshot
(`WorldState`: application, window, visible targets ≤ 64, gaze target,
text-field marker, recent action/command rings ≤ 16, mode, likely
intent + confidence + reason). The likely intent comes from the
predictor and is **suppressed entirely when destructive**
(`is_destructive_action`).

`ContextualCommandResolver` maps 12 deictic phrase families (15 exact
utterances) to structured intents:

| Family | Utterances | Intent | Sensitivity |
|---|---|---|---|
| click | "click that" / "click this" / "click it" | `CLICK` | safe |
| open | "open that" / "open this" | `OPEN` | safe |
| close | "close it" / "close that" | `CLOSE` | **sensitive** |
| copy | "copy that" | `COPY` | safe (targetless) |
| read | "read this" | `SELECT` | safe |
| zoom | "zoom that" | `ZOOM` | safe |
| scroll | "scroll there" | `SCROLL` | safe |
| select | "select this" | `SELECT` | safe |
| use | "use that" | `CLICK` | safe |
| go | "go there" | `NAVIGATE` | safe |
| save | "save that" | `HOTKEY` (ctrl+s) | safe (targetless) |

Confidence model (deterministic): gaze-resolved target 0.85 ·
selection 0.75 · other context 0.55 · fallback 0.5 · targetless ops ≥
0.6 · **nothing resolvable 0.2**. Below `min_confidence` (default 0.4)
or when sensitive ⇒ `requires_confirmation` — the assistant **ASKS**,
it does not guess.

---

## 6. Data flow of a learning event

```
 verified ActionReport (ActionStatus.SUCCESS)
        │  agent._learn_from_report          (never raises; no-op w/o plugin)
        ▼
 IntelligencePlugin.record_action(name, history, success)
        │ learning_active? (enabled ∧ AVAILABLE ∧ learning ∧ ¬privacy)
        ├─► model.learn_action_step(history[-2:], action)   → ActionMarkov
        ├─► memory.record("action:<name>", success=success) → PatternRecord
        │      └─ scrub_pattern(): sensitive ⇒ refused (None); token-like
        │         blobs ⇒ [redacted-*] placeholders; length ≤ 200
        └─► discovery.observe_step(action)                  → workflow window
               └─ 3–8-step pattern ≥ 3 repetitions ⇒ WorkflowSuggestion
                  (suggestion ONLY — a workflow exists after explicit
                   user approval via WorkflowStore.create)
```

Everything down-stream of the report is DATA. The next time the same
context arises, `predict_next_action` can surface the learned
continuation as a suggestion — and the loop in §1 closes. Learning
events stop instantly on privacy mode, paused learning, or a
non-AVAILABLE plugin state; nothing is queued for later.

---

## 7. Transcription pipeline

```
 MICROPHONE (or deterministic injection)
      │ raw audio chunks
      ▼
 AUDIO PREPROCESSING ─► EnergyVAD (hysteresis, auto-finalize on speech end)
      │
      ▼
 STREAMING ASR ── StreamingProviderAdapter (batch providers get a
      │            deterministic word-by-word partial stream)
      │            SimulatedStreamingProvider always available;
      │            vosk/whisper/pocketsphinx adapters guarded, honestly
      │            reported as unavailable when not installed
      ▼
 PARTIAL TRANSCRIPT ──► on_partial callbacks (HUD caption)
      │ (on speech end / finalize)
      ▼
 STABILIZATION ─► PUNCTUATION (apply_spoken_punctuation: "period" → ".")
      ▼
 CAPITALIZATION (insert_discourse_commas → spell_numbers →
      ▼          capitalize_text with personal proper nouns)
 PERSONAL VOCABULARY (terms as proper nouns + learned corrections)
      ▼
 FINAL TRANSCRIPT (TranscriptSegment: text/confidence/provider/
      timing/partial_count) ── bounded history ≤ 500 segments,
      buffer ≤ 200k chars, export txt/json/md ≤ 8 MB, search
```

Details, the spoken-punctuation table and the (deliberately
deterministic, non-AI) punctuation heuristics are documented in
`docs/TRANSCRIPTION_GUIDE.md`.

---

## 8. PREDICTION ≠ EXECUTION — enforcement points

This rule is enforced at five independent layers:

1. **Data types** — `Prediction` and `Suggestion` are inert dataclasses;
   they carry no executor and no way to acquire one.
2. **`ProactiveAssistant`** — produces suggestions only; `prepare()`
   deliberately returns `False` for anything resembling a URL, path, or
   command; destructive-looking suggestions are suppressed outright.
3. **`WorldModel.likely_intent`** — never surfaces a destructive
   prediction as "likely intent".
4. **`FusedIntentCandidate.executable`** — a candidate with only
   prediction signals is never executable; conflicts and destructive
   winners force confirmation.
5. **v10 `SafetySystem`** — even materialized intents pass the e-stop /
   rate-limit / confirmation gate; destructive actions require the
   explicit v10 confirmation flow (unchanged).

---

## 9. Where everything persists

| Path | Content |
|---|---|
| `~/.airmouse/intelligence/model.bin` | quantized packed personal model (AIMM format, magic + version) |
| `~/.airmouse/intelligence/memory.json` | interaction memory patterns (scrubbed) |
| `~/.airmouse/intelligence/vocabulary.json` | personal terms + corrections |
| `~/.airmouse/intelligence/workflows.json` | approved workflows |
| `~/.airmouse/intelligence/selftune.json` | bounded tuner state |
| `~/.airmouse/lecture.md` / meeting summaries / notes | mode artifacts (exported on demand) |
| `~/.airmouse/gestures.json` | v10 custom gestures (unchanged) |

Fresh installs are a few KB and grow only through use, up to the
~30 MB model capacity budget (`intelligence_model_capacity`); the
budget is enforced by refusing further growth (counted in
`capacity_hits`), never by unbounded accumulation.

---

*See `../README.md` for the user guide, `../VERIFICATION_REPORT.md`
for measured evidence, `../docs/V10_ARCHITECTURE.md` for the v10 core
architecture, and `../CHANGELOG.md` for version history.*
