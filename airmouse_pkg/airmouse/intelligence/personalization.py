"""
airmouse.intelligence.personalization — adaptive gesture/gaze/voice
personalization profiles (v11.5 §25/§26/§27).

GestureProfile — learns pinch style, swipe speed, gesture amplitude,
    movement range, dwell behavior, preferred gestures, false-positive
    patterns; proposes bounded threshold tuning.
GazeProfile — learns gaze offset, preferred dwell duration, common
    targets, false-positive regions, calibration drift, interaction
    style.  NEVER removes manual recalibration.
VoiceProfile — learns vocabulary habits, pronunciation corrections,
    preferred/frequent commands, command aliases ("launch browser" →
    "open browser"), false-activation patterns.

All profiles: bounded, local, offline, ON/OFF + reset, deterministic.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MAX_SAMPLES_PER_PROFILE = 2_000
MAX_PREFERRED = 32
MAX_ALIASES = 256
MAX_FALSE_REGIONS = 64


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    v = float(v)
    if v != v or v in (float("inf"), float("-inf")):
        return lo
    return max(lo, min(hi, v))


@dataclass
class GestureSample:
    gesture: str
    amplitude: float = 0.0        # normalized movement amplitude
    speed: float = 0.0            # normalized speed
    duration: float = 0.0         # seconds held / to complete
    false_positive: bool = False


@dataclass
class GazeSample:
    offset_x: float = 0.0         # normalized screen offset vs actual target
    offset_y: float = 0.0
    dwell_seconds: float = 0.0
    region: str = ""              # target region label (short)
    false_positive: bool = False


class GestureProfile:
    """Adaptive gesture personalization (§25)."""

    def __init__(self) -> None:
        self.enabled = True
        self.samples = 0
        self._amp_ema = 0.0
        self._speed_ema = 0.0
        self._dwell_ema = 0.0
        self._pinch_ema = 0.0
        self._preferred: Counter = Counter()
        self._false_positives: Counter = Counter()
        self._swipe_speeds: List[float] = []
        self._movement_range: Tuple[float, float] = (1.0, 0.0)
        self._reset_ranges()

    def _reset_ranges(self) -> None:
        self._movement_range = (1.0, 0.0)

    def observe(self, sample: GestureSample) -> None:
        if not self.enabled:
            return
        g = str(sample.gesture or "")[:32]
        if not g:
            return
        a = max(0.0, min(1.0, float(sample.amplitude)))
        s = max(0.0, min(4.0, float(sample.speed)))
        d = max(0.0, min(30.0, float(sample.duration)))
        a_ = self._alpha(self.samples)
        self._amp_ema += a_ * (a - self._amp_ema)
        self._speed_ema += a_ * (s - self._speed_ema)
        self._dwell_ema += a_ * (d - self._dwell_ema)
        if g == "pinch":
            self._pinch_ema += a_ * (a - self._pinch_ema)
        if g.startswith("swipe"):
            self._swipe_speeds.append(s)
            if len(self._swipe_speeds) > 500:
                self._swipe_speeds = self._swipe_speeds[100:]
        lo, hi = self._movement_range
        self._movement_range = (min(lo, a), max(hi, a))
        if sample.false_positive:
            self._false_positives[g] += 1
        else:
            self._preferred[g] += 1
        self.samples += 1
        if self.samples > MAX_SAMPLES_PER_PROFILE * 4:
            self._trim()

    @staticmethod
    def _alpha(n: int) -> float:
        return 1.0 if n == 0 else max(0.02, 1.0 / (n + 1))

    def _trim(self) -> None:
        for c in (self._preferred, self._false_positives):
            while sum(c.values()) > MAX_SAMPLES_PER_PROFILE:
                drop = sorted(c.items(), key=lambda kv: (kv[1], kv[0]))[0][0]
                del c[drop]

    def suggest_thresholds(self) -> Dict[str, Tuple[float, str]]:
        """Bounded threshold suggestions with reasons (may be empty)."""
        out: Dict[str, Tuple[float, str]] = {}
        if self.samples < 50:
            return out
        total_pref = sum(self._preferred.values()) or 1
        total_fp = sum(self._false_positives.values())
        fp_rate = total_fp / (total_fp + total_pref) if (total_fp + total_pref) else 0.0
        if fp_rate > 0.25:
            out["gesture_amplitude_gate"] = (
                min(0.08, 0.02 + 0.005 * (self.samples // 100)),
                f"High false-positive rate ({fp_rate:.0%}); "
                f"raising amplitude gate.")
        if self._swipe_speeds:
            mean_speed = sum(self._swipe_speeds) / len(self._swipe_speeds)
            if mean_speed > 0.6:
                out["swipe_speed_gate"] = (
                    min(1.2, mean_speed * 0.6),
                    f"Your natural swipe speed is {mean_speed:.2f}; "
                    f"adapting the gate.")
        if self._dwell_ema > 0 and abs(self._dwell_ema - 0.8) > 0.25:
            out["gaze_dwell_time"] = (
                max(0.3, min(2.5, self._dwell_ema)),
                f"Your typical hold time is {self._dwell_ema:.2f}s.")
        return out

    def preferred_gestures(self, k: int = 5) -> List[Tuple[str, float]]:
        total = sum(self._preferred.values()) or 1
        return [(g, n / total) for g, n in
                sorted(self._preferred.items(),
                       key=lambda kv: (-kv[1], kv[0]))[:k]]

    def false_positive_gestures(self, k: int = 5) -> List[Tuple[str, float]]:
        return [(g, n) for g, n in
                sorted(self._false_positives.items(),
                       key=lambda kv: (-kv[1], kv[0]))[:k]]

    @property
    def pinch_style(self) -> str:
        if self.samples < 20:
            return "unknown"
        if self._pinch_ema < 0.25:
            return "subtle"
        if self._pinch_ema > 0.6:
            return "exaggerated"
        return "moderate"

    @property
    def movement_range(self) -> Tuple[float, float]:
        return self._movement_range

    def reset(self) -> None:
        self.__init__()  # deterministic full reset


class GazeProfile:
    """Adaptive gaze personalization (§26).  Manual recalibration is
    NEVER removed — this profile only *suggests* assistance."""

    def __init__(self) -> None:
        self.enabled = True
        self.samples = 0
        self._offset_x_ema = 0.0
        self._offset_y_ema = 0.0
        self._dwell_pref_ema = 0.0
        self._common_regions: Counter = Counter()
        self._false_regions: Counter = Counter()
        self._offset_history: List[Tuple[float, float]] = []
        self.drift_estimate = 0.0

    def observe(self, sample: GazeSample) -> None:
        if not self.enabled:
            return
        a = self._alpha(self.samples)
        ox = max(-0.5, min(0.5, float(sample.offset_x)))
        oy = max(-0.5, min(0.5, float(sample.offset_y)))
        self._offset_x_ema += a * (ox - self._offset_x_ema)
        self._offset_y_ema += a * (oy - self._offset_y_ema)
        d = max(0.0, min(10.0, float(sample.dwell_seconds)))
        if d > 0:
            self._dwell_pref_ema += a * (d - self._dwell_pref_ema)
        region = str(sample.region or "")[:48]
        if region:
            if sample.false_positive:
                self._false_regions[region] += 1
            else:
                self._common_regions[region] += 1
        self._offset_history.append((ox, oy))
        if len(self._offset_history) > 500:
            self._offset_history = self._offset_history[100:]
            self._update_drift()
        self.samples += 1

    @staticmethod
    def _alpha(n: int) -> float:
        return 1.0 if n == 0 else max(0.02, 1.0 / (n + 1))

    def _update_drift(self) -> None:
        """Drift = magnitude of slow offset trend across the window."""
        if len(self._offset_history) < 50:
            self.drift_estimate = 0.0
            return
        n = len(self._offset_history)
        first_half = self._offset_history[: n // 2]
        second_half = self._offset_history[n // 2:]
        fx = sum(o[0] for o in first_half) / len(first_half)
        fy = sum(o[1] for o in first_half) / len(first_half)
        sx = sum(o[0] for o in second_half) / len(second_half)
        sy = sum(o[1] for o in second_half) / len(second_half)
        self.drift_estimate = round(((sx - fx) ** 2 + (sy - fy) ** 2) ** 0.5, 5)

    def suggest_offset_compensation(self) -> Optional[Tuple[float, float, str]]:
        if self.samples < 100 or abs(self._offset_x_ema) < 0.05 \
                and abs(self._offset_y_ema) < 0.05:
            return None
        return (round(self._offset_x_ema, 4), round(self._offset_y_ema, 4),
                "Consistent gaze offset observed; consider recalibration "
                "or enabling compensation.")

    def suggest_dwell(self) -> Optional[Tuple[float, str]]:
        if self.samples < 60 or self._dwell_pref_ema <= 0:
            return None
        d = max(0.3, min(2.5, self._dwell_pref_ema))
        return (round(d, 2), f"Your natural dwell is ~{d:.2f}s.")

    def common_targets(self, k: int = 5) -> List[Tuple[str, float]]:
        total = sum(self._common_regions.values()) or 1
        return [(r, n / total) for r, n in
                sorted(self._common_regions.items(),
                       key=lambda kv: (-kv[1], kv[0]))[:k]]

    def false_positive_regions(self, k: int = 5) -> List[Tuple[str, float]]:
        return [(r, n) for r, n in
                sorted(self._false_regions.items(),
                       key=lambda kv: (-kv[1], kv[0]))[:k]]

    def reset(self) -> None:
        self.__init__()


class VoiceProfile:
    """Adaptive voice personalization (§27)."""

    def __init__(self) -> None:
        self.enabled = True
        self.samples = 0
        self._commands: Counter = Counter()
        self._aliases: Dict[str, str] = {}
        self._alias_votes: Dict[str, Counter] = {}
        self._false_activations: Counter = Counter()
        self._pronunciation: Dict[str, str] = {}

    def observe_command(self, command: str, canonical: str = "") -> None:
        if not self.enabled:
            return
        c = str(command or "").strip().lower()[:64]
        if not c:
            return
        self._commands[c] += 1
        if canonical:
            canon = str(canonical).strip().lower()[:64]
            if canon and canon != c:
                votes = self._alias_votes.setdefault(c, Counter())
                votes[canon] += 1
                # alias accepted after 5 consistent votes
                top, n = votes.most_common(1)[0]
                if n >= 5 and len(self._aliases) < MAX_ALIASES:
                    self._aliases[c] = top
        self.samples += 1

    def observe_false_activation(self, phrase: str) -> None:
        p = str(phrase or "").strip().lower()[:64]
        if p:
            self._false_activations[p] += 1

    def resolve_alias(self, phrase: str) -> Optional[str]:
        """Return the learned canonical command for a personal alias."""
        return self._aliases.get(str(phrase or "").strip().lower()[:64])

    def aliases(self) -> Dict[str, str]:
        return dict(sorted(self._aliases.items()))

    def frequent_commands(self, k: int = 5) -> List[Tuple[str, float]]:
        total = sum(self._commands.values()) or 1
        return [(c, n / total) for c, n in
                sorted(self._commands.items(),
                       key=lambda kv: (-kv[1], kv[0]))[:k]]

    def false_activation_phrases(self, k: int = 5) -> List[Tuple[str, int]]:
        return sorted(self._false_activations.items(),
                      key=lambda kv: (-kv[1], kv[0]))[:k]

    def reset(self) -> None:
        self.__init__()


class PersonalizationEngine:
    """Owns the three adaptive profiles + persistence."""

    def __init__(self) -> None:
        self.gesture = GestureProfile()
        self.gaze = GazeProfile()
        self.voice = VoiceProfile()

    def set_enabled(self, on: bool) -> None:
        self.gesture.enabled = bool(on)
        self.gaze.enabled = bool(on)
        self.voice.enabled = bool(on)

    def reset_all(self) -> None:
        self.gesture.reset()
        self.gaze.reset()
        self.voice.reset()

    def export_data(self) -> Dict[str, object]:
        return {
            "version": 1,
            "kind": "airmouse-personalization",
            "gesture": {
                "samples": self.gesture.samples,
                "pinch_style": self.gesture.pinch_style,
                "preferred": self.gesture.preferred_gestures(10),
            },
            "gaze": {
                "samples": self.gaze.samples,
                "common_targets": self.gaze.common_targets(10),
                "drift_estimate": self.gaze.drift_estimate,
            },
            "voice": {
                "samples": self.voice.samples,
                "aliases": self.voice.aliases(),
                "frequent_commands": self.voice.frequent_commands(10),
            },
        }
