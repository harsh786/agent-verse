# Zapier & Multi-Domain Integrations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ~200 missing integrations from the Zapier list plus 30 additional domain integrations (healthcare, legal, fintech, IoT, gaming, travel, etc.) as MCP servers — world-class, with full test coverage.

**Architecture:** Each integration is a standalone Python module in `app/mcp/servers/` following the `TOOL_DEFINITIONS + call_tool()` pattern. All integrations are registered in `registry_wiring.py` and catalogued in `catalog.py`. E2E Playwright tests verify the connector catalog and marketplace show all integrations.

**Tech Stack:** Python 3.12, httpx, FastAPI, pytest, Playwright/vitest for E2E

---

## Current State
- 117 server files exist in `app/mcp/servers/`
- 150 entries in `registry_wiring.py`
- 32 entries in `catalog.py`
- Missing: ~200 Zapier integrations + 30 new domain integrations

## Server Pattern (MUST follow exactly)

```python
"""<Name> MCP server — <description>.

Environment:
  <ENV_VAR>: <description>
"""
from __future__ import annotations
import os
from typing import Any
import httpx
from app.observability.logging import get_logger

logger = get_logger(__name__)
BASE_URL = "https://api.example.com/v1"

TOOL_DEFINITIONS = [
    {
        "name": "<service>_<action>",
        "description": "<clear description of what this tool does>",
        "parameters": {
            "type": "object",
            "properties": {
                "<param>": {"type": "string", "description": "<desc>"},
            },
            "required": ["<required_params>"],
        },
    },
]

async def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    api_key = os.getenv("<ENV_VAR>", "")
    if not api_key:
        return {"error": "<ENV_VAR> not configured"}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if tool_name == "<service>_<action>":
                r = await client.get(f"{BASE_URL}/endpoint",
                    headers={"Authorization": f"Bearer {api_key}"})
                r.raise_for_status()
                return r.json()
            return {"error": f"Unknown tool: {tool_name}"}
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}
```

## Registry Entry Pattern (append to `registry_wiring.py`)

```python
{
    "server_id": "builtin-<slug>",
    "name": "<DisplayName>",
    "description": "<Name> — <what it does>",
    "tool_definitions": <module>.TOOL_DEFINITIONS,
    "handler": <module>.call_tool,
    "requires_env": ["<ENV_VAR>"],
},
```

## Catalog Entry Pattern (append to `catalog.py`)

```python
ConnectorSpec(
    name="<slug>",
    description="<Name> — <brief description>",
    auth_type="bearer",  # or "api_key", "oauth_ac", etc.
    default_url="https://api.example.com",
    icon="<slug>",
),
```

---

## Batch 1: Email Marketing & Communication (20 integrations)

**Files to create:**
- `app/mcp/servers/activecampaign_server.py`
- `app/mcp/servers/aweber_server.py`
- `app/mcp/servers/campaign_monitor_server.py`
- `app/mcp/servers/constant_contact_server.py`
- `app/mcp/servers/drip_server.py`
- `app/mcp/servers/getresponse_server.py`
- `app/mcp/servers/mailgun_server.py`
- `app/mcp/servers/moosend_server.py`
- `app/mcp/servers/omnisend_server.py`
- `app/mcp/servers/loops_server.py`
- `app/mcp/servers/manychat_server.py`
- `app/mcp/servers/onesignal_server.py`
- `app/mcp/servers/pushover_server.py`
- `app/mcp/servers/pushbullet_server.py`
- `app/mcp/servers/postmark_server.py`
- `app/mcp/servers/ringcentral_server.py`
- `app/mcp/servers/plivo_server.py`
- `app/mcp/servers/vonage_server.py`
- `app/mcp/servers/twitch_server.py`
- `app/mcp/servers/gmail_server.py`

- [ ] **Step 1:** Create all 20 server files with 5+ tools each
- [ ] **Step 2:** Run `uv run pytest tests/mcp/test_batch1_servers.py -v` — PASS
- [ ] **Step 3:** Add all 20 to `registry_wiring.py` and `catalog.py`
- [ ] **Step 4:** Commit: `feat(mcp): add 20 email/comms integrations (batch 1)`

---

## Batch 2: CRM, Sales & Marketing (25 integrations)

