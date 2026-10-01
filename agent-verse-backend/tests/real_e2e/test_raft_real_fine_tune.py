"""KB-32 opt-in: a tiny REAL fine-tune through RAFT, then one served answer.

Billable and slow (a fine-tune queues for minutes), so it is opt-in twice:
``-m slow`` and ``RAFT_REAL_FINE_TUNE=1``. It drives whichever provider the
environment configures — OpenAI (``OPENAI_API_KEY``) or an OpenAI-compatible
vendor (``RAFT_COMPAT_FINE_TUNE_BASE_URL`` + ``RAFT_COMPAT_FINE_TUNE_API_KEY``)
— via ``RAFT_REAL_PROVIDER_ID`` and ``RAFT_REAL_BASE_MODEL``.

Run:
    REAL_PROVIDERS=1 RAFT_REAL_FINE_TUNE=1 RAFT_REAL_PROVIDER_ID=openai \\
    RAFT_REAL_BASE_MODEL=gpt-4o-mini-2024-07-18 \\
    uv run pytest tests/real_e2e/test_raft_real_fine_tune.py -m slow --no-cov -s
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.getenv("RAFT_REAL_FINE_TUNE") != "1",
        reason="billable real fine-tune: set RAFT_REAL_FINE_TUNE=1 (and provider credentials)",
    ),
]


async def test_tiny_real_fine_tune_completes_and_serves_one_answer() -> None:
    from app.core.config import Settings
    from app.rag.raft import PersistedRAFTChunk, RAFTDatasetConfig
    from app.rag.raft_wiring import build_raft_service
    from app.tenancy.context import PlanTier, TenantContext

    provider_id = os.getenv("RAFT_REAL_PROVIDER_ID", "openai")
    base_model = os.getenv("RAFT_REAL_BASE_MODEL", "gpt-4o-mini-2024-07-18")
    deadline = time.monotonic() + float(os.getenv("RAFT_REAL_TIMEOUT_S", "5400"))
    service = build_raft_service(Settings())
    assert service.has_fine_tune_providers, "no fine-tune provider configured"
    tenant = TenantContext(tenant_id="raft-real", api_key_id="k", plan=PlanTier.ENTERPRISE)
    chunks = [
        PersistedRAFTChunk(
            chunk_id=f"c{i:02d}",
            document_id=f"d{i // 3}",
            content=f"The access code for vault {i} is {1000 + i}.",
            metadata={"question": f"What is the access code for vault {i}?",
                      "answer": f"The access code for vault {i} is {1000 + i}."},
        )
        for i in range(14)  # OpenAI requires >= 10 training examples
    ]
    dataset = await service.create_dataset(
        tenant, collection_id="raft-real", chunks=chunks,
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.15, seed=3),
    )
    preview = await service.preview_job(
        tenant, dataset_id=dataset.dataset_id, provider_id=provider_id, base_model=base_model
    )
    job = await service.submit_job(
        tenant, dataset_id=dataset.dataset_id, provider_id=provider_id, base_model=base_model,
        confirmation_token=preview.confirmation_token,
    )
    while job.status not in ("completed", "failed"):
        assert time.monotonic() < deadline, f"fine-tune still {job.status}"
        await asyncio.sleep(30)
        job = await service.refresh_job(tenant, job.job_id)
    assert job.status == "completed", job.error
    assert job.fine_tuned_model
    await service.deploy_job(tenant, job.job_id)
    answer = await service.infer(
        job, query="What is the access code for vault 3?",
        evidence=("The access code for vault 3 is 1003.",),
    )
    assert "1003" in answer
