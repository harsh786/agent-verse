# ADR-D25: Telecom & ISP Vertical on AgentVerse
Status: Accepted · Date: 2026-07-04 · Source: docs/domains/25/use-cases.md

## 1. Market & Monetization
- **TAM:** India telecom ₹2.5 lakh crore, 1.17 billion wireless subscribers (2nd largest globally). Operators spend ₹18,000–35,000 crore/yr on network ops, field force, and customer service — **~40% consumed by manual reactive processes** agents can eliminate. Combined optimization opportunity for top-5 operators: **₹8,000–12,000 crore/yr**.
- **Buyer:** CTO / Circle CTO / Head of NOC / Regulatory Affairs Head / Revenue Assurance Director / CFO (economic buyers). Very small number of whale accounts (Airtel, Jio, Vodafone-Idea, BSNL, tower cos, regional ISPs) — enterprise/whale sales motion, not PLG.
- **Three tiers (from source):**
  - **Tier 1 — Operational Starter: ₹3 lakh/month.** Single-circle NOC automation, 12 mandatory TRAI monthly filings, complaint automation (≤50k/mo), top-10 revenue-assurance controls. 99% SLA, 3-wk onboarding.
  - **Tier 2 — Circle Operations Pro: ₹9 lakh/month.** Full-circle: churn prediction (≤20 lakh subs), SIM-swap fraud, fiber field-force (≤300 techs), all 85+ TRAI filings, roaming (≤50 partners). CSM, quarterly TRAI audit. 99.5% SLA.
  - **Tier 3 — National Telco OS: ₹45 lakh/month + success fee.** All circles; 5G rollout PM (unlimited sites), spectrum optimization, national RA (120 controls), site acquisition, roaming (400+ partners). Custom OSS/BSS integration. **Success fee: 8% of documented revenue recovered** (leakage/churn, audited quarterly). 99.9% SLA, 24/7.
- **Consumption model:** Per-circle subscription (₹8–25 lakh/circle depending on module) scaling to national, **plus success/outcome fees**: 8% of recovered revenue-leakage (UC-11), 8% of incremental retained-churn revenue (UC-2), 15% of prevented SIM-swap fraud value (UC-4, bank-shared), 5% of proven spectrum-efficiency gains (UC-9), per-site fees (₹25k/site UC-7), per-technician (₹400/tech/mo UC-12).
- **WTP: Very high, outcome-denominated.** Penalty avoidance (TRAI ₹50k–₹50 lakh/violation; a real ₹1.05 crore CNAP penalty in 2023), leakage recovery (₹400–1,200 crore/yr UC-11), churn revenue (₹300–600 crore/yr UC-2), capex avoidance (UC-5/UC-9). Deep pockets, defensible spend.
- **Time-to-first-revenue:** **Slow.** Deep OSS/BSS integration, security review, and 5G-scale POCs mean 6–12 month cycles. Wedge = TRAI Filing Automation (UC-3) — bounded, penalty-driven, clear compliance ROI, less integration than NOC/RA.
- **Monetization note:** This is the **highest-ceiling, slowest-landing** vertical. TRAI/DoT RPA portals are core (not optional) and require the biggest new-portal build. Success-fee alignment is strongest here (UC-11 leakage, UC-2 churn, UC-4 fraud) — but requires audited baselines. Whale concentration = high revenue-per-logo, high sales cost, high switching cost once integrated (strong moat).

## 2. Use Cases -> Product Mapping

