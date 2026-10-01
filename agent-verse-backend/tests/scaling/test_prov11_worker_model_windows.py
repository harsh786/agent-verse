"""PROV-11: worker processes probe the on-prem models' context windows at start.

``probe_model_windows`` ran only at API startup, so Celery workers (the
production goal path) kept an empty window map and could not size prompts or
role assignments to the served models' ``max_model_len``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.ai_router import deployment_roles


@pytest.fixture(autouse=True)
def _clean_windows() -> Any:
    saved = dict(deployment_roles._CONTEXT_WINDOWS)
    deployment_roles._CONTEXT_WINDOWS.clear()
    yield
    deployment_roles._CONTEXT_WINDOWS.clear()
    deployment_roles._CONTEXT_WINDOWS.update(saved)


def _patch(monkeypatch: pytest.MonkeyPatch, *, onprem: bool) -> list[Any]:
    import app.core.config as config_mod

    calls: list[Any] = []

    async def _probe(settings: Any = None, **_: Any) -> dict[str, int]:
        calls.append(settings)
        deployment_roles.record_context_window("qwen-local", 32768)
        return dict(deployment_roles._CONTEXT_WINDOWS)

    monkeypatch.setattr(deployment_roles, "probe_model_windows", _probe)
    monkeypatch.setattr(
        config_mod, "get_settings", lambda: SimpleNamespace(onprem_enabled=onprem)
    )
    monkeypatch.setattr("app.scaling.worker_cost.install_worker_cost_services", lambda: None)
    return calls


def test_worker_init_populates_the_window_map(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    calls = _patch(monkeypatch, onprem=True)
    tasks._setup_worker_checkpointer()
    assert calls and deployment_roles._CONTEXT_WINDOWS == {"qwen-local": 32768}


def test_worker_init_skips_the_probe_without_onprem(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    calls = _patch(monkeypatch, onprem=False)
    tasks._setup_worker_checkpointer()
    assert calls == [] and deployment_roles._CONTEXT_WINDOWS == {}
