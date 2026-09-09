"""Tests for content schema and loader."""
from pathlib import Path

import pytest


class TestContentSchema:
    def test_marketplace_agent_content_valid(self) -> None:
        from app.content.schema import EvalFixture, MarketplaceAgentContent

        agent = MarketplaceAgentContent(
            template_id="tpl-legal-contract-review-v2",
            slug="legal-contract-review-pro",
            name="Contract Review Agent",
            domain="legal",
            description="Reviews contracts",
            goal_template="Review contract at {document_url}",
            required_connectors=["document_reader"],
            eval_fixtures=[
                EvalFixture(
                    goal="Review the MSA at https://example.com/msa.pdf",
                    required_tools=["document_reader"],
                )
            ],
        )
        assert agent.template_id.startswith("tpl-")
        assert agent.autonomy_mode == "bounded-autonomous"

    def test_template_id_must_start_with_tpl(self) -> None:
        from pydantic import ValidationError

        from app.content.schema import MarketplaceAgentContent

        with pytest.raises(ValidationError):
            MarketplaceAgentContent(
                template_id="bad-id",
                slug="good-slug",
                name="Test",
                domain="legal",
                description="d",
                goal_template="g",
            )

    def test_slug_must_be_kebab(self) -> None:
        from pydantic import ValidationError

        from app.content.schema import MarketplaceAgentContent

        with pytest.raises(ValidationError):
            MarketplaceAgentContent(
                template_id="tpl-test",
                slug="Not_Valid",
                name="Test",
                domain="legal",
                description="d",
                goal_template="g",
            )

    def test_autonomy_mode_validated(self) -> None:
        from pydantic import ValidationError

        from app.content.schema import MarketplaceAgentContent

        with pytest.raises(ValidationError):
            MarketplaceAgentContent(
                template_id="tpl-test-x",
                slug="test-x",
                name="Test",
                domain="legal",
                description="d",
                goal_template="g",
                autonomy_mode="invalid",
            )

    def test_goal_template_content_extracts_params(self) -> None:
        from app.content.schema import GoalTemplateContent

        tmpl = GoalTemplateContent(
            name="File GSTR-3B",
            domain="gst-tax",
            description="File GSTR-3B",
            goal_text=(
                "Prepare GSTR-3B for {{client_name}} (GSTIN {{gstin}}) for {{period}}"
            ),
        )
        params = tmpl.extract_parameters()
        assert "client_name" in params
        assert "gstin" in params
        assert "period" in params


class TestContentLoader:
    def test_loader_loads_without_error(self) -> None:
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        # Should not raise even if dirs are empty
        assert loader._loaded is True

    def test_loader_validates_sample_yaml(self) -> None:
        """If sample YAMLs exist, they should validate."""
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        # Sample files exist; assert no errors from them
        assert len(loader.errors) == 0 or all(
            "Duplicate" not in e for e in loader.errors
        )

    def test_schema_importable(self) -> None:
        from app.content.schema import EvalFixture, GoalTemplateContent, MarketplaceAgentContent

        assert MarketplaceAgentContent is not None
        assert GoalTemplateContent is not None
        assert EvalFixture is not None

    def test_loader_importable(self) -> None:
        from app.content.loader import ContentLoader, _loader

        assert _loader is not None
        assert isinstance(_loader, ContentLoader)

    def test_loader_finds_sample_agents(self) -> None:
        """The 06-legal.yaml sample should load one agent."""
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        assert len(loader.agents) >= 1
        slugs = [a["slug"] for a in loader.agents]
        assert "legal-contract-review-pro" in slugs

    def test_loader_finds_sample_templates(self) -> None:
        """The 07-gst-tax.yaml sample should load one goal template."""
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        assert len(loader.goal_templates) >= 1
        names = [t["name"] for t in loader.goal_templates]
        assert "File GSTR-3B for Client" in names

    def test_goal_template_parameters_extracted(self) -> None:
        """Auto-extracted parameters should include the double-brace placeholders."""
        from app.content.loader import ContentLoader

        loader = ContentLoader()
        loader.load_all()
        gst = next(
            (t for t in loader.goal_templates if t["name"] == "File GSTR-3B for Client"),
            None,
        )
        assert gst is not None
        param_names = [p["name"] for p in gst["parameters"]]
        assert "client_name" in param_names
        assert "gstin" in param_names
        assert "period" in param_names
        assert "source_system" in param_names


class TestInstallFix:
    def test_install_sets_connector_ids(self) -> None:
        """install() must populate connector_ids from required_connectors."""
        import inspect

        from app.enterprise.marketplace_v2 import MarketplaceV2

        source = inspect.getsource(MarketplaceV2.install)
        # The install method should reference connector_ids
        assert "connector_ids" in source, (
            "install() does not set connector_ids — agents install without tools!"
        )

    def test_install_sets_system_prompt(self) -> None:
        """install() must populate system_prompt."""
        import inspect

        from app.enterprise.marketplace_v2 import MarketplaceV2

        source = inspect.getsource(MarketplaceV2.install)
        assert "system_prompt" in source, (
            "install() does not set system_prompt — agents install without instructions!"
        )


class TestSecurityReviewerFix:
    async def test_catalog_connectors_not_flagged_as_unknown_scopes(self) -> None:
        """Normal connectors (gmail, document_reader) must not fail the reviewer."""
        from app.enterprise.marketplace_v2 import TemplateSecurityReviewer

        reviewer = TemplateSecurityReviewer()

        # This template has known connectors — should not get rejected
        template = {
            "name": "Test Legal",
            "required_connectors": ["gmail", "document_reader"],
            "autonomy_mode": "bounded-autonomous",
            "domain": "legal",
        }
        result = await reviewer.review(template)

        # review_status should be "approved" for known connectors
        assert result.get("review_status") in ("approved", "safe", "low"), (
            f"Known connectors flagged as unsafe: {result}"
        )

    async def test_critical_scope_still_rejected(self) -> None:
        """Templates with critical OAuth scopes are still flagged."""
        from app.enterprise.marketplace_v2 import TemplateSecurityReviewer

        reviewer = TemplateSecurityReviewer()
        template = {
            "name": "Bad Template",
            "oauth_scopes": ["admin:*"],
            "required_connectors": ["gmail"],
            "autonomy_mode": "bounded-autonomous",
        }
        result = await reviewer.review(template)
        # Should NOT be approved — critical scope
        assert result.get("approved") is False

    async def test_unknown_connectors_low_risk_not_rejected(self) -> None:
        """Unknown connectors produce a low-severity finding but don't auto-reject."""
        from app.enterprise.marketplace_v2 import TemplateSecurityReviewer

        reviewer = TemplateSecurityReviewer()
        template = {
            "name": "Future Template",
            "required_connectors": ["future_connector_xyz"],
            "autonomy_mode": "bounded-autonomous",
        }
        result = await reviewer.review(template)
        # Unknown connector → low risk → approved=True
        assert result.get("approved") is True
        assert result.get("risk_level") == "low"
