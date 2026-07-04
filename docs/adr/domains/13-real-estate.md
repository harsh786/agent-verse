# ADR-D13: Real Estate & PropTech Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/13-real-estate/use-cases.md

## 1. Market & Monetization
- **TAM:** Indian real estate is ₹26 lakh crore, growing ~10%/year, running on WhatsApp groups and Excel. Serviceable base: developers (100+ unit projects), large brokerages/agencies, property-management companies, RWAs/HOAs (200–5,000 units), co-living operators, and property investors/family offices (5+ properties).
- **Buyer:** Developer sales/marketing head, property-management company owner, RWA committee, individual/family-office investor, commercial landlord.
- **3 Tiers (₹):**
  - Tier 1 — Property Manager Starter: **₹5,000/month** (listing generation, lead follow-up, rent reminders; up to 50 units/properties).
  - Tier 2 — Developer Pro: **₹30,000/month** (full suite: lead qualification, tenant onboarding, RERA compliance, commission tracking; up to 500 units; CRM integration).
  - Tier 3 — PropTech Enterprise: **₹1,20,000+/month** (full platform + portfolio analytics; white-label for large developers; unlimited units; custom portal integrations).
- **Consumption model:** Hybrid. Per-unit/per-event pricing (₹2,000/listing or valuation, ₹5,000/tenant onboarding, ₹15,000 RERA registration + ₹5,000/quarter reporting, ₹8,000/renewal briefing, ₹75,000/project launch) plus per-unit-per-month recurring (₹50/unit rent collection, ₹100/unit maintenance, ₹20/unit RWA) layered on tier subscriptions.
- **WTP:** Strong on revenue-recovery flows — lead follow-up (5%→12% conversion = 6 extra bookings/month at ₹50K–5L commission each), lease-renewal intelligence (8–18% rent uplift that compounds every escalation cycle), RERA penalty avoidance (₹3–10L/year/project), and launch orchestration (35–50% more first-72h inquiries, ₹5–25cr velocity swing). Property-management recurring is sticky but low-ACV per unit.
- **Time-to-first-revenue:** **Medium.** Bucket-1 for lead/rent/maintenance/onboarding (WhatsApp, Razorpay, DocuSign, email, CRM). **RERA portal + property-listing portals + sub-registrar (SRO) data are RPA-portal builds** that gate the compliance and listing/valuation UCs.
- **Monetization note:** Land property managers on the ₹5,000 Starter (rent + leads + maintenance — near-zero integration), then expand developers into RERA compliance and launch orchestration (high-ACV, RPA-gated). Lease-renewal intelligence (UC-11) is the standout premium module for commercial landlords/family offices — sell as a ₹40,000/month portfolio add-on.

## 2. Use Cases -> Product Mapping
| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|-----------|---------------|-------|
| UC-1 | Property Listing Generation Across Portals | Both | 2 | MagicBricks/99acres/Housing.com (RPA-portal), web_search, email, pdf_generator | Research+doc-gen | No |
| UC-2 | Lead Qualification & Follow-Up Nurturing | Agent | 1 | whatsapp, email, hubspot, salesforce, slack, google_calendar | Real-time-monitoring | No |
| UC-3 | Rent Collection & Escalation Automation | Agent | 1 | razorpay (RazorpayX), whatsapp, email, accounting (RPA-new), pdf_generator | High-volume-repetitive | No |
| UC-4 | Tenant Onboarding & Agreement Execution | Agent | 2 | docusign (new-API), razorpay, digilocker (RPA-portal, KYC), police-verif + e-registration portals (RPA-portal), email, whatsapp | Approval-gated | No |
| UC-5 | Maintenance Request Routing & Resolution | Agent | 1 | whatsapp, email, pdf_generator, razorpay | Real-time-monitoring | Yes (invoice >₹5,000) |
| UC-6 | Property Valuation Analysis | Both | 2 | web_search, SRO/registration portals (RPA-portal), pdf_generator | Research+doc-gen | No |
| UC-7 | RERA Compliance Documentation | Both | 2 | rera_portal (RPA-portal), pdf_generator, accounting (RPA-new), email | Approval-gated | Yes (filing) |
| UC-8 | Broker Commission Tracking & Payment | Agent | 1 | salesforce, hubspot, accounting (RPA-new), email, pdf_generator | Approval-gated | Yes (project-head approval) |
| UC-9 | Society/HOA Management & Communication | Both | 1 | whatsapp, razorpay, accounting (RPA-new), pdf_generator, email | High-volume-repetitive | Yes (vendor payments) |
| UC-10 | Property Portfolio Performance Reporting | Both | 1 | google_sheets, accounting (RPA-new), web_search, email, pdf_generator | Research+doc-gen | No |
| UC-11 | Lease Renewal Negotiation Intelligence | Agent | 2 | web_search, MagicBricks/99acres/JLL/Anarock + SRO (RPA-portal), pdf_generator, docusign, whatsapp, email, google_sheets | Research+doc-gen | Yes (briefing/counter approval) |
| UC-12 | New Project Launch Campaign Orchestration | Agent | 2 | MagicBricks/99acres/Housing.com/PropTiger (RPA-portal), whatsapp, mailchimp, buffer/hootsuite (RPA-new), google_calendar, sms, pdf_generator, slack, google_sheets | Real-time-monitoring | No |

