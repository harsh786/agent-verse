"""Regression: a missing compliance-bundle store must not lift the autonomy ceiling.

``compliance_autonomy_ceiling`` returned ``fully-autonomous`` when
``app.state.compliance_bundle_store`` was absent, silently dropping e.g. HIPAA's
``supervised`` limit for every goal submitted on that app.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.services.goal_service import (
    compliance_autonomy_ceiling,
    resolve_effective_autonomy_mode,
)


@pytest.mark.asyncio
async def test_missing_store_fails_closed_to_supervised() -> None:
    assert await compliance_autonomy_ceiling(SimpleNamespace(), tenant_id="t") == "supervised"
    assert await compliance_autonomy_ceiling(FastAPI(), tenant_id="t") == "supervised"


@pytest.mark.asyncio
async def test_missing_store_clamps_a_requested_fully_autonomous_mode() -> None:
    mode = await resolve_effective_autonomy_mode(
        SimpleNamespace(), tenant_id="t", requested="fully-autonomous"
    )
    assert mode == "supervised"


@pytest.mark.asyncio
async def test_no_app_at_all_reports_no_ceiling() -> None:
    assert await compliance_autonomy_ceiling(None, tenant_id="t") == "fully-autonomous"
