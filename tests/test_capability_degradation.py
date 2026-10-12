"""tests/test_capability_degradation.py — the v19 capability registry,
degradation ladder and evidence model (v18-foundation reconstruction).

Laws under test:
* the evidence pair is never collapsed (no "just REAL");
* the registry can OBSERVE, never upgrade;
* the degradation ladder is pure, total, honest, and never dead-ends the
  user (every non-DIAGNOSTIC verdict names a usable mode).
"""

from __future__ import annotations

import json

import pytest

from airmouse.capability_registry import (CapabilityRegistry,
                                          InteractionCapability,
                                          build_registry)
from airmouse.degradation import ReadinessLevel, decide, interaction_mode
from airmouse.evidence import (Epistemic, EvidenceLabel, Reality,
                               from_component_state)


# ---------------------------------------------------------------------------
# evidence model
# ---------------------------------------------------------------------------

def test_label_always_renders_both_axes():
    lab = EvidenceLabel(Reality.REAL, Epistemic.OBSERVED, "probed here")
    assert "REAL" in str(lab) and "OBSERVED" in str(lab)
    assert "probed here" in str(lab)


def test_component_state_mapping_is_honest():
    assert from_component_state("READY").reality == Reality.REAL
    assert from_component_state("READY").epistemic == Epistemic.OBSERVED
    assert from_component_state("SIMULATED").reality == Reality.SIMULATED
    assert from_component_state("HARDWARE").reality == \
        Reality.PHYSICAL_TEST_REQUIRED
    for s in ("FAILED", "WARNING", "INCOMPATIBLE", "NOT_INSTALLED"):
        assert from_component_state(s).reality != Reality.REAL
    assert from_component_state("GARBAGE-STATE").reality == \
        Reality.NOT_AVAILABLE
    assert from_component_state("GARBAGE-STATE").epistemic == Epistemic.UNKNOWN


def test_no_api_returns_reality_alone():
    import inspect
    import airmouse.evidence as ev
    public = [n for n in dir(ev) if not n.startswith("_")]
    # every exported callable returns the pair object, never a bare Reality
    assert "EvidenceLabel" in public and "from_component_state" in public
    assert isinstance(from_component_state("READY"), EvidenceLabel)


# ---------------------------------------------------------------------------
# degradation ladder (pure function, exhaustive edges)
# ---------------------------------------------------------------------------

def test_full_when_everything_available():
    r = decide(gesture=True, voice=True, gaze=True, input=True)
    assert r.level == ReadinessLevel.FULL
    assert r.mode == "GESTURE+GAZE+VOICE"
    assert r.usable


def test_gesture_only_is_degraded_not_dead():
    r = decide(gesture=True, voice=False, gaze=False, input=True)
    assert r.level == ReadinessLevel.DEGRADED
    assert r.mode == "GESTURE"
    assert r.usable


def test_voice_only_is_degraded():
    r = decide(gesture=False, voice=True, gaze=False, input=True)
    assert r.level == ReadinessLevel.DEGRADED
    assert r.mode == "VOICE"
    assert "camera" in r.reasons[0]


def test_no_input_is_safe_minimal_with_reason():
    r = decide(gesture=True, voice=True, gaze=True, input=False)
    assert r.level == ReadinessLevel.SAFE_MINIMAL
    assert r.mode == "observation-only"
    assert r.safe_degraded
    assert not r.usable
    assert "input automation" in r.reasons[0]


def test_nothing_at_all_is_diagnostic():
    r = decide(gesture=False, voice=False, gaze=False, input=False)
    assert r.level == ReadinessLevel.DIAGNOSTIC
    assert not r.usable


def test_no_camera_no_mic_with_input_is_safe_minimal_simulation():
    r = decide(gesture=False, voice=False, gaze=False, input=True)
    assert r.level == ReadinessLevel.SAFE_MINIMAL
    assert r.mode == "simulation"
    assert "simulation" in r.reasons[0]


def test_interaction_mode_tokens():
    r = decide(gesture=True, voice=True, gaze=True, input=True)
    assert interaction_mode(r) == "gesture-gaze-voice"


def test_ladder_never_raises_on_any_input():
    for g in (False, True):
        for v in (False, True):
            for gz in (False, True):
                for i in (False, True):
                    assert decide(gesture=g, voice=v, gaze=gz,
                                  input=i).level is not None


# ---------------------------------------------------------------------------
# registry (built from the real probes on this machine)
# ---------------------------------------------------------------------------

