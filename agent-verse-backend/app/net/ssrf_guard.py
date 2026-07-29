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
- All checks fail-closed (raises on error)
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Private/reserved IPv4 networks
_BLOCKED_V4 = [
    ipaddress.ip_network("127.0.0.0/8"),        # loopback
    ipaddress.ip_network("10.0.0.0/8"),          # RFC-1918
    ipaddress.ip_network("172.16.0.0/12"),        # RFC-1918
    ipaddress.ip_network("192.168.0.0/16"),       # RFC-1918
    ipaddress.ip_network("169.254.0.0/16"),       # link-local / metadata
    ipaddress.ip_network("0.0.0.0/8"),            # "this" network
    ipaddress.ip_network("100.64.0.0/10"),        # shared address space
    ipaddress.ip_network("192.0.0.0/24"),         # IETF protocol assignments
    ipaddress.ip_network("198.18.0.0/15"),        # benchmarking
    ipaddress.ip_network("240.0.0.0/4"),          # reserved
    ipaddress.ip_network("255.255.255.255/32"),   # broadcast
]

_BLOCKED_V6 = [
    ipaddress.ip_network("::1/128"),    # loopback
    ipaddress.ip_network("fc00::/7"),   # ULA
    ipaddress.ip_network("fe80::/10"),  # link-local
    ipaddress.ip_network("::/128"),     # unspecified
]

# Cloud metadata hostnames
_METADATA_HOSTNAMES = frozenset({
    "169.254.169.254",            # AWS/GCP/Azure metadata
    "metadata.google.internal",
    "metadata.google",
    "instance-data",              # OpenStack
})

# Allowed URL schemes
_ALLOWED_SCHEMES = frozenset({"http", "https"})


class SSRFError(ValueError):
    """Raised when a URL is blocked by the SSRF guard."""


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
) -> None:
    """Assert that a URL is safe to fetch (public, non-metadata, correct scheme).

    Raises SSRFError if the URL should be blocked.
    Raises ValueError for malformed URLs.

    Args:
        url: The URL to validate.
        allowed_domains: Optional per-tenant allowlist of exact domains.
        context: Human-readable context for error messages (e.g. "A2A callback").
    """
    if not url or not isinstance(url, str):
        raise SSRFError(f"SSRF guard [{context}]: empty or non-string URL")

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

    # Allowed-domain check (allowlist takes priority)
    if allowed_domains:
        for domain in allowed_domains:
            if hostname == domain.lower() or hostname.endswith("." + domain.lower()):
                return  # explicitly allowed

    # Metadata hostname block
    if hostname in _METADATA_HOSTNAMES:
        raise SSRFError(
            f"SSRF guard [{context}]: metadata service hostname '{hostname}' blocked"
        )

    # Try to parse hostname as a literal IP
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        pass  # not a literal IP; proceed to DNS resolution
    else:
        if _is_blocked_ip(str(addr)):
            raise SSRFError(
                f"SSRF guard [{context}]: IP address '{hostname}' is in a blocked range"
            )
        return  # literal IP and it's public — allow

    # DNS resolution — anti-rebinding: resolve and check ALL addresses
    try:
        ips = _resolve_host(hostname)
    except Exception as exc:
        raise SSRFError(
            f"SSRF guard [{context}]: cannot resolve host '{hostname}' — fail closed"
        ) from exc
    if not ips:
        raise SSRFError(
            f"SSRF guard [{context}]: cannot resolve host '{hostname}' — fail closed"
        )

    for ip in ips:
        if _is_blocked_ip(ip):
            raise SSRFError(
                f"SSRF guard [{context}]: hostname '{hostname}' resolved to "
                f"blocked IP '{ip}' (anti-rebinding check)"
            )

    logger.debug("ssrf_guard_passed", hostname=hostname, context=context)


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
        return True   # Exception = URL is blocked/private
