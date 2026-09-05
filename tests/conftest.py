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
