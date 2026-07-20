from __future__ import annotations

import time
from pathlib import Path
from datetime import datetime, timezone

import pytest

from omega_quant.broker.safety import LiveGatewayLockedError, register_execution_controller
from omega_quant.core import (
    EVENT_ORDER,
    EVENT_TICK,
    CancelRequest,
    Direction,
    Event,
    EventEngine,
    Exchange,
    MainEngine,
    OrderRequest,
    Product,
    RiskManagerEngine,
    Status,
    SubscribeRequest,
    TickData,
)
from omega_quant.gateways import MassiveMarketDataGateway, TigerOpenGateway
from omega_quant.execution_control import ExecutionControlController
from omega_quant.market_events import new_market_event
from omega_quant.vnpy_core.event.engine import EventEngine as VendoredVnpyEventEngine
from omega_quant.vnpy_core.trader.constant import Currency as VendoredCurrency
from omega_quant.vnpy_core.trader.constant import Exchange as VendoredExchange
from omega_quant.vnpy_core.trader.constant import Product as VendoredProduct
from omega_quant.vnpy_core.trader.gateway import BaseGateway as VendoredBaseGateway
from omega_quant.vnpy_core.trader.engine import MainEngine as VendoredMainEngine
from omega_quant.vnpy_core.trader.engine import BaseEngine as VendoredBaseEngine
from omega_quant.vnpy_core.trader.object import ContractData as VendoredContractData
from omega_quant.vnpy_core.trader.object import OrderData as VendoredOrderData
from omega_quant.vnpy_core.trader.object import TickData as VendoredTickData


def test_vnpy_core_is_vendored_and_pruned_for_omega_us_scope() -> None:
    exchange_names = set(VendoredExchange.__members__)
    product_names = set(VendoredProduct.__members__)
    currency_names = set(VendoredCurrency.__members__)

    assert VendoredVnpyEventEngine.__module__ == "omega_quant.vnpy_core.event.engine"
    assert VendoredBaseGateway.__module__ == "omega_quant.vnpy_core.trader.gateway"
    assert VendoredContractData.__module__ == "omega_quant.vnpy_core.trader.object"

    assert {"SMART", "NASDAQ", "NYSE", "ARCA", "AMEX", "CBOE"} <= exchange_names
    assert {"EQUITY", "OPTION", "ETF"} <= product_names
    assert "USD" in currency_names

    assert {"SSE", "SZSE", "CFFEX", "SHFE", "DCE", "CZCE", "INE", "GFEX"}.isdisjoint(exchange_names)
    assert {"FUTURES", "FOREX", "SPOT", "SWAP", "CFD"}.isdisjoint(product_names)
    assert "CNY" not in currency_names


def test_omega_event_engine_extends_vendored_vnpy_event_engine() -> None:
    engine = EventEngine()
    assert isinstance(engine, VendoredVnpyEventEngine)

    seen: list[str] = []
    engine.register("omega.test", lambda event: seen.append(event.data))
    engine.put(Event("omega.test", "ok"))

    assert engine.process_pending() == 1
    assert seen == ["ok"]


def test_omega_core_objects_and_gateway_extend_vendored_vnpy_core() -> None:
    from omega_quant.core import BaseEngine, BaseGateway, MainEngine, OrderData, TickData

    assert issubclass(BaseGateway, VendoredBaseGateway)
    assert issubclass(BaseEngine, VendoredBaseEngine)
    assert issubclass(MainEngine, VendoredMainEngine)

    tick = TickData("AAPL", Exchange.SMART, datetime.now(timezone.utc), 100.0, raw={"source": "unit"})
    order = OrderData("AAPL", Exchange.SMART, "1", Direction.LONG, 1, 100.0, broker_order_id="BROKER-1")

    assert isinstance(tick, VendoredTickData)
    assert isinstance(order, VendoredOrderData)
    assert tick.raw == {"source": "unit"}
    assert order.broker_order_id == "BROKER-1"


def test_vendored_vnpy_main_engine_is_headless_and_does_not_change_cwd() -> None:
    cwd = Path.cwd()
    engine = VendoredMainEngine()
    try:
        assert Path.cwd() == cwd
        assert sorted(engine.engines) == ["log", "oms"]
        assert engine.apps == {}
    finally:
        engine.close()


class FakeTigerTradeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.cancelled: list[str] = []
        self.assets = [{"account": "TEST-SIM-ACCOUNT-0001", "net_liquidation": 1_000_000, "locked_funds": 12.5}]
        self.positions = [
            {"symbol": "AAPL", "quantity": 3, "average_cost": 140.0, "unrealized_pnl": 9.5},
            {"symbol": "O:NVDA260618C00245000", "quantity": -1, "average_cost": 10.0, "unrealized_pnl": -2.0},
        ]
        self.orders = [
            {"id": "BROKER-OPEN", "symbol": "AAPL", "action": "BUY", "quantity": 2, "limit_price": 141, "filled": 0, "status": "Submitted"},
        ]
    def get_contract(self, symbol, sec_type, **kwargs):
        self.calls.append(("get_contract", (symbol, sec_type), kwargs))
        multiplier = 100 if "OPT" in str(sec_type).upper() else 1
        return type(
            "FakeContract",
            (),
            {"symbol": symbol, "sec_type": str(sec_type), "name": f"{symbol} contract", "multiplier": multiplier, "min_tick": 0.01, "currency": "USD"},
        )()

    def create_order(self, *args, **kwargs):
        self.calls.append(("create_order", args, kwargs))
        return type("FakeOrder", (), {"id": "fake-order"})()

    def preview_order(self, order):
        self.calls.append(("preview_order", (order,), {}))
        return {"warning_text": None}

    def place_order(self, order):
        self.calls.append(("place_order", (order,), {}))
        return "BROKER-123"

    def cancel_order(self, account=None, id=None, order_id=None):
        self.calls.append(("cancel_order", (), {"account": account, "id": id, "order_id": order_id}))
        self.cancelled.append(str(id or order_id))
        return {"status": "cancelled"}

    def get_prime_assets(self):
        self.calls.append(("get_prime_assets", (), {}))
        return self.assets

    def get_positions(self):
        self.calls.append(("get_positions", (), {}))
        return self.positions

    def get_orders(self):
        self.calls.append(("get_orders", (), {}))
        return self.orders


def test_tigeropen_gateway_never_touches_client_before_process_is_armed() -> None:
    fake = FakeTigerTradeClient()
    register_execution_controller(ExecutionControlController())
    gateway = TigerOpenGateway(EventEngine(), TigerOpenGateway.default_name)
    try:
        gateway.connect({"trade_client": fake})
        with pytest.raises(LiveGatewayLockedError):
            gateway.send_order(
                OrderRequest(
                    symbol="AAPL",
                    exchange=Exchange.SMART,
                    direction=Direction.LONG,
                    volume=1,
                    price=140.0,
                    product=Product.EQUITY,
                )
            )
        assert fake.calls == []
    finally:
        gateway.close()


class FakeTigerPushClient:
    def __init__(self) -> None:
        self.callbacks: dict[str, object] = {}
        self.accounts: dict[str, str | None] = {}

    def subscribe_order(self, callback, account=None):
        self.callbacks["order"] = callback
        self.accounts["order"] = account

    def subscribe_position(self, callback, account=None):
        self.callbacks["position"] = callback
        self.accounts["position"] = account

    def subscribe_transaction(self, callback, account=None):
        self.callbacks["transaction"] = callback
        self.accounts["transaction"] = account

    def subscribe_asset(self, callback, account=None):
        self.callbacks["asset"] = callback
        self.accounts["asset"] = account

    def emit(self, kind: str, payload: dict) -> None:
        callback = self.callbacks[kind]
        callback(payload)


def test_event_engine_dispatches_type_specific_and_general_handlers() -> None:
    engine = EventEngine(interval=0.05)
    seen: list[tuple[str, str]] = []

    def typed(event: Event) -> None:
        seen.append(("typed", event.type))

    def general(event: Event) -> None:
        seen.append(("general", event.type))

    engine.register("custom", typed)
    engine.register_general(general)
    engine.start()
    try:
        engine.put(Event("custom", {"x": 1}))
        deadline = time.time() + 2
        while len(seen) < 2 and time.time() < deadline:
            time.sleep(0.01)
    finally:
        engine.stop()

    assert ("typed", "custom") in seen
    assert ("general", "custom") in seen


