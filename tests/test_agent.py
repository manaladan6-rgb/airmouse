"""Tests for airmouse.agent (v9 InteractionAgent orchestrator).

THE golden-path E2E suite: every subsystem real except the executor
(MockExecutor) and the screen providers (fake), everything headless and
deterministic (all clocks injected via ``now=``).
"""
from __future__ import annotations

from airmouse.interfaces import (
    ActionStatus,
    AppContext,
    GazeState,
    IntentType,
    MacroOp,
    MacroProgram,
    MacroStep,
    Modality,
    ScreenTarget,
    ScreenTargetType,
    TelemetryStats,
)
from airmouse.agent import AgentConfig, InteractionAgent, Telemetry
from airmouse.actions import MockExecutor
from airmouse import macros as macros_mod

BUTTON = ScreenTarget(id="btn", type=ScreenTargetType.BUTTON,
                      bbox=(900, 500, 120, 50), text="Submit button",
                      confidence=0.9, actionable=True)

WINDOW = ScreenTarget(id="win", type=ScreenTargetType.WINDOW,
                      bbox=(0, 0, 1920, 1040), text="Firefox — start page",
                      confidence=0.8, actionable=True)


class FakeProvider:
    """ScreenProvider protocol double with canned targets."""

    name = "fake"

    def __init__(self, targets=None):
        self.targets = list(targets if targets is not None else [BUTTON])

    def update(self, now=None):
        return list(self.targets)


class FlakyExecutor(MockExecutor):
    """MockExecutor whose click fails (raises) for the first N dispatches."""

    def __init__(self, fail_first=2):
        super().__init__()
        self.fail_first = int(fail_first)
        self.click_calls = 0

    def _do(self, name, *args):
        if name == "click":
            self.click_calls += 1
            if self.click_calls <= self.fail_first:
                self.record.append((name, args))
                raise RuntimeError("flaky click")
        return super()._do(name, *args)


class FakeRunner:
    def __init__(self):
        self.ran = []

    def run(self, program, stop_check=None):
        self.ran.append(program)
        return {"steps_total": 1, "steps_executed": 1, "verified": 0,
                "failed": 0, "aborted": False, "reason": "", "duration": 0.0}


def gaze(conf=0.9, dwell=False, events=None):
    return GazeState(x=0.5, y=0.5, screen_x=960, screen_y=525,
                     screen_valid=False, confidence=conf, dwell_fired=dwell,
                     events=list(events or []))


def make_agent(targets=None, executor=None, config=None, **overrides):
    overrides.setdefault("screen_providers", [FakeProvider(targets)])
    overrides.setdefault("executor", executor if executor is not None
                         else MockExecutor())
    overrides.setdefault("context_resolver",
                         lambda title: AppContext.BROWSER)
    return InteractionAgent(config, **overrides)


def pinch_hand(point=(960, 540), conf=0.9):
    return {"gesture": "pinch", "point": point, "confidence": conf}


# ---------------------------------------------------------------------------
# Golden path E2E
# ---------------------------------------------------------------------------

def test_golden_path_gaze_plus_pinch_click():
    agent = make_agent()
    out = agent.process_frame(hand_data=pinch_hand(), now=1.0, gaze_state=gaze())
    reports = out["reports"]
    assert len(reports) == 1 and reports[0].ok
    assert reports[0].plan.action.value == "click"
    # executed against the injected MockExecutor at the target centre
    assert ("click", (960.0, 525.0)) in agent.action_engine.executor.record
    # screen model carries the semantic target
    hit = out["screen_model"].target_at(960, 540)
    assert hit is not None and hit.id == "btn"
    # context resolved through the injected resolver
    assert out["context"] is AppContext.BROWSER
    # the click intent fused gaze + hand
    intent = out["intents"][0]
    assert intent.type is IntentType.CLICK
    assert intent.sources & Modality.GAZE and intent.sources & Modality.HAND


