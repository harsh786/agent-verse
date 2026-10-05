/** SourceDetailDrawer for a MongoDB source (mongo re-audit B3, B6, C8). */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { SourceConfig } from '../types';
import { SourceDetailDrawer } from './SourceDetailDrawer';

const MONGO: SourceConfig = {
  source_id: 'src-m1', tenant_id: 't', name: 'Orders', family: 'nosql_database', source_type: 'mongodb',
  enabled: true, sync_mode: 'incremental', sync_interval_seconds: 3600,
  // As GET /sources returns it: secrets masked with "********".
  connection_config: { uri: '********', database: 'orders', username: 'alice', password: '********', collections_csv: 'orders_v2' },
  cursor_value: '', include_patterns: [], exclude_patterns: [], max_doc_size_bytes: 1000,
  chunking_strategy: 'semantic', chunk_size_tokens: 512, chunk_overlap_tokens: 64, embedding_model: 'voyage-3',
  language_hint: 'en', inherit_source_acl: true, min_quality_score: 0.3, pii_action: 'redact',
  freshness_ttl_seconds: 86400, collection_id: 'col-7', tags: [], last_synced_at: null,
  total_docs_indexed: 1, total_chunks: 2, version: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

type Over = { health?: () => Response; syncStatus?: unknown; patch?: () => Response };

function mockFetch(over: Over = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/health')) return over.health ? over.health() : json({ ok: true, latency_ms: 5, error: null, metadata: {} });
    if (url.includes('/sync/status')) return json(over.syncStatus ?? { status: 'completed', sync_mode: 'full', docs_indexed: 1 });
    if (method === 'PATCH') return over.patch ? over.patch() : json({ ...MONGO });
    if (url.includes('/ingestion/documents')) return json([]);
    return json({});
  });
}

function renderDrawer(source: SourceConfig = MONGO) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SourceDetailDrawer source={source} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
  return { qc };
}

const patchBodies = (spy: ReturnType<typeof mockFetch>) =>
  spy.mock.calls
    .filter(([, i]) => (i as RequestInit | undefined)?.method === 'PATCH')
    .map(([u, i]) => ({ url: String(u), body: JSON.parse(String((i as RequestInit).body)) }));

beforeEach(() => {
  vi.restoreAllMocks();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

async function openEditor() {
  await userEvent.click(screen.getByRole('button', { name: /^settings$/i }));
  await userEvent.click(screen.getByRole('button', { name: /edit connection/i }));
}

describe('B3 edit connection_config with a masked round-trip', () => {
  test('secrets show as masked placeholders; untouched secrets are sent back as the mask', async () => {
    const spy = mockFetch();
    renderDrawer();
    await openEditor();
    const uri = screen.getByLabelText('Connection URI');
    expect(uri).toHaveAttribute('type', 'password');
    expect(uri).toHaveValue('');
    expect(uri).toHaveAttribute('placeholder', expect.stringMatching(/saved/i));
    const db = screen.getByDisplayValue('orders', { exact: true });
    await userEvent.clear(db);
    await userEvent.type(db, 'sales');
    await userEvent.click(screen.getByRole('button', { name: /save connection/i }));
    await waitFor(() => expect(patchBodies(spy)).toHaveLength(1));
    const { url, body } = patchBodies(spy)[0];
    expect(url).toMatch(/\/sources\/src-m1$/);
    expect(body).toEqual({ connection_config: { uri: '********', database: 'sales', username: 'alice', password: '********', collections_csv: 'orders_v2' } });
  });

  test('credential rotation: a typed secret replaces the mask, the rest stay masked', async () => {
    const spy = mockFetch();
    renderDrawer();
    await openEditor();
    await userEvent.type(screen.getByLabelText('Password'), 'N3wPassw0rd');
    await userEvent.click(screen.getByRole('button', { name: /save connection/i }));
    await waitFor(() => expect(patchBodies(spy)).toHaveLength(1));
    expect(patchBodies(spy)[0].body.connection_config).toMatchObject({ uri: '********', password: 'N3wPassw0rd' });
    expect(await screen.findByText(/connection saved/i)).toBeInTheDocument();
  });

  test('a refused PATCH shows its error and keeps the editor open', async () => {
    mockFetch({ patch: () => json({ detail: [{ loc: ['body', 'connection_config', 'uri'], msg: 'Value error, invalid MongoDB URI' }] }, 422) });
    renderDrawer();
    await openEditor();
    await userEvent.type(screen.getByLabelText('Connection URI'), 'not-a-uri');
    await userEvent.click(screen.getByRole('button', { name: /save connection/i }));
    expect(await screen.findByText('invalid MongoDB URI')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save connection/i })).toBeInTheDocument();
  });

  test('Cancel discards the draft without a request', async () => {
    const spy = mockFetch();
    renderDrawer();
    await openEditor();
    await userEvent.type(screen.getByLabelText('Password'), 'x');
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(patchBodies(spy)).toHaveLength(0);
    expect(screen.getByRole('button', { name: /edit connection/i })).toBeInTheDocument();
  });
});

void within;
