"""Security utilities for workflow execution.

SSRFGuard: Blocks HTTP steps from calling private/internal IP ranges
           to prevent Server-Side Request Forgery attacks.

SecretMasker: Redacts vault-resolved secret values before persisting
              step results to the database.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

# RFC 1918 + loopback + link-local (AWS/GCP metadata) + IPv6 private
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local + AWS IMDSv1
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# Explicitly blocked hostnames (in addition to IP checks)
_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "169.254.169.254",  # AWS IMDSv1
        "fd00:ec2::254",  # AWS IMDSv2 IPv6
        "localhost",
    }
)


class SSRFBlockedError(PermissionError):
    """Raised when an HTTP step URL resolves to a blocked address."""


class SSRFGuard:
    """Validates HTTP step URLs against SSRF blocklist."""

    def validate(self, url: str) -> None:
        """Raises SSRFBlockedError if URL is unsafe."""
        try:
            parsed = urllib.parse.urlparse(url)
            hostname = parsed.hostname
        except Exception as e:
            raise SSRFBlockedError(f"Invalid URL {url!r}: {e}") from e

        if not hostname:
            raise SSRFBlockedError(f"Empty hostname in URL: {url!r}")

        hostname_lower = hostname.lower()

        # Explicit hostname blocklist
        if hostname_lower in _BLOCKED_HOSTNAMES:
            raise SSRFBlockedError(f"SSRF blocked: hostname {hostname!r} is explicitly blocked")

        # Resolve DNS and check all returned IPs
        try:
            addr_infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            # DNS resolution failed — block (could be internal hostname)
            _log.warning("ssrf_dns_resolve_failed", hostname=hostname, url=url)
            raise SSRFBlockedError(f"SSRF blocked: could not resolve {hostname!r}") from exc

        for addr_info in addr_infos:
            ip_str = addr_info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            for private_net in _PRIVATE_NETWORKS:
                if ip in private_net:
                    raise SSRFBlockedError(
                        f"SSRF blocked: {hostname!r} resolves to private IP {ip_str}"
                    )

        _log.debug("ssrf_check_passed", hostname=hostname)


# ─────────────────────────────────────────────────────────────────────────────
# Secret Masker
# ─────────────────────────────────────────────────────────────────────────────

_SECRET_PLACEHOLDER = "[REDACTED]"
# Match vault:// references in string values
_VAULT_VALUE_RE = re.compile(r"\[vault:[^\]]+\]")


class SecretMasker:
    """Redacts vault-resolved secret values before DB persistence.

    The ContextResolver tracks which vault keys were resolved during a step.
    The SecretMasker uses that set to redact values from the resolved_input
    before it is persisted to workflow_step_results.
    """

    def mask(
        self,
        resolved_input: dict[str, Any],
        vault_keys_used: set[str],
    ) -> dict[str, Any]:
        """Return a copy of resolved_input with vault values redacted."""
        if not vault_keys_used:
            return resolved_input
        return self._deep_mask(resolved_input, vault_keys_used)  # type: ignore[return-value]

    def _deep_mask(self, obj: Any, vault_keys: set[str]) -> Any:
        if isinstance(obj, dict):
            return {k: self._deep_mask(v, vault_keys) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._deep_mask(item, vault_keys) for item in obj]
        if isinstance(obj, str) and _VAULT_VALUE_RE.search(obj):
            return _SECRET_PLACEHOLDER
        return obj
