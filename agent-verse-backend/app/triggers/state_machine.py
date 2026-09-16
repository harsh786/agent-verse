"""State machine — create, transition, query state; emits events on state change."""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

_log = logging.getLogger(__name__)


@dataclass
class StateDefinition:
    name: str
    is_initial: bool = False
    is_terminal: bool = False
    description: str = ""


@dataclass
class TransitionDefinition:
    from_state: str
    to_state: str
    event: str
    condition_cel: str = ""
    description: str = ""


@dataclass
class StateMachineDefinition:
    machine_id: str
    tenant_id: str
    name: str
    states: list[StateDefinition] = field(default_factory=list)
    transitions: list[TransitionDefinition] = field(default_factory=list)
    created_at: str = ""

    def initial_state(self) -> str | None:
        for s in self.states:
            if s.is_initial:
                return s.name
        return self.states[0].name if self.states else None

    def find_transition(self, from_state: str, event: str) -> TransitionDefinition | None:
        for t in self.transitions:
            if t.from_state == from_state and t.event == event:
                return t
        return None


@dataclass
class StateMachineInstance:
    instance_id: str
    machine_id: str
    tenant_id: str
    entity_id: str
    current_state: str
    history: list[dict] = field(default_factory=list)
    status: str = "running"  # running | completed | error
    updated_at: str = ""