def test_golden_path_gaze_plus_voice_click_that():
    agent = make_agent()
    out = agent.process_frame(utterance="click that", now=1.0, gaze_state=gaze())
    assert out["reports"] and out["reports"][0].ok
    assert out["intents"][0].type is IntentType.CLICK
    assert out["nlu"] is not None and out["nlu"].target_ref == "that"


def test_screen_model_and_decision_target():
    agent = make_agent()
    out = agent.process_frame(now=1.0, gaze_state=gaze())
    assert out["decision"].target is not None
    assert out["decision"].target.id == "btn"


# ---------------------------------------------------------------------------
# Safety flows
# ---------------------------------------------------------------------------

def test_close_window_sensitive_needs_confirmation_then_executes():
    agent = make_agent(targets=[WINDOW])
    out1 = agent.process_frame(utterance="close this window", now=1.0,
                               gaze_state=gaze())
    close_intents = [i for i in out1["intents"] if i.type is IntentType.CLOSE]
    assert close_intents and close_intents[0].requires_confirmation is True
    assert out1["reports"][0].status is ActionStatus.BLOCKED
    assert out1["reports"][0].message == "needs_confirmation"
    assert agent.safety.pending_confirmation is not None
    # frame 2: "confirm" → the pending close re-runs and executes
    out2 = agent.process_frame(utterance="confirm", now=2.0, gaze_state=gaze())
    statuses = [r.status for r in out2["reports"]]
    assert ActionStatus.SUCCESS in statuses
    close_ok = [r for r in out2["reports"]
                if r.ok and r.plan is not None
                and r.plan.action.value == "hotkey"]
    assert close_ok
    rec = agent.action_engine.executor.record
    assert any(name == "hotkey" for name, _ in rec)


def test_uncertain_gaze_never_auto_clicks():
    agent = make_agent()
    out = agent.process_frame(hand_data=pinch_hand(), now=1.0,
                              gaze_state=gaze(conf=0.3))
    assert out["reports"], "a blocked report must be produced"
    assert all(r.status is ActionStatus.BLOCKED for r in out["reports"])
    assert any(r.message == "low_gaze_confidence" for r in out["reports"])
    assert agent.action_engine.executor.record == []   # nothing executed
    assert agent.telemetry.counters["actions_blocked"] >= 1


def test_estop_latch_blocks_then_reset_restores():
    agent = make_agent()
    agent.trip_estop("test")
    out = agent.process_frame(hand_data=pinch_hand(), now=1.0, gaze_state=gaze())
    assert all(r.status is ActionStatus.BLOCKED for r in out["reports"])
    agent.reset_estop()
    out2 = agent.process_frame(hand_data=pinch_hand(), now=3.0, gaze_state=gaze())
    assert out2["reports"] and out2["reports"][0].ok


def test_stop_everything_via_voice_latches_estop():
    agent = make_agent()
    out = agent.process_frame(utterance="stop everything", now=1.0,
                              gaze_state=gaze())
    assert out["reports"][0].ok
    assert agent.telemetry.counters["estops"] >= 1
    out2 = agent.process_frame(hand_data=pinch_hand(), now=2.0, gaze_state=gaze())
    assert all(r.status is ActionStatus.BLOCKED for r in out2["reports"])


def test_safe_mode_blocks_clicks_allows_scroll():
    agent = make_agent()
    agent.set_safety_level("safe")
    out = agent.process_frame(utterance="scroll down", now=1.0, gaze_state=gaze())
    assert out["reports"] and out["reports"][0].ok
    out2 = agent.process_frame(utterance="click that", now=2.0, gaze_state=gaze())
    assert all(r.status is ActionStatus.BLOCKED for r in out2["reports"])


# ---------------------------------------------------------------------------
# Failure recovery E2E
# ---------------------------------------------------------------------------

