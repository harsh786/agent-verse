# ADR-D07: GST & Tax Compliance Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/07-gst-tax/use-cases.md

## 1. Market & Monetization

**This is one of the two profit engines of AgentVerse (per ADR-0001), alongside 27-accounting-ca-firm.** GST/tax is *mandatory, recurring, deadline-driven compliance* — the demand does not depend on discretionary budget cycles. Every one of India's ~15 million GST-registered taxpayers must file GSTR-1 + GSTR-3B *every month*, forever. That is a non-negotiable, government-enforced recurring workload — the ideal substrate for autonomous agents billed per-filing.

**TAM.** 15M GST registrants × ~₹5,000–15,000/month achievable ACV = a multi-thousand-crore serviceable market before counting income-tax, TDS, and annual-return work. India processes ~13B invoices/month; 1.2 crore GST notices were issued in FY24 (≈ one per taxpayer) — each a billable event.

**Buyer.** Two distinct buyers, and the channel motion matters:
- **Direct:** CFO / finance controller of a business with >200 invoices/month.
- **Channel (the real unlock):** the **Chartered Accountant firm**. There are **3.5 lakh practising CAs** in India. One CA firm = **50–500 client sub-accounts**. Selling one firm license lands dozens-to-hundreds of paying tenants at a *single* CAC. This is the lowest customer-acquisition-cost path in the entire platform and should be the primary GTM.

**Channel-resale motion.** AgentVerse sells a **white-label CA-firm license** (Tier 3). The CA firm resells GST compliance to its own client book under its own brand, sets its own markup, and AgentVerse charges per-seat/per-client wholesale. The firm keeps the client relationship and the advisory margin; AgentVerse keeps the recurring platform fee. The CA becomes the distribution, billing, and trust layer — we never have to acquire the end SMB. Every new CA firm signed multiplies tenants by 50–500×.

**Three pricing tiers (₹):**
| Tier | Price | Scope |
|------|-------|-------|
| GST Starter | ₹5,000/mo per company | GSTR-1 + GSTR-3B auto-file, ITC reconciliation, tax calendar, up to 200 invoices/mo |
| GST Professional | ₹15,000/mo per company (or ₹40,000/mo CA firm for 10 clients) | + TDS automation, notice response, e-way bills, refund tracking, unlimited invoices |
| Tax Platform (CA Firm License) | ₹1,50,000/mo | Full suite up to 50 clients, white-label branding, GSTR-9/9C, dedicated tax-knowledge updates |

**Consumption model.** Hybrid: recurring subscription floor + per-event overage — ₹500/mo per client for GSTR-1, ₹15/mismatch resolved, ₹3,000/notice response, ₹10/e-way bill, ₹500/product classified, ₹5,000/refund application, ₹10,000/GSTR-9. This aligns cost to the government's own filing cadence.

**WTP.** High and defensible: a CA spends ~200 hours/client/year on GST, mostly data entry; ITC recovery alone protects ₹5–50L/year for a mid-size firm; a single wrong HSN code carries ₹50,000–2,00,000 penalty. Customers are paying to *avoid statutory penalty and interest*, not for convenience — a fear-based, sticky spend.

**Time-to-first-revenue.** Fast for Bucket-1 UCs (ITC reconciliation, HSN classification, notice drafting) that need only accounting-connector + knowledge base — sellable in weeks. Filing/payment UCs are gated on GST-portal RPA (see §7).

**Monetization note.** The recurring monthly filing obligation makes churn structurally low: a business cannot stop filing GST without deregistering. Combined with CA-channel distribution, this is the highest-LTV, lowest-CAC vertical on the platform.

## 2. Use Cases → Product Mapping

| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|------------|---------------|-------|
| UC-1 | GSTR-1 Auto-Filing from ERP/Billing | Flagship filing agent | 2 (RPA) | tally / zoho_books, gst_portal (RPA), document_reader, email | High-volume-repetitive (fan-out across clients, 10th-of-month burst) | **Yes** — file_return gated |
| UC-2 | Input Tax Credit (ITC) Reconciliation | Recon agent + mismatch report | 2 (RPA read) | gst_portal (RPA, GSTR-2B pull), tally/zoho_books, email | High-volume-repetitive | Review of drafts; no filing |
| UC-3 | GST Notice Response Automation | Notice-response drafting agent | 2 (RPA) | gst_portal (RPA), pdf_generator, email, KB (gst-law) | Event-driven (per notice) | **Yes** — CA reviews draft before portal submit |
| UC-4 | E-Way Bill Generation & Management | Event-triggered EWB agent | 2 (RPA/portal) | gst_portal (NIC EWB, RPA), tally/ERP, email, slack | High-volume-repetitive (200–500/mo) | Low-risk auto; alerts on reject |
| UC-5 | HSN/SAC Code Classification | Classification agent + company KB | 1 | KB (hsn-sac-master), web_search (CBIC), pdf_generator | Batch classification | **Yes** — CA confirms new products |
| UC-6 | GST Annual Return (GSTR-9/9C) | Annual-return prep agent | 2 (RPA) | gst_portal (RPA), tally/zoho_books, pdf_generator | Seasonal burst (Dec) | **Yes** — auditor certifies 9C, then file |
| UC-7 | TDS/TCS Compliance Automation | TDS agent | 2 (RPA) | tally/zoho_books, income_tax_portal (RPA, 26AS/TRACES), pdf_generator | High-volume-repetitive (quarterly) | **Yes** — return filing + payment |
| UC-8 | Cross-Border Tax Compliance | TP + RCM + export agent | 2 + 1 | gst_portal (RPA), web_search (OECD comparables), pdf_generator | Low-volume-complex | **Yes** — CA judgment on TP method |
| UC-9 | GST Refund Processing (RFD-01) | Refund agent + status monitor | 2 (RPA) | gst_portal (RPA), income_tax_portal/ICEGATE (RPA), email, document_reader | Event-driven + monitoring loop | **Yes** — file RFD-01 |
| UC-10 | Tax Calendar & Compliance Tracker | Multi-client tracker + scheduler | 1 (+2 status) | triggers/scheduler, email, slack, gov portals (RPA status) | Fan-out across all clients | No (alerts) — filing steps gated |
| UC-11 | ITR Filing & Tax Optimization | ITR agent (old vs new regime) | 2 (RPA) | income_tax_portal (RPA, 26AS/AIS), pdf_generator, email | Seasonal burst (July) | **Yes** — file ITR |
| UC-12 | GST Audit Preparation | Continuous audit-readiness agent | 1 (+2 pull) | tally/zoho_books, gst_portal (RPA), document_reader | Continuous + event (audit notice) | Review only |

## 3. Connectors Required

**Existing (Bucket 1) — reuse, low cost:** `document_reader` (invoice/notice parsing), `email`, `slack`, `zoho_books`, `quickbooks`, `xero`, `google_sheets`, `postgresql`, `web_search` (CBIC circulars, advance rulings, OECD comparables), `pdf_generator`, `razorpay` (fee collection).

**RPA-portal (Bucket 2) — MUST BUILD; these have NO public API:**
- **`gst_portal` (gst.gov.in)** — the keystone connector. Capabilities: GSTR-1/3B/9/9C prepare + file, GSTR-2B/ITC download, RFD-01 refund filing, notice inbox read, tax payment. **No public API exists → browser RPA only.** Rate-limit ~1 req/3s per session. Highest build cost and highest strategic value (gates UC-1,2,3,6,9,10,12). Estimate: **6–8 engineer-weeks** for a resilient, session-managed, captcha/OTP-aware connector; ongoing maintenance as the portal UI changes.
- **`gst_portal` — NIC e-way bill sub-module (ewaybillgst.gov.in)** — EWB generate/extend/cancel. Semi-API (NIC exposes a GSP API but requires GSP licensing); model as RPA-or-GSP. Build: **2–3 weeks** (or integrate a licensed GSP).
- **`income_tax_portal` (incometax.gov.in) + TRACES** — ITR file, 26AS/AIS/TIS download, TDS return (24Q/26Q) file, Form 16A. **No public API → RPA.** Build: **4–6 weeks.** Gates UC-7, UC-11.
- **ICEGATE (customs)** — shipping-bill/BRC validation for export refunds (UC-9). RPA. Build: **2–3 weeks**, lower priority.

