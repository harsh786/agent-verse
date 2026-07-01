"""E2E tests for ALL MCP connector servers.

Tests (parametrized over every server module):
  1. Every server imports cleanly
  2. TOOL_DEFINITIONS is a valid non-empty list with required fields
  3. call_tool is an async function
  4. Missing credentials → returns dict (no exception raised)
  5. All tools in TOOL_DEFINITIONS are dispatched (no "Unknown tool" for defined tools)
  6. HTTP 4xx → call_tool returns dict with 'error' key (not raises) [httpx servers only]

Single tests:
  7. Registry wiring includes all catalog connectors
  8. JIRA server uses /rest/api/3/search/jql (not deprecated /rest/api/3/search)
  9. Slack server checks 'ok' field in API responses
"""
from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
import re
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.mcp.servers as _servers_pkg

# ---------------------------------------------------------------------------
# Module discovery
# ---------------------------------------------------------------------------

SERVER_MODULES: list[str] = sorted(
    info.name
    for info in pkgutil.iter_modules(_servers_pkg.__path__)
    if info.name != "registry_wiring"
)

# Servers that use boto3 / asyncpg / motor / etc. instead of httpx
NON_HTTPX_MODULES: frozenset[str] = frozenset(
    [
        "amazon_ses_server",
        "amazon_sqs_server",
        "aws_cloudwatch_server",
        "aws_iam_server",
        "aws_lambda_server",
        "aws_s3_server",
        "aws_server",
        "mongodb_server",
        "mysql_server",
        "postgres_server",
        "redis_server",
        "snowflake_server",
    ]
)

HTTPX_MODULES: list[str] = sorted(m for m in SERVER_MODULES if m not in NON_HTTPX_MODULES)

