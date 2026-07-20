from __future__ import annotations

from datetime import datetime, timedelta, timezone

from omega_quant.core import BarData, CtaBacktestRequest, CtaBacktesterEngine, DataManagerEngine, Exchange, MainEngine, TickData, bar_to_recorded_bar
from omega_quant.core.data_recorder import tick_to_recorded_tick
from omega_quant.database import build_engine, build_session_factory, init_db


def _seed_bars(session_factory, *, symbol: str = "AAPL", count: int = 60) -> None:
    session = session_factory()
    try:
        start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
        price = 100.0
        for idx in range(count):
            drift = 0.35 if idx < count // 2 else -0.18
            price += drift
            close = round(price, 4)
            bar = BarData(
                symbol=symbol,
                exchange=Exchange.SMART,
                datetime=start + timedelta(minutes=idx),
                interval="1m",
                open_price=close - 0.1,
                high_price=close + 0.3,
                low_price=close - 0.3,
                close_price=close,
                volume=1000 + idx,
                gateway_name="TIGEROPEN",
            )
            session.add(bar_to_recorded_bar(bar, source="unit_test"))
        session.commit()
    finally:
        session.close()


def _seed_ticks(session_factory, *, symbol: str = "AAPL", count: int = 60) -> None:
    session = session_factory()
    try:
        start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
        price = 100.0
        for idx in range(count):
            drift = 0.35 if idx < count // 2 else -0.18
            price += drift
            tick = TickData(
                symbol,
                Exchange.SMART,
                start + timedelta(minutes=idx, seconds=5),
                round(price, 4),
                volume=1_000 + idx,
                gateway_name="TIGEROPEN",
            )
            session.add(tick_to_recorded_tick(tick, source="unit_test"))
        session.commit()
    finally:
        session.close()


def test_data_manager_overview_load_export_and_delete_bars() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    _seed_bars(session_factory, count=5)

    main_engine = MainEngine()
    manager = main_engine.add_engine(DataManagerEngine, session_factory=session_factory)
    try:
        overview = manager.overview()
        assert len(overview) == 1
        assert overview[0].vt_symbol == "AAPL.SMART"
        assert overview[0].data_type == "bar"
        assert overview[0].count == 5

        bars = manager.load_bars("AAPL.SMART", interval="1m")
        assert len(bars) == 5
        csv_text = manager.export_bars_csv("AAPL.SMART", interval="1m")
        assert "datetime,symbol,exchange,interval,open,high,low,close,volume" in csv_text
        assert "AAPL" in csv_text

        deleted = manager.delete_symbol_data("AAPL.SMART")
        assert deleted == {"ticks_deleted": 0, "bars_deleted": 5}
        assert manager.overview() == []
    finally:
        main_engine.close()


def test_data_manager_import_bars_csv_skips_duplicates() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    csv_text = "\n".join(
        [
            "datetime,symbol,exchange,interval,open,high,low,close,volume",
            "2026-05-24T13:30:00,AAPL,SMART,1m,100,101,99,100.5,1000",
            "2026-05-24T13:31:00,AAPL,SMART,1m,100.5,102,100,101.5,1100",
            "2026-05-24T13:31:00,AAPL,SMART,1m,100.5,102,100,101.5,1100",
        ]
    )

    main_engine = MainEngine()
    manager = main_engine.add_engine(DataManagerEngine, session_factory=session_factory)
    try:
        result = manager.import_bars_csv(csv_text, vt_symbol="AAPL.SMART", interval="1m", source="unit_import")
        second = manager.import_bars_csv(csv_text, vt_symbol="AAPL.SMART", interval="1m", source="unit_import")
        bars = manager.load_bars("AAPL.SMART", interval="1m")
    finally:
        main_engine.close()

    assert result == {
        "vt_symbol": "AAPL.SMART",
        "interval": "1m",
        "source": "unit_import",
        "rows_read": 3,
        "bars_created": 2,
        "bars_skipped": 1,
    }
    assert second["bars_created"] == 0
    assert second["bars_skipped"] == 3
    assert len(bars) == 2


