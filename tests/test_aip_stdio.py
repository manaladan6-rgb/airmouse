"""AirMouse v15.1.1 tests — the AIP stdio wire server (§9/§11), real
AipEndpoint execution (§10/§13), ALLOW_ONCE use-coercion (§15) and
§12 budget enforcement.

These tests prove agent-core's ``stdio://`` transport and the
agent-sdk-js StdioTransport now have a real wire server to talk to
(``airmouse --aip-stdio`` / ``python -m airmouse.aip_stdio``).
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from airmouse import aip_stdio
from airmouse.actions import ActionEngine, MockExecutor
from airmouse.agent_sdk import AipEndpoint
from airmouse.agents import AgentRegistry, AgentState
from airmouse.permissions import (AgentPermissionEngine, Decision,
                                  PermissionRule)

_repo = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────


def _req(mtype: str, payload=None, mid: str = "m1", agent: str = "tester",
         version: str = "1.0") -> str:
    """One AIP request envelope as JSON text (agent-core wire shape)."""
    return json.dumps({
        "aip_version": version, "type": mtype, "id": mid,
        "agent_id": agent, "request_id": "", "ts": 0.0,
        "payload": payload if payload is not None else {},
    })


def _serve(lines, endpoint, **kw):
    """Run serve() over StringIO pairs; returns (rc, out_text)."""
    inp = io.StringIO("".join(line + "\n" for line in lines))
    out = io.StringIO()
    rc = aip_stdio.serve(endpoint=endpoint, in_stream=inp,
                         out_stream=out, **kw)
    return rc, out.getvalue()


def _replies(out_text: str):
    return [json.loads(line) for line in out_text.splitlines()
            if line.strip()]


class AllowAll:
    """Duck-typed gate in the SDK's string style."""

    def check(self, agent, key):
        return "allow"


class RecordingEngine:
    """Duck-typed action engine: records payloads, returns a dict."""

    def __init__(self, result=None):
        self.calls = []
        if result is None:
            result = {"ok": True, "message": "stub executed"}
        self.result = result

    def execute(self, payload):
        self.calls.append(payload)
        return self.result


def _allow_endpoint(**kw) -> AipEndpoint:
    return AipEndpoint(permission_engine=AllowAll(), **kw)


# ─────────────────────────────────────────────────────────────────────────────
# serve() round-trips over StringIO pairs
# ─────────────────────────────────────────────────────────────────────────────


