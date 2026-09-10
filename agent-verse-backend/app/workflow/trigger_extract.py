"""Normalise workflow trigger declarations across the two on-disk shapes.

A stored workflow definition can carry its trigger(s) in either of two shapes:

* the visual builder writes a **plural** ``triggers`` list::

      {"triggers": [{"type": "webhook", "webhook": {...}}]}

* the DSL (``WorkflowDefinition``) uses a **singular** ``trigger`` object::

      {"trigger": {"type": "schedule", "schedule": {"cron": "..."}}}

Both the public webhook endpoint and the schedule beat scan must accept either,
so this helper flattens them into a single list of trigger dicts.
"""

from __future__ import annotations

from typing import Any


def extract_triggers(definition: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return every trigger dict declared by ``definition`` (both shapes)."""
    if not isinstance(definition, dict):
        return []
    out: list[dict[str, Any]] = []
    plural = definition.get("triggers")
    if isinstance(plural, list):
        out.extend(t for t in plural if isinstance(t, dict))
    singular = definition.get("trigger")
    if isinstance(singular, dict):
        out.append(singular)
    return out


def schedule_cron(trigger: dict[str, Any]) -> tuple[str, str]:
    """Return ``(cron, timezone)`` from a schedule trigger dict.

    Accepts the cron nested under ``schedule`` (DSL/builder) or flattened onto
    the trigger itself. Returns ``("", "UTC")`` when no cron is present.
    """
    sched = trigger.get("schedule")
    if isinstance(sched, dict):
        cron = str(sched.get("cron") or "").strip()
        tz = str(sched.get("timezone") or "UTC")
    else:
        cron = str(trigger.get("cron") or "").strip()
        tz = str(trigger.get("timezone") or "UTC")
    return cron, tz
