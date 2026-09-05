"""
airmouse.gaze_academy — v16.5 GAZE ACADEMY: five progressive gaze lessons,
honest gaze metrics, and bounded LOCAL gaze personalization.

Mission position (v16.5 "Adaptive Multimodal Intelligence"):
    §7  — the Gaze Academy teaches gaze PROGRESSIVELY in exactly 5 lessons
          (acquire → fixation → dwell → blink → Eye Assist) and measures
          REAL metrics when a camera exists.
    §8  — personalization is LOCAL-ONLY with HARD BOUNDS: everything the
          learner knows lives at ``paths.profile_gaze_file()``
          (<home>/profile/gaze.json, written atomically via
          ``persistence.atomic_write_json``) and every learned parameter is
          clamped to its declared ``(lo, hi)`` bound before it is stored.
    §17 — PREDICTION ≠ EXECUTION.  ``GazeLearner.suggest()`` returns
          PROPOSALS ONLY.  A proposal NEVER applies itself; applying one
          requires an explicit later action by the coordinator or the user.

HONESTY CONTRACT (load-bearing)
-------------------------------
* This sandbox — and many machines — has NO camera.  Without ``camera``
  AND a ``gaze_source`` callable, ``run_gaze_academy`` prints an HONEST
  lesson plan containing the exact line::

      PHYSICAL PRACTICE REQUIRED — needs camera + eyes. I can teach you the
      concepts now. Physical camera lessons will begin when a webcam is
      available.

  …marks NOTHING as passed and returns ``physical_required: True``.
* A physical lesson is NEVER auto-passed.  In the live loop a lesson
  passes only when its success criteria are met from REAL samples
  delivered by the ``gaze_source`` callable.
* ``simulated=True`` runs the SAME measurement pipeline on SYNTHETIC
  samples as a labeled dry-run: lessons may pass, but every result and
  every rendered line is labeled ``SIMULATED`` and is never presented as
  physical performance.
* Unverified (simulated / non-sensor) observations ARE recorded in the
  learner but carry a ``"simulated": true`` flag and are NEVER used for
  suggestions or learned parameters (documented choice).
* The runner NEVER blocks a non-TTY stdin: it never calls ``input()``;
  prompting happens only through a caller-supplied ``input_fn``.
* The runner is side-effect free on disk (no progress file): the
  coordinator feeds ``result["lessons"]`` metrics into
  ``GazeLearner.record_lesson`` (with ``verified=True`` only for real
  camera runs).  Guidance points to the REAL calibration command
  (``airmouse --gaze-calibrate``) — the Academy teaches, it never
  replaces calibration.

THE SAMPLE SCHEMA (the unit of measurement)
-------------------------------------------
Every metric function consumes a list of dicts — one dict per frame.
All fields are optional per-dict; malformed entries are skipped, never
raise::

    {
      "t":           float   # seconds on a monotonic-style clock,
                             # strictly increasing within a recording
      "x":           float|None  # smoothed gaze point, normalized frame
                                 # space [0, 1]; None when the face/eyes
                                 # are not visible
      "y":           float|None  # as "x"
      "eye_closed":  bool|None    # True = eyelids closed (EAR proxy);
                                  # None = unknown (ignored)
      "confidence":  float    # estimator confidence in [0, 1];
                              # 0.0 when nothing is tracked
      "hand_confirmed": bool|None  # optional (lesson l5_eye_assist):
                                   # True when the confirm gesture
                                   # (hand raise/pinch) is observed
    }

A fallback eye-aspect proxy is honored: when ``eye_closed`` is absent, a
finite ``"ear"`` field below 0.18 (the GazeEstimator open threshold)
counts as closed.

A target *region* is ``{"x": cx, "y": cy, "r": radius}`` or a
``(cx, cy, radius)`` sequence in the same normalized frame space; the
default is the screen-centre circle (GAZE_DEFAULT_REGION).

Design rules (house style): heavy deps are never imported here (stdlib
only), every function degrades gracefully on empty/garbage input
(returning ``None`` / ``0.0`` / ``[]`` / ``False`` instead of raising),
and the module imports headless with zero hardware present.

Classes:
    GazeLearner — bounded local gaze personalization (§8/§17)

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import copy
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import paths

__all__ = [
    # curriculum + docs (for the teacher module / renderers)
    "GAZE_LESSONS", "GAZE_SAMPLE_SCHEMA", "GAZE_DEFAULT_REGION",
    "gaze_academy_plan", "lesson_ids",
    # pure metric functions
    "gaze_in_region",
    "acquisition_time", "jitter_score", "stability_ratio", "fixation_hold",
    "dwell_verified", "blink_events", "blink_gaze_lock", "drift_score",
    "mean_confidence", "activation_runs",
    # grading (pure)
    "lesson_metrics", "lesson_passed",
    # bounded personalization
    "BOUNDS", "GazeLearner",
    # runner
    "MEASURE_BUDGET_S", "run_gaze_academy",
]

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

#: minimum in-region stay for ``acquisition_time`` to declare success
ACQUIRE_MIN_HOLD_S = 0.5

#: dwell threshold taught in lesson l3 — aligned with gaze.DwellDetector's
#: default ``dwell_time_s`` (0.80) so the lesson practices the REAL pipeline
DWELL_TEACH_S = 0.80

#: wall-clock measurement budget per live lesson (seconds)
MEASURE_BUDGET_S = 12.0

#: hard cap on gaze_source() calls per lesson (pathological-source guard)
_MAX_SOURCE_CALLS = 20000

#: EAR below this counts as closed when a sample carries only an "ear"
#: proxy (same threshold as GazeEstimator.open_threshold)
_EAR_CLOSED = 0.18

#: verified dwell-observation history kept for suggestions
_DWELL_HISTORY_MAX = 20

#: hard bounds on every learned parameter (mission §8).  Float bounds are
#: ``(lo, hi)`` clamps; ``dominant_regions_max`` is a hard list limit.
BOUNDS: Dict[str, Any] = {
    "preferred_dwell_s": (0.3, 2.0),
    "calibration_quality": (0.0, 1.0),
    "jitter": (0.0, 1.0),
    "acquisition_s": (0.05, 30.0),
    "dominant_regions_max": 8,
}

#: default target region: the screen-centre circle (normalized frame space)
GAZE_DEFAULT_REGION: Dict[str, float] = {"x": 0.5, "y": 0.5, "r": 0.1}

#: documented sample schema (exported for the teacher module / renderer)
GAZE_SAMPLE_SCHEMA: Dict[str, str] = {
    "t": "float — seconds on a monotonic-style clock; strictly increasing "
         "within a recording",
    "x": "float|None — smoothed gaze point, normalized frame space [0,1]; "
         "None when the face/eyes are not visible",
    "y": "float|None — as x",
    "eye_closed": "bool|None — True when the eyelids are closed (EAR "
                  "proxy); None = unknown",
    "confidence": "float — estimator confidence in [0,1]; 0.0 when nothing "
                  "is tracked",
    "hand_confirmed": "bool|None — optional (lesson l5_eye_assist): True "
                      "when the confirm gesture (hand raise/pinch) is "
                      "observed",
    "ear": "float — optional eye-aspect proxy; counts as closed below 0.18 "
           "when eye_closed is absent",
}

#: status vocabulary mirrors academy.py / guided_test.py honesty contract
STATUS_PHYSICAL = ("PHYSICAL PRACTICE REQUIRED — needs camera + eyes. "
                   "I can teach you the concepts now. Physical camera "
                   "lessons will begin when a webcam is available.")
STATUS_SIMULATED = ("SIMULATED — synthetic samples exercised the "
                    "measurement pipeline; this is NOT physical "
                    "performance.")
STATUS_LIVE = ("LIVE MEASUREMENT — real gaze samples from your camera; a "
               "lesson passes only when criteria are met from these real "
               "samples.")


# ---------------------------------------------------------------------------
# the curriculum (mission §7 — exactly five lessons, ordered)
# ---------------------------------------------------------------------------

GAZE_LESSONS: List[Dict[str, Any]] = [
    {
        "id": "l1_acquire",
        "title": "Look at the circle.",
        "track": "gaze",
        "instruction": "A circle appears on screen. Move only your eyes — "
                       "bring the cursor into the circle and keep it "
                       "there for a moment.",
        "metrics": ["acquisition_time_s", "stability", "confidence",
                    "jitter"],
        "requires": "camera + eyes",
        "success_criteria": {"acquire_within_s": 3.0, "stability_min": 0.5},
        "success_criteria_text": "acquire the target within 3.0 s and keep "
                                 "stability ≥ 0.5",
        "tips": [
            "Sit about 50-80 cm from the camera with even lighting on "
            "your face.",
            "Move your eyes first, head second — small, relaxed looks "
            "read best.",
            "If it feels hard, run airmouse --gaze-calibrate once; a "
            "calibrated map makes the circle follow your eyes closely.",
        ],
        "next_step": "airmouse --gaze-calibrate",
        "measure_s": MEASURE_BUDGET_S,
    },
    {
        "id": "l2_fixation",
        "title": "Look at the target. Hold your gaze.",
        "track": "gaze",
        "instruction": "Look at the target and hold your gaze steady. "
                       "Drifting off is fine — just come back and keep "
                       "the cursor inside the target.",
        "metrics": ["hold_duration_s", "drift"],
        "requires": "camera + eyes",
        "success_criteria": {"hold_min_s": 1.0},
        "success_criteria_text": "one continuous hold ≥ 1.0 s",
        "tips": [
            "Rest your eyes on a small detail of the target, not its "
            "edge.",
            "A quiet blink is fine — it does not end the hold.",
            "Relax your jaw and shoulders; tension makes gaze jittery.",
        ],
        "next_step": None,
        "measure_s": MEASURE_BUDGET_S,
    },
    {
        "id": "l3_dwell",
        "title": "Look → hold → activate",
        "track": "gaze",
        "instruction": "Look at the target and keep looking — after the "
                       "dwell time the item activates with no click at "
                       "all. That patient look IS your mouse button.",
        "metrics": ["dwell_threshold_s", "activations"],
        "requires": "camera + eyes",
        "success_criteria": {"dwell_s": DWELL_TEACH_S, "min_activations": 1},
        "success_criteria_text": "≥ 1 dwell activation at the "
                                 f"{DWELL_TEACH_S:.2f} s threshold",
        "tips": [
            "Keep looking through the activation — the click happens "
            "while you hold.",
            "Look AWAY between activations so the next one re-arms.",
            "If activations fire too early for your taste, the learner "
            "can propose a longer dwell (you always apply it yourself).",
        ],
        "next_step": None,
        "measure_s": MEASURE_BUDGET_S,
    },
    {
        "id": "l4_blink",
        "title": "Look at target. Blink.",
        "track": "gaze",
        "instruction": "Look at the target and blink naturally. Your gaze "
                       "must stay on the target through the blink — the "
                       "cursor must not jump when your eyes close.",
        "metrics": ["blink_detected", "gaze_lock_during_blink"],
        "requires": "camera + eyes",
        "success_criteria": {"min_blinks": 1, "need_gaze_lock": True},
        "success_criteria_text": "≥ 1 blink detected with the gaze locked "
                                 "on the target",
        "tips": [
            "A soft, normal blink is enough — no squeezing.",
            "Keep looking at the target while the eyelid closes and "
            "opens.",
            "A long deliberate eye-close is a different signal (the "
            "e-stop) — keep this blink short.",
        ],
        "next_step": None,
        "measure_s": MEASURE_BUDGET_S,
    },
    {
        "id": "l5_eye_assist",
        "title": "Look at target. Use your hand to confirm.",
        "track": "gaze",
        "instruction": "Look at the target, then raise your hand (the "
                       "confirm gesture). This is Eye Assist Mode: your "
                       "eyes SELECT, your hand CONFIRMS — two steps keep "
                       "accidental looks from activating anything.",
        "metrics": ["gaze_target_locked", "confirm_observed"],
        "requires": "camera + eyes (+ hand for the confirm gesture)",
        "success_criteria": {"need_lock": True, "need_confirm": True},
        "success_criteria_text": "gaze locked on the target AND the "
                                 "confirm gesture observed",
        "tips": [
            "Eyes first, then hand — the order is the safety.",
            "Any clear hand raise reads as the confirm; keep it "
            "deliberate.",
            "Eye Assist is the gentlest mode: great when you are "
            "learning or sharing a screen.",
        ],
        "next_step": None,
        "measure_s": MEASURE_BUDGET_S,
    },
]

#: valid lesson ids (derived once — never hand-maintained twice)
_LESSON_IDS = [l["id"] for l in GAZE_LESSONS]

#: lessons keyed by id (read-only use)
_LESSON_BY_ID = {l["id"]: l for l in GAZE_LESSONS}


def lesson_ids() -> List[str]:
    """The five valid lesson ids, in curriculum order."""
    return list(_LESSON_IDS)


def gaze_academy_plan(lesson: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return the curriculum as plain data (copies — callers cannot mutate).

    ``lesson=None`` / ``""`` / ``"all"`` returns every lesson; a single
    id returns just that lesson; an unknown id returns ``[]`` (the
    runner treats that as an honest unknown-lesson error).
    """
    if lesson is None or str(lesson).strip().lower() in ("", "all"):
        chosen = GAZE_LESSONS
    else:
        lid = str(lesson).strip().lower()
        chosen = [l for l in GAZE_LESSONS if l["id"] == lid]
    return copy.deepcopy(list(chosen))


