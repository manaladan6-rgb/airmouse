# AirMouse v15 Developer Guide — from install to first action

Audience: developers building on AirMouse — especially agent authors.
Deeper references: `docs/AIP_SPEC.md` (wire protocol),
`docs/AGENT_SDK.md` (SDKs), `docs/SKILLS.md` (skills + marketplace),
`docs/V15_ARCHITECTURE.md` (system map), `docs/CLI_REFERENCE.md`
(CLI), `docs/SECURITY.md` / `docs/PRIVACY.md` (platform posture).

Everything in this guide is **SIMULATION-VERIFIED**: the deterministic
§26 simulator is the "computer", and no physical hardware is required
— or claimed.

---

## 1. Install (minutes)

```bash
# main platform (Python 3.9+; stdlib for all v12→v15 features)
pip install airmouse-15.0.0-py3-none-any.whl

# standalone agent runtime — OPTIONAL, stdlib-only, never imports airmouse
pip install ./agent-core/           # provides the `airmouse-agent` CLI

# JS/TS SDK — OPTIONAL, dependency-free (no npm install needed)
# agent-sdk-js/airmouse-agent.js (+ .d.ts) is a single file
```

Smoke checks:

```bash
airmouse --version          # AirMouse v15.0.0 banner
airmouse status             # platform, protocol AIP 1.0, §14 hierarchy
airmouse-agent --version    # airmouse-agent-core 1.0.0 (AIP 1.0)
```

## 2. Path A — first action with the in-process SDK (Python)

```python
from airmouse.agent_sdk import AirMouse
from airmouse.permissions import AgentPermissionEngine, Decision

# a gate you control (omit it and every execute fails closed to ASK==NO)
gate = AgentPermissionEngine()
gate.grant("sdk-agent", "mouse.click", Decision.ALLOW)

from airmouse.agent_sdk import AipEndpoint
from airmouse.tasks import TaskEngine

endpoint = AipEndpoint(permission_engine=gate, task_engine=TaskEngine())

air = AirMouse(endpoint=endpoint)
assert air.connect()
print(air.capabilities()["protocol_version"])     # 1.0
print(air.observe())                              # world snapshot fields
res = air.execute(intent="open my research project", verify=True)
assert res["ok"], res
print(res["action_id"], res["verification"]["verified"])
air.stop()                                        # revoke control
```

What just happened, in order: `connect()` negotiated AIP 1.0;
`capabilities()` listed modalities + actions with permission keys;
`observe()` read the shared world model; `execute()` crossed the
permission gate and ran through the shared task layer; `verify` gave
you an evidence-backed verdict; `stop()` revoked your agent.

### Attach the real world model + target resolver

The endpoint accepts the same engines the human path uses (§13 shared
layer):

```python
from airmouse.world_model_temporal import TemporalWorldModel
from airmouse.target_resolver import UniversalTargetResolver

endpoint = AipEndpoint(
    world_model=TemporalWorldModel(),
    target_resolver=UniversalTargetResolver(),
    permission_engine=gate,
    task_engine=TaskEngine(),
)
```

With a resolver attached, `air.targets(description="Submit button")`
returns the §8 chain result + explanation.

## 3. Path B — first action with the standalone core (Python)

```python
from airmouse_agent import AirMouse

# in-process: any callable wire->wire (or an object with .send)
def handler(wire: str) -> str:
    ...  # bridge to your endpoint / simulator

air = AirMouse(handler=handler, agent_id="my-agent")
air.connect()
air.capabilities()

# or spawn a core process and speak AIP JSON-lines over stdio:
air = AirMouse(transport="stdio://airmouse --aip-stdio")
```

The `airmouse_agent` client raises `AipError(code, message)` for
protocol errors; permission denials arrive as normal
`permission_denied` error payloads. Full surface: `docs/AGENT_SDK.md`.

## 4. The simulator — a fake computer (§25/§26)

