"""Tests for airmouse.nl_control (v9 natural-language control).

Deterministic + headless: pure parsing, injected timestamps for the
controller.  30+ cases covering every v9 pattern, magnitude words,
specificity ordering, the legacy fallback channel and dedup.
"""
from __future__ import annotations

import pytest

from airmouse.interfaces import FusionDecision, IntentType, ScreenTarget, ScreenTargetType
from airmouse.nl_control import (
    DEICTIC_REFS,
    LEGACY_FALLBACK_KEYWORDS,
    NLController,
    normalize_text,
    nlu_to_intent,
    parse_utterance,
    resolve_fallback,
)


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------

def test_normalize_lowercases_and_strips_punctuation():
    assert normalize_text("  Click THAT!!!  ") == "click that"


def test_normalize_keeps_digits():
    assert normalize_text("type 123, go!") == "type 123 go"


def test_normalize_collapses_whitespace():
    assert normalize_text("a\t\nb   c") == "a b c"


def test_normalize_none_safe():
    assert normalize_text(None) == ""


# ---------------------------------------------------------------------------
# click family
# ---------------------------------------------------------------------------

def test_click_that():
    r = parse_utterance("click that")
    assert r.is_command and r.intent is IntentType.CLICK
    assert r.target_ref == "that" and r.confidence == 0.9


def test_click_this_it_here_refs():
    for ref in ("this", "it", "here"):
        r = parse_utterance(f"click {ref}")
        assert r.intent is IntentType.CLICK and r.target_ref == ref
        assert r.confidence == 0.9


def test_bare_click_no_target():
    r = parse_utterance("click")
    assert r.intent is IntentType.CLICK and r.target_ref == ""
    assert r.confidence == 0.75 and r.is_command


def test_double_click_beats_click_specificity():
    r = parse_utterance("double click that")
    assert r.intent is IntentType.DOUBLE_CLICK
    assert r.target_ref == "that" and r.confidence == 0.9


def test_double_click_hyphenated():
    assert parse_utterance("double-click").intent is IntentType.DOUBLE_CLICK


def test_right_click():
    r = parse_utterance("right click it")
    assert r.intent is IntentType.RIGHT_CLICK and r.target_ref == "it"


def test_bare_right_click():
    r = parse_utterance("right click")
    assert r.intent is IntentType.RIGHT_CLICK and r.confidence == 0.75


# ---------------------------------------------------------------------------
# open / scroll / zoom
# ---------------------------------------------------------------------------

def test_open_with_and_without_ref():
    assert parse_utterance("open that").target_ref == "that"
    assert parse_utterance("open it").target_ref == "it"
    bare = parse_utterance("open")
    assert bare.intent is IntentType.OPEN and bare.confidence == 0.75


def test_scroll_defaults():
    assert parse_utterance("scroll up").params["amount"] == 3
    assert parse_utterance("scroll down").params["amount"] == -3


def test_scroll_magnitude_words():
    assert parse_utterance("scroll up a little").params["amount"] == 2
    assert parse_utterance("scroll down slightly").params["amount"] == -2
    assert parse_utterance("scroll up a lot").params["amount"] == 8
    assert parse_utterance("scroll down way").params["amount"] == -8


def test_scroll_twice_repeat_param():
    r = parse_utterance("scroll down twice")
    assert r.params["amount"] == -3 and r.params["repeat"] == 2


def test_zoom_in_out_ticks():
    zin = parse_utterance("zoom in")
    assert zin.intent is IntentType.ZOOM
    assert zin.params == {"direction": "in", "ticks": 3}
    zout = parse_utterance("zoom out")
    assert zout.params == {"direction": "out", "ticks": 3}


def test_zoom_a_lot():
    assert parse_utterance("zoom in a lot").params["ticks"] == 8
    assert parse_utterance("zoom out a lot").params["ticks"] == 8


# ---------------------------------------------------------------------------
# close / move / navigation / media
# ---------------------------------------------------------------------------