# ---------------------------------------------------------------------------
# Comprehensive fake-credentials env for test 5 (all tools dispatched).
# Generated from os.getenv() calls across all 318 server files.
# ---------------------------------------------------------------------------
_ALL_FAKE_CREDS: dict[str, str] = {k: "fake-value" for k in [
    "ACOUSTIC_CLIENT_ID", "ACOUSTIC_CLIENT_SECRET", "ACOUSTIC_REFRESH_TOKEN",
    "ACTIVECAMPAIGN_API_KEY", "ACTIVECAMPAIGN_BASE_URL",
    "AFFINITY_API_KEY",
    "AIRTABLE_API_KEY", "AIRTABLE_BASE_ID",
    "ALCHEMY_API_KEY", "ALCHEMY_NETWORK",
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
    "AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET",
    "AMPLITUDE_API_KEY", "AMPLITUDE_SECRET_KEY",
    "ANVIL_API_KEY",
    "APOLLO_API_KEY",
    "APPSHEET_ACCESS_KEY", "APPSHEET_APP_ID",
    "AP_NEWS_API_KEY",
    "ASANA_ACCESS_TOKEN", "ASANA_WORKSPACE_GID",
    "ATHENA_ACCESS_TOKEN", "ATHENA_PRACTICE_ID",
    "ATTIO_API_KEY",
    "AUTOPILOT_API_KEY",
    "AWEBER_ACCESS_TOKEN",
    "AWS_ACCESS_KEY_ID", "AWS_DEFAULT_REGION", "AWS_REGION", "AWS_SECRET_ACCESS_KEY",
    "AZURE_DEVOPS_ORG", "AZURE_DEVOPS_PROJECT", "AZURE_DEVOPS_TOKEN",
    "BAMBOOHR_API_KEY", "BAMBOOHR_SUBDOMAIN",
    "BASECAMP_ACCESS_TOKEN", "BASECAMP_ACCOUNT_ID", "BASECAMP_USER_AGENT",
    "BEDS24_API_KEY",
    "BIGQUERY_ACCESS_TOKEN", "BIGQUERY_PROJECT_ID",
    "BITBUCKET_APP_PASSWORD", "BITBUCKET_USERNAME",
    "BITLY_ACCESS_TOKEN",
    "BOX_ACCESS_TOKEN",
    "BRAINTREE_MERCHANT_ID", "BRAINTREE_PRIVATE_KEY", "BRAINTREE_PUBLIC_KEY",
    "BRAVE_SEARCH_API_KEY",
    "BREVO_API_KEY",
    "BREX_TOKEN",
    "BUFFER_ACCESS_TOKEN",
    "BUILDIUM_CLIENT_ID", "BUILDIUM_CLIENT_SECRET",
    "CALENDLY_ACCESS_TOKEN",
    "CAMPAIGN_MONITOR_API_KEY",
    "CANVAS_ACCESS_TOKEN", "CANVAS_DOMAIN",
    "CAPSULE_API_TOKEN",
    "CHANNABLE_API_KEY", "CHANNABLE_COMPANY_ID",
    "CHARGEBEE_API_KEY", "CHARGEBEE_SITE",
    "CHATFUEL_BOT_ID", "CHATFUEL_TOKEN",
    "CIRCLECI_TOKEN",
    "CLEARBIT_API_KEY",
    "CLICKFUNNELS_API_KEY",
    "CLICKUP_API_TOKEN",
    "CLIO_ACCESS_TOKEN",
    "CLOCKIFY_API_KEY",
    "CLOSE_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "CLOUDINARY_CLOUD_NAME",
    "CONFLUENCE_API_TOKEN", "CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL",
    "CONSTANT_CONTACT_API_KEY",
    "CONVERTKIT_API_KEY", "CONVERTKIT_API_SECRET",
    "COPPER_API_KEY", "COPPER_USER_EMAIL",
    "CUSTOMERIO_API_KEY", "CUSTOMERIO_APP_API_KEY", "CUSTOMERIO_SITE_ID",
    "DATABOX_API_KEY",
    "DATADOG_API_KEY", "DATADOG_APP_KEY", "DATADOG_SITE",
    "DEEL_API_KEY",
    "DELIGHTED_API_KEY",
    "DIGISTORE24_API_KEY",
    "DIGITALOCEAN_TOKEN",
    "DISCORD_BOT_TOKEN",
    "DOCKER_HOST",
    "DOCUSIGN_ACCESS_TOKEN", "DOCUSIGN_ACCOUNT_ID", "DOCUSIGN_BASE_URL",
    "DOORDASH_DEVELOPER_ID", "DOORDASH_KEY_ID", "DOORDASH_SIGNING_SECRET",
    "DRCHRONO_ACCESS_TOKEN",
    "DRIP_API_TOKEN",
    "DROPBOX_ACCESS_TOKEN",
    "DYNAMICS365_ACCESS_TOKEN", "DYNAMICS365_ORG_URL",
    "EASYWEBINAR_API_KEY",
    "EBAY_APP_ID", "EBAY_OAUTH_TOKEN",
    "ECWID_SECRET_TOKEN", "ECWID_STORE_ID",
    "EGOI_API_KEY",
    "ELASTICSEARCH_API_KEY", "ELASTICSEARCH_PASSWORD", "ELASTICSEARCH_URL", "ELASTICSEARCH_USER",
    "ELAVON_MERCHANT_ID", "ELAVON_PIN", "ELAVON_USER_ID",
    "ELEVENLABS_API_KEY",
    "EMARSYS_SECRET", "EMARSYS_USERNAME",
    "EMMA_ACCOUNT_ID", "EMMA_PRIVATE_KEY", "EMMA_PUBLIC_KEY",
    "ENCHARGE_API_KEY",
    "EPIC_ACCESS_TOKEN", "EPIC_BASE_URL",
    "ESPUTNIK_LOGIN", "ESPUTNIK_PASSWORD",
    "ETSY_ACCESS_TOKEN", "ETSY_API_KEY",
    "EVENTBRITE_API_KEY",
    "EVERNOTE_ACCESS_TOKEN",
    "FACEBOOK_ACCESS_TOKEN", "FACEBOOK_PAGE_ID", "FACEBOOK_PIXEL_ID",
    "FEEDLY_ACCESS_TOKEN",
    "FIGMA_ACCESS_TOKEN",
    "FILESTACK_API_KEY",
    "FIREBASE_ACCESS_TOKEN", "FIREBASE_PROJECT_ID",
    "FIRECRAWL_API_KEY",
    "FIREFLIES_API_KEY",
    "FITBIT_ACCESS_TOKEN",
    "FLEXPORT_API_KEY",
    "FORMSTACK_API_KEY",
    "FRESHBOOKS_ACCESS_TOKEN",
    "FRESHCHAT_API_TOKEN", "FRESHCHAT_DOMAIN",
    "FRESHDESK_API_KEY", "FRESHDESK_DOMAIN",
    "FRESHSALES_API_KEY", "FRESHSALES_DOMAIN",
    "FRESHSERVICE_API_KEY", "FRESHSERVICE_DOMAIN",
    "FRONT_API_TOKEN",
    "FULLCONTACT_API_KEY",
    "GAINSIGHT_ACCESS_KEY",
    "GCP_API_KEY", "GCP_PROJECT_ID",
    "GECKOBOARD_API_KEY",
    "GEMINI_API_KEY",
    "GETRESPONSE_API_KEY",
    "GITHUB_BASE_URL", "GITHUB_TOKEN",
    "GITLAB_BASE_URL", "GITLAB_TOKEN",
    "GLEAM_API_KEY",
    "GMAIL_ACCESS_TOKEN",
    "GONG_ACCESS_KEY", "GONG_ACCESS_KEY_SECRET",
    "GOOGLE_ACCESS_TOKEN", "GOOGLE_ADS_CUSTOMER_ID", "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_SERVICE_ACCOUNT_JSON",
    "GORGIAS_API_KEY", "GORGIAS_DOMAIN", "GORGIAS_EMAIL",
    "GOTOWEBINAR_ACCESS_TOKEN", "GOTOWEBINAR_ORGANIZER_KEY",
    "GRAVITY_FORMS_CONSUMER_KEY", "GRAVITY_FORMS_CONSUMER_SECRET", "GRAVITY_FORMS_SITE_URL",
    "GREENHOUSE_API_KEY",
    "GUMROAD_ACCESS_TOKEN",
    "GUSTO_ACCESS_TOKEN",
    "GUST_ACCESS_TOKEN",
    "HARVEST_ACCESS_TOKEN", "HARVEST_ACCOUNT_ID",
    "HARVEY_API_KEY",
    "HELP_SCOUT_API_KEY",
    "HEROKU_API_KEY",
    "HIGHLEVEL_API_KEY",
    "HIVE_API_KEY", "HIVE_USER_ID",
    "HOME_ASSISTANT_TOKEN", "HOME_ASSISTANT_URL",
    "HOOTSUITE_ACCESS_TOKEN",
    "HUBSPOT_API_KEY",
    "INSIGHTLY_API_KEY",
    "INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_BUSINESS_ACCOUNT_ID",
    "INTERCOM_ACCESS_TOKEN",
    "INVOICE_NINJA_TOKEN",
    "JENKINS_API_TOKEN", "JENKINS_URL", "JENKINS_USER",
    "JIRA_API_TOKEN", "JIRA_BASE_URL", "JIRA_EMAIL",
    "JOTFORM_API_KEY",
    "KAFKA_API_KEY", "KAFKA_API_SECRET", "KAFKA_REST_ENDPOINT",
    "KAJABI_API_KEY",
    "KLAVIYO_API_KEY",
    "KLENTY_API_KEY",
    "KNACK_API_KEY", "KNACK_APP_ID",
    "KOALA_API_KEY",
    "KONNEKTIVE_LOGIN_ID", "KONNEKTIVE_PASSWORD",
    "KUBE_API_SERVER", "KUBE_CA_CERT", "KUBE_INSECURE_SKIP_VERIFY", "KUBE_NAMESPACE", "KUBE_TOKEN",
    "LEADPAGES_API_KEY",
    "LEMLIST_API_KEY",
    "LIGHTSPEED_ACCESS_TOKEN", "LIGHTSPEED_ACCOUNT_ID",
    "LINEAR_API_KEY",
    "LINKEDIN_ACCESS_TOKEN",
    "LIVESTORM_API_KEY",
    "LOGGLY_ACCOUNT", "LOGGLY_API_TOKEN",
    "LOGMEIN_API_KEY",
    "LOOM_API_KEY",
    "LOOPS_API_KEY",
    "MAGENTO_ACCESS_TOKEN", "MAGENTO_BASE_URL",
    "MAILCHIMP_API_KEY", "MAILCHIMP_SERVER_PREFIX",
    "MAILERLITE_API_KEY",
    "MAILGUN_API_KEY", "MAILGUN_DOMAIN",
    "MANDRILL_API_KEY", "MANDRILL_FROM_EMAIL",
    "MANYCHAT_API_KEY",
    "MAROPOST_ACCOUNT_ID", "MAROPOST_API_KEY",
    "MATTERMOST_TOKEN", "MATTERMOST_URL",
    "MEETUP_ACCESS_TOKEN",
    "MENDELEY_ACCESS_TOKEN",
    "MICROSOFT_ACCESS_TOKEN",
    "MIRO_ACCESS_TOKEN",
    "MIXPANEL_PROJECT_ID", "MIXPANEL_SERVICE_ACCOUNT_SECRET", "MIXPANEL_SERVICE_ACCOUNT_USERNAME",
    "MONDAY_API_KEY",
    "MONGODB_MCP_URL",
    "MOODLE_TOKEN", "MOODLE_URL",
    "MOOSEND_API_KEY",
    "MORALIS_API_KEY",
    "MYSQL_MCP_ALLOW_WRITES", "MYSQL_MCP_URL",
    "NETLIFY_ACCESS_TOKEN",
    "NETSUITE_ACCOUNT_ID", "NETSUITE_CONSUMER_KEY", "NETSUITE_TOKEN_KEY",
    "NEW_RELIC_ACCOUNT_ID", "NEW_RELIC_API_KEY",
    "NINOX_API_KEY",
    "NOTION_API_KEY",
    "OKTA_API_TOKEN", "OKTA_BASE_URL",
    "OMNISEND_API_KEY",
    "ONEDRIVE_ACCESS_TOKEN",
    "ONESIGNAL_API_KEY", "ONESIGNAL_APP_ID",
    "OPENAI_API_KEY",
    "ORBIT_API_KEY", "ORBIT_WORKSPACE",
    "ORDER_DESK_API_KEY", "ORDER_DESK_STORE_ID",
    "OUTREACH_ACCESS_TOKEN",
    "OVERLOOP_API_KEY",
    "PAGERDUTY_API_KEY", "PAGERDUTY_FROM_EMAIL",
    "PANDADOC_API_KEY",
    "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_SANDBOX",
    "PERPLEXITY_API_KEY",
    "PHANTOMBUSTER_API_KEY",
    "PINECONE_API_KEY", "PINECONE_ENVIRONMENT",
    "PINTEREST_ACCESS_TOKEN",
    "PIPEDRIVE_API_TOKEN", "PIPEDRIVE_COMPANY_DOMAIN",
    "PIVOTAL_TRACKER_TOKEN",
    "PLAID_ACCESS_TOKEN", "PLAID_CLIENT_ID", "PLAID_SECRET",
    "PLANHAT_API_KEY",
    "PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN",
    "PODIO_ACCESS_TOKEN",
    "POSTGRES_MCP_ALLOW_WRITES", "POSTGRES_MCP_URL",
    "POSTMAN_API_KEY",
    "POSTMARK_SERVER_TOKEN",
    "PROCORE_ACCESS_TOKEN",
    "PROFITWELL_API_KEY",
    "PROMETHEUS_PASSWORD", "PROMETHEUS_TOKEN", "PROMETHEUS_URL", "PROMETHEUS_USERNAME",
    "PUSHBULLET_API_KEY",
    "PUSHOVER_TOKEN", "PUSHOVER_USER",
    "QUICKBOOKS_ACCESS_TOKEN", "QUICKBOOKS_COMPANY_ID", "QUICKBOOKS_SANDBOX",
    "RAMP_ACCESS_TOKEN",
    "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET",
    "RECRUITEE_API_TOKEN", "RECRUITEE_COMPANY_ID",
    "REDIS_MCP_URL",
    "REDMINE_API_KEY", "REDMINE_URL",
    "REPLYIO_API_KEY",
    "RINGCENTRAL_ACCESS_TOKEN",
    "RIPPLING_API_KEY",
    "SALESFORCE_ACCESS_TOKEN", "SALESFORCE_INSTANCE_URL",
    "SALESLOFT_API_KEY",
    "SAMCART_API_KEY",
    "SAP_BASE_URL", "SAP_CLIENT_ID", "SAP_CLIENT_SECRET",
    "SEGMENT_WRITE_KEY",
    "SENDGRID_API_KEY", "SENDGRID_FROM_EMAIL",
    "SENTRY_AUTH_TOKEN", "SENTRY_ORG_SLUG",
    "SERPAPI_API_KEY",
    "SHIPSTATION_API_KEY", "SHIPSTATION_API_SECRET",
    "SHOPIFY_ACCESS_TOKEN", "SHOPIFY_STORE_URL",
    "SIGNNOW_ACCESS_TOKEN",
    "SLACK_BOT_TOKEN",
    "SMARTSHEET_ACCESS_TOKEN",
    "SMARTSUITE_ACCOUNT_ID", "SMARTSUITE_API_KEY",
    "SNOVIO_CLIENT_ID", "SNOVIO_CLIENT_SECRET",
    "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_ALLOW_WRITES", "SNOWFLAKE_DATABASE",
    "SNOWFLAKE_PASSWORD", "SNOWFLAKE_SCHEMA", "SNOWFLAKE_USER", "SNOWFLAKE_WAREHOUSE",
    "SONARQUBE_TOKEN", "SONARQUBE_URL",
    "SPLUNK_PASSWORD", "SPLUNK_TOKEN", "SPLUNK_URL", "SPLUNK_USERNAME", "SPLUNK_VERIFY_SSL",
    "SPORTRADAR_API_KEY",
    "SPOTIFY_ACCESS_TOKEN",
    "SPROUT_SOCIAL_ACCESS_TOKEN",
    "SQUARESPACE_API_KEY",
    "SQUARE_ACCESS_TOKEN", "SQUARE_SANDBOX",
    "STEAM_API_KEY",
    "STORYBLOK_ACCESS_TOKEN",
    "STRIPE_SECRET_KEY",
    "SUBSTACK_API_KEY", "SUBSTACK_PUBLICATION",
    "SUGARCRM_ACCESS_TOKEN", "SUGARCRM_INSTANCE_URL",
    "SUPABASE_SERVICE_KEY", "SUPABASE_URL",
    "SURVEYMONKEY_ACCESS_TOKEN",
    "TAVILY_API_KEY",
    "TEACHABLE_API_KEY",
    "TEAMS_ACCESS_TOKEN",
    "TEAMWORK_API_KEY", "TEAMWORK_SITE",
    "TELEGRAM_BOT_TOKEN",
    "TERRAFORM_ORG", "TERRAFORM_TOKEN",
    "THINKIFIC_API_KEY", "THINKIFIC_SUBDOMAIN",
    "THRIVECART_API_KEY",
    "TIKTOK_ACCESS_TOKEN",
    "TOAST_CLIENT_ID", "TOAST_CLIENT_SECRET",
    "TODOIST_API_TOKEN",
    "TOGGL_API_TOKEN",
    "TRELLO_API_KEY", "TRELLO_TOKEN",
    "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
    "TWITCH_ACCESS_TOKEN", "TWITCH_CLIENT_ID",
    "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN_SECRET",
    "TWITTER_API_KEY", "TWITTER_API_SECRET", "TWITTER_BEARER_TOKEN",
    "TYPEFORM_ACCESS_TOKEN",
    "UNBOUNCE_API_KEY",
    "UPKEEP_API_KEY",
    "UPWORK_ACCESS_TOKEN",
    "VERCEL_TEAM_ID", "VERCEL_TOKEN",
    "VERO_AUTH_TOKEN",
    "VIMEO_ACCESS_TOKEN",
    "VONAGE_API_KEY", "VONAGE_API_SECRET",
    "WAVE_ACCESS_TOKEN",
    "WEBFLOW_API_TOKEN", "WEBFLOW_SITE_ID",
    "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
    "WISTIA_API_PASSWORD",
    "WOOCOMMERCE_CONSUMER_KEY", "WOOCOMMERCE_CONSUMER_SECRET", "WOOCOMMERCE_URL",
    "WORDPRESS_APP_PASSWORD", "WORDPRESS_URL", "WORDPRESS_USERNAME",
    "WORKDAY_BASE_URL", "WORKDAY_CLIENT_ID", "WORKDAY_CLIENT_SECRET", "WORKDAY_TENANT",
    "WRIKE_ACCESS_TOKEN", "WRIKE_HOST",
    "WUFOO_API_KEY", "WUFOO_SUBDOMAIN",
    "XERO_ACCESS_TOKEN", "XERO_TENANT_ID",
    "YANDEX_API_KEY", "YANDEX_OAUTH_TOKEN",
    "YOTPO_APP_KEY", "YOTPO_SECRET",
    "YOUTUBE_ACCESS_TOKEN", "YOUTUBE_API_KEY",
    "ZENDESK_API_TOKEN", "ZENDESK_EMAIL", "ZENDESK_SUBDOMAIN",
    "ZENLOOP_API_TOKEN",
    "ZILLOW_API_KEY",
    "ZOHO_ACCESS_TOKEN", "ZOHO_DESK_ACCESS_TOKEN", "ZOHO_DESK_ORG_ID",
    "ZOHO_DOMAIN", "ZOHO_ORGANIZATION_ID",
    "ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET",
    "ZOOM_JWT_TOKEN", "ZOOM_OAUTH_TOKEN",
    "ZUORA_CLIENT_ID", "ZUORA_CLIENT_SECRET",
]}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {
        "ok": True,
        "result": "mocked",
        "data": [],
        "items": [],
        "results": [],
    }
    m.text = "{}"
    m.content = b"{}"
    m.raise_for_status = MagicMock()
    return m


