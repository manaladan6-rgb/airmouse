# AirMouse v16.5.3 — Final Verification Report (WINDOWS NATIVE ASR BOOTSTRAP & ZERO-COMPILER VOICE RUNTIME)

---

# v16.5.3 Windows ASR Bootstrap Verification

Date: 2026-09-06 (build environment: Linux x86_64 headless sandbox,
CPython 3.12.14 AND CPython 3.13.5, vosk 0.3.45 + sounddevice 0.5.6 +
SpeechRecognition 3.17 + mediapipe 1.0.1 installed, pocketsphinx
installed on the 3.13 interpreter only — mirroring the real PyPI wheel
matrix — NO microphone hardware).  Scope: the zero-compiler Windows
voice bootstrap — `voice_bootstrap.ASRBootstrapManager`, the verified
static wheel matrix, `airmouse voice-setup`, `airmouse
voice-benchmark`, the vosk model resolution + honest MODEL MISSING
state, bootstrap-aware doctor/voice-status FIX lines, and the
`NATIVE_ASR_PACKAGE_UNAVAILABLE` packaging diagnosis.  All v16.5.2
architecture preserved.

**Headline honesty statement: the sandbox has NO Windows and NO
microphone.  What IS physically demonstrated here: (1) real local ASR
on real speech audio — vosk on CPython 3.12 transcribed generated
speech ("open browser" → exact match → OPEN intent; "hello bro this is
a real AirMouse voice test" → recognized, ~0.8 s) fully offline; (2)
PocketSphinx 5.1.1 really installing and initializing from its
official wheel on CPython 3.13.  The final physical Windows
microphone drill (voice-setup → voice-status → transcribe → speak)
remains ACTION_REQUIRED for the user
(WINDOWS_REAL_WORLD_TEST.md v16.5.3 steps).  No SIMULATED result was
relabelled hardware PASS.**

## Part 1 — THE PLATFORM FACT (measured from PyPI, §2)

- PyPI JSON API, pocketsphinx 5.1.1: wheels exist ONLY for cp311 and
  cp313 (win_amd64 / macosx / manylinux / musllinux) — **no cp312
  wheel on ANY platform**; the sdist requires a C/C++ toolchain.
  The user's exact failure (`CMake selects NMake → nmake missing →
  install fails`) is therefore a packaging gap, classified
  `NATIVE_ASR_PACKAGE_UNAVAILABLE` — NOT a microphone failure and NOT
  an AirMouse voice failure.  Regression-tested.
- PyPI JSON API, vosk 0.3.45: `py3-none-win_amd64` (13.7 MB) +
  manylinux/musllinux/arm wheels — version-independent platform
  wheels with the native Kaldi library BUNDLED.  Verified live:
  `pip install vosk` succeeds with zero compilation on CPython 3.12.

## Part 2 — AUTOMATED VERIFICATION (measured on this tree)

- **Full suite (3.12)**: `pytest tests/` → **2027 passed / 2 skipped /
  0 failed** (~59 s; baseline v16.5.2 1991/2/0 +1 env-shift → +36 new
  bootstrap tests, zero regressions).
- **Full suite (3.13)**: **2028 passed / 1 skipped / 0 failed** —
  AirMouse runs on Python 3.13 unchanged (mediapipe 1.0.1,
  opencv 5.0.0.93, numpy 2.2.6, pynput 1.8.2, sounddevice 0.5.6,
  vosk, pocketsphinx all import + initialize there).
- **The sandbox-reproduced divergence, killed**: with vosk installed
  but no model, the old registry said AVAILABLE while the engine said
  unavailable.  Now the probe raises `VoskModelMissing` → registry
  reports `INSTALLED` + `initialization: MODEL MISSING` + the FIX
  (`airmouse voice-setup`), `available()` is `[]`, `select()` returns
  None, and the engine's own `available()` agrees.  Regression-tested
  both ways (no model → not usable; model resolved → usable).
- **Never-silently-compile (§4)**: every install command carries
  `--only-binary :all:`; on a NO_COMPATIBLE_WHEEL engine the refusal
  happens BEFORE any subprocess (test asserts pip is never invoked);
  consent-declined installs nothing; offline attempts nothing; a
  failed verification triggers `rollback_if_failed` (pip uninstall +
  guarded model-dir cleanup).
- **Model security (§6)**: the ONLY download is the official
  alphacephei.com vosk-model-small-en-us-0.15.zip — 41,205,932 bytes,
  SHA256 `507575b9efd23f96ad2e9c2f226a8b9b50ca1cd894e2b00e7f07b170ffe6b00a`
  verified BEFORE extraction (mismatch → archive discarded, tested);
  Zip-Slip members blocked (tested); executable members (.exe/.dll/…)
  refused (tested); extraction lands only inside the AirMouse home;
  OFFLINE → `OFFLINE_MODEL_REQUIRED` with the manual URL (tested);
  no consent → `USER_DECLINED` (tested).  Red-team source scan: no
  eval/exec/os.system/shell=True/curl-pipe anywhere in the bootstrap.
- **voice-setup renders (§8)**: `--check-only` installs nothing;
  a no-path machine gets `VOICE SETUP COULD NOT COMPLETE` +
  `NATIVE_ASR_PACKAGE_UNAVAILABLE` + a recommended next step (exit 1);
  the READY path prints the full `VOICE READY / Provider / Mode /
  Network required: NO / Audio uploaded: NO` panel (exit 0).
- **voice-benchmark (§22)**: measured live on 3.12 — engine startup
  0.68 s, first result 0.37 s, average utterance 0.36 s (1 s scripted
  utterances), CPU 1.13 s, peak RAM 268 MB; WITHOUT speech samples it
  explicitly refuses to claim accuracy; with a real WAV it reports
  transcript + latency + words/minute and STILL labels the single
  sample as no accuracy claim.

## Part 3 — REAL-SPEECH END-TO-END (physically demonstrated here)

Generated speech (TTS → WAV → 16 kHz mono PCM) through the SHIPPED
pipeline (`registry.select()` → provider.transcribe), fully offline:

| Spoken | Local transcript (vosk, 3.12) | Latency |
|---|---|---|
| "open browser" | `open browser` (exact) → OPEN intent, `app: browser` | 0.37 s |
| "hello bro this is a real AirMouse voice test" | `hello bro this is a real air mouth voice test` | 0.83 s |
| "hello bro, how are you?" | `hello how are you` | 0.55 s |

`transcribe --text` through the same engine:
"hello bro comma how are you question mark" → `Hello bro, how are
you?` (dictation punctuation transform over a REAL local ASR engine).
On 3.13 the same speech through pocketsphinx's backend path also
transcribes (both engines verified on their respective interpreters).

## Part 4 — REMAINING ACTION_REQUIRED (honest)

1. The user's Windows 11 machine must run the 3-command drill
   (`airmouse voice-setup` → `airmouse voice-status --verbose` →
   `airmouse transcribe` + three spoken phrases).  Everything up to
   the microphone is verified; the microphone itself cannot be tested
   from this sandbox.
2. `--only-binary :all:` wheel identity on the user's machine is pip's
   own guarantee at install time (the static matrix is knowledge, pip
   is the authority).

---

# v16.5.2 Lightweight Local Voice Runtime Hotfix Verification

Date: 2026-09-05 (build environment: Linux x86_64 headless sandbox,
Python 3.12.14, pocketsphinx 5.x + SpeechRecognition 3.17 + sounddevice
0.5.6 installed, NO microphone hardware, NO PyAudio).  Scope: the
focused v16.5.2 hotfix — ONE authoritative ASR provider registry
(`asr_registry`) shared by doctor / transcribe / voice-status / the
academies, the anti-hasattr real-initialization probe for
PocketSphinx, deterministic lightweight-first selection
(explicit config → persisted working → pocketsphinx → vosk → whisper →
simulation only), ONE unified sounddevice-first microphone detector,
sounddevice-first VAD utterance capture (`mic_capture`), the honest
SIMULATED banner with per-engine WHY + FIX, `--text` / `--mic` /
`--provider` flags, `confidence: unavailable` instead of fabricated
percentages, the §8 `voice-status [--verbose]` view, and the
`doctor.active_asr_provider == transcribe.selected_provider`
invariant as a shipped regression test.  No unrelated features; no
architecture rewrites; all historical systems preserved.

**Headline honesty statement: real Windows and physical microphones
were NOT tested in this sandbox — the provider-divergence defect is
reproduced in a deterministic regression test instead, and the real
microphone drill is handed to the user
(WINDOWS_REAL_WORLD_TEST.md Step 16i).  Every SIMULATED PASS below is
labelled as such; none became hardware PASS.**

## Part 1 — AUTOMATED VERIFICATION (measured on this tree)

- **Full suite**: `pytest tests/` → **1992 passed / 2 skipped /
  0 failed** in ~34 s (baseline v16.5.1: 1957/1/0 → +35 tests, zero
  regressions).  The suite count rose honestly: 35 new registry /
  capture / invariant / safety tests.
- **The exact divergence, root-caused (measured)**:
  - doctor (old path) → `offline_voice.detect_providers()` →
    `PocketSphinxProvider().available()` → `hasattr(r,
    "recognize_sphinx")` → True whenever SpeechRecognition exists —
    pocketsphinx itself never imported → false ACTIVE.
  - transcribe (old path) → `voice_stack.pick_provider()` → strict
    `find_spec("pocketsphinx")` gate → None → `simulated_stream`.
  - Now BOTH call `asr_registry.registry`; `PocketSphinxProvider.
    available()` itself runs the real probe (an actual
    `pocketsphinx.Decoder(Config())` build, measured 0.35–0.42 s,
    cached).  A broken engine cannot pass doctor's report any more
    than transcribe's selection.
- **The release invariant, regression-tested (§16)**:
  `test_doctor_active_provider_equals_transcribe_selection` (module
  level) and `test_cli_transcribe_provider_matches_registry` (CLI
  level) both assert `doctor.active_asr_provider ==
  transcribe.selected_provider` in the SAME environment — and that
  when nothing is usable, both honestly agree on simulation with the
  WHY/FIX block present.
- **Selection matrix (all measured via self-consistent fake
  machines)**: explicit `prefer` wins; env `AIRMOUSE_ASR_PROVIDER`
  honoured; `"none"/"off"` → DISABLED (never overridden);
  pocketsphinx beats vosk/whisper (lightweight-first); probe-failure
  → ctor-failure → next candidate; all-fail → None (simulation only);
  unknown engine name → honest fallback; explicit-but-broken →
  falls through, never faked.  Discovery matrix: missing / init-
  failed (reason captured) / vosk / whisper (DEFERRED init — models
  never auto-downloaded) / multiple / none.
- **Microphone matrix (§13/§16)**: device found (backend, count,
  names, default index), device absent, PortAudio query failure
  (honest error string), invalid index (MicCaptureError with
  `voice-status` FIX), name-substring resolution, `default` → system
  default.  The record loop is deterministic under a scripted stream:
  speech-then-silence captured; pure silence →
  `no speech detected (silence/timeout)` with a FIX line.
- **Honesty regressions locked**: simulation can never print
  `MIC LIVE` (v16.5.1 latent bug the suite caught — `simulate` now
  forces text mode); a `--offline` run says "requested via --offline"
  instead of claiming the machine lacks ASR; PocketSphinx segments
  carry `confidence_known=False` and render `conf: unavailable`
  (the old fabricated 0.6 is gone); typed input can never be called
  microphone-validated.
- **`airmouse verify`**: 12/12 automated PASS (+ honest
  ACTION_REQUIRED physical rows) on this tree.  **`airmouse
  offline-test`**: 18/18, network actually blocked.

## Part 2 — SIMULATION VERIFICATION

- `voice-status --verbose` rendered on this (engine-installed,
  mic-less) sandbox: MICROPHONE `○ not detected` + FIX; ASR PROVIDERS
  PocketSphinx `initialization: PASS / runtime: READY / role: ACTIVE`
  with probe time 0.371 s, vosk/whisper `installed: NO / role:
  OPTIONAL`; TRANSCRIPTION `real microphone: NOT AVAILABLE` /
  `provider: pocketsphinx` / `mode: TEXT INPUT` / `offline: YES` /
  `audio storage: NONE` / `network: NOT REQUIRED`.  Exactly the
  mission §8 shape.
- `run_transcribe` end-to-end with feed: banner
  `provider: pocketsphinx (local ASR engine)`, text-input mode
  honestly labelled PHYSICAL TEST REQUIRED, utterance
  `hello bro this is a real airmouse voice test` →
  `Hello bro, this is a real airmouse voice test` final with
  timestamp; command `open browser` → `Open browser`; dictation
  `hello bro comma how are you question mark` → `Hello bro, how are
  you?` (§11 both modes verified).
- Scripted full pipeline (§5): mic frames → EnergyVAD → provider →
  transcript; vosk-style provider temp WAV deleted in the same call —
  `test_voice_capture_never_stores_audio` proves no file remains.

## Part 3 — REAL WINDOWS VERIFICATION

NOT PERFORMED in this sandbox (honest).  The user's Windows 10
(10.0.26100) + Python 3.12.10 machine — the exact profile from
v16.5.1 (sounddevice 0.5.5, PyAudio 0.2.14, 6 microphones,
pocketsphinx available, MediaPipe 0.10.35) — must run
WINDOWS_REAL_WORLD_TEST.md **Step 16i**:

1. `airmouse voice-status --verbose` → PocketSphinx
   `initialization: PASS`, `role: ACTIVE`, `real microphone:
   AVAILABLE`.
2. `airmouse transcribe` → `provider: pocketsphinx (local ASR
   engine)`, `mode: MIC LIVE`; speak
   `hello bro this is a real AirMouse voice test`, then
   `open browser`, then
   `hello bro comma how are you question mark` → real finals.
3. `airmouse doctor --verbose` → the SAME engine named ACTIVE.

Success condition: the engine named by doctor == voice-status ==
transcribe; spoken audio reaches the local ASR; no
`simulated_stream` while PocketSphinx is ACTIVE.

## Part 4 — PHYSICAL HARDWARE VERIFICATION

NOT PERFORMED here (no mic/camera hardware in the sandbox).  All
physical claims remain honestly labelled: microphone capture, real
PocketSphinx accuracy, vosk/whisper model loading, device-busy and
permission-failure behaviour on Windows.  `mic_capture` raises
`MicCaptureError` with actionable `.fix` lines for busy / invalid /
permission / no-speech paths — those paths are unit-tested with
injected failures, but the sound of a real room is the final judge
(Step 16i).

## Part 5 — NOT AVAILABLE / limitations (honest, non-negotiable)

- Vosk/whisper remain OPTIONAL: `AVAILABLE` on verified import with
  `initialization: DEFERRED` — their models load on first use BY THE
  ENGINE and are never auto-downloaded by AirMouse.
- PocketSphinx supplies no confidence — output says
  `conf: unavailable` rather than inventing one (mission §12).
- The persisted working-provider state (`<home>/asr_provider.json`)
  is best-effort; its absence or corruption never disables anything.
- No cloud APIs, no audio storage, no telemetry, no network for
  local ASR — unchanged and re-asserted by `offline-test` 18/18.

---

# AirMouse v16.5.1 — Final Verification Report (WINDOWS REALITY & TEACHING UX HOTFIX)

---

# v16.5.1 Windows Reality & Teaching UX Hotfix Verification

Date: 2026-09-05 (build environment: Linux x86_64 headless sandbox,
Python 3.12.14). Scope: the v16.5.1 hotfix — the one UTF-8 encoding
contract (`airmouse.fileio`), the P0 Windows config-encoding fix and
`airmouse setup --repair-config`, never-silent broken-config handling,
the actionable doctor (WHAT/WHY/FIX + capability states), the Voice
Academy rework (7 progressive lessons, REAL-microphone vs TEXT
practice, provider detection, honest skip/reveal semantics), MIC LIVE
transcription, and third-party log-noise suppression. No unrelated
features. Companion: `WINDOWS_REAL_WORLD_TEST.md` Steps 16e–16h.
**Headline honesty statement: real Windows and physical hardware were
NOT tested in this sandbox — the Windows encoding defect was
reproduced byte-for-byte in automated tests instead, and the physical
validation procedure is handed to the user
(WINDOWS_REAL_WORLD_TEST.md). Simulated PASS never became hardware
PASS.**

## Part 1 — AUTOMATED VERIFICATION (measured on this tree)

- **Full suite**: `pytest tests/` → **1957 passed / 1 skipped /
  0 failed** in ~23 s (baseline v16.5.0: 1890/1/0 → +67 tests, zero
  regressions; the 1 skip is the honest headless skip).
- **The exact Windows defect, reproduced and fixed (measured)**:
  writing the config template with cp1252 byte `0x97` at the em-dash
  position and loading it reproduces the user's error
  (`'utf-8' codec can't decode byte 0x97 … invalid start byte`);
  the lenient load now surfaces a structured problem
  (`kind=encoding`) plus a LOUD WHAT/WHY/FIX block naming
  `airmouse setup --repair-config`; `repair_config_file` recovers the
  text via cp1252, backs the original up byte-for-byte, validates the
  TOML, rewrites canonical UTF-8, and the post-repair load is clean
  (all measured in `tests/test_fileio_encoding.py`).
- **Config round-trip matrix (§3, measured)**: ASCII, Unicode names
  (Nigerian/European), en dash, em dash, smart quotes, emoji —
  write → read → identical through `fileio` and through the real
  config file; UTF-8 without BOM; no `.tmp` residue; missing file →
  honest probe; BOM stripped by repair; invalid TOML → defaults only
  after backup, reported; repair idempotent.
- **Doctor (§4/§15, measured)**: WARNING/FAILED rows render
  `WHY:` + `FIX:`; the CONFIG component reports READY (no file /
  valid UTF-8) or WARNING with the exact repair command and a WHY;
  hardware rows carry `[ACTIVE]` / `[UNAVAILABLE]` /
  `[MICROPHONE DETECTED]` / `[NOT INSTALLED]` labels; the new Gaze
  subsystem row reports `CALIBRATED` / `REQUIRES CALIBRATION` /
  `UNAVAILABLE`; `to_machine()` rows include `why` +
  `hardware_state`.
- **Voice Academy (§5/§6/§7/§9/§10, measured)**: 7 lessons
  (`l1_basic … l7_personal`, legacy ids still resolve); text mode
  prints `Real microphone practice: PHYSICAL TEST REQUIRED` +
  `Text practice: AVAILABLE` + `TEXT PRACTICE` labels; real mode
  (injected ear) drives the REAL matchers — a fabricated transcript
  that does not resolve never passes; skips print
  `Skipped — not completed.` and never reveal; reveals print
  `Answer revealed. Lesson not completed.`; abandoned input prints
  `Practice abandoned — nothing was marked complete.`; the hardware
  panel prints microphone/engine/active-provider truth and
  `Full speech recognition: NOT INSTALLED` when no engine exists;
  the Local Voice Pack offer never auto-installs and never consumes
  scripted input; L6 completes only when voice intent AND an observed
  gaze fixation agree; L5 correction runs the REAL VoiceTypingEngine
  edit ops (scratch that / delete last word / undo / replace that
  with …).
- **MIC LIVE transcription (§8, measured)**: with a (forced) mic
  capture the session prints `mode: MIC LIVE`, final lines with
  timestamp + confidence, honest `nothing usable captured` on failed
  capture, commands intact, summary `+ mic live`; the save path
  writes text only — no audio file ever appears.
- **Noise suppression (§13, measured)**: `quiet_third_party_logs()`
  sets `GLOG_minloglevel=3` / `TF_CPP_MIN_LOG_LEVEL=3` /
  `ABSL_MIN_LOG_LEVEL=3` (respecting user-set values), dials only
  third-party loggers (the `airmouse` logger untouched), is
  idempotent, never raises; wired at process start and at the
  academy/teacher/lab/transcribe entry points.
- **Release battery (measured on this tree)**: `airmouse verify` →
  **12/12 automated PASS** (import 16.5.1, voice determinism,
  intelligence roundtrip, safety gates, offline 18/18, simulated
  browser, permissions deny-by-default, leases, AIP fail-closed,
  packaging match, Teacher, Temporal ≈291 µs/frame) + 5 honest
  ACTION_REQUIRED physical rows; `airmouse doctor` exit 0;
  `airmouse --version` → `AirMouse v16.5.1`.
- **Security**: changed modules py_compile clean; grep audit for
  `eval(`/`exec(`/`os.system`/`shell=True`/`pickle`/`__import__` on
  changed files → no hits; the repair flow backs up before any
  rewrite and never executes file content; the pack offer never
  installs anything; the red-team suite (76 adversarial tests) is
  green inside the full run.

## Part 2 — SIMULATION VERIFICATION

- Simulated/mock paths in the new tests are used ONLY to inject ears
  (`speak_fn`, `capture_fn`) and gaze observers — the graded
  verification chain (command grammar → NL table → phrase table →
  VoiceTypingEngine → VoiceProfile) is the real shipped code in every
  pass assertion. No simulated result is reported anywhere as a
  physical one; every simulated provider is labelled SIMULATED at its
  point of use.

## Part 3 — REAL WINDOWS VERIFICATION

**NOT PERFORMED — TEST REQUIRED (honest).** The v16.5.1-specific
Windows sequence is `WINDOWS_REAL_WORLD_TEST.md` Steps 16e (config
repair drill — recreates the legacy ANSI encoding via Notepad,
doctor must show WHY/FIX, repair must preserve content),
16f (Voice Academy panel + REAL/TEXT modes with a real mic),
16g (MIC LIVE transcription + no-audio-storage check),
16h (doctor capability states). The historical Steps 1–17 remain
unchanged. **Simulated PASS never became hardware PASS.**

## Part 4 — PHYSICAL HARDWARE VERIFICATION

**NOT PERFORMED — TEST REQUIRED.** Same scope as v16.5.0 plus the new
microphone paths: mic capture in the Voice Academy (AudioData →
PocketSphinx; WAV → Whisper/Vosk) and the MIC LIVE transcribe loop
were verified only with injected ears in automation; on a real
machine they must be exercised via Steps 16f/16g. Gaze calibration
and the multimodal L6 lesson remain physical-only.

## Part 5 — NOT AVAILABLE / limitations (honest, non-negotiable)

- No Windows OS in the sandbox → Steps 16e–16h and the Windows
  bundle smoke test remain with the user.
- No microphone/camera/display in the sandbox → pyaudio builds
  nothing here; real-voice capture is code-reviewed + injected-ear
  tested, physically verified only via the handed-over drills.
- Live ASR engines (faster-whisper / vosk / pocketsphinx) were not
  installable in this offline build sandbox; provider detection is
  covered by injected detection snapshots.
- The C++ Clearcut stderr line can pre-date Python log filters; the
  env dials are the fix that reaches it, and Step 16's noise
  expectations were adjusted honestly rather than pretending a
  guaranteed stderr intercept.

---

# v16.5.0 Adaptive Multimodal Intelligence Verification

Date: 2026-09-05 (build environment: Linux x86_64 headless sandbox,
Python 3.12.14). Scope: the v16.5.0 "Adaptive Multimodal Intelligence"
release candidate — the teacher/onboarding system, the honest voice
stack + live transcription, the temporal gesture observer, the gaze
academy + personal interaction profile, `help-me`, the READY panel,
and the privacy-manifest growth 20 → 24. The v16.0.0 gesture surface
and execution spine are preserved unchanged. Companion:
`WINDOWS_REAL_WORLD_TEST.md` (repo root), `docs/CAPABILITY_MATRIX.md`,
`README.md` §Verification status. **Headline honesty statement:
physical hardware (webcam, microphone, real hand tracking single- and
two-handed, real gaze, real browser automation, real OS input
automation, the first-run tour and academy lessons with real sensors,
live ASR engines, PyInstaller Windows bundles) was NOT tested in this
sandbox — it cannot be; the validation procedure is handed to the user
via `WINDOWS_REAL_WORLD_TEST.md` and `airmouse test --guided`.**

## Part 1 — AUTOMATED VERIFICATION

- **Full suite: 1889 passed / 2 skipped / 0 failed** (re-measured on
  the v16.5.0 release-candidate tree, 23.4 s, pytest). The 2 skips
  are honest headless skips: no Chrome binary, no tesseract stack in
  the sandbox. Baseline history: 1056 (v15.0.0) → 1236 (v15.1.0) →
  1556 (v15.2.0/v16.0.0) → **1889 (v16.5.0)**; the v16.5 wave added
  the teacher, voice-academy/stack, transcribe, temporal, gaze-academy
  (68 tests), profile-store (63 tests), help-me, CLI-wiring (11 new
  real-main() tests) and startup-probe suites — including probes that
  drive plain `airmouse` to the frame loop with the temporal observer
  attached, headless, never blocked by the teaching prompt.
- **`airmouse verify`** (exit 0): **12/12 automated checks PASS** —
  Core import (v16.5.0); voice grammar + offline engine determinism;
  intelligence roundtrip; safety gates + e-stop latch + hierarchy (4
  safety levels); offline selftest 18/18; simulated browser bridge;
  agent permission deny-by-default; exclusive lease + challenger
  refusal; AIP malformed-envelope rejection; pyproject/package version
  match (16.5.0); **Teacher** — "onboarding ladder + honest grading +
  help + profile store + learning-loop approval gate + transcribe
  session OK"; **Temporal** — "temporal recognize+features 293.4
  us/frame (direction right, lifecycle engaged)". The 5 PHYSICAL rows
  report ACTION_REQUIRED by design (Part 4).
- **`airmouse --version`** → `AirMouse v16.5.0 — Adaptive
  Human-Computer Intelligence Edition` (exit 0); help banner matches.
- **Teacher + onboarding, measured headless runs (all on this tree,
  isolated `AIRMOUSE_HOME`):**
  - `airmouse teach` → the 5-track / 11-lesson plan (Voice ×3, Eyes
    ×2, Hands ×2, Multimodal ×2, Personalization ×2) with every
    physical lesson marked `PHYSICAL PRACTICE REQUIRED — needs
    camera/microphone; never auto-passed`, the closing lines
    `Headless run: nothing was marked complete — physical lessons are
    NEVER auto-passed.` + the learning-progress panel
    (`Sessions: 1 • Phase: IN_PROGRESS` + `Resume anytime: airmouse
    teach ("Skip for now" always works)`), exit 0.
  - `teach voice` → Voice track only (3 lessons), exit 0; `teach
    gesture` → Hands track only, exit 0; `teach resume` → exit 0;
    `teach nope` → `teach track must be: all | voice | gaze |
    gesture | fusion | resume`, **exit 1**.
  - `airmouse learn` → the same full multi-academy plan, exit 0.
  - Persistence measured: `<home>/profile/onboarding.json` written
    (schema_version 1, `phase: IN_PROGRESS`, per-track dict, session
    count); ladder order NEW → IN_PROGRESS → VOICE_COMPLETE →
    GAZE_COMPLETE → GESTURE_COMPLETE → FUSION_COMPLETE → COMPLETE
    confirmed from `teacher.PHASE_ORDER`. A deliberately corrupted
    `onboarding.json` (garbage bytes) measured live → phase NEW +
    `corrupted_last_load: True` (fail-safe, never raises).
- **Voice stack, measured:**
  - `airmouse voice-status` (exit 0): legacy provider dict
    `{simulated: True, pocketsphinx: True, vosk: False, whisper:
    False}` (the SpeechRecognition sphinx backend wrapper is
    importable) followed by the v16.5 honest engine-level panel:
    `✓ Built-in command recognition`, `○ Local ASR available`,
    `○ Whisper available`, `○ Vosk available`, `○ Microphone
    detected`, `Active: Built-in command recognition`,
    `Full speech recognition is not installed.`, `Command recognition
    still works — it is deterministic grammar, not ASR.` The panel
    checks the actual engine packages; no engine is installed here.
  - Voice grammar: 30-phrase deterministic grammar counted live from
    `voice_control.COMMANDS`; the 75-command / 10-namespace registry
    is unchanged.
- **`airmouse transcribe`, measured (piped stdin; rc 0):** banner
  `provider: simulated_stream` + `⚠ SIMULATED provider — install a
  local ASR engine for real transcription (see: airmouse
  voice-status)` + `text-input mode (microphone capture needs
  hardware)`; typed line became `[12:24:40] 95% "Hello world, this is
  a test"`; `status` showed `history: 2/500 segments` (bounded) +
  the transcripts dir; `pause` muted input; `clear` emptied history;
  `save` → `<home>/transcripts/transcript-<ts>.txt` ("17 bytes, text
  only — no audio is ever stored"); `save json` → `.json` with
  segments + confidence + provider + timestamps; EOF → summary +
  `session ended cleanly (no audio was ever stored)`. No file is
  written unless the user types `save`.
- **`airmouse help-me`, measured (rc 0):** overview panel (22-row
  gesture map incl. the `[refused by default]` OK row and the
  `NOT MAPPED yet` two-hand note; the 30-phrase grammar with the
  NOT-ASR note; gaze; teacher; health); `help-me "how do I scroll?"`
  → the 3 real scroll paths; `help-me "why didn't that work?"` → the
  gate chain (E-STOP → confidence 0.45/0.60 → destructive policy →
  0.12 s rate limit → gesture mapping) + `airmouse doctor` pointer.
- **Privacy, measured:** `privacy_manifest()` length = **24** (was
  20; +`academy_progress`, `onboarding_state`, `personal_profile`,
  `transcript_sessions`); `airmouse privacy` prints all with live
  exists flags and the PERSONALIZATION summary
  (Gestures learned / Voice patterns / Gaze calibration: Incomplete /
  Workflows) ending **"Nothing is uploaded."**
- **Temporal, measured:** the verify Temporal check exercises the
  real recognizer on synthetic frames each run — 293.4 µs/frame for
  recognize + features with the pinch lifecycle engaged; `temporal.py`
  exposes TrajectoryBuffer / PinchLifecycle / TemporalRecognizer /
  CompositionResolver / Hysteresis / Debouncer /
  FalsePositiveSuppressor / TrackingRecovery / CameraWatchdog /
  SensorHealthScore; the live-loop observer (source-verified:
  PinchLifecycle + TrajectoryBuffer + SensorHealthScore fed every
  frame → `TMP:`/`SENSOR:` badges + profile learning) dispatches
  nothing.
- **`airmouse doctor`** (this tree): READY 32 / OPTIONAL 6 /
  HARDWARE 2 / WARNING 1 / FAILED 0 → `[PARTIAL — review WARNING
  items]`. The single WARNING is `Hand tracking model — not downloaded
  yet` (this reset sandbox has not fetched the ~8 MB model; the
  documented behaviour since v15.1.0). The OPTIONAL delta vs v16.0.0
  (7 → 6) is the Speech row, which now reads `Transcription providers
  READY — local ASR: pocketsphinx` because the SpeechRecognition
  wrapper's sphinx backend is importable; the engine-level honesty
  lives in the v16.5 voice-status panel (above).

## Part 2 — SIMULATION VERIFICATION

All simulated runs are honest by contract — labelled at runtime,
never converted into hardware validation:

- **Transcription without an ASR engine** runs on the deterministic
  `simulated_stream` provider; the session banner carries
  `⚠ SIMULATED provider` on every start and the summary reports
  `mode: simulated` (measured; e.g. `segments: 1 words: 6 avg
  confidence: 95% provider: simulated_stream mode: simulated`).
- **Temporal recognizer measurements** run on synthetic sample
  streams (deterministic landmark sequences) — this proves the
  software path and its timing, never camera behaviour. The verify
  Temporal check, the gaze-academy simulated dry-runs (every result
  labelled SIMULATED, never credited as physical practice) and the
  profile-store/learning-loop tests all follow the same rule.
- **Teacher headless plans** are deliberately sensor-free: lessons
  print `PHYSICAL PRACTICE REQUIRED` and nothing is marked complete
  (measured); the Voice/Gaze Academy metric pipelines were exercised
  on synthetic samples in their test suites, always labelled.
- Simulated-verified subsystems unchanged from v16.0.0: guided lab
  7/7 simulation PASS (`[SIMULATION]` labels; 0/5 hardware rows,
  overall PARTIALLY VERIFIED), simulated browser bridge, twin, world
  model, goals/tasks/skills, recovery2, target resolver, AIP EXECUTE
  without `--aip-real` (labelled `simulated: true`, fail-closed with
  no grants), simulator, failure injection.
- The Gesture Lab remains a deliberate simulation instrument (REAL
  spine, dry-run executor, destructive refusal demonstrable without
  hardware).

## Part 3 — REAL WINDOWS VALIDATION

**NOT PERFORMED IN SANDBOX** (unchanged truth). No Windows machine
was available; the build environment is a headless Linux sandbox. The
Windows 10/11 validation procedure is handed to the user step by
step (COMMAND / WHAT TO DO / WHAT SHOULD HAPPEN / PASS / FAIL /
HOW TO FIX): **`WINDOWS_REAL_WORLD_TEST.md`** — now **28 steps**: the
previous 24 plus the v16.5 steps 16a–16d (first-run tour + onboarding
resume, `transcribe` with a real ASR engine, gesture-lab/academy with
the `TMP:` HUD badge visible, and a `help-me` sanity drill). Until
that procedure is executed by a human on real Windows hardware, every
Windows-runtime claim in the docs is marked "expected", not
"verified".

## Part 4 — PHYSICAL HARDWARE VERIFICATION

**NOT TESTED** — the sandbox is headless and has no peripherals.
`airmouse verify` and `airmouse test` report the five physical areas
as **ACTION_REQUIRED** (they can never auto-pass by design). The
v16.5-specific physical areas, explicitly:

| v16.5 physical area | Sandbox status | Where it is validated |
|---|---|---|
| First-run tour with a real TTY + camera/mic (`teach`, the auto-offer) | NOT TESTED — headless runs only ever print the honest plan; the offer is TTY-gated and proven never to block headless | WINDOWS_REAL_WORLD_TEST.md step 16a |
| Voice Academy microphone practice (L1 pass requires hearing you) | NOT TESTED (no microphone; typed practice path is the text-side fallback) | step 16a / guided test [5/12] |
| Gaze Academy live lessons (camera + eyes; passes only from real gaze samples) | NOT TESTED (synthetic samples only, always labelled SIMULATED) | step 16c / guided test [4/12] |
| Live ASR engines (vosk / whisper / pocketsphinx) — provider panel flips to real, segments from real audio | NOT TESTED — no engine installed in the sandbox; detection + honest labelling measured | step 16b |
| Dictation via microphone (VoiceTypingEngine end-to-end) | NOT TESTED (text-side engine verified; audio path untested) | guided test [6/12] |
| Temporal HUD under a real camera (`TMP:` / `SENSOR:` badges live) | NOT TESTED (observer code + startup probes only; on-screen look unverified) | step 16c |
| Two-hand geometry (unchanged) | NOT TESTED | step 10d |
| PyInstaller Windows bundles | NOT PRODUCED / NOT EXECUTED on real Windows | build.py path exists |

## Part 5 — NOT AVAILABLE / limitations (honest, non-negotiable)

- **Temporal proposals do not dispatch OS actions** — the observer
  PROPOSES ONLY; the gesture_spine remains the sole dispatcher and no
  parallel execution path exists (source-scan enforced). Temporal OS
  dispatch for new compositions: NOT AVAILABLE.
- **Two-hand ROTATE / DRAG** are detected honestly (report dict,
  help-me, teacher all say so) but have NO OS mapping — only two-hand
  ZOOM drives real ctrl+wheel. NOT AVAILABLE (row 25).
- **ASR engines are not bundled and are never auto-installed** —
  vosk/whisper/pocketsphinx are OPTIONAL; the install path is
  text-only guidance the user runs themselves (no subprocess, no
  network). ASR auto-execution: NOT AVAILABLE (row 79).
- **Command grammar ≠ ASR** — the built-in 30-phrase grammar is
  deterministic phrase matching, not speech recognition; the
  voice-status panel states this in plain text on machines without
  an engine.
- **RF hardware, cloud/telemetry integrations, the v15 one-choice
  onboarding interview, automatic `selftune_apply`** — unchanged, NOT
  AVAILABLE by design (rows 64, 69, 70, 60).
- Gaze personalization suggestions, temporal badges and profile
  learning are proposals/display only — nothing auto-applies
  (PREDICTION ≠ EXECUTION).

---

# AirMouse v16.0.0 Gesture-First Release Verification — preserved below

Date: 2026-09-04 (build environment: Linux x86_64 headless sandbox,
Python 3.12.14). Scope: the v16.0.0 gesture-first release candidate —
the v15.2.0 execution spine + gesture surface (preserved unchanged)
plus the documentation reset and release hygiene (docs/CLI_REFERENCE
regenerated from the live parser, docs/CAPABILITY_MATRIX rebuilt with
one status per capability, README stale claims removed,
WINDOWS_REAL_WORLD_TEST extended, CHANGELOG refreshed). Companion:
`WINDOWS_REAL_WORLD_TEST.md` (repo root), `docs/CAPABILITY_MATRIX.md`,
`README.md` §Verification status. **Headline honesty statement:
physical hardware (webcam, microphone, real hand tracking single- and
two-handed, real gaze, real browser automation, real OS input
automation, PyInstaller Windows bundles) was NOT tested in this
sandbox — it cannot be; the validation procedure is handed to the user
via `WINDOWS_REAL_WORLD_TEST.md` and `airmouse test --guided`.**

## Part 1 — AUTOMATED VERIFICATION

- **Full suite: 1556 passed / 2 skipped / 0 failed** (re-measured on
  the v16.0.0 release-candidate tree, 26.5 s, pytest). The 2 skips
  are honest headless skips, verbatim from `-rs`:
  `tests/test_endgame_browser_resolver.py:102 — no chrome/chromium/
  edge binary in this sandbox` and `:356 — real tesseract stack
  present — OCR opt-in works` (skipped because the OCR comparison
  needs the real tesseract binary; the honest error path is asserted
  separately). Baseline history: 1056 (v15.0.0) → 1236 (v15.1.0) →
  1323 (v15.1.1) → **1556 (v15.2.0/v16.0.0)**.
- **`airmouse verify`** (exit 0): 10/10 automated checks PASS — Core
  import; voice grammar + offline engine determinism; intelligence
  roundtrip; safety gates + e-stop latch + hierarchy (4 safety
  levels); offline selftest 18/18; simulated browser bridge; agent
  permission deny-by-default; exclusive lease + challenger refusal;
  AIP malformed-envelope rejection; pyproject/package version match.
  The 5 PHYSICAL rows report ACTION_REQUIRED by design (Part 4).
- **`airmouse doctor`** (exit 0, `[READY FOR TESTING]`): READY 32 /
  OPTIONAL 7 / HARDWARE 2 / WARNING 0 / FAILED 0 — 41 components
  across 12 sections (SYSTEM 6, PYTHON 11, AIRMOUSE 4, CAMERA 1,
  MICROPHONE 1, SPEECH 4, INPUT 2, BROWSER 3, INTELLIGENCE 2, AGENT 4,
  OFFLINE 1, SAFETY 2), measured on this tree.
- **v16 gesture surface, measured headless runs (all on this tree):**
  - `airmouse profile list` → 8 profiles; `profile accessibility` →
    "applied: 12 settings" + real config.toml written under
    `$AIRMOUSE_HOME`; `profile nope` → honest listing + exit 1.
  - `airmouse academy` (headless) → full 11-lesson plan, per-core-
    lesson status `PHYSICAL PRACTICE REQUIRED — needs camera + hand`,
    advanced lessons `not verifiable in this run (never
    auto-passed)`, exit 0; `academy bogus-lesson` → valid-ids
    listing + exit 1; progress file under the unified home.
  - `airmouse gesture-lab --no-cam` → observatory explanation + two
    exact example readouts, including
    `RESULT: blocked: destructive_action_blocked_by_policy (a gesture
    must never close windows)` — the destructive refusal is
    demonstrable without any hardware; exit 0.
  - `airmouse gestures` → built-in mappings include the new
    `double_pinch`, `pinch_hold`, `pinch_release`, `shake`, `four`,
    `five`, `swipe_up/down`, `circular_cw/ccw`.
  - Privacy manifest: **20 declared artifacts** (15 user-learning +
    config + third-party model + tutorial marker + backups + exports)
    printed by `airmouse privacy` with live exists flags.
  - Memory lifecycle: stores + real learning artifacts covered;
    `deletion_verifies()` re-scan reported in reset/delete output.
- **Agent last mile, measured end-to-end in the sandbox:**
  `python -m airmouse.aip_stdio` answers DISCOVER with protocol 1.0
  and 13 capabilities; EXECUTE with no grants →
  `permission_denied — decision 'ask': no rule; default ASK fails
  closed`; the standalone agent-core client (stdlib-only)
  connects, negotiates and receives the same fail-closed denial over
  the wire. `airmouse --aip-stdio` (CLI wrapper) serves the identical
  envelope but prints one banner line to stdout first — documented in
  `docs/CLI_REFERENCE.md`; strict first-line-JSON clients target the
  module entry.
- Version strings: the tree's `__init__.py`/`pyproject.toml` still
  read 15.1.1 at documentation time (bumped by release step 1; the
  packaging check in `airmouse verify` confirms they match each
  other).

## Part 2 — SIMULATION VERIFICATION

- **Guided test laboratory, headless non-interactive run
  (`airmouse test`, exit 0): 7/7 simulation tests PASS, 0/5 hardware
  tests (all honestly ACTION_REQUIRED), overall PARTIALLY VERIFIED.**
  Seven simulation PASSes unchanged: Installation; Intelligence
  (`[SIMULATION]` label); Browser (7-step simulated flow); Agent
  (benign request through the full pipeline, destructive REJECTED);
  Multi-Agent (lease → conflict refused → handoff →
  `emergency_stop_all`); Recovery (3 recoveries + permission denial
  correctly NOT retried); Offline (18/18, cloud ASR blocked, local
  grammar allowed).
- Simulated-verified subsystems (status unchanged from v15.0.0):
  twin, temporal world model, goals/tasks, skills, recovery2, target
  resolver, AIP, agent SDK, multi-agent registry, DO IT WITH ME,
  simulator, failure injection, explainability, licensing,
  marketplace, persistence layer, setup wizard, user errors, first-
  run menu matrix.
- **AIP EXECUTE without `--aip-real`** is an honest simulation: every
  result is labelled `simulated: true`; with the permission engine
  empty the default is fail-closed denial (measured). With
  `--aip-real` the EXECUTE path routes through the real
  permission-gated ActionEngine in-process tests; real OS effects
  remain Part 4.
- The Gesture Lab is a deliberate simulation instrument: a REAL
  `GestureActionRouter` whose executor is a dry-run stub — the
  confidence/policy/rate gates are the real code, the hands are the
  user's, and nothing real can fire (allow_destructive forced False
  in the lab).

## Part 3 — REAL WINDOWS VERIFICATION

**NOT PERFORMED IN SANDBOX** (unchanged truth). No Windows machine was
available; the build environment is a headless Linux sandbox. The
complete Windows 10/11 validation procedure is handed to the user,
step by step with COMMAND / WHAT TO DO / WHAT SHOULD HAPPEN / PASS /
FAIL / HOW TO FIX blocks: **`WINDOWS_REAL_WORLD_TEST.md`** in the
repository root — now **24 steps**: the original 19 plus the v16
steps 10a–10e (gesture profiles, Gesture Academy, Gesture Lab, two-
hand check, agent E-stop drill over `airmouse --aip-stdio`). Until
that procedure is executed by a human on real Windows hardware, every
Windows-runtime claim in the docs is marked "expected", not
"verified".

## Part 4 — PHYSICAL HARDWARE VERIFICATION

**NOT TESTED** — the sandbox is headless and has no peripherals.
`airmouse verify` and `airmouse test` report all five physical areas
as **ACTION_REQUIRED** (they can never auto-pass by design):

| Physical area | Sandbox status | Where it is validated |
|---|---|---|
| Webcam | ACTION_REQUIRED (HARDWARE: "no camera device answered at index 0") | guided test [2/12]; WINDOWS_REAL_WORLD_TEST.md Steps 9 / 10b / 10d |
| Microphone | ACTION_REQUIRED (HARDWARE: "PortAudio works but reported no input devices") | guided test [5/12]/[6/12]; WINDOWS_REAL_WORLD_TEST.md Steps 6/12/13 |
| Hand tracking (single-hand, 17 poses + motion + pinch events) | ACTION_REQUIRED | guided test [2/12]/[3/12]; WINDOWS_REAL_WORLD_TEST.md Steps 9/10/10c |
| Two-hand geometry (HOLD/ZOOM/ROTATE/DRAG) | ACTION_REQUIRED (detector code + synthetic-hand tests only) | WINDOWS_REAL_WORLD_TEST.md Step 10d |
| Gaze | ACTION_REQUIRED | guided test [4/12]; WINDOWS_REAL_WORLD_TEST.md Step 11 |
| Real browser automation (CDP :9222) | ACTION_REQUIRED (no browser installed; `--launch-browser` honestly reports `browser_not_found`) | guided test [8/12] is simulated; real: WINDOWS_REAL_WORLD_TEST.md Step 15 |
| Live OS input automation (cursor/clicks/keys, two-hand zoom on screen, macro replay) | ACTION_REQUIRED (pynput present, no display) | WINDOWS_REAL_WORLD_TEST.md Step 10 |
| PyInstaller Windows bundles | NOT PRODUCED / NOT EXECUTED on real Windows | build.py path exists; bundle validation is a Windows task |

## Part 5 — NOT TESTED

- **RF hardware** — the RF modality is an abstraction with a
  simulated provider only; no RF hardware exists or is claimed
  (status: NOT AVAILABLE).
- **Real local ASR engines** — pocketsphinx / vosk / whisper adapters
  exist but the engines are not installed in the sandbox; real engine
  behaviour on Windows audio is unverified (`airmouse voice-status`
  reports availability honestly: all False in the sandbox).
- **Cloud integrations** — none exist by design (no cloud code path);
  "cloud" is structurally impossible to enable, so there is nothing
  to test (status: NOT AVAILABLE).
- **Windows runtime** — see Part 3 (REAL WINDOWS: NOT PERFORMED).
- **PyInstaller bundles** (`build.py --windows` etc.) — build path
  present, bundle artifacts not produced or executed in this pass
  (status: PHYSICAL TEST REQUIRED).
- **Onboarding interview, automatic `selftune_apply`, two-hand
  rotate/drag OS actions, default actions for thumbs_down/four/five**
  — not wired / not available by design; documented as NOT AVAILABLE
  in `docs/CAPABILITY_MATRIX.md` (rows 22, 25, 60, 70).

---

# AirMouse v15.1.0 Hardening Verification — preserved below

Date: 2026-09-04 (build environment: Linux x86_64 headless sandbox,
Python 3.12.14). Scope: the v15.1.0 hardening release of v15.0.0 —
new release surface (`setup`, `doctor`, `test [--guided]`, `verify`,
`privacy`, `memory status|export|reset|delete`, first-run menu),
persistence layer, CLI quality, performance budgets, packaging.
No architecture changes were made; every v15.0.0 subsystem retains
its verified status below. Companion: `WINDOWS_REAL_WORLD_TEST.md`
(repo root), `docs/CAPABILITY_MATRIX.md`, `README.md` §Verification
status. **Headline honesty statement: physical hardware (webcam,
microphone, real hand tracking, real gaze, real browser automation)
was NOT tested in this sandbox — it cannot be; the validation
procedure is handed to the user via `WINDOWS_REAL_WORLD_TEST.md` and
`airmouse test --guided`.**

## Part 1 — AUTOMATED VERIFICATION

- **Full suite: 1236 passed / 0 failed / 0 skipped** (re-measured
  2026-09-04 on the v15.1.0 tree, 21 s, pytest 9.0.2). Baseline at
  v15.0.0 was 1056; +180 new hardening tests:
  `test_cli_quality.py` 33, `test_setup_persistence.py` 46,
  `test_doctor.py` 42, `test_guided_test.py` 54,
  `test_release_perf.py` 5. All 1056 pre-existing tests stayed green.
- **`airmouse verify`** (exit 0): 10/10 automated checks PASS — Core
  import; voice grammar + offline engine determinism; intelligence
  learn/observe roundtrip; safety gates + e-stop + hierarchy; offline
  selftest 18/18; simulated browser bridge; agent permission
  deny-by-default; exclusive lease + challenger refusal; AIP
  malformed-envelope rejection; pyproject/package version match
  (15.1.0). The 5 PHYSICAL rows report ACTION_REQUIRED by design
  (Part 4).
- **`airmouse doctor`** (exit 0, `[READY FOR TESTING]`) summary
  counts on the headless sandbox:

  ```
  READY:       32
  OPTIONAL:    7
  HARDWARE:    2
  WARNING:     0
  FAILED:      0
  ```

  41 components across 12 sections (SYSTEM 6, PYTHON 11, AIRMOUSE 4,
  CAMERA 1, MICROPHONE 1, SPEECH 4, INPUT 2, BROWSER 3, INTELLIGENCE
  2, AGENT 4, OFFLINE 1, SAFETY 2). The READY/WARNING split depends
  on one file: with the hand-landmarker model present (7.5 MB at
  `~/.airmouse/hand_landmarker.task`) it is READY 32 / WARNING 0; on
  a machine without the model yet, "Hand tracking model" reports
  WARNING (READY 31 / WARNING 1). HARDWARE 2 = webcam + microphone
  (none exist in the sandbox); OPTIONAL 7 = 2 NOT_INSTALLED voice
  extras + 2 UNAVAILABLE pynput rows (no display) + simulated-only
  transcription providers + absent Chrome/Edge + mock executors.
- **`airmouse setup`** (non-interactive, never installs): completed
  11 steps, storage initialised, config created, hardware rows
  honestly ACTION_REQUIRED, smoke test 15 self-checks passed with
  hardware checks honestly deferred, marker written.
- **Offline selftest**: 18/18 checks passed with networking truly
  blocked (`airmouse offline-test`).
- Packaging: `pyproject.toml` version == `__version__` == 15.1.0;
  MIT; `requires-python >= 3.9`; extras voice/tts/ocr/sound.

## Part 2 — SIMULATION VERIFICATION

- **Guided test laboratory, headless non-interactive run
  (`airmouse test`, exit 0): 7/7 simulation tests PASS, 0/5 hardware
  tests (all honestly ACTION_REQUIRED), overall PARTIALLY VERIFIED.**
  The seven simulation PASSes: Installation; Intelligence
  (OBSERVED/PREDICTED/EXECUTED with the safety gate, labelled
  `[SIMULATION]`); Browser (7-step simulated flow: open → click →
  verify → new tab → navigate → back); Agent (benign request through
  REQUEST→PERMISSION→SAFETY→AGENT→ACTION→VERIFICATION→RESULT;
  destructive request REJECTED; hierarchy demonstrated); Multi-Agent
  (lease granted → conflict refused → handoff → `emergency_stop_all`
  stops both agents and gates); Recovery (missing_target, timeout,
  app_crash all recovered; permission_denial safely stopped WITHOUT
  retry); Offline (18/18, cloud ASR blocked, local grammar allowed).
- Simulated-verified in the test suite (unchanged status from
  v15.0.0): twin, temporal world model, goals/tasks, skills,
  recovery2, target resolver, AIP, agent SDK, multi-agent registry,
  DO IT WITH ME, simulator, failure injection, explainability,
  licensing, marketplace — plus the new persistence layer (atomic
  writes, checksum tamper/quarantine/recovery, migrations, path
  traversal rejection), setup wizard (consent gating, exact pip
  argv), user errors (token redaction incl. ghp_/sk-/Bearer),
  first-run menu (TTY matrix).
- Measured CLI performance (sandbox, wall clock) against the budgets
  pinned in `tests/test_release_perf.py`:

  | Command | Measured | Pinned budget (test) |
  |---|---:|---:|
  | `airmouse --version` | ≈ 1.5 s (1.4 s in the first hardening run) | 6.0 s |
  | `airmouse doctor` | ≈ 2.6 s | 12.0 s |
  | `airmouse verify` | ≈ 1.6 s | 8.0 s |
  | `airmouse test` | ≈ 1.6 s | 8.0 s |

## Part 3 — REAL WINDOWS VERIFICATION

**NOT PERFORMED IN SANDBOX.** No Windows machine was available; the
build environment is a headless Linux sandbox. The complete Windows
10/11 validation procedure is handed to the user, step by step with
COMMAND / WHAT TO DO / WHAT SHOULD HAPPEN / PASS / FAIL / FIX blocks:
**`WINDOWS_REAL_WORLD_TEST.md`** in the repository root (19 steps:
CMD → Python → install → setup → doctor → webcam/mic privacy →
verify → guided lab → hand → mouse → gaze → voice → dictation →
intelligence → real browser via Chrome `--remote-debugging-port=9222`
→ agent safety → recovery/offline → export → report assembly).
Until that procedure is executed by a human on real Windows
hardware, every Windows-runtime claim in the docs is marked
"expected", not "verified".

## Part 4 — PHYSICAL HARDWARE VERIFICATION

**NOT TESTED** — the sandbox is headless and has no peripherals.
`airmouse verify` and `airmouse test` report all five physical areas
as **ACTION_REQUIRED** (they can never auto-pass by design):

| Physical area | Sandbox status | Where it is validated |
|---|---|---|
| Webcam | ACTION_REQUIRED (HARDWARE: "no camera device answered at index 0") | `airmouse test --guided` test [2/12]; WINDOWS_REAL_WORLD_TEST.md Step 9 |
| Microphone | ACTION_REQUIRED (HARDWARE: "PortAudio works but reported no input devices") | guided test [5/12]/[6/12]; WINDOWS_REAL_WORLD_TEST.md Steps 6/12/13 |
| Hand tracking | ACTION_REQUIRED | guided test [2/12]/[3/12]; WINDOWS_REAL_WORLD_TEST.md Steps 9/10 |
| Gaze | ACTION_REQUIRED | guided test [4/12]; WINDOWS_REAL_WORLD_TEST.md Step 11 |
| Real browser automation (CDP :9222) | ACTION_REQUIRED (no browser installed) | guided test [8/12] is simulated; real: WINDOWS_REAL_WORLD_TEST.md Step 15 |

## Part 5 — NOT TESTED

- **RF hardware** — the RF modality is an abstraction with a
  simulated provider only; no RF hardware exists or is claimed.
- **Real local ASR engines** — pocketsphinx / vosk / whisper adapters
  exist but the engines are not installed in the sandbox; real
  engine behaviour on Windows audio is unverified
  (`airmouse voice-status` reports availability honestly).
- **Cloud integrations** — none exist by design (no cloud code path);
  "cloud" is structurally impossible to enable, so there is nothing
  to test.
- **Windows runtime** — see Part 3.
- **PyInstaller bundles** (`build.py --windows` etc.) — build path
  present, bundle artifacts not produced in this hardening pass.

---

# AirMouse v15.0.0 report (v12.0 → v15.0) — preserved below

Date: 2025-09-05 (build environment: Linux sandbox, Python 3.12.14)
Scope: v11.5.0 → v15.0.0 "Universal Human + AI Interaction Platform"
per mission spec (7 milestone versions: v12.0.0, v12.5.0, v13.0.0,
v13.5.0, v14.0.0, v14.5.0, v15.0.0). Companion documents:
`README.md`, `docs/V15_ARCHITECTURE.md`, `docs/AIP_SPEC.md`,
`docs/AGENT_SDK.md`, `docs/MULTI_AGENT.md`, `docs/SKILLS.md`,
`docs/DEVELOPER_GUIDE.md`, `docs/USER_GUIDE.md`. The full v11.5 and
v10 reports are preserved below.

---

## V1. Tests — exact numbers

```
Baseline before v12 : 786 passed, 0 failed, 0 skipped   (v11.5.0 tag)
Final v15 suite     : 1056 passed, 0 failed, 0 skipped  (Python 3.12.14, ~12 s)
```

New tests by file (measured, `pytest --collect-only`):

| File | New tests |
|---|---:|
| `tests/test_v12.py` | 39 |
| `tests/test_v13.py` | 40 |
| `tests/test_v13_5.py` | 19 |
| `tests/test_v14.py` | 33 |
| `tests/test_v14_5.py` | 34 |
| `tests/test_v15.py` | 75 |
| `tests/test_hardening_v15.py` | 30 |
| **Total new** | **270** |

**786 preserved** v10/v11.5 tests stayed green throughout; the
milestone tags **v12.0.0, v13.0.0, v13.5.0, v14.0.0, v14.5.0** were
pushed with green suites at each commit (825 → 865 → 884 → 917 → 951 →
1026 → 1056).

## V2. SIMULATION vs PHYSICAL — the honest table

| Subsystem | Status |
|---|---|
| Personal Interaction Twin (v12) | **SIMULATION-VERIFIED** |
| Temporal world model (v12.5) | **SIMULATION-VERIFIED** |
| Goals + TaskEngine (v13) | **SIMULATION-VERIFIED** |
| Skills + library (v13.5) | **SIMULATION-VERIFIED** |
| Recovery engine + target resolver (v14) | **SIMULATION-VERIFIED** |
| AIP protocol + SDKs + agent-core (v14.5) | **SIMULATION-VERIFIED** — protocol level, in-process AND stdio transports |
| Multi-agent registry + permissions + DiTM + onboarding + licensing + marketplace + simulator + failure injection + explainability + CLI + HUD (v15) | **SIMULATION-VERIFIED** |
| ALL v12–v15 subsystems, aggregate | **SIMULATION-VERIFIED ONLY — NOT PHYSICALLY VERIFIED** |
| Webcam / microphone / gaze / RF / real-Chrome-CDP / live-ASR | **NOT PHYSICALLY VERIFIED** (unchanged since v11.5) |

The §26 simulator IS the computer model for all v12→v15 verification:
deterministic (same script → same final state), bounded, no real
display. No physical hardware claim is made anywhere in the v12→v15
arc.

## V3. Security audit results (§23 + §30, executable in
`tests/test_hardening_v15.py`)

