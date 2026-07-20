"""Smoke tests for slim MonitorGroupService and /api/monitor-groups/* routes."""
from __future__ import annotations

import pytest

from omega_quant.api import create_app
from omega_quant.database import build_engine, build_session_factory, init_db
from omega_quant.monitor_groups import MonitorGroupService
from omega_quant.schemas import MonitorGroupCreate, MonitorGroupRead, MonitorGroupUpdate


def _endpoint(app, path: str, method: str = "GET"):
    method = method.upper()
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


@pytest.fixture()
def session():
    engine = build_engine("sqlite:///:memory:")
    init_db(engine)
    s = build_session_factory(engine)()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def svc(session):
    return MonitorGroupService(session)


@pytest.fixture()
def app(tmp_path):
    return create_app(f"sqlite:///{tmp_path / 'test.sqlite'}")


# --- MonitorGroupService unit tests ---

def test_seed_default_creates_mg_default_alpha(svc):
    svc.seed_default_monitor_group()
    group = svc.get_monitor_group("mg_default_alpha")
    assert group.id == "mg_default_alpha"
    assert group.name == "Alpha TigerOpen 监控组"
    assert group.status == "running"


def test_seed_default_is_idempotent(svc):
    svc.seed_default_monitor_group()
    svc.seed_default_monitor_group()
    groups = svc.list_monitor_groups(include_archived=True)
    assert sum(1 for g in groups if g.id == "mg_default_alpha") == 1


def test_create_list_get(svc):
    svc.seed_default_monitor_group()
    payload = MonitorGroupCreate(name="Test Group", description="desc", allocated_capital=5000.0)
    group = svc.create_monitor_group(payload)
    assert group.id.startswith("mg_")
    assert group.status == "stopped"

    groups = svc.list_monitor_groups()
    ids = [g.id for g in groups]
    assert group.id in ids
    assert "mg_default_alpha" in ids


def test_update_monitor_group(svc):
    svc.seed_default_monitor_group()
    update = MonitorGroupUpdate(name="Renamed", allocated_capital=20_000.0)
    group = svc.update_monitor_group("mg_default_alpha", update)
    assert group.name == "Renamed"
    assert group.allocated_capital == 20_000.0
    assert group.status == "running"


def test_set_monitor_status(svc):
    svc.seed_default_monitor_group()
    group = svc.set_monitor_status("mg_default_alpha", "paused")
    assert group.status == "paused"


def test_delete_monitor_group(svc):
    svc.seed_default_monitor_group()
    payload = MonitorGroupCreate(name="ToDelete", allocated_capital=1000.0)
    group = svc.create_monitor_group(payload)
    svc.delete_monitor_group(group.id)
    with pytest.raises(ValueError, match="not found"):
        svc.get_monitor_group(group.id)


def test_get_nonexistent_raises(svc):
    with pytest.raises(ValueError, match="not found"):
        svc.get_monitor_group("mg_nonexistent")


def test_list_excludes_archived_by_default(svc):
    svc.seed_default_monitor_group()
    payload = MonitorGroupCreate(name="Archived", allocated_capital=1000.0)
    group = svc.create_monitor_group(payload)
    svc.set_monitor_status(group.id, "archived")

    active = svc.list_monitor_groups()
    assert group.id not in [g.id for g in active]

    all_groups = svc.list_monitor_groups(include_archived=True)
    assert group.id in [g.id for g in all_groups]


# --- HTTP route endpoint unit tests ---

def test_list_monitor_groups_route(app):
    session = app.state.omega_session_factory()
    try:
        svc = MonitorGroupService(session)
        result = _endpoint(app, "/api/monitor-groups")(svc=svc)
        assert isinstance(result, list)
    finally:
        session.close()
        app.state.omega_core_runtime.close()


def test_create_monitor_group_route(app):
    session = app.state.omega_session_factory()
    try:
        svc = MonitorGroupService(session)
        payload = MonitorGroupCreate(name="HTTP Test Group", allocated_capital=3000.0)
        result = _endpoint(app, "/api/monitor-groups", "POST")(payload, svc)
        assert result.name == "HTTP Test Group"
        assert result.id.startswith("mg_")
    finally:
        session.close()
        app.state.omega_core_runtime.close()


def test_monitor_group_read_has_no_legacy_fields():
    fields = set(MonitorGroupRead.model_fields)
    legacy_fields = {"strategy_id", "risk_profile_id", "execution_mode", "auto_submit",
                     "asset_class_allow", "live_unlock_acknowledged_at", "used_capital", "free_capital"}
    assert not legacy_fields.intersection(fields)
    expected_fields = {"id", "name", "description", "allocated_capital", "status", "created_at", "updated_at"}
    assert expected_fields.issubset(fields)
