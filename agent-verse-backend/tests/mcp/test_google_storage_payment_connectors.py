"""Tests for Google Workspace, Storage, and Payment MCP server connectors.

Verifies that:
  - All servers are importable and expose well-formed TOOL_DEFINITIONS
  - Key tools are present with correct names
  - call_tool returns a graceful error dict when credentials are missing
  - registry_wiring includes all new servers
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _assert_tool_schema(tool: dict) -> None:
    """Assert a single tool dict is well-formed."""
    assert "name" in tool, f"Tool missing 'name': {tool}"
    assert "description" in tool, f"Tool missing 'description': {tool}"
    assert "parameters" in tool, f"Tool missing 'parameters': {tool}"
    params = tool["parameters"]
    assert params.get("type") == "object", f"parameters.type must be 'object': {tool['name']}"


# ---------------------------------------------------------------------------
# Google Workspace
# ---------------------------------------------------------------------------

def test_google_workspace_importable() -> None:
    from app.mcp.servers import (
        google_calendar_server,
        google_docs_server,
        google_drive_server,
        google_sheets_server,
    )

    for srv in [
        google_sheets_server,
        google_docs_server,
        google_drive_server,
        google_calendar_server,
    ]:
        assert hasattr(srv, "TOOL_DEFINITIONS"), f"{srv.__name__} missing TOOL_DEFINITIONS"
        assert len(srv.TOOL_DEFINITIONS) >= 5, (
            f"{srv.__name__} should have >=5 tools, got {len(srv.TOOL_DEFINITIONS)}"
        )
        for tool in srv.TOOL_DEFINITIONS:
            _assert_tool_schema(tool)


def test_sheets_has_read_write_tools() -> None:
    from app.mcp.servers.google_sheets_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "sheets_read_range" in names
    assert "sheets_write_range" in names
    assert "sheets_append_rows" in names
    assert "sheets_clear_range" in names
    assert "sheets_list_sheets" in names
    assert "sheets_create_spreadsheet" in names
    assert "sheets_batch_update" in names
    assert "sheets_get_metadata" in names


def test_docs_has_required_tools() -> None:
    from app.mcp.servers.google_docs_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "docs_get_document" in names
    assert "docs_create_document" in names
    assert "docs_batch_update" in names
    assert "docs_export" in names


def test_drive_has_required_tools() -> None:
    from app.mcp.servers.google_drive_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "drive_list_files" in names
    assert "drive_get_file" in names
    assert "drive_upload_file" in names
    assert "drive_create_folder" in names
    assert "drive_share_file" in names
    assert "drive_delete_file" in names


def test_calendar_has_required_tools() -> None:
    from app.mcp.servers.google_calendar_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "calendar_list_events" in names
    assert "calendar_create_event" in names
    assert "calendar_update_event" in names
    assert "calendar_delete_event" in names
    assert "calendar_check_freebusy" in names
    assert "calendar_list_calendars" in names


# ---------------------------------------------------------------------------
# Google Analytics / Ads / Search Console / Cloud Storage
# ---------------------------------------------------------------------------

def test_google_extended_servers_importable() -> None:
    from app.mcp.servers import (
        google_ads_server,
        google_analytics_server,
        google_cloud_storage_server,
        google_search_console_server,
    )

    for srv in [
        google_analytics_server,
        google_ads_server,
        google_search_console_server,
        google_cloud_storage_server,
    ]:
        assert hasattr(srv, "TOOL_DEFINITIONS"), f"{srv.__name__} missing TOOL_DEFINITIONS"
        assert len(srv.TOOL_DEFINITIONS) >= 5
        for tool in srv.TOOL_DEFINITIONS:
            _assert_tool_schema(tool)


def test_ga4_has_required_tools() -> None:
    from app.mcp.servers.google_analytics_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "ga4_run_report" in names
    assert "ga4_run_realtime_report" in names
    assert "ga4_get_metadata" in names


def test_gcs_has_required_tools() -> None:
    from app.mcp.servers.google_cloud_storage_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "gcs_list_buckets" in names
    assert "gcs_upload_object" in names
    assert "gcs_download_object" in names
    assert "gcs_delete_object" in names


# ---------------------------------------------------------------------------
# Stripe (comprehensive)
# ---------------------------------------------------------------------------

def test_stripe_comprehensive() -> None:
    from app.mcp.servers.stripe_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert len(names) >= 10, f"Expected >=10 Stripe tools, got {len(names)}"
    # Customers
    assert "stripe_list_customers" in names
    assert "stripe_create_customer" in names
    # Payments
    assert "stripe_create_payment_intent" in names
    assert "stripe_confirm_payment_intent" in names
    # Subscriptions
    assert "stripe_list_subscriptions" in names
    assert "stripe_create_subscription" in names
    assert "stripe_cancel_subscription" in names
    # Billing
    assert "stripe_create_refund" in names
    assert "stripe_list_invoices" in names
    assert "stripe_list_products" in names
    assert "stripe_create_product" in names
    assert "stripe_list_prices" in names
    assert "stripe_create_price" in names
    assert "stripe_list_charges" in names
    assert "stripe_create_payout" in names


def test_stripe_tools_well_formed() -> None:
    from app.mcp.servers.stripe_server import TOOL_DEFINITIONS

    for tool in TOOL_DEFINITIONS:
        _assert_tool_schema(tool)


# ---------------------------------------------------------------------------
# Payment servers importable + sufficient tools
# ---------------------------------------------------------------------------

def test_payment_servers_importable() -> None:
    from app.mcp.servers import (
        paypal_server,
        quickbooks_server,
        razorpay_server,
        square_server,
        stripe_server,
        xero_server,
    )

    for srv in [stripe_server, paypal_server, square_server, quickbooks_server, razorpay_server, xero_server]:
        assert hasattr(srv, "TOOL_DEFINITIONS"), f"{srv.__name__} missing TOOL_DEFINITIONS"
        assert len(srv.TOOL_DEFINITIONS) >= 5, (
            f"{srv.__name__} should have >=5 tools, got {len(srv.TOOL_DEFINITIONS)}"
        )
        for tool in srv.TOOL_DEFINITIONS:
            _assert_tool_schema(tool)


def test_paypal_has_required_tools() -> None:
    from app.mcp.servers.paypal_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "paypal_create_order" in names
    assert "paypal_capture_order" in names
    assert "paypal_get_order" in names
    assert "paypal_create_payout" in names
    assert "paypal_list_transactions" in names


def test_quickbooks_has_required_tools() -> None:
    from app.mcp.servers.quickbooks_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "qb_query" in names
    assert "qb_create_invoice" in names
    assert "qb_create_customer" in names
    assert "qb_list_accounts" in names
    assert "qb_create_payment" in names
    assert "qb_list_vendors" in names


def test_razorpay_has_required_tools() -> None:
    from app.mcp.servers.razorpay_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "razorpay_create_order" in names
    assert "razorpay_list_payments" in names
    assert "razorpay_capture_payment" in names
    assert "razorpay_create_refund" in names


def test_xero_has_required_tools() -> None:
    from app.mcp.servers.xero_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "xero_list_invoices" in names
    assert "xero_create_invoice" in names
    assert "xero_list_contacts" in names
    assert "xero_create_payment" in names
    assert "xero_list_accounts" in names


# ---------------------------------------------------------------------------
# Cloud Storage / File sync servers
# ---------------------------------------------------------------------------

def test_file_storage_servers_importable() -> None:
    from app.mcp.servers import (
        box_server,
        dropbox_server,
        microsoft_onedrive_server,
    )

    for srv in [dropbox_server, box_server, microsoft_onedrive_server]:
        assert hasattr(srv, "TOOL_DEFINITIONS"), f"{srv.__name__} missing TOOL_DEFINITIONS"
        assert len(srv.TOOL_DEFINITIONS) >= 5, (
            f"{srv.__name__} should have >=5 tools, got {len(srv.TOOL_DEFINITIONS)}"
        )
        for tool in srv.TOOL_DEFINITIONS:
            _assert_tool_schema(tool)


def test_dropbox_has_required_tools() -> None:
    from app.mcp.servers.dropbox_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "dropbox_list_folder" in names
    assert "dropbox_upload" in names
    assert "dropbox_download" in names
    assert "dropbox_delete" in names
    assert "dropbox_create_folder" in names
    assert "dropbox_search" in names
    assert "dropbox_share_link" in names


def test_box_has_required_tools() -> None:
    from app.mcp.servers.box_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "box_list_folder" in names
    assert "box_upload_file" in names
    assert "box_download_file" in names
    assert "box_delete_file" in names
    assert "box_create_folder" in names
    assert "box_search" in names
    assert "box_create_shared_link" in names


def test_onedrive_has_required_tools() -> None:
    from app.mcp.servers.microsoft_onedrive_server import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "onedrive_list_root" in names
    assert "onedrive_upload_file" in names
    assert "onedrive_download_file" in names
    assert "onedrive_delete_item" in names
    assert "onedrive_create_folder" in names
    assert "onedrive_search" in names
    assert "onedrive_share_item" in names


# ---------------------------------------------------------------------------
# call_tool returns graceful errors without credentials
# ---------------------------------------------------------------------------

import asyncio


def _run(coro):  # noqa: D103
    return asyncio.run(coro)


def test_sheets_call_tool_no_creds(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_JSON", raising=False)
    from app.mcp.servers.google_sheets_server import call_tool

    result = _run(call_tool("sheets_read_range", {"spreadsheet_id": "x", "range": "A1"}))
    assert "error" in result


def test_stripe_call_tool_no_creds(monkeypatch) -> None:
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    from app.mcp.servers.stripe_server import call_tool

    result = _run(call_tool("stripe_list_customers", {}))
    assert "error" in result


def test_paypal_call_tool_no_creds(monkeypatch) -> None:
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    from app.mcp.servers.paypal_server import call_tool

    result = _run(call_tool("paypal_get_order", {"order_id": "x"}))
    assert "error" in result


def test_dropbox_call_tool_no_creds(monkeypatch) -> None:
    monkeypatch.delenv("DROPBOX_ACCESS_TOKEN", raising=False)
    from app.mcp.servers.dropbox_server import call_tool

    result = _run(call_tool("dropbox_list_folder", {}))
    assert "error" in result


# ---------------------------------------------------------------------------
# Registry wiring includes all new servers
# ---------------------------------------------------------------------------

def test_registry_wiring_count() -> None:
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    # Expect at minimum the original 3 + 17 new = 20
    assert len(configs) >= 20, f"Expected >=20 server configs, got {len(configs)}"


def test_registry_wiring_has_google_servers() -> None:
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    ids = {c["server_id"] for c in get_builtin_server_configs()}
    assert "builtin-google-sheets" in ids
    assert "builtin-google-docs" in ids
    assert "builtin-google-drive" in ids
    assert "builtin-google-calendar" in ids
    assert "builtin-google-analytics" in ids
    assert "builtin-google-ads" in ids
    assert "builtin-google-search-console" in ids
    assert "builtin-google-cloud-storage" in ids


def test_registry_wiring_has_payment_servers() -> None:
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    ids = {c["server_id"] for c in get_builtin_server_configs()}
    assert "builtin-stripe" in ids
    assert "builtin-paypal" in ids
    assert "builtin-square" in ids
    assert "builtin-razorpay" in ids
    assert "builtin-quickbooks" in ids
    assert "builtin-xero" in ids


def test_registry_wiring_has_storage_servers() -> None:
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    ids = {c["server_id"] for c in get_builtin_server_configs()}
    assert "builtin-dropbox" in ids
    assert "builtin-box" in ids
    assert "builtin-onedrive" in ids


def test_registry_wiring_all_have_handlers() -> None:
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    for cfg in get_builtin_server_configs():
        assert callable(cfg["handler"]), f"{cfg['name']} handler is not callable"
        assert cfg["tool_definitions"], f"{cfg['name']} has no tool_definitions"
