"""Behavioral tests for marketplace monetization."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.api.marketplace_monetization import PricingRequest, OnboardAuthorRequest


def test_pricing_request_validates_revenue_share():
    """Revenue share must be between 0 and 100."""
    r = PricingRequest(template_id="tpl-test", price_usd=9.99, revenue_share_pct=70)
    assert r.revenue_share_pct == 70
    assert r.price_usd == 9.99


def test_pricing_request_allows_free_templates():
    r = PricingRequest(template_id="tpl-free", price_usd=0.0)
    assert r.price_usd == 0.0


def test_router_has_set_price_endpoint():
    from app.api.marketplace_monetization import router
    paths = [r.path for r in router.routes]
    assert any("set-price" in p for p in paths)
    assert any("purchase" in p for p in paths)
    assert any("onboard" in p for p in paths)


@pytest.mark.asyncio
async def test_set_price_writes_to_db():
    """set_template_price must UPDATE marketplace_templates in DB."""
    captured: dict = {}

    async def fake_execute(query, params=None):
        sql = str(query)
        if "UPDATE marketplace_templates" in sql:
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

    from app.api.marketplace_monetization import set_template_price
    from app.tenancy.context import TenantContext, PlanTier

    request = MagicMock()
    request.state.tenant = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1"
    )
    request.app.state.db_session_factory = fake_db

    result = await set_template_price(
        PricingRequest(template_id="tpl-legal", price_usd=29.99, revenue_share_pct=70),
        request,
    )

    assert result["price_usd"] == 29.99
    assert "UPDATE marketplace_templates" in captured.get("sql", ""), (
        "set_template_price must UPDATE the marketplace_templates table"
    )
    assert captured["params"]["price"] == 29.99
