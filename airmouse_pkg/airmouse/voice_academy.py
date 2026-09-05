"""Voice Academy (v16.5.1, hotfix §5-§10) — REAL voice practice, honest text
practice, progressive lessons, every PASS matcher- or pipeline-verified.

v16.5.0 taught voice with a text quiz only ("type the command").  v16.5.1
keeps that text practice (it is real, deterministic grammar training) but
labels it honestly and adds the REAL pipeline when the machine has the
hardware:

    Microphone → VAD → local ASR → Transcript → Command matcher → Intent

* **§5 REAL VOICE PRACTICE** — when a microphone AND a local ASR engine
  are available, the lesson says ``Say: "click"`` and listens; the
  lesson completes ONLY when the real pipeline recognizes the expected
  intent.  A pass produced by anything else would be a lie, so it
  never happens.
* **§6 TEXT PRACTICE** — when mic/ASR is not available the practice is
  explicitly labelled ``TEXT PRACTICE`` and is never presented as
  microphone validation.
* **§7 PROVIDER DETECTION** — every run starts with the honest VOICE
  HARDWARE panel (microphone, Whisper / Vosk / PocketSphinx, active
  provider) and offers the optional Local Voice Pack when no ASR is
  installed (never auto-installs, never touches a cloud API).
* **§9 PROGRESSIVE LESSONS** — L1 basic command → L2 natural language
  → L3 context → L4 dictation → L5 correction → L6 multimodal →
  L7 personal voice learning.  Each level is harder than the last and
  each is verified by the REAL matcher/engine for that skill.
* **§10 HONEST SEMANTICS** — a revealed answer prints ``Answer
  revealed.  Lesson not completed.``; a skip prints ``Skipped — not
  completed.``  Completion counters never inflate.

``run_voice_academy(level="all", out=None, input_fn=None)`` — without an
``input_fn`` (or non-interactive) it prints the full curriculum in
concept mode and marks nothing complete.  With an ``input_fn`` it runs
the practice levels in whatever mode the machine honestly supports.
``VOICE_LESSONS`` is the exportable curriculum data (consumed by the
teacher module).  ``speak_fn`` injects an ear for tests (returns
``(transcript, confidence)`` per call); ``gaze_observer`` injects the
L6 gaze-fixation check.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import os
import sys
import tempfile
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
    "voice_hardware_panel", "resolve_practice_mode",
]

PHYSICAL_NOTE = ("Microphone practice needs hardware (PHYSICAL TEST REQUIRED); "
                 "text practice below is matcher-verified.")

MICRO_ONLY_NOTE = ("Full speech recognition needs a local ASR engine "
                   "(see: airmouse voice-status); command practice works "
                   "without one.")

#: §6 — the label that keeps text practice honest, everywhere it appears
TEXT_PRACTICE_NOTE = ("TEXT PRACTICE — typing proves the phrase grammar, "
                      "NOT the microphone; live-mic validation stays a "
                      "physical test.")

#: §10 — exact honest strings (contract; tests assert them)
REVEALED_LINE = "Answer revealed. Lesson not completed."
SKIPPED_LINE = "Skipped — not completed."
ABANDONED_LINE = "Practice abandoned — nothing was marked complete."

#: alias-learning rule of the real VoiceProfile (5 consistent votes)
ALIAS_VOTES_NEEDED = 5

DEMO_ALIAS = "launch browser"
DEMO_CANONICAL = "open browser"
PRACTICE_CANONICAL = "close tab"
PRACTICE_ALIAS_EXAMPLE = "kill the tab"

MAX_ATTEMPTS = 3          # first try + 2 retries, then the answer is revealed

VOICE_LEVEL_IDS: Tuple[str, ...] = (
    "l1_basic", "l2_natural", "l3_context", "l4_dictation",
    "l5_correction", "l6_multimodal", "l7_personal")

_LEVEL_ALIASES: Dict[str, str] = {
    "1": "l1_basic", "2": "l2_natural", "3": "l3_context",
    "4": "l4_dictation", "5": "l5_correction", "6": "l6_multimodal",
    "7": "l7_personal",
    "l1": "l1_basic", "l2": "l2_natural", "l3": "l3_context",
    "l4": "l4_dictation", "l5": "l5_correction", "l6": "l6_multimodal",
    "l7": "l7_personal",
    # v16.5.0 names keep resolving (backwards compatibility)
    "l3_dictation": "l4_dictation", "l4_personal": "l7_personal",
    "basic": "l1_basic", "natural": "l2_natural", "context": "l3_context",
    "dictation": "l4_dictation", "correction": "l5_correction",
    "multimodal": "l6_multimodal", "personal": "l7_personal",
}

VOICE_LESSONS: List[Dict[str, Any]] = [
    {
        "id": "l1_basic",
        "title": "Lesson 1 — Basic command",
        "instruction": (
            "Voice control starts from a small set of EXACT commands the "
            "deterministic grammar knows cold.  Say or type each command; "
            "the real pipeline must resolve it to the expected intent for "
            "the lesson to pass."),
        "practice": ["click", "double click", "right click", "scroll down",
                     "scroll up", "open browser", "new tab", "close tab",
                     "copy", "paste", "undo", "redo"],
        "verified_by": "deterministic command grammar (airmouse.voice_commands)",
    },
    {
        "id": "l2_natural",
        "title": "Lesson 2 — Natural language",
        "instruction": (
            "You do not have to memorize exact syntax: natural phrasing like "
            "\"click that\" resolves too.  Honesty note: the grammar has "
            "limits — \"move over there\" is NOT in it yet; the real "
            "movement forms are \"move left\", \"move right\", "
            "\"move window left\", \"move cursor up\"."),
        "practice": ["click that", "scroll down", "close this",
                     "copy that", "move left"],
        "verified_by": "command grammar + v9 NL pattern table + v8 phrase table",
    },
    {
        "id": "l3_context",
        "title": "Lesson 3 — Context",
        "instruction": (
            "Real requests are about YOUR context: \"open my browser\", "
            "\"open my files\", \"minimize window\".  Same real matcher, "
            "everyday phrasing — the lesson proves the intent engine "
            "handles how people actually talk."),
        "practice": ["open my browser", "open my files", "minimize window",
                     "maximize window", "switch window"],
        "verified_by": "command grammar + v9 NL pattern table + v8 phrase table",
    },
    {
        "id": "l4_dictation",
        "title": "Lesson 4 — Dictation",
        "instruction": (
            "Dictation turns speech into formatted text: SAY the punctuation "
            "(\"comma\", \"period\", \"question mark\") and the "
            "deterministic formatters apply it — exactly the shipped "
            "apply_spoken_punctuation + capitalize_text chain."),
        "practice": ["hello bro comma how are you question mark",
                     "good morning period the meeting starts now period"],
        "verified_by": "VoiceTypingEngine buffer state (real formatters)",
    },
    {
        "id": "l5_correction",
        "title": "Lesson 5 — Correction",
        "instruction": (
            "Talking means fixing.  The correction commands the engine "
            "REALLY supports: \"scratch that\" / \"delete that\", "
            "\"delete last word\", \"undo\" / \"redo\", "
            "\"replace that with …\"."),
        "practice": ["scratch that", "delete last word", "undo",
                     "replace that with the report is final"],
        "verified_by": "VoiceTypingEngine edit ops on the real buffer",
    },
    {
        "id": "l6_multimodal",
        "title": "Lesson 6 — Multimodal",
        "instruction": (
            "Look at something on screen and say \"click that\" — gaze "
            "picks the target, voice names the action.  This lesson "
            "completes ONLY when both channels are actually observed "
            "(voice intent AND a gaze fixation); otherwise it stays "
            "honestly incomplete."),
        "practice": ["look at a target, then: click that"],
        "verified_by": "voice intent (real matcher) + observed gaze fixation",
    },
    {
        "id": "l7_personal",
        "title": "Lesson 7 — Personal voice learning",
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
              "move cursor up.  Say/type one of those (or any phrase the "
              "matcher resolves to MOVE).")

L2_ITEMS: List[Tuple[str, IntentType, str]] = [
    ("click that", IntentType.CLICK, ""),
    ("scroll down", IntentType.SCROLL, ""),
    ("close this", IntentType.CLOSE, ""),
    ("copy that", IntentType.COPY, ""),
    ("move over there", IntentType.MOVE, _MOVE_NOTE),
]

L3_CONTEXT_ITEMS: List[Tuple[str, IntentType, str]] = [
    ("open my browser", IntentType.OPEN, ""),
    ("open my files", IntentType.OPEN, ""),
    ("minimize window", IntentType.MINIMIZE, ""),
    ("maximize window", IntentType.MAXIMIZE, ""),
    ("switch window", IntentType.SWITCH_WINDOW, ""),
]


# ---------------------------------------------------------------------------
# §7 — honest hardware panel + practice-mode resolution
# ---------------------------------------------------------------------------

def _check(ok: bool) -> str:
    return "✓" if ok else "○"


def voice_hardware_panel(detect: Optional[Dict[str, Any]] = None) -> str:
    """The §7 VOICE HARDWARE panel — what THIS machine actually has.

    ``detect`` injects a :func:`airmouse.voice_stack.detect_voice_stack`
    snapshot (tests); the real detection is used by default.  Never
    raises; a failed detection degrades to the honest unknown state.
    """
    if detect is None:
        try:
            from .voice_stack import detect_voice_stack
            detect = detect_voice_stack()
        except Exception:
            detect = {"built_in_grammar": True, "speech_recognition": False,
                      "pyaudio": False, "pocketsphinx": False, "vosk": False,
                      "whisper": False, "microphone": False,
                      "active": "Built-in command recognition"}
    mic = bool(detect.get("microphone"))
    any_engine = bool(detect.get("whisper") or detect.get("vosk")
                      or detect.get("pocketsphinx"))
    lines = [
        "VOICE HARDWARE",
        "",
        f"Microphone: {_check(mic)} {'detected' if mic else 'not detected'}",
        "",
        "LOCAL ASR",
        f"  Whisper:      {_check(bool(detect.get('whisper')))}",
        f"  Vosk:         {_check(bool(detect.get('vosk')))}",
        f"  PocketSphinx: {_check(bool(detect.get('pocketsphinx')))}",
        "",
        "Active provider:",
        str(detect.get("active") or "Built-in command recognition"),
        "",
        f"Command grammar: {_check(bool(detect.get('built_in_grammar')))} "
        "available",
    ]
    if not any_engine:
        lines.append("Full speech recognition: NOT INSTALLED")
    return "\n".join(lines)


def resolve_practice_mode(
        speak_fn: Optional[Callable[[], Tuple[str, float]]] = None,
        detect: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Which practice mode THIS run honestly supports (§5 vs §6).

    Returns ``{"mode": "real_voice" | "text", "microphone": bool,
    "provider": str, "reason": str}``.

    ``real_voice`` requires a microphone AND a local ASR engine (or an
    injected ``speak_fn`` — the test ear is still graded by the real
    matchers, only the audio capture is simulated there, and the result
    is labelled ``injected-ear`` in the reason).
    """
    if speak_fn is not None:
        return {"mode": "real_voice", "microphone": True,
                "provider": "injected-ear",
                "reason": "injected speak_fn (test ear; matchers are real)"}
    if detect is None:
        try:
            from .voice_stack import detect_voice_stack
            detect = detect_voice_stack()
        except Exception:
            detect = {}
    mic = bool(detect.get("microphone"))
    provider = str(detect.get("active") or "")
    real_provider = provider and provider != "Built-in command recognition"
    if mic and real_provider:
        return {"mode": "real_voice", "microphone": True,
                "provider": provider,
                "reason": f"microphone + local ASR ({provider}) detected"}
    why = []
    if not mic:
        why.append("no microphone detected")
    if not real_provider:
        why.append("no local ASR engine installed")
    return {"mode": "text", "microphone": mic,
            "provider": provider or "Built-in command recognition",
            "reason": "; ".join(why) + " — text practice available"}


