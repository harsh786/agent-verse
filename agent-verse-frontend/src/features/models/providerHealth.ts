/**
 * Provider health as the backend reports it (GET /models/health, model.health).
 *
 * `is_healthy` is `null` until a real call or an operator probe has checked the
 * provider — that is "unverified", never green (it used to default to healthy).
 */
export type ProviderHealthState = 'healthy' | 'unhealthy' | 'unverified';

export function providerHealthState(h: { is_healthy?: boolean | null } | null | undefined): ProviderHealthState {
  if (h == null || h.is_healthy == null) return 'unverified';
  return h.is_healthy ? 'healthy' : 'unhealthy';
}
