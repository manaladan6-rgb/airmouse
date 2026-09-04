# Multi-Agent Infrastructure (§12), Shared Layer (§13) and the Control Hierarchy (§14)

Implementation: `airmouse/agents.py` (418 LOC) + `airmouse/permissions.py`
(305 LOC). Conformance tests: `tests/test_v15.py` (§12 registration /
discovery / leases / handoff / messaging / override / e-stop; §13 shared
model) and `tests/test_hardening_v15.py` (registry fuzz, hierarchy).
Status: **SIMULATION-VERIFIED**.

---

## 1. §12 — Agents on one computer

AirMouse supports MANY agents sharing one computer. Every agent gets:

| Attribute | Details |
|---|---|
| identity | `agent_id` (≤ 40 chars, unique), display name, kind (`agent`/`human`/`service`) |
| priority | 1 (highest) … 9 (default 5) |
| capabilities | bounded tuple (≤ 24) of capability names |
| budgets | per-minute budget dict (≤ 8 keys, e.g. actions/minute) |
| state | `registered → active → waiting → suspended → stopped` |
| audit | every registration/lease/conflict/handoff/stop appended to a bounded audit trail (400 entries) |

Registry bounds: 32 agents, 64 leases, 16-message inboxes.

### Registration and discovery

```python
from airmouse.agents import AgentRegistry
from airmouse.permissions import Decision

reg = AgentRegistry()
reg.permissions.grant("*", "mouse.click", Decision.ALLOW)

reg.register("research", "Research Agent", priority=3)
reg.register("writer",   "Writing Agent",  priority=5)
reg.register("browser",  "Browser Agent",  priority=4)

rows = reg.discover()   # deterministic order: priority, then agent_id
# -> ["research", "browser", "writer"]
```

### Exclusive resource leases

Resources are named strings — `"mouse"`, `"clipboard"`,
`"window:doc1"`, `"tab:research"`. `acquire()` grants an **exclusive**
lease (TTL 30 s default, ≤ 300 s):

```python
lease = reg.acquire("research", "mouse")       # ok
assert reg.acquire("writer", "mouse") is None  # refused: lease held
ok, why = reg.authorize_action("writer", "mouse", "mouse.click")
# ok is False; why contains "held by research"
```

* Re-acquiring your own lease refreshes the TTL (re-entrant).
* Expired leases are garbage-collected and marked `expired`.
* Suspended/stopped agents and unknown agents can never acquire.

### Conflict rules (deterministic)

The §12 gate `authorize_action(agent, resource, permission_key)` runs
in order: agent exists → not stopped → not suspended → resource lease →
permission engine. The conflict rule:

> Two agents may never simultaneously issue conflicting computer
> actions. **The lease holder keeps the resource until release or
> expiry; a higher-priority challenger WAITS — priority never steals a
> live lease.**

Every refused attempt is recorded in `reg.conflicts()` with
`resolution="lease_held"` for audit and explainability. There is no
preemption code path.

### Handoff