* **No `shell=True` / `eval(` / `exec(` / `os.system` / `pickle.load`
  / `yaml.load`** anywhere in the package.
* `subprocess` usage is **argv-list only** (no shell).
* **Zero network code in all 17 new v12→v15 modules** — urllib /
  requests / raw-socket connects / httpx asserted absent.
* **11 parser surfaces fuzzed with 23 hostile strings each** (§30):
  AIP message parser + action schema, goal parser, SDK execute path,
  marketplace manifests, twin import, skill import, task engine,
  permission engine, agent registry, target resolver, workflow
  importer — SQL injection, command substitution, path traversal, XSS,
  credential shapes, control characters, 5000-char blobs, wrong types.
  No crashes, no execution, no coercion: everything fails closed.
* **Telemetry default defect found + fixed**: the legacy v9
  perf-report flag collided with the §21 privacy telemetry flag;
  `telemetry_enabled` is now authoritatively False
  (`test_telemetry_structurally_off`).
* Permission hierarchy (E-STOP > HUMAN OVERRIDE > SAFETY POLICY >
  PERMISSION > AGENT > PREDICTION) holds under adversarial agents;
  `permission_denied` never retries; malformed requests fail closed.

## V4. Performance budgets (all enforced in
`tests/test_hardening_v15.py`)

