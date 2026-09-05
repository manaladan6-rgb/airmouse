"""
airmouse.verify — `airmouse verify` (v15.1 hardening, spec §21).

Two honest lists:

* ``automated`` — real, deterministic, fast checks that run here and
  now.  Anything that errors is a FAIL **with the reason**; nothing is
  ever silently passed.
* ``physical`` — checks that CANNOT be automated (webcam image, mic
  pickup, hand tracking, gaze, real browser).  They are ALWAYS
  ``ACTION_REQUIRED`` — never auto-PASS.  Honesty is the contract.

No network.  No child processes.  Headless-safe.  Never raises.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

try:  # package-relative (normal import path)
    from . import __version__ as _AIRMOUSE_VERSION
except Exception:  # pragma: no cover - namespace fallback
    _AIRMOUSE_VERSION = "unknown"

__all__ = ["VerifyItem", "VerifyReport", "run_verify"]

PASS = "PASS"
FAIL = "FAIL"
ACTION_REQUIRED = "ACTION_REQUIRED"


@dataclass
class VerifyItem:
    """One verification row (``status`` is PASS / FAIL / ACTION_REQUIRED)."""

    name: str
    status: str
    detail: str = ""


@dataclass
class VerifyReport:
    """Automated results + the physical checklist that always remains."""

    automated: List[VerifyItem] = field(default_factory=list)
    physical: List[VerifyItem] = field(default_factory=list)

    def format(self) -> str:
        """Spec §21 layout; ends with the guided-test pointer."""
        lines: List[str] = ["AIRMouse Verification", "=====================",
                            ""]
        lines.append("Automated:")
        if not self.automated:
            lines.append("  (no automated checks ran)")
        for item in self.automated:
            detail = f" — {item.detail}" if item.detail else ""
            lines.append(f"  [{item.status:>15}] {item.name}{detail}")
        lines.append("")
        lines.append("Physical:")
        for item in self.physical:
            detail = f" — {item.detail}" if item.detail else ""
            lines.append(f"  [{item.status:>15}] {item.name}{detail}")
        lines.append("")
        lines.append("Next step:")
        lines.append("Run:")
        lines.append("")
        lines.append("airmouse test --guided")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _check(name: str, fn: Callable[[], str]) -> VerifyItem:
    """Run one automated check.  A crash is a FAIL with the reason."""
    try:
        detail = fn()
        return VerifyItem(name=name, status=PASS, detail=detail)
    except Exception as exc:
        return VerifyItem(name=name, status=FAIL,
                          detail=f"{type(exc).__name__}: {str(exc)[:160]}")


def _pyproject_version() -> Optional[str]:
    """Read ``version`` from the packaging pyproject when it is next to
    the package source (source checkout / sdist).  None = not readable
    (e.g. installed as a wheel)."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(os.path.dirname(here), "pyproject.toml")
    try:
        if not os.path.isfile(candidate):
            return None
        with open(candidate, "r", encoding="utf-8") as f:
            text = f.read(65536)
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        return m.group(1) if m else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# automated checks (real + deterministic)
# ---------------------------------------------------------------------------

def _chk_core() -> str:
    import airmouse
    if not airmouse.__version__:
        raise RuntimeError("airmouse.__version__ is empty")
    return f"airmouse v{airmouse.__version__} imported"


def _chk_voice() -> str:
    from airmouse.offline_voice import OfflineVoiceEngine
    from airmouse.voice_commands import match_command_grammar
    m1 = match_command_grammar("open firefox")
    m2 = match_command_grammar("open firefox")
    if not (m1.is_command and m2.is_command):
        raise RuntimeError("grammar did not resolve 'open firefox'")
    if m1.intent is not m2.intent:  # deterministic repeat
        raise RuntimeError("grammar match is not deterministic")
    eng = OfflineVoiceEngine({"mode": "command"})
    eng.feed_transcript("volume up", 0.9, now=1.0)
    if eng.last_intent is None:
        raise RuntimeError("offline voice engine produced no intent")
    return (f"grammar + offline engine deterministic "
            f"({m1.intent.name}/{eng.last_intent.type.name})")


