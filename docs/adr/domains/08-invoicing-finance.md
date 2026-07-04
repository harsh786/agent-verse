# ADR-D08: Invoicing & AP/AR Finance Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/08-invoicing-finance/use-cases.md

## 1. Market & Monetization

**TAM.** AP/AR is the financial heartbeat of every business — a horizontal, high-frequency workload. The pain is quantified in hard cash: Indian MSME DSO of 60–90 days; duplicate payments at 0.5–1.5% of AP spend; manual invoice processing at ₹500–2,000/invoice; missed early-payment discounts worth ₹3–8 crore/year per ₹100 crore of purchases; AP fraud at up to 5% of revenue (ACFE). Any business with vendors and customers is a prospect — the serviceable market is effectively every mid-market+ company in India.

**Buyer.** The **CFO / finance controller / AP-AR manager**. The purchase is a working-capital and leakage decision: freed cash (DSO reduction), prevented loss (duplicates, fraud), and captured yield (early-payment discounts). ROI is directly measurable in ₹ on the customer's own ledger — the easiest business case of the four domains.

**Three pricing tiers (₹):**
| Tier | Price | Scope |
|------|-------|-------|
| Finance Starter | ₹10,000/mo | Invoice generation, 3-way matching, basic AR reminders; ≤200 invoices/mo; Tally/Zoho Books |
| Finance Professional | ₹35,000/mo | + cash-flow forecasting, vendor reconciliation, expense processing; unlimited invoices; multi-bank + multi-currency; real-time dashboards |
| Finance Enterprise | ₹1,00,000+/mo | + fraud detection, month-end-close automation; multi-entity; SAP/Oracle ERP; custom approval workflows, CFO analytics; SOC2-ready audit trail |

**Consumption model.** Subscription + per-transaction overage + performance-based: ₹50/invoice generated, ₹1,000/mo per 100 invoices matched, ₹2,000/mo AR module, ₹500/mo per 100 invoices duplicate-scanned, ₹500/vendor-statement reconciled, ₹200/expense-report, ₹100/credit-note. Two performance models with strong alignment: **15% of duplicates prevented × invoice value** (UC-4) and **5% of early-payment discount captured** (UC-6) — AgentVerse gets paid a slice of money it demonstrably saves.

**WTP.** High and self-funding: the product pays for itself out of recovered cash. E.g. capturing 40% more early-payment discounts = ₹1.2–3.2 crore/year per ₹100 crore AP; preventing 10% of AP fraud = ₹25L/year for a ₹50-crore-revenue firm; ₹50L freed per ₹1 crore of DSO reduction. Customers fund the subscription from the savings.

**Time-to-first-revenue.** Fast: the entire vertical is essentially **Bucket-1** — accounting connectors (Tally/Zoho/QuickBooks/Xero), document_reader, email, razorpay, plus banking APIs. **No government-portal RPA required** (the one GST touchpoint, e-invoice IRN / GSTR-1 credit-note reporting, reuses the D07 `gst_portal` connector). Sellable in weeks.

**Monetization note.** Not a mandatory-compliance profit engine like GST/CA — AP/AR is operational, not statutory — but it is the **broadest-applicability, easiest-ROI-proof** vertical, ideal for volume land-and-expand and a natural cross-sell to every GST/CA and finance customer. It composes tightly with D07 (shared accounting connectors, e-invoicing, credit-note→GSTR-1 flow).

## 2. Use Cases → Product Mapping

| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|------------|---------------|-------|
| UC-1 | Auto Invoice Generation from POs | Event-triggered invoicing agent | 1 (+e-invoice) | tally/zoho_books/SAP, pdf_generator, email, razorpay, e-invoice IRN (gst_portal), CRM, slack | High-volume-repetitive (>100/mo) | No (low-risk generation) |
| UC-2 | 3-Way Matching & Invoice Validation | AP-matching agent (auto-approve clean) | 1 | document_reader, tally/SAP/zoho_books, email | High-volume-repetitive (200/mo) | Exceptions → AP manager |
| UC-3 | Overdue Invoice Collection | AR collections agent (escalating cadence) | 1 | zoho_books/quickbooks/tally, email, razorpay, slack | Continuous daily scan + fan-out | **Yes** — CEO/exec escalation + write-off gated |
| UC-4 | Duplicate Invoice Detection | Fuzzy-match duplicate guard | 1 | accounting connector, email, dedup engine | Continuous, per-invoice pre-check | High-prob hold; medium → AP manager |
| UC-5 | Vendor Statement Reconciliation | Recon agent + resolution letters | 1 | document_reader (statement parse), accounting connector, email | Batch per vendor per month | Aged disputes → finance manager |
| UC-6 | Early Payment Discount Optimization | Discount-yield optimizer | 1 (+bank) | accounting connector, banking API (ICICI/HDFC/RazorpayX), slack | Continuous daily scan | **Yes** — payments >₹10L gated |
| UC-7 | Expense Report Processing & Policy | Expense-policy agent (OCR + rules) | 1 | document_reader (OCR), accounting connector, HRIS payroll, email | High-volume-repetitive | Violations → manager approval |
| UC-8 | Cash Flow Forecasting | Rolling 13-week forecast agent | 1 (+bank) | banking API (RazorpayX/ICICI), accounting connector, email | Continuous daily update | No (advisory) |
| UC-9 | Credit Note & Return Management | Credit-note lifecycle agent | 1 (+GST) | accounting connector, inventory, email, gst_portal (GSTR-1 credit-note) | High-volume-repetitive | No; refund path may gate |
| UC-10 | Multi-Currency Reconciliation | Forex recon + revaluation agent | 1 (+API) | RBI FX rate API (HTTP), accounting connector, banking API | Batch daily/month-end | No (advisory); postings reviewed |
| UC-11 | Month-End Close Acceleration | Close-orchestration agent | 1 | accounting connector, banking API, ERP | Monthly burst (last day → +3 days) | Close pack → CFO review |
| UC-12 | AP Fraud Detection | Continuous AP-fraud monitor | 1 | accounting connector, banking API, HRIS (conflict check) | Continuous monitoring | **Yes** — high-risk → CFO/internal-audit alert |

## 3. Connectors Required

**Existing (Bucket 1) — essentially the entire vertical, minimal new build:** `zoho_books`, `quickbooks`, `xero`, `tally` (XML gateway, semi-API), `document_reader` (invoice/receipt/statement OCR — the workhorse), `pdf_generator`, `email`, `slack`, `razorpay` (payment links + webhooks), `google_sheets`, `postgresql`. Dedup engine (`app/services/dedup.py`) directly powers UC-4 duplicate detection and UC-12 fraud pattern-matching. Code-execution for forecasting math (UC-8) and forex revaluation (UC-10).

**New-API (HTTP, not RPA):**
- **Corporate banking APIs** — ICICI / HDFC / SBI business banking + RazorpayX for balance, statement pull, and payment initiation. Gates UC-6, UC-8, UC-10, UC-11, UC-12. These have real (partner/OAuth) APIs → HTTP connector, no RPA. Build ~3–5 wks (per-bank auth is the variable cost); start with RazorpayX (already an ecosystem partner) + one major bank.
- **RBI FX reference-rate feed** — daily rates for UC-10. Public feed → trivial HTTP connector (~few days).
- **ERP read (SAP/Oracle)** — Enterprise-tier for UC-11 month-end close + UC-2 in large shops. API/connector, ~2–4 wks.
- **HRIS/payroll** — UC-7 reimbursement handoff + UC-12 employee-vendor conflict check. API, ~1–2 wks.

**Reused RPA (Bucket 2) — one touchpoint only, shared from D07:**
- **`gst_portal` e-invoice IRN + GSTR-1 credit-note reporting** — UC-1 e-invoice generation and UC-9 credit-note flow into GSTR-1. Reuse the D07 GST-portal connector; **no net-new RPA cost to this vertical**. (E-invoice IRN is available via GSP API; prefer that over RPA where licensed.)

**Net assessment:** this is the *least connector-constrained* of the four verticals — no dependence on the slow-to-build government-portal RPAs for its core value; banking APIs are the only meaningful new build.

## 4. Knowledge Collections

