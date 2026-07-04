# ADR-D26: Construction & Project Management Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/26-construction/use-cases.md

## 1. Market & Monetization
- **TAM:** ₹21 lakh crore construction sector; ₹2 lakh crore/year bled to cost overruns and delays; 35,000+ RERA-registered projects. Spend is pain-driven (penalties, LD, cash-flow, disputes).
- **Buyer:** EPC contractor Ops/Projects Head, real-estate promoter/developer CFO, PMC Director, QS Head; for enterprise, CXO at L&T/Shapoorji/NCC-tier and NHAI/PWD project units.
- **3 tiers (₹):** Tier 1 Starter ₹15,000/month (≤5 projects, delay/RA-bill/RERA-alert/safety modules, ₹25k setup); Tier 2 Professional ₹75,000/month (≤20 projects, adds procurement, equipment, BOQ/VO, photo reports, tender intel; Primavera/SAP/Procore integrations, ₹75k setup); Tier 3 Enterprise ₹3,00,000/month custom SLA (unlimited projects, SAP HANA deep integration, lender MIS, white-label portal, FIDIC/SPV modules, ₹3L setup).
- **Consumption model:** per-project/month subscription + transaction fees (₹50/RA bill above 50, ₹50/worker above 500, ₹2,500/machine, ₹2,000/assisted bid).
- **WTP:** Moderate-to-high, ROI-anchored — one 30-day schedule recovery saves ₹2 crore LD; 8% material saving on ₹30 crore = ₹2.4 crore; a single RERA deadline saved covers 12 months of fees. Cited 8–15× year-one ROI.
- **Time-to-first-revenue:** Fast — per-project onboarding, Bucket-1 modules (RA bill, cash flow, photo report) live in days; RERA data migration ~1–2 weeks.
- **Monetization note:** Land with cheap high-frequency Bucket-1 wins (RA bills, site reports, cash flow) at project level, then upsell Professional bundle once ERP/Primavera/Procore integration is in place; per-transaction fees scale revenue with contractor activity.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Schedule Monitoring & Delay Prediction | Agent | 3 | Primavera P6 (New-API), MS Project (New-API), code_execution, pdf_generator, email, whatsapp, Jira, Procore (New-API), IMD weather (New-API) | Real-time-monitoring | Yes (escalation/date revision) |
| UC-2 | Contractor Payment — RA Bills & Retention | Agent | 2 | document_reader, SAP/Oracle ERP (New-API), accounting, email, whatsapp, bank API (New-API), pdf_generator, Procore, SharePoint | Approval-gated | Yes (QS/PM) |
| UC-3 | Material Procurement & Price Benchmarking | Both | 2 | steel/cement dealer portals (RPA-new), web_search, SAP MM (New-API), accounting, email, pdf_generator, IndiaMART (New-API) | High-volume-repetitive + Research | Yes (procurement head) |
| UC-4 | RERA Filing & Ongoing Compliance | Agent | 2 | rera_portal (RPA — MahaRERA/KRERA/UP/HRERA), pdf_generator, email, scheduler, google_drive, Procore, ERP | Approval-gated | Yes (promoter e-sign) |
| UC-5 | Labour Workforce Management | Agent | 2 | biometric attendance (New-API), bank API, pdf_generator, whatsapp, slack, sms, BOCW state portals (RPA-new), ERP | High-volume-repetitive | Yes (payroll approval) |
| UC-6 | Safety Incident Reporting & Investigation | Agent | 2 | whatsapp, speech-to-text/Vision LLM, pdf_generator, email, slack, Jira, web_search, Shram Seva/labour portals (RPA-new) | Real-time-monitoring + Research | Yes (fatality notice) |
| UC-7 | BOQ Verification & Variation Order Mgmt | Agent | 2 | document_reader, web_search (CPWD DSR), ERP, accounting, pdf_generator, email, slack, Procore, SharePoint | Approval-gated | Yes (QS/PM) |
| UC-8 | Site Progress Report from Photos | Agent | 1 | Vision LLM, google_drive, whatsapp, pdf_generator, Primavera P6, IMD weather, code_execution (matplotlib), SharePoint, Procore | Research+doc-gen | No |
| UC-9 | PWD/CPWD/NHB Tender Prep & Bidding | Both | 2 | pwd_cpwd + GePNIC/CPPP/IREPS/GeM/state (RPA-new), document_reader, pdf_generator, web_search, email, code_execution, scheduler, SharePoint | Approval-gated + Research+doc-gen | Yes (bid approval) |
| UC-10 | Equipment Utilization & Maintenance | Agent | 3 | equipment telematics (New-API — VisionLink/KOMTRAX/Trimble), ERP, email, slack, whatsapp, pdf_generator, web_search, code_execution, SharePoint | Real-time-monitoring | No (work orders) |
| UC-11 | Quality Inspection & Snag List Mgmt | Agent | 1 | Vision LLM, whatsapp, Procore, Jira, pdf_generator, email, SharePoint | High-volume-repetitive | No |
| UC-12 | Cash Flow Forecasting & Working Capital | Agent | 1 | accounting, bank API (HDFC/SBI/Axis corporate), ERP, code_execution (pandas), pdf_generator, email, PowerBI, google_sheets | Research+doc-gen | No |

