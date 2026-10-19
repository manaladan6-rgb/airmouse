# Changelog

## v19.0.3 — One Safety Gate, Real Audio, Truthful Diagnostics (2026-10-17)

Centralized-safety and voice-truth release. Full user-facing notes in
the root CHANGELOG.md; engineering summary (rebuilt from the verified
published baseline df26494 = v19.0.2 after an infrastructure loss; the
rebuild was re-verified end-to-end against the same contracts):

* **Central safety gate (`safety_gate.py`, NEW)** — one process E-STOP
  latch + shutdown + backend-availability + confirmation flags; the
  session binds it (`bind_session`) and every consequential route
  consults it: gesture spine, browser voice side-door,
  BrowserController.execute, AIP EXECUTE (fail-closed for unclassified
  actions), macro replay stop_check, PEACE scroll (per-iteration).
  Cross-process E-STOP flag file + `airmouse estop` CLI (subcommand 44).
* **TF-001 root fix** — `_LazyMp` delegating proxy bound as a real
  module global (PEP 562 could not satisfy bare LOAD_GLOBAL);
  actionable ImportError; camera probe BEFORE detector creation with
  actionable failure; tolerant release(); dead-camera read() never
  raises or lies.
* **TF-004/005 voice truth** — `feed_audio` hands real buffered
  utterance PCM to the recognizer (`transcribe(None)` eliminated,
  AST-verified); no fabricated confidence; honest LIVE vs GRAMMAR-ONLY
  banners with exact-reason strings (`session_banner_state()`); v5
  cloud banner discloses the Google upload; self-test SKIP semantics
  (installed-but-unusable = SKIP + reason; FAIL never downgraded; SKIP
  counted).
* **Truthful CLI** — `doctor --json` structured + exit contract
  (READY 0 / PARTIAL 1 / BLOCKED 2); dictation TEXT_EDIT intents edit
  the buffer only (files untouchable); deictic `move over there`;
  autostart entries launch `<python> -m airmouse --skip` +
  `verify_launch_command`; voice extra declares vosk (TT-22).
* **E-STOP hotkeys (`estop_hotkey.py`, NEW)** — Ctrl+Alt+E/R armed
  every session, honest unavailability reporting, bounded cleanup.
* **Ordered shutdown** — gate shutdown first, macro bounded cancel,
  voice stop, bounded joins, honest unterminated-worker report.
* 76+ new deterministic regression tests
  (tests/test_final_form_safety.py, tests/test_final_form_macros_safety.py,
  tests/test_final_form_voice.py, tests/test_final_form_cli.py,
  tests/test_final_form_tracker.py).
* Suite at v19.0.3: **2271 passed / 16 skipped / 0 failed** (37.3 s,
  Linux headless, Python 3.12.15; bare pytest collects 2287, 0 errors).
* The legacy root-level `airmouse_simple.py` single-file demo is now
  explicitly marked LEGACY/UNSUPPORTED in its docstring (no central
  safety gate, no E-STOP) and remains excluded from the wheel/sdist.

## v19.0.2 — Real Physics, E-STOP Contract, Honest Diagnostics (2026-10-16)

Safety, physics and honesty release. Full user-facing notes in the root
CHANGELOG.md; engineering summary:

* **E-STOP contract enforced end-to-end** — `gate_continuous` now gates all
  three cursor-move paths; `trip_estop` releases held mouse buttons from
  every trip source via `estop_release_fn`; the v9 safety-system EMERGENCY
  level syncs to the spine each frame; voice dispatch refuses while
  estopped; SHAKE/hand-loss release un-gated (release is always
  risk-reducing); stale pinch/peace/trackpad state cleared on hand loss;
  macro replay finish hook; scroll clamped ±100; partial key combos roll back.
* **Real timestamps** — tracker → DirectTracker → OneEuro⊕Kalman now use
  real monotonic frame times (synthetic +33 ms removed); measured
  frame-to-frame dt for physics/fps; >0.25 s gap → filter reset + one-frame
  HOLD (no teleport/sweep); landmark validation boundary (non-finite input
  holds, never crashes); honest per-axis px/s velocity; auto-precision
  dead-zone direction fixed; precision/tune retune in place (no snap).
* **Gaze end-to-end** — saved calibration loads by default (was never
  loaded: gaze was confined to the central band); quality gate ("fair"+ +
  screen-size match) before gaze clicks; gaze_config plumbed (dwell time /
  min confidence were dead keys); dwell pauses during blinks; long-blink
  E-STOP = 1.2 s (documented contract); fullscreen calibration window with
  real screen size.
* **v9 tick hoisted out of the debug window** — hiding the preview no
  longer silently disables gaze/voice/fusion.
* **New CLI** — `airmouse logs [N|--json]` (1 MiB rotate, UTC,
  injection-safe), `airmouse config [path|show|validate]`,
  `airmouse diagnostics --live-sim|--live` (bounded, rate-limited, honest
  SIMULATED/REAL labels).
* **Hardened hand-model download** — consent on TTY, pinned versioned URL
  + SHA-256, atomic .part install, 30 s timeout,
  `AIRMOUSE_NO_MODEL_DOWNLOAD` kill-switch, corrupt-cache re-download;
  `paths.model_file()` adoption (AIRMOUSE_HOME split-brain fixed).
* **Honest exits** — setup 1 on FAILED / 2 on ACTION_REQUIRED; self-test 1
  on FAIL rows. Lazy cv2 (~0.4 s faster CLI). OBSENSOR noise dial.
  PyInstaller install consent-gated. AIRMouse→AirMouse branding sweep.
* **Browser bridge** — Host/Origin validation refuses DNS-rebinding and
  non-local clients (403); extension unchanged.
* Tests: 2180 passed / 7 skipped / 0 failed (baseline 2112/7/0).
  +75 tests incl. E-STOP contract, CLI matrix, deterministic physics
  benchmarks, cli_log, bridge hardening.

## v19.0.1 — Public Release Audit hotfixes (2026-10-12)
## v19.0.1 — Public Release Audit hotfixes (2026-10-12)

Fixes found by the first full cross-platform CI matrix (ubuntu py3.9-3.12
+ windows py3.12 + packaging/clean-install) and the public-release audit:

