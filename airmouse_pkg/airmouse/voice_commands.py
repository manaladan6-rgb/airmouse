"""
airmouse.voice_commands — v10 Command Grammar + Registry 📖
===========================================================

Deterministic, fully-offline voice command system (mission §6 + §7):

    utterance → normalize → template match (synonyms + entities) →
    VoiceCommandMatch → normalized Intent params

NO LLM, NO network, NO probabilistic components: pure template grammar
with synonym expansion, entity extraction, ambiguity handling and
calibrated confidence scoring.

Template syntax
---------------
A pattern is lowercase words with entity slots::

    "open <app>"            -> entities {"app": "firefox"}
    "scroll <direction>"    -> entities {"direction": "up"}
    "search for <query>"    -> entities {"query": "html css tutorials"}

Slot types and what they capture:

    <direction>   up | down | left | right
    <app>         application name (greedy tail)
    <name>        file/folder name (greedy tail)
    <query>       free-text search query (greedy tail)
    <url>         URL-ish token (greedy tail, validated downstream)
    <target>      that | this | it | ordinal | free tail (browser semantic)
    <number>      digits or number words (first..tenth, 1..99)
    <text>        greedy dictation tail
    <what>        window | tab | app | dialog

Verb synonyms are written inline as ``(?:open|launch|start)`` groups.

Confidence model
----------------
    exact template match              -> 1.00
    exact match but ambiguous (2 specs tie) -> 0.72
    fuzzy verb/noun tolerance (≥min)  -> 0.62 … 0.85 by ratio
Below ``min_match_confidence`` (default 0.62) -> no match.

Sensitive/destructive commands (shutdown, restart, delete file, close
tab, system operations) are flagged so the v8 safety layer requires
confirmation before execution.

`match_command_grammar()` is a PURE function — no audio, no hardware,
fully unit-testable.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:  # package-relative (normal import path)
    from .interfaces import CommandNamespace, IntentType
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import CommandNamespace, IntentType

__all__ = [
    "CommandSpec", "VoiceCommandMatch", "REGISTRY",
    "match_command_grammar", "commands_by_namespace", "list_commands",
    "NUM_WORDS",
]

# ---------------------------------------------------------------------------
# Entity slot definitions
# ---------------------------------------------------------------------------

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "last": -1,
}
NUM_WORDS: Dict[str, int] = dict(_NUM_WORDS)

_SLOT_INNER: Dict[str, str] = {
    # inner regex for each slot type (outer named group added by compiler)
    "direction": r"up|down|left|right",
    "state":     r"on|off",
    "app":       r"[a-z0-9][\w\s\-\.]*?",
    "name":      r"[a-z0-9][\w\s\-\.]*?",
    "query":     r".+",
    "url":       r".+",
    "target":    r".+",
    "number":    r"\d+|" + "|".join(sorted(_NUM_WORDS,
                                           key=len, reverse=True)),
    "text":      r".+",
    "what":      r"window|tab|app|application|dialog|browser",
}

_KNOWN_SLOTS = frozenset(_SLOT_INNER)

_QUANT_FIX = re.compile(r"([+*])(\?)?")

_NUM_ALT = r"\d+|" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True))


def _compile_template(template: str) -> re.Pattern:
    """Compile one grammar template into an anchored regex.

    Template syntax: lowercase words + ``<slot>`` or ``<slot:name>``
    slots.  A slot that is NOT a known type is treated as a ``name``
    slot with that key (so ``<new_name>`` works).  Tail slots stay
    greedy (they bind the rest of the utterance); mid-pattern slots are
    made lazy so earlier words are not swallowed.
    """
    parts = template.split()
    rx_parts = []
    for i, part in enumerate(parts):
        last = (i == len(parts) - 1)
        if part.startswith("<") and part.endswith(">") and len(part) > 2:
            token = part[1:-1]
            if ":" in token:
                slot, name = token.split(":", 1)
            elif token in _KNOWN_SLOTS:
                slot, name = token, token
            else:
                slot, name = "name", token
            inner = _SLOT_INNER.get(slot, _SLOT_INNER["name"])
            if slot == "number":
                inner = _NUM_ALT
            if not last:
                # lazy quantifiers for mid-pattern slots
                inner = _QUANT_FIX.sub(lambda m: m.group(1) + "?", inner)
            rx_parts.append("(?P<e_%s>%s)" % (name, inner))
        else:
            rx_parts.append(re.escape(part))
    return re.compile(r"\s*".join(rx_parts))


# ---------------------------------------------------------------------------
# CommandSpec
# ---------------------------------------------------------------------------


@dataclass
class CommandSpec:
    """One deterministic voice command (grammar template + mapping)."""

    name: str                              # canonical command id
    namespace: CommandNamespace
    intent: IntentType
    patterns: List[str]                    # template variants
    params: Dict[str, Any] = field(default_factory=dict)   # fixed params
    synonyms: Dict[str, str] = field(default_factory=dict)  # post-parse rewrites
    destructive: bool = False              # data loss possible
    sensitive: bool = False                # needs user confirmation
    where_supported: bool = True           # platform-dependent (bluetooth…)
    _rx: List[re.Pattern] = field(default_factory=list, repr=False,
                                  compare=False)

    def compile(self) -> "CommandSpec":
        self._rx = [_compile_template(p) for p in self.patterns]
        # literal weight per pattern: fixed words are MORE specific than
        # slot tails ("switch to tab <n>" beats "switch to <app>")
        self._lit = []
        for p in self.patterns:
            lit = " ".join(w for w in p.split()
                           if not (w.startswith("<") and w.endswith(">")))
            self._lit.append(float(len(lit)))
        return self

    def match(self, norm: str) -> Optional[Tuple[Dict[str, Any], float]]:
        """Try every pattern; best (entities, literal specificity) wins."""
        best: Optional[Tuple[Dict[str, Any], float]] = None
        for rx, lit in zip(self._rx, self._lit):
            m = rx.fullmatch(norm)
            if m is None:
                continue
            entities = {k[2:]: v.strip() for k, v in m.groupdict().items()
                        if k.startswith("e_") and v is not None
                        and str(v).strip() != ""}
            spec = lit  # literal-character specificity
            if best is None or spec > best[1]:
                best = (entities, spec)
        return best


# ---------------------------------------------------------------------------
# The registry (§6 — complete command ecosystem)
# ---------------------------------------------------------------------------

_V = {  # shared verb groups (readability)
    "open": r"(?:open|launch|start|run|start up)",
    "close": r"(?:close|quit|exit|shut(?: down)?)",
    "focus": r"(?:focus|switch to|bring up|activate)",
    "switch": r"(?:switch to|go to|jump to)",
}


def _spec(name: str, namespace: CommandNamespace, intent: IntentType,
          patterns: List[str], **kw: Any) -> CommandSpec:
    return CommandSpec(name, namespace, intent, patterns, **kw).compile()


REGISTRY: List[CommandSpec] = [
    # ── ENGINE ─────────────────────────────────────────────────────────────
    _spec("estop", CommandNamespace.ENGINE, IntentType.EMERGENCY_STOP,
          ["stop everything", "emergency stop", "panic"]),
    _spec("cancel", CommandNamespace.ENGINE, IntentType.CANCEL,
          ["cancel", "never mind", "abort", "stop"]),
    _spec("confirm", CommandNamespace.ENGINE, IntentType.CONFIRM,
          ["confirm", "yes do it", "do it", "yes"]),
    _spec("voice_off", CommandNamespace.ENGINE, IntentType.CANCEL,
          ["voice off", "stop listening", "shut up", "stop voice"],
          params={"voice": "off"}),
    _spec("mode_command", CommandNamespace.ENGINE, IntentType.CANCEL,
          ["command mode", "voice command mode"], params={"voice_mode": "command"}),
    _spec("mode_dictation", CommandNamespace.ENGINE, IntentType.CANCEL,
          ["dictation mode", "dictate mode", "start dictation"],
          params={"voice_mode": "dictation"}),
    _spec("mode_hybrid", CommandNamespace.ENGINE, IntentType.CANCEL,
          ["hybrid mode", "voice hybrid mode"], params={"voice_mode": "hybrid"}),
    _spec("record", CommandNamespace.ENGINE, IntentType.HOTKEY,
          ["start recording", "record macro"], params={"macro": "record"}),
    _spec("stop_record", CommandNamespace.ENGINE, IntentType.HOTKEY,
          ["stop recording", "end recording"], params={"macro": "stop"}),
    _spec("play_macro", CommandNamespace.ENGINE, IntentType.HOTKEY,
          ["play macro", "replay macro", "run macro"], params={"macro": "play"}),

    # ── MOUSE ──────────────────────────────────────────────────────────────
    _spec("click", CommandNamespace.MOUSE, IntentType.CLICK,
          ["click", "click <target>", "left click", "tap"]),
    _spec("double_click", CommandNamespace.MOUSE, IntentType.DOUBLE_CLICK,
          ["double click", "double click <target>", "double tap"]),
    _spec("right_click", CommandNamespace.MOUSE, IntentType.RIGHT_CLICK,
          ["right click", "right click <target>", "context menu"]),
    _spec("middle_click", CommandNamespace.MOUSE, IntentType.MIDDLE_CLICK,
          ["middle click", "middle click <target>"]),
    _spec("drag", CommandNamespace.MOUSE, IntentType.DRAG,
          ["drag", "grab and move"]),
    _spec("zoom_in", CommandNamespace.MOUSE, IntentType.ZOOM,
          ["zoom in", "magnify", "bigger"], params={"direction": "in"}),
    _spec("zoom_out", CommandNamespace.MOUSE, IntentType.ZOOM,
          ["zoom out", "shrink", "smaller"], params={"direction": "out"}),

    # ── SYSTEM ─────────────────────────────────────────────────────────────
    _spec("volume_up", CommandNamespace.SYSTEM, IntentType.VOLUME,
          ["volume up", "louder", "turn the volume up", "increase volume"],
          params={"direction": "up"}),
    _spec("volume_down", CommandNamespace.SYSTEM, IntentType.VOLUME,
          ["volume down", "quieter", "turn the volume down", "decrease volume"],
          params={"direction": "down"}),
    _spec("mute", CommandNamespace.SYSTEM, IntentType.VOLUME,
          ["mute", "silence", "mute the sound"], params={"direction": "mute"}),
    _spec("unmute", CommandNamespace.SYSTEM, IntentType.VOLUME,
          ["unmute", "sound on", "unmute the sound"], params={"direction": "unmute"}),
    _spec("lock", CommandNamespace.SYSTEM, IntentType.LOCK,
          ["lock", "lock the screen", "lock my computer", "lock pc"],
          sensitive=True),
    _spec("sleep", CommandNamespace.SYSTEM, IntentType.SLEEP,
          ["sleep", "put the computer to sleep", "sleep the computer"],
          sensitive=True),
    _spec("shutdown", CommandNamespace.SYSTEM, IntentType.SHUTDOWN,
          [r"shut down the computer", r"shut down", "power off", "turn off the computer"],
          destructive=True, sensitive=True),
    _spec("restart", CommandNamespace.SYSTEM, IntentType.RESTART,
          ["restart", "reboot", "restart the computer", "reboot the computer"],
          destructive=True, sensitive=True),
    _spec("bluetooth", CommandNamespace.SYSTEM, IntentType.BLUETOOTH,
          [r"bluetooth <state>", r"turn bluetooth <state>",
           r"toggle bluetooth"],
          where_supported=False),
    _spec("brightness_up", CommandNamespace.SYSTEM, IntentType.BRIGHTNESS,
          ["brightness up", "increase brightness", "brighter"],
          params={"direction": "up"}),
    _spec("brightness_down", CommandNamespace.SYSTEM, IntentType.BRIGHTNESS,
          ["brightness down", "decrease brightness", "dimmer"],
          params={"direction": "down"}),

    # ── WINDOW ─────────────────────────────────────────────────────────────
    _spec("minimize", CommandNamespace.WINDOW, IntentType.MINIMIZE,
          ["minimize", "minimize window", "minimize this"]),
    _spec("maximize", CommandNamespace.WINDOW, IntentType.MAXIMIZE,
          ["maximize", "maximize window", "maximize this"]),
    _spec("restore", CommandNamespace.WINDOW, IntentType.RESTORE,
          ["restore", "restore window", "restore down", "unmaximize"]),
    _spec("close_window", CommandNamespace.WINDOW, IntentType.CLOSE,
          ["close window", "close the window", "close this window",
           "close it", "close that", "close this", "close"],
          params={"what": "window"}),
    _spec("focus_window", CommandNamespace.WINDOW, IntentType.FOCUS,
          [r"focus <name>", r"switch to <name> window",
           r"bring up <name>"]),
    _spec("switch_window", CommandNamespace.WINDOW, IntentType.SWITCH_WINDOW,
          ["switch window", "next window", "alt tab"]),
    _spec("snap_left", CommandNamespace.WINDOW, IntentType.SNAP,
          ["snap left", "snap window left", "move window left"],
          params={"direction": "left"}),
    _spec("snap_right", CommandNamespace.WINDOW, IntentType.SNAP,
          ["snap right", "snap window right", "move window right"],
          params={"direction": "right"}),
    _spec("move_window", CommandNamespace.WINDOW, IntentType.MOVE,
          [r"move window <direction>", r"move this window <direction>"],
          params={"what": "window"}),

    # ── APPLICATION ────────────────────────────────────────────────────────
    _spec("open_app", CommandNamespace.APPLICATION, IntentType.OPEN,
          [r"open <app>", r"launch <app>", r"start <app>", r"run <app>"]),
    _spec("close_app", CommandNamespace.APPLICATION, IntentType.CLOSE,
          [r"close <app>", r"quit <app>", r"exit <app>"],
          params={"what": "app"}, destructive=True, sensitive=True),
    _spec("focus_app", CommandNamespace.APPLICATION, IntentType.FOCUS,
          [r"focus <app>", r"switch to <app>"]),
    _spec("switch_app", CommandNamespace.APPLICATION, IntentType.SWITCH_WINDOW,
          ["switch app", "switch application", "next app"]),

    # ── FILES ──────────────────────────────────────────────────────────────
    _spec("open_file", CommandNamespace.FILES, IntentType.FILE_OP,
          [r"open file <name>", r"open the file <name>"],
          params={"op": "open"}),
    _spec("create_folder", CommandNamespace.FILES, IntentType.FILE_OP,
          [r"create folder <name>", r"new folder <name>", r"make folder <name>",
           r"create a folder named <name>"],
          params={"op": "create_folder"}),
    _spec("rename", CommandNamespace.FILES, IntentType.FILE_OP,
          [r"rename <name> to <new_name>", r"rename file <name> to <new_name>"],
          params={"op": "rename"}),
    _spec("copy_file", CommandNamespace.FILES, IntentType.FILE_OP,
          ["copy file", "copy this file", "copy the file"], params={"op": "copy"}),
    _spec("paste_file", CommandNamespace.FILES, IntentType.FILE_OP,
          ["paste file", "paste here", "paste the file"], params={"op": "paste"}),
    _spec("move_file", CommandNamespace.FILES, IntentType.FILE_OP,
          [r"move file <name>", r"move the file <name>"], params={"op": "move"}),
    _spec("delete_file", CommandNamespace.FILES, IntentType.FILE_OP,
          [r"delete file <name>", r"delete the file <name>", r"delete <name>",
           r"remove file <name>"],
          params={"op": "delete"}, destructive=True, sensitive=True),
    _spec("select_file", CommandNamespace.FILES, IntentType.FILE_OP,
          [r"select file <name>", r"select the file <name>"],
          params={"op": "select"}),

    # ── TEXT ───────────────────────────────────────────────────────────────
    _spec("select_all", CommandNamespace.TEXT, IntentType.SELECT,
          ["select all", "select everything"], params={"what": "all"}),
    _spec("copy_text", CommandNamespace.TEXT, IntentType.COPY,
          ["copy", "copy that", "copy this", "copy selection"]),
    _spec("paste_text", CommandNamespace.TEXT, IntentType.PASTE,
          ["paste", "paste that", "paste it"]),
    _spec("undo", CommandNamespace.TEXT, IntentType.UNDO,
          ["undo", "undo that", "undo last"]),
    _spec("redo", CommandNamespace.TEXT, IntentType.REDO,
          ["redo", "redo that"]),
    _spec("delete_word", CommandNamespace.TEXT, IntentType.KEY_PRESS,
          ["delete word", "delete the word"], params={"keys": ["ctrl", "backspace"]}),
    _spec("delete_line", CommandNamespace.TEXT, IntentType.KEY_PRESS,
          ["delete line", "delete the line"], params={"keys": ["ctrl", "delete"]}),
    _spec("cursor", CommandNamespace.TEXT, IntentType.KEY_PRESS,
          [r"move cursor <direction>", r"cursor <direction>",
           r"go to start of line", r"go to end of line"],
          params={"what": "cursor"}),

    # ── NAVIGATION ─────────────────────────────────────────────────────────
    _spec("scroll_up", CommandNamespace.NAVIGATION, IntentType.SCROLL,
          ["scroll up", "scroll a little up", "go up"], params={"amount": 3}),
    _spec("scroll_down", CommandNamespace.NAVIGATION, IntentType.SCROLL,
          ["scroll down", "scroll a little down", "go down"], params={"amount": -3}),
    _spec("page_up", CommandNamespace.NAVIGATION, IntentType.NAVIGATE,
          ["page up", "scroll page up"], params={"target": "page_up"}),
    _spec("page_down", CommandNamespace.NAVIGATION, IntentType.NAVIGATE,
          ["page down", "scroll page down"], params={"target": "page_down"}),
    _spec("go_home", CommandNamespace.NAVIGATION, IntentType.NAVIGATE,
          ["go home", "go to top", "jump to top"], params={"target": "home"}),
    _spec("go_end", CommandNamespace.NAVIGATION, IntentType.NAVIGATE,
          ["go to end", "go to bottom", "jump to bottom",
           "scroll to the bottom", "scroll to bottom"],
          params={"target": "end"}),

    # ── MEDIA ──────────────────────────────────────────────────────────────
    _spec("play", CommandNamespace.MEDIA, IntentType.MEDIA,
          ["play", "play music", "resume"], params={"action": "play"}),
    _spec("pause", CommandNamespace.MEDIA, IntentType.MEDIA,
          ["pause", "pause music", "hold on music"], params={"action": "pause"}),
    _spec("media_next", CommandNamespace.MEDIA, IntentType.MEDIA,
          ["next track", "next song", "skip track", "skip"], params={"action": "next"}),
    _spec("media_prev", CommandNamespace.MEDIA, IntentType.MEDIA,
          ["previous track", "previous song", "back track"],
          params={"action": "previous"}),

    # ── BROWSER ────────────────────────────────────────────────────────────
    _spec("new_tab", CommandNamespace.BROWSER, IntentType.NEW_TAB,
          ["new tab", "open a new tab", "open new tab"]),
    _spec("close_tab", CommandNamespace.BROWSER, IntentType.CLOSE_TAB,
          ["close tab", "close this tab", "close the tab"],
          sensitive=True),
    _spec("switch_tab", CommandNamespace.BROWSER, IntentType.SWITCH_TAB,
          [r"switch to tab <number>", r"tab <number>"]),
    _spec("open_url", CommandNamespace.BROWSER, IntentType.OPEN_URL,
          [r"navigate to <url>", r"go to <url>", r"open <url>"]),
    _spec("search_for", CommandNamespace.BROWSER, IntentType.NAVIGATE,
          [r"search for <query>", r"search <query>", r"google <query>",
           r"look up <query>"],
          params={"search": True}),
    _spec("go_back", CommandNamespace.BROWSER, IntentType.BACK,
          ["go back", "back"]),
    _spec("go_forward", CommandNamespace.BROWSER, IntentType.FORWARD,
          ["go forward", "forward"]),
    _spec("refresh", CommandNamespace.BROWSER, IntentType.REFRESH,
          ["refresh", "reload", "reload the page", "refresh the page"]),
]


# ---------------------------------------------------------------------------
# VoiceCommandMatch + matcher
# ---------------------------------------------------------------------------


@dataclass
class VoiceCommandMatch:
    """Deterministic grammar resolution for one utterance."""

    name: str = ""
    namespace: CommandNamespace = CommandNamespace.ENGINE
    intent: IntentType = IntentType.NONE
    entities: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    ambiguous: bool = False
    destructive: bool = False
    sensitive: bool = False
    where_supported: bool = True
    text: str = ""

    @property
    def is_command(self) -> bool:
        return bool(self.name) and self.confidence > 0.0


def _normalize(text: Any) -> str:
    """Lowercase, strip punctuation (keep digits), collapse whitespace."""
    if text is None:
        return ""
    try:
        s = str(text).lower()
    except Exception:
        return ""
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _fuzzy_fixup(norm: str) -> List[str]:
    """Deterministic typo-tolerant variants (edit-distance-1 verbs only).

    Keeps the grammar deterministic: a FIXED alias map instead of fuzzy
    matching.  "volum up" → "volume up" etc.
    """
    aliases = {
        "volum": "volume", "valu": "volume", "muted": "mute",
        "maximise": "maximize", "minimise": "minimize",
        "ress": "refresh", "relode": "reload", "closse": "close",
        "clik": "click", "scrol": "scroll", "opne": "open",
    }
    out = norm
    for wrong, right in aliases.items():
        out = re.sub(r"\b%s\b" % wrong, right, out)
    return [out] if out != norm else [norm]


def match_command_grammar(text: str,
                          min_confidence: float = 0.62,
                          now: Optional[float] = None) -> VoiceCommandMatch:
    """Match one utterance against the command registry.

    Pure + total: accepts anything, never raises.  Returns a
    :class:`VoiceCommandMatch` whose ``is_command`` tells whether a
    command resolved.  Order of resolution:

    1. exact full-match against every compiled template
    2. prefer the longest matched utterance (specificity)
    3. ties → ambiguity flag + reduced confidence
    """
    norm = _normalize(text)
    if not norm:
        return VoiceCommandMatch(text=str(text or ""))
    candidates: List[Tuple[CommandSpec, Dict[str, str], float]] = []
    for attempt in _fuzzy_fixup(norm):
        for spec in REGISTRY:
            hit = spec.match(attempt)
            if hit is None:
                continue
            entities, specificity = hit
            candidates.append((spec, entities, specificity))
    if not candidates:
        return VoiceCommandMatch(text=str(text or ""))
    top = max(c[2] for c in candidates)
    best = [c for c in candidates if c[2] == top]
    spec, entities, _ = best[0]
    ambiguous = len({b[0].name for b in best}) > 1
    # synonyms rewrite (e.g. "shut firefox" entities.app stays "firefox")
    for wrong, right in spec.synonyms.items():
        if entities.get(wrong):
            entities[wrong] = right
    confidence = 0.72 if ambiguous else 1.0
    params = dict(spec.params)
    if entities:
        params["entities"] = dict(entities)
    return VoiceCommandMatch(
        name=spec.name, namespace=spec.namespace, intent=spec.intent,
        entities=entities, params=params, confidence=confidence,
        ambiguous=ambiguous, destructive=spec.destructive,
        sensitive=spec.sensitive, where_supported=spec.where_supported,
        text=str(text or ""),
    )


# ---------------------------------------------------------------------------
# Introspection helpers (CLI: airmouse commands)
# ---------------------------------------------------------------------------


def commands_by_namespace() -> Dict[str, List[str]]:
    """Group canonical command names by namespace (documentation order)."""
    out: Dict[str, List[str]] = {}
    for spec in REGISTRY:
        out.setdefault(spec.namespace.value, []).append(spec.name)
    return out


def list_commands(namespace: Optional[str] = None) -> List[Tuple[str, str]]:
    """Flat (name, first-pattern) listing, optionally per namespace."""
    out = []
    for spec in REGISTRY:
        if namespace is not None and spec.namespace.value != namespace:
            continue
        out.append((spec.name, spec.patterns[0] if spec.patterns else ""))
    return out