| Operation | Budget |
|---|---|
| Twin learn | < 10 ms |
| World-model observe | < 10 ms |
| Task create | < 10 ms |
| AIP message parse | < 5 ms |
| Goal/intent parse | < 50 ms |
| Target resolve | < 20 ms |
| SDK execute (in-process) | < 50 ms |
| Recovery loop round | < 20 ms |
| agent-core import | < 200 ms |

## V5. Scope note

Only the numbers in sections V1–V4 are claimed. Component self-test,
offline selftest, packaging and v11.5/v10 details are unchanged from
the reports below.

---

# AirMouse v11.5.0 — Final Verification Report

Date: 2025-09-05 (build environment: Linux sandbox, Python 3.12)
Scope: v10.0.0 → v11.5.0 "Adaptive Human-Computer Intelligence" per
mission spec. Companion documents: `README.md` (user guide),
`docs/V11_5_ARCHITECTURE.md` (deep dive), `docs/SECURITY.md`,
`docs/PRIVACY.md`, `CHANGELOG.md` (history). The full v10 report is
preserved below.

---

## 1. Implementation summary

v11.5 adds 17 new modules (9 in the `intelligence/` subpackage) and
extends 4 existing ones. Line counts are real `wc -l` values on the
final tree (docstrings included). Package total: **58 Python modules /
29,646 LOC** (v10: 41 modules / 22,781 LOC ⇒ net growth ≈ +6,900
lines; `browser_extension/` assets unchanged at 235 lines).

