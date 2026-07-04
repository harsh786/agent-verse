# ADR-D36: Architecture & Interior Design Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/36-architecture-interior-design/use-cases.md

## 1. Market & Monetization
- **TAM:** India construction ₹21 lakh crore (8.4% CAGR); addressable design-services market ₹63,000–₹1,05,000 crore at 3–5% design fee. Architects/designers spend 60–70% of hours on admin, not design — that reclaimed time is the wedge.
- **Buyer:** Principal architect / interior-design studio owner (capacity multiplier), PMC firm director, real-estate developer's project head, fit-out/turnkey contractor.
- **3 tiers (₹):** Solo/Small ₹12,000/mo (1–5 architects — site reports, mood boards, proposals, basic timeline, ≤5 projects). Mid-Size ₹45,000/mo (5–30 staff — all 10 UCs, municipal approval RPA for 3 jurisdictions, contractor coordination, procurement, NBC + 1 state bye-law compliance, ≤30 projects). Enterprise ₹1.8 lakh/mo (developer/PMC/large firm — unlimited, all 28 states + 8 UTs approval RPA, ERP/SAP-Oracle integration, custom compliance rule-sets, 10-yr retention, white-label).
- **Consumption model:** Flat SaaS + per-project overage (₹8,000/project approval tracking, ₹15,000/project compliance report, ₹12,000/project procurement) + pay-per-use for discrete artefacts (₹500/site report, ₹1,500/proposal, ₹2,500/room mood board).
- **WTP:** High on delay/rejection-avoidance UCs — one prevented compliance rejection saves ₹8–15L; approval delays cost ₹15,000–₹80,000/day. Time-reclamation UCs (site reports, mood boards, proposals) are cost-substitution with fast, tangible ROI.
- **Time-to-first-revenue:** Fast (2–4 weeks) for site reports, mood boards, proposals, 3D briefs — Bucket-1 + vision. RERA/municipal approval tracking (UC-1) and compliance checking (UC-5) RPA take 6–12 weeks per jurisdiction set.
- **Monetization note:** Site reports + proposals + mood boards (vision/doc-gen, low integration) are the studio wedge with instant ROI. Approval tracking (UC-1) and compliance checking (UC-5) are the developer/PMC enterprise expanders and the moat (RERA/municipal RPA); contractor coordination (UC-8) is the retention hook.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Drawing Approval Tracking (RERA, Municipal) | Agent | 2 | rera_portal (RPA-new), municipal portals BMC/BBMP/DDA/HMDA (RPA-new), fire-NOC portals (RPA-new), whatsapp, email, HITL, pdf_generator, doc-version-controller (SHA-256), scheduler | Real-time-monitoring | Yes (non-standard query/delay) |
| UC-2 | Material & Furniture Procurement | Both | 1+2 | document_reader (BOQ OCR), IndiaMart (New-API/RPA-new), email, whatsapp, HITL, ERP/accounting (zoho_books New-API), pdf_generator, scheduler | Approval-gated + Research+doc-gen | Yes (vendor selection >₹5L) |
| UC-3 | Project Timeline & Milestone Tracking | Both | 1 | whatsapp, email, HITL, pdf_generator, CPM/NLP tool, scheduler, accounting, document_reader | Real-time-monitoring | Yes (client comms draft) |
| UC-4 | 3D Visualisation Brief Generation | Both | 1 | document_reader (drawing OCR), vision AI, speech-to-text (New — transcriber), web_search (Houzz/AD), email, pdf_generator, Houzz (New-API) | Research+doc-gen | No |
| UC-5 | Building Code Compliance (NBC, BIS) | Both | 2 | document_reader (spatial parser), web_search, NBC-code-DB, local bye-law DB (RPA-new: BBMP/BMC/GHMC), HITL, pdf_generator, spatial-analysis | Research+doc-gen + Approval-gated | Yes (compliance review) |
| UC-6 | Site Visit Report from Photos & Notes | Both | 1 | vision AI, speech-to-text (New — transcriber), whatsapp, email, HITL, pdf_generator, scheduler | Research+doc-gen | Yes (10-min report review) |
| UC-7 | Client Proposal & Quotation Generation | Both | 1 | document-gen, pdf_generator, email, HITL, CRM, vision AI (portfolio match), web_search, doc-search | Research+doc-gen | Yes (principal 20-30min) |
| UC-8 | Contractor Coordination & Progress Billing | Agent | 1 | whatsapp, vision AI (work validation), HITL, pdf_generator (FIDIC certs), accounting (zoho_books New-API), scheduler, email | Real-time-monitoring + Approval-gated | Yes (progress cert joint approval) |
| UC-9 | Mood Board Research & Assembly | Both | 1+2 | Pinterest (New-API/RPA-new), Houzz (New-API), web_search, vision AI (clustering), reverse-image-search (New-API), manufacturer-catalogue (New-API), pdf_generator, HITL, canvas-layout | Research+doc-gen | Yes (curation edits) |
| UC-10 | Post-Project Punch List & Warranty Mgmt | Agent | 1 | vision AI (defect detect), speech-to-text, whatsapp, email, HITL, pdf_generator, scheduler, CRM | Real-time-monitoring + High-volume-repetitive | Yes (escalation) |

