"""Voice Academy (v16.5, mission §4) — 4 levels, every PASS matcher-verified.

The gesture side of v16 got a teaching experience (``airmouse.academy``);
the Voice Academy gives voice the same engineering attention:

* **Level 1 — Basic commands**: the deterministic grammar's core phrases
  (click, double click, right click, scroll, browser tabs, clipboard,
  undo/redo).  Practice is graded by the REAL command matcher
  (:mod:`airmouse.voice_commands`, with the v9 NL layer as fallback) —
  a phrase passes only when the matcher actually resolves it to the
  expected intent.  No self-reported success, ever.
* **Level 2 — Natural language**: you do NOT have to memorize exact
  syntax — loose phrasing ("click that", "open my browser") resolves
  too.  Honest by construction: the lesson also shows where the grammar
  ends ("move over there" is NOT in it) and teaches the real forms.
* **Level 3 — Dictation**: spoken punctuation through the REAL
  formatters (:func:`apply_spoken_punctuation` + :func:`capitalize_text`
  via :class:`airmouse.dictation_text.VoiceTypingEngine`) and the
  correction commands the engine actually supports (scratch that /
  delete that, undo, replace that with …).
* **Level 4 — Personal voice learning**: the REAL
  :class:`airmouse.intelligence.personalization.VoiceProfile` learns
  frequent commands and personal aliases (5 consistent observations).
  Everything is learned LOCALLY; nothing is uploaded.  Memory lifecycle:
  ``airmouse memory reset``.

HONESTY IS LOAD-BEARING: microphone practice needs hardware and is
labelled **PHYSICAL TEST REQUIRED**; the text practice below is real
(matcher-verified) — the two are never conflated.  Where a suggested
phrase is outside the deterministic grammar the lesson says so instead
of pretending.

``run_voice_academy(level="all", out=None, input_fn=None)`` — without an
``input_fn`` (or non-interactive) it prints the full curriculum in
concept mode and marks nothing complete.  With an ``input_fn`` it runs
the practice levels.  ``VOICE_LESSONS`` is the exportable curriculum
data (consumed by the teacher module).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .dictation_text import VoiceTypingEngine
from .interfaces import IntentType
from .intelligence.personalization import VoiceProfile
from . import nl_control as _nl
from . import voice_commands as _vc
from .intent import match_phrase
from .transcription import apply_spoken_punctuation, capitalize_text

__all__ = [
    "VOICE_LESSONS", "VOICE_LEVEL_IDS", "ResolvedVoice", "resolve_voice",
    "format_dictation", "run_voice_academy",
]

PHYSICAL_NOTE = ("Microphone practice needs hardware (PHYSICAL TEST REQUIRED); "
                 "text practice below is matcher-verified.")

MICRO_ONLY_NOTE = ("Full speech recognition needs a local ASR engine "
                   "(see: airmouse voice-status); command practice works "
                   "without one.")

#: alias-learning rule of the real VoiceProfile (5 consistent votes)
ALIAS_VOTES_NEEDED = 5

DEMO_ALIAS = "launch browser"
DEMO_CANONICAL = "open browser"
PRACTICE_CANONICAL = "close tab"
PRACTICE_ALIAS_EXAMPLE = "kill the tab"

MAX_ATTEMPTS = 3          # first try + 2 retries, then the answer is revealed

VOICE_LEVEL_IDS: Tuple[str, ...] = ("l1_basic", "l2_natural",
                                    "l3_dictation", "l4_personal")

_LEVEL_ALIASES: Dict[str, str] = {
    "1": "l1_basic", "2": "l2_natural", "3": "l3_dictation", "4": "l4_personal",
    "l1": "l1_basic", "l2": "l2_natural", "l3": "l3_dictation",
    "l4": "l4_personal",
}


# ---------------------------------------------------------------------------
# curriculum data (exportable for the teacher module)
# ---------------------------------------------------------------------------

VOICE_LESSONS: List[Dict[str, Any]] = [
    {
        "id": "l1_basic",
        "title": "Level 1 — Basic commands",
        "instruction": (
            "Voice control starts from a small set of EXACT commands the "
            "deterministic grammar knows cold.  Type each command as you "
            "would say it; the real matcher must resolve it to the expected "
            "intent for the practice to pass."),
        "practice": ["click", "double click", "right click", "scroll down",
                     "scroll up", "open browser", "new tab", "close tab",
                     "copy", "paste", "undo", "redo"],
        "verified_by": "deterministic command grammar (airmouse.voice_commands)",
    },
    {
        "id": "l2_natural",
        "title": "Level 2 — Natural language",
        "instruction": (
            "You do not have to memorize exact syntax: natural phrasing like "
            "\"click that\" or \"open my browser\" resolves too.  Honesty "
            "note: the grammar has limits — \"move over there\" is NOT in it "
            "yet; the real movement forms are \"move left\", \"move right\", "
            "\"move window left\", \"move cursor up\"."),
        "practice": ["click that", "scroll down", "open my browser",
                     "close this", "copy that", "move left"],
        "verified_by": "command grammar + v9 NL pattern table + v8 phrase table",
    },
    {
        "id": "l3_dictation",
        "title": "Level 3 — Dictation",
        "instruction": (
            "Dictation turns speech into formatted text: SAY the punctuation "
            "(\"comma\", \"question mark\", \"new paragraph\") and the "
            "deterministic formatters apply it.  Correction commands the "
            "engine really supports: \"scratch that\" / \"delete that\", "
            "\"delete last word\", \"undo\" / \"redo\", "
            "\"replace that with …\", \"capitalize that\"."),
        "practice": ["hello bro comma how are you question mark",
                     "scratch that", "undo",
                     "replace that with the report is final"],
        "verified_by": "VoiceTypingEngine state (apply_spoken_punctuation + "
                       "capitalize_text)",
    },
    {
        "id": "l4_personal",
        "title": "Level 4 — Personal voice learning",
        "instruction": (
            "AirMouse adapts to how YOU talk: frequent commands and personal "
            "aliases (an alias is accepted after 5 consistent observations). "
            "Everything is learned locally on this machine — nothing is "
            "uploaded.  Reset any time with: airmouse memory reset"),
        "practice": [f"<your own wording for \"{PRACTICE_CANONICAL}\">"],
        "verified_by": "VoiceProfile.resolve_alias (real personalization code)",
    },
]


# ---------------------------------------------------------------------------
# the real resolution chain (grammar → v9 NL → v8 phrase table)
# ---------------------------------------------------------------------------

@dataclass
class ResolvedVoice:
    """What the real voice matchers made of one utterance."""

    ok: bool = False
    source: str = ""                    # grammar | nl | phrase | ""
    command: str = ""                   # grammar command id / intent value
    intent: Optional[IntentType] = None
    confidence: float = 0.0
    entities: Dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    def describe(self) -> str:
        """One honest line about the resolution (for lesson feedback)."""
        if not self.ok:
            return "not in the grammar (nothing resolved)"
        src = {"grammar": "command grammar", "nl": "NL pattern table",
               "phrase": "phrase table"}.get(self.source, self.source)
        ents = f", entities={self.entities}" if self.entities else ""
        return (f"resolved via {src} → intent {self.intent.value} "
                f"(confidence {self.confidence:.2f}){ents}")


def resolve_voice(text: Any) -> ResolvedVoice:
    """Run one utterance through the REAL matchers (pure, never raises).

    Chain mirrors the shipped pipeline: the deterministic command grammar
    (:func:`airmouse.voice_commands.match_command_grammar`), then the v9
    NL pattern table (:func:`airmouse.nl_control.parse_utterance`), then
    the v8 phrase table (:func:`airmouse.intent.match_phrase`).  The
    result is matcher truth — not a simulation of it.
    """
    try:
        norm = _nl.normalize_text(text)
    except Exception:
        return ResolvedVoice(ok=False, detail="normalization failed")
    if not norm:
        return ResolvedVoice(ok=False, detail="empty utterance")
    try:
        m = _vc.match_command_grammar(norm)
        if m.is_command:
            return ResolvedVoice(ok=True, source="grammar", command=m.name,
                                 intent=m.intent, confidence=m.confidence,
                                 entities=dict(m.entities or {}),
                                 detail=m.name)
    except Exception:
        pass
    try:
        nlu = _nl.parse_utterance(norm)
        if nlu.is_command and nlu.intent is not IntentType.NONE:
            return ResolvedVoice(ok=True, source="nl",
                                 command=nlu.intent.value, intent=nlu.intent,
                                 confidence=nlu.confidence,
                                 entities=dict(nlu.params or {}),
                                 detail=nlu.intent.value)
    except Exception:
        pass
    try:
        hit = match_phrase(norm)
        if hit is not None:
            itype, params = hit
            return ResolvedVoice(ok=True, source="phrase",
                                 command=itype.value, intent=itype,
                                 confidence=0.8, entities=dict(params or {}),
                                 detail=itype.value)
    except Exception:
        pass
    return ResolvedVoice(ok=False, detail="no matcher resolved this")


# ---------------------------------------------------------------------------
# practice items (phrase, expected intent, honest note)
# ---------------------------------------------------------------------------

L1_ITEMS: List[Tuple[str, IntentType, str]] = [
    ("click", IntentType.CLICK, ""),
    ("double click", IntentType.DOUBLE_CLICK, ""),
    ("right click", IntentType.RIGHT_CLICK, ""),
    ("scroll down", IntentType.SCROLL, ""),
    ("scroll up", IntentType.SCROLL, ""),
    ("open browser", IntentType.OPEN, ""),
    ("new tab", IntentType.NEW_TAB, ""),
    ("close tab", IntentType.CLOSE_TAB, ""),
    ("copy", IntentType.COPY, ""),
    ("paste", IntentType.PASTE, ""),
    ("undo", IntentType.UNDO, ""),
    ("redo", IntentType.REDO, ""),
]

_MOVE_NOTE = ("\"move over there\" is NOT in the deterministic grammar — "
              "real forms: move left | move right | move window left | "
              "move cursor up.  Type one of those (or any phrase the "
              "matcher resolves to MOVE).")

L2_ITEMS: List[Tuple[str, IntentType, str]] = [
    ("click that", IntentType.CLICK, ""),
    ("scroll down", IntentType.SCROLL, ""),
    ("open my browser", IntentType.OPEN, ""),
    ("close this", IntentType.CLOSE, ""),
    ("copy that", IntentType.COPY, ""),
    ("move over there", IntentType.MOVE, _MOVE_NOTE),
]


# ---------------------------------------------------------------------------
# small render/ask helpers (never raise)
# ---------------------------------------------------------------------------

def _emit(out: Any, text: str = "") -> None:
    w = out if out is not None else sys.stdout
    try:
        w.write(text + "\n")
        try:
            w.flush()
        except Exception:
            pass
    except Exception:
        pass


def _ask(input_fn: Callable[[str], str], prompt: str) -> Optional[str]:
    """One prompt; ``None`` on EOF/interrupt (graceful abandon)."""
    try:
        raw = input_fn(prompt)
    except (EOFError, KeyboardInterrupt):
        return None
    except Exception:
        return None
    return "" if raw is None else str(raw)


def _fmt_intent(it: Optional[IntentType]) -> str:
    return it.value if it is not None else "?"


# ---------------------------------------------------------------------------
# level runners
# ---------------------------------------------------------------------------

def _run_command_items(items: List[Tuple[str, IntentType, str]],
                       out: Any, input_fn: Callable[[str], str],
                       profile: Optional[VoiceProfile],
                       label: str, verb: str
                       ) -> Tuple[int, int, bool]:
    """Levels 1-2: type-a-phrase practice graded by the real matcher.

    Returns (passed, total, aborted).  PASS requires the matcher to
    resolve the typed phrase to the expected intent.
    """
    passed = 0
    total = len(items)
    for i, (phrase, expected, note) in enumerate(items, 1):
        head = f'[{label} {i}/{total}] {verb} "{phrase}"'
        if note:
            _emit(out, f"  ⚠ note: {note}")
        ok = False
        for attempt in range(MAX_ATTEMPTS):
            prompt = head if attempt == 0 else f"[{label} {i}/{total}] try again — "
            raw = _ask(input_fn, prompt + " > ")
            if raw is None:
                _emit(out, "  (practice abandoned)")
                return passed, total, True
            typed = str(raw).strip()
            r = resolve_voice(typed)
            if r.ok and r.intent == expected:
                passed += 1
                ok = True
                _emit(out, f'  ✓ "{typed}" — {r.describe()}')
                if profile is not None:
                    profile.observe_command(_nl.normalize_text(typed),
                                            canonical=phrase)
                break
            left = MAX_ATTEMPTS - attempt - 1
            if left > 0:
                _emit(out, f'  ✗ "{typed}" — {r.describe()} '
                           f"(expected intent: {_fmt_intent(expected)}; "
                           f"{left} attempt(s) left)")
        if not ok:
            reveal = f'  The answer: "{phrase}"'
            if not resolve_voice(phrase).ok:
                reveal += ("  (the suggested phrase itself does not resolve "
                           "in the current grammar — any phrase with the "
                           "same intent passes)")
            _emit(out, reveal)
    return passed, total, False


def _run_level3(out: Any, input_fn: Callable[[str], str],
                profile: Optional[VoiceProfile]) -> Tuple[int, int, bool]:
    """Level 3: one continuous VoiceTypingEngine session.

    Every step verifies the REAL engine state (formatted text / edit op)
    — the spoken-punctuation chain used is exactly the shipped
    apply_spoken_punctuation + capitalize_text via VoiceTypingEngine.
    """
    engine = VoiceTypingEngine()
    expected_text = "Hello bro, how are you?"

    def _chk_dictate(ops, text) -> bool:
        return text == expected_text

    def _chk_scratch(ops, text) -> bool:
        return text == ""

    def _chk_undo(ops, text) -> bool:
        return text == expected_text

    def _chk_replace(ops, text) -> bool:
        return any(getattr(o, "op", "") == "replace" for o in ops) \
            and "the report is final" in text

    steps: List[Dict[str, Any]] = [
        {"kind": "dictate",
         "head": '[l3_dictation 1/4] dictate with spoken punctuation — '
                 'type: hello bro comma how are you question mark',
         "verify": _chk_dictate,
         "reveal": "hello bro comma how are you question mark",
         "observe": "hello bro comma how are you question mark"},
        {"kind": "edit",
         "head": '[l3_dictation 2/4] remove the last segment by voice — '
                 'type: scratch that',
         "verify": _chk_scratch,
         "reveal": "scratch that",
         "observe": "scratch that"},
        {"kind": "edit",
         "head": '[l3_dictation 3/4] bring the text back — type: undo',
         "verify": _chk_undo,
         "reveal": "undo",
         "observe": "undo"},
        {"kind": "edit",
         "seed": "the report is ready",
         "head": '[l3_dictation 4/4] seed dictated: "the report is ready". '
                 'Replace the last segment — type: '
                 'replace that with the report is final',
         "verify": _chk_replace,
         "reveal": "replace that with the report is final",
         "observe": "replace that with"},
    ]

    passed = 0
    total = len(steps)
    for i, step in enumerate(steps, 1):
        seed = step.get("seed")
        if seed:
            engine.ingest(seed)
            _emit(out, f'  (seed dictated: "{seed}")')
        start_text = engine.text                  # step-start buffer state
        ok = False
        for attempt in range(MAX_ATTEMPTS):
            if attempt > 0:
                # clean retry: restore the buffer to the step-start state
                # so a wrong attempt never poisons the rest of the session
                engine.reset()
                if seed:
                    engine.ingest(seed)
                elif start_text:
                    engine.ingest(start_text)
            prompt = step["head"] if attempt == 0 \
                else f"[l3_dictation {i}/{total}] try again — "
            raw = _ask(input_fn, prompt + " > ")
            if raw is None:
                _emit(out, "  (practice abandoned)")
                return passed, total, True
            typed = str(raw).strip()
            ops = engine.ingest(typed)
            text = engine.text
            if step["verify"](ops, text):
                passed += 1
                ok = True
                shown = text.replace("\n", "\\n")
                _emit(out, f'  ✓ dictation buffer: "{shown}"')
                if profile is not None and step.get("observe"):
                    profile.observe_command(_nl.normalize_text(typed),
                                            canonical=step["observe"])
                break
            left = MAX_ATTEMPTS - attempt - 1
            shown = text.replace("\n", "\\n")
            if left > 0:
                _emit(out, f'  ✗ buffer now: "{shown}" — not the expected '
                           f"result ({left} attempt(s) left)")
        if not ok:
            _emit(out, f'  The answer: "{step["reveal"]}"')
    return passed, total, False


def _run_level4(out: Any, input_fn: Callable[[str], str],
                profile: VoiceProfile) -> Tuple[int, int, bool]:
    """Level 4: demonstrate + practice real alias learning (local only)."""
    _emit(out, "  Watch an alias get learned "
               f"(\"{DEMO_ALIAS}\" → \"{DEMO_CANONICAL}\", "
               f"{ALIAS_VOTES_NEEDED} consistent observations):")
    for _ in range(ALIAS_VOTES_NEEDED):
        profile.observe_command(DEMO_ALIAS, canonical=DEMO_CANONICAL)
    got = profile.resolve_alias(DEMO_ALIAS)
    _emit(out, f"  resolve_alias(\"{DEMO_ALIAS}\") -> {got!r} "
               + ("✓ verified live" if got == DEMO_CANONICAL
                  else "(not learned yet)"))

    passed = 0
    total = 1
    head = (f'[l4_personal 1/1] type any wording YOU would say for '
            f'"{PRACTICE_CANONICAL}" (not "{PRACTICE_CANONICAL}" itself) > ')
    ok = False
    for attempt in range(MAX_ATTEMPTS):
        prompt = head if attempt == 0 \
            else "[l4_personal 1/1] try another wording — "
        raw = _ask(input_fn, prompt)
        if raw is None:
            _emit(out, "  (practice abandoned)")
            return passed, total, True
        alias = str(raw).strip()
        if _nl.normalize_text(alias) == PRACTICE_CANONICAL:
            _emit(out, f'  ✗ "{alias}" is already the canonical command — '
                       f"type a DIFFERENT wording you like to say "
                       f"({MAX_ATTEMPTS - attempt - 1} attempt(s) left)")
            continue
        for _ in range(ALIAS_VOTES_NEEDED):
            profile.observe_command(alias, canonical=PRACTICE_CANONICAL)
        learned = profile.resolve_alias(alias)
        if learned == PRACTICE_CANONICAL:
            passed += 1
            ok = True
            _emit(out, f'  ✓ "{alias}" learned → resolves to '
                       f'"{learned}" ({ALIAS_VOTES_NEEDED} observations, '
                       f"real VoiceProfile)")
            break
        _emit(out, f'  ✗ "{alias}" did not learn '
                   f"({MAX_ATTEMPTS - attempt - 1} attempt(s) left)")
    if not ok:
        _emit(out, f'  The answer: any personal wording, e.g. '
                   f'"{PRACTICE_ALIAS_EXAMPLE}" — after 5 observations it '
                   f'resolves to "{PRACTICE_CANONICAL}"')

    _emit(out, "  learned this session (top commands): "
           + (", ".join(f"{c} ({int(r * 100)}%)"
                        for c, r in profile.frequent_commands(5)) or "—"))
    aliases = profile.aliases()
    _emit(out, "  aliases known: "
           + (", ".join(f"{a} → {c}" for a, c in aliases.items()) or "—"))
    _emit(out, "  Learned locally. Nothing is uploaded.")
    _emit(out, "  Memory lifecycle: `airmouse memory reset` clears all "
               "learned data.")
    return passed, total, False


# ---------------------------------------------------------------------------
# curriculum rendering (concept mode)
# ---------------------------------------------------------------------------

def _render_curriculum(out: Any) -> None:
    _emit(out, "VOICE ACADEMY (v16.5 §4) — concept mode")
    _emit(out, PHYSICAL_NOTE)
    _emit(out, MICRO_ONLY_NOTE)
    for lesson in VOICE_LESSONS:
        _emit(out)
        _emit(out, f"[{lesson['id']}] {lesson['title']}")
        _emit(out, f"  {lesson['instruction']}")
        practice = ", ".join(lesson["practice"])
        _emit(out, f"  practice: {practice}")
        _emit(out, f"  verified by: {lesson['verified_by']}")
    _emit(out)
    _emit(out, "Concept mode: the curriculum is shown, nothing is marked "
               "complete.")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def _normalize_level(level: Any) -> Optional[str]:
    s = str(level if level is not None else "all").strip().lower()
    if s in ("", "all", "*"):
        return "all"
    if s in VOICE_LEVEL_IDS:
        return s
    return _LEVEL_ALIASES.get(s)


def run_voice_academy(level: Any = "all",
                      out: Any = None,
                      input_fn: Optional[Callable[[str], str]] = None,
                      profile: Optional[VoiceProfile] = None) -> Dict[str, Any]:
    """Run the 4-level Voice Academy.

    Without a callable ``input_fn`` (or non-interactive) this is CONCEPT
    MODE: the full curriculum is printed, nothing is marked complete and
    ``physical_required`` is True.

    With an ``input_fn`` the requested levels run as text practice where
    EVERY pass is decided by the real matchers (command grammar / NL
    table / VoiceTypingEngine / VoiceProfile) — never by self-report.

    Returns ``{"completed": bool, "physical_required": bool,
    "levels": {level_id: {"completed": bool, "score": (passed, total)}}}``.
    ``physical_required`` is False only when every requested level was
    completed via matcher-verified text practice; the microphone path of
    the Voice Stack remains PHYSICAL TEST REQUIRED regardless (stated in
    the output of every mode).
    """
    which = _normalize_level(level)
    if which is None:
        _emit(out, f"unknown voice academy level {level!r} — valid ids: "
                   + ", ".join(VOICE_LEVEL_IDS))
        return {"completed": False, "physical_required": True, "levels": {}}

    vp = profile if profile is not None else VoiceProfile()

    if not callable(input_fn):
        _render_curriculum(out)
        requested = VOICE_LEVEL_IDS if which == "all" else (which,)
        levels = {lid: {"completed": False, "score": (0, _level_total(lid))}
                  for lid in requested}
        return {"completed": False, "physical_required": True,
                "levels": levels}

    _emit(out, "VOICE ACADEMY (v16.5 §4)")
    _emit(out, PHYSICAL_NOTE)

    run_ids = list(VOICE_LEVEL_IDS) if which == "all" else [which]
    levels: Dict[str, Dict[str, Any]] = {}
    aborted = False
    for lid in run_ids:
        lesson = next(l for l in VOICE_LESSONS if l["id"] == lid)
        _emit(out)
        _emit(out, f"[{lid}] {lesson['title']}")
        _emit(out, f"  {lesson['instruction']}")
        if lid == "l1_basic":
            passed, total, ab = _run_command_items(
                L1_ITEMS, out, input_fn, vp, lid,
                verb="type the command")
        elif lid == "l2_natural":
            passed, total, ab = _run_command_items(
                L2_ITEMS, out, input_fn, vp, lid,
                verb="say it naturally (no exact syntax needed):")
        elif lid == "l3_dictation":
            passed, total, ab = _run_level3(out, input_fn, vp)
        else:
            passed, total, ab = _run_level4(out, input_fn, vp)
        completed = (not ab) and passed == total and total > 0
        levels[lid] = {"completed": completed, "score": (passed, total)}
        _emit(out, f"  level {lid}: {passed}/{total} passed"
                   + (" — COMPLETED" if completed else " — not completed"))
        if ab:
            aborted = True
            break

    completed_all = (not aborted) and all(
        levels[lid]["completed"] for lid in run_ids) and bool(run_ids)
    return {"completed": completed_all,
            "physical_required": not completed_all,
            "levels": levels}


def _level_total(level_id: str) -> int:
    if level_id == "l1_basic":
        return len(L1_ITEMS)
    if level_id == "l2_natural":
        return len(L2_ITEMS)
    if level_id == "l3_dictation":
        return 4
    if level_id == "l4_personal":
        return 1
    return 0


# deterministic formatter demo used by the L3 documentation (pure)
def format_dictation(text: str) -> str:
    """The exact shipped chain: spoken punctuation → capitalization."""
    return capitalize_text(apply_spoken_punctuation(text))
