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
    tls_allow_invalid_certificates  Refused (MDB-07): verification cannot be turned off.
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

Changes (TG-07): with the default ``_id`` cursor, new documents come from the
``_id`` scan and updated / replaced ones from the collection's change stream
(replica sets, Atlas; its resume token is kept in the cursor; a token that fell
off the oplog re-reads the collection). A standalone server has no change
stream: set ``cursor_field`` to an update timestamp to re-read edits. Deleted
documents are removed by upstream-deletion reconciliation (KB-44), which lists
every ``_id``. Document ids are UUID v8; ids of earlier releases (v5) are never
reconciled away.

Cursor: JSON (MongoDB canonical Extended JSON) holding, per collection, the last
``cursor_field`` value and ``_id`` processed (and the change-stream token). A
legacy cursor (a bare ObjectId string) is honoured for the single configured
collection.

Egress: every seed host (and SRV target) must pass the connector egress policy
and is pinned for the connection; replica-set members the server advertises are
checked and pinned before the driver may dial them; a member advertised later is
refused in the driver's socket factory (no socket is ever opened to it), and a
server selector keeps operations on checked members only. URI options that read platform files
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
    ConnectorFetchError,
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


# URI options that set (or unset: 0 = "no timeout") a wait. They are removed
# from the tenant's URI; a positive value may only LOWER the matching bound.
_URI_TIMEOUT_OPTIONS = frozenset(
    {
        "timeoutms",
        "sockettimeoutms",
        "connecttimeoutms",
        "serverselectiontimeoutms",
        "maxtimems",
        "waitqueuetimeoutms",
        "wtimeoutms",
    }
)


def _strip_uri_timeouts(uri: str) -> tuple[str, dict[str, int]]:
    """``uri`` without its timeout options, and the tenant's positive values.

    C1 follow-up: ``?timeoutMS=0`` (client-side operation timeout off) or a
    huge ``socketTimeoutMS`` let a stalled server hang the worker again. The
    options are read the way the driver reads them (``&`` or ``;`` separators,
    percent-encoded names) and dropped; the caller applies a value only when it
    is lower than the operator's bound.
    """
    import re
    from urllib.parse import unquote_plus

    from app.net.mongodb_policy import _query_of

    query = _query_of(uri)
    if not query:
        return uri, {}
    kept: list[str] = []
    found: dict[str, int] = {}
    for item in re.split(r"[&;]", query):
        if not item:
            continue
        key, _, value = item.partition("=")
        name = unquote_plus(key).strip().lower()
        if name not in _URI_TIMEOUT_OPTIONS:
            kept.append(item)
            continue
        try:
            number = int(float(unquote_plus(value).strip()))
        except ValueError:
            continue
        if number > 0:
            found[name] = min(number, found.get(name, number))
    head, _sep, tail = uri.strip().rpartition("?" + query)
    rebuilt = head + (("?" + "&".join(kept)) if kept else "") + tail
    return rebuilt, found


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
    uri, uri_timeouts = _strip_uri_timeouts(_mongo_uri(cc))
    parts = urlsplit(uri)
    if parts.scheme.lower() not in ("mongodb", "mongodb+srv"):
        raise ValueError("MongoDB URI must start with mongodb:// or mongodb+srv://")
    # NF-1 / MDB-07: the shared policy reads the options the way the driver does
    # ('&' or ';'), refuses file-path / proxy / provider-property options,
    # ambient-identity mechanisms and anything that weakens TLS.
    from app.net.mongodb_policy import assert_mongo_connection_allowed, uri_options

    assert_mongo_connection_allowed(uri, cc)
    # One validator (app/net/mongodb_policy): no parallel option / mechanism
    # lists here — this only reads the options the connector acts on.
    options = {k.lower(): v for k, v in uri_options(uri)}

    # C1 / MDB-12: every wait is bounded. Driver kwargs override the same URI
    # options, so ``socketTimeoutMS=0`` in a tenant URI cannot unbound a read;
    # the tenant's ``timeout_ms`` may only lower connect / server selection.
    connect_ms, selection_ms, socket_ms, max_time_ms = _driver_bounds()
    tenant_ms = int(cc.get("timeout_ms") or 0)
    if tenant_ms > 0:
        connect_ms = min(connect_ms, tenant_ms)
        selection_ms = min(selection_ms, tenant_ms)
    # URI timeout options were removed from the URI; a positive one may lower
    # its bound (timeoutMS lowers every bound), never raise or unbound it.
    overall = uri_timeouts.get("timeoutms", 0)

    def _lower(bound: int, option: str) -> int:
        return min(v for v in (bound, uri_timeouts.get(option, 0), overall) if v > 0)

    connect_ms = _lower(connect_ms, "connecttimeoutms")
    selection_ms = _lower(selection_ms, "serverselectiontimeoutms")
    socket_ms = _lower(socket_ms, "sockettimeoutms")
    max_time_ms = _lower(max_time_ms, "maxtimems")
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
    if mechanism:  # allowed by the shared policy above
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
    # MDB-07 / C6: TLS verification can never be weakened (checked above with
    # the rest of the shared connection policy).
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


