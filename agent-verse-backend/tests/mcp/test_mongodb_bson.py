"""TG-09: BSON types survive the MCP path as JSON-safe values.

``_serialize`` only stringified the top-level ``_id``: Decimal128, Binary,
Regex, Int64, datetimes and nested ObjectIds reached the tool result as driver
objects (not JSON-serialisable for the model / event stream). Documents are now
rendered as relaxed Extended JSON (lossless type tags), ``_id`` as a string.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from collections.abc import Iterator
from typing import Any

import pytest
from bson import Binary, Decimal128, Int64, ObjectId, Regex

from app.mcp.servers import mongodb_server

OID = ObjectId("65f000000000000000000001")
DOC: dict[str, Any] = {
    "_id": OID,
    "price": Decimal128("1.10"),
    "blob": Binary(b"\x00\x01\x02", 0),
    "pattern": Regex("^ab", "i"),
    "big": Int64(2**40),
    "when": datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC),
    "ref": ObjectId("65f000000000000000000002"),
    "none": None,
    "many": list(range(50)),
    "nested": {"inner": [{"oid": ObjectId("65f000000000000000000003")}]},
}


def _check(doc: dict[str, Any]) -> None:
    json.dumps(doc)  # strictly JSON-safe, no default=str
    assert doc["_id"] == str(OID)
    assert doc["price"] == {"$numberDecimal": "1.10"}
    assert doc["blob"]["$binary"]["base64"] == "AAEC"
    assert doc["pattern"]["$regularExpression"] == {"pattern": "^ab", "options": "i"}
    assert doc["big"] == 2**40
    assert doc["when"] == {"$date": "2026-01-02T03:04:05Z"}
    assert doc["ref"] == {"$oid": "65f000000000000000000002"}
    assert doc["none"] is None
    assert doc["many"] == list(range(50))
    assert doc["nested"]["inner"][0]["oid"] == {"$oid": "65f000000000000000000003"}


def test_serialize_renders_every_bson_type() -> None:
    (out,) = mongodb_server._serialize([dict(DOC)])
    _check(out)


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
        admin.shop.typed.insert_one(dict(DOC))
        try:
            yield {"host": host, "port": port}
        finally:
            admin.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("tool", "args", "pick"),
    [
        ("mongodb_find", {"collection": "typed"}, lambda r: r["documents"][0]),
        ("mongodb_find_one", {"collection": "typed"}, lambda r: r["document"]),
        (
            "mongodb_aggregate",
            {"collection": "typed", "pipeline": [{"$match": {}}]},
            lambda r: r["results"][0],
        ),
    ],
)
async def test_real_documents_round_trip(
    tool: str,
    args: dict[str, Any],
    pick: Any,
    mongo: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.ingestion.connector_egress as egress

    monkeypatch.setattr(egress, "_effective_allowlist", lambda: [mongo["host"]])
    result = await mongodb_server.call_tool(
        tool, args, credentials={"url": f"mongodb://{mongo['host']}:{mongo['port']}/shop"}
    )
    assert "error" not in result, result
    _check(pick(result))
    assert not re.search(r"ObjectId\(|Decimal128\(", json.dumps(result))
