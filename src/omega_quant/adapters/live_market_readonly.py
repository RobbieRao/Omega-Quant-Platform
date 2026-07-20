from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable


def _resolved_throttle_ms() -> float:
    raw = os.environ.get("OMEGA_LIVE_TICK_THROTTLE_MS", "250").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 250.0
    return max(0.0, value)

from ..config import Settings
from ..data import ReadOnlyStreamCacheFeed
from ..market_events import MarketEvent, normalize_live_payload
from .massive_stream import MarketDataWatchlist, MassiveMarketDataProvider
from .platform_context import canonical_option_symbol


def _split_symbols(symbols: set[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    stocks = tuple(sorted(symbol for symbol in symbols if not symbol.startswith("O:")))
    options = tuple(sorted(symbol for symbol in symbols if symbol.startswith("O:")))
    return stocks, options


def _normalize_subscription_symbol(symbol: object) -> str | None:
    raw = str(symbol or "").strip().upper()
    if not raw:
        return None
    return canonical_option_symbol(raw) or raw


class LiveMarketReadonlyAdapter:
    def __init__(self, settings: Settings | None = None, cache_feed: ReadOnlyStreamCacheFeed | None = None):
        self.settings = settings or Settings()
        self.cache_feed = cache_feed or ReadOnlyStreamCacheFeed()
        self._lock = threading.RLock()
        self._symbols: set[str] = set()
        self._subscribe_quotes = True
        self._subscribe_trades = False
        self._snapshots: dict[str, MarketEvent] = {}
        self._event_listeners: list[Callable[[MarketEvent], None]] = []
        self._status: dict[str, Any] = {
            "status": "disconnected",
            "connected": False,
            "read_only": True,
            "subscribed_symbols": [],
            "subscribe_quotes": self._subscribe_quotes,
            "subscribe_trades": self._subscribe_trades,
            "last_event_at": None,
            "events_received": 0,
            "message": "Live readonly market feed is disconnected.",
        }
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._running_symbols: set[str] = set()
        self._running_subscribe_quotes = self._subscribe_quotes
        self._running_subscribe_trades = self._subscribe_trades
        self._throttle_ms: float = _resolved_throttle_ms()
        self._last_emit_ms: dict[str, float] = {}
        self._throttled_events: int = 0

    def connect(self, symbols: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        restart_thread: threading.Thread | None = None
        with self._lock:
            if symbols:
                self._symbols.update(_normalized_symbols(symbols))
                self._status["subscribed_symbols"] = sorted(self._symbols)
            if self._thread and self._thread.is_alive():
                if self._running_config_matches_locked():
                    return self.status()
                restart_thread = self._stop_current_locked()
                self._status.update(
                    {
                        "status": "restarting",
                        "connected": False,
                        "message": "Live readonly market feed is restarting with updated subscriptions.",
                    }
                )
        self._join_stopped_thread(restart_thread)
        with self._lock:
            if self._thread and self._thread.is_alive() and self._running_config_matches_locked():
                return self.status()
            stocks, options = _split_symbols(self._symbols)
            if not stocks and not options:
                self._status.update(
                    {
                        "status": "idle",
                        "connected": False,
                        "message": "No live readonly market symbols are subscribed.",
                    }
                )
                return self.status()
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._running_symbols = set(self._symbols)
            self._running_subscribe_quotes = self._subscribe_quotes
            self._running_subscribe_trades = self._subscribe_trades
            provider = MassiveMarketDataProvider(
                self.settings,
                MarketDataWatchlist(stocks=stocks, options=options),
                self._record_event,
                self._record_status,
                stop_event,
                subscribe_quotes=self._subscribe_quotes,
                subscribe_trades=self._subscribe_trades,
            )
            self._thread = threading.Thread(target=provider.run, name="omega-live-readonly-market", daemon=True)
            self._thread.start()
            self._status.update(
                {
                    "status": "starting",
                    "connected": False,
                    "message": "Live readonly market feed is starting.",
                }
            )
            return self.status()

    def disconnect(self) -> dict[str, Any]:
        stopped_thread: threading.Thread | None = None
        with self._lock:
            stopped_thread = self._stop_current_locked()
            self._status.update(
                {
                    "status": "disconnected",
                    "connected": False,
                    "message": "Live readonly market feed is disconnected.",
                }
            )
        self._join_stopped_thread(stopped_thread)
        return self.status()

    def set_channels(self, *, subscribe_quotes: bool, subscribe_trades: bool) -> dict[str, Any]:
        stopped_thread: threading.Thread | None = None
        with self._lock:
            was_running = bool(self._thread and self._thread.is_alive())
            self._subscribe_quotes = bool(subscribe_quotes)
            self._subscribe_trades = bool(subscribe_trades)
            if not self._subscribe_quotes and not self._subscribe_trades:
                stopped_thread = self._stop_current_locked()
                self._status.update(
                    {
                        "status": "no_channels",
                        "connected": False,
                        "message": "No readonly market channels are enabled.",
                    }
                )
                status = self.status()
                should_reconnect = False
            elif was_running:
                stopped_thread = self._stop_current_locked()
                self._status.update(
                    {
                        "status": "restarting",
                        "connected": False,
                        "message": "Live readonly market feed is restarting with updated channels.",
                    }
                )
                status = self.status()
                should_reconnect = True
            else:
                status = self.status()
                should_reconnect = False
        self._join_stopped_thread(stopped_thread)
        if should_reconnect:
            return self.connect()
        return status

    def subscribe(self, symbols: list[str] | tuple[str, ...]) -> dict[str, Any]:
        with self._lock:
            normalized = _normalized_symbols(symbols)
            self._symbols.update(normalized)
            self._status["subscribed_symbols"] = sorted(self._symbols)
            return self.status()

    def unsubscribe(self, symbols: list[str] | tuple[str, ...]) -> dict[str, Any]:
        stopped_thread: threading.Thread | None = None
        should_reconnect = False
        with self._lock:
            normalized = _normalized_symbols(symbols)
            if not normalized:
                return self.status()
            self._symbols.difference_update(normalized)
            for sym in normalized:
                self._snapshots.pop(sym, None)
                self._last_emit_ms.pop(sym, None)
            self._status["subscribed_symbols"] = sorted(self._symbols)
            was_running = bool(self._thread and self._thread.is_alive())
            if was_running:
                stopped_thread = self._stop_current_locked()
                if self._symbols and (self._subscribe_quotes or self._subscribe_trades):
                    self._status.update(
                        {
                            "status": "restarting",
                            "connected": False,
                            "message": "Live readonly market feed is restarting after subscription removal.",
                        }
                    )
                    should_reconnect = True
                else:
                    self._status.update(
                        {
                            "status": "idle",
                            "connected": False,
                            "message": "No live readonly market symbols are subscribed.",
                        }
                    )
            status = self.status()
        self._join_stopped_thread(stopped_thread)
        if should_reconnect:
            return self.connect()
        return status

    def snapshot(self, symbol: str) -> MarketEvent | None:
        normalized_symbol = _normalize_subscription_symbol(symbol) or symbol.upper()
        with self._lock:
            event = self._snapshots.get(normalized_symbol)
        if event:
            return event
        return self.cache_feed.latest(normalized_symbol)

    def add_event_listener(self, listener: Callable[[MarketEvent], None]) -> None:
        with self._lock:
            if listener not in self._event_listeners:
                self._event_listeners.append(listener)

    def remove_event_listener(self, listener: Callable[[MarketEvent], None]) -> None:
        with self._lock:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

    def status(self) -> dict[str, Any]:
        with self._lock:
            payload = dict(self._status)
            payload["subscribed_symbols"] = sorted(self._symbols)
            payload["read_only"] = True
            payload["subscribe_quotes"] = self._subscribe_quotes
            payload["subscribe_trades"] = self._subscribe_trades
            payload.setdefault("throttle_ms", self._throttle_ms)
            payload.setdefault("events_throttled", self._throttled_events)
            return payload

    def _record_status(self, patch: dict[str, Any]) -> None:
        with self._lock:
            self._status.update(patch)
            self._status["read_only"] = True
            self._status["subscribed_symbols"] = sorted(self._symbols)
            self._status["subscribe_quotes"] = self._subscribe_quotes
            self._status["subscribe_trades"] = self._subscribe_trades
            if patch.get("lastError"):
                self._status["message"] = str(patch["lastError"])
            elif patch.get("status"):
                self._status["message"] = f"Live readonly market status: {patch['status']}."
            else:
                self._status.setdefault("message", "Live readonly market status updated.")

    def _record_event(self, _bucket: str, symbol: str, payload: dict[str, Any]) -> None:
        event = normalize_live_event_to_market_event(payload, symbol=symbol)
        if event is None:
            return
        now_ms = time.monotonic() * 1000.0
        with self._lock:
            self._snapshots[event.symbol] = event
            now_iso = datetime.now(timezone.utc).isoformat()
            event_count = int(self._status.get("events_received") or 0) + 1
            should_emit = True
            if self._throttle_ms > 0:
                last_ms = self._last_emit_ms.get(event.symbol)
                if last_ms is not None and (now_ms - last_ms) < self._throttle_ms:
                    should_emit = False
                    self._throttled_events += 1
            if should_emit:
                self._last_emit_ms[event.symbol] = now_ms
            self._status.update(
                {
                    "status": "realtime",
                    "connected": True,
                    "last_event_at": now_iso,
                    "events_received": event_count,
                    "events_throttled": self._throttled_events,
                    "throttle_ms": self._throttle_ms,
                    "subscribe_quotes": self._subscribe_quotes,
                    "subscribe_trades": self._subscribe_trades,
                    "message": "Live readonly market event received.",
                }
            )
            listeners = list(self._event_listeners) if should_emit else []
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                continue

    def _running_config_matches_locked(self) -> bool:
        return (
            self._running_symbols == self._symbols
            and self._running_subscribe_quotes == self._subscribe_quotes
            and self._running_subscribe_trades == self._subscribe_trades
        )

    def _stop_current_locked(self) -> threading.Thread | None:
        thread = self._thread
        if self._stop_event:
            self._stop_event.set()
        self._thread = None
        self._stop_event = None
        self._running_symbols = set()
        return thread

    @staticmethod
    def _join_stopped_thread(thread: threading.Thread | None) -> None:
        if thread and thread.is_alive():
            thread.join(timeout=3.0)


_DEFAULT_ADAPTER = LiveMarketReadonlyAdapter()


def _normalized_symbols(symbols: list[str] | tuple[str, ...]) -> set[str]:
    return {
        normalized_symbol
        for symbol in symbols
        if (normalized_symbol := _normalize_subscription_symbol(symbol)) is not None
    }


def normalize_live_event_to_market_event(payload: dict[str, Any], symbol: str | None = None) -> MarketEvent | None:
    return normalize_live_payload(payload, symbol=symbol)


def connect_live_market_feed(symbols: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    return _DEFAULT_ADAPTER.connect(symbols)


def disconnect_live_market_feed() -> dict[str, Any]:
    return _DEFAULT_ADAPTER.disconnect()


def get_live_market_snapshot(symbol: str) -> MarketEvent | None:
    return _DEFAULT_ADAPTER.snapshot(symbol)


def subscribe_symbols_readonly(symbols: list[str] | tuple[str, ...]) -> dict[str, Any]:
    return _DEFAULT_ADAPTER.subscribe(symbols)


def unsubscribe_symbols_readonly(symbols: list[str] | tuple[str, ...]) -> dict[str, Any]:
    return _DEFAULT_ADAPTER.unsubscribe(symbols)


def set_live_market_channels_readonly(*, subscribe_quotes: bool, subscribe_trades: bool) -> dict[str, Any]:
    return _DEFAULT_ADAPTER.set_channels(subscribe_quotes=subscribe_quotes, subscribe_trades=subscribe_trades)


def live_market_status() -> dict[str, Any]:
    return _DEFAULT_ADAPTER.status()
