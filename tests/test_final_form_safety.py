"""tests/test_final_form_safety.py — v19.0.3 R3b2 safety-bypass closures.

Locks the FINAL-FORM safety contracts with deterministic fakes only
(no Chrome, no real input, no network beyond the loopback ephemeral
bridge server):

A. browser voice side-door + BrowserController.execute consult the
   central safety gate ("browser_action"): estopped/shutdown → honest
   refusal, NOTHING dispatched to the bridge; unknown command names
   default-deny; malformed/oversized payloads rejected.
B. browser_bridge EXACT origin allowlist (TT-04/SAF-2): the historical
   substring bypass ("https://trusted.example.attacker.io" matching
   "https://trusted.example") is closed; different port / scheme /
   evil suffix / query-smuggling are refused; loopback binding alone
   is containment, never authorization.
C. AIP agent execution routes ("agent_action", confirmation-required
   by default): unclassified actions fail-closed denied; the real-mode
   engine (--aip-real) runs through GateGuardedEngine so each execute
   is preceded by authorize() and refused pre-dispatch.
D. the agent intent route refuses while estopped / shutting down;
   control intents (the safety release channels) stay reachable.
E. estop_hotkey arm_estop_hotkeys(): honest availability (no faked
   listener), trip/reset callbacks with the documented hotkey reason,
   once-per-fresh-latch behaviour, bounded idempotent stop, no thread
   join anywhere in the module.
"""
from __future__ import annotations

import inspect
import json
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from airmouse import aip as aip_mod
from airmouse import estop_hotkey as eh
from airmouse.agent import InteractionAgent
from airmouse.actions import MockExecutor
from airmouse.agent_sdk import AipEndpoint
from airmouse.browser import (
    BrowserController,
    BrowserElement,
    BrowserResolution,
    SemanticBrowserResolver,
    SimulatedBrowserBridge,
    authorize_browser_action,
    authorize_browser_voice_command,
)
from airmouse.browser_bridge import BrowserBridgeServer, normalize_origin
from airmouse.interfaces import AppContext, Intent, IntentType, Modality
from airmouse.safety_gate import reset_gate_for_tests


# ─────────────────────────────────────────────────────────────────────────────
# fixtures / fakes
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def gate():
    """A FRESH process-wide gate per test (never leaks an E-STOP)."""
    g = reset_gate_for_tests()
    yield g
    reset_gate_for_tests()


class FakeProvider:
    """ScreenProvider protocol double (canned semantic target)."""

    name = "fake"

    def __init__(self, targets=None):
        from airmouse.interfaces import ScreenTarget, ScreenTargetType
        self.targets = list(targets if targets is not None else [
            ScreenTarget(id="btn", type=ScreenTargetType.BUTTON,
                         bbox=(900, 500, 120, 50), text="Submit button",
                         confidence=0.9, actionable=True)])

    def update(self, now=None):
        return list(self.targets)


class RecordingEngine:
    """Duck-typed real-mode engine: records payloads, returns a dict."""

    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {
            "ok": True, "message": "stub executed"}

    def execute(self, payload):
        self.calls.append(payload)
        return self.result


class AllowAll:
    """Duck-typed permission gate in the SDK's string style."""

    def check(self, agent, key):
        return "allow"


def _click_resolution():
    el = BrowserElement(id="btn-login", role="button", text="Login",
                        tag="button", bbox=(0.05, 0.16, 0.12, 0.06),
                        actionable=True)
    return BrowserResolution(matched=True, action="click", element=el,
                             params={}, confidence=1.0,
                             text="click the login button")


def _controller(bridge=None):
    bridge = bridge if bridge is not None else SimulatedBrowserBridge()
    ctrl = BrowserController(config={"enabled": True, "poll_interval": 0.0},
                             bridge=bridge)
    assert ctrl.start() is True
    ctrl.poll(now=1.0)
    return ctrl, bridge


