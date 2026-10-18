"""
airmouse.offline_voice — v10 Offline Voice Engine 🎙️
=====================================================

Complete local voice-control subsystem (mission §4 + §5).  The core
command system requires NO LLM and NO network: deterministic grammar
templates (:mod:`airmouse.voice_commands`) + the v9 NL pattern table.

Architecture (§4)::

    AudioSource ──► preprocessing ──► VAD ──► (wake word) ──►
    OfflineSpeechProvider.transcribe ──► normalization ──►
    grammar / NL intent resolution ──► context resolution ──►
    safety ──► action ──► verification      (downstream of the bus)

Voice modes (§5):

    COMMAND    every utterance must resolve to a deterministic command
    DICTATION  utterances accumulate into text (committed on marker)
    HYBRID     command first; otherwise dictation when it looks like prose

Pluggable offline ASR providers — the engine NEVER touches the network:

    SimulatedSpeechProvider  deterministic scripted utterances (tests/CI)
    PocketSphinxProvider     CMU Sphinx via SpeechRecognition (offline)
    VoskProvider             vosk models (offline)
    WhisperProvider          local whisper / faster_whisper (offline)

`detect_providers()` reports which are importable on this machine; with
none available the engine degrades to transcript injection
(`feed_transcript`) only — the rest of the stack keeps working.

Everything here is pure Python + guarded imports.  Feeding explicit
timestamps makes the whole pipeline deterministic for tests.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import audioop  # stdlib (deprecated in 3.13 but present in 3.12; guarded)
import math
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:  # package-relative (normal import path)
    from .interfaces import (ContextState, Event, EventKind,
                             Intent, IntentType, Modality, VoiceMode,
                             now_ts)
    from . import voice_commands as _vc
    from . import nl_control as _nl
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (ContextState, Event, EventKind,
                                     Intent, IntentType, Modality, VoiceMode,
                                     now_ts)
    import airmouse.voice_commands as _vc
    import airmouse.nl_control as _nl

__all__ = [
    "Transcript", "OfflineSpeechProvider", "SimulatedSpeechProvider",
    "PocketSphinxProvider", "VoskProvider", "WhisperProvider",
    "detect_providers", "provider_registry",
    "resolve_vosk_model_path",  # v16.5.3 — deterministic model lookup
    "EnergyVAD", "WakeWordGate", "DictationBuffer",
    "OfflineVoiceEngine", "voice_match_to_intent",
    "WAKE_MARKERS",
    # voice-truth banner helpers (TF-005) — wired into __main__
    "BANNER_LIVE", "BANNER_GRAMMAR_ONLY", "CLOUD_DISCLOSURE",
    "session_banner_state", "grammar_only_reason", "cloud_voice_banner",
]

# audioop may be absent (py3.13+); fall back to a tiny RMS implementation
try:
    audioop  # noqa: F821
    _HAS_AUDIOOP = True
except NameError:  # pragma: no cover
    _HAS_AUDIOOP = False


def _rms(data: bytes, width: int = 2) -> float:
    """Root-mean-square of a PCM buffer (stdlib audioop or fallback)."""
    if not data:
        return 0.0
    if _HAS_AUDIOOP:
        try:
            return float(audioop.rms(data, width))
        except Exception:
            pass
    try:
        import struct
        fmt = {1: "b", 2: "h", 4: "i"}.get(width, "h")
        count = len(data) // struct.calcsize(fmt)
        if count == 0:
            return 0.0
        samples = struct.unpack("<%d%s" % (count, fmt),
                                data[:count * struct.calcsize(fmt)])
        return math.sqrt(sum(s * s for s in samples) / count)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Transcript + provider abstraction
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# vosk model resolution (v16.5.3) — deterministic, offline, honest
# ---------------------------------------------------------------------------

def _read_voice_config_str(key: str) -> str:
    """Read ``[voice] <key>`` from the TOML config (``""`` when absent).

    Tiny guarded duplicate of the registry's reader — this module sits
    BELOW asr_registry in the import graph (asr_registry imports this
    module), so it must never import back.  Never raises.
    """
    try:
        try:
            import tomllib as _toml  # py311+
        except Exception:  # pragma: no cover - py<3.11
            import tomli as _toml  # type: ignore[no-redef]
        from . import paths as _paths
        cpath = _paths.config_file()
        if not cpath or not os.path.exists(cpath):
            return ""
        with open(cpath, "rb") as fh:
            data = _toml.load(fh)
        v = data.get("voice", {})
        if isinstance(v, dict):
            return str(v.get(key, "") or "").strip()
    except Exception:
        return ""
    return ""


def _looks_like_vosk_model(path: str) -> bool:
    """True when ``path`` is a directory holding real Vosk model files.

    A valid model folder contains ``conf/`` plus ``am/`` (acoustics) or
    ``graph/`` — data files only, never executables.  Never raises.
    """
    try:
        if not path or not os.path.isdir(path):
            return False
        if not os.path.isdir(os.path.join(path, "conf")):
            return False
        return (os.path.isdir(os.path.join(path, "am"))
                or os.path.isdir(os.path.join(path, "graph")))
    except Exception:
        return False


def resolve_vosk_model_path(explicit: str = "") -> str:
    """Deterministically locate a local Vosk acoustics model (v16.5.3).

    Precedence (first hit wins):

    1. ``explicit`` constructor argument (non-empty)
    2. env ``AIRMOUSE_VOSK_MODEL_PATH``
    3. config ``[voice] vosk_model_path``
    4. the shipped model dir (``models/vosk/``, then any
       ``vosk-model-*`` directory inside ``models/``)

    Returns ``""`` when nothing resolves — the honest "no local model"
    answer (``airmouse voice-setup`` installs one with consent).  No
    network is ever touched.  Never raises.
    """
    try:
        for cand in (str(explicit or "").strip(),
                     os.environ.get("AIRMOUSE_VOSK_MODEL_PATH", "").strip(),
                     _read_voice_config_str("vosk_model_path")):
            if cand and _looks_like_vosk_model(cand):
                return os.path.abspath(cand)
        try:
            from . import paths as _paths
        except Exception:
            return ""
        fixed = _paths.vosk_model_dir()
        if _looks_like_vosk_model(fixed):
            return fixed
        root = _paths.models_dir()
        try:
            if os.path.isdir(root):
                for name in sorted(os.listdir(root)):
                    sub = os.path.join(root, name)
                    if name.lower().startswith("vosk") and \
                            _looks_like_vosk_model(sub):
                        return sub
        except Exception:
            return ""
    except Exception:
        return ""
    return ""


@dataclass
class Transcript:
    """One recognized utterance (provider output).

    ``confidence`` is ``Optional[float]``: ``None`` means the provider
    did NOT supply a real confidence.  A number is present ONLY when
    the engine actually reported one — fabricated percentages (hard-
    coded 0.7/0.75 placeholders) are forbidden (TF-005 §12).  Renderers
    treat ``None`` / ``confidence_known=False`` as "confidence:
    unavailable".
    """

    text: str = ""
    confidence: Optional[float] = None   # None = UNKNOWN — never invented
    provider: str = ""
    duration: float = 0.0
    timestamp: float = field(default_factory=now_ts)
    #: False when the provider does NOT supply a real confidence (e.g.
    #: PocketSphinx, or a vosk result without a confidence field).
    #: Never fabricate a percentage; keep this in sync with ``confidence``.
    confidence_known: bool = True


class OfflineSpeechProvider:
    """Protocol for pluggable offline ASR engines (§4).

    Implementations MUST be fully local (networking disabled must not
    change behaviour) and MUST NOT raise: ``transcribe`` returns a
    :class:`Transcript` with empty text when recognition fails.
    """

    name: str = "provider"

    def available(self) -> bool:  # pragma: no cover - interface
        return False

    def transcribe(self, audio: Any) -> Transcript:  # pragma: no cover
        """Transcribe PCM audio bytes (or provider-native audio object)."""
        return Transcript(provider=self.name)


class SimulatedSpeechProvider(OfflineSpeechProvider):
    """Deterministic scripted provider — the test/CI workhorse (§22).

    ``push(text, confidence)`` queues utterances; ``transcribe`` pops
    them in order.  With an empty queue it returns silence.  Fully
    deterministic: no threads, no randomness.
    """

    name = "simulated"

    def __init__(self) -> None:
        self._queue: List[Transcript] = []
        self._lock = threading.Lock()

    def available(self) -> bool:
        return True

    def push(self, text: str, confidence: float = 0.95,
             duration: float = 0.8) -> None:
        with self._lock:
            self._queue.append(Transcript(
                text=str(text or ""), confidence=float(confidence),
                provider=self.name, duration=float(duration)))

    def transcribe(self, audio: Any) -> Transcript:
        with self._lock:
            if self._queue:
                return self._queue.pop(0)
        return Transcript(provider=self.name)


class PocketSphinxProvider(OfflineSpeechProvider):
    """CMU PocketSphinx via SpeechRecognition's OFFLINE sphinx backend.

    Never calls recognize_google.  ``available`` is a REAL capability
    test (v16.5.2 §2): it actually imports ``pocketsphinx`` and runs the
    shared registry initialization probe (a genuine Decoder build with
    the default model — the exact object ``recognize_sphinx`` drives).
    A bare ``hasattr(r, "recognize_sphinx")`` check was the root cause
    of the v16.5.1 Windows divergence: it is True whenever
    SpeechRecognition is installed even when pocketsphinx itself is
    broken, so doctor reported a provider that could not run.
    """

    name = "pocketsphinx"

    def __init__(self, recognizer: Any = None) -> None:
        self._sr = None
        self._recognizer = recognizer
        try:
            import speech_recognition as sr  # optional dependency
            self._sr = sr
        except Exception:
            self._sr = None

    def available(self) -> bool:
        if self._sr is None:
            return False
        try:
            r = self._recognizer or self._sr.Recognizer()
            if not hasattr(r, "recognize_sphinx"):
                return False
        except Exception:
            return False
        # v16.5.2: the REAL probe — cached in asr_registry (≈0.25 s)
        try:
            from .asr_registry import PROBE_POCKETSPHINX
            PROBE_POCKETSPHINX()
            return True
        except Exception:
            return False

    def transcribe(self, audio: Any) -> Transcript:
        try:
            r = self._recognizer or self._sr.Recognizer()
            text = r.recognize_sphinx(audio)
            # §12: PocketSphinx exposes no reliable confidence —
            # report None (unknown) with confidence_known=False,
            # never a made-up %
            return Transcript(text=str(text or ""), confidence=None,
                              provider=self.name, confidence_known=False)
        except Exception:
            return Transcript(provider=self.name, confidence_known=False)


class VoskProvider(OfflineSpeechProvider):
    """Vosk offline ASR (guarded import; model path configurable).

    v16.5.3 — the acoustics model is resolved DETERMINISTICALLY
    (constructor arg > env ``AIRMOUSE_VOSK_MODEL_PATH`` > config
    ``[voice] vosk_model_path`` > AirMouse ``models/`` dir) via
    :func:`resolve_vosk_model_path`.  With no local model the provider
    reports unavailable — the honest "installed, no model" state that
    ``airmouse voice-setup`` fixes; nothing is ever downloaded here.
    """

    name = "vosk"

    def __init__(self, model_path: str = "") -> None:
        self.model_path = str(model_path or "")
        self._vosk = None
        self._model = None
        try:
            from vosk import Model  # optional dependency
            self._vosk = Model
        except Exception:
            self._vosk = None

    def available(self) -> bool:
        if self._vosk is None:
            return False
        try:
            if self._model is None:
                path = resolve_vosk_model_path(self.model_path)
                if not path:
                    return False  # honest: engine present, model missing
                self.model_path = path
                self._model = self._vosk(path)
            return self._model is not None
        except Exception:
            return False

    @staticmethod
    def _result_confidence(obj: Any) -> Optional[float]:
        """The REAL confidence a vosk result reported, or None.

        Vosk only includes ``confidence`` in some result payloads; when
        it is absent (or not a positive number) the honest answer is
        None — never a placeholder percentage.
        """
        try:
            v = obj.get("confidence")
            if isinstance(v, (int, float)) and float(v) > 0.0:
                return float(v)
        except Exception:
            pass
        return None

    def transcribe(self, audio: Any) -> Transcript:
        if not self.available():
            return Transcript(provider=self.name, confidence_known=False)
        try:
            import json
            import wave
            rec = None
            try:
                from vosk import KaldiRecognizer
                rec = KaldiRecognizer(self._model, 16000)
            except Exception:
                return Transcript(provider=self.name, confidence_known=False)
            conf: Optional[float] = None
            if isinstance(audio, str) and audio.endswith(".wav"):
                with wave.open(audio, "rb") as wf:
                    text = ""
                    while True:
                        data = wf.readframes(4000)
                        if not data:
                            break
                        if rec.AcceptWaveform(data):
                            part = json.loads(rec.Result() or "{}")
                            text += " " + str(part.get("text", ""))
                            conf = self._result_confidence(part) or conf
                    final = json.loads(rec.FinalResult() or "{}")
                    text += " " + str(final.get("text", ""))
                    conf = self._result_confidence(final) or conf
            else:  # raw PCM bytes
                if rec.AcceptWaveform(bytes(audio)):
                    part = json.loads(rec.Result() or "{}")
                    text = str(part.get("text", ""))
                    conf = self._result_confidence(part)
                else:
                    final = json.loads(rec.FinalResult() or "{}")
                    text = str(final.get("text", ""))
                    conf = self._result_confidence(final)
            return Transcript(text=re.sub(r"\s+", " ", text).strip(),
                              confidence=conf,
                              confidence_known=conf is not None,
                              provider=self.name)
        except Exception:
            return Transcript(provider=self.name, confidence_known=False)


class WhisperProvider(OfflineSpeechProvider):
    """Local whisper / faster_whisper (guarded import, model optional)."""

    name = "whisper"

    def __init__(self, model_name: str = "base.en") -> None:
        self.model_name = str(model_name or "base.en")
        self._whisper = None
        self._model = None
        self._fw = None
        try:
            import whisper  # optional dependency
            self._whisper = whisper
        except Exception:
            try:
                from faster_whisper import WhisperModel  # optional
                self._fw = WhisperModel
            except Exception:
                self._fw = None

    def available(self) -> bool:
        return self._whisper is not None or self._fw is not None

    def transcribe(self, audio: Any) -> Transcript:
        try:
            if self._whisper is not None:
                self._model = self._model or self._whisper.load_model(
                    self.model_name)
                result = self._model.transcribe(audio)
                # whisper's avg_logprob is NOT a 0-1 confidence — mapping
                # it would be fabrication; report unknown honestly
                return Transcript(
                    text=str(result.get("text", "")).strip(),
                    confidence=None, provider=self.name,
                    confidence_known=False)
            if self._fw is not None:
                self._model = self._model or self._fw(self.model_name)
                segments, _info = self._model.transcribe(audio)
                text = " ".join(str(s.text).strip() for s in segments)
                return Transcript(text=text.strip(), confidence=None,
                                  provider=self.name,
                                  confidence_known=False)
        except Exception:
            pass
        return Transcript(provider=self.name, confidence_known=False)


#: provider name → factory (registration point for third-party engines)
provider_registry: Dict[str, Callable[..., OfflineSpeechProvider]] = {
    "simulated": SimulatedSpeechProvider,
    "pocketsphinx": PocketSphinxProvider,
    "vosk": VoskProvider,
    "whisper": WhisperProvider,
}


def detect_providers() -> Dict[str, bool]:
    """Which offline providers are usable on THIS machine (honest report).

    v16.5.2: delegated to the authoritative
    :class:`airmouse.asr_registry.ASRProviderRegistry` — the ONE
    detection implementation doctor, transcribe, voice-status and this
    function all share.  ``True`` means the engine genuinely passed its
    capability probe (real initialization for PocketSphinx; verified
    import for vosk/whisper with the model loading on first use).
    """
    out: Dict[str, bool] = {}
    try:
        from .asr_registry import registry
        discovered = registry.discover()
        for name, row in discovered.items():
            out[name] = row.state in ("INITIALIZABLE", "AVAILABLE", "ACTIVE")
    except Exception:
        # registry import/probe itself failed — fall back to the raw
        # provider constructors rather than reporting nothing at all
        for name, factory in provider_registry.items():
            try:
                out[name] = bool(factory().available())
            except Exception:
                out[name] = False
    out.setdefault("simulated", True)  # ships with the package
    return out


# ---------------------------------------------------------------------------
# VAD + wake word
# ---------------------------------------------------------------------------


class EnergyVAD:
    """Hysteresis energy voice-activity detector on PCM chunks.

    Deterministic: state depends only on the chunk sequence.  ``feed``
    returns True while speech is active; ``speech_ended`` fires exactly
    once per utterance (falling edge).
    """

    def __init__(self,
                 start_threshold: float = 420.0,
                 end_threshold: float = 260.0,
                 end_silence: float = 0.7,
                 sample_rate: int = 16000,
                 frame_ms: int = 30) -> None:
        self.start_threshold = float(start_threshold)
        self.end_threshold = float(end_threshold)
        self.end_silence = float(end_silence)
        self.sample_rate = int(sample_rate)
        self.frame_s = max(0.01, frame_ms / 1000.0)
        self.active = False
        self.silence_run = 0.0
        self.speech_run = 0.0
        self.speech_ended = False

    def reset(self) -> None:
        self.active = False
        self.silence_run = 0.0
        self.speech_run = 0.0
        self.speech_ended = False

    def feed(self, chunk: bytes, now: Optional[float] = None) -> bool:
        """Process one PCM chunk; update state machine.  Never raises."""
        level = _rms(chunk)
        dt = self.frame_s
        self.speech_ended = False
        if not self.active:
            if level >= self.start_threshold:
                self.active = True
                self.speech_run = dt
                self.silence_run = 0.0
            else:
                self.speech_run = 0.0
        else:
            if level >= self.end_threshold:
                self.silence_run = 0.0
                self.speech_run += dt
            else:
                self.silence_run += dt
                if self.silence_run >= self.end_silence:
                    self.active = False
                    self.speech_run = 0.0
                    self.speech_ended = True
        return self.active


class WakeWordGate:
    """Optional wake-word gate (§4).  When ``required`` is False the
    gate passes everything through with the text unchanged."""

    WORDS = ("airmouse", "air mouse", "hey airmouse", "hey air mouse")
    ARM_WINDOW = 10.0  # seconds the gate stays armed after hearing the word

    def __init__(self, required: bool = False) -> None:
        self.required = bool(required)
        self._armed_until = -1e9

    def check(self, text: str, now: float) -> Tuple[bool, str]:
        """Return (passed, remainder).  Pure given (text, now)."""
        norm = _nl.normalize_text(text)
        if not self.required:
            return True, norm
        for w in self.WORDS:
            if w in norm:
                self._armed_until = float(now) + self.ARM_WINDOW
                remainder = norm.replace(w, " ", 1).strip()
                return True, remainder
        if float(now) <= self._armed_until:
            return True, norm
        return False, ""


# ---------------------------------------------------------------------------
# Dictation
# ---------------------------------------------------------------------------


#: phrases that commit the dictation buffer (§5)
WAKE_MARKERS = ("commit", "new paragraph", "submit text", "end dictation")

_PUNCT_END = re.compile(r"[.!?]\s*$")


class DictationBuffer:
    """Accumulates dictated text; commits on marker / punctuation /
    max-length.  Deterministic; never exceeds ``max_chars``."""

    def __init__(self, max_chars: int = 500) -> None:
        self.max_chars = int(max_chars)
        self._parts: List[str] = []

    @property
    def pending(self) -> str:
        return " ".join(self._parts).strip()

    def reset(self) -> None:
        self._parts.clear()

    def feed(self, text: str) -> Tuple[str, str]:
        """Add one utterance.  Returns (committed_text, still_pending).

        Commit triggers (checked in order): an explicit marker phrase,
        sentence-final punctuation in the RAW text, or exceeding
        ``max_chars``.  Punctuation is checked before normalization
        (normalization strips it).
        """
        raw = str(text or "").strip()
        t = _nl.normalize_text(raw)
        if not t:
            return "", self.pending
        marker = next((m for m in WAKE_MARKERS
                       if t == m or t.endswith(" " + m)), None)
        if marker is not None:
            body = t[: t.rfind(marker)].strip() if t != marker else ""
            if body:
                self._parts.append(body)
            committed = self._commit()
            return committed, self.pending
        self._parts.append(t)
        if len(self.pending) >= self.max_chars:
            committed = self._commit()
            return committed, self.pending
        if _PUNCT_END.search(raw):
            committed = self._commit()
            return committed, self.pending
        return "", self.pending

    def _commit(self) -> str:
        committed = self.pending
        self._parts.clear()
        return committed


# ---------------------------------------------------------------------------
# intent conversion
# ---------------------------------------------------------------------------

_NUM = _vc.NUM_WORDS


def _int_or_word(v: Any) -> Optional[int]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s.isdigit():
        return int(s)
    return _NUM.get(s)


def voice_match_to_intent(match: _vc.VoiceCommandMatch,
                          context: Optional[ContextState] = None,
                          confidence_scale: float = 1.0,
                          now: Optional[float] = None) -> Optional[Intent]:
    """Map a deterministic :class:`VoiceCommandMatch` to an :class:`Intent`.

    Entity → params mapping per command family; deictic targets
    ("that"/"this"/"it") resolve against the :class:`ContextState`.
    Returns None for non-commands.  Never raises.
    """
    if not match.is_command:
        return None
    now = float(now if now is not None else now_ts())
    it = match.intent
    ent = dict(match.entities or {})
    params: Dict[str, Any] = dict(match.params or {})
    target = None
    point = None

    def _resolve_ref(ref: str) -> None:
        nonlocal target, point
        if context is not None and ref:
            resolved = context.resolve_reference(ref)
            if resolved is not None:
                target = resolved
                point = resolved.center

    conf = max(0.0, min(1.0, match.confidence * float(confidence_scale)))

    if match.name in ("click", "double_click", "right_click", "middle_click"):
        _resolve_ref(ent.get("target", ""))
    elif match.name == "open_app":
        params["application"] = ent.get("app", "")
    elif match.name == "close_app":
        params["application"] = ent.get("app", "")
    elif match.name in ("focus_app", "focus_window"):
        params["what"] = ent.get("app") or ent.get("name") or ""
    elif match.name == "open_url":
        params["url"] = ent.get("url", "")
    elif match.name == "search_for":
        params["query"] = ent.get("query", "")
    elif match.namespace is _vc.CommandNamespace.FILES:
        params["op"] = params.get("op", match.name.replace("_file", ""))
        if "name" in ent:
            params["name"] = ent["name"]
        if "new_name" in ent:
            params["new_name"] = ent["new_name"]
    elif it is IntentType.SWITCH_TAB:
        n = _int_or_word(ent.get("number"))
        params["index"] = n if n is not None else 1
    elif it is IntentType.MEDIA:
        pass  # action already in params
    elif it is IntentType.VOLUME or it is IntentType.BRIGHTNESS:
        if "state" in ent:
            params["direction"] = ent["state"]
    elif it is IntentType.BLUETOOTH:
        params["state"] = ent.get("state", "")
    elif it is IntentType.MOVE and ent.get("direction"):
        params["direction"] = ent["direction"]
    elif match.name == "cursor":
        if "start of line" in (ent.get("direction") or ""):
            params["what"] = "cursor"
            params["keys"] = ["home"]
        elif "end of line" in (ent.get("direction") or ""):
            params["what"] = "cursor"
            params["keys"] = ["end"]
        else:
            params["what"] = "cursor"
            params["keys"] = [_DIR_KEY.get(ent.get("direction", ""), "left")]

    return Intent(
        type=it, target=target, point=point, params=params, confidence=conf,
        sources=Modality.VOICE, utterance=match.text, timestamp=now,
    )


_DIR_KEY = {"up": "up", "down": "down", "left": "left", "right": "right"}


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class OfflineVoiceEngine:
    """v10 offline voice orchestrator (mode-aware, bus-connected).

    Deterministic usage (tests / CI)::

        bus = EventBus()
        engine = OfflineVoiceEngine({"mode": "command"}, bus=bus)
        engine.feed_transcript("open firefox", 0.95, now=1.0)
        ev = engine.poll()            # -> Event(VOICE_COMMAND …)
        intent = engine.last_intent   # -> Intent(OPEN, application=firefox)

    Live audio path::

        engine.start_audio(provider)          # optional thread capture
        ... feed_audio(chunk) from your own loop, or let start_audio run
        engine.poll() / engine.last_intent / engine.last_committed_text
    """

    def __init__(self,
                 config: Optional[Dict[str, Any]] = None,
                 bus: Optional[Any] = None,
                 provider: Optional[OfflineSpeechProvider] = None,
                 context: Optional[ContextState] = None) -> None:
        cfg = dict(config or {})
        self.config = cfg
        self.bus = bus
        self.context = context or ContextState()
        self.provider = provider or SimulatedSpeechProvider()

        mode_raw = str(cfg.get("mode", "command")).lower()
        try:
            self.mode = VoiceMode(mode_raw)
        except ValueError:
            self.mode = VoiceMode.COMMAND

        # tuning knobs (deterministic defaults)
        self.command_min_confidence = float(cfg.get("command_min_confidence", 0.62))
        self.hybrid_command_confidence = float(
            cfg.get("hybrid_command_confidence", 0.72))
        self.hybrid_dictation_min_words = int(
            cfg.get("hybrid_dictation_min_words", 3))
        self.wake_word_required = bool(cfg.get("wake_word_required", False))
        self.cooldown = float(cfg.get("cooldown", 0.3))
        self.dedup_window = float(cfg.get("dedup_window", 1.2))

        self.vad = EnergyVAD(
            start_threshold=float(cfg.get("vad_start_threshold", 420.0)),
            end_threshold=float(cfg.get("vad_end_threshold", 260.0)),
            end_silence=float(cfg.get("vad_end_silence", 0.7)),
        )
        self.wake = WakeWordGate(required=self.wake_word_required)
        self.dictation = DictationBuffer(
            max_chars=int(cfg.get("dictation_max_chars", 500)))

        # state
        self.last_transcript = ""
        self.last_command = ""
        self.last_confidence: Optional[float] = 0.0
        self.last_confidence_known = True   # False when ASR gave no number
        self.last_intent: Optional[Intent] = None
        self.last_committed_text = ""
        self.enabled = True
        self.latency_ms = 0.0
        self._events: List[Event] = []
        self._lock = threading.RLock()
        self._last_fire = -1e9
        self._last_norm = ""
        self._last_norm_at = -1e9
        self._audio_thread: Optional[threading.Thread] = None
        self._stop_audio = threading.Event()
        self.on_speech: Optional[Callable[[str], None]] = None
        # TF-004: the REAL PCM of the in-flight utterance (VAD-selected)
        self._utterance: List[bytes] = []
        # honest rejection accounting for feed_audio payloads
        self.rejected_audio = 0
        self.last_audio_reject_reason = ""

    # -- mode switching -------------------------------------------------------

    def set_mode(self, mode: Any) -> VoiceMode:
        """Switch voice mode live (string or VoiceMode)."""
        if isinstance(mode, VoiceMode):
            self.mode = mode
        else:
            try:
                self.mode = VoiceMode(str(mode).lower())
            except ValueError:
                pass
        self.dictation.reset()
        return self.mode

    # -- transcript ingestion (deterministic core) -----------------------------

    def feed_transcript(self, text: str, confidence: Optional[float] = 0.95,
                        now: Optional[float] = None) -> List[Event]:
        """Inject one transcript (from any ASR source) through the full
        mode-aware pipeline.  Returns the Events produced this call.

        This is the deterministic entry point — the audio path just ends
        here after VAD segmentation.  ``confidence=None`` (the ASR did
        not report one) is accepted honestly: the pipeline weights it
        as 0.0 and ``last_confidence_known`` records the uncertainty —
        no number is ever invented.
        """
        t0 = float(now if now is not None else now_ts())
        conf_known = confidence is not None
        conf = float(confidence) if conf_known else 0.0
        with self._lock:
            self.last_confidence_known = conf_known
            self._events = []
            if not self.enabled:
                return []
            try:
                self._handle_transcript(str(text or ""), conf, t0)
            except Exception:
                pass  # the voice layer must never crash the interaction loop
            return list(self._events)

    # -- audio path --------------------------------------------------------------

    def feed_audio(self, chunk: Optional[bytes],
                   now: Optional[float] = None) -> bool:
        """Feed one REAL PCM chunk through the VAD (TF-004, rebuilt).

        The chunks of each utterance (VAD onset → end) are buffered
        VERBATIM; on utterance end the concatenated REAL PCM is handed
        to the provider — ``transcribe(None)`` is FORBIDDEN on this
        path and structurally tested against.

        Honesty contract:

        * ``None`` / empty payloads are REJECTED here with a recorded
          reason — they never reach the engine's ``transcribe``, and no
          exception ever leaks out of this method.
        * Returns True when the chunk was accepted into the utterance
          buffer (or completed one), False when it was rejected.
        """
        try:
            if chunk is None:
                self.rejected_audio += 1
                self.last_audio_reject_reason = \
                    "rejected: None audio payload (no real PCM to transcribe)"
                return False
            data = bytes(chunk)
            if not data:
                self.rejected_audio += 1
                self.last_audio_reject_reason = \
                    "rejected: empty audio payload (zero-length PCM)"
                return False
            t0 = float(now if now is not None else now_ts())
            was_active = self.vad.active
            self.vad.feed(data, t0)
            if self.vad.active:
                # inside an utterance (including the onset chunk):
                # buffer the REAL PCM verbatim
                self._utterance.append(data)
            if was_active and self.vad.speech_ended:
                # the closing (silence) chunk is real captured audio too
                self._utterance.append(data)
                pcm = b"".join(self._utterance)
                self._utterance = []
                if not pcm:
                    return False   # honest: nothing real was captured
                tr = self.provider.transcribe(pcm)
                if tr and tr.text:
                    tr.timestamp = t0
                    self.feed_transcript(tr.text, tr.confidence, t0)
            return True
        except Exception as exc:
            self.last_audio_reject_reason = \
                f"audio path error: {type(exc).__name__}: {exc}"
            return False   # the voice layer must never crash the caller

    def start_audio(self, source: Any = None,
                    chunk_ms: int = 30) -> bool:
        """Optional background capture thread over a ``source`` that has
        ``read(n) -> bytes``.  The engine itself stays usable without it.
        """
        if source is None or self._audio_thread is not None:
            return False
        self._stop_audio.clear()

        def _loop() -> None:
            bytes_per_chunk = int(16000 * 2 * chunk_ms / 1000)
            while not self._stop_audio.is_set():
                try:
                    data = source.read(bytes_per_chunk)
                    if not data:
                        self._stop_audio.wait(0.05)
                        continue
                    self.feed_audio(data)
                except Exception:
                    self._stop_audio.wait(0.2)

        self._audio_thread = threading.Thread(
            target=_loop, name="OfflineVoiceAudio", daemon=True)
        self._audio_thread.start()
        return True

    def stop_audio(self) -> None:
        self._stop_audio.set()
        th = self._audio_thread
        self._audio_thread = None
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=1.5)

    # -- pipeline core ----------------------------------------------------------

    def _emit(self, event: Event) -> None:
        """Record + publish one event."""
        self._events.append(event)
        if self.bus is not None:
            try:
                self.bus.publish(event)
            except Exception:
                pass

    def _handle_transcript(self, text: str, confidence: float,
                           now: float) -> None:
        # 1. wake-word gate
        passed, remainder = self.wake.check(text, now)
        if not passed:
            return
        norm = remainder or _nl.normalize_text(text)
        if not norm:
            return

        # 2. dedup (same normalized text within dedup_window)
        if norm == self._last_norm and (now - self._last_norm_at) < self.dedup_window:
            return
        self._last_norm = norm
        self._last_norm_at = now

        # 3. cooldown between fired commands
        if (now - self._last_fire) < self.cooldown:
            return

        # 4. dictation-mode text events (§5)  (RAW text: punctuation is a
        #    commit trigger and normalization strips it)
        if self.mode is VoiceMode.DICTATION:
            committed, pending = self.dictation.feed(text)
            self.last_transcript = norm
            self.last_confidence = confidence
            if self.on_speech:
                try:
                    self.on_speech(norm)
                except Exception:
                    pass
            if committed:
                self.last_committed_text = committed
                self._last_fire = now
                self._emit(Event(
                    kind=EventKind.VOICE_TEXT, modality=Modality.VOICE,
                    confidence=confidence, source="offline_voice:dictation",
                    payload={"text": committed, "pending": pending},
                    timestamp=now))
            return

        # 5. deterministic grammar match (§6/§7) — NO LLM anywhere
        match = _vc.match_command_grammar(norm)
        if not match.is_command:
            # 5b. v9 NL pattern table fallback (also deterministic)
            nlu = _nl.parse_utterance(norm)
            if nlu is not None and nlu.is_command and \
                    nlu.intent is not IntentType.NONE:
                intent = _nl.nlu_to_intent(nlu, None, norm, now)
                if intent is not None:
                    intent.confidence *= float(confidence)
                    self.last_transcript = norm
                    self.last_command = nlu.intent.value
                    self.last_confidence = nlu.confidence
                    self.last_intent = intent
                    self._last_fire = now
                    self._emit(Event(
                        kind=EventKind.VOICE_COMMAND,
                        modality=Modality.VOICE,
                        confidence=intent.confidence,
                        source="offline_voice:nl",
                        payload={"command": nlu.intent.value,
                                 "text": norm, "intent": intent},
                        timestamp=now))
                    return
        else:
            # 6. HYBRID arbitration (§5): weak grammar matches that look
            #    like prose fall into dictation
            if self.mode is VoiceMode.HYBRID and \
                    match.confidence < self.hybrid_command_confidence:
                words = norm.split()
                if len(words) >= self.hybrid_dictation_min_words:
                    committed, pending = self.dictation.feed(text)
                    self.last_transcript = norm
                    if committed:
                        self.last_committed_text = committed
                        self._last_fire = now
                        self._emit(Event(
                            kind=EventKind.VOICE_TEXT,
                            modality=Modality.VOICE,
                            confidence=confidence,
                            source="offline_voice:hybrid",
                            payload={"text": committed, "pending": pending},
                            timestamp=now))
                    return

            intent = voice_match_to_intent(
                match, self.context, confidence_scale=confidence, now=now)
            self.last_transcript = norm
            self.last_command = match.name
            self.last_confidence = match.confidence * float(confidence)
            self.last_intent = intent
            if intent is not None:
                self._last_fire = now
                self._emit(Event(
                    kind=EventKind.VOICE_COMMAND, modality=Modality.VOICE,
                    confidence=intent.confidence,
                    source="offline_voice:grammar",
                    payload={"command": match.name, "text": norm,
                             "intent": intent,
                             "entities": dict(match.entities),
                             "sensitive": bool(match.sensitive),
                             "destructive": bool(match.destructive)},
                    timestamp=now))
                return

        # 7. nothing resolved:
        #    - COMMAND mode: drop (report as low-value text for the HUD)
        #    - HYBRID mode: unmatched utterances feed dictation (markers
        #      like "commit" are short by design, so no word gate here)
        if self.on_speech:
            try:
                self.on_speech(norm)
            except Exception:
                pass
        if self.mode is VoiceMode.HYBRID:
            committed, pending = self.dictation.feed(text)
            if committed:
                self.last_committed_text = committed
                self._last_fire = now
                self._emit(Event(
                    kind=EventKind.VOICE_TEXT, modality=Modality.VOICE,
                    confidence=confidence, source="offline_voice:hybrid",
                    payload={"text": committed, "pending": pending},
                    timestamp=now))
            elif pending:
                self._emit(Event(
                    kind=EventKind.VOICE_TEXT, modality=Modality.VOICE,
                    confidence=confidence * 0.5,
                    source="offline_voice:hybrid_pending",
                    payload={"text": pending, "committed": False},
                    timestamp=now))
            return
        if self.mode is VoiceMode.COMMAND:
            self._emit(Event(
                kind=EventKind.VOICE_TEXT, modality=Modality.VOICE,
                confidence=confidence * 0.5, source="offline_voice:unmatched",
                payload={"text": norm, "unmatched": True}, timestamp=now))

    # -- consumption --------------------------------------------------------------

    def poll(self) -> Optional[Event]:
        """Pop the oldest produced-but-unconsumed event (main loop API)."""
        with self._lock:
            if self._events:
                return self._events.pop(0)
        return None

    def status(self) -> Dict[str, Any]:
        """Diagnostics snapshot (airmouse voice-status)."""
        return {
            "mode": self.mode.value,
            "enabled": self.enabled,
            "provider": self.provider.name,
            "provider_available": self.provider.available(),
            "providers_detected": detect_providers(),
            "last_command": self.last_command,
            "last_transcript": self.last_transcript,
            "last_confidence": self.last_confidence,
            "last_confidence_known": self.last_confidence_known,
            "rejected_audio": self.rejected_audio,
            "last_audio_reject_reason": self.last_audio_reject_reason,
            "dictation_pending": self.dictation.pending,
            "wake_word_required": self.wake_word_required,
            "latency_ms": self.latency_ms,
        }

    def shutdown(self) -> None:
        self.stop_audio()


# ---------------------------------------------------------------------------
# honest session-banner helpers (TF-005) — wired into __main__
# ---------------------------------------------------------------------------

#: banner state: a REAL local ASR provider is active (proven, not assumed)
BANNER_LIVE = "LIVE"

#: banner state: the deterministic local grammar runs, but NO usable ASR
#: engine is behind it — the reason string names the exact gap + remedy
BANNER_GRAMMAR_ONLY = "GRAMMAR-ONLY"

#: the honest v5 SpeechRecognition disclosure: that path sends recorded
#: audio to Google's cloud for recognition.  It must NEVER be labelled
#: "offline" — this string is the exact wording __main__ prints.
CLOUD_DISCLOSURE = "uses Google Web Speech (cloud)"


def session_banner_state() -> Tuple[str, str]:
    """The honest banner state for the offline voice session.

    Returns ``(state, reason)`` where state is exactly
    :data:`BANNER_LIVE` or :data:`BANNER_GRAMMAR_ONLY`:

    * ``("LIVE", reason)`` — ONLY when a real local ASR provider
      constructs AND reports ``available()`` (engine-installed is NOT
      model-available; readiness is proven here, never assumed).
    * ``("GRAMMAR-ONLY", reason)`` — the engine still runs on the
      deterministic local grammar; ``reason`` names the exact missing
      capability and the actionable remedy (e.g. vosk installed but no
      model → ``run: airmouse voice-setup``).

    Never raises; never claims LIVE without proof.
    """
    provider = None
    try:
        from .asr_registry import registry   # lazy: avoids import cycle
        provider = registry.select()
    except Exception:
        provider = None
    if provider is not None:
        try:
            ok = bool(provider.available())
        except Exception:
            ok = False
        if ok:
            return (BANNER_LIVE,
                    f"real local ASR online: {getattr(provider, 'name', '?')}")
    # no usable provider — build the exact honest reason
    vosk_installed = False
    try:
        from . import voice_stack as _vs
        vosk_installed = any(_vs._spec_found(m)
                             for m in _vs._ENGINE_MODULES.get("vosk", ()))
    except Exception:
        vosk_installed = False
    if vosk_installed:
        reason = ("vosk engine installed but no local acoustics model — "
                  "run: airmouse voice-setup")
    else:
        reason = ("no local ASR engine installed — install pocketsphinx "
                  "or run: airmouse voice-setup (vosk)")
    return BANNER_GRAMMAR_ONLY, reason


def grammar_only_reason() -> str:
    """The exact GRAMMAR-ONLY reason for THIS machine ('' when a real
    ASR provider is LIVE — check :func:`session_banner_state` first)."""
    state, reason = session_banner_state()
    return reason if state == BANNER_GRAMMAR_ONLY else ""


def cloud_voice_banner() -> str:
    """The honest banner for the v5 SpeechRecognition path.

    That path records the microphone and uploads the audio to Google's
    Web Speech API — this string discloses exactly that, instead of any
    fake OFFLINE claim.
    """
    return ("VOICE ONLINE (cloud) — " + CLOUD_DISCLOSURE +
            "; spoken audio is uploaded to Google for recognition "
            "(nothing about this path is offline)")
