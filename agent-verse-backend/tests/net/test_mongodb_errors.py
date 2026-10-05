"""MDB-20: driver errors returned to tenants carry no topology or member hosts.

pymongo's ServerSelectionTimeoutError text includes the whole
TopologyDescription (every member address, rtt, nested errors); server errors
echo the command document. Tenants now get a short classified message with an
error id; the detail is logged server-side under that id.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from pymongo import errors as pe

from app.mcp.servers import mongodb_server
from app.net.mongodb_errors import public_mongo_error

TOPOLOGY = (
    "10.0.0.7:27017: [Errno 61] Connection refused, Timeout: 5.0s, Topology Description: "
    "<TopologyDescription id: 1, topology_type: ReplicaSetNoPrimary, servers: "
    "[<ServerDescription ('mongo-rs-internal', 27017) server_type: Unknown, rtt: None>]>"
)
LEAKS = ("10.0.0.7", "mongo-rs-internal", "Topology", "ServerDescription", "rtt")


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (pe.ServerSelectionTimeoutError(TOPOLOGY), "Could not reach"),
        (
            pe.ServerSelectionTimeoutError(
                "MongoDB member mongo-rs-internal:27017 is not listed in the connector's URI; "
                + TOPOLOGY
            ),
            "not listed in the connector's URI",
        ),
        (
            pe.ServerSelectionTimeoutError(
                "10.0.0.7:27017: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed "
                + TOPOLOGY
            ),
            "TLS handshake",
        ),
        (pe.OperationFailure("Authentication failed.", code=18), "Authentication failed"),
        (
            pe.OperationFailure(
                "not authorized on db_b to execute command { find: 'secret', filter: {ssn: 1} }",
                code=13,
                details={"codeName": "Unauthorized"},
            ),
            "Not authorized",
        ),
        (pe.ExecutionTimeout("operation exceeded time limit", code=50), "timed out"),
        (pe.NetworkTimeout("10.0.0.7:27017: timed out " + TOPOLOGY), "timed out"),
        (
            pe.DuplicateKeyError("E11000 duplicate key { email: 'a@b.c' }", code=11000),
            "Duplicate key",
        ),
        (
            pe.OperationFailure("x { filter: {ssn: 1} }", code=2, details={"codeName": "BadValue"}),
            "BadValue",
        ),
        (RuntimeError("boom " + TOPOLOGY), "The MongoDB call failed"),
    ],
)
def test_classified_without_leaks(
    exc: BaseException, expected: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        message = public_mongo_error(exc, context="test")
    assert expected in message
    assert "error id " in message
    for leak in (*LEAKS, "ssn", "a@b.c", "secret"):
        assert leak not in message, message
    # The detail is kept server-side, under the same id.
    error_id = message.rsplit("error id ", 1)[1].rstrip(")")
    logged = [r.getMessage() for r in caplog.records if error_id in r.getMessage()]
    assert logged and str(exc)[:50] in logged[0]


async def test_mcp_returns_the_sanitized_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import pymongo

    class _Failing:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def __getitem__(self, name: str) -> Any:
            raise pe.ServerSelectionTimeoutError(TOPOLOGY)

        def close(self) -> None:
            pass

    monkeypatch.setattr(pymongo, "MongoClient", _Failing)
    result = await mongodb_server.call_tool(
        "mongodb_count", {"collection": "c"}, credentials={"url": "mongodb://8.8.8.8:27017/db"}
    )
    assert "Could not reach" in result["error"]
    for leak in LEAKS:
        assert leak not in str(result), result
