"""Regression: the hosted reranker connects to the address it validated.

``HostedReranker.rerank`` checked the endpoint with ``assert_public_url`` and
then POSTed with a plain ``httpx.AsyncClient`` (a second DNS lookup). An
operator-trusted LAN endpoint (``allow_internal=True``) is pinned too, with its
own host as the allowlist — private ranges open, cloud metadata still refused.
"""

from __future__ import annotations

import pytest

from app.rag_platform.hosted_reranker import HostedReranker, HostedRerankerError
from tests._pinning import install_connect_spy


@pytest.mark.asyncio
async def test_public_endpoint_connects_via_pinned_client(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = install_connect_spy(monkeypatch)
    rr = HostedReranker(url="https://rebind.example/v1/rerank")
    with pytest.raises(HostedRerankerError):
        await rr.rerank("q", ["a", "b"])
    assert spy.dialed == ["rebind.example"]
    assert spy.allowlists == [None]


@pytest.mark.asyncio
async def test_trusted_internal_endpoint_is_pinned_with_its_own_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = install_connect_spy(monkeypatch)
    rr = HostedReranker(url="http://reranker.lan:8080/v1/rerank", allow_internal=True)
    with pytest.raises(HostedRerankerError):
        await rr.rerank("q", ["a"])
    assert spy.dialed == ["reranker.lan"]
    assert spy.allowlists == [["reranker.lan"]]
