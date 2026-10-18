"""R3b1 — OPERATION FINAL FORM: macro replay / gesture scroll / action
dispatch consult the central safety gate (regression tests).

Every test here is deterministic and fake-backend only — NO real input
automation:

* macro replay dispatch target  = recording callable (v1 MacroPlayer)
                                  / :class:`actions.MockExecutor` (v2 runner)
* ActionEngine dispatch target  = :class:`actions.MockExecutor` (records)
* spine scroll backend          = ``FakeSpineMouse`` (records ``.scroll``)

The process-wide central gate is reset to a VIRGIN singleton before and
after every test (``safety_gate.reset_gate_for_tests``) so estop /
shutdown latches can never leak between tests — and the macro module's
cancel latch / replay-thread slot are cleared for the same reason.

Contracts locked (task R3b1):
1. MacroPlayer consults ``get_gate().authorize("macro_action")`` BEFORE
   the first action, re-checks before EACH step and inside wait/sleep
   loops; estop/shutdown refuse the replay pre-dispatch with the reason
   surfaced.
2. Cooperative cancellation: per-play ``stop_check``, ``MacroPlayer.cancel``
   (latch + BOUNDED join, never self-joining, honest unterminated-worker
   report) and module-wide ``cancel_current_replay``; the old hardcoded
   ``None`` stop_check in ``play_async`` is gone.
3. actions.py dispatch paths consult the gate with the correct
   action_class (pointer→"gesture", scroll→"scroll_action",
   keyboard→"keyboard_action"); unclassified action types are
   DEFAULT-DENIED before dispatch; refusals come back as honest BLOCKED
   reports, never raw exceptions.
4. gesture_spine: ``scroll_continuous`` is the gated PEACE scroll
   emission point (authorize before the first tick, per-iteration
   re-check, mid-scroll trip stops the wheel); clean-gate behavior is
   byte-identical to the pre-gate contract.
"""
from __future__ import annotations

import threading
import time

import pytest

from airmouse import macros as macros_mod
from airmouse import safety_gate
from airmouse.actions import ActionEngine, MockExecutor
from airmouse.gesture_spine import GestureActionRouter
from airmouse.interfaces import (
    ActionPlan,
    ActionStatus,
    ActionType,
    Intent,
    IntentType,
    MacroOp,
    MacroProgram,
    MacroStep,
)
from airmouse.macros import MacroPlayer, ProgramRunner


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_gate_and_replay_slots():
    """Virgin central gate + clean macro replay state for every test."""
    safety_gate.reset_gate_for_tests()
    macros_mod._current_play_thread = None
    macros_mod._MODULE_CANCEL.clear()
    yield
    macros_mod._current_play_thread = None
    macros_mod._MODULE_CANCEL.clear()
    safety_gate.reset_gate_for_tests()


# ── helpers ──────────────────────────────────────────────────────────────────

def _gate():
    return safety_gate.get_gate()


def _trip(reason: str = "test estop") -> None:
    _gate().trip_estop(reason)


def _events(*pairs) -> list:
    """v1 macro events, all at t=0 (no inter-event sleeps)."""
    return [{"t": 0.0, "event": name, **params} for name, params in pairs]


class FakeSpineMouse:
    """Deterministic spine backend; can trip the gate mid-scroll."""

    def __init__(self, trip_on: int = None, trip_reason: str = "mid-scroll trip"):
        self.scrolls: list = []
        self.left_clicks = 0
        self.trip_on = trip_on
        self.trip_reason = trip_reason

    def scroll(self, amount: int) -> bool:
        self.scrolls.append(int(amount))
        if self.trip_on is not None and len(self.scrolls) >= self.trip_on:
            _trip(self.trip_reason)
        return True

    def left_click(self) -> bool:
        self.left_clicks += 1
        return True


# ── 1. macro replay refuses pre-dispatch when the gate is latched ───────────

def test_estopped_gate_refuses_macro_replay_pre_dispatch():
    seen = []
    player = MacroPlayer(lambda ev, pr: seen.append(ev))
    player._macro = {"name": "t", "events": _events(
        ("click", {}), ("scroll", {"amount": 2}))}
    _trip("panic button")

    assert player.play() is False
    assert seen == []                                  # NOTHING dispatched
    res = player.last_result
    assert res["started"] is False                     # refused pre-dispatch
    assert res["stopped"] is True
    assert res["events_dispatched"] == 0
    assert "estop" in res["reason"] and "panic button" in res["reason"]


def test_shutdown_gate_refuses_macro_replay_pre_dispatch():
    seen = []
    player = MacroPlayer(lambda ev, pr: seen.append(ev))
    player._macro = {"name": "t", "events": _events(("click", {}))}
    _gate().mark_shutdown("teardown")

    res = player.play_detailed()
    assert seen == []
    assert res["started"] is False
    assert "shutdown" in res["reason"]