`airmouse.simulator.Simulator` is the deterministic virtual computer:
windows, tabs/pages with buttons and forms, text, files, clipboard,
navigation, UI changes and failure modes. Same script → same final
state. Nothing touches a real display.

```python
from airmouse.simulator import Simulator

sim = Simulator()
sim.add_window("Compose", app="mail", buttons=["Send"], text="body")
sim.focus_window("Compose")
sim.click_button("Send")            # deterministic result
sim.add_tab("https://research.example", title="Research")
sim.set_clipboard("copied text")
sim.change_ui("Send", "Send Now")   # UI changes are first-class
print(sim.observe())                # snapshot for the world model
```

Wire the simulator into your endpoint's executor to test complete agent
workflows offline, deterministically, with zero hardware.

## 5. Failure testing (§27)

Inject all 12 failure classes and verify OBSERVE → DIAGNOSE → RECOVER →
VERIFY (or a safe stop):

```python
from airmouse.simulator import Simulator
from airmouse.failure_injection import run_failure_scenario

sim = Simulator()
for name in ("missing_target", "moved_button", "closed_window",
             "stale_dom", "timeout", "app_crash"):
    outcome = run_failure_scenario(name, sim)
    print(name, outcome.to_dict())
```

`permission_denial`, `agent_conflict` and `malformed_request` never
retry — they resolve through the human or fail closed.

## 6. Tasks, goals and Do-It-With-Me plumbing

```python
from airmouse.goals import GoalHierarchyParser
from airmouse.tasks import TaskEngine
from airmouse.ditm import DoItWithMe

obj = GoalHierarchyParser().parse("prepare the quarterly report")
# obj.level == "task"; obj.risk / required_permissions / proposed_plan
# obj.execution_allowed is ALWAYS False (PREDICTION ≠ PERMISSION ≠ EXECUTION)

tasks = TaskEngine()
ditm = DoItWithMe(task_engine=tasks)
session = ditm.propose("prepare the quarterly report")   # structured proposal
ditm.approve(session.session_id, approved=True)          # human decision
print(ditm.report(session.session_id)["progress"])
```

Destructive plans stay `PENDING_APPROVAL` inside the TaskEngine until a
human approves them.

## 7. CLI for developers

```bash
airmouse protocol        # AIP version, concepts, schema list
airmouse capabilities    # AIP discovery listing
airmouse benchmark       # local twin/world/task spot-check
airmouse observe         # simulated-computer observation (no hardware claimed)
airmouse tasks | skills | agents | permissions | world | twin | status
```

All v15 subcommands are print-and-exit, local and fast.

## 8. Testing your integration

* Run the platform's own suites as a template: `pytest tests/test_v14_5.py
  tests/test_v15.py tests/test_hardening_v15.py` — protocol conformance,
  SDK flows, multi-agent scenarios, fuzzing, budgets.
* Determinism rules the tests follow (adopt them): fresh engines per
  test, explicit timestamps, no sleeps, no network, no cv2/mediapipe.

## 9. Performance budgets (enforced by tests)

| Operation | Budget |
|---|---|
| twin learn / world observe / task create | < 10 ms each |
| AIP message parse | < 5 ms |
| intent (goal) parse | < 50 ms |
| target resolve | < 20 ms |
| SDK execute (in-process) | < 50 ms |
| recovery loop round | < 20 ms |
| `airmouse_agent` import | < 200 ms |
| agent-core source size | ≈ 10 KB, stdlib-only, lazy |

## 10. Safety rules your agent must respect

1. No bypass pathway exists — don't look for one; use `REQUEST`/
   `AUTHORIZE` for pre-flight and treat `permission_denied` as final.
2. Coordinates are last-resort: set `coordinate_fallback` only when
   semantic/accessibility/DOM/OCR/vision/geometry all fail.
3. Multi-step work belongs in `task()` (approval gates + audit).
4. Destructive anything requires a human — plan for the ASK path.
5. In multi-agent runs, acquire leases before acting and wait (never
   expect priority to preempt).
6. Params are inert data; there is no field that executes anything.
