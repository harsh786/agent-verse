# Insurance (India + Global)
### *From first notice of loss to final settlement — autonomous, auditable, and always compliant*

---

## Executive Summary

India's insurance industry manages ₹10.4 lakh crore in total premium collections (FY2024), yet processes fewer than 35% of retail claims without human intervention, against a global best of 70%+. Underwriting still relies on manual data gathering that takes 3–14 days; meanwhile, insurance fraud costs the industry ₹45,000 crore annually. AgentVerse deploys specialised agents that automate the full lifecycle — from KYC/AML-compliant onboarding through claims triage, fraud scoring, IRDAI regulatory filing, and reinsurance data packing — cutting policy issuance from days to minutes and claims settlement from weeks to 48 hours for clean cases.

---

## Use Cases

---

### UC-1: Policy Underwriting Data Gathering & Scoring

**The Problem:** Life and health underwriters spend 60–70% of their time gathering data — medical records, financials, property inspections, motor inspection reports — before they can even start risk assessment. Average policy issuance takes 7–14 days; 18% of applications are abandoned due to friction. Each FTE underwriter costs ₹8–₹14 lakhs/year and can process only 4–6 complex cases per day.

**AgentVerse Solution:** An autonomous underwriting agent receives a completed proposal form, fans out to 12+ data sources simultaneously (CIBIL, MCA21, income tax portal, hospital networks, Carfax equivalent), compiles a structured risk dossier, applies scoring models via the code execution sandbox, and returns a decision recommendation with evidence citations — reducing data gathering from 5 days to under 4 hours.

**Agent Workflow:**
1. Proposal form submitted via API or portal; agent extracts applicant name, PAN, DOB, sum assured, product type.
2. Concurrent data pull: CIBIL score (API), MCA21 director search (RPA), IT return summary (TRACES API), bank statement analysis (PDF parser).
3. For life/health: order medical records from empanelled hospitals via Health Stack API; parse ICD codes from PDF discharge summaries.
4. For motor: query VAHAN database for registration, chassis, previous claims; check blacklisted VIN list.
5. For property: scrape municipal property records (RPA on municipal portal); validate address against satellite imagery metadata.
6. SearXNG search for adverse media: applicant name + company name against financial crime databases.
7. Code sandbox runs actuarial risk scoring model: outputs risk band (preferred/standard/substandard/decline) with confidence score.
8. Agent drafts risk assessment memo: data sources, findings, exceptions flagged, recommended sum assured, loading percentage.
9. HITL gate: underwriter reviews memo for cases scored "substandard" or above ₹50 lakh sum assured.
10. Auto-approve standard/preferred cases below threshold; generate policy schedule PDF.
11. Send policy document to customer via email; update policy admin system via API.
12. Archive all source documents to immutable audit trail for IRDAI inspection readiness.

**Tools Used:** CIBIL API, VAHAN API, TRACES API, MCA21 RPA, PDF parser, SearXNG, Code execution sandbox, Policy admin system API, HITL approval gate, Email/SMTP, PostgreSQL (audit)

**Revenue Model:** ₹350/policy data package for insurers; ₹8,00,000/month enterprise licence for 2,000+ policies/month volume

**ROI:** Underwriting cycle from 7 days to 4 hours; abandonment rate reduced from 18% to 6%; underwriter capacity up 3×; annual saving ₹2.4 crore per 10-underwriter team

**Target Customers:** Life insurance companies, health insurers, general insurance underwriters, digital insurance aggregators (Policybazaar, Acko, Digit)

---

### UC-2: Claims Intake, Triage & First Notice of Loss

**The Problem:** Claims registration in India takes 24–72 hours from FNOL to assignment; 40% of claims files are incomplete at intake, causing repeated back-and-forth that delays settlement by 8–15 days. Each claims intake coordinator handles 30–50 FNOL calls/day at ₹4–₹7 lakhs annual cost; overall cycle time is 21 days for motor claims versus the international benchmark of 7 days.

