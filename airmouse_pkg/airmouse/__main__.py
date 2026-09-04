"""
AirMouse v15.1.0 — ADAPTIVE HUMAN-COMPUTER INTELLIGENCE EDITION (hardened release)

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
import json
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
              v9_state=None, v10_state=None, v115_state=None,
              v15_state=None):
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
    # v10 badges: voice mode, live command + confidence, RF, browser, verify
    if v10_state:
        try:
            if v10_state.get("voice_mode"):
                badges.append((f"V10:{str(v10_state['voice_mode'])[:4].upper()}",
                               (0, 255, 180)))
            if v10_state.get("command"):
                conf = v10_state.get("conf") or ""
                badges.append((f"CMD:{str(v10_state['command'])[:12].upper()}"
                               + (f" {conf}" if conf else ""),
                               (0, 230, 200)))
            if v10_state.get("rf"):
                badges.append(("RF", (180, 100, 255)))
            if v10_state.get("br"):
                badges.append(("BROWSER", (90, 200, 255)))
            if v10_state.get("verify"):
                vcol = (140, 255, 140) if v10_state["verify"] == "passed" \
                    else (255, 170, 80)
                badges.append((f"VER:{str(v10_state['verify'])[:6].upper()}", vcol))
        except Exception:
            pass
    # v11.5 badges: intelligence, interaction mode, suggestions, transcript
    if v115_state:
        try:
            if v115_state.get("intel"):
                badges.append((f"AI:{str(v115_state['intel'])[:6].upper()}",
                               (255, 190, 90)))
            if v115_state.get("mode"):
                badges.append((f"MODE:{str(v115_state['mode'])[:8].upper()}",
                               (255, 150, 220)))
            if v115_state.get("sug"):
                badges.append((f"SUG:{str(v115_state['sug'])[:14].upper()}",
                               (255, 220, 130)))
            if v115_state.get("transcript"):
                badges.append((f"\"{str(v115_state['transcript'])[-18:]}\"",
                               (200, 230, 255)))
        except Exception:
            pass
    # v15 badges: agent control, task, recovery (§33 — the user must
    # ALWAYS know whether an AI agent is controlling the computer)
    if v15_state:
        try:
            if v15_state.get("agent"):
                badges.append((f"AGENT:{str(v15_state['agent'])[:8].upper()}",
                               (255, 120, 120)))
            if v15_state.get("task"):
                badges.append((f"TASK:{str(v15_state['task'])[:12].upper()}",
                               (160, 255, 160)))
            if v15_state.get("confirm"):
                badges.append(("CONFIRM?", (255, 210, 90)))
            if v15_state.get("recovery"):
                badges.append((f"RECOVER:{str(v15_state['recovery'])[:8].upper()}",
                               (255, 160, 90)))
        except Exception:
            pass
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


def _confirm(prompt: str) -> bool:
    """Explicit-y consent gate; fail-closed on any doubt (non-TTY = no)."""
    try:
        if not sys.stdin.isatty():
            return False
        return input(prompt + " [y/N] ").strip().lower() in ("y", "yes")
    except Exception:
        return False


def main():
    # ═══ first-run menu: plain `airmouse` on a fresh machine (§9) ═══
    try:
        from .cli_menu import should_show_menu, run_menu
        if should_show_menu():
            chosen = run_menu(_pkg.__version__.split(".")[0])
            if chosen is None:
                return 0
            if chosen:
                sys.argv = [sys.argv[0]] + chosen
    except Exception:
        pass  # the menu must never block the app

    parser = argparse.ArgumentParser(
        prog="airmouse",
        description=f"AirMouse v{_pkg.__version__} — Adaptive Human-Computer "
                    f"Intelligence Edition",
        epilog="getting started:\n"
               "  airmouse setup              guided setup wizard\n"
               "  airmouse doctor             environment diagnostics\n"
               "  airmouse test --guided      interactive validation lab\n"
               "  airmouse verify             automated checks + remaining physical tests\n"
               "  airmouse privacy            local-first privacy report\n"
               "  airmouse memory status      local memory stores\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
    parser.add_argument("--voice-mode", type=str,
                        choices=["normal", "high", "turbo",
                                 "command", "dictation", "hybrid"],
                        default=None,
                        help="v5 sensitivities (normal/high/turbo) or v10 modes (command/dictation/hybrid)")
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
    # ═══ v10.0 flags — Universal Offline Interaction Engine ═══
    parser.add_argument("--offline", action="store_true",
                        help="v10 TRUE OFFLINE mode: block network features, local ASR/grammar only")
    parser.add_argument("--aip-stdio", action="store_true",
                        help="serve the AIP JSON-lines protocol on stdin/stdout "
                             "(agent-core stdio:// / agent-sdk-js StdioTransport)")
    parser.add_argument("--aip-real", action="store_true",
                        help="with --aip-stdio: route agent EXECUTE through the real "
                             "ActionEngine (permission-gated); default is simulated")
    parser.add_argument("--launch-browser", action="store_true",
                        help="launch Chrome/Chromium/Edge with --remote-debugging-port "
                             "and connect browser control to it")
    parser.add_argument("--browser-port", type=int, default=9222,
                        help="CDP port for --launch-browser (default 9222)")
    parser.add_argument("--browser", action="store_true",
                        help="v10 enable local browser bridge control (semantic page targets)")
    parser.add_argument("--browser-bridge", action="store_true",
                        help="v10 start the localhost browser-bridge server (extension endpoint)")
    parser.add_argument("--gesture", action="store_true",
                        help="v10 enable the gesture registry (custom gesture mappings)")
    parser.add_argument("--rf", action="store_true",
                        help="v10 enable the RF-sensing modality (optional hardware; idles without it)")
    # ── v11.5 flags ──
    parser.add_argument("--intelligence", action="store_true",
                        help="v11.5 enable the adaptive intelligence plugin (local + offline)")
    parser.add_argument("--no-intelligence", action="store_true",
                        help="v11.5 disable the adaptive intelligence plugin")
    parser.add_argument("--dictation", action="store_true",
                        help="v11.5 voice typing / dictation formatting session")
    parser.add_argument("--transcribe", action="store_true",
                        help="v11.5 live transcription session (streaming, local)")
    parser.add_argument("--teacher", action="store_true", help="v11.5 teacher mode")
    parser.add_argument("--student", action="store_true", help="v11.5 student mode")
    parser.add_argument("--office", action="store_true", help="v11.5 office mode")
    parser.add_argument("--meeting", action="store_true", help="v11.5 meeting mode")
    parser.add_argument("--research", action="store_true", help="v11.5 research mode")
    # ═══ v15.1 release flags ═══
    parser.add_argument("--guided", action="store_true",
                        help="test: run the interactive guided laboratory")
    parser.add_argument("--verbose", action="store_true",
                        help="doctor: show per-section detail and fixes")
    parser.add_argument("--json", action="store_true",
                        help="doctor/privacy: machine-readable output")
    parser.add_argument("--to", type=str, default=None,
                        help="memory export: destination file path")
    parser.add_argument("--debug", action="store_true",
                        help="show technical details when something fails")
    parser.add_argument("command", nargs="?", default=None,
                        choices=["voice-status", "gestures", "commands",
                                 "browser", "offline-test", "diagnostics",
                                 "intelligence", "memory", "vocabulary",
                                 "workflows", "self-test",
                                 "status", "capabilities", "observe",
                                 "world", "twin", "skills", "agents",
                                 "permissions", "tasks", "protocol",
                                 "benchmark",
                                 "setup", "doctor", "test", "verify",
                                 "privacy",
                                 "academy", "gesture-lab", "profile"],
                        help="info/diagnostic subcommand (prints and exits)")
    parser.add_argument("command_arg", nargs="?", default=None,
                        help="optional subcommand argument "
                             "(memory: status|export|reset|delete)")
    args = parser.parse_args()

    if args.version:
        print(f"AirMouse v{_pkg.__version__} — Adaptive Human-Computer Intelligence Edition")
        return

    # ═══ v16 agent last mile: AIP wire server (§25) ═══
    # agent-core stdio:// and agent-sdk-js StdioTransport target
    # `airmouse --aip-stdio`. Simulated by default (honestly labeled);
    # --aip-real routes EXECUTE through the real permission-gated
    # ActionEngine. Runs before any camera/mediapipe initialization.
    if args.aip_stdio:
        from . import aip_stdio
        from .actions import ActionEngine
        engine = None
        label = "simulated"
        if args.aip_real:
            try:
                from .actions import PynputExecutor
                engine = ActionEngine(executor=PynputExecutor())
                label = "real"
                print("  >> AIP REAL MODE — agent EXECUTE reaches the OS "
                      "(permission-gated, fail-closed)", flush=True)
            except Exception as _re:
                print(f"  !! real executor unavailable ({_re}) — "
                      "falling back to simulated", flush=True)
                engine, label = None, "simulated"
        endpoint = aip_stdio.default_endpoint(action_engine=engine,
                                              label=label)
        print(f"  >> AIP WIRE SERVER on stdin/stdout ({label} mode; "
              "EXECUTE fail-closed until permissions granted)", flush=True)
        return int(aip_stdio.serve(endpoint=endpoint))

    # ═══ v15.1 release commands (setup / doctor / test / verify / privacy) ═══
    if args.command == "doctor":
        from .capabilities import detect_all
        from .doctor import run_doctor, format_doctor_report
        verbose = bool(args.verbose) or str(args.command_arg or "").strip().lower() in (
            "verbose", "-v", "--verbose")
        if args.json:
            print(json.dumps(detect_all().to_machine(), indent=2))
            return 0
        report = run_doctor(verbose=verbose)
        print(format_doctor_report(report, verbose=verbose))
        verdict = report.overall()
        if "BLOCKED" in verdict:
            return 2
        return 0 if "READY" in verdict else 1

    if args.command == "setup":
        from .setup_wizard import run_setup
        try:
            interactive = sys.stdin.isatty()
        except Exception:
            interactive = False
        report = run_setup(interactive=interactive, out=sys.stdout)
        print()
        print(report.format())
        return 0

    if args.command == "test":
        from .guided_test import run_guided
        guided = bool(args.guided) or str(args.command_arg or "").strip().lower() in (
            "guided", "--guided")
        try:
            interactive = guided and sys.stdin.isatty()
        except Exception:
            interactive = False
        report = run_guided(interactive=interactive, out=sys.stdout)
        failed = [r for r in report.results if r.status.value == "FAIL"]
        return 1 if failed else 0

    if args.command == "verify":
        from .verify import run_verify
        rep = run_verify()
        print(rep.format())
        return 1 if any(i.status == "FAIL" for i in rep.automated) else 0

    if args.command == "privacy":
        from .privacy import privacy_report
        pr = privacy_report()
        if args.json:
            print(json.dumps(pr, indent=2, default=str))
            return 0
        print("AirMouse Privacy — LOCAL-FIRST / OFFLINE BY DEFAULT")
        for key in ("telemetry_state", "network_state", "model_state"):
            print(f"  {key.replace('_', ' '):<10}: {pr.get(key)}")
        storage = pr.get("storage")
        if isinstance(storage, dict):
            print(f"  storage   : {storage.get('home', '(default ~/.airmouse)')}")
            for name, s in (storage.get("stores") or {}).items():
                if isinstance(s, dict):
                    print(f"    {name:<12} exists={s.get('exists')} "
                          f"records={s.get('records', 0)} "
                          f"schema=v{s.get('schema_version')}")
        for key in ("learned_data", "controls"):
            val = pr.get(key)
            print(f"  {key}:")
            if isinstance(val, dict):
                for k2, v2 in val.items():
                    print(f"    {k2}: {v2}")
            elif val:
                print(f"    {val}")
        # v16: the real storage manifest — every artifact AirMouse can
        # persist, with purpose + lifecycle owners (audit P0-3 fix).
        try:
            from .privacy import privacy_manifest
            entries = privacy_manifest()
            print(f"  storage manifest ({len(entries)} artifacts):")
            for e in entries:
                print(f"    - {e.get('name')}: {e.get('purpose')}"
                      f" [{e.get('data_type')}] -> {e.get('location')}"
                      f" (exists={e.get('exists')})")
        except Exception as _me:
            print(f"  (manifest unavailable: {_me})")
        return 0

    # ═══ v16 Gesture Academy — teach MOVE/CLICK/DRAG/... with live feedback ═══
    if args.command == "academy":
        from .academy import run_academy
        return int(run_academy(lesson=str(args.command_arg or "all"),
                               camera=not args.no_cam))

    # ═══ v16 Gesture Lab — live readout: gesture/confidence/target/action ═══
    if args.command == "gesture-lab":
        from .gesture_lab import run_gesture_lab
        return int(run_gesture_lab(camera=not args.no_cam,
                                   seconds=float(args.command_arg) if
                                   (args.command_arg or "").isdigit() else 0.0))

    # ═══ v16 gesture interaction profiles ═══
    if args.command == "profile":
        from .gesture_profiles import apply_profile, list_profiles
        name = str(args.command_arg or "").strip().lower()
        if not name or name in ("list", ""):
            print("  gesture profiles:", ", ".join(list_profiles()))
            print("  apply one:  airmouse profile <name>")
            return 0
        ok, msg = apply_profile(name)
        print(f"  {msg}")
        return 0 if ok else 1

    # ═══ v15.1 memory lifecycle (§10): status | export | reset | delete ═══
    mem_arg = str(args.command_arg or "").strip().lower()
    if args.command == "memory" and mem_arg in ("status", "export", "reset", "delete"):
        from . import persistence
        from . import user_errors as _ue
        if mem_arg == "status":
            st = persistence.memory_status()
            print(f"  local memory stores — {st['home']}")
            for name, s in st["stores"].items():
                if not s.get("exists"):
                    state = "empty"
                elif s.get("checksum_ok", True) and not s.get("corrupted_last_load"):
                    state = "ok"
                else:
                    state = "CORRUPT"
                print(f"    {name:<12} {state:<7} schema=v{s.get('schema_version')} "
                      f"records={s.get('records', 0)}")
            print("  lifecycle: airmouse memory export|reset|delete "
                  "(local-only; nothing leaves this machine)")
            return 0
        if mem_arg == "export":
            dest = args.to
            if not dest:
                dest = os.path.join(
                    persistence.ensure_dirs(), "exports",
                    f"airmouse-memory-{int(time.time())}.json")
            try:
                res = persistence.memory_export(dest, overwrite=bool(args.to is None))
            except Exception as exc:
                raise _ue.AirMouseUserError(
                    title="export your local memory",
                    reason=f"could not write the export file: {exc}",
                    fixes=["choose a different path: airmouse memory export --to <path>",
                           "or remove/rename the existing file first",
                           "check that the folder exists and is writable"]) from exc
            print(f"  exported local memory -> {res.get('path', dest)}")
            print("  (local file only — nothing is sent anywhere)")
            return 0
        if mem_arg == "reset":
            question = ("This BACKS UP then CLEARS all local memory stores "
                        "(twin, vocabulary, skills, workflows, preferences). Continue?")
        else:
            question = ("This PERMANENTLY DELETES local memory store files "
                        "(backups are kept). Continue?")
        if not _confirm(question):
            print("  cancelled — nothing changed")
            return 0
        if mem_arg == "reset":
            res = persistence.memory_reset()
            failed = {n: s for n, s in res.get("stores", {}).items()
                      if not s.get("cleared") or s.get("error")}
            if failed:
                print("  reset INCOMPLETE — some stores could not be cleared:")
                for n, s in sorted(failed.items()):
                    print(f"    - {n}: {s.get('error', 'not cleared')}")
                print("  (check directory permissions; backups are under <home>/backups/)")
                return 1
            _arts = res.get("artifacts", [])
            _done = sum(1 for _a in _arts if _a.get("cleared"))
            print(f"  reset complete — stores: {len(res.get('stores', {}))}, "
                  f"learning artifacts cleared: {_done}/{len(_arts)} "
                  "(intelligence, calibration, gestures, macros, notes)")
            print("  backups saved under <home>/backups/")
            try:
                _v = persistence.deletion_verifies()
                print(f"  verification: {'CLEAN' if _v.get('clean') else 'REMAINS: ' + str(_v.get('remaining', [])[:3])}")
            except Exception:
                pass
        else:
            res = persistence.memory_delete()
            failed = sorted(n for n, s in res.get("stores", {}).items()
                            if not s.get("deleted"))
            if failed:
                print("  delete INCOMPLETE — some store files could not be removed:")
                for n in failed:
                    print(f"    - {n}")
                print("  (check directory permissions; backups are under <home>/backups/)")
                return 1
            _arts = res.get("artifacts", [])
            _done = sum(1 for _a in _arts if _a.get("deleted"))
            print(f"  deleted store files + {_done}/{len(_arts)} learning "
                  "artifacts — backups kept under <home>/backups/")
        return 0

    # ═══ v11.5 subcommands (print + exit) ═══
    if args.command == "intelligence":
        from .intelligence.plugin import IntelligencePlugin
        plug = IntelligencePlugin({"enabled": not args.no_intelligence})
        st = plug.status()
        print("  v11.5 adaptive intelligence")
        for k in ("state", "enabled", "learning_enabled", "privacy_mode"):
            print(f"    {k}: {st.get(k)}")
        m = st.get("model") or {}
        if m:
            print(f"    model: {m.get('size_bytes', 0)/1024:.1f} KB of "
                  f"{m.get('capacity_bytes', 0)/1024/1024:.0f} MB budget "
                  f"| words: {m.get('ngram_words', 0)} | actions: {m.get('action_steps', 0)}")
        print(f"    memory patterns: {st.get('memory_patterns', 0)} | "
              f"vocab terms: {st.get('vocabulary_terms', 0)} | "
              f"workflows: {st.get('workflows', 0)}")
        return
    if args.command == "memory":
        from .intelligence.memory import InteractionMemory
        mem = InteractionMemory.load(os.path.join(
            os.path.expanduser("~"), ".airmouse", "intelligence", "memory.json"))
        rows = mem.top(15)
        print(f"  interaction memory — {mem.size()} pattern(s), "
              f"learning {'active' if mem.learning_active else 'paused'}")
        for r in rows:
            print(f"    {r.pattern[:40]:<40} x{r.frequency:<5} "
                  f"succ {r.success_rate:.0%} corr {r.correction_count}")
        if not rows:
            print("    (empty — patterns appear as you use airmouse)")
        return
    if args.command == "vocabulary":
        from .intelligence.vocabulary import PersonalVocabulary
        v = PersonalVocabulary.load(os.path.join(
            os.path.expanduser("~"), ".airmouse", "intelligence", "vocabulary.json"))
        print(f"  personal vocabulary — {v.size} term(s), "
              f"{v.correction_count} correction(s)")
        for e in v.top(15):
            print(f"    {e.term[:36]:<36} x{e.frequency}")
        for key, e in sorted(v._corrections.items())[:10]:
            print(f"    correction: '{e.raw}' -> '{e.preferred}' (x{e.count})")
        if v.size == 0 and v.correction_count == 0:
            print("    (empty — corrections appear when you fix dictation)")
        return
    if args.command == "workflows":
        from .intelligence.workflows import WorkflowStore
        store = WorkflowStore.load(os.path.join(
            os.path.expanduser("~"), ".airmouse", "intelligence", "workflows.json"))
        rows = store.all()
        print(f"  learned workflows — {len(rows)}")
        for w in rows:
            print(f"    {w.name[:44]:<44} steps={len(w.steps)} "
                  f"succ={w.success_count} fail={w.failure_count} "
                  f"{'[destructive]' if w.destructive else ''}")
        if not rows:
            print("    (none — discovered workflows ask for your approval first)")
        return
    if args.command == "self-test":
        from .selftest import run_self_test, format_self_test
        print(format_self_test(run_self_test(
            intelligence=not args.no_intelligence)))
        return

    # ═══ v15 subcommands (print + exit; all local, all fast) ═══
    if args.command == "status":
        print(f"  AirMouse v{_pkg.__version__} — Universal Human + AI "
              f"Interaction Platform")
        print(f"  protocol: AIP 1.0 (local-first, permission-aware)")
        print(f"  hierarchy: E-STOP > HUMAN OVERRIDE > SAFETY > "
              f"PERMISSION > AGENT > PREDICTION")
        from .licensing import CapabilityLicensing
        lic = CapabilityLicensing().state()
        print(f"  license: {lic['tier']} (local-only, "
              f"free core complete)")
        return
    if args.command == "capabilities":
        from .aip import build_capabilities
        caps = build_capabilities(
            {"voice": False, "hand": False, "gaze": False,
             "keyboard": True, "browser": False, "offline": True},
            {"click": "mouse.click", "type_text": "type.text",
             "open_app": "application.launch",
             "navigate": "browser.navigate",
             "observe": "observe.screen"})
        print("  capabilities (AIP discover):")
        for c in caps:
            av = "+" if c["available"] else "-"
            perm = f" perm={c['permission']}" if c.get("permission") else ""
            print(f"    [{av}] {c['name']} ({c['kind']}){perm}")
        return
    if args.command == "observe":
        from .simulator import Simulator
        snap = Simulator().observe()
        print("  observe (simulated computer — no hardware claimed):")
        for k in sorted(snap):
            print(f"    {k}: {snap[k]}")
        return
    if args.command == "world":
        from .world_model_temporal import TemporalWorldModel
        w = TemporalWorldModel()
        w.observe(human={"mode": "unknown"}, cause="cli")
        print("  temporal world model:", w.explain())
        print("  predict_state:", w.predict_state())
        return
    if args.command == "twin":
        from .intelligence.twin import PersonalInteractionTwin
        t = PersonalInteractionTwin()
        st = t.status()
        print(f"  personal interaction twin (optional, local): "
              f"{st['facts']}/{st['capacity']} facts, "
              f"{st['observations']} observations, "
              f"{st['corrections']} corrections, "
              f"{st['errors']} errors")
        return
    if args.command == "skills":
        from .skills import PersonalSkillLibrary
        lib = PersonalSkillLibrary()
        rows = lib.list_skills()
        print(f"  personal skill library — {len(rows)} skill(s) "
              f"(proposal+approval required; never silent)")
        for r in rows[:10]:
            print(f"    {r['skill_id']} {r['name']} v{r['version']} "
                  f"risk={r['risk']} enabled={r['enabled']}")
        return
    if args.command == "agents":
        from .agents import AgentRegistry
        reg = AgentRegistry()
        rows = reg.discover()
        print(f"  multi-agent registry — {len(rows)} agent(s) "
              f"(leases + conflict resolution + human override)")
        for r in rows:
            print(f"    {r['agent_id']} pri={r['priority']} "
                  f"state={r['state']}")
        return
    if args.command == "permissions":
        from .permissions import PERMISSION_KEYS, AgentPermissionEngine
        p = AgentPermissionEngine()
        print("  agent permission keys (§15 granular set):")
        for k in PERMISSION_KEYS:
            print(f"    {k}")
        print(f"  decisions: allow deny ask allow_once allow_session "
              f"allow_pattern — ASK fails closed")
        return
    if args.command == "tasks":
        from .tasks import TaskEngine
        te = TaskEngine()
        rows = te.list_tasks()
        print(f"  task engine — {len(rows)} task(s); "
              f"destructive steps require human approval")
        return
    if args.command == "protocol":
        from .aip import AIP_VERSION, schemas_document
        doc = schemas_document()
        print(f"  AirMouse Interaction Protocol (AIP) v{AIP_VERSION}")
        print(f"  concepts: DISCOVER OBSERVE TARGET REQUEST AUTHORIZE "
              f"EXECUTE VERIFY RESULT")
        print(f"  schemas: {', '.join(sorted(doc['schemas']))}")
        return
    if args.command == "benchmark":
        import time as _t
        print("  v15 performance spot-check (this machine):")
        t0 = _t.perf_counter()
        from airmouse.intelligence.twin import PersonalInteractionTwin
        twin = PersonalInteractionTwin()
        t1 = _t.perf_counter()
        for i in range(100):
            twin.learn("preference", f"k{i % 20}", "v", confidence=0.6)
        t2 = _t.perf_counter()
        from airmouse.world_model_temporal import TemporalWorldModel
        w = TemporalWorldModel()
        t3 = _t.perf_counter()
        for i in range(100):
            w.observe(computer={"active_application": "sim"},
                      cause="bench")
        t4 = _t.perf_counter()
        from airmouse.tasks import TaskEngine
        te = TaskEngine()
        t5 = _t.perf_counter()
        for i in range(50):
            te.create_task(f"bench {i}")
        t6 = _t.perf_counter()
        print(f"    import+construct twin: {(t1 - t0) * 1000:.2f} ms")
        print(f"    100 twin learns:      {(t2 - t1) * 1000:.2f} ms")
        print(f"    construct world:      {(t3 - t2) * 1000:.2f} ms")
        print(f"    100 world observes:   {(t4 - t3) * 1000:.2f} ms")
        print(f"    construct tasks:      {(t5 - t4) * 1000:.2f} ms")
        print(f"    50 task creations:    {(t6 - t5) * 1000:.2f} ms")
        return

    # ═══ v10.0 subcommands (print + exit) ═══
    if args.command == "commands":
        from .voice_commands import commands_by_namespace
        print("  v10 voice command registry (deterministic grammar, fully offline):")
        for ns, names in commands_by_namespace().items():
            print(f"    [{ns}] " + ", ".join(names))
        return
    if args.command == "gestures":
        from .gesture_registry import GestureRegistry
        reg = GestureRegistry()
        reg.load()
        info = reg.list_gestures()
        print("  v10 built-in gesture mappings:")
        for g, intent in info["builtin"].items():
            print(f"    {g:<14} -> {intent}")
        if info["custom"]:
            print("  custom mappings (~/.airmouse/gestures.json):")
            for name, m in info["custom"].items():
                print(f"    {name}: {' -> '.join(m['pattern'])} => "
                      f"{m['intent']} {m['params']}")
        else:
            print("  no custom mappings (define: see docs §custom gestures)")
        return
    if args.command == "voice-status":
        from .offline_voice import OfflineVoiceEngine, detect_providers
        eng = OfflineVoiceEngine({})
        print(f"  mode: {eng.mode.value}  wake_word_required: {eng.wake_word_required}")
        print(f"  offline ASR providers: {detect_providers()}")
        print("  live engine status requires a running session (HUD / diagnostics)")
        return
    if args.command == "browser":
        from .browser import CDPBrowserBridge
        cdp = CDPBrowserBridge(port=9222)
        print(f"  CDP bridge on :9222 -> available: {cdp.available()}")
        print("  start Chrome/Edge with:  --remote-debugging-port=9222")
        print("  or install the bundled extension (airmouse/browser_extension/)")
        return
    if args.command == "offline-test":
        from .offline import run_offline_selftest
        print("  running the FULL v10 stack with networking disabled ...")
        report = run_offline_selftest()
        for c in report.checks:
            print(f"    [{'PASS' if c['passed'] else 'FAIL'}] {c['name']} {c['detail'][:60]}")
        print(f"  {report.summary()}")
        sys.exit(0 if report.ok else 1)
    if args.command == "diagnostics":
        from .offline import run_offline_selftest
        from .offline_voice import detect_providers
        print("  ══ AirMouse v10 diagnostics ══")
        print(f"  version: {_pkg.__version__}")
        print(f"  offline ASR providers: {detect_providers()}")
        rep = run_offline_selftest()
        print(f"  offline selftest: {rep.summary()}")
        try:
            import importlib
            for mod in ("cv2", "mediapipe", "pynput", "speech_recognition",
                        "vosk", "whisper"):
                try:
                    importlib.import_module(mod)
                    print(f"  optional dep {mod}: OK")
                except Exception:
                    print(f"  optional dep {mod}: not installed")
        except Exception:
            pass
        sys.exit(0 if rep.ok else 1)

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
    # ═══ v10.0 flag overrides ═══
    offline_gate = None
    if args.offline:
        try:
            from .offline import OfflineGate
            offline_gate = OfflineGate.global_gate()
            offline_gate.engage()
            print("  >> V10 TRUE OFFLINE MODE — network-dependent features blocked")
        except Exception as e:
            print(f"  !! offline gate unavailable ({e})")
    v10_voice_mode = (args.voice_mode in ("command", "dictation", "hybrid")) \
        if args.voice_mode else False
    if args.offline and args.voice and not v10_voice_mode:
        # offline voice forces a v10 voice mode (cloud ASR is blocked)
        v10_voice_mode = True
        config.voice_sensitivity = "command"
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
        max_hands=(2 if config.two_hand else 1),
    )

    mouse = MouseController(screen_w=screen_w, screen_h=screen_h)
    audio = AudioFeedback(enabled=config.audio_enabled)

    # ═══ v16 EXECUTION SPINE — one authoritative gate for every gesture
    # action (audit #4/#7/#20): estop > confidence > risk > policy >
    # rate-limit > dispatch. Cursor movement stays continuous (gated by
    # spine.gate_continuous for estop only).
    from .gesture_spine import GestureActionRouter
    spine = GestureActionRouter(
        mouse=mouse,
        kb_getter=lambda: _kb(),
        zoom_fn=zoom_scroll,
        min_confidence={
            "SAFE": config.gesture_min_confidence_safe,
            "CAUTION": config.gesture_min_confidence_caution,
        },
        allow_destructive=bool(config.gesture_allow_destructive),
    )

    # ═══ v16 two-hand interaction (config.two_hand) ═══
    two_hand_engine = None
    if config.two_hand:
        try:
            from .two_hand import TwoHandGestureRecognizer
            two_hand_engine = TwoHandGestureRecognizer()
            print("  >> v16 TWO-HAND TRACKING ONLINE — "
                  "both hands pinched to engage; hold=state, "
                  "zoom/rotate/drag=geometry")
        except Exception as _th:
            two_hand_engine = None
            print(f"  !! two-hand engine unavailable ({_th}) — "
                  "single-hand mode continues")

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
        """Voice engine callback: route transcripts to the v10 offline
        engine when active (deterministic grammar), else the v9 NL tap."""
        try:
            if not transcript:
                return
            if voice_engine10 is not None:
                # v10: the offline voice engine owns grammar resolution;
                # the agent drains its events via poll_events().
                voice_engine10.feed_transcript(
                    transcript, float(score) if score else 0.9)
                if browser_ctrl is not None and browser_ctrl.running:
                    # semantic browser utterances (§12) resolve first
                    try:
                        from .browser import SemanticBrowserResolver
                        resolver = SemanticBrowserResolver(browser_ctrl.mapper)
                        res = resolver.resolve(transcript)
                        if res.matched and res.action:
                            browser_ctrl.execute(res)
                            return
                    except Exception:
                        pass
                return
            if agent is not None and transcript:
                utterance_queue.append((transcript, time.perf_counter()))
        except Exception:
            pass

    # ═══ v10.0 — offline voice engine handle ═══
    # Declared BEFORE the v5 voice block below: the v5 cloud engine is a
    # fallback that must only start when the v10 offline engine will not.
    # (v15.1.0 regression: this read preceded the assignment — UnboundLocalError
    # on every `airmouse --voice` startup; fixed in v15.1.1.)
    voice_engine10 = None
    # ═══ v5.0 — Voice Control ═══
    voice = None
    voice_caption = ""        # last transcript for HUD
    voice_caption_until = 0.0 # show transcript for 2s
    if config.voice_enabled and voice_engine10 is None:
        # v10 offline voice replaces the v5 cloud engine when active
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

    # ═══ v10.0 — Universal Offline Interaction Engine components ═══
    # All optional, all degrade gracefully; the v5/v9 behaviour above is
    # unchanged when these are off.
    event_bus = None
    context_engine = None
    voice_engine10 = None
    gesture_registry = None
    rf_bridge = None
    browser_ctrl = None
    browser_bridge_server = None
    system_executor = None
    file_executor = None
    browser_executor = None
    v10_state = {"voice_mode": "", "command": "", "conf": "",
                 "rf": False, "br": False, "verify": ""}
    # ═══ v11.5 optional adaptive intelligence (guarded) ═══
    intelligence_plugin = None
    transcription_engine = None
    voice_typing_engine = None
    text_controller = None
    modes_controller = None
    v115_state = {"intel": "", "mode": "", "sug": "", "transcript": ""}
    try:
        intelligence_wanted = (not args.no_intelligence)
        if intelligence_wanted:
            from .intelligence.plugin import IntelligencePlugin
            intelligence_plugin = IntelligencePlugin({
                "enabled": True,
                "learning": bool(config.learning_enabled),
                "memory_enabled": bool(config.memory_enabled),
                "privacy_mode": bool(config.privacy_mode),
                "model_capacity_bytes": int(getattr(config,
                    "intelligence_model_capacity", 0) or 0)})
            if intelligence_plugin.state.value == "available":
                print("  >> V11.5 ADAPTIVE INTELLIGENCE ONLINE — local, offline, "
                      "personal (learning "
                      f"{'ON' if config.learning_enabled else 'PAUSED'})")
            else:
                print(f"  >> V11.5 intelligence plugin state: "
                      f"{intelligence_plugin.state.value} — core unaffected")
                if intelligence_plugin.state.value in ("corrupted", "incompatible"):
                    intelligence_plugin = None   # fail safe: run without it
        if args.transcribe:
            from .transcription import LiveTranscriptionEngine, \
                SimulatedStreamingProvider
            transcription_engine = LiveTranscriptionEngine(
                provider=SimulatedStreamingProvider(),
                history_enabled=bool(config.transcription_history))
            transcription_engine.start()
            print("  >> V11.5 LIVE TRANSCRIPTION ENGINE ready (simulated provider "
                  "— install vosk/whisper for real local ASR)")
        if args.dictation:
            from .dictation_text import VoiceTypingEngine
            from .interfaces import VoiceMode as _VM
            voice_typing_engine = VoiceTypingEngine(_VM.HYBRID)
            print("  >> V11.5 VOICE TYPING ready — spoken punctuation + edit "
                  "commands (command/dictation/hybrid)")
        from .text_control import TextController
        text_controller = TextController()
        # v16 §19 decision: fusion2 is REMOVED from the shipped wiring
        # (constructed-but-never-called decorative architecture, audit
        # finding #10). fusion.py is the one authoritative fusion engine;
        # fusion2.py remains an optional library, not advertised.
        for flag, mid in ((args.teacher, "teacher"), (args.student, "student"),
                          (args.office, "office"), (args.meeting, "meeting"),
                          (args.research, "research")):
            if flag:
                from .modes import ModeController
                modes_controller = ModeController(mid)
                v115_state["mode"] = mid
                print(f"  >> V11.5 {mid.upper()} MODE active — "
                      "say 'help' style commands (see TEACHER/STUDENT guides)")
                break
    except Exception as e:
        print(f"  !! v11.5 intelligence partially unavailable ({e}) — continuing")
    try:
        from .eventbus import EventBus
        from .context import ContextEngine
        from .actions import ActionEngine as _AE  # noqa (contract anchor)
        from .system_actions import (SystemActionExecutor, FileActionExecutor)
        event_bus = EventBus(history_size=512)
        context_engine = ContextEngine()
        system_executor = SystemActionExecutor()
        file_executor = FileActionExecutor()
        # gesture registry (custom mappings from ~/.airmouse/gestures.json)
        if args.gesture or args.rf:
            from .gesture_registry import GestureRegistry
            gesture_registry = GestureRegistry()
            loaded = gesture_registry.load()
            if loaded:
                print(f"  >> V10 GESTURE REGISTRY — {loaded} custom mapping(s) loaded")
        # RF modality — only with --rf; idles honestly without hardware
        if args.rf:
            from .rf import RFBridge
            rf_bridge = RFBridge(config={"enabled": True}, bus=event_bus)
            if not rf_bridge.available():
                print("  >> V10 RF modality enabled — no RF hardware/provider "
                      "detected (modality idle; system continues)")
        # offline voice engine (v10 modes)
        if v10_voice_mode or args.offline:
            from .offline_voice import OfflineVoiceEngine
            voice_engine10 = OfflineVoiceEngine(
                {"mode": (args.voice_mode or "command")},
                bus=event_bus, context=context_engine)
            # v10 offline voice replaces the v5 cloud engine when active:
            # exactly one voice owner (v15.1.1 ownership invariant).
            if voice is not None:
                try:
                    voice.stop()
                except Exception:
                    pass
                voice = None
                print("  >> v5 cloud voice stopped — v10 offline voice owns speech")
            # deterministic transcript injection hook for CI (AIRMOUSE_VOICE_TEXT)
            print(f"  >> V10 OFFLINE VOICE ONLINE — mode: "
                  f"{voice_engine10.mode.value.upper()} (deterministic local grammar)")
        # browser subsystem
        if args.launch_browser:
            # v16 browser last mile (audit #16): launch Chrome/Chromium/
            # Edge with --remote-debugging-port, then connect to it.
            from .browser import launch_browser
            _lb = launch_browser(port=int(args.browser_port))
            if _lb.get("ok"):
                print(f"  >> v16 BROWSER LAUNCHED — CDP on 127.0.0.1:{_lb['port']}"
                      f" ({_lb.get('browser', 'chrome')})")
            else:
                print(f"  !! browser launch failed: {_lb.get('error')} — "
                      "start it manually with --remote-debugging-port="
                      f"{args.browser_port}")
        if args.browser or args.browser_bridge or args.launch_browser:
            from .browser import BrowserController
            _bcfg = {"enabled": True, "bridge": "auto",
                     "offline": bool(args.offline)}
            if args.launch_browser:
                _bcfg.update({"bridge": "cdp",
                              "cdp_port": int(args.browser_port)})
            browser_ctrl = BrowserController(
                config=_bcfg,
                context_engine=context_engine, bus=event_bus)
            started = browser_ctrl.start()
            if args.browser_bridge:
                from .browser_bridge import BrowserBridgeServer
                browser_bridge_server = BrowserBridgeServer()
                if browser_bridge_server.start():
                    print(f"  >> V10 BROWSER BRIDGE SERVER — {browser_bridge_server.url} "
                          "(localhost only; load the bundled extension)")
                else:
                    browser_bridge_server = None
            if started:
                print("  >> V10 BROWSER CONTROL ONLINE — "
                      "'click the login button', 'new tab', 'go back' ...")
            else:
                print("  >> V10 browser control unavailable (no bridge) — continuing")
        # browser action executor shim: lets the ACTION ENGINE perform
        # semantic browser ops through the controller (§10/§13)
        if browser_ctrl is not None:
            class _BrowserExecutorShim:
                """Adapts BrowserController to the action-engine contract."""

                def __init__(self, controller):
                    self.controller = controller

                def perform(self, op, params=None):
                    try:
                        from .browser import BrowserResolution
                        res = BrowserResolution(
                            matched=True, action=str(op or "navigate"),
                            element=None, params=dict(params or {}),
                            confidence=0.9, text="")
                        out = self.controller.execute(res)
                        return {"ok": out.get("status") == "executed",
                                **out}
                    except Exception as exc:
                        return {"ok": False, "message": repr(exc)}

            browser_executor = _BrowserExecutorShim(browser_ctrl)
    except Exception as e:
        print(f"  !! v10 components partially unavailable ({e}) — continuing")

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
                # ═══ v10 component injection (all optional) ═══
                "event_bus": event_bus,
                "context_engine": context_engine,
                "voice_engine": voice_engine10,
                "gesture_registry": gesture_registry,
                "rf": rf_bridge,
                "browser": browser_ctrl,
                "system_executor": system_executor,
                "file_executor": file_executor,
                "browser_executor": browser_executor,
                # ═══ v11.5 optional adaptive intelligence ═══
                "intelligence": intelligence_plugin,
                "transcription": transcription_engine,
                "voice_typing": voice_typing_engine,
                "text_controller": text_controller,
                "modes": modes_controller,
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

            # v16 TWO-HAND: the recognizer owns zoom/rotate/drag geometry
            # while both hands are engaged; single-hand actions freeze via
            # the ownership gate below. Zoom maps to real ctrl+wheel ticks.
            two_hand_report = None
            two_hand_active = False
            if two_hand_engine is not None:
                try:
                    two_hand_report = two_hand_engine.update(
                        hand_data.get("hands", []), now)
                    two_hand_active = bool(two_hand_report.get("active"))
                    if two_hand_active:
                        _scale = float(two_hand_report.get("scale") or 1.0)
                        _ticks = int(round((_scale - 1.0) * 40.0))
                        if _ticks and spine.gate_continuous(now):
                            zoom_scroll(_ticks)
                            audio.scroll_tick()
                except Exception:
                    two_hand_report = None
                    two_hand_active = False

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
                gesture = gsm.update(raw_gesture, now=now, hand_stable=hand_stable)
                # v9: when the multimodal agent owns actions, hand gestures
                # become FUSION CONFIRMATIONS (hand:pinch near gaze target)
                # instead of direct mouse actions — no double-control.
                # (v15.1.1 fix: ownership is applied AFTER the state machine
                # update — previously the gate ran before `gesture` existed
                # (UnboundLocalError under --fusion --gaze) and was then
                # silently overwritten, so hands-free modes double-controlled.)
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
                # v16 two-hand ownership: when both hands are engaged the
                # two-hand engine owns interaction — single-hand action
                # gestures freeze so exactly ONE owner acts (no double-fire).
                if two_hand_report is not None and two_hand_report.get("active") \
                        and gesture in (Gesture.PINCH, Gesture.PEACE,
                                        Gesture.PALM, Gesture.FIST,
                                        Gesture.THUMBS_UP, Gesture.THREE,
                                        Gesture.PINKY):
                    gesture = Gesture.NONE  # owned by the two-hand engine
                gesture_changed = (gesture != prev_gesture)

                # v16: drain pinch lifecycle events from the state machine
                # (PINCH_HOLD / PINCH_RELEASE / DOUBLE_PINCH now emitted live)
                for _pev in gsm.poll_pinch_events():
                    if _pev == Gesture.DOUBLE_PINCH and not two_hand_active:
                        if spine.dispatch("double_click",
                                          confidence=gesture_confidence,
                                          now=now)["executed"]:
                            audio.click()
                            last_click_time = now
                            _mrec("double_click")

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

                # v16: swipes route through the execution spine (CAUTION
                # class: reversible navigation, estop + rate-limit gated).
                if swipe_gesture == Gesture.SWIPE_LEFT:
                    if spine.dispatch("browser_back", confidence=1.0, now=now)["executed"]:
                        audio.click()
                elif swipe_gesture == Gesture.SWIPE_RIGHT:
                    if spine.dispatch("browser_forward", confidence=1.0, now=now)["executed"]:
                        audio.click()
                elif swipe_gesture == Gesture.SWIPE_UP:
                    # v16 motion gestures: vertical swipe = discrete scroll
                    if spine.gate_continuous(now):
                        mouse.scroll(10)
                        audio.scroll_tick()
                elif swipe_gesture == Gesture.SWIPE_DOWN:
                    if spine.gate_continuous(now):
                        mouse.scroll(-10)
                        audio.scroll_tick()

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
                                if spine.dispatch("start_drag", confidence=gesture_confidence, now=now)["executed"]:
                                    trackpad_dragging = True
                                    dragging = True
                                    audio.drag_start()
                    else:
                        # Pinch released
                        if pinch_was_active:
                            pinch_duration = now - pinch_start_time
                            if pinch_duration < PINCH_TAP_MAX:
                                # Quick tap = left click (v16: via spine)
                                if now - last_click_time > config.pinch_cooldown:
                                    if spine.dispatch("left_click", confidence=gesture_confidence, now=now)["executed"]:
                                        audio.click()
                                        last_click_time = now
                            # End drag if was dragging
                            if trackpad_dragging:
                                if spine.dispatch("stop_drag", confidence=1.0, now=now)["executed"]:
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
                                    if spine.dispatch("right_click", confidence=gesture_confidence, now=now)["executed"]:
                                        audio.right_click()
                                        last_click_time = now
                            peace_was_active = False
                        scrolling = False

                    # --- THREE: show desktop (3-finger swipe up on trackpad) ---
                    if gesture == Gesture.THREE and gesture_changed:
                        if spine.dispatch("show_desktop", confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()

                    # --- FIST: freeze cursor ---
                    if gesture == Gesture.FIST and gesture_changed:
                        cursor_frozen = not cursor_frozen
                        audio.freeze()

                    # --- THUMBS_UP: double click ---
                    if gesture == Gesture.THUMBS_UP and gesture_changed:
                        if spine.dispatch("double_click", confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()
                            last_click_time = now

                    # --- PINKY: middle click ---
                    if gesture == Gesture.PINKY and gesture_changed:
                        if spine.dispatch("middle_click", confidence=gesture_confidence, now=now)["executed"]:
                            audio.right_click()
                            last_click_time = now

                    # --- OK: close window (DESTRUCTIVE class — refused by
                    # default; policy gate in the spine, audit #4/#20) ---
                    if gesture == Gesture.OK and gesture_changed:
                        if spine.dispatch("close_window", confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()
                        else:
                            audio.mode_exit()  # audible "blocked" cue

                    # --- SIX: task switcher (CAUTION) ---
                    if gesture == Gesture.SIX and gesture_changed:
                        if spine.dispatch("task_switch", confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()

                    # --- ROCK: minimize (CAUTION) ---
                    if gesture == Gesture.ROCK and gesture_changed:
                        if spine.dispatch("minimize_window", confidence=gesture_confidence, now=now)["executed"]:
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
                            if zticks != 0 and spine.gate_continuous(now):
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
                                if spine.dispatch("left_click", confidence=gesture_confidence, now=now)["executed"]:
                                    audio.click()
                                    last_click_time = now
                                    _mrec("click")

                    # PINCH -> Left click (immediate when zoom is off)
                    if gesture == Gesture.PINCH and gesture_changed \
                       and not (zoom_enabled and pinch_zoom is not None):
                        if now - last_click_time > config.pinch_cooldown:
                            if spine.dispatch("left_click", confidence=gesture_confidence, now=now)["executed"]:
                                audio.click()
                                last_click_time = now
                                _mrec("click")

                    # PEACE -> Right click
                    elif gesture == Gesture.PEACE and gesture_changed:
                        if now - last_click_time > config.pinch_cooldown:
                            if spine.dispatch("right_click", confidence=gesture_confidence, now=now)["executed"]:
                                audio.right_click()
                                last_click_time = now
                                _mrec("right_click")

                    # THUMBS_UP -> Double click
                    elif gesture == Gesture.THUMBS_UP and gesture_changed:
                        if spine.dispatch("double_click", confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()
                            last_click_time = now
                            _mrec("double_click")

                    # PINKY -> Middle click
                    elif gesture == Gesture.PINKY and gesture_changed:
                        if spine.dispatch("middle_click", confidence=gesture_confidence, now=now)["executed"]:
                            audio.right_click()
                            last_click_time = now
                            _mrec("middle_click")

                    # FIST -> Toggle freeze
                    elif gesture == Gesture.FIST and gesture_changed:
                        cursor_frozen = not cursor_frozen
                        audio.freeze()

                    # OK -> Close window (Alt+F4) — DESTRUCTIVE class:
                    # refused by default (spine policy gate, audit #4/#20)
                    elif gesture == Gesture.OK and gesture_changed:
                        if spine.dispatch("close_window", confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()
                        else:
                            audio.mode_exit()  # audible "blocked" cue

                    # SIX -> Task switcher (Alt+Tab, CAUTION)
                    elif gesture == Gesture.SIX and gesture_changed:
                        if spine.dispatch("task_switch", confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()

                    # PALM -> Drag mode
                    if gesture == Gesture.PALM and not dragging:
                        if spine.dispatch("start_drag", confidence=gesture_confidence, now=now)["executed"]:
                            dragging = True
                            audio.drag_start()
                            _mrec("drag_start")
                    elif gesture != Gesture.PALM and dragging and not trackpad_dragging:
                        if spine.dispatch("stop_drag", confidence=1.0, now=now)["executed"]:
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
                            if abs(scroll_accum) > 0.5 and spine.gate_continuous(now):
                                scroll_amount = int(scroll_accum)
                                if scroll_amount != 0:
                                    mouse.scroll(scroll_amount)
                                    audio.scroll_tick()
                                    _mrec("scroll", amount=scroll_amount)
                                scroll_accum = 0.0
                            prev_index_y = filtered_pos[1]
                    else:
                        scrolling = False

                    # GUN -> Show desktop (Win+D / Cmd+H, CAUTION)
                    if gesture == Gesture.GUN and gesture_changed:
                        if spine.dispatch("show_desktop", confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()

                    # ROCK -> Minimize (CAUTION)
                    if gesture == Gesture.ROCK and gesture_changed:
                        if spine.dispatch("minimize_window", confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()

                # ═══ v16 MOTION-GESTURE ACTIONS (both modes) ═══
                # Every new gesture: detector → confidence → spine gate →
                # action. Circles/push/pull = zoom (SAFE, analog); SHAKE =
                # safe cancellation; WAVE = attention cue only (no OS action).
                if not two_hand_active:
                    if gesture == Gesture.CIRCLE_CW and gesture_changed:
                        if spine.dispatch("zoom", amount=2, confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()
                    elif gesture == Gesture.CIRCLE_CCW and gesture_changed:
                        if spine.dispatch("zoom", amount=-2, confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()
                    elif gesture == Gesture.PULL and gesture_changed:
                        if spine.dispatch("zoom", amount=3, confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()
                    elif gesture == Gesture.PUSH and gesture_changed:
                        if spine.dispatch("zoom", amount=-3, confidence=gesture_confidence, now=now)["executed"]:
                            audio.click()
                    if gesture == Gesture.SHAKE and gesture_changed:
                        # SAFE cancellation: drop drag, unfreeze, exit modes
                        if dragging:
                            spine.dispatch("stop_drag", confidence=1.0, now=now)
                            dragging = False
                            trackpad_dragging = False
                        cursor_frozen = False
                        audio.mode_exit()
                    if gesture == Gesture.WAVE and gesture_changed:
                        audio.gesture_confirm()  # attention cue; no OS action
                    # THUMBS_DOWN / FOUR / FIVE: recognized + HUD-visible;
                    # no risky default action — map them yourself via
                    # airmouse gestures (registry) if wanted.

                # v16: feed confirmed hand gestures to the custom-sequence
                # registry (audit #9: the matcher finally receives hands).
                if gesture_registry is not None and config.gesture_sequences \
                        and gesture_changed and gesture != Gesture.NONE \
                        and not two_hand_active:
                    try:
                        _ev, _rint = gesture_registry.feed(
                            getattr(gesture, "value", str(gesture)),
                            confidence=float(gesture_confidence or 0.8),
                            now=now)
                        if _rint is not None:
                            _itype = str(getattr(getattr(_rint, "type", None),
                                                 "value", "")).lower()
                            _spine_map = {"click": "left_click",
                                          "double_click": "double_click",
                                          "right_click": "right_click",
                                          "middle_click": "middle_click"}
                            _si = _spine_map.get(_itype)
                            if _si:
                                spine.dispatch(_si, confidence=float(
                                    getattr(_rint, "confidence", 0.8) or 0.8),
                                    now=now)
                    except Exception:
                        pass

                # v16: CLOSE THE PERSONALIZATION LOOP (audit #7) — observe
                # every confirmed gesture (learning flags gate inside the
                # plugin; prediction never executes).
                if intelligence_plugin is not None and gesture_changed \
                        and gesture != Gesture.NONE:
                    try:
                        intelligence_plugin.observe_gesture(
                            getattr(gesture, "value", str(gesture)))
                    except Exception:
                        pass

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
                            _vi = "volume_down" if vd > 0 else "volume_up"
                            if spine.dispatch(_vi, confidence=gesture_confidence, now=now)["executed"]:
                                pass  # dispatch already applied the change
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
                            _bi = "brightness_down" if bd > 0 else "brightness_up"
                            if spine.dispatch(_bi, confidence=gesture_confidence, now=now)["executed"]:
                                pass
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
                            # v10 HUD: verification state of the last report
                            v10_state["verify"] = (
                                getattr(getattr(_lr, "verification", None),
                                        "value", "") or "")
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
                        # v10 per-frame HUD state
                        v10_state["voice_mode"] = \
                            voice_engine10.mode.value if voice_engine10 else ""
                        v10_state["command"] = \
                            voice_engine10.last_command if voice_engine10 else ""
                        v10_state["conf"] = (
                            f"{int(voice_engine10.last_confidence * 100)}%"
                            if voice_engine10 and
                            voice_engine10.last_confidence > 0 else "")
                        v10_state["rf"] = bool(
                            rf_bridge is not None and rf_bridge.available())
                        v10_state["br"] = bool(
                            browser_ctrl is not None and browser_ctrl.running)
                        _gs = v9_out.get("gaze_state")
                        v9_summary["gaze_conf"] = (
                            float(getattr(_gs, "confidence", 0.0) or 0.0)
                            if _gs is not None else None)
                        v9_summary["estop"] = bool(
                            getattr(agent.safety, "level", None) is not None
                            and str(getattr(agent.safety, "level", "")) .endswith("EMERGENCY"))
                        # v11.5 per-frame HUD state (guarded)
                        try:
                            if intelligence_plugin is not None \
                                    and intelligence_plugin.available:
                                v115_state["intel"] = "ON"
                                _sug = intelligence_plugin.suggestions(
                                    getattr(agent, "_learned_actions", []))
                                v115_state["sug"] = \
                                    _sug[0].text if _sug else ""
                            else:
                                v115_state["intel"] = \
                                    (intelligence_plugin.state.value[:4].upper()
                                     if intelligence_plugin is not None else "OFF")
                            if voice_typing_engine is not None:
                                v115_state["transcript"] = \
                                    voice_typing_engine.text[-30:]
                            elif transcription_engine is not None:
                                v115_state["transcript"] = \
                                    transcription_engine._partial[-30:]
                        except Exception:
                            pass
                        if v9_out.get("estop"):
                            v9_summary["intent"] = "EMERGENCY_STOP"
                    except Exception as _v9e:
                        v9_summary["action"] = f"err:{_v9e}"

                _draw_hud(hand_data["frame"], gesture_result, spring, fps,
                          config, cursor_frozen, dragging, scrolling,
                          volume_mode, brightness_mode, precision_mode,
                          gsm.progress, gesture_confidence,
                          voice_caption=voice_caption,
                          voice_active=(voice is not None and voice.listening) or (voice_engine10 is not None),
                          zoom_active=(pinch_zoom.active if pinch_zoom else False),
                          recording=recording_macro,
                          kalman_on=(is_direct and config.kalman_enabled),
                          cal_ready=calib.is_ready,
                          v9_state=v9_summary,
                          v10_state=v10_state,
                          v115_state=v115_state,
                          v15_state=None)  # agent/task/recovery badges
                          # populated by the agent runtime at runtime
                cv2.imshow("AirMouse", hand_data["frame"])
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    running = False
                elif key == 27:  # ESC — emergency stop (v9 agent + v16 spine)
                    spine.trip_estop("escape_key")
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
                    if spine.reset_estop():
                        print("  -> spine E-STOP reset — actions re-enabled")
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
                if getattr(config, "perf_report_enabled", True):
                    # §21: this is a LOCAL report; network telemetry
                    # (telemetry_enabled) is a separate, OFF-by-default flag.
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
    from .user_errors import run_cli_guarded
    raise SystemExit(run_cli_guarded(main))
