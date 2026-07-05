"""Test all required Settings fields exist with correct defaults."""
from app.core.config import Settings


def test_stripe_billing_fields():
    s = Settings()
    assert hasattr(s, "stripe_price_starter")
    assert hasattr(s, "stripe_price_professional")
    assert hasattr(s, "stripe_price_enterprise")
    assert hasattr(s, "stripe_success_url")
    assert hasattr(s, "stripe_cancel_url")
    assert "billing" in s.stripe_success_url or "agentverse" in s.stripe_success_url


def test_india_compliance_fields():
    s = Settings()
    assert hasattr(s, "seller_gstin")
    assert hasattr(s, "seller_name")
    assert hasattr(s, "dpo_name")
    assert hasattr(s, "dpo_email")
    assert "@" in s.dpo_email


def test_seller_gstin_format():
    """GSTIN must be 15 chars: 2 state + 10 PAN + 1 entity + 1 check + Z."""
    s = Settings()
    assert len(s.seller_gstin) == 15, f"GSTIN must be 15 chars, got {len(s.seller_gstin)}"
