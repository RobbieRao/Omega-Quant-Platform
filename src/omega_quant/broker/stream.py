"""把 Omega 已有的实时只读数据源 + Tiger Push 账户事件拼成 preflight_gates 期待的快照字典。

Omega 已经在 ``adapters/live_market_readonly.py`` 维护了 Tiger Push 行情会话；
本模块不重复建立连接，而是把现有 readonly session 暴露出的状态、
Massive 行情、最近账户事件，组合成 ``stream`` 字典送给闸门检查。

Phase 2 提供数据形状定义和组合函数；Phase 3 的 reconciler 会在生产路径上调用。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def compose_stream_snapshot(
    *,
    connected: bool,
    last_event_at_ms: int | None,
    quotes: Mapping[str, dict[str, Any]] | None = None,
    bbo: Mapping[str, dict[str, Any]] | None = None,
    market_data: Mapping[str, Any] | None = None,
    age_seconds: float | None = None,
) -> dict[str, Any]:
    """把分散的实时数据拼成 :func:`run_preflight_gates` 兼容的 stream 字典。"""

    snapshot: dict[str, Any] = {
        "connected": bool(connected),
        "lastEventAtMs": last_event_at_ms,
        "quotes": dict(quotes or {}),
        "bbo": dict(bbo or {}),
        "marketData": dict(market_data or {}),
    }
    if age_seconds is not None:
        snapshot["ageSeconds"] = float(age_seconds)
    return snapshot


def aggregate_subscribed_symbols(stream: Mapping[str, Any]) -> list[str]:
    quotes = stream.get("quotes") or {}
    bbo = stream.get("bbo") or {}
    seen: list[str] = []
    for source in (quotes, bbo):
        for symbol in source.keys():
            if symbol and symbol not in seen:
                seen.append(symbol)
    return seen


def stream_summary(stream: Mapping[str, Any], *, watch_symbols: Iterable[str] | None = None) -> dict[str, Any]:
    """供 ``GET /api/broker/stream/status`` 输出的精简视图。"""

    watch = list(watch_symbols or [])
    quotes = stream.get("quotes") or {}
    fresh_count = 0
    stale_count = 0
    for symbol in watch:
        quote = quotes.get(symbol)
        if not quote:
            continue
        received_at = quote.get("received_at_ms")
        if received_at:
            fresh_count += 1
        else:
            stale_count += 1
    return {
        "connected": bool(stream.get("connected")),
        "ageSeconds": stream.get("ageSeconds"),
        "lastEventAtMs": stream.get("lastEventAtMs"),
        "marketData": dict(stream.get("marketData") or {}),
        "subscribedSymbols": aggregate_subscribed_symbols(stream),
        "watchSymbols": watch,
        "watchSymbolsFresh": fresh_count,
        "watchSymbolsStale": stale_count,
    }
