# ADR-D30: Energy & Utilities Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/30-energy-utilities/use-cases.md

## 1. Market & Monetization
- **TAM:** ₹15 lakh crore energy sector; ₹7 lakh crore/year electricity bills across 700M consumers, 29 DISCOMs, 500+ IPPs, 200+ GW renewables; 22% AT&C losses and ₹6.3 lakh crore DISCOM debt make efficiency spend easy to justify.
- **Buyer:** DISCOM MD/CFO/Director-Commercial (loss, dispatch, meter), renewable developer Asset Manager + DFI/PE lender (performance, predictive maintenance), C&I energy head (open access), Regulatory/Legal Head (CERC/SERC), CPO/BEE-DC sustainability lead.
- **3 tiers (₹):** Tier 1 Efficiency ₹49,999/month (consultants/single plant <50 MW — audits, meter anomaly ≤50k meters, net metering, carbon ≤2 facilities, open access ≤5 MW); Tier 2 Operations ₹1,49,999/month (50–500 MW — asset monitoring ≤200 MW, predictive maint ≤50 transformers, DR ≤50 MW, T&D loss ≤500 DTs, CERC/SERC filing, merit-order dispatch, SCADA integration); Tier 3 Grid-Scale ₹4,99,999/month + usage (unlimited assets, grid fault isolation, CBAM, custom SCADA/OMS/DMS, 24×7 NOC, on-prem option).
- **Consumption model:** subscription + usage — per-meter/month (₹0.10), per-MW/year (₹15,000), per-transformer/year (₹20,000), per-petition, per-unit managed (open access ₹0.10–0.20), plus **verified-savings share** (5% dispatch, 10% loss reduction) and outcome fees (₹/outage avoided).
- **WTP:** Very high — 2–5% AT&C loss reduction = ₹150–1,500 crore/year for a mid-large DISCOM; dispatch optimisation ₹300–800 crore/year; 3–6% PR gain = ₹90–180 lakh/year per 100 MW. Savings dwarf software cost (15–40× ROI).
- **Time-to-first-revenue:** Moderate — Tier 1 doc/RPA modules (audit, net metering, carbon) live in weeks; SCADA-dependent Tier 2/3 need integration (1–3 months).
- **Monetization note:** Land with low-integration Tier 1 (audits, net metering, carbon, meter anomaly via SFTP), then attach high-value savings-share modules (dispatch, loss, DR) once SCADA/on-prem trust is established. Success-fee model aligns to DISCOM debt reduction narrative.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Smart Meter Analysis & Billing Anomaly Detection | Agent | 3 | smart meter AMI/SFTP (New-API), analytics, billing system (New-API), email, pdf_generator | Real-time-monitoring + High-volume | Yes (work orders) |
| UC-2 | DISCOM Power Purchase Optimization (Merit Order) | Agent | 3 | SCADA/EMS (New-API), IEX (New-API), contract DB (New-API), optimization, slack, pdf_generator | Real-time-monitoring | Yes (SLDC engineer) |
| UC-3 | Renewable Asset Performance (Solar/Wind) | Agent | 3 | OPC-UA/REST SCADA (New-API), weather API Solargis/NASA (New-API), analytics, email, field service (New-API), pdf_generator | Real-time-monitoring | No |
| UC-4 | Grid Fault Detection & Automatic Isolation | Agent | 3 | SCADA real-time (New-API), analytics, sms, whatsapp, field service, pdf_generator | Real-time-monitoring | Yes (isolation command) |
| UC-5 | Energy Audit Report Generation (BEE) | Both | 2 | gmail, document_reader (OCR), EMS (New-API), CMMS (New-API), ERP, analytics, pdf_generator, BEE portal (RPA-new) | Research+doc-gen | Yes (certified auditor) |
| UC-6 | CERC/SERC Regulatory Filing Automation | Agent | 2 | regulation DB (New-API), email, slack, SAP/Oracle ERP (New-API), SCADA, billing, pdf_generator, cerc_serc (RPA-new), document_reader | Approval-gated + Research+doc-gen | Yes (legal/reg head) |
| UC-7 | Carbon Credit Calculation & Reporting (CBAM/PAT/REC) | Both | 3 | EMS, ERP, web_search, regulation DB, carbon market API (New-API), pdf_generator, email | Research+doc-gen | Yes (verifier) |
| UC-8 | Demand Response Program Management | Agent | 3 | SCADA real-time, optimization, sms, email, API, smart meter (New-API), pdf_generator | Real-time-monitoring | Yes (incentive payment) |
| UC-9 | T&D Loss Reduction Analysis | Agent | 3 | SCADA, smart meter, billing, analytics, GIS (New-API), optimization, pdf_generator | Research+doc-gen + monitoring | Yes (engineering approval) |
| UC-10 | Generator & Transformer Predictive Maintenance | Agent | 3 | IED/RTU (New-API), SCADA, document_reader (OCR), analytics, CMMS (New-API), slack, pdf_generator | Real-time-monitoring | Yes (critical HI<40) |
| UC-11 | Energy Procurement for C&I (Open Access) | Agent | 2 | IEX (New-API), contract DB, tariff DB (New-API), SLDC open-access portal (RPA), email, pdf_generator | Real-time-monitoring + Approval-gated | Yes (rejections) |
| UC-12 | Net Metering & Solar Rooftop Subsidy Processing | Agent | 2 | whatsapp, document_reader, GIS, billing, DISCOM + PM Surya Ghar portals (RPA), email, slack, google_calendar, pdf_generator | Approval-gated + High-volume | Yes (minimal) |

