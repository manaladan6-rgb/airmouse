# AirMouse v19 ARCHITECTURE — INSTANT DEPLOYMENT

v19 is a **deployment-layer release** built on the v16.5.3 reality of
this repository. It does not add a new sensory organ or a new fusion
engine; it removes every obstacle between a human and a working
AirMouse — without weakening the safety spine, the privacy
architecture, or the evidence discipline.

---

## §1 The v19 layers (and what they sit on)

```
┌──────────────────────────────────────────────────────────────────┐
│  USER SURFACE (v19)                                              │
│  launcher (cli_menu)  ·  wizard  ·  setup 2.0  ·  doctor 2.0     │
│  capabilities  ·  reset/repair/uninstall/update (lifecycle)      │
├──────────────────────────────────────────────────────────────────┤
│  HONESTY LAYER (v19 reconstruction of the lost v18 concepts)     │
│  capability_registry  ·  degradation ladder  ·  evidence model   │
├──────────────────────────────────────────────────────────────────┤
│  TRUST LAYER (v19 reconstruction of the lost v16.5.4 pipeline)   │
│  model_integrity: 12-stage pipeline + 8 MODEL_* states           │
├──────────────────────────────────────────────────────────────────┤
│  EXISTING v16.5.3 SPINE (unchanged and preserved)                │
│  eventbus → fusion → world model → intent → ONE execution spine  │
│  (estop → confidence → risk class → policy → rate limit) →       │
│  action → verification → learning · AIP agent protocol (same     │
│  gates, no bypass) · privacy lifecycle · asr_registry            │
└──────────────────────────────────────────────────────────────────┘
```

## §2 The v19 guarantee set

**G1 — one command to install.** `pip install airmouse` + `airmouse
setup` (Phase 2). The Windows bootstrap script automates Python+venv+pip
with a documented trust model (Phase 3, Option C).

**G2 — never dead-end.** The launcher, doctor, setup and wizard all
render honest partial success: what works, what's missing, the exact
next command (Phase 5/7/8/9). The degradation ladder is a pure function
(`degradation.decide`) — the same answer everywhere.

**G3 — model trust anchor.** The official Vosk model has ONE spec
(`model_integrity.VOSK_MODEL`): size 41,205,931 B, SHA256
`30f26242c4eb…42498`, SHA512 `d56243ba…3bc5a` — re-measured live (4th
independent measurement, 2nd complete download; cross-checked against
the Alpine aports digest). The v16.5.3 pin (`507575b9…`, one byte off)
was WRONG and made every consented download fail verification — fixed
in v19 with regression tests, including a real-41-MB-archive test.

**G4 — the 12-stage pipeline.** temp → fsync → byte count → SHA256 →
SHA512 → CRC → structure/bomb guard → zip-slip → executable/symlink
refusal → staged extract → structure validation → ATOMIC install with
rollback. Lock-protected, stale-lock/stale-staging reaped, disk-full
safe, temp archive never outlives the run, verified model never
silently replaced.

**G5 — the 8 model states.** `MODEL_MISSING / MODEL_DOWNLOAD_REQUIRED
/ MODEL_DOWNLOADING / MODEL_VERIFYING / MODEL_INSTALLING / MODEL_READY
/ MODEL_CORRUPTED / MODEL_INCOMPATIBLE` — surfaced in setup, doctor,
capabilities, repair.

**G6 — evidence never collapses.** Every interaction row carries
Reality × Epistemic (`REAL/SIMULATED/OPTIONAL/PHYSICAL_TEST_REQUIRED/
NOT_AVAILABLE` × `OBSERVED/INFERRED/PREDICTED/UNKNOWN`) as one label
with a stated basis. Runtime probes can OBSERVE; they can never upgrade
a missing capability to REAL.

**G7 — measured, not claimed.** Setup measures per-step seconds; the
five-minute harness measures the whole journey (`12.8 s` software total
on the build machine); budgets are enforced on demand. Missing hardware
is reported PHYSICAL_TEST_REQUIRED, never simulated into success.

**G8 — lifecycle with consent.** `reset --config|--models|--all`
(summary first, backup-first for config, explicit y), `repair`
(never installs), `uninstall` (summary + MB + exact pip command),
`update --check` (explicit-only, offline-honest, never installs).

**G9 — one home.** `~/.airmouse/` holds everything: config/, memory/,
skills/, workflows/, backups/, exports/, logs/, cache/, diagnostics/,
profile/, models/, transcripts/, macros/ + the loose artifacts. No
scattered state (Phase 11).

**G10 — offline stays offline.** No new network path except the two
explicit ones that existed conceptually before: the consented model
download and the user-invoked update check. Every consent gate is in
code, in the UI text, and in the tests.

## §3 What v19 deliberately did NOT do

- Did **not** rebuild the lost v17/v18 research layer (omnisense
  fusion internals, RF ladder, world-bridge waits, head organ,
  identity, execution-separated prediction, fault injection). Those
  were never pushed and were destroyed by a sandbox reset (2nd
  occurrence — recorded in `docs/V19_BASELINE.md`). Reconstructing the
  deployment-critical subset (trust + honesty layers above) was in
  scope; rebuilding the research layer was not, and pretending it
  exists would violate the honesty law.
- Did **not** ship a Windows .exe (Phase 3 Options A/B). Cross-building
  a Windows executable from this Linux environment cannot be verified
  here, so it is specified (docs/PACKAGING.md) as future CI work with
  PHYSICAL_TEST_REQUIRED status — not faked.
- Did **not** change the execution spine, the agent permission model,
  the privacy lifecycle, or the ASR registry contract. Zero regression:
  the full v16.5.3 suite passes unchanged plus 85 new v19 tests.

## §4 Data flow of the 5-minute journey

```
pip install airmouse
        │
        ▼
airmouse setup ──── 14 measured steps ──► home created · config valid ·
        │                                 model state recorded ·
        │                                 best interaction mode chosen +
        │                                 saved to profile · marker written
        ▼
airmouse  (first run → launcher, skipped afterwards)
        │
        ▼
[2] Test My Hardware ──► doctor: 12 sections + interaction panel
        │
        ▼
Start AirMouse ──► tracking loop through the UNCHANGED v16.5.3 spine
        │
        ▼
verify / offline-test / doctor --advanced   (deterministic proof)
```

## §5 Module map (v19 additions, all reconstructed-or-new)

| Module | Role | Origin |
|---|---|---|
| `airmouse/model_integrity.py` | 12-stage pipeline + states + record | reconstructed (v16.5.4 spec) |
| `airmouse/evidence.py` | Reality×Epistemic labels | reconstructed (v18 spec) |
| `airmouse/degradation.py` | readiness ladder | reconstructed (v18 spec) |
| `airmouse/capability_registry.py` | runtime capability fusion | reconstructed (v18 spec) |
| `airmouse/lifecycle.py` | reset/repair/uninstall/update | new (v19) |
| `airmouse/wizard.py` | six-step onboarding | new (v19) |
| `airmouse/cli_menu.py` | launcher + status dots | rewritten (v19) |
| `airmouse/setup_wizard.py` | 14 steps + measured timing | extended (v19) |
| `scripts/five_minute_test.py` | the benchmark | new (v19) |
| `scripts/airmouse-bootstrap.ps1` | Windows bootstrap | new (v19) |
| `tests/test_model_integrity.py` | 38 adversarial tests | new (v19) |
| `tests/test_capability_degradation.py` | 15 tests | new (v19) |
| `tests/test_lifecycle_wizard.py` | 19 tests | new (v19) |
| `tests/test_packaging_security.py` | 11 tests | new (v19) |
