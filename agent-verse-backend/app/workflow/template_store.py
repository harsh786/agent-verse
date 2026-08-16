"""SystemTemplateStore — reads, validates and serves workflow template YAMLs.

Templates are static YAML files in app/workflow/templates/. They are:
  - Loaded lazily on first access
  - Cached in memory (no DB round-trip for reads)
  - Written to system_workflow_templates table by seed_workflow_templates.py

Each template YAML must have:
  name, slug, description, category, tags[], author, version, definition (WorkflowDefinition)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.observability.logging import get_logger
from app.workflow.dsl import WorkflowDefinition

_log = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class TemplateNotFoundError(KeyError):
    pass


class SystemTemplate:
    """Parsed workflow template with metadata."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.slug: str = raw["slug"]
        self.name: str = raw["name"]
        self.description: str = raw.get("description", "")
        self.category: str = raw.get("category", "General")
        self.tags: list[str] = raw.get("tags", [])
        self.author: str = raw.get("author", "AgentVerse Team")
        self.version: str = raw.get("version", "1.0.0")
        self.complexity: str = raw.get("complexity", "medium")
        self.sample_input: dict[str, Any] = raw.get("sample_input", {})
        self.popularity_score: float = float(raw.get("popularity_score", 0.0))
        self._definition_raw: dict[str, Any] = raw.get("definition", {})
        self._definition: WorkflowDefinition | None = None

    @property
    def definition(self) -> WorkflowDefinition:
        if self._definition is None:
            self._definition = WorkflowDefinition(**self._definition_raw)
        return self._definition

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "tags": self.tags,
            "author": self.author,
            "version": self.version,
            "complexity": self.complexity,
            "sample_input": self.sample_input,
            "popularity_score": self.popularity_score,
            "definition": self._definition_raw,
        }


class SystemTemplateStore:
    """In-memory template catalog loaded from YAML files."""

    def __init__(self, templates_dir: Path | None = None) -> None:
        self._dir = templates_dir or _TEMPLATES_DIR
        self._cache: dict[str, SystemTemplate] | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, slug: str) -> SystemTemplate:
        """Get a template by slug. Raises TemplateNotFoundError if missing."""
        catalog = self._load()
        if slug not in catalog:
            raise TemplateNotFoundError(f"Template not found: {slug!r}")
        return catalog[slug]

    def list(
        self,
        category: str | None = None,
        q: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[SystemTemplate], int]:
        """List templates with optional category / full-text filter."""
        catalog = self._load()
        items = list(catalog.values())

        if category:
            items = [t for t in items if t.category.lower() == category.lower()]
        if q:
            q_lower = q.lower()
            items = [
                t for t in items
                if q_lower in t.name.lower()
                or q_lower in t.description.lower()
                or any(q_lower in tag.lower() for tag in t.tags)
            ]

        # Sort by popularity descending
        items.sort(key=lambda t: t.popularity_score, reverse=True)

        total = len(items)
        start = (page - 1) * per_page
        return items[start : start + per_page], total

    def categories(self) -> list[dict[str, Any]]:
        """Return distinct categories with item counts."""
        catalog = self._load()
        counts: dict[str, int] = {}
        for t in catalog.values():
            counts[t.category] = counts.get(t.category, 0) + 1
        return [
            {"category": cat, "count": count}
            for cat, count in sorted(counts.items())
        ]

    def fork(
        self, slug: str, tenant_id: str, overrides: dict[str, Any] | None = None
    ) -> WorkflowDefinition:
        """Create a tenant copy of a template definition."""
        template = self.get(slug)
        definition_raw = dict(template._definition_raw)
        definition_raw["forked_from"] = slug
        if overrides:
            definition_raw.update(overrides)
        wf = WorkflowDefinition(**definition_raw)
        # Apply tenant context
        wf_dict = wf.model_dump()
        wf_dict["id"] = str(__import__("uuid").uuid4())
        return WorkflowDefinition(**wf_dict)

    def all_slugs(self) -> list[str]:
        """Return sorted list of all template slugs."""
        return sorted(self._load().keys())

    def reload(self) -> None:
        """Force reload from disk (used in tests / hot reload)."""
        self._cache = None

    # ── Private ───────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, SystemTemplate]:
        if self._cache is not None:
            return self._cache

        catalog: dict[str, SystemTemplate] = {}
        if not self._dir.exists():
            _log.warning("template_dir_not_found", path=str(self._dir))
            self._cache = catalog
            return catalog

        for yaml_path in sorted(self._dir.glob("*.yaml")):
            try:
                template = self._load_file(yaml_path)
                catalog[template.slug] = template
            except Exception as exc:
                _log.warning("template_load_failed", path=str(yaml_path), error=str(exc))

        _log.info("templates_loaded", count=len(catalog))
        self._cache = catalog
        return catalog

    @staticmethod
    def _load_file(path: Path) -> SystemTemplate:
        import yaml  # type: ignore[import]
        with open(path) as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"Template file must be a YAML dict: {path}")
        required = {"slug", "name", "category"}
        missing = required - set(raw.keys())
        if missing:
            raise ValueError(f"Template {path.name} missing required keys: {missing}")
        return SystemTemplate(raw)
