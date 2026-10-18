"""Pytest bootstrap for AirMouse v9 test suite.

Adds the package source tree (airmouse_pkg/) to sys.path so tests run
directly from a source checkout without installing the wheel.

v16.5.2: an autouse fixture isolates the authoritative ASR provider
registry between tests — provider selection must be deterministic
under monkeypatching, and the persisted "last working provider" state
must never leak between tests (or in from a real CLI run).
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = os.path.join(_ROOT, "airmouse_pkg")
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)


@pytest.fixture(autouse=True)
def _isolated_asr_registry(monkeypatch):
    """Reset the ASR registry singleton + neutralize persistence."""
    import airmouse.asr_registry as asr
    monkeypatch.setenv("AIRMOUSE_ASR_PROVIDER", "")
    monkeypatch.setattr(asr, "_load_persisted", lambda: "")
    monkeypatch.setattr(asr, "_persist", lambda name: None)
    asr.registry.reset()
    asr.registry._diagnoses = {}
    yield
    asr.registry.reset()
    asr.registry._diagnoses = {}


@pytest.fixture(autouse=True)
def _isolated_safety_gate(monkeypatch):
    """v19.0.3: isolate the central safety gate + estop flag file.

    The gate is a process-wide singleton by design; without isolation a
    test that trips the E-STOP (or runs main() in-process, or writes the
    cross-process flag file) would poison every later test with a
    latched gate.  Fail-closed stays fail-closed INSIDE each test.
    """
    import airmouse.safety_gate as sg
    sg.reset_gate_for_tests()
    try:  # clear any flag file left by an earlier test (current env)
        sg.clear_estop_flag()
    except Exception:
        pass
    yield
    sg.reset_gate_for_tests()
    try:
        sg.clear_estop_flag()
    except Exception:
        pass
