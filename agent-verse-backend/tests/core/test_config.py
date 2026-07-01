"""Tests for typed application settings."""

import pytest

import app.core.config as config
from app.core.config import Settings, get_settings


def test_defaults_apply_when_env_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    assert settings.debug is False


def test_reads_environment_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    settings = Settings()
    assert settings.environment == "production"
    assert settings.is_production is True


def test_cors_origins_parsed_from_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.com, https://b.com")
    settings = Settings()
    assert settings.cors_origins == ["https://a.com", "https://b.com"]


def test_invalid_environment_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "banana")
    with pytest.raises(ValueError):
        Settings()


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_provider_env_falls_back_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: Settings(openai_api_key="from-settings", _env_file=None),
    )

    assert config.get_provider_env("OPENAI_API_KEY") == "from-settings"
