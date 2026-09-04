"""Tests for airmouse.verification (v8 verifier + recovery) — headless."""
from __future__ import annotations

import pytest

from airmouse.interfaces import (
    ActionPlan,
    ActionReport,
    ActionStatus,
    RecoveryStrategy,
    VerificationStatus,
)
from airmouse.verification import ActionVerifier, RecoveryManager


def plan(action_point=(100, 100), expected=None, **kw):
    return ActionPlan(point=action_point,
                      expected=expected if expected is not None else {},
                      **kw)


def report(status=ActionStatus.SUCCESS, attempts=1, observation=None):
    return ActionReport(status=status, attempts=attempts,
                        observation=dict(observation or {}))


@pytest.fixture()
def verifier():
    return ActionVerifier()


# ── click verification ─────────────────────────────────────────────────────────

def test_click_passed_on_pixel_change(verifier):
    p = plan(expected={"type": "click", "point": (10, 10)})
    r = report(observation={"pixel": (0, 0, 0)})
    res = verifier.verify(p, r, lambda pt: {"pixel": (255, 0, 0)})
    assert res.status is VerificationStatus.PASSED
    assert res.similarity == pytest.approx(1.0)
    assert res.suggested_recovery is RecoveryStrategy.NONE


def test_click_failed_without_change(verifier):
    p = plan(expected={"type": "click", "point": (10, 10)})
    r = report(observation={"pixel": (1, 2, 3)})
    res = verifier.verify(p, r, lambda pt: {"pixel": (1, 2, 3)})
    assert res.status is VerificationStatus.FAILED
    assert res.similarity == pytest.approx(0.0)
    assert res.suggested_recovery is RecoveryStrategy.RETRY


def test_click_unknown_missing_signals(verifier):
    p = plan(expected={"type": "click", "point": (10, 10)})
    res = verifier.verify(p, report(), lambda pt: {})
    assert res.status is VerificationStatus.UNKNOWN
    assert res.suggested_recovery is RecoveryStrategy.NONE


def test_click_passed_on_pointer_near(verifier):
    p = plan(expected={"type": "click", "point": (10, 10)})
    res = verifier.verify(p, report(), lambda pt: {"pointer": (20, 20)})
    assert res.status is VerificationStatus.PASSED  # dist ≈ 14.1 < 30
    assert 0.0 < res.similarity < 1.0


def test_click_unknown_without_baseline_pixel(verifier):
    p = plan(expected={"type": "click", "point": (10, 10)})
    res = verifier.verify(p, report(), lambda pt: {"pixel": (9, 9, 9)})
    assert res.status is VerificationStatus.UNKNOWN


def test_click_passed_on_presence_change(verifier):
    p = plan(expected={"type": "click", "point": (10, 10)})
    r = report(observation={"present": False})
    res = verifier.verify(p, r, lambda pt: {"present": True})
    assert res.status is VerificationStatus.PASSED


# ── pointer / scroll / zoom / key ───────────────────────────────────────────────

def test_pointer_within_tolerance_similarity(verifier):
    p = plan(expected={"type": "pointer", "point": (0, 0)})
    res = verifier.verify(p, report(), lambda pt: {"pointer": (0, 15)})
    assert res.status is VerificationStatus.PASSED
    assert res.similarity == pytest.approx(0.5)  # 15px / 30px tolerance


def test_pointer_beyond_tolerance_fails(verifier):
    p = plan(expected={"type": "pointer", "point": (0, 0)})
    res = verifier.verify(p, report(), lambda pt: {"pointer": (60, 0)})
    assert res.status is VerificationStatus.FAILED


def test_pointer_unknown_without_pointer(verifier):
    p = plan(expected={"type": "pointer", "point": (0, 0)})
    res = verifier.verify(p, report(), lambda pt: {"pixel": (1, 1, 1)})
    assert res.status is VerificationStatus.UNKNOWN


def test_scroll_threshold(verifier):
    p = plan(expected={"type": "scroll", "delta_min": 1})
    ok = verifier.verify(p, report(), lambda pt: {"content_offset": 5.0})
    assert ok.status is VerificationStatus.PASSED
    bad = verifier.verify(p, report(), lambda pt: {"content_offset": 0.0})
    assert bad.status is VerificationStatus.FAILED


def test_scroll_unknown_without_offset(verifier):
    p = plan(expected={"type": "scroll", "delta_min": 1})
    res = verifier.verify(p, report(), lambda pt: {"pixel": (0, 0, 0)})
    assert res.status is VerificationStatus.UNKNOWN


def test_zoom_delta_vs_baseline(verifier):
    p = plan(expected={"type": "zoom", "delta_min": 0.01})
    r = report(observation={"zoom_level": 1.0})
    ok = verifier.verify(p, r, lambda pt: {"zoom_level": 1.05})
    assert ok.status is VerificationStatus.PASSED
    same = verifier.verify(p, r, lambda pt: {"zoom_level": 1.0})
    assert same.status is VerificationStatus.FAILED
    missing = verifier.verify(p, r, lambda pt: {})
    assert missing.status is VerificationStatus.UNKNOWN


def test_key_window_title_change(verifier):
    p = plan(expected={"type": "key"})
    r = report(observation={"before_window_title": "Before"})
    ok = verifier.verify(p, r, lambda pt: {"window_title": "After"})
    assert ok.status is VerificationStatus.PASSED
    same = verifier.verify(p, r, lambda pt: {"window_title": "Before"})
    assert same.status is VerificationStatus.FAILED
    missing = verifier.verify(p, r, lambda pt: {})
    assert missing.status is VerificationStatus.UNKNOWN


