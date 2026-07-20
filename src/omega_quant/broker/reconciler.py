"""消费 Tiger Push 订单事件，回写 Omega Order / Position。

主要责任：

1. 从 :class:`OrderEventQueue` 消费 ``order`` / ``transaction`` 事件；
2. 用 ``broker_order_id`` 反查 Omega Order，调用
   :meth:`OmegaService.reconcile_live_fill` 写回 fill_price / fill_id；
3. 30 秒为周期跑兜底 ``get_orders`` 轮询，覆盖 push 丢消息场景。

设计原则：

- 只处理 ``is_paper=False`` 的订单；reconcile 接口内部已经强校验。
- 任何异常都不应让后台线程崩溃 —— 全部记录到 ``record_live_event``。
- 在 FastAPI ``lifespan`` 启停；调用方负责注入 session_factory 和事件队列。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .audit import record_live_event
from .order_streamer import OrderEvent, OrderEventQueue


SessionFactory = Callable[[], Any]
"""返回 SQLAlchemy session 的工厂；reconciler 每条事件取新 session 避免长事务。"""


class LiveOrderReconciler:
    """后台 reconciler：把 Tiger Push 订单事件落回 Omega DB。

    使用方式::

        reconciler = LiveOrderReconciler(
            queue=order_event_queue,
            session_factory=SessionLocal,
            polling_interval_seconds=30.0,
        )
        reconciler.start()
        ...
        reconciler.stop()
    """

    def __init__(
        self,
        *,
        queue: OrderEventQueue,
        session_factory: SessionFactory,
        polling_interval_seconds: float = 30.0,
        trade_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._queue = queue
        self._session_factory = session_factory
        self._polling_interval = polling_interval_seconds
        self._trade_client_factory = trade_client_factory
        self._stop_event = threading.Event()
        self._consumer_thread: threading.Thread | None = None
        self._poller_thread: threading.Thread | None = None
        self._last_poll_at = 0.0

    def start(self) -> None:
        if self._consumer_thread is not None:
            return
        self._stop_event.clear()
        self._consumer_thread = threading.Thread(
            target=self._consume_loop, name="omega-reconciler-consumer", daemon=True
        )
        self._consumer_thread.start()
        self._poller_thread = threading.Thread(
            target=self._poll_loop, name="omega-reconciler-poller", daemon=True
        )
        self._poller_thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        for thread in (self._consumer_thread, self._poller_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=timeout)
        self._consumer_thread = None
        self._poller_thread = None

    # === 消费 push 事件 ===

    def _consume_loop(self) -> None:
        while not self._stop_event.is_set():
            event = self._queue.get(timeout=1.0)
            if event is None:
                continue
            self._handle_event_safely(event)

    def _handle_event_safely(self, event: OrderEvent) -> None:
        try:
            self._handle_event(event)
        except Exception as exc:  # noqa: BLE001
            record_live_event(
                "reconciler_error",
                {"kind": event.kind, "payload": event.payload, "error": repr(exc)},
            )

    def _handle_event(self, event: OrderEvent) -> None:
        if event.kind not in {"order", "transaction"}:
            return
        broker_order_id = (
            event.payload.get("broker_order_id")
            or event.payload.get("order_id")
            or event.payload.get("id")
        )
        if not broker_order_id:
            return
        status = str(event.payload.get("status") or "").lower()
        if status not in {"filled", "complete", "completed", "executed"}:
            # 非终态事件：暂时只记录审计，不写 Order.fill。
            record_live_event("reconciler_skip", {"event": event.kind, "broker_order_id": broker_order_id, "status": status})
            return
        fill_payload = {
            "fill_price": event.payload.get("avg_fill_price") or event.payload.get("fill_price"),
            "filled_quantity": event.payload.get("filled_quantity"),
            "broker_fill_id": event.payload.get("trade_id") or event.payload.get("fill_id"),
        }
        session = self._session_factory()
        try:
            from ..services import OmegaService

            service = OmegaService(session)
            service.reconcile_live_fill(str(broker_order_id), fill_payload)
        finally:
            session.close()

    # === 30s 兜底轮询 ===

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(min(self._polling_interval, 1.0))
            if self._stop_event.is_set():
                break
            now = time.time()
            if now - self._last_poll_at < self._polling_interval:
                continue
            self._last_poll_at = now
            self._poll_once_safely()

    def _poll_once_safely(self) -> None:
        if self._trade_client_factory is None:
            return
        try:
            self._poll_once()
        except Exception as exc:  # noqa: BLE001
            record_live_event("reconciler_poll_error", {"error": repr(exc)})

    def _poll_once(self) -> None:
        from sqlalchemy import select

        from ..models import Order

        session = self._session_factory()
        try:
            stale_orders = session.scalars(
                select(Order).where(
                    Order.is_paper.is_(False),
                    Order.status == "submitted",
                    Order.broker_order_id.isnot(None),
                )
            ).all()
            if not stale_orders:
                return
            client = self._trade_client_factory() if self._trade_client_factory else None
            if client is None:
                return
            from ..services import OmegaService

            service = OmegaService(session)
            fetcher = getattr(client, "get_orders", None)
            if fetcher is None:
                return
            broker_orders = fetcher() or []
            by_id = {str(item.get("id") or item.get("broker_order_id")): item for item in broker_orders}
            for omega_order in stale_orders:
                tiger_record = by_id.get(omega_order.broker_order_id)
                if not tiger_record:
                    continue
                status = str(tiger_record.get("status") or "").lower()
                if status not in {"filled", "complete", "completed"}:
                    continue
                service.reconcile_live_fill(
                    omega_order.broker_order_id,
                    {
                        "fill_price": tiger_record.get("avg_fill_price") or tiger_record.get("fill_price"),
                        "filled_quantity": tiger_record.get("filled_quantity"),
                        "broker_fill_id": tiger_record.get("trade_id"),
                    },
                )
        finally:
            session.close()
