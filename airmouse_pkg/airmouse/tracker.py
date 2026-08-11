"""
Hand Tracker — MediaPipe Hands for lightweight finger tracking.
"""

import cv2
import mediapipe as mp
import numpy as np


class HandTracker:
    """Tracks index finger tip position using MediaPipe Hands."""

    WRIST = 0
    THUMB_TIP = 4
    INDEX_PIP = 6
    INDEX_TIP = 8
    MIDDLE_PIP = 10
    MIDDLE_TIP = 12

    def __init__(self, camera_index=0, detection_confidence=0.7,
                 tracking_confidence=0.5, max_num_hands=1):
        self.camera_index = camera_index
        self.hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=max_num_hands,
            min_detection_confidence=detection_confidence,
            min_tracking_confidence=tracking_confidence,
        )
        self.cap = cv2.VideoCapture(camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.frame_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.last_frame = None

    def read(self):
        ret, frame = self.cap.read()
        if not ret:
            return self._empty_result()

        frame = cv2.flip(frame, 1)
        self.last_frame = frame
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.hands.process(rgb)

        if not results.multi_hand_landmarks:
            return self._empty_result(frame=frame)

        hand = results.multi_hand_landmarks[0]
        landmarks = hand.landmark

        index_tip = landmarks[self.INDEX_TIP]
        index_pos = np.array([index_tip.x, index_tip.y])

        thumb_tip = landmarks[self.THUMB_TIP]
        pinch_dist = np.sqrt(
            (thumb_tip.x - index_tip.x) ** 2 + (thumb_tip.y - index_tip.y) ** 2
        )
        pinch = pinch_dist < 0.05

        index_pip = landmarks[self.INDEX_PIP]
        index_up = index_tip.y < index_pip.y

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
        self.cap.release()
        self.hands.close()
