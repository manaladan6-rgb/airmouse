"""AirMouse v13.0 tests — Goal hierarchy (§4) + Task Engine (§5)."""

import pytest

from airmouse.goals import (GoalHierarchyParser, Objective, ObjectiveLevel,
                            PlannedStep, RiskLevel)
from airmouse.tasks import (MAX_STEPS_PER_TASK, RetryPolicy, StepStatus,
                            Task, TaskEngine, TaskStatus)


# ═════════════════════════════════════════════════════════════════════════════
# §4 — COMMAND → INTENT → TASK → GOAL
# ═════════════════════════════════════════════════════════════════════════════

class TestGoalClassification:
    def setup_method(self):
        self.p = GoalHierarchyParser()

    def test_command_open_chrome(self):
        o = self.p.parse("open Chrome")
        assert o.level is ObjectiveLevel.COMMAND
        assert o.confidence >= 0.8
        assert o.risk is RiskLevel.LOW
        assert "application.launch" in o.required_permissions

    def test_intent_want_to(self):
        o = self.p.parse("I want to research AI")
        assert o.level is ObjectiveLevel.INTENT
        assert o.confidence >= 0.7

    def test_task_research(self):
        o = self.p.parse("Research AI computer-use systems")
        assert o.level is ObjectiveLevel.TASK
        assert o.confidence >= 0.7

    def test_goal_determine_whether(self):
        o = self.p.parse("figure out whether AirMouse can compete")
        assert o.level is ObjectiveLevel.GOAL
        assert o.confidence >= 0.6

    def test_spec_example_levels(self):
        # the spec's §4 example quartet
        assert self.p.parse("open Chrome").level is ObjectiveLevel.COMMAND
        assert self.p.parse("I want to research AI").level is \
            ObjectiveLevel.INTENT
        assert self.p.parse(
            "Research AI computer-use systems").level is ObjectiveLevel.TASK
        assert self.p.parse(
            "Determine whether AirMouse can compete").level is \
            ObjectiveLevel.GOAL

    def test_every_interpretation_exposes_full_schema(self):
        o = self.p.parse("delete all the files in downloads",
                         context={"app": "files"})
        for attr in ("level", "name", "utterance", "confidence", "context",
                     "proposed_plan", "risk", "required_permissions",
                     "required_confirmations"):
            assert getattr(o, attr) is not None
        assert o.risk is RiskLevel.DESTRUCTIVE
        assert "destructive.action" in o.required_permissions
        assert "confirm_destructive" in o.required_confirmations

    def test_sensitive_objective_needs_confirmation(self):
        o = self.p.parse("buy the subscription plan")
        assert o.risk is RiskLevel.HIGH
        assert "confirm_sensitive" in o.required_confirmations

    def test_execution_never_allowed(self):
        for utt in ("open chrome", "delete everything", "buy now",
                    "research AI", "figure out the market"):
            o = self.p.parse(utt)
            assert o.execution_allowed is False
            d = o.to_dict()
            assert d["execution_allowed"] is False

    def test_empty_and_garbage_inputs(self):
        o = self.p.parse("")
        assert o.confidence == 0.0
        o2 = self.p.parse(None)
        assert o2.utterance == ""
        o3 = self.p.parse("x" * 5000)
        assert len(o3.utterance) <= 300

    def test_plan_bounded(self):
        o = self.p.parse("prepare the annual report draft")
        assert 1 <= len(o.proposed_plan) <= 12
        assert all(isinstance(s, PlannedStep) for s in o.proposed_plan)


class TestGoalDecomposition:
    def setup_method(self):
        self.p = GoalHierarchyParser()

    def test_goal_decomposes_into_tasks(self):
        o = self.p.parse("Determine whether AirMouse can compete")
        links = self.p.decompose(o)
        assert len(links) >= 2
        assert all(l.parent_level == "goal" and l.child_level == "task"
                   for l in links)

    def test_task_decomposes_into_intents(self):
        o = self.p.parse("Research AI computer-use systems")
        links = self.p.decompose(o)
        assert links
        assert all(l.parent_level == "task" and l.child_level == "intent"
                   for l in links)

    def test_intent_decomposes_into_commands(self):
        o = self.p.parse("I want to research AI")
        links = self.p.decompose(o)
        assert links
        assert all(l.child_level == "command" for l in links)

    def test_hierarchy_view_prediction_only(self):
        h = self.p.hierarchy_of("prepare the presentation")
        assert h["prediction_only"] is True
        assert "objective" in h and "children" in h


