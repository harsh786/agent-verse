"""NEO4J-EGRESS: drivers that learn *extra* hosts from the server are egress-checked.

A Neo4j ``neo4j://`` routing table, an HTTP redirect followed inside an SDK
(azure-core, urllib3), a cluster's advertised members — the seed host was
checked and pinned, but the driver then dialled whatever else the server told it
to, with a plain ``socket.getaddrinfo`` that nothing checked. Driver work now
runs inside :func:`connector_egress.egress_checked_lookups`: every lookup made
from that thread is checked under the ingestion egress policy and answered with
the checked addresses only.
"""

from __future__ import annotations

import socket
import threading
from typing import Any

import pytest

from app.ingestion import connector_egress as egress
from app.ingestion.connector_egress import ConnectorEgressBlockedError

PUBLIC = "93.184.216.34"
PRIVATE = "10.0.0.5"
HOST = "extra-host.rebind-attacker.example"


class _RebindingResolver:
    """Public on the first lookup of each name, private on every later one."""

    def __init__(self) -> None:
        self.lookups: dict[str, int] = {}

    def __call__(self, host: Any, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        flags = int(args[3]) if len(args) > 3 else int(kwargs.get("flags", 0))
        name = host.decode() if isinstance(host, bytes) else str(host)
        if flags & socket.AI_NUMERICHOST or egress._is_ip_literal(name):
            ip = name
        else:
            seen = self.lookups.get(name, 0)
            self.lookups[name] = seen + 1
            ip = PUBLIC if seen == 0 else PRIVATE
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (ip, int(port or 0)))]


@pytest.fixture
def rebinding(monkeypatch: pytest.MonkeyPatch) -> _RebindingResolver:
    resolver = _RebindingResolver()
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    return resolver


def _dialed(host: str, port: int = 0) -> str:
    return str(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][4][0])


def test_internal_literal_is_refused_inside_the_scope(rebinding: _RebindingResolver) -> None:
    with egress.egress_checked_lookups("neo4j"), pytest.raises(ConnectorEgressBlockedError):
        _dialed(PRIVATE, 7687)
    # Outside the scope the platform's own lookups are untouched.
    assert _dialed(PRIVATE, 7687) == PRIVATE


def test_unpinned_name_is_checked_and_answered_with_the_checked_address(
    rebinding: _RebindingResolver,
) -> None:
    with egress.egress_checked_lookups("neo4j"):
        # Checked once (public), then every answer is the checked address even
        # though the name now rebinds to a private one.
        assert _dialed(HOST, 7687) == PUBLIC
    assert _dialed(HOST) == PRIVATE


def test_name_resolving_internal_is_refused_inside_the_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    )
    with egress.egress_checked_lookups("neo4j"), pytest.raises(ConnectorEgressBlockedError):
        _dialed("router.internal.example", 7687)


def test_scope_does_not_leak_into_other_threads(rebinding: _RebindingResolver) -> None:
    seen: list[str] = []

    def _other() -> None:
        seen.append(_dialed(PRIVATE))

    with egress.egress_checked_lookups("neo4j"):
        t = threading.Thread(target=_other)
        t.start()
        t.join()
    assert seen == [PRIVATE]


async def test_run_driver_call_runs_in_a_checked_scope(rebinding: _RebindingResolver) -> None:
    with pytest.raises(ConnectorEgressBlockedError):
        await egress.run_driver_call(_dialed, PRIVATE, 7687, context="neo4j")
    assert await egress.run_driver_call(_dialed, HOST, 7687, context="neo4j") == PUBLIC
