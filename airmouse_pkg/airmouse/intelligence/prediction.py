"""
airmouse.intelligence.prediction — personal prediction with explainability.

v11.5 §5 (personal prediction) + §39 (explainability).

Every prediction carries:
    kind          what is being predicted (action/command/text/emoji/target)
    value         the predicted value (opaque DATA — never executed)
    confidence    0..1
    reason        concise user-facing rationale ("You usually open VS Code
                  after this action.") — never internal model internals
    alternatives  ranked [(value, confidence)]

HARD RULE (§3/§28): PREDICTION ≠ EXECUTION.  A prediction is a
suggestion for the user/HUD; nothing here may trigger the action engine.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .model import PersonalInteractionModel
from .vocabulary import PersonalVocabulary

MAX_CANDIDATES = 8          # §44: maximum prediction candidates


@dataclass
class Prediction:
    """One explainable prediction (DATA ONLY — never auto-executed)."""

    kind: str = ""                  # action | command | text | emoji | target | workflow
    value: str = ""
    confidence: float = 0.0
    reason: str = ""
    alternatives: List[Tuple[str, float]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def clipped(self) -> "Prediction":
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        self.alternatives = self.alternatives[:MAX_CANDIDATES]
        return self


# deterministic baseline emoji map (personal model refines it over time)
EMOJI_KEYWORDS = {
    "amazing": ("🔥", "😂", "🎉", "❤️"),
    "awesome": ("🔥", "👍", "🎉"),
    "congratulations": ("🎉", "👏", "🚀"),
    "congrats": ("🎉", "👏", "🚀"),
    "tired": ("😩", "😴", "🥲"),
    "sleepy": ("😴", "🥱"),
    "love": ("❤️", "😍", "🥰"),
    "happy": ("😊", "😄", "🥳"),
    "sad": ("😢", "😭", "💔"),
    "funny": ("😂", "🤣"),
    "lol": ("😂", "🤣"),
    "haha": ("😂", "😅"),
    "thanks": ("🙏", "😊"),
    "thank you": ("🙏", "😊"),
    "sorry": ("😔", "🙏"),
    "fire": ("🔥",),
    "cool": ("😎", "👍"),
    "great": ("👍", "🎉", "🔥"),
    "good job": ("👏", "👍"),
    "well done": ("👏", "🎉"),
    "wow": ("😮", "🤩"),
    "wow ": ("🤩",),
    "party": ("🥳", "🎉"),
    "birthday": ("🎂", "🥳"),
    "coffee": ("☕",),
    "food": ("🍕", "🍔"),
    "lunch": ("🍽️", "🍕"),
    "work": ("💼", "💻"),
    "code": ("💻", "👨‍💻"),
    "bug": ("🐛",),
    "fixed": ("✅", "🔧"),
    "broken": ("💥", "😭"),
    "done": ("✅", "🎉"),
    "finished": ("✅", "🎉"),
    "deadline": ("⏰", "😰"),
    "urgent": ("🚨", "⚡"),
    "meeting": ("📅", "🤝"),
    "call": ("📞",),
    "question": ("❓",),
    "idea": ("💡",),
    "note": ("📝",),
    "star": ("⭐",),
    "rocket": ("🚀",),
    "launch": ("🚀",),
    "study": ("📚", "✏️"),
    "exam": ("📝", "😫"),
    "good morning": ("☀️", "👋"),
    "good night": ("🌙", "😴"),
    "ok": ("👌", "👍"),
    "yes": ("👍", "✅"),
    "no": ("👎", "❌"),
    "please": ("🙏",),
}


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


class Predictor:
    """Facade over the personal model producing explainable predictions."""

    def __init__(self,
                 model: Optional[PersonalInteractionModel] = None,
                 vocabulary: Optional[PersonalVocabulary] = None) -> None:
        self.model = model
        self.vocabulary = vocabulary
        self.enabled = True
        self.last_observations: dict = {}

    # -- actions ----------------------------------------------------------------

    def predict_next_action(self, history: Sequence[str],
                            min_confidence: float = 0.0) -> Prediction:
        """Predict the likely next action from the recent action history."""
        if not self.enabled or self.model is None:
            return Prediction(kind="action").clipped()
        cands = self.model.actions.predict_next(history or [], k=MAX_CANDIDATES)
        if not cands:
            return Prediction(kind="action").clipped()
        top_a, top_p = cands[0]
        if top_p < min_confidence:
            return Prediction(kind="action").clipped()
        n = self.model.actions.steps
        return Prediction(
            kind="action",
            value=str(top_a),
            confidence=_clamp(top_p),
            reason=(f"You often follow this with {top_a} "
                    f"({n} actions observed)."),
            alternatives=[(str(a), _clamp(p)) for a, p in cands[1:]],
        ).clipped()

    # -- commands -----------------------------------------------------------------

    def predict_command(self, hour: Optional[int] = None,
                        min_confidence: float = 0.0) -> Prediction:
        """Predict the likely next command (overall + time-of-day habit)."""
        if not self.enabled or self.model is None:
            return Prediction(kind="command").clipped()
        cands: List[Tuple[str, float]] = []
        reason = ""
        if hour is not None:
            hour_cands = self.model.commands.frequent_at_hour(int(hour), k=3)
            if hour_cands and hour_cands[0][1] >= 0.34:
                cands = hour_cands
                reason = f"You often run this around {int(hour):02d}:00."
        if not cands:
            cands = self.model.commands.top(k=MAX_CANDIDATES)
            reason = "It is one of your most used commands."
        if not cands:
            return Prediction(kind="command").clipped()
        if cands[0][1] < min_confidence:
            return Prediction(kind="command").clipped()
        return Prediction(
            kind="command",
            value=str(cands[0][0]),
            confidence=_clamp(cands[0][1]),
            reason=reason,
            alternatives=[(str(c), _clamp(p)) for c, p in cands[1:]],
        ).clipped()

    # -- text completion ------------------------------------------------------------

    def complete_text(self, prefix: str, k: int = 3) -> List[Prediction]:
        """Context-aware word completion (personal n-gram + vocabulary)."""
        if not self.enabled or self.model is None:
            return []
        out: List[Prediction] = []
        seen = set()
        for w, p in self.model.ngram.complete(prefix, k=k + 2):
            if w in seen:
                continue
            seen.add(w)
            reason = "Based on how you usually write."
            if self.vocabulary is not None:
                hits = self.vocabulary.lookup(w, k=1)
                if hits and hits[0].frequency > 2:
                    reason = "One of your personal terms."
            out.append(Prediction(kind="text", value=w, confidence=_clamp(p),
                                  reason=reason).clipped())
            if len(out) >= k:
                break
        return out

    def phrase_completions(self, prefix: str, k: int = 3) -> List[Prediction]:
        """Phrase-level completion via repeated trigram continuation."""
        if not self.enabled or self.model is None:
            return []
        out: List[Prediction] = []
        cands = self.model.ngram.predict_next(prefix, k=k + 2)
        for w, p in cands:
            out.append(Prediction(
                kind="text", value=w, confidence=_clamp(p) * 0.9,
                reason="You often continue phrases this way.").clipped())
            if len(out) >= k:
                break
        return out

    # -- emoji -----------------------------------------------------------------------

    def suggest_emoji(self, text: str, k: int = 3) -> List[Prediction]:
        """Contextual emoji suggestions: personal preference first,
        deterministic keyword baseline second.  Never spammy: ≤ k items."""
        if not self.enabled:
            return []
        low = " " + " ".join(str(text or "").lower().split()) + " "
        personal: List[Tuple[str, float]] = []
        if self.model is not None:
            for tag in self._match_tags(low):
                personal.extend(self.model.emoji.suggest(tag, k=k))
        results: List[Tuple[str, float]] = []
        seen = set()
        for e, p in sorted(personal, key=lambda t: (-t[1], t[0])):
            if e not in seen:
                seen.add(e)
                results.append((e, 0.55 + 0.4 * p))  # personal boost
        if len(results) < k:
            for kw, emojis in sorted(EMOJI_KEYWORDS.items()):
                if f" {kw} " in low or f" {kw}." in low or f" {kw}?" in low:
                    for e in emojis:
                        if e not in seen:
                            seen.add(e)
                            results.append((e, 0.5))
                        if len(results) >= k:
                            break
                if len(results) >= k:
                    break
        return [Prediction(kind="emoji", value=e, confidence=_clamp(p),
                           reason=("You often use this emoji here."
                                   if p > 0.6 else
                                   "Matches what you typed."))
                for e, p in results[:max(0, k)]]

    @staticmethod
    def _match_tags(low: str) -> List[str]:
        tags = []
        for kw in EMOJI_KEYWORDS:
            if f" {kw} " in low or f" {kw}." in low or f" {kw}?" in low:
                tags.append(kw)
        return tags

    # -- targets ------------------------------------------------------------------------

    def predict_target(self, recent_targets: Sequence[str],
                       k: int = 3) -> Prediction:
        """Predict the likely next target from recent target history."""
        if not self.enabled or self.model is None or not recent_targets:
            return Prediction(kind="target").clipped()
        last = str(recent_targets[-1])[:64]
        cands = self.model.actions.predict_next([f"target:{last}"], k=k)
        if not cands:
            return Prediction(kind="target").clipped()
        return Prediction(
            kind="target",
            value=str(cands[0][0]).replace("target:", ""),
            confidence=_clamp(cands[0][1]),
            reason="You often interact with this next.",
            alternatives=[(str(a).replace("target:", ""), _clamp(p))
                          for a, p in cands[1:]],
        ).clipped()
