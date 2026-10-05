/** Source health polling (mongo re-audit C8). */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { HEALTH_POLL_BASE_MS, HEALTH_POLL_MAX_MS, healthPollInterval, INGESTION_KEYS, useSourceHealth } from './hooks';

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

function setup(defaultRetry = 3) {
  // The app's QueryClient retries queries up to 3 times (main.tsx); the
  // health query must opt out of that.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: defaultRetry, retryDelay: 0 } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  return { qc, wrapper };
}

const observerOptions = (qc: QueryClient, id: string) =>
  qc.getQueryCache().find({ queryKey: INGESTION_KEYS.health(id) })!.observers[0].options;

beforeEach(() => {
  vi.restoreAllMocks();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

describe('C8 health polling', () => {
  test('backs off exponentially after consecutive failures, capped', () => {
    expect(healthPollInterval(0)).toBe(HEALTH_POLL_BASE_MS);
    expect(healthPollInterval(1)).toBe(2 * HEALTH_POLL_BASE_MS);
    expect(healthPollInterval(2)).toBe(4 * HEALTH_POLL_BASE_MS);
    expect(healthPollInterval(50)).toBe(HEALTH_POLL_MAX_MS);
  });

  test('a failing health request is not retried (no 3 immediate reconnects)', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ detail: 'down' }, 503));
    const { wrapper } = setup();
    const { result } = renderHook(() => useSourceHealth('s-fail', true, { poll: true }), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(spy.mock.calls.filter(([u]) => String(u).includes('/health'))).toHaveLength(1);
  });

  test('without poll (a list card) there is no interval and no refetch on focus', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ ok: true, latency_ms: 1, error: null, metadata: {} }));
    const { qc, wrapper } = setup();
    const { result } = renderHook(() => useSourceHealth('s-card'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const opts = observerOptions(qc, 's-card');
    expect(opts.refetchInterval).toBeFalsy();
    expect(opts.refetchOnWindowFocus).toBe(false);
  });

  test('with poll (the open drawer) it polls only in the foreground, backing off while the source fails', async () => {
    let ok = false;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ ok, latency_ms: 1, error: ok ? null : 'refused', metadata: {} }));
    const { qc, wrapper } = setup();
    const { result } = renderHook(() => useSourceHealth('s-drawer', true, { poll: true }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const opts = observerOptions(qc, 's-drawer');
    expect(opts.refetchIntervalInBackground).toBe(false);
    const query = qc.getQueryCache().find({ queryKey: INGESTION_KEYS.health('s-drawer') })!;
    const interval = opts.refetchInterval as (q: typeof query) => number | false;
    expect(interval(query)).toBe(2 * HEALTH_POLL_BASE_MS); // one failure -> doubled

    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    expect(interval(query)).toBe(false); // hidden tab: no polling
    visibility.mockRestore();

    ok = true;
    await result.current.refetch();
    expect(interval(query)).toBe(HEALTH_POLL_BASE_MS); // recovered -> base interval
  });
});