def _client(
    dsn: str, kwargs: dict[str, Any], *, allowed: set[tuple[str, int]], **extra: Any
) -> Any:
    """A MongoClient that can open sockets to ``allowed`` (checked) hosts only.

    C4 / MDB-11: pymongo's monitor threads dial every member a server lists in
    ``hello`` — including one advertised AFTER discovery, which no egress check
    ever saw (a blind SSRF: TCP connect + hello to any host:port). The member
    guard shared with the MCP builtin (MONGO-MONITOR) refuses, in the driver's
    socket factory, any address not in ``allowed`` before a socket exists; the
    server selector additionally keeps operations off such members.
    """
    from pymongo import MongoClient

    from app.mcp.servers.mongodb_server import _install_member_guard, _MemberGuard

    _install_member_guard()
    options = {**kwargs, **extra}
    options["event_listeners"] = [*options.get("event_listeners", []), _MemberGuard(allowed)]
    return MongoClient(dsn, **options)


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
            allowed={(host, port)},
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
            # MDB-20: the names the SERVER advertised stay in the server log only.
            names = ", ".join(f"{h}:{p}" for h, p in members)
            _log.warning("mongodb_member_refused members=%s error=%s", names, exc)
            raise ConnectorEgressBlockedError(
                f"{len(members)} replica-set member(s) advertised by the server are not "
                "allowed by the egress policy; set direct_connection to use only the given "
                "host"
            ) from exc
        allowed = {_host_key(h, p) for h, p in _dsn_hosts(pins.dsn)} | set(members)
        client = await asyncio.to_thread(
            _client, pins.dsn, kwargs, allowed=allowed, server_selector=_selector(allowed)
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


_MAX_ARRAY_ITEMS = 100  # items rendered per array; the rest is summarised
_MAX_DEEP_JSON_CHARS = 4000  # a subtree below max_depth, as JSON, at most this long


def _scalar(value: object) -> str:
    """A readable rendering of one BSON value (TG-09)."""
    import datetime
    import re

    from bson import Binary, Regex

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Binary):
        # Raw bytes are not text: index what they are, never their content.
        return f"<binary subtype {value.subtype}, {len(value)} bytes>"
    if isinstance(value, bytes | bytearray):
        return f"<binary {len(value)} bytes>"
    if isinstance(value, Regex):
        return f"/{value.pattern}/{_regex_flags(value.flags)}"
    if isinstance(value, re.Pattern):
        return f"/{value.pattern}/{_regex_flags(value.flags)}"
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)  # Decimal128, Int64, ObjectId, UUID, Timestamp, numbers, str


def _regex_flags(flags: object) -> str:
    import re

    if isinstance(flags, str):
        return flags
    out = ""
    for flag, letter in ((re.IGNORECASE, "i"), (re.MULTILINE, "m"), (re.DOTALL, "s"),
                         (re.VERBOSE, "x")):
        if int(flags) & flag:  # type: ignore[call-overload]
            out += letter
    return out


