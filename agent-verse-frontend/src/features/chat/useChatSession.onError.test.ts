/**
 * Regression guard: a failed session mutation must surface a toast, not fail
 * silently (the "New Chat does nothing" bug was a silent 401 with no error path).
 */
import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactNode } from 'react';

const toastMock = vi.fn();
vi.mock('@/stores/toast', () => ({ toast: (opts: unknown) => toastMock(opts) }));
vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    createSession: () => Promise.reject(new Error('HTTP 401: Unauthorized')),
    deleteSession: () => Promise.reject(new Error('HTTP 500')),
  },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return createElement(QueryClientProvider, { client: qc }, children);
}

import { useCreateSession, useDeleteSession } from './hooks/useChatSession';

describe('chat session mutation error surfacing', () => {
  it('toasts an error when createSession fails', async () => {
    toastMock.mockClear();
    const { result } = renderHook(() => useCreateSession(), { wrapper });
    result.current.mutate({ title: 'New Chat' });
    await waitFor(() => expect(toastMock).toHaveBeenCalled());
    const arg = toastMock.mock.calls[0][0] as { kind: string; message: string };
    expect(arg.kind).toBe('error');
    expect(arg.message).toContain('Create chat failed');
    expect(arg.message).toContain('401');
  });

  it('toasts an error when deleteSession fails', async () => {
    toastMock.mockClear();
    const { result } = renderHook(() => useDeleteSession(), { wrapper });
    result.current.mutate('s1');
    await waitFor(() => expect(toastMock).toHaveBeenCalled());
    expect((toastMock.mock.calls[0][0] as { kind: string }).kind).toBe('error');
  });
});