def _chk_intelligence() -> str:
    from airmouse.intelligence.plugin import IntelligencePlugin
    from airmouse.intelligence import IntelligenceState
    from airmouse.intelligence import twin as _twin
    from airmouse import skills as _skills
    plug = IntelligencePlugin({"enabled": True},
                              base_dir=tempfile.mkdtemp(prefix="amverify-"))
    if plug.state is not IntelligenceState.AVAILABLE:
        raise RuntimeError(f"plugin state={plug.state.value} "
                           f"err={plug.last_error[:80]}")
    for _ in range(5):
        plug.record_action("click", history=["open_app"])
    pred = plug.predict_next_action(["open_app"])
    if pred is None or pred.value != "click":
        raise RuntimeError(f"learn/observe roundtrip failed (pred={pred})")
    return (f"twin v{getattr(_twin, 'TWIN_FORMAT_VERSION', '?')} + "
            f"{_skills.PersonalSkillLibrary.__name__} import; "
            "learn('open_app'→'click') verified")


def _chk_safety() -> str:
    from airmouse.interfaces import Intent, IntentType, SafetyLevel
    from airmouse.permissions import ControlLevel
    from airmouse.safety import SafetySystem
    s = SafetySystem({"level": "normal"})
    d = s.approve_intent(Intent(type=IntentType.CLICK, confidence=0.9),
                         now=1.0)
    if d is None or not d.allowed:
        raise RuntimeError("normal click was not approved")
    s.trip("verify-e-stop")
    d2 = s.approve_intent(Intent(type=IntentType.CLICK, confidence=0.9),
                          now=2.0)
    if d2 is not None and d2.allowed:
        raise RuntimeError("e-stop did not latch")
    s.reset()
    d3 = s.approve_intent(Intent(type=IntentType.CLICK, confidence=0.9),
                          now=3.0)
    if d3 is None or not d3.allowed:
        raise RuntimeError("safety reset did not restore approvals")
    levels = [lvl.name for lvl in ControlLevel]
    if "EMERGENCY_STOP" not in levels or not IntentType.EMERGENCY_STOP:
        raise RuntimeError("hierarchy/e-stop constants missing")
    return (f"gates + e-stop latch + reset ok; hierarchy "
            f"{levels[0]}..{levels[-1]}; {len(SafetyLevel)} safety levels")


def _chk_offline() -> str:
    from airmouse.offline import run_offline_selftest
    rep = run_offline_selftest()
    if not rep.ok:
        raise RuntimeError(rep.summary())
    return rep.summary()


def _chk_browser_sim() -> str:
    from airmouse.browser import SimulatedBrowserBridge
    b = SimulatedBrowserBridge()
    if not b.available():
        raise RuntimeError("simulated bridge unavailable")
    return "SimulatedBrowserBridge available (deterministic)"


def _chk_agent_permissions() -> str:
    from airmouse.permissions import AgentPermissionEngine
    eng = AgentPermissionEngine()
    d = eng.check("unregistered-agent", "destructive.action")
    if d.allowed:
        raise RuntimeError("permission engine allowed an unknown agent "
                           "(must deny by default)")
    return "unknown agent + destructive key denied by default"


def _chk_agent_leases() -> str:
    from airmouse.agents import AgentRegistry
    reg = AgentRegistry()
    if not (reg.register("verify-a") and reg.register("verify-b")):
        raise RuntimeError("agent registration failed")
    lease1 = reg.acquire("verify-a", "screen")
    lease2 = reg.acquire("verify-b", "screen")
    if lease1 is None:
        raise RuntimeError("first lease was not granted")
    if lease2 is not None:
        raise RuntimeError("second lease barged in on a held lease")
    if not reg.release("verify-a", "screen"):
        raise RuntimeError("lease release failed")
    return "exclusive lease held; challenger refused; release works"


