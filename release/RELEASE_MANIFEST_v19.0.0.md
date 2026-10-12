# AirMouse v19.0.0 RELEASE MANIFEST — INSTANT DEPLOYMENT

Date: 2026-10-12 · Built from: commit at tag `v19.0.0` · Builder: Linux
sandbox, CPython 3.12.14 (`python -m build`, isolated)

## Artifacts (release/v19/)

| Artifact | Bytes | SHA256 |
|---|---:|---|
| `airmouse-19.0.0-py3-none-any.whl` | 710,304 | `b72c74f5b0fa8d3a8bfc23f4b2df3ab22d8507fae5c36e62ce692a2aec64f28d` |
| `airmouse-19.0.0.tar.gz` | 669,983 | `72bf93a1a822aed3555c55a6f0e27396af9ed564c71c1e8efd5943eb53ae1264` |

SHA512 sums in `SHA512SUMS_v19.0.0.txt`; SHA256 sums in
`SHA256SUMS_v19.0.0.txt` (verify: `sha256sum -c SHA256SUMS_v19.0.0.txt`).

## Wheel identity

- entry point: `airmouse = airmouse.__main__:main`
- package version: 19.0.0 (== pyproject, asserted by test)
- requires-python: >=3.9 · wheels-only core deps (mediapipe, opencv-python,
  pynput, numpy) — never compiles, never installs a toolchain
- audited by `tests/test_packaging_security.py`: no executables, no
  symlinks, no `.pth`/`sitecustomize`, corrupted-wheel detection,
  sdist leakage audit, dependency floor bounds

## The model trust anchor (corrected in v19)

Official Vosk small en-US 0.15 archive — `model_integrity.VOSK_MODEL`:

| Property | Value |
|---|---|
| URL | https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip |
| Size | 41,205,931 bytes |
| SHA256 | `30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498` |
| SHA512 | `d56243ba75343fd42a238ac358f9deef4acc690bdc9d816f1499930f25504bce87495b6f448089a87b426a8d81b31b80525ae5ed6c0ca4fb95a306576f53bc5a` |
| Structure | 20 members, single root `vosk-model-small-en-us-0.15/`, no executables, no symlinks |
| License | Apache-2.0 (model DATA) |

Measured live during the v19 baseline audit — the 4th independent
measurement and 2nd complete download in project history, cross-checked
against the independently published Alpine aports digest. The v16.5.3
pin (`507575b9…` / 41,205,932 B) was WRONG; every consented download
failed verification. Historical release documents are preserved as
records of what was believed at the time; the correction is recorded in
`docs/V19_BASELINE.md` §3 and this manifest.

## Release gates (all green on the tagged tree)

| Gate | Result |
|---|---|
| Full suite | 2112 passed / 2 skipped / 0 failed |
| Clean-clone suite | 2098 passed / 16 skipped / 0 failed (skips: packaging tests without the optional `build` tool + real-archive asset tests + 2 baseline skips — all explained) |
| `airmouse verify` | 12/12 PASS |
| `airmouse offline-test` | 18/18 OK |
| Packaging security | 11/11 PASS |
| Five-minute harness | PASS — 12.8 s software total (budgets in docs/V19_FIVE_MINUTE_TEST.md) |
| Clean-venv wheel battery | install → version → setup → capabilities → offline-test → doctor --json: all green |
| Git tree at tag | clean; annotated tag; no force-push; history intact |

## Known gaps (honest)

- Real-Windows validation (bootstrap script run, camera/mic flows,
  voice-setup consent flow end-to-end): PHYSICAL_TEST_REQUIRED —
  checklist in `docs/WINDOWS.md`.
- Windows `.exe`/installer (Phase 3 Options A/B): NOT_AVAILABLE in v19;
  spec in `docs/PACKAGING.md` for future CI.
- PyPI publishing: performed by the maintainer (no credentials in the
  build environment). After `pip install airmouse`, `airmouse --version`
  must print 19.0.0 — if it does not, the published artifact is not this
  release.
