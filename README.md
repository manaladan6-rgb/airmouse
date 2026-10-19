# AirMouse

**Control your computer with your hands, your voice, and your eyes — locally, offline-capable, no cloud, no telemetry.**

AirMouse is a multimodal human-computer interaction platform for Windows, Linux and macOS.
A webcam turns hand gestures into cursor movement and clicks. An offline speech model turns
spoken commands into actions. Gaze steering (optional) follows your eyes. Everything runs
through one safety spine: every action passes a permission gate, every prediction can only
*advise* — never execute on its own — and an emergency stop always wins.

---

## What's new in 19.0.3

- **One authoritative E-STOP for every route.** A new central safety gate
  (`airmouse.safety_gate`) carries the process-wide emergency stop, shutdown state,
  backend availability and confirmation flags. Gestures, browser voice commands,
  browser-bridge actions, AIP agent execution, macro replay and PEACE scrolling all
  consult it — and `airmouse estop trip` (cross-process flag file) stops a RUNNING
  session within one frame, with no camera or preview required.
- **Global E-STOP hotkeys** — `Ctrl+Alt+E` trips, `Ctrl+Alt+R` resets, armed every
  session with honest availability reporting.
- **TF-001 root fix** — `HandTracker()` no longer crashes with `NameError: name 'mp'
  is not defined`; actionable camera/model/dependency errors instead.
- **Real audio path + honest voice banners** — utterance PCM is buffered and handed to
  the offline recognizer as real frames (`transcribe(None)` eliminated); the session
  banner says `LIVE` only when a real local ASR provider is available, otherwise
  `GRAMMAR-ONLY` with the exact gap and remedy; the v5 path discloses its Google cloud
  upload on the banner.
- **Truthful CLI** — `doctor --json` carries the same exit contract as the panel
  (READY 0 / PARTIAL 1 / BLOCKED 2); dictation edits ("delete last word") edit the
  dictation buffer, never files; deictic `move over there`; autostart entries launch
  the real CLI; self-test counts SKIP rows honestly.
- **Grammar 90 commands / 11 namespaces** (+14 dictation TEXT_EDIT intents, +1 deictic
  move); 44 CLI subcommands incl. `estop`.

---

## The 60-second answer

| Question | Answer |
|---|---|
| What is AirMouse? | Control the mouse/keyboard with hand gestures (webcam), voice (offline speech recognition), or gaze — all on your machine. |
| Does my computer support it? | Windows / Linux / macOS, Python 3.9+. Nothing else is required to try it. |
| How do I install it? | `pip install airmouse` |
| How do I start it? | `airmouse setup` once, then just `airmouse` |
| How do I control it? | Wave to move, pinch to click, say "click" (after `airmouse voice-setup`) |
| Does voice work offline? | Yes — after the one-time `airmouse voice-setup`, recognition is 100% local |
| What permissions does it need? | Camera and/or microphone only if you use those modes; Accessibility/Input permission for controlling the mouse |
| How do I stop it immediately? | `Ctrl+Alt+E` (global hotkey), `airmouse estop trip` from any terminal, `Esc` in the AirMouse window, or `Ctrl+C` in the terminal |
| How do I fix problems? | `airmouse doctor` → `airmouse repair` → [Troubleshooting](airmouse_pkg/docs/TROUBLESHOOTING.md) |
| How do I remove it? | `airmouse uninstall` (asks first, explains what is deleted), then `pip uninstall airmouse` |

```powershell
# The whole journey — under 5 minutes:
pip install airmouse
airmouse setup        # detects hardware, picks your best mode, asks nothing it can answer itself
airmouse              # launcher opens → choose [1] Start AirMouse
# inside: wave to move the cursor, pinch to click,
#         say "click that" (after: airmouse voice-setup)
```

---

## What AirMouse does (plain English)

1. **See** — your webcam watches for hand landmarks (MediaPipe), your microphone listens for
   speech (offline Vosk/PocketSphinx), your eye position can steer the cursor (optional).
2. **Understand** — hand poses, motion gestures and spoken phrases are fused with context
   into an *intent* ("the user wants to click there").
