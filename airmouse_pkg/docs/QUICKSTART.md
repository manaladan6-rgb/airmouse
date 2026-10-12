# AirMouse v19 — Quickstart

The shortest honest path from zero to a working AirMouse. Every command
below exists in the v19 CLI; nothing here claims hardware that was not
tested — live camera/microphone behaviour on a real Windows machine is
**PHYSICAL_TEST_REQUIRED** and the tools tell you so themselves.

## The absolute minimum

```bash
pip install airmouse
airmouse setup
airmouse
```

That is the whole install. `airmouse setup` is consent-gated (it never
installs or downloads anything without an explicit Y) and writes the
first-run marker, so plain `airmouse` then starts the app instead of
showing the launcher. On Windows you can instead run the transparent
bootstrap script (`scripts/airmouse-bootstrap.ps1` — see INSTALL.md and
WINDOWS.md; its real-machine end-to-end run is PHYSICAL_TEST_REQUIRED).

## The 5-minute journey

```
INSTALL ──► FIRST RUN ──► HARDWARE CHECK ──► GUIDED SETUP ──► LIVE TEST ──► WORKING
 pip          airmouse       airmouse          airmouse         airmouse      airmouse
 install      (launcher      doctor            setup            wizard        (camera
 airmouse     with status    (or menu          (14 steps,       (6 steps,     window +
              dots)          choice 2)         measured s)      verdict)      gestures)
```

Measured on the Linux sandbox (no camera/mic): the whole software path —
cold import, CLI, doctor, setup, capabilities, wizard, voice plan,
offline self-test — took **12.6 s** in the five-minute harness
(`scripts/five_minute_test.py`, budget 300 s). The camera/microphone
stages are PHYSICAL_TEST_REQUIRED. `airmouse` is offline-first: nothing
is downloaded without your consent, and the ONE model download happens
later, only inside `airmouse voice-setup`.

## What works without any hardware

- `airmouse doctor`, `airmouse capabilities`, `airmouse wizard` — every
  probe runs, missing hardware is reported as missing (never faked).
- Simulation/diagnostic mode: with no camera and no microphone the
  ladder reports `SAFE_MINIMAL` (simulation — the launcher and doctor
  say so); with no input automation either (e.g. a headless box) it
  reports `DIAGNOSTIC`. Either way AirMouse starts, honestly labelled,
  and nothing is faked.
- `airmouse offline-test` (18 checks) and `airmouse verify` (12
  automated checks) — deterministic, network-isolated, pass on any
  machine.
- Input automation needs a desktop session (Windows: out of the box;
  Linux: a display server; macOS: Accessibility permission) — on a
  headless box this is reported, not pretended.

## What needs a webcam / microphone

- Hand-gesture control needs a webcam (any laptop camera works).
- Voice control needs a microphone plus the one-time local voice model
  (`airmouse voice-setup`, ~41.2 MB, consent-gated, SHA256+SHA512
  verified — the pin was corrected in v19, see CHANGELOG).
- Live hand tracking, real transcription, real clicks on a real desktop
  are **PHYSICAL_TEST_REQUIRED** in every v19 document.

## The first 10 commands worth knowing

| Command | What it does |
|---|---|
| `airmouse setup` | 14-step guided setup (consent-gated, measured seconds) |
| `airmouse wizard` | 60-second interaction wizard — what works here, honestly |
| `airmouse doctor` | health report + interaction panel (`--advanced` = full detail) |
| `airmouse capabilities` | what AirMouse can do on THIS machine, with evidence labels |
| `airmouse voice-setup` | optional local voice engine + model (consent-gated) |
| `airmouse test --guided` | interactive validation lab (simulations labelled) |
| `airmouse verify` | 12 automated checks + the physical items that need you |
| `airmouse repair` | safe local fixes (config, models, marker) — installs nothing |
| `airmouse reset --config\|--models\|--all` | remove selected state — asks first, explains what |
| `airmouse uninstall` | remove the AirMouse home — shows content summary, asks first |

## If something fails

- Start with `airmouse doctor --advanced` — every row carries
  WHAT / WHY / FIX.
- `airmouse repair` fixes the common local breakage (broken config,
  corrupted model, missing marker) without installing anything.
- Symptom → diagnosis → fix tables: see `docs/TROUBLESHOOTING.md`.
- Install/upgrade questions: `docs/INSTALL.md`. Windows specifics:
  `docs/WINDOWS.md`. Full command surface: `docs/CLI_REFERENCE.md`.

Every claim in this file is checkable against the code and the test
suite (2112 passed / 2 skipped / 0 failed at v19).
