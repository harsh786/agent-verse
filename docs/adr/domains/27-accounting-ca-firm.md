# ADR-D27: Accounting & CA Firm Operations Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/27-accounting-ca-firm/use-cases.md

## 1. Market & Monetization

**This is the second profit engine of AgentVerse (per ADR-0001), paired with 07-gst-tax.** Where the GST vertical sells *the compliance work*, this vertical sells *to the firm that owns the compliance relationship* — the Chartered Accountant practice. The two are commercially inseparable: the CA firm is simultaneously the buyer of practice-operations tooling (this ADR) and the distribution channel for GST/tax/ITR/MCA execution (ADR-D07). **This is the lowest-CAC, highest-LTV go-to-market on the entire platform.**

**TAM.** India has **3.5 lakh practising CAs** managing a **₹50,000 crore/year** services market growing ~12% annually. The structural pain is quantified: 200+ regulatory amendments/year; a 100-client firm carries 300+ annual deadlines; 1-in-10 deadlines missed → ₹50,000–5,00,000 client penalties → professional-liability exposure. 70% of firm capacity is consumed by execution *before any advisory is billed*.

**Buyer.** The **CA firm partner** (sole practitioner → 20+ partner firm) and accounting/BPO outsourcing shops. The purchase is a *capacity* and *risk* decision, not a software preference: the firm buys back partner hours (billable at ₹5,000–15,000/hr on advisory) and buys down penalty/malpractice risk.

**The channel-resale motion (why this is the profit engine):** one CA-firm license = **50–500 client sub-accounts**. AgentVerse acquires *one* firm and instantly gains 50–500 downstream tenants that the firm services, brands, and bills — we never pay CAC on the end SMB. The Enterprise tier is explicitly **white-labeled** ("white-labeled client portal", "custom reporting for ICAI peer review") so the firm resells GST returns, ITR filing, MCA21 filing, and financial analysis to its book under its own brand at its own markup. AgentVerse charges wholesale per-client/per-seat; the firm keeps the client trust and advisory margin. **Signing one 500-client firm is economically equivalent to acquiring 500 direct SMB tenants at ~1/500th the sales cost.** This compounding is the core reason the vertical is designated a profit engine.

**Three pricing tiers (₹):**
| Tier | Price | Scope |
|------|-------|-------|
| Associate | ₹8,000/mo | Sole practitioners, ≤50 clients: 300 deadline alerts/mo, 10 agent-runs/day, deadline mgmt + GST prep (25 clients) + ITR (25/season). Onboarding ₹10,000 one-time. |
| Professional | ₹35,000/mo | 3–15 partners, 100–500 clients: unlimited clients, 200 agent-runs/day, 5 concurrent audits, + statutory-audit support, MCA21, financial analysis, billing, regulatory monitoring. Onboarding ₹50,000. |
| Enterprise | ₹1,50,000/mo | 20+ partners / Big-4 support / BPO: dedicated agent cluster, SLA <2h, + M&A DD, transfer pricing, white-label portal, ICAI peer-review reporting, dedicated CA success manager. Onboarding ₹2,00,000. |

**Consumption model.** Subscription floor + per-event overage: ₹200–500/return above base, ₹25,000–75,000/statutory-audit engagement, ₹2,000/onboarding, ₹500/client/mo deadline module, ₹800/GST return, ₹500/MCA21 form, ₹40,000–1,50,000/internal-audit, ₹75,000–2,00,000/TP or M&A-DD engagement. High-value episodic engagements (audit, TP, DD) layer premium revenue on the recurring compliance floor.

**WTP.** Very high — reported outcomes: 40% cut in peak-season overtime, 65% better on-time filing, 25% advisory-revenue growth in 12 months. The firm is paying to convert junior execution cost into partner advisory revenue billed at ₹5,000–15,000/hr; the arithmetic (e.g. UC-4: ₹6,000/mo module vs ₹2L/yr penalties averted = 33× ROI) is overwhelmingly favourable.

**Time-to-first-revenue.** Fast: deadline management (UC-4), regulatory monitoring (UC-11), financial analysis (UC-9), onboarding (UC-3), billing (UC-10) are Bucket-1 and sellable immediately. Filing/audit UCs are gated on the same portal RPAs as ADR-D07 — build once, share across both verticals.

**Monetization note.** Sticky by construction: the compliance calendar, client master, and 10-year retained working papers create high switching cost; the firm's *entire practice operations* run on the platform. Net revenue retention should be the highest on the platform because clients-per-firm grows organically.

## 2. Use Cases → Product Mapping

| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|------------|---------------|-------|
| UC-1 | Bulk ITR Filing Across 50+ Clients | Flagship parallel-filing orchestrator | 2 (RPA) | income_tax_portal (RPA: 26AS/AIS/TIS/submit), document_reader (Form 16), tally, email, whatsapp, HITL | High-volume-repetitive fan-out (July burst) | **Yes** — CA approves each ITR before submit |
| UC-2 | Statutory Audit Evidence & Workpapers | Audit evidence-collection agent | 2 (RPA) + 1 | tally/SAP (ERP), gst_portal + income_tax_portal/TRACES (RPA), banking RPA, pdf_generator, code-exec, SharePoint | Seasonal burst (Oct–Dec), per-engagement | Auditor reviews; no filing |
| UC-3 | Client Onboarding & Engagement Letters | Onboarding orchestrator | 1 (+2 rep-add) | pdf_generator, docusign, email, whatsapp, income_tax_portal/gst_portal/mca21 (RPA rep-add), document_reader, scheduler | Event-driven (per new client) | Some (rep-add authorization) |
| UC-4 | Tax Deadline Management (100+ clients) | Multi-client compliance calendar | 1 | scheduler/triggers, web_search (CBDT/CBIC/MCA), email, whatsapp, slack, KB, tally/zoho/quickbooks | Fan-out across all clients | No (alerts + escalation) |
| UC-5 | GST Return Prep for Multiple Clients | Multi-client GST agent (shares D07 engine) | 2 (RPA) | tally/zoho_books/quickbooks, gst_portal (RPA), pdf_generator, email, HITL, code-exec | High-volume-repetitive fan-out | **Yes** — CA approves before file |
| UC-6 | Company Law Compliance (MCA21) | MCA21 filing agent | 2 (RPA) | mca21 (RPA), pdf_generator, docusign, scheduler, tally, SharePoint, secretarial-sw | High-volume-repetitive (300 forms/30 cos) | **Yes** — DSC apply + submit gated |
| UC-7 | Internal Audit Checklist Execution | Internal-audit exception engine | 1 | tally/SAP/Oracle (ERP), code-exec (pandas sampling), pdf_generator, email, KB (DOA matrix) | Batch analytics over full population | Auditor investigates flags |
| UC-8 | Transfer Pricing Documentation | TP benchmarking + Local/Master File agent | 1 (+web) | web_search (CMIE/Capitaline/MCA21), tally, code-exec (scipy IQR), pdf_generator, KB (TP rules) | Low-volume-complex | **Yes** — CA judgment on method/comparables |
| UC-9 | Financial Statement Analysis & Commentary | Advisory analysis agent | 1 | tally/SAP, web_search (RBI/SIDBI/NABARD), code-exec (matplotlib), pdf_generator, email, KB | Batch per client per quarter | Partner reviews commentary |
| UC-10 | Practice Billing, WIP & Fee Collection | Billing + collections agent | 1 | billing connector, email, whatsapp, razorpay, pdf_generator, tally, calendar | Continuous (daily time capture) | Partner reviews escalation emails |
| UC-11 | Regulatory Change Monitoring & Alerts | Regulatory-intelligence agent | 1 (+2 scrape) | web_search + RPA scrape (CBDT/CBIC/MCA/RBI/SEBI/IBBI), pdf_generator, email, whatsapp, KB, mailchimp, SharePoint | Continuous 3×/day poll + fan-out alerts | No (curated alerts) |
| UC-12 | M&A Due Diligence Support | DD data-assembly + report agent | 2 (RPA) + 1 | document_reader, mca21/gst_portal/income_tax_portal(TRACES)/epfo/esic (RPA), web_search (NCLT/ITAT/HC), code-exec, pdf_generator | Low-volume-complex, per-engagement | Partner adds judgment layer |

## 3. Connectors Required

**Existing (Bucket 1) — reuse:** `document_reader`, `email`, `slack`, `whatsapp`, `docusign`, `razorpay`, `zoho_books`, `quickbooks`, `xero`, `google_sheets`, `postgresql`, `web_search`, `pdf_generator`. Code-execution (pandas/scipy/matplotlib) for reconciliation, statistical sampling, and charts (UC-7/8/9). SharePoint/Google Drive for working-paper storage.

**RPA-portal (Bucket 2) — MUST BUILD (no public APIs). Shared with ADR-D07 where noted:**
- **`income_tax_portal` + TRACES** — shared with D07. ITR submit, 26AS/AIS/TIS download, TDS-return verify. Gates UC-1, UC-2, UC-12. Build ~4–6 wks (shared cost).
- **`gst_portal`** — shared with D07. Gates UC-5, UC-2, UC-12. Build ~6–8 wks (shared cost).
- **`mca21` (mca.gov.in)** — *net-new, this vertical's signature connector.* AOC-4 (XBRL), MGT-7, ADT-1, DIR-3 KYC, DPT-3, etc.; SRN capture; DSC application. No public API → RPA + DSC-signing integration. Gates UC-6, UC-3 (rep-add), UC-12. Build: **5–7 weeks** (XBRL taxonomy formatting + DSC token handling are the hard parts).
- **`epfo` / `esic`** — employer-contribution compliance checks for M&A DD (UC-12). RPA. Build ~2–3 wks each, lower priority.
- **Corporate internet-banking RPA** (HDFC/ICICI/SBI business) — bank-statement pull + reconciliation for statutory audit (UC-2) and DD. RPA or bank API where available. Build ~3–4 wks.

