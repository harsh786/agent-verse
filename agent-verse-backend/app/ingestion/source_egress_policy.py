"""Save-time egress check of a Source's ``connection_config`` (USR-2).

Connectors re-check every destination when they fetch (see
:mod:`app.ingestion.connector_egress`), but a Source pointing at the cloud
metadata service, loopback, RFC 1918, CGNAT or an IPv6 / IPv4-mapped internal
address could still be *saved* — only ``POST /sources/validate`` looked, and
the UI may never call it. :func:`assert_source_config_egress` runs the same
shared SSRF guard over every URL-bearing and host-bearing field when a Source
is created or its ``connection_config`` is updated, so such a Source is refused
(422) instead of stored.

The walk is field-driven, not connector-driven, so a field a connector reads is
covered even if nobody remembered to list it per connector:

* known URL fields (``url``, ``base_url``, ``endpoint_url`` …) and URL lists
  (``urls``, ``seed_urls`` …) — :func:`assert_source_url`;
* DSN / URI fields (``dsn``, ``uri`` …) — :func:`assert_source_dsn` (every host
  the driver could dial, SRV targets included);
* ``host`` (+ ``port``; a comma-separated multi-host value is checked host by
  host) and host lists (``bootstrap_servers``, ``cluster_nodes``, ``sentinels`` …)
  — :func:`assert_source_host`;
* any other string value that *is* a URL / DSN (``scheme://…``), at any depth;
* connector-built destinations (Azure connection strings, ServiceNow
  instances, Zendesk subdomains) through the connector's own builder.

Secret references (``vault://``), masked values and empty fields are skipped.
DNS runs here (fail closed: a name that does not resolve is refused); call it
off the event loop.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.ingestion.connector_egress import (
    ConnectorEgressBlockedError,
    assert_source_dsn,
    assert_source_host,
    assert_source_url,
)

__all__ = ["assert_source_config_egress"]

_URL_KEYS = frozenset(
    {
        "url",
        "base_url",
        "api_url",
        "server_url",
        "site_url",
        "instance_url",
        "login_url",
        "token_url",
        "auth_url",
        "endpoint",
        "endpoint_url",
        "sitemap_url",
        "feed_url",
        "webhook_url",
        "callback_url",
        "proxy",
        "proxy_url",
    }
)
_URL_LIST_KEYS = frozenset({"urls", "seed_urls", "feed_urls", "endpoints", "sitemap_urls"})
_DSN_KEYS = frozenset(
    {"dsn", "uri", "connection_uri", "connection_url", "database_url", "mongo_uri"}
)
_HOST_KEYS = frozenset({"host", "hostname", "server", "server_host"})
_HOST_LIST_KEYS = frozenset(
    {"hosts", "bootstrap_servers", "brokers", "cluster_nodes", "sentinels", "seeds"}
)
# Content, not destinations: never parsed as one (a query may quote a URL).
_CONTENT_KEYS = frozenset(
    {
        "query",
        "cypher",
        "flux_query",
        "jql",
        "cql",
        "headers",
        "params",
        "include_url_pattern",
        "exclude_url_pattern",
        "include_patterns",
        "exclude_patterns",
        "records_path",
        "fields",
    }
)
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")
_WEB_SCHEMES = frozenset({"http", "https"})
# Object-store URIs name a bucket, not a host.
_BUCKET_SCHEMES = frozenset({"s3", "s3a", "gs", "gcs", "az", "abfs", "abfss", "wasb", "wasbs"})
_MASK = "********"


def _skippable(value: str) -> bool:
    stripped = value.strip()
    return not stripped or stripped == _MASK or stripped.lower().startswith("vault://")


def _check_url(value: str, context: str) -> None:
    scheme = _SCHEME.match(value.strip())
    if scheme and scheme.group(1).lower() not in _WEB_SCHEMES:
        _check_uri(value, context)
        return
    assert_source_url(value.strip(), context=context)


def _check_uri(value: str, context: str) -> None:
    """A ``scheme://`` value: a web URL, a bucket URI, or a database / broker DSN."""
    match = _SCHEME.match(value.strip())
    scheme = match.group(1).lower() if match else ""
    if scheme in _WEB_SCHEMES:
        assert_source_url(value.strip(), context=context)
    elif scheme in _BUCKET_SCHEMES:
        return
    else:
        assert_source_dsn(value.strip(), context=context)


