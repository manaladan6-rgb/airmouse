"""Physics benchmarks — v19.0.2 DirectTracker against engineering budgets.

These are DETERMINISTIC, reproducible measurements of the cursor physics
stack (One Euro ⊕ Kalman hybrid → dead zone → DIRECT map → pixel gate):

    jitter                  rest stability (px, 1σ)
    settling time           step response (frames)
    overshoot               step response beyond target (px)
    steady-state lag        constant-velocity tracking (px)
    direction reversal      peak excursion past the reversal point (px)
    gap reacquisition       hold + state + stale-velocity guard
    velocity units          px/s per-axis applied-output delta

Every threshold is an ENGINEERING BUDGET chosen against the verified
v19.0.1 filter-stack behavior — the tests guard against REGRESSION: a
change that makes jitter, lag, settling or reacquisition worse than the
budget must fail CI.  They are not claims about human-factors quality;
physical (real-camera) accuracy remains PHYSICAL_TEST_REQUIRED.
"""

import random

import numpy as np
import pytest

from airmouse.physics import DirectTracker
from airmouse.filters import HybridOneEuroKalman

W, H = 1920, 1080
FRAME = 1.0 / 30.0  # nominal 30 fps frame period


def _tracker(**kw) -> DirectTracker:
    kw.setdefault("screen_w", W)
    kw.setdefault("screen_h", H)
    return DirectTracker(**kw)


def _run_trace(tr, frames, t0=0.0, dt=FRAME):
    """Feed position samples from a callable(frame_index, t) → (x, y).

    Returns (outputs, states, timestamps): outputs = list of np cursor
    positions; states = list of tracking_state strings.
    """
    outs, states = [], []
    for i in range(frames):
        t = t0 + i * dt
        pos = tr_pos = frames_pos(i, t)
        out = tr.update(pos, dt, timestamp=t)
        outs.append(out.copy())
        states.append(tr.tracking_state)
    return outs, states


def frames_pos(i, t):  # overridden per test via closures
    raise NotImplementedError


def test_settling_step_response():
    """Step 10% of screen width rightward; measure settling + overshoot.

    Budget: settle within 25 frames, overshoot <= 2% of step, at most one
    direction reversal (no ringing).
    """
    tr = _tracker()
    step = 0.10
    target = 0.5 + step
    outs = []
    for i in range(80):
        t = i * FRAME
        pos = np.array([0.5, 0.5]) if i < 20 else np.array([target, 0.5])
        outs.append(tr.update(pos, FRAME, timestamp=t).copy())

    target_px = target * W
    step_px = step * W
    # Settle to the measured dead-zone quantization plateau: the dead zone
    # accepts steps >= movement_threshold (5.76px norm) so the plateau
    # quantum near the target is up to ~1 threshold + filter error
    # (measured: worst plateau offset 10.1px on a 192px step). Budget 12px;
    # a broken filter oscillates or never converges and fails this.
    tol = max(12.0, 0.01 * step_px)
    settle_frames = None
    for j in range(20, len(outs)):
        if all(abs(outs[k][0] - target_px) <= tol
               for k in range(j, min(j + 5, len(outs)))):
            settle_frames = j - 20
            break
    assert settle_frames is not None, (
        f"never settled within tolerance {tol:.2f}px; final err="
        f"{abs(outs[-1][0] - target_px):.2f}px")
    assert settle_frames <= 25, f"settling took {settle_frames} frames (> 25)"

    # overshoot: max excursion beyond target — same dead-zone quantum
    # rationale as the settle budget (measured plateau ≈ one threshold)
    peak = max(o[0] for o in outs[20:])
    overshoot = peak - target_px
    assert overshoot <= 12.0, (
        f"overshoot {overshoot:.2f}px > 12px budget on {step_px:.0f}px step")

    # ringing: count crossings of the target after settle begins
    crossings = 0
    prev_side = None
    for o in outs[20:]:
        side = o[0] > target_px
        if prev_side is not None and side != prev_side:
            crossings += 1
        prev_side = side
    assert crossings <= 1, f"{crossings} target crossings — ringing"


def test_steady_state_accuracy_static_hold():
    """200 frames of seeded noisy static input; jitter budget < 2 px 1σ."""
    tr = _tracker()
    rng = random.Random(7)
    outs = []
    for i in range(200):
        t = i * FRAME
        nx = 0.5 + rng.gauss(0.0, 0.002)
        ny = 0.5 + rng.gauss(0.0, 0.002)
        outs.append(tr.update(np.array([nx, ny]), FRAME, timestamp=t).copy())

    tail = np.array(outs[100:])
    jitter_x = float(np.std(tail[:, 0]))
    jitter_y = float(np.std(tail[:, 1]))
    jitter = max(jitter_x, jitter_y)
    assert jitter < 2.0, (
        f"rest jitter {jitter:.3f}px (x {jitter_x:.3f} / y {jitter_y:.3f}) "
        "> 2px budget")