**AgentVerse Solution:** The agent handles multi-channel FNOL intake (email, web form, WhatsApp), extracts key claim data from unstructured text and attached photos/documents, validates policy coverage in real time, assigns an urgency triage score (hospitalisation, total loss, income interruption), and routes to the correct claims handler with a pre-populated file — ensuring handlers start working immediately instead of gathering basics.

**Agent Workflow:**
1. FNOL arrives via email (IMAP), WhatsApp webhook, or web form API.
2. Agent parses natural language description: claimant name, policy number, date of loss, peril type, estimated loss, location.
3. Query policy admin system: verify policy status (in-force/lapsed), coverage sections, sum insured, deductibles, previous claims.
4. If policy lapsed or peril excluded: auto-decline with specific reason; generate rejection letter PDF; send to claimant.
5. For valid claim: extract attachments — FIR copy, photo of damage, medical bills, receipts — using PDF parser + image analysis.
6. Assign triage score: life-threatening injury = Priority 1; total loss property = Priority 2; minor damage = Priority 3.
7. For Priority 1 (hospitalisation): immediately trigger cashless authorisation workflow and assign senior medical claims handler.
8. Pre-populate claims file in claims management system via API: FNOL details, policy data, triage score, extracted documents.
9. Assign to claims handler based on workload, specialisation, and SLA clock start.
10. Send acknowledgement to claimant: claim reference number, assigned handler contact, required documents checklist, expected timeline.
11. Schedule follow-up Celery tasks: T+3 days (document completeness check), T+7 days (survey report expected), T+15 days (settlement SLA).
12. Log complete intake record to audit trail with timestamps for each step.

**Tools Used:** IMAP, WhatsApp Business API, Policy admin system API, PDF parser, Image analysis connector, Claims management system API, HITL approval gate (exclusion decisions), Celery scheduler, Email/SMTP

**Revenue Model:** ₹180/FNOL processed; ₹12,00,000/month for insurers handling 5,000+ FNOLs/month

**ROI:** Intake cycle from 48 hours to 35 minutes; document completeness at intake from 60% to 91%; handler productivity up 45%; ₹1.8 crore saving per 20-handler claims team

**Target Customers:** Motor insurers, health insurers, property & casualty insurers, travel insurance providers

---

### UC-3: Fraud Pattern Detection & SIU Referral

**The Problem:** Insurance fraud in India costs ₹45,000 crore annually — approximately 8–10% of total claims paid. Soft fraud (inflated claims) represents 65% of incidents; hard fraud (staged accidents, false hospitalisation) the rest. Traditional rules-based detection catches only 35–40% of fraud; manual SIU investigators handle 40–60 cases each with inconsistent referral quality, leading to 22% false-positive rates.

**AgentVerse Solution:** The agent applies a multi-signal fraud detection pipeline — network analysis of claimant/repairer/hospital relationships, temporal pattern analysis, social media verification, and behavioural scoring — to every claim above a configurable threshold. It compiles a structured SIU referral package with ranked evidence, enabling investigators to focus on genuine fraud rather than paperwork.

**Agent Workflow:**
1. Claims management system webhook triggers agent for every new claim registration.
2. Agent queries claims history DB: claimant's prior claims across all policies for past 5 years (aggregated).
3. Network graph analysis (code sandbox): map claimant → repairer/hospital → agent → surveyor linkages; flag tightly clustered networks.
4. Temporal pattern check: multiple claims within 12 months, claim filed shortly after policy inception, claims near renewal date.
5. SearXNG adverse media search: claimant name, vehicle registration, hospital/repairer name against fraud alert databases and news.
6. Social media cross-check: search public posts for evidence contradicting claimed injury/loss (RPA on LinkedIn, Facebook, Twitter/X public profiles).
7. For motor claims: query VAHAN for vehicle encumbrance/hypothecation; cross-check claimed accident location via Google Maps API.
8. For health claims: verify hospital registration with ROHINI database; check doctor registration with MCI API.
9. Compute fraud probability score (0–100) using weighted ensemble model in code sandbox.
10. Score 0–40: clear for normal processing. Score 41–70: enhanced scrutiny flag to adjuster. Score 71–100: auto-generate SIU referral.
11. SIU referral package (PDF): claim summary, fraud signals found, evidence exhibits, recommended investigation actions.
12. SIU referral sent to investigator via email + logged in case management system; outcome fed back to model training pipeline.

