"""RedisConnector — ingest keys from a tenant's Redis as knowledge documents.

A Sources connector (Sources -> NoSQL Database -> redis), configured on the
Source itself — independent of any Redis MCP server. Secrets live in the
Source's ``connection_config`` and are vault-encrypted at rest by
``SourceConfigStore`` / masked in every API response (``source_secrets``).

Connection config
-----------------
Topology (``mode``):
    standalone (default)  ``host`` + ``port`` (6379) + ``db`` (0), or ``uri``.
    sentinel              ``sentinels`` ("h1:26379,h2:26379"), ``sentinel_master``
                          (master name), optional ``sentinel_username`` /
                          ``sentinel_password`` (the Sentinel's own auth) and
                          ``sentinel_tls`` (defaults to ``tls``).
    cluster               ``cluster_nodes`` ("h1:7000,h2:7001") or ``host``/``port``
                          as the seed; ``db`` must be 0.

``uri`` (secret)          ``redis://[user[:password]@]host[:port][/db]`` or ``rediss://``
                          (TLS). Query options are not accepted — use the fields.
Authentication (``auth_type``, inferred when omitted):
    none                  no AUTH.
    password              ``password`` (``requirepass`` / the default user).
    acl                   ``username`` + ``password`` (Redis 6+ ACL user).
TLS:
    tls                   use TLS (implied by ``rediss://`` and by any TLS field).
    tls_ca_pem            PEM CA bundle to verify the server (default: system CAs).
    tls_client_cert       PEM client certificate for mutual TLS ...
    tls_client_private_key  ... and its private key (secret);
    tls_client_key_password optional key passphrase (secret).
    tls_check_hostname    verify the certificate's hostname (default true).
    tls_allow_invalid_certificates  explicit opt-out of verification (not recommended).
What to ingest:
    key_patterns          glob patterns (list or comma-separated; default ``*``).
    types                 subset of string, hash, list, set, zset, stream, json.
    max_keys_per_sync     keys per sync run (default 10000); the next run resumes.
    max_value_bytes       per-value byte cap (default 1 MiB); larger values are
                          truncated and flagged ``truncated`` in the metadata.
    max_items             entries read from a hash/list/set/zset/stream (default 1000).

Each key becomes one document (stable id per key). The cursor stores the SCAN
position per node and pattern, so a keyspace larger than ``max_keys_per_sync``
is covered over several runs; after a full pass the next run starts a new pass
(unchanged values are deduplicated by the pipeline's content hash).

Egress: every host dialled — the configured host, each Sentinel, the master a
Sentinel reports, every cluster node the seed announces — passes the connector
egress policy (internal addresses refused unless the operator allow-list
permits them) and is pinned to the checked addresses. Cluster keys are read from
each primary directly; MOVED redirects (which name arbitrary hosts) are never
followed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote, urlsplit

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorUnavailableError,
)
from app.ingestion.connector_egress import ConnectorEgressBlockedError, pin_source_hosts
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

_CONTEXT = "redis"
_DEFAULT_PORT = 6379
_DEFAULT_SENTINEL_PORT = 26379
_DEFAULT_MAX_KEYS = 10_000
_DEFAULT_MAX_VALUE_BYTES = 1024 * 1024
_DEFAULT_MAX_ITEMS = 1000
_SCAN_COUNT = 500
_DISCOVERY_SAMPLE = 1000
_TIMEOUT_S = 10.0

# Redis TYPE reply -> the name used in config / metadata.
_TYPE_NAMES = {
    "string": "string",
    "hash": "hash",
    "list": "list",
    "set": "set",
    "zset": "zset",
    "stream": "stream",
    "ReJSON-RL": "json",
}
_ALL_TYPES = frozenset(_TYPE_NAMES.values())
_MODES = frozenset({"standalone", "sentinel", "cluster"})
_AUTH_TYPES = frozenset({"none", "password", "acl"})
_TRUE = frozenset({"1", "true", "yes", "on"})


def _truthy(value: object) -> bool:
    return value is True or str(value or "").strip().lower() in _TRUE


def _split_list(value: object) -> list[str]:
    items = (
        [str(v) for v in value] if isinstance(value, list | tuple) else str(value or "").split(",")
    )
    return [i.strip() for i in items if i.strip()]


def _host_port(item: str, default_port: int) -> tuple[str, int]:
    item = item.strip()
    if item.startswith("["):
        host, _, rest = item[1:].partition("]")
        port = rest.lstrip(":")
    elif item.count(":") == 1:
        host, _, port = item.partition(":")
    else:
        host, port = item, ""
    if not host:
        raise ValueError(f"invalid Redis address {item!r}")
    try:
        return host, int(port) if port else default_port
    except ValueError as exc:
        raise ValueError(f"invalid port in Redis address {item!r}") from exc


def _addr_key(host: str, port: int) -> tuple[str, int]:
    return host.strip("[]").lower().rstrip("."), int(port)


@dataclass
class _Settings:
    mode: str
    host: str
    port: int
    db: int
    username: str
    password: str
    tls: bool
    tls_ca_pem: str
    tls_client_cert: str
    tls_client_key: str
    tls_key_password: str
    tls_check_hostname: bool
    tls_insecure: bool
    sentinels: list[tuple[str, int]] = field(default_factory=list)
    sentinel_master: str = ""
    sentinel_username: str = ""
    sentinel_password: str = ""
    sentinel_tls: bool = False
    cluster_nodes: list[tuple[str, int]] = field(default_factory=list)
    key_patterns: list[str] = field(default_factory=lambda: ["*"])
    types: frozenset[str] = _ALL_TYPES
    max_keys: int = _DEFAULT_MAX_KEYS
    max_value_bytes: int = _DEFAULT_MAX_VALUE_BYTES
    max_items: int = _DEFAULT_MAX_ITEMS

    @property
    def display(self) -> str:
        if self.mode == "sentinel":
            return f"sentinel/{self.sentinel_master}"
        if self.mode == "cluster":
            first = self.cluster_nodes[0] if self.cluster_nodes else (self.host, self.port)
            return f"cluster/{first[0]}:{first[1]}"
        return f"{self.host}:{self.port}"


def _settings(cc: dict[str, Any]) -> _Settings:
    """Parse and vet ``connection_config``; raises ValueError with an honest message."""
    host = str(cc.get("host") or "").strip()
    port = int(cc.get("port") or _DEFAULT_PORT)
    db = int(cc.get("db") or 0)
    username = str(cc.get("username") or "")
    password = str(cc.get("password") or "")
    tls = _truthy(cc.get("tls"))

    uri = str(cc.get("uri") or "").strip()
    if uri:
        parts = urlsplit(uri)
        scheme = parts.scheme.lower()
        if scheme not in ("redis", "rediss"):
            raise ValueError("Redis URL must start with redis:// or rediss://")
        if parts.query:
            raise ValueError(
                "Redis URL query options are not supported; use the connection fields "
                "(TLS files, timeouts and the like are set there)"
            )
        if not parts.hostname:
            raise ValueError("Redis URL has no host")
        host = parts.hostname
        port = parts.port or port
        path = parts.path.strip("/")
        if path:
            try:
                db = int(path)
            except ValueError as exc:
                raise ValueError(f"Redis URL database must be a number, got {path!r}") from exc
        # Explicit fields win over credentials embedded in the URL.
        username = username or unquote(parts.username or "")
        password = password or unquote(parts.password or "")
        tls = tls or scheme == "rediss"

    mode = str(cc.get("mode") or "standalone").strip().lower()
    if mode not in _MODES:
        raise ValueError(f"Redis mode must be one of {sorted(_MODES)}, got {mode!r}")

    auth_type = str(cc.get("auth_type") or "").strip().lower()
    if auth_type:
        if auth_type not in _AUTH_TYPES:
            raise ValueError(f"Redis auth_type must be one of {sorted(_AUTH_TYPES)}")
        if auth_type == "none":
            username = password = ""
        elif auth_type == "password":
            if not password:
                raise ValueError("Redis auth_type 'password' needs a password")
            username = ""
        elif not (username and password):
            raise ValueError("Redis auth_type 'acl' needs a username and a password")
    elif username and not password:
        raise ValueError("Redis ACL authentication needs a password with the username")

    ca = str(cc.get("tls_ca_pem") or "").strip()
    cert = str(cc.get("tls_client_cert") or "").strip()
    key = str(cc.get("tls_client_private_key") or "").strip()
    if bool(cert) != bool(key):
        raise ValueError("tls_client_cert and tls_client_private_key must be given together")
    tls = tls or bool(ca or cert)

    types = frozenset(t.lower() for t in _split_list(cc.get("types"))) or _ALL_TYPES
    unknown = sorted(types - _ALL_TYPES)
    if unknown:
        raise ValueError(f"unsupported Redis type(s) {unknown}; use {sorted(_ALL_TYPES)}")

    settings = _Settings(
        mode=mode,
        host=host,
        port=port,
        db=db,
        username=username,
        password=password,
        tls=tls,
        tls_ca_pem=ca,
        tls_client_cert=cert,
        tls_client_key=key,
        tls_key_password=str(cc.get("tls_client_key_password") or ""),
        tls_check_hostname=not (
            "tls_check_hostname" in cc and not _truthy(cc.get("tls_check_hostname"))
        ),
        tls_insecure=_truthy(cc.get("tls_allow_invalid_certificates")),
        key_patterns=_split_list(cc.get("key_patterns")) or ["*"],
        types=types,
        max_keys=max(1, int(cc.get("max_keys_per_sync") or _DEFAULT_MAX_KEYS)),
        max_value_bytes=max(1, int(cc.get("max_value_bytes") or _DEFAULT_MAX_VALUE_BYTES)),
        max_items=max(1, int(cc.get("max_items") or _DEFAULT_MAX_ITEMS)),
    )
    if mode == "sentinel":
        settings.sentinels = [
            _host_port(s, _DEFAULT_SENTINEL_PORT) for s in _split_list(cc.get("sentinels"))
        ]
        settings.sentinel_master = str(cc.get("sentinel_master") or "").strip()
        if not settings.sentinels or not settings.sentinel_master:
            raise ValueError("Redis Sentinel mode needs sentinels and sentinel_master")
        settings.sentinel_username = str(cc.get("sentinel_username") or "")
        settings.sentinel_password = str(cc.get("sentinel_password") or "")
        settings.sentinel_tls = (
            _truthy(cc.get("sentinel_tls")) if "sentinel_tls" in cc else settings.tls
        )
    elif mode == "cluster":
        settings.cluster_nodes = [
            _host_port(n, _DEFAULT_PORT) for n in _split_list(cc.get("cluster_nodes"))
        ] or ([(host, port)] if host else [])
        if not settings.cluster_nodes:
            raise ValueError("Redis Cluster mode needs cluster_nodes (or a host as the seed)")
        if db:
            raise ValueError("Redis Cluster only has database 0")
    elif not host:
        raise ValueError("Redis source needs a host or a redis:// URL")
    return settings


# ── Connections ────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _tls_kwargs(settings: _Settings, *, enabled: bool) -> Iterator[dict[str, Any]]:
    """redis-py SSL kwargs; the client cert/key PEM go to private temp files (redis-py
    only loads them from paths) that exist only while the connections are open."""
    if not enabled:
        yield {}
        return
    kwargs: dict[str, Any] = {
        "ssl": True,
        "ssl_cert_reqs": "none" if settings.tls_insecure else "required",
        "ssl_check_hostname": settings.tls_check_hostname and not settings.tls_insecure,
    }
    if settings.tls_ca_pem:
        kwargs["ssl_ca_data"] = settings.tls_ca_pem
    if not settings.tls_client_cert:
        yield kwargs
        return
    tmpdir = tempfile.mkdtemp(prefix="av-redis-tls-")
    try:
        for name, pem in (
            ("client.crt", settings.tls_client_cert),
            ("client.key", settings.tls_client_key),
        ):
            path = os.path.join(tmpdir, name)
            with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), "w") as fh:
                fh.write(pem + "\n")
            kwargs["ssl_certfile" if name.endswith(".crt") else "ssl_keyfile"] = path
        if settings.tls_key_password:
            kwargs["ssl_password"] = settings.tls_key_password
        yield kwargs
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _redis_module() -> Any:
    try:
        import redis
    except ImportError as exc:  # pragma: no cover - redis is a core dependency
        raise ConnectorUnavailableError(
            "redis-py is not installed on this server; the Redis connector cannot run"
        ) from exc
    return redis


def _client(
    host: str, port: int, *, db: int, username: str, password: str, tls: dict[str, Any]
) -> Any:
    redis = _redis_module()
    return redis.Redis(
        host=host,
        port=port,
        db=db,
        username=username or None,
        password=password or None,
        socket_timeout=_TIMEOUT_S,
        socket_connect_timeout=_TIMEOUT_S,
        client_name="agentverse-ingestion",
        decode_responses=False,
        **tls,
    )


def _discover_master(settings: _Settings, sentinel_tls: dict[str, Any]) -> tuple[str, int]:
    """Ask the (checked, pinned) Sentinels for the master's address."""
    from redis.sentinel import Sentinel

    sentinel_kwargs: dict[str, Any] = {
        "socket_timeout": _TIMEOUT_S,
        "socket_connect_timeout": _TIMEOUT_S,
        **sentinel_tls,
    }
    if settings.sentinel_username:
        sentinel_kwargs["username"] = settings.sentinel_username
    if settings.sentinel_password:
        sentinel_kwargs["password"] = settings.sentinel_password
    sentinel = Sentinel(settings.sentinels, sentinel_kwargs=sentinel_kwargs)
    try:
        host, port = sentinel.discover_master(settings.sentinel_master)
    finally:
        for conn in sentinel.sentinels:
            conn.close()
    return str(host), int(port)


