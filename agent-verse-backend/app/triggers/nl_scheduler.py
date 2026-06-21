"""NL Scheduler — parses NL schedule descriptions into TriggerSpecs.

Examples:
  "Every weekday at 9 AM UTC"  → TriggerSpec(CRON, "0 9 * * 1-5", "UTC")
  "Every hour"                 → TriggerSpec(INTERVAL, interval_seconds=3600)
  "At 8 AM, 2 PM, 8 PM daily" → three TriggerSpec(CRON, ...) objects

Compound schedules (multiple times per day) produce multiple TriggerSpecs.
"""

from __future__ import annotations

import json
import re

from app.providers.base import CompletionRequest, LLMProvider, Message
from app.triggers.models import TriggerSpec, TriggerType

_NL_SCHEDULER_SYSTEM = (
    "You are a schedule parser. Convert a natural language schedule description "
    "into one or more trigger specs.\n\n"
    "For a single schedule, respond with:\n"
    '{"trigger_type": "cron|interval|once", "cron_expression": "...", '
    '"timezone": "UTC", "interval_seconds": 0}\n\n'
    'For multiple schedules (compound like "8 AM, 2 PM, 8 PM"), respond with:\n'
    '{"schedules": [{"trigger_type": "cron", "cron_expression": "...", '
    '"timezone": "UTC"}, ...]}\n\n'
    "Respond ONLY with valid JSON. No markdown, no explanation."
)


def _parse_single(obj: dict[str, object]) -> TriggerSpec:
    ttype = TriggerType(str(obj.get("trigger_type", "once")))
    return TriggerSpec(
        trigger_type=ttype,
        cron_expression=str(obj.get("cron_expression", "")),
        timezone=str(obj.get("timezone", "UTC")),
        interval_seconds=int(str(obj.get("interval_seconds", 0))),
        fire_at_iso=str(obj.get("fire_at_iso", "")),
        condition=str(obj.get("condition", "")),
        description=str(obj.get("description", "")),
    )


class NLScheduler:
    """Converts NL schedule descriptions to TriggerSpecs via an LLM provider."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def parse(self, description: str) -> list[TriggerSpec]:
        req = CompletionRequest(
            messages=[
                Message(role="system", content=_NL_SCHEDULER_SYSTEM),
                Message(role="user", content=description),
            ],
            model="claude-opus-4-8",
        )
        resp = await self._provider.complete(req)
        text = re.sub(r"```(?:json)?\n?", "", resp.content).strip()

        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return [TriggerSpec(trigger_type=TriggerType.ONCE, description=description)]

        if "schedules" in obj:
            return [_parse_single(s) for s in obj["schedules"]]
        return [_parse_single(obj)]
