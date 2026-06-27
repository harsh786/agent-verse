"""Tests for CRM & Sales MCP connector servers.

These are pure import/schema tests — no network calls are made.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_CRM_SERVERS = [
    "salesforce_server",
    "hubspot_server",
    "pipedrive_server",
    "zoho_crm_server",
    "close_crm_server",
    "copper_server",
    "attio_server",
    "affinity_server",
    "apollo_server",
    "gong_server",
    "linkedin_server",
    "linkedin_ads_server",
    "planhat_server",
]


def _import_server(name: str):
    import importlib
    return importlib.import_module(f"app.mcp.servers.{name}")


# ---------------------------------------------------------------------------
# Import & interface tests
# ---------------------------------------------------------------------------


def test_all_crm_servers_importable():
    """Every server module must be importable and expose the MCP interface."""
    for srv_name in ALL_CRM_SERVERS:
        srv = _import_server(srv_name)
        assert hasattr(srv, "TOOL_DEFINITIONS"), f"{srv_name} missing TOOL_DEFINITIONS"
        assert hasattr(srv, "call_tool"), f"{srv_name} missing call_tool"
        assert callable(srv.call_tool), f"{srv_name}.call_tool is not callable"
        assert len(srv.TOOL_DEFINITIONS) >= 3, (
            f"{srv_name} has only {len(srv.TOOL_DEFINITIONS)} tools — expected ≥ 3"
        )


# ---------------------------------------------------------------------------
# Per-server required-tool tests
# ---------------------------------------------------------------------------


def test_salesforce_has_required_tools():
    from app.mcp.servers.salesforce_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "salesforce_query" in names
    assert "salesforce_create_record" in names
    assert "salesforce_update_record" in names
    assert "salesforce_delete_record" in names
    assert "salesforce_describe_object" in names
    assert "salesforce_search" in names
    assert "salesforce_get_record" in names


def test_hubspot_has_required_tools():
    from app.mcp.servers.hubspot_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "hubspot_list_contacts" in names
    assert "hubspot_create_contact" in names
    assert "hubspot_list_deals" in names
    assert "hubspot_create_deal" in names
    assert "hubspot_update_deal" in names
    assert "hubspot_create_note" in names
    assert "hubspot_search_crm" in names


def test_pipedrive_has_required_tools():
    from app.mcp.servers.pipedrive_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "pipedrive_list_deals" in names
    assert "pipedrive_create_deal" in names
    assert "pipedrive_update_deal" in names
    assert "pipedrive_list_persons" in names
    assert "pipedrive_create_person" in names
    assert "pipedrive_list_organizations" in names
    assert "pipedrive_create_activity" in names
    assert "pipedrive_get_pipeline_stages" in names


def test_zoho_crm_has_required_tools():
    from app.mcp.servers.zoho_crm_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "zoho_list_records" in names
    assert "zoho_create_record" in names
    assert "zoho_update_record" in names
    assert "zoho_delete_record" in names
    assert "zoho_search_records" in names


def test_close_crm_has_required_tools():
    from app.mcp.servers.close_crm_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "close_list_leads" in names
    assert "close_create_lead" in names
    assert "close_update_lead" in names
    assert "close_list_contacts" in names
    assert "close_create_contact" in names
    assert "close_create_activity" in names


def test_copper_has_required_tools():
    from app.mcp.servers.copper_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "copper_list_people" in names
    assert "copper_create_person" in names
    assert "copper_list_companies" in names
    assert "copper_list_opportunities" in names
    assert "copper_create_opportunity" in names


def test_attio_has_required_tools():
    from app.mcp.servers.attio_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "attio_list_records" in names
    assert "attio_create_record" in names
    assert "attio_update_record" in names
    assert "attio_list_notes" in names
    assert "attio_create_note" in names


def test_affinity_has_required_tools():
    from app.mcp.servers.affinity_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "affinity_list_lists" in names
    assert "affinity_list_list_entries" in names
    assert "affinity_create_list_entry" in names
    assert "affinity_list_persons" in names
    assert "affinity_list_organizations" in names


def test_apollo_has_required_tools():
    from app.mcp.servers.apollo_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "apollo_search_people" in names
    assert "apollo_enrich_person" in names
    assert "apollo_search_companies" in names
    assert "apollo_enrich_company" in names
    assert "apollo_get_email" in names


def test_gong_has_required_tools():
    from app.mcp.servers.gong_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "gong_list_calls" in names
    assert "gong_get_call_transcript" in names
    assert "gong_list_users" in names
    assert "gong_get_call_stats" in names


def test_linkedin_has_required_tools():
    from app.mcp.servers.linkedin_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "linkedin_get_profile" in names
    assert "linkedin_search_people" in names
    assert "linkedin_search_companies" in names
    assert "linkedin_create_post" in names


def test_linkedin_ads_has_required_tools():
    from app.mcp.servers.linkedin_ads_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "linkedin_ads_list_accounts" in names
    assert "linkedin_ads_list_campaigns" in names
    assert "linkedin_ads_get_campaign_analytics" in names
    assert "linkedin_ads_list_creatives" in names
    assert "linkedin_ads_create_campaign" in names


def test_planhat_has_required_tools():
    from app.mcp.servers.planhat_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "planhat_list_companies" in names
    assert "planhat_create_company" in names
    assert "planhat_list_endusers" in names
    assert "planhat_create_enduser" in names
    assert "planhat_log_activity" in names


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


def test_all_crm_tools_have_valid_schema():
    """Every tool definition must have name, description, and valid parameters."""
    for srv_name in ALL_CRM_SERVERS:
        srv = _import_server(srv_name)
        for tool in srv.TOOL_DEFINITIONS:
            assert "name" in tool, f"{srv_name}: tool missing 'name'"
            assert "description" in tool, f"{srv_name}/{tool.get('name')}: missing 'description'"
            assert "parameters" in tool, f"{srv_name}/{tool.get('name')}: missing 'parameters'"
            params = tool["parameters"]
            assert params.get("type") == "object", (
                f"{srv_name}/{tool['name']}: parameters.type must be 'object'"
            )
            assert "properties" in params, (
                f"{srv_name}/{tool['name']}: parameters missing 'properties'"
            )


def test_tool_names_are_unique_per_server():
    """No server should have duplicate tool names."""
    for srv_name in ALL_CRM_SERVERS:
        srv = _import_server(srv_name)
        names = [t["name"] for t in srv.TOOL_DEFINITIONS]
        assert len(names) == len(set(names)), (
            f"{srv_name} has duplicate tool names: {names}"
        )


def test_required_fields_are_subset_of_properties():
    """Every field in 'required' must appear in 'properties'."""
    for srv_name in ALL_CRM_SERVERS:
        srv = _import_server(srv_name)
        for tool in srv.TOOL_DEFINITIONS:
            params = tool["parameters"]
            props = set(params.get("properties", {}).keys())
            required = params.get("required", [])
            for field in required:
                assert field in props, (
                    f"{srv_name}/{tool['name']}: required field '{field}' not in properties"
                )


# ---------------------------------------------------------------------------
# Registry wiring test
# ---------------------------------------------------------------------------


def test_registry_wiring_includes_all_crm_servers():
    """get_builtin_server_configs must include entries for all CRM servers."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    server_ids = {c["server_id"] for c in configs}

    expected_ids = {
        "builtin-salesforce",
        "builtin-hubspot",
        "builtin-pipedrive",
        "builtin-zoho-crm",
        "builtin-close-crm",
        "builtin-copper",
        "builtin-attio",
        "builtin-affinity",
        "builtin-apollo",
        "builtin-gong",
        "builtin-linkedin",
        "builtin-linkedin-ads",
        "builtin-planhat",
        # Original servers still present
        "builtin-github",
        "builtin-postgres",
        "builtin-slack",
    }
    missing = expected_ids - server_ids
    assert not missing, f"Registry missing server IDs: {missing}"


