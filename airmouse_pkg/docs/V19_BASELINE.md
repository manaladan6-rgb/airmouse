# AirMouse v19 BASELINE — Phase 0 Audit (measured, not assumed)

Date: 2026-10-12 · Environment: Linux sandbox, CPython 3.12.14 (uv venv), no camera, no microphone, no display, GitHub push NOT possible from sandbox (no credentials — same as every prior session).

---

## §1 GIT BASELINE (measured live)

| Item | Value |
|---|---|
| Fresh clone HEAD | `0b26072` = tag `v16.5.3` = `origin/main` |
| Tags on remote | `v16.5.3` … `v3.1.0` (v16.5.3 is the newest) |
| Working tree | clean |

**CRITICAL DISCREPANCY (2nd occurrence):** the previous session produced
v16.5.4 (`model_integrity`), v17.0.0R (`omnisense`) and v18.0.0 ADAPTIVE HCI
(commits `c908d33`, `df4c215`, `9534f2f`, `2ca944d`, `c9e7ccf`, `c8c89ce`,
tags `v16.5.4R`, `v17.0.0R`, `v18.0.0`) — but the sandbox could not push and
was reset. **None of that history exists in the repository.** The shared
worklog preserved the specs; the code is gone.

Consequences for v19, honestly applied:

- v19 builds on what actually exists: **v16.5.3** (95 modules, 55 test files).
- The v18 subsystems the v19 mission *depends on* are **reconstructed from the
  recorded specs** in this release (see §4): model integrity pipeline,
  capability registry, degradation ladder, evidence vocabulary.
- The full v17/v18 research layer (omnisense fusion internals, RF ladder,
  world-bridge waits, head organ, identity, personalization learner, fault
  injection) is **NOT reconstructed in v19** and is reported as such in the
  capability matrix. Rebuilding it is future work; pretending it exists is
  forbidden by §0.

## §2 TEST BASELINE (measured live, CPython 3.12.14)

| Battery | Result |
|---|---|
| `pytest tests/` | **2027 passed / 2 skipped / 0 failed** |
| `airmouse verify` | 12/12 PASS (action-required: real browser last mile) |
| `airmouse offline-test` | 18/18 checks, overall OK |
| `airmouse doctor` | 29 READY / 10 OPTIONAL / 2 HARDWARE / 1 WARNING / 0 FAILED |
| Environment gap fixed during audit | `SpeechRecognition` + `sounddevice` missing → installed → the 1 red test green (logged as B-ENV-1, same gap the v18 session hit) |

## §3 P0 DEFECT FOUND BY THE AUDIT — WRONG MODEL PIN (live-reproduced)

`voice_bootstrap.py` still pins the official Vosk model as
`507575b9…` / 41,205,932 B (the v16.5.3 pin the v16.5.4 session proved wrong).

Re-verified live **in this session** (4th independent measurement, 2nd
complete download in project history):

| Property | v16.5.3 pin (repo) | MEASURED live (alphacephei.com) |
|---|---|---|
| Content-Length | 41,205,932 | **41,205,931** |
| SHA256 | `507575b9efd23f96…` | **`30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498`** |
| SHA512 | (absent) | **`d56243ba75343fd4…f25504bce87495b6f448089a87b426a8d81b31b80525ae5ed6c0ca4fb95a306576f53bc5a`** |
| Members | — | 20, single root `vosk-model-small-en-us-0.15/`, no executables, no symlinks |

Cross-checked against the independently published Alpine aports digest and the
v16.5.4 session's streamed measurement — all agree. **Impact:** every consented
model download by a fresh user fails SHA256 verification → the flagship
"voice works in 5 minutes" flow is broken at the trust-anchor level. Fix
lands in v19 §4.1 with the reconstructed integrity pipeline.

## §4 WHAT v18 PROMISED vs WHAT EXISTS (honest delta)

