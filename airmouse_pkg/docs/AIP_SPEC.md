# AirMouse Interaction Protocol (AIP) 1.0 — Specification

Implementation of record: `airmouse/aip.py` (v14.5 §9). Consumers:
`airmouse/agent_sdk.py` (§10), `agent-core/airmouse_agent/` (§11),
`agent-sdk-js/airmouse-agent.js` (§11). Conformance tests:
`tests/test_v14_5.py`, fuzzing + budgets in `tests/test_hardening_v15.py`.

AIP is tiny, deterministic, documented, versioned, language-neutral,
**local-first**, secure and permission-aware. It carries requests and
observations between AI agents and an AirMouse core — **it never grants
powers**: every EXECUTE crosses the core's permission/confirmation gates.

Status: **SIMULATION-VERIFIED** at protocol level, in-process and over
stdio JSON-lines. Not a network protocol; no socket code exists in the
implementation.

---

## 1. Envelope

Every message on the wire is one JSON object with EXACTLY these fields
(`AipMessage.to_dict()`; unknown envelope fields are rejected):

| Field | Type | Limits | Notes |
|---|---|---|---|
| `aip_version` | string | major must be `1` | e.g. `"1.0"`; missing/wrong major ⇒ rejected |
| `type` | string | one of the §2 message types | unknown type ⇒ rejected |
| `id` | string | ≤ 64 chars, regex `^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,63}$` | required; `msg-%08d` convention |
| `agent_id` | string | ≤ 64 chars, same regex | optional; sender identity |
| `request_id` | string | ≤ 64 chars | correlation with the triggering message |
| `ts` | number | — | seconds; `ts` must be int/float (bool rejected) |
| `payload` | object | ≤ 256 KB serialized | must be a JSON object; schema-validated per §4 |

JSON is serialized with sorted keys (`json.dumps(..., sort_keys=True)`).

## 2. Message types

18 conversation message types plus the `status` handshake — 19 enum
values total (`MsgType`):

| Type | Direction | Purpose (§9 concept) |
|---|---|---|
| `discover` | request | **DISCOVER** — what can this AirMouse do? (capability discovery) |
| `capabilities` | reply | capability list + negotiated `protocol_version` + schema names |
| `observe` | request | **OBSERVE** — what is true right now? (read-only) |
| `observation` | reply | one observation snapshot payload |
| `target` | request | **TARGET** — what can I act on? (targets query) |
| `targets` | reply | resolved target(s) + explanation |
| `request` | request | **REQUEST** — "I want to do X — may I?" (permission pre-flight) |
| `authorize` | request | **AUTHORIZE** — human/policy confirmation decision for a pending request |
| `permission` | reply | permission decision (`allow`/`deny`/`ask`/…) |
| `execute` | request | **EXECUTE** — do X now (permission required) |
| `verify` | request | **VERIFY** — did X actually happen? (evidence) |
| `verification` | reply | verification verdict for an action |
| `result` | reply | **RESULT** — the outcome envelope (success/failure/recovery) |
| `task` | request | create a structured task (objective + steps) |
| `confirmation` | payload | the authorize payload schema (request_id + approved) |
| `error` | reply | error envelope (see §4 error codes) |
| `recovery` | payload | recovery description (strategy/rounds/outcome) |
| `stop` | request | **STOP** — revoke agent control |
| `status` | both | handshake: protocol version, endpoint state, attached services |

## 3. Version negotiation and capability discovery

* **Rule (§9):** same MAJOR version required. `negotiate_version(offered)`
  returns the server's exact `AIP_VERSION` (`"1.0"`) when the offered
  major equals `SUPPORTED_MAJOR` (1), otherwise `None` — and the caller
  must fail closed. Minor version differences resolve to the server's
  exact version.
* A `discover` with an unsupported version is answered with an `error`
  of code `unsupported_version`.
* `discover` replies with `protocol_version`, a deterministic
  (sorted) capability list — modalities with `available` flags and
  actions with their permission keys — and the sorted list of schema
  names (§4).

## 4. Payload schemas (the 12 schema set)

All payloads are validated by a small strict validator
(`validate_against_schema`). Copied field-by-field from `_SCHEMAS` in
`airmouse/aip.py`. Types: `string` (max length), `number`/`integer`
(min/max), `boolean`, `array` (item type, max length), `ref`
(nested schema). Limits not listed below: strings default ≤ 4096
(`MAX_STR`), arrays default ≤ 256 (`MAX_ARRAY`).

### capability
Required: `name`, `available`.