def _cluster_primaries(settings: _Settings, tls: dict[str, Any]) -> list[tuple[str, int]]:
    """Primary node addresses the (checked, pinned) seed nodes announce (CLUSTER SLOTS)."""
    last_error: Exception | None = None
    for seed_host, seed_port in settings.cluster_nodes:
        client = _client(
            seed_host,
            seed_port,
            db=0,
            username=settings.username,
            password=settings.password,
            tls=tls,
        )
        try:
            slots = client.execute_command("CLUSTER SLOTS")
        except Exception as exc:
            last_error = exc
            continue
        finally:
            client.close()
        primaries: list[tuple[str, int]] = []
        for entry in slots or []:
            node = entry[2]
            host = node[0].decode() if isinstance(node[0], bytes) else str(node[0])
            primaries.append((host or seed_host, int(node[1])))
        if not primaries:
            raise ValueError("Redis Cluster reports no slots — is the cluster configured?")
        return sorted(set(primaries))
    raise last_error or ValueError("no Redis Cluster seed node answered")


@dataclass
class _Connection:
    """Open clients, keyed by node label (one per cluster primary)."""

    nodes: dict[str, Any]
    info: dict[str, Any]


@contextlib.asynccontextmanager
async def _connected(settings: _Settings) -> AsyncIterator[_Connection]:
    """Clients whose every dialled host passed the egress policy and is pinned."""
    _redis_module()
    async with contextlib.AsyncExitStack() as stack:
        tls = stack.enter_context(_tls_kwargs(settings, enabled=settings.tls))
        if settings.mode == "sentinel":
            await stack.enter_async_context(pin_source_hosts(settings.sentinels, context=_CONTEXT))
            sentinel_tls = stack.enter_context(_tls_kwargs(settings, enabled=settings.sentinel_tls))
            master = await asyncio.to_thread(_discover_master, settings, sentinel_tls)
            try:
                await stack.enter_async_context(pin_source_hosts([master], context=_CONTEXT))
            except ConnectorEgressBlockedError as exc:
                raise ConnectorEgressBlockedError(
                    f"the master address {master[0]}:{master[1]} reported by Sentinel is not "
                    f"allowed by the egress policy ({exc})"
                ) from exc
            targets = {f"{master[0]}:{master[1]}": master}
            info: dict[str, Any] = {"mode": "sentinel", "master": f"{master[0]}:{master[1]}"}
        elif settings.mode == "cluster":
            await stack.enter_async_context(
                pin_source_hosts(settings.cluster_nodes, context=_CONTEXT)
            )
            primaries = await asyncio.to_thread(_cluster_primaries, settings, tls)
            seeds = {_addr_key(h, p) for h, p in settings.cluster_nodes}
            announced = [a for a in primaries if _addr_key(*a) not in seeds]
            try:
                await stack.enter_async_context(pin_source_hosts(announced, context=_CONTEXT))
            except ConnectorEgressBlockedError as exc:
                names = ", ".join(f"{h}:{p}" for h, p in announced)
                raise ConnectorEgressBlockedError(
                    f"cluster node(s) {names} announced by the seed are not allowed by the "
                    f"egress policy ({exc})"
                ) from exc
            targets = {f"{h}:{p}": (h, p) for h, p in primaries}
            info = {"mode": "cluster", "primaries": sorted(targets)}
        else:
            await stack.enter_async_context(
                pin_source_hosts([(settings.host, settings.port)], context=_CONTEXT)
            )
            targets = {f"{settings.host}:{settings.port}": (settings.host, settings.port)}
            info = {"mode": "standalone"}

        db = 0 if settings.mode == "cluster" else settings.db
        clients: dict[str, Any] = {}
        try:
            for label, (host, port) in targets.items():
                clients[label] = _client(
                    host,
                    port,
                    db=db,
                    username=settings.username,
                    password=settings.password,
                    tls=tls,
                )
            yield _Connection(nodes=clients, info=info)
        finally:
            for client in clients.values():
                await asyncio.to_thread(client.close)