**Files to create:**
- `app/mcp/servers/airtable_server.py`
- `app/mcp/servers/capsule_crm_server.py`
- `app/mcp/servers/clearbit_server.py`
- `app/mcp/servers/dynamics365_server.py`
- `app/mcp/servers/encharge_server.py`
- `app/mcp/servers/freshsales_server.py`
- `app/mcp/servers/fullcontact_server.py`
- `app/mcp/servers/gainsight_server.py`
- `app/mcp/servers/highlevel_server.py`
- `app/mcp/servers/hubspot_marketing_server.py`
- `app/mcp/servers/insightly_server.py`
- `app/mcp/servers/klenty_server.py`
- `app/mcp/servers/konnektive_server.py`
- `app/mcp/servers/leadpages_server.py`
- `app/mcp/servers/lemlist_server.py`
- `app/mcp/servers/outreach_server.py`
- `app/mcp/servers/overloop_server.py`
- `app/mcp/servers/reply_io_server.py`
- `app/mcp/servers/salesloft_server.py`
- `app/mcp/servers/segment_server.py`
- `app/mcp/servers/snovio_server.py`
- `app/mcp/servers/sugarcRM_server.py`
- `app/mcp/servers/vero_server.py`
- `app/mcp/servers/podio_server.py`
- `app/mcp/servers/orbit_server.py`

- [ ] **Step 1:** Create all 25 server files with 5+ tools each
- [ ] **Step 2:** Run `uv run pytest tests/mcp/test_batch2_servers.py -v` — PASS
- [ ] **Step 3:** Add all 25 to `registry_wiring.py` and `catalog.py`
- [ ] **Step 4:** Commit: `feat(mcp): add 25 CRM/sales/marketing integrations (batch 2)`

---

## Batch 3: Project Management, HR & Finance (25 integrations)

**Files to create:**
- `app/mcp/servers/clockify_server.py`
- `app/mcp/servers/toggl_server.py`
- `app/mcp/servers/harvest_server.py`
- `app/mcp/servers/greenhouse_server.py`
- `app/mcp/servers/recruitee_server.py`
- `app/mcp/servers/gusto_server.py`
- `app/mcp/servers/freshbooks_server.py`
- `app/mcp/servers/wave_server.py`
- `app/mcp/servers/invoice_ninja_server.py`
- `app/mcp/servers/netsuite_server.py`
- `app/mcp/servers/braintree_server.py`
- `app/mcp/servers/samcart_server.py`
- `app/mcp/servers/profitwell_server.py`
- `app/mcp/servers/zuora_server.py`
- `app/mcp/servers/zoho_books_server.py`
- `app/mcp/servers/zoho_invoice_server.py`
- `app/mcp/servers/teamwork_server.py`
- `app/mcp/servers/hive_server.py`
- `app/mcp/servers/pivotal_tracker_server.py`
- `app/mcp/servers/redmine_server.py`
- `app/mcp/servers/procore_server.py`
- `app/mcp/servers/ninox_server.py`
- `app/mcp/servers/knack_server.py`
- `app/mcp/servers/smartsheets_server.py`
- `app/mcp/servers/miro_server.py`

- [ ] **Step 1:** Create all 25 server files with 5+ tools each
- [ ] **Step 2:** Run `uv run pytest tests/mcp/test_batch3_servers.py -v` — PASS
- [ ] **Step 3:** Add all 25 to `registry_wiring.py` and `catalog.py`
- [ ] **Step 4:** Commit: `feat(mcp): add 25 PM/HR/finance integrations (batch 3)`

---

## Batch 4: E-commerce, Social & Content (25 integrations)

**Files to create:**
- `app/mcp/servers/etsy_server.py`
- `app/mcp/servers/ebay_server.py`
- `app/mcp/servers/ecwid_server.py`
- `app/mcp/servers/magento_server.py`
- `app/mcp/servers/squarespace_server.py`
- `app/mcp/servers/lightspeed_server.py`
- `app/mcp/servers/shipstation_server.py`
- `app/mcp/servers/order_desk_server.py`
- `app/mcp/servers/yotpo_server.py`
- `app/mcp/servers/gumroad_server.py`
- `app/mcp/servers/kajabi_server.py`
- `app/mcp/servers/teachable_server.py`
- `app/mcp/servers/thinkific_server.py`
- `app/mcp/servers/substack_server.py`
- `app/mcp/servers/storyblok_server.py`
- `app/mcp/servers/vimeo_server.py`
- `app/mcp/servers/wistia_server.py`
- `app/mcp/servers/spotify_server.py`
- `app/mcp/servers/pinterest_server.py`
- `app/mcp/servers/hootsuite_server.py`
- `app/mcp/servers/sprout_social_server.py`
- `app/mcp/servers/buffer_server.py`
- `app/mcp/servers/facebook_pages_server.py`
- `app/mcp/servers/facebook_lead_ads_server.py`
- `app/mcp/servers/facebook_conversions_server.py`

