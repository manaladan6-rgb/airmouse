# Contributing to AirMouse

Thank you for helping make AirMouse better. This project values **honest
claims over impressive claims**: every "works" must correspond to a test
or a clearly-labelled limitation.

## The 5-minute developer loop

```bash
git clone https://github.com/manaladan6-rgb/airmouse.git
cd airmouse/airmouse_pkg
pip install -e .                 # editable install
pytest ../tests                  # ~2100 tests, ~45 s
airmouse doctor                  # live sanity check
```

The package lives in `airmouse_pkg/`; tests in `tests/` (run from the
repository root). No virtualenv is forced on you, but one is recommended.

## Ground rules

1. **Every meaningful change gets a test.** Bug fixes get a regression
   test that fails without the fix.
2. **Never count a skipped hardware test as a pass.** Skips must name
   their reason (`-rs` should always read honestly).
3. **Safety code is reviewed line-by-line.** Anything touching model
   downloads, archive extraction, subprocesses, the permission spine or
   the emergency stop needs extra care — and a test that proves the
   unsafe path is *refused*.
4. **Keep the evidence vocabulary honest.** Capabilities are REAL /
   SIMULATED / OPTIONAL / PHYSICAL_TEST_REQUIRED / NOT_AVAILABLE. Never
   upgrade a label because code exists; upgrade it only when a test (or
   documented physical validation) proves the claim.
5. **No hidden network access.** Model downloads are consent-gated and
   checksum-verified; update checks are explicit. If your feature needs
   the network, it asks first.
6. **Preserve user data.** `reset`/`uninstall` must explain what they
   delete and ask first.

## Commit style

Short imperative subject, blank line, body that explains *why*.
Example: `fix(release-audit): plain-string capability states + OpenCV WARN noise dial`.

## Where things live

| Path | What |
|---|---|
| `airmouse_pkg/airmouse/` | the Python package |
| `tests/` | pytest suite |
| `airmouse_pkg/docs/` | user + developer documentation |
| `release/` | release manifests and checksums |
| `docs/dev/WORKLOG.md` | historical development log |
| `WINDOWS_REAL_WORLD_TEST.md` | physical-hardware validation checklist |

## Reporting bugs

Open a GitHub issue with: your OS, Python version, the exact command you
ran, and the output of `airmouse doctor --advanced`. Redact anything you
consider private — though AirMouse stores no telemetry, its logs live on
your disk and may contain file paths.
