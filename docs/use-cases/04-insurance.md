# AgentVerse — Insurance

### *"Underwrite faster, settle smarter, comply automatically."*

---

## Executive Summary

The Indian insurance sector processes over 200 million policies and 30 million claims annually,
yet the industry still relies on manual data gathering, paper-based underwriting, and reactive
fraud detection that add cost, delay, and error at every step of the policy lifecycle. AgentVerse
deploys autonomous insurance agents that gather underwriting data from 40+ sources in minutes,
triage and route claims within seconds of first notice of loss, detect fraud patterns before
disbursement, and automate IRDAI compliance filings on rolling deadlines. With MCP connectors to
bureau databases, policy management systems, government portals, payment gateways, and regulator
APIs, AgentVerse compresses the end-to-end insurance value chain while maintaining the full
human-in-the-loop governance that regulators require.

---

## Use Cases

### UC-1: Policy Underwriting Data Gathering and Scoring

**The Problem**
Commercial and health underwriting requires data from 8–15 sources — bureau reports, financial
statements, property records, medical records, regulatory filings, and inspection reports. Manual
collection takes 3–10 business days per application and costs ₹2,000–₹8,000 in analyst time per
policy. Incomplete data at underwriting leads to adverse selection losses averaging 3–5% of
premium annually (Swiss Re Sigma, 2024).

**AgentVerse Solution**
The Underwriting Data Agent ingests the policy application, identifies all required data sources
based on product type and risk category, and autonomously gathers documents and structured data
from bureau APIs, government portals, financial databases, and the applicant's own systems via
consent-based connectors. It normalises all collected data into a structured risk profile, applies
the underwriting scoring model, and presents the underwriter with a complete, annotated data
package — ready for decision in minutes rather than days.

**Agent Workflow**
1. Ingest policy application from PMS or intake portal; extract applicant details, product type, and risk category
2. Identify required data sources per underwriting guidelines: bureau, financial, property, medical, regulatory
3. Trigger concurrent data pulls: CIBIL/Experian bureau report, MCA filings, CERSAI lien records, GST returns
4. Pull property data from government land records portal; request inspection report via field agent scheduling connector
5. Parse and normalise all collected documents via document parsing; extract structured risk attributes
6. Apply underwriting scoring model: calculate risk score, premium indication, and recommended terms
7. **HITL checkpoint:** underwriter reviews complete data package, model output, and overrides if required
8. Write underwriting decision and rationale to PMS; trigger policy issuance workflow or refer to technical underwriting

**Tools Used**
CIBIL MCP · Experian MCP · MCA21 API · CERSAI API · GST portal API · Document parsing ·
Aadhar/PAN verification API · Email MCP · Slack MCP · Code execution (risk scoring)

**Revenue Model (₹)**
- ₹60,000/month: up to 200 applications/month, standard data sources (bureau + government portals)
- ₹1,50,000/month: unlimited applications, full data source library, ML scoring integration
- Enterprise: ₹3,00,000+/month, custom underwriting models, white-label data portal, API access

**ROI**
Underwriting data collection time drops from 3–10 days to under 2 hours. Data completeness at
initial submission improves from 62% to 94%, reducing referral rates and adverse selection losses.
One mid-tier insurer reduced underwriting turnaround from 7 days to 4 hours for SME commercial
policies, capturing 22% more premium from applicants who previously lapsed before decision.

**Target Customers**
Life, health, and commercial insurers, reinsurance companies, corporate agents and brokers
handling complex placement, bancassurance partners with high application volumes.

---

### UC-2: Claims Intake, Triage, and First Notice of Loss

**The Problem**
Claims filed via call centre average 18–25 minutes of handling time for FNOL (First Notice of
Loss) and require 4–7 data entry steps across multiple systems (Majesco Claims Innovation Report,
2024). 35% of FNOL submissions are incomplete at intake, requiring costly follow-up calls that
add 3–7 days to cycle time and significantly increase early customer dissatisfaction scores.

**AgentVerse Solution**
The Claims Intake Agent handles FNOL across every inbound channel — WhatsApp, web portal, email,
or API — extracting all required claim data, validating policy coverage in real time, classifying
claim type and severity, routing to the appropriate handling unit, and sending the claimant an
acknowledgement with a case number and next-steps communication — all within 3 minutes of the
first message received.

