# ADR-D31: Automobile & EV Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/31-automobile/use-cases.md

## 1. Market & Monetization
- **TAM:** ₹7.78 lakh crore automotive sector (7.1% of GDP, 4.4 crore vehicles/year); 25,000+ dealerships, 10 crore+ vehicles in service/insurance pipelines, ₹1.2 lakh crore auto-loan portfolios, 1,300+ RTOs, fast-growing EV base (17 lakh units FY24). A typical OEM/large dealer group wastes ₹8–18 crore/year on manual coordination.
- **Buyer:** Dealer Principal / DGM After-Sales (service, sales, parts, warranty), OEM After-Sales / Warranty / Recall Head, NBFC Collections Head (delinquency), fleet operator (telematics, EV charging), CPO (charging network).
- **3 tiers (₹):** Tier 1 Dealer ₹24,999/month (single outlet <200 vehicles/month — service scheduling, sales funnel ≤500 leads, insurance renewal ≤200, parts alerts, 1 DMS); Tier 2 Dealer Group ₹89,999/month (5–25 outlets — RC transfer ≤100/mo, warranty ≤500/mo, valuation ≤200/mo, EMI delinquency ≤2,000 accounts, EV charging ≤20 chargers, telematics ≤50 vehicles); Tier 3 OEM/Fleet Enterprise ₹3,49,999/month + per-unit (unlimited scale, recall mgmt, EV fleet optimisation, NBFC collections, pan-India RTO, white-label CPO).
- **Consumption model:** per-outlet/month subscription + heavy per-transaction metering — per-RC-transfer (₹800–1,500), per-valuation (₹500–1,500), per-warranty-claim (₹80–200), per-VIN-recall (₹50–150), per-collection-case (₹300–800), per-vehicle/month (₹300–500), per-charger/month (₹2,000), insurance referral fees (₹150–400), 2% CPO transaction fee.
- **WTP:** Moderate — ROI is real but per-unit values are smaller than pharma/energy: 85% bay utilisation = ₹18 lakh/month per outlet; RC automation saves ₹15 lakh/month per 200-txn group; 35% roll-rate reduction = ₹4.2 crore/year NPA cut per ₹100 crore portfolio.
- **Time-to-first-revenue:** Fast — Tier 1 rides existing DMS + WhatsApp connectors; service/sales/insurance modules live in days.
- **Monetization note:** Land dealers cheaply with high-frequency Bucket-1 WhatsApp/DMS automation (service, sales, insurance), then upsell Vahan-RPA transaction modules (RC, warranty, recall) and enterprise fleet/EV real-time modules; per-transaction fees make revenue scale with dealer throughput.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Service Center Appointment Scheduling & Follow-Up | Agent | 1 | DMS (New-API — CDK/Reynolds), whatsapp, sms, CRM/salesforce, slack | High-volume-repetitive | Yes (dissatisfied callback) |
| UC-2 | Vehicle RC Transfer & RTO Compliance | Both | 2 | DMS, CRM, whatsapp, document_reader, bank API (New-API), vahan (RPA), pdf_generator, email, digilocker | Approval-gated | Yes (delays >30d) |
| UC-3 | Insurance Renewal Reminder & Comparison | Agent | 2 | DMS, loan system (New-API), whatsapp, sms, insurance aggregator PolicyBazaar (New-API), browser RPA, CRM, document_reader, slack | High-volume-repetitive | Yes (lapsed NBFC) |
| UC-4 | Spare Parts Inventory Management & Reorder | Agent | 2 | DMS, analytics, optimization, vahan (RPA), OEM parts portal (RPA-new), pdf_generator | High-volume-repetitive | Yes (PO >₹50k) |
| UC-5 | EV Fleet Charging Schedule Optimization | Agent | 3 | fleet telematics (New-API), fleet management (New-API), utility/DISCOM API (New-API), EVSE OCPP (New-API), optimization, slack, analytics, pdf_generator | Real-time-monitoring | No |
| UC-6 | Loan EMI Delinquency Prediction & Collection | Agent | 3 | loan mgmt Finacle/Nucleus (New-API), CIBIL/Experian (New-API), web_search, analytics, whatsapp, sms, field force (New-API), BBPS (New-API), slack, email | High-volume-repetitive | Yes (field collection 30+ DPD) |
| UC-7 | Vehicle Recall Management & Communication | Agent | 2 | OEM internal API (New-API), vahan (RPA/API), whatsapp, sms, email, google maps, DMS, OEM parts portal, document_reader, analytics, pdf_generator, MORTH portal (RPA-new) | Approval-gated + High-volume | Yes (safety recall dispatch) |
| UC-8 | Used Vehicle Valuation & Trade-In Processing | Both | 3 | DMS, CRM, vahan API, insurance MCP, OEM API, browser RPA (CarDekho/OLX/Cars24), vehicle auction (New-API), analytics, computer vision (New-API), pdf_generator | Research+doc-gen | Yes (>₹10 lakh) |
| UC-9 | Warranty Claim Processing & Dealer Reimbursement | Agent | 2 | DMS, OEM warranty API (New-API), pdf_generator, OEM warranty portal (RPA-new), email, analytics | High-volume-repetitive + Approval-gated | Yes (claim appeal) |
| UC-10 | Sales Funnel Management for Dealerships | Agent | 1 | lead aggregator (New-API), whatsapp, CRM, google_calendar, email, slack, pdf_generator | High-volume-repetitive | No |
| UC-11 | Fleet Telematics Analysis & Driver Scoring | Agent | 3 | fleet telematics Fleetx/Rosmerta (New-API), analytics, whatsapp, slack, CMMS (New-API), pdf_generator | Real-time-monitoring | No |
| UC-12 | EV Charging Station Management & Billing | Agent | 3 | EVSE OCPP 2.0.1 (New-API), field service, whatsapp, pdf_generator, UPI/razorpay, analytics, tax filing/GST (New-API) | Real-time-monitoring | No |

