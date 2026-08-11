"""
AirMouse v3.0 — Iron Man Next-Gen Edition

    airmouse              # Start with tutorial (first run)
    airmouse --skip       # Skip tutorial
    airmouse --tutorial   # Force tutorial

14 Gestures:
    Point   (1)  -> Move cursor          Pinch  (2)  -> Left click
    Peace   (3)  -> Right click          Palm   (4)  -> Drag mode
    Fist    (5)  -> Freeze cursor        Thumb  (6)  -> Double click
    Three   (7)  -> Scroll mode          Pinky  (8)  -> Middle click
    Gun     (9)  -> Snap to center       Rock   (10) -> Minimize
    Shaka   (11) -> Volume mode          OK     (12) -> Close window
    Ring    (13) -> Brightness mode      Six    (14) -> Task switcher

Swipe:
    Left  -> Browser back    Right -> Browser forward
"""

import os
import sys
import time
import argparse

import cv2
import numpy as np
import airmouse as _pkg

from .physics import (JitterFilter, HomePosition, ExponentialCurve,
                       AdaptiveSpringDamper, MomentumThrow, EdgeGravity)
from .tracker import HandTracker
from .gestures import (recognize_gesture, SwipeDetector, GestureStateMachine,
                        Gesture, GESTURE_INFO)
from .mouse_controller import MouseController
from .audio import AudioFeedback
from .config import Config, CONFIG_PATH
from .tutorial import run_tutorial


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