def _flatten(doc: dict[str, Any], max_depth: int = 5) -> tuple[str, dict[str, int]]:
    """Flatten a MongoDB document to ``key: value`` lines, and what was shortened.

    TG-09: nothing is dropped silently. Arrays render their first
    ``_MAX_ARRAY_ITEMS`` items and a marker line saying how many more there
    are; a subtree nested deeper than ``max_depth`` is kept as (bounded)
    Extended JSON. The returned counts go on the document's metadata.
    """
    from bson import json_util

    parts: list[str] = []
    truncation: dict[str, int] = {}

    def _note(key: str, amount: int = 1) -> None:
        truncation[key] = truncation.get(key, 0) + amount

    def _recurse(obj: object, prefix: str = "", depth: int = 0) -> None:
        if depth > max_depth and isinstance(obj, dict | list | tuple):
            dumped = json_util.dumps(obj, json_options=json_util.RELAXED_JSON_OPTIONS)
            if len(dumped) > _MAX_DEEP_JSON_CHARS:
                dumped = dumped[:_MAX_DEEP_JSON_CHARS] + " … (truncated)"
                _note("deep_chars_truncated")
            parts.append(f"{prefix}: [nested deeper than {max_depth} levels, as JSON] {dumped}")
            _note("deep_fields")
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                _recurse(v, f"{prefix}.{k}" if prefix else str(k), depth + 1)
        elif isinstance(obj, list | tuple):
            for i, v in enumerate(obj[:_MAX_ARRAY_ITEMS]):
                _recurse(v, f"{prefix}[{i}]", depth + 1)
            if len(obj) > _MAX_ARRAY_ITEMS:
                omitted = len(obj) - _MAX_ARRAY_ITEMS
                parts.append(f"{prefix}: … {omitted} more item(s) of {len(obj)} not indexed")
                _note("arrays_truncated")
                _note("array_items_omitted", omitted)
        else:
            parts.append(f"{prefix}: {_scalar(obj)}")

    _recurse(doc)
    return "\n".join(parts), truncation


def _flatten_doc(doc: dict[str, Any], max_depth: int = 5) -> str:
    """Flatten a MongoDB document to ``key: value`` lines (see :func:`_flatten`)."""
    return _flatten(doc, max_depth)[0]


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
    if position is not None and "_id" not in position:
        position = None  # only a change-stream token so far: read from the start
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


def _doc_id(config: SourceConfig, collection: str, oid: Any) -> str:
    """Stable id: the Source + collection + the document's ``_id`` (TG-13).

    It used to hash the URI host, so pointing the Source at the same server by
    another name (a new replica-set seed, an Atlas SRV name, an IP) re-ided —
    duplicated — every document. ``_id`` is keyed in canonical Extended JSON,
    so an ObjectId and the string of its hex are different documents.

    The id is a UUID *version 8* (same key derivation as ``stable_doc_id``,
    SHA-256 based): the host-based ids of earlier releases were version 5, and
    :meth:`MongoDBConnector.manages_doc_id` must tell them apart — such a
    document's new-id copy is never indexed (the content-hash dedup skips it),
    so reconciling it away would lose it.
    """
    import hashlib

    from bson import json_util

    key = json_util.dumps({"_id": oid}, json_options=json_util.CANONICAL_JSON_OPTIONS)
    digest = hashlib.sha256(
        f"agentverse-source:{config.source_id}:mongodb\x1f{collection}\x1f{key}".encode()
    ).digest()
    value = int.from_bytes(digest[:16], "big")
    value = (value & ~(0xF << 76)) | (8 << 76)  # version 8
    value = (value & ~(0x3 << 62)) | (0x2 << 62)  # RFC 4122 variant
    return str(uuid.UUID(int=value))


def _raw_document(
    config: SourceConfig, settings: _Settings, collection: str, doc: dict[str, Any]
) -> RawDocument:
    """The RawDocument a sync indexes for one MongoDB document (current v8 id)."""
    from app.ingestion.source_config import RawDocument

    oid = doc.get("_id")
    doc_key = str(oid)
    text, truncation = _flatten({**doc, "_id": doc_key})
    url = f"mongodb://{settings.display_host}/{settings.database}/{collection}"
    metadata: dict[str, Any] = {
        "database": settings.database,
        "collection": collection,
        "_id": doc_key,
    }
    if truncation:
        metadata["truncated"] = truncation  # TG-09: never silent
    return RawDocument(
        doc_id=_doc_id(config, collection, oid),
        source_id=config.source_id,
        tenant_id=config.tenant_id,
        source_url=f"{url}/{quote(doc_key, safe='')}",
        content=text.encode(),
        content_type="text/plain",
        metadata=metadata,
    )


