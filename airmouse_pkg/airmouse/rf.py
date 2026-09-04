"""
airmouse.rf — v10 RF-Sensing Abstraction 📡
============================================

Optional RF-sensing modality (mission §16, "v9.2 RF abstraction").
RF hardware is NEVER mandatory: the abstraction defines the provider
contract + deterministic simulators, and the system degrades to the
remaining modalities (camera/gaze/voice/hand) when no RF hardware is
present — or when RF sensing is simply disabled.

Provider contract
-----------------

    class MyRFProvider:
        name = "my-rf"
        def available(self) -> bool: ...
        def poll(self, now=None) -> List[RFEvent]: ...

A provider classifies raw RF channel state into normalized events:

    RFEvent(kind="gesture"|"motion", label="swipe_left"/"push"/…,
            confidence, payload, source)

Built-in providers:

    SimulatedRFProvider   deterministic scripted events (tests/CI)
    DummyRFProvider       always unavailable (documents degradation)

The :class:`RFBridge` wires a provider into the universal event bus
(kind RF_GESTURE / RF_MOTION) and can feed the gesture registry so RF
gestures participate in fusion like hand gestures do.

Networking/hardware are never touched here beyond the provider's own
implementation; with no provider the bridge reports
``available() is False`` and ``poll()`` returns [] forever.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

try:  # package-relative (normal import path)
    from .interfaces import Event, EventKind, Modality, now_ts
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import Event, EventKind, Modality, now_ts

__all__ = ["RFEvent", "RFProvider", "SimulatedRFProvider", "DummyRFProvider",
           "RFBridge", "RF_GESTURE_LABELS"]


# ---------------------------------------------------------------------------
# Event + provider contract
# ---------------------------------------------------------------------------


@dataclass
class RFEvent:
    """One classified RF observation."""

    kind: str = "motion"            # "gesture" | "motion"
    label: str = ""                 # e.g. "swipe_left", "push", "wave"
    confidence: float = 0.0
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = "rf"
    timestamp: float = field(default_factory=now_ts)


class RFProvider(Protocol):
    """Contract for RF-sensing backends (hardware or simulated)."""

    name: str

    def available(self) -> bool: ...

    def poll(self, now: Optional[float] = None) -> List[RFEvent]: ...


#: labels the fusion layer may treat as gesture equivalents
RF_GESTURE_LABELS: Tuple[str, ...] = (
    "swipe_left", "swipe_right", "swipe_up", "swipe_down",
    "push", "pull", "wave", "circle_cw", "circle_ccw", "tap",
)


class DummyRFProvider:
    """Documents graceful degradation: always unavailable, always []."""

    name = "dummy"

    def available(self) -> bool:
        return False

    def poll(self, now: Optional[float] = None) -> List[RFEvent]:
        return []


class SimulatedRFProvider:
    """Deterministic scripted RF provider (§22 simulator).

    ``push(kind, label, confidence)`` queues events; ``poll`` drains
    them in order.  No randomness, no threads.
    """

    name = "simulated-rf"

    def __init__(self) -> None:
        self._queue: List[RFEvent] = []
        self._lock = threading.Lock()

    def available(self) -> bool:
        return True

    def push(self, kind: str, label: str, confidence: float = 0.9,
             payload: Optional[Dict[str, Any]] = None,
             now: Optional[float] = None) -> None:
        with self._lock:
            self._queue.append(RFEvent(
                kind=str(kind or "motion"), label=str(label or ""),
                confidence=float(confidence),
                payload=dict(payload or {}), source=self.name,
                timestamp=float(now) if now is not None else now_ts()))

    def poll(self, now: Optional[float] = None) -> List[RFEvent]:
        with self._lock:
            out = list(self._queue)
            self._queue.clear()
        return out


# ---------------------------------------------------------------------------
# Bridge into the event bus
# ---------------------------------------------------------------------------


class RFBridge:
    """Wires an :class:`RFProvider` into the universal event bus.

    - ``available()``: provider availability AND enabled flag.
    - ``poll(now)``: drains the provider; for each RFEvent publishes
      ``Event(kind=RF_GESTURE|RF_MOTION, modality=RF)`` on the bus and
      returns the (event, label) pairs so the caller may forward
      gesture labels to the gesture registry / fusion.
    - Missing provider / disabled / provider exceptions degrade to
      "no events this tick" — never raise, never crash (§16).
    """

    def __init__(self,
                 provider: Optional[RFProvider] = None,
                 config: Optional[Dict[str, Any]] = None,
                 bus: Optional[Any] = None) -> None:
        cfg = dict(config or {})
        self.enabled = bool(cfg.get("enabled", True))
        self.min_confidence = float(cfg.get("min_confidence", 0.4))
        self.bus = bus
        self.provider = provider
        self.last_label = ""
        self.last_confidence = 0.0
        self.event_count = 0

    @property
    def sensor_name(self) -> str:
        return getattr(self.provider, "name", "") if self.provider else ""

    def available(self) -> bool:
        """True only when enabled AND the provider reports hardware."""
        if not self.enabled or self.provider is None:
            return False
        try:
            return bool(self.provider.available())
        except Exception:
            return False

    def attach(self, provider: RFProvider) -> None:
        """Hot-swap the provider (e.g. hardware detected at runtime)."""
        self.provider = provider

    def poll(self, now: Optional[float] = None) -> List[Tuple[Event, RFEvent]]:
        """One poll tick.  Returns bus Event + raw RFEvent pairs."""
        now = float(now if now is not None else now_ts())
        out: List[Tuple[Event, RFEvent]] = []
        if not self.available():
            return out
        try:
            raw_events = list(self.provider.poll(now) or [])
        except Exception:
            return out
        for rf in raw_events:
            try:
                conf = float(getattr(rf, "confidence", 0.0) or 0.0)
            except Exception:
                conf = 0.0
            if conf < self.min_confidence:
                continue
            label = str(getattr(rf, "label", "") or "")
            kind = str(getattr(rf, "kind", "motion") or "motion")
            event = Event(
                kind=EventKind.RF_GESTURE if kind == "gesture"
                else EventKind.RF_MOTION,
                modality=Modality.RF, confidence=conf,
                source=str(getattr(rf, "source", "rf") or "rf"),
                payload={"label": label, "rf_kind": kind,
                         **dict(getattr(rf, "payload", {}) or {})},
                timestamp=now,
            )
            if self.bus is not None:
                try:
                    self.bus.publish(event, now=now)
                except Exception:
                    pass
            self.last_label = label
            self.last_confidence = conf
            self.event_count += 1
            out.append((event, rf))
        return out

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.sensor_name,
            "available": self.available(),
            "last_label": self.last_label,
            "last_confidence": self.last_confidence,
            "events": self.event_count,
        }
