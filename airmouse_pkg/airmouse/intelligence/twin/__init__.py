"""
airmouse.intelligence.twin — Personal Interaction Twin (v12 §2).

OPTIONAL personalization layer.  The AirMouse core NEVER imports this
package at module scope; everything here is local, offline, stdlib-only
and safe to omit.

    from airmouse.intelligence.twin import PersonalInteractionTwin

    twin = PersonalInteractionTwin()
    twin.learn("modality_preference", "click", "gaze",
               source="gesture", confidence=0.7)
    twin.explain("modality_preference", "click")
"""

from .twin import (DEFAULT_DECAY_HALF_LIFE_H, MAX_FACTS, MIN_CONFIDENCE,
                   FactSource, PersonalInteractionTwin, ProvenanceEntry,
                   TWIN_FORMAT_VERSION, TwinCategory, TwinFact, TwinStats)

__all__ = [
    "PersonalInteractionTwin", "TwinCategory", "FactSource", "TwinFact",
    "ProvenanceEntry", "TwinStats", "TWIN_FORMAT_VERSION", "MAX_FACTS",
    "MIN_CONFIDENCE", "DEFAULT_DECAY_HALF_LIFE_H",
]
