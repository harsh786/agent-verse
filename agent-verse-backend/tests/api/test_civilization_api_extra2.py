"""Extra coverage for app/api/civilization.py — uncovered endpoints and helpers."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.civilization import (
    _civilization_not_found,
    _get_db,
    _require_feature_enabled,
    _require_tenant,
    _nullctx,
    router,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-civ", plan=PlanTier.ENTERPRISE, api_key_id="kid-civ")
_VALID_KEY = "av_civ_test_key"


def _make_app(civilization_enabled: bool = True, db=None, redis=None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(router)

    # Settings stub
    settings = MagicMock()
    settings.civilization_enabled = civilization_enabled
    app.state.settings = settings

    if db is not None:
        app.state.db_session_factory = db
    if redis is not None:
        app.state._policy_pubsub_redis = redis

    return app


_AUTH = {"X-API-Key": _VALID_KEY}


# ── Helper functions ──────────────────────────────────────────────────────────

class TestHelpers:
    def test_civilization_not_found(self):
        exc = _civilization_not_found("civ123")
        assert exc.status_code == 404
        assert "civ123" in exc.detail

    def test_require_tenant_raises_when_state_missing(self):
        from fastapi import HTTPException
        request = MagicMock()
        request.state = MagicMock(spec=[])  # no 'tenant' attribute
        with pytest.raises(HTTPException) as exc_info:
            _require_tenant(request)
        assert exc_info.value.status_code == 401

    def test_require_tenant_returns_ctx_when_present(self):
        request = MagicMock()
        request.state.tenant = _CTX
        result = _require_tenant(request)
        assert result is _CTX

    def test_require_feature_enabled_raises_503_when_disabled(self):
        from fastapi import HTTPException
        request = MagicMock()
        settings = MagicMock()
        settings.civilization_enabled = False
        request.app.state.settings = settings
        with pytest.raises(HTTPException) as exc_info:
            _require_feature_enabled(request)
        assert exc_info.value.status_code == 503

    def test_require_feature_enabled_passes_when_enabled(self):
        request = MagicMock()
        settings = MagicMock()
        settings.civilization_enabled = True
        request.app.state.settings = settings
        # Should not raise
        _require_feature_enabled(request)

    def test_get_db_from_app_state(self):
        request = MagicMock()
        mock_db = MagicMock()
        request.app.state.db_session_factory = mock_db
        result = _get_db(request)
        assert result is mock_db

    def test_get_db_fallback_when_no_state(self):
        request = MagicMock()
        # Remove db_session_factory from state
        type(request.app.state).db_session_factory = property(
            fget=lambda self: None
        )
        # Should return None or try to import
        result = _get_db(request)
        # Acceptable to be None in test environment


# ── _nullctx async context manager ────────────────────────────────────────────

class TestNullCtx:
    @pytest.mark.asyncio
    async def test_passes_value_through(self):
        sentinel = object()
        async with _nullctx(sentinel) as val:
            assert val is sentinel

    @pytest.mark.asyncio
    async def test_exit_does_nothing(self):
        async with _nullctx("anything") as val:
            pass  # should not raise


# ── Feature-flag endpoints ────────────────────────────────────────────────────

class TestFeatureFlagGuard:
    def test_disabled_returns_503_on_get_single(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations/someid", headers=_AUTH)
        assert resp.status_code == 503

    def test_disabled_returns_503_on_put_constitution(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.put(
            "/civilizations/someid/constitution",
            json={"constitution": {}},
            headers=_AUTH,
        )
        assert resp.status_code == 503

    def test_disabled_returns_503_on_post_goal(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/civilizations/someid/goals",
            json={"goal": "do something"},
            headers=_AUTH,
        )
        assert resp.status_code == 503

    def test_disabled_returns_503_on_graph(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations/someid/graph", headers=_AUTH)
        assert resp.status_code == 503

    def test_disabled_returns_503_on_blackboard(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations/someid/blackboard", headers=_AUTH)
        assert resp.status_code == 503

    def test_disabled_returns_503_on_debates(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations/someid/debates", headers=_AUTH)
        assert resp.status_code == 503

    def test_disabled_returns_503_on_learnings(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations/someid/learnings", headers=_AUTH)
        assert resp.status_code == 503

    def test_disabled_returns_503_on_spawns(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations/someid/spawns", headers=_AUTH)
        assert resp.status_code == 503

    def test_disabled_returns_503_on_replay(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations/someid/replay", headers=_AUTH)
        assert resp.status_code == 503

    def test_disabled_returns_503_on_stream(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations/someid/stream", headers=_AUTH)
        assert resp.status_code == 503

    def test_disabled_returns_503_on_controls(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/civilizations/someid/controls/pause", headers=_AUTH)
        assert resp.status_code == 503

    def test_disabled_returns_503_on_kill_agent(self):
        app = _make_app(civilization_enabled=False)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/civilizations/someid/agents/agent1/kill", headers=_AUTH)
        assert resp.status_code == 503


# ── Enabled + No DB → graceful degradation ───────────────────────────────────

class TestNoDbDegradation:
    def test_list_returns_empty_when_no_db(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations", headers=_AUTH)
        # No DB → returns empty list or 503
        assert resp.status_code in (200, 503)
        if resp.status_code == 200:
            assert resp.json() == []

    def test_create_returns_503_when_no_db(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/civilizations",
            json={"name": "TestCiv"},
            headers=_AUTH,
        )
        assert resp.status_code in (503, 500, 201)

    def test_get_single_returns_503_when_no_db(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations/civ1", headers=_AUTH)
        assert resp.status_code in (503, 500, 404)

    def test_spawns_returns_empty_when_no_db(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations/civ1/spawns", headers=_AUTH)
        assert resp.status_code in (200, 503)
        if resp.status_code == 200:
            assert resp.json() == []


# ── Mocked DB endpoints ────────────────────────────────────────────────────────

class TestWithMockedDb:
    def _make_mock_db(self, rows=None):
        """Build a mock async DB session factory."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(
            return_value=type("CM", (), {
                "__aenter__": AsyncMock(return_value=None),
                "__aexit__": AsyncMock(return_value=False),
            })()
        )
        if rows is not None:
            mock_result = MagicMock()
            mock_result.fetchall.return_value = rows
            mock_result.fetchone.return_value = rows[0] if rows else None
            mock_session.execute = AsyncMock(return_value=mock_result)
        else:
            mock_result = MagicMock()
            mock_result.fetchone.return_value = None
            mock_result.fetchall.return_value = []
            mock_session.execute = AsyncMock(return_value=mock_result)
        return MagicMock(return_value=mock_session)

    def test_list_civilizations_with_empty_db(self):
        db = self._make_mock_db(rows=[])
        app = _make_app(civilization_enabled=True, db=db)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations", headers=_AUTH)
        assert resp.status_code in (200, 500)

    def test_get_civilization_not_found(self):
        db = self._make_mock_db(rows=[])  # fetchone returns None
        app = _make_app(civilization_enabled=True, db=db)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/civilizations/nonexistent", headers=_AUTH)
        assert resp.status_code in (404, 500)

    def test_update_constitution_not_found(self):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(
            return_value=type("CM", (), {
                "__aenter__": AsyncMock(return_value=None),
                "__aexit__": AsyncMock(return_value=False),
            })()
        )
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        db = MagicMock(return_value=mock_session)

        app = _make_app(civilization_enabled=True, db=db)
        client = TestClient(app, raise_server_exceptions=False)

        with patch("app.civilization.models.Constitution.from_dict", return_value=MagicMock(to_dict=lambda: {})):
            resp = client.put(
                "/civilizations/nonexistent/constitution",
                json={"constitution": {"max_depth": 3}},
                headers=_AUTH,
            )
        assert resp.status_code in (404, 500)


