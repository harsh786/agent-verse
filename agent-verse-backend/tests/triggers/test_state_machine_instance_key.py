"""TRG-40: a second state machine on the same entity keeps its own instance.

Instances were keyed by (tenant_id, entity_id) without machine_id, so two
machines tracking the same entity shared one instance: the second machine
reused (and could clobber) the first machine's state.
"""

from __future__ import annotations

from typing import Any

from app.triggers.state_machine import (
    StateDefinition,
    StateMachine,
    StateMachineDefinition,
    TransitionDefinition,
)

T = "t-key"


def _payment() -> StateMachineDefinition:
    return StateMachineDefinition(
        machine_id="payment", tenant_id=T, name="payment",
        states=[StateDefinition(name="unpaid", is_initial=True), StateDefinition(name="paid")],
        transitions=[TransitionDefinition(from_state="unpaid", to_state="paid", event="pay")],
    )


def _shipping() -> StateMachineDefinition:
    return StateMachineDefinition(
        machine_id="shipping", tenant_id=T, name="shipping",
        states=[StateDefinition(name="waiting", is_initial=True), StateDefinition(name="sent")],
        transitions=[TransitionDefinition(from_state="waiting", to_state="sent", event="ship")],
    )


def test_two_machines_on_one_entity_keep_independent_states() -> None:
    sm = StateMachine()
    sm.define(_payment())
    sm.define(_shipping())

    assert sm.transition("payment", "order-1", "pay", T)["to_state"] == "paid"
    # The shipping machine starts from ITS initial state, not payment's "paid".
    shipped = sm.transition("shipping", "order-1", "ship", T)
    assert shipped["transitioned"] is True
    assert shipped["from_state"] == "waiting"

    pay = sm.get_instance("payment", "order-1", T)
    ship = sm.get_instance("shipping", "order-1", T)
    assert pay is not None and ship is not None
    assert (pay.current_state, ship.current_state) == ("paid", "sent")
    assert pay.instance_id != ship.instance_id


async def test_async_instance_lookup_filters_by_machine() -> None:
    """The DB query for an instance includes machine_id."""
    seen: list[str] = []

    class _Result:
        def scalar_one_or_none(self) -> Any:
            return None

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        def begin(self) -> _Session:
            return self

        async def execute(self, stmt: Any, *_: Any, **__: Any) -> _Result:
            seen.append(str(stmt))
            return _Result()

    sm = StateMachine()
    sm._db_factory = lambda: _Session()
    assert await sm.get_instance_async("shipping", "order-1", T) is None
    selects = [s for s in seen if "trigger_state_machine_instances" in s]
    assert selects and all("machine_id" in s.split("WHERE", 1)[1] for s in selects)