3. **Ask permission** — every intent passes a risk gate. Risky or destructive actions need
   explicit approval. Recognition confidence alone can never authorize anything.
4. **Act & verify** — the action runs through one controlled execution path, the result is
   observed, and failures are recovered from — deterministically, not with guesswork.

Nothing leaves your machine: no cloud AI, no telemetry, no hidden network calls. The only
network access AirMouse ever performs is a model download **you explicitly request** with a
verified checksum, and an update check **you explicitly request**.

## Verified in this release (v19.0.3)

Status vocabulary: **REAL** = implemented and verified by automated tests ·
**SIMULATED** = verified in a labelled simulation only · **OPTIONAL** = available when you
add the dependency/hardware · **PHYSICAL TEST REQUIRED** = implemented but needs a real
device to confirm · **NOT AVAILABLE** = not in this release.

| Capability | Status | Notes |
|---|---|---|
| Hand-gesture cursor control (17 poses, 10 motion gestures, pinch click/zoom) | REAL / PHYSICAL TEST REQUIRED | needs any webcam; gesture pipeline fully tested in software |
| Offline voice commands + dictation (Vosk / PocketSphinx, no cloud) | REAL / PHYSICAL TEST REQUIRED | verified end-to-end with real speech on CPython 3.12; one-time model download (~40 MB) with consent + SHA-256 verification |
| Gaze steering | OPTIONAL / PHYSICAL TEST REQUIRED | requires one-time calibration (`airmouse --gaze-calibrate`) |
| 12-stage model integrity pipeline (consent → download → hash-verify → safe extract → atomic install) | REAL | blocks Zip-Slip, refuses executable archive members, verifies before use |
| `airmouse setup` / `doctor` / `repair` / `reset` / `uninstall` / `update --check` lifecycle | REAL | idempotent, preserves user data, explains deletions before asking |
| First-run launcher (menu, capability lights, 60-second interaction wizard) | REAL | degradation ladder: if hardware is missing you get the best mode that still works |
| Agent layer (AIP 1.0 stdio protocol, Python + JavaScript SDKs, multi-agent permissions) | REAL | agents go through the same permission spine as humans — no bypass |
| Deterministic simulation + failure-injection test harness | REAL | runs on machines with no camera/mic at all |
| Offline speech on a machine you can install Vosk on | OPTIONAL | `airmouse voice-setup` picks the smallest compatible engine for your Python/OS |

| E-STOP contract (movement gate + button release from every trip source) | REAL | one central gate locked by `tests/test_estop_contract.py` + `tests/test_final_form_safety.py`; ESC / [x] / voice / long-blink / safety system / `Ctrl+Alt+E` / `airmouse estop` all funnel through one latch |
| Real-timestamp physics (gap → hold + re-acquire, no teleport/sweep) | REAL | deterministic benchmarks in `tests/test_physics_benchmark.py`; on-camera behavior PHYSICAL TEST REQUIRED |
| Gaze calibration loads + quality-gates at runtime | REAL | a fit below "fair" quality or a screen-size mismatch cannot drive gaze clicks |
| `airmouse logs` / `airmouse config validate` / `diagnostics --live-sim` | REAL | bounded, injection-safe, honest SIMULATED labels |
| Hand-model download (consent, pinned SHA-256, atomic install, kill-switch) | REAL | `AIRMOUSE_NO_MODEL_DOWNLOAD=1` refuses all downloads |
| Real-camera accuracy, dwell ergonomics, OS-cursor feel | PHYSICAL TEST REQUIRED | run `WINDOWS_REAL_WORLD_TEST.md`; sandboxed CI has no camera |

The full 111-row evidence matrix lives at
[airmouse_pkg/docs/CAPABILITY_MATRIX.md](airmouse_pkg/docs/CAPABILITY_MATRIX.md).

## Supported platforms

| OS | Status |
|---|---|
| Windows 10/11 (x64) | Primary target — input automation works out of the box; `scripts/airmouse-bootstrap.ps1` gives a one-command install |
| Linux (X11) | Supported — input automation needs a running display server (Wayland is limited by the OS) |
| macOS | Experimental — grant Accessibility permission to Input Monitoring |

