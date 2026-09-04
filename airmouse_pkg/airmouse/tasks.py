"""
airmouse.tasks — bounded Task Engine (v13 §5).

A structured, bounded, human-supervised task engine:

    creation · decomposition · dependency graphs · progress · pause ·
    resume · cancel · retry · checkpoint · rollback (where possible) ·
    human approval · verification

Every step is a structured object carrying (§5):

    objective · action · target · preconditions · expected result ·
    verification · risk · permission · timeout · retry policy

HARD RULES:
    * bounded (MAX_TASKS / MAX_STEPS_PER_TASK / MAX_CHECKPOINTS)
    * destructive steps CANNOT run without explicit human approval
    * rollback is bounded: checkpoints restore task/step STATE only
      (they never undo external side effects — that is the Recovery
      Engine's job, v14 §7)
    * every mutation appends to a bounded audit ring

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# bounded constants
# ─────────────────────────────────────────────────────────────────────────────

MAX_TASKS = 100
MAX_STEPS_PER_TASK = 64
MAX_CHECKPOINTS = 20
MAX_AUDIT = 200
MAX_OBJECTIVE = 200
MAX_FIELD = 120
MAX_DEPENDENCIES_PER_STEP = 8
DEFAULT_STEP_TIMEOUT = 30.0
MAX_TIMEOUT = 600.0
MAX_RETRIES = 3


class TaskStatus(enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    RUNNING = "running"
    PAUSED = "paused"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class StepStatus(enum.Enum):
    PENDING = "pending"
    READY = "ready"               # dependencies satisfied
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    AWAITING_APPROVAL = "awaiting_approval"


@dataclass
class RetryPolicy:
    """Bounded retry policy (§5)."""

    max_attempts: int = 1                 # total attempts allowed
    backoff_seconds: float = 0.0          # deterministic fixed backoff
    recover_with_human: bool = False      # after exhaustion, ask human

    def sanitized(self) -> "RetryPolicy":
        return RetryPolicy(
            max_attempts=max(1, min(int(self.max_attempts), MAX_RETRIES)),
            backoff_seconds=max(0.0, min(float(self.backoff_seconds), 60.0)),
            recover_with_human=bool(self.recover_with_human))


@dataclass
class TaskStep:
    """One structured step (§5 schema)."""

    step_id: str
    objective: str = ""
    action: str = "none"                  # unified action vocabulary name
    target: str = ""                      # semantic target description
    preconditions: Tuple[str, ...] = ()
    expected_result: str = ""
    verification: str = ""                # how success will be verified
    risk: str = "low"                     # none/low/medium/high/destructive
    permission: str = ""                  # required permission key
    timeout: float = DEFAULT_STEP_TIMEOUT
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    last_error: str = ""
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "objective": self.objective,
            "action": self.action,
            "target": self.target,
            "preconditions": list(self.preconditions),
            "expected_result": self.expected_result,
            "verification": self.verification,
            "risk": self.risk,
            "permission": self.permission,
            "timeout": self.timeout,
            "retry_policy": {"max_attempts": self.retry_policy.max_attempts,
                             "backoff_seconds":
                             self.retry_policy.backoff_seconds,
                             "recover_with_human":
                             self.retry_policy.recover_with_human},
            "status": self.status.value,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "verified": self.verified,
        }


@dataclass
class TaskCheckpoint:
    """Bounded rollback point (state snapshot only, §5)."""

    checkpoint_id: str
    label: str = ""
    step_statuses: Dict[str, str] = field(default_factory=dict)
    progress: float = 0.0
    task_status: str = ""


@dataclass
class Task:
    """A structured task with steps, dependencies, audit (§5)."""

    task_id: str
    objective: str = ""
    status: TaskStatus = TaskStatus.DRAFT
    steps: List[TaskStep] = field(default_factory=list)
    dependencies: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    progress: float = 0.0
    checkpoints: List[TaskCheckpoint] = field(default_factory=list)
    owner: str = "human"                  # human|agent:<id>
    approvals: List[str] = field(default_factory=list)
    audit: List[str] = field(default_factory=list)

    def to_dict(self, include_steps: bool = True) -> Dict[str, Any]:
        d = {
            "task_id": self.task_id,
            "objective": self.objective,
            "status": self.status.value,
            "progress": round(self.progress, 4),
            "owner": self.owner,
            "approvals": list(self.approvals[-8:]),
            "checkpoint_count": len(self.checkpoints),
            "audit_tail": list(self.audit[-8:]),
        }
        if include_steps:
            d["steps"] = [s.to_dict() for s in self.steps]
        return d


class TaskEngine:
    """Bounded task engine with human authority (v13 §5)."""

    def __init__(self, max_tasks: int = MAX_TASKS) -> None:
        self.max_tasks = max(4, min(int(max_tasks), MAX_TASKS))
        self._tasks: Dict[str, Task] = {}
        self._counter = 0

    # ── creation & decomposition ────────────────────────────────────────

    def create_task(self, objective: Any, steps: Optional[List[Dict[str,
                                                                       Any]]] = None,
                    owner: str = "human") -> Optional[Task]:
        """Create a task; optionally seed it with structured steps.

        Seeded tasks whose steps include destructive work start in
        PENDING_APPROVAL (never auto-approved).
        """
        try:
            obj = str(objective or "").strip()[:MAX_OBJECTIVE]
            if not obj:
                return None
            if len(self._tasks) >= self.max_tasks:
                self._evict_terminal()
                if len(self._tasks) >= self.max_tasks:
                    return None
            self._counter += 1
            task_id = f"task-{self._counter:04d}"
            task = Task(task_id=task_id, objective=obj,
                        owner=str(owner)[:40] or "human")
            if steps:
                for raw in steps[:MAX_STEPS_PER_TASK]:
                    self._add_step(task, raw)
            if any(s.risk == "destructive" for s in task.steps):
                task.status = TaskStatus.PENDING_APPROVAL
            task.audit.append(f"created owner={task.owner}")
            self._tasks[task_id] = task
            return task
        except Exception:
            return None

    def add_step(self, task_id: str, step: Dict[str, Any]) -> Optional[TaskStep]:
        task = self._tasks.get(task_id)
        if task is None or len(task.steps) >= MAX_STEPS_PER_TASK:
            return None
        added = self._add_step(task, step or {})
        if added and task.status is TaskStatus.DRAFT and \
                added.risk == "destructive":
            task.status = TaskStatus.PENDING_APPROVAL
        return added

    def add_dependency(self, task_id: str, step_id: str,
                       depends_on: str) -> bool:
        """step_id runs only after depends_on succeeds (§5 DAG)."""
        task = self._tasks.get(task_id)
        if task is None:
            return False
        ids = {s.step_id for s in task.steps}
        if step_id not in ids or depends_on not in ids or \
                step_id == depends_on:
            return False
        current = task.dependencies.get(step_id, ())
        if depends_on in current:
            return True
        if len(current) >= MAX_DEPENDENCIES_PER_STEP:
            return False
        task.dependencies[step_id] = tuple(sorted(current + (depends_on,)))
        task.audit.append(f"dep {step_id}<-{depends_on}")
        return True

    def dependency_graph(self, task_id: str) -> Dict[str, Tuple[str, ...]]:
        task = self._tasks.get(task_id)
        return dict(task.dependencies) if task else {}

    def _add_step(self, task: Task, raw: Dict[str, Any]) -> Optional[TaskStep]:
        try:
            n = len(task.steps) + 1
            step_id = str(raw.get("step_id") or f"step-{n:02d}")[:40]
            if any(s.step_id == step_id for s in task.steps):
                step_id = f"{step_id}-{n}"
            retry = raw.get("retry_policy") or RetryPolicy()
            if isinstance(retry, dict):
                retry = RetryPolicy(**{k: retry[k] for k in retry
                                       if k in ("max_attempts",
                                                "backoff_seconds",
                                                "recover_with_human")})
            risk = str(raw.get("risk", "low")).lower()
            if risk not in ("none", "low", "medium", "high", "destructive"):
                risk = "low"
            step = TaskStep(
                step_id=step_id,
                objective=str(raw.get("objective", ""))[:MAX_FIELD],
                action=str(raw.get("action", "none"))[:40],
                target=str(raw.get("target", ""))[:MAX_FIELD],
                preconditions=tuple(str(p)[:MAX_FIELD]
                                    for p in (raw.get("preconditions") or
                                              ())[:6]),
                expected_result=str(raw.get("expected_result", ""))[:MAX_FIELD],
                verification=str(raw.get("verification", ""))[:MAX_FIELD],
                risk=risk,
                permission=str(raw.get("permission", ""))[:40],
                timeout=max(0.1, min(float(raw.get("timeout",
                                                   DEFAULT_STEP_TIMEOUT)),
                                     MAX_TIMEOUT)),
                retry_policy=retry.sanitized(),
            )
            task.steps.append(step)
            return step
        except Exception:
            return None

    # ── approval (§5 human authority) ───────────────────────────────────

    def approve(self, task_id: str, approver: str = "human") -> bool:
        """Human approval.  Works at task level (PENDING_APPROVAL ->
        APPROVED) and at step level (AWAITING_APPROVAL -> PENDING)."""
        task = self._tasks.get(task_id)
        if task is None:
            return False
        pending_steps = any(s.status is StepStatus.AWAITING_APPROVAL
                            for s in task.steps)
        if task.status is TaskStatus.PENDING_APPROVAL:
            task.status = TaskStatus.APPROVED
        elif task.status is not TaskStatus.RUNNING or not pending_steps:
            return False
        task.approvals.append(str(approver)[:40])
        for s in task.steps:
            if s.status is StepStatus.AWAITING_APPROVAL:
                s.status = StepStatus.PENDING
        task.audit.append(f"approved by {str(approver)[:40]}")
        return True

    def requires_approval(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return False
        return (task.status is TaskStatus.PENDING_APPROVAL or
                any(s.status is StepStatus.AWAITING_APPROVAL
                    for s in task.steps))

    # ── lifecycle ───────────────────────────────────────────────────────

    def start(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return False
        if task.status is TaskStatus.PENDING_APPROVAL:
            return False                    # approval gate
        if task.status in (TaskStatus.DRAFT, TaskStatus.APPROVED,
                           TaskStatus.PAUSED, TaskStatus.BLOCKED):
            task.status = TaskStatus.RUNNING
            task.audit.append("started")
            return True
        return False

    def pause(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task and task.status is TaskStatus.RUNNING:
            task.status = TaskStatus.PAUSED
            task.audit.append("paused")
            return True
        return False

    def resume(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task and task.status is TaskStatus.PAUSED:
            task.status = TaskStatus.RUNNING
            task.audit.append("resumed")
            return True
        return False

    def cancel(self, task_id: str, reason: str = "") -> bool:
        task = self._tasks.get(task_id)
        if task and task.status not in (TaskStatus.COMPLETED,
                                        TaskStatus.CANCELLED):
            task.status = TaskStatus.CANCELLED
            for s in task.steps:
                if s.status in (StepStatus.PENDING, StepStatus.READY,
                                StepStatus.RUNNING,
                                StepStatus.AWAITING_APPROVAL):
                    s.status = StepStatus.CANCELLED
            task.audit.append(f"cancelled {str(reason)[:60]}")
            return True
        return False

    # ── execution simulation hooks (engine never executes itself) ───────

    def ready_steps(self, task_id: str) -> List[TaskStep]:
        """Steps whose dependencies are all SUCCEEDED and that are
        runnable now (§5 dependency graph)."""
        task = self._tasks.get(task_id)
        if task is None or task.status is not TaskStatus.RUNNING:
            return []
        done = {s.step_id for s in task.steps
                if s.status is StepStatus.SUCCEEDED}
        out = []
        for s in task.steps:
            if s.status in (StepStatus.PENDING, StepStatus.READY):
                deps = task.dependencies.get(s.step_id, ())
                if all(d in done for d in deps):
                    s.status = StepStatus.READY
                    out.append(s)
        return out

    def begin_step(self, task_id: str, step_id: str) -> bool:
        """Mark a step RUNNING.  Destructive steps require a prior
        human approval recorded on the task (§5)."""
        task = self._tasks.get(task_id)
        step = self._find(task, step_id)
        if task is None or step is None:
            return False
        if step.risk == "destructive" and not task.approvals:
            step.status = StepStatus.AWAITING_APPROVAL
            task.audit.append(f"step {step_id} awaits approval")
            return False
        if task.status is not TaskStatus.RUNNING:
            return False
        if step.attempts >= step.retry_policy.max_attempts and \
                step.status is not StepStatus.READY:
            return False
        step.status = StepStatus.RUNNING
        step.attempts += 1
        task.audit.append(f"step {step_id} attempt {step.attempts}")
        return True

    def complete_step(self, task_id: str, step_id: str,
                      success: bool, verified: bool = False,
                      error: str = "") -> Optional[TaskStatus]:
        """Record a step outcome; advances task state machine.

        A step is only SUCCEEDED when success AND verified (§5
        verification); unverified success enters VERIFYING task state.
        """
        task = self._tasks.get(task_id)
        step = self._find(task, step_id)
        if task is None or step is None:
            return None
        step.verified = bool(verified)
        if success and verified:
            step.status = StepStatus.SUCCEEDED
            step.last_error = ""
        elif success and not verified:
            step.status = StepStatus.RUNNING
            task.status = TaskStatus.VERIFYING
            task.audit.append(f"step {step_id} unverified")
            return task.status
        else:
            step.last_error = str(error)[:MAX_FIELD]
            if step.attempts < step.retry_policy.max_attempts:
                step.status = StepStatus.READY      # retry eligible
                task.audit.append(
                    f"step {step_id} failed, retry "
                    f"{step.attempts}/{step.retry_policy.max_attempts}")
            else:
                step.status = StepStatus.FAILED
                if step.retry_policy.recover_with_human:
                    task.status = TaskStatus.BLOCKED
                    task.audit.append(f"step {step_id} blocked->human")
                else:
                    task.status = TaskStatus.FAILED
                    task.audit.append(f"step {step_id} failed terminal")
                self._recompute_progress(task)
                return task.status
        self._recompute_progress(task)
        if all(s.status is StepStatus.SUCCEEDED for s in task.steps) and \
                task.steps:
            task.status = TaskStatus.COMPLETED
            task.audit.append("completed")
        return task.status

    def record_verification(self, task_id: str, step_id: str,
                            passed: bool) -> bool:
        """Close the VERIFYING state: confirm or fail the step."""
        task = self._tasks.get(task_id)
        step = self._find(task, step_id)
        if task is None or step is None:
            return False
        step.verified = bool(passed)
        if passed:
            step.status = StepStatus.SUCCEEDED
            if task.status is TaskStatus.VERIFYING:
                task.status = TaskStatus.RUNNING
        else:
            return self.complete_step(task_id, step_id, success=False,
                                      error="verification failed") is not None
        self._recompute_progress(task)
        if all(s.status is StepStatus.SUCCEEDED for s in task.steps) and \
                task.steps:
            task.status = TaskStatus.COMPLETED
        return True

    def retry_step(self, task_id: str, step_id: str) -> bool:
        task = self._tasks.get(task_id)
        step = self._find(task, step_id)
        if task is None or step is None:
            return False
        if step.status is StepStatus.FAILED:
            return False            # terminal; use recover_with_human
        if step.attempts >= step.retry_policy.max_attempts:
            return False
        step.status = StepStatus.READY
        return True

    # ── checkpoints & rollback (§5, state-only) ─────────────────────────

    def checkpoint(self, task_id: str, label: str = "") -> Optional[str]:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        cid = f"cp-{len(task.checkpoints) + 1:03d}"
        cp = TaskCheckpoint(
            checkpoint_id=cid, label=str(label)[:60],
            step_statuses={s.step_id: s.status.value for s in task.steps},
            progress=task.progress, task_status=task.status.value)
        task.checkpoints.append(cp)
        if len(task.checkpoints) > MAX_CHECKPOINTS:
            task.checkpoints = task.checkpoints[-MAX_CHECKPOINTS:]
        task.audit.append(f"checkpoint {cid}")
        return cid

    def rollback(self, task_id: str, checkpoint_id: str) -> bool:
        """Restore task/step STATE from a checkpoint (§5).

        Rollback restores ENGINE state only — it never claims to undo
        external side effects (mouse clicks, sent emails, ...).  That
        honesty is explicit in the audit trail.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False
        cp = next((c for c in task.checkpoints
                   if c.checkpoint_id == checkpoint_id), None)
        if cp is None:
            return False
        for s in task.steps:
            prev = cp.step_statuses.get(s.step_id)
            if prev:
                try:
                    s.status = StepStatus(prev)
                except ValueError:
                    pass
        task.progress = cp.progress
        try:
            task.status = TaskStatus(cp.task_status)
        except ValueError:
            task.status = TaskStatus.PAUSED
        task.audit.append(f"rollback -> {checkpoint_id} "
                          f"(state only; side effects not undone)")
        return True

    # ── introspection ───────────────────────────────────────────────────

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        task = self._tasks.get(task_id)
        return task.to_dict() if task else None

    def progress(self, task_id: str) -> float:
        task = self._tasks.get(task_id)
        return task.progress if task else 0.0

    def list_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        rows = [t.to_dict(include_steps=False)
                for t in self._tasks.values()]
        rows.sort(key=lambda d: d["task_id"])
        return rows[: max(1, min(int(limit), MAX_TASKS))]

    def audit(self, task_id: str, limit: int = 20) -> List[str]:
        task = self._tasks.get(task_id)
        return list(task.audit[-max(1, min(int(limit), MAX_AUDIT)):]) \
            if task else []

    # ── internals ────────────────────────────────────────────────────────

    def _find(self, task: Optional[Task],
              step_id: str) -> Optional[TaskStep]:
        if task is None:
            return None
        return next((s for s in task.steps if s.step_id == step_id), None)

    def _recompute_progress(self, task: Task) -> None:
        if not task.steps:
            task.progress = 0.0
            return
        weights = {"pending": 0.0, "ready": 0.0, "running": 0.3,
                   "awaiting_approval": 0.1, "succeeded": 1.0,
                   "failed": 0.0, "skipped": 0.5, "cancelled": 0.0}
        total = sum(weights.get(s.status.value, 0.0) for s in task.steps)
        task.progress = round(min(1.0, total / len(task.steps)), 4)

    def _evict_terminal(self) -> None:
        terminal = [tid for tid, t in self._tasks.items()
                    if t.status in (TaskStatus.COMPLETED,
                                    TaskStatus.CANCELLED,
                                    TaskStatus.FAILED)]
        for tid in terminal[:len(terminal) // 2 + 1]:
            del self._tasks[tid]
