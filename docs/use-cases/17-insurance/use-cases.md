# AgentVerse for Insurance

> **"From first notice of loss to final settlement — autonomous insurance operations that reduce loss ratios, accelerate claims, and prove compliance."**

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Platform Capabilities](#platform-capabilities)
3. [Use Cases](#use-cases)
   - [UC-1: Policy Underwriting Assistance](#uc-1-policy-underwriting-assistance)
   - [UC-2: Claims Intake and Triage](#uc-2-claims-intake-and-triage)
   - [UC-3: Fraud Detection and Investigation](#uc-3-fraud-detection-and-investigation)
   - [UC-4: Renewal Campaign Automation](#uc-4-renewal-campaign-automation)
   - [UC-5: Customer Onboarding (KYC/AML)](#uc-5-customer-onboarding-kycaml)
   - [UC-6: Premium Calculation Verification](#uc-6-premium-calculation-verification)
   - [UC-7: Reinsurance Data Preparation](#uc-7-reinsurance-data-preparation)
   - [UC-8: Regulatory Filing Automation](#uc-8-regulatory-filing-automation)
   - [UC-9: Claims Adjudication Support](#uc-9-claims-adjudication-support)
   - [UC-10: Cross-Sell/Upsell Opportunity Identification](#uc-10-cross-sellupsell-opportunity-identification)
   - [UC-11: Policy Document Generation](#uc-11-policy-document-generation)
   - [UC-12: Loss Run Report Preparation](#uc-12-loss-run-report-preparation)
4. [Monetization Strategy](#monetization-strategy)
5. [Sample AgentManifest](#sample-agentmanifest)
6. [Competitive Displacement](#competitive-displacement)
7. [Implementation Timeline](#implementation-timeline)

---

## Executive Summary

### The Pain

Insurance is one of the last major industries still running its core operations on **manual review and paper-intensive processes**. The average P&C claim takes **18–23 days to close**. An underwriter reviews **30–50 applications per day** when data suggests optimal throughput would be 80–120 with proper AI assistance. KYC/AML onboarding for commercial lines takes **5–15 business days**, during which the prospect may defect to a competitor. Regulatory filings arrive late 22% of the time, triggering fines and remediation costs averaging $85,000 per incident.

The cost structure is brutal:
- **Combined ratio** (the metric that determines insurance profitability) averages 98–104 for most P&C lines — meaning most insurers are technically operating at a loss on underwriting and surviving on investment income
- Fraud accounts for **10–15% of all claims paid** — $40B+ annually in the US P&C market
- Claims handling expense (loss adjustment expense) averages **15–20 cents per dollar of claims paid** — much of it manual coordination labor
- Renewal campaigns that treat all customers identically leave **12–18% of the book** underpriced and attract adverse selection

The industry has attempted digital transformation for two decades with limited success because the problems require **intelligence, not just automation** — and AgentVerse delivers the intelligence layer that legacy insurtechs cannot.

### Market Opportunity

- Global insurance industry GWP: **$6.3T annually**
- Insurtech market: **$10.5B → $152B** by 2030 (CAGR 38%)
- Insurance AI market specifically: **$2.4B → $45B** by 2031
- Claims management software: **$4.8B** market
- Policy administration systems: **$9.3B** market
- Fraud detection market: **$4.1B → $18.8B** by 2029

### The AgentVerse Advantage

AgentVerse addresses insurance's unique challenge: the work is simultaneously **knowledge-intensive** (requiring regulatory and actuarial expertise), **document-heavy** (10–50 documents per claim or application), and **multi-system** (requiring coordination across policy admin, claims, billing, compliance, and reinsurance systems). No point solution addresses all three dimensions.

AgentVerse's competitive moat in insurance:
- Native document parsing for the full spectrum of insurance documents: ACORD forms, loss runs, medical records, inspection reports, police reports
- HITL gates designed for insurance's regulatory environment — every consequential decision is human-approved and audit-trailed
- Multi-agent workflows that parallelize claim investigation while maintaining chain-of-evidence documentation
- Full audit trail that satisfies state insurance department examination requirements
- Integration with insurance-specific systems via MCP connectors alongside general-purpose connectors

---

## Platform Capabilities

| Capability | Insurance Application |
|---|---|
| **Natural-Language Goal Execution** | "Underwrite this commercial property application and recommend premium" / "Process this auto claim from FNOL to reserve setting" |
| **Multi-Agent Workflows** | Parallel claim investigation: coverage analysis + fraud scoring + liability assessment simultaneously |
| **MCP Connectors (119)** | Salesforce, HubSpot, Slack, PagerDuty, AWS, DocuSign, Twilio, Jira, Google Analytics |
| **Browser Automation** | State DMV lookups, county recorder searches, public court records, medical provider license verification |
| **Document Parsing** | ACORD forms, medical records, police reports, inspection reports, loss run parsing |
| **Web Search** | Claimant social media investigation, property ownership research, news verification |
| **Code Sandbox** | Premium calculation, actuarial reserve modeling, fraud score computation |
| **Email Integration** | Claims correspondence, policyholder communications, regulatory submissions |
| **HITL Approval Gates** | Reserve changes, coverage decisions, subrogation pursuit, litigation referral |
| **Cost Governance** | Per-claim LLM budget, per-product spend caps, agent cost allocation to LAE |
| **Full Audit Trail** | Every decision documented for regulatory exam, litigation defense, and actuarial review |
| **RBAC** | Claims adjudicator handles intake; senior adjuster approves reserves; manager approves litigation |

---

## Use Cases

---

### UC-1: Policy Underwriting Assistance

**The Problem**

Commercial lines underwriters at mid-size carriers review **30–50 applications per day** — a throughput constrained not by their analytical capability but by the **manual data gathering** preceding every decision: pulling loss history from five different sources, ordering inspection reports, verifying COPE data, checking claims history, computing experience modification factors. Industry research suggests underwriters spend **40–55% of their time** on data gathering vs. actual risk assessment. This creates a capacity bottleneck where qualified applications wait **5–10 business days** for underwriter attention — during which competitors may bind the risk.

**AgentVerse Solution**

An underwriting assistance agent pre-populates every submission with all required data — loss history, inspection reports, financial data, exposure information — so the underwriter receives a complete risk dossier and focuses exclusively on the judgment call: accept/decline/modify and at what price.

**Agent Workflow**

1. Receive submission via ACORD XML, email, or agency portal → parse submission details
2. Extract key risk characteristics: Named Insured, NAICS code, locations, coverages requested, prior carrier
3. Order and retrieve loss run reports: request from prior carrier via email connector → parse when received
4. Verify entity information: Secretary of State filing, registered agents, subsidiary structure via browser automation
5. Pull property data for commercial property risks: COPE data from county assessor, building permits, recent sales
6. Check claims database: pull all prior claims for named insured across all markets
7. Retrieve credit/financial data for surety or bond risks via authorized data connector
8. Web search: verify business legitimacy, check for adverse news (litigation, regulatory actions, fire history)
9. Compute preliminary risk score: loss ratio, frequency trend, exposure growth pattern
10. Apply automated eligibility rules: screen against appetite guidelines, embargo lists, prior declination flags
11. Generate complete underwriting summary dossier: risk characteristics, loss history, data exceptions, preliminary assessment
12. Route dossier to underwriter with recommended action and pricing range [HITL — underwriter makes final decision]

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Browser Automation | State filings, property records, court records |
| Document Parser | ACORD form and loss run parsing |
| Web Search | Entity verification, adverse news |
| Code Sandbox | Risk scoring, experience modification calculation |
| Email | Loss run requests, broker correspondence |
| Slack | Underwriter queue management |
| Salesforce | Policy management system integration |

**Revenue Model**

- **Per-submission:** $12 (automated pre-fill and dossier) vs. 45 minutes of underwriter time
- **Subscription:** $5,000/month (up to 500 submissions/month)
- **Enterprise:** $15,000/month (unlimited + custom appetite rules + delegated authority workflows)

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| Submission-to-quote turnaround | 7–10 days | 1–2 days |
| Underwriter data gathering time | 55% of workday | 15% of workday |
| Submissions underwritten per underwriter/day | 35 | 85 |
| Quote-to-bind conversion rate | 28% | 41% |

**Target Customers**

- Regional and specialty P&C carriers (commercial lines)
- Managing General Agents (MGAs) with delegated underwriting authority
- Lloyd's syndicates and surplus lines markets

---

### UC-2: Claims Intake and Triage

**The Problem**

First Notice of Loss (FNOL) is the most critical moment in the claims lifecycle. The decisions made in the first 24–48 hours — coverage verification, initial reserve setting, assignment of claim type, investigator assignment — determine cycle time, indemnity cost, and customer satisfaction. Yet FNOL is routinely handled by **entry-level staff following rigid scripts**, often missing red flags that would trigger early intervention and save significant indemnity dollars. Industry studies show that claims identified for early intervention save **18–31% in ultimate indemnity costs**.

**AgentVerse Solution**

An intelligent FNOL agent handles the complete intake process: verifying coverage, extracting loss details, scoring complexity and fraud indicators, setting an initial reserve, and routing to the correct adjuster pool — transforming FNOL from a data-entry function to a diagnostic one.

**Agent Workflow**

1. Receive FNOL via API (phone IVR transcription), email, web portal, or mobile app
2. Verify policy in force: coverage effective dates, limits, deductibles, endorsements via policy admin system
3. Extract loss details: date of loss, location, cause of loss, reported damages, injured parties
4. Validate coverage: apply coverage conditions to reported cause and facts → identify coverage issues
5. Score claim complexity: injury severity indicators, property damage estimate, multiple parties, commercial involvement
6. Run initial fraud score: date of loss pattern (Friday/Monday), prior claims history, recent policy change, inconsistency flags
7. Set initial reserve: apply line-of-business reserve adequacy model for reported facts
8. Determine fast-track eligibility: low-complexity, no injury, confirmed coverage → auto-assign fast-track settlement pathway
9. Assign to adjuster pool based on complexity score, line of business, geography
10. Notify claimant of claim number, adjuster assignment, and initial timeline via email/SMS
11. Trigger first-party investigation tasks: field inspection assignment, recorded statement scheduling
12. Generate claim summary for assigned adjuster: coverage analysis, initial facts, fraud flags, reserve recommendation

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Document Parser | FNOL form and email parsing |
| Code Sandbox | Reserve calculation, complexity scoring |
| Email | Claimant acknowledgment, adjuster assignment |
| Twilio | SMS claimant notification |
| Slack | Adjuster team queue notifications |
| Salesforce | Policy system coverage verification |
| PagerDuty | Catastrophe event escalation |

**Revenue Model**

- **Per-claim intake:** $8 (full FNOL automation vs. 45-minute staff intake)
- **Claims operations platform:** $4,000/month (up to 500 claims/month)
- **Enterprise:** $12,000/month (unlimited claims + reserve modeling + CAT integration)

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| FNOL-to-adjuster-assignment time | 4–8 hours | <30 minutes |
| Initial reserve accuracy (vs. ultimate) | 62% | 81% |
| Claims identified for early intervention | 8% | 31% |
| Customer satisfaction at FNOL (CSAT) | 3.2/5 | 4.4/5 |

**Target Customers**

- P&C insurance carriers (auto, property, workers' comp, liability)
- Third-party administrators (TPAs)
- Self-insured corporations with captive programs

---

### UC-3: Fraud Detection and Investigation

**The Problem**

Insurance fraud costs the US industry **$40–$80B annually** — roughly **$400–$700 per insured household per year** in premium loading. Organized fraud rings, staged accidents, inflated claims, and phantom medical billing are increasingly sophisticated. Traditional rule-based detection catches only the most obvious schemes; statistical anomaly detection generates high false-positive rates that overwhelm SIU units. The average SIU investigation costs **$5,000–$15,000** in investigator time — making comprehensive investigation of all suspicious claims economically impossible.

**AgentVerse Solution**

A fraud detection agent applies multi-dimensional analysis to every claim — behavioral, social network, historical pattern, and public records — to compute a calibrated fraud probability score and produce an investigation-ready dossier for confirmed referrals, so SIU investigators focus on the cases worth pursuing.

**Agent Workflow**

1. Receive claim record at FNOL and at each subsequent claim status change
2. Apply behavioral analytics: loss date/day patterns, FNOL timing vs. loss date, claim reopening patterns
3. Analyze claimant history: prior claims across all markets, attorney representation pattern, medical provider patterns
4. Social network analysis via code sandbox: identify relationships between claimant, medical providers, attorneys, repair shops
5. Web search and browser automation: claimant social media review for activity inconsistent with claimed injury
6. Browser automation: verify medical provider license and disciplinary history, vehicle title history, property deed
7. Cross-reference claimant against known fraud databases (ISO ClaimSearch, NICB) via API connector
8. Apply anomaly detection: compare claim characteristics to fraud ring signature library
9. Compute composite fraud probability score (0–100) with contributing factor weights
10. Score 0–30: continue normal claims handling, fraud flag documented
11. Score 31–70: enhanced investigation protocol — recorded statement, EUO scheduling, surveillance recommendation
12. Score 71–100: generate SIU referral dossier with full evidence package → route to SIU [HITL required]

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Web Search | Social media, news, public records investigation |
| Browser Automation | License verification, property/vehicle records |
| Code Sandbox | Social network analysis, fraud score modeling |
| Document Parser | Medical record and repair estimate analysis |
| Slack | SIU referral notification |
| Email | Investigation correspondence |
| Jira | SIU investigation tracking |

**Revenue Model**

- **Per-claim screening:** $4 (vs. $8,000–$15,000 SIU investigation cost)
- **Fraud operations platform:** $6,000/month (full scoring + SIU workflow)
- **ROI share:** 3% of fraud losses averted above baseline detection rate

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| Fraud detection rate | 8–12% of fraudulent claims | 34–48% |
| SIU referrals (false positive rate) | 52% | 18% |
| Cost per fraud investigation | $12,000 | $4 (screening) + $12,000 (confirmed only) |
| Annual fraud loss reduction (per $100M premium) | — | $1.5M–$3M |

**Target Customers**

- P&C carriers with high fraud-exposure lines (PIP, medical payments, cargo)
- Workers' compensation carriers and TPAs
- Healthcare insurers with billing fraud exposure

---

### UC-4: Renewal Campaign Automation

**The Problem**

Insurance renewal is the most predictable revenue opportunity in the business — and the most systematically mismanaged. Renewals are treated as administrative events rather than sales events: a billing notice goes out, the premium goes up (or flat), and agents hope the customer doesn't shop. The result: **14–22% annual lapse rates** in personal lines and **9–16%** in commercial lines. Customers who leave at renewal represent **5–7x their annual premium value** in lifetime value lost. Personalized retention programs are proven to reduce lapse by **30–40%** but require per-policyholder analysis at scale that manual operations cannot achieve.

**AgentVerse Solution**

A renewal campaign agent analyzes the upcoming renewal book 90–120 days out, segments policies by flight risk and profitability, generates personalized renewal strategies for each segment, and executes multi-channel outreach — treating each renewal as the retention event it is.

**Agent Workflow**

1. Pull renewal book 90–120 days out from policy admin system: all policies renewing in the upcoming 30-day window
2. For each policy, compute renewal profitability: loss ratio, expense ratio, rate adequacy assessment
3. Score flight risk: payment history, rate change magnitude, competitor pricing intelligence, prior shopping signals, agent engagement
4. Segment renewal book: profitable + stable / profitable + at-risk / unprofitable + at-risk / unprofitable + stable
5. Generate renewal strategy per segment:
   - Profitable + at-risk: retention outreach, loyalty offer, proactive agent call trigger
   - Profitable + stable: streamlined auto-renewal confirmation
   - Unprofitable + at-risk: allow lapse or offer re-underwriting at adequate rate
   - Unprofitable + stable: re-underwriting with corrected pricing
6. Draft personalized renewal communication for each segment: email copy, SMS, agent talking points
7. Route renewal campaign plan to agency management / underwriting team [HITL for pricing changes]
8. Execute approved campaigns: send emails via Mailchimp, trigger agent tasks in CRM, send SMS via Twilio
9. Monitor campaign engagement: opens, clicks, calls, payment receipts
10. Identify non-responders at day 30 and day 60 → escalate to agent outreach
11. Track renewal results: retention rate by segment, lapse reason code
12. Post-renewal analysis: update flight risk model with actual vs. predicted lapse outcomes

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Code Sandbox | Profitability scoring, flight risk model |
| Email / Mailchimp | Renewal campaign execution |
| Twilio | SMS outreach |
| Salesforce | Agent task assignment |
| Google Analytics | Campaign engagement tracking |
| Slack | Underwriting team notifications |
| Web Search | Competitor rate intelligence |

**Revenue Model**

- **Per-renewal processed:** $3 (automated analysis + outreach)
- **Retention platform:** $4,000/month (full renewal book automation)
- **Performance model:** $0 setup + 8% of premium retained above lapse baseline

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| Annual lapse rate | 18% | 11% |
| Renewal processing staff hours | 4 hours per policy | 8 minutes |
| Retention outreach personalization | Generic mass email | Per-policyholder strategy |
| Premium retained per $100M book | $82M | $89M |

**Target Customers**

- Personal lines carriers (auto, homeowners, renters)
- Commercial lines carriers with agency distribution
- Independent insurance agencies managing renewal books

---

### UC-5: Customer Onboarding (KYC/AML)

**The Problem**

Insurance companies are increasingly subject to Anti-Money Laundering (AML) and Know Your Customer (KYC) requirements — particularly for life insurance, annuities, and commercial lines with large premium volumes. The AML program requirement under the Bank Secrecy Act's insurance company provisions means carriers must verify customer identity, screen against OFAC and sanction lists, assess source of funds for large premium transactions, and file Suspicious Activity Reports (SARs) when warranted. Manual KYC for commercial accounts averages **8–15 business days** and costs **$150–$800 per customer** in compliance analyst time.

**AgentVerse Solution**

An autonomous KYC/AML agent performs all required customer due diligence at onboarding: identity verification, sanctions screening, beneficial ownership determination, source of wealth verification, and SAR determination — completing in hours what previously took weeks.

**Agent Workflow**

1. Receive new customer application: personal/commercial lines, premium amount, payment method
2. Extract identifying information: legal name, SSN/EIN, date of birth, address, beneficial owners
3. Screen against OFAC SDN list, PEP (Politically Exposed Persons) databases, adverse media — real-time
4. Verify identity documents via document parser: driver's license, passport, corporate formation docs
5. For commercial accounts: determine beneficial ownership (>25% ownership threshold per FinCEN CDD rule)
6. Screen all beneficial owners individually against sanctions and PEP lists
7. For high-risk customers (PEP, unusual payment pattern, high-value policy): initiate enhanced due diligence
8. Browser automation: verify entity existence (Secretary of State), litigation history, financial press
9. Assess source of premium funds for large premium transactions (life/annuity >$10,000)
10. Compute AML risk score → classify as Low / Medium / High risk
11. Document all screening results and decisions with evidence → create KYC file
12. Route high-risk customers to compliance officer for review [HITL — SAR determination requires human]

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Browser Automation | State filings, property records, court records |
| Document Parser | Identity document and corporate record parsing |
| Code Sandbox | AML risk scoring, beneficial ownership mapping |
| Web Search | Adverse media, litigation research |
| Email | Customer correspondence, document requests |
| Slack | Compliance team alerts |
| Jira | High-risk file tracking |

**Revenue Model**

- **Per onboarding:** $25 (full KYC/AML screening vs. $150–$800 manual)
- **Compliance program:** $3,500/month (unlimited screenings + SAR workflow)
- **Enterprise:** $10,000/month (multi-line, multi-jurisdiction, EDD workflows)

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| KYC completion time | 8–15 days | 4–8 hours |
| Cost per customer KYC | $350 | $25 |
| Sanctions screening coverage | 85% of policies | 100% |
| SAR filing accuracy | Variable | Documented, defensible decision trail |

**Target Customers**

- Life insurers and annuity writers with BSA obligations
- Commercial property and casualty carriers with large premium accounts
- Reinsurers conducting cedant KYC due diligence

---

### UC-6: Premium Calculation Verification

**The Problem**

Premium calculation errors are more common than the industry acknowledges. Rating engines apply hundreds of factors — territory, class code, experience modifiers, schedule credits/debits, endorsements, deductibles — and errors in any factor propagate into incorrect premiums. Audits of commercial lines policies routinely find **8–15% of policies priced incorrectly**: some underpriced (adverse selection risk), some overpriced (solvency and regulatory compliance risk). Each incorrect premium is also a potential bad faith liability — charging a premium that doesn't match the filed rate schedule is a regulatory violation in every state.

**AgentVerse Solution**

A premium verification agent independently re-rates every policy at issuance and at renewal against the filed rate schedule, flags discrepancies, and generates correction recommendations — functioning as a continuous actuarial quality control layer.

**Agent Workflow**

1. Receive policy data from policy admin system at issuance or renewal: all risk characteristics, applied factors
2. Extract applied rating elements: territory code, class code, limit/deductible selection, experience modifier, schedule credits
3. Retrieve applicable filed rate schedule from rate database: state, line of business, effective date
4. Code sandbox: independently re-rate policy using extracted risk characteristics and filed rates
5. Compare system-calculated premium to agent-calculated premium → flag all variances
6. Classify variance type: incorrect territory code, wrong class code, misapplied modifier, unapproved schedule credit
7. For variances >$50 or >2%: generate correction memo with specific rating error and correct premium
8. For regulatory-non-compliant deviations from filed rates: urgent alert → route to compliance team [HITL]
9. For acceptable variances: document verification and approve policy for issuance
10. Generate weekly premium accuracy report: error rate by product, territory, agency, underwriter
11. Identify systematic errors in rating engine → escalate to IT actuarial team
12. Maintain audit trail of all premium verifications for state exam evidence

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Code Sandbox | Independent re-rating, variance computation |
| Document Parser | Rate schedule and policy form parsing |
| Slack | Compliance alerts, actuary notifications |
| Email | Correction memo distribution |
| Jira | Systematic error tracking |
| Salesforce | Policy system integration |

**Revenue Model**

- **Per-policy verification:** $1.50 (vs. sampled manual audit)
- **Continuous QC:** $3,000/month (all new issues and renewals)
- **Actuarial QC:** $8,000/month (premium verification + actuarial adequacy monitoring)

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| Premium error detection rate | 2–5% (sampled) | 100% coverage |
| Regulatory violations from rate deviation | 3–6/year | <1/year |
| Underpriced policies at renewal | 12% | <3% |
| Combined ratio impact of corrected pricing | — | 0.8–1.5 points improvement |

**Target Customers**

- P&C carriers with complex commercial lines rating
- Carriers under active state regulatory scrutiny
- New market entrants with unproven rating engines

---

### UC-7: Reinsurance Data Preparation

**The Problem**

Reinsurance treaty renewals require carriers to prepare **comprehensive underwriting data packages** for reinsurance markets: 5–10 years of loss development, exposure summaries by class and territory, large loss listings, catastrophe modeling inputs, underwriting guidelines, and portfolio composition analysis. This preparation takes actuarial and data teams **4–8 weeks** and costs **$80,000–$200,000** in internal and consulting labor. Errors in reinsurance submissions can result in **incorrect treaty pricing**, leaving the carrier exposed to adverse reinsurance economics on its core book.

**AgentVerse Solution**

An autonomous reinsurance data agent continuously maintains the underlying data infrastructure and compiles treaty renewal packages on demand — transforming a painful 8-week sprint into a 48-hour report generation exercise.

**Agent Workflow**

1. Maintain continuous data pipeline: pull policy data, claims data, and financial data from policy admin and claims systems
2. Run monthly triangle updates: develop loss triangles by accident year and report year for each treaty
3. Compile exposure summaries: earned premium, in-force premium, exposure base (payroll, TIV, etc.) by class and territory
4. Prepare large loss listing: extract all losses above $X threshold, with full loss detail and description
5. Run catastrophe exposure summary: PML estimates, CAT model output summaries, geographic concentration
6. Code sandbox: compute loss development factors, ultimate loss estimates, loss ratio by class
7. Generate underwriting portfolio composition analysis: class mix, limit profile, deductible distribution, geographic spread
8. Compile 5-year historical underwriting summary: premium, losses, expenses, combined ratio
9. Assemble reinsurance data package per treaty: specific exhibits required by each reinsurer (some have custom formats)
10. Route package to Chief Actuary and CFO for review [HITL — treaty negotiation strategy]
11. Prepare technical Q&A responses for reinsurer questions
12. Post-treaty: track reinsurance recoverable by treaty and accident year

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Code Sandbox | Actuarial triangle development, statistical analysis |
| Document Parser | Reinsurance treaty parsing, prior year package comparison |
| Email | Reinsurer data package distribution |
| Slack | Actuarial team collaboration |
| AWS | Data warehouse integration |
| Web Search | Reinsurance market intelligence |

**Revenue Model**

- **Annual treaty prep:** $15,000 per treaty (vs. $80,000–$200,000 internal/consulting)
- **Continuous data maintenance:** $5,000/month (ongoing data pipeline + quarterly updates)
- **Full reinsurance analytics:** $12,000/month (unlimited treaties + catastrophe modeling)

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| Treaty renewal preparation time | 6–8 weeks | 3–5 days |
| Internal labor cost per renewal | $120,000 | $15,000 |
| Data quality errors in submission | 8–15% of data points | <1% |
| Reinsurer pricing favorability | Baseline | 2–4% improvement from quality data |

**Target Customers**

- Regional P&C carriers with proportional and non-proportional treaties
- Specialty carriers with complex facultative programs
- Captive managers preparing annual captive data for reinsurers

---

### UC-8: Regulatory Filing Automation

**The Problem**

Insurance is one of the most heavily regulated industries: carriers file **hundreds of forms annually** with 50 state insurance departments — rates, rules, forms, financial statements (NAIC Annual Statement), market conduct exams, ORSA reports, and more. The NAIC Annual Statement alone has **47 exhibits and supporting schedules**. Missing or late filings trigger fines starting at **$1,000/day** and escalating to license suspension. Yet filing management is typically handled by 1–3 compliance specialists using Excel trackers and Outlook reminders, with no automated verification that filed content matches actual company data.

**AgentVerse Solution**

An autonomous regulatory filing agent maintains a complete filing calendar across all states and filing types, automatically prepares data-driven filings from underlying source systems, routes to compliance officer review, and tracks submission and approval status with regulators.

**Agent Workflow**

1. Maintain regulatory filing calendar: all required filings by state, line of business, due date, responsible regulator
2. 60-day advance: alert compliance team of upcoming filing → trigger data collection workflow
3. For financial filings (Quarterly/Annual Statement): pull general ledger, investment schedule, and reserve data from financial systems
4. Code sandbox: populate NAIC statement blank exhibits from source data → validate NAIC edit checks
5. For rate/rule filings: compile actuarial support, rate derivation, and competitive analysis
6. For form filings: parse new/revised policy forms → check against state form requirements via browser automation
7. Browser automation: look up state-specific filing requirements on SERFF (System for Electronic Rate and Form Filing) portal
8. Route completed filing to Chief Compliance Officer for review and approval [HITL — officer signs all regulatory filings]
9. Upon approval, submit via SERFF portal (browser automation) or email to state department
10. Track filing status: acknowledgment received, objections received, approval received
11. For regulatory objections: parse objection text → generate response with supporting actuarial evidence
12. Monthly: Filing compliance dashboard — on-time rate, pending approvals, outstanding objections

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Browser Automation | SERFF filing, state department portals |
| Document Parser | Regulatory notice and objection parsing |
| Code Sandbox | NAIC edit check validation, data population |
| Email | Regulator correspondence |
| Slack | Compliance team deadline alerts |
| Web Search | State regulatory update monitoring |
| Document Management | Filing archive with version control |

**Revenue Model**

- **Per-filing:** $200 (automated preparation + tracking)
- **Compliance calendar:** $3,500/month (all filings for up to 10 states)
- **Enterprise:** $10,000/month (all 50 states + NAIC financial filings + objection management)

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| Filing on-time rate | 78% | 98% |
| Compliance staff hours/filing | 12–20 hours | 2–4 hours |
| Annual regulatory fines | $85,000 | <$5,000 |
| NAIC statement preparation time | 6–8 weeks | 2 weeks |

**Target Customers**

- Multi-state licensed P&C and life carriers
- Captive managers with state regulatory obligations
- Insurance holding companies with complex filing requirements

---

### UC-9: Claims Adjudication Support

**The Problem**

The average property claim involves **20–35 coverage and liability determinations**, each requiring the adjuster to apply policy language to specific facts — a task that requires deep knowledge of coverage forms, endorsements, case law, and state statutes. Junior adjusters make coverage errors in **18–25% of decisions** that result in either incorrect overpayment (increasing loss ratio) or wrongful claim denial (bad faith exposure worth **10–50x the underlying claim value** in punitive damages). The solution — more senior adjuster review — creates a bottleneck that slows cycle time and increases expense.

**AgentVerse Solution**

An adjudication support agent serves as an always-available coverage counsel for adjusters: analyzing policy language against specific claim facts, surfacing relevant case law, flagging coverage issues and exclusions, and producing a coverage analysis memo — empowering junior adjusters to make decisions with senior-level confidence.

**Agent Workflow**

1. Receive claim record with coverage analysis request: policy details, loss facts, specific coverage question
2. Parse policy document: extract relevant insuring agreement, exclusions, conditions, endorsements via document parser
3. Apply specific loss facts to policy language: does the cause of loss fall within the insuring agreement?
4. Identify potentially applicable exclusions: apply exclusion language to specific loss facts → assess applicability
5. Research relevant case law: web search for jurisdiction-specific coverage decisions on similar facts
6. Identify conditions issues: did policyholder comply with notice, cooperation, proof of loss requirements?
7. Flag reservation of rights triggers: if coverage is doubtful, identify duty to defend considerations
8. Compute coverage position: covered / denied / covered with reservation of rights / need additional information
9. Generate coverage analysis memo: policy language, fact application, legal authorities, conclusion, recommended action
10. Route to senior adjuster or coverage counsel for high-value, complex, or ambiguous determinations [HITL]
11. For denied claims: draft denial letter citing specific policy provisions → route for supervisor approval [HITL]
12. Archive coverage analyses for consistent decision-making and training data

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Document Parser | Policy form and endorsement parsing |
| Web Search | Case law and coverage opinion research |
| Code Sandbox | Coverage checklist evaluation |
| Slack | Senior adjuster escalation routing |
| Email | Coverage opinion delivery, denial letters |
| Jira | Complex coverage investigation tracking |

**Revenue Model**

- **Per-coverage analysis:** $15 (vs. 60–90 minutes adjuster/counsel time)
- **Adjudication platform:** $4,000/month (full adjuster support, unlimited analyses)
- **Enterprise LAE reduction:** $12,000/month (full claims AI suite including coverage, reserves, fraud)

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| Coverage determination error rate | 20% | 4% |
| Bad faith exposure incidents | 4–8/year | <1/year |
| Coverage analysis time per claim | 90 minutes | 12 minutes |
| Junior adjuster escalation rate | 45% | 18% |

**Target Customers**

- P&C carriers with large commercial lines claims operations
- Workers' compensation carriers and TPAs
- Excess and surplus lines carriers with non-standard coverage forms

---

### UC-10: Cross-Sell/Upsell Opportunity Identification

**The Problem**

The average insurance customer holds **1.7 policies** with their primary carrier when the optimal number (maximum coverage, maximum loyalty) is **3–4 policies**. Cross-sell penetration is low not because customers don't need the coverage but because agents and carriers lack the triggers and analytical foundation to make the right offer at the right time. Blanket marketing campaigns for auto-to-homeowners cross-sell have **0.8–1.2% conversion rates**. Behavioral analytics-driven targeted offers achieve **6–14%** — but require the analytics infrastructure most carriers lack.

**AgentVerse Solution**

A cross-sell intelligence agent continuously analyzes policyholder life events, coverage gaps, and behavioral signals to identify specific, timely cross-sell and upsell opportunities — surfacing them to agents with personalized talking points and ready-to-present quotes.

**Agent Workflow**

1. Pull complete policy portfolio for all policyholders: all lines in force, coverage limits, deductibles, premium
2. Identify coverage gaps vs. actuarial life-stage needs model: auto without umbrella, homeowners without flood, business owner without cyber
3. Monitor policyholder life event signals: address change (→ homeowners), vehicle addition (→ auto), business registration (→ BOP)
4. Analyze premium adequacy: identify underinsured policies where limits are materially below replacement cost
5. Pull agent engagement data: last contact date, open rates on communications, service interactions
6. Apply propensity model: score each opportunity by conversion probability × premium uplift × lifetime value
7. Generate agent alert with specific opportunity: which product, why this customer, talking points, competitor context
8. Pre-generate quote for highest-priority opportunities: reduce friction from agent conversation to binding
9. Route opportunities to assigned agent via CRM task + email notification
10. Track agent action on opportunities: contacted/presented/bound/declined
11. Monthly: Cross-sell opportunity report by agent, product, territory → feed into agency management review
12. Quarterly: Update propensity model with actual conversion outcomes

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Code Sandbox | Propensity scoring, gap analysis |
| Salesforce | Policyholder data, agent task creation |
| Email | Agent opportunity alerts |
| Slack | High-priority opportunity notifications |
| Web Search | Life event signal research (business registration) |
| Mailchimp | Policyholder direct marketing campaigns |

**Revenue Model**

- **Per-opportunity identified:** $5 (vs. dedicated analytics team)
- **Cross-sell platform:** $3,000/month (continuous opportunity engine)
- **Growth share:** 2% of new premium written from identified opportunities

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| Policies-per-customer | 1.7 | 2.4 |
| Cross-sell campaign conversion rate | 1.1% | 8.4% |
| Agent hours/week on opportunity identification | 8 | 1 |
| Annual new premium from cross-sell (per $100M book) | $2.1M | $7.8M |

**Target Customers**

- Personal lines carriers with captive and independent agent distribution
- Commercial lines carriers with multi-line accounts
- Independent insurance agencies building household penetration

---

### UC-11: Policy Document Generation

**The Problem**

Policy document generation is deceptively complex: declarations pages, coverage forms, endorsements, and certificates of insurance must be **precisely assembled from 100–200 component documents** in the correct combination, with correct typesetting, for each unique risk. Errors in policy documents — wrong limit on the declarations, missing endorsement, incorrect additional insured — create coverage disputes, E&O claims, and regulatory violations. Yet policy printing and issuance remains a manual or semi-automated process at many carriers and agencies, with **turnaround times of 3–10 days** for complex commercial policies.

**AgentVerse Solution**

A policy document agent intelligently assembles the complete policy package for any risk from the component form library, validates the assembly against coverage intent, generates certificate of insurance documents on demand, and delivers to policyholder and agent with full version control.

**Agent Workflow**

1. Receive policy data from policy admin system: named insured, coverage parameters, endorsements attached, effective dates
2. Apply form selection algorithm: select correct editions of all required forms (base policy, conditions, definitions) per state and effective date
3. Assemble declarations page: extract all required fields from policy data → populate per form specifications
4. Select and order endorsements: apply attachment rules (mandatory vs. optional, sequencing requirements)
5. Generate schedule attachments: additional insured schedules, location schedules, vehicle schedules
6. Code sandbox: validate complete policy package against coverage intent — confirm all elected coverages are present and correctly reflected
7. Generate formatted policy document: PDF with correct pagination, form numbers, edition dates
8. Route to agency or underwriter for final review on complex commercial policies [HITL — optional for simple personal lines]
9. Distribute via DocuSign connector (for e-delivery consent) or physical mail instruction
10. For certificate of insurance requests: parse ACORD 25 request → verify coverage exists → generate certificate in <5 minutes
11. Archive policy documents with version control: store all versions for regulatory retention requirements
12. Alert at mid-term endorsement: recheck document accuracy if coverage parameters change

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Document Parser | Form library ingestion, policy data extraction |
| Code Sandbox | Form selection rules, completeness validation |
| DocuSign | E-delivery and e-signature |
| Email | Policy delivery, certificate distribution |
| Slack | Agent notification of policy issuance |
| Salesforce | Policy system integration |

**Revenue Model**

- **Per-policy issued:** $4 (full document assembly vs. $15–$40 manual)
- **Subscription:** $2,500/month (unlimited issuance for personal lines)
- **Enterprise:** $7,500/month (commercial lines with complex form logic + certificate management)

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| Policy issuance turnaround | 3–10 days | 2–4 hours |
| Document assembly error rate | 6% | <0.5% |
| Certificate of insurance turnaround | 24–48 hours | <10 minutes |
| E&O claims from document errors | 2–4/year | <0.5/year |

**Target Customers**

- Independent agencies issuing certificates in high volume (construction, staffing)
- Regional P&C carriers with complex commercial form libraries
- Managing General Agents with multiple program products

---

### UC-12: Loss Run Report Preparation

**The Problem**

Loss run reports — the history of claims by policy for a given period — are the foundational document of commercial insurance. Every renewal, every new market submission, every reinsurance treaty requires current, accurate loss runs. Yet preparing loss runs is a **manual extract from the claims system**, requiring data analysts to run queries, format outputs, and verify accuracy. Insurance agents request loss runs constantly, and carriers have **5–10 business day turnaround** commitments they routinely miss. For insureds in the middle of a marketing exercise, delayed loss runs mean **delayed quotes and delayed coverage** — a customer experience failure.

**AgentVerse Solution**

An automated loss run agent fulfills any loss run request in minutes: pulling current claims data, computing the standardized exhibit, verifying data completeness, and delivering a formatted report directly to the requesting party.

**Agent Workflow**

1. Receive loss run request: named insured, policy number(s), policy period, requesting party (agent/underwriter/reinsurer)
2. Verify requestor authorization: confirm named insured authorization for data release [HITL for non-standard requestors]
3. Pull claims data from claims management system: all claims for requested policy and period
4. Compile loss run exhibit: claim number, date of loss, date reported, cause code, status, paid/reserve/incurred amounts
5. Apply development factors to open claims: compute estimated ultimate for IBNR development
6. Flag claims with coverage issues, subrogation potential, or litigation status
7. Compute summary statistics: number of claims, total incurred, loss ratio, frequency, severity
8. Format standardized loss run exhibit: policy period summary + claim detail in standard ACORD format
9. Verify data completeness: check for missing loss dates, zero-reserve open claims, suspicious data
10. Generate formatted PDF loss run report with carrier letterhead
11. Deliver via email to requesting agent or underwriter within 15 minutes of request
12. Archive loss run with version timestamp → track all requests for audit and compliance

**MCP Connectors / Tools**

| Connector | Purpose |
|---|---|
| Code Sandbox | Claims data aggregation, development factor application |
| Document Parser | Prior loss run comparison |
| Email | Automated report delivery |
| Slack | Internal request queue management |
| Salesforce | Policy and requester verification |
| AWS | Claims data warehouse connection |

**Revenue Model**

- **Per-loss run:** $15 (automated vs. $75–$150 manual analyst time)
- **Subscription:** $1,500/month (unlimited loss run generation)
- **Enterprise:** $4,000/month (loss run + actuarial development analysis + reinsurance package)

**ROI Metrics**

| Metric | Before | After |
|---|---|---|
| Loss run fulfillment time | 5–10 business days | <15 minutes |
| Data analyst hours/month on loss runs | 80 hours | 5 hours |
| Loss run accuracy errors | 4–8% of requests | <0.3% |
| Agent satisfaction with loss run service | 2.9/5 | 4.7/5 |

**Target Customers**

- P&C carriers handling commercial lines renewals
- TPAs and self-insured entities reporting to excess carriers
- Insurance agencies preparing client marketing submissions

---

## Monetization Strategy

### Tier 1 — InsurOps Starter ($1,299/month)

Designed for independent agencies and small-to-mid carriers (< $50M GWP).

**Includes:**
- Claims intake and triage automation (up to 300 claims/month)
- Policy document generation (up to 500 policies/month)
- Loss run automation (unlimited requests)
- Basic fraud scoring (claims flagging, no SIU workflow)
- HITL gates for coverage denials and reserve changes
- Email and Slack integrations
- Standard audit trail

**Target ACV:** $15,588

---

### Tier 2 — Carrier Pro ($5,999/month)

Designed for regional P&C carriers and specialty MGAs ($50M–$500M GWP).

**Includes:**
- Everything in InsurOps Starter
- Full fraud detection with SIU referral workflow
- Underwriting assistance (unlimited submissions)
- KYC/AML customer onboarding
- Regulatory filing automation (up to 10 states)
- Premium calculation verification (all new issues and renewals)
- Reinsurance data preparation (annual treaty support)
- Cross-sell opportunity engine
- Priority support with insurance domain CSM

**Target ACV:** $71,988

---

### Tier 3 — Enterprise Insurance Platform ($20,000+/month)

Designed for national carriers, specialty program carriers, and large TPAs ($500M+ GWP).

**Includes:**
- Everything in Carrier Pro
- Multi-line, multi-state full regulatory compliance suite (all 50 states)
- Custom actuarial models and rate-adequacy monitoring
- Full reinsurance analytics and catastrophe data management
- Claims adjudication support with coverage counsel integration
- Multi-tenant architecture for TPA client programs
- Dedicated insurance solution architect + actuarial consultant
- SLA: 99.9% uptime, insurance-grade data residency, SOC2 Type II
- Custom policy admin system integration

**Target ACV:** $240,000–$1M+

---

## Sample AgentManifest

```yaml
# AgentVerse Manifest — Claims Intake and Triage Agent
# Domain: Insurance | Version: 2.2.0

agent:
  id: claims-intake-triage
  name: "Claims Intake and Triage Agent"
  version: "2.2.0"
  description: >
    Autonomous FNOL processor: verifies coverage, extracts loss details, 
    scores fraud indicators, sets initial reserve, and routes claim to 
    appropriate adjuster pool with complete triage dossier.
  owner: claims-operations
  tenant: acme-insurance
  classification: CONFIDENTIAL-PHI

goal_template: >
  Process First Notice of Loss for claim {claim_reference}. 
  Policy: {policy_number}. Reported loss: {loss_description}.
  Verify coverage, score complexity and fraud, set initial reserve, 
  and route to appropriate adjuster.

planner:
  model: claude-3-7-sonnet
  max_iterations: 18
  replan_on_failure: true
  time_limit_seconds: 300  # 5-minute FNOL processing target

executor:
  model: claude-3-5-haiku
  tools:
    - document_parser
    - code_sandbox
    - web_search
    - browser_automation

verifier:
  model: claude-3-7-sonnet
  success_criteria:
    - "Coverage verified against policy in force"
    - "Fraud score computed with documented indicators"
    - "Initial reserve set with methodology documented"
    - "Claim routed to adjuster with complete triage dossier"
    - "Claimant acknowledgment sent"

connectors:
  - id: policy-admin-system
    connector: mcp://salesforce/v1
    auth: oauth2
    config:
      object_types: [Policy__c, Coverage__c, Endorsement__c]
  - id: claims-management-system
    connector: mcp://jira/v1
    auth: oauth2
    config:
      project_key: CLAIMS
      issue_type: Claim
  - id: twilio
    connector: mcp://twilio/v1
    auth: api_key
    config:
      from_number: ${TWILIO_CLAIMS_NUMBER}
  - id: slack
    connector: mcp://slack/v1
    auth: oauth2
    config:
      claims_ops_channel: "#claims-operations"
      siu_channel: "#siu-referrals"
      cat_channel: "#cat-response"
  - id: pagerduty
    connector: mcp://pagerduty/v1
    auth: api_key
    config:
      cat_service_id: ${PAGERDUTY_CAT_SERVICE}
  - id: docusign
    connector: mcp://docusign/v1
    auth: oauth2
  - id: aws
    connector: mcp://aws/v1
    auth: iam_role
    config:
      region: us-east-1
      s3_claims_bucket: ${S3_CLAIMS_BUCKET}

hitl:
  gates:
    - id: coverage-denial
      trigger: "coverage appears to be excluded or not triggered by reported facts"
      approvers: [role:senior-adjuster, role:coverage-counsel]
      timeout_hours: 4
      escalation: pagerduty
      documentation_required: true
    - id: large-reserve
      trigger: "initial reserve recommendation > $25,000"
      approvers: [role:claims-supervisor]
      timeout_hours: 2
      escalation: slack
    - id: siu-referral
      trigger: "fraud score > 70"
      approvers: [role:siu-supervisor]
      timeout_hours: 1
      escalation: pagerduty
    - id: catastrophe-event
      trigger: "loss location within active CAT event boundary"
      approvers: [role:cat-manager]
      timeout_hours: 0.5
      escalation: pagerduty
    - id: litigation-flag
      trigger: "attorney representation indicated at FNOL"
      approvers: [role:senior-adjuster, role:claims-counsel]
      timeout_hours: 2

reserve_guidelines:
  auto_set_below_usd: 25000
  methodology: "initial_facts_triangulation"
  reserve_model: "s3://claims-config/reserve-models/auto-physical-damage.json"

fraud_scoring:
  auto_refer_above: 70
  enhanced_investigation_above: 40
  iso_claim_search: true
  social_media_check: true

cost_governance:
  max_llm_spend_per_claim_usd: 1.50
  max_daily_spend_usd: 800.00
  cost_allocation: LAE  # Loss Adjustment Expense for financial reporting

audit:
  enabled: true
  immutable: true
  retention_days: 3650  # 10 years for statute of limitations
  export_formats: [json, pdf, csv]
  pii_masking: partial  # mask SSN/DOB in logs, retain in encrypted evidence vault
  chain_of_custody: true
  regulatory_hold: true

memory:
  long_term: true
  learnings:
    - "Store fraud indicator patterns by coverage line and claim type"
    - "Track reserve accuracy by claim type and complexity score"
    - "Log adjuster routing outcomes for workload optimization"

compliance:
  jurisdictions: [US-all-states]
  regulations: [HIPAA, state-fair-claims-practices-acts, GDPR]
  bad_faith_protection: true  # document every coverage decision with policy basis
```

---

## Competitive Displacement

| Incumbent | Weakness | Displacement Strategy |
|---|---|---|
| **Guidewire ClaimCenter** | $10M–$50M implementation; 18–36 month deployment; does not include AI decisioning | Position as AI intelligence layer on top of Guidewire — not a replacement; sell to Guidewire customers frustrated by AI gap |
| **Duck Creek Technologies** | Policy admin focused; minimal AI; requires extensive consulting to extend | Target Duck Creek carriers adding AI programs — AgentVerse extends Duck Creek capabilities immediately |
| **Mitchell / CCC One** | Auto-focused; narrow vertical; no commercial lines or life coverage | Broader cross-line capability; sell to multi-line carriers seeking unified platform |
| **Sapiens ALIS** | Life insurance focused; slow innovation cycles | Target life insurers dissatisfied with transformation pace |
| **Shift Technology** | Fraud detection only; no broader claims or operations automation | AgentVerse includes fraud *and* all other claims operations; one platform vs. point solution |
| **Majesco** | Core system modernization focus; long implementation timelines | Deploy AgentVerse in 90 days while Majesco implementation drags; expand from there |

**Displacement Motions:**

1. **Fraud ROI land:** Lead with fraud detection (pure cost savings, zero false-promise risk) → prove ROI in 90 days → expand to claims and underwriting
2. **Claims velocity pitch:** Time-to-close reduction is a measurable metric every claims VP tracks → show 40% cycle time reduction in proof of concept
3. **Regulatory compliance urgency:** Target carriers under state regulatory scrutiny — offer compliance filing automation as an urgent, defensible purchase

---

## Implementation Timeline

### Week 1–2: Foundation and Policy System Integration
- [ ] Provision AgentVerse insurance tenant with PHI-compliant data handling
- [ ] Connect policy administration system (Salesforce, Guidewire, or equivalent)
- [ ] Connect claims management system (Jira or existing CMS)
- [ ] Configure RBAC: Adjuster, Senior Adjuster, Claims Supervisor, SIU, Compliance, Actuary, Underwriter roles
- [ ] Define all HITL gates: coverage denial, large reserve, SIU referral, litigation flag
- [ ] Establish 10-year audit trail retention with immutability

### Week 3–4: Claims Operations Core
- [ ] Activate claims intake and triage (UC-2) — begin processing live FNOL
- [ ] Activate fraud detection (UC-3) — first 500 claims scored
- [ ] Establish fraud score baseline from first cohort
- [ ] Claims team training on HITL approval interface and SIU workflow

### Month 2: Underwriting and Documents
- [ ] Activate underwriting assistance (UC-1) — connect to submission intake
- [ ] Activate policy document generation (UC-11) — first issuance batch
- [ ] Activate loss run automation (UC-12) — immediate time-to-value
- [ ] Activate premium calculation verification (UC-6) for new issues

### Month 3: Compliance and Intelligence
- [ ] Activate KYC/AML onboarding (UC-5)
- [ ] Activate regulatory filing automation (UC-8) — first state
- [ ] Activate coverage adjudication support (UC-9) for complex commercial claims
- [ ] First compliance audit trail review with Chief Compliance Officer

### Month 4–6: Full Deployment
- [ ] Activate renewal campaign automation (UC-4) — first renewal cycle
- [ ] Activate cross-sell opportunity engine (UC-10)
- [ ] Activate reinsurance data preparation (UC-7) — ahead of treaty renewal season
- [ ] Executive dashboard live: loss ratio, combined ratio contribution from AI programs
- [ ] QBR with actuarial team: measure combined ratio impact, fraud loss reduction, LAE reduction
