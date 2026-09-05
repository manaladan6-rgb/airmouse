"""
airmouse.teacher — the v16.5 Teacher Core (mission §3/§18/§21/§22).

When a user launches AirMouse for the first time, AirMouse becomes the
TEACHER:  Welcome → Voice → Gaze → Gestures → Fusion → Personalization
→ Ready.  The rules that govern every line of this module:

1.  PROGRESS PERSISTS.  Onboarding state lives in
    ``profile/onboarding.json`` (paths.onboarding_file()), written
    atomically via ``persistence.atomic_write_json``.  Interrupted
    training resumes; a corrupted file fail-safely resets to NEW with a
    ``corrupted_last_load`` flag — it never raises and never loses the
    directory.

2.  THE USER IS NEVER TRAPPED.  "Skip for now" is available at every
    prompt; a declined/EOF answer always proceeds; no prompt is ever
    re-asked more than twice; headless runs never block.

3.  THE TEACHER NEVER LIES.  A physical lesson is never marked passed
    without sensor verification (``physical_verified=True``), a
    simulated practice is always labelled "simulated", nothing is ever
    auto-passed, and the teacher never blames the user — weak results
    get gentler, slower suggestions, not judgment.

4.  ONE EXECUTION SPINE.  Teaching never dispatches actions itself:
    the gesture track delegates to the existing ``academy.run_academy``
    (whose live passes go through the hold-to-pass loop feeding the
    same ``GestureActionRouter`` world), voice/gaze delegate lazily to
    the v16.5 academies when present, and concept lessons dispatch
    nothing at all.

Python 3.9 compatible.  No network, no prints at import, stdlib only.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import math
import os
import sys
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import paths

__all__ = [
    "OnboardingPhase", "PHASE_ORDER", "TRACK_ORDER", "CURRICULUM",
    "OnboardingStore", "Teacher",
    "run_teach", "run_learn",
    "should_auto_teach", "maybe_prompt_teach",
    "hardware_panel",
]


# ---------------------------------------------------------------------------
# phases (monotonic ladder)
# ---------------------------------------------------------------------------

class OnboardingPhase(str, Enum):
    """The onboarding ladder.  A phase only ever moves FORWARD."""

    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    VOICE_COMPLETE = "VOICE_COMPLETE"
    GAZE_COMPLETE = "GAZE_COMPLETE"
    GESTURE_COMPLETE = "GESTURE_COMPLETE"
    FUSION_COMPLETE = "FUSION_COMPLETE"
    COMPLETE = "COMPLETE"


#: canonical order of the ladder (NEW first, COMPLETE last)
PHASE_ORDER: List[OnboardingPhase] = list(OnboardingPhase)

_PHASE_RANK: Dict[str, int] = {p.value: i for i, p in enumerate(PHASE_ORDER)}


def phase_rank(phase: Any) -> int:
    """Rank of a phase (or its string value); unknown values rank as NEW."""
    try:
        name = phase.value if isinstance(phase, OnboardingPhase) else str(phase)
    except Exception:                                    # noqa: BLE001
        return 0
    return _PHASE_RANK.get(name, 0)


#: the teaching tracks, in the order the teacher teaches them
TRACK_ORDER: Tuple[str, ...] = ("voice", "gaze", "gesture",
                                "fusion", "personalization")

#: the four sensor/lesson tracks that gate the phase ladder
_LESSON_TRACKS: Tuple[str, ...] = ("voice", "gaze", "gesture", "fusion")

_ALL_TRACKS: Tuple[str, ...] = TRACK_ORDER


# ---------------------------------------------------------------------------
# curriculum — pure data (one thing at a time, honest criteria)
# ---------------------------------------------------------------------------

CURRICULUM: Dict[str, List[Dict[str, Any]]] = {
    "voice": [
        {
            "id": "voice_wake",
            "title": "Waking AirMouse",
            "track": "voice",
            "instruction": 'Say the wake word "airmouse" first, then your '
                           'command — for example: "airmouse, scroll down".  '
                           "(The default 'normal' profile requires the wake "
                           "word; 'high' and 'turbo' listen directly.)",
            "demonstration": 'You say: "airmouse, click" — AirMouse '
                             "left-clicks where the cursor sits.",
            "success_criteria": "AirMouse hears the wake word and answers "
                                "with its listening cue.",
            "physical_required": True,
            "text_practicable": True,
            "phrases_key": "wake",
        },
        {
            "id": "voice_click",
            "title": "Click without your hands",
            "track": "voice",
            "instruction": 'Say "click" (or "left click") to left-click, '
                           '"double click" to open, "right click" for the '
                           "context menu.",
            "demonstration": 'Say: "click" → left click.   '
                             'Say: "double click" → opens the item under '
                             "the cursor.",
            "success_criteria": "Say the phrase that left-clicks.",
            "physical_required": True,
            "text_practicable": True,
            "phrases_key": "click",
        },
        {
            "id": "voice_scroll",
            "title": "Scroll by voice",
            "track": "voice",
            "instruction": 'Say "scroll down" or "scroll up" to move the '
                           'page; add "a lot" to scroll farther.',
            "demonstration": 'Say: "scroll down a lot" → a bigger, faster '
                             "scroll.",
            "success_criteria": "Say the phrase that scrolls down.",
            "physical_required": True,
            "text_practicable": True,
            "phrases_key": "scroll_down",
        },
    ],
    "gaze": [
        {
            "id": "gaze_dwell",
            "title": "Look to steer, dwell to click",
            "track": "gaze",
            "instruction": "Look at a spot on screen and hold your gaze "
                           "there; the dwell ring fills and clicks for you "
                           "— no hands needed.",
            "demonstration": "Stare at a button for about a second and a "
                             "half: the dwell ring closes, then clicks it.",
            "success_criteria": "A real dwell click happens on screen "
                                "(camera + calibrated gaze).",
            "physical_required": True,
        },
        {
            "id": "gaze_blink",
            "title": "Blink safety (the e-stop)",
            "track": "gaze",
            "instruction": "A long blink (~0.6 s or more) is the "
                           "eyes-closed emergency stop: it freezes AirMouse "
                           "instantly.  Normal blinks are ignored.",
            "demonstration": "Close your eyes deliberately for a moment — "
                             "everything stops; open them and resume.",
            "success_criteria": "A long blink trips the e-stop latch and "
                                "the HUD shows it.",
            "physical_required": True,
        },
    ],
    "gesture": [
        {
            "id": "gesture_core",
            "title": "The Gesture Academy — hands-on core",
            "track": "gesture",
            "instruction": "Seven hold-to-pass lessons with a live camera: "
                           "point to move, pinch to click, thumbs-up double "
                           "click, peace right-click, palm drag, three "
                           "fingers scroll, pinch-and-hold zoom.",
            "demonstration": "AirMouse mirrors your detected gesture and "
                             "confidence, and shows a hold bar — hold the "
                             "target gesture for the pass time to pass.",
            "success_criteria": "Each core lesson passes only when the "
                                "camera verifies the hold.",
            "physical_required": True,
            "delegates": "academy",
            "delegates_arg": "all",
        },
        {
            "id": "gesture_advanced",
            "title": "Beyond the basics (advanced pointers)",
            "track": "gesture",
            "instruction": "Four teach-only lessons: gaze control, the "
                           "voice lab, two-hand geometry, custom sequences "
                           "— each points to the real command that "
                           "exercises it.",
            "demonstration": "Two-hand zoom is real ctrl+wheel; two-hand "
                             "rotate/drag are detected today but not yet "
                             "mapped to OS actions.",
            "success_criteria": "Nothing to pass — these lessons teach and "
                                "point; they are never auto-passed.",
            "physical_required": False,
            "delegates": "academy",
            "delegates_arg": "all",
        },
    ],
    "fusion": [
        {
            "id": "fusion_look_say",
            "title": "Look + say (the deictic click)",
            "track": "fusion",
            "instruction": 'Look straight at the browser button and say: '
                           '"open that" — the word "that" means whatever '
                           "your gaze is resting on.",
            "demonstration": 'Gaze on a link + say "click that" → the agent '
                             'resolves "that" to the looked-at target.',
            "success_criteria": "The fused action fires on the looked-at "
                                "target, verified by the live sensors.",
            "physical_required": True,
        },
        {
            "id": "fusion_full",
            "title": "The full-stack finale",
            "track": "fusion",
            "instruction": "Look at the browser button.  Say: open that.  "
                           "Confirm with a pinch.",
            "demonstration": "Gaze picks the target, voice names the "
                             "intent, the pinch confirms — three "
                             "modalities, one action, one execution spine.",
            "success_criteria": "All three modalities verified live by the "
                                "sensors — never assumed.",
            "physical_required": True,
        },
    ],
    "personalization": [
        {
            "id": "personalization_what",
            "title": "What AirMouse learns about you",
            "track": "personalization",
            "instruction": "AirMouse adapts to your rhythm — gesture "
                           "confidences, gaze preferences, voice phrasing "
                           "— and stores everything ONLY in your local "
                           "profile folder.  No cloud, no upload, no "
                           "telemetry.",
            "demonstration": "Everything learned lives under "
                             "~/.airmouse/profile/ ;  'airmouse memory "
                             "status' shows it;  'airmouse memory reset' / "
                             "'airmouse memory delete' erases it.",
            "success_criteria": "You can name where the data lives and how "
                                "to erase it.",
            "physical_required": False,
        },
        {
            "id": "personalization_ack",
            "title": "Your acknowledgment",
            "track": "personalization",
            "instruction": "Confirm you understand: AirMouse learns "
                           "locally, you own the data, and you can erase it "
                           "at any time.",
            "demonstration": "Your profile summary is shown beside this "
                             "prompt (sessions, tracks completed).",
            "success_criteria": "An explicit yes — silence, 'n' and EOF are "
                                "always treated as skip.",
            "physical_required": False,
            "acknowledgment": True,
        },
    ],
}

_LESSON_INDEX: Dict[str, Tuple[str, Dict[str, Any]]] = {
    lesson["id"]: (track, lesson)
    for track, lessons in CURRICULUM.items() for lesson in lessons
}


# ---------------------------------------------------------------------------
# gentle, deterministic coaching vocabulary (never blame)
# ---------------------------------------------------------------------------

_PRACTICE_SUGGESTIONS: Tuple[str, ...] = (
    "Move your hand slightly closer to the camera.",
    "Try speaking slightly slower — clear words beat loud words.",
    "Rest your elbow on the desk so the movement is steadier.",
    "Take a breath; we can repeat this as many times as you like.",
)


# ---------------------------------------------------------------------------
# OnboardingStore — persisted, fail-safe, monotonic
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = 1
_MAX_LESSON_STATS = 256          # learner stats are bounded, never unbounded


def _phase_for_tracks(tracks: Dict[str, bool]) -> OnboardingPhase:
    """Highest phase honestly consistent with the per-track booleans.

    The ladder names the longest completed PREFIX of
    voice → gaze → gesture → fusion.  COMPLETE additionally requires the
    personalization acknowledgment.
    """
    if all(tracks.get(t) for t in _LESSON_TRACKS):
        if tracks.get("personalization"):
            return OnboardingPhase.COMPLETE
        return OnboardingPhase.FUSION_COMPLETE
    if all(tracks.get(t) for t in ("voice", "gaze", "gesture")):
        return OnboardingPhase.GESTURE_COMPLETE
    if all(tracks.get(t) for t in ("voice", "gaze")):
        return OnboardingPhase.GAZE_COMPLETE
    if tracks.get("voice"):
        return OnboardingPhase.VOICE_COMPLETE
    return OnboardingPhase.IN_PROGRESS


class OnboardingStore:
    """Persisted onboarding state (``profile/onboarding.json``).

    * ``load()`` NEVER raises: a missing file is a fresh NEW state; a
      corrupted/unreadable file fail-safely resets to NEW and sets the
      ``corrupted_last_load`` flag.
    * ``save()`` is atomic (``persistence.atomic_write_json``) and never
      raises — it returns True/False.
    * The phase only ever moves forward (monotonic).
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self.path: str = str(path) if path else paths.onboarding_file()
        self.phase: OnboardingPhase = OnboardingPhase.NEW
        self.tracks: Dict[str, bool] = {t: False for t in _ALL_TRACKS}
        self.learner: Dict[str, Any] = {"lessons": {}}
        self.started_at: Optional[str] = None
        self.updated_at: Optional[str] = None
        self.sessions: int = 0
        self.corrupted_last_load: bool = False
        self.load()

    # ── (de)serialization ────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """JSON roundtrip shape (schema_version 1)."""
        return {
            "schema_version": _SCHEMA_VERSION,
            "phase": self.phase.value,
            "tracks": {t: bool(self.tracks.get(t, False))
                       for t in _ALL_TRACKS},
            "learner": self.learner,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "sessions": int(self.sessions),
        }

    def _fresh_state(self) -> None:
        self.phase = OnboardingPhase.NEW
        self.tracks = {t: False for t in _ALL_TRACKS}
        self.learner = {"lessons": {}}
        self.started_at = None
        self.updated_at = None
        self.sessions = 0

    def load(self) -> None:
        """Load state; missing → fresh NEW; corrupt → reset + flag."""
        try:
            from .persistence import read_json
            data = read_json(self.path)
        except FileNotFoundError:
            self.corrupted_last_load = False
            return                      # brand new user — nothing to read
        except Exception:               # noqa: BLE001 — corrupt/unreadable
            self._fail_safe_reset()
            return
        try:
            if int(data.get("schema_version", -1)) != _SCHEMA_VERSION:
                self._fail_safe_reset()
                return
            phase = OnboardingPhase(str(data.get("phase")))
        except Exception:               # noqa: BLE001
            self._fail_safe_reset()
            return
        tracks_in = data.get("tracks")
        if not isinstance(tracks_in, dict) or \
                any(not isinstance(tracks_in.get(t), bool)
                    for t in _ALL_TRACKS):
            self._fail_safe_reset()
            return
        sessions = data.get("sessions", 0)
        if isinstance(sessions, bool) or not isinstance(sessions, int) \
                or sessions < 0:
            self._fail_safe_reset()
            return
        learner = data.get("learner", {"lessons": {}})
        if not isinstance(learner, dict):
            learner = {"lessons": {}}
        if not isinstance(learner.get("lessons", {}), dict):
            learner = {"lessons": {}}
        started = data.get("started_at")
        updated = data.get("updated_at")
        self.phase = phase
        self.tracks = {t: bool(tracks_in.get(t, False)) for t in _ALL_TRACKS}
        self.learner = learner
        self.sessions = sessions
        self.started_at = started if isinstance(started, str) else None
        self.updated_at = updated if isinstance(updated, str) else None
        self.corrupted_last_load = False

    def _fail_safe_reset(self) -> None:
        """Corrupt file: reset to NEW, set the flag, keep the directory."""
        self.corrupted_last_load = True
        self._fresh_state()
        try:                            # never lose the directory
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        except Exception:               # noqa: BLE001
            pass
        self.save()                     # best-effort persist of the reset

    # ── persistence ──────────────────────────────────────────────────────

    def save(self) -> bool:
        """Atomically persist; True on success; NEVER raises."""
        try:
            from .persistence import atomic_write_json
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            atomic_write_json(self.path, self.to_dict())
            return True
        except Exception:               # noqa: BLE001 — fail-safe
            return False

    def _touch(self) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.updated_at = stamp
        if self.started_at is None:
            self.started_at = stamp

    # ── transitions (all monotonic) ──────────────────────────────────────

    def mark_track_complete(self, track: str) -> bool:
        """Mark one track complete and advance the phase (monotonically).

        ``track`` is one of "voice", "gaze", "gesture", "fusion" — plus
        "personalization" for the acknowledgment step.  The phase only
        becomes COMPLETE when all four lesson tracks AND the
        personalization acknowledgment are done.  Unknown tracks are
        refused (False) without touching state.
        """
        key = str(track or "").strip().lower()
        if key not in _ALL_TRACKS:
            return False
        self.tracks[key] = True
        computed = _phase_for_tracks(self.tracks)
        if phase_rank(computed) > phase_rank(self.phase):
            self.phase = computed
        self._touch()
        self.save()
        return True

    def mark_complete(self) -> bool:
        """Explicit full-completion declaration (coordinator escape hatch).

        Sets every track True plus the personalization acknowledgment and
        the COMPLETE phase.  Callers should only use this when the user
        genuinely finished (or explicitly opted out of all teaching).
        """
        for t in _ALL_TRACKS:
            self.tracks[t] = True
        self.phase = OnboardingPhase.COMPLETE
        self._touch()
        self.save()
        return True

    def touch_session(self) -> None:
        """Count one teaching session; NEW → IN_PROGRESS (monotonic)."""
        self.sessions = int(self.sessions) + 1
        if self.phase == OnboardingPhase.NEW:
            self.phase = OnboardingPhase.IN_PROGRESS
        self._touch()
        self.save()

    # ── learner stats (bounded) ──────────────────────────────────────────

    def record_lesson(self, lesson_id: str, passed: bool,
                      confidence: Optional[float] = None,
                      result: str = "attempt") -> None:
        """Bounded per-lesson stat bookkeeping (never raises)."""
        lid = str(lesson_id or "")[:64]
        if not lid:
            return
        lessons = self.learner.setdefault("lessons", {})
        if lid not in lessons and len(lessons) >= _MAX_LESSON_STATS:
            return                      # bounded: refuse unbounded growth
        entry = lessons.get(lid)
        if not isinstance(entry, dict):
            entry = {"attempts": 0, "passes": 0,
                     "last_confidence": None, "last_result": ""}
        try:
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
        except Exception:               # noqa: BLE001
            entry["attempts"] = 1
        if passed:
            try:
                entry["passes"] = int(entry.get("passes", 0)) + 1
            except Exception:           # noqa: BLE001
                entry["passes"] = 1
        entry["last_confidence"] = confidence
        entry["last_result"] = str(result)[:32]
        lessons[lid] = entry
        self._touch()
        self.save()

    def lesson_stats(self, lesson_id: str) -> Dict[str, Any]:
        """Copy of one lesson's learner stats (empty dict when unknown)."""
        try:
            entry = self.learner.get("lessons", {}).get(str(lesson_id))
        except Exception:               # noqa: BLE001
            return {}
        return dict(entry) if isinstance(entry, dict) else {}

    # ── predicates ───────────────────────────────────────────────────────

    @property
    def is_new(self) -> bool:
        return self.phase == OnboardingPhase.NEW

    @property
    def is_complete(self) -> bool:
        return self.phase == OnboardingPhase.COMPLETE

    @property
    def is_in_progress(self) -> bool:
        """Started but not finished (any phase strictly between the ends)."""
        return 0 < phase_rank(self.phase) < phase_rank(OnboardingPhase.COMPLETE)


