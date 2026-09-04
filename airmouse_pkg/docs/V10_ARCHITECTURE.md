# AirMouse v10.0.0 — Architecture ("Universal Offline Interaction Engine")

This document describes the v10 data flow, event model, module
responsibilities, intent→action mapping, sensor-combo degradation,
threading model, and the extension points for adding new ASR providers,
RF hardware, and browser capabilities.

Everything in the v10 core is **stdlib-only, deterministic when
timestamps are injected, and runs with networking disabled**. Hardware
adapters are guarded, optional, and degrade gracefully.

---

## 1. Data flow (full pipeline)

```
                          ┌──────────────────────────────────────────────┐
                          │              MODALITY SOURCES                │
   🎤 VOICE               │  OfflineVoiceEngine ── VAD → wake word →     │
      (local ASR:         │    OfflineSpeechProvider.transcribe          │
       simulated /        │  voice_commands.py: deterministic template   │
       pocketsphinx /     │    grammar (75 commands, 10 namespaces)      │
       vosk / whisper)    │  nl_control.py: v9 natural phrasing          │
   🖐 HAND                │  gestures.py (v5 FSM) + gesture_registry.py  │
   👁 GAZE                │  gaze.py + gaze_filter.py (v6/v9)            │
   📡 RF (optional)       │  rf.py: RFProvider → RFBridge                │
   ⌨ MOUSE / KEYBOARD     │  keyboard.py / mouse monitors                │
   🖥 SCREEN              │  screen_perception.py (v7)                   │
   🌐 BROWSER             │  browser.py + browser_bridge.py (+ MV3 ext)  │
                          └───────────────┬──────────────────────────────┘
                                          │  normalized interfaces.Event
                                          ▼
        ┌──────────────────── EVENT BUS (eventbus.py) ───────────────────┐
        │  bounded per-subscriber queues (drop-oldest, never blocks      │
        │  producers) · history ring · publish/poll stats · local only   │
        └───────┬───────────────────┬─────────────────────┬──────────────┘
                ▼                   ▼                     ▼
         FUSION (v7)         CONTEXT ENGINE (v10)   DIAGNOSTICS / HUD
   MultimodalFusion           ContextEngine: focused app/window,
   arbitration, priorities,   browser state, gaze target (TTL 2 s),
   confirmations              selection, recent action, deictic refs
                │                   │
                ▼                   ▼
          ┌────────────  INTENT ENGINE (v8/v10)  ────────────┐
          │  fusion decisions + NL utterances + injected     │
          │  intents (voice grammar / gesture registry / RF  │
          │  / browser resolver) → 52-member IntentType      │
          └───────────────────────┬──────────────────────────┘
                                  ▼
     ╔══════════════════ SAFETY SYSTEM (v8/v10) ═══════════════════╗
     ║  e-stop latch · rate limiter · confidence gates · sensitive ║
     ║  types (SHUTDOWN/RESTART/LOCK/SLEEP/CLOSE_TAB + param-level ║
     ║  destructive refinement for FILE_OP/SYSTEM_OP) · confirm.   ║
     ╚═════════════════════════╦═══════════════════════════════════╝
                               ▼
              ACTION ENGINE (v8/v10) — canonical vocabulary
              52 IntentType → 32 ActionType, v10 param
              normalization, preconditions, dispatch to
              executors: pynput / system / file / browser
                               ▼
              VERIFICATION (v8 + browser §13) — expected vs
              observed (screen state, browser before/after diff)
                               ▼
              RECOVERY (v8) — retry → adjusted retry → notify
              (never auto-retries sensitive/blocked actions)

     ┌──────────────────────── OFFLINE GATE (offline.py) ─────────────────────┐
     │  engaged ⇒ cloud ASR/TTS, CDP, updates, telemetry are BLOCKED;         │
     │  every local path keeps working. network_isolation() really blocks     │
     │  socket syscalls so the whole pipeline is testable offline.            │
     └────────────────────────────────────────────────────────────────────────┘
```

Offline gate and safety wrap the entire pipeline: the gate decides
**which features may run**, safety decides **which actions may fire**.

---

## 2. Event kinds (event bus vocabulary)

Every modality publishes normalized `interfaces.Event` objects. Events
are pure data — never executable content. 14 kinds:

