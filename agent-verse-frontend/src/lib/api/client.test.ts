import { afterEach, expect, test, vi } from 'vitest';
import { analyticsApi, schedulesApi, agentsApi } from '@/lib/api/client';

afterEach(() => vi.restoreAllMocks());

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
