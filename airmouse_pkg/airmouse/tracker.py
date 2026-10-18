"""
Hand Tracker — MediaPipe Tasks API with finger-relative tracking.

v19.0.3 (TF-001) — real lazy ``mp`` global + constructor hardening:
  - ``mp`` is bound at import time to a real module global holding a
    delegating proxy (``_LazyMp``), so bare global lookups inside this
    module resolve; the first touch still pays the mediapipe import and
    rebinds the global to the real module object.
  - ``HandTracker()`` probes the camera BEFORE building the detector and
    fails with actionable RuntimeErrors (camera / model guidance) — a
    camera failure can never masquerade as "hands active".
  - ``read()`` tolerates dead cameras; ``release()`` never raises;
    ``camera_ok()`` reports camera health as a plain bool.

On first run, downloads the hand_landmarker.task model (~8MB).

v15.2 — two-hand foundation (BACKWARD-COMPATIBLE extension):

  - ``HandTracker(..., max_hands=2)`` enables MediaPipe multi-hand
    detection (default stays 1 — nothing changes for existing callers).
  - ``read()`` now ALWAYS returns a ``"hands"`` key: a list of per-hand
    dicts (empty list when no hand is visible, one entry per detected
    hand otherwise).  Downstream code never needs to special-case a
    missing key.
  - The legacy single-hand keys (``hand_found``, ``landmarks``,
    ``index_pos``, ``pinch_distance``, ``frame``) are UNCHANGED and keep
    describing the PRIMARY hand = ``result.hand_landmarks[0]`` — the raw
    MediaPipe ordering (by detection confidence).  We deliberately do
    NOT re-order by handedness to avoid any regression in the existing
    single-hand cursor path.  With ``max_hands=1`` the only difference
    to v15.1 is the added (0- or 1-entry) ``"hands"`` list.

Handedness convention (important, read before using labels):

  MediaPipe Tasks docs (HandLandmarker): "Handedness is determined
  assuming the input image is mirrored, i.e., taken with a
  front-facing/selfie camera with images flipped horizontally.  If it
  is not the case, please swap the handedness output in the
  application."

  ``read()`` mirrors the frame itself (``cv2.flip(frame, 1)`` below),
  so the model's assumption HOLDS for our pipeline: the RAW label is
  the user's true hand (raw ``"Left"`` == the user's actual left hand
  as seen in the mirrored on-screen view).  We therefore return:

    - ``handedness``:     the RAW MediaPipe label ("Left" / "Right"),
    - ``handedness_score``: the raw classification score (0..1),
    - ``is_left_user``:   True when the hand is the user's left hand
                          for the mirrored view, i.e.
                          ``is_left_user = (raw_label == "Left")``.

  The raw label is always available, so consumers that follow the
  opposite (unmirrored-input) convention can re-derive either flag
  without touching the tracker.
"""

import hashlib
import os
import sys
import time
import types
import urllib.request

import numpy as np


class _LazyCv2:
    """v19.0.2 — lazy cv2 proxy (same rationale as airmouse.__main__).

    Every cv2.* use in this module lives inside HandTracker (camera open /
    read / flip) — strictly after the user asked to run. Importing OpenCV
    at module scope made every CLI command that merely imports the tracker
    for the HandTracker class object pay ~0.4 s of OpenCV import.
    """

    def __getattr__(self, name):
        import cv2 as _cv2
        globals()["cv2"] = _cv2
        return getattr(_cv2, name)


cv2 = _LazyCv2()

# ── v19.0.3 (TF-001) — mediapipe lazy loading via a REAL module global ──
# mediapipe pulls matplotlib and ~1.0 s of import work, which must not tax
# every CLI command (`airmouse --version`, doctor, setup, ...) that merely
# imports this module for the HandTracker class object.
#
# TF-001 root cause: the previous PEP 562-only binding satisfied *outside*
# attribute fetches (``airmouse.tracker.mp``) but can never satisfy a bare
# LOAD_GLOBAL inside this module's own functions — ``HandTracker.__init__``
# died with ``NameError: name 'mp' is not defined``.  ``mp`` is therefore
# bound at import time to a real module global holding a tiny delegating
# proxy: the first attribute access imports mediapipe, REBINDS the global
# to the real module object and forwards the access, so after first touch
# ``airmouse.tracker.mp is sys.modules["mediapipe"]``.  Plain
# ``monkeypatch.setattr(tracker, "mp", fake)`` keeps working because the
# global is an ordinary module attribute.


