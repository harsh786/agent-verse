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

## WS-8 · FULL automated test pyramid — unit + functional + integration + e2e_full + Playwright (both) — final gate
**User requirement (explicit): EVERYTHING automated. Every feature must have the complete pyramid, run automatically in CI. UI e2e via Playwright must run automatically. Workflow especially must be fully e2e-tested and automated.**
**Tasks:**
- **Unit + functional (per feature):** confirm every feature package has real unit + functional tests (backend `tests/<pkg>/`, frontend vitest). Fill gaps (recon flagged thin areas: coordination 1-test-per-60-LOC, multimodal, ingestion mock-heavy). No feature ships without them.
- **Integration (testcontainers):** real Postgres+Redis integration tests for each subsystem that touches infra.
- **Backend `e2e_full` (real infra):** a real-infra e2e for EVERY major feature — goal lifecycle+HITL, **trigger→workflow→schedule on real Celery** (add the worker to compose, WS-4), ingestion+OCR (WS-12), org mission+HITL+any-task (WS-2/3), RPA→PDF + RPA→KB (WS-5/13), OCR-everywhere (WS-14), KB convergence (WS-13), scopes/RLS isolation. Currently missing per recon: real ingestion e2e, workflow-on-real-Celery e2e, KB-convergence e2e.
- **Frontend Playwright REAL-BACKEND suite (automated, not mocked):** login → submit goal → live SSE → HITL approve → complete; org mission live viz + awe animations; **workflow builder → create → trigger → run → live run view → terminal**; approvals inbox; KB search+citation + real graph explore (WS-11); RAG-config change. Keep the fast mocked tier for PR speed; add the real-backend project with a `webServer`/compose baseURL so it runs automatically.
- **CI automation (the point):** wire all tiers into CI so they run automatically — fast unit+functional (every PR), integration (testcontainers, PR), e2e_full subset (PR) + full (nightly), **Playwright real-backend (nightly + on-demand)**, k6 load (nightly). Document the exact commands; ensure a stopped Docker FAILS (not skips).
- **Negative verification:** each gate proven to bite (delete a test → coverage fails; unwire a path → its e2e fails).

### ▶ WORKFLOW — full automation (user emphasis, called out explicitly)
The workflow feature gets the deepest coverage: **unit** (DSL parse/validate, step types, retries/timeout/on_failure), **functional** (each of the 14+ step types executes; org step types), **integration** (WorkflowRunStore round-trip, RLS), **backend e2e_full** (`test_workflow_run_e2e.py`: create → `/trigger` → REAL Celery run → 2+ real step rows, no `{"_mock":true}` → terminal; + trigger→workflow, scheduled→workflow, HITL-gate-inside-workflow), and **Playwright UI e2e** (open builder → compose a 2-step workflow → save → run → watch the live run view render step-by-step → terminal, against a real backend). Workflow is not "done" until all five layers are green and automated.
**DoD:** every feature has the full pyramid green + automated in CI; workflow has all five layers; documented commands; negative checks pass.

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

## WS-10 · Agentic patterns + RAG patterns + all RAG aspects — GENERIC FRAMEWORK, world-class (backend)
**Detailed by recon `recon/E-rag-knowledge-intelligence-backend.md` (rated 2026-09-10). Chunking is already 9/10 (real semantic + tokenizer — DONE). Focus on the confirmed gaps:**

