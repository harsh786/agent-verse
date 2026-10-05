"""MongoDB MCP server — interact with a tenant's MongoDB database.

Credentials come ONLY from the calling connector (``credentials``): the
connection URI (``uri`` / ``connection_string`` / ``url``), optional
``username`` / ``password`` / ``auth_source`` / ``auth_mechanism`` overrides,
TLS options (``tls``, ``tls_ca_pem``; nothing that weakens verification — MDB-07) and a
default ``database``. There is deliberately no platform-env fallback: the old
``MONGODB_MCP_URL`` lookup ran every tenant's calls against the PLATFORM's
database (confused deputy).

Egress: every host the URI names (each replica-set seed, or each SRV target of
a ``mongodb+srv`` URI, expanded so the driver runs no SRV query of its own) is
resolved, checked against the connector egress policy and pinned for the call
(``pin_source_dsn``). The driver only dials those checked hosts: a single-host
URI connects directly (no member discovery); for a replica-set URI, operations
select only the members it lists and the driver opens NO socket (monitoring or
pool) to a member the server advertises but the tenant did not list
(``_MemberGuard``) — so list every member (or use SRV).

URI options that would read platform files (``tlsCAFile`` ...), route through a
proxy (``proxyHost`` ...) or authenticate as the PLATFORM's ambient identity
(``MONGODB-AWS``, ``GSSAPI``, ``MONGODB-OIDC``) are refused. TLS material comes
from the connector as PEM text: ``tls_ca_pem`` and, for mutual TLS or
``MONGODB-X509``, ``tls_client_cert`` + ``tls_client_private_key`` (MDB-05).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from app.observability.logging import get_logger

logger = get_logger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "mongodb_find",
        "description": "Find documents in a MongoDB collection with optional query and projection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Collection name"},
                "database": {
                    "type": "string",
                    "description": "Database name (overrides URI default)",
                },
                "query": {"type": "object", "description": "MongoDB query filter", "default": {}},
                "projection": {"type": "object", "description": "Fields to include/exclude"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["collection"],
        },
    },
    {
        "name": "mongodb_find_one",
        "description": "Find a single document in a MongoDB collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "query": {"type": "object", "default": {}},
                "projection": {"type": "object"},
            },
            "required": ["collection"],
        },
    },
    {
        "name": "mongodb_insert_one",
        "description": "Insert a single document into a MongoDB collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "document": {"type": "object", "description": "Document to insert"},
            },
            "required": ["collection", "document"],
        },
    },
    {
        "name": "mongodb_update_one",
        "description": "Update a single document in a MongoDB collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "filter": {"type": "object", "description": "Filter to match the document"},
                "update": {"type": "object", "description": "Fields to set (passed as $set)"},
                "upsert": {"type": "boolean", "default": False},
            },
            "required": ["collection", "filter", "update"],
        },
    },
    {
        "name": "mongodb_delete_one",
        "description": "Delete a single document from a MongoDB collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "filter": {"type": "object", "description": "Filter to match the document"},
            },
            "required": ["collection", "filter"],
        },
    },
    {
        "name": "mongodb_aggregate",
        "description": "Run an aggregation pipeline on a MongoDB collection",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "pipeline": {
                    "type": "array",
                    "description": "Aggregation pipeline stages",
                    "items": {"type": "object"},
                },
            },
            "required": ["collection", "pipeline"],
        },
    },
    {
        "name": "mongodb_list_collections",
        "description": "List all collections in a MongoDB database",
        "parameters": {
            "type": "object",
            "properties": {
                "database": {"type": "string"},
            },
        },
    },
    {
        "name": "mongodb_count",
        "description": "Count documents in a MongoDB collection matching a filter",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "database": {"type": "string"},
                "query": {"type": "object", "default": {}},
            },
            "required": ["collection"],
        },
    },
]


_URI_KEYS = ("uri", "connection_string", "url", "base_url", "mongodb_uri")
_URI_SCHEMES = ("mongodb://", "mongodb+srv://")

# URI options a tenant may not set (lower-cased): platform file reads, proxy
# routing around the egress pinning, and ambient-identity auth settings.
_BLOCKED_URI_OPTIONS = frozenset(
    {
        "tlscafile",
        "tlscertificatekeyfile",
        "tlscertificatekeyfilepassword",
        "tlscrlfile",
        "proxyhost",
        "proxyport",
        "proxyusername",
        "proxypassword",
        "authmechanismproperties",
        "srvservicename",
    }
)
# SCRAM / PLAIN (LDAP) authenticate with the credentials the tenant supplies.
# MONGODB-AWS / GSSAPI / MONGODB-OIDC fall back to the platform's ambient
# identity (env keys, instance metadata, keytab). MONGODB-X509 is allowed only
# with the tenant's OWN client certificate (tls_client_cert, MDB-05) — never a
# certificate file on the platform's disk (tlsCertificateKeyFile is refused).
_ALLOWED_AUTH_MECHANISMS = frozenset({"DEFAULT", "SCRAM-SHA-1", "SCRAM-SHA-256", "PLAIN"})
_X509 = "MONGODB-X509"

_TRUE = frozenset({"1", "true", "yes", "on"})

CONFIGURE_CREDENTIALS_ERROR = (
    "MongoDB connector has no connection URI configured. Configure credentials on the "
    "connector (a mongodb:// or mongodb+srv:// connection string); platform credentials "
    "are never used."
)


class MongoCredentialError(ValueError):
    """The connector's MongoDB credentials are missing or not allowed."""


