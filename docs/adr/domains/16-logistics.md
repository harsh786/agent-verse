# ADR-D16: Logistics & Supply Chain Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/16/use-cases.md

## 1. Market & Monetization
- **TAM:** India logistics ₹22 lakh crore ($270B), 10.5% CAGR. Logistics cost = 13% of GDP vs 8% global best — the inefficiency *is* the opportunity. Fragmentation (1,500+ carriers, 12M trucks, paper customs) makes orchestration high-value. Early adopters recover ₹40–90 lakh/yr in overbilled freight alone.
- **Buyer:** COO / Head of Supply Chain / Logistics Director (economic buyer); Warehouse/Transport/Procurement managers (champions). Segments: large manufacturers, 3PLs, e-commerce/quick-commerce, pharma cold-chain.
- **Three tiers (from source):**
  - **Starter: ₹49,999/month.** 3 agents, 5,000 shipments/mo, 5 carrier integrations, email support, basic dashboard.
  - **Growth: ₹1,99,999/month.** 10 agents, 50,000 shipments/mo, 20 carrier integrations, 1 WMS/ERP connector, HITL gates, Slack, dedicated CSM.
  - **Enterprise: ₹5,99,999/month.** Unlimited agents/shipments, all connectors, custom integrations, 99.9% SLA, on-prem option, white-glove onboarding, QBRs.
- **Consumption model:** Hybrid subscription + **per-transaction metering** (₹18/shipment tracked, ₹12/booking, ₹2,500/Bill-of-Entry, ₹8/last-mile shipment, ₹800/vehicle/mo, ₹500/vehicle/mo cold-chain). Plus a distinct **performance-fee (success-fee) play** on UC-6 Freight Invoice Audit: 20% of recovered overbilling (min ₹25k/mo retainer).
- **WTP:** High where savings are hard-metered — freight audit (recover ₹1.5–3 crore/yr on ₹10 crore spend), demurrage avoidance (₹1.5–3 crore/yr), cold-chain recall avoidance (₹5–50 crore/incident). Moderate on tracking/routing (labour + fuel savings, competitive/commoditized).
- **Time-to-first-revenue:** Freight audit is the fastest cash wedge — **pays for itself within 30 days of first audit cycle**, success-fee aligns incentives, needs only invoice ingestion + contract rate card (no live carrier integration). Tracking/booking need carrier connectors first (slower).
- **Monetization note:** UC-6 freight-invoice-audit is the flagship **performance-fee** entry (zero-friction ROI, self-funding). Customs (UC-3) and cold-chain (UC-9) are high-ticket, compliance-driven, high-margin. Carrier-portal RPA is the recurring opex/maintenance tax on the whole vertical — price it into transaction fees.

## 2. Use Cases -> Product Mapping

| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Real-Time Shipment Tracking & Exception Mgmt | Both | 2 | postgresql, carrier-APIs(new-API: FedEx/BlueDart/Delhivery/DTDC), browser-RPA (carrier portals), web_search, slack, email, document_reader (POD PDFs) | Real-time-monitoring | No (auto-escalate) |
| UC-2 | Carrier Rate Shopping & Booking | Both | 2 | carrier rate/booking APIs(new-API), browser-RPA (rate calculators), postgresql, slack, pdf_generator, print(new-API label printer) | Approval-gated | Yes (value >₹1L or hazmat) |
| UC-3 | Customs Documentation (Imports/Exports) | Both | 2 | document_reader, icegate-RPA (RPA-new), icegate-API(new-API), web_search (CBIC circulars), email, postgresql, doc-store | Approval-gated | Yes (broker/importer reviews B/E) |
| UC-4 | Route Optimization Analysis | Agent | 2 | google_maps(new-API), here-traffic(new-API), code-exec (OR-Tools), fleet-mgmt-API(new-API), sms/whatsapp(new-API), postgresql, web_search | High-volume-repetitive | No |
| UC-5 | Warehouse Cycle Count Reconciliation | Agent | 2 | wms-API(new-API), erp (SAP/Tally/Oracle)(new-API), postgresql, pdf_generator, slack, email | Approval-gated | Yes (adjustments >₹10k) |
| UC-6 | Freight Invoice Auditing (Overbilling Recovery) | Both | 1 | email/imap, document_reader, edi-parser(new-API), tms-API(new-API), postgresql, pdf_generator, slack | High-volume-repetitive | Yes (disputes >₹50k) |
| UC-7 | Demand Forecasting & Replenishment | Agent | 2 | erp(new-API), google_sheets, web_search, code-exec (ARIMA/XGBoost), slack, email, postgresql | Research+doc-gen | Yes (POs >₹5L) |
| UC-8 | Supplier On-Time Delivery Monitoring | Both | 2 | erp(new-API), email/imap, postgresql, slack, wms-API(new-API), pdf_generator | Real-time-monitoring | No (escalate to buyer) |
| UC-9 | Cold Chain Compliance (FDA/FSSAI) | Both | 2 | iot-mqtt/http(new-API), code-exec (MKT/Arrhenius), slack, erp(new-API), pdf_generator, email, postgresql | Real-time-monitoring | Yes (QA quarantine/release) |
| UC-10 | Last-Mile Delivery Exception Mgmt | Both | 2 | last-mile-API(new-API: Shiprocket/Shadowfax/Dunzo), whatsapp(new-API), sms(new-API), email/imap, oms-API(new-API), postgresql, slack, gps-telemetry(new-API) | Real-time-monitoring | No (conversational auto) |

