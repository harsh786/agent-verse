from __future__ import annotations

import pytest

from app.agent.patterns.constitutional_ai import ConstitutionalAIRuntime
from app.policy_runtime.constraint_model import RuntimeConstraints


def _constraints(allowed: list[str]) -> RuntimeConstraints:
    return RuntimeConstraints(
        allowed_capabilities=allowed,
        denied_capabilities=[],
        required_approvals=[],
        max_cost_usd=1,
        audit_level="full",
    )


@pytest.mark.asyncio
async def test_constitutional_ai_is_bounded_and_returns_safe_artifacts() -> None:
    calls: list[str] = []
    result = await ConstitutionalAIRuntime().revise(
        original_safe_summary="draft",
        requested_capability="model:completion",
        constraints=_constraints(["model:completion"]),
        principle_ids=("honesty",),
        critique=lambda *_: calls.append("critique") or "needs evidence",
        revision=lambda *_: calls.append("revision") or "evidence-backed answer",
    )
    assert result.model_calls == 2 and calls == ["critique", "revision"]
    assert result.principle_ids == ("honesty",)


@pytest.mark.asyncio
async def test_revision_cannot_override_deterministic_denial() -> None:
    with pytest.raises(PermissionError):
        await ConstitutionalAIRuntime().revise(
            original_safe_summary="draft",
            requested_capability="tool:shell",
            constraints=_constraints([]),
            principle_ids=("safety",),
            critique=lambda *_: pytest.fail("model must not be called"),
            revision=lambda *_: pytest.fail("model must not be called"),
        )