def test_play_async_refuses_pre_flight_when_gate_estopped():
    seen = []
    player = MacroPlayer(lambda ev, pr: seen.append(ev))
    player._macro = {"name": "t", "events": _events(("click", {}))}
    _trip("no async under estop")

    assert player.play_async() is None                 # no worker started
    time.sleep(0.05)
    assert seen == []
    assert player.last_result["started"] is False
    assert "estop" in player.last_result["reason"]


# ── 2. cooperative cancellation ──────────────────────────────────────────────

def test_cancel_before_play_refuses_then_latch_is_consumed():
    seen = []
    player = MacroPlayer(lambda ev, pr: seen.append(ev))
    player._macro = {"name": "t", "events": _events(("click", {}), ("click", {}))}

    report = player.cancel()
    assert report["requested"] is True
    assert report["terminated"] is True                # nothing was running

    res = player.play_detailed()
    assert res["started"] is False and seen == []      # refused cleanly
    assert res["reason"] == "cancel_requested"
    assert res["events_dispatched"] == 0

    # the latch was consumed by that refused start: a fresh play runs
    assert player.play() is True
    assert len(seen) == 2


def test_cancel_mid_sequence_via_stop_check_gives_honest_partial():
    seen = []
    player = MacroPlayer(lambda ev, pr: seen.append(ev))
    player._macro = {"name": "t", "events": _events(
        ("click", {}), ("scroll", {"amount": 1}), ("click", {}), ("click", {}))}

    res = player.play_detailed(stop_check=lambda: len(seen) >= 2)
    assert res["stopped"] is True and res["completed"] is False
    assert res["events_dispatched"] == 2
    assert seen == ["click", "scroll"]        # steps 3..4 never dispatched
    assert res["reason"] == "stop_requested"


def test_cancel_from_executor_mid_sequence_stops_cleanly():
    seen = []
    holder = {}

    def executor(ev, params):
        seen.append(ev)
        if len(seen) == 2:
            holder["cancel_report"] = holder["player"].cancel()

    holder["player"] = MacroPlayer(executor)
    holder["player"]._macro = {"name": "t",
                               "events": _events(*[("click", {})] * 4)}
    res = holder["player"].play_detailed()

    assert seen == ["click", "click"]                  # steps 3..4 never ran
    assert res["stopped"] is True
    assert res["reason"] == "cancel_requested"
    assert res["events_dispatched"] == 2
    # self-cancel: the latch was armed, the (self-)join was honestly skipped
    assert holder["cancel_report"]["requested"] is True
    assert holder["cancel_report"]["self_join_skipped"] is True


def test_cancel_during_a_wait_stops_promptly():
    seen = []
    # v1 wait semantics: the sleep is the inter-event ``t`` delta — the
    # 5 s wait here is the gap before the recorded wait/click events.
    player = MacroPlayer(lambda ev, pr: seen.append(ev))
    player._macro = {"name": "t", "events": _events(
        ("click", {}), ("wait", {"seconds": 5.0}), ("click", {}))}
    player._macro["events"][1]["t"] = 5.0   # long sleep BEFORE the wait event
    player._macro["events"][2]["t"] = 5.0

    def canceller():
        time.sleep(0.05)
        player.cancel(timeout_s=1.0)

    th = threading.Thread(target=canceller, daemon=True)
    th.start()
    t0 = time.perf_counter()
    res = player.play_detailed()
    elapsed = time.perf_counter() - t0
    th.join(2.0)

    assert elapsed < 2.5                    # did NOT sleep through the 5 s wait
    assert res["stopped"] is True
    assert res["reason"] == "cancel_requested"
    assert seen == ["click"]                # the step after the wait never ran
    assert res["events_dispatched"] == 1


def test_estop_tripped_mid_play_stops_before_next_step():
    seen = []

    def executor(ev, params):
        seen.append(ev)
        if len(seen) == 1:                  # trip the gate inside a step hook
            _gate().trip_estop("mid-play trip")

    player = MacroPlayer(executor)
    player._macro = {"name": "t", "events": _events(*[("click", {})] * 3)}

    res = player.play_detailed()
    assert seen == ["click"]                # no further steps dispatched
    assert res["stopped"] is True
    assert res["events_dispatched"] == 1
    assert "estop" in res["reason"] and "mid-play trip" in res["reason"]


