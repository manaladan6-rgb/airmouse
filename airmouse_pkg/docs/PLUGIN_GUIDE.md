# AirMouse v11.5 — Plugin & Integration Guide

How to integrate third-party modules with AirMouse's Universal
Interaction API surface: the core dataclasses, the agent override
points, and the contracts your code must honor.

## 1. Core concepts (the shared vocabulary)

All of these live in `airmouse.interfaces` unless noted; they are pure
data — never executable content:

| Concept | Type | Notes |
|---|---|---|
| **Event** | `interfaces.Event` (+ `EventKind`, 14 kinds) | one normalized modality observation; published to the v10 `EventBus` |
| **Intent** | `interfaces.Intent` (+ `IntentType`, 52 members) | a *request* — type, target, point, params, confidence, sources, `requires_confirmation` |
| **Target** | `interfaces.ScreenTarget` | something on screen (text, bbox, confidence, `source`: gaze/selection/context…) |
| **Context** | `interfaces.ContextState` / v10 `ContextEngine` | focused app/window, browser state, gaze target (2 s TTL), selection, recent action |
| **Action** | `interfaces.IntentType → actions.ActionType` (32 members) | planned + executed by the v10 `ActionEngine` after the safety gate |
| **Verification** | `actions.ActionReport` / `verification.ActionVerifier` | expected vs observed; success/failure feeds learning |
| **Prediction** | `intelligence.prediction.Prediction` | kind/value/confidence/reason/alternatives — **DATA ONLY** |
| **LearningEvent** | `agent._learn_from_report` → `IntelligencePlugin.record_action/…` | verified actions only; never raises |
| **Workflow** | `intelligence.workflows.Workflow/WorkflowStep` | validated steps; approval-gated execution |
| **Plugin** | `intelligence.plugin.IntelligencePlugin` | the optional intelligence facade (never raises) |

## 2. The one integration point: `InteractionAgent` overrides

`agent.InteractionAgent(config, **overrides)` accepts injected
implementations. The v11.5 override keys (alongside all v10 keys —
`safety`, `screen`, `fusion`, `executor`, `system_executor`,
`file_executor`, `browser_executor`, `event_bus`, `context_engine`,
`gesture_registry`, `rf`, `browser`, `voice_engine`, `hands_free`,
`program_runner`, …):

```python
overrides = {
    "intelligence":     your_plugin,        # IntelligencePlugin-compatible facade
    "world_model":      your_world_model,   # world_model.WorldModel-compatible
    "text_controller":  your_text_control,  # text_control.TextController-compatible
    "voice_typing":     your_voice_typing,  # dictation_text.VoiceTypingEngine-compatible
    "transcription":    your_transcription, # transcription.LiveTranscriptionEngine-compatible
    "fusion2":          your_fusion2,       # fusion2.FusionEngine2-compatible
    "modes":            your_modes,         # modes.ModeController-compatible
}
```

Minimal example — a custom intelligence facade:

```python
from airmouse.agent import InteractionAgent
from airmouse.actions import MockExecutor

class MyIntelligence:
    state = "available"
    available = True
    learning_active = True
    def record_action(self, action, history=(), success=True): ...
    def record_command(self, command, hour=None): ...
    def record_text(self, text): ...
    def record_correction(self, raw, preferred): ...
    def observe_gesture(self, gesture, amplitude=0.0, success=True): ...
    def observe_gaze(self, offset_x=0.0, offset_y=0.0, success=True): ...
    def observe_voice_command(self, phrase, canonical=""): ...
    def predict_next_action(self, history): return None   # or a Prediction
    def predict_command(self, hour=None): return None
    def suggest_emoji(self, text, k=3): return []
    def complete_text(self, prefix, k=3): return []
    def suggestions(self, history=(), hour=None): return []
    def apply_emoji_preference(self, text, emoji): ...
    def pause_learning(self): ...
    def resume_learning(self): ...
    def set_privacy_mode(self, on): ...
    def delete_learned_data(self): return {}
    def reset_personalization(self): ...
    def clear_history(self): return 0
    def export_profile(self, path): return True
    def import_profile(self, path): return {}
    def save(self): return {}
    def status(self): return {"state": self.state}

agent = InteractionAgent({...}, executor=MockExecutor(),
                         intelligence=MyIntelligence())
```

