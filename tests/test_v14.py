"""AirMouse v14.0 tests — Recovery Engine (§7) + Target Resolver (§8)."""

import pytest

from airmouse.recovery2 import (ActionAttempt, FailureKind, LoopContext,
                                MAX_ROUNDS, RecoveryEngine,
                                RecoveryStrategy2, RecoveryTrace)
from airmouse.target_resolver import (ResolvedTarget, ResolutionResult,
                                      TargetKind, TargetRequest,
                                      UniversalTargetResolver)


# ═════════════════════════════════════════════════════════════════════════════
# §7 — Recovery Engine
# ═════════════════════════════════════════════════════════════════════════════

def _ok_ctx(**kw):
    return LoopContext(action=kw.pop("action", "click"),
                       target=kw.pop("target", {"kind": "semantic",
                                                "value": "Submit"}),
                       **kw)


def _flaky_executor(fail_times, detail="not found: Submit"):
    state = {"n": 0}

    def exec_fn(ctx):
        state["n"] += 1
        if state["n"] <= fail_times:
            return False, detail
        return True, "clicked"
    return exec_fn


def _always_ok_verify(ctx, obs):
    return True, "none", "ok"


def _diag_from_detail(ctx, obs, executed, detail):
    from airmouse.recovery2 import _infer_failure
    return _infer_failure(detail, obs)


