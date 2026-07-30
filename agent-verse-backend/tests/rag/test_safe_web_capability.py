"""Security and production behavior for governed web retrieval."""

from __future__ import annotations

from datetime import UTC
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.governance.policies import Policy, PolicyEngine, PolicyResult
from app.rag.agentic.patterns import web_augmented
from app.rag.agentic.patterns.web_augmented import (
    SafeWebSearchCapability,
    WebEvidence,
    WebSearchRequest,
)
from app.tenancy.context import TenantContext

TENANT = TenantContext("tenant-web", "enterprise", "key-web")


def _build_capability(**kwargs: Any) -> SafeWebSearchCapability | None:
    return web_augmented.build_safe_web_search_capability(**kwargs)


class _Policy:
    def __init__(
        self,
        result: PolicyResult = PolicyResult.ALLOW,
        domains: tuple[str, ...] = (),
    ) -> None:
        self.result = result
        self.domains = domains

    def evaluate(self, tool_name: str, *, tenant_ctx: TenantContext) -> PolicyResult:
        assert tool_name == "web_search"
        assert tenant_ctx is TENANT
        return self.result

    def web_allowed_domains(self, tenant_ctx: TenantContext) -> tuple[str, ...]:
        assert tenant_ctx is TENANT
        return self.domains


def _request(*, domains: tuple[str, ...], max_bytes: int = 4096) -> WebSearchRequest:
    return WebSearchRequest(
        tenant_context=TENANT,
        query="current retention guidance",
        allowed_domains=domains,
        max_results=3,
        max_bytes=max_bytes,
        timeout_seconds=2.0,
    )


def _searx_response(urls: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "title": f"Result {index}",
                    "url": url,
                    "content": "Search snippet",
                    "engine": "test-engine",
                }
                for index, url in enumerate(urls, start=1)
            ]
        },
    )


async def test_governed_searxng_capability_returns_typed_bounded_provenance() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.host == "searx.test":
            return _searx_response(["https://8.8.8.8/guidance"])
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><body>Current public guidance with bounded content.</body></html>",
        )

    capability = _build_capability(
        searxng_url="https://searx.test",
        policy_services=(_Policy(domains=("8.8.8.8",)),),
        transport=httpx.MockTransport(handler),
        fetch_transport_factory=lambda: httpx.MockTransport(handler),
    )

    assert isinstance(capability, SafeWebSearchCapability)
    assert capability is not None
    evidence = await capability.search(_request(domains=("8.8.8.8",), max_bytes=32))

    assert len(evidence) == 1
    assert isinstance(evidence[0], WebEvidence)
    assert evidence[0].url == "https://8.8.8.8/guidance"
    assert evidence[0].domain == "8.8.8.8"
    assert evidence[0].fetched_at.tzinfo is UTC
    assert evidence[0].freshness_seconds >= 0
    assert len(evidence[0].content.encode()) <= 32
    assert requested_urls == [
        "https://searx.test/search?q=current+retention+guidance&format=json&pageno=1",
        "https://8.8.8.8/guidance",
    ]


@pytest.mark.parametrize(
    "blocked_url",
    [
        "http://127.0.0.1/private",
        "http://10.0.0.1/private",
        "http://192.168.1.1/private",
        "http://169.254.1.1/private",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/computeMetadata/v1",
    ],
)
async def test_governed_capability_rejects_ssrf_targets_even_when_allowlisted(
    blocked_url: str,
) -> None:
    requested_hosts: list[str] = []
    blocked_host = httpx.URL(blocked_url).host

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(str(request.url.host))
        if request.url.host == "searx.test":
            return _searx_response([blocked_url])
        raise AssertionError("blocked target must never be fetched")

    capability = _build_capability(
        searxng_url="https://searx.test",
        policy_services=(_Policy(domains=(str(blocked_host),)),),
        transport=httpx.MockTransport(handler),
    )
    assert capability is not None

    assert await capability.search(_request(domains=(str(blocked_host),))) == []
    assert requested_hosts == ["searx.test"]


