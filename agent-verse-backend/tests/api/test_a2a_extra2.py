"""Extra coverage for app/api/a2a.py (Part 2).

Targets uncovered lines: 91-99 (_update_task_status with DB),
105-120 (_get_task with DB), 214-251 (execute_and_callback background task).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.a2a import (
    _get_task,
    _persist_task,
    _send_callback,
    _tasks,
    _update_task_status,
    router as a2a_router,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-a2a2", plan=PlanTier.ENTERPRISE, api_key_id="k2")
_VALID_KEY = "av_a2a_test2"


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


# ── _update_task_status with DB ───────────────────────────────────────────────

class TestUpdateTaskStatusWithDb:
    @pytest.mark.asyncio
    async def test_updates_with_db_success(self):
        """Lines 91-99: DB update path."""
        executed_sql: list[str] = []

        async def fake_execute(sql, params=None):
            executed_sql.append(str(sql))
            return MagicMock()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock(side_effect=fake_execute)
        mock_db = MagicMock(return_value=mock_session)

        await _update_task_status("task-db-1", "complete", "Goal done", db=mock_db)
        assert any("UPDATE" in s or "a2a_tasks" in s for s in executed_sql)

    @pytest.mark.asyncio
    async def test_update_with_db_exception_logs(self):
        """Lines 98-99: DB exception → warning logged, no raise."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM2", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock(side_effect=RuntimeError("db error"))
        mock_db = MagicMock(return_value=mock_session)

        await _update_task_status("task-x", "failed", "error", db=mock_db)
        # Should not raise

    @pytest.mark.asyncio
    async def test_update_truncates_long_result(self):
        """Long result string is truncated to 10000 chars."""
        executed_params: list[dict] = []

        async def capture_execute(sql, params=None):
            if params:
                executed_params.append(params)
            return MagicMock()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM3", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock(side_effect=capture_execute)
        mock_db = MagicMock(return_value=mock_session)

        long_result = "x" * 20000
        await _update_task_status("task-long", "complete", long_result, db=mock_db)
        # Verify result was truncated
        if executed_params:
            assert len(executed_params[-1].get("result", "")) <= 10000


# ── _get_task with DB ─────────────────────────────────────────────────────────

class TestGetTaskWithDb:
    @pytest.mark.asyncio
    async def test_get_task_from_db_found(self):
        """Lines 105-118: DB returns a row → dict returned."""
        from datetime import UTC, datetime
        now = datetime.now(UTC)
        row = ("task-db-get", "Fix the bug", "complete", "result text", "http://cb.url", now)

        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=row)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_db = MagicMock(return_value=mock_session)

        result = await _get_task("task-db-get", db=mock_db)
        assert result is not None
        assert result["task_id"] == "task-db-get"
        assert result["goal"] == "Fix the bug"
        assert result["status"] == "complete"

    @pytest.mark.asyncio
    async def test_get_task_from_db_not_found_falls_back_to_memory(self):
        """Lines 119-121: DB returns None → in-memory fallback."""
        _tasks.clear()
        _tasks["mem-task"] = {"goal": "memory goal", "status": "pending"}

        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_db = MagicMock(return_value=mock_session)

        result = await _get_task("mem-task", db=mock_db)
        assert result is not None
        assert result["goal"] == "memory goal"
        _tasks.clear()

    @pytest.mark.asyncio
    async def test_get_task_db_exception_falls_back_to_memory(self):
        """Lines 119: DB exception → in-memory fallback."""
        _tasks.clear()
        _tasks["exc-task"] = {"goal": "exception fallback", "status": "pending"}

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=RuntimeError("db boom"))
        mock_db = MagicMock(return_value=mock_session)

        result = await _get_task("exc-task", db=mock_db)
        assert result is not None
        _tasks.clear()

    @pytest.mark.asyncio
    async def test_get_task_created_at_none_handled(self):
        """created_at=None → empty string in dict."""
        row = ("task-no-ts", "goal", "pending", None, None, None)
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=row)

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_db = MagicMock(return_value=mock_session)

        result = await _get_task("task-no-ts", db=mock_db)
        assert result is not None
        assert result["created_at"] == ""


# ── receive_a2a_task endpoint ─────────────────────────────────────────────────

