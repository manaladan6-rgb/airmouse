"""Performance benchmarks — AirMouse v9.0.

Quantitative latency/throughput measurements.  These are MEASUREMENT
tests: they print observed numbers and assert generous engineering
bounds (documented per test) so slow CI machines fail loudly only on
real regressions.  Run with -s to see the numbers.
"""
from __future__ import annotations

import time

import numpy as np
import pytest


def test_hand_filter_latency_and_quality():
    """Hybrid One Euro + Kalman: per-call latency and smoothing quality.

    Bounds: filter call < 1 ms (real budget: 30 fps frame = 33 ms);
    moving-target lag error < 0.06 normalized units; still-hand jitter
    below raw input noise.
    """
    from airmouse.filters import HybridOneEuroKalman
    f = HybridOneEuroKalman()
    rng = np.random.default_rng(3)
    # warmup
    for i in range(50):
        f.filter(0.5, 0.5, i / 30.0)
    t0 = time.perf_counter()
    n = 2000
    for i in range(n):
        f.filter(0.5 + 0.01 * np.sin(i / 5.0), 0.5, i / 30.0)
    dt_ms = (time.perf_counter() - t0) / n * 1000.0
    print(f"\n[perf] hand filter call: {dt_ms:.3f} ms")
    assert dt_ms < 1.0

    # moving lag
    f2 = HybridOneEuroKalman()
    errs = []
    for i in range(300):
        x = i / 300.0
        out = f2.filter(x, 0.5, i / 30.0)
        if i > 60:
            errs.append(abs(out[0] - x))
    print(f"[perf] hand filter moving lag: {max(errs):.4f}")
    assert max(errs) < 0.06

    # still-hand jitter
    f3 = HybridOneEuroKalman()
    raw_std = 0.02
    outs = []
    for i in range(300):
        out = f3.filter(0.5 + rng.normal(0, raw_std), 0.5 + rng.normal(0, raw_std),
                        i / 30.0)
        outs.append(out)
    out_std = float(np.std(np.array(outs[100:]), axis=0).mean())
    print(f"[perf] still-hand jitter: raw {raw_std} -> filtered {out_std:.4f}")
    assert out_std < raw_std


def test_gaze_pipeline_latency():
    """Gaze filter + calibration map: per-frame budget.

    Bounds: filter+map < 2 ms per frame (FaceMesh inference itself is the
    dominant cost on real hardware and is hardware-verified separately).
    """
    from airmouse.gaze_filter import GazeFilterPipeline
    from airmouse.gaze_calibration import GazeCalibration, run_point_calibration
    from airmouse.interfaces import GazeSample
    from airmouse.gaze_calibration import GazeCalibration as GC

    pipe = GazeFilterPipeline()
    cal = GazeCalibration(n_points=9, path="/tmp/perf-gaze-cal.json")

    def sampler(t):
        g = (t[0] + 0.5, t[1] + 0.5)  # identity-ish mapping
        return GazeSample(x=g[0], y=g[1], confidence=0.95)

    run_point_calibration(cal, sampler, samples_per_point=12,
                          screen_w=1920, screen_h=1080)
    assert cal.is_calibrated

    rng = np.random.default_rng(11)
    n = 2000
    t0 = time.perf_counter()
    for i in range(n):
        s = GazeSample(x=0.5 + rng.normal(0, 0.01), y=0.5 + rng.normal(0, 0.01),
                       confidence=0.9, timestamp=i / 30.0)
        fs = pipe.apply(s, s.timestamp)
        cal.map(fs.x, fs.y)
    dt_ms = (time.perf_counter() - t0) / n * 1000.0
    print(f"\n[perf] gaze filter+map: {dt_ms:.3f} ms/frame")
    assert dt_ms < 2.0

    # jitter reduction on a synthetic fixation (quantitative filter quality)
    pipe2 = GazeFilterPipeline()
    target = (0.5, 0.5)
    raw = [GazeSample(x=target[0] + rng.normal(0, 0.02),
                      y=target[1] + rng.normal(0, 0.02), confidence=0.95,
                      timestamp=i / 30.0) for i in range(400)]
    outs = [pipe2.apply(r, r.timestamp) for r in raw]
    raw_std = float(np.std([(r.x, r.y) for r in raw[100:]], axis=0).mean())
    out_std = float(np.std([(o.x, o.y) for o in outs[100:]], axis=0).mean())
    print(f"[perf] gaze jitter: raw {raw_std:.4f} -> filtered {out_std:.4f} "
          f"({raw_std / max(out_std, 1e-9):.1f}x reduction)")
    assert out_std < raw_std  # strictly better than raw


def test_fusion_intent_action_roundtrip_latency():
    """Full multimodal tick through the real agent: gaze target + pinch ->
    decision -> intent -> safety -> action.  Budget: < 15 ms per tick
    WITHOUT camera inference (perception is measured separately)."""
    from airmouse.agent import InteractionAgent
    from airmouse.actions import MockExecutor

    ex = MockExecutor()
    agent = InteractionAgent({"mode": "fusion", "gaze_enabled": False},
                             executor=ex)
    n = 300
    t0 = time.perf_counter()
    for i in range(n):
        agent.process_frame(
            hand_data={"gesture": "pinch", "point": (960.0, 540.0),
                       "confidence": 0.9} if i % 30 == 0 else None,
            utterance="click that" if i % 90 == 0 else "",
            now=1.0 + i * 0.033,
        )
    dt_ms = (time.perf_counter() - t0) / n * 1000.0
    print(f"\n[perf] agent fusion+intent+safety+action tick: {dt_ms:.3f} ms")
    assert dt_ms < 15.0
    t = agent.telemetry.snapshot()
    print(f"[perf] counters: {agent.telemetry.counters}")
    assert t.actions_total >= 1  # clicks actually flowed through
    agent.shutdown()


def test_intent_engine_throughput():
    """IntentEngine.process throughput: < 0.5 ms per call."""
    from airmouse.intent import IntentEngine
    from airmouse.interfaces import FusionDecision
    eng = IntentEngine()
    dec = FusionDecision()
    t0 = time.perf_counter()
    n = 5000
    for i in range(n):
        eng.process(dec, utterance="" if i % 2 else "click that",
                    now=1.0 + i * 0.016)
    dt_ms = (time.perf_counter() - t0) / n * 1000.0
    print(f"\n[perf] intent process: {dt_ms:.4f} ms/call")
    assert dt_ms < 0.5


def test_nl_parse_throughput():
    """Natural-language parsing: < 0.2 ms per utterance."""
    from airmouse.nl_control import parse_utterance
    utterances = ["click that", "scroll down a little", "close this window",
                  "zoom in a lot", "move that to the left", "go back",
                  "stop everything", "hello world no command here"]
    t0 = time.perf_counter()
    n = 2000
    for i in range(n):
        parse_utterance(utterances[i % len(utterances)])
    dt_ms = (time.perf_counter() - t0) / n * 1000.0
    print(f"\n[perf] NL parse: {dt_ms:.4f} ms/utterance")
    assert dt_ms < 0.2
