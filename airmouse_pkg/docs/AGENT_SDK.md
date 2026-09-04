# AirMouse Agent SDK — Python, standalone core, and JavaScript

Chapters §10 (SDK) and §11 (standalone agent core) of the v15 spec.
Protocol reference: `docs/AIP_SPEC.md`. Everything here is
**SIMULATION-VERIFIED** (in-process + stdio AIP); nothing in the SDK
touches hardware.

The SDK is **the ONE abstraction AI agents use to drive a computer
through AirMouse**. Agents should NOT need to understand MediaPipe
internals, gaze filters, gesture internals, event bus internals,
browser bridge internals or model internals — everything crosses the
AIP protocol boundary, so the same conversation works in-process, over
stdio, or (by extension) over any local transport.

---

## 1. The §10 example

```python
from airmouse.agent_sdk import AirMouse

air = AirMouse()
air.connect()
air.capabilities()
air.observe()
air.execute(intent="open my research project", verify=True)
```

This exact flow is asserted by `tests/test_v14_5.py::test_spec_example_flow`.

## 2. Python SDK (`airmouse.agent_sdk.AirMouse`)

Part of the main `airmouse` package (§10). Works in two ways:

* `AirMouse(endpoint=...)` — talks to an `AipEndpoint` you wired to real
  services (world model, target resolver, task engine, permission
  engine, agent registry);
* `AirMouse()` — lazily builds a default in-process endpoint on
  `connect()`.

### Primitives (§10)

| Primitive | Returns | Notes |
|---|---|---|
| `connect()` | bool | status handshake; records negotiated protocol version |
| `capabilities()` | dict | `discover` reply: `protocol_version`, capability list, schema names |
| `observe()` | dict | `observation`: ts, active app/window, mode, targets visible, sensor health, browser |
| `targets(description=…, kind=…, value=…)` | dict | `targets`: resolved target(s) + resolver explanation (§8 chain) |
| `execute(intent=…, action=…, target=…, verify=True, **params)` | dict | `result` envelope or `{"ok": False, "error": {...}}` |
| `verify(action_id)` | dict | `verification` verdict for a previous action |
| `task(objective, steps=…)` | dict | create a structured task in the core's TaskEngine |
| `stop()` | dict | revoke this agent's control; later executes are refused |
| `status()` | dict | endpoint state: protocol version, stopped flag, attached services |

`execute()` wraps its arguments as an AIP `action` payload (action name
≤ 40 chars, target kind/value, `verify` flag, params as bounded inert
strings, optional `intent` phrase ≤ 200 chars). The client validates
its own message through `parse_message()` before sending — the SDK
holds itself to the same fail-closed rules as the server.

### The endpoint (`AipEndpoint`)

The server side of the conversation. It is **the SAME layer humans
use** (shared interaction model, §13): observation comes from the world
model, targets from the universal TargetResolver, execution through the
task/permission/recovery machinery. The endpoint NEVER grants anything
by itself:

* every EXECUTE is schema-validated, then permission-checked
  (`permission_engine.check(agent_id, key)`, else the agent registry,
  else **fail closed to ASK ⇒ denied**);
* execution goes through the shared action layer (TaskEngine when
  attached);
* results are re-validated against the `result` schema before being
  returned;
* `stop` latches the stopped state — subsequent requests are refused
  with `stopped`.

## 3. Standalone Python client (`airmouse-agent-core`, §11)

Package `agent-core/` at the repository root — a **separate**
`airmouse-agent-core` distribution (version 1.0.0) for machines that
must not install the full AirMouse package:

```python
from airmouse_agent import AirMouse   # never imports airmouse

air = AirMouse(handler=my_local_endpoint)   # in-process
air.connect()
air.capabilities()
air.execute(intent="open my research project", verify=True)
```

* **stdlib-only, lazy**: imports json/time/typing at module scope;
  `subprocess` is imported only when a stdio transport is first used.
  Never imports `airmouse` — no ML deps, no camera, no microphone, no
  GUI, no cloud.
* **Transports**: a callable `handler(wire)->wire` (in-process), any
  object with `.send(wire)->wire`, or `"stdio://<command>"` (spawns the
  core and speaks AIP JSON-lines over stdin/stdout).
* **Errors as exceptions**: protocol-level failures raise
  `AipError(code, message)`; permission denials arrive as normal error
  payloads (`permission_denied`).
* **CLI**: `airmouse-agent --version` prints
  `airmouse-agent-core 1.0.0 (AIP 1.0)` — a fast smoke check (§11).

