"""
airmouse.licensing — Commercial Platform Architecture (v15 §19).

Product boundaries WITHOUT compromising the open/local core:

    FREE · PRO · DEVELOPER · ENTERPRISE · SDK · MARKETPLACE · HARDWARE

HARD RULES (§19):
    * NO artificial crippling of the free core — the FREE tier is the
      complete local interaction platform.
    * Licensing is transparent (inspectable state), local (no phone-
      home), testable (deterministic), revocable and privacy-
      respecting.
    * Extension points exist for premium workflows, advanced
      transcription/intelligence, enterprise management, developer
      SDK, marketplace skills, hardware integrations and OEM
      licensing — as DATA, not dark patterns.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MAX_FEATURES = 64
MAX_NOTE = 120


class Tier(enum.Enum):
    FREE = "free"
    PRO = "pro"
    DEVELOPER = "developer"
    ENTERPRISE = "enterprise"
    SDK = "sdk"
    MARKETPLACE = "marketplace"
    HARDWARE = "hardware"


# §19 tier -> extra capabilities.  The FREE set is intentionally the
# WHOLE local core (everything below); higher tiers add, never subtract.
CORE_FEATURES: Tuple[str, ...] = (
    "core.hand", "core.gaze", "core.voice", "core.keyboard",
    "core.browser", "core.offline", "core.click", "core.type",
    "core.files", "core.macros", "core.skills", "core.tasks",
    "core.agents", "core.aip", "core.world_model", "core.twin",
    "core.recovery", "core.transcription", "core.dictation",
    "core.prediction", "core.privacy",
)

TIER_FEATURES: Dict[str, Tuple[str, ...]] = {
    "free": CORE_FEATURES,   # complete local core — nothing crippled
    "pro": ("premium.workflows", "advanced.transcription",
            "advanced.intelligence"),
    "developer": ("developer.sdk", "local.simulator", "test.harness"),
    "enterprise": ("enterprise.management", "policy.packs",
                   "audit.export"),
    "sdk": ("sdk.redistribute",),
    "marketplace": ("marketplace.publish", "marketplace.install"),
    "hardware": ("hardware.integrations", "oem.licensing"),
}

TIER_RANK = {"free": 0, "pro": 1, "developer": 2, "enterprise": 3,
             "sdk": 2, "marketplace": 1, "hardware": 1}


@dataclass
class LicenseState:
    """Local, transparent, revocable license state (§19)."""

    tier: str = Tier.FREE.value
    license_key: str = ""          # opaque token, validated locally
    issued_to: str = ""
    issued_wall: str = ""
    expires_wall: str = ""         # empty = no expiry
    revoked: bool = False
    features: Tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "issued_to": self.issued_to[:60],
            "issued_wall": self.issued_wall,
            "expires_wall": self.expires_wall,
            "revoked": self.revoked,
            "features": list(self.features),
            "note": self.note[:MAX_NOTE],
            "local_only": True,          # structural honesty (§19)
            "phones_home": False,
        }


class CapabilityLicensing:
    """Deterministic, local, testable capability/licensing gate."""

    def __init__(self) -> None:
        self._state = self._free_state()

    @staticmethod
    def _free_state() -> LicenseState:
        return LicenseState(tier=Tier.FREE.value, issued_to="local user",
                            features=TIER_FEATURES["free"],
                            note="complete local core — nothing crippled")

    # ── lifecycle (§19: transparent, local, testable, revocable) ────────

    def activate(self, tier: Any, license_key: str = "",
                 issued_to: str = "", expires_wall: str = "") -> bool:
        try:
            t = Tier(str(tier).strip().lower())
        except (ValueError, TypeError):
            return False
        key = str(license_key or "").strip()
        if t is not Tier.FREE and len(key) < 8:
            return False            # non-free needs a local key
        self._state = LicenseState(
            tier=t.value, license_key=key[:64], issued_to=str(
                issued_to or "local user")[:60],
            issued_wall=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            expires_wall=str(expires_wall or "")[:20],
            features=TIER_FEATURES[t.value],
            note=f"activated locally; no telemetry")
        return True

    def revoke(self) -> None:
        """Revocation is instant and local (§19)."""
        self._state = self._free_state()
        self._state.note = "license revoked — free core continues fully"

    def state(self) -> Dict[str, Any]:
        return self._state.to_dict()

    # ── capability checks (§19 extension points) ────────────────────────

    def has_feature(self, feature: str) -> bool:
        feature = str(feature)[:MAX_NOTE]
        if self._state.revoked:
            return feature in TIER_FEATURES["free"]
        if feature in TIER_FEATURES["free"]:
            return True
        if feature in self._state.features:
            return True
        # rank-compatibility: an enterprise activation includes pro
        tier = self._state.tier
        for other, feats in TIER_FEATURES.items():
            if feature in feats and TIER_RANK.get(tier, 0) >= \
                    TIER_RANK.get(other, 9):
                return True
        return False

    def capability_matrix(self) -> Dict[str, Any]:
        """Full transparency surface: what each tier adds (§19)."""
        return {
            "tiers": {tier: list(feats) for tier, feats in
                      TIER_FEATURES.items()},
            "current": self.state(),
            "free_core_complete": True,
            "dark_patterns": "none",
        }
