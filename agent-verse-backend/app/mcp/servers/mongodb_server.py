"""MongoDB MCP server — interact with a tenant's MongoDB database.

Credentials come ONLY from the calling connector (``credentials``): the
connection URI (``uri`` / ``connection_string`` / ``url``), optional
``username`` / ``password`` / ``auth_source`` / ``auth_mechanism`` overrides,
TLS options (``tls``, ``tls_allow_invalid_certificates``, ``tls_ca_pem``) and a
default ``database``. There is deliberately no platform-env fallback: the old
``MONGODB_MCP_URL`` lookup ran every tenant's calls against the PLATFORM's
database (confused deputy).

Egress: every host the URI names (each replica-set seed, or each SRV target of
a ``mongodb+srv`` URI, expanded so the driver runs no SRV query of its own) is
resolved, checked against the connector egress policy and pinned for the call
(``pin_source_dsn``). The driver only uses those checked hosts: a single-host
URI connects directly (no member discovery), and a replica-set URI selects only
among the members it lists — members the server advertises but the tenant did
not list are never used, so list every member (or use SRV).

URI options that would read platform files (``tlsCAFile`` ...), route through a
proxy (``proxyHost`` ...) or authenticate as the PLATFORM's ambient identity
(``MONGODB-AWS``, ``GSSAPI``, ``MONGODB-OIDC``, ``MONGODB-X509``) are refused.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from collections.abc import Iterator
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
# MONGODB-AWS / GSSAPI / MONGODB-OIDC / MONGODB-X509 fall back to the platform's
# ambient identity (env keys, instance metadata, keytab, local files).
_ALLOWED_AUTH_MECHANISMS = frozenset({"DEFAULT", "SCRAM-SHA-1", "SCRAM-SHA-256", "PLAIN"})

_TRUE = frozenset({"1", "true", "yes", "on"})

CONFIGURE_CREDENTIALS_ERROR = (
    "MongoDB connector has no connection URI configured. Configure credentials on the "
    "connector (a mongodb:// or mongodb+srv:// connection string); platform credentials "
    "are never used."
)


class MongoCredentialError(ValueError):
    """The connector's MongoDB credentials are missing or not allowed."""


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


def _check_auth_mechanism(value: str) -> None:
    if value.strip().upper() not in _ALLOWED_AUTH_MECHANISMS:
        raise MongoCredentialError(
            f"MongoDB auth mechanism '{value}' is not allowed; use SCRAM or PLAIN with "
            "credentials supplied on the connector"
        )


def _check_uri_options(uri: str) -> None:
    """Refuse URI options that reach outside the tenant's own database."""
    for key, value in parse_qsl(urlsplit(uri).query, keep_blank_values=True):
        lowered = key.strip().lower()
        if lowered in _BLOCKED_URI_OPTIONS:
            raise MongoCredentialError(f"MongoDB URI option '{key}' is not allowed")
        if lowered == "authmechanism":
            _check_auth_mechanism(value)


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
        _check_auth_mechanism(str(mechanism))
        kwargs["authMechanism"] = str(mechanism).strip().upper()
    if str(creds.get("tls", "")).strip() != "":
        kwargs["tls"] = _truthy(creds["tls"])
    if _truthy(creds.get("tls_allow_invalid_certificates")):
        kwargs["tlsAllowInvalidCertificates"] = True
    return kwargs


@contextlib.contextmanager
def _ca_file(credentials: dict[str, Any] | None) -> Iterator[str | None]:
    """Materialise a tenant-supplied CA bundle (PEM text) for the driver."""
    pem = str((credentials or {}).get("tls_ca_pem") or "").strip()
    if not pem:
        yield None
        return
    if "-----BEGIN CERTIFICATE-----" not in pem:
        raise MongoCredentialError("tls_ca_pem must be a PEM-encoded certificate bundle")
    fd, path = tempfile.mkstemp(prefix="mongo-ca-", suffix=".pem")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(pem)
        yield path
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


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
) -> dict[str, Any]:
    try:
        uri = _connection_uri(credentials)
        _check_uri_options(uri)
        kwargs = _client_kwargs(credentials)
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

    from app.ingestion.connector_egress import pin_source_dsn

    try:
        with _ca_file(credentials) as ca_path:
            if ca_path:
                kwargs["tlsCAFile"] = ca_path
                kwargs.setdefault("tls", True)
            # Every URI host (SRV targets expanded) is checked and pinned for the call.
            async with pin_source_dsn(uri, context="mcp builtin mongodb") as pins:
                _check_uri_options(pins.dsn)  # includes SRV TXT-record options
                return await asyncio.to_thread(
                    _call_sync, pins.dsn, kwargs, tool_name, arguments, credentials
                )
    except MongoCredentialError as exc:
        return {"error": str(exc), "status": "credentials_required"}
    except Exception as exc:
        logger.warning("mongodb_call_tool_error tool=%s error=%s", tool_name, str(exc)[:200])
        return {"error": str(exc)}


def _serialize(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for d in docs:
        if "_id" in d:
            d["_id"] = str(d["_id"])
    return docs


def _run_tool(
    db: Any, db_name: str, coll_name: str, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    coll = db[coll_name]
    if tool_name == "mongodb_find":
        limit = int(arguments.get("limit", 100))
        docs = list(
            coll.find(arguments.get("query") or {}, arguments.get("projection")).limit(limit)
        )
        return {"documents": _serialize(docs), "count": len(docs)}
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
        docs = list(coll.aggregate(arguments["pipeline"]))[:1000]
        return {"results": _serialize(docs), "count": len(docs)}
    if tool_name == "mongodb_list_collections":
        return {"collections": db.list_collection_names(), "database": db_name}
    if tool_name == "mongodb_count":
        count = coll.count_documents(arguments.get("query") or {})
        return {"count": count, "collection": coll_name}
    return {"error": f"Unknown tool: {tool_name}"}


def _seed_selector(seeds: set[tuple[str, int]]) -> Any:
    """Server selector that only ever picks a member the tenant's URI listed."""

    def _select(server_descriptions: list[Any]) -> list[Any]:
        return [
            sd
            for sd in server_descriptions
            if (str(sd.address[0]).lower().rstrip("."), int(sd.address[1])) in seeds
        ]

    return _select


def _call_sync(
    dsn: str,
    kwargs: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
    credentials: dict[str, Any] | None,
) -> dict[str, Any]:
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
    # Closed before the pin block ends: monitor threads resolve hosts while open.
    client: Any = pymongo.MongoClient(dsn, **kwargs)
    try:
        db_name = _db_name(dsn, arguments, credentials)
        coll_name = str(arguments.get("collection") or "documents")
        return _run_tool(client[db_name], db_name, coll_name, tool_name, arguments)
    finally:
        client.close()
