# AgentVerse — Backend

Vendor-agnostic, multi-tenant **operating system for autonomous AI agents**. An agent
receives a natural-language goal, plans its own execution, calls real-world tools via MCP,
verifies the result, and replans on failure — with **zero hardcoded workflows**.

This is **Project 1** of two independently deployable projects (the other is
`agent-verse-frontend`). The backend is the source of truth and publishes an OpenAPI
contract consumed by the frontend over HTTP/SSE/WebSocket.

## Stack

Python 3.12 · FastAPI · LangGraph · Celery + Redis · PostgreSQL 16 + pgvector + RLS ·
OpenTelemetry + Prometheus · Anthropic (default LLM provider, vendor-agnostic protocol).

## Development

```bash
uv sync                 # install deps into .venv (pinned via uv.lock)
uv run pytest           # run the test suite
uv run ruff check .     # lint
uv run mypy app         # type-check
uv run uvicorn app.main:app --reload   # run the API locally
```

See the implementation plan for the phased roadmap and the 47-component architecture.