## 3. Connectors Required
- **Existing (Bucket 1, reuse):** whatsapp, gmail/email, razorpay (+RazorpayX), salesforce, hubspot, google_sheets, google_calendar, web_search, pdf_generator, sms/twilio, mailchimp, slack.
- **New-API (build, low-medium):** docusign (e-signature — documented API, ~3 days), generic accounting/Tally/Zoho adapter (varies per client), buffer/hootsuite social scheduler.
- **RPA-portal (build, medium-high, gating):** **rera_portal** (Form A registration + quarterly progress filing + escrow 70% reconciliation — highest-compliance), property-listing portals **MagicBricks / 99acres / Housing.com / PropTiger / NoBroker** (listing publish + view/inquiry scraping — used by UC-1, UC-11, UC-12), **JLL / Anarock / Cushman** report scraping (comparables for UC-11), **SRO/sub-registrar** transaction-data portals (valuation UC-6, renewal UC-11), **digilocker** (tenant KYC UC-4), police-verification + e-registration state portals (UC-4). Each portal flow ~1.5–2.5 weeks; listing-portal set is reused across three UCs so amortizes well.
- **Build cost summary:** Bucket-1 property-management pack (UC-2/3/5/8/9/10) ships fast. The RPA-portal set (RERA + listing portals + SRO + DigiLocker) is the gating build for listing, valuation, RERA, tenant onboarding, renewal, and launch — front-load the shared listing-portal RPA since it powers UC-1, UC-11, and UC-12.

## 4. Knowledge Collections
- **Seed slugs:** `lease-templates` (state-specific, rent-control-compliant), `property-details`, `vendor-directory` (approved vendors + rate cards), `rera-forms-and-rules` (state-wise filing schemas + deadlines), `portal-field-schemas` (per listing portal char limits/required fields/image specs), `micro-market-comparables` (rent/sale by locality + building grade), `commission-schedules`, `society-bylaws-amc-schedule`, `area-infrastructure-signals` (metro/road/commercial development).
- **Ingestion recipe:** (1) Ingest client's lease templates + property master into `KnowledgeStore`; validate lease clauses against `rera-forms-and-rules` for prohibited-clause detection (UC-4). (2) Weekly RPA scrape of listing portals + SRO registrations refreshes `micro-market-comparables` for valuation/renewal percentile math (UC-6, UC-11). (3) Load per-portal field schemas so listing generation formats correctly per portal (UC-1, UC-12). (4) `SemanticCache` dedupes repeated area-research and comparable-lookup LLM calls across a portfolio.