# ── Discovery ──────────────────────────────────────────────────────────────────


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _pattern_of(key: str) -> str:
    for sep in (":", "/", "."):
        if sep in key:
            return key.split(sep, 1)[0] + sep + "*"
    return key


def _discover(conn: _Connection, settings: _Settings) -> dict[str, Any]:
    meta: dict[str, Any] = dict(conn.info)
    meta["db"] = 0 if settings.mode == "cluster" else settings.db
    total_keys = 0
    patterns: dict[str, dict[str, Any]] = {}
    type_counts: dict[str, int] = {}
    sampled = 0
    for client in conn.nodes.values():
        info = client.info("server")
        meta.setdefault("redis_version", info.get("redis_version"))
        total_keys += int(client.dbsize())
        for pattern in settings.key_patterns:
            for key in client.scan_iter(match=pattern, count=_SCAN_COUNT):
                if sampled >= _DISCOVERY_SAMPLE:
                    break
                sampled += 1
                ktype = _TYPE_NAMES.get(_text(client.type(key)), _text(client.type(key)))
                type_counts[ktype] = type_counts.get(ktype, 0) + 1
                group = patterns.setdefault(_pattern_of(_text(key)), {"count": 0, "types": {}})
                group["count"] += 1
                group["types"][ktype] = group["types"].get(ktype, 0) + 1
    meta["keys"] = total_keys
    meta["sampled_keys"] = sampled
    meta["types"] = type_counts
    meta["key_patterns"] = [
        {"pattern": p, **g} for p, g in sorted(patterns.items(), key=lambda kv: -kv[1]["count"])
    ][:50]
    return meta


