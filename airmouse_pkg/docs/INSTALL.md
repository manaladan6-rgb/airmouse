# AirMouse v19 — Installation

Three supported paths. Option C (Python package + bootstrap) is the
primary v19 distribution — it is the only option that could be built,
tested and verified from the build environment without faking results
(see PACKAGING.md for the decision record).

## Option C (primary): `pip install airmouse`

Works on Windows, Linux and macOS.

- Python **3.9–3.13** (the package declares `requires-python >= 3.9`).
- A virtual environment is recommended:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   Linux/macOS: source .venv/bin/activate
pip install airmouse
airmouse setup
airmouse
```

**Wheels-only fact:** every hard dependency ships prebuilt wheels —
`mediapipe`, `opencv-python`, `pynput`, `numpy` — so no compiler, no
build tools and no MSVC/NMake are ever needed. AirMouse's zero-compiler
law additionally forces `--only-binary :all:` on every engine install it
performs itself (a source build is structurally impossible).

After installing, confirm what you actually got:

```bash
airmouse --version   # must print the release you intended to install
```

(`pip install airmouse` resolves from PyPI; verify the printed version
matches the release notes before relying on it.)

## Windows bootstrap script (the guided path)

`scripts/airmouse-bootstrap.ps1` is a transparent, readable PowerShell
script: it installs Python via winget **only with your consent**, creates
a dedicated venv at `%LOCALAPPDATA%\AirMouse\runtime`, installs the
`airmouse` package from PyPI over HTTPS with `--only-binary :all:`, and
starts `airmouse setup`.

Trust model (from the script header, verifiable by reading it):

- you can read the entire script before running it — no obfuscation, no
  remote second stage;
- package source: PyPI (pypi.org) over HTTPS via pip, wheels only;
- the ONE optional model download (~41.2 MB Vosk en-US) happens LATER,
  inside `airmouse voice-setup`, only with your explicit consent,
  SHA256+SHA512-verified before use;
- nothing is downloaded from any other host by this script;
- pin a release with `.\airmouse-bootstrap.ps1 -Version 19.0.0`.

> **Status:** the script is shipped and script-audited; the end-to-end
> run on REAL Windows hardware is **PHYSICAL_TEST_REQUIRED**
> (see WINDOWS.md for the checklist).

## From source

```bash
git clone <your-fork-url> airmouse
cd airmouse
python -m venv .venv && source .venv/bin/activate
pip install -e airmouse_pkg
python -m airmouse --version
```

The package source of truth is `airmouse_pkg/`; editable install is the
developer path used by the test suite.

## What gets installed where — the single AirMouse home

Everything AirMouse writes lives under ONE home:
`$AIRMOUSE_HOME` if set, else `~/.airmouse` (resolved fresh on every
call by `paths.py` — no split-brain, no scattered state). Windows:
`%USERPROFILE%\.airmouse` (the bootstrap venv is separate, under
`%LOCALAPPDATA%\AirMouse`).

```
~/.airmouse/
├── config.toml            your settings (UTF-8 contract, v16.5.1+)
├── config/                configuration directory
├── memory/                the 5 learning stores (twin, vocabulary, …)
├── skills/                skill definitions
├── workflows/             recorded workflows
├── profile/               your personal interaction profile
├── models/                downloaded local models (vosk model lives here)
├── transcripts/           ONLY transcripts you explicitly saved
├── macros/                recorded macros
├── backups/               safety backups (config-pre-reset-*, …)
├── exports/               user-directed export bundles
├── logs/                  log files
├── cache/                 temporary cache data          (v19)
├── diagnostics/           diagnostic reports             (v19)
├── intelligence/          adaptive-intelligence artifacts
├── calibration.json       hand-reach calibration
├── gaze_calibration.json  gaze calibration
├── gestures.json          custom gesture mappings
├── asr_provider.json      last working ASR provider state
└── .setup_complete        first-run marker (skips the launcher)
```

`airmouse privacy` prints the full storage manifest with purpose +
location for every artifact.

## Offline installation

- `pip download --only-binary :all: -d wheels airmouse` on a connected
  machine, then `pip install --no-index --find-links wheels airmouse` on
  the target machine. That is the standard pip trick; AirMouse adds
  nothing hidden to it.
- AirMouse itself performs NO hidden downloads. The only network fetches
  in the product are (a) the ONE consented Vosk model inside
  `airmouse voice-setup` (size disclosed before download, digest
  verified before use) and (b) `airmouse update --check`, which you
  invoke and which only reads the PyPI JSON API — it never installs.
- The model can also be installed fully manually: download the official
  archive yourself and run `airmouse voice-setup` on a connected box, or
  place it under `~/.airmouse/models/vosk/` — the pipeline verifies the
  same digests either way (12-stage pipeline, see TROUBLESHOOTING.md).

## Upgrade / uninstall / repair / reset

```bash
pip install --upgrade airmouse   # upgrade — preserves the AirMouse home
airmouse repair                  # safe local fixes (never installs)
airmouse reset --config          # remove config (backed up first; asks)
airmouse reset --models          # remove downloaded models (re-downloadable)
airmouse reset --all             # remove the WHOLE home (asks; lists all)
airmouse uninstall               # remove the AirMouse home (asks; summary)
python -m pip uninstall airmouse # remove the package itself
```

Upgrades never touch `~/.airmouse` — your profile, transcripts, macros
and calibration survive. `reset`/`uninstall` always print exactly what
will be removed and require an explicit yes (`--yes` exists for
scripts). `reset --config` backs the config up into `backups/` first.

Every claim in this file is checkable against the code and the test
suite.
