"""
airmouse.goals — COMMAND → INTENT → TASK → GOAL hierarchy (v13 §4).

A deterministic interpretation layer that classifies a natural-language
objective into the four-level hierarchy:

    COMMAND  "open Chrome"                — single atomic instruction
    INTENT   "I want to research AI"      — an aim needing several commands
    TASK     "Research AI computer-use systems" — a bounded unit of work
    GOAL     "Determine whether AirMouse can compete" — a outcome to reach

HARD RULES (§4):
    * Deterministic parsing first — pattern tables, never magic.
    * Optional intelligence-assisted interpretation is an ADAPTER:
      the parser works fully without it and its output is labelled.
    * Every interpretation exposes: intent, confidence, context,
      proposed plan, risk, required permissions, required
      confirmations.
    * PREDICTION ≠ PERMISSION ≠ EXECUTION.  Nothing here executes or
      grants anything.  Risky objectives get required_confirmations.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# bounded constants
# ─────────────────────────────────────────────────────────────────────────────

MAX_UTTERANCE = 300
MAX_PLAN_STEPS = 12
MAX_PERMISSIONS = 8
MAX_NAME = 80

# deterministic pattern tables (checked in order; first match wins)

_COMMAND_RE = re.compile(
    r"^(open|close|launch|quit|click|press|type|scroll|switch|mute|unmute|"
    r"copy|paste|cut|save|undo|redo|zoom|start|stop|pause|play|next|prev|"
    r"take|show|hide|minimize|maximize|kill)\b", re.IGNORECASE)

_INTENT_RE = re.compile(
    r"^(i want to|i'd like to|i would like to|let'?s|i need to|"
    r"help me|i am trying to|im trying to|i'm trying to)\b", re.IGNORECASE)

_GOAL_RE = re.compile(
    r"\b(figure out|determine|find out|decide|evaluate|assess|"
    r"whether|compete|outperform|beat)\b", re.IGNORECASE)

_TASK_RE = re.compile(
    r"^(research|prepare|write|build|plan|organize|organise|draft|"
    r"review|summarize|summarise|compare|test|design|create|set up|setup)\b",
    re.IGNORECASE)

# risk tables (§4 — risk drives permissions + confirmations)

_DESTRUCTIVE_TOKENS = re.compile(
    r"\b(delete|remove|format|wipe|erase|destroy|drop|overwrite|"
    r"shut ?down|shutdown|restart|reboot|uninstall|kill)\b", re.IGNORECASE)

_SENSITIVE_TOKENS = re.compile(
    r"\b(password|credential|payment|billing|bank|send money|purchase|"
    r"buy|checkout|publish|post|share publicly|email\b.*\bsend)\b",
    re.IGNORECASE)

_NAV_TOKENS = re.compile(r"\b(open|launch|navigate|browse|visit|go to)\b",
                         re.IGNORECASE)

_TYPE_TOKENS = re.compile(r"\b(type|write|enter|fill|paste)\b",
                          re.IGNORECASE)


class ObjectiveLevel(enum.Enum):
    COMMAND = "command"
    INTENT = "intent"
    TASK = "task"
    GOAL = "goal"


class RiskLevel(enum.Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class PlannedStep:
    """One proposed plan step (PROPOSAL ONLY — never executed)."""

    index: int
    objective: str
    suggested_action: str = "none"
    risk: str = RiskLevel.LOW.value


@dataclass(frozen=True)
class Objective:
    """A classified objective at one hierarchy level (§4)."""

    level: ObjectiveLevel = ObjectiveLevel.COMMAND
    name: str = ""
    utterance: str = ""
    confidence: float = 0.0
    context: Dict[str, str] = field(default_factory=dict)
    proposed_plan: Tuple[PlannedStep, ...] = ()
    risk: RiskLevel = RiskLevel.NONE
    required_permissions: Tuple[str, ...] = ()
    required_confirmations: Tuple[str, ...] = ()
    parsed_by: str = "deterministic"    # or "intelligence_adapter"
    execution_allowed: bool = False     # ALWAYS False here (§4 hard rule)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "name": self.name,
            "utterance": self.utterance,
            "confidence": round(self.confidence, 4),
            "context": dict(sorted(self.context.items())),
            "proposed_plan": [
                {"index": s.index, "objective": s.objective,
                 "suggested_action": s.suggested_action,
                 "risk": s.risk} for s in self.proposed_plan],
            "risk": self.risk.value,
            "required_permissions": list(self.required_permissions),
            "required_confirmations": list(self.required_confirmations),
            "parsed_by": self.parsed_by,
            "execution_allowed": self.execution_allowed,
        }


@dataclass(frozen=True)
class HierarchyLink:
    """Parent/child edge produced by decomposition (§4)."""

    parent_level: str = ""
    parent_name: str = ""
    child_level: str = ""
    child_name: str = ""
    confidence: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# parser
# ─────────────────────────────────────────────────────────────────────────────


def _clean(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text.strip())[:MAX_UTTERANCE]


def _risk_for(utterance: str) -> RiskLevel:
    if _DESTRUCTIVE_TOKENS.search(utterance):
        return RiskLevel.DESTRUCTIVE
    if _SENSITIVE_TOKENS.search(utterance):
        return RiskLevel.HIGH
    if _TYPE_TOKENS.search(utterance):
        return RiskLevel.MEDIUM
    if _NAV_TOKENS.search(utterance):
        return RiskLevel.LOW
    return RiskLevel.NONE


def _permissions_for(risk: RiskLevel, utterance: str) -> Tuple[str, ...]:
    perms = []
    if _NAV_TOKENS.search(utterance):
        perms.append("application.launch")
    if _TYPE_TOKENS.search(utterance):
        perms.append("type.text")
    if risk is RiskLevel.HIGH:
        perms.append("sensitive.operation")
    if risk is RiskLevel.DESTRUCTIVE:
        perms.append("destructive.action")
    return tuple(perms[:MAX_PERMISSIONS])


def _confirmations_for(risk: RiskLevel) -> Tuple[str, ...]:
    if risk is RiskLevel.DESTRUCTIVE:
        return ("confirm_destructive",)
    if risk is RiskLevel.HIGH:
        return ("confirm_sensitive",)
    return ()


def _plan_for(level: ObjectiveLevel, name: str, utterance: str
              ) -> Tuple[PlannedStep, ...]:
    """Deterministic PLAN TEMPLATE per level (proposal, never run)."""
    if level is ObjectiveLevel.COMMAND:
        verb = name.split(" ", 1)[0] if name else "do"
        return (PlannedStep(0, name, suggested_action=verb,
                            risk=_risk_for(utterance).value),)
    if level is ObjectiveLevel.INTENT:
        return (
            PlannedStep(0, f"clarify scope: {name}", "clarify",
                        RiskLevel.NONE.value),
            PlannedStep(1, f"plan steps for: {name}", "plan",
                        RiskLevel.LOW.value),
            PlannedStep(2, f"execute with verification: {name}", "execute",
                        _risk_for(utterance).value),
        )
    if level is ObjectiveLevel.TASK:
        return (
            PlannedStep(0, f"gather context for: {name}", "observe",
                        RiskLevel.LOW.value),
            PlannedStep(1, f"perform: {name}", "execute",
                        _risk_for(utterance).value),
            PlannedStep(2, f"verify outcome: {name}", "verify",
                        RiskLevel.LOW.value),
        )
    # GOAL: decompose into bounded task suggestions
    return (
        PlannedStep(0, f"define success criteria: {name}", "plan",
                    RiskLevel.LOW.value),
        PlannedStep(1, f"identify tasks toward: {name}", "plan",
                    RiskLevel.LOW.value),
        PlannedStep(2, f"review evidence for: {name}", "review",
                    RiskLevel.LOW.value),
        PlannedStep(3, f"decide and record outcome: {name}", "decide",
                    RiskLevel.LOW.value),
    )


class GoalHierarchyParser:
    """Deterministic COMMAND/INTENT/TASK/GOAL parser (v13 §4).

    ``interpreter`` is an OPTIONAL adapter (e.g. a local model wrapper).
    If provided it may re-classify LOW-CONFIDENCE results; its output
    is labelled ``parsed_by="intelligence_adapter"`` and is bounded to
    the same levels — it can NEVER mark anything execution_allowed.
    """

    def __init__(self, interpreter: Optional[Any] = None) -> None:
        self.interpreter = interpreter

    # ── classification ──────────────────────────────────────────────────

    def parse(self, utterance: Any,
              context: Optional[Dict[str, str]] = None) -> Objective:
        text = _clean(utterance)
        if not text:
            return Objective(level=ObjectiveLevel.COMMAND, name="",
                             utterance="", confidence=0.0,
                             context=dict(context or {}))
        level, confidence = self._classify(text)
        name = self._name_for(level, text)
        risk = _risk_for(text)
        ctx = {k: str(v)[:40] for k, v in
               sorted((context or {}).items())[:6]}
        obj = Objective(
            level=level, name=name, utterance=text, confidence=confidence,
            context=ctx, proposed_plan=_plan_for(level, name, text),
            risk=risk, required_permissions=_permissions_for(risk, text),
            required_confirmations=_confirmations_for(risk),
            parsed_by="deterministic", execution_allowed=False)
        if self.interpreter is not None and confidence < 0.55:
            upgraded = self._ask_interpreter(obj)
            if upgraded is not None:
                return upgraded
        return obj

    def _classify(self, text: str) -> Tuple[ObjectiveLevel, float]:
        if _COMMAND_RE.match(text) and len(text.split()) <= 6:
            return ObjectiveLevel.COMMAND, 0.9
        if _INTENT_RE.match(text):
            return ObjectiveLevel.INTENT, 0.8
        if _GOAL_RE.search(text):
            return ObjectiveLevel.GOAL, 0.7
        if _TASK_RE.match(text):
            return ObjectiveLevel.TASK, 0.75
        # multi-clause statements trend toward TASK
        if len(text.split()) > 6:
            return ObjectiveLevel.TASK, 0.5
        return ObjectiveLevel.COMMAND, 0.45

    def _name_for(self, level: ObjectiveLevel, text: str) -> str:
        # strip leading filler deterministically
        filler = (r"^(i want to|i'd like to|i would like to|let'?s|"
                  r"i need to|help me|please|can you|could you)\s+")
        name = re.sub(filler, "", text, flags=re.IGNORECASE).strip()
        if not name:
            name = text
        # deterministic normalization of the leading verb for commands
        if level is ObjectiveLevel.COMMAND:
            name = name.lower()
        return name[:MAX_NAME]

    def _ask_interpreter(self, obj: Objective) -> Optional[Objective]:
        """Optional adapter hook.  The adapter is UNTRUSTED: its answer
        is validated against the enum set and can never enable
        execution (§4 hard rule)."""
        try:
            raw = self.interpreter(obj.utterance, obj.to_dict())
            if not isinstance(raw, dict):
                return None
            level = raw.get("level")
            try:
                lv = ObjectiveLevel(str(level))
            except (ValueError, TypeError):
                return None
            conf = max(0.0, min(0.99, float(raw.get("confidence", 0.0))))
            name = _clean(raw.get("name", obj.name))[:MAX_NAME] or obj.name
            # SECURITY (§30): risk is ALWAYS recomputed deterministically
            # from the utterance — an untrusted adapter may never
            # downgrade it.
            risk = _risk_for(obj.utterance)
            return Objective(
                level=lv, name=name, utterance=obj.utterance,
                confidence=conf, context=obj.context,
                proposed_plan=_plan_for(lv, name, obj.utterance),
                risk=risk,
                required_permissions=_permissions_for(risk, obj.utterance),
                required_confirmations=_confirmations_for(risk),
                parsed_by="intelligence_adapter",
                execution_allowed=False)   # hard rule, adapter cannot flip
        except Exception:
            return None

    # ── decomposition (§4 hierarchy) ────────────────────────────────────

    def decompose(self, objective: Objective) -> List[HierarchyLink]:
        """Expand an objective into child hierarchy links (deterministic
        templates).  Output is a PROPOSAL — creating real tasks is the
        TaskEngine's job behind approvals (§5)."""
        links: List[HierarchyLink] = []
        if objective.level is ObjectiveLevel.GOAL:
            tasks = [s.objective for s in objective.proposed_plan]
            for t in tasks[:MAX_PLAN_STEPS]:
                links.append(HierarchyLink(
                    "goal", objective.name, "task", t,
                    round(objective.confidence * 0.9, 4)))
        elif objective.level is ObjectiveLevel.TASK:
            for s in objective.proposed_plan[:MAX_PLAN_STEPS]:
                links.append(HierarchyLink(
                    "task", objective.name, "intent", s.objective,
                    round(objective.confidence * 0.85, 4)))
        elif objective.level is ObjectiveLevel.INTENT:
            for s in objective.proposed_plan[:MAX_PLAN_STEPS]:
                links.append(HierarchyLink(
                    "intent", objective.name, "command", s.objective,
                    round(objective.confidence * 0.8, 4)))
        return links

    def hierarchy_of(self, utterance: Any,
                     context: Optional[Dict[str, str]] = None
                     ) -> Dict[str, Any]:
        """Full hierarchy view: objective + decomposed children."""
        obj = self.parse(utterance, context)
        return {
            "objective": obj.to_dict(),
            "children": [l.__dict__ for l in self.decompose(obj)],
            "prediction_only": True,       # §4: PREDICTION ≠ EXECUTION
        }
