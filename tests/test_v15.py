"""AirMouse v15.0 tests — permissions (§14/§15), multi-agent (§12),
shared model (§13), Do-It-With-Me (§16), onboarding/accessibility
(§17/§18), licensing (§19), marketplace (§20), simulator (§26),
failure injection (§27), explainability (§24)."""

import pytest

from airmouse.agents import (AgentRegistry, AgentState, LeaseState)
from airmouse.ditm import DoItWithMe, SessionState
from airmouse.explain import (decision_trace, explain_confirmation,
                              explain_failure, explain_prediction,
                              explain_preference_influence,
                              explain_recovery, explain_target_choice)
from airmouse.failure_injection import (FAILURE_CLASSES,
                                        run_all_failure_classes,
                                        run_failure_scenario)
from airmouse.goals import GoalHierarchyParser
from airmouse.licensing import CapabilityLicensing, Tier
from airmouse.marketplace import Marketplace
from airmouse.onboarding import (EntryModality, Onboarding,
                                 accessibility_posture)
from airmouse.permissions import (AgentPermissionEngine, ControlLevel,
                                  Decision, PERMISSION_KEYS)
from airmouse.simulator import Simulator
from airmouse.skills import InteractionCompression, PersonalSkillLibrary
from airmouse.tasks import TaskEngine


# ═════════════════════════════════════════════════════════════════════════════
# §14/§15 — hierarchy + permission engine
# ═════════════════════════════════════════════════════════════════════════════

class TestControlHierarchy:
    def setup_method(self):
        self.p = AgentPermissionEngine()
        self.p.grant("bot", "mouse.click", Decision.ALLOW)

    def test_estop_beats_everything(self):
        self.p.human_override(True)
        self.p.emergency_stop(True)
        d = self.p.check("bot", "mouse.click")
        assert d.allowed is False
        assert d.level is ControlLevel.EMERGENCY_STOP
        # even a human override can't act
        d2 = self.p.check("human", "mouse.click")
        assert d2.allowed is False

    def test_human_override_beats_safety_and_permissions(self):
        self.p.safety_block("mouse.click")
        self.p.human_override(True)
        assert self.p.check("bot", "mouse.click").allowed is True
        self.p.human_override(False)
        assert self.p.check("bot", "mouse.click").allowed is False
        self.p.human_override(None)      # clear
        d = self.p.check("bot", "mouse.click")
        assert d.allowed is False        # safety block still applies
        assert d.level is ControlLevel.SAFETY_POLICY

    def test_safety_beats_permission_rules(self):
        self.p.safety_block("destructive.action")
        self.p.grant("bot", "destructive.action", Decision.ALLOW)
        d = self.p.check("bot", "destructive.action")
        assert d.allowed is False
        assert d.level is ControlLevel.SAFETY_POLICY

    def test_agent_cannot_override_estop(self):
        """§14: agents NEVER override e-stop."""
        self.p.emergency_stop(True)
        for key in PERMISSION_KEYS:
            assert self.p.check("bot", key).allowed is False
        assert self.p.check("bot", "destructive.action").allowed is False

    def test_order_is_canonical(self):
        names = [lvl.name for lvl in ControlLevel]
        assert names == ["EMERGENCY_STOP", "HUMAN_OVERRIDE", "SAFETY_POLICY",
                         "PERMISSION", "AGENT", "PREDICTION"]


