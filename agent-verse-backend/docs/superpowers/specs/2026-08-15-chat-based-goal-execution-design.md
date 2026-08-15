# Chat-Based Goal Execution — Design Spec
**Date:** 2026-08-15  
**Status:** Approved  
**Author:** Brainstorming session

---

## Overview

AgentVerse Chat is a GitHub Copilot / Claude-style conversational interface that lets users ask questions and execute agent goals from a single chat box. The system intelligently routes each message — short questions get a direct token-by-token LLM response; action-oriented messages trigger the full agent goal execution engine with live step streaming.

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Interaction model | **Intelligent Routing (C)** — Q&A + Goal, auto-classified | Single box like Copilot — no user friction |
| Session persistence | **Hybrid** — 7-day TTL, user-pinnable forever | Balance simplicity vs permanence |
| Progress display | **Inline streaming (A)** — step-by-step messages in thread | Transparent, natural in chat |
| Q&A streaming | **Token-by-token** | Feels alive, like ChatGPT/Claude |
| Backend transport | **Pure SSE** (reuses existing GoalService SSE) | Zero new infrastructure |
| Database | **Postgres** (existing) with proper indexes | Reuse RLS, transactions, existing infra |

---

## Architecture

### Backend Package

```
app/chat/
  __init__.py
  models.py        # ChatSession, ChatMessage SQLAlchemy ORM models + Alembic migration
  router.py        # FastAPI router — 7 endpoints
  service.py       # ChatService: session CRUD, message dispatch, history lookup
  intent.py        # IntentRouter: classify QA vs GOAL in ~50-100ms
  stream.py        # SSE stream generator: multiplexes tokens + goal step events
  context.py       # ConversationContext: build last-20-turns for LLM prompt

app/api/chat.py    # Router registration in main.py
tests/chat/
  test_intent.py
  test_service.py
  test_stream.py
  test_api.py
```

### Data Model (Postgres, RLS by tenant_id)

**`chat_sessions`**
```sql
id           UUID  DEFAULT gen_random_uuid() PRIMARY KEY
tenant_id    UUID  NOT NULL  -- FK, RLS enforced
title        TEXT  NOT NULL  -- auto-generated from first message (first 60 chars)
pinned       BOOL  DEFAULT false
ttl_days     INT   DEFAULT 7  -- null = pinned forever
created_at   TIMESTAMPTZ DEFAULT now()
updated_at   TIMESTAMPTZ DEFAULT now()

INDEX idx_chat_sessions_tenant     ON chat_sessions (tenant_id, updated_at DESC)
INDEX idx_chat_sessions_pinned     ON chat_sessions (tenant_id, pinned)
```

**`chat_messages`**
```sql
id           UUID  DEFAULT gen_random_uuid() PRIMARY KEY
session_id   UUID  NOT NULL  REFERENCES chat_sessions(id) ON DELETE CASCADE
tenant_id    UUID  NOT NULL  -- RLS
role         TEXT  NOT NULL  -- 'user' | 'assistant' | 'system'
content      TEXT  NOT NULL  -- full message text
metadata     JSONB           -- {intent, goal_id, step_count, latency_ms, tool_calls}
created_at   TIMESTAMPTZ DEFAULT now()

INDEX idx_chat_messages_session ON chat_messages (session_id, created_at DESC)
INDEX idx_chat_messages_tenant  ON chat_messages (tenant_id, created_at DESC)
```

### API Endpoints

```
POST   /v1/chat/sessions                    Create new session
GET    /v1/chat/sessions                    List sessions (paginated, 20/page)
GET    /v1/chat/sessions/{id}               Get session + last 50 messages
DELETE /v1/chat/sessions/{id}               Delete session + all messages
PATCH  /v1/chat/sessions/{id}               Pin/unpin, rename session
POST   /v1/chat/sessions/{id}/messages      Send message → returns {message_id}
GET    /v1/chat/sessions/{id}/stream        SSE stream for session
```

### SSE Event Schema

All events on `GET /v1/chat/sessions/{id}/stream`:

```json
{"type": "routing",        "intent": "qa|goal",  "message_id": "..."}
{"type": "token",          "content": "Hello",    "message_id": "..."}
{"type": "step_started",   "step": 1,  "description": "Running tests...", "goal_id": "..."}
{"type": "step_complete",  "step": 1,  "result": "✅ 47 tests passed",    "goal_id": "..."}
{"type": "tool_call",      "tool": "run_command", "args": {...},           "goal_id": "..."}
{"type": "goal_complete",  "goal_id": "...", "summary": "All tests pass", "latency_ms": 4300}
{"type": "message_complete","message_id": "...", "role": "assistant"}
{"type": "hitl_required",    "goal_id": "...", "action": "Deploy to prod?", "risk": "high"}
{"type": "clarify_needed",  "goal_id": "...", "question": "Which environment — staging or prod?", "options": ["staging", "prod"], "required": true}
{"type": "error",           "message": "Rate limit exceeded", "code": "RATE_LIMIT"}
```

### Intent Router

```python
class Intent(StrEnum):
    QA = "qa"       # Direct LLM streaming with conversation history
    GOAL = "goal"   # Submit to GoalService + stream live steps
    CLARIFY = "clarify"  # Agent needs to ask clarifying question before routing

class IntentRouter:
    # Fast LLM call (~50-100ms) with last 5 turns + new message
    # Signal: action verbs (deploy, create, run, fix, analyze) → GOAL
    # Signal: question words (what, how, why, explain) → QA
    # Signal: underspecified goal (missing required param) → CLARIFY
    # Ambiguous + short → QA (conservative fallback)
    async def classify(self, message: str, history: list[dict]) -> Intent: ...

    async def generate_clarifying_question(
        self, message: str, history: list[dict]
    ) -> ClarifyRequest:
        # Returns: question text + optional quick-reply options
        # e.g. "Which environment? staging or prod?" + ["staging", "prod", "I'll specify"]
        ...
```

### Clarification Flows

#### Pre-execution clarification (intent = CLARIFY)
When the goal is underspecified, the agent asks **one focused question** before starting:
```
User:   "Deploy the app"
Agent:  ❓ "Which environment should I deploy to?"
        [staging]  [production]  [type your answer]
User:   "staging"
Agent:  🔵 Routing to agent...
        ⚙️ Step 1: Run tests ...
```
The user's answer is appended to the original message and resubmitted as a fully-specified goal.