class TestServeRoundTrip:
    def test_discover_round_trip(self):
        rc, out = _serve([_req("discover", mid="m1")], AipEndpoint())
        assert rc == 0
        replies = _replies(out)
        assert len(replies) == 1
        reply = replies[0]
        assert reply["type"] == "capabilities"
        assert reply["aip_version"] == "1.0"
        assert reply["request_id"] == "m1"
        payload = reply["payload"]
        assert payload["protocol_version"] == "1.0"
        caps = payload["capabilities"]
        assert isinstance(caps, list) and caps
        names = [c["name"] for c in caps]
        assert names == sorted(names)                 # deterministic order
        click = next(c for c in caps if c["name"] == "click")
        assert click["permission"] == "mouse.click"

    def test_execute_with_stub_engine_is_real(self):
        engine = RecordingEngine()
        endpoint = _allow_endpoint(action_engine=engine, label="real")
        rc, out = _serve([_req("execute", {"action": "click",
                                           "verify": True, "params": {}})],
                         endpoint)
        assert rc == 0
        replies = _replies(out)
        assert len(replies) == 1
        assert replies[0]["type"] == "result"
        payload = replies[0]["payload"]
        assert payload["ok"] is True
        assert payload["simulated"] is False          # honest labeling
        verification = payload["verification"]
        assert verification["verified"] is True
        assert verification["simulated"] is False
        assert "stub executed" in verification["message"]
        assert len(engine.calls) == 1
        # duck-typed engines receive the documented dict payload
        sent = engine.calls[0]
        assert sent["type"] == "click" and sent["action"] == "click"
        assert isinstance(sent["params"], dict)
        assert isinstance(sent["target"], dict)

    def test_execute_without_engine_is_labelled_simulated(self):
        endpoint = _allow_endpoint()                  # no action engine
        rc, out = _serve([_req("execute", {"action": "click",
                                           "params": {}})], endpoint)
        replies = _replies(out)
        assert replies[0]["type"] == "result"
        payload = replies[0]["payload"]
        assert payload["ok"] is True
        assert payload["simulated"] is True           # honest labeling
        assert payload["verification"]["simulated"] is True
        assert payload["verification"]["verified"] is True
        assert payload["verification"]["message"] == \
            "simulated execution verified"

    def test_verify_returns_engine_result(self):
        engine = RecordingEngine(result={"ok": False,
                                         "message": "button not found"})
        endpoint = _allow_endpoint(action_engine=engine)
        rc, out = _serve([
            _req("execute", {"action": "click", "params": {}}, mid="m1"),
            _req("verify", {"action_id": "act-msg-00000001"}, mid="m2"),
        ], endpoint)
        replies = _replies(out)
        assert len(replies) == 2
        assert replies[0]["payload"]["verification"]["verified"] is False
        assert replies[0]["payload"]["verification"]["message"] == \
            "button not found"
        assert replies[1]["type"] == "verification"
        assert replies[1]["payload"]["verified"] is False
        assert replies[1]["payload"]["message"] == "button not found"

    def test_verify_unknown_action_still_false(self):
        rc, out = _serve([_req("verify", {"action_id": "nope"},
                               mid="m1")], AipEndpoint())
        replies = _replies(out)
        assert replies[0]["type"] == "verification"
        assert replies[0]["payload"]["verified"] is False
        assert replies[0]["payload"]["message"] == "unknown action"

    def test_malformed_line_gets_error_and_loop_continues(self):
        rc, out = _serve(["this is not json", _req("status", mid="m2")],
                         AipEndpoint())
        assert rc == 0
        replies = _replies(out)
        assert len(replies) == 2
        assert replies[0]["type"] == "error"
        assert replies[0]["payload"]["code"] == "bad_message"
        assert replies[1]["type"] == "status"         # loop continued

    def test_malformed_line_salvages_request_id(self):
        bad = json.dumps({"aip_version": "9.9", "type": "status",
                          "id": "req-42", "agent_id": "tester", "ts": 0,
                          "payload": {}})
        rc, out = _serve([bad], AipEndpoint())
        replies = _replies(out)
        assert replies[0]["type"] == "error"
        assert replies[0]["payload"]["request_id"] == "req-42"

    def test_oversized_line_rejected_and_loop_continues(self):
        oversized = '{"blob":"' + "x" * (300 * 1024) + '"}'
        rc, out = _serve([oversized, _req("status", mid="after")],
                         AipEndpoint())
        assert rc == 0
        replies = _replies(out)
        assert len(replies) == 2
        assert replies[0]["type"] == "error"
        assert "too large" in replies[0]["payload"]["message"]
        assert replies[1]["type"] == "status"         # framing re-synced

    def test_max_requests_bounds_loop(self):
        lines = [_req("status", mid=f"m{i}") for i in range(5)]
        rc, out = _serve(lines, AipEndpoint(), max_requests=3)
        assert rc == 0
        assert len(_replies(out)) == 3

    def test_zero_max_requests_runs_until_eof(self):
        lines = [_req("status", mid=f"m{i}") for i in range(4)]
        rc, out = _serve(lines, AipEndpoint(), max_requests=0)
        assert rc == 0
        assert len(_replies(out)) == 4

    def test_eof_ends_cleanly(self):
        rc, out = _serve([], AipEndpoint())
        assert rc == 0 and out == ""
        # EOF after some traffic also ends cleanly
        rc, out = _serve([_req("status"), _req("status")], AipEndpoint())
        assert rc == 0 and len(_replies(out)) == 2

    def test_blank_lines_are_tolerated(self):
        rc, out = _serve(["", "   ", _req("status", mid="m1")], AipEndpoint())
        assert rc == 0
        assert len(_replies(out)) == 1

    def test_unknown_type_gets_error_line(self):
        rc, out = _serve([_req("become_root", mid="m1")], AipEndpoint())
        replies = _replies(out)
        assert replies[0]["type"] == "error"
        assert replies[0]["payload"]["code"] == "bad_message"

    def test_stdout_carries_only_response_lines(self):
        """NO banners: every byte on out_stream is a JSON reply line."""
        lines = ["garbage!", _req("discover", mid="m1"),
                 '{"blob":"' + "y" * (270 * 1024) + '"}',
                 _req("status", mid="m2")]
        rc, out = _serve(lines, AipEndpoint())
        assert rc == 0
        parsed = [json.loads(line) for line in out.splitlines()]
        assert len(parsed) == 4
        assert [r["type"] for r in parsed] == \
            ["error", "capabilities", "error", "status"]
        for reply in parsed:
            assert reply["aip_version"] == "1.0"
            assert isinstance(reply["payload"], dict)

    def test_handler_exception_never_crashes_loop(self):
        class Boom:
            def handle(self, msg):
                raise RuntimeError("boom")

        rc, out = _serve([_req("status", mid="m1"),
                          _req("status", mid="m2")], Boom())
        assert rc == 0
        replies = _replies(out)
        assert len(replies) == 2
        assert all(r["type"] == "error" and r["payload"]["code"] == "failed"
                   for r in replies)

    def test_serve_builds_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("AIRMOUSE_AIP_SIMULATOR", "1")
        monkeypatch.delenv("AIRMOUSE_AIP_REAL", raising=False)
        monkeypatch.delenv("AIRMOUSE_AIP_ENGINE", raising=False)
        inp = io.StringIO(_req("status", mid="m1") + "\n")
        out = io.StringIO()
        rc = aip_stdio.serve(in_stream=inp, out_stream=out)
        assert rc == 0
        replies = _replies(out.getvalue())
        assert replies[0]["type"] == "status"
        assert replies[0]["payload"]["action_engine"] is False
        assert replies[0]["payload"]["mode"] == "simulated"