def test_main_engine_oms_receives_gateway_order_events(armed_execution) -> None:
    fake = FakeTigerTradeClient()
    main_engine = MainEngine()
    try:
        gateway = main_engine.add_gateway(TigerOpenGateway)
        gateway.connect({"trade_client": fake})
        vt_orderid = main_engine.send_order(
            OrderRequest(
                symbol="AAPL",
                exchange=Exchange.SMART,
                direction=Direction.LONG,
                volume=1,
                price=140.0,
                product=Product.EQUITY,
                reference="unit-test",
            ),
            TigerOpenGateway.default_name,
        )

        deadline = time.time() + 2
        while (
            (not main_engine.get_order(vt_orderid) or main_engine.get_order(vt_orderid).status == Status.SUBMITTING)
            and time.time() < deadline
        ):
            time.sleep(0.01)

        order = main_engine.get_order(vt_orderid)
        assert order is not None
        assert order.status == Status.NOTTRADED
        assert order.broker_order_id == "BROKER-123"
        assert [call[0] for call in fake.calls] == ["get_contract", "create_order", "preview_order", "place_order"]
    finally:
        main_engine.close()


def test_tigeropen_gateway_marks_preview_blocked_order_rejected(armed_execution) -> None:
    class PreviewBlockedClient(FakeTigerTradeClient):
        def preview_order(self, order):
            self.calls.append(("preview_order", (order,), {}))
            return {"warning_text": "insufficient buying power"}

    fake = PreviewBlockedClient()
    main_engine = MainEngine()
    try:
        gateway = main_engine.add_gateway(TigerOpenGateway)
        gateway.connect({"trade_client": fake})
        vt_orderid = main_engine.send_order(
            OrderRequest(
                symbol="AAPL",
                exchange=Exchange.SMART,
                direction=Direction.LONG,
                volume=1,
                price=140.0,
                product=Product.EQUITY,
                reference="unit-test-preview-blocked",
            ),
            TigerOpenGateway.default_name,
        )
        main_engine.event_engine.process_pending()

        order = main_engine.get_order(vt_orderid)
        assert order is not None
        assert order.status == Status.REJECTED
        assert order.broker_order_id is None
        assert "insufficient buying power" in order.reference
        assert [call[0] for call in fake.calls] == ["get_contract", "create_order", "preview_order"]
    finally:
        main_engine.close()


def test_tigeropen_gateway_can_cancel_by_local_orderid_after_submission(armed_execution) -> None:
    fake = FakeTigerTradeClient()
    gateway = TigerOpenGateway(EventEngine(), TigerOpenGateway.default_name)
    try:
        gateway.connect({"trade_client": fake})
        vt_orderid = gateway.send_order(
            OrderRequest(
                symbol="AAPL",
                exchange=Exchange.SMART,
                direction=Direction.LONG,
                volume=1,
                price=140.0,
                product=Product.EQUITY,
                reference="unit-test-local-cancel",
            )
        )
        local_orderid = vt_orderid.split(".", 1)[1]

        gateway.cancel_order(CancelRequest(orderid=local_orderid, symbol="AAPL", exchange=Exchange.SMART))

        assert fake.cancelled == ["BROKER-123"]
    finally:
        gateway.close()


class FakeMassiveAdapter:
    def __init__(self) -> None:
        self.listeners = []
        self.subscribed: list[str] = []
        self.connected = False

    def add_event_listener(self, listener):
        self.listeners.append(listener)

    def remove_event_listener(self, listener):
        self.listeners.remove(listener)

    def connect(self, symbols=None):
        self.connected = True
        if symbols:
            self.subscribed.extend(symbols)
        return self.status()

    def disconnect(self):
        self.connected = False
        return self.status()

    def subscribe(self, symbols):
        self.subscribed.extend(symbols)
        return self.status()

    def snapshot(self, symbol):
        return None

    def status(self):
        return {"connected": self.connected, "subscribed_symbols": self.subscribed}

    def emit(self, event):
        for listener in list(self.listeners):
            listener(event)


def test_massive_market_data_gateway_publishes_ticks_to_main_engine() -> None:
    adapter = FakeMassiveAdapter()
    main_engine = MainEngine()
    try:
        gateway = main_engine.add_gateway(MassiveMarketDataGateway)
        gateway.connect({"adapter": adapter})
        main_engine.subscribe(
            SubscribeRequest(symbol="AAPL", exchange=Exchange.SMART, product=Product.EQUITY),
            MassiveMarketDataGateway.default_name,
        )
        adapter.emit(
            new_market_event(
                source="live_readonly",
                symbol="AAPL",
                timestamp="2026-05-25T00:00:00Z",
                price=188.25,
                volume=100,
                bid=188.2,
                ask=188.3,
                data_quality="unit_test",
            )
        )
        main_engine.event_engine.process_pending()

        tick = main_engine.get_tick("AAPL.SMART")
        assert tick is not None
        assert tick.gateway_name == "MASSIVE"
        assert tick.last_price == 188.25
        assert "AAPL" in adapter.subscribed
    finally:
        main_engine.close()


