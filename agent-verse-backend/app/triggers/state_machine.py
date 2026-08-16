"""State machine — create, transition, query state; emits events on state change."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

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
        instance.history.append({
            "from_state": old_state,
            "to_state": t.to_state,
            "event": event,
            "at": instance.updated_at,
            "payload": payload or {},
        })

        # Mark complete if terminal
        for state_def in defn.states:
            if state_def.name == t.to_state and state_def.is_terminal:
                instance.status = "completed"
                break

        _log.info(
            "state_machine_transition machine=%s entity=%s %s --(%s)--> %s",
            machine_id, entity_id, old_state, event, t.to_state,
        )
        return {
            "from_state": old_state,
            "to_state": t.to_state,
            "event": event,
            "transitioned": True,
        }
