"""Bounded, charged LLM access for coordination pattern drivers.

Every call goes through :func:`app.providers.guarded_completion.complete_decision`
(circuit breaker, timeout, tenant budget preflight + charge). A run has a hard call
ceiling so a misbehaving pattern cannot spend without bound.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.providers.base import CompletionRequest, Message
from app.providers.guarded_completion import complete_decision


class PatternCallLimitError(RuntimeError):
    """The run exhausted its LLM call ceiling."""


def provider_model(provider: Any) -> str:
    for attr in ("default_model", "_default_model", "model"):
        value = getattr(provider, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def parse_json_object(raw: str) -> dict[str, Any]:
    """The first JSON object in *raw* (fences and prose tolerated); ``{}`` if none."""
    text = _FENCE.sub("", (raw or "").strip())
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except ValueError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : index + 1])
                    except ValueError:
                        break
                    return value if isinstance(value, dict) else {}
        start = text.find("{", start + 1)
    return {}


def string_list(value: Any, *, limit: int = 12, width: int = 500) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        text = str(item).strip()[:width]
        if text and text not in out:
            out.append(text)
    return tuple(out[:limit])


class PatternLLM:
    def __init__(
        self,
        provider: Any,
        *,
        tenant_ctx: Any,
        pattern: str,
        max_calls: int,
        goal_id: str | None = None,
    ) -> None:
        self._provider = provider
        self._tenant_ctx = tenant_ctx
        self._pattern = pattern
        self._max_calls = max_calls
        # Inside a goal every call is charged to that goal's budget (and a denial
        # raises DecisionBudgetExceededError), not only to the tenant's daily one.
        self._goal_id = goal_id
        self.calls = 0
        self.tokens = 0
        self.cost_usd = 0.0

    async def text(
        self,
        prompt: str,
        *,
        step: str,
        max_tokens: int = 800,
        provider: Any = None,
        model: str | None = None,
    ) -> str:
        """One charged call; *provider*/*model* route it to a specific deployment."""
        if self.calls >= self._max_calls:
            raise PatternCallLimitError(
                f"{self._pattern} run exhausted its {self._max_calls}-call LLM ceiling"
            )
        self.calls += 1
        target = provider if provider is not None else self._provider
        role = f"coordination_{self._pattern}_{step}"
        if not model:
            # A specific deployment (MoA proposer) keeps its own model; the run's
            # provider serves the role's model (role_preference: saved order >
            # env pin > the provider's default).
            if provider is not None:
                model = provider_model(target)
            else:
                from app.ai_router.role_preference import resolve_role_model

                model = resolve_role_model(role, provider=target) or provider_model(target)
        response = await complete_decision(
            target,
            CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model=model,
                max_tokens=max_tokens,
            ),
            role=role,
            tenant_ctx=self._tenant_ctx,
            goal_id=self._goal_id,
        )
        input_tokens = int(getattr(response, "input_tokens", 0) or 0)
        output_tokens = int(getattr(response, "output_tokens", 0) or 0)
        self.tokens += input_tokens + output_tokens
        try:
            from app.intelligence.cost_tracker import calculate_cost

            served = str(getattr(response, "model", "") or model or provider_model(target))
            self.cost_usd += float(calculate_cost(served, input_tokens, output_tokens))
        except Exception:  # cost is reported, the charge itself happened above
            pass
        return str(getattr(response, "content", "") or "").strip()

    async def json(self, prompt: str, *, step: str, max_tokens: int = 800) -> dict[str, Any]:
        return parse_json_object(
            await self.text(
                prompt + "\nRespond with a single JSON object only.",
                step=step,
                max_tokens=max_tokens,
            )
        )


__all__ = [
    "PatternCallLimitError",
    "PatternLLM",
    "parse_json_object",
    "provider_model",
    "string_list",
]
