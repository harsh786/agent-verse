/**
 * Schedule minimums per plan. The backend owns them (Settings
 * SCHEDULE_MIN_INTERVAL_<PLAN>_S, served by GET /schedules/plan-floors); the
 * constants below are only used while that request is loading or if it fails.
 */
import { useEffect, useState } from 'react';
import { apiFetch } from '@/lib/api/client';

export interface PlanFloors {
  planMinIntervalSeconds: Record<string, number>;
  apiPollDefaultIntervalSeconds: number;
}

/** Fallback copy of backend PLAN_MIN_SCHEDULE_INTERVAL_SECONDS defaults. */
export const PLAN_MIN_SCHEDULE_INTERVAL_SECONDS: Record<string, number> = {
  free: 900,
  starter: 300,
  professional: 60,
  enterprise: 60,
};

/** Fallback copy of backend API_POLL_DEFAULT_INTERVAL_SECONDS. */
export const API_POLL_DEFAULT_INTERVAL_SECONDS = 300;

export const DEFAULT_PLAN_FLOORS: PlanFloors = {
  planMinIntervalSeconds: PLAN_MIN_SCHEDULE_INTERVAL_SECONDS,
  apiPollDefaultIntervalSeconds: API_POLL_DEFAULT_INTERVAL_SECONDS,
};

let cached: PlanFloors | null = null;
let inflight: Promise<PlanFloors | null> | null = null;

function parsePlanFloors(body: unknown): PlanFloors | null {
  if (!body || typeof body !== 'object') return null;
  const raw = body as { plan_min_interval_seconds?: unknown; api_poll_default_interval_seconds?: unknown };
  const mins = raw.plan_min_interval_seconds;
  if (!mins || typeof mins !== 'object') return null;
  const parsed: Record<string, number> = {};
  for (const [plan, value] of Object.entries(mins as Record<string, unknown>)) {
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) parsed[plan.toLowerCase()] = value;
  }
  if (!parsed.free) return null;
  const poll = raw.api_poll_default_interval_seconds;
  return {
    planMinIntervalSeconds: parsed,
    apiPollDefaultIntervalSeconds:
      typeof poll === 'number' && Number.isFinite(poll) && poll > 0 ? poll : API_POLL_DEFAULT_INTERVAL_SECONDS,
  };
}

/** Fetch once per page load; a failure is not cached (the next mount retries). */
export function fetchPlanFloors(): Promise<PlanFloors | null> {
  if (cached) return Promise.resolve(cached);
  if (!inflight) {
    inflight = apiFetch<unknown>('/schedules/plan-floors')
      .then((body) => {
        cached = parsePlanFloors(body);
        return cached;
      })
      .catch(() => null)
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/** The backend's plan floors; the built-in defaults until they have loaded. */
export function usePlanFloors(): PlanFloors {
  const [floors, setFloors] = useState<PlanFloors>(cached ?? DEFAULT_PLAN_FLOORS);
  useEffect(() => {
    let active = true;
    void fetchPlanFloors().then((loaded) => {
      if (active && loaded) setFloors(loaded);
    });
    return () => {
      active = false;
    };
  }, []);
  return floors;
}

export function resetPlanFloorsCacheForTests(): void {
  cached = null;
  inflight = null;
}

/** The plan's minimum schedule interval; an unknown plan gets FREE's floor. */
export function planMinIntervalSeconds(
  plan: string | null | undefined,
  floors: PlanFloors = DEFAULT_PLAN_FLOORS,
): number {
  const key = (plan || 'free').toLowerCase();
  const table = floors.planMinIntervalSeconds;
  return table[key] ?? table.free ?? PLAN_MIN_SCHEDULE_INTERVAL_SECONDS.free;
}

/** What the backend uses when the interval is omitted: max(spec default, plan floor). */
export function planAwarePollIntervalSeconds(
  plan: string | null | undefined,
  floors: PlanFloors = DEFAULT_PLAN_FLOORS,
): number {
  return Math.max(floors.apiPollDefaultIntervalSeconds, planMinIntervalSeconds(plan, floors));
}
