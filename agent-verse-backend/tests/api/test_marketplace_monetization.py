"""Test marketplace monetization module structure."""


def test_monetization_router_exists():
    from app.api.marketplace_monetization import router
    routes = [r.path for r in router.routes]
    assert any("set-price" in r for r in routes)
    assert any("purchase" in r for r in routes)
    assert any("onboard-author" in r for r in routes)


def test_pricing_request_model():
    from app.api.marketplace_monetization import PricingRequest
    r = PricingRequest(template_id="tpl-test", price_usd=9.99, revenue_share_pct=70)
    assert r.price_usd == 9.99
    assert r.revenue_share_pct == 70


def test_free_template_price_zero():
    from app.api.marketplace_monetization import PricingRequest
    r = PricingRequest(template_id="tpl-free", price_usd=0.0)
    assert r.price_usd == 0.0
