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
from app.workflow.dsl import EventTriggerConfig, ScheduleTriggerConfig, TriggerDefinition

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


EXAMPLE_PHRASES = '"every day at midnight", "every Monday", "hourly", "when a webhook is called"'


def _cache_key(description: str) -> str:
    # Stable across processes: the builtin ``hash()`` is randomised per process
    # (PYTHONHASHSEED), so the shared Redis cache never hit on another replica.
    import hashlib

    return "nl_trigger:" + hashlib.sha256(description.encode()).hexdigest()[:32]


def _definition_from_llm(data: dict[str, Any]) -> TriggerDefinition:
    """Map the prompt's output shape onto the DSL's ``TriggerDefinition``.

    The prompt asks for ``type: manual|cron|webhook|event`` with flat ``cron`` /
    ``event_name`` fields, but ``TriggerDefinition`` only accepts ``schedule``
    (with a nested ``schedule.cron``) / ``api`` / … — so passing the dict
    straight through rejected every cron/manual answer and dropped the cron.
    """
    kind = str(data.get("type") or "").strip().lower()
    if kind in ("cron", "schedule"):
        cron = str(data.get("cron") or (data.get("schedule") or {}).get("cron") or "").strip()
        if not cron:
            raise ValueError("schedule trigger without a cron expression")
        tz = str((data.get("schedule") or {}).get("timezone") or data.get("timezone") or "UTC")
        return TriggerDefinition(
            type="schedule", schedule=ScheduleTriggerConfig(cron=cron, timezone=tz)
        )
    if kind == "event":
        channel = str(data.get("event_name") or (data.get("event") or {}).get("channel") or "")
        return TriggerDefinition(type="event", event=EventTriggerConfig(channel=channel))
    if kind == "manual":
        return TriggerDefinition(type="api")
    return TriggerDefinition(**data)


class NLTriggerResolver:
    """Resolves natural-language trigger descriptions to TriggerDefinition."""

    def __init__(
        self,
        llm_provider: Any | None = None,
        redis_client: Any | None = None,
        cache_ttl: int = 3600,
        llm_provider_resolver: Any | None = None,
    ) -> None:
        self._llm = llm_provider
        # BYOK-3: the calling tenant's provider (tenant BYOK → platform) per parse.
        self._llm_resolver = llm_provider_resolver
        self._redis = redis_client
        self._cache_ttl = cache_ttl

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def resolve(self, description: str, *, tenant_ctx: Any = None) -> TriggerDefinition:
        """Parse *description* and return a TriggerDefinition.

        The LLM fallback is budget-checked and charged to ``tenant_ctx``."""
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
        result = await self._llm_parse(description, tenant_ctx=tenant_ctx)
        await self._cache(description, result)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fast_path(self, description: str) -> TriggerDefinition | None:
        for pattern, cron in _CRON_PATTERNS:
            if pattern.search(description):
                # Keep the matched cron. This used to return a bare
                # ``type="schedule"`` and discard it, so the workflow's schedule
                # trigger had no cron and the beat never fired it.
                return TriggerDefinition(type="schedule", schedule=ScheduleTriggerConfig(cron=cron))
        if any(kw in description.lower() for kw in ("webhook", "http ", "post ", "api ")):
            return TriggerDefinition(type="webhook")
        return None

    async def _get_cached(self, description: str) -> TriggerDefinition | None:
        if self._redis is None:
            return None
        try:
            key = _cache_key(description)
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
            key = _cache_key(description)
            await self._redis.setex(key, self._cache_ttl, trigger.model_dump_json())
        except Exception as exc:
            _log.debug("nl_trigger_cache_write_failed", error=str(exc))

    async def _provider_for(self, tenant_ctx: Any) -> Any | None:
        tenant_id = str(getattr(tenant_ctx, "tenant_id", "") or "")
        if self._llm_resolver is None or not tenant_id:
            return self._llm
        from app.providers.llm_resolution import NoLLMProviderConfiguredError
        from app.providers.tenant_provider import TenantProviderError

        try:
            return await self._llm_resolver(tenant_id)
        except NoLLMProviderConfiguredError:
            return None
        except TenantProviderError as exc:
            raise NLTriggerParseError(f"LLM provider unavailable: {exc}") from exc

    async def _llm_parse(self, description: str, *, tenant_ctx: Any = None) -> TriggerDefinition:
        llm = await self._provider_for(tenant_ctx)
        if llm is None:
            raise NLTriggerParseError(
                "This phrase needs the AI parser, but no LLM provider is configured. "
                f"Try a phrase like: {EXAMPLE_PHRASES}"
            )

        prompt = _PROMPT.format(description=description)
        try:
            from app.providers.base import CompletionRequest, Message
            from app.providers.model_defaults import configured_default_model

            req = CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                # A tenant's own provider uses its configured model ("" → default).
                model=""
                if getattr(llm, "_byok_tenant_id", None)
                else configured_default_model("gpt-4o"),
                max_tokens=256,
                temperature=0.0,
            )
            # Circuit-broken, budget-checked and charged to the tenant like
            # every other narrow LLM decision (was an unmetered raw call).
            from app.providers.guarded_completion import complete_decision

            response = await complete_decision(llm, req, role="nl_trigger", tenant_ctx=tenant_ctx)
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
            return _definition_from_llm(data)
        except Exception as exc:
            _log.warning("nl_trigger_parse_failed", raw=raw_text[:200], error=str(exc))
            raise NLTriggerParseError(
                f"Could not understand this trigger description ({exc}). "
                f"Try a phrase like: {EXAMPLE_PHRASES}"
            ) from exc
