"""SRC-ES: the Elasticsearch connector against a real Elasticsearch 8 (security on).

P1c (live SRC-ES-*) found the connector unable to sync Elasticsearch 8 at all and
wrong in several ways; each test below is one of those findings:

* every search sorted on ``_id`` as the tie-breaker — Elasticsearch 8 refuses
  field data on ``_id`` (400), so no document was ever read;
* documents of an index *pattern* were keyed by the pattern, not their concrete
  index: equal ``_id`` values in two indices collapsed into one document;
* a document without the sort field sorts last with a sentinel value that became
  the cursor, so every later incremental sync started past everything;
* the default sort field on an index that does not map it, a missing index and
  wrong credentials must fail with the reason;
* API-key authentication was not supported; deletions could not be reconciled.

The container is reachable on localhost only, so ``localhost`` / ``127.0.0.1``
go on the operator allowlist (tenant config can never widen it).
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from testcontainers.core.container import DockerContainer

from app.ingestion.base_connector import ConnectorFetchError
from app.ingestion.connectors.elasticsearch_connector import ElasticsearchConnector
from app.ingestion.source_config import SourceConfig, SourceFamily

pytestmark = pytest.mark.integration

_IMAGE = "docker.elastic.co/elasticsearch/elasticsearch:8.15.3"
_PASSWORD = "es-Test-pw-2026"
_T0 = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def es() -> Iterator[str]:
    container = (
        DockerContainer(_IMAGE)
        .with_env("discovery.type", "single-node")
        .with_env("xpack.security.enabled", "true")
        .with_env("xpack.security.http.ssl.enabled", "false")
        .with_env("xpack.security.transport.ssl.enabled", "false")
        .with_env("ELASTIC_PASSWORD", _PASSWORD)
        .with_env("ES_JAVA_OPTS", "-Xms384m -Xmx384m")
        .with_env("cluster.routing.allocation.disk.threshold_enabled", "false")
        .with_exposed_ports(9200)
    )
    with container:
        url = f"http://127.0.0.1:{container.get_exposed_port(9200)}"
        deadline = time.monotonic() + 180
        while True:
            try:
                r = httpx.get(f"{url}/_cluster/health", auth=("elastic", _PASSWORD), timeout=5)
                if r.status_code == 200 and r.json().get("status") in ("green", "yellow"):
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("Elasticsearch did not start")
            time.sleep(2)
        _seed(url)
        yield url


def _req(url: str, method: str, path: str, body: Any = None, ndjson: str | None = None) -> Any:
    kw: dict[str, Any] = {"auth": ("elastic", _PASSWORD), "timeout": 60}
    if ndjson is not None:
        kw.update(content=ndjson.encode(), headers={"Content-Type": "application/x-ndjson"})
    elif body is not None:
        kw["json"] = body
    r = httpx.request(method, f"{url}{path}", **kw)
    assert r.status_code < 300, (path, r.status_code, r.text[:300])
    return r.json() if r.content else {}


def _bulk(url: str, index: str, docs: list[tuple[str, dict[str, Any]]]) -> None:
    lines = []
    for doc_id, src in docs:
        lines += [json.dumps({"index": {"_index": index, "_id": doc_id}}), json.dumps(src)]
    out = _req(url, "POST", "/_bulk?refresh=true", ndjson="\n".join(lines) + "\n")
    assert not out.get("errors"), str(out)[:300]


def _seed(url: str) -> None:
    for month in (9, 10):
        index = f"logs-2026.{month:02d}"
        _req(url, "PUT", f"/{index}", {
            "settings": {"number_of_shards": 2, "number_of_replicas": 0},
            "mappings": {"properties": {"@timestamp": {"type": "date"},
                                        "message": {"type": "text"}}}})
        _bulk(url, index, [
            (f"evt-{i:04d}", {"@timestamp": (_T0 + dt.timedelta(days=30 * (month - 9),
                                                                minutes=i)).isoformat(),
                              "message": f"month {month} event {i}"})
            for i in range(115)])
    _req(url, "PUT", "/kb", {"mappings": {"properties": {
        "updated_at": {"type": "date"}, "body": {"type": "text"}}}})
    _bulk(url, "kb", [(f"kb-{i}", {"updated_at": (_T0 + dt.timedelta(hours=i)).isoformat(),
                                   "body": f"article {i}"}) for i in range(5)]
          + [("kb-draft", {"body": "a draft with no updated_at yet"})])
    _req(url, "PUT", "/secret")
    _req(url, "PUT", "/_security/role/kb_reader", {
        "cluster": ["monitor"],
        "indices": [{"names": ["logs-*", "kb", "inc-*", "live-*", "nope*"],
                     "privileges": ["read", "view_index_metadata"]}]})
    _req(url, "PUT", "/_security/user/reader", {"password": "reader-pw-2026",
                                                "roles": ["kb_reader"]})


@pytest.fixture(autouse=True)
def _allow_localhost(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost,127.0.0.1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _config(url: str, **cc: Any) -> SourceConfig:
    connection = {"url": url, "username": "reader", "password": "reader-pw-2026", **cc}
    return SourceConfig(source_id="src-es-1", tenant_id="t-es", name="es",
                        family=SourceFamily.NOSQL_DATABASE, source_type="elasticsearch",
                        connection_config=connection, collection_id="kb-1")


async def _drain(config: SourceConfig, cursor: str | None = None) -> tuple[list[Any], str | None]:
    docs: list[Any] = []
    last = cursor
    async for doc, new_cursor in ElasticsearchConnector().get_delta(config, cursor):
        docs.append(doc)
        last = new_cursor
    return docs, last


async def test_index_pattern_pages_every_document_of_every_index(es: str) -> None:
    docs, cursor = await _drain(_config(es, index="logs-*", batch_size=40))
    assert len(docs) == 230
    assert len({d.doc_id for d in docs}) == 230  # evt-0001 of September != of October
    assert {d.metadata["index"] for d in docs} == {"logs-2026.09", "logs-2026.10"}
    sample = next(d for d in docs if d.metadata["_id"] == "evt-0007")
    assert sample.source_url.startswith(f"{es}/logs-2026.")
    assert "/_doc/evt-0007" in sample.source_url
    # Nothing new: the next sync reads nothing it has not read.
    again, _ = await _drain(_config(es, index="logs-*", batch_size=40), cursor)
    assert len(again) <= 2  # at most the boundary documents (dedup skips them)


async def test_incremental_sync_reads_new_and_updated_documents(es: str) -> None:
    _bulk(es, "inc-a", [(f"e-{i}", {"@timestamp": (_T0 + dt.timedelta(minutes=i)).isoformat(),
                                    "message": f"event {i}"}) for i in range(60)])
    cfg = _config(es, index="inc-*", batch_size=25, cursor_lookback_seconds=0)
    first, cursor = await _drain(cfg)
    assert len(first) == 60
    later = (_T0 + dt.timedelta(days=90)).isoformat()
    _bulk(es, "inc-a", [("new-1", {"@timestamp": later, "message": "brand new"}),
                        ("e-3", {"@timestamp": later, "message": "edited"})])
    docs, _ = await _drain(cfg, cursor)
    got = {d.metadata["_id"] for d in docs}
    assert {"new-1", "e-3"} <= got
    assert len(docs) <= 3  # + at most the boundary document of the previous run


async def test_a_document_without_the_sort_field_does_not_poison_the_cursor(es: str) -> None:
    cfg = _config(es, index="kb", sort_field="updated_at", cursor_lookback_seconds=0)
    docs, cursor = await _drain(cfg)
    assert {d.metadata["_id"] for d in docs} == {"kb-0", "kb-1", "kb-2", "kb-3", "kb-4",
                                                 "kb-draft"}
    _bulk(es, "kb", [("kb-9", {"updated_at": (_T0 + dt.timedelta(days=5)).isoformat(),
                               "body": "article 9"})])
    docs2, _ = await _drain(cfg, cursor)
    assert "kb-9" in {d.metadata["_id"] for d in docs2}


async def test_unmapped_default_sort_field_missing_index_and_bad_credentials_fail_honestly(
    es: str,
) -> None:
    with pytest.raises(ConnectorFetchError, match="@timestamp"):
        await _drain(_config(es, index="kb"))
    with pytest.raises(ConnectorFetchError, match=r"(?i)index_not_found|no such index"):
        await _drain(_config(es, index="nope-*,nope2"))
    with pytest.raises(ConnectorFetchError, match=r"(?i)401|authenticat"):
        await _drain(_config(es, index="kb", password="wrong"))
    with pytest.raises(ConnectorFetchError, match=r"(?i)403|unauthorized|privilege"):
        await _drain(_config(es, index="secret", sort_field="_doc"))
    health = await ElasticsearchConnector().validate_connection(_config(es, index="nope"))
    assert health.ok is False and "nope" in (health.error or "")
    health = await ElasticsearchConnector().validate_connection(_config(es, index="logs-*"))
    assert health.ok is True, health.error
    assert health.metadata.get("documents") == 230


async def test_api_key_authentication(es: str) -> None:
    key = _req(es, "POST", "/_security/api_key", {
        "name": "reader-key", "role_descriptors": {"r": {"indices": [
            {"names": ["kb"], "privileges": ["read", "view_index_metadata"]}]}}})
    cfg = _config(es, index="kb", sort_field="updated_at", api_key=key["encoded"])
    cfg.connection_config.pop("username")
    cfg.connection_config.pop("password")
    docs, _ = await _drain(cfg)
    assert len(docs) >= 6
    bogus = _config(es, index="kb", api_key="Ym9ndXM6a2V5")
    bogus.connection_config.pop("username")
    with pytest.raises(ConnectorFetchError, match=r"(?i)401|authenticat"):
        await _drain(bogus)


async def test_live_listing_names_every_document_for_reconciliation(es: str) -> None:
    for name in ("live-a", "live-b"):
        _bulk(es, name, [(f"d-{i}", {"@timestamp": _T0.isoformat(), "message": f"doc {i}"})
                         for i in range(70)])
    connector = ElasticsearchConnector()
    cfg = _config(es, index="live-*", batch_size=60)
    docs, _ = await _drain(cfg)
    assert len(docs) == 140
    live = [d async for d in connector.iter_live_doc_ids(cfg)]
    assert set(live) == {d.doc_id for d in docs}
    assert len(live) == len(set(live))
    _req(es, "DELETE", "/live-a/_doc/d-50?refresh=true")
    live2 = {d async for d in connector.iter_live_doc_ids(cfg)}
    gone = {d.doc_id for d in docs if d.metadata["index"] == "live-a"
            and d.metadata["_id"] == "d-50"}
    assert len(gone) == 1 and not (gone & live2)
    assert len(live2) == 139