| UC | Title | Ships as | Bucket | Connectors | Scale pattern | HITL? |
|----|-------|----------|--------|------------|---------------|-------|
| UC-1 | Network Outage Detection, RCA & Ticketing | Both | 2 | oss/nms-SNMP+kafka(new-API: Nokia/Ericsson/Huawei), topology-DB(new-API), pm-API(new-API), servicenow/jira, cmdb(new-API), workforce-mgmt(new-API), sms/call, slack, audit-trail | Real-time-monitoring | Yes (P1 escalation matrix) |
| UC-2 | Customer Churn Prediction & Retention | Both | 2 | bss/crm(new-API), kafka(new-API), churn-ML(code-exec), offer-mgmt(new-API), campaign-platform(new-API), sms, whatsapp(new-API), push(new-API), slack | High-volume-repetitive | Yes (ARPU >₹500 escalation) |
| UC-3 | TRAI Regulatory Filing Automation | Both | 2 | trai_portal-RPA (RPA-new), bss(new-API), oss/nms(new-API), billing-API(new-API), web_search (TRAI gazette), pdf_generator, digital-signature(new-API), slack, audit-trail, scheduler | Approval-gated | Yes (Reg Affairs Head sign-off) |
| UC-4 | SIM Swap Fraud Detection & Prevention | Agent | 2 | crm/provisioning(new-API), uidai-ekyc(new-API), fraud-ML(code-exec), coordinated-fraud-DB, video-kyc(new-API), sms, email, slack, bank-fraud-intel(new-API), audit-trail | Real-time-monitoring | Yes (blocked-request release) |
| UC-5 | Network Capacity Planning from Traffic Data | Both | 2 | oss-pm-API(new-API), topology-DB(new-API), census/gis(new-API), external-demand(new-API), code-exec (SARIMA), vendor-catalogue(new-API), sap-scm(new-API), slack, audit-trail | Research+doc-gen | Yes (capex >₹2 crore) |
| UC-6 | Customer Complaint Resolution (DoT DND/TRAI) | Both | 2 | complaint-crm(new-API), nlp-model, oss-data(new-API), billing-cdr(new-API), trai_dnd-registry(new-API), trai_sanchar_saathi(RPA-new/API), whatsapp(new-API), sms, slack, audit-trail | High-volume-repetitive | Yes (fraud/legal/enterprise escalation) |
| UC-7 | Tower Site Acquisition & Approval Mgmt | Both | 2 | gis/maps(new-API), municipality-RPA(RPA-new), dgca-RPA(RPA-new), dot_wpc-portal-RPA(RPA-new), state-revenue-RPA(RPA-new), web_search, pdf_generator, jira, email, audit-trail | Approval-gated | Yes (novel legal objections) |
| UC-8 | Roaming Settlement Reconciliation | Agent | 2 | sftp/api-file-ingest(new-API), tap/rap-CDR-parser(new-API GSMA TAP3), bilateral-agreement-DB, code-exec, gsma-doc-gen, email, finance-erp(new-API), audit-trail | High-volume-repetitive | Yes (disputes >₹50 lakh) |
| UC-9 | Spectrum Utilization Monitoring & Optimization | Both | 2 | oss/pm-API(new-API), radio-propagation-sim(code-exec), dot_wpc-portal-RPA(RPA-new), gis/maps(new-API), drive-test-analyzer(new-API), pdf_generator, audit-trail | Research+doc-gen | Yes (refarming >₹100 crore) |
| UC-10 | 5G Rollout Project Mgmt & Contractor Coordination | Both | 2 | primavera/ms-project(new-API), contractor-app(new-API), supply-chain-tracker(new-API), sap-scm(new-API), site-survey(new-API), slack, email, cmdb(new-API), audit-trail | Approval-gated | Yes (contractor penalty invocation) |
| UC-11 | Revenue Assurance & Leakage Detection | Agent | 2 | mediation-system(new-API), billing-API(new-API), cdr-recon-engine, interconnect-settlement-DB, fraud-ML(code-exec), sap-finance(new-API), slack, audit-trail | Real-time-monitoring | Yes (leakage >₹10 lakh/day) |
| UC-12 | Fiber Installation Scheduling & Field Force Opt | Both | 2 | fsm-API(new-API), gps/field-app(new-API), google_maps(new-API), route-opt (code-exec OR-Tools), warehouse-mgmt(new-API), whatsapp(new-API), slack, audit-trail | High-volume-repetitive | Yes (civil-work/NOC escalation) |

