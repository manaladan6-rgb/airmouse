"""
AirMouse v9.0.0 — MULTIMODAL INTELLIGENCE EDITION

    airmouse              # Start with tutorial (first run)
    airmouse --skip       # Skip tutorial
    airmouse --tutorial   # Force tutorial
    airmouse --mode direct   # 1:1 finger-to-screen (default, accurate)
    airmouse --mode ironman  # Exponential finger-relative (legacy, stylized)

14 Gestures:
    Point   (1)  -> Move cursor          Pinch  (2)  -> Left click / HOLD = ZOOM
    Peace   (3)  -> Right click          Palm   (4)  -> Drag mode
    Fist    (5)  -> Freeze cursor        Thumb  (6)  -> Double click
    Three   (7)  -> Scroll mode          Pinky  (8)  -> Middle click
    Gun     (9)  -> Snap to center       Rock   (10) -> Minimize
    Shaka   (11) -> Volume mode          OK     (12) -> Close window
    Ring    (13) -> Brightness mode      Six    (14) -> Task switcher

Swipe:
    Left  -> Browser back    Right -> Browser forward

v5.0 POWER FEATURES:
    --voice            Voice commands (30 phrases, works while you gesture)
    --voice-mode turbo MAD voice mode: nonstop listening, fuzzy matching
    --no-kalman        Disable the hybrid One Euro + Kalman fusion filter
    --no-zoom          Disable pinch-to-zoom (hold pinch + move = Ctrl+wheel)
    --no-calibration   Disable adaptive calibration (learns your reach)
    --calibrate        Run a guided 8s calibration sweep on startup
    --record NAME      Record a macro this session (clicks/scrolls/zooms)
    --play NAME        Replay a macro on startup
    --macros           List saved macros and exit

v9.0 MULTIMODAL (eyes + hands + voice + screen understanding):
    --gaze             Enable webcam gaze/eye tracking (iris, blink, dwell)
    --gaze-calibrate   Run guided gaze->screen calibration, save, exit
    --fusion           FUSION mode: gaze targets, hand confirms, voice intents
    --hands-free       HANDS-FREE: eyes target, voice commands, dwell confirm
    --assist           ASSIST mode: observe everything, confirm every action
    --interaction MODE hand|gaze|voice|fusion|hands-free|assist (explicit)
    --no-voice         Disable voice control

Keyboard shortcuts (in camera window):
    [q] quit   [d] debug   [r] recalibrate   [s] sound toggle
    [p] precision mode   [t] tutorial   [h] help
    [v] voice on/off   [k] Kalman hybrid on/off   [z] zoom on/off
    [m] macro record on/off
    [g] gaze on/off    [f] cycle fusion mode   [x] e-stop trip/reset
    ESC  -> EMERGENCY STOP (v9) / quit hook (always available)
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
                       VelocityPredictor, PositionSmoother, DirectTracker,
                       LightJitterFilter)
from .tracker import HandTracker
from .gestures import (recognize_gesture, SwipeDetector, GestureStateMachine,
                        Gesture, GESTURE_INFO)
from .mouse_controller import MouseController
from .audio import AudioFeedback
from .config import Config, CONFIG_PATH
from .tutorial import run_tutorial
from .display import enumerate_displays, get_primary_display
from .voice_control import VoiceCommandEngine, VoiceCommand, SENSITIVITY_PROFILES
from .calibration import AdaptiveCalibration, get_default_calibration
from .macros import MacroRecorder, MacroPlayer, list_macros, MACRO_DIR
from .zoom import PinchZoomController, zoom_scroll
from .interfaces import FusionMode  # v9.0
from .agent import InteractionAgent  # v9.0 orchestrator
from .gaze_calibration import GazeCalibration  # v9.0 gaze calibration
from .gaze import GazeEngine  # v9.0 gaze sensing


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
              precision_mode, gsm_progress, gesture_confidence,
              voice_caption=None, voice_active=False, zoom_active=False,
              recording=False, kalman_on=False, cal_ready=False,
              v9_state=None):
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
    if zoom_active:
        badges.append(("ZOOM", (255, 100, 255)))
    if recording:
        badges.append(("REC", (0, 0, 255)))
    # v9.0 multimodal badges (lightweight single row)
    if v9_state:
        try:
            if v9_state.get("mode"):
                badges.append((f"FUSION:{str(v9_state['mode'])[:4].upper()}",
                               (255, 100, 100)))
            gc = v9_state.get("gaze_conf")
            if gc is not None:
                badges.append((f"GAZE:{int(gc * 100)}%",
                               (100, 220, 255) if gc >= 0.55 else (120, 120, 120)))
            if v9_state.get("target"):
                badges.append((f"T:{str(v9_state['target'])[:22]}", (200, 255, 120)))
            if v9_state.get("intent"):
                badges.append((f"I:{str(v9_state['intent'])[:14].upper()}", (255, 200, 80)))
            if v9_state.get("action"):
                badges.append((f"A:{str(v9_state['action'])[:14].upper()}", (140, 255, 140)))
            if v9_state.get("estop"):
                badges.append(("E-STOP", (0, 0, 255)))
        except Exception:
            pass
    if voice_active:
        badges.append(("VOICE", (0, 255, 180)))
    if kalman_on:
        badges.append(("KALMAN", (180, 255, 0)))
    if cal_ready:
        badges.append(("CAL", (0, 255, 100)))
    if spring is None:
        badges.append(("DIRECT", (0, 255, 255)))
    bx = 10
    for bt, bc in badges:
        # Badge background
        (tw, _), _ = cv2.getTextSize(bt, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.rectangle(frame, (bx - 3, h - 38), (bx + tw + 5, h - 12), (20, 20, 20), -1)
        cv2.putText(frame, bt, (bx, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, bc, 2, cv2.LINE_AA)
        bx += tw + 18

    # Physics stats (bottom-right)
    if spring is not None:
        k = spring.current_stiffness
        speed = np.linalg.norm(spring.velocity)
        stats = [f"FPS:{fps:.0f}", f"k:{k:.0f}", f"v:{speed:.0f}"]
    else:
        stats = [f"FPS:{fps:.0f}", "DIRECT", ""]
    for i, t in enumerate(stats):
        if t:
            cv2.putText(frame, t, (w - 120, h - 55 + i * 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)

    # Voice caption (top-center) — last heard phrase + matched command
    if voice_caption:
        (cw, _), _ = cv2.getTextSize(voice_caption, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        vx = max(4, (w - cw) // 2)
        cv2.rectangle(frame, (vx - 6, 8), (vx + cw + 8, 32), (20, 20, 20), -1)
        cv2.putText(frame, voice_caption, (vx, 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 180), 1, cv2.LINE_AA)

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
    print("            [v]oice [k]alman [z]oom [m]acro-record [h]elp")


def _macro_executor(event, params, mouse, kb=None):
    """v5.0: execute a single macro event — used by macro replay."""
    try:
        if event == "click":
            mouse.left_click()
        elif event == "right_click":
            mouse.right_click()
        elif event == "double_click":
            mouse.double_click()
        elif event == "middle_click":
            if mouse.mouse is not None and mouse._button is not None:
                mouse.mouse.click(mouse._button.middle, 1)
        elif event == "scroll":
            mouse.scroll(int(params.get("amount", 0)))
        elif event == "zoom":
            zoom_scroll(int(params.get("ticks", 0)))
        elif event == "move" and kb is not None:
            pass  # absolute moves are not replayed (screen-dependent)
        elif event == "drag_start":
            mouse.start_drag()
        elif event == "drag_stop":
            mouse.stop_drag()
    except Exception:
        pass


def _safe_kb_action(kb_instance, action_name):
    """Safely call a keyboard action — never crash the main loop."""
    if kb_instance is None:
        return
    try:
        method = getattr(kb_instance, action_name)
        method()
    except Exception:
        pass


def _run_gaze_calibration(config, simulated: bool = False) -> bool:
    """Guided gaze->screen calibration (v9.0).

    Real mode (default): opens a calibration window, shows a 9-point grid,
    collects FaceMesh gaze samples per point, fits + saves the affine
    mapping to ~/.airmouse/gaze_calibration.json.

    Simulated mode (--gaze-sim): deterministic synthetic-eye calibration —
    verifies the complete calibration pipeline without a camera (CI).
    Returns True on success.  Never raises.
    """
    print("  >> GAZE CALIBRATION — follow the on-screen targets with your eyes")
    cal = GazeCalibration(n_points=9)
    engine = GazeEngine({
        "gaze_dwell_time": config.gaze_dwell_time,
        "gaze_min_confidence": config.gaze_min_confidence,
    })
    try:
        if simulated:
            print("  >> SIMULATED eye model (--gaze-sim) — no camera needed")
            import random
            rng = random.Random(42)
            points = cal.begin()
            for t in points:
                for _ in range(14):
                    from airmouse.interfaces import GazeSample
                    noise = rng.uniform(-0.004, 0.004)
                    cal.add_sample(t, GazeSample(x=t[0] + noise, y=t[1] + noise,
                                                 confidence=0.9))
            quality = cal.finish()
        else:
            import cv2 as _cv2
            import numpy as _np
            cam = _cv2.VideoCapture(config.camera_index)
            if not cam.isOpened():
                print("  !! Camera unavailable — use --gaze-sim for a simulated calibration.")
                return False
            points = cal.begin()
            sw, sh = 960, 540
            canvas = _np.zeros((sh, sw, 3), dtype=_np.uint8)
            aborted = False
            for pi, t in enumerate(points):
                tx, ty = int(t[0] * (sw - 80) + 40), int(t[1] * (sh - 80) + 40)
                collected = 0
                t0 = time.perf_counter()
                while collected < 30 and time.perf_counter() - t0 < 3.5:
                    ok, frame = cam.read()
                    if not ok:
                        continue
                    frame = _cv2.flip(frame, 1)
                    st = engine.update(frame)
                    if st.confidence >= 0.35:
                        cal.add_sample(t, type("S", (), {"x": st.x, "y": st.y,
                                                         "confidence": st.confidence,
                                                         "timestamp": st.timestamp})())
                        collected += 1
                    view = canvas.copy()
                    _cv2.circle(view, (tx, ty), 14, (0, 255, 255), -1)
                    _cv2.putText(view, f"Target {pi + 1}/9  ({collected}/30)",
                                 (20, 40), _cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                    _cv2.imshow("AirMouse Gaze Calibration", view)
                    if (_cv2.waitKey(1) & 0xFF) == 27:
                        aborted = True
                        break
                if aborted:
                    break
            cam.release()
            _cv2.destroyAllWindows()
            if aborted:
                print("  >> Calibration aborted.")
                return False
            quality = cal.finish()
        ok = quality.get("status") in ("good", "fair") and cal.save()
        print(f"  >> Calibration quality: {quality.get('status')} "
              f"(mean residual {quality.get('mean_residual_px')} px) "
              f"-> {'SAVED' if ok else 'NOT saved (quality too low)'}")
        return bool(ok)
    except Exception as e:
        print(f"  !! Gaze calibration failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        prog="airmouse",
        description="AirMouse v5.0.0 - Voice + Kalman Edition",
    )
    parser.add_argument("--skip", action="store_true", help="Skip tutorial")
    parser.add_argument("--tutorial", action="store_true", help="Force tutorial")
    parser.add_argument("--no-cam", action="store_true", help="Hide camera window")
    parser.add_argument("--no-sound", action="store_true", help="Disable audio")
    parser.add_argument("--cam", type=int, default=None, help="Camera index")
    parser.add_argument("--power", type=float, default=None, help="Exp curve power")
    parser.add_argument("--scale", type=float, default=None, help="Sensitivity scale")
    parser.add_argument("--precision", action="store_true", help="Start in precision mode")
    parser.add_argument("--mode", type=str, choices=["direct", "ironman"], default=None,
                        help="Tracking mode: direct (1:1) or ironman (exponential)")
    parser.add_argument("--trackpad", action="store_true", help="Trackpad mode: tap=click, hold=drag, 2-finger=scroll")
    parser.add_argument("--monitor", type=int, default=None, help="Monitor index (0=primary)")
    parser.add_argument("--list-monitors", action="store_true", help="List monitors and exit")
    parser.add_argument("--autostart", type=str, choices=["on", "off"], default=None, help="Enable/disable auto-start")
    parser.add_argument("--settings", action="store_true", help="Open settings GUI")
    # ═══ v5.0 flags ═══
    parser.add_argument("--voice", action="store_true", help="Enable voice commands (SpeechRecognition + pyaudio)")
    parser.add_argument("--voice-mode", type=str, choices=["normal", "high", "turbo"], default=None,
                        help="Voice sensitivity: normal | high | turbo (turbo = MAD nonstop listening)")
    parser.add_argument("--mic", type=int, default=None, help="Microphone index (default: system default)")
    parser.add_argument("--no-kalman", action="store_true", help="Disable hybrid One Euro + Kalman filter (pure One Euro)")
    parser.add_argument("--no-zoom", action="store_true", help="Disable pinch-to-zoom gesture")
    parser.add_argument("--no-calibration", action="store_true", help="Disable adaptive calibration")
    parser.add_argument("--calibrate", action="store_true", help="Run guided 8s calibration sweep on startup")
    parser.add_argument("--record", type=str, default=None, metavar="NAME", help="Record a macro this session")
    parser.add_argument("--play", type=str, default=None, metavar="NAME", help="Replay macro NAME on startup")
    parser.add_argument("--macros", action="store_true", help="List saved macros and exit")
    # ═══ v9.0 multimodal flags ═══
    parser.add_argument("--gaze", action="store_true", help="Enable webcam gaze/eye tracking (v9 multimodal)")
    parser.add_argument("--no-gaze", action="store_true", help="Disable gaze even if enabled in config")
    parser.add_argument("--gaze-calibrate", action="store_true", help="Run guided gaze->screen calibration, save, exit")
    parser.add_argument("--gaze-sim", action="store_true", help=argparse.SUPPRESS)  # simulated calibration (CI)
    parser.add_argument("--fusion", action="store_true", help="FUSION mode: gaze targets + hand confirms + voice intents")
    parser.add_argument("--hands-free", action="store_true", help="HANDS-FREE mode: eyes target, voice commands, dwell confirm")
    parser.add_argument("--assist", action="store_true", help="ASSIST mode: multimodal observation, actions need confirmation")
    parser.add_argument("--interaction", type=str, default=None,
                        choices=["hand", "gaze", "voice", "fusion", "hands-free", "assist"],
                        help="v9 interaction mode (overrides --fusion/--hands-free/--assist)")
    parser.add_argument("--no-voice", action="store_true", help="Disable voice control")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    args = parser.parse_args()

    if args.version:
        print(f"AirMouse v{_pkg.__version__} — Multimodal Intelligence Edition")
        return

    # Handle --settings
    if args.settings:
        from .settings_gui import show_settings
        show_settings()
        return

    # Handle --macros (list saved macros)
    if args.macros:
        names = list_macros()
        if names:
            print(f"  Saved macros ({MACRO_DIR}):")
            for n in names:
                print(f"    - {n}")
        else:
            print(f"  No macros saved yet ({MACRO_DIR}).")
            print("  Record one:  airmouse --record my_macro")
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
    if args.mode is not None: config.tracking_mode = args.mode
    # v5.0 flag overrides
    if args.voice: config.voice_enabled = True
    if args.voice_mode is not None: config.voice_sensitivity = args.voice_mode
    if args.no_kalman: config.kalman_enabled = False
    if args.no_zoom: config.zoom_enabled = False
    if args.no_calibration: config.adaptive_calibration = False
    if args.no_voice: config.voice_enabled = False
    # ═══ v9.0 flag overrides ═══
    if args.gaze: config.gaze_enabled = True
    if args.no_gaze: config.gaze_enabled = False
    if args.fusion: config.fusion_mode = "fusion"
    if args.hands_free: config.fusion_mode = "hands_free"
    if args.assist: config.fusion_mode = "assist"
    if args.interaction: config.fusion_mode = args.interaction.replace("-", "_")
    # Handle --gaze-calibrate (v9.0) — runs the guided (or simulated) flow,
    # saves the fit to ~/.airmouse/gaze_calibration.json and exits.
    if args.gaze_calibrate:
        ok = _run_gaze_calibration(config, simulated=args.gaze_sim)
        sys.exit(0 if ok else 1)

    # Trackpad mode — natural trackpad feel (tap=click, hold=drag, 2-finger=scroll)
    trackpad_mode = args.trackpad or getattr(config, 'trackpad_mode', False)
    if trackpad_mode:
        print("  >> TRACKPAD MODE: tap=click, hold=drag, 2-finger=scroll, 3-finger=show desktop")

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

    # ═══ Physics stack v3.2 ═══
    is_direct = (config.tracking_mode == "direct")

    # Direct tracker (v3.2 default) — 1:1 finger-to-screen
    direct_tracker = None
    # Ironman components (legacy)
    spring = None
    jitter_x = jitter_y = None
    home = None
    exp_curve = precision_curve = None
    momentum = None
    edge_grav = None
    velocity_predictor = None
    position_smoother = None

    if is_direct:
        direct_tracker = DirectTracker(
            screen_w=screen_w, screen_h=screen_h,
            movement_threshold=config.direct_movement_threshold,
            pixel_deadzone=config.direct_pixel_deadzone,
            mirror_x=config.direct_mirror_x,
            one_euro_mincutoff=config.one_euro_mincutoff,
            one_euro_beta=config.one_euro_beta,
            one_euro_dcutoff=config.one_euro_dcutoff,
            prediction_factor=config.direct_prediction_factor,
            # v5.0 — hybrid One Euro + Kalman fusion
            use_hybrid=config.kalman_enabled,
            hybrid_process_noise=config.kalman_process_noise,
            hybrid_measurement_noise=config.kalman_measurement_noise,
            hybrid_fusion=config.kalman_fusion,
            hybrid_speed_ref=config.kalman_speed_ref,
        )
    else:
        # Ironman mode physics (legacy v3.1)
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

    # ═══ v5.0 — Adaptive Calibration ═══
    calib = get_default_calibration()
    calib.enabled = bool(config.adaptive_calibration)
    if args.calibrate and config.adaptive_calibration:
        print("  >> Guided calibration: move your hand SLOWLY around the")
        print("     area you want to use for the next 8 seconds...")
        t_end = time.perf_counter() + 8.0
        while time.perf_counter() < t_end:
            hd = tracker.read()
            if hd["hand_found"] and hd["landmarks"] is not None:
                calib.update(hd["index_pos"])
            time.sleep(1.0 / 30.0)
        calib.save()
        print(f"  -> Calibration learned (coverage {calib.coverage:.0%}). Saved.")
    elif config.adaptive_calibration and calib.is_ready:
        print(f"  >> Adaptive calibration: loaded (coverage {calib.coverage:.0%})")

    # ═══ v9.0 — multimodal utterance tap (raw transcripts for NL) ═══
    utterance_queue = []          # raw transcript strings from voice

    def _on_transcript(transcript, command="", score=0.0):
        """Voice engine callback: feed raw text to the v9 NL pipeline."""
        try:
            if agent is not None and transcript:
                utterance_queue.append((transcript, time.perf_counter()))
        except Exception:
            pass

    # ═══ v5.0 — Voice Control ═══
    voice = None
    voice_caption = ""        # last transcript for HUD
    voice_caption_until = 0.0 # show transcript for 2s
    if config.voice_enabled:
        sensitivity = config.voice_sensitivity if config.voice_sensitivity in SENSITIVITY_PROFILES else "high"
        mic_index = config.voice_mic_index if isinstance(config.voice_mic_index, int) and config.voice_mic_index >= 0 else None
        voice = VoiceCommandEngine(sensitivity=sensitivity, mic_index=mic_index,
                                   on_transcript=_on_transcript)
        if voice.is_available():
            voice.start()
            print(f"  >> VOICE ONLINE ({sensitivity} mode) — say: click, right click,"
                  f" scroll up, zoom in, freeze, precision, quit ...")
        else:
            print("  >> Voice requested but SpeechRecognition/pyaudio unavailable.")
            print("     Install:  pip install SpeechRecognition pyaudio")
            voice = None

    # ═══ v9.0 — Interaction Agent (multimodal intelligence) ═══
    # Wires gaze + hand + voice + screen understanding into one pipeline:
    # perception -> fusion -> intent -> action -> verification -> recovery.
    # Pure v5 behaviour is unchanged when the agent is inactive.
    agent = None
    v9_owns_actions = False
    v9_summary = {"intent": "", "action": "", "target": ""}
    if (config.gaze_enabled or config.fusion_mode != "hand") and \
            config.fusion_mode in ("gaze", "voice", "fusion", "hands_free", "assist"):
        try:
            agent = InteractionAgent({
                "gaze_enabled": config.gaze_enabled,
                "voice_enabled": config.voice_enabled,
                "mode": config.fusion_mode,
                "safety_level": config.safety_level,
                "screen_w": screen_w,
                "screen_h": screen_h,
                "screen_refresh": config.screen_refresh_interval,
                "dwell_confirm": True,
                "blink_confirm": config.gaze_blink_click,
                "long_blink_estop": config.gaze_long_blink_estop,
                "min_gaze_confidence": config.gaze_min_confidence,
                "intent_config": {"min_confidence": config.intent_min_confidence},
                "action_config": {"timeout": config.action_timeout,
                                  "max_retries": config.action_max_retries},
                "fusion_config": {"mode_switch_min_interval": 0.35},
                "safety_config": {"max_actions_per_sec": config.max_actions_per_sec,
                                  "min_click_interval": config.min_click_interval,
                                  "confirmation_timeout": config.confirmation_timeout,
                                  "stream_loss_grace": config.stream_loss_grace},
            })
            v9_owns_actions = True
            print(f"  >> V9 MULTIMODAL ONLINE — mode: {config.fusion_mode.upper()}"
                  f"{', gaze ' + ('ON' if config.gaze_enabled else 'off')}"
                  ", fusion engine active")
            print("     Say things like: 'click that', 'scroll down a little',"
                  " 'close this window', 'stop everything'")
            print("     ESC = emergency stop   [f] cycle mode   [g] gaze on/off")
        except Exception as e:
            print(f"  !! v9 agent unavailable ({e}) — continuing in v5 mode")
            agent = None
            v9_owns_actions = False

    # ═══ v5.0 — Pinch-to-Zoom ═══
    # Zoom engages when pinch is HELD past zoom_engage_hold; a quick pinch
    # stays a click. Disabled in trackpad mode (pinch-hold = drag there).
    pinch_zoom = None
    zoom_enabled = config.zoom_enabled and not trackpad_mode
    if zoom_enabled:
        pinch_zoom = PinchZoomController(
            engage_hold=config.zoom_engage_hold,
            gain=config.zoom_gain,
            max_ticks_per_frame=config.zoom_max_ticks,
        )
        print("  >> PINCH-ZOOM: quick pinch = click | hold pinch + move = zoom")

    # ═══ v5.0 — Macro Recorder ═══
    macro_rec = MacroRecorder()
    recording_macro = False
    macro_name = args.record
    if args.record:
        macro_rec.start(args.record)
        recording_macro = True
        print(f"  >> MACRO RECORDING: '{args.record}' — all clicks/scrolls/zooms are captured.")
        print("     Stop with [m] or voice 'stop recording'.")
    if args.play:
        try:
            player = MacroPlayer(lambda ev, pr: _macro_executor(ev, pr, mouse))
            player.load(args.play)
            print(f"  >> Playing macro '{args.play}'...")
            player.play(speed=1.0)
            print(f"  -> Macro '{args.play}' done.")
        except FileNotFoundError:
            print(f"  !! Macro '{args.play}' not found in {MACRO_DIR}")
            print(f"     Saved: {list_macros()}")

    # State
    center = np.array([screen_w / 2.0, screen_h / 2.0])
    if is_direct:
        direct_tracker.reset(center)
    else:
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

    # Trackpad mode state
    pinch_start_time = 0.0      # When pinch began (for tap vs hold detection)
    pinch_was_active = False    # Was pinch active last frame?
    trackpad_dragging = False   # Currently dragging (pinch held)
    peace_start_time = 0.0      # When peace began (for tap vs scroll detection)
    peace_was_active = False    # Was peace active last frame?
    PINCH_TAP_MAX = 0.25        # Pinch held < 250ms = tap (click)
    PINCH_HOLD_MIN = 0.35       # Pinch held > 350ms = hold (drag)
    PEACE_TAP_MAX = 0.25        # Peace held < 250ms = tap (right click)

    # v5.0 — classic-mode pinch-zoom state (quick pinch = click, hold = zoom)
    classic_pinch_start = 0.0   # when current pinch began
    classic_pinch_held = False  # pinch currently down (classic mode)
    zoom_ticks_total = 0        # session zoom counter (debug/HUD)

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

    # ═══ v5.0 — macro record helper ═══
    def _mrec(event, **params):
        """Record a macro event if a recording is active. Never raises."""
        if recording_macro:
            try:
                macro_rec.record(event, **params)
            except Exception:
                pass

    # ═══ v5.0 — voice command dispatch ═══
    def _dispatch_voice(cmd):
        """Execute a voice command. Returns True if the app should quit."""
        nonlocal cursor_frozen, precision_mode, zoom_enabled, dragging
        nonlocal recording_macro, macro_name
        try:
            if cmd == VoiceCommand.CLICK:
                mouse.left_click(); audio.click(); _mrec("click")
            elif cmd == VoiceCommand.RIGHT_CLICK:
                mouse.right_click(); audio.right_click(); _mrec("right_click")
            elif cmd == VoiceCommand.DOUBLE_CLICK:
                mouse.double_click(); audio.click(); _mrec("double_click")
            elif cmd == VoiceCommand.MIDDLE_CLICK:
                try:
                    mouse.mouse.click(mouse._button.middle, 1)
                except Exception:
                    pass
                audio.right_click(); _mrec("middle_click")
            elif cmd == VoiceCommand.SCROLL_UP:
                mouse.scroll(-3); audio.scroll_tick(); _mrec("scroll", amount=-3)
            elif cmd == VoiceCommand.SCROLL_DOWN:
                mouse.scroll(3); audio.scroll_tick(); _mrec("scroll", amount=3)
            elif cmd == VoiceCommand.ZOOM_IN:
                zoom_scroll(3); audio.click(); _mrec("zoom", ticks=3)
            elif cmd == VoiceCommand.ZOOM_OUT:
                zoom_scroll(-3); audio.click(); _mrec("zoom", ticks=-3)
            elif cmd == VoiceCommand.ZOOM_TOGGLE:
                zoom_enabled = not zoom_enabled
                if pinch_zoom is not None:
                    pinch_zoom.reset()
                audio.mode_enter() if zoom_enabled else audio.mode_exit()
                print(f"  -> Zoom {'ON' if zoom_enabled else 'OFF'}")
            elif cmd == VoiceCommand.DRAG:
                if dragging:
                    mouse.stop_drag(); dragging = False; audio.click()
                    _mrec("drag_stop")
                else:
                    mouse.start_drag(); dragging = True; audio.drag_start()
                    _mrec("drag_start")
            elif cmd == VoiceCommand.FREEZE:
                cursor_frozen = True; audio.freeze()
            elif cmd == VoiceCommand.UNFREEZE:
                cursor_frozen = False; audio.mode_enter()
            elif cmd == VoiceCommand.PRECISION:
                precision_mode = not precision_mode
                audio.precision_toggle()
                if is_direct:
                    direct_tracker.set_precision_mode(precision_mode)
                print(f"  -> Precision {'ON' if precision_mode else 'OFF'}")
            elif cmd == VoiceCommand.CALIBRATE:
                calib.reset()
                if is_direct:
                    direct_tracker.reset(center)
                audio.recalibrate()
                print("  -> Recalibrated (adaptive calibration reset)")
            elif cmd == VoiceCommand.RECORD:
                if not recording_macro:
                    macro_name = f"session_{time.strftime('%H%M%S')}"
                    macro_rec.start(macro_name)
                    recording_macro = True
                    audio.mode_enter()
                    print(f"  -> Recording macro '{macro_name}'...")
            elif cmd == VoiceCommand.STOP_RECORD:
                if recording_macro:
                    macro_rec.stop()
                    macro_rec.save()
                    recording_macro = False
                    audio.mode_exit()
                    print(f"  -> Macro '{macro_name}' saved to {MACRO_DIR}")
            elif cmd == VoiceCommand.PLAY_MACRO:
                names = list_macros()
                if names:
                    player = MacroPlayer(lambda ev, pr: _macro_executor(ev, pr, mouse))
                    player.load(names[-1])
                    player.play_async(speed=1.0)
                    audio.click()
                    print(f"  -> Playing macro '{names[-1]}'...")
            elif cmd == VoiceCommand.VOLUME_UP:
                _safe_kb_action(_kb(), "volume_up")
            elif cmd == VoiceCommand.VOLUME_DOWN:
                _safe_kb_action(_kb(), "volume_down")
            elif cmd == VoiceCommand.MUTE:
                _safe_kb_action(_kb(), "volume_mute")
            elif cmd == VoiceCommand.MEDIA_NEXT:
                _safe_kb_action(_kb(), "media_next")
            elif cmd == VoiceCommand.MEDIA_PREV:
                _safe_kb_action(_kb(), "media_prev")
            elif cmd == VoiceCommand.MEDIA_PLAY:
                _safe_kb_action(_kb(), "media_play_pause")
            elif cmd == VoiceCommand.MINIMIZE:
                _safe_kb_action(_kb(), "minimize_window")
            elif cmd == VoiceCommand.CLOSE_WINDOW:
                _safe_kb_action(_kb(), "close_window")
            elif cmd == VoiceCommand.TASK_SWITCHER:
                _safe_kb_action(_kb(), "switch_window")
            elif cmd == VoiceCommand.SHOW_DESKTOP:
                _safe_kb_action(_kb(), "show_desktop")
            elif cmd == VoiceCommand.SCREENSHOT:
                _safe_kb_action(_kb(), "screenshot")
            elif cmd == VoiceCommand.VOICE_OFF:
                if voice is not None:
                    paused = voice.toggle()
                    audio.mode_exit() if paused else audio.mode_enter()
                    print(f"  -> Voice {'PAUSED' if paused else 'LISTENING'}")
            elif cmd == VoiceCommand.QUIT:
                print("  -> Voice quit requested")
                return True
        except Exception as e:
            print(f"  !! voice dispatch error: {e}")
        return False

    # Banner
    print()
    print("  +==================================================+")
    print(f"  |   AirMouse v{_pkg.__version__} - MULTIMODAL INTELLIGENCE   |")
    print("  |  Eyes + Hands + Voice | Fusion | Intent | Safety | Macros |")
    print("  +==================================================+")
    print(f"  Screen: {screen_w}x{screen_h}  |  Audio: {'ON' if config.audio_enabled else 'OFF'}  |  Gestures: 14+2 swipe")
    print(f"  Tracking: {'DIRECT (1:1 finger-to-screen)' if is_direct else 'IRONMAN (exponential finger-relative)'}")
    print(f"  Filter: {'HYBRID One Euro + Kalman (' + config.kalman_fusion + ')' if (is_direct and config.kalman_enabled) else 'One Euro (classic)'}"
          f"  |  Calibration: {'ADAPTIVE' if config.adaptive_calibration else 'off'}"
          f"  |  Zoom: {'ON' if zoom_enabled else 'off'}")
    if voice is not None:
        print(f"  Voice: ON ({config.voice_sensitivity})"
              + (" [MAD TURBO — nonstop listening]" if config.voice_sensitivity == "turbo" else ""))
    else:
        print("  Voice: off (enable with --voice)")
    if recording_macro:
        print(f"  Macro: RECORDING '{macro_name}'")
    if trackpad_mode:
        print("  Mode: TRACKPAD (tap=click, hold=drag, 2-finger=scroll, 3-finger=show desktop)")
    else:
        print("  Mode: CLASSIC (14 gestures)")
    if precision_mode:
        print(f"  Mode: PRECISION (power={config.precision_power}, scale={config.precision_scale})")
    _show_quick_reference()
    print()

    try:
        while running:
            t0 = time.perf_counter()
            dt = 1.0 / max(fps, 1.0)
            now = time.perf_counter()

            # ═══ v5.0 — VOICE POLL ═══
            # Voice works even when no hand is visible — poll every frame.
            if voice is not None and voice.is_available():
                vcmd = voice.poll()
                while vcmd and vcmd != VoiceCommand.NONE and running:
                    transcript = voice.last_transcript or "?"
                    voice_caption = f"\"{transcript}\" -> {vcmd}"
                    voice_caption_until = now + 2.0
                    if _dispatch_voice(vcmd):
                        running = False
                        break
                    vcmd = voice.poll()
                if now > voice_caption_until:
                    voice_caption = ""

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
                if is_direct:
                    hand_stable = True  # Direct mode doesn't need stability gating
                else:
                    hand_stable = home.is_stable() if home.is_calibrated else False
                # v9: when the multimodal agent owns actions, hand gestures
                # become FUSION CONFIRMATIONS (hand:pinch near gaze target)
                # instead of direct mouse actions — no double-clicking.
                if v9_owns_actions:
                    if config.fusion_mode in ("hands_free", "gaze", "assist"):
                        gesture = Gesture.NONE  # hands-free: hands ignored
                    elif config.gaze_enabled and gesture in (Gesture.PINCH,
                                                             Gesture.PEACE,
                                                             Gesture.PALM,
                                                             Gesture.FIST,
                                                             Gesture.THUMBS_UP,
                                                             Gesture.THREE,
                                                             Gesture.PINKY):
                        gesture = Gesture.NONE  # owned by the action engine
                    # fusion WITHOUT gaze: keep v5 gesture actions as the
                    # functional fallback (confirmed clicks need a gaze target)
                gesture = gsm.update(raw_gesture, now=now, hand_stable=hand_stable)
                gesture_changed = (gesture != prev_gesture)

                # Sound feedback on gesture confirmation
                if gesture_changed and gesture != Gesture.NONE:
                    if prev_gesture == Gesture.NONE or gsm.progress >= 1.0:
                        audio.gesture_confirm()

                raw_pos = gesture_result["index_pos"]

                # ═══ v5.0 — ADAPTIVE CALIBRATION ═══
                # Learns the user's reach box + tremor + speed as they use it,
                # then remaps the raw hand position to fill the full screen.
                if config.adaptive_calibration:
                    raw_pos = calib.update(raw_pos)
                    if (not getattr(calib, "_tuned_applied", False)) and calib.is_ready:
                        params = calib.suggested_filter_params()
                        if is_direct:
                            direct_tracker.tune_filters(**params)
                        calib._tuned_applied = True
                        print(f"  -> Adaptive tune: mincutoff={params['mincutoff']:.2f} beta={params['beta']:.2f}")
                    if calib.samples % 300 == 0:
                        calib.save()

                if is_direct:
                    # ═══ DIRECT TRACKING (v3.2) ═══
                    # 1:1 finger-to-screen — no home, no delta, no drift
                    cursor_pos = direct_tracker.update(raw_pos, dt)
                    if not cursor_frozen:
                        mouse.move_to(cursor_pos[0], cursor_pos[1])
                    speed = np.linalg.norm(direct_tracker.velocity)
                    # Use filtered normalized position for scroll/volume/brightness
                    # This gives smooth, noise-free deltas instead of raw noisy input
                    filtered_pos = direct_tracker.filtered_normalized

                    # Swipe detection (direct mode)
                    swipe_gesture = swipe.update(raw_pos, prev_pos, now)
                    prev_pos = raw_pos.copy()

                else:
                    # ═══ IRONMAN TRACKING (legacy v3.1) ═══
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
                # (works for both direct and ironman modes)

                if trackpad_mode:
                    # ═══ TRACKPAD MODE ═══
                    # Natural trackpad feel — no gesture switching needed.
                    #
                    # PINCH (1-finger tap/hold):
                    #   - Quick tap (< 250ms) = left click
                    #   - Hold (> 350ms) + move = drag
                    #   - Release after hold = drop
                    #
                    # PEACE (2-finger):
                    #   - Quick tap (< 250ms) = right click
                    #   - Hold + move up/down = scroll
                    #
                    # THREE (3-finger): show desktop
                    # FIST: freeze cursor
                    # THUMBS_UP: double click
                    # PINKY: middle click
                    # OK: close window
                    # SIX: task switcher
                    # ROCK: minimize
                    # SHAKA: volume mode
                    # RING: brightness mode

                    # --- PINCH: tap=click, hold=drag ---
                    if gesture == Gesture.PINCH:
                        if not pinch_was_active:
                            # Pinch just started — record time
                            pinch_start_time = now
                            pinch_was_active = True
                        else:
                            # Pinch held — check if we should start dragging
                            pinch_duration = now - pinch_start_time
                            if pinch_duration > PINCH_HOLD_MIN and not trackpad_dragging and not dragging:
                                mouse.start_drag()
                                trackpad_dragging = True
                                dragging = True
                                audio.drag_start()
                    else:
                        # Pinch released
                        if pinch_was_active:
                            pinch_duration = now - pinch_start_time
                            if pinch_duration < PINCH_TAP_MAX:
                                # Quick tap = left click
                                if now - last_click_time > config.pinch_cooldown:
                                    mouse.left_click()
                                    audio.click()
                                    last_click_time = now
                            # End drag if was dragging
                            if trackpad_dragging:
                                mouse.stop_drag()
                                trackpad_dragging = False
                                dragging = False
                            pinch_was_active = False

                    # --- PEACE: tap=right click, hold+move=scroll ---
                    if gesture == Gesture.PEACE:
                        if not peace_was_active:
                            peace_start_time = now
                            peace_was_active = True
                            prev_index_y = filtered_pos[1]
                            scroll_accum = 0.0
                        else:
                            peace_duration = now - peace_start_time
                            if peace_duration > PEACE_TAP_MAX:
                                # Held long enough = scroll mode
                                scrolling = True
                                if prev_index_y is not None:
                                    sd = (filtered_pos[1] - prev_index_y) * 80
                                    scroll_accum += sd
                                    if abs(scroll_accum) > 0.5:
                                        scroll_amount = int(scroll_accum)
                                        if scroll_amount != 0:
                                            mouse.scroll(scroll_amount)
                                            audio.scroll_tick()
                                        scroll_accum = 0.0
                                prev_index_y = filtered_pos[1]
                            else:
                                scrolling = False
                    else:
                        # Peace released
                        if peace_was_active:
                            peace_duration = now - peace_start_time
                            if peace_duration < PEACE_TAP_MAX:
                                # Quick tap = right click
                                if now - last_click_time > config.pinch_cooldown:
                                    mouse.right_click()
                                    audio.right_click()
                                    last_click_time = now
                            peace_was_active = False
                        scrolling = False

                    # --- THREE: show desktop (3-finger swipe up on trackpad) ---
                    if gesture == Gesture.THREE and gesture_changed:
                        _safe_kb_action(_kb(), "show_desktop")
                        audio.click()

                    # --- FIST: freeze cursor ---
                    if gesture == Gesture.FIST and gesture_changed:
                        cursor_frozen = not cursor_frozen
                        audio.freeze()

                    # --- THUMBS_UP: double click ---
                    if gesture == Gesture.THUMBS_UP and gesture_changed:
                        mouse.double_click()
                        audio.click()
                        last_click_time = now

                    # --- PINKY: middle click ---
                    if gesture == Gesture.PINKY and gesture_changed:
                        try:
                            mouse.mouse.click(mouse._button.middle, 1)
                        except Exception:
                            pass
                        audio.right_click()
                        last_click_time = now

                    # --- OK: close window ---
                    if gesture == Gesture.OK and gesture_changed:
                        _safe_kb_action(_kb(), "close_window")
                        audio.click()

                    # --- SIX: task switcher ---
                    if gesture == Gesture.SIX and gesture_changed:
                        _safe_kb_action(_kb(), "switch_window")
                        audio.click()

                    # --- ROCK: minimize ---
                    if gesture == Gesture.ROCK and gesture_changed:
                        _safe_kb_action(_kb(), "minimize_window")
                        audio.click()

                else:
                    # ═══ CLASSIC GESTURE MODE ═══
                    # v5.0: HOLD pinch + move = ZOOM (quick pinch still clicks)
                    if zoom_enabled and pinch_zoom is not None:
                        if gesture == Gesture.PINCH:
                            if not classic_pinch_held:
                                classic_pinch_held = True
                                classic_pinch_start = now
                            zticks = pinch_zoom.update(True, filtered_pos[1], now)
                            if zticks != 0:
                                zoom_scroll(zticks)
                                zoom_ticks_total += zticks
                                _mrec("zoom", ticks=zticks)
                        elif classic_pinch_held:
                            classic_pinch_held = False
                            pinch_zoom.update(False, filtered_pos[1], now)
                            # quick pinch (released before zoom engaged) = click
                            if not pinch_zoom.active \
                               and (now - classic_pinch_start) < config.zoom_engage_hold + 0.1 \
                               and now - last_click_time > config.pinch_cooldown:
                                mouse.left_click()
                                audio.click()
                                last_click_time = now
                                _mrec("click")

                    # PINCH -> Left click (immediate when zoom is off)
                    if gesture == Gesture.PINCH and gesture_changed \
                       and not (zoom_enabled and pinch_zoom is not None):
                        if now - last_click_time > config.pinch_cooldown:
                            mouse.left_click()
                            audio.click()
                            last_click_time = now
                            _mrec("click")

                    # PEACE -> Right click
                    elif gesture == Gesture.PEACE and gesture_changed:
                        if now - last_click_time > config.pinch_cooldown:
                            mouse.right_click()
                            audio.right_click()
                            last_click_time = now
                            _mrec("right_click")

                    # THUMBS_UP -> Double click
                    elif gesture == Gesture.THUMBS_UP and gesture_changed:
                        mouse.double_click()
                        audio.click()
                        last_click_time = now
                        _mrec("double_click")

                    # PINKY -> Middle click
                    elif gesture == Gesture.PINKY and gesture_changed:
                        try:
                            mouse.mouse.click(mouse._button.middle, 1)
                        except Exception:
                            pass
                        audio.right_click()
                        last_click_time = now
                        _mrec("middle_click")

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
                        _mrec("drag_start")
                    elif gesture != Gesture.PALM and dragging and not trackpad_dragging:
                        mouse.stop_drag()
                        dragging = False
                        _mrec("drag_stop")

                    # THREE -> Scroll mode
                    if gesture == Gesture.THREE:
                        scrolling = True
                        if gesture_changed:
                            prev_index_y = filtered_pos[1]
                            scroll_accum = 0.0
                        if prev_index_y is not None:
                            sd = (filtered_pos[1] - prev_index_y) * 80
                            scroll_accum += sd
                            if abs(scroll_accum) > 0.5:
                                scroll_amount = int(scroll_accum)
                                if scroll_amount != 0:
                                    mouse.scroll(scroll_amount)
                                    audio.scroll_tick()
                                    _mrec("scroll", amount=scroll_amount)
                                scroll_accum = 0.0
                            prev_index_y = filtered_pos[1]
                    else:
                        scrolling = False

                    # GUN -> Show desktop (Win+D / Cmd+H)
                    if gesture == Gesture.GUN and gesture_changed:
                        _safe_kb_action(_kb(), "show_desktop")
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
                if is_direct:
                    # Direct mode — cursor already moved above in the direct block
                    # Just handle audio feedback
                    if not cursor_frozen:
                        speed = np.linalg.norm(direct_tracker.velocity)
                        if speed > 500:
                            audio.whoosh(speed)

                elif gesture in (Gesture.POINTING, Gesture.PEACE,
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
                    if not is_direct:
                        spring.update(spring.position, dt)
                        momentum.reset()

                prev_gesture = gesture

            else:
                # No hand detected
                hand_absent_frames += 1

                if is_direct:
                    # Direct mode — just let the spring EMA decay
                    pass  # No momentum, cursor holds position
                else:
                    # Ironman mode — momentum throw keeps cursor gliding
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
                    if is_direct:
                        direct_tracker.reset()
                    else:
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
                    if pinch_zoom is not None:
                        pinch_zoom.reset()
                    classic_pinch_held = False
                    if volume_mode:
                        volume_mode = False
                    if brightness_mode:
                        brightness_mode = False

            # Display
            if config.show_camera and debug_mode and hand_data["frame"] is not None:
                # ═══ v9.0 — MULTIMODAL TICK (gaze + hand + voice + screen) ═══
                # Runs once per frame AFTER gesture recognition so the fusion
                # engine receives the confirmed hand gesture as confirmation.
                if agent is not None:
                    v9_t0 = time.perf_counter()
                    v9_frame = hand_data.get("frame") if isinstance(hand_data, dict) else None
                    v9_utterance = ""
                    if utterance_queue:
                        v9_utterance, _v9ts = utterance_queue.pop(0)
                    v9_hand = None
                    if hand_data.get("hand_found") and gesture is not None:
                        try:
                            v9_hand = {
                                "gesture": getattr(gesture, "name", str(gesture)).lower(),
                                "point": (float(cursor_pos[0]), float(cursor_pos[1]))
                                if cursor_pos is not None else None,
                                "confidence": float(gesture_confidence or 0.0),
                            }
                        except Exception:
                            v9_hand = None
                    try:
                        v9_out = agent.process_frame(
                            frame=v9_frame if config.gaze_enabled else None,
                            hand_data=v9_hand,
                            utterance=v9_utterance,
                            now=v9_t0,
                        )
                        v9_reports = v9_out.get("reports", [])
                        if v9_reports:
                            _lr = v9_reports[-1]
                            v9_summary["action"] = (
                                (f"{_lr.plan.action.value}" if _lr.plan else "?")
                                + (":ok" if _lr.ok else f":{_lr.status.value}"))
                        else:
                            v9_summary["action"] = ""
                        _dec = v9_out.get("decision")
                        if _dec is not None:
                            if _dec.target is not None:
                                try:
                                    v9_summary["target"] = agent.screen.describe_target(_dec.target)
                                except Exception:
                                    v9_summary["target"] = _dec.target.id
                            elif _dec.point is not None:
                                v9_summary["target"] = f"({int(_dec.point[0])},{int(_dec.point[1])})"
                            else:
                                v9_summary["target"] = ""
                        _nin = v9_out.get("intents", [])
                        v9_summary["intent"] = _nin[-1].type.value if _nin else ""
                        v9_summary["mode"] = str(getattr(agent, "mode", "") or "")
                        _gs = v9_out.get("gaze_state")
                        v9_summary["gaze_conf"] = (
                            float(getattr(_gs, "confidence", 0.0) or 0.0)
                            if _gs is not None else None)
                        v9_summary["estop"] = bool(
                            getattr(agent.safety, "level", None) is not None
                            and str(getattr(agent.safety, "level", "")) .endswith("EMERGENCY"))
                        if v9_out.get("estop"):
                            v9_summary["intent"] = "EMERGENCY_STOP"
                    except Exception as _v9e:
                        v9_summary["action"] = f"err:{_v9e}"

                _draw_hud(hand_data["frame"], gesture_result, spring, fps,
                          config, cursor_frozen, dragging, scrolling,
                          volume_mode, brightness_mode, precision_mode,
                          gsm.progress, gesture_confidence,
                          voice_caption=voice_caption,
                          voice_active=(voice is not None and voice.listening),
                          zoom_active=(pinch_zoom.active if pinch_zoom else False),
                          recording=recording_macro,
                          kalman_on=(is_direct and config.kalman_enabled),
                          cal_ready=calib.is_ready,
                          v9_state=v9_summary)
                cv2.imshow("AirMouse", hand_data["frame"])
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    running = False
                elif key == 27:  # ESC — emergency stop in v9, quit otherwise
                    if agent is not None and not str(getattr(agent.safety, "level", "")).endswith("EMERGENCY"):
                        agent.trip_estop("escape_key")
                        print("  >> EMERGENCY STOP (ESC) — [x] to reset")
                    else:
                        running = False
                elif key == ord("d"):
                    debug_mode = not debug_mode
                elif key == ord("r"):
                    if is_direct:
                        direct_tracker.reset(center)
                    else:
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
                    if is_direct:
                        # v4.0: precision mode uses One Euro Filter swap
                        direct_tracker.set_precision_mode(precision_mode)
                    else:
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
                # ═══ v5.0 hotkeys ═══
                elif key == ord("v"):
                    if voice is not None and voice.is_available():
                        paused = voice.toggle()
                        audio.mode_exit() if paused else audio.mode_enter()
                        print(f"  -> Voice {'PAUSED' if paused else 'LISTENING'}")
                    else:
                        print("  -> Voice unavailable (install SpeechRecognition + pyaudio, restart with --voice)")
                elif key == ord("k"):
                    if is_direct:
                        config.kalman_enabled = not config.kalman_enabled
                        direct_tracker.toggle_hybrid(config.kalman_enabled)
                        audio.precision_toggle()
                        print(f"  -> Hybrid Kalman filter {'ON' if config.kalman_enabled else 'OFF'}")
                    else:
                        print("  -> Kalman hybrid applies to DIRECT mode only")
                elif key == ord("z"):
                    zoom_enabled = not zoom_enabled
                    if pinch_zoom is not None:
                        pinch_zoom.reset()
                    audio.mode_enter() if zoom_enabled else audio.mode_exit()
                    print(f"  -> Pinch-to-zoom {'ON' if zoom_enabled else 'OFF'}")
                elif key == ord("m"):
                    if recording_macro:
                        macro_rec.stop()
                        macro_rec.save()
                        recording_macro = False
                        audio.mode_exit()
                        print(f"  -> Macro '{macro_name}' saved to {MACRO_DIR}")
                    else:
                        macro_name = f"session_{time.strftime('%H%M%S')}"
                        macro_rec.start(macro_name)
                        recording_macro = True
                        audio.mode_enter()
                        print(f"  -> Recording macro '{macro_name}' (press [m] again to stop)")
                # ═══ v9.0 hotkeys ═══
                elif key == ord("g"):
                    if agent is not None:
                        config.gaze_enabled = not config.gaze_enabled
                        agent.config.gaze_enabled = config.gaze_enabled
                        audio.mode_enter() if config.gaze_enabled else audio.mode_exit()
                        print(f"  -> Gaze {'ON' if config.gaze_enabled else 'OFF'}")
                    else:
                        print("  -> Gaze requires v9 mode (--gaze / --fusion / --hands-free)")
                elif key == ord("f"):
                    if agent is not None:
                        _cycle = ["hand", "fusion", "gaze", "hands_free", "assist", "voice"]
                        _cur = str(agent.config.mode)
                        _next = _cycle[(_cycle.index(_cur) + 1) % len(_cycle)] \
                            if _cur in _cycle else "fusion"
                        if agent.set_mode(_next):
                            config.fusion_mode = _next
                            print(f"  -> Interaction mode: {_next.upper()}")
                        else:
                            print("  -> Mode switch rate-limited, try again in a moment")
                    else:
                        print("  -> Fusion modes require v9 mode (--fusion / --hands-free)")
                elif key == ord("x"):
                    if agent is not None:
                        _lvl = str(getattr(agent.safety, "level", ""))
                        if _lvl.endswith("EMERGENCY"):
                            agent.reset_estop()
                            print("  -> E-STOP RESET — actions re-enabled")
                            audio.mode_enter()
                        else:
                            agent.trip_estop("keyboard")
                            print("  >> EMERGENCY STOP engaged — press [x] again to reset")
                            audio.freeze()
                    else:
                        print("  -> E-stop requires v9 mode")

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
        # v5.0 cleanup — persist what was learned, stop threads, save macros
        try:
            if recording_macro and macro_rec.recording:
                macro_rec.stop()
                macro_rec.save()
                print(f"  Macro '{macro_name}' saved to {MACRO_DIR}")
        except Exception:
            pass
        try:
            if config.adaptive_calibration:
                calib.save()
        except Exception:
            pass
        try:
            if voice is not None:
                voice.stop()
        except Exception:
            pass
        if dragging:
            try:
                mouse.stop_drag()
            except Exception:
                pass
        # ═══ v9.0 shutdown: agent teardown + performance report ═══
        if agent is not None:
            try:
                if config.telemetry_enabled:
                    t = agent.telemetry.snapshot()
                    print("  ── v9 performance report ──")
                    print(f"     camera {t.fps_camera:.1f} fps | gaze {t.fps_gaze:.1f} fps"
                          f" | gaze latency {t.latency_gaze_ms:.1f} ms"
                          f" | fusion {t.latency_fusion_ms:.1f} ms"
                          f" | action {t.latency_action_ms:.1f} ms")
                    print(f"     actions: {t.actions_total} total,"
                          f" {t.actions_success} ok, {t.actions_failed} failed,"
                          f" {t.actions_blocked} blocked by safety,"
                          f" {t.recoveries} recoveries, {t.estop_count} e-stops")
                agent.shutdown()
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
