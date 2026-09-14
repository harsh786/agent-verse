# World-Class Chat-Based Mission Platform — Master Plan

> Turn AgentVerse's chat into the **primary, universal interface** to the entire platform:
> anything doable via goals, agents, triggers, workflows, governance, trust/security,
> AI org team, knowledge bases, connectors and models is doable by **conversation** —
> from the web UI or any external channel (API, WhatsApp, Telegram, …), with multi-format
> media in and out, durable cross-session memory, and a flawless animated UI.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done (tested). Every `[x]` must have
passing unit tests, and phase-level e2e where noted. **No stubs, no simulation** — the whole
point of this effort is to remove the current facade.

---

## 0. Guiding principles

1. **One pipeline.** Web chat and every external channel converge on a single
   `ChatService` → intent → execution path. No parallel simulated pipeline (today the
   chat API is simulated and only the gateway path is real — we unify them on the real one).
2. **Chat orchestrates the real platform.** A chat turn can run the full `AgentGraph`
   (plan→execute→verify with connectors/MCP + core pattern execution), create triggers,
   launch org-team missions, query knowledge bases, and drive governance — using the
   *existing* subsystems. Chat is a thin conversational orchestrator, not a reimplementation.
3. **Durable & continuous.** Everything persists (Postgres, RLS-scoped). Context survives
   restart, spans conversations, and recalls a thread from months ago.
4. **Async is first-class.** Long/scheduled/recurring work runs out-of-band and delivers
   results back into the originating conversation and channel.
5. **Governed & safe.** Every chat-initiated action flows through the existing tool-risk →
   HITL → policy → cost → audit stack; approvals happen *in the conversation*.
6. **World-class UX.** Rich rendering, live agent transparency, motion/animation, responsive,
   themable, accessible — reusing the ~16 already-built-but-unmounted components.
7. **Tested against real-world scenarios.** Each phase ships unit tests; the platform ships
   an e2e suite covering the concrete user journeys in §11.

---

## 1. Canonical chat-event contract (the linchpin — build FIRST)

A single event schema emitted by the backend SSE bridge and consumed by both the chat thread
and the transparency panel. Defined once in `app/chat/events.py` (backend) and mirrored in
`src/features/chat/chat.types.ts` (frontend). Reconciles today's mismatch where the panel
expects `GoalEvent` names the stream never sends.

| event | payload | producer |
|---|---|---|
| `message_started` | message_id, role | ChatService |
| `token` | text delta | QA generator / executor stream |
| `reasoning` | text delta | planner/executor thinking |
| `intent_classified` | intent, confidence | IntentRouter |
| `plan_ready` | steps[] | AgentGraph (via Redis bus) |
| `step_started` / `step_complete` | step_id, description, status, output | AgentGraph |
| `tool_call` / `tool_result` | server, tool, args, result, truncated | AgentGraph |
| `knowledge_retrieved` | citations[] (collection, source, score) | RAG mixin |
| `hitl_required` / `hitl_resolved` | request_id, action, risk | HITL gateway |
| `guardrail_blocked` | rule, reason | guardrails |
| `artifact_created` | artifact_id, kind (pdf/docx/xlsx/img/code/table), url | artifact store |
| `schedule_created` | trigger_id, cron, human_schedule, next_run | scheduler |
| `mission_started` | mission_id (org-team) | org brain |
| `cost` | usd, tokens | cost controller |
| `goal_complete` / `error` / `done` | status, reason | AgentGraph |

**Bridge:** `GoalService` already publishes goal events to Redis pub/sub for cross-replica SSE.
Phase 0 subscribes the chat stream to that bus and maps goal events → the vocabulary above.

Tests: `tests/chat/test_events_contract.py` — every event round-trips; FE/BE enums match
(a contract test asserting the TS union equals the Python enum).

---

## 2. Phase 0 — The spine (make a chat turn actually run the agent) `[foundational]`

**Goal:** delete the simulation; a chat turn genuinely converses OR executes on the real engine,
durably, streaming real events.

Backend:
- [ ] `app/chat/events.py` — canonical event enum + typed payloads (§1).
- [ ] Persistence: `chat_sessions`, `chat_messages`, `chat_artifacts` tables + Alembic
  migration; RLS-scoped. Replace `ChatService`'s in-memory dicts with a Postgres repository
  (`app/chat/repository.py`), mirroring `PostgresMemoryRepository`.
