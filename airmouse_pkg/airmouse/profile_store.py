"""
airmouse.profile_store — the v16.5 Personal Interaction Profile
(mission §15) and the persistent bookkeeping for the §16 learning loop.

WHAT THIS MODULE IS
-------------------
The v16.5 mission is for AirMouse to learn how THIS human communicates:
gestures, gaze behaviour, voice commands, speech patterns, preferences,
confidence thresholds, frequent actions and workflows.  The adaptive
learners themselves live in ``airmouse.intelligence.personalization``
(in-memory EMA/counter profiles); THIS module is the persistent FILE
layer + learning-loop bookkeeping underneath them — not a duplicate
learner:

    profile/interaction.json   frequent intents, confidence
                               preferences, workflows, corrections
    profile/voice.json         command counts, phrase aliases,
                               dictation stats
    profile/gestures.json      gesture counts, preferred confidence,
                               temporal-event counts
    profile/preferences.json   hint level, preferred modalities,
                               teach reminders

All four files live under ``<home>/profile/`` — an artifact already
inventoried by ``airmouse.privacy.PRIVACY_MANIFEST`` (entry
``personal_profile``, ``user_learning=True``) and therefore covered by
the ``memory_reset`` / ``memory_delete`` / ``memory_export`` lifecycle
in ``airmouse.persistence``.  This module mirrors that lifecycle
pattern (atomic byte-copy backup, then defaults) without modifying it.

PRIVACY CONTRACT (hard rules)
-----------------------------
1.  LOCAL-FIRST.  Everything is written under the AirMouse home
    directory.  No network code, no sync, no upload, no telemetry —
    there is no cloud path.
2.  CONTENT-FREE.  The profile files hold bounded counters and learned
    parameters ONLY: no transcript text, no typed content, no raw
    audio, no images, no video.  Raw mic/camera streams are never
    stored (no code path here could store them).  Key strings are
    truncated to 64 characters and every collection is size-capped.
3.  USER-CONTROLLED.  ``ProfileStore.reset()`` / ``PersonalProfile
    .reset_all()`` mirror the persistence lifecycle: backup the current
    file to ``<home>/backups/profile-<name>-<unixts>.json`` (atomic
    byte copy) and then restore defaults.  ``export_all()`` writes a
    JSON bundle only where the user directs it (user-directed export
    flows only).
4.  PREDICTION ≠ EXECUTION.  The §16 closed learning loop
    (SENSE→RECOGNIZE→CONTEXT→PREDICT→PROPOSE→APPROVE→ACTION→OBSERVE→
    VERIFY→LEARN→ADAPT) may only SUGGEST.  ``LearningLoop.propose()``
    creates pending proposals that NEVER execute anything — they only
    surface to the UI/CLI.  ``approve()`` is an explicit, separate
    step (auto-approval is structurally impossible: only
    ``approve(<existing pending id>)`` can mark a proposal approved).
    ``adapt()`` converts an explicitly user-approved proposal into one
    bounded profile observation via ``learn_event(verified=True)`` —
    nothing else mutates state, and no action, macro, or automation is
    ever created or run by this module.
5.  SPINE GUARANTEES STAND.  E-STOP and human override are enforced at
    the gesture-spine level (``airmouse.gesture_spine``); nothing here
    bypasses or reimplements them.

Unverified observations (the sensor did not confirm the interaction)
are counted in a separate ``unverified.`` namespace and are NEVER used
for preference suggestions — consumers must ignore any category whose
name starts with ``unverified.`` (see ``is_unverified_category``).

Everything here is deterministic, bounded, thread-safe (locks around
all mutations) and stdlib-only.  No prints at import.  Every
filesystem method is fail-closed: it returns an honest result instead
of raising.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import copy
import datetime
import itertools
import json
import math
import os
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import paths
from . import persistence

__all__ = [
    "SCHEMA_VERSION",
    "PROFILE_FILES", "DEFAULTS", "BOUNDS",
    "MAX_COUNTER_KEYS", "MAX_LIST_ITEMS", "MAX_STR",
    "STAGES", "MODALITIES", "KINDS",
    "MODALITY_STORE", "KIND_CATEGORY", "UNVERIFIED_PREFIX",
    "is_unverified_category",
    "ProfileStore", "PersonalProfile", "LearningLoop",
]


# ---------------------------------------------------------------------------
# schema constants (paths + defaults resolved DYNAMICALLY)
# ---------------------------------------------------------------------------

#: profile schema version written into every file
SCHEMA_VERSION = 1

#: bounds: default caps applied by :meth:`ProfileStore.bounds`
MAX_COUNTER_KEYS = 512      # max keys kept in a counter dict
MAX_LIST_ITEMS = 32         # lists are trimmed to their LAST 32 items
MAX_STR = 64                # keys / short string fields truncated to 64
_MAX_PRE_SORT_ITEMS = 65536  # memory guard before sorting a counter dict
_MAX_DICT_DEPTH = 8          # recursion guard for untrusted JSON
_MAX_TOP_ITEMS = 16          # LearningLoop: max fields kept per ring event
_MAX_SUGGESTION_KEYS = 32    # LearningLoop: max keys kept per suggestion

#: name -> path RESOLVER (a zero-arg function, called on EVERY use so
#: that ``$AIRMOUSE_HOME`` may change after import).
PROFILE_FILES: Dict[str, Callable[[], str]] = {
    "interaction": paths.profile_interaction_file,
    "voice": paths.profile_voice_file,
    "gestures": paths.profile_gestures_file,
    "preferences": paths.profile_preferences_file,
}

#: name -> default document.  Contents are bounded and content-free
#: (counters/parameters only — no transcript text, no typed content,
#: no media).  ``_defaults()`` hands out deep copies.
DEFAULTS: Dict[str, dict] = {
    "interaction": {
        "schema_version": SCHEMA_VERSION,
        "frequent_intents": {},
        "confidence_preferences": {},
        "workflows": {},
        "corrections": {},
        "total_observations": 0,
        "updated_at": None,
    },
    "voice": {
        "schema_version": SCHEMA_VERSION,
        "command_counts": {},
        "phrase_aliases": {},
        "dictation_stats": {"sessions": 0, "chars": 0},
        "updated_at": None,
    },
    "gestures": {
        "schema_version": SCHEMA_VERSION,
        "gesture_counts": {},
        "preferred_confidence": None,
        "temporal_events": {},
        "updated_at": None,
    },
    "preferences": {
        "schema_version": SCHEMA_VERSION,
        "hint_level": "normal",
        "preferred_modalities": [],
        "teach_reminders": True,
        "updated_at": None,
    },
}

#: field -> bound spec.  ``max_keys`` caps a dict (counter dicts keep
#: the highest-count entries); ``max_items`` caps a list (last items
#: kept); ``max_len`` caps a string; ``clamp`` is a (lo, hi) ratio
#: range applied to numeric values of the field (scalar or per-key).
#: Fields NOT declared here fall back to the generic caps
#: (512 dict keys / 32 list items / 64 chars).
BOUNDS: Dict[str, Dict[str, Any]] = {
    "frequent_intents": {"max_keys": MAX_COUNTER_KEYS},
    "confidence_preferences": {"max_keys": MAX_COUNTER_KEYS,
                               "clamp": (0.0, 1.0)},
    "workflows": {"max_keys": MAX_COUNTER_KEYS},
    "corrections": {"max_keys": MAX_COUNTER_KEYS},
    "command_counts": {"max_keys": MAX_COUNTER_KEYS},
    "phrase_counts": {"max_keys": MAX_COUNTER_KEYS},
    "phrase_aliases": {"max_keys": MAX_COUNTER_KEYS},
    "dictation_stats": {"max_keys": 16},
    "gesture_counts": {"max_keys": MAX_COUNTER_KEYS},
    "temporal_events": {"max_keys": MAX_COUNTER_KEYS},
    "preferred_confidence": {"clamp": (0.0, 1.0)},
    "preferred_modalities": {"max_items": MAX_LIST_ITEMS},
    "hint_level": {"max_len": MAX_STR},
}


def _defaults(name: str) -> dict:
    """A fresh deep copy of the default document for ``name``."""
    return copy.deepcopy(DEFAULTS[name])


# ---------------------------------------------------------------------------
# learning-loop vocabulary + learn_event routing
# ---------------------------------------------------------------------------

#: the §16 closed-loop stage names, in pipeline order
STAGES: Tuple[str, ...] = (
    "SENSE", "RECOGNIZE", "CONTEXT", "PREDICT", "PROPOSE", "APPROVE",
    "ACTION", "OBSERVE", "VERIFY", "LEARN", "ADAPT",
)

#: modalities accepted by :meth:`PersonalProfile.learn_event`
MODALITIES: Tuple[str, ...] = ("gesture", "voice", "gaze", "fusion")

#: event kinds accepted by :meth:`PersonalProfile.learn_event`
KINDS: Tuple[str, ...] = ("intent", "command", "phrase", "correction",
                          "temporal")

#: modality -> profile store that owns the observation
MODALITY_STORE: Dict[str, str] = {
    "gesture": "gestures",
    "voice": "voice",
    "gaze": "interaction",
    "fusion": "interaction",
}

#: event kind -> canonical counter category
KIND_CATEGORY: Dict[str, str] = {
    "intent": "frequent_intents",
    "command": "command_counts",
    "phrase": "phrase_counts",
    "correction": "corrections",
    "temporal": "temporal_events",
}

#: category -> the store that category belongs to
_CATEGORY_STORE: Dict[str, str] = {
    "frequent_intents": "interaction",
    "corrections": "interaction",
    "command_counts": "voice",
    "phrase_counts": "voice",
    "gesture_counts": "gestures",
    "temporal_events": "gestures",
}

#: per-store fallback category when the kind's canonical category does
#: not belong to the modality's store (e.g. a "command" kind observed
#: through the gesture modality lands in ``gesture_counts``)
_MODALITY_FALLBACK: Dict[str, str] = {
    "gestures": "gesture_counts",
    "voice": "command_counts",
    "interaction": "frequent_intents",
}

#: prefix of the namespace for observations the sensor did NOT verify;
#: categories with this prefix are never used for preference suggestions
UNVERIFIED_PREFIX = "unverified."


def is_unverified_category(category: str) -> bool:
    """True when ``category`` is inside the unverified namespace."""
    return str(category or "").startswith(UNVERIFIED_PREFIX)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _stamp(now: Optional[Any]) -> str:
    """ISO-8601 UTC timestamp: now (default), unix seconds, or passthrough."""
    if now is None:
        return _utcnow_iso()
    if isinstance(now, bool):
        return _utcnow_iso()
    if isinstance(now, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(
                float(now), datetime.timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return _utcnow_iso()
    return str(now)[:MAX_STR]


def _is_number(v: Any) -> bool:
    """True for real int/float (bool excluded)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v: float) -> bool:
    return not isinstance(v, bool) and math.isfinite(float(v))


