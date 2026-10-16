"""PHX-4-c — v19.0.2 safety-contract tests (E-STOP cannot latch inputs).

Locks four contracts with fakes only (no OS input calls; pynput is
unavailable headless, so MouseController/KeyboardActions are None-backed):

A. GestureActionRouter.trip_estop invokes estop_release_fn on EVERY trip
   (best-effort, exceptions swallowed) and gate_continuous is False while
   estopped, True again after reset_estop. No dispatch while estopped.
B. MacroPlayer invokes finish_fn exactly once per replay (completion,
   executor exceptions, or abort via stop_check) and clears the
   module-level _current_play_thread slot.
C. MouseController output is bounded: scroll clamped to ±MAX_SCROLL_TICKS,
   drag press/release state machine is idempotent, headless init reports
   itself unavailable.
D. KeyboardActions._press_combo releases every key that DID go down when a
   later press fails (a modifier can never stick) and releases in reverse
   order on success.
E. Risk hierarchy: DESTRUCTIVE refused by policy by default; per-action
   rate limit backstop.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

import airmouse.macros as macros_mod
from airmouse.gesture_spine import GestureActionRouter
from airmouse.keyboard import KeyboardActions
from airmouse.macros import MacroPlayer, MacroRecorder
from airmouse.mouse_controller import MAX_SCROLL_TICKS, MouseController


# ── helpers ───────────────────────────────────────────────────────────────────

class FakeMouse:
    """Records every OS-level call the controller would make."""

    def __init__(self):
        self.presses = []
        self.releases = []
        self.scrolls = []
        self.left_clicks = 0

    def press(self, button):
        self.presses.append(button)

    def release(self, button):
        self.releases.append(button)

    def scroll(self, dx, amount):
        self.scrolls.append((dx, amount))

    def left_click(self):
        self.left_clicks += 1


FAKE_BUTTONS = SimpleNamespace(left="left", right="right", middle="middle")


class FakeKeyboard:
    def __init__(self):
        self.presses = []
        self.releases = []

    def press(self, key):
        self.presses.append(key)

    def release(self, key):
        self.releases.append(key)


class FlakyKeyboard(FakeKeyboard):
    """press() raises after ``ok_presses`` successful presses."""

    def __init__(self, ok_presses=1):
        super().__init__()
        self.ok_presses = ok_presses
        self.attempts = []

    def press(self, key):
        self.attempts.append(key)  # attempted, maybe not successful
        if len(self.presses) >= self.ok_presses:
            raise RuntimeError(f"simulated press failure on {key!r}")
        self.presses.append(key)


def _wait_until(predicate, timeout=2.0, interval=0.005):
    """Bounded wait for a daemon-thread side effect. True when satisfied."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture(autouse=True)
def _clean_macro_slot():
    """Never let a replay-thread slot leak between tests."""
    macros_mod._current_play_thread = None
    yield
    macros_mod._current_play_thread = None


def _controller_with_fake_mouse():
    """MouseController forced onto a fake backend (headless: real one is None)."""
    mc = MouseController()
    mc.mouse = FakeMouse()
    mc._button = FAKE_BUTTONS
    return mc


# ── A. Spine E-STOP release contract ─────────────────────────────────────────

def test_trip_estop_calls_release_fn_once_per_trip():
    calls = []
    router = GestureActionRouter(estop_release_fn=lambda: calls.append(1))
    router.trip_estop("gesture panic")
    assert calls == [1]  # exactly one release for the first trip
    router.trip_estop("still panicking")
    assert calls == [1, 1]  # and one on EVERY trip (never leaves a latch)


def test_trip_estop_swallows_raising_release_fn():
    def boom():
        raise RuntimeError("release backend exploded")

    router = GestureActionRouter(estop_release_fn=boom)
    router.trip_estop("bad release fn")  # must not propagate
    assert router.estopped is True
    assert router.estop_reason() == "bad release fn"


def test_trip_estop_with_release_fn_none_works():
    router = GestureActionRouter(estop_release_fn=None)
    router.trip_estop("no callback configured")  # no error
    assert router.estopped is True
    assert router.estop_reason() == "no callback configured"


def test_gate_continuous_false_while_estopped_true_after_reset():
    router = GestureActionRouter()
    assert router.gate_continuous() is True  # open before any trip
    router.trip_estop("freeze cursor")
    assert router.gate_continuous() is False  # continuous control gated
    assert router.reset_estop() is True  # True: an estop was actually cleared
    assert router.gate_continuous() is True
    assert router.reset_estop() is False  # nothing left to reset
    assert router.estopped is False