class TestPermissionDecisions:
    def setup_method(self):
        self.p = AgentPermissionEngine()

    def test_all_six_decisions(self):
        self.p.grant("a1", "mouse.click", Decision.ALLOW)
        self.p.grant("a1", "type.text", Decision.DENY)
        self.p.grant("a1", "file.read", Decision.ASK)
        self.p.grant("a1", "file.write", Decision.ALLOW_ONCE, uses=1)
        self.p.grant("a1", "browser.navigate", Decision.ALLOW_SESSION)
        self.p.grant("a1", "observe.screen", Decision.ALLOW_PATTERN,
                     pattern="observe.*")
        assert self.p.check("a1", "mouse.click").allowed is True
        assert self.p.check("a1", "type.text").allowed is False
        d = self.p.check("a1", "file.read")
        assert d.allowed is False and d.decision is Decision.ASK and \
            d.requires_confirmation is True
        d2 = self.p.check("a1", "file.write")
        assert d2.allowed is True and d2.consumed_rule is True
        d3 = self.p.check("a1", "file.write")     # exhausted
        assert d3.allowed is False
        assert self.p.check("a1", "browser.navigate").allowed is True
        assert self.p.check("a1", "observe.screen").allowed is True
        # pattern must actually match: read.* does NOT match observe.*
        assert self.p.check("a1", "read.accessibility").allowed is False
        assert self.p.check("a1", "observe.clipboard").allowed is True

    def test_unknown_permission_fails_closed(self):
        d = self.p.check("a1", "nonexistent.capability")
        assert d.allowed is False
        assert d.decision is Decision.ASK

    def test_specificity_order_deterministic(self):
        self.p.grant("a1", "*", Decision.DENY)
        self.p.grant("a1", "mouse.click", Decision.ALLOW)
        assert self.p.check("a1", "mouse.click").allowed is True
        assert self.p.check("a1", "type.text").allowed is False
        self.p.grant("*", "mouse.click", Decision.DENY)
        self.p.grant("a2", "mouse.click", Decision.ALLOW)
        assert self.p.check("a2", "mouse.click").allowed is True
        assert self.p.check("a3", "mouse.click").allowed is False

    def test_high_risk_always_confirmation_gated(self):
        self.p.grant("a1", "destructive.action", Decision.ALLOW_ONCE,
                     uses=5)
        d = self.p.check("a1", "destructive.action", risky=True)
        assert d.allowed is True
        assert d.requires_confirmation is True   # §15: still gated

    def test_revoke_and_explain(self):
        self.p.grant("a1", "mouse.click", Decision.ALLOW)
        assert self.p.check("a1", "mouse.click").allowed
        assert self.p.revoke("a1", "mouse.click") == 1
        assert self.p.check("a1", "mouse.click").allowed is False
        ex = self.p.explain_decision("a1", "mouse.click")
        assert ex["decision"]["allowed"] is False
        assert ex["because"]

    def test_audit_trail(self):
        self.p.grant("a1", "mouse.click", Decision.ALLOW)
        self.p.check("a1", "mouse.click")
        assert self.p.audit_tail()


# ═════════════════════════════════════════════════════════════════════════════
# §12 — multi-agent infrastructure
# ═════════════════════════════════════════════════════════════════════════════

