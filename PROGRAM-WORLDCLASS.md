# AgentVerse → World-Class Program (execution roadmap)

**Owner note:** this is the durable, resumable plan for the world-class push requested 2026-09-10, extending `HANDOFF.md`. A fresh agent executes waves top-to-bottom. Every wave is TDD + ends in an e2e/automation gate. Branch: `feature/platform-10x`. Conventions & gotchas: see `HANDOFF.md` §5 (never `git add -A`; `uv run`; run tests to a file; one implementer per tree; commit before turn ends).

Ratings baseline (recon 2026-09-10): Org 8, JARVIS-UI 8, HITL 8, OCR 8, RPA 7, Coordination 6, Civilization 5. Target: every one → 10/10, proven e2e.

---

## Priority order (rulings)
1. **WS-0 BK6** — self-optimizer disposition (in flight).
2. **WS-1 Civilization throttle** — concrete latent bug (30s discovery, no lock → backlog). Small, high value.
3. **WS-2 Org backend WORLD-CLASS (any-task execution)** — the org backend must genuinely execute ANY NL objective (dynamic team + real goal dispatch + decomposition/handoff + tool access + verified deliverable + rich events for the frontend), plus de-fake analytics + finish RBAC/quality gates. Backend half of the WS-7 awe integration.
4. **WS-3 HITL flawless everywhere** — one gateway, proven pause→approve→resume in goal / org-mission / workflow / agent-step. THE user's top emphasis.
5. **WS-4 Workflow+Trigger+Schedule unified + real Celery e2e** — add Celery worker to e2e compose; prove trigger→workflow→celery run→terminal, scheduled fire governed, and a HITL gate INSIDE a workflow.
6. **WS-5 RPA → report + PDF** — scrape → structured report → rendered PDF artifact.
7. **WS-6 OCR universal ingestion** — any unreadable format → rasterize to image → OCR (LLM-vision fallback) → structured report.
8. **WS-7 AI Org frontend AWE** — spawn/handoff/bots-moving animations, motion polish, JARVIS speaking (TTS on events).
9. **WS-8 Full e2e + Playwright automation suite** — real-backend Playwright for every major feature + backend e2e_full expansion; wire into CI tiers.

Run one backend + one frontend implementer concurrently (disjoint trees). WS-7 (frontend) can run in parallel with any backend WS.

---

## WS-1 · Civilization throttle (backend)
**Gap:** `app/civilization/` discovery/tick task fires ~every 30s with no throttle/lock → beat backlog (bit prior sessions); 17 bare `pass` stubs.
**Tasks:**
- Add a Redis-based distributed lock + min-interval guard around the discovery/tick task (mirror any existing lock pattern in `app/scaling/` or `app/triggers/`). If already running/too-soon → skip cleanly, emit a metric.
- Triage the 17 bare `pass` stubs: implement the meaningful ones, or make them explicit no-ops with a reason (no silent stubs).
**DoD:** test proving a second tick within the interval is skipped (lock held); mypy clean; fast tier green. **e2e:** a civilization tick under a simulated backlog does not pile up.

## WS-2 · Org backend WORLD-CLASS — any-task autonomous execution (backend)
**Intent (user):** the AI org team backend must be world-class and able to do **ANY type of task**, not just hardcoded industry blueprints — and it must emit the rich events the frontend (WS-7) animates (decomposition, assignment, handoff, tool use, progress, completion). The frontend awe is only real if the backend genuinely does the work.

### 2a — Verify the any-task execution path end-to-end (Step 1 deliverable)
Trace `app/org/service.py::create_mission_and_execute` and the composer (`compose_from_nl` / team formation). Answer with evidence:
- For an **arbitrary NL objective** (not a known industry template), does it (i) decompose into real subtasks, (ii) form a fit team (roles/departments) dynamically, (iii) dispatch **real** goals to `GoalService.submit_goal` that the agent loop actually executes (plan→execute→verify→complete) with tool/MCP access, (iv) handle handoffs between agents/teams, (v) track progress from **real** `OrgTask` status, (vi) produce a verified deliverable/report? Mark each REAL/PARTIAL/STUB.

### 2b — Close the capability gaps found
- **Arbitrary-domain composition:** if team/dept composition is limited to hardcoded blueprints, add a generalized LLM-driven composer that produces a sensible team for any objective (degrade to a sane default team when no LLM). One reachable composer.
- **Real task decomposition + assignment + handoff:** mission → subtasks → assigned to agents → executed as real goals → handoff events on reassignment. No simulated/`pass` steps.
- **Tool/MCP access for org agents:** confirm dispatched org goals run with the tenant's MCP tools (so agents can actually accomplish work), not a restricted stub set.
- **Deliverable + report:** a completed mission yields a real aggregated result/report (ties to RPA→PDF WS-5 for document output where relevant).
- **Rich event emission:** emit `mission.*`, `task.decomposed`, `task.assigned`, `task.handoff`, `agent.working`, `mission.progress`, `mission.completed` on the org Redis→SSE channel so WS-7 animates real activity. Coordinate event names with WS-7.

