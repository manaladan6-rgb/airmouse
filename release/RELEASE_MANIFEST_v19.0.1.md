# AirMouse v19.0.1 RELEASE MANIFEST — Public Release Audit hotfixes

Date: 2026-10-12 · Built from: the commit that tag `v19.0.1` points to ·
Builder: Linux sandbox, CPython 3.12.14 (`python -m build`, isolated)

Supersedes v19.0.0 — first release with a GREEN full CI matrix
(ubuntu py3.9/3.10/3.11/3.12 + windows py3.12 + packaging/clean-install).

## Artifacts (GitHub release assets; binaries are not git blobs)

| Artifact | Bytes | SHA256 |
|---|---:|---|
| `airmouse-19.0.1-py3-none-any.whl` | 734,015 | `488230538565f12e62df3c4099dd6c70bf5f0fb1f9cc3a228bdb621b2d9360c2` |
| `airmouse-19.0.1.tar.gz` | 713,649 | `7bbd727b4e8165aec6cbbfbaebe7f90438d2f82b22f99f6b82cc43c8dfc0f543` |

Verify: `sha256sum -c SHA256SUMS_v19.0.1.txt`.

## What changed vs v19.0.0 (all found by the CI matrix — none guessed)

| Fix | Severity | Platform |
|---|---|---|
| Lock reaper `os.kill(pid, 0)` = CTRL_C_EVENT on Windows → OpenProcess probe | HIGH | Windows |
| Piped CLI output crashed on `✓`/`→` (cp1252 pipe codec) → force UTF-8 | HIGH | Windows |
| `tomli` missing on py3.9/3.10 → config parsing silently degraded | HIGH | py<3.11 |
| argparse intermix version difference → version-robust positional parsing | MEDIUM | py3.9-3.11 |
| `os.replace` under concurrent access → bounded retry (`replace_atomic`) | MEDIUM | Windows |
| capabilities enum repr leak / OpenCV WARN noise / lazy mediapipe (3.3x CLI startup) | LOW | all |

## Release gates

| Gate | Result |
|---|---|
| Full suite (local, CPython 3.12) | 2117 passed / 2 skipped / 0 failed |
| CI matrix (GitHub Actions) | 6/6 jobs SUCCESS (ubuntu 3.9-3.12, windows 3.12, packaging) |
| `twine check` | PASSED |
| Clean-venv wheel install | version / `doctor --json` 42 components — green |
| Offline behaviour | unchanged — no new network calls |

## Known gaps (honest)

- Physical-hardware validation remains PHYSICAL_TEST_REQUIRED
  (`WINDOWS_REAL_WORLD_TEST.md` checklist; `airmouse test --guided`).
- The real-Chrome DevTools handshake test is CI-skipped by design
  (hosted runners are uncontrolled desktops) — it runs on dev machines.
- Windows `.exe`/installer: NOT_AVAILABLE (roadmap: signed installer).
- PyPI publishing: maintainer-performed; after `pip install airmouse`,
  `airmouse --version` must print 19.0.1.