The agent treats the facade duck-typed and **guarded**: if your object
raises, the learning path swallows the exception (learning must never
break the action pipeline). To make the core behave *as if* the plugin
is absent, pass `None` — every consumer checks.

The same rule applies to the other overrides: `WorldModel` accepts
`None` context engines; `TextController` accepts any executor with
`hotkey()/key()/type_text()`; `TextExecutor` wraps a backend that may
be absent (calls are always recorded and failures return `False`,
never raise).

## 3. Contracts your integration must honor

### 3.1 The never-raise plugin contract

1. Public entry points catch their own exceptions and degrade to a
   documented no-op (empty list, `None`, `False`, `{}`).
2. Failures surface as **state** (`IntelligenceState`:
   available/disabled/unavailable/corrupted/incompatible/
   out_of_memory/privacy_paused/learning_paused) and `last_error`,
   not exceptions.
3. The core must function when your component is absent, disabled,
   corrupted, incompatible, or out of memory — design for the
   "not contributing" case first.

### 3.2 The PREDICTION ≠ EXECUTION rule (non-negotiable)

* Predictions, suggestions and workflow discoveries are **data**. Your
  component must never execute computer actions from them.
* If you produce intent candidates, mirror
  `FusedIntentCandidate.executable`: no conflicts, confirmation not
  required, and at least one **non-prediction** signal.
* Destructive anything (see
  `intelligence.workflows.is_destructive_action` and
  `fusion2.is_destructive_intent_name`) requires the explicit
  confirmation flow. Low confidence means **ASK**, never guess.

### 3.3 Privacy contract

* Store patterns, not content. Run sensitive data through
  `intelligence.memory.is_sensitive` / `scrub_pattern` before
  persisting (fail-closed: refuse rather than redact-and-keep).
* Honor the dashboard flags: learning/memory/vocabulary/
  workflow-learning OFF must stop persistence *immediately* (no
  queueing for later).
* Bound everything: every growing structure needs a hard cap and a
  deterministic eviction rule.

### 3.4 Determinism & boundedness

* Accept explicit `now=` timestamps where timing matters — the whole
  pipeline is CI-testable because of this.
* No threads required by core components; if your integration spawns
  one, it must only publish events or queue data
  (`agent.inject_intent()` / `agent.poll_events()` are the sanctioned
  hand-offs) and never touch executors directly.

## 4. Extending the other seams

* **ASR providers** — implement the `OfflineSpeechProvider` protocol
  (`offline_voice.py`): `name`, `available()`, `transcribe(audio,
  rate)`; register in `detect_providers()`. For streaming, wrap with
  `transcription.StreamingProviderAdapter` or provide native partials.
* **RF hardware** — implement `rf.RFProvider` (v10) or the extended
  `fusion2.RFExtendedProvider` protocol (presence / motion /
  gesture_classification / direction / range / velocity). Without
  hardware, return `available() → False` (`RFNoHardware` is the
  honest default — never fabricate data).
* **Browser transports** — implement `browser.BrowserBridge`
  (`get_state()`, `perform(...)`, returning `False`/`None` on failure,
  never raising).
* **Text backends** — pass a backend with
  `hotkey(list[str]) / key_press(str) / type_text(str)` into
  `text_control.TextExecutor`; semantic/AT backends slot in without
  touching the controller.
* **Mode phrases** — extend `modes.MODE_REGISTRY` phrase tables (exact
  deterministic utterance → action id) rather than free-text parsing.

## 5. Testing your integration

* Run everything headless: the deterministic simulators
  (`MockExecutor`, `SimulatedStreamingProvider`,
  `SimulatedBrowserBridge`, `SimulatedRFProvider`) show the expected
  shape of test doubles.
* Follow `tests/test_v115.py`: drive parsers with hostile inputs
  (§43), assert boundedness (§44), and assert latency budgets (§34)
  for anything on the hot path.
* The full suite must stay green: `python -m pytest tests/ -q`
  (786 tests at v11.5.0).
