# Chat-Based Goal Execution — Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Complete each phase before advancing. Run the test command at the end of every phase to verify.

**Goal:** Implement a world-class conversational agent interface (GitHub Copilot / Claude style) that routes messages intelligently between Q&A streaming and full agent goal execution — with 60+ features including message editing, artifacts panel, cross-session search, inline code execution, agent memory management, session folders, connected services panel, agent reasoning transparency, typing indicator, image output rendering, and everything in between.

**Spec:** `docs/superpowers/specs/2026-08-15-chat-based-goal-execution-design.md`

**Tech Stack:**
- Backend: Python 3.12, FastAPI, SQLAlchemy async, asyncpg, Redis pub/sub, Celery, LangGraph (existing)
- Frontend: React 19, TypeScript, TanStack Query, Zustand, Tailwind CSS, SSE (EventSource)
- DB: Postgres (existing), Alembic migrations
- Testing: pytest (backend), Vitest (frontend unit), Playwright (E2E)

---

## File Map

### New Backend Files
| File | Responsibility |
|---|---|
| `app/chat/__init__.py` | Package init |
| `app/chat/models.py` | ChatSession, ChatMessage, ChatSessionFolder SQLAlchemy models |
| `app/chat/router.py` | FastAPI router — 16+ endpoints |
| `app/chat/service.py` | ChatService: session CRUD, message dispatch, context building |
| `app/chat/intent.py` | IntentRouter: QA / GOAL / CLARIFY / SCHEDULE classification |
| `app/chat/stream.py` | SSE generator: multiplexes tokens + goal events |
| `app/chat/context.py` | ConversationContext: 20-turn history + compression at 100+ |
| `app/chat/memory_api.py` | Memory management REST layer over LongTermMemoryStore |
| `app/chat/services_api.py` | Connected services REST layer over MCPRegistry |
| `app/chat/execution.py` | Inline code execution sandbox wrapper |
| `app/chat/search.py` | Cross-session and within-session FTS |
| `app/chat/artifacts.py` | Artifact CRUD and content management |
| `app/chat/templates.py` | Prompt library / persona presets |
| `app/chat/workspace_rag.py` | Workspace codebase RAG indexing + retrieval |
| `tests/chat/__init__.py` | Test package |
| `tests/chat/test_intent.py` | Intent classification (18 cases) |
| `tests/chat/test_service.py` | Session CRUD, dispatch, context (32 cases) |
| `tests/chat/test_stream.py` | SSE multiplexing, typing indicator, reasoning (17 cases) |
| `tests/chat/test_scheduling.py` | NL→cron, schedule CRUD (10 cases) |
| `tests/chat/test_rich_output.py` | Table/chart/diff/image routing (10 cases) |
| `tests/chat/test_message_editing.py` | Edit + branch pruning (8 cases) |
| `tests/chat/test_execution.py` | Inline code sandbox (10 cases) |
| `tests/chat/test_search.py` | Cross-session and within-session FTS (8 cases) |
| `tests/chat/test_memory_api.py` | Memory management CRUD (8 cases) |
| `tests/chat/test_services_api.py` | Connected services (6 cases) |
| `tests/chat/test_folders.py` | Session folders CRUD (6 cases) |
| `tests/api/test_chat_api.py` | All 16+ endpoints integration (25 cases) |

### New Alembic Migration
| File | Description |
|---|---|
| `app/db/migrations/versions/XXXX_add_chat_tables.py` | chat_sessions, chat_messages, chat_session_folders, chat_message_usage |

### New Frontend Files
| File | Responsibility |
|---|---|
| `src/features/chat/ChatPage.tsx` | Page: sidebar + thread + input layout |
| `src/features/chat/ChatSidebar.tsx` | Session list with folders, search, pin, TTL badges, agent selector |
| `src/features/chat/ChatThread.tsx` | Virtualized message history + auto-scroll |
| `src/features/chat/ChatInput.tsx` | Textarea, file drop, @mentions, /slash, #file, send |
| `src/features/chat/ChatMessage.tsx` | Renders all message types |
| `src/features/chat/ChatStepCard.tsx` | Goal step with status + expandable tool detail |
| `src/features/chat/ChatGoalSummary.tsx` | Goal complete card + follow-up chips |
| `src/features/chat/ChatClarifyCard.tsx` | Question + quick-reply chips + countdown |
| `src/features/chat/ChatHITLCard.tsx` | Approve/reject + countdown |
| `src/features/chat/ChatGoalFailureCard.tsx` | Failure analysis + 3 suggestion chips |
| `src/features/chat/ChatRichOutput.tsx` | Dispatcher: table/chart/diff/image |
| `src/features/chat/ChatDataTable.tsx` | Sortable/filterable table |
| `src/features/chat/ChatChart.tsx` | Vega-Lite chart renderer |
| `src/features/chat/ChatDiff.tsx` | Unified diff with syntax highlight |
| `src/features/chat/ChatImageOutput.tsx` | Image with fullscreen lightbox |
| `src/features/chat/ChatArtifactPanel.tsx` | Monaco editor side panel |
| `src/features/chat/ChatEmptyState.tsx` | New chat with suggested prompts |
| `src/features/chat/ChatSessionFolders.tsx` | Folder sidebar with drag-and-drop |
| `src/features/chat/TypingIndicator.tsx` | Animated `•••` bubble |
| `src/features/chat/AgentMemoryPage.tsx` | Memory management list/edit/delete |
| `src/features/chat/ConnectedServicesPanel.tsx` | MCP services list + connect/disconnect |
| `src/features/chat/ChatTokenCostBadge.tsx` | Per-message hover tooltip showing tokens + cost |
| `src/features/chat/ChatUsageModal.tsx` | Per-session usage modal (total tokens, cost, LLM calls, goals) |
| `src/features/chat/ChatModelSelector.tsx` | Model dropdown next to send + regenerate-with-model menu |
| `src/features/chat/ChatConversationSummary.tsx` | `/summarize` result card + `[📋 Summary]` button |
| `src/features/chat/ChatSessionSettingsModal.tsx` | Session settings: system_prompt, show_reasoning, proactive_suggestions toggle |
| `src/features/chat/hooks/useChatStream.ts` | SSE hook: opens stream, appends events |
| `src/features/chat/hooks/useChatSession.ts` | TanStack Query: session CRUD |
| `src/features/chat/hooks/useChatHistory.ts` | Load + paginate message history |
| `src/features/chat/hooks/useTypingIndicator.ts` | Typing state management |
| `src/features/chat/hooks/useArtifact.ts` | Artifact panel state |
| `src/features/chat/types/chat.types.ts` | TypeScript types for all chat entities |
| `src/lib/api/chat.ts` | chatApi client with all 16+ API methods |
| `src/features/chat/ChatPage.test.tsx` | Page render + routing tests |
| `src/features/chat/ChatMessage.test.tsx` | Message rendering tests |
| `src/features/chat/useChatStream.test.ts` | SSE hook unit tests |
| `src/features/chat/useChatSession.test.ts` | Session CRUD hook tests |
| `e2e/chat/chat-qa-flow.spec.ts` | E2E: full Q&A flow |
| `e2e/chat/chat-goal-flow.spec.ts` | E2E: full GOAL execution flow |
| `e2e/chat/chat-clarification-flow.spec.ts` | E2E: clarification + HITL |
| `e2e/chat/chat-scheduling-flow.spec.ts` | E2E: schedule a goal |
| `e2e/chat/chat-rich-output.spec.ts` | E2E: table/chart/image rendering |
| `e2e/chat/chat-message-editing.spec.ts` | E2E: edit + regenerate |
| `e2e/chat/chat-artifact-panel.spec.ts` | E2E: artifact canvas panel |
| `e2e/chat/chat-cross-session-search.spec.ts` | E2E: cross-session FTS |
| `e2e/chat/chat-inline-execution.spec.ts` | E2E: inline code execution |
| `e2e/chat/chat-memory-management.spec.ts` | E2E: memory list/delete |
| `e2e/chat/chat-session-folders.spec.ts` | E2E: create folder + move session |
| `e2e/chat/chat-connected-services.spec.ts` | E2E: view + disconnect service |
| `e2e/chat/chat-reasoning-transparency.spec.ts` | E2E: toggle reasoning display |
| `e2e/chat/chat-token-cost-visibility.spec.ts` | E2E: message token hover + session usage modal |
| `e2e/chat/chat-model-selector.spec.ts` | E2E: model selector dropdown + regenerate with different model |
| `e2e/chat/chat-conversation-summary.spec.ts` | E2E: /summarize command + summary card |
| `e2e/chat/chat-session-settings.spec.ts` | E2E: system prompt + reasoning toggle + proactive toggle |

