"""Verify RPA-specific self-improvement suggestions are generated correctly."""
import pytest
from app.intelligence.self_optimization import SelfOptimizer
from app.tenancy.context import TenantContext, PlanTier


def _tenant() -> TenantContext:
    return TenantContext(
        tenant_id="test-rpa-sugg-001",
        plan=PlanTier.FREE,
        api_key_id="test-key",
    )


def test_rpa_selector_failure_generates_text_selector_suggestion():
    """CSS selector timeout → suggest using visible-text selector instead."""
    opt = SelfOptimizer()
    tenant = _tenant()

    suggestions = opt.analyze_rpa_failure(
        tool_name="rpa_click",
        error="Timeout: element '#submit-btn' not found within 5000ms",
        url="https://app.example.com/checkout",
        tenant_ctx=tenant,
    )

    assert len(suggestions) >= 1
    categories = {s.category for s in suggestions}
    assert "rpa_selector" in categories
    found = next(s for s in suggestions if s.category == "rpa_selector")
    assert "text" in found.description.lower() or "visible" in found.description.lower()
    assert found.confidence >= 0.7


def test_rpa_captcha_generates_human_help_suggestion():
    """CAPTCHA error → suggest rpa_detect_captcha + rpa_request_human_help."""
    opt = SelfOptimizer()
    tenant = _tenant()

    suggestions = opt.analyze_rpa_failure(
        tool_name="rpa_click",
        error="rpa_detect_captcha: captcha_detected: true",
        url="https://app.example.com/login",
        tenant_ctx=tenant,
    )

    assert len(suggestions) >= 1
    found = next((s for s in suggestions if "captcha" in s.description.lower()), None)
    assert found is not None
    assert "human" in found.description.lower() or "rpa_request_human_help" in found.after.lower()


def test_rpa_timeout_generates_wait_for_network_idle_suggestion():
    """Network timeout → suggest rpa_wait_for_network_idle before next action."""
    opt = SelfOptimizer()
    tenant = _tenant()

    suggestions = opt.analyze_rpa_failure(
        tool_name="rpa_extract_text",
        error="Timeout: page did not reach networkidle within 10000ms",
        url="https://app.example.com/dashboard",
        tenant_ctx=tenant,
    )

    assert len(suggestions) >= 1
    found = next(
        (s for s in suggestions if "network" in s.description.lower()
         or "wait" in s.description.lower()), None
    )
    assert found is not None


def test_rpa_login_failure_generates_credential_suggestion():
    """Login page auth error → suggest using vault:// credential reference."""
    opt = SelfOptimizer()
    tenant = _tenant()

    suggestions = opt.analyze_rpa_failure(
        tool_name="rpa_type",
        error="Authentication failed: invalid credentials",
        url="https://app.example.com/login",
        tenant_ctx=tenant,
    )

    found = next(
        (s for s in suggestions if "vault" in s.description.lower()
         or "credential" in s.description.lower()), None
    )
    assert found is not None


def test_no_duplicate_rpa_suggestion_for_same_url():
    """Same URL + same error pattern should not generate duplicate suggestions."""
    opt = SelfOptimizer()
    tenant = _tenant()

    for _ in range(3):
        opt.analyze_rpa_failure(
            tool_name="rpa_click",
            error="Timeout: element '#btn' not found",
            url="https://same-url.com",
            tenant_ctx=tenant,
        )

    suggestions = opt.list_suggestions(tenant_ctx=tenant)
    url_suggestions = [
        s for s in suggestions
        if "same-url.com" in (s.description + s.before + s.after)
    ]
    assert len(url_suggestions) <= 2, \
        f"Expected at most 2 suggestions for same url+error, got {len(url_suggestions)}"
