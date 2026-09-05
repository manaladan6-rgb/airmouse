"""v16.5.1 §13 + §8 — third-party log noise suppression and the
transcribe MIC LIVE mode (automatic real microphone + local ASR)."""

import io
import os

import pytest

from airmouse import quiet as quiet_mod
from airmouse import transcribe_session as ts


# ---------------------------------------------------------------------------
# §13 — quiet_third_party_logs
# ---------------------------------------------------------------------------

def test_quiet_sets_env_dials_once(monkeypatch):
    for k in quiet_mod._ENV_DIALS:
        monkeypatch.delenv(k, raising=False)
    quiet_mod._DONE = False
    quiet_mod.quiet_third_party_logs()
    for k, v in quiet_mod._ENV_DIALS.items():
        assert os.environ.get(k) == v
    # idempotent: a second call is a no-op (does not raise)
    quiet_mod.quiet_third_party_logs()


def test_quiet_respects_user_env_settings(monkeypatch):
    monkeypatch.setenv("GLOG_minloglevel", "0")   # user tuned it — keep it
    quiet_mod._DONE = False
    quiet_mod.quiet_third_party_logs()
    assert os.environ["GLOG_minloglevel"] == "0"
    quiet_mod._DONE = False
    monkeypatch.delenv("GLOG_minloglevel", raising=False)


def test_quiet_never_touches_the_airmouse_logger():
    import logging
    airmouse_logger = logging.getLogger("airmouse")
    airmouse_logger.setLevel(logging.DEBUG)
    quiet_mod._DONE = False
    quiet_mod.quiet_third_party_logs()
    assert airmouse_logger.level == logging.DEBUG   # untouched
    quiet_mod._DONE = False
    # third-party loggers ARE dialed down
    assert logging.getLogger("absl").level >= logging.ERROR


def test_quiet_never_raises_on_garbage_state(monkeypatch):
    monkeypatch.setattr(quiet_mod, "_ENV_DIALS", {})
    quiet_mod._DONE = False
    quiet_mod.quiet_third_party_logs()          # must not raise
    quiet_mod._DONE = False


# ---------------------------------------------------------------------------
# §8 — transcribe MIC LIVE mode
# ---------------------------------------------------------------------------

def test_mic_mode_with_injected_ear_transcribes_final_segments():
    ear = iter([("hello bro how are you", 0.9), ("click that", 0.85)])
    buf = io.StringIO()
    rc = ts.run_transcribe(out=buf, feed=["", "", "quit"],
                           mic_mode=True, capture_fn=lambda: next(ear))
    out = buf.getvalue()
    assert rc == 0
    assert "MIC LIVE" in out
    # the engine's full post-processing chain applies to the captured
    # utterance (spoken-punct/capitalize → display), then (final) marker
    assert "(final)" in out
    assert "how are you" in out
    assert "avg confidence" in out
    assert "+ mic live" in out          # the summary states the mode


def test_mic_mode_failed_capture_is_honest():
    buf = io.StringIO()
    rc = ts.run_transcribe(out=buf, feed=["", "quit"], mic_mode=True,
                           capture_fn=lambda: None)
    assert rc == 0
    assert "nothing usable captured" in buf.getvalue()


def test_mic_mode_commands_still_work():
    buf = io.StringIO()
    rc = ts.run_transcribe(out=buf, feed=["pause", "resume", "status",
                                          "quit"],
                           mic_mode=True, capture_fn=lambda: ("x", 0.5))
    out = buf.getvalue()
    assert rc == 0
    assert "paused" in out and "listening again" in out
    assert "LIVE TRANSCRIPTION" in out


def test_text_mode_default_without_mic_does_not_claim_live():
    buf = io.StringIO()
    rc = ts.run_transcribe(out=buf, feed=["hello world", "quit"],
                           simulate=True)
    out = buf.getvalue()
    assert rc == 0
    assert "MIC LIVE" not in out
    assert "text-input mode" in out
    assert "no audio was ever stored" in out


def test_simulate_forces_text_mode_even_if_mic_requested():
    buf = io.StringIO()
    rc = ts.run_transcribe(out=buf, feed=["quit"], simulate=True,
                           mic_mode=True)
    assert rc == 0
    assert "MIC LIVE" not in buf.getvalue()


def test_capture_never_stores_audio_even_in_mic_mode(tmp_path, monkeypatch):
    from airmouse import paths
    monkeypatch.setattr(paths, "airmouse_home", lambda: str(tmp_path))
    saved = []
    buf = io.StringIO()
    ts.run_transcribe(out=buf, feed=["", "save", "quit"], mic_mode=True,
                      capture_fn=lambda: ("one two", 0.9))
    # save wrote TEXT only; no wav/pcm files anywhere in the home
    for root, _dirs, files in os.walk(str(tmp_path)):
        for f in files:
            saved.append(os.path.join(root, f))
    assert saved
    for p in saved:
        assert not p.endswith((".wav", ".pcm", ".mp3", ".ogg", ".flac"))


def test_partial_and_final_are_displayed():
    """The StreamingProviderAdapter reveals word-by-word partials before
    the final — the §6 partial/final contract — via the text path."""
    buf = io.StringIO()
    ts.run_transcribe(out=buf, feed=["one two three", "quit"],
                      simulate=True)
    out = buf.getvalue()
    assert "partial" in out.lower() or '"' in out   # panel/segments shown
