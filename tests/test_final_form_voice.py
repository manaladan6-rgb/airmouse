"""Operation AIRLOCK rebuild — the voice-truth test matrix (R3c).

Covers the rebuilt honest-voice contract (TF-004/TF-005, 15 tests):

* voice_live:  BoundedAudioQueue DROP-OLDEST + exact overflow counter;
  consumer receives REAL PCM frames byte-exact and in order; the audio
  callback stays LIGHTWEIGHT even with a slow consumer (measured +
  structurally asserted: no recognition work in the callback); the
  configured/available/initialized/active state machine; idempotent
  close with bounded joins (never the current thread — structural);
  device disconnect → honest UNAVAILABLE, zero fake frames; overflow
  counter visible in status(); start_live_voice_for wiring.
* offline_voice:  feed_audio(None)/empty rejected honestly (no engine
  call, no exception leak); NO transcribe(None) anywhere on the path
  (structural AST + behavioral byte-exact proof); GRAMMAR-ONLY banner
  reason when the model is missing; LIVE when a real provider proves
  itself; the v5 cloud disclosure string; no fabricated confidence.
* selftest:  installed-but-unusable (vosk, no model) → SKIP with the
  reason (never a silent OPTIONAL rewrite); a FAIL stays FAIL (overall
  FAIL, not downgraded); SKIP counted in the tally.

No test touches the network, a microphone, or real audio hardware:
streams and probes are deterministic fakes.
"""

import ast
import os
import queue
import struct
import time

import pytest

import airmouse.asr_registry as asr_mod
import airmouse.offline_voice as ov
import airmouse.voice_live as vl
import airmouse.voice_stack as vs
from airmouse.asr_registry import ProviderDiagnosis, ProviderState
from airmouse.offline_voice import OfflineSpeechProvider, OfflineVoiceEngine, \
    Transcript
from airmouse.voice_live import BoundedAudioQueue, LiveVoiceCapture, \
    start_live_voice_for

import airmouse
_PKG = os.path.dirname(os.path.abspath(airmouse.__file__))


# ---------------------------------------------------------------------------
# deterministic fakes — no hardware, no network
# ---------------------------------------------------------------------------

_FAKE_STREAMS = []


class _FakeStream:
    """Stand-in for a sounddevice.InputStream (same lifecycle contract)."""

    def __init__(self, params):
        self.params = params
        self.started = False
        self.closed = False
        self.active = False
        _FAKE_STREAMS.append(self)

    def start(self):
        self.started = True
        self.active = True

    def stop(self):
        self.active = False
        self.started = False

    def close(self):
        self.closed = True
        self.active = False


def _ok_probe():
    return True


def _capture(**kw):
    kw.setdefault("stream_factory", _FakeStream)
    kw.setdefault("device_probe", _ok_probe)
    _FAKE_STREAMS.clear()
    return LiveVoiceCapture(**kw)


