# AgentVerse × Recruitment & Staffing

> **Tagline:** Fill roles faster. Screen smarter. Never lose a candidate to a slow process.

---

## Executive Summary

India's staffing industry is valued at ₹32,000 crore with over 10 million placements per year, spanning IT/ITeS, BFSI, manufacturing, retail, and healthcare verticals. Despite the scale, the sector is plagued by manual, fragmented workflows — a typical recruiter spends 6–8 hours a day on screening, scheduling, and paperwork, with 40–60% of candidate drop-offs caused purely by process delays. AgentVerse deploys purpose-built autonomous agents that replace every repetitive step of the recruitment lifecycle — from multi-source sourcing and AI-powered resume screening to background verification orchestration and onboarding document generation — reducing time-to-fill from 42 days to under 10 days. With native integrations across Naukri, LinkedIn, iimjobs, GitHub, Keka, and Zoho People, AgentVerse becomes the coordination layer between every tool a staffing firm already uses. The result: staffing firms can 3× their placement volumes without adding headcount, while corporate HR teams reduce cost-per-hire by 55–70%.

---

## Use Cases

---

### UC-1: Bulk Resume Screening for High-Volume Hiring (IT/BPO)

**The Problem**
IT and BPO companies routinely receive 500–2,000 applications per open role. Human screening of this volume takes 4–6 recruiter-days per role, introduces subjective bias, and still results in qualified candidates being missed. The industry average shortlisting accuracy is only 58%, causing repeated interview rounds and extended time-to-fill — costing an average ₹45,000 per unfilled seat per week in lost productivity.

**AgentVerse Solution**
AgentVerse deploys a Resume Screening Agent that ingests bulk application files (PDF, DOCX, LinkedIn exports) via the Document Parsing connector, extracts structured candidate profiles, and scores each against a role-specific rubric built from the Job Description. The agent applies configurable weightings for skills, years of experience, education pedigree, and employment gap flags. It integrates with ATS platforms (Zoho Recruit, Keka) to write scores back directly, creating a ranked shortlist within minutes. HITL approval gates allow a senior recruiter to spot-check the top 10% before shortlist emails are dispatched automatically.

**Agent Workflow**
1. Ingest JD from ATS via **Zoho Recruit MCP connector** or email parse via **Gmail/Outlook MCP**
2. Parse JD with **Document Parser** to extract required skills, experience bands, qualifications
3. Pull all new applications from ATS using **Zoho Recruit MCP bulk export**
4. For each resume, invoke **PDF/DOCX Document Parser** to extract structured fields
5. Score candidate against JD rubric using **LLM Executor** (semantic skill matching)
6. Flag employment gaps >6 months, location mismatches, and missing must-haves
7. Enrich profiles with public LinkedIn data via **LinkedIn MCP connector**
8. Rank all candidates and bucket into: Strong Shortlist / Borderline / Reject
9. Write scores and labels back to ATS via **Zoho Recruit MCP write API**
10. Trigger **HITL approval gate** — senior recruiter reviews top 20 ranked candidates
11. On approval, dispatch shortlist notification emails via **Gmail MCP** to recruiters
12. Log audit trail of every scoring decision to **Audit Trail module** for bias review

**Tools Used:** Zoho Recruit MCP, Gmail MCP, Outlook MCP, Document Parser (PDF/DOCX), LinkedIn MCP, LLM Executor, HITL Gateway, Audit Trail

**Revenue Model:** ₹8–15 per resume screened (volume pricing); ₹50,000/month platform subscription for unlimited screening up to 10,000 resumes/month

**ROI:** Reduces recruiter screening time by 85%; saves ₹2.1 lakh/month for a team of 5 recruiters handling 3,000 applications/month

**Target Customers:** IT product/services companies, BPO/KPO firms, staffing agencies handling IT mandates, RPO providers

---

### UC-2: Candidate Sourcing from LinkedIn, Naukri, and GitHub Simultaneously

**The Problem**
Sourcing passive and active candidates from multiple job boards is manual, repetitive, and inconsistent. A recruiter spends 2–3 hours per role building search strings, running searches across 3–4 platforms, deduplicating results, and entering data into the ATS. For niche tech roles, 70% of the best candidates are passive and not on job boards at all — they're on GitHub, StackOverflow, or Behance.

**AgentVerse Solution**
AgentVerse's Sourcing Agent runs parallel searches across Naukri, LinkedIn, iimjobs, GitHub, and AngelList (for startup roles) using structured search queries derived from the JD. The agent deduplicates across platforms by email/phone hash, enriches profiles with publicly available work samples, and auto-ranks candidates by fit score before loading them into the ATS pipeline. GitHub profiles are parsed for repository quality, tech stack diversity, and open-source contribution volume, giving technical recruiters signal that no job board can provide.

