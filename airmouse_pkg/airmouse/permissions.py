"""
airmouse.permissions — Agent Permission Engine (v15 §15) and the
global control hierarchy (v15 §14).

GRANULAR PERMISSIONS (§15):  observe screen · read accessibility tree ·
read clipboard · type text · click · browser navigation · file read ·
file write · application launch · application close · system operation ·
destructive actions.

DECISIONS (§15):  ALLOW · DENY · ASK · ALLOW_ONCE · ALLOW_SESSION ·
ALLOW_PATTERN.  ASK fails closed: without a human answer the answer is
NO.

GLOBAL HIERARCHY (§14) — nothing may reorder it:

    EMERGENCY STOP  >  HUMAN OVERRIDE  >  SAFETY POLICY  >
    PERMISSION  >  AGENT  >  PREDICTION

An AI agent can NEVER override: e-stop, human cancellation,
destructive-action confirmation, permission boundaries, security
policies.  ``explain_decision`` returns a §24-style trace.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import fnmatch
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MAX_AGENTS = 32
MAX_RULES = 256
MAX_AUDIT = 500
MAX_KEY = 64


class Decision(enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    ALLOW_PATTERN = "allow_pattern"


class ControlLevel(enum.Enum):
    """§14 hierarchy — lower enum value wins."""

    EMERGENCY_STOP = 0
    HUMAN_OVERRIDE = 1
    SAFETY_POLICY = 2
    PERMISSION = 3
    AGENT = 4
    PREDICTION = 5


# canonical permission keys (§15 granular set)
PERMISSION_KEYS: Tuple[str, ...] = (
    "observe.screen", "read.accessibility", "read.clipboard",
    "type.text", "mouse.click", "browser.navigate", "file.read",
    "file.write", "application.launch", "application.close",
    "system.operation", "destructive.action",
)


@dataclass
class PermissionRule:
    """One agent permission binding (§15)."""

    agent_id: str = "*"
    key: str = "*"
    decision: Decision = Decision.ASK
    pattern: str = ""            # for ALLOW_PATTERN (fnmatch)
    remaining_uses: int = -1     # -1 = unlimited
    granted_by: str = "system"
    reason: str = ""


@dataclass
class PermissionDecision:
    allowed: bool = False
    decision: Decision = Decision.DENY
    level: ControlLevel = ControlLevel.PERMISSION
    reason: str = ""
    requires_confirmation: bool = False
    consumed_rule: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "decision": self.decision.value,
            "level": self.level.name.lower(),
            "reason": self.reason[:160],
            "requires_confirmation": self.requires_confirmation,
        }


class AgentPermissionEngine:
    """Granular agent permissions under the §14 hierarchy."""

    def __init__(self, max_rules: int = MAX_RULES) -> None:
        self.max_rules = max(16, min(int(max_rules), MAX_RULES))
        self._rules: List[PermissionRule] = []
        self._session_grants: Dict[Tuple[str, str], int] = {}
        self._estop = False
        self._human_override: Optional[bool] = None   # None = no override
        self._safety_blocked_keys: set = set()
        self._audit: List[str] = []

    # ── §14 hierarchy controls ──────────────────────────────────────────

    def emergency_stop(self, on: bool = True) -> None:
        """E-STOP: highest level, overrides EVERYTHING (§14)."""
        self._estop = bool(on)
        self._audit.append(f"estop {'ON' if on else 'OFF'}")

    def human_override(self, allow: Optional[bool]) -> None:
        """Human override: force-allow or force-deny for everything
        below HUMAN OVERRIDE (§14).  ``None`` clears it."""
        self._human_override = allow
        self._audit.append(f"human_override -> {allow}")

    def safety_block(self, key: str, blocked: bool = True) -> None:
        """Safety policy marks a permission key (un)usable (§14)."""
        key = str(key)[:MAX_KEY]
        if blocked:
            self._safety_blocked_keys.add(key)
        else:
            self._safety_blocked_keys.discard(key)
        self._audit.append(f"safety {'block' if blocked else 'unblock'} "
                           f"{key}")

    def active_level(self) -> ControlLevel:
        """Which §14 level currently governs decisions."""
        if self._estop:
            return ControlLevel.EMERGENCY_STOP
        if self._human_override is not None:
            return ControlLevel.HUMAN_OVERRIDE
        return ControlLevel.PERMISSION

    # ── rules (§15) ─────────────────────────────────────────────────────

    def grant(self, agent_id: str, key: str, decision: Decision,
              pattern: str = "", uses: int = -1,
              granted_by: str = "human", reason: str = "") -> bool:
        try:
            if not agent_id or not key:
                return False
            if decision is Decision.ALLOW_PATTERN and not pattern:
                return False
            if len(self._rules) >= self.max_rules:
                self._rules = self._rules[-self.max_rules + 1:]
            self._rules.append(PermissionRule(
                agent_id=str(agent_id)[:MAX_KEY], key=str(key)[:MAX_KEY],
                decision=decision, pattern=str(pattern)[:MAX_KEY],
                remaining_uses=int(uses), granted_by=str(granted_by)[:40],
                reason=str(reason)[:160]))
            self._audit.append(f"grant {agent_id}:{key} -> "
                               f"{decision.value}")
            return True
        except Exception:
            return False

    def revoke(self, agent_id: str, key: str) -> int:
        before = len(self._rules)
        self._rules = [r for r in self._rules
                       if not (r.agent_id == agent_id and r.key == key)]
        removed = before - len(self._rules)
        self._session_grants.pop((agent_id, key), None)
        if removed:
            self._audit.append(f"revoke {agent_id}:{key}")
        return removed

    def revoke_agent(self, agent_id: str) -> int:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.agent_id != agent_id]
        removed = before - len(self._rules)
        for (a, k) in list(self._session_grants):
            if a == agent_id:
                del self._session_grants[(a, k)]
        self._audit.append(f"revoke_agent {agent_id}")
        return removed

    # ── the gate (§14/§15) ──────────────────────────────────────────────

    def check(self, agent_id: str, key: str,
              risky: bool = False) -> PermissionDecision:
        """Decide one (agent, permission) request through the §14
        hierarchy.  ASK without a human ⇒ DENY (fail closed)."""
        agent_id = str(agent_id)[:MAX_KEY]
        key = str(key)[:MAX_KEY]

        # 1. EMERGENCY STOP
        if self._estop:
            return PermissionDecision(
                False, Decision.DENY, ControlLevel.EMERGENCY_STOP,
                "emergency stop active", True)

        # 2. HUMAN OVERRIDE
        if self._human_override is True:
            return PermissionDecision(
                True, Decision.ALLOW, ControlLevel.HUMAN_OVERRIDE,
                "human override: allow", risky)
        if self._human_override is False:
            return PermissionDecision(
                False, Decision.DENY, ControlLevel.HUMAN_OVERRIDE,
                "human override: deny", False)

        # 3. SAFETY POLICY
        if key in self._safety_blocked_keys:
            return PermissionDecision(
                False, Decision.DENY, ControlLevel.SAFETY_POLICY,
                "safety policy blocks this capability", risky)

        # 4. PERMISSION rules (most specific wins; deterministic order)
        rule = self._match_rule(agent_id, key)
        if rule is None:
            # fail closed: unknown capability => ASK => NO for agents
            return PermissionDecision(
                False, Decision.ASK, ControlLevel.PERMISSION,
                "no rule; default ASK fails closed", True)

        if rule.decision is Decision.DENY:
            return PermissionDecision(
                False, Decision.DENY, ControlLevel.PERMISSION,
                rule.reason or "denied by rule", False)
        if rule.decision is Decision.ASK:
            return PermissionDecision(
                False, Decision.ASK, ControlLevel.PERMISSION,
                rule.reason or "confirmation required", True)
        if rule.decision is Decision.ALLOW_ONCE:
            if rule.remaining_uses <= 0:
                return PermissionDecision(
                    False, Decision.ASK, ControlLevel.PERMISSION,
                    "one-time grant exhausted", True)
            rule.remaining_uses -= 1
            return PermissionDecision(
                True, Decision.ALLOW_ONCE, ControlLevel.PERMISSION,
                rule.reason or "one-time grant", risky,
                consumed_rule=True)
        if rule.decision is Decision.ALLOW_SESSION:
            n = self._session_grants.get((agent_id, key), 0)
            self._session_grants[(agent_id, key)] = n + 1
            return PermissionDecision(
                True, Decision.ALLOW_SESSION, ControlLevel.PERMISSION,
                rule.reason or "session grant", risky)
        if rule.decision is Decision.ALLOW_PATTERN:
            target = rule.pattern or key
            if fnmatch.fnmatch(key, target):
                return PermissionDecision(
                    True, Decision.ALLOW_PATTERN, ControlLevel.PERMISSION,
                    rule.reason or f"pattern {rule.pattern}", risky)
            return PermissionDecision(
                False, Decision.DENY, ControlLevel.PERMISSION,
                "pattern mismatch", False)
        # ALLOW
        return PermissionDecision(
            True, Decision.ALLOW, ControlLevel.PERMISSION,
            rule.reason or "granted", risky)

    def _match_rule(self, agent_id: str, key: str) -> Optional[PermissionRule]:
        """Most specific rule wins: exact agent+key > agent+wild >
        wildcard-agent exact key > wildcard (§15 deterministic)."""
        candidates = [r for r in self._rules
                      if r.key == key or r.key == "*" or
                      (r.decision is Decision.ALLOW_PATTERN and
                       fnmatch.fnmatch(key, r.pattern or r.key))]
        agent_exact = [r for r in candidates if r.agent_id == agent_id]
        agent_wild = [r for r in candidates if r.agent_id == "*"]
        for pool in (agent_exact, agent_wild):
            exact = [r for r in pool if r.key == key]
            if exact:
                return exact[0]
            pat = [r for r in pool if r.decision is Decision.ALLOW_PATTERN]
            if pat:
                return pat[0]
            wild = [r for r in pool if r.key == "*"]
            if wild:
                return wild[0]
        return None

    # ── explainability (§24) ────────────────────────────────────────────

    def explain_decision(self, agent_id: str, key: str,
                         risky: bool = False) -> Dict[str, Any]:
        """Why was this decision made? (§24, no sensitive data)."""
        d = self.check(agent_id, key, risky)
        return {
            "decision": d.to_dict(),
            "hierarchy": {
                "estop": self._estop,
                "human_override": self._human_override,
                "safety_blocked": key in self._safety_blocked_keys,
            },
            "because": [
                f"active control level: {self.active_level().name.lower()}",
                f"permission '{key}' for agent '{agent_id}' -> "
                f"{d.decision.value}",
            ] + ([f"rule reason: {d.reason}"] if d.reason else []),
        }

    def audit_tail(self, limit: int = 20) -> List[str]:
        return list(self._audit[-max(1, min(int(limit), MAX_AUDIT)):])