def _offer_pack_if_needed(out: Any, input_fn: Optional[Callable],
                          detect: Optional[Dict[str, Any]]) -> None:
    """§7 — offer the optional Local Voice Pack when no ASR is installed."""
    try:
        from .voice_stack import offer_voice_pack
        d = detect
        if d is None:
            try:
                from .voice_stack import detect_voice_stack
                d = detect_voice_stack()
            except Exception:
                d = {}
        if not (d.get("whisper") or d.get("vosk") or d.get("pocketsphinx")):
            offer_voice_pack(out=out, input_fn=input_fn)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# §5 — the REAL capture hook (Microphone → SR listen → local ASR)
# ---------------------------------------------------------------------------

def _capture_transcript(
        provider: Any, timeout_s: float = 6.0,
        device: Optional[str] = None
) -> Tuple[Optional[str], float]:
    """One REAL utterance through the shipped stack (v16.5.2 §4/§5/§10).

    Primary path: sounddevice capture (lightest existing stack — no
    PyAudio hard requirement) → EnergyVAD → the chosen local ASR
    provider, via :mod:`airmouse.mic_capture`.  When the sounddevice
    backend cannot start, the legacy SpeechRecognition (PyAudio) ear is
    used as fallback.  Audio is adapted per engine: AudioData for
    PocketSphinx, a temp WAV file for Whisper / Vosk (deleted
    immediately — nothing is ever stored).

    Returns ``(transcript, confidence)``; ``transcript is None`` means
    capture failed honestly (noise, timeout, no device) — never a
    fabricated phrase.  Never raises.
    """
    try:
        from .mic_capture import capture_transcript as _real_capture
        return _real_capture(provider, device=device, timeout_s=timeout_s)
    except Exception:
        pass
    # legacy path (mic_capture itself failed to import — defensive)
    try:
        import speech_recognition as sr
    except Exception:
        return None, 0.0
    try:
        rec = sr.Recognizer()
        with sr.Microphone() as source:              # pragma: no cover
            rec.adjust_for_ambient_noise(source, duration=0.4)
            audio = rec.listen(source, timeout=timeout_s,
                               phrase_time_limit=max(2.0, timeout_s))
    except Exception:
        return None, 0.0
    try:
        name = str(getattr(provider, "name", "") or "")
        if name == "pocketsphinx":
            t = provider.transcribe(audio)
            return (t.text or None), float(t.confidence or 0.0)
        # whisper / vosk want a file path — write the captured frame out
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            path = tf.name
        try:
            with open(path, "wb") as wf:
                wf.write(audio.get_wav_data())
            t = provider.transcribe(path)
            text = (t.text or "").strip()
            return (text or None), float(t.confidence or 0.0)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception:
        return None, 0.0


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
# §10 — the honest outcome lines
# ---------------------------------------------------------------------------

