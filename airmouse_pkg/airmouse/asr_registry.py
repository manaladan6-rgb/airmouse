"""asr_registry — THE authoritative local ASR provider registry (v16.5.2).

The v16.5.1 Windows reality check exposed the exact bug this module
fixes: doctor and transcribe each had their OWN provider-detection
implementation, and they disagreed on the same machine::

    doctor      -> detect_providers()  -> hasattr(sr.Recognizer(),
                                            "recognize_sphinx")   -> ACTIVE
    transcribe  -> pick_provider()     -> find_spec("pocketsphinx")  -> None
               -> simulated_stream

``hasattr`` never imports the engine — it is True whenever
SpeechRecognition is installed, so doctor could report a provider that
cannot actually run.  transcribe was honest, but the divergence itself
is the defect (mission §0 rule 9: never silently downgrade REAL LOCAL
ASR to SIMULATED TEXT INPUT while a usable provider exists).

v16.5.2 law: there is EXACTLY ONE registry.  Doctor, transcribe,
voice-status, the Voice Academy and offline-test ALL call it.

    doctor      -> ASRProviderRegistry.active()
    transcribe  -> ASRProviderRegistry.select()  (same instance)

States (mission §2, exactly these)::

    NOT_INSTALLED   no importable engine package
    INSTALLED       importable, runtime initialization not yet proven
    AVAILABLE       importable, initialization verified at import level
                    only (vosk/whisper: the model loads on first use)
    INITIALIZABLE   importable AND a real engine initialization PASSED
                    (PocketSphinx: an actual Decoder is built — 0.25 s,
                    cached — proving engine + acoustics model work)
    ACTIVE          the provider select() chose for this session
    FAILED          installed but import/initialization FAILED (reason
                    is captured and shown — never swallowed)
    DISABLED        excluded by explicit user configuration

Selection is DETERMINISTIC (mission §2 priority)::

    1. explicit user configuration   (env AIRMOUSE_ASR_PROVIDER,
                                      config [voice] asr_provider,
                                      CLI --provider)
    2. previously selected working provider (persisted, best-effort)
    3. lightweight local provider    (PocketSphinx)
    4. higher-quality local provider (Vosk, then Whisper)
    5. simulation ONLY if none work

Engine primitives (module tables, importability gates) are shared with
:mod:`airmouse.voice_stack` so there is one set of engine facts; the
registry adds the real-initialization probe, the state machine and the
deterministic selection.  The probe result is cached process-wide.

Honesty is load-bearing: ACTIVE is reported ONLY after a real
initialization succeeded; a FAILED probe keeps its reason and is
surfaced by doctor / voice-status / transcribe.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import paths
from . import voice_stack as _vs
from .offline_voice import OfflineSpeechProvider

__all__ = [
    "ProviderState", "ProviderDiagnosis", "ASRProviderRegistry",
    "registry", "detect_microphone", "active_asr_provider",
    "PROBE_POCKETSPHINX", "PROBE_VOSK", "PROBE_WHISPER",
    "VoskModelMissing",  # v16.5.3 — engine present, model absent
    "verify_offline_path",  # R3c — engine-installed vs model-available
    "offline_capability_report",  #        vs offline-path-works
]


# ---------------------------------------------------------------------------
# states
# ---------------------------------------------------------------------------

class ProviderState:
    """The seven authoritative provider states (mission §2)."""

    NOT_INSTALLED = "NOT_INSTALLED"
    INSTALLED = "INSTALLED"
    AVAILABLE = "AVAILABLE"
    INITIALIZABLE = "INITIALIZABLE"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


#: engines the registry manages, in DEFAULT priority order
#: (mission §2: lightweight first — PocketSphinx is the minimal
#:  installed provider; Vosk and Whisper are the higher-quality
#:  optional engines; Whisper is never mandatory, models never
#:  auto-download)
DEFAULT_PRIORITY: tuple = ("pocketsphinx", "vosk", "whisper")

#: provider states that mean "this engine can actually transcribe now"
_USABLE_STATES = (ProviderState.INITIALIZABLE, ProviderState.AVAILABLE)


# ---------------------------------------------------------------------------
# per-engine REAL initialization probes (the anti-hasattr)
# ---------------------------------------------------------------------------

def PROBE_POCKETSPHINX() -> float:
    """Prove PocketSphinx can really run; returns seconds taken.

    Builds an actual ``pocketsphinx.Decoder`` with the default en-US
    acoustics model — the same object SpeechRecognition's offline
    ``recognize_sphinx`` drives.  A module import alone is NOT enough:
    on Windows a broken wheel/DLL imports as metadata but fails here.
    Raises on failure (the registry captures the reason).  Result is
    cached process-wide (the probe costs ~0.25 s).
    """
    global _PS_PROBE_SECONDS
    if _PS_PROBE_SECONDS is not None:
        return _PS_PROBE_SECONDS
    t0 = time.perf_counter()
    import pocketsphinx  # noqa: F401 — the REAL importability gate
    import speech_recognition as sr  # the provider's runtime input path
    if not hasattr(sr.Recognizer(), "recognize_sphinx"):
        raise RuntimeError("SpeechRecognition lacks the offline sphinx "
                           "backend (upgrade SpeechRecognition)")
    cfg = pocketsphinx.Config()
    pocketsphinx.Decoder(cfg)  # real init: engine + default model load
    _PS_PROBE_SECONDS = time.perf_counter() - t0
    return _PS_PROBE_SECONDS


class VoskModelMissing(RuntimeError):
    """vosk imports but NO local acoustics model resolves (v16.5.3).

    Distinct from a broken install: the engine itself is fine, the
    MODEL is the missing capability.  ``airmouse voice-setup`` is the
    fix (official model, consent-gated, sha256-verified) — the probe
    itself NEVER downloads anything.
    """

    def __init__(self, message: str = "vosk engine is installed but no "
                                      "local acoustics model is available") -> None:
        super().__init__(message)
        self.fix = "run: airmouse voice-setup  (installs the official " \
                   "lightweight en-US model — consent-gated, sha256-verified)"


def PROBE_VOSK() -> float:
    """Prove Vosk can really run HERE: import + model resolution.

    ``import vosk`` loads the native Kaldi binding, so a successful
    import proves the engine itself.  What vosk additionally NEEDS is a
    local acoustics model — v16.5.3 resolves it with the deterministic
    :func:`airmouse.offline_voice.resolve_vosk_model_path` (env /
    config / AirMouse models dir).  No model → :class:`VoskModelMissing`
    (honest: engine installed, capability incomplete — never a silent
    "usable" verdict that would diverge from the engine's own
    ``available()``).  The model itself is NOT loaded here (first use,
    DEFERRED) and is NEVER downloaded by the probe.
    """
    t0 = time.perf_counter()
    import vosk  # noqa: F401 — the REAL importability gate (native lib)
    if not hasattr(vosk, "Model"):
        raise RuntimeError("vosk package lacks Model (broken install)")
    from .offline_voice import resolve_vosk_model_path
    if not resolve_vosk_model_path():
        raise VoskModelMissing()
    return time.perf_counter() - t0


def PROBE_WHISPER() -> float:
    """Prove whisper / faster_whisper is importable (DEFERRED init).

    No model download is ever triggered (mission §9: do NOT force large
    model downloads).  Raises when neither package imports.
    """
    t0 = time.perf_counter()
    try:
        import whisper  # noqa: F401
    except Exception:
        from faster_whisper import WhisperModel  # noqa: F401
    return time.perf_counter() - t0


#: engine name -> (voice_stack engine key, probe callable)
_ENGINE_PROBES: Dict[str, Any] = {
    "pocketsphinx": PROBE_POCKETSPHINX,
    "vosk": PROBE_VOSK,
    "whisper": PROBE_WHISPER,
}

#: init probe result cache for PocketSphinx (None = not yet run);
#: vosk/whisper probes are cheap imports and need no cache
_PS_PROBE_SECONDS: Optional[float] = None


# ---------------------------------------------------------------------------
# explicit configuration (mission §2 priority 1)
# ---------------------------------------------------------------------------

def _read_voice_config_key(key: str) -> str:
    """Read ``[voice] <key>`` from the TOML config; ``""`` when absent.

    Deliberately does NOT go through Config() — registry probing must
    never print, never create files, never raise.  ``tomli``/``tomllib``
    absence simply means "no TOML config" (the env var still works).
    """
    try:
        try:
            import tomllib as _toml  # py311+
        except Exception:  # pragma: no cover - py<3.11
            import tomli as _toml  # type: ignore[no-redef]
        path = paths.config_file()
        if not path or not os.path.exists(path):
            return ""
        with open(path, "rb") as fh:
            data = _toml.load(fh)
        v = data.get("voice", {})
        if isinstance(v, dict):
            return str(v.get(key, "") or "").strip()
    except Exception:
        return ""
    return ""


def explicit_provider(prefer: Optional[str] = None) -> str:
    """The explicit user provider choice, if any (highest priority).

    Precedence: ``prefer`` argument (CLI ``--provider``) > env
    ``AIRMOUSE_ASR_PROVIDER`` > config ``[voice] asr_provider``.
    ``"none"``/``"off"`` means the user explicitly wants NO ASR engine
    (the built-in deterministic grammar + simulation only) and is
    returned as-is for the registry to honour — never silently ignored.
    """
    for cand in (str(prefer or "").strip(),
                 os.environ.get("AIRMOUSE_ASR_PROVIDER", "").strip(),
                 _read_voice_config_key("asr_provider")):
        if cand:
            return cand
    return ""


# ---------------------------------------------------------------------------
# persistence of the previously selected working provider (priority 2)
# ---------------------------------------------------------------------------

def _persist_path() -> str:
    return paths.asr_provider_file()


def _load_persisted() -> str:
    try:
        p = _persist_path()
        if not os.path.exists(p):
            return ""
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        name = str(data.get("provider", "") or "").strip()
        return name if name in DEFAULT_PRIORITY else ""
    except Exception:
        return ""


def _persist(name: str) -> None:
    """Best-effort persistence — NEVER raises, NEVER blocks selection."""
    try:
        p = _persist_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"provider": name, "ts": time.time()}, fh)
        os.replace(tmp, p)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# diagnosis record
# ---------------------------------------------------------------------------

@dataclass
class ProviderDiagnosis:
    """One engine's honest state (rendered by voice-status / doctor).

    R3c three-way capability distinction (all three are reported
    separately — no entry may claim readiness it does not have):

    * ``engine_installed``  the engine package really imports here
    * ``model_available``   its acoustics model/weights resolve LOCALLY
                            now (probed); ``None`` = deferred/unverified
                            (whisper loads on first use, never downloads)
    * ``offline_path_ok``   the END-TO-END offline path was proven:
                            a runtime provider constructs and reports
                            ``available()``; ``None`` = not yet verified
    """

    name: str
    installed: bool = False          # import metadata discoverable
    importable: bool = False         # actual import succeeded
    initialization: str = "NOT RUN"  # PASS | FAIL | DEFERRED | NOT RUN
    init_seconds: float = 0.0
    state: str = ProviderState.NOT_INSTALLED
    reason: str = ""                 # failure/detail reason (shown, never hidden)
    role: str = "OPTIONAL"           # ACTIVE | FALLBACK | OPTIONAL | DISABLED
    engine_installed: bool = False   # R3c — the engine itself imports
    model_available: Optional[bool] = False  # R3c — local model resolves
    offline_path_ok: Optional[bool] = None   # R3c — end-to-end path proven

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "installed": self.installed,
            "importable": self.importable,
            "initialization": self.initialization,
            "init_seconds": round(self.init_seconds, 4),
            "state": self.state,
            "reason": self.reason,
            "role": self.role,
            "engine_installed": self.engine_installed,
            "model_available": self.model_available,
            "offline_path_ok": self.offline_path_ok,
        }


# ---------------------------------------------------------------------------
# THE registry
# ---------------------------------------------------------------------------

class ASRProviderRegistry:
    """ONE authoritative source of truth for local ASR providers.

    Doctor, transcribe, voice-status, the academies and offline-test
    ALL go through this single instance (:data:`registry`).  Discovery
    probes each engine honestly (import + real initialization), and
    :meth:`select` is deterministic with the mission §2 priority.
    """

    def __init__(self) -> None:
        self._selected_name: Optional[str] = None
        self._selected_provider: Optional[OfflineSpeechProvider] = None
        self._diagnoses: Dict[str, ProviderDiagnosis] = {}

    # -- discovery -----------------------------------------------------------

    def discover(self, force: bool = False) -> Dict[str, ProviderDiagnosis]:
        """Probe every engine and return name → :class:`ProviderDiagnosis`.

        Never raises, never pretends.  Re-probing is cached by default;
        ``force=True`` re-runs the (cheap, local) probes.
        """
        if self._diagnoses and not force:
            return self._diagnoses
        out: Dict[str, ProviderDiagnosis] = {}
        explicit = explicit_provider()
        disabled_all = explicit.lower() in ("none", "off", "disabled")
        for name in DEFAULT_PRIORITY:
            d = ProviderDiagnosis(name=name)
            if disabled_all:
                d.state = ProviderState.DISABLED
                d.role = "DISABLED"
                d.reason = "disabled by explicit configuration " \
                           "([voice] asr_provider = 'none')"
                out[name] = d
                continue
            # importability — the same engine tables voice_stack uses
            mods = _vs._ENGINE_MODULES.get(name, ())
            installed = any(_vs._spec_found(m) for m in mods) if mods else False
            d.installed = installed
            d.engine_installed = installed   # R3c: the engine gate, alone
            if not installed:
                d.state = ProviderState.NOT_INSTALLED
                d.reason = "engine package not importable " \
                           "(not installed for this Python)"
                d.role = "OPTIONAL"
                out[name] = d
                continue
            # real initialization probe (the anti-hasattr)
            probe = _ENGINE_PROBES.get(name)
            try:
                secs = float(probe())
                d.importable = True
                if name == "pocketsphinx":
                    d.initialization = "PASS"
                    d.state = ProviderState.INITIALIZABLE
                    # the probe BUILT a Decoder with the bundled
                    # acoustics — the model is genuinely available
                    d.model_available = True
                else:
                    # vosk/whisper: import verified; the acoustics model
                    # loads on first use — reported DEFERRED, honestly.
                    # model_available: for vosk the probe itself resolved
                    # a LOCAL model (PROBE_VOSK raises VoskModelMissing
                    # otherwise), so a PASS here IS the model proof;
                    # whisper's weights stay unverified (None) — they
                    # load on first use and are NEVER auto-downloaded.
                    d.initialization = "DEFERRED"
                    d.state = ProviderState.AVAILABLE
                    d.model_available = True if name == "vosk" else None
                    d.reason = ("import verified; acoustics model loads "
                                "on first use (never auto-downloaded)")
                d.init_seconds = secs
            except Exception as exc:
                d.importable = False
                d.initialization = "FAIL"
                d.state = ProviderState.FAILED
                d.reason = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, VoskModelMissing):
                    # v16.5.3 — engine fine, MODEL missing: not FAILED
                    # (the install is healthy) and not AVAILABLE (it
                    # cannot run yet).  INSTALLED = importable, init
                    # unproven — exactly this situation.  The FIX is
                    # honest and actionable (voice-setup), and the
                    # engine's own available() agrees (no divergence).
                    d.importable = True   # the probe DID import vosk
                    d.initialization = "MODEL MISSING"
                    d.state = ProviderState.INSTALLED
                    d.model_available = False   # R3c: the exact gap
                    d.reason = str(exc) + " — " + getattr(exc, "fix", "")
            out[name] = d
        self._diagnoses = out
        return out

    def available(self) -> List[str]:
        """Engine names that can actually transcribe on THIS machine."""
        d = self.discover()
        return [n for n in DEFAULT_PRIORITY if d[n].state in _USABLE_STATES]

    # -- R3c: the end-to-end offline-path proof --------------------------------

    def verify_offline_path(self, name: str) -> tuple:
        """Prove the OFFLINE path for one engine END-TO-END.

        Distinguishes the three capability levels for ``name``:
        engine-installed (import gate) vs model-available (probe) vs
        offline-path-WORKS (a runtime provider constructs and its own
        ``available()`` is True — the only level that may be called
        ready).  The verdict is recorded on the diagnosis row
        (``offline_path_ok``) and returned as ``(ok, reason)``.
        Never raises.
        """
        d = self.discover()
        row = d.get(name)
        if row is None:
            return False, f"unknown engine '{name}'"
        if row.state == ProviderState.DISABLED:
            row.offline_path_ok = False
            return False, row.reason or "disabled by configuration"
        provider = self._ctor_ok(name)
        if provider is None:
            row.offline_path_ok = False
            reason = row.reason or \
                "runtime provider construction/availability failed"
            if row.state in _USABLE_STATES:
                # the row claimed readiness the runtime disproves —
                # correct it honestly, never leave the over-claim
                row.state = ProviderState.FAILED
                row.initialization = "FAIL"
                row.reason = reason
            return False, reason
        row.offline_path_ok = True
        return True, ""

    # -- deterministic selection ----------------------------------------------

    def _ctor_ok(self, name: str) -> Optional[OfflineSpeechProvider]:
        """Build the runtime provider via voice_stack's table; ``None``
        when the constructor or ``available()`` honestly fails."""
        ctor = _vs._engine_ctor(name)
        if ctor is None:
            return None
        try:
            provider = ctor()
        except Exception:
            return None
        try:
            if provider is not None and provider.available():
                return provider
        except Exception:
            return None
        return None

    def select(self, prefer: Optional[str] = None,
               force: bool = False) -> Optional[OfflineSpeechProvider]:
        """Deterministically pick and construct the ACTIVE provider.

        Priority (mission §2): explicit configuration > previously
        selected working provider > lightweight (pocketsphinx) >
        higher-quality (vosk, whisper) > ``None`` (simulation only when
        nothing works).  Every candidate must still prove itself: the
        importability gate AND a constructor whose ``available()`` is
        True.  Result is cached per process; ``force=True`` re-selects.
        """
        if self._selected_provider is not None and not force and not prefer:
            return self._selected_provider
        d = self.discover(force=force)

        want = explicit_provider(prefer)
        if want.lower() in ("none", "off", "disabled"):
            return None  # user said none — honoured, never overridden

        candidates: List[str] = []
        if want:
            match = next((n for n in DEFAULT_PRIORITY if n == want.lower()),
                         None)
            if match is None:
                # unknown engine name — keep auto selection, report it
                self._last_skip_reason = \
                    f"explicit provider '{want}' is not a known engine"
            else:
                candidates.append(match)
        if not candidates:
            prev = _load_persisted()
            if prev:
                candidates.append(prev)
        # auto candidates always follow the explicit one: if the user's
        # choice cannot initialize, selection falls through honestly
        # (deterministic order) instead of returning nothing at all
        candidates.extend(
            n for n in DEFAULT_PRIORITY
            if d[n].state in _USABLE_STATES and n not in candidates)

        self._last_skip_reason = ""
        for name in candidates:
            st = d.get(name, ProviderDiagnosis(name=name))
            if st.state == ProviderState.DISABLED:
                continue
            if st.state not in _USABLE_STATES and name != (want or "").lower():
                continue  # e.g. explicit prefer of a FAILED engine
            provider = self._ctor_ok(name)
            if provider is None:
                # ctor disagrees with the probe — record honestly
                if st.state in _USABLE_STATES:
                    st.state = ProviderState.FAILED
                    st.initialization = "FAIL"
                    st.reason = st.reason or \
                        "runtime constructor failed after successful probe"
                continue
            self._selected_name = name
            self._selected_provider = provider
            _persist(name)
            return provider
        return None

    def active(self) -> Optional[str]:
        """Name of the ACTIVE provider (selects when needed), or None."""
        if self._selected_provider is None:
            self.select()
        return self._selected_name

    def active_report(self) -> Optional[ProviderDiagnosis]:
        """The diagnosis row of the ACTIVE provider, with its role set."""
        d = self.discover()
        name = self.active()
        if name is None:
            return None
        row = d.get(name)
        if row is not None:
            row.role = "ACTIVE"
            row.state = ProviderState.ACTIVE
        return row

    def diagnose(self, force: bool = False) -> Dict[str, Any]:
        """Everything voice-status --verbose and doctor need — ONE call."""
        d = self.discover(force=force)
        active = self.active()
        # R3c: prove (or honestly disprove) the end-to-end offline path
        # for every engine that CLAIMS usable readiness
        for name, row in d.items():
            if row.state in _USABLE_STATES:
                try:
                    self.verify_offline_path(name)
                except Exception:
                    row.offline_path_ok = False
        rows: Dict[str, ProviderDiagnosis] = {}
        for name, row in d.items():
            r = ProviderDiagnosis(**row.to_dict())
            if name == active:
                r.state = ProviderState.ACTIVE
                r.role = "ACTIVE"
            elif r.role != "DISABLED":
                r.role = "FALLBACK" if r.state in _USABLE_STATES \
                    else "OPTIONAL"
            rows[name] = r
        mic = detect_microphone()
        return {
            "providers": {k: v.to_dict() for k, v in rows.items()},
            "active": active,
            "usable": [n for n in DEFAULT_PRIORITY
                       if rows[n].state in _USABLE_STATES],
            "microphone": mic,
            "offline": True,
            "audio_storage": "NONE",
            "network_required": False,
        }

    # -- test/ops helpers -------------------------------------------------------

    def reset(self) -> None:
        """Forget the per-process selection (tests; never persistent)."""
        self._selected_name = None
        self._selected_provider = None


#: THE single registry instance every subsystem shares
registry = ASRProviderRegistry()


def active_asr_provider() -> str:
    """The ACTIVE provider name, or the built-in grammar label.

    This is THE function doctor reports from — the same registry the
    transcribe runtime selects through, so the invariant
    ``doctor.active_provider == transcribe.selected_provider`` holds by
    construction (mission §16).
    """
    try:
        return registry.active() or _vs.BUILT_IN_ACTIVE_LABEL
    except Exception:
        return _vs.BUILT_IN_ACTIVE_LABEL


def verify_offline_path(name: str) -> tuple:
    """Singleton convenience for :meth:`ASRProviderRegistry.
    verify_offline_path` — proves the engine's end-to-end offline path
    (engine-installed vs model-available vs offline-path-works)."""
    return registry.verify_offline_path(name)


def offline_capability_report() -> Dict[str, Dict[str, Any]]:
    """The R3c three-way capability report for every known engine.

    ``{name: {engine_installed, model_available, offline_path_ok,
    state, reason}}`` — exactly the distinction doctor / voice-status /
    the banner helpers need to never claim readiness that is not there.
    """
    out: Dict[str, Dict[str, Any]] = {}
    try:
        d = registry.discover()
    except Exception:
        return out
    for name, row in d.items():
        try:
            if row.state in _USABLE_STATES:
                registry.verify_offline_path(name)
        except Exception:
            row.offline_path_ok = False
        out[name] = {
            "engine_installed": bool(row.engine_installed),
            "model_available": row.model_available,
            "offline_path_ok": row.offline_path_ok,
            "state": row.state,
            "reason": row.reason,
        }
    return out


# ---------------------------------------------------------------------------
# unified microphone detection (sounddevice-first; PyAudio optional)
# ---------------------------------------------------------------------------

def detect_microphone() -> Dict[str, Any]:
    """Honest microphone snapshot — ONE implementation for everything.

    Prefers sounddevice/PortAudio (mission §4: the lightest existing
    stack; PyAudio is NOT a hard requirement).  Falls back to the
    SpeechRecognition/PyAudio enumeration when sounddevice cannot probe.
    Never raises.
    """
    out: Dict[str, Any] = {
        "available": False,
        "backend": "",
        "device_count": 0,
        "devices": [],
        "default_input": None,
        "error": "",
    }
    try:
        import sounddevice as sd
    except Exception as exc:
        out["error"] = f"sounddevice unavailable: {exc}"
        devs, backend = _pyaudio_devices()
        out["backend"] = backend
        out["devices"] = devs
        out["device_count"] = len(devs)
        out["available"] = bool(devs)
        return out
    try:
        raw = list(sd.query_devices())
    except Exception as exc:
        out["error"] = f"PortAudio query failed: {exc}"
        return out
    devices: List[Dict[str, Any]] = []
    try:
        default_in = sd.default.device[0] \
            if isinstance(sd.default.device, (tuple, list)) else None
    except Exception:
        default_in = None
    for i, dev in enumerate(raw):
        try:
            if int(dev.get("max_input_channels", 0)) <= 0:
                continue
            devices.append({
                "index": i,
                "name": str(dev.get("name", f"device {i}")),
                "channels": int(dev.get("max_input_channels", 0)),
                "sample_rate": int(float(dev.get("default_samplerate", 0))),
            })
        except Exception:
            continue
    out["backend"] = "sounddevice/PortAudio"
    out["devices"] = devices
    out["device_count"] = len(devices)
    out["available"] = bool(devices)
    out["default_input"] = default_in
    return out


def _pyaudio_devices() -> tuple:
    """Legacy fallback enumeration via SpeechRecognition/PyAudio."""
    try:
        import speech_recognition as sr
        names = sr.Microphone.list_microphone_names()
        return [{"index": i, "name": str(n), "channels": 1,
                 "sample_rate": 16000} for i, n in enumerate(names)], \
            "pyaudio/SpeechRecognition"
    except Exception:
        return [], ""
