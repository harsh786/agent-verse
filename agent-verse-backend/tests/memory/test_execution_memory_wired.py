"""Verify ExecutionMemory is wired into AgentGraph and recall/record works."""
import pytest
from app.memory.execution import ExecutionMemory
from app.tenancy.context import TenantContext, PlanTier


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-exec-mem-wire-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


def test_execution_memory_record_and_recall():
    """Record a winning plan and recall it."""
    mem = ExecutionMemory()
    tenant = _tenant()

    mem.record(
        goal="find all open Jira tickets",
        plan=["Search Jira with JQL: status=Open", "Return results"],
        tenant_ctx=tenant,
    )

    results = mem.recall(goal_hint="Jira tickets", tenant_ctx=tenant, top_k=3)
    assert len(results) == 1
    assert results[0]["goal"] == "find all open Jira tickets"
    assert "Search Jira" in results[0]["plan"][0]


def test_execution_memory_record_failure_and_recall():
    """Record a failure and recall it."""
    mem = ExecutionMemory()
    tenant = _tenant()

    mem.record_failure(
        goal="search Jira for bugs",
        failed_step="jira_search_issues",
        error="JQL syntax error: invalid field 'assigneee'",
        tenant_ctx=tenant,
    )

    failures = mem.recall_failures(goal_hint="Jira", tenant_ctx=tenant, top_k=3)
    assert len(failures) == 1
    assert "assigneee" in failures[0]["error"]


def test_agentgraph_accepts_exec_memory():
    """AgentGraph constructor must accept exec_memory kwarg without error."""
    from unittest.mock import MagicMock
    from app.agent.graph import AgentGraph
    from app.memory.execution import ExecutionMemory

    fake_provider = MagicMock()
    mem = ExecutionMemory()

    graph = AgentGraph(
        planner=fake_provider,
        executor=fake_provider,
        verifier=fake_provider,
        exec_memory=mem,
    )
    assert graph._exec_memory is mem


@pytest.mark.asyncio
async def test_exec_memory_async_record():
    """record_async persists to in-memory store when db=None."""
    mem = ExecutionMemory()

    await mem.record_async(
        goal="test async goal",
        plan=["step 1", "step 2"],
        success=True,
        tenant_id="test-async-001",
        db=None,
    )

    results = mem.recall(
        goal_hint="async goal",
        tenant_ctx=TenantContext(
            tenant_id="test-async-001", plan=PlanTier.FREE, api_key_id="k"
        ),
    )
    assert len(results) == 1
    assert results[0]["plan"] == ["step 1", "step 2"]
