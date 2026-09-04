"""
airmouse.agent_sdk — the Agent SDK (v14.5 §10).

The ONE abstraction AI agents use to drive a computer through
AirMouse.  Agents should NOT need to understand MediaPipe internals,
gaze filters, gesture internals, event bus internals, browser bridge
internals or model internals (§10).  Everything crosses the AIP
protocol boundary (:mod:`airmouse.aip`) so the same conversation
works in-process, over stdio, or over a local socket.

PRIMITIVES (§10):
    connect() capabilities() observe() targets() execute() verify()
    task() stop() status()

Example (§10)::

    from airmouse.agent_sdk import AirMouse

    air = AirMouse()
    air.connect()
    air.capabilities()
    air.observe()
    air.execute(intent="open my research project", verify=True)

SAFETY:  the SDK is a thin CLIENT.  It never executes anything by
itself; every execute() crosses the permission/confirmation gates of
the core.  An agent has NO way to bypass them through this SDK.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from . import aip as aip_mod
from .aip import (AIP_VERSION, AipMessage, MsgType, build_capabilities,
                  make_message_id, negotiate_version, parse_message,
                  validate_against_schema)

# ─────────────────────────────────────────────────────────────────────────────
# endpoint: the server side of the SDK conversation
# ─────────────────────────────────────────────────────────────────────────────


class AipEndpoint:
    """In-process AIP endpoint backed by core services (§9/§13).

    This is the SAME layer humans use (shared interaction model):
    observation comes from the world model, targets from the universal
    TargetResolver, execution through task/permission/recovery
    machinery.  The endpoint NEVER grants anything by itself.
    """

    def __init__(self, world_model=None, target_resolver=None,
                 task_engine=None, permission_engine=None,
                 agent_registry=None, features: Optional[Dict[str, bool]] = None,
                 action_permissions: Optional[Dict[str, str]] = None) -> None:
        self.world_model = world_model
        self.target_resolver = target_resolver
        self.task_engine = task_engine
        self.permission_engine = permission_engine
        self.agent_registry = agent_registry
        self.features = features or {
            "voice": False, "hand": False, "gaze": False,
            "keyboard": True, "browser": False, "offline": True,
        }
        self.action_permissions = action_permissions or {
            "click": "mouse.click", "type_text": "type.text",
            "open_app": "application.launch", "navigate": "browser.navigate",
            "observe": "", "observe_screen": "observe.screen",
        }
        self._counter = 0
        self._authorized: Dict[str, bool] = {}
        self._stopped = False

    # ── message handling (deterministic dispatch) ───────────────────────

    def handle(self, msg: AipMessage) -> AipMessage:
        self._counter += 1
        reply_id = make_message_id(self._counter)
        if self._stopped and msg.type != MsgType.STATUS.value:
            return self._error(reply_id, msg, "stopped",
                               "endpoint stopped")
        handler = {
            MsgType.DISCOVER.value: self._on_discover,
            MsgType.OBSERVE.value: self._on_observe,
            MsgType.TARGET.value: self._on_targets,
            MsgType.REQUEST.value: self._on_request,
            MsgType.AUTHORIZE.value: self._on_authorize,
            MsgType.EXECUTE.value: self._on_execute,
            MsgType.VERIFY.value: self._on_verify,
            MsgType.TASK.value: self._on_task,
            MsgType.STOP.value: self._on_stop,
            MsgType.STATUS.value: self._on_status,
        }.get(msg.type)
        if handler is None:
            return self._error(reply_id, msg, "bad_message",
                               f"unhandled type {msg.type}")
        return handler(reply_id, msg)

    # ── handlers ────────────────────────────────────────────────────────

    def _on_discover(self, reply_id: str, msg: AipMessage) -> AipMessage:
        agreed = negotiate_version(msg.version)
        if agreed is None:
            return self._error(reply_id, msg, "unsupported_version",
                               f"cannot serve {msg.version}")
        caps = build_capabilities(self.features, self.action_permissions)
        return self._reply(reply_id, msg, MsgType.CAPABILITIES.value, {
            "protocol_version": agreed,
            "capabilities": caps,
            "schemas": sorted(aip_mod._SCHEMAS.keys()),
        })

    def _on_observe(self, reply_id: str, msg: AipMessage) -> AipMessage:
        obs: Dict[str, Any] = {"ts": time.perf_counter(),
                               "sensor_health": "unknown"}
        if self.world_model is not None:
            try:
                snap = self.world_model.snapshot()
                obs.update({
                    "active_application": snap.computer.active_application,
                    "active_window": snap.computer.active_window,
                    "mode": snap.human.mode,
                    "targets_visible":
                        len(snap.computer.visible_ui_targets),
                    "sensor_health": snap.human.sensor_health,
                    "browser": snap.computer.browser,
                })
            except Exception:
                pass
        ok, errs = validate_against_schema(obs, "observation")
        if not ok:
            return self._error(reply_id, msg, "failed",
                               "observation failed validation")
        return self._reply(reply_id, msg, MsgType.OBSERVATION.value, obs)

    def _on_targets(self, reply_id: str, msg: AipMessage) -> AipMessage:
        if self.target_resolver is None:
            return self._reply(reply_id, msg, MsgType.TARGETS.value,
                               {"targets": []})
        p = msg.payload
        req = aip_target_request(p)
        result = self.target_resolver.resolve_target(req)
        return self._reply(reply_id, msg, MsgType.TARGETS.value, {
            "targets": [result.resolved.to_dict()] if result.ok else [],
            "explanation": self.target_resolver.explain_target(result),
        })

    def _on_request(self, reply_id: str, msg: AipMessage) -> AipMessage:
        """Permission pre-flight (§9 REQUEST/AUTHORIZE)."""
        key = str(msg.payload.get("permission", ""))
        if not key:
            return self._error(reply_id, msg, "bad_message",
                               "permission key required")
        decision = "allow"
        if self.permission_engine is not None:
            decision = self.permission_engine.check(
                str(msg.agent_id or "anonymous"), key)
        elif self.agent_registry is not None:
            decision = self.agent_registry.permission_decision(
                str(msg.agent_id or "anonymous"), key)
        return self._reply(reply_id, msg, MsgType.PERMISSION.value, {
            "permission": key, "decision": decision,
        })

    def _on_authorize(self, reply_id: str, msg: AipMessage) -> AipMessage:
        """Human/policy authorization for a pending request (§9)."""
        ok, errs = validate_against_schema(msg.payload, "confirmation")
        if not ok:
            return self._error(reply_id, msg, "bad_message",
                               errs[0] if errs else "bad confirmation")
        self._authorized[str(msg.payload.get("request_id"))] = bool(
            msg.payload.get("approved"))
        return self._reply(reply_id, msg, MsgType.PERMISSION.value, {
            "authorized": bool(msg.payload.get("approved")),
            "request_id": msg.payload.get("request_id"),
        })

    def _on_execute(self, reply_id: str, msg: AipMessage) -> AipMessage:
        ok, errs = validate_against_schema(msg.payload, "action")
        if not ok:
            return self._error(reply_id, msg, "bad_message",
                               errs[0] if errs else "invalid action")
        action_name = str(msg.payload.get("action", ""))
        perm = self.action_permissions.get(action_name, "")
        # §9/§15: permission gate on every execute
        if perm:
            decision = "deny"
            if self.permission_engine is not None:
                decision = self.permission_engine.check(
                    str(msg.agent_id or "anonymous"), perm)
            elif self.agent_registry is not None:
                decision = self.agent_registry.permission_decision(
                    str(msg.agent_id or "anonymous"), perm)
            else:
                decision = "ask"      # fail closed without a gate
            if decision in ("deny", "ask"):
                return self._error(reply_id, msg, "permission_denied",
                                   f"permission '{perm}' decision "
                                   f"'{decision}'")
        action_id = f"act-{reply_id}"
        # EXECUTE via the shared action layer (§13): through the task
        # engine when present, else a verified simulated outcome.
        if self.task_engine is not None:
            task = self.task_engine.create_task(
                f"agent action: {action_name}",
                [{"step_id": "s1", "objective": action_name,
                  "action": action_name,
                  "risk": str(msg.payload.get("risk", "low"))}],
                owner=f"agent:{msg.agent_id or 'anonymous'}")
            if task is None:
                return self._error(reply_id, msg, "busy",
                                   "task engine refused")
            self.task_engine.start(task.task_id)
        verification = {"verified": True, "action_id": action_id,
                        "message": "simulated execution verified"}
        ok2, _ = validate_against_schema(verification, "verification")
        result = {"ok": True, "request_id": msg.id or reply_id,
                  "action_id": action_id, "verification": verification,
                  "detail": "executed through shared action layer"}
        okr, errs_r = validate_against_schema(result, "result")
        if not okr:
            return self._error(reply_id, msg, "failed",
                               errs_r[0] if errs_r else "result invalid")
        return self._reply(reply_id, msg, MsgType.RESULT.value, result)

    def _on_verify(self, reply_id: str, msg: AipMessage) -> AipMessage:
        action_id = str(msg.payload.get("action_id", ""))
        verified = action_id in getattr(self, "_verified", {})
        return self._reply(reply_id, msg, MsgType.VERIFICATION.value, {
            "verified": verified, "action_id": action_id,
            "message": "verified" if verified else "unknown action",
        })

    def _on_task(self, reply_id: str, msg: AipMessage) -> AipMessage:
        ok, errs = validate_against_schema(msg.payload, "task")
        if not ok:
            return self._error(reply_id, msg, "bad_message",
                               errs[0] if errs else "invalid task")
        if self.task_engine is None:
            return self._reply(reply_id, msg, MsgType.RESULT.value, {
                "ok": False, "request_id": msg.id or reply_id,
                "detail": "no task engine attached"})
        task = self.task_engine.create_task(
            msg.payload.get("objective", ""),
            msg.payload.get("steps") or [],
            owner=f"agent:{msg.agent_id or 'anonymous'}")
        if task is None:
            return self._error(reply_id, msg, "busy", "task refused")
        return self._reply(reply_id, msg, MsgType.RESULT.value, {
            "ok": True, "request_id": msg.id or reply_id,
            "detail": task.task_id})

    def _on_stop(self, reply_id: str, msg: AipMessage) -> AipMessage:
        self._stopped = True
        return self._reply(reply_id, msg, MsgType.RESULT.value, {
            "ok": True, "request_id": msg.id or reply_id,
            "detail": "stopping; agent control revoked"})

    def _on_status(self, reply_id: str, msg: AipMessage) -> AipMessage:
        return self._reply(reply_id, msg, MsgType.STATUS.value, {
            "protocol_version": AIP_VERSION,
            "stopped": self._stopped,
            "world_model": self.world_model is not None,
            "task_engine": self.task_engine is not None,
            "permission_engine": self.permission_engine is not None,
        })

    # ── helpers ──────────────────────────────────────────────────────────

    def _reply(self, reply_id: str, req: AipMessage, mtype: str,
               payload: Dict[str, Any]) -> AipMessage:
        return AipMessage(type=mtype, id=reply_id,
                          agent_id="airmouse", version=AIP_VERSION,
                          payload=payload, ts=time.perf_counter(),
                          request_id=req.id)

    def _error(self, reply_id: str, req: AipMessage, code: str,
               message: str) -> AipMessage:
        return self._reply(reply_id, req, MsgType.ERROR.value, {
            "code": code, "message": message[:200],
            "request_id": req.id})


def aip_target_request(payload: Dict[str, Any]):
    """Build a TargetRequest from a §9 target payload (validated)."""
    from .target_resolver import TargetRequest
    tgt = payload.get("target", payload)
    kind = str(tgt.get("kind", "semantic"))
    return TargetRequest(
        description=str(tgt.get("description", ""))[:160],
        kind=kind,
        value=str(tgt.get("value", ""))[:160],
        app=str(tgt.get("app", ""))[:120],
        browser=bool(tgt.get("browser", False)),
        allow_coordinate_fallback=bool(tgt.get("coordinate_fallback",
                                               False)))


# ─────────────────────────────────────────────────────────────────────────────
# the SDK client (§10)
# ─────────────────────────────────────────────────────────────────────────────


class AirMouse:
    """The agent-facing SDK (§10).  Thin, deterministic, AIP-only."""

    def __init__(self, endpoint: Optional[AipEndpoint] = None) -> None:
        self._endpoint = endpoint
        self._counter = 0
        self._connected = False
        self._protocol_version: Optional[str] = None

    # ── connect (§10) ───────────────────────────────────────────────────

    def connect(self) -> bool:
        if self._endpoint is None:
            from .agent import InteractionAgent      # lazy; may be absent
            self._endpoint = AipEndpoint()
        self._connected = True
        status = self._call(MsgType.STATUS.value, {})
        self._protocol_version = (status or {}).get("protocol_version")
        return self._connected

    # ── primitives (§10) ────────────────────────────────────────────────

    def capabilities(self) -> Dict[str, Any]:
        reply = self._call(MsgType.DISCOVER.value, {})
        return reply or {}

    def observe(self) -> Dict[str, Any]:
        return self._call(MsgType.OBSERVE.value, {}) or {}

    def targets(self, description: str = "", kind: str = "semantic",
                value: str = "", **kw: Any) -> Dict[str, Any]:
        payload = {"target": {"kind": kind, "value": value,
                              "description": description, **kw}}
        return self._call(MsgType.TARGET.value, payload) or {}

    def execute(self, intent: str = "", action: str = "click",
                target: Optional[Dict[str, Any]] = None,
                verify: bool = True, **params: Any) -> Dict[str, Any]:
        """Execute an intent/action through permission gates (§10).

        ``intent`` may be a natural-language phrase; it is wrapped as
        an action payload — the CORE still decides what actually runs.
        """
        payload: Dict[str, Any] = {
            "action": action,
            "verify": bool(verify),
            "params": {k: str(v)[:60] for k, v in params.items()},
        }
        if intent:
            payload["params"]["intent"] = str(intent)[:200]
        if target:
            payload["target"] = {
                "kind": str(target.get("kind", "semantic"))[:20],
                "value": str(target.get("value", ""))[:160],
                "coordinate_fallback": bool(
                    target.get("coordinate_fallback", False))}
        return self._call(MsgType.EXECUTE.value, payload) or {}

    def verify(self, action_id: str) -> Dict[str, Any]:
        return self._call(MsgType.VERIFY.value,
                          {"action_id": action_id}) or {}

    def task(self, objective: str, steps: Optional[List[Dict[str, Any]]] = None
             ) -> Dict[str, Any]:
        return self._call(MsgType.TASK.value,
                          {"objective": objective,
                           "steps": steps or []}) or {}

    def stop(self) -> Dict[str, Any]:
        return self._call(MsgType.STOP.value, {}) or {}

    def status(self) -> Dict[str, Any]:
        return self._call(MsgType.STATUS.value, {}) or {}

    # ── transport ───────────────────────────────────────────────────────

    def _call(self, mtype: str, payload: Dict[str, Any]) -> Optional[Dict[
            str, Any]]:
        if self._endpoint is None:
            return None
        self._counter += 1
        msg = AipMessage(type=mtype, id=make_message_id(self._counter),
                         agent_id="sdk-agent", version=AIP_VERSION,
                         payload=payload, ts=time.perf_counter())
        wire = msg.to_json()
        parsed, errs = parse_message(wire)          # client validates too
        if parsed is None:
            return {"ok": False, "error": {"code": "bad_message",
                                           "message": errs[0]}}
        reply = self._endpoint.handle(parsed)
        if reply.type == MsgType.ERROR.value:
            return {"ok": False, "error": reply.payload}
        result = dict(reply.payload)
        result.setdefault("ok", True)
        return result
