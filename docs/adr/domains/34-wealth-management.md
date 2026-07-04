# ADR-D34: Wealth Management & Investment Advisory Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/34-wealth-management/use-cases.md

## 1. Market & Monetization
- **TAM:** ₹118 lakh crore opportunity (₹78L cr MF AUM + ₹40L cr PMS/AIF). One RIA per 10,000 investors — the advisory-capacity gap is the wedge. AIF/PMS reporting alone is a ₹480 cr/yr cost-substitution market.
- **Buyer:** SEBI-registered Investment Adviser / MFD principal, wealth-arm head at NBFC/private bank, family office CIO, AIF/PMS fund manager, CA firms with HNI/NRI practice.
- **3 tiers (₹):** Starter ₹8,000/mo (RIA/solo — ≤200 clients, portfolio review + goal tracking + basic reports, 5 HITL seats). Growth ₹35,000/mo (boutique — ≤2,000 clients, all 10 UCs, PMS/AIF reporting, NRI compliance, tax-loss harvesting, CRM + broker API). Enterprise ₹2.5 lakh/mo (private bank/large NBFC — unlimited, white-label, custom compliance, SEBI audit export, core-banking API).
- **Consumption model:** Per-client-seat SaaS (₹15–50/client/mo) as the base, plus per-artefact fees (₹1,500/AIF-PMS investor report cycle, ₹5,000/estate package, ₹25,000/NRI audit) and success fees (₹500 per ₹1L tax saved, UC-5).
- **WTP:** High — advisory fees are AUM-linked, so agent-driven client capacity (3× per adviser) and AUM retention translate directly to revenue. SEBI compliance is a licence-protection must-buy (penalties to ₹25L per mis-selling instance).
- **Time-to-first-revenue:** Moderate (4–8 weeks) — even the core UCs need registrar (CAMS/KFintech) and market-data integration. NRI compliance and AIF reporting take 8–12 weeks.
- **Monetization note:** Portfolio review + goal tracking (UC-1, UC-10) are the daily-value base that justifies per-client pricing. Tax-loss harvesting (UC-5, success fee) and AIF/PMS reporting (UC-9, per-investor) are the high-margin expanders. Regulated = approval-gated = sticky.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Portfolio Review & Rebalancing Analysis | Both | 2 | CAMS/KFintech (RPA-new/New-API), BSE/NSE data (New-API), portfolio-math, tax-estimator, pdf_generator, HITL, email, scheduler, salesforce (CRM) | Approval-gated + Research+doc-gen | Yes (memo before send) |
| UC-2 | MF Recommendation with SEBI Risk Suitability | Both | 1+2 | whatsapp, document_reader (OCR KYC), AMFI data (New-API), CRM, pdf_generator, scheduler | Approval-gated + Research+doc-gen | Yes (adviser sign-off) |
| UC-3 | Insurance Portfolio Optimization | Both | 1 | email (inbox scan), document_reader (OCR), pdf_generator, e-sign (New-API), CRM, web_search (insurer portals) | Research+doc-gen | Yes (priority queue) |
| UC-4 | Estate Planning Document Preparation | Both | 2 | document_reader (CAS/demat OCR), Account Aggregator (RPA-new/New-API), epfo/ppf (RPA-new), HITL, DocuSign, email, pdf_generator | Research+doc-gen + Approval-gated | Yes (solicitor review) |
| UC-5 | Tax-Loss Harvesting Identification | Agent | 2 | demat/folio data (New-API), broker API (New-API: order mgmt), nsdl_cdsl (RPA-new), tax-calc-engine, pdf_generator, scheduler, HITL | Real-time-monitoring + Approval-gated | Yes (per-transaction approval) |
| UC-6 | Real Estate vs Financial Asset Allocation | Both | 1+2 | PropTech (New-API: MagicBricks/99acres), document_reader (loan OCR), Monte-Carlo engine, tax-engine, pdf_generator, HITL, CRM, web_search | Research+doc-gen | Yes (discussion talking points) |
| UC-7 | Retirement Corpus & SWP Planning | Both | 2 | epfo/nsdl NPS (RPA-new), Monte-Carlo engine, pdf_generator, email, scheduler, HITL, CRM | Research+doc-gen + Real-time-monitoring | Yes (course-correction memo) |
| UC-8 | NRI Investment Compliance (FEMA/FATCA/CRS) | Agent | 2 | document_reader (OCR), fema_fatca (RPA-new: IT portal 15CA/15CB), email, HITL, CRM, pdf_generator, scheduler | Approval-gated + Research+doc-gen | Yes (CA/compliance sign-off) |
| UC-9 | AIF/PMS Performance Reporting for HNIs | Agent | 2 | fund-admin data (New-API: CAMS PMS), NSE/BSE data (New-API), return-calc/attribution engine, pdf_generator, email (bulk), audit store (SHA-256), scheduler | High-volume-repetitive + Research+doc-gen | No (auto-dispatch) |
| UC-10 | Financial Goal Tracking & Course-Correction | Agent | 2 | CAMS/KFintech (RPA-new/New-API), whatsapp, email, HITL, CRM, scheduler, pdf_generator | Real-time-monitoring | Yes (message before send) |