### 2c — De-fake + governance (original WS-2)
- `app/org/analytics.py`: replace the 7 hardcoded constants with metrics computed from real mission/task/agent/cost data (throughput, success rate, cost, cycle time) — honest empty/indeterminate when no data, never a fabricated number.
- Finish `rbac.py` (1 TODO) + `quality_gates.py` (2 TODOs): real RBAC enforcement + quality-gate peer-review, or explicit rejection if unsupported.

**DoD:** an arbitrary NL mission executes real goals to completion with dynamic team + real progress + emitted events; analytics reflect seeded data; RBAC permits/denies correctly; mypy clean; fast tier green. **e2e (`tests/e2e_full/test_org_any_task_e2e.py`):** submit an arbitrary objective → team forms → real goal(s) dispatched and reach terminal → mission completes with a real result → the decomposition/handoff/progress events were published. This is also the backend half of the WS-7 "awe" integration.
**Size note:** this is large — split into 2a(verify)→2b(build, possibly multiple commits)→2c if one implementer can't hold it; the controller sequences the sub-tasks.

## WS-3 · HITL flawless in every execution path (backend, THE priority)
**Gap:** HITL gateway is real + DB-persisted, `HITL_REQUIRED` honored in `executor_mixin`, but live pause→resume not traced across all paths.
**Tasks — one HITL gateway, four entrypoints, each proven:**
1. **Goal execution:** high-risk step → pause (status `waiting_human`) → approve via API → resume → complete.
2. **AI Org mission:** a mission whose agent hits a gated action pauses at the org level and surfaces to the approvals inbox; approve → resume.
3. **Workflow run:** a workflow step requiring approval pauses the run (`WorkflowRunStatus.PAUSED`/waiting) → approve → resume to terminal.
4. **Agent step:** tool-risk `HITL_REQUIRED` / guardrail-gated tool call pauses and routes to the queue → approve → executes.
For any path where pause/resume is not truly wired, wire it. Reject/deny path also tested (deny → step blocked, goal fails cleanly).
**DoD:** unit + integration for each path; mypy clean. **e2e (`tests/e2e_full/test_hitl_all_paths_e2e.py`):** all four paused-then-resumed, plus one deny.

## WS-4 · Workflow + Trigger + Schedule, unified, real Celery (backend)
**Gap:** `infra/docker-compose.e2e.yml` has NO Celery worker (in-process ASGI); no real distributed run proof; scheduled-fire governance parity.
**Tasks:**
- Add a Celery worker service to the e2e compose consuming `workflows.*,schedules,goals,maintenance`.
- Prove the integrated product: **trigger → workflow → Celery run → real steps → terminal**, **scheduled fire is dispatcher-governed** (dedup/rate/circuit), and a **HITL gate inside a workflow** pauses+resumes (ties to WS-3).
**DoD:** e2e `test_workflow_run_e2e.py` (2 real step rows, no `{"_mock":true}`), `test_scheduled_trigger_is_governed.py`, `test_trigger_fire_e2e.py` (dedup on replay). mypy clean.

## WS-5 · RPA → report + PDF (backend)
**Gap:** RPA executor real, but "scrape → report → PDF" deliverable not confirmed.
**Tasks:** after an RPA scrape, assemble a structured report (extracted data + provenance + screenshots), and render it to a **PDF artifact** stored via the artifact store. Reuse an existing PDF lib if present (check deps: reportlab/weasyprint/fitz); if none, pick one and pin it. Expose retrieval of the generated PDF.
**DoD:** test that a scrape run produces a non-empty PDF artifact with the scraped content; mypy clean. **e2e:** RPA job on a fixture page → downloadable PDF report.

## WS-6 · OCR universal ingestion (backend)
**Gap:** OCR invoked from ingestion (ING-11 closed) but only for image/scanned-PDF; "any type → image → OCR" not universal; no ingestion-level OCR-fallback test.
**Tasks:** for a document whose native parser yields no/low text AND which OCR can't read directly, **rasterize to image(s)** (PDF→image via pdf2image/fitz; office docs→PDF→image if a converter is available, else record an honest "unsupported for OCR" degradation) and run `OcrEngine` (LLM-vision as the fallback tier). Produce a structured OCR report merged with any VisionParser description. Never silently drop content.
**DoD:** unit tests for the rasterize→OCR path per input class; degradation recorded in result metadata. **e2e:** an image-only PDF + a PNG with text ingest to retrievable OCR'd text; OCR fallback asserted in metadata.

