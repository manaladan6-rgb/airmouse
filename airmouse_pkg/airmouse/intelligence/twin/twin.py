"""
airmouse.intelligence.twin.twin — Personal Interaction Twin (v12 §2).

The Twin models HOW THE USER INTERACTS — preferences, habits, patterns —
never private content.  It answers questions like:

    * which modality does this user prefer for clicking?  (§2)
    * which commands does this user run most, and how do they phrase them?
    * does this user confirm destructive actions quickly or slowly?
    * what time of day does this user run workflows?
    * which applications does this user keep returning to?

DESIGN CONTRACTS
----------------
1.  OPTIONAL BY CONTRACT.  The AirMouse core NEVER imports this package
    at module scope and functions perfectly when the Twin is absent,
    disabled, corrupted or reset.  (Same rules as the v11.5
    intelligence plugin facade.)
2.  PATTERN STORAGE, NOT CONTENT STORAGE.  Fact values are small,
    categorical and scrubbed.  Free-text is rejected or truncated;
    secrets are refused outright (fail-closed scrubbing, same spirit as
    ``intelligence.memory``).
3.  EVERY FACT CARRIES ITS EVIDENCE (§2 schema):

        source        what produced the fact (voice/gaze/agent/…)
        confidence    0..1, EMA-updated on repeated evidence
        context       small bounded dict (app, mode, time bucket)
        timestamp     last-updated monotonic ts + wall clock
        frequency     how many supporting observations
        success_rate  0..1 over observed outcomes (or None)
        provenance    bounded evidence list (who/when/how-confident)

4.  LIFECYCLE: learn → decay → forget → correct → export/import →
    reset → inspect → explain.  All bounded, all deterministic.
5.  NEVER RAISES through the public boundary.  Public methods return
    result objects / None on invalid input and record a bounded error
    counter.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# bounded constants (§2 — hard resource limits)
# ─────────────────────────────────────────────────────────────────────────────

TWIN_FORMAT_VERSION = 1

MAX_FACTS = 2000                 # total facts across all categories
MAX_PROVENANCE_PER_FACT = 8      # evidence entries kept per fact
MAX_VALUE_LEN = 120              # categorical values only
MAX_KEY_LEN = 64
MAX_CONTEXT_ENTRIES = 6
MAX_CONTEXT_VALUE_LEN = 60
DEFAULT_DECAY_HALF_LIFE_H = 720  # 30 days — confidence half-life
MIN_CONFIDENCE = 0.05            # facts below this are forgotten
EXPORT_MAX_BYTES = 8 * 1024 * 1024

_SECRET_HINTS = ("password", "passwd", "secret", "token", "api_key", "apikey",
                 "credential", "private key", "pin ", "cvv", "ssn")

_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),          # GitHub-style PATs
    re.compile(r"sk-[A-Za-z0-9-_]{16,}"),                # API keys
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b\d{13,19}\b"),                        # card-length digits
)

_VALID_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:@/-]{0,63}$")


class TwinCategory(enum.Enum):
    """What kind of interaction behaviour a fact describes (§2)."""

    PREFERENCE = "preference"                    # generic user preference
    HABIT = "habit"                              # recurring interaction habit
    GESTURE_PATTERN = "gesture_pattern"          # how the user gestures
    GAZE_BEHAVIOR = "gaze_behavior"              # gaze/dwell behaviour
    VOICE_VOCABULARY = "voice_vocabulary"        # words/phrases the user uses
    COMMAND_PREFERENCE = "command_preference"    # preferred phrasings/commands
    APPLICATION_PREFERENCE = "application_preference"
    WORKFLOW_PREFERENCE = "workflow_preference"
    CONFIRMATION_BEHAVIOR = "confirmation_behavior"
    CORRECTION_BEHAVIOR = "correction_behavior"
    TIMING_PATTERN = "timing_pattern"            # when things are done
    SUCCESSFUL_ACTION = "successful_action"      # observed successes (counted)
    FAILED_ACTION = "failed_action"              # observed failures (counted)
    MODALITY_PREFERENCE = "modality_preference"  # which channel for what


class FactSource(enum.Enum):
    """Where a fact's evidence came from (§2 provenance.source)."""

    VOICE = "voice"
    GESTURE = "gesture"
    GAZE = "gaze"
    KEYBOARD = "keyboard"
    MOUSE = "mouse"
    SCREEN = "screen"
    BROWSER = "browser"
    AGENT = "agent"
    USER_EXPLICIT = "user_explicit"     # user told AirMouse directly
    SYSTEM = "system"                   # deterministic internal inference
    IMPORTED = "imported"


