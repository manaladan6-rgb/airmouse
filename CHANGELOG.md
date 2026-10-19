# Changelog

All notable changes to AirMouse are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/) (advisory).

The detailed per-release engineering notes live in
[airmouse_pkg/CHANGELOG.md](airmouse_pkg/CHANGELOG.md); this file is the
user-facing summary.

## [19.0.3] — One Safety Gate, Real Audio, Truthful Diagnostics

### Fixed — safety
- **One authoritative E-STOP for every route.** A new central safety
  gate (`airmouse.safety_gate`) carries the process-wide emergency
  stop, shutdown state, backend availability and confirmation flags.
  Gestures, browser voice commands, browser-bridge actions, AIP agent
  execution, macro replay and PEACE scrolling all consult it. On
  v19.0.2 an E-STOP did not necessarily block the browser voice
  side-door, AIP execution, PEACE scrolling or macro steps.
- **`airmouse estop trip|reset|status`** — a cross-process E-STOP
  (flag file under `AIRMOUSE_HOME`, never auto-resets) that stops a
  RUNNING session within one frame. Works with the camera preview
  closed. Bare words only; unknown args exit 2.
- **Global E-STOP hotkeys** `Ctrl+Alt+E` (trip) / `Ctrl+Alt+R`
  (reset), armed every session with honest availability reporting —
  E-STOP no longer depends on the camera preview or a debug mode.
- **TF-001 root fix** — `HandTracker()` no longer raises
  `NameError: name 'mp' is not defined`. The lazy import is now a real
  module global (a delegating proxy); missing dependency, missing
  model and unusable camera each produce an actionable error, and a
  dead camera can never look like "hands active".
- **Macro replay is cancellable** — cooperative stop checks before the
  first action, between steps and inside waits; bounded cancel/join;
  fail-closed start while the gate is latched.
- **Browser bridge exact-origin allowlist** — the substring match that
  let a look-alike origin through is closed; payloads are size-capped;
  unknown commands are rejected; binding to loopback is containment,
  not authorization.
- **AIP agent execution is fail-closed** — every action type goes
  through a permission check; unclassified action types are denied;
  `agent_action` requires confirmation by default.
- **Ordered shutdown** — the central gate is marked shutting-down
  first, then macro playback is cancelled (bounded), voice capture
  stops, workers join with bounded timeouts and unterminated workers
  are reported.

### Fixed — voice
- **Real audio path** — utterance PCM is buffered and handed to the
  offline recognizer as real frames; `transcribe(None)` is gone;
  None/empty payloads are rejected with a recorded reason.
- **No fabricated confidence** — when an engine cannot provide a
  confidence value, none is invented (vosk parses its real value).
- **Honest session banners** — `V10 OFFLINE VOICE LIVE` only when a
  real local ASR provider is constructed AND available; otherwise
  `GRAMMAR-ONLY` with the exact gap and the remedy
  (`airmouse voice-setup`). The v5 cloud path discloses on its banner
  that spoken audio is uploaded to Google.
- **Self-test honesty** — a component that is installed but unusable
  (e.g. vosk without a model) is SKIP with the reason, never silently
  rewritten to OPTIONAL; FAIL rows keep the overall result FAIL; SKIP
  rows are counted in the tally.

### Fixed — diagnostics & CLI
- **`doctor --json`** now emits real structured JSON (overall,
  sections, blocked/degraded with reasons and fixes) and carries the
  same exit contract as the text panel: READY → 0, PARTIAL → 1,
  BLOCKED → 2.
- **Dictation edits are buffer edits** — "delete last word",
  "scratch that", "capitalize that" and friends edit the dictation
  text buffer only; they can never delete files ("delete demo.txt"
  stays a destructive FILES command). 14 new TEXT_EDIT intents.
- **Deictic move** — "move over there" moves the cursor toward the
  pointing direction. Grammar is now 90 commands across 11 namespaces.
- **Autostart entries launch the real CLI** (`<python> -m airmouse
  --skip`) — the previous entry ran nothing; `verify_launch_command`
  checks usability.
- `airmouse estop` added as subcommand #44.

### Disclosed limitations (honest)
- Physical hardware validation (webcam, microphone, real clicks,
  hotkey presses) is NOT INCLUDED in this release's automated
  evidence: the release is software-verified only. Run
  `airmouse verify` on real hardware for the ACTION_REQUIRED
  physical checklist.
- Offline ASR needs its acoustic model installed once via
  `airmouse voice-setup` (consent-gated, SHA-256-verified, never
  auto-downloaded).
- The legacy root-level `airmouse_simple.py` single-file demo is
  explicitly UNSUPPORTED: it predates the central safety gate (no
  E-STOP integration), is excluded from the packaged wheel/sdist,
  and is kept only as a reference. Use the `airmouse` CLI.

## [19.0.2] — Real Physics, E-STOP Contract, Honest Diagnostics