- [ ] **Step 1:** Create all 25 server files with 5+ tools each
- [ ] **Step 2:** Run `uv run pytest tests/mcp/test_batch4_servers.py -v` — PASS
- [ ] **Step 3:** Add all 25 to `registry_wiring.py` and `catalog.py`
- [ ] **Step 4:** Commit: `feat(mcp): add 25 e-commerce/social/content integrations (batch 4)`

---

## Batch 5: Dev Tools, Cloud & Infrastructure (25 integrations)

**Files to create:**
- `app/mcp/servers/amazon_ses_server.py`
- `app/mcp/servers/amazon_sqs_server.py`
- `app/mcp/servers/apache_kafka_server.py`
- `app/mcp/servers/bigquery_server.py`
- `app/mcp/servers/cloudflare_server.py`
- `app/mcp/servers/cloudinary_server.py`
- `app/mcp/servers/firebase_server.py`
- `app/mcp/servers/figma_server.py`
- `app/mcp/servers/filestack_server.py`
- `app/mcp/servers/loom_server.py`
- `app/mcp/servers/sonarqube_server.py`
- `app/mcp/servers/sentry_advanced_server.py`
- `app/mcp/servers/bitly_server.py`
- `app/mcp/servers/typeform_server.py`
- `app/mcp/servers/jotform_server.py`
- `app/mcp/servers/surveymonkey_server.py`
- `app/mcp/servers/formstack_server.py`
- `app/mcp/servers/signnow_server.py`
- `app/mcp/servers/docusign_advanced_server.py`
- `app/mcp/servers/evernote_server.py`
- `app/mcp/servers/microsoft_excel_server.py`
- `app/mcp/servers/microsoft_outlook_server.py`
- `app/mcp/servers/microsoft_onenote_server.py`
- `app/mcp/servers/microsoft_todo_server.py`
- `app/mcp/servers/google_forms_server.py`

- [ ] **Step 1:** Create all 25 server files with 5+ tools each
- [ ] **Step 2:** Run `uv run pytest tests/mcp/test_batch5_servers.py -v` — PASS
- [ ] **Step 3:** Add all 25 to `registry_wiring.py` and `catalog.py`
- [ ] **Step 4:** Commit: `feat(mcp): add 25 dev-tools/cloud/infra integrations (batch 5)`

---

## Batch 6: Analytics, AI & Monitoring (20 integrations)

**Files to create:**
- `app/mcp/servers/databox_server.py`
- `app/mcp/servers/geckoboard_server.py`
- `app/mcp/servers/elevenlabs_server.py`
- `app/mcp/servers/gemini_server.py`
- `app/mcp/servers/fireflies_server.py`
- `app/mcp/servers/phantombuster_server.py`
- `app/mcp/servers/clearbit_enrichment_server.py`
- `app/mcp/servers/google_slides_server.py`
- `app/mcp/servers/google_tasks_server.py`
- `app/mcp/servers/google_contacts_server.py`
- `app/mcp/servers/google_chat_server.py`
- `app/mcp/servers/google_meet_server.py`
- `app/mcp/servers/google_my_business_server.py`
- `app/mcp/servers/google_photos_server.py`
- `app/mcp/servers/gotowebinar_server.py`
- `app/mcp/servers/livestorm_server.py`
- `app/mcp/servers/eventbrite_server.py`
- `app/mcp/servers/meetup_server.py`
- `app/mcp/servers/zenloop_server.py`
- `app/mcp/servers/delighted_server.py`

- [ ] **Step 1:** Create all 20 server files with 5+ tools each
- [ ] **Step 2:** Run `uv run pytest tests/mcp/test_batch6_servers.py -v` — PASS
- [ ] **Step 3:** Add all 20 to `registry_wiring.py` and `catalog.py`
- [ ] **Step 4:** Commit: `feat(mcp): add 20 analytics/AI/monitoring integrations (batch 6)`

---

