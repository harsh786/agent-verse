"""System messages must be merged and placed first (strict chat-template compat)."""
from __future__ import annotations

from app.providers.base import CompletionRequest, Message
from app.providers.openai_compatible import OpenAICompatibleProvider as P


def _msgs(**kw):
    return P._normalize_messages(CompletionRequest(**kw))


def test_system_message_moved_to_front():
    out = _msgs(
        model="m",
        messages=[
            Message(role="user", content="hi"),
            Message(role="system", content="be terse"),
        ],
    )
    assert out[0] == {"role": "system", "content": "be terse"}
    assert out[1] == {"role": "user", "content": "hi"}


def test_multiple_system_messages_merged_into_one():
    out = _msgs(
        model="m",
        system="global rule",
        messages=[
            Message(role="system", content="rule A"),
            Message(role="user", content="q"),
            Message(role="system", content="rule B"),
        ],
    )
    assert [m["role"] for m in out] == ["system", "user"]
    assert out[0]["content"] == "global rule\n\nrule A\n\nrule B"


def test_no_system_is_unchanged_order():
    out = _msgs(model="m", messages=[Message(role="user", content="a"), Message(role="assistant", content="b")])
    assert [m["role"] for m in out] == ["user", "assistant"]
