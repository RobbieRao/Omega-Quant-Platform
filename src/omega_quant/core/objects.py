"""Omega trading objects backed by the pruned vn.py object model.

The public constructors remain compatible with earlier Omega code, while the
classes now inherit from ``omega_quant.vnpy_core.trader.object``. This is the
migration bridge toward using vn.py's mature MainEngine/Gateway object model as
OMEGA's core instead of a parallel hand-rolled object stack.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from omega_quant.vnpy_core.trader.constant import Direction, Exchange, Product, Status
from omega_quant.vnpy_core.trader.constant import Interval, Offset, OptionType, OrderType
from omega_quant.vnpy_core.trader.object import AccountData as VnpyAccountData
from omega_quant.vnpy_core.trader.object import BarData as VnpyBarData
from omega_quant.vnpy_core.trader.object import CancelRequest as VnpyCancelRequest
from omega_quant.vnpy_core.trader.object import ContractData as VnpyContractData
from omega_quant.vnpy_core.trader.object import LogData as VnpyLogData
from omega_quant.vnpy_core.trader.object import OrderData as VnpyOrderData
from omega_quant.vnpy_core.trader.object import OrderRequest as VnpyOrderRequest
from omega_quant.vnpy_core.trader.object import PositionData as VnpyPositionData
from omega_quant.vnpy_core.trader.object import SubscribeRequest as VnpySubscribeRequest
from omega_quant.vnpy_core.trader.object import TickData as VnpyTickData
from omega_quant.vnpy_core.trader.object import TradeData as VnpyTradeData


ACTIVE_STATUSES = {Status.SUBMITTING, Status.NOTTRADED, Status.PARTTRADED}


class LogData(VnpyLogData):
    def __init__(self, msg: str, gateway_name: str = "OMEGA", level: str | int = "INFO", **kwargs: Any) -> None:
        super().__init__(gateway_name=gateway_name, msg=msg, level=level)  # type: ignore[arg-type]
        self.extra = kwargs.get("extra")


class TickData(VnpyTickData):
    def __init__(
        self,
        symbol: str,
        exchange: Exchange,
        datetime: datetime,
        last_price: float,
        volume: float = 0.0,
        bid_price_1: float | None = None,
        ask_price_1: float | None = None,
        gateway_name: str = "OMEGA",
        raw: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            gateway_name=gateway_name,
            symbol=symbol,
            exchange=exchange,
            datetime=datetime,
            name=str(kwargs.get("name") or ""),
            volume=float(volume or 0.0),
            turnover=float(kwargs.get("turnover") or 0.0),
            open_interest=float(kwargs.get("open_interest") or 0.0),
            last_price=float(last_price or 0.0),
            last_volume=float(kwargs.get("last_volume") or 0.0),
            limit_up=float(kwargs.get("limit_up") or 0.0),
            limit_down=float(kwargs.get("limit_down") or 0.0),
            open_price=float(kwargs.get("open_price") or 0.0),
            high_price=float(kwargs.get("high_price") or 0.0),
            low_price=float(kwargs.get("low_price") or 0.0),
            pre_close=float(kwargs.get("pre_close") or 0.0),
            bid_price_1=float(bid_price_1 or 0.0),
            ask_price_1=float(ask_price_1 or 0.0),
            localtime=kwargs.get("localtime"),
        )
        self.raw = dict(raw or kwargs.get("raw") or {})


class BarData(VnpyBarData):
    def __init__(
        self,
        symbol: str,
        exchange: Exchange,
        datetime: datetime,
        interval: str | Interval | None = "1m",
        open_price: float = 0.0,
        high_price: float = 0.0,
        low_price: float = 0.0,
        close_price: float = 0.0,
        volume: float = 0.0,
        gateway_name: str = "OMEGA",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            gateway_name=gateway_name,
            symbol=symbol,
            exchange=exchange,
            datetime=datetime,
            interval=interval,  # type: ignore[arg-type]
            volume=float(volume or 0.0),
            turnover=float(kwargs.get("turnover") or 0.0),
            open_interest=float(kwargs.get("open_interest") or 0.0),
            open_price=float(open_price or 0.0),
            high_price=float(high_price or 0.0),
            low_price=float(low_price or 0.0),
            close_price=float(close_price or 0.0),
        )


class ContractData(VnpyContractData):
    def __init__(
        self,
        symbol: str,
        exchange: Exchange,
        name: str,
        product: Product,
        size: float = 1.0,
        pricetick: float = 0.01,
        currency: str = "USD",
        gateway_name: str = "OMEGA",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            gateway_name=gateway_name,
            symbol=symbol,
            exchange=exchange,
            name=name,
            product=product,
            size=float(size or 1.0),
            pricetick=float(pricetick or 0.01),
            min_volume=float(kwargs.get("min_volume") or 1.0),
            max_volume=kwargs.get("max_volume"),
            stop_supported=bool(kwargs.get("stop_supported") or False),
            net_position=bool(kwargs.get("net_position") or False),
            history_data=bool(kwargs.get("history_data") or False),
            option_strike=kwargs.get("option_strike"),
            option_underlying=kwargs.get("option_underlying"),
            option_type=kwargs.get("option_type"),
            option_listed=kwargs.get("option_listed"),
            option_expiry=kwargs.get("option_expiry"),
            option_portfolio=kwargs.get("option_portfolio"),
            option_index=kwargs.get("option_index"),
        )
        self.currency = currency


class OrderRequest(VnpyOrderRequest):
    def __init__(
        self,
        symbol: str,
        exchange: Exchange,
        direction: Direction,
        volume: float,
        price: float | None = None,
        order_type: str = "LMT",
        reference: str = "",
        product: Product = Product.EQUITY,
        currency: str = "USD",
        **kwargs: Any,
    ) -> None:
        vnpy_type = _order_type(order_type)
        super().__init__(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            type=vnpy_type,
            volume=float(volume),
            price=float(price or 0.0),
            offset=kwargs.get("offset") or Offset.NONE,
            reference=reference,
        )
        self.order_type = order_type
        self.product = product
        self.currency = currency
        self.price = price


class CancelRequest(VnpyCancelRequest):
    def __init__(self, orderid: str, symbol: str = "", exchange: Exchange = Exchange.SMART, **_: Any) -> None:
        super().__init__(orderid=orderid, symbol=symbol, exchange=exchange)


class SubscribeRequest(VnpySubscribeRequest):
    def __init__(self, symbol: str, exchange: Exchange = Exchange.SMART, product: Product = Product.EQUITY, **_: Any) -> None:
        super().__init__(symbol=symbol, exchange=exchange)
        self.product = product


class OrderData(VnpyOrderData):
    def __init__(
        self,
        symbol: str,
        exchange: Exchange,
        orderid: str,
        direction: Direction,
        volume: float,
        price: float | None,
        traded: float = 0.0,
        status: Status = Status.SUBMITTING,
        datetime: datetime | None = None,
        gateway_name: str = "OMEGA",
        broker_order_id: str | None = None,
        reference: str = "",
        risk_result: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            gateway_name=gateway_name,
            symbol=symbol,
            exchange=exchange,
            orderid=orderid,
            type=kwargs.get("type") or _order_type(kwargs.get("order_type") or "LMT"),
            direction=direction,
            offset=kwargs.get("offset") or Offset.NONE,
            price=float(price or 0.0),
            volume=float(volume),
            traded=float(traded or 0.0),
            status=status,
            datetime=datetime,
            reference=reference,
        )
        self.price = price
        self.broker_order_id = broker_order_id
        self.risk_result = risk_result


class TradeData(VnpyTradeData):
    def __init__(
        self,
        symbol: str,
        exchange: Exchange,
        tradeid: str,
        orderid: str,
        direction: Direction,
        volume: float,
        price: float,
        datetime: datetime,
        gateway_name: str = "OMEGA",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            gateway_name=gateway_name,
            symbol=symbol,
            exchange=exchange,
            orderid=orderid,
            tradeid=tradeid,
            direction=direction,
            offset=kwargs.get("offset") or Offset.NONE,
            price=float(price),
            volume=float(volume),
            datetime=datetime,
        )


class PositionData(VnpyPositionData):
    def __init__(
        self,
        symbol: str,
        exchange: Exchange,
        direction: Direction,
        volume: float,
        price: float,
        pnl: float = 0.0,
        gateway_name: str = "OMEGA",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            gateway_name=gateway_name,
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            volume=float(volume),
            frozen=float(kwargs.get("frozen") or 0.0),
            price=float(price or 0.0),
            pnl=float(pnl or 0.0),
            yd_volume=float(kwargs.get("yd_volume") or 0.0),
        )


class AccountData(VnpyAccountData):
    def __init__(
        self,
        accountid: str,
        balance: float,
        frozen: float = 0.0,
        cash: float | None = None,
        buying_power: float | None = None,
        gateway_name: str = "OMEGA",
        **_: Any,
    ) -> None:
        super().__init__(
            gateway_name=gateway_name,
            accountid=accountid,
            balance=float(balance or 0.0),
            frozen=float(frozen or 0.0),
        )
        self.cash = cash
        self.buying_power = buying_power
        self.available = cash if cash is not None else self.balance - self.frozen


def _order_type(value: str | OrderType | None) -> OrderType:
    if isinstance(value, OrderType):
        return value
    text = str(value or "LMT").strip().upper()
    if text in {"MKT", "MARKET"}:
        return OrderType.MARKET
    if text == "STOP":
        return OrderType.STOP
    if text == "FAK":
        return OrderType.FAK
    if text == "FOK":
        return OrderType.FOK
    return OrderType.LIMIT


__all__ = [
    "ACTIVE_STATUSES",
    "AccountData",
    "BarData",
    "CancelRequest",
    "ContractData",
    "Direction",
    "Exchange",
    "Interval",
    "LogData",
    "Offset",
    "OptionType",
    "OrderData",
    "OrderRequest",
    "OrderType",
    "PositionData",
    "Product",
    "Status",
    "SubscribeRequest",
    "TickData",
    "TradeData",
]

