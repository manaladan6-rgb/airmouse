"""Gesture interaction profiles (v16, mission §23) — named, SAFE config presets.

A profile is a small, curated set of config overrides that reshapes how
AirMouse *feels* (latency, smoothing, deadzone, confirmation frames,
confidence floors) for a context like presenting on stage or gaming.

SAFETY MODEL — the whitelist is the contract
--------------------------------------------
``apply_profile`` can ONLY ever write keys from :data:`PROFILE_WHITELIST`.
Everything else in ``config.py`` — safety-critical flags like
``gesture_allow_destructive``, ``telemetry_enabled``, ``offline``,
``safety_level``, camera indices, ports — is structurally unreachable
from this module.  A profile cannot turn a destructive action on, cannot
disable the offline gate, and cannot change what data leaves the machine
(nothing leaves the machine anyway).

Every value is validated at apply time (type + finite + range):
unknown keys, missing values, NaN/inf, out-of-range numbers and
wrong types are all REFUSED with an honest message — never clamped,
never silently coerced.

Persistence uses exactly the mechanism the rest of the codebase uses
(setup_wizard._step_config, privacy.report): ``Config.load()`` +
``Config.save_defaults()`` inside ``persistence.config_path_scope()``
so the file operated on is ``paths.config_file()``
(``$AIRMOUSE_HOME/config.toml`` or ``~/.airmouse/config.toml``).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import math
import os

__all__ = ["PROFILES", "PROFILE_WHITELIST", "list_profiles",
           "get_profile", "apply_profile"]


# ---------------------------------------------------------------------------
# The whitelist — the ONLY config keys a profile may touch
# ---------------------------------------------------------------------------

#: Config-attribute names a profile is allowed to override.
PROFILE_WHITELIST = frozenset({
    "tracking_mode",                  # "direct" | "ironman"
    "deadzone",                       # ironman noise gate (normalized units)
    "exp_power",                      # ironman response curve power
    "exp_scale",                      # ironman overall sensitivity
    "gesture_confirm_frames",         # frames to confirm movement gestures
    "gesture_action_confirm_frames",  # frames to confirm action gestures
    "gesture_min_confidence_safe",    # spine confidence floor, SAFE class
    "gesture_min_confidence_caution", # spine confidence floor, CAUTION class
    "audio_enabled",                  # sound feedback on/off
    "adaptive_calibration",           # learn reach-box/tremor on the fly
    "two_hand",                       # v15.2 two-hand tracking
    "position_smooth_alpha",          # final output smoothing (1=responsive)
})

# per-key validation: (python type(s), lo, hi) — hi/lo inclusive; None = open
_VALUE_SPECS = {
    "tracking_mode":                  (("direct", "ironman"), None, None),
    "deadzone":                       (float, 0.0, 0.2),
    "exp_power":                      (float, 0.1, 2.0),
    "exp_scale":                      (float, 0.5, 10.0),
    "gesture_confirm_frames":         (int, 1, 30),
    "gesture_action_confirm_frames":  (int, 1, 30),
    "gesture_min_confidence_safe":    (float, 0.0, 1.0),
    "gesture_min_confidence_caution": (float, 0.0, 1.0),
    "audio_enabled":                  (bool, None, None),
    "adaptive_calibration":           (bool, None, None),
    "two_hand":                       (bool, None, None),
    "position_smooth_alpha":          (float, 0.0, 1.0),
}

assert set(_VALUE_SPECS) == PROFILE_WHITELIST, (
    "value specs and whitelist must stay in lockstep")


# ---------------------------------------------------------------------------
# The profiles — documented, factory-mirrored, internally consistent
# ---------------------------------------------------------------------------

#: Named profiles.  Every value stays inside the whitelist; every float
#: is finite; ``default`` mirrors config.py's class defaults exactly so
#: applying it is a true within-whitelist factory reset.
PROFILES: dict = {
    # Factory defaults (config.py v3.2 direct-tracking baseline).
    "default": {
        "tracking_mode": "direct",
        "deadzone": 0.008,
        "exp_power": 0.6,
        "exp_scale": 3.0,
        "gesture_confirm_frames": 3,
        "gesture_action_confirm_frames": 4,
        "gesture_min_confidence_safe": 0.45,
        "gesture_min_confidence_caution": 0.60,
        "audio_enabled": True,
        "adaptive_calibration": True,
        "two_hand": False,
        "position_smooth_alpha": 0.75,
    },
    # Tight deadzone + snappier confirms + quiet audio for long coding
    # sessions; confidence floors stay at factory values.
    "developer": {
        "tracking_mode": "direct",
        "deadzone": 0.004,
        "exp_power": 0.6,
        "exp_scale": 3.5,
        "gesture_confirm_frames": 2,
        "gesture_action_confirm_frames": 3,
        "gesture_min_confidence_safe": 0.45,
        "gesture_min_confidence_caution": 0.60,
        "audio_enabled": False,
        "adaptive_calibration": True,
        "two_hand": False,
        "position_smooth_alpha": 0.85,
    },
    # Stage mode: cautious confidence floors + slower confirms so a
    # nervous hand cannot misfire in front of an audience; audio off.
    "presentation": {
        "tracking_mode": "direct",
        "deadzone": 0.012,
        "exp_power": 0.6,
        "exp_scale": 2.5,
        "gesture_confirm_frames": 4,
        "gesture_action_confirm_frames": 6,
        "gesture_min_confidence_safe": 0.60,
        "gesture_min_confidence_caution": 0.75,
        "audio_enabled": False,
        "adaptive_calibration": True,
        "two_hand": False,
        "position_smooth_alpha": 0.60,
    },
    # Lowest latency: minimal confirms, responsive smoothing, fixed
    # (non-adaptive) calibration so nothing drifts mid-match.
    "gaming": {
        "tracking_mode": "direct",
        "deadzone": 0.003,
        "exp_power": 0.6,
        "exp_scale": 3.5,
        "gesture_confirm_frames": 2,
        "gesture_action_confirm_frames": 2,
        "gesture_min_confidence_safe": 0.45,
        "gesture_min_confidence_caution": 0.60,
        "audio_enabled": True,
        "adaptive_calibration": False,
        "two_hand": False,
        "position_smooth_alpha": 0.90,
    },
    # Tremor-tolerant: larger deadzone, much higher confirm frames,
    # higher confidence floors, softer smoothing, audio cues ON.
    "accessibility": {
        "tracking_mode": "direct",
        "deadzone": 0.02,
        "exp_power": 0.6,
        "exp_scale": 2.5,
        "gesture_confirm_frames": 6,
        "gesture_action_confirm_frames": 8,
        "gesture_min_confidence_safe": 0.55,
        "gesture_min_confidence_caution": 0.70,
        "audio_enabled": True,
        "adaptive_calibration": True,
        "two_hand": False,
        "position_smooth_alpha": 0.55,
    },
    # Couch/sofa use: moderate confirms, audio ticks on for eyes-free
    # feedback, slightly raised floors for bouncy lap postures.
    "media": {
        "tracking_mode": "direct",
        "deadzone": 0.01,
        "exp_power": 0.6,
        "exp_scale": 3.0,
        "gesture_confirm_frames": 3,
        "gesture_action_confirm_frames": 5,
        "gesture_min_confidence_safe": 0.50,
        "gesture_min_confidence_caution": 0.65,
        "audio_enabled": True,
        "adaptive_calibration": True,
        "two_hand": False,
        "position_smooth_alpha": 0.70,
    },
    # Design work: very tight deadzone + fine sensitivity for pixel
    # work; drags (palm/pinch-hold) confirm a touch slower to hold.
    "creative": {
        "tracking_mode": "direct",
        "deadzone": 0.002,
        "exp_power": 0.6,
        "exp_scale": 2.0,
        "gesture_confirm_frames": 3,
        "gesture_action_confirm_frames": 5,
        "gesture_min_confidence_safe": 0.50,
        "gesture_min_confidence_caution": 0.65,
        "audio_enabled": True,
        "adaptive_calibration": True,
        "two_hand": False,
        "position_smooth_alpha": 0.80,
    },
    # Mobility-first: two-hand geometry ON, audio on, generous deadzone
    # and confirms — pairs with modes.ACCESSIBILITY_PROFILES chains.
    "hands_free": {
        "tracking_mode": "direct",
        "deadzone": 0.018,
        "exp_power": 0.6,
        "exp_scale": 2.5,
        "gesture_confirm_frames": 5,
        "gesture_action_confirm_frames": 7,
        "gesture_min_confidence_safe": 0.55,
        "gesture_min_confidence_caution": 0.70,
        "audio_enabled": True,
        "adaptive_calibration": True,
        "two_hand": True,
        "position_smooth_alpha": 0.60,
    },
}


# ---------------------------------------------------------------------------
# validation (fail-closed, NaN/inf refused, never clamped)
# ---------------------------------------------------------------------------

def _validate_value(key: str, value) -> str | None:
    """Return None when ``value`` is a legal override for ``key``,
    else an honest refusal reason."""
    if key not in PROFILE_WHITELIST:
        return (f"key '{key}' is outside the profile whitelist — "
                f"allowed keys: {', '.join(sorted(PROFILE_WHITELIST))}")
    spec = _VALUE_SPECS[key]
    kinds, lo, hi = spec
    if isinstance(kinds, tuple):          # enum (strings)
        if not isinstance(value, str) or value not in kinds:
            return (f"'{key}' must be one of {', '.join(kinds)}, "
                    f"got {value!r}")
        return None
    if kinds is bool:
        if not isinstance(value, bool):
            return f"'{key}' must be true or false, got {value!r}"
        return None
    # numeric
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return (f"'{key}' must be a number, got {type(value).__name__} "
                f"({value!r})")
    f = float(value)
    if not math.isfinite(f):              # NaN / +inf / -inf
        return f"'{key}' must be a finite number, got {value!r}"
    if kinds is int and int(value) != f:
        return f"'{key}' must be a whole number, got {value!r}"
    if lo is not None and f < lo or hi is not None and f > hi:
        return f"'{key}' must be in [{lo}, {hi}], got {value!r}"
    return None


def _validate_profile(name: str, overrides: dict) -> str | None:
    """Validate a whole profile dict; return refusal reason or None."""
    if not isinstance(overrides, dict):
        return f"profile '{name}' is malformed (expected settings dict)"
    if not overrides:
        return None                        # an empty profile is legal
    for key, value in overrides.items():
        reason = _validate_value(key, value)
        if reason is not None:
            return reason
    # cross-key sanity: a profile must never set safe floor above caution
    safe = overrides.get("gesture_min_confidence_safe")
    caution = overrides.get("gesture_min_confidence_caution")
    if safe is not None and caution is not None and safe > caution:
        return (f"profile '{name}' is incoherent: safe confidence floor "
                f"{safe} exceeds caution floor {caution}")
    return None


# validate the shipped profiles ONCE at import — a corrupt shipped profile
# is a programming error and must be visible immediately
for _n, _o in PROFILES.items():
    _bad = _validate_profile(_n, _o)
    if _bad:
        raise RuntimeError(f"shipped profile broken: {_bad}")
del _n, _o, _bad


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def list_profiles() -> list:
    """Sorted names of all available gesture profiles."""
    return sorted(PROFILES)


def get_profile(name: str) -> dict | None:
    """Return a COPY of the named profile's overrides, or None.

    Pure data accessor for tests, docs and the settings GUI —
    never touches the config file.
    """
    overrides = PROFILES.get(str(name or "").strip().lower())
    return dict(overrides) if overrides is not None else None


def apply_profile(name: str) -> tuple:
    """Apply a named profile to the user's config file.

    Loads the config via ``Config.load()`` inside
    ``persistence.config_path_scope()`` (so the file is
    ``paths.config_file()``), applies ONLY whitelisted overrides,
    validates every value (type/finite/range — unknown keys, NaN,
    out-of-range and wrong types are refused), saves via
    ``Config.save_defaults()`` and reports honestly.

    Returns ``(ok: bool, msg: str)``.  Never raises.
    """
    key = str(name or "").strip().lower()
    if not key:
        return False, ("no profile given — available profiles: "
                       + ", ".join(list_profiles()))
    if key not in PROFILES:
        return False, (f"unknown profile '{key}' — available profiles: "
                       + ", ".join(list_profiles()))
    overrides = PROFILES[key]

    # fail-closed validation BEFORE anything is written
    reason = _validate_profile(key, overrides)
    if reason is not None:
        return False, f"profile '{key}' refused: {reason}"

    try:
        from .persistence import config_path_scope
        from .config import Config

        with config_path_scope() as cfg_path:
            cfg = Config()
            cfg.load()                      # keep every current setting
            for k, v in overrides.items():  # whitelist enforced above
                setattr(cfg, k, v)
            cfg.save_defaults()             # config.py's own writer
            written = os.path.exists(cfg_path)

        if not written:                     # e.g. no tomllib on this python
            return False, (f"profile '{key}' NOT applied — the config "
                           f"writer produced no file at {cfg_path} "
                           "(tomllib unavailable?)")

        if overrides:
            body = (f"profile '{key}' applied: {len(overrides)} settings "
                    f"-> {cfg_path}")
        else:
            body = (f"profile '{key}' applied: 0 settings "
                    f"(factory defaults within the whitelist) -> {cfg_path}")
        return True, body
    except Exception as exc:                # never raise from a profile op
        return False, (f"profile '{key}' could not be applied: "
                       f"{type(exc).__name__}: {exc}")
