"""
Interactive Tutorial — Teaches you every gesture before you start.

Opens your webcam and guides you through each gesture one by one.
Shows what to do, waits for you to do it, celebrates, then moves on.

Press SPACE to skip a gesture, ESC to skip tutorial entirely.
"""

import cv2
import numpy as np
import time

from .gestures import recognize_gesture, Gesture, GESTURE_INFO
from .tracker import HandTracker


# Gestures to teach (in order of importance)
TUTORIAL_GESTURES = [
    Gesture.POINTING,
    Gesture.PINCH,
    Gesture.PEACE,
    Gesture.PALM,
    Gesture.FIST,
    Gesture.THUMBS_UP,
    Gesture.THREE,
    Gesture.PINKY,
    Gesture.GUN,
    Gesture.ROCK,
    Gesture.SHAKA,
]


# Colors
CYAN = (0, 255, 255)
GREEN = (0, 255, 0)
YELLOW = (0, 255, 200)
RED = (0, 0, 255)
WHITE = (255, 255, 255)
ORANGE = (0, 165, 255)
PURPLE = (255, 0, 255)
DARK = (40, 40, 40)
DARK_GREEN = (0, 120, 0)


def _draw_centered_text(frame, text, y, font_scale=0.8, color=WHITE, thickness=2):
    """Draw text centered horizontally."""
    h, w = frame.shape[:2]
    size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)[0]
    x = (w - size[0]) // 2
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                font_scale, color, thickness, cv2.LINE_AA)


def _draw_progress_bar(frame, progress, y, h=8):
    """Draw a progress bar across the screen."""
    fh, fw = frame.shape[:2]
    bar_w = fw - 100
    # Background
    cv2.rectangle(frame, (50, y), (50 + bar_w, y + h), DARK, -1)
    # Fill
    fill_w = int(bar_w * progress)
    cv2.rectangle(frame, (50, y), (50 + fill_w, y + h), CYAN, -1)


def _draw_countdown(frame, count, y):
    """Draw countdown circles."""
    fh, fw = frame.shape[:2]
    spacing = 50
    start_x = (fw - (len(TUTORIAL_GESTURES) * spacing)) // 2
    for i in range(len(TUTORIAL_GESTURES)):
        cx = start_x + i * spacing
        if i < count:
            cv2.circle(frame, (cx, y), 12, GREEN, -1)
        elif i == count:
            cv2.circle(frame, (cx, y), 12, CYAN, 3)
        else:
            cv2.circle(frame, (cx, y), 12, DARK, 2)