def _post(server, origin=None, data=b'{"title": "t"}', expect_error=False):
    """POST to the ephemeral bridge; returns (status_code, body_dict)."""
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(server.url, data=data, headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return resp.code, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _allowed_server(origins):
    srv = BrowserBridgeServer(port=0, config={"allowed_origins": origins})
    assert srv.start() is True
    time.sleep(0.05)
    return srv


# ─────────────────────────────────────────────────────────────────────────────
# A. browser voice side-door + BrowserController gate route
# ─────────────────────────────────────────────────────────────────────────────


def test_browser_voice_side_door_refuses_while_estopped(gate):
    """The __main__ side-door path (resolve → execute) dispatches NOTHING
    while the E-STOP is latched."""
    ctrl, bridge = _controller()
    resolver = SemanticBrowserResolver(ctrl.mapper)
    gate.trip_estop("panic")
    # the side-door resolution itself refuses (unmatched while estopped)
    res = resolver.resolve("click the login button")
    assert res.matched is False and not res.action
    # and even a pre-resolved action is refused before any dispatch
    out = ctrl.execute(_click_resolution(), now=2.0)
    assert out["status"] == "refused"
    assert "estop" in out["message"]
    assert bridge.action_history == []          # nothing reached the bridge


def test_browser_controller_execute_refuses_while_estopped(gate):
    ctrl, bridge = _controller()
    gate.trip_estop("emergency")
    out = ctrl.execute(_click_resolution(), now=1.0)
    assert out["status"] == "refused"
    assert "refused by safety gate" in out["message"]
    assert "estop" in out["message"]
    assert bridge.action_history == []
    # honest refusal survives repeated calls (latch is sticky)
    out2 = ctrl.execute(_click_resolution(), now=1.1)
    assert out2["status"] == "refused"
    assert bridge.action_history == []


def test_browser_controller_execute_passes_when_gate_clean(gate):
    ctrl, bridge = _controller()
    assert authorize_browser_action("click") == (True, "ok")
    out = ctrl.execute(_click_resolution(), now=1.0)
    assert out["status"] == "executed"
    assert bridge.action_history and bridge.action_history[0]["type"] == "click"


def test_unknown_browser_command_default_denied(gate):
    ctrl, bridge = _controller()
    res = BrowserResolution(matched=True, action="format_hard_drive",
                            element=None, params={}, confidence=1.0,
                            text="format the hard drive")
    out = ctrl.execute(res, now=1.0)
    assert out["status"] == "refused"
    assert "unknown browser action" in out["message"]
    assert bridge.action_history == []


def test_oversized_browser_payload_rejected(gate):
    ctrl, bridge = _controller()
    res = BrowserResolution(matched=True, action="type", element=None,
                            params={"text": "a" * (64 * 1024 + 1)},
                            confidence=1.0, text="type ...")
    out = ctrl.execute(res, now=1.0)
    assert out["status"] == "refused"
    assert "payload_too_large" in out["message"]
    assert bridge.action_history == []


def test_malformed_browser_params_rejected(gate):
    ctrl, bridge = _controller()
    res = BrowserResolution(matched=True, action="type", element=None,
                            params="not-a-dict", confidence=1.0, text="type")
    out = ctrl.execute(res, now=1.0)
    assert out["status"] == "refused"
    assert "malformed payload" in out["message"]
    assert bridge.action_history == []


def test_oversized_voice_utterance_rejected(gate):
    ctrl, _bridge = _controller()
    resolver = SemanticBrowserResolver(ctrl.mapper)
    res = resolver.resolve("click the login button " + "x" * (64 * 1024 + 8))
    assert res.matched is False and not res.action
    ok, reason = authorize_browser_voice_command("x" * (64 * 1024 + 8))
    assert ok is False and "payload_too_large" in reason


# ─────────────────────────────────────────────────────────────────────────────
# B. browser_bridge EXACT origin allowlist (TT-04 / SAF-2)
# ─────────────────────────────────────────────────────────────────────────────


def test_origin_exact_allowlist_accepts_exact_origin():
    srv = _allowed_server(["https://trusted.example"])
    try:
        # case / default port / trailing slash all normalize to the SAME
        # origin — the implicit and explicit forms are equal, exactly once
        code, body = _post(srv, origin="https://TRUSTED.example:443/")
        assert code == 200 and body.get("ok") is True
    finally:
        srv.stop()


def test_origin_rejects_evil_suffix_bypass_tt04():
    """THE TT-04 regression: a substring match allowed the attacker origin
    `https://trusted.example.attacker.io` to pass as `trusted.example`."""
    srv = _allowed_server(["https://trusted.example"])
    try:
        code, _ = _post(srv, origin="https://trusted.example.attacker.io")
        assert code == 403
        # query-string marker smuggling (the other substring bypass)
        code2, _ = _post(srv, origin="https://evil.example/?x=trusted.example")
        assert code2 == 403
    finally:
        srv.stop()


def test_origin_rejects_different_port():
    srv = _allowed_server(["https://trusted.example"])
    try:
        code, _ = _post(srv, origin="https://trusted.example:8443")
        assert code == 403
    finally:
        srv.stop()


def test_origin_rejects_different_scheme():
    srv = _allowed_server(["https://trusted.example"])
    try:
        code, _ = _post(srv, origin="http://trusted.example")
        assert code == 403
    finally:
        srv.stop()


def test_loopback_origin_not_authorized_by_binding():
    """Loopback != authorization: the bridge is BOUND to 127.0.0.1 yet a
    loopback web origin is refused until it is allowlisted EXACTLY."""
    srv = _allowed_server([])          # empty allowlist, loopback binding
    try:
        code, _ = _post(srv, origin="http://127.0.0.1:9999")
        assert code == 403
        code2, _ = _post(srv, origin="http://localhost:9999")
        assert code2 == 403
    finally:
        srv.stop()
    # ...and once the exact origin is configured, and ONLY that one, it passes
    srv2 = _allowed_server(["http://127.0.0.1:9999"])
    try:
        code, body = _post(srv2, origin="http://127.0.0.1:9999")
        assert code == 200 and body.get("ok") is True
        code3, _ = _post(srv2, origin="http://localhost:9999")
        assert code3 == 403            # localhost ≠ 127.0.0.1 (exact match)
    finally:
        srv2.stop()


def test_bridge_malformed_json_rejected_honestly():
    srv = _allowed_server([])
    try:
        code, body = _post(srv, data=b"{definitely-not-json")
        assert code == 400 and body.get("error") == "invalid json"
        # unknown command path stays refused
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/nope",
            headers={"Host": f"127.0.0.1:{srv.port}"})
        try:
            urllib.request.urlopen(req, timeout=2.0)
            assert False, "unknown path must be refused"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        srv.stop()


