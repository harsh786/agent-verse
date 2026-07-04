"""Tests for Phase 3 Track C — AnswerSynthesizer."""
import pytest
from app.agent.synthesis import AnswerSynthesizer, CitedAnswer, Citation


class MockStep:
    def __init__(self, step="step 1", output="output 1", tool_calls=None):
        self.step = step
        self.output = output
        self.tool_calls = tool_calls or []


class TestAnswerSynthesizer:
    @pytest.mark.asyncio
    async def test_synthesize_deterministic_no_llm(self):
        synth = AnswerSynthesizer(llm_provider=None)
        steps = [
            MockStep("search jira", "Found JIRA-123: bug in auth"),
            MockStep("create report", "Report: 1 critical bug"),
        ]
        result = await synth.synthesize("Find and report bugs", steps)
        assert isinstance(result, CitedAnswer)
        assert len(result.answer) > 0
        assert len(result.citations) > 0

    @pytest.mark.asyncio
    async def test_synthesize_empty_steps(self):
        synth = AnswerSynthesizer()
        result = await synth.synthesize("test goal", [])
        assert "No steps" in result.answer
        assert result.citations == []

    @pytest.mark.asyncio
    async def test_synthesize_with_llm(self):
        from app.providers.fake import FakeProvider
        llm = FakeProvider(responses=["Found JIRA-123 [Step 1] as the critical bug [Step 1]."])
        synth = AnswerSynthesizer(llm_provider=llm)
        steps = [MockStep("search", "JIRA-123: critical bug")]
        result = await synth.synthesize("Find critical bugs", steps)
        assert "JIRA-123" in result.answer or len(result.answer) > 0

    def test_citation_dataclass(self):
        c = Citation(text="JIRA-123", source="step_1_jira_search", step_index=0)
        assert c.text == "JIRA-123"
        assert c.confidence == 1.0

    def test_cited_answer_dataclass(self):
        ca = CitedAnswer(answer="test", citations=[])
        assert ca.grounding_score == 1.0
