"""Tests for SSRF egress guard."""
import pytest
from app.net.ssrf_guard import SSRFError, assert_public_url, is_public_url


class TestBlockedRanges:
    def test_blocks_localhost(self) -> None:
        with pytest.raises(SSRFError, match="blocked"):
            assert_public_url("http://127.0.0.1/api")

    def test_blocks_localhost_with_port(self) -> None:
        with pytest.raises(SSRFError):
            assert_public_url("http://127.0.0.1:8080/health")

    def test_blocks_private_10_range(self) -> None:
        with pytest.raises(SSRFError):
            assert_public_url("http://10.0.0.1/data")

    def test_blocks_private_172_range(self) -> None:
        with pytest.raises(SSRFError):
            assert_public_url("http://172.16.0.1/")

    def test_blocks_private_192_168(self) -> None:
        with pytest.raises(SSRFError):
            assert_public_url("http://192.168.1.1/admin")

    def test_blocks_link_local(self) -> None:
        with pytest.raises(SSRFError):
            assert_public_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_metadata_hostname(self) -> None:
        with pytest.raises(SSRFError, match="metadata"):
            assert_public_url("http://metadata.google.internal/computeMetadata/v1/")

    def test_blocks_ipv6_loopback(self) -> None:
        with pytest.raises(SSRFError):
            assert_public_url("http://[::1]/")

    def test_blocks_non_http_scheme(self) -> None:
        with pytest.raises(SSRFError, match="scheme"):
            assert_public_url("file:///etc/passwd")

    def test_blocks_ftp_scheme(self) -> None:
        with pytest.raises(SSRFError, match="scheme"):
            assert_public_url("ftp://files.example.com/")

    def test_blocks_empty_url(self) -> None:
        with pytest.raises(SSRFError):
            assert_public_url("")

    def test_blocks_missing_hostname(self) -> None:
        with pytest.raises(SSRFError):
            assert_public_url("http:///path")


class TestAllowedURLs:
    def test_allows_public_https_literal_ip(self) -> None:
        # Use a literal public IP to avoid DNS calls in unit tests
        assert is_public_url("https://8.8.8.8/") is True

    def test_allows_http_public_literal_ip(self) -> None:
        assert is_public_url("http://8.8.4.4/") is True

    def test_non_raising_returns_false_on_block(self) -> None:
        assert is_public_url("http://127.0.0.1/") is False

    def test_non_raising_returns_false_on_metadata(self) -> None:
        assert is_public_url("http://169.254.169.254/") is False


class TestAllowlist:
    def test_allowed_domain_bypasses_block(self) -> None:
        """Per-tenant allowlist can explicitly permit a domain."""
        # Even if DNS resolves to private IP, explicit allowlist wins
        assert_public_url(
            "http://internal-jira.mycompany.com/",
            allowed_domains=["internal-jira.mycompany.com"],
        )

    def test_subdomain_allowed_by_parent(self) -> None:
        assert_public_url(
            "https://api.partner.com/v1/",
            allowed_domains=["partner.com"],
        )

    def test_wrong_domain_not_allowed(self) -> None:
        with pytest.raises(SSRFError):
            assert_public_url(
                "http://127.0.0.1/",
                allowed_domains=["other-domain.com"],
            )


class TestContextInError:
    def test_context_appears_in_error_message(self) -> None:
        with pytest.raises(SSRFError, match="A2A callback"):
            assert_public_url("http://10.0.0.1/", context="A2A callback")