def _reveal(out: Any, phrase: str, note: str = "") -> None:
    """The answer reveal — and the honesty that goes with it (§10)."""
    reveal = f'  The answer: "{phrase}"'
    if note:
        reveal += f"  ({note})"
    _emit(out, reveal)
    _emit(out, f"  {REVEALED_LINE}")


def _skip(out: Any) -> bool:
    """A skip: honest wording; returns True (caller moves on, no credit)."""
    _emit(out, f"  {SKIPPED_LINE}")
    return True


# ---------------------------------------------------------------------------
# level runners — L1/L2/L3 (matcher-graded, mode-aware)
# ---------------------------------------------------------------------------

def _call_capture(capture: Callable) -> Tuple[Optional[str], float]:
    """Invoke an ear (injected speak_fn or the real capture hook).

    Liberal in what it accepts: a bare string transcript, a
    ``(transcript, confidence)`` pair, or ``None``.  Never raises.
    """
    try:
        result = capture()
    except StopIteration:
        return None, 0.0
    except Exception:
        return None, 0.0
    if result is None:
        return None, 0.0
    if isinstance(result, str):
        return result, 0.0
    try:
        text, conf = result
        return (str(text) if text is not None else None), \
            float(conf or 0.0)
    except Exception:
        return None, 0.0


