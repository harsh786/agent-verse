"""MongoDBConnector — MongoDB document database ingestion.

Supports MongoDB Atlas (``mongodb+srv://``), self-hosted standalone servers and
replica sets, and DocumentDB-compatible endpoints.

Connection config (Sources UI: NoSQL / Relational -> mongodb):

    uri                  ``mongodb://`` / ``mongodb+srv://`` connection string (secret).
                         Alternatively ``host`` (``h1:27017,h2:27017`` allowed) + ``port``.
    username, password   Credentials. Used as driver options, so special characters
                         need no URL-escaping; they override credentials in ``uri``.
    auth_source          Database the user is defined in. Defaults to ``admin`` when
                         the credentials come from the fields above (and the URI
                         names no ``authSource``) — the common case the driver's own
                         default (the URI's database) gets wrong.
    auth_mechanism       SCRAM-SHA-256 / SCRAM-SHA-1 / PLAIN / MONGODB-X509.
    replica_set          Replica-set name (host/port form).
    direct_connection    Talk to the given host only (no replica-set discovery).
    tls                  Require TLS.
    tls_ca_pem           PEM CA bundle to verify the server with (instead of system CAs).
    tls_client_cert      PEM client certificate (mutual TLS / MONGODB-X509) ...
    tls_client_private_key  ... and its private key (secret);
    tls_client_key_password optional key passphrase (secret).
    tls_allow_invalid_certificates  Explicit opt-out of server certificate checks.
    database             Database to ingest (required).
    collections          List of collections (or ``collections_csv`` / ``collection``);
                         empty -> every non-system collection in the database.
    cursor_field         Field for incremental sync (default ``_id``); ties are broken
                         by ``_id`` so paging never skips documents.
    batch_size           Documents per page (default 500).
    timeout_ms           Lowers the connect / server-selection timeouts (never raises
                         them). Socket reads and each query's server time (maxTimeMS)
                         are bounded by operator settings (INGESTION_MONGODB_*).
    max_documents_per_sync  Cap per sync run (default 10000); the next sync resumes
                         from the cursor.

Cursor: JSON (MongoDB canonical Extended JSON) holding, per collection, the last
``cursor_field`` value and ``_id`` processed. A legacy cursor (a bare ObjectId
string) is honoured for the single configured collection.

Egress: every seed host (and SRV target) must pass the connector egress policy
and is pinned for the connection; replica-set members the server advertises are
checked and pinned before the driver may dial them, and a server selector keeps
operations on checked members only. URI options that read platform files
(``tlsCAFile``...), route through a proxy, or authenticate with the platform's
own ambient credentials (MONGODB-AWS / OIDC / GSSAPI) are refused.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorUnavailableError,
)
from app.ingestion.connector_egress import (
    ConnectorEgressBlockedError,
    _dsn_hosts,
    pin_source_dsn,
    pin_source_hosts,
)
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

_CONTEXT = "mongodb"
_DEFAULT_BATCH = 500
_DEFAULT_MAX_DOCS = 10_000
_MAX_COLLECTIONS = 200

# URI options a tenant may not set: they read files on the platform's disk, send
# traffic through an arbitrary (unchecked) proxy, or pass provider properties
# that make the driver fetch the platform's own cloud credentials.
_FORBIDDEN_URI_OPTIONS = frozenset(
    {
        "tlscafile",
        "tlscertificatekeyfile",
        "tlscertificatekeyfilepassword",
        "tlscrlfile",
        "ssl_ca_certs",
        "ssl_certfile",
        "ssl_keyfile",
        "ssl_crlfile",
        "ssl_pem_passphrase",
        "authmechanismproperties",
        "proxyhost",
        "proxyport",
        "proxyusername",
        "proxypassword",
    }
)
# MONGODB-AWS / MONGODB-OIDC / GSSAPI authenticate with ambient credentials
# (instance metadata, env vars, Kerberos tickets) — the platform's, not the tenant's.
_ALLOWED_AUTH_MECHANISMS = frozenset({"SCRAM-SHA-1", "SCRAM-SHA-256", "PLAIN", "MONGODB-X509"})
_TRUE = frozenset({"1", "true", "yes", "on"})


def _truthy(value: object) -> bool:
    return value is True or str(value or "").strip().lower() in _TRUE


def _mongo_uri(cc: dict[str, Any]) -> str:
    """The tenant's URI, or one built from host/port — never a localhost default
    (that is the platform's own infrastructure, not the tenant's database)."""
    uri = str(cc.get("uri") or cc.get("connection_string") or "").strip()
    if uri:
        return uri
    host = str(cc.get("host") or "").strip()
    if not host:
        raise ValueError("MongoDB source needs a connection URI or a host")
    port = str(cc.get("port") or 27017)
    hosts = []
    for item in host.split(","):
        item = item.strip()
        if not item:
            continue
        has_port = "]:" in item if item.startswith("[") else ":" in item
        hosts.append(item if has_port else f"{item}:{port}")
    return f"mongodb://{','.join(hosts)}/"


def _driver_bounds() -> tuple[int, int, int, int]:
    """(connect, server selection, socket, maxTimeMS) in ms, from operator settings.

    Never 0: pymongo reads 0 as "no timeout".
    """
    from app.core.config import get_settings

    cfg = get_settings()
    return (
        max(1, int(cfg.ingestion_mongodb_connect_timeout_ms)),
        max(1, int(cfg.ingestion_mongodb_server_selection_timeout_ms)),
        max(1, int(cfg.ingestion_mongodb_socket_timeout_ms)),
        max(1, int(cfg.ingestion_mongodb_max_time_ms)),
    )


def _split_list(value: object) -> list[str]:
    if isinstance(value, list | tuple):
        items = [str(v) for v in value]
    else:
        items = str(value or "").split(",")
    return [i.strip() for i in items if i.strip()]


@dataclass
class _Settings:
    uri: str
    database: str
    collections: list[str]
    cursor_field: str
    batch_size: int
    max_documents: int
    display_host: str
    discover_members: bool
    kwargs: dict[str, Any] = field(default_factory=dict)
    max_time_ms: int = 30_000
    tls_ca_pem: str = ""
    tls_client_pem: str = ""
    tls_client_key_password: str = ""


def _settings(cc: dict[str, Any], *, require_database: bool = True) -> _Settings:
    """Parse and vet ``connection_config``; raises ValueError with an honest message."""
    uri = _mongo_uri(cc)
    parts = urlsplit(uri)
    if parts.scheme.lower() not in ("mongodb", "mongodb+srv"):
        raise ValueError("MongoDB URI must start with mongodb:// or mongodb+srv://")
    options = {k.lower(): v for k, v in parse_qsl(parts.query, keep_blank_values=True)}
    forbidden = sorted(set(options) & _FORBIDDEN_URI_OPTIONS)
    if forbidden:
        raise ValueError(
            f"MongoDB URI option(s) {', '.join(forbidden)} are not allowed; use the "
            "tls_ca_pem / tls_client_cert / tls_client_private_key fields for certificates"
        )

    # C1 / MDB-12: every wait is bounded. Driver kwargs override the same URI
    # options, so ``socketTimeoutMS=0`` in a tenant URI cannot unbound a read;
    # the tenant's ``timeout_ms`` may only lower connect / server selection.
    connect_ms, selection_ms, socket_ms, max_time_ms = _driver_bounds()
    tenant_ms = int(cc.get("timeout_ms") or 0)
    if tenant_ms > 0:
        connect_ms = min(connect_ms, tenant_ms)
        selection_ms = min(selection_ms, tenant_ms)
    kwargs: dict[str, Any] = {
        "serverSelectionTimeoutMS": selection_ms,
        "connectTimeoutMS": connect_ms,
        "socketTimeoutMS": socket_ms,
        "appname": "agentverse-ingestion",
    }
    username = str(cc.get("username") or "")
    password = str(cc.get("password") or "")
    if username:
        kwargs["username"] = username
    if password:
        kwargs["password"] = password
    auth_source = str(cc.get("auth_source") or "").strip()
    if auth_source:
        kwargs["authSource"] = auth_source
    elif username and "authsource" not in options:
        kwargs["authSource"] = "admin"

    mechanism = str(cc.get("auth_mechanism") or options.get("authmechanism") or "").strip()
    if mechanism:
        if mechanism.upper() not in _ALLOWED_AUTH_MECHANISMS:
            raise ValueError(
                f"MongoDB auth mechanism {mechanism!r} is not allowed (it would use the "
                f"platform's own credentials); use one of {sorted(_ALLOWED_AUTH_MECHANISMS)}"
            )
        kwargs["authMechanism"] = mechanism.upper()

    if cc.get("replica_set"):
        kwargs["replicaSet"] = str(cc["replica_set"])
    direct = _truthy(cc.get("direct_connection")) or _truthy(options.get("directconnection"))
    if _truthy(cc.get("direct_connection")):
        kwargs["directConnection"] = True
    load_balanced = _truthy(options.get("loadbalanced"))

    tls_ca_pem = str(cc.get("tls_ca_pem") or "").strip()
    client_cert = str(cc.get("tls_client_cert") or "").strip()
    client_key = str(cc.get("tls_client_private_key") or "").strip()
    if bool(client_cert) != bool(client_key):
        raise ValueError("tls_client_cert and tls_client_private_key must be given together")
    if _truthy(cc.get("tls")) or tls_ca_pem or client_cert:
        kwargs["tls"] = True
    if _truthy(cc.get("tls_allow_invalid_certificates")):
        kwargs["tlsAllowInvalidCertificates"] = True
    if kwargs.get("authMechanism") == "MONGODB-X509" and not client_cert:
        raise ValueError("MONGODB-X509 authentication needs tls_client_cert and its private key")

    database = str(cc.get("database") or "").strip() or parts.path.lstrip("/")
    if require_database and not database:
        raise ValueError("MongoDB source needs a database")
    collections = _split_list(cc.get("collections") or cc.get("collections_csv"))
    if not collections and cc.get("collection"):
        collections = [str(cc["collection"]).strip()]

    seeds = _dsn_hosts(uri)
    display_host = ",".join(f"{h}:{p}" if p else h for h, p in seeds)
    return _Settings(
        uri=uri,
        database=database,
        collections=collections,
        cursor_field=str(cc.get("cursor_field") or "_id"),
        batch_size=max(1, int(cc.get("batch_size") or _DEFAULT_BATCH)),
        max_documents=max(1, int(cc.get("max_documents_per_sync") or _DEFAULT_MAX_DOCS)),
        display_host=display_host,
        discover_members=not direct and not load_balanced,
        kwargs=kwargs,
        max_time_ms=max_time_ms,
        tls_ca_pem=tls_ca_pem,
        tls_client_pem=f"{client_cert}\n{client_key}\n" if client_cert else "",
        tls_client_key_password=str(cc.get("tls_client_key_password") or ""),
    )


@contextlib.contextmanager
def _materialised_tls(settings: _Settings) -> Iterator[dict[str, Any]]:
    """Driver kwargs with the PEM material written to private temp files.

    pymongo only takes certificate *paths*; the tenant supplies PEM text, which is
    written to a 0700 directory for the lifetime of the client and removed after.
    """
    kwargs = dict(settings.kwargs)
    if not settings.tls_ca_pem and not settings.tls_client_pem:
        yield kwargs
        return
    tmpdir = tempfile.mkdtemp(prefix="av-mongo-tls-")
    try:
        if settings.tls_ca_pem:
            path = os.path.join(tmpdir, "ca.pem")
            with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), "w") as fh:
                fh.write(settings.tls_ca_pem + "\n")
            kwargs["tlsCAFile"] = path
        if settings.tls_client_pem:
            path = os.path.join(tmpdir, "client.pem")
            with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), "w") as fh:
                fh.write(settings.tls_client_pem)
            kwargs["tlsCertificateKeyFile"] = path
            if settings.tls_client_key_password:
                kwargs["tlsCertificateKeyFilePassword"] = settings.tls_client_key_password
        yield kwargs
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _host_key(host: str, port: object) -> tuple[str, int]:
    return host.strip("[]").lower().rstrip("."), int(str(port or 27017))


def _parse_member(member: str) -> tuple[str, int]:
    if member.startswith("["):
        host, _, rest = member[1:].partition("]")
        return _host_key(host, rest.lstrip(":") or 27017)
    host, _, port = member.rpartition(":") if member.count(":") == 1 else (member, "", "")
    return _host_key(host, port or 27017)


def _single_host_uri(dsn: str, host: str, port: int) -> str:
    parts = urlsplit(dsn)
    userinfo, sep, _hosts = parts.netloc.rpartition("@")
    netloc_host = f"[{host}]" if ":" in host else host
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in ("replicaset", "directconnection", "loadbalanced")
    ]
    from urllib.parse import urlencode

    return urlunsplit(
        ("mongodb", f"{userinfo}{sep}{netloc_host}:{port}", parts.path or "/", urlencode(query), "")
    )


def _client(dsn: str, kwargs: dict[str, Any], **extra: Any) -> Any:
    from pymongo import MongoClient

    return MongoClient(dsn, **{**kwargs, **extra})


def _discover_members(
    dsn: str, settings: _Settings, kwargs: dict[str, Any]
) -> list[tuple[str, int]]:
    """Replica-set members the seed hosts advertise that are not seeds themselves.

    Asked with ``directConnection`` so the driver dials nothing but the (already
    checked and pinned) seed while we look. The caller checks and pins every
    member returned before a discovering client is created.
    """
    if not settings.discover_members:
        return []
    seeds = {_host_key(h, p) for h, p in _dsn_hosts(dsn)}
    members: set[tuple[str, int]] = set()
    last_error: Exception | None = None
    answered = False
    for host, port in sorted(seeds):
        client = _client(
            _single_host_uri(dsn, host, port),
            {k: v for k, v in kwargs.items() if k not in ("replicaSet", "directConnection")},
            directConnection=True,
        )
        try:
            hello = client.admin.command("hello")
        except Exception as exc:
            last_error = exc
            continue
        finally:
            client.close()
        answered = True
        for key in ("hosts", "passives", "arbiters"):
            members.update(_parse_member(str(m)) for m in hello.get(key) or [])
        if hello.get("primary"):
            members.add(_parse_member(str(hello["primary"])))
    if not answered and last_error is not None:
        raise last_error
    return sorted(members - seeds)


def _selector(allowed: set[tuple[str, int]]) -> Any:
    """Server selector keeping operations on checked hosts only (defence in depth
    against a member added to the replica set after discovery)."""

    def _select(servers: list[Any]) -> list[Any]:
        return [s for s in servers if _host_key(*s.address) in allowed]

    return _select


@contextlib.asynccontextmanager
async def _connected(settings: _Settings) -> AsyncIterator[tuple[Any, _Settings]]:
    """An open pymongo client whose every reachable host was egress-checked and pinned.

    All driver work runs in worker threads; the client is closed before the pins
    are released (pymongo's monitor threads resolve hosts while it is open).
    """
    try:
        import pymongo  # noqa: F401
    except ImportError as exc:
        raise ConnectorUnavailableError(
            "pymongo is not installed on this server; the MongoDB connector cannot run"
        ) from exc

    async with (
        pin_source_dsn(settings.uri, context=_CONTEXT) as pins,
        contextlib.AsyncExitStack() as stack,
    ):
        kwargs = stack.enter_context(_materialised_tls(settings))
        members = await asyncio.to_thread(_discover_members, pins.dsn, settings, kwargs)
        try:
            await stack.enter_async_context(pin_source_hosts(members, context=_CONTEXT))
        except ConnectorEgressBlockedError as exc:
            names = ", ".join(f"{h}:{p}" for h, p in members)
            raise ConnectorEgressBlockedError(
                f"replica-set member(s) {names} advertised by the server are not allowed "
                f"by the egress policy ({exc}); set direct_connection to use only the "
                "given host"
            ) from exc
        allowed = {_host_key(h, p) for h, p in _dsn_hosts(pins.dsn)} | set(members)
        client = await asyncio.to_thread(
            _client, pins.dsn, kwargs, server_selector=_selector(allowed)
        )
        try:
            yield client, settings
        finally:
            await asyncio.to_thread(client.close)


def _probe(client: Any, settings: _Settings) -> dict[str, Any]:
    client.admin.command("ping", maxTimeMS=settings.max_time_ms)
    meta: dict[str, Any] = {"host": settings.display_host, "database": settings.database}
    if settings.database:
        # Listing needs an authenticated, authorised user — ping does not, so a
        # wrong password used to "validate" fine and fail only on sync.
        names = _list_collections(client, settings)
        missing = [c for c in settings.collections if c not in names]
        if missing:
            raise ValueError(
                f"collection(s) {', '.join(missing)} not found in database {settings.database!r}"
            )
        meta["collections"] = names[:_MAX_COLLECTIONS]
    return meta


def _list_collections(client: Any, settings: _Settings) -> list[str]:
    names = client[settings.database].list_collection_names(
        filter={"type": "collection"}, maxTimeMS=settings.max_time_ms
    )
    return sorted(str(n) for n in names if not str(n).startswith("system."))


def _flatten_doc(doc: dict[str, Any], max_depth: int = 5) -> str:
    """Flatten a MongoDB document to key: value pairs."""
    parts: list[str] = []

    def _recurse(obj: object, prefix: str = "", depth: int = 0) -> None:
        if depth > max_depth:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                _recurse(v, f"{prefix}.{k}" if prefix else k, depth + 1)
        elif isinstance(obj, list | tuple):
            for i, v in enumerate(obj[:20]):
                _recurse(v, f"{prefix}[{i}]", depth + 1)
        else:
            parts.append(f"{prefix}: {obj}")

    _recurse(doc)
    return "\n".join(parts)


# ── Cursor ─────────────────────────────────────────────────────────────────────


def _decode_cursor(
    cursor: str | None, settings: _Settings, collections: list[str]
) -> dict[str, dict[str, Any]]:
    """``{collection: {"value": v, "_id": id}}`` from a stored cursor."""
    if not cursor:
        return {}
    from bson import ObjectId, json_util
    from bson.errors import InvalidId

    text = cursor.strip()
    if text.startswith("{"):
        try:
            data = json_util.loads(text)
        except Exception as exc:
            raise ValueError(f"stored MongoDB cursor is unreadable: {exc}") from exc
        if data.get("field") != settings.cursor_field:
            _log.warning(
                "mongodb_cursor_field_changed old=%s new=%s — full resync",
                data.get("field"),
                settings.cursor_field,
            )
            return {}
        positions = data.get("positions") or {}
        return {str(k): dict(v) for k, v in positions.items() if isinstance(v, dict)}
    # Legacy cursor: the last _id (ObjectId hex) of the single configured collection.
    if settings.cursor_field != "_id" or len(collections) != 1:
        return {}
    try:
        oid: Any = ObjectId(text)
    except (InvalidId, TypeError):
        oid = text
    return {collections[0]: {"value": oid, "_id": oid}}


def _encode_cursor(settings: _Settings, positions: dict[str, dict[str, Any]]) -> str:
    from bson import json_util

    return str(
        json_util.dumps(
            {"v": 2, "field": settings.cursor_field, "positions": positions},
            json_options=json_util.CANONICAL_JSON_OPTIONS,
            sort_keys=True,
        )
    )


def _page_query(field_name: str, position: dict[str, Any] | None) -> dict[str, Any]:
    if field_name == "_id":
        return {"_id": {"$gt": position["_id"]}} if position else {}
    # Documents without the cursor field cannot be ordered by it; they are not
    # ingested under a custom cursor_field (use the default _id cursor for them).
    base: dict[str, Any] = {field_name: {"$exists": True, "$ne": None}}
    if not position:
        return base
    after = {
        "$or": [
            {field_name: {"$gt": position["value"]}},
            {field_name: position["value"], "_id": {"$gt": position["_id"]}},
        ]
    }
    return {"$and": [base, after]}


def _fetch_page(
    client: Any,
    settings: _Settings,
    collection: str,
    position: dict[str, Any] | None,
    limit: int,
) -> list[dict[str, Any]]:
    field_name = settings.cursor_field
    sort = [("_id", 1)] if field_name == "_id" else [(field_name, 1), ("_id", 1)]
    col = client[settings.database][collection]
    return list(
        col.find(
            _page_query(field_name, position),
            sort=sort,
            limit=limit,
            max_time_ms=settings.max_time_ms,
        )
    )


def _dotted_get(doc: dict[str, Any], path: str) -> Any:
    value: Any = doc
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


@register("mongodb", feature_flag="ingestion_connector_mongodb_enabled")
class MongoDBConnector(BaseConnector):
    """MongoDB connector — collection-based incremental ingestion."""

    source_type = "mongodb"

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        t0 = time.perf_counter()
        try:
            settings = _settings(config.connection_config)
            async with _connected(settings) as (client, _s):
                meta = await asyncio.to_thread(_probe, client, settings)
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(ok=True, latency_ms=latency, metadata=meta)
        except Exception as exc:
            return ConnectionHealth(ok=False, error=str(exc) or type(exc).__name__)

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        from app.ingestion.source_config import RawDocument

        settings = _settings(config.connection_config)
        async with _connected(settings) as (client, _s):
            collections = settings.collections or await asyncio.to_thread(
                _list_collections, client, settings
            )
            positions = _decode_cursor(cursor, settings, collections)
            remaining = settings.max_documents
            for collection in collections:
                while remaining > 0:
                    limit = min(settings.batch_size, remaining)
                    page = await asyncio.to_thread(
                        _fetch_page, client, settings, collection, positions.get(collection), limit
                    )
                    for doc in page:
                        oid = doc.get("_id")
                        value = oid if settings.cursor_field == "_id" else _dotted_get(
                            doc, settings.cursor_field
                        )
                        positions[collection] = {"value": value, "_id": oid}
                        doc_key = str(oid)
                        text = _flatten_doc({**doc, "_id": doc_key})
                        url = f"mongodb://{settings.display_host}/{settings.database}/{collection}"
                        raw_doc = RawDocument(
                            doc_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url}/{doc_key}")),
                            source_id=config.source_id,
                            tenant_id=config.tenant_id,
                            source_url=f"{url}/{quote(doc_key, safe='')}",
                            content=text.encode(),
                            content_type="text/plain",
                            metadata={
                                "database": settings.database,
                                "collection": collection,
                                "_id": doc_key,
                            },
                        )
                        yield raw_doc, _encode_cursor(settings, positions)
                    remaining -= len(page)
                    if len(page) < limit:
                        break