def _type_ok(default_value: Any, value: Any) -> bool:
    """May ``value`` replace ``default_value`` in a loaded document?"""
    if default_value is None:          # free-form slot (updated_at, ...)
        return (value is None or isinstance(value, (str, int, float, bool,
                                                    dict, list)))
    if isinstance(default_value, bool):
        return isinstance(value, bool)
    if isinstance(default_value, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default_value, float):
        return _is_number(value)
    if isinstance(default_value, str):
        return isinstance(value, str)
    if isinstance(default_value, dict):
        return isinstance(value, dict)
    if isinstance(default_value, list):
        return isinstance(value, list)
    return False


def _bound_document(doc: Any, depth: int = 0) -> dict:
    """Bound a whole profile document: generic caps on the top-level
    mapping plus the per-field specs from :data:`BOUNDS` on every
    field (``max_keys`` / ``max_items`` / ``max_len`` / ``clamp``).
    Never raises; unsupported values collapse to ``None``."""
    if not isinstance(doc, dict) or depth > _MAX_DICT_DEPTH:
        return {}
    raw_items = list(doc.items())
    if len(raw_items) > _MAX_PRE_SORT_ITEMS:
        raw_items = raw_items[:_MAX_PRE_SORT_ITEMS]
    items = [(str(k)[:MAX_STR], v) for k, v in raw_items]
    if len(items) > MAX_COUNTER_KEYS:
        items = items[:MAX_COUNTER_KEYS]     # top level: first-seen fields
    out: Dict[str, Any] = {}
    for k, v in items:
        out[k] = _bound_value(v, BOUNDS.get(k, {}), depth + 1)
    return out