| Field | Type | Constraints |
|---|---|---|
| `name` | string | ≤ 64 |
| `available` | boolean | — |
| `kind` | string | enum: `modality`, `action`, `observation`, `task` |
| `risk` | string | enum: `none`, `low`, `medium`, `high`, `destructive` |
| `permission` | string | ≤ 64 |
| `note` | string | ≤ 120 |

### observation
Required: `ts`.

| Field | Type | Constraints |
|---|---|---|
| `ts` | number | min 0 |
| `active_application` | string | ≤ 120 |
| `active_window` | string | ≤ 160 |
| `mode` | string | ≤ 40 |
| `targets_visible` | integer | 0..100000 |
| `sensor_health` | string | ≤ 20 |
| `browser` | string | ≤ 40 |

### target
Required: `kind`, `value`.

| Field | Type | Constraints |
|---|---|---|
| `kind` | string | enum: `semantic`, `accessibility`, `dom`, `ocr`, `vision`, `geometry`, `coordinate` |
| `value` | string | ≤ 160 |
| `app` | string | ≤ 120 |
| `confidence` | number | 0..1 |
| `point` | array | 2 numbers |
| `coordinate_fallback` | boolean | explicit flag (§6/§8) |

### intent
Required: `name`.

| Field | Type | Constraints |
|---|---|---|
| `name` | string | ≤ 80 |
| `utterance` | string | ≤ 300 |
| `confidence` | number | 0..1 |
| `level` | string | enum: `command`, `intent`, `task`, `goal` |
| `risk` | string | enum: `none`, `low`, `medium`, `high`, `destructive` |

### action
Required: `action`.

| Field | Type | Constraints |
|---|---|---|
| `action` | string | ≤ 40 |
| `target` | ref → `target` | — |
| `params` | object | open string-map, ≤ 8 entries, scalar values, keys ≤ 40 chars |
| `verify` | boolean | — |
| `timeout` | number | 0.05..600 |
| `risk` | string | enum: `none`, `low`, `medium`, `high`, `destructive` |

### task
Required: `objective`.

| Field | Type | Constraints |
|---|---|---|
| `objective` | string | ≤ 200 |
| `steps` | array of objects | ≤ 64 |
| `owner` | string | ≤ 40 |
| `task_id` | string | ≤ 64 |

### permission
Required: `key`, `decision`.

| Field | Type | Constraints |
|---|---|---|
| `key` | string | ≤ 64 |
| `decision` | string | enum: `allow`, `deny`, `ask`, `allow_once`, `allow_session`, `allow_pattern` |
| `agent_id` | string | ≤ 64 |
| `reason` | string | ≤ 160 |

### confirmation
Required: `request_id`, `approved`.

| Field | Type | Constraints |
|---|---|---|
| `request_id` | string | ≤ 64 |
| `approved` | boolean | — |
| `by` | string | ≤ 40 |
| `scope` | string | enum: `once`, `session`, `pattern` |

### verification
Required: `verified`.

| Field | Type | Constraints |
|---|---|---|
| `verified` | boolean | — |
| `action_id` | string | ≤ 64 |
| `message` | string | ≤ 200 |
| `checks` | array of strings | ≤ 16 |

### error
Required: `code`, `message`.

| Field | Type | Constraints |
|---|---|---|
| `code` | string | enum: `bad_message`, `unsupported_version`, `permission_denied`, `not_found`, `conflict`, `timeout`, `failed`, `busy`, `stopped` |
| `message` | string | ≤ 200 |
| `request_id` | string | ≤ 64 |

### recovery
Required: `strategy`.

| Field | Type | Constraints |
|---|---|---|
| `strategy` | string | ≤ 40 |
| `rounds` | integer | 0..64 |
| `outcome` | string | ≤ 40 |
| `human_message` | string | ≤ 200 |

### result
Required: `ok`, `request_id`.

| Field | Type | Constraints |
|---|---|---|
| `ok` | boolean | — |
| `request_id` | string | ≤ 64 |
| `action_id` | string | ≤ 64 |
| `verification` | ref → `verification` | — |
| `recovery` | ref → `recovery` | — |
| `error` | ref → `error` | — |
| `detail` | string | ≤ 200 |

`schemas_document()` returns `{"aip_version": "1.0", "schemas": {...}}`
— the machine-readable schema set for docs and negotiation.

## 5. Size limits

| Limit | Value |
|---|---|
| `MAX_MESSAGE_BYTES` (whole message and payload) | 256 KB |
| `MAX_ARRAY` (default array cap) | 256 items |
| `MAX_STR` (default string cap) | 4096 chars |
| `MAX_ID` (id / agent_id / request_id) | 64 chars |

Oversized messages are rejected (`"message too large"` /
`"payload too large"`), never truncated silently. Outbound clients
mirror the cap (`airmouse_agent` raises `AipError("bad_message",
"outbound message too large")`).