# ─────────────────────────────────────────────────────────────────────────────
# real execution through the core ActionEngine
# ─────────────────────────────────────────────────────────────────────────────


class TestRealExecution:
    def test_real_click_verified_with_observation(self):
        engine = ActionEngine(executor=MockExecutor())
        endpoint = _allow_endpoint(action_engine=engine, label="real")
        rc, out = _serve([_req("execute", {
            "action": "click", "verify": True, "params": {},
            "target": {"kind": "coordinate", "value": "btn",
                       "point": [50, 60]},
        }, mid="m1")], endpoint)
        replies = _replies(out)
        payload = replies[0]["payload"]
        assert replies[0]["type"] == "result"
        assert payload["ok"] is True
        assert payload["simulated"] is False
        assert payload["verification"]["verified"] is True
        assert "[observed: pointer]" in payload["verification"]["message"]

    def test_real_failure_is_verified_false_not_raised(self):
        engine = ActionEngine(executor=MockExecutor())
        endpoint = _allow_endpoint(action_engine=engine)
        # click with no point -> precondition failure (missing_point)
        rc, out = _serve([_req("execute", {"action": "click",
                                           "params": {}}, mid="m1")],
                         endpoint)
        replies = _replies(out)
        payload = replies[0]["payload"]
        assert replies[0]["type"] == "result"
        assert payload["ok"] is True                  # exchange succeeded…
        assert payload["simulated"] is False
        assert payload["verification"]["verified"] is False
        assert payload["verification"]["message"] == "missing_point"
        assert payload["detail"] == "engine execution failed"

    def test_engine_crash_becomes_engine_error(self):
        class CrashingEngine:
            def execute(self, payload):
                raise RuntimeError("boom")

        endpoint = _allow_endpoint(action_engine=CrashingEngine())
        rc, out = _serve([_req("execute", {"action": "click",
                                           "params": {}}, mid="m1")],
                         endpoint)
        replies = _replies(out)
        payload = replies[0]["payload"]
        assert replies[0]["type"] == "result"         # did NOT raise out
        assert payload["verification"]["verified"] is False
        assert "engine_error" in payload["verification"]["message"]
        assert "boom" in payload["verification"]["message"]

    def test_engine_verify_step_is_honoured(self):
        class DoubtfulEngine:
            def __init__(self):
                self.calls = []

            def execute(self, payload):
                self.calls.append(payload)
                return {"ok": True, "message": "moved"}

            def verify_action(self, result):
                return {"verified": False}

        engine = DoubtfulEngine()
        endpoint = _allow_endpoint(action_engine=engine)
        rc, out = _serve([_req("execute", {"action": "click", "params": {},
                                           "target": {
                                               "kind": "coordinate",
                                               "value": "x",
                                               "point": [1, 2]}},
                               mid="m1")], endpoint)
        payload = _replies(out)[0]["payload"]
        assert payload["verification"]["verified"] is False
        assert len(engine.calls) == 1

    def test_task_engine_bookkeeping_still_runs(self):
        from airmouse.tasks import TaskEngine
        te = TaskEngine()
        endpoint = _allow_endpoint(task_engine=te,
                                   action_engine=RecordingEngine())
        rc, out = _serve([_req("execute", {"action": "click",
                                           "params": {}}, mid="m1")],
                         endpoint)
        assert _replies(out)[0]["type"] == "result"
        assert len(te._tasks) == 1                    # task recorded


