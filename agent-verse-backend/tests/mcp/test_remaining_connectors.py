"""Tests for the new MCP connector servers — HR, support, e-commerce,
social media, AI/search, scheduling, finance, and developer tools.
"""
from __future__ import annotations


# ── Helpers ──────────────────────────────────────────────────────────────────

def _assert_tool_structure(tool: dict) -> None:
    """Verify a single tool definition has all required fields."""
    assert "name" in tool, f"Tool missing 'name': {tool}"
    assert "description" in tool, f"Tool missing 'description': {tool}"
    assert "parameters" in tool, f"Tool missing 'parameters': {tool}"
    assert isinstance(tool["parameters"], dict), (
        f"Tool parameters must be a dict: {tool}"
    )


def _assert_server(module, min_tools: int = 5) -> None:
    """Assert that a server module has a valid TOOL_DEFINITIONS list."""
    assert hasattr(module, "TOOL_DEFINITIONS"), (
        f"{module.__name__} missing TOOL_DEFINITIONS"
    )
    assert hasattr(module, "call_tool"), (
        f"{module.__name__} missing call_tool"
    )
    assert callable(module.call_tool), (
        f"{module.__name__}.call_tool is not callable"
    )
    assert len(module.TOOL_DEFINITIONS) >= min_tools, (
        f"{module.__name__} has {len(module.TOOL_DEFINITIONS)} tools, expected >= {min_tools}"
    )
    for tool in module.TOOL_DEFINITIONS:
        _assert_tool_structure(tool)


# ── HR & Workforce ────────────────────────────────────────────────────────────

def test_bamboohr_server_structure() -> None:
    from app.mcp.servers import bamboohr_server
    _assert_server(bamboohr_server, min_tools=5)


def test_bamboohr_server_tools() -> None:
    from app.mcp.servers.bamboohr_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "bamboo_get_employee" in names
    assert "bamboo_list_employees" in names
    assert "bamboo_update_employee" in names
    assert "bamboo_get_time_off" in names
    assert "bamboo_request_time_off" in names
    assert "bamboo_list_departments" in names


def test_workday_server_structure() -> None:
    from app.mcp.servers import workday_server
    _assert_server(workday_server, min_tools=4)


def test_deel_server_structure() -> None:
    from app.mcp.servers import deel_server
    _assert_server(deel_server, min_tools=5)


def test_rippling_server_structure() -> None:
    from app.mcp.servers import rippling_server
    _assert_server(rippling_server, min_tools=4)


def test_hr_support_servers() -> None:
    from app.mcp.servers import bamboohr_server, zendesk_server, freshdesk_server
    for s in [bamboohr_server, zendesk_server, freshdesk_server]:
        assert hasattr(s, "TOOL_DEFINITIONS") and len(s.TOOL_DEFINITIONS) >= 5


# ── Customer Support ──────────────────────────────────────────────────────────

def test_zendesk_server_structure() -> None:
    from app.mcp.servers import zendesk_server
    _assert_server(zendesk_server, min_tools=7)


def test_zendesk_server_tools() -> None:
    from app.mcp.servers.zendesk_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "zendesk_list_tickets" in names
    assert "zendesk_get_ticket" in names
    assert "zendesk_create_ticket" in names
    assert "zendesk_update_ticket" in names
    assert "zendesk_add_comment" in names
    assert "zendesk_search" in names
    assert "zendesk_list_users" in names
    assert "zendesk_list_organizations" in names


def test_freshdesk_server_structure() -> None:
    from app.mcp.servers import freshdesk_server
    _assert_server(freshdesk_server, min_tools=5)


def test_freshdesk_server_tools() -> None:
    from app.mcp.servers.freshdesk_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "freshdesk_list_tickets" in names
    assert "freshdesk_create_ticket" in names
    assert "freshdesk_update_ticket" in names
    assert "freshdesk_reply_ticket" in names


def test_freshservice_server_structure() -> None:
    from app.mcp.servers import freshservice_server
    _assert_server(freshservice_server, min_tools=5)


def test_gorgias_server_structure() -> None:
    from app.mcp.servers import gorgias_server
    _assert_server(gorgias_server, min_tools=5)


def test_front_server_structure() -> None:
    from app.mcp.servers import front_server
    _assert_server(front_server, min_tools=6)


# ── E-commerce ────────────────────────────────────────────────────────────────

def test_shopify_server_structure() -> None:
    from app.mcp.servers import shopify_server
    _assert_server(shopify_server, min_tools=9)


def test_shopify_ecommerce() -> None:
    from app.mcp.servers.shopify_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "shopify_list_products" in names
    assert "shopify_list_orders" in names
    assert "shopify_create_product" in names
    assert "shopify_get_product" in names
    assert "shopify_list_customers" in names
    assert "shopify_create_customer" in names
    assert "shopify_update_inventory" in names


