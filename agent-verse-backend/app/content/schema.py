"""Content-as-data schema for marketplace agents and goal templates.

Every marketplace agent and goal template is a YAML record validated by
these pydantic models. This replaces giant Python literal lists.

At load time:
  - All records are validated against these models
  - slug/template_id/name global uniqueness is enforced
  - required_connectors are checked against the catalog
  - In production: any validation failure aborts startup (fail-closed)
  - In dev: bad records are logged and skipped
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

VALID_AUTONOMY_MODES: frozenset[str] = frozenset(
    {"supervised", "bounded-autonomous", "fully-autonomous"}
)
VALID_DOMAINS: frozenset[str] = frozenset(
    {
        "legal",
        "gst-tax",
        "banking-fintech",
        "healthcare",
        "software",
        "e-commerce",
        "education",
        "hr-talent",
        "devops",
        "sales-crm",
        "operations",
        "invoicing-finance",
        "marketing",
        "cybersecurity",
        "logistics",
        "insurance",
        "manufacturing",
        "real-estate",
        "pharmaceutical",
        "telecom",
        "construction",
        "energy-utilities",
        "customer-support",
        "government-portal",
        "agriculture",
        "hospitality-travel",
        "media-publishing",
        "food-restaurant",
        "recruitment",
        "automobile",
        "nonprofit-ngo",
        "events-mice",
        "wealth-management",
        "fashion-apparel",
        "architecture-interior",
        "public-health",
        "accounting-ca",
        # Also accept the existing V2 domains
        "engineering",
        "testing",
        "support",
        "finance",
        "research",
    }
)


class EvalFixture(BaseModel):
    goal: str
    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    expected_output_contains: list[str] = Field(default_factory=list)


class MarketplaceAgentContent(BaseModel):
    template_id: str
    slug: str
    name: str
    domain: str
    subdomain: str | None = None
    description: str
    long_description: str = ""
    source_use_cases: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    goal_template: str
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    required_connectors: list[str] = Field(default_factory=list)
    optional_connectors: list[str] = Field(default_factory=list)
    autonomy_mode: str = "bounded-autonomous"
    default_trigger: dict[str, Any] = Field(default_factory=dict)
    knowledge_collections: list[str] = Field(default_factory=list)
    eval_fixtures: list[EvalFixture] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    revenue_model: str = ""
    roi_note: str = ""
    version: str = "1.0.0"
    is_verified: bool = False
    author_name: str = "AgentVerse"
    icon_url: str | None = None

    @field_validator("autonomy_mode")
    @classmethod
    def _validate_autonomy_mode(cls, v: str) -> str:
        if v not in VALID_AUTONOMY_MODES:
            raise ValueError(f"autonomy_mode must be one of {set(VALID_AUTONOMY_MODES)}, got '{v}'")
        return v

    @field_validator("template_id")
    @classmethod
    def _validate_template_id(cls, v: str) -> str:
        if not v.startswith("tpl-"):
            raise ValueError(f"template_id must start with 'tpl-', got '{v}'")
        return v

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$", v):
            raise ValueError(f"slug must be lowercase kebab-case, got '{v}'")
        return v


class GoalTemplateContent(BaseModel):
    name: str
    domain: str
    description: str
    goal_text: str  # {{double_brace}} placeholders
    source_use_cases: list[str] = Field(default_factory=list)
    # parameters auto-extracted from goal_text at load

    def extract_parameters(self) -> list[str]:
        """Extract {{param}} names from goal_text."""
        return re.findall(r"\{\{(\w+)\}\}", self.goal_text)