def run_tutorial(tracker):
    """Run the interactive gesture tutorial.

    Returns True if tutorial completed, False if skipped.
    """
    print()
    print("  ╔════════════════════════════════════════════╗")
    print("  ║       AirMouse Gesture Tutorial              ║")
    print("  ╚════════════════════════════════════════════╝")
    print()
    print("  I'll teach you every gesture one by one.")
    print("  Hold each gesture for 1 second to pass.")
    print("  [SPACE] skip gesture  [ESC] skip tutorial")
    print()

    current_idx = 0
    gesture_hold_start = None
    HOLD_DURATION = 1.0  # Seconds to hold gesture

    completed = False

    while current_idx < len(TUTORIAL_GESTURES):
        target_gesture = TUTORIAL_GESTURES[current_idx]
        info = GESTURE_INFO[target_gesture]

        hand_data = tracker.read()
        frame = hand_data["frame"]

        if frame is None:
            time.sleep(0.033)
            continue

        h, w = frame.shape[:2]

        # Detect current gesture
        detected_gesture = Gesture.NONE
        if hand_data["hand_found"] and hand_data["landmarks"] is not None:
            result = recognize_gesture(hand_data["landmarks"])
            detected_gesture = result["gesture"]

        # Check if user is holding the target gesture
        correct = detected_gesture == target_gesture
        if correct:
            if gesture_hold_start is None:
                gesture_hold_start = time.perf_counter()
            hold_time = time.perf_counter() - gesture_hold_start
            if hold_time >= HOLD_DURATION:
                # Gesture passed!
                current_idx += 1
                gesture_hold_start = None
                continue
        else:
            gesture_hold_start = None
            hold_time = 0.0

        # === Draw Tutorial UI ===

        # Dark overlay at top
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 200), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

        # Title
        _draw_centered_text(frame, "AIRMOUSE GESTURE TUTORIAL", 45,
                            font_scale=1.0, color=CYAN, thickness=2)

        # Gesture name + emoji
        title = f'{info["emoji"]}  {info["name"].upper()}'
        _draw_centered_text(frame, title, 90,
                            font_scale=1.2, color=WHITE, thickness=3)

        # Description
        _draw_centered_text(frame, info["desc"], 135,
                            font_scale=0.7, color=YELLOW, thickness=1)

        # Action
        action_text = f'Action: {info["action"]}'
        _draw_centered_text(frame, action_text, 170,
                            font_scale=0.6, color=GREEN, thickness=1)

        # Progress bar
        progress = current_idx / len(TUTORIAL_GESTURES)
        _draw_progress_bar(frame, progress, 200)

        # Gesture dots
        _draw_countdown(frame, current_idx, 230)

        # Hold indicator
        if correct and gesture_hold_start is not None:
            hold_progress = hold_time / HOLD_DURATION
            bar_w = 300
            bx = (w - bar_w) // 2
            by = 270
            cv2.rectangle(frame, (bx, by), (bx + bar_w, by + 20), DARK, -1)
            fill = int(bar_w * hold_progress)
            cv2.rectangle(frame, (bx, by), (bx + fill, by + 20), GREEN, -1)
            cv2.rectangle(frame, (bx, by), (bx + bar_w, by + 20), GREEN, 2)
            _draw_centered_text(frame, "HOLD...", by + 55,
                                font_scale=0.7, color=GREEN, thickness=2)
        else:
            _draw_centered_text(frame, "Show this gesture now", 300,
                                font_scale=0.7, color=WHITE, thickness=1)

        # Detected gesture indicator (bottom)
        cv2.rectangle(frame, (0, h - 80), (w, h), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.0, frame, 1.0, 0, frame)

        det_color = GREEN if correct else RED
        det_text = f"Detected: {detected_gesture.upper()}"
        _draw_centered_text(frame, det_text, h - 30,
                            font_scale=0.6, color=det_color, thickness=1)

        # Instructions
        _draw_centered_text(frame, "[SPACE] Skip   [ESC] Exit Tutorial",
                            h - 55, font_scale=0.5, color=(150, 150, 150), thickness=1)

        # Show frame
        cv2.imshow("AirMouse Tutorial", frame)
        key = cv2.waitKey(1) & 0xFF

        if key == 27:  # ESC
            cv2.destroyAllWindows()
            print("  Tutorial skipped.")
            return False
        elif key == 32:  # SPACE
            current_idx += 1
            gesture_hold_start = None

    # Tutorial complete!
    # Show celebration screen
    for _ in range(90):
        hand_data = tracker.read()
        frame = hand_data["frame"]
        if frame is None:
            continue
        h, w = frame.shape[:2]

        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

        _draw_centered_text(frame, "TUTORIAL COMPLETE!", h // 2 - 40,
                            font_scale=1.2, color=GREEN, thickness=3)
        _draw_centered_text(frame, "You're ready to use AirMouse!", h // 2 + 10,
                            font_scale=0.7, color=WHITE, thickness=1)
        _draw_centered_text(frame, "Starting in a moment...", h // 2 + 50,
                            font_scale=0.5, color=CYAN, thickness=1)

        cv2.imshow("AirMouse Tutorial", frame)
        cv2.waitKey(30)

    cv2.destroyAllWindows()
    print("  Tutorial complete! You're ready!")
    return True
