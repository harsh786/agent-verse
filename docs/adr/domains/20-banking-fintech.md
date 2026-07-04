# ADR-D20: Banking & FinTech Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/20/use-cases.md

## 1. Market & Monetization
- **TAM:** India BFSI ₹290 lakh crore ($3.5T); RBI issued 138 circulars in FY2024; Basel IV expected to consume 8–12% of bank tech budgets; NPAs ₹8.2 lakh crore. RBI fined banks ₹6,400 crore (FY2023) partly for AML failures. Highest-value, most heavily regulated vertical.
- **Buyer:** Chief Compliance Officer / CRO / CFO / Head of Digital Lending (economic buyer); MLRO and RBI IS-Audit lead are mandatory approvers on any procurement.
- **Tiers (₹):**
  - Starter ₹89,999/mo — small NBFCs, co-op banks, fintech startups; 3 agents (KYC + loan processing + reconciliation), 1,000 txns/mo, 1 core-banking integration.
  - Growth ₹3,49,999/mo — mid-size banks, large NBFCs, digital lenders; 8 agents, transaction fraud + AML + regulatory reporting, 10,000 txns/mo, CIBIL/CRIF integration, dedicated BFSI compliance consultant.
  - Enterprise ₹9,99,999/mo — scheduled commercial banks, top-50 NBFCs; unlimited agents, real-time fraud at scale, all regulatory returns, on-prem, RBI IS-Audit compliant, SOC 2 Type II, 99.99% SLA, VAPT included.
- **Consumption model:** Hybrid — per-transaction usage (₹250/KYC, ₹800/loan, ₹0.08/txn fraud-screened, ₹1,200/AML alert, ₹400/collection account/mo, ₹1,200/product-conversion) plus high monthly platform licences; fraud/AML also offer loss-sharing / success-fee models.
- **WTP:** Highest of all verticals — penalty-avoidance (₹1–₹50 crore per RBI action) and fraud-loss reduction justify enterprise pricing. But procurement is slow and compliance-gated.
- **Time-to-first-revenue:** Slowest (6–12 months). Core-banking integration, RBI IS-Audit, VAPT, and MLRO sign-off precede go-live. Not PLG.
- **Monetization note:** **Highest WTP but heaviest compliance (KYC/AML/RBI), slowest to build, approval-gated and fail-closed.** Every agent action is auditable and reversible; regulatory-reporting and AML modules must fail-closed (block, never auto-proceed on ambiguity). Land on KYC/re-KYC backlog (immediate penalty-risk relief) and reconciliation, then expand into fraud/AML/regulatory where enterprise ACV is largest.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | KYC Document Verification & Risk Scoring | Both | 2 | document_reader(OCR), digilocker/uidai(New-API), pan-it-api(New-API), ckyc-api(New-API), ofac_un_api(New-API), web_search, code_sandbox, core_banking(New-API), postgresql | High-volume-repetitive | Yes (high-risk/PEP/sanction) |
| UC-2 | Loan Application Processing & Credit Assessment | Both | 2 | cibil/crif-api(New-API), document_reader(statements/ITR), gstn-api(New-API), state-registration(RPA-new), code_sandbox, core_banking, docusign, email, pdf_generator | Approval-gated | Yes (Refer / >₹50L) |
| UC-3 | Transaction Fraud Detection | Agent | 3 | kafka/http-stream(New-API), code_sandbox(<150ms), sms, postgresql, core_banking/payment-switch(New-API), slack | Real-time-monitoring | Step-up auth (OTP); fraud-team alert |
| UC-4 | RBI / Basel Regulatory Reporting Automation | Both | 2 | core_banking, treasury(New-API), code_sandbox, rbi_portal(RPA-new: XBRL/SCORES/FIU), docusign, email, postgresql, xbrl-gen | Approval-gated | Yes (CFO+CCO always, fail-closed) |
| UC-5 | Customer Churn Prediction & Retention | Both | 2 | core_banking, code_sandbox(XGBoost), web_search, crm(salesforce), slack, email, sms, push-api(New-API), postgresql | High-volume-repetitive | RM briefing (P1 only) |
| UC-6 | AML Alert Investigation & SAR Filing | Agent | 2/3 | aml-system(New-API: Actimize/SAS), core_banking, ofac_un_api, web_search, code_sandbox(graph/typology), rbi_portal(RPA: FIU-IND), pdf_generator, postgresql, email | Approval-gated | Yes (MLRO approves SAR, fail-closed) |
| UC-7 | Account Reconciliation | Agent | 2 | core_banking, npci-sftp(New-API), swift-mt940-parser(New-API), card-network-file(New-API), code_sandbox(matching), postgresql, pdf_generator, email, slack | High-volume-repetitive | Yes (reversals >₹10k) |
| UC-8 | Interest Rate Impact Analysis | Both | 2/3 | rbi_portal(RPA: MPC watch), core_banking, code_sandbox(ALM/EVE), docusign, pdf_generator, xbrl-gen, email, postgresql | Research+doc-gen | Yes (Treasury+CFO commentary) |
| UC-9 | Credit Collection & NPA Management | Both | 2 | core_banking, whatsapp, sms, code_sandbox(OTR/OTS), field-force-api(New-API), pdf_generator(legal notices), email, crm, postgresql, legal-case-api(New-API) | Approval-gated | Yes (settlement/SARFAESI) |
| UC-10 | Financial Product Recommendation | Both | 1/2 | core_banking, code_sandbox(propensity), whatsapp, email, push-api, product-catalogue(New-API), crm, postgresql | High-volume-repetitive | Yes (LTV >₹50L via RM) |