#### Mid-execution clarifying question (`clarify_needed` SSE event)
The LangGraph executor can emit `clarify_needed` when a step cannot proceed without missing info:
```
⚙️ Step 2: Upload artifact    ⏸ Waiting for input
  ❓ "Which S3 bucket? (found: app-assets-prod, app-assets-staging)"
     [app-assets-staging]  [app-assets-prod]  [type bucket name]
```
- Goal is **paused** (not cancelled) while waiting for the answer
- User's reply is injected as a new chat message → GoalService resumes the paused goal
- 10-minute timeout: goal is cancelled if no response, user is notified
- `message_complete` event is NOT emitted until after clarification + resumption

#### Multiple clarifying questions (multi-turn refinement)
For complex goals the agent may ask up to **3 sequential clarifying questions** before starting execution. After the 3rd question it proceeds regardless, using defaults for anything still unclear. This prevents infinite question loops.

```
User:   "Set up the CI pipeline"
Agent:  ❓ Q1: "Which branches should trigger the pipeline?" 
        [main only]  [main + develop]  [all branches]
User:   "main + develop"
Agent:  ❓ Q2: "Should tests run in parallel or sequentially?"
        [parallel (faster)]  [sequential (safer)]
User:   "parallel"
Agent:  🔵 Running... (proceeds with collected answers as goal context)
```

### Quick-Reply Options

The `clarify_needed` event includes optional `options[]` for one-click answers:
```json
{
  "type": "clarify_needed",
  "goal_id": "...",
  "question": "Which environment?",
  "options": ["staging", "production"],
  "required": true,
  "timeout_seconds": 600
}
```
The frontend renders these as **pill chips** below the question bubble. Clicking a chip sends the answer as a chat message. Free-form text is also accepted.
```

### Integration with Existing Agent Engine

**Zero changes** to GoalService, AgentLoop, Celery, LangGraph.

The GOAL path:
1. `ChatService` calls `GoalService.submit_goal(text, tenant_id, context=chat_history_summary, agent_id=inferred)`
2. Celery picks it up → LangGraph loop runs (unchanged)
3. GoalService publishes SSE events via Redis pub/sub
4. `ChatService.stream()` subscribes to those Redis events → forwards to chat SSE stream

The Q&A path:
1. `SemanticCache.lookup(message)` → cache hit returns immediately
2. Optional RAG: retrieve knowledge chunks for grounded answers
3. LLM receives: `[system_prompt] + [rag_context] + [last_20_turns] + [user_message]`
4. Token-by-token streaming via existing provider abstraction

### Conversation Context

```python
# Per message, build context from last 20 turns
history = await db.query("""
    SELECT role, content FROM chat_messages
    WHERE session_id = $1 ORDER BY created_at DESC LIMIT 20
""", session_id)

# For GOAL path, summarize into compact context string
context = summarize_history(history, max_tokens=500)
goal_id = await goal_service.submit_goal(text, context=context, ...)

# For QA path, pass full turns to LLM
messages = [*reversed(history), {"role": "user", "content": new_message}]
```

---

## Frontend Design (World-Class UI)

### Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  ☰  AgentVerse                                      [+ New Chat] │
├──────────────────────┬──────────────────────────────────────────┤
│  SIDEBAR             │  CHAT THREAD                             │
│  ──────              │  ──────────                              │
│  🔍 Search           │                                          │
│                      │  [User bubble: right-aligned, blue]      │
│  📌 Deploy prod      │  "What is the agent loop?"               │
│  📌 Invoice proc.    │                                          │
│  ──────              │  [Agent bubble: left-aligned, dark]      │
│  Today               │  The agent loop follows: initialize →    │
│    Q3 analysis       │  plan → execute → verify → complete...   │
│    Test failures     │  [Copy] [👍] [👎]                        │
│  Yesterday           │                                          │
│    Deploy staging    │  [User: "Deploy the app to production"]  │
│    KYC workflow      │                                          │
│  This week           │  🔵 Routing to agent...                  │
│    ...               │  ⚙️ Step 1: Running tests    2.3s  ✅    │
│                      │    → 47 tests passed                     │
│  ──────              │  ⚙️ Step 2: Building image   ⏳...       │
│  [Agent: Default ▼]  │                               [■ Stop]   │
│                      │                                          │
├──────────────────────┼──────────────────────────────────────────┤
│                      │  [📎] [  Type a message or goal...  ↵ ] │
└──────────────────────┴──────────────────────────────────────────┘
```

### Frontend File Structure

```
agent-verse-frontend/src/features/chat/
  ChatPage.tsx            # Full-page layout: sidebar + thread + input
  ChatSidebar.tsx         # Session list: search, pinning, TTL badges, groups
  ChatThread.tsx          # Message history + live streaming area
  ChatInput.tsx           # Textarea + file drop + agent selector + send
  ChatMessage.tsx         # Renders all message types (user/assistant/step/goal)
  ChatStepCard.tsx        # Single agent step with status icon + expandable detail
  ChatGoalSummary.tsx     # Goal complete card with timing + suggested actions
  ChatClarifyCard.tsx     # Clarifying question card: question text + quick-reply chips
  ChatEmptyState.tsx      # New chat with suggested prompts
  hooks/
    useChatStream.ts      # SSE hook: opens stream, appends events, handles reconnect
    useChatSession.ts     # TanStack Query: session CRUD, message send
    useChatHistory.ts     # Load + paginate message history
  types/
    chat.types.ts         # TypeScript types for sessions, messages, SSE events
```

### Routing

```tsx
<Route path="/chat"            element={<ChatPage />} />
<Route path="/chat/:sessionId" element={<ChatPage />} />
```

Add "Chat" to `AppLayout.tsx` sidebar nav — icon: `MessageSquare` from lucide-react.

### API Client Additions

```typescript
// src/lib/api/chat.ts
export const chatApi = {
  createSession:  ()                          => api.post('/v1/chat/sessions'),
  listSessions:   (page = 1)                  => api.get('/v1/chat/sessions', { params: { page } }),
  getSession:     (id: string)                => api.get(`/v1/chat/sessions/${id}`),
  deleteSession:  (id: string)                => api.delete(`/v1/chat/sessions/${id}`),
  renameSession:  (id: string, title: string) => api.patch(`/v1/chat/sessions/${id}`, { title }),
  pinSession:     (id: string, pinned: bool)  => api.patch(`/v1/chat/sessions/${id}`, { pinned }),
  sendMessage:    (id: string, content: string, files?: File[]) =>
                    api.post(`/v1/chat/sessions/${id}/messages`, { content }),
  streamUrl:      (id: string) => `/v1/chat/sessions/${id}/stream`,
};
```

