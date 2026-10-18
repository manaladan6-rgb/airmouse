"""tests/test_final_form_tracker.py — TF-001 final-form tracker contract.

Task R3a.  At HEAD, ``HandTracker()`` died with
``NameError: name 'mp' is not defined``: the PEP 562 module ``__getattr__``
lazy binding satisfies *outside* attribute fetches but can never satisfy a
bare ``LOAD_GLOBAL`` inside the module's own functions.

Laws under test:
  * ``mp`` is a REAL module global (delegating ``_LazyMp`` proxy) — bare
    LOAD_GLOBAL resolves; first touch imports mediapipe and rebinds the
    global to the real module; importing the tracker never imports
    mediapipe.
  * Headless construction fails with an actionable camera RuntimeError —
    never NameError, never "hands active".
  * The camera is probed BEFORE the model check / detector build.
  * Dead cameras never raise and never fabricate hands.
  * ``release()`` is tolerant; ``camera_ok()`` returns a plain bool.

Headless-safe: no real camera and no real mediapipe inference required.
"""

from __future__ import annotations

import subprocess
import sys
import types

import pytest

from airmouse import tracker as tracker_mod
from airmouse.tracker import HandTracker


# ── fakes ──────────────────────────────────────────────────────────────────

class _ClosedCap:
    """Fake VideoCapture that never opens (headless sandbox behavior)."""

    def __init__(self, index):
        self.index = index
        self.released = False

    def isOpened(self):
        return False

    def read(self):
        return False, None

    def release(self):
        self.released = True


class _OpenCap:
    """Fake VideoCapture that opens but yields no frames (dead camera)."""

    def __init__(self, index):
        self.index = index
        self.released = False

    def isOpened(self):
        return not self.released

    def set(self, *a):
        return True

    def get(self, prop):
        return 0.0

    def read(self):
        return False, None

    def release(self):
        self.released = True


def _dead_read():
    return False, None


def _none_read():
    return None


def _exploding_read():
    raise RuntimeError("camera backend exploded mid-read")


class _RaisingCap:
    def release(self):
        raise OSError("camera backend exploded on release")


class _RaisingDetector:
    def close(self):
        raise RuntimeError("detector close exploded")


@pytest.fixture
def dead_camera(monkeypatch):
    """cv2.VideoCapture replaced by a handle that never opens."""
    made = []

    def _factory(index):
        cap = _ClosedCap(index)
        made.append(cap)
        return cap

    monkeypatch.setattr(tracker_mod.cv2, "VideoCapture", _factory)
    return made


# ── 1. headless constructor: camera RuntimeError, NOT NameError ────────────

def test_headless_constructor_raises_camera_runtimeerror_not_nameerror():
    """TF-001 repro: at HEAD this died with ``NameError: name 'mp' is not
    defined``.  The fixed constructor must probe the camera first and fail
    with an explicit camera RuntimeError (skipped on machines that really
    have a camera at index 0)."""
    import cv2 as _cv2
    probe = _cv2.VideoCapture(0)
    opens = bool(probe.isOpened())
    probe.release()
    if opens:
        pytest.skip("a real camera is present — headless path not applicable")
    with pytest.raises(RuntimeError) as ei:
        HandTracker()
    assert "camera" in str(ei.value).lower()


def test_headless_constructor_never_nameerror_monkeypatched(dead_camera):
    """Deterministic variant with a faked closed camera handle."""
    with pytest.raises(RuntimeError) as ei:
        HandTracker(camera_index=0)
    low = str(ei.value).lower()
    assert "camera" in low
    # the failed handle is released, not leaked
    assert dead_camera and dead_camera[0].released


def test_camera_probe_failure_message_is_actionable(dead_camera):
    """The camera error names the index and tells the user what to do."""
    with pytest.raises(RuntimeError) as ei:
        HandTracker(camera_index=7)
    msg = str(ei.value)
    assert "camera" in msg.lower()
    assert "7" in msg
    assert "fix" in msg.lower()          # actionable guidance section


# ── 2. lazy import contract ─────────────────────────────────────────────────