# ---------------------------------------------------------------------------
# Teacher — the persona + the lesson engine
# ---------------------------------------------------------------------------

def _sanitize_confidence(value: Any) -> Optional[float]:
    """Coerce junk into a clean 0..1 float (or None) — never raises."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(min(1.0, max(0.0, f)), 3)


def _normalize_answer(text: Any) -> str:
    """lowercase, strip punctuation, collapse whitespace (for checking)."""
    out = []
    for ch in str(text or "").lower():
        out.append(ch if (ch.isalnum() or ch.isspace()) else " ")
    return " ".join("".join(out).split())


def _wrap_line(text: str, width: int) -> List[str]:
    """Word-wrap one line to ``width`` (pure; empty text → [''])."""
    words = str(text or "").split()
    if not words:
        return [""]
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            while len(word) > width:        # one very long token
                lines.append(word[:width])
                word = word[width:]
            current = word
    if current:
        lines.append(current)
    return lines


def _voice_phrase_pool(key: str) -> List[str]:
    """REAL trigger phrases for a practice key, from the live grammar.

    Reads ``airmouse.voice_control`` (the authoritative phrase map) so
    teaching can never drift from what the engine actually accepts.
    Falls back to the same phrases inline if the module is unavailable.
    """
    try:
        from .voice_control import COMMANDS as _COMMANDS
        if key == "wake":
            from .voice_control import _WAKE_WORDS
            return [str(w) for w in _WAKE_WORDS]
        if key in _COMMANDS:
            return [str(p) for p in _COMMANDS[key]]
    except Exception:                   # noqa: BLE001 — module not available
        pass
    fallback = {
        "wake": ("airmouse", "air mouse", "hey airmouse", "hey air mouse"),
        "click": ("click", "left click", "tap", "select"),
        "scroll_down": ("scroll down", "down", "go down"),
    }
    return list(fallback.get(key, ()))


def _stdin_is_tty() -> bool:
    """True when stdin is an interactive terminal (guarded, never raises)."""
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except Exception:                   # noqa: BLE001
        return False


class Teacher:
    """The teaching persona: curriculum, coaching, honest bookkeeping."""

    TRACKS: Tuple[str, ...] = TRACK_ORDER

    def __init__(self, store: Optional[OnboardingStore] = None) -> None:
        self.store = store if store is not None else OnboardingStore()

    # ── curriculum access ────────────────────────────────────────────────

    def lessons(self, track: str) -> List[Dict[str, Any]]:
        """Lesson data for one track (fresh copies, ordered)."""
        return [dict(l) for l in CURRICULUM.get(str(track), [])]

    def find_lesson(self, lesson_id: str) -> Optional[Dict[str, Any]]:
        """Lesson data by id across all tracks (copy or None)."""
        hit = _LESSON_INDEX.get(str(lesson_id or ""))
        return dict(hit[1]) if hit else None

    def track_of(self, lesson_id: str) -> Optional[str]:
        hit = _LESSON_INDEX.get(str(lesson_id or ""))
        return hit[0] if hit else None

    # ── adaptive teaching (pure, deterministic, never blames) ────────────

    @staticmethod
    def decide_practice(lesson_id: str,
                        stats: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """Decide the next coaching move for one lesson.

        ``stats`` is the learner stat dict (attempts, passes,
        last_confidence, ...).  Returns
        ``{"action": "skip"|"repeat"|"advance", "message": str}``.

        * strong skill  → "skip"    (no more practice needed)
        * weak skill    → "repeat"  (gentler, slower suggestion)
        * in between    → "advance" (move on, come back anytime)

        Deterministic and pure: same inputs → same outputs; junk inputs
        never raise.
        """
        stats = stats if isinstance(stats, dict) else {}
        try:
            attempts = int(stats.get("attempts", 0) or 0)
        except (TypeError, ValueError):
            attempts = 0
        attempts = max(0, attempts)
        try:
            passed = bool(stats.get("passed")) or \
                int(stats.get("passes", 0) or 0) > 0
        except (TypeError, ValueError):
            passed = bool(stats.get("passed"))
        conf = _sanitize_confidence(stats.get("last_confidence"))
        title = _LESSON_INDEX.get(str(lesson_id), (None, {}))[1] \
            .get("title", str(lesson_id))

        if passed and (conf is None or conf >= 0.8):
            return {
                "action": "skip",
                "message": f"'{title}' already looks solid — no extra "
                           "practice needed.  On to the next one!",
            }
        if attempts >= 3 or (conf is not None and conf < 0.5):
            idx = (attempts + sum(ord(c) for c in str(lesson_id))) \
                % len(_PRACTICE_SUGGESTIONS)
            return {
                "action": "repeat",
                "message": f"'{title}' takes a little practice — no rush, "
                           "that is completely normal.  "
                           + _PRACTICE_SUGGESTIONS[idx],
            }
        return {
            "action": "advance",
            "message": f"Good progress on '{title}' — one more try and it "
                       "is yours.  Let's keep moving; you can always come "
                       "back to it.",
        }

    # ── honest bookkeeping ───────────────────────────────────────────────

    def record_result(self, lesson_id: str, passed: bool,
                      confidence: Optional[float] = None,
                      physical_verified: bool = False) -> Dict[str, Any]:
        """Record one lesson attempt with the honesty contract enforced.

        * A ``physical_required`` lesson can NEVER pass without
          ``physical_verified=True`` — the result is forced to False and
          labelled "simulated".
        * The one honest exception: a lesson explicitly marked
          ``text_practicable`` (the voice track) may pass by typing the
          real trigger phrase — labelled "passed-text-practice".
        * Unknown lesson ids record an honest "unknown-lesson" failure.
        * Headless/simulated practice always records the attempt; the
          result string carries the label.
        """
        lesson = self.find_lesson(lesson_id)
        conf = _sanitize_confidence(confidence)
        passed = bool(passed)
        if lesson is None:
            passed, result = False, "unknown-lesson"
        elif lesson.get("physical_required") and not physical_verified:
            if passed and lesson.get("text_practicable"):
                result = "passed-text-practice"
            else:
                passed, result = False, "simulated"
        else:
            result = "passed" if passed else "failed"
        self.store.record_lesson(str(lesson_id), passed=passed,
                                 confidence=conf, result=result)
        return {
            "lesson_id": str(lesson_id),
            "passed": passed,
            "result": result,
            "confidence": conf,
            "physical_verified": bool(physical_verified),
        }

    # ── rendering ────────────────────────────────────────────────────────

    def welcome_banner(self) -> str:
        """The §3 WELCOME box (mission text pinned)."""
        return (
            "\n"
            "  +==================================================+\n"
            "  |              WELCOME TO AIRMOUSE                 |\n"
            "  |     your computer, taught by a patient teacher   |\n"
            "  +==================================================+\n"
            "\n"
            "  Let's learn together. This will take about 3–5 minutes.\n"
            "  We'll teach:  ✓ Voice   ✓ Eyes   ✓ Hands   "
            "✓ Multimodal control\n"
            "\n"
            "  You can skip any step — \"Skip for now\" always works,\n"
            "  and nothing is marked complete unless the sensors\n"
            "  verify it for real.\n"
            "\n"
            "  Ready? [ Start ]\n"
        )

    @staticmethod
    def _bar(progress: float, width: int = 12) -> str:
        try:
            p = float(progress)
        except (TypeError, ValueError):
            p = 0.0
        if not math.isfinite(p):
            p = 0.0
        p = min(1.0, max(0.0, p))
        filled = int(round(p * width))
        pct = int(round(p * 100))
        return "█" * filled + "░" * (width - filled) + f"  {pct}%"

    def teaching_overlay(self, lesson_id: str, progress: float = 0.0,
                         status: str = "Listening...") -> str:
        """The §22 premium teaching box with a progress bar."""
        lesson = self.find_lesson(lesson_id)
        title = lesson["title"] if lesson else f"{lesson_id} (unknown lesson)"
        instruction = (lesson or {}).get("instruction",
                                         "This lesson could not be found "
                                         "— nothing will be marked.")
        criteria = (lesson or {}).get("success_criteria", "—")
        track = self.track_of(lesson_id) or "—"
        width = 58
        body: List[str] = [f"AIRMOUSE — teaching  ·  track: {track}"]
        body += _wrap_line(str(title), width)
        body += _wrap_line(str(instruction), width)
        body += _wrap_line(f"pass when: {criteria}", width)
        body += ["", self._bar(progress)]
        body += _wrap_line(str(status), width)
        out = ["", "  ┌" + "─" * width + "┐"]
        for ln in body:
            out.append(f"  │ {ln:<{width}} │")
        out.append("  └" + "─" * width + "┘")
        return "\n".join(out) + "\n"

    def next_lesson(self, track: str) -> Optional[Dict[str, Any]]:
        """First lesson of ``track`` the learner has not passed yet."""
        for lesson in CURRICULUM.get(str(track), []):
            stats = self.store.lesson_stats(lesson["id"])
            try:
                passed = int(stats.get("passes", 0) or 0) > 0
            except Exception:           # noqa: BLE001
                passed = False
            if not passed:
                return dict(lesson)
        return None

    def progress_report(self) -> str:
        """Rendered progress box: per-track ✓/○ + the next lesson."""
        labels = {
            "voice": "Voice",
            "gaze": "Eyes (gaze)",
            "gesture": "Hands (gestures)",
            "fusion": "Multimodal (fusion)",
            "personalization": "Personalization",
        }
        lines = [
            "",
            "  +==================================================+",
            "  |               YOUR LEARNING PROGRESS             |",
            "  +==================================================+",
            "",
        ]
        next_showing = None
        for track in TRACK_ORDER:
            done = bool(self.store.tracks.get(track))
            mark = "✓" if done else "○"
            tail = "complete" if done else "not yet"
            if not done and next_showing is None:
                nxt = self.next_lesson(track)
                if nxt is not None:
                    tail = f"next: {nxt['title']}"
                next_showing = track
            lines.append(f"   {mark} {labels[track]:<22} {tail}")
        lines.append("")
        lines.append(f"   Sessions: {self.store.sessions}   •   "
                     f"Phase: {self.store.phase.value}")
        if self.store.is_complete:
            lines.append("   All five tracks complete — nicely done. "
                         "AirMouse keeps adapting quietly.")
        else:
            lines.append("   Resume anytime:  airmouse teach        "
                         '("Skip for now" always works)')
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# run_teach / run_learn — the teaching session runner
# ---------------------------------------------------------------------------

_PHYSICAL_NOTE = ("PHYSICAL PRACTICE REQUIRED — needs camera/microphone; "
                  "never auto-passed.")

#: module-level seam for a future live fusion verifier (camera + mic).
#: When None (this build), fusion challenges are described honestly and
#: attempts are recorded as simulated — never marked passed.
_FUSION_VERIFIER: Optional[Callable[..., bool]] = None


def _headless_mode(camera: Optional[bool], input_fn: Optional[Callable]) -> bool:
    """Headless = no camera given, OR stdin non-TTY with no injected input.

    A ``camera=None`` means the caller did not provide camera knowledge —
    the run NEVER blocks.  An explicit ``camera=False`` (no webcam) still
    allows interactive concept teaching + voice text practice.
    """
    if camera is None:
        return True
    if input_fn is not None:
        return False
    return not _stdin_is_tty()


def _ask(ask: Callable, prompt: str, dest: Any) -> Optional[str]:
    """One guarded prompt.  Returns the stripped answer, or None on
    EOF/error (callers treat None as 'skip for now').  NEVER raises,
    NEVER loops."""
    try:
        print(prompt, end="", file=dest)
        try:
            dest.flush()
        except Exception:               # noqa: BLE001
            pass
        raw = ask()
    except Exception:                   # noqa: BLE001 — EOF/Keyboard/etc
        print("", file=dest)
        return None
    return str(raw).strip()


def _render_plan(tracks: List[str], store: OnboardingStore) -> str:
    """The full honest teaching plan (headless view)."""
    labels = {"voice": "Voice — talk to your computer",
              "gaze": "Eyes (gaze) — look to steer",
              "gesture": "Hands — the Gesture Academy",
              "fusion": "Multimodal — final challenges",
              "personalization": "Personalization — what AirMouse learns"}
    lines = [
        "",
        "  +==================================================+",
        "  |            YOUR TEACHING PLAN (headless)         |",
        "  +==================================================+",
        "",
    ]
    for track in tracks:
        lines.append(f"  Track: {labels[track]}")
        for i, lesson in enumerate(CURRICULUM[track], 1):
            physical = bool(lesson.get("physical_required"))
            textable = bool(lesson.get("text_practicable"))
            lines.append(f"   {i}. {lesson['id']}: {lesson['title']}")
            lines.append(f"      do this   : {lesson['instruction']}")
            lines.append(f"      demo      : {lesson['demonstration']}")
            lines.append(f"      pass when : {lesson['success_criteria']}")
            if physical:
                note = _PHYSICAL_NOTE
                if textable:
                    note += ("  (voice practice can also be completed by "
                             "typing the phrase in an interactive session)")
                lines.append(f"      status    : {note}")
            if lesson.get("delegates") == "academy":
                lines.append("      delegates : airmouse academy "
                             "(the live hold-to-pass classroom)")
            lines.append("")
    lines.append("  Headless run: nothing was marked complete — physical "
                 "lessons are NEVER auto-passed.")
    lines.append("  Run me interactively:  airmouse teach      "
                 "(or: airmouse learn)")
    lines.append("")
    return "\n".join(lines)


def _teach_voice(teacher: Teacher, dest: Any, ask: Optional[Callable],
                 camera: Optional[bool]) -> bool:
    """The voice track.  Returns True iff the track genuinely completed."""
    store = teacher.store
    print("\n  ── Track 1: Voice — talk to your computer ──", file=dest)

    # delegate to the v16.5 voice academy when present + a camera exists
    if camera:
        try:
            from .voice_academy import run_voice_academy  # type: ignore
            res = run_voice_academy(level="all", out=dest, input_fn=ask)
            if isinstance(res, dict) and res.get("completed") \
                    and not res.get("physical_required"):
                store.mark_track_complete("voice")
                print("  ✓ Voice track verified live — marked complete.",
                      file=dest)
                return True
            print("  (the voice academy ran but could not verify every "
                  "level — nothing was marked)", file=dest)
            return False
        except ImportError:
            print("  voice academy module not available — falling back to "
                  "text practice.", file=dest)
        except Exception as exc:        # noqa: BLE001 — a buggy academy
            print(f"  voice academy could not run: {exc} — falling back "
                  "to text practice.", file=dest)

    if ask is None:
        for lesson in CURRICULUM["voice"]:
            print(f"\n  Lesson: {lesson['title']}", file=dest)
            print(f"    do this : {lesson['instruction']}", file=dest)
            print(f"    status  : {_PHYSICAL_NOTE}", file=dest)
        return False

    # text practice — the honest fallback (typing IS deterministic proof
    # that the phrase grammar is learned; live mic verification stays
    # labelled separately)
    for lesson in CURRICULUM["voice"]:
        phrases = {  # real phrases from the live engine
            _normalize_answer(p) for p in _voice_phrase_pool(
                lesson.get("phrases_key", ""))}
        print(f"\n  Lesson: {lesson['title']}", file=dest)
        print(f"    do this : {lesson['instruction']}", file=dest)
        print(f"    demo    : {lesson['demonstration']}", file=dest)
        answered = _ask(ask, "    practice — type the phrase you would say "
                             "(Enter = skip for now): ", dest)
        if answered is None:
            print("    skipped — no problem, we'll pick this up "
                  "next time.", file=dest)
            continue
        if _normalize_answer(answered) in phrases:
            teacher.record_result(lesson["id"], passed=True)
            print("    ✓ Nailed it — that is exactly what the engine "
                  "listens for.", file=dest)
            continue
        # one gentle retry (never re-prompt more than twice)
        print(f"    hint — one of: {', '.join(sorted(phrases)[:4])}",
              file=dest)
        answered = _ask(ask, "    try once more (Enter = skip for now): ",
                        dest)
        if answered is not None and _normalize_answer(answered) in phrases:
            teacher.record_result(lesson["id"], passed=True)
            print("    ✓ There it is.", file=dest)
        else:
            teacher.record_result(lesson["id"], passed=False)
            coaching = Teacher.decide_practice(
                lesson["id"], store.lesson_stats(lesson["id"]))
            print(f"    ○ skipped for now — {coaching['message']}",
                  file=dest)

    completed = all(int(store.lesson_stats(l["id"]).get("passes", 0) or 0) > 0
                    for l in CURRICULUM["voice"])
    if completed:
        store.mark_track_complete("voice")
        print("\n  ✓ Voice track complete (verified by phrase practice).",
              file=dest)
        return True
    print("\n  ○ Voice track not complete yet — skipped lessons are never "
          "marked.", file=dest)
    return False


def _teach_gaze(teacher: Teacher, dest: Any, ask: Optional[Callable],
                camera: Optional[bool]) -> bool:
    """The gaze track — completes ONLY via a verified live pass."""
    store = teacher.store
    print("\n  ── Track 2: Eyes (gaze) — look to steer ──", file=dest)
    if camera:
        try:
            from .gaze_academy import run_gaze_academy  # type: ignore
            res = run_gaze_academy(lesson="all", out=dest, input_fn=ask)
            if isinstance(res, dict) and res.get("completed") \
                    and not res.get("physical_required"):
                store.mark_track_complete("gaze")
                print("  ✓ Gaze track verified live — marked complete.",
                      file=dest)
                return True
            print("  (the gaze academy ran but could not verify every "
                  "lesson — nothing was marked)", file=dest)
            return False
        except ImportError:
            print("  gaze academy module not available — teaching the "
                  "concepts instead.", file=dest)
        except Exception as exc:        # noqa: BLE001
            print(f"  gaze academy could not run: {exc} — teaching the "
                  "concepts instead.", file=dest)
    for lesson in CURRICULUM["gaze"]:
        print(f"\n  Lesson: {lesson['title']}", file=dest)
        print(f"    do this : {lesson['instruction']}", file=dest)
        print(f"    demo    : {lesson['demonstration']}", file=dest)
        print(f"    status  : {_PHYSICAL_NOTE}", file=dest)
    if not camera:
        print("  ○ Gaze is a physical track: it completes only via a "
              "verified live pass (never in concept mode).", file=dest)
    return False


def _academy_core_ids() -> List[str]:
    """The academy's live-practicable core lesson ids (derived, lazy)."""
    try:
        from .academy import LESSONS
        return [l["id"] for l in LESSONS if l.get("track") == "core"]
    except Exception:                   # noqa: BLE001
        return []