`handoff(from_agent, to_agent, resource, task_id)` = release + reacquire
+ notify (a `handoff` message lands in the receiving agent's inbox).
It fails if the sender does not hold the resource or the receiver
cannot acquire.

### Agent-to-agent messaging — DATA only

```python
reg.send("research", "writer", "info", "found 3 sources on topic X")
rows = reg.inbox("writer")
```

Message kinds: `info`, `handoff`, `result`, `question`. Messages are
**DATA** (§30): never executed, never parsed as instructions — no such
pathway exists (asserted by `test_messages_are_data`). Bodies are
bounded (400 chars) and inboxes are bounded (16).

### Human override and emergency stop (§14 authority)

* `suspend_agent(id)` — human override: state → `suspended`, all leases
  released; `authorize_action` refuses with "suspended by human
  override".
* `stop_agent(id)` — state → `stopped`; refuses all actions.
* `emergency_stop_all()` — stops every non-human agent, releases every
  lease, and latches the permission engine's E-STOP (§14 level 0), so
  every permission check is denied until a human clears it.

## 2. The Research → Writing → Browser example

The spec's canonical scenario, exercised by `tests/test_v15.py::TestMultiAgent`
(three agents, one computer):

1. **Registration** — `research` (priority 3), `writer` (priority 5),
   `browser` (priority 4) register and go `active`.
2. **Discovery** — `reg.discover()` lists them deterministically by
   priority: research, browser, writer.
3. **Exclusive mouse lease** — `research` acquires `"mouse"`. `writer`
   tries the same resource and is refused (lease held, recorded as a
   conflict). The holder keeps acting.
4. **Handoff** — `research` finishes with `"clipboard"` and hands it to
   `writer`: release + reacquire + a `handoff` message in writer's
   inbox.
5. **Data-only collaboration** — research sends writer
   `"found 3 sources on topic X"`; the message is stored data, never an
   instruction.
6. **Human override** — the human suspends `browser`; its leases drop
   and any action attempt is refused with the suspension reason.
7. **Emergency stop** — `emergency_stop_all()` stops all three agents,
   releases all leases, and engages the permission E-STOP; even
   previously-allowed permission checks now deny.

## 3. §13 — The shared-layer contract

Humans and agents go through the **SAME** interaction layer. There is
no agent fast-path:

| Layer | Contract |
|---|---|
| World model | agent observations come from the same temporal world model humans' HUD shows (§3) |
| Target resolution | the SAME `UniversalTargetResolver` chain (§8) resolves human voice targets and agent `targets()` requests |
| Permissions | agents and humans face the same permission/safety gates; agents get **no separate bypass pathway** (§14) |
| Action | agent executes go through the shared action layer (TaskEngine when attached) |
| Verification | every agent action gets the same evidence-based verification |
| Recovery | agent failures use the same §7 recovery loop, including REQUEST_HUMAN |

Asserted by `tests/test_v15.py::TestSharedInteractionModel`
(same resolver result for human and agent requests; a blocked
`destructive.action` key denies an agent even at priority 1).

## 4. §14 — The global control hierarchy

Nothing may reorder it:

```
EMERGENCY STOP  >  HUMAN OVERRIDE  >  SAFETY POLICY  >
PERMISSION      >  AGENT           >  PREDICTION
```

`AgentPermissionEngine.check(agent_id, key)` walks the levels:

1. **EMERGENCY STOP** — latched ⇒ everything DENIED (`emergency_stop(on)`).
2. **HUMAN OVERRIDE** — `human_override(True/False)` force-allows or
   force-denies everything below it; `None` clears the override.
3. **SAFETY POLICY** — `safety_block(key)` marks a capability unusable
   regardless of permissions.
4. **PERMISSION** — the §15 granular rules.
5. **AGENT** — agent priority/leases/budgets influence scheduling, never
   authority.
6. **PREDICTION** — predictions are data; they sit at the bottom and can
   never outrank observation or permission.

### §15 granular permission keys

`observe.screen` · `read.accessibility` · `read.clipboard` ·
`type.text` · `mouse.click` · `browser.navigate` · `file.read` ·
`file.write` · `application.launch` · `application.close` ·
`system.operation` · `destructive.action`

### §15 decisions

| Decision | Meaning |
|---|---|
| `ALLOW` | permitted (within hierarchy) |
| `DENY` | refused |
| `ASK` | requires a human answer — **without one it is NO** (fail closed); no rule at all also defaults to ASK |
| `ALLOW_ONCE` | use-counted; exhausted ⇒ ASK |
| `ALLOW_SESSION` | allowed for the session (counted) |
| `ALLOW_PATTERN` | fnmatch-pattern grant; mismatch ⇒ deny |

Rule matching is deterministic: exact agent+key > agent+wildcard >
wildcard-agent exact key > wildcard. `explain_decision()` returns the
active hierarchy level and a "because" chain (§24, no sensitive data).

## 5. Bounds and honest limits

* 32 agents / 64 leases / 400 audit entries / 16-message inboxes —
  hard-coded bounds, deterministically evicted.
* Budgets are tracked on the profile (`actions_used`); enforcement of
  per-minute spend beyond counting is an extension point, not a claim.
* Lease expiry is measured in monotonic time and reaped lazily (on the
  next registry interaction) — deterministic, not wall-clock-guaranteed.
* All of the above is SIMULATION-VERIFIED; no physical multi-machine
  scenario is claimed.
