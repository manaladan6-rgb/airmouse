"""
Voice Control v5.0 — speech recognition + command engine
=========================================================

Adds hands-free voice commands to AirMouse using the free Google Web Speech
API (via the `SpeechRecognition` package with a PyAudio microphone).

Graceful degradation
--------------------
This module NEVER hard-fails. If `speech_recognition`, `pyaudio`, a
microphone, or network access is unavailable, `VoiceCommandEngine.available`
is simply False and `poll()` returns VoiceCommand.NONE forever. The main
loop can therefore always construct a VoiceCommandEngine and call `poll()`
every frame without any extra guards.

Quick usage
-----------
    from airmouse.voice_control import VoiceCommandEngine, VoiceCommand

    voice = VoiceCommandEngine(sensitivity="high",
                               on_transcript=None,   # or fn(transcript, cmd, score)
                               mic_index=None)       # None = system default mic
    voice.start()          # spawns a daemon listener thread (mic NOT opened here)
    ...                    # main loop:
    cmd = voice.poll()     # thread-safe; returns VoiceCommand.NONE when empty
    if cmd == VoiceCommand.CLICK:
        mouse.left_click()
    elif cmd == VoiceCommand.QUIT:
        running = False
    ...
    voice.stop()           # signal the listener thread to exit (on shutdown)

Optional spoken feedback: `voice.speak("voice on")` uses pyttsx3 TTS when
installed, and falls back to a printed `[voice] ...` line — never raises.

Sensitivity profiles (SENSITIVITY_PROFILES)
-------------------------------------------
    normal : wake word required (say "airmouse ..." first), strict 0.78
             fuzzy match, 5.0 s phrases, 1.00 s cooldown
             -> fewest false positives, calmest.
    high   : no wake word, 0.60 fuzzy match, 3.5 s phrases, 0.55 s cooldown
             -> balanced (default).
    turbo  : "MAD" mode — listens nonstop, fuzzy 0.45 match, 2.5 s phrases,
             0.30 s cooldown, lowest energy gate -> fires fast and accepts
             loose phrasing ("bigger", "louder please", "go up"...).

Command reference (COMMANDS maps each canonical command to trigger phrases)
---------------------------------------------------------------------------
    click / right_click / double_click / middle_click ....... mouse buttons
    scroll_up / scroll_down ................................. wheel scrolling
    zoom_in / zoom_out / zoom_toggle ........................ zoom mode
    drag / freeze / unfreeze ................................ cursor modes
    precision / calibrate ................................... accuracy tools
    record / stop_record / play_macro ....................... macro recorder
    volume_up / volume_down / mute .......................... system volume
    media_next / media_prev / media_play .................... media keys
    minimize / close_window / task_switcher / show_desktop .. window control
    screenshot .............................................. screen capture
    voice_off / quit ........................................ engine control
    NONE .................................................... no-op result

`match_command()` is a PURE function (no audio dependencies) and can be
unit-tested directly:

    match_command("please zoom in now", "turbo")   # -> ("zoom_in", 1.0)
    match_command("shut up", "high")               # -> ("voice_off", 1.0)
"""

import collections
import difflib
import os
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

__all__ = [
    "VoiceCommand",
    "COMMANDS",
    "SENSITIVITY_PROFILES",
    "match_command",
    "VoiceCommandEngine",
]

# Module docstring presence is asserted nowhere; this flag simply enables
# verbose stderr/stdout diagnostics inside the listener thread (default: off,
# so the thread never prints spam during normal operation).
_DEBUG = bool(os.environ.get("AIRMOUSE_VOICE_DEBUG"))

# Listener-thread error backoff (seconds): 0.5s doubling up to 5.0s.
_BACKOFF_MIN = 0.5
_BACKOFF_MAX = 5.0

# Wake-word gate (only used when the active profile sets wake_word_required).
# After a wake word is heard the engine stays "armed" for this many seconds.
_WAKE_WINDOW_S = 10.0
_WAKE_WORDS: Tuple[str, ...] = ("airmouse", "air mouse", "hey airmouse",
                                "hey air mouse")


