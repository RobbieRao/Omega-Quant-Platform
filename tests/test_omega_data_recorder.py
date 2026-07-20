from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from omega_quant.core import DataRecorderEngine, EVENT_TICK, Event, EventEngine, Exchange, MainEngine, TickData, market_event_to_tick, replay_bars, replay_ticks
from omega_quant.core.runtime import OmegaCoreRuntime
from omega_quant.core.data_recorder import tick_to_recorded_tick
from omega_quant.database import build_engine, build_session_factory, init_db
from omega_quant.market_events import new_market_event
from omega_quant.models import RecordedBar, RecordedTick


def test_market_event_converts_to_tick_data_for_us_symbol() -> None:
    event = new_market_event(
        source="live_readonly",
        symbol="AAPL",
        timestamp=datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc),
        price=140.0,
        volume=1000,
        bid=139.95,
        ask=140.05,
        last=140.0,
        data_quality="unit_test",
    )

    tick = market_event_to_tick(event, exchange=Exchange.SMART, gateway_name="TIGEROPEN")

    assert tick.symbol == "AAPL"
    assert tick.vt_symbol == "AAPL.SMART"
    assert tick.last_price == 140.0
    assert tick.bid_price_1 == 139.95
    assert tick.ask_price_1 == 140.05
    assert tick.gateway_name == "TIGEROPEN"


def test_data_recorder_records_tick_event_to_sqlite_and_replays() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    main_engine = MainEngine(auto_start=False)
    recorder = DataRecorderEngine(main_engine, main_engine.event_engine, session_factory)
    recorder.add_tick_recording("AAPL.SMART")

    event = new_market_event(
        source="live_readonly",
        symbol="AAPL",
        timestamp=datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc),
        price=140.0,
        volume=1000,
        bid=139.95,
        ask=140.05,
        last=140.0,
        data_quality="unit_test",
    )
    tick = market_event_to_tick(event)
    try:
        main_engine.event_engine.put(Event(EVENT_TICK, tick))
        deadline = time.time() + 2
        rows = []
        while time.time() < deadline:
            rows = recorder.load_ticks("AAPL.SMART")
            if rows:
                break
            time.sleep(0.01)
    finally:
        recorder.close()
        main_engine.close()

    assert len(rows) == 1
    assert rows[0].symbol == "AAPL"
    assert rows[0].last_price == 140.0
    replayed = replay_ticks(rows)
    assert replayed[0].vt_symbol == "AAPL.SMART"
    assert replayed[0].last_price == 140.0


def test_data_recorder_requires_explicit_tick_or_bar_recording() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    main_engine = MainEngine(auto_start=False)
    recorder = DataRecorderEngine(main_engine, main_engine.event_engine, session_factory)
    event = new_market_event(
        source="live_readonly",
        symbol="AAPL",
        timestamp=datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc),
        price=140.0,
        data_quality="unit_test",
    )
    try:
        main_engine.event_engine.put(Event(EVENT_TICK, market_event_to_tick(event)))
        time.sleep(0.1)
        assert recorder.load_ticks("AAPL.SMART") == []
        assert recorder.load_bars("AAPL.SMART") == []
    finally:
        recorder.close()
        main_engine.close()


def test_main_engine_can_add_data_recorder_with_session_factory() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    main_engine = MainEngine(auto_start=False)
    recorder = main_engine.add_engine(DataRecorderEngine, session_factory=session_factory)
    try:
        recorder.add_tick_recording("AAPL.SMART")
        event = new_market_event(
            source="live_readonly",
            symbol="AAPL",
            timestamp=datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc),
            price=140.0,
            data_quality="unit_test",
        )
        main_engine.event_engine.put(Event(EVENT_TICK, market_event_to_tick(event)))
        deadline = time.time() + 2
        while not recorder.load_ticks("AAPL.SMART") and time.time() < deadline:
            time.sleep(0.01)
        assert len(recorder.load_ticks("AAPL.SMART")) == 1
    finally:
        main_engine.close()


def test_data_recorder_ignores_unregistered_symbol_when_filter_is_set() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    recorder = DataRecorderEngine(main_engine, event_engine, session_factory)
    recorder.add_tick_recording("MSFT.SMART")

    event = new_market_event(
        source="live_readonly",
        symbol="AAPL",
        timestamp=datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc),
        price=140.0,
        data_quality="unit_test",
    )
    try:
        event_engine.put(Event(EVENT_TICK, market_event_to_tick(event)))
        time.sleep(0.1)
        assert recorder.load_ticks("AAPL.SMART") == []
    finally:
        recorder.close()
        main_engine.close()


def test_tick_and_bar_tables_are_created_by_init_db() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        assert session.query(RecordedTick).count() == 0
        assert session.query(RecordedBar).count() == 0
    finally:
        session.close()