## Batch 7: New Domain Integrations (35 integrations)

New domains beyond Zapier list:

**Healthcare:**
- `app/mcp/servers/epic_fhir_server.py` — Epic FHIR R4 patient data
- `app/mcp/servers/athenahealth_server.py` — Athenahealth EHR
- `app/mcp/servers/drchrono_server.py` — DrChrono EHR

**Legal:**
- `app/mcp/servers/clio_server.py` — Clio legal practice management
- `app/mcp/servers/harvey_server.py` — Harvey AI legal research
- `app/mcp/servers/docassemble_server.py` — Document assembly

**Finance & Banking:**
- `app/mcp/servers/plaid_server.py` — Plaid bank account linking
- `app/mcp/servers/alpaca_server.py` — Alpaca trading API
- `app/mcp/servers/brex_server.py` — Brex corporate cards
- `app/mcp/servers/ramp_server.py` — Ramp spend management

**Real Estate:**
- `app/mcp/servers/zillow_server.py` — Zillow property data
- `app/mcp/servers/buildium_server.py` — Buildium property management
- `app/mcp/servers/yardi_server.py` — Yardi property management

**Education:**
- `app/mcp/servers/canvas_lms_server.py` — Canvas LMS
- `app/mcp/servers/moodle_server.py` — Moodle e-learning
- `app/mcp/servers/coursera_server.py` — Coursera courses

**IoT & Hardware:**
- `app/mcp/servers/home_assistant_server.py` — Home Assistant
- `app/mcp/servers/aws_iot_server.py` — AWS IoT Core
- `app/mcp/servers/particle_server.py` — Particle IoT platform

**Gaming:**
- `app/mcp/servers/steam_server.py` — Steam game data
- `app/mcp/servers/twitch_advanced_server.py` — Twitch streams/clips
- `app/mcp/servers/epicgames_server.py` — Epic Games store

**Travel & Hospitality:**
- `app/mcp/servers/amadeus_server.py` — Amadeus flight/hotel search
- `app/mcp/servers/expedia_server.py` — Expedia travel data
- `app/mcp/servers/booking_server.py` — Booking.com property API

**Food & Restaurant:**
- `app/mcp/servers/doordash_server.py` — DoorDash Drive delivery
- `app/mcp/servers/toast_pos_server.py` — Toast POS restaurant system
- `app/mcp/servers/olo_server.py` — Olo restaurant ordering

**Blockchain & Web3:**
- `app/mcp/servers/alchemy_server.py` — Alchemy blockchain data
- `app/mcp/servers/moralis_server.py` — Moralis Web3 API
- `app/mcp/servers/opensea_server.py` — OpenSea NFT marketplace

**Sports & Media:**
- `app/mcp/servers/sportradar_server.py` — Sportradar sports data
- `app/mcp/servers/espn_server.py` — ESPN sports API
- `app/mcp/servers/ap_news_server.py` — AP News feed

**Manufacturing & Supply Chain:**
- `app/mcp/servers/flexport_server.py` — Flexport logistics
- `app/mcp/servers/sap_server.py` — SAP ERP

- [ ] **Step 1:** Create all 35 new domain server files with 5+ tools each
- [ ] **Step 2:** Run `uv run pytest tests/mcp/test_batch7_servers.py -v` — PASS
- [ ] **Step 3:** Add all 35 to `registry_wiring.py` and `catalog.py`
- [ ] **Step 4:** Commit: `feat(mcp): add 35 new-domain integrations (batch 7)`

---

## Batch 8: Remaining Zapier Integrations (30 integrations)

- `autopilot_server.py`, `acoustic_server.py`, `appsheet_server.py`
- `anvil_server.py`, `beds24_server.py`, `channable_server.py`
- `chatfuel_server.py`, `cincopa_server.py`, `clickfunnels_server.py`
- `cognito_forms_server.py`, `criteo_server.py`, `customgpt_server.py`
- `digistore24_server.py`, `egoi_server.py`, `easywebinar_server.py`
- `elavon_server.py`, `emarsys_server.py`, `emma_server.py`
- `esputnik_server.py`, `feedly_server.py`, `fitbit_server.py`
- `gleam_server.py`, `gravity_forms_server.py`, `gust_server.py`
- `koala_server.py`, `logmein_server.py`, `maropost_server.py`
- `mendeley_server.py`, `upkeep_server.py`, `yandex_server.py`

