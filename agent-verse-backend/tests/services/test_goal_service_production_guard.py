"""Test that FakeProvider is not silently used in production mode."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _mock_settings(environment: str = "development") -> MagicMock:
    """Build a mock settings object with the given environment value."""
    s = MagicMock()
    s.environment = environment
    return s


def test_make_agent_loop_raises_in_production() -> None:
    """In production mode, _make_agent_loop must raise RuntimeError."""
    with patch("app.core.config.get_settings", return_value=_mock_settings("production")):
        from app.services import goal_service

        with pytest.raises(RuntimeError, match="Cannot use FakeProvider in production"):
            goal_service._make_agent_loop()


def test_make_agent_loop_works_in_development() -> None:
    """In development mode, _make_agent_loop returns a usable AgentGraph."""
    with patch("app.core.config.get_settings", return_value=_mock_settings("development")):
        from app.services import goal_service

        loop = goal_service._make_agent_loop()
        assert loop is not None


def test_make_agent_loop_works_when_environment_missing() -> None:
    """If 'environment' attr is absent, getattr defaults to 'development' (no raise)."""
    no_env_settings = MagicMock(spec=[])  # No attributes — getattr returns default

    with patch("app.core.config.get_settings", return_value=no_env_settings):
        from app.services import goal_service

        loop = goal_service._make_agent_loop()
        assert loop is not None


def test_make_agent_loop_raises_only_for_production_string() -> None:
    """Only the exact string 'production' triggers the guard — not 'PRODUCTION', 'prod', etc."""
    from app.services import goal_service

    for env in ("staging", "test", "PRODUCTION", "prod", ""):
        with patch("app.core.config.get_settings", return_value=_mock_settings(env)):
            loop = goal_service._make_agent_loop()
            assert loop is not None, f"Should not raise for environment={env!r}"


def test_make_agent_loop_error_message_mentions_api_keys() -> None:
    """The RuntimeError message must guide the operator to the solution."""
    with patch("app.core.config.get_settings", return_value=_mock_settings("production")):
        from app.services import goal_service

        with pytest.raises(RuntimeError) as exc_info:
            goal_service._make_agent_loop()

        msg = str(exc_info.value)
        assert "production" in msg.lower()
        # Must mention at least one API key env var
        assert any(
            key in msg for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")
        )
