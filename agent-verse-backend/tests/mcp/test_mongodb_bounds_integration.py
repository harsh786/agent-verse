"""MDB-08 / C7 / TG-10 against a real MongoDB: limits, aggregate bound, maxTimeMS.

The audit probe got 5000 documents from ``mongodb_find`` with ``limit: 0`` and
an aggregate that materialised all 5000 before trimming to 1000; no command
carried ``maxTimeMS``. A command listener now proves what the server receives:
the clamped limit, a final ``$limit`` and ``allowDiskUse: false`` on aggregate,
and ``maxTimeMS`` on EVERY command the tools send.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from app.core.config import get_settings
from app.mcp.servers import mongodb_server

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# count_documents runs as an aggregate.
_DATA_COMMANDS = {"find", "aggregate", "insert", "update", "delete", "listCollections"}


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
        admin.shop.items.insert_many([{"n": i, "pad": "x" * 100} for i in range(5000)])
        try:
            yield {"host": host, "port": port}
        finally:
            admin.close()


class _Commands:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []

    def started_event(self, event: Any) -> None:
        self.started.append({"name": event.command_name, "cmd": dict(event.command)})


@pytest.fixture
def commands(
    mongo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[_Commands, dict[str, Any]]]:
    import pymongo
    from pymongo import monitoring

    import app.ingestion.connector_egress as egress

    seen = _Commands()

    class _Listener(monitoring.CommandListener):
        def started(self, event: Any) -> None:
            seen.started_event(event)

        def succeeded(self, event: Any) -> None:
            return None

        def failed(self, event: Any) -> None:
            return None

    real = pymongo.MongoClient

    def _client(*args: Any, **kwargs: Any) -> Any:
        kwargs["event_listeners"] = [*kwargs.get("event_listeners", []), _Listener()]
        return real(*args, **kwargs)

    monkeypatch.setattr(pymongo, "MongoClient", _client)
    monkeypatch.setattr(egress, "_effective_allowlist", lambda: [mongo["host"]])
    monkeypatch.setenv("MONGODB_TOOL_DEFAULT_LIMIT", "100")
    monkeypatch.setenv("MONGODB_TOOL_MAX_DOCUMENTS", "200")
    monkeypatch.setenv("MONGODB_TOOL_TIMEOUT_MS", "8000")
    get_settings.cache_clear()
    yield seen, {"url": f"mongodb://{mongo['host']}:{mongo['port']}/shop"}
    get_settings.cache_clear()


@pytest.mark.parametrize(("limit", "expected"), [(None, 100), (0, 200), (-3, 200), (10**6, 200)])
async def test_find_returns_at_most_the_maximum(
    limit: int | None, expected: int, commands: tuple[_Commands, dict[str, Any]]
) -> None:
    seen, creds = commands
    args: dict[str, Any] = {"collection": "items"}
    if limit is not None:
        args["limit"] = limit
    result = await mongodb_server.call_tool("mongodb_find", args, credentials=creds)

    assert result["count"] == expected, result.get("error")
    finds = [c["cmd"] for c in seen.started if c["name"] == "find"]
    assert finds and finds[0]["limit"] == expected


async def test_aggregate_is_bounded_on_the_server(
    commands: tuple[_Commands, dict[str, Any]],
) -> None:
    seen, creds = commands
    result = await mongodb_server.call_tool(
        "mongodb_aggregate",
        {"collection": "items", "pipeline": [{"$match": {"n": {"$gte": 0}}}]},
        credentials=creds,
    )

    assert result["count"] == 200, result.get("error")
    assert result["truncated"] is True
    (agg,) = [c["cmd"] for c in seen.started if c["name"] == "aggregate"]
    assert agg["pipeline"][-1] == {"$limit": 201}  # max + 1: tells "truncated"
    assert agg["allowDiskUse"] is False
    # Streamed in bounded batches: the server never builds one giant reply.
    assert 0 < agg["cursor"]["batchSize"] <= 200


async def test_every_command_carries_max_time_ms(
    commands: tuple[_Commands, dict[str, Any]],
) -> None:
    seen, creds = commands
    calls: list[tuple[str, dict[str, Any]]] = [
        ("mongodb_find", {"collection": "items", "limit": 5}),
        ("mongodb_find_one", {"collection": "items"}),
        ("mongodb_count", {"collection": "items"}),
        ("mongodb_aggregate", {"collection": "items", "pipeline": [{"$limit": 3}]}),
        ("mongodb_list_collections", {}),
        ("mongodb_insert_one", {"collection": "scratch", "document": {"a": 1}}),
        ("mongodb_update_one", {"collection": "scratch", "filter": {"a": 1}, "update": {"b": 2}}),
        ("mongodb_delete_one", {"collection": "scratch", "filter": {"a": 1}}),
    ]
    for tool, args in calls:
        result = await mongodb_server.call_tool(tool, args, credentials=creds)
        assert "error" not in result, (tool, result)
    data = [c for c in seen.started if c["name"] in _DATA_COMMANDS]
    assert {c["name"] for c in data} == _DATA_COMMANDS
    for c in data:
        assert 0 < c["cmd"].get("maxTimeMS", 0) <= 8000, c