**Agent Workflow**
1. Receive FNOL via any inbound channel: WhatsApp, web portal, email, or partner API
2. Extract structured claim data from unstructured narrative: date of loss, peril, location, and estimated loss amount
3. Validate policy coverage: pull active policy from PMS, verify coverage applicability, check exclusions and deductibles
4. Classify claim type (property, liability, health, motor) and severity tier using loss amount and coverage type
5. Check fraud indicators at intake: duplicate claims, FNOL timing anomalies, and known SIU watch-list matches
6. Route claim to appropriate handling unit: fast-track (express settlement), standard, complex, or SIU referral
7. **HITL checkpoint:** claims supervisor reviews SIU-flagged or high-value claims before proceeding with investigation
8. Send claimant acknowledgement with case reference, claims handler contact, document checklist, and expected timeline

**Tools Used**
WhatsApp Business MCP · Email MCP · Web portal API · PMS connector · Document parsing ·
Slack MCP (claims handler notification) · Code execution (fraud scoring at intake) · Audit trail

**Revenue Model (₹)**
- ₹40,000/month: up to 500 claims/month, standard intake channels (web + email)
- ₹1,00,000/month: unlimited claims, all intake channels including WhatsApp, fraud scoring at intake
- Enterprise: ₹2,50,000+/month, full FNOL automation with downstream workflow handoffs, SLA reporting

**ROI**
FNOL handling time drops from 18–25 minutes to under 3 minutes. FNOL completeness at intake
improves from 65% to 91%, reducing follow-up calls by 70% and cutting 3–5 days from average
cycle time. Customer satisfaction at FNOL stage improves by 28–35 NPS points.

**Target Customers**
General insurers (motor, property, health), life insurers handling rider claims, TPAs handling
group health claims, insurtech platforms with high-volume digital claims intake.

---

### UC-3: Fraud Pattern Detection and SIU Referral

**The Problem**
Insurance fraud costs the Indian industry an estimated ₹30,000–₹40,000 Cr annually (IIB India
Insurance Fraud Report, 2024). Traditional rule-based fraud detection catches only 15–25% of
fraudulent claims; the rest are detected post-settlement through audits, leaving no recovery
path. SIU investigators spend 60% of their time chasing legitimate false-positive referrals.

**AgentVerse Solution**
The Fraud Detection Agent analyses every claim across 200+ behavioural, relational, and temporal
signals — cross-referencing claimant history, provider reputation scores, network relationships
between parties, social media signals, and claim timing patterns. It generates an explainable
fraud score with the top 5 contributing factors for every claim, enabling adjusters to act on
intelligence rather than intuition. Confirmed high-risk claims are automatically referred to SIU
with a pre-built investigation dossier.

**Agent Workflow**
1. Ingest claim record with all collected FNOL data; enrich from internal claims history database
2. Pull claimant's prior claims across all products; calculate frequency, severity, and peril pattern anomalies
3. Score the service provider (hospital, garage, contractor): rating, prior fraud flags, network relationships with claimant
4. Run network analysis: check for shared addresses, phone numbers, or bank accounts across current and prior claims
5. Apply ML fraud scoring model: generate 0–100 fraud probability score with top contributing factors
6. Cross-reference against IIB fraud registry and internal SIU watch list for known bad actors
7. **HITL checkpoint:** fraud analyst reviews all claims with score above 65; confirms SIU referral or clears claim
8. For confirmed referrals: generate SIU dossier with evidence summary, network map, and investigation recommendations

**Tools Used**
IIB fraud registry API · Internal claims database · Network analysis (code execution: NetworkX) ·
Social media search · Document parsing · Slack MCP (SIU alert) · Code execution (ML scoring) · Audit trail

**Revenue Model (₹)**
- ₹80,000/month: up to 1,000 claims/month analysed, standard signal library
- ₹2,00,000/month: unlimited claims, network analysis, IIB integration, SIU dossier generation
- Contingency: 5–8% of fraud recovery amounts identified through AgentVerse referrals

**ROI**
Fraud detection rate improves from 18% to 52% of fraudulent claims identified pre-settlement.
SIU false-positive rate drops from 55% to under 20% with explainable scoring. One insurer
processing 3,000 health claims per month recovered ₹4.2Cr in avoided fraudulent payments in the
first year.