# ─────────────────────────────────────────────────────────────────────────────
# permission gate interplay (§9/§14/§15)
# ─────────────────────────────────────────────────────────────────────────────


class TestPermissionGateOnWire:
    def _deny_endpoint(self, engine=None) -> AipEndpoint:
        p = AgentPermissionEngine()
        p.grant("agent-x", "mouse.click", Decision.DENY)
        return AipEndpoint(permission_engine=p, action_engine=engine)

    def test_deny_rule_denies_execute_with_real_engine_present(self):
        engine = RecordingEngine()
        endpoint = self._deny_endpoint(engine=engine)
        rc, out = _serve([_req("execute", {"action": "click", "params": {}},
                               mid="m1", agent="agent-x")], endpoint)
        replies = _replies(out)
        assert replies[0]["type"] == "error"
        assert replies[0]["payload"]["code"] == "permission_denied"
        assert engine.calls == []                     # engine NEVER touched

    def test_deny_rule_denies_execute_without_engine(self):
        endpoint = self._deny_endpoint(engine=None)
        rc, out = _serve([_req("execute", {"action": "click", "params": {}},
                               mid="m1", agent="agent-x")], endpoint)
        assert _replies(out)[0]["payload"]["code"] == "permission_denied"

    def test_allow_rule_executes_real(self):
        p = AgentPermissionEngine()
        p.grant("agent-x", "mouse.click", Decision.ALLOW)
        engine = RecordingEngine()
        endpoint = AipEndpoint(permission_engine=p, action_engine=engine)
        rc, out = _serve([_req("execute", {"action": "click", "params": {}},
                               mid="m1", agent="agent-x")], endpoint)
        reply = _replies(out)[0]
        assert reply["type"] == "result"
        assert reply["payload"]["verification"]["verified"] is True
        assert reply["payload"]["simulated"] is False

    def test_estop_denies_every_execute(self):
        p = AgentPermissionEngine()
        p.grant("*", "mouse.click", Decision.ALLOW)
        endpoint = AipEndpoint(permission_engine=p,
                               action_engine=RecordingEngine())
        rc, out = _serve([_req("execute", {"action": "click", "params": {}},
                               mid="m1", agent="anyone")], endpoint)
        assert _replies(out)[0]["type"] == "result"   # allowed pre-estop
        p.emergency_stop(True)
        rc, out = _serve([_req("execute", {"action": "click", "params": {}},
                               mid="m2", agent="anyone")], endpoint)
        reply = _replies(out)[0]
        assert reply["type"] == "error"
        assert reply["payload"]["code"] == "permission_denied"
        assert "emergency stop" in reply["payload"]["message"]

    def test_no_gate_fails_closed(self):
        endpoint = AipEndpoint(action_engine=RecordingEngine())
        rc, out = _serve([_req("execute", {"action": "click", "params": {}},
                               mid="m1", agent="someone")], endpoint)
        assert _replies(out)[0]["payload"]["code"] == "permission_denied"

    def test_request_reports_normalized_decision(self):
        p = AgentPermissionEngine()
        p.grant("agent-x", "mouse.click", Decision.DENY)
        endpoint = AipEndpoint(permission_engine=p)
        rc, out = _serve([_req("request", {"permission": "mouse.click"},
                               mid="m1", agent="agent-x")], endpoint)
        reply = _replies(out)[0]
        assert reply["type"] == "permission"
        assert reply["payload"]["decision"] == "deny"
        assert reply["payload"]["allowed"] is False


# ─────────────────────────────────────────────────────────────────────────────
# §15 fix — ALLOW_ONCE can never be unlimited
# ─────────────────────────────────────────────────────────────────────────────


