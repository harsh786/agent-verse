"""WF-35: the workflow HTTP/OCR egress guard is the central app.net.ssrf_guard.

The old workflow-local ``SSRFGuard`` let IPv4-mapped IPv6 literals
(``[::ffff:169.254.169.254]``), CGNAT cloud-metadata addresses
(``100.100.100.200``, Alibaba) and the unspecified address through, and it
re-resolved DNS separately from the request (rebinding).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

import app.net.ssrf_guard as central
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.security import SSRFBlockedError, SSRFGuard

_BYPASSES = [
    "http://[::ffff:169.254.169.254]/latest/meta-data/",
    "http://[::ffff:127.0.0.1]:8080/",
    "http://100.100.100.200/latest/meta-data/",
    "http://0.0.0.0/",
    "http://[::]/",
    "http://[::ffff:a9fe:a9fe]/",
]


@pytest.mark.parametrize("url", _BYPASSES)
def test_guard_rejects_mapped_cgnat_and_unspecified(url: str) -> None:
    with pytest.raises(SSRFBlockedError):
        SSRFGuard().validate(url)


def test_guard_rejects_non_http_scheme() -> None:
    with pytest.raises(SSRFBlockedError):
        SSRFGuard().validate("file:///etc/passwd")


def test_guard_delegates_to_central_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _fake(url: str, **_: Any) -> list[str]:
        seen.append(url)
        return ["93.184.216.34"]

    monkeypatch.setattr(central, "assert_public_url", _fake)
    SSRFGuard().validate("https://example.com/x")
    assert seen == ["https://example.com/x"]


def _state() -> dict[str, Any]:
    # A real run always carries its tenant (the HTTP step's guardrail check,
    # P8b-2, fails closed without one).
    return {
        "step_outputs": {}, "vars": {}, "inputs": {}, "is_test_run": False,
        "tenant_id": "t-egress",
    }


@pytest.mark.parametrize("url", _BYPASSES[:3])
@pytest.mark.asyncio
async def test_http_step_refuses_bypass_urls(url: str) -> None:
    from app.workflow.steps.http_step import HTTPStepNode

    node = HTTPStepNode(StepDefinition(id="h", type="http", url=url), ContextResolver())
    with pytest.raises(SSRFBlockedError):
        await node.execute(_state())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_http_step_dns_rebinding_is_refused_at_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public at validation time, private at connect time -> refused, never dialled."""
    answers = iter([["93.184.216.34"], ["169.254.169.254"]])
    monkeypatch.setattr(central, "_resolve_host", lambda h: next(answers))
    dialled = AsyncMock()
    monkeypatch.setattr("httpcore.AnyIOBackend.connect_tcp", dialled)

    from app.workflow.steps.http_step import HTTPStepNode

    node = HTTPStepNode(
        StepDefinition(id="h", type="http", url="http://rebind.example/x"), ContextResolver()
    )
    with pytest.raises(SSRFBlockedError):
        await node.execute(_state())  # type: ignore[arg-type]
    dialled.assert_not_called()


@pytest.mark.asyncio
async def test_ocr_fetch_refuses_mapped_metadata() -> None:
    from app.workflow.steps.ocr_step import OcrStepNode

    data, _ct, _name = await OcrStepNode._fetch_url_bytes(
        "http://[::ffff:169.254.169.254]/doc.pdf", None
    )
    assert data is None


@pytest.mark.asyncio
async def test_ocr_fetch_dns_rebinding_is_refused_at_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    monkeypatch.setattr(central, "_resolve_host", lambda h: next(answers))
    dialled = AsyncMock()
    monkeypatch.setattr("httpcore.AnyIOBackend.connect_tcp", dialled)
    from app.workflow.steps.ocr_step import OcrStepNode

    data, _ct, _name = await OcrStepNode._fetch_url_bytes("http://rebind.example/a.pdf", None)
    assert data is None
    dialled.assert_not_called()
