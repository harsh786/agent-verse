"""TriggerConsumerSupervisor (WT-4).

Owns the long-running trigger consumers and their asyncio task handles. On
``start()`` it builds each consumer whose dependencies are satisfied and spawns
one background task per available consumer; on ``stop()`` it cancels and awaits
every task so shutdown is graceful.

The *core* consumers (chain / HITL / memory / event / condition /
conversational) read the trigger Redis Streams through one consumer group per
consumer type (``app.triggers.bus``, TRG-18) and therefore need a
``schedule_store`` (trigger lookups), a ``dispatcher`` (governed dispatch) and a
``redis`` client. A consumer whose dependencies are
missing is skipped and recorded in :attr:`skipped` rather than crashing startup.

The only other consumer is ``GoalNotificationConsumer`` (opt-in goal outcome
notifications, a08-F196-05). The trigger types that would need an external
client (S3 events, Sheets/SharePoint, log patterns, GraphQL/WebSocket streams,
price feeds, MQTT, geofence, sensor thresholds) are UNSUPPORTED in
``app.triggers.dispatch_map`` and refused at creation. The unwired consumer and
evaluator stubs for those families, and the ``enable_extended`` flag that built
an MQTT consumer with no client, were removed (a10-F247-01).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

_log = logging.getLogger(__name__)


class _Consumer(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass
class _ConsumerSpec:
    name: str
    factory: Any  # callable returning a _Consumer
    required: dict[str, Any] = field(default_factory=dict)

    def missing_deps(self) -> list[str]:
        return [dep for dep, value in self.required.items() if value is None]


class TriggerConsumerSupervisor:
    """Start, own and gracefully stop the trigger consumer tasks."""

    def __init__(
        self,
        *,
        schedule_store: Any = None,
        dispatcher: Any = None,
        redis: Any = None,
        notification_service: Any = None,
        db_session_factory: Any = None,
        restart_backoff_s: float = 1.0,
        restart_backoff_max_s: float = 60.0,
    ) -> None:
        self._schedule_store = schedule_store
        self._dispatcher = dispatcher
        self._redis = redis
        # Opt-in goal outcome notifications (a08-F196-05) read the goal stream too.
        self._notification_service = notification_service
        self._db_session_factory = db_session_factory
        self._restart_backoff_s = restart_backoff_s
        self._restart_backoff_max_s = restart_backoff_max_s

        self.consumers: list[_Consumer] = []
        self.tasks: list[asyncio.Task[Any]] = []
        self.skipped: list[tuple[str, str]] = []
        self._started = False
        self._stopping = False
        # Per-consumer health: state (running | restarting | stopped) + restarts.
        self._health: dict[str, dict[str, Any]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Build available consumers and spawn one task per consumer."""
        if self._started:
            return
        self._started = True
        self._stopping = False

        for spec in self._consumer_specs():
            missing = spec.missing_deps()
            if missing:
                self.skipped.append((spec.name, "missing_deps"))
                _log.info(
                    "trigger_consumer_skipped name=%s missing=%s",
                    spec.name,
                    ",".join(missing),
                )
                continue
            try:
                consumer = spec.factory()
            except Exception as exc:  # pragma: no cover - defensive
                self.skipped.append((spec.name, "build_error"))
                _log.warning("trigger_consumer_build_failed name=%s: %s", spec.name, exc)
                continue
            self.consumers.append(consumer)
            self.tasks.append(
                asyncio.create_task(
                    self._run_consumer(spec.name, consumer),
                    name=f"trigger-consumer-{spec.name}",
                )
            )

        _log.info(
            "trigger_consumers_started count=%d skipped=%d",
            len(self.tasks),
            len(self.skipped),
        )

    async def stop(self) -> None:
        """Cancel and await every consumer task."""
        if not self._started:
            return
        self._stopping = True  # an exit from here on is a shutdown, not a crash

        for consumer in self.consumers:
            stop = getattr(consumer, "stop", None)
            if stop is None:
                continue
            try:
                result = stop()
                if isinstance(result, Awaitable):
                    await result
            except Exception as exc:  # pragma: no cover - defensive
                _log.warning("trigger_consumer_stop_error: %s", exc)

        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

        _log.info("trigger_consumers_stopped count=%d", len(self.tasks))
        self.tasks = []
        self.consumers = []
        self._started = False

    # ── Internals ─────────────────────────────────────────────────────────────

    # A consumer that stayed up this long is considered healthy again, so its
    # next failure restarts after the base backoff rather than the grown one.
    _HEALTHY_RUN_S = 60.0

    def health(self) -> dict[str, dict[str, Any]]:
        """Per-consumer state (running | restarting | stopped) and restart count."""
        return {name: dict(info) for name, info in self._health.items()}

    async def check_health(self) -> None:
        """HealthCheck callable for /health: raises while any consumer is down."""
        down = sorted(n for n, i in self._health.items() if i.get("state") != "running")
        if down:
            raise RuntimeError(f"trigger consumers not running: {', '.join(down)}")

    def _set_health(self, name: str, state: str, *, restarted: bool = False) -> None:
        info = self._health.setdefault(name, {"state": state, "restarts": 0})
        info["state"] = state
        if restarted:
            info["restarts"] = int(info.get("restarts", 0)) + 1
        try:
            from app.triggers.metrics import TRIGGER_CONSUMER_UP

            TRIGGER_CONSUMER_UP.labels(consumer=name).set(1 if state == "running" else 0)
        except Exception:  # pragma: no cover - metrics are best-effort
            pass

    async def _run_consumer(self, name: str, consumer: _Consumer) -> None:
        """Run *consumer*, restarting it with exponential backoff whenever it exits.

        Consumers log and RETURN from ``start()`` on a Redis error (failover,
        dropped connection); that used to disable their triggers on this
        replica until the pod restarted (TRG-17). Only a supervisor shutdown
        ends the loop. Events published meanwhile wait in the trigger stream
        and are read when the consumer rejoins its group (TRG-18).
        """
        loop = asyncio.get_running_loop()
        failures = 0
        while True:
            self._set_health(name, "running")
            started_at = loop.time()
            try:
                await consumer.start()
                error: object = "exited"
            except asyncio.CancelledError:
                self._set_health(name, "stopped")
                raise
            except Exception as exc:
                error = exc
            if self._stopping or not self._started:
                self._set_health(name, "stopped")
                return
            failures = 1 if loop.time() - started_at >= self._HEALTHY_RUN_S else failures + 1
            delay = min(
                self._restart_backoff_s * (2 ** (failures - 1)), self._restart_backoff_max_s
            )
            self._set_health(name, "restarting", restarted=True)
            _log.error(
                "trigger_consumer_restarting name=%s error=%s in=%.2fs", name, error, delay
            )
            await asyncio.sleep(delay)
            if self._stopping or not self._started:
                self._set_health(name, "stopped")
                return

    def _consumer_specs(self) -> list[_ConsumerSpec]:
        from app.services.goal_notifications import GoalNotificationConsumer
        from app.triggers.consumers.chain import ChainTriggerConsumer
        from app.triggers.consumers.condition import ConditionTriggerConsumer
        from app.triggers.consumers.conversational import ConversationalTriggerConsumer
        from app.triggers.consumers.event import EventTriggerConsumer
        from app.triggers.consumers.hitl import HITLTriggerConsumer
        from app.triggers.consumers.memory import MemoryTriggerConsumer

        core_deps = {
            "schedule_store": self._schedule_store,
            "dispatcher": self._dispatcher,
            "redis": self._redis,
        }

        def _core_kwargs() -> dict[str, Any]:
            return {
                "trigger_store": self._schedule_store,
                "dispatcher": self._dispatcher,
                "redis": self._redis,
            }

        specs: list[_ConsumerSpec] = [
            _ConsumerSpec(
                name="ChainTriggerConsumer",
                factory=lambda: ChainTriggerConsumer(**_core_kwargs()),
                required=dict(core_deps),
            ),
            _ConsumerSpec(
                name="HITLTriggerConsumer",
                factory=lambda: HITLTriggerConsumer(**_core_kwargs()),
                required=dict(core_deps),
            ),
            _ConsumerSpec(
                name="MemoryTriggerConsumer",
                factory=lambda: MemoryTriggerConsumer(**_core_kwargs()),
                required=dict(core_deps),
            ),
            _ConsumerSpec(
                name="EventTriggerConsumer",
                factory=lambda: EventTriggerConsumer(**_core_kwargs()),
                required=dict(core_deps),
            ),
            _ConsumerSpec(
                name="ConditionTriggerConsumer",
                factory=lambda: ConditionTriggerConsumer(**_core_kwargs()),
                required=dict(core_deps),
            ),
            _ConsumerSpec(
                name="ConversationalTriggerConsumer",
                factory=lambda: ConversationalTriggerConsumer(**_core_kwargs()),
                required=dict(core_deps),
            ),
            _ConsumerSpec(
                name="GoalNotificationConsumer",
                factory=lambda: GoalNotificationConsumer(
                    redis=self._redis,
                    notification_service=self._notification_service,
                    db_session_factory=self._db_session_factory,
                ),
                required={
                    "redis": self._redis,
                    "notification_service": self._notification_service,
                },
            ),
        ]
        return specs
