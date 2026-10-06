"""Shared ingress rules for inbound trigger webhooks (B2).

Used by the token-authenticated ``POST /triggers/webhooks/{type}/{token}`` and
the API-key ``POST /webhooks/{token}`` endpoints:

* :func:`read_capped_body` — the body, streamed, refused with 413 past a cap
  (never buffered whole).
* :func:`parse_body` — JSON, ``application/x-www-form-urlencoded`` and
  ``text/*`` bodies become the trigger payload.
* :func:`delivery_id` — the sender's delivery id (``Idempotency-Key``,
  ``X-Delivery-Id``, ``X-GitHub-Delivery``, Standard Webhooks ``webhook-id``…).
  Retries repeat it, so it is the firing's identity: the same delivery runs once.
  Without one, an identical body inside the same replay window
  (:data:`REPLAY_WINDOW_SECONDS`) is the same delivery; later it is a new one.
* :func:`check_signature` — HMAC-SHA256, constant-time. With a
  ``X-Webhook-Timestamp`` header the signature covers ``"{timestamp}.{body}"``
  and a timestamp outside the replay window is refused, so a captured delivery
  cannot be replayed later. Without it the legacy body-only signature applies.
* :func:`signed_replay_key` — the replay identity of a VERIFIED signed delivery,
  derived only from what was signed (delivery-id headers are not signed, so an
  attacker can change them). A legacy body-only delivery keys on its body,
  unwindowed: the same signed body is the same delivery for as long as the
  secret that signed it is accepted (B2-GAP-1, the GitHub rule of DEF-NEW-3).
  A sender whose events can legitimately repeat a byte-identical body must sign
  with ``X-Webhook-Timestamp``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs

from fastapi import HTTPException, Request

# Largest inbound webhook body accepted at the edge (the dispatcher then applies
# the tenant plan's payload limit).
MAX_WEBHOOK_BODY_BYTES = 1_048_576
# Signed-timestamp tolerance and the no-delivery-id dedup window.
REPLAY_WINDOW_SECONDS = 300

DELIVERY_ID_HEADERS = (
    "idempotency-key",
    "x-delivery-id",
    "x-webhook-id",
    "webhook-id",
    "x-github-delivery",
    "x-request-id",
)
TIMESTAMP_HEADERS = ("x-webhook-timestamp", "webhook-timestamp")
SIGNATURE_HEADERS = ("x-signature", "x-agentverse-signature", "webhook-signature")


async def read_capped_body(request: Request, cap: int = MAX_WEBHOOK_BODY_BYTES) -> bytes:
    """The request body, or 413 once it exceeds ``cap`` bytes."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(status_code=413, detail=f"Webhook body exceeds {cap} bytes")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise HTTPException(status_code=413, detail=f"Webhook body exceeds {cap} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def parse_body(body: bytes, content_type: str) -> dict[str, Any]:
    """The payload a webhook body maps to (400 for a malformed JSON body).

    JSON objects are used as-is, other JSON values under ``data``; a form body
    becomes a dict (repeated fields as lists); ``text/*`` goes under ``text``.
    """
    ctype = (content_type or "").split(";")[0].strip().lower()
    if not body.strip():
        return {}
    if ctype == "application/x-www-form-urlencoded":
        try:
            fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=False)
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="Form body is not UTF-8") from exc
        return {k: (v[0] if len(v) == 1 else v) for k, v in fields.items()}
    if ctype.startswith("text/"):
        return {"text": body.decode("utf-8", errors="replace")}
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Webhook body must be JSON, form-encoded or text/* (Content-Type)",
        ) from exc
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _first(headers: Mapping[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        value = (headers.get(name) or "").strip()
        if value:
            return value
    return ""


def delivery_id(headers: Mapping[str, str]) -> str:
    """The sender's delivery id from the request headers ("" when none)."""
    return _first(headers, DELIVERY_ID_HEADERS)[:200]


def firing_message_id(
    headers: Mapping[str, str], body: bytes, *, now: float | None = None
) -> str:
    """The dispatcher ``message_id`` (idempotency identity) of a delivery.

    The sender's delivery id when it sends one; otherwise the body hash within
    the current replay window, so an identical body a day later (a legitimate
    new delivery) is never dropped as a replay.
    """
    sent = delivery_id(headers)
    if sent:
        return f"delivery:{sent}"
    window = int((time.time() if now is None else now) // REPLAY_WINDOW_SECONDS)
    return f"body:{hashlib.sha256(body).hexdigest()[:32]}:{window}"


def signed_timestamp(headers: Mapping[str, str]) -> str:
    """The signed-timestamp header value ("" = legacy body-only signature)."""
    return _first(headers, TIMESTAMP_HEADERS)


def signed_replay_key(headers: Mapping[str, str], body: bytes) -> str:
    """Replay identity of a delivery whose signature :func:`check_signature` accepted.

    ``signed-ts:<sha256("{ts}.{body}")>`` for the timestamped scheme (the exact
    signed bytes: a replay inside the 300 s window under a fresh delivery id is
    still the same delivery), ``signed-body:<sha256(body)>`` for the legacy
    body-only scheme (unwindowed: nothing signed says how old it is).
    """
    timestamp = signed_timestamp(headers)
    if timestamp:
        digest = hashlib.sha256(f"{timestamp}.".encode() + body).hexdigest()[:40]
        return f"signed-ts:{digest}"
    return f"signed-body:{hashlib.sha256(body).hexdigest()[:40]}"


def _sig_value(header: str) -> str:
    """Hex digest from ``sha256=<hex>`` / ``v1=<hex>`` / ``<hex>`` (first entry)."""
    first = header.split(",")[0].strip()
    return first.split("=", 1)[1].strip() if "=" in first else first


def check_signature(
    headers: Mapping[str, str],
    body: bytes,
    secrets: list[str],
    *,
    now: float | None = None,
) -> None:
    """Raise 401 unless one of ``secrets`` signed this delivery (see module doc)."""
    signature = _first(headers, SIGNATURE_HEADERS)
    if not signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    timestamp = signed_timestamp(headers)
    signed = body
    if timestamp:
        try:
            ts = int(timestamp)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="Invalid webhook timestamp") from exc
        current = time.time() if now is None else now
        if abs(current - ts) > REPLAY_WINDOW_SECONDS:
            raise HTTPException(
                status_code=401,
                detail=f"Webhook timestamp outside the {REPLAY_WINDOW_SECONDS} s replay window",
            )
        signed = f"{timestamp}.".encode() + body
    presented = _sig_value(signature)
    for secret in secrets:
        if not secret:
            continue
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, presented):
            return
    raise HTTPException(status_code=401, detail="Invalid webhook signature")


# Dispatcher skip reasons → the HTTP answer a sender acts on.
_RETRY_LATER = frozenset({"rate_limit", "bulkhead_full"})
_UNAVAILABLE = frozenset({"rate_limit_unavailable", "bulkhead_unavailable", "circuit_open"})


def raise_for_skip(skip_reason: str | None) -> None:
    """Map a dispatcher skip to the sender-facing status.

    Throttled → 429 with ``Retry-After`` (the sender redelivers; the firing was
    never claimed, B2-1); an uncheckable gate / open circuit → 503; an oversized
    payload → 413. Filters (``condition_false``), replays (``dedup``) and
    in-progress folds are a normal, final answer (no raise).
    """
    if not skip_reason:
        return
    if skip_reason in _RETRY_LATER:
        raise HTTPException(
            status_code=429,
            detail=f"Trigger throttled ({skip_reason}); retry later",
            headers={"Retry-After": "60"},
        )
    if skip_reason in _UNAVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=f"Trigger temporarily unavailable ({skip_reason}); retry",
            headers={"Retry-After": "30"},
        )
    if skip_reason == "PAYLOAD_TOO_LARGE":
        raise HTTPException(status_code=413, detail="Payload exceeds the plan's trigger limit")