def _bound_value(value: Any, spec: Dict[str, Any], depth: int) -> Any:
    """Recursively bound ``value`` according to ``spec`` (see BOUNDS).

    Never raises; unsupported types collapse to ``None`` so the result
    is always JSON-safe.  Counter dicts (all-numeric values) keep the
    HIGHEST-count entries when trimmed; other dicts keep the first
    entries in insertion order; lists keep their LAST items.
    """
    if depth > _MAX_DICT_DEPTH:
        return None
    if isinstance(value, dict):
        max_keys = max(0, int(spec.get("max_keys", MAX_COUNTER_KEYS)))
        clamp = spec.get("clamp")
        items: List[Tuple[str, Any]] = []
        raw_items = list(value.items())
        if len(raw_items) > _MAX_PRE_SORT_ITEMS:
            raw_items = raw_items[:_MAX_PRE_SORT_ITEMS]
        for k, v in raw_items:
            items.append((str(k)[:MAX_STR], v))
        numeric = bool(items) and all(_is_number(v) for _, v in items)
        if len(items) > max_keys:
            if numeric:                       # keep the highest counts
                items.sort(key=lambda kv: (-kv[1], kv[0]))
            items = items[:max_keys]
        out: Dict[str, Any] = {}
        for k, v in items:
            if isinstance(v, (dict, list)):
                v = _bound_value(v, {}, depth + 1)
            elif isinstance(v, str):
                v = v[:MAX_STR]
            elif isinstance(v, bool):
                pass
            elif _is_number(v):
                if not math.isfinite(float(v)):
                    v = 0.0 if isinstance(v, float) else 0
                elif clamp is not None:
                    lo, hi = clamp
                    v = max(float(lo), min(float(hi), float(v)))
            else:
                v = None
            out[k] = v
        return out
    if isinstance(value, list):
        max_items = max(0, int(spec.get("max_items", MAX_LIST_ITEMS)))
        trimmed = value[-max_items:] if max_items else []
        return [_bound_value(v, {}, depth + 1) for v in trimmed]
    if isinstance(value, str):
        return value[: int(spec.get("max_len", MAX_STR))]
    if isinstance(value, bool):
        return value
    if _is_number(value):
        clamp = spec.get("clamp")
        if clamp is not None:
            lo, hi = clamp
            return max(float(lo), min(float(hi), float(value)))
        if isinstance(value, float) and not math.isfinite(value):
            return 0.0
        return value
    return None