---

## Frontend Features — Complete List

### Session Sidebar
- [x] Session list grouped by: Pinned, Today, Yesterday, This Week, Older
- [x] Session title auto-generated from first message (truncated to 60 chars)
- [x] **Pin/unpin** via star icon — pinned sessions never expire
- [x] **7-day TTL badge** — sessions expiring within 3 days show "Xd" badge in amber
- [x] **Search** — fuzzy-filter sessions by title in sidebar
- [x] **New Chat** button (top-right) + keyboard shortcut `Ctrl+K` / `Cmd+K`
- [x] **Delete session** via context menu (with confirmation)
- [x] **Rename session** via double-click or context menu
- [x] **Agent selector** dropdown at sidebar bottom — choose which agent handles goals
  - Options: Default (auto-route), Customer Support Agent, Code Review Agent, etc.
  - Persisted per session
- [x] Unread indicator dot for sessions with in-progress goals

### Chat Thread
- [x] Scrollable message history with auto-scroll to bottom on new messages
- [x] **Infinite scroll** upward to load older messages (50 per page)
- [x] Date separators between messages from different days
- [x] **Empty state** with 4 suggested starter prompts:
  - "What can AgentVerse do?"
  - "Run all tests and fix failures"
  - "Analyze Q3 revenue data"
  - "Deploy to staging"

### User Messages
- [x] Right-aligned blue bubble
- [x] Renders markdown in messages (bold, code spans, lists)
- [x] Shows attached file name if file was uploaded
- [x] Timestamp on hover

### Assistant Messages (Q&A)
- [x] Left-aligned, dark background bubble
- [x] **Token-by-token streaming** — characters appear as generated
- [x] Animated cursor while streaming
- [x] Full **Markdown rendering**: headings, bold, italic, lists, tables, blockquotes
- [x] **Syntax-highlighted code blocks** with language label + copy button
- [x] **Copy message** button (appears on hover)
- [x] **Thumbs up / thumbs down** feedback buttons
- [x] Timestamp

### Goal Execution — Step Cards (inline in thread)
- [x] **Routing indicator**: "🔵 Routing to agent..." appears immediately
- [x] Each step renders as a card inline:
  ```
  ⚙️ Step 1: Running test suite             [2.3s]  ✅
    → 47 passed, 3 failed in tests/api/
  ```
- [x] **Step states**: pending (⏳), running (spinner), complete (✅), failed (❌), skipped (⊘)
- [x] **Expandable step detail**: click to see tool inputs/outputs
- [x] **Tool call preview**: shows tool name + truncated args inline
- [x] **Stop/Cancel button** `[■ Stop]` visible while goal is running (sends cancellation)
- [x] Goal complete summary card:
  ```
  ✅ Goal completed in 4.3s
     "All 50 tests now passing. 3 fixes applied."
     [View full trace] [Copy summary] [Run again]
  ```
- [x] Goal failed card with error message + `[Retry]` button

### HITL (Human-In-The-Loop) Gate
- [x] When agent hits high-risk step (deploy, delete, prod):
  ```
  ⚠️ Human approval required
  "Deploy v2.4.1 to production (acme-prod cluster)?"
  [✓ Approve]  [✗ Reject]  [💬 Give feedback]
  ```
- [x] Chat is blocked until user responds
- [x] 10-minute timeout with auto-reject

### Clarifying Questions
- [x] **Pre-execution**: when goal is underspecified, a question bubble appears BEFORE agent starts
  ```
  ❓ Which environment?
  [staging]  [production]
  ```
- [x] **Mid-execution**: `clarify_needed` event renders a pause card inline within the step stream
  ```
  ⚙️ Step 2: Upload artifact    ⏸ Waiting for input
    ❓ "Which S3 bucket?"
       [app-assets-staging]  [app-assets-prod]  [type bucket name]
  ```
- [x] **Quick-reply chips** rendered as pill buttons below the question — one click sends the answer
- [x] Free-form text still accepted alongside chips
- [x] Goal shows `⏸ Paused` status badge while waiting
- [x] 10-minute countdown timer shown on the question card; auto-cancels on timeout
- [x] User's answer appended in chat as a normal user message (full history visibility)
- [x] After answer: agent resumes from the paused step (does not restart from scratch)
- [x] **Max 3 sequential clarifying questions** per goal — 4th question triggers execution with defaults

### Chat Input
- [x] Auto-growing textarea (1→8 lines)
- [x] **Enter** to send, **Shift+Enter** for newline
- [x] **File attachment** via paperclip icon or drag-and-drop into input area
  - Accepted: PDF, images, CSV, JSON, TXT
  - Shows attachment chip with filename + × to remove
  - Triggers OCR tool automatically for documents
- [x] **@mention agent** — type `@` to surface agent list autocomplete
- [x] **`/` slash commands** — type `/` for quick commands:
  - `/new` — new session
  - `/clear` — clear current thread
  - `/pin` — pin current session
  - `/agent CustomerSupport` — switch agent
- [x] Disabled while goal is executing (shows "Agent is working..." placeholder)
- [x] Character count shown when > 2000 chars

### Markdown + Code Rendering
- [x] Full GFM (GitHub Flavored Markdown) rendering via `react-markdown` + `rehype-highlight`
- [x] Code blocks: language detection, syntax highlight, copy button
- [x] Tables rendered with proper borders
- [x] Links open in new tab with `noopener noreferrer`
- [x] Math equations via KaTeX (if enabled)

### Session History & Context
- [x] All messages persisted in `chat_messages` table
- [x] **Last 20 turns** sent as context to every LLM/goal request
- [x] Conversation context flows into goal planner
- [x] Goals executed with prior chat context → agent knows what was discussed
- [x] Cross-session: pinned sessions retrievable from sidebar forever
- [x] Session TTL: auto-deleted after 7 days (configurable), preserved if pinned

### Suggested Follow-ups
- [x] After goal completes, show 2-3 contextual follow-up chips:
  - "View execution trace"
  - "Run again"  
  - "Show what changed"
  - "Create a PR"
- [x] After Q&A, optionally show related questions