## 3. Connectors Required
- **Existing (Bucket-1, reuse):** postgresql, email/gmail (imap/smtp), slack, web_search, document_reader (PDF parse), pdf_generator, code-exec. **UC-6 is almost entirely Bucket-1** — hence the fast success-fee wedge.
- **New-API (build, moderate):** Google Maps, HERE Traffic, WhatsApp Business API, SMS gateway, EDI parser (X12/EDIFACT), GSTN/tariff DB, IoT MQTT→HTTP gateway. Well-documented; ~1–2 wks each.
- **Carrier / freight / ERP-WMS connectors (build, the core investment):** FedEx / BlueDart / Delhivery / DTDC / Xpressbees / Ecom Express / Shadowfax rate+booking+tracking APIs (**new-API where public, browser-RPA fallback where not** — UC-1 explicitly replans API→RPA); TMS, OMS, WMS, Fleet Management, last-mile platforms (Shiprocket/Shadowfax/Dunzo), ERP (SAP/Tally/Oracle) — mostly new-API with per-vendor auth. This cluster is the freight/carrier build the vertical hinges on.
- **RPA-portal (build, brittle + compliance-sensitive):** **ICEGATE** (customs Bill-of-Entry / Shipping Bill / duty drawback — RPA-new, digital-cert auth, the single most complex portal build in this domain), carrier web portals (rate calculators + tracking scrape fallback), vendor portals for PO transmission (UC-7). High maintenance; selector drift + gov-portal downtime risk.
- **Build-cost verdict:** UC-6 ships on Bucket-1 (do first). Everything else gates on the carrier/ERP/WMS API cluster; ICEGATE RPA (UC-3) is a standalone high-effort, high-margin build.

## 4. Knowledge Collections (seed slugs + ingestion recipe)
- `carrier-rate-cards` — contracted rates, surcharge caps (fuel/remote-area/DG), zone matrices per carrier (UC-2, UC-6 audit baseline).
- `carrier-performance-scores` — historical on-time %, exception rates (UC-2 scoring, UC-8 supplier scorecard).
- `hs-code-tariff` — Indian Customs Tariff Schedule, CBIC circulars, antidumping/safeguard duties, duty-drawback schedule (UC-3; weekly web_search refresh).
- `cold-chain-thresholds` — product-specific temp/humidity limits mapped to FDA 21 CFR Part 211 / FSSAI Schedule 4 (UC-9).
- `supplier-master` — supplier contacts, lead-time variability, penalty clauses (UC-8).
- `status-code-taxonomy` — unified normalization map across 20+ carrier status vocabularies (UC-1).
- `pin-code-failure-clusters` — recurring last-mile address failure patterns (UC-10 feedback loop).
- **Ingestion recipe:** contract PDFs/EDI → structured rate-card records; tariff/regulatory pages via scheduled web_search + document_reader; IoT telemetry streamed (not embedded) with code-exec MKT computation; nightly ERP/WMS sync for inventory + PO state; all adjustments/disputes to audit trail.

