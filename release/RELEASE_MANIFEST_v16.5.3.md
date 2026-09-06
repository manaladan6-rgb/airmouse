# Release Manifest — AirMouse v16.5.3

**Title:** AirMouse v16.5.3 — Windows Native ASR Bootstrap & Zero-Compiler Voice Runtime
**Tag:** `v16.5.3` (annotated, on `main`)
**Artifact:** `airmouse-16.5.3-py3-none-any.whl` (681,771 bytes)
**SHA256:** `30ba4102d2c53a59238c1254d7a447235d0d2af0579021cf7bc6109749a60933`
**Install (one command, no tools beyond pip):**

```powershell
pip install --user "https://github.com/manaladan6-rgb/airmouse/releases/download/v16.5.3/airmouse-16.5.3-py3-none-any.whl"
```

## What ships

- `airmouse voice-setup` — the zero-compiler local voice bootstrap
  (§8/§9): deterministic wheel-compatibility matrix, `pip
  --only-binary :all:` installs ONLY (a CMake/NMake/MSVC source build
  is structurally impossible), consent-gated official model fetch
  (vosk small en-US 41,205,932 bytes, SHA256
  `507575b9efd23f96ad2e9c2f226a8b9b50ca1cd894e2b00e7f07b170ffe6b00a`),
  Zip-Slip-safe extraction, verify + smoke test + honest failure panel.
- `ASRBootstrapManager` (`voice_bootstrap.py`) with explicit bootstrap
  states (§15) and the `NATIVE_ASR_PACKAGE_UNAVAILABLE` diagnosis.
- vosk model resolution (explicit > env `AIRMOUSE_VOSK_MODEL_PATH` >
  config `[voice] vosk_model_path` > AirMouse `models/vosk/`) and the
  honest `MODEL MISSING` registry state — the registry and the engine
  can never disagree again.
- Bootstrap-aware doctor / voice-status FIX lines (never a bare
  `pip install pocketsphinx` on machines where it cannot work).
- `airmouse voice-benchmark` — deterministic local timing/footprint;
  recognition quality only with real speech (`--wav`).
- All v16.5.2 systems unchanged: ONE authoritative ASR registry,
  sounddevice-first mic capture, MIC LIVE transcribe, TEXT PRACTICE,
  honest SIMULATED banners, safety spine, gesture/gaze/intelligence/
  agent/AIP/teaching systems.

## Gates (measured)

| Gate | Result |
|---|---|
| Full suite (CPython 3.12) | **2027 passed / 2 honest skips / 0 failed** |
| Full suite (CPython 3.13) | **2028 passed / 1 honest skip / 0 failed** |
| `airmouse offline-test` | 18/18 PASS (network really blocked) |
| `airmouse verify` | 12/12 automated PASS (exit 0) |
| Clean-venv wheel battery | import 16.5.3 OK; voice-status honest; `voice-setup --check-only` correct plan (pocketsphinx NOT FOUND / vosk FOUND on cp312) |
| Real-speech end-to-end (offline, sandbox) | "open browser" → exact transcript → OPEN intent (`app: browser`); "hello bro this is a real AirMouse voice test" → recognized (~0.8 s); benchmark 0.37 s first result / 268 MB peak RAM |
| PocketSphinx on CPython 3.13 | official cp313 wheel installs; real Decoder init PASS |
| Security scan | no eval/exec/os.system/shell=True/curl-pipe in new code; official sources only; SHA256 + Zip-Slip + executable-refusal tests green |

## Honest limits

- The sandbox has NO Windows and NO microphone. The final physical
  drill (`airmouse voice-setup` → `airmouse voice-status --verbose` →
  `airmouse transcribe` + three spoken phrases) is ACTION_REQUIRED for
  the user — see `WINDOWS_REAL_WORLD_TEST.md` Step 16j.
- vosk-small accuracy on accents/noise is modest by design
  (lightweight, 41 MB model); it is a REAL local engine, not a
  accuracy claim.
