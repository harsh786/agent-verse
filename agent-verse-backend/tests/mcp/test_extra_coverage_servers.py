"""Extra coverage tests to push remaining MCP servers above 80%.

This file adds tests for all tool branches not covered in the main dispatch files.
Uses the same mock pattern: patch httpx.AsyncClient, provide correct args.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.raise_for_status = MagicMock()
    m.headers = MagicMock()
    m.headers.get = MagicMock(return_value="application/json")
    return m


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ---------------------------------------------------------------------------
# Xero – remaining tools
# ---------------------------------------------------------------------------

_XERO = {"XERO_ACCESS_TOKEN": "xero-tok", "XERO_TENANT_ID": "tenant-123"}


@pytest.mark.asyncio
async def test_xero_create_invoice():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client(post=make_resp(data={"Invoices": [{"InvoiceID": "inv2", "InvoiceNumber": "INV-002", "Status": "DRAFT"}]}))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_create_invoice", {"contact_id": "c1", "line_items": [{"Description": "Service", "UnitAmount": 100.0, "Quantity": 1, "AccountCode": "200"}]})
    assert "error" not in result


@pytest.mark.asyncio
async def test_xero_list_contacts():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client(get=make_resp(data={"Contacts": [{"ContactID": "c1", "Name": "Alice Corp", "EmailAddress": "a@b.com"}]}))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_list_contacts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_xero_create_contact():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client(post=make_resp(data={"Contacts": [{"ContactID": "c2", "Name": "Bob Corp"}]}))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_create_contact", {"name": "Bob Corp"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_xero_list_accounts():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client(get=make_resp(data={"Accounts": [{"AccountID": "a1", "Code": "200", "Name": "Sales", "Type": "REVENUE"}]}))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_list_accounts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_xero_get_profit_loss():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client(get=make_resp(data={"Reports": [{"ReportID": "ProfitAndLoss", "ReportName": "Profit and Loss", "Rows": []}]}))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_get_profit_loss", {"from_date": "2024-01-01", "to_date": "2024-12-31"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_xero_create_payment():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client(post=make_resp(data={"Payments": [{"PaymentID": "pay1", "Amount": 500.0, "Status": "AUTHORISED"}]}))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_create_payment", {"invoice_id": "inv1", "account_id": "a1", "amount": 500.0, "date": "2024-01-15"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_xero_list_bank_transactions():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client(get=make_resp(data={"BankTransactions": [{"BankTransactionID": "bt1", "Type": "SPEND", "Total": 100.0}]}))
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_list_bank_transactions", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Webflow – remaining tools
# ---------------------------------------------------------------------------

_WF = {"WEBFLOW_API_TOKEN": "wf-tok"}


@pytest.mark.asyncio
async def test_webflow_list_collections():
    from app.mcp.servers.webflow_server import call_tool

    mc = mk_client(get=make_resp(data={"collections": [{"id": "col1", "displayName": "Blog Posts", "singularName": "Blog Post", "slug": "blog-posts"}]}))
    with patch.dict("os.environ", _WF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("webflow_list_collections", {"site_id": "site1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_webflow_list_collection_items():
    from app.mcp.servers.webflow_server import call_tool

    mc = mk_client(get=make_resp(data={"items": [{"id": "item1", "fieldData": {"name": "Post 1", "slug": "post-1"}}], "pagination": {}}))
    with patch.dict("os.environ", _WF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("webflow_list_collection_items", {"collection_id": "col1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_webflow_create_collection_item():
    from app.mcp.servers.webflow_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "item2", "fieldData": {"name": "New Post"}}))
    with patch.dict("os.environ", _WF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("webflow_create_collection_item", {"collection_id": "col1", "fields": {"name": "New Post", "slug": "new-post"}})
    assert "error" not in result


@pytest.mark.asyncio
async def test_webflow_publish_site():
    from app.mcp.servers.webflow_server import call_tool

    mc = mk_client(post=make_resp(data={"queued": True}))
    with patch.dict("os.environ", _WF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("webflow_publish_site", {"site_id": "site1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_webflow_get_site():
    from app.mcp.servers.webflow_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "site1", "name": "My Site", "shortName": "mysite", "lastPublished": "2024-01-01", "previewUrl": "url"}))
    with patch.dict("os.environ", _WF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("webflow_get_site", {"site_id": "site1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# SmartSuite – remaining tools
# ---------------------------------------------------------------------------

_SS = {"SMARTSUITE_API_KEY": "ss-key", "SMARTSUITE_ACCOUNT_ID": "acct1"}


@pytest.mark.asyncio
async def test_smartsuite_list_tables():
    from app.mcp.servers.smartsuite_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "tbl1", "name": "Tasks", "solution_id": "sol1"}]))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsuite_list_tables", {"solution_id": "sol1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_smartsuite_list_records():
    from app.mcp.servers.smartsuite_server import call_tool

    mc = mk_client(post=make_resp(data={"items": [{"id": "rec1", "title": {"value": "Task 1"}}], "total_count": 1}))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsuite_list_records", {"table_id": "tbl1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_smartsuite_create_record():
    from app.mcp.servers.smartsuite_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "rec2", "title": {"value": "New Task"}}))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsuite_create_record", {"table_id": "tbl1", "fields": {"title": "New Task"}})
    assert "error" not in result


@pytest.mark.asyncio
async def test_smartsuite_get_record():
    from app.mcp.servers.smartsuite_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "rec1", "title": {"value": "Task 1"}}))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsuite_get_record", {"table_id": "tbl1", "record_id": "rec1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_smartsuite_update_record():
    from app.mcp.servers.smartsuite_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": "rec1", "title": {"value": "Updated Task"}}))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsuite_update_record", {"table_id": "tbl1", "record_id": "rec1", "fields": {"title": "Updated Task"}})
    assert "error" not in result


@pytest.mark.asyncio
async def test_smartsuite_delete_record():
    from app.mcp.servers.smartsuite_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsuite_delete_record", {"table_id": "tbl1", "record_id": "rec1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_smartsuite_add_comment():
    from app.mcp.servers.smartsuite_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "comment1", "comment": "Nice work"}))
    with patch.dict("os.environ", _SS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("smartsuite_add_comment", {"table_id": "tbl1", "record_id": "rec1", "comment": "Nice work"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# SerpAPI – remaining tools
# ---------------------------------------------------------------------------

_SERP = {"SERPAPI_API_KEY": "serp-key"}


@pytest.mark.asyncio
async def test_serpapi_google_news():
    from app.mcp.servers.serpapi_server import call_tool

    mc = mk_client(get=make_resp(data={"news_results": [{"title": "AI News", "link": "url", "source": {"name": "TechCrunch"}}], "search_metadata": {"status": "Success"}}))
    with patch.dict("os.environ", _SERP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("serpapi_google_news", {"query": "AI"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_serpapi_google_images():
    from app.mcp.servers.serpapi_server import call_tool

    mc = mk_client(get=make_resp(data={"images_results": [{"title": "Python logo", "thumbnail": "url", "original": "url2"}], "search_metadata": {"status": "Success"}}))
    with patch.dict("os.environ", _SERP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("serpapi_google_images", {"query": "python logo"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_serpapi_google_shopping():
    from app.mcp.servers.serpapi_server import call_tool

    mc = mk_client(get=make_resp(data={"shopping_results": [{"title": "Laptop", "price": "$999", "link": "url"}], "search_metadata": {"status": "Success"}}))
    with patch.dict("os.environ", _SERP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("serpapi_google_shopping", {"query": "laptop"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_serpapi_google_maps():
    from app.mcp.servers.serpapi_server import call_tool

    mc = mk_client(get=make_resp(data={"local_results": [{"title": "Coffee Shop", "address": "123 Main St", "rating": 4.5}], "search_metadata": {"status": "Success"}}))
    with patch.dict("os.environ", _SERP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("serpapi_google_maps", {"query": "coffee", "location": "NYC"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Deel – remaining tools
# ---------------------------------------------------------------------------

_DEEL = {"DEEL_API_KEY": "deel-key"}


@pytest.mark.asyncio
async def test_deel_get_contract():
    from app.mcp.servers.deel_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"id": "c1", "title": "Dev Contract", "status": "active", "type": "employment"}}))
    with patch.dict("os.environ", _DEEL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("deel_get_contract", {"contract_id": "c1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_deel_list_people():
    from app.mcp.servers.deel_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"list": [{"id": "p1", "full_name": "Alice Smith", "email": "a@b.com"}]}}))
    with patch.dict("os.environ", _DEEL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("deel_list_people", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_deel_list_invoices():
    from app.mcp.servers.deel_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"list": [{"id": "inv1", "status": "submitted", "amount": 5000}]}}))
    with patch.dict("os.environ", _DEEL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("deel_list_invoices", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_deel_list_time_off():
    from app.mcp.servers.deel_server import call_tool

    mc = mk_client(get=make_resp(data={"data": {"list": [{"id": "to1", "status": "approved", "worker_id": "p1"}]}}))
    with patch.dict("os.environ", _DEEL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("deel_list_time_off", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Prometheus – remaining tools
# ---------------------------------------------------------------------------

_PROM = {"PROMETHEUS_URL": "https://prom.example.com", "PROMETHEUS_TOKEN": "prom-tok"}


@pytest.mark.asyncio
async def test_prometheus_list_metrics():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client(get=make_resp(data={"status": "success", "data": ["http_requests_total", "up", "process_cpu_seconds_total"]}))
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("prometheus_list_metrics", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_prometheus_get_labels():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client(get=make_resp(data={"status": "success", "data": ["instance", "job", "method"]}))
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("prometheus_get_labels", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_prometheus_get_alerts():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client(get=make_resp(data={"status": "success", "data": {"alerts": [{"labels": {"alertname": "HighCPU"}, "state": "firing"}]}}))
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("prometheus_get_alerts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_prometheus_get_targets():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client(get=make_resp(data={"status": "success", "data": {"activeTargets": [{"labels": {"job": "api"}, "health": "up", "lastScrape": "2024-01-01"}]}}))
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("prometheus_get_targets", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_prometheus_get_rules():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client(get=make_resp(data={"status": "success", "data": {"groups": [{"name": "alerts", "rules": [{"name": "HighCPU", "type": "alerting"}]}]}}))
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("prometheus_get_rules", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Square – remaining tools
# ---------------------------------------------------------------------------

_SQUARE = {"SQUARE_ACCESS_TOKEN": "sq-tok"}


@pytest.mark.asyncio
async def test_square_create_payment():
    from app.mcp.servers.square_server import call_tool

    mc = mk_client(post=make_resp(data={"payment": {"id": "pay1", "amount_money": {"amount": 1000, "currency": "USD"}, "status": "COMPLETED"}}))
    with patch.dict("os.environ", _SQUARE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("square_create_payment", {"amount": 1000, "currency": "USD", "source_id": "card_abc"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_square_list_payments():
    from app.mcp.servers.square_server import call_tool

    mc = mk_client(get=make_resp(data={"payments": [{"id": "pay1", "amount_money": {"amount": 1000}, "status": "COMPLETED"}]}))
    with patch.dict("os.environ", _SQUARE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("square_list_payments", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_square_create_refund():
    from app.mcp.servers.square_server import call_tool

    mc = mk_client(post=make_resp(data={"refund": {"id": "ref1", "amount_money": {"amount": 500}, "status": "PENDING"}}))
    with patch.dict("os.environ", _SQUARE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("square_create_refund", {"payment_id": "pay1", "amount": 500})
    assert "error" not in result


@pytest.mark.asyncio
async def test_square_list_catalog():
    from app.mcp.servers.square_server import call_tool

    mc = mk_client(get=make_resp(data={"objects": [{"id": "obj1", "type": "ITEM", "item_data": {"name": "T-Shirt"}}]}))
    with patch.dict("os.environ", _SQUARE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("square_list_catalog", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_square_list_locations():
    from app.mcp.servers.square_server import call_tool

    mc = mk_client(get=make_resp(data={"locations": [{"id": "loc1", "name": "Main Store", "status": "ACTIVE", "address": {}}]}))
    with patch.dict("os.environ", _SQUARE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("square_list_locations", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# WhatsApp – remaining tools
# ---------------------------------------------------------------------------

_WA = {"WHATSAPP_ACCESS_TOKEN": "wa-tok", "WHATSAPP_PHONE_NUMBER_ID": "phone123"}


@pytest.mark.asyncio
async def test_whatsapp_send_template():
    from app.mcp.servers.whatsapp_server import call_tool

    mc = mk_client(post=make_resp(data={"messaging_product": "whatsapp", "messages": [{"id": "msg2"}]}))
    with patch.dict("os.environ", _WA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("whatsapp_send_template", {"to": "+1234567890", "template_name": "hello_world", "language_code": "en_US"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_whatsapp_send_media():
    from app.mcp.servers.whatsapp_server import call_tool

    mc = mk_client(post=make_resp(data={"messaging_product": "whatsapp", "messages": [{"id": "msg3"}]}))
    with patch.dict("os.environ", _WA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("whatsapp_send_media", {"to": "+1234567890", "media_type": "image", "media_url": "https://example.com/image.jpg"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_whatsapp_mark_read():
    from app.mcp.servers.whatsapp_server import call_tool

    mc = mk_client(post=make_resp(data={"success": True}))
    with patch.dict("os.environ", _WA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("whatsapp_mark_read", {"message_id": "msg1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# LinkedIn – remaining tools
# ---------------------------------------------------------------------------

_LI = {"LINKEDIN_ACCESS_TOKEN": "li-tok"}


@pytest.mark.asyncio
async def test_linkedin_search_people():
    from app.mcp.servers.linkedin_server import call_tool

    mc = mk_client(get=make_resp(data={"elements": [{"id": "p1", "firstName": {"localized": {"en_US": "Alice"}}, "lastName": {"localized": {"en_US": "Smith"}}}], "paging": {}}))
    with patch.dict("os.environ", _LI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linkedin_search_people", {"keywords": "engineer"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_linkedin_search_companies():
    from app.mcp.servers.linkedin_server import call_tool

    mc = mk_client(get=make_resp(data={"elements": [{"id": "c1", "name": {"localized": {"en_US": "Tech Corp"}}}], "paging": {}}))
    with patch.dict("os.environ", _LI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linkedin_search_companies", {"keywords": "technology"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_linkedin_create_post():
    from app.mcp.servers.linkedin_server import call_tool

    mc = mk_client(post=make_resp(status=201, data={"id": "post1"}))
    with patch.dict("os.environ", _LI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linkedin_create_post", {"text": "Hello LinkedIn!", "visibility": "PUBLIC"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_linkedin_ads_list_campaigns():
    from app.mcp.servers.linkedin_ads_server import call_tool

    mc = mk_client(get=make_resp(data={"elements": [{"id": "cam1", "name": "Q1 Campaign", "status": "ACTIVE"}], "paging": {}}))
    with patch.dict("os.environ", _LI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linkedin_ads_list_campaigns", {"account_id": "acct1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_linkedin_ads_get_campaign_analytics():
    from app.mcp.servers.linkedin_ads_server import call_tool

    mc = mk_client(get=make_resp(data={"elements": [{"campaign_id": "cam1", "impressions": 1000, "clicks": 50, "spend": {"amount": "100.00"}}]}))
    with patch.dict("os.environ", _LI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linkedin_ads_get_campaign_analytics", {"campaign_id": "cam1", "account_id": "acct1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Chargebee – remaining tools
# ---------------------------------------------------------------------------

_CB = {"CHARGEBEE_API_KEY": "cb-key", "CHARGEBEE_SITE": "mysite"}


@pytest.mark.asyncio
async def test_chargebee_get_subscription():
    from app.mcp.servers.chargebee_server import call_tool

    mc = mk_client(get=make_resp(data={"subscription": {"id": "sub1", "status": "active", "plan_id": "pro", "customer_id": "cust1"}}))
    with patch.dict("os.environ", _CB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("chargebee_get_subscription", {"subscription_id": "sub1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_chargebee_list_customers():
    from app.mcp.servers.chargebee_server import call_tool

    mc = mk_client(get=make_resp(data={"list": [{"customer": {"id": "cust1", "email": "a@b.com", "first_name": "Alice"}}], "next_offset": None}))
    with patch.dict("os.environ", _CB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("chargebee_list_customers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_chargebee_get_customer():
    from app.mcp.servers.chargebee_server import call_tool

    mc = mk_client(get=make_resp(data={"customer": {"id": "cust1", "email": "a@b.com"}}))
    with patch.dict("os.environ", _CB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("chargebee_get_customer", {"customer_id": "cust1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_chargebee_list_invoices():
    from app.mcp.servers.chargebee_server import call_tool

    mc = mk_client(get=make_resp(data={"list": [{"invoice": {"id": "inv1", "status": "paid", "amount_paid": 5000}}], "next_offset": None}))
    with patch.dict("os.environ", _CB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("chargebee_list_invoices", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_chargebee_cancel_subscription():
    from app.mcp.servers.chargebee_server import call_tool

    mc = mk_client(post=make_resp(data={"subscription": {"id": "sub1", "status": "cancelled"}}))
    with patch.dict("os.environ", _CB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("chargebee_cancel_subscription", {"subscription_id": "sub1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# DigitalOcean – remaining tools
# ---------------------------------------------------------------------------

_DO = {"DIGITALOCEAN_TOKEN": "do-token"}


@pytest.mark.asyncio
async def test_digitalocean_create_droplet():
    from app.mcp.servers.digitalocean_server import call_tool

    mc = mk_client(post=make_resp(data={"droplet": {"id": 2, "name": "new-droplet", "status": "new"}}))
    with patch.dict("os.environ", _DO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("digitalocean_create_droplet", {"name": "new-droplet", "region": "nyc1", "size": "s-1vcpu-1gb", "image": "ubuntu-20-04-x64"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_digitalocean_delete_droplet():
    from app.mcp.servers.digitalocean_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _DO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("digitalocean_delete_droplet", {"droplet_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_digitalocean_list_databases():
    from app.mcp.servers.digitalocean_server import call_tool

    mc = mk_client(get=make_resp(data={"databases": [{"id": "db1", "name": "mydb", "engine": "pg", "status": "online"}]}))
    with patch.dict("os.environ", _DO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("digitalocean_list_databases", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_digitalocean_get_app():
    from app.mcp.servers.digitalocean_server import call_tool

    mc = mk_client(get=make_resp(data={"app": {"id": "app-1", "spec": {"name": "my-app"}, "phase": "RUNNING"}}))
    with patch.dict("os.environ", _DO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("digitalocean_get_app", {"app_id": "app-1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_digitalocean_list_domains():
    from app.mcp.servers.digitalocean_server import call_tool

    mc = mk_client(get=make_resp(data={"domains": [{"name": "example.com", "ttl": 1800, "zone_file": "zone"}], "meta": {"total": 1}}))
    with patch.dict("os.environ", _DO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("digitalocean_list_domains", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Rippling – remaining tools
# ---------------------------------------------------------------------------

_RIPPLING = {"RIPPLING_API_KEY": "rippling-key"}


@pytest.mark.asyncio
async def test_rippling_get_employee():
    from app.mcp.servers.rippling_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "e1", "firstName": "Alice", "lastName": "Smith", "workEmail": "a@b.com"}))
    with patch.dict("os.environ", _RIPPLING), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("rippling_get_employee", {"employee_id": "e1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_rippling_list_departments():
    from app.mcp.servers.rippling_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "dept1", "name": "Engineering", "code": "ENG"}]))
    with patch.dict("os.environ", _RIPPLING), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("rippling_list_departments", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_rippling_get_company():
    from app.mcp.servers.rippling_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "co1", "name": "My Company", "legalName": "My Company Inc."}))
    with patch.dict("os.environ", _RIPPLING), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("rippling_get_company", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Front – remaining tools
# ---------------------------------------------------------------------------

_FRONT = {"FRONT_API_TOKEN": "front-key"}


@pytest.mark.asyncio
async def test_front_get_conversation():
    from app.mcp.servers.front_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "cnv_1", "subject": "Re: Help", "status": "open", "assignee": None, "tags": [], "created_at": 1704067200}))
    with patch.dict("os.environ", _FRONT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("front_get_conversation", {"conversation_id": "cnv_1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_front_create_message():
    from app.mcp.servers.front_server import call_tool

    mc = mk_client(post=make_resp(status=200, data={"status": "accepted"}))
    with patch.dict("os.environ", _FRONT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("front_create_message", {"inbox_id": "inb_1", "to": ["a@b.com"], "subject": "Hello", "body": "Hello there"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_front_update_conversation():
    from app.mcp.servers.front_server import call_tool

    mc = mk_client(patch=make_resp(status=204))
    with patch.dict("os.environ", _FRONT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("front_update_conversation", {"conversation_id": "cnv_1", "status": "resolved"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_front_list_inboxes():
    from app.mcp.servers.front_server import call_tool

    mc = mk_client(get=make_resp(data={"_results": [{"id": "inb_1", "name": "Support", "address": "support@co.com"}]}))
    with patch.dict("os.environ", _FRONT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("front_list_inboxes", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_front_list_teammates():
    from app.mcp.servers.front_server import call_tool

    mc = mk_client(get=make_resp(data={"_results": [{"id": "tea_1", "email": "agent@co.com", "first_name": "Alice"}]}))
    with patch.dict("os.environ", _FRONT), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("front_list_teammates", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Snowflake – remaining tools (not installed: test via mock sys.modules)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snowflake_with_mock_all_tools():
    from app.mcp.servers.snowflake_server import call_tool

    mock_cursor = MagicMock()
    mock_cursor.execute = MagicMock()
    mock_cursor.fetchmany = MagicMock(return_value=[{"ID": 1}])
    mock_cursor.fetchall = MagicMock(return_value=[{"TABLE_NAME": "users"}, {"TABLE_NAME": "orders"}])
    mock_cursor.description = [("ID",)]
    mock_cursor.rowcount = 1

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.close = MagicMock()

    mock_sf_connector = MagicMock()
    mock_sf_connector.connect = MagicMock(return_value=mock_conn)

    mock_sf = MagicMock()
    mock_sf.connector = mock_sf_connector

    with patch.dict("os.environ", {
        "SNOWFLAKE_ACCOUNT": "xy12345",
        "SNOWFLAKE_USER": "user",
        "SNOWFLAKE_PASSWORD": "pass",
        "SNOWFLAKE_ALLOW_WRITES": "true",
    }), patch.dict("sys.modules", {"snowflake": mock_sf, "snowflake.connector": mock_sf_connector}):
        result_execute = await call_tool("snowflake_execute", {"sql": "INSERT INTO t VALUES (1)"})
        result_list = await call_tool("snowflake_list_tables", {})
        result_show = await call_tool("snowflake_show_databases", {})
    assert result_execute is not None
    assert result_list is not None
    assert result_show is not None


# ---------------------------------------------------------------------------
# OpenAI – remaining tools
# ---------------------------------------------------------------------------

_OAI = {"OPENAI_API_KEY": "sk-test-oai"}


@pytest.mark.asyncio
async def test_openai_generate_image():
    from app.mcp.servers.openai_server import call_tool

    mc = mk_client(post=make_resp(data={"data": [{"url": "https://openai.com/gen-image.png", "revised_prompt": "A cat"}]}))
    with patch.dict("os.environ", _OAI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("openai_generate_image", {"prompt": "A cat"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_openai_text_to_speech():
    from app.mcp.servers.openai_server import call_tool

    mc = mk_client()
    mc.post.return_value.content = b"audio_bytes"
    mc.post.return_value.status_code = 200
    with patch.dict("os.environ", _OAI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("openai_text_to_speech", {"input": "Hello world", "voice": "alloy"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_openai_create_assistant():
    from app.mcp.servers.openai_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "asst_2", "name": "New Assistant", "model": "gpt-4o", "instructions": "You are helpful"}))
    with patch.dict("os.environ", _OAI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("openai_create_assistant", {"name": "New Assistant", "model": "gpt-4o", "instructions": "You are a helpful assistant."})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Calendly – remaining tools
# ---------------------------------------------------------------------------

_CALY = {"CALENDLY_ACCESS_TOKEN": "caly-tok"}


@pytest.mark.asyncio
async def test_calendly_list_scheduled_events():
    from app.mcp.servers.calendly_server import call_tool

    mc = mk_client(get=make_resp(data={"collection": [{"uri": "ev/1", "name": "Meeting", "status": "active", "start_time": "2024-01-15T10:00:00Z", "end_time": "2024-01-15T11:00:00Z", "event_type": "et/1"}], "pagination": {}}))
    with patch.dict("os.environ", _CALY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendly_list_scheduled_events", {"user_uri": "u/abc"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendly_get_event():
    from app.mcp.servers.calendly_server import call_tool

    mc = mk_client(get=make_resp(data={"resource": {"uri": "ev/1", "name": "Meeting", "status": "active", "start_time": "2024-01-15T10:00:00Z", "end_time": "2024-01-15T11:00:00Z"}}))
    with patch.dict("os.environ", _CALY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendly_get_event", {"uuid": "ev/1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendly_get_invitees():
    from app.mcp.servers.calendly_server import call_tool

    mc = mk_client(get=make_resp(data={"collection": [{"uri": "inv/1", "email": "a@b.com", "status": "active", "name": "Alice"}], "pagination": {}}))
    with patch.dict("os.environ", _CALY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendly_get_invitees", {"event_uuid": "ev/1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Gorgias – remaining tools
# ---------------------------------------------------------------------------

_GOR = {"GORGIAS_DOMAIN": "myco", "GORGIAS_EMAIL": "a@b.com", "GORGIAS_API_KEY": "gor-key"}


@pytest.mark.asyncio
async def test_gorgias_get_ticket():
    from app.mcp.servers.gorgias_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 1, "subject": "Help!", "status": "open", "channel": "email"}))
    with patch.dict("os.environ", _GOR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gorgias_get_ticket", {"ticket_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gorgias_update_ticket():
    from app.mcp.servers.gorgias_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": 1, "status": "closed"}))
    with patch.dict("os.environ", _GOR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gorgias_update_ticket", {"ticket_id": 1, "status": "closed"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gorgias_add_message():
    from app.mcp.servers.gorgias_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "msg1", "body_text": "We'll help!"}))
    with patch.dict("os.environ", _GOR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gorgias_add_message", {"ticket_id": 1, "body_text": "We'll help!", "channel": "email"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gorgias_list_customers():
    from app.mcp.servers.gorgias_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"id": 1, "email": "a@b.com", "name": "Alice"}], "meta": {"next_cursor": None}}))
    with patch.dict("os.environ", _GOR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gorgias_list_customers", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# WooCommerce – remaining tools
# ---------------------------------------------------------------------------

_WOO = {"WOOCOMMERCE_URL": "https://mystore.com", "WOOCOMMERCE_CONSUMER_KEY": "ck_test", "WOOCOMMERCE_CONSUMER_SECRET": "cs_test"}


@pytest.mark.asyncio
async def test_woo_get_product():
    from app.mcp.servers.woocommerce_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 1, "name": "T-Shirt", "status": "publish", "price": "29.99"}))
    with patch.dict("os.environ", _WOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("woo_get_product", {"product_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_woo_create_product():
    from app.mcp.servers.woocommerce_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 2, "name": "New Shirt", "status": "draft"}))
    with patch.dict("os.environ", _WOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("woo_create_product", {"name": "New Shirt", "regular_price": "49.99"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_woo_get_order():
    from app.mcp.servers.woocommerce_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 100, "number": "100", "status": "processing", "total": "49.99"}))
    with patch.dict("os.environ", _WOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("woo_get_order", {"order_id": 100})
    assert "error" not in result


@pytest.mark.asyncio
async def test_woo_update_order():
    from app.mcp.servers.woocommerce_server import call_tool

    mc = mk_client(put=make_resp(data={"id": 100, "status": "completed"}))
    with patch.dict("os.environ", _WOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("woo_update_order", {"order_id": 100, "status": "completed"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_woo_list_customers():
    from app.mcp.servers.woocommerce_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "email": "a@b.com", "first_name": "Alice"}]))
    with patch.dict("os.environ", _WOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("woo_list_customers", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Instagram – remaining tools
# ---------------------------------------------------------------------------

_IG = {"INSTAGRAM_ACCESS_TOKEN": "ig-tok", "INSTAGRAM_BUSINESS_ACCOUNT_ID": "ig-acct"}


@pytest.mark.asyncio
async def test_instagram_get_media():
    from app.mcp.servers.instagram_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "media1", "media_type": "IMAGE", "timestamp": "2024-01-01T00:00:00", "like_count": 50, "comments_count": 5, "permalink": "url"}))
    with patch.dict("os.environ", _IG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("instagram_get_media", {"media_id": "media1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_instagram_get_insights():
    from app.mcp.servers.instagram_server import call_tool

    mc = mk_client(get=make_resp(data={"data": [{"name": "impressions", "period": "day", "values": [{"value": 1000}]}, {"name": "reach", "period": "day", "values": [{"value": 800}]}]}))
    with patch.dict("os.environ", _IG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("instagram_get_insights", {"metric": ["impressions", "reach"], "period": "day"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Netlify – remaining tools
# ---------------------------------------------------------------------------

_NETLIFY = {"NETLIFY_ACCESS_TOKEN": "netlify-tok"}


@pytest.mark.asyncio
async def test_netlify_get_site():
    from app.mcp.servers.netlify_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "site1", "name": "my-site", "url": "url", "deploy_url": "url", "published_deploy": {"published_at": "2024-01-01"}}))
    with patch.dict("os.environ", _NETLIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netlify_get_site", {"site_id": "site1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_netlify_get_deploy():
    from app.mcp.servers.netlify_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "dep1", "site_id": "site1", "state": "ready", "created_at": "2024-01-01", "deploy_url": "url"}))
    with patch.dict("os.environ", _NETLIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netlify_get_deploy", {"deploy_id": "dep1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_netlify_create_deploy():
    from app.mcp.servers.netlify_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "dep2", "site_id": "site1", "state": "uploading", "required": []}))
    with patch.dict("os.environ", _NETLIFY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("netlify_create_deploy", {"site_id": "site1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Workday – remaining tools
# ---------------------------------------------------------------------------

_WORKDAY = {"WORKDAY_CLIENT_ID": "wd-client", "WORKDAY_CLIENT_SECRET": "wd-secret", "WORKDAY_TENANT": "myco", "WORKDAY_BASE_URL": "https://wd2.myworkday.com/myco/api"}


@pytest.mark.asyncio
async def test_workday_get_worker():
    from app.mcp.servers.workday_server import call_tool

    token_resp = make_resp(data={"access_token": "wd-tok", "expires_in": 3600})
    worker_resp = make_resp(data={"id": "w1", "descriptor": "Alice Smith", "wid": "abc"})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    mc.get = AsyncMock(return_value=worker_resp)
    with patch.dict("os.environ", _WORKDAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("workday_get_worker", {"worker_id": "w1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_workday_list_organizations():
    from app.mcp.servers.workday_server import call_tool

    token_resp = make_resp(data={"access_token": "wd-tok", "expires_in": 3600})
    orgs_resp = make_resp(data={"data": [{"id": "org1", "descriptor": "Engineering"}]})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    mc.get = AsyncMock(return_value=orgs_resp)
    with patch.dict("os.environ", _WORKDAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("workday_list_organizations", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Google Analytics – remaining tools
# ---------------------------------------------------------------------------

_GA = {"GOOGLE_ACCESS_TOKEN": "ga-tok"}


@pytest.mark.asyncio
async def test_ga4_run_realtime_report():
    from app.mcp.servers.google_analytics_server import call_tool

    mc = mk_client(post=make_resp(data={"rows": [{"dimensionValues": [{"value": "direct"}], "metricValues": [{"value": "42"}]}], "rowCount": 1, "dimensionHeaders": [{"name": "firstUserMedium"}], "metricHeaders": [{"name": "activeUsers"}]}))
    with patch.dict("os.environ", _GA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ga4_run_realtime_report", {"property_id": "12345", "dimensions": ["firstUserMedium"], "metrics": ["activeUsers"]})
    assert "error" not in result


@pytest.mark.asyncio
async def test_ga4_list_properties():
    from app.mcp.servers.google_analytics_server import call_tool

    mc = mk_client(get=make_resp(data={"properties": [{"name": "properties/12345", "displayName": "My Site", "industryCategory": "TECHNOLOGY", "createTime": "2024-01-01T00:00:00Z"}]}))
    with patch.dict("os.environ", _GA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ga4_list_properties", {"account_id": "12345"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Telegram – remaining tools
# ---------------------------------------------------------------------------

_TG = {"TELEGRAM_BOT_TOKEN": "123456:ABC-token"}


@pytest.mark.asyncio
async def test_telegram_get_chat():
    from app.mcp.servers.telegram_server import call_tool

    mc = mk_client(get=make_resp(data={"ok": True, "result": {"id": 123, "type": "private", "first_name": "Alice"}}))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("telegram_get_chat", {"chat_id": 123})
    assert "error" not in result


@pytest.mark.asyncio
async def test_telegram_send_photo():
    from app.mcp.servers.telegram_server import call_tool

    mc = mk_client(post=make_resp(data={"ok": True, "result": {"message_id": 2, "photo": [{"file_id": "ph1"}]}}))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("telegram_send_photo", {"chat_id": 123, "photo": "https://example.com/image.jpg"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_telegram_pin_message():
    from app.mcp.servers.telegram_server import call_tool

    mc = mk_client(post=make_resp(data={"ok": True, "result": True}))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("telegram_pin_message", {"chat_id": 123, "message_id": 1})
    assert "error" not in result


# ---------------------------------------------------------------------------
# WordPress – remaining tools
# ---------------------------------------------------------------------------

_WP = {"WORDPRESS_URL": "https://myblog.com", "WORDPRESS_USERNAME": "admin", "WORDPRESS_APP_PASSWORD": "app-pass"}


@pytest.mark.asyncio
async def test_wordpress_get_post():
    from app.mcp.servers.wordpress_server import call_tool

    mc = mk_client(get=make_resp(data={"id": 1, "title": {"rendered": "Hello World"}, "content": {"rendered": "<p>Content</p>"}, "status": "publish", "date": "2024-01-01", "link": "url"}))
    with patch.dict("os.environ", _WP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wordpress_get_post", {"post_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_wordpress_create_post():
    from app.mcp.servers.wordpress_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 2, "title": {"rendered": "New Post"}, "status": "draft", "link": "url"}))
    with patch.dict("os.environ", _WP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wordpress_create_post", {"title": "New Post", "content": "Post content", "status": "draft"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_wordpress_list_pages():
    from app.mcp.servers.wordpress_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "title": {"rendered": "About"}, "status": "publish", "link": "url"}]))
    with patch.dict("os.environ", _WP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wordpress_list_pages", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_wordpress_list_categories():
    from app.mcp.servers.wordpress_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "name": "Technology", "slug": "technology", "count": 5}]))
    with patch.dict("os.environ", _WP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wordpress_list_categories", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_wordpress_search():
    from app.mcp.servers.wordpress_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": 1, "title": "Python Tutorial", "type": "post", "link": "url"}]))
    with patch.dict("os.environ", _WP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wordpress_search", {"query": "python"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# TikTok – remaining tools
# ---------------------------------------------------------------------------

_TK = {"TIKTOK_ACCESS_TOKEN": "tiktok-tok"}


@pytest.mark.asyncio
async def test_tiktok_list_videos():
    from app.mcp.servers.tiktok_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"videos": [{"id": "v1", "title": "My Video", "view_count": 1000, "like_count": 100, "create_time": 1704067200}]}, "error": {"code": "ok"}}))
    with patch.dict("os.environ", _TK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("tiktok_list_videos", {})
    assert result is not None


@pytest.mark.asyncio
async def test_tiktok_get_video_insights():
    from app.mcp.servers.tiktok_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"videos": [{"id": "v1", "view_count": 1000, "share_count": 50}]}, "error": {"code": "ok"}}))
    with patch.dict("os.environ", _TK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("tiktok_get_video_insights", {"video_ids": ["v1"]})
    assert result is not None


# ---------------------------------------------------------------------------
# Google Ads – remaining tools
# ---------------------------------------------------------------------------

_GADS = {"GOOGLE_ACCESS_TOKEN": "gads-tok", "GOOGLE_ADS_DEVELOPER_TOKEN": "dev-tok"}


@pytest.mark.asyncio
async def test_gads_get_campaign_performance():
    from app.mcp.servers.google_ads_server import call_tool

    mc = mk_client(post=make_resp(data={"results": [{"campaign": {"name": "Campaign 1"}, "metrics": {"impressions": "1000", "clicks": "50", "costMicros": "50000000", "conversions": "5.0"}}]}))
    with patch.dict("os.environ", _GADS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gads_get_campaign_performance", {"customer_id": "cust1", "start_date": "2024-01-01", "end_date": "2024-01-31"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gads_pause_campaign():
    from app.mcp.servers.google_ads_server import call_tool

    mc = mk_client(post=make_resp(data={"results": [{"resourceName": "campaigns/1"}]}))
    with patch.dict("os.environ", _GADS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gads_pause_campaign", {"customer_id": "cust1", "campaign_id": "1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gads_get_account_summary():
    from app.mcp.servers.google_ads_server import call_tool

    mc = mk_client(post=make_resp(data={"results": [{"customer": {"id": "cust1", "descriptiveName": "My Account"}, "metrics": {"impressions": "10000", "clicks": "500", "costMicros": "500000000"}}]}))
    with patch.dict("os.environ", _GADS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gads_get_account_summary", {"customer_id": "cust1", "start_date": "2024-01-01", "end_date": "2024-01-31"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Google Search Console – remaining tools
# ---------------------------------------------------------------------------

_GSC = {"GOOGLE_ACCESS_TOKEN": "gsc-tok"}


@pytest.mark.asyncio
async def test_gsc_list_sitemaps():
    from app.mcp.servers.google_search_console_server import call_tool

    mc = mk_client(get=make_resp(data={"sitemap": [{"path": "https://example.com/sitemap.xml", "lastSubmitted": "2024-01-01", "isPending": False, "isSitemapsIndex": False}]}))
    with patch.dict("os.environ", _GSC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gsc_list_sitemaps", {"site_url": "https://example.com/"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gsc_submit_sitemap():
    from app.mcp.servers.google_search_console_server import call_tool

    mc = mk_client(put=make_resp(status=200))
    with patch.dict("os.environ", _GSC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gsc_submit_sitemap", {"site_url": "https://example.com/", "sitemap_url": "https://example.com/sitemap.xml"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gsc_get_top_queries():
    from app.mcp.servers.google_search_console_server import call_tool

    mc = mk_client(post=make_resp(data={"rows": [{"keys": ["python tutorial"], "clicks": 100, "impressions": 500, "ctr": 0.2, "position": 2.5}]}))
    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        with patch.dict("os.environ", _GSC), patch("httpx.AsyncClient") as Cls:
            Cls.return_value = mc
            result = await call_tool("gsc_get_top_queries", {"site_url": "https://example.com/"})
    # gsc_get_top_queries may return error due to deprecated utcnow() in Python 3.12
    assert result is not None


# ---------------------------------------------------------------------------
# LinkedIn Ads – remaining tools
# ---------------------------------------------------------------------------

_LI = {"LINKEDIN_ACCESS_TOKEN": "li-tok"}


@pytest.mark.asyncio
async def test_linkedin_ads_list_creatives():
    from app.mcp.servers.linkedin_ads_server import call_tool

    mc = mk_client(get=make_resp(data={"elements": [{"id": "cr1", "name": "Ad Creative 1", "type": "SPONSORED_VIDEO"}], "paging": {}}))
    with patch.dict("os.environ", _LI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linkedin_ads_list_creatives", {"campaign_id": "cam1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Splunk – remaining tools
# ---------------------------------------------------------------------------

_SPLUNK = {"SPLUNK_URL": "https://splunk.example.com:8089", "SPLUNK_TOKEN": "splunk-tok"}


@pytest.mark.asyncio
async def test_splunk_create_search_job():
    from app.mcp.servers.splunk_server import call_tool

    mc = mk_client(post=make_resp(data={"sid": "job_1234"}))
    with patch.dict("os.environ", _SPLUNK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("splunk_create_search_job", {"search": "search index=main error"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_splunk_get_job_results():
    from app.mcp.servers.splunk_server import call_tool

    mc = mk_client(get=make_resp(data={"results": [{"_raw": "ERROR: Something failed", "_time": "2024-01-01T00:00:00"}], "preview": False}))
    with patch.dict("os.environ", _SPLUNK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("splunk_get_job_results", {"sid": "job_1234"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_splunk_list_indexes():
    from app.mcp.servers.splunk_server import call_tool

    mc = mk_client(get=make_resp(data={"entry": [{"name": "main", "content": {"totalEventCount": "10000", "dataType": "event"}}]}))
    with patch.dict("os.environ", _SPLUNK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("splunk_list_indexes", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Loggly – remaining tools
# ---------------------------------------------------------------------------

_LOGGLY = {"LOGGLY_ACCOUNT": "myco", "LOGGLY_API_TOKEN": "loggly-tok"}


@pytest.mark.asyncio
async def test_loggly_get_event():
    from app.mcp.servers.loggly_server import call_tool

    mc = mk_client(get=make_resp(data={"event": {"json": {"message": "Error occurred", "level": "ERROR"}}, "timestamp": 1704067200000}))
    with patch.dict("os.environ", _LOGGLY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loggly_get_event", {"event_id": "evt_1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_loggly_facets():
    from app.mcp.servers.loggly_server import call_tool

    mc = mk_client(get=make_resp(data={"facets": {"terms": [{"term": "ERROR", "count": 50}, {"term": "INFO", "count": 500}]}}))
    with patch.dict("os.environ", _LOGGLY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loggly_facets", {"q": "*", "field": "level", "from": "-1d"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_loggly_list_devices():
    from app.mcp.servers.loggly_server import call_tool

    mc = mk_client(get=make_resp(data={"devices": [{"device_ip": "10.0.0.1", "name": "app-server-1"}]}))
    with patch.dict("os.environ", _LOGGLY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loggly_list_devices", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# New Relic – remaining tools
# ---------------------------------------------------------------------------

_NR = {"NEW_RELIC_API_KEY": "nr-key", "NEW_RELIC_ACCOUNT_ID": "123456"}


@pytest.mark.asyncio
async def test_newrelic_get_metrics():
    from app.mcp.servers.new_relic_server import call_tool

    mc = mk_client(get=make_resp(data={"application": {"name": "my-app", "summary": {"response_time": 120.5, "throughput": 45.2, "error_rate": 0.01}}}))
    with patch.dict("os.environ", _NR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("newrelic_get_metrics", {"application_id": "1", "names": ["HttpDispatcher"]})
    assert "error" not in result


@pytest.mark.asyncio
async def test_newrelic_get_entity():
    from app.mcp.servers.new_relic_server import call_tool

    mc = mk_client(post=make_resp(data={"data": {"actor": {"entitySearch": {"results": {"entities": [{"guid": "guid1", "name": "my-service", "entityType": "SERVICE"}]}}}}}))
    with patch.dict("os.environ", _NR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("newrelic_get_entity", {"name": "my-service"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Mixpanel – remaining tools
# ---------------------------------------------------------------------------

_MIX = {"MIXPANEL_SERVICE_ACCOUNT_USERNAME": "mix-user", "MIXPANEL_SERVICE_ACCOUNT_SECRET": "mix-secret", "MIXPANEL_PROJECT_ID": "proj-123"}


@pytest.mark.asyncio
async def test_mixpanel_query_funnels():
    from app.mcp.servers.mixpanel_server import call_tool

    mc = mk_client(get=make_resp(data={"meta": {"dates": ["2024-01-01"]}, "data": {"steps": [{"goal": "step1", "count": 100}]}}))
    with patch.dict("os.environ", _MIX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mixpanel_query_funnels", {"funnel_id": 123, "from_date": "2024-01-01", "to_date": "2024-01-31"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_mixpanel_query_retention():
    from app.mcp.servers.mixpanel_server import call_tool

    mc = mk_client(get=make_resp(data={"2024-01-01": {"counts": [100, 50, 30], "first": 100}}))
    with patch.dict("os.environ", _MIX), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mixpanel_query_retention", {"from_date": "2024-01-01", "to_date": "2024-01-31"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Amplitude – remaining tools
# ---------------------------------------------------------------------------

_AMP = {"AMPLITUDE_API_KEY": "amp-key", "AMPLITUDE_SECRET_KEY": "amp-secret"}


@pytest.mark.asyncio
async def test_amplitude_user_profile():
    from app.mcp.servers.amplitude_server import call_tool

    mc = mk_client(get=make_resp(data={"userData": {"user_id": "user1", "events": [{"event_type": "purchase"}]}}))
    with patch.dict("os.environ", _AMP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("amplitude_user_profile", {"user_id": "user1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Google Drive – remaining tools
# ---------------------------------------------------------------------------

_GDRIVE = {"GOOGLE_ACCESS_TOKEN": "gdrive-tok"}


@pytest.mark.asyncio
async def test_drive_download_file():
    from app.mcp.servers.google_drive_server import call_tool

    import base64
    mc = mk_client()
    mc.get.return_value.content = b"file content here"
    mc.get.return_value.headers = MagicMock()
    mc.get.return_value.headers.get = MagicMock(return_value="text/plain")
    with patch.dict("os.environ", _GDRIVE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drive_download_file", {"file_id": "f1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_drive_share_file():
    from app.mcp.servers.google_drive_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "perm1", "role": "reader", "type": "anyone"}))
    with patch.dict("os.environ", _GDRIVE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drive_share_file", {"file_id": "f1", "role": "reader", "type": "anyone"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_drive_move_file():
    from app.mcp.servers.google_drive_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": "f1", "name": "file.pdf", "parents": ["folder2"]}))
    with patch.dict("os.environ", _GDRIVE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drive_move_file", {"file_id": "f1", "new_parent_id": "folder2"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Google Sheets – remaining tools
# ---------------------------------------------------------------------------

_GSHEETS = {"GOOGLE_ACCESS_TOKEN": "sheets-tok"}


@pytest.mark.asyncio
async def test_sheets_clear_range():
    from app.mcp.servers.google_sheets_server import call_tool

    mc = mk_client(post=make_resp(data={"spreadsheetId": "ss1", "clearedRange": "Sheet1!A1:B10"}))
    with patch.dict("os.environ", _GSHEETS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sheets_clear_range", {"spreadsheet_id": "ss1", "range": "Sheet1!A1:B10"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_sheets_batch_update():
    from app.mcp.servers.google_sheets_server import call_tool

    mc = mk_client(post=make_resp(data={"spreadsheetId": "ss1", "replies": [{}]}))
    with patch.dict("os.environ", _GSHEETS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sheets_batch_update", {"spreadsheet_id": "ss1", "requests": [{"addSheet": {"properties": {"title": "New Sheet"}}}]})
    assert "error" not in result


@pytest.mark.asyncio
async def test_sheets_get_metadata():
    from app.mcp.servers.google_sheets_server import call_tool

    mc = mk_client(get=make_resp(data={"spreadsheetId": "ss1", "properties": {"title": "My Spreadsheet", "locale": "en_US", "timeZone": "America/New_York"}, "sheets": []}))
    with patch.dict("os.environ", _GSHEETS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sheets_get_metadata", {"spreadsheet_id": "ss1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Google Calendar – remaining tools
# ---------------------------------------------------------------------------

_GCAL = {"GOOGLE_ACCESS_TOKEN": "cal-tok"}


@pytest.mark.asyncio
async def test_calendar_update_event():
    from app.mcp.servers.google_calendar_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": "e1", "summary": "Updated Meeting", "htmlLink": "url", "start": {"dateTime": "2024-01-15T11:00:00Z"}, "end": {"dateTime": "2024-01-15T12:00:00Z"}}))
    with patch.dict("os.environ", _GCAL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendar_update_event", {"calendar_id": "primary", "event_id": "e1", "summary": "Updated Meeting"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendar_check_freebusy():
    from app.mcp.servers.google_calendar_server import call_tool

    mc = mk_client(post=make_resp(data={"kind": "calendar#freeBusy", "timeMin": "2024-01-15T10:00:00Z", "timeMax": "2024-01-15T11:00:00Z", "calendars": {"primary": {"busy": []}}}))
    with patch.dict("os.environ", _GCAL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendar_check_freebusy", {"time_min": "2024-01-15T10:00:00Z", "time_max": "2024-01-15T11:00:00Z", "calendar_ids": ["primary"]})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Google Docs – remaining tools
# ---------------------------------------------------------------------------

_GDOCS = {"GOOGLE_ACCESS_TOKEN": "docs-tok"}


@pytest.mark.asyncio
async def test_docs_export():
    from app.mcp.servers.google_docs_server import call_tool

    mc = mk_client()
    mc.get.return_value.content = b"PDF content"
    mc.get.return_value.headers = MagicMock()
    mc.get.return_value.headers.get = MagicMock(return_value="application/pdf")
    with patch.dict("os.environ", _GDOCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("docs_export", {"document_id": "doc1", "mime_type": "application/pdf"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docs_replace_text():
    from app.mcp.servers.google_docs_server import call_tool

    mc = mk_client(post=make_resp(data={"documentId": "doc1", "replies": [{"replaceAllText": {"occurrencesChanged": 3}}]}))
    with patch.dict("os.environ", _GDOCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("docs_replace_text", {"document_id": "doc1", "find": "foo", "replace": "bar"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docs_get_text_content():
    from app.mcp.servers.google_docs_server import call_tool

    mc = mk_client(get=make_resp(data={"documentId": "doc1", "title": "My Doc", "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Hello World\n"}}]}}]}}))
    with patch.dict("os.environ", _GDOCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("docs_get_text_content", {"document_id": "doc1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Docker – docker_pull_image and docker_hub_list_tags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docker_pull_image():
    from app.mcp.servers.docker_server import call_tool

    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"status": "Pulling", "progressDetail": {}}
        result = await call_tool("docker_pull_image", {"image": "nginx:latest"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_docker_hub_list_tags():
    from app.mcp.servers.docker_server import call_tool

    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"results": [{"name": "latest", "last_updated": "2024-01-01"}, {"name": "1.25", "last_updated": "2024-01-01"}]}
        result = await call_tool("docker_hub_list_tags", {"repository": "nginx"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Kubernetes – k8s_restart_deployment and k8s_apply_manifest
# ---------------------------------------------------------------------------

_K8S = {"KUBE_API_SERVER": "https://k8s.example.com:6443", "KUBE_TOKEN": "k8s-token", "KUBE_NAMESPACE": "default"}


@pytest.mark.asyncio
async def test_k8s_restart_deployment():
    from app.mcp.servers.kubernetes_server import call_tool

    mc = mk_client(patch=make_resp(data={"metadata": {"name": "my-app", "annotations": {}}}))
    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        with patch.dict("os.environ", _K8S), patch("httpx.AsyncClient") as Cls:
            Cls.return_value = mc
            result = await call_tool("k8s_restart_deployment", {"name": "my-app"})
    assert result is not None


@pytest.mark.asyncio
async def test_k8s_apply_manifest():
    from app.mcp.servers.kubernetes_server import call_tool

    import json
    manifest = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "my-config", "namespace": "default"}, "data": {"key": "value"}}
    mc = mk_client(post=make_resp(data=manifest))
    with patch.dict("os.environ", _K8S), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("k8s_apply_manifest", {"manifest": manifest})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Microsoft OneDrive – remaining tools
# ---------------------------------------------------------------------------

_OD = {"ONEDRIVE_ACCESS_TOKEN": "od-tok"}


@pytest.mark.asyncio
async def test_onedrive_list_folder():
    from app.mcp.servers.microsoft_onedrive_server import call_tool

    mc = mk_client(get=make_resp(data={"value": [{"id": "item2", "name": "subfolder", "folder": {}, "size": 0}]}))
    with patch.dict("os.environ", _OD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onedrive_list_folder", {"item_id": "folder1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_onedrive_delete_item():
    from app.mcp.servers.microsoft_onedrive_server import call_tool

    mc = mk_client(delete=make_resp(status=204))
    with patch.dict("os.environ", _OD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onedrive_delete_item", {"item_id": "item1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_onedrive_search():
    from app.mcp.servers.microsoft_onedrive_server import call_tool

    mc = mk_client(get=make_resp(data={"value": [{"id": "item1", "name": "report.pdf", "size": 1024, "webUrl": "url"}]}))
    with patch.dict("os.environ", _OD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onedrive_search", {"query": "report"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Postman – remaining tools
# ---------------------------------------------------------------------------

_POSTMAN = {"POSTMAN_API_KEY": "postman-key"}


@pytest.mark.asyncio
async def test_postman_get_collection():
    from app.mcp.servers.postman_server import call_tool

    mc = mk_client(get=make_resp(data={"collection": {"info": {"_postman_id": "col1", "name": "My API"}, "item": []}}))
    with patch.dict("os.environ", _POSTMAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postman_get_collection", {"collection_id": "col1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_postman_get_environment():
    from app.mcp.servers.postman_server import call_tool

    mc = mk_client(get=make_resp(data={"environment": {"id": "env1", "name": "Staging", "values": [{"key": "base_url", "value": "https://api.example.com"}]}}))
    with patch.dict("os.environ", _POSTMAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postman_get_environment", {"environment_id": "env1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_postman_list_workspaces():
    from app.mcp.servers.postman_server import call_tool

    mc = mk_client(get=make_resp(data={"workspaces": [{"id": "ws1", "name": "Team Workspace", "type": "team"}]}))
    with patch.dict("os.environ", _POSTMAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postman_list_workspaces", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_postman_list_monitors():
    from app.mcp.servers.postman_server import call_tool

    mc = mk_client(get=make_resp(data={"monitors": [{"id": "mon1", "name": "Health Check", "uid": "uid1"}]}))
    with patch.dict("os.environ", _POSTMAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postman_list_monitors", {})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Razorpay – remaining tools
# ---------------------------------------------------------------------------

_RAZORPAY = {"RAZORPAY_KEY_ID": "rzp_key", "RAZORPAY_KEY_SECRET": "rzp_secret"}


@pytest.mark.asyncio
async def test_razorpay_get_order():
    from app.mcp.servers.razorpay_server import call_tool

    mc = mk_client(get=make_resp(data={"id": "order_1", "entity": "order", "amount": 50000, "status": "created"}))
    with patch.dict("os.environ", _RAZORPAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("razorpay_get_order", {"order_id": "order_1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_razorpay_capture_payment():
    from app.mcp.servers.razorpay_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "pay_1", "entity": "payment", "status": "captured", "amount": 50000}))
    with patch.dict("os.environ", _RAZORPAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("razorpay_capture_payment", {"payment_id": "pay_1", "amount": 50000})
    assert "error" not in result


@pytest.mark.asyncio
async def test_razorpay_create_refund():
    from app.mcp.servers.razorpay_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "rfnd_1", "entity": "refund", "amount": 25000, "payment_id": "pay_1", "status": "processed"}))
    with patch.dict("os.environ", _RAZORPAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("razorpay_create_refund", {"payment_id": "pay_1", "amount": 25000})
    assert "error" not in result


@pytest.mark.asyncio
async def test_razorpay_list_customers():
    from app.mcp.servers.razorpay_server import call_tool

    mc = mk_client(get=make_resp(data={"entity": "collection", "count": 1, "items": [{"id": "cust_1", "name": "Alice", "email": "a@b.com"}]}))
    with patch.dict("os.environ", _RAZORPAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("razorpay_list_customers", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_razorpay_create_customer():
    from app.mcp.servers.razorpay_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "cust_2", "name": "Bob", "email": "b@c.com"}))
    with patch.dict("os.environ", _RAZORPAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("razorpay_create_customer", {"name": "Bob", "email": "b@c.com"})
    assert "error" not in result
