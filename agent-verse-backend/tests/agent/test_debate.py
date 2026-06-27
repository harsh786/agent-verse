"""Tests for DebateOrchestrator multi-agent pattern and batch goals endpoint."""
from __future__ import annotations

import pytest
from app.agent.debate import DebateOrchestrator, AgentProposal


def test_agent_proposal() -> None:
    p = AgentProposal(agent_id="agent_1", proposal="Use jira API")
    assert p.agent_id == "agent_1"
    assert p.votes_received == 0


@pytest.mark.asyncio
async def test_debate_single_round() -> None:
    from app.providers.fake import FakeProvider

    fake = FakeProvider(responses=[
        "Use the REST API for data",       # agent_1 proposal
        "Use GraphQL for efficiency",       # agent_2 proposal
        "agent_2",                          # agent_1 votes
        "agent_1",                          # agent_2 votes
    ])
    debate = DebateOrchestrator(provider=fake, n_agents=2, rounds=1)
    result = await debate.run("Fetch customer data")
    assert result.winning_proposal is not None
    assert result.winning_agent in ["agent_1", "agent_2"]
    assert 0.0 <= result.consensus_level <= 1.0


@pytest.mark.asyncio
async def test_batch_goals_endpoint() -> None:
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "B", "email": "batch@t.com"})
        assert r.status_code == 201
        c.headers["X-API-Key"] = r.json()["api_key"]
        r2 = await c.post("/goals/batch", json={
            "goals": ["goal one", "goal two", "goal three"],
            "priority": "normal",
        })
        assert r2.status_code == 202
        data = r2.json()
        assert data["total"] == 3
        assert "batch_id" in data
