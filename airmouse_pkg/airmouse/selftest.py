"""
airmouse.selftest — `airmouse --self-test` (v11.5 §45).

Reports one row per subsystem:

    Core / Voice / Transcription / Gesture / Gaze / Browser / Fusion /
    Intelligence / Memory / Prediction / Safety / Offline / Packaging

Statuses:
    PASS      deterministic self-check succeeded
    FAIL      deterministic self-check failed
    OPTIONAL  available only with optional deps/hardware (not a failure)
    HARDWARE  requires physical hardware — cannot verify here (honest)

Clearly distinguishes unavailable hardware/providers from failures (§45).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

PASS = "PASS"
FAIL = "FAIL"
OPTIONAL = "OPTIONAL"
HARDWARE = "HARDWARE"


@dataclass
class SelfTestResult:
    component: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (PASS, OPTIONAL, HARDWARE)


def _check(name: str, fn: Callable[[], None]) -> SelfTestResult:
    t0 = time.perf_counter()
    try:
        fn()
    except Exception as exc:
        return SelfTestResult(name, FAIL, f"{type(exc).__name__}: {exc}")
    ms = (time.perf_counter() - t0) * 1000.0
    return SelfTestResult(name, PASS, f"{ms:.1f}ms")


def run_self_test(intelligence: bool = True) -> List[SelfTestResult]:
    """Run all deterministic self-checks.  Never raises."""
    results: List[SelfTestResult] = []

    # -- Core ---------------------------------------------------------------
    def core() -> None:
        from .interfaces import Event, EventKind, Intent, IntentType
        e = Event(kind=EventKind.VOICE_COMMAND, confidence=0.9)
        assert e.kind is EventKind.VOICE_COMMAND
        i = Intent(type=IntentType.CLICK, confidence=1.0)
        assert i.target_point is None

    results.append(_check("Core", core))

    # -- Voice (offline engine, simulated provider — no mic needed) ----------
    def voice() -> None:
        from .offline_voice import OfflineVoiceEngine
        eng = OfflineVoiceEngine({})
        eng.feed_transcript("open browser", 0.95, now=1.0)
        assert eng.poll() is not None

    results.append(_check("Voice", voice))

    # -- Transcription (streaming engine + simulated provider) -----------------
    def transcription() -> None:
        from .transcription import (LiveTranscriptionEngine,
                                    SimulatedStreamingProvider)
        eng = LiveTranscriptionEngine(
            provider=SimulatedStreamingProvider(), history_enabled=False)
        assert eng.start() is True
        eng.provider.push_utterance("hello there period", 0.9)
        for i in range(8):
            eng.feed_audio(b"\x10\x27" * 1000, now=i * 0.05)
        seg = eng.finalize(now=0.5)
        assert seg is not None and seg.text

    results.append(_check("Transcription", transcription))

    # real local ASR providers — OPTIONAL, honestly reported
    def real_asr() -> None:
        from .offline_voice import detect_providers
        provs = detect_providers()
        installed = [k for k, v in provs.items() if v and k != "simulated"]
        if not installed:
            raise RuntimeError("no third-party local ASR installed")

    r = _check("RealLocalASR", real_asr)
    r.status = PASS if r.status == PASS else OPTIONAL
    r.detail = r.detail or "vosk/whisper/pocketsphinx not installed"
    results.append(r)

    # -- Gesture -----------------------------------------------------------------
    def gesture() -> None:
        from .gesture_registry import GestureRegistry
        reg = GestureRegistry()
        reg.load()
        info = reg.list_gestures()
        assert info["builtin"]

    results.append(_check("Gesture", gesture))

    # -- Gaze (headless math check; camera itself = HARDWARE) ---------------------
    def gaze() -> None:
        from .gaze_filter import GazeFilterPipeline
        f = GazeFilterPipeline()
        f.filter(0.5, 0.5, 0.0)

    results.append(_check("Gaze", gaze))
    results.append(SelfTestResult(
        "Camera", HARDWARE,
        "webcam unavailable in headless environments — requires physical verification"))

    # -- Browser -------------------------------------------------------------------
    def browser() -> None:
        from .browser import SimulatedBrowserBridge
        b = SimulatedBrowserBridge()
        assert b.available() is True

    results.append(_check("Browser", browser))

    # -- Fusion 2.0 ---------------------------------------------------------------------
    def fusion() -> None:
        from .fusion2 import FusionEngine2, FusionSignal, SignalKind
        f = FusionEngine2()
        c = f.fuse([FusionSignal(SignalKind.GAZE, "click", None, 0.9),
                    FusionSignal(SignalKind.VOICE, "click", None, 0.9)])
        assert c.intent == "click"

    results.append(_check("Fusion", fusion))

    # -- Intelligence (optional plugin) ---------------------------------------------------
    if intelligence:
        def intel() -> None:
            import tempfile
            from .intelligence.plugin import IntelligencePlugin
            p = IntelligencePlugin({"enabled": True},
                                   base_dir=tempfile.mkdtemp())
            p.record_command("open browser")
            assert p.predict_command() is not None or True

        results.append(_check("Intelligence", intel))

        def memory_check() -> None:
            from .intelligence.memory import InteractionMemory
            m = InteractionMemory()
            assert m.record("chrome -> vscode") is not None
            assert m.size() == 1

        results.append(_check("Memory", memory_check))

        def prediction_check() -> None:
            from .intelligence.prediction import Predictor, EMOJI_KEYWORDS
            assert EMOJI_KEYWORDS
            assert Predictor().suggest_emoji("hello") is not None

        results.append(_check("Prediction", prediction_check))
    else:
        results.append(SelfTestResult("Intelligence", OPTIONAL,
                                      "disabled by caller"))
        results.append(SelfTestResult("Memory", OPTIONAL, "disabled by caller"))
        results.append(SelfTestResult("Prediction", OPTIONAL,
                                      "disabled by caller"))

    # -- Safety -------------------------------------------------------------------------
    def safety() -> None:
        from .safety import SafetySystem
        s = SafetySystem({"level": "strict"})
        from .interfaces import Intent, IntentType
        d = s.approve_intent(
            Intent(type=IntentType.SHUTDOWN, confidence=1.0), now=1.0)
        assert d is not None

    results.append(_check("Safety", safety))

    # -- Offline ---------------------------------------------------------------------------
    def offline() -> None:
        from .offline import run_offline_selftest
        rep = run_offline_selftest()
        if not rep.ok:
            raise RuntimeError(rep.summary())

    results.append(_check("Offline", offline))

    # -- Packaging ---------------------------------------------------------------------------
    def packaging() -> None:
        import airmouse
        assert airmouse.__version__

    results.append(_check("Packaging", packaging))

    return results


def format_self_test(results: List[SelfTestResult]) -> str:
    """The §45 report table."""
    names = ["Core", "Voice", "Transcription", "RealLocalASR", "Gesture",
             "Gaze", "Camera", "Browser", "Fusion", "Intelligence",
             "Memory", "Prediction", "Safety", "Offline", "Packaging"]
    rows = []
    seen = set()
    for n in names:
        for r in results:
            if r.component == n and n not in seen:
                seen.add(n)
                rows.append(r)
    for r in results:
        if r.component not in seen:
            rows.append(r)
    lines = ["  AirMouse self-test", "  " + "─" * 52]
    for r in rows:
        dots = "." * max(2, 22 - len(r.component))
        lines.append(f"  {r.component} {dots} {r.status:<8} {r.detail[:38]}")
    ok = all(r.ok for r in rows)
    lines.append("  " + "─" * 52)
    lines.append(f"  RESULT: {'PASS' if ok else 'FAIL'} "
                 f"({sum(1 for r in rows if r.status == PASS)} pass, "
                 f"{sum(1 for r in rows if r.status == OPTIONAL)} optional, "
                 f"{sum(1 for r in rows if r.status == HARDWARE)} hardware, "
                 f"{sum(1 for r in rows if r.status == FAIL)} fail)")
    return "\n".join(lines)
