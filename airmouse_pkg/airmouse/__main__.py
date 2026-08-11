"""
AirMouse — Webcam finger-tracking mouse with pure physics cursor motion.

    airmouse                    # Run with defaults
    airmouse --sensitivity 1.5  # Tune sensitivity
    airmouse --no-cam           # Headless (no camera window)

Controls:
    Index finger up     → Move cursor (spring-damper physics)
    Pinch (thumb+index) → Left click
    Middle finger up    → Right click
    Press 'q'           → Quit
    Press 'd'           → Toggle debug overlay
    Press 'r'           → Recalibrate / reset cursor
"""

import sys
import time
import argparse

import cv2
import numpy as np

from .physics import SpringDamper, JitterFilter, VelocityTracker
from .tracker import HandTracker
from .mouse_controller import MouseController


class Config:
    MASS = 1.0
    STIFFNESS = 180.0
    DAMPING = 24.0
    JITTER_ALPHA = 0.35
    X_SENSITIVITY = 1.3
    Y_SENSITIVITY = 1.3
    X_OFFSET = 0.0
    Y_OFFSET = 0.0
    PINCH_COOLDOWN = 0.3
    CAMERA_INDEX = 0
    DETECTION_CONFIDENCE = 0.7
    TRACKING_CONFIDENCE = 0.5
    TARGET_FPS = 30
    SHOW_CAMERA = True
    SHOW_PHYSICS = True


def map_to_screen(norm_pos, screen_w, screen_h, config):
    centered = norm_pos - np.array([0.5, 0.5])
    scaled = centered * np.array([config.X_SENSITIVITY, config.Y_SENSITIVITY])
    mapped = scaled + np.array([0.5, 0.5])
    mapped += np.array([config.X_OFFSET, config.Y_OFFSET])
    mapped = np.clip(mapped, 0.0, 1.0)
    screen_pos = np.array([
        mapped[0] * screen_w,
        mapped[1] * screen_h,
    ])
    return screen_pos


def draw_debug(frame, hand_data, cursor_pos, velocity, spring, fps, config):
    if frame is None:
        return
    h, w = frame.shape[:2]

    if hand_data["landmarks"] is not None:
        from .tracker import HandTracker
        landmarks = hand_data["landmarks"]
        tip = landmarks[HandTracker.INDEX_TIP]
        cx, cy = int(tip.x * w), int(tip.y * h)
        color = (0, 255, 0) if hand_data["index_up"] else (0, 0, 255)
        cv2.circle(frame, (cx, cy), 8, color, -1)
        cv2.circle(frame, (cx, cy), 12, color, 2)

        thumb = landmarks[HandTracker.THUMB_TIP]
        tx, ty = int(thumb.x * w), int(thumb.y * h)
        cv2.circle(frame, (tx, ty), 6, (255, 200, 0), -1)

        if hand_data["pinch"]:
            cv2.line(frame, (cx, cy), (tx, ty), (0, 255, 255), 2)

    speed = np.linalg.norm(velocity)
    lines = [
        f"FPS: {fps:.0f}",
        f"Speed: {speed:.1f} px/s",
        f"Pos: ({cursor_pos[0]:.0f}, {cursor_pos[1]:.0f})",
        f"Pinch: {'YES' if hand_data['pinch'] else 'no'}",
        f"k={config.STIFFNESS:.0f}  c={config.DAMPING:.0f}  m={config.MASS:.1f}",
    ]
    for i, text in enumerate(lines):
        cv2.putText(
            frame, text, (10, 25 + i * 25),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1, cv2.LINE_AA,
        )