def test_normalize_origin_exact_normalization():
    assert normalize_origin("https://trusted.example") == \
        "https://trusted.example"
    assert normalize_origin("https://trusted.example:443") == \
        "https://trusted.example"          # default port strips
    assert normalize_origin("https://trusted.example:8443") == \
        "https://trusted.example:8443"     # non-default port kept
    assert normalize_origin("http://trusted.example") == \
        "http://trusted.example"           # scheme is part of the identity
    assert normalize_origin("http://LOCALHOST:9000") == "http://localhost:9000"
    assert normalize_origin("chrome-extension://abcdef") == \
        "chrome-extension://abcdef"
    assert normalize_origin("https://host.example/path") == ""   # malformed
    assert normalize_origin("https://host.example/?q=1") == ""   # malformed
    assert normalize_origin("not a url") == ""
    assert normalize_origin(None) == ""


# ─────────────────────────────────────────────────────────────────────────────
# C. AIP agent execution routes ("agent_action")
# ─────────────────────────────────────────────────────────────────────────────


def test_aip_unclassified_action_denied(gate):
    ok, reason = aip_mod.authorize_agent_execution("format_the_internet")
    assert ok is False
    assert "unclassified_action" in reason
    # an empty/absent action name is equally unclassified
    ok2, reason2 = aip_mod.authorize_agent_execution("")
    assert ok2 is False and "unclassified_action" in reason2


def test_agent_action_without_confirmation_refused(gate):
    """agent_action is confirmation-required by default: absent
    confirmation → refused with the reason, engine never involved."""
    ok, reason = aip_mod.authorize_agent_execution("click", confirmed=False)
    assert ok is False
    assert "confirmation_required" in reason


def test_agent_action_with_confirmation_passes_when_gate_clean(gate):
    ok, reason = aip_mod.authorize_agent_execution("click", confirmed=True)
    assert (ok, reason) == (True, "ok")


def test_estopped_aip_refuses_pre_dispatch(gate):
    engine = RecordingEngine()
    guarded = aip_mod.gate_guarded_engine(engine)
    gate.trip_estop("halt")
    result = guarded.execute({"action": "click", "params": {}})
    assert engine.calls == []                  # NOTHING dispatched
    assert result["ok"] is False and result["refused"] is True
    assert "estop" in result["message"]
    assert guarded.refusals == 1 and guarded.executes == 0