# ── Reading values ─────────────────────────────────────────────────────────────


def _read_key(client: Any, key: bytes, settings: _Settings) -> tuple[str, str, bool] | None:
    """``(type, text, truncated)`` for ``key``; None when absent/unsupported/filtered."""
    raw_type = _text(client.type(key))
    ktype = _TYPE_NAMES.get(raw_type)
    if ktype is None or ktype not in settings.types:
        return None
    cap, items = settings.max_value_bytes, settings.max_items
    truncated = False
    if ktype == "string":
        length = int(client.strlen(key))
        value = client.getrange(key, 0, cap - 1) if length > cap else client.get(key)
        truncated = length > cap
        body = _text(value or b"")
    elif ktype == "hash":
        truncated = int(client.hlen(key)) > items
        pairs = []
        for field_name, value in client.hscan_iter(key, count=min(items, _SCAN_COUNT)):
            pairs.append(f"{_text(field_name)}: {_text(value)}")
            if len(pairs) >= items:
                break
        body = "\n".join(pairs)
    elif ktype == "list":
        truncated = int(client.llen(key)) > items
        body = "\n".join(_text(v) for v in client.lrange(key, 0, items - 1))
    elif ktype == "set":
        truncated = int(client.scard(key)) > items
        members = []
        for member in client.sscan_iter(key, count=min(items, _SCAN_COUNT)):
            members.append(_text(member))
            if len(members) >= items:
                break
        body = "\n".join(sorted(members))
    elif ktype == "zset":
        truncated = int(client.zcard(key)) > items
        body = "\n".join(
            f"{_text(m)} ({s:g})" for m, s in client.zrange(key, 0, items - 1, withscores=True)
        )
    elif ktype == "stream":
        truncated = int(client.xlen(key)) > items
        entries = list(reversed(client.xrevrange(key, count=items)))
        body = "\n".join(
            _text(eid) + " " + " ".join(f"{_text(k)}={_text(v)}" for k, v in fields.items())
            for eid, fields in entries
        )
    else:  # json (RedisJSON)
        raw = client.execute_command("JSON.GET", key)
        try:
            body = json.dumps(json.loads(_text(raw or b"null")), indent=2, ensure_ascii=False)
        except ValueError:
            body = _text(raw or b"")
    encoded = body.encode()
    if len(encoded) > cap:
        body = encoded[:cap].decode("utf-8", errors="ignore")
        truncated = True
    return ktype, body, truncated


