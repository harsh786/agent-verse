"""Tests for Database & Analytics MCP connector servers.

Verifies:
- All servers are importable
- TOOL_DEFINITIONS are well-formed (name, description, parameters)
- Each server has the expected minimum number of tools
- Specific tools required by contract are present
- Registry wiring includes all new servers
- No-config guards return sensible error dicts (not raise exceptions)
"""
from __future__ import annotations

import os
import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _assert_tool_schema(tools: list[dict], server_name: str) -> None:
    for t in tools:
        assert "name" in t, f"{server_name}: tool missing 'name': {t}"
        assert "description" in t, f"{server_name}: tool missing 'description': {t}"
        assert "parameters" in t, f"{server_name}: tool missing 'parameters': {t}"
        assert isinstance(t["parameters"], dict), f"{server_name}: 'parameters' must be a dict"


# ---------------------------------------------------------------------------
# Import & schema tests
# ---------------------------------------------------------------------------

def test_database_servers_importable():
    from app.mcp.servers import (
        mysql_server,
        mongodb_server,
        redis_server,
        snowflake_server,
        elasticsearch_server,
        supabase_server,
        pinecone_server,
        sentry_server,
        new_relic_server,
        mixpanel_server,
        amplitude_server,
        prometheus_server,
        splunk_server,
        loggly_server,
    )
    for s in [
        mysql_server,
        mongodb_server,
        redis_server,
        snowflake_server,
        elasticsearch_server,
        supabase_server,
        pinecone_server,
        sentry_server,
        new_relic_server,
        mixpanel_server,
        amplitude_server,
        prometheus_server,
        splunk_server,
        loggly_server,
    ]:
        assert hasattr(s, "TOOL_DEFINITIONS"), f"{s.__name__} missing TOOL_DEFINITIONS"
        assert len(s.TOOL_DEFINITIONS) >= 3, (
            f"{s.__name__} should have at least 3 tools, has {len(s.TOOL_DEFINITIONS)}"
        )
        assert hasattr(s, "call_tool"), f"{s.__name__} missing call_tool"
        assert callable(s.call_tool), f"{s.__name__}.call_tool is not callable"


def test_all_servers_have_valid_tool_schemas():
    from app.mcp.servers import (
        mysql_server, mongodb_server, redis_server, snowflake_server,
        elasticsearch_server, supabase_server, pinecone_server,
        sentry_server, new_relic_server, mixpanel_server, amplitude_server,
        prometheus_server, splunk_server, loggly_server,
    )
    for s in [
        mysql_server, mongodb_server, redis_server, snowflake_server,
        elasticsearch_server, supabase_server, pinecone_server,
        sentry_server, new_relic_server, mixpanel_server, amplitude_server,
        prometheus_server, splunk_server, loggly_server,
    ]:
        _assert_tool_schema(s.TOOL_DEFINITIONS, s.__name__)


# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------

def test_mysql_has_required_tools():
    from app.mcp.servers.mysql_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "mysql_query" in names
    assert "mysql_execute" in names
    assert "mysql_list_tables" in names
    assert "mysql_describe_table" in names


@pytest.mark.asyncio
async def test_mysql_no_url_returns_error():
    from app.mcp.servers.mysql_server import call_tool
    old = os.environ.pop("MYSQL_MCP_URL", None)
    try:
        result = await call_tool("mysql_query", {"sql": "SELECT 1"})
        assert "error" in result
        assert "MYSQL_MCP_URL" in result["error"]
    finally:
        if old is not None:
            os.environ["MYSQL_MCP_URL"] = old


# ---------------------------------------------------------------------------
# MongoDB
# ---------------------------------------------------------------------------

def test_mongodb_has_required_tools():
    from app.mcp.servers.mongodb_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "mongodb_find" in names
    assert "mongodb_find_one" in names
    assert "mongodb_insert_one" in names
    assert "mongodb_update_one" in names
    assert "mongodb_delete_one" in names
    assert "mongodb_aggregate" in names
    assert "mongodb_list_collections" in names
    assert "mongodb_count" in names


