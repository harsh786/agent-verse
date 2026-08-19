"""Ollama local LLM provider.

Ollama exposes an OpenAI-compatible /v1 endpoint for completions, but uses its
own REST API for embeddings (/api/embeddings), model management (/api/tags,
/api/pull, /api/show), and streaming pulls.
"""

from __future__ import annotations

import asyncio
import os
import platform
import subprocess
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
)
from app.providers.openai_compatible import OpenAICompatibleProvider

# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

OLLAMA_MODEL_CATALOG: dict[str, dict[str, Any]] = {
    # Qwen 3 reasoning / general
    "qwen3:0.6b":  {"type": "text", "ram_gb": 1,  "context": 32768,  "capabilities": ["text"]},
    "qwen3:1.7b":  {"type": "text", "ram_gb": 2,  "context": 32768,  "capabilities": ["text"]},
    "qwen3:4b":    {"type": "text", "ram_gb": 4,  "context": 32768,  "capabilities": ["text", "tools"]},
    "qwen3:8b":    {"type": "text", "ram_gb": 6,  "context": 32768,  "capabilities": ["text", "tools"]},
    "qwen3:14b":   {"type": "text", "ram_gb": 10, "context": 32768,  "capabilities": ["text", "tools"]},
    "qwen3:32b":   {"type": "text", "ram_gb": 24, "context": 32768,  "capabilities": ["text", "tools"]},
    # Qwen 2.5 general / coding
    "qwen2.5:7b":  {"type": "text", "ram_gb": 6,  "context": 32768,  "capabilities": ["text", "tools"]},
    "qwen2.5:14b": {"type": "text", "ram_gb": 10, "context": 32768,  "capabilities": ["text", "tools"]},
    "qwen2.5:32b": {"type": "text", "ram_gb": 24, "context": 32768,  "capabilities": ["text", "tools"]},
    # Qwen 2.5-Coder
    "qwen2.5-coder:1.5b": {"type": "text", "ram_gb": 2,  "context": 32768, "capabilities": ["text", "tools"]},
    "qwen2.5-coder:7b":   {"type": "text", "ram_gb": 6,  "context": 32768, "capabilities": ["text", "tools"]},
    "qwen2.5-coder:14b":  {"type": "text", "ram_gb": 10, "context": 32768, "capabilities": ["text", "tools"]},
    "qwen2.5-coder:32b":  {"type": "text", "ram_gb": 24, "context": 32768, "capabilities": ["text", "tools"]},
    # Vision models
    "qwen2.5vl:7b": {"type": "vision", "ram_gb": 6,  "context": 32768, "capabilities": ["text", "vision"]},
    "qwen2-vl:7b":  {"type": "vision", "ram_gb": 6,  "context": 32768, "capabilities": ["text", "vision"]},
    "glm4v:9b":     {"type": "vision", "ram_gb": 7,  "context": 131072, "capabilities": ["text", "vision", "ocr"]},
    # GLM-4 text
    "glm4:9b":      {"type": "text", "ram_gb": 7,  "context": 131072, "capabilities": ["text", "tools", "long_context"]},
    # Moonshot (long context)
    "moonshot:7b":  {"type": "text", "ram_gb": 6,  "context": 131072, "capabilities": ["text", "long_context"]},
    # Embedding models
    "nomic-embed-text":  {"type": "embed", "ram_gb": 1, "dim": 768,  "capabilities": ["embed"]},
    "mxbai-embed-large": {"type": "embed", "ram_gb": 2, "dim": 1024, "capabilities": ["embed"]},
    "bge-m3":            {"type": "embed", "ram_gb": 2, "dim": 1024, "capabilities": ["embed", "rerank"]},
    "all-minilm":        {"type": "embed", "ram_gb": 1, "dim": 384,  "capabilities": ["embed"]},
    "nomic-embed-text:v1.5": {"type": "embed", "ram_gb": 1, "dim": 768, "capabilities": ["embed"]},
}

# Lock to prevent duplicate pulls
_pull_lock: asyncio.Lock | None = None


def _get_pull_lock() -> asyncio.Lock:
    global _pull_lock
    if _pull_lock is None:
        _pull_lock = asyncio.Lock()
    return _pull_lock


