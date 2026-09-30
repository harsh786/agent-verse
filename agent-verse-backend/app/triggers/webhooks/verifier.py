"""WebhookSignatureVerifier — HMAC verification for all webhook families."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import time
from collections.abc import Mapping

_log = logging.getLogger(__name__)

# Stripe's own libraries default to a 5-minute timestamp tolerance.
STRIPE_TOLERANCE_SECONDS = 300
# Slack: "verify that the request timestamp is within five minutes".
SLACK_TOLERANCE_SECONDS = 300
# Grafana has no default timestamp header name (it is set per contact point);
# AgentVerse documents this one for the "Timestamp header" setting.
GRAFANA_TIMESTAMP_HEADER = "x-grafana-alerting-timestamp"
GRAFANA_TOLERANCE_SECONDS = 300


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

    def verify_slack(
        self,
        payload_bytes: bytes,
        signature: str,
        timestamp: str,
        secret: str,
        *,
        tolerance_seconds: int = SLACK_TOLERANCE_SECONDS,
        now: float | None = None,
    ) -> bool:
        """Verify ``X-Slack-Signature: v0=<hex>`` over ``v0:{timestamp}:{body}``.

        ``timestamp`` is ``X-Slack-Request-Timestamp``; one outside the tolerance
        is rejected (replay protection, Slack's documented 5 minutes).
        """
        if not secret or not signature or not timestamp:
            return False
        try:
            ts = int(timestamp)
        except ValueError:
            return False
        current = time.time() if now is None else now
        if abs(current - ts) > tolerance_seconds:
            return False
        base = f"v0:{timestamp}:".encode() + payload_bytes
        expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_teams(self, payload_bytes: bytes, authorization: str, secret: str) -> bool:
        """Verify a Teams outgoing webhook ``Authorization: HMAC <base64>`` header.

        Teams signs the raw body with HMAC-SHA256 keyed by the BASE64-DECODED
        security token shown when the outgoing webhook is created.
        """
        scheme, _, presented = authorization.strip().partition(" ")
        if not secret or scheme.upper() != "HMAC" or not presented:
            return False
        try:
            key = base64.b64decode(secret, validate=True)
        except (binascii.Error, ValueError):
            return False
        expected = base64.b64encode(hmac.new(key, payload_bytes, hashlib.sha256).digest())
        return hmac.compare_digest(expected, presented.strip().encode())

    @staticmethod
    def _hmac_sha256(secret: str, data: bytes) -> bytes:
        return hmac.new(secret.encode(), data, hashlib.sha256).digest()

    def verify_pagerduty(self, payload_bytes: bytes, header: str, secret: str) -> bool:
        """Verify ``X-PagerDuty-Signature: v1=<hex>[,v1=<hex>...]`` (v3 webhooks).

        PagerDuty sends one ``v1`` per active secret while a secret is rolled.
        The generic check compared only the text after the LAST ``=``, so a valid
        signature in any other position was refused.
        """
        if not secret or not header:
            return False
        expected = self._hmac_sha256(secret, payload_bytes).hex()
        for item in header.split(","):
            version, sep, value = item.strip().partition("=")
            if sep and version == "v1" and hmac.compare_digest(expected, value.strip()):
                return True
        return False

    def verify_hub_signature(self, payload_bytes: bytes, header: str, secret: str) -> bool:
        """Verify Atlassian Data Center ``X-Hub-Signature: sha256=<hex>`` (Confluence)."""
        if not secret or not header.startswith("sha256="):
            return False
        expected = "sha256=" + self._hmac_sha256(secret, payload_bytes).hex()
        return hmac.compare_digest(expected, header.strip())

    def verify_salesforce(self, payload_bytes: bytes, header: str, secret: str) -> bool:
        """Verify Salesforce ``x-signature``: HMAC-SHA256 of the body, base64 (the
        Data Cloud webhook target default) or hex (a configurable encoding)."""
        if not secret or not header:
            return False
        mac = self._hmac_sha256(secret, payload_bytes)
        presented = header.strip()
        return hmac.compare_digest(base64.b64encode(mac).decode(), presented) or (
            hmac.compare_digest(mac.hex(), presented.lower())
        )

    def verify_grafana(
        self,
        payload_bytes: bytes,
        signature: str,
        secret: str,
        *,
        timestamp: str = "",
        tolerance_seconds: int = GRAFANA_TOLERANCE_SECONDS,
        now: float | None = None,
    ) -> bool:
        """Verify ``X-Grafana-Alerting-Signature`` (lower-case hex HMAC-SHA256).

        With a timestamp header configured on the contact point Grafana signs
        ``"{timestamp}:{body}"``; the timestamp must then be recent (replay
        protection). Without one it signs the body alone.
        """
        if not secret or not signature:
            return False
        data = payload_bytes
        if timestamp:
            try:
                ts = int(timestamp)
            except ValueError:
                return False
            current = time.time() if now is None else now
            if abs(current - ts) > tolerance_seconds:
                return False
            data = timestamp.encode() + b":" + payload_bytes
        expected = self._hmac_sha256(secret, data).hex()
        return hmac.compare_digest(expected, signature.strip())

    async def verify_for_type(
        self,
        webhook_type: str,
        payload_bytes: bytes,
        header: str,
        secret: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> bool:
        """Verify with the scheme the sending platform actually uses.

        ``headers`` carries the request headers for schemes that sign more than
        the body (Slack's request timestamp).
        """
        if webhook_type == "slack":
            timestamp = (headers or {}).get("x-slack-request-timestamp", "")
            return self.verify_slack(payload_bytes, header, timestamp, secret)
        if webhook_type == "teams":
            return self.verify_teams(payload_bytes, header, secret)
        if webhook_type == "stripe":
            return self.verify_stripe(payload_bytes, header, secret)
        if webhook_type == "github":
            if not secret or not header:
                return False
            return self.verify_github(payload_bytes, header, secret)
        if webhook_type == "pagerduty":
            return self.verify_pagerduty(payload_bytes, header, secret)
        if webhook_type == "confluence":
            return self.verify_hub_signature(payload_bytes, header, secret)
        if webhook_type == "salesforce":
            return self.verify_salesforce(payload_bytes, header, secret)
        if webhook_type == "grafana":
            timestamp = (headers or {}).get(GRAFANA_TIMESTAMP_HEADER, "")
            return self.verify_grafana(payload_bytes, header, secret, timestamp=timestamp)
        return await self.verify(payload_bytes, header, secret)
