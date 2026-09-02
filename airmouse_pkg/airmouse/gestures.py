"""
Gesture Recognition Engine v4.0 — Angle-based detection + hysteresis.

v4.0 improvements:
  - ANGLE-based finger curl detection (true MCP-PIP-DIP joint angles)
    More accurate than distance ratios at all distances
  - HYSTERESIS on finger up/down (prevents gesture flapping)
    Different thresholds for entering "up" state vs "down" state
  - Per-finger state tracking with persistent state
  - Better thumb detection using thumb-to-index-mcp angle
  - Higher-precision pinch detection with z-axis consideration

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
    Gesture.GUN:        {"emoji": "9", "name": "Gun",      "desc": "Thumb up + index pointing (L-shape)",   "action": "Show desktop / Minimize all"},
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


def _angle_at(joint, p1, p3):
    """Compute angle at `joint` formed by p1-joint-p3 (in degrees).

    Uses cosine rule: cos(theta) = (a^2 + b^2 - c^2) / (2ab)
    """
    a = _dist(p1, joint)
    b = _dist(joint, p3)
    c = _dist(p1, p3)
    if a < 1e-6 or b < 1e-6:
        return 180.0
    cos_theta = (a*a + b*b - c*c) / (2 * a * b)
    cos_theta = max(-1.0, min(1.0, cos_theta))
    return np.degrees(np.arccos(cos_theta))


def _finger_curl_angle(landmarks, mcp, pip, dip, tip):
    """Compute finger curl angle at the PIP joint (degrees).

    180° = finger fully extended (straight)
    ~90° = finger fully curled
    ~150° = slightly bent

    This is much more accurate than distance ratios — works at any distance.
    """
    return _angle_at(landmarks[pip], landmarks[mcp], landmarks[dip])


# Per-finger hysteresis state — prevents flapping when finger is borderline
# Each finger has separate "going up" and "going down" thresholds
_FINGER_HYSTERESIS = {
    # finger_name: (up_threshold_deg, down_threshold_deg)
    # Up = angle must exceed this to count as "up"
    # Down = angle must drop below this to count as "down"
    # Difference = hysteresis band (~15°) — prevents flapping
    'index':  (160.0, 145.0),
    'middle': (160.0, 145.0),
    'ring':   (160.0, 145.0),
    'pinky':  (155.0, 140.0),  # Pinky is shorter, thresholds slightly lower
}

# Persistent state for hysteresis
_finger_state = {'index': False, 'middle': False, 'ring': False, 'pinky': False}


def _finger_up_angle(landmarks, mcp, pip, dip, tip, finger_name='index'):
    """Angle-based finger up detection with hysteresis.

    Uses PIP joint angle:
      - > 160° = finger straight (extended)
      - < 145° = finger curled (folded)
      - 145-160° = hysteresis band (keep previous state)

    This prevents gestures from flapping when fingers are borderline.
    """
    angle = _finger_curl_angle(landmarks, mcp, pip, dip, tip)
    up_thresh, down_thresh = _FINGER_HYSTERESIS[finger_name]

    current_state = _finger_state[finger_name]
    if current_state:
        # Currently up — only go down if angle drops below down_thresh
        if angle < down_thresh:
            _finger_state[finger_name] = False
    else:
        # Currently down — only go up if angle exceeds up_thresh
        if angle > up_thresh:
            _finger_state[finger_name] = True
    return _finger_state[finger_name]


def _finger_up(landmarks, tip, pip, mcp, finger_name='index'):
    """Backward-compat wrapper — uses angle-based detection with hysteresis."""
    # Infer DIP index from PIP index (DIP = PIP + 1)
    dip = pip + 1
    return _finger_up_angle(landmarks, mcp, pip, dip, tip, finger_name)


def _thumb_up(landmarks):
    """Thumb up detection — uses CMC-MCP-IP angle.

    A thumb is "up" (extended away from palm) when the CMC-MCP-IP angle is large
    (> 150°). When folded across palm, the angle is much smaller.
    """
    angle = _angle_at(landmarks[THUMB_MCP], landmarks[THUMB_CMC], landmarks[THUMB_IP])
    # Use a slightly lower threshold for thumb — it's less angular than other fingers
    return angle > 150.0


def _thumb_extended_out(landmarks):
    """Check if thumb is extended outward (away from index finger).

    Used for SHAKA / ROCK / SIX gestures where thumb needs to be out, not just up.
    """
    # Distance from thumb tip to index MCP (palm edge)
    thumb_to_palm = _dist(landmarks[THUMB_TIP], landmarks[INDEX_MCP])
    # Distance from thumb MCP to index MCP (anatomical reference)
    ref = _dist(landmarks[THUMB_MCP], landmarks[INDEX_MCP])
    if ref < 1e-6:
        return False
    # If thumb tip is more than 1.3x its MCP distance from index MCP, it's extended out
    return thumb_to_palm > ref * 1.3


def _finger_confidence(landmarks, tip, pip, mcp):
    """Return 0.0-1.0 confidence that a finger is up.

    Based on PIP angle — 180° = 1.0 (fully extended), 90° = 0.0 (fully curled).
    """
    dip = pip + 1
    angle = _finger_curl_angle(landmarks, mcp, pip, dip, tip)
    # Map 145°-170° to 0.0-1.0
    if angle >= 170:
        return 1.0
    elif angle >= 145:
        return (angle - 145) / 25.0
    else:
        return 0.0


def recognize_gesture(landmarks, pinch_threshold=0.07):
    """Classify hand gesture from 21 landmarks.

    v4.0: Uses angle-based detection with hysteresis — no more flapping.

    Returns dict with gesture, finger states, confidence, and positions.
    """

    idx = _finger_up(landmarks, INDEX_TIP, INDEX_PIP, INDEX_MCP, 'index')
    mid = _finger_up(landmarks, MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP, 'middle')
    ring = _finger_up(landmarks, RING_TIP, RING_PIP, RING_MCP, 'ring')
    pin = _finger_up(landmarks, PINKY_TIP, PINKY_PIP, PINKY_MCP, 'pinky')
    thu = _thumb_up(landmarks)
    thu_out = _thumb_extended_out(landmarks)

    # Confidence scores for each finger
    idx_conf = _finger_confidence(landmarks, INDEX_TIP, INDEX_PIP, INDEX_MCP)
    mid_conf = _finger_confidence(landmarks, MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP)
    ring_conf = _finger_confidence(landmarks, RING_TIP, RING_PIP, RING_MCP)
    pin_conf = _finger_confidence(landmarks, PINKY_TIP, PINKY_PIP, PINKY_MCP)

    pinch_dist = _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP])
    ok_dist = _dist(landmarks[THUMB_TIP], landmarks[MIDDLE_TIP])

    # Pinch: thumb-index distance below threshold AND middle is not up
    # (avoids misclassifying peace as pinch)
    is_pinch = pinch_dist < pinch_threshold and not mid
    # OK: thumb-middle distance below threshold AND index/ring are not up
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
    elif thu_out and pin and not idx and not mid and not ring:
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


def reset_finger_state():
    """Reset hysteresis state — call when hand is lost."""
    _finger_state.update({'index': False, 'middle': False, 'ring': False, 'pinky': False})


class SwipeDetector:
    """Detects horizontal swipe gestures from hand velocity history.

    v4.0: Better velocity tracking with longer history and adaptive threshold.
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

    v4.0 improvements:
    - Faster confirmation (3 frames movement, 4 action)
    - Shorter transition cooldown (0.12s) for snappier feel
    - Better progress tracking

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

    def __init__(self, confirm_frames=3, action_confirm_frames=4,
                 transition_cooldown=0.12, stability_frames=2):
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
        reset_finger_state()
