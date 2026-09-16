"""Tests for the org-level connector registry — app/org/connectors/__init__.py"""
from __future__ import annotations

import pytest

from app.org.connectors import (
    BaseConnector,
    ConnectorCategory,
    ConnectorMeta,
    ConnectorRegistry,
    ExpensifyConnector,
    GrafanaConnector,
    LookerConnector,
    org_connector_registry,
)


# ── global registry wiring ────────────────────────────────────────────────────


def test_global_registry_has_three_builtin_connectors():
    names = {m.name for m in org_connector_registry.list_all()}
    assert {"looker", "expensify", "grafana"} <= names


def test_get_returns_registered_connector():
    conn = org_connector_registry.get("looker")
    assert isinstance(conn, LookerConnector)


def test_get_unknown_returns_none():
    assert org_connector_registry.get("does-not-exist") is None


def test_list_by_category_filters_correctly():
    finance_connectors = org_connector_registry.list_by_category(ConnectorCategory.FINANCE)
    assert any(c.name == "expensify" for c in finance_connectors)
    assert all(c.category == ConnectorCategory.FINANCE for c in finance_connectors)


@pytest.mark.asyncio
async def test_health_check_all_returns_true_for_each_registered_connector():
    results = await org_connector_registry.health_check_all()
    assert results["looker"] is True
    assert results["expensify"] is True
    assert results["grafana"] is True


# ── individual connector implementations ──────────────────────────────────────


@pytest.mark.asyncio
async def test_looker_connect_requires_api_key():
    conn = LookerConnector()
    assert await conn.connect({}) is False
    assert await conn.connect({"looker_api_key": "x"}) is True


@pytest.mark.asyncio
async def test_looker_execute_and_list_actions():
    conn = LookerConnector()
    result = await conn.execute("run_look", {"look_id": 1})
    assert result["status"] == "not_configured"
    actions = await conn.list_actions()
    assert "run_look" in actions


@pytest.mark.asyncio
async def test_expensify_connect_requires_partner_user_id():
    conn = ExpensifyConnector()
    assert await conn.connect({}) is False
    assert await conn.connect({"expensify_partner_user_id": "u1"}) is True
    assert conn.meta.high_sensitivity is True
    assert "finance" in conn.meta.dept_restricted


@pytest.mark.asyncio
async def test_expensify_list_actions():
    conn = ExpensifyConnector()
    actions = await conn.list_actions()
    assert "create_expense" in actions


@pytest.mark.asyncio
async def test_expensify_execute_returns_not_configured_stub():
    conn = ExpensifyConnector()
    result = await conn.execute("create_expense", {"amount": 10})
    assert result == {"action": "create_expense", "status": "not_configured", "result": None}


@pytest.mark.asyncio
async def test_grafana_connect_requires_both_key_and_url():
    conn = GrafanaConnector()
    assert await conn.connect({"grafana_api_key": "k"}) is False
    assert await conn.connect({"grafana_api_key": "k", "grafana_url": "https://g"}) is True
    assert conn.meta.always_audit is True


@pytest.mark.asyncio
async def test_grafana_health_check_and_execute():
    conn = GrafanaConnector()
    assert await conn.health_check() is True
    result = await conn.execute("fire_alert", {})
    assert result["action"] == "fire_alert"


@pytest.mark.asyncio
async def test_grafana_list_actions():
    conn = GrafanaConnector()
    actions = await conn.list_actions()
    assert "fire_alert" in actions
    assert "get_alert_rules" in actions


# ── ConnectorRegistry (fresh instance) ────────────────────────────────────────


class _FakeConnector(BaseConnector):
    meta = ConnectorMeta(
        name="fake",
        display_name="Fake",
        category=ConnectorCategory.DATA,
        auth_type="none",
    )

    async def connect(self, credentials: dict) -> bool:
        return True

    async def health_check(self) -> bool:
        return True

    async def execute(self, action: str, inputs: dict) -> dict:
        return {"ok": True}


class _UnhealthyConnector(BaseConnector):
    meta = ConnectorMeta(
        name="unhealthy",
        display_name="Unhealthy",
        category=ConnectorCategory.MONITORING,
        auth_type="none",
    )

    async def connect(self, credentials: dict) -> bool:
        return True

    async def health_check(self) -> bool:
        raise RuntimeError("connection refused")

    async def execute(self, action: str, inputs: dict) -> dict:
        return {}


def test_register_and_get_custom_connector():
    registry = ConnectorRegistry()
    registry.register(_FakeConnector())
    assert isinstance(registry.get("fake"), _FakeConnector)


def test_list_all_on_empty_registry():
    registry = ConnectorRegistry()
    assert registry.list_all() == []


@pytest.mark.asyncio
async def test_health_check_all_handles_exceptions_gracefully():
    registry = ConnectorRegistry()
    registry.register(_FakeConnector())
    registry.register(_UnhealthyConnector())
    results = await registry.health_check_all()
    assert results["fake"] is True
    assert results["unhealthy"] is False


@pytest.mark.asyncio
async def test_base_connector_default_list_actions_is_empty():
    conn = _FakeConnector()
    assert await conn.list_actions() == []