class TestRecoveryLoop:
    def test_first_try_success(self):
        e = RecoveryEngine()
        t = e.run(_ok_ctx(),
                  execute=lambda ctx: (True, "ok"),
                  verify=_always_ok_verify)
        assert t.outcome == "succeeded"
        assert t.rounds_used == 1

    def test_retry_then_success(self):
        e = RecoveryEngine()
        t = e.run(_ok_ctx(), execute=_flaky_executor(1),
                  verify=_always_ok_verify, diagnose=_diag_from_detail)
        assert t.outcome == "succeeded"
        assert t.rounds_used == 2
        assert t.attempts[0].diagnosis == FailureKind.TARGET_MISSING.value

    def test_preconditions_gate_execution(self):
        e = RecoveryEngine()
        calls = {"n": 0}

        def exec_fn(ctx):
            calls["n"] += 1
            return True, "ok"
        t = e.run(_ok_ctx(),
                  preconditions=lambda ctx: (False, "app not open"),
                  execute=exec_fn)
        assert t.outcome == "failed"
        assert calls["n"] == 0
        assert "preconditions unmet" in t.human_message

    def test_preconditions_pass_then_execute(self):
        e = RecoveryEngine()
        t = e.run(_ok_ctx(),
                  preconditions=lambda ctx: (True, "ready"),
                  execute=lambda ctx: (True, "ok"),
                  verify=_always_ok_verify)
        assert t.outcome == "succeeded"

    def test_safety_gate_blocks_every_round(self):
        executed = {"n": 0}

        def exec_fn(ctx):
            executed["n"] += 1
            return True, "ok"
        e = RecoveryEngine(
            safety_gate=lambda ctx: (False, "destructive needs confirm"))
        t = e.run(_ok_ctx(risky=True), execute=exec_fn)
        assert t.outcome == "blocked"
        assert executed["n"] == 0

    def test_round_cap_never_unbounded(self):
        e = RecoveryEngine(max_rounds=4)
        t = e.run(_ok_ctx(),
                  execute=lambda ctx: (False, "target moved"),
                  diagnose=_diag_from_detail)
        assert t.rounds_used <= 4 <= MAX_ROUNDS
        assert t.outcome in ("failed", "requires_human")

    def test_permission_denied_never_retries(self):
        """§7: permission failures go straight to human — never
        escalate privileges silently."""
        e = RecoveryEngine()
        executed = {"n": 0}

        def exec_fn(ctx):
            executed["n"] += 1
            return False, "permission denied"
        t = e.run(_ok_ctx(), execute=exec_fn, diagnose=_diag_from_detail)
        assert t.final_diagnosis == FailureKind.PERMISSION_DENIED.value
        assert t.outcome == "requires_human"
        assert executed["n"] == 1

    def test_malformed_request_fails_closed(self):
        e = RecoveryEngine()
        t = e.run(_ok_ctx(),
                  execute=lambda ctx: (False, "malformed request body"),
                  diagnose=_diag_from_detail)
        assert t.outcome == "gave_up"

    def test_agent_conflict_goes_to_human(self):
        e = RecoveryEngine()
        t = e.run(_ok_ctx(),
                  execute=lambda ctx: (False, "agent conflict on resource"),
                  diagnose=_diag_from_detail)
        assert t.outcome == "requires_human"

    def test_stale_dom_ladder_includes_alternate_execution(self):
        e = RecoveryEngine(max_rounds=6)
        rounds_executed = []

        def exec_fn(ctx):
            rounds_executed.append(ctx.execution_method)
            if len(rounds_executed) < 4:
                return False, "stale dom node"
            return True, "ok"
        t = e.run(_ok_ctx(), execute=exec_fn, verify=_always_ok_verify,
                  diagnose=_diag_from_detail)
        assert t.outcome == "succeeded"
        assert "keyboard_shortcut" in rounds_executed   # execution changed

    def test_reobserve_refreshes_target(self):
        seen_targets = []

        def exec_fn(ctx):
            seen_targets.append(dict(ctx.target))
            if len(seen_targets) < 2:
                return False, "target moved"
            return True, "ok"

        def reobserve(ctx):
            return {"target": {"kind": "semantic", "value": "New Submit"}}
        e = RecoveryEngine(reobserve=reobserve)
        t = e.run(_ok_ctx(), execute=exec_fn, verify=_always_ok_verify,
                  diagnose=_diag_from_detail)
        assert t.outcome == "succeeded"
        assert seen_targets[-1]["value"] == "New Submit"

    def test_retarget_swaps_target(self):
        def retarget(ctx):
            return {"kind": "ocr", "value": "OK button", "confidence": 0.8}
        seen = []

        def exec_fn(ctx):
            seen.append(ctx.target.get("value"))
            ok = ctx.target.get("value") == "OK button"
            return ok, ("clicked" if ok else "target not found: old")
        e = RecoveryEngine(retarget=retarget)
        t = e.run(_ok_ctx(target={"kind": "semantic", "value": "old"}),
                  execute=exec_fn, verify=_always_ok_verify,
                  diagnose=_diag_from_detail)
        assert "OK button" in seen
        assert t.outcome == "succeeded"

    def test_verify_failure_diagnosed_and_recovered(self):
        e = RecoveryEngine(max_rounds=5)

        def verify(ctx, obs):
            if ctx.round_index < 3:
                return False, "target_moved", "button shifted"
            return True, "none", "ok"
        t = e.run(_ok_ctx(), execute=lambda ctx: (True, "ok"),
                  verify=verify)
        assert t.outcome == "succeeded"
        assert t.attempts[0].diagnosis == FailureKind.TARGET_MOVED.value

    def test_alternate_semantic_target_requires_alternative(self):
        e = RecoveryEngine()
        t = e.run(_ok_ctx(target={"kind": "semantic", "value": "X"}),
                  execute=lambda ctx: (False, "ocr failed"),
                  diagnose=_diag_from_detail)
        # ladder: alternate_semantic (unavailable) -> safe stop
        assert t.outcome in ("failed", "requires_human")

    def test_alternate_semantic_target_used_when_present(self):
        def exec_fn(ctx):
            if ctx.target.get("value") != "OK (a11y)":
                return False, "accessibility failed"
            return True, "ok"
        e = RecoveryEngine()
        t = e.run(_ok_ctx(target={
            "kind": "accessibility", "value": "broken-node",
            "alternate": {"kind": "accessibility", "value": "OK (a11y)"}}),
            execute=exec_fn, verify=_always_ok_verify,
            diagnose=_diag_from_detail)
        assert t.outcome == "succeeded"

    def test_human_hook_called_with_message(self):
        msgs = []
        e = RecoveryEngine(human_hook=lambda ctx, msg: msgs.append(msg))
        t = e.run(_ok_ctx(),
                  execute=lambda ctx: (False, "permission denied"),
                  diagnose=_diag_from_detail)
        assert t.outcome == "requires_human"
        assert msgs and "human assistance" in msgs[0]

    def test_destructive_recovery_requires_confirmation(self):
        """§7: destructive recovery must be confirmation-gated."""
        confirmed = {"flag": False}

        def safety(ctx):
            if ctx.risky and not confirmed["flag"]:
                return False, "destructive requires confirmation"
            return True, "ok"
        e = RecoveryEngine(safety_gate=safety,
                           confirm_hook=lambda ctx, why: True)
        t = e.run(_ok_ctx(risky=True),
                  execute=lambda ctx: (True, "ok"),
                  verify=_always_ok_verify,
                  preconditions=lambda ctx: confirmed.update(flag=True)
                  or (True, "user confirmed"))
        assert t.outcome == "succeeded"

    def test_no_confirm_hook_means_no_destructive_execution(self):
        executed = {"n": 0}

        def exec_fn(ctx):
            executed["n"] += 1
            return True, "ok"
        e = RecoveryEngine(safety_gate=lambda ctx: (False, "risky"))
        t = e.run(_ok_ctx(risky=True), execute=exec_fn)
        assert executed["n"] == 0
        assert t.outcome == "blocked"