## 3. Connectors Required
- **Existing (Bucket 1):** whatsapp, email/gmail, document_reader (OCR), web_search, pdf_generator, salesforce/hubspot (CRM). Build cost ~0.
- **New-API:** IndiaMart (procurement catalogue — may need RPA if no open API), Houzz, Pinterest (image search — API restricted, likely RPA-new), reverse-image-search, manufacturer-catalogue (Pepperfry/Urban Ladder/DesignEx), zoho_books/ERP accounting, speech-to-text/transcriber (shared platform capability). Build 2–5 days each; Pinterest/Houzz image access is API-restricted so budget RPA fallback.
- **RPA-portal (New — the moat):** rera_portal (per-state: MahaRERA, RERA Karnataka/Delhi/TN — daily login + status scrape), municipal corporation portals (BMC/BBMP/DDA/HMDA — IOD/CC/OC status), fire-NOC portals (state fire depts), airport clearance, local building bye-law databases (BBMP/BMC/GHMC — supersede NBC on setbacks/FSI). Build ~1 week per jurisdiction; scales to 28 states + 8 UTs at enterprise — high-maintenance, brittle, credential-vault-based. This is the primary Bucket-2 investment and the enterprise differentiator.
- **Vision/compute (New — critical, embedded):** vision AI is load-bearing across UC-4/6/7/8/9/10 (construction-progress detection, defect detection, photo labelling, mood-board clustering, work validation) — a shared platform capability, not per-UC. Spatial-analysis parser (extract dimensions/setbacks/FSI from 2D drawings), CPM engine, NBC-code database.

## 4. Knowledge Collections
Seed slugs: `nbc-2016-provisions` (4,200+ clauses), `state-building-byelaws` (per jurisdiction — setbacks/FSI/ground-coverage), `bis-standards` (SP:7, IS:456, IS:1893 seismic, IS:875 wind), `rpwd-accessibility-2016`, `rera-submission-checklists` (per state), `vendor-database` (rated, city/category, ISI/GREENGUARD/BIS certs), `proposal-templates`, `mood-board-style-library` (Japandi/Art Deco/etc.), `firm-portfolio` (completed projects by style/budget/type), `warranty-register-schemas`.
Ingestion recipe: (1) ingest NBC 2016 + state bye-laws + BIS standards as structured compliance ground truth, keyed by (project-type, occupancy-category, jurisdiction), with mandatory-vs-advisory flag; (2) refresh local bye-laws via portal RPA (they supersede NBC); (3) ingest firm portfolio with "after" photos into pgvector + vision embeddings for UC-7 portfolio matching; (4) build style-reference library for UC-4/9 mood-board grounding.