def test_close_this_window():
    r = parse_utterance("close this window")
    assert r.intent is IntentType.CLOSE
    assert r.params == {"what": "window"} and r.target_ref == "this"
    assert r.confidence == 0.9


def test_close_tab_and_dialog():
    assert parse_utterance("close the tab").params == {"what": "tab"}
    assert parse_utterance("close dialog").params == {"what": "dialog"}


def test_bare_close():
    r = parse_utterance("close")
    assert r.intent is IntentType.CLOSE and r.params == {}


def test_move_ref_to_direction():
    r = parse_utterance("move this to the left")
    assert r.intent is IntentType.MOVE
    assert r.params == {"direction": "left"} and r.target_ref == "this"
    assert r.confidence == 0.9


def test_move_bare_directions():
    for word in ("left", "right", "up", "down", "top", "bottom"):
        r = parse_utterance(f"move {word}")
        assert r.intent is IntentType.MOVE
        assert r.params == {"direction": word}


def test_go_back_forward():
    assert parse_utterance("go back").intent is IntentType.BACK
    assert parse_utterance("go forward").intent is IntentType.FORWARD


def test_select_play_pause():
    assert parse_utterance("select this").intent is IntentType.SELECT
    assert parse_utterance("select this").confidence == 0.9
    assert parse_utterance("play this").intent is IntentType.PLAY
    assert parse_utterance("pause").intent is IntentType.PAUSE


def test_repeat():
    r = parse_utterance("repeat that")
    assert r.intent is IntentType.REPEAT and r.target_ref == "that"
    assert parse_utterance("repeat").intent is IntentType.REPEAT
    assert parse_utterance("repeat the last action").intent is IntentType.REPEAT


# ---------------------------------------------------------------------------
# cancel / emergency / window management / clipboard / dictation
# ---------------------------------------------------------------------------

def test_cancel_family():
    for word in ("stop", "cancel", "nevermind", "never mind", "abort"):
        assert parse_utterance(word).intent is IntentType.CANCEL, word


def test_stop_everything_beats_stop():
    r = parse_utterance("stop everything")
    assert r.intent is IntentType.EMERGENCY_STOP


def test_emergency_stop():
    assert parse_utterance("emergency stop").intent is IntentType.EMERGENCY_STOP


def test_minimize_maximize_switch_window():
    assert parse_utterance("minimize").intent is IntentType.MINIMIZE
    assert parse_utterance("maximize").intent is IntentType.MAXIMIZE
    assert parse_utterance("switch window").intent is IntentType.SWITCH_WINDOW
    assert parse_utterance("next window").intent is IntentType.SWITCH_WINDOW


def test_copy_paste():
    assert parse_utterance("copy this").intent is IntentType.COPY
    r = parse_utterance("paste here")
    assert r.intent is IntentType.PASTE and r.target_ref == "here"


def test_type_captures_rest_of_string():
    r = parse_utterance("type hello world 123")
    assert r.intent is IntentType.TYPE
    assert r.params["text"] == "hello world 123"


def test_type_text_truncated_to_200():
    r = parse_utterance("type " + "x" * 500)
    assert len(r.params["text"]) == 200


def test_generic_commands_confidence_0_6():
    for utt in ("minimize", "maximize", "go back", "stop everything"):
        assert parse_utterance(utt).confidence == 0.6, utt
    # "pause" carries an optional target slot → bare form is 0.75
    assert parse_utterance("pause").confidence == 0.75


# ---------------------------------------------------------------------------
# fallback + no-match + robustness
# ---------------------------------------------------------------------------

def test_legacy_fallback_keyword():
    r = parse_utterance("please zoom in now")
    assert r.is_command is False
    assert r.intent is IntentType.NONE
    assert r.fallback_command == "zoom_in"
    assert r.confidence == 0.3


def test_resolve_fallback_pure():
    assert resolve_fallback("could you scroll up please") == "scroll_up"
    assert resolve_fallback("nothing useful here") == ""