| EventKind | Producer | Typical payload | Consumer |
|---|---|---|---|
| `none` | placeholder | — | — |
| `voice_command` | offline voice / grammar | `{"command", "confidence", "intent"}` | intent, HUD |
| `voice_text` | dictation mode | `{"text"}` | dictation buffer, clipboard |
| `hand_gesture` | gesture registry / v5 FSM | `{"gesture", "point"}` | fusion, intent |
| `hand_motion` | hand tracker | `{"direction", "velocity"}` | fusion |
| `gaze_target` | gaze engine | `{"x", "y", "confidence"}` | fusion, context |
| `gaze_fixation` | dwell detector | `{"duration"}` | hands-free confirm |
| `rf_gesture` | RF bridge | `{"label"}` | gesture registry, fusion |
| `rf_motion` | RF bridge | `{"label"}` | fusion |
| `keyboard_event` | keyboard monitor | `{"key"}` | fusion |
| `mouse_event` | mouse monitor | `{"point"}` | fusion |
| `screen_target` | screen perception | `{"target"}` | context, intent |
| `browser_target` | browser mapper | `{"element"}` | context, browser intent |
| `system_notice` | any subsystem | `{"message"}` | diagnostics, HUD |

Bus guarantees: `publish` is O(subscribers) and **never blocks a
producer** — slow subscribers drop the OLDEST queued events; a bounded
history ring serves diagnostics (`bus.history()`), and `bus.stats()`
reports published/dropped counters per kind. All methods accept `now`
for deterministic tests.

---

## 3. Module responsibility table (v10 modules)

| Module | LOC* | Responsibility |
|---|---|---|
| `interfaces.py` | 890 | Shared contracts (extended in v10): `Event`/`EventKind` (14), `VoiceMode`, `CommandNamespace` (10), `ContextState`; `Modality` gains RF + BROWSER; `IntentType` 52 members, `ActionType` 32 members |
| `eventbus.py` | 323 | `EventBus`, `Subscriber`, `MultiSubscriber` — in-process pub/sub, bounded queues (drop-oldest), history ring, stats; works with networking disabled |
| `voice_commands.py` | 540 | Deterministic command grammar + registry: 75 commands across 10 namespaces, `<slot>` entities (direction/app/name/query/url/target/number/text/what), literal-specificity + namespace-priority resolution, ambiguity flag, sensitive-command flags |
| `offline_voice.py` | 905 | Full local voice subsystem: `OfflineSpeechProvider` protocol; `SimulatedSpeechProvider`, `PocketSphinxProvider`, `VoskProvider`, `WhisperProvider` (guarded imports, `detect_providers()`); `EnergyVAD` with hysteresis; `WakeWordGate`; `DictationBuffer` with commit markers; `voice_match_to_intent` (context-aware); `OfflineVoiceEngine` COMMAND/DICTATION/HYBRID |
| `context.py` | 364 | v10 `ContextEngine`: focused app/window, browser state (active tab/URL), gaze target with 2 s TTL, selection, recent action, `snapshot()` (independent copy), deictic resolution ("that/this/it" → concrete target) |
| `gesture_registry.py` | 424 | Full gesture vocabulary (v5 superset + `pinch_hold`/`pinch_release`/`double_pinch`/`grab`/`grab_move`/`circular_cw`/`circular_ccw`/directional motion); built-in + user-defined `CustomGestureMapping` sequence patterns (JSON at `~/.airmouse/gestures.json`, env `AIRMOUSE_GESTURES`); deterministic sequence matcher; double-pinch synthesis (two PINCHes within `double_pinch_window`, default 0.6 s) |
| `rf.py` | 228 | `RFProvider` protocol (`available()`/`poll()`), `SimulatedRFProvider` (scripted), `DummyRFProvider` (documents degradation), `RFBridge` → event bus + gesture-registry feed; **optional hardware — idles cleanly when absent** |
| `system_actions.py` | 643 | 16 `SYSTEM_OPS` (volume/media/lock/sleep/shutdown/restart/brightness/bluetooth) + 8 `FILE_OPS` (open/create/rename/copy/move/delete/…); **shell-free argv-only subprocess**; allowlisted base roots; `sanitize_file_name`; `validate_url` (http/https/file only); `MockSystemExecutor`/`MockFileExecutor` doubles |
| `actions.py` | 1063 | Action engine (extended): canonical §10 vocabulary, v10 param normalization, destructive-op confirmation flags, executor injection (system/file/browser added to pynput/mock) |
| `browser.py` | 1948 | §11–§13 three layers: `BrowserBridge` protocol → `SimulatedBrowserBridge` (deterministic, per-tab history) / `CDPBrowserBridge` (guarded, stdlib urllib + minimal RFC 6455 websocket); `BrowserTargetMapper` + `SemanticBrowserResolver` (fixed template grammar only — page text is DATA, never commands); `BrowserActionVerifier` (before/after state diff); `BrowserController` wiring bus + context + offline gate |
| `browser_bridge.py` | 331 | `BrowserBridgeServer` — **localhost-only (127.0.0.1:17843)** HTTP endpoint for the MV3 extension (POST /state ≤ 256 KB, GET /state, GET /health); `verify_bridge_server()` round-trip on an ephemeral port |
| `offline.py` | 367 | `OfflineGate` (blocks `cloud_asr`/`cloud_tts`/`browser_cdp`/`software_update`/`telemetry_upload`); `network_isolation()` — REAL socket-level blocking (monkeypatches `connect`/`connect_ex`/`create_connection`, loopback passthrough by default, `block_localhost=True` for strict mode); `run_offline_selftest()` — 13 checks over the whole stack |
| `hands_free.py` | 585 | 8 named sensor combos (`voice_only` … `full_fusion`), `SensorHealth` freshness tracker, `effective_combo()` — deterministic largest-alive-subset downgrade ladder with recovery |
| `safety.py` | 487 | Safety system (extended): `SENSITIVE_TYPES` + SHUTDOWN/RESTART/LOCK/SLEEP/CLOSE_TAB; param-level destructive refinement for FILE_OP/SYSTEM_OP (e.g. `delete` is destructive, `create` is not) |
| `agent.py` | 986 | `InteractionAgent` (extended): `inject_intent()` for externally resolved intents (voice grammar / gesture registry / RF / browser) — they pass the SAME safety gate; `poll_events()` drains optional producers; context engine integration; v10 executor overrides |
| `__main__.py` | 1981 | CLI (extended): `--offline --browser --browser-bridge --gesture --rf`, `--voice-mode command|dictation|hybrid`, subcommands `voice-status / gestures / commands / browser / offline-test / diagnostics`; HUD badges `V10` `CMD` `RF` `BROWSER` `VER` |
| `config.py` | 502 | `[v10]` TOML section: offline, voice_mode10, voice_command_min_confidence, wake_word_required, dictation_max_chars, browser_enabled, browser_bridge_port (17843), browser_cdp_port, gesture_registry_enabled, rf_enabled, rf_min_confidence, macro_max_steps, telemetry_enabled |
| `browser_extension/` | 308 | MV3 extension source (`manifest.json`, `background.js` — 1 s active-tab poll, static `content.js` collector, POSTs to `127.0.0.1:17843`; password fields masked; never dynamic code) |