def _utterance_for_item(capture: Optional[Callable], phrase: str,
                        input_fn: Callable[[str], str],
                        out: Any, real_mode: bool, attempt: int,
                        head: str
                        ) -> Optional[Tuple[Optional[str], str]]:
    """Get one attempt's utterance in the active mode.

    Returns ``(transcript, source)`` where ``source`` is ``"real"`` /
    ``"text"``; ``(None, "skip")`` for an explicit skip; and ``None``
    when the practice was abandoned (EOF).  In real mode an unusable
    capture is returned as ``("", "real")`` — an honest miss.
    """
    if real_mode and capture is not None:
        _emit(out, "  Listening… (press Enter to skip this item)")
        text, _conf = _call_capture(capture)
        if text is None and not _listen_prompt_confirmed(input_fn):
            return None, "abandon"
        if text is None:
            return "", "real"
        return text, "real"
    prompt = head if attempt == 0 else head.replace("Say or type", "try again —")
    raw = _ask(input_fn, prompt + " > ")
    if raw is None:
        return None, "abandon"
    if not str(raw).strip():
        return None, "skip"
    return str(raw).strip(), "text"


def _listen_prompt_confirmed(input_fn: Optional[Callable]) -> bool:
    """After a failed capture ask whether to move on (False = abandon)."""
    if input_fn is None:
        return False
    try:
        ans = input_fn("  (nothing usable captured — press Enter to "
                       "continue, Ctrl+C to stop): ")
        return True
    except (EOFError, KeyboardInterrupt):
        return False
    except Exception:
        return False


