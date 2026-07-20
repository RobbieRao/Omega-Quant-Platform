"""Headless spread trading engine inspired by vn.py SpreadTrading.

Omega absorbs the useful backend model: a spread is built from US stock/option
legs, produces synthetic spread ticks/positions, and can route a spread order as
leg ``OrderRequest`` objects through ``MainEngine``. Qt widgets, China futures
gateways, exchange-provided spread contracts, and crypto venues are excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .engine import BaseEngine, MainEngine
from .event import EVENT_POSITION, EVENT_TICK, Event, EventEngine
from .objects import Direction, Exchange, LogData, OrderRequest, PositionData, Product, TickData
from .option_master import maybe_parse_us_option_contract


EVENT_SPREAD = "eSpread."


@dataclass(frozen=True)
class SpreadLeg:
    symbol: str
    exchange: Exchange = Exchange.SMART
    product: Product = Product.EQUITY
    price_multiplier: float = 1.0
    trading_multiplier: float = 1.0
    variable_name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.upper())
        if self.price_multiplier == 0:
            raise ValueError("price_multiplier cannot be zero")
        if self.trading_multiplier == 0:
            raise ValueError("trading_multiplier cannot be zero")

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange.value}"


@dataclass
class SpreadData:
    name: str
    active_leg: str
    legs: list[SpreadLeg]
    tick: "SpreadTick | None" = None
    net_position: float = 0.0
    leg_positions: dict[str, float] = field(default_factory=dict)
    child_orderids: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "active_leg": self.active_leg,
            "legs": [
                {
                    "symbol": leg.symbol,
                    "vt_symbol": leg.vt_symbol,
                    "product": leg.product.value,
                    "price_multiplier": leg.price_multiplier,
                    "trading_multiplier": leg.trading_multiplier,
                    "variable_name": leg.variable_name,
                }
                for leg in self.legs
            ],
            "tick": self.tick.snapshot() if self.tick else None,
            "net_position": self.net_position,
            "leg_positions": dict(self.leg_positions),
            "child_orderids": list(self.child_orderids),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class SpreadTick:
    name: str
    datetime: datetime
    bid_price: float | None
    ask_price: float | None
    last_price: float | None
    leg_prices: dict[str, dict[str, float | None]]

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "datetime": self.datetime.isoformat(),
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "last_price": self.last_price,
            "leg_prices": self.leg_prices,
        }


class SpreadTradingEngine(BaseEngine):
    """Synthetic spread engine for US stocks/options."""

    engine_name = "spread_trading"

    def __init__(self, main_engine: MainEngine, event_engine: EventEngine) -> None:
        super().__init__(main_engine, event_engine, self.engine_name)
        self.spreads: dict[str, SpreadData] = {}
        self.symbol_to_spreads: dict[str, set[str]] = {}
        self.ticks: dict[str, TickData] = {}
        self.event_engine.register(EVENT_TICK, self.process_tick_event)
        self.event_engine.register(EVENT_POSITION, self.process_position_event)

    def close(self) -> None:
        self.event_engine.unregister(EVENT_TICK, self.process_tick_event)
        self.event_engine.unregister(EVENT_POSITION, self.process_position_event)

    def create_spread(self, name: str, active_leg: str, legs: list[SpreadLeg]) -> SpreadData:
        spread_name = name.strip().upper()
        if not spread_name:
            raise ValueError("spread name is required")
        if spread_name in self.spreads:
            raise ValueError(f"spread already exists: {spread_name}")
        if len(legs) < 2:
            raise ValueError("spread requires at least two legs")
        active = active_leg.upper()
        if active not in {leg.symbol for leg in legs} and active not in {leg.vt_symbol for leg in legs}:
            raise ValueError("active_leg must match one spread leg")
        spread = SpreadData(name=spread_name, active_leg=active, legs=legs)
        self.spreads[spread_name] = spread
        for leg in legs:
            self.symbol_to_spreads.setdefault(leg.symbol, set()).add(spread_name)
            self.symbol_to_spreads.setdefault(leg.vt_symbol, set()).add(spread_name)
            spread.leg_positions.setdefault(leg.symbol, 0.0)
        self._recalculate_spread(spread)
        self.write_log(f"Spread created: {spread_name}")
        return spread

    def remove_spread(self, name: str) -> SpreadData:
        spread = self._spread(name)
        self.spreads.pop(spread.name, None)
        for leg in spread.legs:
            for key in (leg.symbol, leg.vt_symbol):
                spreads = self.symbol_to_spreads.get(key)
                if spreads:
                    spreads.discard(spread.name)
                    if not spreads:
                        self.symbol_to_spreads.pop(key, None)
        self.write_log(f"Spread removed: {spread.name}")
        return spread

    def get_spread(self, name: str) -> SpreadData | None:
        return self.spreads.get(name.strip().upper())

    def get_spread_tick(self, name: str) -> SpreadTick | None:
        spread = self.get_spread(name)
        return spread.tick if spread else None

    def get_spread_position(self, name: str) -> float | None:
        spread = self.get_spread(name)
        return spread.net_position if spread else None

    def send_spread_order(
        self,
        spread_name: str,
        *,
        direction: Direction,
        volume: float,
        gateway_name: str,
        price_offset: float = 0.0,
        reference: str = "",
    ) -> list[str]:
        """Route a spread order into leg orders.

        ``Direction.LONG`` buys positive trading-multiplier legs and sells
        negative legs. ``Direction.SHORT`` reverses the signs.
        """

        if volume <= 0:
            raise ValueError("spread order volume must be positive")
        spread = self._spread(spread_name)
        orderids: list[str] = []
        for leg in spread.legs:
            leg_signed_volume = leg.trading_multiplier * volume
            if direction == Direction.SHORT:
                leg_signed_volume *= -1
            leg_direction = Direction.LONG if leg_signed_volume > 0 else Direction.SHORT
            leg_volume = abs(leg_signed_volume)
            leg_price = self._leg_order_price(leg, leg_direction, price_offset)
            vt_orderid = self.main_engine.send_order(
                OrderRequest(
                    symbol=leg.symbol,
                    exchange=leg.exchange,
                    direction=leg_direction,
                    volume=leg_volume,
                    price=leg_price,
                    reference=reference or f"OmegaSpread:{spread.name}",
                    product=leg.product,
                ),
                gateway_name,
            )
            if vt_orderid:
                orderids.append(vt_orderid)
        spread.child_orderids.extend(orderids)
        spread.updated_at = datetime.now(timezone.utc)
        self._emit_spread(spread)
        return orderids

    def process_tick_event(self, event: Event) -> None:
        tick = event.data
        if not isinstance(tick, TickData):
            return
        self.ticks[tick.symbol.upper()] = tick
        self.ticks[tick.vt_symbol] = tick
        for spread_name in self.symbol_to_spreads.get(tick.symbol.upper(), set()) | self.symbol_to_spreads.get(tick.vt_symbol, set()):
            self._recalculate_spread(self.spreads[spread_name])

    def process_position_event(self, event: Event) -> None:
        position = event.data
        if not isinstance(position, PositionData):
            return
        symbol = position.symbol.upper()
        signed = position.volume if position.direction == Direction.LONG else -position.volume
        for spread_name in self.symbol_to_spreads.get(symbol, set()) | self.symbol_to_spreads.get(position.vt_symbol, set()):
            spread = self.spreads[spread_name]
            spread.leg_positions[symbol] = signed
            spread.net_position = self._calculate_net_position(spread)
            spread.updated_at = datetime.now(timezone.utc)
            self._emit_spread(spread)

    def _recalculate_spread(self, spread: SpreadData) -> None:
        bid_price = 0.0
        ask_price = 0.0
        last_price = 0.0
        leg_prices: dict[str, dict[str, float | None]] = {}
        complete = True
        for leg in spread.legs:
            tick = self.ticks.get(leg.symbol) or self.ticks.get(leg.vt_symbol)
            if tick is None:
                complete = False
                leg_prices[leg.symbol] = {"bid": None, "ask": None, "last": None}
                continue
            bid = tick.bid_price_1 if tick.bid_price_1 is not None else tick.last_price
            ask = tick.ask_price_1 if tick.ask_price_1 is not None else tick.last_price
            last = tick.last_price
            leg_prices[leg.symbol] = {"bid": bid, "ask": ask, "last": last}
            if leg.price_multiplier >= 0:
                bid_price += leg.price_multiplier * bid
                ask_price += leg.price_multiplier * ask
            else:
                bid_price += leg.price_multiplier * ask
                ask_price += leg.price_multiplier * bid
            last_price += leg.price_multiplier * last
        now = datetime.now(timezone.utc)
        spread.tick = SpreadTick(
            name=spread.name,
            datetime=now,
            bid_price=round(bid_price, 6) if complete else None,
            ask_price=round(ask_price, 6) if complete else None,
            last_price=round(last_price, 6) if complete else None,
            leg_prices=leg_prices,
        )
        spread.net_position = self._calculate_net_position(spread)
        spread.updated_at = now
        self._emit_spread(spread)

    def _calculate_net_position(self, spread: SpreadData) -> float:
        ratios = []
        for leg in spread.legs:
            pos = spread.leg_positions.get(leg.symbol)
            if pos is None:
                continue
            ratios.append(pos / leg.trading_multiplier)
        if not ratios:
            return 0.0
        positive = [value for value in ratios if value >= 0]
        negative = [value for value in ratios if value < 0]
        if positive and not negative:
            return round(min(positive), 6)
        if negative and not positive:
            return round(max(negative), 6)
        return 0.0

    def _leg_order_price(self, leg: SpreadLeg, direction: Direction, price_offset: float) -> float | None:
        tick = self.ticks.get(leg.symbol) or self.ticks.get(leg.vt_symbol)
        if not tick:
            return None
        if direction == Direction.LONG:
            base = tick.ask_price_1 if tick.ask_price_1 is not None else tick.last_price
            return round(base + price_offset, 6)
        base = tick.bid_price_1 if tick.bid_price_1 is not None else tick.last_price
        return round(max(0.0, base - price_offset), 6)

    def _spread(self, name: str) -> SpreadData:
        spread = self.get_spread(name)
        if spread is None:
            raise KeyError(f"spread not found: {name}")
        return spread

    def _emit_spread(self, spread: SpreadData) -> None:
        payload = spread.snapshot()
        self.event_engine.put(Event(EVENT_SPREAD, payload))
        self.event_engine.put(Event(EVENT_SPREAD + spread.name, payload))

    def write_log(self, msg: str) -> None:
        self.event_engine.put(Event("eLog", LogData(msg=msg, gateway_name=self.engine_name)))


def make_stock_leg(symbol: str, *, price_multiplier: float, trading_multiplier: float, variable_name: str = "") -> SpreadLeg:
    return SpreadLeg(
        symbol=symbol,
        product=Product.EQUITY,
        price_multiplier=price_multiplier,
        trading_multiplier=trading_multiplier,
        variable_name=variable_name or symbol.upper(),
    )


def make_option_leg(symbol: str, *, price_multiplier: float, trading_multiplier: float, variable_name: str = "") -> SpreadLeg:
    contract = maybe_parse_us_option_contract(symbol)
    if contract is None:
        raise ValueError(f"unsupported US option leg symbol: {symbol}")
    return SpreadLeg(
        symbol=contract.canonical_symbol,
        product=Product.OPTION,
        price_multiplier=price_multiplier,
        trading_multiplier=trading_multiplier,
        variable_name=variable_name or contract.canonical_symbol,
    )