def test_failed_click_recovers_and_succeeds():
    executor = FlakyExecutor(fail_first=2)   # both attempts of call 1 fail
    agent = make_agent(executor=executor)
    out = agent.process_frame(hand_data=pinch_hand(), now=1.0, gaze_state=gaze())
    reports = out["reports"]
    assert len(reports) >= 2
    assert reports[0].status is ActionStatus.FAILED
    assert reports[0].attempts == 2          # engine retried internally
    recovered = [r for r in reports[1:] if r.ok]
    assert recovered, "the recovery attempt must succeed"
    assert recovered[0].recovery.value in ("retry", "retry_adj")
    assert agent.telemetry.counters["recoveries"] >= 1
    assert agent.telemetry.counters["actions_failed"] >= 1
    assert agent.telemetry.counters["actions_success"] >= 1


# ---------------------------------------------------------------------------
# NL command plumbing through the full pipeline
# ---------------------------------------------------------------------------

def test_scroll_command_reaches_executor():
    agent = make_agent()
    out = agent.process_frame(utterance="scroll down a lot", now=1.0,
                              gaze_state=gaze())
    assert out["reports"][0].ok
    assert ("scroll", (-8,)) in agent.action_engine.executor.record


def test_move_command_uses_gaze_target():
    agent = make_agent()
    out = agent.process_frame(utterance="move this to the left", now=1.0,
                              gaze_state=gaze())
    assert out["reports"][0].ok
    assert ("move", (960.0, 525.0)) in agent.action_engine.executor.record


def test_legacy_fallback_still_drives_actions():
    agent = make_agent()
    out = agent.process_frame(utterance="please zoom in now", now=1.0,
                              gaze_state=gaze())
    assert out["nlu"].fallback_command == "zoom_in"
    assert out["reports"][0].ok
    assert ("zoom", (3,)) in agent.action_engine.executor.record


def test_cancel_produces_cancelled_report_without_execution():
    agent = make_agent()
    out = agent.process_frame(utterance="cancel", now=1.0, gaze_state=gaze())
    assert out["reports"][0].status is ActionStatus.CANCELLED
    assert agent.action_engine.executor.record == []


def test_nl_dedup_within_agent_frames():
    agent = make_agent()
    agent.process_frame(utterance="click that", now=1.0, gaze_state=gaze())
    out2 = agent.process_frame(utterance="click that", now=1.4, gaze_state=gaze())
    assert out2["nlu"] is None
    assert len(agent.action_engine.executor.record) == 1


def test_agent_command_debounce_across_phrasings():
    agent = make_agent()
    agent.process_frame(utterance="click that", now=1.0, gaze_state=gaze())
    agent.process_frame(utterance="click it", now=1.3, gaze_state=gaze())
    assert len(agent.action_engine.executor.record) == 1


def test_hand_without_gaze_is_blocked():
    agent = make_agent()
    out = agent.process_frame(hand_data=pinch_hand(), now=1.0)
    assert all(r.status is ActionStatus.BLOCKED for r in out["reports"])
    assert agent.action_engine.executor.record == []


def test_idle_tick_headless():
    agent = make_agent()
    out = agent.process_frame(now=0.0)
    assert "decision" in out and "reports" in out
    assert out["reports"] == []
    assert out["mode"] == "fusion"


# ---------------------------------------------------------------------------
# Hands-free mode through the agent
# ---------------------------------------------------------------------------

def test_hands_free_mode_dwell_path():
    agent = make_agent()
    assert agent.set_mode("hands_free") is True
    out = agent.process_frame(gaze_state=gaze(dwell=True), now=1.0)
    assert out["mode"] == "hands_free"
    assert out["reports"] and out["reports"][0].ok
    assert ("click", (960.0, 525.0)) in agent.action_engine.executor.record


def test_hands_free_mode_voice_command():
    agent = make_agent()
    agent.set_mode("hands_free")
    out = agent.process_frame(gaze_state=gaze(), utterance="click that",
                              now=1.0)
    assert out["reports"] and out["reports"][0].ok


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

