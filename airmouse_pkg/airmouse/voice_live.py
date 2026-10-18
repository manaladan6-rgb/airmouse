"""airmouse.voice_live — REAL microphone PCM capture for the voice path.

This module is the rebuilt (Operation AIRLOCK) live-capture half of the
voice-truth contract (TF-004/TF-005):

    real input device ──► audio callback ──► BoundedAudioQueue ──►
    consumer thread ──► OfflineVoiceEngine.feed_audio ──►
    the session's OFFLINE ASR provider (REAL buffered PCM — never
    ``transcribe(None)``)

Laws enforced here (violating any of them is how the previous build
lost the plot):

* REAL PCM only.  Frames are 16 kHz / 16-bit / mono byte buffers taken
  verbatim from the input callback.  Nothing in this module ever
  synthesizes, pads or fabricates a frame — a dead device yields an
  honest UNAVAILABLE state, never fake audio.
* The audio callback is LIGHTWEIGHT.  ``_on_audio`` copies bytes into a
  bounded queue and updates counters — nothing else.  NO recognition,
  NO VAD, NO engine calls, NO blocking work ever happens inside the
  callback (structurally asserted by the test suite).
* BOUNDED memory.  ``BoundedAudioQueue`` never grows without bound:
  when full, the OLDEST frame is dropped to admit the newest one
  (documented DROP-OLDEST policy — live audio values recency), and an
  exact overflow counter records the cost.  The counter is visible in
  :meth:`LiveVoiceCapture.status`.
* BOUNDED joins.  ``stop``/``close`` join the consumer thread with an
  explicit timeout, and NEVER join the current thread.  Deadlock by
  unbounded join is impossible by construction.
* HONEST states only.  The status API reports exactly
  configured / available / initialized / active (plus UNAVAILABLE and
  CLOSED).  There is no fake "LIVE" state: LIVE is claimed by the
  caller only from a real, started capture on a real device.

The default stream factory uses sounddevice/PortAudio; tests inject a
fake factory + probe, so no hardware is required to verify the logic.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Dict, Optional, Tuple

__all__ = [
    "SAMPLE_RATE", "CHANNELS", "SAMPLE_WIDTH", "FRAME_MS",
    "BoundedAudioQueue", "LiveVoiceCapture", "start_live_voice_for",
    "AudioStreamProtocol",
]

# ---------------------------------------------------------------------------
# the capture contract: 16 kHz / 16-bit / mono
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000   # samples per second
CHANNELS = 1          # mono
SAMPLE_WIDTH = 2      # bytes per sample → 16-bit little-endian PCM
FRAME_MS = 30         # default frame size (480 samples = 960 bytes)


def _pcm_bytes(indata: Any) -> bytes:
    """Best-effort verbatim copy of a PCM frame.  Never raises.

    Accepts anything the buffer protocol can read (sounddevice's numpy
    buffers, raw ``bytes``, ``bytearray``, ``memoryview``); returns
    ``b""`` for anything that cannot be copied — the caller treats an
    empty copy as "no real frame" and NEVER invents one.
    """
    try:
        return bytes(indata)
    except Exception:
        pass
    tb = getattr(indata, "tobytes", None)
    if callable(tb):
        try:
            return bytes(tb())
        except Exception:
            return b""
    return b""


# ---------------------------------------------------------------------------
# the bounded queue (DROP-OLDEST, counted)
# ---------------------------------------------------------------------------


class BoundedAudioQueue:
    """A bounded PCM-frame queue with a documented DROP-OLDEST policy.

    Contract:

    * ``put_frame`` NEVER blocks and the queue NEVER grows without
      bound.
    * When the queue is full, the OLDEST frame is dropped and the new
      frame takes its place (recency beats completeness for live
      audio).  Every dropped frame increments ``overflow_dropped`` by
      exactly one — the cost of the policy is measured, never hidden.
    * ``get_frame`` is the consumer side (blocking, with timeout).

    The overflow counter is exact under concurrency: drops are counted
    under a lock, and a frame that still cannot be admitted after the
    drop (a consumer/producer race) is counted as dropped too — the
    counter can never under-report.
    """

    def __init__(self, maxsize: int = 200) -> None:
        self._maxsize = max(1, int(maxsize))
        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=self._maxsize)
        self._overflow = 0
        self._count_lock = threading.Lock()

    # -- properties ---------------------------------------------------------

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def overflow_dropped(self) -> int:
        """Exact number of frames dropped by the DROP-OLDEST policy."""
        with self._count_lock:
            return self._overflow

    #: alias — the same honest counter, by its policy name
    overflow_count = overflow_dropped

    def qsize(self) -> int:
        return self._q.qsize()

    def empty(self) -> bool:
        return self._q.empty()

    # -- producer side (audio callback) --------------------------------------

    def put_frame(self, frame: bytes) -> bool:
        """Non-blocking put; on a full queue drop the OLDEST frame.

        Returns True when ``frame`` is in the queue afterwards, False
        when it had to be dropped (counted).  Never blocks, never
        raises on overflow — overflow is a counted, expected event.
        """
        frame = bytes(frame)
        try:
            self._q.put_nowait(frame)
            return True
        except queue.Full:
            with self._count_lock:
                try:
                    self._q.get_nowait()   # DROP-OLDEST
                    self._overflow += 1
                except queue.Empty:
                    pass
            try:
                self._q.put_nowait(frame)
                return True
            except queue.Full:
                # a concurrent consumer drained between the calls and
                # something refilled the slot — this frame is lost:
                # count it, never lie about admission
                with self._count_lock:
                    self._overflow += 1
                return False

    # -- consumer side --------------------------------------------------------

    def get_frame(self, timeout: Optional[float] = None) -> bytes:
        """Pop the oldest frame; raises ``queue.Empty`` on timeout."""
        return self._q.get(timeout=timeout)


# ---------------------------------------------------------------------------
# the live capture
# ---------------------------------------------------------------------------


class LiveVoiceCapture:
    """Real-device PCM capture with an honest, bounded lifecycle.

    States (exactly these — there is no fake LIVE state)::

        CONFIGURED    constructed with explicit params; device unproven
        AVAILABLE     device probe PASSED — ready to start
        INITIALIZED   a real stream was opened (kept as history)
        ACTIVE        capturing: callback → queue → consumer running
        UNAVAILABLE   device missing / open failed / disappeared
                      (honest; carries the reason in ``device_error``)
        CLOSED        closed; terminal (idempotent close)

    Usage::

        cap = LiveVoiceCapture(consumer=engine.feed_audio)
        if cap.start():
            ...
            cap.close()

    Tests inject ``stream_factory`` + ``device_probe`` so the full
    lifecycle is verifiable without hardware.  The default factory
    opens a sounddevice.InputStream with THIS ``_on_audio`` as the
    callback.
    """

    STATE_CONFIGURED = "CONFIGURED"
    STATE_AVAILABLE = "AVAILABLE"
    STATE_INITIALIZED = "INITIALIZED"
    STATE_ACTIVE = "ACTIVE"
    STATE_UNAVAILABLE = "UNAVAILABLE"
    STATE_CLOSED = "CLOSED"

    def __init__(self,
                 sample_rate: int = SAMPLE_RATE,
                 channels: int = CHANNELS,
                 dtype: str = "int16",
                 frame_ms: int = FRAME_MS,
                 queue_maxsize: int = 200,
                 consumer: Optional[Callable[[bytes], Any]] = None,
                 stream_factory: Optional[Callable[[Dict[str, Any]], Any]] = None,
                 device_probe: Optional[Callable[[], bool]] = None) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.dtype = str(dtype)
        self.frame_ms = int(frame_ms)
        self._q = BoundedAudioQueue(maxsize=queue_maxsize)
        self._consumer = consumer
        self._stream_factory = stream_factory or _sounddevice_stream_factory
        self._device_probe = device_probe or _sounddevice_device_probe

        self._state = self.STATE_CONFIGURED
        self._probed_ok = False
        self._initialized = False
        self._active = False
        self._closed = False
        self._stream: Optional[Any] = None
        self._consumer_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # honest counters / errors (never fabricated)
        self._frames_captured = 0
        self._frames_consumed = 0
        self._device_error = ""
        self._consumer_error = ""
        self._callback_error = ""
        self._last_status_flags = ""

    # -- honest status API ----------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def configured(self) -> bool:
        """Params are explicit — True from construction (honest)."""
        return not self._closed

    @property
    def available(self) -> bool:
        """A device probe PASSED and the session is not dead."""
        return (self._probed_ok and not self._closed
                and self._state not in (self.STATE_UNAVAILABLE,
                                        self.STATE_CLOSED))

    @property
    def initialized(self) -> bool:
        """A real stream was opened at least once (honest history)."""
        return self._initialized

    @property
    def active(self) -> bool:
        """Capturing right now: callback → queue → consumer running."""
        return self._active and self._state == self.STATE_ACTIVE

    @property
    def overflow_dropped(self) -> int:
        """Frames dropped by the DROP-OLDEST policy (exact count)."""
        return self._q.overflow_dropped

    @property
    def device_error(self) -> str:
        return self._device_error

    def status(self) -> Dict[str, Any]:
        """The honest snapshot — every number here is measured."""
        return {
            "state": self._state,
            "configured": self.configured,
            "available": self.available,
            "initialized": self.initialized,
            "active": self.active,
            "closed": self._closed,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "dtype": self.dtype,
            "frame_ms": self.frame_ms,
            "frames_captured": self._frames_captured,
            "frames_consumed": self._frames_consumed,
            "overflow_dropped": self._q.overflow_dropped,
            "queue_size": self._q.qsize(),
            "queue_maxsize": self._q.maxsize,
            "device_error": self._device_error,
            "consumer_error": self._consumer_error,
            "callback_error": self._callback_error,
            "device_status_flags": self._last_status_flags,
        }

    # -- lifecycle -------------------------------------------------------------

    def probe(self) -> bool:
        """Probe the input device HONESTLY → AVAILABLE or UNAVAILABLE."""
        if self._closed:
            return False
        try:
            ok = bool(self._device_probe())
        except Exception as exc:
            ok = False
            self._device_error = \
                f"device probe failed: {type(exc).__name__}: {exc}"
        self._probed_ok = ok
        if ok:
            self._device_error = ""
            if self._state in (self.STATE_CONFIGURED,
                               self.STATE_UNAVAILABLE):
                self._state = self.STATE_AVAILABLE
        else:
            self._device_error = self._device_error or \
                "no input device available"
            if self._state != self.STATE_ACTIVE:
                self._state = self.STATE_UNAVAILABLE
        return ok

    def start(self) -> bool:
        """Probe + open the real stream + run the consumer.  Honest
        False (and an UNAVAILABLE state with the reason) on failure —
        never a fake success."""
        if self._closed:
            return False
        if self.active:
            return True
        if not self.probe():
            return False
        if self._stream is not None:      # restart after stop()
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        params: Dict[str, Any] = {
            "samplerate": self.sample_rate,
            "channels": self.channels,
            "dtype": self.dtype,
            "blocksize": max(1, int(self.sample_rate * self.frame_ms / 1000)),
            "callback": self._on_audio,
        }
        try:
            stream = self._stream_factory(params)
            stream.start()
        except Exception as exc:
            self._state = self.STATE_UNAVAILABLE
            self._device_error = \
                f"stream open failed: {type(exc).__name__}: {exc}"
            return False
        self._stream = stream
        self._stop_event.clear()
        self._consumer_error = ""
        self._consumer_thread = threading.Thread(
            target=self._consumer_loop, name="LiveVoiceConsumer",
            daemon=True)
        self._consumer_thread.start()
        self._initialized = True
        self._state = self.STATE_ACTIVE
        self._active = True
        return True

    def stop(self, timeout: float = 1.0) -> None:
        """Stop capture and the consumer with a BOUNDED join.

        The join always carries an explicit timeout and NEVER targets
        the current thread — calling ``stop`` from the consumer itself
        is safe.
        """
        self._stop_event.set()
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:
                self._device_error = \
                    f"stream stop failed: {type(exc).__name__}: {exc}"
        th = self._consumer_thread
        self._consumer_thread = None
        if th is not None and th.is_alive() \
                and th is not threading.current_thread():
            th.join(timeout=max(0.0, float(timeout)))   # bounded, always
        self._active = False
        if self._state == self.STATE_ACTIVE:
            self._state = (self.STATE_AVAILABLE if self._probed_ok
                           else self.STATE_CONFIGURED)

    def close(self, timeout: float = 1.0) -> None:
        """Idempotent terminal close: stop (bounded join) + close the
        stream.  Safe to call twice; safe from any thread."""
        if self._closed:
            return
        self._closed = True
        self.stop(timeout=timeout)
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except Exception as exc:
                self._device_error = \
                    f"stream close failed: {type(exc).__name__}: {exc}"
        self._state = self.STATE_CLOSED

    # -- the audio callback (DELIBERATELY lightweight) -------------------------

    def _on_audio(self, indata: Any, frames: Any, time_info: Any,
                  status: Any) -> None:
        """PortAudio input callback — the ONLY work allowed here is:
        copy the PCM bytes into the bounded queue (DROP-OLDEST on
        overflow) and update counters.  NO recognition, NO VAD, NO
        engine calls, NO blocking — recognition happens on the consumer
        thread with REAL buffered frames, never inside this callback.
        """
        try:
            if status:
                self._last_status_flags = str(status)
            pcm = _pcm_bytes(indata)
            if not pcm:
                return   # nothing real arrived — never fabricate a frame
            self._q.put_frame(pcm)
            self._frames_captured += 1
        except Exception as exc:
            self._callback_error = f"{type(exc).__name__}: {exc}"

    # -- the consumer thread ----------------------------------------------------

    def _stream_alive(self) -> bool:
        """True while the underlying stream reports itself active.  A
        stream that died on its own (device unplugged, driver abort) is
        detected here → honest UNAVAILABLE, never fake frames."""
        stream = self._stream
        if stream is None:
            return True
        try:
            flag = getattr(stream, "active", None)
            if flag is None:
                return True
            return bool(flag)
        except Exception:
            return False

    def _consumer_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._stream_alive():
                if not self._stop_event.is_set():
                    self._mark_unavailable(
                        "audio stream ended unexpectedly "
                        "(device disconnected?)")
                break
            try:
                frame = self._q.get_frame(timeout=0.05)
            except queue.Empty:
                continue
            self._frames_consumed += 1
            consumer = self._consumer
            if consumer is None:
                continue
            try:
                consumer(frame)
            except Exception as exc:
                self._consumer_error = f"{type(exc).__name__}: {exc}"

    def _mark_unavailable(self, reason: str) -> None:
        self._state = self.STATE_UNAVAILABLE
        self._active = False
        self._device_error = self._device_error or reason


# ---------------------------------------------------------------------------
# default sounddevice backend (guarded; tests inject fakes instead)
# ---------------------------------------------------------------------------


def _sounddevice_stream_factory(params: Dict[str, Any]) -> Any:
    """Open a real sounddevice.InputStream with the given params."""
    import sounddevice as _sd   # guarded: absence → honest start() False
    return _sd.InputStream(**params)


def _sounddevice_device_probe() -> bool:
    """True only when a REAL input device is detectable.  Never raises."""
    try:
        import sounddevice as _sd
        for dev in _sd.query_devices():
            try:
                if int(dev.get("max_input_channels", 0)) > 0:
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


# ---------------------------------------------------------------------------
# the wiring marker (what __main__ calls)
# ---------------------------------------------------------------------------


def start_live_voice_for(
        engine: Any,
        capture: Optional[LiveVoiceCapture] = None,
        **capture_kwargs: Any) -> Optional[LiveVoiceCapture]:
    """Wire a :class:`LiveVoiceCapture` to an OfflineVoiceEngine.

    THE unique wiring marker between real microphone capture and the
    offline voice path: the engine's ``feed_audio`` becomes the
    consumer of REAL PCM frames (which the engine buffers per utterance
    and hands to the offline ASR provider — never ``transcribe(None)``).

    Returns the capture — inspect ``capture.status()`` for the honest
    state (UNAVAILABLE with a reason when no device exists; the caller
    must print that, not fake a LIVE banner).  Returns ``None`` only
    when ``engine`` has no ``feed_audio`` to wire.
    """
    feed = getattr(engine, "feed_audio", None)
    if not callable(feed):
        return None
    if capture is None:
        capture_kwargs.setdefault("consumer", feed)
        capture = LiveVoiceCapture(**capture_kwargs)
    elif getattr(capture, "_consumer", None) is None:
        capture._consumer = feed
    if not capture.active and not capture.status()["closed"]:
        capture.start()   # honest: may leave the capture UNAVAILABLE
    return capture