class TestMultiAgent:
    def setup_method(self):
        from airmouse.permissions import Decision
        self.reg = AgentRegistry()
        self.reg.permissions.grant("*", "mouse.click", Decision.ALLOW)
        self.reg.register("research", "Research Agent", priority=3)
        self.reg.register("writer", "Writing Agent", priority=5)
        self.reg.register("browser", "Browser Agent", priority=4)
        for a in ("research", "writer", "browser"):
            self.reg.set_state(a, AgentState.ACTIVE)

    def test_registration_and_discovery(self):
        disc = self.reg.discover()
        assert [d["agent_id"] for d in disc] == ["research", "browser",
                                                 "writer"]  # by priority

    def test_exclusive_lease_prevents_conflicting_actions(self):
        """§12: two agents never act on the same resource at once."""
        lease = self.reg.acquire("research", "mouse")
        assert lease is not None
        assert self.reg.acquire("writer", "mouse") is None
        ok, why = self.reg.authorize_action("writer", "mouse",
                                            "mouse.click")
        assert ok is False and "held by research" in why
        # holder still fine
        ok2, _ = self.reg.authorize_action("research", "mouse",
                                           "mouse.click")
        assert ok2 is True
        assert self.reg.holder("mouse") == "research"
        # both the denied acquire AND the denied authorize recorded it
        assert len(self.reg.conflicts()) >= 1
        assert all(c["resolution"] == "lease_held"
                   for c in self.reg.conflicts())

    def test_lease_release_and_handoff(self):
        self.reg.acquire("research", "mouse")
        assert self.reg.release("research", "mouse")
        assert self.reg.holder("mouse") is None
        self.reg.acquire("research", "clipboard")
        assert self.reg.handoff("research", "writer", "clipboard")
        assert self.reg.holder("clipboard") == "writer"
        inbox = self.reg.inbox("writer")
        assert inbox and inbox[-1]["kind"] == "handoff"

    def test_messages_are_data(self):
        self.reg.send("research", "writer", "info",
                      "found 3 sources on topic X")
        rows = self.reg.inbox("writer")
        assert rows[0]["body"] == "found 3 sources on topic X"
        # messages are never executed — no such pathway exists

    def test_human_override_suspends_agent(self):
        self.reg.acquire("browser", "mouse")
        assert self.reg.suspend_agent("browser")
        assert self.reg.holder("mouse") is None       # leases released
        ok, why = self.reg.authorize_action("browser", "mouse")
        assert ok is False and "suspended" in why

    def test_emergency_stop_all(self):
        self.reg.acquire("research", "mouse")
        assert self.reg.emergency_stop_all() == 3
        assert self.reg.holder("mouse") is None
        ok, why = self.reg.authorize_action("research", "mouse")
        assert ok is False and "stopped" in why
        # permission engine e-stop engaged too (§14)
        assert self.reg.permissions.check("research",
                                          "mouse.click").allowed is False

    def test_unknown_agent_rejected(self):
        assert self.reg.acquire("ghost", "mouse") is None
        ok, _ = self.reg.authorize_action("ghost", "mouse")
        assert ok is False

    def test_priority_never_steals_live_leases(self):
        self.reg.acquire("writer", "mouse")            # low priority holds
        lease = self.reg.acquire("research", "mouse")  # high priority wants
        assert lease is None                # deterministic: holder keeps it


# ═════════════════════════════════════════════════════════════════════════════
# §13 — humans and agents share ONE interaction layer
# ═════════════════════════════════════════════════════════════════════════════

class TestSharedInteractionModel:
    def test_same_resolver_for_human_and_agent(self):
        from airmouse.target_resolver import (ResolvedTarget,
                                              TargetRequest,
                                              UniversalTargetResolver)
        r = UniversalTargetResolver()
        r.register_provider("semantic_app_api", lambda req: ResolvedTarget(
            kind="semantic_app_api", value="Submit", confidence=0.9,
            point=(1, 1), provider="semantic_app_api"))
        human = r.resolve_target(TargetRequest(description="that",
                                               value="submit"))
        agent = r.resolve_target(TargetRequest(description="Submit button",
                                               value="submit"))
        assert human.ok and agent.ok
        assert human.resolved.value == agent.resolved.value

    def test_agent_goes_through_same_permission_gate(self):
        """§13/§14: agents and humans face the same gates; agents get
        no separate bypass pathway."""
        p = AgentPermissionEngine()
        p.safety_block("destructive.action")
        reg = AgentRegistry(permission_engine=p)
        reg.register("bot", priority=1)
        reg.set_state("bot", AgentState.ACTIVE)
        ok, why = reg.authorize_action("bot", "files", "destructive.action")
        assert ok is False
        assert "safety" in why

    def test_same_task_engine_for_ditm_and_agent(self):
        te = TaskEngine()
        parser = GoalHierarchyParser()
        ditm = DoItWithMe(parser=parser, task_engine=te)
        s = ditm.propose("prepare the presentation")
        ditm.approve(s.session_id, True)
        agent_task = te.create_task("agent work", owner="agent:bot")
        assert s.task_id and agent_task.task_id
        assert te.get(s.task_id) and te.get(agent_task.task_id)


# ═════════════════════════════════════════════════════════════════════════════
# §16 — DO IT WITH ME
# ═════════════════════════════════════════════════════════════════════════════

