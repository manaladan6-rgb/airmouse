"""
airmouse.interfaces — AirMouse v9.0.0 shared contracts.

This module is the SINGLE SOURCE OF TRUTH for the data structures and
enums exchanged between the v9 subsystems:

    gaze (v6)          -> GazeSample / GazeState
    gaze_filter (v6)   -> GazeSample (filtered)
    gaze_calibration   -> screen-space mapping
    fusion (v7)        -> FusionEvent / FusionDecision
    screen (v7)        -> ScreenTarget / ScreenModel
    intent (v8)        -> Intent
    actions (v8)       -> ActionPlan / ActionReport
    verification (v8)  -> VerificationResult
    safety (v8)        -> SafetyDecision
    context (v8)       -> AppContext
    nl_control (v9)    -> NLUResult
    macros (v8)        -> MacroStep / MacroProgram
    agent (v9)         -> InteractionAgent + Telemetry

DESIGN RULES
------------
1.  This module imports ONLY the standard library (+ numpy where handy).
    It must import headless — no cv2, mediapipe, pynput, GUI, or network.
2.  All timestamps are time.perf_counter() floats (seconds).
3.  Raw gaze coordinates are NORMALIZED frame space [0,1]^2
    (x right, y down).  Screen targets use PIXELS on the virtual desktop.
4.  Every risky capability is expressed as data + confidence so the
    safety layer can gate it.  Nothing here executes side effects.
5.  Conventions:  x = 0..screen_w (left->right),  y = 0..screen_h (top->down).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Protocol, Callable

# ─────────────────────────────────────────────────────────────────────────────
# Version
# ─────────────────────────────────────────────────────────────────────────────

VERSION_V9 = "9.0.0"

__all__ = [
    "VERSION_V9",
    # enums
    "Modality", "FusionMode", "IntentType", "ActionType", "ActionStatus",
    "VerificationStatus", "RecoveryStrategy", "ScreenTargetType", "AppContext",
    "GazeEventKind", "SafetyLevel", "MacroOp",
    # dataclasses
    "GazeSample", "GazeState", "ScreenTarget", "ScreenModel", "FusionEvent",
    "FusionDecision", "Intent", "ActionPlan", "ActionReport",
    "VerificationResult", "SafetyDecision", "NLUResult", "MacroStep",
    "MacroProgram", "TelemetryStats",
    # protocols
    "TargetProvider", "ScreenProvider", "ActionExecutor", "LandmarkProvider",
    "now_ts", "UNKNOWN_TARGET",
]

# ─────────────────────────────────────────────────────────────────────────────
# Timestamp helper
# ─────────────────────────────────────────────────────────────────────────────


def now_ts() -> float:
    """Monotonic wall timestamp (perf_counter seconds)."""
    return time.perf_counter()


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


class Modality(enum.Flag):
    """Input modalities participating in fusion."""

    NONE = 0
    HAND = enum.auto()      # 🖐️ hand gestures
    GAZE = enum.auto()      # 👁️ eye/gaze
    VOICE = enum.auto()     # 🎤 speech
    MOUSE = enum.auto()     # 🖱️ physical mouse state
    KEYBOARD = enum.auto()  # ⌨️ physical keyboard state
    SCREEN = enum.auto()    # 🖥️ screen-understanding context
    ANY = HAND | GAZE | VOICE | MOUSE | KEYBOARD | SCREEN


class FusionMode(enum.Enum):
    """System-level interaction mode (v9).  Which modality leads."""

    HAND = "hand"              # v5 behaviour: hand drives everything
    GAZE = "gaze"              # eyes target, dwell/blink confirm
    VOICE = "voice"            # voice drives, current pointer is target
    FUSION = "fusion"          # gaze targets + hand confirms + voice intents
    HANDS_FREE = "hands_free"  # gaze targets + voice commands + blink/dwell
    ASSIST = "assist"          # everything observed, actions need confirmation


class IntentType(enum.Enum):
    """Structured intents produced by the intent engine."""

    NONE = "none"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    MIDDLE_CLICK = "middle_click"
    MOVE = "move"
    DRAG = "drag"
    DROP = "drop"
    SCROLL = "scroll"
    ZOOM = "zoom"
    TYPE = "type"
    HOTKEY = "hotkey"
    COPY = "copy"
    PASTE = "paste"
    OPEN = "open"
    CLOSE = "close"
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    SWITCH_WINDOW = "switch_window"
    BACK = "back"
    FORWARD = "forward"
    SELECT = "select"
    CANCEL = "cancel"
    PLAY = "play"
    PAUSE = "pause"
    CONFIRM = "confirm"          # explicit user confirmation (safety flow)
    REPEAT = "repeat"            # repeat last successful action
    EMERGENCY_STOP = "emergency_stop"


class ActionType(enum.Enum):
    """Low-level computer actions the action engine can execute."""

    NONE = "none"
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    MIDDLE_CLICK = "middle_click"
    MOVE = "move"
    DRAG = "drag"
    DROP = "drop"
    SCROLL = "scroll"
    ZOOM = "zoom"
    TYPE = "type"
    HOTKEY = "hotkey"
    KEY_PRESS = "key_press"


class ActionStatus(enum.Enum):
    """Lifecycle of a single action execution."""

    PENDING = "pending"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"        # safety gate refused
    UNVERIFIED = "unverified"  # executed but observation unavailable


class VerificationStatus(enum.Enum):
    """Outcome of post-action verification."""

    NOT_NEEDED = "not_needed"
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"        # could not observe
    PENDING = "pending"


class RecoveryStrategy(enum.Enum):
    """What to do after a failed/failed-verification action."""

    NONE = "none"
    RETRY = "retry"                 # safe retry (same plan)
    RETRY_ADJUSTED = "retry_adj"    # retry with adjusted params
    ALTERNATIVE = "alternative"     # different route to same goal
    NOTIFY = "notify"               # tell user, stop
    ABORT = "abort"                 # hard stop, engage safe mode


class ScreenTargetType(enum.Enum):
    """Semantic kinds of on-screen targets from the screen model."""

    UNKNOWN = "unknown"
    BUTTON = "button"
    LINK = "link"
    TEXT_FIELD = "text_field"
    ICON = "icon"
    WINDOW = "window"
    MENU = "menu"
    IMAGE = "image"
    BROWSER_CONTROL = "browser_control"
    APP_REGION = "app_region"
    DESKTOP = "desktop"


class AppContext(enum.Enum):
    """Coarse application context for context-aware behaviour."""

    UNKNOWN = "unknown"
    BROWSER = "browser"
    EDITOR = "editor"
    TERMINAL = "terminal"
    VIDEO = "video"
    FILE_MANAGER = "file_manager"
    DESKTOP = "desktop"
    DIALOG = "dialog"


class GazeEventKind(enum.Enum):
    """Discrete eye events emitted by the gaze subsystem."""

    NONE = "none"
    BLINK = "blink"                # normal blink (short)
    LONG_BLINK = "long_blink"      # intentional long blink
    DOUBLE_BLINK = "double_blink"  # two blinks in quick succession
    WINK_LEFT = "wink_left"
    WINK_RIGHT = "wink_right"
    FIXATION_START = "fixation_start"
    FIXATION_END = "fixation_end"
    DWELL = "dwell"                # sustained fixation -> dwell fired
    FACE_LOST = "face_lost"
    FACE_FOUND = "face_found"


class SafetyLevel(enum.Enum):
    """Global safety posture."""

    NORMAL = "normal"      # full autonomy within thresholds
    CAREFUL = "careful"    # elevated confidence thresholds
    SAFE_MODE = "safe"     # read-only-ish: move cursor, no clicks without confirm
    EMERGENCY = "emergency"  # e-stop latched: no actions at all


class MacroOp(enum.Enum):
    """Semantic macro operations (macro format v2)."""

    LOOK_FOR = "look_for"      # wait until a target matching spec is on screen
    WAIT_UNTIL = "wait_until"  # wait condition: time / fixation / target
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE = "type"
    SCROLL = "scroll"
    HOTKEY = "hotkey"
    VERIFY = "verify"          # assert expected screen state
    IF = "if"                  # conditional on last verify result
    RETRY = "retry"            # retry previous step up to N times
    STOP = "stop"              # abort macro
    # legacy v1 timestamp events remain supported: move/click/scroll/zoom/...


# ─────────────────────────────────────────────────────────────────────────────
# Gaze data (v6)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class GazeSample:
    """One gaze observation.

    Attributes
    ----------
    x, y : normalized frame coords [0,1] of the gaze point
        (raw estimator output BEFORE screen mapping; (0.5,0.5) = frame centre)
    confidence : float in [0,1]
        0 = unusable (no face / eye occluded), 1 = rock solid.
    eye_open_l, eye_open_r : bools
    ear_l, ear_r : eye aspect ratios (closure measure)
    head_dx, head_dy : normalized head-centre offset (for head-pose gating)
    timestamp : perf_counter seconds
    """

    x: float = 0.5
    y: float = 0.5
    confidence: float = 0.0
    eye_open_l: bool = True
    eye_open_r: bool = True
    ear_l: float = 1.0
    ear_r: float = 1.0
    head_dx: float = 0.0
    head_dy: float = 0.0
    timestamp: float = field(default_factory=now_ts)

    def is_usable(self, min_confidence: float = 0.3) -> bool:
        return self.confidence >= min_confidence and (self.eye_open_l or self.eye_open_r)


@dataclass
class GazeState:
    """Post-pipeline gaze state (filtered + events), ready for fusion.

    screen_x/screen_y are PIXELS when a calibration is applied
    (set screen_valid=True), otherwise normalized frame coords.
    """

    x: float = 0.5
    y: float = 0.5
    screen_x: float = 0.0
    screen_y: float = 0.0
    screen_valid: bool = False
    confidence: float = 0.0
    fixation: bool = False
    fixation_duration: float = 0.0
    dwell_fired: bool = False
    events: List[GazeEventKind] = field(default_factory=list)
    eye_open_l: bool = True
    eye_open_r: bool = True
    timestamp: float = field(default_factory=now_ts)

    def latest_event(self) -> GazeEventKind:
        return self.events[-1] if self.events else GazeEventKind.NONE


# Sentinel used when no target exists yet.
UNKNOWN_TARGET: Optional["ScreenTarget"] = None


# ─────────────────────────────────────────────────────────────────────────────
# Screen understanding (v7)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ScreenTarget:
    """A semantically understood on-screen target.

    bbox is (x, y, w, h) in desktop pixels; center is derived.
    `source` records which layer found it: "accessibility" | "dom" | "ocr" |
    "vision" | "geometry".
    """

    id: str
    type: ScreenTargetType = ScreenTargetType.UNKNOWN
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    text: str = ""
    confidence: float = 0.0
    application: str = ""
    actionable: bool = False
    source: str = "geometry"
    timestamp: float = field(default_factory=now_ts)

    @property
    def center(self) -> Tuple[float, float]:
        bx, by, bw, bh = self.bbox
        return (bx + bw / 2.0, by + bh / 2.0)

    def contains(self, px: float, py: float) -> bool:
        bx, by, bw, bh = self.bbox
        return bx <= px <= bx + bw and by <= py <= by + bh


@dataclass
class ScreenModel:
    """Snapshot of understood screen content."""

    targets: List[ScreenTarget] = field(default_factory=list)
    screen_w: int = 0
    screen_h: int = 0
    context: AppContext = AppContext.UNKNOWN
    active_window_title: str = ""
    timestamp: float = field(default_factory=now_ts)

    def target_at(self, px: float, py: float) -> Optional[ScreenTarget]:
        """Smallest actionable target containing the point (fallback: any)."""
        hits = [t for t in self.targets if t.contains(px, py)]
        if not hits:
            return None
        actionable = [t for t in hits if t.actionable]
        pool = actionable or hits
        return min(pool, key=lambda t: t.bbox[2] * t.bbox[3])

    def nearest(self, px: float, py: float,
                max_dist: float = 120.0) -> Optional[ScreenTarget]:
        best, best_d = None, max_dist
        for t in self.targets:
            cx, cy = t.center
            d = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
            if d < best_d:
                best, best_d = t, d
        return best

    def find_by_text(self, needle: str, min_conf: float = 0.3) -> Optional[ScreenTarget]:
        needle_l = (needle or "").strip().lower()
        if not needle_l:
            return None
        for t in self.targets:
            if needle_l in t.text.lower() and t.confidence >= min_conf:
                return t
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Fusion (v7)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class FusionEvent:
    """A discrete observation from any modality, submitted to fusion.

    kind examples per modality:
      GAZE   : "target" (payload: screen point/target + confidence)
      HAND   : "point" | "pinch" | "pinch_release" | "peace" | "fist" | ...
      VOICE  : "utterance" (payload: text, command)
      MOUSE  : "move" | "click"
      KEYBOARD: "key" (payload: key name)
    """

    modality: Modality = Modality.NONE
    kind: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    timestamp: float = field(default_factory=now_ts)


@dataclass
class FusionDecision:
    """Output of the fusion engine for one tick.

    target is either a ScreenTarget (semantic) or a raw pixel point in
    `point`.  `contributing` lists the modalities that shaped it.
    """

    mode: FusionMode = FusionMode.FUSION
    point: Optional[Tuple[float, float]] = None
    target: Optional[ScreenTarget] = None
    confidence: float = 0.0
    contributing: Modality = Modality.NONE
    confirmations: List[str] = field(default_factory=list)  # e.g. ["hand:pinch"]
    utterance: str = ""
    timestamp: float = field(default_factory=now_ts)

    @property
    def has_target(self) -> bool:
        return self.target is not None or self.point is not None

    def target_point(self) -> Optional[Tuple[float, float]]:
        if self.target is not None:
            return self.target.center
        return self.point


# ─────────────────────────────────────────────────────────────────────────────
# Intent (v8)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Intent:
    """A structured, safety-gateable intention to act."""

    type: IntentType = IntentType.NONE
    target: Optional[ScreenTarget] = None
    point: Optional[Tuple[float, float]] = None
    params: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    sources: Modality = Modality.NONE
    utterance: str = ""
    requires_confirmation: bool = False
    timestamp: float = field(default_factory=now_ts)

    @property
    def target_point(self) -> Optional[Tuple[float, float]]:
        if self.target is not None:
            return self.target.center
        return self.point


# ─────────────────────────────────────────────────────────────────────────────
# Actions + verification (v8)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ActionPlan:
    """A bounded, executable plan derived from an Intent."""

    action: ActionType = ActionType.NONE
    point: Optional[Tuple[float, float]] = None
    target: Optional[ScreenTarget] = None
    params: Dict[str, Any] = field(default_factory=dict)
    expected: Dict[str, Any] = field(default_factory=dict)
    timeout: float = 2.0
    max_retries: int = 1
    requires_confirmation: bool = False
    intent: Optional[Intent] = None

    @property
    def target_point(self) -> Optional[Tuple[float, float]]:
        if self.target is not None:
            return self.target.center
        return self.point


@dataclass
class ActionReport:
    """Result of executing one ActionPlan."""

    plan: Optional[ActionPlan] = None
    status: ActionStatus = ActionStatus.PENDING
    message: str = ""
    latency: float = 0.0           # execution seconds
    attempts: int = 0
    observation: Dict[str, Any] = field(default_factory=dict)
    verification: VerificationStatus = VerificationStatus.NOT_NEEDED
    recovery: RecoveryStrategy = RecoveryStrategy.NONE
    timestamp: float = field(default_factory=now_ts)

    @property
    def ok(self) -> bool:
        return self.status == ActionStatus.SUCCESS


@dataclass
class VerificationResult:
    """Outcome of comparing expected vs observed post-action state."""

    status: VerificationStatus = VerificationStatus.UNKNOWN
    expected: Dict[str, Any] = field(default_factory=dict)
    observed: Dict[str, Any] = field(default_factory=dict)
    similarity: float = 0.0        # 0..1 match score
    message: str = ""
    suggested_recovery: RecoveryStrategy = RecoveryStrategy.NONE


# ─────────────────────────────────────────────────────────────────────────────
# Safety (v8)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SafetyDecision:
    """Result of the safety gate for a would-be action."""

    allowed: bool = False
    reason: str = ""
    requires_confirmation: bool = False
    level: SafetyLevel = SafetyLevel.NORMAL
    cooldown_remaining: float = 0.0
    timestamp: float = field(default_factory=now_ts)


# ─────────────────────────────────────────────────────────────────────────────
# Natural language (v9)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class NLUResult:
    """Parsed natural-language utterance."""

    text: str = ""
    intent: IntentType = IntentType.NONE
    params: Dict[str, Any] = field(default_factory=dict)
    target_ref: str = ""           # "", "this", "that", "window", "here", ...
    confidence: float = 0.0
    is_command: bool = False       # True when we recognized a v9 NL pattern
    fallback_command: str = ""     # legacy VoiceCommand string if matched


# ─────────────────────────────────────────────────────────────────────────────
# Macros v2 (v8)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class MacroStep:
    """One semantic macro step (format v2)."""

    op: MacroOp = MacroOp.WAIT_UNTIL
    params: Dict[str, Any] = field(default_factory=dict)
    comment: str = ""


@dataclass
class MacroProgram:
    """Semantic macro program (format v2).  Legacy v1 macros load as a
    MacroProgram with a single `sequence` op preserved in `legacy_events`."""

    name: str = ""
    version: int = 2
    steps: List[MacroStep] = field(default_factory=list)
    legacy_events: List[Dict[str, Any]] = field(default_factory=list)
    created: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry (v9)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TelemetryStats:
    """Rolling performance/behaviour statistics."""

    fps_camera: float = 0.0
    fps_gaze: float = 0.0
    fps_hand: float = 0.0
    latency_gaze_ms: float = 0.0      # landmark -> filtered screen point
    latency_cursor_ms: float = 0.0    # filtered point -> mouse move applied
    latency_fusion_ms: float = 0.0
    latency_voice_ms: float = 0.0     # utterance end -> intent resolved
    latency_action_ms: float = 0.0    # intent -> action executed
    cpu_hint: float = 0.0             # best-effort, 0 when unavailable
    memory_hint_mb: float = 0.0
    actions_total: int = 0
    actions_success: int = 0
    actions_failed: int = 0
    actions_blocked: int = 0
    recoveries: int = 0
    estop_count: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Protocols (structural typing — implementations live in concrete modules)
# ─────────────────────────────────────────────────────────────────────────────


class LandmarkProvider(Protocol):
    """Supplies face landmarks (e.g. mediapipe FaceMesh) for a frame."""

    def landmarks(self, frame: Any) -> Optional[Any]:
        """Return a normalized-landmark container or None."""
        ...


class TargetProvider(Protocol):
    """Anything that can supply the currently-relevant target."""

    def current_target(self) -> Optional[ScreenTarget]:
        ...


class ScreenProvider(Protocol):
    """A layered screen-understanding source."""

    name: str

    def update(self, now: float) -> List[ScreenTarget]:
        """Return the targets visible right now."""
        ...


class ActionExecutor(Protocol):
    """Executes primitive computer actions (implemented over pynput)."""

    def click(self, x: float, y: float) -> bool: ...
    def double_click(self, x: float, y: float) -> bool: ...
    def right_click(self, x: float, y: float) -> bool: ...
    def move(self, x: float, y: float) -> bool: ...
    def scroll(self, amount: int) -> bool: ...
    def type_text(self, text: str) -> bool: ...
    def hotkey(self, keys: Sequence[str]) -> bool: ...
    def drag(self, x0: float, y0: float, x1: float, y1: float,
             duration: float = 0.4) -> bool: ...


# Type alias for verification observation callbacks (used by the observer to
# diff screen state around the action point).
ObserveFn = Callable[[Optional[Tuple[float, float]]], Dict[str, Any]]