class MongoArgumentError(ValueError):
    """A tool argument is malformed (refused before any connection)."""


def _bounds() -> tuple[int, int, int]:
    """(default find limit, max documents, operation timeout ms) from Settings."""
    from app.core.config import get_settings

    settings = get_settings()
    max_docs = max(1, int(settings.mongodb_tool_max_documents))
    default = min(max(1, int(settings.mongodb_tool_default_limit)), max_docs)
    return default, max_docs, max(100, int(settings.mongodb_tool_timeout_ms))


def _find_limit(arguments: dict[str, Any]) -> int:
    """The find limit, clamped: none -> default; 0 / negative / above max -> max.

    MongoDB treats ``limit 0`` as "no limit" (the whole collection) and a negative
    limit as a single batch of ``abs(n)`` — neither may escape the maximum.
    """
    default, max_docs, _ = _bounds()
    raw = arguments.get("limit")
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int | str):
        raise MongoArgumentError("limit must be an integer")
    try:
        limit = int(raw)
    except ValueError as exc:
        raise MongoArgumentError("limit must be an integer") from exc
    return max_docs if limit <= 0 or limit > max_docs else limit


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in _TRUE


def _connection_uri(credentials: dict[str, Any] | None) -> str:
    creds = credentials or {}
    for key in _URI_KEYS:
        value = creds.get(key)
        if isinstance(value, str) and value.strip() and not value.startswith("builtin://"):
            uri = value.strip()
            if not uri.lower().startswith(_URI_SCHEMES):
                raise MongoCredentialError(
                    "MongoDB connection URI must start with mongodb:// or mongodb+srv://"
                )
            return uri
    raise MongoCredentialError(CONFIGURE_CREDENTIALS_ERROR)


def _has_client_cert(credentials: dict[str, Any] | None) -> bool:
    return bool(str((credentials or {}).get("tls_client_cert") or "").strip())


def _check_auth_mechanism(value: str, credentials: dict[str, Any] | None = None) -> None:
    mechanism = value.strip().upper()
    if mechanism == _X509:
        if not _has_client_cert(credentials):
            raise MongoCredentialError(
                "MONGODB-X509 authentication needs the connector's tls_client_cert and "
                "tls_client_private_key"
            )
        return
    if mechanism not in _ALLOWED_AUTH_MECHANISMS:
        raise MongoCredentialError(
            f"MongoDB auth mechanism '{value}' is not allowed; use SCRAM, PLAIN or "
            "MONGODB-X509 with credentials supplied on the connector"
        )


def _check_uri_options(uri: str, credentials: dict[str, Any] | None = None) -> None:
    """Refuse URI options that reach outside the tenant's own database."""
    for key, value in parse_qsl(urlsplit(uri).query, keep_blank_values=True):
        lowered = key.strip().lower()
        if lowered in _BLOCKED_URI_OPTIONS:
            raise MongoCredentialError(f"MongoDB URI option '{key}' is not allowed")
        if lowered == "authmechanism":
            _check_auth_mechanism(value, credentials)