### Modified Files
| File | Change |
|---|---|
| `app/main.py` | Include chat router |
| `app/main_services.py` | Register ChatService in lifespan |
| `src/app/App.tsx` | Add `/chat` and `/chat/:sessionId` routes |
| `src/app/AppLayout.tsx` | Add "Chat" nav item with MessageSquare icon |
| `src/lib/api/client.ts` | Import chatApi |

---

## Phase 1 — Database Migration & Models
**Estimated time:** 2h | **Test command:** `uv run alembic upgrade head`

- [ ] Create Alembic migration `XXXX_add_chat_tables.py`:
  - `chat_session_folders`: id, tenant_id, name, color, position, created_at
  - `chat_sessions`: id, tenant_id, title, pinned, ttl_days, system_prompt, agent_id, folder_id, show_reasoning, created_at, updated_at
  - `chat_messages`: id, session_id, tenant_id, role, content, metadata JSONB, branch_id, created_at
  - `chat_message_usage`: id, message_id, session_id, tokens_in, tokens_out, cost_usd, model, created_at
  - View `chat_session_usage_summary`: `SELECT session_id, SUM(tokens_in) + SUM(tokens_out) AS total_tokens, SUM(cost_usd) AS total_cost` (used by usage modal)
  - Indexes: `idx_chat_sessions_tenant`, `idx_chat_sessions_pinned`, `idx_chat_sessions_folder`, `idx_chat_messages_session`, `idx_chat_messages_fts` (GIN on tsvector)
  - RLS policies for all four tables on `app.tenant_id`

- [ ] Create `app/chat/models.py` with SQLAlchemy async ORM models for all 4 tables
- [ ] Write `tests/chat/test_models.py` — verify model relationships and constraints (5 cases)
- [ ] Run migration: `uv run alembic upgrade head`

---

## Phase 2 — Intent Router
**Estimated time:** 3h | **Test command:** `uv run pytest tests/chat/test_intent.py -v --no-cov`

- [ ] Create `app/chat/intent.py` with `Intent` enum (QA / GOAL / CLARIFY / SCHEDULE)
- [ ] Implement `IntentRouter.classify(message, history)` — fast LLM call (~50-100ms)
- [ ] Implement `IntentRouter.generate_clarifying_question(message, history)` → `ClarifyRequest`
- [ ] Implement `IntentRouter.generate_schedule_confirmation(message, history)` → `ScheduleConfirmation`
- [ ] Write `tests/chat/test_intent.py`:
  - [ ] test_qa_intent_question_words (what, how, why, explain)
  - [ ] test_goal_intent_action_verbs (deploy, run, create, fix, build)
  - [ ] test_clarify_intent_underspecified_goal
  - [ ] test_schedule_intent_time_expressions (every, daily, weekly, at X)
  - [ ] test_qa_ambiguous_short_message (falls back to QA)
  - [ ] test_classify_uses_conversation_history
  - [ ] test_generate_clarifying_question_returns_question_text
  - [ ] test_generate_clarifying_question_returns_options
  - [ ] test_clarify_max_3_rounds (4th round → forces GOAL)
  - [ ] test_schedule_cron_extraction (NL → cron expression)
  - [ ] test_goal_with_file_context
  - [ ] test_qa_with_system_prompt_persona
  - [ ] test_intent_with_empty_history
  - [ ] test_intent_with_20_turn_history
  - [ ] test_clarify_returns_options_array
  - [ ] test_schedule_next_run_calculation
  - [ ] test_qa_code_question
  - [ ] test_goal_keyword_boundary_case

---

## Phase 3 — Conversation Context Builder
**Estimated time:** 2h | **Test command:** `uv run pytest tests/chat/test_service.py::test_context -v --no-cov`

- [ ] Create `app/chat/context.py` with `ConversationContext` class:
  - [ ] `build_for_qa(session_id)` → last 20 turns as message list
  - [ ] `build_for_goal(session_id)` → compact summary string (max 500 tokens)
  - [ ] `compress_long_session(session_id)` → summarize old turns when > 100 messages
  - [ ] `inject_long_term_memory(tenant_id, message, turns)` → inject top-3 memories
  - [ ] `inject_workspace_rag(message, turns)` → inject top-5 codebase snippets
  - [ ] `inject_system_prompt(session, turns)` → prepend session system_prompt
  - [ ] `inject_file_context(file_ids, turns)` → prepend uploaded file content

---

## Phase 4 — SSE Stream Generator
**Estimated time:** 3h | **Test command:** `uv run pytest tests/chat/test_stream.py -v --no-cov`

- [ ] Create `app/chat/stream.py` with `stream_session(session_id, tenant_id)` async generator:
  - [ ] Multiplex from two sources: LLM token generator + Redis pub/sub goal events
  - [ ] Emit `typing_started` within 100ms of message receipt
  - [ ] Emit `routing` event after intent classification
  - [ ] Emit `token` events for Q&A streaming
  - [ ] Bridge `step_started`, `step_complete`, `tool_call` from Redis → SSE
  - [ ] Emit `clarify_needed` when executor pauses
  - [ ] Emit `hitl_required` on high-risk step
  - [ ] Emit `failure_analysis` after goal failure
  - [ ] Emit `proactive_suggestions` after goal success
  - [ ] Emit `reasoning` when thinking mode enabled
  - [ ] Emit `artifact_created` when agent produces large output
  - [ ] Emit `schedule_created` after scheduling
  - [ ] Emit `message_complete` when done
  - [ ] Emit `error` on stream-level failure
  - [ ] Handle SSE reconnect with `Last-Event-ID` header replay (60s buffer in Redis)

- [ ] Write `tests/chat/test_stream.py`:
  - [ ] test_typing_indicator_emitted_within_100ms
  - [ ] test_routing_event_emitted_after_intent_classification
  - [ ] test_qa_tokens_streamed_in_order
  - [ ] test_goal_step_events_bridged_from_redis
  - [ ] test_clarify_needed_pauses_stream
  - [ ] test_hitl_required_event
  - [ ] test_goal_complete_event_with_summary
  - [ ] test_failure_analysis_event_after_goal_failure
  - [ ] test_proactive_suggestions_event
  - [ ] test_reasoning_event_when_show_reasoning_true
  - [ ] test_artifact_created_event
  - [ ] test_schedule_created_event
  - [ ] test_message_complete_event
  - [ ] test_error_event_on_stream_failure
  - [ ] test_sse_reconnect_replays_missed_events
  - [ ] test_multiple_concurrent_sessions
  - [ ] test_stream_cleanup_on_goal_cancel

---

## Phase 5 — ChatService Core (Session + Message CRUD)
**Estimated time:** 4h | **Test command:** `uv run pytest tests/chat/test_service.py -v --no-cov`

