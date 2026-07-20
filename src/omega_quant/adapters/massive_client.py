from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

import pandas as pd

from .rate_limits import record_massive_rest_request
from ..config import Settings

LOGGER = logging.getLogger("omega-quant.massive-client")
LOGGER.addHandler(logging.NullHandler())


def build_rest_client(settings: Settings):
    from massive import RESTClient

    return RESTClient(
        api_key=settings.massive_api_key,
        base=settings.massive_rest_base_url,
    )


def _first_attr(obj: Any, *names: str) -> Any:
    if obj is None:
        return None
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _aggs_to_ohlcv(aggs: list[Any]) -> pd.DataFrame:
    rows = []
    for agg in aggs:
        ts = getattr(agg, "timestamp", None)
        if ts is None:
            continue
        rows.append(
            {
                "timestamp": pd.Timestamp(ts, unit="ms", tz="UTC").normalize().tz_localize(None),
                "open": agg.open,
                "high": agg.high,
                "low": agg.low,
                "close": agg.close,
                "volume": agg.volume,
            }
        )
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).set_index("timestamp")
    return df.sort_index()


def fetch_ohlcv(
    symbols: list[str],
    settings: Settings,
    *,
    start: str = "2021-11-01",
    end: str | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Fetch daily OHLCV for multiple symbols via Massive REST.
    Returns {symbol: DataFrame(DatetimeIndex, columns=[open,high,low,close,volume])}.
    Empty dict on failure.
    """
    if not symbols:
        return {}
    if not settings.massive_api_key:
        return {}

    end_date = end or (date.today() + timedelta(days=1)).isoformat()

    try:
        client = build_rest_client(settings)
    except Exception as exc:
        LOGGER.warning("Massive REST client build failed: %s", exc)
        return {}

    result: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            record_massive_rest_request()
            aggs = list(
                client.list_aggs(
                    ticker=symbol,
                    multiplier=1,
                    timespan="day",
                    from_=start,
                    to=end_date,
                    adjusted=True,
                    sort="asc",
                    limit=50000,
                )
            )
            df = _aggs_to_ohlcv(aggs)
            if not df.empty:
                result[symbol] = df
            else:
                LOGGER.warning("No daily aggs returned for %s", symbol)
        except Exception as exc:
            LOGGER.warning("Failed to fetch OHLCV for %s: %s", symbol, exc)

    return result


def fetch_close_prices(
    symbols: list[str],
    settings: Settings,
    *,
    start: str = "2021-11-01",
    end: str | None = None,
) -> pd.DataFrame:
    """
    Fetch daily close prices for multiple symbols.
    Returns DataFrame(DatetimeIndex, columns=symbols). Empty DataFrame on failure.
    """
    ohlcv = fetch_ohlcv(symbols, settings, start=start, end=end)
    if not ohlcv:
        return pd.DataFrame()

    close_series = {sym: df["close"] for sym, df in ohlcv.items() if "close" in df.columns}
    if not close_series:
        return pd.DataFrame()

    close = pd.DataFrame(close_series)
    close = close.apply(pd.to_numeric, errors="coerce")
    return close.dropna(axis=1, how="all").dropna(axis=0, how="all")


def fetch_ticker_snapshots(
    symbols: list[str],
    settings: Settings,
) -> dict[str, dict[str, Any]]:
    """
    Fetch current market snapshots for stock symbols via Massive REST.
    Returns {symbol: {price, bid, ask, todays_change_pct, ...}}.
    """
    if not symbols or not settings.massive_api_key:
        return {}

    try:
        client = build_rest_client(settings)
        record_massive_rest_request()
        snapshots = client.get_snapshot_all("stocks", tickers=",".join(symbols))
    except Exception as exc:
        LOGGER.warning("Failed to fetch ticker snapshots: %s", exc)
        return {}

    result: dict[str, dict[str, Any]] = {}
    for snap in snapshots or []:
        symbol = snap.ticker
        if not symbol:
            continue
        lq = snap.last_quote
        lt = snap.last_trade
        mb = snap.min
        result[symbol] = {
            "price": getattr(lt, "price", None) if lt else None,
            "bid": _first_attr(lq, "bid_price", "bid") if lq else None,
            "ask": _first_attr(lq, "ask_price", "ask") if lq else None,
            "todays_change": _first_attr(snap, "todays_change"),
            "todays_change_pct": _first_attr(snap, "todays_change_percent"),
            "min_close": getattr(mb, "close", None) if mb else None,
            "min_vwap": getattr(mb, "vwap", None) if mb else None,
            "updated_ms": _first_attr(snap, "updated"),
        }
    return result


_OPTION_CONTRACT_RE = re.compile(r"^O:([A-Z.]+)\d")


def _option_contract_to_underlying(contract: str) -> str | None:
    """'O:MU260618C01070000' → 'MU'"""
    m = _OPTION_CONTRACT_RE.match(contract or "")
    return m.group(1) if m else None


def fetch_options_greeks(
    option_contracts: list[str],
    settings: Settings,
) -> dict[str, dict[str, Any]]:
    """
    Fetch real-time options greeks + IV from Massive snapshots.
    option_contracts: list of 'O:MU260618C01070000' strings.
    Returns {contract: {delta, gamma, theta, vega, iv, bid, ask, open_interest}}.
    """
    if not option_contracts or not settings.massive_api_key:
        return {}

    try:
        client = build_rest_client(settings)
    except Exception as exc:
        LOGGER.warning("Massive REST client build failed for options greeks: %s", exc)
        return {}

    result: dict[str, dict[str, Any]] = {}
    for contract in option_contracts:
        underlying = _option_contract_to_underlying(contract)
        if not underlying:
            continue
        try:
            record_massive_rest_request()
            snap = client.get_snapshot_option(
                underlying_asset=underlying,
                option_contract=contract,
            )
            if snap is None:
                continue
            greeks = snap.greeks
            lq = snap.last_quote
            result[contract] = {
                "delta": getattr(greeks, "delta", None) if greeks else None,
                "gamma": getattr(greeks, "gamma", None) if greeks else None,
                "theta": getattr(greeks, "theta", None) if greeks else None,
                "vega": getattr(greeks, "vega", None) if greeks else None,
                "iv": _first_attr(snap, "implied_volatility", "iv"),
                "bid": _first_attr(lq, "bid_price", "bid") if lq else None,
                "ask": _first_attr(lq, "ask_price", "ask") if lq else None,
                "open_interest": _first_attr(snap, "open_interest"),
                "break_even_price": _first_attr(snap, "break_even_price"),
            }
        except Exception as exc:
            LOGGER.warning("Failed to fetch greeks for %s: %s", contract, exc)

    return result
