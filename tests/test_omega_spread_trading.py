from __future__ import annotations

from datetime import datetime, timezone

from omega_quant.core import (
    Direction,
    Event,
    Exchange,
    MainEngine,
    OrderData,
    OrderRequest,
    PositionData,
    Product,
    RiskManagerEngine,
    SpreadTradingEngine,
    Status,
    TickData,
    make_option_leg,
    make_stock_leg,
)
from omega_quant.core.gateway import BaseGateway


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
            orderid=f"SP{self._order_count:04d}",
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


def _main_with_spread() -> tuple[MainEngine, SpreadTradingEngine, CaptureGateway]:
    main_engine = MainEngine(auto_start=False)
    gateway = main_engine.add_gateway(CaptureGateway)
    spread_engine = main_engine.add_engine(SpreadTradingEngine)
    return main_engine, spread_engine, gateway  # type: ignore[return-value]


def _tick(symbol: str, last: float, bid: float, ask: float) -> TickData:
    return TickData(
        symbol=symbol,
        exchange=Exchange.SMART,
        datetime=datetime.now(timezone.utc),
        last_price=last,
        bid_price_1=bid,
        ask_price_1=ask,
    )


def test_spread_engine_calculates_synthetic_stock_option_spread_tick() -> None:
    main_engine, spread_engine, _gateway = _main_with_spread()
    try:
        spread_engine.create_spread(
            "NVDA_COVERED_CALL",
            "NVDA",
            [
                make_stock_leg("NVDA", price_multiplier=1, trading_multiplier=100),
                make_option_leg("O:NVDA260618C00245000", price_multiplier=-1, trading_multiplier=-1),
            ],
        )

        main_engine.event_engine.put(Event("eTick.", _tick("NVDA", 250.0, 249.9, 250.1)))
        main_engine.event_engine.put(Event("eTick.", _tick("O:NVDA260618C00245000", 10.0, 9.9, 10.1)))
        main_engine.event_engine.process_pending()

        tick = spread_engine.get_spread_tick("NVDA_COVERED_CALL")
        assert tick is not None
        assert tick.bid_price == 239.8
        assert tick.ask_price == 240.2
        assert tick.last_price == 240.0
    finally:
        main_engine.close()


def test_spread_engine_calculates_net_position_from_leg_positions() -> None:
    main_engine, spread_engine, _gateway = _main_with_spread()
    try:
        spread_engine.create_spread(
            "NVDA_COVERED_CALL",
            "NVDA",
            [
                make_stock_leg("NVDA", price_multiplier=1, trading_multiplier=100),
                make_option_leg("O:NVDA260618C00245000", price_multiplier=-1, trading_multiplier=-1),
            ],
        )
        spread_engine.process_position_event(
            Event("ePosition.", PositionData("NVDA", Exchange.SMART, Direction.LONG, 200, 250.0))
        )
        spread_engine.process_position_event(
            Event(
                "ePosition.",
                PositionData("O:NVDA260618C00245000", Exchange.SMART, Direction.SHORT, 2, 10.0),
            )
        )

        assert spread_engine.get_spread_position("NVDA_COVERED_CALL") == 2
    finally:
        main_engine.close()


def test_spread_order_routes_to_us_stock_and_option_legs() -> None:
    main_engine, spread_engine, gateway = _main_with_spread()
    try:
        spread_engine.create_spread(
            "NVDA_COVERED_CALL",
            "NVDA",
            [
                make_stock_leg("NVDA", price_multiplier=1, trading_multiplier=100),
                make_option_leg("O:NVDA260618C00245000", price_multiplier=-1, trading_multiplier=-1),
            ],
        )
        main_engine.event_engine.put(Event("eTick.", _tick("NVDA", 250.0, 249.9, 250.1)))
        main_engine.event_engine.put(Event("eTick.", _tick("O:NVDA260618C00245000", 10.0, 9.9, 10.1)))
        main_engine.event_engine.process_pending()

        orderids = spread_engine.send_spread_order(
            "NVDA_COVERED_CALL",
            direction=Direction.LONG,
            volume=1,
            gateway_name=CaptureGateway.default_name,
            price_offset=0.01,
        )

        assert orderids == ["CAPTURE.SP0001", "CAPTURE.SP0002"]
        assert gateway.orders[0].symbol == "NVDA"
        assert gateway.orders[0].direction == Direction.LONG
        assert gateway.orders[0].volume == 100
        assert gateway.orders[0].price == 250.11
        assert gateway.orders[0].product == Product.EQUITY
        assert gateway.orders[1].symbol == "O:NVDA260618C00245000"
        assert gateway.orders[1].direction == Direction.SHORT
        assert gateway.orders[1].volume == 1
        assert gateway.orders[1].price == 9.89
        assert gateway.orders[1].product == Product.OPTION
    finally:
        main_engine.close()


def test_spread_order_legs_are_blocked_by_main_engine_risk_manager() -> None:
    main_engine, spread_engine, gateway = _main_with_spread()
    try:
        risk_manager = main_engine.add_engine(RiskManagerEngine)
        risk_manager.update_config(allowed_sessions=(), price_band_pct=0.05, fat_finger_notional_limit=1)
        spread_engine.create_spread(
            "NVDA_COVERED_CALL",
            "NVDA",
            [
                make_stock_leg("NVDA", price_multiplier=1, trading_multiplier=100),
                make_option_leg("O:NVDA260618C00245000", price_multiplier=-1, trading_multiplier=-1),
            ],
        )
        main_engine.event_engine.put(Event("eTick.", _tick("NVDA", 250.0, 249.9, 250.1)))
        main_engine.event_engine.put(Event("eTick.", _tick("O:NVDA260618C00245000", 10.0, 9.9, 10.1)))
        main_engine.event_engine.process_pending()

        orderids = spread_engine.send_spread_order(
            "NVDA_COVERED_CALL",
            direction=Direction.LONG,
            volume=1,
            gateway_name=CaptureGateway.default_name,
            price_offset=100,
        )
        main_engine.event_engine.process_pending()

        assert orderids == ["CAPTURE.RISK-REJECT-1", "CAPTURE.RISK-REJECT-2"]
        assert gateway.orders == []
        rejected = [main_engine.get_order(orderid) for orderid in orderids]
        assert all(order and order.status == Status.REJECTED for order in rejected)
    finally:
        main_engine.close()


def test_spread_engine_rejects_invalid_option_leg_symbol() -> None:
    try:
        make_option_leg("BTC-USD-OPTION", price_multiplier=1, trading_multiplier=1)
    except ValueError as exc:
        assert "unsupported US option" in str(exc)
    else:
        raise AssertionError("invalid non-US option symbol should be rejected")


def test_spread_core_does_not_import_qt_domestic_or_crypto_modules() -> None:
    import sys

    forbidden = ["vnpy.trader.ui", "vnpy_ctp", "vnpy_xtp", "vnpy_sopt", "vnpy_binance", "vnpy_okx"]
    assert not any(name in sys.modules for name in forbidden)
