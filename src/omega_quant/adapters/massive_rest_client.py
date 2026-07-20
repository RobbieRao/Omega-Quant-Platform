"""Massive REST 报价兜底客户端.

Massive WS 在很多 plan tier 下接受 auth+subscribe 但不推 quote/trade（特别是
夜盘期间 / 非 RTH 时段）。同样 plan 的 REST 接口却完全开放，可以拉到最近 quote
+ trade + snapshot。本模块在 WS 静默时给行情盘口提供 last bid/ask/last 数据。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..config import Settings
from ..market_events import MarketEvent


def _resolve_base() -> str:
    override = os.environ.get("OMEGA_MASSIVE_REST_BASE")
    if override:
        return override.rstrip("/")
    return Settings().massive_rest_base_url


def _resolve_api_key() -> str:
    return Settings().massive_api_key or ""


@dataclass
class MassiveRESTClient:
    base: str = field(default_factory=_resolve_base)
    api_key: str = field(default_factory=_resolve_api_key)
    timeout_seconds: float = 8.0
    cache_ttl: float = 4.0
    _cache: dict[str, tuple[float, MarketEvent]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _get(self, path: str) -> dict[str, Any] | None:
        if not self.api_key:
            return None
        url = f"{self.base}{path}"
        sep = "&" if "?" in path else "?"
        url = f"{url}{sep}apiKey={urllib.parse.quote(self.api_key)}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_seconds) as r:
                if r.status != 200:
                    return None
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception:
            return None

    def snapshot(self, symbol: str) -> MarketEvent | None:
        symbol = symbol.strip().upper()
        if not symbol:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(symbol)
            if cached and (now - cached[0]) < self.cache_ttl:
                return cached[1]

        last_trade = self._get(f"/v2/last/trade/{symbol}") or {}
        last_nbbo = self._get(f"/v2/last/nbbo/{symbol}") or {}
        prev_day = self._get(f"/v2/aggs/ticker/{symbol}/prev") or {}

        trade_results = (last_trade.get("results") or {}) if isinstance(last_trade, dict) else {}
        nbbo_results = (last_nbbo.get("results") or {}) if isinstance(last_nbbo, dict) else {}
        prev_results_list = (prev_day.get("results") or []) if isinstance(prev_day, dict) else []
        prev_results = prev_results_list[0] if prev_results_list else {}

        # Polygon-format fields: p=trade price, s=trade size, t=sip timestamp (ns)
        #                       P=ask price, S=ask size, p (NBBO)=bid price, s (NBBO)=bid size
        last_price = trade_results.get("p")
        if last_price is None or float(last_price) <= 0:
            # Fall back to NBBO mid if no trade
            bid_ = nbbo_results.get("p")
            ask_ = nbbo_results.get("P")
            if bid_ and ask_:
                last_price = (float(bid_) + float(ask_)) / 2.0
        if last_price is None:
            return None

        trade_ns = trade_results.get("t") or trade_results.get("y")
        nbbo_ns = nbbo_results.get("t") or nbbo_results.get("y")
        ts_ns = max(int(trade_ns or 0), int(nbbo_ns or 0)) or None
        ts = (
            datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
            if ts_ns
            else datetime.now(timezone.utc)
        )

        bid_price = nbbo_results.get("p")
        ask_price = nbbo_results.get("P")
        trade_size = trade_results.get("s")
        decimal_size = trade_results.get("ds")
        try:
            volume = float(decimal_size) if decimal_size else float(trade_size or 0.0)
        except Exception:
            volume = 0.0

        event = MarketEvent(
            event_id=f"massive_rest_{symbol}_{ts_ns or int(ts.timestamp() * 1e9)}",
            source="live_readonly",
            symbol=symbol,
            timestamp=ts,
            price=float(last_price),
            volume=volume,
            bid=_to_float(bid_price),
            ask=_to_float(ask_price),
            last=float(last_price),
            data_quality="massive_rest_snapshot",
            raw={
                "provider": "massive_rest",
                "last_trade": trade_results,
                "last_nbbo": nbbo_results,
                "prev_day": prev_results,
            },
        )
        with self._lock:
            self._cache[symbol] = (now, event)
        return event


_singleton: MassiveRESTClient | None = None


def get_massive_rest_client() -> MassiveRESTClient:
    global _singleton
    if _singleton is None:
        _singleton = MassiveRESTClient()
    return _singleton


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v <= 0:
        return None
    return v