class TestInterpreterAdapter:
    def test_adapter_upgrades_low_confidence(self):
        def interp(utt, ctx):
            return {"level": "task", "confidence": 0.7,
                    "name": "unusual phrasing task", "risk": "low"}
        p = GoalHierarchyParser(interpreter=interp)
        o = p.parse("sort of maybe organize things a little bit there")
        assert o.parsed_by == "intelligence_adapter"
        assert o.level is ObjectiveLevel.TASK
        assert o.execution_allowed is False

    def test_adapter_cannot_enable_execution(self):
        def evil(utt, ctx):
            return {"level": "command", "confidence": 0.99,
                    "execution_allowed": True, "risk": "none",
                    "name": "do it"}
        p = GoalHierarchyParser(interpreter=evil)
        o = p.parse("delete the system folder now")
        assert o.execution_allowed is False
        assert o.risk is RiskLevel.DESTRUCTIVE   # deterministic risk wins

    def test_adapter_garbage_ignored(self):
        def bad(utt, ctx):
            raise RuntimeError("boom")
        p = GoalHierarchyParser(interpreter=bad)
        o = p.parse("open chrome")
        assert o.parsed_by == "deterministic"

    def test_adapter_invalid_level_ignored(self):
        def odd(utt, ctx):
            return {"level": "galaxy", "confidence": 0.9}
        p = GoalHierarchyParser(interpreter=odd)
        o = p.parse("help me write a draft")
        assert o.parsed_by == "deterministic"


# ═════════════════════════════════════════════════════════════════════════════
# §5 — Task Engine
# ═════════════════════════════════════════════════════════════════════════════

def _simple_steps():
    return [
        {"step_id": "s1", "objective": "open editor", "action": "open_app",
         "target": "editor", "expected_result": "editor focused",
         "verification": "active_window == editor",
         "retry_policy": {"max_attempts": 2}},
        {"step_id": "s2", "objective": "type draft", "action": "type_text",
         "target": "editor", "expected_result": "draft typed",
         "verification": "text_field contains draft",
         "retry_policy": {"max_attempts": 2}},
    ]


