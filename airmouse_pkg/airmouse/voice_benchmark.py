"""voice_benchmark — deterministic local ASR benchmark (v16.5.3, §22).

``airmouse voice-benchmark`` measures what can be measured HONESTLY on
the local machine:

* engine startup (registry select + real initialization),
* first-result latency and average utterance latency over a scripted,
  deterministic PCM workload (identical bytes every run),
* CPU time consumed, peak RAM, and the disclosed package/model sizes.

Recognition quality over real speech (words/minute, command/dictation
recognition) is measured ONLY when the caller supplies REAL speech
(``--wav file.wav``) — the benchmark NEVER claims accuracy without
actual speech samples, and never fabricates a WER.

Everything runs locally; nothing is uploaded; no model is downloaded.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

__all__ = ["run_benchmark"]


def _get_registry():
    """THE registry (indirection kept for deterministic tests)."""
    from .asr_registry import registry
    return registry


def _peak_rss_mb() -> float:
    """Peak RSS in MB (honest 0.0 when the platform cannot report it)."""
    try:
        import resource  # POSIX
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        pass
    try:  # Windows — ctypes, no new dependency
        import ctypes

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if k32.GetProcessMemoryInfo(k32.GetCurrentProcess(),
                                    ctypes.byref(pmc), pmc.cb):
            return float(pmc.PeakWorkingSetSize) / (1024.0 * 1024.0)
    except Exception:
        pass
    return 0.0


def _scripted_utterance_pcm(seconds: float = 1.0, rate: int = 16000,
                            phrase_no: int = 0) -> bytes:
    """Deterministic speech-shaped PCM (identical bytes on every run).

    A slow amplitude envelope over a 220 Hz carrier — real AUDIO BYTES
    for latency measurement, deliberately NOT claimed to be speech.
    """
    import math
    import struct
    out = bytearray()
    for i in range(int(seconds * rate)):
        t = i / float(rate)
        env = 0.5 * (1.0 + math.sin(2 * math.pi * 1.5 * t + phrase_no))
        v = int(9000 * env * math.sin(2 * math.pi * 220 * t))
        out += struct.pack("<h", max(-32768, min(32767, v)))
    return bytes(out)


def _as_provider_input(name: str, pcm: bytes, rate: int = 16000) -> Any:
    """Wrap PCM into the engine's expected input (mirrors mic_capture)."""
    if name == "pocketsphinx":
        import speech_recognition as sr
        return sr.AudioData(pcm, rate, 2)
    fd, path = tempfile.mkstemp(suffix=".wav")
    import wave
    with os.fdopen(fd, "wb") as wf:
        with wave.open(wf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
    return path


def run_benchmark(out: Any = None, wav: Optional[str] = None,
                  utterances: int = 3) -> int:
    """Run the deterministic benchmark; returns a process exit code."""
    w = out if out is not None else sys.stdout

    def emit(text: str = "") -> None:
        try:
            w.write(text + "\n")
            w.flush()
        except Exception:
            pass

    emit("AirMouse Voice Benchmark (local, deterministic)")
    emit("=" * 48)
    emit(f"platform: {platform.system()} {platform.machine()} — "
         f"CPython {sys.version_info.major}.{sys.version_info.minor}")

    # -- engine selection (startup) -----------------------------------------
    t0 = time.perf_counter()
    provider = None
    active = ""
    try:
        provider = _get_registry().select(force=True)
        active = str(getattr(provider, "name", "") or "")
    except Exception as exc:
        emit(f"registry select failed: {exc}")
    startup_s = time.perf_counter() - t0

    if provider is None:
        emit("")
        emit("No local ASR engine is usable on this machine.")
        emit("Run: airmouse voice-setup   (zero-compiler lightweight "
             "install)")
        emit("The benchmark measures nothing else without an engine "
             "(no accuracy claims without real capability).")
        return 1

    emit(f"provider: {active} [ACTIVE] — engine startup: "
         f"{startup_s:.3f}s")

    # -- scripted latency workload -------------------------------------------
    utt = max(1, min(10, int(utterances)))
    latencies: List[float] = []
    first_latency = 0.0
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    for n in range(utt):
        pcm = _scripted_utterance_pcm(1.0, phrase_no=n)
        inp = _as_provider_input(active, pcm)
        t1 = time.perf_counter()
        try:
            provider.transcribe(inp)
        except Exception:
            pass
        dt = time.perf_counter() - t1
        if n == 0:
            first_latency = dt
        latencies.append(dt)
        if active != "pocketsphinx":
            try:
                os.unlink(inp)
            except OSError:
                pass
    cpu_s = time.process_time() - cpu0
    wall_s = time.perf_counter() - wall0

    emit("")
    emit("TIMING (scripted deterministic audio — timing only, no accuracy "
         "claim)")
    emit(f"  startup:                {startup_s:.3f}s")
    emit(f"  first result latency:   {first_latency:.3f}s")
    emit(f"  average utterance:      {sum(latencies) / len(latencies):.3f}s "
         f"over {utt} x 1.0s utterances")
    emit(f"  CPU time consumed:      {cpu_s:.3f}s "
         f"({cpu_s / max(wall_s, 1e-9) * 100:.0f}% of wall)")
    rss = _peak_rss_mb()
    emit(f"  peak RAM:               {rss:.0f} MB"
         if rss > 0 else "  peak RAM:               unavailable on "
                         "this platform")

    # -- disclosed package/model footprint (§16) ------------------------------
    emit("")
    emit("FOOTPRINT (disclosed, not marketing)")
    try:
        from .voice_bootstrap import _WHEEL_MATRIX
        for info in _WHEEL_MATRIX:
            if info.name == active:
                emit(f"  wheel size:             "
                     f"{info.wheel_size_mb:.1f} MB")
                emit(f"  model:                  {info.model}")
                if info.model_extra_mb:
                    emit(f"  model download:         "
                         f"{info.model_extra_mb:.1f} MB (one-time)")
    except Exception:
        pass

    # -- real-speech section (ONLY with actual speech samples) ----------------
    if wav:
        emit("")
        emit("REAL SPEECH (--wav supplied)")
        tmp_path = ""
        try:
            import wave
            with wave.open(wav, "rb") as wf:
                rate = wf.getframerate()
                pcm = wf.readframes(wf.getnframes())
            inp = _as_provider_input(active, pcm, rate)
            if active != "pocketsphinx":
                tmp_path = str(inp)
            t1 = time.perf_counter()
            tr = provider.transcribe(inp)
            dt = time.perf_counter() - t1
            text = str(getattr(tr, "text", "") or "").strip()
            words = len(text.split())
            secs = len(pcm) / (2.0 * max(rate, 1))
            emit(f"  utterance:              {secs:.1f}s of audio")
            emit(f"  transcript:             \"{text[:120]}\"")
            emit(f"  latency:                {dt:.3f}s")
            if secs > 0:
                emit(f"  rate:                   "
                     f"{words / (secs / 60.0):.0f} words/minute")
            emit("  (one real sample — no accuracy/WER claim is made "
                 "from it)")
        except Exception as exc:
            emit(f"  real-speech pass failed: {type(exc).__name__}: {exc}")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    else:
        emit("")
        emit("No speech samples supplied — recognition quality is NOT "
             "measured")
        emit("  (provide real speech: airmouse voice-benchmark --wav "
             "sample.wav)")
    emit("")
    emit("All processing was local.  Nothing was uploaded; no audio was "
         "stored.")
    return 0
