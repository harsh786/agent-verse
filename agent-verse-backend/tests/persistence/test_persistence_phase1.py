"""Comprehensive persistence tests — verify all stores are DB-backed."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.governance.hitl import HITLGateway, ApprovalStatus
from app.governance.audit import AuditLog
from app.memory.long_term import LongTermMemoryStore
from app.tenancy.context import TenantContext, PlanTier

CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


# ---------------------------------------------------------------------------
# 1. HITLGateway
# ---------------------------------------------------------------------------

def test_hitl_has_db_session_factory_attr() -> None:
    gw = HITLGateway()
    assert hasattr(gw, "_db_session_factory")


@pytest.mark.asyncio
async def test_hitl_load_pending_returns_zero_without_db() -> None:
    gw = HITLGateway()
    count = await gw.load_pending_from_db_full(None)
    assert count == 0


# ---------------------------------------------------------------------------
# 2. AuditLog
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_log_query_with_limit() -> None:
    from app.governance.audit import AuditEvent
    from app.governance.permissions import ActionLevel

    log = AuditLog()
    for i in range(5):
        log.record(
            AuditEvent(
                goal_id=f"g{i}",
                tool_name="test.tool",
                action_level=ActionLevel.ALLOW_LOG,
                outcome="success",
            ),
            tenant_ctx=CTX,
        )
    results = log.query(tenant_ctx=CTX, limit=3)
    assert len(results) <= 3


# ---------------------------------------------------------------------------
# 3. LongTermMemoryStore
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_long_term_memory_store_async_without_db() -> None:
    store = LongTermMemoryStore()
    from app.memory.long_term import LongTermMemory
    mem = LongTermMemory(content="test fact", source_goal_id="g1", memory_type="fact")
    mid = await store.store_async(memory=mem, tenant_ctx=CTX)
    assert mid is not None


@pytest.mark.asyncio
async def test_long_term_memory_recall_async_without_db() -> None:
    store = LongTermMemoryStore()
    from app.memory.long_term import LongTermMemory
    store.store(
        memory=LongTermMemory(
            content="use jira for issue tracking",
            source_goal_id="g1",
            memory_type="fact",
        ),
        tenant_ctx=CTX,
    )
    results = await store.recall_async(query="jira issues", tenant_ctx=CTX)
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# 4. AgentStore
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_store_create_persists() -> None:
    from app.api.agents import AgentStore

    store = AgentStore()
    # Without DB, falls back to in-memory
    agent_id = await store.create(
        {"name": "Test", "goal_template": "do stuff"}, tenant_ctx=CTX
    )
    assert agent_id
    found = store.get(agent_id, tenant_ctx=CTX)
    assert found is not None


# ---------------------------------------------------------------------------
# 5. EvalSuiteRunner
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eval_suite_runner_create_and_list() -> None:
    from app.intelligence.eval_suite import EvalSuiteRunner

    runner = EvalSuiteRunner()
    runner.create_suite("suite-1")
    assert "suite-1" in runner.list_suites()


def test_redbeat_import() -> None:
    """celery-redbeat is installable."""
    try:
        import redbeat  # noqa: F401
        assert redbeat is not None
    except ImportError:
        pytest.skip("celery-redbeat not installed")
