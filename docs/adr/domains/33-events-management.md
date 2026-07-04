# ADR-D33: Events Management Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/33-events-management/use-cases.md

## 1. Market & Monetization
- **TAM:** India events ₹10,000 crore, 16% CAGR (fastest in Asia); corporate MICE ₹6,200 crore + weddings/social + sports/entertainment. 8 lakh professionals; a 500-pax event = 200+ vendor interactions, 5,000+ communications.
- **Buyer:** Event director / agency principal (delivery capacity), plus BD head (sponsorship) and finance head (budget/reconciliation). Corporate in-house events teams for townhalls/summits.
- **3 tiers (₹):** Starter ₹19,999/mo (boutique/in-house, <10 events/yr — venue, registration ≤500 pax, procurement ≤8 vendors, NPS, 1 budget). Professional ₹59,999/mo (mid-size, 10–50 events — unlimited events, 3,000 pax, speaker mgmt, marketing, sponsorship, on-site runbook, virtual/hybrid, 10 seats). Enterprise ₹1,99,999/mo + ₹15,000/event (large/MICE/government — permit compliance pan-India, GST reconciliation, white-label app, 24×7 event-day support, 50 seats).
- **Consumption model:** Flat SaaS + per-event overage (enterprise) + per-unit for discrete deliverables (₹15/attendee, ₹500/PO, ₹2,000/speaker) + 5% commission on sponsorship revenue generated (UC-5).
- **WTP:** High per-event because failure is publicly visible and penalties (₹15–50L on-day, ₹1–5cr on permit failure/cancellation) are catastrophic. Coordination is the value — willingness scales with event size.
- **Time-to-first-revenue:** Fast (2–4 weeks) for venue/vendor/registration/NPS/marketing — all Bucket-1 + payment gateway. Permit compliance (UC-12) RPA and virtual-platform integrations take 6–10 weeks.
- **Monetization note:** Register + vendor procurement is the operational wedge (every event needs it). Sponsorship commission (UC-5) and permit compliance (UC-12) are the margin/enterprise expanders; event-day runbook (UC-7) is the highest-retention "can't run an event without it" hook.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Venue Discovery, Comparison & Booking | Both | 1+2 | email, document_reader, vector-KB, browser-RPA (venue calendars), gmail, pdf_generator, DocuSign (New-API), audit | Research+doc-gen + Approval-gated | Yes (venue contract) |
| UC-2 | Speaker Invitation & Confirmation Mgmt | Both | 1 | email, document_reader, vector-KB, linkedin, browser-RPA, gmail, DocuSign, whatsapp, audit | Research+doc-gen + Approval-gated | Yes (committee, fee >₹2L) |
| UC-3 | Attendee Registration, Ticketing & Comms | Both | 1 | Eventbrite/Townscript (New-API), form-builder, whatsapp, razorpay, gmail, pdf_generator, reporting, audit | High-volume-repetitive | No |
| UC-4 | Vendor Procurement (RFQ to PO) | Both | 1 | document_reader, vector-KB, gmail, email, pdf_generator, zoho_books (New-API), audit | Approval-gated + Research+doc-gen | Yes (vendor selection) |
| UC-5 | Sponsorship Acquisition & Activation | Both | 1 | linkedin, web_search, browser-RPA, pdf_generator, gmail, DocuSign, whatsapp, slack, reporting, audit | Research+doc-gen + Approval-gated | Yes (proposal send) |
| UC-6 | Event Marketing Campaign Orchestration | Both | 1 | document_reader, CMS/wordpress, Buffer/Hootsuite (New-API), Google Ads (New-API), Meta Ads (New-API), mailchimp, analytics, gmail, web_search, slack, reporting | High-volume-repetitive + Real-time-monitoring | Yes (spend escalation) |
| UC-7 | On-Site Logistics & Runbook Execution | Agent | 1 | document_reader, whatsapp, slack, reporting, audit | Real-time-monitoring | Yes (schedule deviation >20min) |
| UC-8 | Budget Tracking & Financial Reconciliation | Agent | 1 | document_reader, zoho_books, slack, gmail, analytics, pdf_generator, audit | Real-time-monitoring + Approval-gated | Yes (overrun, invoice discrepancy) |
| UC-9 | Post-Event Survey & NPS Analysis | Both | 1 | whatsapp, email, form-builder, analytics, web_search, pdf_generator, audit | High-volume-repetitive + Research+doc-gen | Yes (report action items) |
| UC-10 | Media Coverage & PR Monitoring | Agent | 1+2 | web_search, media-monitoring (New-API), social APIs, news API (New-API), browser-RPA, slack, analytics, pdf_generator, audit | Real-time-monitoring | Yes (negative-PR response) |
| UC-11 | Virtual & Hybrid Event Platform Mgmt | Agent | 2 | virtual-platform (New-API: Zoom Events/Hopin/Airmeet), zoom, email, whatsapp, form-builder, slack, CMS, reporting, audit | Real-time-monitoring | No |
| UC-12 | Permit & Compliance (Police NOC, Fire, FSSAI) | Agent | 2 | document_reader, regulation-DB, pdf_generator, police-NOC + FSSAI + municipal + MHA portals (RPA-new), email, audit | Approval-gated + Real-time-monitoring | Yes (pending-approval escalation) |

