# ADR-D11: Education & EdTech Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/11-education/use-cases.md

## 1. Market & Monetization
- **TAM:** 350M learners, 1.5M schools, 50,000+ colleges, ₹5.8 lakh crore EdTech market. Serviceable base: autonomous colleges (1,500–5,000 students), K-12 school chains, coaching institutes, and EdTech platforms (50,000–200,000 MAU).
- **Buyer:** Registrar/Principal/Bursar and IQAC Coordinator at institutions; Head of Growth/Product at EdTech platforms; Admissions Head during season.
- **3 Tiers (₹):**
  - Tier 1 — Institutional Starter: **₹1,20,000–2,40,000/year** (~₹10,000/mo entry; 3 workflows, popular pack = Fee Collection + Doubt Resolution + Progress Reporting; ≤10 connectors; 50,000 actions/mo; 90-day audit).
  - Tier 2 — Campus Professional: **₹4,80,000–9,60,000/year** (~₹60,000/mo; all 12 workflows; ≤50 connectors; 5,00,000 actions/mo; 3-year NAAC-grade audit; SSO; custom fine-tuning).
  - Tier 3 — University Enterprise: **₹25,00,000–1,20,00,000/year** (dedicated runtime on-prem/private cloud; unlimited; white-label LMS embedding; UGC/CBSE portal RPA; data residency IN).
- **Consumption model:** Hybrid. Per-unit for transactional flows (₹49/assessment, ₹8/submission graded, ₹18/inquiry, ₹1.50/reminder, ₹5/payment reconciled, ₹12/report, ₹6/doubt session, ₹25/scholarship match) layered on annual institutional licenses; per-1,000-call API pricing for EdTech white-label.
- **WTP:** Strong on revenue-linked flows — fee default recovery (15%→6% = ₹90L recovered on ₹10cr collection), admission conversion (3.2×), and NAAC grade (A-grade = 15–30% fee premium). Softer on pure-productivity flows (grading, scheduling) — sold as bundle.
- **Time-to-first-revenue:** **Fast.** Mostly Bucket-1 (WhatsApp, Razorpay/PayU, Google Classroom/Moodle, SendGrid). Fee + doubt + reporting starter pack deploys quickly.
- **Monetization note:** Land with the Fee Collection agent (self-funding via recovered defaults) and 24/7 Doubt Resolution (visible student value), then expand to compliance (NAAC) as the high-ACV Enterprise anchor. State-government deployments (₹8L/lakh-students scholarship) are large but slow procurement — treat as Phase 3.

## 2. Use Cases -> Product Mapping
| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|-----------|---------------|-------|
| UC-1 | Personalized Learning Path from Assessment | Agent | 1 | moodle/google_classroom (RPA-new), google_drive, whatsapp, email, s3 | Research+doc-gen | No |
| UC-2 | Automated Assignment Grading + Feedback | Both | 1 | moodle/google_classroom (RPA-new), s3, whatsapp, email, document_reader, Vision LLM | High-volume-repetitive | Yes (plagiarism/override) |
| UC-3 | Student Progress & Parent Reporting | Agent | 1 | moodle (RPA-new), whatsapp, email, twilio, s3, pdf_generator | High-volume-repetitive | Yes (2-week at-risk) |
| UC-4 | Course Content Generation from Syllabus | Both | 1 | google_drive, s3, ms_office (RPA-new), web_search, email, document_reader | Research+doc-gen | No |
| UC-5 | Admission Inquiry Handling & Nurturing | Agent | 1 | whatsapp, email, hubspot, salesforce, facebook_lead_ads (RPA-new), razorpay, twilio | Real-time-monitoring | Yes (hot-lead route) |
| UC-6 | Fee Reminder, Collection & Tracking | Agent | 1 | razorpay, payu (RPA-new), whatsapp, email, twilio, erp (RPA-new) | High-volume-repetitive | Yes (defaulter >7d) |
| UC-7 | 24/7 Doubt Resolution Chatbot | Agent | 1 | whatsapp, s3, Vision LLM, speech-to-text, RAG | Real-time-monitoring | Yes (confidence<0.75) |
| UC-8 | Faculty Workload & Scheduling Optimization | Agent | 1/2 | erp (RPA-new), google_calendar, google_classroom (RPA-new), whatsapp, email | Approval-gated | Yes (Dean sign-off) |
| UC-9 | Scholarship & Financial Aid Matching | Agent | 2 | digilocker (RPA-portal), web_scraper, whatsapp, email, erp (RPA-new), NSP portal RPA | Research+doc-gen | No |
| UC-10 | NAAC/Accreditation Documentation | Both | 2 | google_drive, s3, erp (RPA-new), NAAC/Scopus/Scholar RPA, email, document_reader | Research+doc-gen | Yes (final SSR) |
| UC-11 | Alumni Engagement & Placement Tracking | Agent | 2 | linkedin RPA, whatsapp, email, razorpay, hubspot, erp (RPA-new) | Research+doc-gen | No |
| UC-12 | Exam Schedule & Hall Ticket Generation | Both | 1 | erp (RPA-new), whatsapp, email, s3, Vision LLM (OCR), pdf_generator | Approval-gated | No |

