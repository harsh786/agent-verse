"""Corrective RAG grading is circuit-broken and NOT double-charged.

The strategy LLM is already a _BudgetedProvider (RAG cost guard), so the
decision path must only add the circuit breaker and timeout.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.providers import guarded_completion as gc
from app.rag.agentic.patterns.corrective import grade_evidence
from app.rag.engine import RetrievalResult, RetrievalStrategyExecutionError
from tests.providers._decision_fakes import RecordingController, ScriptedProvider

_RESULTS = [
    RetrievalResult("c1", "22 days of paid annual leave", 0.9, {}, ["vector"]),
    RetrievalResult("c2", "canteen menu", 0.4, {}, ["vector"]),
]


@pytest.fixture(autouse=True)
def _platform() -> Any:
    saved = gc._platform_services
    yield
    gc.set_platform_cost_services(saved)


async def test_grading_is_not_charged_again() -> None:
    ctrl = RecordingController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    scores = await grade_evidence(
        provider=ScriptedProvider('{"relevance": [0.9, 0.1]}'),
        model="",
        query="leave",
        results=_RESULTS,
    )
    assert scores == [0.9, 0.1]
    assert ctrl.recorded == []


async def test_hung_grader_fails_the_strategy_instead_of_stalling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTVERSE_DECISION_CALL_TIMEOUT_SECONDS", "0.05")
    with pytest.raises(RetrievalStrategyExecutionError, match="evidence grading failed"):
        await asyncio.wait_for(
            grade_evidence(
                provider=ScriptedProvider(hang=True), model="", query="q", results=_RESULTS
            ),
            timeout=5,
        )