## 3. Connectors Required
- **Existing (Bucket 1):** email/gmail, whatsapp, document_reader, web_search, pdf_generator, razorpay, mailchimp, linkedin, slack, salesforce/hubspot (CRM). Build cost ~0.
- **New-API:** Eventbrite/Townscript, DocuSign, zoho_books, Buffer/Hootsuite, Google Ads, Meta Ads, virtual-platform (Zoom Events/Hopin/Airmeet), zoom, media-monitoring, news API, form-builder (or build native). Build 2–5 days each; ad platforms (~1 week, OAuth + campaign schemas).
- **RPA-portal (New):** police NOC (Section 30 Police Act — per-state SHO portals), FSSAI food license, municipal corporation permissions (BMC/BBMP/etc.), fire safety NOC (state fire dept), MHA event clearance (international), traffic/RTO plan approval, venue booking-calendar scrape (UC-1), client/sponsor portals. Build ~1 week each; police/fire/municipal are per-jurisdiction (50+ cities) and brittle — the core Bucket-2 investment, and where physical-document dispatch fallback is needed (no online portal in many jurisdictions).
- **Compute/embedded:** vector-KB (venues, vendors, speakers, sponsors, regulations), analytics (NPS, sentiment, AVE, attribution).

## 4. Knowledge Collections
Seed slugs: `venue-database` (5,000+ India venues with capacity/AV/catering/ESG), `speaker-database` (10,000+ with topics/fees/history), `vendor-database` (rated by city/category), `sponsor-prospect-profiles`, `permit-matrix` (jurisdiction-specific, 50+ cities), `market-rate-benchmarks` (vendor pricing), `rfq-po-templates`, `runbook-task-graphs`.
Ingestion recipe: (1) scrape + maintain venue/vendor/speaker databases into pgvector, refresh availability via RPA; (2) build permit matrix keyed by (city, event-type, size, program: food/alcohol/pyro/international) with authority names, formats, timelines, fees; (3) seed vendor market-rate benchmarks for >20% variance flagging (UC-4); (4) ingest firm's past event runbooks as task-graph templates for UC-7.

## 5. Guardrails & Compliance
- **Permit criticality (UC-12):** missing one approval 48h before a 5,000-pax event = ₹1–5cr loss. 30/15/7-day advance alerts mandatory; HITL escalation to authority on delay; compile approved NOCs into event dossier.
- **Payment/PII:** attendee data consent required; PCI-DSS compliant payment handling (razorpay); 5-year (1825-day) retention for GST + contractual.
- **Event-day HITL (real-time):** schedule deviation >20min alerts event director (10-min SLA); negative-PR response needs comms-head approval (30-min SLA).
- **Financial HITL:** budget overrun >10%, invoice discrepancies, advance payment requests.
- **Ad-spend controls:** daily ad cap ₹25,000, escalation at 80% budget.
- **Hallucination risk:** low-to-moderate — mostly coordination/doc-gen. Vendor quote parsing and budget math should validate against source docs; permit form pre-fill needs human sign-off before submission.

## 6. Scale Pattern & Cost Drivers
- **Real-time-monitoring (defining, high-stakes):** event-day runbook (UC-7 — parallel tracks, WhatsApp broadcast, delay cascade calc), PR monitoring (UC-10), permit status polling (UC-12), budget alerts (UC-8), marketing velocity (UC-6). Bursty compute concentrated on event day — needs reliable low-latency broadcast, not high throughput.
- **High-volume-repetitive:** attendee registration/comms fan-out (UC-3, up to 5,000 pax), survey dispatch (UC-9).
- **Research+doc-gen:** venue/speaker/vendor/sponsor research + comparison matrices (UC-1/2/4/5), marketing content (UC-6).
- Primary cost driver: per-event LLM budget (~1,000 calls cap) dominated by research/doc-gen + event-day coordination burst. WhatsApp message volume on event day. RPA reliability for permits.

## 7. Decision & Phasing
- **Phase 1 (land):** UC-3 registration, UC-1 venue, UC-4 procurement, UC-9 NPS — Bucket-1 + razorpay, every event needs them, fast revenue.
- **Phase 2 (expand):** UC-2 speaker, UC-5 sponsorship (commission upside), UC-6 marketing, UC-7 event-day runbook (retention hook), UC-8 budget — moderate build, high stickiness.
- **Phase 3 (enterprise):** UC-12 permit compliance (police-NOC + FSSAI + municipal RPA, per-jurisdiction), UC-11 virtual/hybrid, UC-10 PR monitoring — heaviest integration, enterprise/government ACV.
- 12 UCs — **not a thin domain, no enrichment flag.** Mostly Bucket-1 + vendor RFQ; the police-NOC / FSSAI / municipal RPA suite (RPA-new, per-jurisdiction) is the primary and most brittle build — plan physical-dispatch fallback where portals are absent.

## 8. KPIs
- Venue research 35h → 4h (UC-1); speaker coordination 25h → 3h/speaker (UC-2).
- Registration staff 3 FTE → 0.5 FTE/event (UC-3); procurement 50h → 8h, 12% cost saving (UC-4).
- Sponsor activation compliance +40%, incremental sponsorship ₹15–50L/event (UC-5); registration conversion +30% (UC-6).
- On-site coordination failures −65%, zero schedule-penalty (UC-7); budget overrun −15%, reconciliation time −70% (UC-8).
- Survey response 20% → 58%, NPS +18pts (UC-9); media value captured 3–5× manual (UC-10).
- Virtual NPS +25pts (UC-11); zero permit failures (UC-12). Platform: event-day broadcast latency, per-event LLM cost, permit RPA success rate, HITL event-day SLA.
