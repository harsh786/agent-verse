"""TriggerConsumerSupervisor (WT-4).

Owns the long-running trigger consumers and their asyncio task handles. On
``start()`` it builds each consumer whose dependencies are satisfied and spawns
one background task per available consumer; on ``stop()`` it cancels and awaits
every task so shutdown is graceful.

The three *core* consumers (chain / HITL / memory) subscribe to Redis pub/sub
and therefore need a ``schedule_store`` (trigger lookups), a ``dispatcher``
(governed dispatch) and a ``redis`` client. A consumer whose dependencies are
missing is skipped and recorded in :attr:`skipped` rather than crashing startup.

The extended families (data / monitoring / IoT / advanced) require external
clients (MQTT/S3/etc.) that are not wired by default, so they are only *built*
when ``enable_extended`` is set and are skipped when their client is absent.
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
        enable_extended: bool = False,
    ) -> None:
        self._schedule_store = schedule_store
        self._dispatcher = dispatcher
        self._redis = redis
        self._enable_extended = enable_extended

        self.consumers: list[_Consumer] = []
        self.tasks: list[asyncio.Task[Any]] = []
        self.skipped: list[tuple[str, str]] = []
        self._started = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Build available consumers and spawn one task per consumer."""
        if self._started:
            return
        self._started = True

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

    async def _run_consumer(self, name: str, consumer: _Consumer) -> None:
        try:
            await consumer.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            _log.error("trigger_consumer_crashed name=%s: %s", name, exc)

    def _consumer_specs(self) -> list[_ConsumerSpec]:
        from app.triggers.consumers.chain import ChainTriggerConsumer
        from app.triggers.consumers.condition import ConditionTriggerConsumer
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
        ]

        if self._enable_extended:
            specs.extend(self._extended_specs())
        return specs

    def _extended_specs(self) -> list[_ConsumerSpec]:
        """Extended families — gated behind a settings flag.

        Their external clients (MQTT/S3/…) are not wired onto app.state, so each
        declares that client as a required dependency and is skipped when it is
        absent. Wire the client and pass it through to enable a family.
        """
        from app.triggers.iot.mqtt import MQTTTriggerConsumer

        mqtt_client: Any = None  # not wired by default
        return [
            _ConsumerSpec(
                name="MQTTTriggerConsumer",
                factory=lambda: MQTTTriggerConsumer(
                    trigger_store=self._schedule_store,
                    dispatcher=self._dispatcher,
                    mqtt_client=mqtt_client,
                ),
                required={"mqtt_client": mqtt_client},
            ),
        ]
