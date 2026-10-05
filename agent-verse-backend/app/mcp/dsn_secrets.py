"""Database connection strings are connector SECRETS (MDB-01 / A7).

A MongoDB (Postgres, MySQL, Redis) connection string carries the database
password in its userinfo. It used to be stored twice — the connector's top-level
``url`` and ``auth_config.url`` — in plaintext in Redis and Postgres, and both
copies were returned by ``GET /connectors``.

:func:`seal_connector_dsns` keeps ONE copy, sealed in the connector secret
store (vault-encrypted, ``app.mcp.connector_secrets``): ``auth_config`` holds
the vault reference, the top-level ``url`` becomes the ``builtin://`` dispatch
marker for a built-in, and ``display_url`` holds the non-secret display form
(:func:`mask_dsn`: no userinfo, secret-looking query values redacted) that the
API shows instead.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote_plus, urlsplit

if TYPE_CHECKING:
    from app.mcp.registry import MCPServerConfig

DSN_SCHEMES = (
    "mongodb://",
    "mongodb+srv://",
    "postgres://",
    "postgresql://",
    "mysql://",
    "redis://",
    "rediss://",
)
# auth_config keys a built-in database handler reads its connection string from.
URI_KEYS = ("uri", "connection_string", "url", "base_url", "mongodb_uri", "dsn")
_SECRET_QUERY_PARTS = ("password", "secret", "token", "key", "properties", "credential")
REDACTED = "<redacted>"


def is_dsn(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(DSN_SCHEMES)


_HOSTLIST = re.compile(r"^[A-Za-z0-9.\-_%\[\]:,]+$")


def mask_dsn(dsn: str) -> str:
    """The connection string without userinfo, secret-looking query values redacted.

    The userinfo is dropped up to the LAST '@' that is followed by a well-formed
    host list, so an unencoded ``# @ : / ? %`` inside a password can never leave
    part of it in the "host", "path" or "query" (NF-4). When no such '@' exists
    but one is present, nothing after the scheme is shown (fail closed).
    """
    text = dsn.strip()
    scheme, sep, rest = text.partition("://")
    if not sep:
        return REDACTED
    if "@" in rest:
        cut = -1
        for index in range(len(rest) - 1, -1, -1):
            if rest[index] != "@":
                continue
            hosts = re.split(r"[/?#]", rest[index + 1 :], maxsplit=1)[0]
            if hosts and _HOSTLIST.match(hosts):
                cut = index
                break
        if cut < 0:
            return f"{scheme.lower()}://{REDACTED}"
        rest = rest[cut + 1 :]
    rest = rest.partition("#")[0]
    match = re.match(r"^([^/?]*)(/?)([^?]*)\??(.*)$", rest, re.S)
    authority, slash, path, query = match.groups() if match else (rest, "", "", "")
    pairs = [p.partition("=") for p in re.split(r"[&;]", query) if p]
    masked_query = "&".join(
        f"{quote(unquote_plus(k), safe='')}="
        + (
            REDACTED
            if any(part in unquote_plus(k).lower() for part in _SECRET_QUERY_PARTS)
            else quote(unquote_plus(v), safe=":,")
        )
        for k, _, v in pairs
    )
    out = f"{scheme.lower()}://{authority}{slash}{path}"
    return f"{out}?{masked_query}" if masked_query else out


def dsn_hosts(dsn: str) -> str:
    """``host:port[,host:port]`` of a connection string (for display)."""
    return urlsplit(mask_dsn(dsn)).netloc


def seal_connector_dsns(
    config: MCPServerConfig, pending_secrets: dict[str, str], *, previous_display: str = ""
) -> MCPServerConfig:
    """Move every plaintext connection string of ``config`` into ``pending_secrets``.

    Returns a copy whose ``auth_config`` holds vault references (one per key that
    held a connection string), whose top-level ``url`` / ``base_url`` hold no
    connection string (``builtin://`` for a built-in, else the masked display
    form) and whose ``display_url`` is the masked display form.
    ``pending_secrets`` maps each reference to the plaintext to store.
    """
    from app.providers.vault import connector_secret_ref, is_connector_secret_ref

    auth = dict(config.auth_config or {})
    display = ""
    for key, value in list(auth.items()):
        if is_dsn(value):
            plain = str(value).strip()
            display = display or mask_dsn(plain)
            ref = connector_secret_ref(config.server_id, key)
            pending_secrets[ref] = plain
            auth[key] = ref
    url = config.url or config.base_url or ""
    if is_dsn(url):
        plain = url.strip()
        has_uri = any(is_connector_secret_ref(auth.get(k)) or is_dsn(auth.get(k)) for k in URI_KEYS)
        if not has_uri:
            key = "url" if "url" not in auth else "connection_string"
            ref = connector_secret_ref(config.server_id, key)
            pending_secrets[ref] = plain
            auth[key] = ref
            display = display or mask_dsn(plain)
        url = "builtin://" if config.builtin_type else (display or mask_dsn(plain))
    return config.model_copy(
        update={
            "auth_config": auth,
            "url": url,
            "base_url": url,
            "display_url": display or previous_display or config.display_url,
        }
    )


def config_has_plaintext_dsn(config: dict[str, Any]) -> bool:
    """True when a stored connector config still holds a connection string in clear."""
    if is_dsn(config.get("url")) or is_dsn(config.get("base_url")):
        return True
    auth = config.get("auth_config") or {}
    return isinstance(auth, dict) and any(is_dsn(v) for v in auth.values())


__all__ = [
    "DSN_SCHEMES",
    "REDACTED",
    "URI_KEYS",
    "config_has_plaintext_dsn",
    "dsn_hosts",
    "is_dsn",
    "mask_dsn",
    "seal_connector_dsns",
]
