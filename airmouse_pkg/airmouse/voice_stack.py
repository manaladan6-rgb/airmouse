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
* :func:`pick_provider` — the best AVAILABLE local ASR provider.
  v16.5.2: DELEGATES to the authoritative
  :class:`airmouse.asr_registry.ASRProviderRegistry` — the ONE
  selection implementation shared with doctor / transcribe /
  voice-status.  Default priority is now lightweight-first
  (pocketsphinx > vosk > whisper; ``prefer`` overrides).  Constructors
  are guarded; a broken engine is skipped, never raised.
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
    "render_voice_status",
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

#: default preference order for local ASR engines (v16.5.2 §2:
#: LIGHTWEIGHT FIRST — PocketSphinx is the minimal installed provider;
#: vosk/whisper are the higher-quality optional engines; ``prefer``
#: and explicit configuration override the order)
_ENGINE_ORDER = ("pocketsphinx", "vosk", "whisper")

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

    v16.5.2: uses the ONE unified detector
    (:func:`airmouse.asr_registry.detect_microphone`) — sounddevice /
    PortAudio FIRST (the lightest existing stack; PyAudio is NOT a
    hard requirement), with the SpeechRecognition/PyAudio enumeration
    as fallback.  A headless sandbox reports ``False``.  Never raises.
    """
    try:
        from .asr_registry import detect_microphone as _unified
        return bool(_unified().get("available"))
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

    v16.5.2: delegates to the authoritative
    :class:`airmouse.asr_registry.ASRProviderRegistry.select()` so
    doctor, transcribe, voice-status and this function can never
    disagree again.  Default priority is lightweight-first
    (pocketsphinx > vosk > whisper); ``prefer`` acts as the explicit
    configuration override.  Every candidate must prove itself via the
    registry's importability gate + real initialization probe +
    ``available()`` — a missing or broken engine is skipped, never
    faked.  Never raises.
    """
    try:
        from .asr_registry import registry
        return registry.select(prefer=prefer)
    except Exception:
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
# v16.5.2 §8 — the AUTHORITATIVE voice-status renderer
# ---------------------------------------------------------------------------

