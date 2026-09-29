"""Regression: the outbound A2A call tool is not open to DNS rebinding.

``call_external_a2a_agent`` checked the endpoint with ``assert_public_url`` and
then POSTed with a plain ``httpx.AsyncClient``, which resolved the name again.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import app.net.ssrf_guard as g
from app.agent.tools.a2a_call import call_external_a2a_agent
from tests._pinning import install_connect_spy


@pytest.mark.asyncio
async def test_rebinding_answer_at_connect_time_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First resolution (the up-front check) is public; the second (at connect
    # time) is loopback — a rebinding DNS server with a 0s TTL.
    answers: Iterator[list[str]] = iter([["93.184.216.34"], ["127.0.0.1"]])
    monkeypatch.setattr(g, "_resolve_host", lambda host: next(answers))

    result = await call_external_a2a_agent(
        agent_endpoint="https://rebind.example/a2a", task_description="hello"
    )

    assert result["status"] == "error"
    assert "blocked IP '127.0.0.1'" in result["output"]


@pytest.mark.asyncio
async def test_a2a_call_connects_via_pinned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = install_connect_spy(monkeypatch)
    result = await call_external_a2a_agent(
        agent_endpoint="https://rebind.example/a2a", task_description="hello"
    )
    assert result["status"] == "error"
    assert spy.dialed == ["rebind.example"]


@pytest.mark.asyncio
async def test_a2a_call_does_not_follow_redirects_to_internal_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    import respx

    monkeypatch.setattr(g, "_resolve_host", lambda host: ["93.184.216.34"])
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://agent.example/a2a").mock(
            return_value=httpx.Response(307, headers={"location": "http://169.254.169.254/"})
        )
        internal = mock.post("http://169.254.169.254/").mock(
            return_value=httpx.Response(200, json={"output": "secret"})
        )
        result = await call_external_a2a_agent(
            agent_endpoint="https://agent.example/a2a", task_description="hello"
        )
    assert not internal.called
    assert result["status"] == "error"
