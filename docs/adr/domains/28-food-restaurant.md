# ADR-D28: Food & Restaurant Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/28-food-restaurant/use-cases.md

## 1. Market & Monetization
- **TAM.** ₹7.76 lakh crore F&B sector; 7.5M food businesses (restaurants, cloud kitchens, QSR chains, banquets, food-manufacturing), growing 9%/yr. Serviceable target: the ~1M+ organised/semi-organised outlets on POS + aggregator platforms.
- **Buyer.** Owner/operator for single outlets; Ops Head / CFO / Franchise Ops for multi-outlet chains and cloud-kitchen brands; hotel F&B departments. Per-outlet economics; multi-outlet groups are the margin.
- **3 tiers (₹).**
  - **Starter:** ₹5,000/outlet/month — FSSAI, review monitoring, inventory basics; 500 runs/mo, 1 GSTIN, WhatsApp 1,000 msgs.
  - **Growth:** ₹15,000/outlet/month (min 3) — + delivery reconciliation, menu engineering, scheduling, supplier payment, loyalty; full POS+accounting; unlimited runs.
  - **Enterprise:** ₹8,000/outlet/month (min 20, annual) — + franchise compliance (Vision LLM), banquet, food-safety traceability, custom ERP, dedicated CSM.
- **Consumption model.** Per-outlet/month subscription (per-module or bundle) + per-GSTIN GST pricing + light usage fees (₹500/event banquet overage, ₹20/payout txn, ₹1,000/incident Starter). Per-outlet flat pricing scales cleanly with chain expansion.
- **WTP.** High and easy to justify — each module maps to a concrete avoided loss: 1 FSSAI closure = ₹5–50L, GST scrutiny = ₹50k–2L, ₹15–30k/mo recovered in aggregator underpayments, 18–22% food-cost reduction. Breakeven cited at 6 weeks.
- **Time-to-first-revenue.** Fast (weeks). FSSAI + review monitoring + reconciliation are self-evident ROI and land single outlets immediately; chains follow via multi-outlet pricing.
- **Monetization note.** Reconciliation and GST are the stickiest (recurring pain, hard to churn), FSSAI is the fear-driven wedge, franchise-compliance Vision is the enterprise upsell that replaces 3–4 audit FTEs. Sell Starter as ROI proof, expand to Growth bundle, land 20+ outlet chains on Enterprise annual.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | FSSAI Compliance Management | Both | 2 | fssai/FoSCoS (RPA-new), document_reader, pdf_generator, email, whatsapp, web_search | Research+doc-gen / Approval-gated | Light (renewal submit) |
| UC-2 | Aggregator Reconciliation (Swiggy/Zomato/ONDC) | Agent | 2 | swiggy_partner (RPA-new), zomato_partner (RPA-new), ondc (RPA-new), pos-connector (RPA-new: Petpooja/Posist/Rista), accounting (Tally/Zoho, RPA-new), code-sandbox, email | High-volume-repetitive (daily) | Yes (dispute >₹5,000) |
| UC-3 | Menu Engineering & Recipe Cost Optimization | Both (Template-led) | 1 | web_search (Agmarknet/APMC), pos-connector, code-sandbox, pdf_generator, whatsapp | Research+doc-gen (weekly/monthly) | No |
| UC-4 | Inventory Management & Wastage Reduction | Agent | 2 | pos-connector, inventory-connector (new), whatsapp, code-sandbox, pdf_generator, booking-system | High-volume-repetitive (daily 06:30) | No |
| UC-5 | Staff Scheduling Optimization | Agent | 2 | pos-connector, scheduler, whatsapp, biometric-attendance (new), google_calendar, code-sandbox | High-volume-repetitive (weekly) | No |
| UC-6 | Customer Review Monitoring & Response | Agent | 2 | zomato/swiggy (RPA-new), google_mybusiness (new), web_search, whatsapp, code-sandbox | Real-time-monitoring (2-hourly) | Yes (response approval, 30s) |
| UC-7 | Supplier Payment & Vendor Management | Agent | 2 | accounting, bank-api (new: HDFC/ICICI/Axis), razorpay-payout, whatsapp, pdf_generator, code-sandbox | Approval-gated (weekly) | Yes (payment plan) |
| UC-8 | GST Reconciliation for Restaurants | Agent | 2 | gst_portal (RPA-new), accounting, pos-connector, swiggy/zomato-api, pdf_generator, code-sandbox | Approval-gated / Research+doc-gen (monthly) | Yes (CA review before file) |
| UC-9 | Franchise Compliance & SOP Monitoring | Agent | 1 (needs vision) | whatsapp, perception/vision-LLM, web_search, pdf_generator, code-sandbox, scheduler | Real-time-monitoring / High-volume (daily photos) | No (auto-corrective request) |
| UC-10 | Banquet & Event Booking Management | Both | 1 | google_calendar, whatsapp, razorpay, pdf_generator, email, accounting | Real-time / Research+doc-gen | No |
| UC-11 | Loyalty Program Management & Win-Back | Agent | 1 | pos-connector, whatsapp, mailchimp, code-sandbox, pdf_generator | High-volume-repetitive (weekly RFM) | No |
| UC-12 | Food Safety Incident Response & Traceability | Agent | 2 | inventory-connector, pos-connector, pdf_generator, email, whatsapp, web_search, code-sandbox | Research+doc-gen / real-time incident | Yes (recall / SFSO report) |