def render_voice_status(verbose: bool = False) -> str:
    """``airmouse voice-status [--verbose]`` — THE debugging tool.

    One call to the ONE registry (mission §2/§3/§8): microphone,
    per-provider honest rows, and the transcription verdict
    (real-mic availability, offline, storage, network).  ``verbose``
    adds per-provider detail (probe result + reason + FIX).  Never
    raises.
    """
    try:
        from .asr_registry import registry
        diag = registry.diagnose()
    except Exception as exc:
        return f"AIRMouse Voice Status\n\nregistry unavailable: {exc}"

    mic = diag.get("microphone", {})
    providers = diag.get("providers", {})
    active = diag.get("active")

    lines: List[str] = ["AIRMouse Voice Status", ""]

    # -- MICROPHONE ---------------------------------------------------------
    lines.append("MICROPHONE")
    if mic.get("available"):
        lines.append(f"  {_CHECK_MARK} detected")
        lines.append(f"  devices: {mic.get('device_count', 0)}")
        dev = (mic.get("devices") or [{}])[0]
        lines.append(f"  selected: {dev.get('name', 'Default')}")
        lines.append(f"  backend: {mic.get('backend', 'PortAudio')}")
        sr_v = dev.get("sample_rate") or 16000
        lines.append(f"  sample rate: {sr_v}")
        lines.append(f"  channels: {dev.get('channels', 1)}")
    else:
        lines.append("  ○ not detected")
        if mic.get("error"):
            lines.append(f"  error: {mic['error']}")
        lines.append("  FIX: connect a microphone and grant permission, "
                     "then re-run this command")
    lines.append("")

    # -- ASR PROVIDERS --------------------------------------------------------
    lines.append("ASR PROVIDERS")
    lines.append("")
    for name, row in providers.items():
        lines.append(f"  {name.capitalize()}")
        installed = bool(row.get("installed"))
        lines.append(f"    installed: {'YES' if installed else 'NO'}")
        lines.append(f"    importable: "
                     f"{'YES' if row.get('importable') else 'NO'}")
        if name == "pocketsphinx":
            lines.append("    model: available (bundled en-US, loads with "
                         "the engine)")
        elif name == "vosk" and \
                row.get("initialization") == "MODEL MISSING":
            # v16.5.3 — the engine is fine, the MODEL is the gap;
            # the fix is voice-setup, never a bare pip line
            lines.append("    model: MISSING — run: airmouse voice-setup "
                         "(official lightweight model, consent-gated, "
                         "sha256-verified)")
        else:
            lines.append("    model: on first use (never auto-downloaded)")
        init = row.get("initialization", "NOT RUN")
        lines.append(f"    initialization: {init}")
        runtime = ("READY" if row.get("state") in
                   ("INITIALIZABLE", "AVAILABLE", "ACTIVE") else
                   "FAILED" if row.get("state") == "FAILED" else "NO")
        lines.append(f"    runtime: {runtime}")
        lines.append(f"    role: {row.get('role', 'OPTIONAL')}")
        if verbose and row.get("reason"):
            lines.append(f"    reason: {row['reason']}")
        if verbose and row.get("init_seconds"):
            lines.append(f"    probe time: {row['init_seconds']:.3f}s")
        lines.append("")

    # -- TRANSCRIPTION --------------------------------------------------------
    lines.append("TRANSCRIPTION")
    real = bool(active)
    lines.append(f"  real microphone: "
                 f"{'AVAILABLE' if (real and mic.get('available')) else 'NOT AVAILABLE'}")
    if real:
        lines.append(f"  provider: {active}")
        mode = "MIC LIVE" if mic.get("available") else "TEXT INPUT"
        lines.append(f"  mode: {mode}")
    else:
        lines.append("  provider: none (built-in command grammar + "
                     "simulated stream only)")
    lines.append(f"  offline: "
                 f"{'YES' if diag.get('offline') else 'UNKNOWN'}")
    lines.append(f"  audio storage: {diag.get('audio_storage', 'NONE')}")
    lines.append(f"  network: "
                 f"{'NOT REQUIRED' if not diag.get('network_required') else 'REQUIRED'}")
    lines.append("")
    if not real:
        # v16.5.3 — the install hint is BOOTSTRAP-AWARE: on a machine
        # where `pip install pocketsphinx` would fail (no prebuilt
        # wheel / would need a compiler) the honest advice is the
        # zero-compiler voice-setup path instead.
        hint = ""
        try:
            from .voice_bootstrap import bootstrap_manager
            _rep, plan = bootstrap_manager.recommend_installation()
            opts = [p for p in plan if p.verdict == "WHEEL_AVAILABLE"]
            if opts:
                first = opts[0]
                extra = f" + {first.model_extra_mb:.1f} MB model" \
                    if first.model_extra_mb else ""
                hint = (f"Run: airmouse voice-setup   (installs "
                        f"{first.engine} — {first.wheel_mb:.1f} MB "
                        f"prebuilt wheel{extra}, NO compiler, official "
                        f"sources only)")
            elif _rep.diagnosis:
                hint = (f"Diagnosis: {_rep.diagnosis} — a prebuilt local "
                        f"ASR wheel for this interpreter is not "
                        f"available (a compiler build is never "
                        f"attempted).  Run: airmouse voice-setup "
                        f"--check-only for the full report and options.")
        except Exception:
            hint = ""
        lines.append("Install the lightweight local ASR (no cloud, no "
                     "telemetry, no compiler):")
        if hint:
            lines.append(f"  {hint}")
        else:
            lines.append("  airmouse voice-setup")
            lines.append("  (manual option: pip install pocketsphinx "
                         "SpeechRecognition)")
        lines.append("Then re-run: airmouse voice-status")
    if verbose:
        lines.append("")
        lines.append("Commands that use this stack:")
        lines.append("  airmouse transcribe          # MIC LIVE when a real "
                     "provider + mic exist")
        lines.append("  airmouse transcribe --text   # explicit TEXT PRACTICE")
        lines.append("  airmouse transcribe --mic N  # pick an input device")
        lines.append("  airmouse teach voice         # lessons through the "
                     "real pipeline")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# optional install guidance (§5) — information only, never actioned
# ---------------------------------------------------------------------------

def install_guidance() -> str:
    """Exact optional-install guidance for local ASR engines.

    v16.5.3: the PRIMARY path is ``airmouse voice-setup`` — the
    deterministic, zero-compiler bootstrap (prebuilt wheels only,
    official sources only, consent-gated model fetch).  Manual pip
    commands remain listed for users who prefer them.
    """
    return "\n".join([
        "Local voice engines (100% offline after setup):",
        "",
        "  RECOMMENDED: airmouse voice-setup",
        "    -> zero-compiler bootstrap: picks the smallest COMPATIBLE",
        "       prebuilt wheel for THIS Python (PocketSphinx, or Vosk),",
        "       installs via pip --only-binary (never builds from "
        "source),",
        "       fetches the official lightweight model with consent +",
        "       SHA256 verification, then verifies the whole chain.",
        "",
        "  Manual alternatives:",
        "  pip install vosk                            # Vosk offline ASR (lightweight)",
        "  pip install SpeechRecognition pocketsphinx  # CMU PocketSphinx (classic)",
        "  pip install faster-whisper                  # Whisper-quality local ASR",
        "",
        "Honesty note:",
        "- No network is used during recognition — no cloud STT, no "
        "telemetry,",
        "  no audio upload.  The one-time consented model fetch during "
        "setup is",
        "  the only download AirMouse ever makes, and it is "
        "SHA256-verified.",
        "- AirMouse never compiles native software and never installs a",
        "  compiler toolchain; PocketSphinx source builds are refused.",
        "- On Windows/CPython 3.12 PocketSphinx has NO prebuilt wheel —",
        "  voice-setup routes to Vosk instead (works on any CPython 3).",
        "- The built-in command recognition is a deterministic grammar — it is",
        "  a real command system, not simulated ASR, but it is NOT free-form",
        "  speech recognition.  Full speech recognition needs one engine above.",
        "- Models are fetched once with consent, SHA256-verified, stored under",
        "  the AirMouse home, and never uploaded anywhere.",
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
