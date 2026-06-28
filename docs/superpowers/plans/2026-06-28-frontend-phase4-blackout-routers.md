# Frontend Phase 4 — Blackout Routers → First-Class Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every "blackout" backend router (memory, artifacts, tools, integrations, training-export, perception, a2a) a first-class, tested page reachable from the sidebar — surfacing list/recall/delete, file/code/email tooling, integration config + copyable endpoints, JSONL export, vision/screenshot, and read-only A2A observability. This is WS-3 of the master plan (Phase 4 of 8).

**Architecture:** Frontend-only feature work in `agent-verse-frontend`. We add new typed-client namespaces (`artifactsApi`, `toolsApi`, `integrationsApi`, `trainingApi`, `perceptionApi`, `a2aApi`) and extend `memoryApi` — every backend call goes through `@/lib/api/client` (no inline `fetch` in pages). Seven new feature pages are added under `src/features/<area>/`, each routed in `src/app/App.tsx` and linked from `src/components/ui/Sidebar.tsx`. Pages reuse the Phase 1 foundations: `toast`/`useToastStore`, the `request()` 401/error handling, and the `Skeleton`/`EmptyState`/`StatusBadge` primitives. Strict TDD with vitest + Testing Library; one commit per task.

**Tech Stack:** React 19, TypeScript (strict), Vite, TanStack Query 5, Zustand 5, Tailwind (design tokens), react-router-dom 7, vitest 3 + @testing-library/react + @testing-library/user-event, Playwright.

## Global Constraints

- **Frontend-only.** No backend files change in Phase 4. No backend endpoints are added. Every page is built against the **verified** endpoints below.
- **No new dependencies.** Reuse what is installed (`lucide-react` for icons, `clsx` already present).
- **Auth access is via `useAuthStore` (`@/stores/auth`)** — never read `localStorage`/`sessionStorage` for the API key directly in pages. The typed client injects `X-API-Key` itself via `getApiKey()`.
- **All backend calls go through the typed client** `@/lib/api/client` (add methods there; do not inline `fetch`).
- **Reuse Phase 1 foundations:** `toast`/`useToastStore` from `@/stores/toast`; `Skeleton` from `@/components/ui/Skeleton`, `EmptyState` from `@/components/ui/EmptyState`, `StatusBadge` from `@/components/ui/StatusBadge`. (Phase 1 created these as individual files; there is no `@/components/ui` barrel — import each by path.) The client's `request()` already emits error toasts and clears the session on 401, so pages do not re-implement that.
- **Tailwind design tokens only** for styling: `bg-card`, `border-border`, `text-foreground`, `text-muted-foreground`, `text-primary`, `bg-muted`, `text-destructive`, with `dark:` siblings where used nearby. Mirror `ObservabilityPage.tsx` layout idioms (`space-y-6`, `bg-card border border-border rounded-xl`).
- **Verified backend paths (ground truth — file:line):**
  - Memory: `GET /memory` (list; query `limit`, `memory_type`) `memory.py:33`; `GET /memory/recall?q=&limit=` returns `{query, results[]}` `memory.py:79`; `DELETE /memory/{memory_id}` returns `{deleted,status}` `memory.py:149`; `GET /memory/tool-reliability` returns `list[dict]` `memory.py:194`. **There is no memory create/store endpoint** — `memoryApi.store` posting to `/memory` (client.ts:452) is wrong (the only `POST`-less `/memory` verb is `DELETE` = clear-all) and is removed in Task 1.
  - Artifacts: `GET /artifacts?goal_id=&artifact_type=&limit=` `artifacts.py:18`; `GET /artifacts/{id}` `artifacts.py:64`; `DELETE /artifacts/{id}` (204) `artifacts.py:104`. **No content/byte-download endpoint exists** — preview/download use the row's `storage_uri` + `content_type` (Task 4 handles `storage_uri` that is a URL vs. an opaque ref).
  - Tools: `POST /tools/execute-code` → `ExecuteCodeResponse{stdout,stderr,exit_code,success,timed_out,execution_time_ms}` `tools.py:27` (max `timeout`=60); `GET /tools/files?directory=` `tools.py:63`; `GET /tools/files/{path}` → `{path,content,success}` `tools.py:75`; `POST /tools/files/{path}` (201) body `{content}` → `{path,bytes_written,success}` `tools.py:93`; `DELETE /tools/files/{path}` (204) `tools.py:111`; `POST /tools/email/send` body `{to,subject,body,from_addr?}` `tools.py:134`.
  - Integrations: all **inbound webhooks** (no config-persistence API). `POST /integrations/slack/commands|events|interactive`, `POST /integrations/zapier/trigger` (header `X-Zapier-Secret`), `GET /integrations/zapier/goals` `integrations.py:420`, `POST /integrations/events/alertmanager` `integrations.py:305`, `POST /integrations/events/datadog` `integrations.py:367`. The UI is **configuration display + copyable endpoint URLs + zapier delivery visibility** (the recent-goals poll), not CRUD.
  - Training export: `POST /intelligence/export-training-data?min_score=&format=(openai|anthropic)&limit=` returns a **StreamingResponse** (JSONL download, header `X-Training-Examples`) `training_export.py:23`.
  - Perception: `GET /perception/status` → `{playwright_available,vision_available,browser_actions[],image_formats[]}` `perception.py:38`; `POST /perception/screenshot` body `{url,full_page?}` → `{success,url,screenshot_b64,error}` `perception.py:57`; `POST /perception/analyze` body `{screenshot_b64?,url?,question?}` → `{analysis,question,screenshot_provided}` `perception.py:80`; `POST /perception/extract` body `{url,selector?}` → `{success,url,selector,text,char_count,error}` `perception.py:112`.
  - A2A: `GET /.well-known/agent.json` → agent card `a2a.py:149`; `GET /a2a/tasks/{task_id}` → task `a2a.py:260`. **There is NO list endpoint** (`POST /a2a/tasks` only creates). The A2A page is therefore: agent-card display + task-lookup-by-id + status card (Task 7 — no fake list call).
- **Quality gate per task:** `npm run typecheck` and `npm run lint` and `npm run test` must pass before commit.
- **Commit style:** conventional commits; end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## Interface ownership

Phase 4 **owns** `artifactsApi`, `toolsApi`, `integrationsApi`, `trainingApi`, `perceptionApi`, `a2aApi`, and the `memoryApi` additions. Phase 7 (detail pages) reuses these for memory/artifact detail views — **keep list responses detail-friendly** (return the full row, not just a label). Do not rename existing methods; only add.

---

## File Structure

**Modify:**
- `src/lib/api/client.ts` — extend `memoryApi` (`list`, `delete`, `toolReliability`; remove the broken `store`); add `artifactsApi`, `toolsApi`, `integrationsApi`, `trainingApi`, `perceptionApi`, `a2aApi` and their types.
- `src/app/App.tsx` — add 7 routes (`memory`, `artifacts`, `tools`, `integrations`, `training-export`, `perception`, `a2a`).
- `src/components/ui/Sidebar.tsx` — add nav items for the 7 pages (new "Tooling" + "Observability" groupings as noted in tasks).

**Create:**
- `src/lib/api/client.test.ts` — append client method tests (file created in Phase 1; append, do not overwrite).
- `src/features/memory/MemoryExplorerPage.tsx` + `.test.tsx`
- `src/features/artifacts/ArtifactsBrowserPage.tsx` + `.test.tsx`
- `src/features/tools/ToolsPage.tsx` + `.test.tsx`
- `src/features/integrations/IntegrationsPage.tsx` + `.test.tsx`
- `src/features/training/TrainingExportPage.tsx` + `.test.tsx`
- `src/features/perception/PerceptionPage.tsx` + `.test.tsx`
- `src/features/a2a/A2APage.tsx` + `.test.tsx`

**Dependency from other phases (consumed, not created here):**
- Phase 1 (landed): `@/stores/toast` (`toast`, `useToastStore`), `@/components/ui/Skeleton`, `@/components/ui/EmptyState`, `@/components/ui/StatusBadge`, and the `request()` 401/error layer in `client.ts`.
- Phase 3 (live updates, optional): a generic SSE hook under `src/lib/sse/`. Phase 4 pages are **request/response only** (no live streams), so there is no hard dependency — if a future live A2A/tool stream is wanted, consume Phase 3's hook rather than recreating it. **Do not create an SSE hook in this phase.**

---

## Test harness reference (existing pattern — reuse verbatim)

Component tests (see `src/features/goals/GoalsListPage.test.tsx`):

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

function renderWithProviders(ui: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 'test-tenant', plan: 'free', isAuthenticated: true });
});

afterEach(() => vi.restoreAllMocks());
```

Client unit tests mock `fetch` directly: `vi.spyOn(globalThis, 'fetch')`. Helper used throughout:

```ts
function mockOk(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  );
}
```

E2E (Playwright) reuse the existing `setupAuth(page)` init-script + `page.route(...)` mock pattern from `e2e/goals.spec.ts` (sets `av-auth` + `av_api_key`, mocks `**/tenants/me`). Phase 4 e2e is exercised in the Task 9 regression gate via `navigation.spec.ts` (the 7 new sidebar links must navigate).

---

### Task 1: Extend `memoryApi` (list / delete / toolReliability; remove broken store)

**Files:**
- Modify: `src/lib/api/client.ts` (`memoryApi`, ~line 449)
- Test: `src/lib/api/client.test.ts` (append)

**Interfaces:**
- Consumes: nothing.
- Produces:
  ```ts
  export interface MemoryEntry {
    id: string;
    content: string;
    memory_type: string;
    confidence: number;
    tags: string[];
    created_at: string;
  }
  export interface RecallResult { content: string; confidence: number; memory_type: string; source: string }
  export interface ToolReliabilityRow {
    tool_name: string;
    total_calls: number;
    failures: number;
    success_rate: number;
    [key: string]: unknown;
  }
  memoryApi.list(opts?: { limit?: number; memoryType?: string }): Promise<MemoryEntry[]>   // GET /memory
  memoryApi.recall(query: string, limit?: number): Promise<RecallResult[]>                  // GET /memory/recall → .results
  memoryApi.delete(id: string): Promise<{ deleted: string; status: string }>                // DELETE /memory/{id}
  memoryApi.toolReliability(): Promise<ToolReliabilityRow[]>                                  // GET /memory/tool-reliability
  ```
  (The existing `memoryApi.recall` returns `Memory[]` against the wrong shape — replace it with the `.results`-unwrapping version below. Remove `memoryApi.store` — there is no backend endpoint for it.)

- [ ] **Step 1: Write the failing test**

```ts
// append to src/lib/api/client.test.ts
import { memoryApi } from '@/lib/api/client';

