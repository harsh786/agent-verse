import { afterEach, expect, test, vi, beforeEach } from 'vitest';
import {
  analyticsApi, schedulesApi, agentsApi, goalsApi, ApiError,
  memoryApi, artifactsApi, toolsApi, perceptionApi, a2aApi, integrationsApi, trainingApi,
} from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';

afterEach(() => vi.restoreAllMocks());

beforeEach(() => {
  useAuthStore.setState({ apiKey: '', tenantId: '', plan: '', isAuthenticated: false });
});

function mockOk(body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
}

test('getCostMetrics calls /analytics/costs (plural)', async () => {
  const f = mockOk({ total_cost_usd: 0, cost_by_day: [], cost_by_model: {}, daily_budget_usd: 0, budget_utilization: 0 });
  await analyticsApi.getCostMetrics(30);
  expect(String(f.mock.calls[0][0])).toContain('/analytics/costs?days=30');
});

test('createNl calls /nl/schedule', async () => {
  const f = mockOk({ schedule_id: 's1', name: 'n', goal_template: 'g', enabled: true, created_at: '' });
  await schedulesApi.createNl('every day at 9am');
  expect(String(f.mock.calls[0][0])).toContain('/nl/schedule');
});

test('createNl posts command+autorun to /agents/create', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ agent_id: 'a1', name: 'X', autonomy_mode: 'bounded-autonomous' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  await agentsApi.createNl('make a triage bot', false);
  const [url, init] = f.mock.calls[0];
  expect(String(url)).toContain('/agents/create');
  expect(init?.method).toBe('POST');
  expect(JSON.parse(String(init?.body))).toEqual({ command: 'make a triage bot', autorun: false });
});

test('401 logs out and surfaces a session toast', async () => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ error: { message: 'unauthorized' } }), { status: 401, headers: { 'Content-Type': 'application/json' } })
  );
  await expect(goalsApi.list()).rejects.toBeInstanceOf(ApiError);
  expect(useAuthStore.getState().isAuthenticated).toBe(false);
});

// ── memoryApi ──────────────────────────────────────────────────────────────────

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

// ── artifactsApi ──────────────────────────────────────────────────────────────

test('artifactsApi.list builds /artifacts query', async () => {
  const f = mockOk([{ id: 'a1', name: 'out.txt', artifact_type: 'file', storage_uri: 's3://x', content_type: 'text/plain', size_bytes: 5, goal_id: 'g1', created_at: '' }]);
  await artifactsApi.list({ goalId: 'g1', artifactType: 'file', limit: 10 });
  const url = String(f.mock.calls[0][0]);
  expect(url).toContain('/artifacts?');
  expect(url).toContain('goal_id=g1');
  expect(url).toContain('artifact_type=file');
  expect(url).toContain('limit=10');
});

// ── toolsApi ──────────────────────────────────────────────────────────────────

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

// ── perceptionApi ──────────────────────────────────────────────────────────────

test('perceptionApi.screenshot posts url + full_page', async () => {
  const f = mockOk({ success: true, url: 'http://x', screenshot_b64: 'AAA', error: null });
  await perceptionApi.screenshot('http://x', true);
  expect(String(f.mock.calls[0][0])).toContain('/perception/screenshot');
  expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body))).toEqual({ url: 'http://x', full_page: true });
});

// ── a2aApi ────────────────────────────────────────────────────────────────────

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

// ── integrationsApi ──────────────────────────────────────────────────────────

test('integrationsApi.zapierCompletedGoals fetches /integrations/zapier/goals', async () => {
  const f = mockOk([{ goal_id: 'g1', status: 'complete' }]);
  await integrationsApi.zapierCompletedGoals();
  expect(String(f.mock.calls[0][0])).toContain('/integrations/zapier/goals');
});

// ── trainingApi ──────────────────────────────────────────────────────────────

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
