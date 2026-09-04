"""
airmouse.agents — Multi-Agent Infrastructure (v15 §12).

First-class support for MANY agents sharing one computer through
AirMouse.  Every agent has:

    identity · permissions · capabilities · task ownership ·
    resource leases · priority · budgets · audit trail

SUPPORT (§12):  registration · capability discovery · task handoff ·
agent-to-agent communication · conflict resolution · resource locking ·
human override · emergency stop.

CONFLICT RULE (§12):  two agents may never simultaneously issue
conflicting computer actions — conflicting EXECUTE on the same locked
resource is refused while a lease is held; a deterministic priority
+ first-lease-wins policy resolves contention; humans always win
(§14).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .permissions import AgentPermissionEngine

MAX_AGENTS = 32
MAX_MESSAGE_QUEUE = 16
MAX_LEASES = 64
MAX_AUDIT = 400
MAX_NAME = 40
DEFAULT_LEASE_TTL = 30.0          # seconds
MAX_LEASE_TTL = 300.0
MAX_ACTION_WINDOW = 2000          # bounded per-agent rate window (§12)


class AgentState(enum.Enum):
    REGISTERED = "registered"
    ACTIVE = "active"
    WAITING = "waiting"            # waiting on lease/permission/human
    SUSPENDED = "suspended"        # by human override (§14)
    STOPPED = "stopped"


class LeaseState(enum.Enum):
    HELD = "held"
    RELEASED = "released"
    EXPIRED = "expired"


@dataclass
class AgentProfile:
    """§12 agent identity + attributes."""

    agent_id: str = ""
    name: str = ""
    kind: str = "agent"             # agent|human|service
    priority: int = 5               # 1 (highest) .. 9
    capabilities: Tuple[str, ...] = ()
    budgets: Dict[str, float] = field(default_factory=dict)  # per minute
    state: AgentState = AgentState.REGISTERED
    registered_wall: str = ""
    actions_used: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "kind": self.kind,
            "priority": self.priority,
            "capabilities": list(self.capabilities),
            "budgets": dict(sorted(self.budgets.items())),
            "state": self.state.value,
            "actions_used": self.actions_used,
        }


@dataclass
class ResourceLease:
    """§12 resource lock (one holder per resource at a time)."""

    lease_id: str = ""
    resource: str = ""              # e.g. "mouse", "window:doc1", "clipboard"
    agent_id: str = ""
    task_id: str = ""
    acquired_ts: float = 0.0
    ttl: float = DEFAULT_LEASE_TTL
    state: LeaseState = LeaseState.HELD

    def to_dict(self) -> Dict[str, Any]:
        return {"lease_id": self.lease_id, "resource": self.resource,
                "agent_id": self.agent_id, "task_id": self.task_id,
                "state": self.state.value, "ttl": self.ttl}


@dataclass
class AgentMessage:
    """§12 agent-to-agent envelope (data only, never commands)."""

    from_agent: str = ""
    to_agent: str = ""
    kind: str = "info"              # info|handoff|result|question
    body: str = ""
    task_id: str = ""
    ts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"from": self.from_agent, "to": self.to_agent,
                "kind": self.kind, "body": self.body[:160],
                "task_id": self.task_id}


@dataclass
class ConflictRecord:
    """§12 recorded conflict (for audit + explainability)."""

    resource: str = ""
    holder: str = ""
    challenger: str = ""
    resolution: str = ""            # lease_held|priority|human
    ts: float = 0.0


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _fmt_budget(value: Any) -> str:
    """Compact, deterministic budget rendering for denial reasons."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)[:20]
    return str(int(f)) if f.is_integer() else str(f)