- [ ] `ChatService.dispatch`: for GOAL intent → `GoalService.submit_goal(...)` binding
  `conversation_id`/`session_id`/`message_id`; for QA intent → real LLM generator that
  consumes `ConversationContext.build_for_qa` (today dead code).
- [ ] `app/chat/stream.py`: subscribe to the real Redis goal-event bus and map → §1 events;
  remove hardcoded fake steps in `chat/router.py`.
- [ ] `IntentRouter`: add the promised fast-LLM fallback for ambiguous messages
  (`intent.py:194`), context-aware (attachments + last N turns).
- [ ] Wire `bootstrap/routers.py` to construct the DB-backed `ChatService` in lifespan
  (swap the in-memory one, following the two-phase wiring pattern).

Frontend (parallel-safe):
- [ ] `chat.types.ts` regenerated from the canonical contract; delete stale event names.

Tests:
- [ ] unit: dispatch routes QA vs GOAL; GOAL calls GoalService (mocked) with conversation binding.
- [ ] unit: stream maps Redis goal events → canonical events, in order.
- [ ] e2e (`e2e_full`): POST a chat message "search my Jira for open bugs" → real AgentGraph
  runs with a FakeProvider + mock MCP → SSE emits plan_ready/tool_call/goal_complete → assistant
  message persisted and reloadable after "restart" (new ChatService instance, same DB).

Acceptance: no code path emits canned `"planning→Done"`; a chat GOAL turn produces real
tool-call events; sessions survive a process restart.

---

## 3. Phase 1 — Memory & continuity (long · cross-conversation · months-later)

**Goal:** the chat remembers — within a long session, across sessions, and after long delays.

Backend:
- [ ] Actually call `ConversationContext.build_for_qa` (20-turn window) and
  `compress_long_session` (summarize >100 msgs) in the answer path; replace the placeholder
  summary (`context.py:79`) with a real LLM summarization step, cached.
- [ ] DB-back `MemoryAPI` onto `LongTermMemoryStore` (pgvector); per-turn semantic recall via
  `inject_long_term_memory` (top-k), gated by data-class.
- [ ] Cross-conversation recall: episodic + long-term retrieval keyed by tenant (+ optional
  user), so a thread from months ago surfaces into a new conversation.
- [ ] On session close / idle, extract durable learnings into long-term/episodic memory
  (reuse `long_term_extractor`, `episodic`).

Tests:
- [ ] unit: long session (>100 msgs) compresses; summary fed to next turn.
- [ ] unit: a fact stated in session A is recalled in session B (semantic).
- [ ] e2e: "what did we decide about the Q3 launch date?" recalls a fact from an earlier
  (simulated 2-months-old) session by timestamp + embedding.

---

## 4. Phase 2 — Async: scheduling, recurring, delayed answers + delivery-back

**Goal:** "remind me Monday", "every day at 9 summarize X", "answer me when the report's done"
create real schedules/goals and deliver results back into the conversation and channel.

Backend:
- [ ] SCHEDULE intent → real `TriggerSpec` via `NLScheduler`/`ScheduleStore` (today only previews).
- [ ] Bind `conversation_id` + `channel` onto goals/triggers (a `delivery_target` record).
- [ ] Delivery-back: on goal/trigger completion (`scaling/tasks.py`), post the result to the
  originating chat session SSE **and** the origin channel via `NotificationRouter`.