def test_telemetry_numeric_after_frames():
    agent = make_agent()
    for i in range(3):
        agent.process_frame(now=1.0 + i, gaze_state=gaze())
    assert agent.telemetry.latency_fusion_ms >= 0.0
    assert isinstance(agent.telemetry.latency_fusion_ms, float)
    assert agent.telemetry.fps_gaze >= 0.0
    snap = agent.telemetry.snapshot()
    assert isinstance(snap, TelemetryStats)


def test_telemetry_counters_consistent_with_reports():
    agent = make_agent()
    agent.process_frame(hand_data=pinch_hand(), now=1.0, gaze_state=gaze())
    counters = agent.telemetry.counters
    assert counters["actions_total"] == 1
    assert counters["actions_success"] == 1
    assert counters["actions_failed"] == 0
    snap = agent.telemetry.snapshot()
    assert snap.actions_total == snap.actions_success + snap.actions_failed \
        + snap.actions_blocked


def test_telemetry_record_routes_metrics_and_counters():
    t = Telemetry(window=16)
    t.record("fps_gaze", 30.0)
    t.record("fps_gaze", 20.0)
    t.record("actions_success", 1)
    assert 20.0 <= t.fps_gaze <= 30.0
    assert t.counters["actions_success"] == 1
    t.reset()
    assert t.fps_gaze == 0.0 and t.counters["actions_success"] == 0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_same_inputs_same_outputs():
    def run():
        agent = make_agent()
        outs = []
        outs.append(agent.process_frame(now=1.0, gaze_state=gaze()))
        outs.append(agent.process_frame(hand_data=pinch_hand(), now=2.0,
                                        gaze_state=gaze()))
        outs.append(agent.process_frame(utterance="scroll down a lot",
                                        now=3.0, gaze_state=gaze()))
        return outs

    a, b = run(), run()
    sig_a = [(o["decision"].target.id if o["decision"].target else None,
              [(r.status.name, r.message) for r in o["reports"]],
              [i.type.value for i in o["intents"]]) for o in a]
    sig_b = [(o["decision"].target.id if o["decision"].target else None,
              [(r.status.name, r.message) for r in o["reports"]],
              [i.type.value for i in o["intents"]]) for o in b]
    assert sig_a == sig_b


# ---------------------------------------------------------------------------
# Macros passthroughs + lifecycle
# ---------------------------------------------------------------------------

def test_run_macro_program_uses_injected_runner(tmp_path):
    runner = FakeRunner()
    agent = make_agent(program_runner=runner)
    program = MacroProgram(name="p1", steps=[MacroStep(op=MacroOp.CLICK,
                                                       params={"point": [1, 2]})])
    path = tmp_path / "p1.json"
    assert macros_mod.save_program(program, str(path)) is True
    result = agent.run_macro_program(str(path))
    assert result["steps_executed"] == 1
    assert runner.ran and runner.ran[0].name == "p1"


def test_record_legacy_macro_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(macros_mod, "MACRO_DIR", str(tmp_path))
    agent = make_agent()
    assert agent.start_macro_recording("agent_macro") is True
    agent.record_legacy_macro("click", x=10, y=20)
    path = agent.stop_macro_recording()
    assert path and str(path).startswith(str(tmp_path))


def test_shutdown_idempotent():
    agent = make_agent()
    agent.shutdown()
    agent.shutdown()   # second call must not raise


def test_agent_config_from_mapping():
    cfg = AgentConfig.from_mapping({"mode": "hands_free", "safety_level":
                                    "careful", "unknown_key": 1})
    assert cfg.mode == "hands_free" and cfg.safety_level == "careful"


def test_config_dict_accepted_and_mode_switch_rate():
    agent = make_agent(config={"mode": "fusion", "command_debounce": 0.6})
    assert agent.config.mode == "fusion"
    assert agent.set_mode("gaze") is True
    assert agent.mode == "gaze"