**Agent Workflow**
1. Parse JD to extract sourcing criteria via **Document Parser + LLM Executor**
2. Generate Boolean and keyword search strings for each platform
3. Run Naukri search via **Naukri MCP connector** (keyword + location + experience filters)
4. Run LinkedIn Recruiter search via **LinkedIn MCP connector**
5. Run GitHub talent search via **GitHub MCP** — filter by language, stars, recent commits
6. Scrape iimjobs and AngelList profiles via **Browser RPA agent**
7. Deduplicate all results by hashing name + contact + location fields
8. Enrich each unique candidate with **LinkedIn profile fetch** for latest employment
9. Score each enriched profile against JD rubric using **LLM Executor**
10. Load ranked candidates into ATS (Zoho Recruit / Keka) via **ATS MCP write**
11. Send personalised outreach emails/InMails via **Gmail MCP** to top 30 candidates
12. Track open/response rates and log to **Audit Trail** for sourcing channel analytics

**Tools Used:** Naukri MCP, LinkedIn MCP, GitHub MCP, Browser RPA, Document Parser, LLM Executor, Gmail MCP, Zoho Recruit MCP, Audit Trail

**Revenue Model:** ₹500–1,200 per sourced and loaded candidate; ₹75,000/month for unlimited sourcing on 5 active JDs

**ROI:** 3× sourcing pipeline volume; 70% reduction in sourcer hours per role; ₹1.8 lakh/month saved per sourcing specialist

**Target Customers:** IT staffing firms, executive search firms, in-house TA teams at 500+ employee companies, RPO providers

---

### UC-3: Interview Scheduling Coordination Across Panels

**The Problem**
Coordinating 4–6 interview rounds across multiple panel members, candidates, and time zones wastes an estimated 45 minutes per candidate per round. For companies with 50 active positions and 8 candidates per position, this is 300+ hours of coordinator time per month. Interview no-shows cost ₹8,000–25,000 per incident in recruiter and interviewer time.

**AgentVerse Solution**
AgentVerse's Scheduling Agent reads availability from all panel members' calendars, proposes optimal slots, negotiates with candidates over email/WhatsApp, and books confirmed slots with auto-generated video conference links. It monitors for conflicts 24 hours before each interview and proactively reschedules or sends reminders. Post-interview, it triggers feedback form dispatch and follows up with interviewers who haven't submitted feedback within the defined SLA.

**Agent Workflow**
1. Receive interview request from ATS when candidate moves to interview stage via **Zoho Recruit MCP**
2. Fetch all panel members' calendar availability via **Google Calendar MCP**
3. Identify 3 optimal slots using **LLM Executor** (considering time zones, interviewer load)
4. Send candidate schedule proposal via **Gmail MCP** with slot selection link
5. On candidate confirmation, book calendar slots via **Google Calendar MCP write**
6. Generate Zoom/Google Meet link via **Zoom MCP / Google Meet MCP**
7. Send confirmation with agenda, JD, and interviewer bios via **Gmail MCP**
8. 24-hour before reminder sent to candidate and interviewers via **Gmail + WhatsApp MCP**
9. Monitor for cancellations and trigger reschedule flow automatically
10. Post-interview: dispatch feedback form link to each interviewer via **Gmail MCP**
11. Escalate non-response on feedback after 24 hours via **Slack MCP** to TA lead
12. Update interview status and feedback in ATS via **Zoho Recruit MCP write**

**Tools Used:** Zoho Recruit MCP, Google Calendar MCP, Gmail MCP, Zoom MCP, WhatsApp MCP, Slack MCP, LLM Executor, Audit Trail

**Revenue Model:** ₹1,500/month per recruiter seat; ₹25,000/month for 20-seat corporate TA team

**ROI:** Saves 280 hours/month for a team managing 50 active roles; ₹4.2 lakh/month coordinator cost saving

**Target Customers:** Corporate TA teams (BFSI, IT, Retail), staffing agencies, campus recruitment teams

---

### UC-4: Background Verification Orchestration

**The Problem**
Background verification (BGV) is a mandatory step for 95% of white-collar hires, yet the process is fragmented across 6–10 data points (education, employment, police, court, address, reference checks). Staffing firms wait 7–21 days for BGV completion, creating offer rescission risk and candidate frustration. BGV failures catch firms by surprise — 30% of employment gaps disclosed in resumes are misrepresented.