| v18 subsystem | Exists in repo? | v19 action |
|---|---|---|
| `model_integrity.py` 12-stage pipeline | NO (lost) | **RECONSTRUCT** (deployment-critical; fixes P0) |
| Model states (MISSING/DOWNLOADING/VERIFYING/READY/CORRUPTED/INCOMPATIBLE) | NO | **RECONSTRUCT** |
| Capability registry (runtime discovery) | NO | **RECONSTRUCT** over v16.5.3 `capabilities.detect_all` |
| Degradation ladder (InteractionReadiness) | NO | **RECONSTRUCT** (Phase 10 depends on it) |
| Evidence vocabulary (REAL/SIMULATED/OPTIONAL/PHYSICAL_TEST_REQUIRED/NOT_AVAILABLE) | partial (`CAPABILITY_MATRIX.md`, doctor states) | **UNIFY** as code |
| omnisense fusion / RF ladder / world waits | NO | NOT reconstructed — documented NOT_AVAILABLE |
| head organ / identity / arbiter / prediction-execution split | NO | NOT reconstructed (safety.py hierarchy + permission gates already exist in v16.5.3 and remain the spine) |

## §5 DEPLOYMENT AUDIT — the actual v19 mission surface

Measured against the 5-minute user journey:

**Works today**
- `pip install airmouse` is a valid install path (PyPI metadata sound: deps are
  wheels-only friendly on Windows: mediapipe/opencv/pynput/numpy).
- `airmouse` (no args) shows a first-run menu instead of a camera window.
- `airmouse setup` (11 honest steps, consent-gated installs only).
- `airmouse doctor` (+ `--verbose`, `--json` via `detect_all().to_machine()`).
- `airmouse voice-setup` bootstrap (consent-gated, honest) — **but the model
  pin is broken (§3)** and the pipeline lacks the v16.5.4 hardening.
- `~/.airmouse` home exists with profile/models/transcripts/macros/backups/
  exports/intelligence + loose artifacts (paths.py single source of truth).
- Zero-config defaults exist and work (Config class defaults, auto-created
  config.toml; `setup --repair-config` for broken files).

**Gaps (the v19 work list)**
1. **No model integrity pipeline / states** — P0 pin bug + no
   MISSING/DOWNLOADING/VERIFYING/CORRUPTED semantics (Phase 6).
2. **Doctor is developer-shaped** — no human "what can I do" summary, no
   advanced/json split of the run_doctor report, no interaction-readiness
   verdict (Phase 7).
3. **Setup doesn't finish the job** — no model acquisition, no capability
   adaptation, no first-profile creation, no onboarding handoff, no timing
   report, no rollback story (Phases 5/8/18).
4. **No `airmouse repair` / `reset` / `uninstall`** (Phase 13).
5. **No update path** (`update --check` honest/offline-safe) (Phase 16).
6. **No `airmouse capabilities`** user-facing registry view (Phase 10/23).
7. **First-run menu is dev-flavored** ("Offline Test", "Intelligence",
   "Agent") — no status dots, no launcher panel (Phase 4).
8. **Home layout missing logs/ cache/ skills/ diagnostics/** (Phase 11).
9. **No Windows distribution** beyond wheel/sdist — no bootstrap script, no
   exe (Phase 3) — exe is impossible from this Linux sandbox: documented
   honestly with a reproducible CI spec instead of a fake claim.
10. **No QUICKSTART/INSTALL/TROUBLESHOOTING/PACKAGING docs**; README lacks
    the instant answer block (Phase 21).
11. **No measured 5-minute benchmark** (Phase 1/19).
12. **No packaging adversarial tests** (Phase 17/24).

## §6 v19 SCOPE DECISION (recorded up-front)

- **Primary distribution (Phase 3 decision): Option C** — Python package +
  automatic bootstrap (`pip install airmouse` + `airmouse setup`), because it
  is the only option implementable and verifiable from this environment
  without faking results. A signed/exe installer is specified (PACKAGING.md)
  as PHYSICAL_TEST_REQUIRED future work requiring real Windows CI.
- v18-foundation reconstruction (§4 rows marked RECONSTRUCT) lands as a
  prerequisite milestone, tested to the same adversarial standard, tagged in
  history — not silently merged into a "v19 rewrite".
- Everything else in the v19 phase list is implemented against v16.5.3
  reality: no behavior invented for subsystems that do not exist.

— End of baseline. Every number above was measured in this session.
