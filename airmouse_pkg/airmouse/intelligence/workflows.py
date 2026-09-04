"""
airmouse.intelligence.workflows — workflow discovery, personal automation
and proactive assistance (v11.5 §15, §16, §28).

THE SAFETY STORY (mission-critical):

* workflows are DISCOVERED from repeated observation but NEVER created
  silently — a user must explicitly approve each suggestion
* every Workflow carries steps / conditions / confidence / provenance /
  creation date / success history / permissions / destructive guards
* before a newly learned workflow runs the first time, the runner
  returns a human-readable PREVIEW — the user must see what will happen
* destructive steps abort unless an explicit confirmation callback
  approves THAT step
* PREDICTION ≠ EXECUTION, everywhere, always

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

MAX_WORKFLOWS = 200                  # §44 hard limit
MAX_STEPS_PER_WORKFLOW = 24
MAX_OBSERVED_WINDOW = 512            # rolling observation window
MIN_PATTERN_LENGTH = 3
MAX_PATTERN_LENGTH = 8
MIN_REPETITIONS = 3
DESTRUCTIVE_ACTIONS = frozenset({
    "delete", "remove", "close_app", "close_tab", "close_window",
    "file_delete", "file_trash", "shutdown", "restart", "sleep", "lock",
    "empty_trash", "format", "kill_process", "uninstall", "overwrite",
    "destructive",
})


def is_destructive_action(symbol: str) -> bool:
    s = str(symbol or "").strip().lower()
    if not s:
        return False
    if s in DESTRUCTIVE_ACTIONS:
        return True
    return s.startswith(("delete", "remove", "trash", "kill", "shutdown",
                         "format", "wipe", "purge"))


# ─────────────────────────────────────────────────────────────────────────────
# data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkflowStep:
    """One step: an opaque action symbol + params (DATA, policy-gated)."""

    action: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"action": str(self.action)[:64],
                "params": {str(k)[:32]: str(v)[:120]
                           for k, v in list(self.params.items())[:6]}}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorkflowStep":
        if not isinstance(d, dict):
            raise ValueError("step not a dict")
        action = str(d.get("action", ""))[:64]
        if not action or not action.replace("_", "").replace("-", "").isalnum():
            raise ValueError("invalid step action")
        params = d.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        return cls(action=action,
                   params={str(k)[:32]: str(v)[:120]
                           for k, v in list(params.items())[:6]})


@dataclass
class Workflow:
    """A learned personal automation with full provenance (§15)."""

    id: str
    name: str
    steps: List[WorkflowStep]
    conditions: Dict[str, str] = field(default_factory=dict)   # e.g. app==chrome
    confidence: float = 0.0
    provenance: str = "discovered"      # discovered | user-defined | imported
    created_at: float = field(default_factory=time.time)
    observations: int = 0               # how many times the pattern was seen
    success_count: int = 0
    failure_count: int = 0
    permissions: List[str] = field(default_factory=lambda: ["safe_actions"])
    enabled: bool = True
    previewed: bool = False             # §16: preview before first execution
    destructive: bool = False           # any step destructive → guarded

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
            "conditions": dict(self.conditions),
            "confidence": round(self.confidence, 4),
            "provenance": self.provenance,
            "created_at": self.created_at,
            "observations": self.observations,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "permissions": list(self.permissions)[:8],
            "enabled": self.enabled,
            "previewed": self.previewed,
            "destructive": self.destructive,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Workflow":
        if not isinstance(d, dict):
            raise ValueError("workflow not a dict")
        steps_raw = d.get("steps") or []
        if not isinstance(steps_raw, list) or not steps_raw:
            raise ValueError("workflow needs steps")
        steps = [WorkflowStep.from_dict(s) for s in steps_raw[:MAX_STEPS_PER_WORKFLOW]]
        if not steps:
            raise ValueError("no valid steps")
        name = str(d.get("name", ""))[:80] or "workflow"
        wid = str(d.get("id", ""))[:64] or uuid.uuid4().hex[:12]
        conds_raw = d.get("conditions") or {}
        conds = ({str(k)[:32]: str(v)[:80]
                  for k, v in list(conds_raw.items())[:6]}
                 if isinstance(conds_raw, dict) else {})
        destructive = any(is_destructive_action(s.action) for s in steps)
        perms = [str(p)[:32] for p in (d.get("permissions") or ["safe_actions"])[:8]]
        if destructive and "destructive_allowed" not in perms:
            perms.append("destructive_allowed")
        return cls(
            id=wid, name=name, steps=steps, conditions=conds,
            confidence=max(0.0, min(1.0, float(d.get("confidence", 0.5) or 0.5))),
            provenance=str(d.get("provenance", "imported"))[:24],
            created_at=float(d.get("created_at", 0.0) or 0.0),
            observations=max(0, int(d.get("observations", 0) or 0)),
            success_count=max(0, int(d.get("success_count", 0) or 0)),
            failure_count=max(0, int(d.get("failure_count", 0) or 0)),
            permissions=perms,
            enabled=bool(d.get("enabled", True)),
            previewed=bool(d.get("previewed", False)),
            destructive=destructive,
        )


@dataclass
class WorkflowSuggestion:
    """A discovered candidate awaiting explicit user approval."""

    pattern: Tuple[str, ...]
    repetitions: int
    confidence: float
    suggested_name: str
    discovered_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"pattern": list(self.pattern),
                "repetitions": self.repetitions,
                "confidence": round(self.confidence, 4),
                "suggested_name": self.suggested_name}


# ─────────────────────────────────────────────────────────────────────────────
# discovery
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowDiscovery:
    """Detects repeated action sequences (§15).

    Deterministic rolling-window suffix counting.  When a sequence of
    length MIN_PATTERN_LENGTH..MAX_PATTERN_LENGTH repeats at least
    MIN_REPETITIONS times, a WorkflowSuggestion is surfaced.  NO
    workflow is created automatically.
    """

    def __init__(self, min_repetitions: int = MIN_REPETITIONS) -> None:
        self.min_repetitions = max(2, int(min_repetitions))
        self._window: List[str] = []
        self._seen: set = set()

    def observe_step(self, action: str, context: Optional[Dict[str, str]] = None
                     ) -> List[WorkflowSuggestion]:
        """Feed one observed action; return NEW suggestions (deduped)."""
        a = str(action or "").strip().lower()[:64]
        if not a or not a.replace("_", "").replace("-", "").isalnum():
            return []
        self._window.append(a)
        if len(self._window) > MAX_OBSERVED_WINDOW:
            self._window = self._window[-int(MAX_OBSERVED_WINDOW * 0.8):]
        out: List[WorkflowSuggestion] = []
        for length in range(MAX_PATTERN_LENGTH, MIN_PATTERN_LENGTH - 1, -1):
            if len(self._window) < length:
                continue
            pattern = tuple(self._window[-length:])
            reps = self._count_occurrences(pattern)
            if reps >= self.min_repetitions and pattern not in self._seen:
                self._seen.add(pattern)
                out.append(WorkflowSuggestion(
                    pattern=pattern,
                    repetitions=reps,
                    confidence=min(0.95, reps / (reps + 4)),
                    suggested_name=self._name_for(pattern),
                ))
        return out

    def _count_occurrences(self, pattern: Tuple[str, ...]) -> int:
        n, L = 0, len(pattern)
        for i in range(0, len(self._window) - L + 1):
            if tuple(self._window[i:i + L]) == pattern:
                n += 1
        return n

    @staticmethod
    def _name_for(pattern: Tuple[str, ...]) -> str:
        words = " ".join(p.replace("_", " ") for p in pattern[:4])
        return (words[:60] + " workflow").strip()

    def pending_suggestion_count(self) -> int:
        return len(self._seen)

    def forget(self) -> None:
        self._window.clear()
        self._seen.clear()


# ─────────────────────────────────────────────────────────────────────────────
# store + runner
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowStore:
    """Bounded registry of user-approved workflows."""

    def __init__(self) -> None:
        self._workflows: Dict[str, Workflow] = {}

    def create(self, suggestion: WorkflowSuggestion, name: str = "",
               provenance: str = "discovered") -> Optional[Workflow]:
        """Create a workflow from a suggestion — the user's approval is
        represented by CALLING this method; it is never auto-invoked."""
        if len(self._workflows) >= MAX_WORKFLOWS:
            return None
        steps = [WorkflowStep(action=a) for a in suggestion.pattern]
        wf = Workflow(
            id=uuid.uuid4().hex[:12],
            name=(name or suggestion.suggested_name)[:80],
            steps=steps,
            confidence=suggestion.confidence,
            provenance=provenance,
            observations=suggestion.repetitions,
            destructive=any(is_destructive_action(s.action) for s in steps),
        )
        self._workflows[wf.id] = wf
        return wf

    def create_manual(self, name: str, actions: Sequence[str]) -> Optional[Workflow]:
        if len(self._workflows) >= MAX_WORKFLOWS:
            return None
        actions = [str(a)[:64] for a in actions if str(a)[:64]][
            :MAX_STEPS_PER_WORKFLOW]
        if not actions:
            return None
        wf = Workflow(
            id=uuid.uuid4().hex[:12], name=name[:80],
            steps=[WorkflowStep(action=a) for a in actions],
            confidence=1.0, provenance="user-defined",
            observations=0,
            destructive=any(is_destructive_action(a) for a in actions),
        )
        self._workflows[wf.id] = wf
        return wf

    def get(self, wf_id: str) -> Optional[Workflow]:
        return self._workflows.get(str(wf_id)[:64])

    def find_by_name(self, name: str) -> Optional[Workflow]:
        n = str(name or "").strip().lower()[:80]
        for wf in self._workflows.values():
            if wf.name.lower() == n:
                return wf
        return None

    def all(self) -> List[Workflow]:
        return sorted(self._workflows.values(), key=lambda w: w.name.lower())

    def remove(self, wf_id: str) -> bool:
        return self._workflows.pop(str(wf_id)[:64], None) is not None

    def __len__(self) -> int:
        return len(self._workflows)

    # -- persistence (validated) --------------------------------------------------

    def export_data(self) -> Dict[str, Any]:
        return {"version": 1, "kind": "airmouse-workflows",
                "workflows": [w.to_dict() for w in self.all()]}

    def import_data(self, data: Dict[str, Any]) -> int:
        if not isinstance(data, dict) or data.get("kind") not in (
                "airmouse-workflows", None):
            return 0
        rows = data.get("workflows")
        if not isinstance(rows, list):
            return 0
        accepted = 0
        for row in rows[:MAX_WORKFLOWS]:
            try:
                wf = Workflow.from_dict(row)
            except Exception:
                continue
            if len(self._workflows) >= MAX_WORKFLOWS:
                break
            self._workflows[wf.id] = wf
            accepted += 1
        return accepted

    def save(self, path: str) -> int:
        import json, os
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        payload = json.dumps(self.export_data(), ensure_ascii=False,
                             sort_keys=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
        return len(payload)

    @classmethod
    def load(cls, path: str) -> "WorkflowStore":
        import json
        store = cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                store.import_data(json.load(f))
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return store


class WorkflowRunner:
    """Policy-gated workflow execution.

    ``executor``: callable(step: WorkflowStep) -> bool — the ONLY thing
    that can touch the outside world.  The runner itself never executes.

    ``confirm``: callable(Workflow, WorkflowStep) -> bool — required for
    destructive steps; False aborts.
    """

    def __init__(self,
                 executor: Callable[[WorkflowStep], bool],
                 confirm: Optional[Callable[[Workflow, WorkflowStep], bool]] = None,
                 max_steps: int = MAX_STEPS_PER_WORKFLOW) -> None:
        self.executor = executor
        self.confirm = confirm
        self.max_steps = max(1, int(max_steps))
        self.aborted_destructive = 0

    def preview(self, workflow: Workflow) -> str:
        """Human-readable plan (§16: show what will happen first)."""
        lines = [f"Workflow '{workflow.name}' — {len(workflow.steps)} steps:"]
        for i, s in enumerate(workflow.steps, 1):
            mark = " ⚠ destructive" if is_destructive_action(s.action) else ""
            lines.append(f"  {i}. {s.action}{mark}")
        if workflow.destructive:
            lines.append("  (contains destructive steps — "
                         "each will ask for confirmation)")
        return "\n".join(lines)

    def run(self, workflow: Workflow,
            conditions: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
        """Execute an approved workflow under policy.  Returns
        (success, message).  Never raises."""
        if workflow is None:
            return (False, "no_workflow")
        if not workflow.enabled:
            return (False, "workflow_disabled")
        if len(workflow.steps) > self.max_steps:
            return (False, "too_many_steps")
        # conditions must hold (app context etc.)
        for key, expected in workflow.conditions.items():
            actual = (conditions or {}).get(key)
            if actual is not None and actual != expected:
                return (False, f"condition_failed:{key}")
        # destructive workflows must have been previewed at least once
        if workflow.destructive and not workflow.previewed:
            return (False, "destructive_workflow_not_previewed")
        for step in workflow.steps:
            if is_destructive_action(step.action):
                ok = bool(self.confirm(workflow, step)) if self.confirm else False
                if not ok:
                    self.aborted_destructive += 1
                    workflow.failure_count += 1
                    return (False, f"destructive_step_refused:{step.action}")
            try:
                result = bool(self.executor(step))
            except Exception as exc:
                workflow.failure_count += 1
                return (False, f"step_error:{exc}")
            if not result:
                workflow.failure_count += 1
                return (False, f"step_failed:{step.action}")
        workflow.success_count += 1
        return (True, "ok")

    def mark_previewed(self, workflow: Workflow) -> None:
        if workflow is not None:
            workflow.previewed = True


# ─────────────────────────────────────────────────────────────────────────────
# proactive assistance (§28)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Suggestion:
    """A proactive suggestion — DATA ONLY.  PREDICTION ≠ EXECUTION."""

    text: str            # what is suggested, e.g. "Open VS Code?"
    reason: str          # explainable rationale
    confidence: float
    kind: str = "action"  # action | workflow | emoji | text
    prepared: bool = False


class ProactiveAssistant:
    """Observe → Predict → Suggest → (optionally) Prepare safe resources.

    The assistant can NEVER execute.  It produces :class:`Suggestion`
    objects; destructive-looking suggestions are suppressed entirely.
    """

    def __init__(self, predictor=None, workflows: Optional[WorkflowStore] = None) -> None:
        self.predictor = predictor
        self.workflows = workflows
        self.enabled = True
        self.suggestions_made = 0
        self.suggestions_accepted = 0

    def suggest(self, action_history: Sequence[str] = (),
                hour: Optional[int] = None,
                k: int = 2) -> List[Suggestion]:
        """Return up to k non-destructive suggestions."""
        if not self.enabled or self.predictor is None:
            return []
        out: List[Suggestion] = []
        seen = set()
        pa = self.predictor.predict_next_action(action_history or [])
        if pa.value and pa.confidence >= 0.5 \
                and not is_destructive_action(pa.value):
            out.append(Suggestion(
                text=f"{pa.value.replace('_', ' ').capitalize()}?",
                reason=pa.reason, confidence=pa.confidence, kind="action"))
            seen.add(pa.value)
        pc = self.predictor.predict_command(hour=hour)
        if pc.value and pc.confidence >= 0.4 and pc.value not in seen \
                and not is_destructive_action(pc.value):
            out.append(Suggestion(
                text=f"{pc.value}?", reason=pc.reason,
                confidence=pc.confidence, kind="command"))
        self.suggestions_made += len(out)
        return out[:max(0, int(k))]

    def record_acceptance(self, accepted: bool) -> None:
        if accepted:
            self.suggestions_accepted += 1

    def prepare(self, resource: str) -> bool:
        """Prepare SAFE resources only (context pre-resolution).

        Deliberately returns False for anything that looks like a URL,
        path, or command — preparation never touches those.
        """
        r = str(resource or "").strip()[:64]
        if not r or any(c in r for c in ("/", "\\", ":", " ", "&", "|", ";")):
            return False
        return r.replace("_", "").isalnum()
