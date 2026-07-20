from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters.rate_limits import massive_rate_limit_snapshot
from ..broker.account_snapshot import fetch_account_snapshot
from ..config import Settings
from ..integrations.lean import lean_engine_status
from ..research.dependencies import optional_imports_status
from ..storage.audit import AUDIT_FALLBACK_DIR, read_audit_events, sanitize_audit_value


_OPTION_SYMBOL_RE = re.compile(r"^(?:O:)?([A-Z.]+)\s*(\d{6}|\d{8})([CP])(\d{8})")


@dataclass(frozen=True)
class QuoteGroup:
    underlying: str
    stock_symbol: str | None
    stock: dict[str, Any] | None
    options: list[dict[str, Any]]


def canonical_option_symbol(value: str | None) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    match = _OPTION_SYMBOL_RE.match(text)
    if not match:
        return None
    underlying, expiry, put_call, strike = match.groups()
    if len(expiry) == 8:
        expiry = expiry[2:]
    return f"O:{underlying}{expiry}{put_call}{strike}"


def option_underlying(value: str | None) -> str | None:
    canonical = canonical_option_symbol(value)
    if canonical is None:
        return None
    match = _OPTION_SYMBOL_RE.match(canonical)
    return match.group(1) if match else None


def is_option_symbol(value: str | None, payload: dict[str, Any] | None = None) -> bool:
    if canonical_option_symbol(value) is not None:
        return True
    asset_class = str((payload or {}).get("asset_class") or "").strip().lower()
    sec_type = str((payload or {}).get("sec_type") or "").strip().upper()
    return asset_class == "options" or sec_type == "OPT"


def holding_position_keys(values: Iterable[Any]) -> set[str]:
    """Normalize position keys while avoiding stock/option underlying collisions."""

    raw_values = {str(value).strip().upper() for value in values if str(value).strip()}
    canonical_options = {canonical for value in raw_values if (canonical := canonical_option_symbol(value))}
    option_underlyings = {option_underlying(value) for value in canonical_options}
    result = {value for value in raw_values if canonical_option_symbol(value) or value not in option_underlyings}
    result.update(canonical_options)
    return result