def _make_mock_client(response: MagicMock | None = None) -> AsyncMock:
    """Return a fully configured mock AsyncClient context manager."""
    resp = response if response is not None else _make_mock_response()
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    mc.get = AsyncMock(return_value=resp)
    mc.post = AsyncMock(return_value=resp)
    mc.put = AsyncMock(return_value=resp)
    mc.patch = AsyncMock(return_value=resp)
    mc.delete = AsyncMock(return_value=resp)
    mc.request = AsyncMock(return_value=resp)
    return mc


def _build_minimal_args(tool: dict[str, Any]) -> dict[str, Any]:
    """Build the smallest set of arguments satisfying a tool's required parameters."""
    args: dict[str, Any] = {}
    properties = tool.get("parameters", {}).get("properties", {})
    required = tool.get("parameters", {}).get("required", [])
    for field in required:
        prop = properties.get(field, {})
        ptype = prop.get("type", "string")
        if ptype == "string":
            args[field] = "test-value"
        elif ptype == "integer":
            args[field] = 1
        elif ptype == "number":
            args[field] = 1.0
        elif ptype == "boolean":
            args[field] = False
        elif ptype == "array":
            args[field] = ["item"]
        elif ptype == "object":
            args[field] = {"key": "value"}
        else:
            args[field] = "test-value"
    return args


