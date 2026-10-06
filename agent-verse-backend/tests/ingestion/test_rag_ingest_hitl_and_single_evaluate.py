"""a03-F063-06: the RAG_INGEST screen honours REQUIRE_HITL and evaluates once.

* A REQUIRE_HITL rule was ignored (only ``blocked`` / ``redacted_content`` were
  acted on), so the document was indexed. It is now withheld for review.
* A redacting rule triggered a second ``evaluate()`` on the PII-redacted text,
  which recorded every violation twice and charged LLM-judge rules twice.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.guardrails_v2.engine import guardrails_engine
from app.ingestion.pipeline import (
    IngestionPipeline,
    IngestionPolicyRejectedError,
    screen_ingest_text,
)

_TENANT = "tid-rag-ingest-hitl"
_TEXT = "Contact jane.doe@example.com about the quarterly key rotation runbook."


def _fake_evaluate(result: dict[str, Any], calls: list[str]) -> Any:
    async def _evaluate(*_a: Any, content: str = "", **_k: Any) -> dict[str, Any]:
        calls.append(content)
        return dict(result)

    return _evaluate


async def test_require_hitl_withholds_the_document(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        guardrails_engine,
        "evaluate",
        _fake_evaluate({"blocked": False, "hitl_required": True, "redacted_content": _TEXT}, calls),
    )
    screened = await IngestionPipeline().screen_text(_TEXT, tenant_id=_TENANT, doc_id="d1")
    assert screened.blocked_reason == "guardrail_review_required"
    assert screened.text == ""
    with pytest.raises(IngestionPolicyRejectedError):
        await screen_ingest_text(_TEXT, tenant_id=_TENANT)


async def test_redacting_rule_is_evaluated_once_and_keeps_the_pii_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        guardrails_engine,
        "evaluate",
        _fake_evaluate(
            {
                "blocked": False,
                "hitl_required": False,
                "redacted_content": guardrails_engine.redact_text(_TEXT),
            },
            calls,
        ),
    )
    pipeline = IngestionPipeline()
    pii_redacted = _TEXT.replace("jane.doe@example.com", "<EMAIL>")
    monkeypatch.setattr(pipeline, "_run_pii", lambda text, action: (pii_redacted, True))
    screened = await pipeline.screen_text(_TEXT, tenant_id=_TENANT, doc_id="d2")
    assert calls == [_TEXT]  # one evaluation, of the original text
    assert screened.blocked_reason == ""
    assert screened.pii_detected is True
    assert "<EMAIL>" in screened.text  # Stage 6's redaction is not undone
    assert screened.text == guardrails_engine.redact_text(pii_redacted)


async def test_no_redaction_keeps_the_pii_redacted_text_without_re_evaluating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        guardrails_engine,
        "evaluate",
        _fake_evaluate(
            {"blocked": False, "hitl_required": False, "redacted_content": _TEXT}, calls
        ),
    )
    pipeline = IngestionPipeline()
    pii_redacted = _TEXT.replace("jane.doe@example.com", "<EMAIL>")
    monkeypatch.setattr(pipeline, "_run_pii", lambda text, action: (pii_redacted, True))
    screened = await pipeline.screen_text(_TEXT, tenant_id=_TENANT, doc_id="d3")
    assert calls == [_TEXT]
    assert screened.text == pii_redacted
