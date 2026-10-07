"""Request-body cap for the public (pre-auth) ingress routes (TRG-57).

Webhook and channel routes are exempt from API-key auth and authenticate the
caller from the body itself (HMAC signature, path token, ...), so they must read
the body BEFORE they know who sent it. They used to read it whole
(``await request.body()``) with no limit anywhere in the app: one anonymous
multi-gigabyte request could exhaust a replica's memory.

This pure-ASGI middleware bounds every request on those prefixes:

* a declared ``Content-Length`` above the cap is refused with 413 before the
  application runs (nothing is read);
* the body stream is counted as the application consumes it, and the chunk that
  crosses the cap ends the request with 413 — a chunked upload or a lying
  ``Content-Length`` is never read past the cap.

Signature verification therefore always runs on bounded bytes.

The authenticated OCR upload routes (``/ocr/``, a10-F243-05) are bounded the
same way: their JSON bodies carry whole documents as base64 and used to be read
with no limit. Their cap is the base64-inflated ``OCR_MAX_UPLOAD_BYTES`` plus
envelope room (a ``/ocr/batch`` request shares it: its documents together); the
route enforces the exact per-document limit. The OCR 413 states the document
limit, not just the inflated body cap, so the one documented limit is the one a
client sees.
"""

from __future__ import annotations

import json
import os
from typing import Any

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_PUBLIC_INGRESS_MAX_BODY_BYTES = 1_048_576
# Inbound email (SendGrid / Mailgun parse webhooks) carries attachments as
# multipart form data; it gets a larger — still bounded — cap.
DEFAULT_EMAIL_INGRESS_MAX_BODY_BYTES = 10 * 1_048_576

# Every route prefix that accepts a request body before authenticating it.
PUBLIC_INGRESS_PREFIXES: tuple[str, ...] = (
    "/triggers/webhooks/",
    "/webhooks/",
    "/channels/",
    "/integrations/",
    "/v1/gateway/",
)
_EMAIL_PREFIXES: tuple[str, ...] = ("/channels/email/",)
_OCR_PREFIXES: tuple[str, ...] = ("/ocr/",)
# JSON / multipart envelope allowance on top of the base64-inflated document.
_OCR_ENVELOPE_BYTES = 1_048_576


def ocr_body_cap(max_document_bytes: int) -> int:
    """The request-body cap for an OCR route: one document base64-encoded plus
    envelope room (a batch shares it — its documents together)."""
    return -(-max(1, max_document_bytes) * 4 // 3) + _OCR_ENVELOPE_BYTES


def _default_ocr_cap() -> int:
    from app.ocr.limits import ocr_max_upload_bytes

    return ocr_body_cap(ocr_max_upload_bytes())


def ocr_body_too_large_detail(body_cap: int) -> str:
    """The OCR 413 detail: the per-document limit behind ``body_cap`` (the
    inverse of :func:`ocr_body_cap`), not only the inflated body size."""
    from app.ocr.limits import describe_byte_limit

    document_bytes = (body_cap - _OCR_ENVELOPE_BYTES) * 3 // 4
    if document_bytes <= 0:  # an explicit cap not derived from a document limit
        return f"Request body exceeds {body_cap} bytes (OCR request body cap)"
    return (
        f"Request body exceeds {body_cap} bytes: the OCR limit is "
        f"{describe_byte_limit(document_bytes)} per document "
        "(OCR_MAX_UPLOAD_BYTES), sent raw or base64-encoded; a /ocr/batch "
        "request's documents share one such body"
    )


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def cap_for_path(
    path: str,
    default_cap: int,
    email_cap: int | None = None,
    ocr_cap: int | None = None,
) -> int | None:
    """The body cap for ``path``, or ``None`` when the path is not bounded here."""
    if ocr_cap is not None and any(path.startswith(p) for p in _OCR_PREFIXES):
        return ocr_cap
    if any(path.startswith(p) for p in _EMAIL_PREFIXES):
        return max(default_cap, email_cap or DEFAULT_EMAIL_INGRESS_MAX_BODY_BYTES)
    if any(path.startswith(p) for p in PUBLIC_INGRESS_PREFIXES):
        return default_cap
    return None


class RequestBodyTooLargeError(HTTPException):
    """Raised from ``receive`` once the streamed body passes the cap."""

    def __init__(self, cap: int, detail: str | None = None) -> None:
        super().__init__(status_code=413, detail=detail or f"Request body exceeds {cap} bytes")


async def _send_413(send: Send, cap: int, detail: str | None = None) -> None:
    body = json.dumps({"detail": detail or f"Request body exceeds {cap} bytes"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class PublicIngressBodyLimitMiddleware:
    """Bound request bodies on :data:`PUBLIC_INGRESS_PREFIXES` and the OCR upload
    routes (413 past the cap)."""

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int | None = None,
        email_max_body_bytes: int | None = None,
        ocr_max_body_bytes: int | None = None,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes or _env_int(
            "PUBLIC_INGRESS_MAX_BODY_BYTES", DEFAULT_PUBLIC_INGRESS_MAX_BODY_BYTES
        )
        self.email_max_body_bytes = email_max_body_bytes or _env_int(
            "EMAIL_INGRESS_MAX_BODY_BYTES", DEFAULT_EMAIL_INGRESS_MAX_BODY_BYTES
        )
        self.ocr_max_body_bytes = ocr_max_body_bytes or _default_ocr_cap()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        cap = cap_for_path(
            path, self.max_body_bytes, self.email_max_body_bytes, self.ocr_max_body_bytes
        )
        if cap is None:
            await self.app(scope, receive, send)
            return
        detail = (
            ocr_body_too_large_detail(cap)
            if any(path.startswith(p) for p in _OCR_PREFIXES)
            else None
        )

        declared = _declared_length(scope)
        if declared is not None and declared > cap:
            await _send_413(send, cap, detail)
            return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                raise RequestBodyTooLargeError(cap, detail)
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b"") or b"")
                if received > cap:
                    exceeded = True
                    raise RequestBodyTooLargeError(cap, detail)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except RequestBodyTooLargeError:
            # A handler that let the error escape (instead of FastAPI turning it
            # into a 413) still answers 413 — unless it already started a reply.
            if not response_started:
                await _send_413(send, cap, detail)


def _declared_length(scope: Scope) -> int | None:
    headers: Any = scope.get("headers") or []
    for name, value in headers:
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