test('memoryApi.list calls GET /memory with limit + memory_type', async () => {
  const f = mockOk([{ id: 'm1', content: 'c', memory_type: 'fact', confidence: 0.9, tags: [], created_at: '' }]);
  const rows = await memoryApi.list({ limit: 50, memoryType: 'fact' });
  const url = String(f.mock.calls[0][0]);
  expect(url).toContain('/memory?');
  expect(url).toContain('limit=50');
  expect(url).toContain('memory_type=fact');
  expect(rows[0].id).toBe('m1');
});

test('memoryApi.recall unwraps results[] from /memory/recall', async () => {
  const f = mockOk({ query: 'x', results: [{ content: 'r', confidence: 0.8, memory_type: 'fact', source: 'g1' }] });
  const res = await memoryApi.recall('x', 5);
  expect(String(f.mock.calls[0][0])).toContain('/memory/recall?q=x&limit=5');
  expect(res).toEqual([{ content: 'r', confidence: 0.8, memory_type: 'fact', source: 'g1' }]);
});

test('memoryApi.delete calls DELETE /memory/{id}', async () => {
  const f = mockOk({ deleted: 'm1', status: 'ok' });
  await memoryApi.delete('m1');
  expect(String(f.mock.calls[0][0])).toContain('/memory/m1');
  expect((f.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
});

test('memoryApi.toolReliability calls GET /memory/tool-reliability', async () => {
  const f = mockOk([{ tool_name: 'http', total_calls: 10, failures: 4, success_rate: 0.6 }]);
  const rows = await memoryApi.toolReliability();
  expect(String(f.mock.calls[0][0])).toContain('/memory/tool-reliability');
  expect(rows[0].tool_name).toBe('http');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/api/client.test.ts -t memoryApi`
Expected: FAIL — `memoryApi.list`/`delete`/`toolReliability` undefined; `recall` returns the wrong shape.

- [ ] **Step 3: Implement**

In `src/lib/api/client.ts`, replace the `Memory` interface + `memoryApi` block (lines ~442-454) with:

```ts
export interface MemoryEntry {
  id: string;
  content: string;
  memory_type: string;
  confidence: number;
  tags: string[];
  created_at: string;
}

export interface RecallResult {
  content: string;
  confidence: number;
  memory_type: string;
  source: string;
}

export interface ToolReliabilityRow {
  tool_name: string;
  total_calls: number;
  failures: number;
  success_rate: number;
  [key: string]: unknown;
}

export const memoryApi = {
  list: (opts: { limit?: number; memoryType?: string } = {}) => {
    const params = new URLSearchParams();
    params.set("limit", String(opts.limit ?? 50));
    if (opts.memoryType) params.set("memory_type", opts.memoryType);
    return request<MemoryEntry[]>(`/memory?${params.toString()}`);
  },
  recall: (query: string, limit = 10) =>
    request<{ query: string; results: RecallResult[] }>(
      `/memory/recall?q=${encodeURIComponent(query)}&limit=${limit}`
    ).then((d) => d.results ?? []),
  delete: (id: string) =>
    request<{ deleted: string; status: string }>(`/memory/${id}`, { method: "DELETE" }),
  toolReliability: () => request<ToolReliabilityRow[]>("/memory/tool-reliability"),
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/lib/api/client.test.ts -t memoryApi`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/api/client.ts src/lib/api/client.test.ts
git commit -m "feat(client): extend memoryApi with list/delete/toolReliability

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Add `artifactsApi`, `toolsApi`, `trainingApi`, `perceptionApi`, `a2aApi`, `integrationsApi` to the client

**Files:**
- Modify: `src/lib/api/client.ts` (append after `memoryApi`)
- Test: `src/lib/api/client.test.ts` (append)

**Interfaces (exact):**

```ts
// ── Artifacts ──
export interface Artifact {
  id: string;
  name: string;
  artifact_type: string;
  storage_uri: string;
  content_type: string;
  size_bytes: number;
  goal_id: string;
  created_at: string;
}
artifactsApi.list(opts?: { goalId?: string; artifactType?: string; limit?: number }): Promise<Artifact[]>  // GET /artifacts
artifactsApi.get(id: string): Promise<Artifact>                                                            // GET /artifacts/{id}
artifactsApi.delete(id: string): Promise<void>                                                             // DELETE /artifacts/{id} (204)

// ── Tools ──
export interface ExecuteCodeResult {
  stdout: string; stderr: string; exit_code: number;
  success: boolean; timed_out: boolean; execution_time_ms: number;
}
export interface WorkspaceFile { name: string; path: string; size_bytes?: number; is_dir?: boolean; [key: string]: unknown }
toolsApi.executeCode(code: string, language: 'python' | 'javascript' | 'bash', timeout?: number): Promise<ExecuteCodeResult>  // POST /tools/execute-code
toolsApi.listFiles(directory?: string): Promise<WorkspaceFile[]>                                            // GET /tools/files?directory=
toolsApi.readFile(path: string): Promise<{ path: string; content: string; success: boolean }>              // GET /tools/files/{path}
toolsApi.writeFile(path: string, content: string): Promise<{ path: string; bytes_written: number; success: boolean }>  // POST /tools/files/{path}
toolsApi.deleteFile(path: string): Promise<void>                                                           // DELETE /tools/files/{path} (204)
toolsApi.sendEmail(body: { to: string | string[]; subject: string; body: string; from_addr?: string }): Promise<{ [key: string]: unknown }>  // POST /tools/email/send

// ── Training export ──
trainingApi.export(opts: { format: 'openai' | 'anthropic'; minScore?: number; limit?: number }): Promise<{ blob: Blob; filename: string; count: number }>  // POST /intelligence/export-training-data

// ── Perception ──
export interface PerceptionStatus {
  playwright_available: boolean; vision_available: boolean;
  browser_actions: string[]; image_formats: string[];
}
perceptionApi.status(): Promise<PerceptionStatus>                                                          // GET /perception/status
perceptionApi.screenshot(url: string, fullPage?: boolean): Promise<{ success: boolean; url: string; screenshot_b64: string; error: string | null }>  // POST /perception/screenshot
perceptionApi.analyze(body: { screenshot_b64?: string; url?: string; question?: string }): Promise<{ analysis: string; question: string; screenshot_provided: boolean }>  // POST /perception/analyze
perceptionApi.extract(url: string, selector?: string): Promise<{ success: boolean; url: string; selector: string; text: string; char_count: number; error: string | null }>  // POST /perception/extract

// ── A2A (read-only) ──
export interface AgentCard {
  agent_id: string; name: string; version: string; description: string;
  endpoint: string; authentication: { scheme: string; header: string; note: string };
  capabilities: string[]; supported_task_types: string[];
}
export interface A2ATask {
  task_id: string; goal: string; status: string;
  result?: string; callback_url?: string; created_at?: string;
}
a2aApi.agentCard(): Promise<AgentCard>                                                                     // GET /.well-known/agent.json
a2aApi.getTask(taskId: string): Promise<A2ATask>                                                           // GET /a2a/tasks/{taskId}

// ── Integrations (inbound webhooks; config + delivery visibility) ──
export interface ZapierCompletedGoal { id?: string; goal_id?: string; goal?: string; status: string; [key: string]: unknown }
integrationsApi.zapierCompletedGoals(): Promise<ZapierCompletedGoal[]>                                     // GET /integrations/zapier/goals
```

> **Note on `trainingApi.export`:** the endpoint streams a download with no JSON body. The client reads the response as a `Blob` and parses the filename from `Content-Disposition` + count from the `X-Training-Examples` header. This needs a raw fetch (not the JSON `request()` helper). Implement it with an exported helper that reuses `API_BASE` + `getApiKey()` (the `request()` JSON path cannot return a blob). The 401/error toast behavior is replicated inline for this one method.

- [ ] **Step 1: Write the failing tests**

```ts
// append to src/lib/api/client.test.ts
import { artifactsApi, toolsApi, perceptionApi, a2aApi, integrationsApi, trainingApi } from '@/lib/api/client';

test('artifactsApi.list builds /artifacts query', async () => {
  const f = mockOk([{ id: 'a1', name: 'out.txt', artifact_type: 'file', storage_uri: 's3://x', content_type: 'text/plain', size_bytes: 5, goal_id: 'g1', created_at: '' }]);
  await artifactsApi.list({ goalId: 'g1', artifactType: 'file', limit: 10 });
  const url = String(f.mock.calls[0][0]);
  expect(url).toContain('/artifacts?');
  expect(url).toContain('goal_id=g1');
  expect(url).toContain('artifact_type=file');
  expect(url).toContain('limit=10');
});

test('toolsApi.executeCode posts code/language/timeout to /tools/execute-code', async () => {
  const f = mockOk({ stdout: 'hi', stderr: '', exit_code: 0, success: true, timed_out: false, execution_time_ms: 12 });
  const r = await toolsApi.executeCode("print('hi')", 'python', 10);
  expect(String(f.mock.calls[0][0])).toContain('/tools/execute-code');
  expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body))).toEqual({ code: "print('hi')", language: 'python', timeout: 10 });
  expect(r.stdout).toBe('hi');
});

test('toolsApi.readFile encodes the path segment', async () => {
  const f = mockOk({ path: 'a/b.txt', content: 'z', success: true });
  await toolsApi.readFile('a/b.txt');
  expect(String(f.mock.calls[0][0])).toContain('/tools/files/a/b.txt');
});

test('toolsApi.sendEmail posts to /tools/email/send', async () => {
  const f = mockOk({ status: 'sent' });
  await toolsApi.sendEmail({ to: 'x@y.z', subject: 'S', body: 'B' });
  expect(String(f.mock.calls[0][0])).toContain('/tools/email/send');
  expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body))).toMatchObject({ to: 'x@y.z', subject: 'S' });
});

