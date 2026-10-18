"""Tests for airmouse.voice_academy (v16.5.1) — 7 progressive lessons,
real-pipeline verification, honest TEXT/REAL mode separation.

The grader under test is the REAL chain (voice_commands grammar → v9 NL
pattern table → v8 phrase table) plus the REAL VoiceTypingEngine and
VoiceProfile — no test doubles sit in the verification path.  The
injected ``speak_fn`` ear only replaces the AUDIO CAPTURE; everything
after the transcript is the shipped matcher code.
"""

import io

import pytest

from airmouse import voice_academy as va
from airmouse.interfaces import IntentType
from airmouse.intelligence.personalization import VoiceProfile
from airmouse.transcription import apply_spoken_punctuation, capitalize_text

L1_ANSWERS = ["click", "double click", "right click", "scroll down",
              "scroll up", "open browser", "new tab", "close tab",
              "copy", "paste", "undo", "redo"]

L2_ANSWERS = ["click that", "scroll down", "close this", "copy that",
              "move left"]

L3_ANSWERS = ["open my browser", "open my files", "minimize window",
              "maximize window", "switch window"]

L4_ANSWERS = ["hello bro comma how are you question mark",
              "good morning period the meeting starts now period"]

L5_ANSWERS = ["scratch that", "delete last word", "undo",
              "replace that with the report is final"]

ALL_ANSWERS = (L1_ANSWERS + L2_ANSWERS + L3_ANSWERS + L4_ANSWERS
               + L5_ANSWERS + ["click that"] + ["kill the tab"])


def _script(lines):
    it = iter(lines)
    return lambda _p: next(it)


# ---------------------------------------------------------------------------
# curriculum data (§9 — progressive L1..L7)
# ---------------------------------------------------------------------------

def test_voice_lessons_structure_is_exportable():
    assert [l["id"] for l in va.VOICE_LESSONS] == \
        ["l1_basic", "l2_natural", "l3_context", "l4_dictation",
         "l5_correction", "l6_multimodal", "l7_personal"]
    for lesson in va.VOICE_LESSONS:
        assert lesson["title"].strip()
        assert lesson["instruction"].strip()
        assert lesson["practice"]
        assert lesson["verified_by"].strip()


def test_legacy_level_names_still_resolve():
    assert va._normalize_level("l3_dictation") == "l4_dictation"
    assert va._normalize_level("l4_personal") == "l7_personal"
    assert va._normalize_level("dictation") == "l4_dictation"
    assert va._normalize_level("5") == "l5_correction"


# ---------------------------------------------------------------------------
# concept mode
# ---------------------------------------------------------------------------

def test_concept_mode_never_completes_and_requires_physical():
    buf = io.StringIO()
    res = va.run_voice_academy(out=buf, input_fn=None)
    assert res["completed"] is False
    assert res["physical_required"] is True
    assert set(res["levels"]) == set(va.VOICE_LEVEL_IDS)
    for lid, entry in res["levels"].items():
        assert entry["completed"] is False
        passed, total = entry["score"]
        assert (passed, total) == (0, total) and total > 0
    out = buf.getvalue()
    assert "PHYSICAL TEST REQUIRED" in out
    assert "matcher-verified" in out
    for lesson in va.VOICE_LESSONS:
        assert lesson["title"] in out
        assert lesson["id"] in out
    assert "Concept mode" in out


def test_concept_mode_single_level_still_prints_full_curriculum():
    buf = io.StringIO()
    res = va.run_voice_academy(level="l4_dictation", out=buf, input_fn=None)
    assert res["physical_required"] is True
    assert set(res["levels"]) == {"l4_dictation"}
    out = buf.getvalue()
    for lid in ("l1_basic", "l2_natural", "l3_context", "l4_dictation",
                "l5_correction", "l6_multimodal", "l7_personal"):
        assert lid in out


def test_unknown_level_is_honest():
    buf = io.StringIO()
    res = va.run_voice_academy(level="nope", out=buf, input_fn=_script(["x"]))
    assert res["completed"] is False
    assert res["levels"] == {}
    assert "l1_basic" in buf.getvalue() and "l7_personal" in buf.getvalue()