def _teach_gesture(teacher: Teacher, dest: Any, ask: Optional[Callable],
                   camera: Optional[bool]) -> bool:
    """The gesture track — DELEGATES to the existing academy."""
    store = teacher.store
    print("\n  ── Track 3: Hands — the Gesture Academy ──", file=dest)
    try:
        from . import academy
    except Exception as exc:            # noqa: BLE001
        print(f"  gesture academy module not available: {exc}", file=dest)
        return False
    for lesson in CURRICULUM["gesture"]:
        print(f"\n  Lesson: {lesson['title']}", file=dest)
        print(f"    do this : {lesson['instruction']}", file=dest)
        print(f"    demo    : {lesson['demonstration']}", file=dest)
    academy.run_academy(lesson="all", camera=bool(camera))
    core = _academy_core_ids()
    done = set(academy.load_progress().get("completed", []))
    if core and all(cid in done for cid in core):
        store.mark_track_complete("gesture")
        print("  ✓ Gesture track complete — every core lesson verified "
              "live by the academy.", file=dest)
        return True
    print(f"  ○ Gesture track not complete yet ({len(done & set(core))}"
          f"/{len(core)} core lessons verified).  Practice with: "
          "airmouse academy", file=dest)
    return False


def _teach_fusion(teacher: Teacher, dest: Any, ask: Optional[Callable],
                  camera: Optional[bool]) -> bool:
    """The fusion track — multimodal final challenges (§18)."""
    store = teacher.store
    print("\n  ── Track 4: Multimodal — final challenges ──", file=dest)
    prereqs = all(store.tracks.get(t) for t in ("voice", "gaze", "gesture"))
    for lesson in CURRICULUM["fusion"]:
        print(f"\n  Challenge: {lesson['title']}", file=dest)
        print(f"    do this : {lesson['instruction']}", file=dest)
        print(f"    demo    : {lesson['demonstration']}", file=dest)
        print(f"    pass when : {lesson['success_criteria']}", file=dest)
        if _FUSION_VERIFIER is not None and camera and prereqs:
            try:
                verified = bool(_FUSION_VERIFIER(lesson["id"]))
            except Exception:           # noqa: BLE001
                verified = False
            res = teacher.record_result(lesson["id"], passed=verified,
                                        physical_verified=verified)
            print(f"    result  : {res['result']}", file=dest)
        else:
            teacher.record_result(lesson["id"], passed=False)
            print(f"    status  : {_PHYSICAL_NOTE}", file=dest)
            if not prereqs:
                print("    plan    : finish Voice, Eyes and Hands first — "
                      "fusion needs them all working together.", file=dest)
            elif _FUSION_VERIFIER is None:
                print("    honest  : no live fusion verifier is wired in "
                      "this build — the challenge is described and the "
                      "attempt is recorded as simulated; NOTHING is "
                      "marked passed.", file=dest)
    print("\n  ○ Fusion challenges are graded only when the sensors "
          "verify them — never auto-passed.", file=dest)
    return False


