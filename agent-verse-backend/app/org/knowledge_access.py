"""PART 15 — Knowledge Access Control (KnowledgeAccessPolicy).

Controls which departments and roles can access which knowledge collections.
Enforces confidentiality tiers: public | internal | confidential | restricted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

_log = structlog.get_logger(__name__)

SensitivityLevel = str  # "public" | "internal" | "confidential" | "restricted"


@dataclass
class CollectionPolicy:
    """Access policy for a single knowledge collection."""

    collection_id: str
    sensitivity: SensitivityLevel = "internal"
    allowed_dept_ids: list[str] = field(default_factory=list)  # empty = all depts
    allowed_role_ids: list[str] = field(default_factory=list)  # empty = all roles in allowed depts
    denied_dept_ids: list[str] = field(default_factory=list)  # explicit deny list


class KnowledgeAccessPolicy:
    """
    PART 15 — Knowledge Fabric access control.

    Examples:
      Engineering agents cannot access Finance's restricted models.
      Junior Engineer cannot access CEO strategy documents.
      Customer Support can access product docs, not engineering architecture.
    """

    # Default per-department collection restrictions
    DEPT_COLLECTIONS: dict[str, list[str]] = {
        "executive": ["executive_strategy", "all_internal", "all_confidential"],
        "engineering": ["engineering_docs", "product_specs", "architecture", "all_internal"],
        "marketing": ["marketing_assets", "product_docs", "customer_insights", "all_internal"],
        "finance": ["financial_models", "budget_data", "all_internal", "finance_restricted"],
        "legal": ["legal_contracts", "compliance_docs", "all_internal"],
        "security": ["security_policies", "threat_intel", "all_internal", "security_restricted"],
        "hr": ["hr_policies", "people_data", "all_internal"],
        "sales": ["sales_playbooks", "customer_data", "product_docs", "all_internal"],
        "data": ["data_assets", "analytics_reports", "all_internal"],
        "research": ["research_papers", "web_search_results", "all_internal"],
        "support": ["product_docs", "knowledge_base", "all_internal"],
        "operations": ["ops_runbooks", "process_docs", "all_internal"],
        "devops": ["infrastructure_docs", "runbooks", "engineering_docs", "all_internal"],
        "product": ["product_specs", "customer_insights", "roadmaps", "all_internal"],
        "ai_ml": ["ai_models", "training_data", "engineering_docs", "all_internal"],
        "design": ["design_assets", "brand_guidelines", "all_internal"],
        "writing": ["content_library", "editorial_guidelines", "all_internal"],
        "qa": ["test_suites", "quality_reports", "engineering_docs", "all_internal"],
        "procurement": ["vendor_data", "contracts", "all_internal"],
        "pmo": ["project_data", "resource_plans", "all_internal"],
        "knowledge_mgmt": ["all_internal", "taxonomy", "metadata"],
    }

    # Sensitivity mappings for special collections
    SENSITIVITY_MAP: dict[str, SensitivityLevel] = {
        "finance_restricted": "restricted",
        "security_restricted": "restricted",
        "executive_strategy": "confidential",
        "legal_contracts": "confidential",
        "hr_policies": "confidential",
        "people_data": "confidential",
        "all_confidential": "confidential",
        "all_internal": "internal",
        "all_public": "public",
    }

    def can_access(
        self,
        agent_dept_id: str,
        agent_role_id: str,
        collection_id: str,
        org_id: str | None = None,
    ) -> bool:
        """
        Check if an agent in a given department/role can access a collection.
        Returns True if access is permitted.
        """
        # Executive always has access
        if agent_dept_id == "executive":
            return True

        # Check sensitivity
        sensitivity = self.SENSITIVITY_MAP.get(collection_id, "internal")
        if sensitivity == "restricted":
            # Only executive + owning department
            collection_owner = self._get_collection_owner(collection_id)
            if collection_owner and agent_dept_id not in ("executive", collection_owner):
                _log.info(
                    "knowledge_access.denied",
                    reason="restricted_collection",
                    agent_dept=agent_dept_id,
                    collection=collection_id,
                )
                return False

        # Check department allowlist
        allowed_collections = self.DEPT_COLLECTIONS.get(agent_dept_id, ["all_internal"])
        if collection_id in allowed_collections:
            return True

        # Public collections accessible by all
        if sensitivity == "public":
            return True

        # Default: internal accessible to all departments
        return bool(collection_id == "all_internal" or sensitivity == "internal")

    def get_allowed_collections(self, agent_dept_id: str) -> list[str]:
        """Return all collection IDs accessible to a department."""
        base = self.DEPT_COLLECTIONS.get(agent_dept_id, ["all_internal"])
        # Always include public
        return list({*base, "all_public"})

    def _get_collection_owner(self, collection_id: str) -> str | None:
        """Map a collection to its owning department."""
        _owner_map = {
            "finance_restricted": "finance",
            "security_restricted": "security",
            "hr_policies": "hr",
            "people_data": "hr",
            "legal_contracts": "legal",
            "executive_strategy": "executive",
        }
        return _owner_map.get(collection_id)

    def filter_search_results(
        self,
        results: list[dict[str, Any]],
        agent_dept_id: str,
        agent_role_id: str,
    ) -> list[dict[str, Any]]:
        """Filter RAG search results to only permitted collections."""
        return [
            r
            for r in results
            if self.can_access(
                agent_dept_id,
                agent_role_id,
                r.get("collection_id", "all_internal"),
            )
        ]


# Global singleton
knowledge_access_policy = KnowledgeAccessPolicy()
