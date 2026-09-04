"""AirMouse v13.5 tests — Interaction Compression + Skills (§6)."""

import pytest

from airmouse.skills import (InteractionCompression, PersonalSkillLibrary,
                             SequenceCluster, Skill, SkillStep, SkillTarget,
                             TargetKind, _clean_target, _signature_for)


def _seq(target_kind="semantic", action="click"):
    return [
        {"action": action, "target": {"kind": target_kind,
                                      "value": "Submit button"}},
        {"action": "type_text", "target": {"kind": "semantic",
                                           "value": "message field"}},
        {"action": "click", "target": {"kind": "semantic",
                                       "value": "Send button"}},
    ]


class TestInteractionCompression:
    def test_signature_ignores_coordinates(self):
        a = _signature_for([
            {"action": "click", "target": {"kind": "coordinate",
                                           "value": "x=100,y=200"}}])
        b = _signature_for([
            {"action": "click", "target": {"kind": "coordinate",
                                           "value": "x=900,y=999"}}])
        assert a == b               # §6: skills must not depend on coords

    def test_repetition_builds_cluster(self):
        c = InteractionCompression()
        for _ in range(3):
            cluster = c.observe_sequence(_seq(), confidence=0.8)
        assert cluster is not None
        assert cluster.occurrences == 3
        assert abs(cluster.avg_confidence - 0.8) < 0.01

    def test_candidate_requires_repetition_and_confidence(self):
        c = InteractionCompression(min_occurrences=3, min_confidence=0.6)
        c.observe_sequence(_seq(), confidence=0.9)
        c.observe_sequence(_seq(), confidence=0.9)
        assert c.candidates() == []          # repetition unmet
        for _ in range(3):
            c.observe_sequence(_seq(), confidence=0.2)
        assert c.candidates() == []          # avg confidence unmet
        for _ in range(3):
            c.observe_sequence(_seq(), confidence=0.9)
        assert len(c.candidates()) == 1

    def test_semantic_clustering_separates_different_templates(self):
        c = InteractionCompression()
        for _ in range(4):
            c.observe_sequence(_seq(), confidence=0.8)
        for _ in range(4):
            c.observe_sequence([
                {"action": "open_app", "target": {"kind": "semantic",
                                                  "value": "editor"}},
                {"action": "type_text", "target": {"kind": "semantic",
                                                   "value": "doc"}},
            ], confidence=0.8)
        cls = c.clusters()
        assert len(cls) == 2
        assert cls[0].occurrences == 4

    def test_proposal_requires_approval_and_is_not_silent(self):
        c = InteractionCompression()
        assert c.propose() is None
        for _ in range(3):
            c.observe_sequence(_seq(), confidence=0.85)
        p = c.propose()
        assert p is not None
        assert p["silent"] is False
        assert p["requires"] == "user_approval"
        assert p["preview_steps"]      # §6 preview

    def test_garbage_sequences_rejected(self):
        c = InteractionCompression()
        assert c.observe_sequence(None) is None
        assert c.observe_sequence([]) is None
        assert c.observe_sequence("click") is None
        assert c.observe_sequence([{"action": "click"}]) is None  # too short
        assert c.observe_sequence([{"action": "click"}] * 99) is None


class TestSkillCreation:
    def test_create_from_cluster(self):
        lib = PersonalSkillLibrary()
        c = InteractionCompression()
        for _ in range(3):
            cl = c.observe_sequence(_seq(), confidence=0.8)
        skill = lib.create_skill_from_cluster(
            cl, "send report", targets=[
                {"kind": "semantic", "value": "Submit button"},
                {"kind": "dom", "value": "#msg"},
                {"kind": "semantic", "value": "Send button"}],
            description="daily report send")
        assert skill is not None
        assert skill.name == "send report"
        assert len(skill.steps) == 3
        assert skill.steps[0].target.kind == "semantic"
        assert "mouse.click" in skill.required_permissions
        assert "type.text" in skill.required_permissions

    def test_coordinates_always_flagged_as_fallback(self):
        t = _clean_target({"kind": "coordinate", "value": "x=10,y=20"})
        assert t.kind == "coordinate"
        assert t.coordinate_fallback is True
        t2 = _clean_target({"kind": "semantic", "value": "ok"})
        assert t2.coordinate_fallback is False

    def test_invalid_names_rejected(self):
        lib = PersonalSkillLibrary()
        cl = SequenceCluster(signature="a:b", occurrences=5,
                             action_names=("click",),
                             avg_confidence=0.9)
        assert lib.create_skill_from_cluster(cl, "") is None
        assert lib.create_skill_from_cluster(cl, "UPPER!!") is None
        assert lib.create_skill_from_cluster(cl, "my password") is None
        assert lib.create_skill_from_cluster(cl, "!" * 500) is None

    def test_direct_creation_and_schema(self):
        lib = PersonalSkillLibrary()
        s = lib.create_skill("morning routine", [
            {"action": "open_app", "target": {"kind": "semantic",
                                              "value": "mail"},
             "expected_result": "mail open"},
            {"action": "click", "target": {"kind": "accessibility",
                                           "value": "Inbox"},
             "risk": "low"},
        ], description="open mail inbox")
        assert s is not None
        d = s.to_dict()
        for field in ("skill_id", "name", "version", "description", "steps",
                      "required_permissions", "risk", "enabled",
                      "confidence", "usage_count"):
            assert field in d