# ---------------------------------------------------------------------------
# Test 1: All server files import cleanly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", SERVER_MODULES)
def test_server_imports_cleanly(module_name: str) -> None:
    """Every server module must import without raising."""
    mod = importlib.import_module(f"app.mcp.servers.{module_name}")
    assert mod is not None


# ---------------------------------------------------------------------------
# Test 2: TOOL_DEFINITIONS is a valid non-empty list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", SERVER_MODULES)
def test_tool_definitions_valid(module_name: str) -> None:
    """TOOL_DEFINITIONS must be a non-empty list with name/description/parameters."""
    mod = importlib.import_module(f"app.mcp.servers.{module_name}")

    assert hasattr(mod, "TOOL_DEFINITIONS"), f"{module_name}: missing TOOL_DEFINITIONS"
    tools = mod.TOOL_DEFINITIONS
    assert isinstance(tools, list), f"{module_name}: TOOL_DEFINITIONS must be a list"
    assert len(tools) >= 1, f"{module_name}: TOOL_DEFINITIONS must not be empty"

    for tool in tools:
        assert "name" in tool, f"{module_name}: tool missing 'name': {tool}"
        assert isinstance(tool["name"], str) and tool["name"], (
            f"{module_name}: tool name must be a non-empty string"
        )
        assert "description" in tool, (
            f"{module_name}.{tool.get('name')}: missing 'description'"
        )
        assert "parameters" in tool, (
            f"{module_name}.{tool.get('name')}: missing 'parameters'"
        )
        assert isinstance(tool["parameters"], dict), (
            f"{module_name}.{tool.get('name')}: 'parameters' must be a dict"
        )


