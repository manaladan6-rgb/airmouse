# AirMouse v19 — Packaging

What is built, what is audited, how it is distributed, and what is
honestly NOT built.

## What is built

- **wheel + sdist** via `python -m build` (setuptools backend, isolated
  build env) from `airmouse_pkg/` — the package source of truth.
- **`SHA256SUMS`** — one sha256 line per artifact, generated exactly the
  way the release battery does (round-trip tested).
- **release manifest** — the release record (as in `release/v18`:
  SHA256SUMS + RELEASE_MANIFEST; v19 follows the same contract).

Where things live:

| Artifact | Location |
|---|---|
| Build + audit tests | `tests/test_packaging_security.py` (11 tests) |
| Five-minute benchmark harness | `scripts/five_minute_test.py` |
| Windows bootstrap | `scripts/airmouse-bootstrap.ps1` |
| Package metadata / dependency bounds | `airmouse_pkg/pyproject.toml` |

## The packaging security audit (what the tests prove)

`tests/test_packaging_security.py` builds the wheel+sdist FOR REAL and
then proves, mechanically:

1. **wheel zip integrity** — `zip.testzip()` clean.
2. **no dangerous members** — no `.exe/.dll/.so/.bat/.cmd/.ps1/.com/
   .scr/.msi/.sh` members, no path escapes (`..`, absolute, drive
   letters), no symlink members, **no executable bits** on any file.
3. **no import-time bombs** — no `.pth`, no `sitecustomize.py`, no
   `usercustomize.py` (classic supply-chain execution vectors).
4. **entry-point contract** — `airmouse = airmouse.__main__:main`.
5. **v19 modules packaged** — `model_integrity.py`,
   `capability_registry.py`, `degradation.py`, `evidence.py`,
   `lifecycle.py`, `wizard.py`, `cli_menu.py`, `voice_bootstrap.py`.
6. **corrupted-wheel detection** — a byte-flipped wheel is DETECTED
   (CRC/integrity failure), never silently accepted.
7. **sdist leakage audit** — no `.git`, `.venv`, `venv`, `.test-assets`,
   `__pycache__`, `node_modules`, `.pytest_cache`, `dist`, `bundle`
   inside the sdist; the build sources ARE present.
8. **dependency floor bounds** — every dependency declares a version
   bound (the supply-chain law; no unpinned floor-less deps).
9. **version consistency** — `pyproject.toml` version == package
   `__version__`.
10. **checksum round-trip** — SHA256SUMS generated and verified the way
    the release does.

Root-cause fix caught by this audit in v19: the LICENSE file shipped
with the executable bit (0o755) — fixed to 0644.

## Distribution architecture (v19 Phase 3 decision)

**Option C — Python package + bootstrap — is the primary channel**
(`pip install airmouse`; Windows convenience via
`scripts/airmouse-bootstrap.ps1`). Rationale: it is the only option
that can be built, audited and verified FROM THIS BUILD ENVIRONMENT
without faking results — the whole wheel/sdist surface is covered by
the mechanical audit above and installable in a clean venv.

| Option | Status |
|---|---|
| **C: Python package + bootstrap** | **PRIMARY in v19** (audited, testable here) |
| **A: AirMouseSetup.exe** (PyInstaller one-file, bundled models, codesigned) | **NOT AVAILABLE in v19 — future work**; requires real Windows CI; marked **PHYSICAL_TEST_REQUIRED** |
| **B: portable folder** | **NOT AVAILABLE in v19 — future work**; same CI requirement |

### Concrete CI sketch for Options A/B (future work)

```yaml
# .github/workflows/windows-dist.yml (sketch — not shipped in v19)
runs-on: windows-latest
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python@v5
    with: {python-version: "3.12"}
  - run: python -m pip install build pyinstaller
  - run: python -m build --wheel --sdist airmouse_pkg --outdir dist
  - run: pyinstaller --onefile <entry-spec>      # bundle decision + model policy TBD
  - run: Get-FileHash dist\* -Algorithm SHA256   # SHA256SUMS equivalent
  - uses: actions/upload-artifact@v4
    with: {name: windows-dist, path: dist/}
# codesigning: a signing certificate + secret must exist first —
# unsigned executables are NOT a release channel.
```

Until that pipeline exists and runs green on real Windows,
`AirMouseSetup.exe` does not exist — and no document may pretend
otherwise.

## Dependency bounds (the floors/caps law)

Every dependency has a floor; caps are set where breakage was real:

| Dependency | Bound | Rationale |
|---|---|---|
| `mediapipe` | `>=0.10.9,<1.0` | floor; capped below the 1.0 rewrite |
| `numpy` | `>=1.24.0,<2.3` | floor; capped where the ecosystem breaks |
| `opencv-python` | `>=4.8.0` | floor, **no cap** — accepted risk |
| `pynput` | `>=1.7.6` | floor, **no cap** — accepted risk |

The two uncapped deps are an accepted, documented risk: capping them
has repeatedly caused Windows wheel-availability gaps (the v16.5.3
lesson), so they get floors only and are revisited at EVERY release
instead. The audit test enforces that no dep ever ships floor-less.

## Release checklist (the order)

1. **Test suite** — `pytest tests/` green (v19: 2112 passed / 2 skipped
   / 0 failed).
2. **Offline test** — `airmouse offline-test` 18/18.
3. **Verify** — `airmouse verify` 12/12 automated PASS.
4. **Packaging security** — `pytest tests/test_packaging_security.py`
   green (real build + all audits).
5. **Five-minute harness** — `python scripts/five_minute_test.py
   --budgets` exit 0 (v19 measured 12.6 s on the Linux sandbox; a
   real-Windows number is PHYSICAL_TEST_REQUIRED).
6. **Checksums** — generate `SHA256SUMS` for wheel + sdist.
7. **Manifest** — write the release manifest (versions, digests, env).
8. **Tag** — tag the release commit; artifacts belong to the tag.

Every claim in this file is checkable against the code and the test
suite.