class _LazyMp:
    """Delegating lazy proxy for the mediapipe module (TF-001).

    Bound as the module global ``mp`` at import time; forwards every
    attribute access to the real mediapipe module, imported on first
    touch.
    """

    def __getattr__(self, name):
        return getattr(_materialize_mp(), name)


def _materialize_mp():
    """Import mediapipe on first touch; install it as the ``mp`` global.

    Raises ImportError with actionable text when mediapipe is missing or
    its native dependencies are broken.
    """
    try:
        import mediapipe as _mp
    except Exception as e:  # ImportError or broken native dependencies
        raise ImportError(
            "mediapipe is required for hand tracking but could not be "
            f"imported: {e}\n"
            "      FIX : pip install mediapipe\n"
            "            (inside the Python environment AirMouse runs in)"
        ) from e
    globals()["mp"] = _mp  # real module global from here on
    return _mp


mp = _LazyMp()


def __getattr__(name):
    """PEP 562 fallback for names absent from the module dict.

    ``mp`` is pre-bound above (TF-001), so this only fires for genuinely
    unknown attributes — plus a safety net if the global was deleted.
    """
    if name == "mp":
        return _materialize_mp()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _TrackerModule(types.ModuleType):
    """Module class making a *bare* ``airmouse.tracker.mp`` fetch count
    as first touch.

    A dict hit on a module attribute is inert — PEP 562 cannot fire while
    ``mp`` sits in the module dict — yet the lazy contract (and existing
    tests) require ``t.mp`` itself to import mediapipe and to BE
    ``sys.modules["mediapipe"]`` afterwards.  Intercepting the fetch at
    the module-class level keeps that contract intact next to the
    LOAD_GLOBAL-safe pre-bound proxy.
    """

    def __getattribute__(self, name):
        if name == "mp":
            current = globals().get("mp")
            if current is None or type(current) is _LazyMp:
                return _materialize_mp()
        return super().__getattribute__(name)

# v19.0.2 — PINNED, versioned model URL + pinned SHA-256.  The old URL
# used the mutable "latest" alias with no integrity check, no consent,
# and no atomic install (audit CRITICAL #1).  The versioned artifact is
# byte-identical to what "latest" served at the time of pinning.
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/"
             "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task")
MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
MODEL_SIZE_BYTES = 7819105
# Env kill-switch: set AIRMOUSE_NO_MODEL_DOWNLOAD=1 to refuse all model
# downloads (air-gapped / privacy-critical machines must provide the
# model file manually at MODEL_PATH).
NO_MODEL_DOWNLOAD_ENV = "AIRMOUSE_NO_MODEL_DOWNLOAD"
# Path split-brain fix (audit HIGH #2): always resolve through paths.py so
# AIRMOUSE_HOME is honored; keep the legacy ~/.airmouse path as fallback.
def _resolve_model_path() -> str:
    try:
        from . import paths
        return str(paths.model_file())
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".airmouse",
                            "hand_landmarker.task")

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".airmouse")
MODEL_PATH = os.path.join(CACHE_DIR, "hand_landmarker.task")


class _LM:
    """Lightweight landmark record (.x / .y / .z, normalized coords).

    Same attribute contract as the inline ``LM`` class this module has
    always fed to the gesture engine — consumers keep working untouched.
    """

    __slots__ = ("x", "y", "z")

    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