## 3. Connectors Required
**Existing (Bucket 1):** whatsapp, email, sms, razorpay (+payout), pdf_generator, document_reader, web_search, google_calendar, mailchimp, slack, code-sandbox.
**New-API (Bucket 2):** POS connectors (Petpooja / Posist / Rista — the critical dependency; build once, reuse across all outlets), accounting (Tally / Zoho Books), Swiggy Partner API, Zomato Partner API, ONDC seller connector, bank corporate-payout APIs (HDFC/ICICI/Axis), biometric-attendance API, google_mybusiness API, booking/reservation system API.
**RPA-portal (Bucket 2, high maintenance):** FoSCoS FSSAI portal (fssai RPA-new — foscos.fssai.gov.in), gst_portal (gst.gov.in GSTR-1/3B filing), Swiggy/Zomato partner portals (dispute raising + review posting where API absent).
**Perception/Multimodal:** Vision-LLM for UC-9 franchise SOP photo scoring (40+ criteria) — reuse the shared `perception/` capability from Agriculture/other verticals.
**Build-cost callout.** POS + accounting connectors are the platform bet — they gate UC-2/3/4/5/8/11/12. Prioritise Petpooja/Posist (dominant in India). GST + FoSCoS RPA are compliance-critical and change frequently — budget maintenance.

## 4. Knowledge Collections
Seed slugs:
- `food-fssai-rules` — licence classes, renewal cycle, 25-point inspection criteria, gazette amendments, additive/labelling standards.
- `food-gst-slabs` — restaurant GST matrix (5%/12%/18%, Section 9(5) ECO reverse charge, Section 17(5) ITC blocks).
- `food-recipe-cost-db` — per-item standard recipes (ingredient × qty) — per-tenant, drives menu engineering + traceability.
- `food-commodity-prices` — Agmarknet/APMC/NHB mandi feeds for onion/tomato/oil/dal/chicken/mutton.
- `food-aggregator-commission` — Swiggy/Zomato/ONDC commission + settlement-cycle + cancellation policy baselines.
- `food-brand-sop` — per-franchise SOP checklist for Vision scoring.
- `food-vendor-master` — per-tenant vendor terms, criticality tier (A/B/C), balances.
**Ingestion recipe.** FSSAI/GST rules from official gazettes + circulars (document_reader → chunk → embed, monthly refresh); recipe DB via one-time per-outlet setup (₹5,000 onboarding); commodity prices via weekly web_search pull → structured append; SOP checklist per franchise brand at onboarding.

