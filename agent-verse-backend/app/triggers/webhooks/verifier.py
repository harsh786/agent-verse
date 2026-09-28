"""WebhookSignatureVerifier — HMAC verification for all webhook families."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

_log = logging.getLogger(__name__)

# Stripe's own libraries default to a 5-minute timestamp tolerance.
STRIPE_TOLERANCE_SECONDS = 300


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

    def verify_stripe(
        self,
        payload_bytes: bytes,
        header: str,
        secret: str,
        *,
        tolerance_seconds: int = STRIPE_TOLERANCE_SECONDS,
        now: float | None = None,
    ) -> bool:
        """Verify a ``Stripe-Signature: t=<ts>,v1=<sig>[,v1=<sig>...]`` header.

        Stripe signs ``"<t>.<raw body>"``. The generic :meth:`verify` took the text
        after the LAST ``=`` and compared it with an HMAC of the bare body, so a
        genuine Stripe delivery could never verify. Every ``v1`` is accepted (Stripe
        sends several during secret rolls) and a timestamp outside the tolerance
        is rejected (replay protection).
        """
        if not secret or not header:
            return False
        timestamp = ""
        signatures: list[str] = []
        for item in header.split(","):
            key, sep, value = item.strip().partition("=")
            if not sep:
                continue
            if key == "t":
                timestamp = value
            elif key == "v1":
                signatures.append(value)
        if not timestamp or not signatures:
            return False
        try:
            ts = int(timestamp)
        except ValueError:
            return False
        current = time.time() if now is None else now
        if tolerance_seconds and abs(current - ts) > tolerance_seconds:
            return False
        expected = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + payload_bytes, hashlib.sha256
        ).hexdigest()
        return any(hmac.compare_digest(expected, sig) for sig in signatures)

    async def verify_for_type(
        self, webhook_type: str, payload_bytes: bytes, header: str, secret: str
    ) -> bool:
        """Verify with the scheme the sending platform actually uses."""
        if webhook_type == "stripe":
            return self.verify_stripe(payload_bytes, header, secret)
        if webhook_type == "github":
            if not secret or not header:
                return False
            return self.verify_github(payload_bytes, header, secret)
        return await self.verify(payload_bytes, header, secret)
