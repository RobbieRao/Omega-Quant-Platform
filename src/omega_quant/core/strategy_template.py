"""vn.py-style CTA strategy template adapted for Omega."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from typing import TYPE_CHECKING

import numpy as np

from .cta_utility import ArrayManager
from .objects import BarData, Direction, OrderData, OrderType, TradeData

if TYPE_CHECKING:
    from .cta_backtester import CtaBacktesterEngine


CATEGORY_STOCK = "stock"
CATEGORY_OPTION = "option"
ALLOWED_CATEGORIES = (CATEGORY_STOCK, CATEGORY_OPTION)
# 可用 tag (与回测/订阅周期对齐): tick + 常见分钟/小时/日/周
ALLOWED_TAGS = ("tick", "1m", "5m", "15m", "30m", "1h", "1d", "1w")


class CtaStrategyTemplate(ABC):
    """Headless CTA strategy base class.

    This mirrors vn.py's strategy template lifecycle and order helper methods,
    but is scoped to Omega's Web backend and US stock/option objects.
    """

    author = "Omega"
    # 大类: "stock" (美股) 或 "option" (美股期权)。空字符串 = 未分类。
    category: str = ""
    # 周期 tag: tick / 1m / 5m / 15m / 30m / 1h / 1d / 1w。可多选。
    tags: list[str] = []
    parameters: list[str] = []
    variables: list[str] = ["inited", "trading", "pos"]

    def __init__(
        self,
        strategy_engine: "CtaBacktesterEngine",
        strategy_name: str,
        vt_symbol: str,
        setting: dict,
    ) -> None:
        self.strategy_engine = strategy_engine
        self.strategy_name = strategy_name
        self.vt_symbol = vt_symbol.upper()
        self.inited = False
        self.trading = False
        self.pos = 0.0
        self.pos_data: dict[str, float] = defaultdict(float)
        self.orders: dict[str, OrderData] = {}
        self.active_orderids: set[str] = set()
        for key, value in setting.items():
            if hasattr(self, key):
                setattr(self, key, value)

    @abstractmethod
    def on_init(self) -> None:
        pass

    def on_start(self) -> None:
        self.trading = True

    def on_stop(self) -> None:
        self.trading = False

    def on_tick(self, tick) -> None:
        return

    @abstractmethod
    def on_bar(self, bar: BarData) -> None:
        pass

    def on_order(self, order: OrderData) -> None:
        return

    def on_trade(self, trade: TradeData) -> None:
        return

    def update_order(self, order: OrderData) -> None:
        self.orders[order.vt_orderid] = order
        if order.is_active():
            self.active_orderids.add(order.vt_orderid)
        else:
            self.active_orderids.discard(order.vt_orderid)
        self.on_order(order)

    def update_trade(self, trade: TradeData) -> None:
        if trade.direction == Direction.LONG:
            self.pos += trade.volume
            self.pos_data[trade.vt_symbol] += trade.volume
        else:
            self.pos -= trade.volume
            self.pos_data[trade.vt_symbol] -= trade.volume
        self.on_trade(trade)

    # ── 限价 (默认) ────────────────────────────────────────────
    def buy(self, price: float, volume: float) -> list[str]:
        """限价买入。回测引擎不会在同一根 bar 内成交此单。"""
        return self.send_order(Direction.LONG, price, volume, order_type=OrderType.LIMIT)

    def sell(self, price: float, volume: float) -> list[str]:
        """限价卖出。回测引擎不会在同一根 bar 内成交此单。"""
        return self.send_order(Direction.SHORT, price, volume, order_type=OrderType.LIMIT)

    # ── 市价 ───────────────────────────────────────────────────
    def buy_market(self, volume: float) -> list[str]:
        """市价买入。下一根 bar 以 open + slippage 成交。"""
        return self.send_order(Direction.LONG, 0.0, volume, order_type=OrderType.MARKET)

    def sell_market(self, volume: float) -> list[str]:
        """市价卖出。下一根 bar 以 open - slippage 成交。"""
        return self.send_order(Direction.SHORT, 0.0, volume, order_type=OrderType.MARKET)

    # ── 止损 / 触发市价 ────────────────────────────────────────
    def buy_stop(self, trigger_price: float, volume: float) -> list[str]:
        """买入止损 (突破触发价后市价买入)。"""
        return self.send_order(Direction.LONG, trigger_price, volume,
                                order_type=OrderType.STOP, stop_price=trigger_price)

    def sell_stop(self, trigger_price: float, volume: float) -> list[str]:
        """卖出止损 (跌破触发价后市价卖出)。"""
        return self.send_order(Direction.SHORT, trigger_price, volume,
                                order_type=OrderType.STOP, stop_price=trigger_price)

    # ── 止损限价 ───────────────────────────────────────────────
    def buy_stop_limit(self, trigger_price: float, limit_price: float, volume: float) -> list[str]:
        """触发后挂限价买单。trigger 通常 >= limit。"""
        return self.send_order(Direction.LONG, limit_price, volume,
                                order_type=OrderType.STOP_LIMIT, stop_price=trigger_price)

    def sell_stop_limit(self, trigger_price: float, limit_price: float, volume: float) -> list[str]:
        """触发后挂限价卖单。trigger 通常 <= limit。"""
        return self.send_order(Direction.SHORT, limit_price, volume,
                                order_type=OrderType.STOP_LIMIT, stop_price=trigger_price)

    def send_order(
        self,
        direction: Direction,
        price: float,
        volume: float,
        order_type: OrderType = OrderType.LIMIT,
        stop_price: float | None = None,
    ) -> list[str]:
        if not self.trading:
            return []
        orderids = self.strategy_engine.send_order(
            self, self.vt_symbol, direction, price, volume,
            order_type=order_type, stop_price=stop_price,
        )
        self.active_orderids.update(orderids)
        return orderids

    def cancel_order(self, vt_orderid: str) -> None:
        self.strategy_engine.cancel_order(self, vt_orderid)

    def cancel_all(self) -> None:
        for vt_orderid in list(self.active_orderids):
            self.cancel_order(vt_orderid)

    def write_log(self, msg: str) -> None:
        self.strategy_engine.write_log(msg, self)


class MovingAverageCrossStrategy(CtaStrategyTemplate):
    """Simple CTA demo strategy using moving-average crossovers."""

    author = "Omega"
    category = CATEGORY_STOCK
    tags = ["1h", "1d"]
    fast_window = 5
    slow_window = 20
    fixed_size = 1.0
    parameters = ["fast_window", "slow_window", "fixed_size"]
    variables = CtaStrategyTemplate.variables + ["fast_ma", "slow_ma"]

    def __init__(self, strategy_engine: "CtaBacktesterEngine", strategy_name: str, vt_symbol: str, setting: dict) -> None:
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        self.am = ArrayManager(size=max(self.slow_window + 1, self.fast_window + 1))
        self.fast_ma = 0.0
        self.slow_ma = 0.0

    def on_init(self) -> None:
        self.inited = True

    def on_bar(self, bar: BarData) -> None:
        self.am.update_bar(bar)
        if not self.am.inited:
            return
        fast_array = self.am.sma(self.fast_window, array=True)
        slow_array = self.am.sma(self.slow_window, array=True)
        self.fast_ma = float(fast_array[-1])
        self.slow_ma = float(slow_array[-1])
        previous_fast = float(fast_array[-2])
        previous_slow = float(slow_array[-2])
        cross_up = previous_fast <= previous_slow and self.fast_ma > self.slow_ma
        cross_down = previous_fast >= previous_slow and self.fast_ma < self.slow_ma
        # 取消任何尚未成交的旧挂单 — pending 信号被新 bar 覆盖, 与 vnpy 惯例一致
        if self.active_orderids:
            self.cancel_all()
        if cross_up and self.pos <= 0:
            self.buy(bar.close_price, self.fixed_size)
        elif cross_down and self.pos > 0:
            self.sell(bar.close_price, abs(self.pos))


class DonchianBreakoutStrategy(CtaStrategyTemplate):
    """Classic channel breakout CTA strategy for US stocks/options bars."""

    author = "Omega"
    category = CATEGORY_STOCK
    tags = ["1d"]
    entry_window = 20
    exit_window = 10
    fixed_size = 1.0
    parameters = ["entry_window", "exit_window", "fixed_size"]
    variables = CtaStrategyTemplate.variables + ["entry_up", "entry_down", "exit_up", "exit_down"]

    def __init__(self, strategy_engine: "CtaBacktesterEngine", strategy_name: str, vt_symbol: str, setting: dict) -> None:
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        size = max(int(self.entry_window) + 1, int(self.exit_window) + 1)
        self.am = ArrayManager(size=size)
        self.entry_up = 0.0
        self.entry_down = 0.0
        self.exit_up = 0.0
        self.exit_down = 0.0

    def on_init(self) -> None:
        self.inited = True

    def on_bar(self, bar: BarData) -> None:
        self.am.update_bar(bar)
        if not self.am.inited:
            return
        previous_high = self.am.high[:-1]
        previous_low = self.am.low[:-1]
        self.entry_up = float(np.max(previous_high[-self.entry_window :]))
        self.entry_down = float(np.min(previous_low[-self.entry_window :]))
        self.exit_up = float(np.max(previous_high[-self.exit_window :]))
        self.exit_down = float(np.min(previous_low[-self.exit_window :]))

        # 取消尚未成交的旧挂单 — 新 bar 信号覆盖旧 pending
        if self.active_orderids:
            self.cancel_all()

        # 突破策略默认用市价单 (next-bar open + slip), 不用限价 — 限价在 gap-up 时漏单
        if self.pos <= 0 and bar.close_price > self.entry_up:
            self.buy_market(self.fixed_size)
        elif self.pos > 0 and bar.close_price < self.exit_down:
            self.sell_market(abs(self.pos))


class TickMomentumStrategy(CtaStrategyTemplate):
    """Live tick momentum CTA strategy for gateway smoke and small live runs."""

    author = "Omega"
    category = CATEGORY_STOCK
    tags = ["tick"]
    trigger_pct = 0.001
    exit_pct = 0.001
    fixed_size = 1.0
    parameters = ["trigger_pct", "exit_pct", "fixed_size"]
    variables = CtaStrategyTemplate.variables + ["reference_price", "last_signal_price"]

    def __init__(self, strategy_engine: "CtaBacktesterEngine", strategy_name: str, vt_symbol: str, setting: dict) -> None:
        super().__init__(strategy_engine, strategy_name, vt_symbol, setting)
        self.reference_price = 0.0
        self.last_signal_price = 0.0

    def on_init(self) -> None:
        self.inited = True

    def on_tick(self, tick) -> None:
        price = float(getattr(tick, "last_price", 0.0) or 0.0)
        if price <= 0:
            return
        if self.reference_price <= 0:
            self.reference_price = price
            return
        if self.active_orderids:
            return
        if self.pos <= 0 and price >= self.reference_price * (1 + float(self.trigger_pct)):
            self.last_signal_price = price
            self.buy(price, float(self.fixed_size))
        elif self.pos > 0 and price <= self.reference_price * (1 - float(self.exit_pct)):
            self.last_signal_price = price
            self.sell(price, abs(self.pos))

    def on_bar(self, bar: BarData) -> None:
        return


@dataclass(frozen=True)
class CtaStrategyMetadata:
    class_name: str
    display_name: str
    author: str
    parameters: list[str]
    variables: list[str]
    description: str
    parameter_defaults: dict[str, Any] | None = None
    category: str = ""
    tags: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:  # noqa: D401
        if self.tags is None:
            object.__setattr__(self, "tags", [])


class TargetPosTemplate(CtaStrategyTemplate):
    """Target-position-based CTA template — set ``self.target_pos`` from ``on_bar``
    and the engine handles the order generation (cancel stale + send delta).

    Mirrors vn.py's ``CtaTemplate.set_target_pos`` pattern. Strategies derived
    from this class shouldn't call ``self.buy/sell`` directly — they set targets.
    """

    author = "Omega"
    parameters: list[str] = ["fixed_size"]
    variables: list[str] = ["inited", "trading", "pos", "target_pos"]

    fixed_size: float = 1.0
    target_pos: float = 0.0

    def set_target_pos(self, target: float) -> None:
        self.target_pos = float(target)
        if not self.trading:
            return
        diff = self.target_pos - float(self.pos)
        if abs(diff) < 1e-9:
            return
        last_bar_price = getattr(self, "_last_price", None)
        if last_bar_price is None or last_bar_price <= 0:
            return
        if diff > 0:
            self.buy(float(last_bar_price), abs(diff))
        else:
            self.sell(float(last_bar_price), abs(diff))

    def on_bar(self, bar: BarData) -> None:
        self._last_price = float(bar.close_price)


class TargetPosMomentumStrategy(TargetPosTemplate):
    """Example: target = +fixed_size if today's close > MA(slow); -fixed_size if below."""

    category = CATEGORY_STOCK
    tags = ["1d"]
    parameters: list[str] = ["slow_window", "fixed_size"]
    variables: list[str] = ["inited", "trading", "pos", "target_pos", "slow_ma"]

    slow_window: int = 20
    slow_ma: float = 0.0

    def on_init(self) -> None:
        from .cta_utility import ArrayManager

        self.am = ArrayManager()
        self.inited = True

    def on_start(self) -> None:
        self.trading = True

    def on_stop(self) -> None:
        self.trading = False

    def on_tick(self, tick) -> None:  # noqa: ANN001
        return

    def on_bar(self, bar: BarData) -> None:
        super().on_bar(bar)
        self.am.update_bar(bar)
        if not self.am.inited:
            return
        self.slow_ma = float(self.am.sma(int(self.slow_window)))
        target = float(self.fixed_size) if bar.close_price > self.slow_ma else -float(self.fixed_size)
        self.set_target_pos(target)