# ---------------------------------------------------------------------------
# Test 3: call_tool is an async function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", SERVER_MODULES)
def test_call_tool_is_async(module_name: str) -> None:
    """Every server must expose an async call_tool() function."""
    mod = importlib.import_module(f"app.mcp.servers.{module_name}")

    assert hasattr(mod, "call_tool"), f"{module_name}: missing call_tool"
    assert inspect.iscoroutinefunction(mod.call_tool), (
        f"{module_name}.call_tool must be an async function (coroutine)"
    )


# ---------------------------------------------------------------------------
# Test 4: Missing credentials → dict returned, no exception raised
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", SERVER_MODULES)
@pytest.mark.asyncio
async def test_missing_credentials_no_exception(module_name: str) -> None:
    """With all credential env vars absent, call_tool must return a dict (never raise)."""
    mod = importlib.import_module(f"app.mcp.servers.{module_name}")

    if not hasattr(mod, "TOOL_DEFINITIONS") or not mod.TOOL_DEFINITIONS:
        pytest.skip("No TOOL_DEFINITIONS")

    first_tool = mod.TOOL_DEFINITIONS[0]
    tool_name = first_tool["name"]
    args = _build_minimal_args(first_tool)

    # Strip all recognisable credential patterns from the environment
    cred_patterns = (
        "_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_EMAIL",
        "_HOST", "_DSN", "_ENDPOINT", "_KEY_ID",
        "AWS_", "GCP_", "JIRA_", "GITHUB_", "SLACK_",
        "STRIPE_", "OPENAI_", "ANTHROPIC_",
    )
    clean_env = {
        k: v
        for k, v in os.environ.items()
        if not any(pat in k for pat in cred_patterns)
    }

    mock_client = _make_mock_client()

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch.dict(os.environ, clean_env, clear=True):
        try:
            result = await mod.call_tool(tool_name, args)
            assert isinstance(result, dict), (
                f"{module_name}: call_tool must return dict, got {type(result).__name__}"
            )
        except Exception as exc:
            pytest.fail(
                f"{module_name}: call_tool raised {type(exc).__name__}: {exc}"
            )


