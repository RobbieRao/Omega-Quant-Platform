"""TigerOpen TradeClient 的延迟加载工厂与合约构造。

所有 ``tigeropen`` 包的 import 都放在函数体内，确保仅 import 本模块不会拉入
TigerOpen SDK，也不会读取凭证文件。

调用方负责：

1. 在调用 :func:`build_trade_client` 之前先用
   :func:`omega_quant.broker.env.load_tiger_env` 把 ``~/.tigeropen/tigermcp.env``
   加载到 ``os.environ``。
2. 在调用前已通过 :func:`omega_quant.broker.safety.require_live_gateway_armed`。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .env import DEFAULT_TIGER_ENV_PATH, load_tiger_env


def configured_tiger_account() -> str | None:
    """返回当前 Omega/TigerOpen 配置指向的账户号。

    Tiger 官方文档说明 account 可以填写真实账户号或模拟盘账户号。Omega 不在
    代码里硬分叉模拟盘/实盘；当前配置的 account 就是 TigerOpen 执行目标。
    """

    if os.environ.get("OMEGA_DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return None
    load_tiger_env(None, override=False)
    return os.environ.get("TIGEROPEN_ACCOUNT")


def configured_tiger_account_kind() -> str:
    """配置层声明的账户类型，仅用于 UI 展示和审计，不参与下单路由。"""

    load_tiger_env(None, override=False)
    return (
        os.environ.get("OMEGA_TIGER_ACCOUNT_KIND")
        or os.environ.get("TIGEROPEN_ACCOUNT_KIND")
        or "unknown"
    ).strip().lower()


def mask_tiger_account(account: str | None, *, prefix: int = 3, suffix: int = 4) -> str | None:
    """Return a UI/audit-safe Tiger account display value."""

    if not account:
        return None
    text = str(account)
    if len(text) <= prefix + suffix:
        return "*" * len(text)
    return f"{text[:prefix]}{'*' * (len(text) - prefix - suffix)}{text[-suffix:]}"


def configured_tiger_account_info() -> dict[str, Any]:
    """Return non-secret Tiger account routing metadata for UI and audit."""

    account = configured_tiger_account()
    kind = configured_tiger_account_kind()
    expected = os.environ.get("OMEGA_EXPECTED_TIGER_ACCOUNT")
    expected_match = bool(expected and account and str(account).strip() == str(expected).strip())
    return {
        "configured": bool(account),
        "account_masked": mask_tiger_account(account),
        "account_kind": kind,
        "expected_account_masked": mask_tiger_account(expected),
        "matches_expected_account": expected_match if expected else None,
        "source": "TIGEROPEN_ACCOUNT from local env",
        "sdk": "tigerfintech/openapi-python-sdk",
    }


def _build_client_config(*, env_path: Path | None = None) -> Any:
    load_tiger_env(env_path, override=True)
    from tigeropen.tiger_open_config import TigerOpenClientConfig

    config = TigerOpenClientConfig()
    tiger_id = os.environ.get("TIGEROPEN_TIGER_ID")
    account = configured_tiger_account()
    private_key = os.environ.get("TIGEROPEN_PRIVATE_KEY")
    private_key_path = os.environ.get("TIGEROPEN_PRIVATE_KEY_PATH")
    if tiger_id:
        config.tiger_id = tiger_id
    if account:
        config.account = account
    if private_key:
        config.private_key = private_key
    elif private_key_path:
        try:
            from tigeropen.common.util.signature_utils import read_private_key

            config.private_key = read_private_key(private_key_path)
        except Exception:
            # 交给 SDK 初始化/请求阶段抛出原始配置错误。
            pass
    return config


def build_trade_client(*, env_path: Path | None = None) -> Any:
    """构造 TigerOpen ``TradeClient``，按需加载凭证 env 文件。"""

    from tigeropen.trade.trade_client import TradeClient

    config = _build_client_config(env_path=env_path)
    return TradeClient(config)


def build_quote_client(*, env_path: Path | None = None) -> Any:
    """构造 TigerOpen ``QuoteClient``，用于历史 K 线 / 期权数据下载。"""

    from tigeropen.quote.quote_client import QuoteClient

    config = _build_client_config(env_path=env_path)
    return QuoteClient(config)


def build_push_client(*, env_path: Path | None = None, connect: bool = True) -> Any:
    """构造 TigerOpen ``PushClient``，用于订单/持仓/资产实时回报。

    官方 SDK 的 PushClient 与 TradeClient 共用 ``TigerOpenClientConfig``，
    socket host/port 由 SDK 配置决定。这里仅封装美股/期权交易回报所需的
    push client，不引入行情执行或国内 gateway。
    """

    from tigeropen.push.push_client import PushClient

    config = _build_client_config(env_path=env_path)
    protocol, host, port = config.socket_host_port
    use_ssl = str(protocol).lower() == "ssl"
    connection_timeout = float(os.environ.get("OMEGA_TIGER_PUSH_TIMEOUT_SECONDS") or "5")
    client = PushClient(host, int(port), use_ssl=use_ssl, connection_timeout=connection_timeout, client_config=config)
    if connect:
        tiger_id = os.environ.get("TIGEROPEN_TIGER_ID") or getattr(config, "tiger_id", None)
        private_key = os.environ.get("TIGEROPEN_PRIVATE_KEY") or getattr(config, "private_key", None)
        if not tiger_id or not private_key:
            raise ValueError("TigerOpen push client 缺少 tiger_id 或 private_key。")
        client.connect(tiger_id, private_key)
    return client


def build_contract(client: Any, action: dict[str, Any]) -> Any:
    """根据 ``action`` 构造 TigerOpen 合约对象，支持 STK / OPT。"""

    from tigeropen.common.consts import Currency, SecurityType
    from tigeropen.common.util.contract_utils import option_contract_by_symbol, stock_contract

    contract_info = action.get("contract") or {}
    symbol = contract_info.get("symbol") or action.get("symbol")
    if not symbol:
        raise ValueError("action 缺少 symbol，无法构造 TigerOpen 合约。")
    currency_text = str(contract_info.get("currency") or "USD").upper()
    currency = Currency.USD if currency_text == "USD" else currency_text
    sec_type = str(action.get("sec_type") or "").upper()

    if sec_type == "OPT":
        strike_raw = contract_info.get("strike")
        if strike_raw is None:
            raise ValueError(f"期权合约缺少 strike：{contract_info}")
        strike = float(strike_raw)
        fetched = client.get_contract(
            symbol,
            SecurityType.OPT,
            currency=currency,
            expiry=contract_info.get("expiry"),
            strike=strike,
            put_call=contract_info.get("put_call"),
        )
        return fetched or option_contract_by_symbol(
            symbol,
            contract_info.get("expiry"),
            strike,
            contract_info.get("put_call"),
            currency_text,
        )

    fetched = client.get_contract(symbol, SecurityType.STK, currency=currency)
    return fetched or stock_contract(symbol=symbol, currency=currency_text)


def submit_via_tiger(
    client: Any,
    *,
    contract: Any,
    side: str,
    order_type: str,
    quantity: float,
    limit_price: float | None,
    time_in_force: str = "DAY",
    outside_rth: bool = False,
    account_segment: str | None = None,
) -> dict[str, Any]:
    """触发 TigerOpen ``create_order`` + ``preview_order`` + ``place_order``。

    返回结果按 status 字段区分：

    - ``preview_blocked``: preview 返回 warning_text，order 未提交
    - ``submitted``: TigerOpen 接受订单
    - ``preview_only``: place 开关未解锁，仅完成 preview dry-run
    """

    create_kwargs: dict[str, Any] = {
        "limit_price": limit_price,
        "time_in_force": time_in_force,
        "outside_rth": outside_rth,
    }
    if account_segment:
        create_kwargs["account"] = account_segment

    account = account_segment or getattr(client, "_account", None) or configured_tiger_account()
    order = client.create_order(
        account,
        contract,
        side,
        order_type or "LMT",
        quantity,
        **create_kwargs,
    )
    preview = client.preview_order(order)
    if isinstance(preview, dict) and preview.get("warning_text"):
        return {
            "status": "preview_blocked",
            "message": preview.get("warning_text"),
            "preview": preview,
            "order_intent": _describe_order(order, contract, side, quantity, limit_price),
        }

    from .safety import tiger_submission_unlocked

    if not tiger_submission_unlocked():
        return {
            "status": "preview_only",
            "message": "TigerOpen preview passed, but live submission is locked for this process.",
            "preview": preview,
            "order_intent": _describe_order(order, contract, side, quantity, limit_price),
        }

    max_notional = _configured_max_order_notional()
    estimated_notional = abs(float(quantity) * float(limit_price or 0.0))
    if max_notional is None:
        raise PermissionError("OMEGA_MAX_ORDER_NOTIONAL must be configured before live submission")
    if not limit_price or limit_price <= 0:
        raise PermissionError("live market orders require an explicit positive protection price")
    if estimated_notional > max_notional:
        raise PermissionError(
            f"order notional {estimated_notional:.2f} exceeds OMEGA_MAX_ORDER_NOTIONAL {max_notional:.2f}"
        )

    broker_order_id = client.place_order(order)
    return {
        "status": "submitted",
        "message": "TigerOpen accepted the order.",
        "broker_order_id": broker_order_id,
        "preview": preview,
        "order_intent": _describe_order(order, contract, side, quantity, limit_price),
    }


def _describe_order(
    order: Any,
    contract: Any,
    side: str,
    quantity: float,
    limit_price: float | None,
) -> dict[str, Any]:
    return {
        "side": side,
        "quantity": quantity,
        "limit_price": limit_price,
        "contract_symbol": getattr(contract, "symbol", None),
        "sec_type": getattr(contract, "sec_type", None),
    }


def _configured_max_order_notional() -> float | None:
    try:
        value = float(os.environ.get("OMEGA_MAX_ORDER_NOTIONAL", ""))
    except ValueError:
        return None
    return value if value > 0 else None


def cancel_via_tiger(client: Any, broker_order_id: str) -> dict[str, Any]:
    """触发 TigerOpen 撤单调用。"""

    from .safety import require_tiger_submission_unlocked

    require_tiger_submission_unlocked()
    if not broker_order_id:
        raise ValueError("broker_order_id 为空，无法撤单。")
    order_id = int(broker_order_id) if str(broker_order_id).isdigit() else broker_order_id
    response = client.cancel_order(account=configured_tiger_account(), id=order_id)
    return {"status": "cancelled", "broker_order_id": broker_order_id, "response": response}
