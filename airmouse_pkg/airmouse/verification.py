"""
airmouse.verification — v8 Post-Action Verification 🔍 + Recovery ♻️
====================================================================

Two cooperating pieces:

* :class:`ActionVerifier` — compares what was EXPECTED from an
  :class:`airmouse.interfaces.ActionPlan` with what was OBSERVED after
  execution (via an injected ``observe_fn``) and produces a
  :class:`airmouse.interfaces.VerificationResult`.
* :class:`RecoveryManager` — decides what to do after a failure:
  a RETRY → adjusted-retry (point nudged +12 px) → NOTIFY ladder, with an
  internal per-plan attempt ledger and a hard cap of 2 total attempts.
  Safety-blocked plans are FINAL (no retry), and plans that require
  confirmation NEVER auto-retry.

Observation contract
--------------------
``observe_fn(point_or_none) -> dict`` with known keys:

    pixel           (r, g, b)      — screen colour near the action point
    pointer         (x, y)         — actual pointer position
    window_title    str            — foreground window title
    content_offset  float          — scroll content offset (px)
    zoom_level      float          — current zoom factor
    present         bool           — element presence flag

Expected-type dispatch (documented per type in :data:`EXPECTED_TYPES`):

    click   PASSED when pixel changed vs the report baseline OR present
            changed OR the pointer ended within 30 px of the point
    pointer PASSED when dist(pointer, expected point) ≤ 30 px
            (similarity = 1 - dist/tolerance)
    scroll  PASSED when |content_offset| ≥ delta_min
    zoom    PASSED when |zoom_level delta vs baseline| ≥ delta_min
    key/window PASSED when window_title or present changed (baseline from
            report.observation["before_window_title"])

Missing expected / missing observer → NOT_NEEDED; missing observation keys
→ UNKNOWN.  FAILED results suggest RETRY.  All logic is pure + headless;
:meth:`ActionVerifier.make_observe_fn` is the optional best-effort real
observer (PIL ImageGrab, never raises).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional, Tuple

try:  # package-relative (normal import path)
    from .interfaces import (
        ActionPlan,
        ActionReport,
        ActionStatus,
        ObserveFn,
        RecoveryStrategy,
        VerificationResult,
        VerificationStatus,
        now_ts,
    )
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (
        ActionPlan,
        ActionReport,
        ActionStatus,
        ObserveFn,
        RecoveryStrategy,
        VerificationResult,
        VerificationStatus,
        now_ts,
    )

__all__ = [
    "DEFAULT_VERIFY_CONFIG",
    "EXPECTED_TYPES",
    "POINTER_TOLERANCE_PX",
    "CLICK_RADIUS_PX",
    "ActionVerifier",
    "RecoveryManager",
]

#: Documented configuration defaults.
DEFAULT_VERIFY_CONFIG: Dict[str, Any] = {
    "pointer_tolerance": 30.0,   # px — pointer/point agreement radius
    "click_radius": 30.0,        # px — click "arrived nearby" radius
    "nudge_px": 12.0,            # px — RecoveryManager adjusted-retry nudge
    "max_total_attempts": 2,     # hard cap of execution attempts per plan
}

#: Human-readable documentation of the expected-type evaluation table.
EXPECTED_TYPES: Dict[str, str] = {
    "click": "pixel changed OR present changed OR pointer within radius",
    "pointer": "dist(pointer, expected point) <= tolerance",
    "scroll": "|content_offset| >= delta_min",
    "zoom": "|zoom_level - baseline| >= delta_min",
    "key": "window_title or present changed",
    "window": "window_title or present changed",
}

POINTER_TOLERANCE_PX: float = 30.0
CLICK_RADIUS_PX: float = 30.0


def _dist(a: Optional[Tuple[float, float]], b: Optional[Tuple[float, float]]) -> Optional[float]:
    """Euclidean distance between two optional points (None-safe)."""
    if a is None or b is None:
        return None
    try:
        return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ActionVerifier
# ---------------------------------------------------------------------------


class ActionVerifier:
    """Verifies post-action screen state against plan expectations."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(DEFAULT_VERIFY_CONFIG)
        cfg.update(config or {})
        self.pointer_tolerance: float = float(cfg["pointer_tolerance"])
        self.click_radius: float = float(cfg["click_radius"])

    # -- entry point ---------------------------------------------------------

    def verify(
        self,
        plan: Optional[ActionPlan],
        report: Optional[ActionReport],
        observe_fn: Optional[ObserveFn] = None,
    ) -> VerificationResult:
        """Compare ``plan.expected`` with the observed post-action state.

        Never raises: an observer exception is treated as an empty
        observation (→ UNKNOWN).
        """
        expected = dict(getattr(plan, "expected", None) or {})
        if not expected or observe_fn is None:
            return VerificationResult(
                status=VerificationStatus.NOT_NEEDED,
                expected=expected,
                observed={},
                similarity=1.0,
                message="no expectation or no observer",
                suggested_recovery=RecoveryStrategy.NONE,
            )
        point = expected.get("point")
        if point is None:
            point = getattr(plan, "target_point", None)
        try:
            observed = observe_fn(point) or {}
        except Exception:
            observed = {}
        if not isinstance(observed, dict):
            observed = {}
        etype = str(expected.get("type", "")).lower()
        checker = self._CHECKS.get(etype)
        if checker is None:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                expected=expected,
                observed=observed,
                similarity=0.0,
                message=f"unknown expected type {etype!r}",
                suggested_recovery=RecoveryStrategy.NONE,
            )
        return checker(self, expected, report or ActionReport(), observed, point)

    # -- per-type checkers (dispatch table below) -----------------------------

    def _check_click(self, expected, report, obs, point) -> VerificationResult:
        """PASSED if pixel changed vs baseline OR present changed OR the
        pointer landed within the click radius; UNKNOWN with no signals."""
        base = dict(getattr(report, "observation", None) or {})
        pixel = obs.get("pixel")
        base_pixel = base.get("pixel")
        present = obs.get("present")
        base_present = base.get("present")
        pointer = obs.get("pointer")
        if pixel is None and present is None and pointer is None:
            return self._result(expected, obs, VerificationStatus.UNKNOWN,
                                0.0, "no observable signals")
        if pixel is not None and base_pixel is not None and pixel != base_pixel:
            return self._result(expected, obs, VerificationStatus.PASSED,
                                1.0, "pixel changed")
        if present is not None and base_present is not None and present != base_present:
            return self._result(expected, obs, VerificationStatus.PASSED,
                                1.0, "presence changed")
        if pixel is not None and base_pixel is None:
            return self._result(expected, obs, VerificationStatus.UNKNOWN,
                                0.0, "no baseline pixel")
        d = _dist(pointer, point)
        if d is not None:
            radius = float(expected.get("radius", self.click_radius))
            if d <= radius:
                return self._result(expected, obs, VerificationStatus.PASSED,
                                    max(0.0, 1.0 - d / radius), "pointer near point")
            return self._result(expected, obs, VerificationStatus.FAILED,
                                max(0.0, 1.0 - d / radius), "pointer far, no change")
        return self._result(expected, obs, VerificationStatus.FAILED,
                            0.0, "no state change detected")

    def _check_pointer(self, expected, report, obs, point) -> VerificationResult:
        """PASSED when the observed pointer is within tolerance of the point."""
        pointer = obs.get("pointer")
        if pointer is None:
            return self._result(expected, obs, VerificationStatus.UNKNOWN,
                                0.0, "pointer not observed")
        target = expected.get("point") or point
        d = _dist(pointer, target)
        if d is None:
            return self._result(expected, obs, VerificationStatus.UNKNOWN,
                                0.0, "no comparable point")
        tol = float(expected.get("tolerance", self.pointer_tolerance))
        similarity = max(0.0, 1.0 - d / tol) if tol > 0 else 0.0
        if d <= tol:
            return self._result(expected, obs, VerificationStatus.PASSED,
                                similarity, f"pointer within {d:.1f}px")
        return self._result(expected, obs, VerificationStatus.FAILED,
                            similarity, f"pointer {d:.1f}px away")

    def _check_scroll(self, expected, report, obs, point) -> VerificationResult:
        """PASSED when |content_offset| reaches delta_min."""
        offset = obs.get("content_offset")
        if offset is None:
            return self._result(expected, obs, VerificationStatus.UNKNOWN,
                                0.0, "content_offset not observed")
        try:
            moved = abs(float(offset))
        except Exception:
            return self._result(expected, obs, VerificationStatus.UNKNOWN,
                                0.0, "content_offset unreadable")
        delta_min = float(expected.get("delta_min", 1))
        if moved >= delta_min:
            return self._result(expected, obs, VerificationStatus.PASSED,
                                1.0, f"content moved {moved:.1f}px")
        return self._result(expected, obs, VerificationStatus.FAILED,
                            0.0, f"content moved only {moved:.1f}px")

    def _check_zoom(self, expected, report, obs, point) -> VerificationResult:
        """PASSED when the zoom level changed by at least delta_min."""
        level = obs.get("zoom_level")
        if level is None:
            return self._result(expected, obs, VerificationStatus.UNKNOWN,
                                0.0, "zoom_level not observed")
        try:
            base = dict(getattr(report, "observation", None) or {})
            baseline = float(base.get("zoom_level", 0.0))
            delta = abs(float(level) - baseline)
        except Exception:
            return self._result(expected, obs, VerificationStatus.UNKNOWN,
                                0.0, "zoom_level unreadable")
        delta_min = float(expected.get("delta_min", 0.01))
        if delta >= delta_min:
            return self._result(expected, obs, VerificationStatus.PASSED,
                                1.0, f"zoom delta {delta:.3f}")
        return self._result(expected, obs, VerificationStatus.FAILED,
                            0.0, f"zoom delta only {delta:.3f}")

    def _check_key(self, expected, report, obs, point) -> VerificationResult:
        """PASSED when window_title or present changed vs the baseline."""
        base = dict(getattr(report, "observation", None) or {})
        before_title = base.get("before_window_title", "")
        title = obs.get("window_title")
        present = obs.get("present")
        base_present = base.get("present")
        if title is None and present is None:
            return self._result(expected, obs, VerificationStatus.UNKNOWN,
                                0.0, "window_title/present not observed")
        if title is not None and str(title) != str(before_title):
            return self._result(expected, obs, VerificationStatus.PASSED,
                                1.0, "window title changed")
        if (present is not None and base_present is not None
                and bool(present) != bool(base_present)):
            return self._result(expected, obs, VerificationStatus.PASSED,
                                1.0, "presence changed")
        return self._result(expected, obs, VerificationStatus.FAILED,
                            0.0, "no observable key/window change")

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _result(
        expected: Dict[str, Any],
        observed: Dict[str, Any],
        status: VerificationStatus,
        similarity: float,
        message: str,
    ) -> VerificationResult:
        recovery = (
            RecoveryStrategy.RETRY
            if status == VerificationStatus.FAILED
            else RecoveryStrategy.NONE
        )
        return VerificationResult(
            status=status,
            expected=dict(expected),
            observed=dict(observed),
            similarity=max(0.0, min(1.0, float(similarity))),
            message=message,
            suggested_recovery=recovery,
        )

    # dispatch table: expected "type" -> checker method (documented above)
    _CHECKS = {
        "click": _check_click,
        "pointer": _check_pointer,
        "scroll": _check_scroll,
        "zoom": _check_zoom,
        "key": _check_key,
        "window": _check_key,
    }

    # -- built-in observer -------------------------------------------------------

    @staticmethod
    def make_observe_fn() -> ObserveFn:
        """Best-effort real observer (PIL ImageGrab), headless-safe.

        Grabs a 5×5 region near ``point`` and returns ``{"pixel": (r,g,b)}``;
        ANY failure (no PIL, no display) returns ``{}`` — never raises.
        """

        def observe(point: Optional[Tuple[float, float]]) -> Dict[str, Any]:
            try:
                if point is None:
                    return {}
                from PIL import ImageGrab  # lazy heavy import

                x, y = int(point[0]), int(point[1])
                box = (x - 2, y - 2, x + 3, y + 3)
                img = ImageGrab.grab(bbox=box)
                px = img.getpixel((2, 2))
                return {"pixel": tuple(px[:3])}
            except Exception:
                return {}

        return observe


