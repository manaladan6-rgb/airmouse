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

v15.2 Gesture Evolution (new poses + motion detectors + pinch events):
    THUMBS_DOWN (15) Thumb only, pointing DOWN -> cancel/reject
    FOUR       (16) Four fingers up, thumb folded (palm family)
    FIVE       (17) All five fingers open incl. thumb (palm family)

  Motion detectors (trajectory/depth/oscillation, all pure-numpy):
    SWIPE_UP / SWIPE_DOWN  vertical swipes (SwipeDetector, dominant-axis
        rule makes horizontal and vertical mutually exclusive per frame)
    CIRCLE_CW / CIRCLE_CCW index-tip circular motion (TrajectoryDetector)
    PUSH / PULL            rapid approach/retreat (DepthDetector: z velocity
                           + hand bounding-box area velocity, combined)
    SHAKE                  high-frequency lateral oscillation, pose-agnostic
                           (OscillationDetector) -> cancel/error
    WAVE                   same oscillation with OPEN PALM held -> hello/wake

  Pinch events (GestureStateMachine, at CONFIRMED level):
    PINCH_HOLD     pinch confirmed and held >= pinch_hold_frames (once/hold)
    PINCH_RELEASE  release after a hold (never after a tap)
    DOUBLE_PINCH   two pinch taps within double_pinch_window (default 0.6s)

POSE PRECEDENCE (most specific wins — order inside recognize_gesture):
    PINCH > OK > GUN > SHAKA > SIX > ROCK > THUMBS_DOWN > THUMBS_UP >
    PINKY > RING > FIST > THREE > PEACE > FIVE > FOUR > PALM > POINTING >
    FIST (fallback).
    FIVE (5/5 fingers) and FOUR (4 fingers, thumb folded) are strictly more
    specific than PALM, which stays the catch-all for every other
    fingers_up >= 4 combination (e.g. index+ring+pinky+thumb) — so the
    historical 4-finger(+thumb) PALM behavior is preserved.
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
    # ── v15.2 gesture evolution ──────────────────────────────────────────
    THUMBS_DOWN = "thumbs_down"
    FOUR = "four"
    FIVE = "five"
    SWIPE_UP = "swipe_up"
    SWIPE_DOWN = "swipe_down"
    CIRCLE_CW = "circle_cw"
    CIRCLE_CCW = "circle_ccw"
    PUSH = "push"
    PULL = "pull"
    SHAKE = "shake"
    WAVE = "wave"
    # Pinch EVENTS (state-machine level) — names align exactly with the
    # registry labels airmouse.gesture_registry.Gestures.PINCH_HOLD /
    # PINCH_RELEASE / DOUBLE_PINCH.
    PINCH_HOLD = "pinch_hold"
    PINCH_RELEASE = "pinch_release"
    DOUBLE_PINCH = "double_pinch"


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
    # ── v15.2 new poses ──────────────────────────────────────────────────
    Gesture.THUMBS_DOWN:{"emoji": "15","name": "Thumbs Down","desc": "Only thumb extended, pointing DOWN",     "action": "Cancel / reject"},
    Gesture.FOUR:       {"emoji": "16","name": "Four",     "desc": "Four fingers up, thumb folded",        "action": "Drag mode (palm family)"},
    Gesture.FIVE:       {"emoji": "17","name": "Five",     "desc": "All five fingers open incl. thumb",    "action": "Drag mode (palm family)"},
}

