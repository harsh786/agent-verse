# ADR-D24: Pharmaceutical & Life Sciences Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/24-pharmaceutical/use-cases.md

## 1. Market & Monetization
- **TAM:** ₹4.2 lakh crore domestic pharma; ~10,500 manufacturing units, 8.5 lakh+ retail licenses; regulated, penalty-driven spend makes willingness-to-pay the highest of any AgentVerse vertical.
- **Buyer:** Regulatory Affairs Head (economic buyer for compliance suite), VP Quality/QA Director (batch + cold chain), Head of Pharmacovigilance, VP Medical Affairs (content), Commercial Ops/BD (MR, tenders, CI).
- **3 tiers (₹):** Tier 1 Regulatory Compliance Starter ₹2.5 L/month (≤50 licenses, ≤200 ICSR/month, 2 cold-chain sites); Tier 2 Commercial Operations Pro ₹7 L/month (adds MR analytics ≤500 reps, patent CI, tender monitoring, ≤50 batches, ≤10 content pieces); Tier 3 Enterprise Pharma OS ₹18 L/month + 1% audited-savings success fee (clinical trials, pharmacoeconomics, DDI/FHIR, multi-geography submissions).
- **Consumption model:** hybrid subscription + per-unit metering — per-license/year, per-ICSR above cap, per-batch reviewed, per-MR/month, per-dossier module, per-content piece, per-IoT sensor node.
- **WTP:** Very high — a single late ICSR = ₹10–50 crore penalty; a missed patent cliff forgoes ₹500 crore+; batch release delay locks ₹30–200 crore working capital. Compliance is non-discretionary.
- **Time-to-first-revenue:** 2-week onboarding for Tier 1 (license monitoring + ICSR). First metered revenue within ~30 days.
- **Monetization note:** Land with regulated Bucket-2 compliance modules (highest urgency, sticky), expand into commercial (MR/tender) and premium research (dossier/CI/HE) where success-fee upside justifies the 1% rider.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Drug License Application & Renewal (CDSCO) | Both | 2 | cdsco_portal (RPA), document_reader, MCA21 (New-API), postgresql, email, slack | Approval-gated | Yes |
| UC-2 | Pharmacovigilance AE Reporting (VigiBase/ICSR) | Both | 2 | email, salesforce, PubMed (New-API), MedDRA (New-API), cdsco-pvpi (RPA), vigibase (RPA-new) | High-volume-repetitive + Approval-gated | Yes |
| UC-3 | Clinical Trial Site Feasibility & Recruitment | Template | 3 | ClinicalTrials.gov (New-API), CTRI (New-API), document_reader, EHR aggregate (New-API), web_search, salesforce/CTMS, slack, email | Research+doc-gen | Yes |
| UC-4 | Regulatory Dossier Prep (CTD/eCTD) | Both | 3 | Veeva Vault/DMS (New-API), document_reader, Jira (New-API), eCTD validator (New-API), web_search | Research+doc-gen | Yes |
| UC-5 | Competitive Intelligence — Patent Cliffs & Biosimilars | Template | 3 | USPTO/EPO (New-API), IPO patent (RPA-new), FDA Orange/Purple/Drugs@FDA (New-API), IQVIA/AIOCD (New-API), web_search, slack | Research+doc-gen | Yes |
| UC-6 | MR Performance & Territory Management | Agent | 2 | salesforce, AIOCD/IQVIA (New-API), GPS field app (New-API), whatsapp, slack | High-volume-repetitive | Yes (ethics flags) |
| UC-7 | Drug-Drug Interaction Querying for Prescribers | Agent | 3 | HL7 FHIR (New-API), DrugBank/Drugs.com/CIMS (New-API), FDA FAERS (New-API), HIS/EMR (New-API) | Real-time-monitoring | Yes (override co-sign) |
| UC-8 | Cold Chain Compliance Monitoring (Schedule M) | Agent | 3 | IoT MQTT (New-API), sensor APIs, whatsapp, slack, SAP/ERP (New-API), WMS (New-API), pdf_generator | Real-time-monitoring | Yes |
| UC-9 | QC Batch Record Review & Deviation Mgmt | Agent | 3 | MES (New-API), document_reader, QMS (New-API), LIMS (New-API), SAP/ERP | High-volume-repetitive + Approval-gated | Yes |
| UC-10 | Market Access & Formulary/Tender Strategy | Both | 2 | GEM + state tender portals (RPA), web_search, ERP, pdf_generator, docusign/DSC signing | Approval-gated + Research+doc-gen | Yes |
| UC-11 | Medical Education Content for HCPs (OPPI) | Both | 3 | PubMed/ClinicalTrials.gov (New-API), DMS, Turnitin (New-API), design API, Veeva CLM (New-API), Jira, pdf_generator | Research+doc-gen | Yes (MLR) |
| UC-12 | Pharmacoeconomic Modeling for Pricing | Template | 3 | PubMed, ClinicalTrials.gov, GBD (New-API), NHA (New-API), code_execution, NPPA scraper (RPA-new), pdf_generator | Research+doc-gen | Yes |

