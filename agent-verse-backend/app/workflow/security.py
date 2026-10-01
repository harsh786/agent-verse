"""Security utilities for workflow execution.

SSRFGuard: Blocks HTTP steps from calling private/internal IP ranges
           to prevent Server-Side Request Forgery attacks.

SecretMasker: Redacts vault-resolved secret values before persisting
              step results to the database.
"""

from __future__ import annotations

import re
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)


class SSRFBlockedError(PermissionError):
    """Raised when an HTTP step URL resolves to a blocked address."""


class SSRFGuard:
    """Validates workflow egress URLs with the CENTRAL guard (app.net.ssrf_guard).

    This used to be a workflow-local copy with its own blocklist. It missed
    IPv4-mapped IPv6 literals ([::ffff:169.254.169.254]), CGNAT cloud-metadata
    addresses (100.100.100.200) and the unspecified address, and it resolved DNS
    separately from the request. All rules now live in one place; the actual
    connection must additionally use ``app.net.ssrf_guard.public_async_client``
    so the dialled address is the checked one (DNS-rebinding defence).
    """

    def validate(self, url: str) -> None:
        """Raises SSRFBlockedError if URL is unsafe (fail closed)."""
        from app.net import ssrf_guard

        try:
            ssrf_guard.assert_public_url(url, context="workflow")
        except Exception as exc:  # SSRFError, malformed URL, resolver failure
            _log.warning("workflow_ssrf_blocked", url=str(url)[:200], error=str(exc))
            raise SSRFBlockedError(str(exc)) from exc


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