### Fixed — safety (the E-STOP contract now actually holds)
- **E-STOP now stops cursor movement.** `mouse.move_to` on all three
  paths (direct tracking, spring-damper, momentum) is gated by the
  spine's E-STOP gate; previously the "movement freezes" claim was
  decorative.
- **E-STOP releases held mouse buttons.** Tripping the stop from ANY
  source (ESC key, [x] panel, voice, long blink, safety system) now
  releases a pressed primary button; previously a drag could be orphaned.
- The v9 safety-system EMERGENCY level (long-blink, voice) now
  dominates the gesture spine as well — movement freezes too.
- Voice commands are refused entirely while an E-STOP is latched.
- SHAKE and hand-loss release held buttons directly (releasing is always
  risk-reducing; it can never be gated).
- Stale pinch/peace/trackpad state is cleared when the hand disappears,
  so a hand re-entering mid-pinch cannot instantly drag or click.
- Macro replay runs a finish hook that releases held buttons when the
  replay ends, aborts or throws.
- Scroll output is clamped to ±100 ticks at the controller boundary;
  partial key-combo failures roll back pressed modifiers.

### Fixed — physics is now measured, not guessed
- **Real timestamps end-to-end.** The old pipeline fed every filter a
  synthetic +33 ms counter, making dropped frames invisible: a 500 ms
  input gap was filtered as one normal frame and the cursor teleported.
  Real monotonic frame timestamps now flow tracker → filters, and the
  main loop uses the measured frame-to-frame period.
- **Gap policy:** a real input gap longer than 0.25 s resets the filter
  chain and HOLDS the cursor for one frame, then re-acquires from the
  new hand position — one predictable step, never a sweep.
- **Input validation boundary:** non-finite or out-of-range landmarks
  HOLD the cursor (invalid_input state) instead of poisoning filters or
  crashing on `int(nan)` in the OS move.
- Honest velocity: per-axis applied-output delta over real time (px/s);
  the old scalar-based vector always pointed diagonally.
- Auto-precision now HALVES the dead zone when the hand slows (the old
  code doubled it — the opposite of precise).
- Precision toggling and adaptive tuning retune filters IN PLACE; the
  old rebuild discarded filter state and snapped the cursor mid-session.

### Fixed — eye/gaze control works end-to-end now
- **CRITICAL: the saved gaze calibration was never loaded at runtime** —
  every live session silently ran the uncalibrated fallback, which
  mathematically confines gaze targets to the central ~20-35% of the
  screen. It now loads from the standard location automatically.
- Calibration quality gate: a fit below "fair" quality, or fitted for a
  different screen size, can no longer drive gaze clicks.
- `gaze_dwell_time` / `gaze_min_confidence` config keys actually reach
  the engine now (they were dead keys; dwell was hardcoded 0.8 s).
- Dwell progress pauses while the eyes are closed — a blink can never
  complete a dwell click.
- Long-blink E-STOP requires 1.2 s of continuous closure (the documented
  value; the code previously tripped at 0.6 s — accident-prone).
- The calibration window is true fullscreen and records the real screen
  size, removing a systematic offset/scale error from every fit.

### Fixed — runtime independence and honesty
- The v9 multimodal tick (gaze + voice + fusion) runs every frame
  regardless of the debug preview window — hiding the preview (or
  `--no-cam`) no longer silently disables gaze/voice/fusion actions.

### Added
- **`airmouse logs [N] [--json]`** — bounded command-outcome log
  (1 MiB rotation cap, UTC timestamps, ANSI-stripped, injection-safe).
  Cursor telemetry is deliberately kept separate.
- **`airmouse config [path|show|validate]`** — resolve the active config
  path, print it, or validate it (unknown sections are errors, unknown
  keys are warnings; `--json` machine-readable).
- **`airmouse diagnostics --live-sim`** — live cursor-physics telemetry:
  the REAL DirectTracker engine fed a deterministic synthetic hand, with
  every line labeled SIMULATED and honest PHYSICAL_TEST_REQUIRED framing.
- **`airmouse diagnostics --live`** — the same telemetry from a real
  camera, with honest failure guidance when no camera is available.
- Hardened hand-model download: consent prompt on TTY, pinned versioned
  URL + SHA-256 verification, atomic `.part` install, 30 s timeout,
  `AIRMOUSE_NO_MODEL_DOWNLOAD` kill-switch, corrupt-cache re-download.
- Browser bridge Host/Origin validation: DNS-rebinding pages and
  non-local processes are refused (403); the bundled extension keeps
  working unchanged.

### Changed
- `airmouse setup` exits 1 on FAILED steps and 2 when steps need user
  attention (it previously always exited 0); `self-test` exits 1 on any
  FAIL row.
- `--version`/`--help` no longer import OpenCV (lazy cv2 loading);
  CLI startup is ~0.4 s faster.
- `OPENCV_VIDEOIO_PRIORITY_OBSENSOR=0` — native obsensor ERROR noise no
  longer leaks into `doctor` output.
- PyInstaller installation by `build.py` is consent-gated with an
  `AIRMOUSE_NO_BUILD_INSTALL` kill-switch.
