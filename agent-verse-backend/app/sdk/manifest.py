"""Versioned agent manifest — commit-able agent configuration format.

Open source YAML parsing only (pyyaml).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConnectorRequirement:
    type: str
    optional: bool = False
    description: str = ""


@dataclass
class PolicySpec:
    name: str
    tools_pattern: str
    action: str = "deny"  # deny | require_approval


@dataclass
class AgentManifest:
    name: str
    version: str
    description: str
    autonomy_mode: str = "bounded-autonomous"
    goal_template: str = ""
    default_model: str = ""
    connector_requirements: list[ConnectorRequirement] = field(default_factory=list)
    knowledge_collections: list[str] = field(default_factory=list)
    policies: list[PolicySpec] = field(default_factory=list)
    eval_suite_id: str | None = None
    tags: list[str] = field(default_factory=list)

    VALID_AUTONOMY_MODES = frozenset(["supervised", "bounded-autonomous", "fully-autonomous"])

    @classmethod
    def from_yaml(cls, path: str) -> AgentManifest:
        try:
            import yaml
        except ImportError:
            raise ImportError("pyyaml is required: pip install pyyaml")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls._from_dict(data)

    @classmethod
    def from_json(cls, path: str) -> AgentManifest:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> AgentManifest:
        return cls(
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            autonomy_mode=data.get("autonomy_mode", "bounded-autonomous"),
            goal_template=data.get("goal_template", ""),
            default_model=data.get("default_model", ""),
            connector_requirements=[
                ConnectorRequirement(**c) if isinstance(c, dict)
                else ConnectorRequirement(type=str(c))
                for c in data.get("connector_requirements", [])
            ],
            knowledge_collections=data.get("knowledge_collections", []),
            policies=[
                PolicySpec(**p) if isinstance(p, dict) else PolicySpec(name=str(p), tools_pattern="*")
                for p in data.get("policies", [])
            ],
            eval_suite_id=data.get("eval_suite_id"),
            tags=data.get("tags", []),
        )

    def to_yaml(self) -> str:
        try:
            import yaml
        except ImportError:
            return self._to_yaml_manual()
        return yaml.dump(self._to_dict(), default_flow_style=False, allow_unicode=True)

    def _to_yaml_manual(self) -> str:
        """Minimal YAML output without pyyaml dependency."""
        lines = [
            f"name: {self.name}",
            f"version: {self.version}",
            f"description: {self.description}",
            f"autonomy_mode: {self.autonomy_mode}",
        ]
        if self.goal_template:
            lines.append("goal_template: |")
            for line in self.goal_template.split("\n"):
                lines.append(f"  {line}")
        return "\n".join(lines)

    def _to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "autonomy_mode": self.autonomy_mode,
            "goal_template": self.goal_template,
            "default_model": self.default_model,
            "connector_requirements": [
                {"type": c.type, "optional": c.optional} for c in self.connector_requirements
            ],
            "knowledge_collections": self.knowledge_collections,
            "policies": [
                {"name": p.name, "tools_pattern": p.tools_pattern, "action": p.action}
                for p in self.policies
            ],
            "eval_suite_id": self.eval_suite_id,
            "tags": self.tags,
        }

    def validate(self) -> list[str]:
        """Return list of validation errors (empty = valid)."""
        errors = []
        if not self.name.strip():
            errors.append("name is required")
        if not self.version.strip():
            errors.append("version is required")
        if self.autonomy_mode not in self.VALID_AUTONOMY_MODES:
            errors.append(
                f"invalid autonomy_mode '{self.autonomy_mode}'. "
                f"Must be one of: {', '.join(sorted(self.VALID_AUTONOMY_MODES))}"
            )
        # Validate semantic version (loosely)
        parts = self.version.split(".")
        if len(parts) < 2 or not all(p.isdigit() for p in parts if p.isdigit()):
            errors.append("version should be semver format (e.g. '1.0.0')")
        for p in self.policies:
            if p.action not in {"deny", "require_approval"}:
                errors.append(f"policy '{p.name}': invalid action '{p.action}'")
        return errors
