from __future__ import annotations

import atexit
import copy
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters.massive_stream import build_market_data_stream, option_display_to_massive_symbol, provider_summary
from ..adapters.rate_limits import massive_rate_limit_snapshot
from ..config import Settings, load_env_file, mask_account
from ..utils import finite_float, now_ms


LOGGER = logging.getLogger("omega-quant.account-stream")
LOGGER.addHandler(logging.NullHandler())

_STREAMERS: dict[Path, "TigerPushStreamer"] = {}
_STREAMERS_LOCK = threading.Lock()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def camel_to_snake(name: str) -> str:
    first = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", first).lower()


def sanitize_value(value: Any, settings: Settings) -> Any:
    if isinstance(value, dict):
        return {
            camel_to_snake(str(key)): sanitize_value(val, settings)
            for key, val in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_value(item, settings) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, float):
        return finite_float(value)
    return value


def frame_to_dict(frame: Any, settings: Settings) -> dict[str, Any]:
    try:
        from google.protobuf.json_format import MessageToDict

        if hasattr(frame, "DESCRIPTOR"):
            try:
                data = MessageToDict(
                    frame,
                    preserving_proto_field_name=False,
                    including_default_value_fields=False,
                )
            except TypeError:
                data = MessageToDict(frame, preserving_proto_field_name=False)
            return sanitize_value(data, settings)
    except Exception:
        pass

    raw = getattr(frame, "__dict__", None)
    if isinstance(raw, dict):
        return sanitize_value(raw, settings)

    fields: dict[str, Any] = {}
    for name in dir(frame):
        if name.startswith("_"):
            continue
        try:
            value = getattr(frame, name)
        except Exception:
            continue
        if callable(value):
            continue
        if isinstance(value, (str, int, float, bool, list, tuple, dict, type(None))):
            fields[name] = value
    return sanitize_value(fields, settings)


def server_timestamp_ms(data: dict[str, Any]) -> int | None:
    for key in (
        "server_timestamp",
        "serverTimestamp",
        "timestamp",
        "latest_price_timestamp",
        "latestPriceTimestamp",
        "ask_timestamp",
        "bid_timestamp",
    ):
        value = data.get(key)
        if value is None:
            continue
        try:
            timestamp = int(float(value))
        except (TypeError, ValueError):
            continue
        if timestamp > 10_000_000_000:
            return timestamp
        if timestamp > 0:
            return timestamp * 1000
    return None


def data_age_seconds(received_at_ms: int | None) -> float | None:
    if not received_at_ms:
        return None
    return max(0.0, (now_ms() - received_at_ms) / 1000)


def data_latency_ms(data: dict[str, Any], received_at_ms: int) -> int | None:
    server_ms = server_timestamp_ms(data)
    if not server_ms:
        return None
    return max(0, received_at_ms - server_ms)


def stream_status(age: float | None, settings: Settings) -> str:
    if age is None:
        return "missing"
    if age <= settings.stream_stale_after_seconds:
        return "live"
    if age <= settings.stream_hard_stale_after_seconds:
        return "stale"
    return "dead"


