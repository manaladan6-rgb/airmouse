# AIRMouse FULL-SPECTRUM AUDIT — v15.1.0

**Audit date:** 2026-09-04 · **Audited tree:** `ed29326` (= tag `v15.1.0`, main == origin/main, tree clean)
**Method:** 5 independent read-only deep-audit passes over every subsystem + coordinator re-verification of all headline claims (tests re-run, P0 crashes re-proven by direct code read + runtime probe, checksums re-verified). Every claim below cites `file:line` or an actual command run in the audit sandbox (Linux x86_64, headless, Python 3.12.14).
**Honesty rule in force:** no simulation is counted as physical validation; no claim without code evidence.

---

## 1. Executive Summary

AirMouse v15.1.0 is a real, tested, honestly-labeled codebase whose **automated surface is exactly what it claims** (1312 tests green — re-verified in this audit) but whose **live end-to-end experience has three critical breaks the test suite cannot see**, because no test executes `main()`'s startup path:

1. **P0 — `airmouse --voice` crashes on startup** (`UnboundLocalError: voice_engine10`, read at `__main__.py:1214`, first assigned `:1234` — confirmed by direct read and runtime probe). Consequence: **no microphone ever feeds the shipped app.** The entire voice stack (wake word, grammar, dictation, ASR, voice+gaze fusion) is unreachable live.
2. **P0 — the v9 gesture-ownership gate is inert and crash-prone** (`__main__.py:1725-1738`): the "hands ignored" nulling is overwritten at `:1738`, so hands-free/gaze modes still fire direct mouse actions (double-control), and `--fusion --gaze` hits a second `UnboundLocalError` (`gesture` read at `:1728` before assignment at `:1738`).
3. **P0 — the privacy lifecycle clears the wrong data**: `airmouse memory reset/delete/export` operate on five stores that are only ever written empty by setup (`setup_wizard.py:276-289`), while real learning persists to hardcoded `~/.airmouse/intelligence/*`, `calibration.json`, `gaze_calibration.json`, `gestures.json`, `macros/`, `lecture.md` — none of which reset/delete touch.

Beyond the P0s, the audit found a consistent pattern: **a strong, tested core (single-hand gesture mouse, filters, gaze pipeline, recovery ladders, permission hierarchy, persistence layer) wrapped in an aspirational superstructure that is partially unwired** — 10 of 25 registry gesture labels have no detector; the intelligence/personalization/self-tuning layer is never fed at runtime; fusion2, the AIP SDK execute path, and the Universal Target Resolver chain are decorative or simulated; the classic gesture path bypasses intent/safety/permissions entirely (an accidental "OK" fires Alt+F4 with zero gating beyond a 0.25 s cooldown).

**Overall system score: 5.4 / 10** (scored honestly, section 26). Automated validation: excellent. Physical real-world validation: zero (all honestly labeled). The fastest path to a dramatically better AirMouse is not new features — it is wiring what already exists: fix 3 P0s, route gestures through the safety engine, feed the personalization layer, put a real transport on the AIP protocol, and give setup a calibration/teaching layer. That is the v15.2 / v16.0 roadmap in section 30/33.

---

## 2. Repository State

| Check | Result |
|---|---|
| HEAD | `ed29326` — "release: v15.1.0 artifacts" |
| Tag `v15.1.0` | Exists (annotated), points to HEAD ✅ |
| Branch | `main`, up to date with `origin/main` ✅ |
| Working tree | Clean ✅ |
| Historical tags | `v3.1.0 v3.2.0 v4.0.0 v4.1.0 v4.2.0 v5.0.0 v9.0.0 v10.0.0 v11.5.0 v12.0.0 v13.0.0 v13.5.0 v14.0.0 v14.5.0 v15.0.0` all intact ✅ |
| Release artifacts | `release/RELEASE_MANIFEST_v15.1.0.md`, `SHA256SUMS_v15.1.0.txt`; wheel 474,542 B + sdist 441,648 B verified **byte-exact by sha256sum -c** ✅ |
| Version | `pyproject.toml` 15.1.0; `python -m airmouse --version` → "AirMouse v15.1.0 — Adaptive Human-Computer Intelligence Edition" |
| Test suite (re-run in this audit) | **1312 passed / 0 failed / 17 warnings / 21.17s** ✅ |
| Repo hygiene | ~60 stray tracked files (section 24) |

No history rewritten, no tags moved. The v15.1.0 hardening release is protected and verified.

---

## 3. Architecture Map (as actually wired — not as documented)

### Pipeline A — Hand/Gesture (the core product)
```
Camera (cv2 640×480@30, mirrored)
  → tracker.py HandTracker.read()            [MediaPipe Tasks, VIDEO mode, max_num_hands=1
                                              (tracker.py:43), handedness NEVER read, normalized
                                              coords, synthetic +33ms timestamps (tracker.py:79)]
  → gestures.py recognize_gesture()          [angle-based 14-pose classifier, per-finger 15°
                                              hysteresis (gestures.py:126-163)]
  → GestureStateMachine.update()             [4 action-confirm frames, 0.12s transition cooldown,
                                              per-gesture cooldowns (gestures.py:397-441)]
  → __main__.py main loop (:1683-2433)       [★ dispatch at :1812-2107 calls pynput DIRECTLY —
                                              bypasses intent.py / safety.py / permissions.py]
  → physics.DirectTracker (One Euro ⊕ Kalman) → MouseController.move_to
```
**Parallel/decorative branches off Pipeline A:** `gesture_registry.py` (custom mappings — fed only by RF events, `agent.py:565-569`, **never by hands**); `fusion2.py` (constructed `__main__.py:1290`, injected `agent.py:420`, **never called**); `intelligence/personalization.py` + `selftune.py` (observers exist, **zero runtime call sites**). `intent.py` receives gestures only in v9-owned modes via `agent.process_frame → submit_gesture` (and only resolves pinch/peace/thumb — `"thumbs_up"` key mismatch silently drops thumbs-up, `intent.py:120-124` vs `__main__.py:2216`).

### Pipeline B — Voice (live status: **BROKEN at startup**)
```
Microphone → (v5 SpeechRecognition energy VAD | v10 EnergyVAD hysteresis 420/260)
  → wake word ("airmouse" variants) → grammar (65 deterministic CommandSpecs, voice_commands.py)
    / NL fallback (22 regex patterns, nl_control.py) / dictation buffer
  → ASR providers (detect_providers(): simulated | pocketsphinx | vosk | whisper — honest labels)
  → intent → safety → action
```
**Live reality:** `airmouse --voice` crashes before any of this runs (P0-1). `OfflineVoiceEngine.start_audio` has **no production caller** (transcript-injection only); `--transcribe`/`--dictation` engines are constructed but never fed; `AIRMOUSE_VOICE_TEXT` CI hook is a comment with no reader (`__main__.py:1332`). The only networked-audio channel (v5 Google ASR, `voice_control.py:516`) is unreachable due to P0-1.

### Pipeline C — Gaze (live, best-wired advanced subsystem)
```
FaceMesh (refine_landmarks=True → iris 468-477, gaze.py:174-183)
  → per-eye iris-offset mapping (x=0.5+gx·0.35, gaze.py:352-371)
  → GazeFilterPipeline (outlier hold, conf+velocity-adaptive EMA, saccade snap — gaze_filter.py)
  → 9-point affine calibration (gaze_calibration.py, quality gates good≤40px/fair≤100px)
  → DwellDetector (0.80s in 0.045 radius) / BlinkClassifier (long-blink = E-STOP)
  → agent.process_frame → fusion → intent → safety → PynputExecutor
```
Caveat: the whole fusion/intent tick runs **only when the camera window is visible** (`if config.show_camera and debug_mode`, `__main__.py:2194`) — `--no-cam` or pressing `[d]` disables the entire multimodal pipeline.

