"""Tests for ServicesAPI — 6 cases."""

from __future__ import annotations

import pytest

from app.chat.services_api import ServicesAPI

TENANT = "t1"
OTHER = "t2"


@pytest.fixture()
def api() -> ServicesAPI:
    return ServicesAPI()


def test_initiate_connection_returns_oauth_url(api: ServicesAPI) -> None:
    result = api.initiate_connection(TENANT, "GitHub", "https://github.com", ["repo"])
    assert "service_id" in result
    assert "oauth_url" in result
    assert result["oauth_url"].startswith("https://")


def test_list_services(api: ServicesAPI) -> None:
    api.initiate_connection(TENANT, "GitHub", "https://github.com")
    api.initiate_connection(TENANT, "Slack", "https://slack.com")
    api.initiate_connection(OTHER, "Other", "https://other.com")
    services = api.list_services(TENANT)
    assert len(services) == 2
    assert all(s.tenant_id == TENANT for s in services)


def test_disconnect_service(api: ServicesAPI) -> None:
    result = api.initiate_connection(TENANT, "Jira", "https://jira.com")
    sid = result["service_id"]
    ok = api.disconnect_service(sid, TENANT)
    assert ok
    services = api.list_services(TENANT)
    assert all(s.id != sid for s in services)


def test_disconnect_service_rls(api: ServicesAPI) -> None:
    result = api.initiate_connection(TENANT, "GitHub", "https://github.com")
    sid = result["service_id"]
    ok = api.disconnect_service(sid, OTHER)
    assert not ok


def test_service_status_connected(api: ServicesAPI) -> None:
    result = api.initiate_connection(TENANT, "GitHub", "https://github.com")
    svc = api.get_service(result["service_id"], TENANT)
    assert svc is not None
    assert svc.status == "connected"


def test_get_service_wrong_tenant(api: ServicesAPI) -> None:
    result = api.initiate_connection(TENANT, "GitHub", "https://github.com")
    svc = api.get_service(result["service_id"], OTHER)
    assert svc is None