**Semi-API (Bucket 3):** `tally` XML/HTTP gateway (shared); ERP read-only for SAP/Oracle (audit/analysis). Third-party data: CAMS/KFintech (capital-gains statements, UC-1) and CMIE Prowess/Capitaline (TP comparables, UC-8) — licensed API/data feeds, ~1–2 wks integration each. Secretarial software (CAMS/CSDocs) for MGT-7 data.

## 4. Knowledge Collections

Seed slugs + ingestion recipe:
- **`indian-tax-calendar`** (shared with D07) — every due date × law × entity type × state. Recipe: structured KB refreshed within 24h of any CBDT/CBIC/MCA notification (fed by UC-11).
- **`indian-accounting-standards`** — Ind AS / AS, Companies Act Schedule II (depreciation), Schedule III (financial-statement format). Recipe: ingest ICAI standards + Companies Act schedules; used by UC-2, UC-9.
- **`standards-on-auditing`** — SA 200–SA 720. Recipe: ingest ICAI SAs; map each to a working-paper requirement so UC-2 auto-indexes evidence per SA.
- **`icai-templates`** — engagement letters, MRL (SA 580), board-resolution templates, Form 60/61. Recipe: seed ICAI-format templates for UC-3, UC-6 document generation.
- **`transfer-pricing-regulations`** — Rule 10D, OECD TP Guidelines, method definitions (CUP/TNMM/RPM/CPM/PSM), Form 3CEB. Recipe: ingest IT Rules + OECD guidance for UC-8.
- **`mca21-form-taxonomy`** — form-wise field maps + XBRL taxonomy for AOC-4/MGT-7 etc. Recipe: structured KB from MCA schema; drives UC-6 auto-population.
- **`sector-benchmarks`** — RBI/SIDBI/NABARD industry median ratios, loan-covenant standards, DOA-matrix templates. Recipe: ingest published sectoral reports for UC-9 benchmarking and UC-7 internal-audit thresholds.
- **`regulatory-notifications-archive`** — *per-firm* archive of every CBDT/CBIC/MCA/RBI notification with the firm's interpretation notes (built by UC-11). Recipe: append classified notifications; source of truth for the firm's advisory memory.

## 5. Guardrails & Compliance

**Regulated, professional-liability-bearing domain — fail-closed.**
- **Every statutory filing and every payment is HITL-gated:** `income_tax_portal.submit_itr`, `gst_portal.file_return`, `mca21.submit_form` + DSC application are deny-by-default in the policy engine; a named CA/partner must approve via the HITL gateway before RPA executes. The flagship manifest encodes `require_approval_for: submit_itr, condition: "always"` with a 4-hour escalation to the managing partner — replicate this for MCA21 and GST filing.
- **Fail-closed on discrepancy:** TDS-vs-26AS mismatch >₹100, defective-return flags, three-way-match divergences, and any figure outside tolerance are routed to a manual review queue, never auto-filed (manifest `on_tds_mismatch: escalate_to_review_queue`).
- **DSC handling:** Digital Signature Certificates (MCA21/IT) are the legal identity of a director/CA — DSC application is a discrete gated action, credentials in the encrypted vault, never exposed to the LLM context.
- **Audit & evidence:** append-only audit trail on every action; **10-year retention** (manifest `retention_days: 3650`, per ICAI policy); data classified `sensitive_pii`. The working-paper file and SRN/acknowledgment register *are* the evidentiary product for UC-2/UC-6 and are court/peer-review admissible.
- **ICAI peer-review readiness:** Enterprise tier produces peer-review reporting — the audit trail must reconstruct who approved what, when, with what evidence, for any engagement.
- **Tenant isolation is doubly critical here:** one CA-firm tenant holds 50–500 *end-clients'* data. RLS (`app/db/rls.py`) enforces per-client sub-scoping so one client's data never bleeds into another's filing; portal credentials vaulted per end-client.
- **Independence & conflict controls:** UC-3 conflict-check and UC-7 DOA-matrix enforcement are compliance features, not conveniences — surface conflicts to the partner (HITL) rather than proceeding.

## 6. Scale Pattern & Cost Drivers

**Dominant pattern: High-volume-repetitive fan-out across each firm's client book, with sharp seasonal peaks** (July ITR, Oct–Dec statutory audit + GSTR-9, quarterly TDS/MCA). A single Professional-tier firm can trigger hundreds of concurrent filings; at platform scale (thousands of firms × hundreds of clients) this is *millions of filings and audit runs*.

