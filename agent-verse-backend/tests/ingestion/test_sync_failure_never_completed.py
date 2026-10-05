"""USR-1: a sync whose connector cannot read its source is never "completed, 0 failures".

Every registered connector runs through the real sync task
(``app.ingestion.scheduler._sync_source_async``) with the real job tracker and
the real ingestion pipeline, against a source it cannot read:

* database / broker / mail connectors dial a closed local port (a real
  connection failure — the operator allowlist admits 127.0.0.1 for the test);
* HTTP API connectors get a 401, a 503 or a transport error for every request;
* cloud-SDK connectors whose endpoint is fixed get their SDK client failing.

The job must end ``failed`` (or ``partial``) with at least one counted failure
and an error message. Connectors used to log such failures and return / skip,
so PDF / DOCX downloads, a refused PostgreSQL connection and a dozen API
connectors were reported as successful, empty syncs.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
import uuid
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.ingestion.connector_registry import _REGISTRY, load_all_connectors
from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import CONFIG_STATUS_OK, SourceConfig, SourceFamily

pytestmark = pytest.mark.asyncio

_CLOSED = "127.0.0.1"  # port 1 below: nothing listens, the connect is refused at once
_PUBLIC_IP = "93.184.216.34"


class _RetriedError(Exception):
    """What the fake Celery task's ``retry`` raises (the sync asks for a retry)."""


class _Task:
    def retry(self, **_kwargs: Any) -> _RetriedError:
        return _RetriedError()


