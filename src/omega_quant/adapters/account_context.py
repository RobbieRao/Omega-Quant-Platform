from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..broker.account_stream import apply_stream_updates, read_stream_cache
from ..broker.tigeropen_client import configured_tiger_account
from ..config import Settings
from ..core.option_pricing import cached_fast_vollib_greeks, option_contract_snapshot_from_position, parse_option_contract
from ..research.portfolio import build_research_report
from ..research.risk import assess_snapshot
from .massive_client import fetch_options_greeks
from .platform_context import (
    canonical_option_symbol,
    fetch_real_account_snapshot_readonly,
    group_market_quotes,
    option_underlying,
    select_option_mark_price,
)


DEFAULT_SNAPSHOT_PATH = Path("reports/json/latest_snapshot.json")
DEFAULT_STREAM_PATH = Path("reports/json/live_stream.json")
OPTION_GREEKS_TTL_SECONDS = 30.0
_OPTION_GREEKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def get_real_account_snapshot() -> dict[str, Any]:
    snapshot_result = _load_snapshot_result()
    snapshot = snapshot_result.get("snapshot") if isinstance(snapshot_result.get("snapshot"), dict) else None
    positions = _positions_from_snapshot(snapshot)
    orders = get_real_orders_readonly(snapshot=snapshot)
    totals = _account_totals(snapshot)
    errors = list(snapshot_result.get("errors") or [])
    if snapshot and isinstance(snapshot.get("errors"), dict):
        errors.extend(str(value) for value in snapshot["errors"].values() if value)
    available = bool(snapshot and (snapshot.get("assets") or positions))
    return {
        "account_view": "real_readonly",
        "execution_mode": "live",
        "live_trading": "enabled",
        "available": available,
        "read_only": True,
        "source": snapshot_result.get("source") or "unavailable",
        "broker_source": "Tiger/Massive readonly context",
        "account": _snapshot_account(snapshot) or _mask_account(configured_tiger_account()),
        "queried_at": (snapshot or {}).get("queried_at"),
        "real_account_equity": totals.get("net_liquidation"),
        "net_liquidation": totals.get("net_liquidation"),
        "real_cash": totals.get("cash_balance"),
        "buying_power": totals.get("buying_power"),
        "positions_count": len(positions),
        "orders_count": len(orders),
        "errors": errors,
        "message": (
            "真实账户只读视图：这里只展示账户上下文；交易提交使用 TigerOpen 当前配置账户。"
            if available
            else "真实账户只读数据暂不可用；TigerOpen 执行账户配置不受影响。"
        ),
    }


