"""真实下单前的实时行情闸门。

Omega market-context 的 13+ 道实时执行门禁，
逐一保留。所有判断只依赖传入的 ``action`` / ``stream`` / ``thresholds``，
无外部副作用，便于在测试中喂入合成 stream 状态精确验证每道门。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Iterable

_SUPPORTED_ASSET_CLASSES: frozenset[str] = frozenset(("us_stock", "us_etf", "us_option", "short", "margin"))


# Omega live execution threshold defaults.
DEFAULT_STREAM_STALE_AFTER_SECONDS: float = 3.0
DEFAULT_STREAM_HARD_STALE_AFTER_SECONDS: float = 10.0
DEFAULT_MAX_STOCK_SPREAD_PCT: float = 0.025
DEFAULT_MAX_OPTION_SPREAD_PCT: float = 0.15


@dataclass(frozen=True)
class GateThresholds:
    stream_stale_after_seconds: float = DEFAULT_STREAM_STALE_AFTER_SECONDS
    stream_hard_stale_after_seconds: float = DEFAULT_STREAM_HARD_STALE_AFTER_SECONDS
    max_stock_spread_pct: float = DEFAULT_MAX_STOCK_SPREAD_PCT
    max_option_spread_pct: float = DEFAULT_MAX_OPTION_SPREAD_PCT


def _now_ms() -> int:
    return int(time.time() * 1000)


def _as_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    if result != result or result in (float("inf"), float("-inf")):
        return 0.0
    return result


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def data_age_seconds(received_at_ms: int | None) -> float | None:
    if not received_at_ms:
        return None
    return max(0.0, (_now_ms() - received_at_ms) / 1000)


def quote_latest_price(quote: dict[str, Any] | None) -> float | None:
    if not quote:
        return None
    for key in ("latest_price", "latestPrice", "price", "last_price", "lastPrice"):
        value = _finite_float(quote.get(key))
        if value is not None and value > 0:
            return value
    bbo = quote.get("bbo") if isinstance(quote.get("bbo"), dict) else None
    if bbo:
        bid = _finite_float(bbo.get("bid_price"))
        ask = _finite_float(bbo.get("ask_price"))
        if bid and ask:
            return (bid + ask) / 2
    return None


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


def _market_data_health(stream: dict[str, Any]) -> dict[str, Any]:
    market_data = stream.get("marketData") if isinstance(stream.get("marketData"), dict) else {}
    return {
        "provider": market_data.get("provider"),
        "status": market_data.get("status"),
        "permission": market_data.get("permission"),
        "lastError": market_data.get("lastError"),
        "latencyMs": market_data.get("latencyMs") or market_data.get("latency_ms"),
    }


def _stream_quote_for_action(
    action: dict[str, Any], stream: dict[str, Any] | None
) -> tuple[str | None, dict[str, Any] | None]:
    if not stream:
        return None, None
    contract = action.get("contract") or {}
    massive_option_symbol = option_display_to_massive_symbol(
        contract.get("display") or contract.get("identifier") or action.get("display")
    )
    if action.get("sec_type") == "OPT":
        candidates = [
            action.get("market_data_symbol"),
            massive_option_symbol,
            contract.get("identifier"),
            contract.get("display"),
            action.get("display"),
        ]
    else:
        candidates = [
            action.get("market_data_symbol"),
            massive_option_symbol,
            contract.get("identifier"),
            contract.get("display"),
            action.get("display"),
            action.get("symbol"),
        ]
    quotes = stream.get("quotes") or {}
    for candidate in candidates:
        if candidate and quotes.get(str(candidate)):
            return str(candidate), quotes[str(candidate)]
    return None, None


def _stream_bbo_for_symbol(symbol: str | None, stream: dict[str, Any] | None) -> dict[str, Any] | None:
    if not symbol or not stream:
        return None
    bbo = stream.get("bbo") or {}
    value = bbo.get(symbol)
    return value if isinstance(value, dict) else None


def _bid_ask_from_quote(
    symbol: str | None,
    quote: dict[str, Any],
    stream: dict[str, Any] | None,
) -> dict[str, float | None]:
    bbo = _stream_bbo_for_symbol(symbol, stream)
    nested_bbo = quote.get("bbo") if isinstance(quote.get("bbo"), dict) else {}
    bid = _as_float((bbo or {}).get("bid_price") or nested_bbo.get("bid_price") or quote.get("bid_price"))
    ask = _as_float((bbo or {}).get("ask_price") or nested_bbo.get("ask_price") or quote.get("ask_price"))
    bid_size = _as_float((bbo or {}).get("bid_size") or nested_bbo.get("bid_size") or quote.get("bid_size"))
    ask_size = _as_float((bbo or {}).get("ask_size") or nested_bbo.get("ask_size") or quote.get("ask_size"))
    midpoint = (bid + ask) / 2 if bid > 0 and ask > 0 and ask >= bid else None
    spread = ask - bid if midpoint is not None else None
    spread_pct = spread / midpoint if spread is not None and midpoint else None
    return {
        "bid": bid if bid > 0 else None,
        "ask": ask if ask > 0 else None,
        "bidSize": bid_size if bid_size > 0 else None,
        "askSize": ask_size if ask_size > 0 else None,
        "mid": midpoint,
        "spread": spread,
        "spreadPct": spread_pct,
    }


def _suggested_limit(action: dict[str, Any], prices: dict[str, float | None]) -> float | None:
    bid = prices.get("bid")
    ask = prices.get("ask")
    mid = prices.get("mid")
    side = str(action.get("action") or "").upper()
    if side == "BUY":
        value = mid or ask
    elif side == "SELL":
        value = mid or bid
    else:
        value = None
    return round(float(value), 2) if value else None


def _asset_class_for_action(action: dict[str, Any]) -> str:
    sec_type = str(action.get("sec_type") or "").upper()
    if sec_type == "OPT":
        return "us_option"
    side = str(action.get("action") or "").upper()
    if action.get("short_sell") is True or side in {"SHORT", "SHORT_SELL"}:
        return "short"
    if action.get("margin") is True:
        return "margin"
    classification = str(action.get("asset_class") or "").lower()
    if classification in _SUPPORTED_ASSET_CLASSES:
        return classification
    return "us_stock"


def check_asset_class_allowed(
    action: dict[str, Any], allow: Iterable[str] | None
) -> dict[str, Any]:
    allowed = {str(item).strip().lower() for item in (allow or []) if str(item).strip()}
    required = _asset_class_for_action(action)
    if required in allowed:
        return {"ok": True, "status": "asset_class_allowed", "asset_class": required, "allow": sorted(allowed)}
    return {
        "ok": False,
        "status": "asset_class_not_allowed",
        "message": (
            f"该监控组未授权 {required} 类别；"
            f"当前允许：{', '.join(sorted(allowed)) or '<空>'}。"
        ),
        "asset_class": required,
        "allow": sorted(allowed),
    }


def run_preflight_gates(
    action: dict[str, Any],
    stream: dict[str, Any] | None,
    *,
    thresholds: GateThresholds | None = None,
    asset_class_allow: Iterable[str] | None = None,
) -> dict[str, Any]:
    """端到端跑 13+ 道实时行情闸门，决定是否允许进入 broker 下单路径。

    返回 Omega 稳定的 stream preflight 字段：
    ``ok`` (bool)、``status`` (str)、``message`` (str)、附加上下文字段。
    """

    thresholds = thresholds or GateThresholds()
    side = str(action.get("action") or "").upper()
    if action.get("approval_required") is False or side not in {"BUY", "SELL"}:
        return {"ok": True, "status": "not_required", "message": "该建议不需要实时行情闸门。"}

    if asset_class_allow is not None:
        asset_check = check_asset_class_allowed(action, asset_class_allow)
        if not asset_check["ok"]:
            return asset_check

    if not stream:
        return {"ok": False, "status": "missing_stream", "message": "没有实时推送数据，不能进入真实下单。"}
    if not stream.get("connected"):
        return {"ok": False, "status": "disconnected", "message": "Tiger Push 未连接，不能进入真实下单。"}

    stream_age = stream.get("ageSeconds")
    if stream_age is None:
        stream_age = data_age_seconds(stream.get("lastEventAtMs"))
    if stream_age is None or stream_age > thresholds.stream_hard_stale_after_seconds:
        return {
            "ok": False,
            "status": "stream_stale",
            "message": "Tiger Push 没有足够新的事件，不能进入真实下单。",
            "streamAgeSeconds": stream_age,
        }

    symbol, quote = _stream_quote_for_action(action, stream)
    if not quote:
        market_health = _market_data_health(stream)
        return {
            "ok": False,
            "status": "missing_quote",
            "message": f"{action.get('display') or action.get('symbol')} 没有实时行情，不能进入真实下单。",
            "streamAgeSeconds": stream_age,
            "marketData": market_health,
            "snapshotPrice": action.get("snapshotPrice"),
            "snapshotQueriedAt": action.get("snapshotQueriedAt"),
        }

    quote_age = data_age_seconds(quote.get("received_at_ms"))
    if quote_age is None or quote_age > thresholds.stream_stale_after_seconds:
        return {
            "ok": False,
            "status": "quote_stale",
            "message": f"{symbol} 实时行情超过 {thresholds.stream_stale_after_seconds:.0f} 秒未更新。",
            "streamAgeSeconds": stream_age,
            "quoteAgeSeconds": quote_age,
        }

    latest_price = quote_latest_price(quote)
    if latest_price is None or latest_price <= 0:
        return {
            "ok": False,
            "status": "bad_quote",
            "message": f"{symbol} 实时行情没有有效价格，不能进入真实下单。",
            "quoteAgeSeconds": quote_age,
        }
    if quote.get("execution_grade") is False:
        return {
            "ok": False,
            "status": "quote_not_execution_grade",
            "message": f"{symbol} 当前行情源不是交易级实时源，不能进入真实下单。",
            "quoteAgeSeconds": quote_age,
            "source": quote.get("source"),
            "dataQuality": quote.get("data_quality"),
        }
    market_health = _market_data_health(stream)
    quote_permission = str(quote.get("permission") or market_health.get("permission") or "unknown")
    if quote_permission in {"delayed", "missing_permission", "missing_key", "unsupported"}:
        return {
            "ok": False,
            "status": f"quote_{quote_permission}",
            "message": f"{symbol} 当前不是实时可执行行情，不能进入真实下单。",
            "quoteAgeSeconds": quote_age,
            "source": quote.get("source"),
            "permission": quote_permission,
            "marketData": market_health,
        }

    prices = _bid_ask_from_quote(symbol, quote, stream)
    if not prices["bid"] or not prices["ask"]:
        return {
            "ok": False,
            "status": "missing_bid_ask",
            "message": f"{symbol} 没有完整 bid/ask，不能进入真实下单。",
            "latestPrice": latest_price,
            "quoteAgeSeconds": quote_age,
            "source": quote.get("source"),
        }
    if not prices["bidSize"] or not prices["askSize"]:
        return {
            "ok": False,
            "status": "empty_top_of_book",
            "message": f"{symbol} 最优买卖盘 size 为空，先不要自动提交。",
            "latestPrice": latest_price,
            "quoteAgeSeconds": quote_age,
            "bid": prices["bid"],
            "ask": prices["ask"],
        }

    if action.get("sec_type") == "OPT":
        option_symbol = option_display_to_massive_symbol(
            (action.get("contract") or {}).get("display")
            or (action.get("contract") or {}).get("identifier")
            or action.get("display")
            or symbol
        )
        if symbol != option_symbol and not str(symbol or "").startswith("O:"):
            return {
                "ok": False,
                "status": "option_symbol_mismatch",
                "message": f"{action.get('display')} 没有匹配到 Massive/OPRA 期权合约行情。",
                "quoteSymbol": symbol,
                "expectedSymbol": option_symbol,
            }
        if quote.get("feed") not in {"opra", None} and quote.get("asset_class") != "options":
            return {
                "ok": False,
                "status": "option_not_opra",
                "message": f"{symbol} 当前不是 OPRA 期权实时行情，不能用于期权下单确认。",
                "feed": quote.get("feed"),
                "assetClass": quote.get("asset_class"),
            }

    spread_pct = prices["spreadPct"]
    max_spread = (
        thresholds.max_option_spread_pct if action.get("sec_type") == "OPT" else thresholds.max_stock_spread_pct
    )
    if spread_pct is not None and spread_pct > max_spread:
        return {
            "ok": False,
            "status": "spread_too_wide",
            "message": f"{symbol} 买卖价差过宽，先不要自动提交。",
            "latestPrice": latest_price,
            "quoteAgeSeconds": quote_age,
            "spreadPct": spread_pct,
            "maxSpreadPct": max_spread,
            "bid": prices["bid"],
            "ask": prices["ask"],
        }

    return {
        "ok": True,
        "status": "passed",
        "message": "实时行情新鲜，可以进入订单预览。",
        "symbol": symbol,
        "latestPrice": latest_price,
        "bid": prices["bid"],
        "ask": prices["ask"],
        "mid": prices["mid"],
        "bidSize": prices["bidSize"],
        "askSize": prices["askSize"],
        "suggestedLimitPrice": _suggested_limit(action, prices),
        "quoteAgeSeconds": quote_age,
        "latencyMs": quote.get("latency_ms"),
        "spreadPct": spread_pct,
        "source": quote.get("source"),
        "feed": quote.get("feed"),
    }
