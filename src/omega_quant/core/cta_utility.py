"""vn.py-style CTA utilities adapted for Omega.

The original vn.py CTA stack relies heavily on BarGenerator and ArrayManager.
Omega keeps the same concepts but limits them to the headless US stock/option
objects used by the Web backend.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

import numpy as np

from .objects import BarData, TickData


BarCallback = Callable[[BarData], None]


class BarGenerator:
    """Generate minute bars from ticks and optional multi-minute window bars."""

    def __init__(self, on_bar: BarCallback, window: int = 0, on_window_bar: BarCallback | None = None) -> None:
        if window < 0:
            raise ValueError("window must be >= 0")
        if window and not on_window_bar:
            raise ValueError("on_window_bar is required when window > 0")
        self.on_bar = on_bar
        self.window = window
        self.on_window_bar = on_window_bar
        self.bar: BarData | None = None
        self.window_bar: BarData | None = None
        self.last_tick: TickData | None = None

    def update_tick(self, tick: TickData) -> None:
        """Update with a tick and emit completed 1-minute bars."""

        if tick.last_price <= 0:
            return

        minute_dt = tick.datetime.replace(second=0, microsecond=0)
        if self.bar and self.bar.datetime != minute_dt:
            completed = self.bar
            self.on_bar(completed)
            self.update_bar(completed)
            self.bar = None

        if not self.bar:
            self.bar = BarData(
                symbol=tick.symbol,
                exchange=tick.exchange,
                datetime=minute_dt,
                interval="1m",
                open_price=tick.last_price,
                high_price=tick.last_price,
                low_price=tick.last_price,
                close_price=tick.last_price,
                volume=0.0,
                gateway_name=tick.gateway_name,
            )
        else:
            self.bar = replace(
                self.bar,
                high_price=max(self.bar.high_price, tick.last_price),
                low_price=min(self.bar.low_price, tick.last_price),
                close_price=tick.last_price,
            )

        volume_delta = 0.0
        if self.last_tick and tick.volume >= self.last_tick.volume:
            volume_delta = tick.volume - self.last_tick.volume
        elif not self.last_tick:
            volume_delta = tick.volume
        self.bar = replace(self.bar, volume=self.bar.volume + max(volume_delta, 0.0))
        self.last_tick = tick

    def update_bar(self, bar: BarData) -> None:
        """Update with a completed minute bar and emit window bars."""

        if not self.window:
            return
        if not self.window_bar:
            self.window_bar = replace(bar, interval=f"{self.window}m")
        else:
            self.window_bar = replace(
                self.window_bar,
                high_price=max(self.window_bar.high_price, bar.high_price),
                low_price=min(self.window_bar.low_price, bar.low_price),
                close_price=bar.close_price,
                volume=self.window_bar.volume + bar.volume,
            )

        minute_index = bar.datetime.minute
        if (minute_index + 1) % self.window == 0:
            completed = self.window_bar
            self.window_bar = None
            if self.on_window_bar:
                self.on_window_bar(completed)

    def generate(self) -> BarData | None:
        """Flush the current unfinished minute bar."""

        if not self.bar:
            return None
        completed = self.bar
        self.on_bar(completed)
        self.update_bar(completed)
        self.bar = None
        return completed


class ArrayManager:
    """Rolling OHLCV container with common indicators."""

    def __init__(self, size: int = 100) -> None:
        if size <= 0:
            raise ValueError("size must be > 0")
        self.size = size
        self.count = 0
        self.open_array = np.zeros(size, dtype=float)
        self.high_array = np.zeros(size, dtype=float)
        self.low_array = np.zeros(size, dtype=float)
        self.close_array = np.zeros(size, dtype=float)
        self.volume_array = np.zeros(size, dtype=float)

    @property
    def inited(self) -> bool:
        return self.count >= self.size

    def update_bar(self, bar: BarData) -> None:
        self.count += 1
        for array, value in (
            (self.open_array, bar.open_price),
            (self.high_array, bar.high_price),
            (self.low_array, bar.low_price),
            (self.close_array, bar.close_price),
            (self.volume_array, bar.volume),
        ):
            array[:-1] = array[1:]
            array[-1] = float(value)

    @property
    def open(self) -> np.ndarray:
        return self.open_array

    @property
    def high(self) -> np.ndarray:
        return self.high_array

    @property
    def low(self) -> np.ndarray:
        return self.low_array

    @property
    def close(self) -> np.ndarray:
        return self.close_array

    @property
    def volume(self) -> np.ndarray:
        return self.volume_array

    def sma(self, n: int, array: bool = False) -> float | np.ndarray:
        if n <= 0:
            raise ValueError("n must be > 0")
        values = _rolling_mean(self.close_array, n)
        return values if array else float(values[-1])

    def ema(self, n: int, array: bool = False) -> float | np.ndarray:
        if n <= 0:
            raise ValueError("n must be > 0")
        alpha = 2.0 / (n + 1.0)
        result = np.zeros_like(self.close_array)
        result[0] = self.close_array[0]
        for idx in range(1, len(self.close_array)):
            result[idx] = alpha * self.close_array[idx] + (1 - alpha) * result[idx - 1]
        return result if array else float(result[-1])

    def atr(self, n: int, array: bool = False) -> float | np.ndarray:
        if n <= 0:
            raise ValueError("n must be > 0")
        previous_close = np.roll(self.close_array, 1)
        previous_close[0] = self.close_array[0]
        true_range = np.maximum.reduce(
            [
                self.high_array - self.low_array,
                np.abs(self.high_array - previous_close),
                np.abs(self.low_array - previous_close),
            ]
        )
        values = _rolling_mean(true_range, n)
        return values if array else float(values[-1])


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    result = np.zeros_like(values, dtype=float)
    if window > len(values):
        return result
    cumsum = np.cumsum(np.insert(values, 0, 0.0))
    result[window - 1 :] = (cumsum[window:] - cumsum[:-window]) / float(window)
    return result