**AgentVerse Solution**
AgentVerse's BGV Orchestration Agent submits verification requests to BGV vendors (AuthBridge, IDfy, SpringVerify) via API, tracks status across all checks in real time, and escalates anomalies to the HR team with structured discrepancy reports. The agent cross-checks employment dates against EPFO records via the DigiLocker MCP, validates degree certificates against National Academic Depository, and compiles a go/no-go BGV summary report automatically. HITL gates ensure legal/HR review before adverse action.

**Agent Workflow**
1. Receive candidate consent and data package from ATS via **Zoho Recruit MCP**
2. Submit BGV request to AuthBridge/IDfy via **BGV Vendor MCP / REST API connector**
3. Cross-check PAN and Aadhaar details via **Aadhaar/PAN verification API MCP**
4. Submit education verification request to **National Academic Depository API**
5. Fetch EPFO employment history via **DigiLocker MCP** (with candidate consent)
6. Monitor BGV vendor portal for check status updates via **Browser RPA** (polling)
7. Parse received BGV reports via **Document Parser** to extract discrepancy flags
8. Cross-reference candidate-declared history against verified data using **LLM Executor**
9. Generate structured BGV summary report with pass/flag/fail per check via **LLM Executor**
10. Trigger **HITL approval gate** if any discrepancy is flagged — HR/legal review
11. Update BGV status in ATS and HRMS via **Zoho Recruit MCP + Keka MCP**
12. Archive all documents and decisions to **Audit Trail** for compliance retention

**Tools Used:** Zoho Recruit MCP, BGV Vendor API MCP, Aadhaar/PAN MCP, National Academic Depository API, DigiLocker MCP, Browser RPA, Document Parser, LLM Executor, HITL Gateway, Keka MCP, Audit Trail

**Revenue Model:** ₹150–400 per BGV case orchestrated; ₹45,000/month for corporate HR teams (500+ hires/year)

**ROI:** 60% reduction in BGV turnaround time; 100% compliance documentation coverage; ₹3 lakh/year saved vs. dedicated BGV coordinator

**Target Customers:** Staffing firms, BFSI HR teams, IT companies, Background Check vendors themselves (as resell)

---

### UC-5: Offer Letter Generation and Onboarding Paperwork

**The Problem**
Generating and dispatching offer letters, NDAs, appointment letters, and onboarding forms is a high-touch, error-prone manual task. A single offer letter mistake — wrong CTC, wrong designation, wrong joining date — can delay joining by days and damage employer brand. For a staffing firm processing 200 offers/month, offer management alone consumes 3 FTE hours daily.

**AgentVerse Solution**
AgentVerse's Offer Generation Agent pulls approved compensation details from the HRMS/ATS, populates pre-approved offer letter templates, routes the document for e-signature, and triggers the full onboarding checklist upon acceptance. The agent handles template versioning (ensuring the latest legal-approved template is always used), applies CTC structuring logic (HRA, LTA, PF, bonus split), and dispatches the complete joining kit — including bank details form, nominee form, and IT declaration — within minutes of approval.

**Agent Workflow**
1. Receive offer approval trigger from HRMS/ATS via **Keka MCP / Zoho Recruit MCP**
2. Fetch approved compensation structure and role details from **HRMS MCP**
3. Select correct offer letter template by grade/location/entity via **Document Template Engine**
4. Populate template fields with candidate and compensation data using **LLM Executor**
5. Apply CTC split calculation (PF, HRA, LTA, Bonus, Gratuity) via **LLM Executor**
6. Generate final PDF offer letter via **Document Generator MCP**
7. Route offer letter for internal approver e-sign via **DocuSign MCP / Leegality MCP**
8. Dispatch signed offer to candidate email with deadline for acceptance via **Gmail MCP**
9. Track acceptance status and send reminders via **Gmail MCP** at D-3, D-1
10. On acceptance, trigger onboarding checklist — bank form, Aadhaar, PAN, photo via **Keka MCP**
11. Auto-populate employee record in HRMS on document collection completion
12. Log full offer lifecycle to **Audit Trail** for compliance and offer analytics

**Tools Used:** Keka MCP, Zoho Recruit MCP, Document Parser, LLM Executor, Document Generator MCP, DocuSign MCP, Leegality MCP, Gmail MCP, Audit Trail

**Revenue Model:** ₹200 per offer processed; ₹30,000/month for companies doing 100+ offers/month

**ROI:** 90% reduction in offer processing time; eliminates offer errors; ₹1.8 lakh/month saved for 200-offer/month staffing firm

**Target Customers:** Staffing agencies, mid-to-large IT companies, BFSI HR teams, HRMS vendors (white-label)

---

### UC-6: Client Requirement Analysis and Job Description Generation