**Target Customers**
General and health insurers, TPAs managing group health schemes, reinsurers seeking improved
cedant fraud management, IRDAI-regulated entities under anti-fraud mandate.

---

### UC-4: Renewal Campaign Personalization

**The Problem**
Industry-wide policy renewal rates average 68–72% for non-life insurance in India (IRDAI Annual
Report, 2024). A 5-percentage-point improvement in renewal rate directly adds 7–10% to annual
premium income without acquisition cost. Generic renewal reminder SMS campaigns achieve 2–4%
incremental conversion over base renewal; personalised, value-anchored communications achieve
12–18%.

**AgentVerse Solution**
The Renewal Campaign Agent analyses each expiring policy 60, 30, 15, and 7 days before renewal,
personalises the renewal offer based on the customer's claims history, coverage gaps, life events,
and competitive pricing intelligence, and orchestrates a multi-touch communication sequence across
the customer's preferred channels. It identifies high-lapse-risk customers for priority human
outreach and optimises the timing and message of every touchpoint.

**Agent Workflow**
1. Pull all policies expiring in the next 60 days from PMS; segment by product, customer lifetime value, and lapse risk
2. Analyse each customer's claims history, coverage gaps, and life event signals (address change, new vehicle, new loan)
3. Research competitive premium rates for the same coverage profile; calculate retention value vs replacement cost
4. Generate personalised renewal offer: premium with applicable loyalty discount, recommended coverage enhancement
5. Create multi-touch communication sequence: email (60 days) → WhatsApp (30 days) → SMS (15 days) → call flag (7 days)
6. **HITL checkpoint:** for high-value policies (>₹50,000 premium), relationship manager reviews offer before dispatch
7. Execute communication sequence via email, WhatsApp, and SMS MCP connectors; track open and click rates
8. Flag non-renewing high-value customers for priority outbound call by agent; log all touches to CRM

**Tools Used**
PMS connector · CRM MCP · Email MCP · WhatsApp Business MCP · SMS gateway MCP ·
Web search (competitive pricing research) · Code execution (lapse risk scoring) · Slack MCP

**Revenue Model (₹)**
- ₹35,000/month: up to 2,000 renewing policies/month, standard 3-touch sequence
- ₹90,000/month: unlimited policies, personalised offers, lapse risk scoring, competitive intelligence
- Enterprise: ₹2,00,000+/month, multi-product orchestration, agent workload routing, renewal analytics dashboard

**ROI**
Renewal rates improve by 4–8 percentage points above base. For an insurer with 10,000 annual
renewals averaging ₹15,000 premium, a 6-point improvement retains 600 additional policies — adding
₹90L in premium at near-zero incremental acquisition cost.

**Target Customers**
Non-life insurers (motor, property, health), life insurers managing annual premium policies,
agency-led distribution models seeking to supplement agent outreach with automated digital nudges.

---

### UC-5: Customer Onboarding KYC/AML

**The Problem**
Insurance KYC/AML onboarding is a regulatory requirement under PMLA and IRDAI KYC guidelines.
Manual KYC verification takes 1–3 business days and introduces 15–22% of customers to a friction
point that triggers abandonment before policy issuance. Incomplete or poorly documented KYC also
represents the insurer's largest single source of regulatory examination findings.

**AgentVerse Solution**
The KYC/AML Onboarding Agent orchestrates the full customer identity verification journey —
triggering Aadhaar/PAN verification, pulling CIBIL AML screening, checking PEP/sanction lists,
analysing the customer's risk profile, and collecting, validating, and storing all required
documents — within a single automated workflow that completes in under 15 minutes for clean
customers and routes complex cases to human review with a pre-populated risk summary.

**Agent Workflow**
1. Receive customer application with consent for digital KYC; extract PAN, Aadhaar, and other identity fields
2. Trigger Aadhaar XML-based offline verification via UIDAI API; validate PAN via Income Tax portal API
3. Pull AML screening from CIBIL AML Watch and internal watch list; check OFAC, UN, and EU sanction lists
4. Classify customer PEP status and source-of-funds risk tier based on occupation, income, and geography
5. Collect required KYC documents via digital upload; validate document authenticity via OCR and forensic checks
6. Calculate overall KYC risk score: identity confidence × AML screening × PEP status × product risk weight
7. **HITL checkpoint:** compliance officer reviews all high-risk customers before KYC is marked as approved
8. Store verified KYC record in central KYC registry (CKYC); issue policy; log complete KYC trail to audit system

