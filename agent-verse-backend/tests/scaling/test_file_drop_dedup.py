"""file_drop schedules must derive a stable goal id from the FILE, not the clock.

`fire_due_schedules`' FILE_DROP branch built its fire instance as
``f"filedrop:{file_name}:{now.isoformat()}"``, where ``now`` is the evaluating
beat tick's wall clock. Two ticks — or two replicas — that both observe the same
new file compute DIFFERENT instance ids, so the derived goal id differs too and
nothing downstream can dedup them: the same dropped file is submitted twice.

That is the same recurring-key defect already fixed for interval schedules. The
file itself is the occurrence, so the id must be derived from it alone.

The branch also read/modified/wrote a `processed_files` JSON blob in Redis
(GET, compute, SET at end of cycle), which two ticks race on; a per-file
`SET NX` claim now settles that atomically.
"""

from __future__ import annotations

from app.scaling.tasks import _scheduled_goal_id


def test_file_drop_goal_id_is_stable_for_the_same_file() -> None:
    a = _scheduled_goal_id("sched-1", fire_instance_id="filedrop:invoice.csv")
    b = _scheduled_goal_id("sched-1", fire_instance_id="filedrop:invoice.csv")
    assert a == b, "same file must map to the same goal id across ticks"


def test_file_drop_goal_id_differs_per_file() -> None:
    a = _scheduled_goal_id("sched-1", fire_instance_id="filedrop:invoice.csv")
    b = _scheduled_goal_id("sched-1", fire_instance_id="filedrop:receipt.csv")
    assert a != b


def test_file_drop_goal_id_differs_per_schedule() -> None:
    a = _scheduled_goal_id("sched-1", fire_instance_id="filedrop:invoice.csv")
    b = _scheduled_goal_id("sched-2", fire_instance_id="filedrop:invoice.csv")
    assert a != b


def test_a_clock_derived_instance_id_would_not_be_stable() -> None:
    """Guards the regression itself: the old shape cannot dedup.

    Two ticks a millisecond apart produced different ids for the same file,
    which is exactly why the duplicate got through.
    """
    a = _scheduled_goal_id(
        "sched-1", fire_instance_id="filedrop:invoice.csv:2026-01-01T00:00:00.000000+00:00"
    )
    b = _scheduled_goal_id(
        "sched-1", fire_instance_id="filedrop:invoice.csv:2026-01-01T00:00:00.000001+00:00"
    )
    assert a != b