## 3. Connectors Required
- **Existing (Bucket 1):** whatsapp, email/smtp, document_reader (OCR), web_search, pdf_generator, salesforce (CRM). Build cost ~0.
- **New-API:** CAMS/KFintech registrar (if API access granted — else RPA), BSE/NSE market data (EOD/realtime feed), AMFI fund universe, broker order-management API, PropTech (MagicBricks/99acres), fund-admin data (CAMS PMS/proprietary), DocuSign/e-signature. Build 3–5 days each; broker order-placement API needs sandbox + risk sign-off (~2 weeks).
- **RPA-portal (New):** sebi_portal (SEBI RIA/AIF filings — RPA-new), nsdl_cdsl (demat CAS, NPS balances — RPA-new), fema_fatca (income-tax portal 15CA/15CB, FATCA/CRS — RPA-new), epfo/ppf (provident-fund nomination + NPS Tier-I), Account Aggregator framework (consent-based bank/FD aggregation — spec-based, treat as regulated New integration), state property/loan portals. Build ~1 week each; these are the regulated-data moat and the bottleneck.
- **Compute engines (New — not connectors):** portfolio-math (drift), tax-calc (LTCG/STCG, wash-sale, indexation), Monte-Carlo (retirement/asset-allocation), return-attribution (TWR/XIRR, Brinson-Hood-Beebower, Sharpe/Sortino/drawdown), performance-fee (high-water mark).

## 4. Knowledge Collections
Seed slugs: `sebi-ia-regulations-2020`, `sebi-risk-category-matrix`, `amfi-fund-factsheets`, `fema-repatriation-rules`, `fatca-crs-reportability`, `income-tax-slabs-ltcg-stcg`, `hindu-vs-indian-succession-act`, `will-clause-templates`, `hlv-actuarial-formulas`, `irdai-policy-taxonomy`.
Ingestion recipe: (1) ingest SEBI IA Regulations 2020 + risk-category matrix as suitability ground truth (every recommendation logged against it); (2) load AMFI fund universe + factsheets, refreshed daily; (3) seed FEMA/FATCA/CRS rule text + Form 15CA/15CB/10F formats for UC-8; (4) tax slab tables (STCL 30%, LTCL 12.5% above ₹1.25L) versioned by financial year; (5) legal will templates keyed by succession-act jurisdiction.

