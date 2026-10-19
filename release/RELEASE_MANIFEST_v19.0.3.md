# AirMouse v19.0.3 — Release Manifest

**Branch:** `engineering/final-form` (rebuilt from the verified published
baseline `df264944168fed20acb19f1c4c9dc22d1e40ed21` = v19.0.2 after an
infrastructure loss; rebuilt work re-verified end-to-end).

## Verdict

**RELEASE CANDIDATE WITH EXPLICIT LIMITATIONS** — software-verified,
physically UNVALIDATED. Every automated release gate that can run in a
headless Linux sandbox passed; every gate that requires real hardware,
Windows CI, or GitHub publication is explicitly listed as OUTSTANDING
below. Nothing here claims more than the evidence shows.

## Artifacts (built clean from the exact candidate tree)

| Artifact | Bytes (decimal) | SHA-256 |
|---|---|---|
| `airmouse-19.0.3-py3-none-any.whl` | 796,343 | `52d3154356765fe37371302e533919cd1ee3c5e6135b359f5b28e7fa2cdf5e4c` |
| `airmouse-19.0.3.tar.gz` | 772,549 | `18617597f80812ebd5d36b5b359d40df0e214f30f33832681e6aade4cc40f781` |

SHA-512: `SHA512SUMS.txt` next to the artifacts (both verify OK).
Verification: `sha256sum -c SHA256SUMS.txt` && `sha512sum -c SHA512SUMS.txt`.

## Install verification (both artifacts, fresh venvs, outside the source tree)

- wheel → fresh venv: `airmouse --version` = 19.0.3; imports resolve to
  site-packages; `estop trip/status/reset` cycle works (0.3 s per op).
- sdist → second fresh venv: `airmouse --version` = 19.0.3; central
  safety gate refuses `authorize('gesture')` while tripped
  (`(False, 'estop: …')`), refuses unknown action classes unclassified,
  reset restores.
- Wheel contents: 126 files; no `__pycache__`/`.pyc`, no test files, no
  `airmouse_simple.py` (intentionally absent — legacy/unsupported), no
  credential-like strings (pattern scan clean); `Requires-Dist` includes
  mediapipe/opencv/pynput/numpy as core deps, vosk under the `voice` extra.

## Automated test evidence (exact commands)

Environment: Linux headless sandbox, CPython 3.12.15.

| Command | Result |
|---|---|
| `python -m pytest tests -q` | **2271 passed / 16 skipped / 0 failed**, exit 0 (~36 s) |
| `pytest` (repo root, bare; root pytest.ini) | 2287 collected, 0 collection errors |
| CLI probes | `--version` = 19.0.3; `doctor --json` exit 1, overall PARTIAL (honest); `self-test` exit 0, "13 pass, 1 skip, 0 optional, 1 hardware, 0 fail"; `estop trip/status/reset` cycle OK; `estop bogus` exit 2; `estop --trip` rejected by argparse (exit 2, no doc advertises it) |

The 16 skips are honest, named environment skips (no Chrome binary, OCR
opt-in, real-archive offline determinism, no local ASR engine, no
display for GUI-adjacent checks, etc.).

## What was fixed in 19.0.3 (all with regression tests)

- TF-001 HandTracker production-path NameError — root-caused (PEP 562
  cannot satisfy bare LOAD_GLOBAL), fixed with a real-module-global
  `_LazyMp` delegating proxy; camera probe BEFORE detector creation;
  actionable dependency/model/camera errors; dead camera never lies.
- One central safety gate for ALL consequential routes (gestures,
  browser voice, browser bridge, AIP, macros, PEACE scroll), with a
  cross-process E-STOP flag and the `airmouse estop` CLI + global
  Ctrl+Alt+E/R hotkeys armed every session.
- Exact-origin browser-bridge allowlist (substring bypass closed);
  payload size caps; unknown commands rejected; loopback ≠ authorization.
- AIP EXECUTE fail-closed for unclassified actions; confirmation
  required for agent actions by default.
- Macro replay cancellable (cooperative stop checks, bounded joins,
  fail-closed start).
- Real utterance PCM → offline ASR (no `transcribe(None)`); honest
  LIVE vs GRAMMAR-ONLY banners with exact reasons; v5 cloud disclosure;
  no fabricated confidence; self-test SKIP semantics.
- `doctor --json` structured + exit contract (READY 0/PARTIAL 1/
  BLOCKED 2); dictation edits are buffer-only; deictic move; usable
  autostart launch; vosk declared in the `voice` extra.

## OUTSTANDING (explicit — not claimed as done)

1. **CI on the exact candidate commit** — triggered after the branch
   push; Windows legs NOT TESTED until a Windows run exists.
2. **Physical validation (WWRT)** — webcam/hand-tracking, real
   microphone + local ASR, real mouse/keyboard movement, E-STOP during
   movement, PEACE scroll while stopped, macro cancellation on real
   hardware, global-hotkey press on Windows/macOS, gaze calibration:
   all **NOT TESTED** (no hardware in this environment).
3. **GitHub release/tag publication** — deliberately NOT created by
   this operation: the current authorization is the branch push only.
   The tag `v19.0.3` does NOT exist; no release page was created.
