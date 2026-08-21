"""Pre-built domain solutions catalog.

Each solution is a packaged deployment: agents + knowledge schemas + eval suite
+ guardrail bundle. Install atomically.
"""

from __future__ import annotations

from typing import Any

# Flagship solutions
DOMAIN_SOLUTIONS: list[dict[str, Any]] = [
    {
        "id": "sol-law-firm",
        "slug": "law-firm",
        "name": "Law Firm AI Operations",
        "domain": "legal",
        "description": (
            "Complete AI operations for law firms: matter intake, contract review, "
            "case research, deadline scheduling, and billing automation. "
            "Powered by citation-backed RAG and multi-hop legal document retrieval."
        ),
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Intake Agent",
                "description": "Handles new client intake and matter creation",
                "skill_ids": ["skill-summarize-compress", "skill-structured-reporting"],
                "system_prompt": (
                    "You are a law firm intake specialist. Create matter files, extract key "
                    "deadlines, and route to the appropriate practice group."
                ),
                "connectors": ["jira", "email"],
            },
            {
                "name": "Contract Review Agent",
                "description": "Reviews contracts for risk clauses and non-standard terms",
                "skill_ids": ["skill-data-extraction"],
                "system_prompt": (
                    "You are an expert contract reviewer. Identify risk clauses, missing "
                    "provisions, and non-standard terms. Always cite specific clauses."
                ),
                "connectors": ["knowledge"],
            },
            {
                "name": "Case Research Agent",
                "description": "Multi-hop legal research across precedents and statutes",
                "skill_ids": ["skill-web-research"],
                "system_prompt": (
                    "You are a legal researcher. Find relevant precedents, cite exact case "
                    "names and citations. Cross-reference multiple sources."
                ),
                "connectors": ["web_search", "knowledge"],
            },
        ],
        "knowledge_recipes": [
            {"name": "Contract Library", "description": "Upload firm contract templates"},
            {"name": "Precedent Database", "description": "Case law and statute references"},
        ],
        "workflows_config": [],
        "schedules_config": [],
        "policies_config": {},
        "onboarding_steps": [
            "Upload firm contract templates to Contract Library",
            "Configure Jira for matter tracking",
            "Set up email connector for client communications",
            "Run simulation dry-run with sample intake request",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-ecommerce",
        "slug": "e-commerce",
        "name": "E-Commerce AI Operations",
        "domain": "e_commerce",
        "description": (
            "Automate catalog management, order exception handling, and customer review responses."
        ),
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Catalog Operations Agent",
                "description": "Manages product catalog updates and categorization",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": (
                    "You manage e-commerce product catalog. Update product details, fix "
                    "categorization errors, and flag price anomalies."
                ),
                "connectors": ["database", "email"],
            },
            {
                "name": "Order Exception Agent",
                "description": "Handles failed orders, refunds, and escalations",
                "skill_ids": ["skill-summarize-compress"],
                "system_prompt": (
                    "Handle order exceptions professionally. For refunds, always verify "
                    "eligibility before processing. Escalate fraud indicators to human review."
                ),
                "connectors": ["jira", "email", "slack"],
            },
        ],
        "knowledge_recipes": [
            {"name": "Product Catalog", "description": "Import product database"},
        ],
        "workflows_config": [],
        "schedules_config": [],
        "policies_config": {},
        "onboarding_steps": [
            "Connect your order management system",
            "Import product catalog",
            "Configure email for customer communications",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-software-ops",
        "slug": "software-ops",
        "name": "Software Development AI",
        "domain": "software",
        "description": (
            "PR review, release notes generation, issue triage, and incident management."
        ),
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "PR Review Agent",
                "description": "Reviews pull requests for code quality and security",
                "skill_ids": ["skill-code-review"],
                "system_prompt": (
                    "Review pull requests thoroughly. Check for security vulnerabilities, "
                    "test coverage, and code quality. Provide specific, actionable feedback."
                ),
                "connectors": ["github"],
            },
            {
                "name": "Incident Manager Agent",
                "description": "Coordinates incident response and post-mortems",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": (
                    "Manage incidents systematically. Assess severity, coordinate response, "
                    "track resolution, and generate post-mortems."
                ),
                "connectors": ["github", "slack", "jira"],
            },
        ],
        "knowledge_recipes": [
            {"name": "Architecture Docs", "description": "System design documentation"},
            {"name": "Runbooks", "description": "Operational runbooks and procedures"},
        ],
        "workflows_config": [],
        "schedules_config": [],
        "policies_config": {},
        "onboarding_steps": [
            "Connect GitHub repository",
            "Upload architecture documentation",
            "Configure Slack for incident notifications",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-education",
        "slug": "education",
        "name": "Education AI Assistant",
        "domain": "education",
        "description": (
            "Curriculum assistant, grading with rubrics, and student progress tracking."
        ),
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Curriculum Assistant",
                "description": "Creates learning materials aligned to curriculum standards",
                "skill_ids": ["skill-structured-reporting", "skill-summarize-compress"],
                "system_prompt": (
                    "Create educational content that is age-appropriate, engaging, and aligned "
                    "to learning objectives. Always include assessment criteria."
                ),
                "connectors": ["knowledge"],
            },
        ],
        "knowledge_recipes": [
            {"name": "Curriculum Standards", "description": "Upload curriculum frameworks"},
        ],
        "workflows_config": [],
        "schedules_config": [],
        "policies_config": {},
        "onboarding_steps": [
            "Upload curriculum standards and learning objectives",
            "Configure grade level and subject area",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-finance",
        "slug": "finance",
        "name": "Finance Operations AI",
        "domain": "finance",
        "description": "Month-end close checklist, reconciliation, and spend analysis.",
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Close Checklist Agent",
                "description": "Manages month-end close tasks and status tracking",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": (
                    "Manage the month-end close process. Track checklist items, flag delays, "
                    "and produce status reports. Flag any discrepancies immediately."
                ),
                "connectors": ["jira", "email"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Close Procedures",
                "description": "Month-end close procedures and checklists",
            },
        ],
        "workflows_config": [],
        "schedules_config": [],
        "policies_config": {},
        "onboarding_steps": [
            "Upload close procedures",
            "Configure Jira for task tracking",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-operations",
        "slug": "operations",
        "name": "Operations AI Hub",
        "domain": "operations",
        "description": "Incident management, vendor onboarding, and SLA monitoring.",
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Operations Manager Agent",
                "description": "Monitors operations KPIs and escalates anomalies",
                "skill_ids": ["skill-structured-reporting", "skill-data-extraction"],
                "system_prompt": (
                    "Monitor operational metrics. Identify anomalies, SLA breaches, and "
                    "escalating issues. Generate actionable alerts."
                ),
                "connectors": ["jira", "slack"],
            },
        ],
        "knowledge_recipes": [
            {"name": "Vendor Catalog", "description": "Vendor agreements and SLA terms"},
        ],
        "workflows_config": [],
        "schedules_config": [],
        "policies_config": {},
        "onboarding_steps": [
            "Upload vendor SLA agreements",
            "Configure monitoring thresholds",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    # ── H5: AI Organization Architecture ────────────────────────────────────────
    {
        "id": "sol-ai-org",
        "slug": "ai-org-architecture",
        "name": "AI Organization Architecture",
        "domain": "enterprise",
        "description": "Full organizational AI — CEO synthesis agent, CTO engineering oversight, HR operations, and Finance reconciliation, all running on scheduled cycles.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "CEO Synthesis Agent",
                "description": "Weekly synthesis from all departments — KPIs, alerts, strategic recommendations",  # noqa: E501
                "skill_ids": ["skill-structured-reporting", "skill-summarize-compress"],
                "system_prompt": "You are the CEO's AI executive assistant. Every week, synthesize department reports into a concise executive brief with KPIs, risks, and opportunities. Ground every claim in actual data from tool outputs. Escalate critical issues immediately.",  # noqa: E501
                "connectors": ["jira", "google_sheets", "email", "slack"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 8 * * MON"},
            },
            {
                "name": "CTO Engineering Agent",
                "description": "Repository health, incident oversight, PR velocity, technical debt tracking",  # noqa: E501
                "skill_ids": ["skill-code-review"],
                "system_prompt": "You are the CTO's AI assistant. Monitor engineering health: open PRs awaiting review, incidents, deployment frequency, and code quality metrics. Flag blockers and generate weekly engineering KPI report.",  # noqa: E501
                "connectors": ["github", "jira", "slack", "datadog"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * MON"},
            },
            {
                "name": "HR Operations Agent",
                "description": "Onboarding pipeline, open requisitions, attrition alerts, leave compliance",  # noqa: E501
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "You are the HR Director's AI assistant. Track onboarding completions, open headcount requisitions, attrition risks, and leave balance anomalies. Generate weekly HR dashboard.",  # noqa: E501
                "connectors": ["jira", "google_sheets", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * MON"},
            },
            {
                "name": "Finance Reconciliation Agent",
                "description": "Weekly spend analysis, budget vs actuals, AP/AR aging, anomaly detection",  # noqa: E501
                "skill_ids": ["skill-data-extraction"],
                "system_prompt": "You are the Finance Director's AI assistant. Every week, reconcile budget vs actuals, identify top spend categories, flag AP/AR anomalies, and generate a cash flow summary. Never modify financial data — only report.",  # noqa: E501
                "connectors": ["google_sheets", "database_query", "email", "pdf_generator"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 8 * * MON"},
            },
        ],
        "knowledge_recipes": [
            {"name": "Company Policies", "description": "HR policies, org chart, department KPIs"},
            {"name": "Financial Templates", "description": "Budget templates, expense categories"},
        ],
        "workflows_config": [],
        "schedules_config": [{"name": "Weekly Org Sync", "cron": "0 8 * * MON"}],
        "policies_config": {"hitl_on_write": True},
        "onboarding_steps": [
            "Connect GitHub, Jira, Slack, and Google Sheets",
            "Upload org chart and department KPI definitions",
            "Configure executive email distribution list",
            "Run simulation dry-run with last quarter data",
            "Activate weekly schedule",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    # ── H6: 30 additional domain solutions ──────────────────────────────────────
    {
        "id": "sol-government-portal",
        "slug": "government-portal",
        "name": "Government Portal AI Suite",
        "domain": "government",
        "description": "AI operations for government portals: citizen query resolution, permit processing, and compliance reporting.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Citizen Services Agent",
                "description": "Handles citizen queries, permit status, and service requests",
                "skill_ids": ["skill-structured-reporting", "skill-summarize-compress"],
                "system_prompt": "You are a government citizen services assistant. Answer queries about permits, services, and regulations accurately. Always cite the relevant policy or regulation. Escalate complex cases to human officers.",  # noqa: E501
                "connectors": ["knowledge", "email", "jira"],
            },
            {
                "name": "Compliance Reporting Agent",
                "description": "Generates statutory compliance reports on schedule",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Generate statutory compliance and audit reports from department data. Ensure accuracy and completeness. Flag missing data immediately.",  # noqa: E501
                "connectors": ["database_query", "google_sheets", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 1 * *"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Government Knowledge Base",
                "description": "Regulations, policies, and service guides",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-banking-fintech",
        "slug": "banking-fintech",
        "name": "Banking & FinTech AI Suite",
        "domain": "banking",
        "description": "AI operations for banking and FinTech: transaction monitoring, KYC automation, and fraud detection alerts.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Transaction Monitor Agent",
                "description": "Monitors transactions for anomalies and fraud patterns",
                "skill_ids": ["skill-data-extraction"],
                "system_prompt": "Monitor financial transactions for anomalies, unusually large transfers, and fraud patterns. Alert compliance officers immediately. Never approve or reject transactions — only report.",  # noqa: E501
                "connectors": ["database_query", "slack", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 */4 * * *"},
            },
            {
                "name": "KYC Operations Agent",
                "description": "Automates KYC document collection and verification workflow",
                "skill_ids": ["skill-structured-reporting", "skill-data-extraction"],
                "system_prompt": "Coordinate KYC document collection, verify completeness, and track pending verifications. Escalate high-risk profiles to compliance team.",  # noqa: E501
                "connectors": ["email", "jira", "knowledge"],
            },
        ],
        "knowledge_recipes": [
            {"name": "Banking Knowledge Base", "description": "Industry docs and standards"},
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-healthcare",
        "slug": "healthcare",
        "name": "Healthcare AI Suite",
        "domain": "healthcare",
        "description": "AI operations for healthcare: patient intake, appointment scheduling, and clinical documentation assistance.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Patient Intake Agent",
                "description": "Handles patient registration and pre-visit data collection",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "You are a healthcare intake assistant. Collect patient information, verify insurance details, and route to appropriate department. Never provide medical advice.",  # noqa: E501
                "connectors": ["email", "knowledge"],
            },
            {
                "name": "Clinical Documentation Agent",
                "description": "Assists with clinical notes and discharge summaries",
                "skill_ids": ["skill-summarize-compress", "skill-data-extraction"],
                "system_prompt": "Assist clinicians with structured documentation. Summarize patient history, extract key clinical findings, and draft discharge summaries for physician review. Always flag for physician sign-off.",  # noqa: E501
                "connectors": ["knowledge", "database_query"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Healthcare Knowledge Base",
                "description": "Clinical protocols, drug formulary, ICD codes",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-hr-talent",
        "slug": "hr-talent",
        "name": "HR & Talent AI Suite",
        "domain": "hr",
        "description": "AI operations for HR teams: talent sourcing, onboarding automation, performance review drafts, and leave management.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Talent Sourcing Agent",
                "description": "Screens resumes and shortlists candidates against job descriptions",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Screen resumes against job requirements objectively. Produce a ranked shortlist with reasoning for each candidate. Flag potential bias risks.",  # noqa: E501
                "connectors": ["email", "knowledge"],
            },
            {
                "name": "Onboarding Automation Agent",
                "description": "Orchestrates day-1 onboarding tasks across tools",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Orchestrate new hire onboarding: create accounts, assign training modules, schedule welcome meetings, and track completion. Escalate blockers to HR manager.",  # noqa: E501
                "connectors": ["jira", "email", "slack"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "HR Knowledge Base",
                "description": "Policies, job descriptions, onboarding checklists",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-devops",
        "slug": "devops",
        "name": "DevOps AI Suite",
        "domain": "devops",
        "description": "AI operations for DevOps teams: CI/CD monitoring, infrastructure cost alerts, deployment health, and on-call escalation.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Deployment Health Agent",
                "description": "Monitors deployments, rollbacks, and infrastructure health",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Monitor CI/CD pipelines and deployments. Flag failed builds, slow deploys, and resource spikes. Generate daily deployment health summaries.",  # noqa: E501
                "connectors": ["github", "slack", "datadog"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 8 * * *"},
            },
            {
                "name": "Infrastructure Cost Agent",
                "description": "Tracks cloud spend and flags cost anomalies",
                "skill_ids": ["skill-data-extraction"],
                "system_prompt": "Analyze cloud infrastructure costs. Identify unused resources, cost spikes, and optimization opportunities. Report weekly to engineering leads.",  # noqa: E501
                "connectors": ["database_query", "google_sheets", "slack"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * MON"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "DevOps Knowledge Base",
                "description": "Runbooks, infra topology, cost baselines",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-sales-crm",
        "slug": "sales-crm",
        "name": "Sales & CRM AI Suite",
        "domain": "sales",
        "description": "AI operations for sales teams: lead scoring, pipeline summaries, follow-up drafts, and deal risk detection.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Lead Scoring Agent",
                "description": "Scores and prioritises inbound leads from CRM data",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Score inbound leads using firmographic and engagement signals. Produce a prioritised list with recommended next action for each rep.",  # noqa: E501
                "connectors": ["database_query", "email"],
            },
            {
                "name": "Pipeline Review Agent",
                "description": "Weekly pipeline health report with risk and opportunity flags",
                "skill_ids": ["skill-structured-reporting", "skill-summarize-compress"],
                "system_prompt": "Analyse the sales pipeline. Flag at-risk deals, identify stale opportunities, and highlight deals close to closing. Generate the weekly pipeline review for sales leadership.",  # noqa: E501
                "connectors": ["database_query", "slack", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 8 * * FRI"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Sales Knowledge Base",
                "description": "Playbooks, ICP profiles, competitor battlecards",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-gst-tax",
        "slug": "gst-tax",
        "name": "GST & Tax AI Suite",
        "domain": "tax",
        "description": "AI operations for tax compliance: GST reconciliation, return filing reminders, TDS computation summaries, and audit trail generation.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "GST Reconciliation Agent",
                "description": "Reconciles GSTR-2A vs purchase register and flags mismatches",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Reconcile GST purchase register with GSTR-2A. Identify mismatches, missing invoices, and ITC claims at risk. Generate mismatch report for the accounts team. Never modify source data.",  # noqa: E501
                "connectors": ["google_sheets", "database_query", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 11 * *"},
            },
            {
                "name": "Tax Filing Reminder Agent",
                "description": "Tracks return due dates and sends proactive reminders",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Track all statutory tax return due dates. Send reminders 7 days and 1 day before deadlines. Escalate missed filings immediately to the CFO.",  # noqa: E501
                "connectors": ["email", "slack"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * *"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "GST & Tax Knowledge Base",
                "description": "GST rates, return calendars, tax circulars",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-invoicing-finance",
        "slug": "invoicing-finance",
        "name": "Invoicing & Finance AI Suite",
        "domain": "invoicing",
        "description": "AI operations for invoicing teams: AR aging summaries, overdue follow-ups, PO matching, and cash flow forecasting.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "AR Follow-Up Agent",
                "description": "Sends automated overdue payment follow-up emails",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Review accounts receivable aging. Draft polite but firm payment follow-up emails for overdue invoices. Escalate invoices over 60 days to the collections team.",  # noqa: E501
                "connectors": ["email", "database_query", "google_sheets"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * MON"},
            },
            {
                "name": "PO Matching Agent",
                "description": "Matches purchase orders to invoices and flags discrepancies",
                "skill_ids": ["skill-data-extraction"],
                "system_prompt": "Match received invoices to open purchase orders. Flag price discrepancies, quantity mismatches, and duplicate invoices. Route exceptions to accounts payable team.",  # noqa: E501
                "connectors": ["database_query", "email"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Invoicing Knowledge Base",
                "description": "Payment terms, vendor master, expense policies",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-real-estate",
        "slug": "real-estate",
        "name": "Real Estate AI Suite",
        "domain": "real_estate",
        "description": "AI operations for real estate: property listing management, tenant communication, lease renewal alerts, and market analysis.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Listing Management Agent",
                "description": "Keeps property listings current and generates descriptions",
                "skill_ids": ["skill-structured-reporting", "skill-summarize-compress"],
                "system_prompt": "Manage property listings. Generate compelling, accurate descriptions from property data. Flag listings needing updates and track days-on-market anomalies.",  # noqa: E501
                "connectors": ["database_query", "email"],
            },
            {
                "name": "Lease Renewal Agent",
                "description": "Tracks lease expiries and automates renewal outreach",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Monitor lease expiry dates. Send renewal reminders 90 and 30 days before expiry. Draft renewal offer letters for property manager review.",  # noqa: E501
                "connectors": ["email", "database_query"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * MON"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Real Estate Knowledge Base",
                "description": "Market data, lease templates, local regulations",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-marketing",
        "slug": "marketing",
        "name": "Marketing AI Suite",
        "domain": "marketing",
        "description": "AI operations for marketing teams: campaign performance analysis, content generation, SEO audits, and social media scheduling.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Campaign Performance Agent",
                "description": "Weekly campaign ROI analysis and optimisation recommendations",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Analyse marketing campaign performance: CTR, conversion rate, CAC, and ROAS. Identify underperforming creatives and budget allocation improvements. Generate weekly marketing KPI report.",  # noqa: E501
                "connectors": ["database_query", "google_sheets", "slack"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 8 * * MON"},
            },
            {
                "name": "Content Generation Agent",
                "description": "Drafts blog posts, ad copy, and social media content",
                "skill_ids": ["skill-summarize-compress"],
                "system_prompt": "Create engaging, brand-aligned marketing content. Generate blog posts, ad copy, and social captions. Always maintain brand voice guidelines and include clear CTAs.",  # noqa: E501
                "connectors": ["knowledge", "web_search"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Marketing Knowledge Base",
                "description": "Brand guidelines, campaign history, audience personas",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-cybersecurity",
        "slug": "cybersecurity",
        "name": "Cybersecurity AI Suite",
        "domain": "cybersecurity",
        "description": "AI operations for security teams: vulnerability triage, threat intelligence summaries, incident response, and compliance posture reporting.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Vulnerability Triage Agent",
                "description": "Prioritises CVEs and security findings by exploitability and impact",  # noqa: E501
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Triage security vulnerabilities by CVSS score, exploitability, and asset criticality. Produce a prioritised remediation list with ownership assignments. Escalate critical CVEs immediately.",  # noqa: E501
                "connectors": ["database_query", "jira", "slack"],
            },
            {
                "name": "Threat Intelligence Agent",
                "description": "Summarises threat feeds and maps to internal attack surface",
                "skill_ids": ["skill-summarize-compress", "skill-web-research"],
                "system_prompt": "Monitor threat intelligence feeds. Summarise relevant threats and map them to the organisation's attack surface. Generate weekly threat briefing for the CISO.",  # noqa: E501
                "connectors": ["web_search", "knowledge", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 7 * * MON"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Cybersecurity Knowledge Base",
                "description": "Asset inventory, security policies, threat model",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-logistics",
        "slug": "logistics",
        "name": "Logistics & Supply Chain AI Suite",
        "domain": "logistics",
        "description": "AI operations for logistics: shipment tracking, route optimisation alerts, warehouse inventory, and supplier performance monitoring.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Shipment Tracking Agent",
                "description": "Monitors shipment status and proactively alerts on delays",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Track all active shipments. Identify delays, customs holds, and delivery exceptions. Send proactive alerts to operations and customer service teams.",  # noqa: E501
                "connectors": ["database_query", "email", "slack"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 */6 * * *"},
            },
            {
                "name": "Inventory Management Agent",
                "description": "Monitors stock levels and triggers reorder alerts",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Monitor warehouse inventory levels. Identify SKUs approaching reorder points. Flag slow-moving and dead stock. Generate weekly inventory health report.",  # noqa: E501
                "connectors": ["database_query", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * *"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Logistics Knowledge Base",
                "description": "Supplier contracts, reorder policies, carrier SLAs",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-insurance",
        "slug": "insurance",
        "name": "Insurance AI Suite",
        "domain": "insurance",
        "description": "AI operations for insurance: claims triage, policy renewal alerts, fraud indicator screening, and underwriting data extraction.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Claims Triage Agent",
                "description": "Classifies and prioritises incoming insurance claims",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Triage incoming insurance claims by severity, coverage type, and fraud risk score. Route to the appropriate adjuster. Flag high-value and suspicious claims immediately.",  # noqa: E501
                "connectors": ["email", "database_query", "jira"],
            },
            {
                "name": "Policy Renewal Agent",
                "description": "Tracks policy renewals and automates retention outreach",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Track policy renewal dates. Send personalised renewal reminders 60 and 14 days before expiry. Identify lapse-risk policies for broker follow-up.",  # noqa: E501
                "connectors": ["email", "database_query"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * *"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Insurance Knowledge Base",
                "description": "Policy terms, claims procedures, compliance rules",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-customer-support",
        "slug": "customer-support",
        "name": "Customer Support AI Suite",
        "domain": "customer_support",
        "description": "AI operations for support teams: ticket triage, first-response drafts, escalation detection, and CSAT trend analysis.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Ticket Triage Agent",
                "description": "Classifies, prioritises, and routes incoming support tickets",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Triage customer support tickets by urgency, product area, and sentiment. Route to the correct queue. Flag VIP customers and escalations for immediate attention.",  # noqa: E501
                "connectors": ["jira", "slack", "knowledge"],
            },
            {
                "name": "First Response Agent",
                "description": "Drafts AI-assisted first responses for agent review",
                "skill_ids": ["skill-summarize-compress"],
                "system_prompt": "Draft empathetic, accurate first-response messages for customer tickets. Base responses on the knowledge base. Always route draft for human agent review before sending.",  # noqa: E501
                "connectors": ["knowledge", "email"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Support Knowledge Base",
                "description": "Product FAQs, troubleshooting guides, escalation policies",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-manufacturing",
        "slug": "manufacturing",
        "name": "Manufacturing AI Suite",
        "domain": "manufacturing",
        "description": "AI operations for manufacturing: production line monitoring, quality control alerts, predictive maintenance summaries, and OEE reporting.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Production Monitor Agent",
                "description": "Tracks production KPIs and escalates line stoppages",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Monitor production line metrics: output rate, downtime, and scrap rate. Alert shift supervisors on anomalies. Generate daily OEE report.",  # noqa: E501
                "connectors": ["database_query", "slack", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 */2 * * *"},
            },
            {
                "name": "Quality Control Agent",
                "description": "Analyses quality inspection data and flags defect trends",
                "skill_ids": ["skill-data-extraction"],
                "system_prompt": "Analyse quality inspection records. Identify defect patterns, supplier quality issues, and process deviations. Generate weekly quality control report for the QA manager.",  # noqa: E501
                "connectors": ["database_query", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * MON"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Manufacturing Knowledge Base",
                "description": "SOPs, quality standards, equipment specs",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-agriculture",
        "slug": "agriculture",
        "name": "Agriculture AI Suite",
        "domain": "agriculture",
        "description": "AI operations for agriculture: crop health monitoring, weather-based advisory, supply chain coordination, and yield forecasting.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Crop Advisory Agent",
                "description": "Provides weather-correlated crop health and irrigation advice",
                "skill_ids": ["skill-web-research", "skill-structured-reporting"],
                "system_prompt": "Monitor weather forecasts and soil data. Provide actionable crop health advisories: irrigation schedules, pest risk alerts, and harvest window recommendations. Cite data sources.",  # noqa: E501
                "connectors": ["web_search", "database_query", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 6 * * *"},
            },
            {
                "name": "Supply Chain Coordination Agent",
                "description": "Coordinates harvest logistics, storage, and market linkages",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Coordinate post-harvest logistics: cold storage availability, transport scheduling, and market price monitoring. Alert farmers and aggregators on optimal selling windows.",  # noqa: E501
                "connectors": ["database_query", "email", "slack"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Agriculture Knowledge Base",
                "description": "Crop calendars, pest guides, market price benchmarks",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-hospitality-travel",
        "slug": "hospitality-travel",
        "name": "Hospitality & Travel AI Suite",
        "domain": "hospitality",
        "description": "AI operations for hospitality and travel: booking management, guest experience personalisation, rate optimisation, and review response.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Guest Experience Agent",
                "description": "Personalises guest communications and handles special requests",
                "skill_ids": ["skill-summarize-compress", "skill-structured-reporting"],
                "system_prompt": "Manage guest communications from pre-arrival to post-stay. Personalise messages based on booking history, acknowledge special requests, and resolve complaints promptly.",  # noqa: E501
                "connectors": ["email", "knowledge", "database_query"],
            },
            {
                "name": "Revenue Management Agent",
                "description": "Monitors occupancy and recommends dynamic pricing adjustments",
                "skill_ids": ["skill-data-extraction"],
                "system_prompt": "Analyse booking trends, occupancy, and competitor rates. Recommend dynamic pricing adjustments to maximise RevPAR. Generate weekly revenue performance report.",  # noqa: E501
                "connectors": ["database_query", "google_sheets"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 7 * * *"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Hospitality Knowledge Base",
                "description": "Property info, service catalogue, guest policies",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-media-publishing",
        "slug": "media-publishing",
        "name": "Media & Publishing AI Suite",
        "domain": "media",
        "description": "AI operations for media and publishing: content scheduling, editorial briefs, rights management alerts, and audience analytics summaries.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Editorial Brief Agent",
                "description": "Generates data-driven editorial briefs from trending topics",
                "skill_ids": ["skill-web-research", "skill-summarize-compress"],
                "system_prompt": "Research trending topics in the publication's coverage area. Generate editorial briefs with angles, hooks, and recommended sources for writers.",  # noqa: E501
                "connectors": ["web_search", "knowledge"],
            },
            {
                "name": "Audience Analytics Agent",
                "description": "Weekly audience engagement and content performance summary",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Analyse content performance: pageviews, read rate, social shares, and subscriber growth. Identify top and underperforming content. Generate weekly editorial analytics report.",  # noqa: E501
                "connectors": ["database_query", "google_sheets", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 8 * * MON"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Media Knowledge Base",
                "description": "Editorial style guide, content calendar, audience data",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-pharmaceutical",
        "slug": "pharmaceutical",
        "name": "Pharmaceutical AI Suite",
        "domain": "pharmaceutical",
        "description": "AI operations for pharma: adverse event monitoring, regulatory submission tracking, clinical trial data extraction, and pharmacovigilance reporting.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Adverse Event Monitor Agent",
                "description": "Screens incoming reports for adverse drug reactions",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Screen incoming adverse event reports. Classify severity (serious/non-serious), identify signal patterns, and ensure MedWatch/EudraVigilance submission deadlines are met. Always escalate serious events to the safety officer immediately.",  # noqa: E501
                "connectors": ["email", "database_query", "jira"],
            },
            {
                "name": "Regulatory Tracking Agent",
                "description": "Tracks regulatory submission deadlines and approval status",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Track all regulatory submission deadlines (IND, NDA, ANDA, MAA). Send alerts 30 and 7 days before deadlines. Monitor approval status and flag correspondence requiring response.",  # noqa: E501
                "connectors": ["email", "database_query"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * *"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Pharma Knowledge Base",
                "description": "Regulatory guidelines, product dossiers, safety database",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-telecom",
        "slug": "telecom",
        "name": "Telecom AI Suite",
        "domain": "telecom",
        "description": "AI operations for telecoms: network incident triage, churn prediction alerts, billing anomaly detection, and SLA breach monitoring.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Network Incident Agent",
                "description": "Triages network alerts and coordinates NOC response",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Triage network incidents by severity and affected customer base. Coordinate NOC response, track resolution SLAs, and generate incident reports.",  # noqa: E501
                "connectors": ["database_query", "slack", "jira"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 */1 * * *"},
            },
            {
                "name": "Churn Prediction Agent",
                "description": "Identifies at-risk customers and triggers retention workflows",
                "skill_ids": ["skill-data-extraction"],
                "system_prompt": "Analyse customer usage and complaint data to identify churn-risk subscribers. Generate prioritised retention outreach list for the customer success team.",  # noqa: E501
                "connectors": ["database_query", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * MON"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Telecom Knowledge Base",
                "description": "Network topology, SLA agreements, tariff plans",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-construction",
        "slug": "construction",
        "name": "Construction AI Suite",
        "domain": "construction",
        "description": "AI operations for construction: project schedule monitoring, safety incident reporting, procurement cost tracking, and subcontractor performance.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Project Schedule Agent",
                "description": "Monitors project milestones and flags schedule slippage",
                "skill_ids": ["skill-structured-reporting", "skill-data-extraction"],
                "system_prompt": "Monitor construction project schedules. Identify critical path delays, resource conflicts, and weather-related risks. Generate weekly project health report for the project manager.",  # noqa: E501
                "connectors": ["database_query", "jira", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 8 * * MON"},
            },
            {
                "name": "Safety Compliance Agent",
                "description": "Tracks safety incidents and compliance documentation",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Track safety incidents, near-misses, and toolbox talks. Ensure compliance documentation is up to date. Escalate recordable incidents to the HSE manager immediately.",  # noqa: E501
                "connectors": ["email", "database_query", "jira"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Construction Knowledge Base",
                "description": "Building codes, safety standards, project specs",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-food-restaurant",
        "slug": "food-restaurant",
        "name": "Food & Restaurant AI Suite",
        "domain": "food_restaurant",
        "description": "AI operations for food service: inventory and wastage tracking, menu engineering, reservation management, and health inspection prep.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Inventory & Wastage Agent",
                "description": "Tracks ingredient consumption and flags wastage anomalies",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Monitor daily inventory consumption. Identify ingredient wastage above threshold, flag stockouts, and generate purchase recommendations for the chef and manager.",  # noqa: E501
                "connectors": ["database_query", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 22 * * *"},
            },
            {
                "name": "Menu Engineering Agent",
                "description": "Analyses dish profitability and popularity for menu optimisation",
                "skill_ids": ["skill-data-extraction"],
                "system_prompt": "Analyse menu item sales velocity, food cost percentage, and contribution margin. Classify dishes as Stars, Plowhorses, Puzzles, or Dogs. Recommend menu pricing and placement changes.",  # noqa: E501
                "connectors": ["database_query", "google_sheets"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 1 * *"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Restaurant Knowledge Base",
                "description": "Recipes, supplier list, compliance checklists",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-recruitment",
        "slug": "recruitment",
        "name": "Recruitment AI Suite",
        "domain": "recruitment",
        "description": "AI operations for recruitment agencies: job description generation, bulk resume screening, interview scheduling, and placement tracking.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Resume Screening Agent",
                "description": "Screens and ranks resumes at scale against job requirements",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Screen resumes against job description requirements. Rank candidates by skill match and experience fit. Generate a shortlist with reasoning for each candidate. Flag potential bias.",  # noqa: E501
                "connectors": ["email", "knowledge"],
            },
            {
                "name": "Interview Scheduling Agent",
                "description": "Automates interview coordination between candidates and hiring managers",  # noqa: E501
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Coordinate interview scheduling between candidates and hiring managers. Send calendar invites, reminders, and follow-up feedback requests. Track pipeline stage for each candidate.",  # noqa: E501
                "connectors": ["email", "slack"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Recruitment Knowledge Base",
                "description": "Job descriptions, competency frameworks, offer templates",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-energy-utilities",
        "slug": "energy-utilities",
        "name": "Energy & Utilities AI Suite",
        "domain": "energy",
        "description": "AI operations for energy and utilities: consumption anomaly detection, outage management, regulatory reporting, and demand forecasting summaries.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Consumption Anomaly Agent",
                "description": "Detects unusual energy consumption patterns per meter or zone",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Analyse energy consumption data per meter, building, and zone. Flag anomalies indicative of equipment faults, leaks, or theft. Generate daily anomaly report for the operations team.",  # noqa: E501
                "connectors": ["database_query", "slack", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 6 * * *"},
            },
            {
                "name": "Outage Management Agent",
                "description": "Coordinates outage response and customer communications",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Manage power or utility outage incidents. Assess scope, coordinate field crews, update customers on ETA, and track restoration progress. Generate post-restoration incident report.",  # noqa: E501
                "connectors": ["database_query", "slack", "email", "jira"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Energy Knowledge Base",
                "description": "Grid topology, regulatory requirements, maintenance history",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-automobile",
        "slug": "automobile",
        "name": "Automobile AI Suite",
        "domain": "automobile",
        "description": "AI operations for automotive: service appointment management, parts inventory alerts, warranty claim triage, and customer satisfaction follow-up.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Service Appointment Agent",
                "description": "Manages service bookings, reminders, and vehicle health notifications",  # noqa: E501
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Manage vehicle service appointments. Send reminders 7 and 1 day before appointments. Alert customers on recall notices and overdue maintenance based on service history.",  # noqa: E501
                "connectors": ["email", "database_query"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 8 * * *"},
            },
            {
                "name": "Parts Inventory Agent",
                "description": "Tracks spare parts inventory and triggers reorder alerts",
                "skill_ids": ["skill-data-extraction"],
                "system_prompt": "Monitor spare parts inventory levels. Flag critical parts approaching reorder threshold. Identify slow-moving parts and generate weekly inventory health report for the parts manager.",  # noqa: E501
                "connectors": ["database_query", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * MON"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Automotive Knowledge Base",
                "description": "Parts catalogue, warranty terms, service manuals",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-nonprofit-ngo",
        "slug": "nonprofit-ngo",
        "name": "Nonprofit & NGO AI Suite",
        "domain": "nonprofit",
        "description": "AI operations for nonprofits and NGOs: donor engagement, grant tracking, volunteer coordination, and impact reporting.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Donor Engagement Agent",
                "description": "Personalises donor communications and tracks giving patterns",
                "skill_ids": ["skill-summarize-compress", "skill-structured-reporting"],
                "system_prompt": "Manage donor communications. Personalise thank-you messages, track lapsed donors, and identify major giving opportunities. Generate monthly donor engagement report.",  # noqa: E501
                "connectors": ["email", "database_query"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * MON"},
            },
            {
                "name": "Grant Tracking Agent",
                "description": "Tracks grant deadlines, reporting requirements, and compliance",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Track grant application deadlines and reporting requirements. Send alerts 30 and 7 days before deadlines. Flag compliance risks and missing deliverables for the grants manager.",  # noqa: E501
                "connectors": ["email", "database_query", "google_sheets"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * *"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Nonprofit Knowledge Base",
                "description": "Grant requirements, impact metrics, donor history",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-events-mice",
        "slug": "events-mice",
        "name": "Events & MICE AI Suite",
        "domain": "events",
        "description": "AI operations for event management: venue coordination, attendee communications, sponsor management, and post-event analytics.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Attendee Communications Agent",
                "description": "Manages event registration, reminders, and post-event follow-up",
                "skill_ids": ["skill-structured-reporting", "skill-summarize-compress"],
                "system_prompt": "Manage all attendee-facing communications for events. Send registration confirmations, agenda updates, day-of logistics, and post-event satisfaction surveys.",  # noqa: E501
                "connectors": ["email", "database_query"],
            },
            {
                "name": "Vendor Coordination Agent",
                "description": "Tracks vendor deliverables, contracts, and payment schedules",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Coordinate with event vendors: AV, catering, venue, and logistics. Track contract milestones, payment schedules, and delivery confirmations. Flag risks to the event manager.",  # noqa: E501
                "connectors": ["email", "jira", "database_query"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * *"},
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Events Knowledge Base",
                "description": "Event templates, vendor contacts, compliance checklists",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-wealth-management",
        "slug": "wealth-management",
        "name": "Wealth Management AI Suite",
        "domain": "wealth",
        "description": "AI operations for wealth managers: portfolio drift alerts, client review prep, rebalancing recommendations, and regulatory reporting.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Portfolio Monitor Agent",
                "description": "Monitors client portfolios for drift and risk threshold breaches",
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Monitor client portfolio allocations against target weights and risk thresholds. Flag drift exceeding tolerance, generate rebalancing recommendations for advisor review. Never execute trades.",  # noqa: E501
                "connectors": ["database_query", "email"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 7 * * *"},
            },
            {
                "name": "Client Review Prep Agent",
                "description": "Prepares quarterly client review packs with performance summaries",
                "skill_ids": ["skill-structured-reporting", "skill-summarize-compress"],
                "system_prompt": "Prepare quarterly client review packs: portfolio performance, asset allocation, goal progress, and market commentary. Personalise for each client's objectives and risk profile.",  # noqa: E501
                "connectors": ["database_query", "google_sheets", "email"],
                "trigger_config": {
                    "trigger_type": "schedule",
                    "cron_expression": "0 8 1 1,4,7,10 *",
                },
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Wealth Management Knowledge Base",
                "description": "Investment policy statements, product catalogue, regulatory rules",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-fashion-apparel",
        "slug": "fashion-apparel",
        "name": "Fashion & Apparel AI Suite",
        "domain": "fashion",
        "description": "AI operations for fashion and apparel: trend analysis, inventory planning, supplier communication, and returns processing automation.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Trend Intelligence Agent",
                "description": "Monitors fashion trend signals and competitor launches",
                "skill_ids": ["skill-web-research", "skill-summarize-compress"],
                "system_prompt": "Monitor fashion trend signals from social media, runway coverage, and competitor product launches. Summarise weekly trend intelligence brief for design and buying teams.",  # noqa: E501
                "connectors": ["web_search", "knowledge"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 8 * * MON"},
            },
            {
                "name": "Inventory Planning Agent",
                "description": "Analyses sell-through rates and flags reorder or markdown opportunities",  # noqa: E501
                "skill_ids": ["skill-data-extraction", "skill-structured-reporting"],
                "system_prompt": "Analyse SKU sell-through rates, size curve breakdowns, and aged inventory. Flag reorder opportunities and recommend markdown strategies for slow-moving styles.",  # noqa: E501
                "connectors": ["database_query", "google_sheets", "email"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Fashion Knowledge Base",
                "description": "Brand guidelines, supplier catalogue, sizing standards",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
    {
        "id": "sol-architecture-interior",
        "slug": "architecture-interior",
        "name": "Architecture & Interior Design AI Suite",
        "domain": "architecture",
        "description": "AI operations for architecture and interior design firms: project milestone tracking, material specification assistance, client communication, and permit documentation.",  # noqa: E501
        "version": "1.0.0",
        "agents_config": [
            {
                "name": "Project Milestone Agent",
                "description": "Tracks design phase milestones and client approval gates",
                "skill_ids": ["skill-structured-reporting"],
                "system_prompt": "Monitor architecture project milestones: schematic design, design development, and construction documents. Flag delays and pending client approvals. Generate weekly project status for the principal architect.",  # noqa: E501
                "connectors": ["jira", "email", "database_query"],
                "trigger_config": {"trigger_type": "schedule", "cron_expression": "0 9 * * MON"},
            },
            {
                "name": "Material Specification Agent",
                "description": "Assists with material research, specifications, and supplier sourcing",  # noqa: E501
                "skill_ids": ["skill-web-research", "skill-data-extraction"],
                "system_prompt": "Research and compile material specifications for design projects. Compare supplier options by cost, lead time, and sustainability rating. Generate material schedule for contractor bidding.",  # noqa: E501
                "connectors": ["web_search", "knowledge", "email"],
            },
        ],
        "knowledge_recipes": [
            {
                "name": "Architecture Knowledge Base",
                "description": "Building codes, specification library, supplier database",
            },
        ],
        "onboarding_steps": [
            "Connect required integrations",
            "Upload domain knowledge documents",
            "Run simulation trial",
            "Activate scheduled operations",
        ],
        "install_count": 0,
        "visibility": "public",
    },
]


def get_solution(slug: str) -> dict[str, Any] | None:
    """Return a solution by slug, or None if not found."""
    return next((s for s in DOMAIN_SOLUTIONS if s["slug"] == slug), None)


def list_solutions(domain: str | None = None) -> list[dict[str, Any]]:
    """Return all solutions, optionally filtered by domain."""
    if domain:
        return [s for s in DOMAIN_SOLUTIONS if s["domain"] == domain]
    return DOMAIN_SOLUTIONS
