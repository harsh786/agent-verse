"""Phase 0.4 — the promised fast-LLM intent fallback (was documented but absent).

The regex classifier stays the fast path; the LLM is consulted ONLY when the
regex path fell through to its default QA bucket with no positive signal. Clear
QA/GOAL/SCHEDULE messages never hit the LLM.
"""

from __future__ import annotations

from typing import Any

from app.chat.intent import Intent, IntentRouter
from app.providers.fake import FakeProvider


class _BoomLLM:
    """An LLM that fails the test if it is ever called."""

    async def complete(self, request: Any) -> Any:
        raise AssertionError("LLM must NOT be consulted for an unambiguous message")


async def test_unambiguous_messages_never_consult_the_llm() -> None:
    router = IntentRouter()
    boom = _BoomLLM()
    assert await router.classify_async("what is the capital of France?", llm=boom) == Intent.QA
    assert (
        await router.classify_async("every monday at 9am email me the report", llm=boom)
        == Intent.SCHEDULE
    )
    assert await router.classify_async("deploy the staging build now", llm=boom) == Intent.GOAL


async def test_ambiguous_message_is_disambiguated_by_the_llm() -> None:
    router = IntentRouter()
    # No schedule word, not a question, no action verb, >4 words → regex default QA,
    # i.e. ambiguous. The LLM decides it's actually a GOAL.
    ambiguous = "the quarterly numbers for the board deck by friday"
    llm = FakeProvider(responses=['{"intent": "goal"}'])
    assert await router.classify_async(ambiguous, llm=llm) == Intent.GOAL


async def test_ambiguous_without_llm_falls_back_to_regex_default() -> None:
    router = IntentRouter()
    ambiguous = "the quarterly numbers for the board deck by friday"
    # No LLM wired → keep the regex default (QA), never crash.
    assert await router.classify_async(ambiguous) == Intent.QA


async def test_llm_bad_output_keeps_regex_result() -> None:
    router = IntentRouter()
    ambiguous = "the quarterly numbers for the board deck by friday"
    llm = FakeProvider(responses=["not json at all"])
    # Unparseable LLM output must not crash; fall back to the regex intent.
    assert await router.classify_async(ambiguous, llm=llm) == Intent.QA
