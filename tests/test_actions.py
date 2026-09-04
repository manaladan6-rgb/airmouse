"""Tests for airmouse.actions (v8 Action Engine + executors) — headless."""
from __future__ import annotations

import pytest

from airmouse.actions import (
    ActionEngine,
    MockExecutor,
    PynputExecutor,
)
from airmouse.interfaces import (
    ActionPlan,
    ActionStatus,
    ActionType,
    Intent,
    IntentType,
    ScreenTarget,
    ScreenTargetType,
)


def make_target(cx=200.0, cy=150.0, w=40.0, h=20.0):
    return ScreenTarget(id="t", type=ScreenTargetType.BUTTON,
                        bbox=(cx - w / 2, cy - h / 2, w, h),
                        confidence=0.9, actionable=True)


def itype(t, **kw):
    return Intent(type=t, **kw)


@pytest.fixture()
def engine():
    ex = MockExecutor()
    return ActionEngine(executor=ex), ex


# ── planning: intent → action mapping ─────────────────────────────────────────

@pytest.mark.parametrize("src,action,keys", [
    (IntentType.CLICK, ActionType.CLICK, None),
    (IntentType.OPEN, ActionType.CLICK, None),
    # v10 (§10): SELECT is a first-class action (point-select); SELECT-all
    # is normalized to HOTKEY ctrl+a during planning.
    (IntentType.SELECT, ActionType.SELECT, None),
    (IntentType.PLAY, ActionType.CLICK, None),
    (IntentType.CONFIRM, ActionType.CLICK, None),
    (IntentType.DOUBLE_CLICK, ActionType.DOUBLE_CLICK, None),
    (IntentType.RIGHT_CLICK, ActionType.RIGHT_CLICK, None),
    (IntentType.SCROLL, ActionType.SCROLL, None),
    (IntentType.ZOOM, ActionType.ZOOM, None),
    (IntentType.TYPE, ActionType.TYPE, None),
    (IntentType.MOVE, ActionType.MOVE, None),
    (IntentType.COPY, ActionType.HOTKEY, ["ctrl", "c"]),
    (IntentType.PASTE, ActionType.HOTKEY, ["ctrl", "v"]),
    (IntentType.CLOSE, ActionType.HOTKEY, ["alt", "f4"]),
    (IntentType.MINIMIZE, ActionType.HOTKEY, ["win", "down"]),
    (IntentType.MAXIMIZE, ActionType.HOTKEY, ["win", "up"]),
    (IntentType.SWITCH_WINDOW, ActionType.HOTKEY, ["alt", "tab"]),
    (IntentType.BACK, ActionType.HOTKEY, ["alt", "left"]),
    (IntentType.FORWARD, ActionType.HOTKEY, ["alt", "right"]),
    (IntentType.CANCEL, ActionType.KEY_PRESS, ["esc"]),
])
def test_plan_intent_to_action_mapping(engine, src, action, keys):
    eng, _ = engine
    plan = eng.plan(itype(src, point=(10, 10), params={} if keys else {"amount": 3}))
    assert plan.action is action
    if keys:
        assert list(plan.params["keys"]) == keys
    assert plan.timeout == pytest.approx(2.0)
    assert plan.max_retries == 1


def test_plan_carries_point_and_requires_confirmation(engine):
    eng, _ = engine
    intent = itype(IntentType.CLICK, point=(12, 34))
    intent.requires_confirmation = True
    plan = eng.plan(intent)
    assert plan.target_point == (12, 34)
    assert plan.requires_confirmation is True


def test_plan_expected_click(engine):
    eng, _ = engine
    plan = eng.plan(itype(IntentType.CLICK, point=(5, 6)))
    assert plan.expected == {"type": "click", "point": (5, 6)}


def test_plan_expected_scroll_zoom_move_key(engine):
    eng, _ = engine
    assert eng.plan(itype(IntentType.SCROLL, params={"amount": 3})).expected == \
        {"type": "scroll", "delta_min": 1}
    assert eng.plan(itype(IntentType.ZOOM, params={"direction": "in"})).expected == \
        {"type": "zoom", "delta_min": 0.01}
    assert eng.plan(itype(IntentType.MOVE, point=(1, 2))).expected == \
        {"type": "pointer", "point": (1, 2)}
    assert eng.plan(itype(IntentType.COPY)).expected == {"type": "key"}