**Semi-API (Bucket 3):** `tally` via its XML/HTTP gateway (Tally Prime ODBC/XML) — not a cloud API but a local gateway; treat as a connector build of **~2 weeks**.

## 4. Knowledge Collections

Seed slugs and ingestion recipe:
- **`gst-law-provisions`** — CGST/SGST/IGST Acts, Rules, sections. Recipe: ingest bare Acts + Rules from CBIC, chunk by section, embed; version by amendment date.
- **`hsn-sac-master`** — full HSN/SAC schedule (8,000+ codes) with rate mapping. Recipe: ingest the GST rate notification schedules; structured table + embedding of descriptions for fuzzy classification; refresh on every rate-change notification.
- **`cbic-circulars`** — CBIC circulars, notifications, advance rulings 2017→present. Recipe: crawl cbic.gov.in via `web_search`/scrape, parse PDF, tag by date + subject; used by UC-3 (notice) and UC-5 (classification defence).
- **`company-product-classifications`** — *per-tenant* confirmed HSN classifications (built by UC-5 HITL loop). Recipe: append CA-confirmed classifications; auto-apply to future invoices.
- **`gst-notice-templates`** — precedent response letters keyed by notice type (ASMT-10, DRC-01, DRC-01C). Recipe: seed with sample responses, grow from HITL-approved drafts.
- **`indian-tax-calendar`** — statutory due dates by law × registration type × state. Recipe: structured KB, updated within 24h of any CBIC/CBDT notification (monitored by UC-11 of the CA domain).

## 5. Guardrails & Compliance

**Regulated domain — fail-closed by default.**
- **Every filing and every payment is HITL-gated.** Policy engine (`app/governance/policies.py`) denies-by-default any `gst_portal.file_return`, `gst_portal.pay_tax`, `income_tax_portal.submit_itr`, `gst_portal.file_rfd01`. The agent may *prepare and stage* but a named CA/approver must approve via the HITL gateway (`app/governance/hitl.py`) before the RPA executes the irreversible portal action. This matches the manifest's `require-approval-for-filing` / `require-approval-for-payments` policies.
- **Fail-closed on ambiguity:** HSN classification with multiple valid codes, credit notes without original invoice, exports, and any tolerance breach are routed to review, never auto-filed.
- **Audit & evidence:** append-only audit trail (`app/governance/audit.py`) records every step — data pulled, computation, draft, approver identity, timestamp, filing acknowledgment number. Store the filed JSON + portal acknowledgment + supporting invoice backup as immutable evidence (10-year retention per ITC/assessment window). This *is* the audit-readiness product (UC-12) — the trail doubles as the deliverable.
- **Tenant isolation:** GST credentials in the encrypted vault (`app/providers/vault.py`); portal sessions per-tenant; RLS (`app/db/rls.py`) enforces per-tenant row filtering so no client's GST data leaks across the CA firm's book.
- **PII/data classification:** returns are `sensitive_pii`; sanitize logs; never log GSTINs, PANs, or amounts in plaintext error paths.
- **Verifier role:** the LangGraph verifier re-computes liability independently before presenting to HITL — cross-model verification guards against a hallucinated tax figure reaching a filing.

## 6. Scale Pattern & Cost Drivers

**Dominant pattern: High-volume-repetitive fan-out.** A CA-firm tenant with 300 clients triggers 300 GSTR-1 + 300 GSTR-3B every month, compressed into the 10th–20th window. GSTR-9 season (December) and ITR season (July) create sharp bursts. At platform scale this is *millions of filings/month*.