## 5. Guardrails & Compliance
- **HITL money/inventory gates:** carrier booking >₹1L or hazmat (UC-2), dispute letters >₹50k (UC-6), inventory adjustments >₹10k (UC-5), POs >₹5L (UC-7), customs B/E submission (UC-3), cold-chain quarantine/release always (UC-9). Bounded-autonomous for tracking, routing, notifications.
- **Regulatory:** customs filings (ICEGATE) and duty-drawback claims are legally binding — human sign-off mandatory, immutable audit trail. Cold-chain excursion decisions logged per FDA/FSSAI audit template; reportable-threshold breaches trigger regulatory notification.
- **Data residency:** India (manifest per source); AES-256; 7-yr audit retention for customs/cold-chain.
- **RPA safety:** ICEGATE/carrier-portal RPA rate-limited; digital-signature credentials in vault; replan-to-RPA fallback (UC-1) must not double-book or double-file — idempotency keys required.
- **Financial-dispute integrity:** freight-audit dispute letters cite specific contract clause + AWB evidence; no auto-send of disputes above threshold without HITL to protect carrier relationships.

## 6. Scale Pattern & Cost Drivers
- **Dominant patterns:** Real-time-monitoring (UC-1 every 15min, UC-8, UC-9 continuous, UC-10) and High-volume-repetitive (UC-6 invoice lines, UC-4 routing). Research+doc-gen for UC-7 forecasting.
- **Cost drivers:** (1) **RPA portal minutes** — carrier-portal scraping and ICEGATE are the dominant opex + the reliability risk. (2) **Polling frequency** — 15-min shipment polls × 50,000 shipments = heavy connector-call + Celery load; batch and back off on stable statuses. (3) **Code-exec** — OR-Tools VRP (UC-4) and ARIMA/XGBoost forecasting (UC-7) are compute-bound, not LLM-bound. (4) **Document parsing** — high-volume invoice/POD/customs PDFs (document_reader).
- **Efficiency levers:** LLM used sparingly (mostly extraction + drafting, not reasoning) — keeps token cost low; cache carrier rate lookups; per-plan Celery queues; circuit breakers on flaky carrier APIs with RPA fallback; dedup duplicate AWBs (UC-6) before dispute.

## 7. Decision & Phasing
- **Phase 1 (self-funding wedge, Bucket-1):** UC-6 Freight Invoice Audit. Success-fee, near-zero connector build, 30-day payback — lands the account and funds the connector build-out.
- **Phase 2 (core operations, carrier/ERP cluster):** UC-1 Shipment Tracking + UC-2 Rate Shopping + UC-8 Supplier Monitoring + UC-5 Cycle Count. Requires the carrier/TMS/WMS/ERP connector investment.
- **Phase 3 (high-margin specialist):** UC-3 Customs (ICEGATE RPA), UC-9 Cold Chain (IoT), UC-7 Demand Forecasting, UC-4 Route Optimization, UC-10 Last-Mile. Compliance-heavy, higher price points, custom integration.
- **Rationale:** Freight audit proves ROI with no integration burden; core ops build the carrier/ERP moat; specialist compliance use cases command Enterprise-tier pricing.

## 8. KPIs
- **Product:** exception resolution time (6h → <22min), freight recovery (₹40–90 lakh/yr), demurrage avoidance, freight cost reduction (12–18%), on-time delivery (62%→89% supplier; 82%→94% first-attempt last-mile), inventory accuracy (99.2%), inventory days (40→24), forecast MAPE (28%→11%), cold-chain loss reduction (−40%).
- **Business:** MRR by tier, per-transaction volumes (shipments/bookings/B-E), success-fee recovery revenue (UC-6), net retention.
- **Trust/quality:** HITL approval/override rate on disputes & customs filings, carrier-relationship health (dispute-win rate), audit-trail completeness.
- **Efficiency:** RPA success rate + selector-break MTTR (carrier portals, ICEGATE), API→RPA fallback frequency, code-exec runtime per route/forecast job, LLM cost per shipment.
