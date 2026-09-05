"""Voice Stack status (v16.5, mission §5) — what is REAL, what is not.

The v16.5 mission gives voice the same engineering honesty as gestures:
a real architecture (Microphone → preprocessing → VAD → wake/activation →
streaming ASR → partial → final → normalization → intent/dictation router
→ context → safety → action → verification) with PLUGGABLE LOCAL
providers — never one heavyweight mandatory engine.

This module is the honest face of that stack:

* :func:`detect_voice_stack` — what is importable on THIS machine right
  now (``importlib.util.find_spec`` based; nothing is ever pretended).
  ``microphone`` is ``False`` unless hardware is actually detectable —
  a headless sandbox reports ``False`` because it has none.
* :func:`status_panel` — the §5 provider panel.  Exactly one ``Active:``
  line.  ``✓`` means installed/usable, ``○`` means missing.  When no ASR
  engine exists the panel says so in plain words and ``Active`` stays
  ``Built-in command recognition`` — the deterministic grammar is a REAL
  command system, not simulated ASR, and it keeps working without any
  engine.
* :func:`pick_provider` — the best AVAILABLE local ASR provider
  (vosk > whisper > pocketsphinx by default, ``prefer`` overrides).
  Constructors are guarded; a broken engine is skipped, never raised.
* :func:`install_guidance` — exact optional-install commands.  NO
  auto-install, NO network calls, ever.
* :func:`offer_voice_pack` — the §5 opt-in prompt.  It prints guidance
  on ``Y`` and returns; it NEVER installs anything itself and NEVER
  raises.

HONESTY IS LOAD-BEARING: a deterministic command grammar is not ASR and
is never labelled as such; a missing engine is always reported missing.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import importlib.util
import sys
from typing import Any, Callable, Dict, Optional

try:  # module-global indirection — tests monkeypatch this to simulate
    from importlib.util import find_spec  # installed/uninstalled engines
except Exception:  # pragma: no cover
    find_spec = None  # type: ignore[assignment]

from .offline_voice import (
    OfflineSpeechProvider,
    PocketSphinxProvider,
    VoskProvider,
    WhisperProvider,
)

__all__ = [
    "detect_voice_stack",
    "status_panel",
    "pick_provider",
    "install_guidance",
    "offer_voice_pack",
    "BUILT_IN_ACTIVE_LABEL",
]

#: what ``active`` / the panel say when no local ASR engine is usable
BUILT_IN_ACTIVE_LABEL = "Built-in command recognition"

#: module names probed per engine family (faster_whisper counts as whisper)
_ENGINE_MODULES: Dict[str, tuple] = {
    "vosk": ("vosk",),
    "whisper": ("whisper", "faster_whisper"),
    "pocketsphinx": ("pocketsphinx",),
}

#: default preference order for local ASR engines (mission §5: pluggable,
#: never one mandatory engine); ``prefer`` overrides the order
_ENGINE_ORDER = ("vosk", "whisper", "pocketsphinx")

#: engine name → constructor global (kept separate so tests can
#: monkeypatch either the flags or the constructors)
_ENGINE_CTORS: Dict[str, str] = {
    "vosk": "VoskProvider",
    "whisper": "WhisperProvider",
    "pocketsphinx": "PocketSphinxProvider",
}

_CHECK_MARK = "✓"
_CIRCLE_MARK = "○"


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

def _spec_found(module: str) -> bool:
    """True when ``module`` is importable on this machine (never raises)."""
    try:
        return find_spec(module) is not None
    except Exception:
        return False


def _detect_microphone() -> bool:
    """True only when input hardware is actually detectable.

    Requires PyAudio (the only microphone backend wired today) plus a
    successful device enumeration through SpeechRecognition.  A headless
    sandbox has neither → ``False``.  Never raises.
    """
    if not _spec_found("pyaudio"):
        return False
    try:
        import speech_recognition as sr  # optional dependency
        return bool(sr.Microphone.list_microphone_names())
    except Exception:
        return False


def detect_voice_stack() -> Dict[str, Any]:
    """Honest snapshot of the voice stack on THIS machine.

    Keys (exactly these):
        built_in_grammar   always True — the deterministic command
                           grammar ships with the package
        speech_recognition SpeechRecognition wrapper importable
        pyaudio            PyAudio (microphone capture) importable
        pocketsphinx       CMU PocketSphinx engine importable
        vosk               Vosk engine importable
        whisper            whisper OR faster_whisper importable
        microphone         input hardware actually detectable (else False)
        active             engine name picked by :func:`pick_provider`,
                           or ``"Built-in command recognition"``
    """
    engines = {name: any(_spec_found(m) for m in mods)
               for name, mods in _ENGINE_MODULES.items()}
    provider = pick_provider()
    active = ""
    if provider is not None:
        try:
            active = str(getattr(provider, "name", "") or "")
        except Exception:
            active = ""
    return {
        "built_in_grammar": True,
        "speech_recognition": _spec_found("speech_recognition"),
        "pyaudio": _spec_found("pyaudio"),
        "pocketsphinx": engines["pocketsphinx"],
        "vosk": engines["vosk"],
        "whisper": engines["whisper"],
        "microphone": _detect_microphone(),
        "active": active or BUILT_IN_ACTIVE_LABEL,
    }


# ---------------------------------------------------------------------------
# provider selection
# ---------------------------------------------------------------------------

def _engine_ctor(name: str) -> Optional[Callable[..., OfflineSpeechProvider]]:
    """Resolve an engine constructor from module globals (monkeypatch-
    friendly); unknown names yield ``None``."""
    try:
        return globals().get(_ENGINE_CTORS.get(name, ""))  # type: ignore[return-value]
    except Exception:
        return None


def pick_provider(prefer: Optional[str] = None) -> Optional[OfflineSpeechProvider]:
    """Best AVAILABLE local ASR provider, or ``None`` when none exists.

    Default preference: vosk > whisper > pocketsphinx.  ``prefer``
    (engine name, case-insensitive) is tried first; every candidate must
    still prove itself via ``available()`` — a missing or broken engine
    is skipped, never faked.  Constructed defensively: a constructor
    that raises simply disqualifies that engine.  Never raises.
    """
    order = list(_ENGINE_ORDER)
    want = str(prefer or "").strip().lower()
    if want:
        matched = [n for n in order if n == want]
        if matched:
            order.remove(matched[0])
            order.insert(0, matched[0])
    for name in order:
        mods = _ENGINE_MODULES.get(name, ())
        if mods and not any(_spec_found(m) for m in mods):
            continue  # honest gate: the engine package is not importable
        ctor = _engine_ctor(name)
        if ctor is None:
            continue
        try:
            provider = ctor()
        except Exception:
            continue
        try:
            if provider is not None and provider.available():
                return provider
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# rendering (§5)
# ---------------------------------------------------------------------------

def _mark(ok: bool) -> str:
    return _CHECK_MARK if ok else _CIRCLE_MARK


def status_panel() -> str:
    """The §5 Voice Provider panel — honest per-machine rendering.

    ``✓`` = installed / usable, ``○`` = missing.  Exactly one
    ``Active:`` line.  When no ASR engine is installed the panel says
    ``Full speech recognition is not installed.`` and Active stays on
    the built-in deterministic grammar (which is real, working code).
    """
    d = detect_voice_stack()
    any_engine = bool(d["pocketsphinx"] or d["vosk"] or d["whisper"])
    lines = [
        "Voice Provider",
        "──────────────",
        f"{_mark(True)} Built-in command recognition",
        f"{_mark(any_engine)} Local ASR available",
        f"{_mark(bool(d['whisper']))} Whisper available",
        f"{_mark(bool(d['vosk']))} Vosk available",
        f"{_mark(bool(d['microphone']))} Microphone detected",
        f"Active: {d['active']}",
    ]
    if not any_engine:
        lines.append("Full speech recognition is not installed.")
        lines.append("Command recognition still works — it is deterministic "
                     "grammar, not ASR.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# optional install guidance (§5) — information only, never actioned
# ---------------------------------------------------------------------------

def install_guidance() -> str:
    """Exact optional-install commands for local ASR engines.

    Pure text: NO auto-install, NO network calls, no subprocess.  The
    user runs the pip command themselves if and when they choose.
    """
    return "\n".join([
        "Optional local voice engines (100% offline — nothing auto-installs):",
        "",
        "  pip install vosk                            # Vosk offline ASR (lightweight)",
        "  pip install SpeechRecognition pocketsphinx  # CMU PocketSphinx (classic)",
        "  pip install faster-whisper                  # Whisper-quality local ASR",
        "",
        "Honesty note:",
        "- AirMouse NEVER installs anything by itself and makes no network",
        "  calls for this; you run the command, the engine stays local.",
        "- The built-in command recognition is a deterministic grammar — it is",
        "  a real command system, not simulated ASR, but it is NOT free-form",
        "  speech recognition.  Full speech recognition needs one engine above.",
        "- Models (e.g. a vosk model) download on first use by the ENGINE you",
        "  installed, after you point AirMouse at it.",
    ])


def offer_voice_pack(out: Any = None,
                     input_fn: Optional[Callable[[str], str]] = None) -> bool:
    """The §5 opt-in prompt for the Local Voice Pack.

    Prints the prompt; on ``Y`` prints :func:`install_guidance` and
    returns True (the user still has to run the pip command themselves —
    this function NEVER installs anything).  ``N`` / EOF / any error
    returns False.  When a local ASR engine is already available the
    offer is skipped honestly.  Never raises.
    """
    w = out if out is not None else sys.stdout

    def _emit(text: str) -> None:
        try:
            w.write(text + "\n")
            try:
                w.flush()
            except Exception:
                pass
        except Exception:
            pass

    if pick_provider() is not None:
        _emit("A local ASR engine is already available — no install needed "
              "(see: airmouse voice-status).")
        return False

    _emit("Command recognition works. Full speech recognition is not "
          "installed. Install Local Voice Pack? [Y] Yes [N] Not now")
    ask = input_fn
    if ask is None:
        try:
            if not sys.stdin or not sys.stdin.isatty():
                return False  # non-interactive: never block, never install
        except Exception:
            return False
        ask = input
    try:
        raw = ask("")
    except EOFError:
        return False
    except KeyboardInterrupt:
        return False
    except Exception:
        return False
    answer = str(raw or "").strip().upper()
    if answer in ("Y", "YES"):
        _emit(install_guidance())
        return True
    return False
