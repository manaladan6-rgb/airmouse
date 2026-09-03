"""
Zoom v5.0 — Pinch-to-Zoom: pinch & move to zoom (frontier gesture).

A tiny state machine that turns a pinch gesture into scroll-wheel zoom
ticks:

1. When the pinch starts, a hold timer begins.
2. Only after the pinch has been held for ``engage_hold`` seconds does
   the controller ENGAGE (hysteresis: once engaged it stays engaged
   until the pinch is released, so a brief jitter never drops zoom).
3. While engaged, the per-frame vertical delta of the index fingertip
   drives zoom: moving the hand UP zooms IN (positive ticks).  The
   delta is EMA-smoothed, converted to ticks, passed through a
   per-frame deadzone (which kills sub-threshold finger jitter while
   sustained slow movement still accumulates), and fed into a float
   accumulator.  Whole ticks are emitted each frame (clamped to
   ``max_ticks_per_frame``) and subtracted from the accumulator — the
   remainder is kept, so slow movements still zoom eventually.

:class:`zoom_scroll` performs the OS-level action: hold Ctrl and turn
the mouse wheel via pynput (lazily imported; falls back to a plain
mouse scroll if the keyboard side is unavailable).  It never raises.

Classes:
    PinchZoomController — pinch-hold + vertical-motion -> zoom ticks
    ZoomDirection       — readable constants IN / OUT

Functions:
    zoom_scroll — OS-level Ctrl+wheel zoom, fully guarded
"""

from __future__ import annotations

import math


class ZoomDirection:
    """Readable constants for zoom direction (sign convention of ticks)."""

    IN = 1
    OUT = -1


class PinchZoomController:
    """Converts a held pinch + vertical hand motion into zoom ticks."""

    def __init__(self, engage_hold: float = 0.30, deadzone: float = 0.015,
                 gain: float = 1.0, max_ticks_per_frame: int = 6,
                 smoothing: float = 0.35):
        """
        Args:
            engage_hold: seconds the pinch must be held before zoom
                engages (prevents accidental zooms during quick pinches).
            deadzone: per-frame deadzone in *ticks*; contributions smaller
                than this are dropped, so finger jitter never emits ticks
                while deliberate movement accumulates across frames.
            gain: multiplier on the vertical delta (ticks per unit of
                motion, scaled by 60 to feel 1:1 at ~60 fps).
            max_ticks_per_frame: clamp for ticks emitted in one frame.
            smoothing: EMA factor applied to the per-frame delta
                (0 = no smoothing, 1 = frozen).
        """
        self.engage_hold = max(0.0, float(engage_hold))
        self.deadzone = max(0.0, float(deadzone))
        self.gain = float(gain)
        self.max_ticks_per_frame = max(1, int(max_ticks_per_frame))
        self.smoothing = min(max(float(smoothing), 0.0), 1.0)
        self.reset()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        """True while zoom is engaged (pinch held past the hold time)."""
        return self._engaged

    def update(self, pinching: bool, index_y: float, now: float) -> int:
        """Feed one frame; returns the zoom ticks emitted this frame.

        Args:
            pinching: whether the thumb+index pinch is currently held.
            index_y: normalized vertical position of the index fingertip
                (image coordinates: smaller = higher = zoom in).
            now: monotonic timestamp in seconds (e.g. time.perf_counter()).

        Returns:
            Integer ticks in [-max_ticks_per_frame, +max_ticks_per_frame];
            positive = zoom in, negative = zoom out, 0 when idle.
        """
        try:
            y = float(index_y)
            t = float(now)
        except (TypeError, ValueError):
            return 0
        if not (math.isfinite(y) and math.isfinite(t)):
            return 0  # ignore garbage frames rather than teleporting the zoom

        if not pinching:
            if self._pinching:
                # pinch released — leave zoom mode, drop any remainder
                self._pinching = False
                self._engaged = False
                self._ema = 0.0
                self._accum = 0.0
                self._has_last = False
            return 0

        if not self._pinching:
            # pinch just started: begin the hold timer
            self._pinching = True
            self._engaged = False
            self._start_time = t
            self._last_y = y
            self._has_last = True
            self._ema = 0.0
            self._accum = 0.0
            return 0

        if not self._engaged:
            if (t - self._start_time) >= self.engage_hold:
                # engage (hysteresis: stays on until the pinch is released)
                self._engaged = True
                self._last_y = y      # re-anchor: no jump from the hold period
                self._ema = 0.0
                self._accum = 0.0
            else:
                self._last_y = y
                return 0

        if not self._has_last:
            self._last_y = y
            self._has_last = True
            return 0

        # --- engaged: vertical motion drives zoom -----------------------
        d = y - self._last_y
        self._last_y = y
        # EMA smoothing absorbs finger jitter before it becomes ticks
        self._ema = self.smoothing * d + (1.0 - self.smoothing) * self._ema
        # hand UP (y decreasing) = zoom IN (positive); 60 ~ ticks/sec feel
        raw = -self._ema * self.gain * 60.0
        if abs(raw) < self.deadzone:
            raw = 0.0  # per-frame deadzone: sub-threshold jitter is dropped
        self._accum += raw
        emit = int(self._accum)  # trunc toward zero
        emit = max(-self.max_ticks_per_frame, min(self.max_ticks_per_frame, emit))
        self._accum -= emit  # keep the remainder so slow motion still zooms
        return emit

    def reset(self) -> None:
        """Clear all pinch/zoom state (call on tracking loss)."""
        self._pinching = False
        self._engaged = False
        self._start_time = 0.0
        self._last_y = 0.0
        self._has_last = False
        self._ema = 0.0
        self._accum = 0.0


