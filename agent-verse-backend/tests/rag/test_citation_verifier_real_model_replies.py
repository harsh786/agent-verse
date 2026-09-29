"""Citation entailment against replies real models actually produced.

Seen in the real-provider KB e2e (tests/e2e_full/test_knowledge_real_documents_e2e.py):
* on-prem Qwen3.5-4B, json_object mode, no schema in the prompt → invented keys
  ``{"is_entailed": true}`` → every grounded answer rejected as verifier_failure;
* NVIDIA nemotron-3-super (reasoning) with max_tokens=100 → truncated before
  any JSON; with a real budget it emits thought and then the JSON verdict.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.providers.base import CompletionRequest, CompletionResponse
from app.rag.contracts import RAGCitation
from app.rag_platform.retriever import MinimalCitationVerifier

_EVIDENCE = "Meal expenses on business travel are reimbursed up to INR 1,500 per day."
_ANSWER = "Business-travel meals are reimbursed up to INR 1,500 per day [1]."


class _Provider:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> Any:
        self.requests.append(request)
        return CompletionResponse(
            content=self.reply, model="m", input_tokens=1, output_tokens=1, stop_reason="stop"
        )


def _citations() -> list[RAGCitation]:
    return [RAGCitation(citation_id="1", chunk_id="c1", content=_EVIDENCE, score=1.0, source="h")]


async def _verify(reply: str) -> tuple[Any, _Provider]:
    provider = _Provider(reply)
    result = await MinimalCitationVerifier(provider=provider, model="m").verify(
        _ANSWER, _citations()
    )
    return result, provider


@pytest.mark.asyncio
async def test_prompt_names_the_fields_and_leaves_room_to_reason() -> None:
    result, provider = await _verify('{"supported": true, "reason": "entailed"}')
    assert result.grounded is True
    request = provider.requests[0]
    assert request.max_tokens >= 512
    prompt = request.messages[0].content
    assert '"supported"' in prompt and '"reason"' in prompt and "not_entailed" in prompt


@pytest.mark.asyncio
async def test_inline_think_block_before_the_verdict_is_accepted() -> None:
    result, _ = await _verify(
        '<think>{draft} compare the numbers</think>{"supported": true, "reason": "entailed"}'
    )
    assert result.grounded is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        '{"is_entailed": true}',
        '{"{"',
        "yes",
        # Prose around the object stays rejected: it may carry another verdict.
        'Not supported, though {"supported": true, "reason": "entailed"}',
    ],
)
async def test_wrong_shape_or_garbage_still_fails_closed(reply: str) -> None:
    result, _ = await _verify(reply)
    assert result.grounded is False
    assert result.reason == "verifier_failure"