class TestRecoveryTrace:
    def test_trace_is_explainable(self):
        e = RecoveryEngine()
        t = e.run(_ok_ctx(), execute=_flaky_executor(2, "target moved"),
                  verify=_always_ok_verify, diagnose=_diag_from_detail)
        d = t.to_dict()
        for field in ("action", "outcome", "rounds_used", "final_diagnosis",
                      "attempts"):
            assert field in d
        assert all("strategy" in a and "diagnosis" in a
                   for a in d["attempts"])
        assert len(d["attempts"]) >= 3

    def test_trace_bounded(self):
        e = RecoveryEngine(max_rounds=6)
        t = e.run(_ok_ctx(),
                  execute=lambda ctx: (False, "timeout"),
                  diagnose=_diag_from_detail)
        assert len(t.attempts) <= 64


# ═════════════════════════════════════════════════════════════════════════════
# §8 — Universal Target Resolver
# ═════════════════════════════════════════════════════════════════════════════

def _provider(kind, conf=0.9, value="found"):
    def fn(request):
        return ResolvedTarget(kind=kind, value=value, confidence=conf,
                              point=(10, 20), provider=kind)
    return fn


class TestResolutionChain:
    def test_priority_order_accessibility_wins(self):
        r = UniversalTargetResolver()
        r.register_provider("accessibility", _provider("accessibility"))
        r.register_provider("ocr", _provider("ocr"))
        req = TargetRequest(description="submit button", value="submit")
        res = r.resolve_target(req)
        assert res.ok
        assert res.resolved.provider == "accessibility"
        assert res.chain_used[0] == "accessibility"

    def test_falls_through_to_next_provider(self):
        r = UniversalTargetResolver()
        r.register_provider("accessibility", lambda req: None)
        r.register_provider("dom", _provider("dom", 0.85))
        res = r.resolve_target(TargetRequest(description="login", value="l"))
        assert res.ok and res.resolved.provider == "dom"
        assert any(a.provider == "accessibility" and not a.ok
                   for a in res.attempts)

    def test_provider_crash_does_not_break_chain(self):
        def boom(req):
            raise RuntimeError("a11y tree exploded")
        r = UniversalTargetResolver()
        r.register_provider("accessibility", boom)
        r.register_provider("dom", _provider("dom"))
        res = r.resolve_target(TargetRequest(value="x"))
        assert res.ok and res.resolved.provider == "dom"

    def test_coordinate_fallback_requires_flag(self):
        r = UniversalTargetResolver()
        r.register_provider("coordinate", _provider("coordinate", 0.99))
        res = r.resolve_target(TargetRequest(value="x"))
        assert not res.ok
        assert any("not permitted" in a.detail for a in res.attempts)
        res2 = r.resolve_target(TargetRequest(value="x",
                                              allow_coordinate_fallback=True))
        assert res2.ok

    def test_low_confidence_continues_chain(self):
        r = UniversalTargetResolver()
        r.register_provider("accessibility", _provider("accessibility", 0.2))
        r.register_provider("dom", _provider("dom", 0.8))
        res = r.resolve_target(TargetRequest(value="x"))
        assert res.ok and res.resolved.provider == "dom"

    def test_nothing_found(self):
        r = UniversalTargetResolver()
        res = r.resolve_target(TargetRequest(value="ghost"))
        assert not res.ok and res.resolved is None
        assert res.attempts == []          # no providers registered

    def test_register_invalid_kind_rejected(self):
        r = UniversalTargetResolver()
        assert r.register_provider("telepathy", lambda req: None) is False
        assert r.register_provider("dom", "not-callable") is False

    def test_sanitize_dict_provider_result(self):
        r = UniversalTargetResolver()
        r.register_provider("ocr", lambda req: {
            "kind": "ocr", "value": "Log in", "confidence": 7.0,
            "point": [5, 6], "bbox": [1, 2, 3, 4],
            "metadata": {"font": "large"}})
        res = r.resolve_target(TargetRequest(value="log in"))
        assert res.ok
        assert res.resolved.confidence == 1.0        # clamped
        assert res.resolved.point == (5.0, 6.0)
        assert res.resolved.metadata == {"font": "large"}