- [ ] Follow-up message injection into the conversation history (so it's part of memory).

Tests:
- [ ] unit: SCHEDULE intent creates a TriggerSpec; recurring cron parsed.
- [ ] unit: completed goal with a `delivery_target` posts a follow-up message + channel send.
- [ ] e2e: "every minute, tell me the time" → trigger fires → follow-up messages appear in the
  session stream; "remind me in 1 min to stretch" → single delayed delivery.

---

## 5. Phase 3 — Channel unification (WhatsApp · Telegram · generic API)

**Goal:** an inbound message from any channel runs the *same* ChatService pipeline and replies
back; chat is a first-class API surface.

Backend:
- [ ] Route `gateway/_process_command` through `ChatService` (shared intent + history +
  context), persisting turns via `ConversationManager` (dedupe the two pipelines).
- [ ] First-class channel→tenant/session mapping (channel registration record), replacing the
  shared `GATEWAY_INGRESS_SECRET`+header binding for tenant resolution.
- [ ] Generic inbound webhook + outbound reply already exist — ensure both use the unified path.
- [ ] Map channel user → durable session so memory persists per channel user.

Tests:
- [ ] unit: WhatsApp inbound → ChatService.dispatch → reply; history persisted via ConversationManager.
- [ ] e2e: same "search Jira" journey via a simulated Telegram webhook produces identical
  behavior to the web chat path.

---

## 6. Phase 4 — Multi-format media I/O (input + output)

**Goal:** accept images/PDF/docx/xlsx/audio as input; produce PDF/docx/xlsx/images as output —
all via existing platform pieces and connectors.

Backend:
- [ ] Input: wire `app/ingestion/parsers/*` + `app/tools/document_parser.py` + OCR/perception
  so an attached file becomes chat context (and optionally a knowledge-base ingest).
- [ ] Output: expose a **document-generation capability** to the agent loop (PDF via
  `rpa/report.py:render_report_pdf`; add docx/xlsx generators) as a first-class agent tool.
- [ ] Binary **artifact store** (MinIO/S3 abstraction already in infra) + `GET /chat/artifacts/{id}`
  download endpoint returning the real file; emit `artifact_created` with a URL.
- [ ] Audio in/out via existing `app/voice/*` (STT for voice notes, TTS for spoken replies on
  channels that support it).

Tests:
- [ ] unit: "make a PDF of this summary" → generator produces a real PDF artifact + URL.
- [ ] unit: attach a CSV → parsed → available as step context.
- [ ] e2e: upload an invoice image → OCR → LLM extract → generate a PDF report → download works.

---

## 7. Phase 5 — "Anything via chat": full-platform command surface

**Goal:** every platform capability is reachable conversationally through a **chat skill/tool
registry** that maps NL intents to existing services (no new business logic — thin adapters).

Backend — a `app/chat/skills/` registry, each skill = {name, description, arg schema, handler}:
- [ ] **Goals/agents:** submit/inspect/cancel goals; route to a specific agent; list agents.
- [ ] **Triggers/schedules:** create/list/pause/delete (via NLScheduler/ScheduleStore).
- [ ] **Workflows:** run/inspect a workflow by name; author a workflow from NL (workflow_planner).
- [ ] **Connectors:** list connected services; connect/authorize (hand off OAuth safely — never
  handle secrets in chat); run a connector tool.
- [ ] **Knowledge bases:** ingest a source, search a collection, cite results in the answer.
- [ ] **AI org team:** launch an org-team mission (org-brain), report mission status, approvals.
- [ ] **Governance/trust/security:** show pending approvals, approve/reject in chat, view audit
  trail, view cost/budget, view guardrail decisions, manage permissions — all read/act through
  existing `governance/*` (HITL, audit, cost, policies) and tenancy.
- Skill selection uses the existing `ToolSelector`/tiered tool-context so the planner sees the
  right skills per turn (tenant/permission filtered).

Tests:
- [ ] unit per skill: NL → correct service call with correct args + permission check.
- [ ] e2e: "connect my Slack", "run the weekly-report workflow", "who approved mission 12?",
  "what's my spend today?" each drive the real subsystem.

---

## 8. Phase 6 — Governance, trust & security *in the conversation*

**Goal:** the safety stack is visible and actionable inside chat.
- [ ] In-chat HITL: a `hitl_required` event renders an approve/reject card (`ChatHITLCard`
  already exists); approval resumes the paused goal via the HITL gateway.
- [ ] Guardrail/policy blocks surface as explained messages (`guardrail_blocked`).
- [ ] Every chat-initiated action writes to the audit trail with `source=chat`, conversation id.
- [ ] Cost/budget shown inline (`cost` events → token/$ badge — `ChatTokenCostBadge` exists).
- [ ] Trust: chat-initiated actions carry the agent identity / (future) Grantex-style scoped
  grant; cross-channel actions verify channel-user → tenant binding.

Tests:
- [ ] e2e: a high-risk step ("delete prod index") pauses → in-chat approval → resumes/rejects;
  audit shows the chat-sourced action; a budget-exceeded turn is blocked with a clear message.

---

## 9. Phase 7 — World-class frontend (flawless, animated, stylish)

**Goal:** move from plain-text bubbles to a polished, motion-rich product. Much is *wiring the
16 already-built, zero-import components*.

- [ ] **Mount the orphans:** `ChatRichOutput`/`RichMarkdown`, `ChatDataTable`, `ChatChart`,
  `ChatImageOutput`, `ChatArtifactPanel`, `ChatStepCard`, `ChatConversationSummary`,
  `ChatGoalSummary`, `ChatClarifyCard`, `ChatGoalFailureCard`, `ConnectedServicesPanel`,
  `ChatModelSelector`, `ChatTokenCostBadge`, `ChatUsageModal`, `ChatSessionSettingsModal`,
  `ChatEmptyState`.
- [ ] **Rich rendering:** markdown + GFM tables + syntax-highlighted code with copy; inline
  images; file/PDF attachment + download cards.
- [ ] **Agent transparency:** accumulate *all* SSE events (not `[currentEvent]`); reconcile
  names to the canonical contract; collapsible "thinking" (reasoning) block; per-step tool I/O;
  citations/sources; a live execution timeline.
- [ ] **Composer:** stop (wire existing `stopStream`), regenerate, inline edit-and-rerun (drop
  `window.prompt`), file attach + drag-drop, slash-commands, `@`-mentions of connectors/agents,
  voice input.
- [ ] **Streaming robustness:** reconnect/backoff, visible error bubble + retry, dedupe DB-vs-local.
- [ ] **Conversation mgmt:** rename (wire `ChatSessionSettingsModal`), delete confirmation,
  message-content + cross-conversation search, archive, resume-old affordance, inline memory.
- [ ] **Design system & motion:** replace hardcoded hex with theme tokens; fix the light/dark
  duality; responsive collapsible sidebar/panels (mobile-first); Framer-motion message entrance,
  streaming shimmer, panel transitions (respecting reduced-motion, per the freeze lessons);
  skeletons for loading.
- [ ] **Schedule & channel awareness:** schedule-confirmation card (`schedule_created`); channel
  origin/continuation badges (WhatsApp/Telegram) with links to channel mappings.

Tests:
- [ ] vitest: markdown/code/table rendering; event accumulation; stop/regenerate; error bubble;
  message dedupe.
- [ ] Playwright e2e: full conversation with a tool call renders a step timeline; approve HITL in
  chat; generate + download a PDF; responsive + dark-mode snapshots.

---

## 10. Cross-cutting

- **Feature flags & rollout:** `chat_v2_enabled` per-tenant (reuse org-brain's per-tenant flag
  resolver) so we can pilot safely; old simulated path removed once parity is proven.
- **Observability:** OTel spans per chat turn (intent, plan, tools, cost); structured logs.
- **Security:** never handle credentials/OAuth secrets in chat (hand off to the connector flow);
  SSRF guard on any chat-fetched URL; sanitize/redact tool outputs (existing `sanitization`).
- **Performance:** reuse semantic + response caches; fast keyword intent first, LLM only when
  ambiguous; stream tokens; small-goal → cheap model (existing model router).

## 11. Real-world e2e scenarios (the acceptance battery — must all pass)

1. **Breakfast recipe (pure QA):** "give me a quick high-protein breakfast recipe" → streamed
   markdown answer, no tools, remembered next turn ("make it vegetarian").
2. **Cross-tool goal:** "find open P1 bugs in Jira and post a summary to #eng on Slack" →
   plan→tool_call(Jira)→tool_call(Slack)→goal_complete, step timeline visible, audited.
3. **Doc generation:** "turn last week's metrics into a PDF report" → artifact_created → download.
4. **Media input:** upload an invoice image → OCR → extracted fields → "save this to the finance
   KB" → ingested + citable later.
5. **Scheduling + delivery-back:** "every Monday 9am send me last week's sales as a spreadsheet"
   → trigger created → fires → xlsx delivered into the chat + WhatsApp.
6. **Long-delayed memory:** (seed a session dated 2 months ago) "what did we say about the
   pricing change?" → recalled with citation.
7. **Governed action:** "delete the staging index" → HITL card in chat → approve → executes →
   audit shows chat source; "spend $5000 on ads" → blocked by policy with explanation.
8. **Org-team mission:** "have the growth team draft a Q3 launch plan" → org mission launched →
   status + deliverable surfaced in chat.
9. **Channel parity:** run scenario 2 from a Telegram message → identical result, reply on Telegram.
10. **Resilience:** kill the stream mid-execution → FE reconnects, no lost/dup messages; restart
    backend → session + history intact.

---

## 12. Execution order (dependency-aware)

Contract (§1) → Phase 0 → {Phase 1, Phase 9-frontend-static parts in parallel} → Phase 2 →
Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 9-transparency → Phase 7-hardening → §11 battery.

Each phase: TDD (red→green), commit working increments, run scoped tests before moving on;
run the relevant §11 scenario as the phase's e2e gate.