### Accessibility
- [x] Full keyboard navigation: Tab through sidebar sessions, Enter to open
- [x] ARIA labels on all interactive elements
- [x] Screen reader announcements for new messages and step completions
- [x] Focus management: focus moves to new message on arrival
- [x] Skip-to-content link

### Responsive Design
- [x] Desktop: sidebar (280px) + thread (flex-1)
- [x] Tablet: sidebar collapses to icon rail, swipe to expand
- [x] Mobile: single-column; sidebar opens as drawer from hamburger menu
- [x] Touch-friendly: 44×44px tap targets

### Dark/Light Mode
- [x] Follows system preference (`prefers-color-scheme`)
- [x] Manual toggle in settings
- [x] Agent step cards use distinct colors per state in both modes

### Keyboard Shortcuts
| Shortcut | Action |
|---|---|
| `Ctrl/Cmd + K` | New chat |
| `Ctrl/Cmd + /` | Focus input |
| `Ctrl/Cmd + P` | Search sessions (same as sidebar search) |
| `Escape` | Close sidebar (mobile) / Cancel goal confirmation |
| `Enter` | Send message |
| `Shift + Enter` | Newline in message |

### Performance
- [x] Session list virtualized (react-virtual) for 100+ sessions
- [x] Message thread virtualized for 500+ messages
- [x] SSE reconnect with exponential backoff (1s, 2s, 4s, max 30s)
- [x] Optimistic message insertion (user message appears before server confirms)
- [x] Debounced session search (300ms)

---

## How It Connects to Existing AgentVerse Systems

| System | Used By Chat? | How |
|---|---|---|
| **GoalService** | ✅ GOAL path | `submit_goal()` called directly, unchanged |
| **LangGraph loop** | ✅ GOAL path | Executes as normal, chat adds no overhead |
| **All 40+ MCP tools** | ✅ GOAL path | Available to executing agent |
| **RAG / Hybrid Search** | ✅ Both paths | Q&A grounded in knowledge; planner uses RAG |
| **Embeddings** | ✅ Both paths | SemanticCache + knowledge retrieval |
| **Agent Memory** | ✅ GOAL path | Working, Episodic, Long-term all active |
| **Agent Patterns** | ✅ GOAL path | Plan-Execute, ReAct, Reflexion unchanged |
| **Cost Control** | ✅ Both paths | Budget enforcement, per-goal cost tracking |
| **Guardrails** | ✅ Both paths | Injection detection, content safety |
| **HITL Gate** | ✅ GOAL path | High-risk steps gate in chat thread |
| **Model Router** | ✅ Both paths | Best LLM per role (planner/executor/verifier) |
| **Tenant RLS** | ✅ Both paths | chat_sessions + chat_messages scoped by tenant |
| **OCR Engine** | ✅ Via file upload | Attach doc → `extract_document` tool called |

---

## Custom System Prompt per Session

Users can set a session-level persona that is prepended to every LLM call in that session:

```
PATCH /v1/chat/sessions/{id}
{ "system_prompt": "You are a Python security expert. Always mention potential security risks." }
```

**Data model:** `chat_sessions.system_prompt TEXT` (max 500 chars, nullable)

**UI:**
- `⚙️ Session settings` button in thread header opens a small modal
- Textarea: "Custom instructions for this session"
- Shows a `🔧 Custom` badge on the session item in sidebar
- Examples in placeholder: "Act as a data analyst", "Always use TypeScript", "Respond in Spanish"

**Backend injection:**
```python
# app/chat/context.py — prepended before all messages
if session.system_prompt:
    messages = [{"role": "system", "content": session.system_prompt}, *messages]
```

Applies to both Q&A streaming AND goal planner context.

---

## Goal Scheduling from Chat

Users can schedule recurring goals directly in chat:

```
User:   "Run the test suite every weekday at 9am"
Agent:  ✅ Scheduled: "Run test suite" → weekdays at 09:00
        Next run: Mon 2026-08-18 09:00
        [View schedule]  [Cancel]
```

**Flow:**
1. `IntentRouter` detects scheduling language → `intent = "schedule"`
2. `NLScheduler.parse(message)` → `TriggerSpec` (existing system, no changes)
3. `ScheduleStore.create(trigger_spec, tenant_id)` persists the schedule
4. On trigger fire: Celery submits goal → `GoalService` with `source="scheduled_chat"`
5. Chat SSE receives `schedule_created` event

**New SSE event:**
```json
{"type":"schedule_created","schedule_id":"...","cron":"0 9 * * 1-5",
 "next_run":"2026-08-18T09:00:00Z","description":"Run test suite"}
```

**New API endpoints:**
```
POST   /v1/chat/sessions/{id}/schedules         Create schedule from NL
GET    /v1/chat/sessions/{id}/schedules         List schedules for session
DELETE /v1/chat/sessions/{id}/schedules/{sid}   Cancel schedule
```

**UI additions:**
- `/schedule list` slash command shows all active schedules
- 🕐 badge on session sidebar item when active schedules exist
- Cancel button on `schedule_created` card

---

## Rich Output Rendering

When goals produce structured data, the chat thread renders it as an interactive visual component rather than plain text.

### Mechanism

The `goal_complete` SSE event carries an `output_type` hint:
```json
{
  "type": "goal_complete",
  "goal_id": "...",
  "summary": "Q3 revenue analysis complete",
  "output_type": "table",
  "output_url": "https://storage/.../output.csv",
  "latency_ms": 3200
}
```

### Supported Output Types

**`table`** — Renders a sortable/filterable inline table:
```
┌──────────┬──────────┬──────────┐
│ Region   │ Revenue  │ Growth   │
├──────────┼──────────┼──────────┤
│ APAC     │ $2.4M    │ +18%     │
│ EMEA     │ $1.8M    │ +12%     │
└──────────┴──────────┴──────────┘
[Download CSV]  [Copy as markdown]
```

**`chart`** — Renders a Vega-Lite chart (bar, line, pie, scatter):
- Hover tooltips, dark-mode compatible
- `[Download PNG]  [Copy spec]` actions

**`diff`** — GitHub-style unified diff with red/green highlighting:
- File-level collapse
- `[Copy patch]` action

### Frontend Components

```
ChatRichOutput.tsx    # Dispatcher routes to correct renderer
ChatDataTable.tsx     # react-table, sortable, filterable
ChatChart.tsx         # react-vega (Vega-Lite)
ChatDiff.tsx          # react-diff-view
```

---

## `#file` Context References