def test_dispatch_left_click_refused_while_estopped():
    mouse = FakeMouse()
    router = GestureActionRouter(mouse=mouse)
    router.trip_estop("hand lost")
    res = router.dispatch("left_click")
    assert res["executed"] is False
    assert res["reason"].startswith("estop")
    assert res["risk"] == "SAFE"
    assert mouse.left_clicks == 0
    # same dispatch succeeds once the estop is explicitly cleared
    router.reset_estop()
    res2 = router.dispatch("left_click", now=1000.0)
    assert res2["executed"] is True
    assert mouse.left_clicks == 1


def test_dispatch_return_dict_shape():
    mouse = FakeMouse()
    router = GestureActionRouter(mouse=mouse)
    ok = router.dispatch("left_click", now=10.0)
    assert ok == {"executed": True, "reason": "ok", "risk": "SAFE"}
    router.trip_estop("shape check")
    refused = router.dispatch("left_click", now=10.5)
    assert set(refused.keys()) == {"executed", "reason", "risk"}


# ── B. MacroPlayer finish hook ───────────────────────────────────────────────

def _player_with_events(events, executor_calls, finish_calls):
    player = MacroPlayer(
        executor=lambda event, params: executor_calls.append((event, params)),
        finish_fn=lambda: finish_calls.append(1),
    )
    player._macro = {"name": "t", "events": events}
    return player


def test_macro_finish_fn_called_once_on_normal_completion():
    executor_calls, finish_calls = [], []
    player = _player_with_events(
        [{"t": 0.0, "event": "click"},
         {"t": 0.0, "event": "scroll", "amount": 3}],
        executor_calls, finish_calls)
    assert player.play() is True
    assert [ev for ev, _ in executor_calls] == ["click", "scroll"]
    assert finish_calls == [1]  # exactly once


def test_macro_finish_fn_called_when_executor_raises_for_every_event():
    executor_calls, finish_calls = [], []

    def bad_executor(event, params):
        executor_calls.append(event)
        raise RuntimeError("executor always fails")

    player = MacroPlayer(executor=bad_executor,
                         finish_fn=lambda: finish_calls.append(1))
    player._macro = {"events": [{"t": 0.0, "event": "click"},
                                {"t": 0.0, "event": "click"}]}
    assert player.play() is True  # one bad action never kills the replay
    assert executor_calls == ["click", "click"]
    assert finish_calls == [1]


def test_macro_finish_fn_called_on_abort_via_stop_check():
    executor_calls, finish_calls = [], []
    player = _player_with_events(
        [{"t": 0.0, "event": "click"},
         {"t": 0.5, "event": "click"}],
        executor_calls, finish_calls)
    # stop flag flips after the first event executes
    assert player.play(stop_check=lambda: len(executor_calls) >= 1) is False
    assert len(executor_calls) == 1  # aborted before the second event
    assert finish_calls == [1]


def test_current_play_thread_cleared_after_run():
    seen_during_run = []
    player = MacroPlayer(
        executor=lambda event, params: seen_during_run.append(
            macros_mod._current_play_thread is threading.current_thread()))
    player._macro = {"events": [{"t": 0.0, "event": "click"}]}
    assert player.play() is True
    assert seen_during_run == [True]  # slot pointed at this replay while live
    assert macros_mod._current_play_thread is None
    assert macros_mod.is_playing() is False


def test_macro_roundtrip_recorder_save_load_finish_hook(tmp_path, monkeypatch):
    monkeypatch.setattr(macros_mod, "MACRO_DIR", str(tmp_path))
    rec = MacroRecorder()
    assert rec.start("estop_contract_t") is True
    rec.record("click")
    rec.record("wait", seconds=0.0)
    rec.stop()
    assert rec.save() != ""
    executor_calls, finish_calls = [], []
    player = MacroPlayer(
        executor=lambda event, params: executor_calls.append(event),
        finish_fn=lambda: finish_calls.append(1))
    player.load("estop_contract_t")
    assert player.play() is True
    assert executor_calls == ["click", "wait"]
    assert finish_calls == [1]


# ── C. MouseController bounded output ────────────────────────────────────────

def test_scroll_clamped_to_max_scroll_ticks():
    assert MAX_SCROLL_TICKS == 100
    mc = _controller_with_fake_mouse()
    mc.scroll(1000)
    mc.scroll(-1000)
    mc.scroll(5)
    assert mc.mouse.scrolls == [(0, 100), (0, -100), (0, 5)]