**Tools Used:** Claims management system API, PostgreSQL (claims history), Code execution sandbox (graph analysis, ML scoring), SearXNG, Playwright RPA (social media), VAHAN API, ROHINI API, MCI API, Google Maps API, PDF generator, Email/SMTP

**Revenue Model:** ₹500/claim screened; ₹15,00,000/month enterprise unlimited; success-fee variant at 5% of fraud recovery

**ROI:** Fraud detection rate from 38% to 71%; false-positive rate reduced from 22% to 8%; ₹8–₹18 crore annual fraud prevented per mid-size insurer; investigator capacity up 2.5×

**Target Customers:** Motor insurers, health insurance TPAs, life insurance companies, crop insurance providers, reinsurers

---

### UC-4: Renewal Campaign Personalization

**The Problem:** Indian insurers see 35–55% policy lapse rates at first renewal, far above the mature-market benchmark of 12–18%. Generic renewal reminder campaigns achieve only 2–4% response rates. Each lapsed policy costs ₹8,000–₹45,000 in acquisition cost written off, plus lost lifetime value of ₹1.2–₹8 lakhs per customer.

**AgentVerse Solution:** The agent analyses each expiring policy holder's claims history, interactions, life-stage signals, and competitive context to craft a personalised renewal offer and communication — delivered at the optimal channel and time. It handles multi-touch sequences, captures inbound interest, and hands off to advisors only for complex cases requiring human consultation.

**Agent Workflow:**
1. Celery job runs daily: fetch all policies expiring in 45, 30, 21, 14, 7, and 3 days.
2. For each policy, build customer profile: claims history (frequency, severity, type), customer tenure, sum insured history, payment mode, NPS score if available.
3. Life-stage signal enrichment: SearXNG search for property purchase, new vehicle registration, marriage/birth announcements (where public) — potential cross-sell triggers.
4. Competitive intelligence: check current market rates for equivalent product from top-3 competitors via public rate calculators (RPA).
5. Personalise offer: for zero-claims customer, highlight no-claims bonus + loyalty discount; for frequent claimant, emphasise service quality and cashless network.
6. Select optimal channel (WhatsApp, email, SMS, advisor call) based on past engagement patterns in CRM.
7. T-45 days: informational warm-up message — reminder of upcoming expiry, highlight key coverage facts.
8. T-30 days: personalised renewal quote with discount applied; CTA to renew online.
9. T-14 days: urgency message + additional incentive (e.g., complimentary OPD voucher for health, extended warranty for motor).
10. T-7 days: schedule advisor callback if no renewal action taken; pre-brief advisor with customer profile and recommended talking points.
11. T-1 day: final digital reminder; auto-renew option if standing instruction exists.
12. Post-renewal: send welcome-back confirmation; trigger next year's renewal cycle entry in Celery.

**Tools Used:** Policy admin system API, CRM API, SearXNG, Playwright RPA (competitor rate scraping), WhatsApp Business API, Email/SMTP, SMS connector, Celery scheduler, PostgreSQL (engagement tracking)

**Revenue Model:** ₹400/renewed policy; ₹10,00,000/month enterprise licence targeting ₹25 crore ARR by Year 2

**ROI:** Renewal retention rate from 48% to 71%; ₹12 crore in preserved premiums per 1 lakh policy portfolio; campaign ROI of 840%

**Target Customers:** Life insurers, health insurers, motor insurers, corporate benefit brokers

---

### UC-5: Customer Onboarding KYC/AML

