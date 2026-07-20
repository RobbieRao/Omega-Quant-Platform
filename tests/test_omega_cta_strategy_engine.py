from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from omega_quant.core import (
    CtaStrategyEngine,
    Direction,
    Event,
    Exchange,
    MainEngine,
    OrderData,
    OrderRequest,
    Product,
    RiskManagerEngine,
    Status,
    TickData,
)
from omega_quant.core.gateway import BaseGateway
from omega_quant.core.runtime import OmegaCoreRuntime
from omega_quant.database import build_engine, build_session_factory, init_db


class CaptureGateway(BaseGateway):
    default_name = "CAPTURE"

    def __init__(self, event_engine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)
        self.orders: list[OrderRequest] = []
        self._order_count = 0

    def connect(self, setting: dict) -> None:
        return

    def close(self) -> None:
        return

    def subscribe(self, req) -> None:
        return

    def send_order(self, req: OrderRequest) -> str:
        self._order_count += 1
        self.orders.append(req)
        order = OrderData(
            symbol=req.symbol,
            exchange=req.exchange,
            orderid=f"CTA{self._order_count:04d}",
            direction=req.direction,
            volume=req.volume,
            price=req.price,
            status=Status.NOTTRADED,
            datetime=datetime.now(timezone.utc),
            gateway_name=self.gateway_name,
            reference=req.reference,
        )
        self.on_order(order)
        return order.vt_orderid

    def cancel_order(self, req) -> None:
        return

    def query_account(self) -> None:
        return

    def query_position(self) -> None:
        return


def _tick(symbol: str, price: float, timestamp: datetime | None = None, volume: float = 100.0) -> TickData:
    return TickData(
        symbol=symbol,
        exchange=Exchange.SMART,
        datetime=timestamp or datetime.now(timezone.utc),
        last_price=price,
        volume=volume,
        bid_price_1=price - 0.01,
        ask_price_1=price + 0.01,
    )


def test_live_cta_strategy_tick_signal_routes_through_main_engine_gateway() -> None:
    main_engine = MainEngine(auto_start=False)
    gateway = main_engine.add_gateway(CaptureGateway)
    cta_engine = main_engine.add_engine(CtaStrategyEngine)
    try:
        snapshot = cta_engine.add_strategy(
            strategy_name="tick_momentum",
            class_name="TickMomentumStrategy",
            vt_symbol="AAPL.SMART",
            setting={"trigger_pct": 0.001, "fixed_size": 2},
            gateway_name=CaptureGateway.default_name,
            product=Product.EQUITY,
        )
        assert snapshot["trading"] is False
        cta_engine.start_strategy("tick_momentum")

        main_engine.event_engine.put(Event("eTick.", _tick("AAPL", 100.0)))
        main_engine.event_engine.process_pending()
        assert gateway.orders == []

        main_engine.event_engine.put(Event("eTick.", _tick("AAPL", 100.2)))
        main_engine.event_engine.process_pending()

        assert len(gateway.orders) == 1
        assert gateway.orders[0].symbol == "AAPL"
        assert gateway.orders[0].direction == Direction.LONG
        assert gateway.orders[0].volume == 2
        assert gateway.orders[0].reference == "tick_momentum"
        strategy = cta_engine.strategies["tick_momentum"]
        assert strategy.active_orderids == {"CAPTURE.CTA0001"}
    finally:
        main_engine.close()


def test_live_cta_strategy_orders_are_blocked_by_risk_manager_before_gateway() -> None:
    main_engine = MainEngine(auto_start=False)
    gateway = main_engine.add_gateway(CaptureGateway)
    cta_engine = main_engine.add_engine(CtaStrategyEngine)
    risk_manager = main_engine.add_engine(RiskManagerEngine)
    risk_manager.update_config(active=True, order_size_limit=1.0, allowed_sessions=())
    try:
        cta_engine.add_strategy(
            strategy_name="too_large_tick_momentum",
            class_name="TickMomentumStrategy",
            vt_symbol="AAPL.SMART",
            setting={"trigger_pct": 0.001, "fixed_size": 2},
            gateway_name=CaptureGateway.default_name,
            product=Product.EQUITY,
        )
        cta_engine.start_strategy("too_large_tick_momentum")

        main_engine.event_engine.put(Event("eTick.", _tick("AAPL", 100.0)))
        main_engine.event_engine.put(Event("eTick.", _tick("AAPL", 100.2)))
        main_engine.event_engine.process_pending()

        assert gateway.orders == []
        orders = main_engine.get_all_orders()
        assert len(orders) == 1
        assert orders[0].status == Status.REJECTED
        assert orders[0].risk_result is not None
        assert orders[0].risk_result["approved"] is False
        assert "too_large_tick_momentum" in orders[0].reference
    finally:
        main_engine.close()


