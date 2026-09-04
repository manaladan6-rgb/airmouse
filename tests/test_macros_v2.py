"""Tests for airmouse.macros v9.0.0 FORMAT V2 (+ v1 regression) — tmp_path IO."""
from __future__ import annotations

import json

import pytest

import airmouse.macros as macros_mod
from airmouse.actions import MockExecutor
from airmouse.interfaces import (
    MacroOp,
    MacroProgram,
    MacroStep,
    ScreenTarget,
    ScreenTargetType,
    VerificationResult,
    VerificationStatus,
)
from airmouse.macros import (
    ProgramRunner,
    load_program,
    save_program,
)

# v1 API must remain importable and fully functional.
from airmouse.macros import (  # noqa: F401
    MacroPlayer,
    MacroRecorder,
    delete_macro,
    is_playing,
    list_macros,
)


# ── helpers ────────────────────────────────────────────────────────────────────

class FlakyExecutor:
    """Executor whose click fails N times then succeeds (for RETRY tests)."""

    def __init__(self, fail_first=1):
        self.fail_first = fail_first
        self.clicks = 0

    def click(self, x, y):
        self.clicks += 1
        if self.clicks <= self.fail_first:
            raise RuntimeError("flaky")
        return True

    def type_text(self, text):
        return True

    def scroll(self, amount):
        return True

    def hotkey(self, keys):
        return True

    def double_click(self, x, y):
        return True

    def right_click(self, x, y):
        return True


class StubVerifier:
    """Verifier stub returning a fixed status and recording calls."""

    def __init__(self, status=VerificationStatus.PASSED):
        self.status = status
        self.calls = []

    def verify(self, plan, report, observe_fn):
        self.calls.append((plan, report, observe_fn))
        return VerificationResult(status=self.status)


class FakeScreenProvider:
    """find_by_text provider with optional hit delay + target."""

    def __init__(self, target=None, hit_after=0):
        self.target = target
        self.hit_after = hit_after
        self.polled = 0

    def find_by_text(self, text):
        self.polled += 1
        if self.target is not None and self.polled > self.hit_after:
            return self.target
        return None

    def model(self):
        class _M:
            targets = [self.target] if self.target is not None else []
        return _M()


class BlockSafety:
    def approve_intent(self, intent, now=None):
        from airmouse.interfaces import SafetyDecision
        return SafetyDecision(allowed=False, reason="macro_blocked")


def make_target(cx=120.0, cy=90.0, text="Submit"):
    return ScreenTarget(id="s1", type=ScreenTargetType.BUTTON,
                        bbox=(cx - 30, cy - 10, 60, 20), text=text,
                        confidence=0.95, actionable=True)


# ── v2 save/load ────────────────────────────────────────────────────────────────

def test_v2_roundtrip(tmp_path):
    program = MacroProgram(
        name="demo",
        steps=[
            MacroStep(op=MacroOp.LOOK_FOR, params={"text": "Submit"},
                      comment="find it"),
            MacroStep(op=MacroOp.CLICK, params={}, comment=""),
            MacroStep(op=MacroOp.WAIT_UNTIL, params={"seconds": 0.05}),
            MacroStep(op=MacroOp.IF, params={
                "then": [MacroStep(op=MacroOp.TYPE, params={"text": "yes"})],
                "else": [MacroStep(op=MacroOp.STOP, params={})],
            }),
        ],
        created="2024-01-01T00:00:00",
    )
    path = tmp_path / "demo.json"
    assert save_program(program, str(path)) is True
    loaded = load_program(str(path))
    assert loaded.version == 2
    assert loaded.name == "demo"
    assert [s.op for s in loaded.steps] == [
        MacroOp.LOOK_FOR, MacroOp.CLICK, MacroOp.WAIT_UNTIL, MacroOp.IF,
    ]
    assert loaded.steps[0].comment == "find it"
    then_steps = loaded.steps[3].params["then"]
    assert then_steps[0].op is MacroOp.TYPE  # nested branches decode


