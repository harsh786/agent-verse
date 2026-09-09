"""Tests for skills CRUD API and red-team corpus expansion."""


class TestSkillsAPI:
    def test_skills_router_importable(self):
        from app.api.skills import router
        assert router is not None

    def test_skills_has_list_endpoint(self):
        from app.api.skills import router
        paths = [r.path for r in router.routes]
        assert any("/skills" in p or p == "" for p in paths)

    def test_skills_has_create_endpoint(self):
        from app.api.skills import router
        methods_paths = [
            (r.path, r.methods)
            for r in router.routes
            if hasattr(r, "methods")
        ]
        post_paths = [p for p, m in methods_paths if m and "POST" in m]
        assert len(post_paths) > 0

    def test_skills_has_delete_endpoint(self):
        from app.api.skills import router
        methods_paths = [
            (r.path, r.methods)
            for r in router.routes
            if hasattr(r, "methods")
        ]
        delete_paths = [p for p, m in methods_paths if m and "DELETE" in m]
        assert len(delete_paths) > 0

    def test_platform_skill_to_response(self):
        from app.agent.skill_selector import PLATFORM_SKILLS
        from app.api.skills import _platform_skill_to_response
        for skill in PLATFORM_SKILLS:
            result = _platform_skill_to_response(skill)
            assert "id" in result
            assert "name" in result
            assert result["is_platform"] is True

    def test_platform_skill_to_response_has_all_fields(self):
        from app.agent.skill_selector import PLATFORM_SKILLS
        from app.api.skills import _platform_skill_to_response
        required_fields = {
            "id", "name", "description", "trigger_hints",
            "instructions", "allowed_tools", "token_estimate",
            "visibility", "is_platform",
        }
        for skill in PLATFORM_SKILLS:
            result = _platform_skill_to_response(skill)
            assert required_fields.issubset(result.keys()), (
                f"Skill '{skill.get('name')}' response missing fields: "
                f"{required_fields - result.keys()}"
            )

    def test_skill_create_request_model(self):
        from app.api.skills import SkillCreateRequest
        req = SkillCreateRequest(
            name="test-skill",
            description="A test skill",
            trigger_hints=["test", "example"],
            instructions="Do something useful",
        )
        assert req.name == "test-skill"
        assert req.allowed_tools == []
        assert req.token_estimate == 100
        assert req.visibility == "tenant"

    def test_skill_response_model(self):
        from app.api.skills import SkillResponse
        resp = SkillResponse(
            id="abc123",
            name="my-skill",
            description="desc",
            trigger_hints=["hint"],
            instructions="instructions",
            allowed_tools=[],
            token_estimate=80,
            visibility="tenant",
        )
        assert resp.is_platform is False


class TestRedTeamCorpus:
    def test_corpus_has_50_plus_cases(self):
        from app.enterprise.red_team_corpus import RED_TEAM_CORPUS
        assert len(RED_TEAM_CORPUS) >= 50, (
            f"Expected ≥50 cases, got {len(RED_TEAM_CORPUS)}"
        )

    def test_corpus_has_multi_turn_category(self):
        from app.enterprise.red_team_corpus import RED_TEAM_CORPUS
        categories = {c.category for c in RED_TEAM_CORPUS}
        assert "multi_turn_escalation" in categories or "social_engineering" in categories

    def test_corpus_has_authorization_bypass(self):
        from app.enterprise.red_team_corpus import RED_TEAM_CORPUS
        categories = {c.category for c in RED_TEAM_CORPUS}
        has_auth = "authorization_bypass" in categories
        has_exfil = "exfiltration" in categories
        assert has_auth or has_exfil

    def test_corpus_has_authorization_bypass_category(self):
        from app.enterprise.red_team_corpus import RED_TEAM_CORPUS
        categories = {c.category for c in RED_TEAM_CORPUS}
        assert "authorization_bypass" in categories

    def test_corpus_has_no_duplicate_ids(self):
        from app.enterprise.red_team_corpus import RED_TEAM_CORPUS
        ids = [c.id for c in RED_TEAM_CORPUS]
        assert len(ids) == len(set(ids)), (
            f"Duplicate IDs found: {[i for i in ids if ids.count(i) > 1]}"
        )

    def test_corpus_legitimate_cases_not_blocked(self):
        from app.enterprise.red_team_corpus import RED_TEAM_CORPUS
        legit = [c for c in RED_TEAM_CORPUS if c.category == "legitimate"]
        assert len(legit) >= 3
        assert all(not c.expected_blocked for c in legit)

    def test_corpus_blocking_cases_all_blocked(self):
        from app.enterprise.red_team_corpus import RED_TEAM_CORPUS
        critical = [c for c in RED_TEAM_CORPUS if c.severity == "critical"]
        assert all(c.expected_blocked for c in critical), (
            "All critical cases must be expected_blocked=True"
        )

    def test_new_categories_present(self):
        from app.enterprise.red_team_corpus import RED_TEAM_CORPUS
        categories = {c.category for c in RED_TEAM_CORPUS}
        expected_new = {"multi_turn_escalation", "authorization_bypass"}
        assert expected_new.issubset(categories), (
            f"Missing new categories: {expected_new - categories}"
        )

    def test_get_cases_by_category(self):
        from app.enterprise.red_team_corpus import get_cases_by_category
        auth_cases = get_cases_by_category("authorization_bypass")
        assert len(auth_cases) >= 2
        assert all(c.category == "authorization_bypass" for c in auth_cases)

    def test_get_blocking_cases(self):
        from app.enterprise.red_team_corpus import get_blocking_cases
        cases = get_blocking_cases()
        assert len(cases) > 30
        assert all(c.expected_blocked for c in cases)
