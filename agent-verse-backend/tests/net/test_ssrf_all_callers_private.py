"""ALLOW_PRIVATE_NETWORK_ACCESS applies to EVERY caller of the SSRF guard.

Before: only the ingestion / connector / model-endpoint paths passed
``allowed_networks``; workflow HTTP / OCR steps, webhooks, notifications, triggers,
the agent HTTP tool, browsers and the rest still refused 127.0.0.1, 10.x, 192.168.x
(owner decision 2026-10-06: allow all private addresses). Now the shared guard
applies the policy itself.

Always refused (unless deliberately opened with ALLOW_LINK_LOCAL_NETWORK_ACCESS):
cloud-metadata names / addresses, link-local, 0.0.0.0, multicast.
"""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.net import ssrf_guard
from app.net.ssrf_guard import SSRFError, assert_public_url, is_metadata_host

PRIVATE = [
    "http://127.0.0.1:8080/x",
    "http://localhost:8080/x",
    "http://10.1.2.3/",
    "http://172.23.76.120:27017/",  # owner MongoDB
    "http://192.168.63.104:30080/v1",  # owner vLLM / MinIO host
    "http://100.64.0.5/",
    "http://198.18.0.9/",
    "http://[fc00::5]/",
    "http://[::1]:9000/",
    "http://[::ffff:192.168.1.5]/",
]
NEVER = [
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.170.2/v2/credentials",
    "http://metadata.google.internal/",
    "http://100.100.100.200/",
    "http://0.0.0.0:80/",
    "http://[fe80::1]/",
    "http://[fd00:ec2::254]/",
    "http://224.0.0.1/",
]


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    monkeypatch.delenv("ALLOW_LINK_LOCAL_NETWORK_ACCESS", raising=False)


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "false")
    monkeypatch.delenv("ALLOW_LINK_LOCAL_NETWORK_ACCESS", raising=False)


@pytest.mark.parametrize("url", PRIVATE)
def test_a_plain_guard_call_allows_private_addresses(on, url):
    # No allowed_networks passed: the guard applies the policy itself.
    assert assert_public_url(url, context="any caller")


@pytest.mark.parametrize("url", PRIVATE)
def test_a_narrower_caller_list_does_not_re_close_private_access(on, url):
    only = ssrf_guard.parse_allowed_networks("203.0.113.0/24")
    assert assert_public_url(url, context="x", allowed_networks=only)


@pytest.mark.parametrize("url", NEVER)
def test_metadata_link_local_and_unspecified_stay_blocked(on, url):
    with pytest.raises(SSRFError):
        assert_public_url(url, context="any caller")


@pytest.mark.parametrize("url", PRIVATE)
def test_flag_off_restores_public_only(off, url):
    with pytest.raises(SSRFError):
        assert_public_url(url, context="any caller")


def test_public_addresses_are_unaffected(on):
    assert assert_public_url("http://93.184.216.34/", context="x")


def test_link_local_can_be_opened_deliberately_but_not_0_0_0_0(on, monkeypatch):
    monkeypatch.setenv("ALLOW_LINK_LOCAL_NETWORK_ACCESS", "true")
    assert assert_public_url("http://169.254.169.254/", context="metadata double")
    for url in ("http://0.0.0.0/", "http://224.0.0.1/"):
        with pytest.raises(SSRFError):
            assert_public_url(url, context="x")


def test_is_metadata_host(on):
    assert is_metadata_host("169.254.169.254")
    assert is_metadata_host("metadata.google.internal")
    assert is_metadata_host("100.100.100.200")
    assert is_metadata_host("0.0.0.0")
    assert is_metadata_host("")
    assert not is_metadata_host("192.168.63.104")
    assert not is_metadata_host("localhost")
    assert not is_metadata_host("example.com")


# ── other guard callers ──────────────────────────────────────────────────────


def test_workflow_ssrf_guard_follows_the_policy(on, monkeypatch):
    from app.workflow.security import SSRFBlockedError, SSRFGuard

    SSRFGuard().validate("http://192.168.63.104:9000/hook")
    with pytest.raises(SSRFBlockedError):
        SSRFGuard().validate("http://169.254.169.254/")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "false")
    with pytest.raises(SSRFBlockedError):
        SSRFGuard().validate("http://192.168.63.104:9000/hook")


def test_agent_http_tool_literal_blocklist_follows_the_policy(on, monkeypatch):
    from app.tools.http_tool import _is_blocked_literal

    for url in ("http://localhost:8000/", "http://127.0.0.1/", "http://10.0.0.5/",
                "http://svc.internal/", "http://printer.local/"):
        assert _is_blocked_literal(url) is False, url
    for url in ("http://169.254.169.254/", "http://metadata.google.internal/",
                "http://100.100.100.200/", "http://0.0.0.0/"):
        assert _is_blocked_literal(url) is True, url
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "false")
    assert _is_blocked_literal("http://localhost:8000/") is True
    assert _is_blocked_literal("http://10.0.0.5/") is True


# ── real connections ─────────────────────────────────────────────────────────


class _Hello(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"hello from a private host"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # keep test output clean
        pass


@pytest.fixture
def local_server():
    srv = HTTPServer(("127.0.0.1", 0), _Hello)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


def test_the_pinned_client_connects_to_a_private_host(on, local_server):
    async def go():
        async with ssrf_guard.public_async_client() as client:
            return await client.get(local_server)

    resp = asyncio.run(go())
    assert resp.status_code == 200 and resp.text == "hello from a private host"


def test_the_pinned_client_still_refuses_when_the_flag_is_off(off, local_server):
    async def go():
        async with ssrf_guard.public_async_client() as client:
            return await client.get(local_server)

    with pytest.raises(Exception) as exc:  # the transport re-checks at connect time
        asyncio.run(go())
    assert "ssrf" in str(exc.value).lower() or "blocked" in str(exc.value).lower()


def test_the_agent_http_tool_reaches_a_private_host(on, local_server):
    from app.tools.http_tool import HttpRequestTool

    out = asyncio.run(HttpRequestTool().execute(url=local_server))
    assert "error" not in out, out
    assert "hello from a private host" in str(out)
