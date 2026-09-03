"""AirMouse Simple v5.0.0 — single-file edition. Everything in one file:
hybrid One Euro + Kalman cursor filter, adaptive calibration, pinch-to-zoom,
optional voice control, macro record/replay. Usage: python airmouse_simple.py
[--cam N] [--voice] [--turbo] [--no-kalman] [--no-calibration] [--zoom-off]
[--record NAME] [--play NAME]

Gestures (actions fire ONCE on gesture ENTER; the cursor follows the index tip
whenever a hand is visible unless FIST-toggled freeze is on):
    POINT (index only) = move cursor; PINCH (thumb tip < 0.07 from index tip) =
    left click on pinch start (0.25 s cooldown), holding >= 0.30 s engages
    pinch-to-zoom; PEACE (index+middle) = right click; THREE (index+middle+ring)
    = scroll mode (vertical hand travel * 60); PALM (>= 4 fingers up) = drag
    (press on enter, release on exit); FIST (0 up) = freeze toggle; THUMB
    (thumb only) = double click.

Finger-up heuristic: the four fingers are "up" when tip.y < pip.y (classic
tip-above-joint test on the mirrored frame); the thumb is "up" when its tip
sits farther from the pinky MCP than its own MCP joint does
(dist(4,17) > 1.2 * dist(2,17)) — extended/spread thumb up, tucked = down.

Design: ONLY stdlib + numpy are imported at module top level, so the embedded
filter / calibration / zoom / macro logic imports and unit-tests headless.
cv2 + mediapipe are imported ONLY inside CameraTracker, pynput ONLY lazily in
the mouse helpers, speech_recognition ONLY inside VoiceEngine.start(), and
tkinter ONLY inside detect_screen() — heavy/optional deps degrade gently.
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import sys
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np  # only non-stdlib top-level import (headless-testable logic)

__version__ = "5.0.0"

# ==== SECTION: constants ====================================================

FRAME_DT = 1.0 / 30.0        # internal filter frame period (30 fps assumption)
LOST_RESET_FRAMES = 5        # hand-lost threshold that resets gesture state
PINCH_DIST = 0.07            # normalized thumb-tip <-> index-tip click radius
CLICK_COOLDOWN = 0.25        # seconds between pinch clicks
CURSOR_DEADZONE = 0.003      # normalized cursor deadzone
SCROLL_GAIN = 60.0           # scroll ticks per normalized hand-travel unit
MACRO_DIR = os.path.join(os.path.expanduser("~"), ".airmouse", "macros")

# ==== SECTION: lazy pynput helpers (mouse actions; safe no-ops headless) ====

_mouse: Any = None
_mouse_probed = False
_mouse_notice = False


def _get_mouse() -> Any:
    """Cached pynput mouse Controller, or None (one-time lazy probe)."""
    global _mouse, _mouse_probed, _mouse_notice
    if not _mouse_probed:
        _mouse_probed = True
        try:
            from pynput.mouse import Controller  # lazy import, cached
            _mouse = Controller()
        except Exception:
            _mouse = None
            if not _mouse_notice:
                _mouse_notice = True
                print("[mouse] pynput unavailable — mouse actions disabled")
    return _mouse


def _mouse_call(action: Any) -> None:
    """Run an action on the mouse controller; swallow every failure."""
    m = _get_mouse()
    if m is not None:
        try:
            action(m)
        except Exception:
            pass


def _left_button() -> Any:
    from pynput.mouse import Button  # lazy import
    return Button.left


def move_mouse(x: int, y: int) -> None:
    """Absolute cursor move; no-op when pynput is missing."""
    _mouse_call(lambda m: setattr(m, "position", (int(x), int(y))))


def click(button: str = "left") -> None:
    """Press+release a mouse button ("left"/"right"/"middle"); no-op safe."""
    _mouse_call(lambda m: (m.press(button), m.release(button)))


def double_click() -> None:
    """Two rapid left clicks."""
    click("left")
    time.sleep(0.03)
    click("left")


def scroll(steps: int) -> None:
    """Vertical wheel scroll (positive = up); no-op safe."""
    _mouse_call(lambda m: m.scroll(0, int(steps)))


def drag_press() -> None:
    """Press and hold the left button (drag start); no-op safe."""
    _mouse_call(lambda m: m.press(_left_button()))


def drag_release() -> None:
    """Release the held left button (drag stop); no-op safe."""
    _mouse_call(lambda m: m.release(_left_button()))


def zoom_scroll(ticks: int) -> None:
    """Ctrl + wheel zoom (pynput keyboard+mouse), mouse-only fallback; never raises."""
    if not int(ticks):
        return
    try:
        from pynput.keyboard import Controller as KC, Key as Key  # lazy import
        from pynput.mouse import Controller as MC
        kb, ms = KC(), MC()
        try:
            kb.press(Key.ctrl)
            ms.scroll(0, int(ticks))
        finally:
            try:
                kb.release(Key.ctrl)
            except Exception:
                pass
    except Exception:
        scroll(int(ticks))  # mouse-only fallback (no ctrl modifier)

# ==== SECTION: One Euro filter ==============================================


class _LowPass:
    """Scalar exponential low-pass (the 1 EUR filter's core primitive)."""
    __slots__ = ("value", "_primed")

    def __init__(self) -> None:
        self.value, self._primed = 0.0, False

    def filter(self, x: float, alpha: float) -> float:
        if not self._primed:
            self._primed, self.value = True, float(x)
        else:
            self.value += alpha * (float(x) - self.value)
        return self.value

    def reset(self) -> None:
        self.value, self._primed = 0.0, False


def _cutoff_alpha(cutoff: float, dt: float) -> float:
    """One EUR smoothing alpha for a cutoff frequency and frame dt."""
    tau = 1.0 / (2.0 * math.pi * max(cutoff, 1e-6))
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter2D:
    """Adaptive-cutoff low-pass for 2-D positions (One EUR filter).

    Two 1-D channels whose cutoff widens with the filtered signal speed:
    jitter rejection at rest, responsiveness in motion. Every filter() call
    advances one internal 30 fps frame (dt = 1/30 s) — deterministic and
    headless-testable. Defaults: mincutoff=1.2, beta=1.5, dcutoff=1.0.
    """

    def __init__(self, mincutoff: float = 1.2, beta: float = 1.5,
                 dcutoff: float = 1.0, fps: float = 30.0) -> None:
        self.min_cutoff, self.beta, self.d_cutoff = float(mincutoff), float(beta), float(dcutoff)
        self._dt = 1.0 / max(float(fps), 1e-6)
        self._x, self._y, self._dx, self._dy = _LowPass(), _LowPass(), _LowPass(), _LowPass()
        self._prev, self._primed = (0.0, 0.0), False

    def filter(self, x: float, y: float) -> Tuple[float, float]:
        if not self._primed:  # first sample snaps (no history to smooth)
            self._primed, self._prev = True, (float(x), float(y))
            self._x.filter(x, 1.0), self._y.filter(y, 1.0)
            self._dx.filter(0.0, 1.0), self._dy.filter(0.0, 1.0)
            return (float(x), float(y))
        dx, dy = (float(x) - self._prev[0]) / self._dt, (float(y) - self._prev[1]) / self._dt
        self._prev = (float(x), float(y))
        edx = self._dx.filter(dx, _cutoff_alpha(self.d_cutoff, self._dt))
        edy = self._dy.filter(dy, _cutoff_alpha(self.d_cutoff, self._dt))
        ax = _cutoff_alpha(self.min_cutoff + self.beta * abs(edx), self._dt)
        ay = _cutoff_alpha(self.min_cutoff + self.beta * abs(edy), self._dt)
        return (self._x.filter(x, ax), self._y.filter(y, ay))

    def reset(self) -> None:
        self._x.reset(), self._y.reset(), self._dx.reset(), self._dy.reset()
        self._prev, self._primed = (0.0, 0.0), False

# ==== SECTION: Kalman filter ================================================


class Kalman1D:
    """Constant-velocity Kalman filter for one axis (state [pos, vel]).

    Discrete white-noise-acceleration model (q = process, r = measurement
    noise); Joseph-form covariance update; dt clamped to [1e-4, 0.5] s.
    """

    def __init__(self, process_noise: float = 1.0, measurement_noise: float = 0.05) -> None:
        self.q, self.r = float(process_noise), float(measurement_noise)
        self.x, self.P = np.zeros(2), np.diag([self.r, 1.0])
        self._primed = False

    @property
    def velocity(self) -> float:
        """Posterior velocity (units/s) — the hybrid filter's speed source."""
        return float(self.x[1])

    def filter(self, z: float, dt: float = FRAME_DT) -> float:
        """Predict + update with one measurement; returns posterior position."""
        dtc = min(max(float(dt), 1e-4), 0.5)
        if not self._primed:
            self._primed = True
            self.x, self.P = np.array([float(z), 0.0]), np.diag([self.r, 1.0])
            return float(z)
        pos, vel = float(self.x[0]), float(self.x[1])
        pos_p = pos + vel * dtc
        F = np.array([[1.0, dtc], [0.0, 1.0]])
        Q = self.q * np.array([[dtc ** 4 / 4.0, dtc ** 3 / 2.0],
                               [dtc ** 3 / 2.0, dtc ** 2]])
        P = F @ self.P @ F.T + Q
        S = P[0, 0] + self.r                       # H = [1, 0]
        k0, k1 = P[0, 0] / S, P[1, 0] / S          # Kalman gain [pos, vel]
        resid = float(z) - pos_p
        self.x = np.array([pos_p + k0 * resid, vel + k1 * resid])
        IKH = np.array([[1.0 - k0, 0.0], [-k1, 1.0]])
        K = np.array([[k0], [k1]])
        self.P = IKH @ P @ IKH.T + K * self.r * K.T
        return float(self.x[0])

    def reset(self) -> None:
        self._primed = False
        self.x, self.P = np.zeros(2), np.diag([self.r, 1.0])

# ==== SECTION: hybrid One Euro + Kalman filter ==============================


class HybridFilter2D:
    """Speed-adaptive blend of OneEuroFilter2D and two Kalman1D channels.

    kalman_weight slides from 0.85 at rest down to 0.15 as speed_ema — an EMA
    (alpha 0.25) of the KALMAN posterior velocity magnitude — crosses
    SPEED_REF (0.15 units/s). The Kalman velocity channel (not the noisy One
    Euro delta) is the speed source, so a still hand reads ~0 speed and stays
    rock solid. use_kalman=False (--no-kalman) gives pure One Euro.
    """

    SPEED_REF, EMA_ALPHA = 0.15, 0.25
    MAX_KALMAN_WEIGHT, MIN_KALMAN_WEIGHT = 0.85, 0.15

    def __init__(self, use_kalman: bool = True) -> None:
        self.use_kalman = bool(use_kalman)
        self._one_euro = OneEuroFilter2D()
        self._kx, self._ky = Kalman1D(), Kalman1D()
        self.speed_ema = 0.0
        self.last_kalman_weight = self.MAX_KALMAN_WEIGHT if use_kalman else 0.0
        self._last_out = (0.0, 0.0)

    def filter(self, x: float, y: float) -> Tuple[float, float]:
        ox, oy = self._one_euro.filter(x, y)
        if not self.use_kalman:  # pure One Euro; speed from output delta
            speed = math.hypot(ox - self._last_out[0], oy - self._last_out[1]) / FRAME_DT
            self._last_out = (ox, oy)
            self.speed_ema += self.EMA_ALPHA * (speed - self.speed_ema)
            self.last_kalman_weight = 0.0
            return (ox, oy)
        kx, ky = self._kx.filter(x, FRAME_DT), self._ky.filter(y, FRAME_DT)
        self.speed_ema += self.EMA_ALPHA * (math.hypot(self._kx.velocity, self._ky.velocity)
                                            - self.speed_ema)
        blend = min(1.0, self.speed_ema / self.SPEED_REF)
        w = self.MAX_KALMAN_WEIGHT + (self.MIN_KALMAN_WEIGHT - self.MAX_KALMAN_WEIGHT) * blend
        self.last_kalman_weight = w
        self._last_out = (w * kx + (1.0 - w) * ox, w * ky + (1.0 - w) * oy)
        return self._last_out

    def reset(self) -> None:
        self._one_euro.reset()
        self._kx.reset(), self._ky.reset()
        self.speed_ema = 0.0
        self.last_kalman_weight = self.MAX_KALMAN_WEIGHT if self.use_kalman else 0.0
        self._last_out = (0.0, 0.0)

# ==== SECTION: adaptive calibration =========================================


class AdaptiveCal:
    """Per-axis adaptive reach calibration for the index fingertip.

    Learns per-axis [min, max] of the raw normalized index-tip position:
    edges expand instantly toward observations (a sample is never clipped by
    its own box) and decay toward the box centre at `decay` per sample so
    stale bounds fade. After `min_samples` (45) samples (is_ready) update()
    remaps onto the box padded with a 10% soft margin (5%/side) and clips to
    [0, 1]; before that it passes through. --no-calibration => enabled=False.
    """

    SOFT_MARGIN = 0.10

    def __init__(self, decay: float = 0.999, min_samples: int = 45, enabled: bool = True) -> None:
        self.decay, self.min_samples, self.enabled = float(decay), int(min_samples), bool(enabled)
        self._box = np.array([[0.5, 0.5], [0.5, 0.5]], dtype=float)
        self._samples = 0

    @property
    def is_ready(self) -> bool:
        return self.enabled and self._samples >= self.min_samples

    @property
    def samples(self) -> int:
        return self._samples

    def update(self, pos: np.ndarray) -> np.ndarray:
        """Fold one observation into the box; return the remapped position."""
        pos = np.asarray(pos, dtype=float).reshape(2)
        self._samples += 1
        for axis in range(2):
            lo, hi = self._box[axis]
            centre = 0.5 * (lo + hi)
            self._box[axis] = (min(centre + (lo - centre) * self.decay, pos[axis]),
                               max(centre + (hi - centre) * self.decay, pos[axis]))
        if not self.is_ready:
            return pos.copy()
        out = np.empty(2)
        for axis in range(2):
            lo, hi = self._box[axis]
            width = hi - lo
            if width <= 1e-9:
                out[axis] = 0.5
                continue
            pad = 0.5 * self.SOFT_MARGIN * width
            out[axis] = float(np.clip((pos[axis] - (lo - pad)) / (width + 2.0 * pad), 0.0, 1.0))
        return out

    def reset(self) -> None:
        self._box = np.array([[0.5, 0.5], [0.5, 0.5]], dtype=float)
        self._samples = 0

# ==== SECTION: pinch-to-zoom ================================================


class PinchZoom:
    """Pinch-hold + vertical hand travel -> accumulated scroll ticks.

    A pinch held >= 0.30 s engages (hysteresis until release; y re-anchors at
    engage so no jump). Engaged: per-frame hand-y delta -> EMA (0.35) ->
    raw = -ema * 60 accumulated; emits clamp(int, +/-6) ticks (remainder
    kept) once the float accumulator passes the 0.015 deadzone — jitter
    dropped, sustained 0.004/frame travel accumulates fine. Hand up
    (y falling) = zoom in = positive ticks.
    """

    def __init__(self, engage_hold: float = 0.30, smoothing: float = 0.35,
                 deadzone: float = 0.015, gain: float = 60.0, max_ticks: int = 6) -> None:
        self.engage_hold, self.smoothing = float(engage_hold), float(smoothing)
        self.deadzone, self.gain, self.max_ticks = float(deadzone), float(gain), int(max_ticks)
        self._engaged = False
        self._start_t: Optional[float] = None
        self._last_y: Optional[float] = None
        self._ema = self._accum = 0.0

    @property
    def active(self) -> bool:
        return self._engaged

    def update(self, pinching: bool, index_y: float, now: float) -> int:
        """Advance one frame; returns ticks emitted this frame (usually 0)."""
        if not pinching:  # release -> full disengage (hysteresis until release)
            self.reset()
            return 0
        if not self._engaged:
            if self._start_t is None:
                self._start_t = float(now)
            elif float(now) - self._start_t >= self.engage_hold:
                self._engaged = True  # engage: re-anchor, no jump
                self._last_y, self._ema = float(index_y), 0.0
            return 0
        delta = float(index_y) - self._last_y
        self._last_y = float(index_y)
        if not math.isfinite(delta):
            delta = 0.0
        self._ema += self.smoothing * (delta - self._ema)
        self._accum += -self._ema * self.gain
        if abs(self._accum) < self.deadzone:
            return 0
        emitted = max(-self.max_ticks, min(self.max_ticks, int(self._accum)))
        self._accum -= emitted  # keep the un-emitted remainder
        return emitted

    def reset(self) -> None:
        self._engaged = False
        self._start_t = self._last_y = None
        self._ema = self._accum = 0.0

# ==== SECTION: gesture recognition (simple heuristics) ======================


def lm_dist(a: Any, b: Any) -> float:
    """Normalized 2-D distance between two MediaPipe landmarks."""
    return math.hypot(a.x - b.x, a.y - b.y)


def fingers_up(lm: Sequence[Any]) -> Tuple[bool, bool, bool, bool, bool]:
    """(thumb, index, middle, ring, pinky) up flags — see module docstring."""
    thumb = lm_dist(lm[4], lm[17]) > 1.2 * lm_dist(lm[2], lm[17])
    return (thumb, lm[8].y < lm[6].y, lm[12].y < lm[10].y,
            lm[16].y < lm[14].y, lm[20].y < lm[18].y)


def classify_gesture(lm: Sequence[Any],
                     ups: Tuple[bool, bool, bool, bool, bool]) -> str:
    """Map landmarks + finger flags to one pose name (or "NONE")."""
    t, i, m, r, p = ups
    if lm_dist(lm[4], lm[8]) < PINCH_DIST:
        return "PINCH"
    count = int(t) + int(i) + int(m) + int(r) + int(p)
    if count >= 4:
        return "PALM"
    if count == 0:
        return "FIST"
    if i and m and r and not p:
        return "THREE"
    if i and m and not r and not p:
        return "PEACE"
    if t and not (i or m or r or p):
        return "THUMB"
    if i and not (m or r or p):
        return "POINT"
    return "NONE"


class GestureState:
    """Mutable per-session gesture/cursor state (was_active bookkeeping)."""

    GESTURES = ("PINCH", "PEACE", "THREE", "PALM", "FIST", "THUMB")

    def __init__(self) -> None:
        self.was: Dict[str, bool] = {g: False for g in self.GESTURES}
        self.gesture = "NONE"
        self.frozen = self.precision = self.dragging = False
        self.last_click_t = 0.0
        self.scroll_anchor: Optional[float] = None
        self.scroll_accum = 0.0
        self.lost = 0
        self.last_sent: Optional[Tuple[float, float]] = None


def reset_gesture_state(state: GestureState, pz: PinchZoom) -> None:
    """Full gesture-state reset (hand lost > 5 frames): drag up, flags off."""
    if state.dragging:
        drag_release()
        state.dragging = False
    for key in state.was:
        state.was[key] = False
    pz.reset()
    state.scroll_anchor, state.scroll_accum = None, 0.0
    state.frozen = False
    state.gesture = "NONE"


def handle_gesture(state: GestureState, gesture: str, tip_y: float, now: float,
                   recorder: MacroRecorder) -> None:
    """Fire enter/exit actions exactly once per gesture transition."""
    was = state.was
    if (gesture == "PINCH" and not was["PINCH"]
            and now - state.last_click_t >= CLICK_COOLDOWN):
        click("left")
        recorder.record("click", button="left")
        state.last_click_t = now
    if gesture == "PEACE" and not was["PEACE"]:
        click("right"); recorder.record("right_click")
    if gesture == "THUMB" and not was["THUMB"]:
        double_click(); recorder.record("double_click")
    if gesture == "FIST" and not was["FIST"]:
        state.frozen = not state.frozen
        print(f"[fist] cursor {'FROZEN' if state.frozen else 'resumed'}")
    if gesture == "PALM" and not was["PALM"]:
        drag_press(); recorder.record("drag_start"); state.dragging = True
    elif was["PALM"] and gesture != "PALM":
        drag_release(); recorder.record("drag_stop"); state.dragging = False
    if gesture == "THREE":
        if state.scroll_anchor is None:
            state.scroll_anchor, state.scroll_accum = tip_y, 0.0
        else:
            state.scroll_accum += (state.scroll_anchor - tip_y) * SCROLL_GAIN
            state.scroll_anchor = tip_y
            steps = int(state.scroll_accum)  # trunc; remainder keeps accumulating
            if steps:
                state.scroll_accum -= steps
                scroll(steps); recorder.record("scroll", amount=steps)
    else:
        state.scroll_anchor = None
    for key in was:
        was[key] = (gesture == key)
    state.gesture = gesture

# ==== SECTION: voice control (optional; guarded speech_recognition) =========


def _import_sr() -> Optional[Any]:
    try:
        import speech_recognition  # optional dependency
        return speech_recognition
    except Exception:
        return None


def _normalize_text(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split())


class VoiceEngine:
    """Background speech-recognition thread -> thread-safe command queue.

    Requires speech_recognition (+ PyAudio); if missing, start() prints one
    notice and the engine stays disabled — never a hard failure.
    """

    PHRASES: Dict[str, str] = {
        "click": "click", "right_click": "right click", "double_click": "double click",
        "scroll_up": "scroll up", "scroll_down": "scroll down", "zoom_in": "zoom in",
        "zoom_out": "zoom out", "freeze": "freeze", "resume": "resume",
        "quit": "quit", "precision": "precision", "calibrate": "calibrate",
    }

    def __init__(self, turbo: bool = False) -> None:
        self.turbo = bool(turbo)
        self.threshold, self.phrase_limit = (0.45, 2.5) if turbo else (0.60, 3.5)
        self._queue: deque = deque(maxlen=8)
        self._lock, self._stop = threading.Lock(), threading.Event()

    def start(self) -> bool:
        """Start the daemon listener; False (with a notice) if sr is missing."""
        sr = _import_sr()
        if sr is None:
            print("[voice] speech_recognition not installed — voice control disabled")
            return False
        threading.Thread(target=self._run, args=(sr,), daemon=True,
                         name="airmouse-voice").start()
        return True

    def _run(self, sr: Any) -> None:
        """Listen loop: Microphone -> recognize_google -> fuzzy match -> queue."""
        try:
            rec = sr.Recognizer()
            rec.energy_threshold, rec.dynamic_energy_threshold = 150, False
            rec.pause_threshold = 0.4
            with sr.Microphone() as source:
                rec.adjust_for_ambient_noise(source, duration=0.5)
                while not self._stop.is_set():
                    try:
                        audio = rec.listen(source, phrase_time_limit=self.phrase_limit)
                        text = rec.recognize_google(audio).lower().strip()
                    except sr.UnknownValueError:
                        continue
                    except Exception:  # RequestError / mic hiccup: back off
                        time.sleep(0.5)
                        continue
                    cmd = self.match(text)
                    if cmd is not None:
                        with self._lock:
                            self._queue.append(cmd)
        except Exception:
            print("[voice] microphone unavailable — voice control disabled")

    def match(self, transcript: str) -> Optional[str]:
        """Fuzzy match: containment (1.0) or difflib ratio >= threshold; longest wins ties."""
        norm = _normalize_text(transcript)
        if not norm:
            return None
        best_cmd, best_key = None, (-1.0, -1)
        for cmd, phrase in self.PHRASES.items():
            p = _normalize_text(phrase)
            score = 1.0 if p in norm else difflib.SequenceMatcher(None, norm, p).ratio()
            if score >= self.threshold and (score, len(p)) > best_key:
                best_cmd, best_key = cmd, (score, len(p))
        return best_cmd

    def poll(self) -> Optional[str]:
        """Pop the oldest queued command, or None."""
        with self._lock:
            return self._queue.popleft() if self._queue else None

    def stop(self) -> None:
        self._stop.set()


def apply_voice_command(cmd: str, state: GestureState, cal: AdaptiveCal,
                        recorder: MacroRecorder) -> bool:
    """Execute one voice command through the same helpers; True = quit."""
    actions = {
        "click": lambda: (click("left"), recorder.record("click", button="left")),
        "right_click": lambda: (click("right"), recorder.record("right_click")),
        "double_click": lambda: (double_click(), recorder.record("double_click")),
        "scroll_up": lambda: (scroll(3), recorder.record("scroll", amount=3)),
        "scroll_down": lambda: (scroll(-3), recorder.record("scroll", amount=-3)),
        "zoom_in": lambda: (zoom_scroll(2), recorder.record("zoom", ticks=2)),
        "zoom_out": lambda: (zoom_scroll(-2), recorder.record("zoom", ticks=-2)),
    }
    if cmd == "quit":
        print("[voice] quit requested")
        return True
    if cmd == "freeze":
        state.frozen = True
    elif cmd == "resume":
        state.frozen = False
    elif cmd == "precision":
        state.precision = not state.precision
        print(f"[voice] precision mode {'on' if state.precision else 'off'}")
    elif cmd == "calibrate":
        cal.reset()
        print("[voice] calibration reset")
    elif cmd in actions:
        actions[cmd]()
    return False

# ==== SECTION: macros =======================================================


def _sanitize_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(name)).strip("_") or "macro"


