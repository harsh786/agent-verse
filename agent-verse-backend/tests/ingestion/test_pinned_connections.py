"""Regression: ingestion connectors connect to the address they validated.

Connectors checked the source URL (``assert_public_url`` / ``assert_source_url``
/ ``guarded_request``) and then fetched it with a plain ``httpx.AsyncClient``,
which resolves the name again — a DNS-rebinding window. They now use
``connector_egress.source_client`` (the pinned ``public_async_client`` carrying
the operator's on-prem allowlist) or ``public_async_client`` directly.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.ingestion.source_config import SourceConfig, SourceFamily
from app.net.ssrf_guard import PinnedNetworkBackend
from tests._pinning import install_connect_spy


def _config(source_type: str, **cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-pin",
        tenant_id="t1",
        name="pin-src",
        family=SourceFamily.AGENT_GENERATED,
        source_type=source_type,
        connection_config=cc,
    )


def _backend(client: Any) -> Any:
    return client._transport._pool._network_backend


def test_source_client_is_pinned_with_the_operator_allowlist() -> None:
    # tests/ingestion/conftest.py turns the operator escape hatch on for its
    # placeholder hosts; the pinned backend must carry that same allowlist.
    from app.ingestion.connector_egress import source_client

    client = source_client(timeout=5)
    try:
        backend = _backend(client)
        assert isinstance(backend, PinnedNetworkBackend)
        assert backend._allowed_domains and "acme.atlassian.net" in backend._allowed_domains
        assert client.follow_redirects is False
    finally:
        asyncio.run(client.aclose())


def test_source_client_has_no_allowlist_without_the_escape_hatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings
    from app.ingestion.connector_egress import source_client

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "false")
    get_settings.cache_clear()
    client = source_client()
    try:
        assert _backend(client)._allowed_domains is None
    finally:
        asyncio.run(client.aclose())


@pytest.mark.asyncio
async def test_http_connector_connects_via_pinned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingestion.connectors.http_connector import HttpApiConnector

    spy = install_connect_spy(monkeypatch)
    health = await HttpApiConnector().validate_connection(
        _config("http", url="https://rebind.example/items")
    )
    assert health.ok is False
    assert spy.dialed == ["rebind.example"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "cls", "cc"),
    [
        (
            "confluence_connector",
            "ConfluenceConnector",
            {"base_url": "https://rebind.example", "username": "u", "api_token": "t"},
        ),
        (
            "jira_connector",
            "JiraConnector",
            {"base_url": "https://rebind.example", "email": "u", "api_token": "t"},
        ),
        ("gitlab_connector", "GitLabConnector", {"base_url": "https://rebind.example"}),
        ("sentry_connector", "SentryConnector", {"base_url": "https://rebind.example"}),
        (
            "servicenow_connector",
            "ServiceNowConnector",
            {"instance": "https://rebind.example", "username": "u", "password": "p"},
        ),
        ("elasticsearch_connector", "ElasticsearchConnector", {"url": "https://rebind.example"}),
    ],
)
async def test_egress_guarded_connectors_connect_via_pinned_client(
    monkeypatch: pytest.MonkeyPatch, module: str, cls: str, cc: dict[str, Any]
) -> None:
    import importlib

    connector_cls = getattr(importlib.import_module(f"app.ingestion.connectors.{module}"), cls)
    spy = install_connect_spy(monkeypatch)
    health = await connector_cls().validate_connection(_config(connector_cls.source_type, **cc))
    assert health.ok is False
    assert spy.dialed == ["rebind.example"]