## 3. Connectors Required
- **Existing (Bucket 1, reuse):** whatsapp, gmail/email (SendGrid maps to email connector), twilio (SMS), razorpay, google_calendar, google_sheets, web_search, document_reader, pdf_generator, s3 (artifact storage in `rpa/perception`), hubspot, salesforce. RAG + Vision via existing embedder/provider abstraction.
- **New-API (build, low-medium):** moodle, google_classroom, canvas LMS connectors (documented REST APIs) ~1 week each; payu; ms_office/pptx-docx generation; erp/sis connector (generic REST adapter — varies per institution, biggest integration variability); facebook_lead_ads.
- **RPA-portal (build, medium-high):** **digilocker** (document pull for KYC/scholarship), **NSP + state scholarship portals** (weekly scrape + status polling), **NAAC portal** (SSR submission), Scopus/Google Scholar (publication scraping), UGC/CBSE affiliation portals (Enterprise). LinkedIn scraping for alumni (fragile, anti-bot — best-effort). Each ~1.5–2.5 weeks.
- **Build cost summary:** LMS connectors and the generic ERP/SIS adapter are the gating build for most UCs. Bucket-1 fee/doubt/reporting starter needs almost no new build. Scholarship (DigiLocker + NSP RPA) and NAAC RPA are the heaviest, deferred to Phase 3.

## 4. Knowledge Collections
- **Seed slugs:** `competency-framework-nsqf-blooms`, `course-knowledge-base` (slides/textbooks/solved-examples per subject for RAG doubt-solving), `grading-rubrics`, `naac-ssr-framework` (7 criteria, 36 metrics, 250+ data points), `scholarship-schemes-registry` (NSP/state/CSR eligibility), `fee-structure-schedule`, `admission-faq-en-hi`, `ugc-aicte-workload-norms`, `brand-institution-voice`, `top-doubts-digest`.
- **Ingestion recipe:** (1) Ingest course materials (PDF slides, textbooks, past papers) into `course-knowledge-base` via document_reader → chunk → pgvector; this powers the RAG doubt chatbot (UC-7). (2) Load NAAC SSR framework as structured checklist with source-system mappings (UC-10). (3) Weekly RPA scrape of NSP/state portals refreshes `scholarship-schemes-registry` with eligibility + deadlines. (4) Rubrics uploaded per assignment (PDF/JSON) parsed into evaluable dimensions. (5) `SemanticCache` + `llm_response_cache` dedupe recurring doubt answers and grading of similar submissions.

