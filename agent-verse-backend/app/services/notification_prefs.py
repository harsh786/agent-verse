"""Tenant notification preferences (``notif_prefs:{tenant_id}`` in Redis).

a08-F196-05 (owner decision: opt-in). Goal outcome notifications
(``goalComplete`` / ``goalFailed``) are OFF unless the tenant switched them on.
Preferences saved before goal notifications were delivered at all carry no
schema version; Settings then showed (and saved back) ``goalComplete: true`` as
a default nobody chose, so for such a record the goal keys are treated as never
set — off. Only a record written by the current PUT (``_v`` >= 2) can opt in.
"""

from __future__ import annotations

import json
from typing import Any

PREFS_VERSION = 2
_VERSION_KEY = "_v"

# The keys a tenant may set, with the value used when it never set them.
DEFAULT_PREFS: dict[str, bool] = {
    "goalComplete": False,
    "goalFailed": False,
    "budgetAlert": True,
    "hitlPending": True,
    "weeklyReport": False,
}
NOTIFICATION_KEYS = frozenset(DEFAULT_PREFS)
# Opt-in keys whose value is honoured only from a versioned record.
_OPT_IN_KEYS = frozenset({"goalComplete", "goalFailed"})

# Goal outcome → the preference that opts the tenant in.
GOAL_OUTCOME_PREF: dict[str, str] = {"complete": "goalComplete", "failed": "goalFailed"}


def prefs_key(tenant_id: str) -> str:
    return f"notif_prefs:{tenant_id}"


def merge_prefs(stored: Any) -> dict[str, bool]:
    """Effective preferences: defaults, overlaid with a stored record."""
    prefs = dict(DEFAULT_PREFS)
    if not stored:
        return prefs
    data = json.loads(stored) if isinstance(stored, str | bytes) else stored
    if not isinstance(data, dict):
        return prefs
    versioned = int(data.get(_VERSION_KEY) or 0) >= PREFS_VERSION
    for key, value in data.items():
        if key not in NOTIFICATION_KEYS or not isinstance(value, bool):
            continue
        if key in _OPT_IN_KEYS and not versioned:
            continue  # saved before opt-in existed: never chosen, so off
        prefs[key] = value
    return prefs


def serialize_prefs(body: dict[str, bool]) -> str:
    """The record a PUT stores (versioned, so its goal opt-ins count)."""
    return json.dumps({**body, _VERSION_KEY: PREFS_VERSION})


async def load_prefs(redis: Any, tenant_id: str) -> dict[str, bool]:
    """Effective preferences for *tenant_id*; Redis errors propagate."""
    return merge_prefs(await redis.get(prefs_key(tenant_id)))
