/** Mirrors backend PLAN_MIN_SCHEDULE_INTERVAL_SECONDS (app/triggers/models.py). */
export const PLAN_MIN_SCHEDULE_INTERVAL_SECONDS: Record<string, number> = {
  free: 900,
  starter: 300,
  professional: 60,
  enterprise: 60,
};

/** Mirrors backend API_POLL_DEFAULT_INTERVAL_SECONDS. */
export const API_POLL_DEFAULT_INTERVAL_SECONDS = 300;

/** The plan's minimum schedule interval; an unknown plan gets FREE's floor. */
export function planMinIntervalSeconds(plan: string | null | undefined): number {
  const key = (plan || 'free').toLowerCase();
  return PLAN_MIN_SCHEDULE_INTERVAL_SECONDS[key] ?? PLAN_MIN_SCHEDULE_INTERVAL_SECONDS.free;
}

/** What the backend uses when the interval is omitted: max(spec default, plan floor). */
export function planAwarePollIntervalSeconds(plan: string | null | undefined): number {
  return Math.max(API_POLL_DEFAULT_INTERVAL_SECONDS, planMinIntervalSeconds(plan));
}
