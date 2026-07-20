from __future__ import annotations

import threading
from collections import deque
from time import monotonic
from typing import Any


MASSIVE_RATE_WINDOW_SECONDS = 60.0
MASSIVE_RATE_LIMITS_RPM: dict[str, int] = {
    "rest": 500,
    "websocket_stocks": 1000,
    "websocket_options": 50,
}
MASSIVE_RATE_LABELS: dict[str, str] = {
    "rest": "REST API",
    "websocket_stocks": "WS equity events",
    "websocket_options": "WS option events",
}

_LOCK = threading.Lock()
_EVENTS: dict[str, deque[float]] = {key: deque() for key in MASSIVE_RATE_LIMITS_RPM}


def _trim(queue: deque[float], now: float) -> None:
    cutoff = now - MASSIVE_RATE_WINDOW_SECONDS
    while queue and queue[0] < cutoff:
        queue.popleft()


def _usage_status(usage_pct: float | None) -> str:
    if usage_pct is None:
        return "unknown"
    if usage_pct >= 0.95:
        return "danger"
    if usage_pct >= 0.75:
        return "warning"
    return "ok"


def record_massive_rest_request(count: int = 1) -> None:
    _record("rest", count=count)


def record_massive_ws_event(asset_class: str | None, count: int = 1) -> None:
    key = "websocket_options" if str(asset_class or "").lower() == "options" else "websocket_stocks"
    _record(key, count=count)


def _record(key: str, *, count: int = 1) -> None:
    if count <= 0:
        return
    now = monotonic()
    with _LOCK:
        queue = _EVENTS.setdefault(key, deque())
        _trim(queue, now)
        for _ in range(count):
            queue.append(now)


def massive_rate_limit_snapshot() -> dict[str, Any]:
    now = monotonic()
    channels: dict[str, dict[str, Any]] = {}
    with _LOCK:
        for key, limit in MASSIVE_RATE_LIMITS_RPM.items():
            queue = _EVENTS.setdefault(key, deque())
            _trim(queue, now)
            rpm = len(queue)
            usage_pct = rpm / limit if limit else None
            channels[key] = {
                "key": key,
                "label": MASSIVE_RATE_LABELS.get(key, key),
                "rpm": rpm,
                "limitRpm": limit,
                "remainingRpm": max(limit - rpm, 0),
                "usagePct": usage_pct,
                "status": _usage_status(usage_pct),
            }
    return {
        "provider": "massive",
        "windowSeconds": int(MASSIVE_RATE_WINDOW_SECONDS),
        "limitsSource": "Omega configured safety budget",
        "channels": channels,
    }


def reset_massive_rate_limits() -> None:
    with _LOCK:
        for queue in _EVENTS.values():
            queue.clear()