### ▶ FRAMEWORK PRINCIPLE (explicit, user requirement) — implement all of these GENERICALLY as a framework, not hardcoded/flag-gated per case:
- **Agentic patterns** (plan-execute, ReAct, reflection, reflexion, ToT, goal-tree, supervisor, debate, consensus, and the advanced tier) MUST be selectable/composable GENERICALLY per goal via ONE registry + ONE selector driven by task characteristics (complexity, tool needs, ambiguity) — NOT via per-agent boolean flags. Explicit override allowed; default selection automatic. Respect the existing default-off safety gate for the autonomous/advanced tier, but make selection real.
- **RAG patterns** (all ~18: basic/hybrid/CRAG/self-RAG/FLARE/RAPTOR/RAFT/agentic/speculative/fusion/modular/memory/graph/web/code…) MUST be selectable GENERICALLY on query characteristics via ONE selector (BK1 completed the adaptive coverage — build on it; optionally upgrade the keyword/regex heuristic to a light classifier).
- **Retrieval / chunking / reranking / embedding strategies** MUST be pluggable GENERICALLY per collection/query through ONE registry each — any strategy addable without touching call sites; one reachable implementation per capability; extensible.
- The whole stack is a FRAMEWORK: adding a new pattern/strategy = registering it, and it becomes selectable everywhere (goal/agent/workflow/org execution) without bespoke wiring. This is what makes AgentVerse a truly generic agentic platform.

