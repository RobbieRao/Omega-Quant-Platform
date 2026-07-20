"""Massive market-data gateway for Omega MainEngine.

This wraps Omega's Massive websocket provider instead of
reimplementing market-data transport. The gateway exposes only market-data
capability and publishes normalized ticks into the same EventEngine used by
TigerOpen trading/account gateways.
"""

from __future__ import annotations

from typing import Any

from ..adapters.live_market_readonly import LiveMarketReadonlyAdapter
from ..core.gateway import BaseGateway, GatewayCapability, MarketDataGateway
from ..core.objects import Exchange, SubscribeRequest, TickData
from ..market_events import MarketEvent


class MassiveMarketDataGateway(BaseGateway, MarketDataGateway):
    default_name = "MASSIVE"
    default_setting = {
        "base_url": "massiveprivateserver.site",
        "products": "us_stocks,us_options",
        "channels": "Q,T",
    }
    exchanges = [Exchange.SMART.value, Exchange.NASDAQ.value, Exchange.NYSE.value, Exchange.ARCA.value, Exchange.CBOE.value]

    def __init__(self, event_engine, gateway_name: str) -> None:
        super().__init__(event_engine, gateway_name)
        self.adapter: LiveMarketReadonlyAdapter = LiveMarketReadonlyAdapter()
        self.connected = False
        self.subscribed_symbols: set[str] = set()

    @property
    def capabilities(self) -> set[GatewayCapability]:
        return {GatewayCapability.MARKET_DATA}

    def connect(self, setting: dict) -> None:
        adapter = setting.get("adapter")
        if adapter is not None:
            self.adapter = adapter
        self.adapter.add_event_listener(self._on_market_event)
        symbols = setting.get("symbols") or []
        if symbols and hasattr(self.adapter, "connect"):
            self.subscribed_symbols.update(str(symbol).upper() for symbol in symbols)
            self.adapter.connect(list(self.subscribed_symbols))
        elif hasattr(self.adapter, "connect"):
            self.adapter.connect()
        self.connected = True
        self.write_log("Massive market-data gateway connected.")

    def close(self) -> None:
        try:
            if hasattr(self.adapter, "remove_event_listener"):
                self.adapter.remove_event_listener(self._on_market_event)
            if hasattr(self.adapter, "disconnect"):
                self.adapter.disconnect()
        finally:
            self.connected = False
            self.write_log("Massive market-data gateway closed.")

    def subscribe(self, req: SubscribeRequest) -> None:
        symbol = req.symbol.upper()
        self.subscribed_symbols.add(symbol)
        self.adapter.subscribe([symbol])
        if self.connected:
            self.adapter.connect()
        self.write_log(f"Massive subscribed: {symbol}")

    def snapshot(self, symbol: str) -> MarketEvent | None:
        return self.adapter.snapshot(symbol)

    def set_channels(self, *, subscribe_quotes: bool, subscribe_trades: bool) -> dict[str, Any]:
        return self.adapter.set_channels(subscribe_quotes=subscribe_quotes, subscribe_trades=subscribe_trades)

    def status(self) -> dict[str, Any]:
        payload = super().status()
        payload.update(
            {
                "connected": self.connected,
                "subscribed_symbols": sorted(self.subscribed_symbols),
                "provider_status": self.adapter.status(),
            }
        )
        return payload

    def _on_market_event(self, event: MarketEvent) -> None:
        tick = TickData(
            symbol=event.symbol.upper(),
            exchange=Exchange.SMART,
            datetime=event.timestamp,
            last_price=float(event.price),
            volume=float(event.volume or 0.0),
            bid_price_1=event.bid,
            ask_price_1=event.ask,
            gateway_name=self.gateway_name,
            raw=dict(event.raw or {}),
        )
        self.on_tick(tick)
