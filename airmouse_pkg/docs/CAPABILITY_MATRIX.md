# Capability Matrix — AirMouse v15.1.0

**Measurement caveat (read first):** every "State" below was **measured
on a Linux headless sandbox** (no display, no webcam, no microphone, no
Chrome) by running the real commands shown in the last column on the
real v15.1.0 tree. It is NOT a Windows measurement. The middle column
is the honest state on that sandbox; the third column is what the same
command is expected to report on **Windows 10/11 with a webcam and a
microphone** — that expectation is derived from the code paths and the
doctor's own remediation strings, **not from a Windows run**. Physical
hardware behaviour (webcam frames, microphone audio, hand tracking,
gaze, real browser automation) was **NOT TESTED** in the sandbox and
can never be auto-verified — validate it on your machine with
`airmouse test --guided` and `WINDOWS_REAL_WORLD_TEST.md`.

Environment measured: Linux x86_64 (kernel 5.10), Python 3.12.14,
numpy 2.1.3, opencv-python 5.0.0, mediapipe 0.10.35, pynput 1.8.2
(present, no display), sounddevice 0.5.6, pyttsx3 present,
pytesseract 0.3.13. AirMouse 15.1.0. Storage isolated via
`AIRMOUSE_HOME=/tmp/am-docs`.

Reproduce with:

```
cd airmouse_pkg
mkdir -p /tmp/am-docs
AIRMOUSE_HOME=/tmp/am-docs python3 -m airmouse doctor --json
AIRMOUSE_HOME=/tmp/am-docs python3 -m airmouse verify
```

---

## 1. Capability table

State names are the doctor's own `ComponentState` values: READY,
OPTIONAL, NOT_INSTALLED, UNAVAILABLE, HARDWARE, ENHANCEMENT, WARNING,
FAILED. `PHYSICAL` in the expected column means: needs you at the desk
(`airmouse test --guided`), never auto-passes.