**The Problem**
Poor job descriptions are the root cause of mis-hires — 72% of candidates who underperform in the first year were hired against inaccurate or vague JDs. Staffing firms receive client briefs in unstructured formats (emails, calls, PDFs) and spend 2–3 hours converting each brief into a structured JD. JD quality also directly impacts sourcing efficiency — keyword-rich JDs attract 3× more relevant applications.

**AgentVerse Solution**
AgentVerse's JD Generation Agent processes client requirement documents or call transcripts, extracts role parameters, cross-references them against a library of 5,000+ role templates, and generates SFIA-aligned job descriptions with embedded Boolean search strings ready for job board posting. The agent also runs a bias detection pass, flagging gender-coded language and age-discriminatory phrasing before the JD is published. Integration with Naukri and LinkedIn ensures the JD is posted automatically upon approval.

**Agent Workflow**
1. Ingest client requirement brief via **Email MCP (Gmail/Outlook)** or meeting transcript via **Zoom MCP**
2. Transcribe any audio/video brief using **Speech-to-Text MCP** if audio format
3. Parse requirement document using **Document Parser** to extract structured role attributes
4. Cross-reference role attributes against JD template library via **Vector Knowledge Base MCP**
5. Generate first-draft JD using **LLM Executor** (skills, responsibilities, qualifications, location)
6. Run bias detection pass via **LLM Executor** — flag gendered, age, or caste-coded language
7. Suggest bias-neutral alternatives and apply rewrites automatically
8. Generate Boolean search strings for Naukri, LinkedIn, and GitHub sourcing
9. Route JD draft to client/recruiter for review via **HITL approval gate**
10. On approval, post JD to Naukri via **Naukri MCP**, LinkedIn via **LinkedIn MCP**
11. Publish to company careers page via **CMS MCP / Website API connector**
12. Archive final JD with version history to **Audit Trail** and JD library

**Tools Used:** Gmail MCP, Outlook MCP, Zoom MCP, Speech-to-Text MCP, Document Parser, Vector Knowledge Base MCP, LLM Executor, HITL Gateway, Naukri MCP, LinkedIn MCP, CMS MCP, Audit Trail

**Revenue Model:** ₹500–1,500 per JD generated and posted; ₹20,000/month subscription for unlimited JD generation

**ROI:** 95% reduction in JD creation time; 35% improvement in application-to-interview ratio; ₹60,000/month saved for 10-JD/week teams

**Target Customers:** Staffing firms, RPO providers, corporate HR, recruitment marketing agencies

---

### UC-7: Talent Pool Warm-Up and Engagement Campaigns

**The Problem**
Staffing firms maintain databases of 50,000–500,000 candidates that go cold within 6–12 months. Reactive sourcing from cold databases results in 40–60% unresponsive candidate rates and forces expensive re-sourcing. Firms with warm, engaged talent pools fill roles 18 days faster on average — a ₹9 lakh per role productivity advantage.

**AgentVerse Solution**
AgentVerse's Talent Engagement Agent segments the candidate database by skill cluster, last active date, and industry, then runs personalised engagement campaigns via email, WhatsApp, and LinkedIn. Campaigns include job alerts, industry salary benchmarks, skill upgrade resources, and referral program invitations. The agent tracks engagement signals (opens, clicks, profile updates), re-scores candidates based on fresh activity, and surfaces "re-engaged" candidates to recruiters when a relevant role opens.

**Agent Workflow**
1. Pull full candidate database from ATS via **Zoho Recruit MCP** (with consent flags)
2. Segment candidates by skill cluster, experience band, last activity date via **LLM Executor**
3. Identify candidates approaching 6-month inactivity threshold for priority warm-up
4. Fetch current job market data for relevant roles via **Web Search MCP (Perplexity/Tavily)**
5. Generate personalised email content (salary trends, matching roles, skill tips) via **LLM Executor**
6. Launch email campaign sequence via **Gmail MCP** (Day 1, Day 7, Day 14 cadence)
7. Send WhatsApp nudges to non-email-openers via **WhatsApp MCP**
8. Track opens, clicks, and replies; update engagement score in ATS
9. For high-engagement candidates, trigger personalised recruiter outreach via **Slack MCP** alert
10. Capture referral nominations via form and add referred candidates to ATS via **Zoho Recruit MCP**
11. Generate monthly talent pool health dashboard via **LLM Executor + Reporting MCP**
12. Archive campaign performance to **Audit Trail** for DPDP compliance documentation

**Tools Used:** Zoho Recruit MCP, LLM Executor, Web Search MCP, Gmail MCP, WhatsApp MCP, Slack MCP, Reporting MCP, Audit Trail

