"""Test webhook signature verification is fail-closed in production."""
import os


def test_slack_verify_fails_closed_in_production():
    os.environ["ENVIRONMENT"] = "production"
    try:
        from app.integrations.slack import handler
        import importlib; importlib.reload(handler)
        result = handler.verify_slack_signature(b"body", "ts", "sig", "")
        assert result is False, "Must reject requests when no secret in production"
    finally:
        os.environ["ENVIRONMENT"] = "development"


def test_slack_verify_passes_in_dev_no_secret():
    os.environ["ENVIRONMENT"] = "development"
    from app.integrations.slack import handler
    import importlib; importlib.reload(handler)
    result = handler.verify_slack_signature(b"body", "ts", "sig", "")
    assert result is True, "Should allow in dev mode with no secret"


def test_zapier_verify_fails_closed_in_production():
    os.environ["ENVIRONMENT"] = "production"
    try:
        from app.integrations.zapier import handler
        import importlib; importlib.reload(handler)
        result = handler.verify_zapier_secret("")
        assert result is False
    finally:
        os.environ["ENVIRONMENT"] = "development"


def test_oauth_state_expiry():
    """Expired OAuth state tokens are rejected."""
    import time
    import importlib.util
    import os as _os

    # Load oauth.py directly to bypass app.mcp package __init__.py which imports
    # registry.py (pre-existing SyntaxError from duplicate docstring outside our scope).
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _oauth_path = _os.path.normpath(
        _os.path.join(_here, "../../app/mcp/oauth.py")
    )
    spec = importlib.util.spec_from_file_location("_test_mcp_oauth", _oauth_path)
    oauth_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    # Must be in sys.modules before exec_module so @dataclass can resolve annotations
    import sys as _sys
    _sys.modules["_test_mcp_oauth"] = oauth_mod
    spec.loader.exec_module(oauth_mod)  # type: ignore[union-attr]

    OAuthFlowManager = oauth_mod.OAuthFlowManager
    OAuthState = oauth_mod.OAuthState

    mgr = OAuthFlowManager()
    state = "test-state-123"
    mgr._pending_flows[state] = OAuthState(
        state=state, server_id="srv1", tenant_id="t1",
        redirect_uri="http://localhost/cb", pkce_verifier="v",
        created_at=time.time() - 700,  # 11+ minutes ago
    )
    result = mgr.get_pending_flow(state)
    assert result is None, "Expired state token should be rejected"
