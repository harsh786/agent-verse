/**
 * Unit tests for useChatSession hooks.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactNode } from 'react';

// Mock chatApi
vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    listSessions: () =>
      Promise.resolve({
        sessions: [
          {
            id: 's1',
            title: 'Session 1',
            pinned: false,
            ttl_days: null,
            system_prompt: null,
            agent_id: null,
            folder_id: null,
            show_reasoning: false,
            proactive_suggestions: true,
            preferred_model: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            tenant_id: 't1',
          },
        ],
      }),
    listFolders: () => Promise.resolve({ folders: [{ id: 'f1', name: 'Work', color: '#ff0000' }] }),
    createSession: () =>
      Promise.resolve({ id: 'new-s', title: 'New Chat', pinned: false, tenant_id: 't1', created_at: new Date().toISOString(), updated_at: new Date().toISOString() }),
    deleteSession: () => Promise.resolve(),
    pinSession: (id: string, pinned: boolean) =>
      Promise.resolve({ id, pinned, title: 'S1', tenant_id: 't1', created_at: new Date().toISOString(), updated_at: new Date().toISOString() }),
    createFolder: (name: string, color: string) =>
      Promise.resolve({ id: 'new-f', name, color, tenant_id: 't1' }),
  },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client: qc }, children);
}

import { useSessions, useFolders, useCreateSession, useDeleteSession, usePinSession } from './hooks/useChatSession';

describe('useSessions', () => {
  it('returns list of sessions', async () => {
    const { result } = renderHook(() => useSessions(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data?.[0].title).toBe('Session 1');
  });
});

describe('useFolders', () => {
  it('returns list of folders', async () => {
    const { result } = renderHook(() => useFolders(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.[0].name).toBe('Work');
  });
});

describe('useCreateSession', () => {
  it('creates a session', async () => {
    const { result } = renderHook(() => useCreateSession(), { wrapper });
    const session = await result.current.mutateAsync({ title: 'New' });
    expect(session.id).toBe('new-s');
  });
});

describe('useDeleteSession', () => {
  it('deletes a session', async () => {
    const { result } = renderHook(() => useDeleteSession(), { wrapper });
    await expect(result.current.mutateAsync('s1')).resolves.toBeUndefined();
  });
});

describe('usePinSession', () => {
  it('pins a session', async () => {
    const { result } = renderHook(() => usePinSession(), { wrapper });
    const updated = await result.current.mutateAsync({ sessionId: 's1', pinned: true });
    expect(updated.pinned).toBe(true);
  });
});