def test_main_engine_risk_manager_rejects_before_gateway_submit() -> None:
    fake = FakeTigerTradeClient()
    main_engine = MainEngine()
    try:
        gateway = main_engine.add_gateway(TigerOpenGateway)
        gateway.connect({"trade_client": fake})
        risk_manager = main_engine.add_engine(RiskManagerEngine)
        risk_manager.update_config(allowed_sessions=(), price_band_pct=0.05)
        main_engine.event_engine.put(
            Event(
                EVENT_TICK,
                TickData(
                    symbol="AAPL",
                    exchange=Exchange.SMART,
                    datetime=datetime.now(timezone.utc),
                    last_price=100.0,
                ),
            )
        )
        main_engine.event_engine.process_pending()

        vt_orderid = main_engine.send_order(
            OrderRequest(
                symbol="AAPL",
                exchange=Exchange.SMART,
                direction=Direction.LONG,
                volume=1,
                price=130.0,
                product=Product.EQUITY,
                reference="risk-test",
            ),
            TigerOpenGateway.default_name,
        )
        main_engine.event_engine.process_pending()

        order = main_engine.get_order(vt_orderid)
        assert order is not None
        assert order.status == Status.REJECTED
        assert "Order price 130.0000" in order.reference
        assert order.risk_result is not None
        assert order.risk_result["approved"] is False
        assert order.risk_result["reference_price"] == 100.0
        assert any(check["name"] == "price_band" and check["passed"] is False for check in order.risk_result["checks"])
        assert fake.calls == []
    finally:
        main_engine.close()


def test_main_engine_risk_manager_allows_gateway_submit_when_checks_pass(armed_execution) -> None:
    fake = FakeTigerTradeClient()
    main_engine = MainEngine()
    try:
        gateway = main_engine.add_gateway(TigerOpenGateway)
        gateway.connect({"trade_client": fake})
        risk_manager = main_engine.add_engine(RiskManagerEngine)
        risk_manager.update_config(allowed_sessions=(), price_band_pct=0.05)
        main_engine.event_engine.put(
            Event(
                EVENT_TICK,
                TickData(
                    symbol="AAPL",
                    exchange=Exchange.SMART,
                    datetime=datetime.now(timezone.utc),
                    last_price=100.0,
                ),
            )
        )
        main_engine.event_engine.process_pending()

        vt_orderid = main_engine.send_order(
            OrderRequest(
                symbol="AAPL",
                exchange=Exchange.SMART,
                direction=Direction.LONG,
                volume=1,
                price=101.0,
                product=Product.EQUITY,
                reference="risk-test",
            ),
            TigerOpenGateway.default_name,
        )

        deadline = time.time() + 2
        while (
            (not main_engine.get_order(vt_orderid) or main_engine.get_order(vt_orderid).status == Status.SUBMITTING)
            and time.time() < deadline
        ):
            time.sleep(0.01)

        order = main_engine.get_order(vt_orderid)
        assert order is not None
        assert order.status == Status.NOTTRADED
        assert [call[0] for call in fake.calls] == ["get_contract", "create_order", "preview_order", "place_order"]
    finally:
        main_engine.close()


def test_main_engine_risk_manager_records_and_blocks_cancel_rate(armed_execution) -> None:
    fake = FakeTigerTradeClient()
    main_engine = MainEngine()
    try:
        gateway = main_engine.add_gateway(TigerOpenGateway)
        gateway.connect({"trade_client": fake})
        risk_manager = main_engine.add_engine(RiskManagerEngine)
        risk_manager.update_config(cancel_limit_per_symbol=1)

        main_engine.cancel_order(
            CancelRequest(orderid="BROKER-1", symbol="AAPL", exchange=Exchange.SMART),
            TigerOpenGateway.default_name,
        )
        main_engine.cancel_order(
            CancelRequest(orderid="BROKER-2", symbol="AAPL", exchange=Exchange.SMART),
            TigerOpenGateway.default_name,
        )
        main_engine.event_engine.process_pending()

        assert [call[0] for call in fake.calls] == ["cancel_order"]
        assert fake.cancelled == ["BROKER-1"]
        status = risk_manager.snapshot()
        assert status["state"]["cancel_counts_by_symbol"]["AAPL"] == 1
    finally:
        main_engine.close()


