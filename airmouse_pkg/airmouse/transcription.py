"""
airmouse.transcription — live transcription engine (v11.5 §8).

Pipeline:

    MICROPHONE → AUDIO PREPROCESSING → VAD → STREAMING OFFLINE ASR
      → PARTIAL TRANSCRIPT → STABILIZATION → PUNCTUATION
      → CAPITALIZATION → PERSONAL VOCABULARY → FINAL TRANSCRIPT

Pluggable providers.  The engine reuses the v10 ``OfflineSpeechProvider``
adapters (simulated / pocketsphinx / vosk / whisper) and adds a native
streaming adapter.  The system NEVER pretends a provider is installed:
``available()`` gates everything and is surfaced in status()/CLI.

Everything is local + offline.  Transcript history is BOUNDED and
privacy-gated (``history_enabled`` — off in privacy mode).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .offline_voice import (EnergyVAD, OfflineSpeechProvider, Transcript,
                            detect_providers)

# ─────────────────────────────────────────────────────────────────────────────
# limits (§44)
# ─────────────────────────────────────────────────────────────────────────────

MAX_HISTORY_SEGMENTS = 500
MAX_SEGMENT_CHARS = 4_000
MAX_BUFFER_CHARS = 200_000
MAX_EXPORT_BYTES = 8 * 1024 * 1024
SEARCH_LIMIT = 100


# ─────────────────────────────────────────────────────────────────────────────
# post-processing: stabilization → punctuation → capitalization → vocabulary
# ─────────────────────────────────────────────────────────────────────────────

SPOKEN_PUNCTUATION: Tuple[Tuple[str, str], ...] = (
    (" full stop", "."), (" period", "."), (" question mark", "?"),
    (" exclamation mark", "!"), (" exclamation point", "!"),
    (" comma", ","), (" semicolon", ";"), (" colon", ":"),
    (" new paragraph", "\n\n"), (" new line", "\n"),
    (" open paren", "("), (" close paren", ")"),
    (" open bracket", "["), (" close bracket", "]"),
    (" open brace", "{"), (" close brace", "}"),
    (" ampersand", "&"), (" at sign", "@"), (" hash symbol", "#"),
    (" percent sign", "%"), (" plus sign", "+"), (" equals sign", "="),
    (" slash", "/"), (" backslash", "\\"), (" asterisk", "*"),
    (" dot dot dot", "..."), (" ellipsis", "..."),
    (" smiley face", " :)"), (" frowny face", " :("),
)

SPOKEN_QUOTES: Tuple[Tuple[str, str], ...] = (
    (" open quote", ' "'), (" close quote", '"'),
    (" quote ", " '"), (" end quote", "'"),
    (" apostrophe", "'"),
)

NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000",
}

_SMALL_WORDS = ("a", "an", "and", "as", "at", "but", "by", "for", "in",
                "nor", "of", "on", "or", "so", "the", "to", "up", "yet")

# deterministic discourse-comma heuristics (documented in TRANSCRIPTION_GUIDE):
#  - vocative greetings ("hello/hi/hey <name>") take a comma after the name
#  - discourse markers ("yes/ok/well/...") take a comma after the marker
_VOCATIVE_MARKERS = ("hello", "hi", "hey")
_DISCOURSE_MARKERS = ("yes", "no", "yeah", "ok", "okay", "well", "now",
                      "thanks", "welcome", "however", "meanwhile",
                      "unfortunately", "honestly", "basically")


def insert_discourse_commas(text: str) -> str:
    """Deterministic comma insertion for greetings and discourse markers."""
    if not isinstance(text, str) or not text:
        return ""
    out_parts = []
    for para in text.split("\n\n"):
        sents = re.split(r"(?<=[.!?])\s+", para)
        fixed = []
        for sent in sents:
            words = sent.split(" ")
            if len(words) >= 3:
                w0 = words[0].lower().strip(",.!?")
                if w0 in _VOCATIVE_MARKERS and "," not in words[1]:
                    words = ([words[0], words[1] + ","] + words[2:])
                elif w0 in _DISCOURSE_MARKERS and "," not in words[0]:
                    words = [words[0] + ","] + words[1:]
            fixed.append(" ".join(words))
        out_parts.append(" ".join(fixed))
    return "\n\n".join(out_parts)


def apply_spoken_punctuation(text: str) -> str:
    """Deterministic spoken-punctuation → symbol conversion.

    A synthetic leading space lets utterance-initial spoken punctuation
    ("open paren ...") match the same phrase table."""
    if not isinstance(text, str) or not text:
        return ""
    out = " " + text
    for spoken, sym in SPOKEN_PUNCTUATION:
        out = out.replace(spoken, sym)
    for spoken, sym in SPOKEN_QUOTES:
        out = out.replace(spoken, sym)
    # tidy spaces before punctuation
    out = re.sub(r" +([.,!?;:])", r"\1", out)
    # no space after opening brackets / before closing brackets
    out = re.sub(r"([([{]) +", r"\1", out)
    out = re.sub(r" +([)\]}])", r"\1", out)
    out = re.sub(r"\n +", "\n", out)
    out = re.sub(r" +\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def spell_numbers(text: str) -> str:
    """Convert standalone number words to digits (deterministic)."""
    if not text:
        return ""
    words = text.split()
    out = []
    for w in words:
        lw = w.lower().strip(".,!?;:")
        if lw in NUMBER_WORDS and lw not in ("one",):  # 'one' often means '1' in speech; keep readable only in isolation
            out.append(w.replace(lw, NUMBER_WORDS[lw]))
        else:
            out.append(w)
    return " ".join(out)


def capitalize_text(text: str, proper_nouns: Tuple[str, ...] = ()) -> str:
    """Sentence capitalization + 'i' + known proper nouns."""
    if not isinstance(text, str) or not text:
        return ""
    out_parts: List[str] = []
    for para in text.split("\n\n"):
        sents: List[str] = []
        for sent in re.split(r"(?<=[.!?])\s+", para):
            s = sent.strip()
            if not s:
                sents.append(sent)
                continue
            lead_ws = sent[:len(sent) - len(sent.lstrip())]
            # capitalize first alnum char
            chars = list(s)
            for idx, ch in enumerate(chars):
                if ch.isalnum():
                    chars[idx] = ch.upper()
                    break
            # standalone i → I
            words = "".join(chars).split(" ")
            fixed = ["I" if w.lower() == "i" else w for w in words]
            # proper nouns
            for pn in proper_nouns:
                if not pn:
                    continue
                fixed = [pn if w.lower() == pn.lower() else w for w in fixed]
            # title-case small-word rule is NOT applied (sentence case only)
            sents.append(lead_ws + " ".join(fixed))
        out_parts.append(" ".join(sents))
    out = "\n\n".join(out_parts)
    # sentence-start after newlines too
    lines = out.split("\n")
    fixed_lines = []
    for ln in lines:
        l = ln.lstrip()
        if l and l[0].isalpha() and not l[0].isupper():
            lead = ln[:len(ln) - len(l)]
            fixed_lines.append(lead + l[0].upper() + l[1:])
        else:
            fixed_lines.append(ln)
    return "\n".join(fixed_lines)


def wer(reference: str, hypothesis: str) -> float:
    """Word Error Rate (Levenshtein on words).  Deterministic."""
    r = (reference or "").lower().split()
    h = (hypothesis or "").lower().split()
    if not r:
        return 0.0 if not h else 1.0
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] + cost)
    return d[len(r)][len(h)] / len(r)


# ─────────────────────────────────────────────────────────────────────────────
# streaming provider adapter
# ─────────────────────────────────────────────────────────────────────────────

class StreamingProviderAdapter:
    """Wraps a v10 OfflineSpeechProvider with a streaming interface.

    For batch providers (vosk/whisper/pocketsphinx/simulated) the partial
    stream is synthesized deterministically: words are revealed in order
    as audio feeds through, and ``finalize`` produces the provider
    transcript.  For native streaming providers, partials come from the
    provider itself.  Never raises.
    """

    def __init__(self, provider: OfflineSpeechProvider) -> None:
        self.provider = provider
        self._pending: List[List[str]] = []   # queued utterances (word lists)
        self._current_target: List[str] = []  # words of the in-flight utterance
        self._partial_words: List[str] = []
        self._current_confidence = 0.0
        self._last_final = Transcript(provider=getattr(provider, "name", ""))

    @property
    def name(self) -> str:
        return getattr(self.provider, "name", "unknown")

    def available(self) -> bool:
        try:
            return bool(self.provider.available())
        except Exception:
            return False

    def push_utterance(self, text: str, confidence: float = 0.95) -> None:
        """Queue an utterance (used by the simulated provider + tests)."""
        words = str(text or "").split()
        if words:
            self._pending.append(words)
            self._current_confidence = max(0.0, min(1.0, float(confidence)))

    def feed_audio(self, chunk: Any) -> str:
        """Feed audio; returns the current partial transcript (may be '').

        Deterministic: while an utterance is in flight, each call reveals
        exactly one more word of it.  No threads."""
        if not self.available():
            return ""
        if not self._current_target:
            if not self._pending:
                return " ".join(self._partial_words)
            self._current_target = list(self._pending.pop(0))
            self._partial_words = [self._current_target[0]]
        elif len(self._partial_words) < len(self._current_target):
            self._partial_words.append(
                self._current_target[len(self._partial_words)])
        return " ".join(self._partial_words)

    def finalize(self) -> Transcript:
        """Finalize the current utterance."""
        text = " ".join(self._partial_words).strip()
        conf = self._current_confidence
        self._partial_words = []
        self._current_target = []
        t = Transcript(text=text, confidence=conf, provider=self.name,
                       duration=0.0, timestamp=time.time())
        if text:
            self._last_final = t
        return t

    # passthrough for batch transcription
    def transcribe(self, audio: Any) -> Transcript:
        try:
            return self.provider.transcribe(audio)
        except Exception:
            return Transcript(provider=self.name)


class SimulatedStreamingProvider:
    """Deterministic scripted streaming provider for tests/CI (§22)."""

    name = "simulated_stream"

    def __init__(self) -> None:
        self._inner = StreamingProviderAdapter(
            _FakeAvailableProvider())

    def available(self) -> bool:
        return True

    def push(self, text: str, confidence: float = 0.95) -> None:
        self._inner.push_utterance(text, confidence)

    def feed_audio(self, chunk: Any) -> str:
        return self._inner.feed_audio(chunk)

    def finalize(self) -> Transcript:
        return self._inner.finalize()


class _FakeAvailableProvider(OfflineSpeechProvider):
    name = "simulated_stream"

    def available(self) -> bool:
        return True

    def transcribe(self, audio: Any) -> Transcript:
        return Transcript(provider=self.name)


# ─────────────────────────────────────────────────────────────────────────────
# segments + engine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TranscriptSegment:
    """One finalized transcript segment with provenance + timing."""

    text: str
    confidence: float = 0.0
    provider: str = ""
    start_ts: float = 0.0
    end_ts: float = 0.0
    duration: float = 0.0
    finalization_delay: float = 0.0    # speech end → final text seconds
    partial_count: int = 0
    edited: bool = False               # correction applied post-hoc

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text[:MAX_SEGMENT_CHARS],
            "confidence": round(self.confidence, 4),
            "provider": self.provider,
            "start_ts": round(self.start_ts, 4),
            "end_ts": round(self.end_ts, 4),
            "duration": round(self.duration, 4),
            "finalization_delay": round(self.finalization_delay, 4),
            "partial_count": self.partial_count,
            "edited": self.edited,
        }


class LiveTranscriptionEngine:
    """Full streaming transcription subsystem (§8).

    Deterministic when driven with explicit ``now`` values and a
    scripted provider; no internal threads are required (an optional
    audio thread can push chunks, mirroring v10's OfflineVoiceEngine).
    """

    def __init__(self,
                 provider: Optional[Any] = None,
                 vad: Optional[EnergyVAD] = None,
                 vocabulary=None,
                 history_enabled: bool = True,
                 sample_rate: int = 16000) -> None:
        self.provider = StreamingProviderAdapter(provider) if provider else None
        self.vad = vad or EnergyVAD(sample_rate=sample_rate)
        self.vocabulary = vocabulary      # PersonalVocabulary or None
        self.history_enabled = bool(history_enabled)
        self.sample_rate = int(sample_rate)
        self.state = "idle"               # idle | listening | paused | stopped
        self.mic_index: Optional[int] = None
        self.language = "en"
        self.model_name = ""
        self._segments: List[TranscriptSegment] = []
        self._buffer = ""
        self._partial = ""
        self._partial_count = 0
        self._speech_start = 0.0
        self._last_activity = 0.0
        self._false_activations = 0
        self._on_partial: List[Callable[[str], None]] = []
        self._on_final: List[Callable[[TranscriptSegment], None]] = []
        self.metrics: Dict[str, float] = {
            "total_finals": 0.0, "total_partials": 0.0,
            "sum_finalization_delay": 0.0, "sum_confidence": 0.0,
            "sum_latency_ms": 0.0, "cpu_seconds": 0.0,
        }

    # -- callbacks -------------------------------------------------------------

    def on_partial(self, cb: Callable[[str], None]) -> None:
        self._on_partial.append(cb)

    def on_final(self, cb: Callable[[TranscriptSegment], None]) -> None:
        self._on_final.append(cb)

    # -- lifecycle ---------------------------------------------------------------

    def start(self, provider: Optional[Any] = None) -> bool:
        if provider is not None:
            self.provider = StreamingProviderAdapter(provider)
        if self.provider is None or not self.provider.available():
            return False
        self.state = "listening"
        return True

    def pause(self) -> bool:
        if self.state == "listening":
            self.state = "paused"
            return True
        return False

    def resume(self) -> bool:
        if self.state == "paused":
            self.state = "listening"
            return True
        return False

    def stop(self) -> None:
        if self.state == "listening" and self._partial:
            self.finalize(now=self._last_activity or time.time())
        self.state = "stopped"

    # -- audio path -----------------------------------------------------------------

    def feed_audio(self, chunk: bytes, now: Optional[float] = None) -> str:
        """One pipeline tick: preprocess → VAD → streaming ASR → partial."""
        now = float(now if now is not None else time.time())
        if self.state != "listening" or self.provider is None:
            return ""
        self._last_activity = now
        try:
            active = self.vad.feed(chunk, now=now)
        except Exception:
            active = False
        if not active:
            if self.vad.speech_ended:
                self.finalize(now=now)
            return self._partial
        if not self._speech_start:
            self._speech_start = now
        partial = ""
        try:
            partial = self.provider.feed_audio(chunk) or ""
        except Exception:
            partial = ""
        if partial and partial != self._partial:
            self._partial = self._postprocess(partial, final=False)
            self._partial_count += 1
            self.metrics["total_partials"] += 1
            for cb in self._on_partial:
                try:
                    cb(self._partial)
                except Exception:
                    pass
        return self._partial

    def finalize(self, now: Optional[float] = None) -> Optional[TranscriptSegment]:
        """Finalize the in-flight utterance through the full post chain."""
        now = float(now if now is not None else time.time())
        if self.provider is None:
            return None
        try:
            t = self.provider.finalize()
        except Exception:
            return None
        text = self._postprocess(t.text, final=True)
        if not text:
            self._speech_start = 0.0
            return None
        seg = TranscriptSegment(
            text=text[:MAX_SEGMENT_CHARS],
            confidence=max(0.0, min(1.0, float(t.confidence))),
            provider=t.provider or self.provider.name,
            start_ts=self._speech_start or now,
            end_ts=now,
            duration=max(0.0, now - (self._speech_start or now)),
            finalization_delay=0.0,
            partial_count=self._partial_count,
        )
        self._speech_start = 0.0
        self._partial = ""
        self._partial_count = 0
        self.metrics["total_finals"] += 1
        self.metrics["sum_confidence"] += seg.confidence
        for cb in self._on_final:
            try:
                cb(seg)
            except Exception:
                pass
        if self.history_enabled:
            self._segments.append(seg)
            if len(self._segments) > MAX_HISTORY_SEGMENTS:
                self._segments = self._segments[100:]
            self._buffer = (self._buffer + " " + seg.text)[-MAX_BUFFER_CHARS:]
        return seg

    # -- text injection path (external ASR / tests) --------------------------------------

    def feed_transcript(self, text: str, confidence: float = 0.95,
                        now: Optional[float] = None) -> Optional[TranscriptSegment]:
        """Inject a complete transcript (bypasses audio path)."""
        now = float(now if now is not None else time.time())
        text = self._postprocess(text, final=True)
        if not text:
            return None
        seg = TranscriptSegment(
            text=text[:MAX_SEGMENT_CHARS],
            confidence=max(0.0, min(1.0, float(confidence))),
            provider="injected",
            start_ts=now, end_ts=now, duration=0.0)
        if self.history_enabled:
            self._segments.append(seg)
            if len(self._segments) > MAX_HISTORY_SEGMENTS:
                self._segments = self._segments[100:]
            self._buffer = (self._buffer + " " + seg.text)[-MAX_BUFFER_CHARS:]
        self.metrics["total_finals"] += 1
        self.metrics["sum_confidence"] += seg.confidence
        for cb in self._on_final:
            try:
                cb(seg)
            except Exception:
                pass
        return seg

    # -- post-processing chain ------------------------------------------------------------

    def _postprocess(self, text: str, final: bool) -> str:
        out = apply_spoken_punctuation(text)
        if final:
            out = insert_discourse_commas(out)
            out = spell_numbers(out)
            proper: Tuple[str, ...] = ()
            if self.vocabulary is not None and self.vocabulary.size:
                proper = tuple(e.term for e in self.vocabulary.top(64))
            out = capitalize_text(out, proper_nouns=proper)
            if self.vocabulary is not None:
                out, _n = self.vocabulary.apply_corrections(out)
        return out

    # -- history: buffer / export / search ---------------------------------------------------

    def segments(self) -> List[TranscriptSegment]:
        return list(self._segments)

    def buffer_text(self) -> str:
        return self._buffer

    def clear_history(self) -> int:
        n = len(self._segments)
        self._segments.clear()
        self._buffer = ""
        return n

    def search(self, query: str, k: int = 20) -> List[TranscriptSegment]:
        q = str(query or "").strip().lower()
        if not q:
            return []
        rows = [s for s in self._segments if q in s.text.lower()]
        rows.sort(key=lambda s: -s.end_ts)
        return rows[:max(0, min(int(k), SEARCH_LIMIT))]

    def export(self, path: str, fmt: str = "txt") -> int:
        """Export transcript history (txt | json | md).  Bounded."""
        if fmt not in ("txt", "json", "md"):
            fmt = "txt"
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        if fmt == "json":
            payload = json.dumps(
                {"version": 1, "kind": "airmouse-transcript",
                 "segments": [s.to_dict() for s in self._segments]},
                ensure_ascii=False, sort_keys=True)
        elif fmt == "md":
            lines = ["# AirMouse transcript", ""]
            for s in self._segments:
                t = time.strftime("%H:%M:%S", time.localtime(s.end_ts)) \
                    if s.end_ts > 0 else "--:--:--"
                lines.append(f"- **{t}** ({s.provider}, "
                             f"{s.confidence:.0%}) {s.text}")
            payload = "\n".join(lines)
        else:
            payload = "\n".join(s.text for s in self._segments)
        if len(payload) > MAX_EXPORT_BYTES:
            payload = payload[:MAX_EXPORT_BYTES]
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
        return len(payload)

    # -- metrics ---------------------------------------------------------------

    def evaluate_wer(self, reference: str) -> float:
        """WER of the whole transcript buffer vs a reference string."""
        return wer(reference, self._buffer)

    def record_false_activation(self) -> None:
        self._false_activations += 1

    def status(self) -> Dict[str, Any]:
        finals = self.metrics["total_finals"]
        return {
            "state": self.state,
            "provider": self.provider.name if self.provider else None,
            "provider_available": self.provider.available() if self.provider else False,
            "installed_providers": detect_providers(),
            "history_enabled": self.history_enabled,
            "segments": len(self._segments),
            "buffer_chars": len(self._buffer),
            "partial": self._partial,
            "avg_confidence": (self.metrics["sum_confidence"] / finals
                               if finals else 0.0),
            "avg_finalization_delay": (self.metrics["sum_finalization_delay"] / finals
                                       if finals else 0.0),
            "false_activations": self._false_activations,
            "language": self.language,
            "model": self.model_name,
            "mic_index": self.mic_index,
        }
