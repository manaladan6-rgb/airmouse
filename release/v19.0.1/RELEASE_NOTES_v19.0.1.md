# AirMouse v19.0.1 — Public Release Audit hotfixes

**Patch release over v19.0.0 — the first AirMouse release with a fully green CI matrix (Linux py3.9–3.12 + Windows + packaging/clean-install).**

```bash
pip install airmouse
airmouse setup        # once
airmouse              # launcher → [1] Start AirMouse
```

## What this patches (all found by real CI failures — none guessed)

| Fix | Impact |
|---|---|
| **Windows: model-install lock reaper sent a real Ctrl+C** — `os.kill(pid, 0)` is a POSIX liveness probe; on Windows signal 0 **is** `CTRL_C_EVENT` | A second model install against a fresh lock could interrupt the running installer; now uses an `OpenProcess`-based probe and never signals. Probe errors fail safe ("busy — refuse honestly"). |
| **Windows: piped CLI output crashed on `✓`/`→`** — a stdout PIPE defaults to the ANSI codepage (cp1252) | `airmouse doctor \| tool` and CI captures now get UTF-8; real consoles are untouched. |
| **Python 3.9/3.10: no TOML parser at all** | `tomli>=1.1.0; python_version < "3.11"` is now declared — config validation silently degraded without it. |
| **Same command, different behaviour per Python version** — argparse intermix parsing changed in 3.12 | `airmouse doctor --json --to X extra` now behaves identically on 3.9–3.12; unknown options still exit 2. |
| **Windows: atomic store writes failed under concurrent access** | `fileio.replace_atomic` retries sharing violations with a bounded backoff. |
| CLI startup 3.3× faster (lazy mediapipe import); capabilities enum-repr leak fixed; no more OpenCV C++ WARN noise on camera-less machines | Usability. |

## Verification evidence

| Gate | Result |
|---|---|
| CI matrix (GitHub Actions, this tag's commit) | **6/6 jobs SUCCESS** — ubuntu py3.9/3.10/3.11/3.12 + windows py3.12 + packaging/clean-install |
| Local full suite (CPython 3.12, headless) | 2117 passed / 2 skipped / 0 failed (skips name their reasons) |
| `twine check` | PASSED |
| Clean-venv wheel install | `airmouse --version` → 19.0.1; `doctor --json` → 42 components |
| Offline behaviour | unchanged — no new network calls |

## Artifacts & checksums

| Artifact | SHA-256 |
|---|---|
| `airmouse-19.0.1-py3-none-any.whl` (734,015 B) | `488230538565f12e62df3c4099dd6c70bf5f0fb1f9cc3a228bdb621b2d9360c2` |
| `airmouse-19.0.1.tar.gz` (713,649 B) | `7bbd727b4e8165aec6cbbfbaebe7f90438d2f82b22f99f6b82cc43c8dfc0f543` |

`SHA256SUMS_v19.0.1.txt` / `SHA512SUMS_v19.0.1.txt` attached; verify with `sha256sum -c SHA256SUMS_v19.0.1.txt`. Provenance: `release/RELEASE_MANIFEST_v19.0.1.md` in the repository, built from this exact tag with isolated builds.

## Compatibility

Unchanged from v19.0.0: Python 3.9–3.12 (3.13 works for the core), Windows 10/11 primary, Linux (X11) supported, macOS experimental. Storage stays at `~/.airmouse/` — upgrading is a plain `pip install --upgrade airmouse`; your config, models and profiles are preserved.

## Known limitations (honest)

- Physical-hardware validation (your webcam/microphone) remains **PHYSICAL_TEST_REQUIRED** — run `airmouse test --guided` and the `WINDOWS_REAL_WORLD_TEST.md` checklist.
- The real-Chrome DevTools handshake test is CI-skipped by design (hosted runners are uncontrolled desktops); it runs on developer machines.
- No signed Windows installer yet (roadmap).

See [v19.0.0](https://github.com/manaladan6-rgb/airmouse/releases/tag/v19.0.0) for the full Instant-Deployment feature list.
