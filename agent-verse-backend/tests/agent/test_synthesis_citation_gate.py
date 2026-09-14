"""Hallucination T6 — citation gate wired into answer synthesis."""
from __future__ import annotations

from app.agent.state import StepResult, StepStatus
from app.agent.synthesis import AnswerSynthesizer


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    async def complete(self, request):
        class _R:
            content = self._content

        return _R()


def _step(output: str) -> StepResult:
    return StepResult(description="s", output=output, status=StepStatus.COMPLETE)


async def test_gate_strips_unsupported_claim_from_synthesis() -> None:
    steps = [_step("We closed JIRA-101 today.")]
    # LLM invents an extra, unsupported number with a bogus citation.
    llm = _FakeLLM("Closed JIRA-101 [Step 1]. Also fixed 999 other bugs [Step 1].")
    engine = AnswerSynthesizer(llm_provider=llm, enforce_citation_gate=True)
    ans = await engine.synthesize("close tickets", steps)
    assert "JIRA-101" in ans.answer
    assert "999" not in ans.answer  # unsupported claim stripped
    assert ans.grounding_score < 1.0


async def test_gate_can_be_disabled() -> None:
    steps = [_step("We closed JIRA-101 today.")]
    llm = _FakeLLM("Closed JIRA-101 [Step 1]. Also fixed 999 bugs [Step 1].")
    engine = AnswerSynthesizer(llm_provider=llm, enforce_citation_gate=False)
    ans = await engine.synthesize("close tickets", steps)
    assert "999" in ans.answer  # ungated → passthrough


async def test_fully_supported_answer_is_unchanged() -> None:
    steps = [_step("Closed JIRA-101 and 7 tickets.")]
    llm = _FakeLLM("Closed JIRA-101 [Step 1] and 7 tickets [Step 1].")
    engine = AnswerSynthesizer(llm_provider=llm, enforce_citation_gate=True)
    ans = await engine.synthesize("close tickets", steps)
    assert "JIRA-101" in ans.answer and "7 tickets" in ans.answer