def _run_command_items(items: List[Tuple[str, IntentType, str]],
                       out: Any, input_fn: Callable[[str], str],
                       profile: Optional[VoiceProfile],
                       label: str, verb: str,
                       capture: Optional[Callable] = None,
                       real_mode: bool = False
                       ) -> Tuple[int, int, bool]:
    """Levels 1-3: say/type-a-phrase practice graded by the real matcher.

    Returns (passed, total, aborted).  PASS requires the REAL matcher to
    resolve the utterance to the expected intent — in either mode.
    """
    passed = 0
    total = len(items)
    for i, (phrase, expected, note) in enumerate(items, 1):
        head = f'[{label} {i}/{total}] {verb} "{phrase}"'
        if note:
            _emit(out, f"  ⚠ note: {note}")
        ok = False
        skipped = False
        for attempt in range(MAX_ATTEMPTS):
            got = _utterance_for_item(capture, phrase, input_fn, out,
                                      real_mode, attempt, head)
            if got is None:
                _emit(out, f"  {ABANDONED_LINE}")
                return passed, total, True
            text, source = got
            if source == "skip":
                _skip(out)
                skipped = True
                break
            if source == "abandon":
                _emit(out, f"  {ABANDONED_LINE}")
                return passed, total, True
            r = resolve_voice(text)
            if r.ok and r.intent == expected:
                passed += 1
                ok = True
                shown = "→ heard" if source == "real" else "→"
                _emit(out, f'  ✓ "{text}" {shown} {r.describe()}')
                if profile is not None:
                    profile.observe_command(_nl.normalize_text(text),
                                            canonical=phrase)
                break
            left = MAX_ATTEMPTS - attempt - 1
            if left > 0:
                _emit(out, f'  ✗ "{text}" — {r.describe()} '
                           f"(expected intent: {_fmt_intent(expected)}; "
                           f"{left} attempt(s) left)")
        if not ok and not skipped:
            # §10: a SKIP is never a reveal — only exhausted attempts are
            reveal_note = ""
            if not resolve_voice(phrase).ok:
                reveal_note = ("the suggested phrase itself does not resolve "
                               "in the current grammar — any phrase with the "
                               "same intent passes")
            _reveal(out, phrase, reveal_note)
    return passed, total, False


# ---------------------------------------------------------------------------
# level runners — L4 dictation / L5 correction (engine-graded)
# ---------------------------------------------------------------------------

def _run_step_session(steps: List[Dict[str, Any]], label: str,
                      out: Any, input_fn: Callable[[str], str],
                      profile: Optional[VoiceProfile],
                      capture: Optional[Callable] = None,
                      real_mode: bool = False
                      ) -> Tuple[int, int, bool]:
    """One continuous VoiceTypingEngine session over graded steps.

    Every step verifies the REAL engine state (formatted text / edit
    op).  In real mode the utterance arrives through the microphone;
    in text mode through typing — the engine grading is identical.
    """
    engine = VoiceTypingEngine()
    passed = 0
    total = len(steps)
    for i, step in enumerate(steps, 1):
        if step.get("reset"):
            engine.reset()
            _emit(out, "  (fresh dictation buffer)")
        seed = step.get("seed")
        if seed:
            engine.ingest(seed)
            _emit(out, f'  (seed dictated: "{seed}")')
        start_text = engine.text                  # step-start buffer state
        ok = False
        skipped = False
        for attempt in range(MAX_ATTEMPTS):
            if attempt > 0:
                # clean retry: restore the buffer to the step-start state
                # so a wrong attempt never poisons the rest of the session
                engine.reset()
                if seed:
                    engine.ingest(seed)
                elif start_text:
                    engine.ingest(start_text)
            if real_mode and capture is not None:
                _emit(out, f'  {step["say"]}  — Listening… (Enter = skip)')
                text, _conf = _call_capture(capture)
                if text is None and not _listen_prompt_confirmed(input_fn):
                    _emit(out, f"  {ABANDONED_LINE}")
                    return passed, total, True
                if text is None:
                    _skip(out)
                    skipped = True
                    break
                if not (text or "").strip():
                    _skip(out)
                    skipped = True
                    break
                typed = str(text).strip()
            else:
                prompt = step["head"] if attempt == 0 \
                    else f"[{label} {i}/{total}] try again — "
                raw = _ask(input_fn, prompt + " > ")
                if raw is None:
                    _emit(out, f"  {ABANDONED_LINE}")
                    return passed, total, True
                if not str(raw).strip():
                    _skip(out)
                    skipped = True
                    break
                typed = str(raw).strip()
            ops = engine.ingest(typed)
            text_now = engine.text
            if step["verify"](ops, text_now):
                passed += 1
                ok = True
                shown = text_now.replace("\n", "\\n")
                _emit(out, f'  ✓ dictation buffer: "{shown}"')
                if profile is not None and step.get("observe"):
                    profile.observe_command(_nl.normalize_text(typed),
                                            canonical=step["observe"])
                break
            left = MAX_ATTEMPTS - attempt - 1
            shown = text_now.replace("\n", "\\n")
            if left > 0:
                _emit(out, f'  ✗ buffer now: "{shown}" — not the expected '
                           f"result ({left} attempt(s) left)")
        if not ok and not skipped:
            # §10: a SKIP is never a reveal — only exhausted attempts are
            _reveal(out, step["reveal"])
    return passed, total, False