\* LOC = `wc -l` on the current tree (includes docstrings). v10 net
growth: package total 22,781 lines vs 15,881 at v9.

---

## 4. Intent → action mapping (canonical §10 vocabulary)

`IntentType` (52 members) normalizes to `ActionType` (32 members) in the
action engine. Core mapping:

| IntentType (52) | ActionType (32) | Executor |
|---|---|---|
| `click` / `double_click` / `right_click` / `middle_click` | same names | pointer (pynput/mock) |
| `move` / `drag` / `drop` | same names | pointer |
| `scroll` | `scroll` | pointer |
| `zoom` | `zoom` | pointer (ctrl+wheel) |
| `type` | `type` | keyboard |
| `hotkey` / `key_press` | `hotkey` / `key_press` | keyboard |
| `copy` / `paste` / `undo` / `redo` / `select` | same names | keyboard |
| `open` | `open_app` | system/file |
| `close` / `minimize` / `maximize` / `restore` / `focus` / `switch_window` / `snap` | `close_app` / `focus_window` / `switch_window` (window family) | keyboard/system |
| `play` / `pause` / `media` | `media` | system |
| `volume` / `brightness` / `bluetooth` | `volume` (brightness/bluetooth via `system_op`) | system |
| `lock` / `sleep` / `shutdown` / `restart` | `system_operation` (destructive refinement at param level) | system |
| `back` / `forward` / `refresh` | `navigate` | browser/keyboard |
| `navigate` / `open_url` | `navigate` / `open_url` | browser |
| `new_tab` / `close_tab` / `switch_tab` | same names | browser |
| `file_op` | `file_operation` (allowlisted roots, destructive at param level) | file |
| `system_op` | `system_operation` (16-op allowlist) | system |
| `browser_op` | `browser_operation` | browser |
| `dictate` | (text pipeline) | dictation buffer |
| `cancel` / `confirm` / `repeat` / `emergency_stop` | control intents — safety/meta layer | — |
| `none` | `none` | — |

Every action passes: safety gate → preconditions → executor dispatch →
observation → verification → recovery (§1 diagram). Destructive ops are
flagged at the intent level (`shutdown`, `restart`, `lock`, `sleep`,
`close_tab`, sensitive FILE_OP/SYSTEM_OP params) and require an explicit
confirmation before the executor is touched.

---

## 5. Combo degradation ladder (example)

