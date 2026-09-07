"""WT-5: ScheduleStore.update_secret_async persists a rotated webhook secret."""

from __future__ import annotations

from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore


async def test_update_secret_rotates_and_retains_previous():
    """FAILS TODAY: ScheduleStore has no update_secret_async (rotation never persists)."""
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, webhook_signature_secret="old-secret")
    store._data[("t1", "tr1")] = {
        "spec": spec,
        "schedule_id": "tr1",
        "goal_id": "g1",
    }

    updated = await store.update_secret_async(
        "tr1", new_secret="new-secret", tenant_id="t1", grace_period_seconds=120
    )
    assert updated is True

    rec = store._data[("t1", "tr1")]
    assert rec["spec"].webhook_signature_secret == "new-secret"
    assert rec["previous_webhook_secret"] == "old-secret"
    assert rec["secret_grace_until"] > 0


async def test_update_secret_missing_record_returns_false():
    store = ScheduleStore()
    assert (
        await store.update_secret_async("nope", new_secret="x", tenant_id="t1") is False
    )