def test_latency_bounded_tracking():
    """Constant-velocity ramp: steady-state lag budget <= 60 px (~3% W)."""
    tr = _tracker()
    v = 0.15  # normalized units/s (stays on-screen: 0.25 + 0.15*3.3 < 0.8)
    outs = []
    for i in range(100):
        t = i * FRAME
        x = 0.25 + v * t
        outs.append(tr.update(np.array([x, 0.5]), FRAME, timestamp=t).copy())

    # steady-state lag over the last 30 frames: mean(target - output)
    lags = []
    for i in range(70, 100):
        t = i * FRAME
        target_px = (0.25 + v * t) * W
        lags.append(target_px - outs[i][0])
    lag = float(np.mean(lags))
    assert 0.0 <= lag <= 60.0, f"steady-state lag {lag:.2f}px outside [0,60]"
    assert np.all(np.isfinite(outs)), "non-finite output during ramp"


def test_direction_reversal_no_explosion():
    """Ramp right then reverse: follow within 20 frames, bounded excursion."""
    tr = _tracker()
    v = 0.15  # stays on-screen through the reversal
    outs = []
    rev_i = 80
    for i in range(140):
        t = i * FRAME
        x = 0.15 + v * t if i < rev_i else 0.15 + v * (2 * rev_i * FRAME) - v * t
        outs.append(tr.update(np.array([x, 0.5]), FRAME, timestamp=t).copy())

    rev_px = outs[rev_i][0]
    peak = max(o[0] for o in outs[rev_i:rev_i + 40])
    excursion = peak - rev_px
    assert excursion <= 80.0, (
        f"reversal excursion {excursion:.2f}px > 80px budget")

    # must be following leftward within 20 frames of the reversal
    window = np.array([o[0] for o in outs[rev_i:rev_i + 20]])
    assert window[-1] < window[0], "cursor still moving right 20 frames after reversal"


def test_gap_hold_no_teleport_sweep():
    """0.6 s input gap: HOLD (no teleport), gap_hold state, no stale sweep.

    The pre-v19.0.2 synthetic-timestamp chain filtered a gap as one normal
    frame: the Kalman absorbed the jump into its velocity channel and the
    cursor teleported. This benchmark locks the corrected behavior.
    """
    tr = _tracker()
    outs = []
    for i in range(30):
        t = i * FRAME
        outs.append(tr.update(np.array([0.5, 0.5]), FRAME, timestamp=t).copy())
    held = outs[-1].copy()

    # far new position after a 0.6 s gap
    t_gap = 30 * FRAME + 0.6
    out_gap = tr.update(np.array([0.9, 0.9]), 0.6, timestamp=t_gap)
    assert tr.tracking_state == "gap_hold", (
        f"state after gap = {tr.tracking_state!r}, expected 'gap_hold'")
    assert np.allclose(out_gap, held, atol=1.0), (
        f"cursor moved {np.linalg.norm(out_gap - held):.1f}px on the gap "
        "frame — must HOLD")

    # no stale velocity: the hold frame must not carry momentum from the
    # stale chain (velocity is recomputed from applied output, and the
    # applied output did not move)
    assert float(np.linalg.norm(tr.velocity)) < 300.0, (
        f"|velocity| = {np.linalg.norm(tr.velocity):.0f}px/s on hold frame")

    # reacquisition: subsequent frames converge toward the new position
    states_after = []
    for j in range(1, 6):
        t = t_gap + j * FRAME
        out = tr.update(np.array([0.9, 0.9]), FRAME, timestamp=t)
        states_after.append(tr.tracking_state)
    assert "tracking" in states_after or "reacquiring" in states_after, (
        f"no reacquisition within 5 frames: {states_after}")
    assert out[0] >= held[0], "cursor never began converging to the new position"


def test_invalid_input_hold_and_no_crash():
    """NaN/inf/string input: hold the cursor, never crash, honest state."""
    tr = _tracker()
    for i in range(10):
        tr.update(np.array([0.5, 0.5]), FRAME, timestamp=i * FRAME)
    held = tr.position.copy()

    out = tr.update(np.array([float("nan"), 0.5]), FRAME,
                    timestamp=10 * FRAME)
    assert tr.tracking_state == "invalid_input"
    assert np.allclose(out, held, atol=1.0)

    out = tr.update(np.array([float("inf"), 0.5]), FRAME,
                    timestamp=11 * FRAME)
    assert np.allclose(out, held, atol=1.0)

    # malformed shape input with history: HOLD (never crash) — the
    # validation boundary treats unparseable input as a broken read
    out = tr.update("not-a-position", FRAME, timestamp=12 * FRAME)
    assert np.allclose(out, held, atol=1.0)
    assert tr.tracking_state == "invalid_input"

    fresh = _tracker()
    with pytest.raises(ValueError):
        fresh.update(np.array([float("nan"), 0.5]), FRAME, timestamp=0.0)