class TestTaskLifecycle:
    def setup_method(self):
        self.e = TaskEngine()

    def test_create_with_steps(self):
        t = self.e.create_task("write the draft", _simple_steps())
        assert t is not None
        assert t.status is TaskStatus.DRAFT
        assert len(t.steps) == 2

    def test_start_pause_resume_cancel(self):
        t = self.e.create_task("write the draft", _simple_steps())
        assert self.e.start(t.task_id)
        assert t.status is TaskStatus.RUNNING
        assert self.e.pause(t.task_id)
        assert t.status is TaskStatus.PAUSED
        assert self.e.resume(t.task_id)
        assert t.status is TaskStatus.RUNNING
        assert self.e.cancel(t.task_id, "user stop")
        assert t.status is TaskStatus.CANCELLED

    def test_start_blocked_without_approval_for_destructive(self):
        t = self.e.create_task("clean downloads", [
            {"step_id": "d1", "objective": "delete files",
             "action": "delete", "risk": "destructive"}])
        assert t.status is TaskStatus.PENDING_APPROVAL
        assert self.e.start(t.task_id) is False     # hard gate
        assert self.e.approve(t.task_id, "human")
        assert self.e.start(t.task_id)

    def test_destructive_step_needs_task_approval_even_running(self):
        # scenario A: destructive step added while DRAFT -> task gated
        t = self.e.create_task("mixed task", _simple_steps())
        self.e.add_step(t.task_id, {"step_id": "s3", "objective": "purge",
                                    "action": "delete",
                                    "risk": "destructive"})
        assert t.status is TaskStatus.PENDING_APPROVAL
        assert self.e.begin_step(t.task_id, "s3") is False
        assert t.steps[2].status is StepStatus.AWAITING_APPROVAL
        # scenario B: after approval the step may begin
        assert self.e.approve(t.task_id, "human")
        assert self.e.start(t.task_id)
        assert self.e.begin_step(t.task_id, "s3")

    def test_new_destructive_step_in_running_task_gated_at_begin(self):
        t = self.e.create_task("base task", _simple_steps())
        self.e.approve(t.task_id, "human")
        self.e.start(t.task_id)
        # add destructive step while RUNNING (add_step only gates DRAFT)
        self.e.add_step(t.task_id, {"step_id": "s9", "objective": "wipe",
                                    "action": "delete",
                                    "risk": "destructive"})
        assert t.status is TaskStatus.RUNNING
        assert self.e.begin_step(t.task_id, "s9") is False
        assert t.steps[2].status is StepStatus.AWAITING_APPROVAL
        assert self.e.approve(t.task_id, "human")
        assert self.e.begin_step(t.task_id, "s9")

    def test_progress_and_completion(self):
        t = self.e.create_task("write the draft", _simple_steps())
        self.e.start(t.task_id)
        self.e.begin_step(t.task_id, "s1")
        self.e.complete_step(t.task_id, "s1", success=True, verified=True)
        assert self.e.progress(t.task_id) == 0.5
        self.e.begin_step(t.task_id, "s2")
        self.e.complete_step(t.task_id, "s2", success=True, verified=True)
        assert t.status is TaskStatus.COMPLETED
        assert self.e.progress(t.task_id) == 1.0

    def test_unverified_success_enters_verifying(self):
        t = self.e.create_task("write the draft", _simple_steps())
        self.e.start(t.task_id)
        self.e.begin_step(t.task_id, "s1")
        st = self.e.complete_step(t.task_id, "s1", success=True,
                                  verified=False)
        assert st is TaskStatus.VERIFYING
        assert self.e.record_verification(t.task_id, "s1", True)
        assert t.steps[0].status is StepStatus.SUCCEEDED


class TestTaskDependencies:
    def setup_method(self):
        self.e = TaskEngine()

    def test_dependency_ordering(self):
        t = self.e.create_task("ordered work", _simple_steps())
        assert self.e.add_dependency(t.task_id, "s2", "s1")
        self.e.start(t.task_id)
        ready = [s.step_id for s in self.e.ready_steps(t.task_id)]
        assert ready == ["s1"]

    def test_dependency_blocks_until_success(self):
        t = self.e.create_task("ordered work", _simple_steps())
        self.e.add_dependency(t.task_id, "s2", "s1")
        self.e.start(t.task_id)
        self.e.begin_step(t.task_id, "s1")
        self.e.complete_step(t.task_id, "s1", success=False, error="nope")
        # s1 retry-eligible again; s2 still blocked by unsatisfied dep
        assert [s.step_id for s in self.e.ready_steps(t.task_id)] == ["s1"]
        self.e.begin_step(t.task_id, "s1")
        self.e.complete_step(t.task_id, "s1", success=True, verified=True)
        assert [s.step_id for s in self.e.ready_steps(t.task_id)] == ["s2"]

    def test_invalid_dependencies_rejected(self):
        t = self.e.create_task("x", _simple_steps())
        assert not self.e.add_dependency(t.task_id, "s1", "nope")
        assert not self.e.add_dependency(t.task_id, "s1", "s1")
        assert not self.e.add_dependency("task-9999", "s1", "s2")

    def test_retry_policy_bounded(self):
        rp = RetryPolicy(max_attempts=99, backoff_seconds=999)
        s = rp.sanitized()
        assert s.max_attempts == 3        # MAX_RETRIES
        assert s.backoff_seconds == 60.0


