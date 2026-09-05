"""Tests for airmouse.voice_stack — honest detection, panel, provider pick.

Every flag flip is driven by monkeypatching ``voice_stack.find_spec``
and/or the engine constructors; nothing here installs or downloads
anything and no network is touched.
"""

import io

import pytest

import airmouse.voice_stack as vs

EXPECTED_KEYS = {"built_in_grammar", "speech_recognition", "pyaudio",
                 "pocketsphinx", "vosk", "whisper", "microphone", "active"}


class _FakeEngine:
    """A provider double that is exactly as available as we say."""

    def __init__(self, name: str, ok: bool = True) -> None:
        self.name = name
        self._ok = ok

    def available(self) -> bool:
        return self._ok

    def transcribe(self, audio):
        from airmouse.offline_voice import Transcript
        return Transcript(provider=self.name)


def _fake_find_spec(allowed):
    allowed = set(allowed)

    def _fs(name, *a, **k):
        return object() if name in allowed else None
    return _fs


# ---------------------------------------------------------------------------
# detect_voice_stack
# ---------------------------------------------------------------------------

def test_detect_keys_and_built_in_always_true():
    d = vs.detect_voice_stack()
    assert set(d) == EXPECTED_KEYS
    assert d["built_in_grammar"] is True
    assert isinstance(d["active"], str) and d["active"]
    assert d["microphone"] is False  # headless sandbox: no mic hardware


def test_detect_nothing_installed_is_honest(monkeypatch):
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec([]))
    d = vs.detect_voice_stack()
    assert d["built_in_grammar"] is True          # the grammar always ships
    assert d["vosk"] is False and d["whisper"] is False
    assert d["pocketsphinx"] is False
    assert d["active"] == "Built-in command recognition"


def test_detect_vosk_flag_flips_with_find_spec(monkeypatch):
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec({"vosk"}))
    d = vs.detect_voice_stack()
    assert d["vosk"] is True
    assert d["whisper"] is False and d["pocketsphinx"] is False


def test_detect_whisper_flag_includes_faster_whisper(monkeypatch):
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec({"faster_whisper"}))
    assert vs.detect_voice_stack()["whisper"] is True
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec({"whisper"}))
    assert vs.detect_voice_stack()["whisper"] is True


def test_detect_active_reports_working_engine(monkeypatch):
    monkeypatch.setattr(vs, "find_spec",
                        _fake_find_spec({"vosk", "whisper", "pocketsphinx"}))
    monkeypatch.setattr(vs, "VoskProvider",
                        lambda: _FakeEngine("vosk"))
    monkeypatch.setattr(vs, "WhisperProvider",
                        lambda: _FakeEngine("whisper"))
    monkeypatch.setattr(vs, "PocketSphinxProvider",
                        lambda: _FakeEngine("pocketsphinx"))
    assert vs.detect_voice_stack()["active"] == "vosk"  # default: vosk first


# ---------------------------------------------------------------------------
# status_panel
# ---------------------------------------------------------------------------

def _active_lines(panel: str):
    return [ln for ln in panel.splitlines() if "Active:" in ln]


def test_panel_contract_in_sandbox():
    panel = vs.status_panel()
    assert panel.startswith("Voice Provider")
    assert "──────" in panel
    assert "✓ Built-in command recognition" in panel
    assert len(_active_lines(panel)) == 1
    assert "Active: Built-in command recognition" in panel
    # honesty: nothing installed in the sandbox
    assert "Full speech recognition is not installed." in panel
    assert "○ Local ASR available" in panel
    assert "○ Whisper available" in panel
    assert "○ Vosk available" in panel
    assert "○ Microphone detected" in panel


def test_panel_honest_when_nothing_installed(monkeypatch):
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec([]))
    panel = vs.status_panel()
    assert "Full speech recognition is not installed." in panel
    assert "Active: Built-in command recognition" in panel
    assert "deterministic grammar" in panel  # grammar ≠ ASR, stated plainly


def test_panel_with_engines_available(monkeypatch):
    monkeypatch.setattr(vs, "find_spec",
                        _fake_find_spec({"vosk", "whisper"}))
    monkeypatch.setattr(vs, "VoskProvider", lambda: _FakeEngine("vosk"))
    monkeypatch.setattr(vs, "WhisperProvider", lambda: _FakeEngine("whisper"))
    panel = vs.status_panel()
    assert "✓ Local ASR available" in panel
    assert "✓ Whisper available" in panel
    assert "✓ Vosk available" in panel
    assert "Active: vosk" in panel                    # exactly one Active line
    assert len(_active_lines(panel)) == 1
    assert "Full speech recognition is not installed." not in panel


def test_panel_single_active_line_even_with_engines(monkeypatch):
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec({"pocketsphinx"}))
    monkeypatch.setattr(vs, "PocketSphinxProvider",
                        lambda: _FakeEngine("pocketsphinx"))
    assert len(_active_lines(vs.status_panel())) == 1


