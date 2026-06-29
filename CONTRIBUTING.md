# Contributing to AgentVerse

Thank you for contributing to AgentVerse — a vendor-agnostic, multi-tenant OS for autonomous AI agents.

## Quick Start

```bash
git clone https://github.com/agentverse/agent-verse
cd Agent-Verse

# Backend
cd agent-verse-backend
uv sync
cp .env.example .env
# Edit .env and set at least one LLM API key
colima start  # macOS — start Docker VM
docker-compose -f infra/docker-compose.yml up -d postgres redis
uv run alembic upgrade head
uv run uvicorn app.main:app --reload

# Frontend (separate terminal)
cd agent-verse-frontend
npm install
npm run dev
```

## Development Rules

1. **TDD**: Write tests first. Run `uv run pytest` (backend) or `npm run test` (frontend) before committing.
2. **No breaking changes** to existing API endpoints. Add new endpoints; version changed ones.
3. **No direct DB queries in routers** — use the service layer (`app/services/`).
4. **All LLM calls go through `LLMProvider` protocol** — never import provider SDKs directly in business logic.
5. **Tenant isolation**: Every DB query must go through `rls_context()` or use tenant-scoped service methods.
6. **No `localStorage` for API keys** — use `sessionStorage` via `setApiKey()` from `client.ts`.

## Backend Conventions

- `uv run pytest` — run all tests (10,000+)
- `uv run ruff check .` — linting (max line 100, ruff rules E,F,I,N,UP,B,A,C4,SIM,RUF)
- `uv run mypy app/` — type checking (strict mode + pydantic plugin)
- New services: construct in `create_app()`, bind to `app.state`, upgrade in `lifespan`
- New DB model: create file in `app/db/models/`, add migration with `uv run alembic revision --autogenerate -m "msg"`
- New API router: create in `app/api/`, include in `main.py` with proper prefix + tags

## Frontend Conventions

- `npm run test` — run Vitest unit tests
- `npm run typecheck` — TypeScript strict mode (0 errors required)
- **Design tokens only**: `bg-card`, `border-border`, `text-foreground`, `text-muted-foreground`. Never `bg-gray-*` or `text-gray-*`.
- **Typed client**: All API calls through `src/lib/api/client.ts`. Never inline `fetch`.
- **Auth store**: `useAuthStore(s => s.apiKey)` for API key. Never `localStorage.getItem`.
- New page: create in `src/features/<name>/`, add route to `App.tsx`, add nav link to `Sidebar.tsx`, add test file.

## Pull Request Process

1. Fork and create a branch: `git checkout -b feat/my-feature`
2. Write tests first
3. Implement the feature
4. Run full test suite (`uv run pytest -m "not slow"` + `npm run test`)
5. Run linting + type check
6. Submit PR against `main` with clear description of:
   - What changed
   - Why (link to issue if applicable)
   - How to test manually
   - Any migration steps

## Reporting Issues

Use [GitHub Issues](https://github.com/agentverse/agent-verse/issues). Include:
- Environment (OS, Python version, Node version)
- Steps to reproduce
- Expected vs actual behavior
- Logs if applicable

## License

Apache 2.0 — see [LICENSE](LICENSE).
