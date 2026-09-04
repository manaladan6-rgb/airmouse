"""
airmouse.ditm — DO IT WITH ME (v15 §16), the flagship experience.

The user provides a GOAL.  AirMouse:

    understands · inspects context · proposes a plan · identifies
    risk · requests approval · executes · observes · verifies ·
    reports progress · learns from corrections

The proposal is STRUCTURED (§16):

    OBJECTIVE / PLAN / SOURCES / CURRENT STATE / RISKS /
    REQUIRED ACTIONS / APPROVAL STATE

The user can: START · EDIT PLAN · PAUSE · STOP · CHANGE DIRECTION.

SAFETY: the plan is built on the §4 goal hierarchy + §5 TaskEngine —
PREDICTION ≠ PERMISSION ≠ EXECUTION.  Nothing runs before approval;
destructive steps keep their confirmation gates; every state change
is reported and explainable (§24).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .goals import GoalHierarchyParser, Objective, RiskLevel
from .tasks import TaskEngine, TaskStatus

MAX_SESSIONS = 16
MAX_PLAN_STEPS = 12
MAX_CORRECTIONS = 32


class SessionState(enum.Enum):
    PROPOSED = "proposed"          # plan ready, waiting for approval
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    CHANGED = "changed"            # user changed direction


@dataclass
class DitmProposal:
    """The §16 structured proposal."""

    objective: str = ""
    plan: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)   # context provenance
    current_state: str = ""
    risks: List[str] = field(default_factory=list)
    required_actions: List[Dict[str, str]] = field(default_factory=list)
    approval_state: str = "pending"     # pending|approved|declined
    confidence: float = 0.0
    required_permissions: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objective": self.objective,
            "plan": list(self.plan),
            "sources": list(self.sources),
            "current_state": self.current_state,
            "risks": list(self.risks),
            "required_actions": list(self.required_actions),
            "approval_state": self.approval_state,
            "confidence": round(self.confidence, 4),
            "required_permissions": list(self.required_permissions),
        }


@dataclass
class DitmSession:
    """One Do-It-With-Me session (bounded)."""

    session_id: str = ""
    proposal: DitmProposal = field(default_factory=DitmProposal)
    state: SessionState = SessionState.PROPOSED
    task_id: str = ""
    progress: float = 0.0
    corrections: List[str] = field(default_factory=list)
    created_ts: float = 0.0
    updated_ts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "proposal": self.proposal.to_dict(),
            "state": self.state.value,
            "task_id": self.task_id,
            "progress": round(self.progress, 4),
            "corrections": list(self.corrections[-8:]),
        }


class DoItWithMe:
    """§16 flagship loop: goal → proposal → approval → execution →
    verification → progress → learning."""

    def __init__(self, parser: Optional[GoalHierarchyParser] = None,
                 task_engine: Optional[TaskEngine] = None,
                 observer: Optional[Any] = None,
                 twin=None) -> None:
        self.parser = parser or GoalHierarchyParser()
        self.tasks = task_engine or TaskEngine()
        self.observer = observer            # context inspector (§16 step 2)
        self.twin = twin                    # optional Personal Twin (§2)
        self._sessions: Dict[str, DitmSession] = {}
        self._counter = 0

    # ── propose (§16 steps 1-5) ─────────────────────────────────────────

    def propose(self, goal: Any) -> Optional[DitmSession]:
        """Build the structured proposal.  NEVER executes anything."""
        try:
            obj = self.parser.parse(goal)
            if not obj.name:
                return None
            if len(self._sessions) >= MAX_SESSIONS:
                self._evict_finished()
            self._counter += 1
            sid = f"ditm-{self._counter:04d}"
            state_desc = ""
            sources: List[str] = []
            if self.observer is not None:
                try:
                    snap = self.observer()
                    if isinstance(snap, dict):
                        state_desc = str(snap.get("summary", ""))[:120]
                        sources = [str(s)[:40] for s in
                                   snap.get("sources", [])[:6]]
                except Exception:
                    pass
            risks = self._risks_for(obj)
            plan_steps = [s.objective for s in obj.proposed_plan]
            proposal = DitmProposal(
                objective=obj.name,
                plan=plan_steps[:MAX_PLAN_STEPS],
                sources=sources or ["deterministic parser"],
                current_state=state_desc or "no observer attached",
                risks=risks,
                required_actions=[
                    {"index": str(i), "objective": s.objective,
                     "action": s.suggested_action, "risk": s.risk}
                    for i, s in enumerate(obj.proposed_plan[:MAX_PLAN_STEPS])],
                approval_state="pending",
                confidence=obj.confidence,
                required_permissions=obj.required_permissions)
            session = DitmSession(session_id=sid, proposal=proposal,
                                  created_ts=time.perf_counter(),
                                  updated_ts=time.perf_counter())
            self._sessions[sid] = session
            return session
        except Exception:
            return None

    def _risks_for(self, obj: Objective) -> List[str]:
        risks: List[str] = []
        if obj.risk is RiskLevel.DESTRUCTIVE:
            risks.append("destructive operation present — explicit "
                         "confirmation required")
        elif obj.risk is RiskLevel.HIGH:
            risks.append("sensitive operation — confirmation required")
        if obj.required_permissions:
            risks.append("requires permissions: " +
                         ", ".join(obj.required_permissions))
        if obj.confidence < 0.55:
            risks.append("low interpretation confidence — ask, don't guess")
        if not risks:
            risks.append("low risk")
        return risks

    # ── user verbs (§16: START/EDIT/PAUSE/STOP/CHANGE) ──────────────────

    def approve(self, session_id: str, approved: bool,
                by: str = "human") -> bool:
        s = self._sessions.get(session_id)
        if s is None or s.state is not SessionState.PROPOSED:
            return False
        if approved:
            s.proposal.approval_state = "approved"
            task = self.tasks.create_task(
                s.proposal.objective,
                [{"step_id": f"p{i:02d}", "objective": r["objective"],
                  "action": r["action"], "risk": r.get("risk", "low")}
                 for i, r in enumerate(s.proposal.required_actions)],
                owner="human")
            if task is None:
                return False
            s.task_id = task.task_id
            # destructive plans stay PENDING_APPROVAL inside TaskEngine
            if task.status is TaskStatus.PENDING_APPROVAL:
                return True
            return self.tasks.start(task.task_id) and \
                self._mark(s, SessionState.RUNNING)
        s.proposal.approval_state = "declined"
        return self._mark(s, SessionState.STOPPED)

    def start(self, session_id: str) -> bool:
        s = self._sessions.get(session_id)
        if s is None or s.state is not SessionState.PAUSED:
            return False
        if self.tasks.resume(s.task_id):
            return self._mark(s, SessionState.RUNNING)
        return self.tasks.start(s.task_id) and \
            self._mark(s, SessionState.RUNNING)

    def pause(self, session_id: str) -> bool:
        s = self._sessions.get(session_id)
        if s is None or s.state is not SessionState.RUNNING:
            return False
        ok = self.tasks.pause(s.task_id)
        return ok and self._mark(s, SessionState.PAUSED)

    def stop(self, session_id: str, reason: str = "user") -> bool:
        """HUMAN STOP always wins (§14)."""
        s = self._sessions.get(session_id)
        if s is None:
            return False
        self.tasks.cancel(s.task_id, f"ditm stop: {reason}")
        s.proposal.approval_state = s.proposal.approval_state
        return self._mark(s, SessionState.STOPPED)

    def edit_plan(self, session_id: str, plan: List[str]) -> bool:
        """EDIT PLAN: allowed while still PROPOSED (before approval)."""
        s = self._sessions.get(session_id)
        if s is None or s.state is not SessionState.PROPOSED:
            return False
        if not isinstance(plan, list) or not plan or \
                len(plan) > MAX_PLAN_STEPS:
            return False
        s.proposal.plan = [str(p)[:120] for p in plan]
        s.proposal.required_actions = [
            {"index": str(i), "objective": str(p)[:120],
             "action": "execute", "risk": "low"}
            for i, p in enumerate(s.proposal.plan)]
        return True

    def change_direction(self, session_id: str, new_goal: str
                         ) -> Optional[DitmSession]:
        """CHANGE DIRECTION: stop current, propose a fresh plan."""
        s = self._sessions.get(session_id)
        if s is None:
            return None
        if s.state is SessionState.RUNNING:
            self.tasks.pause(s.task_id)
        self._mark(s, SessionState.CHANGED)
        self.tasks.cancel(s.task_id, "changed direction")
        return self.propose(new_goal)

    def correct(self, session_id: str, correction: str) -> bool:
        """Learn from corrections (§16 step 10) — Twin optional."""
        s = self._sessions.get(session_id)
        if s is None:
            return False
        s.corrections.append(str(correction)[:120])
        if len(s.corrections) > MAX_CORRECTIONS:
            s.corrections = s.corrections[-MAX_CORRECTIONS:]
        if self.twin is not None:
            try:
                self.twin.correct("correction_behavior",
                                  f"session_{s.session_id}",
                                  "corrected_plan")
            except Exception:
                pass
        return True

    # ── progress reporting (§16 step 9) ─────────────────────────────────

    def report(self, session_id: str) -> Optional[Dict[str, Any]]:
        s = self._sessions.get(session_id)
        if s is None:
            return None
        if s.task_id:
            s.progress = self.tasks.progress(s.task_id)
            task = self.tasks.get(s.task_id)
            if task and task["status"] == "completed":
                self._mark(s, SessionState.COMPLETED)
        return s.to_dict()

    def list_sessions(self) -> List[Dict[str, Any]]:
        rows = [s.to_dict() for s in self._sessions.values()]
        rows.sort(key=lambda d: d["session_id"])
        return rows

    # ── internals ────────────────────────────────────────────────────────

    def _mark(self, s: DitmSession, state: SessionState) -> bool:
        s.state = state
        s.updated_ts = time.perf_counter()
        return True

    def _evict_finished(self) -> None:
        finished = [sid for sid, s in self._sessions.items()
                    if s.state in (SessionState.COMPLETED,
                                   SessionState.STOPPED,
                                   SessionState.CHANGED)]
        for sid in finished[:len(finished) // 2 + 1]:
            del self._sessions[sid]
