"""LLMStepNode — LLM completion with optional RAG retrieval."""

from __future__ import annotations

import time
from typing import Any

from app.observability.logging import get_logger
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState
from app.workflow.steps import StepServiceUnavailableError

_log = get_logger(__name__)


def _default_model_for(provider: Any) -> str:
    """"" for a tenant's own provider (BYOK-3: its configured default_model applies),
    else the system-configured model."""
    from app.providers.model_defaults import configured_default_model

    if getattr(provider, "_byok_tenant_id", None):
        return ""
    return configured_default_model("gpt-4o")


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
        # BYOK-3: resolves the run tenant's provider per execution (tenant BYOK →
        # platform → error). Preferred over the process-wide ``llm_provider``.
        self.llm_provider_resolver = services.get("llm_provider_resolver")
        self.knowledge_store = services.get("knowledge_store")

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        if state.get("is_test_run") and self.step.id in (state.get("mock_overrides") or {}):
            output = (state["mock_overrides"] or {})[self.step.id]
            return {
                "step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output},
            }

        # Resolve prompt template
        prompt_text = self.ctx.resolve(self.step.prompt, state)

        from app.workflow.llm_provider import step_llm_provider

        # A test run may simulate when nothing is configured (provider None);
        # a configured but broken tenant BYOK still fails it.
        provider: Any = await step_llm_provider(
            resolver=self.llm_provider_resolver,
            fallback=self.llm_provider,
            state=state,
            step_id=self.step.id,
            required=not state.get("is_test_run"),
        )

        # Optional RAG context injection
        rag_context = ""
        if self.step.rag and self.knowledge_store:
            try:
                results = await self.knowledge_store.retrieve(
                    query=str(prompt_text),
                    collection_name=self.step.rag.collection,
                    top_k=self.step.rag.top_k,
                    tenant_id=state.get("tenant_id", ""),
                    # The chat provider also embeds (NVIDIA/OpenAI-compatible),
                    # so the query is embedded for semantic retrieval; without it
                    # retrieve() degrades to lexical rather than silently failing.
                    embedder=provider,
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

        if provider is None:
            # Old bug: a REAL run with no provider wired returned the placeholder
            # "[FakeProvider: <id>]" as if the model had answered, so downstream
            # steps consumed fabricated output. Only test/simulation runs may
            # simulate; a real run fails the step (on_failure policy applies).
            if not state.get("is_test_run"):
                raise StepServiceUnavailableError(
                    f"llm step {self.step.id!r}: no LLM provider is configured"
                )
            output = {
                "result": f"[simulated LLM output: {self.step.id}]",
                "confidence": 1.0,
                "_simulated": True,
            }
        else:
            from app.guardrails_v2.models import GuardrailLayer
            from app.providers.base import CompletionRequest, Message
            from app.workflow.guardrails import interpolated_text, screen_step_content

            # P8b-2: input guardrails (tenant rules + baseline, step layer) on
            # the prompt before it leaves for the model. Injection rules look
            # only at the untrusted part: interpolated values + retrieved context.
            full_prompt = await screen_step_content(
                full_prompt,
                layer=GuardrailLayer.STEP,
                state=state,
                step_id=self.step.id,
                step_type=self.step.type,
                direction="prompt",
                untrusted="\n".join(
                    filter(None, (interpolated_text(self.step.prompt, prompt_text), rag_context))
                ),
            )
            req = CompletionRequest(
                messages=[
                    Message(
                        role="system",
                        content="You are a workflow step executor. Return JSON only.",
                    ),
                    Message(role="user", content=full_prompt),
                ],
                # Use the step's model, else the system-configured model — never a
                # hardcoded cloud slug that a self-hosted/NVIDIA endpoint 404s on.
                # A tenant's own provider (BYOK) uses the tenant's configured model
                # ("" → its default_model), never the platform's slug.
                model=self.step.model or _default_model_for(provider),
                temperature=self.step.temperature,
                max_tokens=self.step.max_tokens,
                # JSON-object mode for structured steps → clean JSON out even from
                # a reasoning model that would otherwise emit a prose preamble.
                json_object=self.step.json_output,
            )
            from app.providers.guarded_completion import (
                complete_decision,
                generation_timeout_seconds,
            )

            # Charged to the run's tenant (under the run id, so the per-run cap
            # pools this run's steps only) and circuit-broken with a bounded
            # timeout. A budget refusal raises and fails the step (on_failure
            # policy applies), like any other provider error.
            run_id = str(state.get("run_id") or "")
            response = await complete_decision(
                provider,
                req,
                role="workflow_llm_step",
                tenant_id=str(state.get("tenant_id") or "") or None,
                goal_id=f"workflow:{run_id}" if run_id else None,
                timeout_seconds=generation_timeout_seconds(),
            )
            raw_text = response.content.strip()
            # P8b-2: output guardrails (tool_output layer) before the answer is
            # parsed, stored or handed to a later step: block fails the step,
            # redact keeps the redacted text.
            raw_text = await screen_step_content(
                raw_text,
                layer=GuardrailLayer.TOOL_OUTPUT,
                state=state,
                step_id=self.step.id,
                step_type=self.step.type,
                direction="output",
            )
            # CompletionResponse exposes input_tokens/output_tokens (and a `usage`
            # object); the older prompt_tokens/completion_tokens names don't exist
            # on it, so reading those always yielded 0 tokens. Prefer the real
            # fields, falling back to usage.* then the legacy names.
            _usage = getattr(response, "usage", None)
            tokens_in = (
                getattr(response, "input_tokens", 0)
                or getattr(_usage, "prompt_tokens", 0)
                or getattr(response, "prompt_tokens", 0)
            )
            tokens_out = (
                getattr(response, "output_tokens", 0)
                or getattr(_usage, "completion_tokens", 0)
                or getattr(response, "completion_tokens", 0)
            )
            cost_usd = getattr(response, "cost_usd", 0.0) or getattr(_usage, "cost_usd", 0.0)

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
            # Return the DELTA this step incurred; the WorkflowState reducer sums
            # deltas so concurrent steps are all accounted (see state._add).
            "cost_usd": cost_usd,
            "tokens_used": tokens_in + tokens_out,
        }
