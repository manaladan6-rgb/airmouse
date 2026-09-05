"""
airmouse.paths — THE authoritative AirMouse home/path resolver (v15.2).

Historically ~10 modules each computed their own ``~/.airmouse/...``
path, producing the "AIRMOUSE_HOME split-brain": setting the
``AIRMOUSE_HOME`` environment variable redirected some writers while
others (intelligence artifacts, calibration, gestures, macros, lecture
notes) kept writing to the literal home directory.  This module is now
the single source of truth: every storage location AirMouse uses is
resolved here.

Design rules:

1. DYNAMIC.  Every function re-reads the environment on EVERY call —
   nothing is cached at import time, because tests (and the CLI) may
   set ``AIRMOUSE_HOME`` after this module has been imported.
2. NO SIDE EFFECTS.  Resolvers never create directories or files; use
   :func:`ensure_home` (or the writers in ``airmouse.persistence`` /
   the feature modules) when creation is intended.
3. ONE precedence rule everywhere: a non-empty ``$AIRMOUSE_HOME``
   (after ``strip()``) wins, ``~``-expanded and made absolute;
   otherwise ``~/.airmouse``.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import os

__all__ = [
    "HOME_ENV",
    "airmouse_home", "ensure_home",
    "intelligence_dir", "intelligence_model_file",
    "calibration_file", "gaze_calibration_file", "gestures_file",
    "macros_dir", "model_file", "lecture_file", "tutorial_done_file",
    "config_file", "backups_dir", "exports_dir",
    "profile_dir", "onboarding_file",
    "profile_interaction_file", "profile_voice_file",
    "profile_gaze_file", "profile_gestures_file",
    "profile_preferences_file",
    "transcripts_dir",
]

#: the single environment variable that overrides the home directory
HOME_ENV = "AIRMOUSE_HOME"


def airmouse_home() -> str:
    """AirMouse home directory (resolved fresh on every call).

    ``$AIRMOUSE_HOME`` (non-empty) wins; otherwise ``~/.airmouse``.
    The result is absolute and ``~``-expanded.  Never creates anything.
    """
    raw = os.environ.get(HOME_ENV, "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    return os.path.join(os.path.expanduser("~"), ".airmouse")


def ensure_home() -> str:
    """Create the home directory if missing and return its path."""
    home = airmouse_home()
    os.makedirs(home, exist_ok=True)
    return home


def _join(*parts: str) -> str:
    """os.path.join anchored at the (freshly resolved) home directory."""
    return os.path.join(airmouse_home(), *parts)


# ---------------------------------------------------------------------------
# per-artifact resolvers (all dynamic; all creation-free)
# ---------------------------------------------------------------------------

def intelligence_dir() -> str:
    """Directory of the adaptive-intelligence artifacts."""
    return _join("intelligence")


def intelligence_model_file() -> str:
    """Serialized on-device PersonalInteractionModel (model.bin)."""
    return _join("intelligence", "model.bin")


def calibration_file() -> str:
    """AdaptiveCalibration hand reach-box statistics (calibration.json)."""
    return _join("calibration.json")


def gaze_calibration_file() -> str:
    """Gaze→screen affine calibration (gaze_calibration.json)."""
    return _join("gaze_calibration.json")


def gestures_file() -> str:
    """User's custom gesture→action mappings (gestures.json)."""
    return _join("gestures.json")


def macros_dir() -> str:
    """Directory of recorded macros (macros/<name>.json)."""
    return _join("macros")


def model_file() -> str:
    """MediaPipe hand-landmarker model downloaded on first run."""
    return _join("hand_landmarker.task")


def lecture_file() -> str:
    """Default export path for TeacherMode lecture notes (lecture.md)."""
    return _join("lecture.md")


def tutorial_done_file() -> str:
    """Marker file written when the user finishes the tutorial."""
    return _join("tutorial_done")


def config_file() -> str:
    """User settings file (config.toml)."""
    return _join("config.toml")


def backups_dir() -> str:
    """Directory holding pre-reset/pre-delete backups (kept forever)."""
    return _join("backups")


def exports_dir() -> str:
    """Directory where user-directed export bundles land by default."""
    return _join("exports")


# ---------------------------------------------------------------------------
# v16.5 — personal interaction profile + teaching state
# ---------------------------------------------------------------------------

def profile_dir() -> str:
    """Directory of the personal interaction profile (profile/)."""
    return _join("profile")


def onboarding_file() -> str:
    """Persisted onboarding/teaching state (profile/onboarding.json)."""
    return _join("profile", "onboarding.json")


def profile_interaction_file() -> str:
    """Learned interaction summary (profile/interaction.json)."""
    return _join("profile", "interaction.json")


def profile_voice_file() -> str:
    """Learned voice preferences (profile/voice.json)."""
    return _join("profile", "voice.json")


def profile_gaze_file() -> str:
    """Learned gaze preferences (profile/gaze.json)."""
    return _join("profile", "gaze.json")


def profile_gestures_file() -> str:
    """Learned gesture preferences (profile/gestures.json)."""
    return _join("profile", "gestures.json")


def profile_preferences_file() -> str:
    """Teaching/preference settings (profile/preferences.json)."""
    return _join("profile", "preferences.json")


def transcripts_dir() -> str:
    """Directory of user-saved transcription sessions (transcripts/)."""
    return _join("transcripts")