test('perceptionApi.screenshot posts url + full_page', async () => {
  const f = mockOk({ success: true, url: 'http://x', screenshot_b64: 'AAA', error: null });
  await perceptionApi.screenshot('http://x', true);
  expect(String(f.mock.calls[0][0])).toContain('/perception/screenshot');
  expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body))).toEqual({ url: 'http://x', full_page: true });
});

test('a2aApi.agentCard fetches /.well-known/agent.json', async () => {
  const f = mockOk({ agent_id: 'p', name: 'AgentVerse', version: '0.1.0', description: '', endpoint: '', authentication: { scheme: 'hmac-sha256', header: 'X-A2A-Signature', note: '' }, capabilities: [], supported_task_types: [] });
  await a2aApi.agentCard();
  expect(String(f.mock.calls[0][0])).toContain('/.well-known/agent.json');
});

test('a2aApi.getTask fetches /a2a/tasks/{id}', async () => {
  const f = mockOk({ task_id: 't1', goal: 'g', status: 'complete' });
  await a2aApi.getTask('t1');
  expect(String(f.mock.calls[0][0])).toContain('/a2a/tasks/t1');
});

test('integrationsApi.zapierCompletedGoals fetches /integrations/zapier/goals', async () => {
  const f = mockOk([{ goal_id: 'g1', status: 'complete' }]);
  await integrationsApi.zapierCompletedGoals();
  expect(String(f.mock.calls[0][0])).toContain('/integrations/zapier/goals');
});

test('trainingApi.export downloads a blob + parses headers', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response('{"a":1}\n{"b":2}', {
      status: 200,
      headers: {
        'Content-Type': 'application/x-ndjson',
        'Content-Disposition': 'attachment; filename="agentverse_training_openai_x.jsonl"',
        'X-Training-Examples': '2',
      },
    })
  );
  const res = await trainingApi.export({ format: 'openai', minScore: 0.8, limit: 100 });
  expect(res.filename).toBe('agentverse_training_openai_x.jsonl');
  expect(res.count).toBe(2);
  expect(res.blob).toBeInstanceOf(Blob);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/api/client.test.ts`
Expected: FAIL — the new namespaces are not exported yet.

- [ ] **Step 3: Implement**

Append to `src/lib/api/client.ts` (after `memoryApi`). Note `encodePath` keeps `/` separators while encoding each segment:

```ts
// ── Artifacts ──────────────────────────────────────────────────────────────────

export interface Artifact {
  id: string;
  name: string;
  artifact_type: string;
  storage_uri: string;
  content_type: string;
  size_bytes: number;
  goal_id: string;
  created_at: string;
}

export const artifactsApi = {
  list: (opts: { goalId?: string; artifactType?: string; limit?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.goalId) params.set("goal_id", opts.goalId);
    if (opts.artifactType) params.set("artifact_type", opts.artifactType);
    params.set("limit", String(opts.limit ?? 50));
    return request<Artifact[]>(`/artifacts?${params.toString()}`);
  },
  get: (id: string) => request<Artifact>(`/artifacts/${id}`),
  delete: (id: string) => request<void>(`/artifacts/${id}`, { method: "DELETE" }),
};

// ── Tools ──────────────────────────────────────────────────────────────────────

const encodePath = (p: string): string =>
  p.split("/").map(encodeURIComponent).join("/");

export interface ExecuteCodeResult {
  stdout: string;
  stderr: string;
  exit_code: number;
  success: boolean;
  timed_out: boolean;
  execution_time_ms: number;
}

export interface WorkspaceFile {
  name: string;
  path: string;
  size_bytes?: number;
  is_dir?: boolean;
  [key: string]: unknown;
}

export const toolsApi = {
  executeCode: (
    code: string,
    language: "python" | "javascript" | "bash" = "python",
    timeout = 30
  ) =>
    request<ExecuteCodeResult>("/tools/execute-code", {
      method: "POST",
      body: JSON.stringify({ code, language, timeout }),
    }),
  listFiles: (directory = ".") =>
    request<WorkspaceFile[]>(`/tools/files?directory=${encodeURIComponent(directory)}`),
  readFile: (path: string) =>
    request<{ path: string; content: string; success: boolean }>(`/tools/files/${encodePath(path)}`),
  writeFile: (path: string, content: string) =>
    request<{ path: string; bytes_written: number; success: boolean }>(
      `/tools/files/${encodePath(path)}`,
      { method: "POST", body: JSON.stringify({ content }) }
    ),
  deleteFile: (path: string) =>
    request<void>(`/tools/files/${encodePath(path)}`, { method: "DELETE" }),
  sendEmail: (body: {
    to: string | string[];
    subject: string;
    body: string;
    from_addr?: string;
  }) => request<Record<string, unknown>>("/tools/email/send", {
    method: "POST",
    body: JSON.stringify(body),
  }),
};

// ── Training export ──────────────────────────────────────────────────────────

function parseFilename(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const match = /filename="?([^"]+)"?/.exec(disposition);
  return match ? match[1] : fallback;
}

export const trainingApi = {
  export: async (opts: {
    format: "openai" | "anthropic";
    minScore?: number;
    limit?: number;
  }): Promise<{ blob: Blob; filename: string; count: number }> => {
    const params = new URLSearchParams();
    params.set("format", opts.format);
    params.set("min_score", String(opts.minScore ?? 0.8));
    params.set("limit", String(opts.limit ?? 1000));
    const apiKey = getApiKey();
    const headers: Record<string, string> = {};
    if (apiKey) headers["X-API-Key"] = apiKey;
    const res = await fetch(
      `${API_BASE_URL}/intelligence/export-training-data?${params.toString()}`,
      { method: "POST", headers }
    );
    if (!res.ok) {
      throw new ApiError(res.status, res.statusText);
    }
    const blob = await res.blob();
    return {
      blob,
      filename: parseFilename(
        res.headers.get("Content-Disposition"),
        `training_${opts.format}.jsonl`
      ),
      count: Number(res.headers.get("X-Training-Examples") ?? 0),
    };
  },
};

// ── Perception ─────────────────────────────────────────────────────────────────

export interface PerceptionStatus {
  playwright_available: boolean;
  vision_available: boolean;
  browser_actions: string[];
  image_formats: string[];
}

export const perceptionApi = {
  status: () => request<PerceptionStatus>("/perception/status"),
  screenshot: (url: string, fullPage = false) =>
    request<{ success: boolean; url: string; screenshot_b64: string; error: string | null }>(
      "/perception/screenshot",
      { method: "POST", body: JSON.stringify({ url, full_page: fullPage }) }
    ),
  analyze: (body: { screenshot_b64?: string; url?: string; question?: string }) =>
    request<{ analysis: string; question: string; screenshot_provided: boolean }>(
      "/perception/analyze",
      { method: "POST", body: JSON.stringify(body) }
    ),
  extract: (url: string, selector = "body") =>
    request<{
      success: boolean;
      url: string;
      selector: string;
      text: string;
      char_count: number;
      error: string | null;
    }>("/perception/extract", {
      method: "POST",
      body: JSON.stringify({ url, selector }),
    }),
};

// ── A2A (read-only) ──────────────────────────────────────────────────────────

export interface AgentCard {
  agent_id: string;
  name: string;
  version: string;
  description: string;
  endpoint: string;
  authentication: { scheme: string; header: string; note: string };
  capabilities: string[];
  supported_task_types: string[];
}

export interface A2ATask {
  task_id: string;
  goal: string;
  status: string;
  result?: string;
  callback_url?: string;
  created_at?: string;
}

export const a2aApi = {
  agentCard: () => request<AgentCard>("/.well-known/agent.json"),
  getTask: (taskId: string) => request<A2ATask>(`/a2a/tasks/${taskId}`),
};

// ── Integrations (inbound webhooks; config + delivery visibility) ──────────────

export interface ZapierCompletedGoal {
  id?: string;
  goal_id?: string;
  goal?: string;
  status: string;
  [key: string]: unknown;
}

