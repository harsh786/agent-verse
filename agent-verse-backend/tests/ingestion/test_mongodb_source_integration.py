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
                # A Source with no target knowledge collection is parked as
                # "needs configuration" and refuses to sync (L-02).
                "collection_id": "kb-orders",
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
    # The polled health check is a ping (C8); the full check (Test connection)
    # still lists the collections and verifies the configured ones exist.
    assert "collections" not in health["metadata"]
    from app.ingestion.connectors.mongodb_connector import MongoDBConnector

    stored = await h.store.get(source["source_id"], source["tenant_id"])
    assert stored is not None
    full = await MongoDBConnector().validate_connection(stored)
    assert full.ok is True, full.error
    assert set(full.metadata["collections"]) >= {"orders", "customers"}

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


async def test_a_uri_change_re_ids_nothing(mongo: tuple[str, int]) -> None:
    """TG-13: ids came from the URI host, so pointing the Source at the same
    server by another name re-ided (duplicated) every document."""
    host, port = mongo
    h = _Harness()
    source = h.create(_ui_payload(host, port, collections_csv="customers"))
    _first, pipeline1 = await h.sync(source["source_id"])
    first = {d.doc_id for d in pipeline1.docs}
    assert len(first) == 3

    other_host = "127.0.0.1" if host != "127.0.0.1" else "localhost"
    sid, tid = source["source_id"], source["tenant_id"]
    stored = await h.store.get(sid, tid)
    assert stored is not None
    await h.store.update(
        sid,
        tid,
        connection_config={**stored.connection_config, "uri": f"mongodb://{other_host}:{port}/shop"},
        cursor_value="",  # a full re-read, as after a reindex
    )
    _second, pipeline2 = await h.sync(source["source_id"])

    assert {d.doc_id for d in pipeline2.docs} == first  # 0 new documents
    assert all(d.source_url.startswith(f"mongodb://{other_host}:{port}/") for d in pipeline2.docs)


# ── TG-07: updated and deleted documents ─────────────────────────────────────


async def test_updated_documents_are_re_read_from_the_change_stream(
    mongo_replset: tuple[str, int],
) -> None:
    """With the default _id cursor an update was never re-read. On a replica set
    the change stream (update / replace) brings it back under the same id."""
    host, port = mongo_replset
    client: MongoClient[dict[str, Any]] = MongoClient(host, port, directConnection=True)
    try:
        items = client["shop"]["items_tg07"]
        ids = items.insert_many([{"sku": i, "price": 10 + i} for i in range(5)]).inserted_ids
        h = _Harness()
        source = h.create(
            {
                "uri": f"mongodb://{host}:{port}/?directConnection=true",
                "database": "shop",
                "collection": "items_tg07",
            }
        )
        first, p1 = await h.sync(source["source_id"])
        assert first["docs_indexed"] == 5, first
        by_mongo_id = {d.metadata["_id"]: d.doc_id for d in p1.docs}

        items.update_one({"_id": ids[1]}, {"$set": {"price": 999}})
        items.replace_one({"_id": ids[3]}, {"sku": 3, "price": 333, "note": "replaced"})
        second, p2 = await h.sync(source["source_id"])

        assert second["docs_indexed"] == 2, second
        assert {d.metadata["_id"]: d.doc_id for d in p2.docs} == {
            str(ids[1]): by_mongo_id[str(ids[1])],
            str(ids[3]): by_mongo_id[str(ids[3])],
        }
        texts = {d.metadata["_id"]: d.content for d in p2.docs}
        assert b"price: 999" in texts[str(ids[1])]
        assert b"note: replaced" in texts[str(ids[3])]

        third, p3 = await h.sync(source["source_id"])
        assert third["docs_indexed"] == 0 and p3.docs == [], third
    finally:
        client.close()


async def test_updates_are_re_read_with_an_update_timestamp_cursor(
    mongo: tuple[str, int],
) -> None:
    """A standalone server has no change stream: a cursor_field on an update
    timestamp re-reads an edited document, under the same id."""
    host, port = mongo
    h = _Harness()
    source = h.create(
        _ui_payload(host, port, collections_csv="customers", cursor_field="updated_at")
    )
    _r1, p1 = await h.sync(source["source_id"])
    ada = next(d for d in p1.docs if b"name: Ada" in d.content)
    client: MongoClient[dict[str, Any]] = MongoClient(
        host, port, username=_USER, password=_PASSWORD
    )
    try:
        client["shop"]["customers"].update_one(
            {"name": "Ada"},
            {
                "$set": {
                    "address.city": "Cambridge",
                    "updated_at": datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC),
                }
            },
        )
    finally:
        client.close()
    r2, p2 = await h.sync(source["source_id"])
    assert r2["docs_indexed"] == 1, r2
    assert p2.docs[0].doc_id == ada.doc_id
    assert b"address.city: Cambridge" in p2.docs[0].content


class _IndexedStore:
    """The knowledge-store surface upstream-deletion reconciliation uses."""

    def __init__(self, doc_ids: set[str]) -> None:
        self.docs = set(doc_ids)
        self.staged: set[str] = set()
        self.deleted: list[str] = []

    async def begin_live_listing_async(self, run_id: str, *, tenant_ctx: Any) -> Any:
        return datetime.datetime.now(datetime.UTC)

    async def stage_live_doc_ids_async(self, run_id: str, ids: list[str], **_k: Any) -> None:
        self.staged.update(ids)

    async def list_unlisted_source_documents_async(
        self, run_id: str, *, after: str | None, limit: int, **_k: Any
    ) -> list[str]:
        gone = sorted(d for d in self.docs if d not in self.staged and (after is None or d > after))
        return gone[:limit]

    async def held_document_ids_async(self, collection_id: str, ids: list[str], **_k: Any) -> set[str]:
        return set()

    async def delete_document_async(self, doc_id: str, **_k: Any) -> bool:
        self.docs.discard(doc_id)
        self.deleted.append(doc_id)
        return True

    async def clear_live_listing_async(self, run_id: str, **_k: Any) -> None:
        self.staged.clear()


async def test_deleted_documents_are_removed_by_reconciliation(mongo: tuple[str, int]) -> None:
    from app.ingestion.base_connector import lists_upstream
    from app.ingestion.connectors.mongodb_connector import MongoDBConnector
    from app.ingestion.scheduler import _reconcile_upstream_deletions

    host, port = mongo
    client: MongoClient[dict[str, Any]] = MongoClient(
        host, port, username=_USER, password=_PASSWORD
    )
    try:
        gone = client["shop"]["gone_tg07"]
        ids = gone.insert_many([{"n": i} for i in range(4)]).inserted_ids
        h = _Harness()
        source = h.create(_ui_payload(host, port, collections_csv="gone_tg07"))
        _r1, p1 = await h.sync(source["source_id"])
        by_mongo_id = {d.metadata["_id"]: d.doc_id for d in p1.docs}
        legacy = "6b1c1c2e-0000-5000-8000-000000000001"  # an older (uuid5) id: never touched
        kb = _IndexedStore(set(by_mongo_id.values()) | {legacy})
        gone.delete_one({"_id": ids[2]})

        config = await h.store.get(source["source_id"], source["tenant_id"])
        assert config is not None
        connector = MongoDBConnector()
        assert lists_upstream(connector)
        counts = await _reconcile_upstream_deletions(
            connector, config, type("P", (), {"_kb": kb})()
        )
    finally:
        client.close()

    assert kb.deleted == [by_mongo_id[str(ids[2])]], counts
    assert counts["deleted"] == 1 and counts["listing_failed"] == 0
    assert legacy in kb.docs
