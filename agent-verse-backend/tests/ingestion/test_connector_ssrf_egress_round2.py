"""SSRF round 2: the connectors / ingestors the first egress pass (7cf37e5e) missed.

Same payoff as round 1 — whatever these fetch is chunked and indexed into the
requesting tenant's own knowledge collection — through different entry points:

* URL connectors: RSS (``feedparser.parse(url)`` also reads *local files* and
  follows redirects), InfluxDB ``url``, S3/MinIO ``endpoint_url``, Zendesk
  ``subdomain`` (``"127.0.0.1:1/#"`` rewrites the host), SharePoint's
  ``downloadUrl``.
* host/port connectors: PostgreSQL, MySQL, ClickHouse, MongoDB, Neo4j dial a
  tenant-chosen host — the resolved IP must be public.
* the legacy ``/ingest/confluence`` and ``/ingest/jira`` ingestors and the agent's
  ``knowledge.ingest`` tool (which followed redirects blind).

Targets are 127.0.0.1:1 / literal internal IPs so no DNS is needed and, if a
guard is missing, the connection is refused immediately instead of hanging.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.ingestion.connector_egress import (
    ConnectorEgressBlockedError,
    _dsn_hosts,
    assert_source_dsn,
    assert_source_host,
    guarded_request,
)
from app.ingestion.source_config import SourceConfig, SourceFamily

_CLOSED = "http://127.0.0.1:1"
_METADATA = "http://169.254.169.254/latest/meta-data/"


def _config(source_type: str, cc: dict[str, Any]) -> SourceConfig:
    return SourceConfig(
        source_id=f"src-{source_type}",
        tenant_id="tenant-ssrf",
        name=source_type,
        family=SourceFamily.WEB,
        source_type=source_type,
        collection_id="col-1",
        connection_config=cc,
    )


def _load(module: str, cls_name: str) -> Any:
    import importlib

    return getattr(importlib.import_module(f"app.ingestion.connectors.{module}"), cls_name)


_CASES: list[tuple[str, str, str, dict[str, Any]]] = [
    ("rss_connector", "RSSConnector", "rss", {"url": _CLOSED + "/feed"}),
    ("rss_connector", "RSSConnector", "rss-file", {"url": "/etc/hosts"}),
    ("rss_connector", "RSSConnector", "rss-file-uri", {"url": "file:///etc/hosts"}),
    ("influxdb_connector", "InfluxDBConnector", "influxdb", {"url": _CLOSED, "bucket": "b"}),
    ("influxdb_connector", "InfluxDBConnector", "influxdb-default", {"bucket": "b"}),
    ("s3_connector", "S3Connector", "s3", {"bucket": "b", "endpoint_url": _CLOSED}),
    ("minio_connector", "MinIOConnector", "minio", {"bucket": "b", "endpoint_url": _METADATA}),
    ("minio_connector", "MinIOConnector", "minio-default", {"bucket": "b"}),
    ("postgresql_connector", "PostgreSQLConnector", "pg-host",
     {"host": "127.0.0.1", "port": 1, "user": "u", "password": "p", "database": "d"}),
    ("postgresql_connector", "PostgreSQLConnector", "pg-dsn",
     {"dsn": "postgresql://u:p@127.0.0.1:1/d"}),
    ("postgresql_connector", "PostgreSQLConnector", "pg-dsn-query-host",
     {"dsn": "postgresql://u:p@example.com/d?host=10.0.0.5"}),
    ("mysql_connector", "MySQLConnector", "mysql", {"host": "127.0.0.1", "port": 1}),
    ("clickhouse_connector", "ClickHouseConnector", "clickhouse",
     {"host": "169.254.169.254", "port": 8123}),
    ("mongodb_connector", "MongoDBConnector", "mongo-host",
     {"host": "10.0.0.5", "database": "d", "collection": "c"}),
    ("mongodb_connector", "MongoDBConnector", "mongo-uri",
     {"uri": "mongodb://u:p@example.com:27017,127.0.0.1:1/", "database": "d",
      "collection": "c"}),
    ("neo4j_connector", "Neo4jConnector", "neo4j", {"uri": "bolt://127.0.0.1:1"}),
    ("neo4j_connector", "Neo4jConnector", "neo4j-default", {}),
    ("zendesk_connector", "ZendeskConnector", "zendesk",
     {"subdomain": "127.0.0.1:1/#", "api_token": "t", "email": "e"}),
]


@pytest.mark.parametrize(
    ("module", "cls_name", "case", "cc"), _CASES, ids=[c[2] for c in _CASES]
)
@pytest.mark.asyncio
async def test_validate_connection_refuses_internal_targets(
    module: str, cls_name: str, case: str, cc: dict[str, Any]
) -> None:
    connector = _load(module, cls_name)()
    health = await connector.validate_connection(_config(case, cc))
    assert health.ok is False, f"{case} validated an internal target"
    err = (health.error or "").lower()
    assert "block" in err or "ssrf" in err, f"{case}: not an egress block: {health.error!r}"


@pytest.mark.parametrize(
    ("module", "cls_name", "case", "cc"), _CASES, ids=[c[2] for c in _CASES]
)
@pytest.mark.asyncio
async def test_get_delta_refuses_internal_targets(
    module: str, cls_name: str, case: str, cc: dict[str, Any]
) -> None:
    """The guard must fire (raise) — not merely "nothing came back" because the
    driver is missing or the port happened to be closed."""
    connector = _load(module, cls_name)()
    with pytest.raises(ConnectorEgressBlockedError):
        async for _doc, _cursor in connector.get_delta(_config(case, cc), None):
            pytest.fail(f"{case} yielded a document from an internal target")


@pytest.mark.asyncio
async def test_zendesk_refuses_off_host_next_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """``next_page`` comes from the response body; it must stay on the tenant's
    ``<subdomain>.zendesk.com`` host."""
    from app.ingestion.connectors import zendesk_connector

    seen: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={"tickets": [], "next_page": _METADATA, "end_of_stream": False},
        )

    real = httpx.AsyncClient

    def _client(*a: Any, **kw: Any) -> httpx.AsyncClient:
        kw["transport"] = httpx.MockTransport(_handler)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    cfg = _config("zendesk", {"subdomain": "acme", "api_token": "t", "email": "e"})
    with pytest.raises(ConnectorEgressBlockedError):
        async for _ in zendesk_connector.ZendeskConnector().get_delta(cfg, None):
            pass
    assert all("169.254" not in u for u in seen)


@pytest.mark.asyncio
async def test_sharepoint_download_url_is_guarded() -> None:
    from app.ingestion.connectors.sharepoint_connector import SharePointConnector

    client = SharePointConnector(tenant_id="t", client_id="c", client_secret="s")
    with pytest.raises(ConnectorEgressBlockedError):
        await client._download(_METADATA)


def test_sharepoint_rejects_path_injection_in_ids() -> None:
    from app.ingestion.connectors.sharepoint_connector import SharePointConnector

    with pytest.raises(ConnectorEgressBlockedError):
        SharePointConnector(tenant_id="x/../../evil", client_id="c", client_secret="s")


# ── helpers ───────────────────────────────────────────────────────────────────


def test_dsn_host_extraction() -> None:
    assert _dsn_hosts("postgresql://u:p@h1:5432,h2:5433/db") == [("h1", "5432"), ("h2", "5433")]
    assert _dsn_hosts("postgresql://u:p@[::1]:5432/db") == [("::1", "5432")]
    assert ("10.0.0.5", "") in _dsn_hosts("postgresql://u@example.com/d?host=10.0.0.5")
    assert _dsn_hosts("host=10.0.0.5 port=5432 dbname=x") == [("10.0.0.5", "")]


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "/var/run/postgresql", ""]
)
def test_assert_source_host_blocks_internal(host: str) -> None:
    with pytest.raises(ConnectorEgressBlockedError):
        assert_source_host(host, 5432, context="t")


def test_assert_source_dsn_blocks_socket_dsn() -> None:
    with pytest.raises(ConnectorEgressBlockedError):
        assert_source_dsn("host=/var/run/postgresql dbname=x", context="t")


@pytest.mark.asyncio
async def test_guarded_request_revalidates_every_redirect_and_drops_auth() -> None:
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"location": "http://8.8.8.8/next"})
        if request.url.host == "8.8.8.8":
            return httpx.Response(302, headers={"location": _METADATA})
        return httpx.Response(200, text="SECRET-CREDS")

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as c:
        with pytest.raises(ConnectorEgressBlockedError):
            await guarded_request(
                c, "GET", "http://93.184.216.34/start", context="t",
                headers={"Authorization": "Bearer tenant-token"},
            )
    assert [r.url.host for r in requests] == ["93.184.216.34", "8.8.8.8"]
    assert "authorization" not in {k.lower() for k in requests[1].headers}


# ── legacy /ingest/confluence + /ingest/jira ingestors ───────────────────────


@pytest.mark.asyncio
async def test_legacy_confluence_ingestor_refuses_internal_base_url() -> None:
    from app.knowledge.ingestors.confluence_ingestor import ConfluenceIngestor

    with pytest.raises(ConnectorEgressBlockedError):
        await ConfluenceIngestor(base_url=_CLOSED, token="t", user="u").ingest_space("S")


@pytest.mark.asyncio
async def test_legacy_jira_ingestor_refuses_internal_base_url() -> None:
    from app.knowledge.ingestors.jira_ingestor import JiraIngestor

    with pytest.raises(ConnectorEgressBlockedError):
        await JiraIngestor(base_url=_METADATA, token="t", user="u").ingest_project("P")


# ── agent knowledge.ingest tool ──────────────────────────────────────────────


class _Pipeline:
    def __init__(self) -> None:
        self.ingested: list[Any] = []

    async def ingest(self, raw_doc: Any, config: Any) -> Any:  # pragma: no cover
        self.ingested.append(raw_doc)
        raise AssertionError("an internal response reached the ingestion pipeline")


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real = httpx.AsyncClient

    def _client(*a: Any, **kw: Any) -> httpx.AsyncClient:
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _client)


@pytest.mark.asyncio
async def test_knowledge_ingest_tool_refuses_internal_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tools.knowledge_ingest_tool import KnowledgeIngestTool

    hits: list[str] = []
    _patch_transport(monkeypatch, lambda r: hits.append(str(r.url)) or httpx.Response(200))
    pipeline = _Pipeline()
    out = await KnowledgeIngestTool().execute(_METADATA, pipeline=pipeline)
    assert out["job_status"] == "failed"
    assert hits == []
    assert pipeline.ingested == []


@pytest.mark.asyncio
async def test_knowledge_ingest_tool_does_not_follow_redirect_to_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tools.knowledge_ingest_tool import KnowledgeIngestTool

    hits: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"location": _METADATA})
        return httpx.Response(200, text="AWS-CREDS", headers={"content-type": "text/plain"})

    _patch_transport(monkeypatch, _handler)
    pipeline = _Pipeline()
    out = await KnowledgeIngestTool().execute("http://93.184.216.34/r", pipeline=pipeline)
    assert out["job_status"] == "failed"
    assert all("169.254" not in u for u in hits)
    assert pipeline.ingested == []
