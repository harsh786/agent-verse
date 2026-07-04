# ADR-D18: Customer Support & CX Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/18/use-cases.md

## 1. Market & Monetization
- **TAM:** India CX management market ₹14,800 crore, 17% CAGR; 500M internet users demanding 24×7 support; 68% consumer dissatisfaction, 3.2-day avg resolution, 52% FCR — large, horizontal, cross-industry demand.
- **Buyer:** Head of Customer Support / VP CX / COO of BPO (economic buyer); support ops managers are champions. Fast, self-serve friendly.
- **Tiers (₹):**
  - Starter ₹39,999/mo — SMEs, D2C, early SaaS; 2 agents, 2,000 tickets/mo, auto-resolution + routing, English+Hindi, Zendesk/Freshdesk integration.
  - Growth ₹1,49,999/mo — mid-market, 50–200 agent teams; 6 agents, 20,000 tickets/mo, full 10-UC suite, 5 languages, coaching, SLA prediction, WhatsApp, HITL.
  - Enterprise ₹4,99,999/mo — large BPOs, national banks, telecom; unlimited, 11 Indian languages, custom rubrics, call analysis, voice, on-prem, 99.9% SLA.
- **Consumption model:** Per-ticket usage (₹15/auto-resolved, ₹35/return, ₹25/survey, ₹20/multilingual ticket, ₹800/agent/mo QA) under a monthly platform licence — usage scales cleanly with ticket volume.
- **WTP:** Volume-driven and value-obvious (₹350→₹52 per ticket). Lower per-unit price than regulated verticals but highest recurrence and fastest expansion.
- **Time-to-first-revenue:** Fast (2–6 weeks). Native Zendesk/Freshdesk/Intercom webhooks + WhatsApp + pgvector KB are all existing Bucket-1 connectors — near zero custom integration to go live.
- **Monetization note:** This is the **Bucket-1 fast PLG flagship.** Land on Tier-1 auto-resolution (immediate, measurable ROI), high recurrence, low sales friction — the fastest path to first revenue across all verticals and the reference-logo engine for the platform.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Tier-1 Ticket Auto-Resolution | Both | 1 | email/IMAP, whatsapp, zendesk, freshdesk, intercom, postgresql+pgvector, crm(salesforce), oms(New-API), code_sandbox, slack | High-volume-repetitive | Yes (confidence <85% / enterprise tier) |
| UC-2 | Intelligent Escalation Routing | Agent | 1 | zendesk, freshdesk, crm, postgresql, code_sandbox, slack, whatsapp | High-volume-repetitive | Human agent = the escalation target |
| UC-3 | SLA Breach Prediction & Prevention | Agent | 1 | zendesk, freshdesk, code_sandbox, slack, postgresql, whatsapp/email | Real-time-monitoring | Supervisor alert on high risk |
| UC-4 | Customer Sentiment Trend Analysis | Agent | 1→2 | zendesk, web_search, playwright-RPA(app-store/social), code_sandbox, postgresql, pdf_generator, email, slack | Research+doc-gen | No (advisory brief) |
| UC-5 | KB Auto-Update from Resolved Tickets | Both | 1 | zendesk, freshdesk, postgresql+pgvector, code_sandbox, kb-api(New-API), slack | Research+doc-gen | Yes (KB curator always) |
| UC-6 | Refund & Return Processing | Both | 1→2 | email/IMAP, whatsapp, oms(New-API), code_sandbox(policy engine), logistics(New-API), payment-gateway(New-API), zendesk/freshdesk, postgresql | High-volume-repetitive | Yes (ineligible/denial) |
| UC-7 | Proactive Outage Communication | Agent | 1→2 | pagerduty/opsgenie(New-API), datadog(New-API), web_search, statuspage(New-API), slack, whatsapp, email, zendesk(bulk), crm, pdf_generator | Real-time-monitoring | No (auto, engineer-notify) |
| UC-8 | Agent Coaching from Call & Chat Analysis | Agent | 2 | call-recording(New-API: Exotel/Ozonetel), zendesk(chat), code_sandbox(STT/NLP), postgresql, slack, email, pdf_generator, calendar(New-API) | Research+doc-gen | Yes (supervisor reviews brief) |
| UC-9 | CSAT/NPS Survey Follow-Up | Both | 1 | whatsapp, email/IMAP, survey-api(New-API), crm, zendesk/freshdesk, code_sandbox, slack, postgresql | High-volume-repetitive | Yes (high-value detractors) |
| UC-10 | Multilingual Support (11 languages) | Agent | 1→2 | whatsapp, IMAP, translation-api(New-API: Azure/Google), code_sandbox, postgresql+pgvector, zendesk/freshdesk, slack | High-volume-repetitive | Human handoff w/ bilingual summary |

