from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pandas as pd

from .adapters.platform_context import canonical_option_symbol
from .market_events import MarketEvent, MarketEventSource, new_market_event, normalize_live_payload


def generate_seed_bars(symbol: str = "AAPL", periods: int = 80) -> pd.DataFrame:
    timestamps = pd.date_range("2026-01-02", periods=periods, freq="D", tz="UTC")
    rows: list[dict] = []
    base = 100.0
    for idx, timestamp in enumerate(timestamps):
        trend = idx * 0.42
        wave = ((idx % 9) - 4) * 0.18
        close = round(base + trend + wave, 2)
        open_price = round(close - 0.25, 2)
        volume = 1_000_000 + idx * 7_500
        if idx in {periods - 2, periods - 1, 30, 31}:
            volume *= 2.4
        rows.append(
            {
                "timestamp": timestamp.to_pydatetime(),
                "symbol": symbol,
                "open": open_price,
                "high": round(close + 0.8, 2),
                "low": round(close - 0.9, 2),
                "close": close,
                "volume": float(volume),
            }
        )
    return pd.DataFrame(rows)


def latest_snapshot(symbol: str = "AAPL", source: MarketEventSource = "live_readonly") -> tuple[MarketEvent, pd.DataFrame]:
    bars = generate_seed_bars(symbol)
    row = bars.iloc[-1]
    return (
        new_market_event(
            source=source,
            symbol=symbol,
            timestamp=row["timestamp"],
            price=float(row["close"]),
            volume=float(row["volume"]),
            data_quality="seed_bar",
            raw={"dataset": "seed:trend-and-volume"},
        ),
        bars,
    )


def bars_with_market_event(event: MarketEvent, periods: int = 80) -> pd.DataFrame:
    bars = generate_seed_bars(event.symbol, periods=periods)
    idx = bars.index[-1]
    price = round(float(event.price), 4)
    bars.loc[idx, "timestamp"] = event.timestamp
    bars.loc[idx, "symbol"] = event.symbol
    bars.loc[idx, "open"] = price
    bars.loc[idx, "high"] = max(price, float(event.ask or price))
    bars.loc[idx, "low"] = min(price, float(event.bid or price))
    bars.loc[idx, "close"] = price
    bars.loc[idx, "volume"] = float(event.volume or bars.loc[idx, "volume"])
    return bars


class ReadOnlyStreamCacheFeed:
    """Read Omega's stream cache as market context without exposing trading APIs."""

    def __init__(self, cache_path: Path | None = None):
        configured = os.environ.get("OMEGA_STREAM_CACHE_PATH") or os.environ.get("OMEGA_REAL_READONLY_STREAM_PATH")
        self.cache_path = cache_path or Path(configured or "reports/json/live_stream.json")

    def latest(self, symbol: str) -> MarketEvent | None:
        if not self.cache_path.exists():
            return None
        payload = json.loads(self.cache_path.read_text())
        normalized = symbol.upper()
        resolved_symbol = canonical_option_symbol(symbol) or normalized
        aliases = _market_aliases(symbol)
        quotes = payload.get("quotes") or {}
        bbo = payload.get("bbo") or {}
        ticks = payload.get("ticks") or {}
        quote = _first_market_item(quotes, aliases)
        book = _first_market_item(bbo, aliases)
        tick = _first_market_item(ticks, aliases)
        if not isinstance(quote, dict) and not isinstance(book, dict) and not isinstance(tick, dict):
            return None
        enriched: dict = {}
        if isinstance(quote, dict):
            enriched.update(quote)
        if isinstance(book, dict):
            enriched.setdefault("bbo", book)
            for key, value in book.items():
                enriched[key] = value
        if isinstance(tick, dict):
            for key in ("latest_price", "price", "last_price", "last", "volume", "timestamp", "server_timestamp_ms", "received_at", "received_at_ms"):
                if tick.get(key) is not None:
                    enriched[key] = tick.get(key)
            enriched.setdefault("last_trade", tick)
        enriched["symbol"] = resolved_symbol
        enriched.setdefault("received_at", enriched.get("received_at") or payload.get("lastEventAt") or payload.get("updatedAt"))
        enriched.setdefault("data_quality", enriched.get("data_quality") or "live_stream_cache_readonly")
        return normalize_live_payload(enriched, symbol=resolved_symbol)


def _market_aliases(symbol: str) -> list[str]:
    normalized = str(symbol or "").strip().upper()
    aliases = [normalized]
    canonical = canonical_option_symbol(normalized)
    if canonical:
        aliases.extend([canonical, canonical.removeprefix("O:")])
    result: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        if alias and alias not in seen:
            result.append(alias)
            seen.add(alias)
    return result


def _first_market_item(collection: object, aliases: list[str]) -> dict | None:
    if not isinstance(collection, dict):
        return None
    for alias in aliases:
        item = collection.get(alias)
        if isinstance(item, dict):
            return item
    return None


def _quote_price(quote: dict) -> float | None:
    for key in ("latest_price", "latestPrice", "price", "last_price", "lastPrice"):
        value = quote.get(key)
        try:
            if value is not None and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            continue
    bbo = quote.get("bbo") if isinstance(quote.get("bbo"), dict) else None
    if bbo:
        try:
            bid = float(bbo.get("bid_price") or bbo.get("bid") or 0)
            ask = float(bbo.get("ask_price") or bbo.get("ask") or 0)
        except (TypeError, ValueError):
            return None
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
    return None


def normalize_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return pd.Timestamp(value).to_pydatetime().astimezone(timezone.utc)