async def test_governed_capability_rejects_redirect_to_private_target() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.host == "searx.test":
            return _searx_response(["https://8.8.8.8/start"])
        if request.url.host == "8.8.8.8":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        raise AssertionError("private redirect target must never be fetched")

    capability = _build_capability(
        searxng_url="https://searx.test",
        policy_services=(_Policy(domains=("8.8.8.8", "127.0.0.1")),),
        transport=httpx.MockTransport(handler),
        fetch_transport_factory=lambda: httpx.MockTransport(handler),
    )
    assert capability is not None

    assert await capability.search(
        _request(domains=("8.8.8.8", "127.0.0.1"))
    ) == []
    assert requested_urls == [
        "https://searx.test/search?q=current+retention+guidance&format=json&pageno=1",
        "https://8.8.8.8/start",
    ]


async def test_governed_capability_pins_validated_ip_without_dns_reresolution() -> None:
    fetch_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "searx.test":
            return _searx_response(["https://safe.example/guidance"])
        fetch_requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"Pinned public guidance",
        )

    capability = _build_capability(
        searxng_url="https://searx.test",
        policy_services=(_Policy(domains=("safe.example",)),),
        transport=httpx.MockTransport(handler),
        fetch_transport_factory=lambda: httpx.MockTransport(handler),
    )
    assert capability is not None

    with patch.object(
        web_augmented,
        "assert_public_url",
        side_effect=[["8.8.8.8"], ["127.0.0.1"]],
    ) as resolver:
        evidence = await capability.search(_request(domains=("safe.example",)))

    assert len(evidence) == 1
    assert resolver.call_count == 1
    assert fetch_requests[0].url.host == "8.8.8.8"
    assert fetch_requests[0].headers["host"] == "safe.example"
    assert fetch_requests[0].extensions["sni_hostname"] == "safe.example"
    assert evidence[0].url == "https://safe.example/guidance"


async def test_governed_capability_pins_each_redirect_resolved_destination() -> None:
    fetch_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "searx.test":
            return _searx_response(["https://first.example/start"])
        fetch_requests.append(request)
        if request.url.host == "8.8.8.8":
            return httpx.Response(
                302,
                headers={"location": "https://second.example/final"},
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"Redirected pinned guidance",
        )

    capability = _build_capability(
        searxng_url="https://searx.test",
        policy_services=(_Policy(domains=("first.example", "second.example")),),
        transport=httpx.MockTransport(handler),
        fetch_transport_factory=lambda: httpx.MockTransport(handler),
    )
    assert capability is not None

    with patch.object(
        web_augmented,
        "assert_public_url",
        side_effect=[["8.8.8.8"], ["8.8.4.4"]],
    ) as resolver:
        evidence = await capability.search(
            _request(domains=("first.example", "second.example"))
        )

    assert resolver.call_count == 2
    assert [request.url.host for request in fetch_requests] == ["8.8.8.8", "8.8.4.4"]
    assert [request.headers["host"] for request in fetch_requests] == [
        "first.example",
        "second.example",
    ]
    assert [request.extensions["sni_hostname"] for request in fetch_requests] == [
        "first.example",
        "second.example",
    ]
    assert evidence[0].url == "https://second.example/final"


async def test_same_ip_cross_host_redirect_uses_fresh_closed_transport_and_sni() -> None:
    class RecordingTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.requests: list[httpx.Request] = []
            self.closed = False

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.headers["host"] == "first.example":
                return httpx.Response(
                    302,
                    headers={"location": "https://second.example/final"},
                    request=request,
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                content=b"Fresh second-host handshake",
                request=request,
            )

        async def aclose(self) -> None:
            self.closed = True

    transports: list[RecordingTransport] = []

    def transport_factory() -> httpx.AsyncBaseTransport:
        transport = RecordingTransport()
        transports.append(transport)
        return transport

    capability = _build_capability(
        searxng_url="https://searx.test",
        policy_services=(_Policy(domains=("first.example", "second.example")),),
        transport=httpx.MockTransport(
            lambda request: _searx_response(["https://first.example/start"])
        ),
        fetch_transport_factory=transport_factory,
    )
    assert capability is not None

    with patch.object(
        web_augmented,
        "assert_public_url",
        side_effect=[["8.8.8.8"], ["8.8.8.8"]],
    ):
        evidence = await capability.search(
            _request(domains=("first.example", "second.example"))
        )

    assert len(transports) == 2
    assert transports[0] is not transports[1]
    assert all(transport.closed for transport in transports)
    requests = [transport.requests[0] for transport in transports]
    assert [request.url.host for request in requests] == ["8.8.8.8", "8.8.8.8"]
    assert [request.headers["host"] for request in requests] == [
        "first.example",
        "second.example",
    ]
    assert [request.extensions["sni_hostname"] for request in requests] == [
        "first.example",
        "second.example",
    ]
    assert evidence[0].url == "https://second.example/final"


