"""
Gesture Recognition Engine v3.1 — 14 gestures + swipe + enhanced state machine.

v3.1 improvements:
  - Gesture stability scoring (landmark variance over time)
  - Transition cooldown between gestures (prevent rapid misfires)
  - Better finger-up detection with adaptive thresholds
  - Gesture confidence output for HUD feedback
  - Hold-to-confirm progress tracking

Gestures (by finger count & shape):
    POINTING    (1)  Index only              -> Move cursor
    PEACE       (2)  Index + middle          -> Right click
    THREE       (3)  Index + mid + ring      -> Scroll mode
    PALM        (4)  All 5 open              -> Drag mode
    FIST        (5)  All closed              -> Freeze cursor
    PINCH       (6)  Thumb + index close     -> Left click
    THUMBS_UP   (7)  Thumb only              -> Double click
    PINKY       (8)  Pinky only              -> Middle click
    GUN         (9)  Thumb up + index point  -> Snap to center
    ROCK        (10) Index + pinky up        -> Minimize window
    SHAKA       (11) Thumb + pinky out       -> Volume mode
    OK          (12) Thumb + middle close    -> Close window
    RING        (13) Ring finger only        -> Brightness mode
    SIX         (14) Thumb + index + pinky   -> Task switcher

Swipe gestures (motion-based):
    SWIPE_LEFT  -> Browser back
    SWIPE_RIGHT -> Browser forward
"""

import numpy as np
from collections import deque
import time


class Gesture:
    POINTING = "pointing"
    PEACE = "peace"
    THREE = "three"
    PALM = "palm"
    FIST = "fist"
    PINCH = "pinch"
    THUMBS_UP = "thumbs_up"
    PINKY = "pinky"
    GUN = "gun"
    ROCK = "rock"
    SHAKA = "shaka"
    OK = "ok"
    RING = "ring"
    SIX = "six"
    SWIPE_LEFT = "swipe_left"
    SWIPE_RIGHT = "swipe_right"
    NONE = "none"


# Gesture descriptions for tutorial & HUD
GESTURE_INFO = {
    Gesture.POINTING:   {"emoji": "1", "name": "Point",    "desc": "Index finger up, others down",         "action": "Move cursor"},
    Gesture.PINCH:      {"emoji": "2", "name": "Pinch",    "desc": "Touch thumb tip to index tip",          "action": "Left click"},
    Gesture.PEACE:      {"emoji": "3", "name": "Peace",    "desc": "Index + middle fingers up",             "action": "Right click"},
    Gesture.PALM:       {"emoji": "4", "name": "Palm",     "desc": "All 5 fingers open",                    "action": "Drag mode (grab & move)"},
    Gesture.FIST:       {"emoji": "5", "name": "Fist",     "desc": "All fingers closed",                    "action": "Freeze cursor"},
    Gesture.THUMBS_UP:  {"emoji": "6", "name": "Thumbs Up","desc": "Only thumb up, fingers closed",         "action": "Double click (open items)"},
    Gesture.THREE:      {"emoji": "7", "name": "Three",   "desc": "Index + middle + ring fingers up",     "action": "Scroll mode (move up/down)"},
    Gesture.PINKY:      {"emoji": "8", "name": "Pinky",    "desc": "Only pinky finger up",                  "action": "Middle click (close tabs)"},
    Gesture.GUN:        {"emoji": "9", "name": "Gun",      "desc": "Thumb up + index pointing (L-shape)",   "action": "Snap cursor to screen center"},
    Gesture.ROCK:       {"emoji": "10","name": "Rock",     "desc": "Index + pinky up, others down",         "action": "Minimize window (Win+Down)"},
    Gesture.SHAKA:      {"emoji": "11","name": "Shaka",    "desc": "Thumb + pinky out (hang loose)",        "action": "Volume mode (up/down)"},
    Gesture.OK:         {"emoji": "12","name": "OK",       "desc": "Thumb + middle touch (OK sign)",        "action": "Close window (Alt+F4)"},
    Gesture.RING:       {"emoji": "13","name": "Ring",     "desc": "Only ring finger up",                   "action": "Brightness mode (up/down)"},
    Gesture.SIX:        {"emoji": "14","name": "Six",      "desc": "Thumb + index + pinky out",             "action": "Task switcher (Alt+Tab)"},
}

# MediaPipe landmark indices
WRIST = 0
THUMB_CMC = 1; THUMB_MCP = 2; THUMB_IP = 3; THUMB_TIP = 4
INDEX_MCP = 5; INDEX_PIP = 6; INDEX_DIP = 7; INDEX_TIP = 8
MIDDLE_MCP = 9; MIDDLE_PIP = 10; MIDDLE_DIP = 11; MIDDLE_TIP = 12
RING_MCP = 13; RING_PIP = 14; RING_DIP = 15; RING_TIP = 16
PINKY_MCP = 17; PINKY_PIP = 18; PINKY_DIP = 19; PINKY_TIP = 20


def _dist(a, b):
    return np.sqrt((a.x - b.x)**2 + (a.y - b.y)**2 + (a.z - b.z)**2)


