# Wealth Management & Investment Advisory
### *Autonomous AI agents that act as a tireless research desk, compliance officer, and client communication engine — all in one.*

---

## Executive Summary

India's wealth management industry sits atop a ₹118 lakh crore opportunity — ₹78 lakh crore in mutual fund AUM and ₹40 lakh crore in PMS/AIF mandates — yet the advisory layer remains chronically under-staffed, with one registered investment adviser for every 10,000 investors. AgentVerse deploys autonomous agents that handle portfolio surveillance, SEBI-compliant suitability checks, tax optimisation, and client communication at machine speed, freeing human advisers to focus on relationships and judgment calls. The platform integrates with BSE/NSE data feeds, CAMS/KFintech registrar APIs, income-tax portals, and document repositories to close the loop from signal to action. Firms using AgentVerse report a 60-70 % reduction in analyst grunt work and a 3× increase in the number of clients a single adviser can service.

---

## Use Cases

---

### UC-1: Client Portfolio Review and Rebalancing Analysis

**The Problem**
SEBI's Investment Adviser regulations (IA Regulations 2020) mandate periodic suitability reviews, yet 74 % of advisory firms conduct them less than once a year due to analyst bandwidth constraints. Manual portfolio drift analysis for a 500-client book takes 3-4 weeks per cycle, during which uncorrected drift can cost clients 1.2-2.4 % annualised returns.

**AgentVerse Solution**
An agent continuously fetches live NAV and market-price data for every holding across every client folio, computes target-vs-actual allocation drift, and flags breaches beyond a configurable threshold. It cross-references SEBI's risk-category matrix against the client's KYC risk profile and generates a personalised rebalancing memo in plain English. The memo is reviewed by the adviser inside the HITL gateway before the system emails it to the client — maintaining regulatory human-in-the-loop requirements.

**Agent Workflow**
1. **Scheduler trigger** fires at 8 PM on the last trading day of the month for every active client.
2. **CAMS/KFintech MCP connector** fetches full folio data (scheme name, units, ISIN, current NAV).
3. **BSE/NSE market data MCP** pulls live prices for direct equity and ETF holdings.
4. **Portfolio math tool** computes equity/debt/gold/cash allocation percentages vs the client's target IPS.
5. **SEBI risk-category lookup** maps each fund to its SEBI-defined risk label and validates suitability against KYC risk score.
6. **Drift analysis agent** identifies over/under-weight positions exceeding ±5 % threshold.
7. **Tax-impact estimator** calculates LTCG/STCG exposure before recommending any switch, surfacing tax-efficient redemption sequences.
8. **Document generation tool** produces a PDF rebalancing memo with supporting charts using templated markdown.
9. **HITL gateway** presents the memo to the adviser for approval or edits; adviser approves within the app.
10. **Email MCP** dispatches the approved memo to the client with a calendar invite for a follow-up call.

**Tools Used:** CAMS/KFintech MCP, BSE/NSE data MCP, document generation, HITL gateway, email MCP, PDF renderer, scheduler

**Revenue Model:** ₹8,000–₹25,000/month per advisory firm (per-client-seat pricing at ₹15–50/client/month)

**ROI:** Reduces per-client review time from 45 minutes to 4 minutes; an adviser handling 500 clients saves 341 hours/cycle — equivalent to ₹8.5 lakh in analyst cost per year.

**Target Customers:** SEBI-registered Investment Advisers (RIAs), wealth management arms of NBFCs, private banks, family offices

---

### UC-2: Mutual Fund Recommendation with SEBI Risk Suitability

**The Problem**
Mis-selling of mutual funds costs Indian retail investors an estimated ₹12,000 crore annually in mis-matched risk-return outcomes, and SEBI imposes penalties up to ₹25 lakh per mis-selling instance on distributors. Building a compliant, personalised recommendation workflow manually for thousands of prospects is operationally infeasible.

**AgentVerse Solution**
The agent conducts a structured digital KYC and risk-profiling interview via a web form or WhatsApp, analyses the investor's income, liabilities, time horizon, and stated risk tolerance, and maps the result to SEBI's risk-category spectrum. It then screens the AMFI fund universe using quantitative filters (Sharpe ratio, rolling returns, expense ratio, AUM stability) and returns a shortlist with a written rationale. Every recommendation is logged with a timestamp and suitability justification for audit compliance.