- [ ] Create `app/chat/service.py` with `ChatService`:

  **Session CRUD:**
  - [ ] `create_session(tenant_id, agent_id, system_prompt, folder_id)` → ChatSession
  - [ ] `get_session(session_id, tenant_id)` → ChatSession with last 50 messages
  - [ ] `list_sessions(tenant_id, page, per_page, pinned, search, folder_id)` → paginated
  - [ ] `update_session(session_id, tenant_id, title, pinned, agent_id, folder_id, system_prompt, show_reasoning)` → ChatSession
  - [ ] `delete_session(session_id, tenant_id)` — cascades messages
  - [ ] `auto_generate_title(content)` → first 60 chars of first message

  **Message Dispatch:**
  - [ ] `send_message(session_id, content, tenant_id, file_ids, agent_id)` → {message_id, intent, goal_id, stream_url}
  - [ ] `submit_as_goal(session_id, message, tenant_id)` → calls `GoalService.submit_goal()` unchanged
  - [ ] `handle_clarify_intent(session_id, message, history)` → stores clarify question, returns question text
  - [ ] `resolve_clarification(session_id, answer)` → injects answer + resubmits goal
  - [ ] `handle_schedule_intent(session_id, message, tenant_id)` → calls `NLScheduler` + `ScheduleStore`
  - [ ] `cancel_goal(session_id, goal_id)` → signals Celery task
  - [ ] `edit_message(session_id, message_id, new_content, tenant_id)` → prunes branch, re-streams

  **Context:**
  - [ ] Uses `ConversationContext` for all LLM calls
  - [ ] Stores `metadata.intent`, `metadata.goal_id`, `metadata.reasoning` on messages
  - [ ] Stores `metadata.file_context` for `#file:` references

- [ ] Write `tests/chat/test_service.py` (32 cases covering all above methods)

---

## Phase 6 — Scheduling from Chat
**Estimated time:** 2h | **Test command:** `uv run pytest tests/chat/test_scheduling.py -v --no-cov`

- [ ] Add `schedule_goal_from_chat(session_id, message, tenant_id)` to ChatService:
  - Parses NL → `TriggerSpec` via existing `NLScheduler`
  - Creates schedule via `ScheduleStore.create()`
  - Returns `{schedule_id, cron, next_run, description}`
  - Streams `schedule_created` SSE event

- [ ] Add schedule management to `app/chat/router.py`:
  - `POST /v1/chat/sessions/{id}/schedules`
  - `GET /v1/chat/sessions/{id}/schedules`
  - `DELETE /v1/chat/sessions/{id}/schedules/{sid}`

- [ ] Write `tests/chat/test_scheduling.py` (10 cases):
  - NL→cron, next_run calculation, schedule CRUD, trigger→goal, cancel, sideline badge

---

## Phase 7 — Rich Output Rendering (Backend)
**Estimated time:** 2h | **Test command:** `uv run pytest tests/chat/test_rich_output.py -v --no-cov`

- [ ] Add `output_type` detection to `ChatService.submit_as_goal()`:
  - Reads `goal_complete` event metadata from GoalService
  - Detects: `table`, `chart`, `diff`, `image`, `artifact`, `text`
  - Stores `output_url`, `output_type` in message metadata

- [ ] Write `tests/chat/test_rich_output.py` (10 cases):
  - table/chart/diff/image routing, output_url present, lightbox metadata, download URL generation

---

## Phase 8 — Message Editing + Branching
**Estimated time:** 2h | **Test command:** `uv run pytest tests/chat/test_message_editing.py -v --no-cov`

- [ ] Add `branch_id UUID` column to `chat_messages` (separate migration revision)
- [ ] Implement `ChatService.edit_message(session_id, message_id, new_content, tenant_id)`:
  - Soft-deletes all messages after `message_id` (sets `branch_id = new_uuid`)
  - Updates message content
  - Re-dispatches message (triggers new streaming)
- [ ] Add `PATCH /v1/chat/sessions/{id}/messages/{msg_id}` endpoint

- [ ] Write `tests/chat/test_message_editing.py` (8 cases):
  - Edit pruning, branch UUID set, regeneration triggered, original messages preserved with branch_id, concurrent edit safety

---

## Phase 9 — Inline Code Execution
**Estimated time:** 3h | **Test command:** `uv run pytest tests/chat/test_execution.py -v --no-cov`

- [ ] Create `app/chat/execution.py` with `ChatCodeExecutor`:
  - Wraps existing `app/execution_environment/` sandbox
  - `execute(code, language, session_id)` → async SSE generator of stdout/stderr lines
  - Supported: Python 3.12, JavaScript (Node 22), Bash
  - Limits: 30s timeout, 256MB memory, no network, no filesystem writes
  - Returns exit_code, truncated output (max 10K chars)

- [ ] Add `POST /v1/chat/sessions/{id}/execute` endpoint

- [ ] Write `tests/chat/test_execution.py` (10 cases):
  - Python hello world, JS snippet, bash command, 30s timeout, memory limit, no-network enforcement, output truncation, exit_code capture, stdout/stderr separation, SSE streaming

---

## Phase 10 — Cross-Session + Within-Session Search
**Estimated time:** 2h | **Test command:** `uv run pytest tests/chat/test_search.py -v --no-cov`

- [ ] Create `app/chat/search.py` with:
  - `cross_session_search(query, tenant_id, limit)` → uses `idx_chat_messages_fts` GIN index with `ts_headline` snippets
  - `within_session_search(query, session_id, tenant_id, limit)` → scoped to one session

- [ ] Add endpoints:
  - `GET /v1/chat/search?q=<query>&limit=20` → cross-session
  - `GET /v1/chat/sessions/{id}/search?q=<query>` → within-session

- [ ] Write `tests/chat/test_search.py` (8 cases):
  - cross-session FTS, within-session FTS, snippet highlighting, RLS enforcement, pagination, empty results, special characters, FTS language normalization

---

## Phase 11 — Memory Management API
**Estimated time:** 2h | **Test command:** `uv run pytest tests/chat/test_memory_api.py -v --no-cov`

- [ ] Create `app/chat/memory_api.py` — REST layer over `LongTermMemoryStore`:
  - `GET /v1/memories` → list all memories for tenant, sorted by created_at desc
  - `POST /v1/memories` → add manual memory `{content: str}`
  - `PATCH /v1/memories/{id}` → edit memory text
  - `DELETE /v1/memories/{id}` → delete single memory (creates audit log entry)
  - `DELETE /v1/memories` → delete ALL memories (GDPR right to erasure, requires confirmation header)

- [ ] Write `tests/chat/test_memory_api.py` (8 cases):
  - list, create, edit, delete single, delete all, RLS isolation, audit log creation, GDPR header required

---

## Phase 12 — Session Folders
**Estimated time:** 2h | **Test command:** `uv run pytest tests/chat/test_folders.py -v --no-cov`

- [ ] Create folder endpoints in `app/chat/router.py`:
  - `GET /v1/chat/folders` → list folders with session counts
  - `POST /v1/chat/folders` → create `{name, color}`
  - `PATCH /v1/chat/folders/{id}` → rename / recolor
  - `DELETE /v1/chat/folders/{id}` → delete (sessions move to folder_id=NULL)
  - (Session assignment via existing `PATCH /v1/chat/sessions/{id}` with `folder_id`)

- [ ] Write `tests/chat/test_folders.py` (6 cases):
  - create, rename, recolor, delete with session cascade-to-null, session assignment, session count badge

---

## Phase 13 — Connected Services Panel API
**Estimated time:** 2h | **Test command:** `uv run pytest tests/chat/test_services_api.py -v --no-cov`

- [ ] Create `app/chat/services_api.py` — REST layer over `MCPRegistry`:
  - `GET /v1/chat/services` → list all MCP connectors for tenant with {name, url, scopes, status}
  - `POST /v1/chat/services` → initiate connection (returns OAuth URL or setup wizard URL)
  - `DELETE /v1/chat/services/{id}` → disconnect (removes from MCPRegistry + revokes Vault credentials)

- [ ] Write `tests/chat/test_services_api.py` (6 cases):
  - list services, connect initiation returns URL, disconnect removes from registry, RLS isolation, credential revocation, service status check

---

## Phase 14 — Artifacts + Prompt Templates
**Estimated time:** 2h

