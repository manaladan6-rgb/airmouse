"""
Two-hand gesture recognition — pure geometry, deterministic.

Consumes the ``"hands"`` list emitted by ``tracker.HandTracker.read()``
(list of dicts with keys ``landmarks`` / ``index_pos`` / ``pinch_distance``
/ ``handedness`` / ``handedness_score`` / ``is_left_user``) and turns it
into a small, stable per-frame report for the live loop.

NO mediapipe / cv2 / numpy dependency: this module is pure Python float
math, fully deterministic (same input history -> same outputs), bounded
memory (only scalars + a 2-slot baseline; no per-frame buffers), and
fully resettable via :meth:`TwoHandGestureRecognizer.reset`.

Point accessor accepts BOTH landmark formats: tracker ``_LM`` objects
(``p.x`` / ``p.y``) and plain ``(x, y, z)`` tuples — so tests can build
synthetic hands without the tracker.

State machine (hysteresis + grace)
----------------------------------
IDLE          hold condition (2 hands, both ``pinch_distance`` below
              ``pinch_threshold``) must hold for ``engage_frames``
              consecutive frames -> ENGAGED (baseline captured).
ENGAGED       emits an active report every frame.  The dominant delta
              (largest deadzone-normalized score) picks the gesture:
              ZOOM / ROTATE / DRAG; none above its deadzone -> HOLD.
              On any failed-condition frame (hand lost or pinch opened)
              the miss counter increments: within ``grace_frames`` the
              state is retained (reports go inactive, geometry neutral)
              and a recovering frame resumes with the SAME baseline;
              after ``grace_frames`` consecutive misses the state is
              fully reset (all counters/baseline cleared) and a
              ``cooldown_s`` re-engagement lockout is armed.
reset()       clears everything immediately; does NOT arm the cooldown.

Sign conventions (image coordinates: x right, y DOWN, normalized 0..1)
----------------------------------------------------------------------
- ``scale``            = inter-hand distance / baseline distance.
                         > 1  = hands moved APART (pinch-out / zoom in),
                         < 1  = hands moved together.
- ``angle_delta_deg``  = rotation of the slot0->slot1 hand-centroid line
                         vs. the baseline angle, wrapped to (-180, +180].
                         POSITIVE = clockwise as displayed on the
                         mirrored on-screen view (y-down atan2).  Slot
                         order: user's left hand first (``is_left_user``),
                         fallback leftmost centroid; slots are re-matched
                         frame-to-frame by nearest baseline centroid so
                         handedness flicker cannot flip the line 180°.
- ``centroid_delta``   = (dx, dy) midpoint movement since baseline.
- ``handedness``       = tuple of RAW MediaPipe labels in slot order
                         (slot0 = user's left hand first, per
                         ``is_left_user``; "Unknown" when absent).

Report contract (EVERY frame, exactly these keys)::

    {"active": bool,              # ENGAGED and hold condition holding
     "gesture": None | "TWO_HAND_HOLD" | "TWO_HAND_ZOOM"
              | "TWO_HAND_ROTATE" | "TWO_HAND_DRAG",
     "scale": float,              # 1.0 when inactive
     "angle_delta_deg": float,    # 0.0 when inactive
     "centroid_delta": (dx, dy) | None,
     "confidence": float,         # 0..1 (0.0 when inactive)
     "handedness": (label, ...) }  # raw labels, slot order, () if none
"""

import math

TWO_HAND_HOLD = "TWO_HAND_HOLD"
TWO_HAND_ZOOM = "TWO_HAND_ZOOM"
TWO_HAND_ROTATE = "TWO_HAND_ROTATE"
TWO_HAND_DRAG = "TWO_HAND_DRAG"

_GESTURES = (TWO_HAND_HOLD, TWO_HAND_ZOOM, TWO_HAND_ROTATE, TWO_HAND_DRAG)

_INDEX_TIP = 8
_THUMB_TIP = 4
_FULL_LM = 21


def _xy(p):
    """Tolerant point accessor: landmark objects (.x/.y) or (x, y, z) tuples."""
    try:
        return float(p.x), float(p.y)
    except AttributeError:
        return float(p[0]), float(p[1])


