"""Regression tests — AirMouse v5.0 functionality must survive v9.

Covers the v5 public APIs with deterministic, hardware-free checks:
voice command matching, hybrid One Euro + Kalman filter, adaptive
calibration, pinch-to-zoom controller, v1 macro record/replay.
"""
from __future__ import annotations

import os
import time

import numpy as np
import pytest

from airmouse.voice_control import match_command, VoiceCommand
from airmouse.filters import HybridOneEuroKalman, OneEuroFilter2D
from airmouse.calibration import AdaptiveCalibration
from airmouse.zoom import PinchZoomController
from airmouse import macros as macros_v1


# ── voice matching (v5) ──────────────────────────────────────────────────────

def test_voice_match_exact_phrase():
    cmd, score = match_command("please zoom in now", "turbo")
    assert cmd == "zoom_in" and score >= 0.9


def test_voice_match_fuzzy():
    cmd, _ = match_command("um click over there please", "high")
    assert cmd in ("click", "none")


def test_voice_match_empty_and_garbage():
    assert match_command("", "high")[0] in (VoiceCommand.NONE, "none", "")
    assert match_command("zzzz qqqq", "normal")[0] in (VoiceCommand.NONE, "none", "")


def test_voice_longest_phrase_wins():
    # "stop recording" must beat "stop"
    cmd, _ = match_command("stop recording now", "high")
    assert cmd == "stop_record"


# ── hybrid One Euro + Kalman (v5) ────────────────────────────────────────────

def test_hybrid_filter_still_hand_jitter_reduction():
    rng = np.random.default_rng(7)
    f = HybridOneEuroKalman()
    target = (0.5, 0.5)
    raw = [np.array(target) + rng.normal(0, 0.02, 2) for _ in range(200)]
    out = []
    t = 0.0
    for r in raw:
        out.append(f.filter(r[0], r[1], t))
        t += 1 / 30.0
    tail = np.array(out[100:])
    raw_tail_std = float(np.std(np.array(raw[100:]), axis=0).mean())
    assert float(np.std(tail, axis=0).mean()) < raw_tail_std  # smoother than raw


def test_hybrid_filter_tracks_moving_target():
    f = HybridOneEuroKalman()
    t = 0.0
    errs = []
    for i in range(200):
        x = 0.2 + 0.4 * (i / 200.0)
        y = 0.5
        out = f.filter(x, y, t)
        t += 1 / 30.0
        if i > 60:
            errs.append(abs(out[0] - x))
    assert max(errs) < 0.06  # responsive, no runaway lag
    assert 0.0 <= f.last_kalman_weight <= 1.0


def test_hybrid_all_fusion_modes():
    for mode in ("adaptive", "kalman", "one_euro", "average"):
        f = HybridOneEuroKalman(fusion=mode)
        o = f.filter(0.3, 0.4, 0.0)
        assert np.all(np.isfinite(o))


# ── adaptive calibration (v5) ────────────────────────────────────────────────

def test_adaptive_calibration_expands_and_remaps(tmp_path):
    cal = AdaptiveCalibration()
    # feed corners to expand the learned reach box
    for x, y in [(0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9)] * 20:
        cal.update((x, y))
    assert cal.is_ready
    remapped = cal.remap((0.1, 0.1))
    assert 0.0 <= remapped[0] <= 1.0 and 0.0 <= remapped[1] <= 1.0
    corner = cal.remap((0.5, 0.5))
    assert 0.0 <= corner[0] <= 1.0


def test_adaptive_calibration_nan_safe():
    cal = AdaptiveCalibration()
    cal.update((float("nan"), 0.5))  # must not poison stats
    assert np.isfinite(cal.remap((0.5, 0.5))[0])


# ── pinch-to-zoom (v5) ───────────────────────────────────────────────────────

def test_pinch_zoom_emits_ticks():
    z = PinchZoomController()
    t = 0.0
    ticks = 0
    # pinch hold to engage
    for _ in range(12):
        z.update(True, 0.5, t)
        t += 1 / 30.0
    # move hand up steadily
    for i in range(40):
        ticks += z.update(True, 0.5 - 0.004 * i, t)
        t += 1 / 30.0
    assert ticks > 0  # hand up = zoom in
    z.reset()
    assert not z.active


def test_pinch_zoom_nan_guard():
    z = PinchZoomController()
    assert z.update(True, float("nan"), 0.0) == 0


# ── v1 macros (v5) ───────────────────────────────────────────────────────────

def test_macro_v1_record_save_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(macros_v1, "MACRO_DIR", str(tmp_path))
    rec = macros_v1.MacroRecorder()
    rec.start("reg")
    rec.record("click")
    rec.record("scroll", amount=3)
    rec.stop()
    path = rec.save()
    assert path and os.path.exists(path)
    assert "reg" in macros_v1.list_macros()

    class _Exec:
        def __init__(self):
            self.calls = []

        def __call__(self, event, params):
            self.calls.append((event, dict(params)))

    ex = _Exec()
    player = macros_v1.MacroPlayer(executor=ex)
    player.load("reg")
    assert player.play(speed=64.0) is True
    events = [e for e, _ in ex.calls]
    assert "click" in events and "scroll" in events


def test_macro_v1_missing_file_raises(tmp_path):
    player = macros_v1.MacroPlayer(executor=lambda e, p: None)
    with pytest.raises(FileNotFoundError):
        player.load("definitely_not_a_macro_12345")
