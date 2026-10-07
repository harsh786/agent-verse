/**
 * Per-collection embedders in the Collections tab: the create form offers the
 * configured embedding models with their widths (GET /knowledge/embedders), the
 * chosen one is sent as `embedding_model`, a model that cannot embed a
 * collection is disabled with its reason, a 422 is shown, and a re-embed can
 * move a collection to another model.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { KnowledgePage } from './KnowledgePage';

const EMBEDDERS = {
  embedders: [
    { key: 'default', provider: 'nvidia', model: 'nvidia/nemotron-3-embed-1b', dimension: 2048, is_default: true,
      available: true, reason: '', source: 'default', chunk_table: 'knowledge_chunks_2048' },
    { key: 'nvidia/nvidia/nemotron-3-embed-1b', provider: 'nvidia', model: 'nvidia/nemotron-3-embed-1b', dimension: 2048,
      is_default: true, available: true, reason: '', source: 'registry', chunk_table: 'knowledge_chunks_2048' },
    { key: 'gemini/gemini-embedding-001', provider: 'gemini', model: 'gemini-embedding-001', dimension: 3072,
      is_default: false, available: true, reason: '', source: 'registry', chunk_table: 'knowledge_chunks_3072' },
    { key: 'onprem/Qwen/Qwen3-Embedding-0.6B', provider: 'onprem', model: 'Qwen/Qwen3-Embedding-0.6B', dimension: 1024,
      is_default: false, available: true, reason: '', source: 'registry', chunk_table: 'knowledge_chunks_1024' },
    { key: 'onprem/acme/embed-wide', provider: 'onprem', model: 'acme/embed-wide', dimension: 4096, is_default: false,
      available: false, reason: 'it produces 4096-d vectors and there is no chunk table of that width', source: 'registry',
      chunk_table: null },
  ],
  default_dimension: 2048,
  supported_dimensions: [768, 1024, 1536, 2048, 3072],
};

const QWEN_COLLECTION = {
  collection_id: 'col-q', name: 'Plant manuals', document_count: 3, embedder: 'Qwen/Qwen3-Embedding-0.6B',
  embedding_dim: 1024, embedding_provider: 'onprem', embedding_model: 'Qwen/Qwen3-Embedding-0.6B',
  embedding_model_key: 'onprem/Qwen/Qwen3-Embedding-0.6B', embedding_binding: 'explicit', chunk_table: 'knowledge_chunks_1024',
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch(opts: { create?: () => Response } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (url.includes('/knowledge/embedders')) return json(EMBEDDERS);
    if (url.includes('/re-embed') && method === 'POST') return json({ status: 'queued', job_id: 'j1' }, 202);
    if (url.includes('/re-embed')) return json({ status: 'never_run' });
    if (url.includes('/stats')) return json({ chunk_count: 100 });
    if (url.endsWith('/knowledge/collections') && method === 'POST')
      return opts.create ? opts.create() : json({ ...QWEN_COLLECTION, collection_id: 'col-new' }, 201);
    if (url.includes('/knowledge/collections')) return json([QWEN_COLLECTION]);
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <KnowledgePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function postBody(spy: ReturnType<typeof mockFetch>, path: string): unknown {
  const call = spy.mock.calls.find(([u, i]) => String(u).includes(path) && (i as RequestInit)?.method === 'POST');
  return call ? JSON.parse(String((call[1] as RequestInit).body)) : undefined;
}

async function openCreateForm() {
  await screen.findByTestId('collections-grid');
  await userEvent.click(screen.getByRole('button', { name: /new collection/i }));
  return screen.findByTestId('collection-embedder-select');
}

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'tenant-key', tenantId: 'tenant-1', plan: 'free', isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => vi.restoreAllMocks());

describe('KnowledgePage – per-collection embedders', () => {
  test('the create form offers the default and every configured model with its width', async () => {
    mockFetch();
    renderPage();
    const select = await openCreateForm();
    const options = within(select).getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual([
      'Default — nvidia/nemotron-3-embed-1b · 2048-d',
      'gemini-embedding-001 (gemini) · 3072-d',
      'Qwen/Qwen3-Embedding-0.6B (onprem) · 1024-d',
      'acme/embed-wide (onprem) · 4096-d — unavailable',
    ]);
    expect(select).toHaveValue('default');
    // The default's registry entry is not offered twice; an unusable width is disabled.
    const wide = options[3] as HTMLOptionElement;
    expect(wide.disabled).toBe(true);
    expect(wide.title).toMatch(/no chunk table/);
    expect(screen.getByTestId('collection-embedder-select-hint')).toHaveTextContent('knowledge_chunks_2048');
  });

  test('creating with a Qwen-1024 model sends embedding_model; the default sends none', async () => {
    const spy = mockFetch();
    renderPage();
    const select = await openCreateForm();
    await userEvent.selectOptions(select, 'onprem/Qwen/Qwen3-Embedding-0.6B');
    expect(screen.getByTestId('collection-embedder-select-hint')).toHaveTextContent('knowledge_chunks_1024');
    await userEvent.type(screen.getByPlaceholderText(/my-knowledge-base/i), 'manuals');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));
    await waitFor(() => expect(postBody(spy, '/knowledge/collections')).toEqual({
      name: 'manuals', embedding_model: 'onprem/Qwen/Qwen3-Embedding-0.6B',
    }));
  });

  test('the default embedder is the server default: only the name is sent', async () => {
    const spy = mockFetch();
    renderPage();
    await openCreateForm();
    await userEvent.type(screen.getByPlaceholderText(/my-knowledge-base/i), 'docs');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));
    await waitFor(() => expect(postBody(spy, '/knowledge/collections')).toEqual({ name: 'docs' }));
  });

  test('a 422 (no chunk table / not configured) is shown to the user', async () => {
    mockFetch({ create: () => json({ detail: 'embedding model gemini/gemini-embedding-001: not configured' }, 422) });
    renderPage();
    const select = await openCreateForm();
    await userEvent.selectOptions(select, 'gemini/gemini-embedding-001');
    await userEvent.type(screen.getByPlaceholderText(/my-knowledge-base/i), 'g');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => /not configured/.test(t.message))).toBe(true),
    );
  });

  test('the card shows the bound model and width; re-embed can move it to another model', async () => {
    const spy = mockFetch();
    renderPage();
    const badge = await screen.findByTestId('collection-embedder-col-q');
    expect(badge).toHaveTextContent('Qwen/Qwen3-Embedding-0.6B · 1024d');
    expect(badge.title).toMatch(/knowledge_chunks_1024/);
    const target = screen.getByTestId('reembed-model-col-q');
    expect(target).toHaveValue('onprem/Qwen/Qwen3-Embedding-0.6B'); // its own model by default
    await userEvent.selectOptions(target, 'gemini/gemini-embedding-001');
    await userEvent.click(screen.getByTestId('reembed-collection-col-q'));
    await waitFor(() => expect(postBody(spy, '/col-q/re-embed')).toEqual({
      embedding_model: 'gemini/gemini-embedding-001',
    }));
    expect(String(vi.mocked(window.confirm).mock.calls[0][0])).toMatch(/gemini-embedding-001 \(gemini\) · 3072-d/);
  });
});