Users inject workspace file contents into messages by typing `#`:

```
User: "Review for security issues: #file:app/auth/middleware.py"
Agent: (receives full file content, performs review)
```

**Syntax:**
- `#file:<relative-path>` — inject file content (max 50KB per file, 3 files max)
- `#selection` — inject current editor selection (VS Code extension)

**Flow:**
1. User types `#` → autocomplete popover shows recent/searched files
2. Selected file renders as attachment chip: `📄 middleware.py ×`
3. On send: frontend fetches content via `GET /v1/chat/files?path=<path>`
4. File content prepended to message as system block (not stored in `chat_messages`)
5. `metadata.file_context = [{path, content_hash, token_count}]` stored for audit

**New API endpoint:**
```
GET /v1/chat/files?path=<relative-path>
    → {content: "...", tokens: 847, language: "python"}
```

---

## Message Editing & Branching

Users can edit any previously sent message. All messages after the edited message are pruned and regeneration begins from the edit point — like ChatGPT and Claude.

**UI:**
- Hover any user message → pencil icon appears
- Click pencil → message becomes an inline editable textarea
- `[Save & Regenerate]` button replaces `[Cancel]`
- On save: messages after the edited message are soft-deleted (moved to `metadata.branch_pruned = true`)
- Agent regenerates from the new message content
- A small "↺ Edited" label appears below the updated user message

**Data model addition:**
```sql
-- chat_messages gets a branch_id column
ALTER TABLE chat_messages ADD COLUMN branch_id UUID DEFAULT NULL;
-- Pruned messages kept for audit; excluded from active context by WHERE branch_id IS NULL
```

**New API endpoint:**
```
PATCH /v1/chat/sessions/{id}/messages/{msg_id}
{ "content": "updated message text" }
→ 202: prunes subsequent messages, starts new SSE stream
```

**Keyboard shortcut:** `↑` in empty input focuses the last user message for editing.

---

## Artifacts / Canvas Panel

Long-form content (code files, documents, reports) opens in a **resizable side panel** alongside the chat thread — like Claude Artifacts and ChatGPT Canvas. The agent and user can collaboratively edit the artifact in real time.

**Triggers:** Agent auto-detects artifact-worthy content:
- Code blocks > 50 lines
- Complete file content (when `output_type = "artifact"`)
- Documents / reports > 500 words

**Layout (desktop only):**
```
┌──────────────────┬───────────────────────────────┐
│ CHAT THREAD      │ ARTIFACT PANEL                │
│                  │ ─────────────────────────────  │
│ Agent: Here's    │ [📄 main.py]  [python] [Copy] │
│ the full         │                               │
│ implementation:  │ def deploy():                 │
│ [Open in panel→] │     ...                       │
│                  │                               │
│                  │ ✏️ User can edit directly     │
│                  │ Agent sees edits in next msg  │
└──────────────────┴───────────────────────────────┘
```

**Artifact actions:** `[Copy]  [Download]  [Run]  [Share]  [Close panel]`

**Agent awareness:** When the panel is open, subsequent messages automatically include the current artifact content as context — the agent can diff, extend, or rewrite it.

**New SSE event:**
```json
{"type":"artifact_created","artifact_id":"...","title":"main.py","language":"python",
 "content":"...full content...","size_bytes":3200}
```

**Frontend component:** `ChatArtifactPanel.tsx` — Monaco editor (same as VS Code), syntax highlighting, diff view mode.

---

## Cross-Session Search

Full-text search across message content in ALL sessions for the tenant:

```
GET /v1/chat/search?q=deploy+production&limit=20
→ [{session_id, session_title, message_id, role, snippet, created_at}, ...]
```

**UI:**
- `Ctrl/Cmd+P` opens a command palette with "Search all chats" at the top
- Alternatively, the sidebar search expands to include "Search all sessions" toggle
- Results show: session title, message excerpt with highlighted match, relative time
- Click result → opens that session scrolled to the matching message

**Backend:** Uses the existing `idx_chat_messages_fts` GIN index on `to_tsvector('english', content)`:
```sql
SELECT m.id, m.session_id, s.title, m.role,
       ts_headline('english', m.content, query) AS snippet
FROM chat_messages m
JOIN chat_sessions s ON s.id = m.session_id,
     to_tsquery('english', $1) query
WHERE to_tsvector('english', m.content) @@ query
  AND m.tenant_id = current_setting('app.tenant_id')::uuid
ORDER BY m.created_at DESC LIMIT $2;
```

---

## Inline Code Execution (Q&A)

For Q&A responses containing Python or JavaScript code blocks, a `[▶ Run]` button appears. Clicking it executes the code in an isolated sandbox and streams the output back inline.

