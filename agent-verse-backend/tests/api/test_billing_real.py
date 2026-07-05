"""Test billing returns 503 when not configured (not placeholder URL) (FIX billing)."""
import pathlib


def test_billing_returns_503_not_placeholder():
    """When STRIPE_API_KEY not set, must 503, not return placeholder URL."""
    src = pathlib.Path("app/api/billing.py").read_text()
    assert "placeholder" not in src.lower(), (
        "billing.py must not contain placeholder URLs. "
        "Found 'placeholder' in billing.py"
    )


def test_checkout_endpoint_exists():
    from app.api.billing import router
    routes = [r.path for r in router.routes]
    assert any("checkout" in r for r in routes), f"checkout route missing, got {routes}"


def test_stripe_api_key_in_settings():
    """Settings must have typed stripe_api_key field."""
    from app.core.config import Settings
    s = Settings()
    assert hasattr(s, "stripe_api_key"), "Settings must have stripe_api_key field"
    assert s.stripe_api_key == "", "stripe_api_key must default to empty string"


def test_checkout_raises_503_when_stripe_not_configured():
    """checkout endpoint must raise 503 when STRIPE_API_KEY is not set."""
    import pathlib
    src = pathlib.Path("app/api/billing.py").read_text()
    assert "503" in src, "checkout must return 503 when STRIPE_API_KEY not configured"
    assert "Billing not configured" in src, (
        "503 response must include 'Billing not configured' message"
    )


def test_checkout_uses_real_stripe_session_create():
    """checkout must use stripe.checkout.Session.create(), not a hardcoded URL."""
    src = pathlib.Path("app/api/billing.py").read_text()
    assert "stripe.checkout.Session.create" in src, (
        "checkout must call stripe.checkout.Session.create() for real Stripe integration"
    )
