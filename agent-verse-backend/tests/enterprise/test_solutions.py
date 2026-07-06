"""Tests for Phase 7 — domain solutions catalog and Phase 8 A2A directory."""
import pytest


class TestSolutionsCatalog:
    def test_solutions_catalog_importable(self) -> None:
        from app.enterprise.solutions_catalog import DOMAIN_SOLUTIONS, get_solution, list_solutions

        assert len(DOMAIN_SOLUTIONS) >= 6
        assert callable(get_solution)
        assert callable(list_solutions)

    def test_all_solutions_have_required_fields(self) -> None:
        from app.enterprise.solutions_catalog import DOMAIN_SOLUTIONS

        for sol in DOMAIN_SOLUTIONS:
            assert "id" in sol, f"Missing id in {sol.get('name', 'unknown')}"
            assert "slug" in sol, f"Missing slug in {sol.get('name', 'unknown')}"
            assert "name" in sol, f"Missing name in {sol.get('id', 'unknown')}"
            assert "domain" in sol, f"Missing domain in {sol.get('name', 'unknown')}"
            assert "agents_config" in sol, f"Missing agents_config in {sol.get('name', 'unknown')}"
            assert "onboarding_steps" in sol, (
                f"Missing onboarding_steps in {sol.get('name', 'unknown')}"
            )

    def test_get_solution_by_slug(self) -> None:
        from app.enterprise.solutions_catalog import get_solution

        sol = get_solution("law-firm")
        assert sol is not None
        assert sol["domain"] == "legal"
        assert sol["id"] == "sol-law-firm"

    def test_get_solution_missing_slug_returns_none(self) -> None:
        from app.enterprise.solutions_catalog import get_solution

        assert get_solution("does-not-exist") is None

    def test_list_solutions_by_domain(self) -> None:
        from app.enterprise.solutions_catalog import list_solutions

        legal = list_solutions(domain="legal")
        assert len(legal) >= 1
        assert all(s["domain"] == "legal" for s in legal)

    def test_list_solutions_no_filter_returns_all(self) -> None:
        from app.enterprise.solutions_catalog import DOMAIN_SOLUTIONS, list_solutions

        assert list_solutions() == DOMAIN_SOLUTIONS

    def test_law_firm_solution_has_agents(self) -> None:
        from app.enterprise.solutions_catalog import get_solution

        sol = get_solution("law-firm")
        assert sol is not None
        assert len(sol["agents_config"]) >= 2

    def test_6_flagship_domains_covered(self) -> None:
        from app.enterprise.solutions_catalog import DOMAIN_SOLUTIONS

        domains = {s["domain"] for s in DOMAIN_SOLUTIONS}
        required = {"legal", "e_commerce", "software", "education", "finance", "operations"}
        # The catalog may have MORE domains than the original 6; just check the minimum set
        assert required.issubset(domains), f"Missing domains: {required - domains}"

    def test_solutions_router_importable(self) -> None:
        from app.api.solutions import router

        assert router is not None

    def test_solutions_router_has_endpoints(self) -> None:
        from app.api.solutions import router

        paths = [r.path for r in router.routes]
        assert any("/solutions" in p for p in paths)

    def test_every_solution_has_knowledge_recipes(self) -> None:
        from app.enterprise.solutions_catalog import DOMAIN_SOLUTIONS

        for sol in DOMAIN_SOLUTIONS:
            assert isinstance(sol.get("knowledge_recipes"), list), (
                f"{sol['name']} missing knowledge_recipes list"
            )

    def test_every_solution_has_onboarding_steps(self) -> None:
        from app.enterprise.solutions_catalog import DOMAIN_SOLUTIONS

        for sol in DOMAIN_SOLUTIONS:
            assert len(sol.get("onboarding_steps", [])) >= 2, (
                f"{sol['name']} has fewer than 2 onboarding steps"
            )

    def test_slugs_are_unique(self) -> None:
        from app.enterprise.solutions_catalog import DOMAIN_SOLUTIONS

        slugs = [s["slug"] for s in DOMAIN_SOLUTIONS]
        assert len(slugs) == len(set(slugs)), "Duplicate slugs found"

    def test_ids_are_unique(self) -> None:
        from app.enterprise.solutions_catalog import DOMAIN_SOLUTIONS

        ids = [s["id"] for s in DOMAIN_SOLUTIONS]
        assert len(ids) == len(set(ids)), "Duplicate IDs found"


class TestA2ADirectory:
    def test_a2a_directory_importable(self) -> None:
        from app.api.agent_directory import router

        assert router is not None

    def test_a2a_directory_router_prefix(self) -> None:
        from app.api.agent_directory import router

        assert router.prefix == "/.well-known/agents"

    def test_a2a_outbound_tool_importable(self) -> None:
        from app.agent.tools.a2a_call import call_external_a2a_agent

        assert callable(call_external_a2a_agent)

    @pytest.mark.asyncio
    async def test_a2a_outbound_blocks_private_ipv4(self) -> None:
        """A2A outbound calls to RFC-1918 private IPs must be blocked."""
        from app.agent.tools.a2a_call import call_external_a2a_agent

        result = await call_external_a2a_agent(
            agent_endpoint="http://10.0.0.1/a2a",
            task_description="test task",
        )
        assert result["status"] == "error"
        assert "blocked" in result.get("error", "").lower() or "security" in result.get(
            "error", ""
        ).lower()

    @pytest.mark.asyncio
    async def test_a2a_outbound_blocks_loopback(self) -> None:
        """A2A outbound calls to loopback must be blocked."""
        from app.agent.tools.a2a_call import call_external_a2a_agent

        result = await call_external_a2a_agent(
            agent_endpoint="http://127.0.0.1/a2a",
            task_description="test task",
        )
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_a2a_outbound_blocks_192_168(self) -> None:
        """A2A outbound calls to 192.168.x.x must be blocked."""
        from app.agent.tools.a2a_call import call_external_a2a_agent

        result = await call_external_a2a_agent(
            agent_endpoint="http://192.168.1.100/a2a",
            task_description="test task",
        )
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_a2a_outbound_blocks_172_16(self) -> None:
        """A2A outbound calls to 172.16.x.x must be blocked."""
        from app.agent.tools.a2a_call import call_external_a2a_agent

        result = await call_external_a2a_agent(
            agent_endpoint="http://172.16.0.1/a2a",
            task_description="test task",
        )
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_a2a_outbound_blocks_metadata_endpoint(self) -> None:
        """A2A outbound calls to AWS/GCP metadata endpoint must be blocked."""
        from app.agent.tools.a2a_call import call_external_a2a_agent

        result = await call_external_a2a_agent(
            agent_endpoint="http://169.254.169.254/latest/meta-data",
            task_description="get metadata",
        )
        assert result["status"] == "error"

    def test_a2a_tools_package_init(self) -> None:
        """The tools package __init__ is importable."""
        import app.agent.tools  # noqa: F401

        assert True