| Module | LOC | Status | Contents |
|---|---:|---|---|
| `intelligence/model.py` | 858 | NEW | PersonalInteractionModel: NGram+ActionMarkov+Command+Emoji+FeatureWeights; AIMM artifact (magic+version); ~30 MB capacity budget; quantized; deterministic pruning |
| `intelligence/plugin.py` | 499 | NEW | IntelligencePlugin facade — never raises; 8 lifecycle states; atomic artifact persistence; validated export/import profile |
| `intelligence/workflows.py` | 500 | NEW | WorkflowDiscovery (approval-gated), WorkflowStore, WorkflowRunner (preview + destructive confirmations), ProactiveAssistant, Suggestion |
| `intelligence/memory.py` | 373 | NEW | InteractionMemory + PatternRecord schema; fail-closed sensitive-data scrubbing (is_sensitive/scrub_pattern); bounded 5,000 |
| `intelligence/personalization.py` | 349 | NEW | Gesture/Gaze/Voice profiles incl. alias learning; PersonalizationEngine |
| `intelligence/vocabulary.py` | 310 | NEW | PersonalVocabulary terms + corrections; validated import/export |
| `intelligence/prediction.py` | 271 | NEW | Predictor → explainable Prediction; EMOJI_KEYWORDS baseline |
| `intelligence/selftune.py` | 187 | NEW | SelfTuner; 10 TUNABLES with hard bands + min-samples gates |
| `intelligence/__init__.py` | 98 | NEW | IntelligenceState (8 states); AIMM magic/version/capacity constants |
| `transcription.py` | 624 | NEW | LiveTranscriptionEngine streaming pipeline; adapters; punctuation/caps/numbers/vocab chain; history/export/search; wer(); metrics |
| `modes.py` | 832 | NEW | ModeController + 6 mode phrase tables; PresentationController (generic hotkeys); Timeline/StudyTimer/Notes/SourceCapture; MeetingMode; AccessibilityProfiles (8 + custom) |
| `dictation_text.py` | 343 | NEW | VoiceTypingEngine (modes, spoken formatting, edit commands, undo/redo); TextPredictor; EmojiSuggester (30 s cooldown) |
| `fusion2.py` | 291 | NEW | FusionEngine2 (9 signals), ConflictResolver, executable policy, RFExtendedProvider + RFNoHardware |
| `selftest.py` | 233 | NEW | 15-component self-test with PASS/FAIL/OPTIONAL/HARDWARE |
| `world_model.py` | 265 | NEW | WorldModel bounded snapshot + likely intent; ContextualCommandResolver (12 families) |
| `text_control.py` | 250 | NEW | TextController — 16 TextOps, keyboard fallback, coordinate-independent |
| `privacy.py` | 193 | NEW | PrivacyDashboard/PrivacyFlags/ConnectionState; delete/reset/clear/export/import |
| `__main__.py` | 2,164 | EXTENDED | v11.5 flags + 5 subcommands + HUD badges AI:/MODE:/SUG:/transcript |
| `agent.py` | 1,045 | EXTENDED | guarded intelligence/world_model/fusion2/modes wiring; `_learn_from_report` learning events |
| `config.py` | 593 | EXTENDED | 16 v11.5 TOML sections (backward compatible) |
| `offline.py` | 423 | EXTENDED | offline selftest 13 → 18 checks |

