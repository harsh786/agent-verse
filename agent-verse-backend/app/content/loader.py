"""
Content Loader — validates and seeds marketplace agents and goal templates from YAML.

Usage:
  from app.content.loader import ContentLoader
  loader = ContentLoader()
  loader.load_all()  # loads + validates all YAML content
  await loader.seed(marketplace_v2_instance, template_store)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONTENT_ROOT = Path(__file__).parent


def _load_yaml(path: Path) -> list[dict[str, Any]]:
    """Load a YAML file and return a list of records."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("PyYAML not installed; skipping %s", path)
        return []

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        return [data]
    return []


class ContentLoader:
    """Loads, validates, and seeds marketplace + template content from YAML files."""

    def __init__(self) -> None:
        self._agents: list[dict[str, Any]] = []
        self._templates: list[dict[str, Any]] = []
        self._errors: list[str] = []
        self._loaded = False

    def load_all(self) -> ContentLoader:
        """Load and validate all YAML content. Returns self for chaining."""
        from app.content.schema import GoalTemplateContent, MarketplaceAgentContent

        marketplace_dir = _CONTENT_ROOT / "marketplace"
        templates_dir = _CONTENT_ROOT / "goal_templates"

        # Load marketplace agents
        all_slugs: set[str] = set()
        all_template_ids: set[str] = set()

        if marketplace_dir.exists():
            for yaml_file in sorted(marketplace_dir.glob("*.yaml")):
                records = _load_yaml(yaml_file)
                for raw in records:
                    try:
                        agent = MarketplaceAgentContent(**raw)
                        # Check uniqueness
                        if agent.slug in all_slugs:
                            self._errors.append(f"Duplicate slug '{agent.slug}' in {yaml_file}")
                            continue
                        if agent.template_id in all_template_ids:
                            self._errors.append(
                                f"Duplicate template_id '{agent.template_id}' in {yaml_file}"
                            )
                            continue
                        all_slugs.add(agent.slug)
                        all_template_ids.add(agent.template_id)
                        self._agents.append(agent.model_dump())
                    except Exception as e:
                        self._errors.append(f"Validation error in {yaml_file}: {e}")

        # Load goal templates
        all_template_names: set[str] = set()
        if templates_dir.exists():
            for yaml_file in sorted(templates_dir.glob("*.yaml")):
                records = _load_yaml(yaml_file)
                for raw in records:
                    try:
                        tmpl = GoalTemplateContent(**raw)
                        if tmpl.name in all_template_names:
                            self._errors.append(
                                f"Duplicate template name '{tmpl.name}' in {yaml_file}"
                            )
                            continue
                        all_template_names.add(tmpl.name)
                        self._templates.append(
                            {
                                **tmpl.model_dump(),
                                "parameters": [
                                    {"name": p, "description": p, "required": True}
                                    for p in tmpl.extract_parameters()
                                ],
                            }
                        )
                    except Exception as e:
                        self._errors.append(f"Validation error in {yaml_file}: {e}")

        self._loaded = True

        if self._errors:
            is_prod = os.getenv("ENVIRONMENT", "development") == "production"
            if is_prod:
                raise RuntimeError(
                    f"Content validation failed with {len(self._errors)} errors:\n"
                    + "\n".join(self._errors[:10])
                )
            else:
                for err in self._errors:
                    logger.warning("content_validation_error: %s", err)

        logger.info(
            "content_loaded: agents=%d templates=%d errors=%d",
            len(self._agents),
            len(self._templates),
            len(self._errors),
        )
        return self

    @property
    def agents(self) -> list[dict[str, Any]]:
        return self._agents

    @property
    def goal_templates(self) -> list[dict[str, Any]]:
        return self._templates

    @property
    def errors(self) -> list[str]:
        return self._errors

    def check_mode(self) -> int:
        """CLI check mode: print errors and return exit code."""
        self.load_all()
        if self._errors:
            for err in self._errors:
                print(f"ERROR: {err}")
            print(f"\n{len(self._errors)} validation error(s) found.")
            return 1
        print(f"OK: {len(self._agents)} agents, {len(self._templates)} templates — all valid.")
        return 0


# Module-level default loader (lazy: not loaded until .load_all() is called)
_loader = ContentLoader()