def _counter_len(value: Any) -> int:
    """Number of entries in a counter dict (0 when absent/malformed)."""
    return len(value) if isinstance(value, dict) else 0


# ---------------------------------------------------------------------------
# ProfileStore — one bounded, content-free profile file
# ---------------------------------------------------------------------------

class ProfileStore:
    """One personal-profile file (interaction / voice / gestures /
    preferences) with dynamic path resolution, bounded content, atomic
    writes and lifecycle-compatible reset.

    * ``load()`` never raises: a missing file yields the defaults; a
      corrupted/unreadable file yields the defaults and sets
      ``self.corrupted_last_load = True``.
    * ``save()`` validates + bounds first, then writes atomically via
      ``persistence.atomic_write_json``; it returns ``bool``.
    * ``observe()`` increments a bounded counter and auto-saves.
    * ``reset()`` mirrors the persistence lifecycle: atomic byte-copy
      backup to ``<home>/backups/profile-<name>-<unixts>.json`` then
      restore defaults.
    """

    def __init__(self, name: str = "interaction") -> None:
        if name not in PROFILE_FILES:
            raise ValueError(
                f"unknown profile store {name!r}; valid stores: "
                f"{', '.join(sorted(PROFILE_FILES))}")
        self.name = name
        #: set by load(): True when the last read hit corrupted/unreadable
        #: data (missing files are NOT corruption and clear the flag)
        self.corrupted_last_load = False
        self._lock = threading.RLock()

    # -- paths -------------------------------------------------------------

    @property
    def file(self) -> str:
        """Absolute path of this store's file, resolved DYNAMICALLY."""
        return PROFILE_FILES[self.name]()

    # -- defaults ----------------------------------------------------------

    def defaults(self) -> dict:
        """A fresh deep copy of this store's default document."""
        return _defaults(self.name)

    # -- read ---------------------------------------------------------------

    def load(self) -> dict:
        """Load the profile document (defaults + learned keys).

        Missing file -> defaults (no corruption flag).  Corrupted,
        undecodable, non-dict or unreadable file -> defaults and
        ``corrupted_last_load = True``.  Learned keys are merged over
        the defaults with per-field type checks, and the whole document
        is bounded before use.  NEVER raises.
        """
        with self._lock:
            try:
                path = self.file
                if not os.path.exists(path):
                    self.corrupted_last_load = False
                    return self.defaults()
                with open(path, "rb") as f:
                    raw = f.read()
                obj = json.loads(raw.decode("utf-8"))
                if not isinstance(obj, dict):
                    raise ValueError("profile document is not a JSON object")
                merged = self.defaults()
                for k, v in _bound_document(obj, 0).items():
                    if k in merged:
                        if _type_ok(merged[k], v):
                            merged[k] = v
                    elif isinstance(v, (dict, list, int, float, str)) \
                            and not isinstance(v, bool):
                        # dynamic counter category (e.g. phrase_counts,
                        # "unverified.<category>")
                        merged[k] = v
                self.corrupted_last_load = False
                return merged
            except Exception:
                self.corrupted_last_load = True
                return self.defaults()

    # -- write ---------------------------------------------------------------

    def bounds(self, data: Any) -> dict:
        """Bound untrusted data to the profile limits (never raises).

        Rules: counter dicts capped at 512 keys keeping the
        highest-count entries; lists trimmed to the last 32 items;
        ratio fields clamped to [0.0, 1.0] per :data:`BOUNDS`; keys and
        short strings truncated to 64 chars; counter values numeric
        only; unsupported values collapse to ``None``.  The input is
        not mutated.
        """
        try:
            out = _bound_document(data, 0)
            return out if isinstance(out, dict) else {}
        except Exception:
            return {}

    def save(self, data: dict) -> bool:
        """Validate + bound + atomically persist ``data``.

        Returns True when the file was written; False on any refusal
        or filesystem failure.  NEVER raises.
        """
        if not isinstance(data, dict):
            return False
        with self._lock:
            try:
                persistence.atomic_write_json(self.file, self.bounds(data))
                return True
            except Exception:
                return False

    # -- observation ---------------------------------------------------------

    def observe(self, category: str, key: str, value: float = 1,
                now: Optional[Any] = None) -> bool:
        """Increment the counter ``category[key]`` and auto-save.

        Records a bounded fact: ``category`` and ``key`` are truncated
        to 64 chars and must be non-empty; ``value`` must be a finite
        non-negative number; the target field must be a dict (counter
        map).  Also bumps ``total_observations`` and ``updated_at``.
        Returns True when the updated document was persisted.
        """
        with self._lock:
            category = str(category or "").strip()[:MAX_STR]
            key = str(key or "").strip()[:MAX_STR]
            if not category or not key:
                return False
            try:
                value = float(value)
            except (TypeError, ValueError):
                return False
            if not math.isfinite(value) or value < 0:
                return False
            data = self.load()
            cat = data.get(category)
            if cat is None:
                cat = {}
                data[category] = cat
            if not isinstance(cat, dict):
                return False  # e.g. observing "hint_level" (a string field)
            prev = cat.get(key)
            prev = prev if _is_number(prev) else 0
            new = prev + value
            cat[key] = int(new) if float(new).is_integer() else new
            total = data.get("total_observations")
            total = total if isinstance(total, int) \
                and not isinstance(total, bool) else 0
            data["total_observations"] = total + 1
            data["updated_at"] = _stamp(now)
            return self.save(data)

    # -- queries ---------------------------------------------------------------

    def top(self, category: str, k: int = 5) -> List[Tuple[str, float]]:
        """The ``k`` highest-count keys in ``category`` (count desc,
        key asc for deterministic ties).  Content-free: keys are the
        user's own short labels already stored locally."""
        with self._lock:
            data = self.load()
            cat = data.get(category)
            if not isinstance(cat, dict):
                return []
            try:
                kk = max(0, int(k))
            except (TypeError, ValueError):
                kk = 5
            items = [(str(key), v) for key, v in cat.items() if _is_number(v)]
            items.sort(key=lambda kv: (-kv[1], kv[0]))
            return items[:kk]

    def summary(self) -> dict:
        """Privacy-safe counts for display — NO content, NO keys."""
        with self._lock:
            data = self.load()
            categories = sorted(k for k, v in data.items()
                                if isinstance(v, dict))
            records = sum(len(v) for v in data.values()
                          if isinstance(v, dict))
            return {
                "file": self.name,
                "records": records,
                "categories": categories,
                "updated_at": data.get("updated_at"),
                "corrupted_last_load": self.corrupted_last_load,
            }

    # -- lifecycle ---------------------------------------------------------------

    def reset(self, backup: bool = True) -> dict:
        """Back up the current file, then restore the defaults.

        Mirrors the ``airmouse.persistence`` lifecycle pattern: the
        existing file is byte-copied (atomic) to
        ``<home>/backups/profile-<name>-<unixts>.json`` and the store
        is then rewritten with its default document.  Never raises;
        the result carries honest flags:
        ``{"name", "file", "backed_up", "backup_path"?, "cleared"}``.
        """
        result: Dict[str, Any] = {"name": self.name, "file": self.file,
                                  "backed_up": False, "cleared": False}
        with self._lock:
            try:
                path = self.file
                if backup and os.path.exists(path):
                    try:
                        with open(path, "rb") as f:
                            raw = f.read()
                        stamp = int(time.time())
                        backup_dir = paths.backups_dir()
                        os.makedirs(backup_dir, exist_ok=True)
                        target = os.path.join(
                            backup_dir,
                            f"profile-{self.name}-{stamp}.json")
                        n = 1
                        while os.path.exists(target):
                            n += 1
                            target = os.path.join(
                                backup_dir,
                                f"profile-{self.name}-{stamp}-{n}.json")
                        persistence.atomic_write_bytes(target, raw)
                        result["backed_up"] = True
                        result["backup_path"] = target
                    except OSError:
                        result["backed_up"] = False
                result["cleared"] = self.save(self.defaults())
            except Exception:
                result["cleared"] = False
            return result

    def export_payload(self) -> dict:
        """``{"name", "schema_version", "data"}`` for user-directed
        export flows (the bundle writer decides where it lands)."""
        with self._lock:
            data = self.load()
            version = data.get("schema_version")
            if not isinstance(version, int) or isinstance(version, bool):
                version = SCHEMA_VERSION
            return {"name": self.name, "schema_version": version,
                    "data": data}