def test_save_program_sanitizes_filename(tmp_path):
    program = MacroProgram(name="x", steps=[MacroStep(op=MacroOp.STOP)])
    weird = tmp_path / "my macro/../we!ird@name.json"
    assert save_program(program, str(weird)) is True
    # sanitized stem landed next to it and loads back
    found = [p for p in tmp_path.rglob("*.json")]
    assert found and load_program(str(found[0])).steps[0].op is MacroOp.STOP


def test_v1_format_file_loads_as_legacy(tmp_path):
    v1 = {"name": "legacy", "created": "c", "duration": 1.0,
          "events": [{"t": 0.0, "event": "click", "x": 5, "y": 6}]}
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(v1), encoding="utf-8")
    prog = load_program(str(path))
    assert prog.version == 1
    assert prog.legacy_events[0]["event"] == "click"
    assert prog.steps == []


def test_load_program_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_program(str(tmp_path / "nope.json"))


def test_v1_roundtrip_via_save_program(tmp_path):
    legacy = MacroProgram(version=1, name="old",
                          legacy_events=[{"t": 0.0, "event": "scroll",
                                          "amount": 2}])
    path = tmp_path / "old.json"
    assert save_program(legacy, str(path)) is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["events"][0]["event"] == "scroll"  # events preserved
    reloaded = load_program(str(path))
    assert reloaded.version == 1
    assert reloaded.legacy_events[0]["amount"] == 2


# ── ProgramRunner: basic steps ───────────────────────────────────────────────────

def test_runner_executes_click_type_scroll_hotkey():
    ex = MockExecutor()
    runner = ProgramRunner(ex)
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.CLICK, params={"x": 10, "y": 20}),
        MacroStep(op=MacroOp.TYPE, params={"text": "hello"}),
        MacroStep(op=MacroOp.SCROLL, params={"amount": -2}),
        MacroStep(op=MacroOp.HOTKEY, params={"keys": ["ctrl", "s"]}),
    ])
    rep = runner.run(program)
    assert rep["steps_executed"] == 4
    assert rep["failed"] == 0
    assert rep["aborted"] is False
    assert ex.record == [
        ("click", (10.0, 20.0)),
        ("type_text", ("hello",)),
        ("scroll", (-2,)),
        ("hotkey", ("ctrl", "s")),
    ]


def test_runner_double_and_right_click():
    ex = MockExecutor()
    runner = ProgramRunner(ex)
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.DOUBLE_CLICK, params={"point": [30, 40]}),
        MacroStep(op=MacroOp.RIGHT_CLICK, params={"x": 1, "y": 2}),
    ])
    rep = runner.run(program)
    assert ("double_click", (30.0, 40.0)) in ex.record
    assert ("right_click", (1.0, 2.0)) in ex.record
    assert rep["failed"] == 0


def test_runner_click_without_target_fails():
    ex = MockExecutor()
    runner = ProgramRunner(ex)
    program = MacroProgram(steps=[MacroStep(op=MacroOp.CLICK, params={})])
    rep = runner.run(program)
    assert rep["failed"] == 1
    assert ex.record == []


def test_runner_empty_program():
    rep = ProgramRunner(MockExecutor()).run(MacroProgram(steps=[]))
    assert rep["steps_executed"] == 0 and rep["aborted"] is False


def test_runner_wait_until_seconds_and_fixation_passthrough():
    ex = MockExecutor()
    runner = ProgramRunner(ex, config={"poll_interval": 0.01})
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.WAIT_UNTIL, params={"fixation": True}),
        MacroStep(op=MacroOp.WAIT_UNTIL, params={"seconds": 0.03}),
        MacroStep(op=MacroOp.CLICK, params={"x": 1, "y": 1}),
    ])
    rep = runner.run(program)
    assert rep["steps_executed"] == 3 and rep["failed"] == 0
    assert rep["duration"] >= 0.03


# ── LOOK_FOR ─────────────────────────────────────────────────────────────────────

def test_look_for_finds_target_and_clicks_center():
    ex = MockExecutor()
    provider = FakeScreenProvider(target=make_target())
    runner = ProgramRunner(ex, screen_provider=provider)
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.LOOK_FOR, params={"text": "Submit"}),
        MacroStep(op=MacroOp.CLICK, params={}),
    ])
    rep = runner.run(program)
    assert rep["failed"] == 0
    assert ("click", (120.0, 90.0)) in ex.record  # target centre