def _dictation_steps() -> List[Dict[str, Any]]:
    """L4 — spoken punctuation through the REAL formatters."""
    expected_1 = "Hello bro, how are you?"
    expected_2 = "Good morning. The meeting starts now."

    def _chk_1(ops, text) -> bool:
        return text == expected_1

    def _chk_2(ops, text) -> bool:
        return text == expected_2

    return [
        {"kind": "dictate",
         "say": f'Say: "hello bro comma how are you question mark"',
         "head": '[l4_dictation 1/2] dictate with spoken punctuation — '
                 'type: hello bro comma how are you question mark',
         "verify": _chk_1,
         "reveal": "hello bro comma how are you question mark",
         "observe": "hello bro comma how are you question mark"},
        {"kind": "dictate", "reset": True,
         "say": 'Say: "good morning period the meeting starts now period"',
         "head": '[l4_dictation 2/2] dictate — type: good morning period '
                 'the meeting starts now period',
         "verify": _chk_2,
         "reveal": "good morning period the meeting starts now period",
         "observe": "good morning period the meeting starts now period"},
    ]


def _correction_steps() -> List[Dict[str, Any]]:
    """L5 — the correction commands the engine really supports."""
    def _chk_scratch(ops, text) -> bool:
        return text == ""

    def _chk_delete_word(ops, text) -> bool:
        return text == "Draft two ready"

    def _chk_undo(ops, text) -> bool:
        return text == "Draft two ready now"

    def _chk_replace(ops, text) -> bool:
        return any(getattr(o, "op", "") == "replace" for o in ops) \
            and "the report is final" in text

    return [
        {"kind": "edit",
         "seed": "this sentence is wrong",
         "say": 'Say: "scratch that"',
         "head": '[l5_correction 1/4] remove the last segment by voice — '
                 'type: scratch that',
         "verify": _chk_scratch,
         "reveal": "scratch that",
         "observe": "scratch that"},
        {"kind": "edit",
         "seed": "draft two ready now",
         "say": 'Say: "delete last word"',
         "head": '[l5_correction 2/4] drop the last word — '
                 'type: delete last word',
         "verify": _chk_delete_word,
         "reveal": "delete last word",
         "observe": "delete last word"},
        {"kind": "edit",
         "say": 'Say: "undo"',
         "head": '[l5_correction 3/4] bring it back — type: undo',
         "verify": _chk_undo,
         "reveal": "undo",
         "observe": "undo"},
        {"kind": "edit",
         "seed": "the report is ready",
         "say": 'Say: "replace that with the report is final"',
         "head": '[l5_correction 4/4] seed dictated: "the report is ready". '
                 'Replace the last segment — type: '
                 'replace that with the report is final',
         "verify": _chk_replace,
         "reveal": "replace that with the report is final",
         "observe": "replace that with"},
    ]


# ---------------------------------------------------------------------------
# level runners — L6 multimodal (voice intent + observed gaze)
# ---------------------------------------------------------------------------

def _run_multimodal(out: Any, input_fn: Callable[[str], str],
                    capture: Optional[Callable],
                    real_mode: bool,
                    gaze_observer: Optional[Callable[[], bool]]
                    ) -> Tuple[int, int, bool]:
    """L6: look at a target, say \"click that\" — BOTH channels must be
    observed.  Voice half is graded by the real matcher; the gaze half
    by ``gaze_observer`` (live: a real fixation; tests: injected).
    No observer = honest PHYSICAL TEST REQUIRED, never a pass."""
    _emit(out, '  Look at something on screen, then say: "click that"')
    if gaze_observer is None:
        _emit(out, "  gaze observation is not wired in this session — "
                   "the multimodal pass cannot be verified here")
        _emit(out, "  PHYSICAL TEST REQUIRED (gaze + voice together)")
        return 0, 1, False
    utterance: Optional[str] = None
    if real_mode and capture is not None:
        _emit(out, "  Listening… (press Enter to skip)")
        text, _conf = _call_capture(capture)
        if text is None:
            return 0, 1, False
        utterance = (text or "").strip() or None
    else:
        raw = _ask(input_fn,
                   '[l6_multimodal 1/1] gaze ready? type what you say > ')
        if raw is None:
            _emit(out, f"  {ABANDONED_LINE}")
            return 0, 1, True
        if not str(raw).strip():
            _skip(out)
            return 0, 1, False
        utterance = str(raw).strip()
    r = resolve_voice(utterance or "")
    gaze_ok = False
    try:
        gaze_ok = bool(gaze_observer())
    except Exception:
        gaze_ok = False
    if r.ok and r.intent == IntentType.CLICK and gaze_ok:
        _emit(out, f'  ✓ "{utterance}" — voice intent CLICK + gaze '
                   "fixation observed — multimodal pass verified")
        return 1, 1, False
    if not gaze_ok:
        _emit(out, "  ✗ no gaze fixation was observed during the lesson")
    if not (r.ok and r.intent == IntentType.CLICK):
        _emit(out, f'  ✗ voice half: "{utterance}" — {r.describe()} '
                   "(expected intent: click)")
    _reveal(out, "click that")
    return 0, 1, False