## 3. Connectors Required
- **Existing (Bucket-1, reuse):** slack, email/gmail, web_search, pdf_generator, postgresql, jira, code-exec, google_maps (if present, else new-API), datadog/elasticsearch (telemetry-adjacent). Bucket-1 coverage is thin here — telecom is deeply proprietary.
- **New-API (build, heavy — proprietary OSS/BSS is the dominant cost):** OSS/NMS (Nokia NetAct, Ericsson OSS, Huawei iManager) via SNMP + Kafka streams; BSS/CRM; billing/mediation systems; Performance Management APIs; network topology DB + CMDB; TAP3/RAP3 GSMA CDR parser (UC-8); offer/campaign management; UIDAI Aadhaar e-KYC; video-KYC; workforce/field-service management (FSM); SAP SCM/Finance; Primavera/MS Project; contractor + site-survey apps; census/GIS; drive-test analyzer; WhatsApp/SMS/push gateways; bank fraud-intelligence API. Mostly bespoke per-operator integration — the single largest connector-engineering investment across the four verticals.
- **RPA-portal (build — the regulatory core, non-negotiable):** **trai_portal** (85+ filings, live-changing templates, digital-signature submission — UC-3), **dot_wpc portal** (spectrum + tower-installation approvals — UC-7, UC-9), **TRAI Sanchar Saathi** (complaints — UC-6, RPA-new if no API), municipality / DGCA / state-revenue portals (site acquisition — UC-7). All RPA-new, gov-portal fragility, rate-limited, digital-cert auth. TRAI + DoT-WPC are the flagship portal builds this vertical hinges on.
- **Build-cost verdict:** Highest connector build of the four verticals. TRAI RPA (UC-3) + one OSS/BSS integration unlocks the wedge; full value requires deep, per-operator OSS/BSS + DoT/TRAI portal engineering. Custom-integration engineering is baked into Tier-3 pricing for a reason.

## 4. Knowledge Collections (seed slugs + ingestion recipe)
- `trai-regulatory-calendar` — 85+ filing types, deadlines, required fields, template versions, QoS benchmarks (CSSR/SDR/TCH-drop) — UC-3; continuous web_search of TRAI portal/gazette for changes.
- `network-fault-pattern-library` — historical alarm→root-cause correlations, hardware age/firmware failure signatures (UC-1).
- `fraud-signature-database` — 15 SIM-swap fraud indicators, coordinated-fraud patterns, store-level risk (UC-4).
- `churn-intervention-outcomes` — retention offer → acceptance/recharge outcomes for model feedback (UC-2).
- `bilateral-roaming-agreements` — IOT tariff matrices, GSMA BA.12 specs per partner (UC-8).
- `spectrum-plan` — per-band utilization baselines, interference matrices, refarming eligibility (UC-9).
- `site-approval-templates` — regulatory constraint checklists (30+), authority-query response templates (UC-7).
- `capacity-forecast-context` — festival/event calendars, census density, OTT traffic signals (UC-5).
- **Ingestion recipe:** OSS/PM/CDR streamed via Kafka (not embedded — code-exec computes metrics); regulatory calendars + templates via scheduled web_search + document_reader; TAP/RAP files SFTP-ingested + parsed; all filings/fraud-blocks/leakage-decisions → immutable 7-yr audit trail; **MSISDN hashed in all logs** (PII handling per manifest); 90-day subscriber-history context window.

## 5. Guardrails & Compliance
- **HITL gates (from manifest):** TRAI filing submission (Reg Affairs Head), high-value SIM-swap block release (Fraud Manager), revenue-leakage >₹10 lakh/day (RA Director), capex >₹2 crore (Network Planning Director), spectrum refarming >₹100 crore (Network Director + Regulatory), contractor-penalty invocation (Procurement). 2h approval timeout, escalate after 4h. Bounded-autonomous for scoring, triage, monitoring, reconciliation drafts.
- **Regulatory frameworks:** TRAI QoS Regulations 2023, DoT license conditions, TRAI CNAP/DND regulations, GSMA TAP3 specs. 100% on-time filing is a compliance SLA, not a nice-to-have (penalties ₹50k–₹50 lakh/violation).
- **PII & data classification:** confidential; MSISDN/Aadhaar hashed in logs; UIDAI e-KYC handling per UIDAI norms; India data residency; 7-yr audit retention.
- **Fraud/financial integrity:** SIM-swap block decisions and revenue-leakage patches are high-blast-radius — mandatory HITL + full risk-report audit. Bank fraud-intel sharing gated by partnership agreements.
- **RPA safety:** TRAI/DoT/DGCA portal RPA rate-limited (5/min per manifest), digital-sig creds in vault, idempotent submission (no double-filing), submission acknowledgements captured to compliance vault.
- **Cost controls (manifest):** max ₹8,000/day LLM spend, alert at ₹6,000, max 5 concurrent RPA sessions, Kafka lag alert at 10,000.

