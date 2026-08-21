"""LLMStepNode — LLM completion with optional RAG retrieval."""

from __future__ import annotations

import time
from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState

_log = get_logger(__name__)


class LLMStepNode:
    def __init__(
        self,
        step: StepDefinition,
        context_resolver: ContextResolver,
        **services: Any,
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.llm_provider = services.get("llm_provider")
        self.knowledge_store = services.get("knowledge_store")

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        if state.get("is_test_run") and self.step.id in (state.get("mock_overrides") or {}):
            output = (state["mock_overrides"] or {})[self.step.id]
            return {
                "step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output},
            }

        # Resolve prompt template
        prompt_text = self.ctx.resolve(self.step.prompt, state)

        # Optional RAG context injection
        rag_context = ""
        if self.step.rag and self.knowledge_store:
            try:
                results = await self.knowledge_store.retrieve(
                    query=str(prompt_text),
                    collection_name=self.step.rag.collection,
                    top_k=self.step.rag.top_k,
                    tenant_id=state.get("tenant_id", ""),
                )
                if results:
                    rag_context = "\n\nRelevant context:\n" + "\n---\n".join(
                        r.get("content", "") for r in results
                    )
            except Exception as exc:
                _log.warning("rag_retrieval_failed", step_id=self.step.id, error=str(exc))

        full_prompt = str(prompt_text) + rag_context

        start = time.monotonic()
        tokens_in = tokens_out = 0
        cost_usd = 0.0

        if self.llm_provider is None:
            # Fake provider for tests / dry-runs
            output = {"result": f"[FakeProvider: {self.step.id}]", "confidence": 1.0}
        else:
            from app.providers.base import CompletionRequest, Message

            req = CompletionRequest(
                messages=[
                    Message(
                        role="system",
                        content="You are a workflow step executor. Return JSON only.",
                    ),
                    Message(role="user", content=full_prompt),
                ],
                model=self.step.model or "gpt-4o",
                temperature=self.step.temperature,
                max_tokens=self.step.max_tokens,
            )
            response = await self.llm_provider.complete(req)
            raw_text = response.content.strip()
            tokens_in = getattr(response, "prompt_tokens", 0)
            tokens_out = getattr(response, "completion_tokens", 0)
            cost_usd = getattr(response, "cost_usd", 0.0)

            # Parse JSON from response
            import json
            import re

            json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if json_match:
                try:
                    output = json.loads(json_match.group())
                except json.JSONDecodeError:
                    output = {"result": raw_text}
            else:
                output = {"result": raw_text}

        duration_ms = int((time.monotonic() - start) * 1000)

        return {
            "step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output},
            "step_timings": {**(state.get("step_timings") or {}), self.step.id: duration_ms},
            "cost_usd": (state.get("cost_usd") or 0.0) + cost_usd,
            "tokens_used": (state.get("tokens_used") or 0) + tokens_in + tokens_out,
        }