def main():
    parser = argparse.ArgumentParser(
        prog="airmouse",
        description="AirMouse - Physics-driven finger mouse control via webcam",
    )
    parser.add_argument("--no-cam", action="store_true", help="Hide camera window")
    parser.add_argument("--cam", type=int, default=0, help="Camera device index")
    parser.add_argument("--sensitivity", type=float, default=1.3, help="Cursor sensitivity (default: 1.3)")
    parser.add_argument("--mass", type=float, default=1.0, help="Cursor mass - more = more momentum (default: 1.0)")
    parser.add_argument("--stiffness", type=float, default=180.0, help="Spring stiffness - more = snappier (default: 180)")
    parser.add_argument("--damping", type=float, default=24.0, help="Damping - more = less overshoot (default: 24)")
    parser.add_argument("--jitter", type=float, default=0.35, help="Jitter filter alpha 0-1 (default: 0.35)")
    args = parser.parse_args()

    config = Config()
    config.CAMERA_INDEX = args.cam
    config.X_SENSITIVITY = args.sensitivity
    config.Y_SENSITIVITY = args.sensitivity
    config.MASS = args.mass
    config.STIFFNESS = args.stiffness
    config.DAMPING = args.damping
    config.JITTER_ALPHA = args.jitter

    if args.no_cam:
        config.SHOW_CAMERA = False

    tracker = HandTracker(
        camera_index=config.CAMERA_INDEX,
        detection_confidence=config.DETECTION_CONFIDENCE,
        tracking_confidence=config.TRACKING_CONFIDENCE,
    )

    mouse = MouseController()
    screen_w, screen_h = mouse.mouse._display.size  # type: ignore

    spring = SpringDamper(
        mass=config.MASS,
        stiffness=config.STIFFNESS,
        damping=config.DAMPING,
    )

    jitter_x = JitterFilter(alpha=config.JITTER_ALPHA)
    jitter_y = JitterFilter(alpha=config.JITTER_ALPHA)
    vel_tracker = VelocityTracker(window=5)

    last_click_time = 0.0
    prev_pinch = False
    debug_mode = True
    fps = 0.0
    frame_times = []

    center = np.array([screen_w / 2, screen_h / 2])
    spring.reset(center)
    mouse.move_to(center[0], center[1])

    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║         AirMouse v1.0.0               ║")
    print("  ║   Physics-driven finger mouse          ║")
    print("  ╚══════════════════════════════════════╝")
    print()
    print(f"  Screen:  {screen_w} x {screen_h}")
    print(f"  Physics: mass={config.MASS}, k={config.STIFFNESS}, c={config.DAMPING}")
    print(f"  Crit. damping = {2 * (config.STIFFNESS * config.MASS) ** 0.5:.1f}")
    print(f"  Jitter filter: alpha={config.JITTER_ALPHA}")
    print(f"  Sensitivity:   {config.X_SENSITIVITY}")
    print()
    print("  Index finger  = move cursor")
    print("  Pinch         = left click")
    print("  Middle+pinch  = right click")
    print("  [q] quit  [d] debug  [r] recalibrate")
    print()

    try:
        while True:
            t0 = time.perf_counter()

            hand_data = tracker.read()

            if hand_data["hand_found"] and hand_data["index_pos"] is not None:
                raw_pos = hand_data["index_pos"]

                filtered_pos = np.array([
                    jitter_x.filter(np.array([raw_pos[0]]))[0],
                    jitter_y.filter(np.array([raw_pos[1]]))[0],
                ])

                screen_target = map_to_screen(filtered_pos, screen_w, screen_h, config)

                dt = 1.0 / max(fps, 1.0)
                cursor_pos = spring.update(screen_target, dt)

                mouse.move_to(cursor_pos[0], cursor_pos[1])

                velocity = vel_tracker.update(cursor_pos)

                now = time.perf_counter()
                if hand_data["pinch"] and not prev_pinch:
                    if now - last_click_time > config.PINCH_COOLDOWN:
                        if hand_data["middle_up"]:
                            mouse.right_click()
                            print("  -> Right click")
                        else:
                            mouse.left_click()
                            print("  -> Left click")
                        last_click_time = now

                prev_pinch = hand_data["pinch"]

            else:
                dt = 1.0 / max(fps, 1.0)
                cursor_pos = spring.update(spring.position, dt)
                velocity = np.zeros(2)

                jitter_x.reset()
                jitter_y.reset()
                vel_tracker.reset()

            if config.SHOW_CAMERA and debug_mode and hand_data["frame"] is not None:
                draw_debug(
                    hand_data["frame"], hand_data,
                    cursor_pos, velocity, spring, fps, config,
                )
                cv2.imshow("AirMouse", hand_data["frame"])
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                elif key == ord("d"):
                    debug_mode = not debug_mode
                elif key == ord("r"):
                    spring.reset(center)
                    jitter_x.reset()
                    jitter_y.reset()
                    print("  -> Recalibrated")

            elif not config.SHOW_CAMERA:
                pass

            elapsed = time.perf_counter() - t0
            frame_times.append(elapsed)
            if len(frame_times) > 30:
                frame_times.pop(0)
            avg_frame_time = sum(frame_times) / len(frame_times)
            fps = 1.0 / max(avg_frame_time, 1e-6)

            min_frame_time = 1.0 / config.TARGET_FPS
            if elapsed < min_frame_time:
                time.sleep(min_frame_time - elapsed)

    except KeyboardInterrupt:
        print("\n  Shutting down...")
    finally:
        tracker.release()
        if config.SHOW_CAMERA:
            cv2.destroyAllWindows()
        print("  AirMouse stopped.")


if __name__ == "__main__":
    main()
