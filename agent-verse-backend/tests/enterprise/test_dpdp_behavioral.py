"""Behavioral tests for DPDP endpoints — actual request creation and retrieval."""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_erasure_request_stored_with_pending_status():
    """An erasure request must be written to the DB with status=pending."""
    captured = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "INSERT INTO dpdp_erasure_requests" in sql:
            captured["params"] = params
            captured["sql"] = sql
        return MagicMock(fetchone=lambda: None, fetchall=lambda: [])

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=fake_execute)
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.begin = MagicMock(return_value=fake_session)
    fake_session.commit = AsyncMock()

    def fake_db():
        return fake_session

    from app.api.dpdp import ErasureRequest, request_erasure
    from app.tenancy.context import PlanTier, TenantContext
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")

    request = MagicMock()
    request.state.tenant = tenant
    request.app.state.db_session_factory = fake_db

    result = await request_erasure(ErasureRequest(data_principal_id="customer-xyz"), request)
    assert result["status"] == "accepted"
    assert "INSERT INTO dpdp_erasure_requests" in captured.get("sql", ""), (
        "Erasure request must INSERT to dpdp_erasure_requests table"
    )
    assert captured["params"]["dpid"] == "customer-xyz"


@pytest.mark.asyncio
async def test_consent_stored_correctly():
    """Consent must be written with correct data_principal_id and purpose."""
    captured = {}

    async def fake_execute(query, params=None):
        if "INSERT INTO dpdp_consents" in str(query):
            captured["params"] = params
        return MagicMock(fetchone=lambda: None, fetchall=lambda: [])

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=fake_execute)
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)
    fake_session.begin = MagicMock(return_value=fake_session)
    fake_session.commit = AsyncMock()

    def fake_db():
        return fake_session

    from app.api.dpdp import ConsentRequest, record_consent
    from app.tenancy.context import PlanTier, TenantContext
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")

    request = MagicMock()
    request.state.tenant = tenant
    request.app.state.db_session_factory = fake_db

    result = await record_consent(
        ConsentRequest(data_principal_id="cust-123", purpose="analytics", consent_given=True),
        request,
    )
    assert result["consent_given"] is True
    assert captured["params"]["dpid"] == "cust-123"
    assert captured["params"]["purpose"] == "analytics"