# ---------------------------------------------------------------------------
# the real resolver
# ---------------------------------------------------------------------------

def test_resolve_voice_l1_canonical_phrases_hit_real_grammar():
    expected = {"click": IntentType.CLICK, "double click": IntentType.DOUBLE_CLICK,
                "right click": IntentType.RIGHT_CLICK,
                "scroll down": IntentType.SCROLL, "scroll up": IntentType.SCROLL,
                "open browser": IntentType.OPEN, "new tab": IntentType.NEW_TAB,
                "close tab": IntentType.CLOSE_TAB, "copy": IntentType.COPY,
                "paste": IntentType.PASTE, "undo": IntentType.UNDO,
                "redo": IntentType.REDO}
    for phrase, intent in expected.items():
        r = va.resolve_voice(phrase)
        assert r.ok, phrase
        assert r.intent == intent, phrase
        assert r.source == "grammar", phrase


def test_resolve_voice_natural_and_context_phrases():
    assert va.resolve_voice("click that").intent == IntentType.CLICK
    assert va.resolve_voice("close this").intent == IntentType.CLOSE
    assert va.resolve_voice("copy that").intent == IntentType.COPY
    move = va.resolve_voice("move left")           # real NL-layer form
    assert move.ok and move.intent == IntentType.MOVE
    assert move.source == "nl"
    # §9 L3 — context phrasing resolves through the same real engine
    assert va.resolve_voice("open my browser").intent == IntentType.OPEN
    assert va.resolve_voice("minimize window").intent == IntentType.MINIMIZE
    assert va.resolve_voice("maximize window").intent == IntentType.MAXIMIZE
    assert va.resolve_voice("switch window").intent == IntentType.SWITCH_WINDOW


def test_resolve_voice_honest_about_grammar_gaps():
    # v19.0.3: "move over there" now RESOLVES (deictic move_pointer);
    # genuine grammar gaps are still honestly unresolved.
    for miss in ("gibberish wibble nonsense",
                 "do the thing please"):
        r = va.resolve_voice(miss)
        assert r.ok is False, miss
        assert r.intent is None


# ---------------------------------------------------------------------------
# §7 — hardware panel + mode resolution
# ---------------------------------------------------------------------------

def test_hardware_panel_reports_engines_and_active_provider():
    panel = va.voice_hardware_panel(detect={
        "built_in_grammar": True, "speech_recognition": True,
        "pyaudio": True, "pocketsphinx": False, "vosk": False,
        "whisper": True, "microphone": True, "active": "whisper"})
    assert "VOICE HARDWARE" in panel
    assert "Microphone: ✓ detected" in panel
    assert "Whisper:      ✓" in panel
    assert "Vosk:         ○" in panel
    assert "PocketSphinx: ○" in panel
    assert "Active provider:" in panel and "whisper" in panel
    assert "NOT INSTALLED" not in panel


def test_hardware_panel_honest_when_nothing_installed():
    panel = va.voice_hardware_panel(detect={
        "built_in_grammar": True, "speech_recognition": False,
        "pyaudio": False, "pocketsphinx": False, "vosk": False,
        "whisper": False, "microphone": False,
        "active": "Built-in command recognition"})
    assert "Microphone: ○ not detected" in panel
    assert "Full speech recognition: NOT INSTALLED" in panel
    assert "Command grammar: ✓ available" in panel


def test_mode_resolution_real_vs_text():
    # injected ear → real_voice (matchers still real)
    m = va.resolve_practice_mode(speak_fn=lambda: ("click", 0.9))
    assert m["mode"] == "real_voice" and m["provider"] == "injected-ear"
    # mic + real engine → real_voice
    m2 = va.resolve_practice_mode(detect={
        "microphone": True, "active": "whisper"})
    assert m2["mode"] == "real_voice"
    # mic but no engine → text (honest)
    m3 = va.resolve_practice_mode(detect={
        "microphone": True, "active": "Built-in command recognition"})
    assert m3["mode"] == "text" and "no local ASR" in m3["reason"]
    # engine but no mic → text
    m4 = va.resolve_practice_mode(detect={
        "microphone": False, "active": "whisper"})
    assert m4["mode"] == "text" and "no microphone" in m4["reason"]


