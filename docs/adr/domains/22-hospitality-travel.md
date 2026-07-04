# ADR-D22: Hospitality & Travel Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/22-hospitality-travel/use-cases.md

## 1. Market & Monetization
- **TAM.** ₹15.2 lakh crore sector, domestic travel +28% YoY. 50,000+ unmanaged Indian properties leave ₹12,000 crore of RevPAR on the table; revenue management is still manual at 68% of hotels. Serviceable: independent/boutique/mid-chain hotels (30–200 rooms) plus MICE venues; adjacencies in airline ancillary pricing and corporate travel.
- **Buyer.** Revenue Manager / GM / Owner for hotels; Sales Head for MICE; corporate Travel Manager / Finance Controller for travel-policy (UC-8); airline pricing team for seat-level. Property-level economics; chains and groups aggregate.
- **3 tiers (₹).**
  - **Property:** ₹29,999/month — 3 agents (pricing + reviews + scheduling), 1 PMS integration, top-5 OTA scraping, basic analytics.
  - **Growth:** ₹1,19,999/month — all 10 UCs, unlimited properties in tier, loyalty, group booking, multilingual WhatsApp, HITL gates, CSM.
  - **Enterprise:** ₹3,99,999/month — airline/OTA seat-level pricing AI (IDeaS-equivalent), custom PMS/GDS, corporate travel automation, 99.9% SLA, white-label.
- **Consumption model.** Property/month subscription + per-transaction success fees (₹800/personalised booking, 2% group-revenue commission, ₹500/recovered cancellation, ₹300/travel booking validated). Value-based success fees align with revenue lift.
- **WTP.** High for revenue-impacting agents: dynamic pricing drives 22–38% RevPAR (₹25–90L/yr/hotel) — the platform is a rounding error against that. The wedge is being the affordable IDeaS/Duetto alternative (those cost ₹15–60L/yr, inaccessible <150 rooms).
- **Time-to-first-revenue.** Moderate — dynamic pricing + review monitoring land the Property tier in weeks, but PMS integration is the gating dependency for onboarding.
- **Monetization note.** Anchor on RevPAR lift (UC-1/UC-5) — it self-funds and is the reason to buy. Reviews (UC-3) and cancellation recovery (UC-9) are high-retention add-ons. Corporate travel-policy (UC-8) is a distinct enterprise buyer (corporates, not hotels) with its own large ACV — treat as a separate GTM motion.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Dynamic Room / Seat Pricing Optimization | Agent | 2 | pms-api (new), ota-scrape (RPA-new), web_search, weather_api, ixigo-api (new), channel-manager-api (new: SiteMinder), code-sandbox, slack | Real-time-monitoring (2-hourly) | Yes (rate change >15% / peak dates) |
| UC-2 | Guest Personalization & Pre-Arrival | Agent | 2 | pms-api, whatsapp, crm-api (new), flight-tracking (new), uber/meru (new), pdf_generator, email, slack | High-volume / Real-time | No |
| UC-3 | Online Review Monitoring & Response | Agent | 2 | ota-scrape (RPA-new), google_mybusiness (new), tripadvisor-api (new), translation, code-sandbox, slack, email | Real-time-monitoring (hourly) | Yes (≤2-star responses) |
| UC-4 | Group Booking Coordination | Both | 2 | pms-api, email, whatsapp, pdf_generator, code-sandbox, slack | Approval-gated / Research+doc-gen | Yes (special rate) |
| UC-5 | Revenue Management Analysis | Both (Template-led) | 1 | pms-api, code-sandbox, pdf_generator, email, slack, ota-scrape data | Research+doc-gen (daily briefing) | No |
| UC-6 | Staff Scheduling Optimization | Agent | 2 | pms-api, fb-reservation-api (new), hr-attendance-api (new), code-sandbox, whatsapp, slack | High-volume-repetitive (daily 22:00) | Yes (dept heads) |
| UC-7 | Loyalty Program Management | Agent | 1 | crm/loyalty-api (new), whatsapp, email, web_search, code-sandbox, pms-api | High-volume-repetitive (daily) | No |
| UC-8 | Travel Policy Compliance (corporate) | Agent | 2 | hr-api (new), corporate-booking-api (new), gds-api (new: Amadeus/Sabre), corporate-card-api (new), code-sandbox, slack, pdf_generator | Approval-gated | Yes (out-of-policy / >₹50k excess) |
| UC-9 | Cancellation Recovery Campaigns | Agent | 2 | pms-webhook (new), whatsapp, email, payment, channel-manager-api, web_search, code-sandbox, slack | Real-time-monitoring (event-driven) | No |
| UC-10 | Supplier Contract Management | Both | 1 | document_reader, web_search, e-procurement-api (new), pdf_generator, email, slack | Research+doc-gen | Yes (renewal mandate) |