## 3. Connectors Required
- **Existing (Bucket 1):** email, whatsapp, sms, document_reader, web_search, pdf_generator, razorpay, digilocker, salesforce (as CRM), google_calendar, google_maps. Cost: ~0.
- **New-API (build, medium):** DMS (CDK Global/Reynolds), corporate bank API, insurance aggregator (PolicyBazaar/Coverfox), loan management (Finacle/Nucleus), CIBIL/Experian bureau, BBPS, OEM warranty + internal engineering API, fleet telematics (Fleetx/Rosmerta/Mobisoft), fleet management, EVSE OCPP 2.0.1, vehicle auction platform, computer vision (Azure — damage detection), utility/DISCOM API, CMMS, field-force app, GST/tax filing, lead aggregator. Cost: 8–15 dev-days each; OCPP + telematics streaming higher.
- **RPA-portal (Bucket 2):** **vahan** (parivahan.gov.in — RC transfer, owner lookup, RC status), **sarathi (RPA-new)** for driver-licence/RTO extensions, MORTH recall progress portal (RPA-new), OEM parts + warranty portals (RPA-new, per-OEM), CarDekho/OLX/Cars24 marketplace scraping, state RTO portals. Cost: Vahan is the critical shared connector (rate-limited, CAPTCHA-prone) — 20–30 dev-days + heavy maintenance; per-OEM portals variant.

## 4. Knowledge Collections (seed slugs + ingestion recipe)
- `vahan-form-templates` — Form 28/29/30 layouts + field maps per state RTO. Ingest: parivahan forms → structured template store.
- `warranty-coverage-rules` — VIN/age/mileage/fault eligibility matrix per OEM. Ingest: OEM warranty policy docs → rule table.
- `dtc-fault-code-matrix` — DTC → warranty-claim eligibility mapping. Ingest: OEM service manuals + fault-code catalog.
- `used-vehicle-price-index` — CarDekho/OLX/Cars24/auction comps by model/variant/city. Ingest: daily RPA scrape → time-series regression features.
- `delinquency-propensity-features` — payment history + bureau + income signals. Ingest: LMS + CIBIL feed (monthly refresh).
- `dealer-prequalification` + `ocpp-tariff-schedules` — dealer master + ToU/congestion tariffs per depot.

## 5. Guardrails & Compliance
- Frameworks: DPDP 2023 (customer PII masking, consent framework), RBI supervisory reporting (NBFC collections), MORTH recall completion reporting, RTO/warranty 5-year record retention, GST compliance (CPO billing, GSTR-1), Factories/RTO norms.
- HITL mandatory gates: safety-critical recall notice dispatch (quality + legal head, 2h), high-value trade-in valuation >₹10 lakh (used-car manager, 1h), warranty claim appeal (service head, 4h), field-collection assignment for 30+ DPD, lapsed-policy NBFC escalation, RC-transfer delay escalation.
- Data: customer PII masked; 5-year (1825-day) retention for RTO + warranty; vehicle-state cache in Redis (TTL 3600s); collections interactions archived for RBI audit; multilingual outputs (en/hi/mr/ta/te/kn).

## 6. Scale Pattern & Cost Drivers
- Predominantly **high-volume-repetitive** (service, sales, insurance, parts, warranty) with a **real-time-monitoring** EV/fleet layer and an approval-gated RTO/recall/collections layer.
- Cost drivers: (1) **WhatsApp conversational volume** — LLM tokens per lead/customer/driver at dealer scale is the dominant spend; (2) **Vahan RPA is the throughput bottleneck** (rate-limited, CAPTCHA, session concurrency) and the primary maintenance risk; (3) telematics + OCPP streaming ingestion for fleet/EV; (4) computer-vision inference for damage detection/valuation. Per-transaction economics require tight token budgeting on the WhatsApp qualification flows.

## 7. Decision & Phasing
- **Phase 1 (Bucket 1 fast wins — DMS + WhatsApp, existing connectors):** UC-1 service scheduling, UC-10 sales funnel, UC-3 insurance renewal. Pure existing connectors + DMS API; land single-outlet dealers on Tier 1 in days.
- **Phase 2 (Bucket 2 RPA/API — Vahan RTO core per domain note):** UC-2 RC transfer (build vahan RPA), UC-9 warranty claims, UC-4 parts inventory, UC-7 recall management (MORTH RPA-new), UC-8 used-vehicle valuation, UC-6 EMI collections (LMS + bureau + BBPS). Dealer-group and NBFC/OEM expansion.
- **Phase 3 (Bucket 3 real-time — OCPP/telematics, Enterprise tier):** UC-5 EV fleet charging optimisation, UC-11 fleet telematics + driver scoring, UC-12 CPO station management + billing. Require OCPP/telematics streaming and fleet/CPO customers.

## 8. KPIs
- Bay utilisation 65% → 85% (₹18 lakh/month/outlet); post-service follow-up coverage ↑.
- RC-transfer TAT ↓ 50%, processing cost ↓ 80%; insurance renewal conversion +35%.
- Parts stockouts ↓ 30%, carrying cost ↓ 20%; warranty reimbursement cycle ↓ 50%, doc-error rate ↓ 70%.
- EMI 30–90 DPD roll-rate ↓ 35%; recall completion rate +40% (>50% baseline).
- Lead response time 4h → 60s; test-drive conversion +35%.
- EV fleet electricity cost ↓ ₹25–40 lakh/year per 100 vehicles; SoC compliance 72% → 98%.
- Charger uptime 65% → 91%; CPO billing leakage 12–18% → ~0; fleet fuel cost ↓ 15–20%.