def quote_latest_price(quote: dict[str, Any] | None) -> float | None:
    if not quote:
        return None
    for key in ("latest_price", "latestPrice", "price", "last_price", "lastPrice", "mid_price"):
        value = _finite_float(quote.get(key))
        if value is not None and value > 0:
            return value
    bbo = quote.get("bbo") if isinstance(quote.get("bbo"), dict) else None
    bid = _finite_float((bbo or quote).get("bid_price") or (bbo or quote).get("bid"))
    ask = _finite_float((bbo or quote).get("ask_price") or (bbo or quote).get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        return (bid + ask) / 2
    return None


def select_option_mark_price(
    *,
    bbo: dict[str, Any] | None,
    last_trade: dict[str, Any] | None,
    current_ms: int | None = None,
    last_trade_max_age_seconds: float = 15.0,
    last_mid_max_deviation_pct: float = 0.10,
    last_mid_max_deviation_abs: float = 0.05,
) -> dict[str, Any] | None:
    current_ms = current_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    bid = _finite_float((bbo or {}).get("bid_price") or (bbo or {}).get("bid"))
    ask = _finite_float((bbo or {}).get("ask_price") or (bbo or {}).get("ask"))
    midpoint = _finite_float((bbo or {}).get("mid_price"))
    if midpoint is None and bid is not None and ask is not None and bid > 0 and ask >= bid:
        midpoint = (bid + ask) / 2
    spread = (ask - bid) if bid is not None and ask is not None and ask >= bid else None
    spread_pct = spread / midpoint if spread is not None and midpoint else None

    last_price = _finite_float((last_trade or {}).get("latest_price") or (last_trade or {}).get("price"))
    last_age = _age_seconds(last_trade, current_ms)
    last_is_fresh = last_age is not None and last_age <= last_trade_max_age_seconds
    last_inside_book = bid is not None and ask is not None and last_price is not None and bid <= last_price <= ask
    last_near_mid = False
    if last_price is not None and midpoint is not None:
        tolerance = max(last_mid_max_deviation_abs, abs(midpoint) * last_mid_max_deviation_pct)
        last_near_mid = abs(last_price - midpoint) <= tolerance

    if last_price is not None and last_price > 0 and (midpoint is None or (last_is_fresh and last_inside_book and last_near_mid)):
        return {
            "mark_price": last_price,
            "mark_source": "last_trade",
            "last_trade_price": last_price,
            "last_trade_age_seconds": last_age,
            "mid_price": midpoint,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "spread_pct": spread_pct,
            "mark_reason": "fresh last trade inside current bid/ask",
        }
    if midpoint is not None and midpoint > 0:
        return {
            "mark_price": midpoint,
            "mark_source": "midpoint",
            "last_trade_price": last_price,
            "last_trade_age_seconds": last_age,
            "mid_price": midpoint,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "spread_pct": spread_pct,
            "mark_reason": "current bid/ask midpoint",
        }
    return None


def group_market_quotes(
    quotes: dict[str, dict[str, Any]] | None,
    bbo: dict[str, dict[str, Any]] | None = None,
    *,
    subscribed_stocks: Iterable[str] = (),
    subscribed_options: Iterable[str] = (),
) -> list[dict[str, Any]]:
    quote_map = quotes or {}
    bbo_map = bbo or {}
    stock_symbols = {str(symbol).strip().upper() for symbol in subscribed_stocks if str(symbol).strip()}
    option_aliases: dict[str, set[str]] = {}

    def add_option_alias(value: str | None) -> None:
        canonical = canonical_option_symbol(value)
        if canonical is None:
            return
        aliases = option_aliases.setdefault(canonical, set())
        aliases.add(canonical)
        aliases.add(str(value).strip().upper())

    for symbol in subscribed_options:
        add_option_alias(str(symbol))

    for symbol in sorted(set(quote_map) | set(bbo_map)):
        normalized = str(symbol).strip().upper()
        merged = _merged_market_item(symbol, quote_map, bbo_map)
        if is_option_symbol(normalized, merged):
            add_option_alias(normalized)
        elif normalized:
            stock_symbols.add(normalized)

    groups: dict[str, QuoteGroup] = {}

    def group_for(underlying: str | None) -> QuoteGroup:
        key = str(underlying or "--").strip().upper() or "--"
        if key not in groups:
            groups[key] = QuoteGroup(underlying=key, stock_symbol=None, stock=None, options=[])
        return groups[key]

    for symbol in sorted(stock_symbols):
        item = _merged_market_item(symbol, quote_map, bbo_map)
        if is_option_symbol(symbol, item):
            add_option_alias(symbol)
            continue
        current = group_for(symbol)
        groups[symbol] = QuoteGroup(underlying=current.underlying, stock_symbol=symbol, stock=item, options=current.options)

    for canonical, aliases in sorted(option_aliases.items()):
        item = _merged_market_item_any(sorted(aliases), quote_map, bbo_map, canonical)
        underlying = option_underlying(canonical) or str(item.get("underlying") or item.get("underlying_asset") or canonical).upper()
        group_for(underlying).options.append({"symbol": canonical, "data": item})

    ordered_keys: list[str] = []
    for symbol in subscribed_stocks:
        key = str(symbol).strip().upper()
        if key and key not in ordered_keys:
            ordered_keys.append(key)
    for symbol in subscribed_options:
        key = option_underlying(str(symbol)) or option_underlying(canonical_option_symbol(str(symbol)))
        if key and key not in ordered_keys:
            ordered_keys.append(key)
    for key in sorted(groups):
        if key not in ordered_keys:
            ordered_keys.append(key)

    payload: list[dict[str, Any]] = []
    for key in ordered_keys:
        group = groups.get(key)
        if not group:
            continue
        options = sorted(group.options, key=lambda item: str(item["symbol"]))
        payload.append(
            {
                "underlying": group.underlying,
                "stock_symbol": group.stock_symbol,
                "stock": group.stock,
                "options": options,
                "primary_price": quote_latest_price(group.stock) if group.stock else None,
            }
        )
    return payload


def stream_quote_group_model(
    quotes: dict[str, dict[str, Any]] | None,
    bbo: dict[str, dict[str, Any]] | None,
    subscriptions: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return the compact frontend quote-group contract."""

    subscriptions = subscriptions or {}
    groups = group_market_quotes(
        quotes,
        bbo,
        subscribed_stocks=subscriptions.get("quotes") or subscriptions.get("stocks") or [],
        subscribed_options=subscriptions.get("options") or [],
    )
    return [
        {
            "underlying": group["underlying"],
            "stockSymbol": group["stock_symbol"],
            "stock": group["stock"],
            "options": group["options"],
            "primaryPrice": group["primary_price"],
        }
        for group in groups
    ]


def platform_capabilities() -> dict[str, Any]:
    try:
        research_modules = optional_imports_status()
    except Exception:
        research_modules = {}
    try:
        lean_status = lean_engine_status()
    except Exception as exc:
        lean_status = {"available": False, "error": str(exc), "recommendedRole": "research_only"}
    try:
        settings = Settings()
        audit_memory = {
            "audit_log_path": str(settings.audit_log_path),
            "audit_duckdb_path": str(settings.audit_duckdb_path),
            "read_only": True,
        }
    except Exception:
        audit_memory = {"read_only": True}
    return {
        "source": "omega_platform",
        "read_only": True,
        "absorbed": [
            "massive quote normalization",
            "option symbol canonicalization",
            "stock/option quote grouping",
            "option mark-price selection",
            "massive rpm snapshot",
            "Omega live stream cache detail",
            "readonly account/order/transaction stream context",
            "readonly real account risk assessment",
            "fast-vollib option greeks enrichment",
            "portfolio-level readonly option greeks exposure",
            "live readonly market freshness gate for paper runs",
            "readonly Omega portfolio research report",
        ],
        "rate_limits": massive_rate_limit_snapshot(),
        "research_modules": research_modules,
        "lean_sidecar": lean_status,
        "audit_memory": audit_memory,
    }


def fetch_real_account_snapshot_readonly() -> dict[str, Any]:
    """Fetch and sanitize a Tiger account snapshot for read-only consumers."""
    return fetch_account_snapshot()


def platform_audit_readonly(limit: int = 25) -> dict[str, Any]:
    try:
        settings = Settings()
        configured_path = os.environ.get("OMEGA_AUDIT_LOG_PATH")
        audit_path = Path(configured_path) if configured_path else settings.audit_log_path
        events = read_audit_events(audit_path, limit=limit)
        if not events:
            fallback = AUDIT_FALLBACK_DIR / audit_path.name
            events = read_audit_events(fallback, limit=limit)
        safe_events = [sanitize_audit_value(event) for event in events[-limit:]]
        summary = _audit_summary_from_events(safe_events)
        return {
            "source": "omega_audit_log",
            "read_only": True,
            "available": bool(safe_events),
            "events_count": len(safe_events),
            "summary": summary,
            "events": safe_events,
            "message": "Omega audit history is available as read-only research context.",
        }
    except Exception as exc:
        return {
            "source": "omega_audit_log",
            "read_only": True,
            "available": False,
            "events_count": 0,
            "summary": [],
            "events": [],
            "message": f"Omega audit history is unavailable: {exc}",
        }


def _audit_summary_from_events(events: list[dict[str, Any]]) -> list[str]:
    if not events:
        return ["- No Omega recommendations, approvals, or rejections are available."]
    counts: dict[str, int] = {}
    for event in events:
        kind = str(event.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    count_line = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    lines = [f"- 审计事件统计：{count_line}。"]
    for event in events[-4:]:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        display = payload.get("symbol") or payload.get("display") or event.get("kind") or "--"
        message = payload.get("message") or payload.get("status") or payload.get("decision") or ""
        lines.append(f"- `{display}`：{message}".rstrip())
    return lines


def _merged_market_item(
    symbol: str,
    quotes: dict[str, dict[str, Any]],
    bbo: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    normalized = str(symbol).strip().upper()
    data = dict(quotes.get(symbol) or quotes.get(normalized) or {})
    bbo_data = dict(bbo.get(symbol) or bbo.get(normalized) or {})
    if bbo_data:
        data.setdefault("bbo", bbo_data)
        for key, value in bbo_data.items():
            data.setdefault(key, value)
    data.setdefault("symbol", normalized)
    return data


def _merged_market_item_any(
    aliases: list[str],
    quotes: dict[str, dict[str, Any]],
    bbo: dict[str, dict[str, Any]],
    fallback_symbol: str,
) -> dict[str, Any]:
    merged: dict[str, Any] = {"symbol": fallback_symbol}
    for alias in aliases:
        item = _merged_market_item(alias, quotes, bbo)
        if item:
            merged.update({key: value for key, value in item.items() if value is not None})
    merged["symbol"] = fallback_symbol
    return merged


def _age_seconds(item: dict[str, Any] | None, current_ms: int) -> float | None:
    if not item:
        return None
    received_at_ms = _finite_float(item.get("received_at_ms"))
    if received_at_ms is None:
        return None
    return max(0.0, (current_ms - received_at_ms) / 1000.0)


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number