def test_lazy_import_contract_subprocess():
    """Importing airmouse.tracker must NOT import mediapipe; the bare
    ``t.mp`` fetch is first touch: it imports mediapipe and rebinds the
    global so ``t.mp is sys.modules['mediapipe']``."""
    code = (
        "import sys;"
        "import airmouse.tracker as t;"
        "assert 'mediapipe' not in sys.modules, 'tracker eager mediapipe import';"
        "_ = t.mp;"                                  # bare fetch IS first touch
        "assert 'mediapipe' in sys.modules, 'first touch did not import';"
        "assert t.mp is sys.modules['mediapipe'], 'mp is not the real module'"
    )
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_double_import_is_stable_and_lazy():
    """Double import returns the same module object and stays lazy."""
    code = (
        "import sys;"
        "import airmouse.tracker as a;"
        "import airmouse.tracker as b;"
        "assert a is b, 'double import re-executed the module';"
        "assert a.HandTracker is b.HandTracker;"
        "assert 'mediapipe' not in sys.modules, 'double import pulled mediapipe'"
    )
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_mediapipe_import_failure_is_actionable(monkeypatch):
    """A failing first-touch mediapipe import raises ImportError with a
    pip-install hint — not a raw traceback."""
    # fresh proxy + poisoned sys.modules entry force the import to fail
    monkeypatch.setattr(tracker_mod, "mp", tracker_mod._LazyMp())
    monkeypatch.setitem(sys.modules, "mediapipe", None)
    with pytest.raises(ImportError) as ei:
        tracker_mod.mp.tasks
    assert "pip install mediapipe" in str(ei.value)


# ── 3. dead camera reads ────────────────────────────────────────────────────

@pytest.mark.parametrize("backend", [_dead_read, _none_read, _exploding_read],
                         ids=["false-none", "none", "raising"])
def test_dead_camera_read_never_raises_or_fabricates(backend):
    """read() on a dead camera yields the honest empty result — no
    exception, no fabricated hands."""
    t = object.__new__(HandTracker)          # headless, no hardware __init__
    t.cap = types.SimpleNamespace(read=backend)
    t.detector = types.SimpleNamespace(detect_for_video=lambda *a: None)
    r = t.read()
    assert r["hand_found"] is False
    assert r["hands"] == []
    assert r["landmarks"] is None
    assert r["index_pos"] is None
    assert r["frame"] is None


# ── 4. missing-model guidance (camera probe bypassed) ──────────────────────

def test_missing_model_guidance_is_actionable(tmp_path, monkeypatch):
    """With the camera probe bypassed and a tmp AIRMOUSE_HOME, the missing
    model raises guidance that names the kill-switch env var, the pinned
    URL and the placement path — and never auto-downloads."""
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    # bypass the camera probe so the model check is reached (headless)
    monkeypatch.setattr(tracker_mod.cv2, "VideoCapture",
                        lambda index: _OpenCap(index))
    with pytest.raises(RuntimeError) as ei:
        HandTracker()
    msg = str(ei.value)
    assert "AIRMOUSE_NO_MODEL_DOWNLOAD" in msg
    assert "hand_landmarker.task" in msg
    assert tracker_mod.MODEL_URL in msg
    assert str(tmp_path) in msg
    # never auto-download: no model file appeared
    assert not (tmp_path / "hand_landmarker.task").exists()


# ── 5. teardown + health ────────────────────────────────────────────────────

def test_release_is_tolerant_to_raising_backend():
    """release() never raises — even when both backends explode, and on a
    half-constructed tracker with no backends at all."""
    t = object.__new__(HandTracker)
    t.cap = _RaisingCap()
    t.detector = _RaisingDetector()
    t.release()                              # must not raise
    object.__new__(HandTracker).release()    # attribute-free: must not raise


def test_camera_ok_returns_bool():
    """camera_ok() is a plain bool in every state and never raises."""
    bare = object.__new__(HandTracker)       # no cap at all
    assert bare.camera_ok() is False

    up = object.__new__(HandTracker)         # open camera
    up.cap = _OpenCap(0)
    up._camera_ok = True
    assert up.camera_ok() is True

    down = object.__new__(HandTracker)       # closed camera
    down.cap = _ClosedCap(0)
    down._camera_ok = True
    assert down.camera_ok() is False

    dumb = object.__new__(HandTracker)       # no isOpened: probe verdict
    dumb.cap = types.SimpleNamespace()
    dumb._camera_ok = True
    assert dumb.camera_ok() is True
    assert isinstance(bare.camera_ok(), bool)