class StateMachine:
    """In-memory state machine registry and executor."""

    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], StateMachineDefinition] = {}
        self._instances: dict[tuple[str, str], StateMachineInstance] = {}
        # Wired at startup by lifespan so the async methods persist to Postgres.
        # When None (tests / no-DB dev) the async methods fall back to the
        # in-memory dicts above.
        self._db_factory: Any = None

    def define(self, defn: StateMachineDefinition) -> None:
        """Register a state machine definition."""
        self._definitions[(defn.tenant_id, defn.machine_id)] = defn

    def get_definition(self, machine_id: str, tenant_id: str) -> StateMachineDefinition | None:
        return self._definitions.get((tenant_id, machine_id))

    def list_definitions(self, tenant_id: str) -> list[StateMachineDefinition]:
        return [v for (tid, _), v in self._definitions.items() if tid == tenant_id]

    def create_instance(
        self,
        machine_id: str,
        entity_id: str,
        tenant_id: str,
    ) -> StateMachineInstance:
        defn = self.get_definition(machine_id, tenant_id)
        if defn is None:
            raise ValueError(f"Unknown state machine: {machine_id}")
        initial = defn.initial_state()
        if initial is None:
            raise ValueError(f"State machine {machine_id} has no states")
        instance = StateMachineInstance(
            instance_id=uuid.uuid4().hex,
            machine_id=machine_id,
            tenant_id=tenant_id,
            entity_id=entity_id,
            current_state=initial,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._instances[(tenant_id, entity_id)] = instance
        return instance

    def get_instance(self, entity_id: str, tenant_id: str) -> StateMachineInstance | None:
        return self._instances.get((tenant_id, entity_id))

    def transition(
        self,
        machine_id: str,
        entity_id: str,
        event: str,
        tenant_id: str,
        payload: dict | None = None,
    ) -> dict:
        """Apply an event to an instance.

        Returns a dict with {from_state, to_state, event, transitioned}.
        """
        instance = self.get_instance(entity_id, tenant_id)
        if instance is None:
            instance = self.create_instance(machine_id, entity_id, tenant_id)

        defn = self.get_definition(machine_id, tenant_id)
        if defn is None:
            raise ValueError(f"Unknown state machine: {machine_id}")

        t = defn.find_transition(instance.current_state, event)
        if t is None:
            return {
                "from_state": instance.current_state,
                "to_state": instance.current_state,
                "event": event,
                "transitioned": False,
                "reason": "no_matching_transition",
            }

        old_state = instance.current_state
        instance.current_state = t.to_state
        instance.updated_at = datetime.now(UTC).isoformat()
        instance.history.append(
            {
                "from_state": old_state,
                "to_state": t.to_state,
                "event": event,
                "at": instance.updated_at,
                "payload": payload or {},
            }
        )

        # Mark complete if terminal
        for state_def in defn.states:
            if state_def.name == t.to_state and state_def.is_terminal:
                instance.status = "completed"
                break

        _log.info(
            "state_machine_transition machine=%s entity=%s %s --(%s)--> %s",
            machine_id,
            entity_id,
            old_state,
            event,
            t.to_state,
        )
        return {
            "from_state": old_state,
            "to_state": t.to_state,
            "event": event,
            "transitioned": True,
        }

    # ── DB-backed async CRUD (durable; used by the REST API) ──────────────────
    # Mirror the sync methods above but persist to / read from Postgres when a
    # ``_db_factory`` (async_sessionmaker) is wired by the app lifespan; fall
    # back to the in-memory dicts when it is None (tests / no-DB dev).

    @staticmethod
    def _def_to_json(defn: StateMachineDefinition) -> dict[str, Any]:
        return {
            "states": [asdict(s) for s in defn.states],
            "transitions": [asdict(t) for t in defn.transitions],
        }

    @staticmethod
    def _def_from_row(
        machine_id: str, tenant_id: str, name: str, definition: dict[str, Any], created_at: Any
    ) -> StateMachineDefinition:
        return StateMachineDefinition(
            machine_id=machine_id,
            tenant_id=tenant_id,
            name=name,
            states=[StateDefinition(**s) for s in definition.get("states", [])],
            transitions=[TransitionDefinition(**t) for t in definition.get("transitions", [])],
            created_at=created_at.isoformat() if hasattr(created_at, "isoformat") else "",
        )

    async def define_async(self, defn: StateMachineDefinition) -> None:
        """Register a definition, persisting it when a DB factory is wired."""
        self.define(defn)  # keep in-memory cache in sync
        db = self._db_factory
        if db is None:
            return
        try:
            from sqlalchemy import select
            from sqlalchemy import text as _t

            from app.db.models.state_machine import StateMachineDefinitionRow

            async with db() as session:
                await session.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"),
                    {"tid": defn.tenant_id},
                )
                row = (
                    await session.execute(
                        select(StateMachineDefinitionRow).where(
                            StateMachineDefinitionRow.machine_id == defn.machine_id,
                            StateMachineDefinitionRow.tenant_id == defn.tenant_id,
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    session.add(
                        StateMachineDefinitionRow(
                            machine_id=defn.machine_id,
                            tenant_id=defn.tenant_id,
                            name=defn.name,
                            definition=self._def_to_json(defn),
                        )
                    )
                else:
                    row.name = defn.name
                    row.definition = self._def_to_json(defn)
                await session.commit()
        except Exception as exc:
            get_logger(__name__).warning("state_machine_define_db_failed", error=str(exc))

    async def get_definition_async(
        self, machine_id: str, tenant_id: str
    ) -> StateMachineDefinition | None:
        db = self._db_factory
        if db is None:
            return self.get_definition(machine_id, tenant_id)
        try:
            from sqlalchemy import select
            from sqlalchemy import text as _t

            from app.db.models.state_machine import StateMachineDefinitionRow

            async with db() as session:
                await session.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
                )
                row = (
                    await session.execute(
                        select(StateMachineDefinitionRow).where(
                            StateMachineDefinitionRow.machine_id == machine_id,
                            StateMachineDefinitionRow.tenant_id == tenant_id,
                        )
                    )
                ).scalar_one_or_none()
            if row is None:
                return None
            return self._def_from_row(
                row.machine_id, row.tenant_id, row.name, row.definition, row.created_at
            )
        except Exception as exc:
            get_logger(__name__).warning("state_machine_get_def_db_failed", error=str(exc))
            return self.get_definition(machine_id, tenant_id)

    async def list_definitions_async(self, tenant_id: str) -> list[StateMachineDefinition]:
        db = self._db_factory
        if db is None:
            return self.list_definitions(tenant_id)
        try:
            from sqlalchemy import select
            from sqlalchemy import text as _t

            from app.db.models.state_machine import StateMachineDefinitionRow

            async with db() as session:
                await session.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
                )
                rows = (
                    await session.execute(
                        select(StateMachineDefinitionRow)
                        .where(StateMachineDefinitionRow.tenant_id == tenant_id)
                        .order_by(StateMachineDefinitionRow.created_at.desc())
                    )
                ).scalars().all()
            return [
                self._def_from_row(
                    r.machine_id, r.tenant_id, r.name, r.definition, r.created_at
                )
                for r in rows
            ]
        except Exception as exc:
            get_logger(__name__).warning("state_machine_list_def_db_failed", error=str(exc))
            return self.list_definitions(tenant_id)

    async def delete_definition_async(self, machine_id: str, tenant_id: str) -> None:
        """Delete a definition, removing it from DB when a factory is wired."""
        self._definitions.pop((tenant_id, machine_id), None)  # keep cache in sync
        db = self._db_factory
        if db is None:
            return
        try:
            from sqlalchemy import text as _t

            async with db() as session:
                await session.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
                )
                await session.execute(
                    _t(
                        "DELETE FROM trigger_state_machine_definitions "
                        "WHERE machine_id = :mid AND tenant_id = :tid"
                    ),
                    {"mid": machine_id, "tid": tenant_id},
                )
                await session.commit()
        except Exception as exc:
            get_logger(__name__).warning("state_machine_delete_def_db_failed", error=str(exc))

    async def create_instance_async(
        self, machine_id: str, entity_id: str, tenant_id: str
    ) -> StateMachineInstance:
        db = self._db_factory
        if db is None:
            return self.create_instance(machine_id, entity_id, tenant_id)

        defn = await self.get_definition_async(machine_id, tenant_id)
        if defn is None:
            raise ValueError(f"Unknown state machine: {machine_id}")
        initial = defn.initial_state()
        if initial is None:
            raise ValueError(f"State machine {machine_id} has no states")

        instance = StateMachineInstance(
            instance_id=uuid.uuid4().hex,
            machine_id=machine_id,
            tenant_id=tenant_id,
            entity_id=entity_id,
            current_state=initial,
            updated_at=datetime.now(UTC).isoformat(),
        )
        try:
            from sqlalchemy import select
            from sqlalchemy import text as _t

            from app.db.models.state_machine import StateMachineInstanceRow

            async with db() as session:
                await session.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
                )
                existing = (
                    await session.execute(
                        select(StateMachineInstanceRow).where(
                            StateMachineInstanceRow.tenant_id == tenant_id,
                            StateMachineInstanceRow.entity_id == entity_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    session.add(
                        StateMachineInstanceRow(
                            instance_id=instance.instance_id,
                            machine_id=machine_id,
                            tenant_id=tenant_id,
                            entity_id=entity_id,
                            current_state=initial,
                            history=[],
                            status="running",
                        )
                    )
                else:
                    # Recreating resets the instance (mirrors in-memory overwrite).
                    existing.machine_id = machine_id
                    existing.current_state = initial
                    existing.history = []
                    existing.status = "running"
                    instance.instance_id = existing.instance_id
                await session.commit()
        except Exception as exc:
            get_logger(__name__).warning("state_machine_create_inst_db_failed", error=str(exc))
        return instance

    async def get_instance_async(
        self, entity_id: str, tenant_id: str
    ) -> StateMachineInstance | None:
        db = self._db_factory
        if db is None:
            return self.get_instance(entity_id, tenant_id)
        try:
            from sqlalchemy import select
            from sqlalchemy import text as _t

            from app.db.models.state_machine import StateMachineInstanceRow

            async with db() as session:
                await session.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
                )
                row = (
                    await session.execute(
                        select(StateMachineInstanceRow).where(
                            StateMachineInstanceRow.tenant_id == tenant_id,
                            StateMachineInstanceRow.entity_id == entity_id,
                        )
                    )
                ).scalar_one_or_none()
            if row is None:
                return None
            return StateMachineInstance(
                instance_id=row.instance_id,
                machine_id=row.machine_id,
                tenant_id=row.tenant_id,
                entity_id=row.entity_id,
                current_state=row.current_state,
                history=list(row.history or []),
                status=row.status,
                updated_at=row.updated_at.isoformat() if row.updated_at else "",
            )
        except Exception as exc:
            get_logger(__name__).warning("state_machine_get_inst_db_failed", error=str(exc))
            return self.get_instance(entity_id, tenant_id)

    async def transition_async(
        self,
        machine_id: str,
        entity_id: str,
        event: str,
        tenant_id: str,
        payload: dict | None = None,
    ) -> dict:
        """Async transition — loads/saves the instance from DB when wired."""
        db = self._db_factory
        if db is None:
            return self.transition(machine_id, entity_id, event, tenant_id, payload=payload)

        instance = await self.get_instance_async(entity_id, tenant_id)
        if instance is None:
            instance = await self.create_instance_async(machine_id, entity_id, tenant_id)

        defn = await self.get_definition_async(machine_id, tenant_id)
        if defn is None:
            raise ValueError(f"Unknown state machine: {machine_id}")

        t = defn.find_transition(instance.current_state, event)
        if t is None:
            return {
                "from_state": instance.current_state,
                "to_state": instance.current_state,
                "event": event,
                "transitioned": False,
                "reason": "no_matching_transition",
            }

        old_state = instance.current_state
        new_state = t.to_state
        now = datetime.now(UTC).isoformat()
        history_entry = {
            "from_state": old_state,
            "to_state": new_state,
            "event": event,
            "at": now,
            "payload": payload or {},
        }
        terminal = any(s.name == new_state and s.is_terminal for s in defn.states)
        new_status = "completed" if terminal else instance.status

        try:
            from sqlalchemy import select
            from sqlalchemy import text as _t

            from app.db.models.state_machine import StateMachineInstanceRow

            async with db() as session:
                await session.execute(
                    _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
                )
                row = (
                    await session.execute(
                        select(StateMachineInstanceRow).where(
                            StateMachineInstanceRow.tenant_id == tenant_id,
                            StateMachineInstanceRow.entity_id == entity_id,
                        )
                    )
                ).scalar_one_or_none()
                if row is not None:
                    row.current_state = new_state
                    row.history = [*list(row.history or []), history_entry]
                    row.status = new_status
                await session.commit()
        except Exception as exc:
            get_logger(__name__).warning("state_machine_transition_db_failed", error=str(exc))

        _log.info(
            "state_machine_transition machine=%s entity=%s %s --(%s)--> %s",
            machine_id,
            entity_id,
            old_state,
            event,
            new_state,
        )
        return {
            "from_state": old_state,
            "to_state": new_state,
            "event": event,
            "transitioned": True,
        }
