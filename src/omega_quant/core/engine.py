"""MainEngine/OmsEngine adapted from vn.py for Omega."""

from __future__ import annotations

from abc import ABC
from datetime import datetime, timezone
from typing import Any, TypeVar

from omega_quant.vnpy_core.trader.engine import BaseEngine as VnpyBaseEngine
from omega_quant.vnpy_core.trader.engine import MainEngine as VnpyMainEngine

from .event import (
    EVENT_ACCOUNT,
    EVENT_CONTRACT,
    EVENT_LOG,
    EVENT_ORDER,
    EVENT_POSITION,
    EVENT_TICK,
    EVENT_TRADE,
    Event,
    EventEngine,
)
from .gateway import BaseGateway
from .objects import (
    AccountData,
    CancelRequest,
    ContractData,
    LogData,
    OrderData,
    OrderRequest,
    PositionData,
    SubscribeRequest,
    Direction,
    Status,
    TickData,
    TradeData,
)


EngineType = TypeVar("EngineType", bound="BaseEngine")


class BaseEngine(VnpyBaseEngine, ABC):
    engine_name = ""

    def __init__(self, main_engine: "MainEngine", event_engine: EventEngine, engine_name: str) -> None:
        self.main_engine = main_engine
        self.event_engine = event_engine
        self.engine_name = engine_name

    def close(self) -> None:
        return


class LogEngine(BaseEngine):
    def __init__(self, main_engine: "MainEngine", event_engine: EventEngine) -> None:
        super().__init__(main_engine, event_engine, "log")
        self.logs: list[LogData] = []
        event_engine.register(EVENT_LOG, self.process_log_event)

    def process_log_event(self, event: Event) -> None:
        if isinstance(event.data, LogData):
            self.logs.append(event.data)


class OmsEngine(BaseEngine):
    """Order-management cache, following vn.py's event-driven OMS concept."""

    def __init__(self, main_engine: "MainEngine", event_engine: EventEngine) -> None:
        super().__init__(main_engine, event_engine, "oms")
        self.ticks: dict[str, TickData] = {}
        self.orders: dict[str, OrderData] = {}
        self.trades: dict[str, TradeData] = {}
        self.positions: dict[str, PositionData] = {}
        self.accounts: dict[str, AccountData] = {}
        self.contracts: dict[str, ContractData] = {}
        self.active_orders: dict[str, OrderData] = {}
        self.register_event()

    def register_event(self) -> None:
        self.event_engine.register(EVENT_TICK, self.process_tick_event)
        self.event_engine.register(EVENT_ORDER, self.process_order_event)
        self.event_engine.register(EVENT_TRADE, self.process_trade_event)
        self.event_engine.register(EVENT_POSITION, self.process_position_event)
        self.event_engine.register(EVENT_ACCOUNT, self.process_account_event)
        self.event_engine.register(EVENT_CONTRACT, self.process_contract_event)

    def process_tick_event(self, event: Event) -> None:
        tick = event.data
        if isinstance(tick, TickData):
            self.ticks[tick.vt_symbol] = tick

    def process_order_event(self, event: Event) -> None:
        order = event.data
        if not isinstance(order, OrderData):
            return
        self.orders[order.vt_orderid] = order
        if order.is_active():
            self.active_orders[order.vt_orderid] = order
        else:
            self.active_orders.pop(order.vt_orderid, None)

    def process_trade_event(self, event: Event) -> None:
        trade = event.data
        if isinstance(trade, TradeData):
            self.trades[trade.vt_tradeid] = trade

    def process_position_event(self, event: Event) -> None:
        position = event.data
        if isinstance(position, PositionData):
            self.positions[position.vt_positionid] = position

    def process_account_event(self, event: Event) -> None:
        account = event.data
        if isinstance(account, AccountData):
            self.accounts[account.vt_accountid] = account

    def process_contract_event(self, event: Event) -> None:
        contract = event.data
        if isinstance(contract, ContractData):
            self.contracts[contract.vt_symbol] = contract

    def get_tick(self, vt_symbol: str) -> TickData | None:
        return self.ticks.get(vt_symbol)

    def get_order(self, vt_orderid: str) -> OrderData | None:
        return self.orders.get(vt_orderid)

    def get_all_orders(self) -> list[OrderData]:
        return list(self.orders.values())

    def get_all_trades(self) -> list[TradeData]:
        return list(self.trades.values())

    def get_all_active_orders(self) -> list[OrderData]:
        return list(self.active_orders.values())

    def get_all_ticks(self) -> list[TickData]:
        return list(self.ticks.values())

    def get_all_positions(self) -> list[PositionData]:
        return list(self.positions.values())

    def get_all_accounts(self) -> list[AccountData]:
        return list(self.accounts.values())

    def get_contract(self, vt_symbol: str) -> ContractData | None:
        return self.contracts.get(vt_symbol)

    def get_all_contracts(self) -> list[ContractData]:
        return list(self.contracts.values())


