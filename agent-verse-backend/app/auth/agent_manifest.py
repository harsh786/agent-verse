"""
Signed Agent Capability Manifests
===================================
Every agent can produce a cryptographically signed manifest that external
systems can verify to understand what the agent is allowed to do.

Published at: GET /.well-known/agents/{agent_id}/manifest.json
Signed with: RS256 (AgentIdentity private key) or HMAC fallback

Manifest contents:
- agent_id, name, version
- allowed_tools (with patterns)
- max_iterations, autonomy_mode
- connector_permissions
- validity window
- issuer (agentverse.io)
- parent organization/tenant name

External A2A systems can verify this manifest before accepting tasks.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets as _secrets
import time
from typing import Any

# Per-process random key used only outside production when no secret is set.
_DEV_EPHEMERAL_SECRET = _secrets.token_urlsafe(32)


class ManifestSigningNotConfiguredError(RuntimeError):
    """MANIFEST_SIGNING_SECRET is required in production."""


def _signing_secret(explicit: str) -> str:
    """Resolve the HMAC key.

    There used to be a hard-coded public default (``agentverse-manifest-secret``),
    so anyone could forge a "signed" manifest that verified against any
    deployment that had not set the env var. Production now requires
    ``MANIFEST_SIGNING_SECRET``; other environments get a random per-process key
    (unforgeable, but not verifiable across processes).
    """
    if explicit:
        return explicit
    configured = os.getenv("MANIFEST_SIGNING_SECRET", "").strip()
    if configured and configured != "agentverse-manifest-secret":
        return configured
    if os.getenv("ENVIRONMENT", "development").lower() == "production":
        raise ManifestSigningNotConfiguredError(
            "MANIFEST_SIGNING_SECRET must be set (and not the old public default) in production"
        )
    return _DEV_EPHEMERAL_SECRET


def build_manifest(
    agent: dict[str, Any],
    tenant: Any,
    *,
    valid_days: int = 7,
) -> dict[str, Any]:
    """Build an unsigned manifest dict for an agent."""
    now = int(time.time())
    return {
        "schema_version": "1.0",
        "agent_id": agent.get("id", ""),
        "name": agent.get("name", ""),
        "description": agent.get("description", ""),
        "version": agent.get("version", "1.0.0"),
        "autonomy_mode": agent.get("autonomy_mode", "bounded-autonomous"),
        "max_iterations": agent.get("max_iterations", 10),
        "allowed_tools": agent.get("allowed_tools") or agent.get("connector_ids") or [],
        "required_approvals": ["write_high", "destructive"],
        "tenant": {
            "id": getattr(tenant, "tenant_id", ""),
            "plan": str(getattr(tenant, "plan", "unknown")),
        },
        "issuer": "agentverse.io",
        "issued_at": now,
        "valid_until": now + valid_days * 86400,
        "capabilities": {
            "streaming": True,
            "input_modes": ["text"],
            "output_modes": ["text", "artifacts"],
        },
    }


def sign_manifest(manifest: dict[str, Any], secret: str = "") -> dict[str, Any]:
    """Add an HMAC signature to the manifest."""
    import hmac as _hmac

    signing_secret = _signing_secret(secret)
    canonical = json.dumps(manifest, sort_keys=True).encode()
    sig = _hmac.new(signing_secret.encode(), canonical, hashlib.sha256).digest()
    return {
        **manifest,
        "_signature": base64.urlsafe_b64encode(sig).decode(),
        "_signed": True,
    }


def verify_manifest(manifest: dict[str, Any], secret: str = "") -> bool:
    """Verify the manifest signature."""
    import hmac as _hmac

    sig_b64 = manifest.pop("_signature", None)
    manifest.pop("_signed", None)
    if not sig_b64:
        return False
    signing_secret = _signing_secret(secret)
    canonical = json.dumps(manifest, sort_keys=True).encode()
    expected = _hmac.new(signing_secret.encode(), canonical, hashlib.sha256).digest()
    try:
        actual = base64.urlsafe_b64decode(sig_b64 + "==")
        return _hmac.compare_digest(expected, actual)
    except Exception:
        return False
