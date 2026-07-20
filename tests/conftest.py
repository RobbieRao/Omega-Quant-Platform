from __future__ import annotations

import pytest

from omega_quant.broker.safety import register_execution_controller
from omega_quant.execution_control import ExecutionControlController, LIVE_CONFIRM_VALUE


@pytest.fixture(autouse=True)
def isolate_private_local_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never inherit the operator's ignored credentials or caches."""

    for key in (
        "MASSIVE_API_KEY",
        "OMEGA_MASSIVE_HOST",
        "OMEGA_MASSIVE_REST_HOST",
        "OMEGA_MASSIVE_SECURE",
        "OMEGA_MASSIVE_WS_HOST",
        "TIGEROPEN_ACCOUNT",
        "TIGEROPEN_PRIVATE_KEY",
        "TIGEROPEN_PRIVATE_KEY_PATH",
        "TIGEROPEN_TIGER_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OMEGA_DISABLE_LOCAL_SECRETS", "1")


@pytest.fixture
def armed_execution(monkeypatch: pytest.MonkeyPatch) -> ExecutionControlController:
    """Arm only the in-process fake broker path used by an individual test."""

    account = "TEST-SIM-ACCOUNT-0001"
    monkeypatch.setenv("TIGEROPEN_ACCOUNT", account)
    monkeypatch.setenv("OMEGA_EXPECTED_TIGER_ACCOUNT", account)
    monkeypatch.setenv("OMEGA_LIVE_TRADING", "1")
    monkeypatch.setenv("OMEGA_API_TOKEN", "test-api-token")
    monkeypatch.setenv("OMEGA_MAX_ORDER_NOTIONAL", "1000000")

    controller = ExecutionControlController()
    controller.arm(account_suffix="0001", confirmation=LIVE_CONFIRM_VALUE)
    register_execution_controller(controller)
    try:
        yield controller
    finally:
        controller.disarm()
        register_execution_controller(ExecutionControlController())
