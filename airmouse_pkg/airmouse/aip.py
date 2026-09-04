"""
airmouse.aip — AirMouse Interaction Protocol (AIP) v1 (v14.5 §9).

A tiny, deterministic, documented, versioned, language-neutral,
local-first, secure, permission-aware protocol between AirMouse and
its consumers (humans' tools, AI agents, multi-agent runtimes).

CORE CONCEPTS (§9):

    DISCOVER    what can this AirMouse do?        (capability discovery)
    OBSERVE     what is true right now?           (read-only)
    TARGET      what can I act on?                (targets query)
    REQUEST     I want to do X — may I?           (permission gate)
    AUTHORIZE   permission/confirmation decision  (human or policy)
    EXECUTE     do X now                          (permission required)
    VERIFY      did X actually happen?            (evidence)
    RESULT      the outcome envelope              (success/failure/recovery)

MESSAGE SCHEMAS (§9):  capability, observation, target, intent, action,
    task, permission, confirmation, verification, error, recovery,
    result — JSON-schema-style dicts validated by a small strict
    validator (fail-closed: unknown fields rejected).

SECURITY (§23/§30):  every inbound message is schema-validated;
    unknown types/fields/versions are REJECTED, not coerced.  Size
    caps everywhere.  No code, no shell, no URLs fetched.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

AIP_VERSION = "1.0"
SUPPORTED_MAJOR = 1
MAX_MESSAGE_BYTES = 256 * 1024
MAX_ARRAY = 256
MAX_STR = 4096
MAX_ID = 64

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,63}$")


class MsgType(enum.Enum):
    DISCOVER = "discover"
    CAPABILITIES = "capabilities"
    OBSERVE = "observe"
    OBSERVATION = "observation"
    TARGET = "target"
    TARGETS = "targets"
    REQUEST = "request"
    AUTHORIZE = "authorize"
    PERMISSION = "permission"
    EXECUTE = "execute"
    VERIFY = "verify"
    VERIFICATION = "verification"
    RESULT = "result"
    TASK = "task"
    CONFIRMATION = "confirmation"
    ERROR = "error"
    RECOVERY = "recovery"
    STOP = "stop"
    STATUS = "status"


# ─────────────────────────────────────────────────────────────────────────────
# JSON-schema-style definitions (§9 — documented, versioned)
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "capability": {
        "type": "object",
        "required": ["name", "available"],
        "properties": {
            "name": {"type": "string", "max": 64},
            "available": {"type": "boolean"},
            "kind": {"type": "string", "enum": ["modality", "action",
                                                "observation", "task"]},
            "risk": {"type": "string", "enum": ["none", "low", "medium",
                                                 "high", "destructive"]},
            "permission": {"type": "string", "max": 64},
            "note": {"type": "string", "max": 120},
        },
    },
    "observation": {
        "type": "object",
        "required": ["ts"],
        "properties": {
            "ts": {"type": "number", "min": 0},
            "active_application": {"type": "string", "max": 120},
            "active_window": {"type": "string", "max": 160},
            "mode": {"type": "string", "max": 40},
            "targets_visible": {"type": "integer", "min": 0, "max": 100000},
            "sensor_health": {"type": "string", "max": 20},
            "browser": {"type": "string", "max": 40},
        },
    },
    "target": {
        "type": "object",
        "required": ["kind", "value"],
        "properties": {
            "kind": {"type": "string", "enum": ["semantic", "accessibility",
                                                 "dom", "ocr", "vision",
                                                 "geometry", "coordinate"]},
            "value": {"type": "string", "max": 160},
            "app": {"type": "string", "max": 120},
            "confidence": {"type": "number", "min": 0, "max": 1},
            "point": {"type": "array", "items": "number", "len": 2},
            "coordinate_fallback": {"type": "boolean"},
        },
    },
    "intent": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "max": 80},
            "utterance": {"type": "string", "max": 300},
            "confidence": {"type": "number", "min": 0, "max": 1},
            "level": {"type": "string", "enum": ["command", "intent",
                                                  "task", "goal"]},
            "risk": {"type": "string", "enum": ["none", "low", "medium",
                                                 "high", "destructive"]},
        },
    },
    "action": {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "max": 40},
            "target": {"type": "ref", "ref": "target"},
            "params": {"type": "object", "max_entries": 8},
            "verify": {"type": "boolean"},
            "timeout": {"type": "number", "min": 0.05, "max": 600},
            "risk": {"type": "string", "enum": ["none", "low", "medium",
                                                 "high", "destructive"]},
        },
    },
    "task": {
        "type": "object",
        "required": ["objective"],
        "properties": {
            "objective": {"type": "string", "max": 200},
            "steps": {"type": "array", "items": "object", "max": 64},
            "owner": {"type": "string", "max": 40},
            "task_id": {"type": "string", "max": 64},
        },
    },
    "permission": {
        "type": "object",
        "required": ["key", "decision"],
        "properties": {
            "key": {"type": "string", "max": 64},
            "decision": {"type": "string", "enum": ["allow", "deny",
                                                     "ask", "allow_once",
                                                     "allow_session",
                                                     "allow_pattern"]},
            "agent_id": {"type": "string", "max": 64},
            "reason": {"type": "string", "max": 160},
        },
    },
    "confirmation": {
        "type": "object",
        "required": ["request_id", "approved"],
        "properties": {
            "request_id": {"type": "string", "max": 64},
            "approved": {"type": "boolean"},
            "by": {"type": "string", "max": 40},
            "scope": {"type": "string", "enum": ["once", "session",
                                                  "pattern"]},
        },
    },
    "verification": {
        "type": "object",
        "required": ["verified"],
        "properties": {
            "verified": {"type": "boolean"},
            "action_id": {"type": "string", "max": 64},
            "message": {"type": "string", "max": 200},
            "checks": {"type": "array", "items": "string", "max": 16},
        },
    },
    "error": {
        "type": "object",
        "required": ["code", "message"],
        "properties": {
            "code": {"type": "string", "enum": ["bad_message",
                                                 "unsupported_version",
                                                 "permission_denied",
                                                 "not_found", "conflict",
                                                 "timeout", "failed",
                                                 "busy", "stopped"]},
            "message": {"type": "string", "max": 200},
            "request_id": {"type": "string", "max": 64},
        },
    },
    "recovery": {
        "type": "object",
        "required": ["strategy"],
        "properties": {
            "strategy": {"type": "string", "max": 40},
            "rounds": {"type": "integer", "min": 0, "max": 64},
            "outcome": {"type": "string", "max": 40},
            "human_message": {"type": "string", "max": 200},
        },
    },
    "result": {
        "type": "object",
        "required": ["ok", "request_id"],
        "properties": {
            "ok": {"type": "boolean"},
            "request_id": {"type": "string", "max": 64},
            "action_id": {"type": "string", "max": 64},
            "verification": {"type": "ref", "ref": "verification"},
            "recovery": {"type": "ref", "ref": "recovery"},
            "error": {"type": "ref", "ref": "error"},
            "detail": {"type": "string", "max": 200},
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# strict mini-validator (fail-closed, deterministic)
# ─────────────────────────────────────────────────────────────────────────────


def validate_against_schema(data: Any, schema_name: str) -> Tuple[bool, List[str]]:
    """Validate ``data`` against a §9 schema.  Returns (ok, errors).

    Unknown properties are REJECTED (additionalProperties=false by
    design — §23 fail-closed).
    """
    schema = _SCHEMAS.get(schema_name)
    if schema is None:
        return False, [f"unknown schema: {schema_name}"]
    errors: List[str] = []
    _validate_node(data, schema, errors, path=schema_name)
    return (not errors), errors


def _validate_node(data: Any, schema: Dict[str, Any], errors: List[str],
                   path: str) -> None:
    t = schema.get("type")
    if t == "object":
        if not isinstance(data, dict):
            errors.append(f"{path}: expected object")
            return
        for req in schema.get("required", ()):
            if req not in data:
                errors.append(f"{path}.{req}: missing required field")
        props = schema.get("properties")
        if props is not None:
            # strict object: unknown fields REJECTED (fail-closed §23)
            for key in data:
                if key not in props:
                    errors.append(f"{path}.{key}: unknown field rejected")
            for key, sub in props.items():
                if key in data:
                    _validate_field(data[key], sub, errors,
                                    f"{path}.{key}")
        else:
            # open string-map object (e.g. action.params): bounded
            max_entries = schema.get("max_entries", 16)
            if len(data) > int(max_entries):
                errors.append(f"{path}: too many entries")
            for key, value in list(data.items())[:int(max_entries) + 1]:
                if not isinstance(key, str) or len(key) > 40:
                    errors.append(f"{path}: invalid key")
                elif not isinstance(value, (str, int, float, bool)):
                    errors.append(f"{path}.{key[:20]}: expected scalar")
        return
    elif t == "array":
        if not isinstance(data, list):
            errors.append(f"{path}: expected array")
            return
        if "len" in schema and len(data) != int(schema["len"]):
            errors.append(f"{path}: expected length {schema['len']}")
        if len(data) > int(schema.get("max", MAX_ARRAY)):
            errors.append(f"{path}: array too long")
        items = schema.get("items")
        if items == "number":
            for i, v in enumerate(data):
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    errors.append(f"{path}[{i}]: expected number")
        elif items == "string":
            for i, v in enumerate(data):
                if not isinstance(v, str):
                    errors.append(f"{path}[{i}]: expected string")
        elif items == "object":
            for i, v in enumerate(data):
                if not isinstance(v, dict):
                    errors.append(f"{path}[{i}]: expected object")
    elif t == "ref":
        sub_ok, sub_errors = validate_against_schema(
            data, str(schema.get("ref")))
        errors.extend(sub_errors)
    else:
        _validate_field(data, schema, errors, path)


def _validate_field(value: Any, spec: Dict[str, Any], errors: List[str],
                    path: str) -> None:
    t = spec.get("type")
    if t == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string")
            return
        if len(value) > int(spec.get("max", MAX_STR)):
            errors.append(f"{path}: string too long")
        if "enum" in spec and value not in spec["enum"]:
            errors.append(f"{path}: '{value[:24]}' not in allowed values")
    elif t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}: expected number")
            return
        if "min" in spec and value < spec["min"]:
            errors.append(f"{path}: below minimum")
        if "max" in spec and value > spec["max"]:
            errors.append(f"{path}: above maximum")
    elif t == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: expected integer")
            return
        if "min" in spec and value < spec["min"]:
            errors.append(f"{path}: below minimum")
        if "max" in spec and value > spec["max"]:
            errors.append(f"{path}: above maximum")
    elif t == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: expected boolean")
    elif t == "array":
        _validate_node(value, spec, errors, path)
    elif t == "ref":
        _validate_node(value, {"type": "ref",
                               "ref": spec.get("ref")}, errors, path)
    elif t == "object":
        _validate_node(value, spec, errors, path)


def schemas_document() -> Dict[str, Any]:
    """The documented schema set (§9 — for docs and negotiation)."""
    return {"aip_version": AIP_VERSION, "schemas": _SCHEMAS}


# ─────────────────────────────────────────────────────────────────────────────
# message envelope
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AipMessage:
    """One protocol message (§9 envelope)."""

    type: str = MsgType.STATUS.value
    id: str = ""
    agent_id: str = ""
    version: str = AIP_VERSION
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0
    request_id: str = ""            # correlation

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aip_version": self.version,
            "type": self.type,
            "id": self.id[:MAX_ID],
            "agent_id": self.agent_id[:MAX_ID],
            "request_id": self.request_id[:MAX_ID],
            "ts": round(float(self.ts or 0.0), 6),
            "payload": self.payload,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def make_message_id(counter: int) -> str:
    return f"msg-{counter:08d}"


def parse_message(raw: Any) -> Tuple[Optional[AipMessage], List[str]]:
    """Strict inbound parse (§23 fail-closed).

    Accepts a JSON string or dict; returns (message, errors).  Errors
    are ALWAYS returned alongside a None message when anything is off:
    wrong version, unknown type, unknown fields, oversized payloads.
    """
    errors: List[str] = []
    try:
        if isinstance(raw, (bytes, bytearray)):
            if len(raw) > MAX_MESSAGE_BYTES:
                return None, ["message too large"]
            raw = raw.decode("utf-8", errors="strict")
        if isinstance(raw, str):
            if len(raw) > MAX_MESSAGE_BYTES:
                return None, ["message too large"]
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            return None, ["message must be a JSON object"]
        allowed = {"aip_version", "type", "id", "agent_id", "request_id",
                   "ts", "payload"}
        unknown = [k for k in raw if k not in allowed]
        if unknown:
            return None, [f"unknown envelope fields: {sorted(unknown)[:4]}"]
        version = str(raw.get("aip_version", ""))
        if not version:
            return None, ["missing aip_version"]
        major = version.split(".")[0]
        if not major.isdigit() or int(major) != SUPPORTED_MAJOR:
            return None, [f"unsupported aip_version: {version}"]
        mtype = str(raw.get("type", ""))
        try:
            MsgType(mtype)
        except ValueError:
            return None, [f"unknown message type: {mtype[:24]}"]
        agent_id = str(raw.get("agent_id", ""))
        if agent_id and not _ID_RE.match(agent_id):
            return None, ["invalid agent_id"]
        mid = str(raw.get("id", ""))
        if not mid:
            return None, ["missing id"]
        if not _ID_RE.match(mid):
            return None, ["invalid id"]
        payload = raw.get("payload", {})
        if not isinstance(payload, dict):
            return None, ["payload must be an object"]
        if len(json.dumps(payload)) > MAX_MESSAGE_BYTES:
            return None, ["payload too large"]
        ts = raw.get("ts", 0.0)
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            return None, ["invalid ts"]
        msg = AipMessage(type=mtype, id=mid, agent_id=agent_id,
                         version=version, payload=payload,
                         ts=float(ts),
                         request_id=str(raw.get("request_id", "")))
        return msg, []
    except json.JSONDecodeError:
        return None, ["invalid JSON"]
    except UnicodeDecodeError:
        return None, ["invalid encoding"]
    except Exception:
        return None, ["message parse failed"]


# ─────────────────────────────────────────────────────────────────────────────
# version negotiation (§9)
# ─────────────────────────────────────────────────────────────────────────────


def negotiate_version(offered: str) -> Optional[str]:
    """Agree on a protocol version.  Rule: same MAJOR required; we
    serve our exact AIP_VERSION within that major.  None = no
    agreement (caller must fail closed)."""
    try:
        major = str(offered).split(".")[0]
        if not major.isdigit() or int(major) != SUPPORTED_MAJOR:
            return None
        return AIP_VERSION
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# capability discovery (§9)
# ─────────────────────────────────────────────────────────────────────────────


def build_capabilities(features: Dict[str, bool],
                       actions: Dict[str, str]) -> List[Dict[str, Any]]:
    """Deterministic capability list for DISCOVER (§9).

    ``features``: name -> available.  ``actions``: action -> permission.
    """
    caps: List[Dict[str, Any]] = []
    for name in sorted(features):
        caps.append({"name": name, "available": bool(features[name]),
                     "kind": "modality"})
    for action in sorted(actions):
        caps.append({"name": action, "available": True, "kind": "action",
                     "permission": actions[action]})
    caps.append({"name": AIP_VERSION, "available": True,
                 "kind": "observation", "note": "protocol version"})
    caps.sort(key=lambda c: c["name"])
    return caps
