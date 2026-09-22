"""Tests for app/voice/intent_router.py — voice command intent classification + dispatch."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.voice.intent_router import (
    VoiceIntent,
    classify_intent,
    handle_approve,
    handle_create_mission,
    route_voice_command,
)


# ── classify_intent ───────────────────────────────────────────────────────────


class TestClassifyIntent:
    @pytest.mark.parametrize(
        "transcript",
        [
            "launch a new mission for onboarding",
            "start a project to migrate the database",
            "I want you to build a dashboard",
            "let's work on the Q4 report",
        ],
    )
    def test_create_mission(self, transcript: str) -> None:
        result = classify_intent(transcript)
        assert result.intent == VoiceIntent.CREATE_MISSION
        assert result.confidence == 0.85

    @pytest.mark.parametrize(
        "transcript",
        ["approve", "go ahead", "yes do it", "confirm"],
    )
    def test_approve(self, transcript: str) -> None:
        assert classify_intent(transcript).intent == VoiceIntent.APPROVE

    @pytest.mark.parametrize(
        "transcript",
        ["reject", "deny", "cancel that", "no"],
    )
    def test_reject(self, transcript: str) -> None:
        assert classify_intent(transcript).intent == VoiceIntent.REJECT

    @pytest.mark.parametrize(
        "transcript",
        ["summarize the week", "brief me on the status", "what happened today", "what's new"],
    )
    def test_summarize(self, transcript: str) -> None:
        assert classify_intent(transcript).intent == VoiceIntent.SUMMARIZE

    @pytest.mark.parametrize(
        "transcript",
        ["what is the status", "how is it going", "update on the project"],
    )
    def test_status_check(self, transcript: str) -> None:
        assert classify_intent(transcript).intent == VoiceIntent.STATUS_CHECK

    @pytest.mark.parametrize(
        "transcript",
        ["find the report", "search for invoices", "show me the missions"],
    )
    def test_search(self, transcript: str) -> None:
        assert classify_intent(transcript).intent == VoiceIntent.SEARCH

    def test_unknown_transcript(self) -> None:
        result = classify_intent("banana pancakes are delicious")
        assert result.intent == VoiceIntent.UNKNOWN
        assert result.confidence == 0.5
        assert result.entities == {}

    def test_case_insensitive_and_stripped(self) -> None:
        result = classify_intent("   APPROVE   ")
        assert result.intent == VoiceIntent.APPROVE

    def test_create_mission_precedes_status_when_both_match(self) -> None:
        # "status" pattern order comes after create in checks, so create wins first
        result = classify_intent("launch a status project")
        assert result.intent == VoiceIntent.CREATE_MISSION


# ── handle_create_mission ─────────────────────────────────────────────────────


class TestHandleCreateMission:
    @pytest.mark.asyncio
    async def test_success_path_creates_mission(self) -> None:
        pipeline_instance = MagicMock()
        spec = MagicMock()
        spec.refined_goal = "Build a dashboard"
        spec.autonomy_level = 3
        spec.estimated_budget_usd = 500
        spec.estimated_duration_hours = 10
        spec.departments_involved = ["engineering"]
        spec.success_criteria = ["done"]
        spec.risk_level = "low"
        pipeline_instance.refine.return_value = spec

        mission = MagicMock()
        mission.id = "mission-123"

        svc_instance = AsyncMock()
        svc_instance.create_mission = AsyncMock(return_value=mission)

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock()
        session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
        session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

        session_factory = MagicMock(return_value=session)

        with (
            patch("app.org.goal_refinement.GoalRefinementPipeline", return_value=pipeline_instance),
            patch("app.db.rls.sqlalchemy_rls_context") as mock_rls,
            patch("app.org.service.OrgService", return_value=svc_instance),
        ):
            mock_rls.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_rls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await handle_create_mission(
                "build a dashboard", "org-1", "tenant-1", session_factory
            )

        assert "Mission created" in result
        assert "engineering" in result
        svc_instance.create_mission.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refinement_failure_falls_back_to_transcript(self) -> None:
        pipeline_instance = MagicMock()
        pipeline_instance.refine.side_effect = RuntimeError("llm down")

        mission = MagicMock()
        mission.id = "mission-456"
        svc_instance = AsyncMock()
        svc_instance.create_mission = AsyncMock(return_value=mission)

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock()
        session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
        session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        session_factory = MagicMock(return_value=session)

        with (
            patch("app.org.goal_refinement.GoalRefinementPipeline", return_value=pipeline_instance),
            patch("app.db.rls.sqlalchemy_rls_context") as mock_rls,
            patch("app.org.service.OrgService", return_value=svc_instance),
        ):
            mock_rls.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_rls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await handle_create_mission(
                "do the thing", "org-1", "tenant-1", session_factory
            )

        assert "Mission created" in result
        # fallback goal is the transcript itself
        svc_instance.create_mission.assert_awaited_once()
        _, kwargs = svc_instance.create_mission.call_args
        assert kwargs["objective"] == "do the thing"

    @pytest.mark.asyncio
    async def test_mission_creation_failure_returns_friendly_message(self) -> None:
        pipeline_instance = MagicMock()
        spec = MagicMock()
        spec.refined_goal = "Build a dashboard"
        spec.risk_level = "low"
        pipeline_instance.refine.return_value = spec

        session_factory = MagicMock(side_effect=RuntimeError("db down"))

        with patch(
            "app.org.goal_refinement.GoalRefinementPipeline", return_value=pipeline_instance
        ):
            result = await handle_create_mission(
                "build a dashboard", "org-1", "tenant-1", session_factory
            )

        assert "couldn't create the mission" in result

    @pytest.mark.asyncio
    async def test_high_risk_adds_approval_note(self) -> None:
        pipeline_instance = MagicMock()
        spec = MagicMock()
        spec.refined_goal = "Deploy to prod"
        spec.autonomy_level = 5
        spec.estimated_budget_usd = 0
        spec.estimated_duration_hours = 0
        spec.departments_involved = []
        spec.success_criteria = []
        spec.risk_level = "high"

        pipeline_instance.refine.return_value = spec
        mission = MagicMock()
        mission.id = "m-1"
        svc_instance = AsyncMock()
        svc_instance.create_mission = AsyncMock(return_value=mission)

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock()
        session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
        session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        session_factory = MagicMock(return_value=session)

        with (
            patch("app.org.goal_refinement.GoalRefinementPipeline", return_value=pipeline_instance),
            patch("app.db.rls.sqlalchemy_rls_context") as mock_rls,
            patch("app.org.service.OrgService", return_value=svc_instance),
        ):
            mock_rls.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_rls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await handle_create_mission(
                "deploy to prod", "org-1", "tenant-1", session_factory
            )

        assert "High-risk" in result


# ── handle_approve ─────────────────────────────────────────────────────────────


class TestHandleApprove:
    @pytest.mark.asyncio
    async def test_no_pending_decision_returns_prompt(self) -> None:
        result = await handle_approve("approve", "org-1", "tenant-1", MagicMock(), None)
        assert "don't have a pending decision" in result

    @pytest.mark.asyncio
    async def test_success_records_decision(self) -> None:
        svc_instance = AsyncMock()
        svc_instance.record_decision = AsyncMock()

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock()
        session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
        session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        session_factory = MagicMock(return_value=session)

        with (
            patch("app.db.rls.sqlalchemy_rls_context") as mock_rls,
            patch("app.org.service.OrgService", return_value=svc_instance),
        ):
            mock_rls.return_value.__aenter__ = AsyncMock(return_value=None)
            mock_rls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await handle_approve(
                "approve", "org-1", "tenant-1", session_factory, "decision-1"
            )

        assert result == "Approved. The mission is cleared to proceed."
        svc_instance.record_decision.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_returns_friendly_message(self) -> None:
        session_factory = MagicMock(side_effect=RuntimeError("boom"))
        result = await handle_approve(
            "approve", "org-1", "tenant-1", session_factory, "decision-1"
        )
        assert "couldn't record the approval" in result


# ── route_voice_command ────────────────────────────────────────────────────────


class TestRouteVoiceCommand:
    @pytest.mark.asyncio
    async def test_routes_to_create_mission(self) -> None:
        session_factory = MagicMock()
        with patch(
            "app.voice.intent_router.handle_create_mission", AsyncMock(return_value="created")
        ) as mock_handle:
            result = await route_voice_command(
                "launch a new mission", "org-1", "tenant-1", session_factory
            )
        assert result == "created"
        mock_handle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_mission_without_session_factory_falls_to_unknown(self) -> None:
        result = await route_voice_command("launch a new mission", "org-1", "tenant-1", None)
        assert "I heard:" in result

    @pytest.mark.asyncio
    async def test_routes_to_approve(self) -> None:
        with patch(
            "app.voice.intent_router.handle_approve", AsyncMock(return_value="approved!")
        ) as mock_handle:
            result = await route_voice_command(
                "approve", "org-1", "tenant-1", MagicMock(), "dec-1"
            )
        assert result == "approved!"
        mock_handle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_to_reject(self) -> None:
        result = await route_voice_command("reject", "org-1", "tenant-1", MagicMock())
        assert "rejected" in result

    @pytest.mark.asyncio
    async def test_routes_to_summarize(self) -> None:
        with patch(
            "app.voice.intent_router._handle_summarize", AsyncMock(return_value="summary")
        ):
            result = await route_voice_command(
                "summarize the week", "org-1", "tenant-1", MagicMock()
            )
        assert result == "summary"

    @pytest.mark.asyncio
    async def test_routes_to_status(self) -> None:
        with patch("app.voice.intent_router._handle_status", AsyncMock(return_value="status")):
            result = await route_voice_command(
                "what is the status", "org-1", "tenant-1", MagicMock()
            )
        assert result == "status"

    @pytest.mark.asyncio
    async def test_routes_to_search(self) -> None:
        result = await route_voice_command("find the report", "org-1", "tenant-1", MagicMock())
        assert "Searching for" in result

    @pytest.mark.asyncio
    async def test_unknown_returns_confirmation_prompt(self) -> None:
        result = await route_voice_command("banana pancakes", "org-1", "tenant-1", MagicMock())
        assert "I heard: banana pancakes." in result


# ── _handle_summarize / _handle_status / _get_health ────────────────────────────


class TestSummarizeAndStatus:
    @pytest.mark.asyncio
    async def test_get_health_returns_empty_without_session_factory(self) -> None:
        from app.voice.intent_router import _get_health

        result = await _get_health("org-1", "tenant-1", None)
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_health_returns_empty_on_exception(self) -> None:
        from app.voice.intent_router import _get_health

        session_factory = MagicMock(side_effect=RuntimeError("db down"))
        result = await _get_health("org-1", "tenant-1", session_factory)
        assert result == {}

    @pytest.mark.asyncio
    async def test_summarize_formats_health(self) -> None:
        from app.voice.intent_router import _handle_summarize

        health = {
            "active_missions": 3,
            "active_teams": 2,
            "pending_approvals": 1,
            "overall_health": "good",
        }
        with patch(
            "app.voice.intent_router._get_health", AsyncMock(return_value=health)
        ):
            result = await _handle_summarize("org-1", "tenant-1", MagicMock())
        assert "3 active missions" in result
        assert "good" in result

    @pytest.mark.asyncio
    async def test_status_singular_mission_grammar(self) -> None:
        from app.voice.intent_router import _handle_status

        with patch(
            "app.voice.intent_router._get_health",
            AsyncMock(return_value={"active_missions": 1, "overall_health": "ok"}),
        ):
            result = await _handle_status("org-1", "tenant-1", MagicMock())
        assert "1 mission currently running" in result

    @pytest.mark.asyncio
    async def test_status_plural_mission_grammar(self) -> None:
        from app.voice.intent_router import _handle_status

        with patch(
            "app.voice.intent_router._get_health",
            AsyncMock(return_value={"active_missions": 4, "overall_health": "ok"}),
        ):
            result = await _handle_status("org-1", "tenant-1", MagicMock())
        assert "4 missions currently running" in result