**Fan-out architecture:**
- Celery per-plan queues (`app/scaling/celery_app.py`): Enterprise firms on `goals.enterprise` with a *dedicated agent cluster* and SLA <2h; Associate firms on `goals.starter` — noisy-neighbour isolation so a Big-4 feeder's audit season doesn't starve a sole practitioner.
- **Per-tenant bulkhead** (`app/reliability/`): manifest `max_concurrent_clients: 10`, `rate_limit_per_portal: 1_per_3s` — hard cap on concurrent portal sessions per firm. Mandatory: the IT/GST/MCA portals rate-limit and lock accounts; the bulkhead prevents a 500-client bulk run from tripping lockouts. Agent-run/day caps per tier (10/200/unlimited) are the commercial expression of the same limit.
- Checkpointed state (Redis `AsyncRedisSaver`) so a 500-ITR batch resumes mid-run after any restart; `replan_on_failure: true`, `max_iterations: 5`.

**Cost drivers at millions of engagements:**
- **RPA browser-session time dominates** (as in D07) — bounded by portal politeness, not compute; size RPA worker pools to the July/October peaks, scale to near-zero off-season.
- **Code-execution** (pandas/scipy over full transaction populations in UC-2/7/12) is CPU-bound and can be large for a ₹200-cr-company internal audit — bill per-engagement to cover it.
- **LLM cost** controlled by semantic + LLM-response caching: financial-analysis commentary, regulatory-alert classification, and template drafting are highly repetitive and cache well; prompt compression trims large ledger/financial-statement contexts. Per-tenant/per-goal budgets (`app/governance/cost.py`) cap runaway analytical loops.
- **Data-feed cost:** CAMS/KFintech and CMIE/Capitaline are paid per-query — meter and pass through on TP/DD engagements.

## 7. Decision & Phasing

**Flagship first: UC-1 Bulk ITR Filing Orchestrator** — it is the sharpest pain (75–150 hrs compressed into 2 July weeks, ₹5,000/day 234F penalty), maps directly to the firm's biggest capacity crunch, and is the natural wedge into the whole practice. It is the reference manifest in the source.

**Phasing (reuse D07's portal RPA investment — build once, serve both verticals):**
- **Phase A (weeks 1–4) — no-portal, sellable now:** UC-4 (deadline management — the highest-ROI, lowest-build module and the *daily habit* that anchors retention), UC-11 (regulatory monitoring), UC-9 (financial analysis), UC-3 (onboarding), UC-10 (billing). Land the firm on these while portals are built.
- **Phase B (weeks 4–10) — shared `income_tax_portal`/TRACES + `gst_portal` RPA (co-funded with D07):** unlocks UC-1 (flagship ITR), UC-5 (GST prep), and the filing halves of UC-2/UC-12.
- **Phase C (weeks 8–15) — build `mca21` (this vertical's key net-new unlock):** unlocks UC-6 and the company-law slice of UC-3/UC-12. XBRL + DSC are the gating complexity.
- **Phase D — code-exec-heavy premium engagements:** UC-2 (statutory audit), UC-7 (internal audit), UC-8 (transfer pricing), UC-12 (M&A DD) + `epfo`/`esic`/banking RPA. These are lower-frequency, high-margin; sequence after recurring engines are live.

**Sequencing rule:** filing/DSC actions stay hard-gated until the eval suite proves accuracy on real engagements; only then enable manifest "single-click approval for clean returns" for bulk throughput. **The MCA21 connector is the differentiating unlock that no generic tool has** — prioritize it once ITR/GST are live.

## 8. KPIs

- **Peak-season throughput:** ITRs filed per 24h (target: 100 in 24h vs 2 weeks manual); statutory-audit evidence collection 120h→12h; MCA21 300 forms in 30h vs 300h.
- **On-time filing rate:** 65% improvement; zero missed deadlines across the firm's book.
- **Penalty avoidance:** ₹ 234F/234E/MCA-LD penalties averted per firm per year.
- **Advisory-revenue uplift:** +25% within 12 months; partner hours reallocated from execution to advisory (billed at ₹5,000–15,000/hr).
- **Staff-overtime reduction:** 40% cut in peak-season overtime.
- **Practice economics (UC-10):** realisation rate, utilisation rate, unbilled-WIP % (target 20%→5%), collection period (90→45 days).
- **Channel monetization north-star:** firms signed; avg client sub-accounts per firm; net revenue retention per firm; CAC per end-tenant (target: lowest on platform, driven by the 50–500× multiplier).
- **Reliability:** portal-session success rate, zero rate-limit lockouts, HITL approval turnaround, % clean returns eligible for single-click approval.
