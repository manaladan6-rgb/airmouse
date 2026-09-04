"""
airmouse.nl_control — v9 Natural-Language Control 🗣️➡️🎯
=========================================================

Turns a raw spoken/typed utterance into a structured
:class:`airmouse.interfaces.NLUResult` (v9 pattern layer) with a graceful
fall back to the legacy v5 ``VoiceCommand`` keyword space.

Design rules
------------
* **Deterministic**: a pure function of the text (+ injected clock for the
  controller).  No network, no model downloads, imports headless.
* **First match wins**: the pattern table is ordered most-specific first
  (longer / multi-word patterns before their substrings, e.g. "double
  click" before "click", "stop everything" before "stop").
* **The NL layer never invents coordinates.**  When an utterance says
  "that" / "this" / "it" / "here" the result carries ``target_ref`` and the
  *InteractionAgent* resolves the reference through the fusion decision /
  gaze target / screen model.  Deictic resolution is the agent's job.
* **Graceful degradation**: an empty / garbage utterance yields
  ``is_command=False`` with ``confidence=0``; a non-v9 utterance that still
  contains a legacy keyword ("please zoom in now") yields the legacy
  ``fallback_command`` with ``is_command=False`` and ``confidence=0.3``.

Public API
----------
* :func:`parse_utterance`     — text → NLUResult (pure).
* :func:`resolve_fallback`    — text → legacy VoiceCommand string (pure).
* :func:`normalize_text`      — lowercase / strip punctuation / collapse.
* :func:`nlu_to_intent`       — NLUResult + FusionDecision → Intent (v9
  deictic resolution helper shared by hands_free and agent).
* :class:`NLController`       — parse + consecutive-duplicate suppression.

Confidence contract (spec)
--------------------------
* ``0.9`` — exact regex hit **with** an explicit target_ref ("click that").
* ``0.75`` — pattern hit without a target ("scroll down a lot").
* ``0.6`` — generic fixed-phrase commands ("pause", "minimize", "stop").

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, List, Match, Optional, Tuple

try:  # package-relative (normal import path)
    from .interfaces import (
        FusionDecision,
        Intent,
        IntentType,
        Modality,
        NLUResult,
        now_ts,
    )
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (
        FusionDecision,
        Intent,
        IntentType,
        Modality,
        NLUResult,
        now_ts,
    )

__all__ = [
    "DEFAULT_NL_CONFIG",
    "PATTERNS",
    "LEGACY_FALLBACK_KEYWORDS",
    "DEICTIC_REFS",
    "TYPE_TEXT_MAX",
    "normalize_text",
    "parse_utterance",
    "resolve_fallback",
    "nlu_to_intent",
    "NLController",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum characters captured for a "type ..." dictation intent.
TYPE_TEXT_MAX: int = 200

#: Deictic (point-at-something) references the AGENT must resolve — the NL
#: layer only tags them, it never resolves coordinates itself.
DEICTIC_REFS: Tuple[str, ...] = ("this", "that", "it", "here", "there")

#: Documented configuration (keys read by :class:`NLController`).
DEFAULT_NL_CONFIG: Dict[str, Any] = {
    "dedup_window": 1.2,      # s — identical consecutive utterances suppressed
    "type_text_max": TYPE_TEXT_MAX,
}

#: Scroll sign convention (matches airmouse.intent.PHRASE_TO_INTENT):
#: "scroll up" → POSITIVE amount, "scroll down" → NEGATIVE amount.
_SCROLL_MAGNITUDES: Dict[str, int] = {
    "a little": 2,
    "slightly": 2,
    "a lot": 8,
    "way": 8,
}
_SCROLL_DEFAULT: int = 3

#: Legacy v5 VoiceCommand keyword map (utterance fragment → command name).
#: Used ONLY when no v9 pattern matched; result stays ``is_command=False``.
LEGACY_FALLBACK_KEYWORDS: Dict[str, str] = {
    "emergency stop": "emergency_stop",
    "stop everything": "emergency_stop",
    "double click": "double_click",
    "right click": "right_click",
    "middle click": "middle_click",
    "left click": "click",
    "switch window": "switch_window",
    "next window": "switch_window",
    "scroll up": "scroll_up",
    "scroll down": "scroll_down",
    "scroll left": "scroll_left",
    "scroll right": "scroll_right",
    "zoom in": "zoom_in",
    "zoom out": "zoom_out",
    "go back": "back",
    "go forward": "forward",
    "play pause": "play_pause",
    "next track": "next_track",
    "previous track": "prev_track",
    "screenshot": "screenshot",
    "unfreeze": "unfreeze",
    "freeze": "freeze",
    "minimize": "minimize",
    "maximize": "maximize",
    "click": "click",
    "drag": "drag",
    "drop": "drop",
    "cancel": "cancel",
    "stop": "stop",
    "mute": "mute",
    "unmute": "unmute",
    "pause": "play_pause",
    "play": "play_pause",
    "quit": "quit",
    "exit": "quit",
}

# Longest fragment first so "scroll up" wins over "up"-less substrings and
# "double click" wins over "click".
_LEGACY_ORDERED: List[Tuple[str, str]] = sorted(
    LEGACY_FALLBACK_KEYWORDS.items(), key=lambda kv: len(kv[0]), reverse=True
)


def normalize_text(text: Any) -> str:
    """Lowercase, strip punctuation (keep digits), collapse whitespace.

    Pure + total: accepts anything, always returns a ``str``.
    """
    if text is None:
        return ""
    s = str(text).lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)   # punctuation → space (digits kept)
    return re.sub(r"\s+", " ", s).strip()


def resolve_fallback(text: Any) -> str:
    """Legacy v5 ``VoiceCommand``-style keyword match.

    Returns the mapped command name ("zoom_in", ...) or ``""`` when no
    legacy keyword occurs in the normalized text.  Longest fragment wins.
    """
    norm = normalize_text(text)
    if not norm:
        return ""
    for fragment, command in _LEGACY_ORDERED:
        if fragment in norm:
            return command
    return ""


# ---------------------------------------------------------------------------
# Pattern table
# ---------------------------------------------------------------------------
# Each entry: (compiled regex anchored on the normalized text, IntentType,
# param-builder, generic-flag).  The builder receives the regex match and
# returns ``(params, target_ref)``.  ``generic=True`` marks fixed-phrase
# commands that can never reference a target → confidence 0.6.
#
# ORDER = SPECIFICITY (first match wins):
#   multi-word / compound phrases before their substrings, e.g.
#   "stop everything" before "stop", "double click" before "click",
#   "switch window" before "window"-less catch-alls.
# ---------------------------------------------------------------------------

PATTERN_SPEC: List[Tuple[str, IntentType, Callable[[Match], Tuple[Dict[str, Any], str]], bool]] = [
    # -- emergency (before bare "stop") -------------------------------------
    (r"stop everything|emergency stop",
     IntentType.EMERGENCY_STOP,
     lambda m: ({}, ""),
     True),
    # -- compound click forms (before bare "click") --------------------------
    (r"double click(?: (?P<ref>that|this|it|here))?",
     IntentType.DOUBLE_CLICK,
     lambda m: ({}, m.group("ref") or ""),
     False),
    (r"right click(?: (?P<ref>that|this|it|here))?",
     IntentType.RIGHT_CLICK,
     lambda m: ({}, m.group("ref") or ""),
     False),
    # -- bare click ----------------------------------------------------------
    (r"click(?: (?P<ref>that|this|it|here))?",
     IntentType.CLICK,
     lambda m: ({}, m.group("ref") or ""),
     False),
    # -- window management ----------------------------------------------------
    (r"(?:switch|next) window",
     IntentType.SWITCH_WINDOW,
     lambda m: ({"what": "window"}, ""),
     True),
    (r"minimize",
     IntentType.MINIMIZE,
     lambda m: ({}, ""),
     True),
    (r"maximize",
     IntentType.MAXIMIZE,
     lambda m: ({}, ""),
     True),
    (r"close(?: (?P<ref>this|that|it|the))?(?: (?P<what>window|tab|dialog))?",
     IntentType.CLOSE,
     lambda m: (
         {"what": m.group("what")} if m.group("what") else {},
         (m.group("ref") or "") if m.group("ref") != "the" else "",
     ),
     False),
    # -- navigation -----------------------------------------------------------
    (r"go back",
     IntentType.BACK,
     lambda m: ({}, ""),
     True),
    (r"go forward",
     IntentType.FORWARD,
     lambda m: ({}, ""),
     True),
    # -- scroll + magnitude ----------------------------------------------------
    (r"scroll (?P<dir>up|down)(?: (?P<mag>a little|slightly|a lot|way|twice))?",
     IntentType.SCROLL,
     lambda m: ({
         "amount": _SCROLL_MAGNITUDES.get(m.group("mag"), _SCROLL_DEFAULT)
         * (1 if m.group("dir") == "up" else -1),
         # "twice" executes the scroll two times (params contract)
         **({"repeat": 2} if m.group("mag") == "twice" else {}),
     }, ""),
     False),
    # -- zoom ------------------------------------------------------------------
    (r"zoom (?P<dir>in|out)(?: (?P<mag>a lot|way|a little|slightly))?",
     IntentType.ZOOM,
     lambda m: ({
         "direction": m.group("dir"),
         "ticks": 8 if m.group("mag") in ("a lot", "way") else 3,
     }, ""),
     False),
    # -- move ------------------------------------------------------------------
    (r"move(?: (?P<ref>this|that|it))?(?: to the)? ?"
     r"(?P<dir>left|right|top|bottom|up|down)",
     IntentType.MOVE,
     lambda m: ({"direction": m.group("dir")}, m.group("ref") or ""),
     False),
    # -- open / select / media ---------------------------------------------------
    (r"open(?: (?P<ref>this|that|it))?",
     IntentType.OPEN,
     lambda m: ({}, m.group("ref") or ""),
     False),
    (r"select(?: (?P<ref>this|that))?",
     IntentType.SELECT,
     lambda m: ({}, m.group("ref") or ""),
     False),
    (r"play(?: (?P<ref>this|that))?",
     IntentType.PLAY,
     lambda m: ({}, m.group("ref") or ""),
     False),
    (r"pause(?: (?P<ref>this|that|it))?",
     IntentType.PAUSE,
     lambda m: ({}, m.group("ref") or ""),
     False),
    (r"repeat(?: (?P<ref>that|the last action|it))?",
     IntentType.REPEAT,
     lambda m: ({}, "that" if m.group("ref") == "that" else ""),
     False),
    # -- clipboard -----------------------------------------------------------------
    (r"copy(?: (?P<ref>this|that))?",
     IntentType.COPY,
     lambda m: ({}, m.group("ref") or ""),
     False),
    (r"paste(?: (?P<ref>here|there|this))?",
     IntentType.PASTE,
     lambda m: ({}, m.group("ref") or ""),
     False),
    # -- cancel family (AFTER "stop everything") ---------------------------------
    (r"stop|cancel|never ?mind|abort",
     IntentType.CANCEL,
     lambda m: ({}, ""),
     True),
    # -- dictation (last: greedy text capture) -------------------------------------
    (r"type (?P<text>.+)",
     IntentType.TYPE,
     lambda m: ({"text": m.group("text")[:TYPE_TEXT_MAX]}, ""),
     False),
]

#: Compiled pattern table: ``(regex, intent, builder, generic)``.
PATTERNS: List[Tuple[re.Pattern, IntentType,
                     Callable[[Match], Tuple[Dict[str, Any], str]], bool]] = [
    (re.compile(spec[0]), spec[1], spec[2], spec[3]) for spec in PATTERN_SPEC
]

del PATTERN_SPEC  # the compiled PATTERNS table is the single source of truth


# ---------------------------------------------------------------------------
# parse_utterance
# ---------------------------------------------------------------------------


def parse_utterance(text: str, config: Optional[Dict[str, Any]] = None) -> NLUResult:
    """Parse one utterance into an :class:`airmouse.interfaces.NLUResult`.

    Pipeline: normalize → first-matching v9 pattern (specificity order) →
    build params/target_ref → assign confidence (0.9 target / 0.75 bare /
    0.6 generic).  When no v9 pattern matches, the legacy keyword map is
    consulted: a hit fills ``fallback_command`` with ``is_command=False`` /
    ``intent=NONE`` / ``confidence=0.3``.  Total function — never raises.
    """
    cfg = dict(DEFAULT_NL_CONFIG)
    cfg.update(config or {})
    original = "" if text is None else str(text)
    norm = normalize_text(original)
    if not norm:
        return NLUResult(text=original, confidence=0.0, is_command=False)

    for regex, intent, builder, generic in PATTERNS:
        m = regex.fullmatch(norm)
        if m is None:
            continue
        try:
            params, target_ref = builder(m)
        except Exception:                       # a broken builder degrades
            params, target_ref = {}, ""
        params = dict(params or {})
        if intent is IntentType.TYPE:
            # dictation text is captured from the NORMALIZED string so the
            # result stays deterministic (punctuation already stripped)
            params["text"] = str(params.get("text", ""))[: int(cfg["type_text_max"])]
        if generic or not target_ref:
            confidence = 0.6 if generic else 0.75
        else:
            confidence = 0.9
        return NLUResult(
            text=original,
            intent=intent,
            params=params,
            target_ref=target_ref,
            confidence=confidence,
            is_command=True,
            fallback_command="",
        )

    # No v9 pattern — try the legacy v5 keyword space.
    legacy = resolve_fallback(norm)
    if legacy:
        return NLUResult(
            text=original,
            intent=IntentType.NONE,
            params={},
            target_ref="",
            confidence=0.3,
            is_command=False,
            fallback_command=legacy,
        )

    return NLUResult(text=original, confidence=0.0, is_command=False,
                     fallback_command="")


def nlu_to_intent(nlu: NLUResult,
                  decision: Optional[FusionDecision] = None,
                  utterance: str = "",
                  now: Optional[float] = None) -> Optional[Intent]:
    """Convert a v9 :class:`NLUResult` into an :class:`Intent`.

    **Deictic resolution contract**: when ``target_ref`` is a deictic word
    ("that"/"this"/"it"/"here"/"there") the target/point come from the
    supplied fusion :class:`FusionDecision` (which the AGENT populated from
    gaze + screen perception).  The NL layer never invents coordinates:
    with no decision target, a click-class intent simply carries ``None``
    and is dropped downstream by the preconditions check.

    Returns ``None`` for non-commands or ``IntentType.NONE``.
    """
    if nlu is None or not nlu.is_command or nlu.intent is IntentType.NONE:
        return None
    now = float(now if now is not None else now_ts())
    decision = decision or FusionDecision()
    target = None
    point = None
    if decision.has_target:
        target = decision.target
        point = decision.target_point()
    confidence = 1.0 if nlu.intent is IntentType.EMERGENCY_STOP else nlu.confidence
    return Intent(
        type=nlu.intent,
        target=target,
        point=point,
        params=dict(nlu.params or {}),
        confidence=confidence,
        sources=Modality.VOICE,
        utterance=str(utterance or nlu.text or ""),
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# NLController
# ---------------------------------------------------------------------------


class NLController:
    """Parse + dedup wrapper around :func:`parse_utterance`.

    ``feed(text, timestamp=None)`` returns the :class:`NLUResult` for a new
    utterance, or ``None`` when the identical (normalized) consecutive
    utterance repeats within ``dedup_window`` seconds (default 1.2 s).
    The dedup window is measured against the last ACCEPTED utterance, so a
    stuck voice pipeline cannot lock the channel out forever.

    Thread-safe (voice audio arrives on its own thread).
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(DEFAULT_NL_CONFIG)
        cfg.update(config or {})
        self.dedup_window: float = float(cfg["dedup_window"])
        self.type_text_max: int = int(cfg["type_text_max"])
        self._last_text: str = ""
        self._last_ts: float = float("-inf")
        self._last_result: Optional[NLUResult] = None
        self._lock = threading.RLock()

    # -- main entry ------------------------------------------------------------

    def feed(self, text: str, timestamp: Optional[float] = None) -> Optional[NLUResult]:
        """Parse one utterance; ``None`` on empty text or a duplicate."""
        original = "" if text is None else str(text)
        norm = normalize_text(original)
        if not norm:
            return None
        ts = float(timestamp if timestamp is not None else now_ts())
        with self._lock:
            if (norm == self._last_text
                    and (ts - self._last_ts) < self.dedup_window):
                return None  # duplicate within the window — suppressed
            result = parse_utterance(original, {"type_text_max": self.type_text_max})
            self._last_text = norm
            self._last_ts = ts
            self._last_result = result
            return result

    # -- state -------------------------------------------------------------------

    @property
    def last_result(self) -> Optional[NLUResult]:
        """The most recent accepted :class:`NLUResult` (``None`` initially)."""
        with self._lock:
            return self._last_result

    def reset(self) -> None:
        """Clear dedup state and the cached last result."""
        with self._lock:
            self._last_text = ""
            self._last_ts = float("-inf")
            self._last_result = None


# ---------------------------------------------------------------------------
# Smoke demo (headless, deterministic)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    for utt in ("Click that", "scroll down a lot!!!", "type hello world",
                "please zoom in now", "stop everything", "gibberish"):
        res = parse_utterance(utt)
        print(f"{utt!r:26} -> {res.intent.value:16} conf={res.confidence} "
              f"ref={res.target_ref!r} params={res.params} "
              f"fb={res.fallback_command!r}")
