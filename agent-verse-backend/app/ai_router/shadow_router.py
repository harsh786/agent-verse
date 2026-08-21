"""Shadow Router — fire requests to a candidate model alongside the primary.

Shadow routing lets you compare a new model's responses against the production
model without any user impact. Only the primary response is returned to the
caller; shadow results are stored for offline analysis.

Use cases:
- Evaluate a cheaper model before switching
- Validate that a new model version produces equivalent quality
- A/B experiment data collection
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.providers.base import CompletionRequest, CompletionResponse, LLMProvider


@dataclass
class ShadowRoutingConfig:
    enabled: bool = False
    shadow_provider_id: str = ""
    sample_rate: float = 0.1  # 0.0–1.0: fraction of requests to shadow


@dataclass
class ShadowResult:
    primary_response: CompletionResponse
    shadow_response: CompletionResponse | None
    latency_primary_ms: float
    latency_shadow_ms: float | None
    shadow_provider_id: str
    fired_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class ShadowRouter:
    """Fire primary + shadow requests concurrently; return primary to caller.

    The shadow call is always best-effort — if it fails, the primary
    response is still returned cleanly.

    Parameters
    ----------
    config : ShadowRoutingConfig
        Controls whether shadowing is enabled and the sample rate.
    log_buffer_size : int
        Number of shadow results to keep in-memory for the `/shadow-log` API.
    """

    def __init__(
        self,
        config: ShadowRoutingConfig | None = None,
        log_buffer_size: int = 100,
    ) -> None:
        self._config = config or ShadowRoutingConfig()
        self._log: list[ShadowResult] = []
        self._log_size = log_buffer_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def shadow_call(
        self,
        request: CompletionRequest,
        primary_provider: LLMProvider,
        shadow_provider: LLMProvider | None = None,
    ) -> CompletionResponse:
        """Fire primary (and optionally shadow) call; return primary response.

        The shadow call is fired concurrently with the primary but its result
        is never returned to the caller. Shadow failures are silently ignored.

        Parameters
        ----------
        request : CompletionRequest
            The completion request to send.
        primary_provider : LLMProvider
            The production model provider.
        shadow_provider : LLMProvider | None
            The candidate model. If None or config is disabled, behaves as a
            plain primary call.
        """
        should_shadow = self._config.enabled and shadow_provider is not None and self._sample()

        if not should_shadow:
            return await primary_provider.complete(request)

        # Fire both concurrently
        t_start = time.monotonic()
        primary_task = asyncio.create_task(primary_provider.complete(request))
        shadow_task = asyncio.create_task(shadow_provider.complete(request))

        # Await primary — always return this
        primary_response = await primary_task
        t_primary = (time.monotonic() - t_start) * 1000.0

        # Collect shadow result (non-blocking timeout)
        shadow_response = None
        t_shadow = None
        try:
            t_shadow_start = time.monotonic()
            shadow_response = await asyncio.wait_for(shadow_task, timeout=30.0)
            t_shadow = (time.monotonic() - t_shadow_start) * 1000.0
        except Exception:
            shadow_task.cancel()

        # Log result
        result = ShadowResult(
            primary_response=primary_response,
            shadow_response=shadow_response,
            latency_primary_ms=t_primary,
            latency_shadow_ms=t_shadow,
            shadow_provider_id=self._config.shadow_provider_id,
        )
        self._record(result)

        return primary_response

    def get_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent shadow results as serialisable dicts."""
        return [
            {
                "fired_at": r.fired_at,
                "shadow_provider": r.shadow_provider_id,
                "latency_primary_ms": r.latency_primary_ms,
                "latency_shadow_ms": r.latency_shadow_ms,
                "primary_content": (r.primary_response.content or "")[:200],
                "shadow_content": (r.shadow_response.content or "")[:200]
                if r.shadow_response
                else None,
            }
            for r in self._log[-limit:]
        ]

    def configure(self, config: ShadowRoutingConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sample(self) -> bool:
        """Return True with probability equal to sample_rate."""
        import random

        return random.random() < self._config.sample_rate

    def _record(self, result: ShadowResult) -> None:
        self._log.append(result)
        if len(self._log) > self._log_size:
            self._log = self._log[-self._log_size :]