def test_aip_real_engine_called_only_when_authorized(gate):
    """--aip-real wiring: the guarded engine is what the endpoint holds;
    a clean gate executes, a latched gate refuses before the engine."""
    engine = RecordingEngine()
    endpoint = AipEndpoint(permission_engine=AllowAll(),
                           action_engine=aip_mod.wire_real_mode_engine(engine),
                           label="real")
    req = json.dumps({"aip_version": "1.0", "type": "execute",
                      "id": "m1", "agent_id": "tester", "request_id": "",
                      "ts": 0.0,
                      "payload": {"action": "click", "verify": True,
                                  "params": {}}})
    out = endpoint.handle(aip_mod.parse_message(req)[0])
    assert out.type == "result"
    assert out.payload["simulated"] is False
    assert engine.calls and engine.calls[0]["action"] == "click"

    gate.trip_estop("halt everything")
    out2 = endpoint.handle(aip_mod.parse_message(req)[0])
    assert len(engine.calls) == 1              # NOT called again
    assert out2.payload["verification"]["verified"] is False
    assert "safety gate" in out2.payload["verification"]["message"]


def test_wire_real_mode_engine_idempotent_and_transparent():
    engine = RecordingEngine()
    once = aip_mod.wire_real_mode_engine(engine)
    twice = aip_mod.wire_real_mode_engine(once)
    assert twice is once and isinstance(once, aip_mod.GateGuardedEngine)
    # delegated attribute lookup with a default (AipEndpoint's verify-hook
    # probe) passes through the wrapper and honestly yields None
    assert getattr(once, "verify_action", None) is None
    # delegated attributes reach the inner engine
    assert once._engine is engine


# ─────────────────────────────────────────────────────────────────────────────
# D. agent intent route vs the central gate
# ─────────────────────────────────────────────────────────────────────────────


def _gaze(conf=0.9):
    from airmouse.interfaces import GazeState
    return GazeState(x=0.5, y=0.5, screen_x=960, screen_y=525,
                     screen_valid=False, confidence=conf, dwell_fired=False,
                     events=[])


def _agent():
    overrides = {
        "screen_providers": [FakeProvider()],
        "executor": MockExecutor(),
        "context_resolver": lambda title: AppContext.BROWSER,
    }
    return InteractionAgent({"mode": "fusion"}, **overrides)


def _pinch():
    return {"gesture": "pinch", "point": (960, 540), "confidence": 0.9}


def test_agent_intent_route_refuses_while_estopped(gate):
    agent = _agent()
    gate.trip_estop("global panic")
    out = agent.process_frame(hand_data=_pinch(), now=1.0,
                              gaze_state=_gaze())
    assert out["reports"], "expected at least one report"
    report = out["reports"][0]
    assert report.status.name == "BLOCKED"
    assert report.message.startswith("refused_by_central_gate:")
    assert "estop" in report.message
    # nothing reached the executor
    assert agent.action_engine.executor.record == []


def test_agent_intent_route_passes_when_gate_clean(gate):
    agent = _agent()
    out = agent.process_frame(hand_data=_pinch(), now=1.0,
                              gaze_state=_gaze())
    assert out["reports"][0].ok is True
    assert ("click", (960.0, 525.0)) in agent.action_engine.executor.record


def test_agent_intent_route_refuses_during_shutdown(gate):
    agent = _agent()
    gate.mark_shutdown("teardown")
    out = agent.process_frame(hand_data=_pinch(), now=1.0,
                              gaze_state=_gaze())
    report = out["reports"][0]
    assert report.status.name == "BLOCKED"
    assert "shutdown" in report.message
    assert agent.action_engine.executor.record == []


def test_control_intents_stay_reachable_while_estopped(gate):
    """The safety release channels (CANCEL etc.) are never blocked by the
    central gate — an E-STOP must never disable its own reset paths."""
    agent = _agent()
    gate.trip_estop("global panic")
    agent.inject_intent(Intent(type=IntentType.CANCEL, confidence=1.0,
                               sources=Modality.VOICE, utterance="cancel",
                               timestamp=1.0))
    out = agent.process_frame(now=1.0, gaze_state=_gaze())
    assert out["reports"], "control intent must still be processed"
    assert out["reports"][0].status.name == "CANCELLED"


