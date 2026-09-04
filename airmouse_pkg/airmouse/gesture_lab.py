"""Gesture Lab (v16, mission §21) — the live gesture observatory.

An honest, real-time readout of what the interaction pipeline SEES and
what the execution spine WOULD DO — without ever dispatching a real
action.  It is an observatory, not a controller:

* hand detected yes/no
* recognised gesture + per-frame confidence
* active interaction MODE (classic / trackpad / two-hand) from a
  lightweight config load
* two-hand engine state (when ``config.two_hand`` is on)
* LAST ACTION the spine was asked to perform
* RESULT — the spine's own gate verdict (executed / low_confidence /
  rate_limit / destructive_action_blocked_by_policy ...).  This is the
  teaching moment: you watch the safety machinery refuse things live.

HOW IT OBSERVES WITHOUT ACTING
------------------------------
A real ``GestureActionRouter`` (airmouse.gesture_spine) is constructed
with a **dry-run mouse stub** and a dry-run keyboard getter — objects
with the exact method surface of ``MouseController`` /
``KeyboardActions`` (``left_click``, ``scroll``, ...) that only count
calls.  Every gate rule (e-stop, confidence floors, risk class,
destructive policy, rate limits) runs for real inside the router, but
the "executor" behind it records instead of acting.  The lab also
forces ``allow_destructive=False`` regardless of config, so the
close-window (OK-gesture) refusal can always be demonstrated.

Headless (no camera / ``camera=False``): prints what the lab is and the
exact fields it would render, and exits 0 — never a crash, never a
manufactured reading.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import time

from .gestures import Gesture

__all__ = ["run_gesture_lab", "lab_render", "DRY_RUN_GESTURE_INTENTS"]

#: throttle for the console readout (frames of text stay readable)
_PRINT_INTERVAL_S = 0.2                     # ~5 Hz

#: pose -> spine intent, mirroring the REAL dispatch table in
#: __main__.py's v16 loop (same intents, same risk classes).  Only
#: intents the live loop actually dispatches are listed.
DRY_RUN_GESTURE_INTENTS: dict = {
    Gesture.PINCH: "left_click",
    Gesture.PEACE: "right_click",
    Gesture.THUMBS_UP: "double_click",
    Gesture.PINKY: "middle_click",
    Gesture.THREE: "show_desktop",
    Gesture.SIX: "task_switch",
    Gesture.ROCK: "minimize_window",
    Gesture.OK: "close_window",             # DESTRUCTIVE — always refused
}


# ---------------------------------------------------------------------------
# pure formatting core (unit-testable headless)
# ---------------------------------------------------------------------------

def lab_render(snapshot: dict) -> str:
    """Format one lab snapshot as the canonical console readout block.

    Pure: no clock, no I/O, same input -> same string.  Missing keys
    render as honest placeholders rather than crashing.
    """
    s = snapshot if isinstance(snapshot, dict) else {}
    hand = bool(s.get("hand"))
    gesture = str(s.get("gesture") or "none")
    try:
        conf = float(s.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    mode = str(s.get("mode") or "classic")
    two_hand = str(s.get("two_hand") or "off")
    last_action = str(s.get("last_action") or "none yet")
    result = str(s.get("result") or "no action attempted yet")
    return "\n".join([
        f"HAND DETECTED : {'yes' if hand else 'NO'}",
        f"GESTURE       : {gesture}",
        f"CONFIDENCE    : {conf * 100:.0f}%",
        f"MODE          : {mode}",
        f"TWO-HAND      : {two_hand}",
        f"LAST ACTION   : {last_action}",
        f"RESULT        : {result}",
    ])


# ---------------------------------------------------------------------------
# dry-run hardware stubs — the reason the lab can never act
# ---------------------------------------------------------------------------

class _DryPynputButton:
    """Just the attribute the spine's middle-click path reads."""

    middle = 3


class _DryPynputController:
    """pynput-controller-shaped stub: counts clicks, presses nothing."""

    def __init__(self, calls: list) -> None:
        self._calls = calls

    def click(self, button, count=1):
        self._calls.append(("pynput_click", int(button), int(count)))


