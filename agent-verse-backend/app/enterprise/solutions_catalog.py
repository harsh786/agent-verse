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
            "Automate catalog management, order exception handling, and customer "
            "review responses."
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
]


def get_solution(slug: str) -> dict[str, Any] | None:
    """Return a solution by slug, or None if not found."""
    return next((s for s in DOMAIN_SOLUTIONS if s["slug"] == slug), None)


def list_solutions(domain: str | None = None) -> list[dict[str, Any]]:
    """Return all solutions, optionally filtered by domain."""
    if domain:
        return [s for s in DOMAIN_SOLUTIONS if s["domain"] == domain]
    return DOMAIN_SOLUTIONS