## 3. Connectors Required
- **Existing (Bucket 1):** email, whatsapp, document_reader, web_search, pdf_generator, google_sheets, google_drive, sms. Cost: ~0.
- **New-API (build, medium):** Primavera P6, MS Project Online, SAP/Oracle ERP (MM/PS/Cost), accounting connector, corporate bank API (HDFC/SBI/Axis NEFT/RTGS + statements), Procore, SharePoint, IMD/OpenWeatherMap, biometric attendance (ZKTeco/eSSL/Matrix), equipment telematics (CAT VisionLink/KOMTRAX/JD-Link/Trimble), PowerBI, Jira, IndiaMART. Cost: 8–15 dev-days each.
- **RPA-portal (Bucket 2, high build + fragmentation maintenance):** **rera_portal** (MahaRERA, Karnataka, UP, Haryana — state-by-state), **pwd_cpwd** tenders (RPA-new: GePNIC/CPPP/IREPS/GeM + state e-proc), BOCW state cess portals (RPA-new), Shram Seva/state labour portals (RPA-new), steel/cement dealer price portals (RPA-new). Cost: 15–25 dev-days per portal family; each state RERA/tender portal is a distinct target with recurring breakage.

## 4. Knowledge Collections (seed slugs + ingestion recipe)
- `cpwd-dsr-2021-rates` — CPWD/state Schedule of Rates for VO/tender rate justification. Ingest: parse DSR PDFs → item-code keyed table → pgvector.
- `rera-compliance-calendar` — per-project quarterly due dates + amendment triggers. Ingest: RERA registration exports + state rules.
- `boq-specification-library` — contract BOQ + specs per project. Ingest: DMS/Procore export.
- `material-price-index` — rolling steel/cement/aggregate prices. Ingest: daily RPA crawl → time-series.
- `vendor-performance-scores` — delivery/quality history. Ingest: accounting + PO close-out feed.
- `safety-corrective-action-library` — IS 18001/OSHA remediations by incident type. Ingest: web_search + past investigation reports.
- `tender-prequalification-profiles` — contractor turnover/experience/networth per entity. Ingest: company doc repository.

## 5. Guardrails & Compliance
- Frameworks: RERA (10-year defect liability, quarterly filing), BOCW Act (Form XIV/Form 5, 1% cess), Factories Act (Form 23A accident report, 2× OT), IS 18001/OHSAS 18001 safety, Workmen's Compensation Act 1923, IS 7:2016 (7-year record retention), DPDP 2023 for worker/customer PII.
- HITL mandatory gates: external delay escalation + contract completion-date revision, RA bill certification (QS→PM→Finance), RERA filing/promoter e-sign, tender bid submission, VO approval, fatality/serious-injury notification, ERP payment orders.
- Data: confidential classification; 7-year retention (2555 days); worker Aadhaar/bank data masked; full audit trail to Procore/SharePoint for client + lender + labour audit.

## 6. Scale Pattern & Cost Drivers
- Mixed. Real-time-monitoring: schedule (daily Monte Carlo), equipment telematics (30-min polling). High-volume-repetitive: labour payroll, snag lists. Approval-gated: RA bills, VO, RERA, tenders. Research+doc-gen: site reports, cash flow.
- Cost drivers: (1) **RPA session sprawl** across fragmented state RERA/tender/labour portals is the dominant build + run cost; (2) Vision LLM inference for site-photo classification and snag before/after comparison scales with photo volume; (3) telematics streaming + code-execution (Monte Carlo, pandas cash-flow, matplotlib) compute; (4) per-bill/per-worker/per-machine transaction volume drives token usage. Manifest caps: max_iterations 8, replan_on_failure true.

## 7. Decision & Phasing
- **Phase 1 (Bucket 1 fast wins, existing connectors):** UC-2 RA bills, UC-8 site progress reports, UC-12 cash flow, UC-11 snag lists. Document_reader + Vision LLM + ERP/bank APIs; days-to-live, immediate ROI proof for land motion.
- **Phase 2 (Bucket 2 RPA anchors — approval-gated core per domain note):** UC-4 RERA filing, UC-9 PWD/CPWD tender prep, UC-3 material procurement, UC-5 labour + UC-6 safety (portal filings), UC-7 BOQ/VO. Build rera_portal, pwd_cpwd (RPA-new), BOCW/Shram Seva RPA.
- **Phase 3 (Bucket 3 integration-heavy):** UC-1 schedule monitoring (Primavera/MS Project deep integration + Monte Carlo), UC-10 equipment telematics. Gated on Professional/Enterprise tier with CAT/Komatsu telematics access.

## 8. KPIs
- RA-bill processing time: 5–7 days → <8 hours; interest-claim leakage eliminated.
- RERA on-time filing rate: 100% (zero missed quarterly deadlines).
- Tender win rate +one ₹5 crore project/year (22× module ROI); bid prep 60h → <8h.
- Schedule: SPI-triggered recovery on critical path; ≥3-month early delay warning.
- Material price variance held <3% vs. index; 8%+ procurement saving.
- LTI frequency/severity rate ↓; corrective-action closure on SLA.
- Cash-flow forecast accuracy tracked weekly (13-week rolling); negative-week alerts 4 weeks ahead.