# ── NOT_NEEDED / robustness ─────────────────────────────────────────────────────

def test_not_needed_without_expected(verifier):
    res = verifier.verify(plan(expected=None), report(),
                          lambda pt: {"pixel": (0, 0, 0)})
    assert res.status is VerificationStatus.NOT_NEEDED


def test_not_needed_without_observer(verifier):
    p = plan(expected={"type": "click", "point": (1, 1)})
    res = verifier.verify(p, report(), None)
    assert res.status is VerificationStatus.NOT_NEEDED


def test_observer_exception_treated_as_unknown(verifier):
    def boom(pt):
        raise RuntimeError("no display")
    p = plan(expected={"type": "click", "point": (1, 1)})
    res = verifier.verify(p, report(), boom)
    assert res.status is VerificationStatus.UNKNOWN


def test_make_observe_fn_never_raises(verifier):
    obs = ActionVerifier.make_observe_fn()
    out = obs((10, 10))
    assert isinstance(out, dict)  # empty headless, pixel otherwise
    assert obs(None) == {}


# ── RecoveryManager ladder ──────────────────────────────────────────────────────

def test_recovery_ladder_retry_then_adjusted_then_notify():
    rm = RecoveryManager()
    notes = []
    rm.notify_hook = notes.append
    p = plan(expected={}, max_retries=2)
    strat, p2 = rm.handle(p, report(ActionStatus.FAILED, attempts=1))
    assert strat is RecoveryStrategy.RETRY and p2 is not None and p2 is not p
    strat, p3 = rm.handle(p, report(ActionStatus.FAILED, attempts=2))
    assert strat is RecoveryStrategy.RETRY_ADJUSTED
    assert p3.point[0] == pytest.approx(p.point[0] + 12)
    strat, p4 = rm.handle(p, report(ActionStatus.FAILED, attempts=2))
    assert strat is RecoveryStrategy.NOTIFY and p4 is None
    assert notes and "retries_exhausted" in notes[0]


def test_recovery_ledger_shared_across_copies():
    rm = RecoveryManager()
    p = plan(max_retries=3)
    _, p2 = rm.handle(p, report(ActionStatus.FAILED, attempts=1))
    # The retry copy must share the plan's ledger identity...
    strat, _ = rm.handle(p2, report(ActionStatus.FAILED, attempts=1))
    assert strat is RecoveryStrategy.RETRY_ADJUSTED  # not a fresh RETRY


def test_recovery_blocked_is_final():
    rm = RecoveryManager()
    strat, p2 = rm.handle(plan(), report(ActionStatus.BLOCKED, attempts=1))
    assert strat is RecoveryStrategy.NONE and p2 is None


def test_recovery_cancelled_is_final():
    rm = RecoveryManager()
    strat, p2 = rm.handle(plan(), report(ActionStatus.CANCELLED))
    assert strat is RecoveryStrategy.NONE and p2 is None


def test_recovery_needs_confirmation_never_retries():
    rm = RecoveryManager()
    p = plan(requires_confirmation=True)
    strat, p2 = rm.handle(p, report(ActionStatus.FAILED, attempts=0))
    assert strat is RecoveryStrategy.NOTIFY and p2 is None


def test_recovery_timeout_retry_once():
    rm = RecoveryManager()
    p = plan()
    strat, p2 = rm.handle(p, report(ActionStatus.TIMEOUT, attempts=1))
    assert strat is RecoveryStrategy.RETRY and p2 is not None
    strat, p3 = rm.handle(p, report(ActionStatus.TIMEOUT, attempts=1))
    assert strat is RecoveryStrategy.NOTIFY and p3 is None


def test_recovery_attempt_cap_two():
    rm = RecoveryManager()
    p = plan(max_retries=5)  # generous plan-level retries...
    # ...but the engine already burned 2 attempts → no plain RETRY granted
    strat, p2 = rm.handle(p, report(ActionStatus.FAILED, attempts=2))
    assert strat is RecoveryStrategy.RETRY_ADJUSTED and p2 is not None
    strat, _ = rm.handle(p, report(ActionStatus.FAILED, attempts=2))
    assert strat is RecoveryStrategy.NOTIFY


def test_recovery_success_needs_nothing():
    rm = RecoveryManager()
    strat, p2 = rm.handle(plan(), report(ActionStatus.SUCCESS))
    assert strat is RecoveryStrategy.NONE and p2 is None


def test_recovery_on_failed_verification_of_successful_action():
    from airmouse.interfaces import VerificationResult
    rm = RecoveryManager()
    p = plan(max_retries=2)
    res = VerificationResult(status=VerificationStatus.FAILED)
    strat, p2 = rm.handle(p, report(ActionStatus.SUCCESS), res)
    assert strat is RecoveryStrategy.RETRY and p2 is not None


def test_recovery_reset_clears_ledger():
    rm = RecoveryManager()
    p = plan(max_retries=2)
    rm.handle(p, report(ActionStatus.FAILED, attempts=1))
    rm.handle(p, report(ActionStatus.FAILED, attempts=2))
    rm.handle(p, report(ActionStatus.FAILED, attempts=2))  # now exhausted
    rm.reset()
    strat, p2 = rm.handle(p, report(ActionStatus.FAILED, attempts=0))
    assert strat is RecoveryStrategy.RETRY and p2 is not None
