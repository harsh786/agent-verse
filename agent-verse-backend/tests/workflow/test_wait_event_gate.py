"""2.W-8: the ``wait`` step's event-channel path must actually subscribe to
Redis and resume on the published event — not return immediately.

The old ``WaitStepNode`` returned ``{"waited_channel": channel}`` without ever
subscribing, so an event-gated wait never blocked and never carried the event.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.workflow.dsl import StepDefinition
from app.workflow.steps.wait_step import WaitStepNode

pytestmark = pytest.mark.asyncio


class _FakePubSub:
    def __init__(self, messages: list[dict[str, Any]], delay: float = 0.0) -> None:
        self._messages = messages
        self._delay = delay

    async def __aenter__(self) -> _FakePubSub:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def subscribe(self, channel: str) -> None:
        self.channel = channel

    async def listen(self) -> Any:
        for m in self._messages:
            if self._delay:
                await asyncio.sleep(self._delay)
            else:
                await asyncio.sleep(0)
            yield m


class _FakeRedis:
    def __init__(self, messages: list[dict[str, Any]], delay: float = 0.0) -> None:
        self._messages = messages
        self._delay = delay

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self._messages, self._delay)


def _wait_node(redis: Any) -> WaitStepNode:
    ctx = MagicMock()
    ctx.resolve.side_effect = lambda expr, state: expr
    step = StepDefinition(id="w1", type="wait", event_channel="orders:paid")
    return WaitStepNode(step, ctx, redis=redis)


async def test_wait_resumes_on_published_event() -> None:
    msg = {"type": "message", "data": json.dumps({"order": 42})}
    node = _wait_node(_FakeRedis([msg]))
    result = await node.execute({})
    out = result["step_outputs"]["w1"]
    assert out["waited_channel"] == "orders:paid"
    assert out["event"] == {"order": 42}
    assert out["timed_out"] is False


async def test_wait_event_times_out_without_blocking_forever() -> None:
    node = _wait_node(_FakeRedis([]))  # nothing ever published
    result = await node._await_event("orders:paid", 0.05)
    assert result is None


async def test_test_run_short_circuits_without_redis() -> None:
    node = _wait_node(_FakeRedis([]))
    result = await node.execute({"is_test_run": True})
    assert result["step_outputs"]["w1"]["waited"] is True
