"""Regression + behaviour tests for the org composer's LLM wiring (WS-2 last mile).

Two things are pinned here:

1. ``resolve_llm_provider`` resolves the provider that ``create_app`` actually
   binds (``app.state._app_provider``). A prior bug read a ``planner_provider``
   attribute that is *never* set on ``app.state``, so the LLM composition/
   decomposition path was dead code and every org silently used the template/
   heuristic fallback. This guards that the real attribute is honoured and that
   a bare state (nothing wired) degrades to ``None`` rather than raising.

2. When a usable provider is supplied, ``compose_from_nl`` designs a bespoke
   department structure from the model output (``composition_method == "llm"``)
   instead of the industry template; and ``decompose_mission`` uses the model's
   subtask plan. Both degrade cleanly to the deterministic path on unusable
   output — proving nothing is hardcoded on the happy path yet nothing breaks
   without a model.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.providers.base import CompletionResponse
from app.org.service import OrgService, resolve_llm_provider


class _CannedProvider:
    """Minimal LLMProvider stub returning a fixed completion body."""

    def __init__(self, body: str) -> None:
        self._body = body
        self.calls = 0

    async def complete(self, request: Any) -> CompletionResponse:
        self.calls += 1
        return CompletionResponse(content=self._body, model="canned")


# ── resolve_llm_provider ─────────────────────────────────────────────────────


def test_resolve_prefers_app_provider_when_planner_absent() -> None:
    # The exact shape create_app produces: only _app_provider is set.
    sentinel = object()
    state = SimpleNamespace(_app_provider=sentinel)
    assert resolve_llm_provider(state) is sentinel


def test_resolve_returns_none_when_nothing_wired() -> None:
    assert resolve_llm_provider(SimpleNamespace()) is None
    assert resolve_llm_provider(None) is None


def test_resolve_explicit_planner_wins_then_override() -> None:
    planner, app_prov, override = object(), object(), object()
    state = SimpleNamespace(
        planner_provider=planner, _app_provider=app_prov, _llm_provider_override=override
    )
    assert resolve_llm_provider(state) is planner
    state2 = SimpleNamespace(_llm_provider_override=override)
    assert resolve_llm_provider(state2) is override


# ── compose_from_nl: LLM path vs template degrade ────────────────────────────


def _composer_service() -> tuple[OrgService, list[str]]:
    svc = OrgService(session=MagicMock(), tenant_id="t-compose")
    created_departments: list[str] = []

    async def _create_org(**kw: Any) -> Any:
        import uuid

        return SimpleNamespace(
            id=uuid.uuid4(),
            name=kw.get("name", "Org"),
            autonomy_level=kw.get("autonomy_level", 2),
            status="active",
        )

    async def _create_dept(**kw: Any) -> Any:
        import uuid

        created_departments.append(kw["name"])
        return SimpleNamespace(
            id=uuid.uuid4(),
            name=kw["name"],
            purpose=kw.get("purpose", ""),
            capability_domains=kw.get("capability_domains", []),
        )

    async def _create_mission(**kw: Any) -> Any:
        import uuid

        return SimpleNamespace(id=uuid.uuid4(), title=kw.get("title"), status="pending")

    svc.create_organization = AsyncMock(side_effect=_create_org)  # type: ignore[method-assign]
    svc.create_department = AsyncMock(side_effect=_create_dept)  # type: ignore[method-assign]
    svc.create_mission = AsyncMock(side_effect=_create_mission)  # type: ignore[method-assign]
    return svc, created_departments


@pytest.mark.asyncio
async def test_compose_uses_llm_departments_for_arbitrary_objective() -> None:
    svc, created = _composer_service()
    body = (
        '[{"name": "Lunar Logistics", "purpose": "move cargo", '
        '"capability_domains": ["logistics"]}, '
        '{"name": "Regolith Science", "purpose": "study soil", '
        '"capability_domains": ["research"]}, '
        '{"name": "Habitat Ops", "purpose": "run the base", '
        '"capability_domains": ["operations"]}]'
    )
    provider = _CannedProvider(body)
    result = await svc.compose_from_nl(
        description="Establish a self-sustaining moon base supply chain",
        goals=["Ship first cargo"],
        industry="",  # deliberately no industry -> template would be generic default
        llm_provider=provider,
    )
    assert provider.calls == 1
    assert result["composition_method"] == "llm"
    # Departments came from the model, NOT the deterministic default template.
    assert created == ["Lunar Logistics", "Regolith Science", "Habitat Ops"]
    assert {"Operations", "Engineering", "Research", "Growth"}.isdisjoint(created)


@pytest.mark.asyncio
async def test_compose_degrades_to_template_on_unusable_llm_output() -> None:
    svc, created = _composer_service()
    provider = _CannedProvider("not json at all")
    result = await svc.compose_from_nl(
        description="A generic company doing generic things",
        industry="fintech",
        llm_provider=provider,
    )
    # Degrades honestly to the fintech template — no crash, method disclosed.
    assert result["composition_method"] == "template"
    assert "Risk & Compliance" in created


@pytest.mark.asyncio
async def test_decompose_uses_llm_subtasks_when_provider_usable() -> None:
    svc, _ = _composer_service()
    body = (
        '[{"title": "Scout vendors", "objective": "find suppliers"}, '
        '{"title": "Score cycle life", "objective": "benchmark"}, '
        '{"title": "Recommend", "objective": "shortlist"}]'
    )
    provider = _CannedProvider(body)
    subtasks = await svc.decompose_mission(
        objective="Compare solid-state battery vendors",
        team_departments=["research"],  # would be phase-template without a model
        llm_provider=provider,
    )
    assert [s["title"] for s in subtasks] == [
        "Scout vendors",
        "Score cycle life",
        "Recommend",
    ]
    assert all(s["order"] == i for i, s in enumerate(subtasks))