def test_plan_expected_drag_uses_end_point(engine):
    eng, _ = engine
    plan = eng.plan(itype(IntentType.DRAG, point=(0, 0),
                          params={"end": (40, 60)}))
    assert plan.expected == {"type": "pointer", "point": (40, 60)}


def test_plan_repeat_last_plan(engine):
    eng, _ = engine
    first = eng.plan(itype(IntentType.CLICK, point=(9, 9)))
    rep = eng.plan(itype(IntentType.REPEAT))
    assert rep.action is ActionType.CLICK
    assert rep.target_point == (9, 9)
    assert first.action is ActionType.CLICK


def test_plan_repeat_without_history_is_none_action(engine):
    eng, _ = engine
    plan = eng.plan(itype(IntentType.REPEAT))
    assert plan.action is ActionType.NONE


# ── execution ─────────────────────────────────────────────────────────────────

def test_execute_success_records_call_and_pointer(engine):
    eng, ex = engine
    report = eng.execute(eng.plan(itype(IntentType.CLICK, point=(15, 25))))
    assert report.status is ActionStatus.SUCCESS
    assert report.attempts == 1
    assert ("click", (15, 25)) in ex.record
    assert report.observation["pointer"] == (15, 25)
    assert report.ok


def test_execute_dispatches_all_primitives(engine):
    eng, ex = engine
    eng.execute(eng.plan(itype(IntentType.DOUBLE_CLICK, point=(1, 1))))
    eng.execute(eng.plan(itype(IntentType.RIGHT_CLICK, point=(2, 2))))
    eng.execute(eng.plan(itype(IntentType.MOVE, point=(3, 3))))
    eng.execute(eng.plan(itype(IntentType.SCROLL, params={"amount": -3})))
    eng.execute(eng.plan(itype(IntentType.TYPE, params={"text": "hi"})))
    eng.execute(eng.plan(itype(IntentType.COPY)))
    eng.execute(eng.plan(itype(IntentType.CANCEL)))
    names = [n for n, _ in ex.record]
    assert names == ["double_click", "right_click", "move", "scroll",
                     "type_text", "hotkey", "hotkey"]
    assert ex.record[3] == ("scroll", (-3,))
    assert ex.record[4] == ("type_text", ("hi",))
    assert ex.record[5] == ("hotkey", ("ctrl", "c"))
    assert ex.record[6] == ("hotkey", ("esc",))


def test_execute_zoom_uses_executor_zoom(engine):
    eng, ex = engine
    eng.execute(eng.plan(itype(IntentType.ZOOM, params={"direction": "in"})))
    assert ("zoom", (3,)) in ex.record
    eng.execute(eng.plan(itype(IntentType.ZOOM, params={"ticks": -5})))
    assert ("zoom", (-5,)) in ex.record


def test_executor_exception_retries_then_fails(engine):
    eng, ex = engine
    ex.fail_for.add("click")
    report = eng.execute(eng.plan(itype(IntentType.CLICK, point=(1, 1))))
    assert report.status is ActionStatus.FAILED
    assert report.attempts == 2  # 1 + max_retries
    assert "executor_error" in report.message
    assert len([r for r in ex.record if r[0] == "click"]) == 2


def test_executor_false_result_fails_without_retry(engine):
    eng, ex = engine
    ex.results["click"] = False
    report = eng.execute(eng.plan(itype(IntentType.CLICK, point=(1, 1))))
    assert report.status is ActionStatus.FAILED
    assert report.attempts == 1


class BlockedSafety:
    def approve_intent(self, intent, now=None):
        from airmouse.interfaces import SafetyDecision
        return SafetyDecision(allowed=False, reason="not_today")


class PermitSafety:
    def __init__(self):
        self.seen = []

    def approve_intent(self, intent, now=None):
        from airmouse.interfaces import SafetyDecision
        self.seen.append(intent)
        return SafetyDecision(allowed=True, reason="ok")


