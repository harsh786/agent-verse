"""Public A2A agent directory — ``/.well-known/agents`` (owner decision D3).

Unauthenticated discovery documents (the tenant middleware exempts
``/.well-known/``). Off by default: an agent appears only when its tenant turned
the directory on and the agent opted in (``a2a_public``) and is active — see
:mod:`app.services.a2a_directory`. A card is minimal (agent_id, name,
description, skills, endpoint) and never carries tools, prompts, model,
connector names or the tenant id. Both routes are rate-limited per client IP
(Redis across replicas, an in-process window without it) and the listing is
keyset-paginated by agent id. Anything not publicly listed — private, deleted,
another tenant's, unknown — is the same 404. The platform card stays at
``/.well-known/agent.json``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.services.a2a_directory import (
    DIRECTORY_PAGE_MAX,
    DirectoryUnavailableError,
    agent_card,
    directory_for,
)

router = APIRouter(prefix="/.well-known/agents", tags=["a2a-directory"])

_RATE_BUCKET = "a2a_directory"
_RATE_LIMIT = 60  # requests per client IP ...
_RATE_WINDOW_S = 60  # ... per this many seconds


def _reset_local_rate_limits() -> None:
    """Test seam: forget the in-process windows of this bucket."""
    from app.tenancy import ip_rate_limit

    hits = getattr(ip_rate_limit, "_local_windows", None)
    if isinstance(hits, dict):
        for key in [k for k in hits if str(k).startswith(f"{_RATE_BUCKET}:")]:
            hits.pop(key, None)


async def _rate_limit(request: Request) -> None:
    from app.tenancy.ip_rate_limit import enforce_ip_rate_limit

    await enforce_ip_rate_limit(
        request,
        bucket=_RATE_BUCKET,
        limit=_RATE_LIMIT,
        window_s=_RATE_WINDOW_S,
        redis=getattr(request.app.state, "_redis", None),
        detail="Too many agent directory requests from this IP. Try again later.",
    )


@router.get("/{agent_id}.json")
async def get_agent_card(request: Request, agent_id: str) -> dict[str, Any]:
    """The public card of one publicly listed agent (404 for anything else)."""
    await _rate_limit(request)
    try:
        found = await directory_for(request.app.state).get_public(agent_id)
    except DirectoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Agent directory unavailable") from exc
    if found is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent_card(found, str(request.base_url))


@router.get("")
async def list_public_agents(
    request: Request,
    limit: int = Query(default=20, ge=1, le=DIRECTORY_PAGE_MAX),
    cursor: str | None = Query(default=None, max_length=64),
) -> dict[str, Any]:
    """Publicly listed agents, keyset-paginated by agent id (``next_cursor``)."""
    await _rate_limit(request)
    try:
        page = await directory_for(request.app.state).list_public(after=cursor, limit=limit)
    except DirectoryUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Agent directory unavailable") from exc
    base = str(request.base_url)
    return {
        "agents": [agent_card(a, base) for a in page],
        "next_cursor": page[-1].agent_id if len(page) == limit else None,
    }
