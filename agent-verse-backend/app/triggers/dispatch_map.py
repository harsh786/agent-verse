"""Single source of truth for how every ``TriggerType`` reaches the runtime
(2.W-10).

Each of the 58 ``TriggerType`` members is classified into exactly one dispatch
mechanism so that a type can never *silently* accept-and-never-fire:

  * ``BEAT``        — fired by the Celery beat / schedule loop (``scaling/tasks``).
  * ``PUSH``        — dispatched by an inbound HTTP endpoint (webhook / channel).
  * ``CONSUMER``    — dispatched by a Redis pub/sub consumer started at runtime
                      (``TriggerConsumerSupervisor``).
  * ``UNSUPPORTED`` — code exists but there is **no** started runtime path today;
                      the API rejects registration so a tenant cannot create a
                      trigger that can never fire. Implementing a poller/consumer
                      later moves the member into one of the mechanisms above.

The classification is derived from verified wiring:
  * beat branches   — ``app/scaling/tasks.py`` (``trigger_type == "cron"`` …).
  * push endpoints  — ``WEBHOOK_TYPE_MAP`` below (consumed by ``api/triggers.py``)
                      plus the generic ``webhook``/``rest`` fire path.
  * consumers       — ``ChainTriggerConsumer`` / ``HITLTriggerConsumer`` /
                      ``MemoryTriggerConsumer`` channel maps.

Adding a new ``TriggerType`` without classifying it here fails
``tests/triggers/test_trigger_type_coverage.py`` — the exhaustiveness gate.
"""

from __future__ import annotations

import enum

from app.triggers.models import TriggerType


class DispatchMechanism(enum.StrEnum):
    BEAT = "beat"
    PUSH = "push"
    CONSUMER = "consumer"
    UNSUPPORTED = "unsupported"


# ── Inbound webhook / channel push map ────────────────────────────────────────
# The URL path segment (``/triggers/webhooks/{webhook_type}/{token}``) → the
# TriggerType value it dispatches. ``api/triggers.py`` imports this so the map is
# defined in exactly one place.
WEBHOOK_TYPE_MAP: dict[str, str] = {
    "github": TriggerType.GITHUB_WEBHOOK.value,
    "stripe": TriggerType.STRIPE_WEBHOOK.value,
    "jira": TriggerType.JIRA_WEBHOOK.value,
    "pagerduty": TriggerType.PAGERDUTY.value,
    "linear": TriggerType.LINEAR_WEBHOOK.value,
    "sentry": TriggerType.SENTRY_ISSUE.value,
    "grafana": TriggerType.GRAFANA_ALERT.value,
    "cloudwatch": TriggerType.CLOUDWATCH.value,
    "datadog": TriggerType.DATADOG.value,
    "alertmanager": TriggerType.ALERTMANAGER.value,
    "confluence": TriggerType.CONFLUENCE_WEBHOOK.value,
    "salesforce": TriggerType.SALESFORCE_EVENT.value,
    "slack": TriggerType.SLACK_EVENT.value,
    "teams": TriggerType.TEAMS_WEBHOOK.value,
}


# ── Runtime consumer coverage (Redis pub/sub channel → TriggerType) ───────────
# These consumers are started unconditionally by TriggerConsumerSupervisor when
# Redis is available. Keep in sync with each consumer's channel map.
CONSUMER_TYPES: frozenset[TriggerType] = frozenset(
    {
        TriggerType.GOAL_COMPLETED,
        TriggerType.GOAL_FAILED,
        TriggerType.GOAL_SCORE_BELOW,
        TriggerType.HITL_APPROVED,
        TriggerType.HITL_REJECTED,
        TriggerType.MEMORY_CREATED,
    }
)

# ── Beat / schedule-loop coverage ─────────────────────────────────────────────
BEAT_TYPES: frozenset[TriggerType] = frozenset(
    {
        TriggerType.CRON,
        TriggerType.INTERVAL,
        TriggerType.ONCE,
        TriggerType.FILE_DROP,
        TriggerType.RSS_FEED,
    }
)

# Generic HTTP fire endpoints that dispatch without a per-vendor parser.
_GENERIC_PUSH: frozenset[TriggerType] = frozenset(
    {TriggerType.WEBHOOK, TriggerType.REST}
)

_PUSH_TYPES: frozenset[TriggerType] = _GENERIC_PUSH | frozenset(
    TriggerType(v) for v in WEBHOOK_TYPE_MAP.values()
)


def _build_dispatch() -> dict[TriggerType, DispatchMechanism]:
    mapping: dict[TriggerType, DispatchMechanism] = {}
    for t in TriggerType:
        if t in BEAT_TYPES:
            mapping[t] = DispatchMechanism.BEAT
        elif t in _PUSH_TYPES:
            mapping[t] = DispatchMechanism.PUSH
        elif t in CONSUMER_TYPES:
            mapping[t] = DispatchMechanism.CONSUMER
        else:
            mapping[t] = DispatchMechanism.UNSUPPORTED
    return mapping


#: Exhaustive classification for every TriggerType member.
TRIGGER_DISPATCH: dict[TriggerType, DispatchMechanism] = _build_dispatch()


def dispatch_mechanism(trigger_type: TriggerType | str) -> DispatchMechanism:
    """Return the dispatch mechanism for a TriggerType (accepts the enum or its
    string value). Unknown strings are treated as UNSUPPORTED."""
    try:
        t = trigger_type if isinstance(trigger_type, TriggerType) else TriggerType(trigger_type)
    except ValueError:
        return DispatchMechanism.UNSUPPORTED
    return TRIGGER_DISPATCH.get(t, DispatchMechanism.UNSUPPORTED)


def is_supported(trigger_type: TriggerType | str) -> bool:
    """True when the trigger type has a real runtime dispatch path."""
    return dispatch_mechanism(trigger_type) is not DispatchMechanism.UNSUPPORTED


def unsupported_reason(trigger_type: TriggerType | str) -> str:
    """Human-readable rejection message for an unsupported trigger type."""
    value = trigger_type.value if isinstance(trigger_type, TriggerType) else str(trigger_type)
    return (
        f"Trigger type {value!r} is not yet supported: it has no runtime "
        "dispatch path (no beat branch, HTTP push endpoint, or started consumer), "
        "so a trigger of this type could never fire. Choose a supported type."
    )