## 5. Guardrails & Compliance
- **Regime:** RERA (state authorities — mandatory registration + quarterly filing, escrow 70% rule), state Rent Control Acts + Model Tenancy Act (lease-clause legality), stamp-duty/registration law (agreements >11 months), TDS on commission (Form 16A) and rent, DPDP Act 2023 (tenant/lead KYC PII), DigiLocker consent framework.
- **HITL gates:** maintenance invoice >₹5,000 (UC-5); RERA filing before portal submission (UC-7); project-head commission approval with full attribution trail (UC-8); RWA vendor payments (UC-9); landlord approval of negotiation briefing + each counter-offer (UC-11); legal-notice generation (`document.generate_legal_notice` → `require_approval` per sample manifest).
- **Fail-closed:** RERA agent never auto-files without human sign-off (penalty + legal exposure); lease-generation agent blocks agreements containing prohibited clauses (rent-control violations, illegal eviction) — hard `deny` in policy engine; commission payment instruction requires project-head approval before finance dispatch; escrow reconciliation flags (not auto-corrects) sub-70% balances. DigiLocker document pull requires explicit tenant consent.
- **Audit:** append-only trail (`governance/audit.py`) for every RERA filing, rent transaction, commission payment, lease execution, and legal notice with timestamp + notice-period compliance record (UC-11 dispatch timestamps). DPDP: PII masking in logs, consent tracked for WhatsApp/marketing blasts.

## 6. Scale Pattern & Cost Drivers
- **Dominant patterns:** Real-time-monitoring (lead response <5 min, maintenance routing, launch first-72h surge), High-volume-repetitive (rent reminders, RWA billing across units), Research+doc-gen (listing generation, valuation CMA, renewal briefing, portfolio reports), Approval-gated (RERA, commission, onboarding).
- **Cost drivers:** (1) RPA-portal fragility and rate limits — listing-portal scraping/publishing and SRO data pulls are the operational risk, not LLM cost. (2) Launch orchestration (UC-12) is a burst of 15+ parallel tasks firing at T-0 — needs reliable scheduling + dedup (`services/dedup.py`) to avoid double-posting. (3) Web-search + comparable-analysis token volume for valuation/renewal (percentile math offloaded to code execution, not LLM). (4) WhatsApp broadcast volume for broker/lead/RWA blasts — rate-limit per tenant. Route Enterprise developers to dedicated Celery queues during launch windows.
- **Optimizations in tree:** `mcp/tool_cache.py` (comparable/portal lookups), `rag/llm_response_cache.py`, `prompt_compressor.py`, `model_router` (Haiku for routing/reminders, Sonnet for listing/briefing generation).

## 7. Decision & Phasing
- **Decision:** Adopt real estate as a **Phase-1/2 vertical**. Fast Bucket-1 property-management revenue; RERA + listing-portal RPA as the gating build for developer/compliance ACV; lease-renewal intelligence as a premium commercial add-on.
- **Phase 1 (weeks 0–5):** UC-2 (leads), UC-3 (rent), UC-5 (maintenance), UC-9 (RWA), UC-10 (portfolio) — the property-management pack. WhatsApp + Razorpay + CRM + accounting adapter. Land Starter (₹5,000/mo) property managers and RWAs.
- **Phase 2 (weeks 5–12):** Build shared listing-portal RPA + DocuSign → UC-1 (listing), UC-4 (onboarding), UC-6 (valuation), UC-8 (commission). Launch Developer Pro.
- **Phase 3 (weeks 12–18):** RERA portal RPA → UC-7; SRO + JLL/Anarock comparables → UC-11 (renewal intelligence premium module); full multi-portal orchestration → UC-12 (launch). Launch PropTech Enterprise + white-label.

## 8. KPIs
- Business impact: lead conversion (5%→12%), lead response time (<5 min), listing time (4h→20 min) + inquiry volume (+25–40%), rent collection efficiency by D+5 (75%→95%), maintenance resolution (7 days→24h), RERA on-time filing rate (100%, zero penalties), lease-renewal rent uplift (8–18%), launch first-72h inquiry lift (+35–50%).
- Platform health: agent success/replan rate per UC, RPA-portal uptime (RERA + listing portals + SRO), launch-orchestration task-completion rate (zero dropped tasks), HITL approval latency, WhatsApp broadcast delivery rate, LLM cost per listing/briefing.
- Compliance: RERA filing audit completeness, escrow-threshold flag accuracy, prohibited-lease-clause block rate (100%), DPDP consent coverage, commission-attribution dispute rate (target 40%→5%).
- Commercial: Starter→Developer Pro upgrade rate, units-under-management growth, renewal-intelligence add-on attach rate, Enterprise white-label pipeline.
