# AirMouse v11.5 — Intelligence Guide

How the adaptive-intelligence plugin learns, what it stores (and what
it **never** stores), how predictions stay explainable, and how to
manage all of it from the CLI.

**Honest framing:** this is a compact local model with a ~30 MB
capacity budget — a set of bounded statistical tables, quantized where
appropriate, plus memory + context + rules + fusion + a world model +
verification + learning. It is **not** a neural LLM, it has **no**
pre-trained weights, and nothing ever leaves your machine.

---

## 1. What the personal model learns

`airmouse/intelligence/model.py` packs five bounded components into one
versioned artifact (`PersonalInteractionModel`):

| Component | Learns | Used for |
|---|---|---|
| `NGramModel` | word-level n-grams (order 3, backoff) from text you dictate/commit | word/phrase completion, dictation stabilization |
| `ActionMarkov` | action→action transition statistics from **verified** actions | "you often open the editor after the browser" |
| `CommandModel` | command frequency + time-of-day habits | "you usually say *open browser* around 9 AM" |
| `EmojiModel` | emoji usage per context tag | personal emoji suggestions |
| `FeatureWeights` | small named personalization weights (bounded ±8) | tuning signals |

Every component has hard limits (e.g. 200,000 vocab terms,
1,500,000 n-gram entries, 65,535 count cap) and deterministic pruning
(keep top 75% of mass). Probabilities are quantized to 8 bits in the
packed artifact, which begins with magic `AIMM` and a format version
(currently 1) so corrupt/incompatible files are detected, never
guessed at.

**The model ships empty.** Fresh installs hold a few KB
(`airmouse intelligence` reports something like
`model: 0.1 KB of 30 MB budget | words: 0 | actions: 0`) and grow only
through your own usage.

## 2. What it NEVER stores

The memory layer (`InteractionMemory`) stores short interaction
**patterns** (≤ 200 characters), never raw content. The scrubber is
**fail-closed**:

| Never stored | How it is prevented |
|---|---|
| Passwords / passphrases (`password=…`, `passwd`, `pwd`…) | `is_sensitive()` refuses the record outright |
| Tokens & API keys (`token`, `api_key`, `bearer`, `ghp_…`, `sk_…`, `AKIA…`, `xox…`, `eyJ…`, JWT-shaped) | refused outright |
| Credentials in URLs (`scheme://user:pass@host`) | refused; redacted placeholders if partially matched |
| 32+ char hex blobs / 40+ char base64-ish blobs | redacted to `[redacted-hex]` / `[redacted-blob]` |
| Full conversations / documents | patterns are truncated descriptors, not content |
| Clipboard contents | never captured by the learning path |
| Private file contents | only the action *symbol* (e.g. `file_open`) is learned |

Context values are length-capped (8 keys × 200 chars) and each
`key=value` pair is scrubbed too. Sensitive context keys are dropped.

The same scrubbing applies on **import**: `import_data()` validates
every row (`PatternRecord.from_dict` raises on sensitive or oversized
patterns — the import path counts rejects and skips them, never
trusting the file).

## 3. Capacity budget honesty

* Budget: `MODEL_CAPACITY_BYTES_DEFAULT = 30 MiB` (config:
  `intelligence_model_capacity`).
* Growth is per-use: every learned item adds bytes; when
  `size_bytes() ≥ capacity`, learning **refuses further growth**
  (`capacity_hits` counter increases) instead of growing unbounded.
* Fresh install ≈ a few KB (a 200-sentence training run produces an
  17 KB artifact — measured).
* Learning off ⇒ growth frozen (the plugin pins capacity to the
  current size when `learning_enabled` is false).

## 4. Prediction & explainability

`Predictor` produces `Prediction(kind, value, confidence, reason,
alternatives)` records — for actions, commands, text completions,
phrases, emoji, and targets. Every prediction carries a human-readable
`reason`, e.g.:

```
kind: action   value: type   confidence: 0.61
reason: You often follow this with type (75 actions observed).
alternatives: [('scroll', 0.18), ('save', 0.09)]
```

Candidate count is capped at 8; confidences are clamped to [0, 1].
Predictions are **DATA ONLY** — see the enforcement list in
`docs/V11_5_ARCHITECTURE.md` §8 (nothing in the intelligence layer can
execute an action; destructive-looking suggestions are suppressed
entirely by the `ProactiveAssistant` and the world model).

## 5. Self-tuning (bounded)

`SelfTuner` tracks EMA statistics per tunable and **proposes** new
values; each tunable has a hard `[min, max]` band, a step, and a
`min_samples` gate — proposals before the gate are refused. The 10
tunables (defaults from `selftune.TUNABLES`):

| Tunable | Default | Band | Min samples |
|---|---:|---|---:|
| `gesture_confirm_frames` | 5 | 2 – 10 | 40 |
| `gesture_transition_cooldown` | 0.15 | 0.05 – 0.5 | 40 |
| `gaze_dwell_time` | 0.8 | 0.3 – 2.5 | 30 |
| `voice_command_min_confidence` | 0.75 | 0.5 – 0.95 | 30 |
| `speech_confidence_scale` | 1.0 | 0.6 – 1.4 | 50 |
| `correction_auto_apply` | 0 | 0 – 10 | 10 |
| `prediction_weight` | 1.0 | 0 – 2 | 60 |
| `history_weight` | 1.0 | 0 – 2 | 60 |
| `gesture_amplitude_gate` | 0.02 | 0.005 – 0.08 | 50 |
| `swipe_speed_gate` | 0.35 | 0.1 – 1.2 | 50 |