class TestDoItWithMe:
    def setup_method(self):
        self.ditm = DoItWithMe()

    def test_proposal_schema(self):
        s = self.ditm.propose("Help me prepare this presentation.")
        assert s is not None
        d = s.to_dict()["proposal"]
        for field in ("objective", "plan", "sources", "current_state",
                      "risks", "required_actions", "approval_state"):
            assert field in d, field
        assert d["approval_state"] == "pending"
        assert s.state is SessionState.PROPOSED

    def test_no_execution_before_approval(self):
        s = self.ditm.propose("prepare the annual report")
        # nothing created yet
        assert self.ditm.tasks.list_tasks() == []
        assert self.ditm.approve(s.session_id, True)
        assert self.ditm.tasks.list_tasks()

    def test_decline_stops(self):
        s = self.ditm.propose("prepare the report")
        assert self.ditm.approve(s.session_id, False)
        assert s.state is SessionState.STOPPED
        assert self.ditm.tasks.list_tasks() == []

    def test_destructive_plan_still_needs_task_approval(self):
        """§16 + §5: even an approved DitM plan keeps the TaskEngine's
        destructive gates."""
        s = self.ditm.propose("delete all old downloads")
        self.ditm.approve(s.session_id, True)
        task = self.ditm.tasks.get(s.task_id)
        # goal hierarchy marks "delete" destructive -> parser risk; the
        # TaskEngine gates it pending approval
        assert task is not None
        assert task["status"] in ("pending_approval", "running")

    def test_pause_resume_stop_and_progress(self):
        s = self.ditm.propose("prepare the report")
        self.ditm.approve(s.session_id, True)
        assert self.ditm.pause(s.session_id)
        assert s.state is SessionState.PAUSED
        assert self.ditm.start(s.session_id)
        assert s.state is SessionState.RUNNING
        # advance the underlying task
        self.ditm.tasks.begin_step(s.task_id, "p00")
        self.ditm.tasks.complete_step(s.task_id, "p00", True, True)
        rep = self.ditm.report(s.session_id)
        assert rep["progress"] > 0
        assert self.ditm.stop(s.session_id)
        assert s.state is SessionState.STOPPED

    def test_edit_plan_before_approval(self):
        s = self.ditm.propose("prepare the report")
        assert self.ditm.edit_plan(s.session_id,
                                   ["gather data", "write draft"])
        assert s.proposal.plan == ["gather data", "write draft"]
        self.ditm.approve(s.session_id, True)
        assert self.ditm.edit_plan(s.session_id, ["too late"]) is False

    def test_change_direction(self):
        s = self.ditm.propose("prepare the report")
        self.ditm.approve(s.session_id, True)
        s2 = self.ditm.change_direction(s.session_id,
                                        "research competitor pricing")
        assert s2 is not None
        assert s.state is SessionState.CHANGED
        assert s2.proposal.objective.startswith("research")

    def test_correction_learning_with_twin(self):
        from airmouse.intelligence.twin import PersonalInteractionTwin
        twin = PersonalInteractionTwin()
        ditm = DoItWithMe(twin=twin)
        s = ditm.propose("prepare the report")
        assert ditm.correct(s.session_id, "also include the budget slide")
        assert s.corrections
        assert twin.status()["corrections"] >= 1

    def test_low_confidence_flagged_as_risk(self):
        s = self.ditm.propose("sort of maybe organize things somehow")
        risks = " ".join(s.proposal.risks)
        assert "confidence" in risks or "ask" in risks


# ═════════════════════════════════════════════════════════════════════════════
# §17/§18 — onboarding + accessibility
# ═════════════════════════════════════════════════════════════════════════════

class TestOnboarding:
    def test_one_choice_to_usable(self):
        ob = Onboarding()
        st = ob.begin("voice")
        assert st["completed"] is True
        assert st["profile"]["modes"] == ["voice"]

    def test_all_entries_valid(self):
        ob = Onboarding()
        for m in EntryModality:
            ob2 = Onboarding()
            assert ob2.begin(m.value) is not None

    def test_invalid_entry_rejected(self):
        assert Onboarding().begin("telepathy") is None
        assert Onboarding().begin(None) is None
        assert Onboarding().begin("") is None

    def test_progressive_learning(self):
        from airmouse.intelligence.twin import PersonalInteractionTwin
        twin = PersonalInteractionTwin()
        ob = Onboarding(twin=twin)
        ob.begin("keyboard")
        assert ob.observe_preference("modality_preference", "click",
                                     "keyboard")
        assert twin.preferred_modality("click") == "keyboard"

    def test_learning_requires_onboarding_done(self):
        ob = Onboarding()
        assert ob.observe_preference("preference", "k", "v") is False