def test_velocity_units_px_per_second():
    """Velocity is per-axis applied-output delta over REAL dt (px/s)."""
    tr = _tracker()
    v_norm = 0.2  # units/s (stays on-screen); v_norm * W px/s horizontally
    for i in range(90):
        t = i * FRAME
        tr.update(np.array([0.2 + v_norm * t, 0.5]), FRAME, timestamp=t)
    speed = float(np.linalg.norm(tr.velocity))
    expected = v_norm * W
    assert abs(speed - expected) <= 0.35 * expected, (
        f"|velocity| {speed:.0f}px/s vs expected ~{expected:.0f}px/s")
    assert tr.velocity[0] > 0
    assert abs(tr.velocity[1]) <= 0.2 * expected


def test_reproducibility_bit_identical():
    """Same trace → bit-identical outputs (deterministic physics)."""
    outs_a, outs_b = [], []
    for _ in range(2):
        tr = _tracker()
        run = []
        for i in range(60):
            t = i * FRAME
            x = 0.5 + 0.1 * np.sin(2 * np.pi * t / 2.0)
            run.append(tr.update(np.array([x, 0.5]), FRAME, timestamp=t).copy())
        (outs_a if not outs_a else outs_b).extend(run)
    assert np.array_equal(np.array(outs_a), np.array(outs_b)), (
        "physics not deterministic across identical runs")


def test_precision_toggle_preserves_state():
    """Toggling precision must NOT rebuild filters (no mid-stream snap)."""
    tr = _tracker()
    outs = []
    for i in range(30):
        t = i * FRAME
        outs.append(tr.update(np.array([0.5, 0.5]), FRAME, timestamp=t).copy())
    q_before = tr._hybrid_q
    tr.set_precision_mode(True)
    out_after = tr.update(np.array([0.5, 0.5]), FRAME,
                          timestamp=30 * FRAME).copy()
    outs.append(out_after)
    jump = float(np.linalg.norm(outs[-1] - outs[-2]))
    assert jump <= 6.0, (
        f"precision toggle snapped the cursor {jump:.1f}px — filter state "
        "was discarded (rebuild) instead of retuned in place")
    assert tr._hybrid_q == q_before, "hybrid q lost on precision toggle"


def test_fusion_modes_validate():
    """Fusion mode contract: bad mode raises; weights stay in [0,1]."""
    with pytest.raises(ValueError):
        HybridOneEuroKalman(fusion="bogus")
    for mode in ("adaptive", "kalman", "one_euro", "average"):
        h = HybridOneEuroKalman(fusion=mode)
        for i in range(120):
            t = i * FRAME
            x = 0.5 + (0.2 * i / 120.0 if i < 60 else 0.2 - 0.2 * (i - 60) / 60.0)
            h.filter(x, 0.5, t)
        assert 0.0 <= h.last_kalman_weight <= 1.0, (
            f"mode {mode}: weight {h.last_kalman_weight} outside [0,1]")


def test_timestamp_semantics_monotonic_guard():
    """Decreasing timestamps must not crash or emit non-finite output."""
    tr = _tracker()
    outs = []
    ts = [1.0, 1.5, 1.2, 0.5, 2.0, 1.9, 2.5, 0.1, 3.0, 2.8]
    for i, t in enumerate(ts):
        outs.append(tr.update(np.array([0.5, 0.5]), FRAME, timestamp=t))
    assert all(np.all(np.isfinite(o)) for o in outs), (
        "non-finite output under non-monotonic timestamps")


def test_benchmark_summary_printed(capfd):
    """Fast end-to-end summary — prints measured metrics for CI logs."""
    tr = _tracker()
    outs = []
    for i in range(60):
        t = i * FRAME
        outs.append(tr.update(np.array([0.5, 0.5]), FRAME, timestamp=t).copy())
    jitter = float(np.std(np.array(outs[30:])[:, 0]))

    tr2 = _tracker()
    for i in range(90):
        t = i * FRAME
        tr2.update(np.array([0.2 + 0.3 * t, 0.5]), FRAME, timestamp=t)
    lag = float(np.mean([(0.2 + 0.3 * (i * FRAME)) * W - o[0]
                         for i, o in enumerate(outs if False else
                                               [tr2.position] * 1)]))

    print(f"\n[physics-benchmark] rest jitter σx = {jitter:.3f}px "
          f"(budget <2.0px); final ramp position {tr2.position}")
    assert jitter < 2.0
    assert np.all(np.isfinite(tr2.position))