def get_real_positions(snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if snapshot is None:
        result = _load_snapshot_result()
        snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else None
    return _positions_from_snapshot(snapshot)


def get_real_position_groups_readonly(snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    positions = get_real_positions(snapshot=snapshot)
    groups: dict[str, dict[str, Any]] = {}

    def ensure_group(underlying: str) -> dict[str, Any]:
        key = underlying.strip().upper() or "--"
        if key not in groups:
            groups[key] = {
                "underlying": key,
                "stock_positions": [],
                "option_positions": [],
                "standalone_positions": [],
                "total_market_value": 0.0,
                "total_unrealized_pnl": 0.0,
                "readonly_badge": "readonly_real_account_group",
            }
        return groups[key]

    for position in positions:
        sec_type = str(position.get("sec_type") or "").upper()
        display = str(position.get("display") or position.get("symbol") or "")
        symbol = str(position.get("symbol") or "").upper()
        option_symbol = canonical_option_symbol(display) or canonical_option_symbol(symbol)
        if sec_type == "OPT" or option_symbol:
            underlying = option_underlying(option_symbol or display) or symbol or "--"
            enriched = {**position, "canonical_option_symbol": option_symbol}
            ensure_group(underlying)["option_positions"].append(enriched)
        elif symbol:
            ensure_group(symbol)["stock_positions"].append(position)
        else:
            ensure_group("--")["standalone_positions"].append(position)

    for group in groups.values():
        grouped_positions = group["stock_positions"] + group["option_positions"] + group["standalone_positions"]
        group["total_market_value"] = sum(float(position.get("market_value") or 0.0) for position in grouped_positions)
        group["total_unrealized_pnl"] = sum(float(position.get("unrealized_pnl") or 0.0) for position in grouped_positions)
        group["position_count"] = len(grouped_positions)
        group["option_greeks_exposure"] = _aggregate_option_greeks(group["option_positions"])

    return [groups[key] for key in sorted(groups)]


def get_real_stream_detail_readonly() -> dict[str, Any]:
    stream = _load_json(_stream_path()) or {}
    market = stream.get("marketData") if isinstance(stream.get("marketData"), dict) else {}
    subscriptions = stream.get("subscriptions") if isinstance(stream.get("subscriptions"), dict) else {}
    quotes = stream.get("quotes") if isinstance(stream.get("quotes"), dict) else {}
    bbo = stream.get("bbo") if isinstance(stream.get("bbo"), dict) else {}
    return {
        "source": "live_stream_cache" if stream else "unavailable",
        "read_only": True,
        "status": stream.get("status") or ("cached" if stream else "unavailable"),
        "connected": bool(stream.get("connected")),
        "freshness": stream.get("freshness") or "unknown",
        "updated_at": stream.get("lastEventAt") or stream.get("updatedAt"),
        "sequence": stream.get("sequence"),
        "subscriptions": subscriptions,
        "market_data": {
            "provider": market.get("provider"),
            "label": market.get("label"),
            "permission": market.get("permission"),
            "status": market.get("status"),
            "connected": market.get("connected"),
            "subscribe_quotes": market.get("subscribeQuotes"),
            "subscribe_trades": market.get("subscribeTrades"),
            "viewer_count": market.get("viewerCount"),
            "paused_by_no_viewers": market.get("pausedByNoViewers"),
            "pause_without_viewers_seconds": market.get("pauseWithoutViewersSeconds"),
            "age_seconds": market.get("ageSeconds"),
            "freshness": market.get("freshness"),
            "rate_limits": market.get("rateLimits"),
        },
        "quote_groups": group_market_quotes(
            quotes,
            bbo,
            subscribed_stocks=subscriptions.get("quotes") or subscriptions.get("stocks") or [],
            subscribed_options=subscriptions.get("options") or [],
        ),
        "assets": _dict_values(stream.get("assets")),
        "positions": _dict_values(stream.get("positions")),
        "orders": _dict_values(stream.get("orders")),
        "transactions": _dict_values(stream.get("transactions")),
        "recent_events": list(stream.get("events") or [])[-25:] if isinstance(stream.get("events"), list) else [],
        "message": "Omega live stream cache is available as read-only supervision context.",
    }


def get_real_account_risk_readonly() -> dict[str, Any]:
    snapshot_result = _load_snapshot_result()
    snapshot = snapshot_result.get("snapshot") if isinstance(snapshot_result.get("snapshot"), dict) else None
    if not snapshot:
        return {
            "source": snapshot_result.get("source") or "unavailable",
            "read_only": True,
            "available": False,
            "account": None,
            "net_liquidation": None,
            "cash_balance": None,
            "buying_power": None,
            "gross_position_value": None,
            "total_today_pl": None,
            "total_today_pl_source": None,
            "option_market_value_abs": None,
            "option_weight": None,
            "option_greeks_exposure": _empty_option_greeks_exposure(),
            "flags": [],
            "positions": [],
            "message": "真实账户风险上下文暂不可用。",
        }
    try:
        stream = read_stream_cache(_stream_path(), Settings())
        assessed_snapshot = apply_stream_updates(snapshot, stream)
        assessment = assess_snapshot(assessed_snapshot, Settings())
    except Exception as exc:
        return {
            "source": snapshot_result.get("source") or "cached_snapshot",
            "read_only": True,
            "available": False,
            "account": None,
            "net_liquidation": None,
            "cash_balance": None,
            "buying_power": None,
            "gross_position_value": None,
            "total_today_pl": None,
            "total_today_pl_source": None,
            "option_market_value_abs": None,
            "option_weight": None,
            "option_greeks_exposure": _empty_option_greeks_exposure(),
            "flags": [str(exc)],
            "positions": [],
            "message": "真实账户风险上下文计算失败；Omega 纸面交易不受影响。",
        }
    positions = get_real_positions(snapshot=snapshot)
    return {
        "source": "omega_risk_readonly",
        "read_only": True,
        "available": True,
        "account": assessment.get("account"),
        "net_liquidation": _number(assessment.get("net_liquidation")),
        "cash_balance": _number(assessment.get("cash_balance")),
        "buying_power": _number(assessment.get("buying_power")),
        "gross_position_value": _number(assessment.get("gross_position_value")),
        "total_today_pl": _number(assessment.get("total_today_pl")),
        "total_today_pl_source": assessment.get("total_today_pl_source"),
        "option_market_value_abs": _number(assessment.get("option_market_value_abs")),
        "option_weight": _number(assessment.get("option_weight")),
        "option_greeks_exposure": _aggregate_option_greeks(positions),
        "flags": list(assessment.get("flags") or []),
        "positions": list(assessment.get("positions") or []),
        "message": "真实账户风险只读观察：用于监督和解释，不会生成或修改任何订单。",
    }


def get_portfolio_research_report_readonly() -> dict[str, Any]:
    if os.environ.get("OMEGA_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
        from ..research.demo import synthetic_demo_research_report

        return {
            "source": "omega_synthetic_demo",
            "read_only": True,
            "available": True,
            "generated_at": utc_timestamp(),
            "report": synthetic_demo_research_report(),
            "errors": [],
            "message": "Credential-free deterministic portfolio research demo.",
        }
    snapshot_result = _load_snapshot_result()
    snapshot = snapshot_result.get("snapshot") if isinstance(snapshot_result.get("snapshot"), dict) else None
    if not snapshot:
        return {
            "source": snapshot_result.get("source") or "unavailable",
            "read_only": True,
            "available": False,
            "generated_at": utc_timestamp(),
            "report": {},
            "errors": list(snapshot_result.get("errors") or []),
            "message": "Omega research report is unavailable because no account snapshot is cached.",
        }
    try:
        assessment = get_real_account_risk_readonly()
        report = build_research_report(snapshot, assessment, settings=Settings())
        return {
            "source": "omega_research_report_readonly",
            "read_only": True,
            "available": True,
            "generated_at": utc_timestamp(),
            "report": report,
            "errors": list(snapshot_result.get("errors") or []),
            "message": "Omega portfolio research was generated without creating or changing orders.",
        }
    except Exception as exc:
        return {
            "source": snapshot_result.get("source") or "cached_snapshot",
            "read_only": True,
            "available": False,
            "generated_at": utc_timestamp(),
            "report": {},
            "errors": [*list(snapshot_result.get("errors") or []), str(exc)],
            "message": "Omega portfolio research could not be generated; no order was created.",
        }


def get_real_orders_readonly(snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    stream = _load_json(_stream_path()) or {}
    raw_orders = []
    if snapshot and isinstance(snapshot.get("orders"), list):
        raw_orders.extend(snapshot.get("orders") or [])
    if isinstance(stream.get("orders"), dict):
        raw_orders.extend(stream["orders"].values())
    elif isinstance(stream.get("orders"), list):
        raw_orders.extend(stream["orders"])
    return [_normalise_order(order) for order in raw_orders if isinstance(order, dict)]


def get_real_market_context() -> dict[str, Any]:
    stream = _load_json(_stream_path()) or {}
    market = stream.get("marketData") if isinstance(stream.get("marketData"), dict) else {}
    return {
        "source": "live_stream_cache" if stream else "unavailable",
        "read_only": True,
        "market_data": market,
        "updated_at": stream.get("lastEventAt") or stream.get("updatedAt"),
    }


def get_real_stream_status_readonly() -> dict[str, Any]:
    stream = _load_json(_stream_path()) or {}
    counts = {
        "assets": _collection_size(stream.get("assets")),
        "positions": _collection_size(stream.get("positions")),
        "orders": _collection_size(stream.get("orders")),
        "transactions": _collection_size(stream.get("transactions")),
        "quotes": _collection_size(stream.get("quotes")),
        "bbo": _collection_size(stream.get("bbo")),
        "ticks": _collection_size(stream.get("ticks")),
    }
    market = stream.get("marketData") if isinstance(stream.get("marketData"), dict) else {}
    return {
        "source": "live_stream_cache" if stream else "unavailable",
        "read_only": True,
        "status": stream.get("status") or ("cached" if stream else "unavailable"),
        "connected": bool(stream.get("connected")),
        "freshness": stream.get("freshness") or "unknown",
        "age_seconds": _number(stream.get("ageSeconds")),
        "updated_at": stream.get("lastEventAt") or stream.get("updatedAt"),
        "sequence": stream.get("sequence"),
        "counts": counts,
        "market_data": {
            "provider": market.get("provider"),
            "label": market.get("label"),
            "subscribe_quotes": market.get("subscribeQuotes"),
            "subscribe_trades": market.get("subscribeTrades"),
            "viewer_count": market.get("viewerCount"),
        },
        "message": "真实账户推送流只读上下文；Omega 不会通过该流提交或修改订单。",
    }


def _load_snapshot_result() -> dict[str, Any]:
    errors: list[str] = []
    if os.environ.get("OMEGA_REAL_READONLY_FETCH_LIVE") == "1":
        try:
            return {"snapshot": fetch_real_account_snapshot_readonly(), "source": "tiger_snapshot", "errors": []}
        except Exception as exc:  # pragma: no cover - depends on local Tiger credentials/network
            errors.append(str(exc))

    snapshot_path = _snapshot_path()
    cached = _load_json(snapshot_path)
    if isinstance(cached, dict):
        account_error = _snapshot_account_mismatch(cached)
        if account_error:
            errors.append(account_error)
            return {"snapshot": None, "source": "stale_cached_snapshot", "errors": errors}
        return {"snapshot": cached, "source": "cached_snapshot", "errors": errors}
    if not snapshot_path.exists():
        errors.append(f"cached snapshot not found: {snapshot_path}")
    else:
        errors.append(f"cached snapshot is not a JSON object: {snapshot_path}")
    return {"snapshot": None, "source": "unavailable", "errors": errors}


def _snapshot_account(snapshot: dict[str, Any] | None) -> str | None:
    account = (((snapshot or {}).get("assets") or {}).get("account"))
    return str(account) if account else None


def _mask_account(account: str | None) -> str | None:
    if not account:
        return None
    cleaned = str(account).strip()
    if len(cleaned) <= 4:
        return cleaned
    return f"{cleaned[:2]}****{cleaned[-4:]}"


def _snapshot_account_mismatch(snapshot: dict[str, Any]) -> str | None:
    configured = configured_tiger_account()
    cached_account = _snapshot_account(snapshot)
    if not configured or not cached_account:
        return None
    configured_clean = str(configured).strip()
    cached_clean = str(cached_account).strip()
    if cached_clean == configured_clean or cached_clean.endswith(configured_clean[-4:]):
        return None
    return (
        "cached snapshot account does not match configured TigerOpen account: "
        f"cached={_mask_account(cached_clean)}, configured={_mask_account(configured_clean)}"
    )


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def _snapshot_path() -> Path:
    configured = os.environ.get("OMEGA_REAL_READONLY_SNAPSHOT_PATH")
    return Path(configured) if configured else DEFAULT_SNAPSHOT_PATH


def _stream_path() -> Path:
    configured = os.environ.get("OMEGA_REAL_READONLY_STREAM_PATH") or os.environ.get("OMEGA_STREAM_CACHE_PATH")
    return Path(configured) if configured else DEFAULT_STREAM_PATH


def _positions_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sec_type, positions in ((snapshot or {}).get("positions") or {}).items():
        for position in positions or []:
            if isinstance(position, dict):
                rows.append(_normalise_position(sec_type, position))
    rows = _enrich_option_positions_with_greeks(rows)
    return _enrich_option_positions_with_fast_vollib(rows)


def _normalise_position(sec_type: str, position: dict[str, Any]) -> dict[str, Any]:
    contract = position.get("contract") if isinstance(position.get("contract"), dict) else {}
    symbol = contract.get("symbol") or position.get("symbol")
    display = contract.get("display") or contract.get("identifier") or position.get("display") or symbol
    option_symbol = canonical_option_symbol(str(display or "")) or canonical_option_symbol(str(symbol or ""))
    option_context = _option_contract_context(option_symbol or str(display or ""), position)
    return {
        "account": position.get("account"),
        "sec_type": sec_type,
        "symbol": symbol,
        "display": display,
        "currency": contract.get("currency") or position.get("currency"),
        "quantity": _number(position.get("quantity") or position.get("position_qty")),
        "average_cost": _number(position.get("average_cost")),
        "market_price": _number(position.get("market_price")),
        "market_value": _number(position.get("market_value")),
        "unrealized_pnl": _number(position.get("unrealized_pnl")),
        "today_pnl": _number(position.get("today_pnl")),
        "canonical_option_symbol": option_symbol,
        **option_context,
        "delta": _number(position.get("delta")),
        "gamma": _number(position.get("gamma")),
        "theta": _number(position.get("theta")),
        "vega": _number(position.get("vega")),
        "iv": _number(position.get("iv")),
        "open_interest": _number(position.get("open_interest")),
        "break_even_price": _number(position.get("break_even_price")),
        "option_bid": _number(position.get("option_bid") or position.get("bid")),
        "option_ask": _number(position.get("option_ask") or position.get("ask")),
        "greeks_source": position.get("greeks_source"),
        "greeks_updated_at": position.get("greeks_updated_at"),
        "source": "omega_account_context",
        "readonly_badge": "readonly_real_account",
        "raw_context": {
            "contract": contract,
            "salable_qty": position.get("salable_qty"),
            "unrealized_pnl_percent": position.get("unrealized_pnl_percent"),
            "today_pnl_percent": position.get("today_pnl_percent"),
        },
    }


def _option_contract_context(value: str | None, position: dict[str, Any]) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = parse_option_contract(value)
    except Exception:
        parsed = None
    if not parsed:
        return {}

    expiry = str(parsed.get("expiry") or "")
    days_to_expiry = None
    try:
        expiry_date = datetime.strptime(expiry, "%Y%m%d").date()
        days_to_expiry = max((expiry_date - datetime.now(timezone.utc).date()).days, 0)
    except Exception:
        pass

    strike = _number(parsed.get("strike"))
    underlying_price = _number(
        position.get("underlying_price")
        or position.get("underlying_market_price")
        or position.get("stock_price")
    )
    moneyness = None
    if underlying_price and strike and strike > 0:
        if parsed.get("put_call") == "P":
            moneyness = (strike / underlying_price) - 1.0
        else:
            moneyness = (underlying_price / strike) - 1.0

    return {
        "option_underlying": parsed.get("underlying"),
        "option_expiry": expiry or None,
        "option_put_call": parsed.get("put_call"),
        "option_strike": strike,
        "days_to_expiry": days_to_expiry,
        "option_moneyness": moneyness,
    }


def _enrich_option_positions_with_greeks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contracts = sorted(
        {
            str(row.get("canonical_option_symbol"))
            for row in rows
            if row.get("canonical_option_symbol") and str(row.get("sec_type") or "").upper() == "OPT"
        }
    )
    if not contracts or not _option_greeks_enabled():
        return rows

    greeks = _cached_option_greeks(contracts)
    updated_at = datetime.now(timezone.utc).isoformat()
    for row in rows:
        contract = row.get("canonical_option_symbol")
        if not contract or contract not in greeks:
            continue
        data = greeks[contract]
        row.update(
            {
                "delta": _number(data.get("delta")),
                "gamma": _number(data.get("gamma")),
                "theta": _number(data.get("theta")),
                "vega": _number(data.get("vega")),
                "iv": _number(data.get("iv")),
                "open_interest": _number(data.get("open_interest")),
                "break_even_price": _number(data.get("break_even_price")),
                "option_bid": _number(data.get("bid")),
                "option_ask": _number(data.get("ask")),
                "greeks_source": "massive_rest_option_snapshot",
                "greeks_updated_at": updated_at,
            }
        )
        raw_context = row.setdefault("raw_context", {})
        if isinstance(raw_context, dict):
            raw_context["greeks"] = {
                "source": "massive_rest_option_snapshot",
                "contract": contract,
                "updated_at": updated_at,
            }
    return rows


def _enrich_option_positions_with_fast_vollib(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stream = _load_json(_stream_path()) or {}
    quotes = stream.get("quotes") if isinstance(stream.get("quotes"), dict) else {}
    bbo = stream.get("bbo") if isinstance(stream.get("bbo"), dict) else {}
    ticks = stream.get("ticks") if isinstance(stream.get("ticks"), dict) else {}
    if not rows:
        return rows
    stock_prices = {
        str(row.get("symbol") or "").upper(): _number(row.get("market_price"))
        for row in rows
        if str(row.get("sec_type") or "").upper() == "STK"
    }
    for row in rows:
        if str(row.get("sec_type") or "").upper() != "OPT":
            continue
        canonical = str(row.get("canonical_option_symbol") or "")
        if not canonical:
            continue
        underlying = option_underlying(canonical) or str(row.get("symbol") or "").upper()
        mark = _option_stream_mark(canonical, quotes, bbo, ticks)
        option_price = _number((mark or {}).get("mark_price")) or _number(row.get("market_price"))
        underlying_price = _stream_latest_price(underlying, quotes, bbo, ticks) or stock_prices.get(underlying)
        if option_price is None or underlying_price is None:
            continue
        try:
            position_payload = {"contract": row.get("raw_context", {}).get("contract", {})}
            contract = option_contract_snapshot_from_position(
                position_payload,
                option_price=option_price,
                underlying_price=underlying_price,
            )
            if contract is None:
                continue
            greeks = cached_fast_vollib_greeks(canonical, contract, mark=mark or {})
        except Exception as exc:
            row["greeks_status"] = "error"
            row["greeks_error"] = str(exc)
            continue
        row["mark_price"] = _number(greeks.get("mark_price")) or option_price
        row["mark_source"] = greeks.get("mark_source")
        row["greeks_engine"] = greeks.get("engine")
        row["greeks_status"] = greeks.get("status")
        row["greeks_error"] = greeks.get("error")
        if greeks.get("status") == "ok":
            row["iv"] = _number(greeks.get("iv"))
            row["delta"] = _number(greeks.get("delta"))
            row["gamma"] = _number(greeks.get("gamma"))
            row["theta"] = _number(greeks.get("theta"))
            row["vega"] = _number(greeks.get("vega"))
            row["greeks_source"] = "fast_vollib_live_readonly"
            row["greeks_updated_at"] = datetime.now(timezone.utc).isoformat()
    return rows


def _option_stream_mark(
    canonical: str,
    quotes: dict[str, dict[str, Any]],
    bbo: dict[str, dict[str, Any]],
    ticks: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    aliases = {canonical, canonical.removeprefix("O:")}
    book = next((bbo.get(alias) for alias in aliases if isinstance(bbo.get(alias), dict)), None)
    quote = next((quotes.get(alias) for alias in aliases if isinstance(quotes.get(alias), dict)), None)
    trade = next((ticks.get(alias) for alias in aliases if isinstance(ticks.get(alias), dict)), None) or quote
    return select_option_mark_price(bbo=book or quote, last_trade=trade)


def _empty_option_greeks_exposure() -> dict[str, Any]:
    return {
        "available": False,
        "contracts": 0,
        "total_contracts_abs": 0.0,
        "delta_shares": 0.0,
        "gamma_shares": 0.0,
        "theta": 0.0,
        "vega": 0.0,
        "weighted_iv": None,
        "sources": [],
        "message": "暂无可聚合的期权 Greeks。",
    }


def _aggregate_option_greeks(positions: list[dict[str, Any]]) -> dict[str, Any]:
    exposure = _empty_option_greeks_exposure()
    weighted_iv_numerator = 0.0
    weighted_iv_denominator = 0.0
    sources: set[str] = set()
    contracts = 0
    for position in positions:
        if str(position.get("sec_type") or "").upper() != "OPT" and not position.get("canonical_option_symbol"):
            continue
        quantity = _number(position.get("quantity")) or 0.0
        multiplier = 100.0
        has_any_greek = False
        for key, target in (("delta", "delta_shares"), ("gamma", "gamma_shares"), ("theta", "theta"), ("vega", "vega")):
            value = _number(position.get(key))
            if value is None:
                continue
            exposure[target] = round(float(exposure[target]) + value * quantity * multiplier, 6)
            has_any_greek = True
        iv = _number(position.get("iv"))
        weight = abs(_number(position.get("market_value")) or quantity * (_number(position.get("mark_price")) or 0.0) * multiplier)
        if iv is not None and weight > 0:
            weighted_iv_numerator += iv * weight
            weighted_iv_denominator += weight
            has_any_greek = True
        source = position.get("greeks_engine") or position.get("greeks_source")
        if source:
            sources.add(str(source))
        if has_any_greek:
            contracts += 1
            exposure["total_contracts_abs"] = round(float(exposure["total_contracts_abs"]) + abs(quantity), 6)
    if contracts:
        exposure["available"] = True
        exposure["contracts"] = contracts
        exposure["sources"] = sorted(sources)
        exposure["weighted_iv"] = round(weighted_iv_numerator / weighted_iv_denominator, 6) if weighted_iv_denominator > 0 else None
        exposure["message"] = "只读期权 Greeks 组合暴露估算；用于监督真实账户上下文，不参与 Omega 执行。"
    return exposure


def _stream_latest_price(
    symbol: str,
    quotes: dict[str, dict[str, Any]],
    bbo: dict[str, dict[str, Any]],
    ticks: dict[str, dict[str, Any]],
) -> float | None:
    aliases = {symbol.upper(), symbol.upper().removeprefix("O:")}
    for collection in (ticks, quotes):
        for alias in aliases:
            item = collection.get(alias)
            if isinstance(item, dict):
                for key in ("latest_price", "price", "last_price", "last"):
                    value = _number(item.get(key))
                    if value is not None and value > 0:
                        return value
    for alias in aliases:
        item = bbo.get(alias)
        if isinstance(item, dict):
            bid = _number(item.get("bid_price") or item.get("bid"))
            ask = _number(item.get("ask_price") or item.get("ask"))
            if bid is not None and ask is not None and bid > 0 and ask >= bid:
                return (bid + ask) / 2
    return None


def _option_greeks_enabled() -> bool:
    raw = os.environ.get("OMEGA_REAL_READONLY_OPTION_GREEKS")
    if raw is not None and raw.strip().lower() in {"0", "false", "no", "off"}:
        return False
    settings = Settings()
    return bool(settings.resolved_option_greeks_enabled and settings.massive_api_key)


def _cached_option_greeks(contracts: list[str]) -> dict[str, dict[str, Any]]:
    now = time.monotonic()
    missing: list[str] = []
    result: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        cached = _OPTION_GREEKS_CACHE.get(contract)
        if cached and now - cached[0] <= OPTION_GREEKS_TTL_SECONDS:
            result[contract] = cached[1]
        else:
            missing.append(contract)
    if missing:
        fetched = _fetch_option_greeks_readonly(missing)
        for contract, data in fetched.items():
            _OPTION_GREEKS_CACHE[contract] = (now, data)
            result[contract] = data
    return result


def _fetch_option_greeks_readonly(contracts: list[str]) -> dict[str, dict[str, Any]]:
    try:
        return fetch_options_greeks(contracts, Settings())
    except Exception:
        return {}


def _normalise_order(order: dict[str, Any]) -> dict[str, Any]:
    contract = order.get("contract") if isinstance(order.get("contract"), dict) else {}
    return {
        "broker_order_id": str(order.get("id") or order.get("order_id") or order.get("broker_order_id") or ""),
        "symbol": order.get("symbol") or contract.get("symbol"),
        "side": order.get("action") or order.get("side"),
        "quantity": _number(order.get("quantity") or order.get("filled_quantity")),
        "status": order.get("status") or order.get("order_status"),
        "submitted_at": order.get("submitted_at") or order.get("created_at") or order.get("timestamp"),
        "filled_at": order.get("filled_at") or order.get("updated_at"),
        "source": "omega_account_context",
        "readonly_badge": "readonly_real_account",
        "raw_context": order,
    }


def _account_totals(snapshot: dict[str, Any] | None) -> dict[str, float | None]:
    segments = (((snapshot or {}).get("assets") or {}).get("segments") or {})
    totals = {"net_liquidation": 0.0, "cash_balance": 0.0, "buying_power": 0.0}
    seen = {"net_liquidation": False, "cash_balance": False, "buying_power": False}
    for segment in segments.values():
        if not isinstance(segment, dict):
            continue
        for key in totals:
            value = _number(segment.get(key))
            if value is not None:
                totals[key] += value
                seen[key] = True
    return {key: value if seen[key] else None for key, value in totals.items()}


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _collection_size(value: Any) -> int:
    if isinstance(value, dict | list | tuple | set):
        return len(value)
    return 0


def _dict_values(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
