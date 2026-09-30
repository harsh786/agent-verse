"""SRC-MONGO-SYNC: a MongoDB Source syncs end to end against a real MongoDB.

Drives the real Sources API (create -> health -> POST /sync) and then runs the
worker body the queued Celery task executes (``_sync_source_async``) against a
MongoDB testcontainer, exactly the path a user's "Sync now" takes. The Source is
created with the payload the Sources UI sends.

The containers are only reachable on localhost, so ``localhost`` goes on the
operator allowlist — the documented on-prem mechanism; tenant config can never
widen it.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pymongo import MongoClient
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy
from testcontainers.mongodb import MongoDbContainer

import app.api.ingestion as ingestion_mod
from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.source_config import PipelineResult
from app.ingestion.source_store import SourceConfigStore
from tests.api.test_ingestion_api import _auth, _client
from tests.ingestion.tls_certs import make_pki

pytestmark = pytest.mark.integration

_IMAGE = "mongo:7.0"
_USER = "root"
# Every character that breaks a hand-built mongodb:// URI.
_PASSWORD = "p@ss:w/rd%1?#"
_ORDERS = 1203  # > 2 pages of the default batch size (500)


@pytest.fixture(scope="module")
def mongo() -> Iterator[tuple[str, int]]:
    container = MongoDbContainer(_IMAGE, username=_USER, password=_PASSWORD, dbname="shop")
    with container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(27017))
        client: MongoClient[dict[str, Any]] = MongoClient(
            host, port, username=_USER, password=_PASSWORD, serverSelectionTimeoutMS=30000
        )
        try:
            db = client["shop"]
            db["orders"].insert_many(
                [{"order_no": i, "total": i * 1.5, "status": "paid"} for i in range(_ORDERS)]
            )
            db["customers"].insert_many(
                [
                    {
                        "name": "Ada",
                        "address": {"city": "London"},
                        "updated_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
                    },
                    {
                        "name": "Grace",
                        "address": {"city": "Arlington"},
                        "updated_at": datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC),
                    },
                    {
                        "name": "Linus",
                        "address": {"city": "Portland"},
                        "updated_at": datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC),
                    },
                ]
            )
        finally:
            client.close()
        yield host, port


@pytest.fixture(autouse=True)
def _allow_localhost(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost,127.0.0.1")
    get_settings.cache_clear()
    ingestion_mod._SOURCES.clear()
    yield
    ingestion_mod._SOURCES.clear()
    get_settings.cache_clear()


class _RecordingPipeline:
    def __init__(self) -> None:
        self.docs: list[Any] = []

    async def ingest(self, raw_doc: Any, config: Any) -> PipelineResult:
        self.docs.append(raw_doc)
        return PipelineResult(
            doc_id=raw_doc.doc_id,
            source_id=config.source_id,
            tenant_id=config.tenant_id,
            status="indexed",
        )


class _Harness:
    """The Sources API plus the worker body a queued sync runs."""

    def __init__(self) -> None:
        self.store = SourceConfigStore()
        self.tracker = IngestionJobTracker()
        self.client = _client(
            ingestion_source_store=self.store,
            ingestion_job_tracker=self.tracker,
            ingestion_pipeline=MagicMock(),
        )

    def create(self, connection_config: dict[str, Any], **extra: Any) -> dict[str, Any]:
        resp = self.client.post(
            "/sources",
            headers=_auth(),
            json={
                "name": "orders db",
                "family": "nosql_database",
                "source_type": "mongodb",
                "connection_config": connection_config,
                **extra,
            },
        )
        assert resp.status_code == 201, resp.text
        body: dict[str, Any] = resp.json()
        return body

    def health(self, source_id: str) -> dict[str, Any]:
        resp = self.client.get(f"/sources/{source_id}/health", headers=_auth())
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    async def sync(self, source_id: str) -> tuple[dict[str, Any], _RecordingPipeline]:
        from app.ingestion.scheduler import _sync_source_async

        with patch("app.ingestion.scheduler.sync_source_task") as task:
            resp = self.client.post(f"/sources/{source_id}/sync", headers=_auth())
            assert resp.status_code == 202, resp.text
            assert resp.json()["status"] == "queued", resp.json()
            queued = task.apply_async.call_args.kwargs["kwargs"]

        pipeline = _RecordingPipeline()
        task = MagicMock()
        task.retry.side_effect = lambda exc, **_kw: exc  # surface the real error
        with patch(
            "app.ingestion.scheduler._build_worker_ingestion",
            return_value=(self.tracker, pipeline, self.store),
        ):
            result = await _sync_source_async(task=task, **queued)
        return result, pipeline


def _ui_payload(host: str, port: int, **overrides: Any) -> dict[str, Any]:
    """What the Sources UI (DatabaseForm, mongodb) submits."""
    payload: dict[str, Any] = {
        "uri": f"mongodb://{host}:{port}/shop",
        "username": _USER,
        "password": _PASSWORD,
        "database": "shop",
        "collections_csv": "orders, customers",
    }
    payload.update(overrides)
    return payload


async def test_create_validate_sync_ingests_every_document(mongo: tuple[str, int]) -> None:
    host, port = mongo
    h = _Harness()
    source = h.create(_ui_payload(host, port))
    assert source["connection_config"]["password"] == "********"

    health = h.health(source["source_id"])
    assert health["ok"] is True, health
    assert set(health["metadata"]["collections"]) >= {"orders", "customers"}

    result, pipeline = await h.sync(source["source_id"])

    assert result.get("docs_indexed") == _ORDERS + 3, result
    assert result["docs_failed"] == 0
    by_collection: dict[str, int] = {}
    for doc in pipeline.docs:
        by_collection[doc.metadata["collection"]] = by_collection.get(
            doc.metadata["collection"], 0
        ) + 1
    assert by_collection == {"orders": _ORDERS, "customers": 3}
    ada = next(d for d in pipeline.docs if b"name: Ada" in d.content)
    assert b"address.city: London" in ada.content
    assert _PASSWORD.encode() not in ada.content
    assert _PASSWORD not in ada.source_url
    assert ada.source_url.startswith(f"mongodb://{host}:{port}/shop/customers/")
    assert len({d.doc_id for d in pipeline.docs}) == _ORDERS + 3

    # Incremental: nothing new -> nothing re-ingested.
    again, pipeline2 = await h.sync(source["source_id"])
    assert again.get("docs_indexed") == 0, again
    assert pipeline2.docs == []

    # A new document is picked up by the next sync, and only it.
    client: MongoClient[dict[str, Any]] = MongoClient(
        host, port, username=_USER, password=_PASSWORD
    )
    try:
        client["shop"]["orders"].insert_one({"order_no": 99999, "status": "new"})
    finally:
        client.close()
    third, pipeline3 = await h.sync(source["source_id"])
    assert third.get("docs_indexed") == 1, third
    assert b"order_no: 99999" in pipeline3.docs[0].content


async def test_custom_cursor_field_with_ties_pages_without_skipping(
    mongo: tuple[str, int],
) -> None:
    host, port = mongo
    h = _Harness()
    source = h.create(
        _ui_payload(
            host, port, collections_csv="customers", cursor_field="updated_at", batch_size=1
        )
    )

    result, pipeline = await h.sync(source["source_id"])

    # batch_size=1 and two documents share updated_at: a plain $gt on the field
    # would skip one of them at the page boundary.
    assert result.get("docs_indexed") == 3, result
    assert sorted(d.metadata["_id"] for d in pipeline.docs) == sorted(
        {d.metadata["_id"] for d in pipeline.docs}
    )
    again, _ = await h.sync(source["source_id"])
    assert again.get("docs_indexed") == 0, again


async def test_wrong_password_is_an_honest_auth_error(mongo: tuple[str, int]) -> None:
    host, port = mongo
    h = _Harness()
    source = h.create(_ui_payload(host, port, password="wrong"))

    health = h.health(source["source_id"])

    assert health["ok"] is False
    assert "auth" in health["error"].lower()
    assert "wrong" not in health["error"]


async def test_credentials_inline_in_the_uri_work(mongo: tuple[str, int]) -> None:
    from urllib.parse import quote

    host, port = mongo
    h = _Harness()
    uri = f"mongodb://{_USER}:{quote(_PASSWORD, safe='')}@{host}:{port}/?authSource=admin"
    source = h.create({"uri": uri, "database": "shop", "collection": "customers"})

    assert h.health(source["source_id"])["ok"] is True
    result, _ = await h.sync(source["source_id"])
    assert result.get("docs_indexed") == 3, result


async def test_host_port_form_without_a_uri(mongo: tuple[str, int]) -> None:
    host, port = mongo
    h = _Harness()
    source = h.create(
        {
            "host": host,
            "port": port,
            "username": _USER,
            "password": _PASSWORD,
            "database": "shop",
            "collections": ["customers"],
        }
    )

    assert h.health(source["source_id"])["ok"] is True
    result, _ = await h.sync(source["source_id"])
    assert result.get("docs_indexed") == 3, result


async def test_platform_credential_auth_mechanisms_are_refused(mongo: tuple[str, int]) -> None:
    host, port = mongo
    h = _Harness()
    for uri in (
        f"mongodb://{host}:{port}/?authMechanism=MONGODB-AWS",
        f"mongodb://{host}:{port}/?authMechanism=MONGODB-OIDC"
        "&authMechanismProperties=ENVIRONMENT:azure,TOKEN_RESOURCE:x",
        f"mongodb://{host}:{port}/?tlsCertificateKeyFile=/etc/ssl/private/platform.pem",
        f"mongodb://{host}:{port}/?proxyHost=10.0.0.5&proxyPort=1080",
    ):
        source = h.create({"uri": uri, "database": "shop", "collection": "customers"})
        health = h.health(source["source_id"])
        assert health["ok"] is False, uri
        assert "not allowed" in health["error"], (uri, health["error"])


async def test_sync_failure_is_recorded_not_reported_as_empty_success(
    mongo: tuple[str, int],
) -> None:
    host, port = mongo
    h = _Harness()
    source = h.create(_ui_payload(host, port, password="wrong"))

    with pytest.raises(Exception, match=r"(?i)auth"):
        # The worker re-raises through Celery's retry (the harness's retry
        # returns the original error).
        await h.sync(source["source_id"])

    job = max(h.tracker.list_jobs_for_source(source["source_id"]), key=lambda j: j.created_at)
    assert job.status == "failed"
    assert "auth" in job.error_message.lower()


# ── Replica-set members are egress-checked too ────────────────────────────────


@pytest.fixture(scope="module")
def mongo_replset() -> Iterator[tuple[str, int]]:
    """A single-node replica set whose member host is the container's own name —
    the kind of internal hostname a hostile or misconfigured replica set advertises."""
    container = (
        DockerContainer(_IMAGE)
        .with_command("--replSet rs0 --bind_ip_all")
        .with_exposed_ports(27017)
        .with_kwargs(hostname="mongo-rs-internal")
        .waiting_for(LogMessageWaitStrategy("Waiting for connections").with_startup_timeout(90))
    )
    with container:
        code, out = container.exec(
            [
                "mongosh",
                "--quiet",
                "--eval",
                "rs.initiate({_id:'rs0',members:[{_id:0,host:'mongo-rs-internal:27017'}]});"
                "while(!db.hello().isWritablePrimary){sleep(200)};"
                "db.getSiblingDB('shop').notes.insertOne({text:'hello'})",
            ]
        )
        assert code == 0, out
        yield container.get_container_host_ip(), int(container.get_exposed_port(27017))


async def test_replica_set_member_hosts_are_egress_checked(
    mongo_replset: tuple[str, int],
) -> None:
    host, port = mongo_replset
    h = _Harness()
    source = h.create(
        {"uri": f"mongodb://{host}:{port}/", "database": "shop", "collection": "notes"}
    )

    health = h.health(source["source_id"])

    # The seed host is allowlisted, but the member it advertises is not: the
    # driver must not be allowed to dial it.
    assert health["ok"] is False
    assert "mongo-rs-internal" in health["error"]
    # Refused by the egress check before the driver dialled it — not a driver
    # timeout that merely happens to mention the name.
    assert "not allowed by the egress policy" in health["error"]


async def test_direct_connection_skips_member_discovery(mongo_replset: tuple[str, int]) -> None:
    host, port = mongo_replset
    h = _Harness()
    source = h.create(
        {
            "uri": f"mongodb://{host}:{port}/?directConnection=true",
            "database": "shop",
            "collection": "notes",
        }
    )

    assert h.health(source["source_id"])["ok"] is True
    result, pipeline = await h.sync(source["source_id"])
    assert result.get("docs_indexed") == 1, result
    assert b"text: hello" in pipeline.docs[0].content


# ── TLS with a custom CA and a client certificate ─────────────────────────────


@pytest.fixture(scope="module")
def mongo_tls() -> Iterator[tuple[str, int, Any]]:
    pki = make_pki()
    container = (
        DockerContainer(_IMAGE)
        .with_env("SERVER_PEM", pki.server_cert + pki.server_key)
        .with_env("CA_PEM", pki.ca_cert)
        .with_exposed_ports(27017)
        .with_kwargs(entrypoint=["bash", "-c"])
        .with_command(
            [
                'printf "%s" "$SERVER_PEM" > /tmp/server.pem && '
                'printf "%s" "$CA_PEM" > /tmp/ca.pem && '
                "exec mongod --bind_ip_all --tlsMode requireTLS "
                "--tlsCertificateKeyFile /tmp/server.pem --tlsCAFile /tmp/ca.pem"
            ]
        )
        .waiting_for(LogMessageWaitStrategy("Waiting for connections").with_startup_timeout(90))
    )
    with container:
        yield container.get_container_host_ip(), int(container.get_exposed_port(27017)), pki


async def test_tls_with_custom_ca_and_client_certificate(
    mongo_tls: tuple[str, int, Any],
) -> None:
    host, port, pki = mongo_tls
    h = _Harness()
    base = {"uri": f"mongodb://{host}:{port}/", "database": "admin"}

    # Without the CA the server certificate does not verify.
    untrusted = h.create({**base, "tls": True})
    health = h.health(untrusted["source_id"])
    assert health["ok"] is False
    assert "certificate" in health["error"].lower()

    trusted = h.create(
        {
            **base,
            "tls": True,
            "tls_ca_pem": pki.ca_cert,
            "tls_client_cert": pki.client_cert,
            "tls_client_private_key": pki.client_key,
        }
    )
    health = h.health(trusted["source_id"])
    assert health["ok"] is True, health
    assert trusted["connection_config"]["tls_client_private_key"] == "********"
