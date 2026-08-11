"""
Hand Tracker — MediaPipe Hands for lightweight finger tracking.

Detects the INDEX FINGER TIP landmark and returns its position
normalized to screen coordinates. Also detects pinch gesture
(index + thumb distance) for click actions.
"""

import cv2
import mediapipe as mp
import numpy as np


class HandTracker:
    """Tracks index finger tip position using MediaPipe Hands.

    MediaPipe Hands gives 21 landmarks per hand. We use:
    - Landmark 8  = Index finger tip  → cursor position
    - Landmark 4  = Thumb tip         → pinch detection
    - Landmark 6  = Index finger PIP  → finger raised detection
    """

    # MediaPipe landmark indices
    WRIST = 0
    THUMB_TIP = 4
    INDEX_PIP = 6
    INDEX_TIP = 8
    MIDDLE_PIP = 10
    MIDDLE_TIP = 12

    def __init__(
        self,
        camera_index: int = 0,
        detection_confidence: float = 0.7,
        tracking_confidence: float = 0.5,
        max_num_hands: int = 1,
    ):
        self.camera_index = camera_index

        # MediaPipe Hands — lightweight, runs on CPU
        self.hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=max_num_hands,
            min_detection_confidence=detection_confidence,
            min_tracking_confidence=tracking_confidence,
        )

        # Webcam
        self.cap = cv2.VideoCapture(camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

        # State
        self.frame_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.last_frame: np.ndarray | None = None

    def read(self) -> dict:
        """Read one frame and extract hand data.

        Returns:
            {
                'index_pos':  np.ndarray [x, y] in normalized [0,1] coords,
                              or None if no hand detected.
                'pinch':      bool — True if thumb and index are pinched,
                'index_up':   bool — True if index finger is extended,
                'middle_up':  bool — True if middle finger is extended,
                'hand_found': bool,
                'frame':      np.ndarray — the camera frame (for debug display),
            }
        """
        ret, frame = self.cap.read()
        if not ret:
            return self._empty_result()

        # Flip horizontally for mirror effect (natural feel)
        frame = cv2.flip(frame, 1)
        self.last_frame = frame

        # Convert to RGB for MediaPipe
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.hands.process(rgb)

        if not results.multi_hand_landmarks:
            return self._empty_result(frame=frame)

        hand = results.multi_hand_landmarks[0]
        landmarks = hand.landmark

        # Index finger tip position (normalized 0-1)
        index_tip = landmarks[self.INDEX_TIP]
        index_pos = np.array([index_tip.x, index_tip.y])

        # Thumb tip
        thumb_tip = landmarks[self.THUMB_TIP]

        # Pinch detection: distance between thumb tip and index tip
        pinch_dist = np.sqrt(
            (thumb_tip.x - index_tip.x) ** 2 + (thumb_tip.y - index_tip.y) ** 2
        )
        pinch = pinch_dist < 0.05  # Threshold for pinch

        # Finger raised detection
        index_pip = landmarks[self.INDEX_PIP]
        index_up = index_tip.y < index_pip.y  # Tip above PIP = finger up

        middle_tip = landmarks[self.MIDDLE_TIP]
        middle_pip = landmarks[self.MIDDLE_PIP]
        middle_up = middle_tip.y < middle_pip.y

        return {
            "index_pos": index_pos,
            "pinch": pinch,
            "index_up": index_up,
            "middle_up": middle_up,
            "hand_found": True,
            "frame": frame,
            "landmarks": landmarks,
        }

    def _empty_result(self, frame: np.ndarray | None = None) -> dict:
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
        """Release camera and MediaPipe resources."""
        self.cap.release()
        self.hands.close()