def test_safety_blocked_report_and_no_execution():
    ex = MockExecutor()
    eng = ActionEngine(executor=ex, safety=BlockedSafety())
    report = eng.execute(eng.plan(itype(IntentType.CLICK, point=(1, 1))))
    assert report.status is ActionStatus.BLOCKED
    assert report.message == "not_today"
    assert ex.record == []
    assert eng.stats[ActionStatus.BLOCKED] == 1


def test_safety_receives_intent_from_plan():
    ex = MockExecutor()
    safety = PermitSafety()
    eng = ActionEngine(executor=ex, safety=safety)
    intent = itype(IntentType.CLICK, point=(4, 4))
    eng.execute(eng.plan(intent))
    assert safety.seen and safety.seen[0] is intent


def test_safety_shim_intent_for_bare_plan():
    ex = MockExecutor()
    safety = PermitSafety()
    eng = ActionEngine(executor=ex, safety=safety)
    bare = ActionPlan(action=ActionType.CLICK, point=(8, 8))
    eng.execute(bare)
    assert safety.seen[0].type is IntentType.CLICK
    assert safety.seen[0].point == (8, 8)


# ── preconditions ──────────────────────────────────────────────────────────────

def test_precondition_no_action(engine):
    eng, ex = engine
    report = eng.execute(ActionPlan(action=ActionType.NONE))
    assert report.status is ActionStatus.FAILED
    assert report.message == "no_action"
    assert ex.record == []


def test_precondition_missing_point(engine):
    eng, _ = engine
    report = eng.execute(ActionPlan(action=ActionType.CLICK))
    assert report.status is ActionStatus.FAILED
    assert report.message == "missing_point"


def test_precondition_point_out_of_bounds():
    eng = ActionEngine(executor=MockExecutor())
    eng.set_bounds(100, 100)
    report = eng.execute(ActionPlan(action=ActionType.CLICK, point=(500, 500)))
    assert report.status is ActionStatus.FAILED
    assert report.message == "point_out_of_bounds"


@pytest.mark.parametrize("amount,message", [
    (500, "scroll_amount_out_of_range"),
    (3.5, "scroll_amount_not_int"),
    (None, "missing_scroll_amount"),
])
def test_precondition_scroll_amount(engine, amount, message):
    eng, _ = engine
    params = {} if amount is None else {"amount": amount}
    report = eng.execute(ActionPlan(action=ActionType.SCROLL, params=params))
    assert report.status is ActionStatus.FAILED
    assert report.message == message


@pytest.mark.parametrize("text,message", [
    ("", "invalid_text"),
    ("x" * 501, "invalid_text"),
    (None, "invalid_text"),
])
def test_precondition_type_text(engine, text, message):
    eng, _ = engine
    report = eng.execute(ActionPlan(action=ActionType.TYPE,
                                    params={"text": text}))
    assert report.status is ActionStatus.FAILED
    assert report.message == message


@pytest.mark.parametrize("keys,message", [
    ([], "invalid_hotkey"),
    (["a", "b", "c", "d", "e"], "invalid_hotkey"),
    (["ctrl", ""], "invalid_hotkey"),
    ("ctrl+c", "invalid_hotkey"),
])
def test_precondition_hotkey(engine, keys, message):
    eng, _ = engine
    report = eng.execute(ActionPlan(action=ActionType.HOTKEY, params={"keys": keys}))
    assert report.status is ActionStatus.FAILED
    assert report.message == message


@pytest.mark.parametrize("params", [
    {}, {"direction": "sideways"}, {"ticks": "five"},
])
def test_precondition_zoom(engine, params):
    eng, _ = engine
    report = eng.execute(ActionPlan(action=ActionType.ZOOM, params=dict(params)))
    assert report.status is ActionStatus.FAILED
    assert report.message == "invalid_zoom"


def test_precondition_drag_needs_endpoints(engine):
    eng, _ = engine
    report = eng.execute(ActionPlan(action=ActionType.DRAG, point=(0, 0)))
    assert report.status is ActionStatus.FAILED
    assert report.message == "missing_drag_endpoints"


