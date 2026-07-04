# ADR-D09: Government Portal & Public Services Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/09-government-portal/use-cases.md

Applies ADR-0001 decisions to the Government Portal vertical. This is the **flagship RPA-portal vertical**: India has 1,000+ government portals with **no public APIs**, so nearly every use case is Browser-RPA + credential injection + HITL-gated submission. RPA is the moat here, not a fallback.

## 1. Market & Monetization

- **TAM.** ₹1.76 lakh crore/year in Indian compliance costs (World Bank). Direct addressable slices: ~1.5 crore GST filers, ~15 lakh active private limited companies (MCA21), ~6.3 crore MSMEs (Udyam), ~21 lakh food businesses (FSSAI), ~7 lakh+ EPFO establishments, GeM procurement ₹4 lakh crore/year, 1,700+ schemes with ₹1.8 lakh crore unclaimed benefits. Conservative serviceable revenue: ₹2,000–4,000 crore/year.
- **Buyer.** Mixed B2B/B2SMB/B2C — **primary channel is CA firms and compliance consultants** (a firm tenant carries 10–500 client sub-accounts → lowest CAC), plus in-house CFO/legal/HR teams, real-estate developers, and prosumer citizens. Not a government-as-buyer domain (contrast public health) — the citizen/business is the customer navigating government.
- **Pricing tiers (₹).**
  - **Tier 1 — Citizen Pack ₹2,000/mo:** 5 portal navigations/mo (passport, ITR, EPFO, Udyam, FSSAI), form generation, deadline alerts. Individual / micro-business.
  - **Tier 2 — Business Compliance ₹15,000/mo:** ROC filings, GST/IT portal automation, labour-law compliance, GeM monitoring, up to 20 portal sessions/mo, compliance calendar. **CA-firm edition ₹30,000/mo for 10 client companies** (channel SKU).
  - **Tier 3 — Enterprise Compliance Suite ₹75,000+/mo:** unlimited sessions, multi-entity (group companies), 4-hour SLA on portal issues, dedicated industry KB, ERP/compliance-tool API access.
- **Consumption model.** Per-transaction on top of subscription: ₹25,000/building-permit application; ₹2,000/ROC filing; ₹500/document-set (GST/IT downloads); ₹2,000/Udyam registration; ₹15,000/GeM bid; ₹8,000/property registration; ₹500/RTI filing; ₹2,000/scheme eligibility report + ₹5,000/application. Consumption dominates revenue for high-value/low-frequency events (permits, property, GeM); subscription dominates for recurring compliance (ROC, labour, GST).
- **WTP.** High and defensible: each transaction displaces ₹10,000–8,00,000 in consultant fees or penalty exposure (₹100/day MCA21 penalty, ₹5,00,000 FSSAI closure penalty, 4× stamp-duty shortfall). Penalty avoidance is a stronger sales lever than time savings.
- **Time-to-first-revenue.** 6–8 weeks — gated on the first RPA portal connector (GST + income-tax portals are the cheapest, highest-frequency start). Bucket-2 portals (gst, income_tax, mca21, epfo, esic) exist as prioritized builds; the RPA-new portals (Udyam, GeM, FSSAI, RTI, Passport Seva, SRO) are fast-follow.
- **Monetization note.** Recurring compliance (UC-6 ROC, UC-7 labour, UC-2 GST/IT, UC-8 FSSAI renewal) is the sticky annuity; high-ticket episodic work (UC-1 permits, UC-10 property, UC-4 GeM) is margin-rich but lumpy. Lead with recurring compliance sold through CA firms, upsell episodic.

## 2. Use Cases → Product Mapping

| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|:------:|------------|---------------|:-----:|
| UC-1 | Building Permit Application Automation | Both | 2 (RPA-new) | BMC/state municipal + Fire NOC + Environment + SRO portals (RPA-new), pdf_generator, email, document_reader | Approval-gated | Yes — submissions + fees |
| UC-2 | GST & Income-Tax Certificate Downloads | Both | 2 | gst_portal, income_tax_portal, email, google_sheets | High-volume-repetitive | No (read-only downloads) |
| UC-3 | MSME Udyam Registration & Update | Both | 2 (RPA-new) | udyam (RPA-new), gst_portal (cross-verify), email | Approval-gated | Yes — Aadhaar OTP + submit |
| UC-4 | GeM Tender Monitoring & Bid Prep | Both | 2 (RPA-new) | gem_portal (RPA-new), web_search, pdf_generator, email | Real-time-monitoring + Research+doc-gen | Yes — bid submit |
| UC-5 | Passport & VISA Document Prep | Both | 2 (RPA-new) | Passport Seva / VFS Global / BLS (RPA-new), pdf_generator, email | Approval-gated | Yes — appointment + submit |
| UC-6 | MCA21 Company Compliance (ROC) | Both | 2 | mca21, pdf_generator, accounting connector (Tally/Zoho, catalog), email | High-volume-repetitive (deadline fan-out) | Yes — DSC sign + upload |
| UC-7 | Labour-Law Registrations (PF/ESIC/PT/Shops) | Both | 2 + RPA-new | epfo, esic, state PT + Shops Act portals (RPA-new), HRIS connector (catalog/new-API), email | High-volume-repetitive (monthly ECR) | Yes — filing + payment |
| UC-8 | FSSAI License Application & Renewal | Both | 2 (RPA-new) | fssai/FoSCoS (RPA-new), pdf_generator, email, payment gateway (catalog) | Approval-gated + renewal monitoring | Yes — submit + fee |
| UC-9 | EPFO Pension/PF Withdrawal Assistance | Agent | 2 + RPA-new | epfo (member portal), EPFO grievance portal (RPA-new), email | Approval-gated | Yes — claim submit |
| UC-10 | Property Registration (SRO, Stamp Duty) | Both | 2 (RPA-new) | state revenue / SRO booking / GRN payment portals (RPA-new), pdf_generator, email | Approval-gated | Yes — payment + submit |
| UC-11 | Government Scheme Eligibility Matching | Both | 2 (RPA-new) + 1 | MyScheme + scheme portals (RPA-new), digilocker, web_search, email | Research+doc-gen | Yes — application filing |
| UC-12 | RTI Filing & Tracking | Both | 2 (RPA-new) | rti_portal (RPA-new), state RTI portals (RPA-new), pdf_generator, email | Approval-gated | Yes — file + fee |

Every UC ships as **both** a standing Agent (monitoring/scheduling) and a Goal Template (one-shot task), except UC-9 which is transactional-only.

## 3. Connectors Required

- **Existing catalog (Bucket 1) — zero build:** email, pdf_generator (document generation), document_reader, web_search, google_sheets, whatsapp, sms, digilocker (DigiLocker for document pulls in UC-5/11), plus catalog SaaS with APIs (Tally/Zoho accounting for UC-6, payment gateway for UC-8/10 fees, HRIS for UC-7).
- **RPA-portal, prioritized build (Bucket 2, already scoped):** `gst_portal`, `income_tax_portal`, `mca21`, `epfo`, `esic`. These unlock UC-2, UC-6, UC-7, UC-9 — the highest-frequency recurring work. ~2–3 weeks each on the `app/rpa/` Playwright stack.
- **RPA-portal, NEW builds required (RPA-new — no connector today, no public API):** `udyam` (UC-3), `gem_portal` (UC-4), `fssai`/FoSCoS (UC-8), `rti_portal` (UC-12), plus not-yet-listed portals that must be built: municipal/BMC + Fire NOC + Environment (UC-1), Passport Seva / VFS / BLS (UC-5), state PT & Shops-Act portals (UC-7), EPFO grievance portal (UC-9), state revenue / SRO / GRN stamp-duty portals (UC-10), MyScheme + scheme-specific portals (UC-11). **Build-cost flag: HIGH** — this domain has the largest new-RPA surface of any vertical; sequence by revenue, not by UC number.
- **No new API connectors** — there are no public APIs for these portals; that absence is the moat.

## 4. Knowledge Collections (seed slugs + ingestion recipe)

- `government-portal-navigation-guides` — per-portal login flow, field maps, upload steps, DSC/OTP handling. Recipe: crawl each portal's official help/FAQ + record deterministic RPA scripts as data (scripts are content, versioned).
- `compliance-calendar-rules` — statutory due dates (ROC forms AOC-4/MGT-7/DIR-3-KYC, ECR monthly, FSSAI renewal windows, GST cadence). Recipe: ingest CBIC/MCA/EPFO/FSSAI circulars; encode as rule tables.
- `form-filling-instructions` — field-level guidance per form (Form 26AS, Udyam NIC codes, DS-160, Form 19/10C/31, FC-format). Recipe: official form guides + notified formats.
- `government-schemes-database` — ingested from MyScheme.gov.in eligibility criteria (UC-11). Recipe: crawl scheme metadata (eligibility attributes, benefit value, portal URL); refresh on budget/policy notifications.
- `development-control-regulations` — zone-wise DCR for permits (UC-1). Recipe: municipal DCR PDFs by city/zone.
- `stamp-duty-circle-rates` — state circle-rate tables (UC-10). Recipe: state revenue-dept ready-reckoners, refreshed annually.

