"""MEM-14: department memory is tenant-scoped and actually reaches the planner.

* retrieve requires the tenant; the DB-less store is keyed by (tenant, dept) so
  two tenants with the same dept_id never see each other's entries.
* AgentGraph uses the DB-wired singleton (the one org CRUD writes to) with the
  goal's tenant, and the planner prompt carries the department entries.
* The org MCP gateway's search fallback searches the org's departments — it used
  to pass the org id as a department id and always found nothing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.memory.dept_memory import DepartmentMemory, get_dept_memory
from app.tenancy.context import PlanTier, TenantContext


async def test_two_tenants_same_dept_id_are_isolated_without_db() -> None:
    mem = DepartmentMemory()
    await mem.add(dept_id="eng", org_id="o1", tenant_id="tA", content="A rotates keys weekly",
                  source="s")
    await mem.add(dept_id="eng", org_id="o2", tenant_id="tB", content="B rotates keys daily",
                  source="s")

    a = await mem.retrieve("eng", "rotates keys", tenant_id="tA")
    b = await mem.retrieve("eng", "rotates keys", tenant_id="tB")

    assert [e.content for e in a] == ["A rotates keys weekly"]
    assert [e.content for e in b] == ["B rotates keys daily"]
    assert [e["content"] for e in mem.list_entries("eng", tenant_id="tA")] == [
        "A rotates keys weekly"
    ]


async def test_retrieve_requires_tenant() -> None:
    mem = DepartmentMemory()
    with pytest.raises(TypeError):
        await mem.retrieve("eng", "anything")  # type: ignore[call-arg]


async def test_retrieve_for_org_spans_the_orgs_departments() -> None:
    mem = DepartmentMemory()
    await mem.add(dept_id="eng", org_id="o1", tenant_id="tA", content="deploy on fridays is banned",
                  source="s")
    await mem.add(dept_id="ops", org_id="o1", tenant_id="tA", content="deploy window is 9-5",
                  source="s")
    await mem.add(dept_id="eng", org_id="o9", tenant_id="tA", content="deploy other org",
                  source="s")

    hits = await mem.retrieve_for_org("o1", "deploy", top_k=5, tenant_id="tA")

    assert {e.content for e in hits} == {"deploy on fridays is banned", "deploy window is 9-5"}
    assert await mem.retrieve_for_org("o1", "deploy", tenant_id="tB") == []


async def test_planner_prompt_gets_department_memory_from_the_shared_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    shared = DepartmentMemory()
    monkeypatch.setattr("app.memory.dept_memory._dept_memory", shared)
    assert get_dept_memory() is shared
    tenant = TenantContext(tenant_id="t-dept", plan=PlanTier.ENTERPRISE, api_key_id="k")
    # Written the way the org CRUD route writes (get_dept_memory().add).
    await get_dept_memory().add(
        dept_id="finance", org_id="org-1", tenant_id="t-dept",
        content="SOP: budget approval required for spend over 5000", source="user",
    )
    await get_dept_memory().add(
        dept_id="finance", org_id="org-x", tenant_id="other-tenant",
        content="SOP: other tenant secret budget rule", source="user",
    )

    planner = FakeProvider(responses=['["Step 1: approve the budget spend"]'])
    graph = AgentGraph(
        planner=planner,
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        max_iterations=1,
    )
    await graph.run(
        goal="approve a budget spend of 7000", tenant_ctx=tenant,
        org_id="org-1", dept_id="finance",
    )

    prompt = "\n".join(m.content for m in planner.call_history[0].messages)
    assert "budget approval required for spend over 5000" in prompt
    assert "other tenant secret" not in prompt


async def test_mcp_search_fallback_uses_org_departments_with_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.gateway.mcp_server import OrgMCPServer

    shared = DepartmentMemory()
    monkeypatch.setattr("app.memory.dept_memory._dept_memory", shared)
    await shared.add(dept_id="eng", org_id="org-7", tenant_id="t7",
                     content="runbook: restart the api pod", source="s")
    await shared.add(dept_id="eng", org_id="org-7", tenant_id="other",
                     content="runbook: other tenant", source="s")

    server = OrgMCPServer(org_id="org-7", tenant_id="t7")
    monkeypatch.setattr(server, "_get_knowledge_store", MagicMock(return_value=None))
    result: dict[str, Any] = await server._tool_search_knowledge({"query": "runbook restart"}, {})

    assert [r["content"] for r in result["results"]] == ["runbook: restart the api pod"]
    assert result["results"][0]["source"] == "dept_memory"