- [ ] Create `app/chat/artifacts.py` with artifact CRUD:
  - Artifacts stored in object storage (MinIO/S3)
  - `artifact_created` SSE event triggers on code blocks > 50 lines or `output_type=artifact`

- [ ] Create `app/chat/templates.py`:
  - Built-in personas: Security Auditor, Data Analyst, DevOps Engineer, QA Engineer, Architect
  - `GET /v1/chat/templates` → list built-in + user-saved templates
  - `POST /v1/chat/templates` → save session system_prompt as template
  - `DELETE /v1/chat/templates/{id}` → delete user template

---

## Phase 14b — Token/Cost Visibility + SemanticCache
**Estimated time:** 2h | **Test command:** `uv run pytest tests/chat/test_service.py::test_cost -v --no-cov`

- [ ] Wire `SemanticCache` into `ChatService` Q&A path:
  - `SemanticCache.lookup(message, tenant_id)` → cache hit returns immediately (< 50ms)
  - On miss: call LLM, then `SemanticCache.store(message, response, tenant_id)`
  - Log `metadata.cache_hit = true` on cache-hit messages

- [ ] Track token usage on every LLM call in `ChatService`:
  - After each LLM call: insert row into `chat_message_usage`
  - Fields: `tokens_in`, `tokens_out`, `cost_usd`, `model`
  - Use existing `CostControl` for cost calculation

- [ ] Add `GET /v1/chat/sessions/{id}/usage` endpoint:
  ```json
  {
    "total_tokens": 12450,
    "input_tokens": 9200,
    "output_tokens": 3250,
    "total_cost_usd": 0.04,
    "llm_calls": 14,
    "goal_executions": 2,
    "cache_hits": 3
  }
  ```

- [ ] Add `GET /v1/chat/sessions/{id}/export` endpoint:
  - Returns session as clean markdown with messages + goal summaries
  - Used by sidebar "Export" context menu

- [ ] Add `POST /v1/chat/sessions/{id}/messages/{msg_id}/feedback` endpoint:
  - Body: `{rating: 1|-1, comment?: str}`
  - Stores feedback in `chat_messages.metadata.feedback`
  - Used by thumbs up/down UI buttons

---

## Phase 14c — Model Selector + Conversation Summary
**Estimated time:** 2h

- [ ] Add model override to message dispatch in `ChatService.send_message()`:
  - Accept optional `model_id` param in `POST /v1/chat/sessions/{id}/messages`
  - If provided, bypass `ModelRouter` and use specified model for Q&A
  - Store `metadata.model_id` on resulting assistant message

- [ ] Add regenerate-with-model to `ChatService.edit_message()`:
  - Accept optional `model_id` param in `PATCH /v1/chat/sessions/{id}/messages/{msg_id}`
  - Re-streams response using specified model

- [ ] Add `/summarize` slash command handling in `ChatService`:
  - Detects `/summarize` as special slash command (not intent-routed)
  - Sends last 50 messages to fast summarization model (GPT-4o-mini)
  - Returns `{topic, what_we_did: [], outstanding: [], key_files: [], duration, message_count, goal_count}`
  - Streams result as special `summary` SSE event type

- [ ] Add `summary` SSE event type to stream generator

---

## Phase 14d — Session Settings
**Estimated time:** 1h

- [ ] Session settings modal is handled by existing `PATCH /v1/chat/sessions/{id}` endpoint
- [ ] Ensure the following fields are patchable: `system_prompt`, `show_reasoning`, `proactive_suggestions_enabled`
- [ ] Add `proactive_suggestions_enabled` boolean to `chat_sessions` table (migration addendum)
- [ ] In `ChatService.submit_as_goal()`: check `session.proactive_suggestions_enabled` before triggering proactive suggestions LLM call

---

## Phase 15 — Main Router Assembly
**Estimated time:** 2h | **Test command:** `uv run pytest tests/api/test_chat_api.py -v --no-cov`

- [ ] Create `app/chat/router.py` with ALL endpoints wired:
  ```
  POST   /v1/chat/sessions
  GET    /v1/chat/sessions
  GET    /v1/chat/sessions/{id}
  DELETE /v1/chat/sessions/{id}
  PATCH  /v1/chat/sessions/{id}
  POST   /v1/chat/sessions/{id}/messages
  GET    /v1/chat/sessions/{id}/stream
  PATCH  /v1/chat/sessions/{id}/messages/{msg_id}
  POST   /v1/chat/sessions/{id}/execute
  POST   /v1/chat/sessions/{id}/schedules
  GET    /v1/chat/sessions/{id}/schedules
  DELETE /v1/chat/sessions/{id}/schedules/{sid}
  GET    /v1/chat/sessions/{id}/search
  POST   /v1/chat/sessions/{id}/push-subscription
  GET    /v1/chat/search
  GET    /v1/chat/files
  GET    /v1/chat/folders
  POST   /v1/chat/folders
  PATCH  /v1/chat/folders/{id}
  DELETE /v1/chat/folders/{id}
  GET    /v1/chat/templates
  POST   /v1/chat/templates
  DELETE /v1/chat/templates/{id}
  GET    /v1/memories
  POST   /v1/memories
  PATCH  /v1/memories/{id}
  DELETE /v1/memories/{id}
  DELETE /v1/memories
  GET    /v1/chat/services
  POST   /v1/chat/services
  DELETE /v1/chat/services/{id}
  POST   /v1/chat/sessions/{id}/messages/{msg_id}/feedback
  GET    /v1/chat/sessions/{id}/usage
  GET    /v1/chat/sessions/{id}/export
  ```

- [ ] Register in `app/main.py`: `app.include_router(chat_router, prefix="/v1")`
- [ ] Register `ChatService` in `app/main_services.py` lifespan

- [ ] Write `tests/api/test_chat_api.py` (25 cases):
  - [ ] test_create_session_returns_201
  - [ ] test_list_sessions_paginated
  - [ ] test_get_session_includes_messages
  - [ ] test_delete_session_cascades_messages
  - [ ] test_patch_session_pin_unpin
  - [ ] test_patch_session_system_prompt
  - [ ] test_patch_session_folder_assignment
  - [ ] test_send_message_returns_202_with_stream_url
  - [ ] test_stream_endpoint_returns_sse
  - [ ] test_edit_message_prunes_branch
  - [ ] test_execute_code_returns_output
  - [ ] test_create_schedule
  - [ ] test_cross_session_search_returns_snippets
  - [ ] test_within_session_search
  - [ ] test_list_folders
  - [ ] test_create_folder
  - [ ] test_list_templates_includes_built_in
  - [ ] test_memory_list_rls_isolated
  - [ ] test_memory_delete_creates_audit_log
  - [ ] test_services_list_wraps_mcp_registry
  - [ ] test_files_endpoint_returns_content
  - [ ] test_push_subscription_registration
  - [ ] test_rls_enforced_across_tenants
  - [ ] test_rate_limits_applied
  - [ ] test_all_endpoints_require_auth

---

## Phase 16 — Backend Full Test Run
**Test command:** `uv run pytest tests/chat/ tests/api/test_chat_api.py -v --no-cov`

- [ ] All 143+ backend tests passing
- [ ] `uv run ruff check app/chat/` — zero lint errors
- [ ] `uv run mypy app/chat/` — zero type errors

---

## Phase 17 — Frontend: TypeScript Types + API Client
**Estimated time:** 2h | **Test command:** `npm run typecheck`

- [ ] Create `src/features/chat/types/chat.types.ts`:
  ```typescript
  // All chat entities, SSE event types, API request/response types
  ChatSession, ChatMessage, ChatSessionFolder, ChatTemplate
  ChatSSEEvent (union of all 15 event types)
  ChatStreamState, IntentType, OutputType
  TypingState, ArtifactState, ClarifyCard, HITLCard
  GoalFailureCard, ProactiveSuggestion
  ```

