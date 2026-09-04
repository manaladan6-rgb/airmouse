"""
airmouse.marketplace — Skill Marketplace Foundation (v15 §20).

SkillManifest format (§20).  Every skill declares:

    name · version · author · capabilities · permissions ·
    dependencies · required modalities · supported applications ·
    risk level · installation instructions · uninstall behavior

Operations (§20): install · enable · disable · update · rollback ·
remove · inspect.

TRUST BOUNDARY (§20 hard rule):  skill manifests are DATA.  Arbitrary
skill code NEVER executes without an explicit, per-skill trust grant,
and even then the runtime executes only pre-vetted ACTION NAMES from
the unified action vocabulary — never embedded scripts, never shell,
never fetched code.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .skills import MAX_STEPS_PER_SKILL, PersonalSkillLibrary, Skill

MAX_MANIFEST_BYTES = 256 * 1024
MANIFEST_FORMAT_VERSION = 1
MAX_LIST = 24

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9 ._-]{1,59}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_VALID_KEYS = {
    "name", "version", "author", "capabilities", "permissions",
    "dependencies", "required_modalities", "supported_applications",
    "risk_level", "installation", "uninstall_behavior", "description",
    "steps", "skill_id",
}


@dataclass
class InstalledSkill:
    """An installed marketplace skill (versioned, rollbackable)."""

    manifest: Dict[str, Any] = field(default_factory=dict)
    skill_id: str = ""              # link into PersonalSkillLibrary
    enabled: bool = True
    previous_version: str = ""      # for rollback (§20)
    previous_manifest: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.manifest.get("name", ""),
            "version": self.manifest.get("version", ""),
            "author": self.manifest.get("author", ""),
            "risk_level": self.manifest.get("risk_level", "low"),
            "enabled": self.enabled,
            "skill_id": self.skill_id,
            "previous_version": self.previous_version,
            "permissions": list(self.manifest.get("permissions", []))[:8],
        }


class Marketplace:
    """Deterministic skill marketplace foundation (§20)."""

    def __init__(self, library: Optional[PersonalSkillLibrary] = None) -> None:
        self.library = library or PersonalSkillLibrary()
        self._installed: Dict[str, InstalledSkill] = {}

    # ── manifest validation (§20 fail-closed) ───────────────────────────

    def validate_manifest(self, manifest: Any) -> Tuple[bool, List[str]]:
        errors: List[str] = []
        try:
            import json as _json
            if isinstance(manifest, str):
                if len(manifest) > MAX_MANIFEST_BYTES:
                    return False, ["manifest too large"]
                manifest = _json.loads(manifest)
            if not isinstance(manifest, dict):
                return False, ["manifest must be an object"]
            for key in manifest:
                if key not in _VALID_KEYS:
                    errors.append(f"unknown field: {key}")
            for req in ("name", "version", "author", "permissions",
                        "risk_level"):
                if req not in manifest:
                    errors.append(f"missing required field: {req}")
            if errors:
                return False, errors
            if not _NAME_RE.match(str(manifest.get("name", ""))):
                errors.append("invalid name")
            if not _VERSION_RE.match(str(manifest.get("version", ""))):
                errors.append("version must be semver x.y.z")
            risk = str(manifest.get("risk_level", ""))
            if risk not in ("none", "low", "medium", "high", "destructive"):
                errors.append("invalid risk_level")
            perms = manifest.get("permissions", [])
            if not isinstance(perms, list) or len(perms) > MAX_LIST:
                errors.append("permissions must be a bounded list")
            if manifest.get("steps") is not None:
                steps = manifest.get("steps")
                if not isinstance(steps, list) or not steps or \
                        len(steps) > MAX_STEPS_PER_SKILL:
                    errors.append(f"steps must be 1.."
                                  f"{MAX_STEPS_PER_SKILL} objects")
            return (not errors), errors
        except Exception:
            return False, ["manifest validation failed"]

    # ── install / remove (§20) ──────────────────────────────────────────

    def install(self, manifest: Any,
                trusted_by_human: bool = False) -> Tuple[bool, str]:
        """Install a skill.  ``trusted_by_human`` is REQUIRED for any
        skill whose risk_level is high/destructive (§20 trust)."""
        ok, errs = self.validate_manifest(manifest)
        if not ok:
            return False, errs[0] if errs else "invalid manifest"
        m = manifest if isinstance(manifest, dict) else {}
        name = m["name"]
        if name in self._installed:
            return False, "already installed (use update)"
        risk = str(m.get("risk_level", "low"))
        if risk in ("high", "destructive") and not trusted_by_human:
            return False, f"risk '{risk}' requires explicit human trust"
        steps = m.get("steps")
        skill = None
        if steps:
            skill = self.library.create_skill(
                name, steps, description=str(m.get("description", ""))[:200])
            if skill is None:
                return False, "library rejected the skill steps"
        self._installed[name] = InstalledSkill(manifest=dict(m),
                                               skill_id=skill.skill_id
                                               if skill else "")
        return True, f"installed {name}@{m['version']}"

    def remove(self, name: str) -> bool:
        """Uninstall honouring the declared uninstall behavior (§20)."""
        inst = self._installed.get(name)
        if inst is None:
            return False
        if inst.skill_id:
            self.library.revoke(inst.skill_id)
        del self._installed[name]
        return True

    # ── enable / disable (§20) ──────────────────────────────────────────

    def set_enabled(self, name: str, enabled: bool) -> bool:
        inst = self._installed.get(name)
        if inst is None:
            return False
        inst.enabled = bool(enabled)
        if inst.skill_id:
            self.library.set_enabled(inst.skill_id, enabled)
        return True

    # ── update / rollback (§20) ─────────────────────────────────────────

    def update(self, manifest: Any) -> Tuple[bool, str]:
        ok, errs = self.validate_manifest(manifest)
        if not ok:
            return False, errs[0] if errs else "invalid manifest"
        m = manifest if isinstance(manifest, dict) else {}
        name = m["name"]
        inst = self._installed.get(name)
        if inst is None:
            return False, "not installed"
        old_v = str(inst.manifest.get("version", "0.0.0"))
        new_v = str(m.get("version", ""))
        if not self._is_newer(new_v, old_v):
            return False, f"{new_v} does not supersede {old_v}"
        risk = str(m.get("risk_level", "low"))
        if risk in ("high", "destructive") and \
                str(inst.manifest.get("risk_level")) not in \
                ("high", "destructive"):
            return False, "risk escalation requires reinstall with trust"
        steps = m.get("steps")
        new_skill_id = inst.skill_id
        if steps and inst.skill_id:
            edited = self.library.edit(inst.skill_id, {"steps": steps})
            if edited is None:
                return False, "library rejected the updated steps"
        inst.previous_version = old_v
        inst.previous_manifest = dict(inst.manifest)
        inst.manifest = dict(m)
        return True, f"updated {name} {old_v} -> {new_v}"

    def rollback(self, name: str) -> Tuple[bool, str]:
        inst = self._installed.get(name)
        if inst is None or not inst.previous_manifest:
            return False, "nothing to roll back"
        prev = inst.previous_manifest
        steps = prev.get("steps")
        if steps and inst.skill_id:
            edited = self.library.edit(inst.skill_id, {"steps": steps})
            if edited is None:
                return False, "rollback rejected by library"
        inst.manifest = dict(prev)
        rolled = inst.previous_version
        inst.previous_version = str(inst.manifest.get("version", ""))
        inst.previous_manifest = {}
        return True, f"rolled back {name} -> {rolled}"

    # ── inspect (§20) ───────────────────────────────────────────────────

    def inspect(self, name: str) -> Optional[Dict[str, Any]]:
        inst = self._installed.get(name)
        if inst is None:
            return None
        d = inst.to_dict()
        d["manifest"] = {
            k: v for k, v in inst.manifest.items()
            if k in ("name", "version", "author", "capabilities",
                     "permissions", "dependencies", "required_modalities",
                     "supported_applications", "risk_level",
                     "installation", "uninstall_behavior",
                     "description")}
        return d

    def list_installed(self) -> List[Dict[str, Any]]:
        rows = [self.inspect(n) for n in self._installed]
        rows = [r for r in rows if r]
        rows.sort(key=lambda d: d["name"])
        return rows

    # ── internals ────────────────────────────────────────────────────────

    @staticmethod
    def _is_newer(new: str, old: str) -> bool:
        m1, m2 = _SEMVER_RE.match(new), _SEMVER_RE.match(old)
        if not m1 or not m2:
            return False
        t1 = tuple(int(x) for x in m1.groups())
        t2 = tuple(int(x) for x in m2.groups())
        return t1 > t2