Python **3.9 – 3.13**. Core dependencies are installed automatically by pip:
`mediapipe`, `opencv-python`, `pynput`, `numpy`.

## Installation

```bash
pip install airmouse
```

That is the whole install. AirMouse handles its own storage (`~/.airmouse/`), never asks
you to edit config files, and degrades gracefully if a camera or microphone is missing.

<details>
<summary>Windows one-command bootstrap (optional)</summary>

```powershell
irm https://raw.githubusercontent.com/manaladan6-rgb/airmouse/main/scripts/airmouse-bootstrap.ps1 | iex
```

This checks Python, installs AirMouse into a per-user environment and prints the exact
next command. It runs no hidden downloads — every step it takes is printed. If you prefer
to inspect before running: download the script and read it; it is ~120 readable lines.

</details>

<details>
<summary>From source (developers)</summary>

```bash
git clone https://github.com/manaladan6-rgb/airmouse.git
cd airmouse/airmouse_pkg
pip install -e .
pytest ../tests   # 2271 passed / 16 skipped / 0 failed
                  # (CPython 3.12.15, Linux headless; `python -m pytest tests -q`)
```

</details>

## First-run setup

```bash
airmouse setup
```

One command. It probes your OS, Python, camera, microphone, GPU/CPU, installs nothing
without telling you, verifies any model it fetches (SHA-256 + SHA-512), writes your config,
picks your best interaction mode and creates your profile. Run it again any time — it is
idempotent and never corrupts an existing setup.

## Launch & stop

```bash
airmouse          # opens the launcher menu
airmouse --skip   # start tracking immediately, skip the menu
```

- **Move** the cursor: hold your hand up, point with your index finger.
- **Left-click**: pinch (thumb + index together). **Right-click**: peace sign. **Scroll**: three fingers, move hand up/down. **Drag**: open palm. **Freeze**: fist.
- **Stop instantly**: `Ctrl+Alt+E` (global hotkey, armed every session), `airmouse estop trip` (works against a RUNNING session from any terminal, no camera needed), `Esc` in the AirMouse window, or `Ctrl+C` in the terminal. Voice: "stop" (when voice is set up). E-STOP outranks everything — movement, clicks, voice, browser, agents, macros and prediction — and always releases a held mouse button. Press `[x]` in the window or `Ctrl+Alt+R` to reset the stop state.
- **Watch the physics live**: `airmouse diagnostics --live-sim` shows the real filter engine fed a labelled synthetic hand (raw vs filtered position, velocity in px/s, tracking state, fusion weight). With a camera: `airmouse diagnostics --live`.

## Test your hardware

```bash
airmouse doctor        # human-readable health report (~2s): what works, what's missing, exact fixes
airmouse doctor --advanced   # full technical detail
airmouse doctor --json       # machine-readable
airmouse self-test     # deterministic self-checks (exit code 1 on any FAIL row)
airmouse verify        # automated checks + the list of physical tests still open
airmouse wizard        # 60-second guided interaction lesson
airmouse logs 50       # recent command outcomes (bounded, UTC, rotation-capped)
airmouse logs 20 --json
airmouse config path   # where your config.toml lives (AIRMOUSE_HOME-aware)
airmouse config show   # print it
airmouse config validate   # unknown sections = errors, unknown keys = warnings
airmouse diagnostics --live-sim  # live physics telemetry (labelled synthetic input)
airmouse diagnostics --live      # live telemetry from a real camera
```

## Offline voice setup

```bash
airmouse voice-setup
```

Installs the smallest ASR engine compatible with your Python and OS (no compilers needed —
AirMouse never builds from source) and downloads the official Vosk English model (~40 MB)
**after showing you the size and asking**. The download is checksum-verified before
extraction and stored under `~/.airmouse/models/`. After that, recognition is fully
offline. `airmouse voice-status` shows the current state; `airmouse transcribe --text`
practises dictation without a microphone.

## Diagnostics & troubleshooting

