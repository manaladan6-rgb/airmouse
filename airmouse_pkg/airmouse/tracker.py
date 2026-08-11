"""
Hand Tracker — MediaPipe Tasks API for lightweight finger tracking.

Uses the modern mediapipe.tasks.vision.HandLandmarker API which works
with ALL mediapipe versions (0.10.x and 1.0+). The legacy mp.solutions
API was removed in mediapipe 0.10.35+.

On first run, downloads the hand_landmarker.task model (~8MB) to a
user cache directory.
"""

import os
import urllib.request
import cv2
import mediapipe as mp
import numpy as np

# Model URL and cache path
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".airmouse")
MODEL_PATH = os.path.join(CACHE_DIR, "hand_landmarker.task")


def _ensure_model():
    """Download the hand landmarker model if not cached."""
    if os.path.exists(MODEL_PATH):
        return MODEL_PATH
    os.makedirs(CACHE_DIR, exist_ok=True)
    print(f"  Downloading hand tracking model (first run only)...")
    print(f"  -> {MODEL_URL}")
    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print(f"  Model saved to {MODEL_PATH}")
    except Exception as e:
        raise RuntimeError(
            f"Failed to download hand tracking model: {e}\n"
            f"Download it manually from:\n  {MODEL_URL}\n"
            f"Save it to:\n  {MODEL_PATH}"
        )
    return MODEL_PATH


class HandTracker:
    """Tracks index finger tip position using MediaPipe Tasks API.

    Landmarks (same indices as legacy API):
    - Landmark 8  = Index finger tip  -> cursor position
    - Landmark 4  = Thumb tip         -> pinch detection
    - Landmark 6  = Index finger PIP  -> finger raised detection
    """

    WRIST = 0
    THUMB_TIP = 4
    INDEX_PIP = 6
    INDEX_TIP = 8
    MIDDLE_PIP = 10
    MIDDLE_TIP = 12

    def __init__(self, camera_index=0, detection_confidence=0.7,
                 tracking_confidence=0.5, max_num_hands=1):
        self.camera_index = camera_index

        # Download model if needed
        model_path = _ensure_model()

        # Create HandLandmarker using Tasks API
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

        # Webcam
        self.cap = cv2.VideoCapture(camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.frame_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.last_frame = None
        self.frame_timestamp_ms = 0

    def read(self):
        """Read one frame and extract hand data."""
        ret, frame = self.cap.read()
        if not ret:
            return self._empty_result()

        # Flip horizontally for mirror effect (natural feel)
        frame = cv2.flip(frame, 1)
        self.last_frame = frame

        # Convert to RGB for MediaPipe
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Create MediaPipe Image
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # Detect hands (VIDEO mode needs timestamp)
        self.frame_timestamp_ms += 33  # ~30fps
        result = self.detector.detect_for_video(mp_image, self.frame_timestamp_ms)

        if not result.hand_landmarks:
            return self._empty_result(frame=frame)

        # Get first hand's landmarks
        landmarks = result.hand_landmarks[0]

        # Index finger tip position (normalized 0-1)
        index_tip = landmarks[self.INDEX_TIP]
        index_pos = np.array([index_tip.x, index_tip.y])

        # Thumb tip
        thumb_tip = landmarks[self.THUMB_TIP]

        # Pinch detection
        pinch_dist = np.sqrt(
            (thumb_tip.x - index_tip.x) ** 2 + (thumb_tip.y - index_tip.y) ** 2
        )
        pinch = pinch_dist < 0.05

        # Finger raised detection
        index_pip = landmarks[self.INDEX_PIP]
        index_up = index_tip.y < index_pip.y

        middle_tip = landmarks[self.MIDDLE_TIP]
        middle_pip = landmarks[self.MIDDLE_PIP]
        middle_up = middle_tip.y < middle_pip.y

        # Convert landmarks to a simple object for debug drawing
        class LandmarkProxy:
            pass
        landmark_list = []
        for lm in landmarks:
            p = LandmarkProxy()
            p.x = lm.x
            p.y = lm.y
            p.z = lm.z
            landmark_list.append(p)

        return {
            "index_pos": index_pos,
            "pinch": pinch,
            "index_up": index_up,
            "middle_up": middle_up,
            "hand_found": True,
            "frame": frame,
            "landmarks": landmark_list,
        }

    def _empty_result(self, frame=None):
        return {
            "index_pos": None,
            "pinch": False,
            "index_up": False,
            "middle_up": False,
            "hand_found": False,
            "frame": frame,
            "landmarks": None,
        }

    def release(self):
        """Release camera and detector resources."""
        self.cap.release()
        self.detector.close()
