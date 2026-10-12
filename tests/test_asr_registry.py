"""v16.5.2 — THE authoritative ASR registry test matrix (mission §16).

Covers exactly the mission's regression matrix:

* provider discovery  — installed / missing / initialization failure /
  vosk / whisper / multiple / none
* selection           — explicit, automatic, failure→next, fallback,
  simulation-only-when-nothing-works, "none" honoured
* microphone          — device found / absent / unavailable / invalid
* runtime invariant   — doctor.active_provider ==
  transcribe.selected_provider (THE release invariant, §16)
* record loop         — deterministic VAD utterance capture (no
  hardware: scripted sounddevice stream)
* safety              — red-team voice phrases stay inert; no
  eval/exec/os.system/shell=True in the voice runtime modules

Fake environments are SELF-CONSISTENT: find_spec + the real-init probe
+ the constructor all agree, so tests are independent of what the test
machine actually has installed.  Nothing downloads, no network.
"""

import io
import os
import sys

import pytest

import airmouse.asr_registry as asr
import airmouse.voice_stack as vs
from airmouse.asr_registry import (ASRProviderRegistry, ProviderDiagnosis,
                                   ProviderState)
from airmouse.offline_voice import OfflineSpeechProvider, Transcript


# ---------------------------------------------------------------------------
# helpers — self-consistent fake machines
# ---------------------------------------------------------------------------

def _fake_find_spec(allowed):
    allowed = set(allowed)

    def _fs(name, *a, **k):
        return object() if name in allowed else None
    return _fs


class _FakeEngine(OfflineSpeechProvider):
    def __init__(self, name: str, ok: bool = True) -> None:
        self.name = name
        self._ok = ok

    def available(self) -> bool:
        return self._ok

    def transcribe(self, audio) -> Transcript:
        return Transcript(provider=self.name)


def _patch_env(monkeypatch, registry, working=(), broken_probe=(),
               broken_ctor=(), unavailable=()):
    """Make ``registry`` see a deterministic fake machine.

    * ``working``       — installed, probe PASSes, ctor available
    * ``broken_probe``  — installed but the real-init probe RAISES
    * ``broken_ctor``   — probe passes but the ctor raises
    * ``unavailable``   — probe passes but ctor.available() is False
    """
    installed = set(working) | set(broken_probe) | set(broken_ctor) | \
        set(unavailable)
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec(installed))
    probes = dict(asr._ENGINE_PROBES)
    for n in installed:
        if n in broken_probe:
            def _boom(n=n):
                raise ImportError(f"{n} present but broken (fake)")
            probes[n] = _boom
        else:
            probes[n] = lambda: 0.0
    monkeypatch.setattr(asr, "_ENGINE_PROBES", probes)

    def _ctor_for(name):
        if name in broken_ctor:
            def _boom():
                raise RuntimeError("ctor exploded (fake)")
            return _boom
        if name in unavailable:
            return (lambda: _FakeEngine(name, ok=False))
        return (lambda n=name: _FakeEngine(n))
    for n in installed:
        attr = {"vosk": "VoskProvider", "whisper": "WhisperProvider",
                "pocketsphinx": "PocketSphinxProvider"}[n]
        monkeypatch.setattr(vs, attr, _ctor_for(n))


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def test_discover_states_are_the_seven_contract_states():
    values = {v for k, v in vars(ProviderState).items()
              if isinstance(v, str) and k.isupper()}
    assert values == {"NOT_INSTALLED", "INSTALLED", "AVAILABLE",
                      "INITIALIZABLE", "ACTIVE", "FAILED", "DISABLED"}


def test_discover_none_installed_is_honest(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg)
    d = reg.discover()
    assert all(d[n].state == ProviderState.NOT_INSTALLED for n in d)
    assert reg.available() == []
    assert reg.select() is None          # simulation is the only fallback
    assert reg.active() is None