## 6. Scale Pattern & Cost Drivers
- **Dominant patterns:** Real-time-monitoring (UC-1 alarms, UC-4 fraud, UC-11 hourly CDR recon), High-volume-repetitive (UC-2 churn scoring on 20 lakh+ subs, UC-6 complaints, UC-8 CDR reconciliation on 2–5 crore CDRs/mo, UC-12 field jobs), Research+doc-gen (UC-3, UC-5, UC-9), Approval-gated (UC-7, UC-10).
- **Cost drivers:** (1) **CDR/telemetry throughput** — crore-scale CDR reconciliation and hourly PM ingestion are the massive compute load; done in **code-exec, not LLM** (parse/reconcile/model). (2) **Kafka stream processing** — always-on alarm + traffic streams. (3) **LLM tokens** — reserved for RCA write-ups, complaint NLP, filing formatting, report generation — kept bounded by daily-spend cap. (4) **RPA sessions** — TRAI/DoT portals (capped at 5 concurrent). (5) **ML model retraining** — churn (6h cycle), capacity (SARIMA), fraud.
- **Efficiency levers:** cheap-model executor (Haiku-class) for high-volume parallel steps (manifest: 8 parallel executor steps), strong model only for planner/verifier; noise-filter alarms before LLM (UC-1 45%→<8% FP); SemanticCache/LLM-response-cache on repeated complaint intents and filing formats; per-plan Celery queues; circuit breakers on OSS/BSS APIs; back-pressure on Kafka lag.

## 7. Decision & Phasing
- **Phase 1 (regulatory wedge):** UC-3 TRAI Filing Automation + UC-6 Complaint Resolution. Bounded scope, penalty-driven ROI, requires the TRAI portal RPA build + one BSS integration — lands the compliance beachhead.
- **Phase 2 (circle operations, success-fee):** UC-1 NOC/RCA, UC-2 Churn, UC-4 SIM-Swap Fraud, UC-11 Revenue Assurance. Deep OSS/BSS + Kafka integration; introduces outcome-fee models (churn/leakage/fraud) that need audited baselines.
- **Phase 3 (national / capex-scale):** UC-5 Capacity Planning, UC-7 Site Acquisition, UC-8 Roaming Settlement, UC-9 Spectrum Optimization, UC-10 5G Rollout PM, UC-12 Fiber Field Force. DoT-WPC/DGCA/municipality RPA, GSMA TAP3, project/SCM integration — the "National Telco OS" tier.
- **Rationale:** Regulatory filing is the least-integration, highest-compliance-clarity entry; circle ops build the OSS/BSS moat and unlock success fees; national capex/rollout use cases are the highest-ceiling, deepest-integration expansion — sequenced last because they demand the most engineering and earned trust.

## 8. KPIs
- **Product:** MTTR (4–6h → <45min), false-positive alarms (45%→<8%), churn reduction (15–25%), SIM-swap fraud reduction (85–90%), TRAI on-time filing (100%), complaint first-contact resolution (72%→94%), capex efficiency (+15–20%), roaming dispute cycle (90→14 days), leakage rate (2–4%→<0.5%), 5G site-delay reduction (−30%), fiber SLA breach (35–45%→<8%), tech utilization (60%→85%).
- **Business:** revenue per circle, national ARR, success-fee revenue (leakage/churn/fraud/spectrum), per-site (UC-7) and per-tech (UC-12) fees, logo count (whale accounts), switching-cost-driven retention.
- **Trust/quality:** HITL override rate on fraud blocks & filings, missed-compliance-deadline count (target 0), audit-trail completeness, verifier confidence on RCA/filing (≥0.92), false-positive fraud rate (legitimate-swap friction).
- **Efficiency:** LLM cost/day vs ₹8,000 cap, CDR reconciliation throughput, Kafka lag, RPA success rate + portal selector-break MTTR, code-exec runtime per churn/capacity/roaming cycle.
