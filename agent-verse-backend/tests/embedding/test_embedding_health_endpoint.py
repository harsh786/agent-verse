"""Phase-3 Row 7: /embeddings/health/{id} must use the REAL drift monitor and
re-embedding policy (not ad-hoc inline heuristics)."""
from __future__ import annotations

import datetime as _dt
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.embeddings import router
from app.embedding.drift_monitor import EmbeddingDriftMonitor
from app.embedding.reembedding_policy import ReembeddingPolicy


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_tenant(request: Any, call_next: Any) -> Any:
        t = MagicMock()
        t.tenant_id = "t-health"
        request.state.tenant = t
        return await call_next(request)

    app.include_router(router)
    return app


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _FakeSession:
    """Returns queued rows in call order: stats, collection, avg_similarity."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)
        self.param_bindings: list[dict[str, Any]] = []

    async def execute(self, _stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        self.param_bindings.append(dict(params or {}))
        return _Result(self._rows.pop(0) if self._rows else None)


def _factory(rows: list[Any], sink: list[_FakeSession] | None = None) -> Any:
    @asynccontextmanager
    async def _cm() -> Any:
        sess = _FakeSession(rows)
        if sink is not None:
            sink.append(sess)
        yield sess

    def _make() -> Any:
        return _cm()

    return _make


@pytest.mark.asyncio
async def test_health_reports_real_drift_severity_and_trigger() -> None:
    # 200 chunks, all embedded, updated recently; centroid similarity 0.62 → MEDIUM
    recent = _dt.datetime.now(_dt.UTC)
    rows = [
        (200, 200, recent),          # chunk stats
        ("voyage-3-large", 1024),    # collection embedder + dim
        (0.62,),                     # avg similarity to centroid
    ]
    app = _make_app()
    with patch("app.db.session.get_session_factory", return_value=_factory(rows)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.get("/embeddings/health/col-1")
    assert resp.status_code == 200
    data = resp.json()

    expected_sev = EmbeddingDriftMonitor().measure(0.62).value
    expected_score = round(EmbeddingDriftMonitor().drift_score(0.62), 4)
    assert data["drift_severity"] == expected_sev == "medium"
    assert data["drift_score"] == expected_score
    assert data["avg_similarity"] == 0.62
    # 0.62 similarity → drift_score 0.38 > 0.25 threshold → policy recommends re-embed.
    expected_trigger = ReembeddingPolicy().should_reembed(
        current_model="voyage-3-large", new_model="voyage-3-large",
        collection_size=200, drift_score=0.38, age_days=0,
        old_dim=1024, new_dim=1024,
    ).value
    assert data["reembed_trigger"] == expected_trigger == "drift_detected"
    assert data["needs_reembed"] is True


@pytest.mark.asyncio
async def test_health_stable_collection_needs_no_reembed() -> None:
    recent = _dt.datetime.now(_dt.UTC)
    rows = [
        (150, 150, recent),
        ("voyage-3-large", 1024),
        (0.93,),  # high similarity → STABLE, low drift
    ]
    app = _make_app()
    with patch("app.db.session.get_session_factory", return_value=_factory(rows)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.get("/embeddings/health/col-2")
    data = resp.json()
    assert data["drift_severity"] == "stable"
    assert data["reembed_trigger"] == "none"
    assert data["needs_reembed"] is False


@pytest.mark.asyncio
async def test_health_scopes_every_query_to_the_caller_tenant() -> None:
    """Security: collection_id is client-supplied — every query must be scoped to
    the caller's tenant so it cannot read another tenant's collection (IDOR)."""
    recent = _dt.datetime.now(_dt.UTC)
    rows = [(10, 10, recent), ("voyage-3-large", 1024), (0.9,)]
    sink: list[_FakeSession] = []
    app = _make_app()
    with patch("app.db.session.get_session_factory", return_value=_factory(rows, sink)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.get("/embeddings/health/some-other-tenant-collection")
    assert resp.status_code == 200
    assert sink, "session was never opened"
    bindings = sink[0].param_bindings
    assert bindings, "no queries executed"
    # Every query carries the tenant filter with the caller's tenant id.
    assert all(b.get("tid") == "t-health" for b in bindings), bindings