def test_dispatch_refuses_during_shutdown_browser_and_agent(gate):
    """mark_shutdown refuses NEW actions for BOTH gated classes."""
    gate.mark_shutdown("session shutdown")
    assert gate.shutting_down is True
    ctrl, bridge = _controller()
    out = ctrl.execute(_click_resolution(), now=1.0)
    assert out["status"] == "refused" and "shutdown" in out["message"]
    assert bridge.action_history == []
    ok, reason = aip_mod.authorize_agent_execution("click", confirmed=True)
    assert ok is False and "shutdown" in reason
    engine = RecordingEngine()
    guarded = aip_mod.gate_guarded_engine(engine)
    result = guarded.execute({"action": "click", "params": {}})
    assert engine.calls == [] and result["ok"] is False


# ─────────────────────────────────────────────────────────────────────────────
# E. estop_hotkey — honest global hotkeys
# ─────────────────────────────────────────────────────────────────────────────


class FakeListener:
    """Deterministic pynput.keyboard.Listener stand-in (records itself)."""

    instances = []

    def __init__(self, **kwargs):
        self.on_press = kwargs.get("on_press")
        self.on_release = kwargs.get("on_release")
        self.started = False
        self.stop_count = 0
        FakeListener.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stop_count += 1
        self.started = False

    def is_alive(self):
        return self.started


def _capture_listener():
    """The most recently constructed fake listener (the armed one)."""
    return FakeListener.instances[-1]


class FakeDyingListener(FakeListener):
    def is_alive(self):
        return False                     # dies immediately after start


class FakeBrokenStartListener(FakeListener):
    def start(self):
        raise RuntimeError("no display available")


def fake_keyboard(listener_cls=FakeListener):
    from types import SimpleNamespace as NS
    key = NS(ctrl=object(), ctrl_l=object(), ctrl_r=object(),
             alt=object(), alt_l=object(), alt_r=object(),
             alt_gr=object())
    return NS(Listener=listener_cls, Key=key), key


def _armed(listener_cls=FakeListener):
    kb, key = fake_keyboard(listener_cls)
    gate = reset_gate_for_tests()
    status = eh.arm_estop_hotkeys(gate, keyboard_module=kb,
                                  start_grace=0.05)
    return status, gate, key


def test_arm_reports_honest_unavailable_when_pynput_missing(gate, monkeypatch):
    def _boom():
        raise ImportError("simulated headless box")
    monkeypatch.setattr(eh, "_load_pynput", _boom)
    status = eh.arm_estop_hotkeys(gate)
    assert status["available"] is False
    assert "pynput unavailable" in status["unavailable_reason"]
    assert "simulated headless" in status["unavailable_reason"]
    assert callable(status["stop"])
    status["stop"]()                        # no-op stop must be safe


def test_arm_real_environment_status_truthful(gate):
    """No fake: whatever THIS machine can do must be reported truthfully
    (headless sandbox → unavailable with a real reason; a desktop with a
    keyboard service → available with a live listener)."""
    status = eh.arm_estop_hotkeys(gate)
    try:
        assert isinstance(status["available"], bool)
        if status["available"]:
            assert status["unavailable_reason"] == ""
            assert callable(status["stop"])
        else:
            assert isinstance(status["unavailable_reason"], str)
            assert status["unavailable_reason"] != ""
    finally:
        status["stop"]()


def test_trip_hotkey_trips_gate_with_hotkey_reason():
    status, gate, key = _armed()
    assert status["available"] is True
    try:
        listener = _capture_listener()
        # press ctrl+alt+e
        listener.on_press(key.ctrl)
        listener.on_press(key.alt)
        listener.on_press(SimpleNamespace(char="e"))
        assert gate.estopped is True
        assert gate.estop_reason() == "global hotkey Ctrl+Alt+E"
        # the Ctrl+letter control-code encoding trips too
        gate.reset_estop()
        listener.on_press(SimpleNamespace(char="\x05"))
        assert gate.estopped is True
        assert gate.estop_reason() == "global hotkey Ctrl+Alt+E"
    finally:
        status["stop"]()
        reset_gate_for_tests()


def test_reset_hotkey_clears_gate():
    status, gate, key = _armed()
    try:
        listener = _capture_listener()
        listener.on_press(key.ctrl)
        listener.on_press(key.alt)
        listener.on_press(SimpleNamespace(char="e"))
        assert gate.estopped is True
        listener.on_press(SimpleNamespace(char="r"))
        assert gate.estopped is False
        # a fresh latch after reset keeps a fresh trip count
        listener.on_press(SimpleNamespace(char="e"))
        assert gate.estopped is True
        assert gate.trip_count == 2
    finally:
        status["stop"]()
        reset_gate_for_tests()


