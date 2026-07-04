"""A2A Agent Directory — per-agent AgentCards and queryable directory."""
from __future__ import annotations

import contextlib

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/.well-known/agents", tags=["a2a-directory"])


@router.get("/{agent_id}.json")
async def get_agent_card(agent_id: str, request: Request) -> dict:  # type: ignore[type-arg]
    """Return AgentCard for a specific agent (A2A protocol)."""
    app_state = request.app.state
    agent_store = getattr(app_state, "agent_store", None)

    if agent_store is None:
        raise HTTPException(status_code=503, detail="Agent store unavailable")

    # Try to find the agent
    agent = None
    with contextlib.suppress(Exception):
        agent = await agent_store.get_agent(agent_id)

    if agent is None:
        # Check across all tenants for public agents
        with contextlib.suppress(Exception):
            agents_list = agent_store.list_agents(public_only=True)
            if hasattr(agents_list, "__await__"):
                agents_list = await agents_list  # type: ignore[assignment]
            agent = next((a for a in agents_list if a.get("id") == agent_id), None)

    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    return {
        "id": agent_id,
        "name": agent.get("name", agent_id),
        "description": agent.get("description", ""),
        "version": "1.0",
        "capabilities": {
            "input_modes": ["text"],
            "output_modes": ["text"],
            "streaming": True,
        },
        "endpoint": f"{request.base_url}a2a/agents/{agent_id}",
        "auth": {
            "type": "bearer",
            "description": "AgentVerse API key in Authorization header",
        },
    }


@router.get("")
async def list_public_agents(request: Request, limit: int = 20) -> dict:  # type: ignore[type-arg]
    """List public agents in the A2A directory."""
    app_state = request.app.state
    agent_store = getattr(app_state, "agent_store", None)

    agents: list[dict] = []  # type: ignore[type-arg]
    if agent_store is not None:
        with contextlib.suppress(Exception):
            all_agents = await agent_store.list_agents(public_only=True)
            agents = all_agents[:limit]

    return {
        "agents": [
            {
                "id": a.get("id", ""),
                "name": a.get("name", ""),
                "description": a.get("description", ""),
            }
            for a in agents
        ],
        "total": len(agents),
    }
