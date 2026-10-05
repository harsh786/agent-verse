"""MDB-07 / C6 / TG-05: TLS validation can never be switched off by a tenant.

``tlsInsecure``, ``tlsAllowInvalidCertificates``, ``tlsAllowInvalidHostnames``,
``tlsDisableOCSPEndpointCheck``, ``tlsDisableCertificateRevocationCheck``,
``ssl_cert_reqs`` and ``tls=false`` / ``ssl=false`` were accepted on BOTH the MCP
builtin and the ingestion connector (and the ``tls_allow_invalid_certificates``
field was honoured): against a server with an untrusted certificate the probe
connected. One shared validator now refuses them on both paths; plain-text
transport is allowed only by the dev-only ``MONGODB_ALLOW_NON_TLS`` setting,
which production ignores.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import get_settings
from app.ingestion.connectors.mongodb_connector import _settings
from app.mcp.servers import mongodb_server
from app.net.mongodb_policy import MongoTlsPolicyError, assert_tls_not_weakened

WEAKENING_URI_OPTIONS = [
    "tlsInsecure=true",
    "tlsAllowInvalidCertificates=true",
    "tlsAllowInvalidHostnames=true",
    "tlsDisableOCSPEndpointCheck=true",
    "tlsDisableCertificateRevocationCheck=true",
    "tlsinsecure=TRUE",
    "tls=true&tlsAllowInvalidCertificates=1",
    "ssl_cert_reqs=CERT_NONE",
    "sslAllowInvalidCertificates=true",
    "sslAllowInvalidHostnames=true",
]
PLAINTEXT_URI_OPTIONS = ["tls=false", "ssl=false", "TLS=False", "ssl=0"]
WEAKENING_FIELDS: list[dict[str, Any]] = [
    {"tls_allow_invalid_certificates": True},
    {"tls_allow_invalid_certificates": "true"},
    {"tls_allow_invalid_hostnames": True},
    {"tls_insecure": "yes"},
]
PLAINTEXT_FIELDS: list[dict[str, Any]] = [{"tls": False}, {"tls": "false"}, {"ssl": False}]


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _set(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


class _NoClient:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        raise AssertionError("a TLS-weakened client must never be built")


@pytest.fixture
def no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import pymongo

    monkeypatch.setattr(pymongo, "MongoClient", _NoClient)


# ── the shared validator ─────────────────────────────────────────────────────


@pytest.mark.parametrize("option", WEAKENING_URI_OPTIONS + PLAINTEXT_URI_OPTIONS)
def test_validator_refuses_uri_options(option: str) -> None:
    with pytest.raises(MongoTlsPolicyError, match="TLS"):
        assert_tls_not_weakened(f"mongodb://8.8.8.8:27017/db?{option}")


@pytest.mark.parametrize("fields", WEAKENING_FIELDS + PLAINTEXT_FIELDS)
def test_validator_refuses_config_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(MongoTlsPolicyError, match="TLS"):
        assert_tls_not_weakened("mongodb://8.8.8.8:27017/db", fields)


@pytest.mark.parametrize(
    "option",
    ["tls=true", "ssl=true", "tlsInsecure=false", "tlsAllowInvalidCertificates=false", ""],
)
def test_validator_allows_verified_tls(option: str) -> None:
    assert_tls_not_weakened(f"mongodb://8.8.8.8:27017/db?{option}", {"tls": True})


def test_non_tls_only_with_the_dev_setting(settings_env: Any) -> None:
    settings_env(MONGODB_ALLOW_NON_TLS="true", ENVIRONMENT="development")
    assert_tls_not_weakened("mongodb://8.8.8.8:27017/db?tls=false", {"tls": False})
    # The dev setting never re-enables certificate bypasses.
    with pytest.raises(MongoTlsPolicyError):
        assert_tls_not_weakened("mongodb://8.8.8.8:27017/db?tlsInsecure=true")


def test_production_ignores_the_dev_setting(settings_env: Any) -> None:
    settings_env(MONGODB_ALLOW_NON_TLS="true", ENVIRONMENT="production")
    with pytest.raises(MongoTlsPolicyError):
        assert_tls_not_weakened("mongodb://8.8.8.8:27017/db?tls=false")


# ── MCP builtin path ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("option", WEAKENING_URI_OPTIONS + PLAINTEXT_URI_OPTIONS)
async def test_mcp_refuses_tls_weakening_uri(option: str, no_client: None) -> None:
    result = await mongodb_server.call_tool(
        "mongodb_list_collections", {}, credentials={"url": f"mongodb://8.8.8.8:27017/db?{option}"}
    )
    assert result["status"] == "tls_refused", result
    assert "TLS" in result["error"]


@pytest.mark.parametrize("fields", WEAKENING_FIELDS + PLAINTEXT_FIELDS)
async def test_mcp_refuses_tls_weakening_fields(fields: dict[str, Any], no_client: None) -> None:
    result = await mongodb_server.call_tool(
        "mongodb_list_collections", {}, credentials={"url": "mongodb://8.8.8.8:27017/db", **fields}
    )
    assert result["status"] == "tls_refused", result


# ── ingestion connector path ─────────────────────────────────────────────────


@pytest.mark.parametrize("option", WEAKENING_URI_OPTIONS + PLAINTEXT_URI_OPTIONS)
def test_ingestion_refuses_tls_weakening_uri(option: str) -> None:
    with pytest.raises(ValueError, match="TLS"):
        _settings({"uri": f"mongodb://8.8.8.8:27017/?{option}", "database": "d"})


@pytest.mark.parametrize("fields", WEAKENING_FIELDS + PLAINTEXT_FIELDS)
def test_ingestion_refuses_tls_weakening_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="TLS"):
        _settings({"uri": "mongodb://8.8.8.8:27017/", "database": "d", **fields})


def test_ingestion_never_sets_an_invalid_certificate_kwarg() -> None:
    s = _settings({"uri": "mongodb://8.8.8.8:27017/", "database": "d", "tls": True})
    assert not any("Invalid" in k or "Insecure" in k for k in s.kwargs)