# ---------------------------------------------------------------------------
# level runner — L7 personal voice learning (unchanged machinery)
# ---------------------------------------------------------------------------

def _run_personal(out: Any, input_fn: Callable[[str], str],
                  profile: VoiceProfile) -> Tuple[int, int, bool]:
    """L7: demonstrate + practice real alias learning (local only)."""
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
    head = (f'[l7_personal 1/1] type any wording YOU would say for '
            f'"{PRACTICE_CANONICAL}" (not "{PRACTICE_CANONICAL}" itself) > ')
    ok = False
    skipped = False
    for attempt in range(MAX_ATTEMPTS):
        prompt = head if attempt == 0 \
            else "[l7_personal 1/1] try another wording — "
        raw = _ask(input_fn, prompt)
        if raw is None:
            _emit(out, f"  {ABANDONED_LINE}")
            return passed, total, True
        if not str(raw).strip():
            _skip(out)
            skipped = True
            break
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
    if not ok and not skipped:
        # §10: a SKIP is never a reveal — only exhausted attempts are
        _reveal(out, f"any personal wording, e.g. "
                     f'"{PRACTICE_ALIAS_EXAMPLE}" — after 5 observations it '
                     f'resolves to "{PRACTICE_CANONICAL}"',
                note="")

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
    _emit(out, "VOICE ACADEMY (v16.5.1 §5-§10) — concept mode")
    _emit(out, PHYSICAL_NOTE)
    _emit(out, MICRO_ONLY_NOTE)
    _emit(out, TEXT_PRACTICE_NOTE)
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
                      profile: Optional[VoiceProfile] = None,
                      speak_fn: Optional[Callable[[], Tuple[str, float]]] = None,
                      gaze_observer: Optional[Callable[[], bool]] = None,
                      detect: Optional[Dict[str, Any]] = None,
                      offer_pack: bool = True,
                      ) -> Dict[str, Any]:
    """Run the 7-level Voice Academy (v16.5.1).

    Without a callable ``input_fn`` (or non-interactive) this is CONCEPT
    MODE: the full curriculum is printed, nothing is marked complete and
    ``physical_required`` is True.

    With an ``input_fn`` the requested levels run in the honest mode
    for THIS machine:

    * ``real_voice`` — microphone + local ASR (or an injected
      ``speak_fn``); every pass is decided by the REAL pipeline
      (ASR transcript → command matcher → intent).
    * ``text`` — explicitly labelled TEXT PRACTICE (§6); every pass is
      decided by the real matchers, and the output never calls it
      microphone validation.

    Returns ``{"completed": bool, "physical_required": bool, "mode": str,
    "levels": {level_id: {"completed": bool, "score": (passed, total),
    "mode": str}}}``.
    """
    which = _normalize_level(level)
    if which is None:
        _emit(out, f"unknown voice academy level {level!r} — valid ids: "
                   + ", ".join(VOICE_LEVEL_IDS))
        return {"completed": False, "physical_required": True,
                "mode": "concept", "levels": {}}

    vp = profile if profile is not None else VoiceProfile()

    if not callable(input_fn):
        _render_curriculum(out)
        requested = list(VOICE_LEVEL_IDS) if which == "all" else [which]
        levels = {lid: {"completed": False, "score": (0, _level_total(lid)),
                        "mode": "concept"}
                  for lid in requested}
        return {"completed": False, "physical_required": True,
                "mode": "concept", "levels": levels}

    # §7 — the honest hardware panel, then the mode decision
    _emit(out, "VOICE ACADEMY (v16.5.1)")
    _emit(out, voice_hardware_panel(detect=detect))
    _emit(out)
    mode_info = resolve_practice_mode(speak_fn=speak_fn, detect=detect)
    real_mode = mode_info["mode"] == "real_voice"
    if not real_mode:
        _emit(out, "Real microphone practice: PHYSICAL TEST REQUIRED "
                   f"({mode_info['reason']})")
        _emit(out, "Text practice: AVAILABLE")
        _emit(out, f"  ({TEXT_PRACTICE_NOTE})")
        # §7 — offer the optional Local Voice Pack, but ONLY on a real
        # interactive stdin: a scripted/testing input_fn must never be
        # consumed by the offer prompt.
        if offer_pack and input_fn is input:
            _offer_pack_if_needed(out, input_fn, detect)
    else:
        _emit(out, f"REAL MICROPHONE PRACTICE via {mode_info['provider']} "
                   "— only a recognition by the real pipeline completes "
                   "a lesson.")
    _emit(out, PHYSICAL_NOTE if not real_mode else
          ("live microphone + local ASR are active; every pass below is "
           "verified by the real pipeline."))
    _emit(out)

    capture: Optional[Callable[[], Tuple[str, float]]] = None
    if real_mode:
        if speak_fn is not None:
            capture = speak_fn
        else:
            try:
                from .voice_stack import pick_provider
                _prov = pick_provider()
                if _prov is None or not getattr(_prov, "available", lambda: False)():
                    capture = None
                    real_mode = False
                    _emit(out, "  (the selected ASR engine is not usable — "
                               "falling back to TEXT PRACTICE, honestly "
                               "labelled)")
                    _emit(out, f"  {TEXT_PRACTICE_NOTE}")
                else:
                    capture = lambda: _capture_transcript(_prov)  # noqa: E731
            except Exception:
                capture = None
                real_mode = False
                _emit(out, "  (microphone capture could not start — "
                           "falling back to TEXT PRACTICE, honestly "
                           "labelled)")
                _emit(out, f"  {TEXT_PRACTICE_NOTE}")

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
                verb="Say or type the command" if real_mode
                else "type the command",
                capture=capture, real_mode=real_mode)
        elif lid == "l2_natural":
            passed, total, ab = _run_command_items(
                L2_ITEMS, out, input_fn, vp, lid,
                verb="say it naturally (no exact syntax needed):",
                capture=capture, real_mode=real_mode)
        elif lid == "l3_context":
            passed, total, ab = _run_command_items(
                L3_CONTEXT_ITEMS, out, input_fn, vp, lid,
                verb="say it in your own context:",
                capture=capture, real_mode=real_mode)
        elif lid == "l4_dictation":
            passed, total, ab = _run_step_session(
                _dictation_steps(), "l4_dictation", out, input_fn, vp,
                capture=capture, real_mode=real_mode)
        elif lid == "l5_correction":
            passed, total, ab = _run_step_session(
                _correction_steps(), "l5_correction", out, input_fn, vp,
                capture=capture, real_mode=real_mode)
        elif lid == "l6_multimodal":
            passed, total, ab = _run_multimodal(
                out, input_fn, capture, real_mode, gaze_observer)
        else:
            passed, total, ab = _run_personal(out, input_fn, vp)
        completed = (not ab) and passed == total and total > 0
        levels[lid] = {"completed": completed, "score": (passed, total),
                       "mode": "real_voice" if real_mode else "text"}
        _emit(out, f"  level {lid}: {passed}/{total} passed"
                   + (" — COMPLETED" if completed
                      else " — not completed"))
        if ab:
            aborted = True
            break

    completed_all = (not aborted) and all(
        levels[lid]["completed"] for lid in run_ids) and bool(run_ids)
    return {"completed": completed_all,
            "physical_required": not completed_all,
            "mode": mode_info["mode"],
            "levels": levels}


def _level_total(level_id: str) -> int:
    if level_id == "l1_basic":
        return len(L1_ITEMS)
    if level_id == "l2_natural":
        return len(L2_ITEMS)
    if level_id == "l3_context":
        return len(L3_CONTEXT_ITEMS)
    if level_id == "l4_dictation":
        return 2
    if level_id == "l5_correction":
        return 4
    if level_id == "l6_multimodal":
        return 1
    if level_id == "l7_personal":
        return 1
    return 0


# deterministic formatter demo used by the L4 documentation (pure)
def format_dictation(text: str) -> str:
    """The exact shipped chain: spoken punctuation → capitalization."""
    return capitalize_text(apply_spoken_punctuation(text))
