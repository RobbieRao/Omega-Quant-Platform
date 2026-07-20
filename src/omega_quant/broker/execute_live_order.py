"""真实下单主流程。

入口 :func:`submit_live_order` 接收已通过 paper 风控的 Omega Order 描述 +
当前 Tiger/Massive 行情快照，按 Omega 的多层闸门顺序检验，必要时
调用 TigerOpen ``create_order`` / ``preview_order`` / ``place_order``。

执行模型：

1. ``require_live_gateway_armed``：执行控制面必须 armed。
2. ``run_preflight_gates``：实时行情、bid/ask、价差、OPRA、asset_class 等门。
3. ``tiger_submission_unlocked``：再次确认当前进程仍处于 armed 状态。
4. ``submit_via_tiger``：实际发出三步调用，得到 ``broker_order_id``。
5. ``record_live_event``：把整个尝试写入 omega 实盘审计日志。

返回的 dict 字段是稳定 API；service 层据此更新 Order/lineage。
"""

from __future__ import annotations

from typing import Any, Iterable

from .audit import record_live_event
from .preflight_gates import GateThresholds, run_preflight_gates
from .safety import (
    LiveGatewayLockedError,
    PlaceOrderLockedError,
    tiger_submission_unlocked,
    require_live_gateway_armed,
    write_omega_live_lock,
)


def submit_live_order(
    action: dict[str, Any],
    stream: dict[str, Any] | None,
    *,
    thresholds: GateThresholds | None = None,
    asset_class_allow: Iterable[str] | None = None,
    monitor_group_id: str | None = None,
    order_id: str | None = None,
    lineage_id: str | None = None,
    account_segment: str | None = None,
    trade_client: Any | None = None,
) -> dict[str, Any]:
    """提交真实 TigerOpen 订单（或在最后一道开关未解锁时仅完成 preview dry-run）。

    参数：

    - ``action``：Omega action 字典；至少包含 symbol / sec_type / action /
      quantity / order_type / contract。建议带 display / limit_price。
    - ``stream``：Tiger Push + Massive 综合行情快照，使用 Omega market context
      字段（含 connected / lastEventAtMs / quotes / bbo / marketData）。
    - ``thresholds``：Omega 实时行情阈值。
    - ``asset_class_allow``：MonitorGroup 授权的资产类别。
    - ``monitor_group_id`` / ``order_id`` / ``lineage_id``：仅用于审计上下文。
    - ``account_segment``：TigerOpen account 段，例如 cash / margin。
    - ``trade_client``：测试时可注入 FakeTradeClient；不传则按需新建。

    返回 dict 的 ``status`` 取值：``preflight_blocked`` / ``gateway_locked`` /
    ``preview_blocked`` / ``preview_only`` / ``submitted`` / ``error``。
    """

    base_payload = {
        "monitor_group_id": monitor_group_id,
        "order_id": order_id,
        "lineage_id": lineage_id,
        "action": action,
    }

    try:
        require_live_gateway_armed()
    except LiveGatewayLockedError as exc:
        result = {
            "status": "gateway_locked",
            "message": str(exc),
        }
        record_live_event("live_gateway_locked", {**base_payload, "result": result})
        return result

    gate = run_preflight_gates(
        action,
        stream,
        thresholds=thresholds,
        asset_class_allow=asset_class_allow,
    )
    if not gate.get("ok"):
        result = {
            "status": "preflight_blocked",
            "message": gate.get("message", "实时行情闸门未通过，未提交订单。"),
            "stream_gate": gate,
        }
        record_live_event("preflight_blocked", {**base_payload, "result": result})
        return result

    side = str(action.get("action") or "").upper()
    order_type = str(action.get("order_type") or "LMT").upper()
    quantity = action.get("quantity")
    if quantity is None or float(quantity) <= 0:
        result = {
            "status": "error",
            "message": "quantity 必须为正数，未提交订单。",
            "stream_gate": gate,
        }
        record_live_event("preflight_blocked", {**base_payload, "result": result})
        return result

    limit_price = gate.get("suggestedLimitPrice") or action.get("limit_price")
    time_in_force = str(action.get("time_in_force") or "DAY").upper()
    outside_rth = bool(action.get("outside_rth", False))

    try:
        # 写共存互锁：进入真实提交路径就标记本进程在使用 Tiger 账户。
        try:
            write_omega_live_lock()
        except Exception as lock_exc:  # noqa: BLE001
            record_live_event("lock_write_error", {"error": repr(lock_exc), **base_payload})

        client = trade_client or _lazy_build_client()
        from .tigeropen_client import build_contract, submit_via_tiger

        contract = build_contract(client, action)
        outcome = submit_via_tiger(
            client,
            contract=contract,
            side=side,
            order_type=order_type,
            quantity=float(quantity),
            limit_price=float(limit_price) if limit_price is not None else None,
            time_in_force=time_in_force,
            outside_rth=outside_rth,
            account_segment=account_segment,
        )
    except PlaceOrderLockedError as exc:
        # 理论上 submit_via_tiger 会先返回 preview_only；这里兜底。
        result = {
            "status": "preview_only",
            "message": str(exc),
            "stream_gate": gate,
        }
        record_live_event("preview_only", {**base_payload, "result": result})
        return result
    except Exception as exc:  # noqa: BLE001
        result = {
            "status": "error",
            "message": f"TigerOpen 调用失败：{exc!r}",
            "stream_gate": gate,
        }
        record_live_event("live_error", {**base_payload, "result": result})
        return result

    result = {
        **outcome,
        "stream_gate": gate,
        "usedLimitPrice": limit_price,
        "place_order_unlocked": tiger_submission_unlocked(),
    }
    record_live_event(
        outcome.get("status") or "live_submitted",
        {**base_payload, "result": result},
    )
    return result


def _lazy_build_client() -> Any:
    from .tigeropen_client import build_trade_client

    return build_trade_client()
