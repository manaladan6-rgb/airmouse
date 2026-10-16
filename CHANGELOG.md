# Changelog

All notable changes to AirMouse are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/) (advisory).

The detailed per-release engineering notes live in
[airmouse_pkg/CHANGELOG.md](airmouse_pkg/CHANGELOG.md); this file is the
user-facing summary.

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
