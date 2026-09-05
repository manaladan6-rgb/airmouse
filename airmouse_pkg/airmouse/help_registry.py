"""airmouse.help_registry — contextual help answered from REAL capability
data (v16.5, mission §20).

The teacher must answer "what can I do?", "how do I scroll?",
"teach me this", "what gesture should I use?" and "why didn't that
work?" locally, without any network and without inventing capability.
Every row in this registry is derived from the shipped code:

* gestures  — the documented pose/motion map in ``airmouse.gestures``
  (module header) cross-checked against the execution spine's
  ``airmouse.gesture_spine.RISK_CLASSES`` (what may actually dispatch).
* voice     — the shipped grammar in ``airmouse.voice_control.COMMANDS``
  (30 canonical commands with their phrase variants).
* gaze      — the real gaze pipeline capabilities (calibration, dwell,
  blink, Eye Assist) with their honest status.

Honesty contract: an answer may never describe an unwired feature as
available.  Destructive gestures (OK → close window) are labelled
"refused by default by the safety policy" — that is the spine's real
behavior.  Two-hand ROTATE/DRAG are labelled "detected — OS action not
mapped" exactly as the live loop reports them.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "GESTURE_HELP", "VOICE_HELP", "GAZE_HELP",
    "HELP_TOPICS", "answer", "help_me_panel", "run_help_me",
]

# ---------------------------------------------------------------------------
# gesture registry — pose/motion → what REALLY happens (gestures.py + spine)
# ---------------------------------------------------------------------------

#: (gesture, how, effect, note) — note carries the honest status line
GESTURE_HELP: List[Tuple[str, str, str, str]] = [
    ("pointing", "index finger only",
     "moves the cursor",
     "continuous control — the One Euro/Kalman filter smooths it"),
    ("pinch", "thumb tip + index tip",
     "left click",
     "a quick pinch clicks; pinch + hold starts a drag"),
    ("pinch_hold", "pinch and keep it held",
     "drag / scroll mode",
     "move vertically while held = scroll; horizontal = drag"),
    ("pinch_release", "let go after a hold",
     "drops the drag",
     "never fires after a tap (safety hysteresis)"),
    ("double_pinch", "two quick pinches",
     "double click",
     "debounced so one pinch cannot double-fire"),
    ("thumbs_up", "thumb up, fingers closed",
     "double click (opens item)",
     "thumb down is a different gesture"),
    ("thumbs_down", "thumb pointing down",
     "cancel / reject",
     "recognized; default action refuses destructive follow-ups"),
    ("peace", "index + middle fingers",
     "right click",
     "keep the V clearly spread"),
    ("palm", "whole open hand",
     "drag mode (grab and carry)",
     "relax the hand to drop"),
    ("fist", "all fingers closed",
     "freeze the cursor",
     "a safety pose — movement stops"),
    ("ok", "thumb + middle touching",
     "close window (Alt+F4 class)",
     "DESTRUCTIVE — refused by default by the safety policy "
     "(config gesture_allow_destructive = true to change)"),
    ("gun", "thumb up + index point",
     "snap cursor to screen center",
     ""),
    ("rock", "index + pinky up",
     "minimize window",
     ""),
    ("six", "thumb + index + pinky",
     "task switcher",
     ""),
    ("shaka", "thumb + pinky out",
     "volume mode (hold, then move up/down)",
     "continuous axis, not a one-shot"),
    ("swipe_left / swipe_right", "sweep hand sideways",
     "browser back / forward",
     "temporal motion — needs movement, not just a pose"),
    ("swipe_up / swipe_down", "sweep hand vertically",
     "scroll up / down (fast)",
     "temporal motion gesture"),
    ("circle_cw / circle_ccw", "draw a circle in the air",
     "zoom in / zoom out",
     "clockwise zooms in, counter-clockwise zooms out"),
    ("push / pull", "hand toward / away from camera",
     "zoom in / zoom out",
     "depth motion gesture"),
    ("shake", "quick side-to-side shake",
     "recognized — temporal gesture",
     "map it yourself via the gesture registry (airmouse gestures)"),
    ("wave", "open-hand wave",
     "recognized — temporal gesture",
     "map it yourself via the gesture registry (airmouse gestures)"),
    ("four / five", "four fingers / all five",
     "recognized (palm family)",
     "no default OS action — available for custom mappings"),
]

#: the honest two-hand story (mission §13)
TWO_HAND_HELP: str = (
    "Two hands: pinch with BOTH hands and move them apart/together = zoom "
    "(real).  Rotation and two-hand drag are DETECTED but their OS action "
    "is NOT MAPPED yet — AirMouse reports them honestly instead of faking "
    "an action."
)

# ---------------------------------------------------------------------------
# voice registry — the REAL shipped grammar (voice_control.COMMANDS)
# ---------------------------------------------------------------------------


def _voice_rows() -> List[Tuple[str, str]]:
    """(canonical, example phrases) pulled live from the shipped grammar."""
    try:
        from .voice_control import COMMANDS as _COMMANDS
    except Exception:  # pragma: no cover — grammar module always ships
        return []
    rows: List[Tuple[str, str]] = []
    for canonical, phrases in _COMMANDS.items():
        examples = ", ".join(f'"{p}"' for p in list(phrases)[:3])
        rows.append((str(canonical), examples))
    return rows


VOICE_HELP_NOTE: str = (
    "Command recognition is a deterministic OFFLINE grammar — it matches "
    "the phrases above.  It is NOT full speech recognition: install an "
    "optional local ASR engine (see 'airmouse voice-status') for "
    "dictation and free speech."
)


def _gaze_rows() -> List[Tuple[str, str]]:
    return [
        ("calibration", "airmouse --gaze-calibrate — guided gaze→screen "
                        "calibration (saved locally)"),
        ("cursor control", "airmouse --gaze — look to move the pointer "
                           "(smoothed, jitter-filtered)"),
        ("dwell", "hold your gaze on a target to activate it — dwell time "
                  "is configurable"),
        ("blink", "blink actions where wired (Eye Assist flows)"),
        ("eye assist", "eyes SELECT, hand CONFIRMS (pinch) — fewer "
                       "accidental activations"),
    ]


# ---------------------------------------------------------------------------
# the answer engine — deterministic, local, honest
# ---------------------------------------------------------------------------

HELP_TOPICS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("overview", ("what can i do", "what can you do", "help",
                  "capabilities", "commands")),
    ("scroll", ("how do i scroll", "scrolling", "scroll")),
    ("click", ("how do i click", "click", "left click")),
    ("drag", ("how do i drag", "drag")),
    ("zoom", ("how do i zoom", "zoom")),
    ("teach", ("teach me this", "teach me", "learn", "training",
               "onboarding")),
    ("gesture", ("what gesture should i use", "which gesture",
                 "gesture for")),
    ("broken", ("why didn't that work", "why did it not work",
                "not working", "didn't work", "doesn't work")),
)


def _topic_for(question: str) -> Optional[str]:
    q = " ".join(str(question or "").lower().split())
    if not q:
        return None
    for topic, keys in HELP_TOPICS:
        for k in keys:
            if k in q:
                return topic
    return None


def answer(question: str) -> str:
    """A deterministic, local answer for one natural-language question.

    Unknown questions get an honest fallback (categories + how to ask) —
    never a hallucinated capability.
    """
    topic = _topic_for(question)
    if topic == "overview":
        return help_me_panel()
    if topic == "scroll":
        return (
            "Scrolling, three ways:\n"
            "  1. PINCH + HOLD, then move your hand up/down (vertical "
            "pinch = scroll).\n"
            "  2. SWIPE UP / SWIPE DOWN — a fast vertical hand sweep.\n"
            "  3. Say \"scroll up\" or \"scroll down\" (voice).\n"
            "Practice it:  airmouse teach --gesture"
        )
    if topic == "click":
        return (
            "Clicking:\n"
            "  - left click: pinch (thumb tip + index tip).\n"
            "  - double click: thumbs-up, or a quick double pinch.\n"
            "  - right click: peace sign (index + middle).\n"
            "  - voice: say \"click\", \"double click\", \"right click\".\n"
            "Practice it:  airmouse teach --gesture   |   airmouse academy"
        )
    if topic == "drag":
        return (
            "Dragging: PALM (whole open hand) = grab mode.  Hold it, move "
            "to carry, relax your hand to drop.\n"
            "Also: pinch + hold + move = drag (release to drop).\n"
            "Practice it:  airmouse academy drag"
        )
    if topic == "zoom":
        return (
            "Zooming:\n"
            "  - circle clockwise to zoom in, counter-clockwise to zoom "
            "out.\n"
            "  - push your hand toward the camera (in) / pull away "
            "(out).\n"
            "  - two hands: pinch with both and spread/close them.\n"
            "  - voice: say \"zoom in\" / \"zoom out\"."
        )
    if topic == "teach":
        return (
            "The interactive tour takes about 3-5 minutes:\n"
            "  airmouse teach          (resume where you left off)\n"
            "  airmouse learn          (everything: voice, gaze, "
            "gestures, fusion)\n"
            "  airmouse teach --voice  |  --gaze  |  --gesture  |  "
            "--fusion\n"
            "Lessons only pass when a sensor actually verified them — "
            "physical lessons need a camera/microphone."
        )
    if topic == "gesture":
        return _gesture_answer(str(question or ""))
    if topic == "broken":
        return (
            "Let's debug honestly — actions are gated in this order:\n"
            "  1. E-STOP / freeze — is the cursor frozen (fist pose)? "
            "Say \"unfreeze\".\n"
            "  2. Confidence gate — a weak detection is refused "
            "(SAFE ≥ 0.45, CAUTION ≥ 0.60).  Hold the gesture steadier, "
            "improve lighting, or sit 50-80 cm from the camera.\n"
            "  3. Safety policy — destructive gestures (OK = close "
            "window) are refused unless explicitly enabled.\n"
            "  4. Rate limit — identical actions need ≥ 0.12 s apart.\n"
            "  5. The gesture itself — check the mapping:  airmouse "
            "gestures\n"
            "Run 'airmouse doctor' to check your hardware pipeline."
        )
    return (
        "I can help with: gestures, voice commands, gaze/dwell, zoom, "
        "drag, scrolling, teaching, and troubleshooting.\n"
        "Try: \"how do I scroll?\" · \"what gesture should I use for "
        "right click?\" · \"why didn't that work?\" · \"teach me this\"\n"
        "Full reference: airmouse help-me   |   airmouse commands"
    )


def _gesture_answer(question: str) -> str:
    """Match a keyword in the question to a gesture row."""
    q = " ".join(question.lower().split())
    keyword_map = [
        ("right click", "peace"), ("right-click", "peace"),
        ("double click", "thumbs_up"), ("left click", "pinch"),
        ("click", "pinch"), ("scroll", "pinch_hold"),
        ("drag", "palm"), ("freeze", "fist"), ("volume", "shaka"),
        ("minimize", "rock"), ("zoom", "circle_cw / circle_ccw"),
        ("back", "swipe_left / swipe_right"),
        ("close", "ok"),
    ]
    for key, gesture in keyword_map:
        if key in q:
            for g, how, effect, note in GESTURE_HELP:
                if g == gesture:
                    extra = f"\n  note: {note}" if note else ""
                    return (f'For "{key}" use the {gesture} gesture '
                            f"({how}).\n  effect: {effect}{extra}\n"
                            f"Practice: airmouse teach --gesture")
    rows = "\n".join(f"  {g:<24} {effect}" for g, _h, effect, _n
                     in GESTURE_HELP[:12])
    return ("The core gesture map:\n" + rows +
            "\nFull list:  airmouse gestures   |   practice: airmouse "
            "teach --gesture")


def help_me_panel() -> str:
    """The full 'what can I do' panel (mission §20)."""
    lines: List[str] = ["AIRMouse — what you can do", ""]
    lines.append("HANDS (camera)")
    for g, how, effect, note in GESTURE_HELP[:12]:
        flag = "" if not note.startswith("DESTRUCTIVE") else \
            "  [refused by default]"
        lines.append(f"  {g:<14} {how:<28} → {effect}{flag}")
    lines.append(f"  {TWO_HAND_HELP}")
    lines.append("")
    lines.append("VOICE (offline grammar)")
    try:
        from .voice_control import COMMANDS as _C
        names = list(_C.keys())
        shown = names[:10]
        lines.append("  " + ", ".join(shown) +
                     f"  … and {max(0, len(names) - len(shown))} more "
                     "(airmouse commands)")
    except Exception:  # pragma: no cover — grammar always ships
        lines.append("  (grammar unavailable in this build)")
    lines.append(f"  note: {VOICE_HELP_NOTE}")
    lines.append("")
    lines.append("EYES (webcam gaze)")
    for name, desc in _gaze_rows():
        lines.append(f"  {name:<14} {desc}")
    lines.append("")
    lines.append("LEARN IT — the teacher")
    lines.append("  airmouse teach      interactive 3-5 minute tour "
                 "(resumes automatically)")
    lines.append("  airmouse learn      all academies: voice · gaze · "
                 "gestures · fusion")
    lines.append("  airmouse help-me    this panel; ask questions like "
                 "\"how do I scroll?\"")
    lines.append("")
    lines.append("HEALTH")
    lines.append("  airmouse doctor     hardware + environment check")
    lines.append("  airmouse privacy    what is stored locally "
                 "(local-first; nothing is uploaded)")
    return "\n".join(lines)


def run_help_me(topic: str = "", out: Optional[Any] = None) -> int:
    """`airmouse help-me [question]` — prints an answer and exits."""
    dest = sys.stdout if out is None else out
    text = answer(str(topic or "").strip()) if str(topic or "").strip() \
        else help_me_panel()
    print(text, file=dest)
    return 0