`apply()` refuses values outside the band. Tuner state is exported in
the profile bundle.

## 6. Personalization profiles

* **GestureProfile** — learns your movement range, pinch style,
  preferred gestures and frequent false positives; suggests bounded
  threshold tweaks.
* **GazeProfile** — learns common targets and drift; suggests
  offset compensation and dwell time (bounded suggestions only).
* **VoiceProfile** — learns frequent commands and **aliases**: repeat
  a phrase → canonical mapping a few times and
  e.g. *"launch browser"* resolves to *open browser*. Alias count is
  capped (256).

All three are privacy-flag aware: pausing learning freezes them;
resetting personalization clears them.

## 7. Workflow discovery & the approval gate

1. `WorkflowDiscovery` watches the rolling action window (512 steps).
2. A repeated sequence of 3–8 steps occurring ≥ 3 times produces a
   `WorkflowSuggestion` (confidence `reps/(reps+4)`, capped 0.95).
3. **Nothing is created automatically.** The suggestion becomes a
   workflow only through explicit approval (`WorkflowStore.create` or
   `create_manual`). Store cap: 200 workflows × 24 steps.
4. Running a workflow goes through `WorkflowRunner`:
   * `preview()` prints the exact plan first (destructive steps
     marked ⚠);
   * destructive workflows must be **previewed at least once** before
     they can run at all;
   * every destructive step asks for confirmation **each run** —
     refusing aborts the run (`destructive_step_refused:<step>`);
   * conditions (e.g. expected application) must hold or the run
     aborts (`condition_failed:<key>`).
5. What counts as destructive: `delete`, `remove`, `close_app`,
   `close_tab`, `close_window`, `file_delete`, `file_trash`,
   `shutdown`, `restart`, `sleep`, `lock`, `empty_trash`, `format`,
   `kill_process`, `uninstall`, `overwrite`, and any symbol starting
   with `delete/remove/trash/kill/shutdown/format/wipe/purge`.
6. Workflow step names are validated identifiers
   (`^[a-z0-9][a-z0-9_-]{0,63}$`) — shell fragments, paths and
   `$(…)`/backtick payloads are rejected (see `docs/SECURITY.md`).

The `ProactiveAssistant` wraps the predictor for HUD-style
suggestions ("Open VS Code?" with a reason); it can only *suggest*,
never execute.

## 8. Plugin lifecycle states

`IntelligencePlugin` is the single facade the core touches; it **never
raises**. States (from `IntelligenceState`):

| State | Meaning | Core behavior |
|---|---|---|
| `available` | everything loaded | suggestions/predictions flow |
| `disabled` | `--no-intelligence` / config off | no-op, core unaffected |
| `unavailable` | subpackage missing/import failed | no-op, core unaffected |
| `corrupted` | stored artifact failed to load | no-op; delete the artifact to reset |
| `incompatible` | artifact version mismatch | no-op; re-export/re-learn |
| `out_of_memory` | MemoryError on load/record | no-op; bounded stores resume later |
| `privacy_paused` | privacy mode on | learning paused, history off |
| `learning_paused` | temporary pause | resumes on `resume_learning()` |

Artifacts live in `~/.airmouse/intelligence/` (`model.bin`,
`memory.json`, `vocabulary.json`, `workflows.json`, `selftune.json`).
Every save is atomic (`.tmp` + `os.replace`).

## 9. Managing intelligence from the CLI

```bash
airmouse intelligence        # plugin status: state, model size vs budget,
                             # memory patterns, vocab terms, workflows
airmouse memory              # top learned patterns (frequency/success/corrections)
airmouse vocabulary          # learned terms + corrections
airmouse workflows           # approved workflows (+ destructive flags)
airmouse --self-test         # includes Intelligence/Memory/Prediction checks

airmouse --no-intelligence   # run a session with the plugin fully off
```

Export / import / delete / reset / clear are available through the
`PrivacyDashboard` (see `docs/PRIVACY.md` for the exact procedures):

```python
from airmouse.privacy import PrivacyDashboard
from airmouse.intelligence.plugin import IntelligencePlugin

plug = IntelligencePlugin({"enabled": True})
dash = PrivacyDashboard(plugin=plug)

dash.export_profile("my-profile.json")     # validated bundle
dash.import_profile("my-profile.json")     # validated + scrubbed per section
dash.delete_learned_data()                 # wipe all learned data
dash.reset_model_personalization()         # clear personalization profiles
dash.clear_interaction_history()           # clear memory + transcript history
dash.set_privacy_mode(True)                # pause learning + history
```

Imported profiles are validated per section (`memory`, `vocabulary`,
`workflows`, `selftune`); malformed or sensitive entries are skipped,
and a fuzz suite (`tests/test_v115.py` §43) proves the importer never
raises on hostile JSON.

## 10. Where the intelligence actually comes from

No single component is "smart" on its own. The system behaves
intelligently because bounded statistics (model), bounded episodic
records (memory), the v10 context engine, deterministic rules (grammar,
resolvers, mode phrase tables), weighted fusion, the world model,
verification, and learning all constrain each other. Removing any
piece degrades gracefully instead of breaking — that is the design
contract of v11.5.