`hands_free.HANDS_FREE_COMBOS` defines 8 named combos; `SensorHealth`
tracks last-seen timestamps per modality (`gaze`, `voice`, `hand`, `rf`);
`effective_combo(wanted, health, now)` downgrades to the **largest alive
subset** of the wanted set and recovers automatically when sensors
return.

```
wanted = full_fusion {voice, gaze, hand, rf}

t=10.0  all sensors fresh        → full_fusion      (voice+gaze+hand+rf)
t=12.0  RF idle (never present)  → voice_gaze_hand  (largest alive subset)
t=15.0  hand lost (stale)        → gaze_voice       (or voice_hand/gaze_hand)
t=18.0  gaze also stale          → voice_only       (minimum combo)
t=22.0  gaze + hand return       → voice_gaze_hand  (recovers, no restart)
```

Ties between equally-sized alive subsets resolve deterministically by
combo declaration order. The ladder never *upgrades* mid-command: an
already-arbitrated intent finishes under the combo it started with.

---

## 6. Threading model

**Deterministic main loop (single thread).** Fusion, intent, safety,
action, verification, recovery, context, and the HUD all run on the
main loop (`agent.process_frame` / `__main__` tick). All core classes
accept explicit `now=` timestamps — with injected time the entire
pipeline is reproducible and CI-testable.

**Producer threads (daemon, guarded, optional).** Only I/O-bound
producers get their own threads; they never call executors directly —
they publish events or queue data that the main loop consumes:

| Thread | Source | Notes |
|---|---|---|
| `VoiceCommandEngine` (v5, optional extras) | `voice_control.py` | mic listener, error backoff; v5 behavior unchanged |
| `OfflineVoiceAudio` (v10) | `offline_voice.py` | RMS → `EnergyVAD` → wake gate → provider transcribe; feeds transcripts/intents thread-safely |
| `BrowserBridgeServer` (v10) | `browser_bridge.py` | `http.server` with `daemon_threads`, bound to 127.0.0.1:17843; stores latest state dict only |
| RF provider (v10) | `rf.py` | polled from the main loop by default; hardware providers may add their own thread behind `RFProvider.poll()` |

Cross-thread contracts: every shared structure is lock-protected or
queue-based; producers never block (bounded queues, drop-oldest);
`agent.inject_intent()` / `agent.poll_events()` are the only sanctioned
hand-off points into the main loop.

---

## 7. Extension points

### 7.1 Adding an offline ASR provider

Implement the `OfflineSpeechProvider` protocol next to the built-ins in
`offline_voice.py`:

```python
class MyASRProvider:
    name = "my-asr"
    def available(self) -> bool: ...          # import/model probe, no network
    def transcribe(self, audio: bytes, rate: int) -> Optional[str]: ...
```

Then register it in `detect_providers()` so `airmouse voice-status`
reports it. The engine never touches the network; providers that fail
to import simply report `available() == False` and the engine degrades
to transcript injection (`feed_transcript`) — exactly how the sandbox
runs today with no ASR engine installed.

### 7.2 Adding RF hardware

Implement `RFProvider` (`rf.py`) — `name`, `available()`,
`poll(now) -> List[RFEvent]` where `RFEvent.kind ∈ {"gesture","motion"}`
and `label` is one of the registry's RF gesture labels. Wrap it:

```python
bridge = RFBridge(provider=MyRadar(), bus=bus, registry=gesture_registry)
# main loop, each tick:
bridge.poll(now=t)        # publishes rf_gesture/rf_motion events
```

RF is **never mandatory**: with no provider (or `rf_enabled = false`)
the bridge reports unavailable and polls to `[]` forever — the system
downgrades via the combo ladder. Run `airmouse --rf` to enable.

### 7.3 Adding a browser capability

Three independent layers (extend any one):

1. **Transport** — implement `BrowserBridge` (like
   `SimulatedBrowserBridge`/`CDPBrowserBridge`): `get_state()`,
   `perform(action, element, params)`, action history. Return
   `False`/`None` on failure, never raise.
2. **Semantics** — `SemanticBrowserResolver` matches a FIXED template
   grammar against the mapper's element map. Add a family by extending
   the resolver's pattern table and `BROWSER_ACTIONS`; page text is
   matched against, never executed.
3. **Verification** — `BrowserActionVerifier` diffs before/after
   `BrowserState`; new actions only need a meaningful state delta to
   verify.

The localhost bridge server (`browser_bridge.py`, port 17843) and the
MV3 extension are the reference transport; a CDP-only setup needs no
extension (`--browser` with `browser_cdp_port` configured).

---

*See `../README.md` for the user guide, `../VERIFICATION_REPORT.md`
for measured evidence, and `../CHANGELOG.md` for version history.*