class TestAllowOnceCoercion:
    def test_default_allow_once_is_single_use(self):
        p = AgentPermissionEngine()
        assert p.grant("a", "mouse.click", Decision.ALLOW_ONCE) is True
        rule = p._rules[0]
        assert rule.remaining_uses == 1               # was -1 before fix
        assert p.check("a", "mouse.click").allowed is True
        assert p.check("a", "mouse.click").allowed is False

    def test_explicit_uses_five_stays_five(self):
        p = AgentPermissionEngine()
        p.grant("a", "mouse.click", Decision.ALLOW_ONCE, uses=5)
        assert p._rules[0].remaining_uses == 5
        allowed = sum(p.check("a", "mouse.click").allowed
                      for _ in range(7))
        assert allowed == 5

    def test_zero_and_negative_uses_coerced_to_one(self):
        p = AgentPermissionEngine()
        p.grant("a", "mouse.click", Decision.ALLOW_ONCE, uses=0)
        p.grant("b", "mouse.click", Decision.ALLOW_ONCE, uses=-7)
        assert p._rules[0].remaining_uses == 1
        assert p._rules[1].remaining_uses == 1
        assert p.check("a", "mouse.click").allowed is True
        assert p.check("b", "mouse.click").allowed is True

    def test_allow_and_allow_session_stay_unlimited(self):
        p = AgentPermissionEngine()
        p.grant("a", "mouse.click", Decision.ALLOW)
        p.grant("a", "type.text", Decision.ALLOW_SESSION)
        assert p._rules[0].remaining_uses == -1       # unchanged semantics
        assert all(p.check("a", "mouse.click").allowed
                   for _ in range(100))
        assert all(p.check("a", "type.text").allowed
                   for _ in range(100))

    def test_direct_rule_construction_is_coerced_too(self):
        rule = PermissionRule(decision=Decision.ALLOW_ONCE,
                              remaining_uses=-1)
        assert rule.remaining_uses == 1
        ok_rule = PermissionRule(decision=Decision.ALLOW_ONCE,
                                 remaining_uses=3)
        assert ok_rule.remaining_uses == 3
        free_rule = PermissionRule(decision=Decision.ALLOW,
                                   remaining_uses=-1)
        assert free_rule.remaining_uses == -1         # others unchanged


# ─────────────────────────────────────────────────────────────────────────────
# §12 fix — AgentProfile.budgets are enforced
# ─────────────────────────────────────────────────────────────────────────────


class TestBudgetEnforcement:
    def _registry(self, budgets=None) -> AgentRegistry:
        reg = AgentRegistry()
        reg.permissions.grant("*", "mouse.click", Decision.ALLOW)
        assert reg.register("bot", budgets=budgets) is True
        reg.set_state("bot", AgentState.ACTIVE)
        return reg

    def test_small_budget_grants_then_exhausts(self):
        reg = self._registry(budgets={"max_actions": 2})
        assert reg.authorize_action("bot", "mouse", "mouse.click") == \
            (True, "authorized")
        assert reg.authorize_action("bot", "mouse", "mouse.click")[0] is True
        ok, why = reg.authorize_action("bot", "mouse", "mouse.click")
        assert ok is False
        assert "budget_exhausted" in why
        # audit-recorded
        assert any("budget denied" in line
                   for line in reg.audit_tail())
        # the denied attempt did not consume budget
        assert reg.get("bot")["actions_used"] == 2

    def test_no_budget_means_no_denial(self):
        reg = self._registry(budgets=None)
        results = [reg.authorize_action("bot", "mouse", "mouse.click")[0]
                   for _ in range(50)]
        assert all(results)
        assert reg.get("bot")["actions_used"] == 50

    def test_rate_budget_per_minute(self):
        reg = self._registry(budgets={"max_actions_per_minute": 2})
        assert reg.authorize_action("bot", "mouse", "mouse.click")[0] is True
        assert reg.authorize_action("bot", "mouse", "mouse.click")[0] is True
        ok, why = reg.authorize_action("bot", "mouse", "mouse.click")
        assert ok is False and "budget_exhausted" in why

    def test_zero_budget_denies_immediately(self):
        reg = self._registry(budgets={"max_actions": 0})
        ok, why = reg.authorize_action("bot", "mouse", "mouse.click")
        assert ok is False and "budget_exhausted" in why

    def test_permission_denial_still_reported_first(self):
        reg = self._registry(budgets={"max_actions": 1})
        reg.permissions.revoke("*", "mouse.click")
        ok, why = reg.authorize_action("bot", "mouse", "mouse.click")
        assert ok is False and "permission" in why