## WS-7 · AI Org frontend AWE (frontend) — runs parallel to backend WSs
**Gap:** viz is REAL (d3-force AgentConstellation, particles, flowing beams, voice) but rated 8 — needs the "awe" layer the user described: bots moving, task-handoff animation, spawn motion, JARVIS speaking.
**Tasks (build on existing, don't rewrite):**
- **Agent spawn animation:** new agent nodes animate in (scale/fade/particle burst) on `agent.activated`.
- **Task-handoff animation:** when a task moves between agents/teams, animate a labeled packet/beam traveling the edge (framer-motion / SVG SMIL), with a subtle trail.
- **Bots moving / alive:** idle agents drift/pulse; active agents show a working state (spinner ring, activity glow); mission progress animates.
- **JARVIS speaking:** TTS announces key events (mission started, team formed, agent activated, HITL needed, mission complete) via the existing voice endpoints — debounced, mutable, respects `prefers-reduced-motion` and a user toggle.
- **Motion polish:** page-load orchestration, hover micro-interactions, tasteful — not busy. Respect reduced-motion.
**DoD:** typecheck 0, vitest green (+ tests for the animation state machine & TTS trigger/mute), build ok, bundle not bloated (lazy-load heavy motion libs). **e2e:** Playwright asserts spawn + handoff animations mount and TTS is invoked on a simulated event (mock audio).

## WS-8 · Full e2e + Playwright automation suite (both) — final gate
**Tasks:**
- Backend `e2e_full`: ensure every major feature has a real-infra e2e (goal lifecycle+HITL, trigger, workflow-on-celery, ingestion+OCR, org mission+HITL, RPA→PDF, scopes/RLS isolation).
- Frontend Playwright **real-backend** suite (not mocked): login → submit goal → live SSE → HITL approve → complete; org mission live viz; workflow run; approvals inbox. Keep the fast mocked tier for PR.
- Wire tiers into CI: fast unit (PR), integration (testcontainers), e2e_full subset (PR)+full (nightly), Playwright full-stack (nightly).
**DoD:** all suites green locally; documented run commands; negative checks (each gate proven to bite).

---

## Cross-cutting honesty rules
- No fabricated data anywhere (numbers, estimates, analytics) — real value or an honest empty/indeterminate state.
- Every "wire" leaves exactly ONE reachable implementation (delete or wire dead duplicates).
- Every fix starts with a failing test for the documented reason.
- Never regress mypy (0 errors) or the fast tier (~20870 passed).

## WS-9 · Lint/type debt cleanup (backend) — pre-existing, discovered 2026-09-10
**Gap:** `uv run ruff check .` (backend) = **6502 errors** (325 in `app/`, ~6177 in `tests/`; 2457 auto-fixable). Pre-existing at `be425b42` (not introduced this session). CI ruff step is evidently scoped/not gating on the full tree. mypy is clean (0).
**Tasks (staged, low-risk first):** (1) `ruff check --fix` the safe auto-fixable set, commit in reviewable batches by rule family (imports/I, unused/F401, UP, C4) — run the fast tier after each batch to prove no behavior change; (2) hand-fix the residual `app/` errors (the 325 — these are the ones that matter most); (3) leave genuinely-intentional violations with scoped `# noqa` + reason or a per-file ignore in `pyproject.toml`; (4) then make CI gate on `ruff check .` so debt can't regrow.
**DoD:** `app/` ruff = 0; tests ruff reduced to <a small documented residual or 0; fast tier still ~20990 passed; mypy still 0. Do NOT `--unsafe-fixes` without per-fix review.

## WS-10 · RAG/retrieval/reranking/patterns — make generic & world-class (backend)
**Detailed by recon `recon/E-rag-knowledge-intelligence-backend.md` (rated 2026-09-10). Chunking is already 9/10 (real semantic + tokenizer — DONE). Focus on the confirmed gaps:**
- **[6/10, generic:n] Reranking on the DEFAULT path** — cross-encoder / MMR / ColBERT currently fire only on explicit pattern branches, NOT the default hybrid `retrieve()`. Wire a reranking stage into the default retrieval flow (config-gated, degrade when the reranker/dep is absent). Score calibration beyond raw MMR.
- **[7/10, generic:n] Agent-pattern auto-selection** — ToT/supervisor/debate engage only via per-agent flags. Add automatic per-goal complexity detection that routes a goal to the right pattern (keep an explicit override). One reachable selector (respect the existing default-off safety gate for the advanced tier — make selection real but safe).
- **[8/10] Retrieval confidence** — replace heuristic confidence with calibrated scoring; real fallback when confidence low.
- **[7/10] Embeddings multimodal** — confirm/ās upgrade image path from caption-to-text toward true multimodal vectors where the provider supports it; else keep honest labeling. Add drift/re-embedding policy.
- **[8/10] RAG-pattern selection** — optionally upgrade the keyword/regex adaptive selector toward a light classifier (only if it measurably improves routing).
- **[7/10] Memory TTL** — confirm uniform TTL purge across episodic/procedural (not just cache/tool-call).
- **[8/10] Self-improvement** — trace `apply_suggestion` end-to-end into the live agent-config read path (or confirm it's intentionally gated).
**DoD:** each item has a behavioural test proving it engages generically; mypy clean; fast tier green. **e2e:** a golden-set query exercises hybrid retrieve→**rerank on the default path**→grounded answer with real scores; a complex goal auto-routes to the right agent pattern.

## WS-11 · Knowledge / RAG / memory / graph FRONTEND UX — world-class customer experience (frontend)
**Detailed by recon report `recon/F-knowledge-rag-frontend-ux.md` (read it first).** Build world-class UX for the power features (the org/JARVIS console is the quality bar). Likely surfaces:
- **Knowledge base UI**: collections/documents, upload/ingest with progress, hybrid search with citations, per-collection settings.
- **RAG configuration UX**: see/choose retrieval strategy, chunking, reranking, embedding model per collection — with plain-language explanations.
- **Obsidian / knowledge-graph explorer**: interactive nodes/edges wired to GraphRAG, not cosmetic — elevate to awe level like the org constellation.
- **Memory inspector**: view episodic/procedural/long-term memories + reflexion lessons, scoped.
- **Retrieval/grounding trace viewer**: what was retrieved, scores, which chunks grounded the answer.
- Consistent design tokens, loading/empty/error states, a11y, tasteful motion. Follow the artifact-design principles.
**DoD:** typecheck 0, vitest green (+ component tests), build ok (lazy-load heavy viz), each surface wired to real backend. **e2e:** Playwright covers KB search+citation, graph explore, RAG-config change.

## WS-12 · Ingestion — GENERIC handling + world-class knowledge base (backend)
**Detailed by recon report `recon/G-ingestion-worldclass-kb.md` (read it first).** The user emphasized: ingestion must handle ALL things generically and produce a world-class KB. Close whatever recon marks PARTIAL/STUB. Likely items:
- **Generic parser coverage**: every input type (PDF text+scanned, DOCX, CSV/Excel, JSON, HTML, MD, code, image, audio, video) routed to a REAL parser by MIME — no naive string parsers, no silent drops (ING-4/7/11/12 — confirm closed).
- **Pipeline EMIT + metrics** actually publish `knowledge.updated` + increment counters (ING-8/9); scheduler/connector path alive (ING-11).
- **Two-stack convergence**: `IngestionPipeline` / `IngestionOrchestrator` / `knowledge/ingestors/*` share one parser registry + chunker selector (ING-13) — no silent divergence.
- **Connectors**: Confluence/Jira/Slack/GitHub/S3/GDrive/web real + correct `RawDocument.content_type`; each stub either implemented or marked unsupported.
- **Dedup/hashing, incremental re-ingest, failure/reprocessing** generic; RLS-isolated; retrievable + cited.
- **Universal OCR fallback** (ties to WS-6): unreadable format → rasterize → OCR.
**DoD:** unit tests per parser/route; mypy clean. **e2e (`tests/e2e_full/test_ingestion_worldclass_e2e.py`):** real PDF+DOCX+CSV+image+audio through the connector/scheduler path → real text (no garbage) → correct chunker (no silent fixed) → pgvector rows → retrievable by query → `knowledge.updated` published → dedup on re-ingest.

## Status tracker (update as waves land)
- WS-0 BK6: ✅ DONE 13fcf398 (deleted orphan ImprovementActionExecutor; safety gate default-off kept; mypy 0, tier 20989)
- WS-1 Civilization throttle: TODO
- WS-2 Org de-fake: TODO
- WS-3 HITL flawless: TODO
- WS-4 Workflow/Trigger/Schedule Celery e2e: TODO
- WS-5 RPA→PDF: TODO
- WS-6 OCR universal: TODO
- WS-7 Org frontend AWE: TODO
- WS-8 Full e2e + Playwright: TODO