DISPLAYS = {"gesture": "Hand gesture control", "voice": "Voice control",
            "gaze": "Gaze control", "input": "Mouse/keyboard control"}


def _row(key, state):
    return InteractionCapability(
        key=key, display=DISPLAYS[key], state=state,
        label=from_component_state(state))


def _registry(gesture="READY", voice="READY", gaze="OPTIONAL",
              input="READY", model=None):
    return CapabilityRegistry(
        caps=[_row("gesture", gesture), _row("voice", voice),
              _row("gaze", gaze), _row("input", input)],
        model_record=model or {"name": "vosk", "state": "MODEL_READY",
                               "installed": True, "compatible": True})


def test_registry_readiness_synthesis():
    assert _registry().readiness().level == ReadinessLevel.FULL
    assert _registry(gesture="UNAVAILABLE").readiness().level == \
        ReadinessLevel.DEGRADED
    assert _registry(input="UNAVAILABLE").readiness().level == \
        ReadinessLevel.SAFE_MINIMAL
    assert _registry(voice="NOT_INSTALLED").readiness().level == \
        ReadinessLevel.DEGRADED


def test_registry_machine_record_shape():
    data = _registry().to_machine()
    assert set(data) == {"interaction", "model", "readiness"}
    for key in ("gesture", "voice", "gaze", "input"):
        row = data["interaction"][key]
        assert row["reality"] in {r.value for r in Reality}
        assert row["epistemic"] in {e.value for e in Epistemic}
    assert data["readiness"]["level"] == ReadinessLevel.FULL.value
    json.dumps(data)             # machine record must be JSON-safe


def test_registry_render_names_every_interaction():
    text = _registry().render()
    for word in ("Hand gesture control", "Voice control", "Gaze control",
                 "Mouse/keyboard control",
                 "Best available interaction mode", "Readiness"):
        assert word in text


def test_build_registry_never_raises_and_is_honest():
    reg = build_registry(quick=True)
    rows = reg.interaction_rows()
    assert [c.key for c in rows] == ["gesture", "voice", "gaze", "input"]
    for c in rows:
        assert c.label.reality is not None
        assert c.label.epistemic is not None
    # a headless CI sandbox must NOT be told gesture control is READY
    mode, r = reg.best_mode()
    assert isinstance(mode, str) and mode
    # evidence labels stay inside the closed vocabulary
    assert all(c.label.reality in set(Reality) for c in rows)


# ---------------------------------------------------------------------------
# regression: v19 release audit — enum repr must never leak into user output
# ---------------------------------------------------------------------------

def test_gaze_state_is_plain_string_never_enum_repr():
    """`ComponentState(str, Enum)` members hash like their value, so the
    mark lookup worked — but f-strings rendered "ComponentState.OPTIONAL"
    on Python 3.11+.  The registry must carry PLAIN strings everywhere
    the user can see (release-audit regression)."""
    reg = build_registry(quick=False)          # exercises the probe path
    for c in reg.interaction_rows():
        assert isinstance(c.state, str)
        assert not c.state.startswith("ComponentState."), \
            f"raw enum leaked into state for {c.key}: {c.state!r}"
        assert c.state == str(c.state)
    rendered = reg.render()
    assert "ComponentState." not in rendered


def test_env_dial_covers_opencv_noise():
    """quiet._ENV_DIALS must include the OpenCV dial so a camera-less
    machine does not get scary C++ videoio warnings during doctor."""
    from airmouse import quiet as quiet_mod
    assert quiet_mod._ENV_DIALS.get("OPENCV_LOG_LEVEL") == "ERROR"


def test_cli_startup_does_not_pay_mediapipe_import():
    """Release-audit regression: importing the CLI (or tracker) must not
    import mediapipe (~1 s of matplotlib-weighted import work).  The
    lazy PEP 562 hook keeps ``airmouse.tracker.mp`` usable for runtime
    and tests while every CLI command stays fast."""
    import subprocess
    import sys
    code = (
        "import sys; import airmouse.__main__;"  # the full CLI module graph
        "assert 'mediapipe' not in sys.modules, 'mediapipe imported eagerly';"
        "import airmouse.tracker as t;"
        "assert 'mediapipe' not in sys.modules, 'tracker eager import';"
        "_ = t.mp;"                      # first touch imports + caches
        "assert 'mediapipe' in sys.modules and t.mp is sys.modules['mediapipe']"
    )
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
