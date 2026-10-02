"""ORG-38: accepted handoffs expire when the target crashes, and the parent resumes.

EXPIRED was only set lazily inside ``accept()``; nothing expired ACCEPTED/EXECUTING
handoffs past their deadline, and a resume that failed after the committed
transition only re-ran if the client retried -- the parent stayed paused forever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.coordination.handoffs.models import HandoffRecord, HandoffState
from app.coordination.handoffs.repository import InMemoryHandoffRepository
from tests.coordination.test_handoff_resumption import _TENANT, Runtime, _request

_TOKEN = "token-token-token"


async def _accepted(runtime: Runtime, *, executing: bool = False) -> tuple[str, str]:
    session_id = await runtime.active_session()
    service = runtime.service()
    requested = await _request(service, session_id)
    record = await service.accept(
        "tenant", requested.handoff_id, token=_TOKEN, expected_version=1,
        idempotency_key="accept",
    )
    if executing:
        record = await service.transition(
            "tenant", requested.handoff_id, target=HandoffState.EXECUTING,
            expected_version=record.version, idempotency_key="execute",
        )
    assert (await runtime.coordination.get_session(_TENANT, session_id)).state == "paused"
    return session_id, requested.handoff_id


def _make_overdue(runtime: Runtime, handoff_id: str) -> None:
    key = ("tenant", handoff_id)
    rec = runtime.repository._records[key]
    runtime.repository._records[key] = rec.model_copy(
        update={"deadline": datetime.now(UTC) - timedelta(seconds=1)}
    )


async def test_sweep_expires_an_overdue_accepted_handoff_and_resumes_the_parent() -> None:
    runtime = Runtime()
    session_id, handoff_id = await _accepted(runtime)
    _make_overdue(runtime, handoff_id)

    stats = await runtime.service().sweep()

    assert stats["expired"] == 1
    assert (await runtime.repository.get("tenant", handoff_id)).state == HandoffState.EXPIRED
    assert (await runtime.coordination.get_session(_TENANT, session_id)).state == "active"
    transcript = await runtime.transcript.page("tenant", session_id)
    resumes = [m for m in transcript if m.message_type == "decision"]
    assert len(resumes) == 1 and "expired" in (resumes[0].safe_content or "")

    # A second sweep (or another replica's) finds nothing to do.
    again = await runtime.service().sweep()
    assert again["expired"] == 0
    assert len([m for m in await runtime.transcript.page("tenant", session_id)
                if m.message_type == "decision"]) == 1


async def test_sweep_expires_an_executing_handoff_whose_target_crashed() -> None:
    runtime = Runtime()
    session_id, handoff_id = await _accepted(runtime, executing=True)
    _make_overdue(runtime, handoff_id)

    stats = await runtime.service().sweep()

    assert stats["expired"] == 1
    assert (await runtime.coordination.get_session(_TENANT, session_id)).state == "active"


async def test_sweep_leaves_handoffs_within_their_deadline_alone() -> None:
    runtime = Runtime()
    session_id, handoff_id = await _accepted(runtime)

    stats = await runtime.service().sweep()

    assert stats["expired"] == 0
    assert (await runtime.repository.get("tenant", handoff_id)).state == HandoffState.ACCEPTED
    assert (await runtime.coordination.get_session(_TENANT, session_id)).state == "paused"


async def test_failed_resume_is_retried_by_the_next_sweep() -> None:
    runtime = Runtime()
    session_id, handoff_id = await _accepted(runtime)
    _make_overdue(runtime, handoff_id)

    flaky = runtime.resumer()

    async def broken(_record: Any) -> None:
        raise RuntimeError("transcript store unavailable")

    flaky.resume_parent = broken  # type: ignore[method-assign]
    first = await runtime.service(flaky).sweep()
    assert first["errors"] == 1
    # The expiry committed, the resume did not: the parent is still paused.
    assert (await runtime.repository.get("tenant", handoff_id)).state == HandoffState.EXPIRED
    assert (await runtime.coordination.get_session(_TENANT, session_id)).state == "paused"

    class _Released(InMemoryHandoffRepository):
        """What the Postgres join returns: released handoffs with a paused parent."""

        async def list_released_awaiting_resume(
            self, *, since: datetime, limit: int
        ) -> list[HandoffRecord]:
            return [
                r for r in self._records.values()
                if r.state == HandoffState.EXPIRED and r.updated_at >= since
            ]

    released = _Released()
    released._records = runtime.repository._records
    runtime.repository = released
    second = await runtime.service().sweep()

    assert second["resumed"] == 1
    assert (await runtime.coordination.get_session(_TENANT, session_id)).state == "active"


def test_the_sweeper_is_on_the_beat_schedule() -> None:
    from app.coordination import outbox_tasks
    from app.scaling.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["sweep-coordination-handoffs"]
    assert entry["task"] == "agentverse.coordination.sweep_handoffs"
    assert outbox_tasks.sweep_handoffs.name == entry["task"]