**The Problem:** RBI/IRDAI mandate KYC for all insurance policies above threshold amounts. Manual KYC processing takes 2–5 days and involves 4–6 manual steps. Failed/incomplete KYC causes 12–18% of applications to be rejected or delayed. AML screening errors expose insurers to regulatory penalties — IRDAI fined insurers ₹380 crore for compliance violations in FY2023.

**AgentVerse Solution:** The agent orchestrates the full KYC/AML pipeline from document submission to approval: OCR extracts identity data, government API verification confirms authenticity, AML screening runs against global and domestic watchlists, risk classification assigns the customer tier, and the entire decision is logged with audit evidence. The workflow completes in under 12 minutes for straightforward cases.

**Agent Workflow:**
1. Customer submits KYC documents (Aadhaar, PAN, address proof, photo) via mobile app or portal.
2. PDF/image parser performs OCR: extract name, DOB, ID number, address from each document.
3. Aadhaar Paperless e-KYC via UIDAI XML verification API: validates authenticity without storing biometric data.
4. PAN verification via Income Tax API: confirm name match (fuzzy matching for minor spelling differences).
5. Address proof verification: cross-check with postal PIN code database; flag mismatches.
6. Liveness check result from mobile app (if biometric KYC): fetch liveness score from provider API.
7. AML screening: screen full name + DOB + address against OFAC SDN list, UN sanctions, FATF, SEBI debarred list, PEP database.
8. Adverse media screening via SearXNG: name + ID against financial crime news sources.
9. Risk classification: assign CDD level — Low/Medium/High risk based on PEP status, country of origin, transaction profile.
10. HITL gate: High-risk customers routed to AML compliance officer for enhanced due diligence; Medium risk auto-approved with monitoring flag.
11. Generate KYC completion certificate (PDF); update customer record in policy admin system.
12. Archive all documents, API responses, and decisions in audit trail (7-year retention per IRDAI norms).

**Tools Used:** PDF parser, Image OCR, UIDAI API, Income Tax API, OFAC/UN sanctions API, SearXNG, Code sandbox (fuzzy matching), HITL approval gate, Policy admin system API, PostgreSQL (audit trail)

**Revenue Model:** ₹120/KYC processed; ₹6,00,000/month for 5,000+ KYCs/month; compliance reporting add-on ₹1,50,000/month

**ROI:** KYC cycle from 3 days to 12 minutes; rejection rate from 15% to 4%; compliance fine risk eliminated; ₹2.4 crore annual saving for insurer processing 50,000 KYCs/year

**Target Customers:** Insurance companies (direct + bancassurance), insurance web aggregators, NBFC-insurers

---

### UC-6: IRDAI Regulatory Filing Automation

**The Problem:** Each insurer must file 40+ periodic reports to IRDAI: NB returns, claims ratio analysis, solvency margin reports, rural/social sector obligation reports, actuarial valuations, and more. Manual preparation consumes 8–15 FTE per quarter; deadline misses attract ₹5–₹25 lakh penalties per filing. The 2023 IRDAI data analytics initiative added 15 new data submission requirements overnight.

**AgentVerse Solution:** The agent maintains a master filing calendar, extracts required data from source systems (policy admin, claims, finance, actuarial), applies IRDAI-specified calculations and validations, pre-populates the IRDAI Sarthi portal forms via RPA, and provides a human review checkpoint before formal submission — with complete audit evidence for each filed figure.