# ── Cursor ─────────────────────────────────────────────────────────────────────


def _decode_cursor(cursor: str | None, patterns: list[str]) -> dict[str, Any]:
    """``{"p": pattern index, "n": {node: scan cursor}}``; empty -> start a new pass."""
    if not cursor:
        return {"p": 0, "n": {}}
    try:
        data = json.loads(cursor)
    except ValueError as exc:
        raise ValueError(f"stored Redis cursor is unreadable: {exc}") from exc
    if not isinstance(data, dict) or data.get("done") or data.get("patterns") != patterns:
        return {"p": 0, "n": {}}  # finished pass or changed patterns -> new pass
    return {"p": int(data.get("p", 0)), "n": dict(data.get("n") or {})}


def _encode_cursor(state: dict[str, Any], patterns: list[str], *, done: bool = False) -> str:
    if done:
        return json.dumps({"v": 1, "done": True, "at": int(time.time())})
    return json.dumps(
        {"v": 1, "patterns": patterns, "p": state["p"], "n": state["n"]}, sort_keys=True
    )


@register("redis")
class RedisConnector(BaseConnector):
    """Redis connector — standalone, Sentinel or Cluster; every common auth mode."""

    source_type = "redis"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        t0 = time.perf_counter()
        try:
            settings = _settings(config.connection_config)
            async with _connected(settings) as conn:
                meta = await asyncio.to_thread(_discover, conn, settings)
            return ConnectionHealth(
                ok=True, latency_ms=(time.perf_counter() - t0) * 1000, metadata=meta
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc) or type(exc).__name__)

    @classmethod
    def check_connection_policy(cls, connection_config: dict[str, Any]) -> None:
        """P1c-10: the settings a sync would refuse (URL query options, an unknown
        type, Sentinel without a master, Cluster with a database ...) are refused
        when the Source is saved. A config without any address yet is left to the
        Source's configuration status."""
        cc = dict(connection_config or {})
        if not any(cc.get(k) for k in ("host", "uri", "sentinels", "cluster_nodes")):
            return
        _settings(cc)

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        settings = _settings(config.connection_config)
        patterns = settings.key_patterns
        state = _decode_cursor(cursor, patterns)
        resumed = bool(state["p"] or state["n"])
        budget = [settings.max_keys]
        async with _connected(settings) as conn:
            # Documents are released one behind, so the last one of a finished
            # pass can carry the "pass done" cursor (-> the next sync starts over).
            pending: tuple[RawDocument, str] | None = None
            for attempt in range(2):
                completed = True
                async for item in _scan_pass(conn, settings, config, state, budget):
                    if item is None:
                        completed = False  # max_keys_per_sync reached
                        continue
                    if pending is not None:
                        yield pending
                    pending = item
                if not completed:
                    break
                if pending is not None:
                    yield pending[0], _encode_cursor(state, patterns, done=True)
                    return
                if not resumed or attempt:
                    return
                # A resumed pass had nothing left: begin the next pass right away.
                state.update({"p": 0, "n": {}})
            if pending is not None:
                yield pending


