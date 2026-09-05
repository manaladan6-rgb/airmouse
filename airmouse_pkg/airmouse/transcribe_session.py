"""Live transcription sessions (v16.5, mission §6) — real pipeline, honest labels.

The v16.5 voice architecture ends at live transcription:
Microphone → preprocessing → VAD → streaming ASR → partial → final →
normalization → storage.  :class:`TranscribeSession` wraps the shipped
:class:`airmouse.transcription.LiveTranscriptionEngine` and the §6
controls around it:

* **Provider selection is honest**: a real LOCAL ASR engine
  (``voice_stack.pick_provider``) is used when one is installed, wrapped
  through :class:`airmouse.transcription.StreamingProviderAdapter`;
  otherwise the deterministic :class:`SimulatedStreamingProvider` runs
  under a loud banner — ``SIMULATED provider — install a local ASR
  engine for real transcription (see: airmouse voice-status)``.  The
  session never pretends a simulated stream is real ASR.
* **Works headless**: there is no microphone in a sandbox, so the REPL
  runs in TEXT-INPUT mode — typed lines are pushed through the FULL
  provider pipeline (push → word-by-word partials → finalize) exactly
  like spoken utterances would be.  Microphone capture itself remains
  **PHYSICAL TEST REQUIRED** and is labelled as such.
* **Privacy by construction**: history is BOUNDED (default 500
  segments), nothing is written unless the user explicitly says
  ``save`` — and only the finalized TEXT segments are written to
  ``paths.transcripts_dir()/transcript-<unixts>.txt|.json``.  NO AUDIO
  IS EVER STORED.

``run_transcribe(...)`` is the interactive text-REPL control loop
(pause / resume / save [json] / clear / status / stop / quit; any other
text is treated as a spoken utterance).  rc 0 on clean stop/quit.
``render_transcript_panel`` is the pure §6-style panel renderer.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import os
import sys
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple

from . import paths
from . import voice_stack as _voice_stack
from .offline_voice import OfflineSpeechProvider
from .transcription import (LiveTranscriptionEngine,
                            SimulatedStreamingProvider,
                            StreamingProviderAdapter, TranscriptSegment)

__all__ = [
    "TranscribeSession", "run_transcribe", "render_transcript_panel",
    "DEFAULT_HISTORY_LIMIT",
]

DEFAULT_HISTORY_LIMIT = 500

_SIMULATED_BANNER = ("SIMULATED provider — install a local ASR engine for "
                     "real transcription (see: airmouse voice-status)")
_PHYSICAL_NOTE = ("live microphone capture is PHYSICAL TEST REQUIRED; "
                  "this session exercises the pipeline via text input")
_TEXT_INPUT_NOTE = "text-input mode (microphone capture needs hardware)"

_COMMANDS_LINE = ("commands: pause | resume | save [json] | clear | status "
                  "| stop | quit — any other text is treated as a spoken "
                  "utterance")


# ---------------------------------------------------------------------------
# the session
# ---------------------------------------------------------------------------

class TranscribeSession:
    """One live-transcription session over the REAL engine.

    The wrapped provider is a real local ASR engine when one is
    installed (``StreamingProviderAdapter`` over
    :class:`OfflineSpeechProvider`), else the deterministic
    :class:`SimulatedStreamingProvider` (``simulated=True``, banner
    honesty).  Segments carry timestamp + confidence; history is
    bounded; saving happens ONLY on an explicit call to :meth:`save`.
    """

    def __init__(self,
                 provider: Optional[Any] = None,
                 simulate: bool = False,
                 history_limit: int = DEFAULT_HISTORY_LIMIT,
                 engine: Optional[LiveTranscriptionEngine] = None) -> None:
        self.history_limit = max(1, int(history_limit))
        self._saved: List[str] = []

        stream: Any
        if provider is not None:
            if isinstance(provider, (StreamingProviderAdapter,
                                     SimulatedStreamingProvider)):
                stream = provider
            elif isinstance(provider, OfflineSpeechProvider):
                stream = StreamingProviderAdapter(provider)
            else:
                # duck-typed streaming provider (feed_audio/finalize)
                stream = provider
        else:
            picked: Optional[OfflineSpeechProvider] = None
            if not simulate:
                try:
                    picked = _voice_stack.pick_provider()
                except Exception:
                    picked = None
            if picked is not None:
                stream = StreamingProviderAdapter(picked)
            else:
                stream = SimulatedStreamingProvider()

        self.stream = stream
        self.simulated = isinstance(stream, SimulatedStreamingProvider)

        self.engine = engine or LiveTranscriptionEngine()
        # assign the stream directly: the simulated provider is exercised
        # through its own push → feed_audio → finalize cycle; a real batch
        # provider sits inside a StreamingProviderAdapter (deterministic
        # word-by-word partials), exactly as the §6 pipeline describes.
        self.engine.provider = stream
        self.engine.history_enabled = True
        self._segments: Deque[TranscriptSegment] = deque(
            maxlen=self.history_limit)
        self.engine.on_final(self._record)

        try:
            self.engine.start()
        except Exception:
            pass

    # -- recording -----------------------------------------------------------

    def _record(self, seg: TranscriptSegment) -> None:
        self._segments.append(seg)

    # -- the utterance path (text or streaming — same pipeline) ---------------

    def utterance(self, text: str, confidence: float = 0.95,
                  now: Optional[float] = None) -> Optional[TranscriptSegment]:
        """Push one utterance through the provider → finalize cycle.

        Works identically for the simulated and the wrapped real
        provider: push → word-by-word partial reveal → finalize (the
        engine's full post-processing chain applies).  Returns the
        recorded :class:`TranscriptSegment`, or ``None`` when the
        session is paused/stopped or the text is empty — paused means
        accumulation STOPS (nothing is buffered).
        """
        if self.engine.state != "listening":
            return None
        text = str(text or "").strip()
        if not text:
            return None
        try:
            conf = max(0.0, min(1.0, float(confidence)))
        except Exception:
            conf = 0.95
        try:
            if hasattr(self.stream, "push"):
                self.stream.push(text, conf)
            else:
                self.stream.push_utterance(text, conf)
            for _ in range(len(text.split())):
                self.stream.feed_audio(None)
            return self.engine.finalize(now=now)
        except Exception:
            return None

    # -- §6 controls -------------------------------------------------------------

    def pause(self) -> bool:
        try:
            return bool(self.engine.pause())
        except Exception:
            return False

    def resume(self) -> bool:
        try:
            return bool(self.engine.resume())
        except Exception:
            return False

    def stop(self) -> None:
        try:
            self.engine.stop()
        except Exception:
            pass

    def clear(self) -> int:
        """Drop the whole history (session + engine).  Returns the count."""
        n = len(self._segments)
        self._segments.clear()
        try:
            n = max(n, self.engine.clear_history())
        except Exception:
            pass
        return n

    # -- explicit save (NEVER automatic; text only, no audio) ---------------------

    def save(self, fmt: str = "txt") -> Tuple[str, int]:
        """Write the transcript to ``transcripts_dir()`` — explicit only.

        ``txt`` (plain text lines) or ``json`` (full segments with
        timestamps + confidence).  The filename is
        ``transcript-<unixts>.<fmt>``.  No audio is ever stored.
        Returns ``(path, bytes_written)``; ``("", 0)`` on failure.
        """
        fmt_v = "json" if str(fmt or "").strip().lower().lstrip(".") == "json" \
            else "txt"
        try:
            d = paths.transcripts_dir()
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "transcript-%d.%s"
                                % (int(time.time()), fmt_v))
            n = self.engine.export(path, fmt_v)
            self._saved.append(path)
            return (path, int(n))
        except Exception:
            return ("", 0)

    # -- introspection --------------------------------------------------------------

    @property
    def state(self) -> str:
        try:
            return str(self.engine.state)
        except Exception:
            return "unknown"

    @property
    def provider_name(self) -> str:
        try:
            return str(getattr(self.stream, "name", "") or "unknown")
        except Exception:
            return "unknown"

    def segments(self) -> List[TranscriptSegment]:
        return list(self._segments)

    def buffer_text(self) -> str:
        try:
            return self.engine.buffer_text()
        except Exception:
            return ""

    def partial(self) -> str:
        try:
            return str(self.engine.status().get("partial", ""))
        except Exception:
            return ""

    def saved_files(self) -> List[str]:
        return list(self._saved)

    def status(self) -> Dict[str, Any]:
        """Honest session snapshot (for the REPL ``status`` command)."""
        try:
            engine_status = self.engine.status()
        except Exception:
            engine_status = {}
        return {
            "state": self.state,
            "provider": self.provider_name,
            "simulated": self.simulated,
            "segments": len(self._segments),
            "history_limit": self.history_limit,
            "partial": self.partial(),
            "buffer_chars": len(self.buffer_text()),
            "transcripts_dir": paths.transcripts_dir(),
            "saved": self.saved_files(),
            "engine": engine_status,
        }


# ---------------------------------------------------------------------------
# §6 panel (pure renderer)
# ---------------------------------------------------------------------------

def _seg_field(seg: Any, name: str, default: Any = "") -> Any:
    if isinstance(seg, dict):
        return seg.get(name, default)
    return getattr(seg, name, default)


def _hhmmss(ts: float) -> str:
    try:
        ts = float(ts)
    except Exception:
        return "--:--:--"
    if ts <= 0:
        return "--:--:--"
    return time.strftime("%H:%M:%S", time.localtime(ts))


def render_transcript_panel(segments: Any,
                            listening: bool = True,
                            provider: str = "",
                            partial: str = "",
                            limit: int = 6) -> str:
    """The §6-style live panel; the latest partial is shown in quotes."""
    try:
        rows = list(segments or [])
    except Exception:
        rows = []
    try:
        k = max(0, int(limit))
    except Exception:
        k = 6
    recent = rows[-k:] if k else []
    state_word = "listening" if listening else "not listening"
    lines = [
        "LIVE TRANSCRIPTION",
        "──────────────────",
        f"state: {state_word}   provider: {provider or 'unknown'}   "
        f"segments: {len(rows)}",
    ]
    if recent:
        lines.append("recent:")
        base = len(rows) - len(recent)
        for i, seg in enumerate(recent, 1):
            text = str(_seg_field(seg, "text", ""))
            conf = _seg_field(seg, "confidence", 0.0)
            try:
                conf_v = max(0.0, min(1.0, float(conf)))
            except Exception:
                conf_v = 0.0
            ts = _hhmmss(float(_seg_field(seg, "end_ts", 0.0) or 0.0))
            lines.append(f"  {base + i:>3}. [{ts}] {conf_v:>3.0%}  "
                         f"\"{text}\"")
    else:
        lines.append('recent: (nothing yet — type anything to transcribe)')
    lines.append(f'partial: "{str(partial or "")}"')
    lines.append("Listening..." if listening else "(paused or stopped — "
                                                  "say 'resume' or 'quit')")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the interactive text-REPL control loop
# ---------------------------------------------------------------------------

def _emit(out: Any, text: str = "") -> None:
    w = out if out is not None else sys.stdout
    try:
        w.write(text + "\n")
        try:
            w.flush()
        except Exception:
            pass
    except Exception:
        pass


def _make_line_source(input_fn: Optional[Callable[[str], str]],
                      feed: Optional[Iterable[str]]) -> Callable[[], Optional[str]]:
    """Returns a zero-arg line getter; ``None`` means clean quit/EOF."""
    it = iter(feed) if feed is not None else None

    def _next() -> Optional[str]:
        if input_fn is not None:
            try:
                return str(input_fn("> "))
            except (EOFError, KeyboardInterrupt):
                return None
            except Exception:
                return None
        if it is not None:
            try:
                return str(next(it))
            except StopIteration:
                return None
            except (EOFError, KeyboardInterrupt):
                return None
            except Exception:
                return None
        try:
            return input("> ")
        except (EOFError, KeyboardInterrupt):
            return None
        except Exception:
            return None

    return _next


def run_transcribe(out: Any = None,
                   input_fn: Optional[Callable[[str], str]] = None,
                   simulate: bool = False,
                   feed: Optional[Iterable[str]] = None) -> int:
    """Interactive text-REPL transcription session (works headless).

    ``simulate=True`` forces the simulated provider; otherwise a real
    local ASR engine is used when one is installed (stated honestly,
    with the PHYSICAL TEST REQUIRED note for live capture), else the
    simulated provider runs under its loud banner.  ``feed`` scripts the
    REPL lines (tests/CI); ``input_fn`` (when callable) takes precedence.

    Returns 0 on a clean stop/quit/EOF; 1 only on an unexpected error.
    """
    try:
        session = TranscribeSession(simulate=simulate)
    except Exception as exc:  # pragma: no cover - defensive
        _emit(out, f"transcribe session failed to start: {exc}")
        return 1

    _emit(out, "══ LIVE TRANSCRIPTION ══")
    if session.simulated:
        _emit(out, f"provider: {session.provider_name}")
        _emit(out, f"⚠ {_SIMULATED_BANNER}")
    else:
        _emit(out, f"provider: {session.provider_name} (local ASR engine)")
        _emit(out, f"⚠ {_PHYSICAL_NOTE}")
    _emit(out, _TEXT_INPUT_NOTE)
    _emit(out, "Listening...")
    _emit(out, _COMMANDS_LINE)

    nxt = _make_line_source(input_fn, feed)
    try:
        while True:
            raw = nxt()
            if raw is None:
                break
            line = str(raw).strip()
            low = line.lower()
            if low in ("quit", "stop", "exit", "q"):
                break
            if low == "pause":
                ok = session.pause()
                _emit(out, "paused — accumulation stops until 'resume'"
                      if ok else f"(cannot pause — state: {session.state})")
                continue
            if low == "resume":
                ok = session.resume()
                _emit(out, "listening again"
                      if ok else f"(nothing to resume — state: "
                                 f"{session.state})")
                continue
            if low == "clear":
                _emit(out, f"cleared {session.clear()} segment(s)")
                continue
            if low == "status":
                _emit(out, render_transcript_panel(
                    session.segments(),
                    listening=(session.state == "listening"),
                    provider=session.provider_name,
                    partial=session.partial()))
                st = session.status()
                _emit(out, f"history: {st['segments']}/{st['history_limit']}"
                           f" segments, buffer {st['buffer_chars']} chars, "
                           f"transcripts dir: {st['transcripts_dir']}")
                continue
            if low == "save" or low.startswith("save "):
                fmt = "json" if low.endswith("json") else "txt"
                path, nbytes = session.save(fmt)
                if path:
                    _emit(out, f"saved → {path} ({nbytes} bytes, text only "
                               f"— no audio is ever stored)")
                else:
                    _emit(out, "save failed (nothing written)")
                continue
            if not line:
                continue
            seg = session.utterance(line)
            if seg is not None:
                _emit(out, f'  [{_hhmmss(seg.end_ts)}] '
                           f"{max(0.0, min(1.0, seg.confidence)):.0%} "
                           f'"{seg.text}"')
            elif session.state == "paused":
                _emit(out, "(paused — utterance ignored; say 'resume' to "
                           "keep transcribing)")
            else:
                _emit(out, f"(no segment recorded — state: {session.state})")
    except Exception as exc:  # pragma: no cover - the loop must not crash
        _emit(out, f"transcribe session error: {exc}")
        session.stop()
        return 1

    was_listening = session.state == "listening"
    session.stop()

    segs = session.segments()
    words = sum(len(str(_seg_field(s, "text", "")).split()) for s in segs)
    try:
        avg_conf = (sum(float(_seg_field(s, "confidence", 0.0)) for s in segs)
                    / len(segs)) if segs else 0.0
    except Exception:
        avg_conf = 0.0
    _emit(out)
    _emit(out, "─ transcript summary ─")
    _emit(out, f"segments: {len(segs)}   words: {words}   "
               f"avg confidence: {avg_conf:.0%}   "
               f"provider: {session.provider_name}   "
               f"mode: {'simulated' if session.simulated else 'local ASR'}")
    if session.saved_files():
        _emit(out, "saved: " + ", ".join(session.saved_files()))
    if was_listening and not segs:
        _emit(out, "(nothing was transcribed this session)")
    _emit(out, "session ended cleanly (no audio was ever stored)")
    return 0
