"""MONGO-FAIL-DUPLICATE-SOURCE: the same target into the same collection is a 409.

Live finding (tests/real_world/test_mongo_pipeline_failures.py::
test_duplicate_source_registration): registering the SAME MongoDB collection a
second time into the SAME knowledge collection was accepted (201); its sync
indexed every document again and search returned the same postmortem twice.

``POST /sources`` and ``PATCH /sources/{id}`` now refuse a Source whose
canonical target (connector kind + normalised endpoint + database + collection
set + patterns, credentials stripped) equals an existing Source's in the same
tenant and target collection, answering 409 with the existing Source's id. A
different collection list, a different prefix or a different target collection
stays allowed.

Runs against both the legacy in-process dict and the ``SourceConfigStore``
(in-memory mode) the API uses when one is wired. IP literals only: no DNS.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.ingestion as ingestion_mod
from app.api.ingestion import router
from app.ingestion.source_store import SourceConfigStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-dup-src", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
_OTHER = TenantContext(tenant_id="tid-dup-other", plan=PlanTier.PROFESSIONAL, api_key_id="k2")
_KEYS = {"av_test_dup_a": _CTX, "av_test_dup_b": _OTHER}
_AUTH = {"X-API-Key": "av_test_dup_a"}
_AUTH_OTHER = {"X-API-Key": "av_test_dup_b"}
_HOST = "93.184.216.34"  # public IPv4 literal


@pytest.fixture(params=["legacy_dict", "source_store"])
def client(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    ingestion_mod._SOURCES.clear()
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    if request.param == "source_store":
        app.state.ingestion_source_store = SourceConfigStore()
    yield TestClient(app, raise_server_exceptions=False)
    ingestion_mod._SOURCES.clear()


def _mongo(collections: list[str], **over: Any) -> dict[str, Any]:
    return {
        "uri": f"mongodb://{_HOST}:27017/",
        "username": "rwreader",
        "password": "reader-password",
        "database": "rw_source",
        "collections": collections,
        "batch_size": 500,
        **over,
    }


def _body(cfg: dict[str, Any], collection_id: str = "kb-1", **over: Any) -> dict[str, Any]:
    return {
        "name": "rw-mongodb",
        "family": "nosql_database",
        "source_type": "mongodb",
        "connection_config": cfg,
        "collection_id": collection_id,
        "sync_mode": "incremental",
        "sync_interval_seconds": 86400,
        "pii_action": "none",
        "min_quality_score": 0.0,
        **over,
    }


def _create(client: TestClient, body: dict[str, Any], auth: dict[str, str] = _AUTH) -> Any:
    return client.post("/sources", json=body, headers=auth)


def test_same_mongodb_collection_twice_into_one_kb_is_409_naming_the_first(
    client: TestClient,
) -> None:
    first = _create(client, _body(_mongo(["postmortems"])))
    assert first.status_code == 201, first.text
    first_id = first.json()["source_id"]

    second = _create(client, _body(_mongo(["postmortems"]), name="rw-mongodb-dup"))
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["existing_source_id"] == first_id
    assert detail["code"] == "duplicate_source"
    assert first_id in detail["message"]
    assert "reader-password" not in second.text
    # Nothing was stored for the refused registration.
    listed = client.get("/sources", headers=_AUTH).json()
    assert [s["source_id"] for s in listed] == [first_id]


def test_spelling_differences_and_credentials_do_not_hide_the_duplicate(
    client: TestClient,
) -> None:
    assert _create(client, _body(_mongo(["postmortems", "incidents"]))).status_code == 201
    respelled = _mongo(
        ["incidents", "postmortems"],
        uri=f"MONGODB://other:pw@{_HOST}/?replicaSet=rs0",
        password="a-different-password",
        batch_size=10,
    )
    assert _create(client, _body(respelled)).status_code == 409


@pytest.mark.parametrize(
    ("cfg", "collection_id"),
    [
        (_mongo(["runbooks"]), "kb-1"),  # a different collection
        (_mongo(["postmortems", "runbooks"]), "kb-1"),  # a different collection set
        (_mongo(["postmortems"], database="other_db"), "kb-1"),  # a different database
        (_mongo(["postmortems"]), "kb-2"),  # a different target KB collection
    ],
)
def test_a_different_target_or_kb_collection_is_allowed(
    client: TestClient, cfg: dict[str, Any], collection_id: str
) -> None:
    assert _create(client, _body(_mongo(["postmortems"]))).status_code == 201
    resp = _create(client, _body(cfg, collection_id=collection_id))
    assert resp.status_code == 201, resp.text


def test_another_tenant_may_register_the_same_target(client: TestClient) -> None:
    assert _create(client, _body(_mongo(["postmortems"]))).status_code == 201
    assert _create(client, _body(_mongo(["postmortems"])), _AUTH_OTHER).status_code == 201


def test_s3_prefix_distinguishes_targets(client: TestClient) -> None:
    def s3(prefix: str) -> dict[str, Any]:
        return {
            "name": "s3",
            "family": "object_storage",
            "source_type": "s3",
            "connection_config": {"bucket": "reports", "prefix": prefix, "region": "us-east-1"},
            "collection_id": "kb-1",
        }

    assert _create(client, s3("q1/")).status_code == 201
    assert _create(client, s3("q2/")).status_code == 201
    dup = _create(client, s3("/q1/"))
    assert dup.status_code == 409, dup.text


def test_update_into_a_duplicate_is_refused_and_leaves_the_source_unchanged(
    client: TestClient,
) -> None:
    first = _create(client, _body(_mongo(["postmortems"]))).json()["source_id"]
    second = _create(client, _body(_mongo(["runbooks"]))).json()["source_id"]

    resp = client.patch(
        f"/sources/{second}",
        json={"connection_config": _mongo(["postmortems"])},
        headers=_AUTH,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["existing_source_id"] == first
    stored = client.get(f"/sources/{second}", headers=_AUTH).json()
    assert stored["connection_config"]["collections"] == ["runbooks"]


def test_moving_a_source_into_a_kb_that_already_has_its_target_is_refused(
    client: TestClient,
) -> None:
    first = _create(client, _body(_mongo(["postmortems"]), collection_id="kb-1"))
    second = _create(client, _body(_mongo(["postmortems"]), collection_id="kb-2"))
    assert first.status_code == second.status_code == 201
    resp = client.patch(
        f"/sources/{second.json()['source_id']}",
        json={"collection_id": "kb-1"},
        headers=_AUTH,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["existing_source_id"] == first.json()["source_id"]


def test_updating_a_source_without_changing_its_target_is_allowed(client: TestClient) -> None:
    sid = _create(client, _body(_mongo(["postmortems"]))).json()["source_id"]
    # Re-saving its own target (masked password echoed back) is not a duplicate.
    shown = client.get(f"/sources/{sid}", headers=_AUTH).json()["connection_config"]
    resp = client.patch(
        f"/sources/{sid}",
        json={"connection_config": {**shown, "batch_size": 100}, "name": "renamed"},
        headers=_AUTH,
    )
    assert resp.status_code == 200, resp.text


def test_a_deleted_source_no_longer_blocks_its_target(client: TestClient) -> None:
    sid = _create(client, _body(_mongo(["postmortems"]))).json()["source_id"]
    assert client.delete(f"/sources/{sid}", headers=_AUTH).status_code == 204
    assert _create(client, _body(_mongo(["postmortems"]))).status_code == 201
