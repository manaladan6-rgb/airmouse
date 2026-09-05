"""Live transcription sessions (v16.5, mission §6) — real pipeline, honest labels.

The v16.5 voice architecture ends at live transcription:
Microphone → preprocessing → VAD → streaming ASR → partial → final →
normalization → storage.  :class:`TranscribeSession` wraps the shipped
:class:`airmouse.transcription.LiveTranscriptionEngine` and the §6
controls around it:

* **Provider selection is THE REGISTRY's job (v16.5.2 §2/§3)**: the
  session calls the authoritative
  :class:`airmouse.asr_registry.ASRProviderRegistry.select()` — the
  exact same implementation ``doctor`` reports from, so the invariant
  ``doctor.active_provider == transcribe.selected_provider`` holds by
  construction.  A real LOCAL ASR engine is wrapped through
  :class:`airmouse.transcription.StreamingProviderAdapter`; the
  deterministic :class:`SimulatedStreamingProvider` runs ONLY when no
  usable provider initialized (mission §0 rule 10) — under a loud
  banner that says exactly WHY each engine was rejected and what to
  do about it.  The session never pretends a simulated stream is real
  ASR and never silently downgrades REAL → SIMULATED.
* **Works headless**: there is no microphone in a sandbox, so the REPL
  runs in TEXT-INPUT mode — typed lines are pushed through the FULL
  provider pipeline (push → word-by-word partials → finalize) exactly
  like spoken utterances would be.  ``--text`` forces this mode
  explicitly (labelled TEXT PRACTICE, §6).
* **MIC LIVE mode**: when the registry selected a real provider AND
  the unified microphone detector finds input hardware, a bare Enter
  records one REAL utterance (sounddevice-first capture → VAD → local
  ASR → partial → final, with timestamp; confidence shown as
  ``unavailable`` when the engine supplies none, §12).
* **Privacy by construction**: history is BOUNDED (default 500
  segments), nothing is written unless the user explicitly says
  ``save`` — and only the finalized TEXT segments are written to
  ``paths.transcripts_dir()/transcript-<unixts>.txt|.json``.  NO AUDIO
  IS EVER STORED.

``run_transcribe(...)`` is the interactive control loop
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

_SIMULATED_BANNER = ("SIMULATED provider — real transcription is not "
                     "available on this machine (see: airmouse "
                     "voice-status --verbose)")
_PHYSICAL_NOTE = ("live microphone capture is PHYSICAL TEST REQUIRED; "
                  "this session exercises the pipeline via text input")
_TEXT_INPUT_NOTE = "text-input mode (microphone capture needs hardware)"
_TEXT_PRACTICE_NOTE = ("TEXT PRACTICE — typed input through the real "
                       "pipeline; this is NOT microphone validation")

_COMMANDS_LINE = ("commands: pause | resume | save [json] | clear | status "
                  "| stop | quit — any other text is treated as a spoken "
                  "utterance")


# ---------------------------------------------------------------------------
# §7 — WHY no real provider (honest per-engine reasons, never hidden)
# ---------------------------------------------------------------------------

def _provider_failure_lines(prefer: Optional[str] = None) -> List[str]:
    """Per-engine WHY lines from THE registry when selection failed.

    Never raises, never invents — the reasons are exactly what the
    registry's discovery recorded (import failure, initialization
    failure, not installed, disabled by configuration).
    """
    lines: List[str] = []
    try:
        from .asr_registry import registry
        d = registry.discover()
        for name, row in d.items():
            if row.state == "DISABLED":
                lines.append(f"{name}: DISABLED — {row.reason}")
            elif row.state == "NOT_INSTALLED":
                lines.append(f"{name}: NOT INSTALLED")
            elif row.state == "FAILED":
                lines.append(f"{name}: INITIALIZATION FAILED — {row.reason}")
            else:
                lines.append(f"{name}: {row.state}")
    except Exception as exc:
        lines.append(f"provider registry unavailable: {exc}")
    return lines


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
                 engine: Optional[LiveTranscriptionEngine] = None,
                 prefer: Optional[str] = None) -> None:
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
                    # v16.5.2 §2/§3 — THE authoritative registry (the
                    # same instance doctor reports from)
                    from .asr_registry import registry
                    picked = registry.select(prefer=prefer)
                except Exception:
                    picked = None
            if picked is not None:
                stream = StreamingProviderAdapter(picked)
            else:
                stream = SimulatedStreamingProvider()

        self.stream = stream
        self.simulated = isinstance(stream, SimulatedStreamingProvider)

        # v16.5.2 §7 — WHY no real provider was selected (honest).
        # Only computed when simulation was NOT explicitly requested:
        # a forced --offline run must not claim the machine lacks ASR.
        self.simulate_requested = bool(simulate)
        self.selection_detail: List[str] = []
        if self.simulated and not simulate:
            self.selection_detail = _provider_failure_lines(prefer)

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

    @property
    def selected_provider(self) -> str:
        """Name of the provider this session ACTUALLY runs (invariant).

        When a real engine is active this equals
        ``doctor.active_provider`` (mission §16); when simulation is
        running it is ``"simulated_stream"`` — never a real engine's
        name.
        """
        return self.provider_name

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


def _safe_capture(capture: Callable[[], Any]
                  ) -> Optional[Tuple[str, float]]:
    """One ear call, normalized: ``None`` on failure/empty — never raises."""
    try:
        result = capture()
    except Exception:
        return None
    if result is None:
        return None
    if isinstance(result, str):
        text, conf = result, 0.0
    else:
        try:
            text, conf = result
        except Exception:
            return None
    text = str(text or "").strip()
    if not text:
        return None
    try:
        return text, max(0.0, min(1.0, float(conf)))
    except Exception:
        return text, 0.95


def _make_mic_capture(session: "TranscribeSession",
                      device: Optional[str] = None,
                      ) -> Tuple[Optional[Callable[[], Any]], str]:
    """Build the REAL microphone ear for the session's provider.

    v16.5.2 (mission §4/§5): sounddevice-first capture through
    :mod:`airmouse.mic_capture` (VAD-bounded utterances), with the
    legacy SpeechRecognition/PyAudio ear as fallback.  Returns
    ``(capture_or_None, reason)`` — ``reason`` explains WHY capture
    could not start so the caller can be honest instead of silently
    downgrading to text mode.  Never raises.
    """
    provider = getattr(getattr(session.stream, "provider", None),
                       "provider", getattr(session.stream, "provider", None))
    # StreamingProviderAdapter exposes the wrapped OfflineSpeechProvider
    # as ``.provider``; fall back to the registry's selection otherwise.
    if not isinstance(provider, OfflineSpeechProvider):
        try:
            from .asr_registry import registry
            provider = registry.select()
        except Exception:
            provider = None
    if provider is None:
        return None, "no local ASR provider is active"
    try:
        from .mic_capture import capture_transcript
    except Exception:
        return None, "microphone capture module unavailable"

    def _one() -> Any:
        return capture_transcript(provider, device=device, timeout_s=6.0)

    return _one, ""


def run_transcribe(out: Any = None,
                   input_fn: Optional[Callable[[str], str]] = None,
                   simulate: bool = False,
                   feed: Optional[Iterable[str]] = None,
                   mic_mode: Optional[bool] = None,
                   capture_fn: Optional[Callable[[], Any]] = None,
                   text_mode: Optional[bool] = None,
                   mic_device: Optional[str] = None,
                   prefer_provider: Optional[str] = None) -> int:
    """Interactive transcription session (§8 — auto real mic when possible).

    Mode resolution (honest, automatic, deterministic):

    * ``mic_mode=None`` (default) — MIC LIVE MODE is used automatically
      when a microphone AND a real local ASR engine are both available;
      otherwise the text-REPL runs (typed lines through the full
      provider pipeline, labelled TEXT PRACTICE / TEXT-INPUT).
    * ``text_mode=True`` (CLI ``--text``) forces TEXT PRACTICE — typed
      input through the real pipeline, explicitly labelled as NOT
      microphone validation (mission §6).
    * ``simulate=True`` (CLI ``--offline``) forces the simulated
      provider AND text mode — simulation never claims MIC LIVE.
    * ``capture_fn`` injects the ear (tests); it implies mic mode.
    * ``mic_device`` selects the input device (mission §13).
    * ``prefer_provider`` is the explicit provider choice (CLI
      ``--provider``, config ``[voice] asr_provider``, env
      ``AIRMOUSE_ASR_PROVIDER`` — the registry resolves precedence).

    When no real provider can be selected the banner says so loudly and
    shows WHY each engine was rejected + the FIX (mission §7) — the
    session NEVER silently downgrades REAL LOCAL ASR to SIMULATED.

    Returns 0 on a clean stop/quit/EOF; 1 only on an unexpected error.
    """
    # §13 — silence third-party noise (never AirMouse's own errors)
    try:
        from .quiet import quiet_third_party_logs
        quiet_third_party_logs()
    except Exception:
        pass
    try:
        session = TranscribeSession(simulate=simulate,
                                    prefer=prefer_provider)
    except Exception as exc:  # pragma: no cover - defensive
        _emit(out, f"transcribe session failed to start: {exc}")
        return 1

    # §8 — automatic mode resolution (v16.5.2: deterministic + honest)
    live = mic_mode
    if simulate:
        live = False  # simulate forces text mode — regression-proof
    if text_mode:
        live = False
    if live is None:
        live = False
        if not simulate and not session.simulated:
            try:
                from .asr_registry import detect_microphone
                live = bool(detect_microphone().get("available"))
            except Exception:
                live = False
    if capture_fn is not None:
        live = True
    capture: Optional[Callable[[], Any]] = capture_fn
    mic_fail_reason = ""
    if live and capture is None:
        capture, mic_fail_reason = _make_mic_capture(session,
                                                     device=mic_device)
        if capture is None:      # mic capture could not start — honest
            live = False
            _emit(out, f"⚠ microphone capture could not start — falling "
                       f"back to TEXT-INPUT mode ({mic_fail_reason})")

    _emit(out, "══ LIVE TRANSCRIPTION ══")
    if session.simulated:
        _emit(out, f"provider: {session.provider_name}")
        if session.simulate_requested:
            _emit(out, "⚠ SIMULATED provider — requested via --offline "
                       "(deterministic test provider; this is NOT real "
                       "ASR)")
        else:
            _emit(out, f"⚠ {_SIMULATED_BANNER}")
            # v16.5.2 §7 — WHY + FIX, never a bare "using defaults"
            for why in session.selection_detail:
                _emit(out, f"  WHY: {why}")
            _emit(out, "  FIX: airmouse voice-status --verbose   (install: "
                       "pip install pocketsphinx SpeechRecognition)")
    else:
        _emit(out, f"provider: {session.provider_name} (local ASR engine)")
    if live:
        _emit(out, "mode: MIC LIVE — speak one utterance per Enter; "
                   "typed commands still work")
    elif text_mode and not session.simulated:
        _emit(out, "mode: TEXT PRACTICE")
        _emit(out, _TEXT_PRACTICE_NOTE)
    else:
        _emit(out, _TEXT_INPUT_NOTE)
        if not session.simulated:
            _emit(out, f"⚠ {_PHYSICAL_NOTE}")
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
                if live and capture is not None:
                    got = _safe_capture(capture)
                    if got is None:
                        _emit(out, "  (nothing usable captured — noise or "
                                   "timeout; nothing was recorded)")
                        continue
                    text, conf = got
                    seg = session.utterance(text, conf)
                    if seg is not None:
                        _emit(out, f'  [{_hhmmss(seg.end_ts)}] '
                                   f"{_conf_str(seg)} "
                                   f'"{seg.text}"  (final)')
                    continue
                continue
            seg = session.utterance(line)
            if seg is not None:
                _emit(out, f'  [{_hhmmss(seg.end_ts)}] '
                           f"{_conf_str(seg)} "
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
    known = [float(_seg_field(s, "confidence", 0.0)) for s in segs
             if _seg_field(s, "confidence_known", True)]
    try:
        avg_conf = (sum(known) / len(known)) if known else 0.0
    except Exception:
        avg_conf = 0.0
    avg_str = f"{avg_conf:.0%}" if known else "unavailable"
    _emit(out)
    _emit(out, "─ transcript summary ─")
    _emit(out, f"segments: {len(segs)}   words: {words}   "
               f"avg confidence: {avg_str}   "
               f"provider: {session.provider_name}   "
               f"mode: {'simulated' if session.simulated else 'local ASR'}"
               f"{' + mic live' if live else ''}")
    if session.saved_files():
        _emit(out, "saved: " + ", ".join(session.saved_files()))
    if was_listening and not segs:
        _emit(out, "(nothing was transcribed this session)")
    _emit(out, "session ended cleanly (no audio was ever stored)")
    return 0


def _conf_str(seg: Any) -> str:
    """§12 — real confidence as %, otherwise the honest 'unavailable'."""
    if not _seg_field(seg, "confidence_known", True):
        return "conf: unavailable"
    try:
        conf = max(0.0, min(1.0, float(_seg_field(seg, "confidence", 0.0))))
        return f"{conf:.0%}"
    except Exception:
        return "conf: unavailable"
