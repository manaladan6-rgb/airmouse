"""
airmouse.world_model — lightweight Interaction World Model (v11.5 §13)
plus the §14 contextual command intelligence.

The world model is a BOUNDED snapshot of "where the user is right now":

    CURRENT APPLICATION / WINDOW / VISIBLE TARGETS / CURRENT GAZE TARGET
    / CURRENT TEXT FIELD / RECENT ACTION / RECENT COMMAND / CURRENT MODE
    / LIKELY INTENT / CONFIDENCE

It is NOT a giant autonomous world model — it wraps the v10
ContextEngine, adds a recent-command ring, the text-field marker and an
explainable likely-intent estimate powered by the optional intelligence
plugin.

The §14 contextual resolver maps the deictic command families
("click that", "open that", "close it", "copy that", "read this",
"zoom that", "scroll there", "select this", "use that", "go there",
"open this", "save that") onto structured Intents, resolved through
gaze / accessibility tree / DOM / recent target / recent action /
current application / current window.  LOW CONFIDENCE → ASK, never
guess dangerous actions (§14 hard rule).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .interfaces import (AppContext, Intent, IntentType, Modality,
                         ScreenTarget, now_ts)

MAX_VISIBLE_TARGETS = 64
MAX_RECENT = 16
MAX_TEXT_FIELD_LEN = 120


# ─────────────────────────────────────────────────────────────────────────────
# world model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorldState:
    """Bounded world snapshot (§13)."""

    application: str = ""
    window: str = ""
    visible_targets: List[ScreenTarget] = field(default_factory=list)
    gaze_target: Optional[ScreenTarget] = None
    text_field: str = ""                 # marker of the focused text field
    recent_action: str = ""
    recent_command: str = ""
    mode: str = "hand"
    likely_intent: str = ""
    likely_intent_confidence: float = 0.0
    likely_intent_reason: str = ""
    confidence: float = 0.0
    timestamp: float = field(default_factory=now_ts)

    def to_display(self) -> str:
        """Human-readable summary (used by HUD/CLI)."""
        tgt = self.gaze_target.text if self.gaze_target else "—"
        return (f"Application: {self.application or '—'}\n"
                f"Window:      {self.window or '—'}\n"
                f"Target:      {tgt}\n"
                f"Recent:      {self.recent_action or '—'}\n"
                f"Next likely: {self.likely_intent or '—'} "
                f"({self.likely_intent_confidence:.0%})")


class WorldModel:
    """Bounded interaction world model wrapping the v10 ContextEngine."""

    def __init__(self, context_engine=None, intelligence=None) -> None:
        self.context = context_engine          # v10 ContextEngine or None
        self.intelligence = intelligence        # IntelligencePlugin or None
        self.enabled = True
        self._recent_commands: List[str] = []
        self._text_field = ""
        self._recent_actions: List[str] = []

    # -- updates ---------------------------------------------------------------

    def update_text_field(self, marker: str) -> None:
        self._text_field = str(marker or "")[:MAX_TEXT_FIELD_LEN]

    def record_command(self, command: str) -> None:
        c = str(command or "")[:64]
        if c:
            self._recent_commands.append(c)
            self._recent_commands = self._recent_commands[-MAX_RECENT:]

    def record_action(self, action: str) -> None:
        a = str(action or "")[:64]
        if a:
            self._recent_actions.append(a)
            self._recent_actions = self._recent_actions[-MAX_RECENT:]

    # -- snapshot ------------------------------------------------------------------

    def snapshot(self) -> WorldState:
        st = WorldState()
        cs = None
        if self.context is not None:
            try:
                cs = self.context.snapshot()
            except Exception:
                cs = None
        if cs is not None:
            st.application = cs.focused_application
            st.window = cs.focused_window
            st.gaze_target = cs.current_gaze_target
            st.recent_action = cs.recent_action
            st.mode = cs.active_mode
            targets = list(cs.browser_targets or [])[:MAX_VISIBLE_TARGETS]
            st.visible_targets = targets
        else:
            st.visible_targets = []
        st.text_field = self._text_field
        st.recent_command = self._recent_commands[-1] if self._recent_commands else ""
        # likely intent from the optional intelligence layer
        li = self.likely_intent()
        if li is not None:
            st.likely_intent = li.get("intent", "")
            st.likely_intent_confidence = float(li.get("confidence", 0.0))
            st.likely_intent_reason = str(li.get("reason", ""))
            st.confidence = st.likely_intent_confidence
        return st

    def likely_intent(self) -> Optional[Dict[str, Any]]:
        """Explainable likely-next-intent estimate (DATA ONLY).

        Combines the predictor's next-action with the current context.
        Never executes anything (§3: prediction ≠ permission).
        """
        if not self.enabled or self.intelligence is None \
                or not getattr(self.intelligence, "available", False):
            return None
        try:
            hist = [a for a in self._recent_actions[-3:]]
            pred = self.intelligence.predict_next_action(hist)
            if pred is None or not pred.value:
                return None
            # destructiveness never surfaces as a likely intent
            from .intelligence.workflows import is_destructive_action
            if is_destructive_action(pred.value):
                return None
            return {"intent": pred.value,
                    "confidence": pred.confidence,
                    "reason": pred.reason}
        except Exception:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# §14 contextual command intelligence
# ─────────────────────────────────────────────────────────────────────────────

# utterance → (IntentType, needs_target, sensitivity)
CONTEXTUAL_COMMANDS: Dict[str, Tuple[IntentType, bool, str]] = {
    "click that": (IntentType.CLICK, True, "safe"),
    "click this": (IntentType.CLICK, True, "safe"),
    "click it": (IntentType.CLICK, True, "safe"),
    "open that": (IntentType.OPEN, True, "safe"),
    "open this": (IntentType.OPEN, True, "safe"),
    "close it": (IntentType.CLOSE, True, "sensitive"),
    "close that": (IntentType.CLOSE, True, "sensitive"),
    "copy that": (IntentType.COPY, False, "safe"),
    "read this": (IntentType.SELECT, True, "safe"),      # select for reading
    "zoom that": (IntentType.ZOOM, True, "safe"),
    "scroll there": (IntentType.SCROLL, True, "safe"),
    "select this": (IntentType.SELECT, True, "safe"),
    "use that": (IntentType.CLICK, True, "safe"),
    "go there": (IntentType.NAVIGATE, True, "safe"),
    "save that": (IntentType.HOTKEY, False, "safe"),     # ctrl+s
}

_AMBIGUOUS = ("that", "this", "it", "there", "here")


class ContextualCommandResolver:
    """Resolves deictic commands against the world model.

    Confidence model (deterministic):
        gaze target present       +0.45
        selection present         +0.30
        recent target present     +0.20
        window reference          +0.25
        base by reference kind
    Below ``min_confidence`` → returns a low-confidence intent flagged
    ``needs_confirmation`` (the assistant ASKS, it does not guess).
    """

    def __init__(self, world_model: Optional[WorldModel] = None,
                 context_engine=None,
                 min_confidence: float = 0.4) -> None:
        self.world = world_model
        self.context = context_engine or (world_model.context
                                          if world_model else None)
        self.min_confidence = float(min_confidence)

    def resolve(self, utterance: str) -> Optional[Intent]:
        """Resolve one contextual utterance into an Intent (or None)."""
        u = str(utterance or "").strip().lower()
        if u not in CONTEXTUAL_COMMANDS:
            return None
        itype, needs_target, sensitivity = CONTEXTUAL_COMMANDS[u]
        now = now_ts()
        confidence = 0.15
        target: Optional[ScreenTarget] = None

        # 1. direct reference word in the utterance
        ref = next((w for w in u.split() if w in _AMBIGUOUS), "")
        cs = None
        if self.context is not None:
            try:
                cs = self.context.snapshot()
            except Exception:
                cs = None
        if ref and cs is not None:
            try:
                target = cs.resolve_reference(ref)
            except Exception:
                target = None

        # 2. confidence by what the reference resolved through
        if target is not None:
            src = getattr(target, "source", "")
            if src == "gaze":
                confidence = 0.85
            elif src == "selection":
                confidence = 0.75
            elif src == "context":
                confidence = 0.55
            else:
                confidence = 0.5
        elif needs_target:
            confidence = 0.2        # nothing to act on → ASK, don't guess

        if not needs_target:
            # targetless ops (copy/save) are actionable as-is
            confidence = max(confidence, 0.6)

        intent = Intent(
            type=itype,
            target=target,
            point=target.center if target is not None else None,
            params={"utterance": u,
                    "sensitivity": sensitivity,
                    "hotkey": ("ctrl", "s") if u == "save that" else None},
            confidence=round(confidence, 3),
            sources=Modality.VOICE,
            utterance=u,
            requires_confirmation=(sensitivity == "sensitive"
                                   or confidence < self.min_confidence),
            timestamp=now,
        )
        return intent

    @staticmethod
    def supported_commands() -> List[str]:
        return sorted(CONTEXTUAL_COMMANDS.keys())