| Capability | State (Linux headless sandbox, measured) | Expected on Windows 10/11 + webcam + mic | How to validate (command) |
|---|---|---|---|
| **Core** (package import, CLI, config, storage home) | **READY** — package v15.1.0, config loads, storage writable | READY | `airmouse doctor` |
| **MediaPipe** (hand-landmark runtime) | **READY** — 0.10.35; model file present (7.5 MB at `~/.airmouse/hand_landmarker.task`) | READY (model downloads once on first run; needs internet that one time) | `airmouse doctor` (PYTHON + "Hand tracking model" rows) |
| **OpenCV** | **READY** — opencv-python 5.0.0 | READY | `airmouse doctor` |
| **pynput input control** (cursor/keyboard automation) | **UNAVAILABLE** — no X display on the sandbox (honest, expected headless); action executors show ENHANCEMENT "MockExecutor active — actions are simulated" | READY out of the box (doctor's own fix text: "on Windows it works out of the box — just run `airmouse`") | `airmouse doctor` (INPUT section) |
| **Voice engine** (grammar + offline voice) | **READY** — 75-command grammar; command+dictation modes functional; streaming transcription roundtrip ok | READY (grammar needs no mic; speaking needs Steps 6/12 of WINDOWS_REAL_WORLD_TEST.md) | `airmouse voice-status`, `airmouse commands`, `airmouse doctor` (SPEECH) |
| **Local ASR** (pocketsphinx / vosk / whisper) | **NOT INSTALLED** — optional extra, adapters exist, engines not bundled | OPTIONAL until you install one (`python -m pip install pocketsphinx`) — then real mic→text offline | `airmouse voice-status`, `airmouse doctor` (PYTHON "Optional:" rows) |
| **Webcam** (camera device) | **HARDWARE** — "no camera device answered at index 0" (headless truth, never faked) | READY once a webcam is connected and Windows camera privacy allows desktop apps — actual frames: PHYSICAL | `airmouse doctor` (CAMERA), `airmouse test --guided` test [2/12] |
| **Gaze** (webcam eye tracking + calibration) | software READY (calibration/filter paths simulated-verified); device **HARDWARE** absent | READY after `airmouse --gaze-calibrate`; accuracy: PHYSICAL | `airmouse --gaze-calibrate`, `airmouse test --guided` test [4/12] |
| **Browser bridge — simulated** (deterministic, offline) | **READY** | READY | `airmouse verify` |
| **Browser bridge — extension sink** (localhost-only, default port 17843) | **READY** | READY | `airmouse doctor` (BROWSER) |
| **Chrome / Edge detection** (fixed-path scan) | **OPTIONAL** — "no Chrome/Edge found in the standard install locations" (none installed) | READY when Chrome or Edge is installed in a standard location | `airmouse doctor` (BROWSER), `airmouse browser` |
| **Real browser CDP control** (Chrome/Edge `--remote-debugging-port=9222`) | **NOT TESTED** — no browser and no network in the sandbox; bridge code is simulation-verified | READY with Chrome/Edge started with the debug flag; real-page clicks: PHYSICAL | `airmouse browser` after starting Chrome with the flag; `WINDOWS_REAL_WORLD_TEST.md` Step 15 |
| **Intelligence** (twin + vocabulary + skills + stores) | **READY** — twin v1 + PersonalVocabulary + PersonalSkillLibrary; plugin state=available | READY | `airmouse intelligence`, `airmouse twin`, `airmouse doctor` (INTELLIGENCE) |
| **Memory / persistence** (crash-safe local stores) | **READY** — 5 stores (twin, vocabulary, skills, workflows, preferences), atomic writes, schema versioning, checksum quarantine/recovery | READY (`%USERPROFILE%\.airmouse`, override with `AIRMOUSE_HOME`) | `airmouse memory status` |
| **Skills** (InteractionCompression + PersonalSkillLibrary) | **READY** | READY | `airmouse skills`, `airmouse test --guided` test [7/12] |
| **AIP** (Agent Interaction Protocol 1.0) | **READY** — validator fail-closed ok (malformed envelopes rejected) | READY | `airmouse protocol`, `airmouse verify` |
| **Agent SDK** (Python facade; JS/TS + agent-core ship alongside) | **READY** — `agent_sdk` importable, AirMouse facade over AIP v1.0 | READY | `airmouse doctor` (AGENT), `docs/AGENT_SDK.md` snippet |
| **Multi-agent** (registry, exclusive leases, conflict resolution, e-stop) | **READY** — lease held / challenger refused / handoff / `emergency_stop_all` verified in-process | READY | `airmouse agents`, `airmouse test --guided` test [10/12] |
| **Recovery** (7 strategies, 14 diagnoses, bounded rounds) | **READY** — simulated failure drills recover; permission-denied correctly never retried | READY | `airmouse test --guided` test [11/12] |
| **Offline** (OfflineGate + network-isolation selftest) | **READY** — 18/18 checks with networking blocked | READY | `airmouse offline-test`, `airmouse test --guided` test [12/12] |
| **RF** (RF-sensing modality) | **OPTIONAL, NOT IMPLEMENTED HARDWARE** — abstraction + simulated provider only; no RF hardware is claimed or required | Same (simulated provider; real RF hardware is out of scope) | `airmouse offline-test` (`rf_bridge` check), `airmouse --rf` |
| **TTS** (spoken confirmations, pyttsx3) | **READY (present)** — optional extra installed in sandbox; no audio device to speak through | READY; voice quality depends on Windows voices | `airmouse doctor` (PYTHON "Optional: pyttsx3"), `python -m pip install "airmouse[tts]"` |

Rows whose expected-on-Windows column says PHYSICAL are exactly the
five checks `airmouse verify` always reports as ACTION_REQUIRED:
webcam, microphone, hand tracking, gaze, real browser.

---

## 2. Measured output — `airmouse doctor` (plain)

Captured verbatim on the sandbox (OpenCV's camera-probe warnings on
stderr are expected on a machine with no camera and are not part of
the report):

```
AIRMouse Doctor
===========================

READY:       32
OPTIONAL:    7
HARDWARE:    2
WARNING:     0
FAILED:      0

Overall:
[READY FOR TESTING]
```

Note: the READY/WARNING split depends on one file — when the
hand-landmarker model has not been downloaded yet, "Hand tracking
model" reports WARNING (i.e. READY 31 / WARNING 1); with the model
present it is READY. Both states are honest and expected.

Non-READY components (verbatim from `doctor --json`, all others READY):

```
UNAVAILABLE    PYTHON       Dependency: pynput     | ImportError: ... failed to acquire X connection (headless)
NOT_INSTALLED  PYTHON       Optional: SpeechRecognition | optional extra not installed -> pip install "airmouse[voice]"
NOT_INSTALLED  PYTHON       Optional: pyaudio      | optional extra not installed -> pip install "airmouse[voice]"
HARDWARE       CAMERA       Webcam                 | no camera device answered at index 0
HARDWARE       MICROPHONE   Microphone             | PortAudio works but reported no input devices
OPTIONAL       SPEECH       Transcription providers| simulated provider only (deterministic, offline)
UNAVAILABLE    INPUT        pynput input control   | (same display reason)
ENHANCEMENT    INPUT        Action executors       | MockExecutor active — actions are simulated on this machine
OPTIONAL       BROWSER      Chrome / Edge installation | no Chrome/Edge found in the standard install locations
```

Section sizes: SYSTEM 6, PYTHON 11, AIRMOUSE 4, CAMERA 1, MICROPHONE 1,
SPEECH 4, INPUT 2, BROWSER 3, INTELLIGENCE 2, AGENT 4, OFFLINE 1,
SAFETY 2 = **41 components**.

`airmouse doctor --json` exits 0 and emits the machine report
(`{"version": "15.1.0", "components": [...]}`; each component has
name/category/state/detail/remediation). Excerpt, verbatim:

```json
{
 "version": "15.1.0",
 "components": [
  {"name": "Operating system", "category": "SYSTEM", "state": "READY",
   "detail": "Linux-5.10.134-...-x86_64-with-glibc2.41", "remediation": ""},
  {"name": "Dependency: mediapipe", "category": "PYTHON", "state": "READY",
   "detail": "0.10.35", "remediation": ""},
  {"name": "Dependency: pynput", "category": "PYTHON", "state": "UNAVAILABLE",
   "detail": "ImportError: this platform is not supported: (... Bad display name \"\" ...)",
   "remediation": "run on a machine with a display; see docs (docs/USER_GUIDE.md — input permissions)"},
  {"name": "Hand tracking model", "category": "AIRMOUSE", "state": "READY",
   "detail": "/home/z/.airmouse/hand_landmarker.task (7.5 MB)", "remediation": ""}
 ]
}
```

(exit code 0 = READY FOR TESTING; 1 = PARTIAL; 2 = BLOCKED.)

## 3. Measured output — `airmouse verify`

Captured verbatim on the sandbox:

```
AIRMouse Verification
=====================

Automated:
  [           PASS] Core — airmouse v15.1.0 imported
  [           PASS] Voice — grammar + offline engine deterministic (OPEN/VOLUME)
  [           PASS] Intelligence — twin v1 + PersonalSkillLibrary import; learn('open_app'→'click') verified
  [           PASS] Safety — gates + e-stop latch + reset ok; hierarchy EMERGENCY_STOP..PREDICTION; 4 safety levels
  [           PASS] Offline — offline selftest: 18/18 checks passed, overall=OK
  [           PASS] Browser simulator — SimulatedBrowserBridge available (deterministic)
  [           PASS] Agent permissions — unknown agent + destructive key denied by default
  [           PASS] Agent leases — exclusive lease held; challenger refused; release works
  [           PASS] AIP validator — malformed AIP envelopes rejected (fail-closed)
  [           PASS] Packaging — pyproject version matches package (15.1.0)

Physical:
  [ACTION_REQUIRED] Webcam — look at the camera preview: is your face visible and well lit?
  [ACTION_REQUIRED] Microphone — speak a command (e.g. 'open browser') and confirm the transcript appears
  [ACTION_REQUIRED] Hand tracking — show an open hand, then pinch — the cursor must follow and click
  [ACTION_REQUIRED] Gaze — run airmouse --gaze-calibrate, then dwell on a target to click with your eyes
  [ACTION_REQUIRED] Real browser — install Google Chrome or Microsoft Edge, start it with --remote-debugging-port=9222 and run a browser command

Next step:
Run:

airmouse test --guided
```

(exit code 0 — no automated FAILs; the 5 ACTION_REQUIRED rows are the
physical tests handed to the user.)

## 4. Measured output — memory + privacy (storage layer)

```
$ AIRMOUSE_HOME=/tmp/am-docs python3 -m airmouse memory status
  local memory stores — /tmp/am-docs
    twin         empty   schema=vNone records=0
    vocabulary   empty   schema=vNone records=0
    skills       empty   schema=vNone records=0
    workflows    empty   schema=vNone records=0
    preferences  empty   schema=vNone records=0
  lifecycle: airmouse memory export|reset|delete (local-only; nothing leaves this machine)
```

```
$ AIRMOUSE_HOME=/tmp/am-docs python3 -m airmouse privacy
AirMouse Privacy — LOCAL-FIRST / OFFLINE BY DEFAULT
  telemetry state: {'enabled': False, 'default': 'off', 'default_in_code': True, ...}
  network state: {'posture': 'local-only', 'cloud': False, 'telemetry_upload': False,
                  'offline_gate_features': ['browser_cdp', 'cloud_asr', 'cloud_tts',
                                            'software_update', 'telemetry_upload']}
  model state: {'kind': 'on-device PersonalInteractionModel', 'available': False, ...}
  ...
  controls:
    ['airmouse memory status', 'airmouse memory export <path>', 'airmouse memory reset',
     'airmouse memory delete', 'airmouse privacy']
```

(`...` marks lines elided for length — telemetry OFF by default,
`default_in_code: True`, is the load-bearing fact and is verbatim.)

## 5. Measured performance (same sandbox)

| Command | Wall time (measured) | Note |
|---|---:|---|
| `python -m airmouse --version` | ≈ 1.5 s | dominated by the one-time import of numpy/cv2/mediapipe |
| `python -m airmouse doctor` | ≈ 2.6 s | includes a real camera probe (~2 s) |
| `python -m airmouse verify` | ≈ 1.6 s | 10 automated checks |
| `python -m airmouse test` | ≈ 1.6 s | 12-test lab, non-interactive |

Pinned budgets in `tests/test_release_perf.py`: `--version` 6.0 s,
`doctor` 12.0 s, `verify` 8.0 s, `test` 8.0 s.

## 6. Honesty summary

- AUTOMATED VERIFICATION PASS: 1236 tests, verify 10/10, offline 18/18.
- SIMULATION PASS: guided lab 7/7 simulation tests; browser, agent,
  multi-agent, recovery, RF, transcription-provider paths.
- PHYSICAL HARDWARE NOT TESTED: webcam, microphone, real hand
  tracking, real gaze, real browser CDP — ACTION_REQUIRED by design.
- NOT TESTED: Windows runtime (this matrix was measured on Linux),
  real local ASR engines, real RF hardware, cloud integrations
  (none exist by design).