def test_trip_fires_once_per_fresh_latch():
    status, gate, key = _armed()
    try:
        fired = []
        gate.on_trip(lambda reason: fired.append(reason))
        listener = _capture_listener()
        listener.on_press(key.ctrl)
        listener.on_press(key.alt)
        listener.on_press(SimpleNamespace(char="e"))
        listener.on_press(SimpleNamespace(char="e"))   # latch is idempotent
        assert gate.trip_count == 1
        assert fired == ["global hotkey Ctrl+Alt+E"]   # exactly once
        listener.on_press(SimpleNamespace(char="r"))
        listener.on_press(SimpleNamespace(char="e"))   # FRESH latch
        assert gate.trip_count == 2
        assert len(fired) == 2
    finally:
        status["stop"]()
        reset_gate_for_tests()


def test_hotkey_requires_ctrl_and_alt():
    status, gate, key = _armed()
    try:
        listener = _capture_listener()
        listener.on_press(SimpleNamespace(char="e"))          # no modifiers
        assert gate.estopped is False
        listener.on_press(key.ctrl)
        listener.on_press(SimpleNamespace(char="e"))          # ctrl only
        assert gate.estopped is False
        listener.on_release(key.ctrl)
        listener.on_press(key.alt)
        listener.on_press(SimpleNamespace(char="e"))          # alt only
        assert gate.estopped is False
        assert gate.trip_count == 0
    finally:
        status["stop"]()
        reset_gate_for_tests()


def test_stop_bounded_and_idempotent():
    status, gate, key = _armed()
    listener = _capture_listener()
    t0 = time.perf_counter()
    status["stop"]()
    status["stop"]()                       # idempotent
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0                   # bounded, never hangs
    assert listener.stop_count == 1        # the underlying stop ran once
    fresh = status["status"]()
    assert fresh["available"] is False     # honest post-stop status
    assert fresh["listener_alive"] is None
    reset_gate_for_tests()


def test_no_thread_join_anywhere_structural():
    """The hotkey module NEVER joins any thread (structural)."""
    src = inspect.getsource(eh)
    assert ".join(" not in src
    assert "join(" not in src
    # the bounded-stop contract is documented, not faked
    assert "NEVER joins the listener thread" in src


def test_listener_start_failure_reports_honestly(gate):
    status, _gate, _key = _armed(FakeBrokenStartListener)
    assert status["available"] is False
    assert "listener start failed" in status["unavailable_reason"]
    assert "no display available" in status["unavailable_reason"]
    status["stop"]()                        # must be safe to call


def test_listener_death_reports_honestly(gate):
    status, _gate, _key = _armed(FakeDyingListener)
    assert status["available"] is False
    assert "died immediately" in status["unavailable_reason"]


def test_listener_exception_swallowed_but_reported(gate):
    class ExplodingGate:
        def trip_estop(self, reason):
            raise RuntimeError("gate exploded")

        def reset_estop(self):
            return False

    kb, key = fake_keyboard()
    status = eh.arm_estop_hotkeys(ExplodingGate(), keyboard_module=kb,
                                  start_grace=0.05)
    try:
        listener = _capture_listener()
        listener.on_press(key.ctrl)
        listener.on_press(key.alt)
        listener.on_press(SimpleNamespace(char="e"))   # gate raises
        fresh = status["status"]()
        assert fresh["errors"], "swallowed error must be reported"
        assert "trip_estop failed" in fresh["errors"][-1]
        # the listener itself is still armed (the channel never dies)
        listener.on_press(SimpleNamespace(char="r"))
    finally:
        status["stop"]()


def test_hotkey_status_contract_keys():
    status, _gate, _key = _armed()
    try:
        for key_name in ("available", "unavailable_reason", "stop",
                         "trip_hotkey", "reset_hotkey", "trip_reason",
                         "errors", "status"):
            assert key_name in status, f"missing status key: {key_name}"
        assert status["trip_hotkey"] == "Ctrl+Alt+E"
        assert status["reset_hotkey"] == "Ctrl+Alt+R"
        assert status["trip_reason"] == "global hotkey Ctrl+Alt+E"
        assert callable(status["stop"]) and callable(status["status"])
        assert isinstance(status["errors"], list)
        fresh = status["status"]()
        assert fresh["available"] is True and \
            fresh["listener_alive"] is True
    finally:
        status["stop"]()
        reset_gate_for_tests()
