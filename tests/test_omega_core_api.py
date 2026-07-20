from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

from omega_quant.api import create_app
from omega_quant.execution_control import LIVE_CONFIRM_VALUE
from omega_quant.core import Direction, Event, Exchange, OrderRequest, Product
from omega_quant.core.objects import TradeData
from omega_quant.gateways import TigerOpenGateway
from omega_quant.monitor_groups import MonitorGroupService
from omega_quant.schemas import (
    CtaBacktestOptimizeRequest,
    CtaBacktestRunRequest,
    CoreTickUpdate,
    DataRecordingRequest,
    BarCsvImportRequest,
    AlgoIcebergStartRequest,
    AlgoTwapStartRequest,
    MonitorGroupCreate,
    MonitorGroupCtaStrategyRequest,
    OptionContractRequest,
    OptionPortfolioConfigureRequest,
    OptionReadonlySyncRequest,
    OptionPositionUpdateRequest,
    OptionQuoteUpdateRequest,
    OptionUnderlyingPriceRequest,
    RecordedBarCreate,
    SpreadCreateRequest,
    SpreadLegRequest,
    SpreadOrderRequest,
    TickBarAggregationRequest,
    RiskManagerCancelRecordRequest,
    RiskManagerConfigUpdateRequest,
    RiskManagerEvaluateRequest,
)


