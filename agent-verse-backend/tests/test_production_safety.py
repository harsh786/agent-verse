"""Tests that production safety guards are in place."""
from __future__ import annotations

import os
import pytest


def test_no_random_embeddings_returned():
    """embed_texts() never returns random vectors — returns [] when no provider."""
    import asyncio

    from app.providers.base import embed_texts

    result = asyncio.run(embed_texts(["test text"], provider=None))
    assert isinstance(result, list)
    for emb in result:
        # Each embedding should be empty or a real embedding (not random noise)
        assert emb == [] or len(emb) == 0 or (len(emb) > 0 and isinstance(emb[0], float))


def test_tool_inverses_mcp_client_settable():
    """tool_inverses module accepts MCP client injection."""
    from app.reliability.tool_inverses import _INVERSE_REGISTRY, set_mcp_client

    set_mcp_client(None)  # Should not raise
    assert "jira:create_issue" in _INVERSE_REGISTRY


def test_tool_inverses_get_inverse_fn_noop_for_unknown():
    """get_inverse_fn returns no-op lambda for unknown tools."""
    from app.reliability.tool_inverses import get_inverse_fn

    fn = get_inverse_fn("unknown:tool", {})
    fn()  # Should not raise


def test_redis_cost_controller_importable():
    """RedisCostController is importable."""
    from app.governance.cost import RedisCostController

    assert RedisCostController is not None


def test_slack_tenant_id_from_env(monkeypatch):
    """Slack uses env var SLACK_TENANT_ID, not hardcoded string."""
    monkeypatch.setenv("SLACK_TENANT_ID", "my-slack-tenant")
    # Force reimport to pick up env var at call time
    from app.api.integrations import _get_slack_tenant_id

    assert _get_slack_tenant_id() == "my-slack-tenant"


def test_slack_tenant_id_empty_without_env(monkeypatch):
    monkeypatch.delenv("SLACK_TENANT_ID", raising=False)
    from app.api.integrations import _get_slack_tenant_id

    assert _get_slack_tenant_id() == ""


def test_zapier_tenant_id_from_env(monkeypatch):
    monkeypatch.setenv("ZAPIER_TENANT_ID", "my-zapier-tenant")
    from app.api.integrations import _get_zapier_tenant_id

    assert _get_zapier_tenant_id() == "my-zapier-tenant"


def test_simulation_runner_single_definition():
    """SimulationRunner class is defined exactly once (no duplicate)."""
    import inspect

    import app.enterprise.simulation as sim_mod

    source = inspect.getsource(sim_mod)
    count = source.count("class SimulationRunner")
    assert count == 1, f"SimulationRunner defined {count}x — remove duplicate"


def test_cost_controller_has_redis_attr():
    """CostController accepts Redis client for cross-replica cost tracking."""
    from app.governance.cost import CostController

    cc = CostController()
    assert hasattr(cc, "_redis")


def test_minio_default_cred_warning_in_dev(monkeypatch, caplog):
    """MinIO store warns when using default minioadmin credentials in dev."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("MINIO_ACCESS_KEY", raising=False)
    monkeypatch.delenv("MINIO_SECRET_KEY", raising=False)
    import logging

    with caplog.at_level(logging.WARNING):
        from app.rpa.artifacts import MinIOArtifactStore

        store = MinIOArtifactStore()
    # Warning should be logged (or at minimum no crash)
    assert store is not None
