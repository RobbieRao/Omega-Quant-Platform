"""vn.py-derived daily PnL primitives for Omega backtests.

Source basis:
- ``vnpy/alpha/strategy/backtesting.py``
- Classes ``ContractDailyResult`` and ``PortfolioDailyResult``

The original vn.py project is licensed under the MIT License:

Copyright (c) 2015-present, Xiaoyou Chen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the conditions in the repository LICENSE.

Omega adapts the code to avoid importing vn.py runtime objects directly. The
calculation semantics are intentionally kept close to vn.py: daily mark-to-
market holding PnL, trading PnL, turnover, commission, total PnL, and net PnL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


Direction = Literal["long", "short"]


@dataclass(frozen=True)
class VnpyStyleTrade:
    vt_symbol: str
    direction: Direction
    volume: float
    price: float


class ContractDailyResult:
    """Contract daily profit and loss result.

    Adapted from vn.py's ``ContractDailyResult``.
    """

    def __init__(self, result_date: date, close_price: float) -> None:
        self.date: date = result_date
        self.close_price: float = close_price
        self.pre_close: float = 0
        self.trades: list[VnpyStyleTrade] = []
        self.trade_count: int = 0
        self.start_pos: float = 0
        self.end_pos: float = 0
        self.turnover: float = 0
        self.commission: float = 0
        self.trading_pnl: float = 0
        self.holding_pnl: float = 0
        self.total_pnl: float = 0
        self.net_pnl: float = 0

    def add_trade(self, trade: VnpyStyleTrade) -> None:
        self.trades.append(trade)

    def calculate_pnl(
        self,
        pre_close: float,
        start_pos: float,
        size: float,
        long_rate: float,
        short_rate: float,
    ) -> None:
        if pre_close:
            self.pre_close = pre_close
        self.start_pos = start_pos
        self.end_pos = start_pos
        self.holding_pnl = self.start_pos * (self.close_price - self.pre_close) * size
        self.trade_count = len(self.trades)

        for trade in self.trades:
            if trade.direction == "long":
                pos_change = trade.volume
                rate = long_rate
            else:
                pos_change = -trade.volume
                rate = short_rate

            self.end_pos += pos_change
            turnover = trade.volume * size * trade.price
            self.trading_pnl += pos_change * (self.close_price - trade.price) * size
            self.turnover += turnover
            self.commission += turnover * rate

        self.total_pnl = self.trading_pnl + self.holding_pnl
        self.net_pnl = self.total_pnl - self.commission

    def update_close_price(self, close_price: float) -> None:
        self.close_price = close_price


class PortfolioDailyResult:
    """Portfolio daily profit and loss result.

    Adapted from vn.py's ``PortfolioDailyResult``.
    """

    def __init__(self, result_date: date, close_prices: dict[str, float]) -> None:
        self.date: date = result_date
        self.close_prices: dict[str, float] = close_prices
        self.pre_closes: dict[str, float] = {}
        self.start_poses: dict[str, float] = {}
        self.end_poses: dict[str, float] = {}
        self.contract_results: dict[str, ContractDailyResult] = {
            vt_symbol: ContractDailyResult(result_date, close_price)
            for vt_symbol, close_price in close_prices.items()
        }
        self.trade_count: int = 0
        self.turnover: float = 0
        self.commission: float = 0
        self.trading_pnl: float = 0
        self.holding_pnl: float = 0
        self.total_pnl: float = 0
        self.net_pnl: float = 0

    def add_trade(self, trade: VnpyStyleTrade) -> None:
        self.contract_results[trade.vt_symbol].add_trade(trade)

    def calculate_pnl(
        self,
        pre_closes: dict[str, float],
        start_poses: dict[str, float],
        sizes: dict[str, float],
        long_rates: dict[str, float],
        short_rates: dict[str, float],
    ) -> None:
        self.pre_closes = pre_closes
        self.start_poses = start_poses

        for vt_symbol, contract_result in self.contract_results.items():
            contract_result.calculate_pnl(
                pre_closes.get(vt_symbol, 0),
                start_poses.get(vt_symbol, 0),
                sizes[vt_symbol],
                long_rates[vt_symbol],
                short_rates[vt_symbol],
            )
            self.trade_count += contract_result.trade_count
            self.turnover += contract_result.turnover
            self.commission += contract_result.commission
            self.trading_pnl += contract_result.trading_pnl
            self.holding_pnl += contract_result.holding_pnl
            self.total_pnl += contract_result.total_pnl
            self.net_pnl += contract_result.net_pnl
            self.end_poses[vt_symbol] = contract_result.end_pos

    def update_close_prices(self, close_prices: dict[str, float]) -> None:
        self.close_prices.update(close_prices)
        for vt_symbol, close_price in close_prices.items():
            contract_result = self.contract_results.get(vt_symbol)
            if contract_result:
                contract_result.update_close_price(close_price)
            else:
                self.contract_results[vt_symbol] = ContractDailyResult(self.date, close_price)