No copyrighted third-party text shipped; store official government notifications and derived rule tables only.

## 5. Guardrails & Compliance

- **Regime.** Not a single statute but the aggregate of every portal's ToS + acting-as-the-real-user law. Credentials are the tenant's own, vault-encrypted, injected at runtime — the agent acts *as* the authorized user (per ADR-0001 mitigation). India DPDP 2023 applies to citizen PII (Aadhaar, PAN, passport). IT Act 2000.
- **Mandatory HITL gates (fail-closed):** every `rpa.submit_form` and every `rpa.make_payment` requires human approval before execution (per the domain manifest policy block). Aadhaar/DSC/OTP steps are always human-in-the-loop (`rpa_request_human_help`). No filing or fee is ever auto-submitted.
- **Fail-closed:** on CAPTCHA/2FA/ambiguous DOM → `rpa_detect_captcha` + halt + request human help; never guess-submit. On stamp-duty/tax computation uncertainty → surface calculation for approval (4× penalty risk).
- **Deny policies:** `*.log_credentials` denied; credentials never written to logs, screenshots redacted for PII, audit trail is append-only (acknowledgment/SRN numbers captured as evidence).
- **Audit/evidence:** every download, submission, and payment logged with portal acknowledgment (SRN, ARN, receipt) for the tenant's own compliance evidence.

## 6. Scale Pattern & Cost Drivers

- **Dominant shape: Approval-gated (Pattern 2)** overlaid on **High-volume-repetitive (Pattern 1)** for recurring filings (ROC deadline fan-out, monthly ECR, GST cadence) and **Real-time-monitoring (Pattern 3)** for GeM tender scraping and status polling.
- **Expected goal volume:** medium per-tenant but multiplied by CA-firm sub-accounts; spikes at statutory deadlines (audit season Nov–Dec, monthly ECR, quarterly Form 26AS).
- **Cost drivers:** **RPA session cost dominates, not tokens** — 1 Chromium/session, portals are slow and multi-step. Levers: the RPA headless-browser farm as its own autoscaled Celery queue, per-tenant login/session caching, prefer bulk-upload endpoints where a portal offers one, batch downloads (UC-2). Token cost is secondary (form-field extraction, deficiency-notice parsing); route these to cheap/local models. Cache portal navigation plans (deterministic scripts) so LLM is used for data mapping, not click-by-click.
- **HITL infra:** cross-replica approval queue + notification inbox + SLA escalation (Tier-3 4-hour SLA) are prerequisites.

## 7. Decision & Phasing

- **Flagship first:** **UC-2 (GST + Income-Tax certificate downloads)** — read-only (no HITL submit risk), highest frequency, cheapest RPA build (`gst_portal` + `income_tax_portal`), immediate audit-season demand. Ships in weeks and proves the RPA substrate.
- **Fast-follow (recurring annuity, Bucket-2 connectors):** UC-6 (MCA21 ROC) + UC-7 (labour law EPFO/ESIC) — sold through CA firms as the ₹30,000/mo channel SKU. These reuse `mca21`, `epfo`, `esic` builds.
- **Connector-gated (RPA-new, sequence by revenue):** UC-3 Udyam → UC-8 FSSAI → UC-4 GeM (highest ticket) → UC-11 schemes → UC-12 RTI → UC-1 permits / UC-10 property / UC-5 passport (heaviest multi-portal RPA, last).
- **Prerequisite:** ADR-0001 Phase-0 P0 fixes (cross-replica HITL C6, RPA worker pool) before any production filing.

## 8. KPIs

- **Adoption:** paying tenants; CA-firm sub-accounts activated; portal-connectors live; sessions/tenant/mo.
- **Reliability:** RPA success rate per portal (target >95%, DOM-change alerting); eval-suite pass % for form-fill accuracy; deficiency/rejection rate on submitted filings (<5%).
- **Cost:** RPA-session cost/goal; token cost/goal; % steps on cheap/local models; session-cache hit rate.
- **Revenue:** revenue/tenant, consumption vs subscription mix, penalty/fee avoided per tenant, gross margin per RPA transaction.
- **Outcome:** compliance-deadline miss rate (target 0), turnaround vs manual baseline (e.g. permit 180→45 days, ROC 15–20h→3h).