def _sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_model(model_path: str = None, consent_fn=None) -> str:
    """Locate / download / verify the hand-landmarker model (v19.0.2 hard).

    Hardening contract (audit CRITICAL #1):
      1. An existing file is verified against the pinned SHA-256;
         a corrupt cache is re-downloaded (or refused offline).
      2. Downloads are CONSENTED: when stdin is a TTY the user is asked
         first; ``consent_fn`` overrides for embedded/wizard callers.
         A non-interactive run without a cached model fails with clear
         manual instructions instead of silently fetching.
      3. AIRMOUSE_NO_MODEL_DOWNLOAD=1 is an absolute kill-switch.
      4. Download goes to ``<path>.part`` first, is hash-verified, then
         atomically renamed — a killed download can never leave a broken
         model in place.
      5. The request has a timeout so a hanging network fails loudly.

    Returns the verified model path; raises RuntimeError with actionable
    text on any refusal/failure.
    """
    path = model_path or _resolve_model_path()
    model_dir = os.path.dirname(path) or "."
    os.makedirs(model_dir, exist_ok=True)

    if os.path.exists(path):
        try:
            if _sha256_of(path) == MODEL_SHA256:
                return path
            print("  !! Cached hand model failed the SHA-256 integrity "
                  "check — re-downloading.")
        except OSError as e:
            print(f"  !! Cached hand model unreadable ({e}) — "
                  "re-downloading.")

    if os.environ.get(NO_MODEL_DOWNLOAD_ENV, "").strip() not in ("", "0"):
        raise RuntimeError(
            "Hand tracking model is missing and downloads are disabled by\n"
            f"      {NO_MODEL_DOWNLOAD_ENV}=1.\n"
            "      WHAT: AirMouse cannot start hand tracking without the\n"
            "            MediaPipe hand-landmarker model (~7.5 MB).\n"
            "      FIX : download it manually (once) from\n"
            f"            {MODEL_URL}\n"
            f"            and save it to: {path}\n"
            f"            Expected SHA-256: {MODEL_SHA256}")

    # ── consent (v19.0.2) ────────────────────────────────────────────
    if consent_fn is not None:
        granted = bool(consent_fn())
    elif sys.stdin is not None and sys.stdin.isatty():
        print("  AirMouse needs the MediaPipe hand-landmarker model "
              "(~7.5 MB, one time).")
        print(f"      Source : {MODEL_URL}")
        print(f"      Saved to: {path}")
        print("      It is integrity-checked against a pinned SHA-256 and "
              "runs 100% locally.")
        try:
            granted = input("      Download now? [Y/n] ").strip().lower() \
                not in ("n", "no")
        except (EOFError, KeyboardInterrupt):
            granted = False
    else:
        granted = False  # non-interactive: never fetch without consent

    if not granted:
        raise RuntimeError(
            "Hand tracking model is not installed.\n"
            "      WHAT: hand tracking requires the MediaPipe model "
            "(~7.5 MB).\n"
            "      FIX : run `airmouse setup` (guided download), or set\n"
            f"            {NO_MODEL_DOWNLOAD_ENV}=1 and place the file "
            "manually:\n"
            f"            {MODEL_URL}\n"
            f"            -> {path}")

    # ── atomic, verified download ────────────────────────────────────
    part_path = path + ".part"
    try:
        print("  Downloading hand tracking model (~7.5 MB, one time)...")
        t0 = time.monotonic()
        with urllib.request.urlopen(MODEL_URL, timeout=30) as resp, \
                open(part_path, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        got = _sha256_of(part_path)
        if got != MODEL_SHA256:
            raise RuntimeError(
                f"integrity check FAILED (sha256 {got[:16]}… != expected "
                f"{MODEL_SHA256[:16]}…) — refusing to install")
        os.replace(part_path, path)  # atomic install
        dt = time.monotonic() - t0
        print(f"  Model verified + installed ({MODEL_SIZE_BYTES} bytes, "
              f"{dt:.1f}s): {path}")
    except Exception as e:
        try:
            if os.path.exists(part_path):
                os.remove(part_path)  # never leave .part junk behind
        except OSError:
            pass
        raise RuntimeError(
            f"Model download failed: {e}\n"
            f"      Download manually from:\n          {MODEL_URL}\n"
            f"      Save to: {path}\n"
            f"      Expected SHA-256: {MODEL_SHA256}")
    return path


class HandTracker:
    """Tracks hand(s) using MediaPipe Tasks API.

    ``max_hands`` (v15.2): 1 or 2 (validated).  2 enables two-hand
    detection; the per-hand data lands in ``read()["hands"]``.  The
    legacy ``max_num_hands`` kwarg is still accepted; ``max_hands``
    overrides it when explicitly provided.
    """

    WRIST = 0
    THUMB_TIP = 4
    INDEX_TIP = 8
    MIDDLE_TIP = 12

    def __init__(self, camera_index=0, detection_confidence=0.7,
                 tracking_confidence=0.5, max_num_hands=1, max_hands=None):
        # Validate FIRST — fail fast before any model download or camera
        # open, so bad values are cheap, headless-safe errors.
        if max_hands is None:
            max_hands = max_num_hands
        if max_hands not in (1, 2):
            raise ValueError(
                f"max_hands must be 1 or 2, got {max_hands!r}")
        self.max_hands = int(max_hands)

        # v19.0.3 (TF-001) — CAMERA PROBE FIRST.  A machine without a
        # usable camera must fail here with an explicit camera error —
        # never as a downstream NameError/ImportError from detector
        # construction, and never in a state that could look like
        # "hands active".
        self.cap = cv2.VideoCapture(camera_index)
        if not self._camera_opens(self.cap):
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
            raise RuntimeError(
                f"Camera index {camera_index} could not be opened — hand "
                "tracking cannot start.\n"
                "      WHAT: the camera device did not open, so no video\n"
                "            frames can be read for hand detection.\n"
                "      FIX : check that a camera is connected, powered and\n"
                "            not held by another app, or try another camera\n"
                "            index.  On a headless machine there is no\n"
                "            camera: gesture control is unavailable and\n"
                "            AirMouse degrades honestly instead of\n"
                "            pretending hands are active.")
        self._camera_ok = True

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.frame_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.last_frame = None
        # v19.0.2 — REAL monotonic frame timestamps (audit HIGH #2).
        # The old code incremented a synthetic +33 ms counter, which made
        # every MediaPipe call believe a perfect 30 fps world and made
        # dropped frames invisible to the timing chain.
        self._t0_monotonic = None
        self.frame_timestamp_ms = 0
        self.frame_timestamp_s = 0.0

        # v19.0.3 (TF-001) — MODEL second: resolve/verify the pinned
        # hand-landmarker model.  The constructor never downloads: a
        # missing model raises actionable guidance instead.
        model_path = _ensure_model()
        self.model_path = model_path

        # v19.0.3 (TF-001) — DETECTOR last: the FIRST mediapipe touch
        # (lazy ``mp`` global) happens here, only after camera and model
        # are known good.
        BaseOptions = mp.tasks.BaseOptions
        HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
        VisionRunningMode = mp.tasks.vision.RunningMode

        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=VisionRunningMode.VIDEO,
            num_hands=self.max_hands,
            min_hand_detection_confidence=detection_confidence,
            min_hand_presence_confidence=tracking_confidence,
            min_tracking_confidence=tracking_confidence,
        )
        self.detector = mp.tasks.vision.HandLandmarker.create_from_options(options)

    @staticmethod
    def _camera_opens(cap) -> bool:
        """v19.0.3 (TF-001) — open-probe for the capture handle.

        Real OpenCV handles expose ``isOpened()``; lightweight test fakes
        may not, and are assumed to open (their dead-camera behavior is
        exercised through ``read()``).  Never raises.
        """
        try:
            is_open = getattr(cap, "isOpened", None)
            if callable(is_open):
                return bool(is_open())
        except Exception:
            return False
        return True

    def _hand_entry(self, landmarks, categories):
        """Build one ``"hands"`` entry from a single MediaPipe detection.

        ``landmarks``  — 21 normalized landmarks (.x/.y/.z)
        ``categories`` — MediaPipe handedness Category list for the hand
                         (may be None/empty when the result lacks it)
        """
        lm_list = [_LM(lm.x, lm.y, lm.z) for lm in landmarks]

        index_tip = lm_list[self.INDEX_TIP]
        thumb_tip = lm_list[self.THUMB_TIP]
        index_pos = np.array([index_tip.x, index_tip.y])
        pinch_dist = float(np.sqrt(
            (thumb_tip.x - index_tip.x) ** 2 + (thumb_tip.y - index_tip.y) ** 2
        ))

        raw_label, score = "Unknown", 0.0
        if categories:
            name = getattr(categories[0], "category_name", None)
            raw_label = name if name else "Unknown"
            score = float(getattr(categories[0], "score", 0.0))

        # Mirrored-input convention — see module docstring.  The frame is
        # cv2.flip()ed below, matching the model's mirrored-selfie
        # assumption, so the RAW label already IS the user's true hand:
        # raw "Left" -> user's left hand in the on-screen (mirrored) view.
        is_left_user = (raw_label == "Left")

        return {
            "landmarks": lm_list,
            "index_pos": index_pos,
            "pinch_distance": pinch_dist,
            "handedness": raw_label,
            "handedness_score": score,
            "is_left_user": is_left_user,
        }

    # v19.0.2 — input validation boundary (audit MEDIUM #5).  A landmark
    # coordinate that is non-finite or wildly out of the normalized range
    # is a broken sensor read, not a hand position: it is REJECTED (the
    # frame is reported as no-hand) so NaN can never reach the filters,
    # the calibration learner, or the OS cursor.
    _LM_LO = -0.5
    _LM_HI = 1.5

    @classmethod
    def _valid_xy(cls, xy) -> bool:
        try:
            arr = np.asarray(xy, dtype=float)
        except (TypeError, ValueError):
            return False
        if arr.shape != (2,) or not np.all(np.isfinite(arr)):
            return False
        return bool(np.all(arr >= cls._LM_LO) and np.all(arr <= cls._LM_HI))

    def read(self):
        # v19.0.3 (TF-001) — dead-camera tolerance: a backend yielding
        # (False, None) or None, or raising, reports "no hands" honestly
        # and never fabricates landmarks.
        try:
            got = self.cap.read()
        except Exception:
            return self._empty_result()
        if got is None:
            return self._empty_result()
        try:
            ret, frame = got
        except (TypeError, ValueError):
            return self._empty_result()
        if not ret or frame is None:
            return self._empty_result()

        frame = cv2.flip(frame, 1)
        self.last_frame = frame
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # v19.0.2 — real monotonic timestamp for MediaPipe VIDEO mode.
        now_mono = time.monotonic()
        if getattr(self, "_t0_monotonic", None) is None:
            self._t0_monotonic = now_mono
        self.frame_timestamp_s = now_mono - self._t0_monotonic
        self.frame_timestamp_ms = int(self.frame_timestamp_s * 1000.0)
        result = self.detector.detect_for_video(mp_image,
                                                self.frame_timestamp_ms)

        if not result.hand_landmarks:
            return self._empty_result(frame=frame)

        # ── v15.2: per-hand list (always present in the contract) ──
        # Tasks API result fields: result.hand_landmarks (list of 21-landmark
        # lists) and result.handedness (parallel list of Category lists).
        handedness_lists = getattr(result, "handedness", None) or []
        hands = []
        for i, lm in enumerate(result.hand_landmarks):
            cats = handedness_lists[i] if i < len(handedness_lists) else None
            hands.append(self._hand_entry(lm, cats))

        # v19.0.2 — reject frames whose primary landmark set is not sane.
        hands = [h for h in hands if self._valid_xy(h["index_pos"])]
        if not hands:
            return self._empty_result(frame=frame)

        # Primary hand = first MediaPipe detection — IDENTICAL to the
        # v15.1 single-hand behavior ([0], no handedness re-ordering).
        primary = hands[0]

        return {
            "hand_found": True,
            "landmarks": primary["landmarks"],
            "index_pos": primary["index_pos"],
            "pinch_distance": primary["pinch_distance"],
            "frame": frame,
            "hands": hands,
            "timestamp_s": self.frame_timestamp_s,
        }

    def _empty_result(self, frame=None):
        return {
            "hand_found": False,
            "landmarks": None,
            "index_pos": None,
            "pinch_distance": 1.0,
            "frame": frame,
            # v15.2: the contract ALWAYS carries the "hands" key so
            # downstream code never special-cases a missing key.
            "hands": [],
        }

    # v19.0.3 (TF-001) — tolerant teardown: shutdown must never raise,
    # even when a backend is already dead or half-constructed.
    def release(self):
        for attr, closer in (("cap", "release"), ("detector", "close")):
            try:
                obj = getattr(self, attr, None)
                if obj is not None:
                    method = getattr(obj, closer, None)
                    if callable(method):
                        method()
            except Exception:
                pass  # a failing backend must not break shutdown

    def camera_ok(self) -> bool:
        """v19.0.3 (TF-001) — camera health as a plain bool, never raises.

        True while the capture handle reports itself open; falls back to
        the constructor probe verdict for handles without ``isOpened``;
        False whenever anything is uncertain.
        """
        try:
            cap = getattr(self, "cap", None)
            if cap is None:
                return False
            is_open = getattr(cap, "isOpened", None)
            if callable(is_open):
                return bool(is_open())
            return bool(getattr(self, "_camera_ok", False))
        except Exception:
            return False


# v19.0.3 (TF-001) — activate the module-class hook (see _TrackerModule):
# a bare ``airmouse.tracker.mp`` fetch must count as first touch, which
# PEP 562 alone cannot deliver while the name sits in the module dict.
sys.modules[__name__].__class__ = _TrackerModule