def _hand_centroid(landmarks):
    """Mean of all landmark points, or None when absent."""
    if not landmarks:
        return None
    sx = sy = 0.0
    for p in landmarks:
        x, y = _xy(p)
        sx += x
        sy += y
    n = float(len(landmarks))
    return (sx / n, sy / n)


def _pinch_of(hand):
    """Pinch distance: prefer the tracker-computed key, else derive it
    from landmarks 4 (thumb tip) and 8 (index tip), else 1.0."""
    pd = hand.get("pinch_distance")
    if pd is None:
        lms = hand.get("landmarks") or []
        if len(lms) > max(_INDEX_TIP, _THUMB_TIP):
            ix, iy = _xy(lms[_INDEX_TIP])
            tx, ty = _xy(lms[_THUMB_TIP])
            pd = math.hypot(ix - tx, iy - ty)
        else:
            pd = 1.0
    return float(pd)


def _label_of(hand):
    label = hand.get("handedness")
    return label if isinstance(label, str) and label else "Unknown"


def _wrap180(deg):
    """Wrap an angle in degrees to (-180, +180]."""
    while deg <= -180.0:
        deg += 360.0
    while deg > 180.0:
        deg -= 360.0
    return deg


class TwoHandGestureRecognizer:
    """Deterministic two-hand HOLD / ZOOM / ROTATE / DRAG state machine."""

    GESTURES = _GESTURES

    def __init__(self, zoom_deadzone=0.03, rotate_deadzone_deg=8.0,
                 engage_frames=4, cooldown_s=0.5,
                 pinch_threshold=0.12, drag_deadzone=0.02, grace_frames=5):
        """
        zoom_deadzone      |scale - 1| must exceed this to fire ZOOM.
        rotate_deadzone_deg |angle_delta_deg| must exceed this for ROTATE.
        engage_frames      consecutive hold-condition frames to engage.
        cooldown_s         re-engage lockout after an engaged state resets.
        pinch_threshold    both hands' pinch_distance must be below this
                           to count as the two-hand hold pose.
        drag_deadzone      midpoint movement (normalized) to fire DRAG.
        grace_frames       transient-failure grace before a full reset
                           (spec: 5-frame grace, then full state reset).
        """
        self.zoom_deadzone = float(zoom_deadzone)
        self.rotate_deadzone_deg = float(rotate_deadzone_deg)
        self.engage_frames = max(1, int(engage_frames))
        self.cooldown_s = float(cooldown_s)
        self.pinch_threshold = float(pinch_threshold)
        self.drag_deadzone = float(drag_deadzone)
        self.grace_frames = max(0, int(grace_frames))
        self.reset()

    # ── public API ──────────────────────────────────────────────────────

    def reset(self):
        """Full state reset (counters + baseline).  Does not arm cooldown."""
        self._candidate_frames = 0
        self._miss_frames = 0
        self._engaged = False
        self._base_a = None          # slot0 centroid (user-left first)
        self._base_b = None          # slot1 centroid
        self._base_dist = 0.0
        self._base_angle_deg = 0.0
        self._base_mid = None
        self._cooldown_until = None  # absolute time (s) lockout expires
        # (no other state exists — bounded memory by construction)

    @property
    def engaged(self):
        """True while the two-hand hold state is active (introspection)."""
        return self._engaged

    def update(self, hands, now):
        """Process one frame.  ``hands`` = tracker.read()["hands"] (may be
        None/empty).  ``now`` = monotonic seconds.  Returns the report dict
        (see module docstring) — the same keys on EVERY frame."""
        if hands is None:
            hands = []
        hands = list(hands)[:2]  # tracker caps at 2; deterministic cap here

        entry = self._inactive_report(hands)

        # Hold condition: exactly two hands, both pinched closed-ish.
        if len(hands) == 2:
            a, b = hands[0], hands[1]
            if (_pinch_of(a) < self.pinch_threshold
                    and _pinch_of(b) < self.pinch_threshold):
                return self._update_engaged_or_counting(a, b, now, entry)

        # ── hold condition failed this frame ──
        if self._engaged:
            self._miss_frames += 1
            if self._miss_frames > self.grace_frames:
                # Grace expired: clean, full reset (no stale state) and
                # arm the re-engage lockout so a flapping pose cannot
                # instantly re-engage.
                self._full_reset(now)
            # else: within grace — baseline retained for quick resume.
            return entry

        self._candidate_frames = 0  # engage counting is frame-consecutive
        return entry

    # ── internals ───────────────────────────────────────────────────────

    def _update_engaged_or_counting(self, a, b, now, entry):
        ca = _hand_centroid(a.get("landmarks"))
        cb = _hand_centroid(b.get("landmarks"))
        if ca is None or cb is None:
            # Degenerate input mid-hold: treat as a failed condition.
            if self._engaged:
                self._miss_frames += 1
                if self._miss_frames > self.grace_frames:
                    self._full_reset(now)
                return self._inactive_report([])
            self._candidate_frames = 0
            return self._inactive_report([])

        if not self._engaged:
            # Cooldown lockout after a previous engaged state reset.
            if self._cooldown_until is not None and now < self._cooldown_until:
                self._candidate_frames = 0
                return self._inactive_report([a, b])
            self._candidate_frames += 1
            if self._candidate_frames < self.engage_frames:
                return self._report(counting=True, hands=[a, b])
            # ── ENGAGE: capture baseline ──
            first, second = self._order_by_handedness(a, b, ca, cb)
            self._engaged = True
            self._candidate_frames = 0
            self._miss_frames = 0
            self._base_a = first[1]
            self._base_b = second[1]
            self._base_dist = max(math.hypot(
                self._base_b[0] - self._base_a[0],
                self._base_b[1] - self._base_a[1]), 1e-9)
            self._base_angle_deg = math.degrees(math.atan2(
                self._base_b[1] - self._base_a[1],
                self._base_b[0] - self._base_a[0]))
            self._base_mid = ((self._base_a[0] + self._base_b[0]) / 2.0,
                              (self._base_a[1] + self._base_b[1]) / 2.0)
            # Engage frame is already an active HOLD report (baseline ==
            # current geometry: scale 1.0, angle 0, zero centroid delta).
            return self._report(counting=False, hands=[a, b],
                                slot0=first[0], slot1=second[0],
                                active=True, gesture=TWO_HAND_HOLD,
                                scale=1.0, angle_delta_deg=0.0,
                                centroid_delta=(0.0, 0.0))

        # ── ENGAGED frame: re-match hands to baseline slots (nearest-
        # centroid assignment) so label flicker / crossings stay stable ──
        first, second = self._match_slots(a, b, ca, cb)
        self._miss_frames = 0

        dist = math.hypot(second[1][0] - first[1][0],
                          second[1][1] - first[1][1])
        angle = math.degrees(math.atan2(second[1][1] - first[1][1],
                                        second[1][0] - first[1][0]))
        mid = ((first[1][0] + second[1][0]) / 2.0,
               (first[1][1] + second[1][1]) / 2.0)

        scale = dist / self._base_dist
        angle_delta = _wrap180(angle - self._base_angle_deg)
        cdelta = (mid[0] - self._base_mid[0], mid[1] - self._base_mid[1])

        gesture = self._classify(scale, angle_delta, cdelta)
        return self._report(counting=False, hands=[a, b],
                            slot0=first[0], slot1=second[0],
                            active=True, gesture=gesture,
                            scale=scale, angle_delta_deg=angle_delta,
                            centroid_delta=cdelta)

    def _classify(self, scale, angle_delta, cdelta):
        """Dominant-delta classification: the largest deadzone-normalized
        score wins; exact ties resolve ZOOM > ROTATE > DRAG (deterministic).
        Nothing above its deadzone -> plain HOLD."""
        zoom_score = abs(scale - 1.0) / max(self.zoom_deadzone, 1e-9)
        rot_score = abs(angle_delta) / max(self.rotate_deadzone_deg, 1e-9)
        drag_score = math.hypot(cdelta[0], cdelta[1]) / max(self.drag_deadzone, 1e-9)
        best = max(zoom_score, rot_score, drag_score)
        if best <= 1.0:
            return TWO_HAND_HOLD
        if best == zoom_score:
            return TWO_HAND_ZOOM
        if best == rot_score:
            return TWO_HAND_ROTATE
        return TWO_HAND_DRAG

    def _order_by_handedness(self, a, b, ca, cb):
        """Engage-time slot order: user's left hand first (is_left_user);
        fallback to leftmost centroid when labels are missing/ambiguous.
        Returns ((labelA, centroidA), (labelB, centroidB))."""
        la, lb = _label_of(a), _label_of(b)
        a_left = bool(a.get("is_left_user", False))
        b_left = bool(b.get("is_left_user", False))
        if a_left and not b_left:
            return (la, ca), (lb, cb)
        if b_left and not a_left:
            return (lb, cb), (la, ca)
        if ca[0] <= cb[0]:
            return (la, ca), (lb, cb)
        return (lb, cb), (la, ca)

    def _match_slots(self, a, b, ca, cb):
        """Assign current hands to baseline slots by minimal total centroid
        distance (2 permutations; identity wins exact ties)."""
        d_ident = (math.hypot(ca[0] - self._base_a[0], ca[1] - self._base_a[1])
                   + math.hypot(cb[0] - self._base_b[0], cb[1] - self._base_b[1]))
        d_swap = (math.hypot(cb[0] - self._base_a[0], cb[1] - self._base_a[1])
                  + math.hypot(ca[0] - self._base_b[0], ca[1] - self._base_b[1]))
        if d_swap < d_ident:
            return (_label_of(b), cb), (_label_of(a), ca)
        return (_label_of(a), ca), (_label_of(b), cb)

    def _confidence(self, hands, active_condition):
        """0..1 confidence: landmark completeness (2 full hands = 1.0 base,
        min over hands) scaled by the min per-hand pinch confidence
        clamp(1 - pinch/threshold).  0.0 whenever the hold condition is
        not currently holding."""
        if not active_condition or len(hands) < 2:
            return 0.0
        base = 1.0
        pinch_conf = 1.0
        for h in hands:
            n = len(h.get("landmarks") or [])
            base = min(base, min(n, _FULL_LM) / float(_FULL_LM))
            pinch_conf = min(pinch_conf, max(
                0.0, min(1.0, 1.0 - _pinch_of(h) / max(self.pinch_threshold, 1e-9))))
        return max(0.0, min(1.0, base * pinch_conf))

    def _report(self, counting, hands, slot0=None, slot1=None, active=False,
                gesture=None, scale=1.0, angle_delta_deg=0.0,
                centroid_delta=None):
        if counting:
            return {
                "active": False,
                "gesture": None,
                "scale": 1.0,
                "angle_delta_deg": 0.0,
                "centroid_delta": None,
                "confidence": self._confidence(hands, True),
                "handedness": tuple(_label_of(h) for h in hands),
            }
        if not active:
            return {
                "active": False,
                "gesture": None,
                "scale": 1.0,
                "angle_delta_deg": 0.0,
                "centroid_delta": None,
                "confidence": 0.0,
                "handedness": tuple(_label_of(h) for h in hands),
            }
        return {
            "active": True,
            "gesture": gesture,
            "scale": float(scale),
            "angle_delta_deg": float(angle_delta_deg),
            "centroid_delta": (None if centroid_delta is None else
                               (float(centroid_delta[0]),
                                float(centroid_delta[1]))),
            "confidence": self._confidence(hands, True),
            "handedness": (slot0, slot1),
        }

    def _inactive_report(self, hands):
        """Neutral report for non-active frames (never stale geometry)."""
        return self._report(counting=False, hands=hands, active=False)

    def _full_reset(self, now):
        """Grace expired: clear ALL state; arm the re-engage lockout."""
        self._candidate_frames = 0
        self._miss_frames = 0
        self._engaged = False
        self._base_a = self._base_b = self._base_mid = None
        self._base_dist = 0.0
        self._base_angle_deg = 0.0
        self._cooldown_until = now + self.cooldown_s
