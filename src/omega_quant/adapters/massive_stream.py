from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .rate_limits import massive_rate_limit_snapshot, record_massive_ws_event
from ..config import Settings
from ..utils import finite_float, now_ms


LOGGER = logging.getLogger("omega-quant.market-data")
LOGGER.addHandler(logging.NullHandler())

PROVIDER_RANKING = {
    "massive": {
        "rank": 1,
        "label": "Massive (Polygon)",
        "use": "主实时源：Massive/Polygon 美股 SIP/NBBO、逐笔、期权 OPRA WebSocket。适合交易级盯盘。",
    },
    "polygon": {
        "rank": 1,
        "label": "Massive (Polygon)",
        "use": "Polygon 是 Massive 的旧名；继续兼容旧配置和旧 API key 名称。",
    },
    "alpaca": {
        "rank": 2,
        "label": "Alpaca",
        "use": "备选实时源：有 IEX 免费层和 SIP 付费层；适合已使用 Alpaca 账户的人。",
    },
    "twelvedata": {
        "rank": 3,
        "label": "Twelve Data",
        "use": "多市场数据和展示型行情较方便；交易级美股/期权深度不如专门源。",
    },
    "finnhub": {
        "rank": 4,
        "label": "Finnhub",
        "use": "新闻、基本面、另类数据有用；美股交易级实时盘口不是首选。",
    },
    "yahoo": {
        "rank": 5,
        "label": "Yahoo/yfinance",
        "use": "历史研究兜底；非官方、非交易级实时源。",
    },
}

MarketEventCallback = Callable[[str, str, dict[str, Any]], None]
StatusCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class MarketDataWatchlist:
    stocks: tuple[str, ...]
    options: tuple[str, ...]


def iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def normalize_timestamp_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.endswith("Z"):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return None
    try:
        timestamp = int(float(value))
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000_000:
        return timestamp // 1_000_000
    if timestamp > 10_000_000_000:
        return timestamp
    if timestamp > 0:
        return timestamp * 1000
    return None


def canonical_provider(provider: str | None) -> str:
    value = (provider or "massive").strip().lower()
    if value in {"polygon", "polygon.io", "massive.com"}:
        return "massive"
    return value


def provider_summary(settings: Settings) -> dict[str, Any]:
    raw_provider = settings.resolved_market_data_provider or "massive"
    provider = canonical_provider(raw_provider)
    ranking = PROVIDER_RANKING.get(provider, PROVIDER_RANKING["massive"])
    return {
        "provider": provider,
        "configuredProvider": raw_provider,
        "label": ranking["label"],
        "rank": ranking["rank"],
        "use": ranking["use"],
        "ranking": PROVIDER_RANKING,
    }


def option_display_to_massive_symbol(display: str | None) -> str | None:
    if not display:
        return None
    if display.startswith("O:"):
        return display
    match = re.match(r"^\s*([A-Z.]+)\s+(\d{6}|\d{8})([CP])(\d{8})", display)
    if not match:
        return None
    underlying, expiry, put_call, strike = match.groups()
    if len(expiry) == 8:
        expiry = expiry[2:]
    return f"O:{underlying}{expiry}{put_call}{strike}"


option_display_to_polygon_symbol = option_display_to_massive_symbol


def extract_market_data_watchlist(snapshot: dict[str, Any] | None, settings: Settings) -> MarketDataWatchlist:
    stocks: set[str] = set()
    options: set[str] = set()
    include_options = settings.resolved_market_data_include_options
    for sec_type, positions in ((snapshot or {}).get("positions") or {}).items():
        for position in positions or []:
            contract = position.get("contract") or {}
            symbol = contract.get("symbol")
            if sec_type == "STK" and symbol:
                stocks.add(str(symbol).upper())
            elif sec_type == "OPT":
                if symbol:
                    stocks.add(str(symbol).upper())
                if include_options:
                    massive_symbol = option_display_to_massive_symbol(contract.get("display") or contract.get("identifier"))
                    if massive_symbol:
                        options.add(massive_symbol)
    return MarketDataWatchlist(stocks=tuple(sorted(stocks)), options=tuple(sorted(options)))