**Agent Workflow**
1. **WhatsApp/web form trigger** initiates the onboarding flow when a new prospect submits their details.
2. **KYC document parser** (OCR MCP) extracts income, PAN, and address from uploaded PAN card and Aadhaar.
3. **Risk-profiling questionnaire agent** asks 12 SEBI-prescribed questions and computes a risk score (Conservative/Moderate/Aggressive).
4. **AMFI data MCP** pulls the full fund universe with category, sub-category, and latest factsheet data.
5. **Fund screener agent** applies quantitative filters: 3-year rolling returns > category median, expense ratio < 75th percentile, AUM > ₹500 crore.
6. **Suitability mapper** aligns shortlisted funds with the investor's risk score and investment horizon.
7. **Rationale writer** generates plain-language explanations for each recommendation, citing risk metrics.
8. **Conflict-of-interest checker** flags any recommended fund where the distributor earns above-average commission, appending a SEBI-mandated disclosure.
9. **HITL gateway** routes the recommendation set to the human adviser for sign-off.
10. **CRM MCP** logs the interaction, recommendation, and suitability rationale against the client record with timestamp for SEBI audit trail.

**Tools Used:** OCR MCP, WhatsApp MCP, AMFI data MCP, HITL gateway, CRM MCP, document generation, scheduler

**Revenue Model:** ₹5,000–₹18,000/month per distribution firm; ₹2/recommendation API call for platform integrations

**ROI:** Adviser onboards 30 new clients/month vs the previous 8; incremental AUM of ₹4-5 crore/adviser/month.

**Target Customers:** AMFI-registered mutual fund distributors, digital-first advisory platforms, bank wealth desks

---

### UC-3: Insurance Portfolio Optimization Across Life/Health/Term Policies

**The Problem**
The average Indian HNI holds 6-9 insurance policies across 3-4 insurers, with estimated over-insurance or mis-allocation of 22-35 % of annual premium spend (₹1.8 lakh crore industry premium base). No single view of coverage exists, leading to duplicate riders, coverage gaps at critical life stages, and missed IRDAI-mandated free-look cancellations.

**AgentVerse Solution**
AgentVerse builds a unified insurance inventory by parsing all policy documents via OCR, extracting sum assured, premium, maturity, nomination, and exclusion data. A gap analysis agent compares actual coverage against HLV (Human Life Value) and medical inflation-adjusted health requirements, then surfaces actionable recommendations: cancel duplicates, port health policies, top up term cover, or add critical illness riders. All actions are presented for client sign-off via a branded PDF report.

**Agent Workflow**
1. **Email MCP** scans the client's inbox for policy documents using keyword filters on insurer sender domains.
2. **Document parser** (OCR + NLP) extracts structured fields: policy number, insurer, sum assured, premium, maturity date, nominee.
3. **Insurance inventory builder** consolidates all policies into a single structured data model per client.
4. **HLV calculator** computes required life cover based on income, liabilities, and dependents using standard actuarial formulas.
5. **Health coverage gap tool** calculates inflation-adjusted hospitalisation need vs current health cover, flagging sub-₹25 lakh family floater as under-insured.
6. **Duplicate rider detector** flags policies with overlapping critical illness, accidental disability, or waiver-of-premium riders.
7. **Premium optimiser** models alternate product combinations (pure term + super top-up) that provide equivalent cover at 20-40 % lower premium.
8. **Recommendation writer** generates a section-by-section optimisation report with ₹ impact for each action.
9. **HITL gateway** routes to the adviser with a priority queue sorted by premium-savings opportunity.
10. **Email MCP** sends the branded PDF report to the client with an e-signature link for any cancellation or porting initiations.

**Tools Used:** Email MCP, OCR document parser, HITL gateway, PDF renderer, e-signature MCP, CRM MCP, web search MCP (insurer portals)

**Revenue Model:** ₹1,500–₹8,000/client/year for insurance advisory firms; ₹500 per audit report for D2C clients

**ROI:** Identifies average ₹42,000/year premium savings per HNI client; adviser earns ₹8,000-15,000 advisory fee per optimisation.

**Target Customers:** IRDAI-registered insurance brokers, wealth management firms, independent financial advisers

---

### UC-4: Estate Planning Document Preparation (Wills, Nomination Updates)