- Branding corrected: "AIRMouse" → "AirMouse" in 13 user-visible sites.

### Tests
- 2180 passed / 7 skipped / 0 failed (baseline 2112/7/0 + 1 env-fix).
- +75 tests: E-STOP contract (21), CLI commands (16), physics benchmarks
  (12), cli_log (19), bridge hardening (4), gaze updates, two-hand lazy
  init, exact-shape brand updates.
- Deterministic physics benchmarks lock: rest jitter < 2 px, settle
  ≤ 25 frames, lag ≤ 60 px, reversal excursion ≤ 80 px, gap-hold with
  zero stale velocity, bit-identical reproducibility.

### PHYSICAL_TEST_REQUIRED (not claimable from software)
- Real-camera hand/gaze accuracy (px), camera→click latency, dwell
  ergonomics, blink classifier false-accepts on real eyes, OS cursor
  behavior on Windows/macOS. See README "Verification status".

[19.0.2]: https://github.com/manaladan6-rgb/airmouse/releases/tag/v19.0.2

## [19.0.1] — Public Release Audit hotfixes

### Fixed
- **Windows**: the model-install lock reaper used the POSIX idiom
  `os.kill(pid, 0)`; on Windows signal 0 is `CTRL_C_EVENT`, so a stale
  lock with a live pid interrupted the running process with a real
  Ctrl+C. The reaper now probes via `OpenProcess` and never signals.
- **Windows**: piped/redirected CLI output (`airmouse doctor | tool`)
  could crash on `✓`/`→` characters — stdout/stderr are now UTF-8.
- **Python 3.9/3.10**: the `tomli` TOML parser was never declared as a
  dependency, silently degrading config validation. Now declared for
  `python_version < "3.11"`.
- **All platforms**: `doctor --json --to X garbage` behaved differently
  across Python versions (argparse intermix change in 3.12); the CLI
  parser is now version-robust with strictness preserved.
- **Windows**: atomic store writes could fail when another thread or
  process held the target file open; `fileio.replace_atomic` retries
  sharing violations with a bounded backoff.
- CLI startup 3.3× faster (lazy mediapipe import); `airmouse
  capabilities` no longer leaks a raw enum repr; camera-less machines
  no longer get OpenCV C++ WARN noise during `doctor`.

[19.0.1]: https://github.com/manaladan6-rgb/airmouse/releases/tag/v19.0.1

## [19.0.0] — Instant Deployment

### Added
- **`airmouse setup` 2.0** — one command, 13 idempotent steps: environment
  → dependencies → local storage → configuration → camera → microphone →
  browser → voice extras → input automation → voice model → interaction
  mode → profile → smoke test. It asks nothing it can answer itself.
- **First-run launcher** — menu (Start / Test my hardware / Configure
  voice / Diagnostics / Learn / Help / Exit) with capability lights per
  mode; users without a camera or microphone still get a working mode.
- **`airmouse wizard`** — a 60–120 s guided interaction lesson that
  verifies you actually performed each skill.
- **`airmouse doctor` 2.0** — human-readable health report in ~2 s
  (`--advanced` for technical detail, `--json` for tooling).
- **Lifecycle commands** — `airmouse repair` (safe local fixes),
  `airmouse reset` (explain-then-ask state removal),
  `airmouse uninstall` (explains exactly what is deleted),
  `airmouse update --check` (explicit, check-only).
- **Capability registry + degradation ladder** — one honest answer to
  "what can AirMouse do on THIS machine right now", with evidence labels
  (REAL / SIMULATED / OPTIONAL / PHYSICAL_TEST_REQUIRED / NOT_AVAILABLE)
  and a best-available interaction mode that never dead-ends.
- **Model integrity pipeline (12 stages)** — consent-gated downloads,
  SHA-256 + SHA-512 verification before extraction, Zip-Slip protection,
  executable-member refusal, atomic staging install.
- **Voice bootstrap** — `airmouse voice-setup` picks the smallest
  compatible ASR engine for your Python/OS (never compiles from source)
  and fetches the official Vosk en-US model with verified checksums.
- **Five-minute benchmark + packaging security audit** tooling
  (`scripts/five_minute_test.py`).

### Fixed
- Release audit: `airmouse capabilities` no longer leaks the raw
  `ComponentState.OPTIONAL` enum repr on Python 3.11+ (gaze row).
- Release audit: a machine without a camera no longer gets OpenCV C++
  WARN noise on stderr during `airmouse doctor` (real errors stay
  visible).
- Release audit: CLI startup 3.3× faster (`airmouse --version`
  1.63 s → 0.47 s measured) — mediapipe is now imported lazily via a
  PEP 562 hook in the tracker module; `airmouse.tracker.mp` stays
  usable for runtime and tests, and a regression test pins the
  behaviour.

### Security
- Packaging security audit added to the test suite (archive members,
  path traversal, consent gates, refusal paths).

[19.0.0]: https://github.com/manaladan6-rgb/airmouse/releases/tag/v19.0.0