def test_look_for_by_type():
    ex = MockExecutor()
    provider = FakeScreenProvider(target=make_target(text=""))
    runner = ProgramRunner(ex, screen_provider=provider)
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.LOOK_FOR, params={"type": "button"}),
        MacroStep(op=MacroOp.CLICK, params={}),
    ])
    rep = runner.run(program)
    assert rep["failed"] == 0
    assert ex.record and ex.record[0][0] == "click"


def test_look_for_timeout_counts_failed_and_continues():
    ex = MockExecutor()
    provider = FakeScreenProvider(target=None)
    runner = ProgramRunner(ex, screen_provider=provider,
                           config={"look_for_timeout": 0.1,
                                   "poll_interval": 0.02})
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.LOOK_FOR, params={"text": "ghost"}),
        MacroStep(op=MacroOp.TYPE, params={"text": "ok"}),
    ])
    rep = runner.run(program)
    assert rep["failed"] == 1
    assert rep["steps_executed"] == 2  # continued after the failed LOOK_FOR
    assert ("type_text", ("ok",)) in ex.record


# ── VERIFY + IF ────────────────────────────────────────────────────────────────

def test_verify_stub_passed_increments_verified_and_takes_then():
    ex = MockExecutor()
    verifier = StubVerifier(VerificationStatus.PASSED)
    runner = ProgramRunner(ex, verifier=verifier)
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.VERIFY, params={"expected": {"type": "key"}}),
        MacroStep(op=MacroOp.IF, params={
            "then": [MacroStep(op=MacroOp.TYPE, params={"text": "then"})],
            "else": [MacroStep(op=MacroOp.TYPE, params={"text": "else"})],
        }),
    ])
    rep = runner.run(program)
    assert rep["verified"] == 1 and rep["failed"] == 0
    assert ("type_text", ("then",)) in ex.record
    assert verifier.calls and verifier.calls[0][2] is None  # observe_fn=None


def test_verify_stub_failed_takes_else():
    ex = MockExecutor()
    runner = ProgramRunner(ex, verifier=StubVerifier(VerificationStatus.FAILED))
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.VERIFY, params={"expected": {"type": "key"}}),
        MacroStep(op=MacroOp.IF, params={
            "then": [MacroStep(op=MacroOp.TYPE, params={"text": "then"})],
            "else": [MacroStep(op=MacroOp.STOP, params={})],
        }),
    ])
    rep = runner.run(program)
    assert rep["verified"] == 0
    assert ("type_text", ("then",)) not in ex.record
    assert rep["aborted"] is True and rep["reason"] == "stop_op"


def test_verify_without_verifier_is_passthrough():
    ex = MockExecutor()
    runner = ProgramRunner(ex)
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.VERIFY, params={"expected": {"type": "key"}}),
        MacroStep(op=MacroOp.IF, params={
            "then": [MacroStep(op=MacroOp.TYPE, params={"text": "t"})],
            "else": [],
        }),
    ])
    rep = runner.run(program)
    assert rep["verified"] == 0  # not counted, but branch is deterministic
    assert ("type_text", ("t",)) in ex.record


# ── RETRY ───────────────────────────────────────────────────────────────────────

def test_retry_failing_then_success():
    ex = FlakyExecutor(fail_first=1)
    runner = ProgramRunner(ex)
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.CLICK, params={"x": 5, "y": 5}),
        MacroStep(op=MacroOp.RETRY, params={"times": 3}),
    ])
    rep = runner.run(program)
    assert ex.clicks == 2           # 1 fail + 1 successful retry
    assert rep["failed"] == 1       # only the original failure counted


def test_retry_exhausted_counts_failed():
    ex = FlakyExecutor(fail_first=99)
    runner = ProgramRunner(ex)
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.CLICK, params={"x": 5, "y": 5}),
        MacroStep(op=MacroOp.RETRY, params={"times": 2}),
    ])
    rep = runner.run(program)
    assert ex.clicks == 3  # original + 2 retries
    assert rep["failed"] == 2