def _deliver(cap, payloads):
    """Drive the REAL audio callback with synthetic PCM frames."""
    for p in payloads:
        cap._on_audio(p, len(p) // 2, None, 0)


def _pcm(value, samples=480):
    return struct.pack("<%dh" % samples, *([value] * samples))


def _wait_until(pred, timeout=2.0, step=0.01):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


class _RecordingProvider(OfflineSpeechProvider):
    """Records every transcribe() argument — the no-None witness."""

    name = "recording"

    def __init__(self, texts=("open browser",)):
        self.calls = []
        self._texts = list(texts)

    def available(self):
        return True

    def transcribe(self, audio):
        self.calls.append(audio)
        text = self._texts[len(self.calls) - 1] \
            if len(self.calls) <= len(self._texts) else ""
        return Transcript(text=text, confidence=None, provider=self.name,
                          confidence_known=False)


def _utterance_chunks(n_loud=3, n_silence=25):
    """Loud onset chunks + enough silence for EnergyVAD to close (0.7 s
    end_silence at 30 ms frames → 24 silence chunks minimum)."""
    return [_pcm(1000)] * n_loud + [_pcm(0)] * n_silence


# ---------------------------------------------------------------------------
# 1. bounded queue: drops OLDEST + counts overflow exactly
# ---------------------------------------------------------------------------

def test_bounded_queue_drops_oldest_and_counts_overflow():
    q = BoundedAudioQueue(maxsize=3)
    for i in range(5):
        assert q.put_frame(bytes([i])) is True   # never blocks, never raises
    assert q.qsize() == 3
    assert q.overflow_dropped == 2               # exactly the 2 oldest
    got = [q.get_frame(timeout=0.01) for _ in range(3)]
    assert got == [bytes([2]), bytes([3]), bytes([4])]
    with pytest.raises(queue.Empty):
        q.get_frame(timeout=0.01)
    # the alias is the same honest counter
    assert q.overflow_count == 2
    # maxsize=1: every second put drops exactly one
    q1 = BoundedAudioQueue(maxsize=1)
    q1.put_frame(b"a")
    q1.put_frame(b"b")
    assert q1.overflow_dropped == 1
    assert q1.get_frame(timeout=0.01) == b"b"


# ---------------------------------------------------------------------------
# 2. consumer receives REAL PCM frames, byte-exact, in order
# ---------------------------------------------------------------------------

def test_consumer_receives_real_pcm_frames_in_order():
    got = []
    cap = _capture(consumer=got.append, queue_maxsize=64)
    assert cap.start() is True
    payloads = [_pcm(v) for v in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)]
    _deliver(cap, payloads)
    assert _wait_until(lambda: len(got) == 10), f"only {len(got)} frames"
    assert got == payloads            # byte-exact, in order, nothing invented
    st = cap.status()
    assert st["frames_captured"] == 10
    assert st["frames_consumed"] == 10
    assert st["overflow_dropped"] == 0
    cap.close()


# ---------------------------------------------------------------------------
# 3. the audio callback stays LIGHTWEIGHT (slow consumer proves it)
# ---------------------------------------------------------------------------

