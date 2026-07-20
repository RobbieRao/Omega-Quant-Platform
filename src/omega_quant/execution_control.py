from __future__ import annotations

import os
import shlex
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ExecutionMode = Literal["live"]
LiveGatewayStatus = Literal["locked", "unlocked"]

LIVE_CONFIRM_VALUE = "OMEGA_LIVE_RISK_ACCEPTED"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_mode(value: str | None) -> ExecutionMode:
    mode = str(value or "live").strip().lower()
    if mode in {"paper", "paper_only", "simulation", "live", "live_ready", "live_gateway_armed", "real"}:
        return "live"
    raise ValueError(f"unsupported execution mode: {value}")


@dataclass(frozen=True)
class UnlockRequirement:
    key: str
    label: str
    satisfied: bool
    detail: str


class ExecutionControlController:
    """
    Single control plane for Omega execution posture.

    Omega currently exposes one execution posture: TigerOpen live API. The
    target account comes from TigerOpen local configuration, so switching
    simulated/live accounts is an account configuration change, not another
    Omega execution mode.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._requested_mode: ExecutionMode = "live"
        self._paper_engine_enabled = False
        self._armed = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            requested_mode = self._requested_mode
            paper_enabled = self._paper_engine_enabled
            session_armed = self._armed
        account_info = _configured_tiger_account_info()
        public_account_info = {key: value for key, value in account_info.items() if not key.startswith("_")}
        requirements = self.live_unlock_requirements(account_info)
        live_gateway_armed = session_armed and all(item.satisfied for item in requirements)
        live_gateway_status: LiveGatewayStatus = "unlocked" if live_gateway_armed else "locked"
        effective_mode: ExecutionMode = "live"
        return {
            "control_plane": "omega_execution_control",
            "requested_execution_mode": requested_mode,
            "effective_execution_mode": effective_mode,
            "paper_engine_enabled": paper_enabled,
            "live_gateway_status": live_gateway_status,
            "live_gateway_armed": live_gateway_armed,
            "session_armed": session_armed,
            "real_account_mode": "tigeropen_configured_account",
            "market_data_mode": "live_readonly",
            "broker_execution_gateway": "tigeropen_sdk",
            "target_account": public_account_info,
            "unlock_requirements": [item.__dict__ for item in requirements],
            "centralized_switches": {
                "paper_engine": False,
                "live_gateway": live_gateway_status,
                "real_account": "tigeropen_configured_account",
                "market_data": "live_readonly",
                "requested_execution_mode": requested_mode,
                "effective_execution_mode": effective_mode,
                "target_account_kind": account_info["account_kind"],
                "target_account_masked": account_info["account_masked"],
            },
            "live_readiness": self._live_readiness(requirements, live_gateway_status, effective_mode),
            "message": self._message(requested_mode, effective_mode, live_gateway_status, paper_enabled),
        }

    def update(self, *, requested_execution_mode: str | None = None, paper_engine_enabled: bool | None = None) -> dict[str, Any]:
        with self._lock:
            self._requested_mode = "live"
            self._paper_engine_enabled = False
        return self.status()

    def arm(self, *, account_suffix: str, confirmation: str) -> dict[str, Any]:
        account_info = _configured_tiger_account_info()
        requirements = self.live_unlock_requirements(account_info)
        missing = [item.key for item in requirements if not item.satisfied]
        if missing:
            raise PermissionError(f"live execution requirements are not satisfied: {', '.join(missing)}")
        if confirmation != LIVE_CONFIRM_VALUE:
            raise PermissionError("live risk confirmation is invalid")
        account = str(account_info.get("_account_raw") or "")
        suffix = str(account_suffix or "").strip()
        if len(suffix) < 4 or not account.endswith(suffix):
            raise PermissionError("account suffix does not match the configured TigerOpen account")
        with self._lock:
            self._armed = True
        return self.status()

    def disarm(self) -> dict[str, Any]:
        with self._lock:
            self._armed = False
        return self.status()

    def paper_engine_enabled(self) -> bool:
        return False

    def live_gateway_armed(self) -> bool:
        return bool(self.status()["live_gateway_armed"])

    @staticmethod
    def live_unlock_requirements(account_info: dict[str, Any] | None = None) -> list[UnlockRequirement]:
        info = account_info or _configured_tiger_account_info()
        configured = bool(info.get("configured"))
        account_kind = str(info.get("account_kind") or "unknown")
        expected_configured = bool(info.get("expected_account_masked"))
        expected_matches = info.get("matches_expected_account") is True
        max_notional = _positive_float(os.environ.get("OMEGA_MAX_ORDER_NOTIONAL"))
        return [
            UnlockRequirement(
                key="TIGEROPEN_ACCOUNT",
                label="TigerOpen 目标账户",
                satisfied=configured,
                detail=(
                    f"当前执行账户由本地 TigerOpen 配置决定：{info.get('account_masked')} ({account_kind})。"
                    if configured
                    else "缺少 TIGEROPEN_ACCOUNT，TigerOpen gateway 无法确定目标账户。"
                ),
            ),
            UnlockRequirement(
                key="OMEGA_EXPECTED_TIGER_ACCOUNT",
                label="Expected TigerOpen account",
                satisfied=expected_configured and expected_matches,
                detail="Expected account matches the configured TigerOpen account." if expected_matches else "Set OMEGA_EXPECTED_TIGER_ACCOUNT to the exact target account.",
            ),
            UnlockRequirement(
                key="OMEGA_LIVE_TRADING",
                label="Live execution environment switch",
                satisfied=_env_flag("OMEGA_LIVE_TRADING"),
                detail="OMEGA_LIVE_TRADING must be set to 1 for this process.",
            ),
            UnlockRequirement(
                key="OMEGA_API_TOKEN",
                label="API write token",
                satisfied=bool(os.environ.get("OMEGA_API_TOKEN", "").strip()),
                detail="OMEGA_API_TOKEN must be configured before live writes are accepted.",
            ),
            UnlockRequirement(
                key="OMEGA_MAX_ORDER_NOTIONAL",
                label="Per-order notional limit",
                satisfied=max_notional is not None,
                detail="OMEGA_MAX_ORDER_NOTIONAL must be a positive number.",
            ),
        ]

    @staticmethod
    def _live_readiness(
        requirements: list[UnlockRequirement],
        live_gateway_status: LiveGatewayStatus,
        effective_mode: ExecutionMode,
    ) -> dict[str, Any]:
        satisfied = sum(1 for item in requirements if item.satisfied)
        missing = [item.key for item in requirements if not item.satisfied]
        ready = not missing
        return {
            "status": "ready" if ready else "not_configured",
            "effective_execution_mode": effective_mode,
            "requirements_total": len(requirements),
            "requirements_satisfied": satisfied,
            "missing": missing,
            "broker_execution_available": ready,
            "message": (
                "TigerOpen live API 路径已启用；当前目标账户由本地 TigerOpen 配置决定。"
                if ready
                else "TigerOpen live API 路径需要先配置目标账户。"
            ),
        }

    @staticmethod
    def _message(
        requested_mode: ExecutionMode,
        effective_mode: ExecutionMode,
        live_gateway_status: LiveGatewayStatus,
        paper_enabled: bool,
    ) -> str:
        return "当前执行模式为 TigerOpen live API；目标账户使用本地 TigerOpen 配置。"


def _configured_tiger_account_info() -> dict[str, Any]:
    if _env_flag("OMEGA_DEMO_MODE"):
        return {
            "configured": False,
            "_account_raw": None,
            "account_masked": None,
            "account_kind": "demo",
            "expected_account_masked": None,
            "matches_expected_account": None,
            "source": "credential-free demo",
            "sdk": "disabled",
        }
    account = os.environ.get("TIGEROPEN_ACCOUNT") or _configured_env_value("TIGEROPEN_ACCOUNT")
    expected = os.environ.get("OMEGA_EXPECTED_TIGER_ACCOUNT") or _configured_env_value("OMEGA_EXPECTED_TIGER_ACCOUNT")
    expected_match = bool(expected and account and str(account).strip() == str(expected).strip())
    kind = (
        os.environ.get("OMEGA_TIGER_ACCOUNT_KIND")
        or os.environ.get("TIGEROPEN_ACCOUNT_KIND")
        or _configured_env_value("OMEGA_TIGER_ACCOUNT_KIND")
        or _configured_env_value("TIGEROPEN_ACCOUNT_KIND")
        or "unknown"
    ).strip().lower()
    return {
        "configured": bool(account),
        "_account_raw": account,
        "account_masked": _mask_account(account),
        "account_kind": kind,
        "expected_account_masked": _mask_account(expected),
        "matches_expected_account": expected_match if expected else None,
        "source": "TIGEROPEN_ACCOUNT from local env",
        "sdk": "tigerfintech/openapi-python-sdk",
    }


def _configured_env_value(key: str) -> str | None:
    if _env_flag("OMEGA_DISABLE_LOCAL_SECRETS"):
        return None
    for path in (Path(__file__).resolve().parents[2] / "config" / "omega.local.secrets.env",):
        value = _read_env_value(path, key)
        if value:
            return value
    return None


def _read_env_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        line_key, raw = stripped.split("=", 1)
        if line_key.strip() != key:
            continue
        try:
            parsed = shlex.split(f"x={raw}", posix=True)
            return parsed[0].split("=", 1)[1] if parsed and "=" in parsed[0] else raw.strip()
        except ValueError:
            return raw.strip().strip("'").strip('"')
    return None


def _mask_account(account: str | None, *, prefix: int = 3, suffix: int = 4) -> str | None:
    if not account:
        return None
    text = str(account)
    if len(text) <= prefix + suffix:
        return "*" * len(text)
    return f"{text[:prefix]}{'*' * (len(text) - prefix - suffix)}{text[-suffix:]}"


def _positive_float(value: str | None) -> float | None:
    try:
        parsed = float(str(value or "").strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None
