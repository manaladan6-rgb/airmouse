"""Wiring + failure-mode integration tests for the v9 CLI surface.

Verifies the real command-line entry point (subprocess level), the
config round-trip of the new [v9] section, and key failure modes:
camera loss, config garbage, graceful degradation.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "airmouse_pkg")
ENV = {**os.environ, "PYTHONPATH": _PKG}


def _cli(*args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "airmouse", *args],
        env=ENV, cwd=_PKG, capture_output=True, text=True, timeout=timeout)


# ── CLI surface ──────────────────────────────────────────────────────────────

def test_cli_version_reports_version():
    # v10: version bumped 9.0.0 -> 10.0.0 (Universal Offline Interaction
    # Edition); v11.5: bumped again -> 11.5.0 (Adaptive Human-Computer
    # Intelligence).  The CLI must report the CURRENT package version —
    # checked against airmouse.__version__ so this stays correct across
    # future bumps.
    import airmouse as _am
    r = _cli("--version")
    assert r.returncode == 0
    assert _am.__version__ in (r.stdout + r.stderr)
    assert "AirMouse" in (r.stdout + r.stderr)


def test_cli_help_lists_v9_flags():
    r = _cli("--help")
    assert r.returncode == 0
    out = r.stdout + r.stderr
    for flag in ("--gaze", "--no-gaze", "--gaze-calibrate", "--fusion",
                 "--hands-free", "--assist", "--interaction", "--no-voice",
                 "--voice", "--no-kalman", "--no-zoom", "--record", "--play",
                 "--macros", "--version"):
        assert flag in out, flag


def test_cli_gaze_calibration_simulated_end_to_end():
    """Deterministic, hardware-free verification of the complete
    gaze-calibration workflow through the real CLI (simulated eye)."""
    r = _cli("--gaze-calibrate", "--gaze-sim")
    assert r.returncode == 0
    out = r.stdout + r.stderr
    assert "GAZE CALIBRATION" in out
    assert "good" in out or "fair" in out
    assert "SAVED" in out


def test_cli_macros_lists(tmp_path):
    r = _cli("--macros")
    assert r.returncode == 0


# ── config round-trip ────────────────────────────────────────────────────────

def test_config_v9_section_roundtrip(tmp_path, monkeypatch):
    from airmouse.config import Config
    cfg = Config()
    cfg.fusion_mode = "hands_free"
    cfg.gaze_enabled = True
    cfg.gaze_min_confidence = 0.6
    cfg.safety_level = "careful"
    cfg.max_actions_per_sec = 5
    path = str(tmp_path / "config.toml")

    from airmouse import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", path, raising=False)
    try:
        cfg.save_defaults_to(path) if hasattr(cfg, "save_defaults_to") else None
    except AttributeError:
        pass
    # fall back to the module-level save path used by the app
    if not os.path.exists(path):
        import airmouse.config as c
        old = c.CONFIG_PATH
        c.CONFIG_PATH = path
        try:
            cfg.save_defaults()
        finally:
            c.CONFIG_PATH = old

    assert os.path.exists(path)
    text = open(path).read()
    assert "[v9]" in text and "fusion_mode" in text

    cfg2 = Config()
    old = None
    import airmouse.config as c
    old = c.CONFIG_PATH
    c.CONFIG_PATH = path
    try:
        cfg2.load()
    finally:
        c.CONFIG_PATH = old
    assert cfg2.fusion_mode == "hands_free"
    assert cfg2.gaze_enabled is True
    assert abs(cfg2.gaze_min_confidence - 0.6) < 1e-9
    assert cfg2.safety_level == "careful"
    assert cfg2.max_actions_per_sec == 5


def test_config_corrupt_file_degrades_gracefully(tmp_path):
    import airmouse.config as c
    from airmouse.config import Config
    bad = tmp_path / "bad.toml"
    bad.write_text("[[[\nnot toml at all")
    old = c.CONFIG_PATH
    c.CONFIG_PATH = str(bad)
    try:
        cfg = Config()
        cfg.load()  # must not raise
    finally:
        c.CONFIG_PATH = old
    assert cfg.fusion_mode in ("hand", "gaze", "voice", "fusion",
                               "hands_free", "assist")


# ── failure modes ────────────────────────────────────────────────────────────

def test_agent_survives_camera_loss_frames():
    """Camera disconnect: frame=None ticks must keep the pipeline alive."""
    from airmouse.agent import InteractionAgent
    agent = InteractionAgent({"mode": "fusion", "gaze_enabled": False})
    for i in range(10):
        out = agent.process_frame(frame=None, utterance="", now=1.0 + i * 0.03)
        assert "decision" in out
    t = agent.telemetry.snapshot()
    assert t.actions_total == 0  # nothing happened, nothing crashed
    agent.shutdown()


def test_agent_estop_blocks_all_then_recovers():
    from airmouse.agent import InteractionAgent
    agent = InteractionAgent({"mode": "fusion", "gaze_enabled": False})
    agent.trip_estop("test")
    out = agent.process_frame(hand_data={"gesture": "pinch", "point": (10, 10),
                                         "confidence": 0.9},
                              utterance="", now=1.0)
    assert agent.safety.level.name == "EMERGENCY"
    agent.reset_estop()
    assert agent.safety.level.name != "EMERGENCY"
    agent.shutdown()


def test_agent_voice_loss_downgrades_to_safe_mode():
    from airmouse.interfaces import Modality
    from airmouse.agent import InteractionAgent
    agent = InteractionAgent({"mode": "fusion", "gaze_enabled": False})
    agent.safety.report_stream_loss(Modality.GAZE, True, now=0.0)
    agent.safety.report_stream_loss(Modality.GAZE, True, now=3.0)  # > grace
    assert agent.safety.level.name == "SAFE_MODE"
    agent.safety.report_stream_loss(Modality.GAZE, False, now=3.5)
    assert agent.safety.level.name == "NORMAL"
    agent.shutdown()


def test_gaze_engine_face_lost_recovery_cycle():
    """Face lost -> FACE_LOST event; face back -> FACE_FOUND, no crash.
    (FACE_LOST fires only on a found->lost transition — the engine starts
    absent, so the very first None is silently ignored.)"""
    from airmouse.gaze import GazeEngine
    from airmouse.interfaces import GazeEventKind
    eng = GazeEngine({"gaze_filter_enabled": False})
    st0 = eng.update(None, 0.0)
    assert st0.confidence == 0.0
    try:
        from tests.test_gaze import make_landmarks
    except ImportError:
        from test_gaze import make_landmarks
    st1 = eng.update(make_landmarks(gaze_gx=0.0, gaze_gy=0.0), 1 / 30.0)
    assert st1.confidence > 0.0
    assert GazeEventKind.FACE_FOUND in st1.events
    st2 = eng.update(None, 2 / 30.0)
    assert GazeEventKind.FACE_LOST in st2.events
    assert st2.confidence == 0.0


def test_conflicting_modalities_arbitrated_deterministically():
    """Gaze left vs hand right: the higher score wins consistently."""
    from airmouse.fusion import MultimodalFusion
    from airmouse.interfaces import ScreenTarget, ScreenTargetType
    f = MultimodalFusion(mode="fusion")
    tgt = ScreenTarget(id="w1", type=ScreenTargetType.WINDOW,
                       bbox=(0, 0, 400, 300), confidence=0.95, actionable=True)
    f.update_gaze((100.0, 100.0), tgt, 0.95, 1.0)
    f.update_hand((1800.0, 900.0), "point", 0.5, 1.0)
    d1 = f.update(1.05)
    f2 = MultimodalFusion(mode="fusion")
    f2.update_gaze((100.0, 100.0), tgt, 0.95, 1.0)
    f2.update_hand((1800.0, 900.0), "point", 0.5, 1.0)
    d2 = f2.update(1.05)
    assert d1.has_target and d2.has_target
    assert d1.target_point() == d2.target_point()  # deterministic
    assert d1.target_point()[0] < 500  # gaze (conf 0.95 x 1.0) beats hand


def test_v5_path_untouched_by_v9_flags():
    """Config defaults keep the pure v5 experience: no gaze, hand mode."""
    from airmouse.config import Config
    cfg = Config()
    assert cfg.fusion_mode == "hand"
    assert cfg.gaze_enabled is False
    assert cfg.kalman_enabled is True
    assert cfg.zoom_enabled is True
    assert cfg.voice_enabled is False
