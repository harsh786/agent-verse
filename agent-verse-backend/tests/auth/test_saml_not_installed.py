"""SAML without python3-saml: a clean 501, never a 500 or a fake redirect.

Regression: ``python3-saml`` (``onelogin``) is not a dependency, so on a default
install ``SAML_AVAILABLE`` is False. ``POST /enterprise/saml/acs`` then raised a
bare RuntimeError that the endpoint's catch-all turned into a 500, and
``GET /enterprise/saml/login`` silently redirected to the IdP's SSO URL with no
AuthnRequest — a flow that can never complete. Both now answer 501 "SAML not
installed" (the library is an optional extra: ``uv sync --extra saml``).

Also pinned: when the library IS installed the provider must run python3-saml in
``strict`` mode. It ran with ``strict: False``, in which python3-saml skips the
"assertion must be signed" / destination / audience checks, so an UNSIGNED,
forged SAMLResponse authenticates as any user.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import enterprise as ent
from app.auth.saml_provider import SAMLNotInstalledError, SAMLProvider
from app.tenancy.context import PlanTier, TenantContext

# saml_configs: idp_entity_id, idp_sso_url, idp_cert, sp_entity_id, attribute_mapping,
# default_role, jit_provisioning
_ROW = ("idp-entity", "https://idp.example/sso", "CERT", "sp-entity", {}, "viewer", True)


def _provider() -> SAMLProvider:
    return SAMLProvider(
        tenant_id="t1",
        idp_entity_id="idp",
        idp_sso_url="https://idp.example/sso",
        idp_cert="CERT",
        sp_entity_id="sp",
        acs_url="https://sp.example/enterprise/saml/acs",
    )


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    def begin(self) -> _Session:
        return self

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        result = MagicMock()
        result.fetchone.return_value = None if "set_config" in str(stmt) else _ROW
        return result


def _request() -> Any:
    request = MagicMock()
    request.state.tenant = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")
    request.app.state.db_session_factory = _Session
    request.app.state.redis = None
    request.base_url = "http://testserver/"
    request.form = AsyncMock(return_value={"SAMLResponse": "PHNhbWw+"})
    return request


@pytest.mark.asyncio
async def test_provider_raises_typed_error_without_library() -> None:
    with patch("app.auth.saml_provider.SAML_AVAILABLE", False):
        with pytest.raises(SAMLNotInstalledError):
            await _provider().process_acs("PHNhbWw+")
        with pytest.raises(SAMLNotInstalledError):
            _provider().initiate_login()


@pytest.mark.asyncio
async def test_acs_endpoint_is_501_not_500() -> None:
    with patch("app.auth.saml_provider.SAML_AVAILABLE", False):
        with pytest.raises(HTTPException) as exc:
            await ent.saml_acs(_request(), "t1")
    assert exc.value.status_code == 501
    assert "SAML not installed" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_login_endpoint_is_501_not_a_bare_idp_redirect() -> None:
    with patch("app.auth.saml_provider.SAML_AVAILABLE", False):
        with pytest.raises(HTTPException) as exc:
            await ent.saml_login(_request())
    assert exc.value.status_code == 501


def test_python3_saml_runs_in_strict_mode() -> None:
    settings = _provider()._build_saml_settings()
    assert settings["strict"] is True
    assert settings["security"]["wantAssertionsSigned"] is True