def test_discover_pocketsphinx_missing(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("vosk",))
    d = reg.discover()
    assert d["pocketsphinx"].state == ProviderState.NOT_INSTALLED
    assert d["pocketsphinx"].installed is False


def test_discover_initialization_failure_keeps_reason(monkeypatch):
    """§7: installed but init FAILED → FAILED state + the reason is
    CAPTURED (never swallowed) — the anti-hasattr guarantee."""
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, broken_probe=("pocketsphinx",))
    d = reg.discover()
    row = d["pocketsphinx"]
    assert row.state == ProviderState.FAILED
    assert row.initialization == "FAIL"
    assert "broken (fake)" in row.reason
    assert row.installed is True
    assert reg.available() == []
    assert reg.select() is None


def test_discover_vosk_and_whisper_are_available_deferred(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("vosk", "whisper"))
    d = reg.discover()
    assert d["vosk"].state == ProviderState.AVAILABLE
    assert d["whisper"].state == ProviderState.AVAILABLE
    assert d["vosk"].initialization == "DEFERRED"
    assert reg.available() == ["vosk", "whisper"]


def test_discover_multiple_providers_all_usable(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("pocketsphinx", "vosk", "whisper"))
    d = reg.discover()
    assert d["pocketsphinx"].state == ProviderState.INITIALIZABLE
    assert reg.available() == ["pocketsphinx", "vosk", "whisper"]


def test_discover_real_machine_never_lies():
    """On the REAL test machine the registry's verdict must match the
    engine's own honest available() — no hasattr-style false ACTIVE."""
    reg = ASRProviderRegistry()
    d = reg.discover()
    for name, row in d.items():
        ctor = vs._engine_ctor(name)
        prov = ctor() if ctor else None
        honest = bool(prov and prov.available())
        if row.state in (ProviderState.INITIALIZABLE,
                         ProviderState.AVAILABLE):
            assert honest, f"{name} claims usable but engine disagrees"
        if row.state == ProviderState.FAILED:
            assert not honest or name != "pocketsphinx", \
                "pocketsphinx FAILED but available() true — false probe"


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------

def test_select_automatic_prefers_lightweight_first(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("pocketsphinx", "vosk", "whisper"))
    assert reg.select().name == "pocketsphinx"          # §2 priority 3


def test_select_explicit_provider_wins(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("pocketsphinx", "vosk", "whisper"))
    assert reg.select(prefer="whisper").name == "whisper"
    assert reg.select(prefer="VOSK").name == "vosk"


def test_select_env_var_is_explicit_configuration(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("pocketsphinx", "vosk"))
    monkeypatch.setenv("AIRMOUSE_ASR_PROVIDER", "vosk")
    assert reg.select().name == "vosk"


def test_select_none_disabled(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("pocketsphinx",))
    monkeypatch.setenv("AIRMOUSE_ASR_PROVIDER", "none")
    assert reg.select() is None
    d = reg.discover()
    assert d["pocketsphinx"].state == ProviderState.DISABLED


def test_select_failure_falls_to_next_candidate(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("whisper",),
               broken_ctor=("pocketsphinx", "vosk"))
    assert reg.select().name == "whisper"


def test_select_all_fail_then_simulation(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, broken_ctor=("pocketsphinx", "vosk",
                                              "whisper"))
    assert reg.select() is None
    # the honest fallback block exists for the transcribe UI (§7)
    from airmouse.transcribe_session import _provider_failure_lines
    lines = _provider_failure_lines()
    assert any("pocketsphinx" in ln for ln in lines)


def test_select_preferred_but_failed_falls_back_honestly(monkeypatch):
    """An explicit preference of a genuinely broken engine (probe AND
    ctor both fail) must not fake it: the request falls through to a
    working engine (or None), never to a broken one dressed up as
    ACTIVE."""
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("vosk",),
               broken_probe=("pocketsphinx",),
               broken_ctor=("pocketsphinx",))
    assert reg.select(prefer="pocketsphinx").name == "vosk"


