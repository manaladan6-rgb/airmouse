"""
Multimodal Fusion v7 — gaze 👁 + hand 🖐 + voice 🎤 + mouse 🖱 + keyboard ⌨ + screen 🖥
=====================================================================================

The fusion engine is the single arbitration point that turns a stream of
per-modality observations into ONE :class:`airmouse.interfaces.FusionDecision`
per tick.  It answers three questions every frame:

1. WHERE is the user's attention?          -> ``decision.target`` / ``decision.point``
2. HOW sure are we, and who agrees?        -> ``decision.confidence`` + ``decision.confirmations``
3. WHAT did the user just say?             -> ``decision.utterance``

Modes (``FusionMode``)
----------------------
Which modality leads is a MODE decision, expressed as the
:data:`PRIORITY_WEIGHTS` matrix (mode -> modality -> weight 0.0..1.0).
A modality's *score* is ``confidence × priority_weight``; the highest score
wins the tick.  Conflicting claims (spatially distant targets) are recorded
in :attr:`MultimodalFusion.last_conflict`.

    HAND        hand 1.0 leads (v5 behaviour), gaze 0.4 assists
    GAZE        gaze 1.0 leads, hand 0.3 assists, dwell/blink confirms
    VOICE       voice 1.0 + the LAST STABLE target (intent + where we were)
    FUSION      gaze 1.0 targets, hand 0.85 confirms (pinch), voice 0.95 intents
    HANDS_FREE  gaze 1.0 + voice 1.0; hand completely ignored (weight 0.0)
    ASSIST      observe everything, no auto target lock: confirmations are
                NOT applied and confidence reflects raw observation only

Confirmation patterns
---------------------
When a gaze anchor exists (semantic target or raw point):

* a hand ``pinch``/``pinch_release`` event within ``confirm_window`` whose
  point lies within ``confirm_radius_px`` of the anchor -> ``"hand:pinch"``
* a voice utterance within ``voice_intent_window`` whose mapped command is a
  click-class command (``CLICK_CLASS_COMMANDS``) -> ``"voice:click"`` etc.

Each confirmation boosts the combined confidence by +0.25
(:data:`CONFIRM_BOOST`), capped at 1.0.  A modality whose priority weight is
0.0 (ignored, e.g. hand in HANDS_FREE) can neither win nor confirm.

Determinism & threading
-----------------------
All feeders accept an explicit ``timestamp`` (``time.perf_counter`` seconds,
the v9 convention) so the whole arbitration is deterministic under test.
Internal state is guarded by a single ``RLock``; ``submit()`` and all
``update_*`` feeders are thread-safe.

Config keys (defaults in :data:`DEFAULT_CONFIG`)
------------------------------------------------
    stale_after               0.8   s  — older modality state is treated absent
    confirm_window            0.9   s  — hand pinch look-back for confirmation
    confirm_radius_px         140   px — pinch/near agreement radius
    mode_switch_min_interval  0.35  s  — rate limit for set_mode()
    min_combined_confidence   0.35     — below this no target/point is locked
    voice_intent_window       1.5   s  — voice utterance look-back for intents
    window                    3.0   s  — recent-event retention window

House style: heavy/optional deps are NOT needed here — the module is pure
stdlib + :mod:`airmouse.interfaces` types and imports headless.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import collections
import math
import threading
from typing import Any, Deque, Dict, List, Optional, Tuple, Union

try:  # package-relative (normal import path)
    from .interfaces import (
        FusionDecision,
        FusionEvent,
        FusionMode,
        Modality,
        ScreenTarget,
        now_ts,
    )
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (
        FusionDecision,
        FusionEvent,
        FusionMode,
        Modality,
        ScreenTarget,
        now_ts,
    )

__all__ = [
    "DEFAULT_CONFIG",
    "PRIORITY_WEIGHTS",
    "CLICK_CLASS_COMMANDS",
    "PINCH_KINDS",
    "CONFIRM_BOOST",
    "MultimodalFusion",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Documented configuration defaults (see module docstring).
DEFAULT_CONFIG: Dict[str, float] = {
    "stale_after": 0.8,             # seconds a modality state stays fresh
    "confirm_window": 0.9,          # seconds look-back for hand pinch confirms
    "confirm_radius_px": 140.0,     # px radius: pinch near gaze == agreement
    "mode_switch_min_interval": 0.35,  # seconds between set_mode() switches
    "min_combined_confidence": 0.35,   # below this no target lock is made
    "voice_intent_window": 1.5,     # seconds look-back for voice intent confirms
    "window": 3.0,                  # seconds of recent-event retention
}

#: Mode priority matrix: FusionMode -> {Modality: weight}.
#: score = confidence * weight; weight 0.0 == modality ignored this mode.
PRIORITY_WEIGHTS: Dict[FusionMode, Dict[Modality, float]] = {
    FusionMode.HAND: {
        Modality.HAND: 1.0,
        Modality.GAZE: 0.4,
        Modality.VOICE: 0.6,
        Modality.MOUSE: 0.3,
        Modality.KEYBOARD: 0.3,
        Modality.SCREEN: 0.3,
    },
    FusionMode.GAZE: {
        Modality.GAZE: 1.0,
        Modality.HAND: 0.3,
        Modality.VOICE: 0.6,
        Modality.MOUSE: 0.2,
        Modality.KEYBOARD: 0.2,
        Modality.SCREEN: 0.3,
    },
    FusionMode.VOICE: {
        Modality.VOICE: 1.0,
        Modality.GAZE: 0.35,
        Modality.HAND: 0.2,
        Modality.MOUSE: 0.3,
        Modality.KEYBOARD: 0.3,
        Modality.SCREEN: 0.3,
    },
    FusionMode.FUSION: {
        Modality.GAZE: 1.0,      # gaze leads: it owns the target
        Modality.HAND: 0.85,     # hand confirms (pinch) + may point
        Modality.VOICE: 0.95,    # voice carries intents
        Modality.MOUSE: 0.3,
        Modality.KEYBOARD: 0.3,
        Modality.SCREEN: 0.3,
    },
    FusionMode.HANDS_FREE: {
        Modality.GAZE: 1.0,
        Modality.VOICE: 1.0,
        Modality.HAND: 0.0,      # completely ignored in hands-free
        Modality.MOUSE: 0.25,
        Modality.KEYBOARD: 0.25,
        Modality.SCREEN: 0.3,
    },
    FusionMode.ASSIST: {
        # observe-all: nobody auto-locks, weights only shape the report
        Modality.GAZE: 0.6,
        Modality.HAND: 0.6,
        Modality.VOICE: 0.6,
        Modality.MOUSE: 0.4,
        Modality.KEYBOARD: 0.4,
        Modality.SCREEN: 0.4,
    },
}

#: Hand gesture kinds that count as click confirmations.
PINCH_KINDS: Tuple[str, ...] = ("pinch", "pinch_release")

#: Voice commands that map to click-class intents (confirmation-worthy).
CLICK_CLASS_COMMANDS = frozenset({
    "click", "double_click", "right_click", "middle_click",
    "select", "confirm",
})

#: Confidence bonus added per unique confirmation label.
CONFIRM_BOOST: float = 0.25

# Fixed arbitration order for exact ties (earlier wins).
_PRIORITY_ORDER: Dict[str, int] = {"gaze": 0, "hand": 1, "mouse": 2}


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------

def _coerce_mode(mode: Union[FusionMode, str, None]) -> Optional[FusionMode]:
    """Accept a FusionMode, its value ("hands_free") or name ("HANDS_FREE").

    Returns None for anything unrecognised (caller decides the policy).
    """
    if isinstance(mode, FusionMode):
        return mode
    if isinstance(mode, str):
        key = mode.strip().lower().replace("-", "_").replace(" ", "_")
        for candidate in FusionMode:
            if key in (candidate.value, candidate.name.lower()):
                return candidate
    return None


def _as_point(value: Any) -> Optional[Tuple[float, float]]:
    """Coerce a 2-sequence into a (float, float) tuple; None when invalid."""
    if value is None:
        return None
    try:
        return (float(value[0]), float(value[1]))
    except Exception:
        return None


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Euclidean distance between two 2D points."""
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