@pytest.mark.asyncio
async def test_mongodb_no_url_returns_error():
    from app.mcp.servers.mongodb_server import call_tool
    old = os.environ.pop("MONGODB_MCP_URL", None)
    try:
        result = await call_tool("mongodb_find", {"collection": "test"})
        assert "error" in result
        assert "MONGODB_MCP_URL" in result["error"]
    finally:
        if old is not None:
            os.environ["MONGODB_MCP_URL"] = old


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------

def test_redis_has_required_tools():
    from app.mcp.servers.redis_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "redis_get" in names
    assert "redis_set" in names
    assert "redis_delete" in names
    assert "redis_list_keys" in names
    assert "redis_hget" in names
    assert "redis_hset" in names
    assert "redis_hgetall" in names
    assert "redis_lpush" in names
    assert "redis_lrange" in names
    assert "redis_publish" in names


@pytest.mark.asyncio
async def test_redis_no_url_returns_error():
    from app.mcp.servers.redis_server import call_tool
    old = os.environ.pop("REDIS_MCP_URL", None)
    try:
        result = await call_tool("redis_get", {"key": "test"})
        assert "error" in result
        assert "REDIS_MCP_URL" in result["error"]
    finally:
        if old is not None:
            os.environ["REDIS_MCP_URL"] = old


# ---------------------------------------------------------------------------
# Snowflake
# ---------------------------------------------------------------------------

def test_snowflake_has_required_tools():
    from app.mcp.servers.snowflake_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "snowflake_query" in names
    assert "snowflake_execute" in names
    assert "snowflake_list_tables" in names
    assert "snowflake_describe_table" in names
    assert "snowflake_show_databases" in names


