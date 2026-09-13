"""Focused unit test for OrgService.create_mission status support (Task 6, Step 1).

OrgBrain's ACT step needs to create "proposed" missions (autonomy L3 verdict:
propose, awaiting human approval) without marking them active/dispatched. This
pins the new `status` kwarg added to `create_mission` in app/org/service.py —
the signature stays backward compatible (no new required arg) while callers
can now override the initial mission status.

Mirrors the AsyncMock-session pattern used by
tests/org/test_org_router.py::TestOrgServiceCreate (no real DB needed —
create_mission only calls session.add/flush and a best-effort Redis publish
that no-ops when no publisher is wired).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.org.service import OrgService

TENANT_ID = "t-brain-status"


def _service() -> OrgService:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    return OrgService(session=session, tenant_id=TENANT_ID)


@pytest.mark.asyncio
async def test_create_mission_with_proposed_status() -> None:
    svc = _service()
    mission = await svc.create_mission(
        org_id=str(uuid.uuid4()),
        title="Advance goal g1",
        objective="advance g1",
        source="autonomous",
        status="proposed",
    )
    assert mission.status == "proposed"


@pytest.mark.asyncio
async def test_create_mission_default_status_still_works_without_arg() -> None:
    """Callers that never pass `status` keep working unchanged (no new required arg)."""
    svc = _service()
    mission = await svc.create_mission(org_id=str(uuid.uuid4()), title="Manual mission")
    assert mission.status == "active"
