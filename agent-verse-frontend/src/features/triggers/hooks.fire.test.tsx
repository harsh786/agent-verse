import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { useFireTriggerNow } from './hooks';

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  useToastStore.setState({ toasts: [] });
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('useFireTriggerNow', () => {
  // TRG-12: a role without fire rights gets the server's 403 reason.
  test('surfaces a 403 role denial as an error toast', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ detail: "Role 'viewer' is not permitted to perform 'fire' on triggers" }),
        { status: 403, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const { result } = renderHook(() => useFireTriggerNow(), { wrapper });
    act(() => result.current.mutate({ scheduleId: 'tr1' }));
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(
      useToastStore.getState().toasts.some(
        (t) => t.kind === 'error' && /not permitted to perform 'fire'/.test(t.message),
      ),
    ).toBe(true);
  });
});