# ── STOP / guards ───────────────────────────────────────────────────────────────

def test_stop_aborts_with_reason():
    ex = MockExecutor()
    runner = ProgramRunner(ex)
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.TYPE, params={"text": "a"}),
        MacroStep(op=MacroOp.STOP),
        MacroStep(op=MacroOp.TYPE, params={"text": "b"}),
    ])
    rep = runner.run(program)
    assert rep["aborted"] is True and rep["reason"] == "stop_op"
    assert ("type_text", ("b",)) not in ex.record


def test_max_steps_guard():
    ex = MockExecutor()
    runner = ProgramRunner(ex, config={"max_steps": 3})
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.TYPE, params={"text": str(i)}) for i in range(10)
    ])
    rep = runner.run(program)
    assert rep["aborted"] is True and rep["reason"] == "max_steps"
    assert rep["steps_executed"] == 3


def test_stop_check_mid_run_aborts():
    ex = MockExecutor()
    runner = ProgramRunner(ex)
    calls = {"n": 0}

    def stop_check():
        calls["n"] += 1
        return calls["n"] > 2  # abort after the first couple of steps

    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.TYPE, params={"text": "a"}),
        MacroStep(op=MacroOp.TYPE, params={"text": "b"}),
        MacroStep(op=MacroOp.TYPE, params={"text": "c"}),
        MacroStep(op=MacroOp.TYPE, params={"text": "d"}),
    ])
    rep = runner.run(program, stop_check=stop_check)
    assert rep["aborted"] is True and rep["reason"] == "stop_requested"
    assert rep["steps_executed"] < 4


def test_max_if_depth_guard():
    ex = MockExecutor()
    runner = ProgramRunner(ex, config={"max_if_depth": 2})
    deep = MacroStep(op=MacroOp.IF, params={"then": [
        MacroStep(op=MacroOp.IF, params={"then": [
            MacroStep(op=MacroOp.IF, params={"then": [   # depth 3 > cap 2
                MacroStep(op=MacroOp.TYPE, params={"text": "deep"}),
            ]}),
        ]}),
    ]})
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.VERIFY, params={"expected": {"type": "key"}}),
        deep,
    ])
    rep = runner.run(program)
    assert rep["aborted"] is True and rep["reason"] == "max_if_depth"


# ── safety gate ────────────────────────────────────────────────────────────────

def test_safety_blocked_click_fails_step():
    ex = MockExecutor()
    runner = ProgramRunner(ex, safety=BlockSafety())
    program = MacroProgram(steps=[
        MacroStep(op=MacroOp.CLICK, params={"x": 3, "y": 4}),
        MacroStep(op=MacroOp.TYPE, params={"text": "still runs"}),
    ])
    rep = runner.run(program)
    assert rep["failed"] == 1
    assert ex.record == [("type_text", ("still runs",))]


def test_safety_permitted_click_executes():
    from airmouse.interfaces import SafetyDecision
    ex = MockExecutor()

    class Permit:
        def approve_intent(self, intent, now=None):
            return SafetyDecision(allowed=True, reason="ok")

    runner = ProgramRunner(ex, safety=Permit())
    program = MacroProgram(steps=[MacroStep(op=MacroOp.CLICK,
                                            params={"x": 3, "y": 4})])
    rep = runner.run(program)
    assert rep["failed"] == 0
    assert ("click", (3.0, 4.0)) in ex.record


# ── run_legacy (v1 semantics) ────────────────────────────────────────────────────

def test_run_legacy_replays_events_and_timing():
    ex = MockExecutor()
    runner = ProgramRunner(ex)
    program = MacroProgram(version=1, legacy_events=[
        {"t": 0.0, "event": "move", "x": 100, "y": 50},
        {"t": 0.01, "event": "click"},
        {"t": 0.02, "event": "scroll", "amount": 3},
        {"t": 0.03, "event": "zoom", "ticks": 2},
        {"t": 0.04, "event": "right_click", "x": 9, "y": 9},
    ])
    rep = runner.run_legacy(program, speed=50.0)  # tiny waits
    names = [n for n, _ in ex.record]
    assert names == ["move", "click", "scroll", "scroll", "right_click"]
    assert ex.record[1] == ("click", (100.0, 50.0))  # last move point
    assert ex.record[3] == ("scroll", (2,))          # zoom → plain scroll
    assert ex.record[4] == ("right_click", (9.0, 9.0))
    assert rep["steps_executed"] == 5 and rep["aborted"] is False


