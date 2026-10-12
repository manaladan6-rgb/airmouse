# AirMouse v19.0.0 — Instant Deployment

**From "pip install" to a working multimodal interaction platform in under five minutes.**

AirMouse turns a webcam, a microphone and (optionally) your eyes into computer control — fully local, offline-capable, no telemetry, no cloud. Every action passes one permission spine; predictions can advise but never execute; the emergency stop always wins.

```bash
pip install airmouse
airmouse setup        # probes hardware, picks your best mode, asks nothing it can answer itself
airmouse              # launcher → [1] Start AirMouse
```

## What is new in v19

- **`airmouse setup` 2.0** — one command, 13 idempotent steps (environment → dependencies → storage → config → camera → microphone → browser → voice extras → input automation → voice model → interaction mode → profile → smoke test).
- **First-run launcher** — a menu with capability lights per mode; no camera or microphone? You still get the best mode that works, clearly labelled.
- **`airmouse wizard`** — a 60–120 s guided interaction lesson that verifies you actually performed each skill.
- **`airmouse doctor` 2.0** — a human-readable health report in ~2 s (`--advanced` / `--json` available).
- **Lifecycle commands** — `repair` (safe local fixes), `reset` (explain-then-ask), `uninstall` (explains exactly what is deleted), `update --check` (explicit, check-only).
- **Capability registry + degradation ladder** — one honest answer to "what can AirMouse do on THIS machine right now", with evidence labels: REAL / SIMULATED / OPTIONAL / PHYSICAL_TEST_REQUIRED / NOT_AVAILABLE.
- **12-stage model integrity pipeline** — consent-gated downloads, SHA-256 + SHA-512 verification before extraction, Zip-Slip protection, executable-member refusal, atomic staging.
- **`airmouse voice-setup`** — picks the smallest compatible offline ASR engine for your Python/OS (never compiles from source) and fetches the official Vosk en-US model with verified checksums.
- **Agent layer** — AIP 1.0 stdio protocol, Python + JavaScript SDKs, multi-agent permissions; agents go through the same permission spine as humans, no bypass.

## Release-audit fixes (this exact tag vs. the original candidate)

- `airmouse capabilities` no longer leaks the raw `ComponentState.OPTIONAL` enum repr (Python 3.11+).
- `airmouse doctor` no longer sprays OpenCV C++ WARN noise on camera-less machines (real errors stay visible).
- CLI startup **3.3× faster** (`airmouse --version` 1.63 s → 0.47 s, measured) via lazy mediapipe import.
- PyPI metadata completed (`readme`, project URLs, classifiers); repository front door (README, LICENSE, SECURITY, CONTRIBUTING, CHANGELOG, CODE_OF_CONDUCT, CI) added at the repo root.

## Verification evidence

| Gate | Result |
|---|---|
| Test suite | **2115 passed / 2 skipped / 0 failed** (skips name their reason: no Chrome binary, no local ASR engine in the build sandbox) |
| `airmouse verify` | 12/12 PASS |
| `airmouse offline-test` | 18/18 OK |
| Packaging security tests | 11/11 PASS |
| `twine check` | PASSED (clean) |
| Clean-venv wheel install | version / `doctor --json` (42 components) / 0.46 s startup — all green |
| Clean-venv sdist install + `setup --check-only` | honest 13-step report — green |
| CLI startup (`airmouse --version`) | **0.47 s** (Linux x86_64, CPython 3.12, headless) |
| `airmouse doctor` | **1.9 s** (dominated by the honest hardware probe) |
| Physical hardware (webcam/mic on your machine) | **PHYSICAL TEST REQUIRED** — checklist: `WINDOWS_REAL_WORLD_TEST.md` + `airmouse test --guided` |

## Artifacts & checksums

| Artifact | SHA-256 |
|---|---|
| `airmouse-19.0.0-py3-none-any.whl` (732,203 B) | `3d84d3df69095fa3641d4874d70d7bd1a0035df6ed2a0dc1f99913325a28b0f0` |
| `airmouse-19.0.0.tar.gz` (711,676 B) | `db7ca44034597867d6b8064651b7316d17a8061c4e4fe886ade5ea9450cf46b5` |

`SHA256SUMS_v19.0.0.txt` / `SHA512SUMS_v19.0.0.txt` are attached; verify with `sha256sum -c SHA256SUMS_v19.0.0.txt`. Artifacts were built from this exact tag (`python -m build`, isolated) — the manifest with the full provenance is `release/RELEASE_MANIFEST_v19.0.0.md` in the repository.

## Compatibility

- **Python 3.9 – 3.12** (3.13 works for the core; the optional PocketSphinx extra ships cp313 wheels, Vosk is pure-python + bundled native lib)
- **Windows 10/11** primary target · **Linux (X11)** supported · **macOS** experimental
- Nothing to configure: all state lives in `~/.airmouse/`

## Known limitations (honest)

- Physical-hardware behaviour on *your specific* devices is implemented and software-verified, but final on-device confirmation is a run-through of `WINDOWS_REAL_WORLD_TEST.md` — the code cannot self-certify hardware it has never touched.
- The model-integrity system corrected a historical Vosk model digest; older docs may show the old (wrong) pin.
- No signed Windows installer yet (scaffolded; see `docs/PACKAGING.md` roadmap).
- Wayland restricts raw input automation by design of the display protocol.
- PyPI publication is performed by the maintainer; after `pip install airmouse`, `airmouse --version` must print `19.0.0`.

## Security & privacy

No telemetry. No hidden network calls. The only network operations are a consent-gated, checksum-verified model download and an explicit `update --check`. Computer actions pass one permission spine — recognition confidence alone can never authorize an action, and authorization failures close the gate, never open it. See [SECURITY.md](https://github.com/manaladan6-rgb/airmouse/blob/main/SECURITY.md).

---

**MIT licensed.** Issues and PRs welcome — every behaviour change needs a test that proves it.
