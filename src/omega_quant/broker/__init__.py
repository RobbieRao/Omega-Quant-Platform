"""Omega 实盘 broker 子包。

该子包封装真实券商提交链路。Top-level Omega 不直接 import 这里；
service 层在确认 MonitorGroup.execution_mode == "live" 且
ExecutionControlController.live_gateway_armed() 通过后再 lazy import。

外部调用入口集中在：

- :func:`omega_quant.broker.execute_live_order.submit_live_order`
- :func:`omega_quant.broker.audit.record_live_event`
- :func:`omega_quant.broker.preflight_gates.run_preflight_gates`
- :func:`omega_quant.broker.tigeropen_client.build_trade_client`

`tigeropen` SDK 的 import 全部放在被调用的函数体内，确保仅 import 子包本身
不会引入交易 SDK，也不会触发券商凭证文件加载。
"""

from __future__ import annotations

from .safety import (
    LiveGatewayLockedError,
    PlaceOrderLockedError,
    require_live_gateway_armed,
    require_tiger_submission_unlocked,
    tiger_submission_unlocked,
)


__all__ = [
    "LiveGatewayLockedError",
    "PlaceOrderLockedError",
    "require_live_gateway_armed",
    "require_tiger_submission_unlocked",
    "tiger_submission_unlocked",
]
