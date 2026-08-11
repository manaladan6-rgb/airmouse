"""
AirMouse v2.0 — Iron Man Edition

    airmouse

Finger-relative cursor control with pure physics.
Your hand stays still. Only finger movements drive the cursor.

Gestures:
    Point (index)      → Move cursor
    Pinch (thumb+idx)  → Left click
    Peace (idx+mid)    → Right click
    Palm (open hand)   → Drag mode
    Fist (closed)      → Freeze cursor
    Peace + move       → Scroll

Keys:
    q  → Quit
    d  → Toggle debug
    r  → Recalibrate home position
    s  → Toggle sound
"""

import sys
import time
import argparse

import cv2
import numpy as np
import airmouse as _pkg

from .physics import (JitterFilter, HomePosition, ExponentialCurve,
                       AdaptiveSpringDamper, MomentumThrow, EdgeGravity)
from .tracker import HandTracker
from .gestures import recognize_gesture, Gesture
from .mouse_controller import MouseController
from .audio import AudioFeedback
from .config import Config


def _get_screen_size():
    try:
        import ctypes
        user32 = ctypes.windll.user32
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    try:
        import tkinter as tk
        root = tk.Tk()
        w, h = root.winfo_screenwidth(), root.winfo_screenheight()
        root.destroy()
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return 1920, 1080


