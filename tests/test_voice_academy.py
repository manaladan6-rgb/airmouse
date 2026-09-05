"""Tests for airmouse.voice_academy — 4 levels, every PASS matcher-verified.

The grader under test is the REAL chain (voice_commands grammar → v9 NL
pattern table → v8 phrase table) plus the REAL VoiceTypingEngine and
VoiceProfile — no test doubles sit in the verification path.
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

L2_ANSWERS = ["click that", "scroll down", "open my browser", "close this",
              "copy that", "move left"]

L3_ANSWERS = ["hello bro comma how are you question mark", "scratch that",
              "undo", "replace that with the report is final"]


def _script(lines):
    it = iter(lines)
    return lambda _p: next(it)


# ---------------------------------------------------------------------------
# curriculum data
# ---------------------------------------------------------------------------

def test_voice_lessons_structure_is_exportable():
    assert [l["id"] for l in va.VOICE_LESSONS] == \
        ["l1_basic", "l2_natural", "l3_dictation", "l4_personal"]
    for lesson in va.VOICE_LESSONS:
        assert lesson["title"].strip()
        assert lesson["instruction"].strip()
        assert lesson["practice"]
        assert lesson["verified_by"].strip()


# ---------------------------------------------------------------------------
# concept mode
# ---------------------------------------------------------------------------

def test_concept_mode_never_completes_and_requires_physical():
    buf = io.StringIO()
    res = va.run_voice_academy(out=buf, input_fn=None)
    assert res["completed"] is False
    assert res["physical_required"] is True
    assert set(res["levels"]) == {"l1_basic", "l2_natural", "l3_dictation",
                                  "l4_personal"}
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
    res = va.run_voice_academy(level="l3_dictation", out=buf, input_fn=None)
    assert res["physical_required"] is True
    assert set(res["levels"]) == {"l3_dictation"}
    out = buf.getvalue()
    for lid in ("l1_basic", "l2_natural", "l3_dictation", "l4_personal"):
        assert lid in out


def test_unknown_level_is_honest():
    buf = io.StringIO()
    res = va.run_voice_academy(level="nope", out=buf, input_fn=_script(["x"]))
    assert res["completed"] is False
    assert res["levels"] == {}
    assert "l1_basic" in buf.getvalue() and "l4_personal" in buf.getvalue()


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


def test_resolve_voice_natural_phrases():
    assert va.resolve_voice("click that").intent == IntentType.CLICK
    assert va.resolve_voice("open my browser").intent == IntentType.OPEN
    assert va.resolve_voice("close this").intent == IntentType.CLOSE
    assert va.resolve_voice("copy that").intent == IntentType.COPY
    move = va.resolve_voice("move left")           # real NL-layer form
    assert move.ok and move.intent == IntentType.MOVE
    assert move.source == "nl"


def test_resolve_voice_honest_about_grammar_gaps():
    for miss in ("move over there", "gibberish wibble nonsense",
                 "do the thing please"):
        r = va.resolve_voice(miss)
        assert r.ok is False, miss
        assert r.intent is None


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
    assert out.count("✓") == 12
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


def test_l1_reveals_after_retries_exhausted():
    buf = io.StringIO()
    # "poke"/"press"/"zap" genuinely do NOT resolve in the real grammar
    # (verified against resolve_voice) — "tap" would, it's a click synonym
    lines = ["poke", "press", "zap"] + L1_ANSWERS[1:]
    res = va.run_voice_academy(level="l1_basic", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l1_basic"]["score"] == (11, 12)
    assert res["levels"]["l1_basic"]["completed"] is False
    assert res["completed"] is False and res["physical_required"] is True
    assert "The answer:" in buf.getvalue()


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


# ---------------------------------------------------------------------------
# level 2 — natural language
# ---------------------------------------------------------------------------

def test_l2_all_pass_and_shows_the_honest_grammar_limit():
    buf = io.StringIO()
    res = va.run_voice_academy(level="l2_natural", out=buf,
                               input_fn=_script(L2_ANSWERS))
    entry = res["levels"]["l2_natural"]
    assert entry["completed"] is True
    assert entry["score"] == (6, 6)
    out = buf.getvalue()
    assert "NOT in the deterministic grammar" in out   # "move over there"
    assert "move left" in out
    assert out.count("✓") == 6


def test_l2_move_over_there_fails_then_real_form_passes():
    buf = io.StringIO()
    lines = L2_ANSWERS[:5] + ["move over there", "move over there",
                              "move left"]
    res = va.run_voice_academy(level="l2_natural", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l2_natural"]["score"] == (6, 6)
    out = buf.getvalue()
    assert '✗ "move over there"' in out
    assert "The answer:" not in out


def test_l2_wrong_slot_word_does_not_pass():
    # "copy that" resolves to COPY, not CLICK — honest mismatch feedback
    buf = io.StringIO()
    lines = ["copy that", "click that"] + L2_ANSWERS[1:]
    res = va.run_voice_academy(level="l2_natural", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l2_natural"]["score"] == (6, 6)
    assert "expected intent: click" in buf.getvalue()


# ---------------------------------------------------------------------------
# level 3 — dictation
# ---------------------------------------------------------------------------

def test_l3_formatters_exact_output():
    spoken = "Hello bro comma how are you question mark"
    assert apply_spoken_punctuation(spoken) == "Hello bro, how are you?"
    assert capitalize_text(apply_spoken_punctuation(spoken)) == \
        "Hello bro, how are you?"
    assert va.format_dictation(spoken) == "Hello bro, how are you?"


def test_l3_all_pass_through_real_voice_typing_engine():
    buf = io.StringIO()
    res = va.run_voice_academy(level="l3_dictation", out=buf,
                               input_fn=_script(L3_ANSWERS))
    entry = res["levels"]["l3_dictation"]
    assert entry["completed"] is True
    assert entry["score"] == (4, 4)
    out = buf.getvalue()
    assert 'dictation buffer: "Hello bro, how are you?"' in out
    assert "(seed dictated: \"the report is ready\")" in out


def test_l3_wrong_dictation_gets_feedback_then_passes():
    buf = io.StringIO()
    lines = ["hello world"] + L3_ANSWERS
    res = va.run_voice_academy(level="l3_dictation", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l3_dictation"]["score"] == (4, 4)
    out = buf.getvalue()
    assert "✗" in out and "not the expected result" in out
    assert "The answer:" not in out


def test_l3_reveals_when_correction_unknown():
    buf = io.StringIO()
    lines = L3_ANSWERS[:2] + ["erase it all", "wipe it", "clear it"] \
        + L3_ANSWERS[3:]
    res = va.run_voice_academy(level="l3_dictation", out=buf,
                               input_fn=_script(lines))
    assert res["levels"]["l3_dictation"]["score"] == (3, 4)
    assert "The answer: \"undo\"" in buf.getvalue()


# ---------------------------------------------------------------------------
# level 4 — personal voice learning
# ---------------------------------------------------------------------------

def test_l4_alias_learning_is_real_and_local_only():
    profile = VoiceProfile()
    buf = io.StringIO()
    res = va.run_voice_academy(level="l4_personal", out=buf,
                               input_fn=_script(["kill the tab"]),
                               profile=profile)
    assert res["levels"]["l4_personal"]["completed"] is True
    assert res["levels"]["l4_personal"]["score"] == (1, 1)
    # the REAL profile learned both the demo and the practice alias
    assert profile.resolve_alias("launch browser") == "open browser"
    assert profile.resolve_alias("kill the tab") == "close tab"
    out = buf.getvalue()
    assert "Learned locally. Nothing is uploaded." in out
    assert "airmouse memory reset" in out
    assert "5 observations" in out or "5 consistent observations" in out


def test_l4_rejects_the_canonical_itself():
    profile = VoiceProfile()
    buf = io.StringIO()
    lines = ["close tab", "Close Tab", "kill the tab"]
    res = va.run_voice_academy(level="l4_personal", out=buf,
                               input_fn=_script(lines), profile=profile)
    assert res["levels"]["l4_personal"]["score"] == (1, 1)
    assert "already the canonical command" in buf.getvalue()
    assert profile.resolve_alias("close tab") is None


def test_l4_empty_alias_fails_then_example_revealed():
    profile = VoiceProfile()
    buf = io.StringIO()
    lines = ["", "   ", ""]
    res = va.run_voice_academy(level="l4_personal", out=buf,
                               input_fn=_script(lines), profile=profile)
    assert res["levels"]["l4_personal"]["score"] == (0, 1)
    assert "did not learn" in buf.getvalue()
    assert "The answer:" in buf.getvalue()
    assert "kill the tab" in buf.getvalue()
    assert profile.aliases().get("launch browser") == "open browser"


# ---------------------------------------------------------------------------
# scoring / plumbing
# ---------------------------------------------------------------------------

def test_score_accounting_across_full_run():
    lines = (L1_ANSWERS[:10] + ["poke", "poke", "poke"] + L1_ANSWERS[10:]
             + L2_ANSWERS
             + L3_ANSWERS
             + ["boom the tab"])
    buf = io.StringIO()
    res = va.run_voice_academy(out=buf, input_fn=_script(lines))
    # item 11 (undo) is revealed after 3 misses; "undo" then lands on item
    # 12 (redo) as a mismatched attempt before "redo" passes on retry
    assert res["levels"]["l1_basic"]["score"] == (11, 12)
    assert res["levels"]["l1_basic"]["completed"] is False
    assert res["levels"]["l2_natural"]["score"] == (6, 6)
    assert res["levels"]["l3_dictation"]["score"] == (4, 4)
    assert res["levels"]["l4_personal"]["score"] == (1, 1)
    assert res["completed"] is False
    assert res["physical_required"] is True


def test_every_pass_line_is_matcher_backed_not_self_reported():
    buf = io.StringIO()
    va.run_voice_academy(level="l1_basic", out=buf,
                         input_fn=_script(L1_ANSWERS))
    out = buf.getvalue()
    assert out.count("✓") == out.count("— resolved via")


def test_abandoned_input_stops_gracefully():
    def _eof(_p):
        raise EOFError
    buf = io.StringIO()
    res = va.run_voice_academy(level="l1_basic", out=buf, input_fn=_eof)
    assert res["levels"]["l1_basic"]["score"] == (0, 12)
    assert res["completed"] is False and res["physical_required"] is True
    assert "(practice abandoned)" in buf.getvalue()


def test_l2_also_feeds_the_real_voice_profile():
    profile = VoiceProfile()
    buf = io.StringIO()
    va.run_voice_academy(level="l2_natural", out=buf,
                         input_fn=_script(L2_ANSWERS), profile=profile)
    assert profile.samples >= 6
    assert "click that" in [c for c, _ in profile.frequent_commands(16)]
