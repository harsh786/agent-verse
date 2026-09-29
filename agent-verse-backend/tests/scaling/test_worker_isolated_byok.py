"""Regression: the worker's isolated-execution path swallowed BYOK key errors.

``run_goal`` resolved the tenant's scoped LLM key for the execution envelope in
``try: ... except Exception: logger.warning(...)`` and carried on with an empty
key — so a BYOK tenant whose key could not be read (vault/decrypt/DB error) ran
on the PLATFORM key: platform-billed, and against the tenant's own policy of
using its provider. It now fails the goal closed; a tenant with no BYOK key
configured still runs (the resolver returns '').
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def isolated_worker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.core.runtime_flags as flags_mod
    import app.execution_environment.envelope as envelope_mod
    import app.execution_environment.scheduler as scheduler_mod
    from app.scaling import tasks

    seen: dict[str, Any] = {"envelopes": []}

    isolated = dataclasses.replace(
        flags_mod.get_runtime_flags(),
        isolated_agent_execution=True,
        isolated_execution_required=True,
    )
    monkeypatch.setattr(flags_mod, "get_runtime_flags", lambda: isolated)

    def _build(**kwargs: Any) -> Any:
        seen["envelopes"].append(kwargs)
        return SimpleNamespace(**kwargs)

    class _Scheduler:
        async def schedule(self, envelope: Any, event_callback: Any = None) -> Any:
            return SimpleNamespace(
                status="complete",
                iterations=1,
                runner_type="fake",
                capsule_id="c1",
                execution_time_ms=1,
            )

    monkeypatch.setattr(envelope_mod, "build_envelope", _build)
    monkeypatch.setattr(
        scheduler_mod.ExecutionEnvironmentScheduler,
        "from_flags",
        classmethod(lambda cls, **kw: _Scheduler()),
    )
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    return seen


def test_byok_key_resolution_failure_fails_the_goal_closed(
    isolated_worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.llm_config_store as store_mod
    from app.scaling import tasks

    async def _boom(tenant_id: str) -> str:
        raise RuntimeError("vault decrypt failed")

    monkeypatch.setattr(store_mod, "aget_llm_api_key_for_tenant", _boom)
    result = tasks.run_goal.run("g-byok-1", "tenant-byok", "do it", "normal", False)
    assert result["status"] == "failed"
    assert "byok" in result["reason"].lower()
    assert isolated_worker["envelopes"] == []  # never dispatched on the platform key


def test_resolved_byok_key_is_scoped_into_the_envelope(
    isolated_worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.llm_config_store as store_mod
    from app.scaling import tasks

    async def _key(tenant_id: str) -> str:
        return "sk-tenant"

    monkeypatch.setattr(store_mod, "aget_llm_api_key_for_tenant", _key)
    result = tasks.run_goal.run("g-byok-2", "tenant-byok", "do it", "normal", False)
    assert result["status"] == "complete", result
    [envelope] = isolated_worker["envelopes"]
    assert envelope["scoped_llm_api_key"] == "sk-tenant"
