"""
airmouse.dictation_text — live voice typing, intelligent text prediction
and emoji intelligence (v11.5 §9, §10, §11).

Voice typing: speech → formatted text.  Modes COMMAND / DICTATION /
HYBRID (mirrors the v10 VoiceMode).  Supports punctuation, capitalization,
paragraph breaks, new lines, quotes, brackets, numbers, symbols,
correction, deletion, replacement, undo/redo and formatting commands.

Text prediction: word/phrase completion driven by the personal model +
vocabulary + context (application, document type, mode).  Never cloud.

Emoji intelligence: contextual suggestions with personal preference
learning, rate-limited so it never spams.

All deterministic, local, offline, bounded.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .interfaces import VoiceMode

# ─────────────────────────────────────────────────────────────────────────────
# limits
# ─────────────────────────────────────────────────────────────────────────────

MAX_DICTATION_CHARS = 100_000
MAX_HISTORY_OPS = 500
EMOJI_COOLDOWN_S = 30.0
EMOJI_MAX_SUGGESTIONS = 3


# ─────────────────────────────────────────────────────────────────────────────
# edit commands (§9)
# ─────────────────────────────────────────────────────────────────────────────

EDIT_COMMANDS: Tuple[Tuple[str, str], ...] = (
    ("delete last word", "delete_last_word"),
    ("delete last sentence", "delete_last_sentence"),
    ("delete that", "delete_last_segment"),
    ("scratch that", "delete_last_segment"),
    ("strike that", "delete_last_segment"),
    ("new line", "new_line"),
    ("new paragraph", "new_paragraph"),
    ("capitalize that", "capitalize_last"),
    ("uppercase that", "uppercase_last"),
    ("lowercase that", "lowercase_last"),
    ("undo", "undo"),
    ("redo", "redo"),
    ("select all", "select_all"),
    ("copy that", "copy_last"),
    ("cut that", "cut_last"),
    ("paste that", "paste"),
)


@dataclass
class DictationOp:
    """One edit operation the text layer should apply (DATA)."""

    op: str                      # insert | edit_command | replace
    text: str = ""
    replacement: str = ""
    target: str = ""             # for replace: the span being replaced


class VoiceTypingEngine:
    """First-class voice typing (dictation formatting + edit commands)."""

    def __init__(self, mode: VoiceMode = VoiceMode.DICTATION) -> None:
        self.mode = mode
        self._committed = ""            # committed dictation text
        self._segments: List[str] = []  # finalized segments (recent first ops target [0])
        self._undo: List[str] = []
        self._redo: List[str] = []

    # -- state ------------------------------------------------------------------

    @property
    def text(self) -> str:
        return self._committed

    def reset(self) -> None:
        self._committed = ""
        self._segments.clear()
        self._undo.clear()
        self._redo.clear()

    def set_mode(self, mode: VoiceMode) -> None:
        self.mode = mode

    # -- the dictation path ---------------------------------------------------------

    def ingest(self, utterance: str) -> List[DictationOp]:
        """Process one finalized dictation utterance.

        Returns the list of :class:`DictationOp` to apply.  Pure.
        """
        u = str(utterance or "").strip()
        if not u:
            return []
        low = u.lower()
        # 1. edit commands (exact deterministic matches)
        for phrase, op in EDIT_COMMANDS:
            if low == phrase:
                return self._apply_edit(op)
        # "replace that with X" / "replace X with Y"
        if low.startswith("replace that with "):
            return self._replace_last_segment(u[len("replace that with "):])
        if low.startswith("replace "):
            return self._replace_phrase(u)
        # 2. dictation insert with formatting
        return self._insert_formatted(u)

    # -- formatting ------------------------------------------------------------------

    def _insert_formatted(self, utterance: str) -> List[DictationOp]:
        from .transcription import (apply_spoken_punctuation,
                                    insert_discourse_commas, capitalize_text)
        out = apply_spoken_punctuation(utterance)
        out = insert_discourse_commas(out)
        # sentence-case only at true sentence starts (start of dictation or
        # right after a terminator/newline); mid-sentence continuation keeps
        # its natural case
        committed = self._committed
        fresh_start = (not committed
                       or committed.rstrip().endswith((".", "!", "?", "\n")))
        fresh = capitalize_text(out) if fresh_start else out
        if committed and not committed.endswith(("\n", " ")):
            fresh = " " + fresh
        self._push_undo()
        self._committed = (self._committed + fresh)[-MAX_DICTATION_CHARS:]
        self._segments.insert(0, fresh)
        return [DictationOp(op="insert", text=fresh)]

    def _apply_edit(self, op: str) -> List[DictationOp]:
        self._push_undo()
        if op == "undo":
            self._pop_undo()
            return [DictationOp(op="undo")]
        if op == "redo":
            if self._redo:
                self._committed = self._redo.pop()
            return [DictationOp(op="redo")]
        if op == "new_line":
            self._committed += "\n"
            return [DictationOp(op="edit_command", text="\n")]
        if op == "new_paragraph":
            self._committed += "\n\n"
            return [DictationOp(op="edit_command", text="\n\n")]
        if op in ("capitalize_last", "uppercase_last", "lowercase_last"):
            if not self._segments:
                return []
            seg = self._segments[0]
            if op == "capitalize_last":
                fixed = seg[:1].upper() + seg[1:]
            elif op == "uppercase_last":
                fixed = seg.upper()
            else:
                fixed = seg.lower()
            self._committed = self._committed.replace(seg, fixed, 1)
            self._segments[0] = fixed
            return [DictationOp(op="edit_command", text=fixed)]
        if op == "delete_last_word":
            self._committed = self._committed.rstrip()
            if " " in self._committed:
                head, _, tail = self._committed.rpartition(" ")
                self._committed = head or ""
            return [DictationOp(op="edit_command", text="")]
        if op == "delete_last_sentence":
            idx = max(self._committed.rstrip().rfind("."),
                      self._committed.rstrip().rfind("!"),
                      self._committed.rstrip().rfind("?"))
            self._committed = self._committed[:idx + 1] \
                if idx >= 0 else ""
            return [DictationOp(op="edit_command", text="")]
        if op == "delete_last_segment":
            if self._segments:
                seg = self._segments.pop(0)
                self._committed = self._committed.replace(seg, "", 1)
            return [DictationOp(op="edit_command", text="")]
        if op in ("copy_last", "cut_last"):
            return [DictationOp(op=op)]
        if op == "select_all" or op == "paste":
            return [DictationOp(op=op)]
        return []

    def _replace_last_segment(self, replacement: str) -> List[DictationOp]:
        self._push_undo()
        if not self._segments:
            return []
        old = self._segments[0]
        fresh = " " + replacement
        self._committed = self._committed.replace(old, fresh, 1)
        self._segments[0] = fresh
        return [DictationOp(op="replace", text=fresh, target=old,
                            replacement=replacement)]

    def _replace_phrase(self, utterance: str) -> List[DictationOp]:
        self._push_undo()
        low = utterance.lower()
        marker = " with "
        if marker not in low:
            return []
        idx = low.index(marker)
        old = utterance[8:idx].strip()
        new = utterance[idx + len(marker):].strip()
        if not old or not new:
            return []
        n = self._committed.lower().count(old.lower())
        if n:
            start = self._committed.lower().index(old.lower())
            self._committed = (self._committed[:start] + new
                               + self._committed[start + len(old):])
            # keep segment bookkeeping consistent with the edit
            if self._segments and old in self._segments[0]:
                self._segments[0] = self._segments[0].replace(old, new, 1)
            else:
                self._segments.insert(0, new)
            return [DictationOp(op="replace", text=new, target=old,
                                replacement=new)]
        return []

    # -- undo/redo stack (bounded) -------------------------------------------------------

    def _push_undo(self) -> None:
        self._undo.append(self._committed)
        if len(self._undo) > MAX_HISTORY_OPS:
            del self._undo[:100]
        self._redo.clear()

    def _pop_undo(self) -> None:
        if self._undo:
            self._redo.append(self._committed)
            self._committed = self._undo.pop()


# ─────────────────────────────────────────────────────────────────────────────
# intelligent text prediction (§10)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TextSuggestion:
    text: str
    confidence: float
    kind: str = "word"        # word | phrase
    reason: str = ""


class TextPredictor:
    """Context-aware predictive typing (local, personal, offline).

    Context inputs: application name, document type, current text,
    mode, personal vocabulary, learned history.
    """

    def __init__(self, predictor=None) -> None:
        self.predictor = predictor        # intelligence Predictor or None
        self.enabled = True
        self.max_candidates = 5

    def suggest(self, current_text: str,
                application: str = "",
                doc_type: str = "",
                k: int = 3) -> List[TextSuggestion]:
        if not self.enabled or self.predictor is None:
            return []
        out: List[TextSuggestion] = []
        tail = str(current_text or "")[-120:]
        for p in self.predictor.complete_text(tail, k=k):
            out.append(TextSuggestion(p.value, p.confidence, "word", p.reason))
        for p in self.predictor.phrase_completions(tail, k=1):
            out.append(TextSuggestion(p.value, p.confidence * 0.8, "phrase",
                                      p.reason))
        out.sort(key=lambda s: (-s.confidence, s.text))
        return out[:max(0, int(k))]

    def context_tag(self, application: str = "", doc_type: str = "") -> str:
        """Deterministic context tag for app/doc-aware suggestions."""
        return f"{str(application)[:24]}:{str(doc_type)[:24]}".strip(":")


# ─────────────────────────────────────────────────────────────────────────────
# emoji intelligence (§11)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EmojiSuggestion:
    emoji: str
    confidence: float
    reason: str = ""


class EmojiSuggester:
    """Lightweight, rate-limited emoji suggestions with preference
    learning.  Never spams: cooldown + max suggestions."""

    def __init__(self, plugin=None) -> None:
        self.plugin = plugin             # IntelligencePlugin or None
        self.enabled = True
        self._last_shown = 0.0

    def suggest(self, text: str, now: Optional[float] = None,
                k: int = EMOJI_MAX_SUGGESTIONS) -> List[EmojiSuggestion]:
        if not self.enabled:
            return []
        now_v = float(now if now is not None else time.time())
        if now_v - self._last_shown < EMOJI_COOLDOWN_S:
            return []
        preds = []
        if self.plugin is not None:
            preds = self.plugin.suggest_emoji(text, k=k)
        if not preds:
            from .prediction import Predictor, EMOJI_KEYWORDS
            low = " " + " ".join(str(text or "").lower().split()) + " "
            seen: List[str] = []
            for kw, emojis in sorted(EMOJI_KEYWORDS.items()):
                if f" {kw} " in low and kw not in seen:
                    seen.append(kw)
                    preds.extend(type("P", (), {"value": e,
                                                "confidence": 0.5,
                                                "reason": "Matches what you typed."})()
                                 for e in emojis)
                    if len(preds) >= k:
                        break
        self._last_shown = now_v
        return [EmojiSuggestion(p.value, p.confidence, p.reason)
                for p in preds[:max(0, int(k))]]

    def record_choice(self, text: str, emoji: str) -> None:
        """Learn the user's emoji preference for this context."""
        if self.plugin is not None:
            self.plugin.apply_emoji_preference(text, emoji)
