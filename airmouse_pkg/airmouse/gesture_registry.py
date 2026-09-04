"""
airmouse.gesture_registry — v10 Full Gesture Registry 🤟
========================================================

Formal registry for EVERY gesture the system can recognize (mission §9),
plus user-defined gesture mappings with sequence patterns::

    gesture: air_delete
    pattern: fist -> swipe_left -> release
    action:  HOTKEY ctrl+backspace

Built-in labels cover the v5 hand FSM vocabulary (pointing / peace /
three / palm / fist / pinch / thumbs_up / gun + swipes) so existing
recognition keeps working unchanged, and extend it with the v10 set
(pinch_hold, pinch_release, double_pinch, grab, grab_move, circular_cw,
circular_ccw, directional motion).

Everything is pure + deterministic:  ``feed(label, now)`` in, normalized
:class:`airmouse.interfaces.Event` and optional :class:`Intent` out.
No hardware imports at module level.

Quick usage
-----------

    reg = GestureRegistry()
    intent = reg.map_gesture("pinch", point=(960, 540), confidence=0.9)
    # custom sequences:
    reg.define(CustomGestureMapping(
        name="air_delete", pattern=["fist", "swipe_left", "pinch_release"],
        intent=IntentType.HOTKEY, params={"keys": ["ctrl", "backspace"]}))
    ev = reg.feed("fist", now=1.0)          # partial: no event out yet
    ev = reg.feed("swipe_left", now=1.2)    # partial
    ev = reg.feed("pinch_release", now=1.4) # -> completes air_delete

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:  # package-relative (normal import path)
    from .interfaces import (Event, EventKind, Intent, IntentType, Modality,
                             ScreenTarget, now_ts)
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (Event, EventKind, Intent, IntentType,
                                     Modality, ScreenTarget, now_ts)

__all__ = [
    "Gestures", "SWIPE_PREFIX", "is_swipe", "GestureRegistry",
    "CustomGestureMapping", "DEFAULT_MAPPINGS",
]


# ---------------------------------------------------------------------------
# Gesture labels (superset: v5 vocabulary + v10 additions)
# ---------------------------------------------------------------------------


class Gestures:
    """Canonical gesture label constants (strings, safe to compare)."""

    # v5 hand FSM vocabulary (kept identical — regression contract)
    POINTING = "pointing"
    PEACE = "peace"
    THREE = "three"
    PALM = "palm"                # = open palm
    FIST = "fist"
    PINCH = "pinch"
    THUMBS_UP = "thumbs_up"
    GUN = "gun"
    # v10 additions (§9)
    OPEN_PALM = "palm"           # alias of PALM
    PINCH_HOLD = "pinch_hold"
    PINCH_RELEASE = "pinch_release"
    DOUBLE_PINCH = "double_pinch"
    GRAB = "grab"
    GRAB_MOVE = "grab_move"
    CIRCULAR_CW = "circular_cw"
    CIRCULAR_CCW = "circular_ccw"
    DIRECTIONAL = "directional"  # parametrized motion (payload direction)
    # swipes (produced by the v5 SwipeDetector + v10 vertical/horizontal)
    SWIPE_LEFT = "swipe_left"
    SWIPE_RIGHT = "swipe_right"
    SWIPE_UP = "swipe_up"
    SWIPE_DOWN = "swipe_down"


SWIPE_PREFIX = "swipe_"


def is_swipe(label: str) -> bool:
    """True when the label is a swipe gesture (pure)."""
    return str(label or "").startswith(SWIPE_PREFIX)


#: built-in gesture → intent mapping (§9: each gesture is a normalized
#: event with an optional default action).  "swipe_*" is resolved
#: dynamically by map_gesture.
DEFAULT_MAPPINGS: Dict[str, Tuple[IntentType, Dict[str, Any]]] = {
    Gestures.PINCH: (IntentType.CLICK, {}),
    Gestures.PINCH_HOLD: (IntentType.DRAG, {"phase": "start"}),
    Gestures.PINCH_RELEASE: (IntentType.DROP, {"phase": "end"}),
    Gestures.DOUBLE_PINCH: (IntentType.DOUBLE_CLICK, {}),
    Gestures.PEACE: (IntentType.RIGHT_CLICK, {}),
    Gestures.PALM: (IntentType.DRAG, {"phase": "start"}),
    Gestures.GRAB: (IntentType.DRAG, {"phase": "start"}),
    Gestures.GRAB_MOVE: (IntentType.MOVE, {"phase": "drag"}),
    Gestures.FIST: (IntentType.CANCEL, {"gesture": "fist"}),  # freeze/cancel
    Gestures.THUMBS_UP: (IntentType.DOUBLE_CLICK, {}),
    Gestures.GUN: (IntentType.MOVE, {"target": "center"}),
    Gestures.POINTING: (IntentType.MOVE, {"phase": "point"}),
    Gestures.THREE: (IntentType.SCROLL, {"mode": "gesture"}),
    Gestures.CIRCULAR_CW: (IntentType.SCROLL, {"direction": "down"}),
    Gestures.CIRCULAR_CCW: (IntentType.SCROLL, {"direction": "up"}),
    Gestures.DIRECTIONAL: (IntentType.MOVE, {}),
}


# ---------------------------------------------------------------------------
# Custom gesture mappings (§9 user-defined)
# ---------------------------------------------------------------------------


@dataclass
class CustomGestureMapping:
    """A user-defined gesture → action mapping.

    ``pattern`` is a list of gesture labels that must occur IN ORDER
    within ``window`` seconds total (deterministic matcher, no ML).
    The special label ``"any"`` matches any gesture; ``"!swipe"``-style
    negation is NOT supported (keep patterns explicit and testable).
    """

    name: str
    pattern: List[str]
    intent: IntentType = IntentType.HOTKEY
    params: Dict[str, Any] = field(default_factory=dict)
    window: float = 3.0            # max seconds for the whole sequence
    step_gap: float = 1.5          # max seconds between consecutive steps
    enabled: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        self.pattern = [str(p) for p in (self.pattern or [])]

    def to_json(self) -> Dict[str, Any]:
        return {
            "name": self.name, "pattern": list(self.pattern),
            "intent": self.intent.value, "params": dict(self.params),
            "window": float(self.window), "step_gap": float(self.step_gap),
            "enabled": bool(self.enabled),
            "description": str(self.description),
        }

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "CustomGestureMapping":
        try:
            intent = IntentType(str(data.get("intent", "hotkey")))
        except Exception:
            intent = IntentType.HOTKEY
        return CustomGestureMapping(
            name=str(data.get("name", "")),
            pattern=[str(p) for p in (data.get("pattern") or [])],
            intent=intent, params=dict(data.get("params") or {}),
            window=float(data.get("window", 3.0)),
            step_gap=float(data.get("step_gap", 1.5)),
            enabled=bool(data.get("enabled", True)),
            description=str(data.get("description", "")),
        )


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


class GestureRegistry:
    """Formal gesture registry (§9): every gesture → normalized Event,
    built-in gestures → default intents, user mappings → custom actions.

    - ``feed(label, now, point, confidence)``: the single entry point
      used by the perception loop.  Returns the Event for this
      observation AND any Intent produced (built-in or a completed
      custom sequence).  Deterministic given (labels, timestamps).
    - ``define(mapping)`` / ``remove(name)``: user mapping management.
    - ``load(path)`` / ``save(path)``: JSON persistence
      (default ~/.airmouse/gestures.json).
    - ``list_gestures()``: introspection for the CLI (airmouse gestures).
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(config or {})
        self.enabled = bool(cfg.get("enabled", True))
        self.min_confidence = float(cfg.get("min_confidence", 0.3))
        self.double_pinch_window = float(cfg.get("double_pinch_window", 0.6))
        self.custom: Dict[str, CustomGestureMapping] = {}
        self._lock = threading.RLock()
        # sequence matcher state (one partial per mapping; mappings are
        # matched independently, first completion wins deterministically)
        self._partials: Dict[str, Dict[str, Any]] = {}
        self._last_pinch_at = -1e9
        self._overridden: Dict[str, Tuple[IntentType, Dict[str, Any]]] = {}

    # -- built-in mapping ------------------------------------------------------

    def map_gesture(self, label: str,
                    point: Optional[Tuple[float, float]] = None,
                    target: Optional[ScreenTarget] = None,
                    confidence: float = 0.8,
                    now: Optional[float] = None) -> Optional[Intent]:
        """Built-in/overridden mapping for one gesture label → Intent.

        Swipes map dynamically (direction parameter).  Returns None for
        unknown labels or below-min-confidence observations.
        """
        now = float(now if now is not None else now_ts())
        label = str(label or "")
        if not label or confidence < self.min_confidence:
            return None
        with self._lock:
            entry = self._overridden.get(label) or DEFAULT_MAPPINGS.get(label)
        if entry is None:
            if is_swipe(label):
                direction = label[len(SWIPE_PREFIX):]
                entry = (IntentType.SWITCH_WINDOW if direction in
                         ("left", "right") else IntentType.SCROLL,
                         {"direction": direction})
            else:
                return None
        itype, params = entry
        params = dict(params or {})
        if is_swipe(label) and itype is IntentType.SCROLL:
            direction = label[len(SWIPE_PREFIX):]
            params["amount"] = 4 if direction == "up" else -4 \
                if direction == "down" else params.get("amount", 0)
        return Intent(
            type=itype, target=target,
            point=tuple(point) if point is not None else None,
            params=params, confidence=max(0.0, min(1.0, float(confidence))),
            sources=Modality.HAND, utterance="gesture:" + label, timestamp=now,
        )

    def override_mapping(self, label: str, intent: IntentType,
                         params: Optional[Dict[str, Any]] = None) -> None:
        """Override (or restore with intent=None) a built-in mapping."""
        with self._lock:
            if intent is None:
                self._overridden.pop(label, None)
            else:
                self._overridden[label] = (intent, dict(params or {}))

    # -- custom sequences ---------------------------------------------------------

    def define(self, mapping: CustomGestureMapping) -> bool:
        """Register a user-defined gesture mapping (replaces same name)."""
        if not isinstance(mapping, CustomGestureMapping) \
                or not mapping.name or not mapping.pattern:
            return False
        with self._lock:
            self.custom[mapping.name] = mapping
            self._partials.pop(mapping.name, None)
        return True

    def remove(self, name: str) -> bool:
        with self._lock:
            return self.custom.pop(str(name), None) is not None

    def get(self, name: str) -> Optional[CustomGestureMapping]:
        with self._lock:
            return self.custom.get(str(name))

    # -- the feed loop -----------------------------------------------------------

    def feed(self, label: str,
             point: Optional[Tuple[float, float]] = None,
             confidence: float = 0.8,
             now: Optional[float] = None,
             target: Optional[ScreenTarget] = None,
             payload_extra: Optional[Dict[str, Any]] = None,
             ) -> Tuple[Event, Optional[Intent]]:
        """Process one recognized gesture observation.

        Returns ``(event, intent)``.  The Event is ALWAYS produced
        (normalized §3 event).  The Intent comes from the built-in
        mapping or from a completed custom sequence (custom sequences
        suppress the built-in mapping for their label while mid-pattern).
        """
        now = float(now if now is not None else now_ts())
        label = str(label or "")
        payload: Dict[str, Any] = {"gesture": label}
        if point is not None:
            payload["point"] = (float(point[0]), float(point[1]))
        if payload_extra:
            payload.update(payload_extra)
        event = Event(
            kind=EventKind.HAND_GESTURE, modality=Modality.HAND,
            confidence=max(0.0, min(1.0, float(confidence))),
            payload=payload, source="gesture_registry", target=target,
            timestamp=now,
        )
        if not self.enabled:
            return event, None

        # double-pinch synthesis (two PINCHes inside double_pinch_window)
        synth = ""
        if label == Gestures.PINCH:
            with self._lock:
                gap = now - self._last_pinch_at
                self._last_pinch_at = now
            if gap < self.double_pinch_window:
                synth = Gestures.DOUBLE_PINCH

        intent: Optional[Intent] = None
        completed = self._feed_sequences(label, now)
        if completed is not None:
            intent = completed
        elif synth:
            intent = self.map_gesture(synth, point, target,
                                      confidence, now)
        if intent is None:
            intent = self.map_gesture(label, point, target, confidence, now)
        return event, intent

    def _feed_sequences(self, label: str, now: float) -> Optional[Intent]:
        """Advance every enabled custom mapping; first completion wins."""
        with self._lock:
            for name, mapping in self.custom.items():
                if not mapping.enabled or len(mapping.pattern) == 0:
                    continue
                st = self._partials.get(name)
                if st is None or \
                        (now - st["last_at"]) > mapping.step_gap or \
                        (now - st["started_at"]) > mapping.window:
                    st = {"idx": 0, "started_at": now, "last_at": now}
                # does the current label match the next expected step?
                expected = mapping.pattern[st["idx"]]
                if label == expected or expected == "any":
                    st["idx"] += 1
                    st["last_at"] = now
                    if st["idx"] >= len(mapping.pattern):
                        self._partials.pop(name, None)
                        return Intent(
                            type=mapping.intent, params=dict(mapping.params),
                            confidence=0.9, sources=Modality.HAND,
                            utterance="gesture_seq:" + name, timestamp=now)
                    self._partials[name] = st
                else:
                    # restart attempt when the label could start the pattern
                    st["idx"] = 1 if (label == mapping.pattern[0]
                                      or mapping.pattern[0] == "any") else 0
                    st["started_at"] = now
                    st["last_at"] = now
                    self._partials[name] = st
        return None

    # -- persistence ------------------------------------------------------------

    def load(self, path: Optional[str] = None) -> int:
        """Load user mappings from JSON.  Returns the count loaded."""
        path = path or os.path.expanduser(
            os.getenv("AIRMOUSE_GESTURES", "~/.airmouse/gestures.json"))
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            items = data.get("mappings") if isinstance(data, dict) \
                else data
            count = 0
            for item in items or []:
                m = CustomGestureMapping.from_json(item)
                if self.define(m):
                    count += 1
            return count
        except Exception:
            return 0

    def save(self, path: Optional[str] = None) -> bool:
        path = path or os.path.expanduser(
            os.getenv("AIRMOUSE_GESTURES", "~/.airmouse/gestures.json"))
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with self._lock:
                data = {"version": 1,
                        "mappings": [m.to_json()
                                     for m in self.custom.values()]}
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp, path)
            return True
        except Exception:
            return False

    # -- introspection ---------------------------------------------------------

    def list_gestures(self) -> Dict[str, Any]:
        """Registry snapshot for the CLI (airmouse gestures)."""
        with self._lock:
            builtins = {}
            for label in sorted(set(list(DEFAULT_MAPPINGS)
                                    + [Gestures.SWIPE_LEFT,
                                       Gestures.SWIPE_RIGHT,
                                       Gestures.SWIPE_UP,
                                       Gestures.SWIPE_DOWN])):
                entry = self._overridden.get(label) \
                    or DEFAULT_MAPPINGS.get(label)
                builtins[label] = entry[0].value if entry else "switch_window"
            return {
                "builtin": builtins,
                "custom": {name: m.to_json()
                           for name, m in self.custom.items()},
                "enabled": self.enabled,
            }

    def reset(self) -> None:
        with self._lock:
            self.custom.clear()
            self._partials.clear()
            self._overridden.clear()
            self._last_pinch_at = -1e9
