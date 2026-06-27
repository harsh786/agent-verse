"""Tests for Communication, Email & Marketing MCP connector servers.

These are pure import/schema tests — no network calls are made.
"""
from __future__ import annotations

import importlib

import pytest


# ---------------------------------------------------------------------------
# Server registry
# ---------------------------------------------------------------------------

ALL_COMM_SERVERS = [
    "discord_server",
    "telegram_server",
    "microsoft_teams_server",
    "whatsapp_server",
    "mattermost_server",
    "intercom_server",
    "sendgrid_server",
    "mailchimp_server",
    "klaviyo_server",
    "brevo_server",
    "mailerlite_server",
    "convertkit_server",
    "customerio_server",
    "twilio_server",
    "mandrill_server",
]


def _import_server(name: str):
    return importlib.import_module(f"app.mcp.servers.{name}")


# ---------------------------------------------------------------------------
# Import & interface tests (matches spec)
# ---------------------------------------------------------------------------


def test_communication_servers_importable():
    from app.mcp.servers import (
        discord_server,
        telegram_server,
        microsoft_teams_server,
        whatsapp_server,
        intercom_server,
        sendgrid_server,
        mailchimp_server,
        klaviyo_server,
        twilio_server,
        brevo_server,
    )

    for s in [
        discord_server,
        telegram_server,
        microsoft_teams_server,
        whatsapp_server,
        intercom_server,
        sendgrid_server,
        mailchimp_server,
        klaviyo_server,
        twilio_server,
        brevo_server,
    ]:
        assert hasattr(s, "TOOL_DEFINITIONS") and len(s.TOOL_DEFINITIONS) >= 3


def test_all_comm_servers_have_interface():
    """Every server module must expose TOOL_DEFINITIONS, call_tool, and ≥3 tools."""
    for srv_name in ALL_COMM_SERVERS:
        srv = _import_server(srv_name)
        assert hasattr(srv, "TOOL_DEFINITIONS"), f"{srv_name} missing TOOL_DEFINITIONS"
        assert hasattr(srv, "call_tool"), f"{srv_name} missing call_tool"
        assert callable(srv.call_tool), f"{srv_name}.call_tool is not callable"
        assert len(srv.TOOL_DEFINITIONS) >= 3, (
            f"{srv_name} has only {len(srv.TOOL_DEFINITIONS)} tools — expected ≥ 3"
        )


# ---------------------------------------------------------------------------
# Per-server required-tool tests (matches spec)
# ---------------------------------------------------------------------------


def test_sendgrid_send_email_tool():
    from app.mcp.servers.sendgrid_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "sendgrid_send_email" in names
    assert "sendgrid_send_template" in names


def test_twilio_has_sms_and_whatsapp():
    from app.mcp.servers.twilio_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "twilio_send_sms" in names


def test_telegram_sends_messages():
    from app.mcp.servers.telegram_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "telegram_send_message" in names


def test_discord_has_required_tools():
    from app.mcp.servers.discord_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "discord_send_message" in names
    assert "discord_list_guilds" in names
    assert "discord_list_channels" in names
    assert "discord_get_messages" in names
    assert "discord_create_thread" in names
    assert "discord_add_reaction" in names
    assert "discord_delete_message" in names


def test_telegram_has_required_tools():
    from app.mcp.servers.telegram_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "telegram_send_message" in names
    assert "telegram_send_document" in names
    assert "telegram_send_photo" in names
    assert "telegram_get_updates" in names
    assert "telegram_get_chat" in names
    assert "telegram_create_invite_link" in names
    assert "telegram_pin_message" in names


def test_teams_has_required_tools():
    from app.mcp.servers.microsoft_teams_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "teams_list_teams" in names
    assert "teams_list_channels" in names
    assert "teams_send_message" in names
    assert "teams_create_channel" in names
    assert "teams_list_messages" in names


