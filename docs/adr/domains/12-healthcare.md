# ADR-D12: Healthcare & MedTech Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/12-healthcare/use-cases.md

## 1. Market & Monetization
- **TAM:** Indian healthcare projected at ₹25 lakh crore by 2030; 70,000+ hospitals, 700,000+ clinics. Serviceable base: multi-specialty hospitals (100–500 beds), diagnostic chains, clinics/nursing homes, TPAs/insurers, and MedTech/RCM SaaS platforms embedding the runtime.
- **Buyer:** CMO/Medical Superintendent, Revenue Cycle Manager, Quality Head (NABH), CFO for procurement; Product/Clinical-informatics lead at MedTech/RCM vendors.
- **3 Tiers (₹):**
  - Tier 1 — Clinic/Nursing Home Starter: **₹1,50,000–3,00,000/year** (~₹12,500/mo entry; 3 workflows = Appointment + Discharge Follow-up + Satisfaction Survey; ≤15 connectors; 75,000 actions/mo; 1-year HIPAA/DPDP audit).
  - Tier 2 — Hospital Professional: **₹6,00,000–14,40,000/year** (~₹80,000/mo; all 12 workflows; ≤60 connectors; 10,00,000 actions/mo; 5-year NABH/HIPAA/CEA audit; FHIR R4 + ABDM; 8-week onboarding).
  - Tier 3 — Healthcare Enterprise: **₹30,00,000–2,00,00,000/year** (dedicated/on-prem/NIC-certified runtime; unlimited; white-label EHR embedding; PM-JAY/CGHS/ESI/ABDM integration; 99.95% SLA; air-gapped option).
- **Consumption model:** Hybrid with strong per-unit component (₹12/appointment, ₹15/summary, ₹125/insurance case, ₹4/prescription validated, ₹350/bed/month, ₹35/case coded, ₹80/discharge, ₹45/PO, ₹1,800/credentialing, ₹18/survey, ₹85,000/trial-site). RCM/medical-coding API is highest-value white-label (₹12L/year+).
- **WTP:** Very high on revenue-cycle flows — medical coding (17%→4% error = ₹1.95cr/month recovered on ₹15cr claims), insurance pre-auth (₹15–25L/month recovered), bed optimization (5.4× ROI), NABH accreditation (unlocks ₹2–5cr/year cashless empanelment). Clinical-safety flows (drug interaction) sold on liability avoidance.
- **Time-to-first-revenue:** **Slow (heaviest of the four domains).** Needs FHIR/EMR + hospital-system integration, TPA-portal RPA, and NABH/HIPAA/DPDP compliance posture. 8-week Professional onboarding is standard. High TAM but long sales + integration cycle.
- **Monetization note:** Lead with the low-integration Starter pack (WhatsApp-only appointment + survey + discharge follow-up — no deep EMR write needed) to land clinics fast while the heavy EMR/RCM integrations are built for Professional/Enterprise. RCM coding + insurance pre-auth are the ACV drivers; sell to hospital chains and RCM-platform OEMs.

## 2. Use Cases -> Product Mapping
| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|-----------|---------------|-------|
| UC-1 | Appointment Scheduling & No-Show Reduction | Agent | 1/2 | epic_fhir, practo (RPA-new), whatsapp, twilio, email, google_calendar | Real-time-monitoring | No |
| UC-2 | Medical Record Summarization (Pre-Visit) | Agent | 2 | epic_fhir, practo (RPA-new), s3, document_reader, Vision LLM, TTS | Research+doc-gen | No |
| UC-3 | Insurance Pre-Authorization & Claim Submission | Agent | 2 | epic_fhir, TPA portals (RPA-portal), s3, email, whatsapp | Approval-gated | Yes (query/rejection) |
| UC-4 | Drug Interaction & Prescription Validation | Both | 2 | epic_fhir, practo (RPA-new), drug-interaction-db (RxNorm/DrugBank, new-API) | Real-time-monitoring | Yes (major interaction) |
| UC-5 | Bed & Resource Allocation Optimization | Agent | 2 | epic_fhir, hmis (RPA-new/API), whatsapp, email | Real-time-monitoring | Yes (ICU <2 beds) |
| UC-6 | Medical Billing & ICD-10/CPT Coding | Both | 2 | epic_fhir, icd10-cpt-db (new-API), practo (RPA-new), document_reader | High-volume-repetitive | Yes (physician query) |
| UC-7 | Discharge Planning & Care Coordination | Both | 2 | epic_fhir, practo (RPA-new), whatsapp, email, twilio, google_calendar | Approval-gated | Yes (high-complexity/home-care) |
| UC-8 | Regulatory Compliance (NABH/HIPAA/CEA) | Both | 2 | hmis (RPA-new), hrms (RPA-new), s3, NABH/CEA portal RPA, email | Research+doc-gen | Yes (final submission) |
| UC-9 | Medical Supply Procurement & Stockout Prevention | Agent | 2 | hmis (RPA-new), erp (RPA-new), email, whatsapp | Approval-gated | Yes (PO tiers ₹50K/₹5L) |
| UC-10 | Clinical Trial Patient Matching & Recruitment | Agent | 2 | epic_fhir, whatsapp, email, s3 (eTMF) | Research+doc-gen | Yes (investigator review) |
| UC-11 | Doctor Credentialing & Privileging | Both | 2 | NMC/DigiLocker/university portals (RPA-portal), email, whatsapp, s3, hrms (RPA-new), epic_fhir | Approval-gated | Yes (committee decision) |
| UC-12 | Patient Satisfaction Survey & Improvement | Agent | 1 | whatsapp, email, twilio, hmis (RPA-new) | High-volume-repetitive | Yes (score ≤2/5) |