def test_tigeropen_gateway_queries_account_positions_and_orders_into_oms() -> None:
    fake = FakeTigerTradeClient()
    main_engine = MainEngine()
    try:
        gateway = main_engine.add_gateway(TigerOpenGateway)
        gateway.connect({"trade_client": fake})

        main_engine.query_account(TigerOpenGateway.default_name)
        main_engine.query_position(TigerOpenGateway.default_name)
        main_engine.query_order(TigerOpenGateway.default_name)
        main_engine.event_engine.process_pending()

        accounts = main_engine.get_all_accounts()
        positions = main_engine.get_all_positions()
        order = main_engine.get_order("TIGEROPEN.BROKER-OPEN")
    finally:
        main_engine.close()

    assert accounts[0].accountid == "TEST-SIM-ACCOUNT-0001"
    assert accounts[0].balance == 1_000_000
    assert accounts[0].frozen == 12.5
    assert {position.vt_symbol for position in positions} == {"AAPL.SMART", "O:NVDA260618C00245000.SMART"}
    option_position = next(position for position in positions if position.symbol.startswith("O:"))
    assert option_position.direction == Direction.SHORT
    assert option_position.volume == 1
    assert order is not None
    assert order.status == Status.NOTTRADED
    assert order.broker_order_id == "BROKER-OPEN"
    assert [call[0] for call in fake.calls[-3:]] == ["get_prime_assets", "get_positions", "get_orders"]


def test_tigeropen_gateway_queries_stock_and_option_contracts_into_oms() -> None:
    fake = FakeTigerTradeClient()
    main_engine = MainEngine()
    try:
        gateway = main_engine.add_gateway(TigerOpenGateway)
        gateway.connect({"trade_client": fake})

        main_engine.query_contract(TigerOpenGateway.default_name, "AAPL", product=Product.EQUITY.value)
        main_engine.query_contract(TigerOpenGateway.default_name, "O:NVDA260618C00245000", product=Product.OPTION.value)
        main_engine.event_engine.process_pending()

        contracts = {contract.vt_symbol: contract for contract in main_engine.get_all_contracts()}
    finally:
        main_engine.close()

    assert contracts["AAPL.SMART"].product == Product.EQUITY
    assert contracts["AAPL.SMART"].size == 1
    assert contracts["O:NVDA260618C00245000.SMART"].product == Product.OPTION
    assert contracts["O:NVDA260618C00245000.SMART"].size == 100
    assert fake.calls[-2][0] == "get_contract"
    assert fake.calls[-1][0] == "get_contract"


def test_tigeropen_gateway_push_updates_oms_without_query_polling() -> None:
    fake = FakeTigerTradeClient()
    push = FakeTigerPushClient()
    main_engine = MainEngine()
    try:
        gateway = main_engine.add_gateway(TigerOpenGateway)
        gateway.connect({"trade_client": fake, "push_client": push, "account": "TEST-SIM-ACCOUNT-0001"})

        push.emit("asset", {"account": "TEST-SIM-ACCOUNT-0001", "net_liquidation": 999_001.25, "locked_funds": 3.0})
        push.emit("position", {"symbol": "MU", "quantity": 12, "average_cost": 88.5, "unrealized_pnl": 44.0})
        push.emit(
            "order",
            {
                "id": "BROKER-PUSH-1",
                "symbol": "MU",
                "action": "SELL",
                "quantity": 5,
                "limit_price": 91.2,
                "filled": 2,
                "status": "Part Filled",
            },
        )

        assert gateway.process_push_events() == 3
        main_engine.event_engine.process_pending()

        account = main_engine.get_all_accounts()[0]
        position = next(position for position in main_engine.get_all_positions() if position.symbol == "MU")
        order = main_engine.get_order("TIGEROPEN.BROKER-PUSH-1")
    finally:
        main_engine.close()

    assert push.accounts == {
        "asset": "TEST-SIM-ACCOUNT-0001",
        "order": "TEST-SIM-ACCOUNT-0001",
        "position": "TEST-SIM-ACCOUNT-0001",
        "transaction": "TEST-SIM-ACCOUNT-0001",
    }
    assert account.balance == 999_001.25
    assert account.frozen == 3.0
    assert position.direction == Direction.LONG
    assert position.volume == 12
    assert position.price == 88.5
    assert position.pnl == 44.0
    assert order is not None
    assert order.direction == Direction.SHORT
    assert order.status == Status.PARTTRADED
    assert order.traded == 2
    assert order.broker_order_id == "BROKER-PUSH-1"
    assert fake.calls == []