## 5. Guardrails & Compliance
- **Approval-criticality (UC-1):** RERA/municipal approval delays are 64% of residential project delays — daily portal checks, status-change alerts within 2h, query-response drafting for standard deficiencies; HITL for non-standard queries.
- **Compliance-report integrity (UC-5):** clause-by-clause matrix (Provision | Required | Proposed | Status | Code Ref); distinguish mandatory vs advisory; HITL review before design-review meeting. Structural pre-check (seismic zone, wind load) is advisory only — never substitutes for a licensed structural engineer.
- **Document version control:** SHA-256 checksums on all submitted drawing sets (UC-1) — always submit latest approved version.
- **Progress billing (UC-8):** FIDIC-format certificates require joint PM + client-rep approval (48h window); photo evidence linkage for dispute prevention; 7-10 yr (3650-day) retention for construction liability.
- **Data classification:** project_confidential; RLS tenant isolation.
- **Hallucination risk:** high in UC-5 (code compliance) — a missed or fabricated clause has ₹5-50L rectification cost; ground every check in the code DB with explicit clause citation, never assert compliance the model "believes." Vision defect/progress detection (UC-6/8/10) needs human validation gate before client-facing dispatch.

## 6. Scale Pattern & Cost Drivers
- **Real-time-monitoring (defining):** daily approval-portal polling (UC-1), timeline/CPM re-calc on site check-ins (UC-3), contractor sequence tracking (UC-8), punch-list resolution (UC-10). Scheduler + webhook driven; per-project, not mass-volume.
- **Research+doc-gen (heavy vision):** 3D briefs, site reports, proposals, compliance reports, mood boards (UC-4/5/6/7/9). Vision-model calls are the compute cost driver here.
- **Approval-gated:** procurement selection, progress billing.
- Primary cost driver: **vision AI inference** across site reports, defect detection, progress validation, and mood-board clustering — the single largest compute line, shared across 6 of 10 UCs. RERA/municipal RPA reliability is the operational cost. Per-project LLM budget is modest.

## 7. Decision & Phasing
- **Phase 1 (land):** UC-6 site reports, UC-7 proposals, UC-9 mood boards, UC-4 3D briefs — vision + doc-gen, low integration, instant studio ROI. Requires shared vision-AI + transcriber capability up front.
- **Phase 2 (expand):** UC-2 procurement, UC-3 timeline tracking, UC-8 contractor coordination, UC-10 punch/warranty — WhatsApp-coordination + accounting, high retention.
- **Phase 3 (enterprise):** UC-1 RERA/municipal approval tracking, UC-5 NBC compliance checking — heaviest RPA (per-jurisdiction, scaling to 28 states + 8 UTs) + code DB, developer/PMC enterprise ACV and the moat.
- **ENRICHMENT FLAG — this is one of the 4 THINNEST domains (10 UCs). Enrich to 12 UCs.** Proposed additions (consistent with the vertical, reuse vision + RPA + doc-gen): **UC-11 As-built documentation & drawing-set reconciliation** (vision + drawing OCR to reconcile site photos against approved drawings, auto-flag deviations, generate as-built set for OC submission — reuses UC-1 version control + UC-6 vision) and **UC-12 Energy-efficiency & green-certification support** (ECBC / IGBC / GRIHA compliance pre-check, documentation assembly, and submission tracking — extends the UC-5 code-DB + RPA-portal pattern to green-building authorities).

## 8. KPIs
- Approval-query detection 5–8 days earlier, ₹75,000–₹4L delay cost saved/project (UC-1); material cost −8–14% (UC-2).
- Project overrun rate 85% → 45%; ₹3.5L saved/₹50L project (UC-3); render revision rounds 2.8 → 1.3 (UC-4).
- Compliance rejection prevented = ₹8–15L saved (UC-5); site report prep 4h → 25min, ₹18.2L/yr for 10-site firm (UC-6).
- Proposal prep 16h → 1.5h, volume 3 → 8/month/principal (UC-7); sequencing delay days −4–8/project (UC-8).
- Mood board 8h → 45min/room (UC-9); punch-list cycle 6wk → 2.5wk, warranty recalls −55% (UC-10).
- Platform: vision-AI inference cost per project, RERA/municipal RPA success rate, HITL review latency, code-check clause-citation coverage (100% required).