- [ ] Create `src/lib/api/chat.ts` with `chatApi` object:
  ```typescript
  createSession, listSessions, getSession, deleteSession,
  updateSession, sendMessage, editMessage, streamUrl,
  executeCode, createSchedule, listSchedules, deleteSchedule,
  searchAll, searchSession, getFile, registerPush,
  listFolders, createFolder, updateFolder, deleteFolder,
  listTemplates, createTemplate, deleteTemplate,
  listMemories, createMemory, updateMemory,
  deleteMemory, deleteAllMemories,
  listServices, connectService, disconnectService
  ```

- [ ] `npm run typecheck` — zero type errors

---

## Phase 18 — Frontend: Core Hooks
**Estimated time:** 3h | **Test command:** `npm test -- src/features/chat/hooks`

- [ ] `useChatStream.ts`:
  - Opens `EventSource` for `GET /v1/chat/sessions/{id}/stream`
  - Parses all 15 SSE event types into typed dispatch actions
  - Exponential backoff reconnect (1s, 2s, 4s, 8s, 16s, 30s max)
  - `Last-Event-ID` header for missed event replay
  - Cleans up on unmount

- [ ] `useChatSession.ts`:
  - TanStack Query hooks for all session CRUD operations
  - Optimistic message insertion on send
  - Invalidates session list on create/delete

- [ ] `useChatHistory.ts`:
  - Infinite scroll: loads 50 messages per page
  - Prepends older messages on scroll up

- [ ] `useTypingIndicator.ts`:
  - Sets typing=true on message send (within 100ms)
  - Clears on first `routing` or `token` SSE event

- [ ] `useArtifact.ts`:
  - Manages artifact panel open/close/content state
  - Triggers on `artifact_created` event

- [ ] Write hook unit tests:
  - [ ] `useChatStream.test.ts` (8 cases): event parsing, reconnect, cleanup, Last-Event-ID
  - [ ] `useChatSession.test.ts` (6 cases): CRUD, optimistic insert, pagination
  - [ ] `useTypingIndicator.test.ts` (4 cases): timing, clear on event

---

## Phase 19 — Frontend: Chat Input Component
**Estimated time:** 3h | **Test command:** `npm test -- ChatInput`

- [ ] `ChatInput.tsx`:
  - Auto-growing textarea (1→8 lines)
  - **Enter** = send, **Shift+Enter** = newline, **↑** = edit last message
  - File attachment (click paperclip or drag-drop into input area)
  - Image paste (Ctrl+V) — extracts from clipboard, adds as attachment chip
  - `@agent` autocomplete on `@` keypress
  - `/slash` command palette on `/` keypress
  - `#file:` autocomplete on `#` keypress with file picker
  - Character count badge when > 2000 chars
  - Token count estimate badge
  - Model selector dropdown (collapsed by default)
  - Disabled state while goal running (shows "Agent is working...")

- [ ] Write `ChatInput.test.tsx` (8 cases): send, shift+enter newline, file attachment, image paste, mention autocomplete, slash commands, disabled state, character count

---

## Phase 20 — Frontend: Message Rendering Components
**Estimated time:** 4h | **Test command:** `npm test -- ChatMessage`

- [ ] `TypingIndicator.tsx`:
  - Three CSS-animated dots `•••`
  - `aria-live="polite"`, `aria-label="Agent is thinking"`
  - Appears within 100ms of send

- [ ] `ChatMessage.tsx`:
  - Routes to correct sub-renderer by message type
  - User bubble: right-aligned, blue, markdown support
  - Assistant bubble: left-aligned, dark, full GFM markdown
  - Syntax-highlighted code blocks with language label + copy button
  - Hover actions: copy, thumbs up/down, retry (Q&A only), share
  - Timestamp on hover
  - "↺ Edited" label for edited messages
  - Reasoning section: collapsible `<details>/<summary>` when `show_reasoning` enabled

- [ ] `ChatStepCard.tsx`:
  - States: pending ⏳ / running spinner / complete ✅ / failed ❌ / skipped ⊘
  - Expandable tool detail (tool name + args + output)
  - Elapsed time badge

- [ ] `ChatGoalSummary.tsx`:
  - Completion card with timing + summary text
  - Suggested follow-up chips (2-3 contextual)
  - `[View full trace]` `[Copy summary]` `[Run again ▼]`
  - Parameterized re-run: "Run with overrides..." form with detected params

- [ ] `ChatClarifyCard.tsx`:
  - Question text + quick-reply chip pills (one-click send)
  - Free-form answer still accepted
  - Countdown timer
  - `role="alertdialog"`, focus trap until answered

- [ ] `ChatHITLCard.tsx`:
  - `[✓ Approve]` `[✗ Reject]` `[💬 Give feedback]`
  - Countdown timer (10 minutes)
  - `role="alertdialog"`, focus trap

- [ ] `ChatGoalFailureCard.tsx`:
  - Error summary + failure analysis text
  - 3 suggestion chips with goal text
  - `[↺ Retry original]` button

- [ ] Write `ChatMessage.test.tsx` (10 cases): user bubble, assistant GFM, code block copy, thumbs feedback, reasoning collapsible, step card states, clarify chips, HITL buttons, failure analysis, edited label

---

## Phase 21 — Frontend: Rich Output Components
**Estimated time:** 4h | **Test command:** `npm test -- ChatRichOutput`

- [ ] `ChatRichOutput.tsx` — dispatcher based on `output_type`

- [ ] `ChatDataTable.tsx`:
  - `react-table` with column sorting and filtering
  - Hover row highlight
  - `[Download CSV]` `[Copy as markdown]` actions
  - Virtualized rows for > 500 rows

- [ ] `ChatChart.tsx`:
  - `react-vega` Vega-Lite renderer
  - Bar, line, pie, scatter support
  - Hover tooltips
  - Dark-mode compatible color scheme
  - `[Download PNG]` `[Copy spec]` actions

- [ ] `ChatDiff.tsx`:
  - Unified diff with `react-diff-view`
  - Red/green syntax highlighting
  - File-level collapse/expand
  - `[Copy patch]` action

- [ ] `ChatImageOutput.tsx`:
  - Lazy-loaded `<img>` with proper `alt` from goal text
  - Max width 512px, click for fullscreen lightbox
  - `[📥 Download]` `[🔗 Copy URL]` `[↺ Regenerate]` actions
  - `loading="lazy"` for performance

- [ ] Write tests (6 cases): table sort, chart renders, diff highlights, image lightbox, download triggers, empty state

---

## Phase 22 — Frontend: Artifact Panel
**Estimated time:** 3h

- [ ] `ChatArtifactPanel.tsx`:
  - Monaco editor (same as VS Code) for code artifacts
  - Side panel — resizable, 280-600px width
  - Triggers on `artifact_created` SSE event or code blocks > 50 lines
  - `[Copy]` `[Download]` `[Run]` `[Share]` `[Close panel ×]` actions
  - Diff mode: shows changes when agent modifies existing artifact
  - Agent awareness: subsequent messages include artifact content as context
  - Keyboard: `Escape` closes panel

---

## Phase 23 — Frontend: Chat Thread + Auto-scroll
**Estimated time:** 3h

- [ ] `ChatThread.tsx`:
  - Virtualized message list with `react-virtual` (`useVirtualizer`)
  - Auto-scroll to bottom on new messages (when user is at bottom)
  - "↓ New messages" badge when user scrolls up and new message arrives
  - Date separators between messages from different days
  - Infinite scroll upward: loads 50 older messages per page
  - `aria-live="polite"` announcements for new messages
  - Loading skeleton while fetching history

---

## Phase 24 — Frontend: Chat Sidebar
**Estimated time:** 3h

