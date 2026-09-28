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
    def test_allowed_domain_may_resolve_to_a_private_ip(self, monkeypatch) -> None:
        """An operator allowlist opens private ranges for its own domains."""
        import app.net.ssrf_guard as g

        monkeypatch.setattr(g, "_resolve_host", lambda h: ["10.0.0.7"])
        assert assert_public_url(
            "http://internal-jira.mycompany.com/",
            allowed_domains=["internal-jira.mycompany.com"],
        ) == ["10.0.0.7"]

    def test_subdomain_allowed_by_parent(self, monkeypatch) -> None:
        import app.net.ssrf_guard as g

        monkeypatch.setattr(g, "_resolve_host", lambda h: ["192.168.1.5"])
        assert_public_url(
            "https://api.partner.com/v1/",
            allowed_domains=["partner.com"],
        )

    def test_allowlisted_domain_resolving_to_metadata_is_still_blocked(
        self, monkeypatch
    ) -> None:
        """The allowlist used to skip ALL IP checks (``return []``)."""
        import app.net.ssrf_guard as g

        for ip in ("169.254.169.254", "0.0.0.0", "fd00:ec2::254", "::ffff:169.254.169.254"):
            monkeypatch.setattr(g, "_resolve_host", lambda h, ip=ip: [ip])
            with pytest.raises(SSRFError, match="never-reachable"):
                assert_public_url("http://wiki.corp.example/", allowed_domains=["corp.example"])

    def test_allowlisted_metadata_hostname_is_blocked(self) -> None:
        with pytest.raises(SSRFError, match="metadata"):
            assert_public_url(
                "http://metadata.google.internal/",
                allowed_domains=["metadata.google.internal"],
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