All v11.5 modules are stdlib-only, import headless, and run with
networking disabled (proven by the 18-check offline selftest).

## 2. Tests — exact numbers

```
786 passed, 0 failed, 0 skipped            (pytest tests/ -q, 11.0 s)
```

Breakdown: **630 preserved v10/v9 tests (unchanged) + 156 new v11.5
tests (`tests/test_v115.py`, 1,790 lines, 20 sections)**. The v11.5
suite covers: §5 model (capacity/corruption/quantization), §6 memory +
scrubbing, §7 vocabulary, prediction explainability, self-tuning
bands, personalization, workflows (discovery/store/runner gates),
§4 plugin contract (disabled/corrupted/incompatible/out-of-memory/
privacy-paused), §8 transcription, §9 voice typing, §10/§11 text
prediction + emoji, §12 text control, §13/§14 world model +
contextual commands, §17–23 modes, §22 accessibility chains, §24
Fusion 2.0 (all combos + conflicts), §31 offline under real network
isolation, §32 privacy dashboard, §43 malicious input, §44 resource
limits, §34 performance budgets, §45 self-test, §46 final integration
simulations (voice→learning loop + teacher/student/office/developer/
workflow role scenarios).

## 3. Offline verification (§31) — 18/18

`run_offline_selftest()` re-run for this report: **18/18 PASS**.
The 13 v10 checks (voice grammar/command/dictation/context, custom
gesture, RF bridge, simulated browser ×3, fusion pipeline, event bus,
offline gate, final network-refused proof) are joined by 5 new checks
under the same REAL socket-level isolation
(`network_isolation()` monkeypatches `connect`/`connect_ex`/
`create_connection` to raise; loopback passthrough by default):

