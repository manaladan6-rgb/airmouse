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

v19.0.3 OPERATION FINAL FORM: every replay is gated by the central
safety gate (``safety_gate.get_gate().authorize("macro_action")``)
BEFORE the first action, re-checked before EACH step/event and inside
every wait/sleep chunk (checkpoint-based — a wait can never sleep
through an E-STOP).  A gate that is estopped or shutting down refuses
the replay BEFORE anything is dispatched.  Replay is cooperatively
cancellable: a per-play ``stop_check``, the player's own
:meth:`MacroPlayer.cancel` latch and the module-wide
:func:`cancel_current_replay` latch are all observed at every
checkpoint; a cancelled replay stops cleanly and reports an honest
stopped/partial result (:meth:`MacroPlayer.play_detailed` /
``MacroPlayer.last_result``).

Classes:
    MacroRecorder — captures events while recording
    MacroPlayer  — loads and replays macros through an executor

Functions:
    list_macros  — names of all saved macros
    delete_macro — remove a saved macro by name
    is_playing   — True while a replay thread is running
    cancel_current_replay — latch a cancel + bounded join of the live replay
    join_current_replay   — bounded join of the live replay (no cancel latch)
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

try:  # package-relative (normal import path)
    from .safety_gate import get_gate
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.safety_gate import get_gate

#: Legacy module-level macro directory.  Kept for backwards
#: compatibility (tests and embedders may read or override it), but the
#: ACTIVE directory is resolved by :func:`_macro_dir` on every call:
#: an explicit runtime override of MACRO_DIR (≠ the import-time
#: default) wins, otherwise the authoritative
#: ``airmouse.paths.macros_dir()`` is used — so ``$AIRMOUSE_HOME`` set
#: after import is always honored.
MACRO_DIR = os.path.join(os.path.expanduser("~"), ".airmouse", "macros")

_DEFAULT_MACRO_DIR = MACRO_DIR


def _macro_dir() -> str:
    """Active macro directory (dynamic — one resolution via paths.py)."""
    override = globals().get("MACRO_DIR")
    try:
        if override and os.path.abspath(str(override)) != \
                os.path.abspath(_DEFAULT_MACRO_DIR):
            return str(override)
    except Exception:
        pass
    try:
        from . import paths
        return paths.macros_dir()
    except Exception:
        return _DEFAULT_MACRO_DIR

# Module-level guard so overlapping replays are refused.
_current_play_thread: "Optional[threading.Thread]" = None

# ══ v19.0.3 OPERATION FINAL FORM — central gate + cooperative cancel ══════

#: Gate action class presented by every macro replay route.
MACRO_ACTION_CLASS = "macro_action"

#: Module-wide cancel latch (set by :func:`cancel_current_replay`); it is
#: observed at every replay checkpoint and consumed by the run that honours it.
_MODULE_CANCEL = threading.Event()

#: Default BOUND for cancel/join thread joins (seconds).  A join is always
#: bounded — an unterminated worker is reported honestly, never awaited
#: forever.
_CANCEL_JOIN_TIMEOUT_S = 0.5


def _gate_macro_refusal() -> str:
    """Central-gate verdict for macro replay: "" = allowed, else reason.

    Fail-closed: a broken gate blocks (never enables).  Never raises.
    """
    try:
        ok, reason = get_gate().authorize(MACRO_ACTION_CLASS)
        return "" if ok else str(reason)
    except Exception as exc:  # a broken gate blocks, never enables
        return f"gate_error: {exc}"


def _runner_start_refusal() -> str:
    """Fail-closed START verdict for ProgramRunner runs (never raises).

    Order: module-wide cancel latch, then the central safety gate.
    "" = the run may start.
    """
    if _MODULE_CANCEL.is_set():
        return "cancel_requested"
    return _gate_macro_refusal()


def _runner_stop_reason(stop_check: Optional[Callable[[], bool]]) -> str:
    """Per-step checkpoint verdict for ProgramRunner runs (never raises).

    Order: the per-run ``stop_check`` (legacy reason strings preserved),
    then the module-wide cancel latch, then the central safety gate.
    "" = continue.
    """
    if _stop_requested(stop_check):
        return "stop_requested"
    if _MODULE_CANCEL.is_set():
        return "cancel_requested"
    return _gate_macro_refusal()


