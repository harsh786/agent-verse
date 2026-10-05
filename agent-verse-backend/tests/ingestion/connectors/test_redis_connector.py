"""Unit tests for RedisConnector's config handling, cursor, secrets and egress.

The real-server behaviour for every auth mode (no auth, requirepass, ACL, TLS,
mTLS, URLs, Sentinel, Cluster) lives in
tests/ingestion/test_redis_source_integration.py.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.ingestion.connectors.redis_connector import (
    RedisConnector,
    _decode_cursor,
    _encode_cursor,
    _settings,
)
from app.ingestion.source_config import SourceConfig, SourceFamily
from app.ingestion.source_secrets import (
    ENC_PREFIX,
    MASK,
    encrypt_connection_config,
    mask_connection_config,
)


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-r",
        tenant_id="t1",
        name="r",
        family=SourceFamily.NOSQL_DATABASE,
        source_type="redis",
        connection_config=cc,
    )


class TestSettings:
    def test_host_port_defaults(self) -> None:
        s = _settings({"host": "cache.example"})
        assert (s.mode, s.host, s.port, s.db) == ("standalone", "cache.example", 6379, 0)
        assert (s.username, s.password, s.tls) == ("", "", False)
        assert s.key_patterns == ["*"]

    def test_url_with_acl_credentials_and_db(self) -> None:
        s = _settings({"uri": "rediss://alice:p%40ss%3Aw@cache.example:6380/3"})
        assert (s.host, s.port, s.db) == ("cache.example", 6380, 3)
        assert (s.username, s.password) == ("alice", "p@ss:w")
        assert s.tls is True

    def test_fields_override_url_credentials(self) -> None:
        s = _settings({"uri": "redis://u:urlpw@h:1/0", "password": "fieldpw"})
        assert s.password == "fieldpw"

    @pytest.mark.parametrize(
        "uri",
        [
            "unix:///var/run/redis.sock",
            "redis://h:1/0?ssl_certfile=/etc/platform.pem",
            "redis://h/abc",
            "redis:///0",
        ],
    )
    def test_unsafe_or_malformed_urls_are_refused(self, uri: str) -> None:
        with pytest.raises(ValueError):
            _settings({"uri": uri})

    def test_auth_types(self) -> None:
        assert _settings({"host": "h", "auth_type": "none", "password": "x"}).password == ""
        s = _settings({"host": "h", "auth_type": "password", "password": "p", "username": "u"})
        assert (s.username, s.password) == ("", "p")
        s = _settings({"host": "h", "auth_type": "acl", "username": "u", "password": "p"})
        assert (s.username, s.password) == ("u", "p")
        with pytest.raises(ValueError, match="password"):
            _settings({"host": "h", "auth_type": "password"})
        with pytest.raises(ValueError, match="username"):
            _settings({"host": "h", "auth_type": "acl", "password": "p"})
        with pytest.raises(ValueError, match="auth_type"):
            _settings({"host": "h", "auth_type": "kerberos"})

    def test_tls_fields(self) -> None:
        s = _settings({"host": "h", "tls_ca_pem": "CA"})
        assert s.tls is True and s.tls_check_hostname is True
        assert _settings({"host": "h", "tls": True, "tls_check_hostname": False}).tls_check_hostname is False
        with pytest.raises(ValueError, match="together"):
            _settings({"host": "h", "tls_client_cert": "C"})

    def test_sentinel_needs_sentinels_and_master(self) -> None:
        s = _settings(
            {
                "mode": "sentinel",
                "sentinels": "s1, s2:26380",
                "sentinel_master": "mymaster",
                "sentinel_password": "sp",
                "tls": True,
            }
        )
        assert s.sentinels == [("s1", 26379), ("s2", 26380)]
        assert s.sentinel_tls is True  # follows tls unless set
        with pytest.raises(ValueError, match="sentinel"):
            _settings({"mode": "sentinel", "sentinels": "s1"})

    def test_cluster_seeds_and_db(self) -> None:
        assert _settings({"mode": "cluster", "cluster_nodes": "a:7000,b"}).cluster_nodes == [
            ("a", 7000),
            ("b", 6379),
        ]
        assert _settings({"mode": "cluster", "host": "seed"}).cluster_nodes == [("seed", 6379)]
        with pytest.raises(ValueError, match="database 0"):
            _settings({"mode": "cluster", "host": "seed", "db": 2})

    def test_types_and_caps(self) -> None:
        s = _settings({"host": "h", "types": "hash, JSON", "max_keys_per_sync": 5})
        assert s.types == frozenset({"hash", "json"}) and s.max_keys == 5
        with pytest.raises(ValueError, match="unsupported"):
            _settings({"host": "h", "types": ["bitmap"]})

    def test_needs_a_host(self) -> None:
        with pytest.raises(ValueError, match="host"):
            _settings({})


class TestCursor:
    def test_round_trip_and_new_pass_rules(self) -> None:
        patterns = ["a:*"]
        cursor = _encode_cursor({"p": 0, "n": {"h:1": 42}, "o": {"h:1": 7}}, patterns)
        # P1c-7: the offset inside the SCAN batch round-trips too.
        assert _decode_cursor(cursor, patterns) == {"p": 0, "n": {"h:1": 42}, "o": {"h:1": 7}}
        # Patterns changed or the pass finished -> start a new pass.
        assert _decode_cursor(cursor, ["b:*"]) == {"p": 0, "n": {}, "o": {}}
        done = _encode_cursor({"p": 1, "n": {}}, patterns, done=True)
        assert json.loads(done)["done"] is True
        assert _decode_cursor(done, patterns) == {"p": 0, "n": {}, "o": {}}
        # A v1 cursor (no offsets) still resumes at its batch.
        legacy = json.dumps({"v": 1, "patterns": patterns, "p": 0, "n": {"h:1": 42}})
        assert _decode_cursor(legacy, patterns) == {"p": 0, "n": {"h:1": 42}, "o": {}}

    def test_unreadable_cursor_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="cursor"):
            _decode_cursor("not json", ["*"])


def test_secrets_are_encrypted_at_rest_and_masked_in_responses() -> None:
    cc = {
        "host": "h",
        "username": "alice",
        "password": "p",
        "uri": "redis://alice:p@h/0",
        "sentinel_password": "sp",
        "tls_client_private_key": "KEY",
        "tls_client_key_password": "kp",
        "tls_ca_pem": "CA",
    }
    secret_keys = {
        "password",
        "uri",
        "sentinel_password",
        "tls_client_private_key",
        "tls_client_key_password",
    }
    encrypted = encrypt_connection_config(cc)
    for key in secret_keys:
        assert str(encrypted[key]).startswith(ENC_PREFIX), key
    assert encrypted["username"] == "alice" and encrypted["tls_ca_pem"] == "CA"
    masked, has_credentials = mask_connection_config(cc)
    assert has_credentials
    assert {k for k, v in masked.items() if v == MASK} == secret_keys


async def test_internal_hosts_are_refused_without_the_operator_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "false")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "")
    get_settings.cache_clear()
    try:
        for cc in (
            {"host": "127.0.0.1"},
            {"host": "169.254.169.254", "port": 80},
            {"uri": "redis://10.0.0.5:6379/0"},
            {"mode": "sentinel", "sentinels": "192.168.1.2", "sentinel_master": "m"},
            {"mode": "cluster", "cluster_nodes": "172.16.0.9:7000"},
        ):
            health = await RedisConnector().validate_connection(_config(**cc))
            assert health.ok is False, cc
            assert "SSRF" in health.error, (cc, health.error)
            with pytest.raises(Exception, match="SSRF"):
                _ = [d async for d in RedisConnector().get_delta(_config(**cc), None)]
    finally:
        get_settings.cache_clear()


async def test_config_errors_are_reported_by_validate() -> None:
    health = await RedisConnector().validate_connection(_config(host="h", auth_type="acl"))
    assert health.ok is False
    assert "username" in health.error


def test_registered_in_the_catalogue() -> None:
    from app.ingestion.connector_registry import (
        get_connector,
        get_connector_metadata,
        load_all_connectors,
    )

    load_all_connectors()
    assert get_connector("redis") is RedisConnector
    assert any(m["source_type"] == "redis" for m in get_connector_metadata())