| # | Check | What it proves |
|---|---|---|
| 13 | `intelligence_offline` | plugin loads to `available`; learns click-after-open_app; predicts offline |
| 14 | `memory_offline` | pattern recording + scrubbing works network-free |
| 15 | `vocabulary_offline` | term/correction learning + application offline |
| 16 | `transcription_offline` | simulated streaming produces a finalized segment offline |
| 17 | `fusion2_offline` | 9-signal fusion reaches an executable click offline |
| 18 | `network_actually_blocked` | a real `socket.create_connection(("example.com", 80))` is refused |

## 4. Component self-test (§45)

`airmouse self-test` — 15 components, re-run for this report:

```
Core/Voice/Transcription/Gesture/Gaze/Browser/Fusion/Intelligence/
Memory/Prediction/Safety/Offline/Packaging ... PASS
RealLocalASR ... OPTIONAL (vosk/whisper/pocketsphinx not installed)
Camera ......... HARDWARE (webcam unavailable in headless environment)
RESULT: PASS (13 pass, 1 optional, 1 hardware, 0 fail)
```

OPTIONAL/HARDWARE are honest statuses — missing dependencies or
hardware never masquerade as failures.

## 5. Performance (§34; this environment, budgets asserted by tests)

Re-measured for this report with the exact §34 workloads (single run;
budgets asserted in `tests/test_v115.py` — all 8 budget tests pass):

