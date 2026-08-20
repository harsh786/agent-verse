"""AssemblyAI STT — requires ASSEMBLY_AI_KEY."""
from __future__ import annotations

import asyncio
import os

from app.voice.providers.base import TranscriptResult


class AssemblyAISTT:
    provider_name:      str  = "assemblyai"
    supports_streaming: bool = False

    def __init__(self) -> None:
        self._ready = False

    async def warmup(self) -> None:
        self._ready = bool(os.getenv("ASSEMBLY_AI_KEY"))

    async def is_ready(self) -> bool:
        return self._ready

    async def transcribe(self, audio_bytes: bytes, content_type: str) -> TranscriptResult:
        import httpx
        key     = os.getenv("ASSEMBLY_AI_KEY", "")
        headers = {"authorization": key}
        if not key:
            raise RuntimeError("ASSEMBLY_AI_KEY not set for assemblyai provider")
        async with httpx.AsyncClient(timeout=120) as client:
            up = await client.post(
                "https://api.assemblyai.com/v2/upload",
                headers={**headers, "content-type": "application/octet-stream"},
                content=audio_bytes,
            )
            up.raise_for_status()
            sub = await client.post(
                "https://api.assemblyai.com/v2/transcript",
                headers=headers,
                json={"audio_url": up.json()["upload_url"]},
            )
            sub.raise_for_status()
            job_id = sub.json()["id"]
            for _ in range(30):
                await asyncio.sleep(2)
                poll = await client.get(
                    f"https://api.assemblyai.com/v2/transcript/{job_id}", headers=headers
                )
                poll.raise_for_status()
                data = poll.json()
                if data["status"] == "completed":
                    return TranscriptResult(
                        transcript=data.get("text", ""),
                        language="en",
                        confidence=0.9,
                        provider=self.provider_name,
                    )
                if data["status"] == "error":
                    raise RuntimeError(data.get("error", "AssemblyAI error"))
        raise TimeoutError("AssemblyAI transcription timed out")