def _endpoint(app, path: str, method: str = "GET"):
    method = method.upper()
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def test_core_runtime_status_exposes_vnpy_style_engines(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        payload = _endpoint(app, "/api/core/status")()
    finally:
        app.state.omega_core_runtime.close()

    assert "TIGEROPEN" in payload["gateway_names"]
    assert "MASSIVE" in payload["gateway_names"]
    assert payload["gateway_status"]["tigeropen"]["capabilities"] == ["account", "contract", "market_data", "trading"]
    assert payload["gateway_status"]["massive"]["capabilities"] == ["market_data"]
    assert "algo_trading" in payload["engine_names"]
    assert "option_master" in payload["engine_names"]
    assert "spread_trading" in payload["engine_names"]
    assert "data_recorder" in payload["engine_names"]
    assert "data_manager" in payload["engine_names"]
    assert "cta_backtester" in payload["engine_names"]
    assert "risk_manager" in payload["engine_names"]
    assert payload["risk_manager"]["engine"] == "risk_manager"
    assert "no Qt" in payload["scope"]
    assert "no domestic" in payload["scope"]
    assert "no crypto" in payload["scope"]


def test_monitor_group_cta_strategy_registers_and_links_to_group(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    session = app.state.omega_session_factory()
    try:
        svc = MonitorGroupService(session)
        svc.seed_default_monitor_group()
        group = svc.create_monitor_group(
            MonitorGroupCreate(
                name="CTA group",
                allocated_capital=10_000,
            )
        )
        registered = _endpoint(app, "/api/monitor-groups/{group_id}/cta-strategy", "POST")(
            group.id,
            MonitorGroupCtaStrategyRequest(class_name="TickMomentumStrategy", symbol="AAPL", start=False),
            svc,
        )
        strategy_name = registered["strategy_name"]
        assert registered["class_name"] == "TickMomentumStrategy"
        assert registered["vt_symbol"] == "AAPL.SMART"
        assert registered["gateway_name"] == "TIGEROPEN"
        assert registered["monitor_group_id"] == group.id
        assert registered["trading"] is False
        cta_list = {item["strategy_name"]: item for item in _endpoint(app, "/api/cta-strategy/strategies")()}
        assert strategy_name in cta_list
        assert cta_list[strategy_name]["class_name"] == "TickMomentumStrategy"
    finally:
        session.close()
        app.state.omega_core_runtime.close()


def test_gateways_api_exposes_main_engine_gateway_capabilities(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        payload = _endpoint(app, "/api/gateways")()
    finally:
        app.state.omega_core_runtime.close()

    by_name = {item["gateway_name"]: item for item in payload}
    assert set(by_name) == {"MASSIVE", "TIGEROPEN"}
    assert by_name["MASSIVE"]["capabilities"] == ["market_data"]
    assert by_name["TIGEROPEN"]["capabilities"] == ["account", "contract", "market_data", "trading"]


def test_core_runtime_connects_tiger_push_when_enabled(tmp_path, monkeypatch) -> None:
    class RuntimePushClient:
        def __init__(self) -> None:
            self.subscribed: list[tuple[str, str | None]] = []

        def subscribe_order(self, callback, account=None):
            self.subscribed.append(("order", account))

        def subscribe_position(self, callback, account=None):
            self.subscribed.append(("position", account))

        def subscribe_transaction(self, callback, account=None):
            self.subscribed.append(("transaction", account))

        def subscribe_asset(self, callback, account=None):
            self.subscribed.append(("asset", account))

        def disconnect(self):
            return None

    push = RuntimePushClient()
    monkeypatch.setenv("OMEGA_TIGER_PUSH_ENABLED", "1")
    monkeypatch.setenv("TIGEROPEN_ACCOUNT", "TEST-SIM-ACCOUNT-0001")
    monkeypatch.setattr(
        "omega_quant.gateways.tigeropen_gateway.configured_tiger_account",
        lambda: "TEST-SIM-ACCOUNT-0001",
    )
    monkeypatch.setattr("omega_quant.gateways.tigeropen_gateway.build_trade_client", lambda: object())
    monkeypatch.setattr("omega_quant.gateways.tigeropen_gateway.build_push_client", lambda connect=True: push)

    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}", enable_tiger_push=True)
    try:
        payload = _endpoint(app, "/api/core/status")()
    finally:
        app.state.omega_core_runtime.close()

    assert payload["gateway_status"]["tigeropen"]["connected"] is True
    assert payload["gateway_status"]["tigeropen"]["push_connected"] is True
    assert payload["gateway_status"]["tigeropen"]["push_error"] is None
    assert set(push.subscribed) == {
        ("asset", "TEST-SIM-ACCOUNT-0001"),
        ("order", "TEST-SIM-ACCOUNT-0001"),
        ("position", "TEST-SIM-ACCOUNT-0001"),
        ("transaction", "TEST-SIM-ACCOUNT-0001"),
    }


def test_core_orders_api_exposes_structured_risk_rejection(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        runtime = app.state.omega_core_runtime
        runtime.risk_manager.update_config(allowed_sessions=(), price_band_pct=0.05)
        _endpoint(app, "/api/core/ticks", "POST")(CoreTickUpdate(symbol="AAPL", last_price=100))
        vt_orderid = runtime.main_engine.send_order(
            OrderRequest(
                symbol="AAPL",
                exchange=Exchange.SMART,
                direction=Direction.LONG,
                volume=1,
                price=130,
                product=Product.EQUITY,
                reference="core-api-risk-test",
            ),
            TigerOpenGateway.default_name,
        )
        orders = _endpoint(app, "/api/core/orders")()
    finally:
        app.state.omega_core_runtime.close()

    risk_order = next(order for order in orders if order["vt_orderid"] == vt_orderid)
    assert risk_order["status"] == "rejected"
    assert risk_order["risk_result"]["approved"] is False
    assert risk_order["risk_result"]["reference_price"] == 100.0
    assert any(check["name"] == "price_band" and check["passed"] is False for check in risk_order["risk_result"]["checks"])


def test_core_order_cancel_endpoint_routes_through_main_engine_and_risk_manager(
    tmp_path, monkeypatch, armed_execution
) -> None:
    class FakeTigerTradeClient:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        def get_contract(self, symbol, sec_type, **kwargs):
            return type(
                "FakeContract",
                (),
                {
                    "symbol": symbol,
                    "sec_type": str(sec_type),
                    "name": f"{symbol} contract",
                    "multiplier": 1,
                    "min_tick": 0.01,
                    "currency": "USD",
                },
            )()

        def create_order(self, *args, **kwargs):
            return type("FakeOrder", (), {"id": "fake-order"})()

        def preview_order(self, order):
            return {"warning_text": None}

        def place_order(self, order):
            return "BROKER-CANCEL-ME"

        def cancel_order(self, account=None, id=None, order_id=None):
            self.cancelled.append(str(id or order_id))
            return {"status": "cancelled"}

    fake = FakeTigerTradeClient()
    monkeypatch.setattr("omega_quant.gateways.tigeropen_gateway.build_trade_client", lambda: fake)
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        app.state.omega_execution_control.arm(account_suffix="0001", confirmation=LIVE_CONFIRM_VALUE)
        runtime = app.state.omega_core_runtime
        runtime.risk_manager.update_config(allowed_sessions=(), cancel_limit_per_symbol=1)
        vt_orderid = runtime.main_engine.send_order(
            OrderRequest(
                symbol="AAPL",
                exchange=Exchange.SMART,
                direction=Direction.LONG,
                volume=1,
                price=100,
                product=Product.EQUITY,
                reference="core-cancel-test",
            ),
            TigerOpenGateway.default_name,
        )
        result = _endpoint(app, "/api/core/orders/{orderid:path}/cancel", "POST")(vt_orderid)
        blocked = _endpoint(app, "/api/core/orders/{orderid:path}/cancel", "POST")(vt_orderid)
    finally:
        app.state.omega_core_runtime.close()

    assert result["cancel_requested"] is True
    assert result["cancel_request_id"] == "BROKER-CANCEL-ME"
    assert fake.cancelled == ["BROKER-CANCEL-ME"]
    assert result["risk_manager"]["state"]["cancel_counts_by_symbol"]["AAPL"] == 1
    assert blocked["cancel_requested"] is False
    assert fake.cancelled == ["BROKER-CANCEL-ME"]


def test_core_tigeropen_query_api_populates_oms_from_gateway_snapshot(tmp_path, monkeypatch) -> None:
    class SnapshotClient:
        def get_contract(self, symbol, sec_type, **kwargs):
            multiplier = 100 if "OPT" in str(sec_type).upper() else 1
            return type(
                "FakeContract",
                (),
                {
                    "symbol": symbol,
                    "sec_type": str(sec_type),
                    "name": f"{symbol} contract",
                    "multiplier": multiplier,
                    "min_tick": 0.01,
                    "currency": "USD",
                },
            )()

        def get_prime_assets(self):
            return [{"account": "TEST-SIM-ACCOUNT-0001", "net_liquidation": 1_000_000.0, "locked_funds": 10.0}]

        def get_positions(self):
            return [{"symbol": "AAPL", "quantity": 5, "average_cost": 120.0, "unrealized_pnl": 15.0}]

        def get_orders(self):
            return [{"id": "BROKER-1", "symbol": "AAPL", "action": "BUY", "quantity": 1, "limit_price": 121.0, "status": "Submitted"}]

    monkeypatch.setattr("omega_quant.gateways.tigeropen_gateway.build_trade_client", lambda: SnapshotClient())
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        snapshot = _endpoint(app, "/api/core/tigeropen/query", "POST")()
        stock_contract = _endpoint(app, "/api/core/contracts/query", "POST")("AAPL", "equity")
        option_contract = _endpoint(app, "/api/core/contracts/query", "POST")("O:NVDA260618C00245000", "option")
        accounts = _endpoint(app, "/api/core/accounts")()
        positions = _endpoint(app, "/api/core/positions")()
        contracts = _endpoint(app, "/api/core/contracts")()
    finally:
        app.state.omega_core_runtime.close()

    assert snapshot["gateway_name"] == "TIGEROPEN"
    assert accounts[0]["accountid"] == "TEST-SIM-ACCOUNT-0001"
    assert accounts[0]["available"] == 999_990.0
    assert positions[0]["vt_symbol"] == "AAPL.SMART"
    assert positions[0]["volume"] == 5
    assert snapshot["orders"][0]["broker_order_id"] == "BROKER-1"
    assert stock_contract["contract"]["vt_symbol"] == "AAPL.SMART"
    assert option_contract["contract"]["vt_symbol"] == "O:NVDA260618C00245000.SMART"
    assert {contract["product"] for contract in contracts} == {"equity", "option"}


def test_tiger_account_api_prefers_current_tigeropen_gateway_snapshot(tmp_path, monkeypatch) -> None:
    class SnapshotClient:
        def get_prime_assets(self):
            return [{"account": "TEST-SIM-ACCOUNT-0001", "net_liquidation": 1_000_000.0, "locked_funds": 10.0}]

        def get_positions(self):
            return [{"symbol": "AAPL", "quantity": 5, "average_cost": 120.0, "unrealized_pnl": 15.0}]

        def get_orders(self):
            return [{"id": "BROKER-1", "symbol": "AAPL", "action": "BUY", "quantity": 1, "limit_price": 121.0, "status": "Submitted"}]

    monkeypatch.setattr("omega_quant.gateways.tigeropen_gateway.build_trade_client", lambda: SnapshotClient())
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        session = app.state.omega_session_factory()
        try:
            dashboard = _endpoint(app, "/api/dashboard/summary")(MonitorGroupService(session))
        finally:
            session.close()
        tiger_snapshot = _endpoint(app, "/api/tiger/account/snapshot")()
        summary = _endpoint(app, "/api/tiger/account/summary")()
        positions = _endpoint(app, "/api/tiger/account/positions")()
        groups = _endpoint(app, "/api/tiger/account/position-groups")()
        orders = _endpoint(app, "/api/tiger/account/orders")()
    finally:
        app.state.omega_core_runtime.close()

    assert summary["source"] == "omega_tigeropen_gateway"
    assert summary["account"] == "TEST-SIM-ACCOUNT-0001"
    assert summary["available"] is True
    assert summary["positions_count"] == 1
    assert summary["orders_count"] == 1
    assert positions[0]["source"] == "omega_tigeropen_gateway"
    assert positions[0]["symbol"] == "AAPL"
    assert groups[0]["underlying"] == "AAPL"
    assert groups[0]["readonly_badge"] == "readonly_tigeropen_simulated_group"
    assert orders[0]["broker_order_id"] == "BROKER-1"
    assert orders[0]["readonly_badge"] == "readonly_tigeropen_broker_order"
    assert tiger_snapshot["account_kind"] == "tigeropen_configured_account"
    assert tiger_snapshot["summary"]["account"] == "TEST-SIM-ACCOUNT-0001"
    assert tiger_snapshot["risk"]["source"] == "omega_tigeropen_gateway"
    assert dashboard["source"] == "main_engine"
    assert dashboard["account"] == "TEST-SIM-ACCOUNT-0001"
    assert dashboard["total_equity"] == 1_000_000.0
    assert dashboard["oms_orders_count"] == 1
    assert dashboard["oms_positions_count"] == 1
    assert dashboard["core_orders"][0]["broker_order_id"] == "BROKER-1"


def test_tiger_account_orders_do_not_include_local_oms_orders(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OMEGA_REAL_READONLY_SNAPSHOT_PATH", str(tmp_path / "missing_snapshot.json"))
    monkeypatch.setenv("OMEGA_REAL_READONLY_STREAM_PATH", str(tmp_path / "missing_stream.json"))
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        runtime = app.state.omega_core_runtime
        runtime.risk_manager.update_config(allowed_sessions=(), price_band_pct=0.05)
        _endpoint(app, "/api/core/ticks", "POST")(CoreTickUpdate(symbol="AAPL", last_price=100))
        local_vt_orderid = runtime.main_engine.send_order(
            OrderRequest(
                symbol="AAPL",
                exchange=Exchange.SMART,
                direction=Direction.LONG,
                volume=1,
                price=130,
                product=Product.EQUITY,
                reference="local-risk-rejected-order",
            ),
            TigerOpenGateway.default_name,
        )
        core_orders = _endpoint(app, "/api/core/orders")()
        tiger_orders = _endpoint(app, "/api/tiger/account/orders")()
    finally:
        app.state.omega_core_runtime.close()

    assert any(order["status"] == "rejected" and order["broker_order_id"] is None for order in core_orders)
    assert all(order["broker_order_id"] for order in tiger_orders)
    assert all(order["broker_order_id"] != local_vt_orderid for order in tiger_orders)


def test_core_trades_api_exposes_oms_trade_events(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        runtime = app.state.omega_core_runtime
        runtime.main_engine.event_engine.put(
            Event(
                "eTrade.",
                TradeData(
                    symbol="AAPL",
                    exchange=Exchange.SMART,
                    tradeid="TRD-API-1",
                    orderid="BROKER-1",
                    direction=Direction.LONG,
                    volume=3,
                    price=140.5,
                    datetime=datetime.now(timezone.utc),
                    gateway_name=TigerOpenGateway.default_name,
                ),
            )
        )
        trades = _endpoint(app, "/api/core/trades")()
    finally:
        app.state.omega_core_runtime.close()

    assert trades[0]["vt_tradeid"] == "TIGEROPEN.TRD-API-1"
    assert trades[0]["orderid"] == "BROKER-1"
    assert trades[0]["symbol"] == "AAPL"
    assert trades[0]["volume"] == 3


def test_live_market_events_flow_into_core_runtime_and_data_recorder(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        runtime = app.state.omega_core_runtime
        adapter = app.state.omega_market_data_controller.live_adapter
        runtime.data_recorder.add_tick_recording("AAPL.SMART")
        adapter._record_event(
            "stocks",
            "AAPL",
            {
                "symbol": "AAPL",
                "bid_price": 140.0,
                "ask_price": 140.2,
                "latest_price": 140.1,
                "volume": 1000,
                "data_quality": "unit_test_live_bridge",
            },
        )

        ticks = runtime.data_recorder.load_ticks("AAPL.SMART")
        core_ticks = runtime.main_engine.get_all_ticks()
    finally:
        app.state.omega_core_runtime.close()

    assert len(ticks) == 1
    assert ticks[0].last_price == 140.1
    assert ticks[0].source == "live_readonly"
    assert ticks[0].raw_json["data_quality"] == "unit_test_live_bridge"
    assert core_ticks[0].vt_symbol == "AAPL.SMART"


def test_option_master_api_builds_chain_curve_and_greeks(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    app.state.omega_core_runtime.option_engine.valuation_date_factory = lambda: date(2026, 5, 22)
    try:
        _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/configure", "POST")(
            "nvda", OptionPortfolioConfigureRequest(risk_free_rate=0.04)
        )
        contract = _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/contracts", "POST")(
            "nvda", OptionContractRequest(symbol="O:NVDA260618C00245000")
        )
        assert contract["symbol"] == "O:NVDA260618C00245000"
        _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/underlying-price", "POST")(
            "nvda", OptionUnderlyingPriceRequest(underlying="NVDA", price=250.0)
        )
        quote = _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/quotes", "POST")(
            "nvda",
            OptionQuoteUpdateRequest(symbol="O:NVDA260618C00245000", last_price=10.0, bid_price=9.9, ask_price=10.1),
        )
        assert quote["greeks_status"] == "ok"
        _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/positions", "POST")(
            "nvda",
            OptionPositionUpdateRequest(symbol="O:NVDA260618C00245000", net_volume=2, market_value=2000, average_price=10.0),
        )

        chain = _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/chain")("nvda", "NVDA", "20260618")
        curve = _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/volatility-curve")("nvda", "NVDA", "20260618")
        exposure = _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/greeks-exposure")("nvda")
        scenario = _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/scenario-matrix")("nvda", "NVDA")
    finally:
        app.state.omega_core_runtime.close()

    assert chain["rows"][0]["call"]["iv"] > 0
    assert curve["points"][0]["call_iv"] > 0
    assert exposure["contracts"] == 1
    assert exposure["delta_shares"] != 0
    assert scenario["contracts_used"] == 1
    assert len(scenario["rows"]) == 15


def test_option_master_syncs_tiger_option_positions_into_risk_model(tmp_path, monkeypatch) -> None:
    class SnapshotClient:
        def get_prime_assets(self):
            return [{"account": "TEST-SIM-ACCOUNT-0001", "net_liquidation": 1_000_000.0, "available": 900_000.0}]

        def get_positions(self):
            return [
                {"symbol": "NVDA", "quantity": 100, "average_cost": 250.0, "unrealized_pnl": 0.0},
                {"symbol": "O:NVDA260618C00245000", "quantity": 2, "average_cost": 10.0, "unrealized_pnl": 0.0},
            ]

        def get_orders(self):
            return []

    monkeypatch.setattr("omega_quant.gateways.tigeropen_gateway.build_trade_client", lambda: SnapshotClient())
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    app.state.omega_core_runtime.option_engine.valuation_date_factory = lambda: date(2026, 5, 22)
    try:
        result = _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/sync-tiger-positions", "POST")(
            "tiger_account", OptionReadonlySyncRequest(risk_free_rate=0.04)
        )
        chain = _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/chain")(
            "tiger_account", "NVDA", "20260618"
        )
        exposure = _endpoint(app, "/api/option-master/portfolios/{portfolio_name}/greeks-exposure")("tiger_account")
    finally:
        app.state.omega_core_runtime.close()

    assert result["source"] == "tigeropen_account_snapshot"
    assert result["synced_positions"] == 1
    assert result["primary_underlying"] == "NVDA"
    assert result["primary_expiry"] == "20260618"
    assert result["items"][0]["has_underlying_price"] is True
    assert chain["rows"][0]["call"]["greeks_status"] == "ok"
    assert exposure["contracts"] == 1
    assert exposure["delta_shares"] != 0


