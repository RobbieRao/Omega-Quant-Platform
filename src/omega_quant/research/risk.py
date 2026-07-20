from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..adapters.massive_stream import option_display_to_massive_symbol
from ..config import Settings
from ..utils import as_float, money, pct


HKT = ZoneInfo("Asia/Hong_Kong")
SEGMENT_STALE_SECONDS = 10.0

LEVERAGED_OR_INVERSE_ETFS = {
    "SOXS",
    "SOXL",
    "SQQQ",
    "TQQQ",
    "SPXS",
    "SPXL",
    "UVXY",
    "SVIX",
}


def segment_score(key: str, segment: dict[str, Any]) -> tuple[int, int, int, float]:
    normalized_key = str(key or "").upper()
    age_seconds = segment.get("stream_asset_age_seconds")
    age_value = as_float(age_seconds, default=0.0) if age_seconds is not None else None
    freshness_score = 0
    if age_seconds is not None:
        freshness_score = int(round(1_000_000 - as_float(age_seconds, default=1_000_000)))
    completeness = sum(
        1
        for value in (
            segment.get("net_liquidation"),
            segment.get("gross_position_value"),
            segment.get("cash_balance"),
            segment.get("buying_power"),
        )
        if value is not None
    )
    net_liq = as_float(segment.get("net_liquidation"))
    if normalized_key == "S" and (age_value is None or age_value <= SEGMENT_STALE_SECONDS):
        key_score = 3
    elif normalized_key in {"C", "F"}:
        key_score = 2
    elif normalized_key == "S":
        key_score = 1
    else:
        key_score = 1
    return key_score, completeness, int(round(net_liq * 100)), freshness_score


def security_segment(snapshot: dict[str, Any]) -> dict[str, Any]:
    segments = ((snapshot.get("assets") or {}).get("segments") or {})
    if not isinstance(segments, dict) or not segments:
        return {}
    candidates = [(key, value) for key, value in segments.items() if isinstance(value, dict)]
    if not candidates:
        return {}
    best_key, best_segment = max(candidates, key=lambda item: segment_score(item[0], item[1]))
    return best_segment