- [ ] `ChatSidebar.tsx`:
  - Session list grouped by: 📌 Pinned, Today, Yesterday, This Week, Older
  - `ChatSessionFolders.tsx`: folder tree with drag-and-drop sessions
  - 🔍 Search input with fuzzy filter on title
  - `[+ New Chat]` button (top-right)
  - Agent selector dropdown (bottom of sidebar)
  - Session item: title, pin icon, TTL badge (amber when < 3 days), 🔵 active goal dot, 🕐 schedule badge, 🔧 custom prompt badge
  - Context menu (right-click / ⋮): Rename, Pin/Unpin, Move to Folder, Export, Delete
  - Responsive: icon rail on tablet, drawer on mobile

---

## Phase 25 — Frontend: Chat Empty State
**Estimated time:** 1h

- [ ] `ChatEmptyState.tsx`:
  - Starter prompt grid (4 cards):
    - "💬 What can AgentVerse do?"
    - "🧪 Run all tests and fix failures"
    - "📊 Analyze Q3 revenue data"
    - "🚀 Deploy to staging"
  - Agent capability card: shows tools the selected agent has
  - `[⚡ Quick start]` button → persona picker (Security Auditor, Data Analyst, DevOps, QA, Architect)
  - Configurable prompts per tenant

---

## Phase 26 — Frontend: Agent Memory Page
**Estimated time:** 2h

- [ ] `AgentMemoryPage.tsx`:
  - Route: `/settings/memory` or accessible from sidebar footer
  - List of memories grouped: 📌 Permanent, 📅 Recent learnings
  - Each memory: text + `[Edit]` `[Delete]` buttons
  - `[+ Add memory manually]` button (textarea → POST /v1/memories)
  - `[Delete all memories]` with confirmation modal
  - "🧠 Agent remembers X things about you" link in sidebar footer

---

## Phase 27 — Frontend: Connected Services Panel
**Estimated time:** 2h

- [ ] `ConnectedServicesPanel.tsx`:
  - Accessible via "🔌 N services connected" thread header button
  - Lists each MCP connector: name, URL, scopes, status badge
  - `[Manage]` → opens OAuth re-auth if needed
  - `[Disconnect]` → calls `DELETE /v1/chat/services/{id}` with confirmation
  - `[+ Connect a service]` → shows service type buttons: GitHub, Jira, Slack, Notion, Custom MCP...

---

## Phase 27b — Frontend: Token/Cost UI + Model Selector + Summary
**Estimated time:** 4h

- [ ] `ChatTokenCostBadge.tsx`:
  - Hidden by default; appears on hover of any assistant message
  - Shows: `~847 tokens · ~$0.003`
  - Source: `message.metadata.tokens_in + tokens_out + cost_usd`

- [ ] `ChatUsageModal.tsx`:
  - Opened from thread header "💰 Session cost" or `/usage` slash command
  - Displays: total tokens, input/output split, LLM calls, goal executions, cache hits, total cost
  - Tenant budget bar: "847,320 / 2,000,000 tokens today"
  - `[View usage dashboard →]` link

- [ ] `ChatModelSelector.tsx`:
  - Collapsed icon next to send button; expands on click
  - Lists models configured for tenant from `LLMConfigStore`
  - On existing assistant message: `[↺ Regenerate ▼]` → per-model regenerate options
  - Clicking different model calls `PATCH /messages/{id}` with `{model_id}`
  - Stores chosen model in message `metadata.model_id`

- [ ] `ChatConversationSummary.tsx`:
  - Triggered by `/summarize` slash command or `[📋 Summary]` button in thread header
  - Renders pinned summary card: topic, what we did (bullet list), outstanding, key files
  - Shows duration + message count + goal count
  - `[Close ×]` dismisses the card

- [ ] `ChatSessionSettingsModal.tsx`:
  - `⚙️` button in thread header opens modal
  - "Custom instructions" textarea (system_prompt, max 500 chars, placeholder: "Act as a security expert...")
  - Toggle: "Show agent thinking" (`show_reasoning`, default off)
  - Toggle: "Proactive insights after goals" (`proactive_suggestions_enabled`, default on)
  - On save: `PATCH /v1/chat/sessions/{id}`
  - `🔧 Custom` badge shown in sidebar when system_prompt is set

- [ ] Update `ChatInput.tsx` to handle `/summarize` and `/usage` slash commands

---

## Phase 28 — Frontend: Chat Page Assembly + Routing
**Estimated time:** 2h | **Test command:** `npm run typecheck && npm run lint`

- [ ] `ChatPage.tsx`:
  - Layout: `ChatSidebar` + `ChatMain` (which contains `ChatThread` + `ChatInput` + optional `ChatArtifactPanel`)
  - Manages global chat state via `useChatSession` + `useChatStream`
  - Handles keyboard shortcuts:
    - `Ctrl/Cmd+K` → new chat
    - `Ctrl/Cmd+/` → focus input
    - `Ctrl/Cmd+P` → open search (cross-session)
    - `Escape` → close sidebar (mobile) / close HITL confirmation
    - `↑` → edit last user message

- [ ] Update `src/app/App.tsx`:
  ```tsx
  <Route path="/chat" element={<ChatPage />} />
  <Route path="/chat/:sessionId" element={<ChatPage />} />
  <Route path="/settings/memory" element={<AgentMemoryPage />} />
  ```

- [ ] Update `src/app/AppLayout.tsx`:
  - Add "Chat" nav item with `MessageSquare` icon from lucide-react
  - Position: below Goals, above Settings

---

## Phase 29 — Frontend Unit Tests (Vitest)
**Estimated time:** 4h | **Test command:** `npm test -- --run`

- [ ] Write all missing Vitest unit tests:
  - [ ] `ChatPage.test.tsx` — page renders, sidebar visible, thread visible, routing
  - [ ] `ChatMessage.test.tsx` — all message types render correctly (10 cases)
  - [ ] `ChatDataTable.test.tsx` — sort, filter, download trigger
  - [ ] `ChatChart.test.tsx` — renders with valid Vega-Lite spec
  - [ ] `ChatDiff.test.tsx` — diff highlights red/green
  - [ ] `ChatImageOutput.test.tsx` — lightbox open/close
  - [ ] `ChatArtifactPanel.test.tsx` — Monaco renders, close button
  - [ ] `ChatGoalFailureCard.test.tsx` — suggestion chips clickable
  - [ ] `TypingIndicator.test.tsx` — aria-live, aria-label
  - [ ] `AgentMemoryPage.test.tsx` — list, delete, delete-all confirmation
  - [ ] `ConnectedServicesPanel.test.tsx` — list, disconnect confirmation
  - [ ] `ChatSessionFolders.test.tsx` — create folder, assign session

- [ ] All Vitest tests pass: `npm test -- --run`

---

## Phase 30 — E2E Tests (Playwright)
**Estimated time:** 8h | **Test command:** `npm run test:e2e -- e2e/chat/`

Write all 12 E2E test files. Each file verifies a complete user journey:

### e2e/chat/chat-qa-flow.spec.ts
- [ ] `test_open_new_chat_session` — creates session, sidebar shows it
- [ ] `test_send_qa_message_shows_typing_indicator` — `•••` appears within 1s
- [ ] `test_qa_response_streams_token_by_token` — text appears progressively
- [ ] `test_qa_response_renders_markdown` — headings, bold, code blocks
- [ ] `test_code_block_has_copy_button` — copy button visible on hover
- [ ] `test_thumbs_feedback_buttons` — up/down visible, clickable
- [ ] `test_conversation_history_persists_on_reload` — messages still there after F5
- [ ] `test_session_title_auto_generated` — title = first 60 chars of first message

### e2e/chat/chat-goal-flow.spec.ts
- [ ] `test_goal_intent_shows_routing_indicator` — 🔵 Routing... appears
- [ ] `test_goal_step_cards_appear_inline` — step cards visible in thread
- [ ] `test_step_cards_expand_on_click` — tool detail visible
- [ ] `test_stop_button_visible_during_goal` — ■ Stop visible
- [ ] `test_stop_button_cancels_goal` — goal status becomes cancelled
- [ ] `test_goal_complete_card_shows_summary` — summary + timing visible
- [ ] `test_suggested_followup_chips_visible` — 2-3 chips appear
- [ ] `test_run_again_button_restarts_goal` — new goal starts on click

