"""v16.5 — TranscribeSession tests: live transcription pipeline honesty.

Covers mission §6: streaming segments with timestamp + confidence,
pause/resume/stop/clear/save, bounded local history, save-only-on-
explicit-command, privacy (text only, never audio), and honest
simulated-provider labeling.
"""

import io
import json
import os

import pytest

from airmouse import paths
from airmouse.transcribe_session import (TranscribeSession,
                                         render_transcript_panel,
                                         run_transcribe)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    return tmp_path


def _session():
    return TranscribeSession(simulate=True)


# ---------------------------------------------------------------------------
# segments, timestamps, confidence
# ---------------------------------------------------------------------------

def test_utterance_creates_segment_with_metadata(home):
    s = _session()
    s.utterance("Today we're going to", confidence=0.84)
    segs = s.segments()
    assert len(segs) == 1
    seg = segs[0]
    assert seg.text.lower() == "today we're going to"
    assert 0.0 <= seg.confidence <= 1.0
    assert seg.start_ts > 0 and seg.end_ts >= seg.start_ts
    assert s.state == "listening"


def test_partial_and_buffer_text(home):
    s = _session()
    s.utterance("hello")
    s.utterance("world")
    assert "hello" in s.buffer_text().lower()
    assert "world" in s.buffer_text().lower()


def test_pause_stops_accumulation_resume_restores(home):
    s = _session()
    s.utterance("one")
    assert s.pause() is True
    assert s.state == "paused"
    s.utterance("ignored")
    assert len(s.segments()) == 1
    assert s.resume() is True
    assert s.state == "listening"
    s.utterance("two")
    # the engine formats text (capitalize / spell-numbers) — compare
    # case-insensitively and accept the digit normalization of "two"
    texts = [x.text.strip().lower() for x in s.segments()]
    assert texts[0] == "one"
    assert texts[1] in ("two", "2")


def test_stop_is_final(home):
    s = _session()
    s.utterance("bye")
    s.stop()
    assert s.state == "stopped"
    # a stopped session no longer accumulates
    s.utterance("more")
    assert len(s.segments()) == 1


def test_clear_empties_history(home):
    s = _session()
    s.utterance("a")
    s.utterance("b")
    assert s.clear() == 2
    assert s.segments() == []


def test_history_is_bounded(home):
    s = TranscribeSession(simulate=True, history_limit=50)
    for i in range(120):
        s.utterance(f"line {i}")
    segs = s.segments()
    assert len(segs) <= 50
    # the OLDEST entries were dropped
    assert segs[-1].text.lower().endswith("119")


# ---------------------------------------------------------------------------
# save — explicit only, text only, lands in the transcripts dir
# ---------------------------------------------------------------------------

def test_no_file_until_explicit_save(home):
    s = _session()
    s.utterance("private thought")
    tdir = paths.transcripts_dir()
    assert not os.path.exists(tdir) or not os.listdir(tdir)


def test_save_txt_writes_file(home):
    s = _session()
    s.utterance("save me")
    path, nbytes = s.save("txt")
    assert path and os.path.exists(path)
    assert nbytes > 0
    assert paths.transcripts_dir() in os.path.abspath(path)
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert "save me" in content.lower()
    assert s.saved_files() == [path]


def test_save_json_roundtrip(home):
    s = _session()
    s.utterance("structured", confidence=0.77)
    path, _n = s.save("json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["segments"][0]["text"].lower() == "structured"
    assert data["segments"][0]["confidence"] == pytest.approx(0.77)


def test_status_shape(home):
    s = _session()
    s.utterance("x")
    st = s.status()
    for key in ("state", "segments", "history_limit", "buffer_chars",
                "transcripts_dir", "simulated", "provider"):
        assert key in st
    assert st["segments"] == 1
    assert st["simulated"] is True


def test_provider_is_honest(home):
    s = _session()
    assert s.simulated is True
    assert "simulated" in s.provider_name.lower()


# ---------------------------------------------------------------------------
# the REPL loop
# ---------------------------------------------------------------------------

def test_run_transcribe_scripted_session(home):
    buf = io.StringIO()
    lines = iter(["hello there", "save", "quit"])
    rc = run_transcribe(out=buf, input_fn=lambda _p: next(lines),
                        simulate=True)
    assert rc == 0
    out = buf.getvalue()
    assert "LIVE TRANSCRIPTION" in out
    # honesty: the simulated banner is present
    assert "SIMULATED provider" in out
    tdir = paths.transcripts_dir()
    assert os.path.isdir(tdir) and os.listdir(tdir)


def test_run_transcribe_pause_clear_save_flow(home):
    buf = io.StringIO()
    lines = iter(["one", "pause", "skip this", "resume", "two",
                  "clear", "three", "save json", "stop"])
    rc = run_transcribe(out=buf, input_fn=lambda _p: next(lines),
                        simulate=True)
    assert rc == 0
    out = buf.getvalue()
    assert "paused" in out
    assert "cleared 2 segment(s)" in out
    assert "saved" in out


def test_run_transcribe_eof_is_clean(home):
    buf = io.StringIO()
    rc = run_transcribe(out=buf, input_fn=lambda _p: (_ for _ in ()).throw(
        EOFError), simulate=True)
    assert rc == 0


def test_render_panel_quotes_latest_text():
    class Seg:
        text = "quoted line"
        confidence = 0.9
        timestamp = 123.0

    panel = render_transcript_panel([Seg()], listening=True,
                                    provider="simulated")
    assert "quoted line" in panel
    assert "Listening" in panel or "▍" in panel or "listening" in panel.lower()