def test_estop_during_wait_aborts_the_sleep_checkpoint_based():
    seen = []
    # A single event whose 5 s inter-event delta has not elapsed yet: the
    # replay is INSIDE its sleep loop when the gate trips.
    player = MacroPlayer(lambda ev, pr: seen.append(ev))
    player._macro = {"name": "t",
                     "events": [{"t": 5.0, "event": "click"}]}
    timer = threading.Timer(0.05, lambda: _gate().trip_estop("trip inside wait"))
    timer.start()

    t0 = time.perf_counter()
    res = player.play_detailed()
    elapsed = time.perf_counter() - t0
    timer.join()

    assert elapsed < 2.5                    # the 50 ms-chunked wait noticed
    assert seen == []
    assert res["events_dispatched"] == 0
    assert res["stopped"] is True and "estop" in res["reason"]


def test_bounded_join_reports_unterminated_worker_honestly():
    seen = []
    entered = threading.Event()
    release = threading.Event()

    def blocking_executor(ev, params):
        seen.append(ev)
        entered.set()
        release.wait(5.0)        # worker stuck INSIDE a dispatch: no checkpoint

    player = MacroPlayer(blocking_executor)
    player._macro = {"name": "t", "events": _events(*[("click", {})] * 3)}
    th = player.play_async()
    assert th is not None
    assert entered.wait(2.0)

    t0 = time.perf_counter()
    report = player.cancel(timeout_s=0.05)
    bounded = time.perf_counter() - t0

    assert bounded < 2.0                    # the join returned within its bound
    assert report["join_attempted"] is True
    assert report["terminated"] is False    # honest: worker did NOT terminate
    assert report["thread_alive"] is True

    release.set()                           # worker now hits its checkpoint
    th.join(2.0)
    assert not th.is_alive()
    res = player.last_result
    assert res["stopped"] is True and res["reason"] == "cancel_requested"
    assert seen == ["click"]                # event 2 never dispatched


def test_double_cancel_is_idempotent():
    player = MacroPlayer(lambda ev, pr: None)
    first = player.cancel()
    second = player.cancel()
    assert first == second                  # same honest report, no error
    assert first["requested"] is True
    assert first["terminated"] is True      # no worker was ever running


def test_module_cancel_current_replay_stops_the_live_replay():
    seen = []
    player = MacroPlayer(lambda ev, pr: seen.append(ev))
    player._macro = {"name": "t", "events": [
        {"t": 0.0, "event": "click"},
        {"t": 5.0, "event": "click"},      # worker sleeping here in 50 ms chunks
    ]}
    th = player.play_async()
    assert th is not None
    deadline = time.monotonic() + 2.0
    while not seen and time.monotonic() < deadline:
        time.sleep(0.005)
    assert seen == ["click"]                # worker is inside the 5 s wait

    report = macros_mod.cancel_current_replay(timeout_s=1.0)
    th.join(2.0)

    assert report["requested"] is True
    assert report["was_running"] is True
    assert report["terminated"] is True     # bounded join succeeded
    assert not th.is_alive()
    res = player.last_result
    assert res["stopped"] is True and res["reason"] == "cancel_requested"
    assert len(seen) == 1                   # no further events dispatched


# ── 3. gesture spine: gated PEACE scroll ─────────────────────────────────────

def test_scroll_authorized_when_clean_emits_on_fake_backend():
    fake = FakeSpineMouse()
    router = GestureActionRouter(mouse=fake)

    res = router.scroll_continuous(3)
    assert res == {"executed": True, "reason": "ok"}
    assert fake.scrolls == [3]                          # emitted on the backend

    disp = router.dispatch("scroll", amount=2, now=1.0)  # discrete path, gated
    assert disp["executed"] is True
    assert fake.scrolls == [3, 2]


def test_scroll_stops_mid_scroll_after_gate_trip():
    fake = FakeSpineMouse(trip_on=3, trip_reason="mid-scroll trip")
    router = GestureActionRouter(mouse=fake)

    results = [router.scroll_continuous(1) for _ in range(6)]

    assert fake.scrolls == [1, 1, 1]         # nothing emitted after the trip
    assert all(r["executed"] for r in results[:3])
    assert not any(r["executed"] for r in results[3:])
    assert all("estop" in r["reason"] and "mid-scroll trip" in r["reason"]
               for r in results[3:])


def test_peace_scroll_refusal_reason_visible():
    _trip("peace panic")
    fake = FakeSpineMouse()
    router = GestureActionRouter(mouse=fake)

    res = router.scroll_continuous(2)
    assert res["executed"] is False
    assert fake.scrolls == []                            # nothing emitted
    assert "estop" in res["reason"] and "peace panic" in res["reason"]
    assert router.last_refusal == res["reason"]          # visible on the router


# ── 4. actions.py: gate-consulted dispatch ───────────────────────────────────

