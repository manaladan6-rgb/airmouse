# AirMouse v16.5.1 — RELEASE MANIFEST

**Release:** v16.5.1 — WINDOWS REALITY & TEACHING UX HOTFIX
**Date:** 2026-09-05
**Tag:** `v16.5.1`
**Base:** v16.5.0 (7d93b35)

## Artifacts

| File | Size (B) | SHA256 (in SHA256SUMS) |
|---|---|---|
| airmouse-16.5.1-py3-none-any.whl | 642841 | (in SHA256SUMS_v16.5.1.txt) |
| airmouse-16.5.1.tar.gz | 606349 | (in SHA256SUMS_v16.5.1.txt) |
| SHA256SUMS_v16.5.1.txt | 188 | — |

Verify:

```
sha256sum --check SHA256SUMS_v16.5.1.txt
```

## What shipped (§ references = the v16.5.1 task order)

1. **§1/§2/§3 — the one UTF-8 encoding contract (P0)**: new
   `airmouse.fileio` (strict UTF-8 reads, atomic UTF-8-no-BOM writes,
   `probe`, repair-only best-effort decode).  Root cause of the real
   Windows error `'utf-8' codec can't decode byte 0x97 in position 30`
   was `open(path, "w")` (cp1252 process default) on the config
   writer; the byte and position were reproduced exactly, fixed, and
   locked with round-trip tests (ASCII / Unicode names / dashes /
   smart quotes / emoji; fresh / existing / malformed / BOM /
   invalid-TOML / repair / idempotent-repair).
2. **§2 — never silently fall back**: broken configs record a
   structured problem and print a LOUD WHAT/WHY/FIX block; the user's
   file is never modified by the load path.
   **`airmouse setup --repair-config`**: backup (byte-for-byte) →
   repair → validate → canonical UTF-8 → exact report.
3. **§4/§15 — actionable doctor**: WHY/FIX on every WARNING/FAILED;
   CONFIG check distinguishes no-file / valid-UTF-8 / decode / syntax;
   hardware capability labels (`[ACTIVE]`, `[MICROPHONE DETECTED]`,
   `[NOT INSTALLED]`, `REQUIRES CALIBRATION`…); new Gaze subsystem row.
4. **§5/§6/§7/§9/§10 — Voice Academy**: 7 progressive lessons; REAL
   microphone practice through the shipped pipeline (mic → ASR →
   transcript → matcher → intent) when hardware exists; honestly
   labelled TEXT PRACTICE otherwise; VOICE HARDWARE panel +
   Local Voice Pack offer (never auto-installs);
   `Answer revealed. Lesson not completed.` / `Skipped — not
   completed.` semantics.
5. **§8 — MIC LIVE transcription**: automatic real-mic mode with
   partial/final + confidence + timestamps; text-REPL fallback with
   honest banners; no audio ever stored.
6. **§13 — quiet**: third-party log noise (MediaPipe/Clearcut/TFLite/
   absl) suppressed where safe; AirMouse errors untouched.
7. **§14 — first-run**: welcome lists Personalization; READY panel
   shows per-modality capability states.

## Measured on the release tree (headless Linux sandbox)

- `pytest tests/` → **1957 passed / 1 skipped / 0 failed** (~23 s)
  — baseline 1890/1/0, +67 tests, zero regressions.
- `airmouse verify` → **12/12 automated PASS** + 5 honest
  ACTION_REQUIRED physical rows.
- `airmouse --version` → `AirMouse v16.5.1 …`.
- Clean-venv wheel-only install: `airmouse --version`, `doctor`
  (READY FOR TESTING), `setup --repair-config`, `transcribe`
  (honest simulated/text banners) — all exercised from the wheel.
- Security: py_compile on changed modules; grep audit (eval/exec/
  os.system/shell=True/pickle/__import__) clean; repair never
  executes content; nothing auto-installs.

## NOT tested here (honest)

Real Windows + physical hardware — handed over as
`WINDOWS_REAL_WORLD_TEST.md` Steps **16e–16h** (repair drill, voice
academy REAL/TEXT with a real mic, MIC LIVE transcription, doctor
states) on top of the historical Steps 1–17. Simulated PASS never
became hardware PASS.

## SHA256SUMS (committed at release/SHA256SUMS_v16.5.1.txt)

```
3af2edc5c2cc3ba82ef1584607a1d189f3800711275f7825b2d89c9484259cbe  airmouse-16.5.1-py3-none-any.whl
bc18bb301b44c593f40a1dd72cbae85e5ef9adaa775814d9d75385149073ad92  airmouse-16.5.1.tar.gz
```