class TestSkillLifecycle:
    def setup_method(self):
        self.lib = PersonalSkillLibrary()
        self.skill = self.lib.create_skill("send report", [
            {"action": "click", "target": {"kind": "semantic",
                                           "value": "Send"}}])

    def test_edit_bumps_version(self):
        v0 = self.skill.version
        s = self.lib.edit(self.skill.skill_id, {
            "description": "updated",
            "steps": [{"action": "double_click",
                       "target": {"kind": "semantic", "value": "Send"}}]})
        assert s.version == v0 + 1
        assert s.steps[0].action == "double_click"
        assert "mouse.click" in s.required_permissions

    def test_enable_disable_revoke(self):
        sid = self.skill.skill_id
        assert self.lib.set_enabled(sid, False)
        assert self.lib.get(sid)["enabled"] is False
        assert self.lib.list_skills(include_disabled=False) == []
        assert self.lib.set_enabled(sid, True)
        assert self.lib.list_skills(include_disabled=False)
        assert self.lib.revoke(sid)
        assert self.lib.get(sid) is None

    def test_usage_recording(self):
        sid = self.skill.skill_id
        self.lib.record_use(sid, True)
        self.lib.record_use(sid, True)
        self.lib.record_use(sid, False)
        d = self.lib.get(sid)
        assert d["usage_count"] == 3 and d["success_count"] == 2

    def test_destructive_skill_flagged(self):
        s = self.lib.create_skill("cleanup", [
            {"action": "delete", "risk": "destructive"}])
        assert s.risk == "destructive"
        assert "destructive.action" in s.required_permissions


class TestSkillPersistence:
    def test_export_import_roundtrip(self):
        lib = PersonalSkillLibrary()
        lib.create_skill("send report", [
            {"action": "click", "target": {"kind": "semantic",
                                           "value": "Send"}}])
        data = lib.export()
        assert data["format"] == "airmouse-skills"
        lib2 = PersonalSkillLibrary()
        imp, rej = lib2.import_skills(data)
        assert imp == 1 and rej == 0
        assert lib2.find_by_name("send report") is not None

    def test_import_fail_closed(self):
        lib = PersonalSkillLibrary()
        assert lib.import_skills({"format": "wrong"}) == (0, 1)
        assert lib.import_skills({"format": "airmouse-skills",
                                  "version": 99}) == (0, 1)
        assert lib.import_skills("junk") == (0, 1)
        imp, rej = lib.import_skills({
            "format": "airmouse-skills", "version": 1,
            "skills": [
                {"name": "ok skill",
                 "steps": [{"action": "click"}]},
                {"name": "Bad Name!", "steps": [{"action": "click"}]},
                {"name": "x" * 999, "steps": []},
                "not-a-dict",
            ]})
        assert imp == 1 and rej == 3

    def test_capacity_bounded_eviction(self):
        lib = PersonalSkillLibrary(max_skills=8)
        for i in range(12):
            lib.create_skill(f"skill {i:02d}", [
                {"action": "click", "target": {"kind": "semantic",
                                               "value": f"b{i}"}}])
        assert len(lib.list_skills()) <= 8


class TestSkillSafetyProperties:
    def test_no_silent_automation_end_to_end(self):
        """§6 full pipeline: observe -> cluster -> propose -> approve ->
        create.  Skipping approval means NO skill exists."""
        comp = InteractionCompression()
        lib = PersonalSkillLibrary()
        for _ in range(3):
            cl = comp.observe_sequence(_seq(), confidence=0.9)
        proposal = comp.propose()
        assert proposal is not None
        # user REJECTS: no library call
        assert lib.list_skills() == []
        # user APPROVES: explicit create
        skill = lib.create_skill_from_cluster(cl, "send report")
        assert skill is not None
        assert skill.enabled is True

    def test_skill_steps_carry_targets_not_screenshots(self):
        lib = PersonalSkillLibrary()
        s = lib.create_skill("nav", [
            {"action": "navigate", "target": {"kind": "dom",
                                              "value": "a.login"}},
            {"action": "click", "target": {"kind": "ocr",
                                           "value": "Log in"}}])
        kinds = [st.target.kind for st in s.steps]
        assert all(k in ("semantic", "accessibility", "dom", "ocr", "visual",
                         "coordinate") for k in kinds)
        assert kinds == ["dom", "ocr"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
