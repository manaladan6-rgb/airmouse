"""
Audio Feedback — Generates click, whoosh, and gesture sounds using numpy.

No external audio files needed — all sounds are synthesized.
Uses simpleaudio or falls back to winsound on Windows.
"""

import numpy as np
import threading


class AudioFeedback:
    """Synthesized audio feedback for gestures."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self._backend = None
        if enabled:
            self._init_backend()

    def _init_backend(self):
        """Try to init a sound backend."""
        try:
            import simpleaudio
            self._backend = "simpleaudio"
        except ImportError:
            pass
        try:
            import winsound  # noqa: Windows only
            if self._backend is None:
                self._backend = "winsound"
        except ImportError:
            pass

    def _play_thread(self, samples, sample_rate=22050):
        """Play audio in a background thread."""
        if self._backend == "simpleaudio":
            import simpleaudio
            try:
                # Convert float to 16-bit int
                audio = (samples * 32767).astype(np.int16)
                simpleaudio.WaveObject(audio.tobytes(), 1, 2, sample_rate).play()
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
        # Sharp click: short sine burst with fast decay
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
        # Noise burst with envelope
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
