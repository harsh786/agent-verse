# ADR-D37: Public Health & Community Healthcare Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/37-public-health/use-cases.md

Applies ADR-0001 to the Public Health vertical. This is a **government-as-buyer, RPA-heavy, real-time-monitoring** vertical: state/district NHM offices procure via GeM/NIC on annual contracts, integrations are mostly no-API government health MIS portals (RPA), and the dominant workloads are surveillance monitoring, beneficiary tracking fan-out, and mandated financial/reporting doc-gen.

## 1. Market & Monetization

- **TAM.** NHM alone: ₹35,000 crore/year across 1.75 lakh sub-centres, 25,000 PHCs, 5,700 CHCs, 760 district hospitals, 10 lakh ASHAs, 2 lakh ANMs, 740 districts. Adjacent budgets in scope: AB-PMJAY ₹5,800 crore claims, Jal Jeevan Mission ₹60,000 crore/year, UIP ₹3,200 crore, IDSP ₹850 crore. Serviceable software-licence market: ₹300–600 crore/year across 740 districts + 28 states.
- **Buyer.** **Government** — State Health Departments / NHM State Mission Directors, District Health Societies / CDMO offices, National Health Authority, plus donor-funded programmes (WHO, UNICEF, Gates Foundation, World Bank RMNCH+A). Procurement via **GeM listing + NHM route; STQC empanelment and NIC-cloud data residency are gating requirements.** Not self-serve — sold as annual contracts.
- **Pricing tiers (₹) — govt annual contracts (₹L–Cr):**
  - **Tier 1 — Block / PHC Cluster ₹50,000/block/year:** immunisation tracking, ASHA monitoring, medicine stockout prevention, maternal-health tracking; WhatsApp-based ASHA/ANM data capture; ANMOL/HMIS/ASHA-Soft integration; 1-day training.
  - **Tier 2 — District Package ₹5,00,000/district/year:** all block modules + IDSP surveillance, PM-JAY claim quality, beneficiary enrollment, NHM financial reporting, needs assessment; dedicated coordinator + monthly review; CDMO/DPM dashboards.
  - **Tier 3 — State Enterprise ₹2,00,00,000/state/year (₹2 crore):** all districts, state consolidated dashboard, state-scheme integration (Aarogyasri, MSBY), 2 custom modules, embedded state team, 99.5% uptime, NIC-cloud/State-Data-Centre residency.
- **Consumption model.** Mostly **flat annual per-block/district/state licence** (govt prefers fixed budgets over metered). Exception — **PM-JAY claim QC (UC-4) is per-transaction:** ₹50/claim (govt hospital), ₹150/claim (private empanelled), or ₹30,000/mo for 500+ claims/mo hospitals. Beneficiary enrollment (UC-5) offers outcome-based ₹50/household. International/donor deployments: USD 5,000/district/year (World Bank TA).
- **WTP.** High but budget-cycle-bound (annual APIP allocations). Justified by hard ROI: PM-JAY QC = 57× ROI (rejection 20%→3%); outbreak detection 14 days→2 hours = ₹10 crore/district/year avoided treatment; UC-choke on preventable maternal/child deaths carries political priority.
- **Time-to-first-revenue.** Slow — **6–12 months** due to government procurement + STQC certification + NIC-cloud deployment. Fastest wedge is **PM-JAY claim QC sold to empanelled hospitals** (B2B, per-claim, not govt procurement) — weeks not months.
- **Monetization note.** Government sales are slow but extremely sticky (multi-year, renews on APIP). De-risk the long sales cycle by landing hospital-side PM-JAY QC (private buyer, fast) and donor-funded pilots first, then convert to district/state licences.

## 2. Use Cases → Product Mapping

| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|:------:|------------|---------------|:-----:|
| UC-1 | Disease Surveillance & Outbreak Early Warning (IDSP) | Agent | 2 + RPA-new | idsp/IHIP, WQMIS (RPA-new), email, whatsapp, code_execution, Jira (catalog) | Real-time-monitoring | Yes — RRT escalation |
| UC-2 | Immunisation Campaign Planning & Coverage | Agent | 2 + RPA-new | U-WIN/ANMOL (RPA-new), nhm_portal/HMIS, DHIS2 (RPA-new), IVR (new-API), whatsapp, sms, code_execution | Real-time-monitoring + High-volume (daily due-lists) | Yes — special session launch |
| UC-3 | ASHA/ANM Performance & Payment | Agent | 2 + RPA-new | ASHA-Soft (RPA-new), HMIS, ANMOL, PFMS (RPA-new/new-API), bank DBT (new-API), whatsapp, sms, code_execution | High-volume-repetitive (monthly batch) | Yes — BMO payment approval |
| UC-4 | AB-PMJAY Claim Processing QC | Both | 2 | pmjay, HIS/ERP (new-API), document_reader, pdf_generator, email, code_execution | High-volume-repetitive (per-claim) | Optional (remediation loop) |
| UC-5 | Health Scheme Beneficiary Enrollment | Agent | 2 + RPA-new | pmjay, PM-SBY + state scheme portals (RPA-new), SECC DB (RPA-new), HMIS, whatsapp, sms, code_execution | High-volume-repetitive | Yes — Aadhaar eKYC |
| UC-6 | Medicine Supply Chain / Stockout Prevention | Agent | RPA-new | DVDMS/AUSHADHI (RPA-new), eVIN cold-chain (RPA-new), whatsapp, email, code_execution, scheduler | Real-time-monitoring | Yes — critical-drug escalation |
| UC-7 | Maternal & Child Health Tracking (ANC/PNC) | Agent | RPA-new | ANMOL (RPA-new), nhm_portal/HMIS, whatsapp, IVR (new-API), sms, pdf_generator, code_execution | Real-time-monitoring | Yes — high-risk referral |
| UC-8 | Community Needs Assessment & DLHS Reporting | Both | 2 + RPA-new | nhm_portal/HMIS, ASHA-Soft (RPA-new), DHIS2 (RPA-new), document_reader, web_search, pdf_generator, code_execution | Research+doc-gen | No (report review) |
| UC-9 | Water & Sanitation Compliance (Jal Jeevan) | Agent | RPA-new | JJM IMIS (RPA-new), WQMIS (RPA-new), SBM-G IMIS (RPA-new), whatsapp, Vision LLM (builtin), IoT sensor (new-API), pdf_generator, code_execution | Real-time-monitoring | Yes — non-functional escalation |
| UC-10 | NHM Financial Reporting & Utilisation Certificates | Both | 2 + RPA-new | PFMS (RPA-new/new-API), bank API (new-API), treasury portal (RPA-new), nhm_portal, accounting connector, pdf_generator, email, code_execution | Research+doc-gen (quarterly) | Yes — ambiguous FMR + UC submit |

Surveillance/tracking UCs ship as **standing Agents** (cron + webhook triggers); QC, needs-assessment, and financial UCs ship as **both** (agent + on-demand template).

## 3. Connectors Required

- **Existing catalog (Bucket 1) — zero build:** email, whatsapp, sms, document_reader, web_search, pdf_generator (document generation), google_sheets, postgresql (beneficiary/tracking registries), Jira (RRT checklist UC-1). Plus builtin capabilities: `code_execution` (epidemic thresholds, spatial clustering, Folium/Geopandas mapping, coverage/risk stats, FMR classification — used in 9 of 10 UCs) and **Vision LLM** (tap-photo analysis UC-9) via the provider abstraction.
- **RPA-portal, in scope (Bucket 2, prioritized):** `pmjay` (UC-4, UC-5), `idsp`/IHIP (UC-1), `nhm_portal`/HMIS = hmis.nhp.gov.in (UC-2, UC-8, UC-10). These are the listed Bucket-2 health portals.
- **RPA-portal, NEW builds required (RPA-new — no API):** `anmol`/U-WIN = anmol.nhp.gov.in (UC-2, UC-7), `asha_soft` = ashakendra.nhp.gov.in (UC-3, UC-8), `dvdms`/AUSHADHI = aushadhi.gov.in (UC-6), `jjm_imis` = ejalshakti.gov.in (UC-9), `wqmis` (UC-1, UC-9), `sbm_grameen_imis` (UC-9), `evin` cold-chain (UC-6), `dhis2` state instances (UC-2, UC-8), treasury portal (UC-10), SECC/PM-SBY/state-scheme portals (UC-5). **Build-cost flag: HIGH** — this domain is almost entirely no-API government health MIS.
- **New-API connectors to build:** `pfms` (PFMS API for UC-3, UC-10 — may expose API to authorized govt tenants, else RPA), bank DBT API (UC-3), HIS/hospital-ERP API (UC-4), IVR/voice provider e.g. Exotel (UC-2, UC-7), IoT water-flow sensor API (UC-9).

## 4. Knowledge Collections (seed slugs + ingestion recipe)

- `uip-immunisation-schedule` — 12-vaccine national schedule + intervals. Recipe: MoHFW/UIP official schedule; refresh on policy update.
- `epidemic-threshold-protocols` — CDC EARS thresholds adapted per priority disease (dengue/cholera/measles/lepto). Recipe: IDSP guidelines + 5-year historical district means (tenant data).
- `hbp2-package-codes` + `icd10-mapping` — AB-PMJAY Health Benefit Package 2.0 codes + ICD-10 for claim QC (UC-4). Recipe: NHA HBP master + ICD-10; refresh on HBP revisions.
- `nhm-fmr-classification` — 40+ Fund Management Report head definitions for expenditure classification (UC-10). Recipe: NHM FMR guidelines.
- `nhm-indicator-definitions` — HMIS Form-8/RCH indicators, NFHS-5/DLHS-4 district baselines for benchmarking (UC-8). Recipe: NHM MIS handbook + published NFHS/DLHS baselines.
- `maternal-risk-criteria` + `vaccine-cold-chain-protocol` + `aefi-reporting-guidelines` — clinical thresholds and protocols (UC-7, UC-2). Recipe: MoHFW guidelines.
- `nlem-essential-medicines` — National List of Essential Medicines + life-saving drug list for stockout criticality (UC-6). Recipe: NLEM published list.

