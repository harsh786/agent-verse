"""MDB-08 / C7 / TG-10 (unit part): every MongoDB tool call is bounded.

* ``limit`` 0 (unlimited in MongoDB), negative or above the maximum is clamped
  to ``MONGODB_TOOL_MAX_DOCUMENTS``; no limit -> ``MONGODB_TOOL_DEFAULT_LIMIT``;
* a server that accepts and then stalls cannot hold a call (or the worker
  thread) longer than ``MONGODB_TOOL_TIMEOUT_MS`` (it used to take ~60 s: two
  30 s socket timeouts for a retried read).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.core.config import get_settings
from app.mcp.servers import mongodb_server
from tests.mcp._fake_mongod import FakeMongod


@pytest.fixture
def bounds(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MONGODB_TOOL_DEFAULT_LIMIT", "20")
    monkeypatch.setenv("MONGODB_TOOL_MAX_DOCUMENTS", "50")
    monkeypatch.setenv("MONGODB_TOOL_TIMEOUT_MS", "1500")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("given", "expected"),
    [(None, 20), (0, 50), (-3, 50), (10, 10), (50, 50), (51, 50), (10**9, 50), ("7", 7)],
)
def test_find_limit_is_clamped(given: Any, expected: int, bounds: None) -> None:
    assert mongodb_server._find_limit({} if given is None else {"limit": given}) == expected


@pytest.mark.parametrize("given", ["many", [], {"x": 1}, 1.5, True])
def test_non_integer_limit_is_refused(given: Any, bounds: None) -> None:
    with pytest.raises(mongodb_server.MongoArgumentError, match="limit"):
        mongodb_server._find_limit({"limit": given})


@pytest.fixture
def stalled(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMongod]:
    import app.ingestion.connector_egress as egress

    srv = FakeMongod(stall=True)
    monkeypatch.setattr(egress, "_effective_allowlist", lambda: ["127.0.0.1"])
    try:
        yield srv
    finally:
        srv.close()


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("mongodb_find", {"collection": "c", "limit": 1}),
        ("mongodb_find_one", {"collection": "c"}),
        ("mongodb_count", {"collection": "c"}),
        ("mongodb_aggregate", {"collection": "c", "pipeline": [{"$match": {}}]}),
        ("mongodb_list_collections", {}),
        ("mongodb_insert_one", {"collection": "c", "document": {"a": 1}}),
        ("mongodb_update_one", {"collection": "c", "filter": {}, "update": {"a": 1}}),
        ("mongodb_delete_one", {"collection": "c", "filter": {"a": 1}}),
    ],
)
async def test_stalled_server_is_bounded_by_the_tool_timeout(
    tool: str, args: dict[str, Any], stalled: FakeMongod, bounds: None
) -> None:
    started = time.monotonic()
    result = await mongodb_server.call_tool(
        tool, args, credentials={"url": f"mongodb://127.0.0.1:{stalled.port}/db"}
    )
    elapsed = time.monotonic() - started

    assert "error" in result, result
    assert elapsed < 6.0, f"{tool} took {elapsed:.1f}s against a stalled server"
    assert any(c not in ("hello", "ismaster") for c in stalled.commands), stalled.commands