def test_tick_to_recorded_tick_preserves_raw_payload() -> None:
    event = new_market_event(
        source="live_readonly",
        symbol="NVDA",
        timestamp=datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc),
        price=900.0,
        data_quality="unit_test",
        raw={"provider": "massive"},
    )
    tick = market_event_to_tick(event, gateway_name="TIGEROPEN")
    row = tick_to_recorded_tick(tick, source=event.source, raw=event.raw)

    assert row.vt_symbol == "NVDA.SMART"
    assert row.source == "live_readonly"
    assert row.raw_json == {"provider": "massive"}


def test_data_recorder_generates_minute_bars_from_live_ticks() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    main_engine = MainEngine(auto_start=False)
    recorder = main_engine.add_engine(DataRecorderEngine, session_factory=session_factory)
    recorder.add_bar_recording("AAPL.SMART")
    start = datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc)
    try:
        for tick in [
            TickData("AAPL", Exchange.SMART, start, 100.0, volume=10, gateway_name="TIGEROPEN"),
            TickData("AAPL", Exchange.SMART, start + timedelta(seconds=20), 101.0, volume=15, gateway_name="TIGEROPEN"),
            TickData("AAPL", Exchange.SMART, start + timedelta(minutes=1, seconds=5), 99.5, volume=30, gateway_name="TIGEROPEN"),
        ]:
            main_engine.event_engine.put(Event(EVENT_TICK, tick))
        deadline = time.time() + 2
        rows = []
        while time.time() < deadline:
            rows = recorder.load_bars("AAPL.SMART")
            if rows:
                break
            time.sleep(0.01)
    finally:
        main_engine.close()

    assert len(rows) == 1
    assert rows[0].timestamp == start.replace(tzinfo=None)
    assert rows[0].open_price == 100.0
    assert rows[0].high_price == 101.0
    assert rows[0].low_price == 100.0
    assert rows[0].close_price == 101.0
    assert rows[0].volume == 15.0
    replayed = replay_bars(rows)
    assert replayed[0].vt_symbol == "AAPL.SMART"


def test_data_recorder_can_flush_unfinished_generated_bar() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    main_engine = MainEngine(auto_start=False)
    recorder = main_engine.add_engine(DataRecorderEngine, session_factory=session_factory)
    recorder.add_bar_recording("AAPL.SMART")
    start = datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc)
    try:
        recorder.record_tick_once(TickData("AAPL", Exchange.SMART, start, 100.0, volume=10, gateway_name="TIGEROPEN"))
        flushed = recorder.flush_bar_generators()
        rows = recorder.load_bars("AAPL.SMART")
    finally:
        main_engine.close()

    assert len(flushed) == 1
    assert len(rows) == 1
    assert rows[0].close_price == 100.0


def test_data_recorder_deduplicates_sync_and_event_engine_race() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    main_engine = MainEngine(auto_start=False)
    recorder = main_engine.add_engine(DataRecorderEngine, session_factory=session_factory)
    recorder.add_bar_recording("AAPL.SMART")
    start = datetime(2026, 5, 24, 14, 30, tzinfo=timezone.utc)
    first = TickData("AAPL", Exchange.SMART, start, 100.0, volume=10, gateway_name="TIGEROPEN")
    second = TickData("AAPL", Exchange.SMART, start + timedelta(seconds=20), 101.0, volume=15, gateway_name="TIGEROPEN")
    third = TickData("AAPL", Exchange.SMART, start + timedelta(minutes=1, seconds=5), 102.0, volume=22, gateway_name="TIGEROPEN")
    try:
        for tick in (first, second, third):
            main_engine.event_engine.put(Event(EVENT_TICK, tick))
            recorder.record_tick_once(tick)
        main_engine.event_engine.process_pending()
        rows = recorder.load_bars("AAPL.SMART")
    finally:
        main_engine.close()

    assert len(rows) == 1
    assert rows[0].timestamp == start.replace(tzinfo=None)
    assert rows[0].open_price == 100.0
    assert rows[0].close_price == 101.0


def test_runtime_persists_and_restores_data_recorder_settings(monkeypatch) -> None:
    monkeypatch.setattr("omega_quant.gateways.tigeropen_gateway.build_trade_client", lambda: object())
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)

    runtime = OmegaCoreRuntime(session_factory=session_factory)
    try:
        status = runtime.add_tick_recording("AAPL.SMART")
        assert status["tick_recordings"] == ["AAPL.SMART"]
        status = runtime.add_bar_recording("MSFT.SMART")
        assert status["bar_recordings"] == ["MSFT.SMART"]
    finally:
        runtime.close()

    restored = OmegaCoreRuntime(session_factory=session_factory)
    try:
        status = restored.recording_status()
        assert status["tick_recordings"] == ["AAPL.SMART"]
        assert status["bar_recordings"] == ["MSFT.SMART"]
        restored.remove_tick_recording("AAPL.SMART")
    finally:
        restored.close()

    restored_again = OmegaCoreRuntime(session_factory=session_factory)
    try:
        status = restored_again.recording_status()
        assert status["tick_recordings"] == []
        assert status["bar_recordings"] == ["MSFT.SMART"]
    finally:
        restored_again.close()
