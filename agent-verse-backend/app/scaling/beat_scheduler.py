"""The beat's scheduler: RedBeat (one active beat across replicas) that never
waits for task results (B1-14).

Live (2026-10-06): the compose beat went silent for 5-15 minutes at a time,
then crashed with ``LockNotOwnedError`` (its RedBeat lock had expired) and was
restarted, so no time trigger fired in those windows. Two causes are closed:

* Every task the beat sent subscribed the beat to that task's result channel
  (Celery's Redis result backend), although the beat never reads a result: the
  beat's result connection kept a growing unread backlog (229 KB observed).
  Beat-sent tasks are now sent with ``ignore_result`` (the workers still
  store results for anyone else).
* The beat could sleep for its whole ``max_interval`` (300 s), the same as the
  RedBeat lock timeout (300 s), so waking up late lost the lock. The beat now
  wakes at least every ``beat_max_loop_interval`` (30 s, see celery_app), well
  inside the lock.
"""

from __future__ import annotations

from typing import Any

from redbeat import RedBeatScheduler  # type: ignore[import-untyped]


class AgentVerseRedBeatScheduler(RedBeatScheduler):  # type: ignore[misc]
    def apply_async(self, entry: Any, producer: Any = None, advance: bool = True,
                    **kwargs: Any) -> Any:
        options = dict(getattr(entry, "options", None) or {})
        if not options.get("ignore_result"):
            options["ignore_result"] = True
            entry.options = options
        return super().apply_async(entry, producer=producer, advance=advance, **kwargs)
