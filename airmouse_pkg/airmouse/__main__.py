"""
AirMouse v3.2.0 — Iron Man Next-Gen Edition

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

Keyboard shortcuts (in camera window):
    [q] quit   [d] debug   [r] recalibrate   [s] sound toggle
    [p] precision mode   [t] tutorial   [h] help
"""

import os
import sys
import time
import argparse

import cv2
import numpy as np
import airmouse as _pkg

from .physics import (DualStageJitterFilter, HomePosition, ExponentialCurve,
                       AdaptiveSpringDamper, MomentumThrow, EdgeGravity,
                       VelocityPredictor, PositionSmoother)
from .tracker import HandTracker
from .gestures import (recognize_gesture, SwipeDetector, GestureStateMachine,
                        Gesture, GESTURE_INFO)
from .mouse_controller import MouseController
from .audio import AudioFeedback
from .config import Config, CONFIG_PATH
from .tutorial import run_tutorial
from .display import enumerate_displays, get_primary_display


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
              frozen, dragging, scrolling, volume_mode, brightness_mode,
              precision_mode, gsm_progress, gesture_confidence):
    """Draw Iron Man HUD overlay with v3.1 enhancements."""
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

    # Gesture indicator (top-left) with confirm progress ring
    cx, cy = 50, 50
    # Outer ring — progress indicator
    if gsm_progress > 0 and gsm_progress < 1.0:
        # Draw progress arc
        end_angle = int(360 * gsm_progress)
        cv2.ellipse(frame, (cx, cy), (30, 30), -90, 0, end_angle, (0, 255, 255), 3)
        cv2.circle(frame, (cx, cy), 30, (40, 40, 40), 1)
    else:
        cv2.circle(frame, (cx, cy), 28, color, 3)
    cv2.circle(frame, (cx, cy), 4, color, -1)

    info = GESTURE_INFO.get(gesture)
    label = info["name"] if info else gesture
    cv2.putText(frame, label.upper(), (cx + 38, cy + 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)

    # Confidence bar (next to gesture name)
    if gesture_confidence > 0:
        bar_x = cx + 38 + len(label) * 12 + 10
        bar_w = 40
        cv2.rectangle(frame, (bar_x, cy - 6), (bar_x + bar_w, cy + 6), (40, 40, 40), -1)
        fill_w = int(bar_w * gesture_confidence)
        conf_color = (0, 255, 0) if gesture_confidence > 0.7 else (0, 200, 255)
        cv2.rectangle(frame, (bar_x, cy - 6), (bar_x + fill_w, cy + 6), conf_color, -1)

    # Status badges (bottom)
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
    if precision_mode:
        badges.append(("PRECISE", (200, 200, 255)))
    bx = 10
    for bt, bc in badges:
        # Badge background
        (tw, _), _ = cv2.getTextSize(bt, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.rectangle(frame, (bx - 3, h - 38), (bx + tw + 5, h - 12), (20, 20, 20), -1)
        cv2.putText(frame, bt, (bx, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, bc, 2, cv2.LINE_AA)
        bx += tw + 18

    # Physics stats (bottom-right)
    k = spring.current_stiffness
    speed = np.linalg.norm(spring.velocity)
    stats = [f"FPS:{fps:.0f}", f"k:{k:.0f}", f"v:{speed:.0f}"]
    for i, t in enumerate(stats):
        cv2.putText(frame, t, (w - 120, h - 55 + i * 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)

    # Draw tracked finger landmarks
    lm = gesture_result.get("landmarks")
    if lm is not None:
        from .gestures import INDEX_TIP, THUMB_TIP
        tip = lm[INDEX_TIP]
        tx, ty = int(tip.x * w), int(tip.y * h)

        # Cursor crosshair with glow
        glow_color = (0, 80, 80) if not precision_mode else (80, 80, 0)
        cv2.circle(frame, (tx, ty), 11, glow_color, -1)  # Glow
        cv2.circle(frame, (tx, ty), 7, (0, 255, 255) if not precision_mode else (255, 255, 0), -1)
        cv2.circle(frame, (tx, ty), 13, (0, 255, 255) if not precision_mode else (255, 255, 0), 2)

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
    print("  Keyboard: [q]uit [d]ebug [r]ecalibrate [s]ound [p]recision [t]utorial")


def _safe_kb_action(kb_instance, action_name):
    """Safely call a keyboard action — never crash the main loop."""
    if kb_instance is None:
        return
    try:
        method = getattr(kb_instance, action_name)
        method()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        prog="airmouse",
        description="AirMouse v3.1.0 - Iron Man Next-Gen Edition",
    )
    parser.add_argument("--skip", action="store_true", help="Skip tutorial")
    parser.add_argument("--tutorial", action="store_true", help="Force tutorial")
    parser.add_argument("--no-cam", action="store_true", help="Hide camera window")
    parser.add_argument("--no-sound", action="store_true", help="Disable audio")
    parser.add_argument("--cam", type=int, default=None, help="Camera index")
    parser.add_argument("--power", type=float, default=None, help="Exp curve power")
    parser.add_argument("--scale", type=float, default=None, help="Sensitivity scale")
    parser.add_argument("--precision", action="store_true", help="Start in precision mode")
    parser.add_argument("--monitor", type=int, default=None, help="Monitor index (0=primary)")
    parser.add_argument("--list-monitors", action="store_true", help="List monitors and exit")
    parser.add_argument("--autostart", type=str, choices=["on", "off"], default=None, help="Enable/disable auto-start")
    parser.add_argument("--settings", action="store_true", help="Open settings GUI")
    args = parser.parse_args()

    # Handle --settings
    if args.settings:
        from .settings_gui import show_settings
        show_settings()
        return

    # Handle --list-monitors
    if args.list_monitors:
        displays = enumerate_displays()
        print("  Connected displays:")
        for d in displays:
            print(f"    [{d.index}] {d.width}x{d.height} at ({d.x},{d.y}) {'[PRIMARY]' if d.is_primary else ''} — {d.name}")
        return

    # Handle --autostart
    if args.autostart is not None:
        from .autostart import enable_auto_start, disable_auto_start, is_auto_start_enabled
        if args.autostart == "on":
            if enable_auto_start():
                print("  Auto-start ENABLED — AirMouse will start on boot.")
            else:
                print("  Failed to enable auto-start.")
        else:
            if disable_auto_start():
                print("  Auto-start DISABLED.")
            else:
                print("  Failed to disable auto-start.")
        return

    config = Config()
    config.load()
    if not os.path.exists(CONFIG_PATH):
        config.save_defaults()

    if args.no_cam: config.show_camera = False
    if args.no_sound: config.audio_enabled = False
    if args.cam is not None: config.camera_index = args.cam
    if args.power is not None: config.exp_power = args.power
    if args.scale is not None: config.exp_scale = args.scale

    # Multi-monitor support
    displays = enumerate_displays()
    selected_display = None
    if args.monitor is not None and args.monitor < len(displays):
        selected_display = displays[args.monitor]
    else:
        selected_display = get_primary_display()
    screen_w = selected_display.width
    screen_h = selected_display.height

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

    # ═══ Physics stack v3.1 ═══
    spring = AdaptiveSpringDamper(
        mass=config.mass, stiffness_min=config.stiffness_min,
        stiffness_max=config.stiffness_max, damping_ratio=config.damping_ratio,
        speed_threshold=config.speed_threshold,
        max_accel=config.max_accel,
        stiffness_smoothing=config.stiffness_smoothing,
    )
    # Dual-stage jitter filter (micro-tremor + macro smooth)
    jitter_x = DualStageJitterFilter(
        micro_alpha=config.jitter_micro_alpha,
        macro_alpha=config.jitter_macro_alpha,
    )
    jitter_y = DualStageJitterFilter(
        micro_alpha=config.jitter_micro_alpha,
        macro_alpha=config.jitter_macro_alpha,
    )
    home = HomePosition(
        drift_rate=config.home_drift_rate,
        drift_rate_moving=config.home_drift_rate_moving,
    )
    exp_curve = ExponentialCurve(power=config.exp_power, scale=config.exp_scale)
    precision_curve = ExponentialCurve(power=config.precision_power, scale=config.precision_scale)
    momentum = MomentumThrow(
        friction=config.throw_friction,
        min_speed=config.throw_min_speed,
        max_momentum=config.throw_max_momentum,
    )
    edge_grav = EdgeGravity(
        strength=config.edge_gravity_strength,
        edge_zone=config.edge_gravity_zone,
    )
    # v3.1 new physics components
    velocity_predictor = VelocityPredictor(
        prediction_factor=config.prediction_factor,
        max_correction=config.prediction_max_correction,
    )
    position_smoother = PositionSmoother(alpha=config.position_smooth_alpha)

    swipe = SwipeDetector()
    gsm = GestureStateMachine(
        confirm_frames=config.gesture_confirm_frames,
        action_confirm_frames=config.gesture_action_confirm_frames,
        transition_cooldown=config.gesture_transition_cooldown,
    )

    # State
    center = np.array([screen_w / 2.0, screen_h / 2.0])
    spring.reset(center)
    position_smoother.reset(center)
    mouse.move_to(center[0], center[1])

    last_click_time = 0.0
    prev_gesture = Gesture.NONE
    cursor_frozen = False
    dragging = False
    scrolling = False
    volume_mode = False
    brightness_mode = False
    precision_mode = args.precision or config.precision_mode
    scroll_accum = 0.0
    prev_index_y = None
    prev_pos = None
    fps = 0.0
    frame_times = []
    debug_mode = True
    running = True
    gesture_confidence = 0.0
    hand_absent_frames = 0  # Track how long hand has been absent
    hand_was_lost = False    # Track hand-loss for sound feedback

    # Volume/brightness mode debounce
    last_volume_time = 0.0
    last_brightness_time = 0.0
    VOLUME_COOLDOWN = 0.15   # Seconds between volume adjustments
    BRIGHTNESS_COOLDOWN = 0.3  # Brightness changes are slower

    # Keyboard actions (lazy-init, never crash)
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
    print(f"  |     AirMouse v{_pkg.__version__} - Iron Man Next-Gen Edition  |")
    print("  |     14 gestures | Physics cursor | Finger-relative  |")
    print("  +==================================================+")
    print(f"  Screen: {screen_w}x{screen_h}  |  Audio: {'ON' if config.audio_enabled else 'OFF'}  |  Gestures: 14+2 swipe")
    if precision_mode:
        print(f"  Mode: PRECISION (power={config.precision_power}, scale={config.precision_scale})")
    _show_quick_reference()
    print()

    try:
        while running:
            t0 = time.perf_counter()
            dt = 1.0 / max(fps, 1.0)
            now = time.perf_counter()

            hand_data = tracker.read()
            gesture_result = {"gesture": Gesture.NONE, "landmarks": None, "confidence": 0.0}

            if hand_data["hand_found"] and hand_data["landmarks"] is not None:
                hand_absent_frames = 0  # Reset absent counter
                if hand_was_lost:
                    audio.hand_found()
                    hand_was_lost = False
                gesture_result = recognize_gesture(hand_data["landmarks"],
                                                    pinch_threshold=config.pinch_threshold)
                raw_gesture = gesture_result["gesture"]
                gesture_confidence = gesture_result.get("confidence", 0.0)

                # Gesture state machine — with stability and time
                hand_stable = home.is_stable() if home.is_calibrated else False
                gesture = gsm.update(raw_gesture, now=now, hand_stable=hand_stable)
                gesture_changed = (gesture != prev_gesture)

                # Sound feedback on gesture confirmation
                if gesture_changed and gesture != Gesture.NONE:
                    if prev_gesture == Gesture.NONE or gsm.progress >= 1.0:
                        audio.gesture_confirm()

                raw_pos = gesture_result["index_pos"]

                # Dual-stage jitter filter
                filtered_pos = np.array([
                    jitter_x.filter(np.array([raw_pos[0]]))[0],
                    jitter_y.filter(np.array([raw_pos[1]]))[0],
                ])

                # Velocity prediction (reduce perceived latency)
                predicted_pos = velocity_predictor.predict(filtered_pos)

                # Iron Man: finger-relative tracking
                delta = home.get_delta(predicted_pos)

                # Choose sensitivity curve based on precision mode
                active_curve = precision_curve if precision_mode else exp_curve
                mapped = active_curve.map_with_deadzone(delta, deadzone=config.deadzone)
                screen_target = np.array([
                    screen_w / 2 + mapped[0] * screen_w,
                    screen_h / 2 + mapped[1] * screen_h,
                ])
                screen_target[0] = np.clip(screen_target[0], 0, screen_w)
                screen_target[1] = np.clip(screen_target[1], 0, screen_h)

                # Swipe detection
                swipe_gesture = swipe.update(filtered_pos, prev_pos, now)
                prev_pos = filtered_pos.copy()

                if swipe_gesture == Gesture.SWIPE_LEFT:
                    _safe_kb_action(_kb(), "browser_back")
                    audio.click()
                elif swipe_gesture == Gesture.SWIPE_RIGHT:
                    _safe_kb_action(_kb(), "browser_forward")
                    audio.click()

                # ═══ GESTURE ACTIONS ═══

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
                        mouse.mouse.click(mouse._button.middle, 1)
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
                    _safe_kb_action(_kb(), "close_window")
                    audio.click()

                # SIX -> Task switcher (Alt+Tab)
                elif gesture == Gesture.SIX and gesture_changed:
                    _safe_kb_action(_kb(), "switch_window")
                    audio.click()

                # PALM -> Drag mode
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
                    position_smoother.reset(center)
                    mouse.move_to(center[0], center[1])
                    home.reset()
                    audio.click()

                # ROCK -> Minimize
                if gesture == Gesture.ROCK and gesture_changed:
                    _safe_kb_action(_kb(), "minimize_window")
                    audio.click()

                # SHAKA -> Volume mode (move up/down to adjust)
                #   With debounce — don't spam volume changes
                if gesture == Gesture.SHAKA:
                    if not volume_mode and gesture_changed:
                        volume_mode = True
                        audio.mode_enter()
                    if gesture_changed:
                        prev_index_y = filtered_pos[1]
                    if prev_index_y is not None:
                        vd = filtered_pos[1] - prev_index_y
                        if abs(vd) > 0.02 and (now - last_volume_time > VOLUME_COOLDOWN):
                            k = _kb()
                            if k:
                                if vd > 0:
                                    _safe_kb_action(k, "volume_down")
                                else:
                                    _safe_kb_action(k, "volume_up")
                            prev_index_y = filtered_pos[1]
                            last_volume_time = now
                else:
                    if volume_mode:
                        volume_mode = False
                        audio.mode_exit()

                # RING -> Brightness mode (move up/down to adjust)
                #   With debounce — brightness changes are slower
                if gesture == Gesture.RING:
                    if not brightness_mode and gesture_changed:
                        brightness_mode = True
                        audio.mode_enter()
                    if gesture_changed:
                        prev_index_y = filtered_pos[1]
                    if prev_index_y is not None:
                        bd = filtered_pos[1] - prev_index_y
                        if abs(bd) > 0.03 and (now - last_brightness_time > BRIGHTNESS_COOLDOWN):
                            k = _kb()
                            if k:
                                if bd > 0:
                                    _safe_kb_action(k, "brightness_down")
                                else:
                                    _safe_kb_action(k, "brightness_up")
                            prev_index_y = filtered_pos[1]
                            last_brightness_time = now
                else:
                    if brightness_mode:
                        brightness_mode = False
                        audio.mode_exit()

                # ═══ CURSOR MOVEMENT ═══
                # Pointing and navigation gestures move cursor
                if gesture in (Gesture.POINTING, Gesture.PEACE,
                               Gesture.THUMBS_UP, Gesture.PINKY,
                               Gesture.GUN, Gesture.ROCK) and not cursor_frozen:
                    # Spring-damper physics
                    cursor_pos = spring.update(screen_target, dt)
                    # Edge gravity
                    cursor_pos += edge_grav.apply(cursor_pos, screen_w, screen_h)
                    # Momentum throw
                    cursor_pos += momentum.update(spring.velocity, True, dt)
                    # Clamp to screen
                    cursor_pos[0] = np.clip(cursor_pos[0], 0, screen_w)
                    cursor_pos[1] = np.clip(cursor_pos[1], 0, screen_h)
                    # Final position smoothing for silky output
                    smoothed_pos = position_smoother.smooth(cursor_pos)
                    mouse.move_to(smoothed_pos[0], smoothed_pos[1])

                    speed = np.linalg.norm(spring.velocity)
                    if speed > 500:
                        audio.whoosh(speed)

                elif cursor_frozen:
                    spring.update(spring.position, dt)
                    momentum.reset()

                prev_gesture = gesture

            else:
                # No hand detected
                hand_absent_frames += 1

                # Momentum throw keeps cursor gliding
                throw = momentum.update(spring.velocity, False, dt)
                if momentum.is_active:
                    cp = spring.position + throw
                    cp[0] = np.clip(cp[0], 0, screen_w)
                    cp[1] = np.clip(cp[1], 0, screen_h)
                    smoothed = position_smoother.smooth(cp)
                    mouse.move_to(smoothed[0], smoothed[1])

                spring.update(spring.position, dt)

                # Only reset after hand has been absent for a few frames
                # This prevents jitter from momentary detection loss
                if hand_absent_frames > 5:
                    if not hand_was_lost:
                        audio.hand_lost()
                        hand_was_lost = True
                    jitter_x.reset()
                    jitter_y.reset()
                    home.reset()
                    velocity_predictor.reset()
                    gsm.reset()
                    prev_gesture = Gesture.NONE
                    prev_index_y = None
                    prev_pos = None
                    if dragging:
                        mouse.stop_drag()
                        dragging = False
                    scrolling = False
                    if volume_mode:
                        volume_mode = False
                    if brightness_mode:
                        brightness_mode = False

            # Display
            if config.show_camera and debug_mode and hand_data["frame"] is not None:
                _draw_hud(hand_data["frame"], gesture_result, spring, fps,
                          config, cursor_frozen, dragging, scrolling,
                          volume_mode, brightness_mode, precision_mode,
                          gsm.progress, gesture_confidence)
                cv2.imshow("AirMouse", hand_data["frame"])
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    running = False
                elif key == ord("d"):
                    debug_mode = not debug_mode
                elif key == ord("r"):
                    home.reset()
                    spring.reset(center)
                    position_smoother.reset(center)
                    velocity_predictor.reset()
                    audio.recalibrate()
                    print("  -> Recalibrated")
                elif key == ord("s"):
                    config.audio_enabled = not config.audio_enabled
                    audio.enabled = config.audio_enabled
                    print(f"  -> Audio {'ON' if config.audio_enabled else 'OFF'}")
                elif key == ord("p"):
                    precision_mode = not precision_mode
                    audio.precision_toggle()
                    if precision_mode:
                        position_smoother = PositionSmoother(alpha=0.6)  # Smoother in precision mode
                    else:
                        position_smoother = PositionSmoother(alpha=config.position_smooth_alpha)
                    print(f"  -> Precision mode {'ON' if precision_mode else 'OFF'}"
                          f" (power={'1.0' if precision_mode else config.exp_power},"
                          f" scale={'1.0' if precision_mode else config.exp_scale})")
                elif key == ord("t"):
                    run_tutorial(tracker)
                elif key == ord("h"):
                    _show_quick_reference()

            # FPS throttling
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
        running = False
        tracker.release()
        if dragging:
            try:
                mouse.stop_drag()
            except Exception:
                pass
        try:
            if config.show_camera:
                cv2.destroyAllWindows()
        except Exception:
            pass
        print("  AirMouse stopped.")


if __name__ == "__main__":
    main()
