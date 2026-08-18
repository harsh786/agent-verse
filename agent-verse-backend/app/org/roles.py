"""PART 4 — Complete Role Taxonomy (456 roles across 22 departments).

Provides:
  - ROLE_TAXONOMY: all 456 role definitions indexed by role_id
  - get_roles_for_dept(): roles for a specific department
  - get_role_by_name(): fuzzy lookup
  - DEPT_ROLE_MAP: department → list of role_ids
  - DEFAULT_CAPABILITIES: per-role default capability list
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RoleDefinition:
    """Full role definition per PART 5 spec."""
    id: str
    name: str
    department_id: str
    seniority: str                        # staff | senior | mid | junior
    responsibilities: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    primary_model: str = "smart"          # references MODEL_PROFILES key
    fallback_model: str = "fast"
    allowed_tools: list[str] = field(default_factory=list)
    budget_usd_per_task: float = 1.0
    max_task_duration_minutes: int = 60
    default_autonomy_level: int = 3
    risk_level: str = "medium"
    quality_threshold: float = 0.80


# ── Default tool sets ─────────────────────────────────────────────────────────

_RESEARCH_TOOLS  = ["web_search", "knowledge_search", "org_memory_read", "artifact_store"]
_CODE_TOOLS      = ["code_execution", "git_tool", "web_search", "artifact_store"]
_ANALYSIS_TOOLS  = ["python_exec", "sql_tool", "web_search", "artifact_store"]
_CONTENT_TOOLS   = ["web_search", "knowledge_search", "artifact_store"]
_MGMT_TOOLS      = ["org_memory_read", "org_memory_write", "approval_request", "task_delegate"]

# ── 456 Roles — 22 Departments ────────────────────────────────────────────────

ROLE_TAXONOMY: dict[str, RoleDefinition] = {}


def _add(role_id: str, name: str, dept: str, seniority: str = "senior",
         caps: list[str] | None = None, tools: list[str] | None = None,
         model: str = "smart", budget: float = 1.0, autonomy: int = 3,
         risk: str = "medium", quality: float = 0.80) -> None:
    ROLE_TAXONOMY[role_id] = RoleDefinition(
        id=role_id, name=name, department_id=dept, seniority=seniority,
        capabilities=caps or [], allowed_tools=tools or _MGMT_TOOLS,
        primary_model=model, budget_usd_per_task=budget,
        default_autonomy_level=autonomy, risk_level=risk, quality_threshold=quality,
    )


# ── DEPT 1: EXECUTIVE (25 roles) ──────────────────────────────────────────────
for _name in ["CEO", "COO", "CTO", "CIO", "CPO", "CMO", "CRO", "CFO", "CHRO",
              "Chief AI Officer", "Chief Data Officer", "Chief Security Officer",
              "CISO", "Chief Product Officer", "Chief Revenue Officer",
              "Chief Customer Officer", "Chief Strategy Officer",
              "Chief Research Officer", "Chief Innovation Officer",
              "Chief Risk Officer", "Chief Compliance Officer",
              "Chief Legal Officer", "Chief Knowledge Officer",
              "Chief Quality Officer", "Chief Trust & Safety Officer"]:
    _rid = f"executive:{_name.lower().replace(' ', '_').replace('&', 'and')}"
    _add(_rid, _name, "executive", "staff", ["strategic_planning", "decision_making"],
         _MGMT_TOOLS, "premium", 5.0, 4, "high", 0.95)

# ── DEPT 2: STRATEGY & BUSINESS (20 roles) ───────────────────────────────────
for _name in ["Business Strategist", "Business Analyst", "Strategy Analyst",
              "Strategy Consultant", "Corporate Strategy Agent", "Market Analyst",
              "Market Intelligence Agent", "Competitive Intelligence Agent",
              "Business Development Agent", "Partnership Manager",
              "Partnership Agent", "Opportunity Analyst", "Venture Analyst",
              "Investment Analyst", "Pricing Strategist", "Revenue Strategist",
              "Growth Strategist", "Business Planning Agent",
              "Scenario Planning Agent", "Corporate Development Agent"]:
    _rid = f"strategy:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "strategy", "senior", ["web_search", "data_analysis", "financial_modeling"],
         _RESEARCH_TOOLS + _ANALYSIS_TOOLS, "smart", 1.5)

# ── DEPT 3: PRODUCT (14 roles) ────────────────────────────────────────────────
for _name in ["Product Manager", "Product Director", "Product Owner",
              "Product Strategist", "Product Analyst", "Product Operations",
              "Product Researcher", "User Researcher", "Customer Researcher",
              "Requirements Analyst", "Business Requirements Analyst",
              "Roadmap Manager", "Product Launch Manager", "Product Discovery Agent"]:
    _rid = f"product:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "product", "senior", ["web_search", "data_analysis", "content_writing"],
         _RESEARCH_TOOLS, "smart", 1.0)

# ── DEPT 4: ENGINEERING (21 roles) ───────────────────────────────────────────
for _name in ["Software Architect", "Principal Engineer", "Staff Engineer",
              "Backend Engineer", "Frontend Engineer", "Full-stack Engineer",
              "Mobile Engineer", "Web Engineer", "API Engineer",
              "Integration Engineer", "Distributed Systems Engineer",
              "Database Engineer", "Platform Engineer", "Infrastructure Engineer",
              "Systems Engineer", "Performance Engineer", "Reliability Engineer",
              "Automation Engineer", "SDK Engineer",
              "Developer Experience Engineer", "Open Source Engineer"]:
    _rid = f"engineering:{_name.lower().replace(' ', '_').replace('-', '_')}"
    _add(_rid, _name, "engineering", "senior", ["code_generation", "code_review"],
         _CODE_TOOLS, "coding", 2.0)

# ── DEPT 5: AI / ML (24 roles) ────────────────────────────────────────────────
for _name in ["AI Architect", "AI Engineer", "ML Engineer", "LLM Engineer",
              "Agent Engineer", "Agent Architect", "Prompt Engineer",
              "Context Engineer", "AI Workflow Engineer", "RAG Engineer",
              "Knowledge Engineer", "AI Evaluation Engineer", "AI Safety Engineer",
              "AI Alignment Specialist", "Inference Engineer",
              "Model Optimization Engineer", "Fine-tuning Engineer",
              "Synthetic Data Engineer", "Multimodal AI Engineer",
              "Computer Vision Engineer", "NLP Engineer", "Speech AI Engineer",
              "Reinforcement Learning Engineer", "AI Research Scientist"]:
    _rid = f"ai_ml:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "ai_ml", "senior", ["code_generation", "data_analysis", "rpa_automation"],
         _CODE_TOOLS + _ANALYSIS_TOOLS, "coding", 2.5)

# ── DEPT 6: MARKETING (53 roles) ─────────────────────────────────────────────
for _name in ["CMO", "Marketing Director", "Marketing Manager", "Marketing Strategist",
              "Brand Strategist", "Go-to-Market Strategist", "Product Marketing Manager",
              "Content Strategist", "Content Writer", "Copywriter", "Technical Writer",
              "Blog Writer", "Newsletter Writer", "Whitepaper Writer", "Case Study Writer",
              "Research Writer", "Ghostwriter", "Script Writer", "Editorial Agent",
              "Copy Editor", "Proofreader",
              "SEO Strategist", "SEO Specialist", "Technical SEO Agent",
              "Keyword Research Agent", "SEO Content Planner", "Link Building Agent", "SEO Analyst",
              "Social Media Manager", "Social Media Strategist", "Social Content Creator",
              "Community Manager", "Influencer Marketing Agent", "Creator Partnership Agent",
              "Growth Marketer", "Growth Analyst", "CRO Agent", "Funnel Optimization Agent",
              "Acquisition Agent", "Retention Agent", "Lifecycle Marketing Agent",
              "Performance Marketing Agent",
              "Advertising Strategist", "Media Buyer", "Paid Search Agent",
              "Paid Social Agent", "Campaign Manager", "Ad Creative Agent",
              "Marketing Operations Manager", "Campaign Operations Agent",
              "Marketing Automation Agent", "Attribution Analyst",
              "Marketing Data Analyst", "Marketing Technology Agent"]:
    _rid = f"marketing:{_name.lower().replace(' ', '_').replace('-', '_')}"
    _add(_rid, _name, "marketing", "mid", ["content_writing", "web_search", "data_analysis"],
         _CONTENT_TOOLS + _ANALYSIS_TOOLS, "creative", 1.0)

# ── DEPT 7: SALES (33 roles) ─────────────────────────────────────────────────
for _name in ["CRO", "VP Sales", "Sales Director", "Sales Manager",
              "Lead Generation Agent", "Lead Research Agent", "Prospecting Agent",
              "SDR", "BDR", "Account Research Agent",
              "Account Executive", "Sales Representative", "Enterprise Sales Agent",
              "SMB Sales Agent", "Inside Sales Agent", "Technical Sales Agent",
              "Solution Consultant", "Sales Engineer", "Pre-sales Agent",
              "Account Manager", "Strategic Account Manager", "Key Account Manager",
              "Renewal Agent", "Upsell Agent", "Cross-sell Agent", "Account Expansion Agent",
              "Sales Intelligence Analyst", "Pipeline Analyst", "Forecasting Agent",
              "Deal Desk Agent", "Pricing Agent", "Proposal Agent", "RFP/RFI Agent"]:
    _rid = f"sales:{_name.lower().replace('/', '_').replace(' ', '_')}"
    _add(_rid, _name, "sales", "mid", ["web_search", "data_analysis", "content_writing"],
         _RESEARCH_TOOLS, "fast", 0.5)

# ── DEPT 8: FINANCE (22 roles) ────────────────────────────────────────────────
for _name in ["CFO", "Finance Director", "Finance Manager", "Financial Analyst",
              "FP&A Agent", "Accountant", "Bookkeeper", "Controller",
              "Treasury Agent", "Revenue Analyst", "Cost Analyst",
              "Pricing Analyst", "Tax Agent", "Financial Planning Agent",
              "Expense Management Agent", "Invoice Processing Agent",
              "Accounts Payable Agent", "Accounts Receivable Agent",
              "Financial Reporting Agent", "Financial Forecasting Agent",
              "Audit Support Agent", "Procurement Finance Agent"]:
    _rid = f"finance:{_name.lower().replace('/', '_').replace('&', 'and').replace(' ', '_')}"
    _add(_rid, _name, "finance", "senior", ["financial_modeling", "data_analysis", "sql"],
         _ANALYSIS_TOOLS, "analytical", 2.0, 3, "high", 0.97)

# ── DEPT 9: HR / PEOPLE (20 roles) ───────────────────────────────────────────
for _name in ["CHRO", "HR Director", "HR Manager", "Talent Acquisition Agent",
              "Recruiter", "Technical Recruiter", "Candidate Research Agent",
              "Interview Agent", "Hiring Coordinator", "Onboarding Agent",
              "Employee Support Agent", "Learning & Development Agent",
              "Performance Management Agent", "Workforce Planning Agent",
              "Compensation Analyst", "Benefits Analyst", "People Analytics Agent",
              "Culture Agent", "Internal Communications Agent", "Employee Relations Agent"]:
    _rid = f"hr:{_name.lower().replace(' ', '_').replace('&', 'and').replace('/', '_')}"
    _add(_rid, _name, "hr", "mid", ["web_search", "content_writing", "data_analysis"],
         _CONTENT_TOOLS, "smart", 0.8)

# ── DEPT 10: OPERATIONS (20 roles) ───────────────────────────────────────────
for _name in ["COO", "Operations Director", "Operations Manager",
              "Business Operations Agent", "Operations Analyst",
              "Process Improvement Agent", "Workflow Optimization Agent",
              "Resource Planning Agent", "Capacity Planning Agent",
              "Scheduling Agent", "Administrative Agent", "Executive Assistant Agent",
              "Procurement Agent", "Vendor Management Agent", "Supplier Management Agent",
              "Inventory Agent", "Logistics Agent", "Facilities Agent",
              "Business Continuity Agent", "Incident Operations Agent"]:
    _rid = f"operations:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "operations", "mid", ["data_analysis", "web_search"],
         _ANALYSIS_TOOLS, "smart", 0.8)

# ── DEPT 11: DEVOPS / SRE / IT (29 roles) ───────────────────────────────────
for _name in ["DevOps Engineer", "DevOps Manager", "Platform Engineer",
              "Cloud Engineer", "Cloud Architect", "Infrastructure Engineer",
              "Infrastructure Architect", "SRE", "Production Engineer", "Release Engineer",
              "Build Engineer", "CI/CD Engineer", "Kubernetes Engineer", "Container Engineer",
              "Observability Engineer", "Reliability Engineer", "Performance Engineer",
              "Network Engineer", "Systems Administrator", "Database Reliability Engineer",
              "Disaster Recovery Engineer", "FinOps Engineer", "DevSecOps Engineer",
              "Infrastructure Automation Agent", "Deployment Agent",
              "Incident Response Engineer", "IT Support Agent", "IT Operations Agent",
              "Platform Security Engineer"]:
    _rid = f"devops:{_name.lower().replace('/', '_').replace(' ', '_')}"
    _add(_rid, _name, "devops", "senior", ["code_generation", "rpa_automation"],
         _CODE_TOOLS, "coding", 2.0)

# ── DEPT 12: CUSTOMER SUCCESS / SUPPORT (16 roles) ───────────────────────────
for _name in ["Chief Customer Officer", "Customer Success Manager",
              "Customer Success Agent", "Customer Onboarding Agent",
              "Customer Support Agent", "Technical Support Agent",
              "Support Triage Agent", "Support Escalation Agent",
              "Customer Education Agent", "Customer Training Agent",
              "Knowledge Base Agent", "Customer Feedback Analyst",
              "Customer Experience Analyst", "Customer Retention Agent",
              "Customer Advocacy Agent", "Voice-of-Customer Agent"]:
    _rid = f"support:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "support", "mid", ["web_search", "content_writing"],
         _CONTENT_TOOLS, "fast", 0.3, 3, "low", 0.85)

# ── DEPT 13: DESIGN / CREATIVE (17 roles) ────────────────────────────────────
for _name in ["Creative Director", "Art Director", "Brand Designer", "Graphic Designer",
              "UI Designer", "UX Designer", "Product Designer", "Interaction Designer",
              "Visual Designer", "Presentation Designer", "Motion Designer", "Video Producer",
              "Video Editor", "Illustrator", "Creative Strategist",
              "Design Researcher", "Design System Specialist"]:
    _rid = f"design:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "design", "senior", ["content_writing", "image_generation"],
         _CONTENT_TOOLS, "creative", 1.5)

# ── DEPT 14: WRITING / EDITORIAL (25 roles) ──────────────────────────────────
for _name in ["Editorial Director", "Managing Editor", "Editor", "Copy Editor",
              "Proofreader", "Content Writer", "Technical Writer", "Business Writer",
              "Research Writer", "Creative Writer", "UX Writer", "Documentation Writer",
              "Proposal Writer", "Grant Writer", "Report Writer", "Executive Writer",
              "Speech Writer", "Script Writer", "Storyteller", "Ghostwriter",
              "Newsletter Writer", "Whitepaper Writer", "Case Study Writer",
              "Translator", "Localization Agent"]:
    _rid = f"writing:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "writing", "mid", ["content_writing", "web_search"],
         _CONTENT_TOOLS, "creative", 0.8)

# ── DEPT 15: DATA (16 roles) ──────────────────────────────────────────────────
for _name in ["Chief Data Officer", "Data Architect", "Data Engineer",
              "Analytics Engineer", "Data Scientist", "Data Analyst", "BI Analyst",
              "ML Data Engineer", "Data Quality Engineer", "Data Governance Specialist",
              "Data Steward", "Data Catalog Manager", "Metadata Engineer",
              "Ontology Engineer", "Knowledge Graph Engineer",
              "Data Visualization Specialist"]:
    _rid = f"data:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "data", "senior", ["data_analysis", "sql", "python_exec"],
         _ANALYSIS_TOOLS, "analytical", 2.0, 3, "medium", 0.90)

# ── DEPT 16: RESEARCH (18 roles) ─────────────────────────────────────────────
for _name in ["Research Director", "Research Scientist", "Research Analyst",
              "Literature Researcher", "Web Researcher", "Academic Researcher",
              "Scientific Researcher", "Technical Researcher", "Market Researcher",
              "Competitive Researcher", "Patent Researcher", "Legal Researcher",
              "Financial Researcher", "Fact Checker", "Evidence Analyst",
              "Hypothesis Generator", "Experiment Designer", "Research Reviewer"]:
    _rid = f"research:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "research", "senior", ["web_search", "data_analysis"],
         _RESEARCH_TOOLS, "research", 1.5)

# ── DEPT 17: LEGAL / COMPLIANCE / RISK (15 roles) ────────────────────────────
for _name in ["General Counsel", "Legal Researcher", "Contract Analyst",
              "Contract Reviewer", "Contract Management Agent", "Compliance Officer",
              "Compliance Analyst", "Regulatory Researcher", "Privacy Specialist",
              "Data Protection Specialist", "Policy Analyst", "Risk Analyst",
              "Governance Analyst", "Internal Audit Agent", "Ethics Reviewer"]:
    _rid = f"legal:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "legal", "senior", ["web_search", "content_writing", "legal_review"],
         _RESEARCH_TOOLS + _CONTENT_TOOLS, "expert", 3.0, 2, "high", 0.97)

# ── DEPT 18: SECURITY (17 roles) ─────────────────────────────────────────────
for _name in ["CISO", "Security Architect", "Security Engineer",
              "Application Security Engineer", "Cloud Security Engineer",
              "Identity Security Engineer", "SOC Analyst",
              "Threat Intelligence Analyst", "Threat Hunter",
              "Incident Response Agent", "Security Operations Agent",
              "Vulnerability Analyst", "Security Auditor", "Privacy Engineer",
              "Red Team Agent", "Blue Team Agent", "Purple Team Coordinator"]:
    _rid = f"security:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "security", "senior", ["code_review", "web_search"],
         _CODE_TOOLS, "expert", 3.0, 2, "high", 0.99)

# ── DEPT 19: QA / QUALITY (20 roles) ─────────────────────────────────────────
for _name in ["QA Architect", "QA Engineer", "Test Engineer", "Automation Tester",
              "Integration Tester", "Regression Tester", "Performance Tester",
              "Load Tester", "Chaos Engineer", "Security Tester", "API Tester",
              "UI Tester", "Accessibility Tester", "Compatibility Tester",
              "Reliability Tester", "AI Evaluator", "LLM Evaluator",
              "Hallucination Evaluator", "Factuality Evaluator", "Red Team Evaluator"]:
    _rid = f"qa:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "qa", "mid", ["code_generation", "data_analysis"],
         _CODE_TOOLS + _ANALYSIS_TOOLS, "analytical", 1.5)

# ── DEPT 20: PROCUREMENT (11 roles) ──────────────────────────────────────────
for _name in ["Procurement Director", "Procurement Manager", "Procurement Analyst",
              "Vendor Manager", "Supplier Manager", "Contract Procurement Agent",
              "Vendor Research Agent", "Vendor Risk Analyst", "Purchasing Agent",
              "Sourcing Agent", "Negotiation Support Agent"]:
    _rid = f"procurement:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "procurement", "mid", ["web_search", "data_analysis"],
         _RESEARCH_TOOLS, "smart", 1.0)

# ── DEPT 21: PROJECT / PROGRAM MANAGEMENT (13 roles) ─────────────────────────
for _name in ["Program Director", "Program Manager", "Project Manager",
              "Technical Program Manager", "Project Coordinator", "Scrum Master",
              "Delivery Manager", "Release Manager", "Portfolio Manager",
              "Resource Manager", "Dependency Manager", "Risk Manager", "Project Analyst"]:
    _rid = f"pmo:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "pmo", "senior", ["data_analysis", "content_writing"],
         _MGMT_TOOLS + _ANALYSIS_TOOLS, "smart", 1.0)

# ── DEPT 22: KNOWLEDGE MANAGEMENT (13 roles) ─────────────────────────────────
for _name in ["Chief Knowledge Officer", "Knowledge Manager", "Knowledge Engineer",
              "Knowledge Librarian", "Information Architect", "Taxonomy Manager",
              "Ontology Engineer", "Metadata Specialist", "Document Manager",
              "Records Manager", "Research Librarian",
              "Knowledge Quality Analyst", "Knowledge Curator"]:
    _rid = f"knowledge_mgmt:{_name.lower().replace(' ', '_')}"
    _add(_rid, _name, "knowledge_mgmt", "senior", ["web_search", "content_writing"],
         _CONTENT_TOOLS + _RESEARCH_TOOLS, "smart", 1.0)


# ── Department → role list map ────────────────────────────────────────────────

DEPT_ROLE_MAP: dict[str, list[str]] = {}
for _role_id, _role in ROLE_TAXONOMY.items():
    DEPT_ROLE_MAP.setdefault(_role.department_id, []).append(_role_id)


def get_roles_for_dept(dept_id: str) -> list[RoleDefinition]:
    """Return all role definitions for a department."""
    return [ROLE_TAXONOMY[rid] for rid in DEPT_ROLE_MAP.get(dept_id, []) if rid in ROLE_TAXONOMY]


def get_role_by_name(name: str) -> RoleDefinition | None:
    """Case-insensitive role lookup by name."""
    name_lower = name.lower().strip()
    for role in ROLE_TAXONOMY.values():
        if role.name.lower() == name_lower:
            return role
    return None


def count_roles() -> dict[str, int]:
    """Count roles per department."""
    return {dept: len(roles) for dept, roles in DEPT_ROLE_MAP.items()}


TOTAL_ROLES = len(ROLE_TAXONOMY)