## 3. Connectors Required
- **Existing (Bucket 1):** gmail/email, whatsapp, document_reader, web_search, pdf_generator, google_sheets/calendar, sms. Cost: ~0.
- **New-API (build — some High complexity due to industrial protocols):** SCADA/EMS via OPC-UA + REST, IEX (DAM/RTM), smart meter AMI head-end (API/SFTP), billing system, contract DB, weather (Solargis/NASA), GIS (ArcGIS), CMMS (Maximo), IED/RTU transformer monitors, carbon market API, tariff schedule DB, regulation DB, **pgcil/discom API (New-API)**, SAP/Oracle ERP, field service. Cost: OPC-UA/SCADA + IED/RTU are 20–40 dev-days (protocol + on-prem security); rest 8–15 dev-days.
- **RPA-portal (Bucket 2):** **cerc_serc (RPA-new)** CERC + 29 SERC e-filing portals, BEE filing portal, State SLDC open-access portals, DISCOM net-metering portals + PM Surya Ghar (saubhagya) national portal, CEA filing portal. Cost: 15–30 dev-days per portal family; 29 SERCs are individually variant — phase by state demand.

## 4. Knowledge Collections (seed slugs + ingestion recipe)
- `cerc-serc-filing-calendar` — all CERC/SERC obligations + deadlines per entity. Ingest: order-DB scrape → dated calendar.
- `ppa-contract-terms` — quantum, variable charge, must-run clauses. Ingest: contract DB export → structured records.
- `bee-sectoral-pat-benchmarks` — SEC targets by sector. Ingest: BEE PAT notifications.
- `grid-emission-factors` — CEA/MNRE factors + IPCC Tier-2 fuel factors. Ingest: web_search + regulation DB, versioned.
- `dga-iec60599-fault-ratios` — transformer fault-gas ratio thresholds. Ingest: IEC standard + fleet test history.
- `tariff-schedule-history` + `loss-hotspot-registry` — DISCOM tariff orders and feeder/DT loss history. Ingest: billing + SCADA + GIS join.

## 5. Guardrails & Compliance
- Frameworks: CERC/SERC regulatory codes, RDSS reporting, BEE PAT, GHG Protocol/ISO 14064, EU CBAM, Electricity Act 2003 (open access). Critical-infrastructure data classification.
- HITL mandatory gates: **grid isolation/switching command (5-min SLA, no auto-approve)**, next-day dispatch schedule (SLDC engineer, 2h), CERC/SERC filing (legal/reg head, 24h), DR incentive payment, T&D intervention plan, critical predictive-maintenance (HI<40), carbon inventory (third-party verifier), open-access rejections.
- Security/data: SCADA data = critical_infrastructure; AES-256 at rest, TLS 1.3 in transit, certificate auth for OPC-UA; **on-premise deployment option** for grid data; 10-year (3650-day) retention; TimescaleDB for telemetry.

## 6. Scale Pattern & Cost Drivers
- Predominantly **real-time-monitoring** (SCADA 5-sec polling, 15-min AMI, IEX price webhooks) with an approval-gated regulatory layer.
- Cost drivers: (1) high-frequency telemetry ingestion + time-series storage (TimescaleDB, telemetry_retention 365 days) is the dominant infra cost; (2) optimisation compute for merit-order/DR/loss (LP/MILP + Monte Carlo); (3) ML/analytics inference at meter/feeder/transformer scale (millions of meters); (4) RPA across 29 SERC portals. Manifest caps: max_daily_spend ₹15,000, LLM budget 500/analysis, alert at 80%.

## 7. Decision & Phasing
- **Phase 1 (low-integration Tier 1, fast revenue):** UC-5 energy audit (document_reader + BEE portal), UC-12 net metering (RPA + whatsapp, high volume), UC-7 carbon accounting (data + doc-gen). Minimal SCADA dependence; land consultants/single-plant customers.
- **Phase 2 (RPA + moderate integration — per domain note, CERC/SERC + meter anomaly):** UC-6 CERC/SERC filing (build cerc_serc RPA-new), UC-1 smart meter anomaly (SFTP/AMI API, real-time monitoring anchor), UC-11 C&I open access. These carry savings-share upside and regulatory stickiness.
- **Phase 3 (SCADA-heavy, on-prem, Grid-Scale tier):** UC-2 merit-order dispatch, UC-4 grid fault isolation, UC-3 renewable performance, UC-8 demand response, UC-9 T&D loss, UC-10 predictive maintenance. Require OPC-UA/IED integration, on-prem trust, and 24×7 NOC — highest-value, longest-cycle.

## 8. KPIs
- AT&C/T&D loss reduction: 2–5% (₹150–1,500 crore/year recovery); meter-anomaly revenue recovery ranked by ₹ impact.
- Merit-order dispatch savings: ₹300–800 crore/year for a 5,000 MW DISCOM.
- Renewable PR improvement 3–6%; O&M work orders closed by energy-loss rank.
- SAIDI reduction ~60%; fault detect <30s, isolation on 5-min HITL SLA.
- CERC/SERC on-time filing 100%; regulatory workload ↓ 75%.
- DR delivery rate 3–5× improvement; forced-outage reduction ~60% (predictive maint).
- Net-metering completion rate +55%; energy-audit prep cost ↓ 70%.
