"""AirMouse v16.5 — Temporal Gesture Intelligence (mission §10–§14).

Moves gesture understanding from STATIC pose matching to TEMPORAL
understanding: trajectory, velocity, acceleration, duration, direction,
hand shape, handedness, confidence, and start/transition/release states.

A pinch is no longer a frame — it is a LIFECYCLE:

    PINCH_START → PINCH_HOLD → PINCH_MOVE → PINCH_RELEASE (+ DOUBLE_PINCH)

with compositions that turn episodes into unified intent proposals:

    pointing → pinch                      → "select_target"
    pointing → pinch → movement → release → "drag"
    pinch + dominant vertical movement    → "scroll" (amount ∝ displacement)
    two-hand pinch distance change        → "zoom"
    gaze target + pinch                   → "activate_gaze_target"

PREDICTION NEVER EQUALS EXECUTION
---------------------------------
All outputs are intents/proposals. Dispatch happens ONLY through
airmouse.gesture_spine.GestureActionRouter / airmouse.intent. This module
performs no OS actions and imports no input automation libraries. Every
proposal dict carries ``"requires": "spine_dispatch"`` so the coordinator
can never confuse a prediction with a performed action; unknown or
unwired patterns are reported honestly with ``os_action="NOT MAPPED"``
(mission §13).

Design constraints
------------------
- Deterministic: same input history → same outputs (all time is injected;
  no clocks, no sleeps, no threads).
- Lightweight: pure Python float math, bounded memory (a capped deque +
  scalars). No neural networks, no new dependencies.
- Robust by construction (mission §14): confidence hysteresis, event
  debouncing, false-positive suppression (N frames to enter / M to exit),
  tracking-loss grace period, camera watchdog, sensor health score.
- CompositionResolver is the multimodal unifier (mission §12/§16): voice +
  gaze + gesture candidates collapse into EXACTLY ONE unified intent
  proposal per tick — never duplicate execution.

Coordinator wiring (suggested live-loop flow, one tick at a time)::

    buf.append(sample)                      # TrajectoryBuffer per frame
    ev = lifecycle.update(sample, now)      # pinch events (or None)
    if two_hand_report["active"]:
        proposal = recognizer.recognize_two_hand(two_hand_report)
    detections = recognizer.recognize(buf)  # periodic (e.g. every N frames)
    unified = resolver.resolve(voice_intent=..., gaze_target=...,
                               gesture_event=ev, context=...)
    # → hand `unified` / event "proposal" names to IntentEngine.submit_gesture
    #   / GestureActionRouter.can_execute + dispatch (the ONLY executors).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

try:  # package-relative (normal import path) — label-string alignment only
    from .gestures import Gesture as _Gesture
except Exception:  # pragma: no cover — standalone import fallback
    _Gesture = None

__all__ = [
    "Sample",
    "TrajectoryBuffer",
    "PinchLifecycle",
    "PINCH_EVENTS",
    "TemporalRecognizer",
    "HoldDetection",
    "MotionDetection",
    "SequenceDetection",
    "Composition",
    "COMPOSITION_TABLE",
    "CompositionResolver",
    "Hysteresis",
    "Debouncer",
    "FalsePositiveSuppressor",
    "TrackingRecovery",
    "CameraWatchdog",
    "SensorHealthScore",
    "PINCH_LABEL",
    "POINTING_LABEL",
    "NONE_LABEL",
]

# ── canonical label constants (aligned with airmouse.gestures.Gesture) ──
PINCH_LABEL = _Gesture.PINCH if _Gesture is not None else "pinch"
POINTING_LABEL = _Gesture.POINTING if _Gesture is not None else "pointing"
NONE_LABEL = _Gesture.NONE if _Gesture is not None else "none"

#: Event names emitted by :class:`PinchLifecycle` (mission §11 lifecycle).
PINCH_EVENTS: Tuple[str, ...] = (
    "pinch_start", "pinch_hold", "pinch_move", "pinch_release",
    "double_pinch",
)

#: Documented composition table (mission §12) — pattern → unified intent
#: name.  The intent names are proposals; the spine owns the real actions
#: (drag → start_drag/stop_drag, scroll → scroll, zoom → zoom, ...).
COMPOSITION_TABLE: Dict[str, str] = {
    "pointing->pinch": "select_target",
    "pointing->pinch->move->release": "drag",
    "pinch+vertical": "scroll",
    "two_hand_distance": "zoom",
    "gaze+pinch": "activate_gaze_target",
}

_DEBOUNCED_EVENTS = ("pinch_start", "pinch_hold", "pinch_move")
_DIRECTION_AXES = ("up", "down", "left", "right", "none")


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def _centroid(landmarks: Any) -> Optional[Tuple[float, float]]:
    """Centroid of an iterable of (x, y) pairs (or ``.x``/``.y`` objects).

    Returns ``None`` when absent/empty — callers treat that as "position
    unknown this frame" (never as (0, 0), which would fake motion).
    """
    if not landmarks:
        return None
    sx = 0.0
    sy = 0.0
    n = 0
    for p in landmarks:
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError, KeyError):
            try:
                x, y = float(p.x), float(p.y)
            except AttributeError:
                return None
        sx += x
        sy += y
        n += 1
    if n == 0:
        return None
    return (sx / n, sy / n)


def _dominant_direction(dx: float, dy: float,
                        deadzone: float) -> str:
    """Dominant axis of a net (dx, dy) displacement (image coords: y grows
    DOWN, so dy < 0 is "up").  Inside the deadzone → "none".  Exact axis
    ties resolve horizontally (deterministic)."""
    if max(abs(dx), abs(dy)) <= deadzone:
        return "none"
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


# ---------------------------------------------------------------------------
# 1) Sample — one temporal observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    """One temporal observation (one frame's worth of recognition output).

    t           seconds (caller-injected monotonic time; never a clock).
    landmarks   tuple of normalized (x, y) pairs, or None when the hand
                shape was not observed this frame.
    label       gesture label string (airmouse.gestures.Gesture vocabulary,
                "none" when no pose).
    confidence  0..1 recognition confidence.
    handedness  "Left" / "Right" / "" when unknown.
    """

    t: float
    landmarks: Optional[Tuple[Tuple[float, float], ...]] = None
    label: str = NONE_LABEL
    confidence: float = 0.0
    handedness: str = ""

    @property
    def position(self) -> Optional[Tuple[float, float]]:
        """Centroid of ``landmarks`` (None when landmarks are absent)."""
        return _centroid(self.landmarks)


# ---------------------------------------------------------------------------
# 2) TrajectoryBuffer — bounded temporal window + pure feature extraction
# ---------------------------------------------------------------------------


class TrajectoryBuffer:
    """Bounded FIFO of :class:`Sample` with pure, unit-testable features.

    maxlen default 240 ≈ 8 s at 30 fps.  ``features(window_s=None)``
    computes everything from the buffered samples only (optionally limited
    to the last ``window_s`` seconds) and never raises on empty/degenerate
    input — safe defaults instead.
    """

    def __init__(self, maxlen: int = 240,
                 direction_deadzone: float = 0.0) -> None:
        """``direction_deadzone`` applies to the MEAN per-step displacement
        (image coords); 0.0 = every real movement has a direction."""
        self._samples: Deque[Sample] = deque(maxlen=max(1, int(maxlen)))
        self.direction_deadzone = float(direction_deadzone)
    # ── container API ─────────────────────────────────────────────────
    def append(self, sample: Sample) -> None:
        """Append one sample (oldest dropped beyond ``maxlen``)."""
        self._samples.append(sample)

    def clear(self) -> None:
        self._samples.clear()

    def __len__(self) -> int:
        return len(self._samples)

    def samples(self) -> List[Sample]:
        return list(self._samples)

    def last(self) -> Optional[Sample]:
        return self._samples[-1] if self._samples else None

    # ── features ──────────────────────────────────────────────────────
    def features(self, window_s: Optional[float] = None) -> Dict[str, Any]:
        """Pure feature extraction over the buffer (optionally the last
        ``window_s`` seconds).  All numeric outputs are finite floats;
        ``dominant_direction`` ∈ {up, down, left, right, none}."""
        samples = list(self._samples)
        if window_s is not None and samples:
            cutoff = samples[-1].t - float(window_s)
            samples = [s for s in samples if s.t >= cutoff]

        empty: Dict[str, Any] = {
            "duration": 0.0, "path_length": 0.0, "displacement": 0.0,
            "mean_velocity": 0.0, "max_velocity": 0.0,
            "mean_acceleration": 0.0, "dominant_direction": "none",
            "start_label": "", "end_label": "", "label_changes": [],
            "handedness_stability": 0.0, "n_samples": 0,
        }
        if not samples:
            return empty

        first_t = float(samples[0].t)
        last_t = float(samples[-1].t)
        duration = max(0.0, last_t - first_t)

        # positions (None where landmarks absent)
        positions = [s.position for s in samples]

        # path length + per-step velocities (guarded against zero dt)
        path_length = 0.0
        n_steps = 0
        vel_points: List[Tuple[float, float]] = []   # (t, speed)
        for i in range(len(samples) - 1):
            p0, p1 = positions[i], positions[i + 1]
            if p0 is None or p1 is None:
                continue
            n_steps += 1
            dt = float(samples[i + 1].t) - float(samples[i].t)
            step = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            path_length += step
            if dt > 1e-9:
                vel_points.append((float(samples[i + 1].t), step / dt))

        # displacement (net, first→last observed position)
        first_pos = next((p for p in positions if p is not None), None)
        last_pos = next((p for p in reversed(positions) if p is not None), None)
        if first_pos is not None and last_pos is not None:
            dx = last_pos[0] - first_pos[0]
            dy = last_pos[1] - first_pos[1]
            displacement = math.hypot(dx, dy)
        else:
            dx = dy = 0.0
            displacement = 0.0

        mean_velocity = path_length / duration if duration > 1e-9 else 0.0
        max_velocity = max((v for _, v in vel_points), default=0.0)

        # mean acceleration: |Δv|/dt over consecutive velocity estimates
        accs: List[float] = []
        for (ta, va), (tb, vb) in zip(vel_points, vel_points[1:]):
            dt = tb - ta
            if dt > 1e-9:
                accs.append(abs(vb - va) / dt)
        mean_acceleration = (sum(accs) / len(accs)) if accs else 0.0

        # label runs
        label_changes: List[Tuple[float, str, str]] = []
        prev_label = samples[0].label
        for s in samples[1:]:
            if s.label != prev_label:
                label_changes.append((float(s.t), prev_label, s.label))
                prev_label = s.label

        # handedness stability: share of the dominant non-empty handedness
        hands = [s.handedness for s in samples if s.handedness]
        if hands:
            dominant = max(set(hands), key=hands.count)  # deterministic tie:
            # max() keeps the FIRST maximal element in iteration order
            stability = hands.count(dominant) / float(len(hands))
        else:
            stability = 0.0

        return {
            "duration": duration,
            "path_length": path_length,
            "displacement": displacement,
            "mean_velocity": mean_velocity,
            "max_velocity": max_velocity,
            "mean_acceleration": mean_acceleration,
            "dominant_direction": (
                _dominant_direction(dx / n_steps, dy / n_steps,
                                    self.direction_deadzone)
                if n_steps else "none"),
            "start_label": str(samples[0].label or ""),
            "end_label": str(samples[-1].label or ""),
            "label_changes": label_changes,
            "handedness_stability": stability,
            "n_samples": len(samples),
        }


# ---------------------------------------------------------------------------
# 3) PinchLifecycle — deterministic pinch state machine
# ---------------------------------------------------------------------------


class PinchLifecycle:
    """Deterministic pinch lifecycle over incoming :class:`Sample` frames.

    States::

        IDLE ──(N consecutive pinch frames, conf ≥ enter)──► ENGAGED
        ENGAGED ──(movement > move_deadzone)──► moving (PINCH_MOVE)
        ENGAGED ──(no movement ≥ hold_s)──────► held (PINCH_HOLD, once)
        ENGAGED ──(M non-pinch/weak frames, or grace expiry)──► RELEASE

    Event dicts (one per :meth:`update` at most)::

        {"event": "pinch_start"|"pinch_hold"|"pinch_move"|"pinch_release"
                  |"double_pinch",
         "confidence": float,
         "duration_s": float,          # since pinch_start
         "displacement": float,        # net from engagement anchor
         "dominant_direction": "up"|"down"|"left"|"right"|"none",
         "proposal": None|"left_click"|"double_click"|"drag"|"scroll"
                     |"stop_drag",     # intent-name proposal for the spine
         "amount": int,                # signed scroll ticks (scroll only)
         "reason": "normal"|"tracking_lost"}

    Semantics (1:1 with the existing Gesture.PINCH vocabulary so the
    coordinator can map directly):
      - quick tap (< tap_max_s, ≤ tap_max_displacement) → release carries
        ``proposal="left_click"`` (the PINCH click);
      - two quick taps within ``double_pinch_window`` → ``double_pinch``
        (proposal ``double_click``) instead of the second release;
      - pinch + horizontal/2-D movement → ``pinch_move`` with
        ``proposal="drag"`` (start_drag on the spine); release after
        movement → ``proposal="stop_drag"``;
      - pinch + sustained vertical movement → ``pinch_move`` with
        ``proposal="scroll"`` and signed ``amount`` ticks (∝ |dy| / scroll_tick;
        image y grows down, so hand up = scroll up = positive);
      - pinch held without movement ≥ hold_s → one ``pinch_hold``.

    Robustness (mission §14), all thresholds constructor-injectable:
      - enter/exit confidence HYSTERESIS (enter_frames strong frames to
        engage; between exit/enter keeps the state; below exit for
        exit_frames consecutive frames releases);
      - DEBOUNCE: pinch_start/hold/move re-emits are suppressed within
        min_event_interval_s (releases/double-pinches are safety-critical
        and are NEVER suppressed);
      - tracking-loss GRACE: a missing/none frame within grace_s holds the
        state silently; past grace_s a pinch_release (reason
        "tracking_lost") is emitted so a drag can never hang;
      - false-positive suppression: enter needs N consecutive confident
        frames, exit needs M consecutive weak frames.
    """

    def __init__(self,
                 enter_confidence: float = 0.65,
                 exit_confidence: float = 0.45,
                 enter_frames: int = 2,
                 exit_frames: int = 3,
                 move_deadzone: float = 0.02,
                 hold_s: float = 0.5,
                 tap_max_s: float = 0.35,
                 tap_max_displacement: float = 0.02,
                 double_pinch_window: float = 0.6,
                 min_event_interval_s: float = 0.05,
                 grace_s: float = 0.35,
                 scroll_tick: float = 0.08) -> None:
        self.enter_confidence = float(enter_confidence)
        self.exit_confidence = float(exit_confidence)
        self.enter_frames = max(1, int(enter_frames))
        self.exit_frames = max(1, int(exit_frames))
        self.move_deadzone = float(move_deadzone)
        self.hold_s = float(hold_s)
        self.tap_max_s = float(tap_max_s)
        self.tap_max_displacement = float(tap_max_displacement)
        self.double_pinch_window = float(double_pinch_window)
        self.min_event_interval_s = float(min_event_interval_s)
        self.grace_s = float(grace_s)
        self.scroll_tick = float(scroll_tick)
        self.reset()

    # ── state ─────────────────────────────────────────────────────────
    def reset(self) -> None:
        """Back to IDLE (tap memory kept: double-pinch spans resets)."""
        self.state = "idle"
        self._enter_count = 0
        self._exit_count = 0
        self._anchor: Optional[Tuple[float, float]] = None
        self._last_pos: Optional[Tuple[float, float]] = None
        self._last_emit_pos: Optional[Tuple[float, float]] = None
        self._start_t = 0.0
        self._last_good_t = 0.0
        self._held_fired = False
        self._moved = False
        self._last_conf = 0.0
        self._last_tap_t = -1e9
        self._debounce = Debouncer(self.min_event_interval_s)

    @property
    def engaged(self) -> bool:
        """True while a pinch episode is active (introspection only)."""
        return self.state == "engaged"

    # ── main entry ────────────────────────────────────────────────────
    def update(self, sample: Optional[Sample],
               now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Feed one frame; return at most one event dict (or None).

        ``sample=None`` means "no frame at all" (tracking loss) and
        requires an explicit ``now``.  ``now`` defaults to ``sample.t``.
        """
        if sample is None:
            if now is None:
                return None
            return self._on_missing(float(now))
        t = float(now) if now is not None else float(sample.t)
        self._last_conf = float(max(0.0, min(1.0, sample.confidence)))
        label = str(sample.label or "")
        pos = sample.position

        if label == PINCH_LABEL:
            if self._last_conf >= self.exit_confidence:
                self._last_good_t = t
                self._exit_count = 0
                if self.state == "idle":
                    if self._last_conf >= self.enter_confidence:
                        self._enter_count += 1
                        if self._enter_count >= self.enter_frames:
                            return self._engage(t, pos)
                    else:
                        # in the hysteresis band but not inside: a strong
                        # ENTER streak must be consecutive — reset it.
                        self._enter_count = 0
                    return None
                return self._on_engaged(t, pos)
            if self.state == "idle":
                self._enter_count = 0
                return None
            return self._count_exit(t)          # weak pinch frame

        if label in ("", NONE_LABEL, "none"):
            return self._on_missing(t)

        # a different real pose
        if self.state == "idle":
            self._enter_count = 0
            return None
        return self._count_exit(t)

    # ── internals ─────────────────────────────────────────────────────
    def _engage(self, t: float,
                pos: Optional[Tuple[float, float]]) -> Optional[Dict[str, Any]]:
        self.state = "engaged"
        self._enter_count = 0
        self._exit_count = 0
        self._anchor = pos
        self._last_pos = pos
        self._last_emit_pos = pos
        self._start_t = t
        self._held_fired = False
        self._moved = False
        return self._event("pinch_start", t, 0.0, 0.0, "none", None)

    def _on_engaged(self, t: float,
                    pos: Optional[Tuple[float, float]]) -> Optional[Dict[str, Any]]:
        if pos is not None:
            self._last_pos = pos
        anchor = self._anchor
        if pos is not None and anchor is not None:
            dx = pos[0] - anchor[0]
            dy = pos[1] - anchor[1]
            displacement = math.hypot(dx, dy)
            direction = _dominant_direction(dx, dy, self.move_deadzone)
        else:
            dx = dy = 0.0
            displacement = 0.0
            direction = "none"
        duration = max(0.0, t - self._start_t)

        # movement crossing — drag or scroll proposal
        if not self._moved and displacement > self.move_deadzone:
            self._moved = True
            proposal, amount = self._move_proposal(direction, dy)
            ev = self._event("pinch_move", t, duration, displacement,
                             direction, proposal, amount)
            if ev is not None:
                self._last_emit_pos = pos
            return ev
        # continued movement — stream updates, throttled by deadzone+debounce
        if self._moved and pos is not None:
            ref = self._last_emit_pos if self._last_emit_pos is not None \
                else self._anchor
            if ref is not None:
                step = math.hypot(pos[0] - ref[0], pos[1] - ref[1])
                if step > self.move_deadzone:
                    proposal, amount = self._move_proposal(direction, dy)
                    ev = self._event("pinch_move", t, duration, displacement,
                                     direction, proposal, amount)
                    if ev is not None:
                        self._last_emit_pos = pos
                    return ev
        # hold — once per episode, only before any movement
        if not self._held_fired and not self._moved and duration >= self.hold_s:
            self._held_fired = True
            return self._event("pinch_hold", t, duration, displacement,
                               direction, None)
        return None

    def _move_proposal(self, direction: str,
                       dy: float) -> Tuple[Optional[str], int]:
        """Vertical pinch movement → scroll proposal (signed ticks);
        horizontal/2-D movement → drag proposal."""
        if direction in ("up", "down"):
            ticks = int(round(abs(dy) / max(self.scroll_tick, 1e-9)))
            return "scroll", (ticks if direction == "up" else -ticks)
        return "drag", 0

    def _count_exit(self, t: float) -> Optional[Dict[str, Any]]:
        self._exit_count += 1
        if self._exit_count >= self.exit_frames:
            return self._release(t, "normal")
        return None

    def _on_missing(self, t: float) -> Optional[Dict[str, Any]]:
        if self.state == "idle":
            self._enter_count = 0
            return None
        if t - self._last_good_t <= self.grace_s:
            return None                       # grace: hold breath, keep state
        return self._release(t, "tracking_lost")

    def _release(self, t: float, reason: str) -> Dict[str, Any]:
        duration = max(0.0, t - self._start_t)
        displacement = 0.0
        direction = "none"
        if self._anchor is not None and self._last_pos is not None:
            dx = self._last_pos[0] - self._anchor[0]
            dy = self._last_pos[1] - self._anchor[1]
            displacement = math.hypot(dx, dy)
            direction = _dominant_direction(dx, dy, self.move_deadzone)
        was_moved = self._moved
        proposal: Optional[str] = None
        if was_moved:
            # scroll ticks were already streamed during the episode; a
            # non-vertical (drag) episode must be closed on the spine.
            proposal = None if direction in ("up", "down") else "stop_drag"
        elif (duration < self.tap_max_s
                and displacement <= self.tap_max_displacement):
            gap = t - self._last_tap_t
            self._last_tap_t = t
            if gap < self.double_pinch_window:
                self._reset_after_release()
                return {"event": "double_pinch",
                        "confidence": self._last_conf,
                        "duration_s": duration,
                        "displacement": displacement,
                        "dominant_direction": direction,
                        "proposal": "double_click",
                        "amount": 0,
                        "reason": reason}
            proposal = "left_click"
        payload = {"event": "pinch_release",
                   "confidence": self._last_conf,
                   "duration_s": duration,
                   "displacement": displacement,
                   "dominant_direction": direction,
                   "proposal": proposal,
                   "amount": 0,
                   "reason": reason}
        self._reset_after_release()
        return payload

    def _reset_after_release(self) -> None:
        self.state = "idle"
        self._enter_count = 0
        self._exit_count = 0
        self._anchor = None
        self._last_pos = None
        self._last_emit_pos = None
        self._held_fired = False
        self._moved = False

    def _event(self, event: str, t: float, duration_s: float,
               displacement: float, direction: str,
               proposal: Optional[str], amount: int = 0) -> Optional[Dict[str, Any]]:
        payload = {"event": event,
                   "confidence": self._last_conf,
                   "duration_s": float(duration_s),
                   "displacement": float(displacement),
                   "dominant_direction": direction,
                   "proposal": proposal,
                   "amount": int(amount),
                   "reason": "normal"}
        if event in _DEBOUNCED_EVENTS and not self._debounce.ready(t):
            return None
        return payload


# ---------------------------------------------------------------------------
# 4) TemporalRecognizer — detections, sequences, compositions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldDetection:
    """A pose held stable (per-step movement ≤ still_deadzone) for at
    least ``hold_min_s``."""
    label: str
    duration_s: float
    mean_confidence: float
    os_action: str = "NOT MAPPED"


@dataclass(frozen=True)
class MotionDetection:
    """A continuous movement episode (label-stable, movement-homogeneous)."""
    direction: str
    path_length: float
    mean_velocity: float
    duration_s: float
    os_action: str = "NOT MAPPED"


@dataclass(frozen=True)
class SequenceDetection:
    """A recognized pose transition sequence (e.g. (pointing, pinch))."""
    labels: Tuple[str, ...]
    total_duration_s: float
    gaps: Tuple[float, ...]
    os_action: str = "NOT MAPPED"


@dataclass(frozen=True)
class Composition:
    """A recognized multi-modal/multi-phase pattern resolved to ONE
    unified intent-name proposal (prediction only — the spine executes)."""
    name: str
    confidence: float
    os_action: str
    details: Dict[str, Any] = field(default_factory=dict)


class TemporalRecognizer:
    """Pure recognizer over a :class:`TrajectoryBuffer` (+ two-hand reports).

    ``recognize()`` returns detections (HOLD / MOTION / SEQUENCE — honest
    ``os_action="NOT MAPPED"`` until wired) and COMPOSITIONS (the mapped,
    spine-actionable proposals).  Nothing here executes anything.
    """

    #: pose-transition patterns considered for SEQUENCE recognition.
    SEQUENCE_PATTERNS: Tuple[Tuple[str, str], ...] = (
        (POINTING_LABEL, PINCH_LABEL),
    )

    #: click-class composition names (subject to gaze-target attachment).
    CLICK_CLASS = frozenset({
        "select_target", "activate_gaze_target", "left_click",
        "double_click", "right_click", "middle_click",
    })

    def __init__(self,
                 hold_min_s: float = 0.5,
                 still_deadzone: float = 0.008,
                 min_path_length: float = 0.04,
                 min_motion_s: float = 0.1,
                 max_gap_s: float = 0.8,
                 scroll_tick: float = 0.08,
                 move_deadzone: float = 0.02,
                 zoom_tick: float = 0.05) -> None:
        self.hold_min_s = float(hold_min_s)
        self.still_deadzone = float(still_deadzone)
        self.min_path_length = float(min_path_length)
        self.min_motion_s = float(min_motion_s)
        self.max_gap_s = float(max_gap_s)
        self.scroll_tick = float(scroll_tick)
        self.move_deadzone = float(move_deadzone)
        self.zoom_tick = float(zoom_tick)

    # ── one-hand recognition ──────────────────────────────────────────
    def recognize(self, buffer: TrajectoryBuffer,
                  gaze_target: Any = None) -> List[Any]:
        """Recognize detections + compositions from the buffer.

        Returns a list of :class:`HoldDetection` / :class:`MotionDetection`
        / :class:`SequenceDetection` / :class:`Composition`.  Deterministic
        and side-effect free; empty buffer → empty list.
        """
        samples = buffer.samples() if hasattr(buffer, "samples") else list(buffer)
        if not samples:
            return []
        runs = [r for r in self._runs(samples)
                if r["label"] not in ("", NONE_LABEL, "none")]
        segments = self._segments(samples)

        out: List[Any] = []

        # HOLD: still, label-stable stretches ≥ hold_min_s
        for seg in segments:
            if seg["moving"] is not False:
                continue
            label = seg["label"]
            if label in ("", NONE_LABEL, "none"):
                continue
            duration = seg["t1"] - seg["t0"]
            if duration >= self.hold_min_s:
                out.append(HoldDetection(
                    label=label,
                    duration_s=duration,
                    mean_confidence=sum(seg["confs"]) / len(seg["confs"]),
                ))

        # MOTION: moving, label-stable episodes
        for seg in segments:
            if seg["moving"] is not True:
                continue
            duration = seg["t1"] - seg["t0"]
            if seg["path"] >= self.min_path_length and duration >= self.min_motion_s:
                dx = seg["last"][0] - seg["first"][0]
                dy = seg["last"][1] - seg["first"][1]
                out.append(MotionDetection(
                    direction=_dominant_direction(dx, dy, self.move_deadzone),
                    path_length=seg["path"],
                    mean_velocity=seg["path"] / duration if duration > 1e-9 else 0.0,
                    duration_s=duration,
                ))

        # SEQUENCE: adjacent label runs within max_gap_s
        for a, b in zip(runs, runs[1:]):
            if (a["label"], b["label"]) in self.SEQUENCE_PATTERNS:
                gap = b["t0"] - a["t1"]
                if 0.0 <= gap <= self.max_gap_s:
                    out.append(SequenceDetection(
                        labels=(a["label"], b["label"]),
                        total_duration_s=b["t1"] - a["t0"],
                        gaps=(gap,),
                    ))

        out.extend(self._compositions(runs, gaze_target))
        return out

    # ── two-hand reports (honest mapping, mission §13) ────────────────
    def recognize_two_hand(self, report: Dict[str, Any]) -> Dict[str, Any]:
        """Map a ``two_hand.TwoHandGestureRecognizer``-style report dict to
        an honest proposal.

        Real action ONLY for ZOOM (``os_action="zoom_ticks"``, amount =
        signed tick count from ``scale`` via ``zoom_tick``).  ROTATE / DRAG
        / HOLD are reported with ``os_action="NOT MAPPED"`` until wired —
        detection is never silently dropped and never invented into an
        action (mission §13).
        """
        report = report or {}
        gesture = str(report.get("gesture") or "")
        short = gesture.split("TWO_HAND_")[-1] if gesture else ""
        active = bool(report.get("active"))
        confidence = float(report.get("confidence") or 0.0)
        scale = float(report.get("scale") or 1.0)
        angle = float(report.get("angle_delta_deg") or 0.0)
        cd = report.get("centroid_delta")
        try:
            drag_mag = math.hypot(float(cd[0]), float(cd[1]))
        except (TypeError, ValueError, IndexError):
            drag_mag = 0.0

        base = {"confidence": confidence, "scale": scale,
                "angle_delta_deg": angle, "active": active}

        if not short:
            detected = "COUNTING" if (not active and confidence > 0.0) else "NONE"
            out = {"detected": detected, "os_action": "NOT MAPPED",
                   "amount": 0.0}
            out.update(base)
            return out
        if short == "ZOOM":
            amount = int(round((scale - 1.0) / max(self.zoom_tick, 1e-9)))
            out = {"detected": "ZOOM", "os_action": "zoom_ticks",
                   "amount": amount}
            out.update(base)
            return out
        if short == "ROTATE":
            out = {"detected": "ROTATE", "os_action": "NOT MAPPED",
                   "amount": angle}
            out.update(base)
            return out
        if short == "DRAG":
            out = {"detected": "DRAG", "os_action": "NOT MAPPED",
                   "amount": drag_mag}
            out.update(base)
            return out
        if short == "HOLD":
            out = {"detected": "HOLD", "os_action": "NOT MAPPED",
                   "amount": 0.0}
            out.update(base)
            return out
        # unknown two-hand label — report it, never invent an action
        out = {"detected": short, "os_action": "NOT MAPPED", "amount": 0.0}
        out.update(base)
        return out

    # ── internals ─────────────────────────────────────────────────────
    def _runs(self, samples: List[Sample]) -> List[Dict[str, Any]]:
        """Maximal same-label runs with position/path summaries."""
        runs: List[Dict[str, Any]] = []
        for s in samples:
            label = str(s.label or "")
            pos = s.position
            if runs and runs[-1]["label"] == label:
                r = runs[-1]
                r["t1"] = float(s.t)
                r["confs"].append(float(s.confidence))
                if pos is not None:
                    if r["first"] is None:
                        r["first"] = pos
                    elif r["last"] is not None:
                        r["path"] += math.hypot(pos[0] - r["last"][0],
                                                pos[1] - r["last"][1])
                    r["last"] = pos
            else:
                runs.append({"label": label, "t0": float(s.t), "t1": float(s.t),
                             "first": pos, "last": pos, "path": 0.0,
                             "confs": [float(s.confidence)]})
        return runs

    def _segments(self, samples: List[Sample]) -> List[Dict[str, Any]]:
        """Maximal label-stable, movement-homogeneous stretches.

        A segment closes on: label change, missing position (tracking
        gap), or a stillness flip (moving ↔ still).  Single-sample
        segments have ``moving=None`` and feed neither HOLD nor MOTION.
        """
        segs: List[Dict[str, Any]] = []
        cur: Optional[Dict[str, Any]] = None
        prev_pos: Optional[Tuple[float, float]] = None
        for s in samples:
            pos = s.position
            label = str(s.label or "")
            boundary = (cur is None or label != cur["label"]
                        or pos is None or prev_pos is None)
            step = 0.0
            if not boundary and pos is not None and prev_pos is not None:
                step = math.hypot(pos[0] - prev_pos[0], pos[1] - prev_pos[1])
                moving = step > self.still_deadzone
                if cur is not None and cur["moving"] is not None \
                        and moving != cur["moving"]:
                    boundary = True
            if boundary:
                if cur is not None:
                    segs.append(cur)
                cur = {"label": label, "t0": float(s.t), "t1": float(s.t),
                       "path": 0.0, "moving": None, "first": pos,
                       "last": pos, "confs": [float(s.confidence)]}
            else:
                cur["t1"] = float(s.t)
                cur["path"] += step
                cur["moving"] = step > self.still_deadzone
                cur["last"] = pos
                cur["confs"].append(float(s.confidence))
            prev_pos = pos
        if cur is not None:
            segs.append(cur)
        return segs

    def _compositions(self, runs: List[Dict[str, Any]],
                      gaze_target: Any) -> List[Composition]:
        """Pure pattern → unified intent-name mapping (mission §12).

        At most ONE click-class composition per scenario: when a gaze
        target is present, ``activate_gaze_target`` subsumes
        ``select_target`` (more specific confirmation).
        """
        comps: List[Composition] = []

        # pinch movement compositions (scroll / drag)
        for run in runs:
            if run["label"] != PINCH_LABEL:
                continue
            if run["path"] > self.move_deadzone and run["first"] and run["last"]:
                dx = run["last"][0] - run["first"][0]
                dy = run["last"][1] - run["first"][1]
                direction = _dominant_direction(dx, dy, self.move_deadzone)
                conf = sum(run["confs"]) / len(run["confs"])
                duration = run["t1"] - run["t0"]
                displacement = math.hypot(dx, dy)
                if direction in ("up", "down"):
                    ticks = int(round(abs(dy) / max(self.scroll_tick, 1e-9)))
                    amount = ticks if direction == "up" else -ticks
                    comps.append(Composition(
                        name="scroll", confidence=conf, os_action="scroll",
                        details={"spine": ["scroll"], "direction": direction,
                                 "amount": amount,
                                 "displacement": displacement,
                                 "duration_s": duration}))
                else:
                    comps.append(Composition(
                        name="drag", confidence=conf,
                        os_action="start_drag/stop_drag",
                        details={"spine": ["start_drag", "stop_drag"],
                                 "direction": direction,
                                 "displacement": displacement,
                                 "path_length": run["path"],
                                 "duration_s": duration}))

        # (pointing → pinch) with an unmoved pinch → select_target
        select_added = False
        for a, b in zip(runs, runs[1:]):
            if (a["label"], b["label"]) in self.SEQUENCE_PATTERNS:
                gap = b["t0"] - a["t1"]
                if 0.0 <= gap <= self.max_gap_s and b["path"] <= self.move_deadzone:
                    comps.append(Composition(
                        name="select_target",
                        confidence=sum(b["confs"]) / len(b["confs"]),
                        os_action="left_click",
                        details={"spine": ["left_click"],
                                 "sequence": (a["label"], b["label"]),
                                 "gap_s": gap}))
                    select_added = True

        # gaze target + pinch → activate_gaze_target (subsumes select_target)
        pinch_runs = [r for r in runs if r["label"] == PINCH_LABEL]
        if gaze_target is not None and pinch_runs:
            if select_added:
                comps = [c for c in comps if c.name != "select_target"]
            best = max(pinch_runs, key=lambda r: sum(r["confs"]))
            conf = sum(best["confs"]) / len(best["confs"])
            comps.append(Composition(
                name="activate_gaze_target", confidence=conf,
                os_action="left_click",
                details={"spine": ["left_click"], "target": gaze_target}))
        return comps


# ---------------------------------------------------------------------------
# 5) CompositionResolver — the multimodal unifier (mission §12/§16)
# ---------------------------------------------------------------------------


class CompositionResolver:
    """Deterministic multimodal conflict resolution → EXACTLY ONE proposal.

    Resolution rules (evaluated in order, all deterministic):

    1. E-STOP pass-through guard: ``context["estopped"]`` (or
       ``context["estop"]``) → ``None``.  E-STOP/human-override is decided
       by the spine (GestureActionRouter), which stays dominant; this
       resolver simply refuses to predict through an emergency stop.
    2. Explicit user confirmation (``context["confirmed"]``) outranks
       everything else.
    3. For conflicting pointing/movement demands in the same tick the
       documented priority is:  explicit user confirmation > gesture >
       gaze > voice.  The loser is preserved under ``"superseded"`` for
       honesty/audit; nothing is executed here.
    4. Same-action inputs from different modalities are DEDUPLICATED into
       a single proposal with merged ``sources`` (e.g. voice "click" +
       pinch tap → ONE ``left_click`` proposal, sources
       ``["voice", "gesture"]``) — the dedupe guarantee: no duplicate
       execution.
    5. ``gaze_target`` attaches as ``"target"`` to voice intents (rule:
       a voice intent operates on the gazed target) and turns gaze +
       click-class gesture into ``activate_gaze_target``.
    6. Gaze alone is pointing, not acting → ``None``.

    Output contract: exactly one dict or ``None``; every dict contains
    ``"requires": "spine_dispatch"`` — it is a PROPOSAL, never an
    execution.
    """

    CLICK_CLASS = frozenset({
        "left_click", "double_click", "right_click", "middle_click",
        "click", "select_target", "activate_gaze_target", "confirm",
    })

    #: voice phrase/intent normalization (small, honest alias table).
    _VOICE_ALIASES: Dict[str, str] = {
        "click": "left_click",
        "click that": "left_click",
        "that": "left_click",
        "select": "select_target",
        "double click": "double_click",
        "double_click": "double_click",
        "right click": "right_click",
        "scroll up": "scroll",
        "scroll down": "scroll",
        "zoom in": "zoom",
        "zoom out": "zoom",
    }

    #: bare gesture label / event name → intent-name fallback
    #: (event dicts carrying an explicit "proposal" always win).
    _GESTURE_EVENT_TO_INTENT: Dict[str, Optional[str]] = {
        "pinch": "left_click",
        "pinch_release": "left_click",
        "double_pinch": "double_click",
        "pinch_move": "drag",
        "pinch_start": None,
        "pinch_hold": None,
    }

    def resolve(self,
                voice_intent: Any = None,
                gaze_target: Any = None,
                gesture_event: Any = None,
                context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Resolve one tick's multimodal candidates into ONE proposal.

        Returns ``None`` when there is nothing actionable or when the
        context says the system is e-stopped.  Never executes anything.
        """
        context = context or {}
        if context.get("estopped") or context.get("estop"):
            return None

        v_intent, v_params = self._normalize_voice(voice_intent)
        g_intent, g_params = self._normalize_gesture(gesture_event)
        confirmed = bool(context.get("confirmed") or context.get("confirm"))

        # candidates: [rank, intent, params, sources(set), target]
        cands: List[List[Any]] = []
        if g_intent:
            if gaze_target is not None and g_intent in self.CLICK_CLASS:
                cands.append([3, "activate_gaze_target", dict(g_params),
                              {"gesture", "gaze"}, gaze_target])
            else:
                cands.append([3, g_intent, dict(g_params), {"gesture"}, None])
        if v_intent:
            cands.append([4 if confirmed else 1, v_intent, dict(v_params),
                          {"voice"}, None])
        if not cands:
            return None

        # dedupe same-action candidates into ONE (merge sources/params)
        merged: List[List[Any]] = []
        for c in cands:
            for m in merged:
                if self._same_action(m[1], c[1], gaze_target is not None):
                    m[0] = max(m[0], c[0])
                    m[2].update(c[2])
                    m[3] |= c[3]
                    break
            else:
                merged.append(c)

        # winner: highest rank; tie → more modalities; tie → first defined
        merged.sort(key=lambda c: (-c[0], -len(c[3])))
        rank, intent, params, sources, target = merged[0]

        # gaze target attachment (voice intents operate on the gazed target)
        if target is None and gaze_target is not None \
                and (intent in self.CLICK_CLASS or "voice" in sources):
            target = gaze_target
            sources.add("gaze")

        proposal: Dict[str, Any] = {
            "intent": intent,
            "sources": [m for m in ("voice", "gaze", "gesture") if m in sources],
            "requires": "spine_dispatch",
        }
        if target is not None:
            proposal["target"] = target
        if params:
            proposal["params"] = params
        if len(merged) > 1:
            proposal["resolved_by"] = (
                "priority: explicit confirmation > gesture > gaze > voice")
            proposal["superseded"] = [
                {"intent": m[1],
                 "sources": [x for x in ("voice", "gaze", "gesture")
                             if x in m[3]]}
                for m in merged[1:]
            ]
        return proposal

    # ── internals ─────────────────────────────────────────────────────
    def _same_action(self, a: str, b: str, has_gaze: bool) -> bool:
        if a == b:
            return True
        if has_gaze and {a, b} <= {"left_click", "activate_gaze_target",
                                   "select_target"}:
            return True
        return False

    def _normalize_voice(self, voice_intent: Any) -> Tuple[Optional[str], Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if voice_intent is None:
            return None, params
        raw: Any = voice_intent
        if isinstance(voice_intent, dict):
            raw = voice_intent.get("intent") or voice_intent.get("type") or ""
            params = dict(voice_intent.get("params") or {})
        elif hasattr(voice_intent, "value"):   # tolerate IntentType enums
            raw = voice_intent.value
        text = str(raw or "").strip().lower()
        if not text:
            return None, params
        return self._VOICE_ALIASES.get(text, text), params

    def _normalize_gesture(self, gesture_event: Any) -> Tuple[Optional[str], Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if gesture_event is None:
            return None, params
        if isinstance(gesture_event, dict):
            params = dict(gesture_event.get("params") or {})
            proposal = gesture_event.get("proposal")
            if proposal:
                return str(proposal), params
            ev = str(gesture_event.get("event") or "")
            return self._GESTURE_EVENT_TO_INTENT.get(ev), params
        if hasattr(gesture_event, "value"):
            gesture_event = gesture_event.value
        text = str(gesture_event).strip().lower()
        if not text:
            return None, params
        return self._GESTURE_EVENT_TO_INTENT.get(text, text), params


# ---------------------------------------------------------------------------
# 6) Robustness toolkit (mission §14) — small, pure, tested
# ---------------------------------------------------------------------------


class Hysteresis:
    """Two-threshold band: ``update(value)`` is ``True`` ("inside") once
    ``value >= enter`` and stays ``True`` until ``value < exit``.

    Classic flicker killer for borderline signals (enter 0.65 / exit 0.45
    confidence bands, pinch-distance bands, ...).  Requires
    ``exit <= enter`` (raises ``ValueError`` otherwise).
    """

    def __init__(self, enter: float, exit: float) -> None:
        enter = float(enter)
        exit = float(exit)
        if exit > enter:
            raise ValueError("hysteresis exit threshold must be <= enter")
        self.enter = enter
        self.exit = exit
        self._inside = False

    def update(self, value: float) -> bool:
        v = float(value)
        if self._inside:
            if v < self.exit:
                self._inside = False
        else:
            if v >= self.enter:
                self._inside = True
        return self._inside

    @property
    def inside(self) -> bool:
        return self._inside


class Debouncer:
    """Minimum-interval gate with auto-arm: ``ready(now)`` returns True
    immediately the first time (armed), then only after ``min_interval_s``
    has elapsed since the last ``True``."""

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._last_ready_t: Optional[float] = None

    def ready(self, now: float) -> bool:
        now = float(now)
        if (self._last_ready_t is None
                or now - self._last_ready_t >= self.min_interval_s):
            self._last_ready_t = now
            return True
        return False

    def rearm(self) -> None:
        """Force the next ``ready()`` call to pass."""
        self._last_ready_t = None


class FalsePositiveSuppressor:
    """Confirmation gate: a detection must persist ``min_frames``
    consecutive frames at ``>= min_confidence`` before it is confirmed;
    after each confirmation a ``cooldown_s`` lockout suppresses further
    confirmations (qualifying frames keep counting so a sustained signal
    confirms immediately when the cooldown expires).  Any low-confidence
    frame resets the streak."""

    def __init__(self, min_confidence: float = 0.6, min_frames: int = 3,
                 cooldown_s: float = 0.5) -> None:
        self.min_confidence = float(min_confidence)
        self.min_frames = max(1, int(min_frames))
        self.cooldown_s = max(0.0, float(cooldown_s))
        self._count = 0
        self._cooldown_until: Optional[float] = None

    def feed(self, confidence: float, now: float) -> bool:
        now = float(now)
        if float(confidence) >= self.min_confidence:
            self._count += 1
            if self._count >= self.min_frames and (
                    self._cooldown_until is None
                    or now >= self._cooldown_until):
                self._count = 0
                self._cooldown_until = now + self.cooldown_s
                return True
            return False
        self._count = 0
        return False


class TrackingRecovery:
    """Tracking-loss state machine: ``"ok"`` → ``"recovering"`` (within
    ``grace_s`` of the last good frame) → ``"lost"`` (past grace).

    One-shot ``"lost"`` / ``"recovered"`` events are available via
    :meth:`pop_event` — states may repeat every frame, events never spam.
    """

    def __init__(self, grace_s: float = 0.35) -> None:
        self.grace_s = float(grace_s)
        self._last_ok_t: Optional[float] = None
        self._lost = False
        self._event: Optional[str] = None

    def on_frame(self, ok: bool, now: float) -> str:
        now = float(now)
        if ok:
            if self._lost:
                self._lost = False
                self._event = "recovered"
            self._last_ok_t = now
            return "ok"
        if self._lost:
            return "lost"
        if self._last_ok_t is None or now - self._last_ok_t > self.grace_s:
            self._lost = True
            self._event = "lost"
            return "lost"
        return "recovering"

    def pop_event(self) -> Optional[str]:
        """Return and clear the pending one-shot event (None if none)."""
        ev = self._event
        self._event = None
        return ev

    @property
    def lost(self) -> bool:
        return self._lost


class CameraWatchdog:
    """Frame-freshness watchdog: ``"healthy"`` (younger than
    ``max_stale_s``) → ``"stale"`` (older, but younger than
    ``lost_after_s``, default 3× max_stale_s) → ``"lost"``.
    Before the first frame the camera is ``"lost"`` (conservative)."""

    def __init__(self, max_stale_s: float = 1.0,
                 lost_after_s: Optional[float] = None) -> None:
        self.max_stale_s = float(max_stale_s)
        self.lost_after_s = (float(lost_after_s) if lost_after_s is not None
                             else 3.0 * float(max_stale_s))
        self._last_frame_t: Optional[float] = None

    def on_frame(self, now: float) -> str:
        """Record a fresh frame; returns :meth:`health` for convenience."""
        self._last_frame_t = float(now)
        return self.health(now)

    def health(self, now: float) -> str:
        now = float(now)
        if self._last_frame_t is None:
            return "lost"
        age = now - self._last_frame_t
        if age < self.max_stale_s:
            return "healthy"
        if age < self.lost_after_s:
            return "stale"
        return "lost"


class SensorHealthScore:
    """EWMA sensor-health score in 0..1.

    ``feed(frame_ok, confidence)`` folds one frame: a dropped frame folds
    0.0, a kept frame folds its (clamped) confidence — so drops and low
    confidence both pull the score down.  ``rating()`` maps the score to
    ``"good"`` / ``"degraded"`` / ``"poor"`` (thresholds injectable,
    defaults conservative: good ≥ 0.7, degraded ≥ 0.4).
    """

    def __init__(self, window: int = 120, good_at: float = 0.7,
                 degraded_at: float = 0.4) -> None:
        self.window = max(1, int(window))
        # standard EWMA smoothing factor: a window of 10 frames reacts
        # within ~4-8 frames (conservative but responsive)
        self.alpha = 2.0 / (float(self.window) + 1.0)
        self.good_at = float(good_at)
        self.degraded_at = float(degraded_at)
        self._score = 1.0

    def feed(self, frame_ok: bool, confidence: float = 1.0) -> float:
        sample = float(confidence) if frame_ok else 0.0
        sample = min(1.0, max(0.0, sample))
        self._score = (1.0 - self.alpha) * self._score + self.alpha * sample
        return self._score

    @property
    def score(self) -> float:
        return self._score

    def rating(self) -> str:
        if self._score >= self.good_at:
            return "good"
        if self._score >= self.degraded_at:
            return "degraded"
        return "poor"

    def reset(self) -> None:
        self._score = 1.0