def test_cta_backtester_runs_on_recorded_bars() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    _seed_bars(session_factory, count=80)

    main_engine = MainEngine()
    backtester = main_engine.add_engine(CtaBacktesterEngine, session_factory=session_factory)
    try:
        result = backtester.run_backtest(
            CtaBacktestRequest(
                vt_symbol="AAPL.SMART",
                interval="1m",
                fast_window=3,
                slow_window=8,
                capital=100_000,
                rate=0.0005,
                slippage=0.01,
            )
        )
    finally:
        main_engine.close()

    assert result["status"] == "completed"
    assert result["bar_count"] == 80
    assert "final_equity" in result
    assert "total_commission" in result
    assert "total_turnover" in result
    assert result["daily_results"]
    assert result["vnpy_statistics"]["total_days"] >= 1
    assert result["vnpy_statistics"]["total_trade_count"] == result["trade_count"]
    assert result["final_equity"] == result["vnpy_statistics"]["end_balance"]
    assert result["final_equity"] == result["capital"] + sum(row["net_pnl"] for row in result["daily_results"])
    assert result["total_turnover"] == sum(row["turnover"] for row in result["daily_results"])
    assert result["pnl_source"].startswith("vn.py-style PortfolioDailyResult")
    assert result["parameters"]["fast_window"] == 3
    assert result["parameters"]["slow_window"] == 8


def test_cta_backtester_reports_insufficient_data() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    _seed_bars(session_factory, count=3)

    main_engine = MainEngine()
    backtester = main_engine.add_engine(CtaBacktesterEngine, session_factory=session_factory)
    try:
        result = backtester.run_backtest(CtaBacktestRequest(vt_symbol="AAPL.SMART", fast_window=3, slow_window=8))
    finally:
        main_engine.close()

    assert result["status"] == "insufficient_data"
    assert result["bar_count"] == 3


def test_cta_backtester_runs_on_recorded_ticks_via_bar_generator() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    _seed_ticks(session_factory, count=80)

    main_engine = MainEngine()
    backtester = main_engine.add_engine(CtaBacktesterEngine, session_factory=session_factory)
    try:
        result = backtester.run_backtest(
            CtaBacktestRequest(
                vt_symbol="AAPL.SMART",
                interval="1m",
                data_source="tick",
                fast_window=3,
                slow_window=8,
                capital=100_000,
            )
        )
    finally:
        main_engine.close()

    assert result["status"] == "completed"
    assert result["data_source"] == "tick"
    assert result["tick_count"] == 80
    assert result["bar_count"] == 80
    assert "BarGenerator" in result["source"]
    assert "final_equity" in result


def test_cta_backtester_reports_insufficient_tick_data() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    _seed_ticks(session_factory, count=3)

    main_engine = MainEngine()
    backtester = main_engine.add_engine(CtaBacktesterEngine, session_factory=session_factory)
    try:
        result = backtester.run_backtest(
            CtaBacktestRequest(vt_symbol="AAPL.SMART", data_source="tick", fast_window=3, slow_window=8)
        )
    finally:
        main_engine.close()

    assert result["status"] == "insufficient_data"
    assert result["data_source"] == "tick"
    assert result["tick_count"] == 3
    assert result["bar_count"] == 3


def test_cta_backtester_grid_optimization() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    _seed_bars(session_factory, count=80)

    main_engine = MainEngine()
    backtester = main_engine.add_engine(CtaBacktesterEngine, session_factory=session_factory)
    try:
        results = backtester.optimize(
            CtaBacktestRequest(vt_symbol="AAPL.SMART", interval="1m", capital=100_000),
            fast_values=[2, 3],
            slow_values=[5, 8],
        )
    finally:
        main_engine.close()

    assert results
    assert all(item["fast_window"] < item["slow_window"] for item in results)
    assert "result" in results[0]


def test_data_manager_aggregates_recorded_ticks_to_minute_bars() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    session = session_factory()
    try:
        start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
        ticks = [
            TickData("AAPL", Exchange.SMART, start, 100.0, volume=10, gateway_name="TIGEROPEN"),
            TickData("AAPL", Exchange.SMART, start + timedelta(seconds=20), 101.0, volume=20, gateway_name="TIGEROPEN"),
            TickData("AAPL", Exchange.SMART, start + timedelta(minutes=1, seconds=5), 102.0, volume=30, gateway_name="TIGEROPEN"),
        ]
        for tick in ticks:
            session.add(tick_to_recorded_tick(tick, source="unit_test"))
        session.commit()
    finally:
        session.close()

    main_engine = MainEngine()
    manager = main_engine.add_engine(DataManagerEngine, session_factory=session_factory)
    try:
        result = manager.aggregate_ticks_to_bars("AAPL.SMART")
        bars = manager.load_bars("AAPL.SMART", interval="1m")
        duplicate_result = manager.aggregate_ticks_to_bars("AAPL.SMART")
    finally:
        main_engine.close()

    assert result["ticks_read"] == 3
    assert result["bars_created"] == 2
    assert duplicate_result["bars_created"] == 0
    assert duplicate_result["bars_skipped"] == 2
    assert len(bars) == 2
    assert bars[0].open_price == 100.0
    assert bars[0].high_price == 101.0
    assert bars[0].low_price == 100.0
    assert bars[0].close_price == 101.0
    assert bars[0].volume == 30.0
