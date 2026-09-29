"""Goal dedup must only merge submissions that would run identically.

Regression: the dedup key was tenant + goal text only, so a live submission was
"deduplicated" onto an in-flight dry run (and returned its goal_id — nothing
real ever ran), or onto a run by a different agent / under a different autonomy
mode. The key now fingerprints agent, dry-run, workflow mode, priority,
permissions and the execution context as well.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.dedup import GoalDeduplicator
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-dedup", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _svc() -> GoalService:
    svc = GoalService(task_queue=MagicMock())  # queued: the first goal stays in flight
    return svc


async def _submit(svc: GoalService, dedup: GoalDeduplicator, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"goal": "Ship the release notes", "priority": "normal",
                            "dry_run": False, "tenant_ctx": T}
    args.update(kw)
    with (
        patch("app.services.dedup._default_deduplicator", dedup),
        patch("app.tenancy.limits.check_and_increment_concurrent_goals", AsyncMock()),
    ):
        return await svc.submit_goal(**args)


@pytest.mark.asyncio
async def test_live_submission_is_not_deduplicated_onto_an_in_flight_dry_run() -> None:
    svc = _svc()
    dedup = GoalDeduplicator()
    # A dry run that is still registered (in flight) for the same text.
    from app.services.dedup import goal_dedup_scope

    dry_scope = goal_dedup_scope(
        agent_id=None, dry_run=True, workflow_mode="single_agent", priority="normal",
        execution_context={},
    )
    await dedup.register(T.tenant_id, "Ship the release notes", "dry-goal", scope=dry_scope)

    live = await _submit(svc, dedup)

    assert not live.get("deduplicated"), "a real run must never attach to a dry run"
    assert live["goal_id"] != "dry-goal"


@pytest.mark.asyncio
async def test_different_agent_is_not_deduplicated() -> None:
    svc = _svc()
    dedup = GoalDeduplicator()
    with patch.object(svc, "_validate_agent_id"):
        first = await _submit(svc, dedup, agent_id="agent-a")
        second = await _submit(svc, dedup, agent_id="agent-b")
    assert not second.get("deduplicated")
    assert second["goal_id"] != first["goal_id"]


@pytest.mark.asyncio
async def test_different_autonomy_context_is_not_deduplicated() -> None:
    svc = _svc()
    dedup = GoalDeduplicator()
    first = await _submit(svc, dedup, execution_context={"autonomy_mode": "supervised"})
    second = await _submit(
        svc, dedup, execution_context={"autonomy_mode": "fully-autonomous"}
    )
    assert not second.get("deduplicated")
    assert second["goal_id"] != first["goal_id"]


@pytest.mark.asyncio
async def test_identical_submission_is_still_deduplicated() -> None:
    svc = _svc()
    dedup = GoalDeduplicator()
    first = await _submit(svc, dedup, execution_context={"autonomy_mode": "supervised"})
    second = await _submit(svc, dedup, execution_context={"autonomy_mode": "supervised"})
    assert second.get("deduplicated") is True
    assert second["goal_id"] == first["goal_id"]


@pytest.mark.asyncio
async def test_terminal_event_releases_the_scoped_key() -> None:
    svc = _svc()
    dedup = GoalDeduplicator()
    first = await _submit(svc, dedup)
    with patch("app.services.dedup._default_deduplicator", dedup):
        await svc._dispatch_event(first["goal_id"], {"type": "goal_failed"}, tenant_ctx=T)
    again = await _submit(svc, dedup)
    assert not again.get("deduplicated"), "the failed goal's dedup key must be released"
