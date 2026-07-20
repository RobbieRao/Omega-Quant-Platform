"""Deterministic, credential-free market data used only by Omega demo mode."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ..market_events import MarketEvent, new_market_event


_PRICES = {
    "AAPL": 214.25,
    "MSFT": 486.40,
    "NVDA": 181.75,
    "SPY": 642.10,
    "QQQ": 574.35,
}


class SyntheticMarketDataAdapter:
    """Small adapter with the same read-only surface as the Massive adapter."""

    def __init__(self) -> None:
        self._symbols: set[str] = {"AAPL", "MSFT", "NVDA", "SPY", "QQQ"}
        self._listeners: list[Callable[[MarketEvent], None]] = []
        self._connected = True

    def connect(self, symbols: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        if symbols:
            self._symbols.update(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip())
        self._connected = True
        return self.status()

    def disconnect(self) -> dict[str, Any]:
        self._connected = False
        return self.status()

    def subscribe(self, symbols: list[str] | tuple[str, ...]) -> dict[str, Any]:
        self._symbols.update(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip())
        return self.status()

    def unsubscribe(self, symbols: list[str] | tuple[str, ...]) -> dict[str, Any]:
        self._symbols.difference_update(str(symbol).strip().upper() for symbol in symbols)
        return self.status()

    def set_channels(self, *, subscribe_quotes: bool, subscribe_trades: bool) -> dict[str, Any]:
        del subscribe_quotes, subscribe_trades
        return self.status()

    def snapshot(self, symbol: str) -> MarketEvent:
        resolved = str(symbol or "AAPL").strip().upper()
        self._symbols.add(resolved)
        price = _PRICES.get(resolved, 100.0 + (sum(ord(char) for char in resolved) % 5000) / 100)
        now = datetime.now(timezone.utc)
        event = new_market_event(
            source="live_readonly",
            symbol=resolved,
            timestamp=now,
            received_at=now,
            price=price,
            bid=round(price - 0.05, 2),
            ask=round(price + 0.05, 2),
            last=price,
            volume=1_000_000,
            data_quality="synthetic_demo",
            raw={"demo": True, "dataset": "omega-synthetic-v1"},
        )
        for listener in tuple(self._listeners):
            listener(event)
        return event

    def add_event_listener(self, listener: Callable[[MarketEvent], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_event_listener(self, listener: Callable[[MarketEvent], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def status(self) -> dict[str, Any]:
        return {
            "status": "demo",
            "connected": self._connected,
            "read_only": True,
            "demo": True,
            "subscribed_symbols": sorted(self._symbols),
            "subscribe_quotes": True,
            "subscribe_trades": True,
            "events_received": 0,
            "events_throttled": 0,
            "last_event_at": datetime.now(timezone.utc).isoformat(),
            "message": "Credential-free deterministic synthetic market data.",
        }