def test_run_legacy_click_without_move_uses_center():
    ex = MockExecutor(screen_w=640, screen_h=480)
    runner = ProgramRunner(ex)
    program = MacroProgram(version=1, legacy_events=[
        {"t": 0.0, "event": "click"},
    ])
    runner.run_legacy(program, speed=100.0)
    assert ("click", (320.0, 240.0)) in ex.record


def test_run_legacy_drag_and_wait():
    ex = MockExecutor()
    runner = ProgramRunner(ex, config={"poll_interval": 0.01})
    program = MacroProgram(version=1, legacy_events=[
        {"t": 0.0, "event": "drag_start", "x": 1, "y": 2},
        {"t": 0.01, "event": "drag_stop", "x": 11, "y": 22},
        {"t": 0.02, "event": "wait", "seconds": 0.02},
    ])
    rep = runner.run_legacy(program, speed=100.0)
    assert ("drag", (1.0, 2.0, 11.0, 22.0, 0.4)) in ex.record
    assert rep["steps_executed"] == 3


def test_run_legacy_stop_check_aborts():
    ex = MockExecutor()
    runner = ProgramRunner(ex)
    program = MacroProgram(version=1, legacy_events=[
        {"t": 0.0, "event": "click", "x": 1, "y": 1},
        {"t": 0.5, "event": "click", "x": 2, "y": 2},
    ])
    rep = runner.run_legacy(program, speed=1.0,
                            stop_check=lambda: True)  # immediate abort
    assert rep["aborted"] is True and rep["reason"] == "stop_requested"
    assert rep["steps_executed"] == 0


def test_run_method_dispatches_legacy_program():
    ex = MockExecutor()
    runner = ProgramRunner(ex)
    program = MacroProgram(version=1, legacy_events=[
        {"t": 0.0, "event": "click", "x": 4, "y": 4},
    ])
    rep = runner.run(program, stop_check=None)  # single entry point
    assert rep["steps_executed"] == 1
    assert ("click", (4.0, 4.0)) in ex.record


def test_legacy_executor_exception_contained():
    ex = MockExecutor()
    ex.fail_for.add("click")
    runner = ProgramRunner(ex)
    program = MacroProgram(version=1, legacy_events=[
        {"t": 0.0, "event": "click", "x": 1, "y": 1},
        {"t": 0.0, "event": "move", "x": 2, "y": 2},
    ])
    rep = runner.run_legacy(program, speed=100.0)
    assert rep["failed"] == 1
    assert rep["steps_executed"] == 2  # kept going


# ── v1 regression: recorder/player untouched ─────────────────────────────────────

def test_v1_recorder_roundtrip_with_tmp_macro_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(macros_mod, "MACRO_DIR", str(tmp_path))
    rec = MacroRecorder()
    assert rec.start("v1test") is True
    rec.record("click", x=5, y=6)
    rec.record("scroll", amount=2)
    events = rec.stop()
    assert len(events) == 2 and "t" in events[0]
    path = rec.save()
    assert path and path.endswith("v1test.json")
    assert "v1test" in list_macros()

    seen = []
    player = MacroPlayer(lambda event, params: seen.append((event, params)))
    data = player.load("v1test")
    assert data["events"][0]["event"] == "click"
    assert player.play(speed=100.0) is True
    assert ("click", {"x": 5, "y": 6}) in seen
    assert ("scroll", {"amount": 2}) in seen
    assert is_playing() is False
    assert delete_macro("v1test") is True
    assert "v1test" not in list_macros()


def test_v1_player_missing_macro_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(macros_mod, "MACRO_DIR", str(tmp_path))
    player = MacroPlayer(lambda event, params: None)
    with pytest.raises(FileNotFoundError):
        player.load("does_not_exist")
