from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from omega_quant.core import ArrayManager, BarData, BarGenerator, Exchange, TickData


def _tick(second: int, price: float, volume: float) -> TickData:
    return TickData(
        symbol="AAPL",
        exchange=Exchange.SMART,
        datetime=datetime(2026, 5, 24, 13, 30, second, tzinfo=timezone.utc),
        last_price=price,
        volume=volume,
        gateway_name="TIGEROPEN",
    )


def test_bar_generator_builds_minute_bars_from_ticks_and_flushes() -> None:
    bars: list[BarData] = []
    generator = BarGenerator(bars.append)

    generator.update_tick(_tick(0, 100.0, 10))
    generator.update_tick(_tick(20, 101.0, 15))
    generator.update_tick(
        TickData(
            symbol="AAPL",
            exchange=Exchange.SMART,
            datetime=datetime(2026, 5, 24, 13, 31, 1, tzinfo=timezone.utc),
            last_price=99.5,
            volume=30,
            gateway_name="TIGEROPEN",
        )
    )
    flushed = generator.generate()

    assert len(bars) == 2
    assert bars[0].datetime == datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
    assert bars[0].open_price == 100.0
    assert bars[0].high_price == 101.0
    assert bars[0].low_price == 100.0
    assert bars[0].close_price == 101.0
    assert bars[0].volume == 15.0
    assert flushed == bars[-1]
    assert bars[-1].close_price == 99.5


def test_bar_generator_emits_window_bars() -> None:
    minute_bars: list[BarData] = []
    window_bars: list[BarData] = []
    generator = BarGenerator(minute_bars.append, window=2, on_window_bar=window_bars.append)
    start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)

    for idx, close in enumerate([100.0, 101.0]):
        generator.update_bar(
            BarData(
                symbol="AAPL",
                exchange=Exchange.SMART,
                datetime=start + timedelta(minutes=idx),
                interval="1m",
                open_price=close - 0.1,
                high_price=close + 0.5,
                low_price=close - 0.5,
                close_price=close,
                volume=100 + idx,
                gateway_name="TIGEROPEN",
            )
        )

    assert minute_bars == []
    assert len(window_bars) == 1
    assert window_bars[0].interval == "2m"
    assert window_bars[0].open_price == 99.9
    assert window_bars[0].high_price == 101.5
    assert window_bars[0].low_price == 99.5
    assert window_bars[0].close_price == 101.0
    assert window_bars[0].volume == 201


def test_array_manager_tracks_rolling_ohlcv_and_indicators() -> None:
    manager = ArrayManager(size=5)
    start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)

    for idx, close in enumerate([100, 101, 102, 103, 104]):
        manager.update_bar(
            BarData(
                symbol="AAPL",
                exchange=Exchange.SMART,
                datetime=start + timedelta(minutes=idx),
                interval="1m",
                open_price=close - 0.5,
                high_price=close + 1,
                low_price=close - 1,
                close_price=close,
                volume=1000 + idx,
            )
        )

    assert manager.inited
    assert manager.close.tolist() == [100, 101, 102, 103, 104]
    assert manager.sma(3) == pytest.approx(103.0)
    assert manager.ema(3) > 102
    assert manager.atr(3) == pytest.approx(2.0)


def test_bar_generator_requires_window_callback() -> None:
    with pytest.raises(ValueError):
        BarGenerator(lambda bar: None, window=2)