**Tools Used**
UIDAI Aadhaar API · Income Tax PAN API · CKYC registry API · CIBIL AML Watch MCP ·
OFAC/UN sanction list API · Document parsing (OCR + forensic) · Email MCP · Audit trail

**Revenue Model (₹)**
- ₹30,000/month: up to 500 KYC completions/month, standard verification (PAN + Aadhaar + AML)
- ₹80,000/month: unlimited completions, full document collection, CKYC submission, risk scoring
- Enterprise: ₹2,00,000+/month, custom risk models, regulator-ready audit pack, API for partner integration

**ROI**
KYC completion time drops from 1–3 days to under 15 minutes for clean customers. Customer
abandonment at KYC stage falls from 15–22% to under 4%. Regulatory findings at KYC examination
eliminated due to complete, consistent documentation maintained in the audit trail.

**Target Customers**
Life, health, and general insurers, insurance brokers and corporate agents, bancassurance
partners under IRDAI KYC compliance obligations.

---

### UC-6: Regulatory Filing Automation (IRDAI Returns)

**The Problem**
IRDAI requires insurers to file 40+ periodic returns spanning financial, actuarial, grievance, and
business data. Missed or erroneous filings attract penalties of ₹5–25L per instance and regulatory
censure that can restrict business activities. Each return requires data extraction from 5–12
internal systems, manual reconciliation, and specialist preparation — consuming 15–25 person-days
per quarter across finance, actuarial, and compliance teams.

**AgentVerse Solution**
The Regulatory Filing Agent maintains a rolling filing calendar for all IRDAI, IRDAI Life, and
state regulator returns. It pulls required data from source systems automatically, performs
pre-defined reconciliations to catch discrepancies before submission, drafts each return in the
mandated format, and routes it for compliance officer sign-off with a complete supporting data
package — ensuring every filing is submitted accurately and on time, with a complete audit trail.

**Agent Workflow**
1. Maintain IRDAI filing calendar; trigger preparation workflow 10 business days before each return deadline
2. Pull required data from source systems via MCP: PMS, claims system, finance ERP, actuarial model outputs
3. Run automated reconciliations: premium vs accounting, claims paid vs reserves, portfolio composition checks
4. Flag discrepancies above tolerance for human investigation; halt filing preparation until resolved
5. Populate return template in IRDAI-mandated format; generate supporting schedules and notes
6. **HITL checkpoint:** Chief Compliance Officer reviews completed return and supporting data before submission
7. Submit return to IRDAI portal via API connector; capture submission acknowledgement and timestamp
8. Log filing record to compliance calendar; archive supporting data package; alert on any regulator queries received

**Tools Used**
PMS connector · Claims system MCP · Finance ERP MCP · IRDAI portal API · Document generation ·
Email MCP · Slack MCP · Code execution (reconciliation logic) · Audit trail

**Revenue Model (₹)**
- ₹75,000/month: up to 10 periodic returns per quarter, standard data sources
- ₹1,75,000/month: all IRDAI returns, automated reconciliation, compliance calendar management
- Enterprise: ₹3,50,000+/month, state regulator filings, actuarial return preparation, IRDA audit support

**ROI**
Compliance team effort on filing preparation falls from 15–25 person-days per quarter to 3–5
days of review. Zero late or erroneous filing penalties — avoiding ₹5–25L per incident. One
insurer recovered 18 person-days per quarter which were redeployed to regulatory strategy and
product development.

**Target Customers**
All IRDAI-regulated insurers and reinsurers, insurance holding companies, and foreign reinsurance
branches operating in India.

---

### UC-7: Claims Adjudication Support and Documentation

**The Problem**
Claims adjudication is the most labour-intensive phase of the claims lifecycle. Adjusters spend
40–60% of their time gathering documents, interpreting policy wordings, cross-referencing coverage
terms, and preparing decision documentation — leaving only 40–60% for the actual judgment work
(Accenture Claims Transformation Survey, 2024). Inconsistent documentation is the primary driver
of successful policyholder disputes.

**AgentVerse Solution**
The Claims Adjudication Agent acts as a knowledgeable assistant to the claims adjuster: it
automatically collects all required evidence documents from policyholders and third parties,
interprets the relevant policy clauses against the specific loss scenario, calculates the payable
amount under all applicable coverage heads, and drafts the final adjudication letter — leaving
the adjuster to review, refine, and approve rather than create from scratch.

