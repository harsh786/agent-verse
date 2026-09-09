"""Tests for SSE stream generators — 17 cases."""

from __future__ import annotations

import json

import pytest

from app.chat.stream import (
    stream_artifact_created,
    stream_clarify,
    stream_goal_progress,
    stream_hitl,
    stream_qa_response,
    stream_schedule_created,
)


def _parse_events(raw: list[str]) -> list[dict]:
    events = []
    for line in raw:
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


async def _collect(gen) -> list[str]:
    return [chunk async for chunk in gen]


# ── QA streaming ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_qa_stream_emits_typing_started() -> None:
    chunks = await _collect(stream_qa_response("s1", "m1", ["hello"]))
    events = _parse_events(chunks)
    types = [e["type"] for e in events]
    assert "typing_started" in types


@pytest.mark.asyncio
async def test_qa_stream_emits_routing_event() -> None:
    chunks = await _collect(stream_qa_response("s1", "m1", ["hello"]))
    events = _parse_events(chunks)
    routing = next(e for e in events if e["type"] == "routing")
    assert routing["intent"] == "QA"


@pytest.mark.asyncio
async def test_qa_stream_emits_token_events() -> None:
    tokens = ["Hello", " ", "world"]
    chunks = await _collect(stream_qa_response("s1", "m1", tokens))
    events = _parse_events(chunks)
    token_events = [e for e in events if e["type"] == "token"]
    assert len(token_events) == 3


@pytest.mark.asyncio
async def test_qa_stream_emits_done() -> None:
    chunks = await _collect(stream_qa_response("s1", "m1", ["hi"]))
    events = _parse_events(chunks)
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_qa_stream_emits_usage_when_provided() -> None:
    usage = {"tokens_in": 10, "tokens_out": 20, "cost_usd": 0.001, "model": "gpt-4o"}
    chunks = await _collect(stream_qa_response("s1", "m1", ["hi"], usage=usage))
    events = _parse_events(chunks)
    usage_events = [e for e in events if e["type"] == "usage"]
    assert len(usage_events) == 1
    assert usage_events[0]["tokens_out"] == 20


@pytest.mark.asyncio
async def test_qa_stream_emits_reasoning_when_enabled() -> None:
    chunks = await _collect(
        stream_qa_response("s1", "m1", ["answer"], reasoning_tokens=["think"], show_reasoning=True)
    )
    events = _parse_events(chunks)
    reasoning_events = [e for e in events if e["type"] == "reasoning"]
    assert len(reasoning_events) == 1


@pytest.mark.asyncio
async def test_qa_stream_no_reasoning_by_default() -> None:
    chunks = await _collect(
        stream_qa_response("s1", "m1", ["answer"], reasoning_tokens=["think"], show_reasoning=False)
    )
    events = _parse_events(chunks)
    reasoning_events = [e for e in events if e["type"] == "reasoning"]
    assert len(reasoning_events) == 0


# ── Goal streaming ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_goal_stream_emits_step_events() -> None:
    steps = [{"name": "plan", "result": "done"}, {"name": "execute", "result": "ok"}]
    chunks = await _collect(stream_goal_progress("s1", "m1", "g1", steps))
    events = _parse_events(chunks)
    started = [e for e in events if e["type"] == "step_started"]
    completed = [e for e in events if e["type"] == "step_complete"]
    assert len(started) == 2
    assert len(completed) == 2


@pytest.mark.asyncio
async def test_goal_stream_emits_tool_calls() -> None:
    steps = [{"name": "execute", "result": "ok", "tool_calls": ["bash", "python"]}]
    chunks = await _collect(stream_goal_progress("s1", "m1", "g1", steps))
    events = _parse_events(chunks)
    tool_events = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_events) == 2


@pytest.mark.asyncio
async def test_goal_stream_emits_failure_analysis() -> None:
    chunks = await _collect(
        stream_goal_progress("s1", "m1", "g1", [], outcome="failure", failure_reason="Timeout")
    )
    events = _parse_events(chunks)
    failure = next(e for e in events if e["type"] == "failure_analysis")
    assert failure["reason"] == "Timeout"


@pytest.mark.asyncio
async def test_goal_stream_emits_proactive_suggestions() -> None:
    chunks = await _collect(
        stream_goal_progress("s1", "m1", "g1", [], suggestions=["Try X", "Try Y"])
    )
    events = _parse_events(chunks)
    sugg = next(e for e in events if e["type"] == "proactive_suggestions")
    assert "Try X" in sugg["suggestions"]


# ── Clarify streaming ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clarify_stream_emits_clarify_needed() -> None:
    chunks = await _collect(stream_clarify("s1", "m1", "Which env?", ["Dev", "Prod"]))
    events = _parse_events(chunks)
    clarify = next(e for e in events if e["type"] == "clarify_needed")
    assert clarify["question"] == "Which env?"
    assert "Dev" in clarify["options"]


# ── HITL streaming ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hitl_stream_emits_hitl_required() -> None:
    chunks = await _collect(stream_hitl("s1", "m1", "g1", "delete_prod"))
    events = _parse_events(chunks)
    hitl = next(e for e in events if e["type"] == "hitl_required")
    assert hitl["step"] == "delete_prod"
    assert "approval_token" in hitl


# ── Schedule streaming ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_created_event() -> None:
    chunks = await _collect(stream_schedule_created("s1", "m1", "0 9 * * *", "every day at 9 AM"))
    events = _parse_events(chunks)
    sched = next(e for e in events if e["type"] == "schedule_created")
    assert sched["cron_expression"] == "0 9 * * *"


# ── Artifact streaming ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_artifact_created_event() -> None:
    chunks = await _collect(stream_artifact_created("s1", "m1", "a1", "hello.py", "python"))
    events = _parse_events(chunks)
    art = next(e for e in events if e["type"] == "artifact_created")
    assert art["title"] == "hello.py"
    assert art["language"] == "python"


# ── Typing indicator ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_typing_started_is_first_event() -> None:
    """typing_started must be first so UI can show indicator within 100ms."""
    chunks = await _collect(stream_qa_response("s1", "m1", ["token"]))
    events = _parse_events(chunks)
    assert events[0]["type"] == "typing_started"
