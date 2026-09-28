"""Authentication for inbound integration webhooks (auth-exempt ``/integrations/``).

These endpoints sit under a TenantMiddleware bypass prefix because the callers
(Alertmanager, Datadog, GitHub, Confluence, Notion, Zapier) cannot send an
AgentVerse API key. Several of them authenticated nothing: the Alertmanager
endpoint let anyone submit autonomous agent goals into the configured tenant;
the re-ingest webhooks took the tenant AND collection from request headers and
queued ingestion of an attacker-chosen source into any tenant's knowledge base
(knowledge poisoning); Datadog verified only when a secret happened to be set.

Every such endpoint now fails closed: unconfigured → 503, bad credential → 401.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import HTTPException

__all__ = [
    "reingest_signing_secret",
    "require_bearer_token",
    "require_hmac_body_signature",
    "verify_reingest_signature",
]


def _configured(env_var: str, label: str) -> str:
    value = (os.getenv(env_var) or "").strip()
    if not value:
        raise HTTPException(503, f"{label} webhook is not configured ({env_var} is unset)")
    return value


def require_bearer_token(authorization: str, *, env_var: str, label: str) -> None:
    expected = _configured(env_var, label)
    presented = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not presented or not hmac.compare_digest(expected, presented):
        raise HTTPException(401, f"Invalid {label} webhook credentials")


def require_hmac_body_signature(body: bytes, signature: str, *, env_var: str, label: str) -> None:
    secret = _configured(env_var, label)
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    presented = signature.removeprefix("sha256=").strip()
    if not presented or not hmac.compare_digest(expected, presented):
        raise HTTPException(401, f"Invalid {label} signature")


def reingest_signing_secret(tenant_id: str, collection_id: str) -> str | None:
    """Per-(tenant, collection) webhook secret derived from REINGEST_WEBHOOK_SECRET.

    Derivation (not storage): no table, nothing to leak; rotating the master
    secret rotates every collection's secret. ``None`` when not configured.
    """
    master = (os.getenv("REINGEST_WEBHOOK_SECRET") or "").strip()
    if not master:
        return None
    return hmac.new(
        master.encode(), f"reingest:{tenant_id}:{collection_id}".encode(), hashlib.sha256
    ).hexdigest()


def verify_reingest_signature(
    body: bytes, signature: str, *, tenant_id: str, collection_id: str
) -> None:
    """``signature`` = ``sha256=<hex HMAC-SHA256(collection secret, raw body)>``.

    The same scheme GitHub uses for ``X-Hub-Signature-256``, so a GitHub webhook
    configured with the collection's secret authenticates natively.
    """
    secret = reingest_signing_secret(tenant_id, collection_id)
    if secret is None:
        raise HTTPException(503, "Re-ingest webhooks are not configured (REINGEST_WEBHOOK_SECRET)")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    presented = signature.removeprefix("sha256=").strip()
    if not presented or not hmac.compare_digest(expected, presented):
        raise HTTPException(401, "Invalid re-ingest webhook signature")
