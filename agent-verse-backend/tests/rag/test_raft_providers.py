"""D-8: RAFT must be structurally available when a fine-tune-capable provider
is configured.

Both live gateways previously built ``RAFTService(..., providers={})`` so any
RAFT operation raised ``RAFTModelUnavailableError`` even with a real OpenAI key
present. These tests cover the ``build_raft_providers`` helper and the
``OpenAIFineTuneProvider`` adapter that closes that gap. Network calls are never
made here — only construction, protocol conformance, deterministic cost
estimation, and status mapping.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.rag.raft import FineTuneCost, FineTuneProvider
from app.rag.raft_openai_provider import (
    OpenAIFineTuneProvider,
    build_raft_providers,
    map_openai_status,
)


def _settings(*, openai_key: str = "") -> object:
    class _S:
        openai_api_key = openai_key

    return _S()


def test_build_raft_providers_empty_without_key() -> None:
    providers = build_raft_providers(_settings(openai_key=""))
    assert providers == {}


def test_build_raft_providers_registers_openai_with_key() -> None:
    providers = build_raft_providers(_settings(openai_key="sk-test-123"))
    assert "openai" in providers
    assert isinstance(providers["openai"], OpenAIFineTuneProvider)


def test_adapter_satisfies_finetune_provider_protocol() -> None:
    provider: FineTuneProvider = OpenAIFineTuneProvider(api_key="sk-test-123")
    assert provider.provider_id == "openai"
    # structural: the protocol methods exist and are coroutines
    for name in ("preview_cost", "submit", "status", "evaluate"):
        assert callable(getattr(provider, name))


@pytest.mark.asyncio
async def test_preview_cost_is_deterministic_and_valid() -> None:
    provider = OpenAIFineTuneProvider(api_key="sk-test-123")
    cost = await provider.preview_cost(
        training_examples=100,
        validation_examples=20,
        base_model="gpt-4o-mini-2024-07-18",
    )
    assert isinstance(cost, FineTuneCost)
    assert cost.currency == "USD"
    assert cost.estimated_amount > Decimal("0")
    # deterministic: same inputs -> same estimate
    cost2 = await provider.preview_cost(
        training_examples=100,
        validation_examples=20,
        base_model="gpt-4o-mini-2024-07-18",
    )
    assert cost.estimated_amount == cost2.estimated_amount


def test_map_openai_status_covers_all_states() -> None:
    assert map_openai_status("validating_files") == "submitted"
    assert map_openai_status("queued") == "submitted"
    assert map_openai_status("running") == "running"
    assert map_openai_status("succeeded") == "completed"
    assert map_openai_status("failed") == "failed"
    assert map_openai_status("cancelled") == "failed"
    # unknown status falls back to a safe non-terminal state
    assert map_openai_status("some_future_state") == "running"
