"""Amazon SNS delivery handling for the CloudWatch typed webhook (TRG-25).

CloudWatch alarms reach an HTTPS endpoint only through an SNS topic. SNS first
POSTs a ``SubscriptionConfirmation`` whose ``SubscribeURL`` must be fetched, and
then wraps every alarm in a ``Notification`` whose ``Message`` is the alarm JSON.
Nothing handled either, so the subscription never confirmed and no alarm was
ever delivered.

Every SNS message is signed by AWS (RSA over a canonical string, with a signing
certificate served from ``sns.<region>.amazonaws.com``), so authenticity does not
depend on a tenant-configured secret. The certificate and the ``SubscribeURL``
are fetched only from SNS hosts, through the SSRF-pinned client.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.parse import urlparse

from app.observability.logging import get_logger

_log = get_logger(__name__)

SNS_MESSAGE_TYPES = frozenset(
    {"SubscriptionConfirmation", "Notification", "UnsubscribeConfirmation"}
)

# sns.<region>.amazonaws.com (and the China partition's .com.cn).
_SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com(\.cn)?$")

_NOTIFICATION_KEYS = ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type")
_SUBSCRIPTION_KEYS = (
    "Message",
    "MessageId",
    "SubscribeURL",
    "Timestamp",
    "Token",
    "TopicArn",
    "Type",
)

# Signing certificates are immutable per URL; caching them per process only
# saves a fetch (every replica may fetch its own copy).
_CERT_CACHE: dict[str, bytes] = {}


class SNSVerificationError(ValueError):
    """The message is not an authentic SNS delivery."""


def is_sns_message(body: Any) -> bool:
    return isinstance(body, dict) and body.get("Type") in SNS_MESSAGE_TYPES


def is_sns_url(url: str, *, pem: bool = False) -> bool:
    """True for an ``https://sns.<region>.amazonaws.com/...`` URL."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.port not in (None, 443):
        return False
    if not _SNS_HOST.match((parsed.hostname or "").lower()):
        return False
    return not pem or parsed.path.endswith(".pem")


def string_to_sign(msg: dict[str, Any]) -> bytes:
    """The canonical string SNS signs (keys in byte order, Subject only if set)."""
    keys = _NOTIFICATION_KEYS if msg.get("Type") == "Notification" else _SUBSCRIPTION_KEYS
    parts: list[str] = []
    for key in keys:
        if key == "Subject" and msg.get("Subject") is None:
            continue
        if key not in msg:
            raise SNSVerificationError(f"SNS message is missing {key}")
        parts.append(f"{key}\n{msg[key]}\n")
    return "".join(parts).encode()


async def _fetch_https(url: str) -> Any:
    from app.net.ssrf_guard import public_async_client, request_public

    async with public_async_client(timeout=10.0) as client:
        resp = await request_public(client, "GET", url, context="sns", max_redirects=0)
    resp.raise_for_status()
    return resp


async def _signing_cert(url: str) -> bytes:
    if url not in _CERT_CACHE:
        _CERT_CACHE[url] = (await _fetch_https(url)).content
    return _CERT_CACHE[url]


async def verify_sns_message(msg: dict[str, Any]) -> None:
    """Raise :class:`SNSVerificationError` unless *msg* carries a valid AWS signature."""
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    cert_url = str(msg.get("SigningCertURL") or msg.get("SigningCertUrl") or "")
    if not is_sns_url(cert_url, pem=True):
        raise SNSVerificationError("SigningCertURL is not an SNS certificate URL")
    version = str(msg.get("SignatureVersion") or "1")
    algorithm: hashes.HashAlgorithm
    if version == "1":
        algorithm = hashes.SHA1()  # SNS SignatureVersion 1 is SHA1withRSA
    elif version == "2":
        algorithm = hashes.SHA256()
    else:
        raise SNSVerificationError(f"unsupported SignatureVersion {version!r}")
    try:
        signature = base64.b64decode(str(msg.get("Signature") or ""), validate=True)
    except ValueError as exc:
        raise SNSVerificationError("Signature is not base64") from exc
    canonical = string_to_sign(msg)
    try:
        cert = x509.load_pem_x509_certificate(await _signing_cert(cert_url))
    except Exception as exc:
        raise SNSVerificationError(f"signing certificate unavailable: {exc}") from exc
    public_key = cert.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise SNSVerificationError("signing certificate is not RSA")
    try:
        public_key.verify(signature, canonical, padding.PKCS1v15(), algorithm)
    except InvalidSignature as exc:
        raise SNSVerificationError("SNS signature does not verify") from exc


async def confirm_subscription(msg: dict[str, Any]) -> None:
    """Fetch the (verified) ``SubscribeURL`` — only ever an SNS host."""
    url = str(msg.get("SubscribeURL") or "")
    if not is_sns_url(url):
        raise SNSVerificationError("SubscribeURL is not an SNS URL")
    await _fetch_https(url)
    _log.info("sns_subscription_confirmed", topic_arn=str(msg.get("TopicArn") or ""))


def notification_payload(msg: dict[str, Any]) -> dict[str, Any]:
    """The alarm carried by a Notification (``Message`` is the alarm JSON)."""
    raw = msg.get("Message")
    try:
        inner = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        inner = None
    payload: dict[str, Any] = inner if isinstance(inner, dict) else {"message": raw}
    payload.setdefault("sns_topic_arn", msg.get("TopicArn", ""))
    payload.setdefault("sns_message_id", msg.get("MessageId", ""))
    if msg.get("Subject") is not None:
        payload.setdefault("sns_subject", msg.get("Subject"))
    return payload