## 5. Guardrails & Compliance
- **Regime:** DPDP Act 2023 + minors' data (student PII, parent contacts); FERPA-mode for international/US-facing platforms; NAAC/AISHE/NIRF/UGC/AICTE reporting integrity; academic-integrity policy for grading; consent for WhatsApp/SMS to parents.
- **HITL gates:** grade overrides + plagiarism >0.85 (UC-2); at-risk student flagged 2 consecutive weeks (UC-3); hot-lead counselor routing (UC-5); defaulter >7 days + scholarship disbursal (UC-6); doubt confidence <0.75 with unsatisfied student → tutor escalation (UC-7); Dean/Registrar timetable sign-off (UC-8); IQAC/Principal final SSR approval (UC-10).
- **Fail-closed:** grading agent never finalizes plagiarism-flagged or override-requested grades without faculty approval; NAAC agent never auto-submits to portal — stages package for IQAC sign-off; scholarship auto-fill never submits forms without student confirmation; scheduling never publishes a timetable that violates UGC workload norms (hard `deny` in policy engine).
- **Audit:** 3-year retention (NAAC minimum, `ferpa_mode`, `pii_masking` per manifest global_settings). Every reminder, payment, grade change, scholarship credit, and SSR approval logged to `governance/audit.py`. Data residency IN for Enterprise.

## 6. Scale Pattern & Cost Drivers
- **Dominant patterns:** High-volume-repetitive (grading batches, weekly parent reports, fee reminders) via scheduled Celery tasks; Real-time-monitoring/event (doubt chatbot, admission inquiries via WhatsApp webhook); Research+doc-gen (content generation, NAAC SSR, scholarship matching).
- **Cost drivers:** (1) Peak concurrency — grading a 120-student assignment batch or a Sunday-8PM all-cohort report run; use Celery per-plan queues + bulkhead concurrency (`reliability/`). (2) Vision LLM for handwritten-answer OCR and attendance sheets (expensive per page). (3) RAG retrieval volume for 24/7 doubt bot at 50,000 MAU — cache aggressively. (4) Admission-season inquiry spikes (5,000–15,000 in Mar–Jul) — rate-limit WhatsApp (500/hr per manifest). Route Enterprise/large-MAU tenants to dedicated queues.
- **Optimizations in tree:** `prompt_compressor.py`, `mcp/tool_cache.py`, `rag/llm_response_cache.py`, and `model_router` (Haiku executor/verifier for grading and doubt-solving per sample manifest) materially cut per-action cost.

## 7. Decision & Phasing
- **Decision:** Adopt education as a **Phase-1/2 vertical**. Fast Bucket-1 revenue on fee/doubt/reporting; NAAC + scholarship as high-ACV Enterprise anchors built later.
- **Phase 1 (weeks 0–5):** UC-6 (fee), UC-7 (doubt), UC-3 (reporting) — the Starter pack. Needs WhatsApp + Razorpay/PayU + LMS read + RAG. Land single-campus colleges and school chains.
- **Phase 2 (weeks 5–12):** UC-1, UC-2, UC-4 (learning/grading/content — LMS + ms_office builds), UC-5 (admissions CRM), UC-12 (exams). Launch Campus Professional tier.
- **Phase 3 (weeks 12–20):** UC-9 (scholarship — DigiLocker + NSP RPA), UC-10 (NAAC — portal RPA), UC-11 (alumni), UC-8 (scheduling CSP). Launch University Enterprise + state-government deployments.

## 8. KPIs
- Business impact: fee default rate (15%→6% target), inquiry-to-application conversion (target 3.2×), off-hours doubt resolution rate (27%→84%), grading turnaround (10 days→4 hours), NAAC prep time (14 months→6 weeks), scholarship application rate (22%→71%).
- Platform health: agent success/replan rate per UC, HITL approval latency, doubt-bot confidence-threshold accuracy, RAG hit rate, Vision OCR error rate, RPA portal uptime (NSP/NAAC), LLM cost per graded submission / doubt session.
- Compliance: audit completeness for NAAC/NIRF submissions, PII-masking coverage, consent-tracked message rate, zero UGC-norm-violating timetables published.
- Commercial: Starter→Professional upgrade rate, actions-per-month utilization vs tier cap, Enterprise pipeline (universities + state departments).