class _DryRunMouse:
    """MouseController-shaped stub: counts calls, moves nothing.

    Method surface matches everything the spine's ``_execute`` calls
    on ``mouse`` (left/right/double/middle click, drags, scroll,
    including the ``mouse.mouse`` pynput-style inner controller)."""

    def __init__(self) -> None:
        self.calls: list = []
        self._button = _DryPynputButton()
        self.mouse = _DryPynputController(self.calls)

    def left_click(self):
        self.calls.append("left_click")

    def right_click(self):
        self.calls.append("right_click")

    def double_click(self):
        self.calls.append("double_click")

    def middle_click(self):
        self.calls.append("middle_click")

    def start_drag(self):
        self.calls.append("start_drag")

    def stop_drag(self):
        self.calls.append("stop_drag")

    def scroll(self, amount):
        self.calls.append(("scroll", int(amount)))


class _DryRunKeyboard:
    """KeyboardActions-shaped stub: counts calls, presses nothing."""

    def __init__(self) -> None:
        self.calls: list = []

    def __getattr__(self, name):            # any kb intent the spine uses
        def _record():
            self.calls.append(name)
        return _record


def _mode_label(cfg) -> str:
    """classic / trackpad / two-hand from the lightweight config load."""
    if bool(getattr(cfg, "two_hand", False)):
        return "two-hand"
    if bool(getattr(cfg, "trackpad_mode", False)):
        return "trackpad"
    return "classic"


# ---------------------------------------------------------------------------
# the observatory
# ---------------------------------------------------------------------------

