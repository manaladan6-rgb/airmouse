"""
airmouse.intelligence.model — the compact local Personal Interaction Model.

v11.5 §5: the first production version of a ~30 MB-class compact local
model for INTERACTION intelligence (not a chatbot).  The model is:

* local + CPU-friendly (pure stdlib, no BLAS, O(1) lookups on hot paths)
* quantized where appropriate (counts capped, probabilities quantized
  to 8 bits in the packed artifact)
* versioned + replaceable (magic + format version in every artifact)
* deterministic (sorted serialization, deterministic tie-breaks)
* bounded (hard capacity budget with deterministic pruning)
* independently testable (pure data structures, no I/O required)

Architecture: the intelligence of the system comes from
MODEL + MEMORY + CONTEXT + RULES + FUSION + WORLD MODEL + VERIFICATION
+ LEARNING — this module provides the MODEL part only:

    NGramModel      — personal language statistics (text completion)
    ActionMarkov    — personal action-sequence statistics (next action)
    CommandModel    — personal command frequency + time-of-day habits
    EmojiModel      — personal emoji usage per context tag
    FeatureWeights  — personalization weights (bounded, quantized)

packed together by :class:`PersonalInteractionModel`.

The model does NOT ship pre-trained weights: it is a PERSONAL model that
learns on-device from the user's own interactions and grows toward the
capacity budget (default ~30 MB) as they use the system.  Fresh installs
start at a few KB.  This is honestly documented in the guides.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import io
import os
import struct
import time
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import (
    MODEL_CAPACITY_BYTES_DEFAULT,
    MODEL_FORMAT_MAGIC,
    MODEL_FORMAT_VERSION,
)

# ─────────────────────────────────────────────────────────────────────────────
# limits (hard, deterministic — §44)
# ─────────────────────────────────────────────────────────────────────────────

MAX_VOCAB_TERMS = 200_000          # distinct words tracked by the n-gram LM
MAX_NGRAM_ENTRIES = 1_500_000      # (context, word) pairs across all orders
MAX_ACTION_TYPES = 512             # distinct action symbols in the Markov
MAX_EMOJI_ENTRIES = 4_096          # (tag, emoji) pairs
MAX_FEATURES = 256                 # personalization weight slots
COUNT_CAP = 65_535                 # per-entry count cap (u16 storage)
_PRUNE_KEEP_FRACTION = 0.75        # keep top 75% of mass when pruning

_WORD_MAX_LEN = 64                # tokenizer safety bound


class ModelError(Exception):
    """Raised for corrupt/incompatible model artifacts."""


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """Deterministic lowercase word tokenizer (stdlib regex-free)."""
    if not isinstance(text, str) or not text:
        return []
    out: List[str] = []
    buf = []
    for ch in text[:20000].lower():
        if ch.isalnum() or ch in ("'", "-", "_"):
            buf.append(ch)
        else:
            if buf:
                w = "".join(buf)[:_WORD_MAX_LEN]
                if w.strip("'-_"):
                    out.append(w)
                buf = []
    if buf:
        w = "".join(buf)[:_WORD_MAX_LEN]
        if w.strip("-_'"):
            out.append(w)
    return out


def _quant8(p: float) -> int:
    """Quantize a 0..1 probability to 8 bits (deterministic)."""
    if not (p == p):  # NaN
        return 0
    return max(0, min(255, int(round(max(0.0, min(1.0, p)) * 255))))


def _dequant8(q: int) -> float:
    return q / 255.0


def _pack_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<H", len(b)) + b


class _Reader:
    """Tiny deterministic binary reader for the packed artifact."""

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.buf):
            raise ModelError("truncated artifact")
        b = self.buf[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def s(self) -> str:
        return self.take(self.u16()).decode("utf-8")

    def done(self) -> bool:
        return self.pos >= len(self.buf)


# ─────────────────────────────────────────────────────────────────────────────
# NGramModel — personal language statistics
# ─────────────────────────────────────────────────────────────────────────────

class NGramModel:
    """Bounded word-level n-gram LM (order 3, backoff) with capped counts.

    Used for word/phrase completion and dictation stabilization.
    Deterministic: predictions are ordered by (count desc, word asc).
    """

    ORDER = 3

    def __init__(self) -> None:
        # context tuple (0..2 words) -> Counter(next word) -> count
        self._counts: Dict[Tuple[str, ...], Counter] = {}
        self._total_words = 0
        self._entry_count = 0  # total (context, word) pairs

    # -- learning -------------------------------------------------------------

    def observe(self, text: str) -> int:
        """Learn from a text sample.  Returns tokens learned."""
        words = _tokenize(text)
        n = 0
        for i, w in enumerate(words):
            for order in (0, 1, 2):
                ctx = tuple(words[max(0, i - order):i])
                self._bump(ctx, w)
            n += 1
        self._total_words += n
        self._maybe_prune()
        return n

    def observe_tokens(self, words: Sequence[str]) -> int:
        n = 0
        for i, w in enumerate(words):
            wl = str(w).lower()[:_WORD_MAX_LEN]
            for order in (0, 1, 2):
                ctx = tuple(x.lower()[:_WORD_MAX_LEN]
                            for x in words[max(0, i - order):i])
                self._bump(ctx, wl)
            n += 1
        self._total_words += n
        self._maybe_prune()
        return n

    def _bump(self, ctx: Tuple[str, ...], word: str) -> None:
        c = self._counts.get(ctx)
        if c is None:
            if len(self._counts) >= MAX_NGRAM_ENTRIES:
                return  # hard bound
            c = Counter()
            self._counts[ctx] = c
        if c[word] >= COUNT_CAP and word in c:
            c[word] = min(COUNT_CAP, c[word] + 1)  # saturate
        else:
            c[word] += 1
            self._entry_count += 1

    def _maybe_prune(self) -> None:
        if self._entry_count <= MAX_NGRAM_ENTRIES:
            return
        self.prune()

    def prune(self) -> int:
        """Deterministically prune lowest-count entries.  Returns removed."""
        # rank contexts by total count
        totals = [(ctx, sum(c.values())) for ctx, c in self._counts.items()]
        totals.sort(key=lambda t: (-t[1], t[0]))
        keep_target = int(len(totals) * _PRUNE_KEEP_FRACTION) + 1
        removed = 0
        new_counts: Dict[Tuple[str, ...], Counter] = {}
        new_entries = 0
        for ctx, _tot in totals[:keep_target]:
            c = self._counts[ctx]
            new_counts[ctx] = c
            new_entries += len(c)
        removed = self._entry_count - new_entries
        self._counts = new_counts
        self._entry_count = new_entries
        return removed

    # -- inference ------------------------------------------------------------

    def _candidate(self, ctx: Tuple[str, ...]) -> Optional[Counter]:
        return self._counts.get(ctx)

    def predict_next(self, context: Sequence[str], k: int = 5
                     ) -> List[Tuple[str, float]]:
        """Backoff prediction of the next word.  Deterministic."""
        words = [str(w).lower()[:_WORD_MAX_LEN] for w in (context or [])]
        cands: List[Tuple[str, float]] = []
        for order in (2, 1, 0):
            ctx = tuple(words[-order:]) if order else ()
            c = self._candidate(ctx)
            if not c:
                continue
            total = sum(c.values())
            if total <= 0:
                continue
            items = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
            cands = [(w, cnt / total) for w, cnt in items]
            break
        return cands[:max(0, k)]

    def complete(self, prefix: str, k: int = 5) -> List[Tuple[str, float]]:
        """Word completion given the text so far."""
        return self.predict_next(_tokenize(prefix), k=k)

    def score_sequence(self, words: Sequence[str]) -> float:
        """Mean backoff log-probability proxy (higher = more familiar)."""
        toks = _tokenize(" ".join(words))
        if not toks:
            return 0.0
        s = 0.0
        for i, w in enumerate(toks):
            p = 0.0
            for order in (2, 1, 0):
                ctx = tuple(toks[max(0, i - order):i])
                c = self._candidate(ctx)
                if c:
                    total = sum(c.values())
                    if total > 0:
                        p = c.get(w, 0) / total
                        if p > 0:
                            break
                        p = 0.0
            s += p
        return s / len(toks)

    # -- introspection ---------------------------------------------------------

    @property
    def total_words(self) -> int:
        return self._total_words

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def vocab_size(self) -> int:
        c = self._counts.get(())
        return len(c) if c else 0

    # -- serialization (deterministic packed binary) ---------------------------

    def bytes_estimate(self) -> int:
        return 8 + self._entry_count * 12 + len(self._counts) * 4

    def to_stream(self, out: io.BufferedIOBase) -> None:
        contexts = sorted(self._counts.keys())
        out.write(struct.pack("<IQ", 3, self._total_words))
        out.write(struct.pack("<I", len(contexts)))
        for ctx in contexts:
            c = self._counts[ctx]
            out.write(struct.pack("<B", len(ctx)))
            for w in ctx:
                out.write(_pack_str(w))
            items = sorted(c.items())
            out.write(struct.pack("<I", len(items)))
            for w, cnt in items:
                out.write(_pack_str(w))
                out.write(struct.pack("<H", min(COUNT_CAP, cnt)))

    def from_stream(self, r: _Reader) -> None:
        order = r.u32()
        if order != 3:
            raise ModelError(f"unsupported ngram order {order}")
        self._total_words = r.u64()
        n_ctx = r.u32()
        if n_ctx > MAX_NGRAM_ENTRIES:
            raise ModelError("ngram context count exceeds bounds")
        self._counts = {}
        self._entry_count = 0
        for _ in range(n_ctx):
            ctx_len = r.u8()
            ctx = tuple(r.s() for _ in range(ctx_len))
            n_items = r.u32()
            c = Counter()
            for _ in range(n_items):
                w = r.s()
                cnt = r.u16()
                c[w] = cnt
                self._entry_count += 1
            self._counts[ctx] = c


# ─────────────────────────────────────────────────────────────────────────────
# ActionMarkov — personal action-sequence statistics
# ─────────────────────────────────────────────────────────────────────────────

class ActionMarkov:
    """2nd-order action-transition model with 1st-order backoff.

    Actions are opaque short symbols (canonical action names from the
    v10 vocabulary, e.g. ``"click"``, ``"type"``, ``"open_app"``).
    Deterministic: candidates ordered by (count desc, symbol asc).
    """

    def __init__(self) -> None:
        self._start: Counter = Counter()          # first action counts
        self._t1: Dict[str, Counter] = {}          # prev -> next
        self._t2: Dict[Tuple[str, str], Counter] = {}  # (prev2, prev1) -> next
        self._steps = 0

    def observe_step(self, prev: Sequence[str], cur: str) -> None:
        cur = str(cur)[:64]
        if not cur:
            return
        hist = [str(a)[:64] for a in (prev or [])][-2:]
        if not hist:
            self._start[cur] = min(COUNT_CAP, self._start[cur] + 1)
        elif len(hist) == 1:
            c = self._t1.setdefault(hist[0], Counter())
            c[cur] = min(COUNT_CAP, c[cur] + 1)
        else:
            key = (hist[0], hist[1])
            c = self._t2.setdefault(key, Counter())
            c[cur] = min(COUNT_CAP, c[cur] + 1)
            c1 = self._t1.setdefault(hist[1], Counter())
            c1[cur] = min(COUNT_CAP, c1[cur] + 1)
        self._steps += 1
        self._maybe_prune()

    def observe_sequence(self, seq: Sequence[str]) -> None:
        prev: List[str] = []
        for a in seq:
            self.observe_step(prev, a)
            prev.append(a)

    def _maybe_prune(self) -> None:
        if len(self._t2) <= MAX_ACTION_TYPES * 16:
            return
        # deterministic prune of lowest-mass 2nd-order rows
        rows = sorted(self._t2.items(),
                      key=lambda kv: (-sum(kv[1].values()), kv[0]))
        keep = int(len(rows) * _PRUNE_KEEP_FRACTION) + 1
        self._t2 = dict(rows[:keep])

    def predict_next(self, history: Sequence[str], k: int = 3
                     ) -> List[Tuple[str, float]]:
        hist = [str(a)[:64] for a in (history or [])][-2:]
        for order in (2, 1):
            if len(hist) >= order:
                if order == 2:
                    c = self._t2.get((hist[0], hist[1]))
                else:
                    c = self._t1.get(hist[0])
                if c:
                    total = sum(c.values())
                    if total > 0:
                        items = sorted(c.items(),
                                       key=lambda kv: (-kv[1], kv[0]))[:k]
                        return [(a, n / total) for a, n in items]
        if self._start:
            total = sum(self._start.values())
            if total > 0:
                items = sorted(self._start.items(),
                               key=lambda kv: (-kv[1], kv[0]))[:k]
                return [(a, n / total) for a, n in items]
        return []

    @property
    def steps(self) -> int:
        return self._steps

    def bytes_estimate(self) -> int:
        return 16 + len(self._t1) * 24 + len(self._t2) * 32 + len(self._start) * 12

    def to_stream(self, out: io.BufferedIOBase) -> None:
        out.write(struct.pack("<Q", self._steps))
        out.write(struct.pack("<I", len(self._start)))
        for a, n in sorted(self._start.items()):
            out.write(_pack_str(a))
            out.write(struct.pack("<H", n))
        out.write(struct.pack("<I", len(self._t1)))
        for prev, c in sorted(self._t1.items()):
            out.write(_pack_str(prev))
            out.write(struct.pack("<I", len(c)))
            for nxt, n in sorted(c.items()):
                out.write(_pack_str(nxt))
                out.write(struct.pack("<H", n))
        out.write(struct.pack("<I", len(self._t2)))
        for (p2, p1), c in sorted(self._t2.items()):
            out.write(_pack_str(p2))
            out.write(_pack_str(p1))
            out.write(struct.pack("<I", len(c)))
            for nxt, n in sorted(c.items()):
                out.write(_pack_str(nxt))
                out.write(struct.pack("<H", n))

    def from_stream(self, r: _Reader) -> None:
        self._steps = r.u64()
        self._start = Counter()
        self._t1 = {}
        self._t2 = {}
        n = r.u32()
        if n > MAX_ACTION_TYPES:
            raise ModelError("action start count exceeds bounds")
        for _ in range(n):
            a = r.s()
            self._start[a] = r.u16()
        n = r.u32()
        if n > MAX_ACTION_TYPES:
            raise ModelError("action t1 rows exceed bounds")
        for _ in range(n):
            prev = r.s()
            m = r.u32()
            c = Counter()
            for _ in range(m):
                nxt = r.s()
                c[nxt] = r.u16()
            self._t1[prev] = c
        n = r.u32()
        if n > MAX_ACTION_TYPES * 16:
            raise ModelError("action t2 rows exceed bounds")
        for _ in range(n):
            p2 = r.s()
            p1 = r.s()
            m = r.u32()
            c = Counter()
            for _ in range(m):
                nxt = r.s()
                c[nxt] = r.u16()
            self._t2[(p2, p1)] = c


# ─────────────────────────────────────────────────────────────────────────────
# CommandModel — personal command habits
# ─────────────────────────────────────────────────────────────────────────────

class CommandModel:
    """Command frequency + 12-bin (2h) time-of-day habit histogram."""

    HOUR_BINS = 12

    def __init__(self) -> None:
        self._counts: Counter = Counter()
        self._hours: Dict[str, List[int]] = {}

    def observe(self, command: str, hour: Optional[int] = None) -> None:
        c = str(command)[:64]
        if not c:
            return
        self._counts[c] = min(COUNT_CAP, self._counts[c] + 1)
        if hour is not None and isinstance(hour, int) and 0 <= hour <= 23:
            h = self._hours.setdefault(c, [0] * self.HOUR_BINS)
            h[hour // 2] = min(COUNT_CAP, h[hour // 2] + 1)
        if len(self._counts) > MAX_ACTION_TYPES:
            self._prune()

    def _prune(self) -> None:
        items = sorted(self._counts.items(), key=lambda kv: (-kv[1], kv[0]))
        keep = int(len(items) * _PRUNE_KEEP_FRACTION) + 1
        self._counts = Counter(dict(items[:keep]))
        self._hours = {k: v for k, v in self._hours.items() if k in self._counts}

    def top(self, k: int = 5) -> List[Tuple[str, float]]:
        total = sum(self._counts.values()) or 1
        items = sorted(self._counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(c, n / total) for c, n in items[:max(0, k)]]

    def frequent_at_hour(self, hour: int, k: int = 3) -> List[Tuple[str, float]]:
        if not (0 <= hour <= 23):
            return []
        b = hour // 2
        rows = []
        for c, h in self._hours.items():
            if h[b] > 0:
                rows.append((c, h[b]))
        rows.sort(key=lambda t: (-t[1], t[0]))
        total = sum(n for _, n in rows) or 1
        return [(c, n / total) for c, n in rows[:max(0, k)]]

    def count(self, command: str) -> int:
        return self._counts.get(str(command)[:64], 0)

    @property
    def distinct(self) -> int:
        return len(self._counts)

    def bytes_estimate(self) -> int:
        return 8 + len(self._counts) * 30

    def to_stream(self, out: io.BufferedIOBase) -> None:
        out.write(struct.pack("<I", len(self._counts)))
        for c, n in sorted(self._counts.items()):
            out.write(_pack_str(c))
            out.write(struct.pack("<H", n))
            h = self._hours.get(c) or [0] * self.HOUR_BINS
            out.write(struct.pack("<12H", *[min(COUNT_CAP, x) for x in h]))

    def from_stream(self, r: _Reader) -> None:
        n = r.u32()
        if n > MAX_ACTION_TYPES:
            raise ModelError("command rows exceed bounds")
        self._counts = Counter()
        self._hours = {}
        for _ in range(n):
            c = r.s()
            cnt = r.u16()
            h = list(struct.unpack("<12H", r.take(24)))
            self._counts[c] = cnt
            self._hours[c] = h


# ─────────────────────────────────────────────────────────────────────────────
# EmojiModel — personal emoji usage per context
# ─────────────────────────────────────────────────────────────────────────────

class EmojiModel:
    """Context-tagged emoji preference counts (bounded, personal)."""

    def __init__(self) -> None:
        self._counts: Dict[str, Counter] = {}

    def observe(self, tag: str, emoji: str) -> None:
        t = str(tag)[:32]
        e = str(emoji)[:16]
        if not t or not e:
            return
        c = self._counts.setdefault(t, Counter())
        if c[e] >= COUNT_CAP:
            return
        c[e] += 1
        if len(self._counts) > 256:
            self._prune()

    def _prune(self) -> None:
        rows = sorted(self._counts.items(),
                      key=lambda kv: (-sum(kv[1].values()), kv[0]))
        self._counts = dict(rows[:128])

    def suggest(self, tag: str, k: int = 3) -> List[Tuple[str, float]]:
        c = self._counts.get(str(tag)[:32])
        if not c:
            return []
        total = sum(c.values()) or 1
        items = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:max(0, k)]
        return [(e, n / total) for e, n in items]

    @property
    def entries(self) -> int:
        return sum(len(c) for c in self._counts.values())

    def bytes_estimate(self) -> int:
        return 8 + self.entries * 20

    def to_stream(self, out: io.BufferedIOBase) -> None:
        out.write(struct.pack("<I", len(self._counts)))
        for t, c in sorted(self._counts.items()):
            out.write(_pack_str(t))
            out.write(struct.pack("<I", len(c)))
            for e, n in sorted(c.items()):
                out.write(_pack_str(e))
                out.write(struct.pack("<H", n))

    def from_stream(self, r: _Reader) -> None:
        n = r.u32()
        if n > 256:
            raise ModelError("emoji rows exceed bounds")
        self._counts = {}
        for _ in range(n):
            t = r.s()
            m = r.u32()
            c = Counter()
            for _ in range(m):
                e = r.s()
                c[e] = r.u16()
            self._counts[t] = c


# ─────────────────────────────────────────────────────────────────────────────
# FeatureWeights — personalization weights
# ─────────────────────────────────────────────────────────────────────────────

class FeatureWeights:
    """Bounded named weight table (floats quantized to 8 bits on save)."""

    def __init__(self) -> None:
        self._w: Dict[str, float] = {}

    def set(self, name: str, value: float) -> None:
        n = str(name)[:48]
        if not n:
            return
        v = float(value)
        if v != v or v in (float("inf"), float("-inf")):
            return
        self._w[n] = max(-8.0, min(8.0, v))
        if len(self._w) > MAX_FEATURES:
            self._prune()

    def _prune(self) -> None:
        # keep the most recently set (dict preserves insertion order);
        # deterministic: drop alphabetically-first overflow
        while len(self._w) > MAX_FEATURES:
            drop = sorted(self._w.keys())[0]
            del self._w[drop]

    def get(self, name: str, default: float = 0.0) -> float:
        return self._w.get(str(name)[:48], float(default))

    def bump(self, name: str, delta: float) -> float:
        v = self.get(name) + float(delta)
        self.set(name, v)
        return self.get(name)

    def items(self) -> List[Tuple[str, float]]:
        return sorted(self._w.items())

    def bytes_estimate(self) -> int:
        return 8 + len(self._w) * 24

    def to_stream(self, out: io.BufferedIOBase) -> None:
        out.write(struct.pack("<I", len(self._w)))
        for n, v in sorted(self._w.items()):
            out.write(_pack_str(n))
            out.write(struct.pack("<B", _quant8((v + 8.0) / 16.0)))

    def from_stream(self, r: _Reader) -> None:
        n = r.u32()
        if n > MAX_FEATURES:
            raise ModelError("feature count exceeds bounds")
        self._w = {}
        for _ in range(n):
            name = r.s()
            q = r.u8()
            self._w[name] = _dequant8(q) * 16.0 - 8.0


# ─────────────────────────────────────────────────────────────────────────────
# PersonalInteractionModel — the packed composite artifact
# ─────────────────────────────────────────────────────────────────────────────

_SECTION_NGRAM = 1
_SECTION_ACTIONS = 2
_SECTION_COMMANDS = 3
_SECTION_EMOJI = 4
_SECTION_FEATURES = 5


class PersonalInteractionModel:
    """The versioned composite personal model (all sections packed).

    Capacity budget: ``capacity_bytes`` (default ~30 MB).  ``record``/
    ``observe`` paths check :meth:`at_capacity` and refuse further growth
    (reporting OUT_OF_MEMORY condition via the plugin facade) rather than
    growing without limit.
    """

    def __init__(self, capacity_bytes: int = MODEL_CAPACITY_BYTES_DEFAULT) -> None:
        self.capacity_bytes = int(capacity_bytes)
        self.ngram = NGramModel()
        self.actions = ActionMarkov()
        self.commands = CommandModel()
        self.emoji = EmojiModel()
        self.features = FeatureWeights()
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.dirty = False
        self.capacity_hits = 0

    # -- learning API ----------------------------------------------------------

    def learn_text(self, text: str) -> None:
        if self.at_capacity():
            self.capacity_hits += 1
            return
        self.ngram.observe(text)
        self._touch()

    def learn_action_step(self, history: Sequence[str], action: str) -> None:
        if self.at_capacity():
            self.capacity_hits += 1
            return
        self.actions.observe_step(history, action)
        self._touch()

    def learn_command(self, command: str, hour: Optional[int] = None) -> None:
        if self.at_capacity():
            self.capacity_hits += 1
            return
        self.commands.observe(command, hour)
        self._touch()

    def learn_emoji(self, tag: str, emoji: str) -> None:
        if self.at_capacity():
            self.capacity_hits += 1
            return
        self.emoji.observe(tag, emoji)
        self._touch()

    def learn_feature(self, name: str, delta: float) -> None:
        if self.at_capacity():
            self.capacity_hits += 1
            return
        self.features.bump(name, delta)
        self._touch()

    def _touch(self) -> None:
        self.updated_at = time.time()
        self.dirty = True

    # -- capacity ---------------------------------------------------------------

    def size_bytes(self) -> int:
        return (self.ngram.bytes_estimate() + self.actions.bytes_estimate()
                + self.commands.bytes_estimate() + self.emoji.bytes_estimate()
                + self.features.bytes_estimate() + 64)

    def at_capacity(self) -> bool:
        return self.size_bytes() >= self.capacity_bytes

    def usage_fraction(self) -> float:
        cap = max(1, self.capacity_bytes)
        return min(1.0, self.size_bytes() / cap)

    # -- serialization -----------------------------------------------------------

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        buf.write(MODEL_FORMAT_MAGIC)
        buf.write(struct.pack("<H", MODEL_FORMAT_VERSION))
        buf.write(struct.pack("<dd", self.created_at, self.updated_at))
        sections = (
            (_SECTION_NGRAM, self.ngram),
            (_SECTION_ACTIONS, self.actions),
            (_SECTION_COMMANDS, self.commands),
            (_SECTION_EMOJI, self.emoji),
            (_SECTION_FEATURES, self.features),
        )
        for sid, sec in sections:
            body = io.BytesIO()
            sec.to_stream(body)
            payload = body.getvalue()
            buf.write(struct.pack("<HI", sid, len(payload)))
            buf.write(payload)
        return buf.getvalue()

    def save(self, path: str) -> int:
        data = self.to_bytes()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        self.dirty = False
        return len(data)

    @classmethod
    def from_bytes(cls, data: bytes,
                   capacity_bytes: int = MODEL_CAPACITY_BYTES_DEFAULT
                   ) -> "PersonalInteractionModel":
        r = _Reader(data or b"")
        if r.take(4) != MODEL_FORMAT_MAGIC:
            raise ModelError("bad magic")
        version = r.u16()
        if version != MODEL_FORMAT_VERSION:
            raise ModelError(f"incompatible format version {version}")
        m = cls(capacity_bytes=capacity_bytes)
        m.created_at, m.updated_at = struct.unpack("<dd", r.take(16))
        while not r.done():
            sid = r.u16()
            length = r.u32()
            body = _Reader(r.take(length))
            if sid == _SECTION_NGRAM:
                m.ngram.from_stream(body)
            elif sid == _SECTION_ACTIONS:
                m.actions.from_stream(body)
            elif sid == _SECTION_COMMANDS:
                m.commands.from_stream(body)
            elif sid == _SECTION_EMOJI:
                m.emoji.from_stream(body)
            elif sid == _SECTION_FEATURES:
                m.features.from_stream(body)
            else:
                continue  # unknown section: skip (forward compatible)
            if not body.done():
                raise ModelError(f"section {sid} length mismatch")
        m.dirty = False
        return m

    @classmethod
    def load(cls, path: str,
             capacity_bytes: int = MODEL_CAPACITY_BYTES_DEFAULT
             ) -> "PersonalInteractionModel":
        with open(path, "rb") as f:
            data = f.read()
        return cls.from_bytes(data, capacity_bytes=capacity_bytes)

    # -- introspection -----------------------------------------------------------

    def stats(self) -> Dict[str, object]:
        return {
            "size_bytes": self.size_bytes(),
            "capacity_bytes": self.capacity_bytes,
            "usage_fraction": round(self.usage_fraction(), 4),
            "capacity_hits": self.capacity_hits,
            "ngram_words": self.ngram.total_words,
            "ngram_vocab": self.ngram.vocab_size,
            "ngram_entries": self.ngram.entry_count,
            "action_steps": self.actions.steps,
            "commands_distinct": self.commands.distinct,
            "emoji_entries": self.emoji.entries,
            "features": len(self.features.items()),
            "format_version": MODEL_FORMAT_VERSION,
        }
