"""
airmouse.offline — v10 True Offline Mode 🔌
===========================================

Mission §17: the complete control stack must remain operational with
networking DISABLED.  This module provides:

1. :class:`OfflineGate` — the runtime switch.  When engaged it marks
   network-dependent functionality as blocked so every subsystem can
   consult it (voice ASR, browser CDP, update checks, cloud TTS…).
   Local functionality is untouched.

2. :func:`network_isolation` — a context manager that REALLY blocks
   network syscalls for its body (monkeypatches ``socket.socket.connect``
   + ``create_connection`` to raise).  The automated offline tests run
   the actual system inside it — that is what makes §17 verifiable
   instead of aspirational.

3. :func:`run_offline_selftest` — an end-to-end offline exercise used
   by ``airmouse offline-test`` and by the test suite: voice grammar →
   intent, voice → context → intent, gesture registry, RF bridge,
   browser (simulated bridge), fusion → intent → action → verify — all
   inside network isolation.  Returns a report dict.

Everything is stdlib, deterministic, and headless.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import contextlib
import socket
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

__all__ = ["OfflineGate", "network_isolation", "run_offline_selftest"]


# ---------------------------------------------------------------------------
# OfflineGate
# ---------------------------------------------------------------------------


class OfflineGate:
    """Runtime offline switch (process-wide singleton semantics by flag).

    - ``engage()`` / ``relax()`` flip the gate.
    - ``blocked`` is True while engaged.
    - ``check(feature)`` returns False when the feature requires the
      network and the gate is engaged; True otherwise.
    - ``guard(feature)`` is a decorator for network-dependent functions:
      while engaged it short-circuits with a None result (never raises).

    The gate never blocks anything by default (``OfflineGate()`` starts
    disengaged) so existing behaviour is unchanged.
    """

    #: features that require network access (allowlist of BLOCKED ones)
    NETWORK_FEATURES = frozenset({
        "cloud_asr",          # recognize_google and friends
        "cloud_tts",
        "browser_cdp",        # CDP is LOCAL but blocked too in strict mode
        "software_update",
        "telemetry_upload",
    })

    _instance: Optional["OfflineGate"] = None

    def __init__(self, engaged: bool = False) -> None:
        self._engaged = bool(engaged)
        self.blocked_calls = 0
        self.blocked_features: List[str] = []

    # -- singleton accessor ------------------------------------------------

    @classmethod
    def global_gate(cls) -> "OfflineGate":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -- state ---------------------------------------------------------------

    @property
    def engaged(self) -> bool:
        return self._engaged

    @property
    def blocked(self) -> bool:
        return self._engaged

    def engage(self) -> None:
        self._engaged = True

    def relax(self) -> None:
        self._engaged = False

    # -- queries ------------------------------------------------------------

    def check(self, feature: str) -> bool:
        """True when ``feature`` may run.  Never raises."""
        if not self._engaged:
            return True
        if str(feature or "") in self.NETWORK_FEATURES:
            self.blocked_calls += 1
            if feature not in self.blocked_features:
                self.blocked_features.append(feature)
            return False
        return True

    def guard(self, feature: str) -> Callable[[Callable], Callable]:
        """Decorator: skip a network-dependent function while offline."""
        def deco(fn: Callable) -> Callable:
            def wrapper(*args: Any, **kwargs: Any):
                if not self.check(feature):
                    return None
                return fn(*args, **kwargs)
            wrapper.__name__ = getattr(fn, "__name__", "guarded")
            return wrapper
        return deco

    def status(self) -> Dict[str, Any]:
        return {
            "engaged": self._engaged,
            "blocked_calls": self.blocked_calls,
            "blocked_features": list(self.blocked_features),
        }


# ---------------------------------------------------------------------------
# Network isolation (REAL syscall blocking for tests)
# ---------------------------------------------------------------------------


class _NetworkBlockedError(OSError):
    """Raised when code attempts a network connection offline."""


@contextlib.contextmanager
def network_isolation(block_localhost: bool = False):
    """Context manager that makes every outbound connection attempt fail.

    Monkeypatches ``socket.socket.connect``/``connect_ex`` and
    ``socket.create_connection`` to raise :class:`_NetworkBlockedError`.
    Loopback stays AVAILABLE by default (the browser-bridge server is
    127.0.0.1-only and must keep working offline); pass
    ``block_localhost=True`` for the strictest mode.

    On exit the originals are restored even on exception.
    """
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_create = socket.create_connection

    def _refuse(sock, addr, *a, **kw):
        # NOTE: assigned to socket.socket.connect, so the FIRST arg is the
        # socket instance and the second is the address (bug found by the
        # v10 test suite — the loopback passthrough previously never fired).
        host = ""
        try:
            host = str(addr[0]) if isinstance(addr, (tuple, list)) else str(addr)
        except Exception:
            pass
        if not block_localhost and host in ("127.0.0.1", "localhost", "::1"):
            return orig_connect(sock, addr, *a, **kw)
        raise _NetworkBlockedError(
            f"network disabled (offline test): connect({host!r}) refused")

    def _refuse_ex(sock, addr, *a, **kw):
        try:
            return _refuse(sock, addr, *a, **kw) or 0
        except _NetworkBlockedError as exc:
            raise exc

    def _refuse_create(address, *a, **kw):
        # module-level function: NO self — the first arg IS the address
        host = ""
        try:
            host = str(address[0]) if isinstance(address, (tuple, list)) \
                else str(address)
        except Exception:
            pass
        if not block_localhost and host in ("127.0.0.1", "localhost", "::1"):
            return orig_create(address, *a, **kw)
        raise _NetworkBlockedError(
            f"network disabled (offline test): connect({host!r}) refused")

    socket.socket.connect = _refuse                     # type: ignore
    socket.socket.connect_ex = _refuse_ex               # type: ignore
    socket.create_connection = _refuse_create           # type: ignore
    try:
        yield
    finally:
        socket.socket.connect = orig_connect            # type: ignore
        socket.socket.connect_ex = orig_connect_ex      # type: ignore
        socket.create_connection = orig_create          # type: ignore


# ---------------------------------------------------------------------------
# End-to-end offline self-test (deterministic, no hardware)
# ---------------------------------------------------------------------------


@dataclass
class OfflineReport:
    """Structured result of :func:`run_offline_selftest`."""

    ok: bool = True
    checks: List[Dict[str, Any]] = field(default_factory=list)

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "passed": bool(passed),
                            "detail": str(detail)})
        if not passed:
            self.ok = False

    def summary(self) -> str:
        passed = sum(1 for c in self.checks if c["passed"])
        return (f"offline selftest: {passed}/{len(self.checks)} checks "
                f"passed, overall={'OK' if self.ok else 'FAILED'}")


def run_offline_selftest() -> OfflineReport:
    """Exercise the FULL v10 stack with networking truly disabled (§17).

    Deterministic; uses the simulated providers/bridges.  Every check
    runs inside :func:`network_isolation` — any hidden network call
    fails the test loudly.
    """
    report = OfflineReport()

    with network_isolation():
        try:
            _run_checks(report)
        except Exception as exc:  # a crash IS a failed check
            report.record("stack_crash", False, repr(exc))

    # a real connection attempt must have been impossible — verify by
    # trying one and expecting refusal
    try:
        with network_isolation():
            socket.create_connection(("example.com", 80), timeout=0.2)
        report.record("network_actually_blocked", False,
                      "connection unexpectedly succeeded")
    except OSError:
        report.record("network_actually_blocked", True)

    return report


def _run_checks(report: OfflineReport) -> None:
    # 1. voice grammar → intent (§4/§6/§7)
    from .voice_commands import match_command_grammar
    from .interfaces import IntentType
    m = match_command_grammar("open firefox")
    report.record("voice_grammar", m.is_command and
                  m.intent is IntentType.OPEN, m.name)

    # 2. offline voice engine command + dictation (§5)
    from .offline_voice import OfflineVoiceEngine
    eng = OfflineVoiceEngine({"mode": "command"})
    eng.feed_transcript("volume up", 0.9, now=1.0)
    report.record("voice_command_mode", eng.last_intent is not None and
                  eng.last_intent.type is IntentType.VOLUME,
                  str(eng.last_intent))
    eng.set_mode("dictation")
    eng.feed_transcript("hello there.", 0.9, now=2.0)
    report.record("voice_dictation",
                  eng.last_committed_text == "hello there",
                  eng.last_committed_text)

    # 3. context resolution (§8)
    from .context import ContextEngine
    from .interfaces import ScreenTarget, ScreenTargetType
    ce = ContextEngine()
    btn = ScreenTarget(id="b", type=ScreenTargetType.BUTTON,
                       bbox=(0, 0, 10, 10), text="Submit", confidence=0.9,
                       actionable=True)
    ce.update_gaze_target(btn, now=3.0)
    m2 = match_command_grammar("click that")
    intent = None
    from .offline_voice import voice_match_to_intent
    intent = voice_match_to_intent(m2, ce.snapshot(), now=3.1)
    report.record("voice_context", intent is not None
                  and intent.target is not None and
                  intent.target.text == "Submit", str(intent))

    # 4. gesture registry + custom sequence (§9)
    from .gesture_registry import GestureRegistry, CustomGestureMapping
    reg = GestureRegistry()
    reg.define(CustomGestureMapping(
        name="air_delete", pattern=["fist", "swipe_left", "pinch_release"],
        intent=IntentType.HOTKEY, params={"keys": ["ctrl", "backspace"]}))
    got = None
    for label, t in (("fist", 10.0), ("swipe_left", 10.2),
                     ("pinch_release", 10.4)):
        _, got = reg.feed(label, confidence=0.9, now=t)
    report.record("gesture_custom", got is not None and
                  got.type is IntentType.HOTKEY, str(got))

    # 5. RF bridge (§16)
    from .rf import SimulatedRFProvider, RFBridge
    rf = SimulatedRFProvider()
    rf.push("gesture", "swipe_left", 0.9)
    bridge = RFBridge(provider=rf)
    pairs = bridge.poll(now=11.0)
    report.record("rf_bridge", len(pairs) == 1 and
                  pairs[0][0].kind.value == "rf_gesture",
                  bridge.status().__str__())

    # 6. browser simulated bridge + semantic resolution + verification
    #    (§11-13; loopback stays available but the SIMULATED bridge is
    #    used — no sockets involved at all)
    from .browser import (BrowserController, SemanticBrowserResolver,
                          SimulatedBrowserBridge)
    sim = SimulatedBrowserBridge()
    ctrl = BrowserController(config={"enabled": True}, bridge=sim)
    ctrl.start()
    state = ctrl.poll(now=12.0)
    report.record("browser_bridge", state is not None and
                  len(state.elements) > 0, "simulated bridge")
    resolver = SemanticBrowserResolver(ctrl.mapper)
    res = resolver.resolve("click the login button", now=12.1)
    report.record("browser_semantic", res.matched and
                  res.action == "click", f"{res.action}/{res.element}")
    out = ctrl.execute(res, now=12.2)
    report.record("browser_execute_verify",
                  out.get("status") == "executed" and
                  out.get("verification", {}).get("status") == "passed",
                  str(out))

    # 7. fusion → intent → action → verify pipeline (§14/§10)
    from airmouse.agent import InteractionAgent
    from airmouse.interfaces import GazeState
    agent = InteractionAgent({"mode": "fusion", "verify_actions": False},
                             executor=_StubExecutor())
    gaze = GazeState(x=0.5, y=0.5, confidence=0.9)
    out = agent.process_frame(
        hand_data={"gesture": "pinch", "point": (960, 540),
                   "confidence": 0.9},
        utterance="", now=13.0, gaze_state=gaze)
    ok = any(r.ok for r in out.get("reports", []))
    report.record("fusion_pipeline", ok,
                  f"{len(out.get('reports', []))} reports")

    # 8. event bus flowed events
    from .eventbus import EventBus
    bus = EventBus()
    bus.publish_voice("click", "click", 0.9, now=14.0)
    report.record("event_bus", bus.stats()["published"] == 1,
                  str(bus.stats()))

    # 9. offline gate semantics
    gate = OfflineGate(engaged=True)
    report.record("offline_gate", not gate.check("cloud_asr")
                  and gate.check("local_grammar"), "gate routing")

    # ═══ v11.5: the adaptive intelligence must work fully offline (§31) ═══
    try:
        import tempfile
        from .intelligence.plugin import IntelligencePlugin
        plug = IntelligencePlugin({"enabled": True},
                                  base_dir=tempfile.mkdtemp())
        _ok = plug.state.value == "available"
        if _ok:
            # learn "click" tends to follow "open_app", then ask for the
            # next action after open_app — a pure offline prediction
            for i in range(5):
                plug.record_action("click", history=["open_app"])
            plug.record_command("open browser", hour=9)
            plug.record_text("hello bro how are you")
            _pred = plug.predict_next_action(["open_app"])
            _ok = _pred is not None and _pred.value == "click"
        report.record("intelligence_offline", _ok,
                      f"state={plug.state.value} pred={_pred.value if _pred else None}")

        from .intelligence.memory import InteractionMemory
        mem = InteractionMemory()
        report.record("memory_offline",
                      mem.record("chrome -> vscode") is not None
                      and mem.record("password=x") is None,
                      f"patterns={mem.size()} scrubbed={mem.rejected_sensitive}")

        from .intelligence.vocabulary import PersonalVocabulary
        voc = PersonalVocabulary()
        voc.learn_correction("Hydra Link", "HydraLink")
        _txt, _n = voc.apply_corrections("connect to Hydra Link")
        report.record("vocabulary_offline", _n == 1 and "HydraLink" in _txt,
                      f"corrections={voc.correction_count}")

        from .transcription import (LiveTranscriptionEngine,
                                    SimulatedStreamingProvider)
        _eng = LiveTranscriptionEngine(
            provider=SimulatedStreamingProvider(), history_enabled=False)
        _eng.start()
        _eng.provider.push_utterance("hello bro question mark", 0.9)
        for _i in range(8):
            _eng.feed_audio(b"\x10\x27" * 1000, now=_i * 0.05)
        _seg = _eng.finalize(now=0.5)
        report.record("transcription_offline",
                      _seg is not None and "Hello bro?" == _seg.text,
                      f"final={_seg.text if _seg else None}")

        from .fusion2 import FusionEngine2, FusionSignal, SignalKind
        _f = FusionEngine2()
        _c = _f.fuse([FusionSignal(SignalKind.GAZE, "click", None, 0.9),
                      FusionSignal(SignalKind.VOICE, "click", None, 0.9)])
        report.record("fusion2_offline",
                      _c.intent == "click" and _c.executable,
                      f"conf={_c.confidence}")
    except Exception as exc:
        report.record("intelligence_offline", False, repr(exc))


class _StubExecutor:
    """Minimal executor for the pipeline check inside the selftest."""

    def click(self, x, y):
        return {"pointer": (x, y)}

    def move(self, x, y):
        return {"pointer": (x, y)}