def contract_symbols(position: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    contract = position.get("contract") or {}
    return (
        contract.get("symbol"),
        contract.get("identifier"),
        contract.get("display"),
    )


def extract_watch_symbols(snapshot: dict[str, Any] | None) -> dict[str, list[str]]:
    stocks: set[str] = set()
    option_underlyings: set[str] = set()
    option_symbols: set[str] = set()
    futures: set[str] = set()
    for sec_type, positions in ((snapshot or {}).get("positions") or {}).items():
        for position in positions or []:
            symbol, identifier, display = contract_symbols(position)
            if sec_type == "STK" and symbol:
                stocks.add(str(symbol))
            elif sec_type == "OPT":
                if symbol:
                    option_underlyings.add(str(symbol))
                if identifier:
                    option_symbols.add(str(identifier))
                elif display:
                    option_symbols.add(str(display))
            elif sec_type == "FUT" and symbol:
                futures.add(str(symbol))
    return {
        "stocks": sorted(stocks),
        "option_underlyings": sorted(option_underlyings),
        "options": sorted(option_symbols),
        "futures": sorted(futures),
        "quotes": sorted(stocks | option_underlyings),
    }


def position_match_keys(position: dict[str, Any]) -> set[str]:
    symbol, identifier, display = contract_symbols(position)
    values = {str(value) for value in (symbol, identifier, display) if value}
    massive_symbol = option_display_to_massive_symbol(display or identifier)
    if massive_symbol:
        values.add(massive_symbol)
    return values


def ordered_position_match_keys(
    position: dict[str, Any],
    *,
    sec_type: str | None = None,
    include_underlying_symbol: bool = True,
) -> list[str]:
    symbol, identifier, display = contract_symbols(position)
    normalized_sec_type = str(sec_type or (position.get("contract") or {}).get("sec_type") or "").upper()
    values: list[str] = []
    massive_symbol = option_display_to_massive_symbol(display or identifier)
    if massive_symbol:
        values.append(massive_symbol)
    if identifier:
        values.append(str(identifier))
    if display:
        values.append(str(display))
    if symbol and include_underlying_symbol and normalized_sec_type != "OPT":
        values.append(str(symbol))
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def position_quote_keys(position: dict[str, Any], sec_type: str) -> list[str]:
    return ordered_position_match_keys(
        position,
        sec_type=sec_type,
        include_underlying_symbol=str(sec_type).upper() != "OPT",
    )


def quote_latest_price(quote: dict[str, Any] | None) -> float | None:
    if not quote:
        return None
    for key in ("latest_price", "latestPrice", "price", "last_price", "lastPrice"):
        value = finite_float(quote.get(key))
        if value is not None and value > 0:
            return value
    bbo = quote.get("bbo") if isinstance(quote.get("bbo"), dict) else None
    if bbo:
        bid = finite_float(bbo.get("bid_price"))
        ask = finite_float(bbo.get("ask_price"))
        if bid and ask:
            return (bid + ask) / 2
    return None


def quote_symbol(data: dict[str, Any]) -> str | None:
    value = data.get("symbol") or data.get("identifier")
    return str(value) if value else None


def annotate_event(data: dict[str, Any], received_at_ms: int) -> dict[str, Any]:
    event = dict(data)
    event["received_at"] = datetime.fromtimestamp(received_at_ms / 1000, timezone.utc).isoformat()
    event["received_at_ms"] = received_at_ms
    latency = data_latency_ms(data, received_at_ms)
    if latency is not None:
        event["latency_ms"] = latency
    return event


class TigerPushStreamer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache_path = settings.stream_cache_path
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._market_data_thread: threading.Thread | None = None
        self._market_data_stop_event = threading.Event()
        self._market_data_key: tuple[Any, ...] | None = None
        self._market_data_generation = 0
        self._market_data_channels = {
            "quotes": bool(settings.resolved_massive_subscribe_quotes),
            "trades": bool(settings.resolved_massive_subscribe_trades),
        }
        self._sse_clients = 0
        self._last_sse_disconnect_ms: int | None = now_ms()
        self._client: Any | None = None
        self._sequence = 0
        self._subscriptions: dict[str, list[str]] = {
            "stocks": [],
            "option_underlyings": [],
            "options": [],
            "futures": [],
            "quotes": [],
        }
        self._subscribed_once = False
        self._state: dict[str, Any] = {
            "enabled": settings.stream_enabled,
            "status": "idle",
            "connected": False,
            "startedAt": None,
            "connectedAt": None,
            "lastEventAt": None,
            "lastEventAtMs": None,
            "lastHeartbeatAt": None,
            "lastError": None,
            "account": "",
            "subscriptions": copy.deepcopy(self._subscriptions),
            "quotes": {},
            "bbo": {},
            "depth": {},
            "ticks": {},
            "assets": {},
            "positions": {},
            "orders": {},
            "transactions": {},
            "marketData": {
                "enabled": settings.resolved_market_data_enabled,
                "connected": False,
                "status": "idle",
                "permission": "unknown",
                "lastEventAt": None,
                "lastEventAtMs": None,
                "lastError": None,
                "subscribeQuotes": self._market_data_channels["quotes"],
                "subscribeTrades": self._market_data_channels["trades"],
                "viewerCount": 0,
                "pauseWithoutViewersSeconds": settings.resolved_market_data_pause_without_sse_seconds,
                "rateLimits": massive_rate_limit_snapshot(),
                **provider_summary(settings),
            },
            "events": [],
            "sequence": self._sequence,
        }

    def start(self, snapshot: dict[str, Any] | None = None) -> None:
        if not self.settings.stream_enabled:
            self._set_status("disabled")
            return
        subscriptions = extract_watch_symbols(snapshot)
        with self._lock:
            changed = subscriptions != self._subscriptions
            self._subscriptions = subscriptions
            self._state["subscriptions"] = copy.deepcopy(subscriptions)
            already_running = self._thread is not None and self._thread.is_alive()
            if changed:
                self._touch_locked()
        self._ensure_market_data_stream(snapshot, subscriptions)
        if already_running and changed:
            self._subscribe_current_watchlist()
        if already_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="tiger-push-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        client = self._client
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                LOGGER.debug("Tiger push disconnect failed", exc_info=True)
        with self._lock:
            self._market_data_generation += 1
            self._market_data_stop_event.set()
        self._set_status("stopped", connected=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = copy.deepcopy(self._state)
        age = data_age_seconds(state.get("lastEventAtMs"))
        state["ageSeconds"] = age
        state["freshness"] = stream_status(age, self.settings)
        market_data = state.get("marketData") if isinstance(state.get("marketData"), dict) else None
        if market_data:
            market_data["rateLimits"] = massive_rate_limit_snapshot()
            market_data["subscribeQuotes"] = self._market_data_channels["quotes"]
            market_data["subscribeTrades"] = self._market_data_channels["trades"]
            market_data["viewerCount"] = self._sse_clients
            market_data["pauseWithoutViewersSeconds"] = self.settings.resolved_market_data_pause_without_sse_seconds
            market_age = data_age_seconds(market_data.get("lastEventAtMs"))
            market_data["ageSeconds"] = market_age
            if market_age is not None:
                market_data["freshness"] = stream_status(market_age, self.settings)
                if market_age > self.settings.stream_hard_stale_after_seconds and market_data.get("connected"):
                    market_data["status"] = "stale"
                    market_data["connected"] = False
        state["cachePath"] = str(self.cache_path.resolve())
        return state

    def register_sse_client(self) -> None:
        with self._lock:
            self._sse_clients += 1
            self._last_sse_disconnect_ms = None
            market_data = self._state.setdefault("marketData", {})
            market_data["viewerCount"] = self._sse_clients
            market_data["pausedByNoViewers"] = False
            self._touch_locked()

    def unregister_sse_client(self) -> None:
        with self._lock:
            self._sse_clients = max(0, self._sse_clients - 1)
            if self._sse_clients == 0:
                self._last_sse_disconnect_ms = now_ms()
            market_data = self._state.setdefault("marketData", {})
            market_data["viewerCount"] = self._sse_clients
            self._touch_locked()

    def set_market_data_channels(
        self,
        *,
        subscribe_quotes: bool,
        subscribe_trades: bool,
        snapshot: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._market_data_channels = {
                "quotes": bool(subscribe_quotes),
                "trades": bool(subscribe_trades),
            }
            subscriptions = copy.deepcopy(self._subscriptions)
            market_data = self._state.setdefault("marketData", {})
            market_data["subscribeQuotes"] = self._market_data_channels["quotes"]
            market_data["subscribeTrades"] = self._market_data_channels["trades"]
            self._touch_locked()
        self._ensure_market_data_stream(snapshot, subscriptions)

    def wait_for_update(self, last_sequence: int | None, timeout: float | None) -> bool:
        with self._condition:
            if last_sequence is None or self._sequence != last_sequence:
                return True
            self._condition.wait(timeout)
            return self._sequence != last_sequence

    def _set_status(
        self,
        status: str,
        *,
        connected: bool | None = None,
        error: str | None = None,
        account: str | None = None,
    ) -> None:
        with self._lock:
            self._state["status"] = status
            if connected is not None:
                self._state["connected"] = connected
                if connected:
                    self._state["connectedAt"] = iso_now()
            if error is not None:
                self._state["lastError"] = error
            if account is not None:
                self._state["account"] = account
            self._touch_locked()
        self._flush_cache()

    def _run(self) -> None:
        from tigeropen.push.push_client import PushClient
        from tigeropen.tiger_open_config import TigerOpenClientConfig

        with self._lock:
            self._state["startedAt"] = iso_now()
        while not self._stop_event.is_set():
            try:
                self._set_status("connecting", connected=False)
                load_env_file(self.settings.tiger_env_path)
                config = TigerOpenClientConfig()
                protocol, host, port = config.socket_host_port
                account = mask_account(
                    config.account,
                    self.settings.account_mask_prefix,
                    self.settings.account_mask_suffix,
                )
                client = PushClient(host, port, use_ssl=(protocol == "ssl"), client_config=config)
                self._client = client
                self._install_callbacks(client)
                client.connect(config.tiger_id, config.private_key)
                self._set_status("connected", connected=True, account=account)
                self._subscribe_current_watchlist()
                flush_interval = max(0.05, float(self.settings.stream_cache_flush_seconds))
                while not self._stop_event.wait(flush_interval):
                    self._maybe_pause_market_data_for_idle_sse()
                    self._flush_cache()
            except Exception as exc:  # pragma: no cover - depends on network and Tiger socket
                self._set_status("error", connected=False, error=str(exc))
                LOGGER.warning("Tiger push stream failed: %s", exc)
                if self._stop_event.wait(5):
                    break

    def _install_callbacks(self, client: Any) -> None:
        client.connect_callback = lambda frame: self._set_status("connected", connected=True)
        client.disconnect_callback = lambda: self._set_status("disconnected", connected=False)
        client.error_callback = lambda frame: self._set_status(
            "error",
            error=str(frame_to_dict(frame, self.settings) or frame),
        )
        client.heartbeat_callback = lambda frame: self._record_heartbeat(frame)
        client.quote_changed = lambda frame: self._record_market_event("quotes", frame)
        client.quote_bbo_changed = lambda frame: self._record_market_event("bbo", frame)
        client.quote_depth_changed = lambda frame: self._record_market_event("depth", frame)
        client.tick_changed = lambda frame: self._record_market_event("ticks", frame)
        client.asset_changed = lambda frame: self._record_trade_event("assets", frame, key_field="currency")
        client.position_changed = lambda frame: self._record_trade_event("positions", frame, key_field="identifier")
        client.order_changed = lambda frame: self._record_trade_event("orders", frame, key_field="id")
        client.transaction_changed = lambda frame: self._record_trade_event("transactions", frame, key_field="id")
        client.subscribe_callback = lambda frame: self._append_event("subscribe", frame_to_dict(frame, self.settings))
        client.unsubscribe_callback = lambda frame: self._append_event("unsubscribe", frame_to_dict(frame, self.settings))

    def _subscribe_current_watchlist(self) -> None:
        client = self._client
        if client is None:
            return
        with self._lock:
            subscriptions = copy.deepcopy(self._subscriptions)
        try:
            client.subscribe_asset()
            client.subscribe_position()
            client.subscribe_order()
            client.subscribe_transaction()
            quotes = subscriptions.get("quotes") or []
            if quotes:
                client.subscribe_quote(quotes)
                client.subscribe_tick(quotes)
                client.subscribe_depth_quote(quotes)
            options = subscriptions.get("options") or []
            if options:
                client.subscribe_option(options)
            futures = subscriptions.get("futures") or []
            if futures:
                client.subscribe_future(futures)
            with self._lock:
                self._subscribed_once = True
                self._touch_locked()
        except Exception as exc:  # pragma: no cover - depends on quote permissions
            self._set_status("partial", error=f"subscribe failed: {exc}")

    def _record_heartbeat(self, frame: Any) -> None:
        del frame
        with self._lock:
            self._state["lastHeartbeatAt"] = iso_now()
            self._touch_locked()

    def _record_market_event(self, bucket: str, frame: Any) -> None:
        received_at_ms = now_ms()
        data = annotate_event(frame_to_dict(frame, self.settings), received_at_ms)
        data.setdefault("source", "tiger")
        data.setdefault("source_priority", 60)
        data.setdefault("data_quality", "tiger_push")
        symbol = quote_symbol(data) or f"event-{received_at_ms}"
        with self._lock:
            if bucket == "quotes":
                price = quote_latest_price(data)
                if price is not None:
                    data["latest_price"] = price
            self._store_market_bucket_locked(bucket, symbol, data)
            self._mark_event_locked(received_at_ms)
            self._append_event_locked(bucket, {"symbol": symbol, "received_at_ms": received_at_ms})
            self._touch_locked()

    def _record_external_market_event(
        self,
        bucket: str,
        symbol: str,
        data: dict[str, Any],
        *,
        generation: int | None = None,
    ) -> None:
        received_at_ms = int(data.get("received_at_ms") or now_ms())
        source = str(data.get("source") or "external")
        event_kind = "trade" if data.get("raw_event_type") == "T" else "quote" if data.get("raw_event_type") == "Q" else bucket
        with self._lock:
            if generation is not None and generation != self._market_data_generation:
                return
            self._store_market_bucket_locked(bucket, symbol, data)
            market_data = self._state.setdefault("marketData", {})
            market_data["lastEventAtMs"] = received_at_ms
            market_data["lastEventAt"] = datetime.fromtimestamp(received_at_ms / 1000, timezone.utc).isoformat()
            market_data["connected"] = True
            market_data["subscribeQuotes"] = self._market_data_channels["quotes"]
            market_data["subscribeTrades"] = self._market_data_channels["trades"]
            market_data["viewerCount"] = self._sse_clients
            market_data["pauseWithoutViewersSeconds"] = self.settings.resolved_market_data_pause_without_sse_seconds
            market_data["rateLimits"] = data.get("rate_limits") or massive_rate_limit_snapshot()
            if not str(market_data.get("status") or "").startswith(("connected", "realtime", "authenticated")):
                market_data["status"] = "connected"
            self._mark_event_locked(received_at_ms)
            self._append_event_locked(
                f"{source}_{event_kind}",
                {"symbol": symbol, "received_at_ms": received_at_ms},
            )
            self._touch_locked()

    def _store_market_bucket_locked(self, bucket: str, symbol: str, data: dict[str, Any]) -> None:
        bucket_data = self._state.setdefault(bucket, {})
        existing = bucket_data.get(symbol)
        if not isinstance(existing, dict) or self._should_replace_market_event(existing, data):
            bucket_data[symbol] = data

    def _should_replace_market_event(self, existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
        existing_priority = finite_float(existing.get("source_priority")) or 0
        incoming_priority = finite_float(incoming.get("source_priority")) or 0
        if incoming_priority > existing_priority:
            return True
        if incoming_priority < existing_priority:
            age = data_age_seconds(existing.get("received_at_ms"))
            return age is not None and age > self.settings.stream_hard_stale_after_seconds
        existing_time = int(existing.get("received_at_ms") or 0)
        incoming_time = int(incoming.get("received_at_ms") or 0)
        return incoming_time >= existing_time

    def _set_market_data_status(self, patch: dict[str, Any], *, generation: int | None = None) -> None:
        with self._lock:
            if generation is not None and generation != self._market_data_generation:
                return
            market_data = self._state.setdefault("marketData", {})
            market_data.update(provider_summary(self.settings))
            market_data["enabled"] = self.settings.resolved_market_data_enabled
            market_data["subscribeQuotes"] = self._market_data_channels["quotes"]
            market_data["subscribeTrades"] = self._market_data_channels["trades"]
            market_data["viewerCount"] = self._sse_clients
            market_data["pauseWithoutViewersSeconds"] = self.settings.resolved_market_data_pause_without_sse_seconds
            market_data["rateLimits"] = massive_rate_limit_snapshot()
            asset_class = patch.get("assetClass")
            if asset_class:
                asset_status = market_data.setdefault("assetStatus", {})
                current = asset_status.setdefault(str(asset_class), {})
                for key, value in patch.items():
                    if key == "assetStatus":
                        continue
                    if value is not None or key == "lastError":
                        current[key] = value
            for key, value in patch.items():
                if value is not None or key == "lastError":
                    market_data[key] = value
            if patch.get("connected") is True:
                market_data["connectedAt"] = iso_now()
            self._touch_locked()
        self._flush_cache()

    def _should_pause_market_data_for_idle_sse_locked(self, received_at_ms: int | None = None) -> bool:
        pause_seconds = self.settings.resolved_market_data_pause_without_sse_seconds
        if pause_seconds <= 0 or self._sse_clients > 0 or self._last_sse_disconnect_ms is None:
            return False
        now_value = received_at_ms or now_ms()
        return now_value - self._last_sse_disconnect_ms >= pause_seconds * 1000

    def _mark_market_data_paused_no_viewers_locked(self) -> None:
        paused_at_ms = now_ms()
        market_data = self._state.setdefault("marketData", {})
        market_data.update(provider_summary(self.settings))
        market_data.update(
            {
                "enabled": self.settings.resolved_market_data_enabled,
                "connected": False,
                "status": "paused_no_viewers",
                "permission": market_data.get("permission") or "unknown",
                "lastError": None,
                "connectedAssets": [],
                "activeAssets": [],
                "viewerCount": self._sse_clients,
                "pausedByNoViewers": True,
                "pausedAtMs": paused_at_ms,
                "pausedAt": datetime.fromtimestamp(paused_at_ms / 1000, timezone.utc).isoformat(),
                "pauseWithoutViewersSeconds": self.settings.resolved_market_data_pause_without_sse_seconds,
                "subscribeQuotes": self._market_data_channels["quotes"],
                "subscribeTrades": self._market_data_channels["trades"],
                "rateLimits": massive_rate_limit_snapshot(),
            }
        )

    def _maybe_pause_market_data_for_idle_sse(self) -> None:
        old_thread: threading.Thread | None = None
        with self._lock:
            running = self._market_data_thread is not None and self._market_data_thread.is_alive()
            if not running or not self._should_pause_market_data_for_idle_sse_locked():
                return
            self._market_data_generation += 1
            self._market_data_stop_event.set()
            old_thread = self._market_data_thread
            self._market_data_thread = None
            self._market_data_key = None
            self._mark_market_data_paused_no_viewers_locked()
            self._touch_locked()
        if old_thread is not None:
            old_thread.join(timeout=1.0)
        self._flush_cache()

    def _ensure_market_data_stream(self, snapshot: dict[str, Any] | None, subscriptions: dict[str, list[str]]) -> None:
        provider = self.settings.resolved_market_data_provider or "polygon"
        include_options = self.settings.resolved_market_data_include_options
        key = (
            self.settings.resolved_market_data_enabled,
            provider,
            include_options,
            self._market_data_channels["quotes"],
            self._market_data_channels["trades"],
            tuple(subscriptions.get("quotes") or []),
            tuple(subscriptions.get("options") or []),
        )
        old_thread: threading.Thread | None = None
        with self._lock:
            if self._should_pause_market_data_for_idle_sse_locked():
                self._mark_market_data_paused_no_viewers_locked()
                self._touch_locked()
                return
            running = self._market_data_thread is not None and self._market_data_thread.is_alive()
            if running and key == self._market_data_key:
                return
            if running:
                self._market_data_stop_event.set()
                old_thread = self._market_data_thread
            self._market_data_stop_event = threading.Event()
            self._market_data_key = key
            self._market_data_generation += 1
            generation = self._market_data_generation
            self._state["marketData"].update(
                {
                    "enabled": self.settings.resolved_market_data_enabled,
                    "connected": False,
                    "status": "starting",
                    "permission": "checking",
                    "lastError": None,
                    "connectedAssets": [],
                    "activeAssets": [],
                    "assetStatus": {},
                    "subscribeQuotes": self._market_data_channels["quotes"],
                    "subscribeTrades": self._market_data_channels["trades"],
                    "viewerCount": self._sse_clients,
                    "pausedByNoViewers": False,
                    "pauseWithoutViewersSeconds": self.settings.resolved_market_data_pause_without_sse_seconds,
                    "rateLimits": massive_rate_limit_snapshot(),
                    **provider_summary(self.settings),
                }
            )
            self._touch_locked()

        if old_thread is not None:
            old_thread.join(timeout=1.0)

        try:
            load_env_file(self.settings.tiger_env_path)
        except FileNotFoundError:
            pass
        stream = build_market_data_stream(
            self.settings,
            snapshot,
            lambda bucket, symbol, data: self._record_external_market_event(
                bucket,
                symbol,
                data,
                generation=generation,
            ),
            lambda patch: self._set_market_data_status(patch, generation=generation),
            self._market_data_stop_event,
            subscribe_quotes=self._market_data_channels["quotes"],
            subscribe_trades=self._market_data_channels["trades"],
        )
        if stream is None:
            return
        self._market_data_thread = threading.Thread(
            target=self._run_market_data_stream,
            args=(stream, self._market_data_stop_event),
            name=f"{provider}-market-data-stream",
            daemon=True,
        )
        self._market_data_thread.start()

    def _run_market_data_stream(self, stream: Any, stop_event: threading.Event) -> None:
        reconnect_seconds = max(1.0, float(self.settings.market_data_reconnect_seconds))
        while not stop_event.is_set():
            stream.run()
            if stop_event.wait(self._market_data_retry_seconds(reconnect_seconds)):
                return

    def _market_data_retry_seconds(self, reconnect_seconds: float) -> float:
        with self._lock:
            market_data = self._state.get("marketData") if isinstance(self._state.get("marketData"), dict) else {}
            status = str((market_data or {}).get("status") or "").lower()
            permission = str((market_data or {}).get("permission") or "").lower()
        if status == "missing_permission" or permission == "missing_permission":
            return max(reconnect_seconds, 60.0)
        if status in {"missing_key", "missing_dependency"} or permission in {"missing_key", "missing_dependency"}:
            return max(reconnect_seconds, 30.0)
        return reconnect_seconds

    def _record_trade_event(self, bucket: str, frame: Any, *, key_field: str) -> None:
        received_at_ms = now_ms()
        data = annotate_event(frame_to_dict(frame, self.settings), received_at_ms)
        account = data.get("account")
        if account:
            data["account"] = mask_account(
                account,
                self.settings.account_mask_prefix,
                self.settings.account_mask_suffix,
            )
        if bucket == "assets":
            key = asset_event_key(data, received_at_ms)
        else:
            key = data.get(key_field) or data.get("symbol") or data.get("currency") or f"event-{received_at_ms}"
        with self._lock:
            self._state[bucket][str(key)] = data
            self._mark_event_locked(received_at_ms)
            self._append_event_locked(bucket, {"key": key, "received_at_ms": received_at_ms})
            self._touch_locked()

    def _append_event(self, event_type: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._append_event_locked(event_type, data)
            self._touch_locked()

    def _append_event_locked(self, event_type: str, data: dict[str, Any]) -> None:
        events = self._state.setdefault("events", [])
        events.append({"type": event_type, "at": iso_now(), **data})
        del events[:-25]

    def _mark_event_locked(self, received_at_ms: int) -> None:
        self._state["lastEventAtMs"] = received_at_ms
        self._state["lastEventAt"] = datetime.fromtimestamp(received_at_ms / 1000, timezone.utc).isoformat()

    def _touch_locked(self) -> None:
        self._sequence += 1
        self._state["sequence"] = self._sequence
        self._condition.notify_all()

    def _flush_cache(self) -> None:
        try:
            payload = self.snapshot()
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            LOGGER.debug("Failed to write Tiger stream cache", exc_info=True)


def get_streamer(settings: Settings | None = None) -> TigerPushStreamer:
    settings = settings or Settings()
    cache_path = settings.stream_cache_path.resolve()
    with _STREAMERS_LOCK:
        streamer = _STREAMERS.get(cache_path)
        if streamer is None:
            streamer = TigerPushStreamer(settings)
            _STREAMERS[cache_path] = streamer
        return streamer


def ensure_streaming(settings: Settings | None = None, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or Settings()
    streamer = get_streamer(settings)
    streamer.start(snapshot)
    return streamer.snapshot()


def read_stream_cache(path: Path | None = None, settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or Settings()
    cache_path = path or settings.stream_cache_path
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        age = data_age_seconds(data.get("lastEventAtMs"))
        data["ageSeconds"] = age
        data["freshness"] = stream_status(age, settings)
        return data
    return None


def apply_stream_prices(snapshot: dict[str, Any], stream: dict[str, Any] | None) -> dict[str, Any]:
    if not stream:
        return snapshot
    quotes = stream.get("quotes") or {}
    if not quotes:
        return snapshot
    updated = copy.deepcopy(snapshot)
    for sec_type, positions in (updated.get("positions") or {}).items():
        for position in positions or []:
            keys = position_quote_keys(position, sec_type)
            quote = next((quotes.get(key) for key in keys if quotes.get(key)), None)
            if not quote:
                continue
            price = quote_latest_price(quote)
            if price is None:
                continue
            position["market_price"] = price
            quantity = finite_float(position.get("quantity") or position.get("position_qty"))
            if quantity is not None:
                multiplier = 100 if sec_type == "OPT" else 1
                baseline_market_value = finite_float(position.get("market_value"))
                baseline_today_pnl = finite_float(position.get("today_pnl"))
                position["market_value"] = quantity * price * multiplier
                live_market_value = finite_float(position.get("market_value"))
                if baseline_market_value is not None and baseline_today_pnl is not None and live_market_value is not None:
                    baseline_start_value = baseline_market_value - baseline_today_pnl
                    position["today_pnl"] = round(live_market_value - baseline_start_value, 4)
                    position["today_pnl_source"] = "live_quote_delta"
            position["live_price"] = price
            position["live_price_received_at"] = quote.get("received_at")
            position["live_price_age_seconds"] = data_age_seconds(quote.get("received_at_ms"))
    updated["stream_priced"] = True
    return updated


def first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def normalized_asset_segment(asset: dict[str, Any]) -> str | None:
    value = first_present(asset, "seg_type", "category", "segment", "segment_type")
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def asset_event_key(asset: dict[str, Any], received_at_ms: int) -> str:
    currency = str(asset.get("currency") or "UNKNOWN").upper()
    segment = normalized_asset_segment(asset)
    if segment:
        return f"{segment}:{currency}"
    account = str(asset.get("account") or "")
    if account:
        return f"{account}:{currency}"
    return currency


def asset_event_score(asset: dict[str, Any]) -> tuple[int, int, int]:
    segment = normalized_asset_segment(asset)
    net_liquidation = finite_float(asset.get("net_liquidation")) or 0.0
    gross_position_value = finite_float(asset.get("gross_position_value")) or 0.0
    cash_balance = finite_float(asset.get("cash_balance"))
    received_at_ms = int(asset.get("received_at_ms") or 0)
    if segment == "S":
        segment_score = 4
    elif net_liquidation and gross_position_value:
        segment_score = 3
    elif net_liquidation:
        segment_score = 2
    elif segment in {"F", "C"}:
        segment_score = 1
    else:
        segment_score = 0
    completeness = sum(
        1
        for value in (net_liquidation, gross_position_value, cash_balance)
        if value is not None and value != 0
    )
    return segment_score, completeness, received_at_ms


def primary_asset_event(asset_events: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [asset for asset in asset_events.values() if isinstance(asset, dict)]
    if not candidates:
        return None
    return max(candidates, key=asset_event_score)


def apply_asset_event_to_segment(segment: dict[str, Any], asset: dict[str, Any]) -> None:
    baseline_net_liquidation = finite_float(segment.get("net_liquidation"))
    baseline_today_pl = finite_float(segment.get("total_today_pl"))
    pushed_today_pl = first_present(asset, "total_today_pl", "total_today_pnl", "today_pl", "today_pnl")
    field_map = {
        "cash_balance": ("cash_balance",),
        "cash_available_for_trade": ("cash_available_for_trade", "available_funds"),
        "gross_position_value": ("gross_position_value",),
        "equity_with_loan": ("equity_with_loan",),
        "net_liquidation": ("net_liquidation",),
        "init_margin": ("init_margin", "init_margin_req"),
        "maintain_margin": ("maintain_margin", "maint_margin_req"),
        "excess_liquidation": ("excess_liquidation", "excess_liquidity"),
        "buying_power": ("buying_power",),
        "leverage": ("leverage",),
        "unrealized_pl": ("unrealized_pl",),
        "realized_pl": ("realized_pl",),
    }
    for target, keys in field_map.items():
        value = first_present(asset, *keys)
        if value is not None:
            segment[target] = value
    if pushed_today_pl is not None:
        segment["total_today_pl_source"] = "tiger_push"
        segment["total_today_pl"] = pushed_today_pl
    else:
        live_net_liquidation = finite_float(segment.get("net_liquidation"))
        if baseline_net_liquidation is not None and baseline_today_pl is not None and live_net_liquidation is not None:
            baseline_start_value = baseline_net_liquidation - baseline_today_pl
            segment["total_today_pl"] = round(live_net_liquidation - baseline_start_value, 4)
            segment["total_today_pl_source"] = "live_net_liq_delta"
    segment["stream_asset_key"] = asset_event_key(asset, int(asset.get("received_at_ms") or 0))
    segment["stream_asset_segment"] = normalized_asset_segment(asset)
    segment["stream_asset_received_at"] = asset.get("received_at")
    segment["stream_asset_age_seconds"] = data_age_seconds(asset.get("received_at_ms"))


def find_position_row(positions: list[dict[str, Any]], event: dict[str, Any], *, sec_type: str | None = None) -> dict[str, Any] | None:
    normalized_sec_type = str(sec_type or event.get("sec_type") or "").upper()
    event_keys = {
        str(value)
        for value in (
            event.get("identifier"),
            event.get("display"),
        )
        if value
    }
    massive_symbol = option_display_to_massive_symbol(event.get("display") or event.get("identifier"))
    if massive_symbol:
        event_keys.add(massive_symbol)
    if normalized_sec_type != "OPT" and event.get("symbol"):
        event_keys.add(str(event.get("symbol")))
    for position in positions:
        keys = ordered_position_match_keys(
            position,
            sec_type=normalized_sec_type,
            include_underlying_symbol=normalized_sec_type != "OPT",
        )
        if set(keys) & event_keys:
            return position
    return None


def apply_stream_account_updates(snapshot: dict[str, Any], stream: dict[str, Any] | None) -> dict[str, Any]:
    if not stream:
        return snapshot
    updated = copy.deepcopy(snapshot)

    asset_events = stream.get("assets") or {}
    updated["stream_asset_updated"] = bool(asset_events)
    if asset_events:
        assets_root = updated.setdefault("assets", {})
        if not isinstance(assets_root, dict):
            assets_root = {}
            updated["assets"] = assets_root
        segments = assets_root.setdefault("segments", {})
        if not isinstance(segments, dict):
            segments = {}
            assets_root["segments"] = segments
        for asset in asset_events.values():
            if not isinstance(asset, dict):
                continue
            segment_key = normalized_asset_segment(asset)
            if segment_key in {"S", "F", "C"}:
                apply_asset_event_to_segment(segments.setdefault(segment_key, {}), asset)
        primary_asset = primary_asset_event(asset_events)
        # Only promote a non-S primary asset into the security segment when it is the
        # sole asset update. If multiple asset segments arrive together, using one of
        # the non-S segments as a stand-in for S can corrupt net liquidation and P/L.
        if primary_asset and len(asset_events) == 1:
            apply_asset_event_to_segment(segments.setdefault("S", {}), primary_asset)

    position_events = stream.get("positions") or {}
    if position_events:
        positions_by_bucket = updated.setdefault("positions", {})
        for event in position_events.values():
            if not isinstance(event, dict):
                continue
            sec_type = str(event.get("sec_type") or "STK")
            bucket = positions_by_bucket.setdefault(sec_type, [])
            row = find_position_row(bucket, event, sec_type=sec_type)
            if row is None:
                row = {
                    "contract": {
                        "symbol": event.get("symbol"),
                        "identifier": event.get("identifier"),
                        "sec_type": sec_type,
                        "currency": event.get("currency"),
                        "display": event.get("identifier") or event.get("symbol"),
                    }
                }
                bucket.append(row)
            baseline_market_value = finite_float(row.get("market_value"))
            baseline_today_pnl = finite_float(row.get("today_pnl"))
            pushed_today_pnl = first_present(event, "today_pnl", "today_pl", "today_pnl_value")
            quantity = first_present(event, "quantity", "position", "position_qty")
            latest_price = first_present(event, "latest_price", "market_price")
            event_market_value = first_present(event, "market_value")
            field_map = {
                "quantity": (quantity,),
                "position_qty": (quantity,),
                "salable_qty": (first_present(event, "salable_qty", "salable"),),
                "average_cost": (first_present(event, "average_cost"),),
                "market_price": (latest_price,),
                "market_value": (event_market_value,),
                "unrealized_pnl": (first_present(event, "unrealized_pnl"),),
                "today_pnl": (pushed_today_pnl,),
            }
            for target, values in field_map.items():
                value = next((item for item in values if item is not None), None)
                if value is not None:
                    row[target] = value
            if event_market_value is None:
                qty = finite_float(row.get("quantity") or row.get("position_qty"))
                price = finite_float(row.get("market_price"))
                if qty is not None and price is not None:
                    row["market_value"] = qty * price * (100 if sec_type == "OPT" else 1)
            live_market_value = finite_float(row.get("market_value"))
            if pushed_today_pnl is not None:
                row["today_pnl_source"] = "tiger_push"
            elif baseline_market_value is not None and baseline_today_pnl is not None and live_market_value is not None:
                baseline_start_value = baseline_market_value - baseline_today_pnl
                row["today_pnl"] = round(live_market_value - baseline_start_value, 4)
                row["today_pnl_source"] = "live_market_value_delta"
            row["stream_position_received_at"] = event.get("received_at")
            row["stream_position_age_seconds"] = data_age_seconds(event.get("received_at_ms"))

    updated["stream_position_updated"] = bool(position_events)
    updated["stream_account_updated"] = bool(asset_events or position_events)
    return updated


def apply_stream_updates(snapshot: dict[str, Any], stream: dict[str, Any] | None) -> dict[str, Any]:
    return apply_stream_prices(apply_stream_account_updates(snapshot, stream), stream)


def stop_all_streamers() -> None:
    with _STREAMERS_LOCK:
        streamers = list(_STREAMERS.values())
    for streamer in streamers:
        streamer.stop()


atexit.register(stop_all_streamers)
