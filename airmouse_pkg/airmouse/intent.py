"""
airmouse.intent — v8 Intent Engine 🧠
=====================================

Turns one :class:`airmouse.interfaces.FusionDecision` per tick (plus the raw
utterance and direct gesture events) into a small queue of structured
:class:`airmouse.interfaces.Intent` objects that downstream stages (safety →
actions) can gate and execute.

Resolution rules (evaluated per ``process()`` call, in order)
-------------------------------------------------------------
1. EMERGENCY — the utterance contains "emergency stop" / "stop everything"
   → an ``IntentType.EMERGENCY_STOP`` intent with confidence 1.0 is ALWAYS
   emitted, bypassing the per-tick cap and the minimum-confidence gate.
   The e-stop owns the tick: nothing else is resolved after it.
2. Hand-confirmed click — the decision carries a target/point AND a
   confirmation label (``"hand:pinch"`` or ``"voice:click"``) → a CLICK
   intent with ``sources = GAZE|HAND`` (or ``GAZE|VOICE``).
3. Voice-driven — a non-empty utterance is matched against
   :data:`PHRASE_TO_INTENT` (normalized substring, longest phrase first so
   "double click" wins over "click").  A target is taken from the decision;
   a target-less intent has its confidence scaled by
   ``targetless_confidence_scale`` (0.6).
4. Gesture-direct — gestures submitted via :meth:`IntentEngine.submit_gesture`
   are consumed on the next ``process()`` within the age window
   (:data:`GESTURE_TO_INTENT`).

Safety-filtering rules (applied to every candidate intent)
----------------------------------------------------------
* confidence < ``min_confidence`` → dropped (``dropped_count`` incremented).
* a GAZE-sourced click-class intent with confidence <
  ``gaze_min_confidence`` → dropped (uncertain gaze never clicks).
* sensitive intent types (and TYPE with text > 40 chars) get
  ``requires_confirmation=True``.
* intents expire after ``intent_max_age`` seconds (checked on dequeue).
* at most ``max_intents_per_tick`` actionable intents are emitted per
  ``process()`` call (EMERGENCY_STOP is exempt).

The module is pure stdlib + :mod:`airmouse.interfaces` types, imports
headless and is deterministic when ``now`` is supplied by the caller.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import collections
import re
import threading
from typing import Any, Deque, Dict, List, Optional, Tuple

try:  # package-relative (normal import path)
    from .interfaces import (
        FusionDecision,
        Intent,
        IntentType,
        Modality,
        ScreenTarget,
        now_ts,
    )
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (
        FusionDecision,
        Intent,
        IntentType,
        Modality,
        ScreenTarget,
        now_ts,
    )

__all__ = [
    "DEFAULT_INTENT_CONFIG",
    "PHRASE_TO_INTENT",
    "GESTURE_TO_INTENT",
    "EMERGENCY_PHRASES",
    "CLICK_CLASS",
    "SENSITIVE_TYPES",
    "TYPE_SENSITIVE_TEXT_LEN",
    "normalize_text",
    "match_phrase",
    "IntentEngine",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Documented configuration defaults (see :class:`IntentEngine`).
DEFAULT_INTENT_CONFIG: Dict[str, Any] = {
    "min_confidence": 0.35,          # below this an intent is dropped
    "gaze_min_confidence": 0.55,     # uncertain gaze never clicks
    "max_intents_per_tick": 1,       # actionable intents per process() call
    "intent_max_age": 1.2,           # seconds before a queued intent expires
    "voice_base_confidence": 0.8,    # confidence when fusion reports 0
    "targetless_confidence_scale": 0.6,  # confidence × scale without target
    "sensitive_types": None,         # None → SENSITIVE_TYPES default
}

#: Click-class intent types (subject to the gaze-confidence gate).
CLICK_CLASS: set = {
    IntentType.CLICK,
    IntentType.DOUBLE_CLICK,
    IntentType.RIGHT_CLICK,
    IntentType.MIDDLE_CLICK,
}

#: Intent types that always demand explicit user confirmation.
SENSITIVE_TYPES: set = {
    IntentType.CLOSE,
    IntentType.PASTE,
    IntentType.HOTKEY,
    IntentType.MAXIMIZE,
    IntentType.SWITCH_WINDOW,
}

#: TYPE intents whose text exceeds this many characters are sensitive too.
TYPE_SENSITIVE_TEXT_LEN: int = 40

#: Gesture name -> intent type (gesture-direct resolution).
GESTURE_TO_INTENT: Dict[str, IntentType] = {
    "pinch": IntentType.CLICK,
    "peace": IntentType.RIGHT_CLICK,
    "thumb": IntentType.DOUBLE_CLICK,
}

#: Utterance fragments that ALWAYS trigger an emergency stop.
EMERGENCY_PHRASES: Tuple[str, ...] = ("emergency stop", "stop everything")

#: Voice phrase -> (intent type, params).  Matching is a normalized
#: substring scan ordered LONGEST phrase first, so "double click" beats
#: "click" and "stop everything" beats "stop".
PHRASE_TO_INTENT: Dict[str, Tuple[IntentType, Dict[str, Any]]] = {
    "emergency stop": (IntentType.EMERGENCY_STOP, {}),
    "stop everything": (IntentType.EMERGENCY_STOP, {}),
    "double click": (IntentType.DOUBLE_CLICK, {}),
    "right click": (IntentType.RIGHT_CLICK, {}),
    "switch window": (IntentType.SWITCH_WINDOW, {}),
    "scroll up": (IntentType.SCROLL, {"amount": 3}),
    "scroll down": (IntentType.SCROLL, {"amount": -3}),
    "zoom in": (IntentType.ZOOM, {"direction": "in"}),
    "zoom out": (IntentType.ZOOM, {"direction": "out"}),
    "go back": (IntentType.BACK, {}),
    "forward": (IntentType.FORWARD, {}),
    "minimize": (IntentType.MINIMIZE, {}),
    "maximize": (IntentType.MAXIMIZE, {}),
    "select": (IntentType.SELECT, {}),
    "confirm": (IntentType.CONFIRM, {}),
    "cancel": (IntentType.CANCEL, {}),
    "click": (IntentType.CLICK, {}),
    "open": (IntentType.OPEN, {}),
    "close": (IntentType.CLOSE, {}),
    "scroll": (IntentType.SCROLL, {"amount": 3}),
    "copy": (IntentType.COPY, {}),
    "paste": (IntentType.PASTE, {}),
    "stop": (IntentType.CANCEL, {}),
}

# Longest-first ordering reused from the v5 voice-matcher tie-break idea:
# longer (more specific) phrases are tested before their substrings.
_PHRASES_ORDERED: List[Tuple[str, Tuple[IntentType, Dict[str, Any]]]] = sorted(
    PHRASE_TO_INTENT.items(), key=lambda kv: len(kv[0]), reverse=True
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def normalize_text(text: Any) -> str:
    """Lowercase, strip punctuation and collapse whitespace (pure)."""
    if not text:
        return ""
    return _NON_ALNUM_RE.sub(" ", str(text).lower()).strip()


def match_phrase(utterance: Any) -> Optional[Tuple[IntentType, Dict[str, Any]]]:
    """Match ``utterance`` against :data:`PHRASE_TO_INTENT`.

    Normalized substring containment, longest phrase first.  Returns
    ``(IntentType, params)`` or ``None`` (never raises).
    """
    norm = normalize_text(utterance)
    if not norm:
        return None
    for phrase, (itype, params) in _PHRASES_ORDERED:
        if phrase in norm:
            return itype, dict(params)
    return None


def is_emergency_utterance(utterance: Any) -> bool:
    """True when the utterance contains an emergency-stop phrase (pure)."""
    norm = normalize_text(utterance)
    return any(p in norm for p in EMERGENCY_PHRASES)


# ---------------------------------------------------------------------------
# IntentEngine
# ---------------------------------------------------------------------------


class IntentEngine:
    """Fuses one :class:`FusionDecision` per tick into actionable intents.

    Params (``config`` dict keys, see :data:`DEFAULT_INTENT_CONFIG`)
        min_confidence               0.35
        gaze_min_confidence          0.55
        max_intents_per_tick         1
        intent_max_age               1.2   s
        voice_base_confidence        0.8
        targetless_confidence_scale  0.6
        sensitive_types              None → :data:`SENSITIVE_TYPES`

    Thread-safety: the gesture/pending queues are guarded by an internal
    lock; ``process``/``pop_intent``/``submit_gesture`` are thread-safe.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(DEFAULT_INTENT_CONFIG)
        cfg.update(config or {})
        self.min_confidence: float = float(cfg["min_confidence"])
        self.gaze_min_confidence: float = float(cfg["gaze_min_confidence"])
        self.max_intents_per_tick: int = max(1, int(cfg["max_intents_per_tick"]))
        self.intent_max_age: float = float(cfg["intent_max_age"])
        self.voice_base_confidence: float = float(cfg["voice_base_confidence"])
        self.targetless_confidence_scale: float = float(
            cfg["targetless_confidence_scale"]
        )
        st = cfg.get("sensitive_types") or SENSITIVE_TYPES
        self.sensitive_types: set = set(st)
        self.dropped_count: int = 0
        self.stats: Dict[str, int] = {
            "emitted": 0,
            "dropped_low_confidence": 0,
            "dropped_gaze_uncertain": 0,
            "dropped_capped": 0,
            "dropped_expired": 0,
            "emergency_stops": 0,
        }
        self._pending: Deque[Intent] = collections.deque()
        self._gestures: Deque[Dict[str, Any]] = collections.deque(maxlen=16)
        self._lock = threading.RLock()

    # -- inputs ------------------------------------------------------------

    def submit_gesture(
        self,
        gesture: str,
        point: Optional[Tuple[float, float]] = None,
        confidence: float = 0.0,
        timestamp: Optional[float] = None,
    ) -> None:
        """Queue a direct gesture observation (consumed on next process())."""
        self._gestures.append(
            {
                "gesture": str(gesture or ""),
                "point": point,
                "confidence": float(confidence),
                "timestamp": float(timestamp if timestamp is not None else now_ts()),
            }
        )

    # -- main entry point ----------------------------------------------------

    def process(
        self,
        decision: Optional[FusionDecision],
        utterance: str = "",
        now: Optional[float] = None,
    ) -> List[Intent]:
        """Resolve one fusion tick into a list of emitted intents.

        Deterministic when ``now`` (perf_counter seconds) is supplied.
        Never raises on malformed input — a ``None`` decision is treated
        as an empty one.
        """
        now = float(now if now is not None else now_ts())
        if decision is None:
            decision = FusionDecision()
        emitted: List[Intent] = []
        with self._lock:
            self._cap_used = 0

            # 1. EMERGENCY — always emitted, owns the tick, bypasses the cap.
            text = utterance or decision.utterance
            if is_emergency_utterance(text):
                stop = Intent(
                    type=IntentType.EMERGENCY_STOP,
                    confidence=1.0,
                    sources=Modality.VOICE,
                    utterance=str(text),
                    timestamp=now,
                )
                self._pending.append(stop)
                self.stats["emitted"] += 1
                self.stats["emergency_stops"] += 1
                return [stop]

            # 2. Hand/voice-confirmed click (gaze target + explicit confirm).
            click_emitted = False
            pt = decision.target_point()
            if decision.has_target and pt is not None:
                confirmations = list(decision.confirmations or [])
                if "hand:pinch" in confirmations or "voice:click" in confirmations:
                    sources = (
                        Modality.GAZE | Modality.HAND
                        if "hand:pinch" in confirmations
                        else Modality.GAZE | Modality.VOICE
                    )
                    cand = Intent(
                        type=IntentType.CLICK,
                        target=decision.target,
                        point=pt,
                        confidence=float(decision.confidence),
                        sources=sources,
                        utterance=str(text or ""),
                        timestamp=now,
                    )
                    got = self._admit(cand, now)
                    click_emitted = got is not None
                    if got is not None:
                        emitted.append(got)

            # 3. Voice-driven phrase resolution.
            if text:
                matched = match_phrase(text)
                if matched is not None:
                    itype, params = matched
                    base = float(decision.confidence)
                    if base <= 0.0:
                        base = self.voice_base_confidence
                    target = decision.target
                    if target is None and decision.point is None:
                        base *= self.targetless_confidence_scale
                    cand = Intent(
                        type=itype,
                        target=target,
                        point=pt,
                        params=dict(params),
                        confidence=base,
                        sources=Modality.VOICE,
                        utterance=str(text),
                        timestamp=now,
                    )
                    got = self._admit(cand, now)
                    if got is not None:
                        emitted.append(got)
                        if got.type in CLICK_CLASS:
                            click_emitted = True

            # 4. Gesture-direct queue (consumed within the age window).
            while self._gestures:
                if self._cap_used >= self.max_intents_per_tick:
                    # remaining gestures stay queued for the next tick —
                    # counted as a cap event, NOT a drop (nothing is lost)
                    self.stats["dropped_capped"] += 1
                    break
                g = self._gestures.popleft()
                if now - g["timestamp"] > self.intent_max_age:
                    self._drop("dropped_expired")
                    continue
                itype = GESTURE_TO_INTENT.get(g["gesture"])
                if itype is None:
                    continue
                if click_emitted and itype in CLICK_CLASS:
                    continue  # one click resolution per tick
                cand = Intent(
                    type=itype,
                    point=g["point"],
                    confidence=float(g["confidence"]),
                    sources=Modality.HAND,
                    timestamp=g["timestamp"],
                )
                got = self._admit(cand, now, keep_on_cap=self._gestures)
                if got is not None:
                    emitted.append(got)
                    if got.type in CLICK_CLASS:
                        click_emitted = True
        return emitted

    # -- queue consumption ---------------------------------------------------

    def pop_intent(self, now: Optional[float] = None) -> Optional[Intent]:
        """Pop the oldest non-expired pending intent (FIFO), else ``None``."""
        now = float(now if now is not None else now_ts())
        with self._lock:
            while self._pending:
                intent = self._pending.popleft()
                if now - intent.timestamp > self.intent_max_age:
                    self._drop("dropped_expired")
                    continue
                return intent
        return None

    def cancel_pending(self) -> None:
        """Drop every pending intent and queued gesture immediately."""
        with self._lock:
            self._pending.clear()
            self._gestures.clear()

    @property
    def pending_count(self) -> int:
        """Number of intents currently queued for :meth:`pop_intent`."""
        with self._lock:
            return len(self._pending)

    # -- sensitivity ---------------------------------------------------------

    def mark_sensitive(self, intent: Intent, text: str = "") -> bool:
        """Flag ``intent`` as requiring confirmation when sensitive (in place).

        Sensitive = type in ``sensitive_types`` OR a TYPE intent whose text
        (params ``text``, else ``text`` arg, else the utterance) exceeds
        :data:`TYPE_SENSITIVE_TEXT_LEN` chars.  Returns True when marked.
        """
        itype = intent.type
        sensitive = itype in self.sensitive_types
        if not sensitive and itype == IntentType.TYPE:
            body = intent.params.get("text") or text or intent.utterance or ""
            sensitive = len(str(body)) > TYPE_SENSITIVE_TEXT_LEN
        if sensitive:
            intent.requires_confirmation = True
        return sensitive

    # -- internals -----------------------------------------------------------

    def _admit(
        self,
        intent: Intent,
        now: float,
        keep_on_cap: Optional[Deque] = None,
    ) -> Optional[Intent]:
        """Apply the safety-filter rules; queue + return when admitted."""
        # confidence gate
        if intent.confidence < self.min_confidence:
            self._drop("dropped_low_confidence")
            return None
        # uncertain gaze never clicks
        if (
            (intent.sources & Modality.GAZE)
            and intent.type in CLICK_CLASS
            and intent.confidence < self.gaze_min_confidence
        ):
            self._drop("dropped_gaze_uncertain")
            return None
        # per-tick cap (EMERGENCY_STOP never reaches _admit)
        if self._cap_used >= self.max_intents_per_tick:
            self._drop("dropped_capped")
            if keep_on_cap is not None:  # gesture stays queued for next tick
                keep_on_cap.appendleft(
                    {
                        "gesture": {
                            IntentType.CLICK: "pinch",
                            IntentType.RIGHT_CLICK: "peace",
                            IntentType.DOUBLE_CLICK: "thumb",
                        }.get(intent.type, ""),
                        "point": intent.point,
                        "confidence": intent.confidence,
                        "timestamp": intent.timestamp,
                    }
                )
            return None
        self.mark_sensitive(intent, text=intent.utterance)
        self._pending.append(intent)
        self._cap_used += 1
        self.stats["emitted"] += 1
        return intent

    def _drop(self, reason: str) -> None:
        self.dropped_count += 1
        self.stats[reason] = self.stats.get(reason, 0) + 1
