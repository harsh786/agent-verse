"""USR-2: creating or updating a Source refuses internal destinations (422).

``POST /sources`` and ``PATCH /sources/{id}`` stored any ``connection_config``;
internal addresses (cloud metadata, loopback, RFC 1918, CGNAT, IPv6 and
IPv4-mapped forms) were refused only by ``POST /sources/validate`` — which the
UI may never call — and later by the connector at sync time. Every URL-bearing
and host-bearing field now goes through the shared SSRF guard when the Source is
saved.

Only IP literals and names the guard refuses without DNS are used, so the test
needs no network.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.ingestion as ingestion_mod
from app.api.ingestion import router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-ssrf-src", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
_KEY = "av_test_source_ssrf"
_AUTH = {"X-API-Key": _KEY}
_PUBLIC = "93.184.216.34"  # a public IPv4 literal: no DNS lookup needed


@pytest.fixture(autouse=True)
def _clear_sources() -> Any:
    ingestion_mod._SOURCES.clear()
    yield
    ingestion_mod._SOURCES.clear()


def _client() -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _body(source_type: str, connection_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": f"src-{source_type}",
        "family": "web",
        "source_type": source_type,
        "connection_config": connection_config,
        "collection_id": "col-1",
    }


INTERNAL_CONFIGS: list[tuple[str, dict[str, Any]]] = [
    ("web_crawl", {"seed_urls": ["http://169.254.169.254/latest/meta-data/"]}),
    ("http", {"url": "http://metadata.google.internal/computeMetadata/v1/"}),
    ("pdf_file", {"urls": ["http://[::ffff:169.254.169.254]/creds.pdf"]}),
    ("docx_file", {"urls": ["https://example.org/a.docx", "http://[fd00:ec2::254]/"]}),
    ("jira", {"base_url": "http://127.0.0.1:8080", "api_token": "t"}),
    ("confluence", {"base_url": "http://10.0.0.8/wiki"}),
    ("rss", {"urls": ["http://100.64.0.1/feed.xml"]}),  # CGNAT
    ("elasticsearch", {"url": "http://[::1]:9200"}),
    ("gitlab", {"base_url": "http://[fe80::1]/"}),
    ("influxdb", {"url": "http://192.168.1.20:8086"}),
    ("web_crawl", {"sitemap_url": "http://172.16.5.5/sitemap.xml"}),
    ("salesforce", {"login_url": "http://169.254.169.254/"}),
    ("s3", {"endpoint_url": "http://169.254.169.254", "bucket": "b"}),
    ("postgresql", {"host": "192.168.0.10", "port": 5432, "database": "x"}),
    ("postgresql", {"dsn": "postgresql://u:p@127.0.0.1:5432/db"}),
    ("postgresql", {"host": "::ffff:10.0.0.1", "port": 5432}),
    ("mysql", {"host": "172.16.0.4", "port": 3306}),
    ("mysql", {"host": "localhost", "port": 3306}),  # a name resolving to loopback
    ("clickhouse", {"host": "0.0.0.0"}),
    ("mongodb", {"uri": "mongodb://169.254.169.254:27017/db"}),
    ("redis", {"url": "redis://10.1.1.1:6379/0"}),
    ("redis", {"mode": "cluster", "cluster_nodes": f"{_PUBLIC}:7000,10.0.0.1:7001"}),
    ("redis", {"mode": "sentinel", "sentinels": ["127.0.0.1:26379"], "sentinel_master": "m"}),
    ("kafka", {"bootstrap_servers": f"{_PUBLIC}:9092,127.0.0.1:9092"}),
    ("neo4j", {"uri": "bolt://10.0.0.2:7687"}),
    ("email_imap", {"host": "127.0.0.1"}),
    ("mqtt", {"host": "100.100.100.100"}),  # CGNAT
    (
        "azure_blob",
        {"connection_string": "BlobEndpoint=http://169.254.169.254/;SharedAccessSignature=x"},
    ),
    ("azure_blob", {"connection_string": "UseDevelopmentStorage=true"}),
    ("servicenow", {"instance": "http://192.168.1.1"}),
    ("zendesk", {"subdomain": "127.0.0.1:1/#"}),
]


@pytest.mark.parametrize(("source_type", "cc"), INTERNAL_CONFIGS)
def test_create_refuses_internal_destinations(source_type: str, cc: dict[str, Any]) -> None:
    client = _client()
    resp = client.post("/sources", json=_body(source_type, cc), headers=_AUTH)
    assert resp.status_code == 422, resp.text
    assert "SSRF" in resp.json()["detail"] or "blocked" in resp.json()["detail"]
    assert client.get("/sources", headers=_AUTH).json() == []


@pytest.mark.parametrize(("source_type", "cc"), INTERNAL_CONFIGS)
def test_update_refuses_internal_destinations(source_type: str, cc: dict[str, Any]) -> None:
    client = _client()
    created = client.post(
        "/sources",
        json=_body(source_type, {"base_url": f"https://{_PUBLIC}", "note": "public"}),
        headers=_AUTH,
    )
    assert created.status_code == 201, created.text
    sid = created.json()["source_id"]

    resp = client.patch(f"/sources/{sid}", json={"connection_config": cc}, headers=_AUTH)
    assert resp.status_code == 422, resp.text
    stored = client.get(f"/sources/{sid}", headers=_AUTH).json()["connection_config"]
    assert stored == {"base_url": f"https://{_PUBLIC}", "note": "public"}


def test_public_destinations_are_saved() -> None:
    client = _client()
    for source_type, cc in (
        ("jira", {"base_url": f"https://{_PUBLIC}", "api_token": "vault://jira/token"}),
        ("postgresql", {"host": _PUBLIC, "port": 5432, "database": "app"}),
        ("kafka", {"bootstrap_servers": f"{_PUBLIC}:9092"}),
        ("web_crawl", {"seed_urls": [f"https://{_PUBLIC}/docs"], "max_pages": 5}),
    ):
        resp = client.post("/sources", json=_body(source_type, cc), headers=_AUTH)
        assert resp.status_code == 201, (source_type, resp.text)


def test_update_without_connection_config_does_not_reresolve() -> None:
    """A PATCH that leaves connection_config alone keeps working (e.g. a rename)."""
    client = _client()
    created = client.post(
        "/sources", json=_body("jira", {"base_url": f"https://{_PUBLIC}"}), headers=_AUTH
    )
    sid = created.json()["source_id"]
    resp = client.patch(f"/sources/{sid}", json={"name": "renamed"}, headers=_AUTH)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "renamed"


# ── Multi-host fields (USR-2 follow-up) ─────────────────────────────────────────
# MongoDB documents ``host: "h1:27017,h2:27017"`` for replica-set seeds: every
# host is validated on its own — public seeds are saved, any internal one refused.

_PUBLIC_2 = "93.184.216.35"


@pytest.mark.parametrize(
    "host",
    [
        f"{_PUBLIC}:27017,{_PUBLIC_2}:27017",
        f"{_PUBLIC}:27017, {_PUBLIC_2}:27018",
        f"{_PUBLIC},{_PUBLIC_2}",
        f"[2606:2800:220:1:248:1893:25c8:1946]:27017,{_PUBLIC}:27017",
        "2606:2800:220:1:248:1893:25c8:1946",
    ],
)
def test_public_multi_host_seeds_are_accepted(host: str) -> None:
    client = _client()
    resp = client.post(
        "/sources",
        json=_body("mongodb", {"host": host, "database": "app", "collections": ["c"]}),
        headers=_AUTH,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["connection_config"]["host"] == host  # stored verbatim


@pytest.mark.parametrize(
    "host",
    [
        f"{_PUBLIC}:27017,10.0.0.5:27017",
        f"127.0.0.1:27017,{_PUBLIC}:27017",
        f"{_PUBLIC}:27017,[::1]:27017",
        f"{_PUBLIC}:27017,[::ffff:169.254.169.254]:27017",
        f"{_PUBLIC},169.254.169.254",
        f"{_PUBLIC}:27017,localhost:27017",
    ],
)
def test_an_internal_member_in_a_multi_host_field_is_refused(host: str) -> None:
    client = _client()
    resp = client.post(
        "/sources",
        json=_body("mongodb", {"host": host, "database": "app", "collections": ["c"]}),
        headers=_AUTH,
    )
    assert resp.status_code == 422, resp.text


def test_multi_host_update_is_validated_per_host() -> None:
    client = _client()
    created = client.post(
        "/sources", json=_body("mongodb", {"host": f"{_PUBLIC}:27017"}), headers=_AUTH
    )
    sid = created.json()["source_id"]
    ok = client.patch(
        f"/sources/{sid}",
        json={"connection_config": {"host": f"{_PUBLIC}:27017,{_PUBLIC_2}:27017"}},
        headers=_AUTH,
    )
    assert ok.status_code == 200, ok.text
    bad = client.patch(
        f"/sources/{sid}",
        json={"connection_config": {"host": f"{_PUBLIC}:27017,192.168.1.9:27017"}},
        headers=_AUTH,
    )
    assert bad.status_code == 422, bad.text


def test_uri_query_options_are_stored_untouched() -> None:
    """The check only reads the URI: options (order included) are saved as sent."""
    uri = (
        f"mongodb://u:p@{_PUBLIC}:27017,{_PUBLIC_2}:27017/app"
        "?replicaSet=rs0&tls=true&authSource=admin&readPreference=secondary"
    )
    client = _client()
    resp = client.post(
        "/sources",
        json=_body("mongodb", {"uri": uri, "collections": ["c"]}),
        headers=_AUTH,
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["source_id"]
    stored = ingestion_mod._SOURCES[sid].connection_config["uri"]
    assert stored == uri
