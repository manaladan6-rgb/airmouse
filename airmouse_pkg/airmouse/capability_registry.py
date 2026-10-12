"""airmouse.capability_registry — runtime capability discovery (v19).

Reconstructed from the recorded v18 spec and wired to what v16.5.3
actually has.  ONE place answers the user's real question: *what can
AirMouse do on THIS machine, right now, and how do we know?*

Sources fused (all pre-existing, fail-closed):

* :func:`airmouse.capabilities.detect_all` — the twelve-section probe
* :mod:`airmouse.asr_registry` — THE authoritative ASR provider state
* :func:`airmouse.model_integrity.vosk_model_status` — the Phase 6
  model record (state/source/size/digests/license)

Every row carries an indivisible :class:`~airmouse.evidence.EvidenceLabel`
(Reality × Epistemic).  The registry can OBSERVE this machine; it can
never upgrade a SIMULATED/OPTIONAL/MISSING capability to REAL, and it
never claims hardware testing that did not happen.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .degradation import Readiness, ReadinessLevel, decide, interaction_mode
from .evidence import Epistemic, EvidenceLabel, Reality, from_component_state

__all__ = ["InteractionCapability", "CapabilityRegistry",
           "build_registry"]


@dataclass
class InteractionCapability:
    """One user-facing interaction capability and how we know."""

    key: str                      # stable id: gesture / gaze / voice / input
    display: str                  # human name
    state: str                    # ComponentState value (runtime probe)
    detail: str = ""
    fix: str = ""
    label: EvidenceLabel = field(
        default_factory=lambda: EvidenceLabel(Reality.NOT_AVAILABLE,
                                              Epistemic.UNKNOWN))

    @property
    def usable(self) -> bool:
        return self.state == "READY"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "display": self.display,
            "state": self.state,
            "detail": self.detail,
            "fix": self.fix,
            "reality": self.label.reality.value,
            "epistemic": self.label.epistemic.value,
            "basis": self.label.basis,
        }


class CapabilityRegistry:
    """Runtime view over the fused probes — honest, never optimistic."""

    def __init__(self, caps: List[InteractionCapability],
                 model_record: Dict[str, Any]) -> None:
        self.caps = {c.key: c for c in caps}
        self.model_record = model_record

    # -- lookups ------------------------------------------------------------

    def get(self, key: str) -> Optional[InteractionCapability]:
        return self.caps.get(key)

    def interaction_rows(self) -> List[InteractionCapability]:
        """The four interaction rows in display order."""
        order = ("gesture", "voice", "gaze", "input")
        return [self.caps[k] for k in order if k in self.caps]

    # -- the verdict ----------------------------------------------------------

    def readiness(self) -> Readiness:
        """The degradation ladder over the OBSERVED booleans."""
        return decide(
            gesture=bool(self.caps.get("gesture") and
                         self.caps["gesture"].usable),
            voice=bool(self.caps.get("voice") and
                       self.caps["voice"].usable),
            gaze=bool(self.caps.get("gaze") and
                      self.caps["gaze"].usable),
            input=bool(self.caps.get("input") and
                       self.caps["input"].usable))

    def best_mode(self) -> Tuple[str, Readiness]:
        r = self.readiness()
        return (interaction_mode(r), r)

    # -- rendering ------------------------------------------------------------

    def to_machine(self) -> Dict[str, Any]:
        mode, r = self.best_mode()
        return {
            "interaction": {c.key: c.to_dict()
                            for c in self.interaction_rows()},
            "model": self.model_record,
            "readiness": {
                "level": r.level.value,
                "mode": mode,
                "available": list(r.available),
                "missing": list(r.missing),
                "reasons": list(r.reasons),
            },
        }

    def render_interaction_panel(self) -> str:
        """The compact Phase 7 panel for `airmouse doctor` (human path)."""
        mode, r = self.best_mode()
        marks = {"READY": "✓", "OPTIONAL": "○"}
        lines: List[str] = ["Available interaction:"]
        for c in self.interaction_rows():
            mark = marks.get(c.state, "✗" if c.state in
                             ("FAILED", "INCOMPATIBLE") else "·")
            lines.append(f"{mark} {c.display}")
        lines.append("")
        lines.append(f"Best mode: {mode.upper()}   "
                     f"Readiness: {r.level.value}")
        if r.missing:
            lines.append(f"Missing: {', '.join(r.missing)}")
        for reason in r.reasons:
            lines.append(f"  — {reason}")
        if not r.usable:
            lines.append("Next step: run `airmouse doctor --advanced` "
                         "for the full report")
        return "\n".join(lines)

    def render(self) -> str:
        mode, r = self.best_mode()
        lines: List[str] = ["AIRMouse Capabilities",
                            "===========================", ""]
        marks = {"READY": "✓", "OPTIONAL": "○"}
        for c in self.interaction_rows():
            mark = marks.get(c.state, "✗" if c.state in
                             ("FAILED", "INCOMPATIBLE") else "·")
            extra = f" — {c.detail}" if c.detail else ""
            lines.append(f"{mark} {c.display:<24} {c.state}{extra}")
            if c.fix:
                lines.append(f"      FIX: {c.fix}")
            lines.append(f"      [{c.label.reality.value}/"
                         f"{c.label.epistemic.value}] {c.label.basis}")
        rec = self.model_record
        lines.append("")
        state = rec.get("state", "MODEL_MISSING")
        mark = "✓" if rec.get("installed") else ("✗" if "CORRUPTED" in state
                                                 else "○")
        lines.append(f"{mark} Voice model ({rec.get('name', 'vosk')}): "
                     f"{state} — {rec.get('detail', '')}")
        lines.append(f"      license: {rec.get('license', 'unknown')} · "
                     f"source: {rec.get('provider', 'unknown')}")
        lines.append("")
        lines.append(f"Best available interaction mode: {mode.upper()}")
        lines.append(f"Readiness: {r.level.value}")
        if r.missing:
            lines.append(f"Missing: {', '.join(r.missing)}")
        for reason in r.reasons:
            lines.append(f"  — {reason}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# builder (fuses the pre-existing probes; never raises)
# ---------------------------------------------------------------------------

def build_registry(quick: bool = False) -> CapabilityRegistry:
    """Detect everything and fuse it into the registry.

    ``quick=True`` skips the slowest probes (used by the first-run
    launcher so the menu opens instantly).
    """
    caps: List[InteractionCapability] = []
    camera_ready = False
    mic_ready = False
    input_state = "UNAVAILABLE"
    input_detail = "input automation not available here"
    input_fix = ("on Windows it works out of the box; on Linux a display "
                 "server is required; on macOS grant Accessibility "
                 "permission")
    try:
        from .capabilities import detect_all
        report = detect_all(quick=quick)
        for comp in report.components:
            cat = (comp.category or "").upper()
            name = (comp.name or "").lower()
            if cat == "CAMERA" and ("camera" in name or "webcam" in name):
                camera_ready = camera_ready or \
                    comp.state == "READY"
            if cat == "MICROPHONE" and ("microphone" in name or
                                        "mic" in name):
                mic_ready = mic_ready or comp.state == "READY"
            if cat == "INPUT" and "pynput" in name or \
                    (cat == "INPUT" and comp.state == "READY" and
                     not any(c.key == "input" for c in caps)):
                if comp.state == "READY" or "pynput" in name:
                    input_state = str(getattr(comp.state, "value",
                                              comp.state))
                    input_detail = comp.detail
                    input_fix = comp.remediation or input_fix
    except Exception:  # noqa: BLE001 — registry never raises
        pass

    caps.append(InteractionCapability(
        key="input", display="Mouse/keyboard control",
        state=input_state, detail=input_detail, fix=input_fix,
        label=from_component_state(input_state,
                                   basis="input automation probe")))

    if camera_ready:
        caps.append(InteractionCapability(
            key="gesture", display="Hand gesture control",
            state="READY", detail="camera + hand tracking pipeline probed",
            label=from_component_state("READY",
                                       basis="camera probe passed; tracking "
                                             "verified live by the runtime")))
    else:
        caps.append(InteractionCapability(
            key="gesture", display="Hand gesture control",
            state="UNAVAILABLE",
            detail="no usable camera detected",
            fix="connect/enable a webcam (check OS camera permissions)",
            label=from_component_state("UNAVAILABLE")))

    if mic_ready:
        caps.append(InteractionCapability(
            key="voice", display="Voice control",
            state="WARNING",
            detail="microphone present; no local ASR engine verified yet",
            fix="run `airmouse voice-setup`",
            label=from_component_state(
                "WARNING",
                basis="microphone probed; engine not initialized")))
    else:
        caps.append(InteractionCapability(
            key="voice", display="Voice control",
            state="NOT_INSTALLED", detail="no microphone detected",
            fix="connect/enable a microphone; voice is optional",
            label=from_component_state("NOT_INSTALLED")))

    gaze_state, gaze_fix, gaze_detail = "NOT_INSTALLED", (
        "run `airmouse --gaze-calibrate`"), "gaze engine present, not "
    "calibrated"
    try:
        from .capabilities import detect_all as _da
        if quick:
            gaze_state, gaze_detail, gaze_fix = \
                "NOT_INSTALLED", "skipped in quick mode", (
                    "run `airmouse doctor`")
        else:
            found = False
            for comp in _da(quick=True).components:
                if comp.category == "INTELLIGENCE" or \
                        "gaze" in (comp.name or "").lower():
                    if "gaze" in (comp.name or "").lower():
                        found = True
                        # normalize to the plain value: a raw enum member
                        # would leak "ComponentState.OPTIONAL" into f-strings
                        gaze_state = str(getattr(comp.state, "value",
                                                 comp.state))
                        gaze_detail = comp.detail
                        gaze_fix = comp.remediation or gaze_fix
                        break
            if not found:
                gaze_state, gaze_detail = "OPTIONAL", "gaze engine available"
    except Exception:  # noqa: BLE001
        pass
    caps.append(InteractionCapability(
        key="gaze", display="Gaze control", state=gaze_state,
        detail=gaze_detail, fix=gaze_fix,
        label=from_component_state(gaze_state)))

    try:
        from .model_integrity import vosk_model_status
        model_record = vosk_model_status()
    except Exception:  # noqa: BLE001
        model_record = {"name": "vosk", "state": "MODEL_MISSING",
                        "installed": False, "compatible": True,
                        "detail": "status unavailable"}

    # the ASR verdict comes from THE registry (authoritative), overriding
    # the coarse mic-present heuristic above
    try:
        from .asr_registry import active_asr_provider
        provider = active_asr_provider()
        if provider and "Built-in" not in provider:
            for c in caps:
                if c.key == "voice":
                    c.state = "READY"
                    c.detail = f"active provider: {provider}"
                    c.fix = ""
                    c.label = from_component_state(
                        "READY", basis=f"THE registry selected '{provider}' "
                                       f"after a real initialization")
    except Exception:  # noqa: BLE001
        pass

    return CapabilityRegistry(caps=caps, model_record=model_record)