class VoiceCommand:
    """Canonical command-name constants (plain strings, safe to compare)."""

    # Mouse buttons
    CLICK = "click"
    RIGHT_CLICK = "right_click"
    DOUBLE_CLICK = "double_click"
    MIDDLE_CLICK = "middle_click"
    # Scrolling
    SCROLL_UP = "scroll_up"
    SCROLL_DOWN = "scroll_down"
    # Zoom mode
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    ZOOM_TOGGLE = "zoom_toggle"
    # Cursor modes
    DRAG = "drag"
    FREEZE = "freeze"
    UNFREEZE = "unfreeze"
    # Accuracy tools
    PRECISION = "precision"
    CALIBRATE = "calibrate"
    # Macro recorder
    RECORD = "record"
    STOP_RECORD = "stop_record"
    PLAY_MACRO = "play_macro"
    # System volume
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    MUTE = "mute"
    # Media keys
    MEDIA_NEXT = "media_next"
    MEDIA_PREV = "media_prev"
    MEDIA_PLAY = "media_play"
    # Window control
    MINIMIZE = "minimize"
    CLOSE_WINDOW = "close_window"
    TASK_SWITCHER = "task_switcher"
    SHOW_DESKTOP = "show_desktop"
    # Screen capture
    SCREENSHOT = "screenshot"
    # Engine control
    VOICE_OFF = "voice_off"
    QUIT = "quit"
    # No-op result (empty queue / no match / below threshold)
    NONE = "none"


# Canonical command -> list of lowercase trigger phrases (generous "mad"
# coverage). Longer phrases win ties so "stop recording" beats "stop".
COMMANDS: Dict[str, List[str]] = {
    VoiceCommand.CLICK: ["click", "left click", "tap", "select"],
    VoiceCommand.RIGHT_CLICK: ["right click", "context"],
    VoiceCommand.DOUBLE_CLICK: ["double click", "open", "double tap"],
    VoiceCommand.MIDDLE_CLICK: ["middle click"],
    VoiceCommand.SCROLL_UP: ["scroll up", "up", "go up"],
    VoiceCommand.SCROLL_DOWN: ["scroll down", "down", "go down"],
    VoiceCommand.ZOOM_IN: ["zoom in", "bigger", "magnify", "zoom in please"],
    VoiceCommand.ZOOM_OUT: ["zoom out", "smaller", "shrink"],
    VoiceCommand.ZOOM_TOGGLE: ["zoom mode", "toggle zoom", "pinch zoom"],
    VoiceCommand.DRAG: ["drag", "grab", "hold"],
    VoiceCommand.FREEZE: ["freeze", "stop", "hold on", "wait"],
    VoiceCommand.UNFREEZE: ["unfreeze", "resume", "continue", "go"],
    VoiceCommand.PRECISION: ["precision", "precision mode", "accurate", "sniper"],
    VoiceCommand.CALIBRATE: ["calibrate", "calibration", "recalibrate"],
    VoiceCommand.RECORD: ["start recording", "record", "record macro"],
    VoiceCommand.STOP_RECORD: ["stop recording", "stop record", "end recording"],
    VoiceCommand.PLAY_MACRO: ["play macro", "play recording", "replay", "run macro"],
    VoiceCommand.VOLUME_UP: ["volume up", "louder", "louder please"],
    VoiceCommand.VOLUME_DOWN: ["volume down", "quieter", "softer"],
    VoiceCommand.MUTE: ["mute", "silence"],
    VoiceCommand.MEDIA_NEXT: ["next", "next track", "skip"],
    VoiceCommand.MEDIA_PREV: ["previous", "back", "previous track"],
    VoiceCommand.MEDIA_PLAY: ["play", "pause", "play pause"],
    VoiceCommand.MINIMIZE: ["minimize", "minimize window"],
    VoiceCommand.CLOSE_WINDOW: ["close window", "close", "close this"],
    VoiceCommand.TASK_SWITCHER: ["switch window", "task view", "alt tab", "switch app"],
    VoiceCommand.SHOW_DESKTOP: ["show desktop", "go home", "desktop"],
    VoiceCommand.SCREENSHOT: ["screenshot", "screen shot", "capture screen"],
    VoiceCommand.VOICE_OFF: ["voice off", "stop listening", "shut up", "stop voice"],
    VoiceCommand.QUIT: ["quit", "exit", "goodbye", "shut down airmouse"],
}