Start with `airmouse doctor`. It never dumps stack traces at you — it names the problem
and the exact fix. For deeper issues:
[TROUBLESHOOTING.md](airmouse_pkg/docs/TROUBLESHOOTING.md) ·
[WINDOWS.md](airmouse_pkg/docs/WINDOWS.md) · `airmouse repair` (safe, keeps your data).

## Privacy & local data

Everything AirMouse knows about you lives in **one place**: `~/.airmouse/` (config, models,
profiles, logs, cache, skills, diagnostics). No telemetry. No cloud. No hidden network
calls. `airmouse privacy` prints exactly what is stored and how to erase each part.
`airmouse reset` / `airmouse uninstall` always explain what will be deleted and ask first.

## Safety & permissions

The hierarchy is strict: **EMERGENCY STOP > human override > safety > permission > agent >
prediction.** A predicted intention can *advise* — it can never execute. Agents (via the
AIP protocol or SDKs) use the same permission spine as human input, with no bypass path.
Authorization that is unavailable or invalid fails **closed**.

## Architecture (one paragraph)

`SENSE → RECOGNIZE → CONTEXT → INTENT → RISK → PERMISSION → ACTION → OBSERVE → VERIFY →
RECOVER → LEARN → ADAPT` — one event bus, one fusion layer, one world model, one execution
spine. Details: [V19_ARCHITECTURE.md](airmouse_pkg/docs/V19_ARCHITECTURE.md).

## Testing & benchmarks

2,117 tests pass at release (2 honest skips: no Chrome binary and no local ASR engine in
the CI sandbox — each skip names its reason). Skipped hardware tests are never counted as
passes. Performance numbers are measured, not claimed — `airmouse benchmark` reproduces
them on your machine. The release audit measured on Linux x86_64 / CPython 3.12
(headless sandbox): `airmouse --version` ≈ 0.5 s, `airmouse doctor` ≈ 1.9 s (dominated by
the honest hardware probe, not imports), `setup --check-only` ≈ 0.3 s.

## Contributing

Issues and pull requests welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The developer
quick loop is `pip install -e .` + `pytest`; please add a test for every behaviour you
change. Security-sensitive code (downloads, extraction, subprocesses, permissions) gets
reviewed line-by-line — see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).

## Known limitations (honest list)

- **No physical hardware validation is included in this release's automated evidence** —
  the release is software-verified only. Real webcam tracking on your specific machine,
  real microphone arrays and real hotkey delivery still need the on-device checklist at
  [WINDOWS_REAL_WORLD_TEST.md](WINDOWS_REAL_WORLD_TEST.md) (or run `airmouse verify`
  for the ACTION_REQUIRED physical list).
- Offline ASR needs a one-time model install via `airmouse voice-setup` (consent-gated,
  checksum-verified, never auto-downloaded). Without an engine + model the voice session
  runs GRAMMAR-ONLY (deterministic phrase matching — no dictation), and the banner says so.
- The root-level `airmouse_simple.py` single-file demo is **legacy and unsupported** — it
  does not use the central safety gate (no E-STOP) and is not packaged. Use the `airmouse` CLI.
- Wayland: raw input automation is restricted by the OS display protocol.
- macOS: experimental.
- The browser bridge needs Chrome/Edge with a debug port; without it, a clearly-labelled
  simulated browser keeps browser commands working offline.
- Gaze is experimental and needs per-user calibration. Webcam gaze estimation is
  an assistive pointing aid, not precise eye tracking; on-camera accuracy is
  PHYSICAL TEST REQUIRED.
- Air-gapped machines: set `AIRMOUSE_NO_MODEL_DOWNLOAD=1` and place
  `hand_landmarker.task` in `~/.airmouse/` manually (SHA-256 is verified).

## Roadmap

- Signed Windows installer (the code-signing pipeline is scaffolded, not yet published).
- More offline voice languages (Vosk has 20+ models; only en-US ships by default today).
- Wayland-native input backend.
- Deeper gaze support (blink/dwell selection).

---

*AirMouse keeps its claims falsifiable: every "works" in this README corresponds to a test
you can run (`airmouse verify`) or an explicit `PHYSICAL TEST REQUIRED` note. If you find a
gap between claim and reality, that is a bug — please open an issue.*
