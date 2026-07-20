"""vn.py-style pre-trade risk checks adapted for Omega.

Source reference:
- vn.py community RiskManager documentation describes pre-order checks for
  order flow, per-order volume, total trades, active orders, and per-symbol
  cancel limits.

Omega keeps the idea but adapts it to US stocks/options and TigerOpen live API:
no Qt UI, no China futures gateways, and no domestic exchange assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Iterable
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class VnpyRiskCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True)
class VnpyRiskResult:
    approved: bool
    reason: str
    checks: list[VnpyRiskCheck]


@dataclass(frozen=True)
class VnpyRiskConfig:
    active: bool = True
    order_flow_limit: int = 20
    order_flow_window_seconds: float = 1.0
    order_size_limit: float = 10_000.0
    trade_limit: int = 1_000
    active_order_limit: int = 100
    cancel_limit_per_symbol: int = 50
    price_band_pct: float = 0.10
    fat_finger_notional_limit: float = 250_000.0
    symbol_exposure_limit: float | None = None
    strategy_exposure_limit: float | None = None
    account_exposure_limit: float | None = None
    trading_timezone: str = "America/New_York"
    allowed_sessions: tuple[tuple[time, time], ...] = (
        (time(9, 30), time(16, 0)),
    )


@dataclass(frozen=True)
class VnpyRiskState:
    now: datetime
    order_timestamps: tuple[datetime, ...] = ()
    active_order_count: int = 0
    trades_today: int = 0
    cancel_counts_by_symbol: dict[str, int] = field(default_factory=dict)
    reference_price: float | None = None
    symbol_exposure: float = 0.0
    strategy_exposure: float = 0.0
    account_exposure: float = 0.0


def evaluate_vnpy_style_pretrade(
    *,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    config: VnpyRiskConfig,
    state: VnpyRiskState,
) -> VnpyRiskResult:
    """Evaluate vn.py RiskManager-style checks before TigerOpen submission."""

    if not config.active:
        return VnpyRiskResult(
            approved=True,
            reason="vn.py-style risk manager is disabled.",
            checks=[VnpyRiskCheck("risk_manager_active", True, "Risk manager disabled by config.")],
        )

    checks: list[VnpyRiskCheck] = []

    def add(name: str, passed: bool, message: str) -> None:
        checks.append(VnpyRiskCheck(name=name, passed=passed, message=message))

    notional = abs(quantity * price)
    signed_notional = _signed_notional(side, quantity, price)
    recent_order_count = _count_recent_orders(state.order_timestamps, state.now, config.order_flow_window_seconds)
    add(
        "order_flow_limit",
        recent_order_count < config.order_flow_limit,
        f"Order flow {recent_order_count}/{config.order_flow_limit} in {config.order_flow_window_seconds:.2f}s.",
    )
    add(
        "order_size_limit",
        abs(quantity) <= config.order_size_limit,
        f"Order quantity {quantity:g}, limit {config.order_size_limit:g}.",
    )
    add(
        "trade_limit",
        state.trades_today < config.trade_limit,
        f"Trades today {state.trades_today}/{config.trade_limit}.",
    )
    add(
        "active_order_limit",
        state.active_order_count < config.active_order_limit,
        f"Active orders {state.active_order_count}/{config.active_order_limit}.",
    )
    add(
        "cancel_limit_per_symbol",
        int(state.cancel_counts_by_symbol.get(symbol.upper(), 0)) < config.cancel_limit_per_symbol,
        f"{symbol.upper()} cancels today {int(state.cancel_counts_by_symbol.get(symbol.upper(), 0))}/{config.cancel_limit_per_symbol}.",
    )
    add(
        "fat_finger_notional",
        notional <= config.fat_finger_notional_limit,
        f"Order notional {notional:.2f}, fat-finger limit {config.fat_finger_notional_limit:.2f}.",
    )
    add(
        "trading_session",
        _in_allowed_session(state.now, config.allowed_sessions, config.trading_timezone),
        (
            f"Order time {state.now.astimezone(timezone.utc).isoformat()} outside configured "
            f"US trading session in {config.trading_timezone}."
        ),
    )

    if state.reference_price and state.reference_price > 0:
        band = abs(price / state.reference_price - 1.0)
        add(
            "price_band",
            band <= config.price_band_pct,
            f"Order price {price:.4f}, reference {state.reference_price:.4f}, band {band:.2%}/{config.price_band_pct:.2%}.",
        )

    _exposure_check(checks, "symbol_exposure", state.symbol_exposure, signed_notional, config.symbol_exposure_limit)
    _exposure_check(checks, "strategy_exposure", state.strategy_exposure, signed_notional, config.strategy_exposure_limit)
    _exposure_check(checks, "account_exposure", state.account_exposure, signed_notional, config.account_exposure_limit)

    approved = all(check.passed for check in checks)
    if approved:
        return VnpyRiskResult(True, "vn.py-style pre-trade checks passed.", checks)
    failed = next(check.message for check in checks if not check.passed)
    return VnpyRiskResult(False, failed, checks)


def _count_recent_orders(timestamps: Iterable[datetime], now: datetime, window_seconds: float) -> int:
    now_ts = now.timestamp()
    return sum(1 for item in timestamps if now_ts - item.timestamp() <= window_seconds)


def _in_allowed_session(now: datetime, sessions: tuple[tuple[time, time], ...], trading_timezone: str) -> bool:
    if not sessions:
        return True
    local_now = _to_trading_timezone(now, trading_timezone)
    local_t = local_now.time().replace(tzinfo=None)
    return any(start <= local_t <= end for start, end in sessions)


def _to_trading_timezone(now: datetime, trading_timezone: str) -> datetime:
    timestamp = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    try:
        return timestamp.astimezone(ZoneInfo(trading_timezone))
    except Exception:  # noqa: BLE001
        return timestamp.astimezone(timezone.utc)


def _exposure_check(
    checks: list[VnpyRiskCheck],
    name: str,
    current_exposure: float,
    signed_order_notional: float,
    limit: float | None,
) -> None:
    if limit is None:
        return
    next_exposure = current_exposure + signed_order_notional
    checks.append(
        VnpyRiskCheck(
            name=name,
            passed=abs(next_exposure) <= limit,
            message=f"{name} net after order {next_exposure:.2f}, abs limit {limit:.2f}.",
        )
    )


def _signed_notional(side: str, quantity: float, price: float) -> float:
    notional = abs(quantity * price)
    normalized = side.strip().lower()
    if normalized in {"sell", "short"}:
        return -notional
    return notional