def test_whatsapp_has_required_tools():
    from app.mcp.servers.whatsapp_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "whatsapp_send_text" in names
    assert "whatsapp_send_template" in names
    assert "whatsapp_send_media" in names
    assert "whatsapp_mark_read" in names


def test_mattermost_has_required_tools():
    from app.mcp.servers.mattermost_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "mattermost_send_message" in names
    assert "mattermost_list_channels" in names
    assert "mattermost_get_posts" in names


def test_intercom_has_required_tools():
    from app.mcp.servers.intercom_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "intercom_list_conversations" in names
    assert "intercom_get_conversation" in names
    assert "intercom_reply_conversation" in names
    assert "intercom_list_users" in names
    assert "intercom_create_user" in names
    assert "intercom_create_note" in names
    assert "intercom_tag_user" in names


def test_sendgrid_has_required_tools():
    from app.mcp.servers.sendgrid_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "sendgrid_send_email" in names
    assert "sendgrid_send_bulk" in names
    assert "sendgrid_list_templates" in names
    assert "sendgrid_send_template" in names
    assert "sendgrid_get_stats" in names
    assert "sendgrid_list_contacts" in names
    assert "sendgrid_add_contacts" in names
    assert "sendgrid_create_list" in names


def test_mailchimp_has_required_tools():
    from app.mcp.servers.mailchimp_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "mailchimp_list_lists" in names
    assert "mailchimp_add_member" in names
    assert "mailchimp_update_member" in names
    assert "mailchimp_create_campaign" in names
    assert "mailchimp_send_campaign" in names
    assert "mailchimp_list_members" in names


def test_klaviyo_has_required_tools():
    from app.mcp.servers.klaviyo_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "klaviyo_list_profiles" in names
    assert "klaviyo_create_profile" in names
    assert "klaviyo_update_profile" in names
    assert "klaviyo_list_lists" in names
    assert "klaviyo_add_to_list" in names
    assert "klaviyo_send_event" in names
    assert "klaviyo_list_campaigns" in names


def test_brevo_has_required_tools():
    from app.mcp.servers.brevo_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "brevo_send_email" in names
    assert "brevo_list_contacts" in names
    assert "brevo_create_contact" in names
    assert "brevo_list_campaigns" in names


def test_mailerlite_has_required_tools():
    from app.mcp.servers.mailerlite_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "mailerlite_list_subscribers" in names
    assert "mailerlite_create_subscriber" in names
    assert "mailerlite_list_groups" in names
    assert "mailerlite_list_campaigns" in names


def test_convertkit_has_required_tools():
    from app.mcp.servers.convertkit_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "convertkit_list_subscribers" in names
    assert "convertkit_create_subscriber" in names
    assert "convertkit_list_forms" in names
    assert "convertkit_list_tags" in names
    assert "convertkit_tag_subscriber" in names


def test_customerio_has_required_tools():
    from app.mcp.servers.customerio_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "customerio_identify" in names
    assert "customerio_track_event" in names
    assert "customerio_delete_customer" in names
    assert "customerio_send_email" in names


def test_twilio_has_required_tools():
    from app.mcp.servers.twilio_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "twilio_send_sms" in names
    assert "twilio_send_whatsapp" in names
    assert "twilio_list_messages" in names
    assert "twilio_make_call" in names
    assert "twilio_lookup_number" in names
    assert "twilio_list_numbers" in names


def test_mandrill_has_required_tools():
    from app.mcp.servers.mandrill_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "mandrill_send_email" in names
    assert "mandrill_send_template" in names
    assert "mandrill_list_templates" in names
    assert "mandrill_search_messages" in names


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


def test_all_comm_tools_have_valid_schema():
    """Every tool definition must have name, description, and valid parameters."""
    for srv_name in ALL_COMM_SERVERS:
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
    for srv_name in ALL_COMM_SERVERS:
        srv = _import_server(srv_name)
        names = [t["name"] for t in srv.TOOL_DEFINITIONS]
        assert len(names) == len(set(names)), (
            f"{srv_name} has duplicate tool names: {names}"
        )