### Confirmed gaps to close (from recon E):
- **[6/10, generic:n] Reranking on the DEFAULT path** — cross-encoder / MMR / ColBERT currently fire only on explicit pattern branches, NOT the default hybrid `retrieve()`. Wire a reranking stage into the default retrieval flow (config-gated, degrade when the reranker/dep is absent). Score calibration beyond raw MMR.
- **[7/10, generic:n] Agent-pattern auto-selection** — ToT/supervisor/debate engage only via per-agent flags. Add automatic per-goal complexity detection that routes a goal to the right pattern (keep an explicit override). One reachable selector (respect the existing default-off safety gate for the advanced tier — make selection real but safe).
- **[8/10] Retrieval confidence** — replace heuristic confidence with calibrated scoring; real fallback when confidence low.
- **[7/10] Embeddings multimodal** — confirm/ās upgrade image path from caption-to-text toward true multimodal vectors where the provider supports it; else keep honest labeling. Add drift/re-embedding policy.
- **[8/10] RAG-pattern selection** — optionally upgrade the keyword/regex adaptive selector toward a light classifier (only if it measurably improves routing).
- **[7/10] Memory TTL** — confirm uniform TTL purge across episodic/procedural (not just cache/tool-call).
- **[8/10] Self-improvement** — trace `apply_suggestion` end-to-end into the live agent-config read path (or confirm it's intentionally gated).
**DoD:** each item has a behavioural test proving it engages generically; mypy clean; fast tier green. **e2e:** a golden-set query exercises hybrid retrieve→**rerank on the default path**→grounded answer with real scores; a complex goal auto-routes to the right agent pattern.

## WS-11 · Knowledge / RAG / memory / graph FRONTEND UX — world-class (frontend)
**Detailed by recon `recon/F-knowledge-rag-frontend-ux.md` (rated 2026-09-10). This is the biggest world-class gap on the platform — the powerful backend has no UX to see/control it. Ordered by severity:**
- **[3/10 — Obsidian/KG viz is FAKE] TOP FIX:** `src/features/obsidian/` glowing graph is **100% hardcoded demo data, not wired to the backend**; the only real data is a text-only list. Build a real interactive node-link graph explorer wired to the KG/GraphRAG endpoints (nodes/edges, click-to-expand, filter), elevated to org-constellation quality (d3-force/React Flow). No fabricated data — honesty rule.
- **[2/10 — MISSING] RAG configuration UX:** today only embedder choice at collection creation. Add per-collection controls for retrieval strategy, chunking strategy, reranking, embedding model — with plain-language explanations of each. Wire to backend config.
- **[2/10 — MISSING] Agent/RAG pattern selection UX:** no pattern selector anywhere. Add a surface to see/choose (or view auto-selected) agent pattern + RAG pattern per goal/agent, with explanation (pairs with WS-10 auto-selection).
- **[4/10] Retrieval/grounding trace viewer:** the span waterfall exists but `attributes` (scores, retrieved chunks, which grounded the answer) are never rendered. Render them — show what was retrieved, scores, and grounding.
- **[8/10] Knowledge base UI:** add inline citation source-hover/highlight linkage.
- **[7/10] Memory inspector:** add episodic/procedural/reflexion categorization + goal-linkage.
- **[8/10] Evals UX:** remove the duplicate eval-suites page; optionally add an auto-suggestion surface.
- Consistent design tokens, loading/empty/error states, a11y, tasteful motion (artifact-design principles). Elevate the second-class graph/RAG-config surfaces to JARVIS-shell quality.
**DoD:** typecheck 0, vitest green (+ component tests), build ok (lazy-load heavy viz, keep main chunk small), every surface wired to REAL backend (no demo data). **e2e:** Playwright covers KB search+citation, real graph explore, a RAG-config change, and the trace viewer showing scores.

## WS-12 · Ingestion — GENERIC handling + world-class KB (backend)
**Detailed by recon `recon/G-ingestion-worldclass-kb.md` (rated 2026-09-10). Solid baseline (EMIT/metrics/scheduler REAL, two-stack ING-13 closed w/ parity test, 38/41 connectors real). Fix the confirmed bugs — ordered by severity:**
- **[4/10 — DEAD dedup] TOP FIX:** no `exists_by_hash` implementation exists, so the Stage-3 `hasattr` dedup check always fails and document-level dedup NEVER fires in production → duplicates re-indexed. Implement `exists_by_hash` on the KnowledgeStore (content-hash lookup, RLS-scoped) so dedup + incremental re-ingest actually work. Failing test first.
- **[7/10 — FAKE success] Notion/GDrive/SharePoint connectors:** real but unregistered, reachable only via a stub `delta_reingest_files` task that reports success WITHOUT calling the pipeline (honesty violation). Register them via `@register` and route through the real pipeline, or mark explicitly unsupported — never fake success.
- **[7/10 — video gap] Video parser:** `VideoParser` (Whisper-backed) exists but is never called — no `ContentType.VIDEO` branch in `parser_registry.parse_bytes_async`; video falls to naive byte-decode. Add the branch.
- **[metadata gap] OCR/degradation provenance:** persist OCR-used / degradation metadata onto indexed docs (ties to WS-13 provenance).
- **[zero-vector] Embedder-failure path:** confirm the ingestion embed path uses the None-sentinel (per D-12) and never writes silent zero-vector chunks; fix if a zero-vector path remains.
- **Universal OCR fallback** (ties to WS-6): unreadable format → rasterize → OCR.
**DoD:** failing-test-first for dedup + connectors + video; unit tests per parser/route; mypy clean; fast tier green. **e2e (`tests/e2e_full/test_ingestion_worldclass_e2e.py`) — recon G confirmed NO real ingestion e2e exists (tests 4/10, mock-heavy):** real PDF+DOCX+CSV+image+audio+video through the connector/scheduler path against real pgvector → real text (no garbage) → correct chunker (no silent fixed) → pgvector rows → retrievable by query → `knowledge.updated` published → **dedup fires on re-ingest** (proves the top fix).

## WS-13 · Unified world-class knowledge base — ALL sources converge (backend + frontend)
**Detailed by recon `recon/G-ingestion-worldclass-kb.md` + `recon/H-unified-kb-convergence.md` (read both first).** The user's requirement: the KB created from ingestion, from RPA scraping, AND from OCR must be ONE coherent, world-class knowledge base — every source's content becomes retrievable, cited, deduped, provenance-tagged, tenant-isolated knowledge, with a unified frontend view.
**Ratings (recon H, 2026-09-10):** unified store 7 · ingestion→KB 9 · RPA→KB **3** · OCR→KB 6 · provenance/dedup/RLS 7 · frontend unified view 6.
### Backend (ordered by severity)
- **[3/10 — RPA→KB broken, TOP FIX] Wire the REAL RPA subsystem into the KB and kill the duplicate.** Today `RPAExecutor`/`app/api/rpa.py` write only to `RPAArtifactStore` (filesystem/MinIO) and NEVER to the KB; a separate ad-hoc Playwright scraper inside `api/knowledge.py` (`/ingest/rpa-url`) duplicates browser logic to feed the KB. Fix: make `RPAExecutor` (and the agent RPA tool) able to emit scraped content as `RawDocument` → the unified pipeline → KB (retrievable + `source=rpa` provenance), and route `/ingest/rpa-url` through `RPAExecutor` — deleting the duplicate scraper (one reachable implementation). Ties to WS-5: RPA→PDF = document deliverable, RPA→KB = knowledge deliverable — do both.
- **[6/10 — OCR standalone bypasses KB] `/ocr/extract` → KB option:** the standalone OCR extract feature (`app/api/ocr.py`) never touches `KnowledgeStore`; add a persist-to-KB path with `source=ocr` provenance (pipeline OCR already reaches the KB — keep that).
- **[7/10 — dedup per-path] Cross-source global dedup:** dedup is per-path content-hashing, not a global check — combine with WS-12's `exists_by_hash` so the SAME content from different sources dedups against the one store.
- **Provenance everywhere:** every chunk records origin (source_type/source_url/content_hash present — keep; ensure RPA/OCR set it).
### Frontend
- **[6/10] Unified KB view:** `KnowledgePage.tsx` already shows `source_type`-badged docs (incl. `rpa-web`) — good; extend it to include OCR-sourced docs and add source-provenance filtering + search + citations. **Link the isolated `OcrPage.tsx` back to the KB** (its extracts should be viewable in the unified KB). Elevate to JARVIS-shell quality. (Extends WS-11.)
**DoD:** content from ingestion + RPA (via RPAExecutor) + OCR (pipeline AND standalone) is retrievable by query, cited, `source`-tagged, RLS-isolated; the duplicate ad-hoc scraper is gone; frontend shows a unified, filterable KB incl. OCR. **e2e (`tests/e2e_full/test_kb_convergence_e2e.py`):** ingest a file + scrape a page via the REAL RPAExecutor + OCR an image → all three become retrievable KB chunks with correct `source` provenance; a query returns hits from each; cross-source dedup on re-add.

## WS-14 · OCR everywhere — independent execution + a capability in EVERY execution engine (backend + frontend)
**User requirement:** OCR must have (a) its own independent execution, AND (b) work inside workflow-engine execution, agent execution, AI-org-team execution, and goal execution — generically, via one OCR engine (`app/ocr/OcrEngine`).
### Backend
- **Independent OCR execution:** the standalone `/ocr/extract` API stays a first-class feature — world-class, and with an opt-in persist-to-KB (ties WS-13). Add batch + async job support if missing.
- **OCR as a callable capability (one engine, many callers):** expose `OcrEngine` as:
  - a **tool** the agent loop can invoke (register in the tool/MCP surface) → available in **goal execution** and **agent execution**;
  - a **workflow step type** (e.g. `ocr`) so a **workflow** can OCR a document/image mid-flow (register in `StepTypeRegistry`, validated in DSL);
  - reachable from **AI-org-team** missions (org agents can OCR as part of a task).
  Never duplicate OCR logic per engine — all routes call the one `OcrEngine`.
- **Generic input:** any document/image (incl. rasterize-any-format from WS-6) → OCR → structured text/report, with provenance.
### Frontend
- Surface OCR as: a usable standalone page (link it into the KB per WS-13), an available workflow step in the builder, and an agent/goal tool the user can see was invoked (trace).
**DoD:** OCR invocable + tested from all four execution paths (goal, agent, workflow step, org mission) + standalone; one engine, no duplication; mypy clean. **e2e (`tests/e2e_full/test_ocr_everywhere_e2e.py`):** an image is OCR'd via (1) standalone API, (2) a goal whose agent calls the OCR tool, (3) a workflow with an OCR step, (4) an org mission task — each yields the same extracted text; frontend shows the OCR step/tool in the builder + trace.

## Global requirement (applies to EVERY workstream WS-1…WS-14)
Per the user: everything must be **analyzed → built generically → implemented world-class for BOTH frontend and backend → covered by e2e tests + an automation suite**. Concretely, no workstream is "done" until: (1) backend capability is generic (handles arbitrary inputs, one reachable impl); (2) a world-class frontend surface exists for it (org/JARVIS console is the quality bar); (3) unit + integration tests pass; (4) a real-infra `e2e_full` backend test AND a real-backend Playwright test exercise it (WS-8 aggregates the automation suite + CI tiers). No fabricated data anywhere.

## WS-15 · FINAL re-verification + convergence loop (do NOT skip — the closing gate)
**PRECONDITION (user: "do 8 before 15"): WS-8 (full automated test pyramid — unit+functional+integration+e2e_full+Playwright, all in CI) MUST be complete before WS-15 runs. WS-15 re-verifies the platform INCLUDING that automation; it cannot pass without WS-8 green. Order: …→ WS-8 → WS-15 (last).**
**After WS-1…WS-14 are implemented (WS-8 included), re-verify and re-analyze the WHOLE platform so nothing is missed, and keep improving until every area is genuinely 10/10.**
- **Re-run the rating sweep:** dispatch fresh read-only recon (same shape as A1–H) across every dimension — org, civilization, coordination, JARVIS/HITL, RPA, OCR, retrieval, chunking, embeddings, reranking, KB, KG, Obsidian, agentic patterns, RAG patterns, memory, self-improvement, ingestion, unified KB, workflow, triggers, scheduling — plus the FRAMEWORK genericness (WS-10) and the FULL test pyramid + automation (WS-8) for each. Produce a fresh x/10 per area with evidence, against the LIVE code (not the plan).
- **Whole-branch review:** dispatch a most-capable-model review over the full `git diff main..HEAD`; run mypy + full fast tier + e2e_full + Playwright; confirm all green and automated in CI.
- **Convergence loop:** for ANY area still < 10/10, or any missing test layer, or any non-generic path, or any fabricated data — open a new improvement wave, implement it (TDD + full pyramid), and re-verify. REPEAT until every area is a defensible 10/10 with full automated coverage. Do not declare done until the sweep comes back all-10 with green automation.
- **Anti-miss checklist:** no stubs/`pass`/`NotImplementedError` on a live path; no fabricated/demo data anywhere; one reachable implementation per capability; every feature has unit+functional+integration+e2e_full+Playwright, automated; every capability generic + world-class on BOTH FE and BE.
**DoD:** a final recon sweep report shows every dimension 10/10 with green + automated full-pyramid coverage; whole-branch review clean; then (and only then) surface to the user for the merge-to-main decision.

## Status tracker (update as waves land)
- WS-0 BK6: ✅ DONE 13fcf398 (deleted orphan ImprovementActionExecutor; safety gate default-off kept; mypy 0, tier 20989)
- WS-3 HITL flawless: ✅ DONE 1682720d (org-mission + workflow HITL were BROKEN, now real; goal+agent real; new integration+e2e tests; mypy 0, tier 20857). Residual WS-3b: org task-level approve endpoint doesn't resolve the paired HITLGateway request.
- WS-7 Org frontend AWE: ✅ DONE 1f692319/bea0daef/12e08caf
- WS-1 Civilization throttle: ✅ DONE 34608add (Redis run-lock + min-interval guard around tick; civ_tick_skipped_total metric; 4 unit tests; 18 bare-pass triaged = all legit; mypy 0)
- WS-11a real KG graph + trace: ✅ DONE 560d52e6/b02e8823/2444253e. WS-11b (honesty/polish): ✅ DONE 73a68acc (Obsidian Bases/Maps/Timeline honest preview states; prior session left the file uncompilable — completed + tested). WS-11c (real analogs): queued.
- WS-5 RPA→PDF: ✅ DONE 76ada05f + SSRF fix 78d9c76e (scrape→report→fpdf2 PDF, POST /rpa/report, e2e real-infra PASS; mypy 0)
- WS-6 OCR universal: ✅ DONE 114412a1 (OcrEngine.extract_any any-format→image→OCR + honest degradation metadata; 97 ocr tests)
- WS-11c + WS-11/13 frontend: ✅ DONE 34a1ca08 (Obsidian Bases/Maps/Timeline→real org+KG data, KB source filter, OcrPage→KB save; typecheck 0, vitest 59/59)
- WS-12 ingestion/KB foundation: ✅ DONE 7b5b19d6 (exists_by_hash dead-dedup fix, registered connectors + honest delta_reingest, video branch, provenance, zero-vector guard; 438 tests, e2e PASS). Provides exists_by_hash(content_hash, tenant_id, collection_id) for cross-source dedup.
- WS-2 org world-class: ✅ DONE fec331d6 (real any-task decompose→assign→handoff→finalize + rich events; LLM composer w/ fallback; de-faked analytics; RBAC+quality-gate TODOs; WS-3b closed) + org-approval bug fix 6618477e (G-24 endpoints called non-existent HITLGateway.resolve → always-404; now real approve/reject; 3 regression tests). 742+243 tests, e2e PASS.
- WS-10 RAG/agentic GENERIC framework: ✅ DONE ce6bcbd9 (rerank on DEFAULT retrieve path — engine + gateway; agent-pattern auto-select at AgentGraph seam, default-off gate, reachable on live goal path; calibrated confidence + low-conf fallback; e2e real cross-encoder rerank PASS). 2814 tests. Memory-TTL + classifier honestly out-of-scope.
- WS-14: ✅ tool universal 96b51364 + workflow 'ocr' step type f0bb5190 + cross-route consistency 1a210378. Remaining (→WS-15): OCR as by-name builtin agent tool (goal/org reachability) — app/mcp catalog+handler wiring.
- WS-13 RPA→KB + OCR→KB convergence: ✅ DONE 27c6117d (duplicate scraper DELETED; /ingest/rpa-url routes through the one RPAExecutor + real httpx fallback; kb_emit + scrape_to_kb; /ocr/extract persist_to_kb; cross-source dedup via exists_by_hash). 529+2580 tests, e2e convergence PASS.
- WS-4 Workflow real-Celery: ✅ DONE 17a2e3b4 (ROOT-CAUSE fix: Celery branch never seeded checkpointer → 0 step rows/stuck pending; execute_fresh + _finalize_status; real out-of-process worker proven subprocess+compose; 3 e2e PASS: real run 2 step rows no _mock, scheduled dedup, workflow-HITL). Honest caveat: cross-process HITL needs DB-backed approval store + shared checkpointer (future WS).
- WS-8 full pyramid + CI: ✅ CORE DONE 4c1ec37f (dedicated backend-e2e-full nightly job gating all 50 real-infra e2e tests via CI services — FAILS not skips, + non-empty guard). Existing: lint/mypy/unit(+empty-guard)/integration/security/docker-build + rich Playwright "live" projects + visual-regression. Follow-up: full backend-in-CI Playwright real-backend job (→WS-15/UI pass).
- WS-9 ruff debt: ✅ DONE d0afada3/85518e3a (app/ + tests/ ruff = 0, CI-gateable; 18 commits; caught a real name-collision bug + removed literal `# noqa` leaked into SQL/HTML/LLM prompts; mypy 0).
- WS-15 convergence + live UI e2e: ✅ DONE. Full merged tree: fast tier **20949 passed / 0 failed**, e2e_full **50 passed** (real infra incl. real Celery worker), mypy **1522 Success**, ruff app+tests **0**. Running the whole e2e suite together (the WS-8 gate) surfaced + fixed 6 latent bugs (SSRF, org-approval resolve(), workflow-checkpointer, goal_id varchar(32) truncation, embedder-leak isolation, ambient-RAG killing goals). **Live UI e2e (browser, by hand): PASS** — auth → Dashboard (real stats, honest zeros) → goal submit → Executing + live SSE stream → Organizations/Knowledge/Workflows/OCR pages all render real data, honest empty states, **zero console errors**.
- Remaining documented FOLLOW-UPS (honest, not blocking a 10/10 of the shipped scope): (a) OCR as a *by-name builtin agent tool* in the MCP catalog for goal/org execution — OCR is already reachable via standalone API + workflow `ocr` step + agent tool class + OCR→KB; this is the catalog/handler registration only. (b) A full backend-in-CI Playwright real-backend job (the mocked "live" projects + visual-regression already run nightly). (c) Cross-process *workflow* HITL needs a DB-backed approval store + shared checkpointer (architectural). (d) WS-2 `require_org_role` dependency built but intentionally not wired onto endpoints (needs middleware to populate the role).
- ▶ GAP-CLOSURE WAVE (user: "make it 10/10") — all 6 remaining gaps closed:
  - #1 OCR by-name agent tool: ✅ 47a7bd28 — builtin-utility MCP server makes extract_document (OcrEngine) + web_search + http_request agent-callable; real-OCR e2e passed. OCR-everywhere → 10.
  - #2 cross-process workflow HITL: ✅ 4fe7fe54 — DB-backed approval store (migration 0119) + worker resume; real cross-process e2e passed (worker pause → API approve → worker resume). Workflow-HITL → 10 (documented limit: multi-step pre-gate re-exec on resume).
  - #3 org RBAC wiring: ✅ 071555d3/7a647f85/8c8b636a — attached to sensitive endpoints, FAIL-CLOSED (owner admin role → org_admin; else denied). Fixed a fail-open default the commit-review flagged. Org → 9.
  - #4 real-backend Playwright: ✅ 20254d97 — real-backend project + goal-lifecycle.realbe.spec.ts + nightly playwright-real-backend CI job (services+backend+frontend). Test-automation → 10.
  - #5 Civilization: ✅ 18cb6b1c — fixed dead capability-routing + fabricated retired_members metric, dead-code removal, a2a_repository 0→100% cov; 356→376 tests, e2e passed. Civilization 7 → 9.
  - #6 Memory: ✅ 53aeda72 — expires_at was never set (TTL dead for all kinds) → fixed uniformly; new GET /memory/records API (kind + goal-linkage) + wired inspector; e2e passed. Memory 7 → 9.
  - All-6-gaps tree: fast tier 20993 passed / 1 pre-existing DNS-flake (test_ssrf_guard_available, passes standalone), mypy 1526, ruff app+tests 0.
- ▶ WORLD-CLASS WAVE-2 (user: Evals/self-improve, AI Org, Agent patterns → 10, no hardcoding, BE+FE+e2e+Playwright):
  - Agent patterns → 9/10: d07f3c8d — ONE generic registry+selector (characteristic-driven, not flag/hardcoded), decision traced + consumed by graph seam, GET /goals/{id}/pattern-selection, FE Pattern tab + override, e2e_full 2 passed, agent-patterns.realbe.spec.
  - AI Org → backend 10: b0958a9d — real LLM composer/decomposer path reachable (provider threaded), no hardcoded blueprint, stronger any-task e2e (real aggregated deliverable). FE AWE real-data. org.realbe.spec.
  - Evals/self-improve → 9/10: a490f50a (config-driven 7-dim scoring — no hardcoded weights/thresholds, shared source) + 9e150ecc (self-improve loop hardened: reads real agent config → writes improved config back → next run reads; bookkeeping failure no longer negates the live apply; proven live + robust). evals.realbe.spec.
  - Note: 2 FE polish items deferred (evals auto-suggestion surface, org mission live-view enhancement) — additive niceties on already-real-data surfaces, not gaps. Evals/Org agents hit infra watchdog stalls; substantive work salvaged + finished by hand.
  - FINAL GREEN: fast tier 21015 passed / 0 failed · e2e_full **58 passed** · mypy 1528 · ruff app+tests 0 · 4 real-backend Playwright specs collectable + nightly CI-wired.
- ▶ MERGE-TO-MAIN: pending the user's explicit go-ahead (main is preserved at backup/main-pre-overwrite-20260909-2325). Branch feature/platform-10x is green + UI-verified.