### e2e/chat/chat-clarification-flow.spec.ts
- [ ] `test_underspecified_goal_shows_clarify_card` — question bubble appears
- [ ] `test_quick_reply_chip_sends_answer` — chip click → answer sent as message
- [ ] `test_free_form_answer_accepted` — typed answer works too
- [ ] `test_max_3_clarify_questions` — 4th message proceeds without question
- [ ] `test_mid_execution_clarify_pauses_goal` — step card shows ⏸ Waiting
- [ ] `test_hitl_card_appears_for_high_risk_steps` — approve/reject buttons visible
- [ ] `test_hitl_approve_continues_goal` — goal resumes after approve
- [ ] `test_hitl_reject_stops_goal` — goal fails with rejection reason

### e2e/chat/chat-scheduling-flow.spec.ts
- [ ] `test_schedule_intent_shows_confirmation_card` — schedule card with cron
- [ ] `test_schedule_card_shows_next_run_time` — next run time visible
- [ ] `test_cancel_schedule_button_works` — DELETE schedule → card disappears
- [ ] `test_schedule_badge_in_sidebar` — 🕐 badge appears on session

### e2e/chat/chat-rich-output.spec.ts
- [ ] `test_table_output_renders_sortable_table` — sort column
- [ ] `test_table_download_csv_button` — CSV download triggered
- [ ] `test_chart_output_renders_canvas` — Vega-Lite chart visible
- [ ] `test_diff_output_shows_red_green` — diff colors correct
- [ ] `test_image_output_renders_inline` — image visible in thread
- [ ] `test_image_lightbox_opens_on_click` — fullscreen overlay appears

### e2e/chat/chat-message-editing.spec.ts
- [ ] `test_hover_user_message_shows_pencil_icon` — edit icon visible
- [ ] `test_click_edit_makes_message_editable` — textarea appears
- [ ] `test_save_edit_prunes_subsequent_messages` — messages after disappear
- [ ] `test_save_edit_triggers_regeneration` — new typing indicator appears
- [ ] `test_edited_label_shows_on_message` — "↺ Edited" visible

### e2e/chat/chat-artifact-panel.spec.ts
- [ ] `test_large_code_block_opens_artifact_panel` — panel slides in
- [ ] `test_artifact_panel_has_monaco_editor` — Monaco visible
- [ ] `test_artifact_copy_button` — clipboard triggered
- [ ] `test_artifact_close_button` — panel closes
- [ ] `test_artifact_panel_agent_aware` — subsequent message uses artifact context

### e2e/chat/chat-cross-session-search.spec.ts
- [ ] `test_ctrl_p_opens_search_palette` — palette opens
- [ ] `test_search_returns_session_results` — sessions with snippets visible
- [ ] `test_click_result_navigates_to_session` — session opens scrolled to match
- [ ] `test_within_session_search` — session-scoped search returns matches

### e2e/chat/chat-inline-execution.spec.ts
- [ ] `test_run_button_visible_on_code_block` — ▶ Run button appears
- [ ] `test_run_python_code_shows_output` — output renders below block
- [ ] `test_run_js_code_shows_output` — node output renders
- [ ] `test_timeout_shows_error_message` — > 30s shows timeout error

### e2e/chat/chat-memory-management.spec.ts
- [ ] `test_memory_page_lists_memories` — memories visible in list
- [ ] `test_edit_memory` — edit saves and updates in list
- [ ] `test_delete_single_memory` — memory removed from list
- [ ] `test_delete_all_memories_requires_confirmation` — modal appears
- [ ] `test_delete_all_memories_confirmed` — all memories removed

### e2e/chat/chat-session-folders.spec.ts
- [ ] `test_create_folder_in_sidebar` — folder appears in sidebar
- [ ] `test_move_session_to_folder` — session appears under folder
- [ ] `test_folder_shows_session_count` — "(3)" count visible
- [ ] `test_delete_folder_moves_sessions_to_unfiled` — sessions not deleted

### e2e/chat/chat-connected-services.spec.ts
- [ ] `test_services_button_shows_count` — "🔌 3 services" visible
- [ ] `test_services_panel_lists_connections` — GitHub, Postgres visible
- [ ] `test_disconnect_shows_confirmation` — modal appears
- [ ] `test_disconnect_removes_service` — service gone after confirm

### e2e/chat/chat-token-cost-visibility.spec.ts
- [ ] `test_hover_message_shows_token_badge` — badge appears on hover within 200ms
- [ ] `test_session_cost_visible_in_header` — "💰 $0.04" visible in thread header
- [ ] `test_usage_modal_opens_on_click` — modal with full breakdown appears
- [ ] `test_usage_modal_shows_cache_hits` — cache hit count visible
- [ ] `test_export_session_downloads_markdown` — export creates downloadable .md file

### e2e/chat/chat-model-selector.spec.ts
- [ ] `test_model_selector_visible_in_input` — model icon next to send button
- [ ] `test_select_different_model_and_send` — model name reflected in response metadata
- [ ] `test_regenerate_with_different_model` — `[↺ Regenerate ▼]` expands model list
- [ ] `test_regenerate_replaces_last_response` — new response replaces old in thread

### e2e/chat/chat-conversation-summary.spec.ts
- [ ] `test_summarize_slash_command_shows_card` — `/summarize` → summary card appears
- [ ] `test_summary_card_has_topic_and_bullets` — topic + "What we did" bullets visible
- [ ] `test_summary_card_shows_duration` — "47 minutes" visible
- [ ] `test_summary_close_button_dismisses_card` — card removed on close

### e2e/chat/chat-session-settings.spec.ts
- [ ] `test_settings_modal_opens_from_header` — ⚙️ button → modal opens
- [ ] `test_set_system_prompt_applies_to_qa` — persona applied, agent responds differently
- [ ] `test_custom_prompt_badge_in_sidebar` — 🔧 badge visible on session item
- [ ] `test_enable_show_reasoning_toggle` — toggle on → thinking section visible in response
- [ ] `test_disable_proactive_suggestions` — goal completes without proactive card

---

## Phase 31 — Full Test Run + Lint
**Estimated time:** 1h

- [ ] Backend: `uv run pytest tests/chat/ tests/api/test_chat_api.py -v --no-cov` → 150+ tests passing
- [ ] Frontend unit: `npm test -- --run` → all Vitest tests passing
- [ ] E2E: `npm run test:e2e -- e2e/chat/` → 17 spec files, 70+ scenarios passing
- [ ] Backend lint: `uv run ruff check app/chat/` → 0 errors
- [ ] Backend types: `uv run mypy app/chat/` → 0 errors
- [ ] Frontend lint: `npm run lint` → 0 errors
- [ ] Frontend types: `npm run typecheck` → 0 errors

---

## Phase 32 — Accessibility Audit
**Estimated time:** 2h

- [ ] Run `axe-core` in Playwright on `/chat` route — 0 violations
- [ ] Verify keyboard navigation: Tab through all interactive elements
- [ ] Verify `aria-live="polite"` on thread (screen reader announcements)
- [ ] Verify focus management: HITL/clarify cards trap focus, restore on close
- [ ] Verify all icons have `aria-hidden="true"` with visible text labels
- [ ] Verify color contrast WCAG 2.2 AA (4.5:1 normal text, 3:1 large text)
- [ ] Verify 44×44px touch targets on all interactive elements
- [ ] Verify skip-to-main-content link functional

---

## Phase 33 — Performance Verification
**Estimated time:** 1h

- [ ] Session list virtualized: render 200 sessions, measure FPS > 55
- [ ] Message thread virtualized: render 500 messages, no jank
- [ ] SSE reconnect: disconnect network, reconnect, verify `Last-Event-ID` replay
- [ ] Optimistic insert: message appears before 202 response returns
- [ ] Typing indicator: appears within 100ms of send (measure with `performance.now()`)
- [ ] Session search: debounced 300ms, no unnecessary API calls