@dataclass
class ProvenanceEntry:
    """One piece of supporting evidence for a fact (§2)."""

    source: str = "system"
    ts: float = 0.0                     # monotonic
    wall: str = ""                      # UTC ISO timestamp (display only)
    confidence: float = 0.5
    context: str = ""                   # short tag, e.g. "app:chrome"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "ts": round(self.ts, 6),
            "wall": self.wall,
            "confidence": round(self.confidence, 4),
            "context": self.context,
        }


@dataclass
class TwinFact:
    """A single learned interaction-behaviour fact (§2 schema)."""

    category: str = "preference"
    key: str = ""
    value: str = ""
    source: str = "system"
    confidence: float = 0.5
    context: Dict[str, str] = field(default_factory=dict)
    ts: float = 0.0                     # monotonic last update
    wall: str = ""                      # UTC ISO last update
    frequency: int = 1
    success_rate: Optional[float] = None   # None = no outcome data yet
    successes: int = 0
    failures: int = 0
    provenance: List[ProvenanceEntry] = field(default_factory=list)
    fact_id: str = ""                   # "<category>:<key>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "confidence": round(self.confidence, 4),
            "context": dict(sorted(self.context.items())),
            "ts": round(self.ts, 6),
            "wall": self.wall,
            "frequency": self.frequency,
            "success_rate": (round(self.success_rate, 4)
                             if self.success_rate is not None else None),
            "successes": self.successes,
            "failures": self.failures,
            "provenance": [p.to_dict() for p in self.provenance],
            "fact_id": self.fact_id,
        }


@dataclass
class TwinStats:
    """Bounded counters for introspection (never private content)."""

    facts: int = 0
    observations: int = 0
    corrections: int = 0
    forgotten: int = 0
    decay_passes: int = 0
    rejected_inputs: int = 0
    errors: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# validation helpers (fail-closed, deterministic)
# ─────────────────────────────────────────────────────────────────────────────


def _clean_category(value: Any) -> Optional[str]:
    if isinstance(value, TwinCategory):
        return value.value
    try:
        return TwinCategory(str(value)).value
    except (ValueError, TypeError):
        return None


def _clean_source(value: Any) -> str:
    if isinstance(value, FactSource):
        return value.value
    try:
        return FactSource(str(value)).value
    except (ValueError, TypeError):
        return FactSource.SYSTEM.value