@pytest.mark.parametrize("failure", ["outage", "timeout"])
async def test_governed_capability_raises_typed_backend_failure(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        raise httpx.ConnectError("offline", request=request)

    capability = _build_capability(
        searxng_url="https://searx.test",
        policy_services=(_Policy(),),
        transport=httpx.MockTransport(handler),
    )
    assert capability is not None

    with pytest.raises(web_augmented.WebSearchCapabilityError) as exc_info:
        await capability.search(_request(domains=()))
    assert exc_info.value.reason == f"backend_{failure}"


async def test_governed_capability_preserves_successful_empty_backend_result() -> None:
    capability = _build_capability(
        searxng_url="https://searx.test",
        policy_services=(_Policy(),),
        transport=httpx.MockTransport(lambda request: _searx_response([])),
    )
    assert capability is not None

    assert await capability.search(_request(domains=())) == []


@pytest.mark.parametrize("failure", ["non_2xx", "tls"])
async def test_governed_capability_records_rejection_and_continues_candidates(
    failure: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "searx.test":
            return _searx_response(
                ["https://8.8.8.8/failing", "https://8.8.4.4/success"]
            )
        if request.url.host == "8.8.8.8":
            if failure == "tls":
                raise httpx.ConnectError("TLS handshake failed", request=request)
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"Second candidate succeeds",
            request=request,
        )

    capability = _build_capability(
        searxng_url="https://searx.test",
        policy_services=(_Policy(domains=("8.8.8.8", "8.8.4.4")),),
        transport=httpx.MockTransport(handler),
        fetch_transport_factory=lambda: httpx.MockTransport(handler),
    )
    assert capability is not None
    request = _request(domains=("8.8.8.8", "8.8.4.4"))

    evidence = await capability.search(request)

    assert [item.url for item in evidence] == ["https://8.8.4.4/success"]
    assert request.report.candidate_count == 2
    assert len(request.report.rejections) == 1
    rejection = request.report.rejections[0]
    assert rejection.reason == "fetch_http_error"
    assert len(rejection.url_sha256) == 64
    assert "failing" not in str(rejection)


async def test_governed_capability_bounds_rejection_audit_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "searx.test":
            return _searx_response(
                [f"https://8.8.8.8/failure-{index}" for index in range(20)]
            )
        return httpx.Response(503, request=request)

    capability = _build_capability(
        searxng_url="https://searx.test",
        policy_services=(_Policy(domains=("8.8.8.8",)),),
        transport=httpx.MockTransport(handler),
        fetch_transport_factory=lambda: httpx.MockTransport(handler),
    )
    assert capability is not None
    request = WebSearchRequest(
        tenant_context=TENANT,
        query="current retention guidance",
        allowed_domains=("8.8.8.8",),
        max_results=20,
        max_bytes=4096,
        timeout_seconds=2.0,
    )

    assert await capability.search(request) == []
    assert request.report.candidate_count == 8
    assert len(request.report.rejections) == 8


async def test_governed_capability_enforces_policy_before_search() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _searx_response([])

    capability = _build_capability(
        searxng_url="https://searx.test",
        policy_services=(_Policy(result=PolicyResult.DENY),),
        transport=httpx.MockTransport(handler),
    )
    assert capability is not None

    with pytest.raises(PermissionError, match="web_policy_denied"):
        await capability.search(_request(domains=()))
    assert calls == 0


def test_safe_web_capability_is_absent_without_configured_backend() -> None:
    capability = _build_capability(
        searxng_url="",
        policy_services=(_Policy(),),
    )

    assert capability is None


def test_policy_engine_exposes_tenant_scoped_web_domain_allowlist() -> None:
    engine = PolicyEngine(
        [
            Policy(
                name="tenant-web-domains",
                tenant_id=TENANT.tenant_id,
                web_allowed_domains=["Example.COM", "docs.example.com"],
            ),
            Policy(
                name="other-tenant-domains",
                tenant_id="other-tenant",
                web_allowed_domains=["other.example"],
            ),
        ]
    )

    assert engine.web_allowed_domains(TENANT) == ("docs.example.com", "example.com")
