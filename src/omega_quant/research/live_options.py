"""Live option mark and Greeks enrichment shared by API and research tools."""

from __future__ import annotations

from typing import Any

from ..adapters.massive_stream import option_display_to_massive_symbol
from ..broker.account_stream import position_quote_keys, quote_latest_price
from ..config import Settings
from ..core.option_pricing import (
    cached_fast_vollib_greeks,
    option_contract_snapshot_from_position,
    select_option_mark_price,
)
from ..utils import finite_float
from .risk import snapshot_valuation_date


def _first_stream_item(bucket: dict[str, Any], keys: list[str]) -> dict[str, Any] | None:
    return next((bucket[key] for key in keys if isinstance(bucket.get(key), dict)), None)


def _underlying_stream_price(symbol: str | None, stream: dict[str, Any], snapshot: dict[str, Any]) -> float | None:
    if not symbol:
        return None
    symbol = str(symbol).upper()
    quote_price = quote_latest_price((stream.get("quotes") or {}).get(symbol))
    if quote_price is not None:
        return quote_price
    book = (stream.get("bbo") or {}).get(symbol)
    if isinstance(book, dict):
        book_price = quote_latest_price({"bbo": book})
        if book_price is not None:
            return book_price
    for position in (snapshot.get("positions") or {}).get("STK") or []:
        contract = position.get("contract") or {}
        if str(contract.get("symbol") or "").upper() == symbol:
            return finite_float(position.get("market_price"))
    return None


def live_options_greeks(snapshot: dict[str, Any], stream: dict[str, Any], settings: Settings) -> dict[str, Any]:
    if not settings.resolved_option_greeks_enabled:
        return {}
    quotes = stream.get("quotes") or {}
    bbo = stream.get("bbo") or {}
    ticks = stream.get("ticks") or {}
    if not any((quotes, bbo, ticks)):
        return {}
    valuation_date = snapshot_valuation_date(snapshot)
    result: dict[str, Any] = {}
    for position in (snapshot.get("positions") or {}).get("OPT") or []:
        contract = position.get("contract") or {}
        option_symbol = option_display_to_massive_symbol(contract.get("display") or contract.get("identifier") or "")
        if not option_symbol:
            continue
        keys = list(dict.fromkeys([option_symbol, *position_quote_keys(position, "OPT")]))
        option_quote = _first_stream_item(quotes, keys)
        option_bbo = _first_stream_item(bbo, keys)
        if option_bbo is None and isinstance((option_quote or {}).get("bbo"), dict):
            option_bbo = (option_quote or {}).get("bbo")
        option_tick = _first_stream_item(ticks, keys)
        mark = select_option_mark_price(
            bbo=option_bbo,
            last_trade=option_tick,
            last_trade_max_age_seconds=settings.option_greeks_last_trade_max_age_seconds,
            last_mid_max_deviation_pct=settings.option_greeks_last_mid_max_deviation_pct,
            last_mid_max_deviation_abs=settings.option_greeks_last_mid_max_deviation_abs,
        )
        if not mark:
            continue
        underlying_price = _underlying_stream_price(contract.get("symbol"), stream, snapshot)
        if underlying_price is None:
            continue
        contract_snapshot = option_contract_snapshot_from_position(
            position,
            option_price=float(mark["mark_price"]),
            underlying_price=underlying_price,
            risk_free_rate=settings.option_risk_free_rate,
            valuation_date=valuation_date,
        )
        if contract_snapshot is None:
            continue
        result[option_symbol] = cached_fast_vollib_greeks(
            option_symbol,
            contract_snapshot,
            mark=mark,
            min_interval_seconds=settings.resolved_option_greeks_min_interval_seconds,
        )
    return result
