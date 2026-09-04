# AirMouse v10.0.0 — Final Verification Report

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