def test_wordpress_server_structure() -> None:
    from app.mcp.servers import wordpress_server
    _assert_server(wordpress_server, min_tools=6)


def test_webflow_server_structure() -> None:
    from app.mcp.servers import webflow_server
    _assert_server(webflow_server, min_tools=6)


def test_woocommerce_server_structure() -> None:
    from app.mcp.servers import woocommerce_server
    _assert_server(woocommerce_server, min_tools=6)


def test_ecommerce_social_servers() -> None:
    from app.mcp.servers import shopify_server, x_twitter_server, youtube_server
    for s in [shopify_server, x_twitter_server, youtube_server]:
        assert hasattr(s, "TOOL_DEFINITIONS") and len(s.TOOL_DEFINITIONS) >= 5


# ── Social Media ──────────────────────────────────────────────────────────────

def test_x_twitter_server_structure() -> None:
    from app.mcp.servers import x_twitter_server
    _assert_server(x_twitter_server, min_tools=6)


def test_x_twitter_server_tools() -> None:
    from app.mcp.servers.x_twitter_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "twitter_search_tweets" in names
    assert "twitter_get_tweet" in names
    assert "twitter_create_tweet" in names
    assert "twitter_delete_tweet" in names
    assert "twitter_get_user_tweets" in names
    assert "twitter_lookup_user" in names
    assert "twitter_follow_user" in names


def test_instagram_server_structure() -> None:
    from app.mcp.servers import instagram_server
    _assert_server(instagram_server, min_tools=5)


def test_tiktok_server_structure() -> None:
    from app.mcp.servers import tiktok_server
    _assert_server(tiktok_server, min_tools=4)


def test_youtube_server_structure() -> None:
    from app.mcp.servers import youtube_server
    _assert_server(youtube_server, min_tools=6)


def test_youtube_server_tools() -> None:
    from app.mcp.servers.youtube_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "youtube_search" in names
    assert "youtube_get_video" in names
    assert "youtube_list_channel_videos" in names
    assert "youtube_get_channel" in names
    assert "youtube_list_playlists" in names


# ── AI & Search ───────────────────────────────────────────────────────────────

def test_openai_server_comprehensive() -> None:
    from app.mcp.servers.openai_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert len(names) >= 8
    assert "openai_chat_completion" in names
    assert "openai_create_embedding" in names
    assert "openai_generate_image" in names
    assert "openai_text_to_speech" in names
    assert "openai_speech_to_text" in names
    assert "openai_list_models" in names
    assert "openai_list_assistants" in names
    assert "openai_create_assistant" in names
    assert "openai_create_thread" in names
    assert "openai_fine_tune" in names


def test_openai_server_structure() -> None:
    from app.mcp.servers import openai_server
    _assert_server(openai_server, min_tools=10)


def test_perplexity_server_structure() -> None:
    from app.mcp.servers import perplexity_server
    _assert_server(perplexity_server, min_tools=2)


def test_tavily_server_structure() -> None:
    from app.mcp.servers import tavily_server
    _assert_server(tavily_server, min_tools=2)


def test_tavily_server_tools() -> None:
    from app.mcp.servers.tavily_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "tavily_search" in names
    assert "tavily_extract" in names
    assert "tavily_qna_search" in names


def test_serpapi_server_structure() -> None:
    from app.mcp.servers import serpapi_server
    _assert_server(serpapi_server, min_tools=4)


def test_brave_search_server_structure() -> None:
    from app.mcp.servers import brave_search_server
    _assert_server(brave_search_server, min_tools=3)


def test_firecrawl_server_structure() -> None:
    from app.mcp.servers import firecrawl_server
    _assert_server(firecrawl_server, min_tools=4)


def test_firecrawl_server_tools() -> None:
    from app.mcp.servers.firecrawl_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "firecrawl_scrape" in names
    assert "firecrawl_crawl" in names
    assert "firecrawl_map" in names


def test_ai_search_servers() -> None:
    from app.mcp.servers import perplexity_server, tavily_server, firecrawl_server
    for s in [perplexity_server, tavily_server, firecrawl_server]:
        assert hasattr(s, "TOOL_DEFINITIONS") and len(s.TOOL_DEFINITIONS) >= 2


# ── Communication & Scheduling ────────────────────────────────────────────────

def test_zoom_server_structure() -> None:
    from app.mcp.servers import zoom_server
    _assert_server(zoom_server, min_tools=6)


def test_zoom_server_tools() -> None:
    from app.mcp.servers.zoom_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "zoom_list_meetings" in names
    assert "zoom_create_meeting" in names
    assert "zoom_get_meeting" in names
    assert "zoom_update_meeting" in names
    assert "zoom_delete_meeting" in names
    assert "zoom_list_recordings" in names
    assert "zoom_list_users" in names


def test_calendly_server_structure() -> None:
    from app.mcp.servers import calendly_server
    _assert_server(calendly_server, min_tools=6)