* **Windows: the model-install lock reaper sent a real Ctrl+C** —
  `os.kill(pid, 0)` is a POSIX liveness probe; on Windows signal 0 IS
  CTRL_C_EVENT.  The reaper now uses an OpenProcess-based probe
  (`_pid_alive`) and never signals; probe errors fail safe ("busy,
  refuse honestly").
* **Windows: piped CLI output crashed on '✓'/'→'** — stdout/stderr are
  now reconfigured to UTF-8 in `main()` (the v16.5.1 encoding contract,
  completed for pipes/redirects).
* **py3.9/3.10: no TOML parser at all** — `tomli>=1.1.0;
  python_version < "3.11"` is now a declared dependency (config parsing
  silently degraded without it).
* **CLI: same invocation, different behaviour per Python version** —
  `doctor --json --to X garbage` errored on py3.9-3.11 and passed on
  3.12 (argparse intermix change); the parser is now version-robust
  with strictness preserved (unknown options still exit 2).
* **Windows: atomic file replace could fail under concurrent access** —
  `fileio.replace_atomic()` adds a bounded retry on sharing violations;
  store writes and corruption quarantine use it.
* Repo front door (README/LICENSE/SECURITY/CONTRIBUTING/CHANGELOG/
  CODE_OF_CONDUCT), CI matrix, PyPI metadata (readme/URLs/classifiers),
  CLI startup 3.3x faster (lazy mediapipe), capabilities enum-leak fix,
  OpenCV WARN-noise dial, and environment-honest test skips.

## v19.0.0 — INSTANT DEPLOYMENT (2026-10-12)

Built on v16.5.3 reality (fresh-clone audit in `docs/V19_BASELINE.md`).
The lost v16.5.4/v18 subsystems that v19 depends on were RECONSTRUCTED
from the recorded specs; everything else was implemented against what
actually exists. No hardware claims anywhere: live Windows validation
stays PHYSICAL_TEST_REQUIRED.

### P0 fixed — the wrong model pin (trust-anchor repair)

- v16.5.3 pinned the official Vosk en-US model as
  `507575b9efd23f96…` / **41,205,932 B** — WRONG. Every consented
  download failed SHA256 verification: the flagship "voice works in
  five minutes" flow was broken at the trust-anchor level.
- v19 re-measured LIVE from alphacephei.com (4th independent
  measurement, 2nd complete download in project history; cross-checked
  against the independently published Alpine aports digest):
  **41,205,931 B**, SHA256
  `30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498`,
  SHA512
  `d56243ba75343fd42a238ac358f9deef4acc690bdc9d816f1499930f25504bce87495b6f448089a87b426a8d81b31b80525ae5ed6c0ca4fb95a306576f53bc5a`,
  20 members, single root, no executables, no symlinks.
- Historical documents stay untouched; the correction is recorded in
  `docs/V19_BASELINE.md §3` and the new pin lives in
  `model_integrity.VOSK_MODEL` (single source of truth;
  `voice_bootstrap` delegates to it).

### Reconstructed: the 12-stage model integrity pipeline

- NEW `airmouse/model_integrity.py` — temp file → fsync → byte count →
  SHA256 → SHA512 → CRC → zip structure (+ compression-bomb guard) →
  zip-slip refusal → executable/symlink-member refusal → staged
  extract → structure validation → ATOMIC install with rollback.
  Lock-protected with stale-lock/stale-staging reaping; disk-full
  (ENOSPC) reports "disk full — nothing was changed" and rolls back; a
  previous model is never lost to a failed install.
- NEW 8-state `ModelState` machine: `MODEL_MISSING`,
  `MODEL_DOWNLOAD_REQUIRED`, `MODEL_DOWNLOADING`, `MODEL_VERIFYING`,
  `MODEL_INSTALLING`, `MODEL_READY`, `MODEL_CORRUPTED`,
  `MODEL_INCOMPATIBLE` (`MODEL_UNKNOWN` only when the status record
  itself fails). Consumed by doctor / capabilities / setup / repair.
- Bugs caught by my own tests and fixed during reconstruction:
  SHA512 pin had dropped a character (caught by the real-archive
  test); the staged-structure walk rejected the staging root `.`;
  the backup was retired BEFORE post-install verification (rollback
  would have had nothing to restore); the temp archive leaked on
  success; the per-member danger scan now runs before the layout
  check so the precise refusal reason wins.

### Capability layer: registry, degradation, evidence

- NEW `airmouse/evidence.py` — the indivisible Reality
  (REAL / SIMULATED / OPTIONAL / PHYSICAL_TEST_REQUIRED / NOT_AVAILABLE)
  × Epistemic (OBSERVED / INFERRED / PREDICTED / UNKNOWN) label. No API
  returns "just REAL"; only a successful runtime probe can make REAL.
- NEW `airmouse/degradation.py` — the readiness ladder
  FULL / DEGRADED / SAFE_MINIMAL / DIAGNOSTIC as a pure function over
  observed booleans; SAFE_MINIMAL is deliberately NOT usable; losing
  input automation drops to SAFE_MINIMAL with an explicit reason.
- NEW `airmouse/capability_registry.py` — fuses the twelve-section
  probe + the ASR registry + the model record into ONE honest answer to
  "what can AirMouse do on THIS machine, right now, and how do we
  know?".
- CLI: `airmouse capabilities` is now the user-facing registry (evidence
  labels per row; `--json` machine view; `--aip` preserves the AIP
  agent view). `airmouse doctor` gains the human interaction panel on
  the normal path, `--advanced` (verbose alias), and embeds
  `interaction_readiness` in `--json`.

### Deployment surface: launcher, setup 2.0, lifecycle, wizard

- NEW launcher (`cli_menu.py`): plain `airmouse` on a first run shows
  the friendly menu — 1 Start AirMouse / 2 Test My Hardware / 3 Setup /
  4 Learn Gestures / 5 Settings / 6 Diagnostics (advanced) / 7 Privacy /
  0 Help — plus status dots probed live (never guessed). The menu never
  blocks the app; after `airmouse setup` writes the marker, plain
  `airmouse` starts directly.
- Setup 2.0 (`setup_wizard.py`): 11 → **14 steps** (adds models,
  interaction, profile) with per-step MEASURED seconds and a
  "measured setup time" total — actual, not estimated.
- NEW lifecycle (`lifecycle.py`): `airmouse reset
  --config|--models|--all` (exact removal list, explicit confirmation,
  config backed up first; `--all` warns about profiles/transcripts/
  macros and prints total MB), `airmouse repair` (safe local fixes
  only — structure, config, model re-verify with corrupted-model
  auto-removal, marker; pip is NEVER run — red-team tested),
  `airmouse uninstall` (content summary + MB + the exact pip command
  BEFORE asking), `airmouse update --check` (explicit-only PyPI JSON
  check; honest offline answer; never downloads or installs).
- NEW `airmouse wizard` — the six-step 60-second interaction wizard
  (camera → hand → gesture → voice → input → verdict); a step passes
  only when its probe succeeded on THIS machine; verdict
  READY/PARTIAL/BLOCKED from the same ladder the doctor uses.
- Home layout completed: `logs/`, `cache/`, `diagnostics/` join the
  single-home layout (`$AIRMOUSE_HOME` else `~/.airmouse`, resolved
  fresh on every call).

### Measurement, packaging, distribution

- NEW `scripts/five_minute_test.py` — the automated acceptance harness:
  a fresh simulated user (temp home, no cached state) through cold
  import → CLI → doctor → setup → capabilities → wizard → voice plan →
  offline self-test, every stage MEASURED with perf_counter, budgets
  enforceable, missing hardware reported PHYSICAL_TEST_REQUIRED, never
  faked. **Measured 12.6 s total on the Linux sandbox** (budget 300 s).
- NEW `tests/test_packaging_security.py` (11 tests): real wheel+sdist
  build; dangerous-member audit (executables/symlinks/path
  escapes/executable bits); no `.pth`/`sitecustomize` import-time
  bombs; entry-point contract; v19 modules packaged; corrupted-wheel
  detection; sdist leakage audit; dependency floor bounds; version
  consistency; SHA256SUMS round-trip. ROOT-CAUSE FIX caught by the
  audit: the LICENSE shipped with the executable bit (0o755 → 0644).
- Distribution decision (Phase 3): **Option C — Python package +
  bootstrap — is the primary channel**, because it is verifiable from
  the build environment. Options A (AirMouseSetup.exe) and B (portable
  folder) are documented as FUTURE WORK requiring real Windows CI
  (PyInstaller, codesigning) in `docs/PACKAGING.md` — NOT AVAILABLE in
  v19, never faked.
- NEW `scripts/airmouse-bootstrap.ps1` — the transparent Windows
  bootstrap (winget Python with consent → dedicated venv →
  `--only-binary :all:` PyPI install → `airmouse setup`), trust model
  in its header. Shipped and script-readable; the end-to-end run on
  real Windows hardware remains PHYSICAL_TEST_REQUIRED.
- NEW documentation set: `docs/QUICKSTART.md`, `docs/INSTALL.md`,
  `docs/WINDOWS.md`, `docs/TROUBLESHOOTING.md`, `docs/PACKAGING.md`;
  `docs/CLI_REFERENCE.md` and `docs/CAPABILITY_MATRIX.md` extended;
  `docs/V19_BASELINE.md` records the Phase 0 audit.

### Verification

- Test suite: **2112 passed / 2 skipped / 0 failed**
  (v16.5.3 baseline 2027 + 85; skips are honest).
- `airmouse offline-test` 18/18 · `airmouse verify` 12/12 ·
  `airmouse repair` green on a fresh home.
- Not done (honestly): any real-Windows execution — bootstrap run,
  camera flow, microphone flow, voice-setup consent flow, exe build.
  See `docs/WINDOWS.md` for the PHYSICAL_TEST_REQUIRED checklist.

## v16.5.3 — WINDOWS NATIVE ASR BOOTSTRAP & ZERO-COMPILER VOICE RUNTIME (2026-09-06)

The v16.5.2 runtime was honest, and its honesty exposed the FINAL
blocker on the real Windows 11 machine (CPython 3.12 x64, sounddevice
+ PortAudio working, microphone READY):

```text
pip install pocketsphinx
  → downloads pocketsphinx-5.1.1.tar.gz          (sdist — no wheel)
  → CMake selects NMake
  → nmake missing / CMAKE_C_COMPILER missing
  → installation FAILS
```

**PyPI fact (verified via the PyPI JSON API):** PocketSphinx 5.1.1
publishes CPython wheels for cp311 and cp313 ONLY — there is no cp312
wheel on ANY platform, and the sdist needs a C/C++ toolchain.  Vosk
0.3.45 publishes `py3-none-win_amd64` wheels (13.7 MB) with the native
Kaldi library BUNDLED — they install on ANY CPython 3 with NO compiler.

The failure was therefore classified as a **platform packaging gap**,
NOT a microphone bug and NOT an AirMouse voice bug:
`NATIVE_ASR_PACKAGE_UNAVAILABLE`.

### The zero-compiler law (§4)

- **`airmouse` never compiles native software.**  Every engine install
  runs `python -m pip install --only-binary :all:` — pip ITSELF
  refuses source builds, so a NMake/MSVC failure is structurally
  impossible.  AirMouse never installs or configures a compiler
  toolchain and never tells the user to.
- No arbitrary DLLs, no unofficial binaries, no `curl | powershell`.
  The only network fetches are official PyPI wheels (via pip) and the
  ONE official lightweight Vosk model archive (alphacephei.com,
  Apache-2.0 DATA, 41.2 MB zip / 70.9 MB unpacked) — consent-gated,
  size-disclosed BEFORE download, SHA256-verified
  (`507575b9…6b00a`) BEFORE extraction, extracted with Zip-Slip
  protection and executable-member refusal into the AirMouse home only.

### `airmouse voice-setup` (§8/§9) — the user experience

```text
AIRMouse Voice Setup
Checking microphone................ READY
Checking local ASR................. NOT READY
Checking pocketsphinx wheel compatibility........ NOT FOUND
Checking vosk wheel compatibility........ FOUND
Installing lightweight local voice engine (vosk)...
Fetching the official lightweight model…
Verifying ASR...................... READY
VOICE READY
Provider: Vosk
Mode: MIC LIVE
Network required: NO
Audio uploaded: NO
```

Flags: `--check-only`/`--dry-run` (inspect + plan, installs nothing),
`--provider NAME`, `--repair` (re-verify + fix), `--yes` (scripted),
`--verbose`.  When no compatible runtime exists the command prints the
honest `VOICE SETUP COULD NOT COMPLETE` panel with the reason and the
next step — never a mysterious pip traceback.  When nothing is
installable and the microphone is fine, the machine-level diagnosis is
`NATIVE_ASR_PACKAGE_UNAVAILABLE` with report-only alternate-runtime
detection (e.g. `py -3.13`).

### ASRBootstrapManager (§3/§15) — `airmouse/voice_bootstrap.py`

`detect_platform() / detect_python_version() / detect_architecture() /
detect_installed_engines() / detect_wheel_compatibility() /
recommend_installation() / install_supported_lightweight_runtime() /
install_vosk_model() / verify_installation() / rollback_if_failed()` —
deterministic (a static, versioned, disclosed wheel-knowledge table;
pip remains the final authority behind `--only-binary :all:`), with
explicit states `NOT_INSTALLED / INSTALLED_NOT_IMPORTABLE /
IMPORTABLE_NOT_INITIALIZED / INITIALIZATION_FAILED / READY /
RUNTIME_FAILED` and obstacles `NO_COMPATIBLE_WHEEL /
SOURCE_BUILD_REQUIRED / USER_DECLINED / INSTALL_FAILED /
OFFLINE_MODEL_REQUIRED / VERIFY_FAILED`.

### vosk honesty — the sandbox-reproduced divergence, killed (§14)

The registry previously reported vosk `AVAILABLE` ("model loads on
first use") even with NO local model, while the engine's own
`available()` honestly failed — the same divergence class v16.5.2
killed for pocketsphinx.  Now: the vosk probe resolves the model
deterministically (`explicit arg > $AIRMOUSE_VOSK_MODEL_PATH >
config [voice] vosk_model_path > AirMouse models/vosk/`); with no
model the registry reports `INSTALLED` (importable, initialization
`MODEL MISSING`) and the FIX is `airmouse voice-setup` — registry and
engine can never disagree again.

### Doctor / voice-status now bootstrap-aware (§2/§14)

The FIX lines come from the bootstrap manager, not hardcoded pip text:
on a machine where `pip install pocketsphinx` would fail, doctor and
voice-status recommend `airmouse voice-setup` (the zero-compiler path)
or state the `NATIVE_ASR_PACKAGE_UNAVAILABLE` diagnosis with actionable
official options.

### `airmouse voice-benchmark` (§22)

Deterministic local benchmark: engine startup, first-result latency,
average utterance latency, CPU time, peak RAM, disclosed
package/model sizes.  Recognition quality is measured ONLY with real
speech (`--wav sample.wav`) — never claimed without samples.

### Dual-interpreter evidence (§5/§7)

- **Python 3.12** (the user's machine): vosk installs zero-compiler;
  REAL speech transcribed end-to-end locally
  ("hello bro this is a real AirMouse voice test" → recognized, ~0.8 s;
  "open browser" → exact match → OPEN intent with `app: browser`).
- **Python 3.13**: the FULL suite is green AND PocketSphinx 5.1.1
  installs cleanly from its official cp313 wheel (real Decoder init
  verified) — on 3.13 the lightweight-first registry selects
  pocketsphinx (bundled model, zero downloads).  AirMouse supports
  both interpreters; the wheel matrix in the bootstrap knows which
  engine fits which.

### Tests & gates

Suite: **2027 passed / 2 honest skips / 0 failed** (3.12) and
**2028 passed / 1 honest skip / 0 failed** (3.13) — baseline
1991/2/1-env-shift.  New matrix (`tests/test_voice_bootstrap.py`):
THE cp312 regression, plan determinism, never-silently-compile
(subprocess never invoked on refusal), consent-decline, offline
refusal, verify-failure → rollback, state mapping, vosk model
resolution + registry/engine agreement, SHA256 mismatch → discard,
Zip-Slip + executable-member refusal, honest §8 failure panel,
benchmark honesty, red-team source scan (no eval/exec/os.system/
shell=True; official sources only), and a real-pipeline test with an
honest skip.  `airmouse verify` 12/12 automated PASS; `offline-test`
18/18.

## v16.5.2 — LIGHTWEIGHT LOCAL VOICE RUNTIME HOTFIX (2026-09-05)

A focused integration fix.  The first real v16.5.1 Windows run showed
the defect this release kills:

```text
airmouse doctor --verbose
  → Transcription providers READY — local ASR: pocketsphinx [ACTIVE]
airmouse transcribe
  → provider: simulated_stream
  → SIMULATED provider — install a local ASR engine for real transcription
```

Doctor and transcribe each had their OWN provider-detection
implementation, and they disagreed on the same machine.  Doctor's
path (`offline_voice.detect_providers()`) accepted a provider on a
bare `hasattr(sr.Recognizer(), "recognize_sphinx")` — True whenever
SpeechRecognition is installed, without ever importing pocketsphinx.
Transcribe's path (`voice_stack.pick_provider()`) required a real
`find_spec("pocketsphinx")`.  When the engine package is missing or
broken, doctor said ACTIVE while transcribe honestly (but silently,
to the user) ran the deterministic test provider.  That silent REAL→
SIMULATED downgrade is exactly what the mission forbids.

### The fix — ONE authoritative registry (§2/§3)

- **`airmouse.asr_registry` (new)** — `ASRProviderRegistry` is the
  single source of truth.  `discover()` probes every engine honestly;
  `available()` / `select()` / `active()` / `diagnose()` are the only
  selection API; doctor, transcribe, `voice-status`, the academies
  and `offline_voice.detect_providers()` ALL go through the same
  instance.  Provider states are exactly
  `NOT_INSTALLED / INSTALLED / AVAILABLE / INITIALIZABLE / ACTIVE /
  FAILED / DISABLED`.
- **The anti-hasattr probe**: PocketSphinx is ACTIVE only after a REAL
  initialization — an actual `pocketsphinx.Decoder` build with the
  bundled en-US model (measured 0.35–0.42 s, cached process-wide).
  A broken wheel/DLL that imports as metadata now fails the probe and
  is reported `FAILED` with its exact reason — never "ACTIVE".
- **Deterministic selection (mission priority)**: explicit user
  configuration (`--provider` / env `AIRMOUSE_ASR_PROVIDER` /
  config `[voice] asr_provider`) → previously selected working
  provider (persisted best-effort to `<home>/asr_provider.json`) →
  lightweight local (pocketsphinx) → higher-quality (vosk, whisper —
  never mandatory, models never auto-downloaded) → simulation ONLY
  when nothing works.  `"none"/"off"` is honoured (DISABLED), and an
  explicit choice that fails falls through to the next candidate —
  never to a fake.
- **The invariant holds by construction (§16)**:
  `doctor.active_asr_provider == transcribe.selected_provider` is now
  a shipped regression test (`tests/test_asr_registry.py`), plus the
  CLI-level variant `test_cli_transcribe_provider_matches_registry`.

### Doctor and microphone, unified (§3/§4)

- `capabilities._detect_speech` reports from the registry: ACTIVE
  requires the real init pass (detail: `initialization verified
  (0.35s)`), installed-but-broken engines appear as WARNING rows with
  the exact reason + `FIX: airmouse voice-status --verbose`, and "no
  usable engine" says the detection was a real initialization probe,
  not an import flag.
- **ONE microphone detector** (`asr_registry.detect_microphone`):
  sounddevice/PortAudio FIRST (the lightest existing stack — PyAudio
  is NOT a hard requirement), SpeechRecognition/PyAudio enumeration
  as fallback.  Doctor's Microphone row, the voice stack, transcribe
  and the academies all share it.  It reports backend, device count,
  device names, channels, sample rate and the default input.

### `airmouse transcribe` fixed (§5/§6/§7)

- With a usable local provider the banner reads
  `provider: pocketsphinx (local ASR engine)` — never
  `simulated_stream` while PocketSphinx works.
- **MIC LIVE mode** (mission §5 pipeline): sounddevice-first capture →
  30 ms frames → shipped `EnergyVAD` silence detection → provider →
  transcript + timestamp.  New `mic_capture` module: sounddevice is
  the primary ear, the SpeechRecognition/PyAudio ear is the fallback,
  and every failure raises `MicCaptureError` with an actionable
  `.fix` — device busy, invalid device, permission failure and
  "no speech detected" are all printed verbatim, never swallowed.
- **`--text`** forces TEXT PRACTICE (typed input through the real
  pipeline, explicitly labelled "NOT microphone validation");
  **`--mic N`** selects an input device; **`--provider NAME`** pins
  the engine.  `--offline` still forces the deterministic test
  provider — and a forced simulation no longer claims the machine
  lacks ASR (it says "requested via --offline").
- **Failure is loud and explains itself (§7)**: when no engine can be
  selected the banner prints WHY per engine
  (`pocketsphinx: INITIALIZATION FAILED — <reason>`) plus
  `FIX: airmouse voice-status --verbose (install: pip install
  pocketsphinx SpeechRecognition)`.
- **Never fabricate confidence (§12)**: PocketSphinx exposes none, so
  its segments show `conf: unavailable` (new `confidence_known` flag
  through `Transcript` → `TranscriptSegment` → export JSON) instead
  of the old invented 0.6.
- **Regression fix caught by the suite**: `simulate=True` +
  `mic_mode=True` used to print `mode: MIC LIVE` for a SIMULATED
  provider on machines with ASR installed — simulation now always
  forces text mode.

### voice-status becomes THE debugging tool (§8)

- `airmouse voice-status [--verbose]` renders the mission's §8 view
  from ONE registry call: MICROPHONE (detected/devices/selected/
  backend/rate/channels), ASR PROVIDERS (installed / importable /
  model / initialization / runtime / role, with reason + probe time
  under `--verbose`) and TRANSCRIPTION (real microphone: AVAILABLE,
  provider, mode, offline: YES, audio storage: NONE, network:
  NOT REQUIRED).

### Voice Academy rides the real pipeline (§10/§11)

- Practice-mode resolution now uses the unified mic detector + the
  registry: mic + ACTIVE provider ⇒ REAL MICROPHONE PRACTICE through
  `mic_capture` (sounddevice-first); everything else stays honestly
  labelled TEXT PRACTICE.  Typed input still can never pass a real
  microphone lesson.  COMMAND (`open browser` → `Open browser`) and
  DICTATION (`hello bro comma how are you question mark` →
  `Hello bro, how are you?`) both verified through the real
  post-processing chain.

### Windows-first + quality gates (§14–§23)

- No Linux-only assumptions in the new code (sounddevice + pure
  Python); no Bash/WSL/Docker required; no cloud, no audio storage,
  no network for local ASR.
- **Suite: 1992 passed / 2 honest skips / 0 failed** (baseline
  1957/1/0 → +35 tests, zero regressions).  The 4 environment-shifted
  v16.5.1 tests that assumed "the sandbox has no ASR" were rewritten
  to fake whole environments self-consistently (find_spec + probe +
  ctor) so they pass identically on machines WITH engines installed.
- `airmouse verify` 12/12 automated PASS (honest ACTION_REQUIRED rows
  for physical hardware); `airmouse offline-test` 18/18.
- **Performance (§18, measured on the release tree)**: ASR startup
  (registry discover incl. the real Decoder init) 424 ms one-time,
  then cached; provider selection 0.4 ms; per-utterance pipeline
  (push → partials → finalize) 0.1–0.5 ms; VAD record loop ≈0 ms CPU
  per 30 ms frame; peak RSS 118 MB for the whole voice process
  (dominated by pre-existing numpy/OpenCV imports).  The default
  stack stays lightweight: PocketSphinx + sounddevice only.
- Security scan of the new voice modules: no eval/exec/os.system/
  shell=True/subprocess; red-team phrases (`run powershell …`,
  `execute cmd …`, `delete everything …`, `curl/wget/download …`)
  resolve at most to structured, permission-gated intents — the ASR
  layer itself never executes anything (regression-tested).

## v16.5.1 — WINDOWS REALITY & TEACHING UX HOTFIX (2026-09-05)

A hardening / UX / real-hardware-validation release. v16.5.0's first
real Windows run validated webcam gestures (move, click, double click,
right click, scroll, zoom) but exposed a config-encoding defect and a
voice-teaching UX that was too much like a grammar quiz. v16.5.1 fixes
the encoding class of bug at its root, makes every doctor warning
actionable (WHAT/WHY/FIX), and splits voice teaching into REAL
microphone practice vs clearly-labelled TEXT practice.  No unrelated
features.  Suite: **1957 passed / 1 skipped / 0 failed** (baseline
1890/1/0 — +67 tests, zero regressions).  Every claim below was
re-measured on this tree in a headless Linux sandbox; physical
Windows items remain honestly labelled TEST REQUIRED (§16).

### P0 — the one encoding contract (§1/§2/§3)

- **Root cause found**: `config.py` wrote `config.toml` with plain
  `open(path, "w")` — the Windows process default (cp1252) — so the
  template's em dash (`—`, byte 30 of line 1) became `0x97`, and
  tomllib's strict UTF-8 read failed with
  `'utf-8' codec can't decode byte 0x97 in position 30: invalid start
  byte, using defaults`.  Measured byte-for-byte in this release.
- **`airmouse.fileio` (new)** — THE one explicit text-encoding
  contract: `read_text` (strict UTF-8, raises `TextDecodingError`
  with path/byte-position/fix), `write_text_atomic` (UTF-8 no BOM,
  tmp + `os.replace`), `read_json`/`write_json_atomic`, `probe`
  (honest diagnosis: utf8_ok / decoded_as / bad_byte / BOM) and
  `decode_best_effort` (repair-only recovery: UTF-8 → BOM → cp1252 →
  latin-1).  The Windows process default encoding is never used for
  any AirMouse-owned text file again.
- `Config.save_defaults()` now writes canonical UTF-8 atomically; the
  generated template's non-ASCII content (em dash, box-drawing) is
  now SAFE because the encoding contract guarantees the round trip.
- **Never silently fall back (§2)**: a broken config now records a
  structured problem (`Config.load_problem()`:
  kind=encoding|syntax|io, message, path, fix) and prints a LOUD
  WHAT/WHY/FIX block naming `airmouse setup --repair-config` — the
  old one-line `Config load error … using defaults` is gone.  The
  lenient path never modifies or destroys the user's file.
- **`airmouse setup --repair-config` (new)**: preserve → timestamped
  backup (`backups/config-backup-<ts>.toml`, byte-for-byte) →
  repair → validate (TOML parse) → write canonical UTF-8 → report
  exactly what happened.  Content-preserving for legacy-encoded
  files (measured: cp1252 with em dash → same text, now UTF-8);
  BOM stripped; invalid-TOML files restore defaults only AFTER the
  backup, and say so.
- **Config round-trip tests (§3)**: write → read → identical across
  ASCII, Unicode (Nigerian/European names), en dash, em dash, smart
  quotes, emoji; plus fresh / existing / malformed (0x97) / BOM /
  invalid-TOML / migration / repair / idempotent-repair matrix.
- **Split-brain fix**: `config.py` was the last import-time path
  resolver; `AIRMOUSE_HOME=… airmouse doctor` reported the storage
  home in one directory and the config in another (reproduced on
  this tree).  Config paths now resolve dynamically via
  `airmouse.paths` with the module-global override (tests,
  persistence backups) still honoured.

### Doctor — actionable, honest (§4/§15)

- Every WARNING/FAILED row now explains WHAT (the row) / WHY / FIX —
  `Fix:` lines replaced by explicit `WHY:` + `FIX:` lines.
- The CONFIG check now distinguishes: no file → READY ("defaults"),
  UTF-8+TOML valid → READY ("UTF-8 configuration valid"),
  decode/syntax failure → WARNING with the exact repair command and
  WHY (legacy Windows encoding / invalid TOML).  Doctor no longer
  hides anything behind "using defaults".
- §15 capability states: hardware rows carry explicit real-world
  labels — Webcam `[ACTIVE]`/`[UNAVAILABLE]`, Microphone
  `[MICROPHONE DETECTED]`, Transcription providers `[ACTIVE]` /
  `[NOT INSTALLED]`, and a new **Gaze subsystem** row
  (`CALIBRATED` / `REQUIRES CALIBRATION` with the exact calibration
  command / `UNAVAILABLE` when disabled).  Visible in doctor and in
  the machine report (`why`, `hardware_state` fields).

### Voice Academy — LEARN BY DOING (§5/§6/§7/§9/§10)

- **Progressive curriculum, 7 lessons (§9)**: L1 basic command → L2
  natural language → L3 context ("open my browser") → L4 dictation →
  L5 correction ("scratch that", "delete last word", "replace that
  with …" through the REAL VoiceTypingEngine) → L6 multimodal (voice
  intent + observed gaze fixation — both channels must actually be
  observed) → L7 personal voice learning (unchanged, local-only).
  Legacy level ids (`l3_dictation`, `l4_personal`) still resolve.
- **REAL VOICE PRACTICE (§5)**: when microphone + local ASR exist,
  every lesson runs the real pipeline (Microphone → SR listen →
  local ASR → transcript → command matcher → intent); a lesson
  completes ONLY when the real pipeline recognizes the expected
  intent.  Audio is adapted per engine (AudioData for PocketSphinx,
  WAV for Whisper/Vosk) and never stored.
- **TEXT PRACTICE stays, honestly labelled (§6)**: without mic/ASR
  the academy prints `Real microphone practice: PHYSICAL TEST
  REQUIRED`, `Text practice: AVAILABLE` and labels every prompt
  `TEXT PRACTICE` — it is never called microphone validation.
- **§7 provider detection**: every run starts with the honest VOICE
  HARDWARE panel (Microphone ✓/○, Whisper/Vosk/PocketSphinx ✓/○,
  Active provider, `Command grammar: ✓ available`, `Full speech
  recognition: NOT INSTALLED`) and offers the optional Local Voice
  Pack `[Y] Yes [N] Not now` — never auto-installs, never a cloud
  API; the offer never consumes scripted/non-interactive input.
- **§10 honest semantics**: a revealed answer prints
  `Answer revealed. Lesson not completed.`; a skip prints
  `Skipped — not completed.`; skips never reveal the answer and
  never count as passes; abandoned sessions print
  `Practice abandoned — nothing was marked complete.`
- **Teacher voice track**: no longer (wrongly) gated on a camera;
  completion banners distinguish real-pipeline verification from
  TEXT-practice verification; the text fallback uses the §10 wording.

### Live transcription (§8)

- `airmouse transcribe` now automatically uses MIC LIVE mode when a
  microphone and a real local ASR engine are present: speak one
  utterance per Enter → Microphone → VAD → local ASR → partial →
  final, displayed with confidence and timestamp; typed commands
  (pause/resume/save/clear/status/quit) still work.  Without mic/ASR
  it falls back to the text-REPL under its loud honesty banners.
  No audio is ever stored (measured: save writes text only).

### Noise + first-run (§13/§14)

- **§13**: `airmouse.quiet` silences third-party diagnostic noise
  (MediaPipe/Clearcut/TFLite/absl/speech stack) via env dials
  (respecting user-set values), their loggers only, at process start
  and at every academy/teacher/lab/transcribe entry — AirMouse's own
  errors are never filtered.
- **§14**: the welcome banner now lists Personalization; the
  zero-learning-curve READY panel shows per-modality states
  (Hands/Voice/Mic/ASR/Learning + Gaze `CALIBRATED` /
  `REQUIRES CALIBRATION` with the exact command).

### Honest scope note

Physical Windows validation of the new voice/mic paths (§16) cannot
be performed in the headless release sandbox and remains TEST
REQUIRED in `WINDOWS_REAL_WORLD_TEST.md` (drills 17a–17f added).
Simulated PASS never became hardware PASS anywhere.

## v16.5.0 — ADAPTIVE MULTIMODAL INTELLIGENCE (release candidate, 2026-09-05)

The teaching release. v16.0.0 made the gesture surface trustworthy;
v16.5.0 teaches it: a multi-track teacher with persisted, resumable
onboarding progress that can never auto-pass a physical skill, an
honest voice stack (deterministic grammar ≠ ASR, stated in plain
text), a live transcription session, capability answers from real
data (`help-me`), a deterministic temporal gesture observer that
PROPOSES but never executes, and a local-first personal interaction
profile with PREDICTION ≠ EXECUTION enforcement. Version bump
16.0.0 → **16.5.0**. Every claim below was re-measured on the
release-candidate tree in a headless Linux sandbox.

### Teacher + onboarding (new)

- `teacher.py` + CLI: `airmouse teach [all|voice|gaze|gesture|fusion|resume]`
  and `airmouse learn` (all academies). Headless measured: full plan
  (5 tracks / 11 lessons), every physical lesson marked
  `PHYSICAL PRACTICE REQUIRED — needs camera/microphone; never
  auto-passed`, "nothing was marked complete", exit 0; `teach nope` →
  `teach track must be: all | voice | gaze | gesture | fusion | resume`,
  exit 1. Teacher prints a self-diagnostic hardware panel and adapts
  practice deterministically (strong skill → skip, weak skill → gentle
  repeat, never blames).
- **First-run auto-teaching**: plain `airmouse` on a TTY offers the
  3–5 minute tour (phase NEW) or asks `Continue your training?`
  (phase IN_PROGRESS); decline ("Skip for now"), EOF and non-TTY all
  proceed normally — the prompt is TTY-gated so headless runs are
  never blocked (enforced by startup-probe tests driving real main()).
  Wired behind `config.teach_auto` (default true).
- **Onboarding persistence**: `OnboardingStore` at
  `<home>/profile/onboarding.json` (schema v1) with the honest ladder
  NEW → IN_PROGRESS → VOICE_COMPLETE → GAZE_COMPLETE →
  GESTURE_COMPLETE → FUSION_COMPLETE → COMPLETE; measured: one
  headless `teach` writes `"phase": "IN_PROGRESS"`, sessions count
  increments; a corrupted file measured live fail-safes to phase NEW
  with `corrupted_last_load: True`.

### Voice stack (new)

- `airmouse voice-status` now appends the honest per-machine Voice
  Provider panel: `✓ Built-in command recognition` (always real — the
  deterministic grammar ships), `○/✓ Local ASR available`, `○/✓
  Whisper available`, `○/✓ Vosk available`, `○/✓ Microphone detected`,
  exactly one `Active:` line, and when no ASR engine is installed:
  `Full speech recognition is not installed.` +
  `Command recognition still works — it is deterministic grammar, not
  ASR.` Measured headless: panel all `○` with `Active: Built-in
  command recognition`.
- **Pluggable local ASR providers** (`voice_stack.py` +
  `offline_voice.py`): Vosk / Whisper (or faster_whisper) /
  PocketSphinx adapters with guarded imports, auto-detected the moment
  the engine package is installed (preference vosk > whisper >
  pocketsphinx; a broken engine is skipped, never faked). Engines are
  OPTIONAL and NOT bundled; install guidance is text only — AirMouse
  never runs pip for you (NOT AVAILABLE by design).
- **`airmouse transcribe`** (`transcribe_session.py`): live local
  transcription REPL — `pause | resume | save [json] | clear | status
  | stop | quit`, any other line is a spoken utterance. Measured:
  segments carry `[HH:MM:SS] confidence% "text"` (e.g.
  `[12:24:40] 95% "Hello world, this is a test"`); `status` reports
  `history: n/500 segments` (bounded history); `save` writes only on
  request, only to `<home>/transcripts/transcript-<ts>.txt` (or
  `.json` with segments + confidence + provider + timestamps); text
  only, audio is never stored; EOF ends cleanly (rc 0). Without an
  installed ASR engine the session runs on a deterministic provider
  that is banner-labelled every session: `provider: simulated_stream`
  + `⚠ SIMULATED provider — install a local ASR engine for real
  transcription (see: airmouse voice-status)`.
- **Voice Academy** (inside `teach`/`learn`, 4 levels): l1 basic
  commands, l2 natural language via the REAL grammar matcher, l3
  dictation with spoken punctuation verified by the REAL
  `VoiceTypingEngine` (clean retries restore the step-start buffer),
  l4 personal voice learning via the REAL local-only `VoiceProfile`
  alias learning. Microphone practice remains PHYSICAL TEST REQUIRED.

### Gesture temporal intelligence (new, observer only)

- `temporal.py`: deterministic temporal recognizer — `TrajectoryBuffer`
  features (velocity / acceleration / direction / handedness),
  `PinchLifecycle` START/HOLD/MOVE/RELEASE with hysteresis, debounce,
  false-positive suppression and tracking grace, `TemporalRecognizer`
  sequences + compositions, and a robustness toolkit (`Hysteresis`,
  `Debouncer`, `FalsePositiveSuppressor`, `TrackingRecovery`,
  `CameraWatchdog`, `SensorHealthScore`).
- In the LIVE app it runs as an OBSERVER over the same frame stream:
  HUD badges `TMP:` (pinch lifecycle / proposal) and `SENSOR:` (health
  degraded/poor). **It PROPOSES ONLY — the gesture_spine remains the
  sole dispatcher**; there is no parallel execution path (enforced by
  source-scan tests), so temporal OS dispatch for new compositions is
  honestly NOT AVAILABLE. Two-hand rotate/drag remain detected-but-
  NOT-MAPPED (unchanged truth, now also stated by `teach` and
  `help-me`).
- Measured performance (`airmouse verify` Temporal check):
  recognize + features ≈ **293 µs/frame** (293.4 µs this run,
  direction right, lifecycle engaged).

### Gaze Academy + personal profile (new)

- **Gaze Academy** (5 lessons — l1_acquire, l2_fixation, l3_dwell,
  l4_blink, l5_eye_assist) with real metric functions; headless runs
  print the honest plan and mark nothing passed; simulated dry-runs
  are always labelled SIMULATED and never pass as real. **Gaze
  personalization** (`GazeLearner` at `<home>/profile/gaze.json`) is
  bounded and local: dwell clamped to [0.3, 2.0] s, EMA learning gated
  on VERIFIED observations only, suggestions are proposals that are
  never auto-applied.
- **Personal Interaction Profile** (`profile_store.py`):
  `<home>/profile/{interaction,voice,gestures,preferences}.json` —
  bounded, content-free (counters/parameters only), atomic writes,
  corruption fail-closed, reset with backup to `<home>/backups/`.
  **LearningLoop** enforces PREDICTION ≠ EXECUTION: bounded ring of
  stage events, proposals that never execute, a single explicit
  approve path, and `adapt()` converting only approved proposals into
  profile observations.
- **Privacy**: manifest grew 20 → **24** entries (added
  `academy_progress`, `onboarding_state`, `personal_profile`,
  `transcript_sessions` — all counted live) and every entry stays
  covered by the memory reset / delete / export lifecycle
  (`deletion_verifies()`); user-learning artifacts are backed up then
  deleted, transcripts are user-owned keeps. `airmouse privacy` now
  prints a PERSONALIZATION summary ending **"Nothing is uploaded."**
  (measured).

### Help + startup + verification

- **`airmouse help-me [question]`** (`help_registry.py`): answers
  from REAL capability data — a 22-row gesture map aligned with
  `gestures.py` and the spine's risk classes (including
  `[refused by default]` on the Alt+F4 row and the honest two-hand
  note), the shipped 30-phrase voice grammar with the
  "It is NOT full speech recognition" note, and gate-level debugging
  (measured: `"how do I scroll?"` → the 3 real scroll paths;
  `"why didn't that work?"` → e-stop → confidence floors 0.45/0.60 →
  destructive policy → 0.12 s rate limit → gesture mapping).
- **Zero-learning-curve READY panel**: after startup the app prints
  `AIRMouse READY  Hands: ✓/○ Voice: ✓/○ Gaze: ✓/○ Learning: ✓/paused`
  + the tip `say "help" anytime` + a `airmouse teach` reminder while
  training is incomplete. Display-only, gated by `config.ready_panel`
  (default true; both v16.5 keys land in config.toml `[v10]`).
- **`airmouse verify` grows to 12 automated checks** (added Teacher +
  Temporal) + the same 5 physical ACTION_REQUIRED items; measured
  12/12 PASS, exit 0. CLI surface: 34 subcommand choices (was 30; no
  new flags).
- Startup probe tests cover plain `airmouse` reaching the frame loop
  with the temporal observer attached; 11 new real-main() CLI wiring
  tests cover the four new commands.

### Verification (measured on the release-candidate tree)

- Full suite: **1889 passed / 2 skipped / 0 failed** (re-measured;
  the 2 skips are the honest headless skips: no Chrome binary, no
  tesseract). Baseline history: 1056 (v15.0.0) → 1236 (v15.1.0) →
  1556 (v15.2.0/v16.0.0) → **1889 (v16.5.0)**.
- `airmouse verify`: 12/12 automated PASS + 5 physical
  ACTION_REQUIRED (unchanged truth). `airmouse --version` →
  `AirMouse v16.5.0 — Adaptive Human-Computer Intelligence Edition`.
- Docs reset across 6 files with one status tag per capability
  (capability table now 94 rows: 64 REAL / 5 SIMULATED / 4 OPTIONAL /
  13 PHYSICAL TEST REQUIRED / 8 NOT AVAILABLE). Physical hardware —
  including the first-run tour with camera/mic, Voice Academy
  microphone practice, gaze lessons, live ASR engines, dictation via
  microphone, the temporal HUD under a real camera, two-hand
  geometry, and PyInstaller Windows bundles — was NOT TESTED in the
  sandbox; see `VERIFICATION_REPORT.md` +
  `WINDOWS_REAL_WORLD_TEST.md` (new steps 16a–16d).

---

## v16.0.0 — GESTURE-FIRST RELEASE (release candidate)

The gesture-first release. Where v15.2.0 built the one execution
spine and the new gesture surface, v16.0.0 ships the documentation and
release hygiene that make that surface trustworthy: every claim in the
docs is re-verified against the running code, every capability carries
exactly one status (REAL / SIMULATED / OPTIONAL / PHYSICAL TEST
REQUIRED / NOT AVAILABLE), and every stale or unwired claim found by
the audit (AUDIT_REPORT_v15.1.0.md §23) is removed. **No behaviour
change vs v15.2.0** — this release is docs + hygiene + packaging.

### Documentation reset (audit §23 findings closed)

- `docs/CLI_REFERENCE.md`: **regenerated completely from the live
  parser** (was stale at v11.5 AND corrupted — broken table fragments
  like "`[learning] emory]`"). Now documents all **30 subcommand
  choices**, all **56 visible flags + 1 hidden CI flag**, the
  `command_arg` semantics per command (`memory: status|export|reset|
  delete`; `academy: lesson id|all`; `gesture-lab: [seconds]`;
  `profile: <name>|list`; `doctor`/`test` arg aliases), measured exit
  codes, the v16 config keys, environment variables, and the strict-
  client note for `--aip-stdio`.
- `docs/CAPABILITY_MATRIX.md`: rebuilt as a **70-row capability table
  with exactly one status tag per capability** — measured tally:
  **47 REAL / 4 SIMULATED / 3 OPTIONAL / 10 PHYSICAL TEST REQUIRED /
  6 NOT AVAILABLE** — with live measured outputs (doctor 41
  components, verify 10/10, academy/lab/profile/aip-stdio runs) and
  the reproduce-with commands.
- `README.md`: first-run-menu description corrected to the real
  10-item menu (`cli_menu.py:41-52`: Setup, Doctor, Guided Test,
  Start AirMouse, Voice, Intelligence, Agent, Offline Test, Safety,
  Help — the old text wrongly listed verify/privacy/memory/exit);
  the "onboarding asks for one choice at startup" claim **removed**
  (`onboarding.py` has zero runtime imports and is documented as NOT
  AVAILABLE); gesture counts corrected from "14 gestures" to the real
  code-counted vocabulary (17 poses + 10 motion + 3 pinch events + 4
  two-hand classes); "fist = right-click" corrected to fist = freeze
  (peace = right-click); two-hand rotate/drag honestly labelled
  detected-but-not-wired; `selftune_apply` documented as a reserved
  opt-in flag with no shipped auto-apply path; new sections cover the
  execution spine, Academy/Lab/profiles, the AIP stdio wire server
  and the browser launcher.
- `WINDOWS_REAL_WORLD_TEST.md`: five new six-field steps (10a–10e):
  gesture profiles, Gesture Academy, Gesture Lab, two-hand check, and
  the agent E-stop drill over `airmouse --aip-stdio`; header/footer
  refreshed to the v16 numbers.
- `VERIFICATION_REPORT.md`: v16.0.0 five-part section prepended
  (AUTOMATED / SIMULATION / REAL WINDOWS / PHYSICAL HARDWARE / NOT
  TESTED); REAL WINDOWS and PHYSICAL HARDWARE remain NOT PERFORMED /
  NOT TESTED — unchanged truth.

### Hygiene

- Capability status vocabulary adopted project-wide: nothing is
  described as available unless it is wired; simulated things say
  SIMULATED; hardware things say PHYSICAL TEST REQUIRED; unwired
  things say NOT AVAILABLE.
- Stale-claim sweep verified: no doc claims fusion2 is wired (it was
  removed from shipped wiring in v15.2.0 and stays an optional,
  unadvertised library), no doc claims the onboarding interview runs,
  and the memory lifecycle now documents its REAL artifact coverage
  (intelligence/*, calibration, custom gestures, macros, lecture
  notes) + `deletion_verifies()`.

### Packaging

- Version strings: `__init__.py` / `pyproject.toml` are bumped by the
  standard release process (step 1 of README §Release process); at
  documentation time the tree still prints 15.1.1 — all v16 findings
  are independent of that string, and `airmouse verify` checks the
  match at build time.
- Known integration note documented for agent clients: the
  `airmouse --aip-stdio` CLI wrapper prints one banner line before
  the JSON-lines loop; strict first-line-JSON clients (agent-core
  `stdio://`, agent-sdk-js `StdioTransport`) should target
  `python -m airmouse.aip_stdio`, which emits nothing but reply
  lines. Both entries speak the identical AIP envelope.

### Verification (measured on the release-candidate tree)

- Full suite: **1556 passed / 2 skipped / 0 failed** (re-measured;
  the 2 skips are honest headless skips: no Chrome binary and no
  tesseract stack in the sandbox).
- `airmouse verify`: 10/10 automated PASS + 5 physical
  ACTION_REQUIRED (unchanged truth).
- `airmouse doctor`: READY 32 / OPTIONAL 7 / HARDWARE 2 / WARNING 0 /
  FAILED 0 → `[READY FOR TESTING]`.

---

## v15.2.0 — ONE EXECUTION SPINE + GESTURE-FIRST SURFACE + AGENT/BROWSER LAST MILE + ACADEMY

- gesture_spine.py GestureActionRouter: every gesture action now
  passes estop > confidence gate > risk class (SAFE/CAUTION/DESTRUCTIVE)
  > policy > rate-limit > dispatch; OK->Alt+F4 and macro replay
  REFUSED by default (audit #4/#20); cursor/scroll/zoom are
  estop-gated continuous axes
- all legacy dispatch (trackpad + classic + volume/brightness + swipes)
  migrated; [ESC] trips spine estop, [x] resets
- confidence now gates actions (config gesture_min_confidence_safe/
  caution; audit #8)
- new gestures live: thumbs_down/four/five poses, swipe up/down,
  circle cw/ccw, push/pull zoom, shake=cancel, wave=attention
  (audit #9); pinch_hold/pinch_release/double_pinch emitted by the
  state machine
- two-hand foundation live behind config.two_hand: max_hands=2 +
  handedness + TwoHandGestureRecognizer; zoom=ctrl+wheel; single-hand
  actions freeze while engaged (one owner)
- gesture registry finally fed by hands (sequence matcher live, SAFE
  intents through spine); personalization loop closed: observe_gesture
  on every confirmed gesture (audit #7)
- fusion2 removed from shipped wiring (Option B, audit #10)
- agent last mile: airmouse --aip-stdio wire server (simulated|real
  via --aip-real through permission-gated ActionEngine); ALLOW_ONCE
  single-use; agent budgets enforced (audit #11/#12)
- browser last mile: --launch-browser/--browser-port launch+connect;
  ws host pinned to loopback (audit #16); target resolver providers
  out of the box; Windows volume fixed; app_launch op
- unified home paths.py; privacy manifest + real reset/delete/export +
  deletion_verifies (audit #3/#6)
- Gesture Academy (airmouse academy, 11 lessons), gesture-lab
  observatory, 8 interaction profiles
- memory/privacy CLI surfaces artifacts + verification
- tests: +43 (startup commands incl --aip-stdio roundtrip,
  academy/lab/profiles); suite 1556 passed / 2 skipped (honest
  headless skips)

---

## v15.1.1 — P0 STARTUP REPAIR

Foundation repair release: fixes the three P0 defects found by the
full-spectrum audit (airmouse_pkg/docs/AUDIT_REPORT_v15.1.0.md). No
features removed; behavior otherwise unchanged.

- FIX (P0): `airmouse --voice` crashed at startup with UnboundLocalError
  (voice_engine10 read at __main__.py:1214 before its assignment). The
  v10 offline engine handle is now declared before the v5 fallback
  block, and when the v10 engine activates a running v5 cloud engine is
  stopped — exactly one voice owner. The live microphone path works
  again.
- FIX (P0): the v9 gesture-ownership gate was inert (overwritten by the
  state-machine update) and crashed under `--fusion --gaze`
  (read-before-assignment). Ownership is now applied AFTER the state
  machine: in hands-free/gaze/assist modes hand gestures no longer fire
  direct mouse actions while the agent owns interaction.
- FIX (P0, CLI honesty): `airmouse memory reset/delete` now surface
  per-store failures (cleared=False / errors) and exit 1 instead of
  printing unconditional success.
- FIX: two latent NameError crashes — `Sequence` was undefined in
  modes.py:589 and target_resolver.py:146 (would crash those paths on
  first execution).
- TEST: new tests/test_startup.py — drives the REAL main() through 8
  documented startup flag combinations with stubbed hardware (bare,
  --voice, --gaze, --fusion, --voice --fusion, --gaze --fusion,
  --hands-free, --voice-mode command), plus voice-initialization and
  voice-ownership handoff assertions. Startup wiring regressions can no
  longer ship green. Suite: 1323 passed / 0 failed.

## v15.1.0 — HARDENED RELEASE (of v15.0.0)

A hardening release: **no architecture changes** — everything from
v15.0.0 and earlier is preserved unchanged. v15.1.0 adds the release
quality surface the platform needed: a guided setup wizard, an honest
health doctor, a 12-test guided laboratory, an automated verification
command, a privacy report, a user-controlled memory lifecycle, a
first-run menu, user-grade error messages, and a crash-safe
persistence layer — all verified by 180 new tests including a
red-team suite and pinned performance budgets. Full suite:
**1056 → 1236 passed, 0 failed**.

### Added — release surface (new CLI commands, all fail-closed)

- `airmouse setup` — guided wizard with 11 fixed steps (environment,
  core packages, local storage, configuration, cameras, microphones,
  browsers, optional voice extras, keyboard/mouse access, smoke test,
  finish marker). Consent-gated: the only install command it ever
  runs is `python -m pip install <package>`, and **only after an
  interactive Y/N**; non-interactive runs never install. Ends with a
  plain-language "What remains to test (needs you + hardware)"
  section and writes the `<home>/.setup_complete` marker
  (`setup_wizard.py`).
- `airmouse doctor [--verbose|--json]` — 12-section, 41-component
  health report built on a full capability detection pass
  (`capabilities.py`): SYSTEM, PYTHON, AIRMOUSE, CAMERA, MICROPHONE,
  SPEECH, INPUT, BROWSER, INTELLIGENCE, AGENT, OFFLINE, SAFETY.
  Every non-READY component ships a plain "Fix:" remediation.
  Overall verdict `[READY FOR TESTING]` / `[PARTIAL — …]` /
  `[BLOCKED]` with **exit codes 0 / 1 / 2**. Missing hardware is
  reported HARDWARE, optional extras NOT_INSTALLED, headless input
  UNAVAILABLE — statuses never masquerade as failures.
- `airmouse test` (non-interactive lab) and `airmouse test --guided`
  (interactive 12-test laboratory: installation, camera, mouse, gaze,
  voice, dictation, intelligence, browser, agent, multi-agent,
  recovery, offline). Physical tests **can NEVER auto-pass** — they
  need a human at the desk and report ACTION_REQUIRED; simulation
  results are labelled `[SIMULATION]`. Exit code 0 unless a test
  FAILs (`guided_test.py`).
- `airmouse verify` — 10 real automated checks (core import, voice
  determinism, intelligence roundtrip, safety gates, 18-check offline
  selftest, simulated browser, permission deny-by-default, lease
  conflict refusal, AIP malformed-envelope rejection, packaging
  version match) + the 5-item PHYSICAL list, always ACTION_REQUIRED,
  ending with the `airmouse test --guided` pointer (`verify.py`).
- `airmouse privacy` — local-first report: telemetry state (OFF by
  default, verified against the code default), network posture,
  storage inventory, learned-data summary (content never included),
  model state, and the full control list; `--json` supported.
- `airmouse memory status|export|reset|delete` — user-controlled
  lifecycle over the five local stores (twin, vocabulary, skills,
  workflows, preferences): status inventory, `export --to <path>`
  (local file only), reset (backs up, then clears) and delete (files
  removed, backups kept). Reset/delete are consent-gated and
  fail-closed on non-TTY.
- **First-run menu** — plain `airmouse` on a fresh machine offers the
  10-option menu (setup / doctor / guided test / verify / privacy /
  memory / info / start / exit); TTY-only, suppressed once setup
  completes (`cli_menu.py`).

### Added — persistence + CLI quality

- `persistence.py` — crash-safe local persistence: atomic writes
  (temp file + fsync + `os.replace`), schema-versioned envelopes with
  SHA-256 checksums, corruption quarantine (corrupt store renamed to
  `<name>.json.corrupt-<epoch>`, newest 3 kept) and clean recovery to
  empty, ascending migrations, `AIRMOUSE_HOME` environment override
  for the storage home, and the memory lifecycle facades behind it.
- `user_errors.py` — user-grade error messages (title / reason /
  numbered fixes / hint, always ending with the `airmouse doctor`
  pointer), a guarded CLI wrapper (no tracebacks on screen by
  default; `--debug` prints a **redacted** traceback), and message
  sanitisation that strips GitHub/API/bearer-style tokens, env secret
  assignments and home-path prefixes.
- CLI performance budgets pinned in `tests/
  test_release_perf.py` (measured on the Linux sandbox, then pinned
  with headroom: `--version` ≈ 1.4-1.5 s measured / 6.0 s budget,
  `doctor` ≈ 2.6 s / 12.0 s, `verify` ≈ 1.6 s / 8.0 s, `test` ≈ 1.6 s
  / 8.0 s).
- Red-team suite: token-leak scans, exception-echo checks, consent
  gating (no subprocess without consent), path-traversal rejection in
  persistence/export, checksum tampering, fail-closed non-interactive
  confirmations.

### Fixed — release-quality defects found during hardening

- Stale packaging metadata: `pyproject.toml` description still said
  v10; the `--help` banner still said "v5.0.0" — both now derive from
  `__version__` (15.1.0). `airmouse verify` now checks that the
  pyproject version and the package version match.
- KeyboardInterrupt during CLI startup crashed with a traceback —
  now handled by the guarded CLI (exit 130).
- Doctor verdict wording: a report with WARNINGs but no FAILED items
  now correctly says "[PARTIAL — review WARNING items]".
- Config defect (v15 hardening pass): a legacy v9 performance-report
  flag could collide with the privacy telemetry setting — telemetry
  now stays authoritatively OFF (default in code, test-enforced).

### Tests

- 1056 (v15.0.0 baseline) → **1236 passed / 0 failed / 0 skipped**
  (+180): +33 CLI quality (`test_cli_quality.py`), +46
  setup/persistence/privacy (`test_setup_persistence.py`), +42
  doctor/capabilities/verify (`test_doctor.py`), +54 guided-test
  laboratory (`test_guided_test.py`), +5 release performance budgets
  (`test_release_perf.py`). Hardening/red-team assertions
  (token-leak scans, consent gating, fail-closed confirmations) are
  built into these suites. All 1056 pre-existing tests preserved
  green.

## v15.0.0 — UNIVERSAL HUMAN + AI INTERACTION PLATFORM (v12.0 → v15.0)

The defining evolution: the deterministic interaction core opens a
DOCUMENTED, PERMISSION-AWARE boundary for AI agents — AIP 1.0, Python +
JS SDKs, a standalone stdlib-only agent core, multi-agent
infrastructure under a global control hierarchy — plus goals, tasks,
skills, self-healing recovery, universal target resolution, DO IT WITH
ME, one-choice onboarding, transparent local licensing, a marketplace
foundation, a deterministic simulator, failure injection and
six-question explainability. Humans and agents share ONE interaction
layer; agents get no bypass pathway.

### Added — v12 Personal Interaction Twin (`intelligence/twin/`)
- `twin.py`: `PersonalInteractionTwin` — **14 fact categories**
  (preference, habit, gesture_pattern, gaze_behavior, voice_vocabulary,
  command_preference, application_preference, workflow_preference,
  confirmation_behavior, correction_behavior, timing_pattern,
  successful_action, failed_action, modality_preference); every fact
  carries **source / confidence / context / timestamp / frequency /
  success_rate / provenance** (bounded evidence list, ≤ 8 entries);
  lifecycle `learn` → `record_outcome` → `decay` (30-day half-life,
  forget below 0.05) → `forget`/`correct` → `export`/`import` →
  `reset` → `query`/`explain`; bounded 2,000 facts; secrets scrubbed
  fail-closed (GitHub-PAT/API-key/private-key/card-shaped values
  refused); **optional by contract** — the core never imports it at
  module scope; never raises through the public boundary

### Added — v12.5 temporal world model
- `world_model_temporal.py`: `TemporalWorldModel` — frozen
  `HumanState` / `ComputerState` / `TaskState` snapshot sections;
  `observe()` (partial merge + cause + cause_confidence),
  `snapshot()`/`previous()`/`history()`, `diff()` (dotted paths,
  from→to), `transitions()`, `mismatches()` + `expect()` (expected-vs-
  observed detection), `explain()`, `predict_state()` (**PREDICTION
  ONLY — never a permission**); bounded history **128**; snapshots out
  are immutable deep copies

### Added — v13 goals + tasks
- `goals.py`: `GoalHierarchyParser` — deterministic
  **COMMAND → INTENT → TASK → GOAL** classification with risk tables
  (none/low/medium/high/destructive), required permissions and
  required confirmations per objective; optional `interpreter`
  adapter for low-confidence results, output labelled
  `parsed_by="intelligence_adapter"`, bounded to the same levels, can
  NEVER downgrade risk or set `execution_allowed` (always `False`);
  **PREDICTION ≠ PERMISSION ≠ EXECUTION**
- `tasks.py`: `TaskEngine` — bounded (100 tasks × 64 steps × 8
  dependencies/step), 10 task states, structured `TaskStep`
  (objective/action/target/preconditions/expected/verification/risk/
  permission/timeout ≤ 600 s/bounded `RetryPolicy` ≤ 3 attempts),
  DAG `ready_steps`/`begin_step`/`complete_step`/
  `record_verification`/`retry_step`, **destructive steps require
  explicit human approval** (`PENDING_APPROVAL` until `approve()`),
  `checkpoint()`/`rollback()` restore task/step STATE only (never
  external side effects), bounded audit ring (200)

### Added — v13.5 skills
- `skills.py`: `InteractionCompression` — sequence clustering by
  **action-template signature** (action names + target KINDS;
  coordinates/values deliberately ignored); a skill proposal requires
  **≥ 3 repetitions + avg confidence ≥ 0.6 + user notification +
  preview + approval — NEVER silent**; `PersonalSkillLibrary` —
  100 skills × 24 steps, versioned `edit()`, enable/disable,
  `record_use`, validated export/import, revoke; targets are
  semantic/accessibility/DOM/OCR/visual, raw **coordinates only as an
  explicit flagged fallback** (`coordinate_fallback`, default False)

### Added — v14 recovery + target resolution
- `recovery2.py`: `RecoveryEngine` — full loop
  **PRECONDITION → EXECUTE → OBSERVE → VERIFY → RECOVER** on top of
  (never replacing) the v10 RecoveryManager; 7 strategies
  (retry / reobserve / retarget / alternate_modality /
  alternate_semantic_target / alternate_execution / request_human) +
  safe GIVE_UP; **14 failure diagnoses** with deterministic ladders;
  hard round cap (6) + bounded retries; **safety gate consulted before
  every execution**; no silent privilege escalation; destructive
  recovery needs a confirm hook (absent ⇒ no destructive recovery);
  every round appends an explainable trace entry
- `target_resolver.py`: `UniversalTargetResolver` — one resolver for
  humans AND agents; 7-provider chain **accessibility → DOM →
  semantic_app_api → OCR → vision → geometry → coordinate**;
  the coordinate link runs only behind the explicit
  `allow_coordinate_fallback` flag; `resolve_target()` /
  `explain_target()` / `verify_target()` with per-provider attempts

### Added — v14.5 AIP protocol + SDKs + agent core
- `aip.py`: **AirMouse Interaction Protocol (AIP) 1.0** — concepts
  DISCOVER / OBSERVE / TARGET / REQUEST / AUTHORIZE / EXECUTE /
  VERIFY / RESULT; **18 conversation message types + STATUS**; **12
  JSON schemas** (capability, observation, target, intent, action,
  task, permission, confirmation, verification, error, recovery,
  result) validated by a small strict validator — **unknown fields
  REJECTED, fail-closed**; version negotiation (same-major required);
  deterministic capability discovery; **256 KB** message caps;
  envelope `aip_version/type/id/agent_id/request_id/ts/payload`
- `agent_sdk.py`: `AirMouse` SDK (§10) —
  `connect/capabilities/observe/targets/execute/verify/task/stop/
  status` over an in-process `AipEndpoint` backed by the shared
  engines; **permission gate on every execute**; without a gate the
  endpoint fails closed to ASK ⇒ denied; `stop` revokes agent control
- `agent-core/` (standalone `airmouse-agent-core` 1.0.0, §11):
  stdlib-only, lazy, **never imports airmouse** (≈ 10 KB source,
  3 files), AIP over in-process handler / `.send()` transport /
  **stdio JSON-lines** (lazy subprocess); `AipError` for protocol
  errors; `airmouse-agent --version` smoke CLI
- `agent-sdk-js/` (`airmouse-agent` 1.0.0, §11): **dependency-free**
  JS/TS SDK with the identical primitive surface
  (`InProcessTransport` / `StdioTransport`, Promises, `AipError`)

### Added — v15 agents, permissions, experience, platform, testability
- `agents.py`: `AgentRegistry` (§12) — identity / priority (1..9) /
  capabilities / budgets / audit for up to 32 agents; **exclusive
  resource leases** (TTL 30 s default ≤ 300 s); deterministic conflict
  resolution — **the holder keeps the resource until release or
  expiry; priority NEVER steals a live lease** (challenger waits);
  `handoff` (release + reacquire + notify); agent-to-agent messages
  are **DATA only** (info/handoff/result/question — never executed,
  never parsed as instructions); human override (`suspend_agent` /
  `stop_agent`) and `emergency_stop_all()` (stops every agent,
  releases every lease, latches permission E-STOP)
- `permissions.py`: `AgentPermissionEngine` (§14/§15) — global
  hierarchy **E-STOP > HUMAN OVERRIDE > SAFETY POLICY > PERMISSION >
  AGENT > PREDICTION** (nothing may reorder it); 12 granular keys
  (`observe.screen` … `destructive.action`); decisions
  **ALLOW / DENY / ASK / ALLOW_ONCE / ALLOW_SESSION / ALLOW_PATTERN**;
  **ASK without a human == NO** and no-rule defaults to ASK (fail
  closed); deterministic most-specific-wins rule matching;
  `explain_decision()` §24 trace
- `ditm.py`: **DO IT WITH ME** (§16) — goal → structured proposal
  {OBJECTIVE, PLAN, SOURCES, CURRENT STATE, RISKS, REQUIRED ACTIONS,
  APPROVAL STATE} → user verbs **START / EDIT PLAN / PAUSE / STOP /
  CHANGE DIRECTION** → verified execution with progress reporting →
  corrections learned (Twin optional); destructive plans stay
  PENDING_APPROVAL inside the TaskEngine
- `onboarding.py`: **one-choice progressive onboarding** (§17:
  voice / hands / eyes / keyboard / automatic / all → minimal safe
  profile, usable immediately, preferences learned quietly) +
  **§18 accessibility posture** for 8 modes (voice-only, gesture-only,
  gaze-only, keyboard-only, switch-access, hybrid, hands-free,
  low-mobility) with configurable confirmation per mode and
  large-UI / high-contrast / reduced-motion as architectural flags
- `licensing.py`: **transparent local licensing** (§19) — tiers
  FREE / PRO / DEVELOPER / ENTERPRISE / SDK / MARKETPLACE / HARDWARE;
  **FREE tier = the complete local core** (21 `CORE_FEATURES`);
  higher tiers add, never subtract; revocable; inspectable state with
  `local_only: True`, `phones_home: False`, `dark_patterns: "none"`;
  no phone-home code exists
- `marketplace.py`: skill marketplace foundation (§20) —
  `SkillManifest` validation **fail-closed** (unknown fields rejected,
  semver versions, bounded permission lists, risk levels
  none/low/medium/high/destructive); install / enable / disable /
  update (strict semver supersession; risk escalation requires
  reinstall) / rollback / remove / inspect; **high/destructive risk
  requires explicit human trust** (`trusted_by_human`); **NO
  code-execution path — manifests are DATA** (only pre-vetted action
  names from the unified vocabulary)
- `simulator.py`: **deterministic virtual computer** (§26) — windows /
  tabs / buttons / forms / files / clipboard / navigation /
  UI changes / failure modes; same script → same final state; also the
  developers' "fake computer environment" (§25)
- `failure_injection.py`: **12 failure classes** (§27: missing_target,
  moved_button, closed_window, stale_dom, ocr_failure,
  accessibility_failure, network_failure, permission_denial, timeout,
  app_crash, agent_conflict, malformed_request) driven through the
  recovery loop — OBSERVE → DIAGNOSE → RECOVER → VERIFY or safe stop;
  permission / conflict / malformed classes never retry
- `explain.py`: **six question traces** (§24) — why-predicted,
  why-target, why-confirmation, why-failed, why-recovery,
  which-preference — structured, bounded and **sensitive-data-free**
  (categorical labels and lengths, never content), composable via
  `decision_trace()`
- `__main__.py`: **v15 subcommands** (§32)
  `status / capabilities / observe / world / twin / skills / agents /
  permissions / tasks / protocol / benchmark` (print + exit, all
  local); **v15 HUD badges** (§33) `AGENT:` (an AI agent is
  controlling the computer), `TASK:`, `CONFIRM?`, `RECOVER:`
- Tests: `test_v12.py` (39), `test_v13.py` (40), `test_v13_5.py` (19),
  `test_v14.py` (33), `test_v14_5.py` (34), `test_v15.py` (75),
  `test_hardening_v15.py` (30) — **270 new tests**, 786 preserved

### Changed
- `pyproject.toml` / `__init__.py` / CLI banner: version 11.5.0 →
  **15.0.0** ("Universal Human + AI Interaction Platform")
- `config.py`: v9 perf-report flag decoupled from the privacy telemetry
  flag (see Fixed); all v11.5 sections preserved backward-compatibly
- Docs: `docs/V15_ARCHITECTURE.md`, `docs/AIP_SPEC.md`,
  `docs/AGENT_SDK.md`, `docs/MULTI_AGENT.md`, `docs/SKILLS.md`,
  `docs/DEVELOPER_GUIDE.md`, `docs/USER_GUIDE.md` (new); README +
  VERIFICATION_REPORT updated (prior content preserved)

### Security
- **§23 executable audit** (`tests/test_hardening_v15.py`): no
  `shell=True` / `eval(` / `exec(` / `os.system` / `pickle.load` /
  `yaml.load` anywhere in the package; subprocess argv-list only;
  **zero network code in all 17 new v12→v15 modules** (urllib /
  requests / raw sockets / httpx asserted absent)
- **§30 adversarial fuzz**: 11 parser surfaces (AIP message parser +
  action schema, goal parser, SDK execute, marketplace manifests, twin
  import, skill import, task engine, permission engine, agent
  registry, target resolver, workflow importer) each driven with 23
  hostile strings — SQL injection, command substitution, path
  traversal, XSS, credential shapes, control chars, 5000-char blobs,
  wrong types — no crashes, no execution, no coercion (fail-closed)
- **AIP protocol hardening**: unknown envelope fields, unknown types,
  wrong major versions and unknown schema fields are REJECTED, never
  coerced; 256 KB caps enforced both directions; hostile strings in
  `params` are inert data (no field reaches execution); the endpoint
  fails closed to ASK==NO without a permission gate
- **Permission hierarchy holds under adversarial agents**: e-stop,
  human override, safety blocks, permission boundaries and destructive
  confirmations cannot be reordered or bypassed by any agent; denied
  leases/conflicts are audited, never preempted
- **Telemetry default defect found and fixed** (see Fixed); privacy
  lifecycle re-verified (twin inspect/forget/export/reset; skills
  revocable; everything local, no upload path)

### Fixed
- **telemetry_default defect**: the legacy v9 `[v9] telemetry_enabled`
  key (a LOCAL perf-report flag) collided with the §21 privacy
  telemetry flag and could silently enable the privacy-sensitive
  setting; `perf_report_enabled` now reads its own key first and
  `telemetry_enabled` stays authoritatively **False** (asserted by
  `test_telemetry_structurally_off`)
- v9→v11.5 regression: none — all 786 preserved tests stayed green at
  every milestone commit

### Verification
- **1056 tests passed / 0 failed / 0 skipped** (`pytest tests/ -q`,
  Python 3.12.14, ≈ 12 s) = 786 preserved + **270 new** across the
  v12→v15 suites; milestone tags **v12.0.0, v13.0.0, v13.5.0,
  v14.0.0, v14.5.0** pushed with green suites at each commit
  (825 → 865 → 884 → 917 → 951 → 1026 → 1056)
- Performance budgets enforced in `tests/test_hardening_v15.py`:
  twin learn < 10 ms, world observe < 10 ms, task create < 10 ms, AIP
  parse < 5 ms, goal parse < 50 ms, target resolve < 20 ms, SDK
  execute < 50 ms in-process, recovery round < 20 ms, agent-core
  import < 200 ms
- **SIMULATION-VERIFIED ONLY**: all v12→v15 subsystems verified in
  simulation (deterministic §26 simulator, in-process + stdio AIP);
  webcam / microphone / gaze / RF / real-Chrome-CDP / live-ASR remain
  **NOT PHYSICALLY VERIFIED** (unchanged since v11.5)

## v11.5.0 — ADAPTIVE HUMAN-COMPUTER INTELLIGENCE

The defining evolution: the deterministic v10 interaction core gains an
OPTIONAL personal-intelligence layer — a compact local model (few KB
fresh, ~30 MB hard budget) plus bounded memory, vocabulary,
self-tuning, workflow discovery, live transcription, voice typing,
universal text control, a world model, six interaction modes,
accessibility profiles, Fusion 2.0, and a privacy dashboard.
No LLM, no cloud, no network; PREDICTION ≠ EXECUTION everywhere.

### Added — Intelligence subpackage (`airmouse/intelligence/`, optional plugin)
- `model.py`: `PersonalInteractionModel` — `NGramModel` (order 3,
  backoff, capped counts) + `ActionMarkov` + `CommandModel`
  (frequency + time-of-day) + `EmojiModel` (context tags) +
  `FeatureWeights`; quantized 8-bit packed artifact with magic
  `AIMM` + format version; hard capacity budget
  (`MODEL_CAPACITY_BYTES_DEFAULT` ≈ 30 MB, growth refused beyond it,
  `capacity_hits` counter); deterministic pruning; `save/load/stats`
- `memory.py`: `InteractionMemory` + `PatternRecord` (pattern/
  frequency/confidence/last_seen/context/success_rate/correction_count/
  preferred_action); **fail-closed sensitive-data scrubbing**
  (`is_sensitive` / `scrub_pattern`) — passwords/tokens/credentials
  refused outright, URL creds and token-like blobs redacted to
  `[redacted-*]` placeholders; bounded 5,000 patterns; validated
  import/export
- `vocabulary.py`: `PersonalVocabulary` — terms (20k cap) +
  corrections (5k cap) with `apply_corrections` (e.g. "Hydra Link" →
  "HydraLink"), validated import/export
- `prediction.py`: `Predictor` → explainable `Prediction`
  (kind/value/confidence/reason/alternatives ≤ 8); deterministic
  `EMOJI_KEYWORDS` baseline map; `predict_next_action/command`,
  `complete_text/phrase_completions`, `suggest_emoji`,
  `predict_target`
- `selftune.py`: `SelfTuner` — `TUNABLES` (10 parameters) with hard
  min/max bands, steps, `min_samples` gates, EMA stats; `apply()`
  refuses out-of-band values
- `personalization.py`: `GestureProfile` / `GazeProfile` /
  `VoiceProfile` (frequent commands, false activations, **alias
  learning** e.g. "launch browser" → "open browser"); bounded samples
- `workflows.py`: `WorkflowDiscovery` (rolling window, 3–8-step
  patterns, ≥ 3 repetitions, **suggestions only — nothing auto-created**);
  `WorkflowStore` (≤ 200 workflows × 24 steps, step names validated
  `^[a-z0-9][a-z0-9_-]{0,63}$`); `WorkflowRunner` (preview-first,
  destructive workflows require prior preview, destructive steps
  require per-run confirmation, conditions checked);
  `ProactiveAssistant` + `Suggestion` (can never execute;
  destructive-looking suggestions suppressed; `prepare()` refuses
  URL/path/command-like resources)
- `plugin.py`: `IntelligencePlugin` facade — the ONLY object the core
  touches; **never raises**; `IntelligenceState` = available/disabled/
  unavailable/corrupted/incompatible/out_of_memory/privacy_paused/
  learning_paused; artifact persistence in `~/.airmouse/intelligence/`
  (atomic writes); `export_profile`/`import_profile` validated
  per-section and scrubbed; `delete_learned_data`/
  `reset_personalization`/`clear_history`; `pause_learning`/
  `set_privacy_mode`

### Added — Voice & text
- `transcription.py`: `LiveTranscriptionEngine` — streaming
  partials/finals pipeline (mic → preprocess → `EnergyVAD` → streaming
  ASR → partial → stabilization → punctuation → capitalization →
  personal vocabulary → final); `StreamingProviderAdapter` (wraps v10
  batch providers with a deterministic partial stream) +
  `SimulatedStreamingProvider` (always available); `apply_spoken_
  punctuation` (30+ phrase table), `insert_discourse_commas`
  (deterministic heuristic), `capitalize_text`, `spell_numbers`,
  `wer()` evaluator; bounded history (≤ 500 segments, ≤ 200k buffer) +
  txt/json/md export (≤ 8 MB) + search; pause/resume/stop; callbacks;
  metrics; honest provider availability in `status()`
- `dictation_text.py`: `VoiceTypingEngine` (COMMAND/DICTATION/HYBRID;
  spoken formatting; 16 edit commands incl. "delete last word",
  "scratch that", "replace X with Y", "capitalize that", undo/redo);
  `TextPredictor` (context-aware word/phrase completion);
  `EmojiSuggester` (30 s cooldown, ≤ 3 suggestions, learns
  preferences)
- `text_control.py`: `TextController` — 16 `TextOp`s (TYPE/SELECT/
  DELETE/REPLACE/COPY/PASTE/UNDO/REDO/CUT/MOVE/CAPITALIZE/LOWERCASE/
  UPPERCASE/FORMAT/NEW_LINE/NEW_PARAGRAPH), keyboard fallback,
  never coordinate-dependent; pluggable `TextExecutor`

### Added — Context, modes, fusion, privacy
- `world_model.py`: `WorldModel` — bounded snapshot (application/
  window/visible targets/gaze target/text field/recent action+command/
  mode + explainable likely intent; destructive intents never
  surfaced); `ContextualCommandResolver` — 12 deictic families
  (15 utterances: "click that"…"save that") with a deterministic
  confidence model; **low confidence ⇒ `requires_confirmation`
  (ASK, don't guess)**
- `modes.py`: `ModeController` + `MODE_REGISTRY` phrase tables for
  **teacher / student / office / meeting / research / developer**;
  `PresentationController` via GENERIC hotkeys (works with
  PowerPoint/Keynote/LibreOffice/browser slides); `TimelineSession`,
  `StudyTimer`, `NotesStore`, `SourceCapture` (verbatim, never
  fabricates), `MeetingMode` structured summary (transcript/timeline/
  important/action items/decisions/questions — **no speaker-ID
  claims**), `AccessibilityProfiles` (8 profiles + custom chains,
  fallback via v10 `SensorHealth.alive()`), `DeveloperMode`
- `fusion2.py`: `FusionEngine2` — 9 weighted signals (voice .30,
  gesture .25, gaze .25, personal history .10, keyboard/browser/
  application context/recent action/prediction .05 each);
  `ConflictResolver` (conflicts scale confidence ×0.5 and force
  confirmation; prediction never outranks observation);
  `FusedIntentCandidate.executable` (requires a non-prediction
  signal); `RFExtendedProvider` protocol (presence/motion/
  gesture_classification/direction/range/velocity) + `RFNoHardware`
  honest default
- `privacy.py`: `PrivacyDashboard` — flags (learning/memory/
  transcription_history/vocabulary_learning/workflow_learning ON;
  **telemetry OFF by default; cloud structurally impossible** —
  `__post_init__` forces it off and `set("cloud")` refuses);
  OFFLINE/ONLINE/PRIVACY connection states; `delete_learned_data`/
  `reset_model_personalization`/`clear_interaction_history`/
  `export_profile`/`import_profile`; bounded local audit log
- `selftest.py`: `run_self_test`/`format_self_test` — 15 components
  with PASS/FAIL/**OPTIONAL**/​**HARDWARE** statuses (RealLocalASR =
  OPTIONAL, Camera = HARDWARE — missing hardware is never a failure)

### Changed
- `agent.py`: guarded intelligence wiring (`intelligence`,
  `world_model`, `fusion2`, `modes`, `text_controller`,
  `voice_typing`, `transcription` overrides); `_learn_from_report` —
  verified actions become learning events (model + memory + workflow
  discovery), guarded, never raises
- `__main__.py`: flags `--intelligence/--no-intelligence/
  --dictation/--transcribe/--teacher/--student/--office/--meeting/
  --research`; subcommands `intelligence/memory/vocabulary/workflows/
  self-test` (v10 subcommands preserved); HUD badges `AI:` `MODE:`
  `SUG:` + transcript caption
- `config.py`: v11.5 TOML keys — `[intelligence] [learning] [memory]
  [transcription] [dictation] [prediction] [emoji] [teacher] [student]
  [office] [meeting] [research] [developer] [accessibility]
  [workflow] [privacy]` — fully backward compatible
- `offline.py`: `run_offline_selftest` extended **13 → 18 checks**
  (adds `intelligence_offline`, `memory_offline`, `vocabulary_offline`,
  `transcription_offline`, `fusion2_offline`) — the adaptive
  intelligence is proven to learn and predict under REAL socket-level
  network isolation
- version 10.0.0 → 11.5.0 (package, CLI banner "Adaptive
  Human-Computer Intelligence Edition")

### Security
- Re-audited for v11.5: **no `shell=True` / `eval` / `exec` /
  `os.system` anywhere** in the package (58 modules); all subprocess
  usage remains argv-only with timeouts
- New validation surfaces: workflow step names must match
  `^[a-z0-9][a-z0-9_-]{0,63}$` (shell fragments/SQL/traversal
  rejected); memory/vocabulary/profile imports validated + scrubbed
  per row (malformed or credential-shaped entries skipped, never
  trusted); model artifacts bounds-checked with magic+version
  (corrupt/incompatible ⇒ plugin states, never crashes); typed text
  capped at 4,000 chars; dictation buffer ≤ 100k chars
- PREDICTION ≠ EXECUTION enforced at 5 layers (data types → assistant
  → world model → `executable` policy → v10 safety gate); destructive
  suggestions suppressed, destructive workflow steps confirmed per run
- §43 malicious-input suite: SQL injection, command substitution,
  path traversal, binary junk, 10k-char strings, XSS, credential
  shapes driven through every v11.5 parser — no exceptions, no
  execution, no secret persistence
- Privacy scrubbing is fail-closed: `password=hunter2`-shaped input
  never reaches the learned store (asserted by test)

### Fixed (bugs found during development)
- n-gram packed stream u32/u64 alignment
- dictation segment bookkeeping on "replace"
- undo/redo stack ordering (no self-push)
- mode dispatch routing for shared phrases ("mark important" now
  routes to the ACTIVE mode's controller)
- `_learn_from_report` variable shadowing

### Verification
- **786 tests passed / 0 failed / 0 skipped** (`pytest tests/ -q`,
  Python 3.12, Linux sandbox) = 630 preserved v10 tests + **156** new
  v11.5 tests (`tests/test_v115.py`, 20 sections incl. §43 security,
  §44 resource limits, §34 budgets, §46 integration simulations)
- `airmouse offline-test`: **18/18** checks with networking disabled
  at socket level; `airmouse self-test`: 15 components — 13 pass,
  1 optional (RealLocalASR), 1 hardware (Camera), 0 fail
- Performance (this environment; budgets asserted in §34 tests):
  prediction ≈ 0.004 ms (< 50 ms), emoji suggestion ≈ 0.024 ms
  (< 50 ms), memory record ≈ 0.005 ms (< 10 ms), transcription tick
  ≈ 0.001 ms (< 100 ms), fusion ≈ 0.008 ms (< 20 ms), event-bus
  publish ≈ 0.002 ms, model load ≈ 3.4 ms (< 500 ms) — all ≥ 100×
  headroom
- **Honest hardware status:** no physical webcam/mic/RF in the build
  environment — everything is SIMULATION-VERIFIED on deterministic
  simulators. Real-hardware camera/mic/gaze/RF, real-Chrome CDP, and
  live PocketSphinx/Vosk/Whisper ASR are HARDWARE-UNVERIFIED; the
  simulated streaming provider and transcript-injection path are what
  is verified. No physical verification is claimed
- The personal model ships EMPTY (fresh install ≈ 0.1 KB; a
  200-sentence training run produces a 17 KB artifact) — no
  pre-trained weights, no neural LLM, honestly documented

## v10.0.0 — UNIVERSAL OFFLINE INTERACTION ENGINE

The defining evolution: every interaction modality (voice, hand, gaze,
RF, keyboard/mouse, screen, browser) unified over one local event bus,
with deterministic offline command understanding, universal actions,
true offline operation, and honest simulation-first verification.
No LLM, no cloud AI, no network required.

### Added — Event Bus (foundations)
- `eventbus.py`: `EventBus` / `Subscriber` / `MultiSubscriber` —
  in-process pub/sub over normalized `interfaces.Event` objects;
  bounded per-subscriber queues with drop-oldest (producers never
  block), history ring buffer, publish/poll stats; deterministic
  (`now`-injectable); works with networking disabled
- `interfaces.py` extended: `Event` + `EventKind` (14 kinds),
  `VoiceMode`, `CommandNamespace` (10), `ContextState`; `Modality`
  gains RF + BROWSER (8 modalities); `IntentType` grows to 52 members,
  `ActionType` to 32

### Added — Offline Voice + Command Grammar
- `voice_commands.py`: deterministic template grammar + registry —
  **75 commands across 10 namespaces** (engine/mouse/system/window/
  application/files/text/navigation/media/browser) with `<slot>`
  entities (direction/app/name/query/url/target/number/text/what),
  verb-synonym groups, literal-specificity + namespace-priority
  resolution, ambiguity flags, calibrated confidence (exact 1.00 /
  ambiguous 0.72 / fuzzy 0.62–0.85), sensitive-command flags for the
  safety layer. Pure function, fully offline
- `offline_voice.py`: complete local voice subsystem —
  `OfflineSpeechProvider` protocol with `SimulatedSpeechProvider`,
  `PocketSphinxProvider`, `VoskProvider`, `WhisperProvider` (guarded
  imports, `detect_providers()` auto-detection); `EnergyVAD` with
  hysteresis; `WakeWordGate`; `DictationBuffer` with commit markers;
  `voice_match_to_intent` (context-aware deictic resolution);
  `OfflineVoiceEngine` with COMMAND / DICTATION / HYBRID modes

### Added — Context, Gestures, RF
- `context.py` (v10 engine): focused app/window, browser state
  (active tab/URL), gaze target with 2 s TTL, selection, recent-action
  history, `snapshot()` independent copies, deictic resolution
  ("click that" / "close it" / "open this")
- `gesture_registry.py`: formal registry for every gesture — built-in
  v5 superset + `pinch_hold` / `pinch_release` / `double_pinch` /
  `grab` / `grab_move` / `circular_cw` / `circular_ccw` / directional
  motion; user-defined `CustomGestureMapping` sequence patterns with
  JSON persistence (`~/.airmouse/gestures.json`, `AIRMOUSE_GESTURES`
  env override); deterministic sequence matcher; double-pinch synthesis
- `rf.py`: optional RF-sensing modality — `RFProvider` protocol,
  `SimulatedRFProvider` (deterministic scripted), `DummyRFProvider`
  (documents degradation), `RFBridge` → event bus + gesture registry.
  Hardware is NEVER mandatory: with no provider the bridge idles and
  the system downgrades via the combo ladder

### Added — Universal Actions, Browser, Offline
- `system_actions.py`: 16 `SYSTEM_OPS` (volume/media/lock/sleep/
  shutdown/restart/brightness/bluetooth) + 8 `FILE_OPS`; **shell-free
  argv-only subprocess**; allowlisted base roots (traversal refused);
  `sanitize_file_name`; `validate_url` (http/https/file only); mock
  executors for deterministic tests
- `actions.py` (extended): canonical §10 action vocabulary (52 intents
  → 32 actions), v10 param normalization, destructive-op confirmation
  flags, executor injection (system/file/browser alongside
  pynput/mock)
- `browser.py` (§11–§13, 3 layers): `BrowserBridge` protocol →
  `SimulatedBrowserBridge` (deterministic, per-tab history) /
  `CDPBrowserBridge` (guarded, stdlib-only urllib + minimal RFC 6455
  websocket client); `BrowserTargetMapper` + `SemanticBrowserResolver`
  (fixed template grammar only — page text is DATA, never commands);
  `BrowserActionVerifier` (before/after state diff → passed/failed/
  unknown); `BrowserController` wiring bus + context + offline gate
- `browser_bridge.py`: `BrowserBridgeServer` — **localhost-only**
  (127.0.0.1:17843) HTTP endpoint for the shipped MV3 browser
  extension (`browser_extension/`: manifest + background.js 1 s
  active-tab poll + static content.js collector; password fields
  masked; payload ≤ 256 KB; never dynamic code)
- `offline.py`: `OfflineGate` (blocks cloud_asr/cloud_tts/browser_cdp/
  software_update/telemetry_upload when engaged);
  `network_isolation()` context manager — REAL socket-level blocking
  (monkeypatches `connect`/`connect_ex`/`create_connection`, loopback
  passthrough by default, strict `block_localhost=True` mode);
  `run_offline_selftest()` — 13-check end-to-end exercise of the full
  stack with networking truly disabled
- `hands_free.py` (extended): 8 named sensor combos (`voice_only`,
  `gaze_voice`, `voice_hand`, `gaze_hand`, `voice_gaze_hand`,
  `rf_voice`, `rf_gaze`, `full_fusion`), `SensorHealth` freshness
  tracker, `effective_combo()` — deterministic largest-alive-subset
  downgrade ladder with automatic recovery

### Changed
- `safety.py`: `SENSITIVE_TYPES` extended with SHUTDOWN / RESTART /
  LOCK / SLEEP / CLOSE_TAB; FILE_OP / SYSTEM_OP intents are sensitive
  only when their params are destructive (param-level refinement)
- `agent.py`: `inject_intent()` (externally resolved intents from
  offline voice / gesture registry / RF / browser pass the SAME safety
  gate), `poll_events()` (drains optional producers), context engine
  integration, v10 executor overrides
- `__main__.py`: v10 CLI — `--offline --browser --browser-bridge
  --gesture --rf`, `--voice-mode command|dictation|hybrid`, subcommands
  `voice-status / gestures / commands / browser / offline-test /
  diagnostics`; HUD badges `V10` / `CMD` / `RF` / `BROWSER` / `VER`
- `config.py`: new `[v10]` TOML section (13 keys, privacy-safe
  defaults)
- version 9.0.0 → 10.0.0 (package, CLI, banner)

### Security
- System/file executors: no `shell=True` anywhere — every subprocess is
  an argv list; file operations rooted in an explicit allowlist of base
  directories; traversal and outside-root paths refused; filenames
  sanitized; URL schemes restricted to http/https/file
- Browser: page content treated as untrusted data (never executed);
  CDP adapter evaluates only fixed snippets built from our parameters;
  bridge server hard-bound to 127.0.0.1; oversized (>256 KB) or invalid
  payloads rejected
- Offline gate can hard-block cloud ASR/TTS/CDP/updates/telemetry;
  `offline-test` proves the stack works with sockets actually refused
- Destructive ops (shutdown/restart/lock/sleep/close_tab/destructive
  file-system ops) require explicit confirmation; e-stop, rate limiter
  and confidence gates unchanged and intact

### Fixed (bugs found by the new v10 test suite)
- `offline.py` `network_isolation()`: the refusal handler was assigned
  to the unbound `socket.socket.connect`, so it received the socket
  instance as its first argument and treated it as the address — the
  documented loopback passthrough could never fire and every connect
  (including 127.0.0.1) was refused. Fixed by unpacking the real
  `(sock, addr)` arguments; loopback stays available by default as
  documented
- `context.py` `ContextEngine.snapshot()`: returned the live internal
  state object instead of an independent copy (despite its docstring),
  so mutating a "snapshot" mutated engine state. Now returns a proper
  dataclass copy

### Verification
- **630 tests passed / 0 failed / 0 skipped / 0 xfailed**
  (`pytest tests/ -q`, Python 3.12.14, Linux sandbox) = 497 preserved
  v9 tests + 19 new browser tests + 114 new v10 tests
- Performance (this environment, budgets enforced by tests): grammar
  50 utterances < 0.5 s, 30 voice transcripts < 0.5 s, 1000 bus events
  < 0.5 s, 1000 context resolves < 0.2 s — measured at ~5 ms / ~3 ms /
  ~2 ms / ~4 ms respectively; v9 perf numbers remain valid
- `airmouse offline-test`: 13/13 checks with networking disabled at
  socket level
- **Honest hardware status:** no physical webcam/mic/RF hardware in
  the build environment — everything is SIMULATION-VERIFIED via
  deterministic simulators. Real-hardware camera/eye/mic paths,
  CDP against a real Chrome, loading the MV3 extension, and live
  PocketSphinx/Vosk/Whisper ASR are HARDWARE-UNVERIFIED (code is
  guarded and auto-detecting). No physical verification is claimed

## v9.0.0 — MULTIMODAL INTELLIGENCE EDITION

The defining evolution: from gesture/voice mouse to a multimodal
computer-interaction platform combining eyes, hands, voice, screen
understanding, intent, automation, verification and recovery.

### Added — v6 GAZE
- `gaze.py`: FaceMeshTracker (mediapipe refined iris landmarks 468–477),
  GazeEstimator (iris-offset gaze, EAR eye state, head-pose gating,
  confidence), BlinkClassifier (blink / long blink / double blink / wink
  with confidence gating), DwellDetector (fixation hysteresis + one-shot
  dwell), GazeEngine orchestrator with FACE_FOUND/LOST events
- `gaze_filter.py`: GazeFilterPipeline — outlier rejection,
  confidence-weighted adaptive temporal smoothing (~2.5× jitter
  reduction, bounded lag), quantitative stats hooks
- `gaze_calibration.py`: 9/5/4/1-point workflow, two-pass 5σ-clipped MAD
  outlier gate, least-squares affine fit, px residual quality grading
  (good/fair/poor/incomplete), atomic JSON v2 persistence,
  bit-stable map() across save/load
- `--gaze-calibrate` guided flow + `--gaze-sim` deterministic simulation

### Added — v7 FUSION + SCREEN UNDERSTANDING
- `fusion.py`: MultimodalFusion — 6-modality arbitration with per-mode
  priority matrix (HAND/GAZE/VOICE/FUSION/HANDS_FREE/ASSIST), staleness
  expiry, conflict records, `hand:pinch` / `voice:click` confirmation
  patterns, rate-limited mode switching
- `screen_perception.py`: layered providers (accessibility → OCR
  *(opt-in, off by default)* → geometry) merged into a unified
  ScreenModel with dedupe, AppContext resolution hook, semantic target
  descriptions, guaranteed coordinate fallback

### Added — v8 INTENT / ACTION / VERIFICATION / SAFETY
- `intent.py`: IntentEngine — multimodal decisions + NL utterances +
  gesture queue → structured Intents; confidence decay; uncertain-gaze
  click suppression; sensitive-action marking; per-tick cap with
  carry-over; EMERGENCY_STOP always passes
- `actions.py`: ActionEngine (plan → preconditions → execute → report,
  bounded retries), PynputExecutor (lazy, headless-safe), MockExecutor
- `verification.py`: ActionVerifier (per-expected-type checkers with
  similarity scoring), RecoveryManager (retry → adjusted → notify,
  never auto-retries sensitive/blocked actions)
- `safety.py`: SafetySystem — e-stop latch, SAFE_MODE whitelist,
  confidence gates (incl. dedicated gaze gate), sliding-window rate
  limiter, click cooldowns, one-shot confirmation flow (monotonic
  expiry), camera/mic-loss auto-downgrade + restore
- `context.py`: AppContext detection (browser/editor/terminal/video/…)
  + ContextProfile action adaptations
- `macros.py` v2: semantic programs (LOOK_FOR/WAIT_UNTIL/CLICK/TYPE/
  SCROLL/HOTKEY/VERIFY/IF/RETRY/STOP) with max-steps guard, safety
  integration, legacy v1 replay fully preserved

### Added — v9 AGENT
- `nl_control.py`: local natural-language parsing ("click that",
  "scroll down a little", "close this window", …) with target_ref
  resolution contract and legacy fallback
- `hands_free.py`: HandsFreeController — gaze+voice+dwell/blink ticks
  through the full safety pipeline; blink-click OFF by default;
  long-blink e-stop
- `agent.py`: InteractionAgent — full pipeline orchestrator with
  Telemetry (fps/latency/counters) and shutdown reporting

### Changed
- `__main__.py`: v9 CLI (--gaze --no-gaze --gaze-calibrate --fusion
  --hands-free --assist --interaction --no-voice --version), agent
  wiring (confirmed hand gestures become fusion confirmations; fusion
  without gaze falls back to v5 gestures), v9 HUD badges, hotkeys
  [g]/[f]/[x] + ESC e-stop, telemetry shutdown report
- `config.py`: new `[v9]` section (19 keys, privacy-safe defaults)
- version 5.0.0 → 9.0.0 (package, CLI, banner)

### Preserved (v5 regression suite green)
14 gestures, swipes, DirectTracker + hybrid One Euro + Kalman, 30 voice
commands + turbo mode, pinch-to-zoom, adaptive calibration, v1 macros,
HUD, `airmouse_simple.py`, wheel packaging.

### Verification
- 497 tests passed / 0 failed (unit, integration, failure modes,
  regression, performance, E2E simulation)
- Performance (this environment): hand filter 0.040 ms/call; gaze
  filter+map 0.011 ms; full agent tick 0.032 ms; NL parse 0.0055 ms
- Hardware-unverified: physical webcam gaze behaviour (deterministic
  simulation + FaceMesh smoke only)

## v5.0.0 — VOICE + KALMAN EDITION
- Voice control: 30 commands, normal/high/turbo (MAD) sensitivity
- Hybrid One Euro + Kalman fusion cursor filter
- Pinch-to-zoom, adaptive calibration, macro recorder, single-file mode

## v4.2.0 — TRACKPAD EDITION
- Trackpad feel: tap=click, hold=drag, 2-finger scroll

## v4.1.0 / v4.0.0
- Pure-accuracy direct tracking; One Euro filter; precision mode