def _legacy_key_candidates(key: str) -> list[Any]:
    """The ``_id`` values whose ``str()`` is ``key`` (pre-v8 ids keyed ``str(_id)``).

    Pre-v8 releases keyed a document by the string of its ``_id``, which drops
    the type: ``"64f0..."`` may have been an ObjectId or that string, ``"42"``
    an int or that string. Every candidate is looked up; a match is accepted
    only if ``str(_id)`` equals ``key`` again.
    """
    from bson import ObjectId

    out: list[Any] = [key]
    if ObjectId.is_valid(key) and len(key) == 24:
        out.append(ObjectId(key))
    if key.lstrip("-").isdigit() and len(key) <= 19:
        out.append(int(key))
    return out


def _read_by_legacy_keys(
    client: Any, settings: _Settings, collection: str, keys: list[str]
) -> list[dict[str, Any]]:
    col = client[settings.database][collection]
    values: list[Any] = [v for key in keys for v in _legacy_key_candidates(key)]
    wanted = set(keys)
    return [
        doc
        for doc in col.find(
            {"_id": {"$in": values}}, max_time_ms=settings.max_time_ms, limit=len(values)
        )
        if str(doc.get("_id")) in wanted
    ]


# Change streams (TG-07): server error codes meaning "not available here".
_NO_CHANGE_STREAM_CODES = frozenset({40573, 40324, 13, 115})
# ... and "the resume token fell off the oplog: re-read the collection".
_HISTORY_LOST_CODES = frozenset({286, 280, 136})
_CHANGE_TYPES = ["update", "replace"]


def _change_stream_start(client: Any, settings: _Settings, collection: str) -> Any:
    """A resume token for "now" on ``collection``, or None without change streams.

    Taken BEFORE the collection is scanned, so an update made during or after
    the scan is read from the stream by a later sync.
    """
    from pymongo.errors import OperationFailure

    col = client[settings.database][collection]
    try:
        with col.watch(
            [{"$match": {"operationType": {"$in": _CHANGE_TYPES}}}], max_await_time_ms=50
        ) as stream:
            stream.try_next()
            return stream.resume_token
    except OperationFailure as exc:
        if exc.code in _NO_CHANGE_STREAM_CODES or "replica set" in str(exc).lower():
            _log.warning(
                "mongodb_change_stream_unavailable collection=%s: %s — updates are re-read "
                "only with a cursor_field on an update timestamp",
                collection,
                exc,
            )
            return None
        raise


def _read_changes(
    client: Any, settings: _Settings, collection: str, token: Any, limit: int
) -> tuple[list[tuple[dict[str, Any], Any]], Any, bool]:
    """Updated / replaced documents since ``token``: ([(doc, token_after)], token, lost)."""
    from pymongo.errors import OperationFailure

    col = client[settings.database][collection]
    changes: list[tuple[dict[str, Any], Any]] = []
    try:
        with col.watch(
            [{"$match": {"operationType": {"$in": _CHANGE_TYPES}}}],
            full_document="updateLookup",
            resume_after=token,
            max_await_time_ms=50,
        ) as stream:
            # Bounded by events READ (not only documents kept): events whose
            # document is gone since still count, so the loop always ends.
            for _ in range(limit):
                change = stream.try_next()
                if change is None:
                    break
                doc = change.get("fullDocument")
                if isinstance(doc, dict):  # None: deleted since (reconcile removes it)
                    changes.append((doc, stream.resume_token))
            return changes, stream.resume_token, False
    except OperationFailure as exc:
        if exc.code in _HISTORY_LOST_CODES or exc.has_error_label(
            "NonResumableChangeStreamError"
        ):
            return [], None, True
        raise


def _live_id_page(
    client: Any, settings: _Settings, collection: str, after: Any, limit: int
) -> list[Any]:
    col = client[settings.database][collection]
    query = {"_id": {"$gt": after}} if after is not None else {}
    return [
        d["_id"]
        for d in col.find(
            query, {"_id": 1}, sort=[("_id", 1)], limit=limit, max_time_ms=settings.max_time_ms
        )
    ]