**Fan-out architecture:**
- Celery per-plan queue routing (`app/scaling/celery_app.py`): enterprise CA firms get `goals.enterprise` queue so a large firm's month-end burst never starves a small firm — noisy-neighbour isolation.
- **Per-tenant bulkhead** (`app/reliability/`) caps concurrent portal sessions per tenant (manifest `max_concurrent_clients: 10`, `rate_limit_per_portal: 1_per_3s`) — this is *mandatory* because the GST/IT portals rate-limit and lock accounts on aggressive access. The bulkhead is both a reliability and a compliance control.
- Checkpointed agent state (Redis `AsyncRedisSaver`) so a 300-client run survives replica restarts and resumes mid-batch.

**Cost drivers at millions of filings:**
- **RPA session time**, not LLM tokens, is the dominant cost. Each portal filing is minutes of headless-browser time; parallelism is bounded by portal rate limits, so throughput is capped by *portal politeness*, not compute. Provision RPA worker pools sized to the busiest 3 days of the month.
- **LLM cost** is controlled by the semantic cache (`app/rag/`) and the new `llm_response_cache` — HSN classifications, notice-type parsing, and boilerplate drafts are highly repetitive across clients and cache well. Prompt compression (`app/agent/prompt_compressor.py`) trims the large invoice-context prompts.
- **Cost governance:** per-goal/per-tenant budgets (`app/governance/cost.py`, Redis-backed) prevent a runaway reconciliation loop from burning margin; bill overage per-event so cost tracks revenue.

## 7. Decision & Phasing

**Flagship first:** ship **UC-1 GSTR-1 auto-filing** as the anchor agent — it is the highest-frequency, highest-pain, recurring obligation and the natural land-and-expand wedge into every GST-registered tenant.

**But UC-1 is connector-gated on the GST-portal RPA.** Therefore phase:

- **Phase A (weeks 1–4) — sellable without portal RPA:** UC-2 (ITC reconciliation — reads GSTR-2B, drafts vendor emails), UC-5 (HSN classification), UC-3 (notice-response *drafting*), UC-12 (audit-readiness). These need only accounting connectors + KB and generate revenue immediately while the RPA is built.
- **Phase B (weeks 4–10) — the key unlock: build `gst_portal` RPA.** This lights up UC-1, UC-6, UC-9, UC-10 (filing steps), and closes the loop on UC-2/UC-3. **GST-portal RPA is the single highest-leverage engineering investment in the vertical.**
- **Phase C — build `income_tax_portal`/TRACES RPA:** unlocks UC-7 (TDS) and UC-11 (ITR). Sequence ITR before July.
- **Phase D — NIC EWB + ICEGATE + cross-border:** UC-4, UC-9 export refunds, UC-8. Lower frequency, build after the recurring engines are live.

**Sequencing rule:** every filing UC stays behind a HITL gate on day one; loosen to single-click bulk approval (manifest "single-click approval for clean returns") only after the eval suite (`gst-compliance-accuracy-eval`) proves accuracy on real returns.

## 8. KPIs

- **Filing accuracy:** error rate <0.1% (vs 8% manual); zero rejected filings after 3-month ramp.
- **Time reduction:** GSTR-1 20h→1.5h; GSTR-9 80h→8h; notice response 20h→3h; ITR 8h→45min.
- **ITC recovery uplift:** 15–30% improvement; ₹ ITC protected per client per year.
- **Deadline adherence:** zero missed statutory deadlines across the tenant's client book.
- **Notice turnaround:** % of notices responded within the 30-day statutory window.
- **HITL efficiency:** % of returns clean enough for single-click approval (target rising over time — the automation-maturity metric).
- **Channel metrics (the monetization north-star):** CA firms signed, avg client sub-accounts per firm, net revenue retention per firm, CAC per end-tenant (target: lowest on platform).
- **Reliability:** portal-session success rate, RPA retry rate, zero account-lockouts from rate-limit violations.
