"""TRG-57: public (pre-auth) ingress routes bound the request body.

``receive_typed_webhook`` (and every other webhook route) read the whole body
before any token / signature check and the app had no global limit, so one
anonymous request could exhaust a replica's memory. The public ingress prefixes
now reject an oversized ``Content-Length`` up front and stop a streamed
(chunked) body as soon as it passes the cap — never reading past it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.integrations.body_limit import (
    DEFAULT_PUBLIC_INGRESS_MAX_BODY_BYTES,
    PublicIngressBodyLimitMiddleware,
)

_CAP = 1024


def _app(cap: int = _CAP) -> FastAPI:
    app = FastAPI()

    @app.post("/triggers/webhooks/{kind}/{token}")
    async def hook(kind: str, token: str, request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    @app.post("/goals")
    async def goals(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    app.add_middleware(PublicIngressBodyLimitMiddleware, max_body_bytes=cap)
    return app


def test_default_cap_is_one_mebibyte() -> None:
    assert DEFAULT_PUBLIC_INGRESS_MAX_BODY_BYTES == 1_048_576


def test_body_under_the_cap_is_delivered() -> None:
    resp = TestClient(_app()).post("/triggers/webhooks/github/tok", content=b"x" * 100)
    assert resp.status_code == 200
    assert resp.json() == {"size": 100}


def test_declared_content_length_over_the_cap_is_413() -> None:
    resp = TestClient(_app()).post("/triggers/webhooks/github/tok", content=b"x" * (_CAP + 1))
    assert resp.status_code == 413


def test_two_megabyte_body_is_rejected_with_the_default_cap() -> None:
    app = FastAPI()

    @app.post("/triggers/webhooks/{kind}/{token}")
    async def hook(kind: str, token: str, request: Request) -> dict[str, int]:
        raise AssertionError("handler must not run for an oversized body")

    app.add_middleware(PublicIngressBodyLimitMiddleware)
    resp = TestClient(app).post("/triggers/webhooks/github/tok", content=b"x" * 2_000_000)
    assert resp.status_code == 413


def test_non_ingress_routes_are_not_capped_by_this_middleware() -> None:
    resp = TestClient(_app()).post("/goals", content=b"x" * (_CAP * 4))
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/triggers/webhooks/github/tok",
        "/webhooks/tok",
        "/channels/slack/events",
        "/integrations/slack/commands",
        "/v1/gateway/telegram/chat/bot",
    ],
)
def test_every_public_ingress_prefix_is_capped(path: str) -> None:
    from app.integrations.body_limit import cap_for_path

    assert cap_for_path(path, _CAP) == _CAP


def test_unrelated_path_has_no_cap() -> None:
    from app.integrations.body_limit import cap_for_path

    assert cap_for_path("/goals", _CAP) is None


async def _drive(
    app: Any, chunks: list[bytes], headers: list[tuple[bytes, bytes]]
) -> tuple[int, int]:
    """Run one chunked request through the ASGI app; return (status, chunks read)."""
    pulled = 0
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal pulled
        if pulled < len(chunks):
            body = chunks[pulled]
            pulled += 1
            return {"type": "http.request", "body": body, "more_body": pulled < len(chunks)}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/triggers/webhooks/github/tok",
        "raw_path": b"/triggers/webhooks/github/tok",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("test", 1),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return int(start["status"]), pulled


@pytest.mark.asyncio
async def test_chunked_oversized_upload_stops_at_the_cap() -> None:
    """No Content-Length (chunked): reading stops on the chunk that crosses the cap."""
    chunks = [b"x" * 512] * 100  # 50 KiB offered, cap 1 KiB
    status, pulled = await _drive(_app(), chunks, [(b"transfer-encoding", b"chunked")])
    assert status == 413
    assert pulled <= 3  # 512 + 512 = cap, the third chunk crosses it; nothing more read


def test_create_app_caps_the_typed_webhook_route(app: FastAPI) -> None:
    """The real app answers 413 on an oversized typed-webhook body before the
    route reads it (the route itself does an unbounded ``request.body()``)."""
    resp = TestClient(app).post("/triggers/webhooks/github/some-token", content=b"x" * 2_000_000)
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_lying_content_length_is_still_capped_while_streaming() -> None:
    chunks = [b"x" * 512] * 10
    status, pulled = await _drive(_app(), chunks, [(b"content-length", b"10")])
    assert status == 413
    assert pulled <= 3