def test_registry_configs_have_required_keys():
    """Each registry config must expose the fields the registration loop expects."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    required_keys = {"server_id", "name", "description", "tool_definitions", "handler", "requires_env"}
    for cfg in get_builtin_server_configs():
        missing = required_keys - set(cfg.keys())
        assert not missing, f"Config {cfg.get('server_id')} missing keys: {missing}"
        assert callable(cfg["handler"]), f"handler for {cfg['server_id']} is not callable"
        assert isinstance(cfg["tool_definitions"], list)
        assert isinstance(cfg["requires_env"], list)


@pytest.mark.asyncio
async def test_call_tool_returns_error_without_env(monkeypatch):
    """call_tool must return an error dict when credentials are not set."""
    import os

    servers_and_env = [
        ("salesforce_server", ["SALESFORCE_INSTANCE_URL", "SALESFORCE_ACCESS_TOKEN"]),
        ("hubspot_server", ["HUBSPOT_API_KEY"]),
        ("pipedrive_server", ["PIPEDRIVE_API_TOKEN", "PIPEDRIVE_COMPANY_DOMAIN"]),
        ("zoho_crm_server", ["ZOHO_ACCESS_TOKEN"]),
        ("close_crm_server", ["CLOSE_API_KEY"]),
        ("copper_server", ["COPPER_API_KEY", "COPPER_USER_EMAIL"]),
        ("attio_server", ["ATTIO_API_KEY"]),
        ("affinity_server", ["AFFINITY_API_KEY"]),
        ("apollo_server", ["APOLLO_API_KEY"]),
        ("gong_server", ["GONG_ACCESS_KEY", "GONG_ACCESS_KEY_SECRET"]),
        ("linkedin_server", ["LINKEDIN_ACCESS_TOKEN"]),
        ("linkedin_ads_server", ["LINKEDIN_ACCESS_TOKEN"]),
        ("planhat_server", ["PLANHAT_API_KEY"]),
    ]

    for srv_name, env_vars in servers_and_env:
        # Ensure env vars are cleared
        for ev in env_vars:
            monkeypatch.delenv(ev, raising=False)

        srv = _import_server(srv_name)
        first_tool = srv.TOOL_DEFINITIONS[0]["name"]
        result = await srv.call_tool(first_tool, {})
        assert "error" in result, (
            f"{srv_name}.call_tool should return error dict when credentials missing, got: {result}"
        )
