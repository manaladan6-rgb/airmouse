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
    Gesture.OK,
    Gesture.RING,
    Gesture.SIX,
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
GOLD = (0, 215, 255)
GRAY = (120, 120, 120)


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


def _draw_dots(frame, count, total, y, radius=10, spacing=40):
    """Draw progress dots."""
    fh, fw = frame.shape[:2]
    start_x = (fw - (total * spacing)) // 2
    for i in range(total):
        cx = start_x + i * spacing
        if i < count:
            cv2.circle(frame, (cx, y), radius, GREEN, -1)
            cv2.circle(frame, (cx, y), radius + 2, GREEN, 1)
        elif i == count:
            cv2.circle(frame, (cx, y), radius, CYAN, 3)
        else:
            cv2.circle(frame, (cx, y), radius, DARK, 2)


def _draw_gesture_silhouette(frame, gesture, cx, cy, size=60):
    """Draw a simple visual hint for the gesture using basic shapes."""
    s = size
    # Draw a circle as palm base
    palm_color = CYAN

    if gesture == Gesture.POINTING:
        # Index pointing up
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        cv2.line(frame, (cx, cy - s // 3), (cx, cy - s), palm_color, 3)
        cv2.circle(frame, (cx, cy - s), 6, palm_color, -1)
    elif gesture == Gesture.PINCH:
        # Thumb + index pinch
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        cv2.line(frame, (cx - s // 3, cy), (cx - s // 2, cy - s // 2), palm_color, 3)
        cv2.line(frame, (cx + s // 3, cy), (cx + s // 2, cy - s // 2), palm_color, 3)
        cv2.circle(frame, (cx, cy - s // 3), 8, GREEN, -1)
    elif gesture == Gesture.PEACE:
        # V sign
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        cv2.line(frame, (cx - 10, cy - s // 3), (cx - 15, cy - s), palm_color, 3)
        cv2.line(frame, (cx + 10, cy - s // 3), (cx + 15, cy - s), palm_color, 3)
    elif gesture == Gesture.FIST:
        cv2.circle(frame, (cx, cy), s // 2, palm_color, 2)
        cv2.line(frame, (cx - s // 3, cy), (cx + s // 3, cy), palm_color, 3)
    elif gesture == Gesture.PALM:
        for dx in [-20, -10, 0, 10, 20]:
            cv2.line(frame, (cx + dx, cy + s // 3), (cx + dx, cy - s // 2), palm_color, 2)
        cv2.line(frame, (cx - 25, cy + s // 3), (cx + 25, cy + s // 3), palm_color, 2)
    elif gesture == Gesture.THUMBS_UP:
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        cv2.line(frame, (cx, cy - s // 3), (cx, cy - s), palm_color, 3)
        cv2.circle(frame, (cx, cy - s), 8, GREEN, -1)
        cv2.line(frame, (cx - s // 3, cy + 5), (cx + s // 3, cy + 5), palm_color, 3)
    elif gesture == Gesture.THREE:
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        for dx in [-15, 0, 15]:
            cv2.line(frame, (cx + dx, cy - s // 3), (cx + dx, cy - s), palm_color, 2)
    elif gesture == Gesture.PINKY:
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        cv2.line(frame, (cx + 20, cy - s // 3), (cx + 20, cy - s), palm_color, 3)
    elif gesture == Gesture.GUN:
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        cv2.line(frame, (cx, cy - s // 3), (cx, cy - s), palm_color, 3)
        cv2.line(frame, (cx - s // 3, cy), (cx - s // 2, cy), palm_color, 3)
    elif gesture == Gesture.ROCK:
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        cv2.line(frame, (cx - 15, cy - s // 3), (cx - 15, cy - s), palm_color, 3)
        cv2.line(frame, (cx + 20, cy - s // 3), (cx + 20, cy - s), palm_color, 3)
    elif gesture == Gesture.SHAKA:
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        cv2.line(frame, (cx - s // 3, cy), (cx - s // 2, cy), palm_color, 3)
        cv2.line(frame, (cx + 20, cy - s // 3), (cx + 20, cy - s), palm_color, 3)
    elif gesture == Gesture.OK:
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        cv2.circle(frame, (cx, cy - s // 3), 12, GREEN, 2)
    elif gesture == Gesture.RING:
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        cv2.line(frame, (cx, cy - s // 3), (cx, cy - s), palm_color, 3)
        cv2.circle(frame, (cx, cy - s), 8, GOLD, -1)
    elif gesture == Gesture.SIX:
        cv2.circle(frame, (cx, cy), s // 3, palm_color, 2)
        cv2.line(frame, (cx - s // 3, cy), (cx - s // 2, cy), palm_color, 3)
        cv2.line(frame, (cx - 10, cy - s // 3), (cx - 10, cy - s), palm_color, 3)
        cv2.line(frame, (cx + 20, cy - s // 3), (cx + 20, cy - s), palm_color, 3)


def run_tutorial(tracker):
    """Run the interactive gesture tutorial.

    Returns True if tutorial completed, False if skipped.
    """
    print()
    print("  +============================================+")
    print("  |       AirMouse v3.0 Gesture Tutorial        |")
    print("  |       Learn every gesture before you start   |")
    print("  +============================================+")
    print()
    print("  Hold each gesture for 1 second to pass.")
    print("  [SPACE] skip gesture  [ESC] skip tutorial")
    print()

    current_idx = 0
    gesture_hold_start = None
    HOLD_DURATION = 1.0  # Seconds to hold gesture

    total = len(TUTORIAL_GESTURES)

    while current_idx < total:
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

        # Dark overlay at top and bottom
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 280), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

        # Step counter
        step_text = f"STEP {current_idx + 1} / {total}"
        _draw_centered_text(frame, step_text, 30,
                            font_scale=0.5, color=GRAY, thickness=1)

        # Title
        _draw_centered_text(frame, "AIRMOUSE GESTURE TUTORIAL", 55,
                            font_scale=0.9, color=CYAN, thickness=2)

        # Gesture name
        title = f'{info["name"].upper()}'
        _draw_centered_text(frame, title, 100,
                            font_scale=1.4, color=WHITE, thickness=3)

        # Gesture silhouette
        _draw_gesture_silhouette(frame, target_gesture, w // 2, 180, size=50)

        # Description
        _draw_centered_text(frame, info["desc"], 245,
                            font_scale=0.6, color=YELLOW, thickness=1)

        # Action
        action_text = f'>> {info["action"]} <<'
        _draw_centered_text(frame, action_text, 270,
                            font_scale=0.55, color=GREEN, thickness=1)

        # Progress bar
        progress = current_idx / total
        _draw_progress_bar(frame, progress, 290)

        # Gesture dots
        _draw_dots(frame, current_idx, total, 310, radius=6, spacing=28)

        # Hold indicator
        if correct and gesture_hold_start is not None:
            hold_progress = hold_time / HOLD_DURATION
            bar_w = 300
            bx = (w - bar_w) // 2
            by = 340
            cv2.rectangle(frame, (bx, by), (bx + bar_w, by + 20), DARK, -1)
            fill = int(bar_w * hold_progress)
            cv2.rectangle(frame, (bx, by), (bx + fill, by + 20), GREEN, -1)
            cv2.rectangle(frame, (bx, by), (bx + bar_w, by + 20), GREEN, 2)
            _draw_centered_text(frame, "HOLD...", by + 50,
                                font_scale=0.7, color=GREEN, thickness=2)
        else:
            _draw_centered_text(frame, "Show this gesture now", 370,
                                font_scale=0.7, color=WHITE, thickness=1)

        # Bottom bar — detected gesture
        cv2.rectangle(frame, (0, h - 70), (w, h), (0, 0, 0), -1)
        det_color = GREEN if correct else RED
        det_text = f"Detected: {detected_gesture.upper()}"
        _draw_centered_text(frame, det_text, h - 25,
                            font_scale=0.55, color=det_color, thickness=1)

        _draw_centered_text(frame, "[SPACE] Skip   [ESC] Exit Tutorial",
                            h - 50, font_scale=0.45, color=GRAY, thickness=1)

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

    # Tutorial complete! — Celebration
    for frame_idx in range(90):
        hand_data = tracker.read()
        frame = hand_data["frame"]
        if frame is None:
            continue
        h, w = frame.shape[:2]

        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

        # Animated celebration
        pulse = abs(np.sin(frame_idx * 0.1)) * 0.3 + 0.7
        c = tuple(int(v * pulse) for v in GREEN)

        _draw_centered_text(frame, "TUTORIAL COMPLETE!", h // 2 - 50,
                            font_scale=1.3, color=c, thickness=3)
        _draw_centered_text(frame, "You mastered all 14 gestures!", h // 2,
                            font_scale=0.65, color=WHITE, thickness=1)
        _draw_centered_text(frame, "Starting AirMouse...", h // 2 + 40,
                            font_scale=0.55, color=CYAN, thickness=1)

        cv2.imshow("AirMouse Tutorial", frame)
        cv2.waitKey(30)

    cv2.destroyAllWindows()
    print("  Tutorial complete! You're ready!")
    return True
