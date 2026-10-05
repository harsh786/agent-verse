# Live real-e2e program (sequential, 2026-10-05)

Goal (user): every capability verified **in depth on the live stack** with complex and difficult real
use cases, every failure fixed at the root, one area at a time.

Mechanics per phase (one agent, in its own worktree off `main`):
1. Redeploy the live compose stack from the phase branch (`docker-compose -f infra/docker-compose.yml up -d --build`
   for backend/worker/beat/frontend only; never drop volumes).
2. Run the existing `tests/real_world` scenarios for the area (`scripts/run_real_world.sh`, `RW_ONLY=...`).
3. Add the missing complex/difficult scenarios for the area (real HTTP against the stack, realistic fixtures,
   assert real outcomes: content, counts, citations, statuses, audit rows, timings).
4. Fix every failure at the root (TDD unit/integration test + the live scenario), commit per fix.
5. Report → `docs/audits/fixwave/live/<phase>.md` (scenario verdicts, metrics, fixes, open items).
6. I merge the phase into local `main`, rerun unit/vitest, redeploy, next phase. Nothing is pushed without the user.

| Phase | Areas |
|---|---|
| P0 | Finalize main (USR-1..7), redeploy, full real-world baseline report |
| P1 | Knowledge base, ingestion (all sources), multi-format processing, chunking, embeddings |
| P2 | Retrieval, all RAG patterns/strategies, hallucination/grounding |
| P3 | Scheduling (all schedule types), triggering (all trigger types + channels) |
| P4 | Workflows (all step types), approvals, HITL |
| P5 | Goals, agent core, agent patterns, multi-model routing, agents lifecycle |
| P6 | Agent memories, self-improvement |
| P7 | Evals, golden datasets, rollout gate |
| P8 | Guardrails, governance, grants |
| P9 | Scalability: million-document ingestion/retrieval load, multi-worker |
| P10 | Frontend e2e (Playwright against the live backend) |
| P11 | Full real-world re-run of every scenario + final report |
