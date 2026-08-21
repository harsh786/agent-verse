"""WebhookSignatureVerifier — HMAC verification for all webhook families."""

from __future__ import annotations

import hashlib
import hmac
import logging

_log = logging.getLogger(__name__)


class WebhookSignatureVerifier:
    """Verify incoming webhook payloads using HMAC-SHA256."""

    async def verify(
        self,
        payload_bytes: bytes,
        signature_header: str,
        secret: str,
        *,
        algorithm: str = "sha256",
    ) -> bool:
        """Return True if the signature is valid, False otherwise."""
        if not secret or not signature_header:
            return False
        try:
            hash_func = getattr(hashlib, algorithm, hashlib.sha256)
            expected = hmac.new(
                secret.encode("utf-8"),
                payload_bytes,
                hash_func,
            ).hexdigest()
            # Strip common prefixes: "sha256=", "v0=", etc.
            received = signature_header.split("=")[-1]
            return hmac.compare_digest(expected, received)
        except Exception as exc:
            _log.warning("signature_verify_error: %s", exc)
            return False

    # ── Platform-specific helpers ──────────────────────────────────────────────

    def verify_github(self, payload_bytes: bytes, header: str, secret: str) -> bool:
        """Synchronous verify for GitHub X-Hub-Signature-256."""
        return hmac.compare_digest(
            "sha256=" + hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest(),
            header,
        )

    def verify_stripe(self, payload_bytes: bytes, header: str, secret: str) -> bool:
        """Stripe uses t=timestamp,v1=... format — verify the v1 component."""
        parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
        timestamp = parts.get("t", "")
        sig_v1 = parts.get("v1", "")
        signed_payload = f"{timestamp}.".encode() + payload_bytes
        expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig_v1)