export const integrationsApi = {
  zapierCompletedGoals: () =>
    request<ZapierCompletedGoal[]>("/integrations/zapier/goals"),
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/lib/api/client.test.ts && npm run typecheck`
Expected: PASS / no type errors.

- [ ] **Step 5: Commit**

```bash
git add src/lib/api/client.ts src/lib/api/client.test.ts
git commit -m "feat(client): add artifacts/tools/training/perception/a2a/integrations namespaces

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Memory explorer page (list / recall / delete / tool-reliability)

**Files:**
- Create: `src/features/memory/MemoryExplorerPage.tsx`, `src/features/memory/MemoryExplorerPage.test.tsx`

**Interfaces:**
- Consumes: `memoryApi.list`, `memoryApi.recall`, `memoryApi.delete`, `memoryApi.toolReliability` (Task 1); `toast` (Phase 1); `Skeleton`, `EmptyState` (Phase 1).
- Produces: `export function MemoryExplorerPage(): JSX.Element`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/memory/MemoryExplorerPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MemoryExplorerPage } from './MemoryExplorerPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><MemoryExplorerPage /></MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MemoryExplorerPage', () => {
  test('lists memories from /memory', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (url.includes('/memory?')) return new Response(JSON.stringify([
        { id: 'm1', content: 'Remember the API key rotates monthly', memory_type: 'fact', confidence: 0.9, tags: ['ops'], created_at: '2026-06-01T00:00:00Z' },
      ]), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText(/API key rotates monthly/)).toBeInTheDocument();
  });

  test('recall queries /memory/recall and shows results', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/recall')) return new Response(JSON.stringify({ query: 'keys', results: [{ content: 'rotate keys', confidence: 0.7, memory_type: 'fact', source: 'g1' }] }), { status: 200 });
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByPlaceholderText(/recall/i), 'keys');
    await userEvent.click(screen.getByRole('button', { name: /recall/i }));
    expect(await screen.findByText('rotate keys')).toBeInTheDocument();
  });

  test('delete calls DELETE /memory/{id}', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (url.match(/\/memory\/m1$/) && init?.method === 'DELETE') return new Response(JSON.stringify({ deleted: 'm1', status: 'ok' }), { status: 200 });
      if (url.includes('/memory?')) return new Response(JSON.stringify([{ id: 'm1', content: 'Old note', memory_type: 'fact', confidence: 0.5, tags: [], created_at: '' }]), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await screen.findByText('Old note');
    await userEvent.click(screen.getByRole('button', { name: /delete memory/i }));
    await waitFor(() => {
      const del = f.mock.calls.find(([u, i]) => /\/memory\/m1$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE');
      expect(del).toBeTruthy();
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/memory/MemoryExplorerPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/features/memory/MemoryExplorerPage.tsx
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Trash2, Search } from 'lucide-react';
import { memoryApi, type RecallResult } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';

export function MemoryExplorerPage() {
  const qc = useQueryClient();
  const [query, setQuery] = useState('');
  const [recalled, setRecalled] = useState<RecallResult[] | null>(null);

  const { data: memories = [], isLoading } = useQuery({
    queryKey: ['memories'],
    queryFn: () => memoryApi.list({ limit: 100 }),
  });

  const { data: reliability = [] } = useQuery({
    queryKey: ['tool-reliability'],
    queryFn: () => memoryApi.toolReliability(),
  });

  const recallMutation = useMutation({
    mutationFn: () => memoryApi.recall(query, 10),
    onSuccess: (rows) => setRecalled(rows),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => memoryApi.delete(id),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Memory deleted.' });
      qc.invalidateQueries({ queryKey: ['memories'] });
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Memory Explorer</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Long-term memories, semantic recall, and tool reliability
        </p>
      </div>

      {/* Recall */}
      <div className="bg-card border border-border rounded-xl p-4">
        <form
          className="flex gap-2"
          onSubmit={(e) => { e.preventDefault(); if (query.trim()) recallMutation.mutate(); }}
        >
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Recall memories relevant to…"
            aria-label="Recall query"
            className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-background"
          />
          <button
            type="submit"
            disabled={recallMutation.isPending || !query.trim()}
            className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
          >
            <Search className="h-4 w-4" /> Recall
          </button>
        </form>
        {recalled && (
          <div className="mt-3 space-y-2">
            {recalled.length === 0 ? (
              <p className="text-sm text-muted-foreground">No relevant memories.</p>
            ) : recalled.map((r, i) => (
              <div key={i} className="text-sm border border-border rounded-md p-2">
                <p>{r.content}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {r.memory_type} · confidence {(r.confidence * 100).toFixed(0)}%
                </p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* All memories */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-sm">Long-term Memories</h2>
        </div>
        {isLoading ? (
          <div className="p-5 space-y-2"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
        ) : memories.length === 0 ? (
          <EmptyState title="No memories yet" description="Memories accumulate as agents complete goals." />
        ) : (
          <ul className="divide-y divide-border">
            {memories.map((m) => (
              <li key={m.id} className="px-5 py-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm">{m.content}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {m.memory_type} · {(m.confidence * 100).toFixed(0)}%
                    {m.tags.length > 0 && ` · ${m.tags.join(', ')}`}
                  </p>
                </div>
                <button
                  aria-label="Delete memory"
                  onClick={() => deleteMutation.mutate(m.id)}
                  className="text-muted-foreground hover:text-destructive flex-shrink-0"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Tool reliability */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-sm">Unreliable Tools</h2>
        </div>
        {reliability.length === 0 ? (
          <EmptyState title="All tools reliable" description="No tools below the reliability threshold." />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground border-b border-border">
                <th className="px-5 py-2 font-medium">Tool</th>
                <th className="px-5 py-2 font-medium">Calls</th>
                <th className="px-5 py-2 font-medium">Failures</th>
                <th className="px-5 py-2 font-medium">Success rate</th>
              </tr>
            </thead>
            <tbody>
              {reliability.map((t) => (
                <tr key={t.tool_name} className="border-b border-border last:border-0">
                  <td className="px-5 py-2 font-mono text-xs">{t.tool_name}</td>
                  <td className="px-5 py-2">{t.total_calls}</td>
                  <td className="px-5 py-2 text-destructive">{t.failures}</td>
                  <td className="px-5 py-2">{(t.success_rate * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/memory/MemoryExplorerPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/memory/MemoryExplorerPage.tsx src/features/memory/MemoryExplorerPage.test.tsx
git commit -m "feat(memory): memory explorer page (list/recall/delete/reliability)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Artifacts browser page (list / preview image+text / download / delete)

**Files:**
- Create: `src/features/artifacts/ArtifactsBrowserPage.tsx`, `src/features/artifacts/ArtifactsBrowserPage.test.tsx`

**Context:** No backend byte-download endpoint exists. Download/preview operate on the row's `storage_uri`: if it parses as an `http(s)` URL, render/download it directly; otherwise show the opaque reference and disable the action with a tooltip. Image preview requires `content_type` starting `image/` **and** an http(s) `storage_uri`; text preview requires `content_type` starting `text/` (rendered from `storage_uri` if fetchable, else show the URI).

**Interfaces:**
- Consumes: `artifactsApi.list`, `artifactsApi.delete` (Task 2); `toast`; `Skeleton`, `EmptyState`.
- Produces: `export function ArtifactsBrowserPage(): JSX.Element`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/artifacts/ArtifactsBrowserPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ArtifactsBrowserPage } from './ArtifactsBrowserPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ArtifactsBrowserPage /></MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

const ARTIFACT = {
  id: 'a1', name: 'report.txt', artifact_type: 'file', storage_uri: 'https://cdn/x/report.txt',
  content_type: 'text/plain', size_bytes: 120, goal_id: 'g1', created_at: '2026-06-10T00:00:00Z',
};

describe('ArtifactsBrowserPage', () => {
  test('lists artifacts', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify([ARTIFACT]), { status: 200 }));
    renderPage();
    expect(await screen.findByText('report.txt')).toBeInTheDocument();
  });

  test('empty state when no artifacts', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    expect(await screen.findByText(/no artifacts/i)).toBeInTheDocument();
  });

  test('delete calls DELETE /artifacts/{id}', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.match(/\/artifacts\/a1$/) && init?.method === 'DELETE') return new Response(null, { status: 204 });
      return new Response(JSON.stringify([ARTIFACT]), { status: 200 });
    });
    renderPage();
    await screen.findByText('report.txt');
    await userEvent.click(screen.getByRole('button', { name: /delete artifact/i }));
    await waitFor(() => {
      const del = f.mock.calls.find(([u, i]) => /\/artifacts\/a1$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE');
      expect(del).toBeTruthy();
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/artifacts/ArtifactsBrowserPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/features/artifacts/ArtifactsBrowserPage.tsx
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Trash2, Download, Eye, FileText } from 'lucide-react';
import { artifactsApi, type Artifact } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';

function isHttpUri(uri: string): boolean {
  return /^https?:\/\//.test(uri);
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function ArtifactsBrowserPage() {
  const qc = useQueryClient();
  const [preview, setPreview] = useState<Artifact | null>(null);

  const { data: artifacts = [], isLoading } = useQuery({
    queryKey: ['artifacts'],
    queryFn: () => artifactsApi.list({ limit: 100 }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => artifactsApi.delete(id),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Artifact deleted.' });
      qc.invalidateQueries({ queryKey: ['artifacts'] });
    },
  });

  const previewable = useMemo(() => {
    if (!preview) return null;
    if (!isHttpUri(preview.storage_uri)) return { kind: 'ref' as const };
    if (preview.content_type.startsWith('image/')) return { kind: 'image' as const };
    if (preview.content_type.startsWith('text/')) return { kind: 'text' as const };
    return { kind: 'ref' as const };
  }, [preview]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Artifacts</h1>
        <p className="text-muted-foreground text-sm mt-1">Files produced by agent runs</p>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="p-5 space-y-2"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
        ) : artifacts.length === 0 ? (
          <EmptyState title="No artifacts" description="Agent runs that produce files will appear here." />
        ) : (
          <ul className="divide-y divide-border">
            {artifacts.map((a) => (
              <li key={a.id} className="px-5 py-3 flex items-center justify-between gap-3">
                <div className="flex items-center gap-3 min-w-0">
                  <FileText className="h-5 w-5 text-muted-foreground flex-shrink-0" aria-hidden="true" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{a.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {a.content_type} · {formatBytes(a.size_bytes)} · goal {a.goal_id.slice(0, 8)}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-3 flex-shrink-0">
                  <button aria-label="Preview artifact" onClick={() => setPreview(a)} className="text-muted-foreground hover:text-foreground">
                    <Eye className="h-4 w-4" />
                  </button>
                  <a
                    aria-label="Download artifact"
                    href={isHttpUri(a.storage_uri) ? a.storage_uri : undefined}
                    download={a.name}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={isHttpUri(a.storage_uri) ? 'text-muted-foreground hover:text-foreground' : 'text-muted-foreground/40 pointer-events-none'}
                    title={isHttpUri(a.storage_uri) ? 'Download' : 'No direct URL — stored at ' + a.storage_uri}
                  >
                    <Download className="h-4 w-4" />
                  </a>
                  <button aria-label="Delete artifact" onClick={() => deleteMutation.mutate(a.id)} className="text-muted-foreground hover:text-destructive">
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Preview drawer */}
      {preview && previewable && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-label="Artifact preview" onClick={() => setPreview(null)}>
          <div className="bg-card border border-border rounded-xl max-w-3xl w-full max-h-[80vh] overflow-auto p-5" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-sm">{preview.name}</h3>
              <button aria-label="Close preview" onClick={() => setPreview(null)} className="text-muted-foreground hover:text-foreground">×</button>
            </div>
            {previewable.kind === 'image' && (
              <img src={preview.storage_uri} alt={preview.name} className="max-w-full rounded-md" />
            )}
            {previewable.kind === 'text' && (
              <iframe src={preview.storage_uri} title={preview.name} className="w-full h-96 border border-border rounded-md" />
            )}
            {previewable.kind === 'ref' && (
              <p className="text-sm text-muted-foreground font-mono break-all">{preview.storage_uri}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/artifacts/ArtifactsBrowserPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/artifacts/ArtifactsBrowserPage.tsx src/features/artifacts/ArtifactsBrowserPage.test.tsx
git commit -m "feat(artifacts): artifacts browser (list/preview/download/delete)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Tools page — code runner + file manager + email composer

**Files:**
- Create: `src/features/tools/ToolsPage.tsx`, `src/features/tools/ToolsPage.test.tsx`

**Context:** Code execution and email are **governed actions** — show stdout/stderr/exit code and any errors clearly; surface failures via the per-call result and a toast. A single page with three tab sections.

**Interfaces:**
- Consumes: `toolsApi.executeCode`, `toolsApi.listFiles`, `toolsApi.readFile`, `toolsApi.writeFile`, `toolsApi.deleteFile`, `toolsApi.sendEmail` (Task 2); `toast`; `StatusBadge`.
- Produces: `export function ToolsPage(): JSX.Element`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/tools/ToolsPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ToolsPage } from './ToolsPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ToolsPage /></MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ToolsPage', () => {
  test('runs code and shows stdout + exit code', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/tools/execute-code')) return new Response(JSON.stringify({ stdout: 'hello world', stderr: '', exit_code: 0, success: true, timed_out: false, execution_time_ms: 9 }), { status: 200 });
      if (url.includes('/tools/files')) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByLabelText(/code/i), "print('hi')");
    await userEvent.click(screen.getByRole('button', { name: /run code/i }));
    expect(await screen.findByText('hello world')).toBeInTheDocument();
    expect(screen.getByText(/exit 0/i)).toBeInTheDocument();
  });

  test('email composer posts to /tools/email/send', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/tools/email/send') && init?.method === 'POST') return new Response(JSON.stringify({ status: 'sent' }), { status: 200 });
      if (url.includes('/tools/files')) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /email/i }));
    await userEvent.type(screen.getByLabelText(/to/i), 'x@y.z');
    await userEvent.type(screen.getByLabelText(/subject/i), 'Hi');
    await userEvent.type(screen.getByLabelText(/message/i), 'Body');
    await userEvent.click(screen.getByRole('button', { name: /send email/i }));
    await waitFor(() => {
      const call = f.mock.calls.find(([u, i]) => String(u).includes('/tools/email/send') && (i as RequestInit)?.method === 'POST');
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call![1] as RequestInit).body))).toMatchObject({ to: 'x@y.z', subject: 'Hi', body: 'Body' });
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/tools/ToolsPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/features/tools/ToolsPage.tsx
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Play, Send, Trash2, FileText, RefreshCw } from 'lucide-react';
import { toolsApi, type ExecuteCodeResult, type WorkspaceFile } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { StatusBadge } from '@/components/ui/StatusBadge';

type Tab = 'code' | 'files' | 'email';
const LANGUAGES = ['python', 'javascript', 'bash'] as const;

export function ToolsPage() {
  const [tab, setTab] = useState<Tab>('code');
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Tools</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Sandboxed code execution, workspace files, and email — governed actions
        </p>
      </div>
      <div role="tablist" className="flex gap-1 border-b border-border">
        {(['code', 'files', 'email'] as Tab[]).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium capitalize border-b-2 -mb-px ${tab === t ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
          >
            {t === 'code' ? 'Code Runner' : t === 'files' ? 'File Manager' : 'Email'}
          </button>
        ))}
      </div>
      {tab === 'code' && <CodeRunner />}
      {tab === 'files' && <FileManager />}
      {tab === 'email' && <EmailComposer />}
    </div>
  );
}

function CodeRunner() {
  const [code, setCode] = useState('');
  const [language, setLanguage] = useState<(typeof LANGUAGES)[number]>('python');
  const [result, setResult] = useState<ExecuteCodeResult | null>(null);

  const runMutation = useMutation({
    mutationFn: () => toolsApi.executeCode(code, language, 30),
    onSuccess: (r) => {
      setResult(r);
      if (!r.success) toast({ kind: 'error', message: r.timed_out ? 'Execution timed out.' : 'Code exited non-zero.' });
    },
  });

  return (
    <div className="bg-card border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        <label htmlFor="lang" className="text-sm text-muted-foreground">Language</label>
        <select id="lang" value={language} onChange={(e) => setLanguage(e.target.value as (typeof LANGUAGES)[number])} className="px-2 py-1 border border-border rounded-md text-sm bg-background">
          {LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
      </div>
      <textarea
        aria-label="Code"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        placeholder="Enter code to execute in a sandboxed container…"
        className="w-full h-48 px-3 py-2 border border-border rounded-md text-sm font-mono bg-background"
      />
      <button
        onClick={() => runMutation.mutate()}
        disabled={runMutation.isPending || !code.trim()}
        className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
      >
        <Play className="h-4 w-4" /> Run code
      </button>
      {result && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm">
            <StatusBadge status={result.success ? 'success' : 'failed'} />
            <span className="text-muted-foreground">exit {result.exit_code} · {result.execution_time_ms.toFixed(0)}ms</span>
          </div>
          {result.stdout && (
            <pre className="text-xs font-mono bg-muted rounded-md p-3 overflow-auto whitespace-pre-wrap">{result.stdout}</pre>
          )}
          {result.stderr && (
            <pre className="text-xs font-mono bg-destructive/10 text-destructive rounded-md p-3 overflow-auto whitespace-pre-wrap">{result.stderr}</pre>
          )}
        </div>
      )}
    </div>
  );
}

function FileManager() {
  const qc = useQueryClient();
  const [directory] = useState('.');
  const [selectedPath, setSelectedPath] = useState('');
  const [content, setContent] = useState('');

  const { data: files = [], isLoading } = useQuery({
    queryKey: ['workspace-files', directory],
    queryFn: () => toolsApi.listFiles(directory),
  });

  const openMutation = useMutation({
    mutationFn: (path: string) => toolsApi.readFile(path),
    onSuccess: (r) => { setSelectedPath(r.path); setContent(r.content); },
  });
  const saveMutation = useMutation({
    mutationFn: () => toolsApi.writeFile(selectedPath, content),
    onSuccess: () => { toast({ kind: 'success', message: 'File saved.' }); qc.invalidateQueries({ queryKey: ['workspace-files'] }); },
  });
  const deleteMutation = useMutation({
    mutationFn: (path: string) => toolsApi.deleteFile(path),
    onSuccess: () => { toast({ kind: 'success', message: 'File deleted.' }); qc.invalidateQueries({ queryKey: ['workspace-files'] }); },
  });

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-border flex items-center justify-between">
          <h2 className="font-semibold text-sm">Workspace</h2>
          <button aria-label="Refresh files" onClick={() => qc.invalidateQueries({ queryKey: ['workspace-files'] })} className="text-muted-foreground hover:text-foreground">
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
        {isLoading ? (
          <p className="px-5 py-4 text-sm text-muted-foreground">Loading…</p>
        ) : files.length === 0 ? (
          <p className="px-5 py-4 text-sm text-muted-foreground">No files in workspace.</p>
        ) : (
          <ul className="divide-y divide-border">
            {files.map((f: WorkspaceFile) => (
              <li key={f.path} className="px-5 py-2 flex items-center justify-between gap-2">
                <button onClick={() => openMutation.mutate(f.path)} className="flex items-center gap-2 text-sm hover:text-primary min-w-0">
                  <FileText className="h-4 w-4 flex-shrink-0" /> <span className="truncate">{f.name}</span>
                </button>
                <button aria-label={`Delete ${f.name}`} onClick={() => deleteMutation.mutate(f.path)} className="text-muted-foreground hover:text-destructive flex-shrink-0">
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="bg-card border border-border rounded-xl p-4 space-y-3">
        <input
          value={selectedPath}
          onChange={(e) => setSelectedPath(e.target.value)}
          placeholder="path/to/file.txt"
          aria-label="File path"
          className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background"
        />
        <textarea
          aria-label="File content"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          className="w-full h-64 px-3 py-2 border border-border rounded-md text-sm font-mono bg-background"
        />
        <button
          onClick={() => saveMutation.mutate()}
          disabled={!selectedPath.trim() || saveMutation.isPending}
          className="px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
        >
          Save file
        </button>
      </div>
    </div>
  );
}

function EmailComposer() {
  const [to, setTo] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');

  const sendMutation = useMutation({
    mutationFn: () => toolsApi.sendEmail({ to, subject, body }),
    onSuccess: () => { toast({ kind: 'success', message: 'Email sent.' }); setTo(''); setSubject(''); setBody(''); },
  });

  return (
    <form
      className="bg-card border border-border rounded-xl p-4 space-y-3 max-w-xl"
      onSubmit={(e) => { e.preventDefault(); sendMutation.mutate(); }}
    >
      <div>
        <label htmlFor="to" className="text-sm text-muted-foreground">To</label>
        <input id="to" value={to} onChange={(e) => setTo(e.target.value)} className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
      </div>
      <div>
        <label htmlFor="subject" className="text-sm text-muted-foreground">Subject</label>
        <input id="subject" value={subject} onChange={(e) => setSubject(e.target.value)} className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
      </div>
      <div>
        <label htmlFor="message" className="text-sm text-muted-foreground">Message</label>
        <textarea id="message" value={body} onChange={(e) => setBody(e.target.value)} className="w-full h-40 px-3 py-2 border border-border rounded-md text-sm bg-background" />
      </div>
      <button
        type="submit"
        disabled={!to.trim() || !subject.trim() || sendMutation.isPending}
        className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
      >
        <Send className="h-4 w-4" /> Send email
      </button>
    </form>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/tools/ToolsPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/tools/ToolsPage.tsx src/features/tools/ToolsPage.test.tsx
git commit -m "feat(tools): code runner + file manager + email composer page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Integrations config page (Slack/Zapier/AlertManager/Datadog endpoints + Zapier delivery)

**Files:**
- Create: `src/features/integrations/IntegrationsPage.tsx`, `src/features/integrations/IntegrationsPage.test.tsx`

**Context:** These are **inbound webhooks** with no config-persistence API. The UI shows, per provider: the copyable inbound endpoint URL, the env-var/secret name the operator must set (display-only — never a live value), and (Zapier only) a live delivery view via `GET /integrations/zapier/goals` (recently completed goals the poll trigger would emit). No POST is made from this page except the Zapier delivery refresh (a GET). Copy uses `navigator.clipboard.writeText` + a success toast.

**Interfaces:**
- Consumes: `integrationsApi.zapierCompletedGoals` (Task 2); `API_BASE` (`@/lib/api/client`) to compose displayed URLs; `toast`; `StatusBadge`.
- Produces: `export function IntegrationsPage(): JSX.Element`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/integrations/IntegrationsPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { IntegrationsPage } from './IntegrationsPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><IntegrationsPage /></MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});
afterEach(() => vi.restoreAllMocks());

describe('IntegrationsPage', () => {
  test('shows the four providers with their endpoint paths', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    expect(screen.getByText('Slack')).toBeInTheDocument();
    expect(screen.getByText('Zapier')).toBeInTheDocument();
    expect(screen.getByText('Alertmanager')).toBeInTheDocument();
    expect(screen.getByText('Datadog')).toBeInTheDocument();
    expect(screen.getByText(/\/integrations\/slack\/commands/)).toBeInTheDocument();
    expect(screen.getByText(/\/integrations\/events\/datadog/)).toBeInTheDocument();
  });

  test('copy button writes the endpoint URL to the clipboard', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    await userEvent.click(screen.getAllByRole('button', { name: /copy endpoint/i })[0]);
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalled());
  });

  test('shows Zapier delivery from /integrations/zapier/goals', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/integrations/zapier/goals')) return new Response(JSON.stringify([{ goal_id: 'g1', goal: 'Resolve incident', status: 'complete' }]), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText('Resolve incident')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/integrations/IntegrationsPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/features/integrations/IntegrationsPage.tsx
import { useQuery } from '@tanstack/react-query';
import { Copy } from 'lucide-react';
import { API_BASE, integrationsApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { StatusBadge } from '@/components/ui/StatusBadge';

interface ProviderConfig {
  name: string;
  description: string;
  endpoints: { label: string; path: string }[];
  secretEnv: string[];
  inbound: boolean;
}

const PROVIDERS: ProviderConfig[] = [
  {
    name: 'Slack',
    description: 'Slash command + interactive HITL buttons. Verifies requests with the signing secret.',
    endpoints: [
      { label: 'Slash command', path: '/integrations/slack/commands' },
      { label: 'Events', path: '/integrations/slack/events' },
      { label: 'Interactive', path: '/integrations/slack/interactive' },
    ],
    secretEnv: ['SLACK_SIGNING_SECRET', 'SLACK_TENANT_ID'],
    inbound: true,
  },
  {
    name: 'Zapier',
    description: 'Inbound trigger to create goals; outbound polling trigger for completed goals.',
    endpoints: [
      { label: 'Trigger', path: '/integrations/zapier/trigger' },
      { label: 'Completed-goals poll', path: '/integrations/zapier/goals' },
    ],
    secretEnv: ['ZAPIER_SECRET', 'ZAPIER_TENANT_ID'],
    inbound: true,
  },
  {
    name: 'Alertmanager',
    description: 'Receives firing alerts and creates investigation goals.',
    endpoints: [{ label: 'Webhook', path: '/integrations/events/alertmanager' }],
    secretEnv: ['ALERTMANAGER_TENANT_ID'],
    inbound: true,
  },
  {
    name: 'Datadog',
    description: 'Receives critical/error events and creates goals; HMAC-verified when secret set.',
    endpoints: [{ label: 'Webhook', path: '/integrations/events/datadog' }],
    secretEnv: ['DATADOG_WEBHOOK_SECRET', 'DATADOG_TENANT_ID'],
    inbound: true,
  },
];

async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast({ kind: 'success', message: 'Endpoint copied to clipboard.' });
  } catch {
    toast({ kind: 'error', message: 'Could not copy — copy it manually.' });
  }
}

export function IntegrationsPage() {
  const { data: zapierGoals = [] } = useQuery({
    queryKey: ['zapier-goals'],
    queryFn: () => integrationsApi.zapierCompletedGoals(),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Integrations</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Inbound webhook endpoints. Point each provider at the URL below and set the listed env vars on the server.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {PROVIDERS.map((p) => (
          <div key={p.name} className="bg-card border border-border rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">{p.name}</h2>
              <StatusBadge status={p.inbound ? 'running' : 'pending'} />
            </div>
            <p className="text-sm text-muted-foreground">{p.description}</p>
            <div className="space-y-1.5">
              {p.endpoints.map((e) => {
                const fullUrl = `${API_BASE}${e.path}`;
                return (
                  <div key={e.path} className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground w-32 flex-shrink-0">{e.label}</span>
                    <code className="text-xs bg-muted rounded px-2 py-1 flex-1 truncate">{e.path}</code>
                    <button aria-label={`Copy endpoint ${e.label}`} onClick={() => copy(fullUrl)} className="text-muted-foreground hover:text-foreground flex-shrink-0">
                      <Copy className="h-4 w-4" />
                    </button>
                  </div>
                );
              })}
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Required server env vars:</p>
              <div className="flex flex-wrap gap-1.5">
                {p.secretEnv.map((s) => (
                  <code key={s} className="text-xs bg-muted rounded px-2 py-0.5">{s}</code>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Zapier delivery visibility */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-sm">Zapier — recent completed goals (poll payload)</h2>
        </div>
        {zapierGoals.length === 0 ? (
          <p className="px-5 py-4 text-sm text-muted-foreground">No completed goals available to the Zapier poll trigger.</p>
        ) : (
          <ul className="divide-y divide-border">
            {zapierGoals.map((g, i) => (
              <li key={g.goal_id ?? g.id ?? i} className="px-5 py-2 flex items-center justify-between gap-3">
                <span className="text-sm truncate">{g.goal ?? g.goal_id ?? '—'}</span>
                <StatusBadge status={g.status} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/integrations/IntegrationsPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/integrations/IntegrationsPage.tsx src/features/integrations/IntegrationsPage.test.tsx
git commit -m "feat(integrations): inbound-webhook config + Zapier delivery page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Training-data export page (JSONL trigger + download)

**Files:**
- Create: `src/features/training/TrainingExportPage.tsx`, `src/features/training/TrainingExportPage.test.tsx`

**Context:** `trainingApi.export` returns `{ blob, filename, count }`. On success, trigger a browser download via an object URL and show the example count. Format picker (`openai` | `anthropic`), min-score slider, limit field.

**Interfaces:**
- Consumes: `trainingApi.export` (Task 2); `toast`.
- Produces: `export function TrainingExportPage(): JSX.Element`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/training/TrainingExportPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { TrainingExportPage } from './TrainingExportPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><TrainingExportPage /></MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  // jsdom lacks createObjectURL
  Object.assign(URL, { createObjectURL: vi.fn().mockReturnValue('blob:x'), revokeObjectURL: vi.fn() });
});
afterEach(() => vi.restoreAllMocks());

describe('TrainingExportPage', () => {
  test('triggers export and shows the example count', async () => {
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{"a":1}', {
        status: 200,
        headers: {
          'Content-Type': 'application/x-ndjson',
          'Content-Disposition': 'attachment; filename="agentverse_training_openai_x.jsonl"',
          'X-Training-Examples': '7',
        },
      })
    );
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /export/i }));
    expect(await screen.findByText(/7 examples/i)).toBeInTheDocument();
    const call = f.mock.calls.find(([u]) => String(u).includes('/intelligence/export-training-data'));
    expect(call).toBeTruthy();
    expect(String(call![0])).toContain('format=openai');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/training/TrainingExportPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/features/training/TrainingExportPage.tsx
import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Download } from 'lucide-react';
import { trainingApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';

type Format = 'openai' | 'anthropic';

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function TrainingExportPage() {
  const [format, setFormat] = useState<Format>('openai');
  const [minScore, setMinScore] = useState(0.8);
  const [limit, setLimit] = useState(1000);
  const [lastCount, setLastCount] = useState<number | null>(null);

  const exportMutation = useMutation({
    mutationFn: () => trainingApi.export({ format, minScore, limit }),
    onSuccess: ({ blob, filename, count }) => {
      setLastCount(count);
      if (count === 0) {
        toast({ kind: 'info', message: 'No examples matched the filters.' });
        return;
      }
      triggerDownload(blob, filename);
      toast({ kind: 'success', message: `Exported ${count} examples.` });
    },
    onError: () => toast({ kind: 'error', message: 'Export failed.' }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Training-Data Export</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Export high-scoring goal runs as JSONL for fine-tuning
        </p>
      </div>

      <div className="bg-card border border-border rounded-xl p-5 space-y-4 max-w-lg">
        <div>
          <label htmlFor="format" className="text-sm text-muted-foreground">Format</label>
          <select id="format" value={format} onChange={(e) => setFormat(e.target.value as Format)} className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background">
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </div>
        <div>
          <label htmlFor="min-score" className="text-sm text-muted-foreground">Minimum eval score: {minScore.toFixed(2)}</label>
          <input id="min-score" type="range" min={0} max={1} step={0.05} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} className="w-full" />
        </div>
        <div>
          <label htmlFor="limit" className="text-sm text-muted-foreground">Max examples</label>
          <input id="limit" type="number" min={1} max={10000} value={limit} onChange={(e) => setLimit(Number(e.target.value))} className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
        </div>
        <button
          onClick={() => exportMutation.mutate()}
          disabled={exportMutation.isPending}
          className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
        >
          <Download className="h-4 w-4" /> {exportMutation.isPending ? 'Exporting…' : 'Export JSONL'}
        </button>
        {lastCount != null && (
          <p className="text-sm text-muted-foreground">Last export: {lastCount} examples.</p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/training/TrainingExportPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/training/TrainingExportPage.tsx src/features/training/TrainingExportPage.test.tsx
git commit -m "feat(training): JSONL training-data export page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Perception page (screenshot / analyze / extract + vision-provider status)

**Files:**
- Create: `src/features/perception/PerceptionPage.tsx`, `src/features/perception/PerceptionPage.test.tsx`

**Context:** Show a status banner (`playwright_available`, `vision_available`) from `GET /perception/status`. A URL form drives three actions: screenshot (renders the returned `screenshot_b64` as an inline image), analyze (calls `analyze` with the captured `screenshot_b64` if present, else the `url`), and extract (shows extracted text + char count). Disable screenshot/analyze when `playwright_available` is false (with a note); disable analyze's vision result when `vision_available` is false.

**Interfaces:**
- Consumes: `perceptionApi.status`, `perceptionApi.screenshot`, `perceptionApi.analyze`, `perceptionApi.extract` (Task 2); `toast`; `StatusBadge`.
- Produces: `export function PerceptionPage(): JSX.Element`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/perception/PerceptionPage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { PerceptionPage } from './PerceptionPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><PerceptionPage /></MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('PerceptionPage', () => {
  test('shows vision-provider status', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/perception/status')) return new Response(JSON.stringify({ playwright_available: true, vision_available: false, browser_actions: ['screenshot'], image_formats: ['png'] }), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText(/browser \(playwright\)/i)).toBeInTheDocument();
    expect(screen.getByText(/vision llm/i)).toBeInTheDocument();
  });

  test('screenshot renders the returned image', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/perception/status')) return new Response(JSON.stringify({ playwright_available: true, vision_available: true, browser_actions: [], image_formats: [] }), { status: 200 });
      if (url.includes('/perception/screenshot') && init?.method === 'POST') return new Response(JSON.stringify({ success: true, url: 'http://x', screenshot_b64: 'QUJD', error: null }), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/url/i), 'http://x');
    await userEvent.click(screen.getByRole('button', { name: /screenshot/i }));
    await waitFor(() => expect(screen.getByRole('img', { name: /screenshot/i })).toHaveAttribute('src', expect.stringContaining('QUJD')));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/perception/PerceptionPage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/features/perception/PerceptionPage.tsx
import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Camera, ScanText, Sparkles } from 'lucide-react';
import { perceptionApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { StatusBadge } from '@/components/ui/StatusBadge';

export function PerceptionPage() {
  const [url, setUrl] = useState('');
  const [question, setQuestion] = useState('What is the main purpose and content of this page?');
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [extracted, setExtracted] = useState<{ text: string; charCount: number } | null>(null);

  const { data: status } = useQuery({
    queryKey: ['perception-status'],
    queryFn: () => perceptionApi.status(),
  });

  const playwrightOff = status ? !status.playwright_available : false;

  const screenshotMutation = useMutation({
    mutationFn: () => perceptionApi.screenshot(url, false),
    onSuccess: (r) => {
      if (!r.success) { toast({ kind: 'error', message: r.error ?? 'Screenshot failed.' }); return; }
      setScreenshot(r.screenshot_b64);
    },
  });

  const analyzeMutation = useMutation({
    mutationFn: () => perceptionApi.analyze(screenshot ? { screenshot_b64: screenshot, question } : { url, question }),
    onSuccess: (r) => setAnalysis(r.analysis),
  });

  const extractMutation = useMutation({
    mutationFn: () => perceptionApi.extract(url),
    onSuccess: (r) => {
      if (!r.success) { toast({ kind: 'error', message: r.error ?? 'Extraction failed.' }); return; }
      setExtracted({ text: r.text, charCount: r.char_count });
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Perception</h1>
        <p className="text-muted-foreground text-sm mt-1">Screenshot, analyze, and extract from web pages</p>
      </div>

      {/* Provider status */}
      <div className="bg-card border border-border rounded-xl p-4 flex flex-wrap gap-6">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Browser (Playwright)</span>
          <StatusBadge status={status?.playwright_available ? 'success' : 'failed'} />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Vision LLM</span>
          <StatusBadge status={status?.vision_available ? 'success' : 'failed'} />
        </div>
      </div>

      {/* Controls */}
      <div className="bg-card border border-border rounded-xl p-4 space-y-3">
        <div>
          <label htmlFor="url" className="text-sm text-muted-foreground">URL</label>
          <input id="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com" className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
        </div>
        <div>
          <label htmlFor="question" className="text-sm text-muted-foreground">Analysis question</label>
          <input id="question" value={question} onChange={(e) => setQuestion(e.target.value)} className="w-full px-3 py-2 border border-border rounded-md text-sm bg-background" />
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => screenshotMutation.mutate()} disabled={!url.trim() || playwrightOff || screenshotMutation.isPending} className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50">
            <Camera className="h-4 w-4" /> Screenshot
          </button>
          <button onClick={() => analyzeMutation.mutate()} disabled={!url.trim() || playwrightOff || analyzeMutation.isPending} className="flex items-center gap-1.5 px-3 py-2 border border-border rounded-md text-sm disabled:opacity-50">
            <Sparkles className="h-4 w-4" /> Analyze
          </button>
          <button onClick={() => extractMutation.mutate()} disabled={!url.trim() || playwrightOff || extractMutation.isPending} className="flex items-center gap-1.5 px-3 py-2 border border-border rounded-md text-sm disabled:opacity-50">
            <ScanText className="h-4 w-4" /> Extract text
          </button>
        </div>
        {playwrightOff && (
          <p className="text-xs text-destructive">Playwright is unavailable on the server — browser actions are disabled.</p>
        )}
      </div>

      {screenshot && (
        <div className="bg-card border border-border rounded-xl p-4">
          <h2 className="font-semibold text-sm mb-2">Screenshot</h2>
          <img src={`data:image/png;base64,${screenshot}`} alt="Screenshot" className="max-w-full rounded-md border border-border" />
        </div>
      )}
      {analysis && (
        <div className="bg-card border border-border rounded-xl p-4">
          <h2 className="font-semibold text-sm mb-2">Analysis</h2>
          <p className="text-sm whitespace-pre-wrap">{analysis}</p>
        </div>
      )}
      {extracted && (
        <div className="bg-card border border-border rounded-xl p-4">
          <h2 className="font-semibold text-sm mb-2">Extracted text ({extracted.charCount} chars)</h2>
          <pre className="text-xs font-mono bg-muted rounded-md p-3 overflow-auto max-h-96 whitespace-pre-wrap">{extracted.text}</pre>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/perception/PerceptionPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/perception/PerceptionPage.tsx src/features/perception/PerceptionPage.test.tsx
git commit -m "feat(perception): screenshot/analyze/extract + provider status page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: A2A observability page (agent card + task lookup; read-only)

**Files:**
- Create: `src/features/a2a/A2APage.tsx`, `src/features/a2a/A2APage.test.tsx`

**Context:** The backend exposes **no A2A task list** — only `GET /.well-known/agent.json` and `GET /a2a/tasks/{id}`. The page is therefore: (1) the agent card (capabilities, supported task types, auth scheme), and (2) a task-lookup-by-id with a status card. Do NOT invent a list call. Looked-up tasks are kept in a local recent-lookups list so the operator can re-check several.

**Interfaces:**
- Consumes: `a2aApi.agentCard`, `a2aApi.getTask` (Task 2); `StatusBadge`; `toast`.
- Produces: `export function A2APage(): JSX.Element`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/features/a2a/A2APage.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { A2APage } from './A2APage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><A2APage /></MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

const CARD = {
  agent_id: 'agentverse-platform', name: 'AgentVerse Platform', version: '0.1.0',
  description: 'Agentic OS', endpoint: 'http://x/a2a',
  authentication: { scheme: 'hmac-sha256', header: 'X-A2A-Signature', note: '' },
  capabilities: ['goal_execution', 'audit_log'], supported_task_types: ['goal'],
};

describe('A2APage', () => {
  test('renders the agent card capabilities', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(CARD), { status: 200 }));
    renderPage();
    expect(await screen.findByText('AgentVerse Platform')).toBeInTheDocument();
    expect(screen.getByText('goal_execution')).toBeInTheDocument();
  });

  test('task lookup fetches /a2a/tasks/{id} and shows status', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/.well-known/agent.json')) return new Response(JSON.stringify(CARD), { status: 200 });
      if (url.includes('/a2a/tasks/t1')) return new Response(JSON.stringify({ task_id: 't1', goal: 'Do thing', status: 'complete', result: 'done' }), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByText('AgentVerse Platform');
    await userEvent.type(screen.getByLabelText(/task id/i), 't1');
    await userEvent.click(screen.getByRole('button', { name: /look up/i }));
    expect(await screen.findByText('Do thing')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/features/a2a/A2APage.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/features/a2a/A2APage.tsx
import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { a2aApi, type A2ATask } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { StatusBadge } from '@/components/ui/StatusBadge';

export function A2APage() {
  const [taskId, setTaskId] = useState('');
  const [tasks, setTasks] = useState<A2ATask[]>([]);

  const { data: card, isLoading } = useQuery({
    queryKey: ['a2a-card'],
    queryFn: () => a2aApi.agentCard(),
  });

  const lookupMutation = useMutation({
    mutationFn: (id: string) => a2aApi.getTask(id),
    onSuccess: (task) => setTasks((prev) => [task, ...prev.filter((t) => t.task_id !== task.task_id)]),
    onError: () => toast({ kind: 'error', message: 'Task not found.' }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">A2A Observability</h1>
        <p className="text-muted-foreground text-sm mt-1">Agent-to-agent capability card and task status (read-only)</p>
      </div>

      {/* Agent card */}
      <div className="bg-card border border-border rounded-xl p-5">
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading agent card…</p>
        ) : card ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="font-semibold">{card.name}</h2>
                <p className="text-xs text-muted-foreground">{card.agent_id} · v{card.version}</p>
              </div>
              <code className="text-xs bg-muted rounded px-2 py-1">{card.authentication.scheme}</code>
            </div>
            <p className="text-sm text-muted-foreground">{card.description}</p>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Capabilities</p>
              <div className="flex flex-wrap gap-1.5">
                {card.capabilities.map((c) => <code key={c} className="text-xs bg-muted rounded px-2 py-0.5">{c}</code>)}
              </div>
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Task types</p>
              <div className="flex flex-wrap gap-1.5">
                {card.supported_task_types.map((c) => <code key={c} className="text-xs bg-muted rounded px-2 py-0.5">{c}</code>)}
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Agent card unavailable.</p>
        )}
      </div>

      {/* Task lookup */}
      <div className="bg-card border border-border rounded-xl p-4">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (taskId.trim()) lookupMutation.mutate(taskId.trim()); }}>
          <input
            value={taskId}
            onChange={(e) => setTaskId(e.target.value)}
            placeholder="A2A task id"
            aria-label="Task id"
            className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-background"
          />
          <button type="submit" disabled={!taskId.trim() || lookupMutation.isPending} className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50">
            <Search className="h-4 w-4" /> Look up
          </button>
        </form>
      </div>

      {tasks.length > 0 && (
        <div className="space-y-3">
          {tasks.map((t) => (
            <div key={t.task_id} className="bg-card border border-border rounded-xl p-4">
              <div className="flex items-center justify-between mb-1">
                <code className="text-xs text-muted-foreground">{t.task_id}</code>
                <StatusBadge status={t.status} />
              </div>
              <p className="text-sm">{t.goal}</p>
              {t.result && <p className="text-xs text-muted-foreground mt-1">{t.result}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/features/a2a/A2APage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/features/a2a/A2APage.tsx src/features/a2a/A2APage.test.tsx
git commit -m "feat(a2a): read-only A2A agent-card + task-lookup page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Wire routes + sidebar nav for all 7 pages

**Files:**
- Modify: `src/app/App.tsx`, `src/components/ui/Sidebar.tsx`

**Interfaces:**
- Consumes: the 7 page components from Tasks 3-9.

- [ ] **Step 1: Add the routes in `App.tsx`**

Add imports near the other feature imports:

```tsx
import { MemoryExplorerPage } from "@/features/memory/MemoryExplorerPage";
import { ArtifactsBrowserPage } from "@/features/artifacts/ArtifactsBrowserPage";
import { ToolsPage } from "@/features/tools/ToolsPage";
import { IntegrationsPage } from "@/features/integrations/IntegrationsPage";
import { TrainingExportPage } from "@/features/training/TrainingExportPage";
import { PerceptionPage } from "@/features/perception/PerceptionPage";
import { A2APage } from "@/features/a2a/A2APage";
```

Add the routes inside the authed `<Route path="/">` block (after `rpa/live`):

```tsx
        <Route path="memory" element={<MemoryExplorerPage />} />
        <Route path="artifacts" element={<ArtifactsBrowserPage />} />
        <Route path="tools" element={<ToolsPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="training-export" element={<TrainingExportPage />} />
        <Route path="perception" element={<PerceptionPage />} />
        <Route path="a2a" element={<A2APage />} />
```

- [ ] **Step 2: Add the sidebar nav**

In `src/components/ui/Sidebar.tsx`, add the needed icons to the `lucide-react` import (e.g. `Brain, FileBox, Wrench, Webhook, GraduationCap, Eye, Network`), then add a new "Tooling" section and append A2A/Perception/Training to existing sections. Insert into `NAV_SECTIONS` after the "Platform" section:

```tsx
    {
      heading: "Tooling",
      items: [
        { to: "/tools",           icon: Wrench,        label: "Tools"        },
        { to: "/memory",          icon: Brain,         label: "Memory"       },
        { to: "/artifacts",       icon: FileBox,       label: "Artifacts"    },
        { to: "/integrations",    icon: Webhook,       label: "Integrations" },
        { to: "/perception",      icon: Eye,           label: "Perception"   },
        { to: "/training-export", icon: GraduationCap, label: "Training Export" },
        { to: "/a2a",             icon: Network,       label: "A2A"          },
      ],
    },
```

- [ ] **Step 3: Verify navigation (write/extend e2e)**

Extend `e2e/navigation.spec.ts` to assert each new sidebar link navigates and the heading renders. Mock each backend GET the page fires on mount (`/perception/status`, `/.well-known/agent.json`, `/memory*`, `/artifacts`, `/tools/files`, `/integrations/zapier/goals`) to return `200` with minimal bodies, then for each: `await page.getByRole('link', { name }).click()` and `await expect(page.getByRole('heading', { name })).toBeVisible()`. Reuse `setupAuth(page)`.

```ts
// add to e2e/navigation.spec.ts (inside the existing authed describe)
test('blackout-router pages are reachable from the sidebar', async ({ page }) => {
  await setupAuth(page);
  await page.route('**/perception/status', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ playwright_available: true, vision_available: true, browser_actions: [], image_formats: [] }) }));
  await page.route('**/.well-known/agent.json', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ agent_id: 'p', name: 'AgentVerse Platform', version: '0.1.0', description: '', endpoint: '', authentication: { scheme: 'hmac-sha256', header: 'X-A2A-Signature', note: '' }, capabilities: [], supported_task_types: [] }) }));
  await page.route(/\/(memory|artifacts|integrations\/zapier\/goals|tools\/files)/, (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page.goto('/');
  for (const name of ['Tools', 'Memory', 'Artifacts', 'Integrations', 'Perception', 'A2A']) {
    await page.getByRole('link', { name, exact: true }).click();
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  }
});
```

- [ ] **Step 4: Run typecheck + unit + targeted e2e**

Run: `npm run typecheck && npm run test && npm run test:e2e -- e2e/navigation.spec.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/App.tsx src/components/ui/Sidebar.tsx e2e/navigation.spec.ts
git commit -m "feat(nav): route + sidebar wiring for blackout-router pages

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Phase-4 regression gate

**Files:** none (verification only)

- [ ] **Step 1: Typecheck** — Run: `npm run typecheck` — Expected: no errors.
- [ ] **Step 2: Lint** — Run: `npm run lint` — Expected: no errors (pre-existing warnings acceptable).
- [ ] **Step 3: Full unit suite** — Run: `npm run test` — Expected: all pass; coverage not decreased.
- [ ] **Step 4: E2E smoke (existing + new must pass)** — Run: `npm run test:e2e -- e2e/navigation.spec.ts e2e/goals.spec.ts` — Expected: PASS (Phase 4 only adds routes/nav; existing flows untouched).
- [ ] **Step 5: Tag the phase**

```bash
git tag -a frontend-phase4 -m "Frontend Phase 4: blackout routers → first-class pages"
```

---

## Self-Review

**Spec coverage (against WS-3 / P1-4…P1-8 / P2-1 / P2-2):**
- P1-4 Memory explorer (list/recall/delete/tool-reliability) → Tasks 1, 3. ✅
- P1-5 Artifacts browser (list/preview/download/delete) → Tasks 2, 4. ✅
- P1-6 Tools (code runner/file manager/email) → Tasks 2, 5. ✅
- P1-7 Integrations config → Tasks 2, 6. ✅ (scoped to inbound-webhook config + Zapier delivery — there is no CRUD/test-trigger API; this is the correct, verified shape.)
- P1-8 Training-data export → Tasks 2, 7. ✅
- P2-1 Perception UI → Tasks 2, 8. ✅
- P2-2 A2A observability (read-only) → Tasks 2, 9. ✅ (no list endpoint exists → agent card + task lookup, not a fabricated list.)
- Routing + nav reachability → Task 10; regression gate → Task 11. ✅

**Corrections folded in (vs. naive expectations):**
- `memoryApi.store` posting to `/memory` is **broken** (only `DELETE /memory` exists for that verb = clear-all) → removed in Task 1, not surfaced as a UI action.
- Artifacts have **no byte-download endpoint** → download/preview use `storage_uri` with an http(s) guard (Task 4), not a fabricated `/artifacts/{id}/content`.
- A2A has **no list endpoint** → read-only card + task-lookup (Task 9).
- Integrations are **inbound webhooks only** → config display + copyable endpoints + Zapier delivery poll, not CRUD or "test trigger" buttons.
- Training export is a **StreamingResponse** (not JSON) → `trainingApi.export` uses a raw fetch returning a `Blob` + parsed headers (Task 2), with download via object URL (Task 7).
- SSE: Phase 4 pages are request/response only; the generic SSE hook is **Phase 3's** to provide — this plan creates no SSE hook and notes the dependency.

**Placeholder scan:** none — every code step contains complete, runnable code; every run step has an exact command + expected result.

**Type/name consistency:** namespaces `artifactsApi`/`toolsApi`/`integrationsApi`/`trainingApi`/`perceptionApi`/`a2aApi` and the `memoryApi` additions defined in Tasks 1-2 are the exact names consumed in Tasks 3-9; primitives imported by path (`@/components/ui/Skeleton|EmptyState|StatusBadge`) and `toast` from `@/stores/toast` match Phase 1's created files.

---

## Execution Handoff

Phase 4 depends only on Phase 1 (toast, primitives, the 401/error `request()` layer). Phase 7 (entity detail pages) reuses every namespace landed here — list responses (`memoryApi.list`, `artifactsApi.list`) return full rows so a detail view can render without a second fetch, and `artifactsApi.get` / `a2aApi.getTask` are already single-item fetchers. No backend endpoints were added; if Phase 7 needs a single-item memory GET it must be flagged as backend-owned (none exists today).