def all_positions(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    positions = snapshot.get("positions") or {}
    result: list[dict[str, Any]] = []
    for sec_type in ("STK", "OPT", "FUT"):
        for position in positions.get(sec_type, []) or []:
            copy = dict(position)
            copy["sec_type_bucket"] = sec_type
            result.append(copy)
    return result


def option_days_to_expiry(position: dict[str, Any], today: date | None = None) -> int | None:
    contract = position.get("contract") or {}
    expiry = contract.get("expiry")
    if not expiry:
        return None
    try:
        expiry_date = datetime.strptime(str(expiry), "%Y%m%d").date()
    except ValueError:
        return None
    return (expiry_date - (today or date.today())).days


def snapshot_valuation_date(snapshot: dict[str, Any]) -> date:
    queried_at = snapshot.get("queried_at")
    if isinstance(queried_at, str):
        try:
            return datetime.fromisoformat(queried_at).astimezone(HKT).date()
        except ValueError:
            pass
    return datetime.now(HKT).date()


def revalue_segment_from_live_positions(snapshot: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any] | None:
    positions = all_positions(snapshot)
    live_market_value = sum(as_float(position.get("market_value")) for position in positions)
    if not positions or live_market_value == 0:
        return None

    cash_balance = as_float(segment.get("cash_balance"))
    live_segment = dict(segment)
    live_segment["gross_position_value"] = live_market_value
    live_segment["net_liquidation"] = cash_balance + live_market_value
    live_segment["unrealized_pl"] = sum(as_float(position.get("unrealized_pnl")) for position in positions)

    baseline_net_liq = segment.get("net_liquidation")
    baseline_today_pl = segment.get("total_today_pl")
    if baseline_net_liq is not None and baseline_today_pl is not None:
        baseline_start_value = as_float(baseline_net_liq) - as_float(baseline_today_pl)
        live_segment["total_today_pl"] = round(live_segment["net_liquidation"] - baseline_start_value, 4)
        live_segment["total_today_pl_source"] = "live_market_value_delta"

    live_segment["valuation_source"] = "live_positions"
    return live_segment


def assess_snapshot(
    snapshot: dict[str, Any],
    settings: Settings | None = None,
    greeks: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    greeks: optional map of {massive_option_symbol: {delta, gamma, theta, vega, iv, ...}}
    keyed by 'O:...' strings as returned by massive_client.fetch_options_greeks().
    """
    settings = settings or Settings()
    segment = security_segment(snapshot)
    positions = all_positions(snapshot)
    if snapshot.get("stream_priced") or snapshot.get("stream_position_updated"):
        live_segment = revalue_segment_from_live_positions(snapshot, segment)
        if live_segment is not None:
            segment = live_segment
    net_liq = as_float(segment.get("net_liquidation"))
    valuation_date = snapshot_valuation_date(snapshot)

    position_rows = []
    option_value = 0.0
    flags: list[str] = []
    for position in positions:
        contract = position.get("contract") or {}
        symbol = str(contract.get("symbol") or "")
        market_value = as_float(position.get("market_value"))
        weight = market_value / net_liq if net_liq else None
        is_option = position.get("sec_type_bucket") == "OPT"
        if is_option:
            option_value += abs(market_value)

        # Look up live greeks for options by Massive O: symbol format
        greek_data: dict[str, Any] = {}
        if is_option and greeks:
            identifier = contract.get("identifier") or contract.get("display") or ""
            massive_sym = option_display_to_massive_symbol(identifier)
            if massive_sym and massive_sym in greeks:
                greek_data = greeks[massive_sym]

        position_rows.append(
            {
                "symbol": symbol,
                "display": contract.get("display"),
                "sec_type": position.get("sec_type_bucket"),
                "quantity": position.get("quantity"),
                "market_value": market_value,
                "weight": weight,
                "today_pnl": as_float(position.get("today_pnl")),
                "today_pnl_source": position.get("today_pnl_source") or "rest_snapshot",
                "unrealized_pnl": as_float(position.get("unrealized_pnl")),
                "days_to_expiry": option_days_to_expiry(position, valuation_date),
                "leveraged_or_inverse": symbol in LEVERAGED_OR_INVERSE_ETFS,
                "delta": greek_data.get("delta"),
                "gamma": greek_data.get("gamma"),
                "theta": greek_data.get("theta"),
                "vega": greek_data.get("vega"),
                "iv": greek_data.get("iv"),
                "open_interest": greek_data.get("open_interest"),
                "mark_price": greek_data.get("mark_price"),
                "mark_source": greek_data.get("mark_source"),
                "last_trade_price": greek_data.get("last_trade_price"),
                "mid_price": greek_data.get("mid_price"),
                "greeks_engine": greek_data.get("engine"),
            }
        )

    option_weight = option_value / net_liq if net_liq else None
    if option_weight is not None and option_weight > settings.max_option_weight:
        flags.append(
            f"期权市值暴露 {pct(option_weight)}，高于预设上限 {pct(settings.max_option_weight)}。"
        )

    for row in position_rows:
        weight = row["weight"]
        if weight is not None and abs(weight) > settings.max_single_position_weight:
            flags.append(
                f"{row['symbol']} 单一仓位占比 {pct(abs(weight))}，高于预设上限 "
                f"{pct(settings.max_single_position_weight)}。"
            )
        if row["sec_type"] == "OPT" and row["days_to_expiry"] is not None:
            if row["days_to_expiry"] <= settings.short_dated_option_days:
                flags.append(
                    f"{row['display']} 距离到期仅剩 {row['days_to_expiry']} 天。"
                )
        if row["sec_type"] == "OPT" and row["leveraged_or_inverse"]:
            flags.append(
                f"{row['display']} 是杠杆/反向 ETF 的期权，应只作为事件型风险暴露处理。"
            )
        if row["sec_type"] == "OPT" and row.get("delta") is not None:
            if abs(row["delta"]) < 0.05:
                flags.append(
                    f"{row['display']} Delta 接近零（{row['delta']:.3f}），期权深度虚值，市值接近归零风险。"
                )
        if row["sec_type"] == "OPT" and row.get("iv") is not None:
            if row["iv"] > 1.0:
                flags.append(
                    f"{row['display']} 隐含波动率异常高（{row['iv'] * 100:.0f}%），风险极高。"
                )

    cash_balance = as_float(segment.get("cash_balance"))
    if cash_balance < 0:
        flags.append(f"证券账户现金余额为负：{money(cash_balance)}。")

    return {
        "account": (snapshot.get("assets") or {}).get("account"),
        "net_liquidation": net_liq,
        "gross_position_value": as_float(segment.get("gross_position_value")),
        "cash_balance": cash_balance,
        "cash_available_for_trade": as_float(segment.get("cash_available_for_trade")),
        "buying_power": as_float(segment.get("buying_power")),
        "leverage": as_float(segment.get("leverage")),
        "unrealized_pl": as_float(segment.get("unrealized_pl")),
        "total_today_pl": as_float(segment.get("total_today_pl")),
        "total_today_pl_source": segment.get("total_today_pl_source") or "rest_snapshot",
        "total_today_pl_age_seconds": segment.get("stream_asset_age_seconds"),
        "option_market_value_abs": option_value,
        "option_weight": option_weight,
        "positions": position_rows,
        "flags": flags,
    }


def render_console_summary(assessment: dict[str, Any]) -> str:
    lines = [
        f"Account: {assessment.get('account')}",
        f"Net liquidation: {money(assessment.get('net_liquidation'))}",
        f"Gross position value: {money(assessment.get('gross_position_value'))}",
        f"Cash balance: {money(assessment.get('cash_balance'))}",
        f"Option exposure: {money(assessment.get('option_market_value_abs'))} "
        f"({pct(assessment.get('option_weight'))})",
        "",
        "Positions:",
    ]
    for row in assessment.get("positions", []):
        expiry = ""
        if row.get("days_to_expiry") is not None:
            expiry = f", DTE {row['days_to_expiry']}"
        lines.append(
            f"- {row['display']}: {money(row['market_value'])}, "
            f"weight {pct(row['weight'])}, today {money(row['today_pnl'])}, "
            f"unrealized {money(row['unrealized_pnl'])}{expiry}"
        )
    if assessment.get("flags"):
        lines.extend(["", "Flags:"])
        lines.extend(f"- {flag}" for flag in assessment["flags"])
    return "\n".join(lines)
