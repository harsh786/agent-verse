import { afterEach, expect, test, vi } from 'vitest';
import { analyticsApi, schedulesApi } from '@/lib/api/client';

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