Seed slugs + ingestion recipe:
- **`payment-terms-by-customer`** — *per-tenant* customer master with terms (Net 30, 2/10 Net 30). Recipe: sync from accounting connector; drives UC-1 term application and UC-6 discount detection.
- **`collection-escalation-playbooks`** — tone/cadence templates for D+5/15/30/45/60 reminders (UC-3). Recipe: seed default cadence; per-tenant customization; grows from what actually gets paid.
- **`customer-relationship-notes`** — *per-tenant* payment-behaviour history + "handle with care" flags for key accounts (UC-3 HITL guardrail). Recipe: append collection outcomes + relationship notes.
- **`expense-policy`** — *per-tenant* meal/hotel/pre-approval limits + GL-coding rules (UC-7). Recipe: ingest company expense policy; version on change.
- **`vendor-master-fraud-signals`** — bank-account/IFSC fingerprints, vendor-add history, DOA thresholds for UC-12. Recipe: derive from accounting + banking data; flag shared-account and threshold-splitting patterns.
- **`fx-revaluation-rules`** — accounting treatment for realized/unrealized forex gain/loss, FIRC-matching rules (UC-10). Recipe: ingest Ind AS / accounting-standard guidance on forex.
- **`gst-invoicing-rules`** (shared with D07) — HSN/SAC → rate, e-invoice IRN thresholds, credit-note 180-day rule. Recipe: reuse D07's `hsn-sac-master` + `gst-law-provisions`; drives UC-1 tax computation and UC-9 credit-note timing.
- **`close-checklist`** — month-end task list: accruals, depreciation, prepayment amortization, intercompany eliminations (UC-11). Recipe: structured per-tenant close template.

## 5. Guardrails & Compliance

**Money moves here — payments are the irreversible action, so HITL gates the *outflow*, not government filings.**
- **Every payment above threshold is HITL-gated.** UC-6 early-payment execution requires approval for payments >₹10L; the reference AR manifest hard-denies `*.initiate_legal*`, and gates `email.send_to_executive` (CEO escalation) and `accounting.write_off_receivable` — money-moving and relationship-damaging actions never fire autonomously. Replicate: any `banking.initiate_payment` = require_approval with a tenant-configurable threshold; write-offs and refunds gated.
- **Fail-closed on fraud/duplicate suspicion:** UC-4 high-probability duplicates are *held* (not paid) pending resolution; UC-12 high-risk transactions block and alert CFO/internal audit rather than auto-processing. Bank-detail changes shortly before large payments (a classic diversion pattern) trigger a mandatory hold.
- **Segregation of duties:** the agent must not both create a vendor and approve its payment; UC-12 conflict checks (AP staff processing own related-party invoices) are enforced controls, surfaced to HITL.
- **Audit & evidence:** append-only, **WAL-based immutable audit trail** for all AP transactions (UC-12 explicitly; SOC2-ready trail is the Enterprise tier promise). Record every match decision, approval, payment, and the evidence (PO/GRN/invoice) behind it. Duplicate-prevention and fraud reports *are* the deliverable to the audit committee.
- **Tenant + entity isolation:** multi-entity groups (UC-11 intercompany) require per-entity scoping within a tenant; RLS enforces isolation; banking credentials in the encrypted vault, never in LLM context.
- **Approval-threshold config:** thresholds (₹10L etc.) are per-tenant policy in `app/governance/policies.py`, propagated across replicas via Redis pub/sub so a threshold change takes effect immediately everywhere.

## 6. Scale Pattern & Cost Drivers

**Mixed high-volume + continuous-monitoring; monthly bursts but not the millions-of-government-filings fan-out of GST/CA.**
- **High-volume-repetitive:** invoice generation (UC-1), 3-way matching (UC-2), expense processing (UC-7), credit notes (UC-9) — hundreds to low-thousands of transactions per tenant per month.
- **Continuous monitoring:** AR collections (UC-3), duplicate/fraud detection (UC-4/UC-12), early-payment scan (UC-6), cash-flow forecast (UC-8) — daily loops per tenant.
- **Monthly burst:** month-end close (UC-11) concentrates reconciliation load on the last day → +3 days.

