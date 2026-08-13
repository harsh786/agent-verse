from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.intelligence.improvement_action_executor import (
    ImprovementActionExecutor,
)
from app.memory.contracts import ImprovementActionRecord

ACTIONS = (
    "store_reflexion_lesson",
    "update_prompt_variant",
    "update_model_routing",
    "update_rag_strategy",
    "blacklist_tool_pattern",
    "create_regression_case",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("action_type", ACTIONS)
async def test_every_governed_action_reaches_a_durable_terminal_result(action_type: str) -> None:
    async def handler(payload):
        return {"evidence_ref": payload["evidence_ref"], "rollback_ref": "rollback://1"}

    executor = ImprovementActionExecutor(handlers={action_type: handler})
    record = ImprovementActionRecord(
        action_id=f"action:{action_type}",
        tenant_id="tenant",
        goal_id="goal",
        action_type=action_type,
        payload={"evidence_ref": "evidence://1"},
        state="pending",
        idempotency_key=f"command:{action_type}",
        attempts=0,
        created_at=datetime.now(UTC),
    )
    terminal = await executor.execute(record, policy_allowed=True)
    assert terminal.state == "completed"
    assert terminal.result is not None and terminal.result["rollback_ref"] == "rollback://1"
    assert await executor.execute(record, policy_allowed=True) == terminal


@pytest.mark.asyncio
async def test_policy_denial_fails_closed_before_handler() -> None:
    called = False

    async def handler(payload):
        nonlocal called
        called = True
        return payload

    record = ImprovementActionRecord(
        action_id="denied",
        tenant_id="tenant",
        goal_id="goal",
        action_type="update_prompt_variant",
        payload={"candidate": "v2"},
        state="pending",
        idempotency_key="denied",
        attempts=0,
        created_at=datetime.now(UTC),
    )
    terminal = await ImprovementActionExecutor(
        handlers={"update_prompt_variant": handler}
    ).execute(record, policy_allowed=False)
    assert terminal.state == "failed" and terminal.error_code == "policy_denied"
    assert not called