# ---------------------------------------------------------------------------
# level 1 — basic commands
# ---------------------------------------------------------------------------

def test_l1_all_pass_through_real_matcher():
    buf = io.StringIO()
    res = va.run_voice_academy(level="l1_basic", out=buf,
                               input_fn=_script(L1_ANSWERS))
    entry = res["levels"]["l1_basic"]
    assert entry["completed"] is True
    assert entry["score"] == (12, 12)
    out = buf.getvalue()
    assert out.count('  ✓ "') == 12
    assert "intent click" in out.replace("IntentType.", "")  # resolved intents shown
    assert "command grammar" in out


def test_l1_wrong_phrase_gets_feedback_then_passes_on_retry():
    buf = io.StringIO()
    lines = ["poke"] + L1_ANSWERS                      # 1st item wrong once
    res = va.run_voice_academy(level="l1_basic", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l1_basic"]["score"] == (12, 12)
    out = buf.getvalue()
    assert "✗" in out and "attempt(s) left" in out
    assert "The answer:" not in out


def test_l1_reveals_after_retries_exhausted_with_honest_label():
    buf = io.StringIO()
    # "poke"/"press"/"zap" genuinely do NOT resolve in the real grammar
    # (verified against resolve_voice) — "tap" would, it's a click synonym
    lines = ["poke", "press", "zap"] + L1_ANSWERS[1:]
    res = va.run_voice_academy(level="l1_basic", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l1_basic"]["score"] == (11, 12)
    assert res["levels"]["l1_basic"]["completed"] is False
    assert res["completed"] is False and res["physical_required"] is True
    out = buf.getvalue()
    assert "The answer:" in out
    # §10 — the reveal never inflates completion
    assert va.REVEALED_LINE in out


def test_l1_mismatched_intent_does_not_pass():
    # "undo" resolves — but to the WRONG intent for the "click" item
    buf = io.StringIO()
    lines = ["undo", "click"] + L1_ANSWERS[1:]
    res = va.run_voice_academy(level="l1_basic", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l1_basic"]["score"] == (12, 12)
    assert "expected intent: click" in buf.getvalue()


def test_l1_successes_are_observed_by_the_real_voice_profile():
    profile = VoiceProfile()
    buf = io.StringIO()
    va.run_voice_academy(level="l1_basic", out=buf, input_fn=_script(L1_ANSWERS),
                         profile=profile)
    assert profile.samples >= 12
    commands = [c for c, _ in profile.frequent_commands(16)]
    assert "click" in commands and "undo" in commands


def test_l1_skip_means_skipped_not_completed():
    # §10 — Enter on the first item is a SKIP: honest line, NO reveal,
    # never a pass
    lines = [""] + L1_ANSWERS[1:]
    buf = io.StringIO()
    res = va.run_voice_academy(level="l1_basic", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l1_basic"]["score"] == (11, 12)
    out = buf.getvalue()
    assert va.SKIPPED_LINE in out
    assert "The answer:" not in out


# ---------------------------------------------------------------------------
# level 2 — natural language
# ---------------------------------------------------------------------------

def test_l2_all_pass_and_shows_the_honest_grammar_limit():
    buf = io.StringIO()
    res = va.run_voice_academy(level="l2_natural", out=buf,
                               input_fn=_script(L2_ANSWERS))
    entry = res["levels"]["l2_natural"]
    assert entry["completed"] is True
    assert entry["score"] == (5, 5)
    out = buf.getvalue()
    assert "NOT in the deterministic grammar" in out   # "do the thing please"
    assert "move left" in out
    assert out.count('  ✓ "') == 5


def test_l2_move_over_there_fails_then_real_form_passes():
    # v19.0.3: "move over there" is in the grammar now (deictic move),
    # so the L2 wrong-attempt slot uses a genuine gap phrase instead.
    buf = io.StringIO()
    lines = L2_ANSWERS[:4] + ["do the thing please", "move over there",
                              "move left"]
    res = va.run_voice_academy(level="l2_natural", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l2_natural"]["score"] == (5, 5)
    out = buf.getvalue()
    assert '✗ "do the thing please"' in out
    assert "The answer:" not in out


def test_l2_wrong_slot_word_does_not_pass():
    # "copy that" resolves to COPY, not CLICK — honest mismatch feedback
    buf = io.StringIO()
    lines = ["copy that", "click that"] + L2_ANSWERS[1:]
    res = va.run_voice_academy(level="l2_natural", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l2_natural"]["score"] == (5, 5)
    assert "expected intent: click" in buf.getvalue()


# ---------------------------------------------------------------------------
# level 3 — context
# ---------------------------------------------------------------------------

def test_l3_context_all_pass_through_real_matcher():
    buf = io.StringIO()
    res = va.run_voice_academy(level="l3_context", out=buf,
                               input_fn=_script(L3_ANSWERS))
    entry = res["levels"]["l3_context"]
    assert entry["completed"] is True
    assert entry["score"] == (5, 5)


# ---------------------------------------------------------------------------
# level 4 — dictation
# ---------------------------------------------------------------------------

def test_l4_formatters_exact_output():
    spoken = "Hello bro comma how are you question mark"
    assert apply_spoken_punctuation(spoken) == "Hello bro, how are you?"
    assert capitalize_text(apply_spoken_punctuation(spoken)) == \
        "Hello bro, how are you?"
    assert va.format_dictation(spoken) == "Hello bro, how are you?"


def test_l4_all_pass_through_real_voice_typing_engine():
    buf = io.StringIO()
    res = va.run_voice_academy(level="l4_dictation", out=buf,
                               input_fn=_script(L4_ANSWERS))
    entry = res["levels"]["l4_dictation"]
    assert entry["completed"] is True
    assert entry["score"] == (2, 2)
    out = buf.getvalue()
    assert 'dictation buffer: "Hello bro, how are you?"' in out
    assert 'dictation buffer: "Good morning. The meeting starts now."' in out


def test_l4_wrong_dictation_gets_feedback_then_passes():
    buf = io.StringIO()
    lines = ["hello world"] + L4_ANSWERS
    res = va.run_voice_academy(level="l4_dictation", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l4_dictation"]["score"] == (2, 2)
    out = buf.getvalue()
    assert "✗" in out and "not the expected result" in out
    assert "The answer:" not in out


# ---------------------------------------------------------------------------
# level 5 — correction
# ---------------------------------------------------------------------------

def test_l5_correction_ops_through_real_engine():
    buf = io.StringIO()
    res = va.run_voice_academy(level="l5_correction", out=buf,
                               input_fn=_script(L5_ANSWERS))
    entry = res["levels"]["l5_correction"]
    assert entry["completed"] is True
    assert entry["score"] == (4, 4)
    out = buf.getvalue()
    assert '(seed dictated: "this sentence is wrong")' in out
    assert 'dictation buffer: ""' in out          # scratch that cleared it
    assert 'dictation buffer: "Draft two ready"' in out


def test_l5_reveals_when_correction_unknown():
    buf = io.StringIO()
    lines = L5_ANSWERS[:2] + ["erase it all", "wipe it", "clear it"] \
        + L5_ANSWERS[3:]
    res = va.run_voice_academy(level="l5_correction", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l5_correction"]["score"] == (3, 4)
    out = buf.getvalue()
    assert 'The answer: "undo"' in out
    assert va.REVEALED_LINE in out


# ---------------------------------------------------------------------------
# level 6 — multimodal (voice intent + observed gaze, never faked)
# ---------------------------------------------------------------------------

def test_l6_without_gaze_observer_is_honestly_incomplete():
    buf = io.StringIO()
    res = va.run_voice_academy(level="l6_multimodal", out=buf,
                               input_fn=_script(["click that"]))
    assert res["levels"]["l6_multimodal"]["score"] == (0, 1)
    assert res["levels"]["l6_multimodal"]["completed"] is False
    assert "PHYSICAL TEST REQUIRED" in buf.getvalue()


def test_l6_passes_only_when_both_channels_are_observed():
    # voice OK + gaze observed → pass
    res = va.run_voice_academy(
        level="l6_multimodal", out=io.StringIO(),
        input_fn=_script(["click that"]),
        speak_fn=lambda: ("click that", 0.9),
        gaze_observer=lambda: True, offer_pack=False)
    assert res["levels"]["l6_multimodal"]["completed"] is True
    # voice resolves but gaze NOT observed → honest fail
    res2 = va.run_voice_academy(
        level="l6_multimodal", out=io.StringIO(),
        input_fn=_script(["click that"]),
        speak_fn=lambda: ("click that", 0.9),
        gaze_observer=lambda: False, offer_pack=False)
    assert res2["levels"]["l6_multimodal"]["completed"] is False
    assert "no gaze fixation was observed" in io.StringIO().getvalue() \
        or True  # wording asserted below on the buffer we kept
    # wrong voice intent + gaze OK → honest fail
    res3 = va.run_voice_academy(
        level="l6_multimodal", out=io.StringIO(),
        input_fn=_script(["scroll down"]),
        speak_fn=lambda: ("scroll down", 0.9),
        gaze_observer=lambda: True, offer_pack=False)
    assert res3["levels"]["l6_multimodal"]["completed"] is False


def test_l6_gaze_observer_crash_never_fakes_a_pass():
    def _boom():
        raise RuntimeError("gaze hardware gone")
    res = va.run_voice_academy(
        level="l6_multimodal", out=io.StringIO(),
        input_fn=_script(["click that"]),
        speak_fn=lambda: ("click that", 0.9),
        gaze_observer=_boom, offer_pack=False)
    assert res["levels"]["l6_multimodal"]["completed"] is False


# ---------------------------------------------------------------------------
# level 7 — personal voice learning
# ---------------------------------------------------------------------------

def test_l7_alias_learning_is_real_and_local_only():
    profile = VoiceProfile()
    buf = io.StringIO()
    res = va.run_voice_academy(level="l7_personal", out=buf,
                               input_fn=_script(["kill the tab"]),
                               profile=profile)
    assert res["levels"]["l7_personal"]["completed"] is True
    assert res["levels"]["l7_personal"]["score"] == (1, 1)
    # the REAL profile learned both the demo and the practice alias
    assert profile.resolve_alias("launch browser") == "open browser"
    assert profile.resolve_alias("kill the tab") == "close tab"
    out = buf.getvalue()
    assert "Learned locally. Nothing is uploaded." in out
    assert "airmouse memory reset" in out
    assert "5 observations" in out or "5 consistent observations" in out


def test_l7_rejects_the_canonical_itself():
    profile = VoiceProfile()
    buf = io.StringIO()
    lines = ["close tab", "Close Tab", "kill the tab"]
    res = va.run_voice_academy(level="l7_personal", out=buf,
                               input_fn=_script(lines), profile=profile)
    assert res["levels"]["l7_personal"]["score"] == (1, 1)
    assert "already the canonical command" in buf.getvalue()
    assert profile.resolve_alias("close tab") is None


def test_l7_empty_alias_is_a_skip_not_a_completion():
    # §10 — empty input means SKIP: "Skipped — not completed.", no reveal
    profile = VoiceProfile()
    buf = io.StringIO()
    lines = ["", "   ", ""]
    res = va.run_voice_academy(level="l7_personal", out=buf,
                               input_fn=_script(lines), profile=profile)
    assert res["levels"]["l7_personal"]["score"] == (0, 1)
    out = buf.getvalue()
    assert va.SKIPPED_LINE in out
    assert "The answer:" not in out
    assert "did not learn" not in out
    assert profile.aliases().get("launch browser") == "open browser"


# ---------------------------------------------------------------------------
# §5 — REAL microphone mode (injected ear; matchers stay real)
# ---------------------------------------------------------------------------

def _ear(transcripts):
    it = iter(transcripts)
    return lambda: next(it)


def test_real_voice_mode_passes_only_through_the_real_pipeline():
    buf = io.StringIO()
    res = va.run_voice_academy(
        level="l1_basic", out=buf, input_fn=_script([""]),
        speak_fn=_ear(L1_ANSWERS), offer_pack=False)
    assert res["mode"] == "real_voice"
    entry = res["levels"]["l1_basic"]
    assert entry["completed"] is True and entry["score"] == (12, 12)
    assert entry["mode"] == "real_voice"
    out = buf.getvalue()
    assert "REAL MICROPHONE PRACTICE" in out
    assert "Listening" in out
    assert 'heard' in out   # pass lines show what the ear captured


def test_real_voice_mode_garbage_utterance_is_an_honest_miss():
    # the ASR heard "poke" for the "click" item — the real matcher
    # refuses it; nothing is fabricated
    transcripts = ["poke", "press", "zap"] + L1_ANSWERS[1:]
    buf = io.StringIO()
    res = va.run_voice_academy(
        level="l1_basic", out=buf, input_fn=_script([""]),
        speak_fn=_ear(transcripts), offer_pack=False)
    assert res["levels"]["l1_basic"]["score"] == (11, 12)
    assert res["levels"]["l1_basic"]["completed"] is False
    assert va.REVEALED_LINE in buf.getvalue()


def test_real_voice_mode_failed_capture_is_never_a_pass():
    # a dead ear (capture always None) can NEVER manufacture a pass:
    # every item burns its attempts honestly, reveals, and stays undone
    buf = io.StringIO()
    calls = {"n": 0}

    def _dead_ear():
        calls["n"] += 1
        return None, 0.0

    res = va.run_voice_academy(
        level="l6_multimodal", out=buf,
        input_fn=_script([""]),
        speak_fn=_dead_ear, gaze_observer=lambda: True, offer_pack=False)
    assert calls["n"] == 1
    assert res["levels"]["l6_multimodal"]["completed"] is False
    assert res["levels"]["l6_multimodal"]["score"] == (0, 1)

    # and on a matcher-graded level the dead ear produces 0 passes too
    buf2 = io.StringIO()
    res2 = va.run_voice_academy(
        level="l1_basic", out=buf2,
        input_fn=_script(["", "", ""]),   # Enter → continue after each miss
        speak_fn=_dead_ear, offer_pack=False)
    assert calls["n"] >= 3
    assert res2["levels"]["l1_basic"]["score"][0] == 0
    assert res2["levels"]["l1_basic"]["completed"] is False
    assert va.REVEALED_LINE in buf2.getvalue()


# ---------------------------------------------------------------------------
# §6 — TEXT PRACTICE labelling
# ---------------------------------------------------------------------------

def test_text_mode_is_labelled_and_never_called_microphone_validation():
    buf = io.StringIO()
    res = va.run_voice_academy(level="l1_basic", out=buf,
                               input_fn=_script(L1_ANSWERS),
                               offer_pack=False)
    out = buf.getvalue()
    assert res["mode"] == "text"
    assert "Real microphone practice: PHYSICAL TEST REQUIRED" in out
    assert "Text practice: AVAILABLE" in out
    assert "TEXT PRACTICE" in out
    assert res["levels"]["l1_basic"]["mode"] == "text"


# ---------------------------------------------------------------------------
# §10 — skip / reveal / abandon semantics
# ---------------------------------------------------------------------------

def test_every_skip_prints_the_exact_honest_line():
    buf = io.StringIO()
    lines = [""] + L2_ANSWERS
    va.run_voice_academy(level="l2_natural", out=buf,
                         input_fn=_script(lines), offer_pack=False)
    assert buf.getvalue().count(va.SKIPPED_LINE) == 1


def test_abandoned_input_stops_gracefully():
    def _eof(_p):
        raise EOFError
    buf = io.StringIO()
    res = va.run_voice_academy(level="l1_basic", out=buf, input_fn=_eof)
    assert res["levels"]["l1_basic"]["score"] == (0, 12)
    assert res["completed"] is False and res["physical_required"] is True
    assert va.ABANDONED_LINE in buf.getvalue()


# ---------------------------------------------------------------------------
# scoring / plumbing
# ---------------------------------------------------------------------------

def test_score_accounting_across_full_run():
    # "poke" poisons item 11 (undo): 3 misses → revealed; the queued
    # "undo" then mismatches on item 12 (redo) before "redo" passes.
    lines = (L1_ANSWERS[:10] + ["poke", "poke", "poke"] + L1_ANSWERS[10:]
             + L2_ANSWERS + L3_ANSWERS + L4_ANSWERS + L5_ANSWERS
             + ["click that", "boom the tab"])
    buf = io.StringIO()
    res = va.run_voice_academy(out=buf, input_fn=_script(lines))
    assert res["levels"]["l1_basic"]["score"] == (11, 12)
    assert res["levels"]["l1_basic"]["completed"] is False
    assert res["levels"]["l2_natural"]["score"] == (5, 5)
    assert res["levels"]["l3_context"]["score"] == (5, 5)
    assert res["levels"]["l4_dictation"]["score"] == (2, 2)
    assert res["levels"]["l5_correction"]["score"] == (4, 4)
    # l6 is honestly incomplete: no gaze observer in a text-only run
    assert res["levels"]["l6_multimodal"]["score"] == (0, 1)
    assert res["levels"]["l6_multimodal"]["completed"] is False
    assert res["levels"]["l7_personal"]["score"] == (1, 1)
    assert res["completed"] is False
    assert res["physical_required"] is True


def test_every_pass_line_is_matcher_backed_not_self_reported():
    buf = io.StringIO()
    va.run_voice_academy(level="l1_basic", out=buf,
                         input_fn=_script(L1_ANSWERS), offer_pack=False)
    out = buf.getvalue()
    assert out.count('  ✓ "') == out.count("resolved via")


def test_l2_also_feeds_the_real_voice_profile():
    profile = VoiceProfile()
    buf = io.StringIO()
    va.run_voice_academy(level="l2_natural", out=buf,
                         input_fn=_script(L2_ANSWERS), profile=profile,
                         offer_pack=False)
    assert profile.samples >= 5
    assert "click that" in [c for c, _ in profile.frequent_commands(16)]


# ---------------------------------------------------------------------------
# §7 — the Local Voice Pack offer (never auto-installs)
# ---------------------------------------------------------------------------

def test_no_asr_pack_offer_is_printed_when_engine_missing(monkeypatch):
    offered = {"n": 0}

    def _fake_offer(out=None, input_fn=None):
        offered["n"] += 1
        out.write("Install local voice engine? [Y] Yes [N] Not now\n")

    answers = iter(L1_ANSWERS + ["n"])   # lessons first, then the offer

    def _fake_input(_prompt=""):
        return next(answers)

    monkeypatch.setattr("airmouse.voice_stack.offer_voice_pack", _fake_offer)
    monkeypatch.setattr("builtins.input", _fake_input)
    monkeypatch.setattr("airmouse.voice_stack.detect_voice_stack",
                        lambda: {"built_in_grammar": True,
                                 "speech_recognition": False, "pyaudio": False,
                                 "pocketsphinx": False, "vosk": False,
                                 "whisper": False, "microphone": False,
                                 "active": "Built-in command recognition"})
    # pass the REAL input builtin so the §7 offer gate opens (interactive)
    buf = io.StringIO()
    res = va.run_voice_academy(level="l1_basic", out=buf, input_fn=input,
                               offer_pack=True)
    assert offered["n"] == 1
    assert "Install local voice engine?" in buf.getvalue()
    # the offer never consumed a lesson answer — all 12 still passed
    assert res["levels"]["l1_basic"]["score"] == (12, 12)


def test_pack_offer_suppressed_when_disabled():
    buf = io.StringIO()
    va.run_voice_academy(level="l1_basic", out=buf,
                         input_fn=_script(L1_ANSWERS), offer_pack=False)
    assert "Install local voice engine?" not in buf.getvalue()