# ---------------------------------------------------------------------------
# PersonalProfile — facade across the four profile stores
# ---------------------------------------------------------------------------

class PersonalProfile:
    """The four personal-profile stores behind one object, plus the
    single ``learn_event()`` entry point for the LIVE loop.

    Stores are created lazily (one property per name: ``interaction``,
    ``voice``, ``gestures``, ``preferences``) and resolve their files
    dynamically on every call, so ``$AIRMOUSE_HOME`` may change after
    import.  No content is ever stored here — counters and parameters
    only.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stores: Dict[str, ProfileStore] = {}

    # -- store access --------------------------------------------------------

    def _store(self, name: str) -> ProfileStore:
        with self._lock:
            store = self._stores.get(name)
            if store is None:
                store = ProfileStore(name)
                self._stores[name] = store
            return store

    @property
    def interaction(self) -> ProfileStore:
        return self._store("interaction")

    @property
    def voice(self) -> ProfileStore:
        return self._store("voice")

    @property
    def gestures(self) -> ProfileStore:
        return self._store("gestures")

    @property
    def preferences(self) -> ProfileStore:
        return self._store("preferences")

    def stores(self) -> Dict[str, ProfileStore]:
        """All four stores by name."""
        return {name: self._store(name) for name in PROFILE_FILES}

    # -- display ---------------------------------------------------------------

    def summary(self) -> dict:
        """Privacy-safe counts across all four stores (for an
        ``airmouse privacy``-style display): per-store record counts
        and category names — NO learned keys, NO content."""
        per_store: Dict[str, Any] = {}
        total = 0
        for name in PROFILE_FILES:
            info = self._store(name).summary()
            per_store[name] = info
            total += int(info.get("records", 0))
        return {
            "profile_dir": paths.profile_dir(),
            "stores": per_store,
            "total_records": total,
        }

    def personalization_summary(self) -> str:
        """The mission §27 rendering — counts only, nothing uploaded::

            PERSONALIZATION
            Gestures learned: N        (distinct keys in gestures.gesture_counts)
            Voice patterns: N          (distinct keys in voice.command_counts)
            Gaze calibration: Complete|Incomplete|Unknown
            Workflows: N               (distinct keys in interaction.workflows)
            Nothing is uploaded.

        Gaze calibration reads the existence of
        ``<home>/gaze_calibration.json`` via :mod:`airmouse.paths`
        (guarded): exists -> Complete, missing -> Incomplete, the check
        itself failing -> Unknown.
        """
        gestures = self._store("gestures").load()
        voice = self._store("voice").load()
        interaction = self._store("interaction").load()
        try:
            gaze_state = "Complete" if os.path.exists(
                paths.gaze_calibration_file()) else "Incomplete"
        except Exception:
            gaze_state = "Unknown"
        return "\n".join([
            "PERSONALIZATION",
            f"Gestures learned: {_counter_len(gestures.get('gesture_counts'))}",
            f"Voice patterns: {_counter_len(voice.get('command_counts'))}",
            f"Gaze calibration: {gaze_state}",
            f"Workflows: {_counter_len(interaction.get('workflows'))}",
            "Nothing is uploaded.",
        ])

    # -- lifecycle ---------------------------------------------------------------

    def export_all(self, dest_path: Optional[str] = None) -> bool:
        """Write a JSON bundle of all four stores for the user.

        User-directed export only: lands at ``dest_path`` (parent
        directories are created), or, when ``dest_path`` is falsy, at
        ``<home>/exports/profile-export-<unixts>.json``.  Never
        raises; returns True when the bundle was written.
        """
        try:
            if dest_path:
                target = os.path.abspath(os.path.expanduser(str(dest_path)))
            else:
                exports = paths.exports_dir()
                os.makedirs(exports, exist_ok=True)
                target = os.path.join(
                    exports, f"profile-export-{int(time.time())}.json")
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            bundle = {
                "format": "airmouse-profile-export",
                "version": SCHEMA_VERSION,
                "created_at": _utcnow_iso(),
                "stores": {name: self._store(name).export_payload()
                           for name in PROFILE_FILES},
            }
            persistence.atomic_write_json(target, bundle)
            return True
        except Exception:
            return False

    def reset_all(self, backup: bool = True) -> Dict[str, dict]:
        """Reset every store (backup-then-defaults); per-store results."""
        return {name: self._store(name).reset(backup=backup)
                for name in PROFILE_FILES}

    # -- live-loop entry point -----------------------------------------------------

    def learn_event(self, event: dict) -> int:
        """THE single entry point for the LIVE loop to feed observations.

        ``event`` keys: ``{"modality": "gesture"|"voice"|"gaze"|"fusion",
        "kind": "intent"|"command"|"phrase"|"correction"|"temporal",
        "key": str, "value": number=1, "verified": bool}``.

        Routing: the modality picks the store (gesture->gestures,
        voice->voice, gaze/fusion->interaction); the kind picks the
        canonical counter category, falling back to a per-modality
        category when the kind does not belong to that store.
        UNVERIFIED events (``verified`` falsy — the sensor did not
        confirm) are counted in the separate ``"unverified."``
        namespace and are NEVER used for preference suggestions.

        Returns the updated counter value for the routed (category,
        key) — an int >= 1 — or 0 when the event was ignored (invalid
        modality/kind/key/value or the store refused it).  Never raises.
        """
        try:
            if not isinstance(event, dict):
                return 0
            modality = str(event.get("modality", "") or "").strip().lower()
            kind = str(event.get("kind", "") or "").strip().lower()
            if modality not in MODALITY_STORE or kind not in KIND_CATEGORY:
                return 0
            key = str(event.get("key", "") or "").strip()[:MAX_STR]
            if not key:
                return 0
            try:
                value = float(event.get("value", 1))
            except (TypeError, ValueError):
                return 0
            if not math.isfinite(value) or value < 0:
                return 0
            verified = bool(event.get("verified", False))

            store_name = MODALITY_STORE[modality]
            category = KIND_CATEGORY[kind]
            if _CATEGORY_STORE.get(category) != store_name:
                category = _MODALITY_FALLBACK[store_name]
            if not verified:
                category = UNVERIFIED_PREFIX + category

            store = self._store(store_name)
            if not store.observe(category, key, value=value):
                return 0
            data = store.load()
            cat = data.get(category)
            if isinstance(cat, dict) and _is_number(cat.get(key)):
                return int(round(float(cat[key])))
            return 1
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# LearningLoop — §16 closed-loop bookkeeping (PREDICTION ≠ EXECUTION)
# ---------------------------------------------------------------------------

class LearningLoop:
    """Bounded, thread-safe bookkeeping for the §16 closed learning
    loop: SENSE→RECOGNIZE→CONTEXT→PREDICT→PROPOSE→APPROVE→ACTION→
    OBSERVE→VERIFY→LEARN→ADAPT.

    HARD RULE — PREDICTION ≠ EXECUTION: this loop may SUGGEST, never
    silently execute, never create dangerous automation.

    * :meth:`record` appends stage events to a bounded ring buffer
      (default 256 entries).
    * :meth:`propose` stores a PROPOSAL with status ``"pending"``.
      Proposals NEVER execute anything — they only surface to the
      UI/CLI for the user to inspect.
    * :meth:`approve` marks an existing PENDING proposal approved.
      Approval is an explicit, separate step; auto-approval is
      structurally impossible (an unknown/non-pending id -> False, and
      no other code path sets ``status = "approved"``).
    * :meth:`expire_proposals` expires stale pending proposals so
      learning never piles up stale suggestions.
    * :meth:`adapt` turns the OLDEST approved proposal into ONE
      bounded profile observation (``learn_event`` with
      ``verified=True``) and marks it ``"applied"`` — nothing else
      mutates state, and no action/macro/automation is executed.

    All mutations are guarded by a lock (the live loop is
    multithreaded).
    """

    #: maximum proposals kept (oldest resolved — else oldest — dropped)
    MAX_PROPOSALS = 256
    _MIN_RING = 16
    _MAX_RING = 4096

    def __init__(self, ring_size: int = 256) -> None:
        try:
            size = int(ring_size)
        except (TypeError, ValueError):
            size = 256
        size = max(self._MIN_RING, min(self._MAX_RING, size))
        self._ring: deque = deque(maxlen=size)
        self._proposals: Dict[str, dict] = {}   # id -> proposal (ordered)
        self._lock = threading.RLock()
        self._seq = itertools.count(1)
        self._last_adapt_at: Optional[float] = None

    # -- stage ring ----------------------------------------------------------

    def record(self, stage: str, **fields: Any) -> None:
        """Append a stage event to the ring buffer (bounded).

        ``stage`` must be one of :data:`STAGES` (ValueError otherwise).
        At most the first 16 keyword fields are kept; string values are
        truncated to 256 chars.
        """
        if stage not in STAGES:
            raise ValueError(
                f"unknown loop stage {stage!r}; valid stages: "
                f"{', '.join(STAGES)}")
        entry: Dict[str, Any] = {"stage": stage, "ts": time.time()}
        for i, (k, v) in enumerate(fields.items()):
            if i >= _MAX_TOP_ITEMS:
                break
            if isinstance(v, str):
                v = v[:256]
            entry[str(k)[:MAX_STR]] = v
        with self._lock:
            self._ring.append(entry)

    def events(self) -> Tuple[dict, ...]:
        """A read-only snapshot of the ring buffer (oldest first)."""
        with self._lock:
            return tuple(dict(e) for e in self._ring)

    # -- proposals (PREDICTION ≠ EXECUTION) ------------------------------------

    @staticmethod
    def _bound_suggestion(suggestion: dict) -> dict:
        """Bounded copy of a suggestion (JSON-safe, size-capped)."""
        out: Dict[str, Any] = {}
        for k, v in list(suggestion.items())[:_MAX_SUGGESTION_KEYS]:
            k = str(k)[:MAX_STR]
            if isinstance(v, bool):
                out[k] = v
            elif _is_number(v):
                out[k] = v if math.isfinite(float(v)) else 0
            elif isinstance(v, str):
                out[k] = v[:128]
            elif isinstance(v, dict):
                out[k] = {
                    str(kk)[:MAX_STR]:
                        (vv[:128] if isinstance(vv, str)
                         else vv if isinstance(vv, (int, float, bool))
                         else str(vv)[:128])
                    for kk, vv in list(v.items())[:16]}
            elif isinstance(v, list):
                out[k] = [
                    x if isinstance(x, (int, float, bool)) else str(x)[:128]
                    for x in list(v)[:16]]
            else:
                out[k] = str(v)[:128]
        return out

    def propose(self, suggestion: dict) -> str:
        """Register a PROPOSAL (status ``"pending"``) and return its id.

        The suggestion is stored as a bounded copy.  Proposals NEVER
        execute — they only surface to the UI/CLI; the user decides.
        """
        if not isinstance(suggestion, dict):
            raise TypeError("propose() expects a suggestion dict")
        pid = f"prop-{uuid.uuid4().hex[:12]}"
        with self._lock:
            if len(self._proposals) >= self.MAX_PROPOSALS:
                self._drop_oldest_proposal_locked()
            self._proposals[pid] = {
                "id": pid,
                "suggestion": self._bound_suggestion(suggestion),
                "status": "pending",
                "created_at": time.time(),
                "approved_by": None,
                "approved_at": None,
                "applied_at": None,
                "expired_at": None,
            }
        return pid

    def _drop_oldest_proposal_locked(self) -> None:
        if not self._proposals:
            return
        for pid, p in list(self._proposals.items()):
            if p["status"] != "pending":
                del self._proposals[pid]
                return
        del self._proposals[next(iter(self._proposals))]

    def proposals(self) -> Tuple[dict, ...]:
        """Read-only snapshot of all tracked proposals (oldest first)."""
        with self._lock:
            return tuple(copy.deepcopy(p) for p in self._proposals.values())

    def approve(self, proposal_id: str, approved_by: str = "user") -> bool:
        """Explicitly approve a PENDING proposal.  Returns True only
        for an existing, pending id (auto-approval is structurally
        impossible — this is the ONLY code path that approves)."""
        pid = str(proposal_id or "")
        with self._lock:
            p = self._proposals.get(pid)
            if p is None or p["status"] != "pending":
                return False
            p["status"] = "approved"
            p["approved_by"] = str(approved_by or "user")[:MAX_STR]
            p["approved_at"] = time.time()
            return True

    def expire_proposals(self, max_age_s: float = 300,
                         now: Optional[float] = None) -> int:
        """Expire PENDING proposals older than ``max_age_s``; return
        how many were expired (learning must not pile up stale
        suggestions)."""
        try:
            max_age = max(0.0, float(max_age_s))
        except (TypeError, ValueError):
            max_age = 300.0
        try:
            now_ts = time.time() if now is None else float(now)
        except (TypeError, ValueError):
            return 0
        expired = 0
        with self._lock:
            for p in self._proposals.values():
                if p["status"] == "pending" \
                        and p["created_at"] <= now_ts - max_age:
                    p["status"] = "expired"
                    p["expired_at"] = now_ts
                    expired += 1
        return expired

    # -- adaptation ---------------------------------------------------------------

    def adapt(self, profile: "PersonalProfile", max_updates: int = 1) -> list:
        """Turn the OLDEST approved proposal(s) into bounded profile
        observations (PREDICTION ≠ EXECUTION).

        For each of up to ``max_updates`` oldest proposals with status
        ``"approved"``: the suggestion is routed through
        ``profile.learn_event(..., verified=True)`` and the proposal is
        marked ``"applied"`` (with the routed count recorded on it).
        Nothing else mutates state — no action is executed, no
        automation is created.  Returns the list of applied proposal
        ids (empty when nothing was approved / the profile is unusable).
        """
        try:
            updates = max(1, int(max_updates))
        except (TypeError, ValueError):
            updates = 1
        applied: List[str] = []
        if profile is None or not hasattr(profile, "learn_event"):
            return applied
        now = time.time()
        with self._lock:
            targets = [p for p in self._proposals.values()
                       if p["status"] == "approved"][:updates]
            for p in targets:
                event = dict(p["suggestion"])
                event["verified"] = True
                routed = profile.learn_event(event)
                p["status"] = "applied"
                p["applied_at"] = now
                p["routed"] = routed
                applied.append(p["id"])
            if applied:
                self._last_adapt_at = now
        return applied

    # -- stats ---------------------------------------------------------------

    def stats(self) -> dict:
        """Counts per stage, proposal counters and ``last_adapt_at``."""
        with self._lock:
            stages = {stage: 0 for stage in STAGES}
            for e in self._ring:
                stage = e.get("stage")
                if stage in stages:
                    stages[stage] += 1
            counts = {"pending": 0, "approved": 0, "expired": 0,
                      "applied": 0}
            for p in self._proposals.values():
                if p["status"] in counts:
                    counts[p["status"]] += 1
            return {
                "stages": stages,
                "events": len(self._ring),
                "proposals_pending": counts["pending"],
                "proposals_approved": counts["approved"],
                "proposals_expired": counts["expired"],
                "proposals_applied": counts["applied"],
                "proposals_total": len(self._proposals),
                "last_adapt_at": self._last_adapt_at,
            }