# Motion-gesture descriptions (v15.2) — trajectory/depth/oscillation and
# swipe gestures.  Kept separate from GESTURE_INFO (poses) so existing
# consumers that iterate GESTURE_INFO see no change.
MOTION_GESTURE_INFO = {
    Gesture.SWIPE_LEFT:  {"name": "Swipe Left",  "desc": "Fast horizontal motion left",        "action": "Browser back"},
    Gesture.SWIPE_RIGHT: {"name": "Swipe Right", "desc": "Fast horizontal motion right",       "action": "Browser forward"},
    Gesture.SWIPE_UP:    {"name": "Swipe Up",    "desc": "Fast vertical motion up",            "action": "Scroll up"},
    Gesture.SWIPE_DOWN:  {"name": "Swipe Down",  "desc": "Fast vertical motion down",          "action": "Scroll down"},
    Gesture.CIRCLE_CW:   {"name": "Circle CW",   "desc": "Clockwise circular fingertip motion",  "action": "Scroll down"},
    Gesture.CIRCLE_CCW:  {"name": "Circle CCW",  "desc": "Counter-clockwise circular motion",   "action": "Scroll up"},
    Gesture.PUSH:        {"name": "Push",        "desc": "Rapid approach (z + hand-area velocity)", "action": "Depth event (contextual)"},
    Gesture.PULL:        {"name": "Pull",        "desc": "Rapid retreat (z + hand-area velocity)",  "action": "Depth event (contextual)"},
    Gesture.SHAKE:       {"name": "Shake",       "desc": "High-frequency lateral oscillation",  "action": "Cancel / error"},
    Gesture.WAVE:        {"name": "Wave",        "desc": "Oscillation with open palm held",     "action": "Hello / wake"},
    Gesture.PINCH_HOLD:  {"name": "Pinch Hold",  "desc": "Pinch confirmed and held",            "action": "Drag start"},
    Gesture.PINCH_RELEASE:{"name": "Pinch Release","desc": "Release after a pinch hold",        "action": "Drop"},
    Gesture.DOUBLE_PINCH:{"name": "Double Pinch", "desc": "Two pinch taps close together",       "action": "Double click"},
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


def _thumb_pointing_down(landmarks):
    """True when the extended thumb points DOWNWARD in image coordinates.

    Image coords grow downward, so a thumbs-down has the thumb TIP below
    both the IP joint and the MCP joint (larger y).  A straight thumb has
    the same CMC-MCP-IP angle whether it points up or down, so this
    direction test is what disambiguates THUMBS_UP from THUMBS_DOWN.
    """
    tip_y = landmarks[THUMB_TIP].y
    return tip_y > landmarks[THUMB_IP].y and tip_y > landmarks[THUMB_MCP].y


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
    v15.2: adds THUMBS_DOWN / FOUR / FIVE poses.

    Returns dict with gesture, finger states, confidence, and positions
    (contract unchanged; the "gesture" value domain is extended with the
    new labels).

    Pose precedence — the MOST SPECIFIC matching pose wins (first match
    in this chain):
        PINCH > OK > GUN > SHAKA > SIX > ROCK > THUMBS_DOWN > THUMBS_UP >
        PINKY > RING > FIST > THREE > PEACE > FIVE > FOUR > PALM >
        POINTING > FIST (fallback)
    - FIVE: all five fingers extended (incl. thumb) — strictly more
      specific than the old all-open PALM.
    - FOUR: the four fingers extended with the thumb folded — distinct
      from FIVE (thumb up) and from PALM.
    - PALM: unchanged catch-all for every other fingers_up >= 4
      combination (e.g. index+ring+pinky+thumb with middle folded).
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
    elif thu and not idx and not mid and not ring and not pin \
            and _thumb_pointing_down(landmarks):
        gesture = Gesture.THUMBS_DOWN
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
    elif idx and mid and ring and pin and thu:
        gesture = Gesture.FIVE
    elif idx and mid and ring and pin and not thu:
        gesture = Gesture.FOUR
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
    """Detects horizontal AND vertical swipe gestures from hand velocity
    history.

    v4.0: Better velocity tracking with longer history and adaptive threshold.
    v15.2: adds vertical tracking — mean Δy over the same 4-frame deque and
    the same 0.5s cooldown — emitting SWIPE_UP / SWIPE_DOWN.

    Mutual exclusion is guaranteed BY CONSTRUCTION: the dominant axis
    (larger |mean velocity|) is evaluated first and at most one swipe can
    be returned per update() call, so a horizontal and a vertical swipe
    can never fire in the same frame.

    y semantics: image coordinates grow downward, so a NEGATIVE mean Δy
    (hand moving up the image) is SWIPE_UP and a positive mean Δy is
    SWIPE_DOWN.  Pure-horizontal input behaves exactly as in v4.0.
    """

    def __init__(self, speed_threshold=0.4, min_frames=4, cooldown=0.5):
        self.speed_threshold = speed_threshold
        self.min_frames = min_frames
        self.cooldown = cooldown
        self.x_velocities = deque(maxlen=min_frames)
        self.y_velocities = deque(maxlen=min_frames)
        self.last_swipe_time = 0.0

    def update(self, current_pos, prev_pos, now):
        """Check for swipe (returns at most one gesture per call)."""
        if prev_pos is None:
            self.x_velocities.append(0.0)
            self.y_velocities.append(0.0)
            return Gesture.NONE

        vx = current_pos[0] - prev_pos[0]
        vy = current_pos[1] - prev_pos[1]
        self.x_velocities.append(vx)
        self.y_velocities.append(vy)

        if now - self.last_swipe_time < self.cooldown:
            return Gesture.NONE

        if len(self.x_velocities) >= self.min_frames:
            avg_vx = np.mean(self.x_velocities)
            avg_vy = np.mean(self.y_velocities)
            # Dominant axis decides — horizontal and vertical can never
            # fire together in the same frame.
            if abs(avg_vx) >= abs(avg_vy):
                if avg_vx > self.speed_threshold:
                    return self._fire(Gesture.SWIPE_RIGHT, now)
                elif avg_vx < -self.speed_threshold:
                    return self._fire(Gesture.SWIPE_LEFT, now)
            else:
                if avg_vy > self.speed_threshold:
                    return self._fire(Gesture.SWIPE_DOWN, now)
                elif avg_vy < -self.speed_threshold:
                    return self._fire(Gesture.SWIPE_UP, now)

        return Gesture.NONE

    def _fire(self, gesture, now):
        self.last_swipe_time = now
        self.x_velocities.clear()
        self.y_velocities.clear()
        return gesture

    def reset(self):
        self.x_velocities.clear()
        self.y_velocities.clear()


class TrajectoryDetector:
    """Detects circular hand motion (CIRCLE_CW / CIRCLE_CCW) from a
    fingertip position trace (feed the normalized index tip).

    Keeps a ring buffer of ~20 normalized (x, y) samples.  When the
    buffered path travels more than ``min_angle_deg`` of angular distance
    around its centroid, with a mean radius inside a plausible band
    [min_radius, max_radius] and a radial deviation small enough to count
    as circular, one event is emitted (1.5s cooldown, buffer cleared
    after firing).

    Direction convention: coordinates are IMAGE coordinates (y grows
    downward), so a POSITIVE accumulated atan2 angle corresponds to
    CLOCKWISE rotation as seen on screen (CIRCLE_CW); negative accumulated
    angle is CIRCLE_CCW.

    Confidence comes from the circularity fit: mean radial deviation
    divided by mean radius (perfect circle → 1.0).
    """

    TWO_PI = 2.0 * np.pi

    def __init__(self, buffer_size=20, min_angle_deg=270.0,
                 min_radius=0.02, max_radius=0.35,
                 max_radial_deviation=0.45, min_samples=8, cooldown=1.5):
        self.buffer = deque(maxlen=buffer_size)
        self.min_angle_deg = float(min_angle_deg)
        self.min_radius = float(min_radius)
        self.max_radius = float(max_radius)
        self.max_radial_deviation = float(max_radial_deviation)
        self.min_samples = int(min_samples)
        self.cooldown = float(cooldown)
        self.last_fire_time = -1e9
        self.last_confidence = 0.0

    def update(self, pos, now):
        """Feed one normalized (x, y) position; return a Gesture label."""
        self.buffer.append((float(pos[0]), float(pos[1])))
        self.last_confidence = 0.0
        if now - self.last_fire_time < self.cooldown:
            return Gesture.NONE
        if len(self.buffer) < max(self.min_samples, 3):
            return Gesture.NONE
        arr = np.asarray(self.buffer, dtype=float)
        cx = float(arr[:, 0].mean())
        cy = float(arr[:, 1].mean())
        dx = arr[:, 0] - cx
        dy = arr[:, 1] - cy
        radii = np.hypot(dx, dy)
        r_mean = float(radii.mean())
        # Plausibility band: too small = jitter around a fixed point
        # (angles are noise), too big = not a deliberate circle.
        if r_mean < self.min_radius or r_mean > self.max_radius:
            return Gesture.NONE
        deviation = float(np.abs(radii - r_mean).mean())
        if deviation / r_mean > self.max_radial_deviation:
            return Gesture.NONE
        angles = np.arctan2(dy, dx)
        diffs = np.diff(angles)
        # unwrap: shortest angular distance per step
        diffs = (diffs + np.pi) % self.TWO_PI - np.pi
        total_deg = float(np.degrees(diffs.sum()))
        if abs(total_deg) < self.min_angle_deg:
            return Gesture.NONE
        quality = 1.0 - min(1.0, deviation / r_mean)
        self.last_confidence = float(np.clip(0.3 + 0.7 * quality, 0.0, 1.0))
        self.last_fire_time = now
        self.buffer.clear()
        return Gesture.CIRCLE_CW if total_deg > 0 else Gesture.CIRCLE_CCW

    def reset(self):
        self.buffer.clear()
        self.last_confidence = 0.0


class DepthDetector:
    """Detects PUSH / PULL (rapid approach / retreat) from two combined
    depth signals:

      1. z-velocity — mean landmark z of the palm (wrist + MCP knuckles).
         MediaPipe z grows with distance from the camera, so DECREASING z
         means the hand is approaching (PUSH) and increasing z a retreat
         (PULL).
      2. area-velocity — relative growth rate of the hand's bounding-box
         area; an approaching hand appears bigger.  This keeps the
         detector working when z is unreliable or flat (e.g. synthetic
         tests).

    Both signals are normalized by their thresholds and combined as a
    mean, so a score of +1.0 means "at threshold" overall (either one
    signal at ~2x its threshold, or both at threshold).  A score whose
    magnitude reaches 1.0 and whose per-frame deltas are sustained
    (consistent sign, one outlier frame tolerated) over ``min_frames``
    deltas fires PUSH (positive) or PULL (negative).  Confidence scales
    with |score|.  Cooldown 0.8s by default.
    """

    def __init__(self, z_velocity_threshold=0.45, area_velocity_threshold=0.9,
                 min_frames=3, cooldown=0.8, buffer_size=10,
                 palm_indices=(0, 5, 9, 13, 17)):
        self.z_velocity_threshold = float(z_velocity_threshold)
        self.area_velocity_threshold = float(area_velocity_threshold)
        self.min_frames = int(min_frames)
        self.cooldown = float(cooldown)
        self.palm_indices = tuple(palm_indices)
        self._buf = deque(maxlen=max(int(buffer_size), self.min_frames + 1))
        self.last_fire_time = -1e9
        self.last_confidence = 0.0
        self.last_score = 0.0

    def update(self, landmarks, now):
        """Feed one 21-landmark hand; return Gesture.PUSH/PULL/NONE."""
        z = float(np.mean([landmarks[i].z for i in self.palm_indices]))
        xs = [lm.x for lm in landmarks]
        ys = [lm.y for lm in landmarks]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        self._buf.append((float(now), z, float(area)))
        self.last_confidence = 0.0
        self.last_score = 0.0
        if now - self.last_fire_time < self.cooldown:
            return Gesture.NONE
        if len(self._buf) < self.min_frames + 1:
            return Gesture.NONE
        win = list(self._buf)[-(self.min_frames + 1):]
        t0, z0, a0 = win[0]
        t1, z1, a1 = win[-1]
        dt = t1 - t0
        if dt <= 0:
            return Gesture.NONE
        z_vel = (z1 - z0) / dt                       # + = retreating
        a_ref = max(abs(a0), 1e-6)
        a_vel = ((a1 - a0) / a_ref) / dt             # + = growing = approach
        score = 0.5 * (-z_vel / self.z_velocity_threshold) \
            + 0.5 * (a_vel / self.area_velocity_threshold)
        self.last_score = score
        if abs(score) < 1.0:
            return Gesture.NONE
        # Sustained check: per-frame deltas must agree with the direction
        # (one outlier frame tolerated out of min_frames).
        dz = np.diff([s[1] for s in win])
        da = np.diff([s[2] for s in win])
        need = max(len(dz) - 1, 1)
        if score > 0:   # approach: z decreasing and/or area growing
            sustained = (int(np.sum(dz < 0)) >= need
                         or int(np.sum(da > 0)) >= need)
        else:           # retreat: z increasing and/or area shrinking
            sustained = (int(np.sum(dz > 0)) >= need
                         or int(np.sum(da < 0)) >= need)
        if not sustained:
            return Gesture.NONE
        self.last_confidence = float(np.clip(abs(score) / 2.0, 0.0, 1.0))
        self.last_fire_time = now
        self._buf.clear()
        return Gesture.PUSH if score > 0 else Gesture.PULL

    def reset(self):
        self._buf.clear()
        self.last_confidence = 0.0
        self.last_score = 0.0


class OscillationDetector:
    """Detects high-frequency lateral oscillation of the whole hand:

      - SHAKE — pose-agnostic: >= ``min_reversals`` direction reversals of
        the hand's x position within ``window`` seconds and amplitude
        (max-min) above ``amplitude_threshold``.  Semantics: cancel/error.
      - WAVE — the same oscillation while an OPEN PALM is held: the caller
        passes ``open_palm`` (e.g. fingers_up >= 4 / PALM-family pose) and
        WAVE fires when the palm was held for at least
        ``open_palm_fraction`` of the window.  Semantics: hello/wake.

    Exactly one of SHAKE/WAVE is returned per fire — they are mutually
    exclusive by construction.  Confidence combines amplitude and
    reversal-frequency consistency.  One event per ``cooldown`` seconds;
    the buffer is cleared after a fire.
    """

    def __init__(self, amplitude_threshold=0.03, window=1.2, min_reversals=3,
                 cooldown=1.2, buffer_size=64, open_palm_fraction=0.6):
        self.amplitude_threshold = float(amplitude_threshold)
        self.window = float(window)
        self.min_reversals = int(min_reversals)
        self.cooldown = float(cooldown)
        self.open_palm_fraction = float(open_palm_fraction)
        self._buf = deque(maxlen=int(buffer_size))
        self.last_fire_time = -1e9
        self.last_confidence = 0.0
        self.last_amplitude = 0.0
        self.last_reversals = 0

    def update(self, pos, now, open_palm=False):
        """Feed one normalized hand position; return WAVE/SHAKE/NONE."""
        self._buf.append((float(now), float(pos[0]), bool(open_palm)))
        self.last_confidence = 0.0
        if now - self.last_fire_time < self.cooldown:
            return Gesture.NONE
        # keep only samples inside the analysis window
        while self._buf and now - self._buf[0][0] > self.window:
            self._buf.popleft()
        if len(self._buf) < self.min_reversals + 2:
            return Gesture.NONE
        xs = [s[1] for s in self._buf]
        amplitude = max(xs) - min(xs)
        self.last_amplitude = amplitude
        reversals = 0
        prev_sign = 0
        for a, b in zip(xs, xs[1:]):
            d = b - a
            if abs(d) < 1e-9:
                continue
            sign = 1 if d > 0 else -1
            if prev_sign and sign != prev_sign:
                reversals += 1
            prev_sign = sign
        self.last_reversals = reversals
        if amplitude < self.amplitude_threshold or reversals < self.min_reversals:
            return Gesture.NONE
        open_frac = sum(1 for s in self._buf if s[2]) / float(len(self._buf))
        gesture = Gesture.WAVE if open_frac >= self.open_palm_fraction \
            else Gesture.SHAKE
        a_factor = min(amplitude / (2.0 * self.amplitude_threshold), 1.0)
        f_factor = min(reversals / float(self.min_reversals + 2), 1.0)
        self.last_confidence = float(
            np.clip(0.35 + 0.35 * a_factor + 0.30 * f_factor, 0.0, 1.0))
        self.last_fire_time = now
        self._buf.clear()
        return gesture

    def reset(self):
        self._buf.clear()
        self.last_confidence = 0.0
        self.last_amplitude = 0.0
        self.last_reversals = 0


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
        Gesture.THUMBS_DOWN,  # v15.2 (deliberate like THUMBS_UP)
    }

    # Movement gestures that can activate faster
    MOVEMENT_GESTURES = {
        Gesture.POINTING, Gesture.PALM, Gesture.THREE,
        Gesture.FOUR, Gesture.FIVE,  # v15.2 (palm family, keeps drag feel)
    }

    def __init__(self, confirm_frames=3, action_confirm_frames=4,
                 transition_cooldown=0.12, stability_frames=2,
                 pinch_hold_frames=15, double_pinch_window=0.6):
        self.confirm_frames = confirm_frames
        self.action_confirm_frames = action_confirm_frames
        self.transition_cooldown = transition_cooldown
        self.stability_frames = stability_frames
        # ── v15.2 pinch-event parameters ─────────────────────────────
        # PINCH_HOLD fires once after this many CONFIRMED pinch frames
        # (~15 frames @ 30fps ≈ 0.5s); DOUBLE_PINCH after two confirmed
        # pinch taps within this window (mirrors GestureRegistry).
        self.pinch_hold_frames = int(pinch_hold_frames)
        self.double_pinch_window = float(double_pinch_window)

        self.current = Gesture.NONE
        self.confirmed = Gesture.NONE
        self._candidate = Gesture.NONE
        self._count = 0
        self._last_change_time = 0.0
        self._progress = 0.0  # 0.0 to 1.0 — confirm progress
        # pinch-event tracking state (consumes the CONFIRMED pinch level,
        # so raw single-frame pinch noise can never become a tap/hold)
        self._pinch_engaged = False
        self._pinch_frames = 0
        self._pinch_hold_fired = False
        self._last_pinch_tap_at = -1e9
        self._pinch_events = []

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

        # ── v15.2: pinch-event tracking at CONFIRMED level ───────────
        # Runs inside update() so PINCH_HOLD / PINCH_RELEASE /
        # DOUBLE_PINCH flow through the same confirm + cooldown
        # machinery as the click gestures (they are edge-triggered
        # events, so the transition cooldown does not gate them).
        self._track_pinch(self.confirmed == Gesture.PINCH, now)

        return self.confirmed

    def _track_pinch(self, engaged, now):
        """Track CONFIRMED pinch engagement → pinch events (v15.2).

        - PINCH_HOLD: emitted ONCE per hold after ``pinch_hold_frames``
          consecutive confirmed-pinch frames.
        - PINCH_RELEASE: on release AFTER a hold (never after a tap).
        - DOUBLE_PINCH: a tap (confirmed pinch released before the hold
          threshold) within ``double_pinch_window`` of the previous tap.
          Gap logic mirrors GestureRegistry's synthesis exactly.
        Events are queued; drain with poll_pinch_events().
        """
        if engaged:
            if not self._pinch_engaged:
                self._pinch_engaged = True
                self._pinch_frames = 1
                self._pinch_hold_fired = False
            else:
                self._pinch_frames += 1
            if (not self._pinch_hold_fired
                    and self._pinch_frames >= self.pinch_hold_frames):
                self._pinch_hold_fired = True
                self._pinch_events.append(Gesture.PINCH_HOLD)
        elif self._pinch_engaged:
            was_hold = self._pinch_hold_fired
            self._pinch_engaged = False
            self._pinch_frames = 0
            self._pinch_hold_fired = False
            if was_hold:
                self._pinch_events.append(Gesture.PINCH_RELEASE)
            else:
                # a tap: confirmed pinch that ended before hold threshold
                gap = now - self._last_pinch_tap_at
                self._last_pinch_tap_at = now
                if gap < self.double_pinch_window:
                    self._pinch_events.append(Gesture.DOUBLE_PINCH)

    def poll_pinch_events(self):
        """Drain queued pinch events (v15.2).

        Returns a list of 0..N labels from {PINCH_HOLD, PINCH_RELEASE,
        DOUBLE_PINCH}; the queue is cleared.
        """
        events = self._pinch_events
        self._pinch_events = []
        return events

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
        self._pinch_engaged = False
        self._pinch_frames = 0
        self._pinch_hold_fired = False
        self._last_pinch_tap_at = -1e9
        self._pinch_events = []
        reset_finger_state()
