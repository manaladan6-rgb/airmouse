"""
airmouse.fusion2 — Multimodal Fusion 2.0 (v11.5 §24) + RF/Omnisense
interface preparation (§30).

Fusion 2.0 extends the v10 fusion inputs:

    VOICE + GESTURE + GAZE + KEYBOARD + BROWSER CONTEXT +
    APPLICATION CONTEXT + RECENT ACTION + PERSONAL HISTORY +
    PREDICTION

Each signal contributes a weighted vote toward one intent candidate.
A CONFLICT RESOLVER handles disagreeing modalities: conflicts lower
confidence and force confirmation; destructive actions are NEVER
executed from prediction or conflicting evidence alone (§24 hard rule).

RF preparation (§30) keeps the v10 abstraction honest: new PROTOCOLS
for presence / motion / gesture-classification / direction / range /
velocity that future Wi-Fi CSI or mmWave sensors can implement WITHOUT
touching the core interaction system.  No fake RF capabilities: with
no hardware, the interfaces simply report unavailable.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .interfaces import Intent, IntentType, Modality, ScreenTarget, now_ts

# ─────────────────────────────────────────────────────────────────────────────
# weights (§24) — personal history + prediction weights come from the tuner
# ─────────────────────────────────────────────────────────────────────────────

SIGNAL_WEIGHTS: Dict[str, float] = {
    "voice": 0.30,
    "gesture": 0.25,
    "gaze": 0.25,
    "keyboard": 0.05,
    "browser_context": 0.05,
    "application_context": 0.05,
    "recent_action": 0.05,
    "personal_history": 0.10,
    "prediction": 0.05,
}


class SignalKind(enum.Enum):
    VOICE = "voice"
    GESTURE = "gesture"
    GAZE = "gaze"
    KEYBOARD = "keyboard"
    BROWSER_CONTEXT = "browser_context"
    APPLICATION_CONTEXT = "application_context"
    RECENT_ACTION = "recent_action"
    PERSONAL_HISTORY = "personal_history"
    PREDICTION = "prediction"


@dataclass
class FusionSignal:
    """One modality/context signal (DATA)."""

    kind: SignalKind
    intent: str = ""                    # canonical intent name (e.g. "click")
    target: Optional[ScreenTarget] = None
    confidence: float = 0.0
    detail: str = ""

    @property
    def weight(self) -> float:
        return SIGNAL_WEIGHTS.get(self.kind.value, 0.0)


DESTRUCTIVE_INTENTS = frozenset({
    "close", "close_app", "close_tab", "delete", "file_delete",
    "shutdown", "restart", "sleep", "lock", "system_op",
})


def is_destructive_intent_name(name: str) -> bool:
    n = str(name or "").lower()
    if n in DESTRUCTIVE_INTENTS:
        return True
    return n.startswith(("delete", "remove", "shutdown", "kill", "trash"))


@dataclass
class FusedIntentCandidate:
    """Output of Fusion 2.0 for one tick — an INTENT CANDIDATE (data)."""

    intent: str = ""
    confidence: float = 0.0
    target: Optional[ScreenTarget] = None
    signals: List[FusionSignal] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    explanation: str = ""

    @property
    def executable(self) -> bool:
        """Executable only when: no conflicts, confirmation not required,
        and at least one NON-prediction signal contributed."""
        has_real_signal = any(s.kind is not SignalKind.PREDICTION
                              for s in self.signals)
        return (not self.conflicts and not self.requires_confirmation
                and has_real_signal)


class ConflictResolver:
    """Resolves disagreeing modalities (§24).

    Rules (deterministic):
    * two signals naming different intents with confidence ≥ 0.5 each
      → conflict; fused confidence is scaled down by the disagreement
    * any conflict involving a destructive intent → requires_confirmation
      (and the destructive branch loses unless it is the ONLY signal)
    * prediction never outranks a real observation (PREDICTION ≠ EXECUTION)
    """

    CONFLICT_PENALTY = 0.5

    def resolve(self, signals: List[FusionSignal]) -> Tuple[float, List[str]]:
        conflicts: List[str] = []
        real = [s for s in signals if s.kind is not SignalKind.PREDICTION]
        strong = [s for s in real if s.confidence >= 0.5 and s.intent]
        intents = {s.intent for s in strong}
        if len(intents) > 1:
            names = sorted(intents)
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    conflicts.append(f"{a}≠{b}")
        # destructive involved → always confirm
        if any(is_destructive_intent_name(s.intent) for s in real):
            if len({s.intent for s in strong}) > 1:
                conflicts.append("destructive_conflict")
        conf_scale = 1.0
        if conflicts:
            conf_scale = self.CONFLICT_PENALTY
        return conf_scale, conflicts


# ─────────────────────────────────────────────────────────────────────────────
# fusion engine 2.0
# ─────────────────────────────────────────────────────────────────────────────

class FusionEngine2:
    """Weighted multimodal fusion over all v11.5 signal sources."""

    def __init__(self,
                 weights: Optional[Dict[str, float]] = None,
                 min_confidence: float = 0.45,
                 personal_history_weight: float = 1.0,
                 prediction_weight: float = 1.0) -> None:
        self.weights = dict(SIGNAL_WEIGHTS)
        if weights:
            for k, v in weights.items():
                if k in self.weights and isinstance(v, (int, float)):
                    self.weights[k] = max(0.0, min(1.0, float(v)))
        self.min_confidence = float(min_confidence)
        self.personal_history_weight = max(0.0, min(2.0,
                                                    personal_history_weight))
        self.prediction_weight = max(0.0, min(2.0, prediction_weight))
        self.resolver = ConflictResolver()
        self.last_candidate: Optional[FusedIntentCandidate] = None

    def _w(self, kind: SignalKind) -> float:
        w = self.weights.get(kind.value, 0.0)
        if kind is SignalKind.PERSONAL_HISTORY:
            w *= self.personal_history_weight
        if kind is SignalKind.PREDICTION:
            w *= self.prediction_weight
        return w

    def fuse(self, signals: Sequence[FusionSignal]) -> FusedIntentCandidate:
        """Fuse signals into one explainable candidate.  Pure."""
        sigs = [s for s in (signals or []) if s is not None]
        cand = FusedIntentCandidate(signals=list(sigs))
        if not sigs:
            cand.explanation = "no signals"
            self.last_candidate = cand
            return cand

        # weighted vote per intent name
        votes: Dict[str, float] = {}
        target_votes: Dict[str, Tuple[float, Optional[ScreenTarget]]] = {}
        for s in sigs:
            if not s.intent:
                continue
            w = self._w(s.kind) * max(0.0, min(1.0, s.confidence))
            votes[s.intent] = votes.get(s.intent, 0.0) + w
            if s.target is not None:
                cur = target_votes.get(s.intent, (0.0, None))
                if w > cur[0]:
                    target_votes[s.intent] = (w, s.target)
        if not votes:
            cand.explanation = "no intent-bearing signals"
            self.last_candidate = cand
            return cand

        scale, conflicts = self.resolver.resolve(list(sigs))
        cand.conflicts = conflicts
        total = sum(votes.values()) or 1.0
        best = max(votes.items(), key=lambda kv: (kv[1], kv[0]))
        raw_conf = best[1] / total
        conf = max(0.0, min(1.0, raw_conf * scale))
        cand.intent = best[0]
        cand.confidence = round(conf, 4)
        _, tgt = target_votes.get(best[0], (0.0, None))
        cand.target = tgt
        cand.requires_confirmation = bool(
            conflicts
            or is_destructive_intent_name(cand.intent)
            or conf < self.min_confidence)
        parts = [f"{s.kind.value}:{s.intent}@{s.confidence:.2f}"
                 for s in sigs if s.intent]
        cand.explanation = ("weighted consensus over [" + ", ".join(parts)
                            + "]" + (f"; conflicts: {conflicts}"
                                     if conflicts else ""))
        self.last_candidate = cand
        return cand

    def to_intent(self, cand: FusedIntentCandidate) -> Optional[Intent]:
        """Materialize an executable candidate into a v10 Intent (only
        when policy allows).  Returns None otherwise."""
        if cand is None or not cand.executable:
            return None
        try:
            itype = IntentType(cand.intent)
        except ValueError:
            return None
        return Intent(
            type=itype,
            target=cand.target,
            point=cand.target.center if cand.target is not None else None,
            params={"fusion2": cand.explanation[:200]},
            confidence=cand.confidence,
            sources=Modality.NONE,
            requires_confirmation=cand.requires_confirmation,
        )


# ─────────────────────────────────────────────────────────────────────────────
# §30 RF / Omnisense interface preparation (HONEST — no fake hardware)
# ─────────────────────────────────────────────────────────────────────────────

class RFExtendedProvider:
    """Protocol future RF sensors implement (Wi-Fi CSI, mmWave, …).

    Implementations must be fully local and report ``available() ->
    False`` when the hardware is absent.  The core never depends on
    any of this being present.
    """

    def available(self) -> bool:  # pragma: no cover - protocol
        return False

    def presence(self) -> Optional[Dict[str, Any]]:
        """→ {"present": bool, "confidence": float} or None."""
        return None

    def motion(self) -> Optional[Dict[str, Any]]:
        """→ {"energy": float, "velocity": float} or None."""
        return None

    def gesture_classification(self) -> Optional[Dict[str, Any]]:
        """→ {"label": str, "confidence": float} or None."""
        return None

    def direction(self) -> Optional[Dict[str, Any]]:
        """→ {"azimuth_deg": float, "confidence": float} or None."""
        return None

    def range(self) -> Optional[Dict[str, Any]]:
        """→ {"meters": float, "confidence": float} or None."""
        return None

    def velocity(self) -> Optional[Dict[str, Any]]:
        """→ {"m_per_s": float, "confidence": float} or None."""
        return None


class RFNoHardware(RFExtendedProvider):
    """The honest default: no RF hardware present.  Every query reports
    unavailable — never fabricated data."""

    def available(self) -> bool:
        return False
