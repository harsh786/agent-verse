"""KB-18: driver-level connectors dial the address the egress check validated.

mysql / clickhouse / imap / mqtt / postgresql / mongodb / neo4j / influxdb / s3 /
azure checked the tenant's host and then let the driver or SDK resolve it again,
so a name that answered public at check time and internal at connect time (DNS
rebinding) reached 127.0.0.1 / 10.x / 169.254.169.254.

Every test here uses a *rebinding resolver*: the first lookup of a name answers
with a public address, every later one with a private address. A driver that
looks the name up again after the check would be handed the private address;
pinned, it only ever sees the checked public one.
"""

from __future__ import annotations

import socket
import ssl
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion import connector_egress as egress
from app.ingestion.connector_egress import ConnectorEgressBlockedError
from app.ingestion.source_config import SourceConfig, SourceFamily

PUBLIC = "93.184.216.34"
PRIVATE = "10.0.0.5"
HOST = "db.rebind-attacker.example"


class _RebindingResolver:
    """Public on the first lookup of each name, private on every later one."""

    def __init__(self) -> None:
        self.lookups: dict[str, int] = {}

    def __call__(self, host: Any, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        # socket.getaddrinfo(host, port, family, type, proto, flags)
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
    """What a driver does: resolve the name with the stdlib and take the first answer."""
    return str(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][4][0])


def _config(source_type: str, **cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id=f"src-{source_type}",
        tenant_id="tenant-pin",
        name=source_type,
        family=SourceFamily.WEB,
        source_type=source_type,
        collection_id="col-1",
        connection_config=cc,
    )


# ── the pin itself ────────────────────────────────────────────────────────────


async def test_pinned_block_answers_with_the_checked_address_only(
    rebinding: _RebindingResolver,
) -> None:
    async with egress.pin_source_hosts([(HOST, 5432)], context="t") as pins:
        assert pins.ip(HOST) == PUBLIC
        # The driver's own (second, third...) lookups: still the checked address.
        assert _dialed(HOST, 5432) == PUBLIC
        assert _dialed(HOST.upper() + ".", 5432) == PUBLIC
    # Outside the block the name is no longer pinned — the rebinding answer shows.
    assert _dialed(HOST) == PRIVATE


async def test_internal_answer_at_check_time_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    )
    with pytest.raises(ConnectorEgressBlockedError):
        async with egress.pin_source_hosts([(HOST, 5432)], context="t"):
            pass


async def test_pins_are_released_even_when_the_block_raises(
    rebinding: _RebindingResolver,
) -> None:
    with pytest.raises(RuntimeError):
        async with egress.pin_source_hosts([(HOST, 1)], context="t"):
            raise RuntimeError("driver failed")
    assert egress._host_key(HOST) not in egress._pins


# ── driver families ───────────────────────────────────────────────────────────


async def test_mysql_driver_dials_the_checked_address(rebinding: _RebindingResolver) -> None:
    from app.ingestion.connectors.mysql_connector import MySQLConnector

    dialed: list[str] = []
    fake = ModuleType("pymysql")
    cursors = ModuleType("pymysql.cursors")
    cursors.DictCursor = object  # type: ignore[attr-defined]
    fake.cursors = cursors  # type: ignore[attr-defined]

    def _connect(**kw: Any) -> Any:
        dialed.append(_dialed(kw["host"], kw["port"]))
        conn = MagicMock()
        conn.cursor.return_value.fetchall.return_value = []
        return conn

    fake.connect = _connect  # type: ignore[attr-defined]
    cfg = _config("mysql", host=HOST, port=3306, table="t", primary_keys={"t": ["id"]})
    with patch.dict(sys.modules, {"pymysql": fake, "pymysql.cursors": cursors}):
        _ = [d async for d in MySQLConnector().get_delta(cfg, None)]
    assert dialed == [PUBLIC]


async def test_imap_driver_dials_the_checked_address(rebinding: _RebindingResolver) -> None:
    from app.ingestion.connectors import email_imap_connector as mod

    dialed: list[str] = []

    class _FakeIMAP:
        def __init__(self, host: str, port: int) -> None:
            dialed.append(_dialed(host, port))

        def login(self, *_a: Any) -> None: ...
        def select(self, *_a: Any) -> None: ...
        def logout(self) -> None: ...

        def uid(self, *_a: Any) -> tuple[str, list[bytes]]:
            return "OK", [b""]

    cfg = _config("imap", host=HOST, port=993, username="u", password="p")
    with patch.object(mod.imaplib, "IMAP4_SSL", _FakeIMAP):
        _ = [d async for d in mod.EmailIMAPConnector().get_delta(cfg, None)]
    assert dialed == [PUBLIC]


