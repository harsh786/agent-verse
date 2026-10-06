"""ALLOW_PRIVATE_NETWORK_ACCESS: private hosts / IPs for ingestion, connectors and
model endpoints (owner decision 2026-10-06, default on in every environment).

Private and internal addresses (10/8, 172.16/12, 192.168/16, 127/8, 100.64/10,
fc00::/7) become reachable; cloud metadata, link-local and 0.0.0.0 stay blocked.
"""

import pytest

from app.core.config import Settings
from app.ingestion import connector_egress as ce
from app.net.ssrf_guard import (
    ANY_NETWORK,
    SSRFError,
    assert_public_url,
    private_access_networks,
    private_network_access_enabled,
)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")


@pytest.fixture
def off(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "false")
    # Independent of settings other tests may have cached: the operator
    # allowlist escape hatch is off too, so only the flag decides.
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "ingestion_allow_internal_sources", False, raising=False)
    monkeypatch.setattr(s, "ingestion_internal_source_allowlist", "", raising=False)


def test_the_shipped_default_is_on():
    assert Settings.model_fields["allow_private_network_access"].default is True


def test_flag_switches_the_networks(on):
    assert private_network_access_enabled() is True
    assert private_access_networks() is ANY_NETWORK


def test_flag_off_keeps_the_fallback(off):
    assert private_network_access_enabled() is False
    assert private_access_networks() is None


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.63.104:30900/bucket",  # owner MinIO
        "http://172.23.76.120:27017/",  # owner MongoDB
        "http://10.1.2.3/",
        "http://127.0.0.1:8080/",
        "http://100.64.0.5/",
        "http://[fc00::5]/",
    ],
)
def test_private_addresses_pass_with_private_networks(url):
    assert assert_public_url(url, context="t", allowed_networks=ANY_NETWORK)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
        "http://0.0.0.0/",
        "http://[fe80::1]/",
        "http://[fd00:ec2::254]/",
    ],
)
def test_metadata_and_link_local_stay_blocked(url):
    with pytest.raises(SSRFError):
        assert_public_url(url, context="t", allowed_networks=ANY_NETWORK)


# ── ingestion + connectors (connector_egress) ───────────────────────────────


def test_ingestion_source_urls_and_hosts_on_private_ips(on):
    assert ce.assert_source_url("http://192.168.63.104:30900/", context="minio")
    assert ce.assert_source_host("172.23.76.120", 27017, context="mongodb")
    ce.assert_source_dsn("mongodb://172.23.76.120:27017/app", context="mongodb")
    ce.assert_source_dsn("postgresql://u:p@10.0.0.7:5432/db", context="postgres")


def test_ingestion_still_refuses_metadata_with_private_access_on(on):
    with pytest.raises(ce.ConnectorEgressBlockedError):
        ce.assert_source_url("http://169.254.169.254/", context="crawl")


def test_ingestion_refuses_private_ips_when_turned_off(off):
    with pytest.raises(ce.ConnectorEgressBlockedError):
        ce.assert_source_url("http://192.168.63.104:30900/", context="minio")


def test_self_resolving_drivers_run_with_private_access_on(on):
    ce.require_pinnable_driver("librdkafka", context="kafka")  # no raise


def test_self_resolving_drivers_refused_under_strict_pinning_when_off(off):
    with pytest.raises(ce.ConnectorEgressBlockedError):
        ce.require_pinnable_driver("librdkafka", context="kafka")


def test_mcp_connector_urls_on_private_hosts(on):
    from app.mcp.client import _assert_egress_allowed

    _assert_egress_allowed("http://192.168.63.104:8080/mcp", context="mcp")
    with pytest.raises(SSRFError):
        _assert_egress_allowed("http://169.254.169.254/", context="mcp")


# ── model endpoints ─────────────────────────────────────────────────────────


def test_tenant_llm_base_url_on_a_private_host(on):
    from app.providers.tenant_provider import TenantProviderError, _assert_tenant_base_url_allowed

    _assert_tenant_base_url_allowed("http://192.168.63.104:30080/v1")
    with pytest.raises(TenantProviderError):
        _assert_tenant_base_url_allowed("http://169.254.169.254/v1")


def test_tenant_llm_base_url_private_refused_when_off(off):
    from app.providers.tenant_provider import TenantProviderError, _assert_tenant_base_url_allowed

    with pytest.raises(TenantProviderError):
        _assert_tenant_base_url_allowed("http://192.168.63.104:30080/v1")


@pytest.mark.asyncio
async def test_hosted_reranker_on_a_private_ip(on):
    from app.rag_platform.hosted_reranker import HostedReranker

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"index": 0, "relevance_score": 0.9}]}

    class _Client:
        async def post(self, *a, **k):
            return _Resp()

    rr = HostedReranker(url="http://192.168.63.104:30083/v1/rerank", client=_Client())
    assert await rr.rerank("q", ["doc"]) == [(0, 0.9)]


@pytest.mark.asyncio
async def test_hosted_reranker_metadata_still_blocked(on):
    from app.rag_platform.hosted_reranker import HostedReranker, HostedRerankerError

    rr = HostedReranker(url="http://169.254.169.254/rerank", client=object())
    with pytest.raises(HostedRerankerError):
        await rr.rerank("q", ["doc"])