def run_gesture_lab(camera: bool = True, seconds: float = 0.0) -> int:
    """Run the Gesture Lab.  Returns a process exit code (always 0 when
    the lab itself started; the lab is a readout, it does not fail on
    missing hardware — it explains honestly instead)."""
    if not camera:
        print()
        print("  +==================================================+")
        print("  |                AIRMOUSE GESTURE LAB               |")
        print("  +==================================================+")
        print()
        print("  The lab is a LIVE observatory: it watches the same")
        print("  recognition + execution-spine pipeline the real cursor")
        print("  uses, but dispatches into a dry-run stub — it can show")
        print("  you the safety gates, never move your mouse.")
        print()
        print("  WITHOUT A CAMERA there is nothing to observe, so here is")
        print("  exactly what a live run prints ~5x per second:")
        print()
        print(lab_render({
            "hand": True, "gesture": "pinch", "confidence": 0.86,
            "mode": "classic", "two_hand": "off",
            "last_action": "left_click (executed)",
            "result": "executed",
        }))
        print()
        print("  ...and when the spine refuses something (teaching!):")
        print()
        print(lab_render({
            "hand": True, "gesture": "ok", "confidence": 0.91,
            "mode": "classic", "two_hand": "off",
            "last_action": "close_window (attempted)",
            "result": ("blocked: destructive_action_blocked_by_policy "
                       "(a gesture must never close windows)"),
        }))
        print()
        print("  Run it live:  airmouse gesture-lab [seconds]")
        print("  (Ctrl-C or a [seconds] bound ends the session)")
        print()
        return 0

    # ── camera path ──────────────────────────────────────────────────────
    tracker = None
    try:
        from .tracker import HandTracker
        from .gesture_spine import GestureActionRouter
        from .config import Config
        from .persistence import config_path_scope

        # lightweight config load (same scope pattern as privacy.py)
        with config_path_scope():
            cfg = Config()
            cfg.load()
        mode = _mode_label(cfg)

        dry_mouse = _DryRunMouse()
        dry_kb = _DryRunKeyboard()
        spine = GestureActionRouter(
            mouse=dry_mouse,                    # dry-run: records, never acts
            kb_getter=lambda: dry_kb,
            zoom_fn=lambda ticks: dry_mouse.calls.append(
                ("zoom", int(ticks))),
            min_confidence={
                "SAFE": float(cfg.gesture_min_confidence_safe),
                "CAUTION": float(cfg.gesture_min_confidence_caution),
            },
            allow_destructive=False,            # the lab ALWAYS demonstrates
            #                              the destructive refusal
        )

        two_hand_engine = None
        if cfg.two_hand:
            try:
                from .two_hand import TwoHandGestureRecognizer
                two_hand_engine = TwoHandGestureRecognizer()
            except Exception:
                two_hand_engine = None          # honest degrade: single-hand

        tracker = HandTracker(
            camera_index=cfg.camera_index,
            detection_confidence=cfg.detection_confidence,
            tracking_confidence=cfg.tracking_confidence,
            max_hands=(2 if cfg.two_hand else 1),
        )
    except Exception as exc:
        print(f"  Gesture Lab cannot start: {exc}")
        print("  (OpenCV/MediaPipe missing or no camera attached — "
              "run `airmouse gesture-lab` with a camera for the live "
              "readout)")
        print("  The headless explanation above the fold is what a live "
              "session would print.")
        if tracker is not None:
            try:
                tracker.release()
            except Exception:
                pass
        return 0

    print()
    print("  AIRMOUSE GESTURE LAB — live observatory (dry-run spine: "
          "nothing real can fire).  Ctrl-C to stop.")
    print(f"  mode={mode}   safe_floor={spine.min_confidence['SAFE']} "
          f"caution_floor={spine.min_confidence['CAUTION']}   "
          "try: pinch, peace, thumbs up, OK (blocked!) ...")
    print()

    deadline = (time.monotonic() + max(0.0, float(seconds))
                if seconds and seconds > 0 else None)
    last_print = 0.0
    last_pose = Gesture.NONE
    snapshot = {"hand": False, "gesture": "none", "confidence": 0.0,
                "mode": mode, "two_hand": "off",
                "last_action": "none yet",
                "result": "no action attempted yet"}

    try:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            data = tracker.read()
            now = time.monotonic()

            hand = bool(data.get("hand_found"))
            gesture, conf = Gesture.NONE, 0.0
            if hand and data.get("landmarks") is not None:
                from .gestures import recognize_gesture
                res = recognize_gesture(data["landmarks"])
                gesture = res["gesture"]
                try:
                    conf = float(res.get("confidence", 0.0))
                except (TypeError, ValueError):
                    conf = 0.0

            # two-hand state (engine available only when config asks)
            two_state = ("on" if two_hand_engine is not None else "off")
            if two_hand_engine is not None:
                th = two_hand_engine.update(data.get("hands"), now)
                hands_n = len(data.get("hands") or [])
                if th.get("active"):
                    two_state = f"ENGAGED: {th.get('gesture')}"
                elif th.get("gesture"):
                    two_state = f"counting ({th.get('gesture')})"
                else:
                    two_state = f"idle ({hands_n} hand[s] seen)"

            # discrete-action demo: pose CHANGED into a mapped pose
            if gesture != last_pose:
                intent = DRY_RUN_GESTURE_INTENTS.get(gesture)
                if intent is not None:
                    outcome = spine.dispatch(intent, confidence=conf)
                    if outcome["executed"]:
                        snapshot["last_action"] = (
                            f"{intent} (executed into dry-run stub)")
                        snapshot["result"] = "executed"
                    else:
                        snapshot["last_action"] = f"{intent} (attempted)"
                        snapshot["result"] = f"blocked: {outcome['reason']}"
                elif gesture in (Gesture.FIST,):
                    snapshot["last_action"] = "freeze cursor (continuous)"
                    snapshot["result"] = ("continuous control — gated by "
                                          "estop only")
            last_pose = gesture

            snapshot.update({
                "hand": hand, "gesture": str(gesture),
                "confidence": conf, "mode": mode, "two_hand": two_state,
            })

            if now - last_print >= _PRINT_INTERVAL_S:
                print(lab_render(snapshot))
                print("  " + "-" * 46)
                last_print = now
    except KeyboardInterrupt:
        pass                                        # Ctrl-C is a clean exit
    finally:
        try:
            tracker.release()
        except Exception:
            pass

    print()
    print(f"  session ended — dry-run stub received "
          f"{len(dry_mouse.calls)} call(s) "
          f"(none reached real hardware); spine history: "
          f"{len(spine.history(64))} executed action(s)")
    return 0