## 3. Connectors Required
- **Existing (Bucket-1, ready):** document_reader (OCR — statements/ITR/ID), whatsapp, sms, email, web_search, pdf_generator, docusign (digital signature), salesforce (CRM), postgresql, digilocker (partial UIDAI). Modest overlap.
- **New-API (build, HIGH cost — enterprise, regulated):** core_banking (Finacle/Flexcube/in-house — the critical-path enterprise integration, 4–8 wks + security review), treasury, uidai/pan-it/ckyc, ofac_un sanctions, cibil/crif bureau, gstn, kafka/http transaction stream (< 150ms fraud path), payment-switch, aml-system (Actimize/NICE/SAS), npci-sftp + swift-mt940 + card-network file parsers, field-force-api, legal-case-api, push-api, product-catalogue, xbrl-generator. Cost: high; core_banking and the sub-150ms fraud stream are the hardest.
- **RPA-portal (build, RPA-new, brittle+regulated):** rbi_portal (XBRL/OSMOS/CIMS filing, SEBI SCORES, FIU-IND SAR/CTR submission, MPC-release monitoring). **RPA-new, banking regulatory** — highest-risk RPA in the platform; requires resilient selectors, submission-proof capture, and fail-closed behavior (never blind-submit). State-registration (circle-rate) RPA for loan collateral is lower-criticality.

## 4. Knowledge Collections
- Seed slugs: `rbi-master-directions`, `rbi-kyc-2023`, `pmla-2002`, `basel-iii-iv-methodology`, `aml-typologies-35`, `sanction-watchlists`, `sarfaesi-ibc-procedures`, `rbi-otr-framework`, `xbrl-filing-formats`, `credit-policy-rules`, `crc-risk-criteria`.
- Ingestion recipe: ingest RBI circulars/master-directions + SEBI/FIU guidance (PDF) → chunk by clause → embed to pgvector for compliance retrieval; load sanction/PEP/defaulter watchlists (OFAC/UN/RBI/SEBI) via scheduled Celery refresh into structured postgresql (real-time API preferred for screening); seed regulatory calculation methodologies (LCR/NSFR/CRAR/SMA), depreciation/OTS/ALM formulas as versioned structured rules; AML typology library drives UC-6 pattern matching. All collections versioned — regulatory changes tracked to circular reference.

## 5. Guardrails & Compliance
- **Frameworks:** RBI KYC Master Direction 2023, PMLA 2002, FEMA, Basel III/IV, FATF Recommendations, RBI IS-Audit, SOC 2 Type II, VAPT (Enterprise).
- **Fail-closed posture:** regulatory reporting and AML/SAR modules **never auto-proceed on ambiguity** — block and escalate. Fraud decisions default to step-up auth, not silent approve.
- **HITL mandatory** on: high-risk/PEP/sanction KYC, loan Refer/>₹50L, all regulatory filings (CFO+CCO always), all SARs (MLRO always), reversals >₹10k, settlement/SARFAESI actions, ALM commentary, >₹50L product recs.
- **Audit & residency:** append-only trail, **10-year retention**, India-only residency, AES-256, PII masking (Aadhaar/PAN/account). Every filed figure and every dismissed AML alert carries documented justification for RBI/FIU inspection.
- **Tool-risk:** no autonomous fund movement, no blind portal submission; reversible/compensating actions required (rollback engine) for any core-banking write.

## 6. Scale Pattern & Cost Drivers
- All four patterns present: **high-volume-repetitive** (KYC, reconciliation, product recs), **real-time-monitoring** (fraud stream — sub-150ms, 50M+ txn/mo), **approval-gated** (loans, regulatory, AML, collections), **research+doc-gen** (ALM reports).
- Cost drivers: fraud path is **latency-not-token** bound (optimized code_sandbox feature extraction + ML scoring at scale — 50M txn/mo dominates infra); code_sandbox compute for credit scorecards, ALM/EVE, XGBoost churn, AML graph/typology; RPA session-minutes on RBI/FIU portals (high maintenance). LLM used for narrative/doc-gen and screening summaries — semantic-cache dedups repetitive KYC/reconciliation prompts. On-prem/RBI-IS-Audit adds infra + audit cost.

## 7. Decision & Phasing
- **Phase 1 (land, penalty-risk relief):** KYC + re-KYC backlog (UC-1) and Account Reconciliation (UC-7) — immediate compliance value, contained core-banking read integration; Loan processing (UC-2) completes the Starter bundle.
- **Phase 2 (expand, high-WTP):** RBI/Basel Regulatory Reporting (UC-4, rbi_portal RPA), AML Investigation & SAR (UC-6, aml-system) — largest enterprise ACV, MLRO/CCO buy-in.
- **Phase 3 (real-time + advanced):** Transaction Fraud (UC-3, sub-150ms Kafka stream — heaviest infra), ALM/Interest-rate (UC-8), Churn (UC-5), Collections/NPA (UC-9), Product recs (UC-10).
- Decision: **Accepted.** Slowest, most compliance-gated, fail-closed build; highest ACV and stickiness. Sequence last among the four assigned verticals; reuse KYC/AML/reporting patterns from insurance (ADR-D17).

## 8. KPIs
- KYC cycle 3d→18m; cost ₹2,200→₹320; re-KYC backlog cleared in 90d; zero late-filing penalties.
- Loan sanction 10d→4h; fraud loss −62%; fraud false-positive 85%→24%; detection latency <150ms.
- AML false-positive 75%→38%; SAR filing 5d→8h; reconciliation auto-match 97.8%, FTE 30→8.
- ALM turnaround 5d→4h; churn (high-value) 22%→11%; NPA recovery 28%→51%; product-per-customer 2.1→3.8.
- Platform: fraud-path p99 latency, HITL/fail-closed override rate, RBI/FIU portal submission success, audit-trail completeness, cost-per-transaction by module.
