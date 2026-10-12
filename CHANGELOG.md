# Changelog

All notable changes to AirMouse are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/) (advisory).

The detailed per-release engineering notes live in
[airmouse_pkg/CHANGELOG.md](airmouse_pkg/CHANGELOG.md); this file is the
user-facing summary.

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
