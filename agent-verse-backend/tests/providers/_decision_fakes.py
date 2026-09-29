"""Shared fakes for the narrow-decision call tests (guarded_completion)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.providers.base import CompletionRequest, CompletionResponse


class ScriptedProvider:
    """Replies with ``reply``; ``hang`` never returns; ``fail`` raises."""

    def __init__(self, reply: str = "", *, hang: bool = False, fail: bool = False) -> None:
        self._default_model = f"test-model-{uuid.uuid4().hex[:8]}"
        self.reply, self.hang, self.fail = reply, hang, fail
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        if self.hang:
            await asyncio.sleep(30)
        if self.fail:
            raise ConnectionError("provider down")
        return CompletionResponse(
            content=self.reply, model="gpt-4o-mini", input_tokens=400, output_tokens=20
        )


class RecordingController:
    def __init__(self, *, allow: bool = True, remaining: bool = True) -> None:
        self.allow, self.remaining = allow, remaining
        self.recorded: list[tuple[str, str]] = []

    async def check_and_record(self, *, goal_id: str, cost_usd: float, tenant_ctx: Any) -> bool:
        self.recorded.append((goal_id, tenant_ctx.tenant_id))
        return self.allow

    async def ahas_remaining_budget(self, *, tenant_ctx: Any) -> bool:
        return self.remaining