# ---------------------------------------------------------------------------
# pick_provider
# ---------------------------------------------------------------------------

def test_pick_provider_none_when_nothing_installed(monkeypatch):
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec([]))
    assert vs.pick_provider() is None
    assert vs.pick_provider(prefer="vosk") is None


def test_pick_provider_default_order_vosk_first(monkeypatch):
    monkeypatch.setattr(vs, "find_spec",
                        _fake_find_spec({"vosk", "whisper", "pocketsphinx"}))
    monkeypatch.setattr(vs, "VoskProvider", lambda: _FakeEngine("vosk"))
    monkeypatch.setattr(vs, "WhisperProvider", lambda: _FakeEngine("whisper"))
    monkeypatch.setattr(vs, "PocketSphinxProvider",
                        lambda: _FakeEngine("pocketsphinx"))
    assert vs.pick_provider().name == "vosk"


def test_pick_provider_prefer_overrides_order(monkeypatch):
    monkeypatch.setattr(vs, "find_spec",
                        _fake_find_spec({"vosk", "whisper", "pocketsphinx"}))
    monkeypatch.setattr(vs, "VoskProvider", lambda: _FakeEngine("vosk"))
    monkeypatch.setattr(vs, "WhisperProvider", lambda: _FakeEngine("whisper"))
    monkeypatch.setattr(vs, "PocketSphinxProvider",
                        lambda: _FakeEngine("pocketsphinx"))
    assert vs.pick_provider(prefer="whisper").name == "whisper"
    assert vs.pick_provider(prefer="POCKETSPHINX").name == "pocketsphinx"


def test_pick_provider_skips_raising_constructors(monkeypatch):
    monkeypatch.setattr(vs, "find_spec",
                        _fake_find_spec({"vosk", "whisper"}))

    def _boom():
        raise RuntimeError("engine packaging is broken")
    monkeypatch.setattr(vs, "VoskProvider", _boom)
    monkeypatch.setattr(vs, "WhisperProvider", lambda: _FakeEngine("whisper"))
    assert vs.pick_provider().name == "whisper"


def test_pick_provider_skips_unavailable_instances(monkeypatch):
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec({"vosk"}))
    monkeypatch.setattr(vs, "VoskProvider",
                        lambda: _FakeEngine("vosk", ok=False))
    assert vs.pick_provider() is None


def test_pick_provider_gated_on_real_importability(monkeypatch):
    # flag claims vosk exists, but the real constructor cannot import it
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec({"vosk"}))
    assert vs.pick_provider() is None


# ---------------------------------------------------------------------------
# install_guidance / offer_voice_pack
# ---------------------------------------------------------------------------

def test_install_guidance_has_exact_commands_and_honesty():
    g = vs.install_guidance()
    assert "pip install vosk" in g
    assert "pip install SpeechRecognition pocketsphinx" in g
    assert "pip install faster-whisper" in g
    assert "NEVER installs" in g or "never installs" in g
    assert "no network" in g.lower() or "no network calls" in g.lower()
    assert "deterministic grammar" in g      # honesty note
    assert "not simulated ASR" in g or "not free-form" in g


def test_offer_voice_pack_yes_prints_guidance_installs_nothing():
    buf = io.StringIO()
    assert vs.offer_voice_pack(out=buf, input_fn=lambda p: "Y") is True
    out = buf.getvalue()
    assert "Install Local Voice Pack? [Y] Yes [N] Not now" in out
    assert "pip install vosk" in out
    assert "NEVER installs" in out or "never installs" in out


def test_offer_voice_pack_no_returns_false():
    buf = io.StringIO()
    assert vs.offer_voice_pack(out=buf, input_fn=lambda p: "N") is False


def test_offer_voice_pack_eof_returns_false():
    def _eof(_p):
        raise EOFError
    buf = io.StringIO()
    assert vs.offer_voice_pack(out=buf, input_fn=_eof) is False


def test_offer_voice_pack_junk_answer_is_decline():
    buf = io.StringIO()
    assert vs.offer_voice_pack(out=buf, input_fn=lambda p: "maybe later") \
        is False


def test_offer_voice_pack_never_raises_on_broken_input():
    def _broken(_p):
        raise RuntimeError("stdin exploded")
    buf = io.StringIO()
    assert vs.offer_voice_pack(out=buf, input_fn=_broken) is False


def test_offer_voice_pack_skipped_when_engine_already_available(monkeypatch):
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec({"vosk"}))
    monkeypatch.setattr(vs, "VoskProvider", lambda: _FakeEngine("vosk"))
    calls = []

    def _input(_p):
        calls.append(_p)
        return "Y"
    buf = io.StringIO()
    assert vs.offer_voice_pack(out=buf, input_fn=_input) is False
    assert calls == []                                  # never even asked
    assert "already available" in buf.getvalue()