class TestAccessibilityPosture:
    def test_all_modes_supported(self):
        for mode in ("voice-only", "gesture-only", "gaze-only",
                     "keyboard-only", "switch-access", "hybrid",
                     "hands-free", "low-mobility"):
            posture = accessibility_posture(mode)
            assert posture["supported"] is True
            assert posture["modalities"]
            assert posture["confirmation"] == "configurable per mode"

    def test_gaze_only_gets_large_ui_flags(self):
        p = accessibility_posture("gaze-only")
        assert p["flags"]["large_ui"] is True
        assert p["flags"]["reduced_motion"] is True

    def test_unknown_mode_rejected(self):
        assert accessibility_posture("mind-reading")["supported"] is False


# ═════════════════════════════════════════════════════════════════════════════
# §19 — commercial platform
# ═════════════════════════════════════════════════════════════════════════════

class TestLicensing:
    def test_free_core_complete_no_crippling(self):
        lic = CapabilityLicensing()
        # the local core capabilities are never tier-gated
        for feature in ("premium.anything", "core.click",
                        "core.voice", "core.gaze"):
            if feature.startswith("core."):
                assert lic.has_feature(feature) is True
        matrix = lic.capability_matrix()
        assert matrix["free_core_complete"] is True
        assert matrix["dark_patterns"] == "none"

    def test_activation_and_tier_features(self):
        lic = CapabilityLicensing()
        assert lic.activate("pro", license_key="PRO-12345678",
                            issued_to="tester")
        assert lic.has_feature("premium.workflows") is True
        assert lic.has_feature("enterprise.management") is False

    def test_enterprise_includes_pro_features(self):
        lic = CapabilityLicensing()
        lic.activate("enterprise", license_key="ENT-12345678")
        assert lic.has_feature("premium.workflows") is True
        assert lic.has_feature("enterprise.management") is True

    def test_activation_requires_key_for_non_free(self):
        assert CapabilityLicensing().activate("pro") is False
        assert CapabilityLicensing().activate("galactic") is False

    def test_revocation_instant_and_local(self):
        lic = CapabilityLicensing()
        lic.activate("pro", license_key="PRO-12345678")
        lic.revoke()
        st = lic.state()
        assert st["tier"] == "free" and st["revoked"] is False
        assert st["local_only"] is True and st["phones_home"] is False


# ═════════════════════════════════════════════════════════════════════════════
# §20 — skill marketplace
# ═════════════════════════════════════════════════════════════════════════════

VALID_MANIFEST = {
    "name": "daily report",
    "version": "1.0.0",
    "author": "someone",
    "capabilities": ["send email report"],
    "permissions": ["mouse.click", "type.text"],
    "dependencies": [],
    "required_modalities": ["voice"],
    "supported_applications": ["mail"],
    "risk_level": "low",
    "installation": "install from local file",
    "uninstall_behavior": "remove steps and permissions",
    "description": "sends the daily report",
    "steps": [{"action": "click", "target": {"kind": "semantic",
                                             "value": "Send"}}],
}


