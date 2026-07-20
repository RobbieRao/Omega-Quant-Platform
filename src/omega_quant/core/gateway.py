"""vn.py-style gateway abstraction for Omega."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from omega_quant.vnpy_core.trader.gateway import BaseGateway as VnpyBaseGateway

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
from .objects import (
    AccountData,
    CancelRequest,
    ContractData,
    LogData,
    OrderData,
    OrderRequest,
    PositionData,
    SubscribeRequest,
    TickData,
    TradeData,
)


class GatewayCapability(StrEnum):
    TRADING = "trading"
    MARKET_DATA = "market_data"
    ACCOUNT = "account"
    CONTRACT = "contract"


class BaseGateway(VnpyBaseGateway):
    """Thread-safe, non-Qt gateway base following vn.py's shape.

    ``BaseGateway`` owns event publication and lifecycle only. Domain-specific
    abilities are split into small interfaces below, so Omega can add Tiger,
    Massive, IB, Alpaca, or other gateways without forcing market-data-only
    gateways to implement trading methods.
    """

    default_name = ""
    default_setting: dict[str, str | int | float | bool] = {}
    exchanges: list[str] = []

    def __init__(self, event_engine: EventEngine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)

    def write_log(self, msg: str, level: str = "INFO") -> None:
        self.on_event(EVENT_LOG, LogData(msg=msg, level=level, gateway_name=self.gateway_name))

    @abstractmethod
    def connect(self, setting: dict) -> None:
        pass

    @abstractmethod
    def close(self) -> None:
        pass

    def subscribe(self, req: SubscribeRequest) -> None:
        return

    def send_order(self, req: OrderRequest) -> str:
        return ""

    def cancel_order(self, req: CancelRequest) -> None:
        return

    def query_account(self) -> None:
        return

    def query_position(self) -> None:
        return

    @property
    def capabilities(self) -> set[GatewayCapability]:
        return set()

    def status(self) -> dict[str, Any]:
        return {
            "gateway_name": self.gateway_name,
            "capabilities": sorted(capability.value for capability in self.capabilities),
        }

    def get_default_setting(self) -> dict[str, str | int | float | bool]:
        return self.default_setting


class MarketDataGateway(ABC):
    """行情 gateway：订阅、推送 tick、提供最新快照。"""

    @abstractmethod
    def subscribe(self, req: SubscribeRequest) -> None:
        pass


class TradingGateway(ABC):
    """交易 gateway：提交和撤销订单。"""

    @abstractmethod
    def send_order(self, req: OrderRequest) -> str:
        pass

    @abstractmethod
    def cancel_order(self, req: CancelRequest) -> None:
        pass


class AccountGateway(ABC):
    """账户 gateway：查询资产、持仓、订单。"""

    @abstractmethod
    def query_account(self) -> None:
        pass

    @abstractmethod
    def query_position(self) -> None:
        pass

    def query_order(self) -> None:
        return


class ContractGateway(ABC):
    """合约 gateway：查询标的和期权合约元数据。"""

    def query_contract(self, symbol: str, *, product: str | None = None) -> None:
        return
