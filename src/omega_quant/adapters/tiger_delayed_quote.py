"""Tiger 延时 / 实时报价兜底来源。

Tiger 账户通常自带美股 **延时报价 (~15min)** 的免费权限；实时美股 quote、夜盘 quote、深度盘口需要单独付费订阅。
Massive WebSocket 静默（连接成功但 0 events）时，本模块作为后备数据源给前端「行情盘口」提供可读价格。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..market_events import MarketEvent


@dataclass
class TigerDelayedQuoteCache:
    """Process-wide cache for Tiger delayed brief snapshots."""

    ttl_seconds: float = 5.0
    _cache: dict[str, tuple[float, MarketEvent]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _quote_client: Any = None
    _permissions: list[dict[str, Any]] = field(default_factory=list)
    _last_permission_check: float = 0.0

    def _client(self) -> Any:
        if self._quote_client is not None:
            return self._quote_client
        from tigeropen.quote.quote_client import QuoteClient

        from ..broker.tigeropen_client import _build_client_config

        self._quote_client = QuoteClient(_build_client_config())
        return self._quote_client

    def permissions(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        if not force_refresh and self._permissions and (now - self._last_permission_check) < 60.0:
            return list(self._permissions)
        try:
            raw = self._client().get_quote_permission() or []
            self._permissions = [dict(p) for p in raw]
            self._last_permission_check = now
        except Exception:
            self._permissions = []
            self._last_permission_check = now
        return list(self._permissions)

    def has_us_realtime(self) -> bool:
        for p in self.permissions():
            name = str(p.get("name") or "").lower()
            if "usstock" in name and ("lv1" in name or "lv2" in name or "realtime" in name):
                return True
        return False

    def snapshot(self, symbol: str) -> MarketEvent | None:
        symbol = symbol.strip().upper()
        if not symbol:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(symbol)
            if cached and (now - cached[0]) < self.ttl_seconds:
                return cached[1]
        try:
            client = self._client()
            df = client.get_stock_delay_briefs(symbols=[symbol])
        except Exception:
            return None
        if df is None or getattr(df, "empty", True):
            return None
        try:
            row = df.iloc[0].to_dict()
        except Exception:
            return None

        last_price = _to_float(row.get("latest_price") or row.get("close"))
        if last_price is None:
            return None
        ts_ms = row.get("latest_time") or row.get("time")
        try:
            ts = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc) if ts_ms else datetime.now(timezone.utc)
        except Exception:
            ts = datetime.now(timezone.utc)

        event = MarketEvent(
            event_id=f"tiger_delay_{symbol}_{int(ts.timestamp() * 1000)}",
            source="live_readonly",
            symbol=symbol,
            timestamp=ts,
            price=float(last_price),
            volume=_to_float(row.get("volume")),
            bid=_to_float(row.get("bid_price")),
            ask=_to_float(row.get("ask_price")),
            last=float(last_price),
            data_quality="delayed_15min_tigeropen",
            raw={
                "provider": "tigeropen_delay_briefs",
                "pre_close": row.get("pre_close"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "halted": row.get("halted"),
            },
        )
        with self._lock:
            self._cache[symbol] = (now, event)
        return event


_singleton: TigerDelayedQuoteCache | None = None


def get_tiger_delayed_cache() -> TigerDelayedQuoteCache:
    global _singleton
    if _singleton is None:
        _singleton = TigerDelayedQuoteCache()
    return _singleton


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:
        return None
    return result
