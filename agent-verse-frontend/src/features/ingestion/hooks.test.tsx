import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import {
  useSources,
  useSource,
  useDocuments,
  useIngestionQuota,
  useCreateSource,
  useTriggerSync,
  useDeleteSource,
} from './hooks';

const SOURCE = {
  source_id: 'src-1',
  tenant_id: 't',
  name: 'Prod S3 Bucket',
  family: 'object_storage',
  source_type: 's3',
  enabled: true,
  sync_mode: 'incremental',
  total_docs_indexed: 42,
  total_chunks: 128,
};

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

function mockFetch(handler?: (url: string, method: string) => Response) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (handler) {
      const custom = handler(url, method);
      if (custom) return custom;
    }
    // Default happy-path routing by path + method.
    if (url.includes('/ingestion/documents')) return json([{ id: 'd1', title: 'Doc A', chunk_count: 3, quality_score: 0.9, ingested_at: '2026-01-01T00:00:00Z' }]);
    if (url.includes('/ingestion/quota')) return json({ tenant_id: 't', plan: 'free', sources_used: 1, sources_limit: 5, docs_used: 42, docs_limit: null, tokens_used_month: 10, tokens_limit_month: null, cost_usd_month: 0.1 });
    if (url.match(/\/sources\/[^/]+\/sync$/) && method === 'POST') return json({ status: 'queued', job_id: 'job-9' });
    if (url.match(/\/sources\/[^/]+$/) && method === 'DELETE') return json({});
    if (url.endsWith('/sources') && method === 'POST') return json({ ...SOURCE, source_id: 'src-new', name: 'Created' });
    if (url.match(/\/sources\/[^/]+$/) && method === 'GET') return json(SOURCE);
    if (url.endsWith('/sources') && method === 'GET') return json([SOURCE]);
    return json({});
  });
}

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => vi.restoreAllMocks());

describe('ingestion hooks', () => {
  test('useSources resolves the list of sources from GET /sources', async () => {
    mockFetch();
    const { result } = renderHook(() => useSources(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data?.[0].name).toBe('Prod S3 Bucket');
  });

  test('useSource fetches a single source by id', async () => {
    const spy = mockFetch();
    const { result } = renderHook(() => useSource('src-1'), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.source_id).toBe('src-1');
    expect(spy.mock.calls.some(([u]) => String(u).endsWith('/sources/src-1'))).toBe(true);
  });

  test('useSource is disabled (no fetch) when the id is empty', async () => {
    const spy = mockFetch();
    const { result } = renderHook(() => useSource(''), { wrapper: makeWrapper() });
    // enabled:!!sourceId → query never runs; stays in pending/idle without a fetch.
    expect(result.current.fetchStatus).toBe('idle');
    expect(spy).not.toHaveBeenCalled();
  });

  test('useDocuments builds the documents URL with source_id + limit', async () => {
    const spy = mockFetch();
    const { result } = renderHook(() => useDocuments('src-1'), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.[0].title).toBe('Doc A');
    const called = spy.mock.calls.find(([u]) => String(u).includes('/ingestion/documents'));
    expect(String(called?.[0])).toContain('source_id=src-1');
    expect(String(called?.[0])).toContain('limit=50');
  });

  test('useIngestionQuota resolves the quota payload', async () => {
    mockFetch();
    const { result } = renderHook(() => useIngestionQuota(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.sources_limit).toBe(5);
  });

  test('useCreateSource POSTs the new source body to /sources', async () => {
    const spy = mockFetch();
    const { result } = renderHook(() => useCreateSource(), { wrapper: makeWrapper() });
    await result.current.mutateAsync({ name: 'New Source', source_type: 's3' });
    const post = spy.mock.calls.find(
      ([u, i]) => String(u).endsWith('/sources') && (i as RequestInit)?.method === 'POST',
    );
    expect(post).toBeDefined();
    expect(JSON.parse((post?.[1] as RequestInit).body as string)).toMatchObject({ name: 'New Source', source_type: 's3' });
  });

  test('useTriggerSync POSTs to /sources/:id/sync', async () => {
    const spy = mockFetch();
    const { result } = renderHook(() => useTriggerSync(), { wrapper: makeWrapper() });
    const res = await result.current.mutateAsync('src-1');
    expect(res.status).toBe('queued');
    expect(
      spy.mock.calls.some(([u, i]) => String(u).endsWith('/sources/src-1/sync') && (i as RequestInit)?.method === 'POST'),
    ).toBe(true);
  });

  test('useDeleteSource DELETEs /sources/:id', async () => {
    const spy = mockFetch();
    const { result } = renderHook(() => useDeleteSource(), { wrapper: makeWrapper() });
    await result.current.mutateAsync('src-1');
    expect(
      spy.mock.calls.some(([u, i]) => String(u).endsWith('/sources/src-1') && (i as RequestInit)?.method === 'DELETE'),
    ).toBe(true);
  });

  test('a 500 on GET /sources surfaces as an error state', async () => {
    mockFetch((url, method) => (url.endsWith('/sources') && method === 'GET' ? json({ error: { message: 'boom' } }, 500) : (undefined as unknown as Response)));
    const { result } = renderHook(() => useSources(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
