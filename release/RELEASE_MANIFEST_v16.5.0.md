# AirMouse v16.5.0 — Adaptive Multimodal Intelligence Release Manifest

Tag: v16.5.0 · Commit: see `git show v16.5.0` · Date: 2026-09-05

## Artifacts
| File | Bytes | SHA-256 (see SHA256SUMS_v16.5.0.txt) |
|---|---|---|
| airmouse-16.5.0-py3-none-any.whl | 625209 | (in SHA256SUMS) |
| airmouse-16.5.0.tar.gz | 589823 | (in SHA256SUMS) |

## Verification performed (this release)
- Built with PyPA build from airmouse_pkg/ (repo root invocation; build.py does NOT shadow this path)
- Clean-venv install (§38 battery): fresh venv, wheel only, no source tree —
  --version (16.5.0) / doctor (FAILED: 0) / teach rc 0 headless (plan printed,
  nothing auto-passed) / help-me panel + question / transcribe rc 0 with honest
  SIMULATED banner / voice-status provider panel (6 lines) / privacy with
  PERSONALIZATION summary / offline-test 18/18 / academy headless rc 0 /
  AIP `discover` → `capabilities` (13 capabilities) over stdin/stdout
- sdist download verified (`pip download --no-binary :all:`)
- Full test suite at tag: 1889 passed / 2 skipped (honest headless) / 0 failed
- `airmouse verify`: 12/12 automated PASS + 5 physical ACTION_REQUIRED
  (Teacher + Temporal checks added in v16.5)
- Red-team: static scan clean on all 8 new v16.5 modules (no eval/exec/
  shell=True/os.system/pickle/subprocess/network); temporal.py
  no-parallel-dispatch source scan enforced by tests AND by `verify`;
  no-network guard suite green with the 12-check contract
- Performance (measured in `verify`): temporal recognize+features
  ~294 µs/frame (frame budget at 30 fps ≈ 33 ms — <1%); v15/v16 perf
  budgets all pass (test_performance.py, test_release_perf.py green)

## v16.0.0 → v16.5.0 upgrade notes
- Backward compatible: all v16 commands/flags preserved; new commands added
  (teach [track], learn, transcribe, help-me [question])
- First-run auto-teaching: plain `airmouse` on a TTY now offers the 3–5 min
  interactive tour (NEW) or "Continue your training?" (IN_PROGRESS);
  "Skip for now" always works; non-TTY/headless is never blocked
- New config keys: teach_auto = true, ready_panel = true (both default ON,
  both safe to disable)
- Storage: new artifacts under the unified home — profile/onboarding.json,
  profile/{interaction,voice,gestures,preferences}.json, transcripts/
  (user-saved only); privacy manifest 20 → 24 entries; all user-learning
  artifacts covered by memory reset/delete/export
- The v16.5 temporal intelligence runs as an OBSERVER in the live loop:
  it PROPOSES, the gesture_spine disposes — no new execution path, no
  double control; destructive-gesture policy unchanged (refused by default)

## Honest status (unchanged truth)
- REAL WINDOWS RUNTIME: NOT PERFORMED IN SANDBOX (procedure:
  WINDOWS_REAL_WORLD_TEST.md, now 28 steps incl. v16.5 drills 16a–16d)
- PHYSICAL HARDWARE (webcam, mic, hand tracking, gaze, live ASR engines,
  live-Chrome control, RF): NOT TESTED — PHYSICAL TEST REQUIRED labels
  throughout the docs; first-run tour with real sensors unverified
- PyInstaller Windows bundle: NOT TESTED (build.py exists; run it on Windows)
- Voice: built-in deterministic grammar is REAL (offline, 30 canonical
  commands); full local ASR (vosk/whisper/pocketsphinx) is OPTIONAL and
  auto-detected when installed; dictation formatting is REAL via
  VoiceTypingEngine; microphone capture is PHYSICAL TEST REQUIRED

## Release engineering notes
- `airmouse_pkg/build.py` shadows PyPA `build` when CWD=airmouse_pkg — build
  from the repo root (`python -m build airmouse_pkg/`)