class TestTaskFailureRecovery:
    def setup_method(self):
        self.e = TaskEngine()

    def test_retry_until_max_then_fail(self):
        t = self.e.create_task("flaky work", [
            {"step_id": "f1", "objective": "flaky", "action": "click",
             "retry_policy": {"max_attempts": 3}}])
        self.e.start(t.task_id)
        for i in range(3):
            assert self.e.begin_step(t.task_id, "f1")
            self.e.complete_step(t.task_id, "f1", success=False,
                                 error=f"attempt {i}")
        assert t.steps[0].status is StepStatus.FAILED
        assert t.status is TaskStatus.FAILED

    def test_failure_with_human_recovery_blocks(self):
        t = self.e.create_task("needs human", [
            {"step_id": "h1", "objective": "hard", "action": "click",
             "retry_policy": {"max_attempts": 1,
                              "recover_with_human": True}}])
        self.e.start(t.task_id)
        self.e.begin_step(t.task_id, "h1")
        self.e.complete_step(t.task_id, "h1", success=False, error="stuck")
        assert t.status is TaskStatus.BLOCKED

    def test_failed_step_cannot_retry_past_policy(self):
        t = self.e.create_task("one shot", [
            {"step_id": "o1", "objective": "one", "action": "click",
             "retry_policy": {"max_attempts": 1}}])
        self.e.start(t.task_id)
        self.e.begin_step(t.task_id, "o1")
        self.e.complete_step(t.task_id, "o1", success=False)
        assert self.e.retry_step(t.task_id, "o1") is False


class TestTaskCheckpoints:
    def setup_method(self):
        self.e = TaskEngine()

    def test_checkpoint_and_rollback_state(self):
        t = self.e.create_task("write the draft", _simple_steps())
        self.e.start(t.task_id)
        self.e.begin_step(t.task_id, "s1")
        self.e.complete_step(t.task_id, "s1", success=True, verified=True)
        cid = self.e.checkpoint(t.task_id, "after s1")
        assert cid is not None
        self.e.begin_step(t.task_id, "s2")
        self.e.complete_step(t.task_id, "s2", success=False, error="bad")
        assert t.steps[1].status is StepStatus.READY  # retrying
        # rollback to checkpoint state
        assert self.e.rollback(t.task_id, cid)
        assert t.steps[1].status is StepStatus.PENDING
        assert self.e.progress(t.task_id) == 0.5

    def test_rollback_honest_about_side_effects(self):
        t = self.e.create_task("write the draft", _simple_steps())
        self.e.start(t.task_id)
        cid = self.e.checkpoint(t.task_id)
        self.e.rollback(t.task_id, cid)
        assert any("side effects not undone" in a for a in t.audit)

    def test_unknown_checkpoint_rejected(self):
        t = self.e.create_task("x", _simple_steps())
        assert self.e.rollback(t.task_id, "cp-999") is False

    def test_checkpoint_ring_bounded(self):
        t = self.e.create_task("x", _simple_steps())
        for i in range(30):
            self.e.checkpoint(t.task_id, f"cp{i}")
        assert len(t.checkpoints) == 20


class TestTaskBoundsAndSafety:
    def test_max_steps_bounded(self):
        e = TaskEngine()
        t = e.create_task("big", None)
        for i in range(MAX_STEPS_PER_TASK + 10):
            e.add_step(t.task_id, {"step_id": f"s{i}",
                                   "objective": f"o{i}", "action": "none"})
        assert len(t.steps) == MAX_STEPS_PER_TASK

    def test_garbage_inputs_fail_closed(self):
        e = TaskEngine()
        assert e.create_task("") is None
        assert e.create_task(None) is None
        assert e.create_task("ok", "not-a-list") is not None  # no crash
        assert e.get("task-9999") is None
        assert e.progress("task-9999") == 0.0
        assert e.audit("task-9999") == []
        assert e.dependency_graph("task-9999") == {}

    def test_approval_gates_full_schema(self):
        e = TaskEngine()
        t = e.create_task("destructive cleanup", [
            {"step_id": "d1", "objective": "wipe temp", "action": "delete",
             "risk": "destructive", "permission": "destructive.action",
             "preconditions": ("temp visible",),
             "expected_result": "temp empty",
             "verification": "file count == 0",
             "timeout": 5.0,
             "retry_policy": {"max_attempts": 2,
                              "recover_with_human": True}}])
        d = t.to_dict()
        step = d["steps"][0]
        for field in ("objective", "action", "target", "preconditions",
                      "expected_result", "verification", "risk",
                      "permission", "timeout", "retry_policy"):
            assert field in step, field
        assert d["status"] == "pending_approval"

    def test_task_eviction_bounded(self):
        e = TaskEngine(max_tasks=8)
        for i in range(20):
            e.create_task(f"task {i}")
        assert len(e.list_tasks(limit=100)) <= 8


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
