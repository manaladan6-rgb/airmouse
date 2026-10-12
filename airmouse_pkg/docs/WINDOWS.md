# AirMouse v19 — Windows

Windows is the primary target platform for v19's instant-deployment
goal. This file states exactly what is Windows-ready in software and
what still needs a real machine.

## Quick path

```powershell
# from the repository root, in PowerShell:
Set-ExecutionPolicy -Scope Process Bypass   # this session only
.\scripts\airmouse-bootstrap.ps1
# or pin a release:  .\scripts\airmouse-bootstrap.ps1 -Version 19.0.0
```

The script: checks for Python 3 (offers `winget install Python.Python.3.12`
WITH a consent prompt if missing), creates a dedicated venv under
`%LOCALAPPDATA%\AirMouse\runtime`, runs
`pip install --only-binary :all: airmouse`, and starts `airmouse setup`.
Flags: `-SkipPython` (Python already present), `-NoSetup`.

Manual equivalent (works everywhere):

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
pip install airmouse
airmouse setup
airmouse
```

Python **3.9–3.13** are supported (`requires-python >= 3.9`).

## The cp312 pocketsphinx packaging fact (why voice routes to vosk)

PocketSphinx 5.1.1 publishes CPython wheels for cp311/cp313 only —
**there is no cp312 wheel on any platform**, and its sdist needs a
C/C++ toolchain (NMake/MSVC). Vosk 0.3.45 ships `py3-none-win_amd64`
wheels with the native Kaldi library bundled — zero compiler on any
CPython 3. AirMouse therefore routes Windows + Python 3.12 to vosk and
NEVER compiles anything (`--only-binary :all:` is structural; this is
the v16.5.3 zero-compiler law). The 41.2 MB model pin was corrected in
v19 — see CHANGELOG and TROUBLESHOOTING.md.

## Camera and microphone privacy settings

Windows gates camera/microphone access per-app and system-wide. If
detection fails with hardware present:

- **Settings → Privacy & security → Camera** → enable *Camera access*,
  *Let apps access your camera*, and *Let desktop apps access your
  camera* (Python/airmouse counts as a desktop app).
- **Settings → Privacy & security → Microphone** → the same three
  switches for the microphone.
- Then re-check with `airmouse doctor` — the camera/microphone rows
  report what they could actually observe.

## Known Windows-specific behaviours

- **UTF-8 config contract** (fixed in v16.5.1): `config.toml` is always
  written and read as UTF-8; a legacy/broken file is repaired by
  `airmouse setup --repair-config` (backup → repair → validate →
  report) or `airmouse repair`.
- **Input automation works out of the box** on Windows — pynput drives
  the real cursor; no Accessibility-style permission step exists.
- **No compiler ever**: every engine install uses wheels only; an MSVC
  failure is structurally impossible.
- The AirMouse home is `%USERPROFILE%\.airmouse` (single home; layout
  in INSTALL.md). `AIRMOUSE_HOME` overrides it.

## REAL Windows validation status — PHYSICAL_TEST_REQUIRED checklist

Nothing in this section has been executed on real Windows hardware.
The sandbox is Linux; claiming otherwise is forbidden. Checklist:

| # | Step | Status |
|---|---|---|
| 1 | Bootstrap script end-to-end on Windows 10/11 x64 (Python install → venv → pip → setup) | **PHYSICAL_TEST_REQUIRED** |
| 2 | Camera flow: real webcam detected, hand tracked on screen, pinch clicks | **PHYSICAL_TEST_REQUIRED** |
| 3 | Microphone flow: real mic captured, local ASR transcribes a spoken command | **PHYSICAL_TEST_REQUIRED** |
| 4 | `airmouse voice-setup` consent flow: size disclosure → download → SHA256+SHA512 verify → VOICE READY | **PHYSICAL_TEST_REQUIRED** |
| 5 | `airmouse` launcher with status dots on a real desktop + plain start into the camera window | **PHYSICAL_TEST_REQUIRED** |
| 6 | Camera/microphone privacy-settings recovery (deny → enable → doctor sees it) | **PHYSICAL_TEST_REQUIRED** |
| 7 | Signed exe / installer distribution | **NOT_AVAILABLE in v19** — future CI work (PACKAGING.md) |

What IS verified without Windows: the full software stack (launcher,
setup 2.0, doctor, capabilities, wizard, lifecycle, model pipeline —
including the real 41 MB Vosk archive end-to-end), 2112 passed /
2 skipped / 0 failed tests, and the 12.6 s five-minute harness run —
all on the Linux sandbox.

Every claim in this file is checkable against the code and the test
suite; the hardware rows above are explicitly handed to the user.
