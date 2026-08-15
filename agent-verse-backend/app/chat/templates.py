"""Prompt templates (personas) for the chat system.

Built-in personas + user-saved templates stored per tenant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

BUILT_IN_TEMPLATES: list[dict] = [
    {
        "id": "builtin_security_auditor",
        "name": "Security Auditor",
        "description": "Performs security code review and threat modeling.",
        "system_prompt": (
            "You are a senior security engineer specialised in OWASP Top 10, "
            "threat modeling, and secure code review. Analyse every piece of code "
            "for vulnerabilities and suggest mitigations."
        ),
        "builtin": True,
    },
    {
        "id": "builtin_data_analyst",
        "name": "Data Analyst",
        "description": "Helps with data analysis, SQL, and visualisations.",
        "system_prompt": (
            "You are an expert data analyst. Help write SQL queries, analyse "
            "datasets, suggest visualisations, and explain statistical findings clearly."
        ),
        "builtin": True,
    },
    {
        "id": "builtin_devops_engineer",
        "name": "DevOps Engineer",
        "description": "CI/CD, containers, Kubernetes, and infrastructure.",
        "system_prompt": (
            "You are a senior DevOps engineer. Help with CI/CD pipelines, Docker, "
            "Kubernetes, Terraform, and cloud infrastructure best practices."
        ),
        "builtin": True,
    },
    {
        "id": "builtin_qa_engineer",
        "name": "QA Engineer",
        "description": "Test strategy, test cases, and automation.",
        "system_prompt": (
            "You are a senior QA engineer. Help design test strategies, write "
            "test cases, suggest automation frameworks, and review code for testability."
        ),
        "builtin": True,
    },
    {
        "id": "builtin_architect",
        "name": "Solution Architect",
        "description": "System design, architecture decisions, and trade-offs.",
        "system_prompt": (
            "You are a senior solution architect. Help with system design, "
            "architecture diagrams, technology selection, and trade-off analysis."
        ),
        "builtin": True,
    },
]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Template:
    id: str
    tenant_id: str
    name: str
    description: str
    system_prompt: str
    builtin: bool = False
    created_at: datetime = field(default_factory=_now)


class TemplateStore:
    def __init__(self) -> None:
        self._user_templates: dict[str, Template] = {}

    def list_templates(self, tenant_id: str) -> list[dict]:
        user = [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "system_prompt": t.system_prompt,
                "builtin": False,
                "created_at": t.created_at.isoformat(),
            }
            for t in self._user_templates.values()
            if t.tenant_id == tenant_id
        ]
        return BUILT_IN_TEMPLATES + user

    def create_template(
        self,
        tenant_id: str,
        name: str,
        description: str,
        system_prompt: str,
    ) -> Template:
        t = Template(
            id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            name=name,
            description=description,
            system_prompt=system_prompt,
        )
        self._user_templates[t.id] = t
        return t

    def delete_template(self, template_id: str, tenant_id: str) -> bool:
        if template_id.startswith("builtin_"):
            return False
        t = self._user_templates.get(template_id)
        if not t or t.tenant_id != tenant_id:
            return False
        del self._user_templates[template_id]
        return True