## 6. Fail-closed validation rules (§23)

Every inbound message passes `parse_message()` + schema validation.
Anything off yields `(None, [errors])` — messages are REJECTED, never
coerced:

1. Only the 7 envelope fields are accepted — **unknown envelope fields
   are rejected**.
2. Missing `aip_version`/`id`, or a major version ≠ 1 ⇒ rejected
   (`unsupported aip_version`).
3. Unknown `type` (not in `MsgType`) ⇒ rejected.
4. `agent_id`/`id` must match the id regex ⇒ otherwise rejected.
5. `payload` must be an object within the 256 KB cap.
6. Schema validation is strict: **unknown fields inside schema'd
   objects are rejected** (`additionalProperties=false` by design);
   required fields must be present; enum/min/max/length constraints
   enforced; booleans are never accepted where numbers are expected.
7. Malformed JSON / wrong encoding ⇒ rejected, never guessed.
8. At the engine level: an EXECUTE without a permission gate, or with a
   gate that answers `deny` or `ask`, is refused with
   `error.code = "permission_denied"` — **ASK without a human == NO**
   (§15). `permission_denied` is never retried by the recovery engine;
   `malformed_request` fails closed (GIVE_UP) (§7).

Hostile strings inside `params` remain inert DATA: the protocol has no
field that leads to shell/command execution (asserted by
`tests/test_v14_5.py::test_malicious_params_are_inert_data`).

## 7. Example — EXECUTE request/response

The §10 SDK flow from `tests/test_v14_5.py`
(`TestSdkPrimitives::test_spec_example_flow`: connect → capabilities →
observe → execute). Wire forms, exactly as `AipMessage.to_dict()`
produces and `parse_message()` accepts:

Request (client → core):

```json
{
  "aip_version": "1.0",
  "type": "execute",
  "id": "msg-00000004",
  "agent_id": "sdk-agent",
  "request_id": "",
  "ts": 1234.0,
  "payload": {
    "action": "click",
    "verify": true,
    "params": { "intent": "open my research project" }
  }
}
```

Success reply (core → client, `RESULT`):

```json
{
  "aip_version": "1.0",
  "type": "result",
  "id": "msg-00000001",
  "agent_id": "airmouse",
  "request_id": "msg-00000004",
  "ts": 1234.001,
  "payload": {
    "ok": true,
    "request_id": "msg-00000004",
    "action_id": "act-msg-00000001",
    "verification": {
      "verified": true,
      "action_id": "act-msg-00000001",
      "message": "simulated execution verified"
    },
    "detail": "executed through shared action layer"
  }
}
```

Permission-denied reply (no permission engine attached — fails closed):

```json
{
  "aip_version": "1.0",
  "type": "error",
  "id": "msg-00000002",
  "agent_id": "airmouse",
  "request_id": "msg-00000004",
  "ts": 1234.001,
  "payload": {
    "code": "permission_denied",
    "message": "permission 'mouse.click' decision 'ask'",
    "request_id": "msg-00000004"
  }
}
```

(Exact `ts`/id values vary; the shape is normative. The denied variant
is the behaviour asserted by
`test_endpoint_fails_closed_without_permission_engine`.)

## 8. Transports

AIP is transport-agnostic; the verified transports are local-only:

* **In-process handler** — an endpoint object exposing
  `handle(AipMessage) -> AipMessage` (Python `AipEndpoint`) or a
  callable `handler(wire: str) -> wire: str` (Python `airmouse_agent`,
  JS `InProcessTransport`). Used by embedded runtimes and the test
  suites. Zero latency beyond serialization (SDK execute budget < 50 ms
  in-process).
* **stdio JSON-lines** — the client spawns the AirMouse core as a child
  process (`"stdio://<command>"` in `airmouse_agent`; `StdioTransport`
  in JS) and writes one JSON envelope per line to stdin, reading one
  reply line from stdout. Lazy subprocess import (§11 stdlib-only
  budget). No network sockets are involved anywhere.
* A local-socket transport is possible by design (same envelope) but is
  **not implemented and not verified**; any future transport must keep
  the same fail-closed parsing rules.

## 9. Conformance checklist

An AIP endpoint conforms when it:

1. rejects unknown envelope fields, unknown types and wrong majors;
2. validates all payloads against the 12 schemas, rejecting unknown
   fields;
3. enforces the 256 KB size cap;
4. never executes without a permission decision, and treats ASK as NO;
5. answers `discover` with a deterministic capability list;
6. correlates replies via `request_id`;
7. treats all payload strings as DATA (never instructions);
8. fails closed on every error path.
