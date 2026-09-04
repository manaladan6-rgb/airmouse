# AirMouse v11.5 — Privacy

AirMouse is **local-first by construction**. There is no cloud AI, no
telemetry pipeline, and no network path in any control loop. The v11.5
privacy architecture makes the defaults explicit and the destructive
operations (delete/reset/clear) first-class.

## 1. The dashboard flags (§32)

`airmouse.privacy.PrivacyFlags` defaults:

| Flag | Default | Controls |
|---|---|---|
| `learning` | ON | master learning switch (model + profiles) |
| `memory` | ON | interaction memory recording |
| `transcription_history` | ON | whether transcript segments are retained |
| `vocabulary_learning` | ON | personal terms + corrections |
| `workflow_learning` | ON | workflow discovery observations |
| `telemetry` | **OFF by default** | nothing phones home; the only "telemetry" is a local perf report printed on shutdown |
| `cloud` | **OFF — structurally impossible** | `__post_init__` forces `cloud = False` and `set("cloud", …)` refuses it: **there is no cloud code path to enable** |
| `intelligence_enabled` | ON | the optional plugin master switch |

Connection state machine (`ConnectionState`): `OFFLINE` (default and
guaranteed) / `ONLINE` (your OS may have internet — AirMouse still
talks to nothing) / `PRIVACY` (learning paused, history off).

## 2. What is stored — and what is never stored

Stored locally under `~/.airmouse/`:

| Data | Where | Bounded by |
|---|---|---|
| quantized personal model | `intelligence/model.bin` | ~30 MB capacity budget, refused growth beyond |
| interaction patterns | `intelligence/memory.json` | 5,000 patterns × 200 chars, scrubbed |
| personal terms/corrections | `intelligence/vocabulary.json` | 20,000 terms / 5,000 corrections |
| approved workflows | `intelligence/workflows.json` | 200 × 24 steps |
| tuner state | `intelligence/selftune.json` | 10 bounded tunables |
| transcript history | in-memory only (≤ 500 segments) unless you export it |
| mode artifacts | `lecture.md`, summaries, notes — written **only on export** |

**Never stored anywhere:** passwords, tokens, API keys, credentials
(fail-closed scrubbing refuses them outright), clipboard contents,
full conversations or documents, raw camera frames, raw audio, and —
since there is no upload path — any of the above *off your machine*.

## 3. Privacy mode behavior

`PrivacyDashboard.set_privacy_mode(True)`:

* pauses **all** learning (model, memory, vocabulary, workflows,
  profiles);
* turns transcription **history off** (live captions still work;
  segments are not retained);
* sets the connection state to `PRIVACY`;
* wipes **nothing** — it is a pause, not a delete;
* is itself recorded in the bounded local audit log (≤ 200 entries,
  in-memory only).

`--offline` (v10) additionally hard-blocks the network-dependent
features at runtime; both can be combined.

## 4. Data procedures (§32)

```python
from airmouse.privacy import PrivacyDashboard
from airmouse.intelligence.plugin import IntelligencePlugin

plug = IntelligencePlugin({"enabled": True})
dash = PrivacyDashboard(plugin=plug)          # + transcription_engine=… if running

dash.delete_learned_data()        # wipe ALL learned data (model/memory/vocab/workflows)
dash.reset_model_personalization()# clear gesture/gaze/voice personalization
dash.clear_interaction_history()  # clear interaction memory + transcript history
dash.export_profile("backup.json")# validated export of the learned profile
dash.import_profile("backup.json")# validated + scrubbed per-section import
```

* **Export** produces one JSON bundle (`kind:
  airmouse-intelligence-profile`) containing memory/vocabulary/
  workflows/selftune sections, written atomically (≤ 4 MB per section
  payload).
* **Import** validates *every* section: malformed rows and
  credential-shaped patterns are skipped (the scrubber is fail-closed
  on import too); counts of accepted items are returned. The fuzz
  suite in `tests/test_v115.py` §43 proves hostile files never crash
  the importer.
* Every dashboard action is appended to the local audit log.

## 5. Telemetry & network

* `telemetry` flag is OFF by default; enabling it only extends the
  **local** shutdown report — no code path sends anything anywhere.
* The v10 `OfflineGate` can block cloud ASR/TTS, browser CDP, software
  updates, and telemetry uploads at runtime; `airmouse offline-test`
  proves the whole stack (now 18 checks) works with sockets **really**
  refused at the syscall level.
* The browser extension (v10) talks only to `127.0.0.1:17843` and
  masks password fields.

## 6. The honest cloud statement

`PrivacyFlags.cloud` cannot be turned on because **no cloud capability
exists**: there is no cloud client code, no upload endpoint, no API
key handling, and no remote inference anywhere in the package (the
optional v5 `SpeechRecognition` extra is the one documented exception
from the v5 era — it is bypassed when `offline = true`). "Cloud" in
AirMouse is a state that the type system refuses, not a feature
waiting to be enabled.
