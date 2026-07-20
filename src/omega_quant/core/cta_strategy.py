"""vn.py-style live CTA strategy engine for Omega.

This engine is intentionally headless: no Qt, no domestic gateways, and no
separate execution path. Strategy orders route through MainEngine so RiskManager
and the configured TigerOpen gateway remain the single order path.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .cta_utility import BarGenerator
from .engine import BaseEngine, MainEngine
from .event import EVENT_ORDER, EVENT_TICK, EVENT_TRADE, Event, EventEngine
from .objects import BarData, CancelRequest, Direction, Exchange, OrderData, OrderRequest, OrderType, Product, TickData, TradeData
from .strategy_template import CtaStrategyTemplate, get_cta_strategy_class, list_cta_strategy_metadata


EVENT_CTA_STRATEGY = "eCtaStrategy."


_TAG_TO_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 0}


def _bar_window_minutes(setting: dict[str, Any]) -> int:
    """Return an explicit live aggregation window; strategy tags are metadata only."""

    raw = setting.get("bar_window_minutes", 0)
    try:
        minutes = int(raw or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("bar_window_minutes must be an integer") from exc
    if minutes < 0:
        raise ValueError("bar_window_minutes must be >= 0")
    return minutes


@dataclass
class CtaLiveStrategyConfig:
    strategy_name: str
    class_name: str
    vt_symbol: str
    setting: dict[str, Any] = field(default_factory=dict)
    gateway_name: str = "TIGEROPEN"
    product: Product = Product.EQUITY
    exchange: Exchange = Exchange.SMART


class CtaStrategyEngine(BaseEngine):
    """Live CTA manager adapted from vn.py's CtaStrategy engine shape."""

    engine_name = "cta_strategy"

    def __init__(self, main_engine: MainEngine, event_engine: EventEngine) -> None:
        super().__init__(main_engine, event_engine, self.engine_name)
        self._lock = threading.RLock()
        self.strategies: dict[str, CtaStrategyTemplate] = {}
        self.configs: dict[str, CtaLiveStrategyConfig] = {}
        self.orderid_strategy_map: dict[str, str] = {}
        self.bar_generators: dict[str, BarGenerator] = {}
        self.last_bars: dict[str, BarData] = {}
        self.bar_counts: dict[str, int] = {}
        self.logs: list[str] = []
        self._processed_tick_events: list[Event] = []
        self.register_event()

    def register_event(self) -> None:
        self.event_engine.register(EVENT_TICK, self.process_tick_event)
        self.event_engine.register(EVENT_ORDER, self.process_order_event)
        self.event_engine.register(EVENT_TRADE, self.process_trade_event)

    def close(self) -> None:
        self.event_engine.unregister(EVENT_TICK, self.process_tick_event)
        self.event_engine.unregister(EVENT_ORDER, self.process_order_event)
        self.event_engine.unregister(EVENT_TRADE, self.process_trade_event)

    def list_templates(self) -> list[dict[str, Any]]:
        return [metadata.__dict__ for metadata in list_cta_strategy_metadata()]

    def add_strategy(
        self,
        *,
        strategy_name: str,
        class_name: str,
        vt_symbol: str,
        setting: dict[str, Any] | None = None,
        gateway_name: str = "TIGEROPEN",
        product: Product = Product.EQUITY,
        exchange: Exchange = Exchange.SMART,
    ) -> dict[str, Any]:
        normalized_name = strategy_name.strip()
        if not normalized_name:
            raise ValueError("strategy_name 不能为空")
        normalized_vt_symbol = vt_symbol.strip().upper()
        if "." not in normalized_vt_symbol:
            normalized_vt_symbol = f"{normalized_vt_symbol}.{exchange.value}"
        with self._lock:
            if normalized_name in self.strategies:
                raise ValueError(f"CTA strategy already exists: {normalized_name}")
            strategy_class = get_cta_strategy_class(class_name)
            config = CtaLiveStrategyConfig(
                strategy_name=normalized_name,
                class_name=strategy_class.__name__,
                vt_symbol=normalized_vt_symbol,
                setting=dict(setting or {}),
                gateway_name=gateway_name,
                product=product,
                exchange=exchange,
            )
            strategy = strategy_class(self, normalized_name, normalized_vt_symbol, config.setting)
            self.configs[normalized_name] = config
            self.strategies[normalized_name] = strategy
            window = _bar_window_minutes(config.setting)
            forward_to_strategy = lambda bar, name=normalized_name: self.process_strategy_bar(name, bar)
            if window > 0:
                noop = lambda _bar: None
                self.bar_generators[normalized_name] = BarGenerator(
                    noop, window=window, on_window_bar=forward_to_strategy
                )
            else:
                self.bar_generators[normalized_name] = BarGenerator(forward_to_strategy)
            self.bar_counts[normalized_name] = 0
            window_label = f"{window}m" if window > 0 else "1m"
            self.write_log(
                f"added CTA strategy {normalized_name} {config.class_name} {normalized_vt_symbol} (bar window={window_label})",
                strategy,
            )
            return self.strategy_snapshot(normalized_name)

    def init_strategy(self, strategy_name: str) -> dict[str, Any]:
        strategy = self._get_strategy(strategy_name)
        if not strategy.inited:
            strategy.on_init()
            self._warmup_strategy(strategy)
        return self.strategy_snapshot(strategy.strategy_name)

    def _warmup_strategy(self, strategy: CtaStrategyTemplate, *, max_bars: int = 300) -> None:
        """Pre-feed historical bars from RecordedBar so ArrayManager is hot at init time."""
        self.write_log("warmup: starting", strategy)
        tags = getattr(strategy.__class__, "tags", None) or []
        interval = next((t for t in tags if t in _TAG_TO_MINUTES), None)
        self.write_log(f"warmup: tags={tags} resolved interval={interval}", strategy)
        if not interval or _TAG_TO_MINUTES[interval] == 0:
            self.write_log("warmup: skipped (no usable interval)", strategy)
            return
        try:
            data_recorder = self.main_engine.get_engine("data_recorder")
        except Exception as exc:
            self.write_log(f"warmup: get_engine failed: {exc!r}", strategy)
            return
        if data_recorder is None or not hasattr(data_recorder, "load_bars"):
            self.write_log(f"warmup: data_recorder unavailable (={data_recorder})", strategy)
            return
        try:
            # load_bars 默认 ASC + limit → 拿到最早的 N 条；我们要最近的 N 条用于 warm-up。
            # 通过 SQLAlchemy session 直接查 DESC limit 后再 reverse。
            from sqlalchemy import select
            from ..models import RecordedBar

            session = data_recorder.session_factory()
            try:
                stmt = (
                    select(RecordedBar)
                    .where(
                        RecordedBar.vt_symbol == strategy.vt_symbol.upper(),
                        RecordedBar.interval == interval,
                    )
                    .order_by(RecordedBar.timestamp.desc())
                    .limit(max_bars)
                )
                rows = list(session.scalars(stmt))
            finally:
                session.close()
            rows.reverse()  # chronological order for feeding through on_bar
        except Exception as exc:
            self.write_log(f"warmup load_bars failed: {exc!r}", strategy)
            return
        if not rows:
            self.write_log(
                f"warmup: no historical {interval} bars for {strategy.vt_symbol} in RecordedBar -- strategy starts cold",
                strategy,
            )
            return
        symbol = strategy.vt_symbol.split(".")[0].upper()
        try:
            exchange = Exchange(strategy.vt_symbol.split(".")[1])
        except (IndexError, ValueError):
            exchange = Exchange.SMART
        fed = 0
        for row in rows:
            bar = BarData(
                symbol=symbol,
                exchange=exchange,
                datetime=row.timestamp,
                interval=interval,
                open_price=float(row.open_price),
                high_price=float(row.high_price),
                low_price=float(row.low_price),
                close_price=float(row.close_price),
                volume=float(row.volume),
                gateway_name="WARMUP",
            )
            try:
                strategy.on_bar(bar)
                fed += 1
            except Exception as exc:
                self.write_log(f"warmup on_bar #{fed} failed: {exc!r}", strategy)
                break
        self.write_log(
            f"warmup fed {fed} historical {interval} bars to {strategy.vt_symbol}; ArrayManager ready",
            strategy,
        )

    def start_strategy(self, strategy_name: str) -> dict[str, Any]:
        strategy = self._get_strategy(strategy_name)
        if not strategy.inited:
            strategy.on_init()
            self._warmup_strategy(strategy)
        strategy.on_start()
        self.write_log(f"started CTA strategy {strategy.strategy_name}", strategy)
        return self.strategy_snapshot(strategy.strategy_name)

    def stop_strategy(self, strategy_name: str) -> dict[str, Any]:
        strategy = self._get_strategy(strategy_name)
        strategy.cancel_all()
        strategy.on_stop()
        self.write_log(f"stopped CTA strategy {strategy.strategy_name}", strategy)
        return self.strategy_snapshot(strategy.strategy_name)

    def edit_strategy(self, strategy_name: str, setting: dict[str, Any]) -> dict[str, Any]:
        """Update parameters on an existing strategy. Stops it first if running, then restarts."""
        strategy = self._get_strategy(strategy_name)
        was_trading = strategy.trading
        if was_trading:
            strategy.cancel_all()
            strategy.on_stop()
        for key, value in setting.items():
            if key in strategy.parameters and hasattr(strategy, key):
                setattr(strategy, key, value)
        with self._lock:
            config = self.configs.get(strategy.strategy_name)
            if config is not None:
                merged = dict(config.setting or {})
                merged.update(setting)
                config.setting = merged
        self.write_log(f"edited CTA strategy {strategy.strategy_name} setting={setting}", strategy)
        if was_trading:
            strategy.on_start()
            self.write_log(f"restarted CTA strategy {strategy.strategy_name}", strategy)
        return self.strategy_snapshot(strategy.strategy_name)

    def remove_strategy(self, strategy_name: str) -> dict[str, Any]:
        strategy = self._get_strategy(strategy_name)
        if strategy.trading:
            raise ValueError("运行中的 CTA strategy 不能删除，请先 stop")
        with self._lock:
            self.strategies.pop(strategy.strategy_name, None)
            self.configs.pop(strategy.strategy_name, None)
            self.bar_generators.pop(strategy.strategy_name, None)
            self.last_bars.pop(strategy.strategy_name, None)
            self.bar_counts.pop(strategy.strategy_name, None)
            self.orderid_strategy_map = {
                orderid: name for orderid, name in self.orderid_strategy_map.items() if name != strategy.strategy_name
            }
        return {"removed": True, "strategy_name": strategy.strategy_name}

    def flush_strategy_bars(self, strategy_name: str | None = None) -> list[dict[str, Any]]:
        """Flush unfinished 1-minute bars, useful for tests and manual runs."""

        if strategy_name:
            names = [self._get_strategy(strategy_name).strategy_name]
        else:
            with self._lock:
                names = sorted(self.strategies)
        flushed: list[dict[str, Any]] = []
        for name in names:
            generator = self.bar_generators.get(name)
            if not generator:
                continue
            bar = generator.generate()
            if bar:
                flushed.append(_bar_payload(bar))
        return flushed

    def send_order(
        self,
        strategy: CtaStrategyTemplate,
        vt_symbol: str,
        direction: Direction,
        price: float,
        volume: float,
        order_type: OrderType = OrderType.LIMIT,
        stop_price: float | None = None,
    ) -> list[str]:
        config = self.configs[strategy.strategy_name]
        symbol, exchange = _split_vt_symbol(vt_symbol, default_exchange=config.exchange)
        req = OrderRequest(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            order_type=order_type,
            volume=float(volume),
            price=float(price),
            product=config.product,
            reference=strategy.strategy_name,
        )
        # STOP / STOP_LIMIT: 把触发价附到 reference 里, 让网关 (TigerOpen) 可以读取
        if order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and stop_price is not None:
            req.reference = f"{strategy.strategy_name}|stop={stop_price}"
        vt_orderid = self.main_engine.send_order(req, config.gateway_name)
        if vt_orderid:
            with self._lock:
                self.orderid_strategy_map[vt_orderid] = strategy.strategy_name
        return [vt_orderid] if vt_orderid else []

    def cancel_order(self, strategy: CtaStrategyTemplate, vt_orderid: str) -> None:
        config = self.configs[strategy.strategy_name]
        order = self.main_engine.get_order(vt_orderid)
        orderid = vt_orderid.split(".", 1)[1] if "." in vt_orderid else vt_orderid
        symbol = order.symbol if order else _split_vt_symbol(strategy.vt_symbol, default_exchange=config.exchange)[0]
        exchange = order.exchange if order else config.exchange
        self.main_engine.cancel_order(CancelRequest(orderid=orderid, symbol=symbol, exchange=exchange), config.gateway_name)

    def write_log(self, msg: str, strategy: CtaStrategyTemplate | None = None) -> None:
        prefix = datetime.now(timezone.utc).isoformat()
        name = f"[{strategy.strategy_name}] " if strategy else ""
        row = f"{prefix} {name}{msg}"
        self.logs.append(row)
        self.main_engine.write_log(row, source=self.engine_name)

    def process_tick_event(self, event: Event) -> None:
        tick = event.data
        if not isinstance(tick, TickData):
            return
        # ``OmegaCoreRuntime._process_tick`` publishes a tick to EventEngine and
        # also calls this handler synchronously for read-after-write API calls.
        # Deduplicate only that exact Event object. Do not deduplicate by
        # symbol/time/price: live feeds and manual strategy runs can validly
        # receive repeated ticks with the same values, especially when replaying
        # a cached market snapshot.
        with self._lock:
            if any(processed is event for processed in self._processed_tick_events):
                return
            self._processed_tick_events.append(event)
            if len(self._processed_tick_events) > 5_000:
                self._processed_tick_events = self._processed_tick_events[-2_500:]
            strategies = list(self.strategies.values())
        for strategy in strategies:
            if strategy.vt_symbol.upper() != tick.vt_symbol.upper() or not strategy.trading:
                continue
            strategy.on_tick(tick)
            generator = self.bar_generators.get(strategy.strategy_name)
            if generator:
                generator.update_tick(tick)
            self._put_strategy_event(strategy)

    def process_strategy_bar(self, strategy_name: str, bar: BarData) -> None:
        strategy = self.strategies.get(strategy_name)
        if not strategy or not strategy.trading:
            return
        self.last_bars[strategy_name] = bar
        self.bar_counts[strategy_name] = self.bar_counts.get(strategy_name, 0) + 1
        strategy.on_bar(bar)
        self._put_strategy_event(strategy)

    def process_order_event(self, event: Event) -> None:
        order = event.data
        if not isinstance(order, OrderData):
            return
        strategy = self._strategy_from_order(order)
        if not strategy:
            return
        self.orderid_strategy_map[order.vt_orderid] = strategy.strategy_name
        strategy.update_order(order)
        self._put_strategy_event(strategy)

    def process_trade_event(self, event: Event) -> None:
        trade = event.data
        if not isinstance(trade, TradeData):
            return
        strategy_name = self.orderid_strategy_map.get(f"{trade.gateway_name}.{trade.orderid}")
        if not strategy_name:
            return
        strategy = self.strategies.get(strategy_name)
        if not strategy:
            return
        strategy.update_trade(trade)
        self._put_strategy_event(strategy)

    def strategies_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            names = sorted(self.strategies)
        return [self.strategy_snapshot(name) for name in names]

    def strategy_snapshot(self, strategy_name: str) -> dict[str, Any]:
        strategy = self._get_strategy(strategy_name)
        config = self.configs[strategy.strategy_name]
        return {
            "strategy_name": strategy.strategy_name,
            "class_name": config.class_name,
            "vt_symbol": strategy.vt_symbol,
            "gateway_name": config.gateway_name,
            "product": config.product.value,
            "inited": strategy.inited,
            "trading": strategy.trading,
            "pos": strategy.pos,
            "active_orderids": sorted(strategy.active_orderids),
            "bar_count": self.bar_counts.get(strategy.strategy_name, 0),
            "last_bar": _bar_payload(self.last_bars[strategy.strategy_name]) if strategy.strategy_name in self.last_bars else None,
            "parameters": {key: getattr(strategy, key, None) for key in getattr(strategy, "parameters", [])},
            "variables": {key: getattr(strategy, key, None) for key in getattr(strategy, "variables", [])},
            "logs": self.logs[-50:],
        }

    def _get_strategy(self, strategy_name: str) -> CtaStrategyTemplate:
        strategy = self.strategies.get(strategy_name.strip())
        if not strategy:
            raise ValueError(f"CTA strategy not found: {strategy_name}")
        return strategy

    def _strategy_from_order(self, order: OrderData) -> CtaStrategyTemplate | None:
        strategy_name = self.orderid_strategy_map.get(order.vt_orderid)
        if not strategy_name and order.reference:
            strategy_name = order.reference.split("|", 1)[0].strip()
        if not strategy_name:
            return None
        return self.strategies.get(strategy_name)

    def _put_strategy_event(self, strategy: CtaStrategyTemplate) -> None:
        self.event_engine.put(Event(EVENT_CTA_STRATEGY + strategy.strategy_name, self.strategy_snapshot(strategy.strategy_name)))


def _split_vt_symbol(vt_symbol: str, *, default_exchange: Exchange = Exchange.SMART) -> tuple[str, Exchange]:
    normalized = vt_symbol.strip().upper()
    if "." not in normalized:
        return normalized, default_exchange
    symbol, exchange_text = normalized.rsplit(".", 1)
    return symbol, Exchange(exchange_text)


def _bar_payload(bar: BarData) -> dict[str, Any]:
    return {
        "vt_symbol": bar.vt_symbol,
        "interval": bar.interval,
        "datetime": bar.datetime.isoformat(),
        "open_price": bar.open_price,
        "high_price": bar.high_price,
        "low_price": bar.low_price,
        "close_price": bar.close_price,
        "volume": bar.volume,
        "gateway_name": bar.gateway_name,
    }
