"""airmouse.evidence — the Reality × Epistemic evidence model (v19).

Reconstructed from the recorded v17/v18 spec: every capability carries a
TWO-PART label that may never be collapsed into one word:

    Reality      — what exists in the world
                   REAL / SIMULATED / OPTIONAL / PHYSICAL_TEST_REQUIRED /
                   NOT_AVAILABLE
    Epistemic    — how we know what we know
                   OBSERVED / INFERRED / PREDICTED / UNKNOWN

Laws (enforced by construction):

* the pair is one object; there is no API that returns "just REAL";
* packaging/UX code can NEVER upgrade a label (no ``REAL because the
  code exists``); the only lawful transition into REAL is a successful
  runtime probe (OBSERVED) on this machine;
* SIMULATED things remain SIMULATED no matter how polished the UX is;
* anything requiring physical hardware that this machine cannot probe
  stays PHYSICAL_TEST_REQUIRED (release view) — the runtime view simply
  reports what could and could not be observed HERE.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

__all__ = ["Reality", "Epistemic", "EvidenceLabel", "from_component_state"]


class Reality(str, Enum):
    """What exists in the world (the release/runtime honesty axis)."""

    REAL = "REAL"
    SIMULATED = "SIMULATED"
    OPTIONAL = "OPTIONAL"
    PHYSICAL_TEST_REQUIRED = "PHYSICAL_TEST_REQUIRED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class Epistemic(str, Enum):
    """How we know (the knowledge axis — never merged with Reality)."""

    OBSERVED = "OBSERVED"          # probed successfully on THIS machine
    INFERRED = "INFERRED"          # derived from configuration/environment
    PREDICTED = "PREDICTED"        # claimed ahead of any probe
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EvidenceLabel:
    """The indivisible two-part honesty label."""

    reality: Reality
    epistemic: Epistemic
    basis: str = ""                # one human line: WHY this label

    def __str__(self) -> str:      # the ONLY rendering — always both parts
        head = f"{self.reality.value}/{self.epistemic.value}"
        return f"{head} ({self.basis})" if self.basis else head


def from_component_state(state: str, *, basis: str = "",
                         optional: bool = False) -> EvidenceLabel:
    """Honest mapping from a runtime ComponentState to a label.

    The runtime can OBSERVE (probes ran here) — it can never claim
    hardware-tested beyond what the probe touched, and it can never
    promote a simulated or missing capability to REAL.
    """
    state = (state or "").upper()
    if state == "READY":
        return EvidenceLabel(Reality.REAL, Epistemic.OBSERVED,
                             basis or "runtime probe passed on this machine")
    if state in ("HARDWARE",):
        return EvidenceLabel(Reality.PHYSICAL_TEST_REQUIRED,
                             Epistemic.INFERRED,
                             basis or "needs real hardware to verify")
    if state in ("OPTIONAL", "NOT_INSTALLED", "ENHANCEMENT", "UNAVAILABLE") \
            or optional:
        return EvidenceLabel(Reality.OPTIONAL, Epistemic.OBSERVED
                             if state == "OPTIONAL" else Epistemic.INFERRED,
                             basis or "optional; absence is not a failure")
    if state in ("SIMULATED",):
        return EvidenceLabel(Reality.SIMULATED, Epistemic.OBSERVED,
                             basis or "simulated implementation observed")
    if state in ("INCOMPATIBLE",):
        return EvidenceLabel(Reality.NOT_AVAILABLE, Epistemic.OBSERVED,
                             basis or "incompatible with this machine")
    # FAILED / WARNING / anything unknown
    return EvidenceLabel(Reality.NOT_AVAILABLE, Epistemic.UNKNOWN,
                         basis or "probe did not succeed")