@pytest.mark.asyncio
async def test_snowflake_no_creds_returns_error():
    from app.mcp.servers.snowflake_server import call_tool
    for key in ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"]:
        os.environ.pop(key, None)
    result = await call_tool("snowflake_query", {"sql": "SELECT 1"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Elasticsearch
# ---------------------------------------------------------------------------

def test_elasticsearch_has_required_tools():
    from app.mcp.servers.elasticsearch_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "elasticsearch_search" in names
    assert "elasticsearch_index_document" in names
    assert "elasticsearch_get_document" in names
    assert "elasticsearch_delete_document" in names
    assert "elasticsearch_list_indices" in names
    assert "elasticsearch_create_index" in names
    assert "elasticsearch_bulk_index" in names


@pytest.mark.asyncio
async def test_elasticsearch_no_url_returns_error():
    from app.mcp.servers.elasticsearch_server import call_tool
    old = os.environ.pop("ELASTICSEARCH_URL", None)
    try:
        result = await call_tool("elasticsearch_search", {"index": "test"})
        assert "error" in result
        assert "ELASTICSEARCH_URL" in result["error"]
    finally:
        if old is not None:
            os.environ["ELASTICSEARCH_URL"] = old


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def test_supabase_has_required_tools():
    from app.mcp.servers.supabase_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "supabase_select" in names
    assert "supabase_insert" in names
    assert "supabase_update" in names
    assert "supabase_delete" in names
    assert "supabase_execute_sql" in names
    assert "supabase_list_tables" in names
    assert "supabase_auth_list_users" in names


@pytest.mark.asyncio
async def test_supabase_no_creds_returns_error():
    from app.mcp.servers.supabase_server import call_tool
    for key in ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"]:
        os.environ.pop(key, None)
    result = await call_tool("supabase_select", {"table": "users"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Pinecone
# ---------------------------------------------------------------------------

def test_pinecone_has_vector_search():
    from app.mcp.servers.pinecone_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "pinecone_query_vectors" in names
    assert "pinecone_upsert_vectors" in names
    assert "pinecone_list_indexes" in names
    assert "pinecone_describe_index" in names
    assert "pinecone_delete_vectors" in names
    assert "pinecone_fetch_vectors" in names


@pytest.mark.asyncio
async def test_pinecone_no_api_key_returns_error():
    from app.mcp.servers.pinecone_server import call_tool
    old = os.environ.pop("PINECONE_API_KEY", None)
    try:
        result = await call_tool("pinecone_list_indexes", {})
        assert "error" in result
        assert "PINECONE_API_KEY" in result["error"]
    finally:
        if old is not None:
            os.environ["PINECONE_API_KEY"] = old


# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------

def test_sentry_has_issue_tools():
    from app.mcp.servers.sentry_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "sentry_list_issues" in names
    assert "sentry_update_issue" in names
    assert "sentry_list_projects" in names
    assert "sentry_get_issue" in names
    assert "sentry_list_events" in names
    assert "sentry_create_release" in names
    assert "sentry_query" in names


@pytest.mark.asyncio
async def test_sentry_no_token_returns_error():
    from app.mcp.servers.sentry_server import call_tool
    old = os.environ.pop("SENTRY_AUTH_TOKEN", None)
    try:
        result = await call_tool("sentry_list_issues", {"project_slug": "test"})
        assert "error" in result
        assert "SENTRY_AUTH_TOKEN" in result["error"]
    finally:
        if old is not None:
            os.environ["SENTRY_AUTH_TOKEN"] = old


# ---------------------------------------------------------------------------
# New Relic
# ---------------------------------------------------------------------------

def test_new_relic_has_required_tools():
    from app.mcp.servers.new_relic_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "newrelic_nrql_query" in names
    assert "newrelic_list_alerts" in names
    assert "newrelic_list_applications" in names
    assert "newrelic_get_metrics" in names


@pytest.mark.asyncio
async def test_new_relic_no_api_key_returns_error():
    from app.mcp.servers.new_relic_server import call_tool
    old = os.environ.pop("NEW_RELIC_API_KEY", None)
    try:
        result = await call_tool("newrelic_nrql_query", {"nrql": "SELECT count(*) FROM Transaction"})
        assert "error" in result
        assert "NEW_RELIC_API_KEY" in result["error"]
    finally:
        if old is not None:
            os.environ["NEW_RELIC_API_KEY"] = old


# ---------------------------------------------------------------------------
# Mixpanel
# ---------------------------------------------------------------------------

def test_mixpanel_has_required_tools():
    from app.mcp.servers.mixpanel_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "mixpanel_query_events" in names
    assert "mixpanel_query_funnels" in names
    assert "mixpanel_query_retention" in names
    assert "mixpanel_user_profile" in names


@pytest.mark.asyncio
async def test_mixpanel_no_creds_returns_error():
    from app.mcp.servers.mixpanel_server import call_tool
    for key in ["MIXPANEL_SERVICE_ACCOUNT_USERNAME", "MIXPANEL_SERVICE_ACCOUNT_SECRET", "MIXPANEL_PROJECT_ID"]:
        os.environ.pop(key, None)
    result = await call_tool("mixpanel_query_events", {"from_date": "2024-01-01", "to_date": "2024-01-02"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Amplitude
# ---------------------------------------------------------------------------

def test_amplitude_has_required_tools():
    from app.mcp.servers.amplitude_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "amplitude_query_events" in names
    assert "amplitude_get_cohort" in names
    assert "amplitude_export_events" in names
    assert "amplitude_user_profile" in names


@pytest.mark.asyncio
async def test_amplitude_no_creds_returns_error():
    from app.mcp.servers.amplitude_server import call_tool
    for key in ["AMPLITUDE_API_KEY", "AMPLITUDE_SECRET_KEY"]:
        os.environ.pop(key, None)
    result = await call_tool("amplitude_query_events", {
        "event": {"event_type": "Click"},
        "start": "20240101",
        "end": "20240102",
    })
    assert "error" in result


# ---------------------------------------------------------------------------
# Prometheus
# ---------------------------------------------------------------------------

def test_prometheus_has_required_tools():
    from app.mcp.servers.prometheus_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "prometheus_query" in names
    assert "prometheus_query_range" in names
    assert "prometheus_list_metrics" in names
    assert "prometheus_get_alerts" in names
    assert "prometheus_get_targets" in names


@pytest.mark.asyncio
async def test_prometheus_no_url_returns_error():
    from app.mcp.servers.prometheus_server import call_tool
    old = os.environ.pop("PROMETHEUS_URL", None)
    try:
        result = await call_tool("prometheus_query", {"query": "up"})
        assert "error" in result
        assert "PROMETHEUS_URL" in result["error"]
    finally:
        if old is not None:
            os.environ["PROMETHEUS_URL"] = old


# ---------------------------------------------------------------------------
# Splunk
# ---------------------------------------------------------------------------

def test_splunk_has_required_tools():
    from app.mcp.servers.splunk_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "splunk_search" in names
    assert "splunk_create_search_job" in names
    assert "splunk_get_job_results" in names
    assert "splunk_list_indexes" in names
    assert "splunk_get_alerts" in names


@pytest.mark.asyncio
async def test_splunk_no_url_returns_error():
    from app.mcp.servers.splunk_server import call_tool
    old = os.environ.pop("SPLUNK_URL", None)
    try:
        result = await call_tool("splunk_search", {"search": "search index=main | head 10"})
        assert "error" in result
        assert "SPLUNK_URL" in result["error"]
    finally:
        if old is not None:
            os.environ["SPLUNK_URL"] = old


# ---------------------------------------------------------------------------
# Loggly
# ---------------------------------------------------------------------------

def test_loggly_has_required_tools():
    from app.mcp.servers.loggly_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "loggly_search" in names
    assert "loggly_get_event" in names
    assert "loggly_facets" in names
    assert "loggly_list_devices" in names


@pytest.mark.asyncio
async def test_loggly_no_creds_returns_error():
    from app.mcp.servers.loggly_server import call_tool
    for key in ["LOGGLY_ACCOUNT", "LOGGLY_API_TOKEN"]:
        os.environ.pop(key, None)
    result = await call_tool("loggly_search", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

def test_registry_wiring_includes_all_new_servers():
    """registry_wiring.get_builtin_server_configs() must include all new servers."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    server_ids = {c["server_id"] for c in configs}

    # Core originals must still be present
    assert "builtin-github" in server_ids
    assert "builtin-postgres" in server_ids
    assert "builtin-slack" in server_ids

    # New database servers (already existed before this PR in some cases)
    assert "builtin-mysql" in server_ids
    assert "builtin-mongodb" in server_ids
    assert "builtin-redis" in server_ids
    assert "builtin-snowflake" in server_ids

    # New analytics & monitoring servers added by this PR
    new_servers = {
        "builtin-elasticsearch",
        "builtin-supabase",
        "builtin-pinecone",
        "builtin-sentry",
        "builtin-new-relic",
        "builtin-mixpanel",
        "builtin-amplitude",
        "builtin-prometheus",
        "builtin-splunk",
        "builtin-loggly",
    }
    missing = new_servers - server_ids
    assert not missing, f"Missing server IDs in registry: {missing}"


def test_registry_wiring_all_handlers_callable():
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    for cfg in get_builtin_server_configs():
        assert callable(cfg["handler"]), f"{cfg['name']} handler is not callable"
        assert cfg["tool_definitions"], f"{cfg['name']} has no tool_definitions"


def test_registry_wiring_total_count():
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    server_ids = {c["server_id"] for c in configs}
    # We added 10 new analytics/monitoring servers; total ≥ 76 (existing) + 10
    assert len(configs) >= 76, f"Expected at least 76 builtin server configs, got {len(configs)}"
    # All 10 new servers must be present
    new_ids = {
        "builtin-elasticsearch", "builtin-supabase", "builtin-pinecone",
        "builtin-sentry", "builtin-new-relic", "builtin-mixpanel",
        "builtin-amplitude", "builtin-prometheus", "builtin-splunk",
        "builtin-loggly",
    }
    missing = new_ids - server_ids
    assert not missing, f"Missing new server IDs: {missing}"


def test_registry_wiring_existing_test_still_passes(monkeypatch):
    """Backward compat: setting GITHUB_TOKEN still yields builtin-github as active."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")

    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    active = [
        c
        for c in configs
        if all(os.getenv(e, "") for e in c.get("requires_env", []))
    ]
    # At minimum GitHub is active
    active_ids = {c["server_id"] for c in active}
    assert "builtin-github" in active_ids
