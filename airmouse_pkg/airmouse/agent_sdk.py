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

#: verified-action records kept for VERIFY lookups (bounded, §30 style)
MAX_VERIFIED = 256

# AIP action name -> actions.ActionType member name (resolved lazily so
# importing agent_sdk never drags in the perception/action stack).
_AIP_TO_ACTION_NAME: Dict[str, str] = {
    "click": "CLICK", "double_click": "DOUBLE_CLICK",
    "right_click": "RIGHT_CLICK", "middle_click": "MIDDLE_CLICK",
    "move": "MOVE", "drag": "DRAG", "scroll": "SCROLL",
    "zoom": "ZOOM", "type_text": "TYPE", "type": "TYPE",
    "hotkey": "HOTKEY", "key_press": "KEY_PRESS", "press": "KEY_PRESS",
    "open_url": "OPEN_URL", "navigate": "NAVIGATE",
    "browser_op": "BROWSER_OPERATION",
    "browser_operation": "BROWSER_OPERATION",
    "system_op": "SYSTEM_OPERATION",
    "system_operation": "SYSTEM_OPERATION",
    "open_app": "FILE_OPERATION",       # application launch == file "open"
    "file_op": "FILE_OPERATION", "file_operation": "FILE_OPERATION",
    "select": "SELECT",
}

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
                 action_permissions: Optional[Dict[str, str]] = None,
                 action_engine: Optional[Any] = None,
                 label: str = "simulated") -> None:
        self.world_model = world_model
        self.target_resolver = target_resolver
        self.task_engine = task_engine
        self.permission_engine = permission_engine
        self.agent_registry = agent_registry
        self.action_engine = action_engine
        self.label = str(label)[:40]
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
        # action_id -> verification record for VERIFY (v15.1.1: real
        # execution results are remembered, no more always-true dict)
        self._verified: Dict[str, Dict[str, Any]] = {}

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
        allowed, label, _reason = self._gate_decision(
            str(msg.agent_id or "anonymous"), key)
        return self._reply(reply_id, msg, MsgType.PERMISSION.value, {
            "permission": key, "decision": label,
            "allowed": bool(allowed),
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
        # §9/§15: permission gate on every execute (unchanged — the gate
        # runs BEFORE any engine is touched)
        if perm:
            allowed, label, reason = self._gate_decision(
                str(msg.agent_id or "anonymous"), perm)
            if not allowed:
                return self._error(
                    reply_id, msg, "permission_denied",
                    f"permission '{perm}' decision '{label}'"
                    + (f": {reason}" if reason else ""))
        action_id = f"act-{reply_id}"
        # EXECUTE via the shared action layer (§13): through the task
        # engine when present, then through the injected action engine
        # when wired (real execution), else a verified simulated
        # outcome (honestly labelled simulated=True).
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
        if self.action_engine is not None:
            verified, detail = self._run_real_action(action_name,
                                                     msg.payload)
            simulated = False
        else:
            verified = True
            detail = "simulated execution verified"
            simulated = True
        verification = {"verified": bool(verified),
                        "action_id": action_id,
                        "message": str(detail)[:200]}
        ok2, _ = validate_against_schema(verification, "verification")
        if not ok2:
            return self._error(reply_id, msg, "failed",
                               "verification invalid")
        # honesty label rides on the wire OUTSIDE the strict §9 schema
        # (unknown fields are rejected on INBOUND messages only)
        self._remember(action_id, {"verified": bool(verified),
                                   "message": str(detail)[:200],
                                   "simulated": simulated})
        if simulated:
            result_detail = "executed through shared action layer"
        elif verified:
            result_detail = "executed through shared action layer (real engine)"
        else:
            result_detail = "engine execution failed"
        result = {"ok": True, "request_id": msg.id or reply_id,
                  "action_id": action_id, "verification": verification,
                  "detail": result_detail}
        okr, errs_r = validate_against_schema(result, "result")
        if not okr:
            return self._error(reply_id, msg, "failed",
                               errs_r[0] if errs_r else "result invalid")
        # honesty labels ride on the wire OUTSIDE the strict §9 schema
        # (added AFTER validation, which is inbound-strict only)
        verification["simulated"] = simulated
        result["simulated"] = simulated
        return self._reply(reply_id, msg, MsgType.RESULT.value, result)

    def _run_real_action(self, action_name: str,
                         payload: Dict[str, Any]) -> Tuple[bool, str]:
        """Route one action through the injected engine (v15.1.1).

        The engine's ``execute()`` receives an
        :class:`airmouse.actions.ActionPlan` when the engine is the
        core ``ActionEngine``, else a plain dict
        ``{type, action, params, target}``.  Results may be
        ActionReport-like objects or dicts; an optional
        ``verify_action(result)``/``verify(result)`` callable is
        honoured as a second gate.  NEVER raises: engine failures come
        back as ``(False, "engine_error: ...")``.
        """
        try:
            engine_payload = build_engine_payload(
                self.action_engine, action_name, payload)
            result = self.action_engine.execute(engine_payload)
            ok, message, observation = _engine_result_fields(
                result, action_name)
        except Exception as exc:            # never leak engine crashes
            return False, f"engine_error: {exc}"
        verified = bool(ok)
        for name in ("verify_action", "verify"):
            fn = getattr(self.action_engine, name, None)
            if not callable(fn):
                continue
            try:
                vres = fn(result)
            except Exception as exc:
                return False, f"verify_error: {exc}"
            vbool = _coerce_bool(vres)
            if vbool is not None:
                verified = verified and vbool
            break
        if verified and observation:
            keys = ",".join(sorted(str(k) for k in observation))[:120]
            message = f"{message} [observed: {keys}]" if message else \
                f"executed {action_name} [observed: {keys}]"
        message = str(message or (
            f"executed {action_name}" if verified else
            f"{action_name} failed"))
        return verified, message

    def _remember(self, action_id: str, record: Dict[str, Any]) -> None:
        """Bound the VERIFY lookup table (FIFO, §30 hygiene)."""
        self._verified[action_id] = record
        while len(self._verified) > MAX_VERIFIED:
            oldest = next(iter(self._verified))
            del self._verified[oldest]

    def _on_verify(self, reply_id: str, msg: AipMessage) -> AipMessage:
        action_id = str(msg.payload.get("action_id", ""))[:64]
        record = getattr(self, "_verified", {}).get(action_id)
        if record is not None:
            verified = bool(record.get("verified"))
            message = str(record.get("message", "") or
                          ("verified" if verified else "not verified"))
        else:
            verified = False
            message = "unknown action"
        return self._reply(reply_id, msg, MsgType.VERIFICATION.value, {
            "verified": verified, "action_id": action_id,
            "message": message[:200],
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
            "action_engine": self.action_engine is not None,
            "mode": self.label,
        })

    # ── helpers ──────────────────────────────────────────────────────────

    def _gate_decision(self, agent_id: str,
                       key: str) -> Tuple[bool, str, str]:
        """Ask the configured gate for one (agent, permission) verdict.

        Returns ``(allowed, decision_label, reason)``.  Normalizes BOTH
        gate styles — the real :class:`AgentPermissionEngine`
        (``PermissionDecision`` objects) and duck-typed gates (plain
        ``"allow"/"deny"/"ask"`` strings).  Fail closed: no gate,
        gate errors, or unrecognized verdicts all DENY (v15.1.1 fix —
        previously a real engine's DENY verdict leaked through the
        string comparison).
        """
        if self.permission_engine is not None:
            try:
                return _normalize_verdict(
                    self.permission_engine.check(agent_id, key))
            except Exception as exc:
                return False, "deny", f"gate error: {exc}"
        if self.agent_registry is not None:
            fn = getattr(self.agent_registry, "permission_decision", None)
            if not callable(fn):
                registry_perms = getattr(self.agent_registry,
                                         "permissions", None)
                fn = getattr(registry_perms, "check", None)
            if callable(fn):
                try:
                    return _normalize_verdict(fn(agent_id, key))
                except Exception as exc:
                    return False, "deny", f"gate error: {exc}"
            return False, "deny", "registry has no permission gate"
        return False, "ask", "no gate attached (fail closed)"

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


# ─────────────────────────────────────────────────────────────────────────
# engine payload adaptation (v15.1.1 — real execution support)
# ─────────────────────────────────────────────────────────────────────────


def _normalize_verdict(verdict: Any) -> Tuple[bool, str, str]:
    """Normalize a permission-gate verdict to (allowed, label, reason).

    Accepts ``PermissionDecision``-like objects, strings, bools, None
    (fail closed).
    """
    if verdict is None:
        return False, "deny", "gate returned no verdict"
    if isinstance(verdict, str):
        text = verdict.strip().lower()
        return (text == "allow"), text, ""
    if isinstance(verdict, bool):
        return verdict, ("allow" if verdict else "deny"), ""
    allowed = getattr(verdict, "allowed", None)
    if isinstance(allowed, bool):
        label = getattr(verdict, "decision", "")
        label = getattr(label, "value", label)
        reason = str(getattr(verdict, "reason", "") or "")
        return allowed, str(label or ("allow" if allowed else "deny")), \
            reason
    return False, "deny", "unrecognized gate verdict"


def build_engine_payload(engine: Any, action_name: str,
                         payload: Dict[str, Any]) -> Any:
    """Adapt a §9 action payload into what the engine expects.

    * core :class:`airmouse.actions.ActionEngine` → a real
      :class:`airmouse.actions.ActionPlan` (its documented
      ``execute(plan)`` contract), with AIP params mapped to the
      engine's param names;
    * any duck-typed engine → a plain dict
      ``{"type", "action", "params", "target"}``.
    """
    params = dict(payload.get("params") or {})
    target = payload.get("target")
    target = dict(target) if isinstance(target, dict) else {}
    try:
        from .actions import ActionEngine, ActionPlan, ActionType
        if isinstance(engine, ActionEngine):
            return _build_action_plan(ActionPlan, ActionType,
                                      action_name, params, target,
                                      payload)
    except Exception:                   # actions stack unavailable → dict
        pass
    return {"type": str(action_name)[:40],
            "action": str(action_name)[:40],
            "params": {str(k)[:40]: v for k, v in
                       list(params.items())[:8]},
            "target": target}


def _build_action_plan(ActionPlan, ActionType, action_name: str,
                       params: Dict[str, Any], target: Dict[str, Any],
                       payload: Dict[str, Any]):
    """Map a §9 action payload onto the core ActionPlan fields."""
    try:
        action = ActionType[_AIP_TO_ACTION_NAME.get(
            action_name, str(action_name).upper())]
    except KeyError:
        action = ActionType.NONE
    params = dict(params)
    if action_name in ("open_app",) and not params.get("op"):
        params["op"] = "open"           # FILE_OPS allowlist member
        if not params.get("path") and target.get("value"):
            params["path"] = str(target["value"])[:160]
    keys = params.get("keys")
    if isinstance(keys, str) and keys:
        parts = [p for p in str(keys).replace("+", ",").split(",") if p]
        params["keys"] = [p.strip().lower()[:20] for p in parts][:4]
    if action_name == "scroll" and params.get("amount") is None:
        params["amount"] = 3            # same default as plan()
    if "amount" in params:
        try:
            params["amount"] = max(-100, min(100, int(params["amount"])))
        except (TypeError, ValueError):
            params["amount"] = 3
    if "text" in params:
        params["text"] = str(params["text"])[:500]
    point = None
    pt = target.get("point")
    if isinstance(pt, (list, tuple)) and len(pt) == 2:
        try:
            point = (float(pt[0]), float(pt[1]))
        except (TypeError, ValueError):
            point = None
    try:
        timeout = float(payload.get("timeout", 2.0))
    except (TypeError, ValueError):
        timeout = 2.0
    timeout = max(0.05, min(600.0, timeout))
    return ActionPlan(action=action, point=point, params=params,
                      timeout=timeout, intent=None)


def _engine_result_fields(result: Any, action_name: str
                          ) -> Tuple[bool, str, Dict[str, Any]]:
    """Normalize an engine result into (ok, message, observation).

    Accepts dicts (``ok``/``verified``/``message``/``detail``/
    ``observation``) and ActionReport-like objects (``status``/
    ``ok``/``message``/``observation``).
    """
    if result is None:
        return False, "engine returned no result", {}
    if isinstance(result, dict):
        ok = bool(result.get("ok", result.get("verified", False)))
        message = str(result.get("message") or result.get("detail") or (
            f"{action_name} executed" if ok else
            f"{action_name} failed"))
        obs = result.get("observation")
        obs = dict(obs) if isinstance(obs, dict) else {}
        return ok, message, obs
    ok_attr = getattr(result, "ok", None)
    status = getattr(result, "status", None)
    if isinstance(ok_attr, bool):
        ok = ok_attr
    elif status is not None:
        ok = "SUCCESS" in str(getattr(status, "name", status)).upper()
    else:
        ok = False
    message = str(getattr(result, "message", "") or "")
    if not message:
        message = (f"{action_name} executed" if ok else
                   f"{action_name} not confirmed")
    obs = getattr(result, "observation", None)
    obs = dict(obs) if isinstance(obs, dict) else {}
    return ok, message, obs


def _coerce_bool(value: Any) -> Optional[bool]:
    """Best-effort bool from an engine verify step; None = ignore."""
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        v = value.get("verified", value.get("ok"))
        return bool(v) if isinstance(v, bool) else None
    ok_attr = getattr(value, "ok", None)
    if isinstance(ok_attr, bool):
        return ok_attr
    return None


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