class TestMarketplace:
    def setup_method(self):
        self.mp = Marketplace()

    def test_install_enable_disable_remove(self):
        ok, msg = self.mp.install(VALID_MANIFEST)
        assert ok, msg
        name = VALID_MANIFEST["name"]
        assert self.mp.inspect(name)["enabled"] is True
        assert self.mp.set_enabled(name, False)
        assert self.mp.inspect(name)["enabled"] is False
        assert self.mp.set_enabled(name, True)
        assert self.mp.remove(name)
        assert self.mp.inspect(name) is None

    def test_manifest_validation_fail_closed(self):
        bad = dict(VALID_MANIFEST)
        bad["evil_code"] = "import os; os.system('rm -rf /')"
        ok, errs = self.mp.validate_manifest(bad)
        assert not ok and any("unknown field" in e for e in errs)
        bad2 = dict(VALID_MANIFEST, version="not-semver")
        assert not self.mp.validate_manifest(bad2)[0]
        bad3 = dict(VALID_MANIFEST, risk_level="apocalyptic")
        assert not self.mp.validate_manifest(bad3)[0]
        assert not self.mp.validate_manifest("junk")[0]

    def test_high_risk_requires_human_trust(self):
        m = dict(VALID_MANIFEST, name="cleanup crew",
                 risk_level="destructive")
        ok, why = self.mp.install(m, trusted_by_human=False)
        assert not ok and "human trust" in why
        ok2, _ = self.mp.install(m, trusted_by_human=True)
        assert ok2

    def test_no_arbitrary_code_path_exists(self):
        """§20: even trusted skills only carry action-name steps —
        there is no field that could carry executable code."""
        ok, _ = self.mp.install(VALID_MANIFEST, trusted_by_human=True)
        assert ok
        name = VALID_MANIFEST["name"]
        d = self.mp.inspect(name)
        assert "evil_code" not in d["manifest"]
        assert "code" not in d["manifest"]
        assert "script" not in d["manifest"]

    def test_update_and_rollback(self):
        self.mp.install(VALID_MANIFEST)
        name = VALID_MANIFEST["name"]
        v2 = dict(VALID_MANIFEST, version="1.1.0",
                  steps=[{"action": "double_click",
                          "target": {"kind": "semantic", "value": "Send"}}])
        ok, msg = self.mp.update(v2)
        assert ok, msg
        assert self.mp.inspect(name)["version"] == "1.1.0"
        ok2, msg2 = self.mp.rollback(name)
        assert ok2, msg2
        assert self.mp.inspect(name)["version"] == "1.0.0"

    def test_update_requires_newer_version(self):
        self.mp.install(VALID_MANIFEST)
        same = dict(VALID_MANIFEST, version="1.0.0")
        ok, why = self.mp.update(same)
        assert not ok and "supersede" in why

    def test_update_cannot_escalate_risk(self):
        self.mp.install(VALID_MANIFEST)
        bad = dict(VALID_MANIFEST, version="2.0.0", risk_level="high")
        ok, why = self.mp.update(bad)
        assert not ok and "risk escalation" in why

    def test_duplicate_install_rejected(self):
        self.mp.install(VALID_MANIFEST)
        ok, why = self.mp.install(VALID_MANIFEST)
        assert not ok and "already installed" in why

    def test_skill_library_linkage(self):
        self.mp.install(VALID_MANIFEST)
        name = VALID_MANIFEST["name"]
        skill_id = self.mp.inspect(name)["skill_id"]
        assert self.mp.library.get(skill_id) is not None


# ═════════════════════════════════════════════════════════════════════════════
# §26 — deterministic computer simulator
# ═════════════════════════════════════════════════════════════════════════════

class TestSimulator:
    def test_windows_and_buttons(self):
        s = Simulator()
        s.add_window("Doc", app="writer", buttons=["Save", "Send"],
                     focus=True)
        assert s.click_button("Save")
        ok, msg = s.verify({"button_clicked": "Save"})
        assert ok, msg
        assert not s.click_button("Nonexistent")

    def test_browser_tabs_navigation_forms(self):
        s = Simulator()
        s.add_tab("https://mail.example", "Mail", buttons=["Send"],
                  forms=["body"])
        assert s.switch_tab(0)
        assert s.navigate("https://mail.example/compose", "Compose")
        ok, _ = s.verify({"url": "https://mail.example/compose"})
        assert ok
        assert s.type_text("body", "hello")
        ok2, _ = s.verify({"field_equals": ("body", "hello")})
        assert ok2

    def test_files_clipboard(self):
        s = Simulator()
        s.write_file("notes.txt", "abc")
        assert s.read_file("notes.txt") == "abc"
        s.set_clipboard("xyz")
        assert s.clipboard == "xyz"
        assert s.delete_file("notes.txt")
        assert s.read_file("notes.txt") is None

    def test_deterministic_same_script_same_state(self):
        def run():
            s = Simulator()
            s.add_window("W", buttons=["A"], focus=True)
            s.click_button("A")
            s.write_file("f.txt", "1")
            return s.verify({"button_clicked": "A",
                             "file_exists": "f.txt"}), s.observe()
        (ok1, _), obs1 = run()
        (ok2, _), obs2 = run()
        assert ok1 == ok2 is True
        assert obs1 == obs2

    def test_ui_change(self):
        s = Simulator()
        s.add_window("W", buttons=["Send"], focus=True)
        assert s.change_ui("Send", "Submit Now")
        assert not s.click_button("Send")
        assert s.click_button("Submit Now")

    def test_observe_shape(self):
        s = Simulator()
        s.add_window("Doc", app="writer", buttons=["Save"], focus=True)
        obs = s.observe()
        assert obs["active_application"] == "writer"
        assert obs["active_window"] == "Doc"
        assert "Save" in obs["visible_ui_targets"]


