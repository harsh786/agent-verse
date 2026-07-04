# ADR-D05: Operations & Supply Chain Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/05-operations/use-cases.md

## 1. Market & Monetization
- **TAM:** India supply-chain pain is the anchor: ₹2.7 lakh-crore working capital locked, ₹1.4 lakh-crore/yr lost to disruptions, 32% of logistics invoices overbilled, OTIF failure 23% in Indian manufacturing. India-first B2B ops software market.
- **Buyer persona:** COO / Head of Supply Chain / CPO (procurement) / plant or warehouse ops manager at manufacturers, distributors (100+ SKUs), D2C/e-commerce brands, retail chains; often the CFO co-signs on the cash-recovery UCs (freight audit, SLA penalties, vendor credits).
- **Pricing tiers (₹):**
  - **Operations Starter — ₹15,000/mo:** demand forecasting, supplier delivery monitoring, freight invoice auditing; ≤50 active POs; Tally/Zoho ERP connector.
  - **Operations Professional — ₹45,000/mo:** full suite incl. spend analysis, RFQ-to-PO, returns management; unlimited POs/suppliers; multi-ERP; custom procurement rules.
  - **Supply Chain Enterprise — ₹1,50,000+/mo:** + predictive disruption modeling; custom carrier integrations; multi-site/multi-entity; SAP/Oracle ERP.
- **Consumption add-on model:** strong **outcome/recovery-based pricing** — 20% of recovered freight overbilling (min ₹5,000/mo), 10% of recovered SLA penalties; plus usage meters (₹10/PO processed, ₹200/return processed) and module fees (₹20,000/mo demand, ₹15,000/mo supplier, ₹12,000/mo cycle count, ₹10,000/mo exceptions, ₹30,000 one-time spend-analysis report). Contract-registry setup ₹15,000 one-time.
- **Willingness-to-pay:** High on the cash-recovery UCs (freight audit recovers ₹75L–1.5cr on ₹5cr freight; SLA penalties ₹40–80L/yr; vendor credits ₹18–30L/yr) — these are net-positive-from-day-one and CFO-approved fast.
- **Time-to-first-revenue:** ~2 weeks for the document-driven cash-recovery UCs (freight audit, contract registry) which need no ERP connector; ERP-dependent UCs slower.
- **Monetization note:** **Split — recovery-based Bucket-1 fast-cash vs ERP/logistics connector-gated.** Freight audit (UC-6) and contract renewal (UC-10) run on document_reader + email + knowledge + docusign (catalog) and pay for themselves on recovery. The operational core (demand, supplier, cycle-count, RFQ-to-PO, returns) is **connector-gated on ERP/WMS** — Tally is a **Bucket-2 RPA portal**, SAP/Oracle/OMS/WMS are **New-API**; zoho_books/xero cover the accounting side (catalog).

## 2. Use Cases -> Product Mapping
| UC-N | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|------|-------|----------|--------|------------|---------------|-------|
| UC-1 | Demand Sensing and Rolling Forecast Updates | Marketplace Agent | 2 | web_search, slack, google_sheets, zoho_books/xero; **RPA:** tally (ERP); **New-API:** SAP/Oracle ERP | Real-time-monitoring | No |
| UC-2 | Supplier On-Time Delivery Monitoring | Marketplace Agent | 2 | email, web_search, slack, google_sheets; **RPA:** tally; **New-API:** ERP | Real-time-monitoring | Yes (mitigation selection) |
| UC-3 | Procurement Spend Analysis and Optimization | Both | 2 | web_search, email, pdf_generator, google_sheets; **RPA:** tally; **New-API:** ERP | Research+doc-gen | No |
| UC-4 | Warehouse Cycle Count and Inventory Reconciliation | Marketplace Agent | 2 | email, slack, pdf_generator; **RPA:** tally; **New-API:** WMS | High-volume-repetitive | Yes (inventory adjustment approval) |
| UC-5 | Order Exception Management | Marketplace Agent | 3 | email, razorpay (refunds), slack; **New-API:** OMS/ERP, Shiprocket/Delhivery | Real-time-monitoring | Yes (high-value/VIP exceptions) |
| UC-6 | Freight Invoice Auditing | Both | 1 | document_reader, email + knowledge base (rate cards); **New-API:** Shiprocket/Delhivery (claim submit) | High-volume-repetitive | No |
| UC-7 | RFQ-to-PO Automation | Marketplace Agent | 2 | email, document_reader, google_sheets; **RPA:** tally; **New-API:** ERP | Approval-gated | Yes (spend-threshold approvals) |
| UC-8 | SLA Tracking for Logistics Partners | Marketplace Agent | 3 | email, pdf_generator; **New-API:** ERP, Delhivery/BlueDart/DTDC/Shiprocket | Real-time-monitoring | No |
| UC-9 | Returns Processing and Vendor Credit Management | Marketplace Agent | 2 | email, pdf_generator, zoho_books/xero; **RPA:** tally; **New-API:** WMS | High-volume-repetitive | No |
| UC-10 | Contract Renewal and Vendor Management | Both | 1 | document_reader, email, pdf_generator, docusign, slack + knowledge base | Research+doc-gen | Yes (renewal-term change approval) |

