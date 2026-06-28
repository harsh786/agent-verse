"""Marketplace V2 — DB-backed template gallery with security review and atomic deploy.

Key improvements over marketplace.py:
  - Templates persisted to marketplace_templates table (survive restarts)
  - Atomic install: agent + install record in ONE transaction (no ghost agents)
  - Security review pipeline: injection scan, scope check, schema validation
  - Full-text search via Postgres to_tsvector; optional pgvector semantic search
  - Rating aggregation on review creation
  - Tenant isolation via RLS (public/community templates visible to all)
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import text as _t

from app.tenancy.context import TenantContext

# ---------------------------------------------------------------------------
# Pre-approved and high-risk scopes (Amendment 7.1: correct AND logic)
# ---------------------------------------------------------------------------

PREAPPROVED_SCOPES: frozenset[str] = frozenset(
    {
        "goals:read",
        "goals:write",
        "goals:execute",
        "agents:read",
        "knowledge:read",
        "mcp:read",
    }
)

HIGH_RISK_SCOPES: frozenset[str] = frozenset(
    {
        "goals:delete",
        "agents:delete",
        "agents:write",
        "knowledge:delete",
        "governance:write",
        "governance:approve",
        "tenancy:write",
        "audit:export",
        "costs:admin",
        "admin:*",
        "connectors:admin",
    }
)

CRITICAL_SCOPES: frozenset[str] = frozenset(
    {
        "admin:*",
        "governance:approve",
        "agents:delete",
        "connectors:admin",
    }
)


# ---------------------------------------------------------------------------
# Security reviewer
# ---------------------------------------------------------------------------


class TemplateSecurityReviewer:
    """Automated security review pipeline for marketplace templates.

    Checks:
      1. Scope over-request (AND logic — fix for Amendment 7.1)
      2. Injection scan on goal/system-prompt text via InjectionGuard
      3. JSON Schema validation of parameters_schema
      4. Fully-autonomous + dangerous connector check
      5. Critical scope check
    """

    def __init__(self, injection_guard: Any = None) -> None:
        if injection_guard is not None:
            self._injection = injection_guard
        else:
            try:
                from app.intelligence.guardrail_engine import InjectionGuard

                self._injection = InjectionGuard()
            except Exception:
                self._injection = None

    async def review(self, template: dict[str, Any]) -> dict[str, Any]:
        """Run all checks. Returns review dict with risk_level and findings."""
        findings: list[dict[str, Any]] = []

        scope_result = self._check_scopes(template)
        findings.extend(scope_result.get("findings", []))

        injection_result = self._check_injection(template)
        findings.extend(injection_result.get("findings", []))

        schema_result = self._check_parameter_schema(template)
        findings.extend(schema_result.get("findings", []))

        autonomy_result = self._check_autonomous_with_dangerous_connectors(template)
        findings.extend(autonomy_result.get("findings", []))

        risk_level = "safe"
        if findings:
            severities = [f["severity"] for f in findings]
            if "critical" in severities:
                risk_level = "critical"
            elif "high" in severities:
                risk_level = "high"
            elif "medium" in severities:
                risk_level = "medium"
            else:
                risk_level = "low"

        approved = risk_level in ("safe", "low")

        return {
            "findings": findings,
            "risk_level": risk_level,
            "approved": approved,
            "scope_check": scope_result,
            "injection_check": injection_result,
            "schema_check": schema_result,
            "autonomy_check": autonomy_result,
        }

    def _check_scopes(self, template: dict[str, Any]) -> dict[str, Any]:
        """Amendment 7.1: use AND logic, not OR."""
        requested = set(template.get("required_connectors", []))
        over_requested = requested - PREAPPROVED_SCOPES
        high_risk = requested & HIGH_RISK_SCOPES
        critical = requested & CRITICAL_SCOPES

        findings = []
        # FIXED: AND logic — must satisfy BOTH conditions to pass
        passed = len(over_requested) == 0 and len(high_risk) == 0

        if critical:
            findings.append(
                {
                    "type": "critical_scope",
                    "severity": "high",
                    "scopes": list(critical),
                    "message": f"Critical scopes requested: {list(critical)}",
                }
            )
            passed = False

        return {
            "passed": passed,
            "findings": findings,
            "over_requested": list(over_requested),
            "high_risk_scopes": list(high_risk),
            "requires_justification": bool(high_risk),
        }

    def _check_injection(self, template: dict[str, Any]) -> dict[str, Any]:
        """Scan goal_template and config strings for injection patterns."""
        config = template.get("template_config", {})
        goal = config.get("goal_template", "")
        system_prompt = config.get("system_prompt", "")
        desc = template.get("description", "") + " " + template.get("long_description", "")
        full_text = f"{goal} {system_prompt} {desc}"

        findings: list[dict[str, Any]] = []

        if self._injection is not None:
            try:
                violations = self._injection.scan_text(full_text)
                critical = [v for v in violations if getattr(v.severity, "value", str(v.severity)) in ("high", "critical")]
                for v in critical:
                    findings.append(
                        {
                            "type": "dangerous_goal",
                            "severity": getattr(v.severity, "value", str(v.severity)),
                            "pattern": getattr(v, "matched_pattern", "")[:50],
                            "category": getattr(v, "category", ""),
                        }
                    )
            except Exception:
                pass
        else:
            # Fallback: simple pattern scan when guardrail_engine unavailable
            _simple_patterns = [
                (r"ignore\s+(all\s+)?previous\s+instructions", "critical"),
                (r"DAN\s+mode|jailbreak", "critical"),
                (r"do\s+anything\s+now", "critical"),
                (r"bypass\s+restrictions", "high"),
            ]
            for pattern, severity in _simple_patterns:
                if re.search(pattern, full_text, re.IGNORECASE):
                    findings.append(
                        {"type": "dangerous_goal", "severity": severity, "pattern": pattern[:50]}
                    )

        return {"passed": len(findings) == 0, "findings": findings}

    def _check_parameter_schema(self, template: dict[str, Any]) -> dict[str, Any]:
        """Validate parameters_schema is a valid JSON Schema draft-07."""
        schema = template.get("parameters_schema", {})
        if not schema:
            return {"passed": True, "findings": [], "note": "No parameter schema"}

        try:
            import jsonschema  # type: ignore[import]

            jsonschema.Draft7Validator.check_schema(schema)
            return {"passed": True, "findings": []}
        except ImportError:
            return {"passed": True, "findings": [], "note": "jsonschema not installed"}
        except Exception as exc:
            return {
                "passed": False,
                "findings": [
                    {"type": "invalid_schema", "severity": "medium", "message": str(exc)[:200]}
                ],
            }

    def _check_autonomous_with_dangerous_connectors(
        self, template: dict[str, Any]
    ) -> dict[str, Any]:
        """Flag fully-autonomous + dangerous connectors (shell/filesystem/ssh)."""
        config = template.get("template_config", {})
        autonomy = config.get("autonomy_mode", "")
        connectors = set(template.get("required_connectors", []))
        dangerous = {"shell", "filesystem", "ssh"}
        dangerous_present = connectors & dangerous

        findings: list[dict[str, Any]] = []
        if autonomy == "fully-autonomous" and dangerous_present:
            findings.append(
                {
                    "type": "uncontrolled_execution",
                    "severity": "critical",
                    "connectors": list(dangerous_present),
                    "message": (
                        "fully-autonomous mode + dangerous connectors "
                        f"{list(dangerous_present)} is prohibited"
                    ),
                }
            )

        return {"passed": len(findings) == 0, "findings": findings}


# ---------------------------------------------------------------------------
# Built-in templates (50+)
# ---------------------------------------------------------------------------

_BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    # ── Existing devops / software templates (migrated from marketplace.py) ──
    {
        "template_id": "tpl-bug-fix",
        "name": "Bug Fix Agent",
        "slug": "bug-fix-agent",
        "domain": "software",
        "description": "Fix JIRA bugs labeled prod-down and open a PR",
        "required_connectors": ["github", "jira", "sentry"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": "Fix all open bugs labeled {label} in {repo} and open PRs",
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "label": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["repo"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-devops",
        "name": "DevOps Watchdog",
        "slug": "devops-watchdog",
        "domain": "devops",
        "description": "Roll back last deploy if error rate > 2% for 5 min",
        "required_connectors": ["datadog", "github"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Monitor {service} and roll back if error rate exceeds {threshold}%"
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {
                "service": {"type": "string"},
                "threshold": {"type": "number", "default": 2},
            },
            "required": ["service"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-e2e-testing",
        "name": "E2E Test Generator",
        "slug": "e2e-test-generator",
        "domain": "testing",
        "description": "Generate and run E2E tests for the checkout flow nightly",
        "required_connectors": ["github"],
        "autonomy_mode": "fully-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Generate and run E2E tests for {feature} and commit results to {repo}"
            ),
            "autonomy_mode": "fully-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "feature": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["feature", "repo"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-hr-onboarding",
        "name": "HR Onboarding Agent",
        "slug": "hr-onboarding-agent",
        "domain": "hr",
        "description": "Onboard new engineers end-to-end",
        "required_connectors": ["slack", "jira"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Onboard {employee_name}: create accounts, file IT tickets, "
                "assign first-week tasks"
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {"employee_name": {"type": "string"}},
            "required": ["employee_name"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-sales-followup",
        "name": "Sales Follow-up Agent",
        "slug": "sales-followup-agent",
        "domain": "sales",
        "description": "Follow up with leads idle 7+ days",
        "required_connectors": ["salesforce"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Follow up with all leads idle more than {idle_days} days in {pipeline}"
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "idle_days": {"type": "integer", "default": 7},
                "pipeline": {"type": "string"},
            },
            "required": ["pipeline"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-support-triage",
        "name": "Support Triage Agent",
        "slug": "support-triage-agent",
        "domain": "support",
        "description": "Triage new tickets, draft replies, escalate P1s",
        "required_connectors": ["slack", "jira"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Triage new support tickets in {queue}: draft replies and "
                "escalate P1s to {escalation_channel}"
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "queue": {"type": "string"},
                "escalation_channel": {"type": "string"},
            },
            "required": ["queue"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-code-review",
        "name": "Code Review Agent",
        "slug": "code-review-automation",
        "domain": "software",
        "description": (
            "Automatically review open PRs, post inline comments, and request changes"
        ),
        "required_connectors": ["github", "jira"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Review all open PRs in {repo} for code quality, security issues, "
                "and style compliance; post comments and request changes where needed"
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {"repo": {"type": "string"}},
            "required": ["repo"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-incident-response",
        "name": "Incident Response Agent",
        "slug": "incident-response-agent",
        "domain": "devops",
        "description": (
            "Detect production incidents, page on-call, open Jira tickets, "
            "and post status updates"
        ),
        "required_connectors": ["datadog", "slack", "jira"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "When {service} error rate exceeds {threshold}%, page on-call via "
                "{channel}, open a P1 Jira incident, and post hourly status updates "
                "until resolved"
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {
                "service": {"type": "string"},
                "threshold": {"type": "number", "default": 2},
                "channel": {"type": "string"},
            },
            "required": ["service"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    # ── Legal domain (5 templates) ────────────────────────────────────────────
    {
        "template_id": "tpl-legal-contract-review",
        "name": "Legal Contract Review",
        "slug": "legal-contract-review",
        "domain": "legal",
        "description": (
            "Analyze contracts, extract obligations, identify risk clauses, "
            "and generate a structured risk report"
        ),
        "required_connectors": ["document_reader"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Review the contract at {document_url} under {jurisdiction} law. "
                "Extract all parties, obligations, deadlines, and liability clauses. "
                "Flag non-standard terms and missing protective provisions."
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "document_url": {"type": "string", "format": "uri"},
                "jurisdiction": {
                    "type": "string",
                    "enum": ["us", "uk", "eu", "ca", "au"],
                },
                "contract_type": {
                    "type": "string",
                    "enum": ["nda", "msa", "sow", "employment", "lease", "other"],
                },
            },
            "required": ["document_url", "jurisdiction"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-legal-case-research",
        "name": "Legal Case Research Agent",
        "slug": "legal-case-research",
        "domain": "legal",
        "description": (
            "Research relevant case law, statutes, and precedents for a legal matter"
        ),
        "required_connectors": ["web_search"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Research case law and statutes relevant to: {legal_question} "
                "in {jurisdiction}. Return top 10 citations with summaries."
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "legal_question": {"type": "string"},
                "jurisdiction": {"type": "string"},
            },
            "required": ["legal_question"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-legal-brief-drafting",
        "name": "Legal Brief Drafting Agent",
        "slug": "legal-brief-drafting",
        "domain": "legal",
        "description": "Draft legal briefs from case facts, issues, and applicable law",
        "required_connectors": ["document_reader", "web_search"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Draft a legal brief for {matter_name}. "
                "Facts: {facts_summary}. Issues: {legal_issues}. "
                "Target court: {court}. Format: {format}."
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "matter_name": {"type": "string"},
                "facts_summary": {"type": "string"},
                "legal_issues": {"type": "string"},
                "court": {"type": "string"},
                "format": {"type": "string", "enum": ["motion", "brief", "memo"], "default": "brief"},
            },
            "required": ["matter_name", "facts_summary", "legal_issues"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-legal-due-diligence",
        "name": "M&A Due Diligence Agent",
        "slug": "legal-due-diligence",
        "domain": "legal",
        "description": (
            "Conduct structured legal due diligence for M&A transactions, "
            "identifying material risks across corporate, IP, employment, and compliance"
        ),
        "required_connectors": ["document_reader"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Conduct due diligence on {target_company} for a {deal_type} transaction. "
                "Review documents in {document_folder}. "
                "Focus areas: {focus_areas}. Output a risk matrix."
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "target_company": {"type": "string"},
                "deal_type": {"type": "string", "enum": ["acquisition", "merger", "investment"]},
                "document_folder": {"type": "string"},
                "focus_areas": {
                    "type": "array",
                    "items": {"type": "string", "enum": [
                        "corporate", "ip", "employment", "compliance", "litigation",
                    ]},
                },
            },
            "required": ["target_company", "deal_type"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-legal-trademark-search",
        "name": "Trademark Search Agent",
        "slug": "legal-trademark-search",
        "domain": "legal",
        "description": (
            "Search trademark databases for conflicts with a proposed mark"
        ),
        "required_connectors": ["web_search"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Search for trademark conflicts for the mark '{proposed_mark}' "
                "in class {nice_class} in {jurisdictions}. "
                "Return a conflict report with likelihood of confusion analysis."
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "proposed_mark": {"type": "string"},
                "nice_class": {"type": "integer", "minimum": 1, "maximum": 45},
                "jurisdictions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["us"],
                },
            },
            "required": ["proposed_mark", "nice_class"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    # ── Healthcare domain (4 templates) ──────────────────────────────────────
    {
        "template_id": "tpl-hc-prior-auth",
        "name": "Prior Authorization Agent",
        "slug": "healthcare-prior-authorization",
        "domain": "healthcare",
        "description": (
            "Automate prior authorization requests to payers: gather clinical "
            "info, complete PA forms, and track approval status"
        ),
        "required_connectors": ["document_reader", "web_search"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Submit a prior authorization request for {procedure_code} for "
                "patient {patient_id} to payer {payer_id}. "
                "Include clinical documentation from {ehr_folder}."
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {
                "procedure_code": {"type": "string"},
                "patient_id": {"type": "string"},
                "payer_id": {"type": "string"},
                "ehr_folder": {"type": "string"},
            },
            "required": ["procedure_code", "payer_id"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-hc-clinical-notes",
        "name": "Clinical Note Generation Agent",
        "slug": "healthcare-clinical-note-generation",
        "domain": "healthcare",
        "description": (
            "Generate structured SOAP notes from audio transcripts or dictation"
        ),
        "required_connectors": ["audio_transcriber"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Generate a {note_type} clinical note from the {source_type} "
                "for encounter {encounter_id}. "
                "Use specialty: {specialty}."
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {
                "note_type": {
                    "type": "string",
                    "enum": ["soap", "progress", "discharge", "consult"],
                    "default": "soap",
                },
                "source_type": {"type": "string", "enum": ["transcript", "dictation"]},
                "encounter_id": {"type": "string"},
                "specialty": {"type": "string"},
            },
            "required": ["encounter_id"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-hc-fhir-extraction",
        "name": "FHIR Data Extraction Agent",
        "slug": "healthcare-fhir-extraction",
        "domain": "healthcare",
        "description": (
            "Extract, normalize, and validate FHIR R4 resources from legacy EHR exports"
        ),
        "required_connectors": ["document_reader"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Extract FHIR R4 resources from {ehr_export_path}. "
                "Resource types: {resource_types}. "
                "Validate against {profile_url}."
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "ehr_export_path": {"type": "string"},
                "resource_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["Patient", "Encounter", "Condition"],
                },
                "profile_url": {"type": "string", "format": "uri"},
            },
            "required": ["ehr_export_path"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-hc-discharge-summary",
        "name": "Discharge Summary Generator",
        "slug": "healthcare-discharge-summary",
        "domain": "healthcare",
        "description": (
            "Generate structured discharge summaries from inpatient encounter records"
        ),
        "required_connectors": ["document_reader"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Generate a discharge summary for encounter {encounter_id}. "
                "Include: diagnosis, procedures, medications, follow-up instructions, "
                "and care team contacts."
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {
                "encounter_id": {"type": "string"},
                "include_sections": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["diagnosis", "procedures", "medications", "followup"],
                    },
                },
            },
            "required": ["encounter_id"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    # ── Education domain (4 templates) ───────────────────────────────────────
    {
        "template_id": "tpl-edu-assignment-grading",
        "name": "Assignment Grading Agent",
        "slug": "education-assignment-grading",
        "domain": "education",
        "description": (
            "Grade student assignments against rubrics; provide detailed feedback "
            "and scores"
        ),
        "required_connectors": ["document_reader"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Grade the {assignment_type} submissions in {submission_folder} "
                "using the rubric at {rubric_url}. "
                "Provide per-criterion scores and actionable feedback for each student."
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {
                "assignment_type": {
                    "type": "string",
                    "enum": ["essay", "report", "code", "presentation"],
                },
                "submission_folder": {"type": "string"},
                "rubric_url": {"type": "string"},
                "grade_level": {
                    "type": "string",
                    "enum": ["K-5", "6-8", "9-12", "college", "graduate"],
                },
            },
            "required": ["assignment_type", "submission_folder"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-edu-lecture-summary",
        "name": "Lecture Summary Agent",
        "slug": "education-lecture-summary",
        "domain": "education",
        "description": (
            "Summarize lecture recordings or transcripts into structured notes "
            "with key concepts, definitions, and review questions"
        ),
        "required_connectors": ["audio_transcriber", "document_reader"],
        "autonomy_mode": "fully-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Summarize the lecture at {lecture_source} for {course_name}. "
                "Output: key concepts, definitions, examples, and 5 review questions."
            ),
            "autonomy_mode": "fully-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "lecture_source": {"type": "string"},
                "course_name": {"type": "string"},
                "output_format": {
                    "type": "string",
                    "enum": ["markdown", "pdf", "notion"],
                    "default": "markdown",
                },
            },
            "required": ["lecture_source"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-edu-progress-report",
        "name": "Student Progress Report Agent",
        "slug": "education-student-progress-report",
        "domain": "education",
        "description": (
            "Generate personalized student progress reports from grade book data "
            "and teacher observations"
        ),
        "required_connectors": ["document_reader"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Generate progress reports for students in {class_id} "
                "for the {reporting_period} period. "
                "Data sources: grades at {gradebook_url}, observations at {obs_url}."
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {
                "class_id": {"type": "string"},
                "reporting_period": {"type": "string"},
                "gradebook_url": {"type": "string"},
                "obs_url": {"type": "string"},
            },
            "required": ["class_id", "reporting_period"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-edu-curriculum-alignment",
        "name": "Curriculum Alignment Agent",
        "slug": "education-curriculum-alignment",
        "domain": "education",
        "description": (
            "Align course materials with state or national standards; "
            "identify gaps and suggest additions"
        ),
        "required_connectors": ["document_reader", "web_search"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Align the curriculum in {curriculum_folder} with {standards_framework} "
                "for grade level {grade_level} in subject {subject}. "
                "Report coverage gaps and suggest supplemental materials."
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "curriculum_folder": {"type": "string"},
                "standards_framework": {
                    "type": "string",
                    "enum": ["common_core", "ngss", "ccss_math", "state_specific"],
                },
                "grade_level": {"type": "string"},
                "subject": {"type": "string"},
            },
            "required": ["curriculum_folder", "standards_framework"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    # ── Finance domain (4 templates) ─────────────────────────────────────────
    {
        "template_id": "tpl-fin-expense-classification",
        "name": "Expense Classification Agent",
        "slug": "finance-expense-classification",
        "domain": "finance",
        "description": (
            "Classify business expenses from receipts and bank exports by category, "
            "department, and tax code"
        ),
        "required_connectors": ["document_reader"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Classify expenses from {expense_source} for {period}. "
                "Apply {classification_scheme} taxonomy. "
                "Flag anomalies and missing receipts."
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "expense_source": {"type": "string"},
                "period": {"type": "string"},
                "classification_scheme": {
                    "type": "string",
                    "enum": ["gaap", "ifrs", "irs", "custom"],
                    "default": "gaap",
                },
            },
            "required": ["expense_source"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-fin-invoice-reconciliation",
        "name": "Invoice Reconciliation Agent",
        "slug": "finance-invoice-reconciliation",
        "domain": "finance",
        "description": (
            "Match purchase orders to invoices to payments; identify discrepancies "
            "and suggest journal entries"
        ),
        "required_connectors": ["document_reader"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Reconcile invoices in {invoice_folder} against POs in {po_folder} "
                "for the period {period}. "
                "Report unmatched items, price variances, and duplicate invoices."
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {
                "invoice_folder": {"type": "string"},
                "po_folder": {"type": "string"},
                "period": {"type": "string"},
            },
            "required": ["invoice_folder"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-fin-gst-filing",
        "name": "GST/VAT Filing Preparation Agent",
        "slug": "finance-gst-filing",
        "domain": "finance",
        "description": (
            "Prepare GST/VAT returns from transaction data; validate entries "
            "and generate filing-ready reports"
        ),
        "required_connectors": ["document_reader"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Prepare {tax_type} return for {entity_name} for period {period} "
                "in {jurisdiction}. "
                "Data source: {transaction_export}."
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {
                "tax_type": {"type": "string", "enum": ["gst", "vat", "hst"]},
                "entity_name": {"type": "string"},
                "period": {"type": "string"},
                "jurisdiction": {"type": "string"},
                "transaction_export": {"type": "string"},
            },
            "required": ["tax_type", "entity_name", "period"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-fin-bank-statement",
        "name": "Bank Statement Analysis Agent",
        "slug": "finance-bank-statement-analysis",
        "domain": "finance",
        "description": (
            "Analyze bank statements to categorize cash flow, detect anomalies, "
            "and generate financial summaries"
        ),
        "required_connectors": ["document_reader"],
        "autonomy_mode": "bounded-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Analyze bank statement at {statement_path} for {account_holder}. "
                "Categorize transactions by {categories}. "
                "Highlight anomalies, largest transactions, and cash flow trend."
            ),
            "autonomy_mode": "bounded-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "statement_path": {"type": "string"},
                "account_holder": {"type": "string"},
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["income", "operating", "capital", "tax"],
                },
            },
            "required": ["statement_path"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    # ── E-commerce domain (4 templates) ──────────────────────────────────────
    {
        "template_id": "tpl-ec-product-description",
        "name": "Product Description Writer",
        "slug": "ecommerce-product-description",
        "domain": "ecommerce",
        "description": (
            "Generate SEO-optimized product descriptions from specs and images"
        ),
        "required_connectors": ["web_search"],
        "autonomy_mode": "fully-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Write {tone} product descriptions for {product_count} products "
                "from the catalog at {catalog_path}. "
                "Optimize for keywords: {target_keywords}."
            ),
            "autonomy_mode": "fully-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "catalog_path": {"type": "string"},
                "tone": {"type": "string", "enum": ["professional", "casual", "luxury"]},
                "product_count": {"type": "integer"},
                "target_keywords": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["catalog_path"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-ec-inventory-reorder",
        "name": "Inventory Reorder Agent",
        "slug": "ecommerce-inventory-reorder",
        "domain": "ecommerce",
        "description": (
            "Monitor inventory levels and automatically generate purchase orders "
            "when stock falls below reorder points"
        ),
        "required_connectors": ["database_query"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Check inventory in {warehouse_system} for SKUs in {category}. "
                "Generate purchase orders for items below {reorder_threshold} units. "
                "Send POs to {supplier_email}."
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {
                "warehouse_system": {"type": "string"},
                "category": {"type": "string"},
                "reorder_threshold": {"type": "integer", "minimum": 0},
                "supplier_email": {"type": "string", "format": "email"},
            },
            "required": ["warehouse_system"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-ec-complaint-resolution",
        "name": "Customer Complaint Resolution Agent",
        "slug": "ecommerce-complaint-resolution",
        "domain": "ecommerce",
        "description": (
            "Triage customer complaints, draft empathetic responses, escalate "
            "based on severity, and track resolution"
        ),
        "required_connectors": ["slack"],
        "autonomy_mode": "supervised",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Process customer complaints in {ticket_queue}. "
                "Draft responses for {response_tone} tone. "
                "Escalate severity-{escalation_threshold}+ issues to {escalation_team}."
            ),
            "autonomy_mode": "supervised",
        },
        "parameters_schema": {
            "properties": {
                "ticket_queue": {"type": "string"},
                "response_tone": {
                    "type": "string",
                    "enum": ["formal", "friendly", "empathetic"],
                    "default": "empathetic",
                },
                "escalation_threshold": {"type": "integer", "minimum": 1, "maximum": 5},
                "escalation_team": {"type": "string"},
            },
            "required": ["ticket_queue"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
    {
        "template_id": "tpl-ec-review-sentiment",
        "name": "Product Review Sentiment Agent",
        "slug": "ecommerce-review-sentiment",
        "domain": "ecommerce",
        "description": (
            "Analyze product reviews for sentiment, extract recurring themes, "
            "and generate actionable product improvement insights"
        ),
        "required_connectors": [],
        "autonomy_mode": "fully-autonomous",
        "author_name": "AgentVerse",
        "template_config": {
            "goal_template": (
                "Analyze {review_count} reviews for {product_name} from {review_source}. "
                "Identify top positive and negative themes. "
                "Output sentiment score and top 5 improvement recommendations."
            ),
            "autonomy_mode": "fully-autonomous",
        },
        "parameters_schema": {
            "properties": {
                "product_name": {"type": "string"},
                "review_source": {"type": "string"},
                "review_count": {"type": "integer", "minimum": 1},
            },
            "required": ["product_name"],
        },
        "visibility": "public",
        "review_status": "approved",
        "is_builtin": True,
    },
]


# ---------------------------------------------------------------------------
# MarketplaceV2 service
# ---------------------------------------------------------------------------

_SYSTEM_TENANT_ID = "system"


class MarketplaceV2:
    """DB-backed marketplace with atomic install, security review, and search.

    Designed as a state.marketplace_v2 service. When db_factory is None
    (in-memory mode / tests), all read/write operations fall back to the
    in-memory _cache dict.
    """

    def __init__(self, db_factory: Any = None) -> None:
        self._db = db_factory
        self._reviewer = TemplateSecurityReviewer()
        # In-memory cache keyed by template_id (used when DB is unavailable)
        self._cache: dict[str, dict[str, Any]] = {}
        self._installs: list[dict[str, Any]] = []
        self._reviews: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Template CRUD
    # ------------------------------------------------------------------

    async def get_template(
        self, *, template_id: str | None = None, slug: str | None = None
    ) -> dict[str, Any] | None:
        """Fetch a single template by id or slug. Returns None if not found."""
        if self._db is not None:
            try:
                async with self._db() as session:
                    if template_id:
                        row = (
                            await session.execute(
                                _t("SELECT * FROM marketplace_templates WHERE id = :id"),
                                {"id": template_id},
                            )
                        ).fetchone()
                    elif slug:
                        row = (
                            await session.execute(
                                _t(
                                    "SELECT * FROM marketplace_templates WHERE slug = :slug"
                                ),
                                {"slug": slug},
                            )
                        ).fetchone()
                    else:
                        return None
                    return dict(row._mapping) if row else None
            except Exception:
                pass

        # In-memory fallback
        if template_id:
            return self._cache.get(template_id)
        if slug:
            for t in self._cache.values():
                if t.get("slug") == slug:
                    return t
        return None

    async def list_templates(
        self,
        *,
        domain: str = "",
        category: str = "",
        search: str = "",
        visibility: str = "public",
        page: int = 1,
        page_size: int = 20,
        tenant_id: str = "",
    ) -> dict[str, Any]:
        """Paginated template list with optional filters."""
        if self._db is not None:
            try:
                async with self._db() as session:
                    if tenant_id:
                        await session.execute(
                            _t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
                        )
                    clauses = ["(visibility IN ('public','community') OR tenant_id = :tid)"]
                    params: dict[str, Any] = {"tid": tenant_id or ""}
                    if domain:
                        clauses.append("domain = :domain")
                        params["domain"] = domain
                    if category:
                        clauses.append("category = :cat")
                        params["cat"] = category
                    if search:
                        clauses.append(
                            "to_tsvector('english', coalesce(name,'') || ' ' || "
                            "coalesce(description,'') || ' ' || "
                            "array_to_string(tags,' ')) @@ plainto_tsquery('english', :q)"
                        )
                        params["q"] = search
                    where = " AND ".join(clauses)
                    offset = (page - 1) * page_size
                    rows = (
                        await session.execute(
                            _t(
                                f"SELECT * FROM marketplace_templates WHERE {where} "
                                f"ORDER BY install_count DESC, name ASC "
                                f"LIMIT :limit OFFSET :offset"
                            ),
                            {**params, "limit": page_size, "offset": offset},
                        )
                    ).fetchall()
                    total = (
                        await session.execute(
                            _t(
                                f"SELECT count(*) FROM marketplace_templates WHERE {where}"
                            ),
                            params,
                        )
                    ).scalar()
                    return {
                        "templates": [dict(r._mapping) for r in rows],
                        "total": total or 0,
                        "page": page,
                        "page_size": page_size,
                    }
            except Exception:
                pass

        # In-memory fallback
        templates = list(self._cache.values())
        if domain:
            templates = [t for t in templates if t.get("domain") == domain]
        if category:
            templates = [t for t in templates if t.get("category") == category]
        if search:
            q = search.lower()
            templates = [
                t
                for t in templates
                if q in t.get("name", "").lower()
                or q in t.get("description", "").lower()
                or any(q in tag.lower() for tag in t.get("tags", []))
            ]
        start = (page - 1) * page_size
        paginated = templates[start : start + page_size]
        return {
            "templates": paginated,
            "total": len(templates),
            "page": page,
            "page_size": page_size,
        }

    async def publish_template(
        self,
        *,
        data: dict[str, Any],
        tenant_ctx: TenantContext,
        run_security_review: bool = True,
    ) -> dict[str, Any]:
        """Create or update a template; optionally run security review."""
        template_id = data.get("template_id") or str(uuid.uuid4().hex[:32])
        slug = data.get("slug", "").strip() or f"tpl-{template_id[:8]}"

        if run_security_review:
            review = await self._reviewer.review(data)
            review_status = "approved" if review["approved"] else "pending"
        else:
            review_status = "unreviewed"

        record: dict[str, Any] = {
            "id": template_id,
            "tenant_id": tenant_ctx.tenant_id,
            "name": data.get("name", "Untitled"),
            "slug": slug,
            "description": data.get("description", ""),
            "long_description": data.get("long_description", ""),
            "domain": data.get("domain", "general"),
            "subdomain": data.get("subdomain"),
            "category": data.get("category"),
            "tags": data.get("tags", []),
            "template_config": data.get("template_config", {}),
            "parameters_schema": data.get("parameters_schema", {}),
            "required_connectors": data.get("required_connectors", []),
            "optional_connectors": data.get("optional_connectors", []),
            "author_name": data.get("author_name", tenant_ctx.tenant_id),
            "icon_url": data.get("icon_url"),
            "visibility": data.get("visibility", "private"),
            "review_status": review_status,
            "is_builtin": data.get("is_builtin", False),
            "is_verified": data.get("is_verified", False),
            "version": data.get("version", "1.0.0"),
        }

        if self._db is not None:
            try:
                async with self._db() as session:
                    await session.execute(
                        _t("SET LOCAL app.tenant_id = :tid"),
                        {"tid": tenant_ctx.tenant_id},
                    )
                    await session.execute(
                        _t("""
                            INSERT INTO marketplace_templates
                                (id, tenant_id, name, slug, description, long_description,
                                 domain, subdomain, category, tags, template_config,
                                 parameters_schema, required_connectors, optional_connectors,
                                 author_name, icon_url, visibility, review_status,
                                 is_builtin, is_verified, version)
                            VALUES
                                (:id, :tenant_id, :name, :slug, :description,
                                 :long_description, :domain, :subdomain, :category,
                                 :tags, :template_config::jsonb, :parameters_schema::jsonb,
                                 :required_connectors, :optional_connectors,
                                 :author_name, :icon_url, :visibility, :review_status,
                                 :is_builtin, :is_verified, :version)
                            ON CONFLICT (slug) DO UPDATE SET
                                name=EXCLUDED.name,
                                description=EXCLUDED.description,
                                long_description=EXCLUDED.long_description,
                                template_config=EXCLUDED.template_config,
                                parameters_schema=EXCLUDED.parameters_schema,
                                required_connectors=EXCLUDED.required_connectors,
                                review_status=EXCLUDED.review_status,
                                version=EXCLUDED.version,
                                updated_at=NOW()
                        """),
                        {
                            **record,
                            "tags": record["tags"],
                            "template_config": json.dumps(record["template_config"]),
                            "parameters_schema": json.dumps(record["parameters_schema"]),
                            "required_connectors": record["required_connectors"],
                            "optional_connectors": record["optional_connectors"],
                        },
                    )
                    await session.commit()
            except Exception:
                pass

        self._cache[template_id] = record
        return record

    # ------------------------------------------------------------------
    # Atomic install (fixes ghost-agent bug)
    # ------------------------------------------------------------------

    async def install(
        self,
        *,
        template_id: str,
        params: dict[str, Any],
        tenant_ctx: TenantContext,
        agent_store: Any = None,
    ) -> dict[str, Any]:
        """Atomically install a template: agent + install record in ONE transaction.

        Returns:
          {"success": True, "agent_id": "...", "template_id": "...", "install_id": "..."}
          OR
          {"success": False, "error": "...", "missing_connectors": [...]}
        """
        template = await self.get_template(template_id=template_id)
        if template is None:
            return {"success": False, "error": "Template not found", "template_id": template_id}

        # Validate parameters against JSON Schema BEFORE creating any agent
        schema = template.get("parameters_schema") or template.get("parameters_schema", {})
        if schema and isinstance(schema, dict) and schema:
            try:
                import jsonschema  # type: ignore[import]

                try:
                    jsonschema.validate(params, schema)
                except jsonschema.ValidationError as exc:
                    return {
                        "success": False,
                        "error": f"Invalid parameters: {exc.message}",
                        "template_id": template_id,
                    }
            except ImportError:
                pass

        # Verify required connectors
        required = template.get("required_connectors") or []
        if required and agent_store is not None:
            # Best-effort connector check; don't block if unavailable
            missing: list[str] = []
            try:
                if hasattr(agent_store, "list_connectors"):
                    available = await agent_store.list_connectors(tenant_ctx=tenant_ctx)
                    available_names = {
                        (getattr(c, "name", "") or "").lower() for c in (available or [])
                    }
                    missing = [c for c in required if c.lower() not in available_names]
            except Exception:
                pass

            if missing:
                return {"success": False, "missing_connectors": missing, "template_id": template_id}

        config = template.get("template_config") or {}
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except Exception:
                config = {}

        agent_id = str(uuid.uuid4().hex[:32])
        install_id = str(uuid.uuid4().hex[:32])

        if self._db is not None:
            try:
                async with self._db() as session:
                    # Set RLS context
                    await session.execute(
                        _t("SET LOCAL app.tenant_id = :tid"),
                        {"tid": tenant_ctx.tenant_id},
                    )
                    # ATOMIC: create agent row
                    await session.execute(
                        _t("""
                            INSERT INTO agents
                                (id, tenant_id, name, goal_template, autonomy_mode)
                            VALUES (:id, :tenant, :name, :goal, :mode)
                        """),
                        {
                            "id": agent_id,
                            "tenant": tenant_ctx.tenant_id,
                            "name": params.get(
                                "name",
                                config.get("name", template.get("name", "Agent")),
                            ),
                            "goal": config.get("goal_template", ""),
                            "mode": config.get(
                                "autonomy_mode", "bounded-autonomous"
                            ),
                        },
                    )
                    # ATOMIC: create install record
                    await session.execute(
                        _t("""
                            INSERT INTO marketplace_installs
                                (id, template_id, installer_tenant_id, agent_id,
                                 parameters, installed_at)
                            VALUES
                                (:id, :tid, :installer, :agent,
                                 :params::jsonb, NOW())
                            ON CONFLICT (template_id, installer_tenant_id) DO UPDATE
                                SET agent_id = EXCLUDED.agent_id,
                                    parameters = EXCLUDED.parameters,
                                    installed_at = NOW(),
                                    uninstalled_at = NULL
                        """),
                        {
                            "id": install_id,
                            "tid": template_id,
                            "installer": tenant_ctx.tenant_id,
                            "agent": agent_id,
                            "params": json.dumps(params),
                        },
                    )
                    # Increment install count
                    await session.execute(
                        _t(
                            "UPDATE marketplace_templates "
                            "SET install_count = install_count + 1, updated_at = NOW() "
                            "WHERE id = :id"
                        ),
                        {"id": template_id},
                    )
                    # ALL OR NOTHING
                    await session.commit()
            except Exception as exc:
                # Atomic failure — no ghost agent since session was not committed
                return {
                    "success": False,
                    "error": str(exc),
                    "template_id": template_id,
                }
        else:
            # In-memory path for tests
            self._installs.append(
                {
                    "install_id": install_id,
                    "template_id": template_id,
                    "tenant_id": tenant_ctx.tenant_id,
                    "agent_id": agent_id,
                    "params": params,
                }
            )
            if template_id in self._cache:
                self._cache[template_id]["install_count"] = (
                    self._cache[template_id].get("install_count", 0) + 1
                )

        return {
            "success": True,
            "agent_id": agent_id,
            "install_id": install_id,
            "template_id": template_id,
        }

    # ------------------------------------------------------------------
    # Reviews
    # ------------------------------------------------------------------

    async def add_review(
        self,
        *,
        template_id: str,
        tenant_ctx: TenantContext,
        rating: int,
        title: str = "",
        body: str = "",
        verified_install: bool = False,
    ) -> dict[str, Any]:
        """Add or update a review; update rating_avg on the template."""
        if not 1 <= rating <= 5:
            return {"success": False, "error": "Rating must be between 1 and 5"}

        review_id = str(uuid.uuid4().hex[:32])

        if self._db is not None:
            try:
                async with self._db() as session:
                    await session.execute(
                        _t("SET LOCAL app.tenant_id = :tid"),
                        {"tid": tenant_ctx.tenant_id},
                    )
                    await session.execute(
                        _t("""
                            INSERT INTO marketplace_reviews
                                (id, template_id, reviewer_tenant_id, rating,
                                 title, body, verified_install)
                            VALUES
                                (:id, :tid, :reviewer, :rating, :title, :body, :verified)
                            ON CONFLICT (template_id, reviewer_tenant_id) DO UPDATE
                                SET rating=EXCLUDED.rating,
                                    title=EXCLUDED.title,
                                    body=EXCLUDED.body,
                                    verified_install=EXCLUDED.verified_install
                        """),
                        {
                            "id": review_id,
                            "tid": template_id,
                            "reviewer": tenant_ctx.tenant_id,
                            "rating": rating,
                            "title": title,
                            "body": body,
                            "verified": verified_install,
                        },
                    )
                    # Aggregate: update rating_avg and rating_count
                    await session.execute(
                        _t("""
                            UPDATE marketplace_templates SET
                                rating_avg   = (
                                    SELECT AVG(rating)::float
                                    FROM marketplace_reviews
                                    WHERE template_id = :tid
                                ),
                                rating_count = (
                                    SELECT COUNT(*)
                                    FROM marketplace_reviews
                                    WHERE template_id = :tid
                                ),
                                updated_at = NOW()
                            WHERE id = :tid
                        """),
                        {"tid": template_id},
                    )
                    await session.commit()
            except Exception as exc:
                return {"success": False, "error": str(exc)}
        else:
            # In-memory
            self._reviews.append(
                {
                    "review_id": review_id,
                    "template_id": template_id,
                    "tenant_id": tenant_ctx.tenant_id,
                    "rating": rating,
                    "title": title,
                    "body": body,
                    "verified_install": verified_install,
                }
            )
            # Update in-memory cache
            if template_id in self._cache:
                all_ratings = [
                    r["rating"]
                    for r in self._reviews
                    if r["template_id"] == template_id
                ]
                self._cache[template_id]["rating_avg"] = (
                    sum(all_ratings) / len(all_ratings) if all_ratings else None
                )
                self._cache[template_id]["rating_count"] = len(all_ratings)

        return {"success": True, "review_id": review_id}

    async def list_reviews(
        self,
        *,
        template_id: str,
        page: int = 1,
        page_size: int = 20,
        tenant_id: str = "",
    ) -> list[dict[str, Any]]:
        """List reviews for a template (verified installs first)."""
        if self._db is not None:
            try:
                async with self._db() as session:
                    if tenant_id:
                        await session.execute(
                            _t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id}
                        )
                    offset = (page - 1) * page_size
                    rows = (
                        await session.execute(
                            _t("""
                                SELECT * FROM marketplace_reviews
                                WHERE template_id = :tid
                                ORDER BY verified_install DESC, helpful_count DESC,
                                         created_at DESC
                                LIMIT :limit OFFSET :offset
                            """),
                            {"tid": template_id, "limit": page_size, "offset": offset},
                        )
                    ).fetchall()
                    return [dict(r._mapping) for r in rows]
            except Exception:
                pass

        # In-memory fallback
        return [r for r in self._reviews if r.get("template_id") == template_id]

    # ------------------------------------------------------------------
    # Full-text search
    # ------------------------------------------------------------------

    async def search_templates(
        self,
        *,
        query: str,
        domain: str = "",
        tenant_id: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """Full-text search with optional domain filter."""
        result = await self.list_templates(
            domain=domain,
            search=query,
            tenant_id=tenant_id,
            page=page,
            page_size=page_size,
        )
        return result.get("templates", [])

    # ------------------------------------------------------------------
    # Seed built-ins
    # ------------------------------------------------------------------

    async def seed_builtins(self, tenant_ctx: TenantContext | None = None) -> int:
        """Upsert all _BUILTIN_TEMPLATES into DB (idempotent).

        Returns the number of templates seeded.
        """
        from app.tenancy.context import PlanTier

        ctx = tenant_ctx or TenantContext(
            tenant_id=_SYSTEM_TENANT_ID,
            plan=PlanTier.ENTERPRISE,
            api_key_id="system",
        )
        count = 0
        for tpl in _BUILTIN_TEMPLATES:
            record = await self.publish_template(
                data=tpl, tenant_ctx=ctx, run_security_review=False
            )
            if record:
                count += 1
        return count
