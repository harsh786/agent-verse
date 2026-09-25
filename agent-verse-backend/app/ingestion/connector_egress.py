"""Egress guard for ingestion connectors.

Most connectors fetch a host the **tenant** supplies — Jira/Confluence
``base_url``, a self-hosted GitLab, a ServiceNow ``instance``, an Elasticsearch
``url``, web-crawl ``seed_urls``, a list of PDF ``urls``. Without a guard that is
a Server-Side Request Forgery primitive with an unusually direct payoff: whatever
the platform fetches is parsed, chunked, embedded and indexed into *the
requesting tenant's own* knowledge collection, where they can then simply
retrieve it. A tenant pointing a web-crawl source at
``http://169.254.169.254/latest/meta-data/iam/security-credentials/`` gets the
platform's cloud credentials delivered into their RAG index.

``app.net.ssrf_guard.assert_public_url`` already implements the check (private
ranges, link-local, metadata hostnames, scheme allowlist, and a post-DNS
re-check against rebinding). This module is the ingestion-side policy wrapper:
one import, one call, one consistent error, plus the operator-only escape hatch
an on-prem deployment needs when its Jira really does live on a LAN.

The escape hatch is deliberately **operator-scoped, never tenant-scoped**:
``INGESTION_ALLOW_INTERNAL_SOURCES`` plus an explicit
``INGESTION_INTERNAL_SOURCE_ALLOWLIST`` of hostnames. A value in
``connection_config`` can never widen it — that field is exactly what an attacker
controls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.net.ssrf_guard import SSRFError, assert_public_url
from app.observability.logging import get_logger

if TYPE_CHECKING:
    from app.ingestion.source_config import SourceConfig

_log = get_logger(__name__)

__all__ = ["ConnectorEgressBlocked", "assert_source_url", "source_url_is_allowed"]


class ConnectorEgressBlocked(SSRFError):
    """A connector tried to fetch a URL the egress policy forbids."""


def _operator_allowlist() -> tuple[bool, list[str]]:
    """Return ``(allow_internal, allowed_domains)`` from operator settings."""
    try:
        from app.core.config import get_settings

        settings = get_settings()
    except Exception:  # pragma: no cover - settings unavailable: fail closed
        return False, []
    allow = bool(getattr(settings, "ingestion_allow_internal_sources", False))
    raw = str(getattr(settings, "ingestion_internal_source_allowlist", "") or "")
    domains = [d.strip().lower() for d in raw.split(",") if d.strip()]
    return allow, domains


def assert_source_url(url: str, *, context: str, config: SourceConfig | None = None) -> None:
    """Raise :class:`ConnectorEgressBlocked` unless ``url`` is safe to fetch.

    Call this before *any* outbound request a connector makes to a host derived
    from ``connection_config`` — in ``validate_connection`` as well as
    ``get_delta``, since validation runs against the same attacker-supplied URL
    and returns its own response body/error to the caller.
    """
    del config  # tenant config must never widen the policy; kept for call-site clarity
    if not url:
        raise ConnectorEgressBlocked(f"SSRF guard [{context}]: empty URL")

    allow_internal, allowed_domains = _operator_allowlist()
    # The allowlist is only honoured when the operator has *also* turned the
    # escape hatch on. Both halves are env-only: an allowlist alone must not be
    # able to punch a hole, and connection_config can never reach either.
    effective_allowlist = allowed_domains if (allow_internal and allowed_domains) else None
    try:
        assert_public_url(url, allowed_domains=effective_allowlist, context=context)
    except (SSRFError, ValueError) as exc:
        _log.warning("connector_egress_blocked", context=context, error=str(exc)[:200])
        raise ConnectorEgressBlocked(str(exc)) from exc


def source_url_is_allowed(url: str, *, context: str) -> bool:
    """Non-raising form, for loops that skip bad URLs instead of aborting a sync."""
    try:
        assert_source_url(url, context=context)
        return True
    except (ConnectorEgressBlocked, SSRFError, ValueError):
        return False
