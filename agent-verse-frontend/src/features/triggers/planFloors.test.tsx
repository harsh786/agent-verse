/**
 * Plan floors come from GET /schedules/plan-floors (Settings-driven on the
 * backend); the built-in constants are only the loading/failure fallback.
 */
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { DataFamilyForm } from './components/families/DataFamilyForm';
import { TimeFamilyForm } from './components/families/TimeFamilyForm';
import {
  DEFAULT_PLAN_FLOORS,
  planAwarePollIntervalSeconds,
  planMinIntervalSeconds,
  resetPlanFloorsCacheForTests,
} from './planFloors';

function serveFloors(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    if (String(input).includes('/schedules/plan-floors'))
      return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

const OVERRIDDEN = {
  plan_min_interval_seconds: { free: 120, starter: 300, professional: 60, enterprise: 600 },
  api_poll_default_interval_seconds: 300,
};

beforeEach(() => resetPlanFloorsCacheForTests());
afterEach(() => {
  vi.restoreAllMocks();
  useAuthStore.setState({ plan: '' });
});

describe('planFloors', () => {
  test('pure helpers use the given floors, defaults otherwise', () => {
    expect(planMinIntervalSeconds('free')).toBe(900);
    expect(planMinIntervalSeconds('unknown')).toBe(900);
    const floors = { ...DEFAULT_PLAN_FLOORS, planMinIntervalSeconds: OVERRIDDEN.plan_min_interval_seconds };
    expect(planMinIntervalSeconds('free', floors)).toBe(120);
    expect(planAwarePollIntervalSeconds('enterprise', floors)).toBe(600);
  });

  test('the cron hint shows the backend floor once loaded (env override reaches the UI)', async () => {
    const spy = serveFloors(OVERRIDDEN);
    useAuthStore.setState({ plan: 'free' });
    render(<TimeFamilyForm triggerType="cron" value={{}} onChange={vi.fn()} />);
    expect(await screen.findByText(/free plan runs a schedule at most every 2 min/i)).toBeInTheDocument();
    expect(spy.mock.calls.some(([u]) => String(u).includes('/schedules/plan-floors'))).toBe(true);
  });

  test('the api_poll pre-fill follows the backend floor', async () => {
    serveFloors(OVERRIDDEN);
    useAuthStore.setState({ plan: 'enterprise' });
    render(<DataFamilyForm triggerType="api_poll" value={{}} onChange={vi.fn()} />);
    expect(await screen.findByText(/enterprise plan polls at most every 10 min; left unchanged, 600s/)).toBeInTheDocument();
  });

  test('a failed or malformed response keeps the built-in floors', async () => {
    const spy = serveFloors({ tables: ['x'] });
    useAuthStore.setState({ plan: 'free' });
    render(<TimeFamilyForm triggerType="cron" value={{}} onChange={vi.fn()} />);
    await vi.waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('/schedules/plan-floors'))).toBe(true),
    );
    expect(screen.getByText(/free plan runs a schedule at most every 15 min/i)).toBeInTheDocument();
  });
});