def test_no_match_is_not_command():
    r = parse_utterance("gibberish widget frobnicate")
    assert r.is_command is False and r.fallback_command == ""
    assert r.confidence == 0.0


def test_empty_and_none_safe():
    for bad in ("", "   ", None):
        r = parse_utterance(bad)
        assert r.is_command is False and r.confidence == 0.0


def test_case_and_punctuation_robustness():
    r = parse_utterance("CLICK That!!!")
    assert r.intent is IntentType.CLICK and r.target_ref == "that"


def test_every_legacy_keyword_maps_to_string():
    for fragment, command in LEGACY_FALLBACK_KEYWORDS.items():
        assert isinstance(fragment, str) and isinstance(command, str)
        assert command == normalize_text(command).replace(" ", "_") or \
            command in {"play_pause", "prev_track", "back", "forward", "click",
                        "stop", "cancel"}


# ---------------------------------------------------------------------------
# NLController
# ---------------------------------------------------------------------------

def test_controller_feeds_and_dedup():
    c = NLController()
    first = c.feed("click that", timestamp=10.0)
    assert first is not None and first.is_command
    # identical consecutive utterance within the window → suppressed
    assert c.feed("click that", timestamp=10.5) is None
    # after the window it fires again
    again = c.feed("click that", timestamp=10.0 + 1.3)
    assert again is not None


def test_controller_dedup_window_is_1_2s():
    c = NLController()
    assert c.dedup_window == pytest.approx(1.2)
    assert c.feed("pause", timestamp=0.0) is not None
    assert c.feed("pause", timestamp=1.1) is None
    assert c.feed("pause", timestamp=1.3) is not None


def test_controller_dedup_different_text_fires():
    c = NLController()
    assert c.feed("click that", timestamp=0.0) is not None
    assert c.feed("click it", timestamp=0.1) is not None


def test_controller_empty_feed_returns_none():
    c = NLController()
    assert c.feed("", timestamp=0.0) is None
    assert c.feed(None, timestamp=0.0) is None


def test_controller_last_result_and_reset():
    c = NLController()
    assert c.last_result is None
    res = c.feed("minimize", timestamp=1.0)
    assert c.last_result is res
    c.feed("minimize", timestamp=1.1)  # deduped
    assert c.last_result is res        # last ACCEPTED result unchanged
    c.reset()
    assert c.last_result is None
    assert c.feed("minimize", timestamp=1.2) is not None


# ---------------------------------------------------------------------------
# nlu_to_intent (deictic resolution contract)
# ---------------------------------------------------------------------------

def _button():
    return ScreenTarget(id="b1", type=ScreenTargetType.BUTTON,
                        bbox=(900, 500, 120, 50), text="Submit button",
                        confidence=0.9, actionable=True)


def test_nlu_to_intent_resolves_deixis_from_decision():
    decision = FusionDecision(target=_button(), confidence=0.9)
    nlu = parse_utterance("click that")
    intent = nlu_to_intent(nlu, decision, "click that", now=5.0)
    assert intent is not None and intent.type is IntentType.CLICK
    assert intent.target is decision.target
    assert intent.point == (960.0, 525.0)   # the target center — never invented
    assert intent.confidence == 0.9


def test_nlu_to_intent_without_decision_invents_nothing():
    nlu = parse_utterance("click that")
    intent = nlu_to_intent(nlu, None, "click that", now=5.0)
    assert intent.target is None and intent.point is None


def test_nlu_to_intent_non_command_returns_none():
    nlu = parse_utterance("gibberish")
    assert nlu_to_intent(nlu, FusionDecision(), "gibberish", now=0.0) is None


def test_nlu_to_intent_emergency_boosts_confidence():
    nlu = parse_utterance("stop everything")
    intent = nlu_to_intent(nlu, FusionDecision(), "stop everything", now=0.0)
    assert intent.type is IntentType.EMERGENCY_STOP
    assert intent.confidence == 1.0


def test_deictic_refs_constant():
    assert set(DEICTIC_REFS) == {"this", "that", "it", "here", "there"}