# Sensitivity profiles. "turbo" is the MAD mode: listens nonstop, matches
# fuzzier, and fires faster than the other profiles.
SENSITIVITY_PROFILES: Dict[str, Dict[str, float]] = {
    "normal": {
        "match_threshold": 0.78,       # strict fuzzy ratio
        "phrase_time_limit": 5.0,      # max seconds of captured speech
        "pause_threshold": 0.8,        # silence that ends a phrase
        "energy_threshold": 300,       # mic energy gate
        "adjust_for_noise": True,      # ambient-noise calibration per cycle
        "wake_word_required": True,    # say "airmouse ..." first
        "command_cooldown": 1.0,       # min seconds between fired commands
    },
    "high": {
        "match_threshold": 0.60,
        "phrase_time_limit": 3.5,
        "pause_threshold": 0.45,
        "energy_threshold": 200,
        "adjust_for_noise": True,
        "wake_word_required": False,
        "command_cooldown": 0.55,
    },
    "turbo": {
        "match_threshold": 0.45,
        "phrase_time_limit": 2.5,
        "pause_threshold": 0.25,
        "energy_threshold": 120,
        "adjust_for_noise": True,
        "wake_word_required": False,
        "command_cooldown": 0.30,
    },
}


# ---------------------------------------------------------------------------
# Pure text-matching helpers (no audio dependencies — unit-testable)
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^a-z0-9\s]+")


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Never raises."""
    try:
        lowered = str(text or "").lower()
    except Exception:
        return ""
    return " ".join(_PUNCT_RE.sub(" ", lowered).split())


def _ratio(a: str, b: str) -> float:
    """difflib similarity ratio between two short strings (0.0 .. 1.0)."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_command(transcript: str, sensitivity: str = "high") -> Tuple[str, float]:
    """Match a transcript against COMMANDS and return (command, score).

    Pure function — no audio imports, safe for unit tests.

    Scoring:
      * substring containment of a trigger phrase in the transcript -> 1.0
      * otherwise the best difflib.SequenceMatcher ratio of the phrase
        against the whole transcript and against same-word-count windows
        of the transcript.
    Ties prefer the LONGEST phrase, so "stop recording" beats "stop" and
    "play macro" beats "play".

    Returns (VoiceCommand.NONE, 0.0) when the transcript is empty or the
    best score is below the profile's match_threshold.
    """
    if not isinstance(transcript, str) or not transcript.strip():
        return VoiceCommand.NONE, 0.0
    profile = SENSITIVITY_PROFILES.get(sensitivity) or SENSITIVITY_PROFILES["high"]
    threshold = float(profile["match_threshold"])

    norm = _normalize(transcript)
    if not norm:
        return VoiceCommand.NONE, 0.0
    tokens = norm.split()

    best_command = VoiceCommand.NONE
    best_score = -1.0
    best_phrase_len = -1

    for command, phrases in COMMANDS.items():
        for phrase in phrases:
            p = _normalize(phrase)
            if not p:
                continue
            if p in norm:
                score = 1.0
            else:
                score = _ratio(norm, p)
                n_words = len(p.split())
                if 0 < n_words <= len(tokens):
                    for i in range(len(tokens) - n_words + 1):
                        window = " ".join(tokens[i:i + n_words])
                        w_score = _ratio(window, p)
                        if w_score > score:
                            score = w_score
            # Strictly better, or tied but with a longer (more specific) phrase.
            if score > best_score or (score == best_score and len(p) > best_phrase_len):
                best_score = score
                best_command = command
                best_phrase_len = len(p)

    if best_score >= threshold:
        return best_command, float(best_score)
    return VoiceCommand.NONE, 0.0


# ---------------------------------------------------------------------------
# Live engine (background listener thread + thread-safe command queue)
# ---------------------------------------------------------------------------

# Cached speech_recognition module (None when unavailable). Imported lazily
# inside a guarded try block so the package works without it installed.
_SR = None
_SR_CHECKED = False


def _import_speech_recognition():
    """Lazy, cached, guarded import of speech_recognition. Never raises."""
    global _SR, _SR_CHECKED
    if not _SR_CHECKED:
        _SR_CHECKED = True
        try:
            import speech_recognition as _sr_mod  # optional dependency
            _SR = _sr_mod
        except Exception:
            _SR = None
    return _SR