Source size ≈ 10 KB (three files: `__init__.py`, `client.py`,
`version.py`).

## 4. JavaScript/TypeScript SDK (`agent-sdk-js`, §11)

`agent-sdk-js/airmouse-agent.js` (+ `airmouse-agent.d.ts`) —
**dependency-free**, UMD-style (Node `require` or browser global
`AirMouseAgent`), identical primitive surface to the Python SDK:

```js
const { AirMouse, InProcessTransport } = require("./airmouse-agent");

const air = new AirMouse(new InProcessTransport(handlerFn), { agentId: "my-agent" });
await air.connect();
await air.execute({ intent: "open my research project", verify: true });
```

* `InProcessTransport(handlerFn)` — in-process; `StdioTransport(cmd)` —
  spawns the core and speaks JSON-lines (lazy `child_process`).
* `execute(spec)` takes `{intent, action, target, verify, params}`;
  `targets({kind, value, description, coordinateFallback})`; other
  primitives are 1:1 with Python.
* Replies are Promises (resolved inline for sync transports); protocol
  errors throw `AipError`.
* The `.d.ts` typing surface mirrors the Python SDK.

## 5. Full primitive reference (all three SDKs)

| Primitive | Python (`agent_sdk`) | Python (`airmouse_agent`) | JS (`airmouse-agent`) |
|---|---|---|---|
| connect / status | `connect()`, `status()` | `connect()`, `status()`, `negotiate()` | `connect()`, `status()` |
| capability discovery | `capabilities()` | `capabilities()` | `capabilities()` |
| observation | `observe()` | `observe()` | `observe()` |
| targets | `targets(...)` | `targets(...)` | `targets({...})` |
| execute | `execute(...)` | `execute(...)` | `execute({...})` |
| verify | `verify(action_id)` | `verify(action_id)` | `verify(actionId)` |
| task | `task(objective, steps)` | `task(objective, steps)` | `task(objective, steps)` |
| stop | `stop()` | `stop()` | `stop()` |

## 6. What the SDK HIDES

Agents never see (§10):

* MediaPipe hand/gaze internals and any camera code;
* gaze filters, dwell/blink logic, gesture state machines;
* the v10 event bus, fusion engine and context engine;
* browser bridge / CDP internals;
* the world model, target resolver chains, task engine, recovery
  engine, twin and personalization internals;
* model artifacts, learning and memory stores.

All of it sits behind the AIP boundary; agents speak intents, actions
and targets and receive observations, verifications and results.

## 7. What the SDK CANNOT do

* **Bypass permissions.** There is no bypass pathway: every `execute`
  crosses the permission gate, and the SDK's own endpoint fails closed
  to ASK (== NO) when no gate is attached (asserted by
  `test_endpoint_fails_closed_without_permission_engine`).
* **Override the hierarchy.** E-STOP > HUMAN OVERRIDE > SAFETY POLICY >
  PERMISSION > AGENT > PREDICTION. An agent can never override e-stop,
  human cancellation, destructive-action confirmation, permission
  boundaries or security policies (§14).
* **Execute code.** No protocol field leads to shell/command execution;
  hostile strings in params are inert bounded data (§23/§30, asserted).
* **Skip verification or hide failure.** Every result carries the
  verification verdict; permission denials and conflicts are reported,
  never retried by the agent-side engine.
* **Act without a lease in a multi-agent setting.** Conflicting actions
  on a lease-held resource are refused (`docs/MULTI_AGENT.md`).
* **Phone home.** The SDKs have zero network code; transports are
  in-process/stdio only.

## 8. Performance budgets (all asserted in `tests/test_hardening_v15.py`)

| Budget | Value |
|---|---|
| `airmouse_agent` (agent-core) import | < 200 ms |
| agent-core package size | ≈ 10 KB source (three files; never imports airmouse) |
| SDK execute round-trip, in-process | < 50 ms |
| AIP message parse | < 5 ms |

Measured environments vary; the budgets are enforced as regression
tests so the numbers cannot silently rot.

## 9. Safety checklist for agent authors

1. Always call `connect()` and read `capabilities()` — availability and
   permission keys are discoverable, not assumed.
2. Treat every non-`ok` result as final; `permission_denied` means NO.
3. Use `targets()` + semantic descriptors; pass
   `coordinate_fallback` only when you truly need it (explicit flag,
   §6/§8).
4. Prefer `task()` for multi-step work so steps get verification,
   approval gates and audit.
5. Call `stop()` when done — it revokes control immediately.
6. Never try to encode actions in strings (params are inert data); use
   the declared action vocabulary.
