from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from ..config import Settings, load_env_file, mask_account


def clean_number(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def public_contract(contract: Any) -> dict[str, Any] | None:
    if contract is None:
        return None
    data: dict[str, Any] = {}
    for name in (
        "symbol",
        "sec_type",
        "currency",
        "market",
        "expiry",
        "strike",
        "put_call",
        "identifier",
    ):
        if hasattr(contract, name):
            value = getattr(contract, name)
            if value is not None:
                data[name] = str(value)
    data["display"] = str(contract)
    return data


def public_position(pos: Any, settings: Settings) -> dict[str, Any]:
    return {
        "account": mask_account(
            getattr(pos, "account", ""),
            settings.account_mask_prefix,
            settings.account_mask_suffix,
        ),
        "contract": public_contract(getattr(pos, "contract", None)),
        "quantity": clean_number(getattr(pos, "quantity", None)),
        "position_qty": clean_number(getattr(pos, "position_qty", None)),
        "salable_qty": clean_number(getattr(pos, "salable_qty", None)),
        "average_cost": clean_number(getattr(pos, "average_cost", None)),
        "market_price": clean_number(getattr(pos, "market_price", None)),
        "market_value": clean_number(getattr(pos, "market_value", None)),
        "unrealized_pnl": clean_number(getattr(pos, "unrealized_pnl", None)),
        "unrealized_pnl_percent": clean_number(getattr(pos, "unrealized_pnl_percent", None)),
        "today_pnl": clean_number(getattr(pos, "today_pnl", None)),
        "today_pnl_percent": clean_number(getattr(pos, "today_pnl_percent", None)),
    }


def public_currency_asset(asset: Any) -> dict[str, Any]:
    return {
        "currency": getattr(asset, "currency", None),
        "cash_balance": clean_number(getattr(asset, "cash_balance", None)),
        "cash_available_for_trade": clean_number(
            getattr(asset, "cash_available_for_trade", None)
        ),
        "gross_position_value": clean_number(getattr(asset, "gross_position_value", None)),
        "stock_market_value": clean_number(getattr(asset, "stock_market_value", None)),
        "futures_market_value": clean_number(getattr(asset, "futures_market_value", None)),
        "option_market_value": clean_number(getattr(asset, "option_market_value", None)),
        "unrealized_pl": clean_number(getattr(asset, "unrealized_pl", None)),
        "realized_pl": clean_number(getattr(asset, "realized_pl", None)),
    }


def public_segment(segment: Any) -> dict[str, Any]:
    currency_assets = getattr(segment, "currency_assets", None) or {}
    return {
        "currency": getattr(segment, "currency", None),
        "capability": getattr(segment, "capability", None),
        "category": getattr(segment, "category", None),
        "cash_balance": clean_number(getattr(segment, "cash_balance", None)),
        "cash_available_for_trade": clean_number(
            getattr(segment, "cash_available_for_trade", None)
        ),
        "cash_available_for_withdrawal": clean_number(
            getattr(segment, "cash_available_for_withdrawal", None)
        ),
        "gross_position_value": clean_number(getattr(segment, "gross_position_value", None)),
        "equity_with_loan": clean_number(getattr(segment, "equity_with_loan", None)),
        "net_liquidation": clean_number(getattr(segment, "net_liquidation", None)),
        "init_margin": clean_number(getattr(segment, "init_margin", None)),
        "maintain_margin": clean_number(getattr(segment, "maintain_margin", None)),
        "overnight_margin": clean_number(getattr(segment, "overnight_margin", None)),
        "unrealized_pl": clean_number(getattr(segment, "unrealized_pl", None)),
        "realized_pl": clean_number(getattr(segment, "realized_pl", None)),
        "total_today_pl": clean_number(getattr(segment, "total_today_pl", None)),
        "excess_liquidation": clean_number(getattr(segment, "excess_liquidation", None)),
        "overnight_liquidation": clean_number(getattr(segment, "overnight_liquidation", None)),
        "buying_power": clean_number(getattr(segment, "buying_power", None)),
        "leverage": clean_number(getattr(segment, "leverage", None)),
        "locked_funds": clean_number(getattr(segment, "locked_funds", None)),
        "currency_assets": {
            str(key): public_currency_asset(value) for key, value in currency_assets.items()
        },
    }


def public_prime_assets(assets: Any, settings: Settings) -> dict[str, Any] | None:
    if assets is None:
        return None
    segments = getattr(assets, "segments", None) or {}
    return {
        "account": mask_account(
            getattr(assets, "account", ""),
            settings.account_mask_prefix,
            settings.account_mask_suffix,
        ),
        "update_timestamp": getattr(assets, "update_timestamp", None),
        "segments": {str(key): public_segment(value) for key, value in segments.items()},
    }


def fetch_account_snapshot(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or Settings()
    load_env_file(settings.tiger_env_path)

    from tigeropen.common.consts import SecurityType
    from tigeropen.tiger_open_config import TigerOpenClientConfig
    from tigeropen.trade.trade_client import TradeClient

    logger = logging.getLogger("omega-quant.account-snapshot")
    logger.addHandler(logging.NullHandler())
    config = TigerOpenClientConfig()
    client = TradeClient(config, logger=logger)

    snapshot: dict[str, Any] = {
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "assets": None,
        "positions": {},
        "errors": {},
    }

    try:
        snapshot["assets"] = public_prime_assets(
            client.get_prime_assets(consolidated=True),
            settings,
        )
    except Exception as exc:  # pragma: no cover - depends on remote API
        snapshot["errors"]["assets"] = str(exc)

    for label, sec_type in {
        "STK": SecurityType.STK,
        "OPT": SecurityType.OPT,
        "FUT": SecurityType.FUT,
    }.items():
        try:
            positions = client.get_positions(sec_type=sec_type) or []
            snapshot["positions"][label] = [
                public_position(position, settings) for position in positions
            ]
        except Exception as exc:  # pragma: no cover - depends on remote API
            snapshot["errors"][f"positions_{label}"] = str(exc)
            snapshot["positions"][label] = []

    return snapshot