def macro_path(name: str) -> str:
    return os.path.join(MACRO_DIR, _sanitize_name(name) + ".json")


class MacroRecorder:
    """Collects {"t": seconds-since-start, "event", **params} events in RAM."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self._t0: Optional[float] = None

    @property
    def recording(self) -> bool:
        return self._t0 is not None

    def start(self) -> None:
        self._t0, self.events = time.perf_counter(), []

    def stop(self) -> None:
        self._t0 = None

    def record(self, event: str, **params: Any) -> None:
        """Append {"t", "event", **params} while recording."""
        if self._t0 is not None:
            self.events.append({"t": round(time.perf_counter() - self._t0, 3),
                                "event": event, **params})

    def save(self, name: str) -> str:
        """Atomically write ~/.airmouse/macros/NAME.json; returns path or ''."""
        if not self.events:
            return ""
        path = macro_path(name)
        try:
            os.makedirs(MACRO_DIR, exist_ok=True)
            payload = {"name": _sanitize_name(name),
                       "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "count": len(self.events), "events": self.events}
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1)
            os.replace(tmp, path)
            return path
        except Exception as exc:
            print(f"[macro] save failed: {exc}")
            return ""


def execute_macro_event(ev: Dict[str, Any]) -> None:
    """Replay one macro event through the same lazy mouse helpers."""
    kind = str(ev.get("event", ""))
    if kind == "click":
        click(str(ev.get("button", "left")))
    elif kind == "right_click":
        click("right")
    elif kind == "double_click":
        double_click()
    elif kind == "scroll":
        scroll(int(ev.get("amount", 0)))
    elif kind == "zoom":
        zoom_scroll(int(ev.get("ticks", 0)))
    elif kind == "drag_start":
        drag_press()
    elif kind == "drag_stop":
        drag_release()


def play_macro(name: str) -> bool:
    """Replay a saved macro (sleeping the deltas, capped 5 s per step)."""
    path = macro_path(name)
    if not os.path.isfile(path):
        print(f"[macro] no such macro: {path}")
        return False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            events = json.load(fh).get("events", [])
    except Exception as exc:
        print(f"[macro] load failed: {exc}")
        return False
    print(f"[macro] playing {len(events)} events from {path}")
    prev_t = 0.0
    try:
        for ev in events:
            try:
                t = float(ev.get("t", prev_t))
            except (TypeError, ValueError):
                t = prev_t
            time.sleep(min(max(t - prev_t, 0.0), 5.0))
            prev_t = t
            execute_macro_event(ev)
    except KeyboardInterrupt:
        print("[macro] playback aborted")
        return False
    print("[macro] playback done")
    return True

# ==== SECTION: camera tracker (cv2 + mediapipe live ONLY here) ==============


class CameraTracker:
    """Webcam capture + MediaPipe Hands (classic solutions API).

    cv2 + mediapipe are imported in __init__ — never at module top level —
    so the rest of the file stays importable and testable headless.
    """

    def __init__(self, cam_index: int = 0, window: str = "AirMouse Simple") -> None:
        try:
            import cv2
            import mediapipe as mp
        except Exception as exc:
            raise ImportError(
                f"camera dependencies missing (pip install opencv-python mediapipe): {exc}") from exc
        self._cv2, self._window = cv2, window
        self._cap = cv2.VideoCapture(int(cam_index))
        if not self._cap.isOpened():
            raise RuntimeError(f"cannot open camera {cam_index}")
        self._hands = mp.solutions.hands.Hands(max_num_hands=1, min_detection_confidence=0.6,
                                               min_tracking_confidence=0.5)

    def read(self) -> Tuple[bool, Any]:
        """Grab one mirrored BGR frame."""
        ok, frame = self._cap.read()
        return ok, (self._cv2.flip(frame, 1) if ok else frame)  # selfie view

    def locate(self, frame: Any) -> Tuple[Optional[Any], Optional[Tuple[float, float]]]:
        """Return (landmarks, normalized index-tip (x, y)) or (None, None)."""
        result = self._hands.process(self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB))
        if not result.multi_hand_landmarks:
            return None, None
        lm = result.multi_hand_landmarks[0].landmark
        return lm, (float(lm[8].x), float(lm[8].y))  # landmark 8 = index tip

    def draw(self, frame: Any, tip_px: Optional[Tuple[int, int]],
             lines: Sequence[str]) -> None:
        """Overlay the fingertip crosshair + status lines; show the window."""
        cv2 = self._cv2
        if tip_px is not None:
            cv2.drawMarker(frame, tip_px, (0, 255, 255), cv2.MARKER_CROSS, 24, 2)
            cv2.circle(frame, tip_px, 6, (0, 255, 255), 2)
        for n, text in enumerate(lines):
            cv2.putText(frame, text, (10, 24 + 20 * n), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 255, 0), 1, cv2.LINE_AA)
        cv2.imshow(self._window, frame)

    def wait_key(self) -> int:
        """Pump the GUI event loop; returns the pressed key code."""
        return self._cv2.waitKey(1) & 0xFF

    def close(self) -> None:
        """Release camera, hands, and windows; never raises."""
        try:
            self._cap.release()
            self._hands.close()
            self._cv2.destroyAllWindows()
        except Exception:
            pass

# ==== SECTION: CLI & main loop ==============================================


def detect_screen() -> Optional[Tuple[int, int]]:
    """Auto-detect screen size via tkinter; None when unavailable/headless."""
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        size = (root.winfo_screenwidth(), root.winfo_screenheight())
        root.destroy()
        return size
    except Exception:
        return None


def resolve_screen(args: argparse.Namespace) -> Tuple[int, int]:
    """Explicit flags win; else tkinter auto-detect; else 1920x1080."""
    if args.screen_w is not None or args.screen_h is not None:
        return (args.screen_w if args.screen_w is not None else 1920,
                args.screen_h if args.screen_h is not None else 1080)
    detected = detect_screen()
    return detected if detected is not None else (1920, 1080)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airmouse_simple.py",
        description="AirMouse Simple v5.0.0 — single-file webcam hand-gesture mouse "
                    "(hybrid One Euro + Kalman filter, adaptive calibration, "
                    "pinch-to-zoom, optional voice, macros).")
    parser.add_argument("--cam", type=int, default=0, help="camera index (default: 0)")
    parser.add_argument("--voice", action="store_true",
                        help="enable voice commands (requires speech_recognition)")
    parser.add_argument("--turbo", action="store_true",
                        help="voice turbo mode: looser 0.45 match threshold, shorter 2.5 s phrases")
    parser.add_argument("--no-kalman", action="store_true",
                        help="disable the Kalman channel (pure One Euro filtering)")
    parser.add_argument("--no-calibration", action="store_true",
                        help="disable adaptive reach calibration")
    parser.add_argument("--zoom-off", action="store_true",
                        help="disable pinch-to-zoom scrolling")
    parser.add_argument("--record", metavar="NAME", default=None,
                        help="record a macro to ~/.airmouse/macros/NAME.json this session")
    parser.add_argument("--play", metavar="NAME", default=None,
                        help="replay macro NAME before the live session starts")
    parser.add_argument("--screen-w", type=int, default=None,
                        help="override screen width (auto-detected via tkinter when possible)")
    parser.add_argument("--screen-h", type=int, default=None,
                        help="override screen height")
    return parser


def _banner(args: argparse.Namespace, screen: Tuple[int, int]) -> None:
    voice_mode = "off" if not args.voice else ("turbo" if args.turbo else "normal")
    print(f"AirMouse Simple v{__version__} — single-file webcam hand-gesture mouse")
    print(f"  screen: {screen[0]}x{screen[1]}   camera: {args.cam}   voice: {voice_mode}")
    print(f"  kalman hybrid: {not args.no_kalman}   adaptive calibration: "
          f"{not args.no_calibration}   pinch-zoom: {not args.zoom_off}")
    print(f"  macro play: {args.play or '-'}   record: {args.record or '-'}")


def _status_lines(state: GestureState, flt: HybridFilter2D, pz: PinchZoom,
                  cal: AdaptiveCal, voice: Optional[VoiceEngine]) -> List[str]:
    return [
        f"AirMouse Simple {__version__}  gesture: {state.gesture}"
        f"  {'FROZEN' if state.frozen else ''}{' PRECISION' if state.precision else ''}",
        f"kalman w: {flt.last_kalman_weight:.2f}  speed: {flt.speed_ema:.2f}"
        f"  zoom: {'ON' if pz.active else 'off'}  drag: {'on' if state.dragging else 'off'}",
        f"cal samples: {cal.samples} ({'ready' if cal.is_ready else 'learning'})"
        f"  voice: {'on' if voice is not None else 'off'}  q=quit",
    ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse args, then run the live session (macro playback first if requested)."""
    args = build_parser().parse_args(argv)
    screen_w, screen_h = resolve_screen(args)
    _banner(args, (screen_w, screen_h))
    if args.play:  # --play: replay BEFORE the live session
        play_macro(args.play)
    voice = VoiceEngine(turbo=args.turbo) if args.voice else None
    if voice is not None and not voice.start():  # notice printed if sr missing
        voice = None
    recorder = MacroRecorder()
    if args.record:
        recorder.start()
    try:
        tracker = CameraTracker(args.cam)
    except (ImportError, RuntimeError) as exc:
        print(f"[camera] {exc}")
        if voice is not None:
            voice.stop()
        return 1
    state = GestureState()
    cal = AdaptiveCal(enabled=not args.no_calibration)
    flt = HybridFilter2D(use_kalman=not args.no_kalman)
    pz = PinchZoom()
    print("Running — press q in the 'AirMouse Simple' window (or Ctrl+C) to quit.")
    try:
        while True:
            t0 = time.perf_counter()
            ok, frame = tracker.read()
            if not ok:
                state.lost += 1
                if state.lost == LOST_RESET_FRAMES + 1:
                    reset_gesture_state(state, pz)
                time.sleep(0.02)
                continue
            fh, fw = frame.shape[:2]
            lm, tip = tracker.locate(frame)
            quit_now = False
            if lm is None:
                state.lost += 1
                if state.lost == LOST_RESET_FRAMES + 1:  # hand lost > 5 frames
                    reset_gesture_state(state, pz)
                    print("[hand] lost — gesture state reset")
            else:
                state.lost = 0
                now = time.perf_counter()
                gesture = classify_gesture(lm, fingers_up(lm))
                # cursor: index tip -> calibration -> hybrid filter -> deadzone
                fx, fy = flt.filter(*cal.update(np.array([tip[0], tip[1]])))
                if state.precision:  # shrink to central 50% for fine work
                    fx, fy = 0.5 + (fx - 0.5) * 0.5, 0.5 + (fy - 0.5) * 0.5
                last = state.last_sent
                if last is None or math.hypot(fx - last[0], fy - last[1]) >= CURSOR_DEADZONE:
                    state.last_sent = (fx, fy)
                    if not state.frozen:
                        move_mouse(min(max(int(fx * screen_w), 0), screen_w - 1),
                                   min(max(int(fy * screen_h), 0), screen_h - 1))
                handle_gesture(state, gesture, tip[1], now, recorder)
                if not args.zoom_off:
                    ticks = pz.update(gesture == "PINCH", tip[1], now)
                    if ticks:
                        zoom_scroll(ticks)
                        recorder.record("zoom", ticks=ticks)
            if voice is not None:  # voice poll: drain the queue
                while True:
                    cmd = voice.poll()
                    if cmd is None:
                        break
                    if apply_voice_command(cmd, state, cal, recorder):
                        quit_now = True
            tip_px = (int(tip[0] * fw), int(tip[1] * fh)) if tip is not None else None
            tracker.draw(frame, tip_px, _status_lines(state, flt, pz, cal, voice))
            time.sleep(max(0.0, FRAME_DT - (time.perf_counter() - t0)))  # 30 FPS cap
            if quit_now or tracker.wait_key() == ord("q"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        tracker.close()
        if voice is not None:
            voice.stop()
        if args.record:
            recorder.stop()
            path = recorder.save(args.record)
            if path:
                print(f"[macro] saved {len(recorder.events)} events -> {path}")
    print("AirMouse Simple stopped.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAirMouse Simple stopped.")
        sys.exit(130)