def _draw_hud(frame, gesture_result, spring, fps, config,
              frozen, dragging, scrolling, volume_mode, brightness_mode):
    """Draw Iron Man HUD overlay."""
    if frame is None:
        return
    h, w = frame.shape[:2]
    gesture = gesture_result.get("gesture", "none")

    colors = {
        Gesture.POINTING: (0, 255, 255), Gesture.PINCH: (0, 255, 0),
        Gesture.PEACE: (0, 255, 200), Gesture.PALM: (255, 128, 0),
        Gesture.FIST: (0, 0, 255), Gesture.THUMBS_UP: (0, 200, 0),
        Gesture.THREE: (255, 0, 255), Gesture.PINKY: (200, 200, 0),
        Gesture.GUN: (0, 200, 200), Gesture.ROCK: (200, 0, 200),
        Gesture.SHAKA: (0, 200, 100), Gesture.OK: (0, 215, 255),
        Gesture.RING: (255, 200, 0), Gesture.SIX: (100, 200, 200),
        "none": (80, 80, 80),
    }
    color = colors.get(gesture, (80, 80, 80))

    # Gesture circle
    cx, cy = 50, 50
    cv2.circle(frame, (cx, cy), 28, color, 3)
    cv2.circle(frame, (cx, cy), 4, color, -1)

    info = GESTURE_INFO.get(gesture)
    label = info["name"] if info else gesture
    cv2.putText(frame, label.upper(), (cx + 38, cy + 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)

    # Badges
    badges = []
    if frozen:
        badges.append(("FROZEN", (0, 0, 255)))
    if dragging:
        badges.append(("DRAG", (255, 128, 0)))
    if scrolling:
        badges.append(("SCROLL", (255, 0, 255)))
    if volume_mode:
        badges.append(("VOL", (0, 200, 100)))
    if brightness_mode:
        badges.append(("BRIGHT", (255, 200, 0)))
    bx = 10
    for bt, bc in badges:
        cv2.putText(frame, bt, (bx, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, bc, 2, cv2.LINE_AA)
        bx += 100

    # Stats
    k = spring.current_stiffness
    speed = np.linalg.norm(spring.velocity)
    stats = [f"FPS:{fps:.0f}", f"k:{k:.0f}", f"v:{speed:.0f}"]
    for i, t in enumerate(stats):
        cv2.putText(frame, t, (w - 120, h - 55 + i * 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)

    # Landmarks
    lm = gesture_result.get("landmarks")
    if lm is not None:
        from .gestures import INDEX_TIP, THUMB_TIP
        tip = lm[INDEX_TIP]
        tx, ty = int(tip.x * w), int(tip.y * h)
        cv2.circle(frame, (tx, ty), 7, (0, 255, 255), -1)
        cv2.circle(frame, (tx, ty), 13, (0, 255, 255), 2)
        thumb = lm[THUMB_TIP]
        thx, thy = int(thumb.x * w), int(thumb.y * h)
        cv2.circle(frame, (thx, thy), 4, (255, 200, 0), -1)
        if gesture == Gesture.PINCH:
            cv2.line(frame, (tx, ty), (thx, thy), (0, 255, 0), 2)


def _show_quick_reference():
    """Print gesture reference to terminal."""
    print("  +------------------------------------------------------+")
    print("  |              GESTURE QUICK REFERENCE                  |")
    print("  +------------------------------------------------------+")
    for g in [Gesture.POINTING, Gesture.PINCH, Gesture.PEACE, Gesture.PALM,
              Gesture.FIST, Gesture.THUMBS_UP, Gesture.THREE, Gesture.PINKY,
              Gesture.GUN, Gesture.ROCK, Gesture.SHAKA, Gesture.OK,
              Gesture.RING, Gesture.SIX]:
        info = GESTURE_INFO[g]
        row = f"  |  {info['emoji']:>2}  {info['name']:<10} {info['desc']:<28} -> {info['action']:<22}|"
        print(row)
    print("  |  <-  Swipe Left   Fast left motion              -> Browser Back       |")
    print("  |  ->  Swipe Right  Fast right motion             -> Browser Forward    |")
    print("  +------------------------------------------------------+")


def main():
    parser = argparse.ArgumentParser(
        prog="airmouse",
        description="AirMouse v3.0 — Iron Man Next-Gen Edition",
    )
    parser.add_argument("--skip", action="store_true", help="Skip tutorial")
    parser.add_argument("--tutorial", action="store_true", help="Force tutorial")
    parser.add_argument("--no-cam", action="store_true", help="Hide camera window")
    parser.add_argument("--no-sound", action="store_true", help="Disable audio")
    parser.add_argument("--cam", type=int, default=None, help="Camera index")
    parser.add_argument("--power", type=float, default=None, help="Exp curve power")
    parser.add_argument("--scale", type=float, default=None, help="Sensitivity scale")
    args = parser.parse_args()

    from .mouse_controller import MouseController

    config = Config()
    config.load()
    if not os.path.exists(CONFIG_PATH):
        config.save_defaults()

    if args.no_cam: config.show_camera = False
    if args.no_sound: config.audio_enabled = False
    if args.cam is not None: config.camera_index = args.cam
    if args.power is not None: config.exp_power = args.power
    if args.scale is not None: config.exp_scale = args.scale

    screen_w, screen_h = _get_screen_size()

    tracker = HandTracker(
        camera_index=config.camera_index,
        detection_confidence=config.detection_confidence,
        tracking_confidence=config.tracking_confidence,
    )

    mouse = MouseController(screen_w=screen_w, screen_h=screen_h)
    audio = AudioFeedback(enabled=config.audio_enabled)

    # Tutorial check
    tutorial_done_file = os.path.join(os.path.expanduser("~"), ".airmouse", "tutorial_done")
    should_tutorial = args.tutorial or (not args.skip and not os.path.exists(tutorial_done_file))

    if should_tutorial:
        completed = run_tutorial(tracker)
        if completed:
            os.makedirs(os.path.dirname(tutorial_done_file), exist_ok=True)
            with open(tutorial_done_file, "w") as f:
                f.write("done")

    # Physics
    spring = AdaptiveSpringDamper(
        mass=config.mass, stiffness_min=config.stiffness_min,
        stiffness_max=config.stiffness_max, damping_ratio=config.damping_ratio,
        speed_threshold=config.speed_threshold,
    )
    jitter_x = JitterFilter(alpha=config.jitter_alpha)
    jitter_y = JitterFilter(alpha=config.jitter_alpha)
    home = HomePosition(drift_rate=config.home_drift_rate)
    exp_curve = ExponentialCurve(power=config.exp_power, scale=config.exp_scale)
    momentum = MomentumThrow(friction=config.throw_friction, min_speed=config.throw_min_speed)
    edge_grav = EdgeGravity(strength=config.edge_gravity_strength, edge_zone=config.edge_gravity_zone)
    swipe = SwipeDetector()
    gsm = GestureStateMachine(confirm_frames=config.gesture_confirm_frames)

    # State
    center = np.array([screen_w / 2.0, screen_h / 2.0])
    spring.reset(center)
    mouse.move_to(center[0], center[1])

    last_click_time = 0.0
    prev_gesture = Gesture.NONE
    cursor_frozen = False
    dragging = False
    scrolling = False
    volume_mode = False
    brightness_mode = False
    scroll_accum = 0.0
    prev_index_y = None
    prev_pos = None
    fps = 0.0
    frame_times = []
    debug_mode = True

    # Keyboard actions (lazy)
    kb = None
    def _kb():
        nonlocal kb
        if kb is None:
            try:
                from .keyboard import KeyboardActions
                kb = KeyboardActions()
            except Exception:
                pass
        return kb

    # Banner
    print()
    print("  +==================================================+")
    print(f"  |     AirMouse v{_pkg.__version__} — Iron Man Next-Gen Edition  |")
    print("  |     14 gestures | Physics cursor | Finger-relative  |")
    print("  +==================================================+")
    print(f"  Screen: {screen_w}x{screen_h}  |  Audio: {'ON' if config.audio_enabled else 'OFF'}  |  Gestures: 14+2 swipe")
    _show_quick_reference()
    print("  [q] quit  [d] debug  [r] recalibrate  [s] sound  [t] tutorial")
    print()

    try:
        while True:
            t0 = time.perf_counter()
            dt = 1.0 / max(fps, 1.0)

            hand_data = tracker.read()
            gesture_result = {"gesture": Gesture.NONE, "landmarks": None}

            if hand_data["hand_found"] and hand_data["landmarks"] is not None:
                gesture_result = recognize_gesture(hand_data["landmarks"],
                                                    pinch_threshold=config.pinch_threshold)
                raw_gesture = gesture_result["gesture"]

                # Gesture state machine — prevents accidental triggers
                gesture = gsm.update(raw_gesture)
                gesture_changed = (gesture != prev_gesture)

                raw_pos = gesture_result["index_pos"]

                # Jitter
                filtered_pos = np.array([
                    jitter_x.filter(np.array([raw_pos[0]]))[0],
                    jitter_y.filter(np.array([raw_pos[1]]))[0],
                ])

                # Iron Man: relative tracking
                delta = home.get_delta(filtered_pos)
                mapped = exp_curve.map_with_deadzone(delta, deadzone=config.deadzone)
                screen_target = np.array([
                    screen_w / 2 + mapped[0] * screen_w,
                    screen_h / 2 + mapped[1] * screen_h,
                ])
                screen_target[0] = np.clip(screen_target[0], 0, screen_w)
                screen_target[1] = np.clip(screen_target[1], 0, screen_h)

                # Swipe detection
                now = time.perf_counter()
                swipe_gesture = swipe.update(filtered_pos, prev_pos, now)
                prev_pos = filtered_pos.copy()

                if swipe_gesture == Gesture.SWIPE_LEFT:
                    k = _kb()
                    if k:
                        k.browser_back()
                        audio.click()
                elif swipe_gesture == Gesture.SWIPE_RIGHT:
                    k = _kb()
                    if k:
                        k.browser_forward()
                        audio.click()

                # === GESTURE ACTIONS ===

                # PINCH -> Left click
                if gesture == Gesture.PINCH and gesture_changed:
                    if now - last_click_time > config.pinch_cooldown:
                        mouse.left_click()
                        audio.click()
                        last_click_time = now

                # PEACE -> Right click
                elif gesture == Gesture.PEACE and gesture_changed:
                    if now - last_click_time > config.pinch_cooldown:
                        mouse.right_click()
                        audio.right_click()
                        last_click_time = now

                # THUMBS_UP -> Double click
                elif gesture == Gesture.THUMBS_UP and gesture_changed:
                    mouse.double_click()
                    audio.click()
                    last_click_time = now

                # PINKY -> Middle click
                elif gesture == Gesture.PINKY and gesture_changed:
                    try:
                        mouse.mouse.click(mouse.mouse.Button.middle, 1)
                    except Exception:
                        pass
                    audio.right_click()
                    last_click_time = now

                # FIST -> Toggle freeze
                elif gesture == Gesture.FIST and gesture_changed:
                    cursor_frozen = not cursor_frozen
                    audio.freeze()

                # OK -> Close window (Alt+F4)
                elif gesture == Gesture.OK and gesture_changed:
                    k = _kb()
                    if k:
                        k.close_window()
                        audio.click()

                # SIX -> Task switcher (Alt+Tab)
                elif gesture == Gesture.SIX and gesture_changed:
                    k = _kb()
                    if k:
                        k.switch_window()
                        audio.click()

                # PALM -> Drag
                if gesture == Gesture.PALM and not dragging:
                    mouse.start_drag()
                    dragging = True
                    audio.drag_start()
                elif gesture != Gesture.PALM and dragging:
                    mouse.stop_drag()
                    dragging = False

                # THREE -> Scroll mode
                if gesture == Gesture.THREE:
                    scrolling = True
                    if gesture_changed:
                        prev_index_y = filtered_pos[1]
                        scroll_accum = 0.0
                    if prev_index_y is not None:
                        sd = (filtered_pos[1] - prev_index_y) * 40
                        scroll_accum += sd
                        if abs(scroll_accum) > 1.0:
                            mouse.scroll(int(scroll_accum))
                            audio.scroll_tick()
                            scroll_accum = 0.0
                        prev_index_y = filtered_pos[1]
                else:
                    scrolling = False

                # GUN -> Snap to center
                if gesture == Gesture.GUN and gesture_changed:
                    spring.reset(center)
                    mouse.move_to(center[0], center[1])
                    home.reset()
                    audio.click()

                # ROCK -> Minimize
                if gesture == Gesture.ROCK and gesture_changed:
                    k = _kb()
                    if k:
                        k.minimize_window()
                        audio.click()

                # SHAKA -> Volume mode (move up/down to adjust)
                if gesture == Gesture.SHAKA:
                    volume_mode = True
                    if gesture_changed:
                        prev_index_y = filtered_pos[1]
                    if prev_index_y is not None:
                        vd = filtered_pos[1] - prev_index_y
                        if abs(vd) > 0.02:
                            k = _kb()
                            if k:
                                if vd > 0:
                                    k.volume_down()
                                else:
                                    k.volume_up()
                            prev_index_y = filtered_pos[1]
                else:
                    volume_mode = False

                # RING -> Brightness mode (move up/down to adjust)
                if gesture == Gesture.RING:
                    brightness_mode = True
                    if gesture_changed:
                        prev_index_y = filtered_pos[1]
                    if prev_index_y is not None:
                        bd = filtered_pos[1] - prev_index_y
                        if abs(bd) > 0.03:
                            k = _kb()
                            if k:
                                if bd > 0:
                                    k.brightness_down()
                                else:
                                    k.brightness_up()
                            prev_index_y = filtered_pos[1]
                else:
                    brightness_mode = False

                # POINTING (or any non-special gesture) -> Move cursor
                if gesture in (Gesture.POINTING, Gesture.PEACE,
                               Gesture.THUMBS_UP, Gesture.PINKY,
                               Gesture.GUN, Gesture.ROCK) and not cursor_frozen:
                    cursor_pos = spring.update(screen_target, dt)
                    cursor_pos += edge_grav.apply(cursor_pos, screen_w, screen_h)
                    cursor_pos += momentum.update(spring.velocity, True, dt)
                    cursor_pos[0] = np.clip(cursor_pos[0], 0, screen_w)
                    cursor_pos[1] = np.clip(cursor_pos[1], 0, screen_h)
                    mouse.move_to(cursor_pos[0], cursor_pos[1])

                    speed = np.linalg.norm(spring.velocity)
                    if speed > 500:
                        audio.whoosh(speed)

                elif cursor_frozen:
                    spring.update(spring.position, dt)
                    momentum.reset()

                prev_gesture = gesture

            else:
                # No hand — momentum throw
                throw = momentum.update(spring.velocity, False, dt)
                if momentum.is_active:
                    cp = spring.position + throw
                    cp[0] = np.clip(cp[0], 0, screen_w)
                    cp[1] = np.clip(cp[1], 0, screen_h)
                    mouse.move_to(cp[0], cp[1])

                spring.update(spring.position, dt)
                jitter_x.reset()
                jitter_y.reset()
                home.reset()
                gsm.reset()
                prev_gesture = Gesture.NONE
                prev_index_y = None
                prev_pos = None
                if dragging:
                    mouse.stop_drag()
                    dragging = False
                scrolling = False
                volume_mode = False
                brightness_mode = False

            # Debug display
            if config.show_camera and debug_mode and hand_data["frame"] is not None:
                _draw_hud(hand_data["frame"], gesture_result, spring, fps,
                          config, cursor_frozen, dragging, scrolling,
                          volume_mode, brightness_mode)
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
                elif key == ord("t"):
                    run_tutorial(tracker)

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
