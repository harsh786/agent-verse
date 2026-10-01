/** KB-15: cancel a running sync and reindex a source from the detail drawer. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { SourceConfig } from '../types';
import { SourceDetailDrawer } from './SourceDetailDrawer';

const SOURCE = {
  source_id: 'src-42', tenant_id: 't', name: 'Docs', family: 'document_store', source_type: 'confluence',
  enabled: true, sync_mode: 'incremental', sync_interval_seconds: 3600, connection_config: {}, cursor_value: '',
  include_patterns: [], exclude_patterns: [], max_doc_size_bytes: 1000, chunking_strategy: 'semantic',
  chunk_size_tokens: 512, chunk_overlap_tokens: 64, embedding_model: 'voyage-3', language_hint: 'en',
  inherit_source_acl: true, min_quality_score: 0.3, pii_action: 'redact', freshness_ttl_seconds: 86400,
  collection_id: 'col-7', tags: [], last_synced_at: null, total_docs_indexed: 1, total_chunks: 2, version: 1,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
} as SourceConfig;

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

function mockFetch(syncStatus: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/health')) return json({ ok: true, latency_ms: 1, error: null, metadata: {} });
    if (url.includes('/sync/status')) return json(syncStatus);
    if (url.includes('/sync/cancel') && method === 'POST') return json({ status: 'cancelling', job_id: 'j' }, 202);
    if (url.includes('/reindex') && method === 'POST') return json({ status: 'queued', job_id: 'j' }, 202);
    if (url.includes('/ingestion/documents')) return json([]);
    return json({});
  });
}

function renderDrawer() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SourceDetailDrawer source={SOURCE} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

const posted = (spy: ReturnType<typeof mockFetch>, suffix: string) =>
  spy.mock.calls.some(([u, i]) => String(u).endsWith(suffix) && (i as RequestInit)?.method === 'POST');

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('SourceDetailDrawer – sync operations', () => {
  test('a running sync can be cancelled', async () => {
    const spy = mockFetch({ status: 'running', sync_mode: 'incremental' });
    renderDrawer();
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel sync' }));
    await waitFor(() => expect(posted(spy, '/sources/src-42/sync/cancel')).toBe(true));
  });

  test('reindex asks for confirmation, then queues it', async () => {
    const spy = mockFetch({ status: 'completed', sync_mode: 'full' });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderDrawer();
    const button = await screen.findByRole('button', { name: 'Reindex source' });
    await userEvent.click(button);
    expect(posted(spy, '/sources/src-42/reindex')).toBe(false);
    await userEvent.click(button);
    await waitFor(() => expect(posted(spy, '/sources/src-42/reindex')).toBe(true));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  test('KB-43: a 409 legal-hold refusal of a reindex is shown', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/health')) return json({ ok: true, latency_ms: 1, error: null, metadata: {} });
      if (url.includes('/sync/status')) return json({ status: 'completed' });
      if (url.includes('/reindex') && method === 'POST')
        return json({ detail: "The source's collection is under legal hold; reindex would delete held data" }, 409);
      return json([]);
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderDrawer();
    await userEvent.click(await screen.findByRole('button', { name: 'Reindex source' }));
    expect(await screen.findByText(/reindex refused: the collection is under legal hold/i)).toBeInTheDocument();
  });
});