def zoom_scroll(ticks: int) -> None:
    """Perform an OS-level zoom via Ctrl + mouse-wheel scroll.

    Lazy-imports pynput (keyboard + mouse controllers).  Holds Ctrl
    (falling back to right Ctrl) while scrolling ``ticks`` wheel
    notches; if the keyboard side is unavailable for any reason, falls
    back to a plain mouse-only scroll.  This function never raises.
    """
    try:
        ticks = int(ticks)
    except (TypeError, ValueError):
        return
    if ticks == 0:
        return

    kb = None
    ctrl_key = None
    try:  # lazy import — pynput may be missing in headless/test environments
        from pynput.keyboard import Controller as KeyboardController, Key
        kb = KeyboardController()
        for key in (Key.ctrl, Key.ctrl_r):
            try:
                kb.press(key)
                ctrl_key = key
                break
            except Exception:
                continue
    except Exception:
        kb = None

    try:
        from pynput.mouse import Controller as MouseController
        MouseController().scroll(0, ticks)
    except Exception:
        pass

    if kb is not None and ctrl_key is not None:
        try:
            kb.release(ctrl_key)
        except Exception:
            pass


if __name__ == "__main__":
    # Mini-demo: simulate a pinch -> hold -> move hand up -> release
    # sequence at 30 fps.  Pure logic, no camera required.
    pz = PinchZoomController()
    now = 0.0
    total = 0
    was_engaged = False
    print("PinchZoom demo (simulated @30fps): pinch at t=0.20s, "
          "move hand up from t=0.67s, release at t=2.00s")
    for i in range(75):
        now += 1.0 / 30.0
        pinching = 5 <= i < 60
        if i < 20:
            y = 0.5
        else:
            y = 0.5 - (i - 20) * 0.006  # steady upward hand motion
        ticks = pz.update(pinching, y, now)
        total += ticks
        was_engaged = was_engaged or pz.active
        if ticks:
            print(f"  t={now:5.2f}s  ticks={ticks:+d}  (total {total:+d})")
    print(f"zoom engaged during demo: {was_engaged}   total ticks: {total:+d}")
    print(f"ZoomDirection.IN={ZoomDirection.IN}  ZoomDirection.OUT={ZoomDirection.OUT}")