class MassiveMarketDataProvider:
    source = "massive"
    source_priority = 90

    def __init__(
        self,
        settings: Settings,
        watchlist: MarketDataWatchlist,
        on_event: MarketEventCallback,
        on_status: StatusCallback,
        stop_event: threading.Event,
        *,
        subscribe_quotes: bool | None = None,
        subscribe_trades: bool | None = None,
    ):
        self.settings = settings
        self.watchlist = watchlist
        self.on_event = on_event
        self.on_status = on_status
        self.stop_event = stop_event
        self.subscribe_quotes = (
            settings.resolved_massive_subscribe_quotes if subscribe_quotes is None else bool(subscribe_quotes)
        )
        self.subscribe_trades = (
            settings.resolved_massive_subscribe_trades if subscribe_trades is None else bool(subscribe_trades)
        )
        self.api_key = settings.massive_api_key
        self._ws_host: str = getattr(
            settings,
            "massive_ws_host",
            getattr(settings, "massive_host", "massiveprivateserver.site"),
        )
        self._ws_secure: bool = bool(getattr(settings, "resolved_massive_secure", True))
        self._status_lock = threading.Lock()
        self._active_asset_classes: set[str] = set()
        self._connected_asset_classes: set[str] = set()

    def run(self) -> None:
        if not self.api_key:
            self.on_status(
                {
                    "status": "missing_key",
                    "permission": "missing_key",
                    "connected": False,
                    "lastError": "MASSIVE_API_KEY is not set.",
                }
            )
            return
        if not self.subscribe_quotes and not self.subscribe_trades:
            self.on_status(
                {
                    "status": "no_channels",
                    "connected": False,
                    "lastError": None,
                    "subscribeQuotes": False,
                    "subscribeTrades": False,
                }
            )
            return
        try:
            import websockets
        except ImportError:
            self.on_status(
                {
                    "status": "missing_dependency",
                    "permission": "missing_dependency",
                    "connected": False,
                    "lastError": "Install websockets to enable Massive streaming.",
                }
            )
            return

        streams: list[tuple[str, tuple[str, ...], str]] = []
        if self.watchlist.stocks:
            streams.append(("stocks", self.watchlist.stocks, "stocks"))
        if self.watchlist.options:
            streams.append(("options", self.watchlist.options, "options"))
        if not streams:
            self.on_status({"status": "idle", "connected": False, "lastError": None})
            return

        self._set_active_asset_classes(asset_class for _, _, asset_class in streams)
        self._emit_status({"status": "starting", "connected": False, "lastError": None})

        threads: list[threading.Thread] = []
        for market_path, symbols, asset_class in streams:
            thread = threading.Thread(
                target=self._run_socket_worker,
                args=(websockets, market_path, symbols, asset_class),
                name=f"massive-{asset_class}-socket",
                daemon=True,
            )
            threads.append(thread)
            thread.start()

        while not self.stop_event.is_set() and any(thread.is_alive() for thread in threads):
            self.stop_event.wait(0.2)

        for thread in threads:
            thread.join(timeout=2.0)

    def _set_active_asset_classes(self, asset_classes: Any) -> None:
        with self._status_lock:
            self._active_asset_classes = set(asset_classes)
            self._connected_asset_classes.clear()

    def _emit_status(self, patch: dict[str, Any], *, asset_class: str | None = None) -> None:
        status_patch = dict(patch)
        status_patch.setdefault("subscribeQuotes", self.subscribe_quotes)
        status_patch.setdefault("subscribeTrades", self.subscribe_trades)
        resolved_asset_class = asset_class or status_patch.get("assetClass")
        if resolved_asset_class:
            status_patch["assetClass"] = resolved_asset_class

        with self._status_lock:
            if "connected" in status_patch:
                if status_patch["connected"] is False and not resolved_asset_class:
                    self._connected_asset_classes.clear()
                elif resolved_asset_class and status_patch["connected"] is True:
                    self._connected_asset_classes.add(str(resolved_asset_class))
                elif resolved_asset_class and status_patch["connected"] is False:
                    self._connected_asset_classes.discard(str(resolved_asset_class))

                connected_assets = sorted(self._connected_asset_classes)
                status_patch["connectedAssets"] = connected_assets
                status_patch["connected"] = bool(connected_assets)

                status_text = str(status_patch.get("status") or "")
                if connected_assets and (
                    status_text in {"missing_permission", "error"}
                    or status_text.startswith("disconnected_")
                ):
                    status_patch["partialStatus"] = status_text
                    status_patch["status"] = "partial"

            if self._active_asset_classes:
                status_patch["activeAssets"] = sorted(self._active_asset_classes)

        self.on_status(status_patch)

    def _run_socket_worker(self, websockets_module: Any, market_path: str, symbols: tuple[str, ...], asset_class: str) -> None:
        if self.stop_event.is_set():
            return
        try:
            self._run_massive_socket(websockets_module, market_path, symbols, asset_class)
        except Exception as exc:
            error_text = str(exc)
            status = "missing_permission" if self._is_permission_error(error_text) else "error"
            self._emit_status(
                {
                    "status": status,
                    "permission": status if status == "missing_permission" else "unknown",
                    "connected": False,
                    "lastError": error_text,
                },
                asset_class=asset_class,
            )
            if status != "missing_permission":
                LOGGER.warning("Massive %s market data stream failed: %s", asset_class, exc)

    def _run_massive_socket(self, websockets_module: Any, market_path: str, symbols: tuple[str, ...], asset_class: str) -> None:
        import asyncio
        import json

        channels = self._channels_for_symbols(
            symbols,
            subscribe_quotes=self.subscribe_quotes,
            subscribe_trades=self.subscribe_trades,
        )
        scheme = "wss" if self._ws_secure else "ws"
        url = f"{scheme}://{self._ws_host}/{market_path}"
        stop = self.stop_event
        handle_fn = self._handle_control_message

        async def run_loop() -> None:
            self._emit_status(
                {"status": f"connecting_{asset_class}", "connected": False, "lastError": None},
                asset_class=asset_class,
            )
            async with websockets_module.connect(url, open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps({"action": "auth", "params": self.api_key}))
                auth_msg = await asyncio.wait_for(ws.recv(), timeout=10)
                auth_errors = handle_fn(
                    auth_msg if isinstance(auth_msg, str) else auth_msg.decode("utf-8", errors="replace"),
                    asset_class=asset_class,
                )
                if auth_errors and any(self._is_permission_error(error) for error in auth_errors):
                    return
                await ws.send(json.dumps({"action": "subscribe", "params": ",".join(channels)}))
                sub_msg = await asyncio.wait_for(ws.recv(), timeout=10)
                handle_fn(
                    sub_msg if isinstance(sub_msg, str) else sub_msg.decode("utf-8", errors="replace"),
                    asset_class=asset_class,
                )
                while not stop.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    handle_fn(
                        msg if isinstance(msg, str) else msg.decode("utf-8", errors="replace"),
                        asset_class=asset_class,
                    )

        try:
            asyncio.run(run_loop())
        finally:
            self._emit_status({"status": f"disconnected_{asset_class}", "connected": False}, asset_class=asset_class)

    @staticmethod
    def _channels_for_symbols(
        symbols: tuple[str, ...],
        *,
        subscribe_quotes: bool = True,
        subscribe_trades: bool = False,
    ) -> list[str]:
        channels: list[str] = []
        for symbol in symbols:
            if subscribe_trades:
                channels.append(f"T.{symbol}")
            if subscribe_quotes:
                channels.append(f"Q.{symbol}")
        return channels

    @staticmethod
    def _is_permission_error(message: str) -> bool:
        text = message.lower()
        return any(token in text for token in ("auth_failed", "permission", "plan", "websocket access", "not entitled"))

    def _status_patch(self, status: str, message: str) -> dict[str, Any]:
        status_lower = status.lower()
        message_lower = message.lower()
        is_error = "error" in status_lower or "fail" in status_lower or "failed" in message_lower
        if is_error and self._is_permission_error(f"{status} {message}"):
            return {
                "status": "missing_permission",
                "permission": "missing_permission",
                "connected": False,
                "lastError": message or status,
            }
        if "delayed" in status_lower or "delayed" in message_lower:
            return {
                "status": "delayed",
                "permission": "delayed",
                "connected": True,
                "lastError": message if is_error else None,
            }
        if status_lower == "auth_success":
            return {"status": "authenticated", "permission": "authenticated", "connected": True, "lastError": None}
        if status_lower == "connected":
            return {"status": "connected", "permission": "checking", "lastError": None}
        return {
            "status": f"massive_{status_lower or 'status'}",
            "permission": "unknown" if is_error else None,
            "connected": not is_error,
            "lastError": message if is_error else None,
        }

    def _handle_control_message(self, message: str, *, asset_class: str | None = None) -> list[str]:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self._emit_status({"lastError": f"non-json websocket payload: {message[:120]}"}, asset_class=asset_class)
            return []
        errors: list[str] = []
        events = payload if isinstance(payload, list) else [payload]
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = event.get("ev")
            if event_type == "status":
                status = str(event.get("status") or "status")
                message = str(event.get("message") or "")
                patch = self._status_patch(status, message)
                self._emit_status(patch, asset_class=asset_class)
                if patch.get("lastError"):
                    errors.append(message or status)
            elif event_type == "T":
                event_asset_class = asset_class or ("options" if str(event.get("sym") or "").startswith("O:") else "stocks")
                record_massive_ws_event(event_asset_class)
                normalized = self._normalize_trade(event, asset_class=event_asset_class)
                normalized["rate_limits"] = massive_rate_limit_snapshot()
                self.on_event("quotes", normalized["symbol"], normalized)
                self.on_event("ticks", normalized["symbol"], normalized)
            elif event_type == "Q":
                event_asset_class = asset_class or ("options" if str(event.get("sym") or "").startswith("O:") else "stocks")
                record_massive_ws_event(event_asset_class)
                quote, bbo = self._normalize_quote(event, asset_class=event_asset_class)
                rate_limits = massive_rate_limit_snapshot()
                quote["rate_limits"] = rate_limits
                bbo["rate_limits"] = rate_limits
                self.on_event("quotes", quote["symbol"], quote)
                self.on_event("bbo", bbo["symbol"], bbo)
        return errors

    def _base_event(self, event: dict[str, Any], *, asset_class: str | None = None) -> dict[str, Any]:
        received_at_ms = now_ms()
        server_ms = normalize_timestamp_ms(event.get("t") or event.get("sip_timestamp"))
        latency = max(0, received_at_ms - server_ms) if server_ms else None
        symbol = str(event.get("sym") or "")
        resolved_asset_class = asset_class or ("options" if symbol.startswith("O:") else "stocks")
        data: dict[str, Any] = {
            "source": self.source,
            "source_priority": self.source_priority,
            "data_quality": "realtime_sip_or_opra",
            "execution_grade": True,
            "permission": "realtime",
            "asset_class": resolved_asset_class,
            "feed": "opra" if resolved_asset_class == "options" else "sip",
            "received_at": iso_from_ms(received_at_ms),
            "received_at_ms": received_at_ms,
            "server_timestamp_ms": server_ms,
            "raw_event_type": event.get("ev"),
        }
        if latency is not None:
            data["latency_ms"] = latency
        return data

    def _normalize_trade(self, event: dict[str, Any], *, asset_class: str | None = None) -> dict[str, Any]:
        symbol = str(event.get("sym") or "")
        data = self._base_event(event, asset_class=asset_class)
        data.update(
            {
                "symbol": symbol,
                "latest_price": finite_float(event.get("p")),
                "last_size": finite_float(event.get("s")),
                "exchange_id": event.get("x"),
                "conditions": event.get("c"),
                "sequence": event.get("q"),
            }
        )
        return data

    def _normalize_quote(self, event: dict[str, Any], *, asset_class: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        symbol = str(event.get("sym") or "")
        bid = finite_float(event.get("bp"))
        ask = finite_float(event.get("ap"))
        midpoint = (bid + ask) / 2 if bid and ask and ask >= bid else None
        spread = (ask - bid) if bid and ask and ask >= bid else None
        spread_pct = spread / midpoint if spread is not None and midpoint else None
        bid_size = finite_float(event.get("bs"))
        ask_size = finite_float(event.get("as"))
        base = self._base_event(event, asset_class=asset_class)
        bbo = {
            **base,
            "symbol": symbol,
            "bid_price": bid,
            "ask_price": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "mid_price": midpoint,
            "spread": spread,
            "spread_pct": spread_pct,
            "bid_exchange_id": event.get("bx"),
            "ask_exchange_id": event.get("ax"),
            "conditions": event.get("c"),
            "sequence": event.get("q"),
        }
        quote = {
            **base,
            "symbol": symbol,
            "latest_price": midpoint,
            "bid_price": bid,
            "ask_price": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "mid_price": midpoint,
            "spread": spread,
            "spread_pct": spread_pct,
            "bbo": {
                "bid_price": bid,
                "ask_price": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "mid_price": midpoint,
                "spread": spread,
                "spread_pct": spread_pct,
            },
        }
        return quote, bbo


PolygonMarketDataStream = MassiveMarketDataProvider


def build_market_data_stream(
    settings: Settings,
    snapshot: dict[str, Any] | None,
    on_event: MarketEventCallback,
    on_status: StatusCallback,
    stop_event: threading.Event,
    *,
    subscribe_quotes: bool | None = None,
    subscribe_trades: bool | None = None,
) -> MassiveMarketDataProvider | None:
    if not settings.resolved_market_data_enabled:
        on_status({"status": "disabled", "connected": False})
        return None
    provider = canonical_provider(settings.resolved_market_data_provider)
    if provider != "massive":
        on_status(
            {
                "status": "unsupported_provider",
                "connected": False,
                "permission": "unsupported",
                "lastError": f"{provider} adapter is not implemented yet; use massive for trading-grade quotes.",
            }
        )
        return None
    watchlist = extract_market_data_watchlist(snapshot, settings)
    return MassiveMarketDataProvider(
        settings,
        watchlist,
        on_event,
        on_status,
        stop_event,
        subscribe_quotes=subscribe_quotes,
        subscribe_trades=subscribe_trades,
    )
