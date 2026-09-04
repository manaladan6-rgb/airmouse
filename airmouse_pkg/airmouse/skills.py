"""
airmouse.skills — Interaction Compression + Personal Skill Library
(v13.5 §6).

Semantic workflow discovery: learn REPEATED interaction sequences and
— only with explicit user approval — convert them into semantic
Skills.

HARD RULES (§6):
    1.  Never silently create automations.  A skill exists only after:
            repeated behavior  (>= MIN_OCCURRENCES identical sequences)
            confidence threshold
            semantic clustering  (same action/template signature)
            user notification
            preview
            approval
    2.  A Skill must NOT depend on screen coordinates.  Targets are
        semantic / accessibility / DOM / OCR / visual first; raw
        coordinates are an explicit, flagged fallback only.
    3.  Skills are inspectable, editable (versioned), exportable,
        importable, permission-aware and revocable.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# bounded constants (§6)
# ─────────────────────────────────────────────────────────────────────────────

MAX_SKILLS = 100
MAX_STEPS_PER_SKILL = 24
MAX_OBSERVED_SEQUENCES = 64
MIN_OCCURRENCES = 3
MIN_CONFIDENCE = 0.6
SKILL_FORMAT_VERSION = 1
MAX_NAME = 60
MAX_DESC = 200
MAX_TARGET_VALUE = 120

_VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9 _-]{1,59}$")
_SECRET_HINTS = ("password", "token", "secret", "credential", "api_key")


class TargetKind(enum.Enum):
    SEMANTIC = "semantic"          # preferred: meaning-based ("Submit button")
    ACCESSIBILITY = "accessibility"  # a11y tree node
    DOM = "dom"                    # browser bridge DOM selector
    OCR = "ocr"                    # on-screen text match
    VISUAL = "visual"              # template/appearance match
    COORDINATE = "coordinate"      # LAST-RESORT fallback (§6 rule 2)


@dataclass(frozen=True)
class SkillTarget:
    """A resolution-ordered target descriptor (§6 semantic targets)."""

    kind: str = TargetKind.SEMANTIC.value
    value: str = ""
    fallback: Optional[Tuple[str, str]] = None   # (kind, value) chain tail
    coordinate_fallback: bool = False            # explicit flag (§6)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "fallback": list(self.fallback) if self.fallback else None,
            "coordinate_fallback": self.coordinate_fallback,
        }


@dataclass
class SkillStep:
    """One step of a skill (structured, target-based, §6)."""

    index: int = 0
    description: str = ""
    action: str = "none"
    target: SkillTarget = field(default_factory=SkillTarget)
    params: Dict[str, str] = field(default_factory=dict)
    expected_result: str = ""
    risk: str = "low"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "description": self.description[:MAX_DESC],
            "action": self.action,
            "target": self.target.to_dict(),
            "params": dict(sorted(self.params.items())),
            "expected_result": self.expected_result[:MAX_DESC],
            "risk": self.risk,
        }


@dataclass
class Skill:
    """A versioned, permission-aware, revocable automation (§6)."""

    skill_id: str = ""
    name: str = ""
    version: int = 1
    description: str = ""
    steps: List[SkillStep] = field(default_factory=list)
    source_workflow_id: str = ""
    required_permissions: Tuple[str, ...] = ()
    risk: str = "low"
    enabled: bool = True
    confidence: float = 0.0
    usage_count: int = 0
    success_count: int = 0
    created_wall: str = ""
    updated_wall: str = ""
    cluster_signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "source_workflow_id": self.source_workflow_id,
            "required_permissions": list(self.required_permissions),
            "risk": self.risk,
            "enabled": self.enabled,
            "confidence": round(self.confidence, 4),
            "usage_count": self.usage_count,
            "success_count": self.success_count,
            "created_wall": self.created_wall,
            "updated_wall": self.updated_wall,
            "cluster_signature": self.cluster_signature,
        }


# ─────────────────────────────────────────────────────────────────────────────
# observation → proposal (§6 steps 1-3: repetition, confidence, clustering)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SequenceCluster:
    """A cluster of repeated interaction sequences (§6)."""

    signature: str = ""                 # action-template fingerprint
    occurrences: int = 0
    action_names: Tuple[str, ...] = ()
    target_kinds: Tuple[str, ...] = ()
    avg_confidence: float = 0.0
    sample_actions: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signature": self.signature,
            "occurrences": self.occurrences,
            "action_names": list(self.action_names),
            "target_kinds": list(self.target_kinds),
            "avg_confidence": round(self.avg_confidence, 4),
        }


def _utcnow() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _signature_for(actions: Sequence[Dict[str, Any]]) -> str:
    """Deterministic template signature: action names + target KINDS,
    deliberately IGNORING coordinates/values (§6 rule 2)."""
    parts = []
    for a in actions[:MAX_STEPS_PER_SKILL]:
        if not isinstance(a, dict):
            continue
        name = str(a.get("action", "none"))[:40].lower()
        tgt = a.get("target")
        kind = "none"
        if isinstance(tgt, dict):
            kind = str(tgt.get("kind", "semantic"))[:20]
        elif isinstance(tgt, str):
            kind = "semantic"
        parts.append(f"{name}:{kind}")
    return "->".join(parts)


class InteractionCompression:
    """Watches interaction sequences; proposes skills; never silent (§6)."""

    def __init__(self, min_occurrences: int = MIN_OCCURRENCES,
                 min_confidence: float = MIN_CONFIDENCE) -> None:
        self.min_occurrences = max(2, int(min_occurrences))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self._clusters: Dict[str, Dict[str, Any]] = {}
        self._recent: List[str] = []       # bounded recent signatures

    def observe_sequence(self, actions: Any,
                         confidence: float = 0.5) -> Optional[SequenceCluster]:
        """Record one observed interaction sequence (§6 step 1)."""
        try:
            if not isinstance(actions, (list, tuple)) or not actions:
                return None
            if len(actions) < 2 or len(actions) > MAX_STEPS_PER_SKILL:
                return None
            conf = max(0.0, min(1.0, float(confidence)))
            sig = _signature_for(actions)
            if not sig or sig in ("none",) or "->" not in sig:
                return None
            names = tuple(str(a.get("action", "none"))[:40]
                          for a in actions if isinstance(a, dict))
            kinds = tuple(
                str(a.get("target", {}).get("kind", "semantic"))[:20]
                if isinstance(a.get("target"), dict) else "semantic"
                for a in actions if isinstance(a, dict))
            entry = self._clusters.get(sig)
            if entry is None:
                if len(self._clusters) >= MAX_OBSERVED_SEQUENCES:
                    # evite weakest
                    weak = min(self._clusters,
                               key=lambda k: (self._clusters[k]["occurrences"],
                                              self._clusters[k]["confidence_sum"]))
                    del self._clusters[weak]
                entry = {"occurrences": 0, "confidence_sum": 0.0,
                         "actions": names, "kinds": kinds}
                self._clusters[sig] = entry
            entry["occurrences"] += 1
            entry["confidence_sum"] += conf
            self._recent.append(sig)
            if len(self._recent) > MAX_OBSERVED_SEQUENCES * 4:
                self._recent = self._recent[-MAX_OBSERVED_SEQUENCES * 2:]
            return self.cluster(sig)
        except Exception:
            return None

    def cluster(self, signature: str) -> Optional[SequenceCluster]:
        entry = self._clusters.get(signature)
        if entry is None:
            return None
        n = entry["occurrences"]
        return SequenceCluster(
            signature=signature, occurrences=n,
            action_names=entry["actions"], target_kinds=entry["kinds"],
            avg_confidence=entry["confidence_sum"] / max(1, n),
            sample_actions=entry["actions"])

    def clusters(self) -> List[SequenceCluster]:
        rows = [self.cluster(sig) for sig in self._clusters]
        rows = [r for r in rows if r]
        rows.sort(key=lambda c: (-c.occurrences, c.signature))
        return rows

    def candidates(self) -> List[SequenceCluster]:
        """Clusters meeting repetition + confidence thresholds (§6)."""
        return [c for c in self.clusters()
                if c.occurrences >= self.min_occurrences and
                c.avg_confidence >= self.min_confidence]

    def propose(self) -> Optional[Dict[str, Any]]:
        """Build the TOP candidate proposal for user review (§6 steps
        4-5: notification + preview).  Returns None when nothing
        qualifies.  The proposal is DATA — approval is a separate,
        explicit act."""
        for c in self.candidates():
            return {
                "proposal": True,
                "signature": c.signature,
                "occurrences": c.occurrences,
                "avg_confidence": round(c.avg_confidence, 4),
                "preview_steps": [
                    {"index": i, "action": a}
                    for i, a in enumerate(c.action_names)],
                "requires": "user_approval",
                "silent": False,
            }
        return None


# ─────────────────────────────────────────────────────────────────────────────
# personal skill library (§6)
# ─────────────────────────────────────────────────────────────────────────────


def _clean_target(raw: Any) -> SkillTarget:
    """Build a target descriptor; coordinates demoted to flagged
    fallback (§6 rule 2)."""
    if isinstance(raw, str):
        return SkillTarget(kind=TargetKind.SEMANTIC.value,
                           value=raw[:MAX_TARGET_VALUE])
    if isinstance(raw, dict):
        kind = str(raw.get("kind", TargetKind.SEMANTIC.value))
        try:
            TargetKind(kind)
        except ValueError:
            kind = TargetKind.SEMANTIC.value
        value = str(raw.get("value", ""))[:MAX_TARGET_VALUE]
        fb = raw.get("fallback")
        fb_t: Optional[Tuple[str, str]] = None
        if isinstance(fb, (list, tuple)) and len(fb) == 2:
            fb_t = (str(fb[0])[:20], str(fb[1])[:MAX_TARGET_VALUE])
        coord = bool(raw.get("coordinate_fallback", False))
        if kind == TargetKind.COORDINATE.value:
            coord = True       # coordinates are ALWAYS flagged (§6)
        return SkillTarget(kind=kind, value=value, fallback=fb_t,
                           coordinate_fallback=coord)
    return SkillTarget()


def _skills_risk_for(steps: List[SkillStep]) -> str:
    for s in steps:
        if s.risk == "destructive":
            return "destructive"
    for s in steps:
        if s.risk in ("high", "medium"):
            return s.risk
    return "low"


def _permissions_for(actions: Sequence[str]) -> Tuple[str, ...]:
    perms = set()
    for a in actions:
        if a in ("open_app", "launch", "navigate"):
            perms.add("application.launch")
        if a in ("type_text", "write"):
            perms.add("type.text")
        if a in ("click", "double_click", "right_click"):
            perms.add("mouse.click")
        if a in ("delete", "remove", "wipe", "purge"):
            perms.add("destructive.action")
        if a in ("read_file", "open_file"):
            perms.add("file.read")
        if a in ("write_file", "save_file"):
            perms.add("file.write")
    return tuple(sorted(perms))


class PersonalSkillLibrary:
    """Bounded, inspectable, revocable skill store (§6)."""

    def __init__(self, max_skills: int = MAX_SKILLS) -> None:
        self.max_skills = max(8, min(int(max_skills), MAX_SKILLS))
        self._skills: Dict[str, Skill] = {}
        self._counter = 0

    # ── creation (approval happens OUTSIDE, by the caller) ─────────────

    def create_skill_from_cluster(self, cluster: SequenceCluster,
                                  name: Any, description: str = "",
                                  targets: Optional[List[Any]] = None,
                                  source_workflow_id: str = ""
                                  ) -> Optional[Skill]:
        """Convert an approved cluster into a skill (§6 step 6)."""
        try:
            name_c = str(name or "").strip().lower()[:MAX_NAME]
            if not _VALID_NAME_RE.match(name_c):
                return None
            if any(h in name_c for h in _SECRET_HINTS):
                return None
            if len(self._skills) >= self.max_skills:
                self._evict_weakest()
            self._counter += 1
            skill_id = f"skill-{self._counter:04d}"
            steps: List[SkillStep] = []
            for i, action in enumerate(cluster.action_names[:MAX_STEPS_PER_SKILL]):
                tgt = _clean_target(targets[i]) if targets and i < len(
                    targets) else SkillTarget()
                steps.append(SkillStep(
                    index=i, description=f"step {i + 1}",
                    action=str(action)[:40], target=tgt))
            skill = Skill(
                skill_id=skill_id, name=name_c,
                description=str(description or "")[:MAX_DESC],
                steps=steps,
                source_workflow_id=str(source_workflow_id or "")[:40],
                required_permissions=_permissions_for(
                    [s.action for s in steps]),
                risk=_skills_risk_for(steps),
                confidence=round(cluster.avg_confidence, 4),
                created_wall=_utcnow(), updated_wall=_utcnow(),
                cluster_signature=cluster.signature[:120])
            self._skills[skill_id] = skill
            return skill
        except Exception:
            return None

    def create_skill(self, name: Any, steps_spec: List[Dict[str, Any]],
                     description: str = "") -> Optional[Skill]:
        """Direct skill creation (used by import and advanced users)."""
        try:
            name_c = str(name or "").strip().lower()[:MAX_NAME]
            if not _VALID_NAME_RE.match(name_c):
                return None
            if not isinstance(steps_spec, list) or not steps_spec or \
                    len(steps_spec) > MAX_STEPS_PER_SKILL:
                return None
            if len(self._skills) >= self.max_skills:
                self._evict_weakest()
            self._counter += 1
            skill_id = f"skill-{self._counter:04d}"
            steps = []
            for i, raw in enumerate(steps_spec):
                if not isinstance(raw, dict):
                    return None
                steps.append(SkillStep(
                    index=i,
                    description=str(raw.get("description", ""))[:MAX_DESC],
                    action=str(raw.get("action", "none"))[:40],
                    target=_clean_target(raw.get("target")),
                    params={str(k)[:20]: str(v)[:60]
                            for k, v in list(
                                (raw.get("params") or {}).items())[:6]},
                    expected_result=str(raw.get("expected_result", ""))[:MAX_DESC],
                    risk=str(raw.get("risk", "low"))[:12]))
            skill = Skill(
                skill_id=skill_id, name=name_c,
                description=str(description or "")[:MAX_DESC], steps=steps,
                required_permissions=_permissions_for(
                    [s.action for s in steps]),
                risk=_skills_risk_for(steps),
                created_wall=_utcnow(), updated_wall=_utcnow())
            self._skills[skill_id] = skill
            return skill
        except Exception:
            return None

    # ── lifecycle (§6: inspectable, editable, revocable) ────────────────

    def get(self, skill_id: str) -> Optional[Dict[str, Any]]:
        skill = self._skills.get(skill_id)
        return skill.to_dict() if skill else None

    def find_by_name(self, name: str) -> Optional[Skill]:
        return self._skills.get("") or next(
            (s for s in self._skills.values()
             if s.name == str(name).strip().lower()), None)

    def list_skills(self, include_disabled: bool = True,
                    limit: int = 100) -> List[Dict[str, Any]]:
        rows = []
        for s in self._skills.values():
            if not include_disabled and not s.enabled:
                continue
            rows.append(s.to_dict())
        rows.sort(key=lambda d: d["skill_id"])
        return rows[: max(1, min(int(limit), MAX_SKILLS))]

    def edit(self, skill_id: str, changes: Optional[Dict[str, Any]] = None
             ) -> Optional[Skill]:
        """Versioned edit (§6 editable + versioned)."""
        skill = self._skills.get(skill_id)
        if skill is None or not isinstance(changes, dict):
            return None
        if "description" in changes:
            skill.description = str(changes["description"])[:MAX_DESC]
        if "steps" in changes and isinstance(changes["steps"], list) and \
                changes["steps"]:
            new_steps = []
            for i, raw in enumerate(changes["steps"][:MAX_STEPS_PER_SKILL]):
                if not isinstance(raw, dict):
                    return None
                new_steps.append(SkillStep(
                    index=i, action=str(raw.get("action", "none"))[:40],
                    target=_clean_target(raw.get("target")),
                    description=str(raw.get("description", ""))[:MAX_DESC],
                    risk=str(raw.get("risk", "low"))[:12]))
            skill.steps = new_steps
            skill.required_permissions = _permissions_for(
                [s.action for s in new_steps])
            skill.risk = _skills_risk_for(new_steps)
        skill.version += 1
        skill.updated_wall = _utcnow()
        return skill

    def set_enabled(self, skill_id: str, enabled: bool) -> bool:
        skill = self._skills.get(skill_id)
        if skill is None:
            return False
        skill.enabled = bool(enabled)
        skill.updated_wall = _utcnow()
        return True

    def revoke(self, skill_id: str) -> bool:
        """Remove a skill outright (§6 revocable)."""
        if skill_id in self._skills:
            del self._skills[skill_id]
            return True
        return False

    def record_use(self, skill_id: str, success: bool) -> bool:
        skill = self._skills.get(skill_id)
        if skill is None:
            return False
        skill.usage_count += 1
        if success:
            skill.success_count += 1
        return True

    # ── persistence (§6 exportable / importable) ────────────────────────

    def export(self) -> Dict[str, Any]:
        rows = [s.to_dict() for s in self._skills.values()]
        rows.sort(key=lambda d: d["skill_id"])
        return {"format": "airmouse-skills",
                "version": SKILL_FORMAT_VERSION,
                "skill_count": len(rows), "skills": rows}

    def import_skills(self, data: Any) -> Tuple[int, int]:
        """Validated import.  Returns (imported, rejected).  Fail-closed
        on wrong format, oversized payloads, invalid names/steps and
        secret-looking names."""
        imported = rejected = 0
        try:
            if not isinstance(data, dict) or \
                    data.get("format") != "airmouse-skills" or \
                    int(data.get("version", 0)) != SKILL_FORMAT_VERSION:
                return 0, 1
            skills = data.get("skills")
            if not isinstance(skills, list):
                return 0, 1
            for row in skills[:MAX_SKILLS * 2]:
                if not isinstance(row, dict):
                    rejected += 1
                    continue
                before = len(self._skills)
                skill = self.create_skill(
                    row.get("name"), row.get("steps") or [],
                    description=row.get("description", ""))
                if skill is None or len(self._skills) == before:
                    rejected += 1
                    continue
                skill.confidence = max(0.0, min(
                    1.0, float(row.get("confidence", 0.5) or 0.5)))
                skill.enabled = bool(row.get("enabled", True))
                imported += 1
            return imported, rejected
        except Exception:
            return imported, rejected + 1

    # ── internals ────────────────────────────────────────────────────────

    def _evict_weakest(self) -> None:
        if not self._skills:
            return
        # prefer unused, then least confident
        sid = min(self._skills,
                  key=lambda k: (self._skills[k].usage_count,
                                 self._skills[k].confidence))
        del self._skills[sid]
