# ADR-D29: Recruitment & Staffing Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/29-recruitment-staffing/use-cases.md

## 1. Market & Monetization
- **TAM:** India staffing ₹32,000 crore, 10M+ placements/year across IT/ITeS, BFSI, manufacturing, retail, healthcare. Addressable = recruiter productivity + statutory payroll/compliance automation.
- **Buyer:** TA head / recruitment delivery head (staffing firms, RPOs) and corporate HR/CHRO. For UC-8/9 (payroll, CLRA compliance): compliance officer / finance head.
- **3 tiers (₹):** Starter ₹29,999/mo (boutique / in-house TA, <500 placements/yr — 5 JDs, 2k resumes, 2 seats). Professional ₹89,999/mo (mid-size, 500–5k placements — unlimited JDs, 15k resumes, sourcing, BGV, payroll 500 workers, 3-state compliance, 15 seats). Enterprise ₹2,49,999/mo + ₹20/incremental placement (>5k placements — campus module, pan-India compliance, white-label, custom ATS).
- **Consumption model:** Hybrid — per-unit for high-volume ops (₹8–15/resume, ₹500–1,200/sourced candidate, ₹200/offer, ₹150–400/BGV, ₹25–80/payslip, ₹50/invoice) layered on flat SaaS + per-placement overage at enterprise.
- **WTP:** High — recruiter time is the direct cost centre; cost-per-hire reduction of 55–70% is a board-level metric. Statutory compliance (PF/ESIC/CLRA) is a penalty-avoidance must-buy.
- **Time-to-first-revenue:** Fast (2–3 weeks) for resume screening + scheduling + JD gen (ATS + Gmail + doc parser). Sourcing RPA (Naukri) and payroll/compliance (EPFO/ESIC RPA) take 6–10 weeks.
- **Monetization note:** Bulk resume screening (UC-1) is the highest-volume wedge and cleanest ROI story — land here, expand into sourcing then payroll/compliance stickiness (statutory lock-in = low churn).

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Bulk Resume Screening (IT/BPO) | Both | 1 | zoho_recruit (New-API), gmail, outlook (New-API), document_reader, linkedin, audit | High-volume-repetitive | Yes (top-20 spot-check) |
| UC-2 | Candidate Sourcing (LinkedIn/Naukri/GitHub) | Agent | 2 | naukri (RPA-new), linkedin, github (New-API), browser-RPA (iimjobs/AngelList), document_reader, gmail, zoho_recruit | High-volume-repetitive | No (outreach auto) |
| UC-3 | Interview Scheduling Across Panels | Both | 1 | zoho_recruit, google_calendar, gmail, zoom (New-API), whatsapp, slack | Approval-gated (coordination) | No |
| UC-4 | Background Verification Orchestration | Agent | 2 | zoho_recruit, BGV-vendor API (New-API: AuthBridge/IDfy), Aadhaar/PAN verify (New-API), NAD API (New-API), DigiLocker/EPFO (RPA-new), browser-RPA, document_reader, keka (New-API), audit | Approval-gated | Yes (any discrepancy) |
| UC-5 | Offer Letter & Onboarding Paperwork | Both | 1 | keka, zoho_recruit, document_reader, pdf_generator, DocuSign/Leegality (New-API), gmail, audit | Approval-gated + Research+doc-gen | Yes (via e-sign approver) |
| UC-6 | Client Requirement Analysis & JD Generation | Both | 1 | gmail, outlook, zoom, speech-to-text (New — transcriber), document_reader, vector-KB, naukri (RPA-new), linkedin, CMS/website, audit | Research+doc-gen | Yes (client/recruiter review) |
| UC-7 | Talent Pool Warm-Up & Engagement | Agent | 1 | zoho_recruit, web_search, gmail, whatsapp, slack, reporting, audit | High-volume-repetitive | No |
| UC-8 | Payroll for Contract/Temp Workforce | Agent | 1+2 | keka, attendance API (New-API), payroll-engine (New — compute), pdf_generator, bank SFTP/NEFT (New-API), gmail, audit; PF ECR/ESIC files | High-volume-repetitive + Approval-gated | Yes (finance approves register) |
| UC-9 | Compliance Mgmt (CLRA, PF/ESIC) | Agent | 2 | keka, document_reader, epfo (RPA-new), esic (RPA-new), state Labour Dept portals (RPA-new), slack, gmail, reporting, audit | Approval-gated + Real-time-monitoring (calendar) | Yes (penalty risk >₹10k) |
| UC-10 | Timesheet Collection & Invoice Generation | Both | 1 | gmail, whatsapp, document_reader (OCR), keka, pdf_generator, browser-RPA (client portal upload), tally (New-API), zoho_books (New-API), slack, audit | High-volume-repetitive | Yes (invoices >₹5L) |
| UC-11 | Diversity Analytics & JD Bias Detection | Agent | 1 | zoho_recruit, data-masking, analytics, web_search, reporting, slack, audit | Research+doc-gen | No |
| UC-12 | Campus Recruitment Coordination | Both | 1+2 | gmail, google_calendar, form-builder, HackerRank/AMCAT (New-API), pdf_generator, reporting, CRM, audit | Approval-gated | Yes (low-acceptance colleges) |

