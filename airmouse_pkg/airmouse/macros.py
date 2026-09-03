"""
Macros v5.0 — Macro Recorder: record & replay gesture/action sequences.

A :class:`MacroRecorder` captures timestamped action events produced by
the main loop (clicks, scrolls, moves, drags, waits...) while a gesture
"record" mode is active.  :class:`MacroPlayer` replays a saved macro
synchronously or on a daemon thread, calling an ``executor(event,
params)`` callback at the right moment for every event.

Event names used by the main loop:
    ``click``, ``right_click``, ``double_click``, ``middle_click``,
    ``scroll`` (``amount: int``), ``zoom`` (``ticks: int``),
    ``move`` (``x: float, y: float``), ``drag_start``, ``drag_stop``,
    ``wait`` (``seconds: float``).

Each recorded event is a dict ``{"t": <seconds since record start>,
"event": <name>, **params}``.  Macros persist as JSON files in
``~/.airmouse/macros/<name>.json`` (atomic writes, sanitized names).
Every filesystem interaction is guarded — a missing directory or an
unwritable disk degrades gracefully instead of raising mid-gesture.

Classes:
    MacroRecorder — captures events while recording
    MacroPlayer  — loads and replays macros through an executor

Functions:
    list_macros  — names of all saved macros
    delete_macro — remove a saved macro by name
    is_playing   — True while a replay thread is running
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

MACRO_DIR = os.path.join(os.path.expanduser("~"), ".airmouse", "macros")

# Module-level guard so overlapping replays are refused.
_current_play_thread: "Optional[threading.Thread]" = None


def _sanitize_name(name: str) -> str:
    """Reduce a macro name to a safe file stem (no paths / weird chars)."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(name))
    safe = safe.strip("_")[:64]
    return safe or "macro"


def _macro_path(name: str) -> str:
    """File path for a macro name inside MACRO_DIR."""
    return os.path.join(MACRO_DIR, _sanitize_name(name) + ".json")


def _stop_requested(stop_check: Optional[Callable[[], bool]]) -> bool:
    """Evaluate a stop_check callback defensively (exceptions = keep going)."""
    if stop_check is None:
        return False
    try:
        return bool(stop_check())
    except Exception:
        return False


def list_macros() -> List[str]:
    """Sorted names of all saved macros (empty list on any error)."""
    try:
        return sorted(f[:-5] for f in os.listdir(MACRO_DIR) if f.endswith(".json"))
    except Exception:
        return []


def delete_macro(name: str) -> bool:
    """Delete a saved macro by name. True on success, never raises."""
    try:
        os.remove(_macro_path(name))
        return True
    except Exception:
        return False


def is_playing() -> bool:
    """True while a macro replay (sync or async) is currently running."""
    t = _current_play_thread
    return t is not None and t.is_alive()


class MacroRecorder:
    """Records timestamped action events while a gesture macro is active."""

    def __init__(self) -> None:
        self.recording: bool = False
        self.name: Optional[str] = None
        self.events: List[Dict[str, Any]] = []
        self._t0: float = 0.0

    def start(self, name: str) -> bool:
        """Begin recording a macro called ``name``. False if already recording."""
        if self.recording:
            return False
        self.name = str(name)
        self.events = []
        self._t0 = time.perf_counter()
        self.recording = True
        return True

    def record(self, event: str, **params: Any) -> None:
        """Append an event (no-op unless currently recording).

        ``event`` is one of the action names from the main loop, e.g.
        ``"click"``, ``"scroll"`` (``amount:int``), ``"move"``
        (``x,y`` floats), ``"drag_start"``/``"drag_stop"``, ``"zoom"``
        (``ticks:int``), ``"wait"`` (``seconds:float``).  The timestamp
        ``t`` (seconds since :meth:`start`, via a monotonic clock) is
        added automatically.
        """
        if not self.recording:
            return
        ev: Dict[str, Any] = {"t": time.perf_counter() - self._t0, "event": str(event)}
        ev.update(params)
        self.events.append(ev)

    def stop(self) -> List[Dict[str, Any]]:
        """Finish recording and return the captured event list.

        Events are kept in memory so :meth:`save` can still be called
        afterwards.
        """
        self.recording = False
        return list(self.events)

    def cancel(self) -> None:
        """Discard the current recording entirely."""
        self.recording = False
        self.name = None
        self.events = []

    def save(self) -> str:
        """Write the recording to ``MACRO_DIR/<name>.json`` (atomic write).

        Returns the file path on success, ``""`` on any failure (never
        raises).  The JSON layout is::

            {"name": ..., "created": <iso timestamp>,
             "duration": <last event t>, "events": [...]}
        """
        try:
            name = self.name or "macro"
            path = _macro_path(name)
            os.makedirs(MACRO_DIR, exist_ok=True)
            payload = {
                "name": name,
                "created": datetime.now().isoformat(timespec="seconds"),
                "duration": float(self.events[-1]["t"]) if self.events else 0.0,
                "events": list(self.events),
            }
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, path)
            return path
        except Exception:
            return ""