**The Problem**
Only 11 % of Indians have a written will, and nomination mismatches across bank accounts, demat holdings, insurance policies, and provident fund accounts cause an estimated ₹1 lakh crore in inheritance disputes annually. Document preparation by a solicitor costs ₹15,000-₹2 lakh and takes 4-12 weeks.

**AgentVerse Solution**
AgentVerse agents compile a complete asset inventory from linked accounts, demat statements, and document uploads, then identify every asset class requiring a nomination or testamentary disposition. The agent drafts a structured will template pre-populated with asset schedules, executor details, and specific bequests based on a guided Q&A with the client. All outputs are handed to a legal professional via HITL before finalisation — the agent handles the data gathering and drafting, the lawyer handles the legal execution.

**Agent Workflow**
1. **Demat statement parser** (OCR) extracts equity, mutual fund, and bond holdings from CDSL/NSDL CAS statements.
2. **Bank account aggregator MCP** (Account Aggregator framework) fetches FD, savings, and current account balances with nomination status.
3. **Insurance inventory tool** (from UC-3 pipeline) surfaces all policies with current nominee vs desired beneficiary.
4. **EPF/PPF portal MCP** checks provident fund nomination status via EPFO portal scrape.
5. **Client interview agent** conducts a structured chat interview covering heirs, specific bequests, executor preferences, and guardianship wishes.
6. **Asset schedule builder** generates a tabular schedule of all assets, values, and intended beneficiaries.
7. **Will drafting agent** populates a legal will template with the asset schedule, standard clauses, and client-specified instructions; flags jurisdictional nuances (Hindu Succession Act vs Indian Succession Act).
8. **Nomination gap report** lists every account/policy where current nominee differs from the will's beneficiary, with direct update links.
9. **HITL gateway** routes the draft will and nomination gap report to a partner solicitor for review and sign-off.
10. **Document delivery MCP** sends the finalised will draft and nomination update checklist to the client via encrypted email with a DocuSign e-signature flow.

**Tools Used:** OCR parser, Account Aggregator MCP, EPFO portal MCP, HITL gateway, DocuSign MCP, email MCP, document generation

**Revenue Model:** ₹5,000 per estate planning package (one-time); ₹2,000/year for annual review and update service

**ROI:** Reduces estate planning preparation time from 3 weeks to 2 days; solicitor earns 3× more clients per month.

**Target Customers:** Wealth advisory firms, estate planning law firms, chartered accountants with HNI clients, family offices

---

### UC-5: Tax-Loss Harvesting Opportunity Identification

**The Problem**
Tax-loss harvesting in equity portfolios can improve post-tax returns by 0.8-1.4 % per year, representing ₹9,360 crore in unrealised tax savings across India's demat account holders. Yet 97 % of retail and HNI investors never harvest losses because the identification and wash-sale rules are too complex to execute manually.

**AgentVerse Solution**
An agent runs a nightly scan across every client's equity and mutual fund portfolio, identifies unrealised losses eligible for harvesting, checks 30-day wash-sale windows to avoid SEBI's deemed-dividend rules, and ranks opportunities by tax saving quantum. For each opportunity it models the reinvestment — either in a near-identical fund/stock or a cash hold — and quantifies the net after-tax benefit. The complete action list is delivered to the adviser before market open.

**Agent Workflow**
1. **Scheduler** triggers the scan at 6 AM every trading day during Q3 (October-December) tax-planning season.
2. **Demat/folio data MCP** fetches current market value vs average acquisition cost for all holdings.
3. **Unrealised loss screener** identifies positions with >₹10,000 unrealised short-term or long-term capital loss.
4. **SEBI wash-sale checker** verifies no purchase of the same ISIN in the preceding 30 days (prevents disallowance).
5. **LTCG/STCG tax calculator** quantifies the tax saving at applicable slab rate (30 % for STCL, 12.5 % for LTCL above ₹1.25 lakh threshold).
6. **Reinvestment modeller** finds the closest correlation-equivalent ETF or fund for same-day reinvestment to maintain market exposure.
7. **Opportunity ranker** sorts all identified harvests by net tax saving after transaction costs and exit loads.
8. **Report generator** produces a daily "Tax Harvest Opportunities" PDF with sell-and-reinvest pairs, quantum, and deadline.
9. **HITL gateway** routes the top-20 opportunities to the adviser for single-click approval per transaction.
10. **Order management MCP** (broker API integration) places the approved sell and buy orders simultaneously to minimise market-timing risk.