**Agent Workflow:**
1. Celery job monitors regulatory filing calendar (imported from IRDAI Sarthi portal schedule via RPA monthly).
2. T-15 days before deadline: agent triggers data extraction tasks for the relevant filing.
3. Extract premium data from policy admin system; claims data from claims management system; investment data from finance system.
4. Apply IRDAI calculation methodology (e.g., incurred claims ratio = net claims incurred / net earned premium × 100).
5. Cross-validate figures: premium reconciliation, claims triangle consistency, solvency margin computation per IRDAI Circular 1/2024.
6. Code sandbox runs actuarial projections required for life insurance policyholder liability reporting.
7. Compile draft filing in IRDAI-prescribed format (Excel/XML) with all schedules.
8. HITL gate: CFO/Appointed Actuary reviews draft filing via Slack approval workflow with line-item visibility.
9. Post-approval: RPA agent logs into IRDAI Sarthi portal, navigates to relevant return section, uploads/populates the report.
10. Capture submission reference number and portal confirmation screenshot.
11. Email submission confirmation with reference number to CFO, Compliance head, and Board Secretary.
12. Archive full audit package: source data extracts, calculation workings, draft, approved version, submission proof.

**Tools Used:** Policy admin system API, Claims management system API, Finance system MCP, Code execution sandbox, Playwright RPA (IRDAI Sarthi portal), HITL approval gate, Slack, Email/SMTP, PostgreSQL (audit), Celery scheduler

**Revenue Model:** ₹5,00,000/month for complete IRDAI filing automation; individual filing ₹50,000 per submission; penalty risk coverage advisory add-on

**ROI:** 12 FTE regulatory team reduced to 4; zero late-filing penalties (saving ₹75 lakh/year average); CFO review time cut from 3 days to 4 hours per filing cycle

**Target Customers:** All IRDAI-registered insurers (life, general, health, reinsurance), insurance brokers (IRDA broker returns)

---

### UC-7: Claims Adjudication Documentation

**The Problem:** Claims adjusters spend 55–65% of their time on documentation tasks: writing investigation summaries, drafting settlement letters, filling in legal releases, computing depreciation, and logging system entries. This leaves only 35–45% for actual adjudication judgement. A 100-adjuster team wastes ₹3–₹5 crore/year on documentation that a well-designed agent can handle.

**AgentVerse Solution:** The agent acts as an adjudication co-pilot — ingesting surveyor reports, medical records, and repair estimates, computing the admissible settlement amount per policy terms and applicable depreciation schedules, drafting the settlement communication in plain language, and pre-populating the claims system for adjuster final review and approval. Adjusters focus exclusively on judgement; the agent handles everything else.

**Agent Workflow:**
1. Claims handler triggers adjudication agent from claims management system interface.
2. Agent fetches complete claims file: FNOL, policy document, surveyor/investigation report, medical records, repair estimate, prior communication history.
3. PDF parser extracts damage items and repair estimates from surveyor report; maps to standard parts classification.
4. Apply depreciation schedule (per IRDAI motor tariff or company policy schedule) to each repair item in code sandbox.
5. For health claims: verify each procedure code against standard CGHS/hospital tariff; flag overbilled items.
6. Compute admissible settlement: sum insured − excess/deductible − depreciation − excluded items.
7. Cross-check against fraud score from UC-3; if high, attach fraud assessment note to adjudication file.
8. Draft settlement letter (PDF): policy number, claim reference, admitted amount, basis of computation, deductions with explanation, payment instructions.
9. For claims with disputes: draft alternative scenarios (with/without disputed items) for adjuster to choose from.
10. HITL gate: adjuster reviews computed settlement and draft letter; approves or modifies in claims system.
11. Post-approval: agent triggers payment instruction in finance system API; sends settlement letter to claimant via email/WhatsApp.
12. Log finalised settlement details to claims system; generate management accounting entries.

**Tools Used:** Claims management system API, PDF parser, Code execution sandbox (depreciation, tariff calculations), Finance system API, HITL approval gate, Email/SMTP, WhatsApp Business API, PostgreSQL

**Revenue Model:** ₹250/claim adjudicated; ₹8,00,000/month for insurers processing 3,000+ claims/month

**ROI:** Adjuster documentation time cut by 60%; settlement cycle from 21 days to 8 days; adjuster capacity up 2.4×; ₹2.8 crore annual saving per 100 adjusters

**Target Customers:** Motor TPAs, health insurance TPAs, property and casualty claim teams, self-insured large corporations

---