CTA_STRATEGY_REGISTRY: dict[str, type[CtaStrategyTemplate]] = {
    "MovingAverageCrossStrategy": MovingAverageCrossStrategy,
    "DonchianBreakoutStrategy": DonchianBreakoutStrategy,
    "TickMomentumStrategy": TickMomentumStrategy,
    "TargetPosMomentumStrategy": TargetPosMomentumStrategy,
}


def get_cta_strategy_class(class_name: str | None) -> type[CtaStrategyTemplate]:
    normalized = (class_name or "MovingAverageCrossStrategy").strip()
    strategy_class = CTA_STRATEGY_REGISTRY.get(normalized)
    if not strategy_class:
        available = ", ".join(sorted(CTA_STRATEGY_REGISTRY))
        raise ValueError(f"unsupported CTA strategy_class: {class_name}. available: {available}")
    return strategy_class


def list_cta_strategy_metadata() -> list[CtaStrategyMetadata]:
    descriptions = {
        "MovingAverageCrossStrategy": "均线交叉 CTA 示例策略，适合验证 Bar/ArrayManager/撮合链路。",
        "DonchianBreakoutStrategy": "唐奇安通道突破 CTA 策略，使用历史高低点通道生成开平仓信号。",
        "TickMomentumStrategy": "实时 Tick 动量 CTA 策略，用于验证 live market -> CTA -> MainEngine -> TigerOpen 链路。",
        "TargetPosMomentumStrategy": "vnpy TargetPos 模式示例：策略只设 target_pos，引擎计算 delta 自动下单 / 反手。",
    }
    results: list[CtaStrategyMetadata] = []
    for name, strategy_class in sorted(CTA_STRATEGY_REGISTRY.items()):
        defaults: dict[str, Any] = {}
        for pname in strategy_class.parameters:
            value = getattr(strategy_class, pname, None)
            if isinstance(value, (int, float, bool, str)):
                defaults[pname] = value
        results.append(
            CtaStrategyMetadata(
                class_name=name,
                display_name=name.replace("Strategy", ""),
                author=strategy_class.author,
                parameters=list(strategy_class.parameters),
                variables=list(strategy_class.variables),
                description=descriptions.get(name, ""),
                parameter_defaults=defaults,
                category=getattr(strategy_class, "category", "") or "",
                tags=list(getattr(strategy_class, "tags", []) or []),
            )
        )
    return results


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _compare(left: float, operator: str, right: float) -> bool:
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    return False