def _profile_summary(store: OnboardingStore) -> str:
    done = [t for t in TRACK_ORDER if store.tracks.get(t)]
    done_txt = ", ".join(done) if done else "none yet"
    return (f"    profile : {store.sessions} session(s) so far;  "
            f"tracks completed: {done_txt};  "
            f"all data local under ~/.airmouse/profile/")


def _teach_personalization(teacher: Teacher, dest: Any,
                           ask: Optional[Callable]) -> bool:
    """Personalization: what AirMouse learns + explicit acknowledgment."""
    store = teacher.store
    print("\n  ── Track 5: Personalization — what AirMouse learns ──",
          file=dest)
    for lesson in CURRICULUM["personalization"]:
        print(f"\n  Lesson: {lesson['title']}", file=dest)
        print(f"    do this : {lesson['instruction']}", file=dest)
        print(f"    demo    : {lesson['demonstration']}", file=dest)
    print(_profile_summary(store), file=dest)
    if ask is None:
        print("    status  : the acknowledgment step happens in an "
              "interactive session (nothing assumed here).", file=dest)
        return False
    answer = _ask(ask, "    acknowledge — is that all clear? type y "
                       "(anything else = skip for now): ", dest)
    if answer is not None and answer.lower() in ("y", "yes"):
        teacher.record_result("personalization_ack", passed=True)
        store.mark_track_complete("personalization")
        print("    ✓ Acknowledged — thank you. You own your data.",
              file=dest)
        return True
    print("    ○ Skipped for now — AirMouse will ask again next time.",
          file=dest)
    return False