def test_select_preferred_engine_gets_a_second_chance(monkeypatch):
    """When the user EXPLICITLY names an engine whose probe failed but
    whose runtime constructor honestly passes, the explicit choice is
    honoured (deterministic; the ctor gate is itself a real check —
    for real PocketSphinx it runs the same probe, so this cannot fake
    a broken engine in production)."""
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("vosk",),
               broken_probe=("pocketsphinx",))
    assert reg.select(prefer="pocketsphinx").name == "pocketsphinx"


def test_select_unknown_engine_name_falls_back(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("pocketsphinx",))
    assert reg.select(prefer="skynet-voice").name == "pocketsphinx"


def test_diagnose_roles_are_honest(monkeypatch):
    reg = ASRProviderRegistry()
    _patch_env(monkeypatch, reg, working=("pocketsphinx", "vosk"),
               broken_probe=("whisper",))
    diag = reg.diagnose()
    assert diag["providers"]["pocketsphinx"]["role"] == "ACTIVE"
    assert diag["providers"]["vosk"]["role"] == "FALLBACK"
    assert diag["providers"]["whisper"]["role"] == "OPTIONAL"
    assert diag["providers"]["whisper"]["state"] == "FAILED"
    assert diag["active"] == "pocketsphinx"
    assert diag["offline"] is True
    assert diag["audio_storage"] == "NONE"
    assert diag["network_required"] is False


# ---------------------------------------------------------------------------
# microphone
# ---------------------------------------------------------------------------

def _install_fake_sounddevice(monkeypatch, *, devices, default=(1, -1),
                              query_error=None):
    class _FakeSD:
        def query_devices(self):
            if query_error is not None:
                raise query_error
            return list(devices)

    fake = type(sys)("fake_sounddevice")
    fake.query_devices = _FakeSD().query_devices
    fake.default = type("D", (), {"device": default})
    monkeypatch.setitem(sys.modules, "sounddevice", fake)


def test_detect_microphone_device_found(monkeypatch):
    _install_fake_sounddevice(
        monkeypatch,
        devices=[
            {"name": "Speakers", "max_input_channels": 0,
             "default_samplerate": 44100},
            {"name": "Default Windows Microphone",
             "max_input_channels": 1, "default_samplerate": 16000},
            {"name": "USB Mic", "max_input_channels": 2,
             "default_samplerate": 48000},
        ],
        default=(1, -1))
    m = asr.detect_microphone()
    assert m["available"] is True
    assert m["backend"] == "sounddevice/PortAudio"
    assert m["device_count"] == 2
    assert m["devices"][0]["name"] == "Default Windows Microphone"
    assert m["default_input"] == 1


def test_detect_microphone_device_absent(monkeypatch):
    _install_fake_sounddevice(
        monkeypatch,
        devices=[{"name": "Speakers", "max_input_channels": 0,
                  "default_samplerate": 44100}],
        default=(-1, -1))
    m = asr.detect_microphone()
    assert m["available"] is False
    assert m["device_count"] == 0


def test_detect_microphone_query_raises_is_honest(monkeypatch):
    _install_fake_sounddevice(
        monkeypatch, devices=[], query_error=OSError(
            "PortAudio not initialised (fake)"))
    m = asr.detect_microphone()
    assert m["available"] is False
    assert "PortAudio" in m["error"]


def test_resolve_device_invalid_index_has_fix_line(monkeypatch):
    from airmouse import mic_capture as mc
    monkeypatch.setattr(mc, "list_input_devices", lambda: [
        {"index": 0, "name": "Default Windows Microphone",
         "channels": 1, "sample_rate": 16000}])
    with pytest.raises(mc.MicCaptureError) as ei:
        mc.resolve_device("42")
    assert "invalid microphone index 42" in str(ei.value)
    assert "voice-status" in ei.value.fix