# ---------------------------------------------------------------------------
# Fusion engine
# ---------------------------------------------------------------------------

class MultimodalFusion:
    """Thread-safe multimodal arbitration engine (v7).

    Lifecycle::

        fusion = MultimodalFusion(mode=FusionMode.FUSION)
        # per frame — feed the freshest per-modality state (any subset):
        fusion.update_gaze(point, target, confidence, timestamp=now)
        fusion.update_hand(point, "pinch", 0.9, timestamp=now)
        fusion.update_voice("click here", "click", 0.95, timestamp=now)
        fusion.update_mouse((512, 300), timestamp=now)
        fusion.update_keyboard("k", timestamp=now)
        # ... or push raw FusionEvent objects:
        fusion.submit(FusionEvent(Modality.HAND, "pinch", {"point": (x, y)},

        # then arbitrate exactly once per tick:
        decision = fusion.update(now=now)

    ``update()`` never raises on missing modalities: anything stale (older
    than ``stale_after``) is simply treated as absent.  Given the same
    injected events and timestamps the output is fully deterministic.
    """

    def __init__(self,
                 mode: Union[FusionMode, str] = FusionMode.FUSION,
                 config: Optional[Dict[str, Any]] = None) -> None:
        coerced = _coerce_mode(mode)
        # Unknown mode string at construction: fall back to FUSION (the
        # permissive default); set_mode() is the strict runtime path.
        self._mode: FusionMode = coerced if coerced is not None else FusionMode.FUSION
        self.config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        if config:
            try:
                self.config.update({k: v for k, v in config.items() if v is not None})
            except Exception:
                pass  # a broken config dict degrades to defaults, never raises

        self._lock = threading.RLock()
        self._events: "Deque[FusionEvent]" = collections.deque(maxlen=512)

        # Latest per-modality state; ``ts`` is None until first observation.
        self._gaze: Dict[str, Any] = {"point": None, "target": None,
                                      "confidence": 0.0, "ts": None}
        self._hand: Dict[str, Any] = {"point": None, "gesture": "",
                                      "confidence": 0.0, "ts": None}
        self._voice: Dict[str, Any] = {"text": "", "command": "",
                                       "confidence": 0.0, "ts": None}
        self._mouse: Dict[str, Any] = {"point": None, "confidence": 1.0, "ts": None}
        self._keyboard: Dict[str, Any] = {"key": "", "ts": None}

        self._last_mode_switch: float = float("-inf")
        self._last_decision: Optional[FusionDecision] = None
        self._last_conflict: Optional[Dict[str, Any]] = None
        self._stable_target: Optional[ScreenTarget] = None

    # -- properties ----------------------------------------------------------

    @property
    def mode(self) -> FusionMode:
        """Currently active fusion mode."""
        return self._mode

    @property
    def last_decision(self) -> Optional[FusionDecision]:
        """The most recent FusionDecision produced by update() (or None)."""
        return self._last_decision

    @property
    def last_conflict(self) -> Optional[Dict[str, Any]]:
        """Conflict record from the last update() tick, or None.

        Shape::

            {"at": ts, "mode": "FUSION", "winner": "gaze",
             "winner_score": 0.9, "scores": {"gaze": 0.9, "hand": 0.32},
             "losers": [{"modality": "hand", "score": 0.32, "distance": 950.1}]}
        """
        return self._last_conflict

    # -- mode management -------------------------------------------------------

    def set_mode(self, mode: Union[FusionMode, str], now: Optional[float] = None) -> bool:
        """Switch the fusion mode (rate-limited).

        Accepts a :class:`FusionMode` or a case-insensitive value/name string
        ("fusion", "HANDS_FREE", "hands-free", ...).  Unknown modes return
        False without changing state.  A switch inside
        ``mode_switch_min_interval`` seconds of the previous one is refused
        (False) to debounce gesture/voice triggered flapping.  Switching to
        the already-active mode is a successful no-op.

        ``now`` may be injected for deterministic tests.
        """
        target = _coerce_mode(mode)
        if target is None:
            return False
        ts = float(now_ts() if now is None else now)
        with self._lock:
            if target is self._mode:
                return True
            interval = float(self.config.get("mode_switch_min_interval", 0.35))
            if (ts - self._last_mode_switch) < interval:
                return False
            self._mode = target
            self._last_mode_switch = ts
            return True

    # -- feeders (per-modality latest state + event history) -------------------

    def update_gaze(self,
                    point: Optional[Tuple[float, float]],
                    target: Optional[ScreenTarget],
                    confidence: float,
                    timestamp: Optional[float] = None) -> None:
        """Feed the latest gaze state: raw screen point and/or semantic target."""
        ts = float(now_ts() if timestamp is None else timestamp)
        event = FusionEvent(modality=Modality.GAZE, kind="target",
                            payload={"point": _as_point(point), "target": target},
                            confidence=float(confidence), timestamp=ts)
        with self._lock:
            self._apply_event(event)
            self._push_event(event)

    def update_hand(self,
                    point: Optional[Tuple[float, float]],
                    gesture: str,
                    confidence: float,
                    timestamp: Optional[float] = None) -> None:
        """Feed the latest hand state: cursor point (if any) + gesture name."""
        ts = float(now_ts() if timestamp is None else timestamp)
        event = FusionEvent(modality=Modality.HAND, kind=str(gesture or "point"),
                            payload={"point": _as_point(point)},
                            confidence=float(confidence), timestamp=ts)
        with self._lock:
            self._apply_event(event)
            self._push_event(event)

    def update_voice(self,
                     text: str,
                     command: Optional[str] = None,
                     confidence: float = 0.0,
                     timestamp: Optional[float] = None) -> None:
        """Feed the latest voice utterance (text) and optional mapped command.

        ``command`` is a plain string ("click", "scroll_up", ...) or any
        enum-like value with a ``.value`` attribute; None means "no command
        recognized".
        """
        ts = float(now_ts() if timestamp is None else timestamp)
        cmd = command
        if cmd is not None and not isinstance(cmd, str):
            cmd = getattr(cmd, "value", str(cmd))
        event = FusionEvent(modality=Modality.VOICE, kind="utterance",
                            payload={"text": str(text or ""), "command": cmd or ""},
                            confidence=float(confidence), timestamp=ts)
        with self._lock:
            self._apply_event(event)
            self._push_event(event)

    def update_mouse(self, point: Tuple[float, float],
                     timestamp: Optional[float] = None) -> None:
        """Feed the latest physical mouse position (high trust, low priority)."""
        ts = float(now_ts() if timestamp is None else timestamp)
        event = FusionEvent(modality=Modality.MOUSE, kind="move",
                            payload={"point": _as_point(point)},
                            confidence=1.0, timestamp=ts)
        with self._lock:
            self._apply_event(event)
            self._push_event(event)

    def update_keyboard(self, key: str,
                        timestamp: Optional[float] = None) -> None:
        """Feed the latest physical key press (context signal, no target)."""
        ts = float(now_ts() if timestamp is None else timestamp)
        event = FusionEvent(modality=Modality.KEYBOARD, kind="key",
                            payload={"key": str(key or "")},
                            confidence=1.0, timestamp=ts)
        with self._lock:
            self._apply_event(event)
            self._push_event(event)

    def submit(self, event: FusionEvent) -> bool:
        """Submit a raw :class:`FusionEvent` (thread-safe).

        The event is appended to the recent window (``window`` seconds, oldest
        pruned) AND applied to the matching per-modality latest state, so
        ``submit()`` and the ``update_*`` feeders are interchangeable.
        Malformed events are ignored (False) instead of raising.
        """
        if event is None:
            return False
        try:
            getattr(event, "modality")
            float(getattr(event, "timestamp", 0.0) or 0.0)
        except Exception:
            return False
        with self._lock:
            self._apply_event(event)
            self._push_event(event)
        return True

    # -- internal state management ----------------------------------------------

    def _apply_event(self, event: Any) -> None:
        """Merge one event into the per-modality latest state (lock held)."""
        mod = getattr(event, "modality", Modality.NONE)
        kind = str(getattr(event, "kind", "") or "")
        payload = getattr(event, "payload", None) or {}
        try:
            conf = float(getattr(event, "confidence", 0.0) or 0.0)
        except Exception:
            conf = 0.0
        try:
            ts = float(getattr(event, "timestamp", 0.0) or 0.0)
        except Exception:
            ts = float(now_ts())

        if mod is Modality.GAZE:
            self._gaze = {
                "point": _as_point(payload.get("point")),
                "target": payload.get("target"),
                "confidence": conf,
                "ts": ts,
            }
        elif mod is Modality.HAND:
            self._hand = {
                "point": _as_point(payload.get("point")),
                "gesture": kind,
                "confidence": conf,
                "ts": ts,
            }
        elif mod is Modality.VOICE:
            cmd = payload.get("command")
            if cmd is not None and not isinstance(cmd, str):
                cmd = getattr(cmd, "value", str(cmd))
            self._voice = {
                "text": str(payload.get("text", "") or ""),
                "command": str(cmd or ""),
                "confidence": conf,
                "ts": ts,
            }
        elif mod is Modality.MOUSE:
            try:
                mconf = float(payload.get("confidence", 1.0) or 1.0)
            except Exception:
                mconf = 1.0
            self._mouse = {"point": _as_point(payload.get("point")),
                           "confidence": mconf, "ts": ts}
        elif mod is Modality.KEYBOARD:
            self._keyboard = {"key": str(payload.get("key", kind) or kind), "ts": ts}

    def _push_event(self, event: FusionEvent) -> None:
        """Append to the recent window and prune anything expired (lock held)."""
        self._events.append(event)
        try:
            window = float(self.config.get("window", DEFAULT_CONFIG["window"]))
        except Exception:
            window = DEFAULT_CONFIG["window"]
        newest = max(float(getattr(e, "timestamp", 0.0) or 0.0) for e in self._events)
        cutoff = newest - window
        while self._events and float(getattr(self._events[0], "timestamp", 0.0) or 0.0) < cutoff:
            self._events.popleft()

    def _is_fresh(self, state: Dict[str, Any], now: float, stale_after: float) -> bool:
        """True when the state exists and is not older than ``stale_after``."""
        ts = state.get("ts")
        return ts is not None and (now - float(ts)) <= stale_after

    @staticmethod
    def _effective_point(candidate: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        """Semantic target center if available, else the raw point."""
        target = candidate.get("target")
        if target is not None:
            try:
                return target.center
            except Exception:
                return None
        return candidate.get("point")

    # -- THE arbitration tick -----------------------------------------------------

    def update(self, now: Optional[float] = None) -> FusionDecision:
        """Arbitrate one tick and return a :class:`FusionDecision`.

        Pipeline (all under the state lock):

        1. staleness filter — states older than ``stale_after`` are absent;
           modalities with priority weight 0.0 (ignored) never contribute;
        2. candidate collection — gaze / hand / mouse claims with a point or
           target (voice is an intent source, keyboard a context signal);
        3. winner selection — ``score = confidence × weight``; ties resolved
           by the fixed priority gaze > hand > mouse; conflicting claims
           (effective points farther apart than ``confirm_radius_px``) are
           recorded in :attr:`last_conflict`;
        4. VOICE-mode override — a fresh voice command locks the last stable
           target;
        5. confirmation patterns — hand pinch near the anchor and click-class
           voice commands within their windows (skipped in ASSIST mode);
        6. combined confidence — ``min(1.0, max_conf + 0.25/confirmation)``
           (raw observation confidence in ASSIST); below
           ``min_combined_confidence`` no target/point is locked.

        Deterministic for a given set of injected events/timestamps.
        """
        now = float(now_ts() if now is None else now)
        with self._lock:
            cfg = self.config
            stale_after = float(cfg.get("stale_after", DEFAULT_CONFIG["stale_after"]))
            confirm_window = float(cfg.get("confirm_window",
                                           DEFAULT_CONFIG["confirm_window"]))
            radius = float(cfg.get("confirm_radius_px",
                                   DEFAULT_CONFIG["confirm_radius_px"]))
            min_conf = float(cfg.get("min_combined_confidence",
                                     DEFAULT_CONFIG["min_combined_confidence"]))
            voice_window = float(cfg.get("voice_intent_window",
                                         DEFAULT_CONFIG["voice_intent_window"]))
            window = float(cfg.get("window", DEFAULT_CONFIG["window"]))
            weights = PRIORITY_WEIGHTS.get(self._mode,
                                           PRIORITY_WEIGHTS[FusionMode.FUSION])

            # 0. prune event history relative to this tick
            cutoff = now - window
            while self._events and \
                    float(getattr(self._events[0], "timestamp", 0.0) or 0.0) < cutoff:
                self._events.popleft()

            decision = FusionDecision(mode=self._mode, timestamp=now)
            contributing = Modality.NONE
            candidates: List[Dict[str, Any]] = []

            gaze_ok = self._is_fresh(self._gaze, now, stale_after)
            hand_ok = self._is_fresh(self._hand, now, stale_after)
            voice_ok = self._is_fresh(self._voice, now, stale_after)
            mouse_ok = self._is_fresh(self._mouse, now, stale_after)
            key_ok = self._is_fresh(self._keyboard, now, stale_after)

            w_gaze = float(weights.get(Modality.GAZE, 0.0))
            w_hand = float(weights.get(Modality.HAND, 0.0))
            w_voice = float(weights.get(Modality.VOICE, 0.0))
            w_mouse = float(weights.get(Modality.MOUSE, 0.0))
            w_key = float(weights.get(Modality.KEYBOARD, 0.0))

            # 1. collect fresh, non-ignored modalities --------------------------
            if gaze_ok and w_gaze > 0.0:
                contributing |= Modality.GAZE
                if float(self._gaze["confidence"]) > 0.0:
                    candidates.append({
                        "name": "gaze", "modality": Modality.GAZE,
                        "confidence": float(self._gaze["confidence"]),
                        "weight": w_gaze,
                        "point": self._gaze["point"],
                        "target": self._gaze["target"],
                    })
            if hand_ok and w_hand > 0.0:
                contributing |= Modality.HAND
                if float(self._hand["confidence"]) > 0.0 and \
                        self._hand["point"] is not None:
                    candidates.append({
                        "name": "hand", "modality": Modality.HAND,
                        "confidence": float(self._hand["confidence"]),
                        "weight": w_hand,
                        "point": self._hand["point"],
                        "target": None,
                    })
            if mouse_ok and w_mouse > 0.0:
                contributing |= Modality.MOUSE
                if self._mouse["point"] is not None:
                    candidates.append({
                        "name": "mouse", "modality": Modality.MOUSE,
                        "confidence": float(self._mouse["confidence"]),
                        "weight": w_mouse,
                        "point": self._mouse["point"],
                        "target": None,
                    })
            if voice_ok and w_voice > 0.0:
                contributing |= Modality.VOICE
            if key_ok and w_key > 0.0:
                contributing |= Modality.KEYBOARD

            decision.contributing = contributing
            self._last_conflict = None

            # 2. winner selection ----------------------------------------------
            candidates.sort(key=lambda c: (-(c["confidence"] * c["weight"]),
                                           _PRIORITY_ORDER.get(c["name"], 99)))
            winner = candidates[0] if candidates else None

            if winner is not None:
                wpoint = self._effective_point(winner)
                losers: List[Dict[str, Any]] = []
                for other in candidates[1:]:
                    opoint = self._effective_point(other)
                    if wpoint is None or opoint is None:
                        continue
                    d = _dist(wpoint, opoint)
                    if d > radius:
                        losers.append({
                            "modality": other["name"],
                            "score": other["confidence"] * other["weight"],
                            "distance": d,
                        })
                if losers:
                    self._last_conflict = {
                        "at": now,
                        "mode": self._mode.name,
                        "winner": winner["name"],
                        "winner_score": winner["confidence"] * winner["weight"],
                        "scores": {c["name"]: c["confidence"] * c["weight"]
                                   for c in candidates},
                        "losers": losers,
                    }

                decision.target = winner["target"]
                decision.point = None if winner["target"] is not None \
                    else winner["point"]

            base_conf = max((c["confidence"] for c in candidates), default=0.0)

            # 3. VOICE mode: fresh command locks the last stable target --------
            if self._mode is FusionMode.VOICE and voice_ok and \
                    str(self._voice["command"] or ""):
                locked = self._stable_target
                if locked is None and winner is not None:
                    locked = winner["target"]
                if locked is not None:
                    decision.target = locked
                    decision.point = None
                elif decision.point is None:
                    if gaze_ok and self._gaze["point"] is not None:
                        decision.point = self._gaze["point"]
                    elif mouse_ok and self._mouse["point"] is not None:
                        decision.point = self._mouse["point"]
                if float(self._voice["confidence"]) > base_conf:
                    base_conf = float(self._voice["confidence"])

            # 4. confirmation patterns (never in ASSIST) -------------------------
            confirmations: List[str] = []
            if self._mode is not FusionMode.ASSIST:
                anchor = decision.target.center if decision.target is not None \
                    else decision.point
                if anchor is not None:
                    seen = set()
                    if w_hand > 0.0:  # ignored modalities cannot confirm
                        fallback_pt = self._hand["point"] if hand_ok else None
                        for ev in reversed(self._events):
                            if getattr(ev, "modality", None) is not Modality.HAND:
                                continue
                            kind = str(getattr(ev, "kind", "") or "")
                            if kind not in PINCH_KINDS:
                                continue
                            dt = now - float(getattr(ev, "timestamp", 0.0) or 0.0)
                            if dt < 0.0 or dt > confirm_window:
                                continue
                            pt = _as_point((getattr(ev, "payload", None) or {})
                                           .get("point")) or fallback_pt
                            if pt is None:
                                continue
                            if _dist(pt, anchor) <= radius:
                                label = "hand:{}".format(kind)
                                if label not in seen:
                                    seen.add(label)
                                    confirmations.append(label)
                    if w_voice > 0.0:
                        for ev in reversed(self._events):
                            if getattr(ev, "modality", None) is not Modality.VOICE:
                                continue
                            payload = getattr(ev, "payload", None) or {}
                            cmd = str(payload.get("command", "") or "")
                            if cmd not in CLICK_CLASS_COMMANDS:
                                continue
                            dt = now - float(getattr(ev, "timestamp", 0.0) or 0.0)
                            if dt < 0.0 or dt > voice_window:
                                continue
                            label = "voice:{}".format(cmd)
                            if label not in seen:
                                seen.add(label)
                                confirmations.append(label)
            decision.confirmations = confirmations

            # 5. combined confidence + gate ---------------------------------------
            if self._mode is FusionMode.ASSIST:
                combined = base_conf            # observation only, no boost
            elif base_conf > 0.0:
                combined = min(1.0, base_conf + CONFIRM_BOOST * len(confirmations))
            else:
                combined = 0.0
            decision.confidence = combined
            if combined < min_conf:
                decision.target = None
                decision.point = None

            # 6. utterance + stable-target bookkeeping -----------------------------
            if voice_ok and self._voice["text"]:
                decision.utterance = str(self._voice["text"])
            if decision.target is not None:
                self._stable_target = decision.target

            self._last_decision = decision
            return decision

    # -- observability / lifecycle ---------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Observability snapshot: mode, event count, modality ages, extras."""
        now = now_ts()
        with self._lock:
            def age(state: Dict[str, Any]) -> Optional[float]:
                ts = state.get("ts")
                return None if ts is None else max(0.0, now - float(ts))

            return {
                "mode": self._mode.value,
                "events": len(self._events),
                "ages": {
                    "gaze": age(self._gaze),
                    "hand": age(self._hand),
                    "voice": age(self._voice),
                    "mouse": age(self._mouse),
                    "keyboard": age(self._keyboard),
                },
                "last_decision": self._last_decision,
                "last_conflict": self._last_conflict,
            }

    def reset(self) -> None:
        """Clear ALL state: events, per-modality states, decision, conflicts."""
        with self._lock:
            self._events.clear()
            self._gaze = {"point": None, "target": None, "confidence": 0.0, "ts": None}
            self._hand = {"point": None, "gesture": "", "confidence": 0.0, "ts": None}
            self._voice = {"text": "", "command": "", "confidence": 0.0, "ts": None}
            self._mouse = {"point": None, "confidence": 1.0, "ts": None}
            self._keyboard = {"key": "", "ts": None}
            self._last_decision = None
            self._last_conflict = None
            self._stable_target = None