class AgentRegistry:
    """§12 multi-agent registry + resource locks + conflict policy."""

    def __init__(self, permission_engine: Optional[
            AgentPermissionEngine] = None,
                 max_agents: int = MAX_AGENTS) -> None:
        self.permissions = permission_engine or AgentPermissionEngine()
        self.max_agents = max(2, min(int(max_agents), MAX_AGENTS))
        self._agents: Dict[str, AgentProfile] = {}
        self._leases: Dict[str, ResourceLease] = {}       # resource -> lease
        self._lease_seq = 0
        self._inboxes: Dict[str, List[AgentMessage]] = {}
        self._conflicts: List[ConflictRecord] = []
        self._audit: List[str] = []
        self._human_present = True
        # §12 budget bookkeeping: rolling action timestamps per agent
        # (only maintained for agents carrying max_actions_per_minute).
        self._action_times: Dict[str, List[float]] = {}

    # ── registration + discovery (§12) ──────────────────────────────────

    def register(self, agent_id: str, name: str = "",
                 kind: str = "agent", priority: int = 5,
                 capabilities: Tuple[str, ...] = (),
                 budgets: Optional[Dict[str, float]] = None) -> bool:
        try:
            agent_id = str(agent_id)[:MAX_NAME]
            if not agent_id or agent_id in self._agents:
                return False
            if len(self._agents) >= self.max_agents:
                return False
            if not (1 <= int(priority) <= 9):
                priority = 5
            self._agents[agent_id] = AgentProfile(
                agent_id=agent_id, name=str(name or agent_id)[:MAX_NAME],
                kind=str(kind)[:20],
                priority=int(priority),
                capabilities=tuple(str(c)[:40]
                                   for c in tuple(capabilities)[:24]),
                budgets={str(k)[:32]: max(0.0, float(v))
                         for k, v in list((budgets or {}).items())[:8]},
                state=AgentState.REGISTERED, registered_wall=_utcnow())
            self._inboxes[agent_id] = []
            self._audit.append(f"registered {agent_id} pri={priority}")
            return True
        except Exception:
            return False

    def unregister(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        self.release_all(agent_id)
        del self._agents[agent_id]
        self._inboxes.pop(agent_id, None)
        self._audit.append(f"unregistered {agent_id}")
        return True

    def set_state(self, agent_id: str, state: AgentState) -> bool:
        agent = self._agents.get(agent_id)
        if agent is None:
            return False
        agent.state = state
        return True

    def discover(self) -> List[Dict[str, Any]]:
        """§12 capability discovery (deterministic order)."""
        rows = [a.to_dict() for a in self._agents.values()]
        rows.sort(key=lambda d: (d["priority"], d["agent_id"]))
        return rows

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        agent = self._agents.get(agent_id)
        return agent.to_dict() if agent else None

    # ── resource leases (§12) ───────────────────────────────────────────

    def acquire(self, agent_id: str, resource: str,
                ttl: float = DEFAULT_LEASE_TTL,
                task_id: str = "") -> Optional[ResourceLease]:
        """Acquire an exclusive lease.  None = conflict (held by
        someone else) — the challenger must WAIT, never barge in."""
        agent = self._agents.get(agent_id)
        if agent is None or agent.state in (AgentState.SUSPENDED,
                                            AgentState.STOPPED):
            return None
        resource = str(resource)[:80]
        if not resource:
            return None
        held = self._leases.get(resource)
        if held is not None and held.state is LeaseState.HELD and \
                held.agent_id != agent_id:
            self._conflicts.append(ConflictRecord(
                resource=resource, holder=held.agent_id,
                challenger=agent_id, resolution="lease_held",
                ts=time.perf_counter()))
            self._audit.append(f"conflict {resource}: {agent_id} vs "
                               f"{held.agent_id} -> lease_held")
            self._set_waiting(agent_id)
            return None
        if held is not None and held.agent_id == agent_id:
            held.ttl = max(0.1, min(float(ttl), MAX_LEASE_TTL))
            return held                       # re-entrant refresh
        if len(self._leases) >= MAX_LEASES:
            self._gc_leases()
        self._lease_seq += 1
        lease = ResourceLease(
            lease_id=f"lease-{self._lease_seq:05d}", resource=resource,
            agent_id=agent_id, task_id=str(task_id)[:40],
            acquired_ts=time.perf_counter(),
            ttl=max(0.1, min(float(ttl), MAX_LEASE_TTL)))
        self._leases[resource] = lease
        self._set_state_if(agent_id, AgentState.WAITING, AgentState.ACTIVE)
        self._audit.append(f"lease {resource} -> {agent_id}")
        return lease

    def release(self, agent_id: str, resource: str) -> bool:
        lease = self._leases.get(str(resource)[:80])
        if lease is None or lease.agent_id != agent_id:
            return False
        lease.state = LeaseState.RELEASED
        del self._leases[lease.resource]
        self._audit.append(f"release {lease.resource} by {agent_id}")
        return True

    def release_all(self, agent_id: str) -> int:
        n = 0
        for res, lease in list(self._leases.items()):
            if lease.agent_id == agent_id:
                lease.state = LeaseState.RELEASED
                del self._leases[res]
                n += 1
        return n

    def holder(self, resource: str) -> Optional[str]:
        lease = self._leases.get(str(resource)[:80])
        if lease and lease.state is LeaseState.HELD:
            return lease.agent_id
        return None

    def _gc_leases(self) -> None:
        now = time.perf_counter()
        for res, lease in list(self._leases.items()):
            if now - lease.acquired_ts > lease.ttl:
                lease.state = LeaseState.EXPIRED
                del self._leases[res]
                self._audit.append(f"lease expired {res}")

    # ── conflicting action gate (§12) ───────────────────────────────────

    def authorize_action(self, agent_id: str, resource: str,
                         permission_key: str = "",
                         priority_bump: bool = False) -> Tuple[bool, str]:
        """The §12 gate used before ANY agent action on a resource.

        Order (§14-compatible): human presence + e-stop via
        permissions, then agent state, then resource lease, then
        permission engine.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            return False, "unknown agent"
        if agent.state is AgentState.STOPPED:
            return False, "agent stopped"
        if agent.state is AgentState.SUSPENDED:
            return False, "suspended by human override"
        held = self._leases.get(str(resource)[:80])
        if held is not None and held.state is LeaseState.HELD and \
                held.agent_id != agent_id:
            # conflict resolution: deterministic priority, but the
            # lease holder ALWAYS keeps the resource until release or
            # expiry — the challenger waits (never barges in).
            if priority_bump and agent.priority < \
                    self._agents.get(held.agent_id).priority if \
                    held.agent_id in self._agents else False:
                pass                  # priority never steals live leases
            self._conflicts.append(ConflictRecord(
                resource=resource, holder=held.agent_id,
                challenger=agent_id, resolution="lease_held",
                ts=time.perf_counter()))
            return False, (f"resource '{resource}' held by "
                           f"{held.agent_id}")
        if permission_key:
            decision = self.permissions.check(agent_id, permission_key)
            if not decision.allowed:
                return False, (f"permission {permission_key} -> "
                               f"{decision.decision.value}: "
                               f"{decision.reason}")
        # §12 budgets: enforced at the ONLY path that increments
        # actions_used.  Unset budgets (None/empty) change nothing.
        budget_reason = self._budget_check(agent)
        if budget_reason:
            self._audit.append(f"budget denied {agent_id}: "
                               f"{budget_reason}")
            return False, budget_reason
        agent.actions_used += 1
        if "max_actions_per_minute" in agent.budgets:
            now = time.perf_counter()
            window = self._action_window(agent_id)
            window.append(now)
        self._set_state_if(agent_id, AgentState.WAITING, AgentState.ACTIVE)
        return True, "authorized"

    def _budget_check(self, agent: AgentProfile) -> str:
        """§12 budget gate.  Returns "" when the action is within
        budget, else a denial reason starting with
        "budget_exhausted".  Fails CLOSED on malformed budgets."""
        try:
            budgets = agent.budgets or {}
            max_total = budgets.get("max_actions")
            if max_total is not None and \
                    agent.actions_used + 1 > float(max_total):
                return (f"budget_exhausted: max_actions="
                        f"{_fmt_budget(max_total)} "
                        f"(used {agent.actions_used})")
            max_rate = budgets.get("max_actions_per_minute")
            if max_rate is not None:
                window = self._action_window(agent.agent_id)
                if len(window) + 1 > float(max_rate):
                    return (f"budget_exhausted: "
                            f"max_actions_per_minute="
                            f"{_fmt_budget(max_rate)}")
            return ""
        except Exception:
            return "budget_exhausted: malformed budget"

    def _action_window(self, agent_id: str) -> List[float]:
        """Bounded rolling 60s window of action timestamps."""
        now = time.perf_counter()
        window = [t for t in self._action_times.get(agent_id, [])
                  if now - t < 60.0]
        self._action_times[agent_id] = window[-MAX_ACTION_WINDOW:]
        return self._action_times[agent_id]

    # ── communication + handoff (§12) ───────────────────────────────────

    def send(self, from_agent: str, to_agent: str, kind: str,
             body: str, task_id: str = "") -> bool:
        """Agent-to-agent message.  Messages are DATA (§30): never
        executed, never parsed as instructions."""
        if from_agent not in self._agents or to_agent not in self._inboxes:
            return False
        if kind not in ("info", "handoff", "result", "question"):
            kind = "info"
        inbox = self._inboxes[to_agent]
        if len(inbox) >= MAX_MESSAGE_QUEUE:
            inbox.pop(0)
        inbox.append(AgentMessage(from_agent=from_agent, to_agent=to_agent,
                                  kind=kind, body=str(body)[:400],
                                  task_id=str(task_id)[:40],
                                  ts=time.perf_counter()))
        return True

    def inbox(self, agent_id: str, clear: bool = False) -> List[Dict[str,
                                                                      Any]]:
        rows = [m.to_dict() for m in self._inboxes.get(agent_id, [])]
        if clear:
            self._inboxes[agent_id] = []
        return rows

    def handoff(self, from_agent: str, to_agent: str, resource: str,
                task_id: str = "") -> bool:
        """§12 task handoff: release + reacquire + notify."""
        if from_agent not in self._agents or to_agent not in self._agents:
            return False
        if not self.release(from_agent, resource):
            if self.holder(resource) not in (None, from_agent):
                return False
        lease = self.acquire(to_agent, resource, task_id=task_id)
        if lease is None:
            return False
        self.send(from_agent, to_agent, "handoff",
                  f"handed off {resource}", task_id)
        self._audit.append(f"handoff {resource}: {from_agent} -> "
                           f"{to_agent}")
        return True

    # ── human authority (§14) ───────────────────────────────────────────

    def suspend_agent(self, agent_id: str) -> bool:
        ok = self.set_state(agent_id, AgentState.SUSPENDED)
        if ok:
            self.release_all(agent_id)
            self._audit.append(f"suspended {agent_id} (human)")
        return ok

    def stop_agent(self, agent_id: str) -> bool:
        ok = self.set_state(agent_id, AgentState.STOPPED)
        if ok:
            self.release_all(agent_id)
            self._audit.append(f"stopped {agent_id}")
        return ok

    def emergency_stop_all(self) -> int:
        """E-STOP every agent + release every lease (§12/§14)."""
        n = 0
        for agent_id in list(self._agents):
            if self._agents[agent_id].kind != "human":
                self.set_state(agent_id, AgentState.STOPPED)
                n += 1
        for res in list(self._leases):
            self._leases[res].state = LeaseState.RELEASED
            del self._leases[res]
        self.permissions.emergency_stop(True)
        self._audit.append("EMERGENCY STOP ALL")
        return n

    def resume_agent(self, agent_id: str) -> bool:
        return self.set_state(agent_id, AgentState.ACTIVE)

    # ── introspection ───────────────────────────────────────────────────

    def conflicts(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = [{"resource": c.resource, "holder": c.holder,
                 "challenger": c.challenger, "resolution": c.resolution}
                for c in self._conflicts[-max(1, min(int(limit), 100)):]]
        return rows

    def audit_tail(self, limit: int = 20) -> List[str]:
        return list(self._audit[-max(1, min(int(limit), MAX_AUDIT)):])

    # ── internals ────────────────────────────────────────────────────────

    def _set_waiting(self, agent_id: str) -> None:
        agent = self._agents.get(agent_id)
        if agent and agent.state is AgentState.ACTIVE:
            agent.state = AgentState.WAITING

    def _set_state_if(self, agent_id: str, was: AgentState,
                      becomes: AgentState) -> None:
        agent = self._agents.get(agent_id)
        if agent and agent.state is was:
            agent.state = becomes
