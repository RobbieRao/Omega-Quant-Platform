"""Headless algorithmic execution engine inspired by vn.py AlgoTrading.

Omega keeps the mature vn.py idea: execution algos are functional engines that
slice a parent order into child ``OrderRequest`` objects sent through
``MainEngine``. Qt widgets, domestic gateways, and desktop setting persistence
are intentionally excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from math import ceil
from typing import Any

from .engine import BaseEngine, MainEngine
from .event import EVENT_ORDER, EVENT_TIMER, Event, EventEngine
from .objects import CancelRequest, Direction, Exchange, LogData, OrderData, OrderRequest, Product, TickData


EVENT_ALGO = "eAlgo."


class AlgoStatus(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FINISHED = "finished"
    ERROR = "error"


@dataclass(frozen=True)
class AlgoTemplate:
    """Static metadata for an algo type exposed to the React UI/API layer."""

    name: str
    display_name: str
    description: str
    required_fields: list[str]


@dataclass
class TwapAlgoSetting:
    """TWAP parent-order setting.

    ``interval_ticks`` uses EventEngine timer events instead of wall-clock
    sleeps, which makes the engine deterministic in tests and compatible with a
    web backend.
    """

    symbol: str
    direction: Direction
    total_volume: float
    limit_price: float
    gateway_name: str
    exchange: Exchange = Exchange.SMART
    product: Product = Product.EQUITY
    order_type: str = "LMT"
    interval_ticks: int = 1
    slices: int | None = None
    clip_volume: float | None = None
    reference: str = ""

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper()
        if self.total_volume <= 0:
            raise ValueError("total_volume must be positive")
        if self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        if self.interval_ticks < 1:
            raise ValueError("interval_ticks must be >= 1")
        if self.clip_volume is not None and self.clip_volume <= 0:
            raise ValueError("clip_volume must be positive when provided")
        if self.slices is not None and self.slices < 1:
            raise ValueError("slices must be >= 1 when provided")

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange.value}"


@dataclass
class IcebergAlgoSetting:
    """Iceberg parent-order setting.

    The algo exposes only a small slice of the parent quantity at a time. This
    mirrors vn.py's practical algo module style while keeping the Omega backend
    headless and gateway-neutral.
    """

    symbol: str
    direction: Direction
    total_volume: float
    display_volume: float
    limit_price: float
    gateway_name: str
    exchange: Exchange = Exchange.SMART
    product: Product = Product.EQUITY
    order_type: str = "LMT"
    interval_ticks: int = 1
    reference: str = ""

    def __post_init__(self) -> None:
        self.symbol = self.symbol.upper()
        if self.total_volume <= 0:
            raise ValueError("total_volume must be positive")
        if self.display_volume <= 0:
            raise ValueError("display_volume must be positive")
        if self.display_volume > self.total_volume:
            raise ValueError("display_volume must be <= total_volume")
        if self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        if self.interval_ticks < 1:
            raise ValueError("interval_ticks must be >= 1")

    @property
    def vt_symbol(self) -> str:
        return f"{self.symbol}.{self.exchange.value}"


@dataclass
class AlgoState:
    algoid: str
    algo_type: str
    status: AlgoStatus
    setting: TwapAlgoSetting | IcebergAlgoSetting
    sent_volume: float = 0.0
    traded_volume: float = 0.0
    remaining_volume: float = 0.0
    timer_count: int = 0
    child_orderids: list[str] = field(default_factory=list)
    active_orderids: set[str] = field(default_factory=set)
    message: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def snapshot(self) -> dict[str, Any]:
        return {
            "algoid": self.algoid,
            "algo_type": self.algo_type,
            "status": self.status.value,
            "symbol": self.setting.symbol,
            "vt_symbol": self.setting.vt_symbol,
            "direction": self.setting.direction.value,
            "total_volume": self.setting.total_volume,
            "sent_volume": self.sent_volume,
            "traded_volume": self.traded_volume,
            "remaining_volume": self.remaining_volume,
            "limit_price": self.setting.limit_price,
            "display_volume": getattr(self.setting, "display_volume", None),
            "interval_ticks": self.setting.interval_ticks,
            "gateway_name": self.setting.gateway_name,
            "child_orderids": list(self.child_orderids),
            "active_orderids": sorted(self.active_orderids),
            "message": self.message,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class AlgoTradingEngine(BaseEngine):
    """Non-UI algo execution engine compatible with Omega MainEngine."""

    engine_name = "algo_trading"

    templates = {
        "twap": AlgoTemplate(
            name="twap",
            display_name="TWAP",
            description="按时间片把父订单拆成多笔限价子订单。",
            required_fields=["symbol", "direction", "total_volume", "limit_price", "gateway_name"],
        ),
        "iceberg": AlgoTemplate(
            name="iceberg",
            display_name="Iceberg",
            description="按固定显示量逐步提交子订单，避免一次暴露完整父订单数量。",
            required_fields=["symbol", "direction", "total_volume", "display_volume", "limit_price", "gateway_name"],
        )
    }

    def __init__(self, main_engine: MainEngine, event_engine: EventEngine) -> None:
        super().__init__(main_engine, event_engine, self.engine_name)
        self.algos: dict[str, AlgoState] = {}
        self.finished_algos: dict[str, AlgoState] = {}
        self._algo_count = 0
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)
        self.event_engine.register(EVENT_ORDER, self.process_order_event)

    def close(self) -> None:
        self.stop_all()
        self.event_engine.unregister(EVENT_TIMER, self.process_timer_event)
        self.event_engine.unregister(EVENT_ORDER, self.process_order_event)

    def list_templates(self) -> list[AlgoTemplate]:
        return list(self.templates.values())

    def start_twap(self, setting: TwapAlgoSetting) -> AlgoState:
        self._algo_count += 1
        algoid = f"TWAP{self._algo_count:08d}"
        state = AlgoState(
            algoid=algoid,
            algo_type="twap",
            status=AlgoStatus.RUNNING,
            setting=setting,
            remaining_volume=setting.total_volume,
            message="TWAP started",
        )
        self.algos[algoid] = state
        self.write_log(f"TWAP algo started: {algoid} {setting.symbol} {setting.total_volume}")
        self._emit_algo(state)
        return state

    def start_iceberg(self, setting: IcebergAlgoSetting) -> AlgoState:
        self._algo_count += 1
        algoid = f"ICE{self._algo_count:08d}"
        state = AlgoState(
            algoid=algoid,
            algo_type="iceberg",
            status=AlgoStatus.RUNNING,
            setting=setting,
            remaining_volume=setting.total_volume,
            message="Iceberg started",
        )
        self.algos[algoid] = state
        self.write_log(f"Iceberg algo started: {algoid} {setting.symbol} {setting.total_volume}")
        self._emit_algo(state)
        return state

    def pause_algo(self, algoid: str) -> AlgoState:
        state = self._get_active_algo(algoid)
        if state.status == AlgoStatus.RUNNING:
            state.status = AlgoStatus.PAUSED
            state.message = "paused"
            state.updated_at = datetime.now(timezone.utc)
            self._emit_algo(state)
        return state

    def resume_algo(self, algoid: str) -> AlgoState:
        state = self._get_active_algo(algoid)
        if state.status == AlgoStatus.PAUSED:
            state.status = AlgoStatus.RUNNING
            state.message = "running"
            state.updated_at = datetime.now(timezone.utc)
            self._emit_algo(state)
        return state

    def stop_algo(self, algoid: str, *, cancel_active: bool = True) -> AlgoState:
        state = self._get_active_algo(algoid)
        if cancel_active:
            self._cancel_active_orders(state)
        state.status = AlgoStatus.STOPPED
        state.message = "stopped"
        state.updated_at = datetime.now(timezone.utc)
        self.algos.pop(algoid, None)
        self.finished_algos[algoid] = state
        self.write_log(f"Algo stopped: {algoid}")
        self._emit_algo(state)
        return state

    def stop_all(self) -> None:
        for algoid in list(self.algos):
            self.stop_algo(algoid)

    def get_algo(self, algoid: str) -> AlgoState | None:
        return self.algos.get(algoid) or self.finished_algos.get(algoid)

    def get_all_active_algos(self) -> list[AlgoState]:
        return list(self.algos.values())

    def get_all_finished_algos(self) -> list[AlgoState]:
        return list(self.finished_algos.values())

    def process_timer_event(self, event: Event) -> None:
        for state in list(self.algos.values()):
            if state.status != AlgoStatus.RUNNING:
                continue
            state.timer_count += 1
            if state.timer_count % state.setting.interval_ticks:
                state.updated_at = datetime.now(timezone.utc)
                self._emit_algo(state)
                continue
            if state.algo_type == "iceberg":
                self._run_iceberg_step(state)
            else:
                self._run_twap_step(state)

    def process_order_event(self, event: Event) -> None:
        order = event.data
        if not isinstance(order, OrderData):
            return
        for state in list(self.algos.values()):
            if order.vt_orderid not in state.child_orderids:
                continue
            state.traded_volume = max(state.traded_volume, min(state.setting.total_volume, state.traded_volume + max(order.traded, 0.0)))
            if order.is_active():
                state.active_orderids.add(order.vt_orderid)
            else:
                state.active_orderids.discard(order.vt_orderid)
            state.updated_at = datetime.now(timezone.utc)
            self._emit_algo(state)

    def _run_twap_step(self, state: AlgoState) -> None:
        if state.remaining_volume <= 0:
            self._finish_algo(state, "finished")
            return
        tick = self.main_engine.get_tick(state.setting.vt_symbol)
        if tick and not self._price_condition_passed(state, tick):
            state.message = "waiting for limit price"
            state.updated_at = datetime.now(timezone.utc)
            self._emit_algo(state)
            return

        volume = min(state.remaining_volume, self._clip_volume(state))
        self._send_child_order(state, volume)

    def _run_iceberg_step(self, state: AlgoState) -> None:
        if state.remaining_volume <= 0:
            self._finish_algo(state, "finished")
            return
        tick = self.main_engine.get_tick(state.setting.vt_symbol)
        if tick and not self._price_condition_passed(state, tick):
            state.message = "waiting for limit price"
            state.updated_at = datetime.now(timezone.utc)
            self._emit_algo(state)
            return

        display_volume = getattr(state.setting, "display_volume")
        volume = min(state.remaining_volume, display_volume)
        self._send_child_order(state, volume)

    def _send_child_order(self, state: AlgoState, volume: float) -> None:
        req = OrderRequest(
            symbol=state.setting.symbol,
            exchange=state.setting.exchange,
            direction=state.setting.direction,
            volume=volume,
            price=state.setting.limit_price,
            order_type=state.setting.order_type,
            reference=state.setting.reference or f"OmegaAlgo:{state.algoid}",
            product=state.setting.product,
        )
        vt_orderid = self.main_engine.send_order(req, state.setting.gateway_name)
        if not vt_orderid:
            state.status = AlgoStatus.ERROR
            state.message = "gateway rejected child order"
            state.updated_at = datetime.now(timezone.utc)
            self.algos.pop(state.algoid, None)
            self.finished_algos[state.algoid] = state
            self._emit_algo(state)
            return

        state.child_orderids.append(vt_orderid)
        state.active_orderids.add(vt_orderid)
        state.sent_volume += volume
        state.remaining_volume = max(0.0, state.setting.total_volume - state.sent_volume)
        state.message = f"sent child order {vt_orderid}"
        state.updated_at = datetime.now(timezone.utc)
        if state.remaining_volume <= 0:
            self._finish_algo(state, "finished")
        else:
            self._emit_algo(state)

    def _clip_volume(self, state: AlgoState) -> float:
        if state.setting.clip_volume is not None:
            return state.setting.clip_volume
        slices = state.setting.slices or max(1, ceil(state.setting.total_volume))
        return max(state.setting.total_volume / slices, 1.0 if state.setting.product == Product.EQUITY else 0.01)

    def _price_condition_passed(self, state: AlgoState, tick: TickData) -> bool:
        if state.setting.direction == Direction.LONG:
            ask = tick.ask_price_1 if tick.ask_price_1 is not None else tick.last_price
            return ask <= state.setting.limit_price
        bid = tick.bid_price_1 if tick.bid_price_1 is not None else tick.last_price
        return bid >= state.setting.limit_price

    def _cancel_active_orders(self, state: AlgoState) -> None:
        for vt_orderid in list(state.active_orderids):
            orderid = vt_orderid.split(".", 1)[1] if "." in vt_orderid else vt_orderid
            self.main_engine.cancel_order(
                CancelRequest(orderid=orderid, symbol=state.setting.symbol, exchange=state.setting.exchange),
                state.setting.gateway_name,
            )
        state.active_orderids.clear()

    def _finish_algo(self, state: AlgoState, message: str) -> None:
        state.status = AlgoStatus.FINISHED
        state.message = message
        state.updated_at = datetime.now(timezone.utc)
        self.algos.pop(state.algoid, None)
        self.finished_algos[state.algoid] = state
        self.write_log(f"Algo finished: {state.algoid}")
        self._emit_algo(state)

    def _get_active_algo(self, algoid: str) -> AlgoState:
        state = self.algos.get(algoid)
        if not state:
            raise KeyError(f"active algo not found: {algoid}")
        return state

    def _emit_algo(self, state: AlgoState) -> None:
        self.event_engine.put(Event(EVENT_ALGO, state.snapshot()))
        self.event_engine.put(Event(EVENT_ALGO + state.algoid, state.snapshot()))

    def write_log(self, msg: str) -> None:
        self.event_engine.put(Event("eLog", LogData(msg=msg, gateway_name=self.engine_name)))