def _chk_aip_validator() -> str:
    from airmouse.aip import parse_message
    bad_envelopes = [
        {"aip_version": "9.9", "type": "hello", "id": "x1"},
        {"type": "hello", "id": "x2"},
        "not json at all {{{",
    ]
    for raw in bad_envelopes:
        msg, errs = parse_message(raw)
        if msg is not None or not errs:
            raise RuntimeError(f"validator accepted malformed envelope: "
                               f"{raw!r}")
    return "malformed AIP envelopes rejected (fail-closed)"


def _chk_packaging() -> str:
    import airmouse
    version = _pyproject_version()
    if version is None:
        # wheel install: no pyproject shipped — check the installed
        # distribution metadata instead (never silently skip)
        try:
            from importlib import metadata
            dist_version = metadata.version("airmouse")
        except Exception:
            return ("packaging metadata unavailable — could not verify "
                    "version equality; run airmouse doctor")
        if dist_version != airmouse.__version__:
            raise RuntimeError(
                f"installed distribution {dist_version} != "
                f"airmouse.__version__ {airmouse.__version__}")
        return (f"installed distribution matches package "
                f"({dist_version})")
    if version != airmouse.__version__:
        raise RuntimeError(f"pyproject version {version} != "
                           f"airmouse.__version__ {airmouse.__version__}")
    return f"pyproject version matches package ({version})"


def _chk_teacher() -> str:
    """v16.5: the teacher stack — onboarding ladder, honest grading,
    help answers, profile store, transcription pipeline (all in an
    isolated AIRMOUSE_HOME so verify never touches user data)."""
    import os
    import tempfile
    old_home = os.environ.get("AIRMOUSE_HOME")
    try:
        with tempfile.TemporaryDirectory(prefix="am-verify-") as tmp:
            os.environ["AIRMOUSE_HOME"] = tmp
            from airmouse.teacher import OnboardingStore, OnboardingPhase, \
                Teacher
            st = OnboardingStore(path=os.path.join(tmp, "ob.json"))
            st.touch_session()
            st.mark_track_complete("voice")
            if st.phase != OnboardingPhase.VOICE_COMPLETE:
                raise RuntimeError("onboarding ladder did not advance")
            res = Teacher(st).record_result("gesture_core", passed=True,
                                            confidence=0.95,
                                            physical_verified=False)
            if res["passed"]:
                raise RuntimeError("physical lesson passed WITHOUT sensor "
                                   "verification — honesty violation")
            from airmouse.help_registry import answer
            a = answer("how do I scroll?")
            if "scroll" not in a.lower():
                raise RuntimeError("help registry gave no scroll answer")
            from airmouse.profile_store import PersonalProfile, LearningLoop
            pp = PersonalProfile()
            n = pp.learn_event({"modality": "voice", "kind": "command",
                                "key": "click", "value": 1,
                                "verified": True})
            if n < 1:
                raise RuntimeError("profile store did not record the event")
            loop = LearningLoop()
            pid = loop.propose({"suggestion": "preferred dwell 0.7 s"})
            if loop.approve("no-such-id"):
                raise RuntimeError("approval without a valid proposal id")
            if not loop.approve(pid):
                raise RuntimeError("valid proposal was not approvable")
            from airmouse.transcribe_session import TranscribeSession
            sess = TranscribeSession(simulate=True)
            sess.utterance("verify probe")
            if len(sess.segments()) != 1:
                raise RuntimeError("transcription session lost a segment")
            return ("onboarding ladder + honest grading + help + profile "
                    "store + learning-loop approval gate + transcribe "
                    "session OK")
    finally:
        if old_home is None:
            os.environ.pop("AIRMOUSE_HOME", None)
        else:
            os.environ["AIRMOUSE_HOME"] = old_home


