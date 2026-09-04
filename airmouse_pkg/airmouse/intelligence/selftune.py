"""
airmouse.intelligence.selftune — bounded self-tuning of interaction
thresholds (v11.5 §5 Self-tuning + §25/§26/§27 adaptive personalization).

The SelfTuner LEARNS optimal:
    gesture thresholds        (confirm frames / amplitude gates)
    dwell timing              (gaze dwell seconds)
    confidence thresholds     (voice command acceptance)
    speech confidence handling
    correction behavior       (auto-apply after N consistent corrections)
    personalization weights

SAFETY CONTRACT: every tunable has a HARD [min, max] band and a minimum
sample count before any adjustment is proposed.  The tuner PROPOSES
bounded adjustments; applying them is a deliberate, logged act by the
plugin facade (never a silent jump).  Users can turn learning OFF and
reset to defaults at any time.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# name -> (default, min, max, min_samples, step)
TUNABLES: Dict[str, Tuple[float, float, float, int, float]] = {
    "gesture_confirm_frames": (5.0, 2.0, 10.0, 40, 1.0),
    "gesture_transition_cooldown": (0.15, 0.05, 0.5, 40, 0.01),
    "gaze_dwell_time": (0.8, 0.3, 2.5, 30, 0.05),
    "voice_command_min_confidence": (0.75, 0.5, 0.95, 30, 0.02),
    "speech_confidence_scale": (1.0, 0.6, 1.4, 50, 0.02),
    "correction_auto_apply": (0.0, 0.0, 10.0, 10, 1.0),   # N consistent corrections
    "prediction_weight": (1.0, 0.0, 2.0, 60, 0.05),
    "history_weight": (1.0, 0.0, 2.0, 60, 0.05),
    "gesture_amplitude_gate": (0.02, 0.005, 0.08, 50, 0.002),
    "swipe_speed_gate": (0.35, 0.1, 1.2, 50, 0.02),
}

_EMA_ALPHA = 0.2


@dataclass
class TunableStat:
    value: float = 0.0
    ema: float = 0.0
    ema_var: float = 0.0
    success_ema: float = 0.5
    samples: int = 0


class SelfTuner:
    """Bounded, explainable threshold learner."""

    def __init__(self) -> None:
        self.current: Dict[str, float] = {k: v[0] for k, v in TUNABLES.items()}
        self.defaults: Dict[str, float] = {k: v[0] for k, v in TUNABLES.items()}
        self._stats: Dict[str, TunableStat] = {}
        self.enabled = True
        self.proposals_applied = 0
        self.last_proposal_time = 0.0

    # -- observation ----------------------------------------------------------

    def observe(self, name: str, value: float, success: bool) -> None:
        """Observe one interaction sample for a tunable.

        ``value``  the raw measurement (e.g. dwell seconds used)
        ``success`` whether the interaction worked for the user
        """
        if not self.enabled or name not in TUNABLES:
            return
        v = float(value)
        if v != v or v in (float("inf"), float("-inf")):
            return
        st = self._stats.get(name)
        if st is None:
            st = TunableStat()
            self._stats[name] = st
        if st.samples == 0:
            st.ema = v
            st.ema_var = 0.0
        else:
            d = v - st.ema
            st.ema += _EMA_ALPHA * d
            st.ema_var = (1 - _EMA_ALPHA) * (st.ema_var + _EMA_ALPHA * d * d)
        st.value = v
        st.success_ema = ((1 - _EMA_ALPHA) * st.success_ema
                          + _EMA_ALPHA * (1.0 if success else 0.0))
        st.samples += 1

    # -- bounded proposal ---------------------------------------------------------

    def propose(self, name: str) -> Optional[Tuple[float, str]]:
        """Propose a bounded adjustment for one tunable.

        Returns (new_value, reason) or None.  Adjustment direction is
        driven by observed success rate around the current value:
          success < 0.4  → move toward the sample mean (user struggles)
          success > 0.85 → tighten slightly toward faster interaction
        """
        if not self.enabled or name not in TUNABLES:
            return None
        default, lo, hi, min_samples, step = TUNABLES[name]
        st = self._stats.get(name)
        if st is None or st.samples < min_samples:
            return None
        cur = self.current.get(name, default)
        new = cur
        reason = ""
        if st.success_ema < 0.4:
            # adapt toward what the user actually does
            target = min(hi, max(lo, st.ema))
            if abs(target - cur) >= step:
                direction = 1.0 if target > cur else -1.0
                new = cur + direction * step
                reason = (f"Low success rate ({st.success_ema:.0%}); "
                          f"adapting toward your natural {name}.")
        elif st.success_ema > 0.85:
            # things work: tighten conservatively for speed
            tighter = cur - step
            if tighter >= lo:
                new = tighter
                reason = (f"Consistently successful ({st.success_ema:.0%}); "
                          f"tightening {name} for responsiveness.")
        new = max(lo, min(hi, new))
        if abs(new - cur) < 1e-9:
            return None
        return (round(new, 4), reason)

    def propose_all(self) -> Dict[str, Tuple[float, str]]:
        out = {}
        for name in TUNABLES:
            p = self.propose(name)
            if p is not None:
                out[name] = p
        return out

    def apply(self, name: str, value: float) -> bool:
        """Apply a bounded adjustment (the only mutation path; logged)."""
        if name not in TUNABLES:
            return False
        default, lo, hi, _ms, _s = TUNABLES[name]
        v = float(value)
        if v != v or v in (float("inf"), float("-inf")):
            return False
        self.current[name] = max(lo, min(hi, v))
        self.proposals_applied += 1
        self.last_proposal_time = time.time()
        return True

    # -- lifecycle --------------------------------------------------------------------

    def reset(self) -> None:
        self.current = dict(self.defaults)
        self._stats.clear()
        self.proposals_applied = 0

    # -- persistence ---------------------------------------------------------------------

    def export_data(self) -> Dict[str, object]:
        return {
            "version": 1,
            "kind": "airmouse-selftune",
            "current": dict(self.current),
            "proposals_applied": self.proposals_applied,
        }

    def import_data(self, data: Dict[str, object]) -> int:
        if not isinstance(data, dict) or data.get("kind") != "airmouse-selftune":
            return 0
        cur = data.get("current")
        if not isinstance(cur, dict):
            return 0
        n = 0
        for k, v in cur.items():
            if k in TUNABLES and isinstance(v, (int, float)):
                if self.apply(k, float(v)):
                    n += 1
        return n

    def stats(self) -> Dict[str, Dict[str, float]]:
        return {k: {"samples": s.samples, "ema": round(s.ema, 4),
                    "success_ema": round(s.success_ema, 3)}
                for k, s in self._stats.items()}
