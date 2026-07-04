# ADR-D17: Insurance Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/17/use-cases.md

## 1. Market & Monetization
- **TAM:** India insurance ₹10.4 lakh crore total premium (FY2024); < 35% of retail claims processed touchless vs. 70%+ global best; fraud drains ₹45,000 crore/year. Serviceable target = mid/large insurers, health TPAs, aggregators, reinsurers.
- **Buyer:** Chief Underwriter / Chief Claims Officer / Head of Compliance (economic buyer); CFO + Appointed Actuary co-sign regulatory automation.
- **Tiers (₹):**
  - Starter ₹79,999/mo — insurtech startups, NBFC-insurers; 3 agents, 1,000 txns/mo (KYC + FNOL + 1 workflow), IRDAI audit trail.
  - Growth ₹2,99,999/mo — mid-size life/general insurers, brokers; 8 agents, 10,000 txns/mo, full claims suite + fraud + renewals + 2 regulatory filings, HITL gates.
  - Enterprise ₹8,99,999/mo — top-20 insurers, global reinsurers; unlimited, all 10 modules, custom treaty/regulatory formats, on-prem, 99.95% SLA, SOC 2 Type II, IRDAI data-privacy compliance.
- **Consumption model:** Hybrid — per-transaction usage (₹350/policy data pack, ₹180/FNOL, ₹500/claim screened, ₹120/KYC, ₹400/renewal, ₹250/claim adjudicated, ₹800/cross-sell, ₹80/inspection, ₹8L/quarter reinsurance) layered under a monthly platform licence.
- **WTP:** High for fraud (success-fee 5% of recovery) and regulatory filing (₹5L/mo, penalty-avoidance driven); moderate for intake/inspection commodity volume.
- **Time-to-first-revenue:** Medium (3–5 months). Bucket-1 workflows (KYC, FNOL, renewals) ship fast on existing connectors; claims/fraud/IRDAI require RPA + policy-admin integration.
- **Monetization note:** This is an **approval-gated, regulated** vertical. Land with fast Bucket-1 KYC/FNOL/renewal PLG, then expand into high-WTP regulated modules (fraud SIU, IRDAI filing, reinsurance) where value justifies enterprise pricing and slower integration.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Policy Underwriting Data Gathering & Scoring | Both | 2 | cibil_api(New-API), vahan_api(New-API), traces_api(New-API), mca21(RPA-new), document_reader, web_search, code_sandbox, policy_admin(New-API), email, postgresql | Research+doc-gen | Yes (substandard / >₹50L) |
| UC-2 | Claims Intake, Triage & FNOL | Both | 1→2 | email/IMAP, whatsapp, document_reader, image-analysis(New-API), policy_admin(New-API), claims_system(New-API), email | High-volume-repetitive | Yes (exclusion decisions) |
| UC-3 | Fraud Pattern Detection & SIU Referral | Agent | 2 | claims_system(New-API), postgresql, code_sandbox, web_search, playwright-RPA(social), vahan_api, rohini_api(New-API), mci_api(New-API), maps_api, pdf_generator, email | Real-time-monitoring | Yes (SIU referral review) |
| UC-4 | Renewal Campaign Personalization | Both | 1 | policy_admin, crm(salesforce), web_search, playwright-RPA(rate scrape), whatsapp, email, sms, postgresql | High-volume-repetitive | Advisor handoff only |
| UC-5 | Customer Onboarding KYC/AML | Both | 1→2 | document_reader(OCR), digilocker/uidai(New-API), traces_api, ofac_un_api(New-API), web_search, code_sandbox, policy_admin, postgresql | High-volume-repetitive | Yes (high-risk / PEP) |
| UC-6 | IRDAI Regulatory Filing Automation | Both | 2 | policy_admin, claims_system, finance_system(New-API), code_sandbox, irdai_portal(RPA-portal), slack, email, postgresql | Approval-gated | Yes (CFO/Actuary always) |
| UC-7 | Claims Adjudication Documentation | Agent | 2 | claims_system, document_reader, code_sandbox, finance_system, email, whatsapp, postgresql | Approval-gated | Yes (adjuster approval) |
| UC-8 | Cross-Sell/Upsell from Claims History | Both | 1 | policy_admin, crm, code_sandbox, web_search, product-catalogue(New-API), whatsapp, email, slack, postgresql | High-volume-repetitive | Advisor briefing only |
| UC-9 | Reinsurance Data Preparation | Agent | 2/3 | policy_admin, code_sandbox, google_sheets/excel-gen, email(encrypted), playwright-RPA(reinsurer portals), postgresql | Research+doc-gen | Yes (cession manager) |
| UC-10 | Motor Inspection Scheduling & Management | Both | 1 | postgresql, geocoding(New-API), whatsapp, sms, mobile-form(New-API), image-analysis, policy_admin, claims_system | High-volume-repetitive | Yes (damage/discrepancy) |