**Revenue Model:** ₹2/candidate/month engagement fee; ₹35,000/month for database of 50,000 candidates

**ROI:** 55% improvement in database re-engagement rate; 18-day faster time-to-fill from warm pool; ₹54 lakh/year incremental placement revenue for mid-size staffing firm

**Target Customers:** Staffing agencies, executive search firms, RPO providers, in-house TA teams with large ATS databases

---

### UC-8: Payroll Processing for Contract/Temp Workforce

**The Problem**
Staffing firms managing 1,000–50,000 contract workers process payroll across multiple client billing cycles, pay codes, overtime rules, and statutory deduction structures. Manual payroll for contract workers generates ₹180 crore in error-related penalties and re-processing costs annually across the Indian staffing industry. PF, ESIC, PT, and TDS calculations vary by state, employment type, and salary slab — making error-free processing nearly impossible at scale without automation.

**AgentVerse Solution**
AgentVerse's Payroll Agent ingests attendance and timesheet data from client systems, applies the correct pay structure for each worker's employment type (fixed-term, CLRA-covered, CTC), computes gross-to-net pay including all statutory deductions, generates payslips, and initiates bank transfer batches. The agent handles mid-month joiners and exits, LOP (Loss of Pay) calculations, and variable pay inputs from performance systems. It generates ECR (Electronic Challan cum Return) for PF and ESIC submissions automatically.

**Agent Workflow**
1. Ingest attendance data from client biometric/HRMS via **Keka MCP / Attendance API MCP**
2. Validate attendance against approved leaves and holidays calendar
3. Fetch approved variable pay and incentive inputs from performance system via **HRMS MCP**
4. Apply pay structure rules (CTC splits, LOP, overtime) per employment contract via **LLM Executor**
5. Calculate statutory deductions (PF 12%, ESIC 0.75%, PT, TDS) per applicable slabs
6. Generate gross-to-net payroll register via **Payroll Calculation Engine MCP**
7. Route payroll register for finance approval via **HITL approval gate**
8. On approval, generate individual payslips as PDFs via **Document Generator MCP**
9. Initiate salary disbursement batch file for bank upload via **Bank SFTP MCP / NEFT batch**
10. Generate PF ECR file and ESIC contribution file for statutory uploads
11. Dispatch payslips to each employee via **Email MCP** with password protection
12. Archive payroll run to **Audit Trail** for statutory compliance and audit readiness

**Tools Used:** Keka MCP, Attendance API MCP, HRMS MCP, LLM Executor, Payroll Engine MCP, HITL Gateway, Document Generator MCP, Bank SFTP MCP, Gmail MCP, Audit Trail

**Revenue Model:** ₹25–80 per payslip generated; ₹1.5 lakh/month for 5,000-employee payroll processing

**ROI:** 80% reduction in payroll processing time; zero statutory penalty risk; ₹45 lakh/year cost reduction for 10,000-worker staffing firm

**Target Customers:** Staffing firms (Teamlease, Quess, Randstad India), CLRA-registered contractors, temp workforce managers

---

### UC-9: Compliance Management (Contract Labour Regulation, PF/ESIC)

**The Problem**
India's Contract Labour (Regulation & Abolition) Act requires principal employers and contractors to maintain 14 distinct registers, obtain establishment registration, and renew contractor licenses annually. Non-compliance penalties range from ₹500 to ₹50,000 per violation, and with 73 state-level amendments, compliance tracking is a full-time job. 60% of staffing firms have received at least one compliance notice in the last 3 years.

**AgentVerse Solution**
AgentVerse's Compliance Agent maintains a living compliance calendar calibrated to the firm's locations, client sites, and workforce types. It monitors due dates for PF ECR submissions, ESIC contribution payments, PT challan deposits, CLRA license renewals, and Form D/Form XIII register maintenance. The agent auto-drafts required filings, routes them for approval, submits to the appropriate government portals via Browser RPA, and generates compliance certificates for client audits on demand.

**Agent Workflow**
1. Maintain compliance calendar from firm's registration and license database via **Document Parser**
2. Monitor due dates daily and trigger alerts 30/15/7/1 days in advance via **Slack MCP + Email MCP**
3. Fetch monthly payroll data for PF ECR preparation from payroll system via **Keka MCP**
4. Generate PF ECR file and submit to EPFO unified portal via **Browser RPA**
5. Generate ESIC contribution file and submit to ESIC portal via **Browser RPA**
6. Calculate Professional Tax liability per state slab and generate payment challans
7. Prepare CLRA Form D (Register of Workmen) updates from HRMS data via **LLM Executor**
8. Submit CLRA license renewal application on respective state Labour Dept portals via **Browser RPA**
9. Track acknowledgement receipts and update compliance tracker via **Document Parser**
10. Generate compliance status dashboard for client and internal audit via **Reporting MCP**
11. Trigger **HITL gate** for any filing where penalty risk >₹10,000 for human review
12. Archive all filings, receipts, and challans to **Audit Trail** with tamper-proof timestamping