# ---------------------------------------------------------------------------
# small safe primitives (never raise on garbage)
# ---------------------------------------------------------------------------

def _opt_float(v: Any) -> Optional[float]:
    """Finite float or None (bools and junk rejected)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _as_region(region: Any) -> Tuple[float, float, float]:
    """Normalize a region spec to (cx, cy, r); garbage → default centre."""
    reg = (GAZE_DEFAULT_REGION["x"], GAZE_DEFAULT_REGION["y"],
           GAZE_DEFAULT_REGION["r"])
    if region is None:
        return reg
    try:
        if isinstance(region, dict):
            cx = float(region["x"])
            cy = float(region["y"])
            r = float(region.get("r", region.get("radius", reg[2])))
        else:
            cx = float(region[0])
            cy = float(region[1])
            r = float(region[2])
    except (TypeError, ValueError, KeyError, IndexError):
        return reg
    if not (math.isfinite(cx) and math.isfinite(cy) and math.isfinite(r)):
        return reg
    return (cx, cy, min(1.0, max(1e-4, r)))


def gaze_in_region(point: Any, region: Any = None) -> bool:
    """True when *point* (``{"x","y"}`` or ``(x, y)``) sits inside *region*.

    Missing / non-finite coordinates are honestly OUT (False), never an
    exception.
    """
    reg = _as_region(region)
    if isinstance(point, dict):
        x, y = point.get("x"), point.get("y")
    else:
        try:
            x, y = point[0], point[1]
        except (TypeError, KeyError, IndexError):
            return False
    fx, fy = _opt_float(x), _opt_float(y)
    if fx is None or fy is None:
        return False
    return math.hypot(fx - reg[0], fy - reg[1]) <= reg[2]


def _valid_points(samples: Any) -> List[Tuple[float, float, float]]:
    """[(t, x, y)] from a sample list — sorted by t, garbage dropped."""
    out: List[Tuple[float, float, float]] = []
    if not isinstance(samples, (list, tuple)):
        return out
    for s in samples:
        if not isinstance(s, dict):
            continue
        t = _opt_float(s.get("t"))
        x = _opt_float(s.get("x"))
        y = _opt_float(s.get("y"))
        if t is None or x is None or y is None:
            continue
        out.append((t, x, y))
    out.sort(key=lambda p: p[0])
    return out


def _dicts(samples: Any) -> List[dict]:
    """The dict entries of *samples* (any iterable garbage tolerated)."""
    if not isinstance(samples, (list, tuple)):
        return []
    return [s for s in samples if isinstance(s, dict)]


def _region_runs(points: List[Tuple[float, float, float]],
                 region: Any) -> List[Tuple[float, float]]:
    """(t_enter, t_last) runs of consecutive in-region samples.

    Sampling-gap semantics: a run bridges the gap between consecutive
    in-region samples (frame-stream assumption) and BREAKS on any
    out-of-region or non-visible (x/y None) sample — losing the face is
    never silently bridged.
    """
    reg = _as_region(region)
    runs: List[Tuple[float, float]] = []
    cur: Optional[List[float]] = None
    for t, x, y in points:
        if math.hypot(x - reg[0], y - reg[1]) <= reg[2]:
            if cur is None:
                cur = [t, t]
            else:
                cur[1] = t
        else:
            if cur is not None:
                runs.append((cur[0], cur[1]))
                cur = None
    if cur is not None:
        runs.append((cur[0], cur[1]))
    return runs


# ---------------------------------------------------------------------------
# pure metric functions (fully unit-testable, garbage-safe)
# ---------------------------------------------------------------------------

def acquisition_time(samples: Any, target_region: Any,
                     now_fn: Optional[Callable[[], float]] = None,
                     min_hold: float = ACQUIRE_MIN_HOLD_S) -> Optional[float]:
    """Seconds from the first valid sample until the gaze had entered
    *target_region* and STAYED for ``min_hold`` — i.e. the moment the
    acquisition is proven: ``(t_enter + min_hold) - t_start``.

    * ``None`` when the gaze never acquired the target (or the input is
      empty/garbage).
    * ``now_fn`` (a zero-arg callable returning seconds on the SAME clock
      as the samples' ``t``) lets the trailing run — still inside the
      region when the recording ends — be credited up to "now".  Without
      it, evidence ends at the last sample's own timestamp (deterministic).
    """
    pts = _valid_points(samples)
    if not pts:
        return None
    if min_hold is None or min_hold < 0.0:
        min_hold = 0.0
    t0 = pts[0][0]
    runs = _region_runs(pts, target_region)
    best: Optional[float] = None

    def _offer(cand: float) -> None:
        nonlocal best
        if best is None or cand < best:
            best = cand

    for i, (t_enter, t_last) in enumerate(runs):
        dur = t_last - t_enter
        is_last = (i == len(runs) - 1)
        if dur >= min_hold:
            _offer(t_enter + min_hold - t0)
        elif is_last and now_fn is not None:
            now = _opt_float(now_fn())
            if now is not None and now > t_last and (now - t_enter) >= min_hold:
                _offer(t_enter + min_hold - t0)
    return best


def jitter_score(samples: Any, region: Any = None) -> float:
    """Mean frame-to-frame deviation of the gaze point (0.0 = perfect).

    Units are the samples' own (normalized) space.  With *region*, only
    in-region points participate.  Empty/garbage input → 0.0.
    """
    pts = _valid_points(samples)
    if region is not None:
        reg = _as_region(region)
        pts = [p for p in pts
               if math.hypot(p[1] - reg[0], p[2] - reg[1]) <= reg[2]]
    if len(pts) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        total += math.hypot(b[1] - a[1], b[2] - a[2])
    return total / (len(pts) - 1)


def stability_ratio(samples: Any, target_region: Any,
                    min_hold: float = 0.0) -> float:
    """Fraction (0..1) of the observed time the gaze spends in *region*
    across runs lasting ≥ *min_hold* seconds.

    With ``min_hold=0`` this is simply the in-region time fraction; a
    positive ``min_hold`` ignores brief crossings.  Zero-span or garbage
    input → 0.0.
    """
    pts = _valid_points(samples)
    if len(pts) < 1:
        return 0.0
    span = pts[-1][0] - pts[0][0]
    if span <= 0.0:
        return 0.0
    if min_hold is None or min_hold < 0.0:
        min_hold = 0.0
    inside = sum((tl - te) for te, tl in _region_runs(pts, target_region)
                 if (tl - te) >= min_hold)
    return min(1.0, max(0.0, inside / span))


def fixation_hold(samples: Any, target_region: Any) -> Optional[float]:
    """Longest continuous in-region duration, or None on empty/garbage
    input; 0.0 when there is evidence but the gaze never entered."""
    pts = _valid_points(samples)
    if not pts:
        return None
    runs = _region_runs(pts, target_region)
    if not runs:
        return 0.0
    return max(tl - te for te, tl in runs)


def dwell_verified(samples: Any, target_region: Any,
                   threshold_s: float) -> bool:
    """True when the gaze stayed in *region* continuously ≥ *threshold_s*
    (the sample stream itself is the evidence — exactly-threshold holds
    count).  Garbage input → False."""
    if threshold_s is None or threshold_s < 0.0:
        threshold_s = 0.0
    pts = _valid_points(samples)
    if not pts:
        return False
    return any((tl - te) >= threshold_s
               for te, tl in _region_runs(pts, target_region))


def blink_events(samples: Any) -> List[Tuple[float, float]]:
    """(t_start, t_end) closure episodes from ``eye_closed`` flags (or the
    ``ear`` proxy).  An episode opens on the first True and closes on the
    first explicit False; an episode still open at the end of the
    recording is NOT reported (an end time is never fabricated)."""
    flags: List[Tuple[float, bool]] = []
    for s in _dicts(samples):
        t = _opt_float(s.get("t"))
        if t is None:
            continue
        ec = s.get("eye_closed")
        if isinstance(ec, bool):
            closed = ec
        elif isinstance(ec, int):
            closed = bool(ec)
        else:
            ear = _opt_float(s.get("ear"))
            if ear is None:
                continue  # unknown eye state — honest skip, never guessed
            closed = ear < _EAR_CLOSED
        flags.append((t, closed))
    flags.sort(key=lambda p: p[0])
    events: List[Tuple[float, float]] = []
    onset: Optional[float] = None
    for t, closed in flags:
        if closed:
            if onset is None:
                onset = t
        elif onset is not None:
            events.append((onset, t))
            onset = None
    return events


def blink_gaze_lock(samples: Any, target_region: Any) -> bool:
    """True when ≥ 1 blink kept the gaze on the target: the last valid
    point before the blink and the first valid point after it are both
    inside *region* (the cursor must not jump when eyes close)."""
    events = blink_events(samples)
    if not events:
        return False
    pts = _valid_points(samples)
    if not pts:
        return False
    for t_start, t_end in events:
        # before: strictly before the onset sample (which is mid-close);
        # after: from the reopen sample on — the reopen IS the post-blink
        # gaze, so it is legitimate evidence of where the eyes landed.
        before = [p for p in pts if p[0] < t_start]
        after = [p for p in pts if p[0] >= t_end]
        if before and after \
                and gaze_in_region((before[-1][1], before[-1][2]),
                                   target_region) \
                and gaze_in_region((after[0][1], after[0][2]),
                                   target_region):
            return True
    return False


def drift_score(samples: Any, target_region: Any) -> float:
    """Mean distance from the region centre over in-region samples
    (0.0 = perfectly centred; garbage → 0.0)."""
    pts = _valid_points(samples)
    reg = _as_region(target_region)
    inside = [p for p in pts
              if math.hypot(p[1] - reg[0], p[2] - reg[1]) <= reg[2]]
    if not inside:
        return 0.0
    return sum(math.hypot(p[1] - reg[0], p[2] - reg[1]) for p in inside) \
        / len(inside)


def mean_confidence(samples: Any) -> float:
    """Mean estimator confidence over samples with a visible gaze point
    (0.0 when nothing measurable)."""
    vals: List[float] = []
    for s in _dicts(samples):
        if _opt_float(s.get("x")) is None or _opt_float(s.get("y")) is None:
            continue
        c = _opt_float(s.get("confidence"))
        if c is not None:
            vals.append(min(1.0, max(0.0, c)))
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def activation_runs(samples: Any, target_region: Any,
                    threshold_s: float) -> int:
    """Number of distinct in-region runs lasting ≥ *threshold_s* (each is
    one dwell activation)."""
    if threshold_s is None or threshold_s < 0.0:
        threshold_s = 0.0
    pts = _valid_points(samples)
    if not pts:
        return 0
    return sum(1 for te, tl in _region_runs(pts, target_region)
               if (tl - te) >= threshold_s)


# ---------------------------------------------------------------------------
# per-lesson metrics + grading (pure)
# ---------------------------------------------------------------------------

def lesson_metrics(lesson_id: str, samples: Any, region: Any = None,
                   now_fn: Optional[Callable[[], float]] = None) -> dict:
    """Compute a lesson's declared metrics from a sample list (pure).

    Unknown lesson ids yield ``{}``.  Metrics use the sample stream's own
    timeline; ``now_fn`` (same clock as ``t``) only extends the trailing
    hold for the acquisition metric.
    """
    lid = str(lesson_id or "")
    reg = region if region is not None else dict(GAZE_DEFAULT_REGION)
    if lid == "l1_acquire":
        acq = acquisition_time(samples, reg, now_fn=now_fn,
                               min_hold=ACQUIRE_MIN_HOLD_S)
        return {
            "acquisition_time_s": acq,
            "stability": stability_ratio(samples, reg,
                                         min_hold=ACQUIRE_MIN_HOLD_S),
            "confidence": mean_confidence(samples),
            "jitter": jitter_score(samples, reg),
        }
    if lid == "l2_fixation":
        fh = fixation_hold(samples, reg)
        return {
            "hold_duration_s": fh if fh is not None else 0.0,
            "drift": drift_score(samples, reg),
        }
    if lid == "l3_dwell":
        fh = fixation_hold(samples, reg)
        return {
            "dwell_threshold_s": DWELL_TEACH_S,
            "activations": activation_runs(samples, reg, DWELL_TEACH_S),
            "longest_hold_s": fh if fh is not None else 0.0,
        }
    if lid == "l4_blink":
        return {
            "blink_detected": len(blink_events(samples)),
            "gaze_lock_during_blink": blink_gaze_lock(samples, reg),
        }
    if lid == "l5_eye_assist":
        fh = fixation_hold(samples, reg)
        confirmed = any(s.get("hand_confirmed") is True
                        for s in _dicts(samples))
        return {
            "gaze_target_locked": bool(fh is not None and fh >= 1.0),
            "confirm_observed": confirmed,
        }
    return {}


def lesson_passed(lesson_id: str, metrics: Any) -> bool:
    """Grade computed metrics against the lesson's success_criteria
    (pure, honest — unknown lessons and garbage metrics never pass)."""
    lesson = _LESSON_BY_ID.get(str(lesson_id or ""))
    if lesson is None or not isinstance(metrics, dict):
        return False
    c = lesson["success_criteria"]
    if lesson["id"] == "l1_acquire":
        acq = _opt_float(metrics.get("acquisition_time_s"))
        stab = _opt_float(metrics.get("stability"))
        return (acq is not None and acq <= float(c["acquire_within_s"])
                and stab is not None and stab >= float(c["stability_min"]))
    if lesson["id"] == "l2_fixation":
        hold = _opt_float(metrics.get("hold_duration_s"))
        return hold is not None and hold >= float(c["hold_min_s"])
    if lesson["id"] == "l3_dwell":
        acts = _opt_float(metrics.get("activations"))
        return acts is not None and acts >= float(c["min_activations"])
    if lesson["id"] == "l4_blink":
        n = metrics.get("blink_detected")
        n_f = _opt_float(n) if not isinstance(n, bool) else None
        return (n_f is not None and n_f >= float(c["min_blinks"])
                and metrics.get("gaze_lock_during_blink") is True)
    if lesson["id"] == "l5_eye_assist":
        return (metrics.get("gaze_target_locked") is True
                and metrics.get("confirm_observed") is True)
    return False


# ---------------------------------------------------------------------------
# GazeLearner — bounded LOCAL personalization (mission §8 + §17)
# ---------------------------------------------------------------------------

def _default_store() -> Dict[str, Any]:
    """Fail-safe defaults (also what a corrupted file degrades to)."""
    return {
        "schema_version": 1,
        "calibration_quality": None,
        "preferred_dwell_s": None,
        "jitter": None,
        "acquisition_s": None,
        "dominant_regions": [],
        "updated_at": None,
        # -- bounded bookkeeping below (capped lists, known ids only) ----
        "verified_lessons": {},     # lid -> {passes, attempts}
        "simulated_lessons": {},    # lid -> {attempts, simulated: true}
        "dwell_holds": [],          # verified dwell activation holds
        "region_counts": {},        # region name -> count (capped keys)
        "obs_counts": {},           # observations per learned param
    }


def _pos_int(v: Any) -> int:
    n = _opt_float(v)
    return int(n) if n is not None and n > 0 else 0


def _coerce_store(data: Any) -> Dict[str, Any]:
    """Validate an on-disk store field-by-field; anything malformed
    degrades to its default (never raises, never trusts junk)."""
    d = _default_store()
    if not isinstance(data, dict):
        return d
    sv = data.get("schema_version")
    d["schema_version"] = sv if isinstance(sv, int) and sv >= 1 else 1
    for key in ("calibration_quality", "preferred_dwell_s", "jitter",
                "acquisition_s"):
        v = _opt_float(data.get(key))
        if v is not None:
            lo, hi = BOUNDS[key]
            d[key] = min(hi, max(lo, v))
    regions = data.get("dominant_regions")
    if isinstance(regions, list):
        d["dominant_regions"] = [str(r)[:48] for r in regions
                                 if isinstance(r, str) and r.strip()]
    rc = data.get("region_counts")
    if isinstance(rc, dict):
        for k, v in rc.items():
            if isinstance(k, str) and k.strip() and isinstance(v, int) \
                    and not isinstance(v, bool):
                d["region_counts"][k[:48]] = v
    vl = data.get("verified_lessons")
    if isinstance(vl, dict):
        for lid, st in vl.items():
            if isinstance(lid, str) and lid in _LESSON_IDS \
                    and isinstance(st, dict):
                d["verified_lessons"][lid] = {
                    "passes": _pos_int(st.get("passes")),
                    "attempts": _pos_int(st.get("attempts")),
                }
    sl = data.get("simulated_lessons")
    if isinstance(sl, dict):
        for lid, st in sl.items():
            if isinstance(lid, str) and lid in _LESSON_IDS \
                    and isinstance(st, dict):
                d["simulated_lessons"][lid] = {
                    "attempts": _pos_int(st.get("attempts")),
                    "simulated": True,
                }
    dh = data.get("dwell_holds")
    if isinstance(dh, list):
        d["dwell_holds"] = [round(v, 4) for v in
                            (_opt_float(x) for x in dh)
                            if v is not None and v > 0][:_DWELL_HISTORY_MAX]
    oc = data.get("obs_counts")
    if isinstance(oc, dict):
        for k, v in oc.items():
            if isinstance(k, str) and k in BOUNDS:
                d["obs_counts"][k] = _pos_int(v)
    ua = data.get("updated_at")
    d["updated_at"] = ua if isinstance(ua, str) else None
    _refresh_regions(d)
    return d


def _refresh_regions(d: Dict[str, Any]) -> None:
    """Rebuild dominant_regions (top-N by count, name-tiebroken) and trim."""
    counts = d.get("region_counts") or {}
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    d["dominant_regions"] = [k for k, _ in
                             ranked[:BOUNDS["dominant_regions_max"]]]


class GazeLearner:
    """Bounded local gaze personalization (mission §8 + §17).

    * Stores LOCALLY at ``paths.profile_gaze_file()`` (or an explicit
      ``path=`` override for tests) via ``persistence.atomic_write_json``
      — crash-safe, checksum-free plain JSON under the AirMouse home.
    * HARD BOUNDS: every learned float is clamped to ``BOUNDS[key]``
      before storage and again on load; ``dominant_regions`` is trimmed
      to at most ``BOUNDS["dominant_regions_max"]`` (8).
    * PREDICTION ≠ EXECUTION (§17): ``suggest()`` returns PROPOSALS ONLY —
      every key except ``"reason"`` is a parameter proposal, already
      clamped, and NOTHING applies itself.  Applying a proposal requires
      an explicit later action by the coordinator or the user.
    * SIMULATED observations (``record_lesson(..., verified=False)``) ARE
      recorded — with a ``"simulated": true`` flag — and are NEVER used
      for suggestions or learned parameters (documented choice).
    * ``load()`` / ``save()`` are atomic and NEVER raise; a corrupted
      file degrades to fail-safe defaults with ``corrupted_last_load``
      set so the UI can say so honestly.
    """

    def __init__(self, path: Optional[str] = None):
        self._path_override = str(path) if path else None
        self.corrupted_last_load = False
        self._data = _default_store()
        try:
            self.load()
        except Exception:          # belt & braces — load never raises
            self._data = _default_store()

    # -- path ----------------------------------------------------------------

    def _path(self) -> str:
        """Active store path (dynamic — honors $AIRMOUSE_HOME changes)."""
        if self._path_override:
            return self._path_override
        try:
            return paths.profile_gaze_file()
        except Exception:
            return "/tmp/airmouse-profile-gaze.json"

    # -- persistence ---------------------------------------------------------

    def load(self) -> bool:
        """Load from disk; True when usable data was loaded.

        Missing file / unreadable location → False, flag False (an IO
        problem, not corruption).  Existing but undecodable/corrupt
        contents → False, ``corrupted_last_load`` True, fail-safe
        defaults.  NEVER raises.
        """
        try:
            from .persistence import read_json
            data = read_json(self._path())
        except FileNotFoundError:
            self.corrupted_last_load = False
            return False
        except OSError:
            # unreadable LOCATION (parent is a file, permissions, …) —
            # an IO problem, honestly not data corruption
            self.corrupted_last_load = False
            return False
        except Exception:
            # the file EXISTS but its contents are unusable → corruption
            self._data = _default_store()
            self.corrupted_last_load = True
            return False
        if not isinstance(data, dict) \
                or not isinstance(data.get("schema_version"), int):
            self._data = _default_store()
            self.corrupted_last_load = True
            return False
        self._data = _coerce_store(data)
        self.corrupted_last_load = False
        return True

    def save(self) -> bool:
        """Atomically persist the store; True on success.

        Any IO problem (read-only location, parent path is a file, …)
        returns False — never raises.
        """
        self._data["updated_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            from .persistence import atomic_write_json
            atomic_write_json(self._path(), self._data)
            return True
        except Exception:
            return False

    # -- observation ---------------------------------------------------------

    def record_lesson(self, lesson_id: str, metrics: Any,
                      verified: bool = True) -> bool:
        """Record one lesson outcome.

        ``verified=True`` (sensor-true, camera-live) updates the learned
        parameters (EMA, clamped to BOUNDS) and the lesson pass stats.
        ``verified=False`` (simulated / non-sensor) is RECORDED with a
        ``"simulated": true`` flag and NEVER touches learned parameters —
        so it can never skew a suggestion.

        Include ``"passed": True`` in *metrics* to count a pass.
        Returns False for unknown lesson ids (never raises).
        """
        lid = str(lesson_id or "")
        if lid not in _LESSON_IDS:
            return False
        m = metrics if isinstance(metrics, dict) else {}
        if verified:
            st = self._data["verified_lessons"].setdefault(
                lid, {"passes": 0, "attempts": 0})
            st["attempts"] = _pos_int(st.get("attempts")) + 1
            if m.get("passed") is True:
                st["passes"] = _pos_int(st.get("passes")) + 1
            if lid == "l1_acquire":
                self._learn("acquisition_s", m.get("acquisition_time_s"))
                self._learn("jitter", m.get("jitter"))
            elif lid == "l3_dwell" and _pos_int(m.get("activations")) >= 1:
                # only a real dwell activation demonstrates the user's
                # natural hold — a failed attempt teaches nothing here
                self._note_dwell_hold(m.get("longest_hold_s"))
            region = m.get("region")
            if isinstance(region, str) and region.strip():
                self._note_region(region)
        else:
            st = self._data["simulated_lessons"].setdefault(
                lid, {"attempts": 0, "simulated": True})
            st["attempts"] = _pos_int(st.get("attempts")) + 1
            st["simulated"] = True
        return True

    def record_calibration(self, quality: Any) -> bool:
        """Feed a verified calibration-quality observation (0..1, e.g. from
        ``GazeCalibration``); learned value stays clamped to BOUNDS."""
        q = _opt_float(quality)
        if q is None:
            return False
        self._learn("calibration_quality", q)
        return True

    def record_usage(self, dwell_used_s: Any = None,
                     region: Any = None) -> None:
        """Observe real usage stats (verified by construction: they come
        from the live pipeline) — dwell activations and where the user's
        gaze actually lands."""
        if dwell_used_s is not None:
            self._note_dwell_hold(dwell_used_s)
        if isinstance(region, str) and region.strip():
            self._note_region(region)

    # -- learning internals (all clamped) -------------------------------------

    def _learn(self, key: str, obs: Any) -> None:
        """EMA-learn *key* from one observation, clamped to BOUNDS."""
        val = _opt_float(obs)
        if val is None:
            return
        lo, hi = BOUNDS[key]
        val = min(hi, max(lo, val))
        n = _pos_int(self._data["obs_counts"].get(key))
        cur = _opt_float(self._data.get(key))
        if cur is None or n <= 0:
            new = val
        else:
            new = cur + (1.0 / (n + 1)) * (val - cur)
        self._data[key] = round(new, 4)
        self._data["obs_counts"][key] = n + 1

    def _note_dwell_hold(self, value: Any) -> None:
        """Learn preferred_dwell_s from one dwell observation.  ANY finite
        observation is clamped into BOUNDS (5.0 → 2.0, -1 → 0.3); only
        meaningful (positive) values also enter the suggestion history."""
        v = _opt_float(value)
        if v is None:
            return
        if v > 0.0:
            holds = self._data["dwell_holds"]
            holds.append(round(v, 4))
            if len(holds) > _DWELL_HISTORY_MAX:
                del holds[:len(holds) - _DWELL_HISTORY_MAX]
        self._learn("preferred_dwell_s", v)

    def _note_region(self, name: str) -> None:
        counts = self._data["region_counts"]
        key = str(name).strip()[:48]
        counts[key] = _pos_int(counts.get(key)) + 1
        if len(counts) > 64:            # bound the file, keep the common
            for k, _ in sorted(counts.items(), key=lambda kv: kv[1])[:16]:
                counts.pop(k, None)
        _refresh_regions(self._data)

    # -- suggestions (§17: PROPOSALS ONLY) -------------------------------------

    def suggest(self) -> Dict[str, Any]:
        """Return PROPOSALS ONLY — never applies anything (§17).

        Every key except ``"reason"`` is a parameter proposal, already
        clamped to its BOUNDS; applying it is an explicit later action by
        the coordinator or the user.  Simulated-only observations never
        produce a proposal.
        """
        proposals: Dict[str, Any] = {}
        holds = self._data.get("dwell_holds") or []
        if len(holds) >= 3:
            recent = holds[-5:]
            avg = sum(recent) / len(recent)
            lo, hi = BOUNDS["preferred_dwell_s"]
            val = round(min(hi, max(lo, avg)), 2)
            proposals["preferred_dwell_s"] = val
            cur = self._data.get("preferred_dwell_s")
            note = (f" (currently {float(cur):.2f} s)"
                    if isinstance(cur, (int, float)) else "")
            proposals["reason"] = (
                f"your last {len(recent)} dwell activations averaged "
                f"{avg:.2f} s → proposed dwell {val:.2f} s{note}. "
                "Review and apply it yourself — nothing changes until "
                "you do.")
        else:
            proposals["reason"] = (
                "not enough verified observations yet — complete gaze "
                "lessons with a camera to collect real data (simulated "
                "runs are recorded with a simulated flag and are never "
                "used for suggestions)")
        return proposals

    # -- read access -----------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """A deep copy of the store (callers cannot mutate the learner)."""
        return copy.deepcopy(self._data)


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------

def _normalize_sample(raw: Any, fallback_t: float) -> Optional[dict]:
    """Coerce one raw source dict into the documented sample schema;
    non-dicts → None (dropped)."""
    if not isinstance(raw, dict):
        return None
    t = _opt_float(raw.get("t"))
    if t is None:
        t = float(fallback_t)
    conf = _opt_float(raw.get("confidence"))
    conf = 0.0 if conf is None else min(1.0, max(0.0, conf))

    def _opt_bool(v: Any) -> Optional[bool]:
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return bool(v)
        return None

    return {
        "t": t,
        "x": _opt_float(raw.get("x")),
        "y": _opt_float(raw.get("y")),
        "eye_closed": _opt_bool(raw.get("eye_closed")),
        "confidence": conf,
        "hand_confirmed": _opt_bool(raw.get("hand_confirmed")),
    }


def _simulated_samples(lesson_id: str) -> List[dict]:
    """Deterministic SYNTHETIC sample streams for the labeled simulated
    dry-run.  Satisfy each lesson's criteria through the REAL metric
    pipeline — and are always labeled SIMULATED, never physical."""
    S: List[dict] = []

    def add(t: float, x: float = 0.5, y: float = 0.5,
            eye_closed: bool = False, conf: float = 0.9,
            hand: Optional[bool] = None) -> None:
        d = {"t": round(t, 3), "x": x, "y": y,
             "eye_closed": eye_closed, "confidence": conf}
        if hand is not None:
            d["hand_confirmed"] = hand
        S.append(d)

    if lesson_id == "l1_acquire":
        t = 0.0
        while t <= 3.5 + 1e-9:
            add(t, x=0.1 if t < 0.7 else 0.5)
            t += 0.1
    elif lesson_id == "l2_fixation":
        t = 0.0
        while t <= 1.4 + 1e-9:
            add(t)
            t += 0.1
    elif lesson_id == "l3_dwell":
        t = 0.0
        while t <= 2.0 + 1e-9:
            add(t)
            t += 0.1
    elif lesson_id == "l4_blink":
        t = 0.0
        while t <= 1.6 + 1e-9:
            add(t, eye_closed=abs(t - 1.0) < 0.05)
            t += 0.1
    elif lesson_id == "l5_eye_assist":
        t = 0.0
        while t <= 2.0 + 1e-9:
            add(t, hand=(t >= 1.2))
            t += 0.1
    return S


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return f"{float(v):.3f}"
    return "—" if v is None else str(v)


def _render_header(lines: List[str], mode_line: str) -> None:
    lines += [
        "",
        "  +==================================================+",
        "  |              AIRMOUSE GAZE ACADEMY               |",
        "  |    look → hold → dwell → blink → eye-assist      |",
        "  +==================================================+",
        "",
        mode_line,
        "",
    ]


def _render_lesson(lines: List[str], lesson: dict, idx: int, n: int) -> None:
    lines.append(f"  Lesson {idx}/{n} — {lesson['id']}: {lesson['title']}")
    lines.append(f"    what to do  : {lesson['instruction']}")
    lines.append("    will measure: " + ", ".join(lesson["metrics"]))
    lines.append(f"    pass        : {lesson['success_criteria_text']}")
    lines.append(f"    requires    : {lesson['requires']}")
    for tip in lesson["tips"]:
        lines.append(f"    tip         : {tip}")


def _flush(out: Any, lines: List[str]) -> None:
    """Write the rendered lines to *out* if it accepts .write — never
    raises, never prints to stdout on its own."""
    if out is None:
        return
    write = getattr(out, "write", None)
    if not callable(write):
        return
    try:
        for line in lines:
            write(line + "\n")
    except Exception:
        pass


def _measure_lesson(lesson: dict, gaze_source: Callable[[], Any],
                    input_fn: Optional[Callable[[str], Any]]) -> List[dict]:
    """The live measurement loop: pull samples from ``gaze_source`` until
    the lesson's criteria are met from REAL samples, the source ends the
    stream (None), or the wall-clock budget expires.

    Source contract: each call returns ONE sample dict (even "face lost"
    → x/y None, confidence 0) or ``None`` to end measurement.  The loop
    never sleeps and never blocks stdin; prompting happens only through
    a caller-supplied ``input_fn``.
    """
    if input_fn is not None:
        try:
            input_fn(f"  [{lesson['id']}] press Enter to start measuring "
                     f"({float(lesson.get('measure_s', MEASURE_BUDGET_S)):.0f}"
                     " s max)…")
        except Exception:
            pass
    budget = float(lesson.get("measure_s", MEASURE_BUDGET_S))
    samples: List[dict] = []
    start = time.monotonic()
    calls = 0
    lid = lesson["id"]
    while (time.monotonic() - start) < budget and calls < _MAX_SOURCE_CALLS:
        calls += 1
        try:
            raw = gaze_source()
        except Exception:
            break                    # a crashing source ends measurement
        if raw is None:
            break                    # end-of-stream contract
        s = _normalize_sample(raw, time.monotonic())
        if s is not None:
            samples.append(s)
        if lesson_passed(lid, lesson_metrics(lid, samples)):
            break
    return samples


def run_gaze_academy(lesson: str = "all",
                     out: Any = None,
                     input_fn: Optional[Callable[[str], Any]] = None,
                     camera: Any = None,
                     gaze_source: Any = None,
                     simulated: bool = False) -> dict:
    """Run the Gaze Academy and return a plain-data report.

    Modes (mutually exclusive, in priority order):
      * ``simulated=True``                     — labeled SIMULATED dry-run
        of the measurement pipeline on deterministic synthetic samples.
      * ``camera`` truthy AND ``gaze_source``  — live measurement; lessons
        pass only from REAL samples meeting the criteria.
      * otherwise                              — honest headless plan;
        NOTHING is passed; ``physical_required: True``.

    Returns::

        {"completed": bool,          # all selected lessons passed
         "physical_required": bool,  # True when no real measurement ran
         "simulated": bool,          # True for the labeled dry-run
         "rc": int,                  # 0 plan/measured · 1 unknown lesson
         "lessons": {id: {"passed": bool,
                          "metrics": {...},
                          "simulated": bool}},
         "output": [str, ...]}       # the exact rendered lines

    ``rc`` semantics (CLI convention, mirrors academy.run_academy):
    0 = plan rendered or lessons measured (even when nothing passed);
    1 = unknown lesson id.  The runner never blocks a non-TTY stdin
    (``input_fn`` is the only prompt path) and never writes to disk —
    feed ``result["lessons"]`` into ``GazeLearner.record_lesson``
    yourself, with ``verified=True`` only for real camera runs.
    """
    lines: List[str] = []

    def emit(s: str = "") -> None:
        lines.append(s)

    plan = gaze_academy_plan(lesson)
    if not plan:
        lid = str(lesson if lesson is not None else "")
        emit(f"  unknown gaze lesson '{lid}' — valid lesson ids: "
             + ", ".join(_LESSON_IDS))
        emit(STATUS_PHYSICAL)
        _flush(out, lines)
        return {"completed": False, "physical_required": True,
                "simulated": False, "rc": 1, "lessons": {},
                "output": lines}

    live = bool(camera) and callable(gaze_source) and not simulated
    results: Dict[str, Dict[str, Any]] = {}

    if live:
        _render_header(lines, "  " + STATUS_LIVE)
        n = len(plan)
        for i, les in enumerate(plan, 1):
            _render_lesson(lines, les, i, n)
            samples = _measure_lesson(les, gaze_source, input_fn)
            metrics = lesson_metrics(les["id"], samples)
            passed = lesson_passed(les["id"], metrics)
            if not samples:
                emit("    measured   : (no gaze samples arrived — nothing "
                      "was graded)")
            else:
                emit("    measured   : "
                     + "  ".join(f"{k}={_fmt(v)}"
                                 for k, v in metrics.items()))
            results[les["id"]] = {"passed": passed, "metrics": metrics,
                                  "simulated": False}
            emit("    result     : "
                 + ("PASSED — criteria met from REAL camera samples"
                    if passed else
                    "NOT PASSED — criteria not met yet; come back and try "
                    "again (a physical lesson is never auto-passed)"))
            emit("")
    elif simulated:
        _render_header(lines, "  SIMULATED DRY-RUN — no camera used; "
                       "synthetic samples through the real metric "
                       "pipeline; always labeled, never physical.")
        n = len(plan)
        for i, les in enumerate(plan, 1):
            _render_lesson(lines, les, i, n)
            samples = _simulated_samples(les["id"])
            metrics = lesson_metrics(les["id"], samples)
            passed = lesson_passed(les["id"], metrics)
            results[les["id"]] = {"passed": passed, "metrics": metrics,
                                  "simulated": True}
            emit("    measured   : "
                 + "  ".join(f"{k}={_fmt(v)}" for k, v in metrics.items()))
            emit("    result     : "
                 + ("PASSED (SIMULATED)" if passed else "NOT PASSED"))
            emit(f"    status     : {STATUS_SIMULATED}")
            emit("")
    else:
        _render_header(lines, "  Headless plan — no camera stream attached.")
        n = len(plan)
        for i, les in enumerate(plan, 1):
            _render_lesson(lines, les, i, n)
            emit(f"    status     : {STATUS_PHYSICAL}")
            if les.get("next_step"):
                emit(f"    next step  : {les['next_step']}")
            emit("")
            results[les["id"]] = {"passed": False, "metrics": {},
                                  "simulated": False}
        emit("  " + STATUS_PHYSICAL)
        emit("  Calibration pointer: airmouse --gaze-calibrate — the "
             "Academy teaches, calibration maps YOUR eyes; they "
             "complement each other.")
        emit("  Honesty contract: a physical lesson is never auto-passed; "
             "simulated runs are always labeled SIMULATED.")
        emit("")

    completed = bool(results) and all(
        r["passed"] for r in results.values())
    physical_required = not (live or simulated)
    emit(f"  Gaze Academy session — {sum(1 for r in results.values() if r['passed'])}"
         f"/{len(results)} lesson(s) passed"
         + ("  (SIMULATED dry-run — not physical performance)"
            if simulated else "")
         + ("  (physical practice still required)"
            if physical_required else ""))
    _flush(out, lines)
    return {
        "completed": completed,
        "physical_required": physical_required,
        "simulated": bool(simulated),
        "rc": 0,
        "lessons": results,
        "output": lines,
    }