## 3. Connectors Required
**Existing (Bucket 1):** whatsapp, email, sms, web_search (SearXNG — event detection, market benchmarks), weather_api, pdf_generator, document_reader, payment gateway, slack, code-sandbox, google_calendar, translation.
**New-API (Bucket 2):** PMS APIs (Opera Cloud / Hotelogix / eZee Absolute / IDS Next — the foundational dependency), Channel Manager API (SiteMinder / eZee / Hotelogix), Google My Business API, TripAdvisor API, CRM/loyalty system API, flight-tracking API, IXigo/Kayak forward-demand API, Uber/Meru transport API, F&B reservation/POS API, HR/time-attendance API, GDS (Amadeus/Sabre), corporate booking tool + corporate-card transaction API, e-procurement platform APIs (Jumbotail/Bigbasket Business).
**RPA-portal (Bucket 2):** OTA rate + review scraping (MakeMyTrip, Booking.com, Expedia, Goibibo, Agoda, OYO — ota-scrape RPA-new) for both pricing comp-set and review harvesting where APIs are absent. This is largely a scraping/RPA vertical, not government-portal RPA (contrast with Agri/Food).
**Perception/Multimodal:** none required — this vertical is data/analytics + RPA + NLP heavy, not vision.
**Build-cost callout.** PMS + Channel Manager integration is the entire vertical's gate (every UC touches PMS). OTA scraping is high-maintenance (anti-bot). GDS/corporate-card for UC-8 is a distinct, heavier integration serving a different buyer.

## 4. Knowledge Collections
Seed slugs:
- `hosp-comp-set-rates` — scraped OTA competitor rates, 30-day forward, per property comp-set.
- `hosp-demand-signals` — local events/conferences/weddings, Google Trends, flight-search proxies per city.
- `hosp-reservation-history` — pickup/pace/segmentation history for forecasting.
- `hosp-guest-profiles` — CRM/loyalty stay history, preferences, ancillary spend (per-tenant, PII).
- `hosp-banquet-menu` — F&B package pricing for group proposals.
- `hosp-review-sentiment` — review corpus + topic/sentiment trends per platform.
- `hosp-travel-policy` — per-corporate grade-based entitlement rules (UC-8).
- `hosp-supplier-contracts` — contract register: terms, renewal dates, SLA, escalation clauses.
**Ingestion recipe.** OTA rates/reviews via scheduled RPA (2-hourly/hourly) → structured append; demand signals via SearXNG + Trends daily; reservation/pace data via daily PMS extract; contracts + travel policy via document_reader at onboarding; guest profiles synced from CRM/PMS.

## 5. Guardrails & Compliance
- **Pricing HITL.** Rate changes >15% or on peak/event dates require Revenue Manager approval via Slack; smaller deltas auto-push. Enforce minimum-rate floors and rate-parity across channels (verify by re-scraping own listing post-push).
- **Review responses HITL** for ≤2-star before posting; 4–5-star auto-post after delay; respond in reviewer's language; no template-generic replies.
- **Group/contract HITL** for special rates below floor and for renewal negotiation mandates (target + walkaway).
- **Corporate travel (UC-8) HITL** for out-of-policy or >₹50k excess; cite specific policy clause + cost impact.
- **Staff scheduling HITL** = always (dept heads adjust for human factors); respect max-shift-hours, weekly rest, overtime caps.
- **PII + payments.** Guest data + saved payment methods (no-show fee auto-collection) → data residency India, AES-256, per-tenant RLS, audit trail. No-show fee only per booking terms.

## 6. Scale Pattern & Cost Drivers
- **Dominant pattern:** Real-time-monitoring (2-hourly pricing, hourly reviews, cancellation webhooks) + High-volume-repetitive (daily scheduling, daily loyalty segmentation, daily revenue briefing).
- **Cost drivers (ranked):** (1) OTA scraping infrastructure (anti-bot, proxies, per-property comp-set × 30 days); (2) code-sandbox compute for demand-forecast + optimisation models running every 2h per property; (3) LLM for review drafting + personalisation + multilingual responses; (4) WhatsApp guest/staff messaging; (5) PMS API polling.
- **Mitigations:** cache comp-set scrapes and share across nearby properties in same city; LLM-response cache for review-reply and loyalty message templates; incremental forecasting (only recompute changed dates); per-plan Celery queues (enterprise airline seat-level isolated); tool-cache OTA pulls with short TTL.

## 7. Decision & Phasing
- **Phase 1 (Property wedge, weeks 0–8):** UC-1 Dynamic Pricing + UC-3 Reviews + UC-5 Revenue Analysis. The RevPAR + reputation trio that justifies the whole subscription; requires PMS + OTA-scrape + Channel Manager.
- **Phase 2 (Growth):** UC-2 Personalization + UC-6 Scheduling + UC-7 Loyalty + UC-9 Cancellation Recovery + UC-4 Group Booking. Guest-experience + labour + revenue-recovery breadth.
- **Phase 3 (Enterprise / separate GTM):** UC-8 Corporate Travel Policy (distinct corporate buyer, GDS/card integration) + UC-10 Supplier Contracts + airline/OTA seat-level pricing.
- **Verdict:** Accept. Best pure-ROI narrative (RevPAR lift) and the "IDeaS at 1/10th cost" positioning is a sharp wedge for 50k+ unmanaged properties. Gate on PMS/Channel-Manager integration breadth and OTA-scrape resilience. Treat UC-8 as a separate product line.

## 8. KPIs
- RevPAR improvement 22–38% (pricing) / 18–28% (analytics); occupancy at premium pricing +18%.
- Review response rate 35%→100%; response time 3 days→<4h; TripAdvisor ranking +8–15 positions.
- Group inquiry conversion 28%→52%; attrition 45%→12%; sales capacity 3×.
- Overtime cost −32%; schedule prep 2h→10 min/day.
- Loyalty active-member rate 28%→51%; win-back success 22%.
- Cancellation recovery 22–31% of attempts; net recovery ₹18–35L/yr/100-room.
- Travel policy violation 28%→<5%; corporate travel savings ₹1.2–4.5cr/1,000 employees.
- Platform: OTA-scrape success rate, per-property cost vs subscription, forecast accuracy (pace vs actual), review-reply cache hit-rate.