| Metric | Measured | Budget |
|---|---|---|
| Prediction (`predict_next_action`) | ≈ 0.004 ms | < 50 ms |
| Emoji suggestion | ≈ 0.024 ms | < 50 ms |
| Memory record (`InteractionMemory.record`) | ≈ 0.005 ms | < 10 ms |
| Transcription tick (`feed_audio`) | ≈ 0.001 ms | < 100 ms |
| Fusion (`FusionEngine2.fuse`) | ≈ 0.008 ms | < 20 ms |
| Event bus publish | ≈ 0.002 ms | < 5 ms (test bound) |
| Model load (200-sentence artifact, 17 KB) | ≈ 3.4 ms | < 500 ms |

Model size honesty: fresh install ≈ 0.1 KB
(`airmouse intelligence` output); a 200-sentence training run
produces a **17,296-byte** artifact. The ~30 MB figure is the hard
capacity budget, not the shipped size.

v10/v9 performance numbers remain valid (v10: grammar 50 utterances
≈ 5 ms, 30 voice transcripts ≈ 3 ms, 1000 bus events ≈ 2 ms, 1000
context resolves ≈ 4 ms).

## 6. SIMULATION vs PHYSICAL hardware status — EXPLICIT

**No physical webcam, microphone, or RF hardware exists in the build
environment. Nothing below claims physical verification.**

| Area | Status | What is verified |
|---|---|---|
| All v11.5 logic (intelligence, memory, vocabulary, prediction, self-tune, workflows, transcription pipeline, dictation, text control, world model, modes, fusion2, privacy, selftest) | **SIMULATION-VERIFIED** | deterministic simulators + injected timestamps; 156 new tests, 630 preserved |
| Webcam / camera paths (hand, gaze) | **NOT PHYSICALLY VERIFIED** | no camera in sandbox; Camera = HARDWARE in self-test |
| Microphone / audio-device paths | **NOT PHYSICALLY VERIFIED** | VAD/pipeline verified with synthetic buffers; no mic in sandbox |
| Gaze behavior on real eyes | **NOT PHYSICALLY VERIFIED** | v9 targeting-aid caveat unchanged |
| RF (any hardware, incl. RFExtendedProvider protocols) | **NOT PHYSICALLY VERIFIED** | `RFNoHardware` honest default; simulated providers only |
| Real Chrome/Edge via CDP | **NOT PHYSICALLY VERIFIED** | v10 guarded implementation unchanged; graceful-unavailability tested |
| PocketSphinx / Vosk / Whisper ASR | **NOT PHYSICALLY VERIFIED** (engines not installed) | adapters present with guarded imports; **simulated streaming provider + transcript injection are the verified paths**; `detect_providers()` reports honestly |

Recommended first real-machine checks: `airmouse self-test`,
`airmouse voice-status`, `airmouse --gaze-calibrate`,
`airmouse --teacher`, `airmouse offline-test`.

## 7. Security / privacy audit (v11.5 additions)

- Grep-verified: **no `shell=True`, `eval(`, `exec(`, `os.system`**
  anywhere in the 58 package modules; subprocess remains argv-only
  with timeouts (the one `--shell` string is xdotool's own output
  flag, not a shell).
- New validation surfaces (workflow step regex, import scrubbing,
  artifact bounds, length caps) enumerated in `docs/SECURITY.md` §3.
- §43 hostile-corpus suite passes (SQLi, command substitution,
  traversal, binary junk, XSS, credential shapes → no exceptions, no
  execution, no secret persistence).
- Privacy: telemetry OFF by default; `cloud` flag structurally
  impossible (forced False in `PrivacyFlags.__post_init__`; `set()`
  refuses it); fail-closed scrubbing keeps credential-shaped input out
  of the learned store; bounded local audit log.

## 8. Regression gate & packaging

- All **630** pre-existing v10/v9 tests pass **unchanged**; v11.5
  additions are confined to `tests/test_v115.py`. Product-bug fixes
  found during development are listed in CHANGELOG "Fixed".
- `pyproject.toml` at **11.5.0**; `airmouse --version` →
  `AirMouse v11.5.0 — Adaptive Human-Computer Intelligence Edition`;
  all v11.5 flags/subcommands registered (`--help` inspection).
- Wheel build/tag/release remains a release step from this tree.

## 9. Known limitations (honest)

- **Simulation-first:** every claim above is simulation-verified;
  physical camera/mic/gaze/RF, real-Chrome CDP, and live offline-ASR
  engines are **not physically verified** (§6). Real-machine threshold
  tuning is expected (the bounded self-tuner is the intended
  mechanism).
- The personal model is a bounded statistical system, **not** a neural
  LLM; it ships empty and only knows what it observed on your machine.
- Punctuation/capitalization are deterministic heuristics, not an AI
  punctuation model (see `docs/TRANSCRIPTION_GUIDE.md` §4).
- Meeting summaries contain only what the user marked; **no speaker
  identification** is claimed or implemented.
- Presentation control uses generic hotkeys — a few actions are
  app-dependent (documented in `docs/TEACHER_GUIDE.md`).
