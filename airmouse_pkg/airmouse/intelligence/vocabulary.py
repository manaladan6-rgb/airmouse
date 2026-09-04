"""
airmouse.intelligence.vocabulary — personal vocabulary learner (v11.5 §7).

Learns the user's own words: technical terminology, names, project names,
custom words, abbreviations, preferred spellings and capitalization —
plus CORRECTIONS: if speech repeatedly produces ``Hydra Link`` and the
user corrects it to ``HydraLink``, the system learns the preferred
representation and applies it to future transcripts.

All local, bounded, offline, import/export supported.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

MAX_TERMS = 20_000
MAX_CORRECTIONS = 5_000
MAX_TERM_LEN = 64
MAX_TERM_FREQ = 1_000_000

_WORD = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)


@dataclass
class VocabEntry:
    term: str
    frequency: int = 1
    context: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "term": self.term,
            "frequency": self.frequency,
            "context": self.context,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass
class CorrectionEntry:
    raw: str
    preferred: str
    count: int = 1
    last_seen: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "raw": self.raw,
            "preferred": self.preferred,
            "count": self.count,
            "last_seen": self.last_seen,
        }


def _clean(text: str, max_len: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:max_len]


class PersonalVocabulary:
    """Bounded personal vocabulary + correction table."""

    def __init__(self, max_terms: int = MAX_TERMS,
                 max_corrections: int = MAX_CORRECTIONS) -> None:
        self.max_terms = max(10, int(max_terms))
        self.max_corrections = max(10, int(max_corrections))
        self._terms: Dict[str, VocabEntry] = {}
        self._corrections: Dict[str, CorrectionEntry] = {}
        self.enabled = True
        self.learning_paused = False

    @property
    def learning_active(self) -> bool:
        return self.enabled and not self.learning_paused

    def pause_learning(self) -> None:
        self.learning_paused = True

    def resume_learning(self) -> None:
        self.learning_paused = False

    # -- terms ---------------------------------------------------------------

    def learn_term(self, term: str, context: str = "",
                   now: Optional[float] = None) -> Optional[VocabEntry]:
        t = _clean(term, MAX_TERM_LEN)
        if not t or not self.learning_active:
            return None
        t_final = t
        key = t_final.lower()
        now_v = float(now if now is not None else time.time())
        entry = self._terms.get(key)
        if entry is None:
            if len(self._terms) >= self.max_terms:
                self._evict_term()
            if len(self._terms) >= self.max_terms:
                return None
            entry = VocabEntry(term=t_final, frequency=1,
                               context=_clean(context, 64)[:64],
                               first_seen=now_v, last_seen=now_v)
            self._terms[key] = entry
        else:
            entry.frequency = min(MAX_TERM_FREQ, entry.frequency + 1)
            entry.last_seen = now_v
            if context and not entry.context:
                entry.context = _clean(context, 64)
        return entry

    def learn_terms_from_text(self, text: str, min_len: int = 3,
                              now: Optional[float] = None) -> int:
        """Learn capitalized/repeated words from a text sample.

        Deterministic tokenizer; learns lowercase form, keeps the most
        frequent display form.
        """
        n = 0
        for w in _WORD.findall(str(text or ""))[:200]:
            if len(w) >= min_len:
                if self.learn_term(w, now=now) is not None:
                    n += 1
        return n

    def _evict_term(self) -> None:
        if not self._terms:
            return
        victim = min(self._terms.items(),
                     key=lambda kv: (kv[1].frequency, kv[1].last_seen, kv[0]))
        del self._terms[victim[0]]

    def lookup(self, prefix: str, k: int = 5) -> List[VocabEntry]:
        """Personal-vocabulary completions (frequency desc, term asc)."""
        p = _clean(prefix, MAX_TERM_LEN).lower()
        if not p:
            return []
        rows = [e for key, e in self._terms.items() if key.startswith(p)]
        rows.sort(key=lambda e: (-e.frequency, e.term.lower()))
        return rows[:max(0, int(k))]

    def known(self, term: str) -> bool:
        return _clean(term, MAX_TERM_LEN).lower() in self._terms

    def top(self, k: int = 20) -> List[VocabEntry]:
        rows = sorted(self._terms.values(),
                      key=lambda e: (-e.frequency, e.term.lower()))
        return rows[:max(0, int(k))]

    @property
    def size(self) -> int:
        return len(self._terms)

    # -- corrections -------------------------------------------------------------

    def learn_correction(self, raw: str, preferred: str,
                         now: Optional[float] = None) -> Optional[CorrectionEntry]:
        r = _clean(raw, MAX_TERM_LEN)
        p = _clean(preferred, MAX_TERM_LEN)
        if not r or not p or not self.learning_active:
            return None
        if r.lower() == p.lower():
            return None
        key = r.lower()
        now_v = float(now if now is not None else time.time())
        entry = self._corrections.get(key)
        if entry is None:
            if len(self._corrections) >= self.max_corrections:
                self._evict_correction()
            if len(self._corrections) >= self.max_corrections:
                return None
            entry = CorrectionEntry(raw=r, preferred=p, count=1, last_seen=now_v)
            self._corrections[key] = entry
        else:
            entry.count += 1
            entry.preferred = p
            entry.last_seen = now_v
        return entry

    def _evict_correction(self) -> None:
        if not self._corrections:
            return
        victim = min(self._corrections.items(),
                     key=lambda kv: (kv[1].count, kv[1].last_seen, kv[0]))
        del self._corrections[victim[0]]

    def apply_corrections(self, text: str) -> Tuple[str, int]:
        """Apply learned corrections to a transcript (case-insensitive,
        whole-word).  Returns (corrected_text, n_corrections)."""
        if not isinstance(text, str) or not text or not self._corrections:
            return (text or "", 0)
        out = text
        n = 0
        for key in sorted(self._corrections.keys(), key=len, reverse=True):
            entry = self._corrections[key]
            pattern = re.compile(
                r"\b" + re.escape(entry.raw) + r"\b", re.IGNORECASE)
            out, k = pattern.subn(entry.preferred, out)
            n += k
        return (out, n)

    def correction_for(self, raw: str) -> Optional[str]:
        e = self._corrections.get(_clean(raw, MAX_TERM_LEN).lower())
        return e.preferred if e else None

    @property
    def correction_count(self) -> int:
        return len(self._corrections)

    # -- export / import ------------------------------------------------------------

    def export_data(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "kind": "airmouse-personal-vocabulary",
            "terms": [e.to_dict() for e in self.top(self.max_terms)],
            "corrections": [e.to_dict() for e in
                            sorted(self._corrections.values(),
                                   key=lambda e: (-e.count, e.raw.lower()))],
        }

    def export_json(self) -> str:
        return json.dumps(self.export_data(), ensure_ascii=False, sort_keys=True)

    def import_data(self, data: Dict[str, Any]) -> int:
        """Validated import — strings only, bounded lengths/counts."""
        if not isinstance(data, dict) or data.get("kind") not in (
                "airmouse-personal-vocabulary", None):
            return 0
        accepted = 0
        terms = data.get("terms")
        if isinstance(terms, list):
            for row in terms[: self.max_terms]:
                if not isinstance(row, dict):
                    continue
                t = _clean(str(row.get("term", "")), MAX_TERM_LEN)
                if not t or len(t) < 1:
                    continue
                try:
                    freq = max(1, min(MAX_TERM_FREQ, int(row.get("frequency", 1))))
                except Exception:
                    freq = 1
                key = t.lower()
                self._terms[key] = VocabEntry(
                    term=t, frequency=freq,
                    context=_clean(str(row.get("context", "")), 64),
                    first_seen=float(row.get("first_seen", 0.0) or 0.0),
                    last_seen=float(row.get("last_seen", 0.0) or 0.0))
                accepted += 1
        corrections = data.get("corrections")
        if isinstance(corrections, list):
            for row in corrections[: self.max_corrections]:
                if not isinstance(row, dict):
                    continue
                r = _clean(str(row.get("raw", "")), MAX_TERM_LEN)
                p = _clean(str(row.get("preferred", "")), MAX_TERM_LEN)
                if not r or not p or r.lower() == p.lower():
                    continue
                try:
                    cnt = max(1, min(MAX_TERM_FREQ, int(row.get("count", 1))))
                except Exception:
                    cnt = 1
                self._corrections[r.lower()] = CorrectionEntry(
                    raw=r, preferred=p, count=cnt,
                    last_seen=float(row.get("last_seen", 0.0) or 0.0))
                accepted += 1
        return accepted

    def import_json(self, text: str) -> int:
        try:
            data = json.loads(text or "")
        except Exception:
            return 0
        return self.import_data(data)

    # -- persistence -------------------------------------------------------------------

    def save(self, path: str) -> int:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        payload = self.export_json()
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
        return len(payload)

    @classmethod
    def load(cls, path: str) -> "PersonalVocabulary":
        v = cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                v.import_json(f.read())
        except FileNotFoundError:
            pass
        except Exception:
            pass  # corrupted: start empty
        return v

    def reset(self) -> None:
        self._terms.clear()
        self._corrections.clear()