### UC-8: Cross-Sell/Upsell from Claims History

**The Problem:** A motor claim creates a natural conversation opportunity for life/health insurance; a health claim surfaces the need for critical illness cover. Yet 78% of Indian insurers never leverage claims interactions for cross-sell — they treat it as purely a cost centre. Companies that do cross-sell during claims realise 3× higher conversion rates versus cold outreach, but lack the system to systematically identify and act on these moments.

**AgentVerse Solution:** The agent analyses every settled claim for cross-sell triggers, profiles the customer's current portfolio to identify coverage gaps, generates a personalised product recommendation, and automatically briefs the assigned advisor with a conversation guide — or, for digital-first customers, sends a contextually relevant offer immediately post-settlement.

**Agent Workflow:**
1. Claims closure webhook triggers cross-sell analysis agent.
2. Agent fetches customer's complete insurance portfolio from policy admin system: all in-force and lapsed policies.
3. Coverage gap analysis: compare against life-stage-appropriate coverage benchmarks (age, income band, family size from KYC data).
4. Analyse claim type for product affinity signals: motor accident → personal accident + health; hospitalisation → critical illness + top-up health; fire → home content insurance.
5. SearXNG search (where applicable): check if customer recently purchased property/vehicle → home/motor insurance opportunity.
6. Compute cross-sell propensity score per product category (code sandbox model).
7. Fetch available product portfolio from product catalogue API; match best-fit products to identified gaps.
8. Generate personalised recommendation brief: customer name, existing portfolio summary, recommended product, reason for fit, suggested premium, expected benefit illustration.
9. Select channel: digital-first customer (app-active) → in-app notification + email; non-digital → advisor call brief.
10. For advisor: send call guide via CRM activity + email: customer summary, recommended pitch, objection handling notes.
11. For digital: send personalised email/WhatsApp with product highlight and quote link; track open/click.
12. Log cross-sell action in CRM; schedule follow-up task T+7 days; feed conversion outcome to propensity model.

**Tools Used:** Policy admin system API, CRM API, Code execution sandbox, SearXNG, Product catalogue API, WhatsApp Business API, Email/SMTP, Slack (advisor briefing), PostgreSQL, Celery scheduler

**Revenue Model:** ₹800/successful cross-sell policy issued; ₹5,00,000/month platform licence for advisor-assisted model

**ROI:** Cross-sell conversion rate 3× higher than cold outreach; average incremental premium ₹18,000 per converted customer; ₹6.5 crore incremental premium for insurer converting 5% of 35,000 annual settled claims

**Target Customers:** Composite insurers (life + general), bancassurance partners, insurance brokers, large corporate agents

---

### UC-9: Reinsurance Data Preparation

**The Problem:** Reinsurance bordereaux preparation is a quarterly nightmare for cession departments: extracting and transforming 50+ data fields per risk, mapping to reinsurer's treaty format, computing cession/retention splits, and reconciling premiums. Manual preparation takes 15–25 working days per treaty; errors in cession data expose insurers to coverage disputes worth ₹50–₹500 crore.

**AgentVerse Solution:** The agent extracts treaty-eligible risks from the policy system, applies treaty terms (quota share percentages, surplus lines, facultative eligibility, catastrophe XL layers), computes cession amounts, generates bordereaux in each reinsurer's required format, performs internal reconciliation, and delivers final files to reinsurers — compressing the cycle from 3 weeks to 3 days.

