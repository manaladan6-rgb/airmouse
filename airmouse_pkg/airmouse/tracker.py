"""
Hand Tracker — MediaPipe Tasks API with finger-relative tracking.

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

import os
import urllib.request
import cv2
import mediapipe as mp
import numpy as np

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
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


def _ensure_model():
    if os.path.exists(MODEL_PATH):
        return MODEL_PATH
    os.makedirs(CACHE_DIR, exist_ok=True)
    print(f"  Downloading hand tracking model (first run only)...")
    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print(f"  Model saved to {MODEL_PATH}")
    except Exception as e:
        raise RuntimeError(
            f"Failed to download model: {e}\n"
            f"Download manually:\n  {MODEL_URL}\nSave to:\n  {MODEL_PATH}"
        )
    return MODEL_PATH


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

        model_path = _ensure_model()

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

        self.cap = cv2.VideoCapture(camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.frame_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.last_frame = None
        self.frame_timestamp_ms = 0

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

    def read(self):
        ret, frame = self.cap.read()
        if not ret:
            return self._empty_result()

        frame = cv2.flip(frame, 1)
        self.last_frame = frame
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        self.frame_timestamp_ms += 33
        result = self.detector.detect_for_video(mp_image, self.frame_timestamp_ms)

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

    def release(self):
        self.cap.release()
        self.detector.close()
