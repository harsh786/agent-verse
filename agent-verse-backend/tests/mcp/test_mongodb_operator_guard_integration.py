"""MDB-03 / TG-03 against a real MongoDB: refused operators write nothing and run nothing.

The probe of the audit wrote ``shop.pwned_out`` with ``$out`` and
``otherdb.pwned_merge`` with a cross-database ``$merge``, and ran ``$where`` /
``$function`` / ``$accumulator`` JavaScript on the server. With the database
profiler on (level 2: every operation is recorded) the refused calls must leave
no trace: no new collection, no profiled command at all.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from app.mcp.servers import mongodb_server

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_JS = {"body": "function(){return true}", "args": [], "lang": "js"}
_ACC = {
    "init": "function(){return 0}",
    "accumulate": "function(s){return s+1}",
    "accumulateArgs": [],
    "merge": "function(a,b){return a+b}",
    "lang": "js",
}

ATTACKS: list[tuple[str, dict[str, Any]]] = [
    ("mongodb_aggregate", {"pipeline": [{"$limit": 2}, {"$out": "pwned_out"}]}),
    (
        "mongodb_aggregate",
        {"pipeline": [{"$limit": 2}, {"$merge": {"into": {"db": "otherdb", "coll": "pwned"}}}]},
    ),
    ("mongodb_find", {"query": {"$where": "sleep(5000) || true"}, "limit": 2}),
    ("mongodb_count", {"query": {"$expr": {"$function": _JS}}}),
    (
        "mongodb_aggregate",
        {"pipeline": [{"$limit": 1}, {"$group": {"_id": None, "a": {"$accumulator": _ACC}}}]},
    ),
    ("mongodb_update_one", {"filter": {"$where": "true"}, "update": {"hacked": True}}),
    ("mongodb_delete_one", {"filter": {"$where": "true"}}),
]


@pytest.fixture(scope="module")
def mongo() -> Iterator[dict[str, Any]]:
    if not os.environ.get("DOCKER_HOST"):
        pytest.skip("Docker not configured (DOCKER_HOST unset)")
    from pymongo import MongoClient
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    container = (
        DockerContainer("mongo:7.0")
        .with_exposed_ports(27017)
        .waiting_for(LogMessageWaitStrategy("Waiting for connections").with_startup_timeout(90))
    )
    with container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(27017))
        admin: Any = MongoClient(host, port, directConnection=True, serverSelectionTimeoutMS=30000)
        admin.shop.items.insert_many([{"n": i} for i in range(10)])
        try:
            yield {"host": host, "port": port, "admin": admin}
        finally:
            admin.close()


@pytest.fixture
def creds(mongo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.ingestion.connector_egress as egress

    monkeypatch.setattr(egress, "_effective_allowlist", lambda: [mongo["host"]])
    return {"url": f"mongodb://{mongo['host']}:{mongo['port']}/shop"}


@pytest.mark.parametrize(("tool", "args"), ATTACKS)
async def test_refused_operator_writes_and_runs_nothing(
    tool: str, args: dict[str, Any], mongo: dict[str, Any], creds: dict[str, Any]
) -> None:
    admin = mongo["admin"]
    admin.shop.command("profile", 0)
    admin.shop.system.profile.drop()
    admin.shop.command("profile", 2)
    try:
        result = await mongodb_server.call_tool(
            tool, {"collection": "items", **args}, credentials=creds
        )
    finally:
        admin.shop.command("profile", 0)

    assert result.get("status") == "operator_refused", result
    assert "pwned_out" not in admin.shop.list_collection_names()
    assert "pwned" not in admin.otherdb.list_collection_names()
    assert admin.shop.items.count_documents({"hacked": True}) == 0
    assert admin.shop.items.count_documents({}) == 10
    profiled = [p for p in admin.shop.system.profile.find({}) if p.get("ns") == "shop.items"]
    assert profiled == [], profiled


async def test_ordinary_calls_still_work(creds: dict[str, Any]) -> None:
    found = await mongodb_server.call_tool(
        "mongodb_find", {"collection": "items", "query": {"n": {"$lt": 3}}}, credentials=creds
    )
    agg = await mongodb_server.call_tool(
        "mongodb_aggregate",
        {"collection": "items", "pipeline": [{"$group": {"_id": None, "s": {"$sum": "$n"}}}]},
        credentials=creds,
    )

    assert found.get("count") == 3, found
    assert agg["results"][0]["s"] == 45, agg
