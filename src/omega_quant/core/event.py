"""Omega event engine backed by the pruned vn.py EventEngine."""

from __future__ import annotations

from collections.abc import Callable
from queue import Empty
from threading import Thread
from typing import Any

from omega_quant.vnpy_core.event import EVENT_TIMER, Event
from omega_quant.vnpy_core.event.engine import EventEngine as VnpyEventEngine


EVENT_TICK = "eTick."
EVENT_TRADE = "eTrade."
EVENT_ORDER = "eOrder."
EVENT_POSITION = "ePosition."
EVENT_ACCOUNT = "eAccount."
EVENT_CONTRACT = "eContract."
EVENT_LOG = "eLog"

HandlerType = Callable[[Event], None]


class EventEngine(VnpyEventEngine):
    """vn.py EventEngine plus Omega's deterministic test flush helper.

    The queue dispatch, timer event model, and handler registration are the
    upstream vn.py implementation. Omega only adds safe repeated start/stop and
    ``process_pending`` for FastAPI/tests that need read-after-write behavior.
    """

    def __init__(self, interval: float = 1.0) -> None:
        super().__init__(interval=interval)
        self._thread.daemon = True
        self._timer.daemon = True

    @property
    def active(self) -> bool:
        return bool(self._active)

    def start(self) -> None:
        if self._active:
            return
        if self._thread.ident is not None:
            self._thread = Thread(target=self._run, name="omega-vnpy-event-engine", daemon=True)
        if self._timer.ident is not None:
            self._timer = Thread(target=self._run_timer, name="omega-vnpy-event-timer", daemon=True)
        super().start()

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        self._timer.join(timeout=max(2.0, float(self._interval) + 1.0))
        self._thread.join(timeout=2.0)

    def process_pending(self, limit: int = 1_000) -> int:
        processed = 0
        while processed < limit:
            try:
                event = self._queue.get(block=False)
            except Empty:
                break
            self._process(event)
            processed += 1
        return processed


__all__ = [
    "EVENT_TIMER",
    "EVENT_TICK",
    "EVENT_TRADE",
    "EVENT_ORDER",
    "EVENT_POSITION",
    "EVENT_ACCOUNT",
    "EVENT_CONTRACT",
    "EVENT_LOG",
    "Event",
    "EventEngine",
    "HandlerType",
]