def _sanitize_name(name: str) -> str:
    """Reduce a macro name to a safe file stem (no paths / weird chars)."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(name))
    safe = safe.strip("_")[:64]
    return safe or "macro"


def _macro_path(name: str) -> str:
    """File path for a macro name inside the active macro dir."""
    return os.path.join(_macro_dir(), _sanitize_name(name) + ".json")


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
        return sorted(f[:-5] for f in os.listdir(_macro_dir())
                      if f.endswith(".json"))
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


def cancel_current_replay(
    timeout_s: float = _CANCEL_JOIN_TIMEOUT_S,
    reason: str = "cancel_requested",
) -> Dict[str, Any]:
    """Cooperatively cancel the CURRENT module-wide replay (never raises).

    Sets the module-wide cancel latch (observed by every
    :class:`MacroPlayer`/`ProgramRunner` checkpoint — before each step and
    inside every wait/sleep chunk) and joins the live replay thread with a
    BOUND timeout.  NEVER joins the current thread (a worker asked to cancel
    itself just keeps hitting its checkpoints).  Returns an honest report
    dict; ``terminated`` is False (with ``thread_alive`` True) when a worker
    did not terminate within the bound.
    """
    _MODULE_CANCEL.set()
    return MacroPlayer._join_worker(_current_play_thread, timeout_s, reason)


def join_current_replay(
    timeout_s: float = _CANCEL_JOIN_TIMEOUT_S,
) -> Dict[str, Any]:
    """Bounded join of the live replay thread WITHOUT latching a cancel.

    Stale-safe: a finished/absent replay thread reports immediately.
    Returns the same honest report shape as :func:`cancel_current_replay`
    with ``requested`` False.
    """
    report = MacroPlayer._join_worker(
        _current_play_thread, timeout_s, "join_requested")
    report["requested"] = False
    return report


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
        """Write the recording to ``<macros dir>/<name>.json`` (atomic).

        Returns the file path on success, ``""`` on any failure (never
        raises).  The JSON layout is::

            {"name": ..., "created": <iso timestamp>,
             "duration": <last event t>, "events": [...]}
        """
        try:
            name = self.name or "macro"
            path = _macro_path(name)
            macro_dir = _macro_dir()
            os.makedirs(macro_dir, exist_ok=True)
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
    """Replays saved macros by calling an executor for each event.

    v19.0.3 OPERATION FINAL FORM: every replay consults the central
    safety gate (``safety_gate.get_gate().authorize("macro_action")``)
    BEFORE the first action, re-checks before EACH event and inside every
    wait/sleep chunk, and refuses to start — fail-closed, NOTHING
    dispatched — while the gate is estopped or shutting down (the refusal
    reason is surfaced on the result).  Replay is cooperatively
    cancellable: a per-play ``stop_check``, the player's own
    :meth:`cancel` latch and the module-wide :func:`cancel_current_replay`
    latch are all observed at every checkpoint.  A stopped replay returns
    an honest stopped/partial result (see :meth:`play_detailed` and
    :attr:`last_result`) — never a silent lie.
    """

    def __init__(self, executor: Callable[[str, Dict[str, Any]], None],
                 finish_fn: Optional[Callable[[], None]] = None) -> None:
        """
        Args:
            executor: callable invoked as ``executor(event, params)`` for
                every replayed event, where ``params`` holds all event
                payload keys except ``t`` and ``event``.
            finish_fn: optional zero-arg callable invoked exactly once when
                a replay ends (completion, abort, or exception). v19.0.2
                safety: use it to release a held mouse button so an
                interrupted drag replay can never orphan a press.
        """
        self._executor = executor
        self._finish_fn = finish_fn
        self._macro: Optional[Dict[str, Any]] = None
        # v19.0.3 — cooperative cancellation + honest result surface.
        self._cancel_event = threading.Event()
        self._play_thread: "Optional[threading.Thread]" = None
        self.last_result: Dict[str, Any] = {}

    def load(self, name: str) -> Dict[str, Any]:
        """Load ``<macros dir>/<name>.json`` and remember it for :meth:`play`.

        Raises FileNotFoundError if the macro does not exist.
        """
        path = _macro_path(name)
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"macro file {path!r} does not contain a JSON object")
        self._macro = data
        return data

    # ── cancellation ─────────────────────────────────────────────────
    def cancel(self, timeout_s: float = _CANCEL_JOIN_TIMEOUT_S,
               reason: str = "cancel_requested") -> Dict[str, Any]:
        """Request cooperative cancellation of this player's replay.

        Sets this player's stop latch (observed at every replay checkpoint —
        before each event and inside every wait/sleep chunk) and joins this
        player's live replay thread with a BOUND timeout.  NEVER joins the
        current thread (a self-cancel from inside an executor hook just
        arms the latch; the report says so honestly).  Returns an honest
        report dict — ``terminated`` False + ``thread_alive`` True when a
        worker did not terminate within the bound.  Never raises.
        """
        self._cancel_event.set()
        return self._join_worker(self._play_thread, timeout_s, reason)

    @staticmethod
    def _join_worker(th: "Optional[threading.Thread]",
                     timeout_s: float,
                     reason: str) -> Dict[str, Any]:
        """Bounded, self-join-safe worker join with an honest report."""
        try:
            bound = max(0.0, float(timeout_s))
        except Exception:
            bound = _CANCEL_JOIN_TIMEOUT_S
        report: Dict[str, Any] = {
            "requested": True,
            "reason": str(reason or "cancel_requested"),
            "was_running": False,
            "join_attempted": False,
            "self_join_skipped": False,
            "terminated": True,
            "thread_alive": False,
            "timeout_s": bound,
        }
        if th is None or not th.is_alive():
            return report  # nothing live: report is already the truth
        report["was_running"] = True
        report["terminated"] = False
        report["thread_alive"] = True
        if th is threading.current_thread():
            # NEVER join the current thread — it would deadlock forever.
            report["self_join_skipped"] = True
            return report
        report["join_attempted"] = True
        th.join(bound)
        alive = th.is_alive()
        report["terminated"] = not alive
        report["thread_alive"] = alive
        return report

    # ── replay entry points ──────────────────────────────────────────
    def play_detailed(self, speed: float = 1.0,
                      stop_check: Optional[Callable[[], bool]] = None
                      ) -> Dict[str, Any]:
        """Synchronously replay the loaded macro with an honest result.

        Returns ``{"completed", "stopped", "started", "reason",
        "events_dispatched", "events_total"}``: ``completed`` True only
        when EVERY event was dispatched; ``stopped`` True when the replay
        ended early (cancel/stop_check/gate); ``started`` False when the
        replay was refused BEFORE dispatching anything (overlap, cancel
        latch, stop_check, or an estopped/shutting-down gate — the
        ``reason`` then carries the refusal reason verbatim).  Sleeps the
        inter-event deltas (divided by ``speed``) in small chunks so every
        checkpoint stays responsive.  If no macro was loaded, the most
        recently saved macro is used as a convenience.  Refuses to run
        while another replay is active.
        """
        macro = self._macro
        if macro is None:
            macro = self._load_latest()
            if macro is None:
                return self._store_result({
                    "completed": False, "stopped": False, "started": False,
                    "reason": "no_macro", "events_dispatched": 0,
                    "events_total": 0})
            self._macro = macro
        cur = _current_play_thread
        if cur is not None and cur.is_alive():
            return self._store_result({
                "completed": False, "stopped": False, "started": False,
                "reason": "already_playing", "events_dispatched": 0,
                "events_total": self._macro_event_count(macro)})
        events = macro.get("events")
        if not isinstance(events, list):
            return self._store_result({
                "completed": False, "stopped": False, "started": False,
                "reason": "no_events", "events_dispatched": 0,
                "events_total": 0})
        return self._run([e for e in events if isinstance(e, dict)],
                         speed, stop_check)

    def play(self, speed: float = 1.0,
             stop_check: Optional[Callable[[], bool]] = None) -> bool:
        """Synchronously replay the loaded macro.  True on full completion.

        Thin boolean view over :meth:`play_detailed` — False means the
        replay was refused or stopped early; the detailed reason is on
        ``self.last_result`` (and the gate's own refusal reason is always
        available via ``safety_gate.get_gate().last_refusal()``).
        """
        return bool(self.play_detailed(speed=speed,
                                       stop_check=stop_check)["completed"])

    def play_async(self, speed: float = 1.0,
                   stop_check: Optional[Callable[[], bool]] = None
                   ) -> Optional[threading.Thread]:
        """Replay on a daemon thread and return it (None if refused).

        Overlapping plays are refused via the module-level
        ``_current_play_thread`` guard: while a replay thread is alive,
        further :meth:`play_async` calls return None.  v19.0.3: the
        per-play ``stop_check`` is carried into the worker (the old
        hardcoded-None path that lost cancellation is gone — the worker
        also observes this player's :meth:`cancel` latch and the central
        gate at every checkpoint), and a fail-closed pre-flight refuses
        BEFORE starting a worker when the gate is estopped/shutting down
        or a cancel latch is already set (reason on ``self.last_result``).
        """
        global _current_play_thread
        cur = _current_play_thread
        if cur is not None and cur.is_alive():
            self._store_result({
                "completed": False, "stopped": False, "started": False,
                "reason": "already_playing", "events_dispatched": 0,
                "events_total": 0})
            return None
        macro = self._macro
        if macro is None:
            macro = self._load_latest()
            if macro is None:
                self._store_result({
                    "completed": False, "stopped": False, "started": False,
                    "reason": "no_macro", "events_dispatched": 0,
                    "events_total": 0})
                return None
            self._macro = macro
        events = macro.get("events")
        if not isinstance(events, list):
            self._store_result({
                "completed": False, "stopped": False, "started": False,
                "reason": "no_events", "events_dispatched": 0,
                "events_total": 0})
            return None
        refusal = self._start_refusal(stop_check)
        if refusal:
            self._store_result({
                "completed": False, "stopped": True, "started": False,
                "reason": refusal, "events_dispatched": 0,
                "events_total": len([e for e in events if isinstance(e, dict)])})
            return None
        th = threading.Thread(
            target=self._run,
            args=([e for e in events if isinstance(e, dict)], speed,
                  stop_check),
            daemon=True,
            name="airmouse-macro-replay",
        )
        _current_play_thread = th  # claim the slot before starting (race-safe)
        th.start()
        return th

    # ── internals ──────────────────────────────────────────────────────────
    @staticmethod
    def _macro_event_count(macro: Dict[str, Any]) -> int:
        events = macro.get("events")
        if not isinstance(events, list):
            return 0
        return len([e for e in events if isinstance(e, dict)])

    def _store_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Remember + return the honest result of the last replay attempt."""
        self.last_result = dict(result)
        return self.last_result

    def _start_refusal(
            self, stop_check: Optional[Callable[[], bool]]) -> str:
        """Fail-closed start check: "" = may start, else the refusal reason.

        Order: cancel latches (this player + module-wide), then the
        per-play ``stop_check``, then the central safety gate.  The gate
        verdict is fetched fresh — an estopped/shutting-down gate refuses
        BEFORE the first action could ever be dispatched.
        """
        if self._cancel_event.is_set() or _MODULE_CANCEL.is_set():
            return "cancel_requested"
        if _stop_requested(stop_check):
            return "stop_requested"
        return _gate_macro_refusal()

    def _checkpoint(
            self, stop_check: Optional[Callable[[], bool]]) -> str:
        """Cooperative checkpoint: "" = continue, else the honest stop reason.

        Evaluated before EVERY event and inside every wait/sleep chunk:
        cancel latches first, then the per-play ``stop_check``, then the
        central safety gate (a mid-play E-STOP stops the replay before
        the next step).
        """
        if self._cancel_event.is_set() or _MODULE_CANCEL.is_set():
            return "cancel_requested"
        if _stop_requested(stop_check):
            return "stop_requested"
        return _gate_macro_refusal()

    def _run(self, events: List[Dict[str, Any]], speed: float,
             stop_check: Optional[Callable[[], bool]]) -> Dict[str, Any]:
        global _current_play_thread
        _current_play_thread = threading.current_thread()
        self._play_thread = threading.current_thread()
        result: Dict[str, Any] = {
            "completed": False, "stopped": False, "started": True,
            "reason": "", "events_dispatched": 0, "events_total": len(events),
        }
        try:
            try:
                spd = float(speed)
            except Exception:
                spd = 1.0
            if not math.isfinite(spd) or spd <= 0.0:
                spd = 1.0
            # Fail-closed start: the gate/cancel/stop verdict happens
            # BEFORE the first action could be dispatched.
            refusal = self._start_refusal(stop_check)
            if refusal:
                result.update(started=False, stopped=True, reason=refusal)
                return result
            prev = 0.0
            for ev in events:
                stop_reason = self._checkpoint(stop_check)
                if stop_reason:
                    result.update(stopped=True, reason=stop_reason)
                    return result
                try:
                    t = float(ev.get("t", prev) or 0.0)
                except Exception:
                    t = prev
                if t < prev:
                    t = prev
                delay = (t - prev) / spd
                if delay > 0.0:
                    stop_reason = self._sleep(delay, stop_check)
                    if stop_reason:
                        result.update(stopped=True, reason=stop_reason)
                        return result
                prev = t
                params = {k: v for k, v in ev.items() if k not in ("t", "event")}
                try:
                    self._executor(str(ev.get("event", "")), params)
                except Exception:
                    pass  # one bad action must not kill the whole replay
                result["events_dispatched"] += 1
            result["completed"] = True
            return result
        finally:
            if _current_play_thread is threading.current_thread():
                _current_play_thread = None
            self._store_result(result)
            # The cancel latch is consumed by the run that honoured it (or
            # that refused to start because of it) — a fresh replay may run.
            self._cancel_event.clear()
            _MODULE_CANCEL.clear()
            # v19.0.2 — always run the finish hook: an interrupted drag
            # replay must never leave the primary button pressed.
            try:
                if self._finish_fn is not None:
                    self._finish_fn()
            except Exception:
                pass  # never let cleanup break cleanup

    def _sleep(self, delay: float,
               stop_check: Optional[Callable[[], bool]]) -> str:
        """Sleep ``delay`` seconds in <=50 ms chunks (checkpoint-based).

        Returns "" when the delay elapsed, else the stop reason.  EVERY
        chunk re-checks the cancel latches, ``stop_check`` and the central
        gate — a wait can never sleep through an E-STOP.
        """
        end = time.perf_counter() + max(0.0, delay)
        while True:
            stop_reason = self._checkpoint(stop_check)
            if stop_reason:
                return stop_reason
            remaining = end - time.perf_counter()
            if remaining <= 0.0:
                return ""
            time.sleep(min(0.05, remaining))

    @staticmethod
    def _load_latest() -> Optional[Dict[str, Any]]:
        """Load the most recently saved macro (used when none was loaded)."""
        try:
            macro_dir = _macro_dir()
            candidates = [
                (os.path.getmtime(os.path.join(macro_dir, f)), f)
                for f in os.listdir(macro_dir) if f.endswith(".json")
            ]
            if not candidates:
                return None
            _, fname = max(candidates)
            with open(os.path.join(macro_dir, fname), "r",
                      encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════════════
# v9.0.0 MACRO FORMAT V2
# ═══════════════════════════════════════════════════════════════════════════
#
# Everything below this marker is NEW in v9.0.0 and is strictly ADDITIVE:
# the v1 recorder/player above is untouched and keeps working.  Format v2
# stores SEMANTIC steps (:class:`airmouse.interfaces.MacroStep`) instead of
# raw timestamped events, enabling LOOK_FOR targets, VERIFY assertions,
# IF branches, RETRY loops and STOP — while v1 files still load (as a
# MacroProgram with ``legacy_events``) and replay via
# :meth:`ProgramRunner.run_legacy`.
#
# v2 JSON layout (written by :func:`save_program`)::
#
#     {"version": 2, "name": ..., "created": ...,
#      "steps": [{"op": "click", "params": {...}, "comment": ""}, ...]}
#
# Legacy v1 files keep their original layout and are detected by the
# presence of an "events" list (and absence of non-empty "steps").

from typing import Tuple  # noqa: F401  (annotations)

try:  # package-relative (normal import path)
    from .interfaces import (
        ActionPlan,
        ActionReport,
        ActionStatus,
        ActionType,
        Intent,
        IntentType,
        MacroOp,
        MacroProgram,
        MacroStep,
        Modality,
        ScreenTarget,
        ScreenTargetType,
        VerificationStatus,
        now_ts,
    )
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (
        ActionPlan,
        ActionReport,
        ActionStatus,
        ActionType,
        Intent,
        IntentType,
        MacroOp,
        MacroProgram,
        MacroStep,
        Modality,
        ScreenTarget,
        ScreenTargetType,
        VerificationStatus,
        now_ts,
    )

#: Documented :class:`ProgramRunner` configuration defaults.
PROGRAM_RUNNER_DEFAULTS: Dict[str, float] = {
    "max_steps": 200,        # hard cap on executed steps per run
    "step_timeout": 10.0,    # s — cap for a single wait-type step
    "look_for_timeout": 8.0,  # s — LOOK_FOR polling budget
    "poll_interval": 0.05,   # s — polling/sleep chunk size
    "max_if_depth": 3,       # nested IF depth cap
}

_OP_TO_INTENT: Dict[MacroOp, IntentType] = {
    MacroOp.CLICK: IntentType.CLICK,
    MacroOp.DOUBLE_CLICK: IntentType.DOUBLE_CLICK,
    MacroOp.RIGHT_CLICK: IntentType.RIGHT_CLICK,
}


def _decode_step(data: Dict[str, Any]) -> MacroStep:
    """JSON dict -> MacroStep (unknown ops degrade to WAIT_UNTIL, pure)."""
    try:
        op = MacroOp(str(data.get("op", "wait_until")))
    except ValueError:
        op = MacroOp.WAIT_UNTIL
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    params = dict(params)
    if op == MacroOp.IF:  # nested branches decode recursively
        for branch in ("then", "else"):
            raw = params.get(branch)
            if isinstance(raw, list):
                params[branch] = [
                    _decode_step(s) for s in raw if isinstance(s, dict)
                ]
    return MacroStep(op=op, params=params, comment=str(data.get("comment", "")))


def _encode_step(step: MacroStep) -> Dict[str, Any]:
    """MacroStep -> JSON dict (IF branches encode recursively, pure)."""
    params = dict(step.params or {})
    if step.op == MacroOp.IF:
        for branch in ("then", "else"):
            raw = params.get(branch)
            if isinstance(raw, list):
                params[branch] = [
                    _encode_step(s) if isinstance(s, MacroStep) else dict(s)
                    for s in raw
                ]
    return {"op": step.op.value, "params": params, "comment": step.comment}


def load_program(path: str) -> MacroProgram:
    """Load a macro file as a :class:`MacroProgram`.

    A JSON object with a ``steps`` list loads as a v2 program; one with an
    ``events`` list (v1 recorder layout) loads as
    ``MacroProgram(version=1, legacy_events=events)``.  A missing file
    raises FileNotFoundError.
    """
    with open(str(path), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"macro file {path!r} does not contain a JSON object")
    name = str(data.get("name") or os.path.splitext(os.path.basename(str(path)))[0])
    created = str(data.get("created", ""))
    steps_raw = data.get("steps")
    events_raw = data.get("events")
    if isinstance(steps_raw, list) and steps_raw and not isinstance(events_raw, list):
        return MacroProgram(
            name=name,
            version=2,
            steps=[_decode_step(s) for s in steps_raw if isinstance(s, dict)],
            created=created,
        )
    if isinstance(events_raw, list):
        return MacroProgram(
            name=name,
            version=1,
            steps=[],
            legacy_events=[e for e in events_raw if isinstance(e, dict)],
            created=created,
        )
    # fallback: steps present but empty/malformed → empty v2 program
    return MacroProgram(
        name=name,
        version=2,
        steps=[_decode_step(s) for s in (steps_raw or []) if isinstance(s, dict)],
        created=created,
    )


def save_program(program: MacroProgram, path: str) -> bool:
    """Atomically write ``program`` as v2 JSON (tmp + os.replace).

    The filename stem is sanitized; legacy events (when present) are kept
    under an "events" key so a v1 file round-trips.  Returns True on
    success, False on any failure (never raises).
    """
    try:
        directory = os.path.dirname(str(path)) or "."
        stem = _sanitize_name(os.path.splitext(os.path.basename(str(path)))[0])
        target = os.path.join(directory, stem + ".json")
        payload: Dict[str, Any] = {
            "version": 2,
            "name": str(getattr(program, "name", "") or stem),
            "created": str(getattr(program, "created", "")
                           or datetime.now().isoformat(timespec="seconds")),
            "steps": [_encode_step(s) for s in (getattr(program, "steps", None) or [])],
        }
        if getattr(program, "legacy_events", None):
            payload["events"] = list(program.legacy_events)
        os.makedirs(directory, exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, target)
        return True
    except Exception:
        return False


class ProgramRunner:
    """Executes semantic MacroPrograms (format v2) through an executor.

    Args:
        executor: object with ``click/double_click/right_click/type_text/
            scroll/hotkey`` (e.g. :class:`actions.MockExecutor`).
        safety: optional object with ``approve_intent(intent) ->
            SafetyDecision``; click steps are gated through it.
        screen_provider: optional object with ``find_by_text(text)`` (and
            optionally ``model()``) used by LOOK_FOR steps.
        verifier: optional object with ``verify(plan, report, observe_fn)
            -> VerificationResult`` used by VERIFY steps.
        config: see :data:`PROGRAM_RUNNER_DEFAULTS`.

    v19.0.3 OPERATION FINAL FORM: runs consult the central safety gate
    (``safety_gate.get_gate().authorize("macro_action")``) before the
    first step, re-check before EACH step and inside every sleep/poll
    loop, and observe the module-wide cancel latch — an estop, shutdown
    or :func:`cancel_current_replay` aborts the run with an honest
    ``aborted`` + ``reason`` report before anything further dispatches.

    :meth:`run` returns ``{"steps_total", "steps_executed", "verified",
    "failed", "aborted", "reason", "duration"}``.  Executor exceptions are
    contained per step (counted as failed) — a run never raises.
    """

    def __init__(
        self,
        executor: Any,
        safety: Any = None,
        screen_provider: Any = None,
        verifier: Any = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        cfg = dict(PROGRAM_RUNNER_DEFAULTS)
        cfg.update(config or {})
        self.executor = executor
        self.safety = safety
        self.screen_provider = screen_provider
        self.verifier = verifier
        self.max_steps: int = max(1, int(cfg["max_steps"]))
        self.step_timeout: float = float(cfg["step_timeout"])
        self.look_for_timeout: float = float(cfg["look_for_timeout"])
        self.poll_interval: float = max(0.005, float(cfg["poll_interval"]))
        self.max_if_depth: int = max(1, int(cfg["max_if_depth"]))

    # -- public API ------------------------------------------------------------

    def run(
        self,
        program: MacroProgram,
        stop_check: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Run a v2 program (or the legacy replay for a v1 program).

        Passing a v1 (``legacy_events``) program to :meth:`run` replays it
        via :meth:`run_legacy` so callers can use one entry point.
        """
        if getattr(program, "legacy_events", None) and not getattr(program, "steps", None):
            return self.run_legacy(program, stop_check=stop_check)
        t0 = time.perf_counter()
        state = self._fresh_state()
        # Fail-closed start: an estopped/shutting-down gate (or a latched
        # module cancel) refuses the run BEFORE the first step dispatches.
        start_refusal = _runner_start_refusal()
        if start_refusal:
            self._abort(state, start_refusal)
            return self._report(state, t0)
        self._run_steps(list(getattr(program, "steps", None) or []), 0, state,
                        stop_check)
        return self._report(state, t0)

    def run_legacy(
        self,
        program: MacroProgram,
        speed: float = 1.0,
        stop_check: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Replay ``program.legacy_events`` with v1 semantics.

        Inter-event ``t`` deltas are scaled by ``1/speed`` and slept in
        <=50 ms chunks (honoring ``stop_check``).  Event dispatch:
        click/right_click/double_click/middle_click fire at the event's
        point or the last move point (or screen centre); scroll fires the
        wheel; zoom replays as a plain scroll (v1 files carry no ctrl
        modifier semantics — documented limitation); move updates the
        tracked point; drag_start/drag_stop bracket an executor drag;
        wait sleeps.  Report shape matches :meth:`run`.
        """
        t0 = time.perf_counter()
        try:
            spd = float(speed)
        except Exception:
            spd = 1.0
        if not math.isfinite(spd) or spd <= 0.0:
            spd = 1.0
        events = [e for e in (getattr(program, "legacy_events", None) or [])
                  if isinstance(e, dict)]
        ex = self.executor
        cx = int(getattr(ex, "screen_w", 1920) or 1920) // 2
        cy = int(getattr(ex, "screen_h", 1080) or 1080) // 2
        last_point: Tuple[float, float] = (float(cx), float(cy))
        drag_origin: Optional[Tuple[float, float]] = None
        state = self._fresh_state()
        state["steps_total"] = len(events)
        # Fail-closed start (same verdict as the v2 path).
        start_refusal = _runner_start_refusal()
        if start_refusal:
            self._abort(state, start_refusal)
            return self._report(state, t0)
        prev_t = 0.0
        for ev in events:
            if state["aborted"]:
                break
            stop_reason = _runner_stop_reason(stop_check)
            if stop_reason:
                self._abort(state, stop_reason)
                break
            if state["executed"] >= self.max_steps:
                self._abort(state, "max_steps")
                break
            try:
                t = float(ev.get("t", prev_t) or 0.0)
            except Exception:
                t = prev_t
            t = max(t, prev_t)
            delay = (t - prev_t) / spd
            if delay > 0.0 and not self._sleep(delay, stop_check, state):
                break
            prev_t = t
            params = {k: v for k, v in ev.items() if k not in ("t", "event")}
            name = str(ev.get("event", ""))
            pt = self._params_point(params) or last_point
            try:
                if name == "move":
                    last_point = (float(pt[0]), float(pt[1]))
                    ex.move(pt[0], pt[1])
                elif name == "click":
                    last_point = (float(pt[0]), float(pt[1]))
                    ex.click(pt[0], pt[1])
                elif name == "right_click":
                    last_point = (float(pt[0]), float(pt[1]))
                    ex.right_click(pt[0], pt[1])
                elif name == "double_click":
                    last_point = (float(pt[0]), float(pt[1]))
                    ex.double_click(pt[0], pt[1])
                elif name == "middle_click":
                    last_point = (float(pt[0]), float(pt[1]))
                    ex.middle_click(pt[0], pt[1])
                elif name == "scroll":
                    ex.scroll(int(params.get("amount", 0) or 0))
                elif name == "zoom":
                    # v1 zoom events replay as a plain wheel scroll; the
                    # ctrl modifier semantics are not stored in v1 files.
                    ex.scroll(int(params.get("ticks", 0) or 0))
                elif name == "drag_start":
                    drag_origin = (float(pt[0]), float(pt[1]))
                    last_point = drag_origin
                elif name == "drag_stop":
                    if drag_origin is not None:
                        ex.drag(drag_origin[0], drag_origin[1], pt[0], pt[1])
                        drag_origin = None
                elif name == "wait":
                    self._sleep(float(params.get("seconds", 0.0) or 0.0) / spd,
                                stop_check, state)
                # unknown v1 event names are ignored (executed, no-op)
            except Exception:
                state["failed"] += 1
                state["last_step_failed"] = True
            state["executed"] += 1
            state["last_step_failed"] = False
        return self._report(state, t0)

    # -- step machinery -----------------------------------------------------------

    def _fresh_state(self) -> Dict[str, Any]:
        return {
            "steps_total": 0,
            "executed": 0,
            "verified": 0,
            "failed": 0,
            "aborted": False,
            "reason": "",
            "last_verify_passed": False,
            "last_step_failed": False,
            "last_fail_reason": "",
            "current_target": None,
            "prev_step": None,
        }

    @staticmethod
    def _report(state: Dict[str, Any], t0: float) -> Dict[str, Any]:
        return {
            "steps_total": state["steps_total"],
            "steps_executed": state["executed"],
            "verified": state["verified"],
            "failed": state["failed"],
            "aborted": bool(state["aborted"]),
            "reason": state["reason"],
            "duration": time.perf_counter() - t0,
        }

    @staticmethod
    def _abort(state: Dict[str, Any], reason: str) -> None:
        if not state["aborted"]:
            state["aborted"] = True
            state["reason"] = reason

    def _sleep(
        self,
        delay: float,
        stop_check: Optional[Callable[[], bool]],
        state: Dict[str, Any],
    ) -> bool:
        """Sleep in poll_interval chunks; False = stop requested (aborts).

        v19.0.3: every chunk re-checks the cooperative stop conditions
        (``stop_check``, the module-wide cancel latch and the central
        safety gate) — a wait can never sleep through an E-STOP.
        """
        end = time.perf_counter() + max(0.0, delay)
        while True:
            stop_reason = _runner_stop_reason(stop_check)
            if stop_reason:
                self._abort(state, stop_reason)
                return False
            remaining = end - time.perf_counter()
            if remaining <= 0.0:
                return True
            time.sleep(min(self.poll_interval, remaining))

    @staticmethod
    def _params_point(params: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        """Extract a point from step params ({"x","y"} or {"point":[x,y]})."""
        pt = params.get("point")
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            try:
                return (float(pt[0]), float(pt[1]))
            except Exception:
                return None
        try:
            return (float(params["x"]), float(params["y"]))
        except Exception:
            return None

    def _provider_targets(self) -> List[ScreenTarget]:
        """Best-effort target list from the screen provider (never raises)."""
        p = self.screen_provider
        if p is None:
            return []
        try:
            if hasattr(p, "model"):
                model = p.model()
                return list(getattr(model, "targets", None) or [])
        except Exception:
            pass
        return list(getattr(p, "targets", None) or [])

    def _resolve_point(
        self, params: Dict[str, Any], state: Dict[str, Any]
    ) -> Optional[Tuple[float, float]]:
        """Click point: LOOK_FOR target centre → explicit params → None."""
        target = state.get("current_target")
        if target is not None:
            try:
                return (float(target.center[0]), float(target.center[1]))
            except Exception:
                pass
        return self._params_point(params)

    def _run_steps(
        self,
        steps: List[MacroStep],
        depth: int,
        state: Dict[str, Any],
        stop_check: Optional[Callable[[], bool]],
    ) -> None:
        for step in steps:
            if state["aborted"]:
                return
            stop_reason = _runner_stop_reason(stop_check)
            if stop_reason:
                self._abort(state, stop_reason)
                return
            if state["executed"] >= self.max_steps:
                self._abort(state, "max_steps")
                return
            if not isinstance(step, MacroStep):
                continue
            state["steps_total"] += 1
            ok = self._execute_one(step, depth, state, stop_check)
            if state["aborted"]:
                return
            state["executed"] += 1
            state["last_step_failed"] = not ok
            if ok:
                state["last_fail_reason"] = ""
            else:
                state["failed"] += 1
            if step.op != MacroOp.RETRY:
                state["prev_step"] = step

    def _execute_one(
        self,
        step: MacroStep,
        depth: int,
        state: Dict[str, Any],
        stop_check: Optional[Callable[[], bool]],
    ) -> bool:
        """Run a single step; True = success.  Never raises."""
        op = step.op
        params = dict(step.params or {})
        try:
            if op == MacroOp.LOOK_FOR:
                return self._do_look_for(params, state, stop_check)
            if op == MacroOp.WAIT_UNTIL:
                return self._do_wait(params, state, stop_check)
            if op in (MacroOp.CLICK, MacroOp.DOUBLE_CLICK, MacroOp.RIGHT_CLICK):
                return self._do_click(op, params, state)
            if op == MacroOp.TYPE:
                return bool(self.executor.type_text(str(params.get("text", ""))))
            if op == MacroOp.SCROLL:
                return bool(self.executor.scroll(int(params.get("amount", 0) or 0)))
            if op == MacroOp.HOTKEY:
                keys = params.get("keys") or []
                return bool(self.executor.hotkey([str(k) for k in keys]))
            if op == MacroOp.VERIFY:
                return self._do_verify(params, state)
            if op == MacroOp.IF:
                return self._do_if(params, depth, state, stop_check)
            if op == MacroOp.RETRY:
                return self._do_retry(params, state, depth, stop_check)
            if op == MacroOp.STOP:
                self._abort(state, "stop_op")
                return True
            state["last_fail_reason"] = f"unknown_op:{op}"
            return False
        except Exception as exc:
            state["last_fail_reason"] = f"executor_error:{exc}"
            return False

    # -- step handlers ------------------------------------------------------------

    def _do_look_for(
        self,
        params: Dict[str, Any],
        state: Dict[str, Any],
        stop_check: Optional[Callable[[], bool]],
    ) -> bool:
        """Poll the screen provider until the target appears (or timeout)."""
        text = params.get("text")
        ttype = params.get("type")
        deadline = time.perf_counter() + self.look_for_timeout
        while True:
            stop_reason = _runner_stop_reason(stop_check)
            if stop_reason:
                self._abort(state, stop_reason)
                return False
            target: Optional[ScreenTarget] = None
            try:
                if text and self.screen_provider is not None \
                        and hasattr(self.screen_provider, "find_by_text"):
                    target = self.screen_provider.find_by_text(str(text))
                elif ttype:
                    try:
                        want = ScreenTargetType(str(ttype))
                    except ValueError:
                        want = None
                    if want is not None:
                        cands = [t for t in self._provider_targets()
                                 if getattr(t, "type", None) == want]
                        if cands:  # nearest-of-type: highest confidence wins
                            target = max(cands,
                                         key=lambda t: getattr(t, "confidence", 0.0))
            except Exception:
                target = None
            if target is not None:
                state["current_target"] = target
                return True
            if time.perf_counter() >= deadline:
                state["last_fail_reason"] = "look_for_timeout"
                return False
            time.sleep(self.poll_interval)

    def _do_wait(
        self,
        params: Dict[str, Any],
        state: Dict[str, Any],
        stop_check: Optional[Callable[[], bool]],
    ) -> bool:
        """Wait {seconds} in poll_interval chunks; {fixation: true} is a
        passthrough no-op (fixation data arrives via fusion, not the
        screen provider, so there is nothing to poll here)."""
        if params.get("fixation"):
            return True
        try:
            seconds = float(params.get("seconds", 0.0) or 0.0)
        except Exception:
            seconds = 0.0
        seconds = min(seconds, self.step_timeout)
        return self._sleep(seconds, stop_check, state)

    def _do_click(
        self, op: MacroOp, params: Dict[str, Any], state: Dict[str, Any]
    ) -> bool:
        """Safety-gated click execution at the resolved point."""
        point = self._resolve_point(params, state)
        if point is None:
            state["last_fail_reason"] = "no_target"
            return False
        if self.safety is not None:
            intent = Intent(
                type=_OP_TO_INTENT.get(op, IntentType.CLICK),
                point=point,
                confidence=0.9,
                sources=Modality.NONE,
                timestamp=now_ts(),
            )
            try:
                decision = self.safety.approve_intent(intent)
            except Exception:
                state["last_fail_reason"] = "safety_error"
                return False
            if decision is None or not getattr(decision, "allowed", False):
                state["last_fail_reason"] = (
                    f"safety:{getattr(decision, 'reason', 'blocked') or 'blocked'}"
                )
                return False
        ex = self.executor
        if op == MacroOp.CLICK:
            return bool(ex.click(point[0], point[1]))
        if op == MacroOp.DOUBLE_CLICK:
            return bool(ex.double_click(point[0], point[1]))
        return bool(ex.right_click(point[0], point[1]))

    def _do_verify(self, params: Dict[str, Any], state: Dict[str, Any]) -> bool:
        """VERIFY {expected} — assert screen state via the verifier.

        With no verifier attached the step is an optimistic passthrough
        (``last_verify_passed`` stays True so downstream IF branches are
        deterministic); with a verifier, ``observe_fn=None`` is passed (the
        runner has no pixel observer of its own).
        """
        expected = params.get("expected")
        if not isinstance(expected, dict):
            expected = {k: v for k, v in params.items()
                        if k not in ("comment",)} or params
        if self.verifier is None:
            state["last_verify_passed"] = True
            return True
        target = state.get("current_target")
        point = None
        if target is not None:
            try:
                point = (float(target.center[0]), float(target.center[1]))
            except Exception:
                point = None
        plan_stub = ActionPlan(
            action=ActionType.CLICK,
            point=point,
            target=target if isinstance(target, ScreenTarget) else None,
            expected=dict(expected),
        )
        report_stub = ActionReport(status=ActionStatus.SUCCESS)
        try:
            result = self.verifier.verify(plan_stub, report_stub, None)
        except Exception:
            result = None
        status = getattr(result, "status", VerificationStatus.UNKNOWN)
        if status == VerificationStatus.PASSED:
            state["verified"] += 1
            state["last_verify_passed"] = True
            return True
        state["last_verify_passed"] = False
        state["last_fail_reason"] = f"verify_{getattr(status, 'value', status)}"
        return False

    def _do_if(
        self,
        params: Dict[str, Any],
        depth: int,
        state: Dict[str, Any],
        stop_check: Optional[Callable[[], bool]],
    ) -> bool:
        """IF {then: [...], else: [...]} on the last VERIFY result."""
        if depth + 1 > self.max_if_depth:
            self._abort(state, "max_if_depth")
            return False
        branch = "then" if state.get("last_verify_passed") else "else"
        steps = params.get(branch) or []
        if not isinstance(steps, list):
            steps = []
        self._run_steps(list(steps), depth + 1, state, stop_check)
        return not state["last_step_failed"] and not state["aborted"]

    def _do_retry(
        self,
        params: Dict[str, Any],
        state: Dict[str, Any],
        depth: int,
        stop_check: Optional[Callable[[], bool]],
    ) -> bool:
        """RETRY {times: n} — re-run the previous step until it succeeds."""
        try:
            times = max(0, int(params.get("times", 1)))
        except Exception:
            times = 1
        prev = state.get("prev_step")
        if prev is None or not isinstance(prev, MacroStep) or prev.op == MacroOp.RETRY:
            state["last_fail_reason"] = "retry_no_previous_step"
            return False
        for _ in range(times):
            if state["aborted"]:
                return False
            stop_reason = _runner_stop_reason(stop_check)
            if stop_reason:
                self._abort(state, stop_reason)
                return False
            if state["executed"] >= self.max_steps:
                self._abort(state, "max_steps")
                return False
            state["steps_total"] += 1
            ok = self._execute_one(prev, depth, state, stop_check)
            state["executed"] += 1
            state["last_step_failed"] = not ok
            if ok:
                return True
        state["last_fail_reason"] = "retry_exhausted"
        return False
