"""
airmouse.safety — v8 Safety System 🛡️
======================================

The single gating authority for would-be actions.  Every intent passes
through :meth:`SafetySystem.approve_intent` before any executor is touched;
the system also owns confirmation flow, the latching e-stop and the
modality stream-loss watchdog.

Approval gates (evaluated IN ORDER)
-----------------------------------
0. ``EMERGENCY_STOP`` intents always pass (the e-stop itself must never be
   blocked, even while latched).
1. level EMERGENCY → blocked "emergency_stop".
2. level SAFE_MODE whitelist → only MOVE / SCROLL intents pass; everything
   else is blocked "safe_mode".
3. confidence < ``min_confidence`` (plus ``careful_confidence_bonus`` when
   CAREFUL) → blocked "low_confidence".
4. GAZE-sourced click-class intent with confidence <
   ``gaze_min_confidence`` → blocked "low_gaze_confidence".
5. Rate limiter — sliding 1.0 s window of granted approvals; over
   ``max_actions_per_sec`` → blocked "rate_limit" with
   ``cooldown_remaining``.
6. Click-class cooldown — a click-class intent within
   ``min_click_interval`` of the last approved click → blocked
   "click_cooldown".
7. Sensitive intent type without a fresh confirmation → ``allowed=False``,
   ``requires_confirmation=True``, reason "needs_confirmation", pending
   stored (see confirmation flow).

Every gate increments a per-reason counter in :attr:`SafetySystem.stats`;
approved click-class intents record the click time.

Confirmation flow
-----------------
``approve_intent`` stores a pending intent for sensitive types;
:meth:`~SafetySystem.confirm` (voice/hotkey source) approves it exactly
once; pending confirmations expire after ``confirmation_timeout`` (checked
at query time, so injected timestamps make tests deterministic).

E-stop
------
:meth:`~SafetySystem.trip` latches EMERGENCY (remembering the previous
level), bumps ``estop_count`` and clears pending confirmations;
:meth:`~SafetySystem.reset` restores the remembered level (only from
EMERGENCY).

Stream loss
-----------
:meth:`~SafetySystem.report_stream_loss` watches GAZE (camera) / VOICE
(mic): a modality lost for longer than ``stream_loss_grace`` downgrades
the system to SAFE_MODE once per episode (remembering the previous
level); regaining ALL lost modalities restores the remembered level
(hysteresis).

Thread-safety: every public method takes the internal ``RLock``.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Deque, Dict, Optional, Union

try:  # package-relative (normal import path)
    from .interfaces import (
        Intent,
        IntentType,
        Modality,
        SafetyDecision,
        SafetyLevel,
        now_ts,
    )
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (
        Intent,
        IntentType,
        Modality,
        SafetyDecision,
        SafetyLevel,
        now_ts,
    )

__all__ = [
    "DEFAULT_SAFETY_CONFIG",
    "CLICK_CLASS",
    "SAFE_MODE_WHITELIST",
    "SENSITIVE_TYPES",
    "RATE_WINDOW_S",
    "SafetySystem",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Documented configuration defaults (see :class:`SafetySystem`).
DEFAULT_SAFETY_CONFIG: Dict[str, Any] = {
    "level": "normal",
    "max_actions_per_sec": 8,
    "min_click_interval": 0.15,
    "min_confidence": 0.35,
    "careful_confidence_bonus": 0.15,
    "gaze_min_confidence": 0.55,
    "confirmation_timeout": 5.0,
    "stream_loss_grace": 2.0,
    "sensitive_types": None,   # None → SENSITIVE_TYPES default
}

#: Click-class intent types (click-cooldown + gaze-confidence gates).
CLICK_CLASS: set = {
    IntentType.CLICK,
    IntentType.DOUBLE_CLICK,
    IntentType.RIGHT_CLICK,
    IntentType.MIDDLE_CLICK,
}

#: Intents still permitted in SAFE_MODE.
SAFE_MODE_WHITELIST: set = {IntentType.MOVE, IntentType.SCROLL}

#: Intent types that demand explicit confirmation.
SENSITIVE_TYPES: set = {
    IntentType.CLOSE,
    IntentType.PASTE,
    IntentType.HOTKEY,
    IntentType.MAXIMIZE,
    IntentType.SWITCH_WINDOW,
}

#: Sliding window length of the rate limiter (seconds).
RATE_WINDOW_S: float = 1.0


# ---------------------------------------------------------------------------
# SafetySystem
# ---------------------------------------------------------------------------


class SafetySystem:
    """Lock-guarded safety gate: thresholds, confirmation, e-stop, watchdog."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(DEFAULT_SAFETY_CONFIG)
        cfg.update(config or {})
        self.max_actions_per_sec: int = max(1, int(cfg["max_actions_per_sec"]))
        self.min_click_interval: float = float(cfg["min_click_interval"])
        self.min_confidence: float = float(cfg["min_confidence"])
        self.careful_confidence_bonus: float = float(cfg["careful_confidence_bonus"])
        self.gaze_min_confidence: float = float(cfg["gaze_min_confidence"])
        self.confirmation_timeout: float = float(cfg["confirmation_timeout"])
        self.stream_loss_grace: float = float(cfg["stream_loss_grace"])
        st = cfg.get("sensitive_types") or SENSITIVE_TYPES
        self.sensitive_types: set = set(st)

        self._level = self._coerce_level(cfg["level"])
        self._lock = threading.RLock()
        self._approval_times: Deque[float] = deque()
        self._last_click_time: float = float("-inf")
        self._pending: Optional[Intent] = None
        self._pending_ts: float = 0.0
        # monotonic receipt time — expiry is measured in REAL elapsed time so
        # injected (test) timestamps in _pending_ts never poison the timeout
        self._pending_mono: Optional[float] = None
        self._confirmed_sigs: Deque[str] = deque(maxlen=32)
        self._pre_trip_level: Optional[SafetyLevel] = None
        self._pre_stream_level: Optional[SafetyLevel] = None
        # modality -> {"lost_since": float|None, "downgraded": bool}
        self._stream_episodes: Dict[Modality, Dict[str, Any]] = {}
        self.estop_count: int = 0
        self.stats: Dict[str, int] = {
            "approved": 0,
            "confirmations": 0,
            "confirmations_expired": 0,
            "estops": 0,
            "stream_downgrades": 0,
            "stream_restores": 0,
        }

    # -- level management ------------------------------------------------------

    @staticmethod
    def _coerce_level(value: Union[SafetyLevel, str]) -> SafetyLevel:
        """Accept a SafetyLevel, its name or value (NORMAL on garbage)."""
        if isinstance(value, SafetyLevel):
            return value
        try:
            return SafetyLevel(str(getattr(value, "value", value)).lower())
        except Exception:
            return SafetyLevel.NORMAL

    def set_level(self, level: Union[SafetyLevel, str]) -> None:
        """Set the safety posture (accepts ``SafetyLevel`` or its value)."""
        with self._lock:
            self._level = self._coerce_level(level)

    @property
    def level(self) -> SafetyLevel:
        """Current :class:`SafetyLevel`."""
        with self._lock:
            return self._level

    # -- the gate ---------------------------------------------------------------

    def approve_intent(
        self, intent: Intent, now: Optional[float] = None
    ) -> SafetyDecision:
        """Run the ordered gate ladder for ``intent`` (never raises)."""
        now = float(now if now is not None else now_ts())
        with self._lock:
            level = self._level
            itype = getattr(intent, "type", IntentType.NONE)
            confidence = float(getattr(intent, "confidence", 0.0))
            sources = getattr(intent, "sources", Modality.NONE)

            # 0. the e-stop intent itself always passes.
            if itype == IntentType.EMERGENCY_STOP:
                self._bump("approved")
                return SafetyDecision(
                    allowed=True, reason="emergency_stop_intent",
                    level=level, timestamp=now,
                )

            def blocked(reason: str,
                        cooldown_remaining: float = 0.0,
                        needs_confirmation: bool = False) -> SafetyDecision:
                self._bump(f"blocked_{reason}")
                return SafetyDecision(
                    allowed=False,
                    reason=reason,
                    requires_confirmation=needs_confirmation,
                    level=level,
                    cooldown_remaining=max(0.0, float(cooldown_remaining)),
                    timestamp=now,
                )

            # 1. latched e-stop blocks everything.
            if level == SafetyLevel.EMERGENCY:
                return blocked("emergency_stop")

            # 2. SAFE_MODE whitelist.
            if level == SafetyLevel.SAFE_MODE and itype not in SAFE_MODE_WHITELIST:
                return blocked("safe_mode")

            # 3. confidence threshold (CAREFUL raises it).
            threshold = self.min_confidence + (
                self.careful_confidence_bonus if level == SafetyLevel.CAREFUL else 0.0
            )
            if confidence < threshold:
                return blocked("low_confidence")

            # 4. uncertain gaze never clicks.
            if (
                (sources & Modality.GAZE)
                and itype in CLICK_CLASS
                and confidence < self.gaze_min_confidence
            ):
                return blocked("low_gaze_confidence")

            # 5. rate limiter (sliding window of granted approvals).
            self._approval_times = deque(
                t for t in self._approval_times if now - t <= RATE_WINDOW_S
            )
            if len(self._approval_times) >= self.max_actions_per_sec:
                cooldown = self._approval_times[0] + RATE_WINDOW_S - now
                return blocked("rate_limit", cooldown_remaining=cooldown)

            # 6. click-class cooldown.
            if itype in CLICK_CLASS:
                elapsed = now - self._last_click_time
                if elapsed < self.min_click_interval:
                    return blocked(
                        "click_cooldown",
                        cooldown_remaining=self.min_click_interval - elapsed,
                    )

            # 7. sensitive types need explicit confirmation (one-shot:
            #    a stored confirmation is CONSUMED by the next approval so
            #    an identical follow-up intent re-arms the flow).
            sig = self._signature(intent)
            if itype in self.sensitive_types:
                if sig in self._confirmed_sigs:
                    try:
                        self._confirmed_sigs.remove(sig)
                    except ValueError:
                        pass
                else:
                    if self._pending is None or self._signature(self._pending) != sig:
                        self._pending = intent
                        self._pending_ts = now
                        self._pending_mono = now_ts()
                    return blocked("needs_confirmation", needs_confirmation=True)

            # approved — record for rate/click bookkeeping.
            self._approval_times.append(now)
            if itype in CLICK_CLASS:
                self._last_click_time = now
            self._bump("approved")
            return SafetyDecision(
                allowed=True, reason="ok", level=level, timestamp=now,
            )

    # -- confirmation flow ---------------------------------------------------------

    def request_confirmation(
        self, intent: Intent, now: Optional[float] = None
    ) -> bool:
        """Store ``intent`` as the pending confirmation (True when stored)."""
        now = float(now if now is not None else now_ts())
        with self._lock:
            if intent is None:
                return False
            self._pending = intent
            self._pending_ts = now
            self._pending_mono = now_ts()
            return True

    def confirm(self, source: str = "voice") -> bool:
        """Approve the pending confirmation exactly once (False if none/
        expired)."""
        with self._lock:
            pending = self._peek_pending()
            if pending is None:
                return False
            self._confirmed_sigs.append(self._signature(pending))
            self._pending = None
            self._bump("confirmations")
            return True

    @property
    def pending_confirmation(self) -> Optional[Intent]:
        """The pending confirmation intent, or None (expiry applied)."""
        with self._lock:
            return self._peek_pending()

    def _peek_pending(self) -> Optional[Intent]:
        """Expiry-aware pending read (caller holds the lock).

        Expiry is measured against the MONOTONIC receipt time, never the
        (possibly injected) intent timestamp, so external clocks cannot
        accidentally expire or immortalize a pending confirmation.
        """
        if self._pending is None:
            return None
        mono = self._pending_mono
        if mono is None:
            mono = self._pending_ts  # legacy path
        if now_ts() - mono > self.confirmation_timeout:
            self._pending = None
            self._pending_mono = None
            self._bump("confirmations_expired")
            return None
        return self._pending

    # -- e-stop ------------------------------------------------------------------

    def trip(self, reason: str = "") -> None:
        """Latch EMERGENCY (remembers the previous level), clear pending."""
        with self._lock:
            if self._level != SafetyLevel.EMERGENCY:
                self._pre_trip_level = self._level
            self._level = SafetyLevel.EMERGENCY
            self.estop_count += 1
            self._bump("estops")
            self._pending = None

    def reset(self) -> None:
        """Restore the pre-trip level (only from EMERGENCY) and clear
        transient state.  ``estop_count`` is kept as cumulative telemetry."""
        with self._lock:
            if self._level == SafetyLevel.EMERGENCY and self._pre_trip_level is not None:
                self._level = self._pre_trip_level
            self._pre_trip_level = None
            self._approval_times.clear()
            self._last_click_time = float("-inf")
            self._pending = None
            self._confirmed_sigs.clear()
            for key in [k for k in self.stats if k.startswith("blocked_")]:
                self.stats[key] = 0
            self.stats["approved"] = 0
            self.stats["confirmations"] = 0

    # -- stream-loss watchdog -------------------------------------------------------

    def report_stream_loss(
        self,
        modality: Modality,
        lost: bool,
        now: Optional[float] = None,
    ) -> None:
        """Report camera/mic loss; sustained loss downgrades to SAFE_MODE
        once per episode, regain restores the remembered level."""
        if modality not in (Modality.GAZE, Modality.VOICE):
            return  # only camera / mic are watched
        now = float(now if now is not None else now_ts())
        with self._lock:
            ep = self._stream_episodes.setdefault(
                modality, {"lost_since": None, "downgraded": False}
            )
            if lost:
                if ep["lost_since"] is None:
                    ep["lost_since"] = now
                    return
                if (
                    not ep["downgraded"]
                    and now - ep["lost_since"] > self.stream_loss_grace
                ):
                    ep["downgraded"] = True
                    if self._level not in (SafetyLevel.EMERGENCY, SafetyLevel.SAFE_MODE):
                        self._pre_stream_level = self._level
                        self._level = SafetyLevel.SAFE_MODE
                        self._bump("stream_downgrades")
            else:
                ep["lost_since"] = None
                others_lost = any(
                    e["lost_since"] is not None
                    for m, e in self._stream_episodes.items() if m != modality
                )
                # restore whenever the LAST lost watched stream comes back,
                # regardless of which episode triggered the downgrade
                if (
                    not others_lost
                    and self._level == SafetyLevel.SAFE_MODE
                    and self._pre_stream_level is not None
                ):
                    self._level = self._pre_stream_level
                    self._pre_stream_level = None
                    self._bump("stream_restores")
                if ep["downgraded"]:
                    ep["downgraded"] = False

    # -- internals ----------------------------------------------------------------

    def _bump(self, key: str) -> None:
        self.stats[key] = self.stats.get(key, 0) + 1

    @staticmethod
    def _signature(intent: Intent) -> str:
        """Stable one-shot confirmation signature for an intent."""
        return "|".join(
            (
                str(getattr(getattr(intent, "type", None), "value",
                            getattr(intent, "type", ""))),
                repr(getattr(intent, "point", None)),
                repr(float(getattr(intent, "timestamp", 0.0) or 0.0)),
            )
        )