def _clean_value(value: Any) -> Optional[str]:
    """Values are categorical labels, never content.  Fail-closed."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or len(text) > MAX_VALUE_LEN:
        return None
    low = text.lower()
    if any(h in low for h in _SECRET_HINTS):
        return None
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            return None
    return text


def _clean_key(key: Any) -> Optional[str]:
    if key is None:
        return None
    text = str(key).strip().lower()
    if not text or len(text) > MAX_KEY_LEN:
        return None
    if not _VALID_KEY_RE.match(text):
        return None
    if any(h in text for h in _SECRET_HINTS):
        return None
    return text


def _clean_context(context: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(context, dict):
        return out
    for k, v in list(context.items())[: MAX_CONTEXT_ENTRIES * 2]:
        ck = _clean_key(k)
        cv = _clean_value(v)
        if ck and cv:
            out[ck] = cv
        if len(out) >= MAX_CONTEXT_ENTRIES:
            break
    return out


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ─────────────────────────────────────────────────────────────────────────────
# the twin
# ─────────────────────────────────────────────────────────────────────────────


class PersonalInteractionTwin:
    """Learns interaction behaviour — never private content (v12 §2).

    The Twin is OPTIONAL: instantiate it only where personalization is
    wanted; the core AirMouse never requires it.
    """

    def __init__(self, enabled: bool = True,
                 max_facts: int = MAX_FACTS,
                 decay_half_life_hours: float = DEFAULT_DECAY_HALF_LIFE_H,
                 min_confidence: float = MIN_CONFIDENCE) -> None:
        self.enabled = bool(enabled)
        self.max_facts = max(64, min(int(max_facts), MAX_FACTS))
        self.decay_half_life_hours = max(0.1, float(decay_half_life_hours))
        self.min_confidence = float(min_confidence)
        self._facts: Dict[str, TwinFact] = {}
        self.stats = TwinStats()
        self._t0 = time.perf_counter()

    # ── learning ─────────────────────────────────────────────────────────

    def learn(self, category: Any, key: Any, value: Any,
              source: Any = "system", confidence: float = 0.5,
              context: Optional[Dict[str, str]] = None) -> Optional[TwinFact]:
        """Record one interaction observation as a fact (§2 learning).

        Repeated observations of the same (category, key) update
        frequency and blend confidence (EMA).  Returns the fact or
        None when the input was rejected (invalid/secret/oversized).
        """
        if not self.enabled:
            return None
        try:
            cat = _clean_category(category)
            ck = _clean_key(key)
            cv = _clean_value(value)
            if cat is None or ck is None or cv is None:
                self.stats.rejected_inputs += 1
                return None
            conf = max(0.0, min(1.0, float(confidence)))
            src = _clean_source(source)
            ctx = _clean_context(context)
            fid = f"{cat}:{ck}"
            now = time.perf_counter()
            existing = self._facts.get(fid)
            if existing is not None:
                existing.frequency += 1
                existing.confidence = round(
                    0.7 * existing.confidence + 0.3 * conf, 4)
                existing.value = cv
                existing.source = src
                existing.ts = now
                existing.wall = _utcnow()
                if ctx:
                    merged = dict(existing.context)
                    merged.update(ctx)
                    existing.context = dict(
                        sorted(merged.items())[:MAX_CONTEXT_ENTRIES])
                existing.provenance.append(
                    ProvenanceEntry(source=src, ts=now, wall=_utcnow(),
                                    confidence=conf,
                                    context=";".join(
                                        f"{k}:{v}" for k, v in
                                        sorted(ctx.items()))[:MAX_CONTEXT_VALUE_LEN]))
                if len(existing.provenance) > MAX_PROVENANCE_PER_FACT:
                    existing.provenance = existing.provenance[
                        -MAX_PROVENANCE_PER_FACT:]
                self.stats.observations += 1
                return existing
            if len(self._facts) >= self.max_facts:
                self._evict_weakest()
            fact = TwinFact(category=cat, key=ck, value=cv, source=src,
                            confidence=round(conf, 4), context=ctx,
                            ts=now, wall=_utcnow(), frequency=1,
                            provenance=[ProvenanceEntry(
                                source=src, ts=now, wall=_utcnow(),
                                confidence=conf,
                                context=";".join(
                                    f"{k}:{v}" for k, v in
                                    sorted(ctx.items()))[:MAX_CONTEXT_VALUE_LEN])],
                            fact_id=fid)
            self._facts[fid] = fact
            self.stats.facts = len(self._facts)
            self.stats.observations += 1
            return fact
        except Exception:
            self.stats.errors += 1
            return None

    def record_outcome(self, category: Any, key: Any,
                       success: bool) -> bool:
        """Update success_rate for a fact (§2 success/failure tracking)."""
        if not self.enabled:
            return False
        try:
            cat = _clean_category(category)
            ck = _clean_key(key)
            if cat is None or ck is None:
                return False
            fact = self._facts.get(f"{cat}:{ck}")
            if fact is None:
                return False
            if success:
                fact.successes += 1
            else:
                fact.failures += 1
            total = fact.successes + fact.failures
            fact.success_rate = round(fact.successes / total, 4)
            return True
        except Exception:
            self.stats.errors += 1
            return False

    def correct(self, category: Any, key: Any, value: Any,
                source: Any = "user_explicit") -> Optional[TwinFact]:
        """User-corrected fact (§2 correction behaviour).

        Corrections strongly boost confidence (the user said so) and
        are themselves tracked as CORRECTION_BEHAVIOR facts.
        """
        fact = self.learn(category, key, value, source=source,
                          confidence=0.95)
        if fact is not None:
            # user corrections are authoritative — snap confidence up
            fact.confidence = round(max(fact.confidence, 0.95), 4)
            self.stats.corrections += 1
            self.learn(TwinCategory.CORRECTION_BEHAVIOR.value,
                       f"corrected_{_clean_category(category) or 'unknown'}",
                       _clean_key(key) or "unknown",
                       source=FactSource.USER_EXPLICIT.value, confidence=0.9)
        return fact

    def forget(self, category: Any, key: Any) -> bool:
        """Delete one fact (§2 forgetting)."""
        try:
            cat = _clean_category(category)
            ck = _clean_key(key)
            if cat is None or ck is None:
                return False
            fid = f"{cat}:{ck}"
            if fid in self._facts:
                del self._facts[fid]
                self.stats.facts = len(self._facts)
                self.stats.forgotten += 1
                return True
            return False
        except Exception:
            self.stats.errors += 1
            return False

    def decay(self) -> int:
        """Apply confidence decay; forget facts below the floor (§2).

        Exponential decay by fact age using the configured half-life.
        Returns the number of facts forgotten.
        """
        if not self.enabled:
            return 0
        try:
            now = time.perf_counter()
            half_life_s = self.decay_half_life_hours * 3600.0
            forgotten = 0
            for fid in list(self._facts):
                fact = self._facts[fid]
                age_s = max(0.0, now - fact.ts)
                if half_life_s > 0:
                    factor = 0.5 ** (age_s / half_life_s)
                else:
                    factor = 1.0
                # frequency keeps well-used facts alive longer
                boost = min(0.2, 0.01 * fact.frequency)
                fact.confidence = round(
                    max(0.0, min(1.0, fact.confidence * factor + boost * factor)),
                    4)
                if fact.confidence < self.min_confidence:
                    del self._facts[fid]
                    forgotten += 1
            self.stats.facts = len(self._facts)
            self.stats.forgotten += forgotten
            self.stats.decay_passes += 1
            return forgotten
        except Exception:
            self.stats.errors += 1
            return 0

    def _evict_weakest(self) -> None:
        """Bounded eviction: lowest confidence, then least used (§2)."""
        if not self._facts:
            return
        fid = min(self._facts,
                  key=lambda f: (self._facts[f].confidence,
                                 -self._facts[f].frequency,
                                 self._facts[f].ts))
        del self._facts[fid]
        self.stats.forgotten += 1

    # ── inspection ───────────────────────────────────────────────────────

    def get(self, category: Any, key: Any) -> Optional[Dict[str, Any]]:
        """Read-only fact view (§2 inspection)."""
        try:
            cat = _clean_category(category)
            ck = _clean_key(key)
            if cat is None or ck is None:
                return None
            fact = self._facts.get(f"{cat}:{ck}")
            return dict(fact.to_dict()) if fact else None
        except Exception:
            self.stats.errors += 1
            return None

    def query(self, category: Optional[str] = None,
              min_confidence: float = 0.0,
              limit: int = 50) -> List[Dict[str, Any]]:
        """Read-only sorted fact list (deterministic order)."""
        try:
            cat = _clean_category(category) if category else None
            rows = []
            for fact in self._facts.values():
                if cat is not None and fact.category != cat:
                    continue
                if fact.confidence < min_confidence:
                    continue
                rows.append(fact.to_dict())
            rows.sort(key=lambda d: (-d["confidence"], d["fact_id"]))
            return rows[: max(1, min(int(limit), 500))]
        except Exception:
            self.stats.errors += 1
            return []

    def preferred_modality(self, action: str = "click") -> Optional[str]:
        """Which modality the user prefers for an action (§2)."""
        fact = self._facts.get(
            f"{TwinCategory.MODALITY_PREFERENCE.value}:"
            f"{_clean_key(action) or 'click'}")
        return fact.value if fact else None

    def top_commands(self, limit: int = 5) -> List[Tuple[str, int]]:
        """Most-used command preferences (deterministic order)."""
        rows = self.query(category=TwinCategory.COMMAND_PREFERENCE.value,
                          limit=max(1, min(int(limit), 50)))
        rows.sort(key=lambda d: (-d["frequency"], d["key"]))
        return [(r["key"], r["frequency"]) for r in rows]

    def explain(self, category: Any, key: Any) -> Dict[str, Any]:
        """Explain why a fact is believed (§2 explainability).

        Returns the evidence chain without any private content.
        """
        fact = self.get(category, key)
        if fact is None:
            return {"known": False, "reason": "no such fact"}
        return {
            "known": True,
            "fact_id": fact["fact_id"],
            "value": fact["value"],
            "confidence": fact["confidence"],
            "frequency": fact["frequency"],
            "success_rate": fact["success_rate"],
            "because": [
                f"observed {fact['frequency']}x "
                f"(latest via {p['source']}, conf {p['confidence']})"
                for p in fact["provenance"][-3:]
            ],
            "context": fact["context"],
        }

    # ── persistence ──────────────────────────────────────────────────────

    def export(self) -> Dict[str, Any]:
        """Deterministic, versioned export (§2 export)."""
        try:
            facts = [f.to_dict() for f in self._facts.values()]
            facts.sort(key=lambda d: d["fact_id"])
            return {
                "format": "airmouse-twin",
                "version": TWIN_FORMAT_VERSION,
                "exported_at": _utcnow(),
                "fact_count": len(facts),
                "facts": facts,
            }
        except Exception:
            self.stats.errors += 1
            return {"format": "airmouse-twin",
                    "version": TWIN_FORMAT_VERSION,
                    "exported_at": _utcnow(),
                    "fact_count": 0, "facts": []}

    def export_json(self) -> str:
        return json.dumps(self.export(), sort_keys=True)

    def import_data(self, data: Any) -> Tuple[int, int]:
        """Validated import (§2 import).  Returns (imported, rejected).

        Fail-closed: wrong format/version, oversized payloads,
        secret-looking values and invalid keys are rejected.
        """
        imported = rejected = 0
        try:
            if not isinstance(data, dict):
                return 0, 1
            if data.get("format") != "airmouse-twin":
                return 0, 1
            if int(data.get("version", 0)) != TWIN_FORMAT_VERSION:
                return 0, 1
            raw = json.dumps(data)
            if len(raw) > EXPORT_MAX_BYTES:
                return 0, 1
            facts = data.get("facts")
            if not isinstance(facts, list):
                return 0, 1
            for row in facts[:MAX_FACTS * 2]:
                if not isinstance(row, dict):
                    rejected += 1
                    continue
                cat = row.get("category")
                key = row.get("key")
                fact = self.learn(
                    cat, key, row.get("value"),
                    source=FactSource.IMPORTED.value,
                    confidence=float(row.get("confidence", 0.5) or 0.5),
                    context=row.get("context") if isinstance(
                        row.get("context"), dict) else None)
                if fact is None:
                    rejected += 1
                    continue
                imported += 1
                try:
                    s = min(int(row.get("successes", 0) or 0), 10_000)
                    f = min(int(row.get("failures", 0) or 0), 10_000)
                    for _ in range(s):
                        self.record_outcome(cat, key, True)
                    for _ in range(f):
                        self.record_outcome(cat, key, False)
                except Exception:
                    pass
            return imported, rejected
        except Exception:
            self.stats.errors += 1
            return imported, rejected + 1

    def reset(self, category: Optional[str] = None) -> int:
        """Reset all facts or one category (§2 reset)."""
        try:
            if category is None:
                n = len(self._facts)
                self._facts.clear()
            else:
                cat = _clean_category(category)
                if cat is None:
                    return 0
                fids = [f for f in self._facts
                        if f.startswith(cat + ":")]
                n = len(fids)
                for fid in fids:
                    del self._facts[fid]
            self.stats.facts = len(self._facts)
            self.stats.forgotten += n
            return n
        except Exception:
            self.stats.errors += 1
            return 0

    # ── status ───────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """Bounded introspection summary (never private content)."""
        cats: Dict[str, int] = {}
        for fact in self._facts.values():
            cats[fact.category] = cats.get(fact.category, 0) + 1
        return {
            "enabled": self.enabled,
            "facts": len(self._facts),
            "capacity": self.max_facts,
            "categories": dict(sorted(cats.items())),
            "observations": self.stats.observations,
            "corrections": self.stats.corrections,
            "forgotten": self.stats.forgotten,
            "decay_passes": self.stats.decay_passes,
            "rejected_inputs": self.stats.rejected_inputs,
            "errors": self.stats.errors,
        }
