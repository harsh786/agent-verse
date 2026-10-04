# Fix wave — agent brief (restored after machine restart)

You fix one package of verified open gaps in the AgentVerse monorepo, in YOUR OWN git worktree
(never edit /Users/harsh/Documents/Learning/agent-verse directly, never touch Docker containers or the running local services).

Setup in your worktree:
- Backend: `cd agent-verse-backend && uv sync` then `uv run ...` (Python 3.12). Read the repo-root CLAUDE.md.
- Frontend (only if needed): `ln -s /Users/harsh/Documents/Learning/agent-verse/agent-verse-frontend/node_modules agent-verse-frontend/node_modules`
  then `npx vitest run <files>`, `npm run typecheck`. Never `git add` the symlink.

For EACH item, in order of severity (critical, high, medium, low):
1. Verify the defect still exists at the current code. If not, mark it `already_fixed` with the reason.
2. Write a failing test first (pytest under tests/<package>/ mirroring the source, or vitest next to the component).
3. Fix backend and, where needed, frontend.
4. Run the targeted tests you touched plus directly related files. ruff + mypy (strict) clean on changed Python; typecheck clean for frontend.
5. Commit that item alone: `fix(<area>): <ID> <short title>`, body ending with
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
6. Update your progress file (path in your prompt) — valid JSON rewritten after every item:
   `{"<ID>": {"status": "done|already_fixed|skipped|blocked", "commit": "<sha or null>", "note": "<1 line>"}}`

Rules:
- Never weaken a security check or make something fail open to get a test green. Fail closed with an honest error. No fake success.
- Multi-replica correctness: state that must be shared lives in Postgres/Redis, not process memory. Million-scale: bounded queries, keyset pagination, indexes, no whole-table loads.
- Tests must not depend on the developer .env or on real LLMs; tests can never reach the live local Postgres/Redis (conftest enforces it).
- Docker-backed tests (testcontainers) at most one file at a time, with
  DOCKER_HOST="unix:///Users/harsh/.colima/default/docker.sock" TESTCONTAINERS_RYUK_DISABLED=true. Never run the whole suite.
- New dependencies: commit pyproject.toml + uv.lock (keep the CPU-only torch index).
- New defects outside your items: list them under "_new_findings" in the progress file.
- Commit after every item so an interruption loses at most one item.

Final reply (<= 20 lines): counts per status, branch, commit count, skipped/blocked with reasons, owner decisions, new findings.
