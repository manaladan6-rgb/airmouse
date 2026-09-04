# AirMouse v16.0.0 — Gesture-First Release Manifest

Tag: v16.0.0 · Commit: see `git show v16.0.0` · Date: 2026-09-04

## Artifacts
| File | Bytes | SHA-256 (see SHA256SUMS_v16.0.0.txt) |
|---|---|---|
| airmouse-16.0.0-py3-none-any.whl | 538,617 | (in SHA256SUMS) |
| airmouse-16.0.0.tar.gz | 505,027 | (in SHA256SUMS) |

## Verification performed (this release)
- Built with PyPA build from airmouse_pkg/ (pyproject-only; build.py does NOT shadow this path)
- Clean-venv install (§38): fresh venv, wheel only, no source tree —
  --version / doctor [READY FOR TESTING] / self-test PASS 13/1/1/0 /
  offline-test 18/18 / setup 11 honest steps / profile list (8) /
  academy plan / gesture-lab honest demo / memory status /
  `python -m airmouse.aip_stdio` DISCOVER→capabilities round-trip
- Full test suite at tag: 1556 passed / 2 skipped (honest headless) / 0 failed
- Red-team: static scan clean (no shell/eval/exec/pickle/unsafe-deserialization);
  all subprocess argv-list + shell=False; new network surface = none
  (aip_stdio is stdio-only; browser launcher loopback-pinned)
- Performance: execution-spine dispatch overhead measured 1.7 µs/action
  (budget 500 µs); v15 perf budgets all pass

## v15.1.0 → v16.0.0 upgrade notes
- Backward compatible: all v15 commands/flags preserved; new commands added
  (academy, gesture-lab, profile, --aip-stdio, --launch-browser)
- New default-OFF flags: two_hand, gesture_allow_destructive (stays FALSE),
  selftune_apply
- Gesture actions now route through the execution spine: destructive gestures
  (OK→Alt+F4, macro replay) are REFUSED by default — re-enable consciously via
  config gesture_allow_destructive = true if you truly want the old behavior
- Storage: all artifacts unified under one home (AIRMOUSE_HOME honored everywhere);
  memory reset/delete/export now cover the REAL learning artifacts

## Honest status (unchanged truth)
- REAL WINDOWS RUNTIME: NOT PERFORMED IN SANDBOX (procedure: WINDOWS_REAL_WORLD_TEST.md)
- PHYSICAL HARDWARE (webcam, mic, hand tracking, gaze, live-Chrome control,
  RF): NOT TESTED — PHYSICAL TEST REQUIRED labels throughout the docs
- PyInstaller Windows bundle: NOT TESTED (build.py exists; run it on Windows)

## Release engineering notes
- `airmouse_pkg/build.py` shadows PyPA `build` when CWD=airmouse_pkg — build
  from the repo root (`python -m build airmouse_pkg/`) or accept the PyInstaller path