**Tools Used:** Keka MCP, Document Parser, Slack MCP, Gmail MCP, Browser RPA, LLM Executor, HITL Gateway, Reporting MCP, Audit Trail

**Revenue Model:** ₹15,000–50,000/month compliance management retainer per entity; ₹500 per on-demand compliance certificate

**ROI:** 100% on-time filing rate; ₹35 lakh/year in avoided penalties for a 10-state staffing operation

**Target Customers:** Staffing firms, CLRA-registered principal employers, construction contractors, facility management companies

---

### UC-10: Timesheet Collection and Invoice Generation (Staffing Billing)

**The Problem**
Staffing billing is inherently complex — timesheets arrive late, in inconsistent formats (Excel, email, WhatsApp photos), from multiple client contacts. A typical staffing firm with 500 deployed workers processes 2,000 timesheet rows/month manually, taking 8–12 FTE-days. Billing errors average 4–6% of invoice value, and disputed invoices delay collections by 30–45 days, stranding ₹2–8 crore in working capital at any given time.

**AgentVerse Solution**
AgentVerse's Billing Agent automates the complete timesheets-to-invoice cycle: it sends automated collection reminders, ingests timesheets in any format (email attachment, WhatsApp image, web form, HRMS export), normalises data using Document Parser and OCR, applies billable rate cards, generates GST-compliant invoices, and dispatches them to client accounts payable contacts. It tracks payment against due dates and escalates overdue invoices with a structured collections workflow.

**Agent Workflow**
1. At billing cycle start, send timesheet submission reminder to all client contacts via **Gmail + WhatsApp MCP**
2. Ingest received timesheets from email attachments via **Gmail MCP + Document Parser (OCR)**
3. Normalise timesheet data to standard format using **LLM Executor** (handling varied layouts)
4. Cross-validate hours against project codes and approved working hours from **Keka MCP**
5. Flag discrepancies and send clarification requests to client contacts via **Email MCP**
6. Receive confirmed hours and apply rate cards from contract master via **LLM Executor**
7. Generate GST-compliant invoice PDF with HSN codes via **Document Generator MCP**
8. Route invoice for internal approval via **HITL approval gate** (>₹5 lakh invoices)
9. Dispatch invoice to client AP via **Gmail MCP** and upload to client portal via **Browser RPA**
10. Track payment status against due date; send reminders at D+7, D+15, D+30
11. Escalate invoices overdue >45 days to collections team via **Slack MCP** alert
12. Reconcile payments received against invoices via **Tally/Zoho Books MCP** and update ledger

**Tools Used:** Gmail MCP, WhatsApp MCP, Document Parser (OCR), LLM Executor, Keka MCP, Document Generator MCP, HITL Gateway, Browser RPA, Tally MCP, Zoho Books MCP, Slack MCP, Audit Trail

**Revenue Model:** ₹50 per invoice generated; ₹40,000/month for staffing firms with 500+ deployed workers

**ROI:** 92% reduction in billing cycle time; 4% billing error elimination = ₹24 lakh/year recovered on ₹6 crore monthly billing

**Target Customers:** Staffing firms, facility management companies, IT project-based contractors, housekeeping/security service providers

---

### UC-11: Diversity Hiring Analytics and Bias Detection in JDs

**The Problem**
Despite 81% of Indian organisations stating diversity hiring as a strategic priority, only 27% track diversity metrics across the funnel. Gender representation in tech hiring drops from 46% application rate to 28% shortlist to 19% offer rate — a 27-point attrition through the funnel that is almost entirely process-driven. JDs with masculine-coded language receive 42% fewer female applications.

**AgentVerse Solution**
AgentVerse's Diversity Analytics Agent instruments the entire hiring funnel with demographic and source-of-hire data, runs statistical disparity analysis across each stage, and identifies where specific groups are disproportionately filtered out. The JD Bias Detector analyses every active and historical JD against a 2,400-term bias dictionary (gender, age, caste, ability), flags problematic phrases, and suggests inclusive rewrites. Monthly diversity dashboards are auto-generated for CHRO and DEI team review.

