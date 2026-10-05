"""MDB-10 / C3: the replica-member egress guard cannot silently stop working.

``_MemberGuard`` wraps pymongo's private socket factory
``pymongo.pool_shared._create_connection`` (pymongo has no public hook: a
listener cannot veto a connection, and monitor threads never carry the
egress-checked lookup scope). A pymongo refactor that renames or stops calling
it would disable the guard with no error. So:

* pymongo is pinned below the next minor (pyproject + uv.lock);
* installing the guard VERIFIES the hook (exists, ``(address, options)``,
  called by name from the socket-configuring functions) and refuses MongoDB
  calls when it does not hold (fail closed);
* a behavioural canary drives a REAL MongoClient against a hostile server that
  advertises an unlisted member — with the guard the victim is never dialled,
  without it (control) it is.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.mcp.servers import mongodb_server
from tests.mcp._fake_mongod import FakeMongod, Victim


def test_the_private_hook_still_exists_with_its_signature() -> None:
    import inspect

    import pymongo.pool_shared as pool_shared

    hook = getattr(pool_shared, "_create_connection", None)
    assert callable(hook), "pymongo.pool_shared._create_connection is gone: re-port the guard"
    # Once the guard is installed the module attribute is our wrapper.
    original = mongodb_server._ORIGINAL_CREATE_CONNECTION or hook
    params = list(inspect.signature(original).parameters)
    assert params[:2] == ["address", "options"], params


def test_the_hook_is_called_by_name_from_the_socket_factories() -> None:
    mongodb_server._assert_member_guard_hook()  # raises when the hook no longer applies


def test_pymongo_is_pinned_below_the_next_minor() -> None:
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())
    (spec,) = [d for d in pyproject["project"]["dependencies"] if d.startswith("pymongo")]
    assert "<" in spec, f"pymongo needs an upper bound (guard uses a private hook): {spec}"


@pytest.mark.parametrize(
    "breakage",
    ["removed", "renamed_caller"],
)
async def test_a_broken_hook_fails_closed(breakage: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import pymongo.pool_shared as pool_shared

    if breakage == "removed":
        monkeypatch.delattr(pool_shared, "_create_connection")
    else:
        # pymongo stopped calling the factory by that name: the wrapper would be inert.
        monkeypatch.setattr(mongodb_server, "_SOCKET_FACTORY_CALLERS", ("_no_such_function",))
    result = await mongodb_server.call_tool(
        "mongodb_count", {"collection": "c"}, credentials={"url": "mongodb://8.8.8.8:27017/db"}
    )
    assert "error" in result
    assert "member guard" in result["error"].lower(), result


@pytest.fixture
def hostile(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FakeMongod, Victim]]:
    import app.ingestion.connector_egress as egress

    victim = Victim()
    server = FakeMongod(set_name="rs0", advertise=(f"localhost:{victim.port}",))
    monkeypatch.setattr(egress, "_effective_allowlist", lambda: ["127.0.0.1"])
    try:
        yield server, victim
    finally:
        server.close()
        victim.close()


async def test_real_client_never_dials_an_advertised_member(
    hostile: tuple[FakeMongod, Victim],
) -> None:
    server, victim = hostile
    uri = f"mongodb://127.0.0.1:{server.port}/db?replicaSet=rs0"
    result = await mongodb_server.call_tool(
        "mongodb_find", {"collection": "c", "limit": 1}, credentials={"url": uri}
    )
    assert result.get("count") == 0, result
    time.sleep(1.5)  # monitors have discovered the advertised member by now
    assert victim.hits == 0


def test_control_an_unguarded_client_does_dial_it(hostile: tuple[FakeMongod, Victim]) -> None:
    """Without the guard the same server makes pymongo connect to the victim."""
    import pymongo

    server, victim = hostile
    client: Any = pymongo.MongoClient(
        f"mongodb://127.0.0.1:{server.port}/db?replicaSet=rs0", serverSelectionTimeoutMS=3000
    )
    try:
        client.db.c.find_one()
        deadline = time.monotonic() + 5
        while victim.hits == 0 and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        client.close()
    assert victim.hits > 0


async def test_member_advertised_after_discovery_is_never_dialled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TG-08: the server answers discovery clean, THEN advertises an internal member.

    The guard sits in the socket factory, so it also covers members a monitor
    learns about later (and the pooled client keeps living across calls, C2).
    """
    import app.ingestion.connector_egress as egress

    victim = Victim()
    server = FakeMongod(
        set_name="rs0", advertise=(f"localhost:{victim.port}",), advertise_late=True
    )
    monkeypatch.setattr(egress, "_effective_allowlist", lambda: ["127.0.0.1"])
    try:
        uri = f"mongodb://127.0.0.1:{server.port}/db?replicaSet=rs0&heartbeatFrequencyMS=500"
        for _ in range(3):  # the pooled client keeps monitoring between calls
            result = await mongodb_server.call_tool(
                "mongodb_find", {"collection": "c", "limit": 1}, credentials={"url": uri}
            )
            assert result.get("count") == 0, result
            time.sleep(1.0)
        assert server.hellos > 1  # the late advertisement was served
        assert victim.hits == 0
    finally:
        server.close()
        victim.close()