## 3. Connectors Required
- **Existing (Bucket 1, reuse):** whatsapp, email (SendGrid→email), twilio (SMS), google_calendar, s3/document_reader, web_search, pdf_generator, Vision LLM + TTS via provider abstraction, athenahealth/drchrono (existing EMR connectors alongside epic_fhir). RAG for record retrieval.
- **New-API (build, medium):** **epic_fhir** (HL7 FHIR R4 — required by 9 of 12 UCs; the central build), practo, drug-interaction-db (RxNorm/DrugBank), icd10-cpt-db (ICD-10-CM/PCS + CCI bundling rules), hmis + hrms + erp generic REST adapters (vary per hospital — highest integration variability), ABDM Health ID API.
- **RPA-portal (build, high cost + compliance-critical):** **TPA/insurer portals** (40+ formats: Medi Assist, Paramount, Vidal Health, HDFC ERGO — pre-auth submission + status polling), **NABH portal** (SSR submission) + **CEA portal**, **NMC/SMC registration + DigiLocker + university** portals (credentialing PSV), Scopus/Scholar (research output for NABH). Each TPA/portal flow ~2–3 weeks incl. maintenance; TPA breadth is the single largest build in the whole platform.
- **Build cost summary:** Heaviest domain. FHIR/EMR + HMIS integration gates almost everything; TPA-portal RPA breadth is a multi-month program. Only UC-12 (and a WhatsApp-only variant of UC-1) is truly low-integration. Plan for PM-JAY/CGHS/ESI government integration (RPA-portal, `pmjay`) at Enterprise.

## 4. Knowledge Collections
- **Seed slugs:** `icd10-cpt-cci-rules`, `drug-interaction-db`, `formulary-dosing-ranges`, `tpa-preauth-form-templates` (per-insurer), `nabh-643-measurable-elements`, `cea-pcpndt-cdsco-requirements`, `clinical-summarization-templates`, `discharge-instruction-templates-multilang`, `trial-eligibility-protocols`, `credentialing-standards-privileges`, `patient-satisfaction-nabh-hcahps-instrument`.
- **Ingestion recipe:** (1) Load ICD-10-CM/PCS + CPT + CCI bundling rules as structured code DB (UC-6). (2) Ingest RxNorm/DrugBank interaction data (UC-4). (3) NABH 643 elements loaded as checklist mapped to evidence-source systems (UC-8). (4) Per-insurer TPA form templates parsed into field-mapping schemas (UC-3). (5) Patient EMR history retrieved on-demand via FHIR (not bulk-ingested — PHI minimization); pre-visit summaries generated per encounter and NOT persisted beyond audit needs. (6) `SemanticCache` for repeat coding/summarization patterns, with PHI masking on all cached content.

