"""Connector feature flags are enforced even when callers pass no settings.

Regression: the scheduler, sources API and Celery sync all called
``get_connector(source_type)`` without ``settings``, so LAW-20 feature flags
were never checked. DuckDB (tenant SQL executed in-process on the host) is now
off by default and must be enabled explicitly.
"""

from __future__ import annotations

import importlib

import pytest

from app.core.config import get_settings
from app.ingestion.connector_registry import get_connector


@pytest.fixture(autouse=True)
def _load_duckdb() -> None:
    importlib.import_module("app.ingestion.connectors.duckdb_connector")


def test_duckdb_disabled_by_default_without_explicit_settings() -> None:
    assert get_settings().ingestion_connector_duckdb_enabled is False
    with pytest.raises(RuntimeError, match="disabled by feature flag"):
        get_connector("duckdb")


def test_duckdb_enabled_when_operator_turns_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ingestion_connector_duckdb_enabled", True)
    assert get_connector("duckdb").__name__ == "DuckDBConnector"