def test_live_cta_strategy_generates_bars_and_runs_bar_strategy() -> None:
    main_engine = MainEngine(auto_start=False)
    gateway = main_engine.add_gateway(CaptureGateway)
    cta_engine = main_engine.add_engine(CtaStrategyEngine)
    try:
        cta_engine.add_strategy(
            strategy_name="ma_cross_live",
            class_name="MovingAverageCrossStrategy",
            vt_symbol="AAPL.SMART",
            setting={"fast_window": 1, "slow_window": 2, "fixed_size": 1},
            gateway_name=CaptureGateway.default_name,
            product=Product.EQUITY,
        )
        cta_engine.start_strategy("ma_cross_live")
        start = datetime(2026, 5, 24, 13, 30, tzinfo=timezone.utc)
        prices = [100.0, 99.0, 101.0, 102.0]
        for idx, price in enumerate(prices):
            main_engine.event_engine.put(Event("eTick.", _tick("AAPL", price, start + timedelta(minutes=idx), volume=100 + idx)))
            main_engine.event_engine.process_pending()

        snapshot = cta_engine.strategy_snapshot("ma_cross_live")
        assert snapshot["bar_count"] >= 3
        assert snapshot["last_bar"]["close_price"] == 101.0
        assert len(gateway.orders) == 1
        assert gateway.orders[0].symbol == "AAPL"
        assert gateway.orders[0].direction == Direction.LONG
        assert gateway.orders[0].reference == "ma_cross_live"
    finally:
        main_engine.close()


def test_live_cta_strategy_lifecycle_and_templates() -> None:
    main_engine = MainEngine(auto_start=False)
    cta_engine = main_engine.add_engine(CtaStrategyEngine)
    try:
        template_names = {item["class_name"] for item in cta_engine.list_templates()}
        assert "TickMomentumStrategy" in template_names

        cta_engine.add_strategy(
            strategy_name="lifecycle",
            class_name="TickMomentumStrategy",
            vt_symbol="MSFT.SMART",
            gateway_name="CAPTURE",
        )
        started = cta_engine.start_strategy("lifecycle")
        assert started["inited"] is True
        assert started["trading"] is True
        stopped = cta_engine.stop_strategy("lifecycle")
        assert stopped["trading"] is False
        removed = cta_engine.remove_strategy("lifecycle")
        assert removed["removed"] is True
    finally:
        main_engine.close()


def test_runtime_persists_and_restores_live_cta_strategy_config(monkeypatch) -> None:
    monkeypatch.setattr("omega_quant.gateways.tigeropen_gateway.build_trade_client", lambda: object())
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = build_session_factory(engine)

    runtime = OmegaCoreRuntime(session_factory=session_factory)
    try:
        runtime.add_cta_live_strategy(
            SimpleNamespace(
                strategy_name="persisted_live_cta",
                class_name="TickMomentumStrategy",
                vt_symbol="AAPL.SMART",
                setting={"trigger_pct": 0.002, "fixed_size": 3},
                gateway_name="TIGEROPEN",
                product="equity",
                exchange="SMART",
            )
        )
        runtime.start_cta_live_strategy("persisted_live_cta")
    finally:
        runtime.close()

    restored = OmegaCoreRuntime(session_factory=session_factory)
    try:
        strategies = {item["strategy_name"]: item for item in restored.cta_live_strategies()}
        assert "persisted_live_cta" in strategies
        assert strategies["persisted_live_cta"]["trading"] is True
        assert strategies["persisted_live_cta"]["parameters"]["trigger_pct"] == 0.002
        assert strategies["persisted_live_cta"]["parameters"]["fixed_size"] == 3

        restored.stop_cta_live_strategy("persisted_live_cta")
        restored.remove_cta_live_strategy("persisted_live_cta")
    finally:
        restored.close()

    restored_again = OmegaCoreRuntime(session_factory=session_factory)
    try:
        assert restored_again.cta_live_strategies() == []
    finally:
        restored_again.close()