def test_keyboard_action_through_gate():
    ex = MockExecutor()
    eng = ActionEngine(executor=ex)
    report = eng.execute(
        eng.plan(Intent(type=IntentType.TYPE, params={"text": "hi"})))
    assert report.status is ActionStatus.SUCCESS        # clean gate → dispatched
    assert ("type_text", ("hi",)) in ex.record

    _trip("keys locked")
    ex2 = MockExecutor()
    eng2 = ActionEngine(executor=ex2)
    blocked = eng2.execute(
        eng2.plan(Intent(type=IntentType.TYPE, params={"text": "no"})))
    assert blocked.status is ActionStatus.BLOCKED       # honest refusal result
    assert "estop" in blocked.message and "keys locked" in blocked.message
    assert ex2.record == []                             # never dispatched


def test_unknown_action_type_default_denied_in_actions():
    ex = MockExecutor()
    eng = ActionEngine(executor=ex)

    report = eng.execute(ActionPlan(action=ActionType.VOLUME, params={}))

    assert report.status is ActionStatus.BLOCKED
    assert report.message.startswith("unclassified_action:")
    assert "volume" in report.message
    assert ex.record == []                              # DEFAULT-DENY: never dispatched
    assert eng.stats[ActionStatus.BLOCKED] == 1


# ── 5. clean-gate behavior is identical + reason strings surfaced ────────────

def test_gate_clean_behavior_identical_smoke():
    # v1 macro replay completes exactly as before the gate existed
    seen = []
    player = MacroPlayer(lambda ev, pr: seen.append(ev))
    player._macro = {"name": "t", "events": _events(
        ("click", {}), ("scroll", {"amount": 2}))}
    assert player.play() is True
    assert seen == ["click", "scroll"]
    assert player.last_result["completed"] is True
    assert player.last_result["reason"] == ""

    # v2 program runner unchanged on a clean gate
    ex = MockExecutor()
    runner = ProgramRunner(ex)
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.CLICK, params={"x": 10, "y": 20}),
        MacroStep(op=MacroOp.TYPE, params={"text": "hello"}),
        MacroStep(op=MacroOp.SCROLL, params={"amount": -2}),
        MacroStep(op=MacroOp.HOTKEY, params={"keys": ["ctrl", "s"]}),
    ])
    rep = runner.run(program)
    assert rep["steps_executed"] == 4 and rep["failed"] == 0
    assert rep["aborted"] is False
    assert ex.record == [
        ("click", (10.0, 20.0)),
        ("type_text", ("hello",)),
        ("scroll", (-2,)),
        ("hotkey", ("ctrl", "s")),
    ]

    # action engine primitives unchanged on a clean gate
    eng = ActionEngine(executor=ex)
    assert eng.execute(
        eng.plan(Intent(type=IntentType.CLICK, point=(15, 25))
                 )).status is ActionStatus.SUCCESS
    assert eng.execute(
        eng.plan(Intent(type=IntentType.SCROLL, params={"amount": 3})
                 )).status is ActionStatus.SUCCESS
    assert eng.execute(
        eng.plan(Intent(type=IntentType.COPY))).status is ActionStatus.SUCCESS

    # spine discrete dispatch + continuous cursor gate unchanged
    fake = FakeSpineMouse()
    router = GestureActionRouter(mouse=fake)
    ok = router.dispatch("left_click", now=1000.0)
    assert ok == {"executed": True, "reason": "ok", "risk": "SAFE"}
    assert fake.left_clicks == 1
    assert router.gate_continuous() is True
    assert router.dispatch("scroll", amount=1, now=1000.5)["executed"] is True


def test_refusal_reason_strings_surfaced():
    _trip("surface me")
    reason = "estop: surface me"

    # macro player result
    player = MacroPlayer(lambda ev, pr: None)
    player._macro = {"name": "t", "events": _events(("click", {}))}
    res = player.play_detailed()
    assert res["reason"] == reason
    assert player.last_result["reason"] == reason

    # action engine BLOCKED report
    ex = MockExecutor()
    eng = ActionEngine(executor=ex)
    rep = eng.execute(eng.plan(Intent(type=IntentType.CLICK, point=(1, 1))))
    assert rep.status is ActionStatus.BLOCKED
    assert rep.message == reason
    assert ex.record == []

    # spine scroll + dispatch
    fake = FakeSpineMouse()
    router = GestureActionRouter(mouse=fake)
    assert router.scroll_continuous(1)["reason"] == reason
    disp = router.dispatch("left_click")
    assert disp["executed"] is False and disp["reason"] == reason
    assert fake.scrolls == [] and fake.left_clicks == 0

    # v2 program runner abort report
    run = ProgramRunner(MockExecutor()).run(MacroProgram(steps=[
        MacroStep(op=MacroOp.TYPE, params={"text": "x"})]))
    assert run["aborted"] is True and run["reason"] == reason
    assert run["steps_executed"] == 0