# ---------------------------------------------------------------------------
# RecoveryManager
# ---------------------------------------------------------------------------


class RecoveryManager:
    """Failure-recovery ladder with a per-plan attempt ledger.

    Ladder for FAILED (execution or verification) failures:

    1. ``RETRY`` the same plan while ``report.attempts < plan.max_retries``
       and the ledgered total attempts are under the hard cap (2).
    2. once: ``RETRY_ADJUSTED`` — same plan with the point nudged +12 px on
       x (an alternative route to the same goal).
    3. otherwise ``NOTIFY`` (``notify_hook`` fired, no plan returned).

    TIMEOUT gets a single RETRY then NOTIFY.  BLOCKED / CANCELLED are final
    (``NONE, None``) — safety decisions are never second-guessed.  Plans
    with ``requires_confirmation=True`` NEVER auto-retry (NOTIFY).
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(DEFAULT_VERIFY_CONFIG)
        cfg.update(config or {})
        self.nudge_px: float = float(cfg["nudge_px"])
        self.max_total_attempts: int = max(1, int(cfg["max_total_attempts"]))
        self.notify_hook: Optional[Callable[[str], None]] = None
        self._ledger: Dict[str, Dict[str, int]] = {}
        self._counter: int = 0

    # -- entry point -----------------------------------------------------------

    def handle(
        self,
        plan: ActionPlan,
        report: ActionReport,
        result: Optional[VerificationResult] = None,
    ) -> Tuple[RecoveryStrategy, Optional[ActionPlan]]:
        """Decide the recovery for a finished (plan, report, result) triple."""
        key = self._plan_key(plan)
        entry = self._ledger.setdefault(key, {"stage": 0, "attempts": 0})
        entry["attempts"] += int(getattr(report, "attempts", 0) or 0)

        # Confirmation-gated plans are never auto-retried.
        if bool(getattr(plan, "requires_confirmation", False)):
            return self._notify(plan, "requires_confirmation")
        status = getattr(report, "status", ActionStatus.FAILED)
        # Safety decisions are final.
        if status in (ActionStatus.BLOCKED, ActionStatus.CANCELLED):
            return (RecoveryStrategy.NONE, None)

        verification_failed = (
            result is not None
            and getattr(result, "status", None) == VerificationStatus.FAILED
        )
        if status not in (ActionStatus.FAILED, ActionStatus.TIMEOUT) and not verification_failed:
            return (RecoveryStrategy.NONE, None)

        if status == ActionStatus.TIMEOUT:
            if entry["stage"] == 0:
                entry["stage"] = 1
                return (RecoveryStrategy.RETRY, self._copy(plan, key))
            return self._notify(plan, "timeout_exhausted")

        # FAILED ladder (execution failure or failed verification).
        attempts = int(getattr(report, "attempts", 0) or 0)
        if (
            entry["stage"] == 0
            and attempts < max(1, int(getattr(plan, "max_retries", 1)))
            and entry["attempts"] < self.max_total_attempts
        ):
            entry["stage"] = 1
            return (RecoveryStrategy.RETRY, self._copy(plan, key))
        if entry["stage"] <= 1:
            entry["stage"] = 2
            return (RecoveryStrategy.RETRY_ADJUSTED, self._nudged(plan, key))
        return self._notify(plan, "retries_exhausted")

    def reset(self) -> None:
        """Forget every ledgered plan (fresh recovery budgets)."""
        self._ledger.clear()

    # -- internals ---------------------------------------------------------------

    def _plan_key(self, plan: ActionPlan) -> str:
        """Stable identity for a plan across copies (ledger key)."""
        key = getattr(plan, "_recovery_id", None)
        if key:
            return str(key)
        self._counter += 1
        key = f"plan-{self._counter}"
        try:
            object.__setattr__(plan, "_recovery_id", key)
        except Exception:
            pass
        return key

    def _copy(self, plan: ActionPlan, key: str) -> ActionPlan:
        """Plain copy of the plan for a straight RETRY (keeps the key)."""
        from dataclasses import replace

        cp = replace(plan)
        try:
            object.__setattr__(cp, "_recovery_id", key)
        except Exception:
            pass
        return cp

    def _nudged(self, plan: ActionPlan, key: str) -> ActionPlan:
        """Copy with the point nudged +nudge_px on x (adjusted retry)."""
        from dataclasses import replace

        pt = plan.target_point
        new_point = None
        if pt is not None:
            new_point = (float(pt[0]) + self.nudge_px, float(pt[1]))
        # The target is dropped so the nudged pixel point is what the retry
        # actually uses (target_point would otherwise re-resolve the centre).
        cp = replace(plan, point=new_point, target=None)
        try:
            object.__setattr__(cp, "_recovery_id", key)
        except Exception:
            pass
        return cp

    def _notify(
        self, plan: ActionPlan, reason: str
    ) -> Tuple[RecoveryStrategy, None]:
        if self.notify_hook is not None:
            try:
                self.notify_hook(f"{reason}:{getattr(plan, 'action', '')}")
            except Exception:
                pass
        return (RecoveryStrategy.NOTIFY, None)

