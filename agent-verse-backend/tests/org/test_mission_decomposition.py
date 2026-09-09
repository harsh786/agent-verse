"""WS-2b: real mission decomposition, assignment, handoff and progress events.

These unit-level tests pin the composer/decomposition behaviour with the DB
layer stubbed (AsyncMock create_task/update_task_status/_emit_event). The full
DB + real-goal path is covered by tests/e2e_full/test_org_any_task_e2e.py.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.org.service import OrgService


def _service() -> OrgService:
    svc = OrgService(session=MagicMock(), tenant_id="t-decomp")
    return svc


@pytest.mark.asyncio
async def test_decompose_mission_heuristic_multiple_subtasks() -> None:
    svc = _service()
    subtasks = await svc.decompose_mission(
        objective="Research the market and build a launch plan for a new product",
        title="Launch plan",
        team_departments=["research", "operations", "growth"],
    )
    assert len(subtasks) >= 2
    # Every subtask is a real, ordered work item.
    for i, st in enumerate(subtasks):
        assert st["title"]
        assert st["objective"]
        assert st["order"] == i


@pytest.mark.asyncio
async def test_decompose_mission_single_department_uses_phases() -> None:
    svc = _service()
    subtasks = await svc.decompose_mission(
        objective="Summarize this document",
        title="Summarize",
        team_departments=["research"],
    )
    # Falls back to a phased plan (>=2 subtasks) even for a single department.
    assert len(subtasks) >= 2


@pytest.mark.asyncio
async def test_decomposition_emits_assignment_handoff_progress_events() -> None:
    svc = _service()
    mission = SimpleNamespace(id=uuid.uuid4(), org_id=uuid.uuid4(), title="Do the thing")

    created: list[str] = []

    async def _fake_create_task(**kw: Any) -> Any:
        t = SimpleNamespace(id=uuid.uuid4(), title=kw.get("title"))
        created.append(str(t.id))
        return t

    emitted: list[str] = []

    async def _fake_emit(org_id: Any, event_type: str, **kw: Any) -> Any:
        emitted.append(event_type)
        return SimpleNamespace()

    svc.create_task = AsyncMock(side_effect=_fake_create_task)  # type: ignore[method-assign]
    svc.update_task_status = AsyncMock()  # type: ignore[method-assign]
    svc._emit_event = AsyncMock(side_effect=_fake_emit)  # type: ignore[method-assign]

    tasks = await svc.decompose_and_assign(
        mission=mission,
        org_id=str(mission.org_id),
        objective="Research and then build and then verify the deliverable",
        title="Do the thing",
        team_id=str(uuid.uuid4()),
        team_departments=["research", "engineering", "operations"],
        viz_agent_ids=["m1-agent-1", "m1-agent-2", "m1-agent-3"],
    )

    assert len(tasks) >= 2
    assert "task.decomposed" in emitted
    assert "task.assigned" in emitted
    assert "agent.working" in emitted
    assert "task.handoff" in emitted  # at least one handoff between consecutive subtasks
    assert "mission.progress" in emitted
    # A subtask is created + moved to assigned for each work item.
    assert svc.update_task_status.await_count >= len(tasks)