**Agent Workflow**
1. Receive assigned claim from claims system; load policy wording, coverage schedule, and FNOL details
2. Generate document checklist per loss type; send automated collection requests to claimant and third parties via email/WhatsApp
3. Track document receipt; send reminders for outstanding items; escalate to adjuster after 3 follow-up attempts
4. Parse all received documents: medical reports, repair estimates, invoices, and survey reports via document parsing
5. Interpret policy clauses relevant to the loss scenario; identify applicable coverages, exclusions, and sub-limits
6. Calculate gross loss, deductible, applicable limits, and recoverable amount; prepare coverage analysis memo
7. **HITL checkpoint:** adjuster reviews coverage analysis, document sufficiency, and approves or modifies loss assessment
8. Draft adjudication letter: coverage decision, payable amount, any reservations of rights, and payment timeline

**Tools Used**
Claims system MCP · PMS connector · Email MCP · WhatsApp Business MCP · Document parsing ·
OpenAI (policy clause interpretation) · Code execution (loss calculation) · Document generation (adjudication letter)

**Revenue Model (₹)**
- ₹50,000/month: up to 300 claims/month, document collection + policy interpretation
- ₹1,20,000/month: unlimited claims, full adjudication support including loss calculation and letter drafting
- Enterprise: ₹2,50,000+/month, custom policy wording libraries, dispute management support, audit analytics

**ROI**
Adjuster productive time (on actual decision-making) increases from 40–60% to 80–85% of working
hours. Average claims handling time drops from 22 days to 11 days. Customer disputes over
adjudication documentation fall by 55% due to consistently structured decision letters.

**Target Customers**
General insurers (property, casualty, motor), life insurers handling death and disability claims,
TPAs managing high-volume health claims, loss adjusting firms.

---

### UC-8: Cross-Sell/Upsell Identification from Claims History

**The Problem**
Claims events are high-intent signals for insurance need — a motor claim reveals the owner has
only third-party cover; a health claim reveals a policy with inadequate sum insured; a home
contents claim reveals the absence of a comprehensive home insurance policy. Yet 78% of insurers
do not systematically mine claims data for cross-sell signals (EY Insurance Consumer Survey, 2024).

**AgentVerse Solution**
The Cross-Sell Intelligence Agent continuously analyses settled claims to identify coverage gaps
and life event signals, scores each customer by product propensity and offer timing, and routes
personalised cross-sell recommendations to the customer's relationship manager or triggers an
automated digital offer via the preferred communication channel — striking while the policyholder's
risk awareness is highest.

**Agent Workflow**
1. Monitor claims settlements feed from claims system; trigger cross-sell analysis within 48 hours of settlement
2. Analyse coverage profile: identify products the customer does not hold that are relevant to the loss experienced
3. Check life event signals from claims data: new address (home product), new vehicle endorsement (motor upgrade)
4. Score cross-sell propensity: product affinity × recent loss salience × customer lifetime value × channel preference
5. Generate personalised offer: product recommendation, premium indication, and narrative anchored to the recent claim
6. Route offer to relationship manager via CRM task with talking points for personal outreach call
7. **HITL checkpoint:** for high-value customers, RM reviews and personalises the offer before any outbound contact
8. For digital-first customers, trigger automated WhatsApp/email offer; track response and report conversion rate

**Tools Used**
Claims system MCP · CRM MCP · PMS connector · WhatsApp Business MCP · Email MCP ·
Code execution (propensity scoring) · OpenAI (personalised offer narrative) · Slack MCP

**Revenue Model (₹)**
- ₹30,000/month: up to 500 claims analysed/month, standard signal library, CRM task creation
- ₹75,000/month: unlimited claims, propensity scoring, automated digital offer orchestration
- Contingency: 3–5% of new premium generated from cross-sell recommendations

**ROI**
Claims-triggered cross-sell conversion rates average 18–24% (vs 3–5% for cold campaigns) due
to high post-claim risk salience. One motor insurer generated ₹1.8Cr in new health premium in
one year from claims-triggered cross-sell — at zero additional acquisition cost.