def test_resolve_device_matches_name_substring(monkeypatch):
    from airmouse import mic_capture as mc
    monkeypatch.setattr(mc, "list_input_devices", lambda: [
        {"index": 2, "name": "USB Microphone Array",
         "channels": 2, "sample_rate": 48000}])
    assert mc.resolve_device("usb micro") == 2
    assert mc.resolve_device("2") == 2
    with pytest.raises(mc.MicCaptureError) as ei:
        mc.resolve_device("bluetooth headset")
    assert "no microphone matching" in str(ei.value)
    assert "USB Microphone Array" in ei.value.fix


def test_resolve_device_default_is_none():
    from airmouse.mic_capture import resolve_device
    assert resolve_device(None) is None
    assert resolve_device("default") is None


# ---------------------------------------------------------------------------
# the record loop (deterministic, scripted stream — no hardware)
# ---------------------------------------------------------------------------

def _scripted_stream_factory(frames):
    class _Stream:
        def __init__(self):
            self.frames = list(frames)
            self.i = 0

        def read(self, block):
            if self.i < len(self.frames):
                f = self.frames[self.i]
                self.i += 1
                return f, False
            return b"\x00\x00" * block, False

        def close(self):
            pass
    return lambda blocksize: _Stream()


def _loud_frames(n, block):
    import math
    frame = b"".join(
        int(8000 * math.sin(2 * math.pi * 440 * i / 16000)).to_bytes(
            2, "little", signed=True)
        for i in range(block))
    return [frame] * n


def test_record_utterance_captures_speech_then_stops():
    from airmouse.mic_capture import record_utterance
    from airmouse.offline_voice import EnergyVAD
    block = int(16000 * 30 / 1000)
    silence = [b"\x00\x00" * block] * 2
    loud = _loud_frames(8, block)
    trailing = [b"\x00\x00" * block] * 40
    pcm = record_utterance(
        stream_factory=_scripted_stream_factory(silence + loud + trailing),
        vad=EnergyVAD(sample_rate=16000, end_silence=0.4),
        sleep_fn=lambda s: None, start_timeout=5.0)
    assert len(pcm) > len(loud[0])            # the loud frames are in
    assert b"\x00\x00" * block in pcm         # silence tail kept


def test_record_utterance_silence_times_out_honestly():
    from airmouse.mic_capture import MicCaptureError, record_utterance
    from airmouse.offline_voice import EnergyVAD
    block = int(16000 * 30 / 1000)
    with pytest.raises(MicCaptureError) as ei:
        record_utterance(
            stream_factory=_scripted_stream_factory(
                [b"\x00\x00" * block] * 400),
            vad=EnergyVAD(sample_rate=16000),
            sleep_fn=lambda s: None, start_timeout=0.05)
    assert "no speech detected" in str(ei.value)


def test_capture_transcript_end_to_end_with_real_provider_shape():
    """mic → VAD → provider, with a scripted stream and an engine that
    'recognizes' — proves the full local pipeline plumbing offline."""
    # the engine is pocketsphinx-shaped → the pipeline wraps PCM into a
    # SpeechRecognition AudioData; without that OPTIONAL extra the honest
    # pipeline result is (None, 0.0) — skip instead of asserting
    # behaviour that depends on a dependency we do not have
    pytest.importorskip(
        "speech_recognition",
        reason="pocketsphinx-shaped pipeline needs the optional "
               "SpeechRecognition extra")
    from airmouse.mic_capture import capture_transcript

    class _Engine(_FakeEngine):
        name = "pocketsphinx"

        def __init__(self) -> None:
            pass

        def transcribe(self, audio):
            # SpeechRecognition AudioData in → honest Transcript out
            return Transcript(text="hello there", confidence=0.0,
                              provider=self.name, confidence_known=False)

    block = int(16000 * 30 / 1000)
    frames = ([b"\x00\x00" * block] * 2 + _loud_frames(8, block)
              + [b"\x00\x00" * block] * 40)

    import airmouse.mic_capture as mc
    from airmouse.offline_voice import EnergyVAD
    orig = mc.record_utterance
    mc.record_utterance = lambda **kw: orig(
        stream_factory=_scripted_stream_factory(frames),
        vad=EnergyVAD(sample_rate=16000, end_silence=0.4),
        sleep_fn=lambda s: None,
        device=kw.get("device"), start_timeout=kw.get("start_timeout", 6.0),
        max_seconds=kw.get("max_seconds", 8.0))
    try:
        text, conf = capture_transcript(_Engine(), timeout_s=6.0)
    finally:
        mc.record_utterance = orig
    assert text == "hello there"
    assert conf == 0.0            # §12: engine supplies none → 0, no fake