def test_zoom_precondition_passes_with_direction(engine):
    eng, ex = engine
    report = eng.execute(ActionPlan(action=ActionType.ZOOM,
                                    params={"direction": "out"}))
    assert report.status is ActionStatus.SUCCESS
    assert ("zoom", (-3,)) in ex.record


# ── stats / convenience / bounds ────────────────────────────────────────────────

def test_stats_and_reset_stats(engine):
    eng, _ = engine
    eng.execute(eng.plan(itype(IntentType.CLICK, point=(1, 1))))
    eng.execute(ActionPlan(action=ActionType.NONE))
    assert eng.stats[ActionStatus.SUCCESS] == 1
    assert eng.stats[ActionStatus.FAILED] == 1
    eng.reset_stats()
    assert eng.stats[ActionStatus.SUCCESS] == 0
    assert eng.stats[ActionStatus.FAILED] == 0


def test_execute_intent_convenience(engine):
    eng, ex = engine
    report = eng.execute_intent(itype(IntentType.CLICK, point=(33, 44)))
    assert report.status is ActionStatus.SUCCESS
    assert ("click", (33, 44)) in ex.record


def test_set_bounds_propagates_to_executor():
    ex = MockExecutor()
    eng = ActionEngine(executor=ex)
    eng.set_bounds(800, 600)
    assert (ex.screen_w, ex.screen_h) == (800, 600)
    assert (eng.screen_w, eng.screen_h) == (800, 600)


# ── MockExecutor behaviour ─────────────────────────────────────────────────────

def test_mock_executor_records_and_results():
    ex = MockExecutor()
    assert ex.click(1, 2) is True
    assert ex.record == [("click", (1, 2))]
    ex.results["scroll"] = {"offset": 5}
    assert ex.scroll(5) == {"offset": 5}


def test_mock_executor_fail_for_raises():
    ex = MockExecutor()
    ex.fail_for.add("hotkey")
    with pytest.raises(RuntimeError):
        ex.hotkey(["ctrl", "c"])


# ── PynputExecutor: graceful degradation + clamping ─────────────────────────────

class _FakeMouse:
    def __init__(self):
        self.position = (0, 0)
        self.calls = []

    def click(self, button, count=1):
        self.calls.append(("click", self.position, count))

    def scroll(self, dx, dy):
        self.calls.append(("scroll", dx, dy))

    def press(self, button):
        self.calls.append(("press",))

    def release(self, button):
        self.calls.append(("release",))


class _FakeButton:
    left = "L"
    right = "R"
    middle = "M"


def test_pynput_executor_constructs_and_degrades():
    ex = PynputExecutor()
    assert isinstance(ex.available, bool)
    # never raises whether or not pynput/display exist:
    assert isinstance(ex.click(10, 10), bool)
    assert isinstance(ex.scroll(3), bool)
    assert isinstance(ex.type_text("x"), bool)
    assert isinstance(ex.hotkey(["ctrl", "c"]), bool)


def test_pynput_executor_clamps_with_injected_mouse():
    ex = PynputExecutor(screen_w=100, screen_h=80)
    fake = _FakeMouse()
    ex._mouse = fake  # inject before first use → no pynput needed
    assert ex.move(-50, -50) is True
    assert fake.position == (0, 0)
    assert ex.move(500, 500) is True
    assert fake.position == (99, 79)


def test_pynput_executor_drag_chunks_with_injected_mouse(monkeypatch):
    ex = PynputExecutor(screen_w=200, screen_h=200)
    fake = _FakeMouse()
    ex._mouse = fake
    monkeypatch.setattr("airmouse.actions.pynput_button_left", lambda: "L")
    sleeps = []
    monkeypatch.setattr("airmouse.actions.time.sleep",
                        lambda s: sleeps.append(s))
    assert ex.drag(0, 0, 80, 40, duration=0.4) is True
    moves = [c for c in fake.calls if c[0] == "press"]
    releases = [c for c in fake.calls if c[0] == "release"]
    assert len(moves) == 1 and len(releases) == 1
    # 8 interpolated chunks were slept
    assert len(sleeps) == 8


def test_pynput_executor_set_bounds():
    ex = PynputExecutor()
    ex.set_bounds(1234, 567)
    assert (ex.screen_w, ex.screen_h) == (1234, 567)