def test_audio_callback_stays_lightweight_with_slow_consumer():
    started = []

    def slow_consumer(frame):     # pretends to be a slow ASR path
        started.append(frame)
        time.sleep(0.05)

    cap = _capture(consumer=slow_consumer, queue_maxsize=2)
    assert cap.start() is True
    payloads = [_pcm(i % 30000 + 1) for i in range(40)]
    t0 = time.perf_counter()
    _deliver(cap, payloads)       # 40 callbacks; inline recognition would
    elapsed = time.perf_counter() - t0   # take ~2 s (40 x 50 ms)
    assert elapsed < 0.5, \
        f"audio callback blocked {elapsed*1000:.0f}ms — work leaked in"
    assert cap.status()["frames_captured"] == 40
    assert cap.overflow_dropped > 0          # queue stayed bounded, honestly
    # structural: the callback body must not call recognition/engine work
    src = open(os.path.join(_PKG, "voice_live.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_on_audio")
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    forbidden = {"transcribe", "feed_audio", "recognize", "recognizer",
                 "vad", "feed_transcript", "poll", "available", "select"}
    assert not names & forbidden, names & forbidden
    assert "put_frame" in names              # the ONLY payload work: enqueue
    cap.close()


# ---------------------------------------------------------------------------
# 4. feed_audio(None) and empty payloads rejected honestly
# ---------------------------------------------------------------------------

def test_feed_audio_rejects_none_and_empty_payloads_honestly():
    provider = _RecordingProvider()
    eng = OfflineVoiceEngine({}, provider=provider)
    # None payload: rejected, no engine call, no exception leak
    assert eng.feed_audio(None) is False
    assert provider.calls == []                       # engine never called
    assert eng.rejected_audio == 1
    assert "None" in eng.last_audio_reject_reason
    # empty payload: same honest rejection
    assert eng.feed_audio(b"") is False
    assert provider.calls == []
    assert eng.rejected_audio == 2
    assert "empty" in eng.last_audio_reject_reason
    # the rejections are visible in the honest status snapshot
    st = eng.status()
    assert st["rejected_audio"] == 2
    assert st["last_audio_reject_reason"]
    # real audio still works after rejections (one full utterance)
    chunks = _utterance_chunks()
    for i, chunk in enumerate(chunks):
        assert eng.feed_audio(chunk, now=i * 0.03) is True
    assert len(provider.calls) == 1 and provider.calls[0]


# ---------------------------------------------------------------------------
# 5. NO transcribe(None) anywhere on the path (structural + behavioral)
# ---------------------------------------------------------------------------

def test_no_transcribe_none_anywhere_on_the_path():
    # structural: no module on the rebuilt path CALLS transcribe(None).
    # (Docstrings that MENTION the ban are documentation, not calls —
    # the AST check is the accurate one: it sees executable code only.)
    for mod in ("offline_voice.py", "voice_live.py"):
        src = open(os.path.join(_PKG, mod), encoding="utf-8").read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = node.func.id if isinstance(node.func, ast.Name) else \
                getattr(node.func, "attr", "")
            if fname == "transcribe" and node.args:
                first = node.args[0]
                assert not (isinstance(first, ast.Constant)
                            and first.value is None), \
                    f"{mod}: transcribe(None) is forbidden (TF-004)"
    # behavioral: the engine hands REAL buffered PCM to the provider
    provider = _RecordingProvider()
    eng = OfflineVoiceEngine({}, provider=provider)
    chunks = _utterance_chunks()
    for i, chunk in enumerate(chunks):
        eng.feed_audio(chunk, now=i * 0.03)
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call is not None and isinstance(call, bytes) and len(call) > 0
    # byte-exact verbatim prefix: every REAL chunk in feed order —
    # nothing invented, nothing reordered; the whole loud onset is in
    frame = len(chunks[0])
    assert len(call) % frame == 0
    assert call == b"".join(chunks[: len(call) // frame])
    assert call.startswith(b"".join(chunks[:3]))
    assert eng.last_transcript == "open browser"
    assert eng.poll() is not None


# ---------------------------------------------------------------------------
# 6. GRAMMAR-ONLY banner state + exact reason when the model is missing
# ---------------------------------------------------------------------------

def test_grammar_only_banner_reason_when_model_missing(monkeypatch):
    # deterministic fake machine: vosk importable, NO model anywhere,
    # and no usable provider — exactly the vosk-installed-no-model case
    monkeypatch.setattr(asr_mod.registry, "select",
                        lambda *a, **k: None)
    monkeypatch.setattr(vs, "_spec_found",
                        lambda m: str(m).startswith("vosk"))
    state, reason = ov.session_banner_state()
    assert state == ov.BANNER_GRAMMAR_ONLY == "GRAMMAR-ONLY"
    assert "vosk" in reason
    assert "voice-setup" in reason            # the actionable remedy
    assert reason == ov.grammar_only_reason()
    # the LIVE branch is honest too: a provider that proves available()
    class _Ok(OfflineSpeechProvider):
        name = "fake-ok"
        def available(self):
            return True
    monkeypatch.setattr(asr_mod.registry, "select",
                        lambda *a, **k: _Ok())
    state, reason = ov.session_banner_state()
    assert state == ov.BANNER_LIVE == "LIVE"
    assert "fake-ok" in reason
    # a provider that constructs but does NOT prove available() must
    # NOT be called LIVE — readiness is proven, never assumed
    class _Dead(OfflineSpeechProvider):
        name = "dead"
        def available(self):
            return False
    monkeypatch.setattr(asr_mod.registry, "select",
                        lambda *a, **k: _Dead())
    assert ov.session_banner_state()[0] == "GRAMMAR-ONLY"


# ---------------------------------------------------------------------------
# 7. selftest: vosk installed + no model → SKIP with the reason
# ---------------------------------------------------------------------------

def test_selftest_vosk_no_model_is_skip_with_reason(monkeypatch):
    monkeypatch.setattr(asr_mod.registry, "discover", lambda force=False: {
        "pocketsphinx": ProviderDiagnosis(name="pocketsphinx"),
        "vosk": ProviderDiagnosis(
            name="vosk", installed=True, importable=True,
            initialization="MODEL MISSING",
            state=ProviderState.INSTALLED,
            reason="vosk engine is installed but no local acoustics model "
                   "is available — run: airmouse voice-setup"),
        "whisper": ProviderDiagnosis(name="whisper"),
    })
    monkeypatch.setattr(vs, "_spec_found",
                        lambda m: str(m).startswith("vosk"))
    monkeypatch.setattr(ov, "detect_providers",
                        lambda: {"vosk": False, "pocketsphinx": False,
                                 "whisper": False, "simulated": True})
    from airmouse import selftest as st
    rows = st.run_self_test()
    row = next(r for r in rows if r.component == "RealLocalASR")
    assert row.status == st.SKIP              # installed but unusable
    assert row.status != st.OPTIONAL          # never silently rewritten
    assert row.status != st.FAIL              # the install is not broken
    assert "voice-setup" in row.detail        # the actionable remedy
    assert "model" in row.detail.lower()
    assert row.ok is True                     # a SKIP is not a failure


# ---------------------------------------------------------------------------
# 8. self-test overall FAIL stays FAIL (never downgraded)
# ---------------------------------------------------------------------------

def test_selftest_fail_stays_fail_and_overall_fails(monkeypatch):
    from airmouse import selftest as st
    # unit level: a FAIL row is a FAIL — the reporter must not soften it
    rows = [st.SelfTestResult("Core", st.PASS, "ok"),
            st.SelfTestResult("Voice", st.FAIL, "fake voice failure",
                              required=True)]
    out = st.format_self_test(rows)
    assert "RESULT: FAIL" in out
    assert st.SelfTestResult("Voice", st.FAIL).ok is False
    # functional level: force the required Voice check to fail for real
    def _boom(*a, **k):
        raise RuntimeError("fake voice failure")
    monkeypatch.setattr(ov, "OfflineVoiceEngine", _boom)
    results = st.run_self_test()
    row = next(r for r in results if r.component == "Voice")
    assert row.status == st.FAIL              # stays FAIL — not OPTIONAL,
    assert row.status not in (st.OPTIONAL, st.SKIP, st.HARDWARE)  # not soft
    assert "fake voice failure" in row.detail
    out = st.format_self_test(results)
    assert "RESULT: FAIL" in out              # the OVERALL result fails too
    assert not all(r.ok for r in results)     # honest exit code: rc 1


# ---------------------------------------------------------------------------
# 9. SKIP is counted in the tally (and included in the totals)
# ---------------------------------------------------------------------------

def test_selftest_tally_counts_skip_separately():
    from airmouse import selftest as st
    rows = [st.SelfTestResult("A", st.PASS, "ok"),
            st.SelfTestResult("B", st.SKIP, "vosk installed, no model"),
            st.SelfTestResult("C", st.FAIL, "broken")]
    out = st.format_self_test(rows)
    assert "RESULT: FAIL" in out                       # the FAIL governs
    assert "(1 pass, 1 skip, 0 optional, 0 hardware, 1 fail)" in out
    assert "SKIP" in out                               # the row is rendered
    assert "vosk installed, no model" in out           # with its reason
    all_skip = st.format_self_test([st.SelfTestResult("B", st.SKIP, "r")])
    assert "RESULT: PASS" in all_skip                  # SKIP alone ≠ FAIL
    assert "1 skip" in all_skip


# ---------------------------------------------------------------------------
# 10. the honest state machine: configured/available/initialized/active
# ---------------------------------------------------------------------------

def test_capture_state_machine_transitions():
    cap = _capture(consumer=lambda f: None)
    # constructed: CONFIGURED — configured True, everything else unproven
    assert cap.state == LiveVoiceCapture.STATE_CONFIGURED
    assert cap.configured and not cap.available
    assert not cap.initialized and not cap.active
    # probe passes → AVAILABLE (device proven, not yet capturing)
    assert cap.probe() is True
    assert cap.state == LiveVoiceCapture.STATE_AVAILABLE
    assert cap.available and not cap.active and not cap.initialized
    # start → ACTIVE with a real stream opened (initialized)
    assert cap.start() is True
    assert cap.state == LiveVoiceCapture.STATE_ACTIVE
    assert cap.active and cap.initialized and cap.available
    assert len(_FAKE_STREAMS) == 1 and _FAKE_STREAMS[0].started
    # stop → back to AVAILABLE (probed, restartable, not active)
    cap.stop()
    assert cap.state == LiveVoiceCapture.STATE_AVAILABLE
    assert not cap.active and cap.initialized
    # restart works, close is terminal
    assert cap.start() is True
    assert cap.state == LiveVoiceCapture.STATE_ACTIVE
    cap.close()
    assert cap.state == LiveVoiceCapture.STATE_CLOSED
    assert not cap.active and not cap.available and not cap.configured
    # a failed probe is honest UNAVAILABLE, never a fake state
    cap2 = _capture(device_probe=lambda: False)
    assert cap2.start() is False
    assert cap2.state == LiveVoiceCapture.STATE_UNAVAILABLE
    assert cap2.status()["device_error"]
    cap2.close()


# ---------------------------------------------------------------------------
# 11. close(): idempotent + bounded joins + never joins current thread
# ---------------------------------------------------------------------------

def test_close_is_idempotent_with_bounded_joins():
    got = []
    cap = _capture(consumer=got.append)
    assert cap.start() is True
    _deliver(cap, [_pcm(7), _pcm(8)])
    assert _wait_until(lambda: len(got) == 2)
    cap.close(timeout=1.0)
    first_status = cap.status()
    assert first_status["state"] == LiveVoiceCapture.STATE_CLOSED
    assert first_status["closed"] is True
    cap.close(timeout=1.0)                    # idempotent: no error, no-op
    assert cap.status()["state"] == LiveVoiceCapture.STATE_CLOSED
    assert cap._consumer_thread is None       # consumer was joined (bounded)
    # structural: EVERY join in voice_live carries a timeout, and the
    # module never joins threading.current_thread()
    src = open(os.path.join(_PKG, "voice_live.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    joins = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "join"]
    assert joins, "expected bounded joins in voice_live.py"
    for call in joins:
        assert any(kw.arg == "timeout" for kw in call.keywords), \
            "unbounded join found — deadlock by construction"
    assert "is not threading.current_thread()" in src   # the self-join guard


# ---------------------------------------------------------------------------
# 12. device disconnect → honest UNAVAILABLE, zero fabricated frames
# ---------------------------------------------------------------------------

def test_disconnect_honest_unavailable_no_fake_frames():
    got = []
    cap = _capture(consumer=got.append, queue_maxsize=64)
    assert cap.start() is True
    stream = _FAKE_STREAMS[0]
    payloads = [_pcm(v) for v in (11, 12, 13, 14, 15)]
    _deliver(cap, payloads)
    assert _wait_until(lambda: len(got) == 5)
    # the device disappears: PortAudio deactivates the stream on unplug
    stream.active = False
    assert _wait_until(lambda: cap.state == LiveVoiceCapture.STATE_UNAVAILABLE)
    assert cap.status()["device_error"]
    assert not cap.active and not cap.available
    time.sleep(0.2)                      # grace: nothing more may arrive
    assert got == payloads               # EXACTLY the real frames — no
    assert cap.status()["frames_captured"] == 5   # fabricated audio, ever
    assert cap.overflow_dropped == 0
    cap.close()


# ---------------------------------------------------------------------------
# 13. the v5 cloud disclosure string exists and is the honest banner
# ---------------------------------------------------------------------------

def test_cloud_disclosure_string_exists_and_discloses_cloud():
    assert ov.CLOUD_DISCLOSURE == "uses Google Web Speech (cloud)"
    banner = ov.cloud_voice_banner()
    assert ov.CLOUD_DISCLOSURE in banner
    assert "cloud" in banner.lower()
    # the v5 path itself really is the Google Web Speech path — its own
    # module must disclose it (structural), so the banner is truthful
    v5_src = open(os.path.join(_PKG, "voice_control.py"),
                  encoding="utf-8").read()
    assert "Google Web Speech" in v5_src
    assert "recognize_google" in v5_src
    # and the banner never dresses the cloud path up as OFFLINE voice
    assert "OFFLINE VOICE" not in banner
    assert "(offline)" not in banner.lower()


# ---------------------------------------------------------------------------
# 14. no fabricated confidence: unknown is None, never an invented number
# ---------------------------------------------------------------------------

def test_no_fabricated_confidence_unknown_is_none():
    # PocketSphinx exposes no confidence → None + known=False
    class _FakeRecognizer:
        def recognize_sphinx(self, audio):
            return "hello world"
    ps = ov.PocketSphinxProvider(recognizer=_FakeRecognizer())
    t = ps.transcribe(object())
    assert t.text == "hello world"
    assert t.confidence is None and t.confidence_known is False
    # vosk: a number ONLY when the engine actually reported one
    import json as _json
    import vosk as _vosk

    class _FakeKR:
        payload = {"text": "hi there"}
        def __init__(self, model, rate):
            pass
        def AcceptWaveform(self, data):
            return False
        def Result(self):
            return _json.dumps(self.payload)
        def FinalResult(self):
            return _json.dumps(self.payload)

    vp = ov.VoskProvider()
    assert vp.available() is False        # this sandbox: no model — honest
    monkey_patch_target = _vosk
    saved = monkey_patch_target.KaldiRecognizer
    try:
        monkey_patch_target.KaldiRecognizer = _FakeKR
        vp._model = object()              # bypass the (absent) real model
        t = vp.transcribe(b"\x00\x00" * 480)
        assert t.text == "hi there"
        assert t.confidence is None and t.confidence_known is False
        _FakeKR.payload = {"text": "hi there", "confidence": 0.83}
        t2 = vp.transcribe(b"\x00\x00" * 480)
        assert t2.confidence == 0.83 and t2.confidence_known is True
    finally:
        monkey_patch_target.KaldiRecognizer = saved
    # the engine path: unknown confidence flows through without a leak
    # and is recorded as UNKNOWN — never replaced by an invented number
    eng = OfflineVoiceEngine({}, provider=_RecordingProvider())
    eng.feed_transcript("open browser", None, now=1.0)
    assert eng.last_confidence_known is False
    assert eng.status()["last_confidence_known"] is False
    assert eng.poll() is not None          # the command still fired


# ---------------------------------------------------------------------------
# 15. the overflow counter is visible in the honest status API
# ---------------------------------------------------------------------------

def test_overflow_counter_visible_in_status():
    started = []

    def slow_consumer(frame):      # parks on frame 1, holding the drain
        started.append(frame)
        time.sleep(0.3)

    cap = _capture(consumer=slow_consumer, queue_maxsize=2)
    assert cap.start() is True
    _deliver(cap, [_pcm(99)])
    assert _wait_until(lambda: len(started) == 1)   # consumer provably busy
    burst = [_pcm(v + 1) for v in range(5)]         # capacity 2 → exactly
    t0 = time.perf_counter()                        # 3 oldest dropped
    _deliver(cap, burst)
    assert time.perf_counter() - t0 < 0.2           # puts never block
    st = cap.status()
    assert st["overflow_dropped"] == 3
    assert st["overflow_dropped"] == cap.overflow_dropped
    assert st["frames_captured"] == 6               # all real frames seen
    assert st["queue_size"] == 2                    # queue stayed bounded
    assert st["queue_maxsize"] == 2
    assert st["state"] == LiveVoiceCapture.STATE_ACTIVE
    cap.close(timeout=2.0)
    assert cap.status()["overflow_dropped"] == 3    # survives the close
    # and the wiring marker hands the REAL engine path the capture
    eng = OfflineVoiceEngine({})
    wired = start_live_voice_for(eng, stream_factory=_FakeStream,
                                 device_probe=lambda: False)
    assert isinstance(wired, LiveVoiceCapture)
    assert wired.status()["state"] == LiveVoiceCapture.STATE_UNAVAILABLE
    wired.close()
    assert start_live_voice_for(object()) is None   # no feed_audio → honest