class TestTargetExplainVerify:
    def test_explain_success_trace(self):
        r = UniversalTargetResolver()
        r.register_provider("accessibility", lambda req: None)
        r.register_provider("dom", _provider("dom"))
        res = r.resolve_target(TargetRequest(value="submit"))
        ex = r.explain_target(res)
        assert ex["ok"] is True
        assert any("accessibility" in line for line in ex["trace"])
        assert any("dom" in line for line in ex["trace"])

    def test_explain_failure_trace(self):
        r = UniversalTargetResolver()
        r.register_provider("ocr", lambda req: None)
        res = r.resolve_target(TargetRequest(value="ghost"))
        ex = r.explain_target(res)
        assert ex["ok"] is False
        assert "no provider" in ex["why"]

    def test_verify_target_contract(self):
        r = UniversalTargetResolver()
        good = ResolvedTarget(kind="dom", value="submit", confidence=0.9,
                              point=(1, 2))
        v = r.verify_target(good)
        assert v.verified and "still_visible" not in v.checks
        v2 = r.verify_target(good, expected={"kind": "dom",
                                             "value": "Submit"})
        assert v2.verified and "matches_expected" in v2.checks
        v3 = r.verify_target(good, expected={"kind": "ocr"})
        assert not v3.verified
        v4 = r.verify_target(None)
        assert not v4.verified
        v5 = r.verify_target(ResolvedTarget(kind="dom", value="x"))
        assert not v5.verified          # no location

    def test_verify_with_visibility_hook(self):
        r = UniversalTargetResolver()
        t = ResolvedTarget(kind="dom", value="x", confidence=0.5,
                           point=(0, 0))
        assert r.verify_target(t, still_visible_fn=lambda: True).verified
        v = r.verify_target(t, still_visible_fn=lambda: False)
        assert not v.verified and "no longer visible" in v.message

    def test_shared_resolver_for_human_and_agent(self):
        """§13 contract preview: same resolver serves both channels."""
        r = UniversalTargetResolver()
        r.register_provider("semantic_app_api",
                            _provider("semantic_app_api", 0.88))
        human = r.resolve_target(TargetRequest(description="that button",
                                               value="submit"))
        agent = r.resolve_target(TargetRequest(
            description="click the Submit button", value="submit"))
        assert human.ok and agent.ok
        assert human.resolved.provider == agent.resolved.provider


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