def _dotted_get(doc: dict[str, Any], path: str) -> Any:
    value: Any = doc
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _is_own_error(exc: BaseException) -> bool:
    """Our own policy / egress / availability errors (never driver text)."""
    try:
        from pymongo.errors import PyMongoError
    except ImportError:
        return True  # no driver: nothing of its can be in the error
    if isinstance(exc, PyMongoError):
        return False
    return isinstance(
        exc,
        ValueError | ConnectorEgressBlockedError | ConnectorUnavailableError | ConnectorFetchError,
    )


def _public_error(exc: BaseException) -> str:
    """MDB-20: our own policy / egress messages as-is; driver text never."""
    if _is_own_error(exc):
        return str(exc) or type(exc).__name__
    from app.net.mongodb_errors import public_mongo_error

    return public_mongo_error(exc, context="ingestion mongodb")


@register("mongodb", feature_flag="ingestion_connector_mongodb_enabled")
class MongoDBConnector(BaseConnector):
    """MongoDB connector — collection-based incremental ingestion."""

    source_type = "mongodb"

    @classmethod
    def check_connection_policy(cls, connection_config: dict[str, Any]) -> None:
        """P1c-1: the shared MongoDB policy (NF-1 / MDB-07) when a Source is saved —
        URI options read the way the driver reads them ('&', ';', percent-encoded),
        TLS weakening, platform files, proxies and ambient-identity mechanisms are
        refused before the Source exists. A config without a URI / host yet is the
        Source's configuration status, not a policy violation."""
        cc = dict(connection_config or {})
        if not (cc.get("uri") or cc.get("connection_string") or cc.get("host")):
            return
        _settings(cc, require_database=False)

    def manages_doc_id(self, doc_id: str) -> bool:
        """Only this connector's current (UUID v8) ids are deletion candidates."""
        try:
            return uuid.UUID(str(doc_id)).version == 8
        except ValueError:
            return False

    async def iter_live_doc_ids(self, config: SourceConfig) -> AsyncIterator[str]:
        """Every document id upstream (TG-07 / KB-44 deletes), keyset-paged by _id.

        Only ``_id`` is read, 1000 per query; any error propagates, so a partial
        listing never looks complete (nothing is deleted then).
        """
        settings = _settings(config.connection_config)
        async with _connected(settings) as (client, _s):
            collections = settings.collections or await asyncio.to_thread(
                _list_collections, client, settings
            )
            for collection in collections:
                after: Any = None
                while True:
                    page = await asyncio.to_thread(
                        _live_id_page, client, settings, collection, after, 1000
                    )
                    for oid in page:
                        yield _doc_id(config, collection, oid)
                    if len(page) < 1000:
                        break
                    after = page[-1]

    def legacy_scope(self, config: SourceConfig) -> tuple[str, list[str]]:
        """(database, configured collections — empty = all) a legacy id may come from."""
        settings = _settings(config.connection_config)
        return settings.database, list(settings.collections)

    async def read_legacy_documents(
        self, config: SourceConfig, refs: list[tuple[str, str]]
    ) -> dict[tuple[str, str], list[RawDocument]]:
        """D2: re-read documents indexed under pre-v8 ids, as the sync would index them now.

        ``refs`` are ``(collection, str(_id))`` pairs parsed from the legacy
        documents; the result maps each to the upstream documents it names
        (normally one; none when it was deleted upstream), each under its
        current v8 id. One bounded ``$in`` query per collection. Any error is
        raised (sanitised, MDB-20) so the caller never deletes on a failed read.
        """
        if not refs:
            return {}
        by_collection: dict[str, list[str]] = {}
        for collection, key in refs:
            by_collection.setdefault(collection, []).append(key)
        out: dict[tuple[str, str], list[RawDocument]] = {ref: [] for ref in refs}
        try:
            settings = _settings(config.connection_config)
            async with _connected(settings) as (client, _s):
                for collection, keys in by_collection.items():
                    docs = await asyncio.to_thread(
                        _read_by_legacy_keys, client, settings, collection, keys
                    )
                    for doc in docs:
                        key = str(doc.get("_id"))
                        out[(collection, key)].append(
                            _raw_document(config, settings, collection, doc)
                        )
        except Exception as exc:
            if _is_own_error(exc):
                raise
            raise ConnectorFetchError(f"mongodb: {_public_error(exc)}") from exc
        return out

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
            return ConnectionHealth(ok=False, error=_public_error(exc))

    async def health_check(self, config: SourceConfig) -> ConnectionHealth:
        """C8: a ping only (no collection listing) — polled by every open UI.

        With credentials the driver authenticates while opening the connection,
        so a wrong password still fails here; missing collections are reported by
        :meth:`validate_connection` (Test connection) and by the sync.
        """
        import time

        t0 = time.perf_counter()
        try:
            settings = _settings(config.connection_config)
            async with _connected(settings) as (client, _s):
                await asyncio.to_thread(
                    client.admin.command, "ping", maxTimeMS=settings.max_time_ms
                )
            latency = (time.perf_counter() - t0) * 1000
            return ConnectionHealth(
                ok=True,
                latency_ms=latency,
                metadata={"host": settings.display_host, "database": settings.database},
            )
        except Exception as exc:
            return ConnectionHealth(ok=False, error=_public_error(exc))

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """The sync. A failure is raised as :class:`ConnectorFetchError` with the
        sanitised message (MDB-20): the job shows a classified reason and an error
        id, never pymongo's topology / host text (logged under that id)."""
        try:
            async for item in self._delta(config, cursor):
                yield item
        except Exception as exc:
            if _is_own_error(exc):
                raise  # policy / egress / unavailable: already honest, keep the type
            raise ConnectorFetchError(f"mongodb: {_public_error(exc)}") from exc

    async def _delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        settings = _settings(config.connection_config)
        async with _connected(settings) as (client, _s):
            collections = settings.collections or await asyncio.to_thread(
                _list_collections, client, settings
            )
            positions = _decode_cursor(cursor, settings, collections)
            remaining = settings.max_documents
            # TG-07: with the default _id cursor new documents come from the _id
            # scan and updated / replaced ones from the collection's change
            # stream (replica sets / Atlas); deletions are reconciled (KB-44).
            track_changes = settings.cursor_field == "_id"

            def _raw(collection: str, doc: dict[str, Any]) -> RawDocument:
                return _raw_document(config, settings, collection, doc)

            for collection in collections:
                # A second pass only when the change stream's history was lost.
                for _pass in range(2):
                    if track_changes and not (positions.get(collection) or {}).get("cs"):
                        token = await asyncio.to_thread(
                            _change_stream_start, client, settings, collection
                        )
                        if token is not None:
                            positions[collection] = {**positions.get(collection, {}), "cs": token}
                    while remaining > 0:
                        limit = min(settings.batch_size, remaining)
                        page = await asyncio.to_thread(
                            _fetch_page,
                            client,
                            settings,
                            collection,
                            positions.get(collection),
                            limit,
                        )
                        for doc in page:
                            oid = doc.get("_id")
                            value = oid if settings.cursor_field == "_id" else _dotted_get(
                                doc, settings.cursor_field
                            )
                            positions[collection] = {
                                **positions.get(collection, {}),
                                "value": value,
                                "_id": oid,
                            }
                            yield _raw(collection, doc), _encode_cursor(settings, positions)
                        remaining -= len(page)
                        if len(page) < limit:
                            break
                    token = (positions.get(collection) or {}).get("cs")
                    if not track_changes or token is None or remaining <= 0:
                        break
                    changes, next_token, lost = await asyncio.to_thread(
                        _read_changes, client, settings, collection, token, remaining
                    )
                    if lost:
                        # The resume point fell off the oplog: updates in between
                        # are unknown, so the collection is read again from the
                        # start (unchanged documents are skipped by the dedup).
                        _log.warning(
                            "mongodb_change_stream_history_lost source=%s collection=%s — "
                            "re-reading the collection",
                            config.source_id,
                            collection,
                        )
                        positions[collection] = {}
                        continue
                    for doc, after in changes:
                        positions[collection] = {**positions[collection], "cs": after}
                        remaining -= 1
                        yield _raw(collection, doc), _encode_cursor(settings, positions)
                    positions[collection] = {**positions[collection], "cs": next_token}
                    break
