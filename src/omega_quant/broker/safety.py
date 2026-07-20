"""Fail-closed process-local safety controls for TigerOpen execution."""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from ..execution_control import ExecutionControlController


class LiveGatewayLockedError(RuntimeError):
    """Raised when the current process has not passed live arming."""


class PlaceOrderLockedError(RuntimeError):
    """Raised when broker submission is not armed for this process."""


_active_controller: ExecutionControlController | None = None


def register_execution_controller(controller: ExecutionControlController) -> None:
    global _active_controller
    _active_controller = controller


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def live_gateway_armed(controller: ExecutionControlController | None = None) -> bool:
    active = controller or _active_controller
    return bool(active and active.live_gateway_armed())


def tiger_submission_unlocked() -> bool:
    """Return true only after the active process session has been armed."""

    return live_gateway_armed()


def require_live_gateway_armed(controller: ExecutionControlController | None = None) -> None:
    if not live_gateway_armed(controller):
        raise LiveGatewayLockedError("TigerOpen live execution is locked for this process")


def require_tiger_submission_unlocked() -> None:
    if not tiger_submission_unlocked():
        raise PlaceOrderLockedError("TigerOpen order submission is locked for this process")


# ============================================================================
# Omega live-process marker
# ============================================================================

OMEGA_LIVE_LOCK_PATH = Path.home() / ".tigeropen" / "omega_live.lock"
OMEGA_LIVE_LOCK_FRESHNESS_SECONDS = 300.0  # 5 分钟内视为"活跃"


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def write_omega_live_lock(*, path: Path | None = None) -> Path:
    """Write a diagnostic marker for another Omega process on this account."""

    target = path or OMEGA_LIVE_LOCK_PATH
    payload = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "host": socket.gethostname(),
        "hint": "Omega is actively submitting live orders against this Tiger account.",
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return target


def read_omega_live_lock(path: Path | None = None) -> dict | None:
    target = path or OMEGA_LIVE_LOCK_PATH
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def omega_live_lock_active(*, path: Path | None = None, freshness_seconds: float | None = None) -> bool:
    """文件存在、内容可解析、mtime 在 freshness 内、pid 还活着。"""

    target = path or OMEGA_LIVE_LOCK_PATH
    if not target.exists():
        return False
    freshness = freshness_seconds if freshness_seconds is not None else OMEGA_LIVE_LOCK_FRESHNESS_SECONDS
    try:
        mtime_age = time.time() - target.stat().st_mtime
    except OSError:
        return False
    if mtime_age > freshness:
        return False
    payload = read_omega_live_lock(target)
    if not payload:
        return False
    pid = int(payload.get("pid") or 0)
    if pid == os.getpid():
        # 当前进程的锁不应阻止自己。
        return False
    return _is_pid_alive(pid)


def clear_omega_live_lock(path: Path | None = None) -> None:
    target = path or OMEGA_LIVE_LOCK_PATH
    try:
        target.unlink()
    except FileNotFoundError:
        pass