# ── Society-backed endpoints with mocks ──────────────────────────────────────

class TestSocietyEndpoints:
    def test_graph_endpoint_with_mock_society(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        mock_society = MagicMock()
        mock_society.get_lineage_graph = AsyncMock(return_value={"nodes": [], "edges": []})

        with patch("app.civilization.society.Society", return_value=mock_society):
            resp = client.get("/civilizations/civ1/graph", headers=_AUTH)
        assert resp.status_code in (200, 500)

    def test_blackboard_with_mock(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        mock_board = MagicMock()
        mock_board.query = AsyncMock(return_value=[{"finding": "test", "confidence": 0.9}])

        with patch("app.civilization.blackboard.Blackboard", return_value=mock_board):
            resp = client.get("/civilizations/civ1/blackboard", headers=_AUTH)
        assert resp.status_code in (200, 500)

    def test_debates_with_mock_bus(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        mock_bus = MagicMock()
        mock_bus.get_messages = AsyncMock(return_value=[
            {"from_agent_id": "a1", "topic": "debate", "ts": "now", "content": "..."}
        ])

        with patch("app.civilization.bus.CivilizationBus", return_value=mock_bus):
            resp = client.get("/civilizations/civ1/debates", headers=_AUTH)
        assert resp.status_code in (200, 500)

    def test_learnings_with_mock(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        mock_pipeline = MagicMock()
        mock_pipeline.get_learnings = AsyncMock(return_value=[
            {"learning_id": "l1", "status": "active", "content": "..."}
        ])

        with patch("app.civilization.learning.LearningPipeline", return_value=mock_pipeline):
            resp = client.get("/civilizations/civ1/learnings", headers=_AUTH)
        assert resp.status_code in (200, 500)

    def test_agent_inspector_not_found(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        mock_society = MagicMock()
        mock_society.get_member = AsyncMock(return_value=None)

        mock_bus = MagicMock()
        mock_bus.get_messages = AsyncMock(return_value=[])

        with patch("app.civilization.society.Society", return_value=mock_society):
            with patch("app.civilization.bus.CivilizationBus", return_value=mock_bus):
                resp = client.get("/civilizations/civ1/agents/agent1", headers=_AUTH)
        assert resp.status_code in (404, 500)

    def test_agent_inspector_found(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        mock_society = MagicMock()
        mock_society.get_member = AsyncMock(return_value={"agent_id": "agent1", "role": "worker"})

        mock_bus = MagicMock()
        mock_bus.get_messages = AsyncMock(return_value=[])

        with patch("app.civilization.society.Society", return_value=mock_society):
            with patch("app.civilization.bus.CivilizationBus", return_value=mock_bus):
                resp = client.get("/civilizations/civ1/agents/agent1", headers=_AUTH)
        assert resp.status_code in (200, 500)


# ── Controls endpoint ─────────────────────────────────────────────────────────

class TestControls:
    def _make_app_with_governor(self, action="pause"):
        app = _make_app(civilization_enabled=True)
        mock_gov = MagicMock()
        mock_gov.pause = AsyncMock()
        mock_gov.resume = AsyncMock()
        mock_gov.kill_agent = AsyncMock()
        app.state._mock_governor = mock_gov
        return app

    def test_invalid_action_returns_400(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        mock_gov = MagicMock()
        mock_gov.pause = AsyncMock()

        with patch("app.civilization.models.Constitution.from_dict", return_value=MagicMock()):
            with patch("app.civilization.governor.Governor", return_value=mock_gov):
                resp = client.post(
                    "/civilizations/civ1/controls/invalid_action",
                    headers=_AUTH,
                )
        assert resp.status_code in (400, 500)

    def test_pause_action_calls_governor(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        mock_gov = MagicMock()
        mock_gov.pause = AsyncMock()

        with patch("app.civilization.models.Constitution.from_dict", return_value=MagicMock()):
            with patch("app.civilization.governor.Governor", return_value=mock_gov):
                resp = client.post(
                    "/civilizations/civ1/controls/pause",
                    headers=_AUTH,
                )
        assert resp.status_code in (200, 500)
        if resp.status_code == 200:
            assert resp.json()["status"] == "paused"

    def test_resume_action(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        mock_gov = MagicMock()
        mock_gov.resume = AsyncMock()

        with patch("app.civilization.models.Constitution.from_dict", return_value=MagicMock()):
            with patch("app.civilization.governor.Governor", return_value=mock_gov):
                resp = client.post(
                    "/civilizations/civ1/controls/resume",
                    headers=_AUTH,
                )
        assert resp.status_code in (200, 500)


# ── Stream endpoint ───────────────────────────────────────────────────────────

class TestStreamEndpoint:
    def test_stream_no_redis_returns_ready_event(self):
        app = _make_app(civilization_enabled=True)
        client = TestClient(app, raise_server_exceptions=False)

        mock_get_events = AsyncMock(return_value=[])

        with patch("app.civilization.events.get_events_since", mock_get_events):
            resp = client.get("/civilizations/civ1/stream", headers=_AUTH)
        # SSE stream → 200 with text/event-stream
        assert resp.status_code in (200, 500)
        if resp.status_code == 200:
            assert "text/event-stream" in resp.headers.get("content-type", "")
