"""
airmouse.eventbus — v10 Universal Local Event Bus 🚌
====================================================

The single in-process nervous system for AirMouse v10.  Every perception
system (offline voice, hand tracker, gaze engine, RF abstraction, browser
bridge, screen understanding, physical keyboard/mouse monitors) publishes
normalized :class:`airmouse.interfaces.Event` objects; every consumer
(fusion, intent, HUD, diagnostics, macros) subscribes or polls.

Design rules
------------
1.  **Local only.**  No sockets, no network, no serialization boundary —
    a pure in-process pub/sub that works with networking disabled.
2.  **Events are data.**  An Event never carries executable content; the
    intent/safety layers decide what an event means.
3.  **Never blocks the producer.**  ``publish`` is O(subscribers) with
    bounded per-subscriber queues: a slow consumer drops the OLDEST
    events (never blocks perception, never grows unbounded).
4.  **Deterministic in tests.**  Every method accepts ``now``; a bus
    driven with explicit timestamps is fully reproducible.
5.  **Headless.**  stdlib only, no hardware imports.

Quick usage
-----------

    from airmouse.eventbus import EventBus, Subscriber
    from airmouse.interfaces import Event, EventKind, Modality, now_ts

    bus = EventBus(history_size=256)
    sub = bus.subscribe(kinds={EventKind.VOICE_COMMAND})
    bus.publish(Event(kind=EventKind.VOICE_COMMAND,
                      modality=Modality.VOICE, confidence=0.9,
                      payload={"command": "click"}, source="voice"))
    ev = sub.poll()          # -> the Event above (thread-safe)
    bus.history()            # ring buffer snapshot for diagnostics
    bus.stats()              # {"published": n, "dropped": n, by kind…}

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import collections
import itertools
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional, Set

try:  # package-relative (normal import path)
    from .interfaces import (Event, EventKind, Modality)
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (Event, EventKind, Modality)

__all__ = ["Subscriber", "EventBus", "MultiSubscriber"]


# ---------------------------------------------------------------------------
# Subscriber
# ---------------------------------------------------------------------------


@dataclass
class Subscriber:
    """One consumer's mailbox on the bus.

    ``kinds`` / ``modalities`` are optional whitelist filters (None = all).
    ``queue`` is a bounded deque: when full, the OLDEST event is dropped
    before insertion (perception never waits on a slow consumer).
    """

    kinds: Optional[Set[EventKind]] = None
    modalities: Optional[Set[Modality]] = None
    queue_size: int = 64
    queue: "collections.deque[Event]" = field(init=False)
    dropped: int = 0
    received: int = 0

    def __post_init__(self) -> None:
        self.queue = collections.deque(maxlen=int(self.queue_size))

    # -- filtering -----------------------------------------------------------

    def wants(self, event: Event) -> bool:
        """Deterministic filter check (pure)."""
        if self.kinds is not None and event.kind not in self.kinds:
            return False
        if self.modalities is not None and event.modality not in self.modalities:
            return False
        return True

    # -- delivery ------------------------------------------------------------

    def deliver(self, event: Event) -> bool:
        """Append to the mailbox; drop-oldest when full.  True if stored."""
        if self.queue.maxlen is not None and len(self.queue) >= self.queue.maxlen:
            try:
                self.queue.popleft()
            except IndexError:
                pass
            self.dropped += 1
            stored = False
        else:
            stored = True
        self.queue.append(event)
        self.received += 1
        return stored

    def poll(self) -> Optional[Event]:
        """Pop the oldest queued event (None when empty).  Thread-safe."""
        try:
            return self.queue.popleft()
        except IndexError:
            return None

    def drain(self) -> list:
        """Pop every queued event in order (oldest first)."""
        out = []
        while True:
            ev = self.poll()
            if ev is None:
                return out
            out.append(ev)


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------


class EventBus:
    """Thread-safe, bounded, in-process pub/sub for normalized Events.

    - ``publish(event, now=None)``: validate → fan out to matching
      subscribers → append to the ring history.  Never raises on a bad
      event (logs it into ``rejected`` counter instead).
    - ``subscribe(...)`` → :class:`Subscriber` (call ``bus.unsubscribe``
      on teardown; weakrefs are intentionally NOT used so test
      lifecycles stay explicit).
    - ``history(kinds=None, limit=None)``: snapshot of recent events for
      HUD/diagnostics.
    - ``stats()``: cumulative counters (published/rejected/dropped).
    """

    def __init__(self,
                 history_size: int = 512,
                 default_queue_size: int = 64) -> None:
        self._history: "collections.deque[Event]" = \
            collections.deque(maxlen=max(0, int(history_size)))
        self._subs: Dict[int, Subscriber] = {}
        self._sub_seq = itertools.count(1)
        self._lock = threading.RLock()
        self._default_queue_size = max(1, int(default_queue_size))
        # counters
        self.published = 0
        self.rejected = 0
        self.dropped_total = 0
        self._by_kind: Dict[str, int] = {}

    # -- subscription management -------------------------------------------------

    def subscribe(self,
                  kinds: Optional[Iterable[EventKind]] = None,
                  modalities: Optional[Iterable[Modality]] = None,
                  queue_size: Optional[int] = None,
                  callback: Optional[Callable[[Event], None]] = None
                  ) -> Subscriber:
        """Register a subscriber.  ``callback`` (optional) is invoked
        inline after delivery — guard your own exceptions."""
        sub = Subscriber(
            kinds=set(kinds) if kinds is not None else None,
            modalities=set(modalities) if modalities is not None else None,
            queue_size=self._default_queue_size if queue_size is None
            else int(queue_size),
        )
        with self._lock:
            sub_id = next(self._sub_seq)
            self._subs[sub_id] = sub
        if callback is not None:
            # store callback on the sub object for publish hot path
            sub.__dict__["_callback"] = callback
        return sub

    def unsubscribe(self, sub: Subscriber) -> bool:
        with self._lock:
            for sid, s in list(self._subs.items()):
                if s is sub:
                    del self._subs[sid]
                    return True
        return False

    # -- publishing ------------------------------------------------------------

    def publish(self, event: Event, now: Optional[float] = None) -> bool:
        """Validate + fan out one Event.  Returns True when accepted.

        Validation: an Event with kind NONE or a non-Event object is
        rejected (counted, never raised).  ``now`` overrides the
        timestamp (determinism) when provided.
        """
        if not isinstance(event, Event):
            with self._lock:
                self.rejected += 1
            return False
        if event.kind is EventKind.NONE:
            with self._lock:
                self.rejected += 1
            return False
        if now is not None:
            event.timestamp = float(now)
        # clamp confidence into [0,1] — perception bugs must not poison gates
        try:
            conf = float(event.confidence)
        except Exception:
            conf = 0.0
        event.confidence = max(0.0, min(1.0, conf))
        if not isinstance(event.payload, dict):
            event.payload = {}

        with self._lock:
            self.published += 1
            key = event.kind.value
            self._by_kind[key] = self._by_kind.get(key, 0) + 1
            self._history.append(event)
            subs = list(self._subs.values())
        for sub in subs:
            if sub.wants(event):
                sub.deliver(event)
                cb = sub.__dict__.get("_callback")
                if cb is not None:
                    try:
                        cb(event)
                    except Exception:
                        pass  # a bad callback must never kill the bus
        return True

    # -- introspection ------------------------------------------------------------

    def history(self,
                kinds: Optional[Iterable[EventKind]] = None,
                limit: Optional[int] = None) -> list:
        """Snapshot of recent events (oldest → newest), optionally
        filtered by kinds and truncated to the last ``limit``."""
        with self._lock:
            items = list(self._history)
        if kinds is not None:
            kset = set(kinds)
            items = [e for e in items if e.kind in kset]
        if limit is not None and limit >= 0:
            items = items[-int(limit):]
        return items

    def stats(self) -> Dict[str, Any]:
        """Cumulative bus statistics (counters snapshot)."""
        with self._lock:
            by_kind = dict(self._by_kind)
            dropped = sum(s.dropped for s in self._subs.values())
            return {
                "published": self.published,
                "rejected": self.rejected,
                "dropped": dropped,
                "subscribers": len(self._subs),
                "by_kind": by_kind,
            }

    def reset(self) -> None:
        """Clear history + counters (subscribers keep their mailboxes)."""
        with self._lock:
            self._history.clear()
            self.published = 0
            self.rejected = 0
            self.dropped_total = 0
            self._by_kind.clear()

    # -- convenience bridges ---------------------------------------------------------

    def publish_voice(self, command: str, text: str, confidence: float,
                      source: str = "offline_voice",
                      now: Optional[float] = None) -> bool:
        """Shorthand: publish a resolved voice command event."""
        return self.publish(Event(
            kind=EventKind.VOICE_COMMAND, modality=Modality.VOICE,
            confidence=confidence, source=source,
            payload={"command": str(command), "text": str(text)},
            timestamp=now), now=now)

    def publish_text(self, text: str, confidence: float,
                     source: str = "offline_voice",
                     now: Optional[float] = None) -> bool:
        """Shorthand: publish a raw/dictated transcript event."""
        return self.publish(Event(
            kind=EventKind.VOICE_TEXT, modality=Modality.VOICE,
            confidence=confidence, source=source,
            payload={"text": str(text)}, timestamp=now), now=now)


class MultiSubscriber:
    """Fan-in helper: poll several subscribers in FIFO-merge order.

    The main loop uses this to consume voice/gesture/RF/browser events
    from one place while each producer stays independent.
    """

    def __init__(self, subs: Iterable[Subscriber]) -> None:
        self._subs = list(subs)

    def poll(self) -> Optional[Event]:
        """Round-robin poll across subscribers (oldest-first per sub)."""
        best: Optional[Event] = None
        for sub in self._subs:
            ev = sub.poll()
            if ev is not None and (best is None
                                   or ev.timestamp < best.timestamp):
                best = ev
        return best

    def drain(self) -> list:
        out = []
        while True:
            ev = self.poll()
            if ev is None:
                return out
            out.append(ev)