**Agent Workflow:**
1. Quarter-end trigger (Celery): agent extracts all in-force risks for the treaty period from policy admin system.
2. Apply treaty eligibility filters: product type, sum insured bands, geography, exclusion classes per treaty schedule.
3. For each eligible risk, compute retention and cession amounts per quota share percentage or surplus line factor.
4. Apply catastrophe XL layer calculations for property treaty: aggregate exposure by geographic zone (code sandbox).
5. Reconcile premium cession against UPR movements and mid-term endorsements.
6. Map data fields to each reinsurer's bordereaux format (treaties may have 5–12 different reinsurers with different formats).
7. Validate computed figures against prior quarter trends: flag anomalies > 15% variance for manual review.
8. HITL gate: cession manager reviews reconciliation summary and flags on Slack before file generation.
9. Generate bordereaux files (Excel/CSV per reinsurer format); generate internal cession accounting entries.
10. Deliver bordereaux to reinsurers: email with encrypted attachment (SMTP) or upload to reinsurer portal (RPA).
11. Monitor reinsurer acknowledgement receipts; flag non-responses after 5 working days.
12. Archive all cession calculations, source data, and acknowledgements in audit vault (8-year retention per treaty standard).

**Tools Used:** Policy admin system API, Code execution sandbox (treaty calculations, XL layers), HITL approval gate, Slack, Email/SMTP with encryption, Playwright RPA (reinsurer portals), PostgreSQL (audit vault), Celery scheduler, Excel generator

**Revenue Model:** ₹8,00,000/quarter per insurer; ₹25,00,000/year annual contract; consulting add-on for treaty format onboarding ₹3,00,000/treaty

**ROI:** Bordereaux cycle from 20 days to 3 days; errors eliminated (saving potential ₹5–₹50 crore coverage dispute exposure); cession team reduced from 8 to 3 FTE

**Target Customers:** General insurers (property, marine, engineering, motor), life insurers, reinsurance brokers (Aon, Marsh, Willis India operations)

---

### UC-10: Motor Inspection Scheduling & Management

**The Problem:** Motor insurance policies require pre-inspection before issuance (for used vehicles) and post-accident inspection for claims. Scheduling 2,000–5,000 inspections per day across a national network of 800+ surveyors is a logistics problem: mismatched geography, missed appointments, and 35–40% first-visit failure rates cost insurers ₹280–₹450 per failed inspection attempt.

**AgentVerse Solution:** The agent intelligently matches inspection requests to available surveyors using location, availability, and specialisation criteria, communicates directly with both the vehicle owner and surveyor, handles rescheduling requests conversationally, and processes submitted inspection reports — triggering policy issuance or claims advancement automatically on receipt of a clean report.

**Agent Workflow:**
1. Inspection request arrives (new policy pre-inspection or claims survey assignment) via system webhook.
2. Agent geocodes vehicle location; query surveyor availability database for surveyors within 15km radius.
3. Match surveyor: consider specialisation (passenger car vs. commercial vehicle vs. two-wheeler), workload, rating score, and travel distance.
4. Send appointment confirmation to vehicle owner: WhatsApp message with surveyor name, contact, date/time, inspection checklist.
5. Send assignment notification to surveyor: vehicle details, owner contact, location PIN, inspection form link.
6. T-2 hours before appointment: automated reminder to both owner and surveyor via WhatsApp.
7. If owner requests reschedule (WhatsApp reply parsing): check next available slot for same or alternate surveyor; confirm new appointment.
8. If surveyor marks no-show by owner: automatically trigger alternative slot within 24 hours; send apology + reschedule to owner.
9. Post-inspection: surveyor submits report via mobile app (form data + photos); agent validates completeness.
10. AI image analysis: detect photographic evidence quality (min. 8 photos required, all panels covered).
11. If report complete and no red flags: auto-trigger next step (policy issuance API call or claims advancement).
12. If inspection reveals damage/discrepancy: flag for underwriter/adjuster review with annotated photo set.

**Tools Used:** PostgreSQL (surveyor availability DB), Geocoding API, WhatsApp Business API, SMS connector, Mobile form API, Image analysis connector, Policy admin system API, Claims management system API, HITL approval gate, Celery scheduler

**Revenue Model:** ₹80/inspection scheduled; ₹4,00,000/month for insurers with 2,000+ daily inspections

**ROI:** First-visit success rate from 62% to 84%; inspection cycle from 4 days to 18 hours; surveyor utilisation from 65% to 88%; ₹1.8 crore annual saving per 2,000 daily inspections