class MacroPlayer:
    """Replays saved macros by calling an executor for each event."""

    def __init__(self, executor: Callable[[str, Dict[str, Any]], None]) -> None:
        """
        Args:
            executor: callable invoked as ``executor(event, params)`` for
                every replayed event, where ``params`` holds all event
                payload keys except ``t`` and ``event``.
        """
        self._executor = executor
        self._macro: Optional[Dict[str, Any]] = None

    def load(self, name: str) -> Dict[str, Any]:
        """Load ``MACRO_DIR/<name>.json`` and remember it for :meth:`play`.

        Raises FileNotFoundError if the macro does not exist.
        """
        path = _macro_path(name)
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"macro file {path!r} does not contain a JSON object")
        self._macro = data
        return data

    def play(self, speed: float = 1.0,
             stop_check: Optional[Callable[[], bool]] = None) -> bool:
        """Synchronously replay the loaded macro.  Returns True on completion.

        Sleeps the inter-event deltas (divided by ``speed``) with
        ``time.sleep`` in small chunks so ``stop_check`` stays responsive;
        if ``stop_check()`` returns True the replay aborts and False is
        returned.  If no macro was loaded, the most recently saved macro
        is used as a convenience.  Refuses to run while another replay is
        active (returns False).
        """
        macro = self._macro
        if macro is None:
            macro = self._load_latest()
            if macro is None:
                return False
            self._macro = macro
        cur = _current_play_thread
        if cur is not None and cur.is_alive():
            return False  # a replay is already running
        events = macro.get("events")
        if not isinstance(events, list):
            return False
        return self._run([e for e in events if isinstance(e, dict)], speed, stop_check)

    def play_async(self, speed: float = 1.0) -> Optional[threading.Thread]:
        """Replay on a daemon thread and return it (None if refused).

        Overlapping plays are refused via the module-level
        ``_current_play_thread`` guard: while a replay thread is alive,
        further :meth:`play_async` calls return None.
        """
        global _current_play_thread
        cur = _current_play_thread
        if cur is not None and cur.is_alive():
            return None
        macro = self._macro
        if macro is None:
            macro = self._load_latest()
            if macro is None:
                return None
            self._macro = macro
        events = macro.get("events")
        if not isinstance(events, list):
            return None
        th = threading.Thread(
            target=self._run,
            args=([e for e in events if isinstance(e, dict)], speed, None),
            daemon=True,
        )
        _current_play_thread = th  # claim the slot before starting (race-safe)
        th.start()
        return th

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _run(self, events: List[Dict[str, Any]], speed: float,
             stop_check: Optional[Callable[[], bool]]) -> bool:
        global _current_play_thread
        _current_play_thread = threading.current_thread()
        try:
            try:
                spd = float(speed)
            except Exception:
                spd = 1.0
            if not math.isfinite(spd) or spd <= 0.0:
                spd = 1.0
            prev = 0.0
            for ev in events:
                try:
                    t = float(ev.get("t", prev) or 0.0)
                except Exception:
                    t = prev
                if t < prev:
                    t = prev
                delay = (t - prev) / spd
                if delay > 0.0 and not self._sleep(delay, stop_check):
                    return False  # aborted by stop_check
                prev = t
                params = {k: v for k, v in ev.items() if k not in ("t", "event")}
                try:
                    self._executor(str(ev.get("event", "")), params)
                except Exception:
                    pass  # one bad action must not kill the whole replay
            return True
        finally:
            if _current_play_thread is threading.current_thread():
                _current_play_thread = None

    @staticmethod
    def _sleep(delay: float, stop_check: Optional[Callable[[], bool]]) -> bool:
        """Sleep ``delay`` seconds in <=50 ms chunks.  False = abort requested."""
        end = time.perf_counter() + delay
        while True:
            if _stop_requested(stop_check):
                return False
            remaining = end - time.perf_counter()
            if remaining <= 0.0:
                return True
            time.sleep(min(0.05, remaining))

    @staticmethod
    def _load_latest() -> Optional[Dict[str, Any]]:
        """Load the most recently saved macro (used when none was loaded)."""
        try:
            candidates = [
                (os.path.getmtime(os.path.join(MACRO_DIR, f)), f)
                for f in os.listdir(MACRO_DIR) if f.endswith(".json")
            ]
            if not candidates:
                return None
            _, fname = max(candidates)
            with open(os.path.join(MACRO_DIR, fname), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except Exception:
            return None