def _draw_hud(frame, gesture_result, cursor_pos, spring, fps, config, frozen, dragging):
    """Draw Iron Man-style HUD overlay."""
    if frame is None:
        return
    h, w = frame.shape[:2]

    # Gesture indicator (top-left)
    gesture = gesture_result.get("gesture", "none")
    colors = {
        Gesture.POINTING: (0, 255, 255),   # Cyan
        Gesture.PINCH: (0, 255, 0),        # Green
        Gesture.PEACE: (255, 200, 0),      # Yellow
        Gesture.PALM: (255, 128, 0),       # Orange
        Gesture.FIST: (0, 0, 255),         # Red
        Gesture.SCROLL: (255, 0, 255),     # Magenta
        "none": (128, 128, 128),           # Gray
    }
    color = colors.get(gesture, (128, 128, 128))

    # Status badges
    badges = []
    if frozen:
        badges.append(("FROZEN", (0, 0, 255)))
    if dragging:
        badges.append(("DRAG", (255, 128, 0)))

    # Draw gesture circle indicator
    cx, cy = 50, 50
    cv2.circle(frame, (cx, cy), 30, color, 3)
    cv2.circle(frame, (cx, cy), 5, color, -1)
    cv2.putText(frame, gesture.upper(), (cx + 40, cy + 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)

    # Badges
    bx = 10
    for badge_text, badge_color in badges:
        cv2.putText(frame, badge_text, (bx, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, badge_color, 2, cv2.LINE_AA)
        bx += 100

    # Physics stats (bottom-right)
    speed = np.linalg.norm(spring.velocity)
    k = spring.current_stiffness
    crit_c = 2.0 * np.sqrt(k * spring.mass)
    stats = [
        f"FPS: {fps:.0f}",
        f"k: {k:.0f}  c: {crit_c:.0f}",
        f"Speed: {speed:.0f}",
    ]
    for i, text in enumerate(stats):
        cv2.putText(frame, text, (w - 180, h - 70 + i * 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)

    # Draw finger landmarks if available
    landmarks = gesture_result.get("landmarks")
    if landmarks is not None:
        from .gestures import INDEX_TIP, THUMB_TIP
        tip = landmarks[INDEX_TIP]
        tx, ty = int(tip.x * w), int(tip.y * h)
        cv2.circle(frame, (tx, ty), 8, (0, 255, 255), -1)
        cv2.circle(frame, (tx, ty), 14, (0, 255, 255), 2)

        thumb = landmarks[THUMB_TIP]
        thx, thy = int(thumb.x * w), int(thumb.y * h)
        cv2.circle(frame, (thx, thy), 5, (255, 200, 0), -1)

        if gesture == Gesture.PINCH:
            cv2.line(frame, (tx, ty), (thx, thy), (0, 255, 0), 2)


def main():
    parser = argparse.ArgumentParser(
        prog="airmouse",
        description="AirMouse v2.0 — Iron Man Edition: Physics-driven finger mouse",
    )
    parser.add_argument("--no-cam", action="store_true", help="Hide camera window")
    parser.add_argument("--no-sound", action="store_true", help="Disable audio")
    parser.add_argument("--cam", type=int, default=None, help="Camera device index")
    parser.add_argument("--power", type=float, default=None, help="Exp curve power (0.4-0.8, lower=more amplified)")
    parser.add_argument("--scale", type=float, default=None, help="Sensitivity scale (1.0-5.0)")
    args = parser.parse_args()

    # Lazy import — pynput needs display
    from .mouse_controller import MouseController

    # Load config
    config = Config()
    config.load()
    if not sys.argv[0].endswith("config.toml"):
        # Save defaults on first run so user can edit later
        import os
        from .config import CONFIG_PATH
        if not os.path.exists(CONFIG_PATH):
            config.save_defaults()
            print(f"  Config saved to {CONFIG_PATH} (edit to customize)")

    # CLI overrides
    if args.no_cam:
        config.show_camera = False
    if args.no_sound:
        config.audio_enabled = False
    if args.cam is not None:
        config.camera_index = args.cam
    if args.power is not None:
        config.exp_power = args.power
    if args.scale is not None:
        config.exp_scale = args.scale

    screen_w, screen_h = _get_screen_size()

    # Initialize components
    tracker = HandTracker(
        camera_index=config.camera_index,
        detection_confidence=config.detection_confidence,
        tracking_confidence=config.tracking_confidence,
    )

    mouse = MouseController(screen_w=screen_w, screen_h=screen_h)
    audio = AudioFeedback(enabled=config.audio_enabled)

    # Physics stack
    spring = AdaptiveSpringDamper(
        mass=config.mass,
        stiffness_min=config.stiffness_min,
        stiffness_max=config.stiffness_max,
        damping_ratio=config.damping_ratio,
        speed_threshold=config.speed_threshold,
    )
    jitter_x = JitterFilter(alpha=config.jitter_alpha)
    jitter_y = JitterFilter(alpha=config.jitter_alpha)
    home = HomePosition(drift_rate=config.home_drift_rate)
    exp_curve = ExponentialCurve(power=config.exp_power, scale=config.exp_scale)
    momentum = MomentumThrow(friction=config.throw_friction, min_speed=config.throw_min_speed)
    edge_grav = EdgeGravity(strength=config.edge_gravity_strength, edge_zone=config.edge_gravity_zone)

    # State
    center = np.array([screen_w / 2, screen_h / 2])
    spring.reset(center)
    mouse.move_to(center[0], center[1])

    last_click_time = 0.0
    prev_gesture = Gesture.NONE
    cursor_frozen = False
    dragging = False
    scroll_accumulator = 0.0
    prev_index_y = None
    fps = 0.0
    frame_times = []
    debug_mode = True

    # Banner
    print()
    print("  ╔════════════════════════════════════════════╗")
    print(f"  ║       AirMouse v{_pkg.__version__} — Iron Man Edition    ║")
    print("  ║   Finger-relative physics-driven mouse      ║")
    print("  ╚════════════════════════════════════════════╝")
    print()
    print(f"  Screen:    {screen_w} x {screen_h}")
    print(f"  Physics:   mass={config.mass}, k=[{config.stiffness_min}-{config.stiffness_max}], ratio={config.damping_ratio}")
    print(f"  Iron Man:  power={config.exp_power}, scale={config.exp_scale}, deadzone={config.deadzone}")
    print(f"  Throw:     friction={config.throw_friction}")
    print(f"  Audio:     {'ON' if config.audio_enabled else 'OFF'}")
    print()
    print("  Point   = move    Pinch = click    Peace = right click")
    print("  Palm    = drag    Fist  = freeze   Peace+move = scroll")
    print("  [q] quit  [d] debug  [r] recalibrate  [s] sound toggle")
    print()

    try:
        while True:
            t0 = time.perf_counter()
            dt = 1.0 / max(fps, 1.0)

            hand_data = tracker.read()
            gesture_result = {"gesture": Gesture.NONE, "landmarks": None}

            if hand_data["hand_found"] and hand_data["landmarks"] is not None:
                # --- Gesture Recognition ---
                gesture_result = recognize_gesture(
                    hand_data["landmarks"],
                    pinch_threshold=config.pinch_threshold,
                )
                gesture = gesture_result["gesture"]
                raw_pos = gesture_result["index_pos"]

                # --- Jitter Filter ---
                filtered_pos = np.array([
                    jitter_x.filter(np.array([raw_pos[0]]))[0],
                    jitter_y.filter(np.array([raw_pos[1]]))[0],
                ])

                # --- Iron Man: Finger-Relative Tracking ---
                delta = home.get_delta(filtered_pos)
                mapped_delta = exp_curve.map_with_deadzone(delta, deadzone=config.deadzone)

                # Convert delta to screen position (center + mapped_delta * screen_size)
                screen_target = np.array([
                    screen_w / 2 + mapped_delta[0] * screen_w,
                    screen_h / 2 + mapped_delta[1] * screen_h,
                ])
                # Clamp target
                screen_target[0] = np.clip(screen_target[0], 0, screen_w)
                screen_target[1] = np.clip(screen_target[1], 0, screen_h)

                # --- Handle Gestures ---
                now = time.perf_counter()

                # FIST → Freeze cursor
                if gesture == Gesture.FIST and prev_gesture != Gesture.FIST:
                    cursor_frozen = not cursor_frozen
                    audio.freeze()

                # PINCH → Left click
                if gesture == Gesture.PINCH and prev_gesture != Gesture.PINCH:
                    if now - last_click_time > config.pinch_cooldown:
                        mouse.left_click()
                        audio.click()
                        last_click_time = now

                # PEACE → Right click or scroll
                if gesture == Gesture.PEACE:
                    if prev_gesture != Gesture.PEACE:
                        prev_index_y = filtered_pos[1]
                        scroll_accumulator = 0.0

                    # Check vertical movement for scroll
                    if prev_index_y is not None:
                        scroll_delta = (filtered_pos[1] - prev_index_y) * 50
                        scroll_accumulator += scroll_delta
                        if abs(scroll_accumulator) > 1.0:
                            mouse.scroll(int(scroll_accumulator))
                            audio.scroll_tick()
                            scroll_accumulator = 0.0
                        prev_index_y = filtered_pos[1]

                elif gesture == Gesture.PEACE and prev_gesture == Gesture.PEACE:
                    # Peace released → right click (if no scroll happened)
                    if abs(scroll_accumulator) < 0.5:
                        if now - last_click_time > config.pinch_cooldown:
                            mouse.right_click()
                            audio.right_click()
                            last_click_time = now

                # PALM → Drag mode
                if gesture == Gesture.PALM and not dragging:
                    mouse.start_drag()
                    dragging = True
                    audio.drag_start()
                elif gesture != Gesture.PALM and dragging:
                    mouse.stop_drag()
                    dragging = False

                # POINTING → Move cursor (default)
                if gesture == Gesture.POINTING and not cursor_frozen:
                    # Physics update
                    cursor_pos = spring.update(screen_target, dt)

                    # Edge gravity
                    grav = edge_grav.apply(cursor_pos, screen_w, screen_h)
                    cursor_pos += grav

                    # Momentum throw
                    throw_offset = momentum.update(spring.velocity, True, dt)
                    cursor_pos += throw_offset

                    # Clamp to screen
                    cursor_pos[0] = np.clip(cursor_pos[0], 0, screen_w)
                    cursor_pos[1] = np.clip(cursor_pos[1], 0, screen_h)

                    mouse.move_to(cursor_pos[0], cursor_pos[1])

                    # Whoosh on fast movement
                    speed = np.linalg.norm(spring.velocity)
                    if speed > 500 and int(fps * now) % 3 == 0:
                        audio.whoosh(speed)

                elif cursor_frozen:
                    # Frozen — spring still settles but don't move mouse
                    spring.update(spring.position, dt)
                    momentum.reset()

                prev_gesture = gesture

            else:
                # No hand — momentum throw kicks in
                throw_offset = momentum.update(spring.velocity, False, dt)
                if momentum.is_active:
                    cursor_pos = spring.position + throw_offset
                    cursor_pos[0] = np.clip(cursor_pos[0], 0, screen_w)
                    cursor_pos[1] = np.clip(cursor_pos[1], 0, screen_h)
                    mouse.move_to(cursor_pos[0], cursor_pos[1])

                spring.update(spring.position, dt)
                jitter_x.reset()
                jitter_y.reset()
                home.reset()
                prev_gesture = Gesture.NONE
                prev_index_y = None
                if dragging:
                    mouse.stop_drag()
                    dragging = False

            # --- Debug Display ---
            if config.show_camera and debug_mode and hand_data["frame"] is not None:
                _draw_hud(
                    hand_data["frame"], gesture_result,
                    spring.position, spring, fps, config,
                    cursor_frozen, dragging,
                )
                cv2.imshow("AirMouse", hand_data["frame"])
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                elif key == ord("d"):
                    debug_mode = not debug_mode
                elif key == ord("r"):
                    home.reset()
                    spring.reset(center)
                    print("  -> Recalibrated")
                elif key == ord("s"):
                    config.audio_enabled = not config.audio_enabled
                    audio.enabled = config.audio_enabled
                    print(f"  -> Audio {'ON' if config.audio_enabled else 'OFF'}")

            # --- FPS ---
            elapsed = time.perf_counter() - t0
            frame_times.append(elapsed)
            if len(frame_times) > 30:
                frame_times.pop(0)
            avg = sum(frame_times) / len(frame_times)
            fps = 1.0 / max(avg, 1e-6)

            min_frame = 1.0 / config.target_fps
            if elapsed < min_frame:
                time.sleep(min_frame - elapsed)

    except KeyboardInterrupt:
        print("\n  Shutting down...")
    finally:
        tracker.release()
        if dragging:
            mouse.stop_drag()
        if config.show_camera:
            cv2.destroyAllWindows()
        print("  AirMouse stopped.")


if __name__ == "__main__":
    main()
