"""Solutions API — installable domain solution packages (app.api.solutions).

The solutions_catalog module itself is covered by
tests/enterprise/test_solutions.py; this file exercises the FastAPI router
endpoints on top of it.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.solutions import (
    InstallRequest,
    get_solution_detail,
    install_solution,
    list_all_solutions,
    solutions_stats,
)
from app.enterprise.solutions_catalog import DOMAIN_SOLUTIONS


@pytest.mark.asyncio
async def test_list_all_solutions_no_filter():
    result = await list_all_solutions()
    assert result["total"] == len(DOMAIN_SOLUTIONS)
    assert len(result["solutions"]) == len(DOMAIN_SOLUTIONS)


@pytest.mark.asyncio
async def test_list_all_solutions_filtered_by_domain():
    result = await list_all_solutions(domain="legal")
    assert result["total"] >= 1
    assert all(s["domain"] == "legal" for s in result["solutions"])


@pytest.mark.asyncio
async def test_list_all_solutions_unknown_domain_returns_empty():
    result = await list_all_solutions(domain="not-a-real-domain")
    assert result == {"solutions": [], "total": 0}


@pytest.mark.asyncio
async def test_get_solution_detail_found():
    sol = await get_solution_detail("law-firm")
    assert sol["slug"] == "law-firm"
    assert sol["domain"] == "legal"


@pytest.mark.asyncio
async def test_get_solution_detail_not_found_raises_404():
    with pytest.raises(HTTPException) as exc:
        await get_solution_detail("nonexistent-solution")
    assert exc.value.status_code == 404
    assert "nonexistent-solution" in exc.value.detail


@pytest.mark.asyncio
async def test_install_solution_not_found_raises_404():
    with pytest.raises(HTTPException) as exc:
        await install_solution(
            "nonexistent-solution", InstallRequest(tenant_id="t1"), MagicMock()
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_install_solution_returns_plan():
    result = await install_solution("law-firm", InstallRequest(tenant_id="t1"), MagicMock())
    assert result["slug"] == "law-firm"
    assert result["tenant_id"] == "t1"
    assert result["status"] == "planned"
    assert isinstance(result["steps"], list)
    assert len(result["steps"]) == 3
    assert any("agent" in s.lower() for s in result["steps"])
    assert any("knowledge" in s.lower() for s in result["steps"])
    assert any("onboarding" in s.lower() for s in result["steps"])


@pytest.mark.asyncio
async def test_install_solution_includes_onboarding_steps():
    result = await install_solution("law-firm", InstallRequest(tenant_id="t2"), MagicMock())
    from app.enterprise.solutions_catalog import get_solution

    sol = get_solution("law-firm")
    assert result["onboarding_steps"] == sol.get("onboarding_steps", [])


@pytest.mark.asyncio
async def test_solutions_stats():
    result = await solutions_stats()
    assert result["total_solutions"] == len(DOMAIN_SOLUTIONS)
    all_domains = {s["domain"] for s in DOMAIN_SOLUTIONS}
    assert set(result["domains"]) == all_domains
    assert result["domains"] == sorted(all_domains)
    assert result["total_domains"] == len(all_domains)
