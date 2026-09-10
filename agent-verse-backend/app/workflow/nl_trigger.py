"""NLTriggerResolver — maps a natural-language description to a TriggerDefinition.

Uses the LLM provider (planner role) to parse phrases like:
  "every Monday at 9am UTC"           → cron trigger
  "when a new GitHub PR is opened"    → webhook trigger with schema hints
  "when the Slack message contains …" → webhook/event trigger
  "daily at midnight"                 → cron trigger

Returns a ``TriggerDefinition`` or raises ``NLTriggerParseError`` if parsing
fails.  Caches resolved definitions in Redis to avoid repeated LLM calls for
identical descriptions.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.observability.logging import get_logger
from app.workflow.dsl import TriggerDefinition

_log = get_logger(__name__)

_CRON_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"every\s+minute", re.I), "* * * * *"),
    (re.compile(r"every\s+hour", re.I), "0 * * * *"),
    (re.compile(r"every\s+day\s+at\s+midnight", re.I), "0 0 * * *"),
    (re.compile(r"daily\s+at\s+midnight", re.I), "0 0 * * *"),
    (re.compile(r"every\s+day", re.I), "0 0 * * *"),
    (re.compile(r"every\s+monday", re.I), "0 9 * * 1"),
    (re.compile(r"every\s+friday", re.I), "0 9 * * 5"),
    (re.compile(r"every\s+weekday", re.I), "0 9 * * 1-5"),
    (re.compile(r"every\s+weekend", re.I), "0 9 * * 6-7"),
    (re.compile(r"weekly", re.I), "0 9 * * 1"),
    (re.compile(r"monthly", re.I), "0 9 1 * *"),
]

_PROMPT = """\
Convert the following natural language description into a workflow trigger specification.

Input: {description}

Output a JSON object with these fields:
  type: "manual" | "cron" | "webhook" | "event"
  cron: string (only when type=cron, crontab format)
  event_name: string (only when type=event)
  webhook_schema: object (optional, JSON Schema of expected webhook payload)

Rules:
- Return ONLY the JSON object, no commentary.
- For time-based triggers use type=cron.
- For API/webhook triggers use type=webhook.
- For internal events use type=event.
- If unsure, default to type=manual.
"""


class NLTriggerParseError(ValueError):
    pass


class NLTriggerResolver:
    """Resolves natural-language trigger descriptions to TriggerDefinition."""

    def __init__(
        self,
        llm_provider: Any | None = None,
        redis_client: Any | None = None,
        cache_ttl: int = 3600,
    ) -> None:
        self._llm = llm_provider
        self._redis = redis_client
        self._cache_ttl = cache_ttl

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def resolve(self, description: str) -> TriggerDefinition:
        """Parse *description* and return a TriggerDefinition."""
        description = description.strip()
        if not description:
            raise NLTriggerParseError("Empty trigger description")

        # 1. Check regex fast-path (no LLM needed)
        fast = self._fast_path(description)
        if fast:
            return fast

        # 2. Check Redis cache
        cached = await self._get_cached(description)
        if cached:
            return cached

        # 3. LLM path
        result = await self._llm_parse(description)
        await self._cache(description, result)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fast_path(self, description: str) -> TriggerDefinition | None:
        for pattern, _cron in _CRON_PATTERNS:
            if pattern.search(description):
                return TriggerDefinition(type="schedule")
        if any(kw in description.lower() for kw in ("webhook", "http ", "post ", "api ")):
            return TriggerDefinition(type="webhook")
        return None

    async def _get_cached(self, description: str) -> TriggerDefinition | None:
        if self._redis is None:
            return None
        try:
            key = f"nl_trigger:{hash(description)}"
            raw = await self._redis.get(key)
            if raw:
                return TriggerDefinition(**json.loads(raw))
        except Exception as exc:
            _log.debug("nl_trigger_cache_miss", error=str(exc))
        return None

    async def _cache(self, description: str, trigger: TriggerDefinition) -> None:
        if self._redis is None:
            return
        try:
            key = f"nl_trigger:{hash(description)}"
            await self._redis.setex(key, self._cache_ttl, trigger.model_dump_json())
        except Exception as exc:
            _log.debug("nl_trigger_cache_write_failed", error=str(exc))

    async def _llm_parse(self, description: str) -> TriggerDefinition:
        if self._llm is None:
            raise NLTriggerParseError("No LLM provider configured for NL trigger resolution")

        prompt = _PROMPT.format(description=description)
        try:
            from app.providers.base import CompletionRequest, Message
            from app.providers.model_defaults import configured_default_model

            req = CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model=configured_default_model("gpt-4o"),
                max_tokens=256,
                temperature=0.0,
            )
            response = await self._llm.complete(req)
            raw_text = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            raise NLTriggerParseError(f"LLM call failed: {exc}") from exc

        # Extract JSON from response
        try:
            # Attempt to find JSON block
            match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if not match:
                raise ValueError("No JSON found in response")
            data = json.loads(match.group())
            return TriggerDefinition(**data)
        except Exception as exc:
            _log.warning("nl_trigger_parse_failed", raw=raw_text[:200], error=str(exc))
            raise NLTriggerParseError(
                f"Failed to parse LLM response into TriggerDefinition: {exc}"
            ) from exc