**Agent Workflow**
1. Pull full hiring funnel data from ATS by role, stage, gender, source via **Zoho Recruit MCP**
2. Anonymise PII fields for analysis compliance via **Data Masking MCP**
3. Compute stage-by-stage demographic pass-through rates via **LLM Executor + Analytics MCP**
4. Identify statistically significant drop-off points (>10% disparity) using chi-square analysis
5. Scan all active JDs against 2,400-term bias dictionary via **LLM Executor**
6. Generate per-JD bias report with flagged terms and suggested rewrites
7. Analyse sourcing channel diversity contribution (LinkedIn vs. campus vs. referral vs. Naukri)
8. Benchmark firm's diversity metrics against industry data via **Web Search MCP**
9. Generate CHRO diversity dashboard (funnel waterfall, source attribution, bias score trend) via **Reporting MCP**
10. Trigger Slack alert for CHRO when diversity metric falls below configured threshold via **Slack MCP**
11. Route bias-flagged JDs back to recruiter for revision via **Zoho Recruit MCP workflow**
12. Archive monthly diversity report to **Audit Trail** for board and ESG reporting

**Tools Used:** Zoho Recruit MCP, Data Masking MCP, LLM Executor, Analytics MCP, Web Search MCP, Reporting MCP, Slack MCP, Audit Trail

**Revenue Model:** ₹25,000/month DEI analytics module; ₹5,000 per one-time diversity audit report

**ROI:** 35% improvement in gender diversity at shortlist stage; ₹1.2 crore/year value from reduced mis-hire rate (diverse teams perform 35% better per McKinsey)

**Target Customers:** Enterprise HR (BFSI, IT, FMCG), ESG-reporting listed companies, DEI consultancies, MNC India operations

---

### UC-12: Campus Recruitment Coordination

**The Problem**
Campus hiring season (August–March) sees companies simultaneously managing 50–200 college partnerships, pre-placement talks, aptitude drives, technical rounds, and offer issuance for 500–5,000 students. Co-ordination failures — missed SPOC communications, test link errors, offer letter delays — regularly result in offer rescissions and campus relationship damage that locks companies out of premium institutions for 2–3 years.

**AgentVerse Solution**
AgentVerse's Campus Recruitment Agent manages the entire campus season lifecycle: college SPOC communication, pre-placement talk scheduling, registration link management, aptitude test coordination (HackerRank/AMCAT integration), interview slot allocation, offer letter dispatch, and post-season analytics. The agent maintains a college relationship score and flags institutions where engagement is below benchmark, enabling proactive relationship management before the next hiring season.

**Agent Workflow**
1. Ingest campus hiring plan (target colleges, headcount, role types) from HR via **Email/HRMS MCP**
2. Segment colleges by tier, location, and discipline match to open roles via **LLM Executor**
3. Draft and dispatch personalised Pre-Placement Talk (PPT) invitation emails to TPOs via **Gmail MCP**
4. Track responses and schedule PPT slots on company representatives' calendars via **Google Calendar MCP**
5. Generate registration microsite/form for each campus drive via **Form Builder MCP**
6. Dispatch registration link to eligible students via college SPOC **Email MCP**
7. Coordinate aptitude/coding test schedule with **HackerRank MCP / AMCAT API connector**
8. Shortlist qualified students and schedule technical/HR interview rounds via **Scheduling Agent**
9. Generate and dispatch offer letters to selected students via **Document Generator + Gmail MCP**
10. Track offer acceptance rates per campus and flag low-acceptance colleges for HITL review
11. Generate season-end analytics: campus ROI, cost-per-hire by college, diversity breakdown via **Reporting MCP**
12. Update college relationship database with season performance for next year planning via **Audit Trail + CRM MCP**

**Tools Used:** Gmail MCP, Google Calendar MCP, LLM Executor, Form Builder MCP, HackerRank MCP, AMCAT API MCP, Document Generator MCP, Reporting MCP, HITL Gateway, CRM MCP, Audit Trail

**Revenue Model:** ₹500 per campus candidate processed; ₹2.5 lakh/season for companies running 50+ campus drives

**ROI:** 65% reduction in campus coordination effort; 22% improvement in offer acceptance rate; ₹35 lakh/year saved for a company hiring 1,000 campus candidates annually

**Target Customers:** IT product companies (Infosys, Wipro, TCS in-house), BFSI graduate programs, FMCG management trainee programs, staffing firms running campus RPO

---

## Monetization Strategy

### Tier 1 — Starter (Boutique Staffing Firms / In-house TA, <500 placements/year)
**₹29,999/month**
- Up to 5 active JDs
- Resume screening (2,000 resumes/month)
- Interview scheduling (100 interviews/month)
- Offer letter generation (50 offers/month)
- 2 recruiter seats
- Standard ATS integrations (Zoho Recruit, Keka)
- Email support

