"""Test scope enforcement GET bypass fix (FIX 0.12)."""
import os
from unittest.mock import patch


def test_scope_enforcement_legacy_allow_in_settings():
    from app.core.config import Settings
    s = Settings()
    assert hasattr(s, "scope_enforcement_legacy_allow"), (
        "Settings must have scope_enforcement_legacy_allow field"
    )
    assert s.scope_enforcement_legacy_allow is False, (
        "scope_enforcement_legacy_allow must default to False (secure by default)"
    )


def test_write_blocked_for_no_roles_by_default():
    """Role-less keys must not bypass write checks."""
    with patch.dict(os.environ, {"SCOPE_ENFORCEMENT_LEGACY_ALLOW": "false"}):
        from app.core.config import Settings
        s = Settings()
        assert not s.scope_enforcement_legacy_allow


def test_scope_enforcement_uses_typed_settings():
    """scope_enforcement.py must read from get_settings() not os.getenv()."""
    import pathlib
    src = pathlib.Path("app/auth/scope_enforcement.py").read_text()
    assert "get_settings" in src or "_gs()" in src, (
        "scope_enforcement.py must use get_settings() for typed config"
    )
    assert "INSUFFICIENT_SCOPE" in src, (
        "Error response must use 'INSUFFICIENT_SCOPE' code for role-less key writes"
    )


def test_scope_enforcement_grants_scopes_in_error():
    """Error response for role-less keys must include granted_scopes field."""
    import pathlib
    src = pathlib.Path("app/auth/scope_enforcement.py").read_text()
    assert "granted_scopes" in src, (
        "Error response must include 'granted_scopes' field for client debugging"
    )


def test_status_in_exempt_paths():
    from app.auth.scope_enforcement import EXEMPT_PATH_PREFIXES
    assert any("status" in p for p in EXEMPT_PATH_PREFIXES), (
        "/status must be in EXEMPT_PATH_PREFIXES so the public status page works without auth"
    )
