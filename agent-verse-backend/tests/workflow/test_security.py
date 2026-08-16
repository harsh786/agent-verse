"""Tests for security utilities: SSRFGuard and SecretMasker."""
from __future__ import annotations

import pytest
from unittest.mock import patch

from app.workflow.security import SSRFGuard, SSRFBlockedError, SecretMasker


# ── SSRFGuard ─────────────────────────────────────────────────────────────────

@pytest.fixture
def guard():
    return SSRFGuard()


def test_ssrf_public_url_passes(guard):
    """Simulated: public IP 1.1.1.1 should pass."""
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("1.1.1.1", 0))]):
        guard.validate("https://cloudflare.com/api")  # should not raise


def test_ssrf_loopback_blocked(guard):
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 0))]):
        with pytest.raises(SSRFBlockedError):  # blocked (loopback resolves to private)
            guard.validate("http://127.0.0.1:8080/internal")


def test_ssrf_private_10_blocked(guard):
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("10.0.0.1", 0))]):
        with pytest.raises(SSRFBlockedError, match="private IP"):
            guard.validate("http://10.0.0.1/admin")


def test_ssrf_private_172_blocked(guard):
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("172.16.0.5", 0))]):
        with pytest.raises(SSRFBlockedError, match="private IP"):
            guard.validate("http://172.16.0.5/")


def test_ssrf_private_192_blocked(guard):
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("192.168.1.1", 0))]):
        with pytest.raises(SSRFBlockedError, match="private IP"):
            guard.validate("http://192.168.1.1/")


def test_ssrf_aws_metadata_hostname_blocked(guard):
    with pytest.raises(SSRFBlockedError):
        guard.validate("http://169.254.169.254/latest/meta-data/")


def test_ssrf_localhost_hostname_blocked(guard):
    with pytest.raises(SSRFBlockedError):
        guard.validate("http://localhost/secret")


def test_ssrf_ipv6_loopback_blocked(guard):
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("::1", 0, 0, 0))]):
        with pytest.raises(SSRFBlockedError, match="private IP"):
            guard.validate("http://[::1]/")


def test_ssrf_dns_failure_blocked(guard):
    import socket
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("NXDOMAIN")):
        with pytest.raises(SSRFBlockedError, match="could not resolve"):
            guard.validate("http://internal.corp.example.com/api")


def test_ssrf_empty_hostname_blocked(guard):
    with pytest.raises(SSRFBlockedError):
        guard.validate("http:///path")


def test_ssrf_link_local_blocked(guard):
    with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("169.254.1.1", 0))]):
        with pytest.raises(SSRFBlockedError):
            guard.validate("http://some-host.local/")


# ── SecretMasker ──────────────────────────────────────────────────────────────

@pytest.fixture
def masker():
    return SecretMasker()


def test_secret_masker_no_vaults(masker):
    d = {"url": "https://example.com", "token": "abc123"}
    result = masker.mask(d, set())
    assert result == d  # unchanged when no vault keys


def test_secret_masker_redacts_vault_placeholder(masker):
    d = {"Authorization": "Bearer [vault:MY_API_KEY]"}
    result = masker.mask(d, {"MY_API_KEY"})
    assert result["Authorization"] == "[REDACTED]"


def test_secret_masker_non_vault_not_redacted(masker):
    d = {"url": "https://example.com", "env_val": "plain_text"}
    result = masker.mask(d, {"SOME_SECRET"})
    assert result["url"] == "https://example.com"
    assert result["env_val"] == "plain_text"


def test_secret_masker_nested(masker):
    d = {"headers": {"Authorization": "[vault:TOKEN]", "Content-Type": "application/json"}}
    result = masker.mask(d, {"TOKEN"})
    assert result["headers"]["Authorization"] == "[REDACTED]"
    assert result["headers"]["Content-Type"] == "application/json"
