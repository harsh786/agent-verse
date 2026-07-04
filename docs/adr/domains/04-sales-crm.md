# ADR-D04: Sales & CRM Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/04-sales-crm/use-cases.md

## 1. Market & Monetization
- **TAM:** Global CRM $96B (2024) → $262B (2032). Problem cost: B2B reps spend 65% of time on non-selling work, bad CRM data drives 27% revenue loss, 91% of CRM data is incomplete/inaccurate, forecasts wrong 79% of the time.
- **Buyer persona:** VP Sales / CRO / RevOps lead at $5M–100M ARR B2B firms; SDR-team leads for the bottom-of-funnel entry motion. Champion is a RevOps manager tired of CRM hygiene and forecast misses.
- **Pricing tiers (₹):**
  - **SDR Pack — ₹20,000/mo per 5 SDRs:** lead enrichment, email personalization, meeting follow-up; SF/HubSpot; 5,000 goals/mo.
  - **Sales Team — ₹75,000/mo (10-rep team):** + deal risk, forecast generation, win/loss analysis; Gong; 25,000 goals/mo; manager dashboard.
  - **Revenue Intelligence Platform — ₹3,00,000+/mo:** full suite + custom CRM integrations, commission automation; unlimited users; models trained on company win/loss history.
- **Consumption add-on model:** per-seat (₹25,000/mo/rep email personalization, ₹12,500/mo/rep follow-up) + usage (₹17,000/1,000 leads enriched) + intelligence modules (₹84,000/mo deal risk & win/loss, ₹1,25,000/mo forecast, ₹1,65,000/mo customer-success, ₹42,000/mo battle-cards & commission, ₹25,000/mo data hygiene).
- **Willingness-to-pay:** High — direct revenue attribution (response rate 2%→15%, win-rate +5–18%, forecast accuracy 21%→78%); reps and RevOps buy on pipeline lift, not cost savings.
- **Time-to-first-revenue:** ~2 weeks — CRM integration + lead enrichment live in weeks 1–2 (Bucket-1 SF/HubSpot).
- **Monetization note:** **Fast-cash Bucket-1 beachhead.** CRM read/write (salesforce, hubspot, pipedrive, zoho_crm), linkedin, web_search, email, slack, stripe, quickbooks, docusign, zendesk/intercom are all catalog. The higher tiers are **connector-gated on conversation intelligence (Gong/Chorus) and enrichment (Apollo)** New-API builds — expansion levers, not landing blockers.

## 2. Use Cases -> Product Mapping
| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|------------|---------------|-------|
| UC-1 | Intelligent Lead Enrichment & Scoring | Marketplace Agent | 1 | salesforce/hubspot, linkedin, web_search, slack; **New-API:** Apollo | High-volume-repetitive | No |
| UC-2 | Hyper-Personalized Outreach Email Writing | Both | 1 | hubspot/salesforce, linkedin, web_search, email; **New-API:** Salesloft/Outreach | High-volume-repetitive | No (optional rep review) |
| UC-3 | Meeting Follow-Up Automation | Both | 3 | salesforce/hubspot, google_calendar, email, slack; **New-API:** Gong/Chorus/Zoom | High-volume-repetitive | Yes (rep review before send) |
| UC-4 | Deal Risk Analysis & Early Warning System | Marketplace Agent | 1 | salesforce/hubspot, email, linkedin, slack; **New-API:** Gong | Real-time-monitoring | No |
| UC-5 | Competitor Intelligence Battle Cards | Marketplace Agent | 1 | web_search, confluence, slack; **New-API:** Gong; **RPA:** competitor sites (RPA-new) | Research+doc-gen | No |
| UC-6 | CRM Data Hygiene & Enrichment | Marketplace Agent | 1 | salesforce/hubspot, linkedin; **New-API:** Apollo, email-validation (HTTP) | High-volume-repetitive | Yes (merge/archive approval) |
| UC-7 | Sales Forecast Generation & Accuracy Improvement | Marketplace Agent | 1 | salesforce/hubspot, slack, google_sheets | Research+doc-gen | No |
| UC-8 | Customer Renewal Risk & Expansion Monitoring | Marketplace Agent | 1 | salesforce, zendesk/intercom, slack; **New-API:** Amplitude/Mixpanel | Real-time-monitoring | No |
| UC-9 | Quote & Proposal Generation | Both | 1 | salesforce/hubspot, docusign, pdf_generator, slack | Research+doc-gen | Yes (discount >15% approval) |
| UC-10 | Win/Loss Analysis & Sales Coaching | Marketplace Agent | 1 | salesforce, confluence, slack; **New-API:** Gong | Research+doc-gen | No |
| UC-11 | Commission Calculation Verification | Marketplace Agent | 1 | salesforce/hubspot, quickbooks, stripe, slack; **New-API:** HRIS payroll | Approval-gated | Yes (manager + finance approval) |
| UC-12 | Sales Content Personalization & Enablement | Both | 1 | salesforce, email, slack + knowledge base | Research+doc-gen | No |

