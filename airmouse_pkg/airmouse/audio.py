"""
Audio Feedback v3.1 — Generates click, whoosh, gesture, and mode sounds using numpy.

New in v3.1:
  - Mode enter/exit sounds (volume, brightness, precision)
  - Gesture confirm sound (ascending tone)
  - Better whoosh with pitch variation

No external audio files needed — all sounds are synthesized.
Uses winsound on Windows (stdlib, ZERO compilation) or sounddevice as fallback.
"""

import numpy as np
import threading
import wave
import tempfile
import os


class AudioFeedback:
    """Synthesized audio feedback for gestures."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self._backend = None
        if enabled:
            self._init_backend()

    def _init_backend(self):
        """Try to init a sound backend.

        Priority: winsound (Windows stdlib) > sounddevice (pre-built wheel).
        """
        try:
            import winsound  # noqa: Windows stdlib — zero deps
            self._backend = "winsound"
            return
        except ImportError:
            pass
        try:
            import sounddevice  # noqa: pre-built wheel, no C++ needed
            self._backend = "sounddevice"
        except ImportError:
            pass

    def _samples_to_wav_bytes(self, samples, sample_rate=22050):
        """Convert float numpy samples to in-memory WAV bytes for winsound."""
        audio = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                tmp_path = f.name
            with wave.open(tmp_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio.tobytes())
            with open(tmp_path, 'rb') as rf:
                return rf.read()
        except Exception:
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _play_thread(self, samples, sample_rate=22050):
        """Play audio in a background thread."""
        try:
            if self._backend == "winsound":
                import winsound
                wav_bytes = self._samples_to_wav_bytes(samples, sample_rate)
                if wav_bytes:
                    winsound.PlaySound(wav_bytes,
                                       winsound.SND_MEMORY | winsound.SND_ASYNC)
            elif self._backend == "sounddevice":
                import sounddevice as sd
                audio = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
                sd.play(audio.astype(np.float32) / 32767.0, sample_rate)
        except Exception:
            pass

    def _play(self, samples):
        """Play samples asynchronously."""
        if not self.enabled or self._backend is None:
            return
        threading.Thread(target=self._play_thread, args=(samples,), daemon=True).start()

    def click(self):
        """Short click sound."""
        sr = 22050
        t = np.linspace(0, 0.05, int(sr * 0.05), False)
        samples = np.sin(2 * np.pi * 800 * t) * np.exp(-t * 80)
        self._play(samples)

    def right_click(self):
        """Higher-pitched click for right click."""
        sr = 22050
        t = np.linspace(0, 0.05, int(sr * 0.05), False)
        samples = np.sin(2 * np.pi * 1200 * t) * np.exp(-t * 80)
        self._play(samples)

    def whoosh(self, speed=1.0):
        """Whoosh sound for fast cursor movement."""
        sr = 22050
        duration = min(0.08, 0.03 + speed * 0.01)
        t = np.linspace(0, duration, int(sr * duration), False)
        # Noise burst with envelope + slight pitch based on speed
        noise = np.random.randn(len(t)) * 0.3
        envelope = np.exp(-t * 50) * min(speed / 1000, 1.0)
        samples = noise * envelope
        self._play(samples)

    def scroll_tick(self):
        """Tiny tick sound for scroll."""
        sr = 22050
        t = np.linspace(0, 0.02, int(sr * 0.02), False)
        samples = np.sin(2 * np.pi * 600 * t) * np.exp(-t * 100)
        self._play(samples)

    def drag_start(self):
        """Low tone for drag start."""
        sr = 22050
        t = np.linspace(0, 0.1, int(sr * 0.1), False)
        samples = np.sin(2 * np.pi * 300 * t) * np.exp(-t * 30)
        self._play(samples)

    def freeze(self):
        """Deep tone for cursor freeze."""
        sr = 22050
        t = np.linspace(0, 0.15, int(sr * 0.15), False)
        samples = np.sin(2 * np.pi * 200 * t) * np.exp(-t * 20)
        self._play(samples)

    def mode_enter(self):
        """Ascending tone when entering a mode (volume/brightness/precision)."""
        sr = 22050
        t = np.linspace(0, 0.12, int(sr * 0.12), False)
        # Rising pitch from 400Hz to 800Hz
        freq = 400 + 400 * (t / 0.12)
        phase = 2 * np.pi * np.cumsum(freq) / sr
        samples = np.sin(phase) * np.exp(-t * 15) * 0.6
        self._play(samples)

    def mode_exit(self):
        """Descending tone when exiting a mode."""
        sr = 22050
        t = np.linspace(0, 0.12, int(sr * 0.12), False)
        # Falling pitch from 800Hz to 400Hz
        freq = 800 - 400 * (t / 0.12)
        phase = 2 * np.pi * np.cumsum(freq) / sr
        samples = np.sin(phase) * np.exp(-t * 15) * 0.6
        self._play(samples)

    def gesture_confirm(self):
        """Quick ascending blip when gesture is confirmed."""
        sr = 22050
        t = np.linspace(0, 0.06, int(sr * 0.06), False)
        freq = 600 + 400 * (t / 0.06)
        phase = 2 * np.pi * np.cumsum(freq) / sr
        samples = np.sin(phase) * np.exp(-t * 30) * 0.5
        self._play(samples)

    def precision_toggle(self):
        """Dual-tone for precision mode toggle."""
        sr = 22050
        t = np.linspace(0, 0.1, int(sr * 0.1), False)
        samples = (np.sin(2 * np.pi * 500 * t) + np.sin(2 * np.pi * 750 * t)) * 0.3 * np.exp(-t * 25)
        self._play(samples)

    def recalibrate(self):
        """Smooth confirmation tone for recalibration."""
        sr = 22050
        t = np.linspace(0, 0.15, int(sr * 0.15), False)
        freq = 440 + 220 * (t / 0.15)
        phase = 2 * np.pi * np.cumsum(freq) / sr
        samples = np.sin(phase) * np.exp(-t * 12) * 0.5
        self._play(samples)