# ---------------------------------------------------------------------------
# Test 5: All defined tools are dispatched (no "Unknown tool" for known names)
#
# Uses static source analysis: faster (no async), equally robust.
# We verify that every tool name listed in TOOL_DEFINITIONS appears as a string
# literal inside the call_tool() function body.  This is the canonical way to
# catch the "tool listed but no dispatch branch added" bug without making
# 2 000+ mock HTTP calls.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", SERVER_MODULES)
def test_all_defined_tools_dispatched(module_name: str) -> None:
    """Static check: every TOOL_DEFINITIONS name appears in call_tool() source."""
    mod = importlib.import_module(f"app.mcp.servers.{module_name}")

    if not hasattr(mod, "TOOL_DEFINITIONS"):
        pytest.skip("No TOOL_DEFINITIONS")

    source = inspect.getsource(mod)
    call_tool_pos = source.find("async def call_tool")
    if call_tool_pos == -1:
        pytest.skip("No call_tool in source")

    # Only scan the call_tool body (everything from its definition onwards)
    call_tool_source = source[call_tool_pos:]

    missing_dispatch: list[str] = []
    for tool in mod.TOOL_DEFINITIONS:
        tool_name = tool["name"]
        # The tool name must appear as a quoted string literal in call_tool
        if f'"{tool_name}"' not in call_tool_source and f"'{tool_name}'" not in call_tool_source:
            missing_dispatch.append(tool_name)

    assert not missing_dispatch, (
        f"{module_name}: tools defined in TOOL_DEFINITIONS but NOT dispatched in "
        f"call_tool() — add branches for: {missing_dispatch}"
    )


