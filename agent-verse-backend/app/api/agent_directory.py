"""A2A Agent Directory — per-agent AgentCards and queryable directory.

NOT IMPLEMENTED. These routes sit on the unauthenticated ``/.well-known`` path.
They used to call ``agent_store.get_agent`` / ``list_agents(public_only=True)``,
methods ``AgentStore`` does not have, inside ``contextlib.suppress(Exception)`` —
so every card was a 404, the directory was always empty, and the advertised
endpoint (``/a2a/agents/{id}``) does not exist either. AgentVerse has no notion
of a *public* agent, so wiring these to the real store would expose every
tenant's agents to anonymous callers. Until a publish/visibility model exists
the routes answer 501 rather than pretending the directory is merely empty.
The platform-level card remains at ``/.well-known/agent.json``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/.well-known/agents", tags=["a2a-directory"])

_NOT_IMPLEMENTED = (
    "NOT IMPLEMENTED: the per-agent A2A directory requires a public-agent "
    "visibility model, which does not exist yet. Use /.well-known/agent.json."
)


@router.get("/{agent_id}.json")
async def get_agent_card(agent_id: str) -> dict:  # type: ignore[type-arg]
    """Per-agent AgentCard (A2A protocol) — not implemented."""
    del agent_id
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)


@router.get("")
async def list_public_agents(limit: int = 20) -> dict:  # type: ignore[type-arg]
    """Public A2A agent directory — not implemented."""
    del limit
    raise HTTPException(status_code=501, detail=_NOT_IMPLEMENTED)