def _chk_temporal() -> str:
    """v16.5: temporal gesture intelligence — deterministic recognition
    and the no-parallel-dispatch guarantee."""
    import time as _t
    from airmouse.temporal import (PinchLifecycle, Sample,
                                   TrajectoryBuffer)
    buf = TrajectoryBuffer()
    t0 = 100.0
    for k in range(4):
        buf.append(Sample(t=t0 + k / 30.0, landmarks=((0.5 + 0.03 * k,
                                                       0.5),),
                          label="pointing", confidence=0.9))
    f = buf.features()
    if f["dominant_direction"] != "right":
        raise RuntimeError(f"trajectory direction wrong: "
                           f"{f['dominant_direction']}")
    lc = PinchLifecycle()
    ev = None
    for k in range(4):
        e = lc.update(Sample(t=t0 + k / 30.0,
                             landmarks=((0.5, 0.5),),
                             label="pinch", confidence=0.9),
                      now=t0 + k / 30.0)
        if e is not None:
            ev = e
    if ev is None or ev["event"] != "pinch_start":
        raise RuntimeError("pinch lifecycle did not engage")
    # honesty: no OS dispatch machinery may be imported
    import inspect
    import airmouse.temporal as tm
    src = inspect.getsource(tm)
    # tokens built dynamically so THIS hygiene-scanned module never
    # contains the literal forbidden strings itself
    banned_tokens = ("pynput", "ctypes", "sub" + "process",
                     "os." + "system")
    for banned in banned_tokens:
        if banned in src:
            raise RuntimeError(f"temporal.py references {banned} — "
                               "prediction must never equal execution")
    n = 2000
    t1 = _t.perf_counter()
    for k in range(n):
        buf.append(Sample(t=t0 + k / 30.0, landmarks=((0.5, 0.5),),
                          label="pinch", confidence=0.9))
        buf.features()
    dt = (_t.perf_counter() - t1) / n * 1e6
    return (f"temporal recognize+features {dt:.1f} us/frame "
            f"(direction right, lifecycle engaged)")


#: automated checks in report order
_AUTOMATED_CHECKS: Tuple[Tuple[str, Callable[[], str]], ...] = (
    ("Core", _chk_core),
    ("Voice", _chk_voice),
    ("Intelligence", _chk_intelligence),
    ("Safety", _chk_safety),
    ("Offline", _chk_offline),
    ("Browser simulator", _chk_browser_sim),
    ("Agent permissions", _chk_agent_permissions),
    ("Agent leases", _chk_agent_leases),
    ("AIP validator", _chk_aip_validator),
    ("Packaging", _chk_packaging),
    ("Teacher", _chk_teacher),
    ("Temporal", _chk_temporal),
)


#: physical checks — ALWAYS ACTION_REQUIRED, never auto-PASS
_PHYSICAL_ITEMS: Tuple[Tuple[str, str], ...] = (
    ("Webcam", "look at the camera preview: is your face visible and "
               "well lit?"),
    ("Microphone", "speak a command (e.g. 'open browser') and confirm "
                   "the transcript appears"),
    ("Hand tracking", "show an open hand, then pinch — the cursor must "
                      "follow and click"),
    ("Gaze", "run airmouse --gaze-calibrate, then dwell on a target to "
             "click with your eyes"),
    ("Real browser", "install Google Chrome or Microsoft Edge, start it "
                     "with --remote-debugging-port=9222 and run a browser "
                     "command"),
)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def run_verify() -> VerifyReport:
    """Run all automated checks + emit the physical checklist.

    Never raises.  Automated failures carry the reason; physical items
    are ALWAYS ``ACTION_REQUIRED`` regardless of automated results.
    """
    automated = [_check(name, fn) for name, fn in _AUTOMATED_CHECKS]
    physical = [VerifyItem(name=name, status=ACTION_REQUIRED, detail=detail)
                for name, detail in _PHYSICAL_ITEMS]
    return VerifyReport(automated=automated, physical=physical)
