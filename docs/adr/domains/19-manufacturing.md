# ADR-D19: Manufacturing & Industry 4.0 Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/19/use-cases.md

## 1. Market & Monetization
- **TAM:** India manufacturing contributes ₹35 lakh crore to GDP; 78% of plants still reactive-maintenance, costing ₹4.2 lakh crore/year in unplanned downtime; quality defects add ₹1.8 lakh crore in waste/recalls. Serviceable target = mid-market + large OEMs across auto, steel, cement, pharma, electronics, FMCG.
- **Buyer:** Plant Head / VP Manufacturing / Head of Quality / Maintenance Head (economic buyer); OT/IT integration lead is the technical gatekeeper.
- **Tiers (₹):**
  - Starter ₹59,999/mo — SME single plant; 3 agents (predictive maintenance + OEE + safety), 50 assets, 1 ERP integration.
  - Growth ₹2,49,999/mo — mid-market 2–5 plants; 8 agents, 250 assets, full 10-UC suite, MES+QMS+ERP integration, ISO audit prep, dedicated OT consultant.
  - Enterprise ₹7,99,999/mo — large OEMs, multi-site groups; unlimited agents/assets, on-prem/hybrid (OT network security), custom OPC-UA/MQTT, IATF/AS9100 support, 99.9% SLA.
- **Consumption model:** Primarily **per-asset / per-plant subscription** (monitoring is continuous, not per-transaction), with module add-ons (₹3L/mo quality, ₹2.5L/mo scheduling, ₹2.5L/mo energy) and success-fee options (15% of documented downtime/energy cost avoidance).
- **WTP:** High — downtime is ₹8,000–₹2.5L/hour; but WTP is gated on **proven OT integration** and pilot ROI. Success-fee pilots de-risk the sale.
- **Time-to-first-revenue:** Slow (4–8 months). Requires **New-API enterprise integrations** (MES/SCADA/SAP/OPC-UA/MQTT), OT network security review, and on-site pilot — not a PLG motion.
- **Monetization note:** This vertical **needs MES/SCADA/SAP enterprise integrations (New-API, not RPA) plus real-time monitoring (predictive maintenance, OEE, energy).** Sell via ROI-share pilots on predictive maintenance (fastest, most visceral ROI), then land-and-expand into quality, scheduling, and compliance. Enterprise deal cycles; low volume, high ACV.

## 2. Use Cases -> Product Mapping
| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Predictive Maintenance Alerting | Both | 3 | mqtt/opc-ua(New-API), code_sandbox(LSTM/RUL), cmms(New-API: SAP PM/Maximo), erp(New-API), slack, postgresql | Real-time-monitoring | Yes (P1 / RUL<48h) |
| UC-2 | Quality Control Defect RCA | Both | 3 | qms(New-API), mes(New-API), erp, bms(New-API), hr-system(New-API), code_sandbox(stats/causal), postgresql+pgvector, email, pdf_generator | Research+doc-gen | Yes (QE confirms root cause) |
| UC-3 | Production Schedule Optimization | Agent | 3 | erp, cmms, hr/mes, code_sandbox(OR-Tools MIP), slack, postgresql, mes | Approval-gated | Yes (production mgr approves) |
| UC-4 | Supplier Quality Monitoring & Scorecard | Both | 2/3 | qms, erp, document_reader(CoA), code_sandbox, email, postgresql, pdf_generator, slack | Research+doc-gen | Yes (disqualification) |
| UC-5 | Safety Incident Reporting & Compliance | Both | 2 | mobile-form(New-API), email, slack, pdf_generator, playwright-RPA(DGFASLI/state portal), code_sandbox, document-storage, postgresql | Approval-gated | Yes (EHS mgr, LTI+) |
| UC-6 | BOM Management & Costing | Agent | 3 | plm/pdm(New-API: Windchill), erp(BOM API), code_sandbox, playwright-RPA(vendor portals), slack, email, postgresql, pdf_generator | Approval-gated | Yes (Eng+Finance approve ECO) |
| UC-7 | Energy Consumption Optimization | Both | 3 | ems(New-API), smart-meter(New-API), bms, mes, code_sandbox, cmms, playwright-RPA(BEE portal), slack, email, pdf_generator | Real-time-monitoring | Yes (HVAC/production actions) |
| UC-8 | OEE Reporting & Analysis | Both | 3 | mes/plc(New-API), qms, code_sandbox, digital-signage(New-API), slack, email, pdf_generator, postgresql | Real-time-monitoring | No (reporting) |
| UC-9 | ISO 9001 / 14001 Audit Preparation | Both | 2 | dms(New-API), postgresql(CAPA), calibration-api(New-API), training-api(New-API), code_sandbox, pdf_generator, email, slack | Research+doc-gen | Yes (QMS/EMS mgr approves closure) |
| UC-10 | Supply Chain Disruption Response | Agent | 2/3 | web_search, erp, postgresql, email, playwright-RPA(supplier portals), code_sandbox, slack, mes | Real-time-monitoring | Yes (CPO reviews options matrix) |