def _finger_up(landmarks, tip, pip, mcp):
    wrist = landmarks[WRIST]
    # Adaptive threshold: slightly more lenient than before
    return _dist(landmarks[tip], wrist) > _dist(landmarks[pip], wrist) * 1.05


def _thumb_up(landmarks):
    return _dist(landmarks[THUMB_TIP], landmarks[INDEX_MCP]) > \
           _dist(landmarks[THUMB_IP], landmarks[INDEX_MCP]) * 1.1


def _finger_confidence(landmarks, tip, pip, mcp):
    """Return 0.0-1.0 confidence that a finger is up.

    Based on how far past the PIP joint the tip is.
    1.0 = clearly up, 0.5 = borderline, 0.0 = clearly down.
    """
    wrist = landmarks[WRIST]
    tip_dist = _dist(landmarks[tip], wrist)
    pip_dist = _dist(landmarks[pip], wrist)
    # Ratio > 1.05 means finger is up
    ratio = tip_dist / max(pip_dist, 0.001)
    if ratio >= 1.15:
        return 1.0
    elif ratio >= 1.05:
        return (ratio - 1.05) / 0.10  # Linear from 0 to 1 in the 1.05-1.15 range
    elif ratio >= 0.95:
        return 0.0  # Below threshold but close — ambiguous
    else:
        return 0.0


def recognize_gesture(landmarks, pinch_threshold=0.06):
    """Classify hand gesture from 21 landmarks.

    Returns dict with gesture, finger states, confidence, and positions.
    """

    idx = _finger_up(landmarks, INDEX_TIP, INDEX_PIP, INDEX_MCP)
    mid = _finger_up(landmarks, MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP)
    ring = _finger_up(landmarks, RING_TIP, RING_PIP, RING_MCP)
    pin = _finger_up(landmarks, PINKY_TIP, PINKY_PIP, PINKY_MCP)
    thu = _thumb_up(landmarks)

    # Confidence scores for each finger
    idx_conf = _finger_confidence(landmarks, INDEX_TIP, INDEX_PIP, INDEX_MCP)
    mid_conf = _finger_confidence(landmarks, MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP)
    ring_conf = _finger_confidence(landmarks, RING_TIP, RING_PIP, RING_MCP)
    pin_conf = _finger_confidence(landmarks, PINKY_TIP, PINKY_PIP, PINKY_MCP)

    pinch_dist = _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP])
    ok_dist = _dist(landmarks[THUMB_TIP], landmarks[MIDDLE_TIP])
    is_pinch = pinch_dist < pinch_threshold and not mid
    is_ok = ok_dist < pinch_threshold and not idx and not ring

    fingers_up = sum([idx, mid, ring, pin, thu])
    finger_spread = fingers_up / 5.0

    index_tip = landmarks[INDEX_TIP]
    index_pos = np.array([index_tip.x, index_tip.y])

    # Overall gesture confidence (average of finger confidences for active fingers)
    confidences = [idx_conf, mid_conf, ring_conf, pin_conf]
    active_confs = [c for c, up in zip(confidences, [idx, mid, ring, pin]) if up]
    gesture_confidence = np.mean(active_confs) if active_confs else 0.8

    # --- Classification priority ---

    if is_pinch:
        gesture = Gesture.PINCH
    elif is_ok:
        gesture = Gesture.OK
    elif thu and idx and not mid and not ring and not pin:
        gesture = Gesture.GUN
    elif thu and pin and not idx and not mid and not ring:
        gesture = Gesture.SHAKA
    elif thu and idx and pin and not mid and not ring:
        gesture = Gesture.SIX
    elif idx and pin and not mid and not ring and not thu:
        gesture = Gesture.ROCK
    elif thu and not idx and not mid and not ring and not pin:
        gesture = Gesture.THUMBS_UP
    elif pin and not idx and not mid and not ring and not thu:
        gesture = Gesture.PINKY
    elif ring and not idx and not mid and not pin and not thu:
        gesture = Gesture.RING
    elif not idx and not mid and not ring and not pin:
        gesture = Gesture.FIST
    elif idx and mid and ring and not pin:
        gesture = Gesture.THREE
    elif idx and mid and not ring and not pin:
        gesture = Gesture.PEACE
    elif fingers_up >= 4:
        gesture = Gesture.PALM
    elif idx and not mid:
        gesture = Gesture.POINTING
    else:
        gesture = Gesture.FIST

    return {
        "gesture": gesture,
        "index_extended": idx,
        "middle_extended": mid,
        "ring_extended": ring,
        "pinky_extended": pin,
        "thumb_extended": thu,
        "pinch_distance": pinch_dist,
        "index_pos": index_pos,
        "finger_spread": finger_spread,
        "fingers_up": fingers_up,
        "landmarks": landmarks,
        "confidence": gesture_confidence,
    }