async def test_mongodb_driver_dials_the_checked_address(rebinding: _RebindingResolver) -> None:
    from app.ingestion.connectors.mongodb_connector import MongoDBConnector

    member = "member.rebind-attacker.example"
    dialed: list[tuple[str, str]] = []

    class _Client:
        def __init__(self, uri: str, **kw: Any) -> None:
            self.direct = bool(kw.get("directConnection"))
            dialed.append(("seed", _dialed(HOST, 27017)))
            if not self.direct:
                # The discovering client also dials the member the seed advertised.
                dialed.append(("member", _dialed(member, 27017)))
            self.admin = MagicMock()
            self.admin.command.return_value = {"hosts": [f"{HOST}:27017", f"{member}:27017"]}

        def __getitem__(self, _name: str) -> Any:
            db = MagicMock()
            db.list_collection_names.return_value = ["c"]
            db.__getitem__.return_value.find.return_value = []
            return db

        def close(self) -> None: ...

    cfg = _config("mongodb", host=HOST, port=27017, database="d", collection="c")
    with patch("pymongo.MongoClient", _Client):
        _ = [d async for d in MongoDBConnector().get_delta(cfg, None)]
    # Seed (discovery + client) and the advertised member: only checked addresses.
    assert dialed == [("seed", PUBLIC), ("seed", PUBLIC), ("member", PUBLIC)]


async def test_postgres_driver_is_handed_the_checked_ip(rebinding: _RebindingResolver) -> None:
    from app.ingestion.connectors.postgresql_connector import PostgreSQLConnector

    calls: list[dict[str, Any]] = []

    async def _connect(**kw: Any) -> Any:
        calls.append(kw)
        raise ConnectionError("not a real database")

    dsn = f"postgresql://u:p@{HOST}:5432/app?sslmode=verify-full"
    with patch("asyncpg.connect", _connect):
        health = await PostgreSQLConnector().validate_connection(_config("postgresql", dsn=dsn))
    assert health.ok is False
    assert calls, "asyncpg.connect was never reached"
    # asyncpg (uvloop resolves in libuv, outside socket.getaddrinfo) gets the IP…
    assert f"@{PUBLIC}:5432/app" in calls[0]["dsn"]
    assert HOST not in calls[0]["dsn"]
    # …and verify-full still checks the certificate against the configured name.
    ctx = calls[0]["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True
    assert ctx._host_for(PUBLIC) == egress._host_key(HOST)  # type: ignore[attr-defined]


async def test_s3_custom_endpoint_is_pinned_and_path_style(
    rebinding: _RebindingResolver,
) -> None:
    from app.ingestion.connectors.minio_connector import MinIOConnector

    dialed: list[str] = []
    configs: list[Any] = []

    class _Session:
        def __init__(self, **_k: Any) -> None: ...

        def client(self, _svc: str, **kw: Any) -> Any:
            configs.append(kw.get("config"))
            dialed.append(_dialed(HOST, 443))
            s3 = MagicMock()
            s3.get_paginator.return_value.paginate.return_value = []
            return s3

    cfg = _config("minio", endpoint_url=f"https://{HOST}", bucket="b")
    with patch("boto3.Session", _Session):
        _ = [d async for d in MinIOConnector().get_delta(cfg, None)]
    assert dialed == [PUBLIC]
    # Path-style: requests go to the checked host, never <bucket>.<host>.
    assert configs[0].s3 == {"addressing_style": "path"}


async def test_mongodb_srv_is_expanded_to_the_checked_targets(
    rebinding: _RebindingResolver,
) -> None:
    srv = [MagicMock(target="shard-0.cluster.rebind-attacker.example.", port=27017)]
    with (
        patch("dns.resolver.resolve") as resolve,
        patch.object(egress, "_srv_txt_options", return_value=[("authSource", "admin")]),
    ):
        resolve.return_value = srv
        async with egress.pin_source_dsn(
            "mongodb+srv://u:p@cluster.rebind-attacker.example/db?retryWrites=true",
            context="mongodb",
        ) as pins:
            # The driver gets a plain seed list: it runs no SRV query of its own.
            assert pins.dsn.startswith(
                "mongodb://u:p@shard-0.cluster.rebind-attacker.example:27017/db?"
            )
            assert "tls=true" in pins.dsn and "authSource=admin" in pins.dsn
            assert _dialed("shard-0.cluster.rebind-attacker.example", 27017) == PUBLIC


async def test_mongodb_srv_target_outside_the_parent_domain_is_refused(
    rebinding: _RebindingResolver,
) -> None:
    srv = [MagicMock(target="metadata.internal.", port=27017)]
    with (
        patch("dns.resolver.resolve", return_value=srv),
        pytest.raises(ConnectorEgressBlockedError),
    ):
        async with egress.pin_source_dsn(
            "mongodb+srv://cluster.rebind-attacker.example/", context="mongodb"
        ):
            pass


async def test_kafka_is_refused_while_strict_pinning_is_on() -> None:
    from app.ingestion.connectors.kafka_connector import KafkaConnector

    cfg = _config("kafka", bootstrap_servers="8.8.8.8:9092", topics=["t"])
    health = await KafkaConnector().validate_connection(cfg)
    assert health.ok is False
    assert "pinned" in (health.error or "")
    with pytest.raises(ConnectorEgressBlockedError):
        _ = [d async for d in KafkaConnector().get_delta(cfg, None)]


def test_pinned_ssl_context_sends_and_verifies_the_original_hostname() -> None:
    pins = egress.EgressPins(ips={"db.example.com": [PUBLIC]})
    ctx = egress.pinned_hostname_ssl(pins)
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    sslobj = ctx.wrap_bio(incoming, outgoing, server_hostname=PUBLIC)
    assert sslobj.server_hostname == "db.example.com"
