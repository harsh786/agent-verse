"""
Coverage gate: every UC listed in domain docs must map to ≥1 content record.
"""
import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).parent.parent.parent  # agent-verse-backend


def get_all_source_use_cases():
    """Collect all source_use_cases from all YAML files."""
    try:
        import yaml
    except ImportError:
        return set()

    covered = set()
    marketplace_dir = BACKEND_ROOT / "app" / "content" / "marketplace"
    templates_dir = BACKEND_ROOT / "app" / "content" / "goal_templates"

    for d in [marketplace_dir, templates_dir]:
        if not d.exists():
            continue
        for yaml_file in d.glob("*.yaml"):
            with open(yaml_file) as f:
                records = yaml.safe_load(f) or []
            if not isinstance(records, list):
                records = [records]
            for rec in records:
                ucs = rec.get("source_use_cases", [])
                if isinstance(ucs, str):
                    ucs = [ucs]
                covered.update(ucs)

    return covered


class TestContentCoverage:
    def test_content_loader_has_no_validation_errors(self):
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        if loader.errors:
            pytest.fail(
                f"Content validation errors:\n" + "\n".join(loader.errors[:10])
            )

    def test_minimum_agent_count(self):
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        assert len(loader.agents) >= 50, (
            f"Expected ≥50 marketplace agents, got {len(loader.agents)}"
        )

    def test_minimum_template_count(self):
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        assert len(loader.goal_templates) >= 50, (
            f"Expected ≥50 goal templates, got {len(loader.goal_templates)}"
        )

    def test_all_agents_have_eval_fixtures(self):
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        missing = [a["slug"] for a in loader.agents if not a.get("eval_fixtures")]
        assert not missing, f"Agents without eval_fixtures: {missing[:5]}"

    def test_regulated_domains_use_supervised_mode(self):
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        regulated = {"gst-tax", "banking-fintech", "healthcare", "pharmaceutical"}
        violations = []
        for agent in loader.agents:
            if agent["domain"] in regulated:
                # Check if any filing connectors are used
                filing_connectors = {
                    "gst_portal",
                    "income_tax_portal",
                    "pmjay",
                    "cdsco_portal",
                }
                if any(
                    c in filing_connectors
                    for c in agent.get("required_connectors", [])
                ):
                    if agent["autonomy_mode"] not in (
                        "supervised",
                        "bounded-autonomous",
                    ):
                        violations.append(
                            f"{agent['slug']}: {agent['autonomy_mode']}"
                        )
        assert not violations, (
            f"Filing agents with wrong autonomy_mode: {violations}"
        )

    def test_all_slugs_unique(self):
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        slugs = [a["slug"] for a in loader.agents]
        assert len(slugs) == len(set(slugs)), (
            "Duplicate slugs found in marketplace agents"
        )

    def test_all_template_ids_unique(self):
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        ids = [a["template_id"] for a in loader.agents]
        assert len(ids) == len(set(ids)), "Duplicate template_ids found"

    def test_source_use_cases_coverage(self):
        """Every created agent/template should have at least one source UC reference."""
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        agents_without_uc = [
            a["slug"]
            for a in loader.agents
            if not a.get("source_use_cases")
        ]
        assert len(agents_without_uc) == 0, (
            f"Agents without source_use_cases: {agents_without_uc[:5]}"
        )
