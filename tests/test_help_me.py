"""v16.5 — help_registry tests: contextual help answers from REAL data.

The answers must match the shipped grammar (voice_control.COMMANDS) and
the shipped gesture map (gestures.py header + gesture_spine policy).
"""

import io

from airmouse import help_registry as hr


# ---------------------------------------------------------------------------
# mission §20 — the five required questions
# ---------------------------------------------------------------------------

def test_what_can_i_do():
    text = hr.answer("what can I do?")
    assert "HANDS" in text and "VOICE" in text and "EYES" in text
    assert "airmouse teach" in text
    assert "nothing is uploaded" in text.lower()


def test_how_do_i_scroll():
    text = hr.answer("how do I scroll?")
    assert "pinch" in text.lower() and "swipe" in text.lower()
    assert "scroll up" in text.lower()


def test_teach_me_this():
    text = hr.answer("teach me this")
    assert "airmouse teach" in text.lower()
    assert "airmouse learn" in text.lower()


def test_what_gesture_should_i_use():
    text = hr.answer("what gesture should I use for right click?")
    assert "peace" in text.lower()
    assert "right click" in text.lower()


def test_why_didnt_that_work():
    text = hr.answer("why didn't that work?")
    # honest gating explanation: e-stop, confidence, policy, rate limit
    assert "e-stop" in text.lower() or "freeze" in text.lower()
    assert "confidence" in text.lower()
    assert "destructive" in text.lower()


# ---------------------------------------------------------------------------
# truthfulness against the shipped code
# ---------------------------------------------------------------------------

def test_gesture_map_matches_gestures_py():
    """Cross-check core pose→effect rows against the gestures.py header
    facts (module docstring) and the spine's risk classes."""
    rows = {g: effect for g, _h, effect, _n in hr.GESTURE_HELP}
    assert "left click" in rows["pinch"]
    assert "right click" in rows["peace"]
    assert "double click" in rows["thumbs_up"]
    assert "drag" in rows["palm"].lower()
    assert "freeze" in rows["fist"].lower()
    # destructive honesty: ok → close window, flagged refused by default
    assert "close window" in rows["ok"].lower()
    ok_note = [n for g, _h, _e, n in hr.GESTURE_HELP if g == "ok"][0]
    assert "refused by default" in ok_note.lower()

    from airmouse.gesture_spine import RISK_CLASSES
    assert RISK_CLASSES["close_window"] == "DESTRUCTIVE"


def test_gesture_map_motion_rows():
    rows = {g for g, _h, _e, _n in hr.GESTURE_HELP}
    for g in ("swipe_left / swipe_right", "swipe_up / swipe_down",
              "circle_cw / circle_ccw", "push / pull", "shake", "wave",
              "four / five"):
        assert g in rows
    # shake/wave are honestly recognized-without-default-action
    shake_note = [n for g, _h, _e, n in hr.GESTURE_HELP if g == "shake"][0]
    assert "registry" in shake_note.lower()


def test_voice_help_matches_real_grammar():
    from airmouse.voice_control import COMMANDS
    assert len(hr._voice_rows()) == len(COMMANDS)
    # a sample of canonical commands appear
    canon = {c for c, _p in hr._voice_rows()}
    for name in ("click", "right_click", "scroll_up", "scroll_down"):
        assert name in canon


def test_voice_help_honesty_note():
    # deterministic grammar is NOT full ASR — the note must say so
    assert "NOT full speech recognition" in hr.VOICE_HELP_NOTE


def test_two_hand_honesty():
    assert "NOT MAPPED" in hr.TWO_HAND_HELP
    assert "zoom" in hr.TWO_HAND_HELP.lower()


# ---------------------------------------------------------------------------
# fallback + rendering
# ---------------------------------------------------------------------------

def test_unknown_question_honest_fallback():
    text = hr.answer("what is the airspeed velocity of an unladen swallow")
    assert "I can help with" in text
    assert "how do I scroll" in text


def test_gesture_answer_keyword_variants():
    click_answer = hr.answer("what gesture should I use to click?").lower()
    assert "pinch" in click_answer
    drag_answer = hr.answer("which gesture for drag?").lower()
    assert "palm" in drag_answer


def test_help_me_panel_renders_sections():
    text = hr.help_me_panel()
    assert "HANDS (camera)" in text
    assert "VOICE (offline grammar)" in text
    assert "EYES (webcam gaze)" in text
    assert "airmouse doctor" in text
    assert "airmouse learn" in text


def test_run_help_me_rc_and_output():
    buf = io.StringIO()
    rc = hr.run_help_me("how do I zoom?", out=buf)
    assert rc == 0
    assert "zoom" in buf.getvalue().lower()
    buf2 = io.StringIO()
    rc2 = hr.run_help_me("", out=buf2)
    assert rc2 == 0
    assert "AIRMouse — what you can do" in buf2.getvalue()