- Learned artifacts under `~/.airmouse/intelligence/` are plaintext
  JSON/binary on local disk (scrubbed + bounded; disk encryption is
  the OS's job).

---

# AirMouse v10.0.0 — Final Verification Report (preserved)

Date: 2025-09-04 (build environment: Linux sandbox, Python 3.12.14)
Scope: v9.0.0 → v10.0.0 "Universal Offline Interaction Engine" per
mission spec. Companion documents: `README.md` (user guide),
`docs/V10_ARCHITECTURE.md` (deep dive), `CHANGELOG.md` (history).

---

## 1. Implementation summary

v10 adds 9 new modules and extends 8 existing ones. Line counts are
real `wc -l` values on the final tree (docstrings included).

| Module | LOC | Status | Contents |
|---|---:|---|---|
| `eventbus.py` | 323 | NEW | EventBus/Subscriber/MultiSubscriber, bounded queues (drop-oldest), history ring, stats |
| `voice_commands.py` | 540 | NEW | 75-command deterministic grammar, 10 namespaces, `<slot>` entities, ambiguity flags |
| `offline_voice.py` | 905 | NEW | OfflineSpeechProvider protocol + 4 providers, EnergyVAD hysteresis, wake-word gate, dictation buffer, COMMAND/DICTATION/HYBRID |
| `gesture_registry.py` | 424 | NEW | Full gesture vocabulary (v5 superset + v10 set), user-defined JSON sequence mappings, sequence matcher, double-pinch synthesis |
| `rf.py` | 228 | NEW | RFProvider protocol, Simulated/Dummy providers, RFBridge → bus; optional hardware, idles without |
| `system_actions.py` | 643 | NEW | 16 SYSTEM_OPS + 8 FILE_OPS, argv-only subprocess, allowlisted roots, sanitize_file_name, validate_url |
| `browser.py` | 1,948 | NEW | Bridge protocol + Simulated/CDP transports, target mapper, semantic resolver, action verifier, controller |
| `browser_bridge.py` | 331 | NEW | Localhost-only bridge server (127.0.0.1:17843) + MV3 extension round-trip helper |
| `offline.py` | 367 | NEW | OfflineGate, network_isolation (real socket blocking), run_offline_selftest (13 checks) |
| `browser_extension/` | 308 | NEW | MV3 extension source: manifest.json (14), background.js (101), content.js (120), README.md (73) |
| `interfaces.py` | 890 | EXTENDED | Event/EventKind(14)/VoiceMode/CommandNamespace(10)/ContextState; Modality +RF+BROWSER; IntentType 52, ActionType 32 |
| `actions.py` | 1,063 | EXTENDED | Canonical §10 vocabulary, v10 param normalization, destructive-op flags, executor injection |
| `context.py` | 364 | EXTENDED | v10 ContextEngine (browser state, gaze TTL 2 s, selection, recent action, deictic resolution) |
| `safety.py` | 487 | EXTENDED | SENSITIVE_TYPES + SHUTDOWN/RESTART/LOCK/SLEEP/CLOSE_TAB; param-level FILE_OP/SYSTEM_OP refinement |
| `hands_free.py` | 585 | EXTENDED | 8 named combos, SensorHealth, largest-alive-subset downgrade ladder |
| `agent.py` | 986 | EXTENDED | inject_intent, poll_events, context integration, v10 executor overrides |
| `__main__.py` | 1,981 | EXTENDED | v10 flags/subcommands, full wiring, HUD badges V10/CMD/RF/BROWSER/VER |
| `config.py` | 502 | EXTENDED | `[v10]` TOML section (13 keys) |

Package total: **22,781 lines** across 41 modules under `airmouse/`
(v9 report: 15,881 lines) — net v10 growth ≈ 6,900 lines.
All v10 core modules are stdlib-only and import with networking
disabled.

## 2. Tests — exact numbers

```
630 passed, 0 failed, 0 skipped, 0 xfailed   (pytest tests/ -q, 10.3 s)
```

Breakdown: **497 pre-existing v9 tests (all preserved, unchanged) +
133 new = 19 browser tests (`tests/test_browser.py`) + 114 v10 tests
(`tests/test_v10.py`)**.

New suite coverage:
- `tests/test_browser.py` (19): simulated bridge state/click/verify
  cycle, ScreenTarget mapping, target-map finders, deterministic
  semantic resolver for the §12 utterance set, action verifier,
  controller execute+verify pipeline incl. bus/context/offline-gate
  wiring, CDP never-raise/unavailable behaviour, localhost bridge
  server POST→GET round-trip
- `tests/test_v10.py` (114, 14 sections): command grammar + §7 intent
  mapping; OfflineVoiceEngine modes/VAD/wake-word/dedup/dictation/
  hybrid/provider detection; EventBus publish/poll/history/stats;
  ContextEngine incl. deictic resolution + snapshot isolation;
  GestureRegistry incl. sequences/wildcard/save-load/override; RF
  degradation; system+file executors incl. sanitize/validate_url/
  root-refusal/traversal/dry-run; SafetySystem §18 confirmations;
  hands-free combos; offline §17 incl. the real `run_offline_selftest`
  + `network_isolation`; browser verifier/resolver extras; fusion
  pipeline via InteractionAgent injection/voice/RF-confirm-retry;
  performance budgets §20/§21

All new tests are deterministic (explicit `now=` timestamps), headless
(no cv2/mediapipe/pynput), and network-free except the §17 isolation
tests, which *block* networking as the behavior under test.

## 3. Offline verification (§17)

**Methodology.** `offline.network_isolation()` monkeypatches
`socket.socket.connect`, `socket.socket.connect_ex` and
`socket.create_connection` to raise `_NetworkBlockedError` for the
duration of its body (originals restored on exit). Loopback stays
available by default (the 127.0.0.1-only browser bridge must keep
working offline); `block_localhost=True` gives the strictest mode.
Every selftest check therefore runs with network syscalls **really
refused** — any hidden network call fails loudly. A final check
attempts a real outbound connection and asserts it is refused.

`run_offline_selftest()` — **13/13 passed** (re-run for this report):

| # | Check | What it proves |
|---|---|---|
| 1 | `voice_grammar` | "open firefox" → deterministic OPEN intent, no network |
| 2 | `voice_command_mode` | engine COMMAND mode: "volume up" → VOLUME intent |
| 3 | `voice_dictation` | DICTATION mode commits buffered text ("hello there") |
| 4 | `voice_context` | "click that" resolves against the context engine's gaze target |
| 5 | `gesture_custom` | user-defined 3-step sequence fires a HOTKEY intent |
| 6 | `rf_bridge` | simulated RF provider → bus events (rf_gesture) |
| 7 | `browser_bridge` | simulated bridge produces element state offline |
| 8 | `browser_semantic` | "click the login button" resolves to click + element |
| 9 | `browser_execute_verify` | execute + before/after verification = passed |
| 10 | `fusion_pipeline` | full InteractionAgent frame: fusion → intent → action executed |
| 11 | `event_bus` | publish/poll stats flow correctly |
| 12 | `offline_gate` | engaged gate blocks `cloud_asr`, passes `local_grammar` |
| 13 | `network_actually_blocked` | a real `socket.create_connection(("example.com", 80))` is refused |

`airmouse offline-test` runs the same selftest from the CLI.

## 4. Performance (this environment; budgets enforced by tests)

| Metric | Measured | Budget |
|---|---|---|
| Command grammar, 50 utterances | ~5 ms | < 0.5 s |
| OfflineVoiceEngine, 30 transcripts | ~3 ms | < 0.5 s |
| EventBus, 1000 publishes | ~2 ms | < 0.5 s |
| ContextEngine, 1000 resolves | ~4 ms | < 0.2 s |

Measured with wall-clock timing around the exact test-suite workloads
(single run, this sandbox; budgets are asserted in
`tests/test_v10.py` — all pass with ≥ 50× headroom).

v9 performance numbers remain valid and unchanged (hand filter
0.040 ms/call, gaze filter+map 0.011 ms, full agent tick 0.032 ms,
NL parse 0.0055 ms — see the v9 report sections preserved in git).

## 5. Security / privacy audit summary

- **No `shell=True` anywhere** (re-scanned for v10): all subprocess
  calls are fixed-argv lists with timeouts; user-derived strings are
  passed as arguments, never interpolated into a shell line.
- **Allowlists enforced:** 16 SYSTEM_OPS + 8 FILE_OPS are the only
  system/file operations that execute; file paths must resolve inside
  allowlisted base roots (traversal and outside-root paths refused);
  `sanitize_file_name` strips hostile components; `validate_url`
  accepts http/https/file schemes only.
- **Browser untrusted-content policy:** page-derived text is DATA used
  only for matching against our fixed template grammar — it can never
  become a command; the CDP adapter evaluates only fixed JavaScript
  snippets built from our own parameters (JSON-encoded); the bridge
  server parses payloads with `json.loads` and stores them as plain
  dicts; oversized (> 256 KB) or invalid payloads rejected (413/400);
  the server is hard-bound to 127.0.0.1 (never 0.0.0.0).
- **Offline gate** can hard-block cloud ASR/TTS/CDP/update/telemetry
  paths; §3 proves the stack runs with sockets actually refused.
- **Safety intact:** e-stop latch, sliding-window rate limiter, click
  cooldowns, confidence gates, one-shot confirmation flow, and the
  no-auto-retry rule for sensitive/blocked actions are all covered by
  both the preserved v8/v9 suites and the new §18 tests; destructive
  ops (shutdown/restart/lock/sleep/close_tab/destructive
  FILE_OP/SYSTEM_OP params) require explicit confirmation.
- No `eval`/`exec`; no secrets/tokens in tracked files (scanned);
  OCR remains opt-in; no outbound telemetry. The extension masks
  password fields and only talks to 127.0.0.1:17843.

## 6. Hardware verification status — EXPLICIT

**No physical webcam, microphone, or RF hardware exists in the build
environment. Nothing below claims physical verification.**

| Area | Status | What is verified |
|---|---|---|
| All v10 logic | **SIMULATION-VERIFIED** | deterministic simulators: scripted transcripts, scripted RF events, simulated browser bridge with per-tab history, simulated providers — 630 tests |
| Camera / eye-tracking paths on real hardware | **HARDWARE-UNVERIFIED** | MediaPipe FaceMesh smoke (blank frame) from v9 remains true; no camera in sandbox |
| Microphone / audio-device paths on real hardware | **HARDWARE-UNVERIFIED** | VAD/wake-word/dictation logic verified with synthetic buffers; no mic in sandbox |
| PocketSphinx / Vosk / Whisper offline ASR | **CODE PRESENT, UNVERIFIED LIVE** | guarded imports + `detect_providers()` auto-detection; in the sandbox none are installed (`pocketsphinx/vosk/whisper: False`), so the **transcript-injection path is what is verified** |
| CDP bridge against a real Chrome/Edge | **UNVERIFIED** | guarded, stdlib-only implementation; tests cover graceful unavailability only |
| MV3 extension loaded in a real browser | **UNVERIFIED** | source ships and is lint-reviewed; bridge-server round-trip verified with a plain HTTP client |
| RF hardware | **UNVERIFIED** | provider protocol + simulated/dummy providers verified; hardware idles cleanly by design |

Recommended first real-machine checks: `airmouse voice-status`
(which ASR engines are detected), `airmouse --gaze-calibrate`,
`airmouse --browser` with a CDP-enabled Chrome, and
`airmouse offline-test`.

## 7. Packaging

- `pyproject.toml` at version **10.0.0**; package metadata, entry
  point (`airmouse …`) and long-description unchanged in shape; the
  wheel now additionally ships the 9 new v10 modules and the
  `browser_extension/` source.
- Module inventory verified: 41 modules under `airmouse/`, all
  importable headless with networking disabled (stdlib-only core).
- `airmouse --version` → `10.0.0 — Universal Offline Interaction
  Edition`; all v10 flags and subcommands registered
  (`--help` inspection).
- `browser_extension/` ships with its own README (unpacked-load
  instructions for chrome://extensions).
- Wheel build/release (tag, GitHub Release) remains a release step,
  to be performed from a tree with this report.

## 8. Regression gate

- All **497** pre-existing v9 tests pass **unchanged** — zero
  modifications to existing test files; v10 additions are confined to
  two new files (`tests/test_browser.py`, `tests/test_v10.py`).
- v5 behaviors preserved and re-proven by the regression suite: 14
  gestures + swipes, hybrid One Euro + Kalman, 30 voice commands +
  turbo, pinch-zoom, adaptive calibration, v1 macros, HUD.
- v9 behaviors preserved: fusion modes, gaze stack, screen
  perception, intent/action/verification/safety, macros v2, NL
  control, hands-free, agent E2E, failure modes (camera loss, face
  lost, mic loss → SAFE_MODE), performance benchmarks.
- Product-bug fixes (see CHANGELOG "Fixed") were made *after* the new
  suite exposed them; their tests moved from xfail to passing, giving
  the final 630/0/0/0.

## 9. Known limitations (honest)

- **Simulation-first:** every claim above is simulation-verified;
  physical webcam/mic/RF behavior, live offline-ASR engines, real-Chrome
  CDP, and the MV3 extension in a real browser are **unverified**
  (§6). The code is guarded and degrades gracefully, but real-hardware
  tuning (e.g. `gaze_min_confidence`, VAD thresholds) is expected.
- Webcam gaze accuracy (~1–3°) remains a targeting aid (v9 design,
  unchanged); confirmation patterns compensate.
- Accessibility/DOM providers remain platform-dependent best-effort;
  the geometry fallback always works.
- The command grammar is intentionally a fixed template grammar (75
  commands) — not free-form NLU; the v9 NL pattern table remains the
  second local layer. No LLM, by design.
- RF sensing requires third-party hardware implementing the provider
  protocol; without it the modality simply does not exist.
- `airmouse_simple.py` intentionally remains the v5 single-file mode.