- [ ] **Step 1:** Create all 30 server files with 5+ tools each
- [ ] **Step 2:** Run `uv run pytest tests/mcp/test_batch8_servers.py -v` — PASS
- [ ] **Step 3:** Add all 30 to `registry_wiring.py` and `catalog.py`
- [ ] **Step 4:** Commit: `feat(mcp): add 30 remaining zapier integrations (batch 8)`

---

## Task 9: Update Connector Catalog (catalog.py)

**File:** `app/mcp/catalog.py`

- [ ] Add all new `ConnectorSpec` entries for all new servers (batches 1-8)
- [ ] Group by category in comments
- [ ] Run `uv run pytest tests/mcp/test_catalog.py -v` — PASS
- [ ] Commit: `feat(mcp): update connector catalog with 250+ integrations`

---

## Task 10: Marketplace Templates

**File:** `app/enterprise/marketplace.py` or seed data

Add marketplace agent templates for key use cases per new integration domain:
- Healthcare: "Sync patient appointments to Google Calendar"
- Legal: "Draft NDA and send via DocuSign from Clio matter"
- Finance: "Monitor Plaid transactions and alert on anomalies"
- E-commerce: "Sync Etsy orders to ShipStation and notify via Slack"
- Real Estate: "Send Zillow leads to CRM and schedule follow-up"
- Education: "Create Canvas assignments from Google Docs"

- [ ] Add 50 new marketplace templates
- [ ] Test template deployment
- [ ] Commit: `feat(marketplace): add 50 templates for new integrations`

---

## Task 11: E2E Tests (Playwright)

**File:** `agent-verse-frontend/e2e/connectors-catalog.spec.ts`

```typescript
test('connector catalog shows 250+ integrations', async ({ page }) => {
  await setupAuth(page);
  await page.goto('/connectors/catalog');
  const cards = page.locator('[data-testid="catalog-card"]');
  await expect(cards).toHaveCountGreaterThan(250);
});

test('can register any integration from catalog', async ({ page }) => {
  // test registering ActiveCampaign
  await page.goto('/connectors/catalog');
  await page.getByText('ActiveCampaign').click();
  await expect(page.getByText('Register')).toBeVisible();
});
```

**File:** `agent-verse-backend/tests/mcp/test_all_integrations.py`

```python
@pytest.mark.parametrize("server_id", get_all_server_ids())
def test_server_has_tool_definitions(server_id):
    configs = {c["server_id"]: c for c in get_builtin_server_configs()}
    assert server_id in configs
    assert len(configs[server_id]["tool_definitions"]) >= 3
```

- [ ] Write all E2E tests
- [ ] Run full test suite — all pass
- [ ] Commit: `test(e2e): comprehensive connector catalog and integration tests`

---

## Task 12: Backend Unit Tests Per Batch

For each batch, create `tests/mcp/test_batch<N>_servers.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_activecampaign_list_contacts():
    from app.mcp.servers.activecampaign_server import call_tool
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=AsyncMock(
                status_code=200,
                json=lambda: {"contacts": [{"id": "1", "email": "test@test.com"}]},
                raise_for_status=lambda: None,
            )
        )
        with patch.dict("os.environ", {"ACTIVECAMPAIGN_API_KEY": "test", "ACTIVECAMPAIGN_BASE_URL": "https://test.api-us1.com"}):
            result = await call_tool("activecampaign_list_contacts", {"limit": 10})
    assert "contacts" in result or "error" not in result
```

Each batch test file tests every server in that batch.

---

## Summary

| Batch | Integrations | Status |
|-------|-------------|--------|
| 1 | Email/Comms (20) | - [ ] |
| 2 | CRM/Sales (25) | - [ ] |
| 3 | PM/HR/Finance (25) | - [ ] |
| 4 | E-commerce/Social (25) | - [ ] |
| 5 | Dev/Cloud/Infra (25) | - [ ] |
| 6 | Analytics/AI (20) | - [ ] |
| 7 | New Domains (35) | - [ ] |
| 8 | Remaining Zapier (30) | - [ ] |
| 9 | Catalog update | - [ ] |
| 10 | Marketplace templates | - [ ] |
| 11 | E2E tests | - [ ] |
| 12 | Unit tests per batch | - [ ] |

**Total new integrations: ~235 + 35 new domains = ~270 integrations**
**Final total: 117 existing + 270 new = ~387 integrations**