def test_tigeropen_gateway_timer_drains_push_queue_into_oms() -> None:
    fake = FakeTigerTradeClient()
    push = FakeTigerPushClient()
    main_engine = MainEngine(auto_start=False)
    try:
        gateway = main_engine.add_gateway(TigerOpenGateway)
        gateway.connect({"trade_client": fake, "push_client": push, "account": "TEST-SIM-ACCOUNT-0001"})
        push.emit("order", {"id": "BROKER-TIMER-1", "symbol": "AAPL", "action": "BUY", "quantity": 1, "status": "Submitted"})

        main_engine.event_engine.put(Event("eTimer"))
        main_engine.event_engine.process_pending()
        main_engine.event_engine.process_pending()

        order = main_engine.get_order("TIGEROPEN.BROKER-TIMER-1")
    finally:
        main_engine.close()

    assert order is not None
    assert order.status == Status.NOTTRADED
    assert gateway.order_event_queue.qsize() == 0


def test_tigeropen_gateway_transaction_push_updates_trades_and_risk_state() -> None:
    fake = FakeTigerTradeClient()
    push = FakeTigerPushClient()
    main_engine = MainEngine()
    try:
        gateway = main_engine.add_gateway(TigerOpenGateway)
        gateway.connect({"trade_client": fake, "push_client": push, "account": "TEST-SIM-ACCOUNT-0001"})
        risk_manager = main_engine.add_engine(RiskManagerEngine)

        push.emit(
            "transaction",
            {
                "transaction_id": "TRD-1",
                "order_id": "BROKER-1",
                "symbol": "AAPL",
                "action": "BUY",
                "quantity": 7,
                "price": 123.45,
                "timestamp": 1_771_000_000,
            },
        )

        assert gateway.process_push_events() == 1
        main_engine.event_engine.process_pending()

        trades = main_engine.get_all_trades()
        risk_status = risk_manager.snapshot()
    finally:
        main_engine.close()

    assert len(trades) == 1
    assert trades[0].vt_tradeid == "TIGEROPEN.TRD-1"
    assert trades[0].orderid == "BROKER-1"
    assert trades[0].symbol == "AAPL"
    assert trades[0].direction == Direction.LONG
    assert trades[0].volume == 7
    assert trades[0].price == 123.45
    assert risk_status["state"]["trades_today"] == 1


def test_tigeropen_gateway_rejects_failed_sdk_submit_and_emits_order_event(armed_execution) -> None:
    class RejectingClient(FakeTigerTradeClient):
        def place_order(self, order):
            raise RuntimeError("simulated submit failure")

    fake = RejectingClient()
    event_engine = EventEngine()
    gateway = TigerOpenGateway(event_engine, TigerOpenGateway.default_name)
    seen = []

    def on_order(event: Event) -> None:
        seen.append(event.data)

    event_engine.register(EVENT_ORDER, on_order)
    event_engine.start()
    try:
        gateway.connect({"trade_client": fake})
        vt_orderid = gateway.send_order(
            OrderRequest(
                symbol="AAPL",
                exchange=Exchange.SMART,
                direction=Direction.LONG,
                volume=1,
                price=140.0,
            )
        )
        deadline = time.time() + 2
        while len(seen) < 2 and time.time() < deadline:
            time.sleep(0.01)
    finally:
        event_engine.stop()

    assert vt_orderid
    assert seen[-1].status == Status.REJECTED


def test_core_does_not_import_qt_or_domestic_gateway_modules() -> None:
    import sys

    forbidden = ["vnpy.trader.ui", "vnpy_ctp", "vnpy_xtp", "vnpy_sopt", "vnpy_ctptest"]
    assert not any(name in sys.modules for name in forbidden)
