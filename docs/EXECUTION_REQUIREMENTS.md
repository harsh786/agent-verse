# AgentVerse Execution Requirements

## Minimum required for any goal to execute

### 1. LLM Provider (mandatory — without this, only FakeProvider runs)
Set ONE of:
```
ANTHROPIC_API_KEY=sk-ant-...         # Claude 3.5 Sonnet recommended
OPENAI_API_KEY=sk-proj-...           # GPT-4o recommended
GOOGLE_API_KEY=...                   # Gemini 2.5 Pro
OLLAMA_BASE_URL=http://localhost:11434  # Local models
```

### 2. Embedder (needed for RAG, semantic cache, LTM recall)
Set ONE of:
```
VOYAGE_API_KEY=pa-...               # Best for semantic search
OPENAI_API_KEY=sk-proj-...          # text-embedding-3-small
SENTENCE_TRANSFORMERS_MODEL=all-MiniLM-L6-v2  # Local, no API key
```

### 3. Postgres with pgvector (needed for KB, execution memory, audit, eval persistence)
```
DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse
```

### 4. Redis (needed for HITL approval, rate limiting, semantic cache, checkpointing)
```
REDIS_URL=redis://localhost:6379/0
```

## For specific goal types

### RPA / Browser goals
```bash
playwright install chromium
```

### Web search goals
```
SEARXNG_URL=http://localhost:8080  # Self-hosted SearxNG
```
Or register a web-search MCP connector pointing to any search API.

### Multimodal goals (PDF/audio/video)
- Install: `pip install pdfminer.six pymupdf` for PDF
- Install: `pip install openai-whisper` for audio transcription
- Set `OPENAI_API_KEY` for vision (GPT-4V) and audio models

## Dynamic Orchestration feature flags
Enable new orchestration layers incrementally:
```
DYNAMIC_ORCHESTRATION=true       # Enable RuntimeProfileBuilder
AGENTIC_RAG=true                 # Enable RetrieverTool + source inventory
PLAN_VERIFICATION=true           # Enable PlanVerifier before execution
GUARDRAIL_PROFILE=true           # Enable dynamic guardrail bundle selection
READINESS_GATE=true              # Enable ReadinessGate check before goals
RUNTIME_SCORECARD=true           # Enable RuntimeScorecard after completion
```

## Quick start (development)
```bash
colima start
docker-compose -f infra/docker-compose.yml up -d postgres redis

export ANTHROPIC_API_KEY=sk-ant-...
export VOYAGE_API_KEY=pa-...
export DATABASE_URL=postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse
export REDIS_URL=redis://localhost:6379/0
export DYNAMIC_ORCHESTRATION=true

uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

## Goal type execution matrix

| Goal type | LLM key | Embedder | Postgres | Redis | Web | Works? |
|-----------|---------|----------|----------|-------|-----|--------|
| Simple CRUD (via MCP) | required | optional | optional | optional | no | yes |
| Complex research | required | recommended | recommended | recommended | yes | yes |
| Coding / analysis | required | no | no | no | no | yes |
| High-risk (HITL) | required | optional | required | required | no | yes |
| Multi-agent | required | optional | recommended | required | no | yes |
| KB-grounded | required | required | required | optional | no | yes |
| RPA / Browser | required | optional | optional | recommended | optional | yes |
| Long-horizon | required | optional | required | required | no | yes |
| Multimodal PDF | required | required | required | optional | no | yes* |
| Multimodal audio | required | optional | required | optional | no | yes* |

*Requires multimodal model (GPT-4V / Claude 3.5 Sonnet with vision)
