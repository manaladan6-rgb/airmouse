# Skills — Interaction Compression (§6) and the Marketplace (§20)

Implementations: `airmouse/skills.py` (551 LOC, v13.5 §6) and
`airmouse/marketplace.py` (243 LOC, v15 §20). Conformance tests:
`tests/test_v13_5.py`, `tests/test_v15.py` (§20 marketplace), fuzz in
`tests/test_hardening_v15.py`. Status: **SIMULATION-VERIFIED**.

---

## 1. §6 — Discovery → proposal → approval pipeline

A Skill is a semantic, versioned automation learned from the user's own
repeated behavior. The pipeline is deterministic and **never silent**:

```
observe sequences ──► cluster by action-template signature
        │                    (action names + target KINDS;
        │                     coordinates/values deliberately IGNORED)
        ▼
≥ MIN_OCCURRENCES (3) identical sequences
        ▼
avg confidence ≥ MIN_CONFIDENCE (0.6)
        ▼
user NOTIFICATION + PREVIEW  (proposal is DATA; "silent": False)
        ▼
explicit user APPROVAL            ◄── nothing exists before this
        ▼
PersonalSkillLibrary (versioned, editable, revocable, exportable)
```

### InteractionCompression (the watcher)

```python
from airmouse.skills import InteractionCompression

comp = InteractionCompression()
for run in observed_runs:                     # each run = list of step dicts
    comp.observe_sequence(run, confidence=0.7)

proposal = comp.propose()
# {"proposal": True, "signature": "open_app:semantic->click:dom->type_text:semantic",
#  "occurrences": 3, "avg_confidence": 0.7,
#  "preview_steps": [{"index": 0, "action": "open_app"}, ...],
#  "requires": "user_approval", "silent": False}
```

* The template signature (`_signature_for`) is `action:target_kind`
  pairs joined by `->`. Two runs cluster together when they use the
  same actions against the same target KINDS — even if the click
  coordinates or typed values differed. This is what makes skills
  coordinate-independent by construction.
* Sequences shorter than 2 or longer than 24 steps are ignored;
  ≤ 64 clusters retained (weakest evicted).
* `candidates()` lists clusters meeting both thresholds; `propose()`
  builds the top candidate's notification + preview. **Approval is a
  separate, explicit act** — `propose()` never creates anything.

### Coordinate rule (§6 rule 2)

A Skill must NOT depend on screen coordinates. Targets are semantic /
accessibility / DOM / OCR / visual first. Raw coordinates are an
explicit, flagged fallback only:

* `SkillTarget.coordinate_fallback` defaults to `False`;
* `_clean_target()` demotes coordinate-shaped input into the flagged
  fallback of a semantic target;
* at resolution time the universal resolver (§8) only walks the
  `coordinate` provider when `allow_coordinate_fallback=True`.

### PersonalSkillLibrary

```python
lib = PersonalSkillLibrary()
skill = lib.create_skill_from_cluster(cluster, name="daily report")
lib.edit(skill.skill_id, {"description": "compiles the daily report"})
lib.set_enabled(skill.skill_id, False)
lib.record_use(skill.skill_id, success=True)
data = lib.export()          # versioned JSON bundle (≤ bounds)
lib.import_skills(data)      # validated per row, scrubbed
lib.revoke(skill.skill_id)   # gone — skills are revocable
```

* Bounded: 100 skills × 24 steps; names validated
  `^[a-z0-9][a-z0-9 _-]{1,59}$`; credential-shaped strings refused.
* Skills are permission-aware (each step carries risk) and inspected
  via `get()` / `find_by_name()` / `list_skills()`.

## 2. Skill schema

A skill step (as stored/exported):

```json
{
  "index": 0,
  "description": "open the report template",
  "action": "open_app",
  "target": {
    "kind": "semantic",
    "value": "Quarterly Report",
    "fallback": null,
    "coordinate_fallback": false
  },
  "params": {},
  "expected_result": "template window focused",
  "risk": "low"
}
```

A skill:

| Field | Meaning |
|---|---|
| `skill_id` | library id (`skill-%04d`) |
| `name` | validated, human-readable |
| `version` | incremented by `edit()` |
| `description` | ≤ 200 chars |
| `steps` | ≤ 24 `SkillStep`s (above) |
| `enabled` | soft on/off without revocation |
| `risk` | per-step risk from the §4 vocabulary (none/low/medium/high/destructive) |

## 3. Library lifecycle

`create_skill` / `create_skill_from_cluster` → `list_skills` →
`edit` (version bump) → `set_enabled` → `record_use` (success stats) →
`export` / `import_skills` (validated) → `revoke` (removal). Every
mutation is bounded and deterministic; imports are fail-closed
(malformed or credential-shaped entries rejected, counted, never
trusted).

## 4. §20 — Marketplace manifest format

Every marketplace skill declares a **manifest** — and manifests are
**DATA**: there is NO code-execution path. Arbitrary skill code never
executes; the runtime executes only pre-vetted ACTION NAMES from the
unified action vocabulary. No embedded scripts, no shell, no fetched
code.

### Manifest fields (§20)

| Field | Required | Rules |
|---|---|---|
| `name` | ✔ | `^[a-z0-9][a-z0-9 ._-]{1,59}$` |
| `version` | ✔ | semver `x.y.z` |
| `author` | ✔ | string |
| `permissions` | ✔ | bounded list (≤ 24) of permission keys |
| `risk_level` | ✔ | `none` / `low` / `medium` / `high` / `destructive` |
| `capabilities` | — | bounded list |
| `dependencies` | — | bounded list |
| `required_modalities` | — | bounded list |
| `supported_applications` | — | bounded list |
| `installation` | — | string (instructions, data only) |
| `uninstall_behavior` | — | string |
| `description` | — | string |
| `steps` | — | 1..24 step objects (compiled into the personal library) |
| `skill_id` | — | library link on install |

Validation is **fail-closed**: unknown fields are rejected outright
(`unknown field: <key>`), missing required fields rejected, manifest
size ≤ 256 KB, non-objects rejected. There is no "best effort" mode.

### Trust boundaries

* **high / destructive risk requires explicit human trust:**
  `install(manifest, trusted_by_human=False)` refuses with
  "risk 'high' requires explicit human trust". The flag is a distinct
  human act, not an inference.
* **Risk escalation needs reinstall:** `update()` refuses when the new
  manifest raises risk level (high/destructive) over the installed one.
* **Updates must supersede:** strict semver comparison; equal/older
  versions are refused.
* **Rollback:** the previous manifest/version is kept; `rollback()`
  restores it (including library steps).
* **Remove** honours the declared uninstall behavior (revokes the
  library skill).

### Lifecycle

```python
from airmouse.marketplace import Marketplace

mp = Marketplace(library=lib)
ok, msg = mp.validate_manifest(manifest)        # fail-closed check
ok, msg = mp.install(manifest)                  # or trusted_by_human=True
mp.set_enabled(name, False)                     # disable
ok, msg = mp.update(newer_manifest)             # strict semver, risk guard
ok, msg = mp.rollback(name)                     # previous version back
mp.inspect(name)                                # manifest + state, redacted to safe keys
mp.remove(name)
mp.list_installed()
```

## 5. How skills run (and what they can never do)

When a skill runs, each step becomes a structured task step (§5) with
its declared action, semantic target, risk and permission — through the
SAME permission gates, target resolver, verification and recovery as
everything else. Skills therefore cannot:

* execute code (manifests and steps are data);
* bypass confirmation for destructive steps;
* silently install or run (proposal + approval is mandatory);
* depend on hidden coordinates (flagged fallback only).

## 6. Bounds summary

| Bound | Value |
|---|---|
| skills per library | 100 |
| steps per skill | 24 |
| observed clusters | 64 |
| repetitions before proposal | ≥ 3 |
| confidence threshold | ≥ 0.6 |
| manifest size | ≤ 256 KB |
| manifest list fields | ≤ 24 entries |
