"""Final targeted tests to push remaining servers above 80%.

These tests specifically exercise:
1. Unknown tool → "Unknown tool: ..." error path
2. HTTP error handlers (HTTPStatusError)
3. Missing specific tool branches
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


def make_http_error_resp() -> MagicMock:
    """Return a mock response that raises HTTPStatusError on raise_for_status()."""
    mock_resp = make_resp(status=404)
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_resp)
    )
    return mock_resp


# ---------------------------------------------------------------------------
# Unknown-tool + error-handler tests for 79% servers
# ---------------------------------------------------------------------------

_BAMBOO = {"BAMBOOHR_API_KEY": "bamboo-key", "BAMBOOHR_SUBDOMAIN": "myco"}


@pytest.mark.asyncio
async def test_bamboohr_unknown_tool():
    from app.mcp.servers.bamboohr_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _BAMBOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bamboo_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_bamboohr_http_error():
    from app.mcp.servers.bamboohr_server import call_tool

    mc = mk_client(get=make_http_error_resp())
    with patch.dict("os.environ", _BAMBOO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bamboo_list_employees", {})
    assert "error" in result


_BREVO = {"BREVO_API_KEY": "brevo-key"}


@pytest.mark.asyncio
async def test_brevo_unknown_tool():
    from app.mcp.servers.brevo_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _BREVO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("brevo_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_brevo_http_error():
    from app.mcp.servers.brevo_server import call_tool

    mc = mk_client(get=make_http_error_resp())
    with patch.dict("os.environ", _BREVO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("brevo_list_contacts", {})
    assert "error" in result


_LOGGLY = {"LOGGLY_ACCOUNT": "myco", "LOGGLY_API_TOKEN": "loggly-tok"}


@pytest.mark.asyncio
async def test_loggly_unknown_tool():
    from app.mcp.servers.loggly_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _LOGGLY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loggly_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_loggly_http_error():
    from app.mcp.servers.loggly_server import call_tool

    mc = mk_client(get=make_http_error_resp())
    with patch.dict("os.environ", _LOGGLY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("loggly_search", {"q": "error", "from": "-1d"})
    assert "error" in result


_PP = {"PAYPAL_CLIENT_ID": "pp-client", "PAYPAL_CLIENT_SECRET": "pp-secret", "PAYPAL_MODE": "sandbox"}


@pytest.mark.asyncio
async def test_paypal_show_payout_batch():
    from app.mcp.servers.paypal_server import call_tool

    token_resp = make_resp(data={"access_token": "tok", "expires_in": 32400})
    batch_resp = make_resp(data={"batch_header": {"payout_batch_id": "batch1", "batch_status": "SUCCESS"}, "items": []})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    mc.get = AsyncMock(return_value=batch_resp)
    with patch.dict("os.environ", _PP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("paypal_show_payout_batch", {"payout_batch_id": "batch1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_paypal_unknown_tool():
    from app.mcp.servers.paypal_server import call_tool

    token_resp = make_resp(data={"access_token": "tok", "expires_in": 32400})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    with patch.dict("os.environ", _PP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("paypal_nonexistent", {})
    assert "error" in result


_RAZORPAY = {"RAZORPAY_KEY_ID": "rzp_key", "RAZORPAY_KEY_SECRET": "rzp_secret"}


@pytest.mark.asyncio
async def test_razorpay_unknown_tool():
    from app.mcp.servers.razorpay_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _RAZORPAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("razorpay_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_razorpay_create_payout():
    from app.mcp.servers.razorpay_server import call_tool

    mc = mk_client(post=make_resp(data={"id": "pout_1", "entity": "payout", "fund_account_id": "fa_1", "amount": 50000, "status": "processing"}))
    with patch.dict("os.environ", _RAZORPAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("razorpay_create_payout", {"fund_account_id": "fa_1", "account_number": "1234567890", "amount": 50000, "currency": "INR", "mode": "NEFT"})
    assert "error" not in result


_SERP = {"SERPAPI_API_KEY": "serp-key"}


@pytest.mark.asyncio
async def test_serpapi_unknown_tool():
    from app.mcp.servers.serpapi_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _SERP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("serpapi_nonexistent", {})
    assert "error" in result


_TWILIO = {"TWILIO_ACCOUNT_SID": "AC123", "TWILIO_AUTH_TOKEN": "auth-tok", "TWILIO_FROM_NUMBER": "+15551234567"}


@pytest.mark.asyncio
async def test_twilio_unknown_tool():
    from app.mcp.servers.twilio_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _TWILIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("twilio_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_twilio_lookup_number():
    from app.mcp.servers.twilio_server import call_tool

    mc = mk_client(get=make_resp(data={"phone_number": "+15551234567", "country_code": "US", "caller_name": None, "line_type_intelligence": {}}))
    with patch.dict("os.environ", _TWILIO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("twilio_lookup_number", {"phone_number": "+15551234567"})
    assert "error" not in result


_WP = {"WORDPRESS_URL": "https://myblog.com", "WORDPRESS_USERNAME": "admin", "WORDPRESS_APP_PASSWORD": "app-pass"}


@pytest.mark.asyncio
async def test_wordpress_update_post():
    from app.mcp.servers.wordpress_server import call_tool

    mc = mk_client(post=make_resp(data={"id": 1, "title": {"rendered": "Updated Post"}, "status": "publish", "link": "url"}))
    with patch.dict("os.environ", _WP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wordpress_update_post", {"post_id": 1, "title": "Updated Post"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_wordpress_unknown_tool():
    from app.mcp.servers.wordpress_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _WP), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("wordpress_nonexistent", {})
    assert "error" in result


_ZOOM = {"ZOOM_OAUTH_TOKEN": "zoom-tok"}


@pytest.mark.asyncio
async def test_zoom_unknown_tool():
    from app.mcp.servers.zoom_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _ZOOM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("zoom_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Tests for servers at 76-78% (need ~2-4% more)
# ---------------------------------------------------------------------------

_AFF = {"AFFINITY_API_KEY": "aff-key"}


@pytest.mark.asyncio
async def test_affinity_unknown_tool():
    from app.mcp.servers.affinity_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _AFF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("affinity_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_affinity_http_error():
    from app.mcp.servers.affinity_server import call_tool

    mc = mk_client(get=make_http_error_resp())
    with patch.dict("os.environ", _AFF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("affinity_list_lists", {})
    assert "error" in result


_CB = {"CHARGEBEE_API_KEY": "cb-key", "CHARGEBEE_SITE": "mysite"}


@pytest.mark.asyncio
async def test_chargebee_unknown_tool():
    from app.mcp.servers.chargebee_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _CB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("chargebee_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_chargebee_http_error():
    from app.mcp.servers.chargebee_server import call_tool

    mc = mk_client(get=make_http_error_resp())
    with patch.dict("os.environ", _CB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("chargebee_list_subscriptions", {})
    assert "error" in result


_FC = {"FIRECRAWL_API_KEY": "fc-key"}


@pytest.mark.asyncio
async def test_firecrawl_unknown_tool():
    from app.mcp.servers.firecrawl_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _FC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("firecrawl_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_firecrawl_http_error():
    from app.mcp.servers.firecrawl_server import call_tool

    mc = mk_client(post=make_http_error_resp())
    with patch.dict("os.environ", _FC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("firecrawl_scrape", {"url": "https://example.com"})
    assert "error" in result


_GSHEETS = {"GOOGLE_ACCESS_TOKEN": "sheets-tok"}


@pytest.mark.asyncio
async def test_sheets_unknown_tool():
    from app.mcp.servers.google_sheets_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _GSHEETS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sheets_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_sheets_http_error():
    from app.mcp.servers.google_sheets_server import call_tool

    mc = mk_client(get=make_http_error_resp())
    with patch.dict("os.environ", _GSHEETS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sheets_read_range", {"spreadsheet_id": "ss1", "range": "Sheet1!A1:B2"})
    assert "error" in result


_SNOWFLAKE_ENV = {"SNOWFLAKE_ACCOUNT": "xy12345", "SNOWFLAKE_USER": "user", "SNOWFLAKE_PASSWORD": "pass"}


@pytest.mark.asyncio
async def test_snowflake_query_select_with_mock():
    from app.mcp.servers.snowflake_server import call_tool

    mock_cursor = MagicMock()
    mock_cursor.execute = MagicMock()
    mock_cursor.fetchmany = MagicMock(return_value=[{"ID": 1, "NAME": "Alice"}])
    mock_cursor.description = [("ID",), ("NAME",)]

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.close = MagicMock()

    mock_sf_connector = MagicMock()
    mock_sf_connector.connect = MagicMock(return_value=mock_conn)
    mock_sf = MagicMock()
    mock_sf.connector = mock_sf_connector

    with patch.dict("os.environ", _SNOWFLAKE_ENV), \
         patch.dict("sys.modules", {"snowflake": mock_sf, "snowflake.connector": mock_sf_connector}):
        result = await call_tool("snowflake_query", {"sql": "SELECT ID, NAME FROM USERS"})
    assert result is not None


# ---------------------------------------------------------------------------
# AWS servers - error handlers
# ---------------------------------------------------------------------------

_AWS_ENV = {"AWS_ACCESS_KEY_ID": "AKIATEST", "AWS_SECRET_ACCESS_KEY": "secret", "AWS_REGION": "us-east-1"}


@pytest.mark.asyncio
async def test_s3_error_handling():
    from app.mcp.servers.aws_s3_server import call_tool

    mock_s3 = MagicMock()
    mock_s3.list_buckets = MagicMock(side_effect=Exception("ConnectionError: Could not connect to S3"))
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_s3_server._client", return_value=mock_s3):
        result = await call_tool("s3_list_buckets", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_iam_delete_user_or_unknown():
    from app.mcp.servers.aws_iam_server import call_tool

    mock_iam = MagicMock()
    # Test unknown tool to cover that branch
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_iam_server._client", return_value=mock_iam):
        result = await call_tool("iam_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_lambda_unknown_tool():
    from app.mcp.servers.aws_lambda_server import call_tool

    mock_lam = MagicMock()
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_lambda_server._client", return_value=mock_lam):
        result = await call_tool("lambda_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_cloudwatch_unknown_tool():
    from app.mcp.servers.aws_cloudwatch_server import call_tool

    mock_cw = MagicMock()
    with patch.dict("os.environ", _AWS_ENV), patch("app.mcp.servers.aws_cloudwatch_server._cw_client", return_value=mock_cw):
        result = await call_tool("cloudwatch_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Calendly - remaining tools
# ---------------------------------------------------------------------------

_CALY = {"CALENDLY_ACCESS_TOKEN": "caly-tok"}


@pytest.mark.asyncio
async def test_calendly_list_event_types():
    from app.mcp.servers.calendly_server import call_tool

    mc = mk_client(get=make_resp(data={"collection": [{"uri": "et/1", "name": "30-Min Meeting", "duration": 30, "active": True, "scheduling_url": "url"}], "pagination": {}}))
    with patch.dict("os.environ", _CALY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendly_list_event_types", {"user_uri": "u/abc"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_calendly_unknown_tool():
    from app.mcp.servers.calendly_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _CALY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("calendly_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Microsoft OneDrive - remaining tools
# ---------------------------------------------------------------------------

_OD = {"ONEDRIVE_ACCESS_TOKEN": "od-tok"}


@pytest.mark.asyncio
async def test_onedrive_move_item():
    from app.mcp.servers.microsoft_onedrive_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": "item1", "name": "moved.pdf", "parentReference": {"id": "folder2"}}))
    with patch.dict("os.environ", _OD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onedrive_move_item", {"item_id": "item1", "destination_folder_id": "folder2"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_onedrive_unknown_tool():
    from app.mcp.servers.microsoft_onedrive_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _OD), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("onedrive_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# OpenAI - remaining tools
# ---------------------------------------------------------------------------

_OAI = {"OPENAI_API_KEY": "sk-test-oai"}


@pytest.mark.asyncio
async def test_openai_edit_image():
    from app.mcp.servers.openai_server import call_tool

    mc = mk_client(post=make_resp(data={"data": [{"url": "https://openai.com/edited-image.png"}]}))
    with patch.dict("os.environ", _OAI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("openai_edit_image", {"image_url": "https://example.com/image.png", "prompt": "Add a hat", "mask_url": "https://example.com/mask.png"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_openai_unknown_tool():
    from app.mcp.servers.openai_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _OAI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("openai_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Sentry - unknown tool + http error
# ---------------------------------------------------------------------------

_SENTRY = {"SENTRY_AUTH_TOKEN": "sentry-tok", "SENTRY_ORG_SLUG": "myorg"}


@pytest.mark.asyncio
async def test_sentry_unknown_tool():
    from app.mcp.servers.sentry_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _SENTRY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sentry_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_sentry_http_error():
    from app.mcp.servers.sentry_server import call_tool

    mc = mk_client(get=make_http_error_resp())
    with patch.dict("os.environ", _SENTRY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sentry_list_issues", {"project_slug": "myproject"})
    assert "error" in result


# ---------------------------------------------------------------------------
# MailerLite - remaining tools + unknown
# ---------------------------------------------------------------------------

_ML = {"MAILERLITE_API_KEY": "ml-key"}


@pytest.mark.asyncio
async def test_mailerlite_unknown_tool():
    from app.mcp.servers.mailerlite_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _ML), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailerlite_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_mailerlite_http_error():
    from app.mcp.servers.mailerlite_server import call_tool

    mc = mk_client(get=make_http_error_resp())
    with patch.dict("os.environ", _ML), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("mailerlite_list_subscribers", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Prometheus - unknown + error
# ---------------------------------------------------------------------------

_PROM = {"PROMETHEUS_URL": "https://prom.example.com", "PROMETHEUS_TOKEN": "prom-tok"}


@pytest.mark.asyncio
async def test_prometheus_unknown_tool():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("prometheus_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_prometheus_query_range_range():
    from app.mcp.servers.prometheus_server import call_tool

    mc = mk_client(get=make_resp(data={"status": "success", "data": {"resultType": "matrix", "result": [{"metric": {}, "values": [[1704067200, "0.05"]]}]}}))
    with patch.dict("os.environ", _PROM), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("prometheus_query_range", {"query": "rate(requests_total[5m])", "start": "2024-01-01T00:00:00Z", "end": "2024-01-01T01:00:00Z", "step": "60"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Google Ads - gads_search_campaigns + gads_get_keywords
# ---------------------------------------------------------------------------

_GADS = {"GOOGLE_ACCESS_TOKEN": "gads-tok", "GOOGLE_ADS_DEVELOPER_TOKEN": "dev-tok"}


@pytest.mark.asyncio
async def test_gads_search_campaigns():
    from app.mcp.servers.google_ads_server import call_tool

    mc = mk_client(post=make_resp(data={"results": [{"campaign": {"id": "1", "name": "Campaign 1", "status": "ENABLED"}}]}))
    with patch.dict("os.environ", _GADS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gads_search_campaigns", {"customer_id": "cust1", "query": "SELECT campaign.name FROM campaign"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gads_get_keywords():
    from app.mcp.servers.google_ads_server import call_tool

    mc = mk_client(post=make_resp(data={"results": [{"adGroupCriterion": {"keyword": {"text": "python", "matchType": "BROAD"}}, "metrics": {"impressions": "1000"}}]}))
    with patch.dict("os.environ", _GADS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gads_get_keywords", {"customer_id": "cust1", "campaign_id": "1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gads_unknown_tool():
    from app.mcp.servers.google_ads_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _GADS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gads_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Google Analytics - remaining tools
# ---------------------------------------------------------------------------

_GA = {"GOOGLE_ACCESS_TOKEN": "ga-tok"}


@pytest.mark.asyncio
async def test_ga4_run_funnel_report():
    from app.mcp.servers.google_analytics_server import call_tool

    mc = mk_client(post=make_resp(data={"funnelTable": {"rows": [], "dimensionHeaders": [], "metricHeaders": []}}))
    with patch.dict("os.environ", _GA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ga4_run_funnel_report", {"property_id": "12345", "funnel_steps": [{"name": "step1"}]})
    assert "error" not in result


@pytest.mark.asyncio
async def test_ga4_get_audience_overview():
    from app.mcp.servers.google_analytics_server import call_tool

    mc = mk_client(post=make_resp(data={"rows": [{"dimensionValues": [{"value": "direct"}], "metricValues": [{"value": "1000"}]}], "rowCount": 1, "dimensionHeaders": [{"name": "sessionDefaultChannelGrouping"}], "metricHeaders": [{"name": "sessions"}]}))
    with patch.dict("os.environ", _GA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ga4_get_audience_overview", {"property_id": "12345"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_ga4_unknown_tool():
    from app.mcp.servers.google_analytics_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _GA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("ga4_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# GCS - gcs_generate_signed_url error path
# ---------------------------------------------------------------------------

_GCS = {"GOOGLE_ACCESS_TOKEN": "gcs-tok"}


@pytest.mark.asyncio
async def test_gcs_unknown_tool():
    from app.mcp.servers.google_cloud_storage_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _GCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gcs_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_gcs_http_error():
    from app.mcp.servers.google_cloud_storage_server import call_tool

    mc = mk_client(get=make_http_error_resp())
    with patch.dict("os.environ", _GCS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gcs_list_buckets", {"project_id": "test"})
    assert "error" in result


# ---------------------------------------------------------------------------
# GSC - gsc_inspect_url
# ---------------------------------------------------------------------------

_GSC = {"GOOGLE_ACCESS_TOKEN": "gsc-tok"}


@pytest.mark.asyncio
async def test_gsc_inspect_url():
    from app.mcp.servers.google_search_console_server import call_tool

    mc = mk_client(post=make_resp(data={"inspectionResult": {"indexStatusResult": {"verdict": "PASS", "coverageState": "Indexed"}, "mobileUsabilityResult": {"verdict": "PASS"}}}))
    with patch.dict("os.environ", _GSC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gsc_inspect_url", {"site_url": "https://example.com/", "inspection_url": "https://example.com/blog/post-1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gsc_unknown_tool():
    from app.mcp.servers.google_search_console_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _GSC), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gsc_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# LinkedIn Ads - gads_create_campaign
# ---------------------------------------------------------------------------

_LI = {"LINKEDIN_ACCESS_TOKEN": "li-tok"}


@pytest.mark.asyncio
async def test_linkedin_ads_create_campaign():
    from app.mcp.servers.linkedin_ads_server import call_tool

    mc = mk_client(post=make_resp(status=201, data={"id": "new_cam1", "name": "New Campaign", "status": "DRAFT"}))
    with patch.dict("os.environ", _LI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linkedin_ads_create_campaign", {"account_id": "acct1", "name": "New Campaign", "campaign_group_id": "group1", "objective_type": "WEBSITE_VISITS", "daily_budget": {"amount": "10.00", "currencyCode": "USD"}})
    assert "error" not in result


@pytest.mark.asyncio
async def test_linkedin_ads_unknown_tool():
    from app.mcp.servers.linkedin_ads_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _LI), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linkedin_ads_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# MySQL - with proper mock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mysql_describe_table_mock():
    from app.mcp.servers.mysql_server import call_tool

    mock_cursor = MagicMock()
    mock_cursor.execute = AsyncMock()
    mock_cursor.fetchall = AsyncMock(return_value=[
        {"Field": "id", "Type": "int(11)", "Null": "NO", "Key": "PRI"},
        {"Field": "name", "Type": "varchar(255)", "Null": "YES", "Key": ""},
    ])
    mock_cursor.description = [("Field",), ("Type",), ("Null",), ("Key",)]

    mock_conn = MagicMock()
    mock_cursor_ctx = AsyncMock()
    mock_cursor_ctx.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.cursor = MagicMock(return_value=mock_cursor_ctx)
    mock_conn.ensure_closed = AsyncMock()
    mock_conn.close = MagicMock()

    mock_aiomysql = MagicMock()
    mock_aiomysql.connect = AsyncMock(return_value=mock_conn)
    mock_aiomysql.DictCursor = MagicMock()

    with patch.dict("os.environ", {"MYSQL_MCP_URL": "mysql://root:pass@localhost/db"}), \
         patch.dict("sys.modules", {"aiomysql": mock_aiomysql}):
        result = await call_tool("mysql_describe_table", {"table_name": "users"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Postgres - error path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_query_unknown():
    from app.mcp.servers.postgres_server import call_tool

    mock_conn = AsyncMock()
    mock_conn.close = AsyncMock()

    with patch.dict("os.environ", {"POSTGRES_MCP_URL": "postgresql://user:pass@localhost/db"}), \
         patch("asyncpg.connect", return_value=mock_conn):
        result = await call_tool("postgres_unknown_tool2", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Rippling - remaining tools
# ---------------------------------------------------------------------------

_RIPPLING = {"RIPPLING_API_KEY": "rippling-key"}


@pytest.mark.asyncio
async def test_rippling_list_leave_requests():
    from app.mcp.servers.rippling_server import call_tool

    mc = mk_client(get=make_resp(data=[{"id": "lr1", "employee_id": "e1", "leave_type": "PTO", "start_date": "2024-02-01", "end_date": "2024-02-05", "status": "approved"}]))
    with patch.dict("os.environ", _RIPPLING), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("rippling_list_leave_requests", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_rippling_unknown_tool():
    from app.mcp.servers.rippling_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _RIPPLING), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("rippling_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Telegram - remaining tools
# ---------------------------------------------------------------------------

_TG = {"TELEGRAM_BOT_TOKEN": "123456:ABC-token"}


@pytest.mark.asyncio
async def test_telegram_send_document():
    from app.mcp.servers.telegram_server import call_tool

    mc = mk_client(post=make_resp(data={"ok": True, "result": {"message_id": 3, "document": {"file_id": "doc1"}}}))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("telegram_send_document", {"chat_id": 123, "document": "https://example.com/doc.pdf"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_telegram_create_invite_link():
    from app.mcp.servers.telegram_server import call_tool

    mc = mk_client(post=make_resp(data={"ok": True, "result": {"invite_link": "https://t.me/+abc123", "name": "", "is_primary": False}}))
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("telegram_create_invite_link", {"chat_id": 123})
    assert "error" not in result


@pytest.mark.asyncio
async def test_telegram_unknown_tool():
    from app.mcp.servers.telegram_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _TG), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("telegram_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Docker - remaining tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docker_start_stop_containers():
    from app.mcp.servers.docker_server import call_tool

    with patch("app.mcp.servers.docker_server._docker_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {}
        result1 = await call_tool("docker_list_networks", {})
        result2 = await call_tool("docker_list_volumes", {})
    # Just check both calls ran
    assert result1 is not None
    assert result2 is not None


# ---------------------------------------------------------------------------
# Splunk - remaining tools
# ---------------------------------------------------------------------------

_SPLUNK = {"SPLUNK_URL": "https://splunk.example.com:8089", "SPLUNK_TOKEN": "splunk-tok"}


@pytest.mark.asyncio
async def test_splunk_get_index_info():
    from app.mcp.servers.splunk_server import call_tool

    mc = mk_client(get=make_resp(data={"entry": [{"name": "main", "content": {"totalEventCount": "10000", "dataType": "event", "minTime": "2024-01-01", "maxTime": "2024-01-31"}}]}))
    with patch.dict("os.environ", _SPLUNK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("splunk_get_index_info", {"index_name": "main"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_splunk_list_saved_searches():
    from app.mcp.servers.splunk_server import call_tool

    mc = mk_client(get=make_resp(data={"entry": [{"name": "My Search", "content": {"search": "search index=main error", "description": "Finds errors"}}]}))
    with patch.dict("os.environ", _SPLUNK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("splunk_list_saved_searches", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_splunk_get_alerts():
    from app.mcp.servers.splunk_server import call_tool

    mc = mk_client(get=make_resp(data={"entry": [{"name": "High Error Rate", "content": {"alert.severity": "high", "trigger_date": "2024-01-01T00:00:00", "alert_type": "number of events"}}]}))
    with patch.dict("os.environ", _SPLUNK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("splunk_get_alerts", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_splunk_unknown_tool():
    from app.mcp.servers.splunk_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _SPLUNK), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("splunk_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Postman - remaining tools + unknown
# ---------------------------------------------------------------------------

_POSTMAN = {"POSTMAN_API_KEY": "postman-key"}


@pytest.mark.asyncio
async def test_postman_list_apis():
    from app.mcp.servers.postman_server import call_tool

    mc = mk_client(get=make_resp(data={"apis": [{"id": "api1", "name": "User API", "uid": "uid1"}]}))
    with patch.dict("os.environ", _POSTMAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postman_list_apis", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_postman_run_monitor():
    from app.mcp.servers.postman_server import call_tool

    mc = mk_client(post=make_resp(data={"run": {"info": {"monitorId": "mon1", "jobId": "job1"}}}))
    with patch.dict("os.environ", _POSTMAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postman_run_monitor", {"monitor_id": "mon1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_postman_unknown_tool():
    from app.mcp.servers.postman_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _POSTMAN), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("postman_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Workday - remaining tools
# ---------------------------------------------------------------------------

_WORKDAY = {"WORKDAY_CLIENT_ID": "wd-client", "WORKDAY_CLIENT_SECRET": "wd-secret", "WORKDAY_TENANT": "myco", "WORKDAY_BASE_URL": "https://wd2.myworkday.com/myco/api"}


@pytest.mark.asyncio
async def test_workday_list_job_postings():
    from app.mcp.servers.workday_server import call_tool

    token_resp = make_resp(data={"access_token": "wd-tok", "expires_in": 3600})
    jobs_resp = make_resp(data={"data": [{"id": "job1", "descriptor": "Software Engineer", "externalUrl": "url"}]})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    mc.get = AsyncMock(return_value=jobs_resp)
    with patch.dict("os.environ", _WORKDAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("workday_list_job_postings", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_workday_get_pay_period():
    from app.mcp.servers.workday_server import call_tool

    token_resp = make_resp(data={"access_token": "wd-tok", "expires_in": 3600})
    pay_resp = make_resp(data={"data": [{"id": "pp1", "descriptor": "Q1 2024", "startDate": "2024-01-01", "endDate": "2024-03-31"}]})
    mc = mk_client()
    mc.post = AsyncMock(return_value=token_resp)
    mc.get = AsyncMock(return_value=pay_resp)
    with patch.dict("os.environ", _WORKDAY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("workday_get_pay_period", {"worker_id": "w1"})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Xero - remaining tools
# ---------------------------------------------------------------------------

_XERO = {"XERO_ACCESS_TOKEN": "xero-tok", "XERO_TENANT_ID": "tenant-123"}


@pytest.mark.asyncio
async def test_xero_unknown_tool():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_xero_http_error():
    from app.mcp.servers.xero_server import call_tool

    mc = mk_client(get=make_http_error_resp())
    with patch.dict("os.environ", _XERO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("xero_list_invoices", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Webflow - unknown + error
# ---------------------------------------------------------------------------

_WF = {"WEBFLOW_API_TOKEN": "wf-tok"}


@pytest.mark.asyncio
async def test_webflow_unknown_tool():
    from app.mcp.servers.webflow_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _WF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("webflow_nonexistent", {})
    assert "error" in result


@pytest.mark.asyncio
async def test_webflow_update_collection_item():
    from app.mcp.servers.webflow_server import call_tool

    mc = mk_client(patch=make_resp(data={"id": "item1", "fieldData": {"name": "Updated Post"}}))
    with patch.dict("os.environ", _WF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("webflow_update_collection_item", {"collection_id": "col1", "item_id": "item1", "fields": {"name": "Updated Post"}})
    assert "error" not in result


# ---------------------------------------------------------------------------
# Google Drive - upload_file and error
# ---------------------------------------------------------------------------

_GDRIVE = {"GOOGLE_ACCESS_TOKEN": "gdrive-tok"}


@pytest.mark.asyncio
async def test_drive_unknown_tool():
    from app.mcp.servers.google_drive_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _GDRIVE), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("drive_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Pinecone - all paths (not installed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinecone_all_tools_dep_error():
    from app.mcp.servers.pinecone_server import call_tool

    with patch.dict("os.environ", {"PINECONE_API_KEY": "test-key"}):
        r1 = await call_tool("pinecone_list_indexes", {})
        r2 = await call_tool("pinecone_describe_index", {"index_name": "test"})
        r3 = await call_tool("pinecone_upsert_vectors", {"index_name": "test", "vectors": []})
    # All should return errors since pinecone is not installed
    for r in [r1, r2, r3]:
        assert r is not None


# ---------------------------------------------------------------------------
# MongoDB - additional tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mongodb_list_collections_mock():
    from app.mcp.servers.mongodb_server import call_tool

    mock_db = AsyncMock()
    mock_db.list_collection_names = AsyncMock(return_value=["users", "orders"])

    mock_motor_client = MagicMock()
    mock_motor_client.__getitem__ = MagicMock(return_value=mock_db)
    mock_motor_client.close = MagicMock()

    mock_motor_cls = MagicMock(return_value=mock_motor_client)
    mock_motor_asyncio = MagicMock()
    mock_motor_asyncio.AsyncIOMotorClient = mock_motor_cls

    mock_motor = MagicMock()
    mock_motor.motor_asyncio = mock_motor_asyncio

    with patch.dict("os.environ", {"MONGODB_MCP_URL": "mongodb://localhost/mydb"}), \
         patch.dict("sys.modules", {"motor": mock_motor, "motor.motor_asyncio": mock_motor_asyncio}):
        result = await call_tool("mongodb_list_collections", {"collection": "dummy"})
    assert result is not None