class OllamaProvider(OpenAICompatibleProvider):
    """Ollama local LLM provider.

    Completion/streaming uses the OpenAI-compatible /v1 endpoint.
    Embeddings use Ollama's native /api/embeddings endpoint.
    """

    provider_name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        default_model: str = "qwen3:8b",
        default_embed_model: str = "nomic-embed-text",
    ) -> None:
        _raw_base = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") or "http://localhost:11434"
        self._base = _raw_base.rstrip("/")
        super().__init__(
            api_key="ollama",  # Ollama does not require a real key
            base_url=f"{self._base}/v1",
            default_model=default_model,
            supports_vision_flag=False,
        )
        self._default_embed_model = default_embed_model

    # ------------------------------------------------------------------
    # Completion — inherited from OpenAICompatibleProvider
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Embeddings — native Ollama API
    # ------------------------------------------------------------------

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        """Embed a list of texts using Ollama's /api/embeddings endpoint."""
        model = request.model or self._default_embed_model
        embeddings: list[list[float]] = []
        async with httpx.AsyncClient(timeout=120.0) as client:
            for text in request.texts:
                resp = await client.post(
                    f"{self._base}/api/embeddings",
                    json={"model": model, "prompt": text},
                )
                resp.raise_for_status()
                embeddings.append(resp.json()["embedding"])
        return EmbedResponse(embeddings=embeddings, model=model)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch embed via Ollama. Runs concurrently (max 8 at a time)."""
        if not texts:
            return []
        sem = asyncio.Semaphore(8)
        model = self._default_embed_model

        async def _one(text: str) -> list[float]:
            async with sem:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(
                        f"{self._base}/api/embeddings",
                        json={"model": model, "prompt": text},
                    )
                    resp.raise_for_status()
                    return resp.json()["embedding"]  # type: ignore[no-any-return]

        return list(await asyncio.gather(*[_one(t) for t in texts]))

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    async def list_local_models(self) -> list[dict[str, Any]]:
        """Return locally available models from /api/tags."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self._base}/api/tags")
            resp.raise_for_status()
            return resp.json().get("models", [])  # type: ignore[no-any-return]

    async def pull_model(self, model: str) -> AsyncIterator[dict[str, Any]]:
        """Stream model pull progress from /api/pull.

        Yields dicts with keys: status, digest, total, completed.
        """
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self._base}/api/pull",
                json={"name": model, "stream": True},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.strip():
                        import json as _json
                        yield _json.loads(line)

    async def get_model_info(self, model: str) -> dict[str, Any]:
        """Return model metadata from /api/show."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self._base}/api/show",
                json={"name": model},
            )
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # RAM validation
    # ------------------------------------------------------------------

    def validate_ram(self, model: str) -> tuple[bool, str]:
        """Check whether the host has sufficient RAM for *model*.

        Returns (ok, message). Falls back to True on unknown models or when
        RAM detection is not available.
        """
        info = OLLAMA_MODEL_CATALOG.get(model)
        if info is None:
            return True, f"Unknown model {model!r} — RAM check skipped"

        required_gb: float = info.get("ram_gb", 0)
        available_gb = _get_available_ram_gb()
        if available_gb is None:
            return True, "RAM detection unavailable — assuming sufficient memory"

        if available_gb >= required_gb:
            return True, f"{available_gb:.1f} GB available ≥ {required_gb} GB required for {model}"
        return (
            False,
            f"Insufficient RAM: {available_gb:.1f} GB available, {required_gb} GB required for {model}",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url_for_model(self, model: str) -> str:
        """Return the appropriate base URL segment based on model capabilities."""
        info = OLLAMA_MODEL_CATALOG.get(model, {})
        caps = info.get("capabilities", [])
        if "vision" in caps:
            return f"{self._base}/v1"  # vision models still use OpenAI-compat path
        if "embed" in caps:
            return f"{self._base}/api"  # embeddings use native API
        return f"{self._base}/v1"

    async def _ensure_model(self, model: str) -> None:
        """Pull *model* if it is not present locally. Thread-safe via asyncio lock."""
        local = {m["name"] for m in await self.list_local_models()}
        if model in local:
            return
        async with _get_pull_lock():
            # Re-check after acquiring lock
            local = {m["name"] for m in await self.list_local_models()}
            if model in local:
                return
            # Consume the pull iterator to completion
            async for _ in self.pull_model(model):
                pass

    def supports_vision(self) -> bool:
        """Vision support depends on the active model; conservatively False."""
        return False

    def supports_tool_use(self) -> bool:
        return True

    def supports_structured_output(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_available_ram_gb() -> float | None:
    """Return available system RAM in GB, or None if undetectable."""
    try:
        system = platform.system()
        if system == "Darwin":
            # macOS: use vm_stat
            out = subprocess.check_output(["vm_stat"], text=True)
            pages_free = 0
            pages_inactive = 0
            for line in out.splitlines():
                if "Pages free" in line:
                    pages_free = int(line.split(":")[1].strip().rstrip("."))
                elif "Pages inactive" in line:
                    pages_inactive = int(line.split(":")[1].strip().rstrip("."))
            page_size = 16384  # 16 KB on M-series Macs
            return (pages_free + pages_inactive) * page_size / (1024**3)
        elif system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        kb = int(line.split()[1])
                        return kb / (1024**2)
    except Exception:
        pass
    return None