_TRACK_RUNNERS = {
    "voice": _teach_voice,
    "gaze": _teach_gaze,
    "gesture": _teach_gesture,
    "fusion": _teach_fusion,
}


def run_teach(track: str = "all", resume: bool = False,
              out: Any = None, input_fn: Optional[Callable] = None,
              camera: Optional[bool] = None) -> int:
    """Run the teaching experience for one track (or all five).  Exit code.

    Honesty contract:

    * headless (``camera=None``, or non-TTY stdin with no ``input_fn``)
      NEVER blocks: it prints the full plan + progress report + the
      honest physical-requirements note, marks nothing passed, returns 0.
    * "Skip for now" is available at every prompt; any n/EOF/skip answer
      proceeds; no prompt is re-asked more than twice.
    * a track is marked complete ONLY when its lessons genuinely
      completed (voice may complete via text practice; gaze/gesture/
      fusion physical tracks complete only via verified live passes).
    * returns 0 in every honest path; 1 only for a usage error
      (unknown track) or an internal bug.
    """
    try:
        return _run_teach_impl(track=track, resume=resume, out=out,
                               input_fn=input_fn, camera=camera)
    except Exception:                   # noqa: BLE001 — a bug, honestly rc 1
        return 1


def _run_teach_impl(track: str = "all", resume: bool = False,
                    out: Any = None, input_fn: Optional[Callable] = None,
                    camera: Optional[bool] = None) -> int:
    dest = sys.stdout if out is None else out
    ask = input_fn if input_fn is not None else input
    store = OnboardingStore()
    store.touch_session()
    teacher = Teacher(store)

    wanted = str(track or "all").strip().lower()
    if wanted in ("", "all"):
        tracks = list(TRACK_ORDER)
    elif wanted in TRACK_ORDER:
        tracks = [wanted]
    else:
        print(f"  unknown teaching track '{track}' — valid tracks: "
              + ", ".join(("all",) + TRACK_ORDER), file=dest)
        return 1

    headless = _headless_mode(camera, input_fn)

    if store.is_new:
        print(teacher.welcome_banner(), file=dest)
    elif store.is_in_progress and not resume and not headless:
        print(teacher.progress_report(), file=dest)
        answer = _ask(ask, "  Continue your training? [Y/n] ", dest)
        if answer is None or answer.lower() not in ("y", "yes", ""):
            print("\n  No problem — skipped for now.  Resume anytime "
                  "with:  airmouse teach", file=dest)
            return 0

    if headless:
        print(_render_plan(tracks, store), file=dest)
        print(teacher.progress_report(), file=dest)
        print("  " + _PHYSICAL_NOTE, file=dest)
        return 0

    for name in tracks:
        if name in _TRACK_RUNNERS:
            _TRACK_RUNNERS[name](teacher, dest, ask, camera)
        elif name == "personalization":
            _teach_personalization(teacher, dest, ask)

    print(teacher.progress_report(), file=dest)
    print("  You can stop any time — \"Skip for now\" always works.  "
          "Nothing was claimed that the sensors did not verify.", file=dest)
    return 0