---

## Phase 34 — Responsive Design QA
**Estimated time:** 1h

- [ ] Desktop (1280px+): sidebar (280px fixed) + thread (flex-1) + optional artifact panel
- [ ] Tablet (640-1024px): sidebar collapses to icon rail (60px), swipe/click to expand
- [ ] Mobile (< 640px): single column; sidebar is drawer from hamburger menu
- [ ] Test drag-and-drop on touch devices (session folders)
- [ ] Test image paste on mobile (share sheet)
- [ ] Verify all keyboard shortcuts work on mobile (software keyboard)

---

## Phase 35 — Dark/Light Mode Verification
**Estimated time:** 30m

- [ ] All chat components render correctly in dark mode
- [ ] All chat components render correctly in light mode
- [ ] Toggle switches correctly between modes
- [ ] Step card status colors visible in both modes
- [ ] Code block syntax highlighting readable in both modes
- [ ] `ChatChart.tsx` uses dark-mode compatible Vega-Lite color scheme

---

## Phase 36 — Commit + Push
**Estimated time:** 30m

- [ ] `git add app/chat/ tests/chat/ tests/api/test_chat_api.py`
- [ ] `git add src/features/chat/ src/lib/api/chat.ts e2e/chat/`
- [ ] `git add app/main.py app/main_services.py src/app/App.tsx src/app/AppLayout.tsx`
- [ ] `git add app/db/migrations/versions/XXXX_add_chat_tables.py`
- [ ] `git commit -m "feat(chat): implement world-class conversational agent interface"`
- [ ] `git push origin main`

---

## Feature Coverage Checklist

Verify all 60+ spec features are implemented:

**Core Chat:**
- [ ] Intelligent routing (QA / GOAL / CLARIFY / SCHEDULE)
- [ ] Token-by-token streaming with typing indicator (•••)
- [ ] Session management (TTL 7d, pinnable forever, search, groups)
- [ ] 20-turn conversation context + compression at 100+ messages
- [ ] Cross-session LongTermMemory injection (top-3 memories)
- [ ] Pre-execution clarification (max 3 questions)
- [ ] Mid-execution `clarify_needed` blocking (pauses, resumes)
- [ ] Quick-reply chips on clarification questions
- [ ] HITL gate with 10-minute auto-reject timeout
- [ ] Stop/cancel button during goal execution
- [ ] File upload (PDF, image, CSV, JSON, TXT)
- [ ] Image paste from clipboard (Ctrl+V)
- [ ] @agent mentions in input
- [ ] /slash commands in input
- [ ] #file context references (file picker + injection)
- [ ] Full GFM markdown + syntax-highlighted code blocks
- [ ] Goal step cards (⏳ → ✅ → ❌) + expandable tool detail
- [ ] Goal complete card + 2-3 contextual follow-up chips
- [ ] Rich output rendering: table (sortable), chart (Vega-Lite), diff (unified), image (lightbox)
- [ ] Goal scheduling from chat ("run every weekday at 9am")
- [ ] Custom system prompt per session (persona)
- [ ] Browser push notifications when goal completes (background)
- [ ] Session export as markdown
- [ ] Keyboard shortcuts (Ctrl+K, Ctrl+/, Ctrl+P, Enter, Shift+Enter, ↑, Escape)
- [ ] Dark/light mode (follows system, manual toggle)
- [ ] Mobile responsive (sidebar → icon rail → drawer)
- [ ] WCAG 2.2 AA accessibility
- [ ] SSE reconnect with exponential backoff + event replay
- [ ] Optimistic message insertion
- [ ] Virtualized session list + message thread
- [ ] Thumbs up/down feedback on messages
- [ ] SemanticCache for Q&A (dedupes LLM calls)
- [ ] RAG retrieval for grounded Q&A (KnowledgeStore)
- [ ] Agent selector per session (in sidebar)
- [ ] OCR on file attachments (via existing `extract_document` tool)
- [ ] Message editing + branch pruning + regeneration
- [ ] Artifacts / Canvas panel (Monaco editor, agent-aware)
- [ ] Cross-session search (FTS with snippets)
- [ ] Within-session search
- [ ] Inline code execution (Python/JS/Bash, sandboxed)
- [ ] Agent capability disclosure card (tools list before first message)
- [ ] Goal failure analysis (auto-diagnose + 3 alternative approaches)
- [ ] Token/cost visibility (per-message hover + per-session modal)
- [ ] Proactive suggestions after goal complete (opt-in toggle)
- [ ] Model selector per message (regenerate with different LLM)
- [ ] Prompt library + built-in personas (Security Auditor, Data Analyst, DevOps, QA, Architect)
- [ ] Parameterized goal re-run ("Run again with overrides")
- [ ] Workspace codebase RAG (auto-indexes repo, no #file needed)
- [ ] Conversation summary on demand (/summarize)
- [ ] Agent memory management UI (list/edit/delete/add memories)
- [ ] Typing/thinking indicator (before first token)
- [ ] Image output rendering (`output_type: "image"`)
- [ ] Session folders/tags (create, assign, recolor, delete)
- [ ] Connected services panel (MCP services list + connect/disconnect)
- [ ] Agent reasoning transparency ("Show thinking" toggle)
- [ ] Session groups: Pinned, Today, Yesterday, This Week, Older
- [ ] 7-day TTL badge (amber when < 3 days)
- [ ] "New Chat" button + Ctrl+K shortcut
- [ ] Conversation auto-title from first message
- [ ] Empty state with 4 suggested prompts + persona picker
- [ ] Follow-up chips after goal: "View trace", "Run again", "Show diff", "Create PR"

---

## Implementation Order Summary

```
Phase 1:  DB Migration + Models          (2h)
Phase 2:  Intent Router                  (3h)
Phase 3:  Context Builder                (2h)
Phase 4:  SSE Stream Generator           (3h)
Phase 5:  ChatService Core               (4h)
Phase 6:  Scheduling from Chat           (2h)
Phase 7:  Rich Output (Backend)          (2h)
Phase 8:  Message Editing + Branching    (2h)
Phase 9:  Inline Code Execution          (3h)
Phase 10: Cross-Session + FTS Search     (2h)
Phase 11: Memory Management API          (2h)
Phase 12: Session Folders                (2h)
Phase 13: Connected Services Panel API   (2h)
Phase 14: Artifacts + Templates          (2h)
Phase 14b:Token/Cost + SemanticCache     (2h)
Phase 14c:Model Selector + Summary       (2h)
Phase 14d:Session Settings               (1h)
Phase 15: Main Router Assembly           (2h)
Phase 16: Backend Full Test Run          (1h)
Phase 17: TS Types + API Client          (2h)
Phase 18: Core Hooks                     (3h)
Phase 19: Chat Input Component           (3h)
Phase 20: Message Rendering Components   (4h)
Phase 21: Rich Output Components         (4h)
Phase 22: Artifact Panel                 (3h)
Phase 23: Chat Thread + Auto-scroll      (3h)
Phase 24: Chat Sidebar                   (3h)
Phase 25: Chat Empty State               (1h)
Phase 26: Agent Memory Page              (2h)
Phase 27: Connected Services Panel       (2h)
Phase 27b:Token/Cost UI + Model Selector (4h)
Phase 28: Page Assembly + Routing        (2h)
Phase 29: Frontend Unit Tests (Vitest)   (4h)
Phase 30: E2E Tests (Playwright)         (10h)
Phase 31: Full Test Run + Lint           (1h)
Phase 32: Accessibility Audit            (2h)
Phase 33: Performance Verification       (1h)
Phase 34: Responsive Design QA           (1h)
Phase 35: Dark/Light Mode Verification   (0.5h)
Phase 36: Commit + Push                  (0.5h)
─────────────────────────────────────────────
Total: ~95 hours (≈ 12 engineering days)
```