def _client_kwargs(credentials: dict[str, Any] | None) -> dict[str, Any]:
    """Driver options from the connector's credential fields (never from env)."""
    creds = credentials or {}
    kwargs: dict[str, Any] = {
        "serverSelectionTimeoutMS": 5000,
        "connectTimeoutMS": 5000,
        "socketTimeoutMS": 30000,
    }
    if creds.get("username"):
        kwargs["username"] = str(creds["username"])
    if creds.get("password"):
        kwargs["password"] = str(creds["password"])
    auth_source = creds.get("auth_source") or creds.get("authSource")
    if auth_source:
        kwargs["authSource"] = str(auth_source)
    mechanism = creds.get("auth_mechanism") or creds.get("authMechanism")
    if mechanism:
        _check_auth_mechanism(str(mechanism), creds)
        kwargs["authMechanism"] = str(mechanism).strip().upper()
        if kwargs["authMechanism"] == _X509:
            # The identity is the certificate subject; a password makes no sense.
            kwargs.pop("password", None)
            kwargs.setdefault("authSource", "$external")
    if str(creds.get("tls", "")).strip() != "":
        kwargs["tls"] = _truthy(creds["tls"])
    # MDB-07: no certificate-verification opt-out exists (assert_tls_not_weakened).
    return kwargs


def _pem(credentials: dict[str, Any] | None, key: str) -> str:
    return str((credentials or {}).get(key) or "").strip()


def _tls_files(credentials: dict[str, Any] | None) -> tuple[dict[str, Any], Callable[[], None]]:
    """Driver TLS kwargs with tenant-supplied PEM material in private temp files.

    pymongo only takes certificate PATHS; the connector holds PEM text: the CA
    bundle (``tls_ca_pem``) and, for mutual TLS / MONGODB-X509, the client
    certificate + private key (``tls_client_cert`` / ``tls_client_private_key``,
    optional ``tls_client_key_password``). They are written to a 0700 directory
    that lives as long as the (pooled) client; ``cleanup()`` removes it.
    """
    ca = _pem(credentials, "tls_ca_pem")
    cert = _pem(credentials, "tls_client_cert")
    key = _pem(credentials, "tls_client_private_key")
    if ca and "-----BEGIN CERTIFICATE-----" not in ca:
        raise MongoCredentialError("tls_ca_pem must be a PEM-encoded certificate bundle")
    if bool(cert) != bool(key):
        raise MongoCredentialError("tls_client_cert and tls_client_private_key go together")
    if cert and "-----BEGIN CERTIFICATE-----" not in cert:
        raise MongoCredentialError("tls_client_cert must be a PEM-encoded certificate")
    if key and "PRIVATE KEY-----" not in key:
        raise MongoCredentialError("tls_client_private_key must be a PEM-encoded private key")
    if not ca and not cert:
        return {}, lambda: None
    tmpdir = tempfile.mkdtemp(prefix="av-mcp-mongo-tls-")

    def _cleanup() -> None:
        shutil.rmtree(tmpdir, ignore_errors=True)

    try:
        kwargs: dict[str, Any] = {"tls": True}
        if ca:
            path = os.path.join(tmpdir, "ca.pem")
            with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), "w") as handle:
                handle.write(ca + "\n")
            kwargs["tlsCAFile"] = path
        if cert:
            path = os.path.join(tmpdir, "client.pem")
            with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), "w") as handle:
                handle.write(f"{cert}\n{key}\n")
            kwargs["tlsCertificateKeyFile"] = path
            password = _pem(credentials, "tls_client_key_password")
            if password:
                kwargs["tlsCertificateKeyFilePassword"] = password
    except BaseException:
        _cleanup()
        raise
    return kwargs, _cleanup


def _db_name(uri: str, arguments: dict[str, Any], credentials: dict[str, Any] | None) -> str:
    """The call's ``database`` argument, else the connector default, else the URI path."""
    if db := arguments.get("database"):
        return str(db)
    if db := (credentials or {}).get("database"):
        return str(db)
    return urlsplit(uri).path.lstrip("/") or "test"


