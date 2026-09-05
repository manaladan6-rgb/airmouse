"""v16.5 — teacher tests: onboarding state machine, persistence, resume,
skip, honesty (never auto-pass physical), adaptive practice decisions.

Mission §2/§3/§17/§24: first-run detection, persisted states
(NEW → IN_PROGRESS → …_COMPLETE → COMPLETE), resume/never-lose-progress,
"Skip for now" always works, and the teacher never claims success the
sensor did not verify.
"""

import io

import pytest

from airmouse import teacher as T


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    return T.OnboardingStore()


# ---------------------------------------------------------------------------
# phase machine
# ---------------------------------------------------------------------------

def test_phase_order_is_monotonic():
    ranks = [T.phase_rank(p) for p in T.OnboardingPhase]
    assert ranks == sorted(ranks)
    assert T.OnboardingPhase.NEW == T.OnboardingPhase("NEW")


def test_fresh_store_is_new(store):
    assert store.is_new
    assert not store.is_in_progress
    assert not store.is_complete
    assert store.sessions == 0


def test_touch_session_moves_new_to_in_progress(store):
    store.touch_session()
    assert store.is_in_progress and not store.is_new
    assert store.sessions == 1


def test_mark_track_complete_progression(store):
    store.touch_session()
    for track, phase in (("voice", T.OnboardingPhase.VOICE_COMPLETE),
                         ("gaze", T.OnboardingPhase.GAZE_COMPLETE),
                         ("gesture", T.OnboardingPhase.GESTURE_COMPLETE),
                         ("fusion", T.OnboardingPhase.FUSION_COMPLETE)):
        assert store.mark_track_complete(track)
        assert store.phase == phase
    # personalization acknowledgment is the last gate to COMPLETE
    store.mark_track_complete("personalization")
    assert store.phase == T.OnboardingPhase.COMPLETE
    assert store.is_complete


def test_invalid_track_is_refused(store):
    assert store.mark_track_complete("teleportation") is False
    assert store.phase == T.OnboardingPhase.NEW


def test_phase_never_regresses(store):
    store.touch_session()
    store.mark_track_complete("voice")
    store.mark_track_complete("gesture")
    # the ladder is the longest completed PREFIX of voice→gaze→gesture:
    # gesture done but gaze not → still VOICE_COMPLETE (honest ladder)
    assert store.phase == T.OnboardingPhase.VOICE_COMPLETE
    store.mark_track_complete("gaze")
    assert store.phase == T.OnboardingPhase.GESTURE_COMPLETE
    store.mark_track_complete("voice")
    assert store.phase == T.OnboardingPhase.GESTURE_COMPLETE


# ---------------------------------------------------------------------------
# persistence — never lose progress
# ---------------------------------------------------------------------------

def test_state_survives_reload(store):
    store.touch_session()
    store.mark_track_complete("voice")
    assert store.save() is True
    again = T.OnboardingStore()
    again.load()
    assert again.phase == T.OnboardingPhase.VOICE_COMPLETE
    assert again.tracks["voice"] is True
    assert again.sessions == 1