## 3. Connectors Required
- **Existing (catalog · Bucket 1 · no build):** salesforce, hubspot, pipedrive, zoho_crm, linkedin, web_search, gmail/email, slack, stripe, quickbooks, xero, docusign, pandadoc, zendesk, intercom, google_sheets, google_calendar, google_docs, pdf_generator, document_reader.
- **New-API (build):** **Gong** (conversation intelligence — highest-value single build, gates UC-3/4/5/10 upsell), Chorus, Apollo (enrichment — UC-1/6), Salesloft/Outreach (sequence execution), Amplitude/Mixpanel (product usage for renewal risk), Notion (Confluence covers primary), email-validation API (via HTTP tool), HRIS payroll (for commission push — shared with HR domain).
- **RPA-portal (Bucket 2):** competitor website monitoring for battle cards (RPA-new — brittle, low priority; web_search covers most of UC-5).

## 4. Knowledge Collections
- `sales-playbooks` — discovery frameworks, objection-handling, sequence templates.
- `ideal-customer-profile` — ICP scoring criteria (size, industry, pain, budget signals) for UC-1.
- `win-loss-database` — historical closed-won/lost deals with reasons for UC-10/coaching and forecast win-rate models.
- `product-positioning` + `battle-cards` — value props, competitive differentiators (UC-2/5/12).
- `case-study-library` + proposal templates + pricing catalog & discount rules — for UC-9 proposals and UC-12 enablement.
- **Ingestion recipe:** ingest the customer's playbooks, case studies, pricing sheets, and closed-deal history from CRM + shared drive/Confluence; competitor and market intel fetched live via web_search; ingest customer-owned content only.

## 5. Guardrails & Compliance
- **Regulated:** Not statutorily regulated, but CRM data is PII (GDPR/CCPA) and commission touches payroll/financial accuracy; email outreach must respect CAN-SPAM/anti-spam and deliverability rules.
- **Mandatory HITL gates:** discount >15% (proposal), opportunity stage changes (`salesforce.update_opportunity_stage` → require_approval), commission statements (manager + finance approval before payroll push), merge/archive of CRM records, and follow-up email send (rep review, configurable auto-send).
- **Fail-closed policy:** `salesforce.delete*` denied — the agent never deletes CRM data (dedup/archive only, flagged for merge). Outreach passes a spam-trigger/banned-phrase check before queueing; commission cross-referenced against paid-invoice status in Stripe/QuickBooks before any statement is emitted.
- **Audit needs:** trail of every CRM write, stage change, sent email, and commission calculation for RevOps and finance dispute resolution; per-rep RLS so reps query only their own commission/pipeline.

## 6. Scale Pattern & Cost Drivers
- **Dominant shape:** High-volume-repetitive (lead enrichment, outreach, follow-up, hygiene) + Real-time-monitoring (deal risk, renewal) + scheduled Research+doc-gen (forecast, win/loss, battle-cards, proposals).
- **Expected goal volume:** high per-seat — 50+ leads/week enriched, 30 personalized emails/day/rep; SDR Pack 5,000/mo, Sales Team 25,000/mo caps reflect this.
- **Cost drivers:** research-heavy enrichment (web_search + LLM synthesis of 40+ data points/lead); per-email personalization tokens; transcript ingestion (Gong meetings can be long); daily pipeline/renewal scans across hundreds of accounts.
- **Caching/routing levers:** semantic cache on company-research (same accounts recur across leads); reuse enriched profile (UC-1) as input to UC-2/9/12 rather than re-researching; cheap model for scoring/classification, strong model for outreach copy and forecast reasoning; batch nightly hygiene and forecast runs; long-term memory of what subject lines/hooks convert per tenant.

## 7. Decision & Phasing
- **Flagship first:** **UC-1 Intelligent Lead Enrichment & Scoring** (paired with **UC-2 personalized outreach**) — the SDR Pack wedge, live weeks 1–2 on SF/HubSpot + LinkedIn + web_search, with Apollo as a fast New-API follow.
- **Fast-follows (Bucket-1):** UC-6 CRM data hygiene, UC-7 forecast generation, UC-8 renewal/expansion (zendesk/intercom), UC-9 proposals, UC-12 content enablement.
- **Connector-gated (build Gong first):** UC-3 meeting follow-up, UC-4 deal risk, UC-10 win/loss coaching (all lift materially with Gong transcripts); UC-8 deepens with Amplitude/Mixpanel; UC-11 commission needs the HRIS-payroll connector.

## 8. KPIs
- **Adoption:** leads enriched/week; % of outreach sent via agent; deals under risk-monitoring; accounts under renewal-monitoring; proposals generated via agent.
- **Reliability:** `deal-prediction-accuracy-eval` pass %; forecast accuracy (target 21%→78%); enrichment field-fill accuracy; outreach spam-check catch rate; commission-calc discrepancy rate.
- **Cost/goal:** tokens per enriched lead / per personalized email; transcript-processing cost per meeting; scan cost per account/day.
- **Revenue/tenant:** seat MRR + intelligence-module MRR + per-lead usage; SDR Pack→Sales Team→RevIntel expansion (Gong adoption = tier-up trigger); pipeline/win-rate lift attributable to the agent (the real retention driver).