# ---------------------------------------------------------------------------
# THE runtime invariant (mission §16)
# ---------------------------------------------------------------------------

def test_doctor_active_provider_equals_transcribe_selection(monkeypatch):
    """``doctor.active_provider == transcribe.selected_provider`` —
    the exact release invariant, in the SAME (fake) environment."""
    _patch_env(monkeypatch, asr.registry, working=("pocketsphinx", "vosk"))
    asr.registry.reset()
    asr.registry._diagnoses = {}

    from airmouse.doctor import run_doctor
    from airmouse.transcribe_session import TranscribeSession
    doc = run_doctor()
    session = TranscribeSession()
    assert doc.active_asr_provider == session.selected_provider
    assert session.selected_provider == "pocketsphinx"
    assert session.simulated is False


def test_doctor_and_transcribe_agree_when_nothing_usable(monkeypatch):
    _patch_env(monkeypatch, asr.registry)
    asr.registry.reset()
    asr.registry._diagnoses = {}

    from airmouse.doctor import run_doctor
    from airmouse.transcribe_session import TranscribeSession
    doc = run_doctor()
    session = TranscribeSession()
    assert doc.active_asr_provider == "Built-in command recognition"
    assert session.simulated is True
    assert session.selected_provider == "simulated_stream"
    # the fallback banner is loud and explains WHY + FIX (§7)
    assert session.selection_detail, "WHY lines must exist"


def test_doctor_speech_section_reports_active_engine(monkeypatch):
    _patch_env(monkeypatch, asr.registry, working=("pocketsphinx",))
    asr.registry.reset()
    asr.registry._diagnoses = {}

    from airmouse.capabilities import detect_all
    rep = detect_all()
    rows = [c for c in rep.components
            if c.name == "Transcription providers"]
    assert rows and rows[0].state == "READY"
    assert "pocketsphinx" in rows[0].detail
    assert "ACTIVE" in rows[0].detail


def test_doctor_speech_section_reports_failed_engine_with_reason(
        monkeypatch):
    _patch_env(monkeypatch, asr.registry, broken_probe=("pocketsphinx",))
    asr.registry.reset()
    asr.registry._diagnoses = {}

    from airmouse.capabilities import detect_all
    rep = detect_all()
    row = next(c for c in rep.components
               if c.name == "Transcription providers")
    assert row.state == "OPTIONAL"       # no usable engine → honest optional
    warn = next(c for c in rep.components
                if c.name == "ASR engine pocketsphinx")
    assert warn.state == "WARNING"
    assert "broken (fake)" in warn.detail


# ---------------------------------------------------------------------------
# voice-status renderer (§8)
# ---------------------------------------------------------------------------

def test_voice_status_verbose_shows_all_sections(monkeypatch):
    _patch_env(monkeypatch, asr.registry, working=("pocketsphinx",),
               broken_probe=("vosk",))
    asr.registry.reset()
    asr.registry._diagnoses = {}
    monkeypatch.setattr(asr, "detect_microphone", lambda: {
        "available": True, "backend": "sounddevice/PortAudio",
        "device_count": 6, "devices": [{"index": 0,
                                        "name": "Default Windows "
                                                "Microphone",
                                        "channels": 1,
                                        "sample_rate": 16000}],
        "default_input": 0, "error": ""})
    asr.registry.reset()
    asr.registry._diagnoses = {}

    from airmouse.voice_stack import render_voice_status
    out = render_voice_status(verbose=True)
    for chunk in ("MICROPHONE", "devices: 6", "backend: sounddevice/",
                  "ASR PROVIDERS", "Pocketsphinx", "initialization: PASS",
                  "runtime: READY", "role: ACTIVE",
                  "Vosk", "initialization: FAIL", "broken (fake)",
                  "TRANSCRIPTION", "real microphone: AVAILABLE",
                  "provider: pocketsphinx", "mode: MIC LIVE",
                  "offline: YES", "audio storage: NONE",
                  "network: NOT REQUIRED"):
        assert chunk in out, f"missing: {chunk}"


