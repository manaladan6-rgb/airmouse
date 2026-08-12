"""
Hand Tracker — MediaPipe Tasks API with finger-relative tracking.

On first run, downloads the hand_landmarker.task model (~8MB).
"""

import os
import urllib.request
import cv2
import mediapipe as mp
import numpy as np

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".airmouse")
MODEL_PATH = os.path.join(CACHE_DIR, "hand_landmarker.task")


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
    """Tracks hand using MediaPipe Tasks API."""

    WRIST = 0
    THUMB_TIP = 4
    INDEX_TIP = 8
    MIDDLE_TIP = 12

    def __init__(self, camera_index=0, detection_confidence=0.7,
                 tracking_confidence=0.5, max_num_hands=1):
        model_path = _ensure_model()

        BaseOptions = mp.tasks.BaseOptions
        HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
        VisionRunningMode = mp.tasks.vision.RunningMode

        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=VisionRunningMode.VIDEO,
            num_hands=max_num_hands,
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

        landmarks = result.hand_landmarks[0]

        # Convert to simple objects for gesture engine
        class LM:
            pass
        landmark_list = []
        for lm in landmarks:
            p = LM()
            p.x, p.y, p.z = lm.x, lm.y, lm.z
            landmark_list.append(p)

        index_tip = landmarks[self.INDEX_TIP]
        index_pos = np.array([index_tip.x, index_tip.y])

        thumb_tip = landmarks[self.THUMB_TIP]
        pinch_dist = np.sqrt(
            (thumb_tip.x - index_tip.x) ** 2 + (thumb_tip.y - index_tip.y) ** 2
        )

        return {
            "hand_found": True,
            "landmarks": landmark_list,
            "index_pos": index_pos,
            "pinch_distance": pinch_dist,
            "frame": frame,
        }

    def _empty_result(self, frame=None):
        return {
            "hand_found": False,
            "landmarks": None,
            "index_pos": None,
            "pinch_distance": 1.0,
            "frame": frame,
        }

    def release(self):
        self.cap.release()
        self.detector.close()