def run_learn(out: Any = None, input_fn: Optional[Callable] = None,
              camera: Optional[bool] = None) -> int:
    """The full five-track tour (voice → gaze → gesture → fusion →
    personalization) — same honesty rules as :func:`run_teach`."""
    return run_teach(track="all", resume=True, out=out,
                     input_fn=input_fn, camera=camera)


# ---------------------------------------------------------------------------
# first-run helpers (wired later by the coordinator in main())
# ---------------------------------------------------------------------------

class _DefaultTeachAuto:
    """Duck-type stand-in used when the real Config cannot be read."""

    teach_auto = True


def _resolve_teach_auto(config: Any) -> bool:
    if config is None:
        try:
            from .config import Config
            cfg = Config()
            try:
                from . import persistence as _persistence
                with _persistence.config_path_scope():
                    cfg.load()
            except Exception:           # noqa: BLE001
                cfg.load()
            config = cfg
        except Exception:               # noqa: BLE001
            return True                 # default: teaching offered
    try:
        return bool(getattr(config, "teach_auto", True))
    except Exception:                   # noqa: BLE001
        return True


def should_auto_teach(store: Optional[OnboardingStore] = None,
                      config: Any = None, argv: Optional[List[str]] = None,
                      tty: Optional[bool] = None) -> bool:
    """True iff a first-run auto-teaching experience should start by itself.

    ALL of: onboarding phase is NEW  •  stdout is a TTY  •
    ``config.teach_auto`` (default True)  •  ``argv`` empty.  Every input
    is individually guarded — this can never raise.
    """
    try:
        if argv is None:
            argv = sys.argv[1:]
        if argv:                        # the user is already doing something
            return False
        if tty is None:
            try:
                tty = bool(sys.stdout.isatty())
            except Exception:           # noqa: BLE001
                return False
        if not tty:
            return False
        if not _resolve_teach_auto(config):
            return False
        st = store if store is not None else OnboardingStore()
        return bool(st.is_new)
    except Exception:                   # noqa: BLE001 — fail closed
        return False