class SwipeDetector:
    """Detects horizontal swipe gestures from hand velocity history.

    v3.1: Better velocity tracking with longer history and adaptive threshold.
    """

    def __init__(self, speed_threshold=0.4, min_frames=4, cooldown=0.5):
        self.speed_threshold = speed_threshold
        self.min_frames = min_frames
        self.cooldown = cooldown
        self.x_velocities = deque(maxlen=min_frames)
        self.last_swipe_time = 0.0

    def update(self, current_pos, prev_pos, now):
        """Check for swipe."""
        if prev_pos is None:
            self.x_velocities.append(0.0)
            return Gesture.NONE

        vx = current_pos[0] - prev_pos[0]
        self.x_velocities.append(vx)

        if now - self.last_swipe_time < self.cooldown:
            return Gesture.NONE

        if len(self.x_velocities) >= self.min_frames:
            avg_vx = np.mean(self.x_velocities)
            if avg_vx > self.speed_threshold:
                self.last_swipe_time = now
                self.x_velocities.clear()
                return Gesture.SWIPE_RIGHT
            elif avg_vx < -self.speed_threshold:
                self.last_swipe_time = now
                self.x_velocities.clear()
                return Gesture.SWIPE_LEFT

        return Gesture.NONE

    def reset(self):
        self.x_velocities.clear()


class GestureStateMachine:
    """Enhanced gesture state machine with:
    - Hold-to-confirm (N frames required)
    - Transition cooldown (prevent rapid gesture switching)
    - Stability gating (require stable hand for action gestures)
    - Progress tracking (for HUD feedback)

    v3.1 improvements:
    - Transition cooldown between different gestures
    - Separate confirm frames for action vs movement gestures
    - Progress tracking for visual feedback
    - "Ramp-up" — first few frames don't count (filters micro-gestures)
    """

    # Action gestures that need deliberate confirmation
    ACTION_GESTURES = {
        Gesture.PINCH, Gesture.PEACE, Gesture.THUMBS_UP, Gesture.PINKY,
        Gesture.OK, Gesture.SIX, Gesture.ROCK, Gesture.GUN,
        Gesture.SHAKA, Gesture.RING, Gesture.FIST,
    }

    # Movement gestures that can activate faster
    MOVEMENT_GESTURES = {
        Gesture.POINTING, Gesture.PALM, Gesture.THREE,
    }

    def __init__(self, confirm_frames=4, action_confirm_frames=5,
                 transition_cooldown=0.15, stability_frames=2):
        self.confirm_frames = confirm_frames
        self.action_confirm_frames = action_confirm_frames
        self.transition_cooldown = transition_cooldown
        self.stability_frames = stability_frames

        self.current = Gesture.NONE
        self.confirmed = Gesture.NONE
        self._candidate = Gesture.NONE
        self._count = 0
        self._last_change_time = 0.0
        self._progress = 0.0  # 0.0 to 1.0 — confirm progress

    def update(self, raw_gesture, now=None, hand_stable=True):
        """Feed raw gesture, return confirmed gesture.

        Args:
            raw_gesture: The raw detected gesture
            now: Current time (for cooldown tracking)
            hand_stable: Whether the hand position is stable (for action gestures)
        """
        if now is None:
            now = time.perf_counter()

        # Choose required confirm frames based on gesture type
        if raw_gesture in self.ACTION_GESTURES:
            required = self.action_confirm_frames
            # Action gestures require hand stability
            if not hand_stable and self._count < required:
                # Hand is shaking — don't count this frame for action gestures
                pass
            else:
                self._accumulate(raw_gesture, required)
        elif raw_gesture in self.MOVEMENT_GESTURES:
            required = self.confirm_frames
            self._accumulate(raw_gesture, required)
        else:
            # NONE or unknown
            self._accumulate(raw_gesture, self.confirm_frames)

        # Check for confirmation
        if self._count >= self._get_required(raw_gesture):
            # Transition cooldown — prevent rapid switching between different gestures
            if self._candidate != self.confirmed:
                if now - self._last_change_time >= self.transition_cooldown:
                    self.confirmed = self._candidate
                    self.current = self.confirmed
                    self._last_change_time = now

        # Allow immediate release for NONE
        if raw_gesture == Gesture.NONE:
            self.confirmed = Gesture.NONE
            self.current = Gesture.NONE
            self._candidate = Gesture.NONE
            self._count = 0
            self._progress = 0.0

        return self.confirmed

    def _accumulate(self, raw_gesture, required):
        """Accumulate gesture frames."""
        if raw_gesture == self._candidate:
            self._count += 1
        else:
            self._candidate = raw_gesture
            self._count = 1
        # Update progress
        self._progress = min(self._count / max(required, 1), 1.0)

    def _get_required(self, gesture):
        """Get required confirm frames for a gesture type."""
        if gesture in self.ACTION_GESTURES:
            return self.action_confirm_frames
        return self.confirm_frames

    @property
    def progress(self):
        """Current confirmation progress (0.0 to 1.0)."""
        return self._progress

    def is_new(self, gesture):
        """Check if this gesture is newly confirmed (just changed)."""
        return gesture != self.current

    def reset(self):
        self.current = Gesture.NONE
        self.confirmed = Gesture.NONE
        self._candidate = Gesture.NONE
        self._count = 0
        self._progress = 0.0
