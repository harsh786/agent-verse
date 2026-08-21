"""Solutions API — installable domain solution packages."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.enterprise.solutions_catalog import DOMAIN_SOLUTIONS, get_solution, list_solutions

router = APIRouter(prefix="/solutions", tags=["solutions"])


@router.get("")
async def list_all_solutions(domain: str | None = None) -> dict:  # type: ignore[type-arg]
    """List all available domain solutions, optionally filtered by domain."""
    results = list_solutions(domain=domain)
    return {"solutions": results, "total": len(results)}


@router.get("/{slug}")
async def get_solution_detail(slug: str) -> dict:  # type: ignore[type-arg]
    """Get full detail for a solution by slug."""
    sol = get_solution(slug)
    if not sol:
        raise HTTPException(status_code=404, detail=f"Solution '{slug}' not found")
    return sol


class InstallRequest(BaseModel):
    tenant_id: str


@router.post("/{slug}/install")
async def install_solution(slug: str, body: InstallRequest, request: Request) -> dict:  # type: ignore[type-arg]
    """Atomically install a solution for a tenant.

    Returns an installation plan describing what will be created.
    Full atomic execution (agent creation, knowledge seeding, schedule wiring)
    is wired in Phase 7 completion.
    """
    sol = get_solution(slug)
    if not sol:
        raise HTTPException(status_code=404, detail=f"Solution '{slug}' not found")

    return {
        "solution_id": sol["id"],
        "slug": slug,
        "tenant_id": body.tenant_id,
        "status": "planned",
        "steps": [
            f"Create {len(sol.get('agents_config', []))} agent(s)",
            f"Create {len(sol.get('knowledge_recipes', []))} knowledge collection(s)",
            f"Configure {len(sol.get('onboarding_steps', []))} onboarding steps",
        ],
        "onboarding_steps": sol.get("onboarding_steps", []),
    }


# Expose catalog count for health / metrics
@router.get("/catalog/stats")
async def solutions_stats() -> dict:  # type: ignore[type-arg]
    """Return aggregate stats about the solutions catalog."""
    domains = list({s["domain"] for s in DOMAIN_SOLUTIONS})
    return {
        "total_solutions": len(DOMAIN_SOLUTIONS),
        "domains": sorted(domains),
        "total_domains": len(domains),
    }