**Architecture:**
- Per-plan Celery queues + per-tenant bulkhead as elsewhere; here the bulkhead also protects **banking-API rate limits** (banks throttle and flag aggressive access — same discipline as portal RPA but gentler).
- Dedup engine (`app/services/dedup.py`) does the heavy fuzzy-matching for UC-4/UC-12 in-process (embedding + rule-based) rather than per-invoice LLM calls — the key cost lever.
- Checkpointed state for long close/recon batches.

**Cost drivers:**
- **Balanced token + OCR cost.** Document parsing (invoices, receipts, vendor statements) is high-volume — batch OCR and cache parsed structures; reserve LLM only for exception reasoning and draft generation. Deterministic matching (dedup, 3-way match, forecast math via code-exec) should *not* hit the LLM at all — route through code, not the model.
- **Model routing** (`app/agent/model_router.py`): cheap models for extraction/classification, stronger model only for collection-email personalization and reconciliation-narrative drafting.
- Semantic + LLM-response caching for repetitive reminder templates and reconciliation letters; prompt compression for large ledger contexts. Per-tenant/per-goal budgets (`app/governance/cost.py`) cap runaway forecast/recon loops.
- Banking-API call volume (UC-8 daily balance pulls across many tenants) — cache intra-day, poll on schedule not on demand.

## 7. Decision & Phasing

**Flagship first: UC-3 Overdue Invoice Collection** — it is the reference manifest, targets the single most visceral MSME pain (DSO 60–90 days), and produces immediately measurable cash (DSO → 18–22 days, ₹50L freed per ₹1 crore reduction). It needs only accounting + email + razorpay — Bucket-1, ships fast. (UC-2 3-way matching is a close co-flagship on the AP side.)

**Phasing — largely un-gated on connectors, so sequence by ROI-provability:**
- **Phase A (weeks 1–4) — Bucket-1, self-funding proof:** UC-3 (AR collections), UC-2 (3-way matching), UC-4 (duplicate detection — performance-based revenue lands fast), UC-1 (invoice generation). All accounting-connector + document_reader; instant ROI story.
- **Phase B (weeks 3–8) — banking-API-gated value:** build the RazorpayX + one-bank connector, then UC-6 (early-payment optimization — performance revenue), UC-8 (cash-flow forecasting), UC-5 (vendor reconciliation), UC-7 (expense processing).
- **Phase C — Enterprise close + risk suite:** UC-11 (month-end close, needs ERP connectors), UC-12 (AP fraud detection — SOC2 trail), UC-10 (multi-currency, needs RBI FX + FIRC matching).
- **Phase D — GST-coupled flows:** UC-9 credit-note→GSTR-1 and UC-1 e-invoice IRN reuse the D07 `gst_portal` connector — sequence once that shared connector exists (no independent build).

**Sequencing rule:** payment-initiating actions (UC-6, refunds, write-offs) stay behind HITL thresholds from day one; loosen thresholds per-tenant only after the fraud/duplicate eval suites and collection-effectiveness eval (`collection-effectiveness-eval`) prove precision. The core AR/AP analysis value ships without ever needing autonomous money movement.

## 8. KPIs

- **DSO reduction:** 60–90 days → 18–22 days; ₹ working capital freed (₹50L per ₹1 crore DSO cut).
- **Duplicate/fraud prevention:** ₹ duplicates prevented + recovered (0.5–1.5% of AP spend); ₹ fraud losses averted (target ≥10% of the 5%-of-revenue exposure); false-positive rate on holds.
- **Early-payment yield:** discount-capture rate uplift (target +40%); annualized ₹ yield vs cost of funds.
- **Processing efficiency:** invoice generation 15min→2min; 3-way match 60h/mo → 5h; expense report 30min→5min; reconciliation 4h/vendor → 20min; error rate 8% → <0.5%.
- **Close speed:** month-end close 10 days → 3 days; days-earlier management information.
- **Forecast reliability:** 13-week forecast accuracy vs actuals; zero unplanned emergency credit draws.
- **Cost efficiency:** avg cost per invoice processed; % of transactions handled deterministically (no LLM call); cache/dedup hit rates.
- **Commercial:** transactions-per-tenant/month, performance-fee revenue share, attach rate to GST/CA and CFO customers.