### Tier 2 — Professional (Mid-size Staffing Firms / Corporate TA, 500–5,000 placements/year)
**₹89,999/month**
- Unlimited active JDs
- Resume screening (15,000 resumes/month)
- Full sourcing automation (Naukri + LinkedIn + GitHub)
- BGV orchestration (100 cases/month)
- Payroll processing (500 contract workers)
- Compliance management (3 states)
- Diversity analytics dashboard
- 15 recruiter seats
- Priority Slack support + dedicated CSM

### Tier 3 — Enterprise (Large Staffing Firms / RPO Providers, >5,000 placements/year)
**₹2,49,999/month + ₹20 per incremental placement**
- All Tier 2 features
- Unlimited resume screening and sourcing
- Campus recruitment module (unlimited colleges)
- Full payroll processing (unlimited contract workers)
- Compliance management (pan-India, all states)
- White-label option for client-facing portals
- Custom ATS/HRMS integrations
- SLA-backed uptime (99.9%)
- Dedicated implementation engineer
- Quarterly business reviews

---

## Sample AgentManifest

```yaml
# AgentVerse AgentManifest
# Domain: Recruitment & Staffing
# Agent: RecruitmentOrchestrator v1.0

agent:
  id: avx-recruitment-orchestrator
  name: RecruitmentOrchestrator
  version: "1.0.0"
  domain: recruitment-staffing
  description: >
    End-to-end autonomous recruitment lifecycle management covering
    sourcing, screening, scheduling, BGV, offer, and compliance.

triggers:
  - type: ats_event
    source: zoho_recruit
    event: new_jd_created
  - type: ats_event
    source: zoho_recruit
    event: candidate_stage_changed
  - type: schedule
    cron: "0 8 * * 1-5"
    task: talent_pool_warmup
  - type: schedule
    cron: "0 9 1 * *"
    task: payroll_cycle_initiation
  - type: schedule
    cron: "0 7 * * *"
    task: compliance_calendar_check

tools:
  - name: zoho_recruit_mcp
    type: mcp_connector
    auth: oauth2
    scopes: [read_jobs, write_candidates, update_stages]
  - name: linkedin_mcp
    type: mcp_connector
    auth: oauth2
    scopes: [talent_search, profile_read, inmail_send]
  - name: naukri_mcp
    type: mcp_connector
    auth: api_key
    scopes: [search_candidates, post_job, download_resumes]
  - name: github_mcp
    type: mcp_connector
    auth: github_token
    scopes: [search_users, read_profile, list_repos]
  - name: google_calendar_mcp
    type: mcp_connector
    auth: oauth2
    scopes: [read_calendars, write_events]
  - name: gmail_mcp
    type: mcp_connector
    auth: oauth2
    scopes: [send_email, read_inbox, manage_labels]
  - name: whatsapp_mcp
    type: mcp_connector
    auth: business_api_key
  - name: keka_mcp
    type: mcp_connector
    auth: api_key
    scopes: [read_employees, write_payroll, read_attendance]
  - name: document_parser
    type: builtin
    capabilities: [pdf_parse, docx_parse, ocr]
  - name: browser_rpa
    type: builtin
    capabilities: [web_navigate, form_fill, screenshot, download]
  - name: llm_executor
    type: builtin
    model: anthropic/claude-3-5-sonnet
  - name: docusign_mcp
    type: mcp_connector
    auth: oauth2
  - name: web_search_mcp
    type: mcp_connector
    provider: tavily
    auth: api_key
  - name: slack_mcp
    type: mcp_connector
    auth: bot_token
    scopes: [post_message, create_channel]

hitl:
  enabled: true
  gates:
    - id: bgv_adverse_action
      description: "Human review before adverse hiring action on BGV discrepancy"
      approvers: [hr_lead, legal_lead]
      sla_hours: 4
    - id: offer_approval
      description: "Finance approval for offers above Grade 7 or >₹25 LPA CTC"
      approvers: [finance_head, hr_head]
      sla_hours: 2
    - id: compliance_filing
      description: "Legal sign-off before filing with penalty risk >₹10,000"
      approvers: [compliance_officer]
      sla_hours: 8

memory:
  short_term: redis
  long_term: postgres_pgvector
  candidate_embedding_model: voyage-3

governance:
  audit_trail: enabled
  data_retention_days: 2555  # 7 years for statutory compliance
  pii_masking: enabled
  dpdp_consent_check: required
  bias_detection: enabled

cost_controls:
  max_daily_spend_inr: 5000
  alert_threshold_inr: 4000
  llm_call_budget_per_jd: 200

notifications:
  slack_channel: "#recruitment-ops"
  escalation_email: "ta-head@company.com"
  daily_summary: enabled
```