## 5. Guardrails & Compliance
- **Regulatory filings are HITL-gated.** GSTR-3B filing requires CA approval before submit; FSSAI renewal submission and show-cause responses reviewed before dispatch. Never auto-file a statutory return.
- **Financial actions HITL-gated.** Supplier payment plans (UC-7) and aggregator disputes >₹5,000 (UC-2) require owner approval; payment initiation via bank/razorpay-payout logged in audit trail.
- **GST classification correctness.** Misclassification is the #1 restaurant scrutiny trigger — hard-map each transaction to slab by outlet type + service mode + Section 9(5); flag, never guess, ITC eligibility (food ingredients blocked under 17(5)).
- **Food-safety incident (UC-12).** SFSO reporting within FSSAI-mandated 24h for serious incidents; proactive customer notification requires owner sign-off; preserve full traceability timeline for legal counsel.
- **Review responses (UC-6)** pass HITL for ≤2-star before posting; brand-voice guardrail; no auto-compensation without approval.
- **Data classification = financial/PII** (customer DB, bank credentials, GSTINs). Per-tenant RLS isolation; secrets in vault; audit trail on all money movements and filings.

## 6. Scale Pattern & Cost Drivers
- **Dominant pattern:** High-volume-repetitive (daily reconciliation, daily inventory 06:30, daily franchise photos) + Real-time-monitoring (reviews every 2h). Multi-outlet chains multiply every daily job by outlet count.
- **Cost drivers (ranked):** (1) POS/accounting API polling + reconciliation compute (pandas per outlet per day); (2) RPA runtime for GST filing, FoSCoS, aggregator dispute/review portals; (3) Vision inference for franchise SOP (5 photos × N outlets daily); (4) WhatsApp messaging (staff, suppliers, loyalty, reviews); (5) LLM review-response drafting.
- **Mitigations:** dedup + LLM-response cache for repetitive review replies and vendor messages; per-plan Celery queues so a 30-outlet enterprise chain isn't blocked by free-tier; batch multi-outlet reconciliation; tool-cache aggregator settlement pulls; schedule Vision scoring off-peak.

## 7. Decision & Phasing
- **Phase 1 (single-outlet wedge, weeks 0–6):** UC-1 FSSAI + UC-6 Reviews + UC-4 Inventory (Starter bundle). Fear-driven + reputation + waste ROI; requires POS connector + FoSCoS RPA + Vision-lite.
- **Phase 2 (Growth bundle):** UC-2 Reconciliation + UC-8 GST + UC-3 Menu + UC-5 Scheduling + UC-7 Supplier Pay + UC-11 Loyalty. The recurring-pain, sticky modules; requires accounting + aggregator APIs + gst_portal RPA hardened.
- **Phase 3 (Enterprise):** UC-9 Franchise Compliance (Vision) + UC-10 Banquet + UC-12 Traceability. Chain/hotel-group tier; Vision + full traceability spine.
- **Verdict:** Accept. Strongest per-outlet ROI story in the portfolio and clean multi-outlet scaling. Gate on POS connector coverage (Petpooja/Posist) and GST/FoSCoS RPA reliability — these are the whole vertical's foundation.

## 8. KPIs
- Aggregator underpayment recovered ₹15–30k/outlet/mo; reconciliation time 15h→<1h/mo.
- FSSAI: zero lapsed licences across managed outlets; renewal cycle time reduction.
- Food cost 36%→32%; wastage 25%→12%.
- Review response rate <30%→~100%; response time →<30 min; Zomato rating maintained ≥4.0.
- GST: zero misclassification scrutiny notices; 8h→<1h monthly filing prep.
- Franchise compliance score trend; corrective-action closure rate; audit-FTE reduction (3–4 FTE).
- Platform: per-outlet cost vs subscription, POS-connector uptime, RPA success rate (GST/FoSCoS/aggregator), review-reply cache hit-rate.
