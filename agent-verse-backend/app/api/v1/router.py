"""AgentVerse v1 API router."""
from fastapi import APIRouter

v1_router = APIRouter(prefix="/v1", tags=["v1"])

@v1_router.get("/health")
async def health_v1() -> dict:
    return {"status": "ok", "version": "1", "api": "agentverse"}

@v1_router.get("/info")
async def api_info() -> dict:
    return {
        "version": "1.0.0",
        "deprecation_policy": "6 months notice before breaking changes",
        "changelog_url": "https://docs.agentverse.ai/api/changelog",
    }
