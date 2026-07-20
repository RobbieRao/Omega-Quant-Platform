from __future__ import annotations

from datetime import datetime, timezone

from omega_quant.core import (
    EVENT_TICK,
    AlgoStatus,
    AlgoTradingEngine,
    BaseGateway,
    Direction,
    Event,
    Exchange,
    IcebergAlgoSetting,
    MainEngine,
    OrderData,
    OrderRequest,
    Product,
    Status,
    TickData,
    TwapAlgoSetting,
)


class CaptureGateway(BaseGateway):
    default_name = "CAPTURE"
    exchanges = [Exchange.SMART.value]

    def __init__(self, event_engine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)
        self.orders: list[OrderRequest] = []
        self.cancelled: list[str] = []
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
            orderid=f"CAP{self._order_count:04d}",
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
        self.cancelled.append(req.orderid)

    def query_account(self) -> None:
        return

    def query_position(self) -> None:
        return


def _main_with_algo() -> tuple[MainEngine, AlgoTradingEngine, CaptureGateway]:
    main_engine = MainEngine(auto_start=False)
    gateway = main_engine.add_gateway(CaptureGateway)
    algo_engine = main_engine.add_engine(AlgoTradingEngine)
    return main_engine, algo_engine, gateway  # type: ignore[return-value]


def _setting(**kwargs) -> TwapAlgoSetting:
    defaults = {
        "symbol": "AAPL",
        "direction": Direction.LONG,
        "total_volume": 10,
        "limit_price": 140.0,
        "gateway_name": CaptureGateway.default_name,
        "slices": 5,
        "interval_ticks": 1,
        "product": Product.EQUITY,
    }
    defaults.update(kwargs)
    return TwapAlgoSetting(**defaults)


def _iceberg_setting(**kwargs) -> IcebergAlgoSetting:
    defaults = {
        "symbol": "AAPL",
        "direction": Direction.LONG,
        "total_volume": 7,
        "display_volume": 3,
        "limit_price": 140.0,
        "gateway_name": CaptureGateway.default_name,
        "interval_ticks": 1,
        "product": Product.EQUITY,
    }
    defaults.update(kwargs)
    return IcebergAlgoSetting(**defaults)


def test_twap_splits_parent_order_into_child_orders() -> None:
    main_engine, algo_engine, gateway = _main_with_algo()
    try:
        state = algo_engine.start_twap(_setting(total_volume=10, slices=5))
        for _ in range(5):
            algo_engine.process_timer_event(Event("eTimer"))

        assert [order.volume for order in gateway.orders] == [2, 2, 2, 2, 2]
        finished = algo_engine.get_algo(state.algoid)
        assert finished is not None
        assert finished.status == AlgoStatus.FINISHED
        assert finished.sent_volume == 10
        assert len(finished.child_orderids) == 5
        assert all(order.reference == f"OmegaAlgo:{state.algoid}" for order in gateway.orders)
    finally:
        main_engine.close()


def test_twap_waits_for_limit_price_when_tick_is_worse() -> None:
    main_engine, algo_engine, gateway = _main_with_algo()
    try:
        oms = main_engine.get_engine("oms")
        tick = TickData(
            symbol="AAPL",
            exchange=Exchange.SMART,
            datetime=datetime.now(timezone.utc),
            last_price=141.0,
            bid_price_1=140.95,
            ask_price_1=141.05,
        )
        oms.process_tick_event(Event(EVENT_TICK, tick))  # type: ignore[attr-defined]

        state = algo_engine.start_twap(_setting(limit_price=140.0))
        algo_engine.process_timer_event(Event("eTimer"))

        assert gateway.orders == []
        assert algo_engine.get_algo(state.algoid).message == "waiting for limit price"  # type: ignore[union-attr]
    finally:
        main_engine.close()


def test_twap_stop_cancels_active_child_orders_and_prevents_more_sends() -> None:
    main_engine, algo_engine, gateway = _main_with_algo()
    try:
        state = algo_engine.start_twap(_setting(total_volume=9, slices=3))
        algo_engine.process_timer_event(Event("eTimer"))
        assert len(gateway.orders) == 1

        stopped = algo_engine.stop_algo(state.algoid)
        algo_engine.process_timer_event(Event("eTimer"))

        assert stopped.status == AlgoStatus.STOPPED
        assert gateway.cancelled == ["CAP0001"]
        assert len(gateway.orders) == 1
    finally:
        main_engine.close()


def test_iceberg_sends_fixed_display_quantity_until_parent_is_done() -> None:
    main_engine, algo_engine, gateway = _main_with_algo()
    try:
        state = algo_engine.start_iceberg(_iceberg_setting(total_volume=7, display_volume=3))
        for _ in range(3):
            algo_engine.process_timer_event(Event("eTimer"))

        assert state.algo_type == "iceberg"
        assert [order.volume for order in gateway.orders] == [3, 3, 1]
        finished = algo_engine.get_algo(state.algoid)
        assert finished is not None
        assert finished.status == AlgoStatus.FINISHED
        assert finished.sent_volume == 7
        assert all(order.reference == f"OmegaAlgo:{state.algoid}" for order in gateway.orders)
    finally:
        main_engine.close()


def test_iceberg_waits_for_limit_price_when_tick_is_worse() -> None:
    main_engine, algo_engine, gateway = _main_with_algo()
    try:
        oms = main_engine.get_engine("oms")
        tick = TickData(
            symbol="AAPL",
            exchange=Exchange.SMART,
            datetime=datetime.now(timezone.utc),
            last_price=141.0,
            bid_price_1=140.95,
            ask_price_1=141.05,
        )
        oms.process_tick_event(Event(EVENT_TICK, tick))  # type: ignore[attr-defined]

        state = algo_engine.start_iceberg(_iceberg_setting(limit_price=140.0))
        algo_engine.process_timer_event(Event("eTimer"))

        assert gateway.orders == []
        assert algo_engine.get_algo(state.algoid).message == "waiting for limit price"  # type: ignore[union-attr]
    finally:
        main_engine.close()


def test_algo_core_does_not_import_qt_or_domestic_gateway_modules() -> None:
    import sys

    forbidden = ["vnpy.trader.ui", "vnpy_ctp", "vnpy_xtp", "vnpy_sopt", "vnpy_ctptest"]
    assert not any(name in sys.modules for name in forbidden)
