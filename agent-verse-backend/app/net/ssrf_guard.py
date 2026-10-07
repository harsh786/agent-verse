"""SSRF egress guard — prevents Server-Side Request Forgery.

`assert_public_url(url)` resolves DNS and rejects:
- Loopback addresses (127.x.x.x, ::1)
- RFC-1918 private ranges (10/8, 172.16/12, 192.168/16)
- Link-local (169.254/16, fe80::/10)
- AWS/GCP/Azure metadata endpoints (169.254.169.254, metadata.google.internal)
- Non-http/https schemes
- Empty or malformed URLs

Features:
- Re-validates after DNS resolution (anti-rebinding defence)
- Per-tenant allowed-domain allowlist (override via env or runtime config)
- Operator-only allowed NETWORKS (``allowed_networks``; EGRESS-NET): private
  ranges an operator opens for LAN sources. Only the ingestion egress policy
  passes them — never a tenant-supplied list. Link-local / metadata / 0.0.0.0
  stay unreachable even inside a listed network.
- All checks fail-closed (raises on error)
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Private/reserved IPv4 networks
_BLOCKED_V4 = [
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("10.0.0.0/8"),  # RFC-1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC-1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC-1918
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / metadata
    ipaddress.ip_network("0.0.0.0/8"),  # "this" network
    ipaddress.ip_network("100.64.0.0/10"),  # shared address space
    ipaddress.ip_network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
    ipaddress.ip_network("255.255.255.255/32"),  # broadcast
]

_BLOCKED_V6 = [
    ipaddress.ip_network("::1/128"),  # loopback
    ipaddress.ip_network("fc00::/7"),  # ULA
    ipaddress.ip_network("fe80::/10"),  # link-local
    ipaddress.ip_network("::/128"),  # unspecified
]

# Cloud metadata hostnames
_METADATA_HOSTNAMES = frozenset(
    {
        "169.254.169.254",  # AWS/GCP/Azure metadata
        "metadata.google.internal",
        "metadata.google",
        "instance-data",  # OpenStack
        "100.100.100.200",  # Alibaba Cloud (inside the carrier-grade 100.64/10 range)
    }
)

# Allowed URL schemes
_ALLOWED_SCHEMES = frozenset({"http", "https"})


# Never reachable, not even for an operator-allowlisted domain: link-local
# (cloud metadata 169.254.169.254, fd00:ec2::254 via ULA is separately private),
# the unspecified address and multicast.
_ALWAYS_BLOCKED = [
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS IMDS over IPv6
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba Cloud metadata (not link-local)
]


# What stays blocked when link-local access is deliberately opened.
_ALWAYS_BLOCKED_NO_LINK_LOCAL = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
    # Alibaba Cloud metadata is inside 100.64/10 (opened as "private"), not link-local:
    # ALLOW_LINK_LOCAL_NETWORK_ACCESS must not open it, by name OR by resolved address.
    ipaddress.ip_network("100.100.100.200/32"),
]


class SSRFError(ValueError):
    """Raised when a URL is blocked by the SSRF guard."""


IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Every address. As ``allowed_networks`` it means "private and internal
# addresses are reachable" — the never-reachable ones (cloud metadata,
# link-local, 0.0.0.0, multicast) and metadata hostnames stay blocked.
ANY_NETWORK: list[IPNetwork] = [
    ipaddress.ip_network("0.0.0.0/0"),
    ipaddress.ip_network("::/0"),
]


def private_network_access_enabled() -> bool:
    """``ALLOW_PRIVATE_NETWORK_ACCESS`` (default true, owner decision 2026-10-06).

    On: ingestion sources (HTTP, MinIO/S3, crawl, repositories), connectors
    (MongoDB and every database/broker source, MCP / tool connectors) and model
    endpoints (LLM base URLs, embedding / reranker / fine-tune endpoints) may
    reach private and internal hosts and IPs in every environment. Cloud
    metadata, link-local and 0.0.0.0 stay blocked. Off: the previous policy
    (public only, plus the operator allowlists).
    """
    import os

    raw = os.getenv("ALLOW_PRIVATE_NETWORK_ACCESS")
    if raw is None or not raw.strip():
        try:
            from app.core.config import get_settings

            return bool(getattr(get_settings(), "allow_private_network_access", True))
        except Exception:  # pragma: no cover - settings unavailable: the default
            return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def link_local_access_enabled() -> bool:
    """``ALLOW_LINK_LOCAL_NETWORK_ACCESS`` (default false).

    The one thing ``ALLOW_PRIVATE_NETWORK_ACCESS`` does not open: link-local
    addresses (169.254.0.0/16, fe80::/10), which is where cloud instance-metadata
    services (AWS/GCP/Azure 169.254.169.254) live and hand out the node's
    credentials. On an on-prem LAN nothing legitimate needs them. Set this to true
    only to reach such an address on purpose (e.g. a metadata-service test double).
    0.0.0.0 and multicast stay blocked either way.
    """
    import os

    return os.getenv("ALLOW_LINK_LOCAL_NETWORK_ACCESS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_metadata_host(host: str) -> bool:
    """True for a literal host that is a cloud-metadata name/address or another
    never-reachable address (0.0.0.0, and link-local unless it was opened with
    ``ALLOW_LINK_LOCAL_NETWORK_ACCESS``). DNS is not consulted."""
    h = (host or "").strip().lower().strip(".")
    if not h:
        return True
    if h in _METADATA_HOSTNAMES and not link_local_access_enabled():
        return True
    try:
        ipaddress.ip_address(h)
    except ValueError:
        return False
    return _is_always_blocked_ip(h)


def private_access_networks(
    fallback: list[IPNetwork] | None = None,
) -> list[IPNetwork] | None:
    """``allowed_networks`` for an ingestion / connector / model-endpoint call:
    every network when private access is on, else *fallback* (the operator's
    own allowlist, or ``None``)."""
    return ANY_NETWORK if private_network_access_enabled() else fallback


def parse_allowed_networks(raw: str) -> list[IPNetwork]:
    """The CIDR entries (``a.b.c.d/n``, ``fc00::/7``) of a comma-separated list.

    Entries without a ``/`` are hostnames / single IPs (handled as allowed
    domains) and are skipped here. A malformed CIDR is dropped and logged —
    it never widens the policy.
    """
    networks: list[IPNetwork] = []
    for entry in (part.strip() for part in raw.split(",")):
        if "/" not in entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("ssrf_allowed_network_invalid", entry=entry[:64])
    return networks


def _in_allowed_networks(ip_str: str, networks: list[IPNetwork] | None) -> bool:
    """True when ``ip_str`` is inside an operator-allowed network and is not one of
    the never-reachable addresses (metadata / link-local / 0.0.0.0 / multicast)."""
    if not networks or _is_always_blocked_ip(ip_str):
        return False
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return any(addr.version == net.version and addr in net for net in networks)


def _is_always_blocked_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    blocked = _ALWAYS_BLOCKED_NO_LINK_LOCAL if link_local_access_enabled() else _ALWAYS_BLOCKED
    return addr.is_multicast or any(
        addr.version == net.version and addr in net for net in blocked
    )


def _is_blocked_ip(ip_str: str) -> bool:
    """Return True if the IP address is in a blocked range."""
    try:
        addr = ipaddress.ip_address(ip_str)
        if not addr.is_global or addr.is_multicast or addr.is_reserved:
            return True
        if isinstance(addr, ipaddress.IPv4Address):
            return any(addr in net for net in _BLOCKED_V4)
        return any(addr in net for net in _BLOCKED_V6)
    except ValueError:
        return True  # fail-closed on parse error


def _resolve_host(hostname: str) -> list[str]:
    """Resolve hostname to IP addresses.

    Raises on any failure — callers must fail closed on exception.
    """
    results = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return [str(r[4][0]) for r in results]


def assert_public_url(
    url: str,
    *,
    allowed_domains: list[str] | None = None,
    context: str = "",
    allowed_networks: list[IPNetwork] | None = None,
) -> list[str]:
    """Assert that a URL is safe to fetch (public, non-metadata, correct scheme).

    Raises SSRFError if the URL should be blocked.
    Raises ValueError for malformed URLs.

    Args:
        url: The URL to validate.
        allowed_domains: Optional per-tenant allowlist of exact domains.
        context: Human-readable context for error messages (e.g. "A2A callback").
        allowed_networks: Operator-only private networks whose addresses are
            reachable (never link-local / metadata / 0.0.0.0). Every resolved
            address must still be public or inside one of them.
    """
    if not url or not isinstance(url, str):
        raise SSRFError(f"SSRF guard [{context}]: empty or non-string URL")

    # ALLOW_PRIVATE_NETWORK_ACCESS (default on): EVERY caller of the guard — tools,
    # workflow steps, webhooks, triggers, notifications, browsers, connectors —
    # may reach private / internal addresses, not just the ingestion and connector
    # paths that used to pass allowed_networks. Off: the caller's own list (or
    # public-only). Link-local / metadata / 0.0.0.0 stay blocked either way
    # (see link_local_access_enabled).
    allowed_networks = private_access_networks(allowed_networks)

    # Parse URL
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise SSRFError(f"SSRF guard [{context}]: malformed URL: {exc}") from exc

    # Scheme check
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(
            f"SSRF guard [{context}]: scheme '{scheme}' not allowed. "
            f"Only {sorted(_ALLOWED_SCHEMES)} are permitted."
        )

    hostname = (parsed.hostname or "").lower().strip(".")
    if not hostname:
        raise SSRFError(f"SSRF guard [{context}]: no hostname in URL '{url}'")

    # Allowed-domain check. An operator allowlist may open PRIVATE ranges for
    # its own (sub)domains — that is its purpose — but it used to skip every IP
    # check (``return []``), so an allowlisted name whose DNS points at the
    # cloud metadata service (169.254.169.254) or 0.0.0.0 was fetched. It is
    # still resolved (fail closed) and the always-blocked ranges still apply.
    if allowed_domains:
        for domain in allowed_domains:
            if hostname == domain.lower() or hostname.endswith("." + domain.lower()):
                if hostname in _METADATA_HOSTNAMES and not link_local_access_enabled():
                    raise SSRFError(
                        f"SSRF guard [{context}]: metadata service hostname '{hostname}' blocked"
                    )
                try:
                    allowed_ips = _resolve_host(hostname)
                except Exception as exc:
                    # Fail closed (SSRF-02): this used to return [] — nothing
                    # checked — trusting every caller to connect through the
                    # pinned client. A driver or plain client resolves the name
                    # itself, to whatever it points at by then.
                    logger.warning("ssrf_allowlisted_host_unresolved", hostname=hostname)
                    raise SSRFError(
                        f"SSRF guard [{context}]: cannot resolve allowlisted host "
                        f"'{hostname}' — fail closed"
                    ) from exc
                if not allowed_ips:
                    raise SSRFError(
                        f"SSRF guard [{context}]: cannot resolve allowlisted host "
                        f"'{hostname}' — fail closed"
                    )
                for ip in allowed_ips:
                    if _is_always_blocked_ip(ip):
                        raise SSRFError(
                            f"SSRF guard [{context}]: allowlisted host '{hostname}' resolved "
                            f"to never-reachable IP '{ip}'"
                        )
                return allowed_ips

    # Metadata hostname block
    if hostname in _METADATA_HOSTNAMES and not link_local_access_enabled():
        raise SSRFError(f"SSRF guard [{context}]: metadata service hostname '{hostname}' blocked")

    # Try to parse hostname as a literal IP
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        pass  # not a literal IP; proceed to DNS resolution
    else:
        if _in_allowed_networks(str(addr), allowed_networks):
            return [str(addr)]  # inside an operator-allowed network
        if _is_blocked_ip(str(addr)):
            raise SSRFError(
                f"SSRF guard [{context}]: IP address '{hostname}' is in a blocked range"
            )
        return [str(addr)]  # literal IP and it's public — allow

    # DNS resolution — anti-rebinding: resolve and check ALL addresses
    try:
        ips = _resolve_host(hostname)
    except Exception as exc:
        raise SSRFError(
            f"SSRF guard [{context}]: cannot resolve host '{hostname}' — fail closed"
        ) from exc
    if not ips:
        raise SSRFError(f"SSRF guard [{context}]: cannot resolve host '{hostname}' — fail closed")

    for ip in ips:
        if _is_blocked_ip(ip) and not _in_allowed_networks(ip, allowed_networks):
            raise SSRFError(
                f"SSRF guard [{context}]: hostname '{hostname}' resolved to "
                f"blocked IP '{ip}' (anti-rebinding check)"
            )

    logger.debug("ssrf_guard_passed", hostname=hostname, context=context)
    return ips


async def assert_public_url_async(
    url: str,
    *,
    context: str = "",
    allowed_domains: list[str] | None = None,
    allowed_networks: list[IPNetwork] | None = None,
) -> list[str]:
    """:func:`assert_public_url` without blocking the event loop on DNS."""
    import asyncio

    return await asyncio.to_thread(
        assert_public_url,
        url,
        context=context,
        allowed_domains=allowed_domains,
        allowed_networks=allowed_networks,
    )


async def request_public(
    client: Any,
    method: str,
    url: str,
    *,
    context: str = "",
    max_redirects: int = 5,
    allowed_domains: list[str] | None = None,
    allowed_networks: list[IPNetwork] | None = None,
    stream: bool = False,
    **kwargs: Any,
) -> Any:
    """Send ``method url`` with ``client`` re-validating the URL at EVERY hop.

    ``client`` must be an ``httpx.AsyncClient`` created with
    ``follow_redirects=False``: with automatic redirects a public URL can 302 to
    an internal address (169.254.169.254, 10.x, localhost) after the first check.
    Same pattern as app/tools/http_tool.py. Raises :class:`SSRFError`.

    ``stream=True`` returns the final response unread (``client.send(...,
    stream=True)``) so the caller can cap the body while reading it; the caller
    must ``await resp.aclose()``. Redirect hops are closed here.
    """
    current_url, current_method = url, method.upper()
    for _hop in range(max_redirects + 1):
        await assert_public_url_async(
            current_url,
            context=context,
            allowed_domains=allowed_domains,
            allowed_networks=allowed_networks,
        )
        if stream:
            resp = await client.send(
                client.build_request(current_method, current_url, **kwargs), stream=True
            )
        else:
            resp = await client.request(current_method, current_url, **kwargs)
        if not resp.is_redirect:
            return resp
        location = resp.headers.get("location", "")
        if not location:
            return resp
        if stream:
            await resp.aclose()
        current_url = str(resp.url.join(location))
        if resp.status_code in (301, 302, 303) and current_method not in ("GET", "HEAD"):
            current_method = "GET"
            kwargs = {k: v for k, v in kwargs.items() if k not in ("json", "content", "data")}
    raise SSRFError(f"SSRF guard [{context}]: too many redirects (>{max_redirects})")


def resolve_and_check_host(
    host: str,
    *,
    allowed_domains: list[str] | None = None,
    allowed_networks: list[IPNetwork] | None = None,
) -> list[str]:
    """Resolve *host* and validate every address; return the checked IPs.

    Used at CONNECT time by :class:`PinnedNetworkBackend`, so the address the
    socket connects to is the address that was checked.
    """
    return assert_public_url(
        f"http://{host}/" if ":" not in host else f"http://[{host}]/",
        allowed_domains=allowed_domains,
        context="connect",
        allowed_networks=allowed_networks,
    )


class PinnedNetworkBackend:
    """httpcore network backend that resolves + validates the host AT CONNECT.

    ``assert_public_url`` followed by an ordinary client request is
    validate-then-connect: the client resolves the name again, and a DNS answer
    with a tiny TTL can flip from a public IP (checked) to 127.0.0.1 / 10.x /
    169.254.169.254 (connected) — DNS rebinding. This backend resolves once,
    rejects blocked addresses, and connects the socket to the checked IP. TLS
    still uses the request's hostname for SNI and certificate verification
    (httpcore passes the origin host to ``start_tls``, not the connect address).
    """

    def __init__(
        self,
        *,
        allowed_domains: list[str] | None = None,
        allowed_networks: list[IPNetwork] | None = None,
    ) -> None:
        import httpcore

        self._inner = httpcore.AnyIOBackend()
        self._allowed_domains = allowed_domains
        self._allowed_networks = allowed_networks

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        import asyncio

        ips = await asyncio.to_thread(
            resolve_and_check_host,
            host,
            allowed_domains=self._allowed_domains,
            allowed_networks=self._allowed_networks,
        )
        if not ips:
            raise SSRFError(f"SSRF guard [connect]: no checked address for '{host}'")
        last_exc: Exception | None = None
        for ip in ips:
            try:
                return await self._inner.connect_tcp(
                    ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # try the next checked address
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> Any:
        raise SSRFError("SSRF guard: unix sockets are not reachable from a public client")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def public_async_client(
    *,
    allowed_domains: list[str] | None = None,
    allowed_networks: list[IPNetwork] | None = None,
    **kwargs: Any,
) -> Any:
    """An ``httpx.AsyncClient`` whose connections are pinned to validated IPs.

    Redirects are never followed automatically (``follow_redirects=False``) —
    use :func:`request_public`, which re-validates every hop. Proxy env vars are
    ignored (``trust_env=False``): a proxy would resolve the name itself.
    """
    import httpx

    kwargs["follow_redirects"] = False
    kwargs.setdefault("trust_env", False)
    transport = httpx.AsyncHTTPTransport()
    # httpcore>=1.0 AsyncConnectionPool keeps its backend here (pinned in
    # uv.lock; tests/net/test_ssrf_guard_pinning.py fails if it moves).
    transport._pool._network_backend = PinnedNetworkBackend(
        allowed_domains=allowed_domains, allowed_networks=allowed_networks
    )
    return httpx.AsyncClient(transport=transport, **kwargs)


class PinnedSyncNetworkBackend:
    """Synchronous twin of :class:`PinnedNetworkBackend` (for ``httpx.Client``)."""

    def __init__(self, *, allowed_domains: list[str] | None = None) -> None:
        import httpcore

        self._inner = httpcore.SyncBackend()
        self._allowed_domains = allowed_domains

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        ips = resolve_and_check_host(host, allowed_domains=self._allowed_domains)
        if not ips:
            raise SSRFError(f"SSRF guard [connect]: no checked address for '{host}'")
        last_exc: Exception | None = None
        for ip in ips:
            try:
                return self._inner.connect_tcp(
                    ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # try the next checked address
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    def connect_unix_socket(self, *args: Any, **kwargs: Any) -> Any:
        raise SSRFError("SSRF guard: unix sockets are not reachable from a public client")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


def public_client(*, allowed_domains: list[str] | None = None, **kwargs: Any) -> Any:
    """Synchronous :func:`public_async_client`: an ``httpx.Client`` pinned to checked IPs."""
    import httpx

    kwargs["follow_redirects"] = False
    kwargs.setdefault("trust_env", False)
    transport = httpx.HTTPTransport()
    transport._pool._network_backend = PinnedSyncNetworkBackend(allowed_domains=allowed_domains)
    return httpx.Client(transport=transport, **kwargs)


_WS_SCHEMES = {"ws": "http", "wss": "https"}
# Arguments that would let a caller steer the socket away from the checked IP.
_WS_PINNED_KWARGS = frozenset({"host", "port", "sock", "unix", "proxy"})


async def connect_public_websocket(
    ws_url: str,
    *,
    allowed_domains: list[str] | None = None,
    context: str = "websocket",
    **kwargs: Any,
) -> Any:
    """Open a ``websockets`` client connection pinned to validated IPs.

    ``websockets.connect(url)`` resolves the host itself, so checking the URL
    first and connecting afterwards leaves a DNS-rebinding window (public at
    check time, 127.0.0.1 / 169.254.169.254 at connect time). This resolves and
    checks once, then dials the socket to the checked address via ``host=``.
    The URI is unchanged, so the Host header, TLS SNI and certificate
    verification still use the hostname. Env proxies are disabled (a proxy
    would resolve the name itself); cross-origin redirects are refused by
    ``websockets`` when ``host`` is explicit, same-origin ones stay pinned.
    """
    import websockets

    overridden = _WS_PINNED_KWARGS & set(kwargs)
    if overridden:
        raise ValueError(
            f"connect_public_websocket: {sorted(overridden)} cannot override the pinned target"
        )
    scheme, sep, rest = (ws_url or "").partition("://")
    http_scheme = _WS_SCHEMES.get(scheme.lower()) if sep else None
    if http_scheme is None:
        raise SSRFError(f"SSRF guard [{context}]: only ws:// and wss:// URLs are allowed")
    ips = await assert_public_url_async(
        f"{http_scheme}://{rest}", context=context, allowed_domains=allowed_domains
    )
    if not ips:
        raise SSRFError(f"SSRF guard [{context}]: no checked address to connect to — fail closed")
    last_exc: Exception | None = None
    for ip in ips:
        try:
            return await websockets.connect(ws_url, host=ip, proxy=None, **kwargs)
        except (OSError, TimeoutError) as exc:  # try the next checked address
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def is_public_url(url: str, *, allowed_domains: list[str] | None = None) -> bool:
    """Non-raising version of assert_public_url. Returns False if blocked."""
    try:
        assert_public_url(url, allowed_domains=allowed_domains)
        return True
    except (SSRFError, ValueError):
        return False


def is_ssrf_blocked(url: str) -> bool:
    """Alias for SSRF protection check — returns True if URL is blocked (internal/private)."""
    try:
        assert_public_url(url)
        return False  # No exception = URL is public = not blocked
    except Exception:
        return True  # Exception = URL is blocked/private
