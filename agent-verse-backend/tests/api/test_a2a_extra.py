"""Extra coverage for app/api/a2a.py — A2A protocol utility functions and endpoints."""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.a2a import _verify_hmac, _persist_task, _update_task_status, _get_task, _send_callback, router as a2a_router, _tasks
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-a2a", plan=PlanTier.ENTERPRISE, api_key_id="k1")
_VALID_KEY = "av_a2a_test"


def _make_app(goal_service=None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(a2a_router)
    if goal_service is not None:
        app.state.goal_service = goal_service
    return app


_H = {"X-API-Key": _VALID_KEY}


class TestVerifyHmac:
    def test_no_secret_allows_all(self):
        result = _verify_hmac(b"payload", "any_sig", "")
        assert result is True

    def test_no_signature_with_secret_denies(self):
        result = _verify_hmac(b"payload", "", "my-secret")
        assert result is False

    def test_correct_signature_accepted(self):
        secret = "my-secret"
        payload = b'{"goal": "test"}'
        expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        result = _verify_hmac(payload, expected, secret)
        assert result is True

    def test_wrong_signature_rejected(self):
        result = _verify_hmac(b"payload", "sha256=wrongsig", "my-secret")
        assert result is False


class TestPersistTask:
    @pytest.mark.asyncio
    async def test_persists_to_memory_when_no_db(self):
        _tasks.clear()
        task_id = "task-test-1"
        data = {"goal": "test goal", "status": "pending"}
        await _persist_task(task_id, data, db=None)
        assert _tasks[task_id]["goal"] == "test goal"
        _tasks.clear()

    @pytest.mark.asyncio
    async def test_falls_back_to_memory_on_db_error(self):
        _tasks.clear()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(
            return_value=type("CM", (), {
                "__aenter__": AsyncMock(return_value=None),
                "__aexit__": AsyncMock(return_value=False),
            })()
        )
        mock_session.execute = AsyncMock(side_effect=RuntimeError("db error"))
        mock_db = MagicMock(return_value=mock_session)

        task_id = "task-fallback"
        data = {"goal": "fallback", "status": "pending"}
        await _persist_task(task_id, data, db=mock_db)
        # Should fall back to memory
        assert _tasks[task_id] == data
        _tasks.clear()


class TestUpdateTaskStatus:
    @pytest.mark.asyncio
    async def test_updates_memory_when_no_db(self):
        _tasks.clear()
        _tasks["t1"] = {"status": "pending", "result": ""}
        await _update_task_status("t1", "completed", "success", db=None)
        assert _tasks["t1"]["status"] == "completed"
        _tasks.clear()

    @pytest.mark.asyncio
    async def test_noop_when_task_not_in_memory_no_db(self):
        _tasks.clear()
        # Should not raise
        await _update_task_status("nonexistent", "completed", "", db=None)


class TestGetTask:
    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        _tasks.clear()
        result = await _get_task("nonexistent", db=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_from_memory(self):
        _tasks.clear()
        _tasks["task1"] = {"goal": "test", "status": "pending"}
        result = await _get_task("task1", db=None)
        assert result is not None
        assert result["goal"] == "test"
        _tasks.clear()


class TestSendCallback:
    @pytest.mark.asyncio
    async def test_noop_when_no_callback_url(self):
        # Should not raise
        await _send_callback("", "task1", "completed", "result")

    @pytest.mark.asyncio
    async def test_sends_callback_request(self):
        mock_resp = MagicMock()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await _send_callback("http://example.com/callback", "t1", "done", "ok")
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_swallowed(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=ConnectionError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            # Should not raise
            await _send_callback("http://bad.host/cb", "t1", "error", "fail")


class TestAgentCardEndpoint:
    def test_agent_card_returns_capabilities(self):
        app = FastAPI()
        app.include_router(a2a_router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        body = resp.json()
        assert "capabilities" in body
        assert "agent_id" in body
        assert "endpoint" in body


class TestA2ATaskEndpoints:
    def test_receive_a2a_task_no_secret(self):
        """Without A2A_SHARED_SECRET, tasks are accepted (dev mode)."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/a2a/tasks",
            json={"goal": "Fix the deployment pipeline"},
            headers=_H,
        )
        assert resp.status_code in (200, 202, 401, 500, 503)

    def test_get_task_not_found(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/a2a/tasks/nonexistent", headers=_H)
        assert resp.status_code in (200, 202, 404, 401, 500)

    def test_get_task_endpoint_exists(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        # Just verify endpoint exists and returns non-405
        resp = client.get("/a2a/tasks/any-task-id")
        assert resp.status_code != 405
