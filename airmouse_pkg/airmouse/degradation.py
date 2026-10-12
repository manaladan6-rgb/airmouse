"""airmouse.degradation — the InteractionReadiness ladder (v19).

Reconstructed from the recorded v18 spec (Phase 10 of the v19 mission):
AirMouse ALWAYS tells the user which interaction modes are usable right
now and which mode the system will run in.  The ladder is a PURE
FUNCTION over observed capability booleans — no hardware probing here,
no silent fallbacks, no optimism:

    FULL            every core modality available
    DEGRADED        at least one core modality missing (named explicitly)
    SAFE_MINIMAL    only gesture/mouse remains (or nothing sensory)
    DIAGNOSTIC      nothing usable — setup/doctor still work

Design laws:

* decide() never raises and never invents capability;
* losing voice degrades but never blocks (voice is always optional);
* losing input automation (the actual click) drops to SAFE_MINIMAL with
  an explicit reason — AirMouse refuses to pretend to control a cursor
  it cannot move;
* the chosen mode is the best mode the OBSERVED capabilities support,
  and the reason is human-readable.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple

__all__ = ["ReadinessLevel", "Readiness", "decide", "interaction_mode"]


class ReadinessLevel(str, Enum):
    FULL = "FULL"
    DEGRADED = "DEGRADED"
    SAFE_MINIMAL = "SAFE_MINIMAL"
    DIAGNOSTIC = "DIAGNOSTIC"


# alias used by the capability registry / doctor rendering
ReadinessLevel.SAFE_DEGRADED = ReadinessLevel.SAFE_MINIMAL  # type: ignore[attr-defined]


@dataclass(frozen=True)
class Readiness:
    """The honest interaction verdict for one machine, right now."""

    level: ReadinessLevel
    mode: str                      # the best available interaction mode
    available: Tuple[str, ...] = ()
    missing: Tuple[str, ...] = ()
    reasons: Tuple[str, ...] = ()

    @property
    def safe_degraded(self) -> bool:
        return self.level in (ReadinessLevel.SAFE_MINIMAL,
                              ReadinessLevel.DIAGNOSTIC)

    @property
    def usable(self) -> bool:
        """True when at least one REAL interaction path exists.

        SAFE_MINIMAL is deliberately NOT usable: observation-only and
        simulation modes cannot actually operate the computer.
        """
        return self.level in (ReadinessLevel.FULL, ReadinessLevel.DEGRADED)


def decide(*, gesture: bool = False, voice: bool = False,
           gaze: bool = False, input: bool = False) -> Readiness:
    """The degradation ladder — pure, total, honest.

    ``gesture``  hand tracking + cursor pipeline usable (camera + model)
    ``voice``    a local ASR engine + microphone are actually usable
    ``gaze``     gaze engine calibrated/usable
    ``input``    OS input automation (the click itself) available
    """
    available: List[str] = []
    missing: List[str] = []
    if gesture:
        available.append("Gesture")
    else:
        missing.append("Gesture")
    if voice:
        available.append("Voice")
    else:
        missing.append("Voice")
    if gaze:
        available.append("Gaze")
    else:
        missing.append("Gaze")

    reasons: List[str] = []
    if not input:
        # no OS input automation: nothing can actually click
        if available:
            return Readiness(
                level=ReadinessLevel.SAFE_MINIMAL,
                mode="observation-only",
                available=tuple(available), missing=tuple(missing),
                reasons=("input automation unavailable — AirMouse can "
                         "observe and speak, but cannot move or click "
                         "the cursor",))
        return Readiness(
            level=ReadinessLevel.DIAGNOSTIC, mode="diagnostic",
            available=(), missing=tuple(missing),
            reasons=("no interaction hardware and no input automation — "
                     "run `airmouse doctor` for the full report",))

    if gesture and (voice or gaze):
        mode_bits = ["GESTURE"]
        if gaze:
            mode_bits.append("GAZE")
        if voice:
            mode_bits.append("VOICE")
        return Readiness(level=ReadinessLevel.FULL, mode="+".join(mode_bits),
                         available=tuple(available), missing=tuple(missing),
                         reasons=())

    if gesture:
        # gesture alone = degraded (no second modality) but fully usable
        level = ReadinessLevel.DEGRADED
        reasons.append("single-modality: gesture only (voice and gaze "
                       "unavailable or not set up)")
        return Readiness(level=level, mode="GESTURE",
                         available=tuple(available),
                         missing=tuple(missing), reasons=tuple(reasons))

    if voice or gaze:
        bits = []
        if voice:
            bits.append("VOICE")
        if gaze:
            bits.append("GAZE")
        reasons.append("no camera usable — gesture tracking is off; "
                       "running with the remaining modality only")
        return Readiness(level=ReadinessLevel.DEGRADED,
                         mode="+".join(bits),
                         available=tuple(available),
                         missing=tuple(missing), reasons=tuple(reasons))

    return Readiness(
        level=ReadinessLevel.SAFE_MINIMAL, mode="simulation",
        available=(), missing=tuple(missing),
        reasons=("no camera and no microphone usable — AirMouse starts "
                 "in simulation/diagnostic mode; nothing is faked",
                 ))


def interaction_mode(readiness: Readiness) -> str:
    """The CLI-facing interaction mode string (lowercase token)."""
    return readiness.mode.lower().replace("+", "-")
