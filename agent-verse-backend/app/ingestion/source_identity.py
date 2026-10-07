"""Canonical target of an ingestion Source — what data it actually reads.

Registering the same upstream data twice into one knowledge-base collection (the
same MongoDB collection, the same bucket prefix, the same feed) used to be
accepted: the second Source's sync indexed every document again, the collection
held each document twice under the same ``source_url`` and search returned the
same passage twice. ``POST /sources`` / ``PATCH /sources/{id}`` now refuse such a
Source (409, naming the existing one), and ``source_configs`` carries a unique
index on ``(tenant_id, collection_id, canonical_target_hash)`` so two concurrent
creates cannot both win.

The canonical target is::

    connector kind + normalised endpoint (scheme, host(s), port; multi-host DSNs
    sorted) + database / bucket / account + the set of collections, tables,
    prefixes, topics, URLs or patterns + the Source's include/exclude patterns

Credentials are stripped first and are never part of the key, never compared and
never logged: user-info is removed from every URI, secret-looking keys
(:func:`app.ingestion.source_secrets.is_secret_key`) are dropped and secret-looking
URL query parameters are removed.

Each connector type has its own canonicaliser (:data:`_CANONICALIZERS`,
extendable with :func:`register_canonicalizer`), so e.g. a different MongoDB
collection list, a different S3 prefix or a different SQL table set is a
different target. A canonicaliser returns ``None`` when the target cannot be
identified without a secret (a HubSpot or PagerDuty account is named only by its
token): such a Source is never refused. Unknown connector types fall back to
:func:`_default_canonical` — refuse only an exact match of the normalised,
secret-free config, and only when that config still names an endpoint (or the
Source has no credentials at all), so two different accounts behind the same
non-secret settings are never mistaken for one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

from app.ingestion.source_secrets import is_secret_key

__all__ = [
    "CANONICAL_TARGET_INDEX",
    "DuplicateSourceError",
    "canonical_source_url",
    "canonical_target",
    "canonical_target_hash",
    "canonical_target_hash_for",
    "find_duplicate_in",
    "register_canonicalizer",
]

Canonicalizer = Callable[[dict[str, Any]], dict[str, Any] | None]

_CANONICALIZERS: dict[str, Canonicalizer] = {}

_DEFAULT_PORTS: dict[str, int] = {
    "http": 80,
    "https": 443,
    "ws": 80,
    "wss": 443,
    "mongodb": 27017,
    "postgres": 5432,
    "postgresql": 5432,
    "mysql": 3306,
    "redis": 6379,
    "rediss": 6379,
    "neo4j": 7687,
    "neo4j+s": 7687,
    "neo4j+ssc": 7687,
    "bolt": 7687,
    "bolt+s": 7687,
    "bolt+ssc": 7687,
    "imap": 143,
    "imaps": 993,
    "mqtt": 1883,
}

# Scheme aliases that dial the same server.
_SCHEME_ALIASES = {"postgres": "postgresql", "postgresql+asyncpg": "postgresql"}

# Query parameters that carry a credential (pre-signed URLs, API keys, tokens).
_SECRET_QUERY_KEYS = frozenset(
    {
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
        "x-goog-signature",
        "x-goog-credential",
        "sig",
        "signature",
    }
)


def register_canonicalizer(*source_types: str) -> Callable[[Canonicalizer], Canonicalizer]:
    """Register ``fn(connection_config) -> target dict | None`` for connector types."""

    def deco(fn: Canonicalizer) -> Canonicalizer:
        for source_type in source_types:
            _CANONICALIZERS[source_type] = fn
        return fn

    return deco


# ── normalisation helpers ─────────────────────────────────────────────────────


def _s(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _items(value: Any) -> list[str]:
    """A list or comma-separated string → stripped, non-empty strings."""
    if value is None:
        return []
    if isinstance(value, list | tuple | set | frozenset):
        raw: Iterable[Any] = value
    else:
        raw = str(value).split(",")
    return [_s(v) for v in raw if _s(v)]


def _set(value: Any, *, fold: Callable[[str], str] | None = None) -> list[str]:
    """Order- and duplicate-insensitive list (optionally case-folded)."""
    items = _items(value)
    if fold is not None:
        items = [fold(i) for i in items]
    return sorted(set(items))


def _lower(value: str) -> str:
    return value.lower()


def _upper(value: str) -> str:
    return value.upper()


def _is_secret_query_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _SECRET_QUERY_KEYS or is_secret_key(key) or "token" in lowered


def _host(host: str) -> str:
    host = unquote(host).strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def _host_port(item: str, scheme: str, default_port: int | None = None) -> str:
    """``host[:port]`` normalised: lower-case host, explicit default port."""
    item = item.strip()
    if item.startswith("["):
        host, _, rest = item[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = item.rpartition(":") if item.count(":") == 1 else (item, "", "")
    host = _host(host)
    default = default_port if default_port is not None else _DEFAULT_PORTS.get(scheme)
    port = port.strip()
    if not port and default is not None and not scheme.endswith("+srv"):
        port = str(default)
    if port.isdigit():
        port = str(int(port))
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{port}" if port else host


def _scheme(raw: str) -> str:
    scheme = raw.strip().lower()
    return _SCHEME_ALIASES.get(scheme, scheme)


def _split_uri(uri: str) -> tuple[str, list[str], str, str]:
    """``scheme://[userinfo@]h1[:p1],h2[:p2]/path?query`` → (scheme, hosts, path, query).

    User-info (credentials) is dropped here and nowhere else consulted. Works for
    multi-host DSNs that ``urlsplit`` cannot parse (it chokes on several ports).
    """
    text = uri.strip()
    scheme, sep, rest = text.partition("://")
    if not sep:
        return "", [], "", ""
    scheme = _scheme(scheme)
    rest, _, _fragment = rest.partition("#")
    rest, _, query = rest.partition("?")
    authority, slash, path = rest.partition("/")
    authority = authority.rpartition("@")[2]
    hosts = [h for h in (x.strip() for x in authority.split(",")) if h]
    return scheme, hosts, (slash + path) if slash else "", query


def _endpoint(uri: str, *, default_port: int | None = None) -> dict[str, Any]:
    """Normalised endpoint of a DSN/URI: scheme + sorted host:port set + path.

    Connection options in the query (TLS, timeouts, auth source, replica set) do
    not change what data is read, so they are not part of the identity.
    """
    scheme, hosts, path, _query = _split_uri(uri)
    return {
        "scheme": scheme,
        "hosts": sorted({_host_port(h, scheme, default_port) for h in hosts}),
        "path": unquote(path).strip("/"),
    }


def _norm_url(url: Any) -> str:
    """A URL normalised for identity: lower-case scheme/host, default port dropped,
    user-info, fragment and secret query parameters removed, remaining query
    parameters sorted, trailing slash of the path removed."""
    text = _s(url)
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        scheme = parts.scheme.lower()
        host = _host(parts.hostname or "")
        port = parts.port
    except ValueError:
        return text
    if port is not None and _DEFAULT_PORTS.get(scheme) == port:
        port = None
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    path = parts.path.rstrip("/")
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_secret_query_key(k)
        )
    )
    return f"{scheme}://{netloc}{path}" + (f"?{query}" if query else "")


def _urls(value: Any) -> list[str]:
    return sorted({u for u in (_norm_url(v) for v in _items(value)) if u})


def _host_field(cc: dict[str, Any], scheme: str, default_port: int) -> list[str]:
    """``host`` (possibly a comma list) + ``port`` → sorted host:port set."""
    port = _s(cc.get("port")) or str(default_port)
    out = set()
    for item in _items(cc.get("host")):
        has_port = "]:" in item if item.startswith("[") else item.count(":") == 1
        out.add(_host_port(item if has_port else f"{item}:{port}", scheme, default_port))
    return sorted(out)


def _query_text(value: Any) -> str:
    """A SQL / Flux / Cypher query with whitespace collapsed (no case change: string
    literals are case-sensitive)."""
    return " ".join(_s(value).split())


def _sql_ident(value: str) -> str:
    """Unquoted SQL identifiers fold case; quoted ones keep it."""
    return value if '"' in value or "`" in value else value.lower()


# ── per-connector canonicalisers ──────────────────────────────────────────────


@register_canonicalizer("mongodb")
def _mongodb(cc: dict[str, Any]) -> dict[str, Any] | None:
    uri = _s(cc.get("uri") or cc.get("connection_string"))
    if uri:
        ep = _endpoint(uri, default_port=27017)
        scheme, hosts, path = ep["scheme"], ep["hosts"], ep["path"]
    else:
        scheme, hosts, path = "mongodb", _host_field(cc, "mongodb", 27017), ""
    if not hosts:
        return None
    database = _s(cc.get("database")) or path.split("/", 1)[0]
    collections = _set(cc.get("collections") or cc.get("collections_csv"))
    if not collections and _s(cc.get("collection")):
        collections = [_s(cc.get("collection"))]
    return {
        "endpoint": {"scheme": scheme, "hosts": hosts},
        # MongoDB refuses two databases differing only in case on one server;
        # collection names are case-sensitive.
        "database": database.lower(),
        "collections": collections or ["*"],
    }


def _sql_target(
    cc: dict[str, Any], scheme: str, default_port: int, *, dsn_key: str | None = None
) -> dict[str, Any] | None:
    dsn = _s(cc.get(dsn_key)) if dsn_key else ""
    database = _s(cc.get("database"))
    if dsn and "://" in dsn:
        ep = _endpoint(dsn, default_port=default_port)
        hosts = ep["hosts"]
        database = database or ep["path"].split("/", 1)[0]
    elif dsn:  # libpq keyword form: host=... port=... dbname=...
        kv: dict[str, str] = {}
        for part in dsn.split():
            key, _, value = part.partition("=")
            kv[key.lower()] = value.strip("'\"")
        hosts = _host_field(
            {"host": kv.get("host") or kv.get("hostaddr"), "port": kv.get("port")},
            scheme,
            default_port,
        )
        database = database or kv.get("dbname", "")
    else:
        hosts = _host_field(cc, scheme, default_port)
    if not hosts:
        return None
    tables = _set(cc.get("tables") or cc.get("table"), fold=_sql_ident)
    return {
        "endpoint": {"scheme": scheme, "hosts": hosts},
        "database": database,
        "tables": tables or ["*"],
        "query": _query_text(cc.get("query")),
    }


@register_canonicalizer("postgresql")
def _postgresql(cc: dict[str, Any]) -> dict[str, Any] | None:
    return _sql_target(cc, "postgresql", 5432, dsn_key="dsn")


@register_canonicalizer("mysql")
def _mysql(cc: dict[str, Any]) -> dict[str, Any] | None:
    return _sql_target(cc, "mysql", 3306, dsn_key="dsn")


@register_canonicalizer("clickhouse")
def _clickhouse(cc: dict[str, Any]) -> dict[str, Any] | None:
    return _sql_target(cc, "clickhouse", 8123)


def _s3_endpoint(cc: dict[str, Any]) -> str:
    endpoint = _norm_url(cc.get("endpoint_url"))
    # AWS bucket names are global: without a custom endpoint the region does not
    # change which bucket is read.
    return endpoint or "aws-s3"


@register_canonicalizer("s3", "minio")
def _s3(cc: dict[str, Any]) -> dict[str, Any] | None:
    bucket = _s(cc.get("bucket")).lower()
    if not bucket:
        return None
    return {
        # minio is an S3 API: the same bucket at the same endpoint is one target.
        "kind": "s3",
        "endpoint": _s3_endpoint(cc),
        "bucket": bucket,
        "prefix": _s(cc.get("prefix")).lstrip("/"),
    }


@register_canonicalizer("gcs")
def _gcs(cc: dict[str, Any]) -> dict[str, Any] | None:
    bucket = _s(cc.get("bucket")).lower()
    if not bucket:
        return None
    return {"bucket": bucket, "prefix": _s(cc.get("prefix")).lstrip("/")}


@register_canonicalizer("azure_blob")
def _azure_blob(cc: dict[str, Any]) -> dict[str, Any] | None:
    account = _s(cc.get("account_name")).lower()
    if not account:
        # The account is named inside the (secret) connection string.
        return None
    container = _s(cc.get("container")).lower()
    if not container:
        return None
    return {
        "account": account,
        "container": container,
        "prefix": _s(cc.get("prefix")).lstrip("/"),
    }


@register_canonicalizer("web_crawl")
def _web_crawl(cc: dict[str, Any]) -> dict[str, Any] | None:
    seeds = _urls([*_items(cc.get("seed_urls")), *_items(cc.get("urls"))])
    sitemap = _norm_url(cc.get("sitemap_url"))
    if not seeds and not sitemap:
        return None
    return {
        "seeds": seeds,
        "sitemap": sitemap,
        "include_url_pattern": _s(cc.get("include_url_pattern")),
        "exclude_url_pattern": _s(cc.get("exclude_url_pattern")),
    }


@register_canonicalizer("rss", "pdf_file", "docx_file")
def _url_list(cc: dict[str, Any]) -> dict[str, Any] | None:
    urls = _urls(cc.get("urls"))
    return {"urls": urls} if urls else None


@register_canonicalizer("http")
def _http(cc: dict[str, Any]) -> dict[str, Any] | None:
    url = _norm_url(cc.get("url"))
    if not url:
        return None
    params = cc.get("params") if isinstance(cc.get("params"), dict) else {}
    return {
        "url": url,
        "method": (_s(cc.get("method")) or "GET").upper(),
        "params": sorted(
            (str(k), _s(v)) for k, v in params.items() if not _is_secret_query_key(str(k))
        ),
        "records_path": _s(cc.get("records_path")),
    }


@register_canonicalizer("redis")
def _redis(cc: dict[str, Any]) -> dict[str, Any] | None:
    uri = _s(cc.get("uri"))
    db = _s(cc.get("db"))
    if uri:
        ep = _endpoint(uri, default_port=6379)
        scheme = "redis"  # rediss is the same server over TLS
        hosts = ep["hosts"]
        db = db or ep["path"] or "0"
    else:
        scheme = "redis"
        hosts = _host_field(cc, "redis", 6379)
    nodes = sorted(
        {_host_port(n, "redis", 6379) for n in _items(cc.get("cluster_nodes"))}
        | {_host_port(n, "redis", 26379) for n in _items(cc.get("sentinels"))}
    )
    if not hosts and not nodes:
        return None
    return {
        "endpoint": {"scheme": scheme, "hosts": hosts, "nodes": nodes},
        "sentinel_master": _s(cc.get("sentinel_master")),
        "db": db or "0",
        "key_patterns": _set(cc.get("key_patterns")) or ["*"],
        "types": _set(cc.get("types"), fold=_lower),
    }


@register_canonicalizer("elasticsearch")
def _elasticsearch(cc: dict[str, Any]) -> dict[str, Any] | None:
    url = _norm_url(cc.get("url"))
    if not url:
        return None
    # Index names are lower-case by definition; ``a,b`` is a multi-index target.
    return {"url": url, "index": _set(cc.get("index"), fold=_lower) or ["*"]}


@register_canonicalizer("kafka")
def _kafka(cc: dict[str, Any]) -> dict[str, Any] | None:
    servers = sorted({_host_port(s, "kafka") for s in _items(cc.get("bootstrap_servers"))})
    topics = _set(cc.get("topics"))
    if not servers or not topics:
        return None
    return {"bootstrap": servers, "topics": topics}


@register_canonicalizer("mqtt")
def _mqtt(cc: dict[str, Any]) -> dict[str, Any] | None:
    hosts = _host_field(cc, "mqtt", 1883)
    return {"hosts": hosts, "topics": _set(cc.get("topics"))} if hosts else None


@register_canonicalizer("neo4j")
def _neo4j(cc: dict[str, Any]) -> dict[str, Any] | None:
    uri = _s(cc.get("uri"))
    if not uri:
        return None
    ep = _endpoint(uri, default_port=7687)
    return {
        "hosts": ep["hosts"],
        "node_labels": _set(cc.get("node_labels")),
        "cypher": _query_text(cc.get("cypher")),
    }


@register_canonicalizer("imap")
def _imap(cc: dict[str, Any]) -> dict[str, Any] | None:
    ssl = cc.get("ssl", True) not in (False, "false", "0", 0)
    hosts = _host_field(cc, "imaps" if ssl else "imap", 993 if ssl else 143)
    if not hosts:
        return None
    # The mailbox owner (not a credential) names which account is read.
    return {
        "hosts": hosts,
        "username": _s(cc.get("username")).lower(),
        "mailbox": _s(cc.get("mailbox")) or "INBOX",
    }


@register_canonicalizer("influxdb")
def _influxdb(cc: dict[str, Any]) -> dict[str, Any] | None:
    url = _norm_url(cc.get("url"))
    if not url:
        return None
    return {
        "url": url,
        "org": _s(cc.get("org")),
        "bucket": _s(cc.get("bucket")),
        "measurement": _s(cc.get("measurement")),
        "flux_query": _query_text(cc.get("flux_query")),
    }


@register_canonicalizer("snowflake")
def _snowflake(cc: dict[str, Any]) -> dict[str, Any] | None:
    account = _s(cc.get("account")).lower()
    if not account:
        return None
    return {
        "account": account,
        "database": _s(cc.get("database")).lower(),
        "schema": _s(cc.get("schema")).lower(),
        "query": _query_text(cc.get("query")),
        "stream_name": _s(cc.get("stream_name")).lower(),
        "mode": _s(cc.get("mode")).lower(),
    }


@register_canonicalizer("bigquery")
def _bigquery(cc: dict[str, Any]) -> dict[str, Any] | None:
    project = _s(cc.get("project"))
    table = _s(cc.get("table"))
    query = _query_text(cc.get("query"))
    if not (project or table or query):
        return None
    return {"project": project, "table": table, "query": query, "mode": _s(cc.get("mode"))}


@register_canonicalizer("duckdb")
def _duckdb(cc: dict[str, Any]) -> dict[str, Any] | None:
    database = _s(cc.get("database") or cc.get("file"))
    if not database:
        return None
    return {"database": database, "query": _query_text(cc.get("query"))}


@register_canonicalizer("confluence")
def _confluence(cc: dict[str, Any]) -> dict[str, Any] | None:
    base = _norm_url(cc.get("base_url"))
    if not base:
        return None
    return {
        "base_url": base,
        "space_keys": _set(cc.get("space_keys"), fold=_upper) or ["*"],
        "content_types": _set(cc.get("content_types"), fold=_lower),
    }


@register_canonicalizer("jira")
def _jira(cc: dict[str, Any]) -> dict[str, Any] | None:
    base = _norm_url(cc.get("base_url"))
    if not base:
        return None
    return {
        "base_url": base,
        "project_keys": _set(cc.get("project_keys"), fold=_upper) or ["*"],
        "jql_extra": _query_text(cc.get("jql_extra")),
    }


@register_canonicalizer("github")
def _github(cc: dict[str, Any]) -> dict[str, Any] | None:
    repos = _set(cc.get("repos"), fold=_lower)
    if not repos:
        return None
    return {
        "repos": repos,
        "branch": _s(cc.get("branch")),
        "include_code": bool(cc.get("include_code")),
    }


@register_canonicalizer("gitlab")
def _gitlab(cc: dict[str, Any]) -> dict[str, Any] | None:
    projects = _set(cc.get("project_ids"))
    if not projects:
        return None
    return {
        "base_url": _norm_url(cc.get("base_url") or "https://gitlab.com"),
        "project_ids": projects,
        "ingest_types": _set(cc.get("ingest_types"), fold=_lower),
    }


@register_canonicalizer("sentry")
def _sentry(cc: dict[str, Any]) -> dict[str, Any] | None:
    org = _s(cc.get("org_slug")).lower()
    if not org:
        return None
    return {
        "base_url": _norm_url(cc.get("base_url") or "https://sentry.io"),
        "org_slug": org,
        "project_slugs": _set(cc.get("project_slugs"), fold=_lower) or ["*"],
    }


@register_canonicalizer("zendesk")
def _zendesk(cc: dict[str, Any]) -> dict[str, Any] | None:
    sub = _s(cc.get("subdomain")).lower()
    if not sub:
        return None
    return {"subdomain": sub, "ingest_types": _set(cc.get("ingest_types"), fold=_lower)}


@register_canonicalizer("servicenow")
def _servicenow(cc: dict[str, Any]) -> dict[str, Any] | None:
    instance = _s(cc.get("instance")).lower()
    if not instance:
        return None
    return {"instance": instance, "tables": _set(cc.get("tables"), fold=_lower)}


@register_canonicalizer("salesforce")
def _salesforce(cc: dict[str, Any]) -> dict[str, Any] | None:
    user = _s(cc.get("username")).lower()
    if not user:
        return None  # the org is named only by the credentials
    return {
        "login_url": _norm_url(cc.get("login_url") or "https://login.salesforce.com"),
        "username": user,
        "sobjects": _set(cc.get("sobjects"), fold=_lower),
    }


@register_canonicalizer("notion")
def _notion(cc: dict[str, Any]) -> dict[str, Any] | None:
    db = _s(cc.get("database_id")).lower().replace("-", "")
    return {"database_id": db} if db else None


@register_canonicalizer("gdrive")
def _gdrive(cc: dict[str, Any]) -> dict[str, Any] | None:
    folder = _s(cc.get("folder_id"))
    return {"folder_id": folder} if folder else None


@register_canonicalizer("sharepoint")
def _sharepoint(cc: dict[str, Any]) -> dict[str, Any] | None:
    site = _s(cc.get("site_id")).lower()
    if not site:
        return None
    return {
        "tenant_id": _s(cc.get("tenant_id")).lower(),
        "site_id": site,
        "drive_id": _s(cc.get("drive_id")).lower(),
    }


@register_canonicalizer("teams")
def _teams(cc: dict[str, Any]) -> dict[str, Any] | None:
    team = _s(cc.get("team_id")).lower()
    if not team:
        return None
    return {
        "tenant_id": _s(cc.get("tenant_id")).lower(),
        "team_id": team,
        "channel_ids": _set(cc.get("channel_ids")) or ["*"],
    }


@register_canonicalizer("discord")
def _discord(cc: dict[str, Any]) -> dict[str, Any] | None:
    channels = _set(cc.get("channel_ids"))  # global snowflake ids
    return {"channel_ids": channels} if channels else None


@register_canonicalizer("slack")
def _slack(cc: dict[str, Any]) -> dict[str, Any] | None:
    # Channel ids are workspace-unique; channel *names* repeat across workspaces
    # and the workspace is named only by the bot token, so names are no identity.
    if _items(cc.get("channel_names")):
        return None
    channels = _set(cc.get("channels"), fold=_upper)
    return {"channels": channels} if channels else None


@register_canonicalizer("youtube")
def _youtube(cc: dict[str, Any]) -> dict[str, Any] | None:
    channel = _s(cc.get("channel_id"))
    videos = _set(cc.get("video_ids"))
    if not channel and not videos:
        return None
    return {"channel_id": channel, "video_ids": videos}


@register_canonicalizer("kinesis")
def _kinesis(cc: dict[str, Any]) -> dict[str, Any] | None:
    stream = _s(cc.get("stream_name"))
    if not stream:
        return None
    # A stream name is unique per account+region; the access key id (not a
    # secret) keeps two accounts' same-named streams apart.
    return {
        "region": _s(cc.get("region")).lower(),
        "stream_name": stream,
        "access_key_id": _s(cc.get("access_key_id")),
    }


@register_canonicalizer("pubsub")
def _pubsub(cc: dict[str, Any]) -> dict[str, Any] | None:
    sub = _s(cc.get("subscription"))
    return {"project": _s(cc.get("project")), "subscription": sub} if sub else None


@register_canonicalizer("hubspot", "pagerduty")
def _account_named_by_token(cc: dict[str, Any]) -> dict[str, Any] | None:
    # The account is identified only by its token: never comparable, never refused.
    return None


# ── safe default ──────────────────────────────────────────────────────────────

# Settings that tune *how* a Source reads, not *what* it reads.
_TUNING_KEYS = frozenset(
    {
        "batch_size",
        "timeout",
        "timeout_ms",
        "timeout_seconds",
        "poll_timeout_seconds",
        "page_size",
        "concurrency",
        "retries",
        "max_retries",
        "event_listeners",
    }
)
_TUNING_PREFIXES = ("max_", "min_", "num_", "tls", "ssl", "verify")
# Keys that name an endpoint / account outside of any credential.
_ENDPOINT_HINTS = (
    "url",
    "uri",
    "host",
    "endpoint",
    "server",
    "bucket",
    "account",
    "instance",
    "subdomain",
    "domain",
    "project",
    "database",
    "org",
    "site",
    "workspace",
)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k).lower(): _normalize_value(v)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]).lower())
            if not is_secret_key(k)
        }
    if isinstance(value, list | tuple | set | frozenset):
        normalized = [_normalize_value(v) for v in value]
        return sorted(normalized, key=lambda v: json.dumps(v, sort_keys=True, default=str))
    if isinstance(value, str):
        stripped = value.strip()
        return _norm_url(stripped) if "://" in stripped else stripped
    return value


def _default_canonical(cc: dict[str, Any]) -> dict[str, Any] | None:
    """Exact match of the normalised, secret-free, non-tuning config.

    ``None`` when the config carries credentials but nothing outside them names
    an endpoint: two different accounts would look identical once the secrets
    are stripped, so such a Source can never be called a duplicate.
    """
    has_secret = False
    kept: dict[str, Any] = {}
    for key, value in cc.items():
        name = str(key)
        lowered = name.lower()
        if is_secret_key(name):
            has_secret = has_secret or value not in (None, "", {}, [])
            continue
        if lowered in _TUNING_KEYS or lowered.startswith(_TUNING_PREFIXES):
            continue
        if value in (None, "", {}, []):
            continue
        kept[lowered] = _normalize_value(value)
    names_endpoint = any(any(h in k for h in _ENDPOINT_HINTS) for k in kept)
    if has_secret and not names_endpoint:
        return None
    return {"config": kept}


# ── public API ────────────────────────────────────────────────────────────────


def canonical_target(
    source_type: str,
    connection_config: dict[str, Any] | None,
    *,
    include_patterns: Iterable[str] | None = None,
    exclude_patterns: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    """The secret-free canonical target of a Source, or ``None`` when it cannot be
    identified without credentials (such a Source is never refused as a duplicate).

    ``connection_config`` must be the plaintext config (as the API and the
    connectors see it); nothing secret ends up in the result.
    """
    stype = _s(source_type).lower()
    cc = dict(connection_config or {})
    fn = _CANONICALIZERS.get(stype, _default_canonical)
    try:
        target = fn(cc)
    except (TypeError, ValueError, AttributeError):
        return None  # a malformed config is no identity; it is refused elsewhere
    if target is None:
        return None
    kind = str(target.pop("kind", stype))
    return {
        "kind": kind,
        "target": target,
        "include": sorted({_s(p) for p in include_patterns or () if _s(p)}),
        "exclude": sorted({_s(p) for p in exclude_patterns or () if _s(p)}),
    }


def canonical_target_hash(
    source_type: str,
    connection_config: dict[str, Any] | None,
    *,
    include_patterns: Iterable[str] | None = None,
    exclude_patterns: Iterable[str] | None = None,
) -> str | None:
    """SHA-256 hex of :func:`canonical_target` (``None`` when it is ``None``)."""
    target = canonical_target(
        source_type,
        connection_config,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )
    if target is None:
        return None
    payload = json.dumps(target, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(b"agentverse-source-target-v1:" + payload.encode()).hexdigest()


def canonical_target_hash_for(config: Any) -> str | None:
    """:func:`canonical_target_hash` of a ``SourceConfig`` (plaintext config)."""
    return canonical_target_hash(
        str(getattr(config, "source_type", "") or ""),
        getattr(config, "connection_config", None) or {},
        include_patterns=getattr(config, "include_patterns", None) or (),
        exclude_patterns=getattr(config, "exclude_patterns", None) or (),
    )


def canonical_source_url(url: Any) -> str:
    """Identity of a document URL for retrieval de-duplication.

    Scheme and host(s) lower-cased (a multi-host authority sorted, so
    ``mongodb://b,a/db/c/1`` and ``mongodb://a,b/db/c/1`` are one document),
    user-info and fragment dropped, default ports removed, secret query
    parameters removed and the rest sorted, trailing slash removed. The path keeps
    its case: document keys are case-sensitive.
    """
    text = _s(url)
    if not text or "://" not in text:
        return text
    scheme, hosts, path, query = _split_uri(text)
    if not hosts:
        return text
    norm_hosts = []
    for h in hosts:
        hp = _host_port(h, scheme)
        default = _DEFAULT_PORTS.get(scheme)
        if default is not None and hp.endswith(f":{default}"):
            hp = hp[: -len(f":{default}")]
        norm_hosts.append(hp)
    pairs = sorted(
        (k, v)
        for k, v in parse_qsl(query, keep_blank_values=True)
        if not _is_secret_query_key(k)
    )
    q = urlencode(pairs)
    return f"{scheme}://{','.join(sorted(norm_hosts))}{path.rstrip('/')}" + (
        f"?{q}" if q else ""
    )


# ── duplicate detection ───────────────────────────────────────────────────────

# The partial unique index that makes the refusal race-free (migration
# a8c2e4f6b1d3): one Source per (tenant, KB collection, canonical target).
CANONICAL_TARGET_INDEX = "uq_source_configs_canonical_target"


class DuplicateSourceError(Exception):
    """A Source with the same canonical target already feeds the same collection."""

    def __init__(self, existing_source_id: str) -> None:
        super().__init__(
            "a source reading the same data into the same knowledge collection "
            f"already exists: {existing_source_id}"
        )
        self.existing_source_id = existing_source_id


def find_duplicate_in(
    configs: Iterable[Any], candidate: Any, *, exclude_source_id: str | None = None
) -> str | None:
    """The id of a Source in ``configs`` (same tenant) that reads the same target as
    ``candidate`` into the same collection, or ``None``. Plaintext configs only."""
    collection = _s(getattr(candidate, "collection_id", ""))
    if not collection:
        return None
    wanted = canonical_target_hash_for(candidate)
    if wanted is None:
        return None
    tenant = getattr(candidate, "tenant_id", None)
    for other in configs:
        if other is candidate or getattr(other, "tenant_id", None) != tenant:
            continue
        if exclude_source_id and getattr(other, "source_id", None) == exclude_source_id:
            continue
        if _s(getattr(other, "collection_id", "")) != collection:
            continue
        if canonical_target_hash_for(other) == wanted:
            return str(other.source_id)
    return None
