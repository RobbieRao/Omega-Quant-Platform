"""vn.py-style DataRecorder engine for Omega.

The design follows vn.py's DataRecorder idea: subscribe to market events and
persist Tick/Bar data. Omega keeps it backend-only and scoped to US stocks and
options.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..market_events import MarketEvent
from ..models import RecordedBar, RecordedTick
from .cta_utility import BarGenerator
from .engine import BaseEngine, MainEngine
from .event import EVENT_TICK, Event, EventEngine
from .objects import BarData, Exchange, TickData


class DataRecorderEngine(BaseEngine):
    engine_name = "data_recorder"

    def __init__(
        self,
        main_engine: MainEngine,
        event_engine: EventEngine,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        super().__init__(main_engine, event_engine, self.engine_name)
        self.session_factory = session_factory
        self.tick_recordings: set[str] = set()
        self.bar_recordings: set[str] = set()
        self.bar_generators: dict[str, BarGenerator] = {}
        self._recent_tick_keys: list[tuple[str, datetime, float, float]] = []
        self._recording_lock = threading.RLock()
        self.event_engine.register(EVENT_TICK, self.process_tick_event)

    def bind_session_factory(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def add_tick_recording(self, vt_symbol: str) -> None:
        self.tick_recordings.add(vt_symbol.upper())

    def remove_tick_recording(self, vt_symbol: str) -> None:
        self.tick_recordings.discard(vt_symbol.upper())

    def add_bar_recording(self, vt_symbol: str) -> None:
        self.bar_recordings.add(vt_symbol.upper())

    def remove_bar_recording(self, vt_symbol: str) -> None:
        normalized = vt_symbol.upper()
        self.bar_recordings.discard(normalized)
        self.bar_generators.pop(normalized, None)

    def process_tick_event(self, event: Event) -> None:
        tick = event.data
        if not isinstance(tick, TickData):
            return
        vt_symbol = tick.vt_symbol.upper()
        with self._recording_lock:
            should_record_tick = vt_symbol in self.tick_recordings
            should_record_bar = vt_symbol in self.bar_recordings
            if not should_record_tick and not should_record_bar:
                return
            if self._has_seen_tick(tick):
                return
            self._remember_tick(tick)
            if should_record_tick:
                self.record_tick(tick)
            if should_record_bar:
                self._update_bar_generator(tick)

    def record_tick_once(self, tick: TickData, *, source: str = "live_readonly", raw: dict | None = None) -> RecordedTick | None:
        vt_symbol = tick.vt_symbol.upper()
        with self._recording_lock:
            should_record_tick = vt_symbol in self.tick_recordings
            should_record_bar = vt_symbol in self.bar_recordings
            if not should_record_tick and not should_record_bar:
                return None
            if self._has_seen_tick(tick):
                return None
            self._remember_tick(tick)
            row = self.record_tick(tick, source=source, raw=raw) if should_record_tick else None
            if should_record_bar:
                self._update_bar_generator(tick, source=source)
            return row

    def record_tick(self, tick: TickData, *, source: str = "live_readonly", raw: dict | None = None) -> RecordedTick:
        if not self.session_factory:
            raise RuntimeError("DataRecorderEngine session_factory is not bound")
        row = tick_to_recorded_tick(tick, source=source, raw=raw)
        session = self.session_factory()
        try:
            session.add(row)
            session.commit()
            return row
        finally:
            session.close()

    def record_bar(self, bar: RecordedBar) -> RecordedBar:
        if not self.session_factory:
            raise RuntimeError("DataRecorderEngine session_factory is not bound")
        session = self.session_factory()
        try:
            session.add(bar)
            session.commit()
            return bar
        finally:
            session.close()

    def load_bars(
        self,
        vt_symbol: str,
        *,
        interval: str = "1m",
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1_000,
    ) -> list[RecordedBar]:
        if not self.session_factory:
            raise RuntimeError("DataRecorderEngine session_factory is not bound")
        self.event_engine.process_pending()
        session = self.session_factory()
        try:
            stmt = (
                select(RecordedBar)
                .where(RecordedBar.vt_symbol == vt_symbol.upper(), RecordedBar.interval == interval)
                .order_by(RecordedBar.timestamp)
            )
            if start:
                stmt = stmt.where(RecordedBar.timestamp >= start)
            if end:
                stmt = stmt.where(RecordedBar.timestamp <= end)
            stmt = stmt.limit(limit)
            return list(session.scalars(stmt))
        finally:
            session.close()

    def load_ticks(
        self,
        vt_symbol: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1_000,
    ) -> list[RecordedTick]:
        if not self.session_factory:
            raise RuntimeError("DataRecorderEngine session_factory is not bound")
        self.event_engine.process_pending()
        session = self.session_factory()
        try:
            stmt = select(RecordedTick).where(RecordedTick.vt_symbol == vt_symbol.upper()).order_by(RecordedTick.timestamp)
            if start:
                stmt = stmt.where(RecordedTick.timestamp >= start)
            if end:
                stmt = stmt.where(RecordedTick.timestamp <= end)
            stmt = stmt.limit(limit)
            return list(session.scalars(stmt))
        finally:
            session.close()

    def close(self) -> None:
        self.event_engine.unregister(EVENT_TICK, self.process_tick_event)

    def flush_bar_generators(self) -> list[BarData]:
        bars: list[BarData] = []
        for vt_symbol, generator in list(self.bar_generators.items()):
            if vt_symbol not in self.bar_recordings:
                continue
            bar = generator.generate()
            if bar:
                bars.append(bar)
        return bars

    def _update_bar_generator(self, tick: TickData, *, source: str = "live_readonly") -> None:
        vt_symbol = tick.vt_symbol.upper()
        generator = self.bar_generators.get(vt_symbol)
        if not generator:
            generator = BarGenerator(lambda bar: self.record_bar(bar_to_recorded_bar(bar, source=source)))
            self.bar_generators[vt_symbol] = generator
        generator.update_tick(tick)

    def _tick_key(self, tick: TickData) -> tuple[str, datetime, float, float]:
        return (tick.vt_symbol.upper(), tick.datetime, float(tick.last_price), float(tick.volume or 0.0))

    def _has_seen_tick(self, tick: TickData) -> bool:
        return self._tick_key(tick) in self._recent_tick_keys

    def _remember_tick(self, tick: TickData) -> None:
        key = self._tick_key(tick)
        self._recent_tick_keys.append(key)
        if len(self._recent_tick_keys) > 2_000:
            self._recent_tick_keys = self._recent_tick_keys[-1_000:]


def tick_to_recorded_tick(tick: TickData, *, source: str = "live_readonly", raw: dict | None = None) -> RecordedTick:
    raw_payload = raw if raw is not None else dict(getattr(tick, "raw", {}) or {})
    return RecordedTick(
        symbol=tick.symbol.upper(),
        exchange=tick.exchange.value,
        vt_symbol=tick.vt_symbol.upper(),
        timestamp=tick.datetime,
        last_price=float(tick.last_price),
        volume=float(tick.volume or 0.0),
        bid_price_1=tick.bid_price_1,
        ask_price_1=tick.ask_price_1,
        gateway_name=tick.gateway_name,
        source=source,
        raw_json=raw_payload,
    )


def market_event_to_tick(event: MarketEvent, *, exchange: Exchange = Exchange.SMART, gateway_name: str = "OMEGA") -> TickData:
    return TickData(
        symbol=event.symbol.upper(),
        exchange=exchange,
        datetime=event.timestamp,
        last_price=float(event.last or event.price),
        volume=float(event.volume or 0.0),
        bid_price_1=event.bid,
        ask_price_1=event.ask,
        gateway_name=gateway_name,
        raw=dict(event.raw or {}),
    )


def replay_ticks(rows: Iterable[RecordedTick]) -> list[TickData]:
    ticks: list[TickData] = []
    for row in rows:
        ticks.append(
            TickData(
                symbol=row.symbol,
                exchange=Exchange(row.exchange),
                datetime=row.timestamp,
                last_price=row.last_price,
                volume=row.volume,
                bid_price_1=row.bid_price_1,
                ask_price_1=row.ask_price_1,
                gateway_name=row.gateway_name,
            )
        )
    return ticks


def bar_to_recorded_bar(bar: BarData, *, source: str = "live_readonly", raw: dict | None = None) -> RecordedBar:
    return RecordedBar(
        symbol=bar.symbol.upper(),
        exchange=bar.exchange.value,
        vt_symbol=bar.vt_symbol.upper(),
        interval=bar.interval,
        timestamp=bar.datetime,
        open_price=float(bar.open_price),
        high_price=float(bar.high_price),
        low_price=float(bar.low_price),
        close_price=float(bar.close_price),
        volume=float(bar.volume or 0.0),
        gateway_name=bar.gateway_name,
        source=source,
        raw_json=raw or {},
    )


def replay_bars(rows: Iterable[RecordedBar]) -> list[BarData]:
    bars: list[BarData] = []
    for row in rows:
        bars.append(
            BarData(
                symbol=row.symbol,
                exchange=Exchange(row.exchange),
                datetime=row.timestamp,
                interval=row.interval,
                open_price=row.open_price,
                high_price=row.high_price,
                low_price=row.low_price,
                close_price=row.close_price,
                volume=row.volume,
                gateway_name=row.gateway_name,
            )
        )
    return bars