### Pipeline D — Agent (protocol real, execution simulated)
```
AI Agent → agent-sdk-js / agent-core (stdlib-only, verified) → AIP envelope (20 verbs, strict
  fail-closed validation, 256KB cap, version negotiation)
  → ⚠ NO WIRE SERVER EXISTS (no --aip-stdio mode in __main__.py) — cross-process agents
    have nothing to connect to; only in-process import works today
  → AipEndpoint.execute → permission gate (real, fails closed) → TaskEngine
    → ⚠ returns {"verified": True, "message": "simulated execution verified"} —
      NEVER reaches PynputExecutor (agent_sdk.py:204-228)
```

### Where the paths actually connect
| Junction | Connected? |
|---|---|
| Gesture → direct pynput action | ✅ live (bypasses safety) |
| Gesture → intent/fusion | ⚠️ only when v9 agent owns actions — and the ownership gate is broken (P0-2) |
| Gaze → fusion → intent → safety → action | ✅ live (camera window visible) |
| Voice → anything | ❌ dead (P0-1) |
| Gesture registry / macros → hands | ❌ not connected (RF/hotkey only) |
| Personalization → gesture thresholds | ❌ not connected (AdaptiveCalibration is the exception — fully live) |
| AIP → real OS execution | ❌ simulated execution only |

---

## 4. Gesture Engine Audit

**16 gestures are live-recognizable** (14 static poses + left/right swipes) plus behavioral variants (trackpad pinch tap/hold, peace tap/hold, pinch-hold zoom). Full inventory with detection math, cooldowns, and wiring per gesture (verbatim from the audit):

| # | Gesture | Detection (file:line) | Cooldown / hysteresis | Action | Personalized | Verified | Recovery | Physically tested |
|---|---------|----------------------|----------------------|--------|--------------|----------|----------|-------------------|
| 1 | pinch → click | 3D thumb-index < 0.07 + middle down (gestures.py:236-259) | 4 confirm frames; 0.25s cooldown | `mouse.left_click()` | ❌ | ❌ | ❌ silent | **NO** |
| 2 | pinch-hold → zoom | hold ≥0.30s then Δy ticks (zoom.py:109-149) | 0.30s engage hold; tick deadzone 0.015 | Ctrl+wheel | ❌ | ❌ | ❌ never raises | NO |
| 3 | pinch release | implicit (state machines) | — | ends drag / quick click | ❌ | ❌ | ❌ | NO |
| 4 | double pinch | **no live detector** (registry-synthesized from RF only, gesture_registry.py:309-316) | 0.6s window | DOUBLE_CLICK — dead | ❌ | ❌ | ❌ | NO |
| 5 | palm → drag | ≥4 fingers up (gestures.py:282) | 3 movement frames | start/stop_drag | ❌ | ❌ | ✅ force-release on hand loss (:2181) | NO |
| 6 | trackpad pinch-hold → drag | hold >0.35s (__main__.py:1846) | 0.35/0.25 split | start_drag | ❌ | ❌ | ❌ | NO |
| 7 | swipe L/R | mean Δx > ±0.4 over 4 frames (gestures.py:316-346) | 0.5s cooldown | browser back/forward (Alt+←/→) | ❌ (GestureProfile never fed) | ❌ | ❌ daemon thread | NO |
| 8 | three → scroll | 3-finger pose + Δy×80 (gestures.py:278, __main__:2032) | accum gate 0.5 | mouse.scroll | ❌ | ❌ | ❌ | NO |
| 9 | fist → freeze cursor | 0 fingers up (gestures.py:276) | 4 frames | boolean freeze + audio | ❌ | n/a | n/a | NO |
| 10 | peace → right click | index+middle up (gestures.py:280) | 4 frames + 0.25s | right_click | ❌ | ❌ | ❌ | NO |
| 11 | peace-hold → scroll (trackpad) | hold >0.25s (:1877) | tap max 0.25s | scroll | ❌ | ❌ | ❌ | NO |
| 12 | thumbs_up → double click | thumb angle >150° (gestures.py:173-181) | 4 frames | double_click | ❌ | ❌ | ❌ | NO |
| 13 | pinky → middle click | pinky only (gestures.py:272) | 4 frames | middle click | ❌ | ❌ | ❌ | NO |
| 14 | OK → **close window (Alt+F4)** | thumb-middle <0.07 (gestures.py:242) | 4 frames + 0.12s | KeyboardActions.close_window | ❌ | ❌ | ❌ | NO |
| 15 | gun → show desktop | thumb+index (gestures.py:262) | 4 frames | Win+D | ❌ | ❌ | ❌ | NO |
| 16 | rock → minimize | index+pinky (gestures.py:268) | 4 frames | minimize | ❌ | ❌ | ❌ | NO |
| 17 | shaka → volume mode | thumb+pinky (gestures.py:184-196) | Δy>0.02 + 0.15s | volume up/down | ❌ | ❌ | ❌ | NO |
| 18 | ring → brightness mode | ring only (gestures.py:274) | Δy>0.03 + 0.3s | brightness | ❌ | ❌ | ❌ | NO |
| 19 | six → task switcher | thumb+index+pinky (gestures.py:266) | 4 frames | Alt+Tab | ❌ | ❌ | ❌ | NO |
| 20 | point → cursor move | index only (gestures.py:284) | 3 frames | DirectTracker → move_to | ✅ AdaptiveCalibration | n/a | reset on loss | NO |
| 21 | macro record/replay | hotkey [m]/voice only (:2375) | — | MacroPlayer **without safety gate** in classic path (:1466) | ❌ | ❌ | ❌ | NO |

Dead registry vocabulary (labels with **zero producers**): `pinch_hold`, `pinch_release`, `double_pinch` (hands), `grab`, `grab_move`, `circular_cw`, `circular_ccw`, `directional`, `swipe_up`, `swipe_down` (gesture_registry.py:78-90). `GestureStateMachine.stability_frames` stored but never used (gestures.py:388); in direct mode `hand_stable=True` always (`__main__.py:1719`) — stability gating is a no-op where it matters most.

**Test evidence:** 174 gesture-path tests green (test_regression_v5 13, test_macros_v2 32, test_safety 32, test_fusion 35, test_wiring 12, test_intent 50) — all explicitly hardware-free.

---

## 5. Gesture Capability Matrix

