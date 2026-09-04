# AirMouse v15.1.0 — Release Manifest

**Release**: v15.1.0 (hardening release of v15.0.0 — no feature removal, backward compatible)
**Date**: 2026-09-04
**Branch**: main · **Head**: see `git rev-parse HEAD`
**Requires**: Python >= 3.9 · Windows 10/11 (primary target), macOS, Linux

## Artifacts

| Artifact | Size | SHA-256 |
|---|---|---|
| `airmouse-15.1.0-py3-none-any.whl` | 474,542 bytes | `5b399b5eba72d152c017f47e83ce42a663c1cec67e3b5d4a7a1c0517765c1daf` |
| `airmouse-15.1.0.tar.gz` (sdist) | 441,648 bytes | `691a684d4cceffdc8d318a0c7814404eea232d7aeb5366f0393c0150bf0b8558` |

Checksums also in `SHA256SUMS_v15.1.0.txt` (verify with `sha256sum -c SHA256SUMS_v15.1.0.txt`).

## Wheel contents

- 95 files: 85 Python modules (incl. `airmouse/intelligence/` twin subpackage), 4 browser-extension assets, console script `airmouse = airmouse.__main__:main`
- Core dependencies (auto-installed by pip): `mediapipe>=0.10.9,<1.0`, `opencv-python>=4.8.0`, `pynput>=1.7.6`, `numpy>=1.24.0,<2.3`
- Optional extras: `ocr` (pytesseract, Pillow), `sound` (sounddevice), `voice` (SpeechRecognition, pyaudio), `tts` (pyttsx3)
- agent-core: unchanged since v15.0.0 — the `airmouse_agent_core-1.0.0-py3-none-any.whl` from the v15.0.0 release remains current (verified: `git diff v15.0.0..HEAD -- agent-core/` is empty)

## Verification summary (§23 labels)

| Section | Result |
|---|---|
| AUTOMATED VERIFICATION | PASS — 1312 tests (1056 baseline + 256 new), 0 failed, 0 skipped |
| SIMULATION VERIFICATION | PASS — guided lab 7/7 simulation tests (intelligence, browser, agent, multi-agent, recovery, offline, installation) |
| REAL WINDOWS VERIFICATION | NOT PERFORMED IN SANDBOX — procedure: `WINDOWS_REAL_WORLD_TEST.md` |
| PHYSICAL HARDWARE VERIFICATION | NOT TESTED — webcam / microphone / hand tracking / gaze / real browser = ACTION REQUIRED (`airmouse verify`) |
| NOT TESTED | RF sensing hardware, real cloud/local ASR engines, PyInstaller Windows bundle |

## Clean-environment validation performed (§18/§19)

- Fresh venv → installed **only** the wheel → `airmouse --version` / `doctor` / `self-test` (13 pass) / `offline-test` (18/18) / `test` / `verify` all OK
- Upgrade path: v15.0.0 wheel → `pip install --upgrade` → v15.1.0 works; uninstall removes the CLI; reinstall works
- Perf (headless sandbox): `--version` 1.4s · `doctor` 2.6s · `verify` 1.6s · `test` 1.6s (budgets 4x, enforced in `tests/test_release_perf.py`)

## Privacy model (shipped defaults)

LOCAL · OFFLINE · NO TELEMETRY · NO CLOUD — telemetry off in code (`telemetry_enabled = False` default, verified by tests), storage under `~/.airmouse` (honors `AIRMOUSE_HOME`), atomic crash-safe persistence with schema versioning, corruption recovery, export/reset/delete controls (`airmouse memory ...`, `airmouse privacy`).

## Release engineering notes

- `python -m build` must be invoked from a directory that does NOT contain `airmouse_pkg/build.py` (it shadows the PyPA `build` module); e.g. `cd /tmp && python -m build /path/to/airmouse_pkg --outdir /path/to/airmouse_pkg/dist`
- Windows PyInstaller bundle (`build.py --windows`) exists but was NOT built/tested in this sandbox — artifact intentionally not shipped from Linux
