"""Headless vn.py-style RiskManager engine for Omega.

The engine keeps vn.py's practical idea: stateful pre-trade checks based on
recent order flow, active orders, trades, cancels, prices, sessions, and
exposure. Omega scopes it to US stocks/options and exposes it through FastAPI
instead of vn.py's Qt widget.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from ..vendor.vnpy_risk_manager import VnpyRiskConfig, VnpyRiskState, evaluate_vnpy_style_pretrade
from .engine import BaseEngine, MainEngine
from .event import EVENT_ORDER, EVENT_POSITION, EVENT_TRADE, Event, EventEngine
from .objects import Direction, OrderData, PositionData, TradeData


class RiskManagerEngine(BaseEngine):
    engine_name = "risk_manager"

    def __init__(self, main_engine: MainEngine, event_engine: EventEngine) -> None:
        super().__init__(main_engine, event_engine, self.engine_name)
        self.config = VnpyRiskConfig()
        self.order_timestamps: list[datetime] = []
        self.trades_today = 0
        self.cancel_counts_by_symbol: dict[str, int] = {}
        self.symbol_exposure: dict[str, float] = {}
        self.strategy_exposure: dict[str, float] = {}
        self.account_exposure = 0.0
        self._seen_orderids: set[str] = set()
        self._order_references: dict[str, str] = {}
        self.event_engine.register(EVENT_ORDER, self.process_order_event)
        self.event_engine.register(EVENT_TRADE, self.process_trade_event)
        self.event_engine.register(EVENT_POSITION, self.process_position_event)

    def close(self) -> None:
        self.event_engine.unregister(EVENT_ORDER, self.process_order_event)
        self.event_engine.unregister(EVENT_TRADE, self.process_trade_event)
        self.event_engine.unregister(EVENT_POSITION, self.process_position_event)

    def update_config(self, **patch: Any) -> dict[str, Any]:
        allowed = set(VnpyRiskConfig.__dataclass_fields__)
        clean = {key: value for key, value in patch.items() if key in allowed and value is not None}
        self.config = VnpyRiskConfig(**{**asdict(self.config), **clean})
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "engine": self.engine_name,
            "config": _config_payload(self.config),
            "state": {
                "order_flow_count": len(self.order_timestamps),
                "active_order_count": len(self.main_engine.get_all_active_orders()),
                "trades_today": self.trades_today,
                "cancel_counts_by_symbol": dict(sorted(self.cancel_counts_by_symbol.items())),
                "symbol_exposure": dict(sorted(self.symbol_exposure.items())),
                "strategy_exposure": dict(sorted(self.strategy_exposure.items())),
                "account_exposure": round(self.account_exposure, 6),
            },
        }

    def evaluate_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = symbol.upper()
        timestamp = _as_utc(now or datetime.now(timezone.utc))
        direction = Direction.LONG if side.lower() in {"buy", "long"} else Direction.SHORT
        reference_price = self._reference_price(normalized_symbol)
        result = evaluate_vnpy_style_pretrade(
            symbol=normalized_symbol,
            side=side,
            quantity=quantity,
            price=price,
            config=self.config,
            state=VnpyRiskState(
                now=timestamp,
                order_timestamps=tuple(self.order_timestamps),
                active_order_count=len(self.main_engine.get_all_active_orders()),
                trades_today=self.trades_today,
                cancel_counts_by_symbol=self.cancel_counts_by_symbol,
                reference_price=reference_price,
                symbol_exposure=self.symbol_exposure.get(normalized_symbol, 0.0),
                strategy_exposure=self.strategy_exposure.get(strategy_id or "", 0.0),
                account_exposure=self.account_exposure,
            ),
        )
        return {
            "approved": result.approved,
            "reason": result.reason,
            "checks": [asdict(check) for check in result.checks],
            "reference_price": reference_price,
            "symbol": normalized_symbol,
            "side": direction.value,
            "quantity": quantity,
            "price": price,
            "strategy_id": strategy_id,
        }

    def record_cancel(self, symbol: str) -> dict[str, Any]:
        normalized = symbol.upper()
        self.cancel_counts_by_symbol[normalized] = self.cancel_counts_by_symbol.get(normalized, 0) + 1
        return self.snapshot()

    def evaluate_cancel(self, symbol: str) -> dict[str, Any]:
        normalized = symbol.upper()
        count = int(self.cancel_counts_by_symbol.get(normalized, 0))
        approved = not self.config.active or count < self.config.cancel_limit_per_symbol
        reason = (
            "cancel checks passed."
            if approved
            else f"{normalized} cancels today {count}/{self.config.cancel_limit_per_symbol}; cancel request blocked."
        )
        return {
            "approved": approved,
            "reason": reason,
            "checks": [
                {
                    "name": "cancel_limit_per_symbol",
                    "passed": approved,
                    "message": f"{normalized} cancels today {count}/{self.config.cancel_limit_per_symbol}.",
                }
            ],
            "symbol": normalized,
            "cancel_count": count,
            "cancel_limit_per_symbol": self.config.cancel_limit_per_symbol,
        }

    def process_order_event(self, event: Event) -> None:
        order = event.data
        if not isinstance(order, OrderData):
            return
        if order.reference:
            self._order_references[order.orderid] = order.reference
            self._order_references[order.vt_orderid] = order.reference
            if order.broker_order_id:
                self._order_references[order.broker_order_id] = order.reference
        if order.vt_orderid in self._seen_orderids:
            return
        self._seen_orderids.add(order.vt_orderid)
        self.order_timestamps.append(_as_utc(order.datetime or datetime.now(timezone.utc)))
        if len(self.order_timestamps) > max(1_000, self.config.order_flow_limit * 20):
            self.order_timestamps = self.order_timestamps[-1_000:]
            self._seen_orderids = set(list(self._seen_orderids)[-1_000:])

    def process_trade_event(self, event: Event) -> None:
        trade = event.data
        if not isinstance(trade, TradeData):
            return
        self.trades_today += 1
        notional = _signed_notional(trade.direction, trade.volume, trade.price)
        self.symbol_exposure[trade.symbol.upper()] = self.symbol_exposure.get(trade.symbol.upper(), 0.0) + notional
        strategy_id = self._strategy_id_for_trade(trade)
        if strategy_id:
            self.strategy_exposure[strategy_id] = self.strategy_exposure.get(strategy_id, 0.0) + notional
        self.account_exposure = sum(abs(value) for value in self.symbol_exposure.values())

    def process_position_event(self, event: Event) -> None:
        position = event.data
        if not isinstance(position, PositionData):
            return
        notional = _signed_notional(position.direction, position.volume, position.price)
        self.symbol_exposure[position.symbol.upper()] = notional
        self.account_exposure = sum(abs(value) for value in self.symbol_exposure.values())

    def _reference_price(self, symbol: str) -> float | None:
        for tick in self.main_engine.get_all_ticks():
            if tick.symbol.upper() == symbol or tick.vt_symbol.upper() == symbol:
                return float(tick.last_price)
        return None

    def _strategy_id_for_trade(self, trade: TradeData) -> str | None:
        reference = self._order_references.get(trade.orderid)
        if reference:
            return reference
        for order in self.main_engine.get_all_orders():
            if trade.orderid in {order.orderid, order.vt_orderid, order.broker_order_id}:
                return order.reference or None
        return None


def _config_payload(config: VnpyRiskConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload["allowed_sessions"] = [
        {"start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes")}
        for start, end in config.allowed_sessions
    ]
    return payload


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _signed_notional(direction: Direction, volume: float, price: float) -> float:
    notional = abs(float(volume) * float(price))
    if direction == Direction.SHORT:
        return -notional
    return notional
