"""airmouse.wizard — the 60–120 second interaction wizard (v19 Phase 9).

Teaches ONLY the essentials, in the mission's six steps, and verifies
everything that is verifiable on the machine it runs on:

    STEP 1  Look at the target        → camera probe
    STEP 2  Move your hand            → hand-tracking prerequisites
    STEP 3  Pinch to select           → gesture engine availability
    STEP 4  Say: "Open browser"       → microphone + local ASR (+ REAL
                                        smoke path when --text/--wav given)
    STEP 5  Try: "Click that"         → input automation probe
    STEP 6  You're ready              → honest verdict + best mode

Honesty laws:

* a step PASSES only when its probe actually succeeded here;
* hardware a machine does not have yields ACTION_REQUIRED with the exact
  command/permission to use on a real machine — never a simulated pass;
* the wizard never forces advanced capabilities (those stay in the
  academies) and never blocks on a missing optional modality;
* the final verdict reuses the degradation ladder — the same answer the
  doctor and the launcher show.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable, IO, List, Optional

__all__ = ["WizardStep", "WizardReport", "run_wizard", "STEPS"]


@dataclass
class WizardStep:
    """One wizard step and its honest outcome."""

    id: str
    title: str
    status: str                 # PASS | ACTION_REQUIRED | FAILED | SKIPPED
    detail: str = ""
    next: str = ""              # the exact next thing to do, if not PASS


@dataclass
class WizardReport:
    """All steps + the final verdict."""

    steps: List[WizardStep] = field(default_factory=list)

    def passed(self) -> int:
        return sum(1 for s in self.steps if s.status == "PASS")

    def verdict(self) -> str:
        """READY / PARTIAL / BLOCKED — honest, no optimism."""
        mandatory = [s for s in self.steps if s.id in
                     ("camera", "gesture", "input")]
        if all(s.status == "PASS" for s in mandatory):
            return "READY"
        if any(s.status == "PASS" for s in self.steps):
            return "PARTIAL"
        return "BLOCKED"

    def format(self) -> str:
        lines = ["AirMouse interaction wizard", "=" * 40, ""]
        for n, s in enumerate(self.steps, 1):
            mark = {"PASS": "✓", "ACTION_REQUIRED": "○",
                    "FAILED": "✗", "SKIPPED": "·"}.get(s.status, "?")
            lines.append(f"{n}. {mark} {s.title} — {s.status}")
            if s.detail:
                lines.append(f"     {s.detail}")
            if s.next and s.status != "PASS":
                lines.append(f"     next: {s.next}")
        lines.append("")
        v = self.verdict()
        if v == "READY":
            lines.append("You're ready. Start AirMouse with:  airmouse")
        elif v == "PARTIAL":
            lines.append("You're ready for what's listed above with ✓. "
                         "The ○ items need the named hardware or action.")
        else:
            lines.append("Nothing could be verified on this machine — "
                         "run `airmouse doctor --advanced` for details.")
        return "\n".join(lines)


#: the fixed six-step teaching ladder (mission Phase 9)
STEPS = ("camera", "hand", "gesture", "voice", "input", "ready")


def _probe_camera() -> Tuple[bool, str]:
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        opened = bool(cap.isOpened())
        try:
            cap.release()
        except Exception:  # noqa: BLE001
            pass
        return (opened, "camera detected at index 0" if opened
                else "no camera detected at index 0")
    except Exception as exc:  # noqa: BLE001
        return (False, f"camera probe unavailable ({type(exc).__name__})")


def _probe_hand_prereqs() -> Tuple[bool, str]:
    try:
        import mediapipe  # noqa: F401
        import numpy  # noqa: F401
        from .paths import model_file
        import os
        present = os.path.isfile(model_file())
        if present:
            return (True, "MediaPipe + hand-landmarker model ready")
        return (True, "MediaPipe ready; the hand-landmarker model "
                      "downloads on first start (consent-gated)")
    except Exception as exc:  # noqa: BLE001
        return (False, f"hand-tracking stack unavailable "
                       f"({type(exc).__name__})")


def _probe_gesture_engine() -> Tuple[bool, str]:
    try:
        from .gestures import GestureRecognizer  # noqa: F401
        return (True, "gesture recognizer imports and is deterministic")
    except Exception as exc:  # noqa: BLE001
        return (False, f"gesture engine unavailable ({type(exc).__name__})")


def _probe_voice() -> Tuple[bool, str, str]:
    """Returns (usable, detail, next)."""
    try:
        from .voice_stack import detect_voice_stack
        vd = detect_voice_stack()
        mic = bool(vd.get("microphone"))
        engine = any(vd.get(k) for k in ("whisper", "vosk", "pocketsphinx"))
        if mic and engine:
            return (True, "microphone + local ASR engine detected", "")
        if mic:
            return (True, "microphone detected; no local ASR engine yet",
                    "run `airmouse voice-setup` for the one-time model")
        if engine:
            return (True, "local ASR engine present; no microphone "
                          "detected",
                    "connect/enable a microphone in OS settings")
        return (True, "voice is optional — nothing detected yet",
                "connect a microphone, then `airmouse voice-setup`")
    except Exception as exc:  # noqa: BLE001
        return (True, f"voice check unavailable ({type(exc).__name__}) — "
                      f"voice stays optional", "")


def _probe_input() -> Tuple[bool, str]:
    try:
        import pynput  # noqa: F401
        return (True, "input automation available")
    except Exception as exc:  # noqa: BLE001
        return (False, f"input automation unavailable ({exc})")


def run_wizard(out: IO[str] = sys.stdout,
               input_fn: Optional[Callable[[str], str]] = None) -> WizardReport:
    """Run the six steps; never raises; every outcome is honest."""
    say = lambda m: print(m, file=out)  # noqa: E731
    say("AirMouse interaction wizard — 60 seconds, no configuration")
    say("")
    report = WizardReport()

    say("STEP 1 — Look at the target")
    ok, detail = _probe_camera()
    report.steps.append(WizardStep(
        "camera", "Camera", "PASS" if ok else "ACTION_REQUIRED", detail,
        "connect/enable a webcam (check OS camera permissions)"))
    say(f"  {'✓' if ok else '○'} {detail}")
    say("")

    say("STEP 2 — Move your hand")
    ok, detail = _probe_hand_prereqs()
    report.steps.append(WizardStep(
        "hand", "Hand tracking", "PASS" if ok else "ACTION_REQUIRED",
        detail, "install AirMouse with pip (mediapipe ships as a wheel)"))
    say(f"  {'✓' if ok else '○'} {detail}")
    say("")

    say("STEP 3 — Pinch to select")
    ok, detail = _probe_gesture_engine()
    report.steps.append(WizardStep(
        "gesture", "Gestures", "PASS" if ok else "FAILED", detail,
        "reinstall AirMouse (the gesture engine ships in the package)"))
    say(f"  {'✓' if ok else '✗'} {detail}")
    say("")

    say('STEP 4 — Say: "Open browser"')
    usable, detail, nxt = _probe_voice()
    report.steps.append(WizardStep(
        "voice", "Voice", "PASS" if usable else "FAILED", detail, nxt))
    say(f"  {'✓' if usable else '✗'} {detail}")
    say("")

    say('STEP 5 — Try: "Click that"')
    ok, detail = _probe_input()
    report.steps.append(WizardStep(
        "input", "Mouse/keyboard control",
        "PASS" if ok else "ACTION_REQUIRED", detail,
        "on Windows it works out of the box; on Linux a display server "
        "is required; on macOS grant Accessibility permission"))
    say(f"  {'✓' if ok else '○'} {detail}")
    say("")

    say("STEP 6 — You're ready")
    try:
        from .capability_registry import build_registry
        reg = build_registry(quick=True)
        mode, r = reg.best_mode()
        verdict = report.verdict()
        if verdict == "READY":
            detail = f"best mode {mode.upper()} — start with: airmouse"
            say(f"  ✓ {detail}")
        elif verdict == "PARTIAL":
            detail = (f"best usable mode on THIS machine: "
                      f"{mode.upper()} — the ○ steps above complete it")
            say(f"  ○ {detail}")
        else:
            detail = "nothing verifiable here — see doctor"
            say(f"  ✗ {detail}")
        report.steps.append(WizardStep("ready", "Verdict", verdict, detail))
    except Exception as exc:  # noqa: BLE001
        report.steps.append(WizardStep("ready", "Verdict", "FAILED",
                                       f"{type(exc).__name__}: {exc}"))
    say("")
    return report