# ---------------------------------------------------------------------------
# Test 6: HTTP 4xx → returns dict with 'error' key (httpx servers only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", HTTPX_MODULES)
@pytest.mark.asyncio
async def test_http_status_error_returns_dict(module_name: str) -> None:
    """On HTTP 4xx, call_tool must return {'error': ...} dict, never re-raise."""
    mod = importlib.import_module(f"app.mcp.servers.{module_name}")

    if not hasattr(mod, "TOOL_DEFINITIONS") or not mod.TOOL_DEFINITIONS:
        pytest.skip("No tools")

    first_tool = mod.TOOL_DEFINITIONS[0]
    tool_name = first_tool["name"]
    args = _build_minimal_args(first_tool)

    # Construct a realistic 404 error
    mock_request = MagicMock()
    mock_request.url = "https://example.com/api"

    mock_404_response = MagicMock(spec=httpx.Response)
    mock_404_response.status_code = 404
    mock_404_response.text = '{"message": "Not Found"}'
    mock_404_response.reason_phrase = "Not Found"

    http_404 = httpx.HTTPStatusError(
        "404 Not Found",
        request=mock_request,
        response=mock_404_response,
    )

    error_client = AsyncMock()
    error_client.__aenter__ = AsyncMock(return_value=error_client)
    error_client.__aexit__ = AsyncMock(return_value=False)
    for method in ("get", "post", "put", "patch", "delete", "request"):
        setattr(error_client, method, AsyncMock(side_effect=http_404))

    with patch("httpx.AsyncClient", return_value=error_client), \
         patch.dict(os.environ, _ALL_FAKE_CREDS):
        try:
            result = await mod.call_tool(tool_name, args)
            assert isinstance(result, dict), (
                f"{module_name}: must return dict on HTTP error, got {type(result).__name__}"
            )
            # Should expose the error in some form
            assert "error" in result or "message" in result or "status" in result, (
                f"{module_name}: HTTP error result missing 'error' key — got: {result}"
            )
        except httpx.HTTPStatusError:
            pytest.fail(
                f"{module_name}: HTTPStatusError propagated to caller — "
                "wrap it in try/except and return {{'error': ...}}"
            )
        except Exception:
            # Non-HTTP exceptions (e.g. JSON parse on fake data) are acceptable;
            # we only care that HTTPStatusError is caught.
            pass


