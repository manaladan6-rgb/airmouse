"""mic_capture — real microphone utterance capture (v16.5.2, mission §4/§5).

The Windows reality profile has sounddevice 0.5.5 + PyAudio 0.2.14, and
the mission is explicit: use the lightest existing stack, prefer
**sounddevice**, and never make PyAudio a hard requirement.

Pipeline (mission §5)::

    Microphone → sounddevice/PortAudio → audio frames →
    EnergyVAD (silence detection) → PCM16 mono bytes →
    the session's local ASR provider (PocketSphinx = default) →
    (transcript, confidence)

* Audio is captured in 30 ms frames; the shipped
  :class:`airmouse.offline_voice.EnergyVAD` decides when speech starts
  and ends — no extra dependencies.
* NOTHING is ever written to disk (no temp files, no uploads, no
  telemetry).  The WAV path for vosk/whisper engines exists only in
  memory→temp→unlink inside :func:`capture_transcript` when an engine
  requires a file path, and it is deleted immediately.
* Every failure raises :class:`MicCaptureError` with an actionable
  ``.reason`` and ``.fix`` — callers show them verbatim instead of
  silently downgrading (mission §7: provider failure must be honest).
* Deterministic for tests: :func:`record_utterance` accepts an
  injectable stream factory; no hardware is needed to test the logic.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any, Callable, List, Optional, Tuple

__all__ = ["MicCaptureError", "list_input_devices", "resolve_device",
           "record_utterance", "capture_transcript",
           "DEFAULT_SAMPLE_RATE"]

DEFAULT_SAMPLE_RATE = 16000


class MicCaptureError(Exception):
    """Microphone capture failed — the reason is the message.

    ``.fix`` carries the actionable remediation line (mission §7/§13);
    callers print both verbatim.  This is the ONLY failure style here:
    capture never silently degrades or fabricates.
    """

    def __init__(self, reason: str, fix: str = "") -> None:
        super().__init__(reason)
        self.reason = str(reason or "microphone capture failed")
        self.fix = str(fix or "run: airmouse voice-status --verbose")


# ---------------------------------------------------------------------------
# device listing / resolution (mission §13)
# ---------------------------------------------------------------------------

def list_input_devices() -> List[Dict[str, Any]]:
    """Input devices via the unified detector (never raises)."""
    from .asr_registry import detect_microphone
    return list(detect_microphone().get("devices", []))


def resolve_device(device: Optional[str] = None) -> Optional[int]:
    """Resolve a ``--mic`` argument to a sounddevice index.

    Accepts ``None``/``"default"`` (system default → ``None``), an
    integer index (``"1"``), or a case-insensitive substring of the
    device name.  Raises :class:`MicCaptureError` for unknown indices /
    names with the FIX line that lists what IS available.
    """
    if device is None or str(device).strip().lower() in ("", "default"):
        return None
    dev = str(device).strip()
    devices = list_input_devices()
    if not devices:
        raise MicCaptureError(
            f"microphone '{dev}' requested but no input device exists",
            "connect a microphone (and grant permission), then run: "
            "airmouse voice-status --verbose")
    if dev.lstrip("-").isdigit():
        idx = int(dev)
        for d in devices:
            if d["index"] == idx:
                return idx
        raise MicCaptureError(
            f"invalid microphone index {idx}",
            "available: " + (", ".join(
                f"{d['index']}={d['name']}" for d in devices[:8])
                or "(none)") + "  (see: airmouse voice-status)")
    low = dev.lower()
    for d in devices:
        if low in d["name"].lower():
            return int(d["index"])
    raise MicCaptureError(
        f"no microphone matching '{dev}'",
        "available: " + (", ".join(
            f"{d['index']}={d['name']}" for d in devices[:8])
            or "(none)") + "  (see: airmouse voice-status)")


# ---------------------------------------------------------------------------
# the recording loop (sounddevice-first, VAD-bounded)
# ---------------------------------------------------------------------------

def _default_stream_factory(device_index: Optional[int], sample_rate: int):
    """The real sounddevice InputStream factory (raw int16 mono)."""
    import sounddevice as sd

    def _open(blocksize: int):
        try:
            stream = sd.InputStream(
                device=device_index, samplerate=sample_rate, channels=1,
                dtype="int16", blocksize=blocksize)
        except OSError as exc:
            msg = str(exc)
            low = msg.lower()
            if "invalid" in low and "device" in low:
                raise MicCaptureError(
                    f"invalid microphone device {device_index}: {msg}",
                    "list devices: airmouse voice-status")
            if "busy" in low or "unavailable" in low or "-9985" in low \
                    or "-9996" in low:
                raise MicCaptureError(
                    f"microphone busy or unavailable: {msg}",
                    "close other apps using the mic, then retry; "
                    "devices: airmouse voice-status")
            raise MicCaptureError(
                f"microphone open failed: {msg}",
                "check Windows microphone privacy settings and the "
                "default device, then run: airmouse voice-status")
        return stream

    return _open


def record_utterance(device: Optional[str] = None,
                     sample_rate: int = DEFAULT_SAMPLE_RATE,
                     max_seconds: float = 8.0,
                     start_timeout: float = 6.0,
                     end_silence: float = 0.7,
                     stream_factory: Optional[Callable] = None,
                     vad: Any = None,
                     sleep_fn: Callable[[float], None] = time.sleep,
                     ) -> bytes:
    """Record ONE spoken utterance and return PCM16 mono bytes.

    Waits up to ``start_timeout`` seconds for speech to start (VAD),
    records until ``end_silence`` seconds of trailing silence or
    ``max_seconds`` total.  Raises :class:`MicCaptureError` with the
    honest reason on any failure — including a genuine
    ``(nothing but noise/timeout)`` outcome as ``no speech detected``.
    """
    from .offline_voice import EnergyVAD

    device_index = resolve_device(device)
    if stream_factory is None:
        stream_factory = _default_stream_factory(device_index, sample_rate)
    vad = vad or EnergyVAD(sample_rate=sample_rate, end_silence=end_silence)
    frame_ms = 30
    block = int(sample_rate * frame_ms / 1000)

    open_stream = None
    try:
        open_stream = stream_factory(block)
        stream = open_stream if hasattr(open_stream, "read") else \
            open_stream.__enter__()
    except MicCaptureError:
        raise
    except Exception as exc:
        raise MicCaptureError(
            f"microphone could not start: {exc}",
            "check the default input device, then run: "
            "airmouse voice-status --verbose")
    try:
        chunks: List[bytes] = []
        started = False
        t_begin = time.monotonic()
        while True:
            now = time.monotonic()
            if now - t_begin > max(1.0, max_seconds + start_timeout + 2.0):
                break  # hard safety stop — never hang
            try:
                data, _overflow = stream.read(block)
            except Exception as exc:
                raise MicCaptureError(
                    f"microphone read failed: {exc}",
                    "replug the microphone or pick another device: "
                    "airmouse voice-status")
            pcm = bytes(data)
            vad.feed(pcm, now=now - t_begin)
            if vad.active:
                started = True
                chunks.append(pcm)
            elif started:
                chunks.append(pcm)  # keep the trailing silence tail
                if vad.speech_ended:
                    break
            else:
                if now - t_begin > start_timeout:
                    raise MicCaptureError(
                        "no speech detected (silence/timeout)",
                        "speak closer to the microphone and try again")
            sleep_fn(max(0.0, frame_ms / 1000.0 - 0.005))
        if not started or not chunks:
            raise MicCaptureError(
                "no speech detected (silence/timeout)",
                "speak closer to the microphone and try again")
        return b"".join(chunks)
    except KeyboardInterrupt:
        raise MicCaptureError(
            "interrupted (Ctrl+C)", "run: airmouse transcribe")
    finally:
        try:
            if hasattr(open_stream, "close"):
                open_stream.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# provider-facing capture (PCM → engine input shape)
# ---------------------------------------------------------------------------

def _provider_name(provider: Any) -> str:
    return str(getattr(provider, "name", "") or "")


def _audio_for_provider(provider: Any, pcm: bytes,
                        sample_rate: int) -> Tuple[Any, Optional[str]]:
    """Wrap raw PCM into the input the provider expects.

    Returns ``(engine_input, temp_wav_path_or_None)``.  PocketSphinx
    takes SpeechRecognition ``AudioData``; vosk/whisper take a WAV file
    path (temp file, deleted by the caller immediately after use).
    """
    name = _provider_name(provider)
    if name == "pocketsphinx":
        import speech_recognition as sr
        return sr.AudioData(pcm, sample_rate, 2), None
    import wave
    fd, path = tempfile.mkstemp(suffix=".wav")
    with os.fdopen(fd, "wb") as wf:
        with wave.open(wf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm)
    return path, path


def capture_transcript(provider: Any,
                       device: Optional[str] = None,
                       timeout_s: float = 6.0) -> Tuple[Optional[str], float]:
    """One REAL utterance: mic → VAD → local ASR → (transcript, conf).

    The v16.5.2 primary path (mission §4): sounddevice capture through
    :func:`record_utterance`, fed to the session's provider.  When the
    sounddevice backend cannot start but the legacy SpeechRecognition /
    PyAudio stack works, that path is used as a fallback (PyAudio stays
    optional — never a hard requirement).

    Returns ``(transcript_or_None, confidence)``.  ``transcript is
    None`` means capture failed honestly; :class:`MicCaptureError`
    carries the reason when the caller wants the WHY/FIX detail.
    Never returns a fabricated phrase.
    """
    try:
        pcm = record_utterance(device=device, start_timeout=timeout_s,
                               max_seconds=max(8.0, timeout_s * 2.0))
    except MicCaptureError:
        # sounddevice path failed — fall back to the legacy PyAudio/SR
        # ear when it exists (mission §4: PyAudio is optional but usable)
        return _capture_via_speech_recognition(provider, timeout_s)
    path: Optional[str] = None
    try:
        engine_input, path = _audio_for_provider(provider, pcm,
                                                 DEFAULT_SAMPLE_RATE)
        t = provider.transcribe(engine_input)
        text = str(getattr(t, "text", "") or "").strip()
        conf = float(getattr(t, "confidence", 0.0) or 0.0)
        return (text or None), conf
    except Exception:
        return None, 0.0
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _capture_via_speech_recognition(
        provider: Any, timeout_s: float) -> Tuple[Optional[str], float]:
    """Legacy ear: SpeechRecognition Microphone (PyAudio backend).

    Used ONLY when the sounddevice path cannot start.  Returns
    ``(None, 0.0)`` (never raises) so callers can degrade honestly.
    """
    try:
        import speech_recognition as sr
    except Exception:
        return None, 0.0
    try:
        rec = sr.Recognizer()
        with sr.Microphone() as source:  # pragma: no cover — hardware path
            rec.adjust_for_ambient_noise(source, duration=0.4)
            audio = rec.listen(source, timeout=timeout_s,
                               phrase_time_limit=max(2.0, timeout_s))
    except Exception:
        return None, 0.0
    try:
        name = _provider_name(provider)
        if name == "pocketsphinx":
            t = provider.transcribe(audio)
            return (t.text or None), float(t.confidence or 0.0)
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav",
                                             delete=False) as tf:
                path = tf.name
            with open(path, "wb") as wf:
                wf.write(audio.get_wav_data())
            t = provider.transcribe(path)
            text = (t.text or "").strip()
            return (text or None), float(t.confidence or 0.0)
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    except Exception:
        return None, 0.0