**Tools Used:** Demat MCP, broker API MCP, HITL gateway, PDF renderer, scheduler, tax calculation engine

**Revenue Model:** ₹12,000/year per adviser seat; ₹500 success fee per ₹1 lakh tax saved for premium tier

**ROI:** Average client saves ₹55,000-₹1.8 lakh in taxes annually; advisory firm charges 10-20 % of savings as fee.

**Target Customers:** Portfolio Management Services (PMS), wealth management firms, SEBI RIAs, CA firms with HNI clients

---

### UC-6: Real Estate vs Financial Asset Allocation Modeling

**The Problem**
60-65 % of Indian household wealth is locked in real estate, generating net rental yields of 1.5-2.5 % against a 7.5 % risk-free rate. Yet advisers lack a quantitative framework to have the "shift to financial assets" conversation with clients — a ₹220 lakh crore reallocation opportunity.

**AgentVerse Solution**
The agent ingests the client's complete balance sheet — real estate valuations, loan liabilities, rental income, and financial portfolio — and runs a multi-scenario optimisation model. It compares the IRR of holding property vs selling and deploying into financial assets, accounting for rental income, capital appreciation assumptions, EMI tax shields, and liquidity needs. The output is a scenario comparison report with specific reallocation recommendations calibrated to the client's liquidity horizon.