## 3. Connectors Required
- **Existing (catalog · Bucket 1 · no build):** document_reader, pdf_generator, gmail/email, slack, google_sheets, web_search, razorpay, zoho_books, xero, docusign, quickbooks.
- **New-API (build; medium–high cost):** **ERP/OMS/WMS** — SAP, Oracle, generic OMS/WMS (the gating build cluster for the operational core); **logistics carriers** — Shiprocket, Delhivery, BlueDart, DTDC (these have APIs → New-API; gate UC-5/6/8). Enterprise SAP/Oracle is the high-cost, high-value tier unlock.
- **RPA-portal (Bucket 2 · needs build):** **tally** (catalog RPA — the default India ERP for SMB tenants; unlocks UC-1/2/3/4/7/9). Other Indian-portal ops connectors (gst_portal, mca21, vahan, ondc) not required by these UCs but adjacent.
- **Build-cost flag:** heaviest connector-build burden of the five domains — nearly every operational UC needs Tally (RPA) or an ERP/WMS/logistics New-API. Prioritize Tally + one logistics carrier to unlock the majority.

## 4. Knowledge Collections
- `rate-cards-by-carrier` — contracted freight rate cards + surcharge/accessorial rules per carrier and lane (UC-6, UC-8) — the core asset enabling freight audit recovery.
- `supplier-master` — supplier registry, escalation contacts, historical OTIF scorecards (UC-2).
- `procurement-policies` — approved-vendor lists, spend-approval thresholds, standard payment/commercial terms (UC-3, UC-7).
- `vendor-contracts` — parsed contract registry: rates, SLA clauses, expiry dates, auto-renewal flags (UC-10) + purchase-agreement return terms (UC-9).
- **Ingestion recipe:** ingest the customer's rate cards, supplier master, procurement policy, and signed vendor contracts via document_reader; extract commercial terms into structured records; market-price benchmarks fetched live via web_search + vendor catalogs; ingest customer-owned documents only.

## 5. Guardrails & Compliance
- **Regulated:** Not statutorily regulated at the agent layer, but financially sensitive — procurement fraud/segregation-of-duties controls, contract-liability exposure, and 3-way-match integrity for payables. GST/tax-document handling implied on invoices.
- **Mandatory HITL gates:** all PO creation above threshold (`erp.create_po` → require_approval; auto <₹25K, HITL ₹25K–₹5L, senior >₹5L per UC-7), inventory adjustments (UC-4), delay-mitigation selection incl. airfreight booking (UC-2), high-value/VIP order-exception handling (UC-5), and renewal-term changes (UC-10).
- **Fail-closed policy:** the agent never issues a PO, adjusts inventory, or books freight without approval above the configured threshold; freight/SLA claims are generated as drafts citing the specific contract clause and submitted only after the rate-card comparison is verified; 3-way match must reconcile before payment is initiated.
- **Audit needs:** trail of every PO, adjustment, dispute claim, debit/credit note, and contract change for procurement audit and CFO cash-recovery reporting; supplier scorecards and drift retained for quarterly reviews.

## 6. Scale Pattern & Cost Drivers
- **Dominant shape:** Real-time-monitoring (daily PO/order/demand/SLA scans) + High-volume-repetitive (cycle counts, exceptions, invoice/return processing) + Research+doc-gen (spend analysis, contract benchmarking).
- **Expected goal volume:** moderate-to-high, order-linked — e.g. 10,000 orders/mo yields 150–400 exception goals; 500 POs/mo; per-invoice and per-return metered volume.
- **Cost drivers:** document parsing at volume (freight invoices, POs, contracts, vendor quotes — the dominant token cost); daily ERP/order scans; RPA session minutes for Tally (brittle, retry-prone); market-price web_search.
- **Caching/routing levers:** deterministic rate-card matching (rules, not LLM) for freight/SLA audit — reserve the LLM for exception explanation and claim-letter drafting; cache market-price benchmarks (weekly refresh); cheap model for classification (return condition, spend category, exception type), strong model for negotiation briefs and root-cause analysis; batch nightly scans; dedup on repeated supplier/carrier queries.

## 7. Decision & Phasing
- **Flagship first:** **UC-6 Freight Invoice Auditing** — Bucket-1 (document_reader + rate-card knowledge + email), 20%-of-recovery pricing makes it net-positive on day one, no ERP connector required. The CFO-friendly wedge.
- **Fast-follows (Bucket-1, no ERP):** UC-10 Contract Renewal & Vendor Management (document_reader + docusign); pairs with UC-6 as the "cash-recovery + leakage-prevention" landing bundle.
- **Connector-gated (build Tally RPA + one logistics carrier first):** UC-8 SLA tracking and UC-5 order exceptions (logistics carriers), then the Tally-dependent operational core — UC-1 demand, UC-2 supplier monitoring, UC-3 spend analysis, UC-4 cycle count, UC-7 RFQ-to-PO, UC-9 returns. SAP/Oracle + predictive disruption modeling gated to Enterprise tier.

## 8. KPIs
- **Adoption:** invoices audited/mo; contracts under registry; POs processed via agent; suppliers/orders under monitoring; ERP tenants connected (Tally vs SAP/Oracle mix).
- **Reliability:** `supply-chain-ops-eval` pass %; RPA (Tally) success % (target >90%, watch brittleness); freight-audit false-positive rate on flagged overbilling; claim-recovery success rate.
- **Cost/goal:** tokens per invoice/contract/PO parsed; RPA-minutes per Tally session; scan cost per order/day.
- **Revenue/tenant:** tier MRR + recovery-share revenue (freight 20%, SLA 10%) + per-PO/per-return usage; Starter→Professional→Enterprise expansion (ERP depth = tier-up); total customer cash recovered/prevented (the headline retention metric CFOs renew on).
