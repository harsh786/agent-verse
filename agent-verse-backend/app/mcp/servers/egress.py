"""Connect-time egress pinning for built-in handlers on tenant calls (BUILTIN-PINNING).

Tenant-supplied endpoints are checked against the connector egress policy when
they are read (:func:`app.mcp.servers.credentials.tenant_getenv`). A check
followed by an ordinary connect is a DNS-rebinding window: the driver resolves
the name again and a short-TTL answer can flip from the checked public address
to 127.0.0.1 / 10.x / 169.254.169.254. Inside a tenant call every connection is
therefore dialled to an address that was checked *for that connection*:

* HTTP (httpx/httpcore — ~300 built-ins): the httpcore network backends resolve
  and check the host at connect time and dial the checked IP; TLS still uses the
  request's hostname for SNI and certificate verification. Unix sockets are
  refused. An operator-configured HTTP(S) proxy (process env) is dialled as is:
  the proxy resolves the target itself.
* PostgreSQL (asyncpg): DSN hosts replaced by checked IPs; a verifying sslmode
  gets a context that verifies the configured hostname.
* MySQL (aiomysql): connected to the checked IP (hostname-verifying TLS context).
* Redis (redis.asyncio): connections dial the checked IP, TLS keeps the hostname.
* Snowflake: the account host is checked and pinned for the call.

Outside a tenant call (platform code, direct handler calls) nothing changes.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import threading
from typing import Any
from urllib.parse import urlsplit

from app.observability.logging import get_logger

_log = get_logger(__name__)
_CONTEXT = "mcp built-in connect"
_install_lock = threading.Lock()
_installed = False
# The un-patched httpcore connect functions (kept here so tests can observe them).
_ORIGINAL: dict[str, Any] = {}


def _in_tenant_scope() -> bool:
    from app.mcp.servers.credentials import in_tenant_scope

    return in_tenant_scope()


def _proxy_hosts() -> set[str]:
    hosts: set[str] = set()
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        value = os.environ.get(var, "")
        if value:
            host = urlsplit(value if "://" in value else f"http://{value}").hostname
            if host:
                hosts.add(host.lower())
    return hosts


def checked_addresses(host: str, port: object = None) -> list[str]:
    """Resolve ``host`` and return the addresses the egress policy allows.

    Raises ConnectorEgressBlockedError (an SSRFError) when any address is blocked.
    """
    from app.ingestion.connector_egress import assert_source_host

    raw = str(host).strip("[]")
    ips = assert_source_host(raw, port or None, context=_CONTEXT)
    return ips or [raw]  # an operator-allowlisted name unresolvable from here


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def install_http_pinning() -> None:
    """Patch httpcore's backends once; the patch only acts inside a tenant call."""
    global _installed
    with _install_lock:
        if _installed:
            return
        from httpcore._backends.anyio import AnyIOBackend
        from httpcore._backends.sync import SyncBackend

        from app.net.ssrf_guard import SSRFError

        _ORIGINAL["async_tcp"] = AnyIOBackend.connect_tcp
        _ORIGINAL["async_unix"] = AnyIOBackend.connect_unix_socket
        _ORIGINAL["sync_tcp"] = SyncBackend.connect_tcp
        _ORIGINAL["sync_unix"] = SyncBackend.connect_unix_socket

        async def connect_tcp(self: Any, host: str, port: int, *args: Any, **kwargs: Any) -> Any:
            original = _ORIGINAL["async_tcp"]
            if not _in_tenant_scope() or host.lower() in _proxy_hosts():
                return await original(self, host, port, *args, **kwargs)
            ips = await asyncio.to_thread(checked_addresses, host, port)
            last: Exception | None = None
            for ip in ips:
                try:
                    return await original(self, ip, port, *args, **kwargs)
                except Exception as exc:  # try the next checked address
                    last = exc
            assert last is not None
            raise last

        async def connect_unix_socket(self: Any, *args: Any, **kwargs: Any) -> Any:
            if _in_tenant_scope():
                raise SSRFError("SSRF guard: unix sockets are not reachable on a tenant call")
            return await _ORIGINAL["async_unix"](self, *args, **kwargs)

        def sync_connect_tcp(self: Any, host: str, port: int, *args: Any, **kwargs: Any) -> Any:
            original = _ORIGINAL["sync_tcp"]
            if not _in_tenant_scope() or host.lower() in _proxy_hosts():
                return original(self, host, port, *args, **kwargs)
            last: Exception | None = None
            for ip in checked_addresses(host, port):
                try:
                    return original(self, ip, port, *args, **kwargs)
                except Exception as exc:
                    last = exc
            assert last is not None
            raise last

        def sync_connect_unix_socket(self: Any, *args: Any, **kwargs: Any) -> Any:
            if _in_tenant_scope():
                raise SSRFError("SSRF guard: unix sockets are not reachable on a tenant call")
            return _ORIGINAL["sync_unix"](self, *args, **kwargs)

        AnyIOBackend.connect_tcp = connect_tcp  # type: ignore[method-assign]
        AnyIOBackend.connect_unix_socket = connect_unix_socket  # type: ignore[method-assign]
        SyncBackend.connect_tcp = sync_connect_tcp  # type: ignore[method-assign]
        SyncBackend.connect_unix_socket = sync_connect_unix_socket  # type: ignore[method-assign]
        _installed = True


# ── database drivers ─────────────────────────────────────────────────────────


def pinned_asyncpg_kwargs(dsn: str, pins: Any) -> dict[str, Any]:
    """asyncpg connect kwargs dialling the checked IPs (asyncpg may resolve in libuv)."""
    from urllib.parse import parse_qsl

    from app.ingestion.connector_egress import dsn_with_pinned_hosts, pinned_hostname_ssl

    query = {k.lower(): v for k, v in parse_qsl(urlsplit(dsn).query, keep_blank_values=True)}
    kwargs: dict[str, Any] = {"dsn": dsn_with_pinned_hosts(dsn, pins)}
    if query.get("sslmode", "").lower() in {"verify-ca", "verify-full"}:
        kwargs["ssl"] = pinned_hostname_ssl(pins)
    return kwargs


def pinned_redis_client(url: str, ips: list[str] | str, **kwargs: Any) -> Any:
    """A redis.asyncio client whose connections dial only ``ips`` (checked
    addresses, tried in order); TLS keeps the configured hostname for SNI and
    certificate verification."""
    import redis.asyncio as aioredis

    addresses = [ips] if isinstance(ips, str) else list(ips)
    hostname = urlsplit(url).hostname or ""
    pool = aioredis.ConnectionPool.from_url(url, **kwargs)
    base = pool.connection_class

    class _PinnedConnection(base):  # type: ignore[valid-type,misc]
        _pin_ip = addresses[0]

        def _connection_arguments(self) -> Any:
            args = dict(super()._connection_arguments())
            args["host"] = self._pin_ip
            if args.get("ssl") is not None and hostname and not _is_ip(hostname):
                args["server_hostname"] = hostname
            return args

        async def _connect(self) -> None:
            last: Exception | None = None
            for ip in addresses:
                self._pin_ip = ip
                try:
                    await super()._connect()
                    return
                except OSError as exc:  # try the next checked address
                    last = exc
            assert last is not None
            raise last

    pool.connection_class = _PinnedConnection
    return aioredis.Redis(connection_pool=pool)


__all__ = [
    "checked_addresses",
    "install_http_pinning",
    "pinned_asyncpg_kwargs",
    "pinned_redis_client",
]
