"""Gesture Academy (v16, mission §20) — a teaching experience, not a checker.

The v15.1 audit (§9) found that ``airmouse setup`` validates the
environment but never TEACHES.  The Academy is the missing classroom:

* **Core track** — 7 lessons practicable LIVE at the desk with a camera:
  move, click, double_click, right_click, drag, scroll, zoom.  Each one
  shows the instruction, mirrors your detected gesture + confidence back
  and requires HOLDING the target gesture for the lesson's
  ``success_criteria`` seconds before it passes (hold-to-pass, exactly
  like ``tutorial.py``).  A physical lesson is NEVER auto-passed.
* **Advanced track** — 4 lessons that teach what is REAL today and point
  to the command that actually exercises it (``--gaze-calibrate``,
  ``airmouse test --guided`` for voice, ``profile hands_free`` +
  ``config two_hand`` for two-hand, the gesture registry for sequences).
  These are marked hardware/config-required honestly.

``academy_plan()`` is the pure, deterministic curriculum data (fully
unit-testable headless); ``run_academy()`` renders it — headless it
prints the plan and exits 0; with a camera it runs the live loop and
persists per-lesson progress to ``<airmouse_home>/academy_progress.json``
(atomically, via ``persistence.atomic_write_json``).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import os
import time

from .gestures import Gesture
from . import paths

__all__ = ["LESSONS", "academy_plan", "run_academy",
           "progress_path", "load_progress", "save_progress"]

# status vocabulary mirrors guided_test.py's honesty contract
STATUS_PHYSICAL = "PHYSICAL PRACTICE REQUIRED — needs camera + hand"


# ---------------------------------------------------------------------------
# The curriculum (ordered)
# ---------------------------------------------------------------------------

LESSONS: list = [
    # ── CORE — live-practicable with a camera ────────────────────────────
    {
        "id": "move",
        "title": "Move the cursor",
        "track": "core",
        "gesture": Gesture.POINTING,          # "pointing"
        "instruction": "Point your index finger at the camera and sweep "
                       "it slowly left and right — the cursor follows "
                       "your fingertip.",
        "success_criteria": 2.0,              # seconds to hold POINTING
        "tips": [
            "Keep your palm facing the camera; only the index finger up.",
            "Sit about 50-80 cm from the camera with even lighting.",
            "Small, relaxed wrist movements work better than big arm swings.",
        ],
        "requires": "camera + hand",
        "next_step": None,
    },
    {
        "id": "click",
        "title": "Left click (pinch)",
        "track": "core",
        "gesture": Gesture.PINCH,             # "pinch"
        "instruction": "Touch your thumb tip to your index fingertip — "
                       "a quick pinch is a left click wherever the "
                       "cursor sits.",
        "success_criteria": 1.5,
        "tips": [
            "Pinch decisively; touching-not-quite reads as pointing.",
            "Keep the other three fingers relaxed and curled.",
            "A pinch-and-hold starts a drag instead — release to drop.",
        ],
        "requires": "camera + hand",
        "next_step": None,
    },
    {
        "id": "double_click",
        "title": "Double click (thumbs up)",
        "track": "core",
        "gesture": Gesture.THUMBS_UP,         # "thumbs_up"
        "instruction": "Make a thumbs-up with your fingers closed — "
                       "that opens the item under the cursor "
                       "(double click).",
        "success_criteria": 1.5,
        "tips": [
            "Point the thumb UP; a downward thumb is thumbs_down.",
            "Turn your knuckles toward the camera so the thumb is clear.",
        ],
        "requires": "camera + hand",
        "next_step": None,
    },
    {
        "id": "right_click",
        "title": "Right click (peace)",
        "track": "core",
        "gesture": Gesture.PEACE,             # "peace"
        "instruction": "Raise index + middle fingers in a V — a quick "
                       "peace sign right-clicks at the cursor.",
        "success_criteria": 1.5,
        "tips": [
            "Spread the two fingers so they read as two, not one blob.",
            "A peace HOLD + move scrolls instead — keep the tap quick.",
        ],
        "requires": "camera + hand",
        "next_step": None,
    },
    {
        "id": "drag",
        "title": "Drag (palm grab)",
        "track": "core",
        "gesture": Gesture.PALM,              # "palm"
        "instruction": "Open your whole hand like you are grabbing a "
                       "window — palm is drag mode: hold it, move to "
                       "carry, then relax your hand to drop.",
        "success_criteria": 2.0,
        "tips": [
            "All fingers open, thumb out; hold steady for a moment.",
            "Four fingers with the thumb folded reads as FOUR — also drag.",
        ],
        "requires": "camera + hand",
        "next_step": None,
    },
    {
        "id": "scroll",
        "title": "Scroll (three fingers)",
        "track": "core",
        "gesture": Gesture.THREE,             # "three"
        "instruction": "Raise index + middle + ring fingers, then move "
                       "your hand up and down — three fingers is "
                       "scroll mode.",
        "success_criteria": 2.0,
        "tips": [
            "Keep the pinky folded — four fingers changes the meaning.",
            "Slow, vertical movements give the smoothest scrolling.",
        ],
        "requires": "camera + hand",
        "next_step": None,
    },
    {
        "id": "zoom",
        "title": "Zoom (pinch & hold)",
        "track": "core",
        "gesture": Gesture.PINCH,             # "pinch"
        "instruction": "Pinch and HOLD, then move your hand up to zoom "
                       "in and down to zoom out — release to finish.",
        "success_criteria": 2.5,
        "tips": [
            "The zoom engages after a short pinch hold (~0.3 s).",
            "A quick pinch release is just a click — hold a beat longer.",
        ],
        "requires": "camera + hand",
        "next_step": None,
    },
    # ── ADVANCED — teach what is REAL today, point to the real command ──
    {
        "id": "gaze",
        "title": "Gaze control (look to steer)",
        "track": "advanced",
        "gesture": None,
        "instruction": "Your webcam can track your eyes: look at a spot "
                       "and hold your gaze to dwell-click there.  Gaze "
                       "must be calibrated to YOUR eyes first.",
        "success_criteria": None,             # not hold-verifiable here
        "tips": [
            "Gaze + pinch fusion is real (look at a button, pinch to "
            "press it).",
            "A long blink (~1.2 s) trips the e-stop latch by default.",
        ],
        "requires": "camera (face) + calibration",
        "next_step": "airmouse --gaze-calibrate",
    },
    {
        "id": "voice",
        "title": "Voice control (say it)",
        "track": "advanced",
        "gesture": None,
        "instruction": "Voice can click, scroll, switch windows and "
                       "dictate.  Whether a speech engine is available "
                       "on your machine is checked honestly by the "
                       "guided test laboratory.",
        "success_criteria": None,
        "tips": [
            "The guided lab reports SIMULATION vs real providers — "
            "nothing pretends to be real ASR.",
            "Text-side grammar (65 commands) is tested headless and "
            "deterministic.",
        ],
        "requires": "microphone + a speech engine",
        "next_step": "airmouse test --guided   (voice section)",
    },
    {
        "id": "two_hand",
        "title": "Two-hand geometry (zoom / rotate / drag)",
        "track": "advanced",
        "gesture": None,
        "instruction": "With two-hand tracking ON, pinch BOTH hands to "
                       "engage, then pull apart to zoom, twist to "
                       "rotate and move to drag — real geometry, not "
                       "single-hand emulation.",
        "success_criteria": None,
        "tips": [
            "Requires config two_hand = true and a camera that sees "
            "both hands.",
            "The hands_free profile turns it on for you: "
            "airmouse profile hands_free.",
        ],
        "requires": "config.two_hand = true + camera",
        "next_step": "airmouse profile hands_free",
    },
    {
        "id": "sequences",
        "title": "Custom gesture sequences (registry)",
        "track": "advanced",
        "gesture": None,
        "instruction": "You can bind your own gesture sequences "
                       "(like double-pinch or wave) to actions in the "
                       "gesture registry; they feed the same execution "
                       "spine and the same safety gates as built-ins.",
        "success_criteria": None,
        "tips": [
            "Enable with config gesture_registry_enabled = true.",
            "Custom mappings pass the same risk classification — a "
            "custom sequence mapped to close_window is still refused "
            "by default.",
        ],
        "requires": "config.gesture_registry_enabled = true",
        "next_step": "airmouse gestures --help",
    },
]

#: valid lesson ids (derived, never hand-maintained twice)
_LESSON_IDS = [l["id"] for l in LESSONS]

#: all valid Gesture value strings ("pointing", "pinch", ...) — Gesture
#: members ARE plain strings, so a lesson's gesture value compares
#: directly against ``recognize_gesture()``'s returned member.
_VALID_GESTURE_VALUES = frozenset(
    v for k, v in vars(Gesture).items()
    if k.isupper() and isinstance(v, str))


# ---------------------------------------------------------------------------
# pure, deterministic core (unit-testable headless)
# ---------------------------------------------------------------------------

def academy_plan(lesson: str | None = None) -> list:
    """Return the curriculum as plain data.

    ``lesson=None`` or ``"all"`` returns every lesson; a single id
    returns just that lesson; an unknown id returns ``[]`` (the caller —
    ``run_academy`` — treats that as an honest unknown-lesson error).
    Copies are returned so callers cannot mutate the curriculum.
    """
    if lesson is None or str(lesson).strip().lower() in ("", "all"):
        chosen = LESSONS
    else:
        lid = str(lesson).strip().lower()
        chosen = [l for l in LESSONS if l["id"] == lid]
    return [{k: (list(v) if isinstance(v, list) else v)
             for k, v in l.items()} for l in chosen]


def _format_plan(plan: list, completed: set) -> str:
    """Render the lesson plan as honest plain text (headless view)."""
    lines = [
        "",
        "  +==================================================+",
        "  |            AIRMOUSE GESTURE ACADEMY              |",
        "  |     learn every gesture, practice it live        |",
        "  +==================================================+",
        "",
        f"  {len(plan)} lesson(s).  Completed so far: "
        f"{len(completed)}  (progress: {progress_path()})",
        "",
    ]
    for i, l in enumerate(plan, 1):
        lines.append(f"  Lesson {i}/{len(plan)} — {l['id']}: {l['title']}")
        lines.append(f"    what to do : {l['instruction']}")
        if l["track"] == "core":
            lines.append(f"    gesture    : {l['gesture']}   "
                         f"pass = hold it for "
                         f"{l['success_criteria']:.1f}s")
            lines.append(f"    status     : {STATUS_PHYSICAL}")
        else:
            lines.append(f"    status     : PHYSICAL — {l['requires']}; "
                         "not verifiable in this run (never auto-passed)")
            if l.get("next_step"):
                lines.append(f"    next step  : {l['next_step']}")
        for tip in l["tips"]:
            lines.append(f"    tip        : {tip}")
        if l["id"] in completed:
            lines.append("    progress   : COMPLETED on a previous run")
        lines.append("")
    lines.append("  Headless plan only — a physical lesson is NEVER "
                 "auto-passed without a camera.")
    lines.append("  Run the live classroom:  airmouse academy "
                 "(with your camera plugged in)")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# progress persistence (atomic, under the unified home)
# ---------------------------------------------------------------------------

def progress_path() -> str:
    """academy_progress.json under the authoritative AirMouse home."""
    return os.path.join(paths.airmouse_home(), "academy_progress.json")


def load_progress() -> dict:
    """Load saved progress; always returns a usable dict (fail-closed)."""
    try:
        from .persistence import read_json
        data = read_json(progress_path())
        completed = data.get("completed", [])
        if isinstance(completed, list):
            return {"completed": [c for c in completed
                                  if c in _LESSON_IDS]}
    except FileNotFoundError:
        pass
    except Exception:                       # corrupt file == no progress
        pass
    return {"completed": []}


def save_progress(completed: list) -> str:
    """Atomically persist the completed lesson-id list; returns the path."""
    from .persistence import atomic_write_json
    clean = [c for c in list(completed) if c in _LESSON_IDS]
    atomic_write_json(progress_path(), {
        "completed": clean,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    return progress_path()


# ---------------------------------------------------------------------------
# the live classroom
# ---------------------------------------------------------------------------

def run_academy(lesson: str = "all", camera: bool = True) -> int:
    """Run the Gesture Academy.  Returns a process exit code.

    * unknown lesson id      -> prints valid ids, exit 1
    * headless / no camera   -> prints the full plan honestly, exit 0
    * camera + display       -> live hold-to-pass lessons, exit 0
    """
    lid = str(lesson if lesson is not None else "all")
    plan = academy_plan(lid)
    if not plan:
        print(f"  unknown academy lesson '{lid}' — valid lesson ids: "
              + ", ".join(_LESSON_IDS))
        return 1

    completed = set(load_progress()["completed"])

    if not camera:
        print(_format_plan(plan, completed))
        return 0

    # ── camera path — everything hardware-related is guarded ────────────
    tracker = None
    try:
        _import_cv2()                       # fail fast if OpenCV is missing
        from .tracker import HandTracker
        tracker = HandTracker()
        probe = 0
        while probe < 30:                   # ~1 s of probe frames
            data = tracker.read()
            if data.get("frame") is not None:
                break
            probe += 1
            time.sleep(0.033)
        else:
            raise RuntimeError("camera opened but delivers no frames")
    except Exception as exc:
        print(f"  cannot start the live academy: {exc}")
        print("  (camera busy, missing OpenCV/MediaPipe, or no camera "
              "attached)")
        print(_format_plan(plan, completed))
        if tracker is not None:
            try:
                tracker.release()
            except Exception:
                pass
        return 0                            # honest plan, not an error

    try:
        return _live_loop(tracker, plan, completed)
    finally:
        try:
            tracker.release()
        except Exception:
            pass


def _import_cv2():
    """Prove OpenCV is importable BEFORE any tracker/window is built.

    Raises on failure so run_academy's guarded block can degrade to the
    honest headless plan.  (cv2 itself is used inside _live_loop.)
    """
    import cv2
    return cv2


def _live_loop(tracker, plan: list, completed: set) -> int:
    """The per-lesson live teaching loop (mirrors tutorial.py)."""
    from .gestures import recognize_gesture

    # lazy imports: cv2 is guaranteed importable here (run_academy checked)
    import cv2

    def note(msg: str) -> None:
        print(f"  {msg}")

    note("Gesture Academy live — hold each gesture to pass. "
         "[SPACE] skip   [q] quit (progress saved)")

    display_ok = [True]                     # degrade honestly if no GUI
    lost_frames = 0                         # camera died mid-lesson guard
    idx = 0
    hold_start: float | None = None

    def _put(frame, text, y, scale=0.7, color=(255, 255, 255), thick=1):
        h, w = frame.shape[:2]
        size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX,
                               scale, thick)[0]
        cv2.putText(frame, text,
                    (max(10, (w - size[0]) // 2), y),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick,
                    cv2.LINE_AA)

    while idx < len(plan):
        lesson = plan[idx]
        if lesson["track"] != "core":
            # Advanced: teach on the terminal, verify NOTHING, pass nothing.
            note(f"LESSON {idx + 1}/{len(plan)} — {lesson['title']} "
                 f"[advanced]")
            note(f"  {lesson['instruction']}")
            note(f"  requires: {lesson['requires']}"
                 + (f"  |  try: {lesson['next_step']}"
                    if lesson.get("next_step") else ""))
            idx += 1
            hold_start = None
            continue

        if lesson["id"] in completed:
            note(f"skipping '{lesson['id']}' — already completed "
                 "(re-run with: airmouse academy "
                 f"{lesson['id']} to practice again)")
            idx += 1
            hold_start = None
            continue

        target = lesson["gesture"]
        if target not in _VALID_GESTURE_VALUES:
            target = None                      # never pass on a bad lesson
        needed = float(lesson["success_criteria"] or 2.0)
        hold_for = 0.0

        while True:
            data = tracker.read()
            frame = data.get("frame")
            if frame is None:
                lost_frames += 1
                if lost_frames > 600:       # ~20 s with no picture
                    note("camera stopped delivering frames — ending the "
                         "live session (progress saved)")
                    save_progress(sorted(completed))
                    cv2.destroyAllWindows()
                    return 0
                time.sleep(0.033)
                continue
            lost_frames = 0

            detected, conf = Gesture.NONE, 0.0
            if data.get("hand_found") and data.get("landmarks") is not None:
                res = recognize_gesture(data["landmarks"])
                detected = res["gesture"]
                try:
                    conf = float(res.get("confidence", 0.0))
                except (TypeError, ValueError):
                    conf = 0.0

            correct = (target is not None and detected == target)
            if correct:
                if hold_start is None:
                    hold_start = time.perf_counter()
                hold_for = time.perf_counter() - hold_start
            else:
                hold_start = None
                hold_for = 0.0

            # ── overlay (same drawing philosophy as tutorial.py) ────────
            h, w = frame.shape[:2]
            overlay = frame.copy()
            cv2.rectangle(overlay, (0, 0), (w, 150), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

            _put(frame, f"AIRMOUSE ACADEMY — lesson {idx + 1}"
                        f"/{len(plan)}: {lesson['title']}",
                 35, 0.65, (0, 255, 255), 2)
            _put(frame, lesson["instruction"][:78], 75, 0.55,
                 (255, 255, 255), 1)
            _put(frame, lesson["tips"][0][:78], 105, 0.5,
                 (0, 255, 200), 1)
            _put(frame, f"target: {lesson['gesture']}   "
                        f"detected: {str(detected)} "
                        f"({conf * 100:.0f}% confidence)",
                 135, 0.55,
                 (0, 255, 0) if correct else (0, 0, 255), 1)

            # hold-to-pass progress bar
            bar_w, bx, by = w - 100, 50, h - 60
            prog = min(1.0, hold_for / needed) if needed > 0 else 0.0
            cv2.rectangle(frame, (bx, by), (bx + bar_w, by + 18),
                          (40, 40, 40), -1)
            cv2.rectangle(frame, (bx, by), (bx + int(bar_w * prog), by + 18),
                          (0, 255, 0), -1)
            _put(frame, f"hold {hold_for:.1f}s / {needed:.1f}s to pass",
                 by - 8, 0.5, (255, 255, 255), 1)
            _put(frame, "[SPACE] skip   [q] quit (progress saved)",
                 h - 15, 0.45, (120, 120, 120), 1)

            if display_ok[0]:
                try:
                    cv2.imshow("AirMouse Gesture Academy", frame)
                    key = cv2.waitKey(1) & 0xFF
                except cv2.error:
                    display_ok[0] = False       # no GUI: stop honestly
                    note("(no display available — the live classroom "
                         "needs a window; showing the plan instead)")
                    print(_format_plan(plan, completed))
                    save_progress(sorted(completed))
                    cv2.destroyAllWindows()
                    return 0
            else:
                key = 0xFF

            if key in (ord('q'), 27):           # quit, save progress
                save_progress(sorted(completed))
                note(f"quitting — progress saved to {progress_path()} "
                     f"({len(completed)} lesson(s) completed)")
                return 0
            if key == ord(' '):                 # explicit skip: NO credit
                note(f"skipped '{lesson['id']}' — skipped lessons are "
                     "not marked complete")
                break
            if correct and hold_for >= needed:
                completed.add(lesson["id"])
                save_progress(sorted(completed))
                note(f"PASSED '{lesson['id']}' — held for "
                     f"{hold_for:.1f}s, progress saved")
                break

        idx += 1
        hold_start = None

    cv2.destroyAllWindows()
    note(f"Academy session complete — {len(completed)}/{len(plan)} "
         "lesson(s) completed.  Advanced lessons still point to their "
         "real commands (they are never auto-passed).")
    return 0