class VoiceCommandEngine:
    """Background voice listener producing commands consumable via poll().

    Lifecycle:
        engine = VoiceCommandEngine(sensitivity="high", mic_index=None)
        engine.start()          # launches daemon listener thread
        cmd = engine.poll()     # call every frame from the main loop
        engine.stop()           # on shutdown

    Notes:
        * The microphone is NOT opened in __init__ — only in start()'s thread.
        * available/is_available() is True only if speech_recognition imports
          AND a Recognizer can be constructed; everything else degrades to a
          no-op engine whose poll() always returns VoiceCommand.NONE.
        * on_transcript(transcript, command, score) is invoked (guarded)
          only when a command actually fires (past wake gate + cooldown).
        * last_transcript / last_score update on every recognized utterance;
          active_command holds the most recently fired command.
        * In "normal" sensitivity a wake word ("airmouse ...") is required;
          hearing it arms command matching for 10 seconds.
    """

    def __init__(self, sensitivity: str = "high",
                 on_transcript: Optional[Callable[[str, str, float], None]] = None,
                 mic_index: Optional[int] = None) -> None:
        self.sensitivity: str = sensitivity if sensitivity in SENSITIVITY_PROFILES else "high"
        self.on_transcript = on_transcript
        self.mic_index: Optional[int] = mic_index

        self.available: bool = False
        try:
            sr = _import_speech_recognition()
            if sr is not None:
                _ = sr.Recognizer()  # verify the Recognizer can be created
                self.available = True
        except Exception:
            self.available = False

        self.last_transcript: str = ""
        self.last_score: float = 0.0
        self.active_command: str = VoiceCommand.NONE
        self.enabled: bool = True          # toggle() gate; thread stays alive
        self.listening: bool = False       # True while the thread is running

        self._queue: "collections.deque[str]" = collections.deque(maxlen=8)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_fire: float = 0.0       # monotonic time of last fired cmd
        self._wake_until: float = 0.0      # monotonic wake-word arm deadline
        self._error_backoff: float = _BACKOFF_MIN
        self._tts_engine: object = None    # lazy pyttsx3 engine; False = failed

    # -- availability -------------------------------------------------------

    def is_available(self) -> bool:
        """True when speech_recognition is importable and usable."""
        return bool(self.available)

    # -- thread lifecycle ----------------------------------------------------

    def start(self) -> bool:
        """Launch the daemon listener thread (idempotent). Returns available."""
        if self._thread is not None and self._thread.is_alive():
            return self.available
        self._stop_event.clear()
        self._error_backoff = _BACKOFF_MIN
        self._thread = threading.Thread(
            target=self._loop, name="VoiceCommandEngine", daemon=True)
        self._thread.start()
        self.listening = True
        return self.available

    def stop(self) -> None:
        """Signal the listener thread to exit and drain the command queue."""
        self._stop_event.set()
        self.listening = False
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() \
                and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            self._queue.clear()

    def toggle(self) -> bool:
        """Pause/resume listening. Returns the new enabled state.

        The listener thread stays alive while disabled; the loop simply
        gates on this flag, so toggling back is instant.
        """
        self.enabled = not self.enabled
        return self.enabled

    def set_sensitivity(self, s: str) -> None:
        """Switch sensitivity profile live ("normal" / "high" / "turbo")."""
        if s in SENSITIVITY_PROFILES:
            self.sensitivity = s

    # -- main-loop API --------------------------------------------------------

    def poll(self) -> str:
        """Pop the oldest queued command (VoiceCommand.NONE when empty).

        Called once per frame by the main loop; thread-safe.
        """
        try:
            with self._lock:
                return self._queue.popleft()
        except IndexError:
            return VoiceCommand.NONE

    def speak(self, text: str) -> None:
        """Optional TTS via pyttsx3; falls back to a printed line.

        Short-lived and fully guarded — never raises. The TTS engine is
        initialized lazily on first use and cached; a failed init disables
        TTS for the process and sticks to the print fallback.
        """
        try:
            if self._tts_engine is False:
                print("[voice] {}".format(text))
                return
            if self._tts_engine is None:
                import pyttsx3  # optional dependency, guarded
                self._tts_engine = pyttsx3.init()
            self._tts_engine.say(text)
            self._tts_engine.runAndWait()
        except Exception:
            self._tts_engine = False
            try:
                print("[voice] {}".format(text))
            except Exception:
                pass

    # -- listener thread internals --------------------------------------------

    def _loop(self) -> None:
        """Listener loop — never dies; every iteration is guarded."""
        while not self._stop_event.is_set():
            try:
                self._cycle()
            except Exception as exc:
                if _DEBUG:
                    try:
                        print("[voice] cycle error: {!r}".format(exc))
                    except Exception:
                        pass
                # Repeated mic errors: sleep with backoff 0.5s -> 5.0s.
                self._stop_event.wait(min(self._error_backoff, _BACKOFF_MAX))
                self._error_backoff = min(self._error_backoff * 2.0, _BACKOFF_MAX)

    def _cycle(self) -> None:
        """One listen/recognize cycle. Raises on mic errors (-> backoff)."""
        sr = _import_speech_recognition()
        if sr is None:
            # Library missing — keep the thread alive but idle.
            self._stop_event.wait(1.0)
            return
        if not self.enabled:
            # Toggled off — gate the loop, keep the thread warm.
            self._stop_event.wait(0.2)
            return

        profile = SENSITIVITY_PROFILES[self.sensitivity]

        # Fresh Microphone per listen cycle; raising here (e.g. no PyAudio,
        # device busy) is handled by the loop's backoff handler.
        mic = sr.Microphone(device_index=self.mic_index)
        recognizer = sr.Recognizer()
        try:
            recognizer.energy_threshold = float(profile["energy_threshold"])
            recognizer.pause_threshold = float(profile["pause_threshold"])
        except Exception:
            pass  # attribute tweaks are best-effort across sr versions

        with mic as source:
            if profile.get("adjust_for_noise"):
                try:
                    recognizer.adjust_for_ambient_noise(source, duration=0.5)
                except Exception:
                    pass  # calibration is best-effort
            try:
                audio = recognizer.listen(
                    source,
                    timeout=max(0.5, float(profile["pause_threshold"])),
                    phrase_time_limit=float(profile["phrase_time_limit"]),
                )
            except getattr(sr, "WaitTimeoutError", RuntimeError):
                # Silence within the timeout window — healthy mic, just quiet.
                self._error_backoff = _BACKOFF_MIN
                return
            # OSError / anything else propagates to the loop backoff handler.

        if audio is None:
            return
        try:
            transcript = recognizer.recognize_google(audio)  # free web API
        except getattr(sr, "UnknownValueError", Exception):
            return  # unintelligible — continue silently
        except getattr(sr, "RequestError", Exception):
            self._stop_event.wait(0.5)  # web API hiccup — brief polite pause
            return
        except Exception:
            raise  # unexpected recognizer failure -> backoff handler

        self._error_backoff = _BACKOFF_MIN  # mic healthy again
        self._handle_transcript(str(transcript or ""))

    def _handle_transcript(self, raw: str) -> None:
        """Wake-word gate + command matching + queueing (thread context)."""
        transcript = _normalize(raw)
        if not transcript:
            return

        profile = SENSITIVITY_PROFILES[self.sensitivity]
        if profile.get("wake_word_required"):
            now = time.monotonic()
            wake = next((w for w in _WAKE_WORDS if w in transcript), None)
            if wake is not None:
                # Wake heard: arm command matching for a short window and
                # try to match the remainder of this same utterance.
                self._wake_until = now + _WAKE_WINDOW_S
                remainder = transcript.replace(wake, " ", 1).strip()
                if not remainder:
                    return  # wake word alone — armed, nothing to fire yet
                transcript = remainder
            elif now >= self._wake_until:
                return  # sleeping — ignore everything until woken

        command, score = match_command(transcript, self.sensitivity)
        self.last_transcript = transcript
        self.last_score = float(score)

        if command == VoiceCommand.NONE:
            return
        now = time.monotonic()
        if now - self._last_fire < float(profile["command_cooldown"]):
            return
        self._last_fire = now
        self.active_command = command

        with self._lock:
            self._queue.append(command)

        callback = self.on_transcript
        if callback is not None:
            try:
                callback(transcript, command, float(score))
            except Exception:
                pass  # user callback must never kill the listener


# ---------------------------------------------------------------------------
# Manual smoke test:  python3 -m airmouse.voice_control
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    def _on_transcript(text: str, command: str, score: float) -> None:
        print("[voice] heard '{0}' -> {1} (score {2:.2f})".format(text, command, score))

    engine = VoiceCommandEngine(on_transcript=_on_transcript)
    print("[voice] available: {0}".format(engine.is_available()))
    print("[voice] listening for 20s — try 'zoom in', 'click', 'quit' ...")
    engine.start()
    deadline = time.time() + 20.0
    try:
        while time.time() < deadline:
            cmd = engine.poll()
            if cmd != VoiceCommand.NONE:
                print("[voice] COMMAND: {0}".format(cmd))
                if cmd == VoiceCommand.QUIT:
                    break
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        print("[voice] demo done")
