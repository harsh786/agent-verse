"""Domain-specific role templates for regulated industries.

Pre-built role definitions that can be instantiated for a tenant via
``POST /api/auth/roles/from-template``.  Each entry defines the role's
name, display name, description, permission set, and optional ABAC conditions.

Domains: healthcare, legal, finance, education, ecommerce
"""

from __future__ import annotations

from typing import Any

DOMAIN_ROLE_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "healthcare": [
        {
            "name": "phi_reader",
            "display_name": "PHI Reader",
            "description": "Read-only access to patient health information goals",
            "permissions": ["goals:read", "knowledge:read"],
            "conditions": {"data_classification_lte": "PHI"},
        },
        {
            "name": "prescribing_physician",
            "display_name": "Prescribing Physician",
            "description": "Full clinical goal access with HITL authority",
            "permissions": [
                "goals:read",
                "goals:write",
                "goals:execute",
                "governance:approve",
                "knowledge:read",
            ],
            "conditions": {"license_verification": "required"},
        },
        {
            "name": "care_coordinator",
            "display_name": "Care Coordinator",
            "description": "Manages care plan goals across assigned patient roster",
            "permissions": ["goals:read", "goals:write", "agents:read"],
            "conditions": {"patient_roster": "assigned_only"},
        },
        {
            "name": "hipaa_compliance_officer",
            "display_name": "HIPAA Compliance Officer",
            "description": "Audit access and policy governance for HIPAA compliance",
            "permissions": [
                "audit:read",
                "audit:export",
                "governance:read",
                "governance:write",
            ],
        },
    ],
    "legal": [
        {
            "name": "paralegal",
            "display_name": "Paralegal",
            "description": "Research and document drafting — assigned matters only",
            "permissions": [
                "goals:read",
                "goals:write",
                "knowledge:read",
                "knowledge:write",
            ],
            "conditions": {"matter_access": "assigned_only"},
        },
        {
            "name": "associate_attorney",
            "display_name": "Associate Attorney",
            "description": "Full matter execution requiring senior review above threshold",
            "permissions": [
                "goals:read",
                "goals:write",
                "goals:execute",
                "agents:read",
                "knowledge:read",
            ],
            "conditions": {"supervisor_approval_over_usd": 50000},
        },
        {
            "name": "senior_partner",
            "display_name": "Senior Partner",
            "description": "Full firm access including billing and governance",
            "permissions": [
                "goals:read",
                "goals:write",
                "goals:execute",
                "goals:delete",
                "agents:read",
                "agents:write",
                "governance:read",
                "governance:write",
                "governance:approve",
                "costs:read",
                "costs:admin",
                "knowledge:read",
                "knowledge:write",
            ],
        },
        {
            "name": "client_portal",
            "display_name": "Client Portal (External)",
            "description": "Limited read access for external clients",
            "permissions": ["goals:read"],
            "conditions": {"matter_access": "client_own_matters"},
        },
    ],
    "finance": [
        {
            "name": "analyst",
            "display_name": "Financial Analyst",
            "description": "Read-only financial analysis",
            "permissions": ["goals:read", "knowledge:read", "costs:read"],
        },
        {
            "name": "trader",
            "display_name": "Trader",
            "description": "Execute trading goals within risk limits",
            "permissions": [
                "goals:read",
                "goals:write",
                "goals:execute",
                "knowledge:read",
            ],
            "conditions": {
                "risk_limit": "within_daily_var",
                "time_window": {
                    "start": "09:30",
                    "end": "16:00",
                    "tz": "America/New_York",
                },
            },
        },
        {
            "name": "risk_officer",
            "display_name": "Chief Risk Officer",
            "description": "Governance, policy, and emergency stop authority",
            "permissions": [
                "goals:read",
                "goals:delete",
                "governance:read",
                "governance:write",
                "governance:approve",
                "audit:read",
                "costs:admin",
            ],
        },
        {
            "name": "sox_compliance_manager",
            "display_name": "SOX Compliance Manager",
            "description": "SOX/FINRA compliance oversight and audit export",
            "permissions": [
                "audit:read",
                "audit:export",
                "governance:read",
                "costs:read",
            ],
        },
    ],
    "education": [
        {
            "name": "student",
            "display_name": "Student",
            "description": "Access own learning goals only",
            "permissions": ["goals:read", "goals:execute"],
            "conditions": {"ownership": "creator"},
        },
        {
            "name": "instructor",
            "display_name": "Instructor",
            "description": "Manage course goals and student progress",
            "permissions": [
                "goals:read",
                "goals:write",
                "goals:execute",
                "agents:read",
                "knowledge:read",
                "knowledge:write",
            ],
            "conditions": {"course_access": "assigned_courses"},
        },
        {
            "name": "institution_admin",
            "display_name": "Institution Administrator",
            "description": "Full institutional access",
            "permissions": [
                "goals:read",
                "goals:write",
                "goals:delete",
                "agents:read",
                "agents:write",
                "tenancy:read",
                "tenancy:write",
                "audit:read",
                "costs:admin",
            ],
        },
    ],
    "ecommerce": [
        {
            "name": "catalog_manager",
            "display_name": "Catalog Manager",
            "description": "Product catalog automation",
            "permissions": [
                "goals:read",
                "goals:write",
                "agents:read",
                "knowledge:read",
                "knowledge:write",
            ],
        },
        {
            "name": "customer_success",
            "display_name": "Customer Success",
            "description": "Customer support automation — assigned region",
            "permissions": [
                "goals:read",
                "goals:write",
                "goals:execute",
                "knowledge:read",
            ],
            "conditions": {"department_match": True},
        },
        {
            "name": "operations_lead",
            "display_name": "Operations Lead",
            "description": "Full operational access excluding financial admin",
            "permissions": [
                "goals:read",
                "goals:write",
                "goals:execute",
                "agents:read",
                "agents:write",
                "knowledge:read",
                "knowledge:write",
                "costs:read",
            ],
        },
    ],
}