**Target Customers**
Multi-line insurers, bancassurance channels with diverse product portfolios, insurance groups
seeking to maximise policyholder lifetime value.

---

### UC-9: Reinsurance Data Preparation

**The Problem**
Quarterly reinsurance bordereau preparation consumes 5–10 person-days per quarter per treaty, with
teams manually extracting, transforming, and validating policy and claims data from internal systems
to match reinsurer-specific submission formats. Errors in bordereau submissions cause reinsurance
recoveries to be delayed by 30–90 days, creating significant working capital pressure.

**AgentVerse Solution**
The Reinsurance Data Agent maintains treaty-specific submission templates and extraction logic for
every active reinsurance arrangement. It pulls policy and claims data from the core systems on a
scheduled basis, applies treaty-specific data transformations, runs automated validation checks
against the reinsurer's acceptance criteria, and generates the final bordereau ready for compliance
review and submission — reducing preparation from days to hours.

**Agent Workflow**
1. Pull active treaty schedule from reinsurance system; identify bordereau due dates and submission requirements
2. Extract policy-level and claims-level data from PMS and claims system per treaty's cession criteria
3. Apply treaty-specific transformations: currency conversion, deductible netting, risk classification mapping
4. Run automated validation: completeness checks, value reasonableness flags, cross-totals reconciliation
5. Flag anomalies: unusual loss ratios, large single risks, and aggregate bordereau movements for review
6. Prepare bordereau in reinsurer-mandated format (Excel, EDI, or web portal upload format)
7. **HITL checkpoint:** reinsurance manager reviews validated bordereau and approves before submission
8. Submit to reinsurer via email or portal connector; track acknowledgement; log submission record for audit trail

**Tools Used**
PMS connector · Claims system MCP · Reinsurance system MCP · Email MCP ·
Code execution (data transformation, reconciliation) · Document generation (bordereau) · Audit trail

**Revenue Model (₹)**
- ₹50,000/month: up to 5 treaties, standard bordereau formats (Excel/EDI)
- ₹1,20,000/month: unlimited treaties, automated validation, portal submission connectors
- Enterprise: ₹2,50,000+/month, multi-currency support, loss development triangulation, reinsurer portal APIs

**ROI**
Bordereau preparation time drops from 5–10 person-days to 4–8 hours per treaty per quarter.
Submission error rate falls from 8–12% to under 0.5%, eliminating 30–90 day recovery delays.
Finance teams recover ₹5–20L per quarter in reinsurance recoveries previously delayed by
submission errors.

**Target Customers**
Non-life and life insurers with proportional and non-proportional reinsurance treaties, reinsurance
brokers preparing cedant data, reinsurers receiving facultative bordereau.

---

### UC-10: Motor Inspection Scheduling and Report Generation

**The Problem**
Pre-insurance motor inspections are required for vehicles over 3 years old or with prior claims.
Coordination between the customer, inspection agency, surveyor, and underwriter averages 5–7 days
and 4–6 manual touchpoints per inspection. Inspection report quality is inconsistent, leading to
disputes and underwriting errors. 30% of motor proposal conversions are lost during the inspection
scheduling friction period.

**AgentVerse Solution**
The Motor Inspection Agent orchestrates the complete inspection lifecycle — scheduling the
appointment at the customer's preferred time and location, briefing the surveyor with the vehicle
and coverage details, receiving and parsing the inspection report, validating completeness, and
uploading the structured data directly to the underwriting system for policy issuance — compressing
the process from 5–7 days to same-day for most customers.

**Agent Workflow**
1. Receive motor proposal from PMS or direct sales portal; identify inspection requirement based on vehicle age and type
2. Check customer's preferred inspection mode (garage, home, or drive-through centre) and availability via scheduling MCP
3. Assign nearest available surveyor or inspection agency; send appointment confirmation to customer via WhatsApp
4. Brief surveyor with vehicle details, coverage requested, and inspection checklist requirements
5. Receive completed inspection report from surveyor via email or mobile app upload; parse with document parsing
6. Validate report completeness: all required fields present, photographs included, odometer reading captured
7. **HITL checkpoint:** underwriter reviews flagged inspection findings (prior damage, modification, odometer anomaly)
8. Upload structured inspection data to PMS; trigger policy issuance workflow for clean inspections; update customer

**Tools Used**
PMS connector · Scheduling system MCP · WhatsApp Business MCP · Email MCP ·
Document parsing (inspection report OCR) · Code execution (completeness validation) · Audit trail