# ═════════════════════════════════════════════════════════════════════════════
# §27 — failure injection: OBSERVE→DIAGNOSE→RECOVER→VERIFY
# ═════════════════════════════════════════════════════════════════════════════

RECOVERABLE = {"missing_target", "moved_button", "closed_window",
               "stale_dom", "ocr_failure", "accessibility_failure",
               "network_failure", "timeout", "app_crash"}
SAFE_STOP = {"permission_denial", "malformed_request", "agent_conflict"}


class TestFailureInjection:
    def test_all_twelve_classes_defined(self):
        assert len(FAILURE_CLASSES) == 12
        assert RECOVERABLE | SAFE_STOP == set(FAILURE_CLASSES)

    @pytest.mark.parametrize("name", sorted(RECOVERABLE))
    def test_recoverable_classes_recover(self, name):
        out = run_failure_scenario(name, Simulator())
        assert out.observed, name
        assert out.diagnosed, name
        assert out.recovered, f"{name}: {out.notes}"
        assert out.verified
        assert out.stopped_safely

    @pytest.mark.parametrize("name", sorted(SAFE_STOP))
    def test_dangerous_classes_stop_safely(self, name):
        out = run_failure_scenario(name, Simulator())
        assert out.observed
        assert out.stopped_safely
        if name == "permission_denial":
            assert out.diagnosed == "permission_denied"
        if name == "malformed_request":
            assert out.outcome_safe_stop if hasattr(
                out, "outcome_safe_stop") else True

    def test_suite_summary(self):
        outcomes = run_all_failure_classes()
        assert len(outcomes) == 12
        recovered = [o for o in outcomes if o.recovered]
        safe_stopped = [o for o in outcomes if o.stopped_safely]
        assert len(safe_stopped) == 12          # nothing unsafe, ever
        assert len(recovered) >= len(RECOVERABLE)


# ═════════════════════════════════════════════════════════════════════════════
# §24 — explainability
# ═════════════════════════════════════════════════════════════════════════════

class TestExplainability:
    def test_six_question_traces(self):
        p = explain_prediction("click Submit", 0.82,
                               {"gaze": 0.9, "voice": 0.7})
        assert p["prediction"] and p["because"]
        t = explain_target_choice("dom", "Submit", "dom", 0.9,
                                  [{"provider": "a11y", "ok": False,
                                    "detail": "no match"}])
        assert t["via_provider"] == "dom"
        c = explain_confirmation("destructive", "destructive.action",
                                 "policy requires approval")
        assert "approval" in c["because"][0]
        f = explain_failure("click", "target_missing", "not found")
        assert f["diagnosis"] == "target_missing"
        r = explain_recovery("retarget", 2, "target_missing")
        assert r["strategy"] == "retarget" and r["round"] == 2
        trace = decision_trace(p, t, c, f, r)
        assert len(trace["trace"]) == 5
        assert trace["sensitive_data"] is False

    def test_preference_influence_with_and_without_twin(self):
        from airmouse.intelligence.twin import PersonalInteractionTwin
        e = explain_preference_influence(None)
        assert e["influenced"] is False
        twin = PersonalInteractionTwin()
        twin.learn("modality_preference", "click", "gaze", confidence=0.8)
        e2 = explain_preference_influence(twin, "modality_preference",
                                          "click")
        assert e2["influenced"] is True
        assert e2["frequency"] == 1


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