def test_required_fields_are_subset_of_properties():
    """Every field in 'required' must appear in 'properties'."""
    for srv_name in ALL_COMM_SERVERS:
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


def test_registry_wiring_includes_all_comm_servers():
    """get_builtin_server_configs must include entries for all communication servers."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    server_ids = {c["server_id"] for c in configs}

    expected_ids = {
        "builtin-discord",
        "builtin-telegram",
        "builtin-microsoft-teams",
        "builtin-whatsapp",
        "builtin-mattermost",
        "builtin-intercom",
        "builtin-sendgrid",
        "builtin-mailchimp",
        "builtin-klaviyo",
        "builtin-brevo",
        "builtin-mailerlite",
        "builtin-convertkit",
        "builtin-customerio",
        "builtin-twilio",
        "builtin-mandrill",
    }
    missing = expected_ids - server_ids
    assert not missing, f"Registry missing server IDs: {missing}"


def test_registry_comm_configs_have_required_keys():
    """Each communication registry config must expose the required fields."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    required_keys = {"server_id", "name", "description", "tool_definitions", "handler", "requires_env"}
    comm_ids = {
        "builtin-discord", "builtin-telegram", "builtin-microsoft-teams",
        "builtin-whatsapp", "builtin-mattermost", "builtin-intercom",
        "builtin-sendgrid", "builtin-mailchimp", "builtin-klaviyo",
        "builtin-brevo", "builtin-mailerlite", "builtin-convertkit",
        "builtin-customerio", "builtin-twilio", "builtin-mandrill",
    }
    for cfg in get_builtin_server_configs():
        if cfg.get("server_id") not in comm_ids:
            continue
        missing = required_keys - set(cfg.keys())
        assert not missing, f"Config {cfg.get('server_id')} missing keys: {missing}"
        assert callable(cfg["handler"]), f"handler for {cfg['server_id']} is not callable"
        assert isinstance(cfg["tool_definitions"], list)
        assert isinstance(cfg["requires_env"], list)


# ---------------------------------------------------------------------------
# Error-without-env tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_returns_error_without_env(monkeypatch):
    """call_tool must return an error dict when credentials are not set."""
    servers_and_env = [
        ("discord_server", ["DISCORD_BOT_TOKEN"]),
        ("telegram_server", ["TELEGRAM_BOT_TOKEN"]),
        ("microsoft_teams_server", ["TEAMS_ACCESS_TOKEN"]),
        ("whatsapp_server", ["WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN"]),
        ("mattermost_server", ["MATTERMOST_URL", "MATTERMOST_TOKEN"]),
        ("intercom_server", ["INTERCOM_ACCESS_TOKEN"]),
        ("sendgrid_server", ["SENDGRID_API_KEY"]),
        ("mailchimp_server", ["MAILCHIMP_API_KEY"]),
        ("klaviyo_server", ["KLAVIYO_API_KEY"]),
        ("brevo_server", ["BREVO_API_KEY"]),
        ("mailerlite_server", ["MAILERLITE_API_KEY"]),
        ("convertkit_server", ["CONVERTKIT_API_KEY"]),
        ("customerio_server", ["CUSTOMERIO_SITE_ID", "CUSTOMERIO_API_KEY", "CUSTOMERIO_APP_API_KEY"]),
        ("twilio_server", ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"]),
        ("mandrill_server", ["MANDRILL_API_KEY"]),
    ]

    for srv_name, env_vars in servers_and_env:
        for ev in env_vars:
            monkeypatch.delenv(ev, raising=False)

        srv = _import_server(srv_name)
        first_tool = srv.TOOL_DEFINITIONS[0]["name"]
        result = await srv.call_tool(first_tool, {})
        assert "error" in result, (
            f"{srv_name}.call_tool should return error dict when credentials missing, got: {result}"
        )