def test_spread_trading_api_calculates_and_routes_stock_option_spread_order(
    tmp_path, monkeypatch, armed_execution
) -> None:
    class FakeTigerTradeClient:
        def get_contract(self, symbol, sec_type, **kwargs):
            return type("FakeContract", (), {"symbol": symbol, "sec_type": str(sec_type)})()

        def create_order(self, *args, **kwargs):
            return type("FakeOrder", (), {"id": "fake-spread-order"})()

        def preview_order(self, order):
            return {"warning_text": None}

        def place_order(self, order):
            return f"BROKER-{order.id}"

    monkeypatch.setattr(
        "omega_quant.gateways.tigeropen_gateway.build_trade_client",
        lambda: FakeTigerTradeClient(),
    )
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        app.state.omega_execution_control.arm(account_suffix="0001", confirmation=LIVE_CONFIRM_VALUE)
        _endpoint(app, "/api/spread-trading/spreads", "POST")(
            SpreadCreateRequest(
                name="NVDA_COVERED_CALL",
                active_leg="NVDA",
                legs=[
                    SpreadLegRequest(symbol="NVDA", product="equity", price_multiplier=1, trading_multiplier=100),
                    SpreadLegRequest(
                        symbol="O:NVDA260618C00245000",
                        product="option",
                        price_multiplier=-1,
                        trading_multiplier=-1,
                    ),
                ],
            )
        )
        _endpoint(app, "/api/core/ticks", "POST")(CoreTickUpdate(symbol="NVDA", last_price=250, bid_price=249.9, ask_price=250.1))
        _endpoint(app, "/api/core/ticks", "POST")(
            CoreTickUpdate(symbol="O:NVDA260618C00245000", last_price=10, bid_price=9.9, ask_price=10.1)
        )
        spread = _endpoint(app, "/api/spread-trading/spreads/{spread_name}")("NVDA_COVERED_CALL")
        order = _endpoint(app, "/api/spread-trading/spreads/{spread_name}/orders", "POST")(
            "NVDA_COVERED_CALL",
            SpreadOrderRequest(direction="long", volume=1, price_offset=0.01, reference="api-spread-test"),
        )
    finally:
        app.state.omega_core_runtime.close()

    assert spread["tick"]["last_price"] == 240.0
    assert order["spread_name"] == "NVDA_COVERED_CALL"
    assert len(order["orderids"]) == 2
    assert order["spread"]["child_orderids"] == order["orderids"]