async def _scan_pass(
    conn: _Connection,
    settings: _Settings,
    config: SourceConfig,
    state: dict[str, Any],
    budget: list[int],
) -> AsyncIterator[tuple[RawDocument, str] | None]:
    """One SCAN pass from ``state`` (mutated as it advances); yields ``(doc, cursor)``
    and a final ``None`` when the per-sync key budget runs out mid-pass.

    Each document's cursor points at the start of its SCAN batch (the last one of
    a batch at the next batch), so an interrupted sync re-reads at most one batch.
    """
    from app.ingestion.source_config import RawDocument

    patterns = settings.key_patterns
    db = 0 if settings.mode == "cluster" else settings.db
    while state["p"] < len(patterns):
        pattern = patterns[state["p"]]
        for label, client in conn.nodes.items():
            scan_at = int(state["n"].get(label, 0))
            while scan_at != -1:
                if budget[0] <= 0:
                    yield None
                    return
                next_at, keys = await asyncio.to_thread(client.scan, scan_at, pattern, _SCAN_COUNT)
                following = int(next_at) or -1
                for index, key in enumerate(keys):
                    if budget[0] <= 0:
                        yield None
                        return
                    read = await asyncio.to_thread(_read_key, client, key, settings)
                    if read is None:
                        continue
                    ktype, body, truncated = read
                    name = _text(key)
                    url = f"redis://{settings.display}/{db}/{quote(name, safe='')}"
                    at = following if index == len(keys) - 1 else scan_at
                    cursor = _encode_cursor(
                        {"p": state["p"], "n": {**state["n"], label: at}}, patterns
                    )
                    budget[0] -= 1
                    yield (
                        RawDocument(
                            doc_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{config.source_id}:{url}")),
                            source_id=config.source_id,
                            tenant_id=config.tenant_id,
                            source_url=url,
                            title=name,
                            content=f"# {name}\n\ntype: {ktype}\n\n{body}".encode(),
                            content_type="text/plain",
                            metadata={
                                "key": name,
                                "type": ktype,
                                "db": db,
                                "node": label,
                                "truncated": truncated,
                            },
                        ),
                        cursor,
                    )
                state["n"][label] = following
                scan_at = following
        state["p"] += 1
        state["n"] = {}
