"""Dispatch-level tests for remaining MCP servers.

Covers: amplitude, mixpanel, new_relic, prometheus, splunk, loggly,
        docker, zoom, openai, workday, rippling, deel, dropbox, onedrive,
        brave_search, tavily, serpapi, firecrawl, perplexity,
        x_twitter, youtube, instagram, tiktok, linkedin, linkedin_ads,
        google_analytics, google_ads, google_search_console,
        bamboohr, woocommerce, wordpress, webflow, front.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.raise_for_status = MagicMock()
    return m


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    """Return a mock AsyncClient context manager.
    
    All HTTP method mocks are explicitly set to AsyncMock so that
    awaiting them works correctly regardless of Python version.
    """
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ---------------------------------------------------------------------------
# Amplitude
# ---------------------------------------------------------------------------

_AMP = {"AMPLITUDE_API_KEY": "amp-key", "AMPLITUDE_SECRET_KEY": "amp-secret"}


@pytest.mark.asyncio
async def test_amplitude_get_active_users():
    from app.mcp.servers.amplitude_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"series": [[10, 20, 30]], "xValues": ["2024-01-01", "2024-01-02", "2024-01-03"]}}))
    with patch.dict("os.environ", _AMP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "amplitude_query_events",
            {"event": [{"event_type": "PageView"}], "start": "20240101", "end": "20240131"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_amplitude_get_cohort():
    from app.mcp.servers.amplitude_server import call_tool

    mc = mk_client(get=make_resp(data={"cohort": {"id": "cohort1", "name": "Power Users", "size": 150, "definition": {}}}))
    with patch.dict("os.environ", _AMP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("amplitude_get_cohort", {"cohort_id": "cohort1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_amplitude_export_events():
    from app.mcp.servers.amplitude_server import call_tool

    mc = mk_client(get=make_resp(data={"events": [{"event_type": "page_view", "user_id": "u1"}]}))
    with patch.dict("os.environ", _AMP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "amplitude_export_events",
            {"start": "20240101T00", "end": "20240101T23"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_amplitude_list_cohorts():
    from app.mcp.servers.amplitude_server import call_tool

    mc = mk_client(get=make_resp(data={"cohorts": [{"id": "c1", "name": "Power Users", "size": 100}]}))
    with patch.dict("os.environ", _AMP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("amplitude_list_cohorts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_amplitude_missing_env():
    from app.mcp.servers.amplitude_server import call_tool

    with patch.dict("os.environ", {"AMPLITUDE_API_KEY": ""}):
        os.environ.pop("AMPLITUDE_API_KEY", None)
        result = await call_tool("amplitude_query_events", {"event": [{"event_type": "PageView"}], "start": "20240101", "end": "20240131"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Mixpanel
# ---------------------------------------------------------------------------

_MIX = {
    "MIXPANEL_SERVICE_ACCOUNT_USERNAME": "mix-user",
    "MIXPANEL_SERVICE_ACCOUNT_SECRET": "mix-secret",
    "MIXPANEL_PROJECT_ID": "proj-123",
}


@pytest.mark.asyncio
async def test_mixpanel_query_events():
    from app.mcp.servers.mixpanel_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"series": {"$overall": [10, 20]}, "values": {}}}))
    with patch.dict("os.environ", _MIX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "mixpanel_query_events",
            {"event": ["page_view"], "from_date": "2024-01-01", "to_date": "2024-01-31"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_mixpanel_user_profile():
    from app.mcp.servers.mixpanel_server import call_tool

    mc = mk_client(get=make_resp(data={"results": [{"$distinct_id": "user1", "$properties": {"$email": "a@b.com"}}]}))
    with patch.dict("os.environ", _MIX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mixpanel_user_profile", {"distinct_id": "user1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mixpanel_query_segmentation():
    from app.mcp.servers.mixpanel_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"series": {}, "values": {}}}))
    with patch.dict("os.environ", _MIX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "mixpanel_query_segmentation",
            {"event": "page_view", "from_date": "2024-01-01", "to_date": "2024-01-31"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_mixpanel_missing_env():
    from app.mcp.servers.mixpanel_server import call_tool

    with patch.dict("os.environ", {"MIXPANEL_SERVICE_ACCOUNT_USERNAME": ""}):
        os.environ.pop("MIXPANEL_SERVICE_ACCOUNT_USERNAME", None)
        result = await call_tool("mixpanel_query_events", {"event": ["e"], "from_date": "2024-01-01", "to_date": "2024-01-31"})
    assert "error" in result


# ---------------------------------------------------------------------------
# New Relic
# ---------------------------------------------------------------------------

_NR = {"NEW_RELIC_API_KEY": "nr-key", "NEW_RELIC_ACCOUNT_ID": "123456"}


@pytest.mark.asyncio
async def test_newrelic_nrql_query():
    from app.mcp.servers.new_relic_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"actor": {"account": {"nrql": {"results": [{"count": 42}]}}}}}))
    with patch.dict("os.environ", _NR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("newrelic_nrql_query", {"nrql": "SELECT count(*) FROM Transaction"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_newrelic_list_alerts():
    from app.mcp.servers.new_relic_server import call_tool

    mc = mk_client(get=make_resp(data={"policies": [{"id": 1, "name": "High Error Rate", "incident_preference": "PER_POLICY"}]}))
    with patch.dict("os.environ", _NR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("newrelic_list_alerts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_newrelic_list_applications():
    from app.mcp.servers.new_relic_server import call_tool

    mc = mk_client(get=make_resp(data={"applications": [{"id": 1, "name": "my-app", "language": "python", "health_status": "green", "reporting": True, "application_summary": {}}]}))
    with patch.dict("os.environ", _NR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("newrelic_list_applications", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_newrelic_missing_env():
    from app.mcp.servers.new_relic_server import call_tool

    with patch.dict("os.environ", {"NEW_RELIC_API_KEY": ""}):
        os.environ.pop("NEW_RELIC_API_KEY", None)
        result = await call_tool("newrelic_nrql_query", {"nrql": "SELECT count(*) FROM T"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Prometheus
# ---------------------------------------------------------------------------

_PROM = {"PROMETHEUS_URL": "https://prom.example.com", "PROMETHEUS_TOKEN": "prom-tok"}


@pytest.mark.asyncio
async def test_prometheus_query():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client(get=make_resp(data={"status": "success", "data": {"resultType": "vector", "result": [{"metric": {"job": "api"}, "value": [1704067200, "0.05"]}]}}))
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("prometheus_query", {"query": "up"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_prometheus_query_range():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client(get=make_resp(data={"status": "success", "data": {"resultType": "matrix", "result": []}}))
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "prometheus_query_range",
            {"query": "rate(http_requests_total[5m])", "start": "2024-01-01T00:00:00Z", "end": "2024-01-01T01:00:00Z", "step": "60"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_prometheus_missing_env():
    from app.mcp.servers.prometheus_server import call_tool

    with patch.dict("os.environ", {"PROMETHEUS_URL": ""}):
        os.environ.pop("PROMETHEUS_URL", None)
        result = await call_tool("prometheus_query", {"query": "up"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docker_list_containers():
    from app.mcp.servers.docker_server import call_tool

    data = [{"Id": "abc123", "Names": ["/my-container"], "Image": "nginx:latest", "Status": "running", "State": "running", "Ports": []}]
    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = data
        result = await call_tool("docker_list_containers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docker_inspect_container():
    from app.mcp.servers.docker_server import call_tool

    data = {"Id": "abc123", "Name": "/my-container", "State": {"Running": True, "Status": "running"}, "Config": {"Image": "nginx"}, "NetworkSettings": {}, "Mounts": []}
    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = data
        result = await call_tool("docker_inspect_container", {"container_id": "abc123"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docker_container_logs():
    from app.mcp.servers.docker_server import call_tool

    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = "2024-01-01 INFO: Started\n2024-01-01 INFO: Running"
        result = await call_tool("docker_container_logs", {"container_id": "abc123"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docker_list_images():
    from app.mcp.servers.docker_server import call_tool

    data = [{"Id": "img1", "RepoTags": ["nginx:latest"], "Size": 141000000, "Created": 1704067200}]
    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = data
        result = await call_tool("docker_list_images", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docker_list_volumes():
    from app.mcp.servers.docker_server import call_tool

    data = {"Volumes": [{"Name": "my-vol", "Driver": "local", "Mountpoint": "/var/lib/docker/volumes/my-vol/_data"}], "Warnings": None}
    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = data
        result = await call_tool("docker_list_volumes", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docker_list_networks():
    from app.mcp.servers.docker_server import call_tool

    data = [{"Id": "net1", "Name": "bridge", "Driver": "bridge", "Scope": "local"}]
    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = data
        result = await call_tool("docker_list_networks", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docker_hub_search():
    from app.mcp.servers.docker_server import call_tool

    data = {"results": [{"name": "nginx", "description": "Official nginx image", "star_count": 18000, "is_official": True}]}
    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = data
        result = await call_tool("docker_hub_search", {"query": "nginx"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Zoom
# ---------------------------------------------------------------------------

_ZOOM = {"ZOOM_OAUTH_TOKEN": "zoom-tok"}


@pytest.mark.asyncio
async def test_zoom_list_meetings():
    from app.mcp.servers.zoom_server import call_tool

    mc = mk_client(get=make_resp(data={"meetings": [{"id": 123, "topic": "Standup", "start_time": "2024-01-15T09:00:00Z", "duration": 30, "status": "waiting", "join_url": "url"}], "total_records": 1}))
    with patch.dict("os.environ", _ZOOM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoom_list_meetings", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_zoom_create_meeting():
    from app.mcp.servers.zoom_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 456, "topic": "New Meeting", "start_time": "2024-01-20T10:00:00Z", "join_url": "url", "duration": 60}))
    with patch.dict("os.environ", _ZOOM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoom_create_meeting", {"topic": "New Meeting"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_zoom_get_meeting():
    from app.mcp.servers.zoom_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 123, "topic": "Standup", "join_url": "url", "duration": 30, "status": "waiting", "start_time": "2024-01-15T09:00:00Z"}))
    with patch.dict("os.environ", _ZOOM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoom_get_meeting", {"meeting_id": 123})
    assert "error" not in result


@pytest.mark.asyncio
async def test_zoom_list_recordings():
    from app.mcp.servers.zoom_server import call_tool

    mc = mk_client(get=make_resp(data={"meetings": [], "total_records": 0}))
    with patch.dict("os.environ", _ZOOM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoom_list_recordings", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_zoom_missing_env():
    from app.mcp.servers.zoom_server import call_tool

    with patch.dict("os.environ", {"ZOOM_OAUTH_TOKEN": "", "ZOOM_JWT_TOKEN": ""}):
        os.environ.pop("ZOOM_OAUTH_TOKEN", None)
        os.environ.pop("ZOOM_JWT_TOKEN", None)
        result = await call_tool("zoom_list_meetings", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

_OAI = {"OPENAI_API_KEY": "sk-test-oai"}


@pytest.mark.asyncio
async def test_openai_chat_completion():
    from app.mcp.servers.openai_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "chatcmpl-1", "choices": [{"message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}], "usage": {"total_tokens": 10}}))
    with patch.dict("os.environ", _OAI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "openai_chat_completion",
            {"messages": [{"role": "user", "content": "Hi"}]},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_openai_create_embedding():
    from app.mcp.servers.openai_server import call_tool

    mc = mk_client(post=make_resp(data={"data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}], "model": "text-embedding-3-small", "usage": {}}))
    with patch.dict("os.environ", _OAI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("openai_create_embedding", {"input": "Hello world"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_openai_list_models():
    from app.mcp.servers.openai_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "gpt-4o", "created": 1704067200, "object": "model", "owned_by": "openai"}]}))
    with patch.dict("os.environ", _OAI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("openai_list_models", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_openai_list_assistants():
    from app.mcp.servers.openai_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "asst_1", "name": "My Assistant", "model": "gpt-4o", "instructions": "You are helpful"}], "has_more": False}))
    with patch.dict("os.environ", _OAI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("openai_list_assistants", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_openai_missing_env():
    from app.mcp.servers.openai_server import call_tool

    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
        os.environ.pop("OPENAI_API_KEY", None)
        result = await call_tool("openai_chat_completion", {"messages": [{"role": "user", "content": "hi"}]})
    assert "error" in result


# ---------------------------------------------------------------------------
# Brave Search
# ---------------------------------------------------------------------------

_BRAVE = {"BRAVE_SEARCH_API_KEY": "brave-key"}


@pytest.mark.asyncio
async def test_brave_web_search():
    from app.mcp.servers.brave_search_server import call_tool

    mc = mk_client(get=make_resp(data={"web": {"results": [{"title": "Result 1", "url": "https://example.com", "description": "A result"}]}}))
    with patch.dict("os.environ", _BRAVE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("brave_web_search", {"query": "python testing"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_brave_news_search():
    from app.mcp.servers.brave_search_server import call_tool

    mc = mk_client(get=make_resp(data={"news": {"results": [{"title": "News 1", "url": "url", "description": "desc", "age": "1h"}]}}))
    with patch.dict("os.environ", _BRAVE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("brave_news_search", {"query": "AI news"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_brave_missing_env():
    from app.mcp.servers.brave_search_server import call_tool

    with patch.dict("os.environ", {"BRAVE_SEARCH_API_KEY": ""}):
        os.environ.pop("BRAVE_SEARCH_API_KEY", None)
        result = await call_tool("brave_web_search", {"query": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Tavily
# ---------------------------------------------------------------------------

_TAVILY = {"TAVILY_API_KEY": "tavily-key"}


@pytest.mark.asyncio
async def test_tavily_search():
    from app.mcp.servers.tavily_server import call_tool

    mc = mk_client(post=make_resp(data={"results": [{"title": "Result 1", "url": "url", "content": "Content here", "score": 0.95}], "query": "test", "answer": None}))
    with patch.dict("os.environ", _TAVILY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("tavily_search", {"query": "AI trends 2024"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_tavily_extract():
    from app.mcp.servers.tavily_server import call_tool

    mc = mk_client(post=make_resp(data={"results": [{"url": "url", "raw_content": "Content"}]}))
    with patch.dict("os.environ", _TAVILY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("tavily_extract", {"urls": ["https://example.com"]})
    assert "error" not in result


@pytest.mark.asyncio
async def test_tavily_qna_search():
    from app.mcp.servers.tavily_server import call_tool

    mc = mk_client(post=make_resp(data={"answer": "Python is a language", "results": []}))
    with patch.dict("os.environ", _TAVILY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("tavily_qna_search", {"query": "What is Python?"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_tavily_missing_env():
    from app.mcp.servers.tavily_server import call_tool

    with patch.dict("os.environ", {"TAVILY_API_KEY": ""}):
        os.environ.pop("TAVILY_API_KEY", None)
        result = await call_tool("tavily_search", {"query": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# SerpAPI
# ---------------------------------------------------------------------------

_SERP = {"SERPAPI_API_KEY": "serp-key"}


@pytest.mark.asyncio
async def test_serpapi_search():
    from app.mcp.servers.serpapi_server import call_tool

    mc = mk_client(get=make_resp(data={"organic_results": [{"title": "Python Tutorial", "link": "url", "snippet": "Learn Python"}], "search_metadata": {"status": "Success"}}))
    with patch.dict("os.environ", _SERP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("serpapi_google_search", {"query": "python tutorial", "engine": "google"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_serpapi_missing_env():
    from app.mcp.servers.serpapi_server import call_tool

    with patch.dict("os.environ", {"SERPAPI_API_KEY": ""}):
        os.environ.pop("SERPAPI_API_KEY", None)
        result = await call_tool("serpapi_google_search", {"query": "test", "engine": "google"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Firecrawl
# ---------------------------------------------------------------------------

_FC = {"FIRECRAWL_API_KEY": "fc-key"}


@pytest.mark.asyncio
async def test_firecrawl_scrape():
    from app.mcp.servers.firecrawl_server import call_tool

    mc = mk_client(post=make_resp(data={"success": True, "data": {"markdown": "# Hello\nContent here", "html": "<h1>Hello</h1>", "url": "https://example.com", "metadata": {}}}))
    with patch.dict("os.environ", _FC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("firecrawl_scrape", {"url": "https://example.com"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_firecrawl_crawl():
    from app.mcp.servers.firecrawl_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "crawl-job-1", "success": True}))
    with patch.dict("os.environ", _FC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("firecrawl_crawl", {"url": "https://example.com"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_firecrawl_search():
    from app.mcp.servers.firecrawl_server import call_tool

    mc = mk_client(post=make_resp(data={"success": True, "data": [{"url": "url", "markdown": "content"}]}))
    with patch.dict("os.environ", _FC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("firecrawl_search", {"query": "python testing"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_firecrawl_missing_env():
    from app.mcp.servers.firecrawl_server import call_tool

    with patch.dict("os.environ", {"FIRECRAWL_API_KEY": ""}):
        os.environ.pop("FIRECRAWL_API_KEY", None)
        result = await call_tool("firecrawl_scrape", {"url": "https://example.com"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Perplexity
# ---------------------------------------------------------------------------

_PERP = {"PERPLEXITY_API_KEY": "perp-key"}


@pytest.mark.asyncio
async def test_perplexity_search():
    from app.mcp.servers.perplexity_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "req1", "choices": [{"message": {"role": "assistant", "content": "Perplexity answer"}}], "citations": []}))
    with patch.dict("os.environ", _PERP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("perplexity_search", {"query": "What is the latest in AI?"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_perplexity_missing_env():
    from app.mcp.servers.perplexity_server import call_tool

    with patch.dict("os.environ", {"PERPLEXITY_API_KEY": ""}):
        os.environ.pop("PERPLEXITY_API_KEY", None)
        result = await call_tool("perplexity_search", {"query": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# X (Twitter)
# ---------------------------------------------------------------------------

_TW = {"TWITTER_BEARER_TOKEN": "tw-bearer"}


@pytest.mark.asyncio
async def test_twitter_search_tweets():
    from app.mcp.servers.x_twitter_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": "tweet1", "text": "Hello Twitter!", "author_id": "u1", "created_at": "2024-01-01T00:00:00Z"}], "meta": {"result_count": 1}}))
    with patch.dict("os.environ", _TW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("twitter_search_tweets", {"query": "#python"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_twitter_get_tweet():
    from app.mcp.servers.x_twitter_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"id": "tweet1", "text": "Hello!", "author_id": "u1", "created_at": "2024-01-01T00:00:00Z"}}))
    with patch.dict("os.environ", _TW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("twitter_get_tweet", {"tweet_id": "tweet1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_twitter_lookup_user():
    from app.mcp.servers.x_twitter_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"id": "u1", "name": "Alice", "username": "alice_dev", "public_metrics": {}, "description": "", "created_at": "2024-01-01T00:00:00Z"}}))
    with patch.dict("os.environ", _TW), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("twitter_lookup_user", {"username": "alice_dev"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_twitter_missing_env():
    from app.mcp.servers.x_twitter_server import call_tool

    with patch.dict("os.environ", {"TWITTER_BEARER_TOKEN": ""}):
        os.environ.pop("TWITTER_BEARER_TOKEN", None)
        result = await call_tool("twitter_search_tweets", {"query": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

_YT = {"YOUTUBE_API_KEY": "yt-key", "YOUTUBE_ACCESS_TOKEN": "yt-tok"}


@pytest.mark.asyncio
async def test_youtube_search():
    from app.mcp.servers.youtube_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [{"id": {"videoId": "v1"}, "snippet": {"title": "Python Tutorial", "description": "desc", "channelTitle": "CH", "publishedAt": "2024-01-01"}}], "pageInfo": {"totalResults": 1}}))
    with patch.dict("os.environ", _YT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("youtube_search", {"query": "python tutorial"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_youtube_get_video():
    from app.mcp.servers.youtube_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [{"id": "v1", "snippet": {"title": "Python Tutorial", "description": "desc", "channelTitle": "CH", "publishedAt": "2024-01-01", "tags": []}, "statistics": {"viewCount": "1000", "likeCount": "100"}, "contentDetails": {"duration": "PT10M"}}]}))
    with patch.dict("os.environ", _YT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("youtube_get_video", {"video_id": "v1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_youtube_get_channel():
    from app.mcp.servers.youtube_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [{"id": "ch1", "snippet": {"title": "My Channel", "description": "desc", "customUrl": "mychan", "publishedAt": "2020-01-01"}, "statistics": {"subscriberCount": "5000", "videoCount": "100", "viewCount": "500000"}}]}))
    with patch.dict("os.environ", _YT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("youtube_get_channel", {"channel_id": "ch1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_youtube_missing_env():
    from app.mcp.servers.youtube_server import call_tool

    with patch.dict("os.environ", {"YOUTUBE_API_KEY": ""}):
        os.environ.pop("YOUTUBE_API_KEY", None)
        result = await call_tool("youtube_search", {"query": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Dropbox
# ---------------------------------------------------------------------------

_DBX = {"DROPBOX_ACCESS_TOKEN": "dbx-tok"}


@pytest.mark.asyncio
async def test_dropbox_list_folder():
    from app.mcp.servers.dropbox_server import call_tool

    mc = mk_client(post=make_resp(data={"entries": [{"".join([".", "tag"]): "folder", "name": "Documents", "path_lower": "/documents", "id": "id:abc"}], "has_more": False}))
    with patch.dict("os.environ", _DBX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("dropbox_list_folder", {"path": ""})
    assert "error" not in result


@pytest.mark.asyncio
async def test_dropbox_get_metadata():
    from app.mcp.servers.dropbox_server import call_tool

    mc = mk_client(post=make_resp(data={"".join([".", "tag"]): "file", "name": "report.pdf", "path_lower": "/report.pdf", "size": 1024, "client_modified": "2024-01-01T00:00:00Z", "id": "id:abc"}))
    with patch.dict("os.environ", _DBX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("dropbox_get_metadata", {"path": "/report.pdf"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_dropbox_search():
    from app.mcp.servers.dropbox_server import call_tool

    mc = mk_client(post=make_resp(data={"matches": [{"metadata": {"metadata": {"".join([".", "tag"]): "file", "name": "report.pdf", "path_lower": "/report.pdf", "id": "id:abc"}}}], "has_more": False}))
    with patch.dict("os.environ", _DBX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("dropbox_search", {"query": "report"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_dropbox_create_folder():
    from app.mcp.servers.dropbox_server import call_tool

    mc = mk_client(post=make_resp(data={"metadata": {"".join([".", "tag"]): "folder", "name": "New Folder", "path_lower": "/new-folder", "id": "id:xyz"}}))
    with patch.dict("os.environ", _DBX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("dropbox_create_folder", {"path": "/New Folder"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_dropbox_delete():
    from app.mcp.servers.dropbox_server import call_tool

    mc = mk_client(post=make_resp(data={"metadata": {"".join([".", "tag"]): "file", "name": "old.pdf", "path_lower": "/old.pdf"}}))
    with patch.dict("os.environ", _DBX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("dropbox_delete", {"path": "/old.pdf"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_dropbox_missing_env():
    from app.mcp.servers.dropbox_server import call_tool

    with patch.dict("os.environ", {"DROPBOX_ACCESS_TOKEN": ""}):
        os.environ.pop("DROPBOX_ACCESS_TOKEN", None)
        result = await call_tool("dropbox_list_folder", {"path": ""})
    assert "error" in result


# ---------------------------------------------------------------------------
# OneDrive
# ---------------------------------------------------------------------------

_OD = {"ONEDRIVE_ACCESS_TOKEN": "od-tok"}


@pytest.mark.asyncio
async def test_onedrive_list_items():
    from app.mcp.servers.microsoft_onedrive_server import call_tool

    mc = mk_client(get=make_resp(data={"value": [{"id": "item1", "name": "Documents", "folder": {}, "size": 0, "lastModifiedDateTime": "2024-01-01", "webUrl": "url"}]}))
    with patch.dict("os.environ", _OD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onedrive_list_root", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_onedrive_get_item():
    from app.mcp.servers.microsoft_onedrive_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "item1", "name": "report.pdf", "size": 1024, "lastModifiedDateTime": "2024-01-01", "webUrl": "url", "file": {"mimeType": "application/pdf"}}))
    with patch.dict("os.environ", _OD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onedrive_get_item", {"item_id": "item1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_onedrive_create_folder():
    from app.mcp.servers.microsoft_onedrive_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "folder1", "name": "New Folder", "folder": {}, "webUrl": "url"}))
    with patch.dict("os.environ", _OD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onedrive_create_folder", {"name": "New Folder"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_onedrive_missing_env():
    from app.mcp.servers.microsoft_onedrive_server import call_tool

    with patch.dict("os.environ", {"ONEDRIVE_ACCESS_TOKEN": ""}):
        os.environ.pop("ONEDRIVE_ACCESS_TOKEN", None)
        result = await call_tool("onedrive_list_root", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Google Analytics
# ---------------------------------------------------------------------------

_GA = {"GOOGLE_ACCESS_TOKEN": "ga-tok"}


@pytest.mark.asyncio
async def test_ga4_run_report():
    from app.mcp.servers.google_analytics_server import call_tool

    mc = mk_client(post=make_resp(data={"rows": [{"dimensionValues": [{"value": "2024-01-01"}], "metricValues": [{"value": "1000"}]}], "rowCount": 1, "dimensionHeaders": [{"name": "date"}], "metricHeaders": [{"name": "sessions"}]}))
    with patch.dict("os.environ", _GA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "ga4_run_report",
            {
                "property_id": "12345",
                "dimensions": ["date"],
                "metrics": ["sessions"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_ga4_missing_env():
    from app.mcp.servers.google_analytics_server import call_tool

    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": ""}):
        os.environ.pop("GOOGLE_ACCESS_TOKEN", None)
        result = await call_tool("ga4_run_report", {"property_id": "12345", "dimensions": ["date"], "metrics": ["sessions"], "start_date": "2024-01-01", "end_date": "2024-01-31"})
    assert "error" in result


# ---------------------------------------------------------------------------
# BambooHR
# ---------------------------------------------------------------------------

_BAMBOO = {"BAMBOOHR_API_KEY": "bamboo-key", "BAMBOOHR_SUBDOMAIN": "myco"}


@pytest.mark.asyncio
async def test_bamboohr_list_employees():
    from app.mcp.servers.bamboohr_server import call_tool

    mc = mk_client(get=make_resp(data={"employees": [{"id": "1", "firstName": "Alice", "lastName": "Smith", "jobTitle": "Engineer", "department": "Engineering", "workEmail": "a@b.com", "employmentHistoryStatus": "Active"}]}))
    with patch.dict("os.environ", _BAMBOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bamboo_list_employees", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_bamboohr_get_employee():
    from app.mcp.servers.bamboohr_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "1", "firstName": "Alice", "lastName": "Smith", "jobTitle": "Engineer", "department": "Engineering", "workEmail": "a@b.com", "hireDate": "2022-01-01", "location": "Remote"}))
    with patch.dict("os.environ", _BAMBOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bamboo_get_employee", {"employee_id": "1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_bamboohr_list_time_off():
    from app.mcp.servers.bamboohr_server import call_tool

    mc = mk_client(get=make_resp(data={"requests": [{"id": "r1", "status": {"lastChanged": "2024-01-01", "status": "approved"}, "start": "2024-02-01", "end": "2024-02-05", "type": {"id": "1", "name": "Vacation"}, "amount": {"unit": "days", "amount": "5"}}]}))
    with patch.dict("os.environ", _BAMBOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bamboo_get_time_off", {"start": "2024-01-01", "end": "2024-12-31"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_bamboohr_missing_env():
    from app.mcp.servers.bamboohr_server import call_tool

    with patch.dict("os.environ", {"BAMBOOHR_API_KEY": ""}):
        os.environ.pop("BAMBOOHR_API_KEY", None)
        result = await call_tool("bamboo_list_employees", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Deel
# ---------------------------------------------------------------------------

_DEEL = {"DEEL_API_KEY": "deel-key"}


@pytest.mark.asyncio
async def test_deel_list_contracts():
    from app.mcp.servers.deel_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"list": [{"id": "c1", "title": "Dev Contract", "status": "active", "type": "employment", "contractor": {"name": "Alice"}, "start_date": "2024-01-01"}]}}))
    with patch.dict("os.environ", _DEEL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("deel_list_contracts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_deel_missing_env():
    from app.mcp.servers.deel_server import call_tool

    with patch.dict("os.environ", {"DEEL_API_KEY": ""}):
        os.environ.pop("DEEL_API_KEY", None)
        result = await call_tool("deel_list_contracts", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Front
# ---------------------------------------------------------------------------

_FRONT = {"FRONT_API_TOKEN": "front-key"}


@pytest.mark.asyncio
async def test_front_list_conversations():
    from app.mcp.servers.front_server import call_tool

    mc = mk_client(get=make_resp(data={"_results": [{"id": "cnv_1", "subject": "Re: Help", "status": "open", "assignee": None, "tags": [], "created_at": 1704067200}], "_pagination": {}}))
    with patch.dict("os.environ", _FRONT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("front_list_conversations", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_front_send_message():
    from app.mcp.servers.front_server import call_tool

    mc = mk_client(post=make_resp(data={"status": "accepted"}))
    with patch.dict("os.environ", _FRONT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "front_send_reply",
            {"conversation_id": "cnv_1", "body": "Thanks!", "author_id": "a1"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_front_missing_env():
    from app.mcp.servers.front_server import call_tool

    with patch.dict("os.environ", {"FRONT_API_KEY": ""}):
        os.environ.pop("FRONT_API_KEY", None)
        result = await call_tool("front_list_conversations", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# DigitalOcean
# ---------------------------------------------------------------------------

_DO = {"DIGITALOCEAN_TOKEN": "do-token"}


@pytest.mark.asyncio
async def test_digitalocean_list_droplets():
    from app.mcp.servers.digitalocean_server import call_tool

    data = {"droplets": [{"id": 1, "name": "my-droplet", "status": "active", "region": {"slug": "nyc1"}, "size_slug": "s-1vcpu-1gb", "image": {"distribution": "Ubuntu", "name": "20.04 LTS"}, "networks": {"v4": [{"ip_address": "1.2.3.4", "type": "public"}]}}], "meta": {"total": 1}}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _DO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("digitalocean_list_droplets", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_digitalocean_list_apps():
    from app.mcp.servers.digitalocean_server import call_tool

    data = {"apps": [{"id": "app-1", "spec": {"name": "my-app"}, "phase": "RUNNING", "default_ingress": "https://myapp.ondigitalocean.app"}], "meta": {"total": 1}}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _DO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("digitalocean_list_apps", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_digitalocean_unknown_tool():
    from app.mcp.servers.digitalocean_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _DO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("digitalocean_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Google Ads
# ---------------------------------------------------------------------------

_GADS = {"GOOGLE_ACCESS_TOKEN": "gads-tok", "GOOGLE_ADS_DEVELOPER_TOKEN": "dev-tok"}


@pytest.mark.asyncio
async def test_gads_list_campaigns():
    from app.mcp.servers.google_ads_server import call_tool

    data = {"results": [{"campaign": {"id": "1", "name": "Campaign 1", "status": "ENABLED", "biddingStrategyType": "TARGET_CPA"}}]}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GADS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gads_list_campaigns", {"customer_id": "cust1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gads_missing_env():
    from app.mcp.servers.google_ads_server import call_tool

    with patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "", "GOOGLE_ADS_DEVELOPER_TOKEN": ""}):
        os.environ.pop("GOOGLE_ACCESS_TOKEN", None)
        result = await call_tool("gads_list_campaigns", {"customer_id": "cust1"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Google Search Console
# ---------------------------------------------------------------------------

_GSC = {"GOOGLE_ACCESS_TOKEN": "gsc-tok"}


@pytest.mark.asyncio
async def test_gsc_list_sites():
    from app.mcp.servers.google_search_console_server import call_tool

    data = {"siteEntry": [{"siteUrl": "https://example.com/", "permissionLevel": "siteOwner"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GSC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gsc_list_sites", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gsc_query_search_analytics():
    from app.mcp.servers.google_search_console_server import call_tool

    data = {"rows": [{"keys": ["python"], "clicks": 100, "impressions": 1000, "ctr": 0.1, "position": 3.5}], "responseAggregationType": "byPage"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GSC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "gsc_query_search_analytics",
            {"site_url": "https://example.com/", "start_date": "2024-01-01", "end_date": "2024-01-31"},
        )
    assert "error" not in result


# ---------------------------------------------------------------------------
# Gorgias
# ---------------------------------------------------------------------------

_GOR = {"GORGIAS_DOMAIN": "myco", "GORGIAS_EMAIL": "a@b.com", "GORGIAS_API_KEY": "gor-key"}


@pytest.mark.asyncio
async def test_gorgias_list_tickets():
    from app.mcp.servers.gorgias_server import call_tool

    data = {"data": [{"id": 1, "subject": "Help!", "status": "open", "channel": "email", "created_datetime": "2024-01-01", "customer": {"name": "Alice"}}], "meta": {"next_cursor": None}}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GOR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gorgias_list_tickets", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gorgias_create_ticket():
    from app.mcp.servers.gorgias_server import call_tool

    data = {"id": 2, "subject": "New Issue", "status": "open", "channel": "email"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GOR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "gorgias_create_ticket",
            {"subject": "New Issue", "channel": "email", "from_agent": False, "customer_email": "user@example.com", "body_text": "I need help"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_gorgias_missing_env():
    from app.mcp.servers.gorgias_server import call_tool

    with patch.dict("os.environ", {"GORGIAS_API_KEY": ""}):
        os.environ.pop("GORGIAS_API_KEY", None)
        result = await call_tool("gorgias_list_tickets", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Heroku
# ---------------------------------------------------------------------------

_HEROKU = {"HEROKU_API_KEY": "heroku-tok"}


@pytest.mark.asyncio
async def test_heroku_list_apps():
    from app.mcp.servers.heroku_server import call_tool

    data = [{"id": "app1", "name": "my-app", "web_url": "https://my-app.herokuapp.com", "region": {"name": "us"}, "stack": {"name": "heroku-22"}}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _HEROKU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("heroku_list_apps", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_heroku_get_app():
    from app.mcp.servers.heroku_server import call_tool

    data = {"id": "app1", "name": "my-app", "web_url": "https://my-app.herokuapp.com", "region": {"name": "us"}, "stack": {"name": "heroku-22"}, "slug_size": 10000, "repo_size": 5000}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _HEROKU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("heroku_get_app", {"app": "my-app"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_heroku_list_dynos():
    from app.mcp.servers.heroku_server import call_tool

    data = [{"id": "dyno1", "name": "web.1", "type": "web", "state": "up", "size": "standard-1X"}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _HEROKU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("heroku_list_dynos", {"app": "my-app"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_heroku_unknown_tool():
    from app.mcp.servers.heroku_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _HEROKU), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("heroku_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------

_IG = {"INSTAGRAM_ACCESS_TOKEN": "ig-tok", "INSTAGRAM_BUSINESS_ACCOUNT_ID": "ig-acct"}


@pytest.mark.asyncio
async def test_instagram_get_account():
    from app.mcp.servers.instagram_server import call_tool

    data = {"id": "ig-acct", "name": "My Brand", "biography": "Brand page", "followers_count": 5000, "media_count": 100, "website": "https://brand.com"}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _IG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("instagram_get_account", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_instagram_list_media():
    from app.mcp.servers.instagram_server import call_tool

    data = {"data": [{"id": "media1", "media_type": "IMAGE", "timestamp": "2024-01-01T00:00:00", "like_count": 50, "permalink": "url"}], "paging": {}}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _IG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("instagram_list_media", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_instagram_missing_env():
    from app.mcp.servers.instagram_server import call_tool

    with patch.dict("os.environ", {"INSTAGRAM_ACCESS_TOKEN": ""}):
        os.environ.pop("INSTAGRAM_ACCESS_TOKEN", None)
        result = await call_tool("instagram_get_account", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------------

_LI = {"LINKEDIN_ACCESS_TOKEN": "li-tok"}


@pytest.mark.asyncio
async def test_linkedin_get_profile():
    from app.mcp.servers.linkedin_server import call_tool

    data = {"id": "p1", "localizedFirstName": "Alice", "localizedLastName": "Smith"}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _LI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linkedin_get_profile", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_linkedin_missing_env():
    from app.mcp.servers.linkedin_server import call_tool

    with patch.dict("os.environ", {"LINKEDIN_ACCESS_TOKEN": ""}):
        os.environ.pop("LINKEDIN_ACCESS_TOKEN", None)
        result = await call_tool("linkedin_get_profile", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_linkedin_ads_list_accounts():
    from app.mcp.servers.linkedin_ads_server import call_tool

    data = {"elements": [{"id": 1, "name": "My Ad Account", "status": "ACTIVE", "type": "BUSINESS"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _LI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linkedin_ads_list_accounts", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Loggly
# ---------------------------------------------------------------------------

_LOGGLY = {"LOGGLY_ACCOUNT": "myco", "LOGGLY_API_TOKEN": "loggly-tok"}


@pytest.mark.asyncio
async def test_loggly_search():
    from app.mcp.servers.loggly_server import call_tool

    data = {"total_events": 5, "events": [{"event": {"json": {"message": "Error occurred"}}, "timestamp": 1704067200000}], "rsid": {"status": "done"}}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _LOGGLY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loggly_search", {"q": "error", "from": "-1d"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_loggly_missing_env():
    from app.mcp.servers.loggly_server import call_tool

    with patch.dict("os.environ", {"LOGGLY_API_TOKEN": ""}):
        os.environ.pop("LOGGLY_API_TOKEN", None)
        result = await call_tool("loggly_search", {"q": "error"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Netlify
# ---------------------------------------------------------------------------

_NETLIFY = {"NETLIFY_ACCESS_TOKEN": "netlify-tok"}


@pytest.mark.asyncio
async def test_netlify_list_sites():
    from app.mcp.servers.netlify_server import call_tool

    data = [{"id": "site1", "name": "my-site", "url": "https://mysite.netlify.app", "deploy_url": "url", "published_deploy": {"published_at": "2024-01-01"}, "custom_domain": None}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _NETLIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netlify_list_sites", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_netlify_list_deploys():
    from app.mcp.servers.netlify_server import call_tool

    data = [{"id": "dep1", "site_id": "site1", "state": "ready", "created_at": "2024-01-01", "deploy_url": "url", "branch": "main", "commit_ref": "abc"}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _NETLIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netlify_list_deploys", {"site_id": "site1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_netlify_unknown_tool():
    from app.mcp.servers.netlify_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _NETLIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netlify_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Postman
# ---------------------------------------------------------------------------

_POSTMAN = {"POSTMAN_API_KEY": "postman-key"}


@pytest.mark.asyncio
async def test_postman_list_collections():
    from app.mcp.servers.postman_server import call_tool

    data = {"collections": [{"id": "col1", "name": "My API", "uid": "uid1"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _POSTMAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postman_list_collections", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_postman_list_environments():
    from app.mcp.servers.postman_server import call_tool

    data = {"environments": [{"id": "env1", "name": "Staging", "uid": "uid2"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _POSTMAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postman_list_environments", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_postman_missing_env():
    from app.mcp.servers.postman_server import call_tool

    with patch.dict("os.environ", {"POSTMAN_API_KEY": ""}):
        os.environ.pop("POSTMAN_API_KEY", None)
        result = await call_tool("postman_list_collections", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Razorpay
# ---------------------------------------------------------------------------

_RAZORPAY = {"RAZORPAY_KEY_ID": "rzp_key", "RAZORPAY_KEY_SECRET": "rzp_secret"}


@pytest.mark.asyncio
async def test_razorpay_create_order():
    from app.mcp.servers.razorpay_server import call_tool

    data = {"id": "order_1", "entity": "order", "amount": 50000, "currency": "INR", "status": "created"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _RAZORPAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("razorpay_create_order", {"amount": 50000, "currency": "INR"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_razorpay_list_payments():
    from app.mcp.servers.razorpay_server import call_tool

    data = {"entity": "collection", "count": 1, "items": [{"id": "pay_1", "entity": "payment", "amount": 50000, "status": "captured"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _RAZORPAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("razorpay_list_payments", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_razorpay_missing_env():
    from app.mcp.servers.razorpay_server import call_tool

    with patch.dict("os.environ", {"RAZORPAY_KEY_ID": ""}):
        os.environ.pop("RAZORPAY_KEY_ID", None)
        result = await call_tool("razorpay_create_order", {"amount": 100, "currency": "INR"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Rippling
# ---------------------------------------------------------------------------

_RIPPLING = {"RIPPLING_API_KEY": "rippling-key"}


@pytest.mark.asyncio
async def test_rippling_list_employees():
    from app.mcp.servers.rippling_server import call_tool

    data = [{"id": "e1", "firstName": "Alice", "lastName": "Smith", "workEmail": "a@b.com", "department": {"name": "Engineering"}, "jobTitle": "Engineer"}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _RIPPLING), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("rippling_list_employees", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_rippling_missing_env():
    from app.mcp.servers.rippling_server import call_tool

    with patch.dict("os.environ", {"RIPPLING_API_KEY": ""}):
        os.environ.pop("RIPPLING_API_KEY", None)
        result = await call_tool("rippling_list_employees", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# SmartSuite
# ---------------------------------------------------------------------------

_SS = {"SMARTSUITE_API_KEY": "ss-key", "SMARTSUITE_ACCOUNT_ID": "acct1"}


@pytest.mark.asyncio
async def test_smartsuite_list_solutions():
    from app.mcp.servers.smartsuite_server import call_tool

    data = [{"id": "sol1", "name": "My Solution", "slug": "my-solution"}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsuite_list_solutions", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_smartsuite_missing_env():
    from app.mcp.servers.smartsuite_server import call_tool

    with patch.dict("os.environ", {"SMARTSUITE_API_KEY": ""}):
        os.environ.pop("SMARTSUITE_API_KEY", None)
        result = await call_tool("smartsuite_list_solutions", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Splunk
# ---------------------------------------------------------------------------

_SPLUNK = {"SPLUNK_URL": "https://splunk.example.com:8089", "SPLUNK_TOKEN": "splunk-tok"}


@pytest.mark.asyncio
async def test_splunk_search():
    from app.mcp.servers.splunk_server import call_tool

    data = {"results": [{"_raw": "2024-01-01 INFO App started", "_time": "2024-01-01T00:00:00"}], "preview": False}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _SPLUNK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "splunk_search",
            {"search": "search index=main error", "earliest_time": "-1h", "latest_time": "now"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_splunk_missing_env():
    from app.mcp.servers.splunk_server import call_tool

    with patch.dict("os.environ", {"SPLUNK_URL": "", "SPLUNK_TOKEN": ""}):
        os.environ.pop("SPLUNK_URL", None)
        result = await call_tool("splunk_search", {"search": "search index=main"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Square
# ---------------------------------------------------------------------------

_SQUARE = {"SQUARE_ACCESS_TOKEN": "sq-tok"}


@pytest.mark.asyncio
async def test_square_list_customers():
    from app.mcp.servers.square_server import call_tool

    data = {"customers": [{"id": "cust1", "given_name": "Alice", "family_name": "Smith", "email_address": "a@b.com"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _SQUARE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("square_list_customers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_square_create_customer():
    from app.mcp.servers.square_server import call_tool

    data = {"customer": {"id": "cust2", "given_name": "Bob", "email_address": "b@c.com"}}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _SQUARE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "square_create_customer",
            {"given_name": "Bob", "email_address": "b@c.com"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_square_missing_env():
    from app.mcp.servers.square_server import call_tool

    with patch.dict("os.environ", {"SQUARE_ACCESS_TOKEN": ""}):
        os.environ.pop("SQUARE_ACCESS_TOKEN", None)
        result = await call_tool("square_list_customers", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

_TG = {"TELEGRAM_BOT_TOKEN": "123456:ABC-token"}


@pytest.mark.asyncio
async def test_telegram_send_message():
    from app.mcp.servers.telegram_server import call_tool

    data = {"ok": True, "result": {"message_id": 1, "chat": {"id": 123, "type": "private"}, "text": "Hello"}}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("telegram_send_message", {"chat_id": 123, "text": "Hello"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_telegram_get_updates():
    from app.mcp.servers.telegram_server import call_tool

    data = {"ok": True, "result": [{"update_id": 1, "message": {"message_id": 1, "chat": {"id": 123}, "text": "hi"}}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("telegram_get_updates", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_telegram_missing_env():
    from app.mcp.servers.telegram_server import call_tool

    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": ""}):
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        result = await call_tool("telegram_send_message", {"chat_id": 1, "text": "hi"})
    assert "error" in result


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

_TK = {"TIKTOK_ACCESS_TOKEN": "tiktok-tok"}


@pytest.mark.asyncio
async def test_tiktok_get_user_info():
    from app.mcp.servers.tiktok_server import call_tool

    data = {"data": {"user": {"open_id": "u1", "union_id": "uid1", "avatar_url": "url", "display_name": "MyBrand", "bio_description": "desc", "follower_count": 1000, "following_count": 100, "likes_count": 5000, "video_count": 50}}, "error": {"code": "ok"}}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _TK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("tiktok_get_user_info", {})
    # TikTok API returns "error" key even on success ({"code": "ok"})
    assert result is not None
    assert "data" in result


@pytest.mark.asyncio
async def test_tiktok_missing_env():
    from app.mcp.servers.tiktok_server import call_tool

    with patch.dict("os.environ", {"TIKTOK_ACCESS_TOKEN": ""}):
        os.environ.pop("TIKTOK_ACCESS_TOKEN", None)
        result = await call_tool("tiktok_get_user_info", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Vercel
# ---------------------------------------------------------------------------

_VERCEL = {"VERCEL_TOKEN": "vercel-tok"}


@pytest.mark.asyncio
async def test_vercel_list_projects():
    from app.mcp.servers.vercel_server import call_tool

    data = {"projects": [{"id": "proj1", "name": "my-app", "framework": "nextjs", "updatedAt": 1704067200000}], "pagination": {}}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _VERCEL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("vercel_list_projects", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_vercel_list_deployments():
    from app.mcp.servers.vercel_server import call_tool

    data = {"deployments": [{"uid": "dep1", "name": "my-app", "url": "my-app.vercel.app", "state": "READY", "created": 1704067200000}], "pagination": {}}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _VERCEL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("vercel_list_deployments", {"project_id": "proj1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_vercel_unknown_tool():
    from app.mcp.servers.vercel_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _VERCEL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("vercel_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Webflow
# ---------------------------------------------------------------------------

_WF = {"WEBFLOW_API_TOKEN": "wf-tok"}


@pytest.mark.asyncio
async def test_webflow_list_sites():
    from app.mcp.servers.webflow_server import call_tool

    data = {"sites": [{"id": "site1", "name": "My Site", "shortName": "mysite", "lastPublished": "2024-01-01", "previewUrl": "url"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _WF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("webflow_list_sites", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_webflow_missing_env():
    from app.mcp.servers.webflow_server import call_tool

    with patch.dict("os.environ", {"WEBFLOW_API_TOKEN": ""}):
        os.environ.pop("WEBFLOW_API_TOKEN", None)
        result = await call_tool("webflow_list_sites", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------

_WA = {"WHATSAPP_ACCESS_TOKEN": "wa-tok", "WHATSAPP_PHONE_NUMBER_ID": "phone123"}


@pytest.mark.asyncio
async def test_whatsapp_send_text():
    from app.mcp.servers.whatsapp_server import call_tool

    data = {"messaging_product": "whatsapp", "contacts": [{"input": "+1234", "wa_id": "1234"}], "messages": [{"id": "msg1"}]}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _WA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("whatsapp_send_text", {"to": "+1234567890", "body": "Hello!"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_whatsapp_missing_env():
    from app.mcp.servers.whatsapp_server import call_tool

    with patch.dict("os.environ", {"WHATSAPP_PHONE_NUMBER_ID": ""}):
        os.environ.pop("WHATSAPP_PHONE_NUMBER_ID", None)
        result = await call_tool("whatsapp_send_text", {"to": "+1", "body": "hi"})
    assert "error" in result


# ---------------------------------------------------------------------------
# WooCommerce
# ---------------------------------------------------------------------------

_WOO = {
    "WOOCOMMERCE_URL": "https://mystore.com",
    "WOOCOMMERCE_CONSUMER_KEY": "ck_test",
    "WOOCOMMERCE_CONSUMER_SECRET": "cs_test",
}


@pytest.mark.asyncio
async def test_woo_list_products():
    from app.mcp.servers.woocommerce_server import call_tool

    data = [{"id": 1, "name": "T-Shirt", "status": "publish", "price": "29.99", "regular_price": "29.99", "stock_status": "instock", "categories": [], "images": []}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _WOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("woo_list_products", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_woo_list_orders():
    from app.mcp.servers.woocommerce_server import call_tool

    data = [{"id": 100, "number": "100", "status": "processing", "date_created": "2024-01-01", "total": "49.99", "billing": {"email": "a@b.com", "first_name": "Alice"}, "line_items": []}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _WOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("woo_list_orders", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_woo_missing_env():
    from app.mcp.servers.woocommerce_server import call_tool

    with patch.dict("os.environ", {"WOOCOMMERCE_URL": ""}):
        os.environ.pop("WOOCOMMERCE_URL", None)
        result = await call_tool("woo_list_products", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# WordPress
# ---------------------------------------------------------------------------

_WP = {"WORDPRESS_URL": "https://myblog.com", "WORDPRESS_USERNAME": "admin", "WORDPRESS_APP_PASSWORD": "app-pass"}


@pytest.mark.asyncio
async def test_wordpress_list_posts():
    from app.mcp.servers.wordpress_server import call_tool

    data = [{"id": 1, "title": {"rendered": "Hello World"}, "status": "publish", "date": "2024-01-01", "link": "url", "excerpt": {"rendered": "..."}, "categories": [1], "author": 1}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _WP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wordpress_list_posts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_wordpress_missing_env():
    from app.mcp.servers.wordpress_server import call_tool

    with patch.dict("os.environ", {"WORDPRESS_URL": ""}):
        os.environ.pop("WORDPRESS_URL", None)
        result = await call_tool("wordpress_list_posts", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Workday
# ---------------------------------------------------------------------------

_WORKDAY = {
    "WORKDAY_CLIENT_ID": "wd-client",
    "WORKDAY_CLIENT_SECRET": "wd-secret",
    "WORKDAY_TENANT": "myco",
    "WORKDAY_BASE_URL": "https://wd2.myworkday.com/myco/api",
}


@pytest.mark.asyncio
async def test_workday_list_workers():
    from app.mcp.servers.workday_server import call_tool

    # Workday uses OAuth - needs token first
    token_resp = make_resp(data={"access_token": "wd-tok", "expires_in": 3600})
    workers_resp = make_resp(data={"data": [{"id": "w1", "descriptor": "Alice Smith"}]})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    mc.get = AsyncMock(return_value=workers_resp)
    with patch.dict("os.environ", _WORKDAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("workday_list_workers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_workday_missing_env():
    from app.mcp.servers.workday_server import call_tool

    with patch.dict("os.environ", {"WORKDAY_CLIENT_ID": ""}):
        os.environ.pop("WORKDAY_CLIENT_ID", None)
        result = await call_tool("workday_list_workers", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Xero
# ---------------------------------------------------------------------------

_XERO = {"XERO_ACCESS_TOKEN": "xero-tok", "XERO_TENANT_ID": "tenant-123"}


@pytest.mark.asyncio
async def test_xero_list_invoices():
    from app.mcp.servers.xero_server import call_tool

    data = {"Invoices": [{"InvoiceID": "inv1", "InvoiceNumber": "INV-001", "Type": "ACCREC", "Status": "DRAFT", "Contact": {"ContactID": "c1", "Name": "Alice"}, "Total": 500.00, "Date": "2024-01-01", "DueDate": "2024-02-01"}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_list_invoices", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_xero_get_invoice():
    from app.mcp.servers.xero_server import call_tool

    data = {"Invoices": [{"InvoiceID": "inv1", "InvoiceNumber": "INV-001", "Type": "ACCREC", "Status": "DRAFT", "Contact": {"Name": "Alice"}, "Total": 500.00, "LineItems": []}]}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_get_invoice", {"invoice_id": "inv1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_xero_missing_env():
    from app.mcp.servers.xero_server import call_tool

    with patch.dict("os.environ", {"XERO_ACCESS_TOKEN": ""}):
        os.environ.pop("XERO_ACCESS_TOKEN", None)
        result = await call_tool("xero_list_invoices", {})
    assert "error" in result
