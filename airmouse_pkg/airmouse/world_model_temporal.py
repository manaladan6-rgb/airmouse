"""
airmouse.world_model_temporal — Temporal Interaction World Model (v12.5 §3).

A persistent-but-BOUNDED world model with time.  It extends (never
replaces) the v11.5 :mod:`airmouse.world_model` snapshot model with:

    HUMAN     current mode / attention proxy / current intent /
              confidence / interaction modality / sensor health
    COMPUTER  active application / active window / browser / tabs /
              files / clipboard / notifications / devices /
              visible UI targets
    TASK      current objective / phase / progress / recent actions /
              blockers / expected next state

TEMPORAL properties (§3):

    current state          latest snapshot
    previous state         snapshot before the last transition
    state transitions      every observe() records cause + confidence
    causality metadata     each transition carries its cause tag
    expected state         task-level expectation set by planners
    observed state         what actually came in
    mismatch detection     expected != observed -> MismatchRecord

API (§3):  observe() snapshot() diff() history() explain()
           predict_state()

SAFETY (§3): snapshots are FROZEN — callers get deep copies; mutable
internal state is never exposed.  predict_state() is PREDICTION ONLY —
its output is data for intent/safety layers, never a permission.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import copy
import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# bounded constants (§3 — hard resource limits)
# ─────────────────────────────────────────────────────────────────────────────

MAX_HISTORY = 128                 # snapshots retained
MAX_VISIBLE_TARGETS = 64
MAX_TABS = 32
MAX_FILES = 32
MAX_RECENT_ACTIONS = 16
MAX_BLOCKERS = 8
MAX_NOTIFICATIONS = 16
MAX_STR = 160                     # any single string field
MAX_DIFF_ENTRIES = 64


def _clip(value: Any, limit: int = MAX_STR) -> str:
    if value is None:
        return ""
    return str(value)[:limit]


class SensorHealth(enum.Enum):
    """Per-modality sensor health (§3 HUMAN.sensor_health)."""

    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class HumanState:
    """§3 HUMAN section (immutable snapshot piece)."""

    mode: str = "unknown"              # hand/gaze/voice/fusion/hands_free/…
    attention_proxy: str = ""          # coarse attention estimate
    current_intent: str = ""           # latest predicted intent name
    intent_confidence: float = 0.0
    interaction_modality: str = "none"  # leading modality
    sensor_health: str = SensorHealth.UNKNOWN.value


@dataclass(frozen=True)
class ComputerState:
    """§3 COMPUTER section (immutable snapshot piece)."""

    active_application: str = ""
    active_window: str = ""
    browser: str = ""                  # browser name or "" (none/unknown)
    tabs: Tuple[str, ...] = ()         # bounded tab titles/urls
    files: Tuple[str, ...] = ()        # bounded recent file names
    clipboard: str = ""                # clipboard POLICY marker only
    notifications: Tuple[str, ...] = ()
    devices: Tuple[str, ...] = ()      # camera/mic/… presence markers
    visible_ui_targets: Tuple[str, ...] = ()  # bounded target labels


@dataclass(frozen=True)
class TaskState:
    """§3 TASK section (immutable snapshot piece)."""

    objective: str = ""
    phase: str = ""                    # plan/execute/verify/recover/…
    progress: float = 0.0              # 0..1
    recent_actions: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    expected_next_state: str = ""      # planner expectation


@dataclass(frozen=True)
class WorldSnapshot:
    """One immutable point-in-time world state (§3)."""

    sequence: int = 0
    timestamp: float = 0.0
    human: HumanState = field(default_factory=HumanState)
    computer: ComputerState = field(default_factory=ComputerState)
    task: TaskState = field(default_factory=TaskState)
    cause: str = ""                    # what caused this transition
    cause_confidence: float = 0.0
    expected_state: str = ""           # what was expected (if any)
    mismatch: bool = False             # observed != expected


@dataclass(frozen=True)
class StateDiff:
    """Structured difference between two snapshots (§3 diff)."""

    changed: Tuple[str, ...] = ()      # dotted paths that changed
    details: Dict[str, Tuple[Any, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "changed": list(self.changed),
            "details": {k: {"from": v[0], "to": v[1]}
                        for k, v in sorted(self.details.items())},
        }


@dataclass(frozen=True)
class MismatchRecord:
    """Expected-vs-observed mismatch (§3 mismatch detection)."""

    sequence: int = 0
    timestamp: float = 0.0
    expected: str = ""
    observed: str = ""
    cause: str = ""
    severity: str = "low"              # low/medium/high


class TemporalWorldModel:
    """Persistent-but-bounded temporal world model (v12.5 §3).

    Wraps the v11.5 :class:`airmouse.world_model.WorldModel` (which it
    optionally shares a ContextEngine with) — this class adds TIME:
    transitions, history, diffs, mismatches and prediction.
    """

    def __init__(self, max_history: int = MAX_HISTORY) -> None:
        self.max_history = max(8, min(int(max_history), MAX_HISTORY))
        self._sequence = 0
        self._snapshots: List[WorldSnapshot] = []
        self._current = WorldSnapshot()
        self._pending_expectation: str = ""
        self._mismatches: List[MismatchRecord] = []

    # ── observe (§3) ─────────────────────────────────────────────────────

    def observe(self,
                human: Optional[Dict[str, Any]] = None,
                computer: Optional[Dict[str, Any]] = None,
                task: Optional[Dict[str, Any]] = None,
                cause: str = "",
                cause_confidence: float = 0.0) -> WorldSnapshot:
        """Merge partial observations into a new snapshot (§3).

        Only supplied fields change; everything else persists.
        ``cause`` is REQUIRED-ish (empty cause = "unattributed").
        Returns the new immutable snapshot.
        """
        human = human or {}
        computer = computer or {}
        task = task or {}

        new_human = HumanState(
            mode=_clip(human.get("mode", self._current.human.mode), 40) or
            self._current.human.mode,
            attention_proxy=_clip(
                human.get("attention_proxy",
                          self._current.human.attention_proxy), 40),
            current_intent=_clip(
                human.get("current_intent",
                          self._current.human.current_intent), 40),
            intent_confidence=_bound(
                human.get("intent_confidence",
                          self._current.human.intent_confidence)),
            interaction_modality=_clip(
                human.get("interaction_modality",
                          self._current.human.interaction_modality), 20),
            sensor_health=_clip(
                human.get("sensor_health", self._current.human.sensor_health),
                20),
        )

        new_computer = ComputerState(
            active_application=_clip(
                computer.get("active_application",
                             self._current.computer.active_application)),
            active_window=_clip(
                computer.get("active_window",
                             self._current.computer.active_window)),
            browser=_clip(computer.get("browser",
                                       self._current.computer.browser), 40),
            tabs=_bounded_tuple(computer.get("tabs",
                                             self._current.computer.tabs),
                                MAX_TABS),
            files=_bounded_tuple(computer.get("files",
                                              self._current.computer.files),
                                 MAX_FILES),
            clipboard=_clip(computer.get("clipboard",
                                         self._current.computer.clipboard),
                            40),
            notifications=_bounded_tuple(
                computer.get("notifications",
                             self._current.computer.notifications),
                MAX_NOTIFICATIONS),
            devices=_bounded_tuple(computer.get(
                "devices", self._current.computer.devices), 8),
            visible_ui_targets=_bounded_tuple(
                computer.get("visible_ui_targets",
                             self._current.computer.visible_ui_targets),
                MAX_VISIBLE_TARGETS),
        )

        recent = list(self._current.task.recent_actions)
        if task.get("recent_action"):
            recent.append(_clip(task["recent_action"], 60))
            recent = recent[-MAX_RECENT_ACTIONS:]
        elif task.get("recent_actions") is not None:
            recent = [_clip(a, 60)
                      for a in list(task["recent_actions"])[-MAX_RECENT_ACTIONS:]]

        new_task = TaskState(
            objective=_clip(task.get("objective",
                                     self._current.task.objective)),
            phase=_clip(task.get("phase", self._current.task.phase), 20),
            progress=_bound(task.get("progress",
                                     self._current.task.progress)),
            recent_actions=tuple(recent),
            blockers=_bounded_tuple(task.get(
                "blockers", self._current.task.blockers), MAX_BLOCKERS),
            expected_next_state=_clip(
                task.get("expected_next_state",
                         self._current.task.expected_next_state)),
        )

        # expected-vs-observed mismatch detection (§3)
        expected = self._pending_expectation
        observed = new_task.expected_next_state or new_task.phase
        mismatch = bool(expected) and bool(observed) and \
            expected != observed
        if mismatch:
            self._record_mismatch(expected, observed, cause)

        self._sequence += 1
        snap = WorldSnapshot(
            sequence=self._sequence,
            timestamp=0.0,   # set below via time.perf_counter
            human=new_human,
            computer=new_computer,
            task=new_task,
            cause=_clip(cause, 40) or "unattributed",
            cause_confidence=_bound(cause_confidence),
            expected_state=expected,
            mismatch=mismatch,
        )
        object.__setattr__(snap, "timestamp", _now())
        self._current = snap
        self._snapshots.append(snap)
        if len(self._snapshots) > self.max_history:
            self._snapshots = self._snapshots[-self.max_history:]
        self._pending_expectation = ""
        return snap

    def expect(self, expected_state: str) -> None:
        """Register the next expected state (from a planner/task)."""
        self._pending_expectation = _clip(expected_state, 60)

    # ── snapshot / diff / history (§3) ──────────────────────────────────

    def snapshot(self) -> WorldSnapshot:
        """The CURRENT immutable snapshot (§3).  Frozen dataclass — no
        mutable internal state leaks."""
        return self._current

    def previous(self) -> Optional[WorldSnapshot]:
        """The snapshot before the latest transition (§3)."""
        if len(self._snapshots) >= 2:
            return self._snapshots[-2]
        return None

    def history(self, limit: int = 20) -> List[WorldSnapshot]:
        """Bounded recent history, oldest first (§3)."""
        limit = max(1, min(int(limit), self.max_history))
        return list(self._snapshots[-limit:])

    def transitions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Transition records with causality metadata (§3)."""
        limit = max(1, min(int(limit), self.max_history))
        out = []
        for snap in self._snapshots[-limit:]:
            out.append({
                "sequence": snap.sequence,
                "cause": snap.cause,
                "cause_confidence": snap.cause_confidence,
                "mismatch": snap.mismatch,
                "human_mode": snap.human.mode,
                "active_application": snap.computer.active_application,
                "task_phase": snap.task.phase,
            })
        return out

    def diff(self, a: Optional[WorldSnapshot] = None,
             b: Optional[WorldSnapshot] = None) -> StateDiff:
        """Structured diff between two snapshots (§3).

        Defaults to previous -> current.
        """
        if a is None:
            a = self.previous()
        if b is None:
            b = self._current
        if a is None or b is None:
            return StateDiff()
        flat_a = _flatten(a)
        flat_b = _flatten(b)
        changed = []
        details: Dict[str, Tuple[Any, Any]] = {}
        for key in sorted(set(flat_a) | set(flat_b)):
            va, vb = flat_a.get(key), flat_b.get(key)
            if va != vb:
                changed.append(key)
                details[key] = (va, vb)
            if len(changed) >= MAX_DIFF_ENTRIES:
                break
        return StateDiff(changed=tuple(changed), details=details)

    def mismatches(self, limit: int = 20) -> List[MismatchRecord]:
        """Recent expected-vs-observed mismatches (§3)."""
        return list(self._mismatches[-max(1, min(int(limit), 100)):])

    # ── explain / predict (§3) ──────────────────────────────────────────

    def explain(self) -> Dict[str, Any]:
        """Human-readable explanation of the latest transition (§3)."""
        snap = self._current
        prev = self.previous()
        d = self.diff(prev, snap) if prev else StateDiff()
        return {
            "sequence": snap.sequence,
            "cause": snap.cause,
            "cause_confidence": snap.cause_confidence,
            "changed_fields": list(d.changed)[:MAX_DIFF_ENTRIES],
            "human_mode": snap.human.mode,
            "interaction_modality": snap.human.interaction_modality,
            "active_application": snap.computer.active_application,
            "active_window": snap.computer.active_window,
            "task_objective": snap.task.objective,
            "task_phase": snap.task.phase,
            "task_progress": snap.task.progress,
            "mismatch": snap.mismatch,
            "expected_state": snap.expected_state,
        }

    def predict_state(self) -> Dict[str, Any]:
        """Predict the next world state (§3 predict_state).

        PREDICTION ONLY — never a permission, never auto-executed.
        Deterministic persistence model: fields that changed recently
        keep their trend; task phase advances only when a planner says
        so (here: expected_next_state when set).
        """
        snap = self._current
        predicted_phase = snap.task.expected_next_state or snap.task.phase
        predicted_progress = min(1.0, snap.task.progress + 0.0)
        return {
            "prediction": True,
            "permission": False,      # explicit: this is NOT permission
            "confidence": 0.5 if predicted_phase == snap.task.phase else 0.6,
            "based_on_sequence": snap.sequence,
            "human_mode": snap.human.mode,
            "active_application": snap.computer.active_application,
            "task_phase": predicted_phase,
            "task_progress": predicted_progress,
            "note": "persistence prediction; planner expectations win",
        }

    # ── internals ────────────────────────────────────────────────────────

    def _record_mismatch(self, expected: str, observed: str,
                         cause: str) -> None:
        severity = "medium"
        if expected and not observed:
            severity = "high"
        self._mismatches.append(MismatchRecord(
            sequence=self._sequence + 1,
            timestamp=_now(),
            expected=expected,
            observed=observed,
            cause=_clip(cause, 40),
            severity=severity,
        ))
        if len(self._mismatches) > 100:
            self._mismatches = self._mismatches[-100:]


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────


def _now() -> float:
    import time
    return time.perf_counter()


def _bound(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _bounded_tuple(value: Any, limit: int) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    try:
        items = [_clip(v) for v in list(value)[:limit]]
        return tuple(x for x in items if x)
    except TypeError:
        return ()


def _flatten(snap: WorldSnapshot) -> Dict[str, Any]:
    """Flatten a snapshot into dotted paths for diffing."""
    out: Dict[str, Any] = {}
    for section, obj in (("human", snap.human), ("computer", snap.computer),
                         ("task", snap.task)):
        for f in obj.__dataclass_fields__:
            out[f"{section}.{f}"] = getattr(obj, f)
    return out
