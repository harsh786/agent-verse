# ADR-D32: Non-Profit & NGO Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/32-nonprofit-ngo/use-cases.md

Applies ADR-0001 to the NGO vertical. Unlike Government-Portal (09) and Public-Health (37), this is **B2B SaaS-first**: most work runs on Bucket-1 catalog SaaS APIs (CRM, Tally/Zoho, KoboToolbox, Razorpay, Google Drive, Mailchimp, Meta/Google Ads, LinkedIn), with only a **small set of no-API government portals** (FCRA, income-tax 12A/80G, NGO Darpan, GeM, ministry grant portals) needing RPA. Dominant workloads are **research + doc-gen** (grants, proposals, reports) and **approval-gated compliance** (FCRA, 12A/80G).

## 1. Market & Monetization

- **TAM.** 3.3 million registered NGOs (world's densest). Funding pools in scope: ₹60,000 crore CSR/philanthropy, ₹24,000 crore government implementation grants, ₹18,000 crore international/FCRA funding. 21,000+ FCRA-registered entities. Administrative burden = 25–40% of programme staff time — the addressable pain. Serviceable SaaS market: ₹500–1,000 crore/year (mid/large NGOs + CSR implementers).
- **Buyer.** **NGO management** — Programme/Executive Directors, Finance Heads, CEOs — plus adjacent: CSR consulting firms, corporate foundations, INGO India chapters (Oxfam, Save the Children), crowdfunding platforms (Milaap, Ketto, GIVEIndia). Self-serve SaaS with a channel play through CSR consultancies and NGO networks (CAF India, CSRBOX). Not government-procured (the NGO is the customer, even when it implements government schemes).
- **Pricing tiers (₹) — SaaS subscription:**
  - **Tier 1 — Grassroots ₹4,999/mo** (<50 staff, <₹1 cr budget): grant discovery + basic proposal drafting (3/mo), donor mgmt (≤500 donors), 80G receipt automation, basic beneficiary tracking (≤2,000), 1 FCRA return/year, 2 seats, WhatsApp support.
  - **Tier 2 — Growth ₹19,999/mo** (50–200 staff, ₹1–10 cr): all Tier-1 unlimited-in-tier + full CSR reporting (≤8 corporate partners), multi-stakeholder reporting (≤15 reports/qtr), volunteer mgmt (≤500), fundraising analytics, supply chain (≤5 points), 12A/80G compliance, 10 seats.
  - **Tier 3 — Scale ₹59,999/mo** (>200 staff, >₹10 cr): all Tier-2 unlimited + social-audit prep + evaluation data room, unlimited proposals (incl. bilateral), FCRA multi-entity, SROI/impact measurement, custom templates, board dashboard, API access, 50 seats, dedicated partner + quarterly review.
- **Consumption model.** Subscription-led with high-value add-ons: ₹50/successful grant application (success fee), ₹20,000–75,000/proposal prepared (tiered by grant size), ₹5,000/CSR report, ₹3,000/multi-stakeholder report, ₹15,000/FCRA annual return, ₹25,000/12A-80G package, ₹10/beneficiary/year, ₹5/volunteer-hour, ₹30,000/social-audit prep, ₹2 lakh/relief-operation surge, **2% of incremental donation uplift** (outcome fee on fundraising analytics).
- **WTP.** Moderate — NGOs are budget-constrained, but WTP spikes where the alternative is a licence-cancellation risk (FCRA: replaces ₹4–6 lakh/yr consultant + existential compliance risk) or direct funding lift (proposal/grant success fees pay for themselves on one win worth ₹25–50 lakh). Success/outcome fees align price with value and lower adoption friction.
- **Time-to-first-revenue.** Fast — **2–4 weeks.** Most of the flagship value (grant discovery, proposal writing, donor mgmt, CSR/multi-stakeholder reporting) is **Bucket-1 only** (no new connectors), self-serve PLG via marketplace + simulation trial. FCRA/12A-80G RPA is a fast-follow, not a launch blocker.
- **Monetization note.** Land on the cheap Grassroots tier + a success-fee proposal/grant win (immediate ROI proof), expand to Growth on reporting-automation pain, retain via FCRA/compliance stickiness (annual, existential). Channel through CSR consultancies to aggregate many small NGOs (tenant-of-tenants, low CAC).

## 2. Use Cases → Product Mapping

| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|:------:|------------|---------------|:-----:|
| UC-1 | Grant Discovery & Preparation | Both | 1 + RPA-new | gem_portal + DST/MoWCD/MoSJE grant portals (RPA-new), gmail, web_search, document_reader, pdf_generator, email, Slack (catalog), knowledge_base | Research+doc-gen + monitoring | Yes — submit |
| UC-2 | Donor Management & Renewal | Agent | 1 | CRM (Salesforce, catalog), gmail, whatsapp, payment gateway (Razorpay, catalog), pdf_generator, Slack | High-volume-repetitive (segmented sends) | Yes — >₹5L donor outreach |
| UC-3 | FCRA Compliance & FC-4 Filing | Both | 1 + RPA-new | fcra_portal (RPA-new), bank-statement/email, document_reader, pdf_generator, gmail, Slack | Approval-gated | Yes — Finance/CEO/Auditor |
| UC-4 | CSR Project Reporting (Schedule VII) | Both | 1 + RPA-new | KoboToolbox/ODK (catalog/new-API), Tally/Zoho (catalog), Google Drive (catalog), pdf_generator, email, corporate CSR portals (RPA-new) | Research+doc-gen | Yes — PM/Finance review |
| UC-5 | Beneficiary Data & Impact Tracking | Agent | 1 | KoboToolbox (catalog), document_reader, postgresql (registry), whatsapp, analytics (code_execution) | High-volume-repetitive | No (consent-gated ingest) |
| UC-6 | 12A/80G Registration & Compliance | Both | 2 + RPA-new | income_tax_portal (Bucket 2 — Form 10A/10G/10BD), NGO Darpan (RPA-new), email, document_reader, pdf_generator, Tally (catalog) | Approval-gated | Yes — Trustee/Secretary |
| UC-7 | Volunteer Recruitment & Management | Agent | 1 + RPA-new | LinkedIn (catalog), iVolunteer/Goodera/NSSplatform (RPA-new), email, whatsapp, pdf_generator, Slack, BGV API (new-API), LMS (new-API) | High-volume-repetitive | Yes — at-risk outreach |
| UC-8 | Fundraising Campaign Analytics | Both | 1 | payment gateway (Razorpay catalog; Ketto/Milaap new-API), Mailchimp (catalog), Meta Ads + Google Ads (catalog), analytics, pdf_generator | Real-time-monitoring (give-day) | No (spend pivots reviewed) |
| UC-9 | Supply Chain for Relief Distribution | Both | 1 | email, Tally/Zoho (catalog), KoboToolbox field (catalog), Google Maps (catalog), whatsapp, pdf_generator | Real-time-monitoring (surge) | Yes — PO approval |
| UC-10 | Proposal Writing from Program Data | Both | 1 | gmail, document_reader, knowledge_base, Tally (catalog), web_search, pdf_generator | Research+doc-gen | Yes — Director review |
| UC-11 | Multi-Stakeholder Reporting | Both | 1 + RPA-new | calendar (catalog), document_reader, email, Slack, KoboToolbox, Tally, pdf_generator, donor portals (RPA-new) | Research+doc-gen | Yes — Coordinator review |
| UC-12 | Social Audit & Evaluation Support | Both | 1 | postgresql (beneficiary registry), Google Drive, Tally, KoboToolbox, pdf_generator | Research+doc-gen | Yes — leadership review |

Compliance and reporting UCs ship as **both** (standing agent + on-demand template); donor/beneficiary/volunteer/analytics UCs ship as **standing Agents** (cron + webhook, per manifest).

## 3. Connectors Required

- **Existing catalog (Bucket 1) — zero build, drives the majority of value:** gmail, email, whatsapp, sms, document_reader, web_search, pdf_generator (document generation), google_sheets, postgresql (beneficiary registry / audit store), plus catalog SaaS-API connectors from the 227-catalog: Salesforce Nonprofit CRM (UC-2), Razorpay/payment gateway (UC-2, UC-8), Tally/Zoho Books (UC-4/6/9/10/11/12), Google Drive cloud storage (UC-4/12), KoboToolbox/ODK field collection (UC-4/5/9/11), Mailchimp/Sendinblue email marketing + Meta Ads + Google Ads (UC-8), LinkedIn (UC-7), Google Maps (UC-9), Slack + Calendar (UC-1/2/11). `analytics`/dedup runs on builtin `code_execution`.
- **RPA-portal, in scope (Bucket 2):** `income_tax_portal` (UC-6 — Form 10A/10G/10BD on the IT e-filing portal), `gem_portal` (UC-1 GeM grant/procurement monitoring — RPA-new build shared with domain 09).
- **RPA-portal, NEW builds required (RPA-new — no API):** `fcra_portal` = fcraonline.nic.in (UC-3 — the highest-stakes compliance connector), NGO Darpan = ngodarpan.gov.in (UC-6), ministry grant portals DST/MoWCD/MoSJE (UC-1), corporate CSR management portals (UC-4, per-company), donor upload portals (UC-11), volunteering platforms iVolunteer/Goodera/NSSplatform (UC-7). **Build-cost flag: LOW–MEDIUM overall** — far lighter RPA surface than 09/37; only `fcra_portal` is on the launch-critical path.
- **New-API connectors to build:** Ketto/Milaap crowdfunding APIs (UC-8), BGV background-verification API (UC-7), LMS API (UC-7).

## 4. Knowledge Collections (seed slugs + ingestion recipe)

- `ngo-org-profile` (per-tenant) — focus areas, geography, beneficiary types, team expertise, past projects, impact data, financials. Recipe: ingest tenant's own reports/documents via document_reader into pgvector; the substrate for grant matching (UC-1) and proposal drafting (UC-10).
- `grant-opportunities-index` — government (GeM/DST/MoWCD/MoSJE), CSR RFPs (CSRBOX/CAF/GivingTuesday), international (USAID/Ford/GIZ/UN). Recipe: crawl portals + RFP feeds daily; store deadline/eligibility/format metadata.
- `fcra-compliance-rules` — FC-4 format, utilisation limits (admin ≤50%, sub-grant rules), designated-account rules. Recipe: MHA FCRA rules + circulars.
- `12a-80g-requirements` — Form 10A/10AC/10G/10BD requirements + CIT(E) query patterns. Recipe: Income Tax Act provisions + notified forms.
- `sdg-iris-mapping` — SDG indicators + IRIS+ metrics for impact reporting (UC-5). Recipe: published SDG/IRIS+ frameworks.
- `csr-schedule-vii` + `donor-reporting-templates` — Section 135 Schedule VII activity heads + per-funder reporting templates (UC-4, UC-11). Recipe: Companies Act Schedule VII + funder-provided templates (per-tenant).
- `social-audit-formats` — prescribed SAIP / Gram Sabha verification formats (UC-12). Recipe: government social-audit guidelines.

Store tenant-owned documents (consent-gated) + official framework text; no copyrighted third-party research shipped (UC-10 fetches research live via web_search).

## 5. Guardrails & Compliance

- **Regime.** **FCRA (Foreign Contribution Regulation Act)** — most legally consequential (20,000+ licences cancelled last decade); **Income Tax Act 12A/80G + Form 10BD**; **Companies Act Section 135 / Schedule VII** (CSR); **DPDP 2023** (high-sensitivity beneficiary PII, consent framework).
- **Mandatory HITL gates (fail-closed, per manifest):** grant/proposal submission → Programme Director/CEO (48h SLA); FCRA FC-4 filing → CEO + Statutory Auditor (72h); flagged FCRA transaction → Finance Head (4h); large-donor (>₹5 lakh) outreach → CEO (24h); PO approval (UC-9) → Finance Head; CSR/multi-stakeholder report dispatch → PM/Coordinator; 12A/80G responses → Trustee/Secretary; social-audit SAIP → leadership (30 days pre-audit).
- **Fail-closed:** FCRA agent flags non-compliant transactions (utilisation in non-designated account, sub-grant to ineligible entity, admin >50%) and **blocks** rather than files; never auto-submits a return that fails cross-reconciliation with audited accounts.
- **Audit/evidence:** append-only audit trail with submission timestamps and portal acknowledgements; **10-year retention (3,650 days) for FCRA + 80G**; PII masking; consent-gated beneficiary data (DPDP); role-based access. All donor communications archived for FCRA/80G evidence.
- **Cost controls (per manifest):** max daily spend ₹2,000/tenant, alert at ₹1,600, LLM budget ₹300/proposal — a real guardrail given the multi-agent research/doc-gen workload.

## 6. Scale Pattern & Cost Drivers

- **Dominant shape: Research + doc-gen (Pattern 4)** — grant discovery, proposal writing, CSR/multi-stakeholder reporting, social-audit prep all run multi-agent supervisor + long-context RAG over tenant knowledge. Secondary: **Approval-gated (Pattern 2)** for FCRA/12A-80G compliance; **High-volume-repetitive (Pattern 1)** for segmented donor sends and beneficiary tracking; **Real-time-monitoring (Pattern 3)** for give-day fundraising analytics and relief-surge supply chain.
- **Expected goal volume:** low-to-moderate per tenant (an NGO files ~20–30 reports/yr, 8–20 grants/yr, tracks thousands of beneficiaries) — not a millions-of-goals fan-out vertical. Volume comes from tenant count (channel aggregation), not per-tenant throughput.
- **Cost drivers:** **LLM token cost dominates** (doc-gen is long-context: proposals, reports, impact narratives) — this is the primary COGS. Levers per ADR-0001 Decision 4: aggregate budget caps on the multi-agent supervisor (the manifest's ₹300/proposal cap), prompt caching for repeated org-profile/KB context, semantic + response caching for similar report sections, model routing (draft on cheap models, polish on premium). RPA cost is minor (few portals, low frequency). Beneficiary dedup (UC-5) runs on `code_execution`, cheap.
- **Infra:** modest — per-plan Celery queues suffice; the master-plan's aggregate multi-agent budget cap (a flagged gap) is the key prerequisite for the research/doc-gen supervisor pattern.

## 7. Decision & Phasing

- **Flagship first (Bucket-1, PLG, weeks):** **UC-10 Proposal Writing + UC-1 Grant Discovery** — pure Bucket-1 value, self-serve via marketplace + simulation trial, immediate ROI on a single grant win (success-fee model), no connector blocker. This is the wedge that funds the rest.
- **Fast-follow (retention drivers, still mostly Bucket-1):** UC-2 Donor Management, UC-4 CSR Reporting, UC-11 Multi-Stakeholder Reporting, UC-5 Beneficiary Tracking — the Growth-tier expansion bundle; sticky reporting-automation pain.
- **Connector-gated (compliance stickiness):** **UC-3 FCRA (build `fcra_portal` — the single launch-critical RPA build)** and **UC-6 12A/80G (reuse Bucket-2 `income_tax_portal` + build NGO Darpan)**. These are existential-compliance retainers that lock in annual revenue; sequence right after the PLG wedge proves the platform.
- **Later / channel-led:** UC-7 Volunteer (needs BGV/LMS APIs + volunteering-portal RPA), UC-8 Fundraising Analytics (crowdfunding APIs), UC-9 Relief Supply Chain (surge/project-based), UC-12 Social Audit (Scale-tier, government-scheme implementers).
- **Prerequisite:** ADR-0001 aggregate multi-agent budget cap (research/doc-gen supervisor) + Phase-0 P0 (cross-replica HITL for the approval gates) before production compliance filing.

## 8. KPIs

- **Adoption:** paying tenants by tier; channel (CSR-consultancy) sub-accounts; grants discovered→applied conversion; proposals generated.
- **Reliability:** grant/proposal success-rate uplift (target +35–40% vs manual); FCRA/10BD on-time filing rate (target 100%); RPA success rate on fcra_portal/IT portal (>95%); beneficiary dedup accuracy (35% duplicate reduction).
- **Cost:** token cost/proposal & /report (enforce ₹300/proposal cap); cache hit rate; % steps on cheap models; daily-spend-cap adherence.
- **Revenue:** revenue/tenant, subscription vs success-fee/consumption mix, tier-upgrade rate, FCRA-retainer renewal rate, 2%-uplift outcome-fee capture (UC-8).
- **Outcome (sales narrative):** admin-time returned to mission (20–30%); grant funding lift (₹25 lakh/yr mid-NGO); donor retention (43%→68%); report prep time (20h→2h); FCRA licence-cancellation risk eliminated.