class MainEngine(VnpyMainEngine):
    """Headless main engine: gateways + functional engines + event bus."""

    def __init__(self, event_engine: EventEngine | None = None, *, auto_start: bool = True) -> None:
        self.event_engine = event_engine or EventEngine()
        if auto_start:
            self.event_engine.start()
        self.gateways: dict[str, BaseGateway] = {}
        self.engines: dict[str, BaseEngine] = {}
        self._risk_reject_count = 0
        self.init_engines()

    def init_engines(self) -> None:
        self.add_engine(LogEngine)
        oms = self.add_engine(OmsEngine)
        self.get_tick = oms.get_tick
        self.get_all_ticks = oms.get_all_ticks
        self.get_order = oms.get_order
        self.get_all_orders = oms.get_all_orders
        self.get_all_trades = oms.get_all_trades
        self.get_all_active_orders = oms.get_all_active_orders
        self.get_all_positions = oms.get_all_positions
        self.get_all_accounts = oms.get_all_accounts
        self.get_contract = oms.get_contract
        self.get_all_contracts = oms.get_all_contracts

    def add_engine(self, engine_class: type[EngineType], **kwargs: Any) -> EngineType:
        engine = engine_class(self, self.event_engine, **kwargs)  # type: ignore[call-arg]
        self.engines[engine.engine_name] = engine
        return engine

    def add_gateway(self, gateway_class: type[BaseGateway], gateway_name: str = "") -> BaseGateway:
        name = gateway_name or gateway_class.default_name
        gateway = gateway_class(self.event_engine, name)
        self.gateways[name] = gateway
        return gateway

    def get_gateway(self, gateway_name: str) -> BaseGateway | None:
        return self.gateways.get(gateway_name)

    def get_engine(self, engine_name: str) -> BaseEngine | None:
        return self.engines.get(engine_name)

    def write_log(self, msg: str, source: str = "MainEngine") -> None:
        self.event_engine.put(Event(EVENT_LOG, LogData(msg=msg, gateway_name=source)))

    def connect(self, setting: dict, gateway_name: str) -> None:
        gateway = self.get_gateway(gateway_name)
        if gateway:
            self.write_log(f"connect -> {gateway_name}")
            gateway.connect(setting)

    def subscribe(self, req: SubscribeRequest, gateway_name: str) -> None:
        gateway = self.get_gateway(gateway_name)
        if gateway:
            self.write_log(f"subscribe -> {gateway_name}: {req}")
            gateway.subscribe(req)

    def send_order(self, req: OrderRequest, gateway_name: str) -> str:
        gateway = self.get_gateway(gateway_name)
        if not gateway:
            return ""
        risk_rejection = self._evaluate_pre_gateway_risk(req, gateway_name)
        if risk_rejection:
            self.event_engine.put(Event(EVENT_ORDER, risk_rejection))
            self.write_log(
                f"risk_manager rejected order -> {gateway_name}: {risk_rejection.symbol} "
                f"{risk_rejection.direction.value} {risk_rejection.volume:g} @ {risk_rejection.price}",
                source="RiskManager",
            )
            return risk_rejection.vt_orderid
        self.write_log(f"send_order -> {gateway_name}: {req}")
        return gateway.send_order(req)

    def cancel_order(self, req: CancelRequest, gateway_name: str) -> bool:
        gateway = self.get_gateway(gateway_name)
        if not gateway:
            return False
        risk_engine = self.get_engine("risk_manager")
        if risk_engine and hasattr(risk_engine, "evaluate_cancel") and req.symbol:
            result = risk_engine.evaluate_cancel(req.symbol)
            if not result.get("approved"):
                self.write_log(
                    f"risk_manager blocked cancel -> {gateway_name}: {req.symbol} {req.orderid}: {result.get('reason')}",
                    source="RiskManager",
                )
                return False
        self.write_log(f"cancel_order -> {gateway_name}: {req}")
        gateway.cancel_order(req)
        if risk_engine and hasattr(risk_engine, "record_cancel") and req.symbol:
            risk_engine.record_cancel(req.symbol)
        return True

    def query_account(self, gateway_name: str) -> None:
        gateway = self.get_gateway(gateway_name)
        if gateway:
            self.write_log(f"query_account -> {gateway_name}")
            gateway.query_account()

    def query_position(self, gateway_name: str) -> None:
        gateway = self.get_gateway(gateway_name)
        if gateway:
            self.write_log(f"query_position -> {gateway_name}")
            gateway.query_position()

    def query_order(self, gateway_name: str) -> None:
        gateway = self.get_gateway(gateway_name)
        if gateway and hasattr(gateway, "query_order"):
            self.write_log(f"query_order -> {gateway_name}")
            gateway.query_order()

    def query_contract(self, gateway_name: str, symbol: str, product: str | None = None) -> None:
        gateway = self.get_gateway(gateway_name)
        if gateway and hasattr(gateway, "query_contract"):
            self.write_log(f"query_contract -> {gateway_name}: {symbol}")
            gateway.query_contract(symbol, product=product)

    def close(self) -> None:
        self.event_engine.stop()
        for engine in self.engines.values():
            engine.close()
        for gateway in self.gateways.values():
            gateway.close()

    def _evaluate_pre_gateway_risk(self, req: OrderRequest, gateway_name: str) -> OrderData | None:
        risk_engine = self.get_engine("risk_manager")
        if not risk_engine or not hasattr(risk_engine, "evaluate_order"):
            return None

        price = req.price
        if price is None or price <= 0:
            tick = self.get_tick(f"{req.symbol}.{req.exchange.value}")
            price = tick.last_price if tick else None
        if price is None or price <= 0:
            return self._risk_rejected_order(
                req,
                gateway_name,
                reason="order price is missing and no reference tick is available",
                risk_result={
                    "approved": False,
                    "reason": "order price is missing and no reference tick is available",
                    "checks": [],
                    "reference_price": None,
                    "symbol": req.symbol.upper(),
                    "side": "buy" if req.direction == Direction.LONG else "sell",
                    "quantity": float(req.volume),
                    "price": None,
                    "strategy_id": req.reference or None,
                },
            )

        side = "buy" if req.direction == Direction.LONG else "sell"
        result = risk_engine.evaluate_order(
            symbol=req.symbol,
            side=side,
            quantity=float(req.volume),
            price=float(price),
            strategy_id=req.reference or None,
        )
        if result.get("approved"):
            return None
        return self._risk_rejected_order(
            req,
            gateway_name,
            reason=str(result.get("reason") or "risk rejected"),
            risk_result=result,
        )

    def _risk_rejected_order(
        self,
        req: OrderRequest,
        gateway_name: str,
        *,
        reason: str,
        risk_result: dict[str, Any],
    ) -> OrderData:
        self._risk_reject_count += 1
        return OrderData(
            symbol=req.symbol,
            exchange=req.exchange,
            orderid=f"RISK-REJECT-{self._risk_reject_count}",
            direction=req.direction,
            volume=req.volume,
            price=req.price,
            traded=0.0,
            status=Status.REJECTED,
            datetime=datetime.now(timezone.utc),
            gateway_name=gateway_name,
            reference=f"{req.reference} | {reason}".strip(" |"),
            risk_result=risk_result,
        )