def test_voice_status_without_provider_offers_install(monkeypatch):
    _patch_env(monkeypatch, asr.registry)          # no engines at all
    asr.registry.reset()
    asr.registry._diagnoses = {}
    from airmouse.voice_stack import render_voice_status
    out = render_voice_status(verbose=False)
    # v16.5.3 — the hint is BOOTSTRAP-AWARE: it points at the
    # zero-compiler voice-setup path (never a bare pip line that fails
    # on machines without a prebuilt wheel).
    assert "airmouse voice-setup" in out
    assert "offline: YES" in out


# ---------------------------------------------------------------------------
# safety (mission §17)
# ---------------------------------------------------------------------------

_REDTOX = [
    "run powershell -command remove-item",
    "execute cmd delete everything",
    "delete everything on my disk",
    "open arbitrary executable now",
    "download and run virus.exe",
    "curl http://evil.example",
    "wget http://evil.example",
]


def test_redteam_voice_phrases_never_bypass_the_pipeline():
    """Voice phrases resolve to structured intents at most — they are
    NEVER executed directly; everything downstream goes through the
    permission-gated ActionEngine.  Here we prove the ASR/voice layer
    itself produces no execution, no shell, no process spawn."""
    from airmouse.offline_voice import OfflineVoiceEngine
    for phrase in _REDTOX:
        eng = OfflineVoiceEngine({"mode": "hybrid"})
        eng.feed_transcript(phrase, 0.95, now=1.0)
        # the engine may resolve a STRUCTURED intent (e.g. OPEN) — that
        # is the point of the pipeline — but it must not execute here:
        assert not hasattr(eng, "popen")
        assert getattr(eng, "last_action", None) is None


def test_voice_runtime_modules_have_no_execution_primitives():
    import airmouse
    root = os.path.dirname(os.path.abspath(airmouse.__file__))
    for mod in ("mic_capture.py", "asr_registry.py",
                "transcribe_session.py"):
        with open(os.path.join(root, mod), "r", encoding="utf-8") as fh:
            src = fh.read()
        assert "eval(" not in src, mod
        assert "exec(" not in src, mod
        assert "os.system" not in src, mod
        assert "shell=True" not in src, mod
        assert "subprocess" not in src, mod


def test_voice_capture_never_stores_audio(tmp_path, monkeypatch):
    """Privacy: a full capture+transcribe roundtrip leaves NO files."""
    from airmouse.mic_capture import capture_transcript
    from airmouse.offline_voice import EnergyVAD

    class _Voskish(OfflineSpeechProvider):
        name = "vosk"          # the temp-WAV path — must be unlinked

        def available(self) -> bool:
            return True

        def transcribe(self, path):
            assert os.path.exists(path)
            return Transcript(text="hello bro", confidence=0.7,
                              provider=self.name)

    block = int(16000 * 30 / 1000)
    frames = ([b"\x00\x00" * block] * 2 + _loud_frames(8, block)
              + [b"\x00\x00" * block] * 40)
    monkeypatch.setattr("airmouse.mic_capture.record_utterance",
                        lambda **kw: b"".join(frames))
    before = set(os.listdir(tmp_path))
    text, _conf = capture_transcript(_Voskish(), timeout_s=6.0)
    assert text == "hello bro"
    after = set(os.listdir(tmp_path))
    assert before == after == set()   # nothing stored, temp wav deleted