# ─────────────────────────────────────────────────────────────────────────────
# subprocess smoke — the wire server boots as a REAL process
# ─────────────────────────────────────────────────────────────────────────────


def _stdio_env(**extra):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_repo / "airmouse_pkg")
    env.pop("AIRMOUSE_AIP_ENGINE", None)
    env.pop("AIRMOUSE_AIP_HOME", None)
    env.update(extra)
    return env


class TestSubprocessSmoke:
    def test_simulator_mode_answers_discover(self):
        proc = subprocess.run(
            [sys.executable, "-m", "airmouse.aip_stdio"],
            input=_req("discover", mid="m1") + "\n",
            capture_output=True, text=True, timeout=60,
            env=_stdio_env(AIRMOUSE_AIP_SIMULATOR="1"),
            cwd=str(_repo))
        assert proc.returncode == 0, proc.stderr
        lines = proc.stdout.splitlines()
        assert len(lines) == 1                        # ONE response line
        reply = json.loads(lines[0])                  # valid JSON line
        assert reply["type"] == "capabilities"
        assert reply["request_id"] == "m1"
        assert reply["payload"]["protocol_version"] == "1.0"

    def test_sequential_framing_over_a_pipe(self):
        requests = "\n".join([
            _req("discover", mid="m1"),
            "garbage line",
            _req("status", mid="m2"),
        ]) + "\n"
        proc = subprocess.run(
            [sys.executable, "-m", "airmouse.aip_stdio"],
            input=requests, capture_output=True, text=True, timeout=60,
            env=_stdio_env(AIRMOUSE_AIP_SIMULATOR="1"),
            cwd=str(_repo))
        assert proc.returncode == 0, proc.stderr
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        assert [r["type"] for r in replies] == \
            ["capabilities", "error", "status"]

    def test_real_mode_without_executor_exits_2(self):
        proc = subprocess.run(
            [sys.executable, "-m", "airmouse.aip_stdio"],
            input="", capture_output=True, text=True, timeout=60,
            env=_stdio_env(AIRMOUSE_AIP_REAL="1",
                           AIRMOUSE_AIP_SIMULATOR="0"),
            cwd=str(_repo))
        assert proc.returncode == 2
        assert "real mode requires --executor wiring" in proc.stderr
        assert proc.stdout == ""

    def test_real_mode_with_env_engine_serves(self):
        """AIRMOUSE_AIP_ENGINE="module:attr" wires a real engine."""
        proc = subprocess.run(
            [sys.executable, "-m", "airmouse.aip_stdio"],
            input=_req("status", mid="m1") + "\n",
            capture_output=True, text=True, timeout=60,
            env=_stdio_env(AIRMOUSE_AIP_REAL="1",
                           AIRMOUSE_AIP_ENGINE="airmouse.actions:ActionEngine"),
            cwd=str(_repo))
        assert proc.returncode == 0, proc.stderr
        reply = json.loads(proc.stdout.splitlines()[0])
        assert reply["type"] == "status"
        assert reply["payload"]["action_engine"] is True
        assert reply["payload"]["mode"] == "real"

    def test_stdio_transport_of_agent_core_round_trips(self):
        """agent-core's ``stdio://`` client speaks to the wire server
        end-to-end: envelope framing, sequential reply, error codes."""
        _repo_agent_core = _repo / "agent-core"
        code = "\n".join([
            "import sys",
            "sys.path.insert(0, r'%s')" % _repo_agent_core,
            "from airmouse_agent import AirMouse, AipError",
            "air = AirMouse(transport='stdio://%s -m airmouse.aip_stdio',"
            "agent_id='core-test')" % sys.executable,
            "try:",
            "    air.execute(intent='noop')",
            "    print('EXECUTED')",
            "except AipError as exc:",
            "    print('DENIED:' + exc.code)",
        ])
        env = _stdio_env(
            AIRMOUSE_AIP_SIMULATOR="1",
            PYTHONPATH=str(_repo / "airmouse_pkg") + os.pathsep +
            str(_repo_agent_core))
        proc = subprocess.run([sys.executable, "-c", code], env=env,
                              capture_output=True, text=True, timeout=60,
                              cwd=str(_repo))
        # the simulated endpoint ships with NO grants -> execute fails
        # closed as permission_denied over the wire (proves the full
        # round trip; a desync would hang and hit the timeout instead)
        assert "DENIED:permission_denied" in proc.stdout, (
            proc.stdout, proc.stderr)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
