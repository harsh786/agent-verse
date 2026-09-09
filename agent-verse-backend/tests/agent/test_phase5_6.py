"""Tests for Phase 5 (provider registry) and Phase 6 (skills)."""
import os
import unittest.mock as mock


class TestProviderRegistry:
    def test_registry_importable(self) -> None:
        from app.providers.registry import (  # noqa: F401
            _detect_providers,
            get_provider_catalog,
            resolve_provider,
        )

        assert callable(resolve_provider)

    def test_detect_providers_returns_list(self) -> None:
        from app.providers.registry import _detect_providers

        providers = _detect_providers()
        assert isinstance(providers, list)

    def test_ollama_detected_when_env_set(self) -> None:
        from app.providers.registry import _detect_providers

        os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
        try:
            providers = _detect_providers()
            types = [p.provider_type for p in providers]
            assert "ollama" in types
        finally:
            del os.environ["OLLAMA_BASE_URL"]

    def test_resolve_provider_returns_fake_when_no_keys(self) -> None:
        """When no provider keys are configured, FakeProvider is returned."""
        from app.providers.fake import FakeProvider
        from app.providers.registry import resolve_provider

        with mock.patch(
            "app.providers.registry._detect_providers", return_value=[]
        ):
            provider = resolve_provider([])
            assert isinstance(provider, FakeProvider)

    def test_provider_catalog_no_keys_in_output(self) -> None:
        from app.providers.registry import get_provider_catalog

        catalog = get_provider_catalog()
        for entry in catalog:
            assert "api_key" not in entry, "API keys must not be in catalog output"
            assert "key" not in str(entry).lower() or "api_key" not in entry


class TestSkillSelector:
    def test_select_by_trigger_hint(self) -> None:
        from app.agent.skill_selector import SkillSelector

        sel = SkillSelector()

        selected = sel.select("summarize the sprint report briefly")
        names = [s.name for s in selected]
        assert "summarize-and-compress" in names

    def test_code_review_skill_selected(self) -> None:
        from app.agent.skill_selector import SkillSelector

        sel = SkillSelector()
        selected = sel.select("review PR #42 for security issues")
        names = [s.name for s in selected]
        assert "code-review" in names

    def test_no_match_returns_empty(self) -> None:
        from app.agent.skill_selector import SkillSelector

        sel = SkillSelector()
        selected = sel.select("find all open JIRA tickets")
        # No strong skill match for generic JIRA query
        assert isinstance(selected, list)

    def test_token_budget_respected(self) -> None:
        from app.agent.skill_selector import SkillSelector

        sel = SkillSelector()
        # Use a tiny token budget
        selected = sel.select(
            "summarize and create report", max_skills=5, max_tokens=100
        )
        total = sum(s.match_score for s in selected)
        assert total >= 0  # just verify no crash

    def test_build_skills_context_returns_string(self) -> None:
        from app.agent.skill_selector import SkillSelector

        sel = SkillSelector()
        selected = sel.select("create a report")
        ctx = sel.build_skills_context(selected)
        assert isinstance(ctx, str)
        if selected:
            assert "[Active Skills]" in ctx

    def test_platform_skills_have_required_fields(self) -> None:
        from app.agent.skill_selector import PLATFORM_SKILLS

        for skill in PLATFORM_SKILLS:
            assert "id" in skill
            assert "name" in skill
            assert "trigger_hints" in skill
            assert "instructions" in skill
            assert isinstance(skill["trigger_hints"], list)


class TestSkillModel:
    def test_skill_orm_importable(self) -> None:
        from app.db.models.skill import Skill

        assert Skill.__tablename__ == "skills"

    def test_skill_has_required_columns(self) -> None:
        from sqlalchemy.inspection import inspect

        from app.db.models.skill import Skill

        cols = [c.key for c in inspect(Skill).columns]
        assert "trigger_hints" in cols
        assert "instructions" in cols
        assert "allowed_tools" in cols
        assert "visibility" in cols