## 3. Connectors Required
- **Existing (Bucket 1, reuse now):** gmail/email, whatsapp, document_reader, web_search, pdf_generator, google_sheets, postgresql, salesforce, docusign. Build cost: ~0.
- **New-API (build, medium cost):** MedDRA, PubMed, ClinicalTrials.gov, CTRI, USPTO, EPO esp@cenet, FDA Orange/Purple Book + FAERS + Drugs@FDA, DrugBank, Drugs.com, CIMS, IQVIA/AIOCD, HL7 FHIR (HIS/EMR), MCA21, eCTD validator (Lorenz/Extedo), MES (Werum/SAP ME), QMS (MasterControl/Veeva Quality), LIMS, SAP S/4HANA (MM/QM/WM), Veeva Vault RIM/CLM, GBD, NHA, IoT MQTT broker, WMS. Cost: 8–20 dev-days each; MedDRA/IQVIA also carry data-licensing fees.
- **RPA-portal (Bucket 2, high build + maintenance):** cdsco_portal (cdsco.gov.in), cdsco-pvpi (pvpi.nic.in), **vigibase (RPA-new)** WHO-UMC E2B, Indian Patent Office portal (RPA-new), GEM + 35 state procurement portals, NPPA public DB. Cost: 15–30 dev-days per portal + ongoing breakage maintenance; DSC/eSign integration for submissions.

## 4. Knowledge Collections (seed slugs + ingestion recipe)
- `regulatory-precedent-library` — CDSCO orders, guidance, past approvals. Ingest: scrape cdsco.gov.in + upload consultant archive → chunk by section → pgvector.
- `cdsco-query-response-templates` — historical deficiency letters + drafted responses. Ingest: DMS export → tag by query type.
- `meddra-coding-precedents` — verbatim→PT mappings from prior ICSRs. Ingest: safety DB dump → embed verbatim terms.
- `cold-chain-excursion-history` — past excursions + MKT outcomes. Ingest: sensor/QA event log stream.
- `ctd-module-templates` — ICH CTD/eCTD skeletons per authority. Ingest: regulatory template repo.
- `oppi-compliance-ruleset` — OPPI/APBI code clauses + approved-label claim library. Ingest: code PDF + SmPC/label DMS.
- `patent-cliff-watchlist` + `tender-pricing-history` — Orange Book + GEM L1 transaction history.

## 5. Guardrails & Compliance
- Frameworks: Schedule M (Drugs & Cosmetics Act), GMP 21 CFR 211, ICH E6(R2) GCP, ICH E2A/E2B(R3), OPPI/APBI code, DPDP 2023.
- HITL mandatory gates: CDSCO application submission, serious-AE ICSR submission (Drug Safety Physician e-signature), batch release, product quarantine/recall, tender bid, regulatory query responses, MLR sign-off, HE dossier release. Timeout 4h, escalate 8h.
- Data: 10-year audit-trail retention (tamper-proof regulatory vault); PII anonymized before logging; patient/prescriber data classified confidential; verifier confidence threshold ≥0.95 for schema/E2B/MedDRA validation before any submit.

## 6. Scale Pattern & Cost Drivers
- Mixed profile. Real-time-monitoring: cold chain (MQTT streams), DDI (FHIR intercept, <3s SLA). High-volume-repetitive: ICSR intake, batch records, MR analytics. Approval-gated + Research+doc-gen: license/dossier/tender/content/HE.
- Cost drivers: (1) large-document LLM generation (dossiers 50k–200k pages, ICSR narratives, HE reports) dominates token spend; (2) RPA session concurrency across regulated portals (rate-limited, cap 3 concurrent); (3) IoT telemetry ingestion + time-series storage for cold chain; (4) MedDRA/IQVIA data-licensing pass-through. Daily LLM budget cap ₹5,000/agent with alert at ₹3,500.

## 7. Decision & Phasing
- **Phase 1 (regulated flagship, Bucket 2 — highest TAM/urgency):** UC-1 CDSCO license + UC-2 PV/ICSR. Build `cdsco_portal`, `cdsco-pvpi`, `vigibase` (RPA-new), MedDRA + PubMed APIs. These are the approval-gated, penalty-avoidance anchors that justify the land motion.
- **Phase 2 (compliance + commercial expansion):** UC-9 batch record, UC-8 cold chain, UC-10 tender/market access, UC-6 MR performance. Adds MES/QMS/LIMS, IoT MQTT, GEM/state RPA.
- **Phase 3 (premium research + real-time):** UC-3 trial feasibility, UC-4 dossier, UC-5 patent CI, UC-11 medical content, UC-12 pharmacoeconomics, UC-7 DDI. Integration-heavy (Veeva, eCTD validator, HL7 FHIR, code execution); gated on Tier 3 enterprise deals.

## 8. KPIs
- First-submission rejection rate: 34% → <3% (license); major-deficiency rate 50% → <10% (dossier).
- ICSR on-time submission rate: 100% within 15-day window; signal-detection latency ↓ 3×.
- Batch release cycle: 5–15 days → 1–3 days; QA review hours ↓ 75%.
- Cold-chain excursion spoilage ↓ 60%; Schedule M audit-prep time ↓ 80%.
- Tender win rate +20–30%; MR field productivity +20–30%.
- Zero missed patent cliffs; content time-to-field 12 wks → <2 wks at 100% OPPI compliance.
