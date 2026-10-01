"""KB-48: POST /embeddings/embed is charged to the tenant budget and size-capped.

It spent embedding money with no reservation and no per-text cap, and fell
back to the chat provider (``_app_provider``) when no embedder was wired.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.embeddings import router
from app.governance.cost import CostController
from app.providers.base import EmbedResponse
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext("t-embed", PlanTier.PROFESSIONAL, "k")
_KEY = "key-embed"
_H = {"X-API-Key": _KEY}


class _Embedder:
    provider_name = "fake"
    _embed_model_name = "fake-embed"

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, request: Any) -> EmbedResponse:
        self.calls += 1
        return EmbedResponse(embeddings=[[0.1] * 4 for _ in request.texts], model="fake-embed")


def _client(**state: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    for key, value in state.items():
        setattr(app.state, key, value)
    return TestClient(app, raise_server_exceptions=False)


def test_spent_budget_is_429_and_the_provider_is_never_called() -> None:
    embedder = _Embedder()
    broke = CostController(per_goal_usd=10.0, per_tenant_daily_usd=0.0)
    resp = _client(embedder=embedder, cost_controller=broke).post(
        "/embeddings/embed", json={"texts": ["hello"]}, headers=_H
    )
    assert resp.status_code == 429, resp.text
    assert embedder.calls == 0


def test_unverifiable_budget_is_503() -> None:
    embedder = _Embedder()
    controller = MagicMock()
    controller.check_and_record = AsyncMock(side_effect=ConnectionError("redis down"))
    resp = _client(embedder=embedder, cost_controller=controller).post(
        "/embeddings/embed", json={"texts": ["hello"]}, headers=_H
    )
    assert resp.status_code == 503
    assert embedder.calls == 0


def test_each_call_is_reserved_against_the_tenant() -> None:
    embedder = _Embedder()
    controller = CostController(per_goal_usd=10.0, per_tenant_daily_usd=10.0)
    resp = _client(embedder=embedder, cost_controller=controller).post(
        "/embeddings/embed", json={"texts": ["a"] * 70}, headers=_H
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 70
    assert controller.daily_total(tenant_ctx=_CTX) > 0


def test_oversized_text_is_422() -> None:
    embedder = _Embedder()
    resp = _client(embedder=embedder).post(
        "/embeddings/embed", json={"texts": ["x" * 32_001]}, headers=_H
    )
    assert resp.status_code == 422
    assert embedder.calls == 0


def test_oversized_total_payload_is_422() -> None:
    embedder = _Embedder()
    resp = _client(embedder=embedder).post(
        "/embeddings/embed", json={"texts": ["x" * 30_000] * 20}, headers=_H
    )
    assert resp.status_code == 422
    assert embedder.calls == 0


def test_no_chat_provider_fallback() -> None:
    chat = MagicMock()
    chat.embed = AsyncMock(side_effect=AssertionError("chat provider must not embed"))
    resp = _client(embedder=None, _app_provider=chat).post(
        "/embeddings/embed", json={"texts": ["hello"]}, headers=_H
    )
    assert resp.status_code == 503
    chat.embed.assert_not_awaited()
