import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { SourceConfig } from '../types';
import { SourceDetailDrawer } from './SourceDetailDrawer';

const SOURCE: SourceConfig = {
  source_id: 'src-42',
  tenant_id: 't',
  name: 'Marketing Confluence',
  family: 'document_store',
  source_type: 'confluence',
  enabled: true,
  sync_mode: 'incremental',
  sync_interval_seconds: 3600,
  connection_config: {},
  cursor_value: '',
  include_patterns: [],
  exclude_patterns: [],
  max_doc_size_bytes: 1000,
  chunking_strategy: 'semantic',
  chunk_size_tokens: 512,
  chunk_overlap_tokens: 64,
  embedding_model: 'voyage-3',
  language_hint: 'en',
  inherit_source_acl: true,
  min_quality_score: 0.3,
  pii_action: 'redact',
  freshness_ttl_seconds: 86400,
  collection_id: 'col-7',
  tags: [],
  last_synced_at: null,
  total_docs_indexed: 1234,
  total_chunks: 5678,
  version: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

interface MockOpts {
  docs?: unknown[];
  syncStatus?: unknown;
}

function mockFetch({ docs = [], syncStatus = { status: 'completed', sync_mode: 'full', docs_indexed: 10, docs_skipped: 1, docs_failed: 0, chunks_created: 30 } }: MockOpts = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/health')) return json({ ok: true, latency_ms: 42, error: null, metadata: {} });
    if (url.includes('/sync/status')) return json(syncStatus);
    if (url.includes('/sync') && method === 'POST') return json({ status: 'queued', job_id: 'job-1' });
    if (url.includes('/ingestion/documents')) return json(docs);
    return json({});
  });
}

function renderDrawer(onClose = vi.fn(), source: SourceConfig = SOURCE) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SourceDetailDrawer source={source} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose };
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => vi.restoreAllMocks());

describe('SourceDetailDrawer', () => {
  test('renders the source header: name, source_type and family label', () => {
    mockFetch();
    renderDrawer();
    expect(screen.getByRole('heading', { name: 'Marketing Confluence' })).toBeInTheDocument();
    expect(screen.getByText('confluence')).toBeInTheDocument();
    // FAMILY_CONFIG.document_store.label
    expect(screen.getByText('Documents & Drive')).toBeInTheDocument();
  });

  test('the Overview tab shows formatted stats from the source', () => {
    mockFetch();
    renderDrawer();
    expect(screen.getByText('1,234')).toBeInTheDocument(); // total_docs_indexed.toLocaleString()
    expect(screen.getByText('5,678')).toBeInTheDocument(); // total_chunks.toLocaleString()
    expect(screen.getByText('Never')).toBeInTheDocument(); // last_synced_at null
    expect(screen.getByText('col-7')).toBeInTheDocument(); // collection_id
  });

  test('the Close button invokes the onClose callback', async () => {
    mockFetch();
    const { onClose } = renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('the Sync Now button fires a POST to /sources/:id/sync', async () => {
    const spy = mockFetch();
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: /Sync source/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => String(u).endsWith('/sources/src-42/sync') && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });

  test('the Documents tab renders indexed documents from the fetch', async () => {
    mockFetch({ docs: [{ id: 'doc-1', title: 'Runbook', chunk_count: 4, quality_score: 0.87, ingested_at: '2026-02-01T00:00:00Z' }] });
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: 'Documents' }));
    expect(await screen.findByText('Runbook')).toBeInTheDocument();
    expect(screen.getByText(/4 chunks · score 0.87/)).toBeInTheDocument();
  });

  test('the Documents tab shows an empty state when there are no documents', async () => {
    mockFetch({ docs: [] });
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: 'Documents' }));
    expect(await screen.findByText('No documents indexed yet.')).toBeInTheDocument();
  });

  test('the History tab renders the latest sync job stats', async () => {
    mockFetch();
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: 'History' }));
    expect(await screen.findByText('completed')).toBeInTheDocument();
    expect(screen.getByText(/full sync/i)).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument(); // docs_indexed
  });

  test('the Settings tab renders the source configuration', async () => {
    mockFetch();
    renderDrawer();
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(await screen.findByText('semantic')).toBeInTheDocument(); // chunking_strategy
    expect(screen.getByText('voyage-3')).toBeInTheDocument(); // embedding_model
    expect(screen.getByText('redact')).toBeInTheDocument(); // pii_action
  });
});