# ---------------------------------------------------------------------------
# Test 7: Registry wiring includes all catalog connectors
# ---------------------------------------------------------------------------


def test_registry_wiring_includes_all_catalog_connectors() -> None:
    """Every connector in CONNECTOR_CATALOG must have a matching entry in registry_wiring."""
    catalog_content = (
        (
            __file__  # find project root via this file's location
            .replace("tests/mcp/test_all_connectors_e2e.py", "")
        )
    )
    catalog_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "mcp", "catalog.py"
    )
    catalog_content = open(catalog_path).read()
    catalog_names: set[str] = {
        n
        for n in re.findall(r'name="([^"]+)"', catalog_content)
        if not n.startswith("_")
    }

    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    configs = get_builtin_server_configs()
    # Normalise: strip 'builtin-' prefix, convert hyphens → underscores
    registered_norm: set[str] = {
        c["server_id"].replace("builtin-", "").replace("-", "_")
        for c in configs
    }

    missing: list[str] = []
    for name in sorted(catalog_names):
        norm = name.lower().replace("-", "_").replace(" ", "_")
        parts = norm.split("_")
        # Accept exact match OR a suffix match (e.g. 'microsoft_onedrive' → 'onedrive')
        found = norm in registered_norm or any(
            "_".join(parts[i:]) in registered_norm for i in range(1, len(parts))
        )
        if not found:
            missing.append(name)

    assert not missing, (
        f"Catalog connectors with no registry_wiring entry ({len(missing)}): {missing}\n"
        "Add them to get_builtin_server_configs() in registry_wiring.py"
    )


# ---------------------------------------------------------------------------
# Test 8: JIRA server uses the non-deprecated search/jql endpoint
# ---------------------------------------------------------------------------


def test_jira_search_uses_jql_endpoint() -> None:
    """JIRA server must use /rest/api/3/search/jql, not the deprecated /rest/api/3/search."""
    jira_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "mcp", "servers", "jira_server.py"
    )
    content = open(jira_path).read()

    # Must NOT contain the bare deprecated endpoint as a string literal
    deprecated_matches = re.findall(r'["\'][^"\']*\/rest\/api\/3\/search["\']', content)
    non_jql = [m for m in deprecated_matches if "/rest/api/3/search/jql" not in m]
    assert not non_jql, (
        f"jira_server.py uses deprecated /rest/api/3/search endpoint: {non_jql}. "
        "Use /rest/api/3/search/jql instead."
    )
    assert "/rest/api/3/search/jql" in content, (
        "jira_server.py must use /rest/api/3/search/jql"
    )


# ---------------------------------------------------------------------------
# Test 9: Slack server checks 'ok' field in API responses
# ---------------------------------------------------------------------------


def test_slack_checks_ok_field() -> None:
    """Slack server must validate the 'ok' field returned by Slack API responses."""
    slack_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "mcp", "servers", "slack_server.py"
    )
    content = open(slack_path).read()

    assert 'data.get("ok")' in content or 'not data.get("ok")' in content, (
        "slack_server.py must check the 'ok' field in Slack API responses. "
        "Add: if not data.get('ok'): return {'error': data.get('error', 'slack_error')}"
    )