## 5. Guardrails & Compliance
- **SEBI IA Regulations 2020:** mandatory HITL — adviser must approve every rebalancing memo, recommendation, and course-correction before it reaches the client (regulatory human-in-the-loop). Suitability justification logged with timestamp for audit; 7-year (2557-day) retention.
- **Conflict-of-interest disclosure (UC-2):** flag funds where distributor earns above-average commission, append SEBI-mandated disclosure.
- **Wash-sale / deemed-dividend (UC-5):** verify no same-ISIN purchase in preceding 30 days before harvesting.
- **NRI (UC-8):** FEMA penalties to 3× transaction value; FATCA criminal exposure — CA/compliance sign-off mandatory before any filing.
- **Data classification:** financial_pii; RLS tenant isolation; tamper-evident audit store with SHA-256 checksums for SEBI inspection (UC-9).
- **Hallucination risk:** critical — all money math (drift, tax, returns, corpus) must run in deterministic compute engines, not LLM; LLM only writes rationale/memos over verified numbers. Broker order placement (UC-5) is highest-stakes — per-transaction human approval, no autonomous trading.

## 6. Scale Pattern & Cost Drivers
- **Approval-gated (defining, regulatory):** nearly every client-facing output passes adviser HITL — throughput bounded by adviser review capacity, which is exactly the constraint the platform relieves (4min vs 45min review). Batch memos into a priority queue.
- **Real-time-monitoring:** daily NAV sync + goal-trajectory (UC-10), nightly tax-harvest scan (UC-5), quarterly corpus tracking (UC-7). Scheduler-driven.
- **High-volume-repetitive:** AIF/PMS bulk report generation (UC-9 — per-investor personalized PDFs at report cycle).
- **Research+doc-gen:** insurance, estate, asset-allocation, retirement (UC-3/4/6/7).
- Primary cost driver: daily NAV/market-data ingestion volume + per-client compute (drift/tax scans), and per-investor report rendering at AIF scale. LLM cost is modest (rationale writing); the moat cost is registrar/regulated-portal RPA reliability.

## 7. Decision & Phasing
- **Phase 1 (land):** UC-1 portfolio review + UC-10 goal tracking — the daily-value base that justifies per-client pricing. Requires CAMS/KFintech + BSE/NSE integration up front.
- **Phase 2 (expand):** UC-2 MF recommendation, UC-5 tax-loss harvesting (success fee), UC-3 insurance, UC-7 retirement — moderate build, high WTP.
- **Phase 3 (enterprise/regulated):** UC-9 AIF/PMS reporting, UC-8 NRI compliance, UC-4 estate, UC-6 asset allocation — heaviest RPA (sebi_portal, nsdl_cdsl, fema_fatca) + compute engines, enterprise ACV.
- **ENRICHMENT FLAG — this is one of the 4 THINNEST domains (10 UCs). Enrich to 12 UCs.** Proposed additions (both Bucket-2, regulated, consistent with the vertical): **UC-11 SIP/mandate lifecycle automation** (NACH e-mandate setup, SIP registration/pause/step-up via registrar + bank RPA, bounce-recovery workflow) and **UC-12 client onboarding & periodic KYC/re-KYC** (CKYC registry lookup, PAN-Aadhaar seeding, FATCA self-cert, in-person-verification video capture, SEBI/AMFI KYC status sync) — both extend the regulated-data + approval-gated pattern and deepen registrar/portal RPA reuse.

## 8. KPIs
- Per-client review time 45min → 4min; adviser capacity ×3; ₹8.5L/yr analyst cost saved per 500-client book (UC-1).
- New client onboarding 8 → 30/month; incremental AUM ₹4–5cr/adviser/mo (UC-2).
- Insurance premium saving ₹42,000/yr/HNI client (UC-3); estate prep 3 weeks → 2 days (UC-4).
- Tax saved ₹55,000–₹1.8L/client/yr (UC-5); AIF/PMS report cost ₹12,000 → ₹2,400/investor/yr (UC-9).
- Goal-based AUM retention 71% → 89%, +₹3.2cr/adviser/yr (UC-10); NRI compliance 80% automated, 600 CA-hours/yr freed (UC-8).
- Platform: HITL approval latency, suitability-log completeness (100% required), deterministic-engine coverage of money math (100%), registrar RPA success rate.