**Agent Workflow**
1. **Client intake form** collects property details: location, purchase price, current valuation (via Proptech API), outstanding loan, monthly rental.
2. **PropTech MCP** (MagicBricks/99acres API) fetches current market valuation and rental yield benchmarks for the locality.
3. **Loan statement parser** (OCR) extracts outstanding principal, EMI schedule, and prepayment penalty from the loan account statement.
4. **Real estate IRR calculator** computes 5/10/15-year holding IRR under bear/base/bull capital appreciation scenarios (3 %/6 %/9 %).
5. **Financial portfolio IRR modeller** models equivalent ₹ deployed in a diversified financial portfolio (equity + debt + gold) with Monte Carlo simulation.
6. **Tax comparison engine** calculates LTCG on property sale (with indexation) vs capital gains on financial assets over the same horizon.
7. **Liquidity need overlay** models liquidity events (child's education, retirement) against the illiquidity of real estate.
8. **Scenario report builder** generates a side-by-side 3-scenario PDF with ₹-for-₹ comparison, recommendation summary, and suggested reallocation steps.
9. **HITL gateway** routes the report to the adviser with discussion talking points for the client meeting.
10. **CRM MCP** logs the analysis against the client record and schedules a 6-month follow-up to revisit property valuation.

**Tools Used:** PropTech MCP, OCR parser, Monte Carlo modelling engine, PDF renderer, HITL gateway, CRM MCP, web search MCP

**Revenue Model:** ₹8,000 per client reallocation analysis; ₹3,500/year for annual balance sheet review

**ROI:** A single client reallocation of ₹50 lakh from property to financial assets generates ₹75,000-₹1.5 lakh in AUM-linked advisory fees annually.

**Target Customers:** Wealth managers, SEBI RIAs, financial planners, NBFC loan-against-property desks

---

### UC-7: Retirement Corpus Calculation and Systematic Withdrawal Planning

**The Problem**
India has approximately 480 million workers in the unorganised sector with zero structured retirement savings, and even formal-sector retirees face a longevity risk gap: average life expectancy has risen to 72 years while retirement plans assume death at 75, creating a ₹35 lakh crore systemic under-saving problem.

**AgentVerse Solution**
The agent runs a comprehensive retirement readiness assessment: current corpus, monthly savings rate, expected retirement date, inflation-adjusted lifestyle cost, healthcare inflation (12 % in India), and longevity probability. It models the required corpus under multiple scenarios and prescribes a step-up SIP schedule. Post-retirement, it designs a bucket-based Systematic Withdrawal Plan (SWP) with tax optimisation and annual rebalancing triggers. A dynamic alert fires whenever savings trajectory diverges from plan.

**Agent Workflow**
1. **Client profiling agent** collects current age, retirement age, current savings, monthly surplus, and post-retirement lifestyle cost via guided web form.
2. **Inflation modeller** applies differentiated inflation rates: 6 % general, 12 % healthcare, 8 % travel/leisure for the retirement spending basket.
3. **Corpus calculator** runs FV calculations for 15 scenarios (early/on-time/late retirement × conservative/moderate/aggressive returns).
4. **Savings gap identifier** computes the monthly SIP required today to close the gap and models 10 % annual step-up impact.
5. **NPS/EPF integrator** (EPFO/NSDL MCP) fetches existing provident fund and NPS Tier-I balances to credit against the required corpus.
6. **Bucket strategy planner** divides the retirement corpus into 3 buckets: 0-3 year liquidity (FD/liquid fund), 4-10 year moderate (balanced advantage), 10+ year growth (equity).
7. **SWP optimiser** schedules monthly withdrawals from the tax-optimal bucket, ensuring no LTCG breach in equity bucket during the first 12 months.
8. **Longevity stress test** simulates corpus exhaustion under 90th-percentile longevity (age 92) and recommends annuity purchase threshold.
9. **Retirement readiness report generator** produces a 15-page PDF with charts, scenarios, and action plan.
10. **Scheduler MCP** sets quarterly corpus-tracking alerts; if actual vs projected savings deviates >8 %, an automated course-correction memo is generated and sent via email MCP.

**Tools Used:** EPFO/NPS MCP, PDF renderer, email MCP, scheduler MCP, HITL gateway, CRM MCP, Monte Carlo engine

**Revenue Model:** ₹15,000 one-time retirement plan fee; ₹6,000/year ongoing monitoring; bundled at ₹18,000/year

**ROI:** Adviser converts 4× more retirement planning conversations into fee-paying mandates; average client lifetime value increases by ₹2.4 lakh over 10 years.

**Target Customers:** SEBI RIAs, financial planning firms, HR/payroll platforms offering employee financial wellness, NPS Points of Presence (POPs)

---

### UC-8: NRI Investment Compliance (FEMA, FATCA, CRS)

**The Problem**
India has 32 million NRIs and PIOs with an estimated ₹18.7 lakh crore in India-linked investments. Non-compliance with FEMA (repatriation rules), FATCA (US-linked NRIs), and CRS (OECD reporting) exposes investors to penalties up to 3× the transaction value under FEMA and criminal prosecution under FATCA. Yet 68 % of NRI investors have at least one non-compliant account.

**AgentVerse Solution**
AgentVerse runs a compliance audit across all India-linked financial accounts, classifying each by NRE/NRO/FCNR status, repatriability, FATCA/CRS reportability, and FEMA transaction limits. It auto-generates Form 15CA/15CB data inputs, flags accounts that need conversion from resident to NRO status, and tracks annual repatriation utilized vs permissible limits. Alerts fire before filing deadlines with pre-filled forms ready for CA sign-off.

**Agent Workflow**
1. **NRI onboarding agent** collects country of residence, tax residency certificates, NRE/NRO account details, and investment list via a structured intake form.
2. **Account classification tool** maps each bank/demat/MF account to its correct FEMA category (NRE = freely repatriable; NRO = current income only).
3. **FEMA transaction tracker** monitors annual remittances against the USD 1 million basic travel quota and business income limits.
4. **FATCA screening agent** checks if the NRI is a US Person (citizenship, green card, substantial presence test) and flags all accounts that require FATCA disclosure.
5. **CRS reportability mapper** identifies accounts reportable to OECD treaty countries based on the NRI's country of tax residence.
6. **Form 15CA/15CB pre-filler** extracts remittance purpose, amount, and TDS rate from transaction records and pre-fills the IT portal XML upload format.
7. **Compliance gap report** lists every non-compliant account with the specific FEMA/FATCA/CRS regulation breached and recommended remediation action.
8. **Document bundler** compiles TRC (Tax Residency Certificate), Form 10F, and bank declarations into a single ZIP for the CA.
9. **HITL gateway** routes the compliance package to the CA/compliance officer for review and digital sign-off.
10. **Email MCP** dispatches the compliance report and pre-filled forms to the client and CA with deadline reminders 30/15/7 days before due dates.

**Tools Used:** OCR document parser, IT portal MCP, email MCP, HITL gateway, CRM MCP, document generation, scheduler MCP

**Revenue Model:** ₹12,000/year per NRI client for compliance monitoring; ₹25,000 for initial full-audit package

**ROI:** A CA firm handling 200 NRI clients automates 80 % of compliance prep work, freeing 600 hours/year worth ₹18 lakh in billing capacity.

**Target Customers:** NRI-focused wealth advisory firms, CA firms with NRI practice, FEMA consultants, private banks with NRI desks

---

### UC-9: AIF/PMS Portfolio Performance Reporting for HNIs

**The Problem**
AIF (Alternative Investment Fund) Category I, II, III and PMS managers are required by SEBI to send monthly/quarterly performance reports to investors, but generating personalised, SEBI-compliant reports for each investor across multiple strategies costs ₹8,000-₹20,000 per investor per report cycle when done by a fund administrator. For a 200-investor PMS, that is ₹40 lakh/year in report generation costs alone.

**AgentVerse Solution**
AgentVerse automates end-to-end performance reporting: it ingests the fund administrator's NAV and transaction data, computes TWR/MWR returns, benchmarks against NIFTY/BSE indices, calculates performance fees under the high-water mark, and generates a SEBI-compliant PDF personalised to each investor's entry NAV and holding period. Delivery is automated via email with a portal link, and all reports are archived with digital timestamps for SEBI inspection readiness.

**Agent Workflow**
1. **Fund admin data MCP** pulls daily NAV, transaction ledger, and investor-level folio data from the fund administration system (CAMS PMS or proprietary).
2. **Return calculator** computes Time-Weighted Return (TWR) and Money-Weighted Return (XIRR) for each investor based on their specific cash flow dates.
3. **Benchmark fetcher** (NSE/BSE MCP) pulls NIFTY 500, BSE 500, and strategy-specific benchmark index returns for the same period.
4. **Alpha/attribution analyser** decomposes returns into market beta, sector alpha, and stock-selection alpha using Brinson-Hood-Beebower model.
5. **Performance fee calculator** applies the manager's high-water mark and hurdle rate logic to compute accrued performance fee per investor.
6. **Portfolio composition renderer** generates a holdings snapshot as of report date with market value, % weight, 1-month return, and sector classification.
7. **Risk metric engine** computes Sharpe ratio, maximum drawdown, Sortino ratio, and volatility for the period.
8. **Personalised report generator** populates a SEBI-compliant HTML/PDF template with all investor-specific data, blending fund-level commentary with investor-level performance.
9. **Bulk dispatcher** (email MCP) sends personalised PDFs to each investor with a secure portal login link; tracks open/read receipts.
10. **Audit archive tool** stores all generated reports in a tamper-evident S3-compatible store with SHA-256 checksums for SEBI inspection.

**Tools Used:** Fund admin MCP, NSE/BSE data MCP, PDF renderer, email MCP, document generation, audit trail store, scheduler MCP

**Revenue Model:** ₹1,500/investor/report cycle for outsourced reporting; ₹2.5 lakh/month platform licence for large AMCs (500+ investors)

**ROI:** Reduces per-investor report cost from ₹12,000/year to ₹2,400/year; 5 lakh crore AIF industry saves ₹480 crore/year.

**Target Customers:** AIF managers (Cat I, II, III), PMS providers, fund administrators, family offices managing pooled vehicles

---

### UC-10: Client Financial Goal Tracking and Course-Correction Alerts

**The Problem**
Only 12 % of Indian investors who set financial goals with an adviser review progress more than once a year. Market volatility, life events, and SIP misses cause 38 % of goal-based plans to fall behind trajectory within 24 months, but advisers lack a scalable system to proactively alert every client when deviation occurs.

**AgentVerse Solution**
AgentVerse maintains a live financial goal registry for every client — child's education, home purchase, retirement, foreign travel — with funding status computed daily. When actual progress falls below the 90 % trajectory threshold, the agent automatically generates a personalised course-correction memo explaining the gap in rupee terms, modelling options (increase SIP, extend horizon, reduce goal amount), and recommending the least-disruptive path. The memo is delivered to the adviser for a 2-click send to the client.

**Agent Workflow**
1. **Goal registry** stores each client's named goals with target amount, target date, current allocated corpus, and monthly SIP amount.
2. **Daily NAV sync** (CAMS/KFintech MCP) updates current portfolio value for each goal's linked folios every evening.
3. **Trajectory engine** computes expected corpus on target date using future value formula with current SIP + existing corpus vs the required goal amount.
4. **Deviation detector** fires an alert when projected corpus falls below 90 % of required amount OR when 2 consecutive SIP payments are missed.
5. **Root cause analyser** identifies whether the gap is caused by: (a) market underperformance, (b) SIP miss, (c) changed goal amount, or (d) new liabilities.
6. **Course-correction modeller** generates 3 options: (a) increase SIP by X %, (b) extend goal date by N months, (c) reduce goal target by Y % — with ₹ impact quantified for each.
7. **Communication writer** drafts a WhatsApp/email message in the client's preferred language (Hindi/English) with empathetic framing and clear action buttons.
8. **HITL gateway** presents the message draft to the adviser; adviser selects the preferred course-correction option and approves the send.
9. **WhatsApp/email MCP** dispatches the personalised alert to the client with a link to the adviser's calendar for a quick call if needed.
10. **CRM MCP** logs the alert, the adviser's action, and the client's response; escalates to the branch head if no client response in 7 days.

**Tools Used:** CAMS/KFintech MCP, WhatsApp MCP, email MCP, HITL gateway, CRM MCP, scheduler MCP, document generation

**Revenue Model:** ₹4,000/adviser/month for goal-tracking module; bundled into full platform licence

**ROI:** Adviser retention rate on goal-based AUM improves from 71 % to 89 %; incremental AUM retention of ₹3.2 crore per adviser per year.

**Target Customers:** SEBI RIAs, MFD firms, digital wealth platforms, bank wealth management divisions

---

## Monetization Strategy

### Tier 1 — Starter (RIA / Solo Adviser)
**₹8,000/month** — Up to 200 clients, portfolio review + goal tracking + basic reports. Email and WhatsApp delivery included. HITL gateway with 5 adviser seats.

### Tier 2 — Growth (Boutique Wealth Firm)
**₹35,000/month** — Up to 2,000 clients, all 10 use cases active, PMS/AIF reporting module, NRI compliance, tax-loss harvesting. Unlimited HITL seats. CRM + broker API integrations. Dedicated onboarding support.

### Tier 3 — Enterprise (Private Bank / Large NBFC)
**₹2.5 lakh/month** — Unlimited clients, white-label portal, custom compliance workflows, full audit trail with SEBI export, API access for core-banking integration, SLA-backed uptime, dedicated customer success manager.

---

## Sample AgentManifest YAML

```yaml
agent_manifest:
  id: wealth-portfolio-reviewer-v2
  name: "WealthAdvisor — Portfolio Review & Rebalancing Agent"
  version: "2.4.0"
  domain: wealth_management
  tenant_tier: growth

  triggers:
    - type: schedule
      cron: "0 20 28-31 * *"   # last trading day of month at 8 PM
      description: "Monthly portfolio review cycle"
    - type: event
      source: cams_webhook
      event_type: folio_transaction
      description: "Ad-hoc review on any client transaction"

  goals:
    primary: "Generate SEBI-compliant rebalancing memos for all active clients with >5% allocation drift."
    secondary: "Identify tax-loss harvesting opportunities before each review cycle."

  tools:
    - id: cams_kfintech_mcp
      type: mcp_connector
      config:
        auth: oauth2
        scopes: [folio.read, transaction.read]
    - id: bse_nse_data_mcp
      type: mcp_connector
      config:
        feed: realtime_eod
    - id: hitl_gateway
      type: human_in_the_loop
      config:
        approval_required: true
        timeout_hours: 24
        escalation_email: compliance@adviserfirm.com
    - id: email_mcp
      type: mcp_connector
      config:
        provider: sendgrid
        from_domain: adviserfirm.com
    - id: pdf_renderer
      type: document_tool
      config:
        template: wealth_rebalancing_v3
        branding: tenant_logo
    - id: crm_mcp
      type: mcp_connector
      config:
        provider: salesforce
        module: wealth_client

  planner:
    model: claude-3-7-sonnet
    max_steps: 12
    replan_on_failure: true

  verifier:
    checks:
      - all_clients_processed: true
      - sebi_suitability_logged: true
      - hitl_approved_before_send: true
      - audit_trail_written: true

  governance:
    audit_trail: true
    data_classification: financial_pii
    retention_days: 2557   # 7 years per SEBI IA regulations
    hitl_mandatory: true
    rls_tenant_isolation: true

  escalation:
    on_llm_failure: notify_compliance_officer
    on_hitl_timeout: escalate_to_branch_head
    on_data_error: pause_and_alert
```