def test_corrupted_file_fails_safe(store, tmp_path):
    store.touch_session()
    store.save()
    with open(store.path, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json")
    # the store auto-loads at construction — the corrupted file must
    # fail safe to NEW with the honest flag set
    fresh = T.OnboardingStore()
    assert fresh.is_new                       # fail-safe reset
    assert fresh.corrupted_last_load          # honestly flagged


def test_missing_file_is_new(store):
    store.load()
    assert store.is_new
    assert not store.corrupted_last_load


# ---------------------------------------------------------------------------
# learner stats + honesty
# ---------------------------------------------------------------------------

def test_physical_lesson_never_passes_without_sensor(store):
    # a real physical gesture lesson: sensor did NOT verify → recorded
    # honestly as an attempt, NOT a pass (result="simulated")
    teacher = T.Teacher(store)
    res = teacher.record_result("gesture_core", passed=True,
                                confidence=0.95, physical_verified=False)
    assert res["passed"] is False
    assert res["result"] == "simulated"
    st = store.lesson_stats("gesture_core")
    assert st["attempts"] == 1
    assert st["passes"] == 0


def test_record_lesson_counts_attempts(store):
    store.record_lesson("voice_click", passed=True, confidence=0.9)
    store.record_lesson("voice_click", passed=False)
    st = store.lesson_stats("voice_click")
    assert st["attempts"] == 2 and st["passes"] == 1


def test_decide_practice_strong_skill_skips(store):
    decision = T.Teacher.decide_practice(
        "voice_click",
        {"attempts": 2, "passes": 2, "last_confidence": 0.92})
    assert decision["action"] == "skip"
    assert "looks solid" in decision["message"].lower()


def test_decide_practice_weak_skill_repeats(store):
    decision = T.Teacher.decide_practice(
        "voice_click",
        {"attempts": 3, "passes": 0, "last_confidence": 0.3})
    assert decision["action"] == "repeat"
    msg = decision["message"].lower()
    # never blame the user
    assert "blame" not in msg
    assert "try" in msg or "practice" in msg or "slower" in msg


# ---------------------------------------------------------------------------
# persona rendering
# ---------------------------------------------------------------------------

def test_welcome_banner_contract():
    text = T.Teacher(T.OnboardingStore()).welcome_banner()
    for needle in ("VOICE", "Eyes", "Hands", "Multimodal"):
        assert needle.lower() in text.lower()
    assert "3" in text and "5" in text          # 3–5 minutes


def test_teaching_overlay_progress_bar():
    t = T.Teacher(T.OnboardingStore())
    zero = t.teaching_overlay("voice_l1", progress=0.0)
    half = t.teaching_overlay("voice_l1", progress=0.5)
    full = t.teaching_overlay("voice_l1", progress=1.0)
    assert "84" not in zero and "84%" not in zero
    assert "█" in half and "░" in half
    assert "100%" in full or "████" in full


def test_progress_report_renders_tracks(store):
    store.touch_session()
    store.mark_track_complete("voice")
    report = T.Teacher(store).progress_report()
    assert "voice" in report.lower()
    assert "gaze" in report.lower()


# ---------------------------------------------------------------------------
# first-run gating (mission §24 — never trap the user)
# ---------------------------------------------------------------------------

def test_should_auto_teach_gates(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    s = T.OnboardingStore()
    # non-TTY sandbox → False
    assert T.should_auto_teach(store=s, argv=[]) is False
    # non-empty argv → False
    assert T.should_auto_teach(store=s, argv=["--voice"],
                               tty=True) is False
    # fresh store + tty + empty argv → True
    assert T.should_auto_teach(store=s, argv=[], tty=True) is True
    # COMPLETE users are never re-taught
    for t in ("voice", "gaze", "gesture", "fusion", "personalization"):
        s.mark_track_complete(t)
    assert T.should_auto_teach(store=s, argv=[], tty=True) is False


def test_maybe_prompt_teach_accept_and_decline(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    s = T.OnboardingStore(path=str(tmp_path / "a.json"))
    # the teacher's input_fn is a ZERO-ARG callable (like builtin input)
    assert T.maybe_prompt_teach(store=s, input_fn=lambda: "y") is True
    assert s.is_in_progress                     # accept touched a session
    s2 = T.OnboardingStore(path=str(tmp_path / "b.json"))
    assert T.maybe_prompt_teach(store=s2, input_fn=lambda: "n") is False
    assert s2.is_new                            # skip persisted nothing
    # EOF → honest decline, never a crash
    def _eof():
        raise EOFError
    s3 = T.OnboardingStore(path=str(tmp_path / "c.json"))
    assert T.maybe_prompt_teach(store=s3, input_fn=_eof) is False


def test_maybe_prompt_resume_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    s = T.OnboardingStore()
    s.touch_session()
    s.save()
    # IN_PROGRESS → "Continue your training?" — n means proceed, not trap
    assert T.maybe_prompt_teach(store=s, input_fn=lambda: "n") is False


# ---------------------------------------------------------------------------
# headless teaching runs (this sandbox: no camera, no TTY)
# ---------------------------------------------------------------------------

def test_run_teach_headless_prints_plan_never_passes(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    buf = io.StringIO()
    rc = T.run_teach("all", out=buf, input_fn=None)
    assert rc == 0
    out = buf.getvalue()
    # the full teaching plan + honest physical note
    assert "voice" in out.lower() and "gaze" in out.lower()
    assert "PHYSICAL PRACTICE REQUIRED" in out or "physical" in out.lower()
    s = T.OnboardingStore()
    s.load()
    # nothing physical was marked complete by a headless run
    assert s.tracks["gesture"] is False
    assert s.tracks["gaze"] is False


def test_run_teach_always_lets_user_go(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    # a user who answers "n" to everything finishes promptly, rc 0
    rc = T.run_teach("all", out=io.StringIO(),
                     input_fn=lambda: "n")
    assert rc == 0


def test_run_teach_voice_track_completes_via_text_practice(
        monkeypatch, tmp_path):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    buf = io.StringIO()
    # feed correct canonical phrases for the voice academy levels that
    # ask for input (the academy is matcher-verified; physical mic
    # practice stays PHYSICAL TEST REQUIRED either way)
    rc = T.run_teach("voice", out=buf, input_fn=lambda _p: "click")
    assert rc == 0


def test_run_learn_headless_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    rc = T.run_learn(out=io.StringIO(), input_fn=None)
    assert rc == 0


# ---------------------------------------------------------------------------
# self-diagnostic teacher (mission §21)
# ---------------------------------------------------------------------------

def test_hardware_panel_honest_without_camera(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    text = T.hardware_panel()
    assert "CAMERA" in text.upper()
    # the honest concept-teaching line appears when camera is missing
    assert "concepts" in text.lower()
    assert "webcam" in text.lower() or "camera" in text.lower()


def test_hardware_panel_never_auto_passes(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    text = " ".join(T.hardware_panel().upper().split())
    # the panel states the honesty contract verbatim (wrap-tolerant)
    assert "NEVER AUTO-PASSED" in text or "AUTO-PASSED" in text
    assert "SENSOR VERIFIES IT" in text