def test_calendly_server_tools() -> None:
    from app.mcp.servers.calendly_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "calendly_get_user" in names
    assert "calendly_list_event_types" in names
    assert "calendly_list_scheduled_events" in names
    assert "calendly_get_event" in names
    assert "calendly_cancel_event" in names


def test_docusign_server_structure() -> None:
    from app.mcp.servers import docusign_server
    _assert_server(docusign_server, min_tools=5)


def test_docusign_server_tools() -> None:
    from app.mcp.servers.docusign_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "docusign_list_envelopes" in names
    assert "docusign_get_envelope" in names
    assert "docusign_create_envelope" in names
    assert "docusign_get_signing_url" in names


def test_pandadoc_server_structure() -> None:
    from app.mcp.servers import pandadoc_server
    _assert_server(pandadoc_server, min_tools=5)


def test_scheduling_servers() -> None:
    from app.mcp.servers import zoom_server, calendly_server, docusign_server
    for s in [zoom_server, calendly_server, docusign_server]:
        assert hasattr(s, "TOOL_DEFINITIONS") and len(s.TOOL_DEFINITIONS) >= 4


# ── Finance & Accounting ──────────────────────────────────────────────────────

def test_xero_server_structure() -> None:
    from app.mcp.servers import xero_server
    _assert_server(xero_server, min_tools=6)


def test_xero_server_tools() -> None:
    from app.mcp.servers.xero_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "xero_list_invoices" in names
    assert "xero_create_invoice" in names
    assert "xero_list_contacts" in names
    assert "xero_get_profit_loss" in names


def test_chargebee_server_structure() -> None:
    from app.mcp.servers import chargebee_server
    _assert_server(chargebee_server, min_tools=6)


def test_chargebee_server_tools() -> None:
    from app.mcp.servers.chargebee_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "chargebee_list_subscriptions" in names
    assert "chargebee_list_customers" in names
    assert "chargebee_list_invoices" in names
    assert "chargebee_cancel_subscription" in names


# ── Developer Tools ───────────────────────────────────────────────────────────

def test_notion_server_structure() -> None:
    from app.mcp.servers import notion_server
    _assert_server(notion_server, min_tools=7)


def test_notion_server_tools() -> None:
    from app.mcp.servers.notion_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "notion_search" in names
    assert "notion_get_page" in names
    assert "notion_create_page" in names
    assert "notion_query_database" in names
    assert "notion_append_blocks" in names


def test_postman_server_structure() -> None:
    from app.mcp.servers import postman_server
    _assert_server(postman_server, min_tools=7)


def test_postman_server_tools() -> None:
    from app.mcp.servers.postman_server import TOOL_DEFINITIONS
    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "postman_list_collections" in names
    assert "postman_get_collection" in names
    assert "postman_list_environments" in names
    assert "postman_run_monitor" in names


# ── Registry wiring ───────────────────────────────────────────────────────────

def test_registry_wiring_has_all_new_servers() -> None:
    """All new servers should appear in the registry wiring catalog."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs
    configs = get_builtin_server_configs()
    server_ids = {c["server_id"] for c in configs}
    expected_new = {
        "builtin-bamboohr",
        "builtin-workday",
        "builtin-deel",
        "builtin-rippling",
        "builtin-zendesk",
        "builtin-freshdesk",
        "builtin-freshservice",
        "builtin-gorgias",
        "builtin-front",
        "builtin-shopify",
        "builtin-wordpress",
        "builtin-webflow",
        "builtin-woocommerce",
        "builtin-x-twitter",
        "builtin-instagram",
        "builtin-tiktok",
        "builtin-youtube",
        "builtin-openai",
        "builtin-perplexity",
        "builtin-tavily",
        "builtin-serpapi",
        "builtin-brave-search",
        "builtin-firecrawl",
        "builtin-zoom",
        "builtin-calendly",
        "builtin-docusign",
        "builtin-pandadoc",
        "builtin-xero",
        "builtin-chargebee",
        "builtin-notion",
        "builtin-postman",
    }
    missing = expected_new - server_ids
    assert not missing, f"Missing server IDs in registry: {missing}"


def test_registry_wiring_total_count() -> None:
    """Registry should now have at least 60 server configs."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs
    configs = get_builtin_server_configs()
    assert len(configs) >= 60, (
        f"Expected at least 60 configs, got {len(configs)}"
    )


def test_all_registry_configs_have_handler() -> None:
    """Every config in the expanded registry must have a callable handler."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs
    for cfg in get_builtin_server_configs():
        assert callable(cfg["handler"]), f"{cfg['name']} handler not callable"


def test_all_registry_configs_have_tool_definitions() -> None:
    """Every config in the expanded registry must have non-empty tool_definitions."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs
    for cfg in get_builtin_server_configs():
        assert cfg["tool_definitions"], f"{cfg['name']} has no tool_definitions"
        for tdef in cfg["tool_definitions"]:
            assert "name" in tdef
            assert "description" in tdef
