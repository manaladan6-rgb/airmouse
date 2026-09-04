"""Startup wiring regression tests (added v15.1.1).

v15.1.0 shipped with two startup crashes that 1312 green tests never
executed, because no test ran main():

  1. `airmouse --voice` -> UnboundLocalError (voice_engine10 read at
     __main__.py:1214 before its assignment at :1234).
  2. `--fusion --gaze` -> UnboundLocalError (gesture ownership gate read
     `gesture` before assignment) AND a silently-inert ownership gate that
     allowed double-control in hands-free modes.

Rule established after that release: every documented startup flag
combination must be exercised against the REAL main() wiring, with only
the camera/tracker hardware stubbed (headless-safe). A startup wiring
regression must never ship green again.

Each test drives main() through its genuine startup path. The stub
tracker returns contract-correct empty results for N frames, then raises
_StartupProbeDone. The test asserts _StartupProbeDone is the ONLY
exception that escapes — any other exception (UnboundLocalError,
ImportError at wiring time, TypeError in constructor wiring, ...) is a
startup defect.
"""

import os
import sys

import pytest

import airmouse.__main__ as am

# Legacy v15.1.0-era module paths used by some configurations via
# `import airmouse...` inside functions; ensure they resolve headless.


class _StartupProbeDone(RuntimeError):
    """Raised by the stub tracker to end the frame loop deterministically."""


class _StubTracker:
    """Contract-correct HandTracker stub: no hand, no frame, then exit."""

    PROBE_FRAMES = 12

    def __init__(self, camera_index=0, detection_confidence=0.5,
                 tracking_confidence=0.5):
        self._frames = 0
        self.camera_index = camera_index

    def read(self):
        self._frames += 1
        if self._frames > self.PROBE_FRAMES:
            raise _StartupProbeDone(
                "startup probe complete — frame loop was reached and ran")
        # Exact contract of HandTracker._empty_result() (tracker.py:112)
        return {
            "hand_found": False,
            "landmarks": None,
            "index_pos": None,
            "pinch_distance": 1.0,
            "frame": None,
        }

    def release(self):
        pass


def _run_startup(monkeypatch, tmp_path, extra_flags, expect_stdout=None):
    """Drive the real main() with stubbed hardware; return captured output.

    Fails on ANY exception other than _StartupProbeDone — that is the
    whole point: startup wiring defects must fail loudly here.
    """
    home = tmp_path / "am_startup_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("AIRMOUSE_HOME", str(home))
    monkeypatch.setattr(am, "HandTracker", _StubTracker)
    monkeypatch.setattr(sys, "argv", ["airmock"] + list(extra_flags))

    captured = {"out": ""}

    class _Raiser:
        pass

    with pytest.raises(_StartupProbeDone):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            am.main()
        captured["out"] = buf.getvalue()
    captured["out"] = captured["out"]  # buf content only on success path
    return captured


# The flag matrix from the endgame spec §32. `--skip` avoids the camera
# tutorial, `--no-sound` avoids audio-device probing, `--no-cam` avoids
# the HUD window; the tracker stub removes the only hardware dependency.
_FLAG_MATRIX = [
    ["--skip", "--no-cam", "--no-sound"],
    ["--skip", "--no-cam", "--no-sound", "--voice"],
    ["--skip", "--no-cam", "--no-sound", "--gaze"],
    ["--skip", "--no-cam", "--no-sound", "--fusion"],
    ["--skip", "--no-cam", "--no-sound", "--voice", "--fusion"],
    ["--skip", "--no-cam", "--no-sound", "--gaze", "--fusion"],
    ["--skip", "--no-cam", "--no-sound", "--hands-free"],
    ["--skip", "--no-cam", "--no-sound", "--voice-mode", "command"],
]


@pytest.mark.parametrize("flags", _FLAG_MATRIX,
                         ids=["bare", "voice", "gaze", "fusion",
                              "voice+fusion", "gaze+fusion", "hands-free",
                              "v10-voice"])
def test_startup_reaches_frame_loop(monkeypatch, tmp_path, flags):
    """Every documented startup combo must reach the frame loop.

    Regression guard for the two v15.1.0 startup crashes:
    - 'voice'     would die with UnboundLocalError (voice_engine10).
    - 'gaze+fusion' would die with UnboundLocalError (ownership gate).
    """
    _run_startup(monkeypatch, tmp_path, flags)


def test_voice_startup_actually_initializes_voice(monkeypatch, tmp_path):
    """P0-1 regression, part 2: `--voice` must reach the voice subsystem.

    Before the fix main() died before printing any voice banner. After the
    fix the voice section runs: with no SpeechRecognition installed the
    engine degrades honestly (unavailable notice), with it installed the
    VOICE ONLINE banner prints. Either way a voice-related banner MUST
    exist — silence means the voice block never executed.
    """
    home = tmp_path / "am_voice_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("AIRMOUSE_HOME", str(home))
    monkeypatch.setattr(am, "HandTracker", _StubTracker)
    monkeypatch.setattr(sys, "argv",
                        ["airmock", "--skip", "--no-cam", "--no-sound",
                         "--voice"])
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            am.main()
        except _StartupProbeDone:
            pass
    out = buf.getvalue()
    assert ("VOICE" in out.upper()), (
        "voice subsystem banner missing — voice block did not execute")


def test_v10_voice_owns_speech_when_active(monkeypatch, tmp_path):
    """Ownership invariant: when the v10 offline engine activates, a RUNNING
    v5 cloud engine is stopped (exactly one voice owner)."""
    home = tmp_path / "am_v10_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("AIRMOUSE_HOME", str(home))

    stopped = {"count": 0}

    class _FakeRunningV5:
        """V5 engine stub that IS available, so the handoff branch runs."""

        def __init__(self, **kw):
            self.available = True

        def is_available(self):
            return True

        def start(self):
            pass

        def stop(self):
            stopped["count"] += 1

        def poll(self):
            return None  # never fires commands during the probe

    monkeypatch.setattr(am, "HandTracker", _StubTracker)
    monkeypatch.setattr(am, "VoiceCommandEngine", _FakeRunningV5)
    monkeypatch.setattr(sys, "argv",
                        ["airmock", "--skip", "--no-cam", "--no-sound",
                         "--voice", "--voice-mode", "command"])
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            am.main()
        except _StartupProbeDone:
            pass
    out = buf.getvalue()
    assert "V10 OFFLINE VOICE ONLINE" in out
    # v5 engine was constructed, started, then replaced by v10: stopped once.
    assert "v5 cloud voice stopped" in out
    assert stopped["count"] == 1


def test_startup_probe_is_deterministic(monkeypatch, tmp_path):
    """The probe must end via the sentinel (loop ran), never via a crash
    masquerading as success. Run the bare config twice for stability."""
    for _ in range(2):
        _run_startup(monkeypatch, tmp_path,
                     ["--skip", "--no-cam", "--no-sound"])