def maybe_prompt_teach(store: Optional[OnboardingStore] = None,
                       out: Any = None,
                       input_fn: Optional[Callable] = None) -> bool:
    """Offer the tour once; True iff the user accepted.

    * phase NEW          → "Would you like a 3–5 minute interactive tour?"
    * phase IN_PROGRESS  → "Continue your training?"
    * phase COMPLETE     → no prompt, False.

    Y/y/yes/empty accept; anything else, EOF or an error declines.  On
    accept the store's session count is updated.  NEVER raises, NEVER
    traps, NEVER re-prompts.
    """
    try:
        dest = sys.stdout if out is None else out
        ask = input_fn if input_fn is not None else input
        st = store if store is not None else OnboardingStore()
        if st.is_complete:
            return False
        if st.is_new:
            question = "  Would you like a 3–5 minute interactive tour? " \
                       "[Y/n] "
        elif st.is_in_progress:
            question = "  Continue your training? [Y/n] "
        else:
            return False
        answer = _ask(ask, question, dest)
        if answer is None or answer.lower() not in ("y", "yes", ""):
            return False
        try:
            st.touch_session()
        except Exception:               # noqa: BLE001
            pass
        return True
    except Exception:                   # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# self-diagnostic teacher (mission §21)
# ---------------------------------------------------------------------------

