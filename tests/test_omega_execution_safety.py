from __future__ import annotations

import asyncio

import httpx

from omega_quant.api import create_app
from omega_quant.execution_control import ExecutionControlController, LIVE_CONFIRM_VALUE


def _configure_live_requirements(monkeypatch, *, account: str = "TEST-SIM-ACCOUNT-0001") -> None:
    monkeypatch.setenv("TIGEROPEN_ACCOUNT", account)
    monkeypatch.setenv("OMEGA_EXPECTED_TIGER_ACCOUNT", account)
    monkeypatch.setenv("OMEGA_LIVE_TRADING", "1")
    monkeypatch.setenv("OMEGA_API_TOKEN", "test-api-token")
    monkeypatch.setenv("OMEGA_MAX_ORDER_NOTIONAL", "1000000")


def test_state_changing_api_rejects_wrong_token_without_account_leak(monkeypatch, tmp_path) -> None:
    account = "TEST-SIM-ACCOUNT-0001"
    _configure_live_requirements(monkeypatch, account=account)
    app = create_app(f"sqlite:///{tmp_path / 'omega.sqlite'}")

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://omega.test") as client:
            return await client.post(
                "/api/execution/disarm",
                headers={"X-Omega-Token": "wrong-token"},
            )

    try:
        response = asyncio.run(request())
    finally:
        app.state.omega_core_runtime.close()

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "OMEGA_API_TOKEN_INVALID"
    assert account not in response.text


def test_arm_rejects_expected_account_mismatch(monkeypatch) -> None:
    _configure_live_requirements(monkeypatch, account="TEST-SIM-ACCOUNT-0001")
    monkeypatch.setenv("OMEGA_EXPECTED_TIGER_ACCOUNT", "TEST-SIM-ACCOUNT-9999")

    controller = ExecutionControlController()
    try:
        controller.arm(account_suffix="0001", confirmation=LIVE_CONFIRM_VALUE)
    except PermissionError as exc:
        assert "OMEGA_EXPECTED_TIGER_ACCOUNT" in str(exc)
    else:  # pragma: no cover - fail-closed invariant
        raise AssertionError("mismatched expected account must not arm live execution")

    assert controller.status()["session_armed"] is False


def test_arm_is_process_local_and_restart_relocks(monkeypatch) -> None:
    _configure_live_requirements(monkeypatch)
    controller = ExecutionControlController()
    armed = controller.arm(account_suffix="0001", confirmation=LIVE_CONFIRM_VALUE)

    assert armed["session_armed"] is True
    assert armed["live_gateway_armed"] is True

    restarted_controller = ExecutionControlController()
    restarted = restarted_controller.status()
    assert restarted["session_armed"] is False
    assert restarted["live_gateway_status"] == "locked"


def test_arm_rejects_invalid_risk_confirmation(monkeypatch) -> None:
    _configure_live_requirements(monkeypatch)
    controller = ExecutionControlController()

    try:
        controller.arm(account_suffix="0001", confirmation="not-accepted")
    except PermissionError as exc:
        assert "risk confirmation" in str(exc)
    else:  # pragma: no cover - fail-closed invariant
        raise AssertionError("invalid risk confirmation must not arm live execution")

    assert controller.status()["session_armed"] is False