## 5. Guardrails & Compliance
- **Regime:** HIPAA (international telehealth), DPDP Act 2023, NABH (643 elements/100 standards), Clinical Establishment Act, PCPNDT, CDSCO drug licensing, IRB/IEC + ICMR for trials, Schedule H/H1 controlled-substance rules, NMC credentialing. Air-gapped/NIC-certified deployment option for sensitive government sites.
- **HITL gates:** insurance query + rejection (UC-3); major drug interaction alert to physician, override requires mandatory justification (UC-4); ICU availability <2 beds (UC-5); physician documentation query for coding specificity (UC-6); high-complexity/home-care discharge (UC-7); final NABH submission (UC-8); PO ₹50K→Store Manager, >₹5L→CFO/committee (UC-9); PI/Sub-I investigator review before any patient outreach (UC-10); credentials committee decision (UC-11); satisfaction score ≤2/5 → 4-hour SLA (UC-12).
- **Fail-closed:** the platform **is not a medical device** — clinical decisions remain with licensed professionals. Drug-interaction agent is alert-only (`block_prescription: false`); it never blocks or auto-changes a prescription. No auto-submission to TPA/NABH/NMC portals without HITL sign-off. Trial outreach is blocked until investigator approval (IRB/IEC compliance). Schedule H1 non-compliance is a hard `deny` on dispense workflows.
- **Audit:** 5-year tamper-proof, AES-256, PHI-masked-in-logs audit trail (`governance/audit.py`, per manifest global_settings: `phi_masking`, encryption at rest + in transit). Every prescription override, claim submission, credentialing verification, and NABH approval logged with source URL + timestamp. RLS + `rls_context()` enforces per-tenant PHI isolation at the DB.

## 6. Scale Pattern & Cost Drivers
- **Dominant patterns:** Real-time-monitoring (bed allocation 5-min poll, prescription validation <3s SLA, appointment webhooks), Approval-gated (pre-auth, procurement, credentialing, discharge), Research+doc-gen (record summarization, NABH SSR, trial matching), High-volume-repetitive (coding, satisfaction surveys).
- **Cost drivers:** (1) Sub-3-second prescription-validation latency SLA (UC-4) — needs low-latency Haiku + cached interaction lookups, not full replan loops. (2) FHIR bulk EMR pulls for pre-visit summaries × 60–80 patients/doctor/day. (3) TPA-portal RPA fragility and 4-hour polling cycles across 40+ portal formats. (4) Vision LLM for imaging/lab-report OCR. (5) Clinical-trial full-population EMR screens (large cohort scans). Route Enterprise hospitals to dedicated Celery queues; use bulkhead per-tenant concurrency.
- **Optimizations in tree:** `mcp/tool_cache.py` (drug-DB/ICD lookups), `rag/llm_response_cache.py`, `prompt_compressor.py`, `model_router` (Haiku for scheduling/validation/survey, Sonnet for summarization/pre-auth/discharge per sample manifest).

## 7. Decision & Phasing
- **Decision:** Adopt healthcare as a **high-TAM, slow-burn Enterprise vertical**. Land clinics fast on low-integration flows; build the heavy EMR/TPA/NABH stack for Professional/Enterprise ACV. Explicitly gate on compliance sign-off before any clinical-adjacent flow ships.
- **Phase 1 (weeks 0–8):** UC-12 (satisfaction) + WhatsApp-only variant of UC-1 (appointment) + UC-7 follow-up messaging. Clinic Starter tier; no deep EMR write. Establish HIPAA/DPDP audit posture.
- **Phase 2 (weeks 8–20):** Build epic_fhir + HMIS + drug/ICD DBs → UC-2, UC-4, UC-5, UC-6, UC-7 (full). Launch Hospital Professional with FHIR R4 + ABDM. RCM coding as OEM API.
- **Phase 3 (weeks 20–36):** TPA-portal RPA program → UC-3; NABH/CEA RPA → UC-8; credentialing PSV RPA → UC-11; UC-9 procurement; UC-10 trial matching (CRO channel). Government PM-JAY/CGHS/ESI integration for Enterprise.

## 8. KPIs
- Business impact: no-show rate (22%→8%), coding error rate (17%→4%) and first-pass claim acceptance (+13pts), pre-auth first-pass (82%→94%), bed occupancy (68%→82%), 30-day readmission (14%→7%), critical-drug stockouts (−87%), NABH prep (6 months→6 weeks), survey response rate (10%→62%).
- Clinical safety: preventable ADE reduction (35–50%), zero auto-blocked prescriptions (alert-only invariant held), override-justification capture rate 100%.
- Platform health: prescription-validation latency (<3s), FHIR pull success rate, TPA-portal RPA uptime + polling SLA adherence, HITL approval latency, PHI-masking coverage 100%, LLM cost per case/summary.
- Compliance: 5-year audit completeness for NABH/HIPAA/CEA, zero PHI-in-log incidents, IRB/IEC-gated outreach adherence, data-residency conformance.
