"""NF-1: URI options are read the way the DRIVER reads them ('&' OR ';').

``?appName=a;tlsInsecure=true``, ``?appName=a;tlsCAFile=/path`` and
``?appName=a;authMechanism=MONGODB-AWS`` slipped past a ``parse_qsl`` check
(which only splits on '&') and pymongo accepted them. One shared policy now
parses options like pymongo (both separators, percent-decoding, pymongo's own
normalisation) on the MCP builtin and the ingestion connector.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ingestion.connectors.mongodb_connector import _settings
from app.mcp.servers import mongodb_server
from app.net.mongodb_policy import assert_mongo_connection_allowed, uri_options

BASE = "mongodb://8.8.8.8:27017/db"

BYPASSES = [
    # ';' separators
    "appName=a;tlsInsecure=true",
    "appName=a;tlsAllowInvalidCertificates=true",
    "appName=a;tlsCAFile=/etc/ssl/private/platform.pem",
    "appName=a;tlsCertificateKeyFile=/var/run/secrets/key.pem",
    "appName=a;tlsCRLFile=/x",
    "appName=a;authMechanism=MONGODB-AWS",
    "appName=a;authMechanism=GSSAPI",
    "appName=a;authMechanism=MONGODB-OIDC",
    "appName=a;authMechanismProperties=ENVIRONMENT:azure",
    "appName=a;proxyHost=10.0.0.5",
    "appName=a;ssl=false",
    # '&' (still refused)
    "tlsCAFile=/x",
    "authMechanism=MONGODB-AWS",
    "authMechanism=GSSAPI",
    # percent-encoded option names / values
    "tls%43AFile=/x",
    "authMechanism=MONGODB%2DAWS",
    "appName=a%3B&tlsInsecure=true",
    # legacy / alias spellings
    "sslCAFile=/x",
    "ssl_ca_certs=/x",
    "sslAllowInvalidCertificates=true",
    "tlsCertificateKeyFilePassword=x",
    # mixed separators: pymongo refuses to parse them, so do we
    "appName=a&w=1;tlsInsecure=true",
    # MONGODB-X509 needs the tenant's own certificate field
    "authMechanism=MONGODB-X509",
]


@pytest.mark.parametrize("query", BYPASSES)
def test_validator_refuses(query: str) -> None:
    with pytest.raises(ValueError):
        assert_mongo_connection_allowed(f"{BASE}?{query}")


@pytest.mark.parametrize("query", BYPASSES)
async def test_mcp_refuses_before_connecting(query: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import pymongo

    def _no_client(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("no client may be built")

    monkeypatch.setattr(pymongo, "MongoClient", _no_client)
    result = await mongodb_server.call_tool(
        "mongodb_count", {"collection": "c"}, credentials={"url": f"{BASE}?{query}"}
    )
    assert result.get("status") in ("credentials_required", "tls_refused"), result


@pytest.mark.parametrize("query", BYPASSES)
def test_ingestion_refuses(query: str) -> None:
    with pytest.raises(ValueError):
        _settings({"uri": f"mongodb://8.8.8.8:27017/?{query}", "database": "d"})


def test_semicolon_options_are_all_seen() -> None:
    keys = {k.lower() for k, _ in uri_options(f"{BASE}?appName=a;replicaSet=rs0;w=1")}
    assert {"appname", "replicaset", "w"} <= keys


@pytest.mark.parametrize(
    "query", ["appName=a;replicaSet=rs0", "tls=true&authSource=admin", "retryWrites=true"]
)
def test_ordinary_options_are_allowed(query: str) -> None:
    assert_mongo_connection_allowed(f"{BASE}?{query}")


def test_x509_with_a_client_certificate_is_allowed() -> None:
    assert_mongo_connection_allowed(
        f"{BASE}?authMechanism=MONGODB-X509&tls=true",
        {"tls_client_cert": "-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----"},
    )


def test_ingestion_reads_semicolon_direct_connection() -> None:
    s = _settings(
        {"uri": "mongodb://8.8.8.8:27017/?appName=a;directConnection=true", "database": "d"}
    )
    assert s.discover_members is False
