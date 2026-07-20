from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from .models import id_with


MarketEventSource = Literal["live_readonly"]
DATA_MODES: tuple[MarketEventSource, ...] = ("live_readonly",)


@dataclass(frozen=True)
class MarketEvent:
    event_id: str
    source: MarketEventSource
    symbol: str
    timestamp: datetime
    price: float
    volume: float | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    data_quality: str = "live_readonly"
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "source": self.source,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "price": self.price,
            "volume": self.volume,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "data_quality": self.data_quality,
            "received_at": self.received_at.isoformat(),
            "raw": self.raw,
        }


def normalize_data_mode(value: str | None) -> MarketEventSource:
    mode = (value or "live_readonly").strip().lower()
    if mode not in DATA_MODES:
        raise ValueError(f"unsupported data mode: {value}")
    return mode  # type: ignore[return-value]


def utc_datetime(value: object | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000_000:
            number = number / 1_000_000
        elif number > 10_000_000_000:
            number = number / 1_000
        return datetime.fromtimestamp(number, timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def finite_number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def market_event_age_seconds(event: MarketEvent, *, now: datetime | None = None) -> float:
    current = now or datetime.now(timezone.utc)
    received_at = event.received_at.astimezone(timezone.utc) if event.received_at.tzinfo else event.received_at.replace(tzinfo=timezone.utc)
    return max(0.0, (current - received_at).total_seconds())


def assess_market_event_quality(event: MarketEvent, *, max_age_seconds: float = 300.0) -> dict[str, Any]:
    age_seconds = market_event_age_seconds(event)
    if event.source != "live_readonly":
        return {
            "ok": False,
            "status": "not_live_readonly",
            "message": "Omega 纸面运行只接受实时只读行情输入。",
            "source": event.source,
            "age_seconds": age_seconds,
        }
    if event.price <= 0:
        return {
            "ok": False,
            "status": "bad_price",
            "message": "实时只读行情没有有效价格，Omega 已暂停本次纸面运行。",
            "source": event.source,
            "age_seconds": age_seconds,
        }
    if age_seconds > max_age_seconds:
        return {
            "ok": False,
            "status": "stale",
            "message": f"实时只读行情超过 {max_age_seconds:.0f} 秒未更新，Omega 已暂停本次纸面运行。",
            "source": event.source,
            "age_seconds": age_seconds,
            "max_age_seconds": max_age_seconds,
        }
    return {
        "ok": True,
        "status": "passed",
        "message": "实时只读行情质量检查通过，可用于纸面模拟。",
        "source": event.source,
        "age_seconds": age_seconds,
        "max_age_seconds": max_age_seconds,
        "data_quality": event.data_quality,
    }


def new_market_event(
    *,
    source: MarketEventSource,
    symbol: str,
    timestamp: object | None,
    price: object,
    volume: object = None,
    bid: object = None,
    ask: object = None,
    last: object = None,
    data_quality: str,
    received_at: object | None = None,
    raw: dict[str, Any] | None = None,
) -> MarketEvent:
    price_number = finite_number(price)
    if price_number is None or price_number <= 0:
        raise ValueError("market event price must be a positive number")
    bid_number = finite_number(bid)
    ask_number = finite_number(ask)
    last_number = finite_number(last)
    return MarketEvent(
        event_id=id_with("mkt"),
        source=source,
        symbol=symbol.upper(),
        timestamp=utc_datetime(timestamp),
        price=price_number,
        volume=finite_number(volume),
        bid=bid_number,
        ask=ask_number,
        last=last_number if last_number is not None else price_number,
        data_quality=data_quality,
        received_at=utc_datetime(received_at),
        raw=raw or {},
    )


def normalize_live_payload(payload: dict[str, Any], *, symbol: str | None = None) -> MarketEvent | None:
    resolved_symbol = str(symbol or payload.get("symbol") or payload.get("sym") or "").upper()
    if not resolved_symbol:
        return None
    bbo = payload.get("bbo") if isinstance(payload.get("bbo"), dict) else {}
    bid = payload.get("bid") or payload.get("bid_price") or bbo.get("bid") or bbo.get("bid_price")
    ask = payload.get("ask") or payload.get("ask_price") or bbo.get("ask") or bbo.get("ask_price")
    last = payload.get("last") or payload.get("last_price") or payload.get("latest_price") or payload.get("price")
    midpoint = payload.get("mid_price") or bbo.get("mid_price")
    price = last or midpoint
    if price is None:
        bid_number = finite_number(bid)
        ask_number = finite_number(ask)
        if bid_number and ask_number and ask_number >= bid_number:
            price = (bid_number + ask_number) / 2
    if price is None:
        return None
    timestamp = payload.get("timestamp") or payload.get("server_timestamp_ms") or payload.get("received_at")
    return new_market_event(
        source="live_readonly",
        symbol=resolved_symbol,
        timestamp=timestamp,
        price=price,
        volume=payload.get("volume") or payload.get("last_size"),
        bid=bid,
        ask=ask,
        last=last or price,
        data_quality=str(payload.get("data_quality") or "live_readonly"),
        received_at=payload.get("received_at"),
        raw=dict(payload),
    )
