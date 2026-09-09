"""Tests for OrgDigitalTwin — app/org/digital_twin.py"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_digital_twin_exists():
    from app.org.digital_twin import OrgDigitalTwin
    twin = OrgDigitalTwin()
    assert twin is not None


@pytest.mark.asyncio
async def test_digital_twin_sync_accepts_event():
    from app.org.digital_twin import OrgDigitalTwin
    twin = OrgDigitalTwin()
    event = {"event_type": "org.mission.created", "org_id": "org1", "payload": {}}
    await twin.sync(event)   # should not raise
    assert twin._synced_event_counts["org1"] == 1


@pytest.mark.asyncio
async def test_digital_twin_simulate_returns_result():
    from app.org.digital_twin import OrgDigitalTwin
    twin = OrgDigitalTwin()
    result = await twin.simulate_mission(
        org_id="org1",
        mission_config={"goal": "Research competitors"},
    )
    assert result is not None
    assert hasattr(result, "estimated_duration_h")


@pytest.mark.asyncio
async def test_digital_twin_capacity_forecast():
    from app.org.digital_twin import OrgDigitalTwin
    twin = OrgDigitalTwin()
    forecast = await twin.capacity_plan(org_id="org1")
    assert forecast is not None


@pytest.mark.asyncio
async def test_digital_twin_never_modifies_production():
    """Digital twin operations must be read-only/simulation only."""
    from app.org.digital_twin import OrgDigitalTwin
    twin = OrgDigitalTwin()
    # simulate should not write to real DB
    result = await twin.simulate_mission(
        org_id="org1",
        mission_config={"goal": "Test"},
    )
    # If it reaches here without DB error, twin is stateless
    assert result is not None
    # No DB session was ever attached — nothing could have been written.
    assert twin._db is None
