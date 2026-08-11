"""
Gesture Recognition Engine v3 — 14 gestures + swipe + gesture state machine.

Gestures (by finger count & shape):
    POINTING    (☝️)  Index only              → Move cursor
    PEACE       (✌️)  Index + middle          → Right click
    THREE       (3️⃣)  Index + mid + ring     → Scroll mode
    PALM        (🖐️)  All 5 open              → Drag mode
    FIST        (✊)  All closed              → Freeze cursor
    PINCH       (🤏)  Thumb + index close     → Left click
    THUMBS_UP   (👍)  Thumb only              → Double click
    PINKY       (🤙)  Pinky only              → Middle click
    GUN         (👉)  Thumb up + index point  → Snap to center
    ROCK        (🤘)  Index + pinky up        → Minimize window
    SHAKA       (🤙)  Thumb + pinky out       → Volume mode
    OK          (👌)  Thumb + middle close    → Close window
    RING        (💍)  Ring finger only        → Brightness mode
    SIX         (🤟)  Thumb + index + pinky   → Task switcher

Swipe gestures (motion-based):
    SWIPE_LEFT  → Browser back
    SWIPE_RIGHT → Browser forward
"""

import numpy as np
from collections import deque


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
    return _dist(landmarks[tip], wrist) > _dist(landmarks[pip], wrist) * 1.05


def _thumb_up(landmarks):
    return _dist(landmarks[THUMB_TIP], landmarks[INDEX_MCP]) > \
           _dist(landmarks[THUMB_IP], landmarks[INDEX_MCP]) * 1.1


def recognize_gesture(landmarks, pinch_threshold=0.06):
    """Classify hand gesture from 21 landmarks."""

    idx = _finger_up(landmarks, INDEX_TIP, INDEX_PIP, INDEX_MCP)
    mid = _finger_up(landmarks, MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP)
    ring = _finger_up(landmarks, RING_TIP, RING_PIP, RING_MCP)
    pin = _finger_up(landmarks, PINKY_TIP, PINKY_PIP, PINKY_MCP)
    thu = _thumb_up(landmarks)

    pinch_dist = _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP])
    ok_dist = _dist(landmarks[THUMB_TIP], landmarks[MIDDLE_TIP])
    is_pinch = pinch_dist < pinch_threshold and not mid
    is_ok = ok_dist < pinch_threshold and not idx and not ring

    fingers_up = sum([idx, mid, ring, pin, thu])
    finger_spread = fingers_up / 5.0

    index_tip = landmarks[INDEX_TIP]
    index_pos = np.array([index_tip.x, index_tip.y])

    # --- Classification priority ---

    if is_pinch:
        gesture = Gesture.PINCH
    # OK: thumb + middle touch (others not involved)
    elif is_ok:
        gesture = Gesture.OK
    # Gun: thumb up + index pointing + others down
    elif thu and idx and not mid and not ring and not pin:
        gesture = Gesture.GUN
    # Shaka: thumb + pinky out, others down
    elif thu and pin and not idx and not mid and not ring:
        gesture = Gesture.SHAKA
    # Six: thumb + index + pinky out (ILY sign)
    elif thu and idx and pin and not mid and not ring:
        gesture = Gesture.SIX
    # Rock: index + pinky up, others down
    elif idx and pin and not mid and not ring and not thu:
        gesture = Gesture.ROCK
    # Thumbs up: thumb only
    elif thu and not idx and not mid and not ring and not pin:
        gesture = Gesture.THUMBS_UP
    # Pinky only
    elif pin and not idx and not mid and not ring and not thu:
        gesture = Gesture.PINKY
    # Ring only (ring finger only up)
    elif ring and not idx and not mid and not pin and not thu:
        gesture = Gesture.RING
    # Fist: nothing up
    elif not idx and not mid and not ring and not pin:
        gesture = Gesture.FIST
    # Three fingers
    elif idx and mid and ring and not pin:
        gesture = Gesture.THREE
    # Peace
    elif idx and mid and not ring and not pin:
        gesture = Gesture.PEACE
    # Palm: 4+ fingers
    elif fingers_up >= 4:
        gesture = Gesture.PALM
    # Pointing
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
    }


class SwipeDetector:
    """Detects horizontal swipe gestures from hand velocity history.

    Swipe = fast horizontal movement (>threshold) over several frames.
    """

    def __init__(self, speed_threshold=0.4, min_frames=4, cooldown=0.5):
        self.speed_threshold = speed_threshold
        self.min_frames = min_frames
        self.cooldown = cooldown
        self.x_velocities = deque(maxlen=min_frames)
        self.last_swipe_time = 0.0

    def update(self, current_pos, prev_pos, now):
        """Check for swipe. Call every frame.

        Returns: Gesture.SWIPE_LEFT, SWIPE_RIGHT, or NONE
        """
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
    """Prevents accidental gesture triggers during transitions.

    A gesture must be held for N frames before it's confirmed.
    This prevents misfires when moving between gestures.
    """

    def __init__(self, confirm_frames=3):
        self.confirm_frames = confirm_frames
        self.current = Gesture.NONE
        self.confirmed = Gesture.NONE
        self._candidate = Gesture.NONE
        self._count = 0

    def update(self, raw_gesture):
        """Feed raw gesture, return confirmed gesture."""
        if raw_gesture == self._candidate:
            self._count += 1
        else:
            self._candidate = raw_gesture
            self._count = 1

        if self._count >= self.confirm_frames:
            if self._candidate != self.confirmed:
                self.confirmed = self._candidate
                self.current = self.confirmed
        # Allow immediate release for NONE
        if raw_gesture == Gesture.NONE:
            self.confirmed = Gesture.NONE
            self.current = Gesture.NONE
            self._candidate = Gesture.NONE
            self._count = 0

        return self.confirmed

    def is_new(self, gesture):
        """Check if this gesture is newly confirmed (just changed)."""
        return gesture != self.current

    def reset(self):
        self.current = Gesture.NONE
        self.confirmed = Gesture.NONE
        self._candidate = Gesture.NONE
        self._count = 0