Store official govt clinical/programme guidelines and derived rule tables only; no PII in KB.

## 5. Guardrails & Compliance

- **Regime.** IT Act 2000, **DPDP 2023 (high-sensitivity health PII + Aadhaar)**, NHM data-privacy policy. Government procurement compliance: **STQC empanelment, GeM listing, NIC-cloud / State-Data-Centre data residency (India-only).**
- **Mandatory HITL gates (fail-closed):** ASHA/beneficiary payments (UC-3) require Block-Medical-Officer digital approval before PFMS transfer; special immunisation session launch (UC-2) and state escalation gated (per manifest: zero-dose >10, coverage <50% for 3 weeks); high-risk maternal referral (UC-7) and critical-drug/outbreak escalation (UC-1, UC-6, UC-9) require officer confirmation; ambiguous FMR classification + UC submission (UC-10) gated. Approvers are named district officials; 24-hour SLA.
- **Fail-closed:** on data-integrity gaps (claim missing docs, unverifiable ASHA service) → flag and route to human, never auto-pass; PM-JAY QC blocks submission until remediation.
- **Audit/evidence:** append-only audit trail; **Aadhaar masked in logs**; AES-256 at rest; 1095-day (3-year HMIS) / longer retention per policy. RLS-enforced tenant isolation per district/state.
- **PII handling:** beneficiary health data is high-sensitivity; consent-gated; masking in all logs and screenshots.

## 6. Scale Pattern & Cost Drivers

- **Dominant shape: Real-time-monitoring (Pattern 3)** — beat monitors + dedup + circuit breakers + backpressure for IDSP surveillance (30-min threshold), immunisation due-lists (daily 06:00/17:00 cron), stockout monitoring, JJM verification, MCH tracking. Overlaid with **High-volume-repetitive (Pattern 1)** for monthly ASHA-payment fan-out and per-claim PM-JAY QC, and **Research+doc-gen (Pattern 4)** for quarterly UC/APIP reports.
- **Expected goal volume:** very high fan-out at scale — a state licence spans hundreds of districts × thousands of sub-centres × lakhs of beneficiaries; daily due-list and monitoring jobs multiply across facilities.
- **Cost drivers:** mixed. **RPA-session cost** for the many no-API MIS portals (autoscaled headless-browser farm, session/login caching per district). **`code_execution` compute** (Python/pandas/geopandas/Folium in 9/10 UCs) — runs in sandboxed runtime, meter memory/CPU. **Communication cost** — IVR calls (max 2,000/day/manifest) + SMS/WhatsApp at population scale is a real line item. **Token cost** is modest and mostly cheap-model-routable (classification, extraction, narrative gen); route bulk classification (FMR, ICD) to cheap/local models. Cache portal navigation and KB lookups.
- **Infra:** per-plan Celery queues (state tenants isolated from block tenants); per-tenant bulkhead so a large state cannot starve a block; NIC-cloud deployment target.

## 7. Decision & Phasing

- **Flagship first:** **UC-4 AB-PMJAY Claim QC** — fastest revenue (hospital B2B, per-claim, not slow govt procurement), only Bucket-2 `pmjay` connector needed, 57× ROI story, immediately demonstrable. Land empanelled hospitals while the district/state pipeline matures.
- **Fast-follow (block/district licence anchor):** **UC-2 Immunisation + UC-7 Maternal Health + UC-3 ASHA Payment** — the Tier-1 block bundle; drives the ₹50,000/block and ₹5,00,000/district SKUs. Gated on `anmol`/U-WIN, `asha_soft`, HMIS RPA + IVR + PFMS builds.
- **Connector-gated / donor-pilot led:** UC-1 IDSP surveillance (marquee ROI, needs idsp/IHIP + WQMIS), UC-6 medicine supply (DVDMS/AUSHADHI + eVIN), UC-9 Jal Jeevan (JJM IMIS + Vision + IoT — cross-ministry, best as World Bank/ADB-funded pilot), UC-8/UC-10 reporting (HMIS + PFMS + treasury).
- **Prerequisites:** STQC empanelment + NIC-cloud/data-residency (ADR-0001 flagged DPDP/residency work) + ADR-0001 Phase-0 P0 (cross-replica HITL, RPA pool) before any government production deployment.

## 8. KPIs

- **Adoption:** blocks/districts/states licensed; hospitals on PM-JAY QC; facilities integrated; ASHAs/beneficiaries under management.
- **Reliability:** RPA success rate per MIS portal (>95%, DOM-change alerting); eval pass % for claim-QC and risk-stratification accuracy; false-positive rate on outbreak alerts.
- **Cost:** RPA-session + code_execution compute + IVR/SMS cost per district; token cost/goal; % steps on cheap/local models.
- **Revenue:** revenue/district & /state; claim-QC volume × margin; renewal rate on annual contracts; donor-pilot→licence conversion.
- **Outcome (sold on these):** outbreak mean-time-to-detection (14 days→<2 h); immunisation coverage lift (+5%); ASHA payment cycle (120→21 days); PM-JAY rejection rate (20%→<3%); PHC stockout days (50→<10/yr); UC preparation (3 weeks→3 days).
