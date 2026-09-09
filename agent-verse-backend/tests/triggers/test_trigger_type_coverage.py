"""2.W-10: exhaustive trigger-type coverage gate.

Every one of the 58 ``TriggerType`` members must be classified into exactly one
dispatch mechanism, and each mechanism claim must be backed by real wiring:

  (a) BEAT      — a beat branch in ``app/scaling/tasks.py``,
  (b) PUSH      — the shared ``WEBHOOK_TYPE_MAP`` (or the generic webhook/rest fire),
  (c) CONSUMER  — a Redis consumer started by the supervisor,
  (d) UNSUPPORTED — rejected at API registration so it cannot silently never-fire.

No member may silently accept-and-never-fire: an unclassified or mis-backed type
fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.triggers.dispatch_map import (
    BEAT_TYPES,
    CONSUMER_TYPES,
    TRIGGER_DISPATCH,
    WEBHOOK_TYPE_MAP,
    DispatchMechanism,
    dispatch_mechanism,
    is_supported,
)
from app.triggers.models import TriggerType

_BACKEND = Path(__file__).resolve().parents[2]
_TASKS_SRC = (_BACKEND / "app" / "scaling" / "tasks.py").read_text()
_CONSUMERS_SRC = "\n".join(
    p.read_text() for p in (_BACKEND / "app" / "triggers" / "consumers").glob("*.py")
)
_GENERIC_PUSH = {TriggerType.WEBHOOK, TriggerType.REST}


def test_every_trigger_type_is_classified() -> None:
    """Exhaustiveness — the gate that catches a new type added without wiring."""
    missing = [t for t in TriggerType if t not in TRIGGER_DISPATCH]
    assert not missing, f"unclassified TriggerType members: {missing}"
    assert len(TRIGGER_DISPATCH) == len(list(TriggerType)) == 58


@pytest.mark.parametrize("trigger_type", list(TriggerType))
def test_type_is_fired_or_explicitly_unsupported(trigger_type: TriggerType) -> None:
    """The core DoD: each member is beat / push / consumer OR unsupported."""
    mech = dispatch_mechanism(trigger_type)
    assert mech in DispatchMechanism
    # Supported ⇔ not UNSUPPORTED — no third silent state.
    assert is_supported(trigger_type) == (mech is not DispatchMechanism.UNSUPPORTED)


@pytest.mark.parametrize(
    "trigger_type", [t for t, m in TRIGGER_DISPATCH.items() if m is DispatchMechanism.BEAT]
)
def test_beat_types_have_a_beat_branch(trigger_type: TriggerType) -> None:
    assert f'trigger_type == "{trigger_type.value}"' in _TASKS_SRC, (
        f"{trigger_type.value}: classified BEAT but no beat branch in scaling/tasks.py"
    )


@pytest.mark.parametrize(
    "trigger_type", [t for t, m in TRIGGER_DISPATCH.items() if m is DispatchMechanism.PUSH]
)
def test_push_types_backed_by_webhook_map_or_generic(trigger_type: TriggerType) -> None:
    backed = trigger_type in _GENERIC_PUSH or trigger_type.value in WEBHOOK_TYPE_MAP.values()
    assert backed, f"{trigger_type.value}: classified PUSH but not in WEBHOOK_TYPE_MAP"


@pytest.mark.parametrize("trigger_type", sorted(CONSUMER_TYPES, key=lambda t: t.value))
def test_consumer_types_referenced_by_a_started_consumer(trigger_type: TriggerType) -> None:
    assert f'"{trigger_type.value}"' in _CONSUMERS_SRC, (
        f"{trigger_type.value}: classified CONSUMER but not handled by any consumer"
    )


def test_distribution_matches_verified_ground_truth() -> None:
    counts: dict[DispatchMechanism, int] = dict.fromkeys(DispatchMechanism, 0)
    for mech in TRIGGER_DISPATCH.values():
        counts[mech] += 1
    # cron/interval/once/file_drop/rss_feed/api_poll + relative_delay/deadline/business_calendar
    assert counts[DispatchMechanism.BEAT] == 9
    assert counts[DispatchMechanism.PUSH] == 16
    # chain(3)+hitl(2)+memory+event+familyD(5)+conversational(7)+email_arrival+discord+meeting
    assert counts[DispatchMechanism.CONSUMER] == 22
    assert counts[DispatchMechanism.UNSUPPORTED] == 11


def test_known_unsupported_types_are_unsupported() -> None:
    # Types the plan calls out as having no consumer / subscriber.
    for t in (
        TriggerType.GOOGLE_SHEETS,
        TriggerType.SHAREPOINT,
        TriggerType.MQTT,
    ):
        assert not is_supported(t), f"{t.value} should be unsupported until wired"


def test_beat_and_consumer_sets_are_disjoint_from_push() -> None:
    push = {t for t, m in TRIGGER_DISPATCH.items() if m is DispatchMechanism.PUSH}
    assert not (BEAT_TYPES & push)
    assert not (CONSUMER_TYPES & push)