def get_tools() -> list[dict[str, Any]]:
    try:
        import pymongo  # noqa: F401
    except ImportError:
        return [
            {
                "name": "unavailable",
                "description": "pymongo not installed. Run: pip install pymongo",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
    return TOOL_DEFINITIONS


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    credentials: dict[str, Any] | None = None,
    tenant_ctx: Any = None,
    server_id: str = "",
) -> dict[str, Any]:
    from app.net.mongodb_policy import (
        MongoOperatorError,
        MongoTlsPolicyError,
        assert_safe_mongo_arguments,
        assert_tls_not_weakened,
    )

    try:
        # MDB-03: write stages / server-side JavaScript anywhere in the call are
        # refused before any connection exists.
        assert_safe_mongo_arguments(arguments or {})
    except MongoOperatorError as exc:
        return {"error": str(exc), "tool": tool_name, "status": "operator_refused"}
    try:
        uri = _connection_uri(credentials)
        _check_uri_options(uri, credentials)
        assert_tls_not_weakened(uri, credentials)
        kwargs = _client_kwargs(credentials)
    except MongoTlsPolicyError as exc:
        return {"error": str(exc), "status": "tls_refused"}
    except MongoCredentialError as exc:
        return {"error": str(exc), "status": "credentials_required"}
    try:
        import pymongo  # noqa: F401
    except ImportError:
        return {
            "error": "pymongo not installed. Run: pip install pymongo",
            "tool": tool_name,
            "status": "dependency_missing",
        }

    from app.mcp import mongodb_clients

    tenant_id = str(getattr(tenant_ctx, "tenant_id", "") or "")
    key = (tenant_id, str(server_id or ""), mongodb_clients.fingerprint(uri, credentials))
    try:
        entry = mongodb_clients.acquire(key)
        if entry is None:
            entry = await _open_client(key, uri, kwargs, credentials)
        try:
            return await asyncio.to_thread(_call_sync, entry, tool_name, arguments, credentials)
        except Exception as exc:
            from pymongo.errors import ConfigurationError, ConnectionFailure

            if isinstance(exc, ConnectionFailure | ConfigurationError):
                mongodb_clients.discard(entry)  # rebuilt (and re-checked) next call
            raise
        finally:
            mongodb_clients.release(entry)
    except MongoTlsPolicyError as exc:
        return {"error": str(exc), "status": "tls_refused"}
    except MongoCredentialError as exc:
        return {"error": str(exc), "status": "credentials_required"}
    except MongoArgumentError as exc:
        return {"error": str(exc), "tool": tool_name, "status": "invalid_arguments"}
    except Exception as exc:
        logger.warning("mongodb_call_tool_error tool=%s error=%s", tool_name, str(exc)[:200])
        return {"error": str(exc)}


async def _open_client(
    key: tuple[str, str, str],
    uri: str,
    kwargs: dict[str, Any],
    credentials: dict[str, Any] | None,
) -> Any:
    """Build a pooled client: hosts checked + pinned for ITS lifetime, TLS files kept."""
    from app.ingestion import connector_egress
    from app.mcp import mongodb_clients
    from app.net.mongodb_policy import assert_tls_not_weakened

    # Every URI host (SRV targets expanded) is checked; the pins live as long as
    # the client (its monitor threads resolve those names while it is open).
    pins, release_pins = await asyncio.to_thread(
        connector_egress.hold_source_dsn_pins, uri, context="mcp builtin mongodb"
    )

    def _no_tls_files() -> None:
        return None

    cleanup_tls: Callable[[], None] = _no_tls_files
    try:
        _check_uri_options(pins.dsn, credentials)  # incl. SRV TXT-record options
        assert_tls_not_weakened(pins.dsn, credentials)
        tls_kwargs, cleanup_tls = _tls_files(credentials)
        client = await asyncio.to_thread(_build_client, pins.dsn, {**kwargs, **tls_kwargs})
    except BaseException:
        cleanup_tls()
        release_pins()
        raise
    try:
        await asyncio.to_thread(_first_contact, client, kwargs)
    except BaseException:
        client.close()
        cleanup_tls()
        release_pins()
        raise

    def _closer() -> None:
        cleanup_tls()
        release_pins()

    return mongodb_clients.insert(key, client, _closer, dsn=pins.dsn)


def _serialize(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for d in docs:
        if "_id" in d:
            d["_id"] = str(d["_id"])
    return docs


def _run_tool(
    db: Any, db_name: str, coll_name: str, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Run one tool under the per-call operation timeout (MDB-08).

    ``pymongo.timeout`` (client-side operation timeout) bounds the whole call —
    server selection, retries, every round trip — and makes the driver send
    ``maxTimeMS`` with every command, writes included.
    """
    import pymongo

    _, _, timeout_ms = _bounds()
    with pymongo.timeout(timeout_ms / 1000.0):
        return _run_tool_bounded(db, db_name, coll_name, tool_name, arguments)


def _run_tool_bounded(
    db: Any, db_name: str, coll_name: str, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    coll = db[coll_name]
    if tool_name == "mongodb_find":
        limit = _find_limit(arguments)
        cursor = coll.find(
            arguments.get("query") or {},
            arguments.get("projection"),
            limit=limit,
            batch_size=min(limit, 500),
        )
        try:
            docs = list(cursor)
        finally:
            cursor.close()
        return {"documents": _serialize(docs), "count": len(docs), "limit": limit}
    if tool_name == "mongodb_find_one":
        doc = coll.find_one(arguments.get("query") or {}, arguments.get("projection"))
        if doc:
            doc["_id"] = str(doc["_id"])
        return {"document": doc}
    if tool_name == "mongodb_insert_one":
        inserted = coll.insert_one(arguments["document"])
        return {"inserted_id": str(inserted.inserted_id), "success": True}
    if tool_name == "mongodb_update_one":
        updated = coll.update_one(
            arguments["filter"],
            {"$set": arguments["update"]},
            upsert=arguments.get("upsert", False),
        )
        return {
            "matched": updated.matched_count,
            "modified": updated.modified_count,
            "upserted_id": str(updated.upserted_id) if updated.upserted_id else None,
        }
    if tool_name == "mongodb_delete_one":
        deleted = coll.delete_one(arguments["filter"])
        return {"deleted": deleted.deleted_count}
    if tool_name == "mongodb_aggregate":
        return _aggregate(coll, arguments)
    if tool_name == "mongodb_list_collections":
        return {"collections": db.list_collection_names(), "database": db_name}
    if tool_name == "mongodb_count":
        count = coll.count_documents(arguments.get("query") or {})
        return {"count": count, "collection": coll_name}
    return {"error": f"Unknown tool: {tool_name}"}


def _aggregate(coll: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """A bounded aggregate: final ``$limit``, no disk spill, streamed cursor.

    The pipeline gets a final ``{$limit: max + 1}`` (the server stops producing
    after it; the extra document only tells whether the result was cut) and
    ``allowDiskUse: false``; the cursor streams in small batches and is read to
    at most ``max`` documents — it used to be materialised whole, then sliced.
    """
    _, max_docs, _ = _bounds()
    pipeline = arguments.get("pipeline")
    if not isinstance(pipeline, list) or not all(isinstance(st, dict) for st in pipeline):
        raise MongoArgumentError("pipeline must be a list of stage documents")
    bounded = [*pipeline, {"$limit": max_docs + 1}]
    cursor = coll.aggregate(bounded, allowDiskUse=False, batchSize=min(max_docs + 1, 101))
    docs: list[dict[str, Any]] = []
    truncated = False
    try:
        for doc in cursor:
            if len(docs) >= max_docs:
                truncated = True
                break
            docs.append(doc)
    finally:
        cursor.close()
    return {"results": _serialize(docs), "count": len(docs), "truncated": truncated}


# The driver's own socket factory, wrapped once by _install_member_guard().
_ORIGINAL_CREATE_CONNECTION: Any = None


def _normalise_address(address: Any) -> tuple[str, int]:
    return (str(address[0]).lower().rstrip("."), int(address[1]))


try:  # a ServerListener subclass, so pymongo accepts and keeps it as a listener
    from pymongo.monitoring import ServerListener as _ListenerBase
except ImportError:  # pragma: no cover - pymongo is a dependency
    _ListenerBase = object  # type: ignore[assignment,misc]


class _MemberGuard(_ListenerBase):  # type: ignore[misc,valid-type]
    """Event listener carried by OUR clients only: the members they may dial.

    pymongo opens monitoring (and pool) sockets to every member a server
    advertises in its ``hello`` reply — including hosts the tenant never listed,
    i.e. a hostile server could make the platform connect to internal
    addresses. Every socket the driver opens goes through
    ``pymongo.pool_shared._create_connection(address, options)`` with this
    client's listeners in ``options``; the wrapper refuses any address this
    guard does not allow BEFORE a socket exists.
    """

    def __init__(self, seeds: set[tuple[str, int]]) -> None:
        self.seeds = {_normalise_address(a) for a in seeds}
        self.refused: list[tuple[str, int]] = []

    def allows(self, address: Any) -> bool:
        return _normalise_address(address) in self.seeds

    # pymongo.monitoring.ServerListener interface (no-ops).
    def opened(self, event: Any) -> None:
        return None

    def description_changed(self, event: Any) -> None:
        return None

    def closed(self, event: Any) -> None:
        return None


def _install_member_guard() -> None:
    """Wrap pymongo's socket factory once (only acts for clients carrying a guard)."""
    global _ORIGINAL_CREATE_CONNECTION
    import pymongo.pool_shared as pool_shared

    if getattr(pool_shared._create_connection, "_member_guard", False):
        return
    _ORIGINAL_CREATE_CONNECTION = pool_shared._create_connection

    def _guarded(address: Any, options: Any) -> Any:
        listeners: Any = getattr(options, "_event_listeners", None)
        if hasattr(listeners, "event_listeners"):
            listeners = listeners.event_listeners()
        for listener in listeners or ():
            if isinstance(listener, _MemberGuard) and not listener.allows(address):
                listener.refused.append(_normalise_address(address))
                raise OSError(
                    f"MongoDB member {address[0]}:{address[1]} is not listed in the "
                    "connector's URI; the egress policy refuses to dial it"
                )
        return _ORIGINAL_CREATE_CONNECTION(address, options)

    _guarded._member_guard = True  # type: ignore[attr-defined]
    pool_shared._create_connection = _guarded


def _seed_selector(seeds: set[tuple[str, int]]) -> Any:
    """Server selector that only ever picks a member the tenant's URI listed."""

    def _select(server_descriptions: list[Any]) -> list[Any]:
        return [
            sd
            for sd in server_descriptions
            if (str(sd.address[0]).lower().rstrip("."), int(sd.address[1])) in seeds
        ]

    return _select


def _first_contact(client: Any, kwargs: dict[str, Any]) -> None:
    """Reach the server once, bounded by the server-selection timeout.

    Under the per-call operation timeout pymongo ignores serverSelectionTimeoutMS,
    so an unreachable / TLS-failing host would take the whole tool timeout to
    fail. A new pooled client pings first (one round trip per client lifetime).
    """
    import pymongo

    _, _, timeout_ms = _bounds()
    selection_ms = min(int(kwargs.get("serverSelectionTimeoutMS") or 5000), timeout_ms)
    with pymongo.timeout(selection_ms / 1000.0):
        client["admin"].command("ping")


def _build_client(dsn: str, kwargs: dict[str, Any]) -> Any:
    import pymongo
    from pymongo.uri_parser import parse_uri

    parsed = parse_uri(dsn)
    options = parsed.get("options") or {}
    seeds = {(str(h).lower().rstrip("."), int(p)) for h, p in parsed["nodelist"]}
    if len(seeds) == 1 and not options.get("replicaSet") and not options.get("loadBalanced"):
        # One host: talk to it only — never discover members it advertises.
        kwargs = {**kwargs, "directConnection": True}
    else:
        kwargs = {**kwargs, "server_selector": _seed_selector(seeds)}
    # Monitors and pools never open a socket to a member the URI did not list.
    _install_member_guard()
    kwargs["event_listeners"] = [*kwargs.get("event_listeners", []), _MemberGuard(seeds)]
    return pymongo.MongoClient(dsn, **kwargs)


def _call_sync(
    entry: Any,
    tool_name: str,
    arguments: dict[str, Any],
    credentials: dict[str, Any] | None,
) -> dict[str, Any]:
    db_name = _db_name(entry.dsn, arguments, credentials)
    coll_name = str(arguments.get("collection") or "documents")
    return _run_tool(entry.client[db_name], db_name, coll_name, tool_name, arguments)