def test_scroll_noop_when_backend_missing():
    mc = MouseController()
    if mc.mouse is not None:  # only meaningful on a None-backed (headless) box
        pytest.skip("pynput backend loaded; nothing to prove")
    mc.scroll(99999)  # must be a silent no-op, never raise
    assert mc.available is False


def test_start_drag_presses_once_and_is_idempotent():
    mc = _controller_with_fake_mouse()
    mc.start_drag()
    mc.start_drag()  # double start while dragging
    assert mc.mouse.presses == ["left"]
    assert mc.is_dragging is True
    assert mc.mouse.releases == []


def test_stop_drag_releases_once_and_only_when_dragging():
    mc = _controller_with_fake_mouse()
    mc.stop_drag()  # stop when not dragging → no release
    assert mc.mouse.releases == []
    mc.start_drag()
    mc.stop_drag()
    mc.stop_drag()  # double stop
    assert mc.mouse.releases == ["left"]
    assert mc.is_dragging is False


def test_headless_backend_unavailable_and_init_error_reported():
    mc = MouseController()
    try:
        import pynput  # noqa: F401
        has_backend = mc.mouse is not None
    except Exception:
        has_backend = False
    if has_backend:
        pytest.skip("pynput backend loaded; headless contract not applicable")
    assert mc.available is False
    assert isinstance(mc.init_error, str) and mc.init_error
    assert mc.mouse is None and mc._button is None


def test_available_property_tracks_backend_presence():
    mc = MouseController()
    assert mc.available is (mc.mouse is not None)
    mc.mouse = FakeMouse()
    assert mc.available is True  # property follows the backend, not a flag


# ── D. Keyboard partial-press rollback ───────────────────────────────────────

def test_partial_press_releases_keys_that_did_go_down():
    kb = KeyboardActions()
    flaky = FlakyKeyboard(ok_presses=1)  # 2nd press raises
    kb._keyboard = flaky
    saved_hook = threading.excepthook
    threading.excepthook = lambda args: None  # the rollback raises by design
    try:
        kb._press_combo(["a", "b", "c"])
        done = _wait_until(lambda: len(flaky.releases) >= 1)
    finally:
        threading.excepthook = saved_hook
    assert done, "rollback release never ran within 2s"
    assert flaky.attempts == ["a", "b"]  # press #2 attempted, #3 never tried
    assert flaky.presses == ["a"]  # only #1 actually went down
    assert flaky.releases == ["a"]  # ...and it was released (nothing sticks)


def test_full_press_releases_in_reverse_order():
    kb = KeyboardActions()
    good = FakeKeyboard()
    kb._keyboard = good
    kb._press_combo(["a", "b", "c"])
    assert _wait_until(lambda: len(good.releases) >= 3), \
        "combo release never completed within 2s"
    assert good.presses == ["a", "b", "c"]
    assert good.releases == ["c", "b", "a"]


# ── E. Risk hierarchy + rate limit ───────────────────────────────────────────

def test_destructive_classified_and_refused_by_policy():
    router = GestureActionRouter()  # allow_destructive defaults to False
    kb_calls = []
    router.kb_getter = lambda: SimpleNamespace(
        close_window=lambda: kb_calls.append(1))
    assert router.classify("close_window") == "DESTRUCTIVE"
    assert router.classify("left_click") == "SAFE"
    # confidence above the unreachable DESTRUCTIVE floor → policy is the refusal
    res = router.dispatch("close_window", confidence=1.2)
    assert res["executed"] is False
    assert res["risk"] == "DESTRUCTIVE"
    assert "destructive_action_blocked_by_policy" in res["reason"]
    assert "policy" in res["reason"].lower()
    assert kb_calls == []  # nothing executed
    # default confidence is also refused (1.0 < 1.1 floor)
    res2 = router.dispatch("close_window")
    assert res2["executed"] is False
    assert kb_calls == []


def test_rate_limit_blocks_rapid_repeat_dispatch():
    mouse = FakeMouse()
    router = GestureActionRouter(mouse=mouse, min_action_interval_s=0.12)
    first = router.dispatch("left_click", now=1000.0)
    assert first["executed"] is True and first["reason"] == "ok"
    second = router.dispatch("left_click", now=1000.0 + 0.05)
    assert second["executed"] is False
    assert second["reason"] == "rate_limit"
    assert mouse.left_clicks == 1  # only the first click went through
    later = router.dispatch("left_click", now=1000.0 + 0.13)
    assert later["executed"] is True
    assert mouse.left_clicks == 2