## 3. Connectors Required
- **Existing (Bucket 1):** gmail, linkedin, whatsapp, sms, google_calendar, document_reader, web_search, pdf_generator, slack, salesforce/hubspot (CRM). Build cost ~0.
- **New-API:** zoho_recruit, keka, outlook, github, zoom, DocuSign/Leegality, tally, zoho_books, HackerRank, AMCAT, attendance API, bank SFTP/NEFT batch, BGV-vendor (AuthBridge/IDfy), Aadhaar/PAN verification, National Academic Depository, speech-to-text/transcriber (shared platform capability). Build 2–5 days each; bank SFTP and BGV vendors need contracts + sandbox (1–2 weeks).
- **RPA-portal (New):** naukri (candidate search/download — sourcing core), epfo (PF ECR + employment history), esic (contribution filing), DigiLocker (consent-based EPFO pull), state Labour Dept portals (CLRA license renewal — 73 state variants), client vendor portals (invoice upload). Build ~1 week each; epfo/esic/state-labour are high-maintenance, brittle, and the primary Bucket-2 investment.
- **Compute engines (New — not connectors):** payroll gross-to-net engine (PF 12%, ESIC 0.75%, PT, TDS by state slab), CTC-split logic.

## 4. Knowledge Collections
Seed slugs: `jd-role-templates` (5,000+ SFIA-aligned), `bias-term-dictionary` (2,400 terms — gender/age/caste/ability), `clra-registers-14` (Form D/XIII), `pf-esic-pt-slab-rules` (state-wise), `ctc-structuring-rules`, `bgv-check-matrix`, `offer-letter-templates` (grade/location/entity versioned), `campus-relationship-scores`.
Ingestion recipe: (1) ingest firm's historical JDs + role templates into pgvector for UC-6 matching; (2) load statutory slab tables (PF/ESIC/PT/TDS) + CLRA register formats + 73 state amendments as structured compliance ground truth, versioned by effective date; (3) seed bias dictionary for UC-11 lexical + LLM scan; (4) template library with version control (latest legal-approved always selected in UC-5).

## 5. Guardrails & Compliance
- **DPDP Act:** candidate PII consent required (dpdp_consent_check); PII masking for diversity analytics (UC-11); 7-year (2555-day) retention for statutory records.
- **Bias controls:** bias detection on generated JDs (UC-6/11); audit trail of every scoring decision (UC-1) for bias review; anonymized funnel analysis.
- **BGV adverse action:** mandatory HITL (HR+legal) before any adverse hiring decision on a discrepancy; candidate consent before EPFO/Aadhaar pulls.
- **Statutory filing HITL:** finance approves payroll register; compliance officer signs filings with penalty risk >₹10k; offers above Grade 7 / ₹25 LPA need finance approval.
- **Cost controls:** per-JD LLM budget cap (~200 calls), daily spend cap ₹5,000.
- **Hallucination risk:** offer letters (wrong CTC/date) and payroll are high-stakes numeric — deterministic compute engine, not LLM, for money math; LLM only structures/explains.

## 6. Scale Pattern & Cost Drivers
- **High-volume-repetitive (dominant, defining pattern):** bulk resume screening (500–2,000/role — the domain's signature scale), sourcing across platforms, talent-pool campaigns, payroll fan-out, timesheet/invoice. Cost driver = document-parse + LLM scoring per resume — mitigate with cheap pre-filter (rules/keyword) then LLM only for ranked shortlist; batch embeddings.
- **Approval-gated:** BGV, offers, compliance filings, high-value invoices — throughput bounded by human SLA, not compute.
- **Real-time-monitoring:** compliance calendar (daily due-date checks), scheduling conflict watch.
- Primary cost lever: resume-screening LLM calls at volume (per-resume unit price must exceed marginal LLM cost). Naukri/EPFO RPA reliability is the primary operational cost/risk.

## 7. Decision & Phasing
- **Phase 1 (land):** UC-1 resume screening, UC-3 scheduling, UC-6 JD generation — Bucket-1, ATS + Gmail + doc parser, fastest ROI, clearest volume story.
- **Phase 2 (expand):** UC-2 sourcing (Naukri RPA + GitHub), UC-5 offers, UC-7 talent engagement, UC-11 diversity — moderate build, high stickiness.
- **Phase 3 (enterprise/statutory):** UC-8 payroll, UC-9 CLRA/PF/ESIC compliance, UC-4 BGV, UC-12 campus — heaviest RPA (epfo/esic/state-labour) + compute engine, highest ACV and lock-in.
- 12 UCs — **not a thin domain, no enrichment flag.** The Naukri/EPFO/ESIC RPA suite is the moat; fund the shared transcriber and payroll compute engine early.

## 8. KPIs
- Time-to-fill 42 → <10 days (headline). Recruiter screening time −85% (UC-1); shortlist accuracy > 58% baseline.
- Sourcing pipeline ×3 (UC-2); scheduling coordinator hours −280/mo (UC-3).
- BGV turnaround −60% (UC-4); offer processing time −90%, zero offer errors (UC-5).
- JD creation time −95%, application-to-interview +35% (UC-6); database re-engagement +55% (UC-7).
- Payroll processing time −80%, zero statutory penalties (UC-8); on-time filing 100% (UC-9); billing cycle −92% (UC-10).
- Gender diversity at shortlist +35% (UC-11); offer acceptance +22% (UC-12). Platform: per-resume LLM cost, RPA portal success rate, HITL SLA adherence.
