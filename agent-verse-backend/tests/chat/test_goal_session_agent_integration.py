"""CHAT-D-3 on a real Postgres: a chat GOAL turn runs with the session's agent.

The session lives only in the database (PostgresChatRepository, least-privilege
NOBYPASSRLS role). Through the real router, a GOAL turn of a session bound to an
agent whose config differs from the tenant default must reach GoalService with
that agent — it used to read process memory, find no session, and submit with
no agent (auto-routing to another one).

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/chat/test_goal_session_agent_integration.py -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.chat.repository import PostgresChatRepository
from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration

_AGENTS: dict[str | None, dict[str, Any]] = {
    None: {"system_prompt": "You are a general assistant.", "model": "m-default"},
    "agent-billing": {"system_prompt": "You only handle invoices.", "model": "m-billing"},
}


class _GoalService:
    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        self.submitted.append({**kwargs, "config": _AGENTS[kwargs.get("agent_id")]})
        return {"goal_id": "g-1", "status": "accepted"}

    async def subscribe_events(
        self, goal_id: str, tenant_ctx: Any, since_sequence: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "goal_complete", "status": "complete"}


async def test_goal_turn_uses_the_sessions_agent_in_db_mode(pg_url: str) -> None:
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    engine = await app_engine(pg_url)
    goals = _GoalService()
    try:
        from fastapi import FastAPI

        app = FastAPI()
        svc = ChatService(goal_service=goals, repository=PostgresChatRepository(sessions(engine)))
        app.state.chat_service = svc
        caller = TenantContext(t, PlanTier.FREE, "user:u1", roles=("operator",), user_id="u1")

        @app.middleware("http")
        async def _who(request: Any, call_next: Any) -> Any:
            request.state.tenant = caller
            return await call_next(request)

        app.include_router(chat_router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://x") as c:
            made = await c.post(
                "/chat/sessions", json={"title": "billing", "agent_id": "agent-billing"}
            )
            assert made.status_code == 201, made.text
            sid = made.json()["id"]
            assert svc.get_session(sid, t) is None  # durable only, not in memory

            sent = await c.post(
                f"/chat/sessions/{sid}/messages",
                json={"content": "Deploy the billing service to staging"},
            )
            assert sent.status_code == 200, sent.text
            assert sent.json()["intent"] == "GOAL"
            mid = sent.json()["message_id"]

            streamed = await c.get(f"/chat/sessions/{sid}/stream", params={"message_id": mid})
            assert streamed.status_code == 200, streamed.text
            assert "goal_complete" in streamed.text

        assert len(goals.submitted) == 1
        run = goals.submitted[0]
        assert run["agent_id"] == "agent-billing"
        assert run["config"] == _AGENTS["agent-billing"] != _AGENTS[None]
        assert run["execution_context"]["session_id"] == sid
    finally:
        await engine.dispose()
