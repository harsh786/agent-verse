"""Comprehensive tests for app/auth/saml_provider.py."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.saml_provider import (
    SAMLIdentity,
    SAMLProvider,
    build_saml_provider_from_config,
)


# ---------------------------------------------------------------------------
# SAMLIdentity
# ---------------------------------------------------------------------------


def test_saml_identity_defaults():
    identity = SAMLIdentity(email="user@example.com", name_id="user@example.com")
    assert identity.email == "user@example.com"
    assert identity.name_id == "user@example.com"
    assert identity.first_name is None
    assert identity.last_name is None
    assert identity.department is None
    assert identity.attributes == {}


def test_saml_identity_full():
    identity = SAMLIdentity(
        email="user@corp.com",
        name_id="uid-123",
        first_name="Alice",
        last_name="Smith",
        department="Engineering",
        attributes={"role": ["admin"]},
    )
    assert identity.first_name == "Alice"
    assert identity.department == "Engineering"
    assert identity.attributes["role"] == ["admin"]


# ---------------------------------------------------------------------------
# SAMLProvider construction
# ---------------------------------------------------------------------------


def _make_provider(**kwargs) -> SAMLProvider:
    defaults = dict(
        tenant_id="t1",
        idp_entity_id="https://idp.example.com",
        idp_sso_url="https://idp.example.com/sso",
        idp_cert="MIICERT",
        sp_entity_id="https://sp.example.com",
        acs_url="https://sp.example.com/api/enterprise/saml/acs",
    )
    defaults.update(kwargs)
    return SAMLProvider(**defaults)


def test_saml_provider_stores_params():
    provider = _make_provider(jit_provisioning=False)
    assert provider._tenant_id == "t1"
    assert provider._idp_sso_url == "https://idp.example.com/sso"
    assert provider._jit_provisioning is False


def test_saml_provider_default_attribute_mapping():
    provider = _make_provider()
    assert provider._attribute_mapping == {}


def test_saml_provider_custom_attribute_mapping():
    mapping = {"email": "mail", "first_name": "givenName"}
    provider = _make_provider(attribute_mapping=mapping)
    assert provider._attribute_mapping == mapping


# ---------------------------------------------------------------------------
# initiate_login — SAML NOT available
# ---------------------------------------------------------------------------


def test_initiate_login_without_saml_returns_idp_url():
    provider = _make_provider()
    with patch("app.auth.saml_provider.SAML_AVAILABLE", False):
        url = provider.initiate_login()
    assert url == "https://idp.example.com/sso"


def test_initiate_login_without_saml_ignores_relay_state():
    provider = _make_provider()
    with patch("app.auth.saml_provider.SAML_AVAILABLE", False):
        url = provider.initiate_login(relay_state="https://app.example.com/dashboard")
    # Still returns the IdP SSO URL directly when SAML lib is absent
    assert url == "https://idp.example.com/sso"


# ---------------------------------------------------------------------------
# initiate_login — SAML available (mocked)
# ---------------------------------------------------------------------------


def test_initiate_login_with_saml_returns_redirect_url():
    provider = _make_provider()
    mock_auth = MagicMock()
    mock_auth.login.return_value = "https://idp.example.com/sso?SAMLRequest=abc"

    with (
        patch("app.auth.saml_provider.SAML_AVAILABLE", True),
        patch("app.auth.saml_provider.OneLogin_Saml2_Auth", return_value=mock_auth, create=True),
    ):
        url = provider.initiate_login(relay_state="https://app.example.com")
    assert "SAMLRequest" in url


def test_initiate_login_saml_exception_falls_back_to_idp_url():
    provider = _make_provider()

    with (
        patch("app.auth.saml_provider.SAML_AVAILABLE", True),
        patch("app.auth.saml_provider.OneLogin_Saml2_Auth", side_effect=Exception("boom"), create=True),
    ):
        url = provider.initiate_login()
    assert url == "https://idp.example.com/sso"


# ---------------------------------------------------------------------------
# process_acs — SAML NOT available
# ---------------------------------------------------------------------------


async def test_process_acs_without_saml_raises_runtime_error():
    provider = _make_provider()
    with patch("app.auth.saml_provider.SAML_AVAILABLE", False):
        with pytest.raises(RuntimeError, match="python3-saml is not installed"):
            await provider.process_acs("base64encodedresponse")


# ---------------------------------------------------------------------------
# process_acs — SAML available (mocked)
# ---------------------------------------------------------------------------


async def test_process_acs_successful_extracts_identity():
    provider = _make_provider()
    mock_auth = MagicMock()
    mock_auth.is_authenticated.return_value = True
    mock_auth.get_nameid.return_value = "user@corp.com"
    mock_auth.get_session_index.return_value = "session-1"
    mock_auth.get_attributes.return_value = {
        "email": ["user@corp.com"],
        "firstName": ["Alice"],
        "lastName": ["Smith"],
    }

    with (
        patch("app.auth.saml_provider.SAML_AVAILABLE", True),
        patch("app.auth.saml_provider.OneLogin_Saml2_Auth", return_value=mock_auth, create=True),
    ):
        identity = await provider.process_acs("saml_response_base64")

    assert identity.email == "user@corp.com"
    assert identity.name_id == "user@corp.com"
    assert identity.first_name == "Alice"
    assert identity.last_name == "Smith"


async def test_process_acs_raises_value_error_on_auth_failure():
    provider = _make_provider()
    mock_auth = MagicMock()
    mock_auth.is_authenticated.return_value = False
    mock_auth.get_errors.return_value = ["InvalidSignature"]

    with (
        patch("app.auth.saml_provider.SAML_AVAILABLE", True),
        patch("app.auth.saml_provider.OneLogin_Saml2_Auth", return_value=mock_auth, create=True),
    ):
        with pytest.raises(ValueError, match="SAML authentication failed"):
            await provider.process_acs("bad_response")


async def test_process_acs_replay_protection_raises_on_replay():
    redis_mock = AsyncMock()
    provider = _make_provider(redis=redis_mock)

    mock_auth = MagicMock()
    mock_auth.is_authenticated.return_value = True
    mock_auth.get_nameid.return_value = "user@corp.com"
    mock_auth.get_session_index.return_value = "sess-42"
    mock_auth.get_attributes.return_value = {}

    # Simulate Redis returning a hit (replay detected)
    redis_mock.exists = AsyncMock(return_value=1)

    with (
        patch("app.auth.saml_provider.SAML_AVAILABLE", True),
        patch("app.auth.saml_provider.OneLogin_Saml2_Auth", return_value=mock_auth, create=True),
    ):
        with pytest.raises(ValueError, match="replay detected"):
            await provider.process_acs("repeated_saml_response")


async def test_process_acs_no_replay_stores_assertion_id():
    redis_mock = AsyncMock()
    provider = _make_provider(redis=redis_mock)

    mock_auth = MagicMock()
    mock_auth.is_authenticated.return_value = True
    mock_auth.get_nameid.return_value = "user@corp.com"
    mock_auth.get_session_index.return_value = "sess-new"
    mock_auth.get_attributes.return_value = {"email": ["user@corp.com"]}

    redis_mock.exists = AsyncMock(return_value=0)
    redis_mock.setex = AsyncMock()

    with (
        patch("app.auth.saml_provider.SAML_AVAILABLE", True),
        patch("app.auth.saml_provider.OneLogin_Saml2_Auth", return_value=mock_auth, create=True),
    ):
        identity = await provider.process_acs("fresh_response")

    redis_mock.setex.assert_awaited_once()
    assert identity.email == "user@corp.com"


async def test_process_acs_uses_attribute_mapping():
    provider = _make_provider(attribute_mapping={"email": "mail", "first_name": "givenName"})
    mock_auth = MagicMock()
    mock_auth.is_authenticated.return_value = True
    mock_auth.get_nameid.return_value = "uid-999"
    mock_auth.get_session_index.return_value = ""
    mock_auth.get_attributes.return_value = {
        "mail": ["mapped@corp.com"],
        "givenName": ["Bob"],
    }

    with (
        patch("app.auth.saml_provider.SAML_AVAILABLE", True),
        patch("app.auth.saml_provider.OneLogin_Saml2_Auth", return_value=mock_auth, create=True),
    ):
        identity = await provider.process_acs("response")

    assert identity.email == "mapped@corp.com"
    assert identity.first_name == "Bob"


# ---------------------------------------------------------------------------
# _check_saml_replay
# ---------------------------------------------------------------------------


async def test_check_saml_replay_returns_true_if_key_exists():
    redis_mock = AsyncMock()
    redis_mock.exists = AsyncMock(return_value=1)
    provider = _make_provider(redis=redis_mock)

    result = await provider._check_saml_replay("user@corp.com:sess-1")
    assert result is True
    redis_mock.setex.assert_not_awaited()


async def test_check_saml_replay_stores_key_and_returns_false_if_new():
    redis_mock = AsyncMock()
    redis_mock.exists = AsyncMock(return_value=0)
    redis_mock.setex = AsyncMock()
    provider = _make_provider(redis=redis_mock)

    result = await provider._check_saml_replay("user@corp.com:sess-2")
    assert result is False
    redis_mock.setex.assert_awaited_once()
    # TTL must be 3600
    args = redis_mock.setex.call_args[0]
    assert args[1] == 3600


async def test_check_saml_replay_fails_open_on_redis_error():
    redis_mock = AsyncMock()
    redis_mock.exists = AsyncMock(side_effect=ConnectionError("redis down"))
    provider = _make_provider(redis=redis_mock)

    result = await provider._check_saml_replay("user@corp.com:sess-3")
    # Fail-open: no replay detected when Redis is unavailable
    assert result is False


# ---------------------------------------------------------------------------
# get_sp_metadata
# ---------------------------------------------------------------------------


def test_get_sp_metadata_without_saml_returns_xml():
    provider = _make_provider()
    with patch("app.auth.saml_provider.SAML_AVAILABLE", False):
        xml = provider.get_sp_metadata()
    assert "EntityDescriptor" in xml
    assert "https://sp.example.com" in xml
    assert "AssertionConsumerService" in xml


def test_get_sp_metadata_with_saml_uses_builder():
    provider = _make_provider()
    mock_metadata = "<md:EntityDescriptor>...</md:EntityDescriptor>"

    with (
        patch("app.auth.saml_provider.SAML_AVAILABLE", True),
        patch(
            "app.auth.saml_provider.OneLogin_Saml2_Metadata",
            create=True,
        ) as mock_meta_cls,
    ):
        mock_meta_cls.builder.return_value = mock_metadata
        xml = provider.get_sp_metadata()
    assert xml == mock_metadata


def test_get_sp_metadata_falls_back_on_builder_exception():
    provider = _make_provider()

    with (
        patch("app.auth.saml_provider.SAML_AVAILABLE", True),
        patch(
            "app.auth.saml_provider.OneLogin_Saml2_Metadata",
            create=True,
        ) as mock_meta_cls,
    ):
        mock_meta_cls.builder.side_effect = Exception("builder error")
        xml = provider.get_sp_metadata()
    # Falls back to minimal XML
    assert "EntityDescriptor" in xml


# ---------------------------------------------------------------------------
# _build_saml_settings
# ---------------------------------------------------------------------------


def test_build_saml_settings_structure():
    provider = _make_provider()
    settings = provider._build_saml_settings()
    assert "sp" in settings
    assert "idp" in settings
    assert settings["sp"]["entityId"] == "https://sp.example.com"
    assert settings["idp"]["entityId"] == "https://idp.example.com"
    assert settings["security"]["wantAssertionsSigned"] is True
    assert settings["strict"] is False


# ---------------------------------------------------------------------------
# _build_empty_request_data
# ---------------------------------------------------------------------------


def test_build_empty_request_data():
    data = SAMLProvider._build_empty_request_data()
    assert data["http_host"] == "localhost"
    assert data["script_name"] == "/api/enterprise/saml/login"
    assert data["post_data"] == {}
    assert "https" in data


def test_build_empty_request_data_https_flag():
    data = SAMLProvider._build_empty_request_data()
    # On Python 3.11+ this should be True; older versions use "on"
    if sys.version_info >= (3, 11):
        assert data["https"] is True
    else:
        assert data["https"] == "on"


# ---------------------------------------------------------------------------
# build_saml_provider_from_config factory
# ---------------------------------------------------------------------------


def test_build_saml_provider_from_config():
    config_row = MagicMock()
    config_row.idp_entity_id = "https://idp.corp.com"
    config_row.idp_sso_url = "https://idp.corp.com/sso"
    config_row.idp_cert = "CERT123"
    config_row.sp_entity_id = "https://app.corp.com"
    config_row.attribute_mapping = {"email": "mail"}
    config_row.name_id_format = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"
    config_row.jit_provisioning = True

    provider = build_saml_provider_from_config(
        tenant_id="t99",
        config_row=config_row,
        base_url="https://app.corp.com",
    )

    assert provider._tenant_id == "t99"
    assert provider._acs_url == "https://app.corp.com/api/enterprise/saml/acs"
    assert provider._idp_cert == "CERT123"
    assert provider._attribute_mapping == {"email": "mail"}
    assert provider._jit_provisioning is True


def test_build_saml_provider_from_config_strips_trailing_slash():
    config_row = MagicMock()
    config_row.idp_entity_id = "https://idp.corp.com"
    config_row.idp_sso_url = "https://idp.corp.com/sso"
    config_row.idp_cert = "CERT"
    config_row.sp_entity_id = "https://app.corp.com"
    config_row.attribute_mapping = {}

    provider = build_saml_provider_from_config(
        tenant_id="t1",
        config_row=config_row,
        base_url="https://app.corp.com/",
    )
    # Trailing slash is stripped before building ACS URL
    assert provider._acs_url == "https://app.corp.com/api/enterprise/saml/acs"


def test_build_saml_provider_from_config_default_jit_provisioning():
    config_row = MagicMock(spec=[
        "idp_entity_id", "idp_sso_url", "idp_cert",
        "sp_entity_id", "attribute_mapping",
    ])
    config_row.idp_entity_id = "https://idp.corp.com"
    config_row.idp_sso_url = "https://idp.corp.com/sso"
    config_row.idp_cert = "CERT"
    config_row.sp_entity_id = "https://app.corp.com"
    config_row.attribute_mapping = None

    provider = build_saml_provider_from_config("t1", config_row, "https://app.corp.com")
    # Default jit_provisioning is True
    assert provider._jit_provisioning is True