## 3. Connectors Required
- **Existing (Bucket-1, ready):** gmail/email/IMAP, whatsapp, sms, document_reader, web_search, pdf_generator, google_sheets, postgresql, salesforce (CRM), digilocker (partial UIDAI e-KYC), slack.
- **New-API (build, ~1–2 wks each):** cibil_api, vahan_api, traces_api, ofac_un sanctions API, policy_admin system, claims_system, finance_system, product-catalogue, rohini_api, mci_api, geocoding, image-analysis/vision, mobile-form. Cost: medium — each is REST + auth; policy_admin & claims_system are the critical-path enterprise integrations (2–4 wks, per-vendor variance: e.g. Duck Creek, Sapiens, in-house).
- **RPA-portal (build, high cost):** irdai_portal (IRDAI Sarthi filing — RPA-new, brittle, high maintenance), mca21 (director search RPA-new), reinsurer portals (bespoke per treaty partner), social-media scraping (Playwright, ToS-fragile). Cost: high — portal RPA needs resilient selectors + monitoring.

## 4. Knowledge Collections
- Seed slugs: `irdai-regulations`, `irdai-filing-formats`, `policy-wordings-motor`, `policy-wordings-health`, `policy-wordings-life`, `motor-depreciation-tariff`, `cghs-hospital-tariff`, `aml-typologies`, `fraud-red-flags`, `reinsurance-treaty-glossary`, `icd-code-reference`.
- Ingestion recipe: pull IRDAI circulars/master-circulars (PDF) via document_reader → chunk by section → embed to pgvector; ingest company policy wordings + endorsement library; seed depreciation/tariff tables as structured rows in postgresql (not vector); load OFAC/UN/SEBI-debarred watchlists via scheduled Celery refresh; hybrid trigram+vector retrieval for clause lookups during adjudication.

## 5. Guardrails & Compliance
- **Frameworks:** IRDAI regulations, DPDP Act 2023, PMLA (AML/KYC), SOC 2 Type II (Enterprise).
- **Data residency:** India-only; AES-256 at rest; PII masking (Aadhaar/PAN) in logs and prompts.
- **HITL mandatory** on: exclusion/decline decisions, substandard underwriting, SIU referral, adjudication settlement, IRDAI filing (CFO+Actuary always), reinsurance cession.
- **Audit:** append-only trail, 7-year retention (IRDAI), 8-year for reinsurance cession. Every filed figure carries source-data + calculation evidence.
- **Tool-risk:** payment instructions, policy issuance, and portal submissions route through tool_risk gating; no autonomous fund movement.

## 6. Scale Pattern & Cost Drivers
- Mixed workload: **high-volume-repetitive** (FNOL, KYC, renewals, inspections — LLM cost dominated by intake classification + doc parsing), **approval-gated** (adjudication, IRDAI, reinsurance — low volume, high compute per case in code_sandbox), **real-time-monitoring** (fraud webhook per claim).
- Cost drivers: document/image parsing volume (OCR + vision), code_sandbox for actuarial/graph/ML scoring, RPA session-minutes on IRDAI/reinsurer portals (maintenance is the hidden cost), semantic-cache dedup on repetitive KYC/renewal prompts materially cuts LLM spend.

## 7. Decision & Phasing
- **Phase 1 (land, Bucket-1):** KYC/AML (UC-5), FNOL intake (UC-2), Renewal campaigns (UC-4), Inspection scheduling (UC-10) — existing connectors, fast revenue, PLG into insurtech/NBFC-insurers.
- **Phase 2 (expand, New-API):** Underwriting data (UC-1), Adjudication docs (UC-7), Cross-sell (UC-8) — requires policy_admin/claims_system integration.
- **Phase 3 (regulated high-WTP):** Fraud SIU (UC-3), IRDAI filing (UC-6, irdai_portal RPA), Reinsurance (UC-9) — enterprise-only, heaviest build, highest price and stickiness.
- Decision: **Accepted.** Build order prioritizes Bucket-1 PLG then IRDAI RPA as the enterprise moat.

## 8. KPIs
- Underwriting cycle 7d→4h; abandonment 18%→6%.
- FNOL intake 48h→35m; doc completeness 60%→91%.
- Fraud detection rate 38%→71%; false-positive 22%→8%.
- KYC cycle 3d→12m; rejection 15%→4%; zero IRDAI late-filing penalties.
- Renewal retention 48%→71%; adjudication cycle 21d→8d; reinsurance bordereaux 20d→3d.
- Platform: touchless-claim %, HITL approval-latency, RPA portal success rate, cost-per-transaction by module.