| Group | Implemented | Partial | Missing |
|---|---|---|---|
| **Hand poses** | open palm, fist, point, peace, three, pinch, thumbs up, OK, rock, call-me (9) | five-fingers (folds into palm), pinch-hold (behavior without event) | **four fingers, thumbs down** |
| **Motion** | swipe L, swipe R | air tap (trackpad tap), air hold (timers, no event) | **swipe U/D, circle CW/CCW, push, pull, flick, shake, wave, draw shape, air double-tap** |
| **Multi-step** | pinch→move (drag), pinch→release (implicit) | — | **point→pinch, palm→fist, swipe→pinch, circle→pinch** (the deterministic sequence matcher exists in gesture_registry.py:129-158 but nothing feeds it) |
| **Two-hand** | — | — | **ALL: zoom, rotate, resize, drag, object manipulation, hand-to-hand, L/R roles** — `max_num_hands=1` (tracker.py:43), only `hand_landmarks[0]` consumed (:85), handedness never read (zero grep hits) |
| **Combos** | gesture+gaze (fusion confirmations, live) | gesture+voice (dead: P0-1), gesture+context (context engine runs, gestures don't consult it) | gesture sequences/macros from hands (registry not fed) |

**Docs vs reality:** the gesture vocabulary is overstated by ~50% relative to what is wired (25 advertised labels vs 16 live recognizers + 2 swipes).

---

## 6. Gesture Reliability Findings

| Mechanism | Evidence | Rating |
|---|---|---|
| One Euro filter | mincutoff 1.2, beta 1.5, dcutoff 1.0 (filters.py:82, config.py:179-181); precision swap 0.5/0.5 (physics.py:690-693) | **STRONG** — correct Casiez implementation |
| Hybrid OneEuro⊕Kalman | q=1.0, r=0.05, Joseph-form update, dt clamp (filters.py:243-269, 393-400) | **STRONG** |
| Deadzone | 0.003 normalized + 1.0px (config.py:174-175) | adequate — but "auto-precision" **doubles** the deadzone when slow while the comment claims it tightens it (physics.py:609-613) — likely inverted logic, unverifiable without hardware |
| Anti-flap | per-finger 15° hysteresis bands (gestures.py:126-135) | **STRONG** |
| Accidental activation | 4 confirm frames, 0.25s click cooldown, 0.30s zoom engage, tap/hold split 0.25/0.35s | adequate — **but confidence is computed and never gates classic-path actions** (HUD-only, `__main__.py:2211`): a borderline OK still fires Alt+F4 |
| Hand loss | 5-frame grace (~166ms), full teardown: filters reset, gsm reset, **drag force-released**, modes dropped (`__main__.py:2145-2191`) | **STRONG** |
| Camera loss | `cap.read()` failure → same path as hand loss (tracker.py:70-72); **no reconnect, no SAFE_MODE downgrade in classic mode** (stream watchdog exists only in v9 path, safety.py:425-468) | weak — dead camera silently looks like "no hand" forever |
| Timestamps | synthetic +33ms regardless of real frame time (tracker.py:79) | adequate; wrong dt under FPS drops degrades filter weighting |
| Handedness / left-right | nonexistent | weak |
| Ironman stack | 5 filters in series (DualStage + OneEuro + Kalman + PositionSmoother α0.75 + accel limit 50000) | adequate but latency-additive; unmeasured |

**Hardware-required evidence (cannot be produced in a sandbox — remains explicitly unverified):** jitter in px, false-positive/false-negative rates per pose per hour, lighting/distance/background sensitivity, pinch_threshold 0.07 robustness at 0.5–2 m, perceptual latency of the filter stack, swipe threshold across arm lengths, occlusion behavior.

---

## 7. Gesture UX Findings

- **What's good:** live HUD with gesture + confidence readout (`__main__.py:2294-2318`); audio feedback on 13 events (audio.py); tutorial with silhouette hints, hold-to-pass, progress dots and detected-gesture readout (tutorial.py:153-314, **wired** at `__main__.py:1072-1081` + hotkey `t`); hotkeys (t/h/g/f/x/d); guarded CLI errors with secret redaction (user_errors.py).
- **What's missing:** no cursor or gesture calibration in setup (app-only `--calibrate`, `--gaze-calibrate`); the guided lab's 5 physical tests show **no detected-gesture/confidence feedback** (pure Y/N prompts, guided_test.py:657-742); no per-test retry (a FAIL records a note and moves on; the `only=` filter exists in the runner but is not exposed via CLI, guided_test.py:302-306); no calibration-adjustment hooks in the lab; two competing first-run markers (menu honors `$AIRMOUSE_HOME` via cli_menu.py:88-116, tutorial hardcodes `~/.airmouse/tutorial_done`, `__main__.py:1073`); OpenCV C++ warnings leak raw to stderr during camera probes (not sanitized) — scary for novices.
- **First-run menu** is real and honest (10 pinned items, TTY+marker gated, cli_menu.py:156-198) — but the README describes a different menu (section 23).

---

## 8. Gesture Personalization

| Component | Would learn | Wired live? | Evidence |
|---|---|---|---|
| `GestureProfile` | pinch style, swipe speeds, amplitude/dwell EMAs, false positives → threshold suggestions | **NO — never fed.** `observe_gesture()` has zero call sites | intelligence/plugin.py:221-232 |
| `GazeProfile` | offset, dwell, drift, false regions | NO (`observe_gaze` uncalled) | plugin.py:234-246 |
| `VoiceProfile` | aliases after 5 votes | NO (`observe_voice_command` uncalled) | plugin.py:248-252 |
| `SelfTuner` | 10 bounded tunables incl. confirm frames, cooldowns, swipe gate | **NO** — propose/apply never invoked at runtime; `gesture_confirm_frames` read once at startup | selftune.py:54-152, `__main__.py:1156-1160` |
| `Predictor` | next-action Markov | ✅ live but **suggestion-only** (HUD text); PREDICTION ≠ EXECUTION properly enforced (prediction.py:14-16, workflows.py:8) | agent.py:905-924 |
| `AdaptiveCalibration` | reach box, speed EMA, tremor → cursor remap + One-Euro auto-tune, persisted atomically | ✅ **INTEGRATED every frame** — the one genuine Observe→Adapt loop | calibration.py:62-307, `__main__.py:1751-1760` |

**Verdict: PARTIAL trending DECORATIVE for gestures specifically.** The classifier's actual thresholds (pinch 0.07, confirm frames, cooldowns) are static config — learned parameters never reach them. The learning that does run is AdaptiveCalibration (cursor ergonomics) and a suggestion-only Markov chain.

---

## 9. Setup Experience

**What `airmouse setup` does today (11 fixed steps, all guarded, consent-gated, statuses DONE/SKIPPED/ACTION_REQUIRED/FAILED — verified by live run):** environment → core packages (installs only on interactive Y, 600s timeout) → storage init (seeds 5 stores) → config.toml → camera probe (real `cv2.VideoCapture(0)`) → mic probe (`sounddevice.query_devices`) → browser detection → optional voice extras (never auto-installed) → keyboard/mouse probe → 15-check automated smoke test → completion marker with an honest "what remains to test" list. Exit 0; real output reproduced in the audit.

**What it does NOT do (the gap to a "Gesture Academy"):** no cursor calibration, no gesture calibration, no hand-positioning guidance, no lighting guidance, no distance guidance, no left/right-hand selection, no interactive test actions, no per-step error-recovery coaching, no OS-permission coaching (camera/mic), no resume-where-you-left-off. Priorities: cursor calibration + hand positioning + lighting check + interactive practice = **P0**; distance + gesture calibration + mic level meter + permission coaching = P1; hand selection = P2.

**Doctor** (12 sections, READY 32 / OPTIONAL 7 / HARDWARE 2 in-sandbox, exit codes 0/1/2) and **verify** (10/10 automated PASS + 5 physical ACTION_REQUIRED) are accurate and honest — verified by live runs.

---

## 10. Eye/Gaze Audit

| Feature | Verdict | Evidence |
|---|---|---|
| Face/iris detection | IMPLEMENTED | FaceMesh refine_landmarks → iris 468-477 (gaze.py:174-183); refuses degenerate output (:243-245) |
| Gaze mapping | IMPLEMENTED | normalized iris offset, x=0.5+gx·0.35 / y=0.5+gy·0.30 (gaze.py:352-371); confidence = face-size × eye-state × head-pose × gaze-ok (:414-460) |
| Calibration | IMPLEMENTED (strong) | 9-point grid, MAD outlier gate, least-squares affine, quality gates good≤40px/fair≤100px, reliability gate, atomic v2 persistence (gaze_calibration.py:94-397); CLI `--gaze-calibrate` real |
| Fixation/dwell | IMPLEMENTED | fixation <0.08 u/s for 0.15s; DWELL at 0.80s in 0.045 radius, fires once, re-arms (gaze.py:604-741); dwell→click through safety (hands_free.py:457-471) |
| Blink/wink | blink IMPLEMENTED (long-blink ≥0.60s = **E-STOP**, on by default); wink DETECTED BUT UNUSED (no consumer anywhere); blink-click default OFF (config.py:275) | gaze.py:481-601 |
| Smoothing | IMPLEMENTED | outlier jump hold, conf+velocity-adaptive EMA, saccade snap, jitter/lag stats (gaze_filter.py) |
| Target selection | IMPLEMENTED (coarse) | calibrated pixels else normalized×screen (agent.py:645-660) → smallest actionable target (screen_perception.py:734-741); accessibility provider = active-window only; **OCR opt-in, off by default** (:458-465); geometry 9-zone fallback |
| Gaze+hand fusion | IMPLEMENTED | pinch confirmation within 0.9s/140px, +0.25 boost (fusion.py:677-692) |
| Hands-free / eye-assist | IMPLEMENTED | HandsFreeController with sensor-degradation ladder (:505-585); FusionMode.ASSIST (fusion.py:132-139) |

**Missing integrations:** wink consumer; blink-click default-on decision; OCR-backed "button" perception off by default; zero physical validation of gaze accuracy (hardware-required); gaze pipeline disabled when camera window hidden (section 3, Pipeline C caveat).

**Score: 7/10** — the best-wired advanced subsystem; everything above is code-verified and unit-tested (55 tests), but no human eye has ever been tracked by it in CI.

---

## 11. Voice Audit

**Honest labeling (verified):** `detect_providers()` live output in sandbox = `{'simulated': True, 'pocketsphinx': False, 'vosk': False, 'whisper': False}`. `SimulatedSpeechProvider` is explicitly labeled "deterministic scripted provider — the test/CI workhorse" (offline_voice.py:135). PocketSphinx/Vosk/Whisper providers are **real code** (`recognize_sphinx`, `KaldiRecognizer`, `whisper.load_model`) but NOT_INSTALLED-dependent; confidences are hardcoded (0.6/0.7/0.75), not engine-reported. The v5 Google cloud path is real networked ASR but unreachable (P0-1). `--transcribe` ships with the simulated provider and **says so** (`__main__.py:1275-1278`). Nothing pretends to be real ASR. ✅

**What works in code (all unit-tested, 114+49+32 tests):** energy VAD with hysteresis; wake word ("airmouse" variants, 10s arming window); 65-command deterministic grammar across 10 namespaces with slot compiler and specificity tie-break (exact 1.0 / tie 0.72 / <0.62 no-match); 22-pattern NL fallback with deictic resolution; dictation buffer with commit markers; VoiceTypingEngine with undo/redo/replace edit commands; spoken punctuation (30+ entries), number words, sentence casing, vocabulary proper nouns; mic selection (`voice_mic_index`); confidence propagation; bounded in-RAM transcript history (500 segments) with privacy gating; **audio is never written to disk**; the only audio egress is the (currently unreachable) Google call.

**What is broken/inert live:** P0-1 startup crash kills the entire pipeline; `start_audio` has no production caller (no mic source for the v10 engine); `AIRMOUSE_VOICE_TEXT` injection hook unimplemented; `--dictation`/`--transcribe` engines constructed but never fed; TTS `speak()` never called.

**Deterministic grammar ≠ local ASR ≠ cloud ASR ≠ simulation — correctly distinguished throughout.** Score: **4/10** (deep tested text-side stack; dead live path).

---

## 12. Multimodal Fusion

- **fusion.py IS live** (via `agent.process_frame`, camera-window-gated): winner-take-all per tick, mode-weight matrix, staleness 0.8s, explicit conflict records (points >140px apart), `hand:pinch` and `voice:click` confirmation boosts (+0.25 each), 0.35 min-confidence gate.
- **fusion2.py is decorative** — constructed, injected, never called (only selftest/offline self-checks call `.fuse()`).
- Conflict resolution = priority scoring + conflict records; no voting (fusion2's voting + destructive-requires-confirmation logic never runs).

**The six mission scenarios, traced to real functions:**

| Scenario | Verdict |
|---|---|
| (a) look at button + pinch → click | **REAL** — full chain wired (and is the v9 design center); needs camera + `--gaze` |
| (b) look + say "click" | PARTIAL — wired and tested, unreachable live (P0-1) |
| (c) point at window + "close this" | PARTIAL — wired; CLOSE is sensitive → confirmation required; window target = active-window bbox only; voice leg dead |
| (d) look at text + "copy that" | PARTIAL — wired (COPY → ctrl+c); voice leg dead; "text" perception requires opt-in OCR |
| (e) point + "open this" | PARTIAL — wired (OPEN → click at point); voice leg dead |
| (f) gesture+voice+gaze simultaneously | PARTIAL — arbitration real, confirmations stack; voice component impossible live (P0-1) |

**Ambiguity handling:** grammar ties flagged `ambiguous` at 0.72; v9 NL suppresses the v8 phrase resolver; intent engine caps one click per tick.

**Score: 5/10** — the gaze+hand half of the dream is genuinely wired; the voice half is dead until P0-1 is fixed, and the whole tick hides behind camera-window visibility.

---

## 13. Computer Control

| Capability | Backend | Class |
|---|---|---|
| Mouse move/click/double/right/middle | pynput via PynputExecutor, clamped coords (actions.py:739-960) | REAL-OS cross-platform, **hardware-UNVERIFIED** |
| Drag / scroll / zoom | press + 8 interpolated moves; scroll; ctrl+wheel (actions.py:874-960) | REAL-OS, unverified |
| Typing / hotkeys | pynput keyboard with alias table (actions.py:748-974) | REAL-OS, unverified |
| Window switch/min/max/close | **hotkey emulation only** (alt+tab, win+down/up, alt+f4) — no window-manager API | REAL-OS keystroke, unverified |
| Clipboard | OS clipboard only via ctrl+c/v keystrokes; `FileActionExecutor._clipboard` is an internal path list, **not** the OS clipboard | REAL-OS keystroke path |
| Volume/media | Linux pactl/playerctl, macOS osascript; **Windows path is dead code** — imports a nonexistent `CrossPlatformKeyboard` (system_actions.py:164-178); legacy keyboard.py has working Windows SendInput but SystemActionExecutor never calls it | Linux/macOS real; **Windows BROKEN via this executor** |
| Lock/sleep/shutdown/restart | rundll32/systemctl/osascript per platform, destructive-flagged (system_actions.py:302-342) | REAL-OS, unverified |
| Files open/create/rename/copy/move/delete | stdlib under realpath root-allowlist + name sanitizer, argv-only subprocess with timeouts | REAL-OS, **tested in-sandbox** (temp dirs) |
| Application launch | **no first-class op exists** | GAP |
| URLs/tabs | CDP navigate or ctrl+l+type fallback; ctrl+t/w, ctrl+1..8 | REAL-OS fallback, unverified |
| Displays | ctypes EnumDisplayMonitors / xrandr / system_profiler | REAL-OS, unverified |

**Bypass finding:** the legacy v5 loop calls `MouseController`/`KeyboardActions` directly (`__main__.py:68,1069,1530-1531`; zoom.py:194) — **these bypass the ActionEngine safety gate**. KeyboardActions fires combos in un-joined daemon threads with failures swallowed (keyboard.py:167-223).

**Simulator separation:** clean at module level; CLI honestly prints "simulated computer — no hardware claimed" (`__main__.py:795-801`). One violation: `AipEndpoint` returns "simulated execution verified" in the *real* agent API surface (section 15).

**Score: 6/10.**

---

## 14. Browser

- **CDP client: REAL code, never tested against live Chrome.** `/json/list` discovery (0.5s timeout), hand-rolled RFC6455 websocket with accept-key verification, `Runtime.evaluate` **only with fixed JS snippets** (collect elements, click by index, focus, json-encoded typing), navigate/tabs/scroll/back/forward/reload (browser.py:544-1128). **Missing: no code launches Chrome with `--remote-debugging-port`** — the user must do it manually; no live-browser test exists (tests cover dead-port/offline negatives only). Known defect: `_ws_for` connects to the unpinned ws host:port from the DevTools response (browser.py:833-834).
- **Extension: REAL (MV3, tabs+scripting, `<all_urls>`), hardware-unverified** — background.js polls 1s, injects static content.js (no eval), POSTs metadata JSON to `127.0.0.1:17843`; password values masked; `untrusted:true` data-only.
- **Bridge server: REAL and exercised** — hard-bound 127.0.0.1:17843, 256KB cap, data-only JSON, loopback POST→GET round-trip **passes in-suite** (tests/test_browser.py:620). No auth token (any local process can POST fake state — loopback + data-only mitigation).
- **Simulated bridge:** deterministic, honestly separate.
- Fixed-JS-only posture means **no arbitrary code execution via page content**; page text is data.

**Score: 7/10** (code-complete localhost-only stack; zero live-Chrome evidence; no launcher).

---

## 15. AI Agents

**Real today:** strict AIP protocol (20 verbs, 12 schemas, fail-closed validation, 256KB cap, same-major version negotiation — adversarially fuzzed); `agent_sdk.py` endpoint with DISCOVER/OBSERVE/TARGETS/REQUEST/AUTHORIZE/EXECUTE/VERIFY/TASK/STOP/STATUS and a permission gate that **fails closed** (no engine ⇒ ASK ⇒ denied, agent_sdk.py:182-228); stdlib-only `agent-core/` (AST-verified imports: json/subprocess/sys/time/typing; never imports airmouse — enforced by test) and dependency-free `agent-sdk-js/`.

**Not real today:**
1. **`execute` is simulated**: post-gate it creates a task and returns `{"verified": True, "message": "simulated execution verified"}` — never reaches PynputExecutor (agent_sdk.py:204-228); `_on_verify` checks a dict that is never populated (:230-236).
2. **No wire transport**: no `--aip-stdio` CLI mode exists (grep-verified) — `agent-core`'s `stdio://` and agent-sdk-js's `StdioTransport("airmouse --aip-stdio")` target a server that doesn't exist. **Cross-process agent use is impossible; only in-process import works.**
3. **Target resolver ships unwired**: `DEFAULT_RESOLUTION_ORDER` (7 providers) defined but `register_provider` is called only by tests with fakes — every out-of-box `resolve_target` returns `ok=False` (target_resolver.py:51-219). No vision or semantic-API provider implementations exist anywhere.
4. agent.py (human pipeline) vs agent_sdk.py (machine facade) are correctly distinct — but they don't share the execution path (SDK bypasses ActionEngine).

**Verification honesty:** post-action verification compares expected vs an injected observer (verification.py:132-173) with real 5×5px pixel read-back where PIL exists; missing observer ⇒ NOT_NEEDED/UNKNOWN — success is **never claimed without observation**. Weakness: the agent-path observer reports the *expected* pointer position as "observed" (tautological, agent.py:935-947).

**Answer to "Can an AI agent use AirMouse as a universal HCI layer today?" — No.** The protocol, permissions, and SDKs are real and safe; the last mile (wire transport + real execution + wired target resolution) is architectural.

**Score: 5/10.**

---

## 16. Multi-Agent

**Real and tested:** registry (bounded 32), identity/capabilities, **exclusive resource leases** (TTL 30s, max 300, re-entrant refresh, GC expiry), conflict policy = **lease holder always wins; priority never steals live leases; challenger waits** (agents.py:278-318), agent-to-agent messages are DATA never executed (:322-337), handoff = release+reacquire+notify, suspend/stop, **`emergency_stop_all()`** stops every non-human agent, releases all leases, trips the permission E-STOP (tested).

**Hierarchy enforcement:** `check()` order = E-STOP denies all → HUMAN_OVERRIDE allow/deny-all → SAFETY-blocked keys → rules (exact > wildcard) → **no rule ⇒ ASK ⇒ denied for agents (fails closed)** → ALLOW_ONCE decrements → ALLOW_PATTERN fnmatch (permissions.py:196-257). E-stop dominance is tested (test_v15.py TestControlHierarchy).

**Defects:** `grant(..., uses=-1)` default makes ALLOW_ONCE grants **unlimited** unless the caller passes `uses=1` (permissions.py:76, 145-147 — fails closed, but the flag is dead-on-arrival); `AgentProfile.budgets` is declared but **never enforced** (`actions_used` merely incremented, agents.py:64 vs 316); the whole layer is single-process/in-memory.

**Score: 6/10.**

---

## 17. Recovery

**Two bounded, safety-gated ladders:**
- `RecoveryEngine` (recovery2.py): 7-strategy escalation RETRY → REOBSERVE → RETARGET → ALTERNATE_MODALITY → ALTERNATE_SEMANTIC_TARGET → ALTERNATE_EXECUTION → REQUEST_HUMAN (+GIVE_UP safe stop); MAX_ROUNDS=6, retry budget ≤4; **safety gate before every execution round** (a crashed gate blocks); `PERMISSION_DENIED → REQUEST_HUMAN` only — **never retried, never escalated** (:110-111); MALFORMED_REQUEST → GIVE_UP fail-closed; every attempt traced.
- `RecoveryManager` (verification.py:352-480): RETRY → +12px adjusted retry → NOTIFY, attempt cap 2, confirmation-gated plans never auto-retry.

**14 failure diagnoses** with deterministic diagnosis→ladder mapping; **12/12 injected failure classes behave** (9 recover + verified, 3 safe-stop — end-to-end through the real engine on the simulator, test names cited in audit). 

**Gaps:** hand disappearance / camera disconnect / voice failure are **not in the ladder** — handled only by the v9 stream-loss watchdog (2s grace → SAFE_MODE allowing MOVE/SCROLL only, safety.py:425-471); diagnosis at the edges is keyword-based (mostly UNKNOWN); **all recovery evidence is simulator-level** — no real-OS recovery has ever been exercised.

**Score: 8/10** (best-engineered subsystem; simulation-only evidence).

---

## 18. Persistence

**`persistence.py` is exemplary**: temp-file → fsync → `os.replace` → dir fsync (atomic, crash-safe, Windows-safe); envelope with `schema_version` + sha256 checksum; forward-version refused, backward migrated ascending, missing migration fails closed; corruption → `.corrupt-<epoch>` quarantine (newest 3 kept); **symlink containment verified** (realpath must stay inside home); exports reject `..`; reset backs up to `<home>/backups/` and returns failure dicts instead of raising.

**The integrity layer is not the layer the app writes through (P0-3):** all real learning goes to hardcoded `~/.airmouse/intelligence/{memory,vocabulary,workflows,selftune}.json` + `model.bin` (plugin.py:30-45, 457-478), plus `calibration.json`, `gaze_calibration.json`, `gestures.json`, `macros/*.json`, `lecture.md` (verbatim user note content), `config.toml` (plain write, not atomic), and the 7.8MB `hand_landmarker.task` (unsigned download). The 5 CLI-managed stores are written **only** as empty `{}` by setup. Consequently `airmouse memory reset/delete/export` and `privacy_report.learned_data` **misreport the deletion lifecycle** (HIGH). `AIRMOUSE_HOME` is honored by persistence/cli_menu/capabilities (post-fix) but **~10 modules hardcode `~/.airmouse`** (MED split-brain; privacy.py even dual-checks both paths — the authors knew).

**Content sensitivity:** no tokens, audio, or screenshots persisted; twin/memory scrub secret-shaped strings (hex64/b64/URL-creds; PATs/API keys/cards); gaze calibration fingerprints screen geometry; export embeds the absolute home path.

**Score: 8/10 for the storage layer; the lifecycle honesty defect is P0.**

---

## 19. Security (red-team re-run)

**Fresh whole-package scan: zero hits** for `shell=True`, `eval(`, `exec(`, `os.system`, `os.popen`, `pickle`, `yaml.load`, `marshal`, `__import__`, `compile(`. All subprocess call sites are argv-list + `shell=False`; all have timeouts **except** settings_gui.py:246/248 (known, persists) and build.py (build-time). Skills/workflows/marketplace/twin imports are JSON-only with strict schemas, size caps, name validation, destructive guards, preview-before-run — **a malicious skill cannot execute arbitrary code** (worst case: naming a dangerous action, which hits confirm gates).

**Network surface = exactly 4 channels + 1 build path:** (1) tracker.py model download — **no timeout, no SHA-256 pinning** (supply-chain + hang risk; tension with the offline promise); (2) CDP discovery to localhost; (3) CDP websocket — **host:port unpinned from DevTools response** (known, persists; accept-key verified, fixed-JS-only); (4) bridge server hard-bound 127.0.0.1 (tested). No requests/httpx; no telemetry.

**Secrets:** env reads are paths/flags only; user_errors redacts `ghp_/sk-/Bearer/…`, env-pairs, ANSI/C0 (v15.1 fix verified present); no logging of secret values.

**Confirmed persisting (report-only, not re-fixed):** settings_gui timeout gap; unpinned ws host:port; ALLOW_ONCE `uses=-1` footgun; memory-reset CLI prints success unconditionally (`__main__.py:699-703`); unsigned model download.

**Tests:** test_redteam_release.py (76) + test_hardening_v15.py (30) → **106 passed** in this audit, including socket-guarded no-network proofs for setup/doctor/guided-lab.

**Score: 7.5/10.**

---

## 20. Privacy

- Telemetry **OFF by default and structurally absent** (no network calls anywhere in the runtime path beyond the 4 channels above); the historic dual-`telemetry_enabled` config bug was fixed in v15 and remains fixed.
- `airmouse privacy` report is real but **under-reports learned data** (P0-3: reports the 5 empty stores, not `intelligence/`, `calibration.json`, etc.).
- Audio never written to disk; transcript history in-RAM, bounded, privacy-gated, flipped off by privacy mode.
- `lecture.md` persists verbatim user note content; gaze calibration persists screen geometry; memory export embeds the absolute home path.
- Redaction layers (twin scrubbing, memory scrubbing, CLI error redaction) are real and tested.

**Score: 7/10 — honest architecture, broken deletion lifecycle.**

---

## 21. Performance (measured in this audit)

| Metric | Measured | Budget | Verdict |
|---|---|---|---|
| `import airmouse` (warm) | 0–1 ms | — | lazy imports, excellent |
| `--version` cold subprocess | 1.51 s | — | interpreter+lazy CLI import; acceptable |
| twin construct / 100 learns | 6.82 ms / 0.90 ms | <10ms / <10ms | ✅ huge margin |
| world construct / 100 observes | 6.16 ms / 1.27 ms | <10ms | ✅ |
| tasks construct / 50 creates | 5.97 ms / 1.26 ms | <10ms | ✅ |
| Perf budget test files | **10 passed** | — | ✅ |
| `doctor` peak child RSS | **151.1 MB** | — | mediapipe+cv2+numpy; typical |
| Full suite wall time | 21.17s / 1312 tests | — | fast |

**Gesture-loop bottlenecks (code-level, unmeasurable headless):** ironman's 5-filter series adds latency; synthetic 33ms timestamps mis-weight filters under FPS drops; `agent.process_frame` runs the whole fusion/intent stack per frame only when the camera window is visible. No hard performance problems found in code; the real loop latency is a **hardware-required measurement**.

**Score: 8/10.**

---

## 22. Packaging

- Wheel (95 entries, 85 modules, 4 extension assets, no tests/strays) + sdist; **checksums re-verified byte-exact**; METADATA/Requires-Dist/entry_points sane; `pip install --dry-run` clean.
- `hand_landmarker.task` is **NOT bundled** → first tracker use performs an unsigned network download (conflicts with the offline promise; pinning recommended).
- Extras `ocr/sound/voice/tts` correctly gate the painful Windows wheels (pyaudio).
- Windows compatibility: no Linux-only imports; Linux tools capability-probed, not imported. **PyInstaller Windows bundle: NOT TESTED (honestly labeled).**
- Upgrade path v15.0.0→v15.1.0 documented and consistent with git history; `git diff v15.0.0..HEAD -- agent-core/` empty (agent-core untouched) as the manifest claims.

**Score: 8.5/10.**

---

## 23. Documentation

- README (591 lines, 26 sections): spot-checked claims — 75 commands/10 namespaces ✅ (executed `commands_by_namespace()`), 14 gestures ✅, doctor exit codes ✅, setup consent ✅, guided-lab "0/5 hardware, 7/7 simulation" ✅ reproduced. **Two inaccurate claims:** the first-run menu description lists verify/privacy/memory/exit items that don't exist in `MENU_ITEMS` (cli_menu.py:41-52); the "onboarding asks one choice" claim describes `onboarding.py`, which has **zero runtime imports**.
- WINDOWS_REAL_WORLD_TEST.md: all 24 quoted commands/flags verified real against the parser.
- **docs/CLI_REFERENCE.md is stale (v11.5) AND corrupted** ("`[learning] emory]`", "`[office] eeting]`") and missing the entire v15 command surface. docs/INTELLIGENCE_GUIDE.md:191 documents the broken `--self-test` spelling (real: `airmouse self-test`).
- CHANGELOG/VERIFICATION_REPORT: honesty labels verified clean — no physical validation claimed anywhere.
- CAPABILITY_MATRIX.md: fresh, matches measured output.

**Score: 6/10** (largely truthful with honest labels; one stale+corrupted reference doc, two README claims wrong, dormant features described as active).

---

## 24. Dead / Orphaned / Duplicated Code (cleanup list — nothing deleted)

| Item | Status | Evidence | Recommendation |
|---|---|---|---|
| `agent_sdk.py`, `marketplace.py`, `ditm.py`, `explain.py`, `onboarding.py` | ORPHAN (library/test-only; no runtime imports) | grep import map | keep as documented library APIs; wire onboarding or stop describing it as active |
| `fusion2.py` | DECORATIVE (constructed, injected, never called live) | agent.py:420 sole reference | wire-or-remove decision |
| 10 registry gesture labels | DEAD (no detectors/producers) | gesture_registry.py:78-90 | add emitters or remove labels |
| `GestureProfile`/`GazeProfile`/`VoiceProfile`/`SelfTuner` observers | DECORATIVE (never fed) | plugin.py:221-252 | wire into live path (roadmap #5) |
| `TextController` | constructed+injected, never executed | agent.py:417 | wire or demote |
| `run_with_tray` (settings_gui) | no callers | :258-307 | wire or remove |
| top-level `airmouse/` dir | STALE FORK (differs from airmouse_pkg/airmouse/) | diff -q fails | cleanup-later |
| `airmouse_simple.py` (root) | duplicate single-file v5 re-implementation; 0 references | — | remove-candidate |
| `download/` (19 old wheels), 20× `research*.json`, `tool-results/`, `.env`, repo-root `worklog.md` | STRAY (60+ files tracked in the public repo) | git ls-files | remove-candidate + .gitignore |
| `memory` vs `memory status/…` CLI names | DUPLICATE NAME, different stores — feeds the P0-3 confusion | __main__.py:652/723 | unify naming |
| `recovery2.py` naming | cosmetic (no recovery.py exists) | — | rename later |

---

## 25. Real-World Validation Matrix

| Capability | Automated | Simulated | Real Windows | Physical Hardware | Status |
|---|---|---|---|---|---|
| Installation / CLI surface | ✅ 1312 tests + clean-venv §19 | ✅ | ❌ NOT TESTED | n/a | code-ready |
| Packaging (wheel/sdist/checksums) | ✅ byte-exact re-verified | — | ❌ (PyInstaller bundle NOT TESTED) | n/a | shipped |
| Camera / hand tracking | import-level only | ✅ (synthetic landmarks) | ❌ NOT TESTED | ❌ NOT TESTED | **hardware required** |
| Gesture recognition | ✅ 174 tests | ✅ | ❌ NOT TESTED | ❌ NOT TESTED | **hardware required** |
| Gaze pipeline | ✅ 55 tests | ✅ (synthetic injection) | ❌ NOT TESTED | ❌ NOT TESTED | **hardware required** |
| Voice grammar/NL/dictation (text-side) | ✅ 195 tests | ✅ | ❌ NOT TESTED | ❌ NOT TESTED | live path broken (P0-1) |
| Real ASR (sphinx/vosk/whisper) | provider code real | — | ❌ | ❌ (nothing installed) | **hardware + install required** |
| Cloud ASR (v5 Google) | code real | — | ❌ | ❌ unreachable (P0-1) | broken |
| OS actions (mouse/keyboard/files) | ✅ 61 tests (file ops real in sandbox) | ✅ simulator | ❌ NOT TESTED | ❌ NOT TESTED | code-ready |
| Browser control | ✅ 19 tests (bridge loopback real) | ✅ simulated bridge | ❌ NOT TESTED | ❌ (no live Chrome ever) | code-ready |
| Agent protocol/permissions/leases | ✅ 109+ tests | ✅ (execute is simulated) | n/a | n/a | protocol real, execution simulated |
| Recovery | ✅ 12/12 injection classes | ✅ simulator-only | n/a | n/a | simulator-verified |
| Guided lab / doctor / verify / setup | ✅ 175 tests + live runs | ✅ | ❌ NOT TESTED | ❌ (physical tests never auto-PASS) | honest |
| RF hardware | code present, honestly labeled | — | ❌ | ❌ NOT TESTED | **hardware required** |

**No physical validation exists for anything. This is honestly labeled everywhere in the docs — and must remain so.**

---

## 26. Current Scores (0–10, no hype)

| Dimension | Score | One-line justification |
|---|---|---|
| Gesture detection | 6 | 14 solid poses + swipes with strong filtering; 10/25 registry labels dead; no motion gestures beyond swipes; no two-hand |
| Gesture reliability | 5 | strong filters/hysteresis/hand-loss teardown; but confidence never gates actions, stability gating is a no-op, camera loss silently ignored |
| Gesture UX | 6 | wired tutorial + HUD + audio feedback; no calibration in setup; lab lacks retry/confidence feedback |
| Gesture personalization | 3 | AdaptiveCalibration integrated; the entire intelligence observer/tuner layer is never fed |
| Eye control | 7 | full iris→calibration→dwell→safety pipeline, genuinely wired; unused wink, default-off blink click, zero physical evidence |
| Voice | 3 | deep tested text-side stack; **live microphone path crashes at startup (P0)**; no real ASR installed |
| Multimodal fusion | 5 | gaze+hand arbitration genuinely live; voice leg dead; fusion2 decorative; tick gated behind camera window |
| Computer control | 6 | real executors with clamps/allowlists; Windows volume broken; legacy paths bypass safety; unverified on hardware |
| Browser | 7 | real stdlib CDP + extension + tested loopback bridge; no launcher; no live-Chrome test; unpinned ws host |
| Agent interoperability | 5 | strict protocol + fail-closed permissions + stdlib SDKs; execute simulated; **no wire transport** |
| Multi-agent | 6 | leases/handoff/e-stop-all real and tested; budgets unenforced; in-memory only |
| Recovery | 8 | two bounded gated ladders, 14 diagnoses, 12/12 injection classes behave — simulator-only evidence |
| Safety | 6 | v9 ladder real and tested with E-STOP dominance; **classic gesture path bypasses all of it**; OK→Alt+F4 unconfirmed |
| Privacy | 7 | real scrubbing/redaction, telemetry structurally absent; **deletion lifecycle clears the wrong stores** |
| Security | 7.5 | zero injection/eval/deserialization surface; residual unpinned-ws + unsigned model download |
| Accessibility | 6.5 | dwell/blink/audio/8 modality chains real; sticky-gesture, target snapping, high-contrast rendering absent |
| Offline capability | 6 | offline gate real and tested; first-run model download un-pinned; no real ASR by default |
| Performance | 8 | all budgets pass with huge margin; lazy imports; real-loop latency unmeasured |
| Packaging | 8.5 | byte-exact artifacts, clean metadata, honest labels; model not bundled |
| Documentation | 6 | honest labels throughout; stale+corrupted CLI_REFERENCE; 2 wrong README claims; dormant features described as active |
| Code hygiene | 6 | 1312 green; 60+ stray tracked files; dead labels; decorative modules |
| Commercial readiness | 5 | transparent licensing (nothing essential paywalled, nothing enforced); killer experience blocked by P0s; zero real-world evidence |
| Real-world validation | 2 | nothing has ever touched hardware in CI; honestly labeled |
| **OVERALL** | **5.4** | strong tested core + unwired superstructure + 3 live-breaking P0s |

---

## 27. Top 25 Improvements (ranked by user impact × reliability × difficulty × risk × value)

| # | Improvement | Priority | Why / evidence |
|---|---|---|---|
| 1 | Fix `voice_engine10` UnboundLocalError (hoist `voice_engine10 = None` above the v5 voice block) | **P0** | one line unblocks the entire voice+fusion surface (`__main__.py:1214` vs `:1234`) |
| 2 | Fix the v9 gesture-ownership gate: assign before read + actually suppress direct actions when the agent owns them | **P0** | crash under `--fusion --gaze` (`:1728`/`:1738`); double-control in hands-free |
| 3 | Make the memory/privacy lifecycle operate on the **real** stores (intelligence/, calibration, gestures, macros, lecture.md) or migrate them into the persistence layer | **P0** | deletion-rights honesty (`setup_wizard.py:276-289` vs `plugin.py:45`; `__main__.py:699-703`) |
| 4 | Route classic-path gestures through the ActionEngine safety/permission gate (or add a minimal confirm gate for destructive-class actions like OK→Alt+F4) | **P0** | today raw pynput from the main loop (`:1812-2107`); mission §6 requirement |
| 5 | Add a startup smoke test that executes `main()`'s arg surface (stubbed hardware) so startup crashes can't ship green | **P0** | both P0 crashes invisible to 1312 tests |
| 6 | Give the offline voice engine a real audio source (`start_audio` wiring + mic thread) and implement or remove the `AIRMOUSE_VOICE_TEXT` hook | P1 | engine is transcript-injection-only today |
| 7 | AIP wire server: `airmouse --aip-stdio` (and/or TCP localhost) routing `execute` through the real ActionEngine behind permissions | P1 | agent-core/agent-sdk-js target a nonexistent server; SDK execute is simulated |
| 8 | Register the real screen_perception providers in the Universal Target Resolver | P1 | 7-provider chain returns ok=False out of the box |
| 9 | Wire GestureProfile/SelfTuner observers into the live frame loop (bounded, opt-in, persisted via persistence.py) and let SelfTuner actually adjust confirm frames/cooldowns | P1 | the advertised "learns your gestures" is decorative |
| 10 | Gesture Academy in setup: cursor calibration, hand-positioning/lighting/distance guidance, interactive practice targets (build on tutorial.py) | P1 | setup validates the environment but teaches nothing |
| 11 | Two-hand foundation: `max_num_hands=2` + handedness read + single-hand feature flag; first gesture = two-hand zoom | P1 | tracker.py:43/:85; enables the whole two-hand category |
| 12 | Emit the dead labels: `pinch_hold`, `pinch_release`, `double_pinch` (live detector), `swipe_up/down` — and feed hand events into gesture_registry sequences | P1 | 10/25 vocabulary dead; sequence matcher exists but unfed |
| 13 | Pin the model download: SHA-256 + timeout + offline/quiet path; consider bundling in the wheel | P1 | tracker.py:14-34; offline-promise tension + supply chain |
| 14 | Confidence-gate classic-path actions (drop borderline-confidence triggers) + use `stability_frames` in direct mode | P1 | confidence is HUD-only today |
| 15 | Camera watchdog for classic mode: reconnect attempts + explicit on-screen state (reuse the v9 SAFE_MODE ladder) | P1 | dead camera silently = "no hand" |
| 16 | Repo hygiene: remove research*.json / tool-results/ / download/ / .env / legacy airmouse fork / airmouse_simple.py; extend .gitignore | P2 | 60+ strays in the public repo |
| 17 | CLI honesty fixes: honor `cleared=False` in memory reset/delete; validate `memory`/`doctor` args; fix `--self-test` doc spelling; suppress raw OpenCV stderr | P2 | B-5 findings |
| 18 | Docs: fix README menu + onboarding claims; regenerate CLI_REFERENCE.md for v15; demote unwired features in prose | P2 | section 23 |
| 19 | Fix Windows volume/media path (use the existing keyboard.py SendInput or drop the dead import) | P2 | system_actions.py:164-178 references a nonexistent class |
| 20 | fusion2: wire as the confirm-voting layer behind a flag or remove from the shipped path | P2 | decorative code with real logic |
| 21 | Browser: implement Chrome launch with `--remote-debugging-port` + pin ws host:port to 127.0.0.1 | P2 | no launcher; browser.py:833-834 |
| 22 | Wink consumer + blink-click default review; expose dwell duration in settings GUI | P2 | gaze.py:599-601; config.py:275 |
| 23 | Multi-agent budget enforcement + fix ALLOW_ONCE default (`uses=1` when kind is ALLOW_ONCE) | P2 | agents.py:64/316; permissions.py:145-147 |
| 24 | Accessibility rendering: high_contrast/large_ui/reduced_motion flags → HUD/settings; sticky-gesture hold-lock; target snapping for motor-impaired users | P3 | flags exist as data, never rendered (onboarding.py:133-160) |
| 25 | Interactive Gesture Lab: per-test detected-gesture + confidence feedback, retry loop, calibration hooks, `only=` CLI exposure | P3 | guided_test.py:302-306, 657-742 |

---

## 28. Missing Capabilities (what AirMouse cannot do today)

**Gestures:** thumbs-down, four-finger, swipe up/down, circles, push/pull/flick/shake/wave, shape drawing, air double-tap, any multi-step sequence from hands, any two-hand interaction, handedness, per-user gesture thresholds.
**Voice:** any live microphone input in the shipped app (P0); real ASR out-of-the-box (nothing installed); TTS output (never called); wake-word arming live.
**Gaze:** wink actions; blink-click by default; OCR-based UI-element targeting by default; gaze accuracy evidence.
**Fusion:** voice leg of every scenario; any fusion when the camera window is hidden; fusion2 voting.
**Computer control:** application launch as a first-class action; real window-manager operations (hotkeys only); Windows volume/media via the system executor; OS clipboard as a file-operation target.
**Agents:** cross-process agent connection (no transport); real execution through the SDK; wired target resolution; budget enforcement.
**Accessibility:** sticky gestures, target snapping, high-contrast/large-UI rendering, switch-device input, audio-only menus.
**Evidence:** all physical-hardware claims (jitter, false-positive rates, latency, lighting/distance robustness, gaze accuracy, ASR quality, battery/CPU on real machines).

---

## 29. Recommended Next Architecture ("Gesture-First Human-Computer Intelligence")

Keep the current layered design — it is sound. Evolve it along four axes:

1. **One spine, not parallel universes.** Make the ActionEngine the *only* execution path: classic gestures, v9 fusion, hands-free, macros, AIP agents, and workflows all dispatch through `ActionEngine` → safety → permission → execute → verify → recover. Delete the legacy direct-pynput bypasses. This single change converts the safety hierarchy from "v9-only" to "universal" and makes every future gesture automatically safe.
2. **Close the sense→learn→adapt loop for real.** Feed GestureProfile/GazeProfile/VoiceProfile + SelfTuner from the live frame loop (bounded, opt-in, persisted through persistence.py); let SelfTune adjust only bounded tunables; keep PREDICTION ≠ EXECUTION (already correctly enforced). Personalization becomes a feature instead of a folder.
3. **One home, one truth.** A `paths.py` resolving every artifact through `AIRMOUSE_HOME` (fixes the split-brain, enables the honest privacy lifecycle, enables portable installs).
4. **Real last mile for agents.** `--aip-stdio` transport + SDK execute → ActionEngine; register the real target providers. The protocol/permission work is already done and tested — this is wiring, not invention.

Then the experience target (§27 of the mission — open hand moves, pinch clicks, pinch+move drags, swipe navigates, look+pinch targets, "open YouTube", "click that") becomes reachable with **zero new detection algorithms**: it is P0 fixes (#1-2), the safety spine (#4/#1 above), voice unbreakage, and the Academy.

---

## 30. Proposed Next Release Roadmap

- **v15.1.1 (hotfix, days):** P0 #1, #2, #5 (voice crash, gesture gate, main() smoke test) + memory-reset CLI honesty (#17 partial). No features. Goal: the shipped binary does what the docs say.
- **v15.2.0 (weeks):** P0 #3 (real privacy lifecycle + paths.py), P0 #4 (universal safety spine), #6 (live mic source), #12 (dead label emitters), #14 (confidence gating), #17/#18 (CLI+docs honesty), #13 (model pinning). Theme: **"Everything wired is true."**
- **v16.0.0 (the gesture-first release):** two-hand foundation + two-hand zoom (#11), Gesture Academy setup (#10), personalization loop live (#9), AIP wire transport + real agent execution (#7/#8), Gesture Lab with feedback/retry (#25), browser launcher (#21). Theme: **"Teach, adapt, connect."**
- Each release: full-suite green, red-team delta, honest five-part verification report, tag + artifacts (per §22/§23/§30 of the hardening standard already in place).

---

## 31. Risks

1. **Credibility risk (highest):** three P0s mean the advertised experience ("look+pinch", voice) is partially false on a fresh install. Every day unpatched erodes trust more than any missing feature.
2. **Safety risk:** classic-path Alt+F4/clicks with no gate; hands-free double-control. A single viral "it closed my unsaved work" incident is fatal for an accessibility product.
3. **Privacy-lifecycle risk:** delete/reset that misses the real stores is a compliance and trust defect, not a cosmetic one.
4. **Supply-chain risk:** unsigned, timeout-less model download on first run.
5. **Validation-debt risk:** the entire reliability story is simulator-backed; physical variance (lighting, cameras, users) is unquantified until real testing happens (WINDOWS_REAL_WORLD_TEST.md exists — it needs users).
6. **Complexity risk:** 92 modules with decorative branches (fusion2, onboarding, TextController, tray) raise maintenance cost and doc-drift — exactly what produced today's gaps.
7. **Single-process assumption in the agent layer** will not survive real multi-agent use (leases are in-memory).

---

## 32. What NOT To Build

- **More gesture vocabulary before the spine exists** (a 30th pose adds nothing while actions bypass safety).
- **Cloud anything** (ASR, telemetry, sync) — breaks the local/offline promise for marginal value.
- **Neural gesture classifiers / big ML models** — the angle+hysteresis design is deterministic, debuggable, and fast; switch only on physical evidence of failure.
- **A second fusion engine** (fusion2 duplicated fusion; don't repeat that pattern — extend fusion.py).
- **Gesture marketplace/economy** before gesture recording and profiles even exist live.
- **More agent surfaces** before the wire transport and real execution exist.
- **New CLI commands** before `memory`/`doctor` arg handling is honest.
- **Any feature that cannot be tested headless AND by a real user** (the project's own standard: features without verification are debt).
- **Paywalling the core** (licensing must stay additive; it currently is).

---

## 33. Exact Implementation Order

```
1. v15.1.1 hotfix
   1.1  Hoist voice_engine10=None; startup smoke test over main() arg surface (P0 #1,#5)
   1.2  Fix gesture-ownership gate: initialize `gesture` before :1728; make :1738 respect
        the v9 nulling (P0 #2)
   1.3  memory reset/delete honor cleared=False (P0 #3, CLI half)
   1.4  Full suite + red-team delta + tag v15.1.1
2. v15.2.0 — "Everything wired is true"
   2.1  paths.py; migrate all hardcoded ~/.airmouse writers; unify memory stores (P0 #3)
   2.2  Universal safety spine: ActionEngine as sole dispatch; confirm gate for
        destructive-class gestures (P0 #4)
   2.3  Live mic source for offline voice; implement or remove AIRMOUSE_VOICE_TEXT (#6)
   2.4  Emit dead labels + feed gesture_registry from hands (#12); confidence gating (#14)
   2.5  Model download pinning (#13); camera watchdog in classic mode (#15)
   2.6  CLI + docs honesty batch (#17,#18)
3. v16.0.0 — "Teach, adapt, connect"
   3.1  Two-hand tracking + two-hand zoom (#11)
   3.2  Gesture Academy setup + interactive Gesture Lab (#10,#25)
   3.3  Personalization loop live with bounded SelfTune (#9)
   3.4  --aip-stdio + real SDK execution + wired target providers (#7,#8)
   3.5  Browser launcher + ws pinning (#21); Windows volume fix (#19)
4. Continuous: physical validation program — every WINDOWS_REAL_WORLD_TEST.md run feeds
   measured jitter/latency/false-positive data back into gesture thresholds (replaces
   guesses with evidence; this is the only path to reliability score >7).
```

---

**END OF AUDIT.** Per the mission's FINAL RULE: the audit is complete, the report is presented, and **no further implementation begins until this report is reviewed and the next objective is chosen.**

*Audit artifacts: this file (committed), shared worklog `/home/z/my-project/worklog.md` (Task IDs A, B-1…B-5, C, D), all findings reproducible from tag `v15.1.0` @ `ed29326`.*
