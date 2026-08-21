"""RAGStepNode — knowledge retrieval + LLM synthesis."""

from __future__ import annotations

from typing import Any

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.steps.llm_step import LLMStepNode


class RAGStepNode(LLMStepNode):
    """RAG step = LLM step with rag config always active."""

    def __init__(
        self,
        step: StepDefinition,
        context_resolver: ContextResolver,
        **services: Any,
    ) -> None:
        super().__init__(step, context_resolver, **services)
        # Ensure rag is enabled even if not set in DSL (step type forces it)
        if not self.step.rag and self.step.input.get("collection"):
            from app.workflow.dsl import RAGConfig

            self.step = self.step.model_copy(
                update={"rag": RAGConfig(collection=str(self.step.input.get("collection", "")))}
            )