## 3. Connectors Required
- **Existing (Bucket-1, ready):** email, whatsapp/slack, web_search, pdf_generator, document_reader (CoA/PDF parsing), postgresql+pgvector. Limited overlap — this vertical is integration-heavy.
- **New-API (build, HIGH cost — the critical path):** mqtt/opc-ua IoT gateway, mes/plc, scada, sap (ERP), cmms (SAP PM/Maximo), qms, plm/pdm (Windchill/SOLIDWORKS), bms, hr-system, ems + smart-meter, calibration-api, training-api, dms, digital-signage, mobile-form. These are **enterprise OT/IT integrations, not RPA** — protocol adapters (OPC-UA/MQTT), on-prem connectors, and per-vendor ERP APIs. Cost: high, 2–6 wks each, plus OT-network security hardening (isolated network mode).
- **RPA-portal (build):** DGFASLI / state Factory Inspectorate portal (safety filing, RPA-new), BEE PAT portal (energy, RPA-new), vendor/supplier portals (pricing, availability). Portal RPA is secondary to the OT integration effort.

## 4. Knowledge Collections
- Seed slugs: `iso-9001-clauses`, `iso-14001-clauses`, `iatf-16949-requirements`, `factories-act-provisions`, `8d-capa-templates`, `5why-fishbone-templates`, `asset-failure-modes`, `bee-pat-methodology`, `bom-costing-rules`, `defect-history` (pgvector), `supplier-scorecard-rubric`.
- Ingestion recipe: ingest ISO/IATF standard clause libraries + company procedures (DMS export) → chunk → embed to pgvector for audit gap-analysis; seed failure-mode/RUL reference tables and depreciation/routing/costing rules as structured postgresql rows; UC-2 defect history is a growing pgvector collection for similarity search against past resolved root causes; energy/OEE baselines stored as time-series in postgresql.

## 5. Guardrails & Compliance
- **Frameworks:** ISO 9001, ISO 14001, IATF 16949, ISO 45001 (safety), BIS, Factories Act, BEE PAT.
- **OT network security:** isolated/segmented network mode (Enterprise); read-mostly from OT, actuation (equipment standby, HVAC) strictly HITL-gated and rate-limited — **never autonomous control of safety-critical equipment.**
- **HITL** on: P1 maintenance scheduling, defect root-cause confirmation, production schedule approval, supplier disqualification, LTI+ safety notification & investigation, ECO release (Eng+Finance), energy actions affecting production, ISO closure actions.
- **Audit:** append-only trail; statutory safety filings within 24h (Factories Act); calibration/training/CAPA evidence retained for surveillance audits.
- **Data residency** India; AES-256.

## 6. Scale Pattern & Cost Drivers
- Dominant **real-time-monitoring** (predictive maintenance, OEE, energy, supply-chain signals — continuous sensor/counter ingestion), plus **research+doc-gen** (RCA, supplier scorecards, ISO binders) and **approval-gated** (scheduling, ECO, safety).
- Cost drivers: **not LLM-dominated** — heaviest cost is code_sandbox compute (LSTM/RUL time-series, OR-Tools MIP scheduling, statistical/causal RCA, energy regression) and high-frequency sensor ingestion throughput. LLM used mainly for narrative generation (8D reports, work orders, audit binders). On-prem/edge deployment adds infra cost. Sensor stream volume, not token count, drives unit economics.

## 7. Decision & Phasing
- **Phase 1 (land via ROI-share pilot):** Predictive Maintenance (UC-1) + OEE (UC-8) — most visceral ROI, single OPC-UA/MQTT + MES integration unlocks both; Safety (UC-5) rounds out the Starter bundle.
- **Phase 2 (expand):** Quality RCA (UC-2), Supplier scorecard (UC-4), ISO audit prep (UC-9) — leverage QMS/ERP integration already in place.
- **Phase 3 (advanced/enterprise):** Production scheduling (UC-3, OR-Tools), BOM/costing (UC-6, PLM), Energy optimization (UC-7, EMS/BEE), Supply-chain disruption (UC-10) — deepest integration and highest ACV.
- Decision: **Accepted.** Not a PLG vertical — enterprise field-sales + OT-consultant motion, gated on New-API MES/SCADA/SAP integrations. Sequence after Bucket-1 verticals prove the platform.

## 8. KPIs
- Unplanned downtime −68%; maintenance cost −23%; predictive-maintenance payback 4 months.
- Defect RCA 5d→6h; defect recurrence −71%; supplier PPM −52%.
- Schedule adherence 62%→87%; OEE 56%→71%.
- Energy cost −12–22%; ECO cycle 12d→6h; ISO audit prep 300h→40h, first-pass 60%→97%.
- Supply-chain disruption response 72h→4h; LTIFR −45%.
- Platform: sensor-ingestion throughput, code_sandbox compute cost per asset, false-positive alert rate, OT-network uptime.