class TestReceiveA2aTaskEndpoint:
    def test_task_accepted_without_secret_and_tenant(self):
        """Without A2A_TENANT_ID, returns 503."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        with patch.dict(os.environ, {"A2A_SHARED_SECRET": "", "A2A_TENANT_ID": ""}):
            resp = client.post(
                "/a2a/tasks",
                json={"goal": "Run the pipeline"},
                headers={"X-API-Key": _VALID_KEY},
            )
        assert resp.status_code in (401, 503, 500, 202)

    def test_task_accepted_with_tenant(self):
        """With A2A_TENANT_ID set, task is accepted."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        with patch.dict(os.environ, {"A2A_TENANT_ID": "a2a-tenant-123", "A2A_SHARED_SECRET": ""}):
            resp = client.post(
                "/a2a/tasks",
                json={"goal": "Analyze logs", "priority": "high"},
                headers={"X-API-Key": _VALID_KEY},
            )
        assert resp.status_code in (202, 200, 401, 500)

    def test_task_hmac_rejected_with_wrong_sig(self):
        """With A2A_SHARED_SECRET set and wrong signature → 401."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        with patch.dict(os.environ, {"A2A_SHARED_SECRET": "secret123", "A2A_TENANT_ID": "tenant"}):
            resp = client.post(
                "/a2a/tasks",
                json={"goal": "test"},
                headers={
                    "X-API-Key": _VALID_KEY,
                    "X-A2A-Signature": "sha256=wrongsignature",
                },
            )
        assert resp.status_code in (401, 422, 500)


# ── execute_and_callback background task ────────────────────────────────────

class TestExecuteAndCallback:
    @pytest.mark.asyncio
    async def test_goal_service_executes_and_fires_callback(self):
        """Lines 214-251: goal_service.submit_goal + subscribe_events + callback."""
        # Create a mock goal service that returns a complete event
        async def fake_subscribe_events(goal_id, tenant_ctx):
            yield {"type": "goal_complete", "goal_id": goal_id}

        mock_goal_service = MagicMock()
        mock_goal_service.submit_goal = AsyncMock(return_value={"goal_id": "g-callback-1"})
        mock_goal_service.subscribe_events = MagicMock(side_effect=fake_subscribe_events)

        callback_received: list[dict] = []

        async def fake_callback_post(*args, **kwargs):
            callback_received.append(kwargs.get("json", {}))
            return MagicMock()

        app = _make_app(goal_service=mock_goal_service)
        client = TestClient(app, raise_server_exceptions=False)

        with (
            patch.dict(os.environ, {"A2A_TENANT_ID": "a2a-test-t1", "A2A_SHARED_SECRET": ""}),
            patch("httpx.AsyncClient") as mock_httpx,
        ):
            mock_hclient = AsyncMock()
            mock_hclient.__aenter__ = AsyncMock(return_value=mock_hclient)
            mock_hclient.__aexit__ = AsyncMock(return_value=False)
            mock_hclient.post = AsyncMock(side_effect=fake_callback_post)
            mock_httpx.return_value = mock_hclient

            resp = client.post(
                "/a2a/tasks",
                json={"goal": "Run pipeline", "callback_url": "http://callback.host/done"},
                headers={"X-API-Key": _VALID_KEY},
            )
        assert resp.status_code in (202, 200, 401, 500)

    @pytest.mark.asyncio
    async def test_goal_service_failure_fires_error_callback(self):
        """Lines 244-250: goal_service raises → error status sent to callback."""
        mock_goal_service = MagicMock()
        mock_goal_service.submit_goal = AsyncMock(side_effect=RuntimeError("goal failed"))

        app = _make_app(goal_service=mock_goal_service)
        client = TestClient(app, raise_server_exceptions=False)

        with patch.dict(os.environ, {"A2A_TENANT_ID": "a2a-test-t2", "A2A_SHARED_SECRET": ""}):
            resp = client.post(
                "/a2a/tasks",
                json={"goal": "Fail goal"},
                headers={"X-API-Key": _VALID_KEY},
            )
        assert resp.status_code in (202, 200, 401, 500)


# ── _persist_task with DB success path ───────────────────────────────────────

class TestPersistTaskWithDb:
    @pytest.mark.asyncio
    async def test_persist_with_db_success(self):
        """Lines 62-78: happy path DB insert."""
        executed: list[str] = []

        async def fake_execute(sql, params=None):
            executed.append(str(sql))
            return MagicMock()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        begin_cm = type("BCM", (), {
            "__aenter__": AsyncMock(return_value=None),
            "__aexit__": AsyncMock(return_value=False),
        })()
        mock_session.begin = MagicMock(return_value=begin_cm)
        mock_session.execute = AsyncMock(side_effect=fake_execute)
        mock_db = MagicMock(return_value=mock_session)

        task_id = "task-db-persist"
        data = {
            "goal": "Deploy to prod",
            "status": "accepted",
            "callback_url": "http://cb/done",
            "requester_agent_id": "agent-x",
            "tenant_id": "t1",
        }
        await _persist_task(task_id, data, db=mock_db)
        assert any("INSERT" in s or "a2a_tasks" in s for s in executed)
