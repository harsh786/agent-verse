"""a02-F036-02 (d): the tenant-owned SMTP transport against an in-process server.

The host goes through the SSRF egress policy, only allowed ports are used, the
socket goes to the checked address, and failures name the stage, never the
credentials.
"""

from __future__ import annotations

import secrets
from typing import Any

import pytest

from app.tools import email_tool, tenant_smtp
from app.tools.tenant_smtp import SMTPTarget
from tests.tools._smtp_server import FakeSMTPServer


@pytest.fixture(autouse=True)
def _private_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    # The in-process server is on loopback: reachable only with private access on
    # (the default outside tests; tests/conftest.py turns it off).
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")


@pytest.fixture
def creds() -> tuple[str, str]:
    return "mailer", secrets.token_urlsafe(18)


def _target(port: int, *, username: str = "mailer", host: str = "127.0.0.1") -> SMTPTarget:
    return SMTPTarget(
        host=host, port=port, tls_mode="none", username=username, from_address="bot@tenant.test"
    )


@pytest.fixture
def allow_port(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _allow(port: int) -> None:
        monkeypatch.setattr(tenant_smtp, "allowed_ports", lambda: frozenset({port}))

    return _allow


async def test_probe_connects_and_authenticates(creds: tuple[str, str], allow_port: Any) -> None:
    user, secret = creds
    async with FakeSMTPServer(username=user, secret=secret) as server:
        allow_port(server.port)
        outcome = await tenant_smtp.probe(_target(server.port), secret)
    assert outcome.ok and outcome.stage == "done", outcome
    assert server.auth_attempts == 1 and server.messages == []


async def test_wrong_secret_is_an_auth_failure_without_echoing_it(
    creds: tuple[str, str], allow_port: Any
) -> None:
    user, secret = creds
    wrong = secrets.token_urlsafe(18)
    async with FakeSMTPServer(username=user, secret=secret) as server:
        allow_port(server.port)
        outcome = await tenant_smtp.probe(_target(server.port), wrong)
    assert not outcome.ok and outcome.stage == "auth" and outcome.code == 535
    assert wrong not in str(outcome.as_dict()) and secret not in str(outcome.as_dict())


async def test_tenant_send_delivers_from_the_configured_address(
    creds: tuple[str, str], allow_port: Any
) -> None:
    user, secret = creds
    async with FakeSMTPServer(username=user, secret=secret) as server:
        allow_port(server.port)
        result = await email_tool.email_send_tenant_smtp(
            _target(server.port),
            secret,
            ["a@partner.test", "b@partner.test"],
            "Quarterly report",
            "Body text",
            reply_to="owner@tenant.test",
            tenant_id="t-1",
        )
    assert result["success"] is True and result["relay"] == "tenant", result
    [msg] = server.messages
    assert msg.mail_from == "bot@tenant.test" and msg.authenticated_as == user
    assert msg.rcpt_to == ["a@partner.test", "b@partner.test"]
    assert "Subject: Quarterly report" in msg.data and "Reply-To: owner@tenant.test" in msg.data
    assert "X-AgentVerse-Tenant: t-1" in msg.data


async def test_tenant_send_refuses_a_foreign_from_address(allow_port: Any) -> None:
    result = await email_tool.email_send_tenant_smtp(
        _target(2525), None, "a@partner.test", "s", "b", from_addr="ceo@bank.test"
    )
    assert result["success"] is False and result["rejected"] is True
    assert "tenant SMTP sender" in result["error"]


async def test_tenant_send_failure_names_the_stage(creds: tuple[str, str], allow_port: Any) -> None:
    user, secret = creds
    async with FakeSMTPServer(username=user, secret=secret) as server:
        allow_port(server.port)
        result = await email_tool.email_send_tenant_smtp(
            _target(server.port), secrets.token_urlsafe(18), "a@partner.test", "s", "b"
        )
    assert result["success"] is False and result.get("rejected") is None
    assert result["stage"] == "auth" and "error id" in result["error"]
    assert server.messages == []


async def test_metadata_address_is_refused_by_the_egress_policy(allow_port: Any) -> None:
    allow_port(25)
    outcome = await tenant_smtp.probe(_target(25, host="169.254.169.254"), None)
    assert not outcome.ok and outcome.stage == "policy"


async def test_private_hosts_follow_the_private_network_switch(
    creds: tuple[str, str], allow_port: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, secret = creds
    async with FakeSMTPServer(username=user, secret=secret) as server:
        allow_port(server.port)
        monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "false")
        refused = await tenant_smtp.probe(_target(server.port), secret)
        monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
        allowed = await tenant_smtp.probe(_target(server.port), secret)
    assert not refused.ok and refused.stage == "policy"
    assert allowed.ok
    assert server.auth_attempts == 1  # the refused probe never connected


async def test_ports_outside_the_allowed_list_are_refused() -> None:
    outcome = await tenant_smtp.probe(_target(22), None)
    assert not outcome.ok and outcome.stage == "policy" and "port 22" in outcome.message


def test_plaintext_mode_is_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(tenant_smtp.SMTPConfigError):
        tenant_smtp.validate_target(_target(587))


def test_hosts_must_be_bare_names() -> None:
    for host in ("smtp.example.com:25", "smtp example.com", "SMTP.example.com", "http://x.io"):
        with pytest.raises(tenant_smtp.SMTPConfigError):
            tenant_smtp.validate_target(_target(587, host=host))