**Revenue Model (₹)**
- ₹25,000/month: up to 200 inspections/month, scheduling + report upload
- ₹60,000/month: unlimited inspections, full lifecycle automation, report quality validation, underwriter dashboard
- Per-inspection: ₹100–₹200/inspection (for partners and brokers with variable volumes)

**ROI**
Inspection-to-issuance time drops from 5–7 days to same-day for 70% of vehicles. Motor proposal
conversion rate improves by 15–25% due to reduced friction. One insurer eliminated 3 FTE
inspection coordination roles (₹15–20L/year) while handling 40% more inspection volume.

**Target Customers**
Motor insurers and digital aggregators, insurance brokers managing fleet inspections,
bancassurance partners where motor is a high-volume product line.

---

## Monetization Strategy

### Tier 1 — Digital Core (₹30,000–₹80,000/month)
For regional insurers and insurtechs digitising their first workflows. Includes claims FNOL intake
(up to 500 claims/month), KYC onboarding automation, motor inspection scheduling, and renewal
campaign for up to 2,000 policies. All decisions require HITL approval. 5 user seats, IRDAI
audit trail included, and standard integration with 1 PMS platform.

### Tier 2 — Operations Excellence (₹1,20,000–₹2,50,000/month)
For mid-size insurers automating across the policy lifecycle. Unlimited claims handling, full
underwriting data gathering suite, fraud detection with SIU dossier generation, IRDAI regulatory
filing for up to 20 returns per quarter, reinsurance bordereau preparation for up to 10 treaties,
and cross-sell intelligence. 20 user seats, dedicated CSM, and quarterly compliance audit reports.

### Tier 3 — Insurer OS (₹4,00,000+/month)
For large insurers and insurance groups seeking end-to-end automation. Includes full 119-connector
library, custom underwriting models and fraud scoring, multi-entity regulatory filing across all
IRDAI returns, on-premise or VPC deployment, white-label customer portal, dedicated Solutions
Architect, and regulatory examination support documentation. SLA-backed 99.9% uptime with
financial penalties. Custom integrations with legacy core insurance platforms.

---

## Sample AgentManifest — Claims Intake Agent

```yaml
name: claims-intake-agent
version: "1.6.0"
domain: insurance
description: >
  Handles First Notice of Loss across all inbound channels — WhatsApp,
  web portal, email, and partner API — validating coverage, classifying
  severity, detecting fraud signals, and routing to the correct handling
  unit within 3 minutes of first contact.

goal_template: |
  Process incoming {claim_type} FNOL for policy {policy_number}
  with reported loss of {reported_loss_inr} INR,
  routing to {handling_unit} within {sla_minutes} minutes.

planner:
  model: claude-3-5-sonnet
  max_iterations: 6
  replan_on_failure: true
  context_sources:
    - policy_wording_library
    - underwriting_guidelines
    - fraud_signal_registry

executor:
  model: gpt-4o
  tool_timeout_seconds: 20
  parallel_tool_calls: true

verifier:
  model: claude-3-5-sonnet
  success_criteria:
    - coverage_validated: true
    - fraud_score_calculated: true
    - routing_decision_made: true
    - claimant_acknowledgement_sent: true
    - audit_entry_created: true

mcp_connectors:
  - whatsapp-business
  - email
  - pms-connector
  - iib-fraud-registry
  - cibil-aml-watch
  - slack
  - claims-system

hitl:
  enabled: true
  triggers:
    - action: refer_to_siu
      threshold: always
    - action: decline_claim
      threshold: always
    - action: fast_track_settlement
      threshold: amount_inr > 100000
  approval_timeout_hours: 2
  escalation_channel: "slack:#claims-supervision"

audit:
  enabled: true
  retention_days: 3650      # 10 years (IRDAI requirement)
  include_llm_reasoning: true
  tamper_evident: true
  export_format: json
  regulator: IRDAI

schedule:
  fraud_model_refresh:  "0 2 * * 0"    # weekly Sunday 2 AM
  pending_docs_followup: "0 9 * * *"   # daily 9 AM
  sla_breach_check:     "*/30 * * * *" # every 30 minutes
```

---

*AgentVerse — the insurer's autonomous operations layer, built for IRDAI compliance from day one.*
