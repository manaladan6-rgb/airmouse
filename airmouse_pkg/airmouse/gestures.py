"""
Gesture Recognition Engine — Classifies hand pose from MediaPipe landmarks.

Gestures:
    POINTING   — Index finger up only        → Move cursor
    PEACE      — Index + middle up            → Right click
    PALM       — All fingers extended         → Drag mode
    FIST       — All fingers closed           → Freeze cursor
    PINCH      — Thumb + index close together → Left click
    SCROLL     — Peace sign + vertical motion → Scroll
    NONE       — No hand detected
"""

import numpy as np


class Gesture:
    POINTING = "pointing"
    PEACE = "peace"
    PALM = "palm"
    FIST = "fist"
    PINCH = "pinch"
    SCROLL = "scroll"
    NONE = "none"


# MediaPipe landmark indices
WRIST = 0
THUMB_CMC = 1
THUMB_MCP = 2
THUMB_IP = 3
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_PIP = 6
INDEX_DIP = 7
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_PIP = 10
MIDDLE_DIP = 11
MIDDLE_TIP = 12
RING_MCP = 13
RING_PIP = 14
RING_DIP = 15
RING_TIP = 16
PINKY_MCP = 17
PINKY_PIP = 18
PINKY_DIP = 19
PINKY_TIP = 20


def _distance(a, b):
    """Euclidean distance between two landmarks."""
    return np.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _finger_extended(landmarks, tip, pip, mcp):
    """Check if a finger is extended (tip above PIP joint)."""
    # A finger is extended if tip is further from wrist than PIP
    wrist = landmarks[WRIST]
    tip_dist = _distance(landmarks[tip], wrist)
    pip_dist = _distance(landmarks[pip], wrist)
    return tip_dist > pip_dist * 1.05  # Small margin for noise


def _thumb_extended(landmarks):
    """Check if thumb is extended (tip far from index MCP)."""
    thumb_tip = landmarks[THUMB_TIP]
    thumb_ip = landmarks[THUMB_IP]
    index_mcp = landmarks[INDEX_MCP]
    # Thumb is extended if tip is far from palm center
    tip_dist = _distance(thumb_tip, index_mcp)
    ip_dist = _distance(thumb_ip, index_mcp)
    return tip_dist > ip_dist * 1.1


def recognize_gesture(landmarks, pinch_threshold=0.06):
    """Classify hand gesture from landmarks.

    Args:
        landmarks: List of 21 MediaPipe landmarks (each with .x, .y, .z)
        pinch_threshold: Distance threshold for pinch detection

    Returns:
        dict with:
            gesture: str — one of Gesture constants
            index_extended: bool
            middle_extended: bool
            ring_extended: bool
            pinky_extended: bool
            thumb_extended: bool
            pinch_distance: float
            index_pos: np.array [x, y] — normalized position of index tip
            finger_spread: float — how open the hand is (0=fist, 1=fully open)
    """
    # Check each finger
    index_up = _finger_extended(landmarks, INDEX_TIP, INDEX_PIP, INDEX_MCP)
    middle_up = _finger_extended(landmarks, MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP)
    ring_up = _finger_extended(landmarks, RING_TIP, RING_PIP, RING_MCP)
    pinky_up = _finger_extended(landmarks, PINKY_TIP, PINKY_PIP, PINKY_MCP)
    thumb_up = _thumb_extended(landmarks)

    # Pinch detection
    pinch_dist = _distance(landmarks[THUMB_TIP], landmarks[INDEX_TIP])
    is_pinch = pinch_dist < pinch_threshold

    # Finger spread (0 = fist, 1 = fully open)
    fingers_up = sum([index_up, middle_up, ring_up, pinky_up, thumb_up])
    finger_spread = fingers_up / 5.0

    # Index tip position
    index_tip = landmarks[INDEX_TIP]
    index_pos = np.array([index_tip.x, index_tip.y])

    # --- Classify gesture ---

    # Pinch overrides everything (click action)
    if is_pinch and not middle_up:
        gesture = Gesture.PINCH
    # Fist — all fingers closed
    elif fingers_up <= 1 and not index_up and not middle_up:
        gesture = Gesture.FIST
    # Peace sign — index + middle up, others down
    elif index_up and middle_up and not ring_up and not pinky_up:
        gesture = Gesture.PEACE
    # Open palm — most fingers extended
    elif fingers_up >= 4:
        gesture = Gesture.PALM
    # Pointing — only index up
    elif index_up and not middle_up and not ring_up:
        gesture = Gesture.POINTING
    # Default to pointing if index is up
    elif index_up:
        gesture = Gesture.POINTING
    else:
        gesture = Gesture.FIST

    return {
        "gesture": gesture,
        "index_extended": index_up,
        "middle_extended": middle_up,
        "ring_extended": ring_up,
        "pinky_extended": pinky_up,
        "thumb_extended": thumb_up,
        "pinch_distance": pinch_dist,
        "index_pos": index_pos,
        "finger_spread": finger_spread,
    }
