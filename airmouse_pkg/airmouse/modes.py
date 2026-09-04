"""
airmouse.modes — interaction modes (v11.5 §17–§23).

    --mode teacher     presentation control + classroom transcription +
                       lecture timeline + teacher shortcuts
    --mode student     research navigation, notes, dictation, study timer,
                       bookmarks, source capture, searchable notes
    --mode office      meeting control, dictation, window management,
                       task capture, workflow shortcuts
    --mode meeting     transcription + structured output (timeline,
                       important moments, action items, decisions,
                       questions) — honest: no speaker-ID claims
    --mode research    QUESTION→SEARCH→SOURCE→READ→CAPTURE→ANNOTATE→
                       ORGANIZE assistance (never fabricates results)
    --mode developer   terminal/editor/tab shortcuts + technical vocab
    accessibility      profiles with modality fallback chains — no
                       single sensor is a mandatory point of failure

Teacher presentation control uses GENERIC keyboard bindings so it works
with any presentation software (PowerPoint, Keynote, LibreOffice,
Google Slides in a browser, PDF viewers) — no proprietary app required.

All controllers are local, offline, bounded and export clean artifacts.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

MAX_MARKERS = 500
MAX_NOTES = 300
MAX_NOTE_LEN = 2_000
MAX_ACTION_ITEMS = 200
MAX_SOURCES = 200
MAX_TIMELINE_ENTRIES = 500


# ─────────────────────────────────────────────────────────────────────────────
# presentation control (teacher §17) — generic hotkeys, any presentation app
# ─────────────────────────────────────────────────────────────────────────────

PRESENTATION_KEYS: Dict[str, Tuple[str, ...]] = {
    "next_slide": ("right",),
    "previous_slide": ("left",),
    "start_presentation": ("f5",),
    "exit_presentation": ("esc",),
    "pause": ("b",),          # blank screen (standard presenter key)
    "black_screen": ("b",),
    "white_screen": ("w",),
    "first_slide": ("home",),
    "last_slide": ("end",),
    "pointer": ("dot",),       # some apps: ctrl+p / dot — app dependent
    "highlight": ("ctrl", "l"),
}


@dataclass
class TimelineEntry:
    timestamp: float
    kind: str                    # topic | important | note | action_item | decision | question
    text: str
    slide: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"timestamp": round(self.timestamp, 3), "kind": self.kind,
                "text": self.text[:MAX_NOTE_LEN], "slide": self.slide}


class KeyDispatcher:
    """Pluggable key dispatch (pynput in production; recorded in tests)."""

    def __init__(self, backend: Optional[Any] = None) -> None:
        self.backend = backend
        self.sent: List[Tuple[str, ...]] = []

    def press(self, keys: Tuple[str, ...]) -> bool:
        self.sent.append(keys)
        if self.backend is not None:
            try:
                return bool(self.backend.key_press(keys[-1])
                            if len(keys) == 1
                            else self.backend.hotkey(list(keys)))
            except Exception:
                return False
        return True


class PresentationController:
    """Works with EXISTING presentation software via generic keys (§17)."""

    def __init__(self, dispatcher: Optional[KeyDispatcher] = None) -> None:
        self.dispatcher = dispatcher or KeyDispatcher()
        self.enabled = True
        self.slide = 0
        self.presenting = False

    def control(self, command: str) -> bool:
        cmd = str(command or "").strip().lower().replace(" ", "_")
        keys = PRESENTATION_KEYS.get(cmd)
        if not keys or not self.enabled:
            return False
        ok = self.dispatcher.press(keys)
        if cmd == "next_slide":
            self.slide += 1
        elif cmd == "previous_slide":
            self.slide = max(0, self.slide - 1)
        elif cmd == "start_presentation":
            self.presenting = True
        elif cmd == "exit_presentation":
            self.presenting = False
        return ok

    def jump_to_slide(self, n: int) -> bool:
        """Jump by relative navigation (app-independent best effort)."""
        if n < 0:
            return False
        while self.slide < n:
            if not self.control("next_slide"):
                return False
        return True


# ─────────────────────────────────────────────────────────────────────────────
# timeline / notes / meeting / study primitives
# ─────────────────────────────────────────────────────────────────────────────

class TimelineSession:
    """Timestamped topic/important/note timeline with export (§17/§21)."""

    KINDS = ("topic", "important", "note", "action_item", "decision",
             "question", "bookmark")

    def __init__(self, title: str = "session") -> None:
        self.title = str(title)[:80]
        self.active = False
        self.paused = False
        self.started_at = 0.0
        self.paused_total = 0.0
        self._pause_started = 0.0
        self.entries: List[TimelineEntry] = []

    def start(self, now: Optional[float] = None) -> bool:
        if self.active:
            return False
        self.active = True
        self.paused = False
        self.started_at = float(now if now is not None else time.time())
        self.paused_total = 0.0
        return True

    def pause(self, now: Optional[float] = None) -> bool:
        if self.active and not self.paused:
            self.paused = True
            self._pause_started = float(now if now is not None else time.time())
            return True
        return False

    def resume(self, now: Optional[float] = None) -> bool:
        if self.active and self.paused:
            self.paused = False
            self.paused_total += (float(now if now is not None else time.time())
                                  - self._pause_started)
            return True
        return False

    def elapsed(self, now: Optional[float] = None) -> float:
        if not self.active:
            return 0.0
        now_v = float(now if now is not None else time.time())
        e = now_v - self.started_at - self.paused_total
        if self.paused:
            e -= now_v - self._pause_started
        return max(0.0, e)

    def mark(self, kind: str, text: str,
             now: Optional[float] = None) -> Optional[TimelineEntry]:
        if kind not in self.KINDS or not self.active:
            return None
        if len(self.entries) >= MAX_TIMELINE_ENTRIES:
            self.entries = self.entries[50:]
        e = TimelineEntry(timestamp=self.elapsed(now), kind=kind,
                          text=str(text or "")[:MAX_NOTE_LEN])
        self.entries.append(e)
        return e

    def by_kind(self, kind: str) -> List[TimelineEntry]:
        return [e for e in self.entries if e.kind == kind]

    def export_data(self) -> Dict[str, Any]:
        return {"version": 1, "kind": "airmouse-timeline",
                "title": self.title, "elapsed": round(self.elapsed(), 3),
                "entries": [e.to_dict() for e in self.entries]}

    def export_markdown(self) -> str:
        lines = [f"# {self.title}", "",
                 f"Duration: {self.elapsed():.0f}s", ""]
        for e in self.entries:
            mm, ss = divmod(int(e.timestamp), 60)
            lines.append(f"- **[{mm:02d}:{ss:02d}]** {e.kind}: {e.text}")
        return "\n".join(lines)

    def export_json(self, path: str) -> int:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        payload = json.dumps(self.export_data(), ensure_ascii=False,
                             sort_keys=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)
        return len(payload)

    def export_md(self, path: str) -> int:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        payload = self.export_markdown()
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)
        return len(payload)


# ─────────────────────────────────────────────────────────────────────────────
# study timer + notes + sources (student §18, research §19)
# ─────────────────────────────────────────────────────────────────────────────

class StudyTimer:
    """Bounded pomodoro-style study timer (state machine, no threads)."""

    def __init__(self, focus_minutes: float = 25.0,
                 break_minutes: float = 5.0) -> None:
        self.focus_minutes = max(1.0, float(focus_minutes))
        self.break_minutes = max(1.0, float(break_minutes))
        self.running = False
        self.on_break = False
        self._started = 0.0
        self.completed_focus_blocks = 0

    def start(self, now: Optional[float] = None) -> bool:
        if self.running:
            return False
        self.running = True
        self.on_break = False
        self._started = float(now if now is not None else time.time())
        return True

    def stop(self, now: Optional[float] = None) -> Dict[str, Any]:
        if not self.running:
            return {"was_running": False}
        now_v = float(now if now is not None else time.time())
        elapsed = (now_v - self._started) / 60.0
        self.running = False
        info = {"was_running": True, "minutes": round(elapsed, 1),
                "on_break": self.on_break,
                "completed": self.completed_focus_blocks}
        if not self.on_break and elapsed >= self.focus_minutes:
            self.completed_focus_blocks += 1
        return info

    def check(self, now: Optional[float] = None) -> Optional[str]:
        """Returns 'focus_done' | 'break_done' | None (state machine)."""
        if not self.running:
            return None
        now_v = float(now if now is not None else time.time())
        elapsed = (now_v - self._started) / 60.0
        if not self.on_break and elapsed >= self.focus_minutes:
            self.on_break = True
            self._started = now_v
            self.completed_focus_blocks += 1
            return "focus_done"
        if self.on_break and elapsed >= self.break_minutes:
            self.on_break = False
            self._started = now_v
            return "break_done"
        return None


class NotesStore:
    """Searchable notes with bookmarks + annotations (§18)."""

    def __init__(self) -> None:
        self.notes: List[Dict[str, Any]] = []

    def add(self, text: str, tags: Tuple[str, ...] = (),
            important: bool = False,
            now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        t = str(text or "").strip()[:MAX_NOTE_LEN]
        if not t or len(self.notes) >= MAX_NOTES:
            return None
        note = {"id": len(self.notes) + 1,
                "text": t,
                "tags": [str(x)[:24] for x in tags[:6]],
                "important": bool(important),
                "ts": float(now if now is not None else time.time())}
        self.notes.append(note)
        return note

    def search(self, query: str, k: int = 20) -> List[Dict[str, Any]]:
        q = str(query or "").strip().lower()
        if not q:
            return []
        rows = [n for n in self.notes
                if q in n["text"].lower()
                or any(q in tag for tag in n["tags"])]
        rows.sort(key=lambda n: -n["ts"])
        return rows[:max(0, int(k))]

    def important(self) -> List[Dict[str, Any]]:
        return [n for n in self.notes if n["important"]]

    def export_markdown(self) -> str:
        lines = ["# Notes", ""]
        for n in self.notes:
            star = " ⭐" if n["important"] else ""
            tags = " ".join(f"#{t}" for t in n["tags"])
            lines.append(f"- {n['text']}{star} {tags}".rstrip())
        return "\n".join(lines)


@dataclass
class Source:
    title: str
    url: str
    selection: str = ""
    annotation: str = ""
    captured_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title[:160], "url": self.url[:400],
                "selection": self.selection[:MAX_NOTE_LEN],
                "annotation": self.annotation[:MAX_NOTE_LEN],
                "captured_at": self.captured_at}


class SourceCapture:
    """Research source capture (§19) — stores provenance verbatim,
    never alters or fabricates source content."""

    def __init__(self) -> None:
        self.sources: List[Source] = []

    def capture(self, title: str, url: str, selection: str = "") -> Optional[Source]:
        if len(self.sources) >= MAX_SOURCES:
            return None
        u = str(url or "").strip()
        if u and not (u.startswith("http://") or u.startswith("https://")
                      or u.startswith("file://")):
            u = ""      # only sane schemes are recorded
        s = Source(title=str(title or "")[:160], url=u,
                   selection=str(selection or "")[:MAX_NOTE_LEN])
        self.sources.append(s)
        return s

    def annotate(self, index: int, annotation: str) -> bool:
        if 0 <= index < len(self.sources):
            self.sources[index].annotation = str(annotation)[:MAX_NOTE_LEN]
            return True
        return False

    def export_data(self) -> Dict[str, Any]:
        return {"version": 1, "kind": "airmouse-sources",
                "sources": [s.to_dict() for s in self.sources]}


# ─────────────────────────────────────────────────────────────────────────────
# mode controllers
# ─────────────────────────────────────────────────────────────────────────────

class TeacherMode:
    """Presentation control + classroom transcription + timeline (§17)."""

    def __init__(self, dispatcher: Optional[KeyDispatcher] = None) -> None:
        self.presentation = PresentationController(dispatcher)
        self.timeline = TimelineSession("lecture")

    def start_lecture(self, now: Optional[float] = None) -> bool:
        return self.timeline.start(now)

    def pause_lecture(self, now: Optional[float] = None) -> bool:
        return self.timeline.pause(now)

    def resume_lecture(self, now: Optional[float] = None) -> bool:
        return self.timeline.resume(now)

    def mark_important(self, text: str = "", now: Optional[float] = None):
        return self.timeline.mark("important", text, now)

    def add_note(self, text: str, now: Optional[float] = None):
        return self.timeline.mark("note", text, now)

    def export_lecture(self, path: str, fmt: str = "md") -> int:
        if fmt == "json":
            return self.timeline.export_json(path)
        return self.timeline.export_md(path)


class StudentMode:
    """Notes, dictation, study timer, bookmarks, sources (§18)."""

    def __init__(self) -> None:
        self.notes = NotesStore()
        self.timer = StudyTimer()
        self.sources = SourceCapture()
        self.timeline = TimelineSession("study-session")

    def start_study_session(self, now: Optional[float] = None) -> bool:
        self.timeline.start(now)
        return self.timer.start(now)

    def take_note(self, text: str, important: bool = False,
                  now: Optional[float] = None):
        return self.notes.add(text, important=important, now=now)

    def mark_important(self, query: str = "") -> int:
        """Mark recent notes important (all when no query)."""
        if not query:
            n = 0
            for nte in self.notes.notes[-5:]:
                if not nte["important"]:
                    nte["important"] = True
                    n += 1
            return n
        n = 0
        for nte in self.notes.search(query, k=10):
            nte["important"] = True
            n += 1
        return n

    def save_source(self, title: str, url: str, selection: str = ""):
        return self.sources.capture(title, url, selection)


class MeetingMode:
    """Meeting transcription + structured output (§21).

    Honest limitation: speaker separation is NOT claimed.  Markers are
    user-driven; the transcript is whatever the local ASR heard.
    """

    def __init__(self) -> None:
        self.timeline = TimelineSession("meeting")

    def start_meeting(self, now: Optional[float] = None) -> bool:
        return self.timeline.start(now)

    def pause_transcription(self, now: Optional[float] = None) -> bool:
        return self.timeline.pause(now)

    def resume_transcription(self, now: Optional[float] = None) -> bool:
        return self.timeline.resume(now)

    def mark_important(self, text: str = "", now: Optional[float] = None):
        return self.timeline.mark("important", text, now)

    def add_action_item(self, text: str, now: Optional[float] = None):
        return self.timeline.mark("action_item", text, now)

    def add_note(self, text: str, now: Optional[float] = None):
        return self.timeline.mark("note", text, now)

    def bookmark_moment(self, text: str = "", now: Optional[float] = None):
        return self.timeline.mark("bookmark", text, now)

    def add_decision(self, text: str, now: Optional[float] = None):
        return self.timeline.mark("decision", text, now)

    def add_question(self, text: str, now: Optional[float] = None):
        return self.timeline.mark("question", text, now)

    def summary(self) -> Dict[str, Any]:
        return {
            "title": self.timeline.title,
            "elapsed": round(self.timeline.elapsed(), 1),
            "action_items": [e.text for e in self.timeline.by_kind("action_item")],
            "decisions": [e.text for e in self.timeline.by_kind("decision")],
            "questions": [e.text for e in self.timeline.by_kind("question")],
            "important": [e.text for e in self.timeline.by_kind("important")],
            "timeline": [e.to_dict() for e in self.timeline.entries],
        }

    def export_summary(self, path: str, fmt: str = "md") -> int:
        if fmt == "json":
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            payload = json.dumps(self.summary(), ensure_ascii=False,
                                 sort_keys=True, indent=1)
            with open(path, "w", encoding="utf-8") as f:
                f.write(payload)
            return len(payload)
        return self.timeline.export_md(path)


class OfficeMode:
    """Office workflows (§20) — delegates to meeting + dictation + window
    management; integrations stay modular."""

    def __init__(self) -> None:
        self.meeting = MeetingMode()
        self.tasks: List[str] = []

    def capture_task(self, text: str) -> bool:
        t = str(text or "").strip()[:200]
        if t and len(self.tasks) < MAX_ACTION_ITEMS:
            self.tasks.append(t)
            return True
        return False


class ResearchMode:
    """QUESTION → SEARCH → SOURCE → READ → CAPTURE → ANNOTATE → ORGANIZE
    assistance (§19).  Navigation/organization only — NEVER fabricates
    results or alters source content."""

    STAGES = ("question", "search", "source", "read", "capture",
              "annotate", "organize")

    def __init__(self) -> None:
        self.sources = SourceCapture()
        self.notes = NotesStore()
        self.stage = "question"

    def advance(self) -> str:
        idx = self.STAGES.index(self.stage)
        self.stage = self.STAGES[min(idx + 1, len(self.STAGES) - 1)]
        return self.stage

    def organize(self) -> Dict[str, Any]:
        return {"stage": self.stage, "sources": self.sources.export_data(),
                "notes": self.notes.export_markdown()}


# ─────────────────────────────────────────────────────────────────────────────
# accessibility profiles (§22)
# ─────────────────────────────────────────────────────────────────────────────

# modality fallback chains: try in order, first alive wins
ACCESSIBILITY_PROFILES: Dict[str, Tuple[str, ...]] = {
    "hands-free": ("voice", "gaze", "gesture", "keyboard"),
    "voice-first": ("voice", "keyboard", "gesture", "gaze"),
    "gaze-first": ("gaze", "voice", "gesture", "keyboard"),
    "voice+gaze": ("voice", "gaze", "keyboard", "gesture"),
    "gesture+gaze": ("gesture", "gaze", "keyboard", "voice"),
    "camera-free": ("voice", "keyboard", "rf"),
    "low-vision": ("voice", "keyboard", "gaze"),
    "reduced-mobility": ("voice", "gaze", "keyboard"),
}


class AccessibilityProfiles:
    """Modality fallback resolution — the v10 SensorHealth feeds it.

    No single sensor is a mandatory point of failure (§22).
    """

    def __init__(self) -> None:
        self.current = "hands-free"
        self.custom_chains: Dict[str, Tuple[str, ...]] = {}

    def set_profile(self, name: str) -> bool:
        n = str(name or "").strip().lower()
        if n in ACCESSIBILITY_PROFILES or n in self.custom_chains:
            self.current = n
            return True
        return False

    def chain(self, name: Optional[str] = None) -> Tuple[str, ...]:
        n = (name or self.current).strip().lower()
        return self.custom_chains.get(n) or ACCESSIBILITY_PROFILES.get(n, ())

    def resolve(self, health=None, name: Optional[str] = None) -> Tuple[str, ...]:
        """Return the alive-subset of the chain (first = preferred)."""
        chain = self.chain(name)
        if not chain:
            return ()
        alive = []
        for mod in chain:
            if health is None:
                alive.append(mod)     # no health info: assume available
            elif health.alive(mod):
                alive.append(mod)
        return tuple(alive)

    def set_custom_chain(self, name: str, chain: Sequence[str]) -> bool:
        n = str(name or "").strip().lower()
        mods = tuple(str(m).lower() for m in chain
                     if str(m).lower() in ("voice", "gaze", "gesture",
                                           "keyboard", "rf", "hand",
                                           "browser"))[:6]
        if not n or not mods:
            return False
        self.custom_chains[n] = mods
        return True


# ─────────────────────────────────────────────────────────────────────────────
# developer / creator mode (§23)
# ─────────────────────────────────────────────────────────────────────────────

DEVELOPER_BINDINGS: Dict[str, Tuple[str, ...]] = {
    "next_tab": ("ctrl", "tab"),
    "close_tab": ("ctrl", "w"),
    "new_tab": ("ctrl", "t"),
    "switch_editor": ("alt", "tab"),
    "open_terminal": ("ctrl", "`"),
    "copy_code": ("ctrl", "c"),
    "paste_code": ("ctrl", "v"),
    "search_documentation": ("ctrl", "f"),
    "run_command": ("f5",),
    "save_all": ("ctrl", "shift", "s"),
}


class DeveloperMode:
    def __init__(self, dispatcher: Optional[KeyDispatcher] = None) -> None:
        self.dispatcher = dispatcher or KeyDispatcher()
        self.enabled = True

    def control(self, command: str) -> bool:
        cmd = str(command or "").strip().lower().replace(" ", "_")
        keys = DEVELOPER_BINDINGS.get(cmd)
        if not keys or not self.enabled:
            return False
        return self.dispatcher.press(keys)


# ─────────────────────────────────────────────────────────────────────────────
# mode registry (mode phrase commands resolve deterministically)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModeDefinition:
    id: str
    title: str
    phrases: Dict[str, str] = field(default_factory=dict)   # utterance → action


MODE_REGISTRY: Dict[str, ModeDefinition] = {
    "teacher": ModeDefinition("teacher", "Teacher", {
        "start lecture": "lecture_start",
        "pause lecture": "lecture_pause",
        "resume lecture": "lecture_resume",
        "mark important": "mark_important",
        "add note": "add_note",
        "export transcript": "export_lecture",
        "start presentation": "presentation_start",
        "next slide": "next_slide",
        "previous slide": "previous_slide",
        "black screen": "black_screen",
        "white screen": "white_screen",
    }),
    "student": ModeDefinition("student", "Student", {
        "start study session": "study_start",
        "take a note": "note_add",
        "mark this important": "mark_important",
        "save this source": "source_capture",
        "next page": "presentation_next",
        "previous page": "presentation_previous",
    }),
    "office": ModeDefinition("office", "Office", {
        "start meeting": "meeting_start",
        "stop meeting": "meeting_stop",
        "capture task": "task_capture",
    }),
    "meeting": ModeDefinition("meeting", "Meeting", {
        "start transcription": "meeting_start",
        "pause transcription": "meeting_pause",
        "resume transcription": "meeting_resume",
        "mark important": "mark_important",
        "add action item": "add_action_item",
        "add note": "add_note",
        "bookmark moment": "bookmark",
        "export transcript": "export_summary",
    }),
    "research": ModeDefinition("research", "Research", {
        "save this source": "source_capture",
        "take a note": "note_add",
        "highlight this": "note_important",
        "next page": "presentation_next",
        "previous page": "presentation_previous",
    }),
    "developer": ModeDefinition("developer", "Developer", {
        "open terminal": "open_terminal",
        "next tab": "next_tab",
        "close tab": "close_tab",
        "switch editor": "switch_editor",
        "copy code": "copy_code",
        "paste code": "paste_code",
        "run command": "run_command",
    }),
}


class ModeController:
    """Wires a named mode's phrases to its controller (deterministic)."""

    def __init__(self, mode_id: str,
                 dispatcher: Optional[KeyDispatcher] = None) -> None:
        mode_id = str(mode_id or "").strip().lower()
        self.mode_id = mode_id
        self.definition = MODE_REGISTRY.get(mode_id)
        self.teacher = TeacherMode(dispatcher)
        self.student = StudentMode()
        self.office = OfficeMode()
        self.meeting = MeetingMode()
        self.research = ResearchMode()
        self.developer = DeveloperMode(dispatcher)
        self._last_note = ""

    @property
    def available(self) -> bool:
        return self.definition is not None

    def handle(self, utterance: str, now: Optional[float] = None) -> Optional[str]:
        """Handle one mode phrase; returns the action id or None."""
        if not self.available:
            return None
        u = str(utterance or "").strip().lower()
        action = self.definition.phrases.get(u)
        if action is None:
            # parameterized phrases
            for phrase, act in self.definition.phrases.items():
                if u.startswith(phrase + " ") and phrase:
                    action = act
                    u_param = u[len(phrase) + 1:]
                    return self._dispatch(act, u_param, now)
            return None
        return self._dispatch(action, "", now)

    def _dispatch(self, action: str, param: str,
                  now: Optional[float] = None) -> Optional[str]:
        p = param.strip()
        mode = self.mode_id
        # shared action names route to the ACTIVE mode's controller
        if action == "mark_important":
            if mode == "student":
                self.student.mark_important(p or self._last_note)
            elif mode == "meeting":
                self.meeting.mark_important(p, now)
            elif mode == "research":
                if p:
                    self.research.notes.add(p, important=True, now=now)
                elif self._last_note:
                    self.research.notes.add(self._last_note, important=True,
                                            now=now)
            else:
                self.teacher.mark_important(p or self._last_note, now)
            return action
        if action == "add_note":
            self._last_note = p
            if mode == "meeting":
                self.meeting.add_note(p, now)
            elif mode == "student" or mode == "research":
                self.student.take_note(p, now=now)
            else:
                self.teacher.add_note(p, now)
            return action
        if action == "source_capture":
            if mode == "research":
                self.research.sources.capture(p or "captured source", "")
            else:
                self.student.save_source(p or "captured source", "")
            return action
        if action == "export_lecture" and mode == "meeting":
            action = "export_summary"   # meeting phrase table maps here
        if action == "lecture_start":
            self.teacher.start_lecture(now)
        elif action == "lecture_pause":
            self.teacher.pause_lecture(now)
        elif action == "lecture_resume":
            self.teacher.resume_lecture(now)
        elif action == "export_lecture":
            self.teacher.export_lecture(
                os.path.join(os.path.expanduser("~"), ".airmouse",
                             "lecture.md"))
        elif action == "presentation_start":
            self.teacher.presentation.control("start_presentation")
        elif action == "presentation_next" or action == "next_slide":
            self.teacher.presentation.control("next_slide")
        elif action == "presentation_previous" or action == "previous_slide":
            self.teacher.presentation.control("previous_slide")
        elif action == "black_screen":
            self.teacher.presentation.control("black_screen")
        elif action == "white_screen":
            self.teacher.presentation.control("white_screen")
        elif action == "study_start":
            self.student.start_study_session(now)
        elif action == "note_add":
            self._last_note = p
            self.student.take_note(p, now=now)
        elif action == "note_important":
            self.student.take_note(p or self._last_note, important=True,
                                   now=now)
        elif action == "source_capture":
            self.student.save_source(p or "captured source", "")
        elif action == "meeting_start":
            self.meeting.start_meeting(now)
        elif action == "meeting_pause":
            self.meeting.pause_transcription(now)
        elif action == "meeting_resume":
            self.meeting.resume_transcription(now)
        elif action == "add_action_item":
            self.meeting.add_action_item(p, now)
        elif action == "bookmark":
            self.meeting.bookmark_moment(p, now)
        elif action == "meeting_stop":
            self.meeting.pause_transcription(now)
        elif action == "task_capture":
            self.office.capture_task(p)
        elif action in DEVELOPER_BINDINGS:
            self.developer.control(action)
        return action