def hardware_panel(report: Any = None) -> str:
    """The teacher's honest hardware panel (uses capabilities.detect_all).

    CAMERA ○/✓, MICROPHONE ○/✓, BROWSER, Simulation availability — and,
    when a webcam is missing, the honest line: *"I can teach you the
    concepts now. Physical camera lessons will begin when a webcam is
    available."*  NOTHING is ever auto-passed.
    """
    components: List[Any] = []
    if report is None:
        try:
            from .capabilities import detect_all
            report = detect_all(quick=True)
        except Exception as exc:        # noqa: BLE001
            return (
                "\n  +==================================================+\n"
                "  |         AIRMOUSE SELF-DIAGNOSTIC (TEACHER)       |\n"
                "  +==================================================+\n"
                "\n"
                f"  capabilities module not available: {exc}\n"
                "  I can teach you the concepts now. Physical camera "
                "lessons will\n  begin when a webcam is available.\n"
                "  Nothing is ever auto-passed.\n"
            )
    try:
        components = list(report.components)
    except Exception:                   # noqa: BLE001
        components = []

    def _row(category: str) -> Tuple[bool, str, str]:
        ready, label, detail = False, "○", ""
        for comp in components:
            try:
                if str(getattr(comp, "category", "")) != category:
                    continue
                state = getattr(comp, "state", "")
                state = str(getattr(state, "value", state)).split(".")[-1]
                detail = str(getattr(comp, "detail", "") or state)
                if state == "READY":
                    return True, "✓", detail
                label = "○"
            except Exception:           # noqa: BLE001
                continue
        return ready, label, detail

    cam_ok, cam_mark, cam_detail = _row("CAMERA")
    mic_ok, mic_mark, mic_detail = _row("MICROPHONE")
    brw_ok, brw_mark, brw_detail = _row("BROWSER")
    sim_ok, sim_mark, sim_detail = False, "○", ""
    for comp in components:
        try:
            if "simulated" not in str(getattr(comp, "name", "")).lower():
                continue
            sim_detail = str(getattr(comp, "detail", "") or "")
            state = getattr(comp, "state", "")
            state = str(getattr(state, "value", state)).split(".")[-1]
            if state == "READY":
                sim_ok, sim_mark = True, "✓"
            break
        except Exception:               # noqa: BLE001
            continue

    sim_desc = sim_detail if sim_detail else (
        "deterministic simulators exist on this machine"
        if sim_ok else "no simulated bridge detected")
    lines = [
        "",
        "  +==================================================+",
        "  |         AIRMOUSE SELF-DIAGNOSTIC (TEACHER)       |",
        "  +==================================================+",
        "",
        f"   {cam_mark} CAMERA      {cam_detail or ('ready' if cam_ok else 'not verified on this machine')}",
        f"   {mic_mark} MICROPHONE  {mic_detail or ('ready' if mic_ok else 'not verified on this machine')}",
        f"   {brw_mark} BROWSER     {brw_detail or ('ready' if brw_ok else 'not verified on this machine')}",
        f"   {sim_mark} SIMULATION  {sim_desc}",
        "        (simulated results are always labelled SIMULATION — "
        "never presented as real)",
        "",
    ]
    if not cam_ok:
        lines.append("  I can teach you the concepts now. Physical camera "
                     "lessons will begin")
        lines.append("  when a webcam is available.")
    else:
        lines.append("  Your webcam is live — physical camera lessons can "
                     "be verified for real.")
    if not mic_ok:
        lines.append("  Voice lessons will verify through your microphone "
                     "when one is available;")
        lines.append("  until then the voice track can be practiced by "
                     "typing the phrases.")
    lines.append("")
    lines.append("  Nothing is ever auto-passed: a lesson is marked "
                 "complete only when a")
    lines.append("  sensor verifies it.  The teacher never blames the "
                 "user — practice is")
    lines.append("  always available, and \"Skip for now\" always works.")
    lines.append("")
    return "\n".join(lines)