def _split_hosts(value: Any) -> Iterable[Any]:
    if isinstance(value, str):
        return [part for part in re.split(r"[,\s]+", value) if part]
    if isinstance(value, list | tuple):
        return value
    return [value]


def _check_host_entry(entry: Any, default_port: Any, context: str) -> None:
    if isinstance(entry, Mapping):
        _check_host_entry(entry.get("host") or entry.get("hostname"), entry.get("port"), context)
        return
    if isinstance(entry, list | tuple) and entry:
        _check_host_entry(entry[0], entry[1] if len(entry) > 1 else default_port, context)
        return
    text = str(entry or "").strip()
    if _skippable(text):
        return
    if _SCHEME.match(text):
        _check_uri(text, context)
        return
    host, port = text, default_port
    if text.startswith("["):  # [v6]:port
        host, _, rest = text[1:].partition("]")
        port = rest.lstrip(":") or default_port
    elif text.count(":") == 1:  # host:port (a bare IPv6 literal has several colons)
        host, _, port = text.partition(":")
    assert_source_host(host, port or None, context=context)


def _walk(node: Any, context: str, *, key: str = "", port: Any = None) -> None:
    if isinstance(node, Mapping):
        local_port = node.get("port", port)
        for child_key, child in node.items():
            name = str(child_key).lower()
            if name in _CONTENT_KEYS:
                continue
            _walk(child, context, key=name, port=local_port)
        return
    if isinstance(node, list | tuple):
        if key in _HOST_LIST_KEYS:
            for entry in node:
                _check_host_entry(entry, None, context)
            return
        for item in node:
            _walk(item, context, key=key if key in _URL_LIST_KEYS else "", port=port)
        return
    if not isinstance(node, str) or _skippable(node):
        return
    if key in _URL_KEYS or key in _URL_LIST_KEYS:
        _check_url(node, context)
    elif key in _DSN_KEYS:
        if _SCHEME.match(node.strip()):
            _check_uri(node, context)
        else:  # a libpq keyword DSN ("host=... port=...")
            assert_source_dsn(node, context=context)
    elif key in _HOST_KEYS:
        # A host field may list several hosts ("h1:27017,h2:27017" — MongoDB
        # replica-set seeds): each is validated on its own, so legitimate seeds
        # are accepted and any internal member is still refused.
        for entry in (part.strip() for part in node.split(",")):
            if entry:
                _check_host_entry(entry, port, context)
    elif key in _HOST_LIST_KEYS:
        for entry in _split_hosts(node):
            _check_host_entry(entry, None, context)
    elif _SCHEME.match(node.strip()):
        _check_uri(node, context)


def _connector_built_destinations(
    source_type: str, connection_config: Mapping[str, Any], context: str
) -> None:
    """Destinations a connector builds from non-URL fields (checked with its own builder)."""
    if source_type == "azure_blob" and (
        connection_config.get("connection_string") or connection_config.get("account_name")
    ):
        from app.ingestion.connectors.azure_blob_connector import azure_blob_endpoints

        for endpoint in azure_blob_endpoints(connection_config):
            assert_source_url(endpoint, context=context)
    elif source_type == "servicenow" and connection_config.get("instance"):
        from app.ingestion.connectors.servicenow_connector import _instance_base_url

        assert_source_url(
            _instance_base_url(str(connection_config["instance"])), context=context
        )
    elif source_type == "zendesk" and connection_config.get("subdomain"):
        from app.ingestion.connectors.zendesk_connector import _zendesk_base

        _zendesk_base(connection_config["subdomain"])


def assert_source_config_egress(source_type: str, connection_config: Mapping[str, Any]) -> None:
    """Raise :class:`ConnectorEgressBlockedError` if any destination in the config is internal.

    Blocking (DNS) — run it with ``asyncio.to_thread`` from async code.
    """
    if not isinstance(connection_config, Mapping):
        raise ConnectorEgressBlockedError("connection_config must be an object")
    context = f"{source_type or 'source'}.config"
    _walk(connection_config, context)
    _connector_built_destinations(source_type, connection_config, context)
