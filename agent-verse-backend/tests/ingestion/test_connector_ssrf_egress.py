"""Every connector whose host comes from tenant config must refuse internal targets.

Ingestion connectors fetch a host the tenant supplies in ``connection_config``:
Jira/Confluence ``base_url``, a self-hosted GitLab, a ServiceNow ``instance``, an
Elasticsearch ``url`` (which defaulted to ``http://localhost:9200``), web-crawl
``seed_urls``, a list of PDF ``urls``. Only ``http_connector`` ran the SSRF guard.

The payoff is unusually direct: whatever the platform fetches is parsed,
chunked, embedded and indexed into *the requesting tenant's own* collection,
where they retrieve it through the ordinary knowledge API. A tenant creating a
web-crawl source pointed at
``http://169.254.169.254/latest/meta-data/iam/security-credentials/`` has the
platform's cloud credentials delivered into their RAG index.

These tests drive the real connector methods (no HTTP mocking) so a connector
that forgets the guard fails here, and would make a real outbound request —
which is the failure being prevented.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ingestion.connector_egress import (
    ConnectorEgressBlocked,
    assert_source_url,
    source_url_is_allowed,
)
from app.ingestion.source_config import SourceConfig, SourceFamily

# Targets a tenant would reach for. Literal IPs so no DNS is needed and the test
# can never accidentally touch a real host.
_METADATA = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
_LOOPBACK = "http://127.0.0.1:9200"
_PRIVATE = "http://10.1.2.3:8080"
_LINK_LOCAL = "http://169.254.0.7/"


class TestEgressPolicy:
    @pytest.mark.parametrize("url", [_METADATA, _LOOPBACK, _PRIVATE, _LINK_LOCAL])
    def test_internal_targets_are_blocked(self, url: str) -> None:
        with pytest.raises(ConnectorEgressBlocked):
            assert_source_url(url, context="test")
        assert source_url_is_allowed(url, context="test") is False

    @pytest.mark.parametrize("url", ["", "file:///etc/passwd", "gopher://x/", "not-a-url"])
    def test_non_http_and_malformed_are_blocked(self, url: str) -> None:
        with pytest.raises(ConnectorEgressBlocked):
            assert_source_url(url, context="test")

    def test_tenant_config_cannot_widen_the_policy(self) -> None:
        """connection_config is attacker-controlled; it must not grant an exemption."""
        config = SourceConfig(
            source_id="s",
            tenant_id="t",
            name="n",
            family=SourceFamily.WEB,
            source_type="http",
            connection_config={
                "allow_internal": True,
                "allowed_domains": ["169.254.169.254"],
                "ssrf_bypass": True,
            },
        )
        with pytest.raises(ConnectorEgressBlocked):
            assert_source_url(_METADATA, context="test", config=config)

    def test_operator_allowlist_needs_both_halves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An allowlist with the flag off must not punch a hole."""
        from app.core.config import get_settings

        monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "10.1.2.3")
        monkeypatch.delenv("INGESTION_ALLOW_INTERNAL_SOURCES", raising=False)
        get_settings.cache_clear()
        try:
            with pytest.raises(ConnectorEgressBlocked):
                assert_source_url(_PRIVATE, context="test")

            monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
            get_settings.cache_clear()
            assert_source_url(_PRIVATE, context="test")  # both halves: allowed
        finally:
            get_settings.cache_clear()


def _config(source_type: str, connection_config: dict[str, Any]) -> SourceConfig:
    return SourceConfig(
        source_id=f"src-{source_type}",
        tenant_id="tenant-ssrf",
        name=source_type,
        family=SourceFamily.WEB,
        source_type=source_type,
        collection_id="col-1",
        connection_config=connection_config,
    )


# (module, class name, connection_config pointing at an internal host)
_VULNERABLE_CONNECTORS: list[tuple[str, str, str, dict[str, Any]]] = [
    (
        "jira_connector",
        "JiraConnector",
        "jira",
        {"base_url": "http://169.254.169.254", "username": "u", "api_token": "t"},
    ),
    (
        "confluence_connector",
        "ConfluenceConnector",
        "confluence",
        {"base_url": "http://169.254.169.254", "username": "u", "api_token": "t"},
    ),
    (
        "gitlab_connector",
        "GitLabConnector",
        "gitlab",
        {"base_url": "http://10.1.2.3", "token": "t", "project_ids": [1]},
    ),
    (
        "elasticsearch_connector",
        "ElasticsearchConnector",
        "elasticsearch",
        {"url": "http://127.0.0.1:9200", "index": "secrets"},
    ),
    (
        "servicenow_connector",
        "ServiceNowConnector",
        "servicenow",
        {"instance": "http://169.254.169.254", "username": "u", "password": "p"},
    ),
    (
        "sentry_connector",
        "SentryConnector",
        "sentry",
        {"base_url": "http://10.0.0.5", "token": "t", "organization": "o"},
    ),
    (
        "web_crawl_connector",
        "WebCrawlConnector",
        "web_crawl",
        {"seed_urls": [_METADATA], "max_pages": 1, "max_depth": 1},
    ),
    (
        "pdf_file_connector",
        "PDFFileConnector",
        "pdf_file",
        {"urls": [_METADATA]},
    ),
    (
        "salesforce_connector",
        "SalesforceConnector",
        "salesforce",
        {
            "login_url": "http://169.254.169.254",
            "client_id": "c",
            "client_secret": "s",
            "username": "u",
            "password": "p",
        },
    ),
]


def _load(module: str, cls_name: str) -> Any:
    import importlib

    mod = importlib.import_module(f"app.ingestion.connectors.{module}")
    return getattr(mod, cls_name)


@pytest.mark.parametrize(
    ("module", "cls_name", "source_type", "connection_config"),
    _VULNERABLE_CONNECTORS,
    ids=[c[2] for c in _VULNERABLE_CONNECTORS],
)
@pytest.mark.asyncio
async def test_validate_connection_refuses_internal_hosts(
    module: str, cls_name: str, source_type: str, connection_config: dict[str, Any]
) -> None:
    """``validate_connection`` runs against the same attacker-supplied URL.

    It is reachable from source creation, so it must fail closed too — otherwise
    it doubles as a blind-SSRF oracle that reports the internal host's response
    status and error text back to the tenant.
    """
    connector = _load(module, cls_name)()
    health = await connector.validate_connection(_config(source_type, connection_config))
    assert health.ok is False, f"{source_type} validated an internal host"
    assert "block" in health.error.lower() or "ssrf" in health.error.lower(), (
        f"{source_type} rejected the host but not as an egress block: {health.error!r}"
    )


@pytest.mark.parametrize(
    ("module", "cls_name", "source_type", "connection_config"),
    _VULNERABLE_CONNECTORS,
    ids=[c[2] for c in _VULNERABLE_CONNECTORS],
)
@pytest.mark.asyncio
async def test_get_delta_yields_nothing_for_internal_hosts(
    module: str, cls_name: str, source_type: str, connection_config: dict[str, Any]
) -> None:
    """No document from an internal host may ever reach the pipeline."""
    connector = _load(module, cls_name)()
    config = _config(source_type, connection_config)
    produced = []
    try:
        async for doc, _cursor in connector.get_delta(config, None):
            produced.append(doc)
    except (ConnectorEgressBlocked, Exception) as exc:  # noqa: BLE001
        # Raising is an acceptable fail-closed outcome; silently producing is not.
        assert not isinstance(exc, AssertionError)
    assert produced == [], (
        f"{source_type} fetched and yielded {len(produced)} document(s) from an "
        "internal host — SSRF straight into the tenant's knowledge index"
    )
