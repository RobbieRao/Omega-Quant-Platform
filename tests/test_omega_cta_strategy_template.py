from __future__ import annotations

from datetime import datetime, timedelta, timezone

from omega_quant.core import (
    BarData,
    CtaBacktestRequest,
    CtaBacktesterEngine,
    CtaStrategyTemplate,
    DonchianBreakoutStrategy,
    Direction,
    Exchange,
    MainEngine,
    MovingAverageCrossStrategy,
    ArrayManager,
    bar_to_recorded_bar,
    get_cta_strategy_class,
    list_cta_strategy_metadata,
)
from omega_quant.database import build_engine, build_session_factory, init_db


class LifecycleStrategy(CtaStrategyTemplate):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.init_called = False
        self.start_called = False
        self.stop_called = False
        self.trade_count = 0
        self.bar_count = 0

    def on_init(self) -> None:
        self.init_called = True
        self.inited = True

    def on_start(self) -> None:
        super().on_start()
        self.start_called = True

    def on_stop(self) -> None:
        super().on_stop()
        self.stop_called = True

    def on_bar(self, bar: BarData) -> None:
        self.bar_count += 1
        if self.bar_count == 2 and self.pos == 0:
            self.buy(bar.close_price, 1)
        elif self.bar_count == 4 and self.pos > 0:
            self.sell(bar.close_price, 1)

    def on_trade(self, trade) -> None:
        self.trade_count += 1


def _seed_trending_bars(session_factory, *, count: int = 40) -> None:
    session = session_factory()
    try:
        start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
        for idx in range(count):
            close = 100 + idx * 0.3 if idx < count // 2 else 106 - (idx - count // 2) * 0.2
            bar = BarData(
                symbol="AAPL",
                exchange=Exchange.SMART,
                datetime=start + timedelta(minutes=idx),
                interval="1m",
                open_price=close - 0.05,
                high_price=close + 0.5,
                low_price=close - 0.5,
                close_price=close,
                volume=1000,
                gateway_name="TIGEROPEN",
            )
            session.add(bar_to_recorded_bar(bar, source="unit_test"))
        session.commit()
    finally:
        session.close()


def test_cta_strategy_template_lifecycle_and_order_helpers() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    _seed_trending_bars(session_factory, count=12)

    main_engine = MainEngine()
    backtester = main_engine.add_engine(CtaBacktesterEngine, session_factory=session_factory)
    try:
        result = backtester.run_strategy_class_backtest(
            CtaBacktestRequest(vt_symbol="AAPL.SMART", interval="1m", capital=100_000, slippage=0.01),
            strategy_class=LifecycleStrategy,
            setting={},
        )
    finally:
        main_engine.close()

    assert result["status"] == "completed"
    assert result["strategy_class"] == "LifecycleStrategy"
    assert result["trade_count"] >= 2
    assert {trade["direction"] for trade in result["trades"]} >= {Direction.LONG.value, Direction.SHORT.value}
    assert round(result["trades"][0]["price"], 2) == 100.31
    assert round(result["trades"][1]["price"], 2) == 101.14
    assert result["daily_results"][0]["trading_pnl"] != 0
    assert result["vnpy_statistics"]["total_turnover"] > 0


def test_moving_average_cross_strategy_runs_through_template_path() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    _seed_trending_bars(session_factory, count=80)

    main_engine = MainEngine()
    backtester = main_engine.add_engine(CtaBacktesterEngine, session_factory=session_factory)
    try:
        result = backtester.run_strategy_class_backtest(
            CtaBacktestRequest(vt_symbol="AAPL.SMART", interval="1m", capital=100_000),
            strategy_class=MovingAverageCrossStrategy,
            setting={"fast_window": 3, "slow_window": 8, "fixed_size": 2},
        )
    finally:
        main_engine.close()

    assert result["status"] == "completed"
    assert result["strategy_class"] == "MovingAverageCrossStrategy"
    assert result["parameters"]["fast_window"] == 3
    assert result["parameters"]["slow_window"] == 8


def test_cta_strategy_registry_exposes_supported_headless_strategies() -> None:
    metadata = list_cta_strategy_metadata()
    names = {item.class_name for item in metadata}

    assert "MovingAverageCrossStrategy" in names
    assert "DonchianBreakoutStrategy" in names
    assert get_cta_strategy_class("DonchianBreakoutStrategy") is DonchianBreakoutStrategy


def test_donchian_breakout_strategy_runs_through_registered_backtest_path() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    _seed_trending_bars(session_factory, count=80)

    main_engine = MainEngine()
    backtester = main_engine.add_engine(CtaBacktesterEngine, session_factory=session_factory)
    try:
        result = backtester.run_backtest(
            CtaBacktestRequest(
                vt_symbol="AAPL.SMART",
                interval="1m",
                strategy_class="DonchianBreakoutStrategy",
                strategy_setting={"entry_window": 12, "exit_window": 6, "fixed_size": 1},
                capital=100_000,
            )
        )
    finally:
        main_engine.close()

    assert result["status"] == "completed"
    assert result["strategy_class"] == "DonchianBreakoutStrategy"
    assert result["parameters"]["entry_window"] == 12
    assert result["parameters"]["exit_window"] == 6


def test_moving_average_cross_strategy_uses_array_manager() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    main_engine = MainEngine()
    backtester = main_engine.add_engine(CtaBacktesterEngine, session_factory=session_factory)
    try:
        strategy = MovingAverageCrossStrategy(
            backtester,
            "MovingAverageCrossStrategy",
            "AAPL.SMART",
            {"fast_window": 3, "slow_window": 8, "fixed_size": 1},
        )
        assert isinstance(strategy.am, ArrayManager)
        assert strategy.am.size == 9
    finally:
        main_engine.close()


def test_strategy_send_order_returns_empty_when_not_trading() -> None:
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)
    main_engine = MainEngine()
    backtester = main_engine.add_engine(CtaBacktesterEngine, session_factory=session_factory)
    try:
        strategy = LifecycleStrategy(backtester, "LifecycleStrategy", "AAPL.SMART", {})
        assert strategy.buy(100, 1) == []
        strategy.on_start()
        assert strategy.buy(100, 1)
        strategy.cancel_all()
    finally:
        main_engine.close()