**Target Customers:** Motor insurance companies, vehicle insurance aggregators, auto loan disbursement banks (requiring insurance as condition)

---

## Monetization Strategy

| Tier | Target | Price | Inclusions |
|------|--------|-------|------------|
| **Starter** | Digital insurtech startups, NBFC-insurers | ₹79,999/month | 3 agents, 1,000 transactions/month, KYC + FNOL + 1 additional workflow, IRDAI audit trail, email support |
| **Growth** | Mid-size life/general insurers, large brokers | ₹2,99,999/month | 8 agents, 10,000 transactions/month, full claims suite, fraud scoring, renewal campaigns, 2 regulatory filings, Slack integration, HITL gates, dedicated implementation manager |
| **Enterprise** | Top-20 Indian insurers, global reinsurers | ₹8,99,999/month | Unlimited agents and transactions, all 10 use-case modules, custom treaty/regulatory formats, on-prem deployment option, 99.95% SLA, SOC 2 Type II, IRDAI data privacy compliance, quarterly compliance audits included |

---

## Sample AgentManifest YAML

```yaml
agent_manifest:
  name: insurance-operations-suite
  version: "3.0.0"
  domain: insurance
  description: >
    Full-lifecycle insurance automation: underwriting data gathering,
    claims intake, fraud detection, IRDAI regulatory filing, and
    reinsurance bordereaux for India-domiciled insurers.

  agents:
    - id: underwriting-data-agent
      goal: "Gather and score underwriting data for new policy proposals within 4 hours"
      trigger: webhook
      event: "proposal.submitted"
      max_iterations: 15
      tools:
        - cibil_api
        - vahan_api
        - traces_api
        - mca21_rpa
        - pdf_parser
        - searxng
        - code_sandbox
        - smtp
        - policy_admin_api
      hitl:
        enabled: true
        threshold: "risk_band == 'substandard' OR sum_assured > 5000000"
        approvers: ["chief.underwriter@insurer.com"]

    - id: claims-intake-agent
      goal: "Process FNOL, triage claim, and route to appropriate handler within 35 minutes"
      trigger: multi_channel
      channels: [imap, whatsapp_webhook, web_form_api]
      max_iterations: 10
      tools:
        - imap
        - whatsapp_api
        - pdf_parser
        - image_analysis
        - policy_admin_api
        - claims_management_api
        - smtp

    - id: fraud-detection-agent
      goal: "Screen each new claim for fraud signals and generate SIU referral if warranted"
      trigger: webhook
      event: "claim.registered"
      max_iterations: 12
      tools:
        - postgresql
        - code_sandbox
        - searxng
        - playwright_rpa
        - vahan_api
        - rohini_api
        - pdf_generator
        - smtp

    - id: irdai-filing-agent
      goal: "Prepare and submit all periodic IRDAI regulatory returns on time"
      schedule: "0 9 1 * *"
      max_iterations: 20
      tools:
        - policy_admin_api
        - claims_api
        - finance_system_api
        - code_sandbox
        - playwright_rpa
        - slack
        - smtp
      hitl:
        enabled: true
        threshold: "always"
        approvers: ["cfo@insurer.com", "appointed.actuary@insurer.com"]

    - id: reinsurance-bordereaux-agent
      goal: "Generate and deliver quarterly reinsurance bordereaux to all treaty partners"
      schedule: "0 8 1 1,4,7,10 *"
      max_iterations: 25
      tools:
        - policy_admin_api
        - code_sandbox
        - excel_generator
        - smtp
        - playwright_rpa
        - postgresql
      hitl:
        enabled: true
        threshold: "always"
        approvers: ["cession.manager@insurer.com"]

  global_settings:
    audit_trail: true
    retention_years: 7
    data_residency: india
    encryption: AES-256
    pii_masking: true
    compliance_frameworks: [IRDAI, DPDP_Act_2023]
    alert_channel: "#insurance-ops"
```
