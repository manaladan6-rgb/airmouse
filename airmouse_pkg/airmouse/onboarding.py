"""
airmouse.onboarding — Zero Learning Curve first-run (v15 §17).

Goal:  INSTALL → LAUNCH → USE.

Progressive onboarding (§17): the user picks an entry modality
(voice · hands · eyes · keyboard · automatic · all modalities);
AirMouse starts with a minimal safe setup and progressively learns
preferences (via the optional Personal Twin §2).  No dozens of
configuration steps; nothing is required beyond one choice; every
choice is changeable later.

Also verifies the §18 accessibility posture: every path has a
modality-independent confirmation setting and large-UI /
high-contrast / reduced-motion flags are architectural (data, not a
theme).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MAX_STEPS = 8


class EntryModality(enum.Enum):
    VOICE = "voice"
    HANDS = "hands"
    EYES = "eyes"
    KEYBOARD = "keyboard"
    AUTOMATIC = "automatic"
    ALL = "all"


# §17: each entry choice yields a small, safe starting profile
_START_PROFILES: Dict[str, Dict[str, Any]] = {
    "voice": {"modes": ["voice"], "confirm_style": "spoken",
              "flags": {"hud_confidence": True}},
    "hands": {"modes": ["hand"], "confirm_style": "dwell",
              "flags": {"hud_confidence": True}},
    "eyes": {"modes": ["gaze"], "confirm_style": "dwell",
             "flags": {"hud_confidence": True, "large_ui": True}},
    "keyboard": {"modes": ["keyboard"], "confirm_style": "explicit_key",
                 "flags": {"hud_confidence": False}},
    "automatic": {"modes": ["keyboard", "voice"], "confirm_style":
                  "explicit_key",
                  "flags": {"hud_confidence": True}},
    "all": {"modes": ["hand", "gaze", "voice", "keyboard"],
            "confirm_style": "explicit_key",
            "flags": {"hud_confidence": True}},
}


@dataclass
class OnboardingState:
    """Where the user is in the progressive flow."""

    step: int = 0                          # 0 = not started
    entry: str = ""
    profile: Dict[str, Any] = field(default_factory=dict)
    learned_preferences: List[str] = field(default_factory=list)
    completed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step,
            "entry": self.entry,
            "profile": dict(sorted(self.profile.items())),
            "learned_preferences": list(self.learned_preferences[-8:]),
            "completed": self.completed,
        }


class Onboarding:
    """Progressive onboarding state machine (§17)."""

    def __init__(self, twin=None) -> None:
        self.twin = twin                     # optional (§2)
        self.state = OnboardingState()

    def begin(self, entry: Any) -> Optional[Dict[str, Any]]:
        """One choice → working profile (INSTALL→LAUNCH→USE)."""
        try:
            choice = EntryModality(str(entry).strip().lower())
        except (ValueError, TypeError):
            return None
        profile = dict(_START_PROFILES[choice.value])
        self.state.step = 1
        self.state.entry = choice.value
        self.state.profile = profile
        self.state.completed = True          # usable immediately (§17)
        if self.twin is not None:
            try:
                self.twin.learn("preference", "entry_modality",
                                choice.value, source="user_explicit",
                                confidence=0.9)
            except Exception:
                pass
        return self.state.to_dict()

    def observe_preference(self, category: str, key: str, value: str) -> bool:
        """Progressive learning (§17): quiet, bounded, optional."""
        if not self.state.completed:
            return False
        tag = f"{str(category)[:20]}:{str(key)[:30]}={str(value)[:20]}"
        self.state.learned_preferences.append(tag)
        self.state.learned_preferences = \
            self.state.learned_preferences[-MAX_STEPS * 4:]
        if self.twin is not None:
            try:
                self.twin.learn(category, key, value, confidence=0.6)
            except Exception:
                pass
        return True

    def status(self) -> Dict[str, Any]:
        return self.state.to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# §18 — accessibility as an architectural property
# ─────────────────────────────────────────────────────────────────────────────

ACCESSIBILITY_MODES: tuple = (
    "voice-only", "gesture-only", "gaze-only", "keyboard-only",
    "switch-access", "hybrid", "hands-free", "low-mobility",
)

ACCESSIBILITY_FLAGS: Dict[str, bool] = {
    "configurable_confirmation": True,
    "large_ui": False,
    "high_contrast": False,
    "reduced_motion": False,
}


def accessibility_posture(mode: str) -> Dict[str, Any]:
    """Return the architectural guarantees for one accessibility mode
    (§18).  Every mode MUST map to at least one modality and a
    confirmation path — that is the architecture, not a theme."""
    mode = str(mode).strip().lower()
    if mode not in ACCESSIBILITY_MODES:
        return {"supported": False, "mode": mode}
    modality_map = {
        "voice-only": ["voice"], "gesture-only": ["hand"],
        "gaze-only": ["gaze"], "keyboard-only": ["keyboard"],
        "switch-access": ["keyboard"], "hybrid": ["voice", "hand", "gaze",
                                                   "keyboard"],
        "hands-free": ["gaze", "voice"], "low-mobility": ["voice", "gaze",
                                                           "keyboard"],
    }
    flags = dict(ACCESSIBILITY_FLAGS)
    if mode in ("gaze-only", "low-mobility", "switch-access"):
        flags["large_ui"] = True
        flags["high_contrast"] = True
        flags["reduced_motion"] = True
    return {
        "supported": True,
        "mode": mode,
        "modalities": modality_map[mode],
        "confirmation": "configurable per mode",
        "flags": flags,
    }
