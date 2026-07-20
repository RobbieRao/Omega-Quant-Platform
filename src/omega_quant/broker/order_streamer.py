"""Tiger Push 订单/持仓/成交回调队列。

Phase 2 提供线程安全的事件队列与订阅句柄占位；Phase 3 的
:mod:`omega_quant.broker.reconciler` 会从这里消费事件并写回 Omega Order /
Position 表。

为避免 import 即触发 ``tigeropen`` 加载，所有 TigerPushClient 相关 import
都放在 :func:`subscribe_tiger_push` 内部。
"""

from __future__ import annotations

import queue
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


OrderEventKind = Literal["order", "position", "transaction", "asset", "raw"]


@dataclass
class OrderEvent:
    kind: OrderEventKind
    payload: dict[str, Any]
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class OrderEventQueue:
    """线程安全的事件队列；reconciler 在后台 consumer 循环里 ``get``。"""

    def __init__(self, *, maxsize: int = 1024) -> None:
        self._queue: queue.Queue[OrderEvent] = queue.Queue(maxsize=maxsize)

    def put(self, event: OrderEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # 队列满意味着 reconciler 跟不上；直接丢弃比阻塞订阅线程更安全，
            # 兜底由 Phase 3 计划中的 30s ``get_orders`` 轮询负责对账。
            return

    def get(self, *, timeout: float | None = 1.0) -> OrderEvent | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def qsize(self) -> int:
        return self._queue.qsize()


def subscribe_tiger_push(
    push_client: Any,
    event_queue: OrderEventQueue,
    *,
    account: str | None = None,
) -> None:
    """注册 Tiger Push 订单/持仓/成交回调。

    Phase 2 仅做接口定义；Phase 3 的 reconciler 实施时会把回调内部填入。
    """

    def _handle_order(payload: Any) -> None:
        event_queue.put(OrderEvent(kind="order", payload=_to_dict(payload)))

    def _handle_position(payload: Any) -> None:
        event_queue.put(OrderEvent(kind="position", payload=_to_dict(payload)))

    def _handle_transaction(payload: Any) -> None:
        event_queue.put(OrderEvent(kind="transaction", payload=_to_dict(payload)))

    def _handle_asset(payload: Any) -> None:
        event_queue.put(OrderEvent(kind="asset", payload=_to_dict(payload)))

    _bind_subscription(push_client, "order", _handle_order, account=account)
    _bind_subscription(push_client, "position", _handle_position, account=account)
    _bind_subscription(push_client, "transaction", _handle_transaction, account=account)
    _bind_subscription(push_client, "asset", _handle_asset, account=account)


def _to_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    if hasattr(payload, "__dict__"):
        return {k: v for k, v in vars(payload).items() if not k.startswith("_")}
    return {"raw": repr(payload)}


def _bind_subscription(push_client: Any, kind: str, callback: Any, *, account: str | None) -> None:
    """兼容 TigerOpen SDK 与测试 fake 的订阅形态。

    官方 ``tigeropen`` PushClient 使用 ``order_changed`` 等属性注册回调，
    ``subscribe_order(account=...)`` 只负责发订阅命令。早期 Omega fake 使用
    ``subscribe_order(callback, account=...)``，这里保留兼容，避免重新造一层
    与 SDK 脱节的接口。
    """

    callback_attr = f"{kind}_changed"
    subscribe_name = f"subscribe_{kind}"
    subscribe = getattr(push_client, subscribe_name, None)
    if not callable(subscribe):
        return

    if hasattr(push_client, callback_attr):
        setattr(push_client, callback_attr, callback)
        subscribe(account=account) if account else subscribe()
        return

    try:
        subscribe(callback, account=account) if account else subscribe(callback)
    except TypeError:
        subscribe(account=account) if account else subscribe()
