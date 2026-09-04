"""
airmouse.intelligence — OPTIONAL adaptive intelligence plugin (v11.5).

This subpackage is the ``airmouse-intelligence`` plugin architecture
referred to in the v11.5 mission.  It is OPTIONAL by contract:

* the AirMouse core NEVER imports this package at module scope
* the core functions normally when this package is not installed,
  disabled, unavailable, corrupted, incompatible, out of memory,
  disabled for privacy, or disabled for performance
* everything here is LOCAL, OFFLINE and stdlib-only (no cloud, no
  mandatory internet, no heavy third-party deps)

Public facade: :class:`IntelligencePlugin` (see ``plugin.py``).

DESIGN RULES
------------
1.  No component may raise through the plugin boundary.  Every public
    entry point catches exceptions and degrades to a documented no-op.
2.  Learning stores PATTERNS, not private content.  Passwords, tokens,
    credentials, private files, full conversations, arbitrary clipboard
    and sensitive document contents are scrubbed before they can be
    persisted (see ``memory.scrub_pattern``).
3.  ``PREDICTION != EXECUTION`` — nothing in this package executes
    computer actions.  Predictions are data for the intent/safety layers
    and the HUD.  Destructive operations always require the v10 safety
    confirmations.
4.  All stores are BOUNDED (hard resource limits) and deterministic
    (sorted serialization, no wall-clock jitter in logic paths).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

__all__ = [
    "IntelligenceState",
    "MODEL_FORMAT_MAGIC",
    "MODEL_FORMAT_VERSION",
    "MODEL_CAPACITY_BYTES_DEFAULT",
    "PersonalInteractionModel",
    "NGramModel",
    "ActionMarkov",
    "CommandModel",
    "EmojiModel",
    "FeatureWeights",
    "ModelError",
]

import enum


class IntelligenceState(enum.Enum):
    """Lifecycle/health states reported by the plugin facade.

    The core treats every state other than AVAILABLE as "intelligence is
    not contributing" — and MUST keep working regardless.
    """

    AVAILABLE = "available"
    DISABLED = "disabled"                # user/config disabled
    UNAVAILABLE = "unavailable"          # plugin missing / not installed
    CORRUPTED = "corrupted"              # stored artifacts failed to load
    INCOMPATIBLE = "incompatible"        # artifact version mismatch
    OUT_OF_MEMORY = "out_of_memory"      # resource limits exceeded
    PRIVACY_PAUSED = "privacy_paused"    # learning paused for privacy
    LEARNING_PAUSED = "learning_paused"  # temporary learning pause


MODEL_FORMAT_MAGIC = b"AIMM"
MODEL_FORMAT_VERSION = 1
MODEL_CAPACITY_BYTES_DEFAULT = 30 * 1024 * 1024  # ~30 MB budget (§5)


# lazy attribute exports keep import time minimal
def __getattr__(name):  # PEP 562
    if name == "PersonalInteractionModel":
        from .model import PersonalInteractionModel
        return PersonalInteractionModel
    if name == "NGramModel":
        from .model import NGramModel
        return NGramModel
    if name == "ActionMarkov":
        from .model import ActionMarkov
        return ActionMarkov
    if name == "CommandModel":
        from .model import CommandModel
        return CommandModel
    if name == "EmojiModel":
        from .model import EmojiModel
        return EmojiModel
    if name == "FeatureWeights":
        from .model import FeatureWeights
        return FeatureWeights
    if name == "ModelError":
        from .model import ModelError
        return ModelError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