## 3. Connectors Required
- **Existing (Bucket-1, ready):** email/IMAP, whatsapp, sms, zendesk, freshdesk, intercom, postgresql+pgvector, salesforce (CRM), web_search, pdf_generator, slack, document_reader. This vertical maps almost entirely onto the existing connector set — the core reason it is the PLG flagship.
- **New-API (build, low–medium cost):** oms (order management, per-merchant), payment-gateway (refunds), logistics (Shiprocket/Delhivery reverse pickup), kb-api (publish endpoint), pagerduty/opsgenie + datadog + statuspage (outage signals), call-recording platforms (Exotel/Ozonetel/Avaya), survey-api (Typeform/SurveyMonkey), translation-api (Azure/Google), calendar. Most are well-documented REST APIs — ~1 wk each.
- **RPA-portal (build):** app-store review scraping (Play/App Store) + social monitoring (Playwright, ToS-fragile) for UC-4 sentiment. Lower criticality — can degrade to web_search only.

## 4. Knowledge Collections
- Seed slugs: `product-faq`, `troubleshooting-runbooks`, `return-refund-policy`, `billing-policy`, `account-management-howto`, `sla-definitions`, `escalation-playbook`, `qa-rubric-templates`, `multilingual-term-glossary`, `known-issues`.
- Ingestion recipe: import existing Zendesk/Freshdesk KB articles + macros via API → chunk → embed to pgvector; UC-5 closes the loop (mined resolved tickets → drafted articles → curator HITL → re-index); seed multilingual term glossary (financial/medical/legal vocab) as structured rows for translation quality-check; hybrid trigram+vector retrieval in the Tier-1 resolver.

## 5. Guardrails & Compliance
- **Frameworks:** DPDP Act 2023, PII masking, AES-256; SOC 2 for enterprise BPO buyers. Lighter regulatory load than BFSI/insurance.
- **HITL** on: auto-resolution below confidence threshold, enterprise-tier customers, refund denials, KB publication, high-value detractor outreach, coaching briefs (supervisor-first, never straight to agent).
- **Sentiment guard:** negative-reply detection auto-reopens and escalates any auto-closed ticket — prevents silent bad-CSAT.
- **Data residency** India; call-recording/PII redaction before LLM; translation glossary enforces domain-term fidelity (back-translation verification).
- **Tool-risk:** refund/payment initiation gated; bulk ticket operations (outage) rate-limited and audit-logged.

## 6. Scale Pattern & Cost Drivers
- Overwhelmingly **high-volume-repetitive** (auto-resolution, routing, returns, surveys, multilingual) plus **real-time-monitoring** (SLA prediction every 10m, outage detection) and **research+doc-gen** (sentiment, coaching, KB).
- Cost drivers: raw LLM inference on ticket classification/response at scale — **semantic-cache and llm_response_cache are decisive** here (repetitive intents dedup heavily); STT transcription volume (UC-8); translation-api calls (UC-10). Per-ticket margin depends on cache hit-rate; target aggressive dedup to hold the ₹15/ticket economics.

## 7. Decision & Phasing
- **Phase 1 (land fast, Bucket-1):** Tier-1 auto-resolution (UC-1), Escalation routing (UC-2), SLA prediction (UC-3), CSAT/NPS (UC-9), KB auto-update (UC-5) — all on existing connectors, weeks to live.
- **Phase 2 (expand):** Refund/return (UC-6, oms+payment+logistics), Multilingual (UC-10, translation-api), Outage comms (UC-7, monitoring connectors).
- **Phase 3 (enterprise upsell):** Agent coaching from call analysis (UC-8, call-recording + STT), Sentiment intelligence (UC-4) — differentiators for large BPO/contact-centre deals.
- Decision: **Accepted.** Designated cross-vertical PLG flagship — first to GA, drives platform reference logos and connector reuse for all other domains.

## 8. KPIs
- Auto-resolution 65% of tickets; per-ticket cost ₹350→₹52; CSAT +0.8.
- Transfer rate 2.4→0.3; FCR +31%; SLA breach 8%→1.8%.
- KB accuracy 68%→94%; return processing 7d→32h; refund cost ₹450→₹65.
- Detractor churn 78%→34%; NPS +18 over 6mo; QA coverage 3%→100%.
- Non-English CSAT +1.4; outage duplicate tickets −72%.
- Platform: cache hit-rate (margin driver), auto-resolution confidence calibration, cost-per-ticket by intent.