```
Agent: Here's how to parse the CSV:

```python
import csv
with open('data.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        print(row)
```
[▶ Run]  [Copy]

→ (on Run):
⚙️ Executing...
Output:
{'name': 'Alice', 'age': '30'}
{'name': 'Bob', 'age': '25'}
```

**Backend:** Uses the existing `app/execution_environment/` sandbox (Docker/gVisor). A lightweight wrapper exposes it:
```
POST /v1/chat/sessions/{id}/execute
{ "code": "import csv...", "language": "python" }
→ SSE stream of stdout/stderr lines
```

**Languages supported:** Python 3.12, JavaScript (Node 22), Bash
**Limits:** 30-second timeout, 256MB memory, no network access, no filesystem writes

---

## Agent Capability Disclosure

Before the first message in a new session, an agent capability card shows what the selected agent can do:

```
┌──────────────────────────────────────────────────┐
│ 🤖 Default Agent                                 │
│ ────────────────────────────────────────────────  │
│ Can: 🔍 Search web  💻 Run code  📂 Read files   │
│      🗄️ Query DB   📦 Deploy    🔧 Fix tests     │
│      📊 Analyze data  📝 Write docs              │
│                                                  │
│ 40+ tools via MCP · RAG over your knowledge base │
│ [Change agent ▼]                                 │
└──────────────────────────────────────────────────┘
```

**Data source:** Each agent in `AgentStore` has a `capabilities: list[str]` field (auto-derived from connected MCP tool descriptions). The chat service fetches this on session creation.

**UI:** Capability card appears in `ChatEmptyState.tsx`. Dismissed after first message is sent. Accessible via `⚙️ Agent info` button in thread header.

---

## Goal Failure Analysis

When a goal fails, instead of just showing `❌ Goal failed — [Retry]`, the agent automatically runs a lightweight failure analysis and suggests alternatives:

```
❌ Goal failed: "Deploy to production"
Error: Tests failing — 3 tests in auth/test_middleware.py

🔍 Analysis:
The test failures are in authentication middleware, likely caused by
a missing environment variable PROD_SECRET_KEY.

💡 Suggested approaches:
[1] Fix the failing tests first →  "Run tests and fix auth failures"
[2] Check environment variables →  "List production env vars"
[3] Deploy to staging instead →   "Deploy to staging"

[↺ Retry original]  [Try suggestion 1]  [Try suggestion 2]  [Try suggestion 3]
```

**How it works:**
1. `GoalService` publishes `goal_failed` with `error_details` (error message, failed step, tool outputs)
2. `ChatService` detects failure → calls a lightweight LLM with the error context (~200 tokens)
3. LLM returns `{analysis: str, suggestions: [{label: str, goal_text: str}]}`
4. `failure_analysis` SSE event carries the result
5. Frontend renders `ChatGoalFailureCard.tsx` with analysis + 3 clickable suggestion chips

**New SSE event:**
```json
{"type":"failure_analysis","goal_id":"...","analysis":"Tests failing due to...",
 "suggestions":[{"label":"Fix failing tests","goal_text":"Run tests and fix auth failures"}]}
```

---

## Token & Cost Visibility

Users see token usage and estimated cost for each session and message:

**Per-message:** On hover, a tooltip shows `~847 tokens · ~$0.003`

**Per-session header:**
```
Session: Deploy to staging    Tokens: 12,450   Cost: ~$0.04   [ℹ️]
```

**Session settings modal (expanded):**
```
This session
  Total tokens:  12,450
  Input tokens:   9,200
  Output tokens:  3,250
  LLM calls:        14
  Goal executions:   2
  Est. cost:      $0.04

Tenant usage (today)
  Tokens: 847,320 / 2,000,000
  Cost:   $3.12 / $10.00 budget
  [Usage dashboard →]
```

**Backend:** `CostControl` already tracks per-goal cost in Redis. Chat adds per-session aggregation:
```sql
-- chat_session_usage view
SELECT session_id, SUM(tokens_in) AS input_tokens, SUM(tokens_out) AS output_tokens,
       SUM(cost_usd) AS total_cost_usd
FROM chat_message_usage GROUP BY session_id;
```

Message metadata stores `{tokens_in, tokens_out, cost_usd, model}` on every LLM call.

---

## Proactive Suggestions

After completing a goal, the agent proactively surfaces related issues or next steps it noticed without being asked — like Devin's proactive awareness:

```
✅ Goal completed: "Deploy to staging" (4.3s)

💡 I noticed while running:
• Staging server memory usage is at 89% — consider scaling up
• 3 deprecation warnings in app/auth/ that may break in Python 3.13
• The test coverage for payments/ dropped to 62% after your recent changes

Want me to investigate any of these?
[Investigate memory]  [Fix deprecations]  [Add payment tests]
```

**How it works:**
1. After `goal_complete`, LangGraph executor collects `observations[]` — anomalies noted in tool outputs during execution
2. These are passed to a lightweight proactive-suggestion LLM call (~100ms)
3. Returns 0-3 suggestions (0 if nothing interesting found)
4. `proactive_suggestions` SSE event carries them

**New SSE event:**
```json
{"type":"proactive_suggestions","goal_id":"...",
 "observations":["Memory at 89%","Deprecation warnings in auth/"],
 "suggestions":[{"text":"Investigate high memory","goal_text":"Check staging server memory and suggest fixes"}]}
```

The feature is **opt-in** with a toggle in session settings: "Proactive insights after goals" (default: on).

---

## Model Selector per Message

Users can select which LLM model to use for a specific response — or regenerate with a different model to compare:

**Input area:** Model selector dropdown next to send button (collapsed to icon by default):
```
[📎] [  Type a message...  ] [claude-3.5-sonnet ▼] [↵]
```

**After response:** `[↺ Regenerate ▼]` expands to:
```
Regenerate with:
● Claude 3.5 Sonnet (current)
○ GPT-4o
○ Gemini 1.5 Pro
○ Llama 3.1 405B
```

Selecting a different model regenerates just that response using the same context.

**Data model:** `chat_messages.metadata.model_id` stores which model produced the response.

**Backend:** Routes through existing `ModelRouter` — just overrides the model selection for that specific call. Only applies to Q&A path; GOAL path always uses the role-specific model assignment.

**Constraint:** Model list is filtered to models the tenant has configured credentials for (from `LLMConfigStore`).

---

## Prompt Library & Persona Presets

A curated library of prompt templates and agent personas that users load with one click:

**Access:** `[⚡ Quick start]` button in empty state or `[+ From template]` in new chat.

**Built-in personas:**
| Persona | System Prompt | Default Agent |
|---|---|---|
| 🛡️ Security Auditor | "You are a security expert. Always surface OWASP risks..." | Default |
| 📊 Data Analyst | "You are a data analyst. Always show SQL and charts..." | Default |
| 🚀 DevOps Engineer | "You are a DevOps expert. Prefer infrastructure-as-code..." | Default |
| 🧪 QA Engineer | "Always write tests before fixing bugs..." | Default |
| 🏗️ Architect | "Think in systems. Consider scalability and failure modes..." | Default |

**User-saved templates:**
- `[Save as template]` on any session with a system prompt → names and saves it to the library
- Templates visible in the Quick start panel
- `GET /v1/chat/templates` — list tenant templates + built-in presets
- `POST /v1/chat/templates` — save a new template `{name, system_prompt, agent_id}`

Loading a template applies its `system_prompt` and `agent_id` to the current session.

---

## Parameterized Goal Re-run

The `[Run again]` button on a goal complete card expands to allow parameter overrides before re-running:

```
✅ Goal completed: "Deploy to staging"

[↺ Run again ▼]
  ├─ Run again as-is
  ├─ Run with overrides...
  └─ Schedule this goal →
```

"Run with overrides..." opens a mini-form pre-populated with detected parameters from the original goal:

```
Goal: "Deploy the app to staging"

Override parameters:
  Environment: [staging ▼] → production
  Branch: [main] → feature/auth-fix
  Run tests: [✓ Yes]

[▶ Run with overrides]
```

**How parameters are detected:** After goal completion, a lightweight LLM call extracts `{param_name: value}` pairs from the goal text (e.g. `environment=staging`, `branch=main`). These are stored in `metadata.extracted_params` on the `goal_complete` message.

---

## Workspace Codebase RAG

The agent automatically has access to the full indexed codebase — not just files explicitly shared via `#file`. Like Cursor AI, typing about any module or function triggers automatic retrieval:

**How it works:**
1. On tenant onboarding, a background Celery task indexes the connected code repository via the `KnowledgeStore` (existing hybrid pgvector + trigram search)
2. When chat context is built for Q&A or GOAL, `WorkspaceRAG.retrieve(message, top_k=5)` automatically injects the top relevant code snippets
3. Injected context includes: file path, lines, language, last modified

**User experience:**
```
User: "Why is the auth middleware failing in tests?"
Agent: (automatically retrieves app/auth/middleware.py + tests/auth/test_middleware.py)
       "Looking at your middleware code on lines 47-63, I can see the issue is..."
```

No `#file` needed — the agent finds relevant code on its own.

**Settings:** Workspace RAG toggle in session settings (default: on). The `[🔍 Sources]` button below any Q&A response shows which code snippets were retrieved and cited.

**Indexing status indicator:** First-time setup shows `⏳ Indexing your codebase... (2,340 files)` in the sidebar.

---

## Conversation Summary on Demand

A `/summarize` slash command or `[📋 Summary]` button generates a TL;DR of the current session:

```
User: /summarize

Agent: 📋 Session summary
       ────────────────────
       Topic: Deploy auth service v2.4.1

       What we did:
       1. Fixed 3 failing auth tests (session token expiry issue)
       2. Deployed to staging — all tests passing
       3. Investigated high memory usage — root cause: connection pool leak in auth/db.py

       Outstanding:
       • Production deployment pending HITL approval
       • Connection pool fix needs code review

       Key files changed: app/auth/middleware.py, tests/auth/test_middleware.py
       Duration: 47 minutes · 23 messages · 2 goals executed
```

**Implementation:** Sends the last 50 messages to a summarization LLM call (fast, cheap model like GPT-4o-mini). Result rendered as a pinned card at top of thread with `[Close]` to dismiss.

Also used automatically in the session export feature to generate the report header.

---

## Agent Memory Management UI

Users must be able to **see, edit, and delete** what the agent remembers about them. This is critical for trust, privacy, and GDPR compliance.

**Access:** Settings → "My AI Memory" or from session sidebar footer "🧠 Agent remembers X things about you"

**Memory management page:**
```
🧠 What the agent knows about you
────────────────────────────────
📌 Permanent memories
  • "You prefer Python 3.12 with type annotations"    [Edit] [Delete]
  • "You always deploy to staging before production"  [Edit] [Delete]
  • "You work on the payments service in the backend" [Edit] [Delete]

📅 Recent learnings (last 30 days)
  • "You like using pytest for testing"               [Edit] [Delete]
  • "You prefer Tailwind over CSS modules"            [Edit] [Delete]

[+ Add memory manually]  [Delete all memories]
```

**Backend:**
- `GET /v1/memories` — list all memories for tenant (from `LongTermMemoryStore`)
- `DELETE /v1/memories/{id}` — delete single memory
- `PATCH /v1/memories/{id}` — edit memory text
- `POST /v1/memories` — add manual memory
- `DELETE /v1/memories` — delete all memories (with confirmation)

**Data model addition:** `long_term_memories` table already exists in `LongTermMemoryStore`. This adds a management API layer on top.

**Privacy compliance:** All memory deleted when user requests account deletion (GDPR right to erasure). Audit log entry created on each delete. Memory cannot contain PII unless user explicitly adds it via "Add memory manually".

---

## Typing / Thinking Indicator

A `...` animated bubble appears immediately when a message is sent — before the first SSE token arrives — so the user knows the agent received and is processing their message.

**Q&A path:** Three animated dots in an assistant message bubble:
```
[User]: What is the agent loop?

[Agent]: • • •   ← animated, appears within 100ms of send
         (then replaced by streaming tokens)
```

**Goal path:** A routing indicator with spinner:
```
[User]: Deploy to staging

🔵 Routing...    ← appears within 100ms
(then replaced by "🔵 Routing to agent..." → step cards)
```

**Implementation:**
```typescript
// src/features/chat/hooks/useChatStream.ts
// On POST /messages success (202), immediately set typing state:
dispatch({ type: 'TYPING_STARTED', messageId: res.data.message_id });

// On first SSE 'routing' or 'token' event, clear typing:
case 'routing':
case 'token':
  dispatch({ type: 'TYPING_ENDED', messageId: event.message_id });
```

```tsx
// src/features/chat/ChatMessage.tsx
function TypingIndicator() {
  return (
    <div className="typing-bubble" aria-label="Agent is thinking">
      <span /><span /><span />  {/* CSS-animated dots */}
    </div>
  );
}
```

The indicator has `aria-live="polite"` and `aria-label="Agent is thinking"` for accessibility.

---

## Image Output Rendering

When a goal uses an image-generation MCP tool (DALL-E, Stable Diffusion, etc.), the output renders inline as an image in the chat thread.

**New SSE `output_type` value:**
```json
{
  "type": "goal_complete",
  "goal_id": "...",
  "summary": "Generated logo design",
  "output_type": "image",
  "output_url": "https://storage/.../output.png",
  "metadata": {"width": 1024, "height": 1024, "prompt": "minimal logo..."}
}
```

**UI rendering:**
```
✅ Goal completed: "Generate a logo for AgentVerse"

[Image rendered inline — max width 512px, click to fullscreen]
[📥 Download]  [🔗 Copy URL]  [↺ Regenerate]
```

**Frontend component:** `ChatRichOutput.tsx` already has a dispatcher — add image case:
```tsx
case 'image':
  return <ChatImageOutput url={output.output_url} meta={output.metadata} />;
```

`ChatImageOutput.tsx` renders:
- Lazy-loaded `<img>` with `loading="lazy"` and `alt` from goal text
- Click to open fullscreen lightbox
- Download button
- "Regenerate" chip that re-runs the goal

---

## Session Folders & Tags

Power users managing 50+ sessions need organization beyond "pinned" vs. unpinned.

**UI:**
```
SIDEBAR
────────
📁 Project: Payments Service   (12)
  ├─ Deploy auth v2
  ├─ Fix test failures
  └─ Code review PR #47

📁 Project: DevOps             (8)
  ├─ Set up staging
  └─ K8s memory tuning

📌 Pinned (no folder)
  ├─ Architecture brainstorm
```

**Create folder:** Right-click session → "Move to folder" → type folder name or select existing

**Data model addition:**
```sql
CREATE TABLE chat_session_folders (
  id         UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  tenant_id  UUID NOT NULL,
  name       TEXT NOT NULL,
  color      TEXT DEFAULT '#6366f1',  -- accent color for folder icon
  position   INT  DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE chat_sessions ADD COLUMN folder_id UUID REFERENCES chat_session_folders(id);
```

**New API endpoints:**
```
GET    /v1/chat/folders             List folders for tenant
POST   /v1/chat/folders             Create folder
PATCH  /v1/chat/folders/{id}        Rename / recolor folder
DELETE /v1/chat/folders/{id}        Delete folder (sessions move to no-folder)
PATCH  /v1/chat/sessions/{id}       Already exists — add folder_id field
```

**Sidebar behavior:**
- Folders collapsed by default, click to expand
- Drag-and-drop sessions between folders
- Folder shows session count badge
- "Unfiled" section at bottom for sessions with no folder

---

## Connected Services Panel

Users need visibility into what external services (GitHub, Jira, Slack, databases) the agent can access in this session — and the ability to connect new services or revoke access.

**Access:** Thread header "🔌 3 services connected" button → panel opens

**Panel:**
```
Connected services
──────────────────
✅ GitHub             github.com/acme-corp    [Manage] [Disconnect]
   Repos: 12 visible · PRs, Issues, Code

✅ Postgres (staging)  staging-db.acme.com    [Manage] [Disconnect]
   Read-only · 8 tables indexed

✅ Stripe (test mode)  api.stripe.com         [Manage] [Disconnect]
   Read: Customers, Invoices

+ Connect a service
  [GitHub] [Jira] [Slack] [Notion] [Custom MCP...]
```

**How it works:**
- Each connected service is an MCP connector registered in `MCPRegistry` for this tenant
- The panel reads from `MCPRegistry.list_connectors(tenant_id)` — no new data — just exposes it in chat UI
- "Connect a service" → opens OAuth flow or MCP server URL input
- "Disconnect" → removes connector from MCPRegistry + revokes stored credentials from Vault

**New API endpoints (wraps existing MCP registry):**
```
GET    /v1/chat/services             List connected MCP services for tenant
POST   /v1/chat/services             Initiate new service connection (returns OAuth URL or setup wizard)
DELETE /v1/chat/services/{id}        Disconnect service
```

**Agent capability card integration:** Connected services also appear in the Agent Capability Disclosure card when starting a new session.

---

## Agent Reasoning Transparency

A "Show thinking" toggle reveals the agent's internal reasoning chain — what it considered before responding. Like Claude's extended thinking and OpenAI's chain-of-thought display.

**Default:** Off (collapsed). Toggle appears in session settings and on each message.

**When enabled**, a collapsible "Thinking..." section appears above the response:

```
▶ Thinking (click to expand)              [Toggle off]

  ┌──────────────────────────────────────────────────────┐
  │ The user wants to deploy to staging. Let me check:   │
  │ 1. Are there failing tests? → I should run them first│
  │ 2. Is there a migration needed? → Check alembic state│
  │ 3. Staging env available? → Check infra status       │
  │                                                       │
  │ Decision: Step 1 = run tests, Step 2 = check schema  │
  └──────────────────────────────────────────────────────┘

✅ Deployment complete
```

**Implementation:**
- LLM providers that support chain-of-thought / reasoning tokens (Claude extended thinking, o1/o3 reasoning) pass their reasoning output in a separate field
- `ModelRouter` captures `reasoning_content` from provider response
- `ChatService` stores it in `chat_messages.metadata.reasoning`
- New SSE event:
```json
{"type":"reasoning","content":"The user wants to deploy...","message_id":"..."}
```
- Frontend renders it collapsed by default
- "Thinking" section uses `<details>/<summary>` for native accessibility

**Toggle persistence:** Per-session setting stored in `chat_sessions.metadata.show_reasoning` (boolean, default false).

---

## Non-Goals (Out of Scope for v1)

- Voice input / speech-to-text
- Real-time multi-user collaboration on same chat (future)
- Export chat as PDF (future)

---

## Test Coverage Requirements

- `tests/chat/test_intent.py` — 18 tests (QA / GOAL / CLARIFY / SCHEDULE classification)
- `tests/chat/test_service.py` — 32 tests (session CRUD, dispatch, context, system prompt, file context, failure analysis, workspace RAG, memory management)
- `tests/chat/test_stream.py` — 17 tests (SSE multiplexing, tokens, goal events, artifact_created, failure_analysis, proactive_suggestions, reasoning event, typing indicator)
- `tests/chat/test_scheduling.py` — 10 tests (NL → cron parsing, schedule CRUD, trigger→goal wiring)
- `tests/chat/test_rich_output.py` — 10 tests (table/chart/diff/image output routing, output_url, lightbox metadata)
- `tests/chat/test_message_editing.py` — 8 tests (edit + branch pruning, regeneration from edit point)
- `tests/chat/test_execution.py` — 10 tests (inline code execution sandbox, timeout, output streaming)
- `tests/chat/test_search.py` — 8 tests (cross-session FTS, within-session search, snippet highlighting, pagination)
- `tests/chat/test_memory_api.py` — 8 tests (list, edit, delete, add, delete-all, GDPR audit log)
- `tests/chat/test_services_api.py` — 6 tests (list connected services, connect, disconnect, MCP registry sync)
- `tests/chat/test_folders.py` — 6 tests (folder CRUD, session assignment, delete cascade to sessions)
- `tests/api/test_chat_api.py` — 25 tests (all 16+ endpoints, auth, RLS, pagination, system_prompt, file context, templates, folders, services)
- Frontend: Vitest for `useChatStream`, `useChatSession`, `ChatArtifactPanel`, `ChatDataTable`, `ChatChart`, `ChatDiff`, `ChatImageOutput`, `ChatGoalFailureCard`, `TypingIndicator`, `AgentMemoryPage`, `ConnectedServicesPanel`, `ChatSessionFolders`
- E2E (Playwright): QA flow, GOAL flow, clarification, scheduling, rich output (table/chart/image), message editing, artifact panel, cross-session search, inline execution, memory management, session folders