def test_algo_api_can_start_pause_resume_and_stop_twap(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        templates = _endpoint(app, "/api/algo/templates")()
        started = _endpoint(app, "/api/algo/twap", "POST")(
            AlgoTwapStartRequest(
                symbol="AAPL",
                direction="long",
                total_volume=3,
                limit_price=100,
                slices=3,
                interval_ticks=1,
                reference="api-twap-test",
            )
        )
        active = _endpoint(app, "/api/algo/active")()
        paused = _endpoint(app, "/api/algo/{algoid}/pause", "POST")(started["algoid"])
        resumed = _endpoint(app, "/api/algo/{algoid}/resume", "POST")(started["algoid"])
        stopped = _endpoint(app, "/api/algo/{algoid}/stop", "POST")(started["algoid"])
        finished = _endpoint(app, "/api/algo/finished")()
    finally:
        app.state.omega_core_runtime.close()

    assert templates[0]["name"] == "twap"
    assert started["status"] == "running"
    assert active[0]["algoid"] == started["algoid"]
    assert paused["status"] == "paused"
    assert resumed["status"] == "running"
    assert stopped["status"] == "stopped"
    assert finished[0]["algoid"] == started["algoid"]


def test_algo_api_can_start_iceberg(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        templates = _endpoint(app, "/api/algo/templates")()
        started = _endpoint(app, "/api/algo/iceberg", "POST")(
            AlgoIcebergStartRequest(
                symbol="AAPL",
                direction="long",
                total_volume=10,
                display_volume=2,
                limit_price=100,
                interval_ticks=1,
                reference="api-iceberg-test",
            )
        )
        active = _endpoint(app, "/api/algo/active")()
        stopped = _endpoint(app, "/api/algo/{algoid}/stop", "POST")(started["algoid"])
    finally:
        app.state.omega_core_runtime.close()

    assert {template["name"] for template in templates} >= {"twap", "iceberg"}
    assert started["algo_type"] == "iceberg"
    assert started["display_volume"] == 2
    assert active[0]["algoid"] == started["algoid"]
    assert stopped["status"] == "stopped"


def test_data_recorder_manager_and_cta_backtester_api_flow(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
    try:
        recordings = _endpoint(app, "/api/data-recorder/tick-recordings", "POST")(
            DataRecordingRequest(vt_symbol="AAPL.SMART")
        )
        assert recordings["tick_recordings"] == ["AAPL.SMART"]

        for idx in range(40):
            close = 100.0 + idx * 0.25
            _endpoint(app, "/api/data-recorder/bars", "POST")(
                RecordedBarCreate(
                    symbol="AAPL",
                    timestamp=start + timedelta(minutes=idx),
                    open_price=close - 0.1,
                    high_price=close + 0.3,
                    low_price=close - 0.3,
                    close_price=close,
                    volume=1_000 + idx,
                    source="unit_test",
                )
            )

        overview = _endpoint(app, "/api/data-manager/overview")()
        result = _endpoint(app, "/api/cta-backtester/run", "POST")(
            CtaBacktestRunRequest(vt_symbol="AAPL.SMART", fast_window=3, slow_window=8, capital=100_000)
        )
    finally:
        app.state.omega_core_runtime.close()

    assert overview[0]["vt_symbol"] == "AAPL.SMART"
    assert overview[0]["data_type"] == "bar"
    assert overview[0]["count"] == 40
    assert result["status"] == "completed"
    assert result["engine"] == "omega_cta_backtester"
    assert "total_commission" in result


def test_data_recorder_recording_can_auto_subscribe_live_feed(tmp_path) -> None:
    class FakeAdapter:
        def add_event_listener(self, _listener):
            return None

        def status(self):
            return {
                "status": "disconnected",
                "connected": False,
                "read_only": True,
                "subscribed_symbols": [],
                "subscribe_quotes": True,
                "subscribe_trades": False,
                "last_event_at": None,
                "events_received": 0,
                "message": "fake",
            }

    class FakeMarketController:
        def __init__(self) -> None:
            self.live_adapter = FakeAdapter()
            self.subscribed: list[list[str]] = []
            self.unsubscribed: list[list[str]] = []

        def subscribe_live(self, symbols: list[str]) -> dict:
            self.subscribed.append(symbols)
            return {
                "status": "starting",
                "connected": False,
                "read_only": True,
                "subscribed_symbols": symbols,
                "subscribe_quotes": True,
                "subscribe_trades": False,
                "last_event_at": None,
                "events_received": 0,
                "message": "fake subscribe",
            }

        def unsubscribe_live(self, symbols: list[str]) -> dict:
            self.unsubscribed.append(symbols)
            return {
                "status": "idle",
                "connected": False,
                "read_only": True,
                "subscribed_symbols": [],
                "subscribe_quotes": True,
                "subscribe_trades": False,
                "last_event_at": None,
                "events_received": 0,
                "message": "fake unsubscribe",
            }

    controller = FakeMarketController()
    app = create_app(
        f"sqlite:///{tmp_path / 'omega.sqlite'}",
        enable_data_recorder_live_subscription=True,
        market_data_controller=controller,  # type: ignore[arg-type]
    )
    try:
        tick_recording = _endpoint(app, "/api/data-recorder/tick-recordings", "POST")(DataRecordingRequest(vt_symbol="AAPL.SMART"))
        bar_recording = _endpoint(app, "/api/data-recorder/bar-recordings", "POST")(
            DataRecordingRequest(vt_symbol="O:NVDA260618C00245000.SMART")
        )
        tick_removed = _endpoint(app, "/api/data-recorder/tick-recordings/{vt_symbol:path}", "DELETE")(
            "AAPL.SMART"
        )
        bar_removed = _endpoint(app, "/api/data-recorder/bar-recordings/{vt_symbol:path}", "DELETE")(
            "O:NVDA260618C00245000.SMART"
        )
    finally:
        app.state.omega_core_runtime.close()

    assert controller.subscribed == [["AAPL"], ["O:NVDA260618C00245000"]]
    assert controller.unsubscribed == [["AAPL"], ["O:NVDA260618C00245000"]]
    assert tick_recording["live_readonly_status"]["subscribed_symbols"] == ["AAPL"]
    assert bar_recording["live_readonly_status"]["subscribed_symbols"] == ["O:NVDA260618C00245000"]
    assert tick_removed["live_readonly_status"]["message"] == "fake unsubscribe"
    assert bar_removed["live_readonly_status"]["message"] == "fake unsubscribe"


def test_data_recorder_auto_unsubscribe_waits_until_symbol_has_no_recordings(tmp_path) -> None:
    class FakeAdapter:
        def add_event_listener(self, _listener):
            return None

        def status(self):
            return {
                "status": "disconnected",
                "connected": False,
                "read_only": True,
                "subscribed_symbols": [],
                "subscribe_quotes": True,
                "subscribe_trades": False,
                "last_event_at": None,
                "events_received": 0,
                "message": "fake",
            }

    class FakeMarketController:
        def __init__(self) -> None:
            self.live_adapter = FakeAdapter()
            self.unsubscribed: list[list[str]] = []

        def subscribe_live(self, symbols: list[str]) -> dict:
            return {"subscribed_symbols": symbols}

        def unsubscribe_live(self, symbols: list[str]) -> dict:
            self.unsubscribed.append(symbols)
            return {"subscribed_symbols": []}

    controller = FakeMarketController()
    app = create_app(
        f"sqlite:///{tmp_path / 'omega.sqlite'}",
        enable_data_recorder_live_subscription=True,
        market_data_controller=controller,  # type: ignore[arg-type]
    )
    try:
        _endpoint(app, "/api/data-recorder/tick-recordings", "POST")(DataRecordingRequest(vt_symbol="AAPL.SMART"))
        _endpoint(app, "/api/data-recorder/bar-recordings", "POST")(DataRecordingRequest(vt_symbol="AAPL.SMART"))
        _endpoint(app, "/api/data-recorder/tick-recordings/{vt_symbol:path}", "DELETE")("AAPL.SMART")
        assert controller.unsubscribed == []
        _endpoint(app, "/api/data-recorder/bar-recordings/{vt_symbol:path}", "DELETE")("AAPL.SMART")
    finally:
        app.state.omega_core_runtime.close()

    assert controller.unsubscribed == [["AAPL"]]


def test_data_manager_api_exports_and_deletes_recorded_bars(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        endpoint_record_bar = _endpoint(app, "/api/data-recorder/bars", "POST")
        for idx in range(3):
            endpoint_record_bar(
                RecordedBarCreate(
                    symbol="AAPL",
                    timestamp=datetime(2026, 5, 24, 13, 30 + idx, tzinfo=timezone.utc),
                    open_price=100 + idx,
                    high_price=101 + idx,
                    low_price=99 + idx,
                    close_price=100.5 + idx,
                    volume=1000 + idx,
                    interval="1m",
                    exchange="SMART",
                    source="unit_test",
                )
            )

        overview = _endpoint(app, "/api/data-manager/overview")()
        response = _endpoint(app, "/api/data-manager/bars/export")("AAPL.SMART", "1m")
        deleted = _endpoint(app, "/api/data-manager/symbols/{vt_symbol:path}", "DELETE")("AAPL.SMART")
        overview_after = _endpoint(app, "/api/data-manager/overview")()
    finally:
        app.state.omega_core_runtime.close()

    assert overview == [
        {
            "vt_symbol": "AAPL.SMART",
            "data_type": "bar",
            "interval": "1m",
            "count": 3,
            "start": "2026-05-24T13:30:00",
            "end": "2026-05-24T13:32:00",
        }
    ]
    csv_text = response.body.decode()
    assert "datetime,symbol,exchange,interval,open,high,low,close,volume" in csv_text
    assert "AAPL" in csv_text
    assert response.media_type.startswith("text/csv")
    assert deleted == {"ticks_deleted": 0, "bars_deleted": 3}
    assert overview_after == []


def test_data_manager_api_imports_bars_csv(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    csv_text = "\n".join(
        [
            "datetime,symbol,exchange,interval,open,high,low,close,volume",
            "2026-05-24T13:30:00,AAPL,SMART,1m,100,101,99,100.5,1000",
            "2026-05-24T13:31:00,AAPL,SMART,1m,100.5,102,100,101.5,1100",
        ]
    )
    try:
        result = _endpoint(app, "/api/data-manager/bars/import", "POST")(
            BarCsvImportRequest(csv_text=csv_text, vt_symbol="AAPL.SMART", interval="1m", source="unit_import")
        )
        overview = _endpoint(app, "/api/data-manager/overview")()
    finally:
        app.state.omega_core_runtime.close()

    assert result["vt_symbol"] == "AAPL.SMART"
    assert result["bars_created"] == 2
    assert result["bars_skipped"] == 0
    assert overview[0]["vt_symbol"] == "AAPL.SMART"
    assert overview[0]["data_type"] == "bar"
    assert overview[0]["count"] == 2


def test_core_tick_api_records_tick_synchronously_without_duplicate(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        _endpoint(app, "/api/data-recorder/tick-recordings", "POST")(
            DataRecordingRequest(vt_symbol="AAPL.SMART")
        )
        tick_result = _endpoint(app, "/api/core/ticks", "POST")(
            CoreTickUpdate(symbol="AAPL", last_price=140, bid_price=139.95, ask_price=140.05, volume=1000)
        )
        overview_now = _endpoint(app, "/api/data-manager/overview")()
        time.sleep(0.15)
        overview_later = _endpoint(app, "/api/data-manager/overview")()
    finally:
        app.state.omega_core_runtime.close()

    assert tick_result["recorded_tick_id"]
    tick_rows_now = [row for row in overview_now if row["vt_symbol"] == "AAPL.SMART" and row["data_type"] == "tick"]
    tick_rows_later = [row for row in overview_later if row["vt_symbol"] == "AAPL.SMART" and row["data_type"] == "tick"]
    assert tick_rows_now[0]["count"] == 1
    assert tick_rows_later[0]["count"] == 1


def test_data_recorder_recording_delete_endpoints_persist_disabled_state(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'omega.sqlite'}"
    app = create_app(database_url)
    try:
        _endpoint(app, "/api/data-recorder/tick-recordings", "POST")(
            DataRecordingRequest(vt_symbol="AAPL.SMART")
        )
        _endpoint(app, "/api/data-recorder/bar-recordings", "POST")(
            DataRecordingRequest(vt_symbol="MSFT.SMART")
        )
        tick_status = _endpoint(app, "/api/data-recorder/tick-recordings/{vt_symbol:path}", "DELETE")(
            "AAPL.SMART"
        )
        bar_status = _endpoint(app, "/api/data-recorder/bar-recordings/{vt_symbol:path}", "DELETE")(
            "MSFT.SMART"
        )
    finally:
        app.state.omega_core_runtime.close()

    assert tick_status["tick_recordings"] == []
    assert bar_status["bar_recordings"] == []

    restarted = create_app(database_url)
    try:
        restored = _endpoint(restarted, "/api/data-recorder/recordings", "GET")()
    finally:
        restarted.state.omega_core_runtime.close()

    assert restored["tick_recordings"] == []
    assert restored["bar_recordings"] == []


def test_core_tick_api_updates_oms_without_implicit_database_recording(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    try:
        runtime = app.state.omega_core_runtime
        tick_result = _endpoint(app, "/api/core/ticks", "POST")(
            CoreTickUpdate(symbol="AAPL", last_price=140, bid_price=139.95, ask_price=140.05, volume=1000)
        )
        overview = _endpoint(app, "/api/data-manager/overview")()
        oms_ticks = runtime.main_engine.get_all_ticks()
    finally:
        app.state.omega_core_runtime.close()

    assert tick_result["updated"] is True
    assert tick_result["recorded_tick_id"] is None
    assert [row for row in overview if row["vt_symbol"] == "AAPL.SMART"] == []
    assert oms_ticks[0].vt_symbol == "AAPL.SMART"


def test_core_tick_api_can_feed_tick_aggregation_and_cta_backtest(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
    try:
        _endpoint(app, "/api/data-recorder/tick-recordings", "POST")(
            DataRecordingRequest(vt_symbol="AAPL.SMART")
        )
        for idx in range(40):
            close = 100.0 + idx * 0.25
            _endpoint(app, "/api/core/ticks", "POST")(
                CoreTickUpdate(
                    symbol="AAPL",
                    timestamp=start + timedelta(minutes=idx, seconds=5),
                    last_price=close,
                    bid_price=close - 0.01,
                    ask_price=close + 0.01,
                    volume=100 + idx,
                )
            )
        aggregate = _endpoint(app, "/api/data-manager/aggregate-ticks", "POST")(
            TickBarAggregationRequest(vt_symbol="AAPL.SMART")
        )
        result = _endpoint(app, "/api/cta-backtester/run", "POST")(
            CtaBacktestRunRequest(vt_symbol="AAPL.SMART", fast_window=3, slow_window=8, capital=100_000)
        )
    finally:
        app.state.omega_core_runtime.close()

    assert aggregate["ticks_read"] == 40
    assert aggregate["bars_created"] == 40
    assert result["status"] == "completed"
    assert result["bar_count"] == 40


def test_core_tick_api_generates_live_bars_when_only_bar_recording_is_enabled(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
    try:
        _endpoint(app, "/api/data-recorder/tick-recordings", "POST")(
            DataRecordingRequest(vt_symbol="AAPL.SMART")
        )
        _endpoint(app, "/api/data-recorder/bar-recordings", "POST")(
            DataRecordingRequest(vt_symbol="MSFT.SMART")
        )
        _endpoint(app, "/api/core/ticks", "POST")(
            CoreTickUpdate(symbol="MSFT", timestamp=start, last_price=100.0, volume=10)
        )
        _endpoint(app, "/api/core/ticks", "POST")(
            CoreTickUpdate(symbol="MSFT", timestamp=start + timedelta(seconds=20), last_price=101.0, volume=15)
        )
        _endpoint(app, "/api/core/ticks", "POST")(
            CoreTickUpdate(symbol="MSFT", timestamp=start + timedelta(minutes=1, seconds=5), last_price=102.0, volume=22)
        )
        overview = _endpoint(app, "/api/data-manager/overview")()
    finally:
        app.state.omega_core_runtime.close()

    tick_rows = [row for row in overview if row["vt_symbol"] == "MSFT.SMART" and row["data_type"] == "tick"]
    bar_rows = [row for row in overview if row["vt_symbol"] == "MSFT.SMART" and row["data_type"] == "bar"]
    assert tick_rows == []
    assert bar_rows
    assert bar_rows[0]["count"] == 1


def test_core_tick_api_can_feed_direct_tick_cta_backtest(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
    try:
        _endpoint(app, "/api/data-recorder/tick-recordings", "POST")(
            DataRecordingRequest(vt_symbol="AAPL.SMART")
        )
        for idx in range(40):
            close = 100.0 + idx * 0.25
            _endpoint(app, "/api/core/ticks", "POST")(
                CoreTickUpdate(
                    symbol="AAPL",
                    timestamp=start + timedelta(minutes=idx, seconds=5),
                    last_price=close,
                    bid_price=close - 0.01,
                    ask_price=close + 0.01,
                    volume=1_000 + idx,
                )
            )
        result = _endpoint(app, "/api/cta-backtester/run", "POST")(
            CtaBacktestRunRequest(
                vt_symbol="AAPL.SMART",
                data_source="tick",
                fast_window=3,
                slow_window=8,
                capital=100_000,
            )
        )
    finally:
        app.state.omega_core_runtime.close()

    assert result["status"] == "completed"
    assert result["data_source"] == "tick"
    assert result["tick_count"] == 40
    assert result["bar_count"] == 40
    assert "BarGenerator" in result["source"]


def test_cta_backtester_strategy_registry_api_and_registered_strategy_run(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
    try:
        strategies = _endpoint(app, "/api/cta-backtester/strategies")()
        for idx in range(30):
            close = 100.0 + idx * 0.35
            _endpoint(app, "/api/data-recorder/bars", "POST")(
                RecordedBarCreate(
                    symbol="AAPL",
                    timestamp=start + timedelta(minutes=idx),
                    open_price=close - 0.1,
                    high_price=close + 0.3,
                    low_price=close - 0.3,
                    close_price=close,
                    volume=1_000 + idx,
                    source="unit_test",
                )
            )
        result = _endpoint(app, "/api/cta-backtester/run", "POST")(
            CtaBacktestRunRequest(
                vt_symbol="AAPL.SMART",
                strategy_class="DonchianBreakoutStrategy",
                strategy_setting={"entry_window": 8, "exit_window": 4, "fixed_size": 1},
                fast_window=3,
                slow_window=9,
                capital=100_000,
            )
        )
    finally:
        app.state.omega_core_runtime.close()

    names = {item["class_name"] for item in strategies}
    assert {"MovingAverageCrossStrategy", "DonchianBreakoutStrategy"} <= names
    assert result["status"] == "completed"
    assert result["strategy_class"] == "DonchianBreakoutStrategy"
    assert result["parameters"]["entry_window"] == 8


def test_cta_backtester_optimize_api_runs_on_recorded_ticks(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
    try:
        _endpoint(app, "/api/data-recorder/tick-recordings", "POST")(
            DataRecordingRequest(vt_symbol="AAPL.SMART")
        )
        for idx in range(50):
            close = 100.0 + idx * 0.2 + (idx % 5) * 0.03
            _endpoint(app, "/api/core/ticks", "POST")(
                CoreTickUpdate(
                    symbol="AAPL",
                    timestamp=start + timedelta(minutes=idx, seconds=5),
                    last_price=close,
                    bid_price=close - 0.01,
                    ask_price=close + 0.01,
                    volume=2_000 + idx,
                )
            )
        result = _endpoint(app, "/api/cta-backtester/optimize", "POST")(
            CtaBacktestOptimizeRequest(
                vt_symbol="AAPL.SMART",
                data_source="tick",
                fast_values=[2, 3],
                slow_values=[8, 13],
                capital=100_000,
            )
        )
    finally:
        app.state.omega_core_runtime.close()

    assert result["status"] == "completed"
    assert result["data_source"] == "tick"
    assert result["tested_combinations"] == 4
    assert result["completed_runs"] == 4
    assert result["results"]
    assert result["results"][0]["fast_window"] < result["results"][0]["slow_window"]
    assert result["results"][0]["result"]["tick_count"] == 50


def test_risk_manager_api_evaluates_price_band_and_records_cancels(tmp_path) -> None:
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")
    now = datetime(2026, 5, 24, 10, 0, tzinfo=timezone.utc)
    try:
        _endpoint(app, "/api/core/ticks", "POST")(
            CoreTickUpdate(symbol="AAPL", timestamp=now, last_price=100, volume=100)
        )
        cancel_state = _endpoint(app, "/api/risk-manager/cancels", "POST")(
            RiskManagerCancelRecordRequest(symbol="AAPL")
        )
        result = _endpoint(app, "/api/risk-manager/evaluate", "POST")(
            RiskManagerEvaluateRequest(symbol="AAPL", side="buy", quantity=1, price=130, now=now)
        )
    finally:
        app.state.omega_core_runtime.close()

    assert cancel_state["state"]["cancel_counts_by_symbol"]["AAPL"] == 1
    assert result["approved"] is False
    assert any(check["name"] == "price_band" and not check["passed"] for check in result["checks"])


def test_risk_manager_api_updates_pretrade_config(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'omega.sqlite'}"
    app = create_app(database_url)
    try:
        status = _endpoint(app, "/api/risk-manager/config", "PATCH")(
            RiskManagerConfigUpdateRequest(
                order_flow_limit=3,
                cancel_limit_per_symbol=2,
                price_band_pct=0.03,
                fat_finger_notional_limit=50_000,
                symbol_exposure_limit=100_000,
                strategy_exposure_limit=75_000,
                account_exposure_limit=250_000,
                trading_timezone="America/New_York",
                allowed_sessions=[("09:30", "16:00")],
            )
        )
    finally:
        app.state.omega_core_runtime.close()

    config = status["config"]
    assert config["order_flow_limit"] == 3
    assert config["cancel_limit_per_symbol"] == 2
    assert config["price_band_pct"] == 0.03
    assert config["fat_finger_notional_limit"] == 50_000
    assert config["symbol_exposure_limit"] == 100_000
    assert config["strategy_exposure_limit"] == 75_000
    assert config["account_exposure_limit"] == 250_000
    assert config["trading_timezone"] == "America/New_York"
    assert config["allowed_sessions"] == [{"start": "09:30", "end": "16:00"}]

    restarted_app = create_app(database_url)
    try:
        restored = _endpoint(restarted_app, "/api/risk-manager/status", "GET")()
    finally:
        restarted_app.state.omega_core_runtime.close()

    restored_config = restored["config"]
    assert restored_config["order_flow_limit"] == 3
    assert restored_config["cancel_limit_per_symbol"] == 2
    assert restored_config["price_band_pct"] == 0.03
    assert restored_config["fat_finger_notional_limit"] == 50_000
    assert restored_config["symbol_exposure_limit"] == 100_000
    assert restored_config["strategy_exposure_limit"] == 75_000
    assert restored_config["account_exposure_limit"] == 250_000
    assert restored_config["trading_timezone"] == "America/New_York"
    assert restored_config["allowed_sessions"] == [{"start": "09:30", "end": "16:00"}]