class _Store:
    """In-memory SourceConfigStore stand-in (the parts the sync task uses)."""

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.synced: list[dict[str, Any]] = []

    async def get(self, source_id: str, tenant_id: str) -> SourceConfig | None:
        return self.config

    async def update(self, source_id: str, tenant_id: str, **fields: Any) -> SourceConfig:
        for key, value in fields.items():
            setattr(self.config, key, value)
        return self.config

    async def mark_synced(self, source_id: str, tenant_id: str, **stats: Any) -> None:
        self.synced.append(stats)

    async def mark_needs_configuration(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("the test sources are fully configured")


@pytest.fixture(autouse=True)
def _test_egress_and_flags(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Admit the closed local port (operator allowlist) and enable every connector."""
    import os

    from app.core.config import get_settings

    allow = os.environ.get("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", f"{allow},127.0.0.1")
    monkeypatch.setenv("INGESTION_CONNECTOR_DUCKDB_ENABLED", "true")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _sync(source_type: str, connection_config: dict[str, Any]) -> Any:
    from app.ingestion.scheduler import _sync_source_async

    load_all_connectors()
    config = SourceConfig(
        source_id=uuid.uuid4().hex,
        tenant_id="tid-usr1",
        name=f"usr1-{source_type}",
        family=SourceFamily.WEB,
        source_type=source_type,
        connection_config=connection_config,
        collection_id="col-usr1",
    )
    assert config.config_status == CONFIG_STATUS_OK
    tracker = IngestionJobTracker()
    pipeline = IngestionPipeline(dry_run=True)
    store = _Store(config)
    with (
        patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(tracker, pipeline, store),
        ),
        contextlib.suppress(_RetriedError),
    ):
        await _sync_source_async(
            task=_Task(),
            source_id=config.source_id,
            tenant_id=config.tenant_id,
            triggered_by="manual",
        )
    (job,) = tracker._jobs.values()
    return job, store


def _assert_failure_is_reported(job: Any, store: _Store) -> None:
    assert job.status in ("failed", "partial"), (job.status, job.error_message)
    assert job.docs_failed >= 1, job
    assert job.error_message, job
    assert not (job.status == "completed" and job.docs_failed == 0)
    assert store.synced and store.synced[-1]["failed"] >= 1  # backoff sees the failure


# ── HTTP API connectors: every request answers 401 / 503 or fails in transport ──

_HTTP_SOURCES: dict[str, dict[str, Any]] = {
    "arxiv": {"categories": ["cs.AI"]},
    "confluence": {"base_url": "https://acme.atlassian.net/wiki", "space_keys": ["ENG"]},
    "discord": {"bot_token": "t", "channel_ids": ["1"]},
    "elasticsearch": {"url": "https://example.com:9200", "index": "logs"},
    "opensearch": {"url": "https://example.com:9200", "index": "logs"},
    "github": {"token": "t", "repos": ["acme/app"]},
    "gitlab": {"base_url": "https://gitlab.com", "token": "t", "project_ids": [1]},
    "hubspot": {"access_token": "t"},
    "http": {"url": f"https://{_PUBLIC_IP}/api/items"},
    "rest": {"url": f"https://{_PUBLIC_IP}/api/items"},
    "jira": {"base_url": "https://acme.atlassian.net", "project_keys": ["ENG"]},
    "notion": {"api_key": "t"},
    "pagerduty": {"api_token": "t"},
    "pdf_file": {"urls": ["https://example.com/handbook.pdf"]},
    "docx_file": {"urls": ["https://example.com/handbook.docx"]},
    "rss": {"url": "https://example.com/feed.xml"},
    "atom": {"url": "https://example.com/feed.atom"},
    "salesforce": {"login_url": "https://example.com", "sobjects": ["Account"]},
    "sentry": {"base_url": "https://example.com/api/0", "org_slug": "acme"},
    "servicenow": {"instance": "https://acme.service-now.com", "tables": ["incident"]},
    "sharepoint": {"tenant_id": "t", "client_id": "c", "client_secret": "s", "site_id": "x"},
    "slack": {"bot_token": "t", "channels": ["C1"]},
    "teams": {"tenant_id": "t", "client_id": "c", "client_secret": "s", "team_id": "x"},
    "web_crawl": {"seed_urls": ["https://example.com/docs"], "crawl_delay_seconds": 0},
    "youtube": {"channel_id": "UC1", "api_key": "k"},
    "zendesk": {"subdomain": "acme", "api_token": "t", "email": "a@example.com"},
}


def _http_failure(mode: str) -> Callable[..., Any]:
    async def _send(self: httpx.AsyncClient, request: httpx.Request, **_: Any) -> httpx.Response:
        if mode == "transport":
            raise httpx.ConnectError("connection refused", request=request)
        status = 401 if mode == "401" else 503
        return httpx.Response(
            status, json={"ok": False, "error": "nope", "message": "nope"}, request=request
        )

    return _send


@pytest.mark.parametrize("mode", ["401", "503", "transport"])
@pytest.mark.parametrize("source_type", sorted(_HTTP_SOURCES))
async def test_http_connector_failures_fail_the_sync(source_type: str, mode: str) -> None:
    with patch.object(httpx.AsyncClient, "send", _http_failure(mode)):
        job, store = await _sync(source_type, _HTTP_SOURCES[source_type])
    _assert_failure_is_reported(job, store)


# ── Database / broker / mail connectors: a real refused connection ─────────────

_REFUSED_SOURCES: dict[str, dict[str, Any]] = {
    "postgresql": {
        "host": _CLOSED, "port": 1, "database": "app", "username": "u", "password": "p",
        "tables": ["public.orders"],
    },
    "mysql": {"host": _CLOSED, "port": 1, "table": "orders", "username": "u"},
    "mariadb": {"host": _CLOSED, "port": 1, "table": "orders", "username": "u"},
    "clickhouse": {"host": _CLOSED, "port": 1, "table": "events"},
    "mongodb": {
        "uri": f"mongodb://{_CLOSED}:1/app", "collections": ["orders"], "timeout_ms": 300,
        "direct_connection": True,
    },
    "redis": {"host": _CLOSED, "port": 1},
    "influxdb": {
        "url": f"http://{_CLOSED}:1", "token": "t", "org": "o", "bucket": "b",
        "measurement": "m",
    },
    "imap": {"host": _CLOSED, "port": 1, "ssl": False, "username": "u"},
    "gmail": {"host": _CLOSED, "port": 1, "ssl": False, "username": "u"},
    "mqtt": {"host": _CLOSED, "port": 1, "topics": ["t/#"], "timeout_seconds": 0},
    "s3": {
        "bucket": "b", "endpoint_url": f"http://{_CLOSED}:1",
        "credentials": {"access_key_id": "x", "secret_access_key": "y"},
    },
    "minio": {
        "bucket": "b", "endpoint_url": f"http://{_CLOSED}:1",
        "credentials": {"access_key_id": "x", "secret_access_key": "y"},
    },
    "kafka": {"bootstrap_servers": f"{_CLOSED}:1", "topics": ["orders"]},
    "duckdb": {"database": "does-not-exist.duckdb"},
    "gdrive": {"folder_id": "f", "key_path": "/nonexistent/service-account.json"},
    "agent_generated": {},
}


@pytest.mark.parametrize("source_type", sorted(_REFUSED_SOURCES))
async def test_connection_failures_fail_the_sync(source_type: str) -> None:
    job, store = await _sync(source_type, _REFUSED_SOURCES[source_type])
    _assert_failure_is_reported(job, store)


async def test_neo4j_connection_failure_fails_the_sync() -> None:
    import neo4j

    class _Driver:
        def __enter__(self) -> _Driver:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def session(self) -> Any:
            raise neo4j.exceptions.ServiceUnavailable("Couldn't connect to 127.0.0.1:1")

    with patch.object(neo4j.GraphDatabase, "driver", return_value=_Driver()):
        job, store = await _sync(
            "neo4j", {"uri": f"bolt://{_CLOSED}:1", "cypher": "MATCH (n) RETURN n"}
        )
    _assert_failure_is_reported(job, store)


# ── Cloud SDK connectors with fixed endpoints: the SDK client fails ────────────


@contextlib.contextmanager
def _fake_sdk(dotted: str, **attrs: Any) -> Iterator[None]:
    """Stand in for an SDK module without importing the real one (other tests fake
    these modules too; importing the real SDK here would leak into them)."""
    parent_name, _, leaf = dotted.rpartition(".")
    fake = ModuleType(dotted)
    fake.__dict__.update(attrs)
    parent = importlib.import_module(parent_name)
    with patch.dict(sys.modules, {dotted: fake}), patch.object(parent, leaf, fake, create=True):
        yield


@contextlib.contextmanager
def _sdk_failure(source_type: str) -> Iterator[None]:
    boom = ConnectionError(f"{source_type}: could not reach the service")
    failing = MagicMock(side_effect=boom)
    if source_type == "kinesis":
        session = MagicMock()
        session.return_value.client.return_value.list_shards.side_effect = boom
        with patch("boto3.Session", session):
            yield
    elif source_type == "azure_blob":
        client_cls = MagicMock()
        client_cls.from_connection_string.side_effect = boom
        with _fake_sdk("azure.storage.blob", BlobServiceClient=client_cls):
            yield
    elif source_type == "gcs":
        with _fake_sdk("google.cloud.storage", Client=failing):
            yield
    elif source_type == "bigquery":
        with _fake_sdk("google.cloud.bigquery", Client=failing):
            yield
    elif source_type == "pubsub":
        with _fake_sdk("google.cloud.pubsub_v1", SubscriberClient=failing):
            yield
    elif source_type == "snowflake":
        with _fake_sdk("snowflake.connector", connect=failing, DictCursor=object):
            yield
    else:  # pragma: no cover - table below and branches must agree
        raise AssertionError(source_type)


_SDK_SOURCES: dict[str, dict[str, Any]] = {
    "kinesis": {"stream_name": "s", "access_key_id": "x", "secret_access_key": "y"},
    "azure_blob": {
        "connection_string": "BlobEndpoint=https://example.com/;SharedAccessSignature=sv=x",
        "container": "c",
    },
    "gcs": {"bucket": "b"},
    "bigquery": {"project": "p", "table": "p.d.t"},
    "pubsub": {"project": "p", "subscription": "s"},
    "snowflake": {"account": "acme", "user": "u", "password": "p", "query": "SELECT 1"},
}


@pytest.mark.parametrize("source_type", sorted(_SDK_SOURCES))
async def test_cloud_sdk_failures_fail_the_sync(source_type: str) -> None:
    with _sdk_failure(source_type):
        job, store = await _sync(source_type, _SDK_SOURCES[source_type])
    _assert_failure_is_reported(job, store)


async def test_every_registered_connector_is_covered() -> None:
    load_all_connectors()
    covered = set(_HTTP_SOURCES) | set(_REFUSED_SOURCES) | set(_SDK_SOURCES) | {"neo4j"}
    assert covered == set(_REGISTRY), sorted(set(_REGISTRY) ^ covered)


# ── Per-item failures: the rest syncs, the job is partial, never completed ─────


def _real_pdf() -> bytes:
    """A readable one-page PDF (a fake ``%PDF`` stub now fails as unreadable, P1b-2)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, "Employee handbook: the Pune office opens at 09:00 on weekdays.")
    return bytes(pdf.output())


async def test_one_unreadable_url_makes_the_job_partial() -> None:
    async def _send(self: httpx.AsyncClient, request: httpx.Request, **_: Any) -> httpx.Response:
        if request.url.path.endswith("missing.pdf"):
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=_real_pdf(), request=request)

    real_ingest = IngestionPipeline.ingest

    async def _ingest(self: IngestionPipeline, raw: Any, config: Any, **kw: Any) -> Any:
        result = await real_ingest(self, raw, config, **kw)
        if result.status == "dry_run":  # the harness pipeline is dry-run: parsed = indexed
            result.status = "indexed"
        return result

    with (
        patch.object(httpx.AsyncClient, "send", _send),
        patch.object(IngestionPipeline, "ingest", _ingest),
    ):
        job, _store = await _sync(
            "pdf_file",
            {"urls": ["https://example.com/ok.pdf", "https://example.com/missing.pdf"]},
        )
    assert job.status in ("partial", "failed")
    assert job.docs_failed == 1
    assert "1 document(s) failed" in job.error_message


async def test_a_clean_sync_still_completes() -> None:
    async def _send(self: httpx.AsyncClient, request: httpx.Request, **_: Any) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1, "title": "a"}], request=request)

    with patch.object(httpx.AsyncClient, "send", _send):
        job, _store = await _sync("http", {"url": f"https://{_PUBLIC_IP}/api/items"})
    assert (job.status, job.docs_failed, job.error_message) == ("completed", 0, "")
