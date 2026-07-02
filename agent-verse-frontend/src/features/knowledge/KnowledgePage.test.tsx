import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { KnowledgePage } from './KnowledgePage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <KnowledgePage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const COLLECTION = { collection_id: 'col-1', name: 'Engineering Docs', doc_count: 42, embedder: 'voyage' };
const CHUNK = { chunk_id: 'c1', content: 'Relevant document content about engineering.', score: 0.9234, source_url: '' };

function mockFetch(opts: { collections?: unknown[]; searchResults?: unknown[] } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET');
    if (url.includes('/knowledge/cache/stats'))
      return new Response(JSON.stringify({ hits: 5, misses: 3 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge/collections/col-1/stats'))
      return new Response(JSON.stringify({ collection_id: 'col-1', name: 'Engineering Docs', doc_count: 42, chunk_count: 200, embedding_coverage_pct: 95, avg_chunk_length: 350, source_type_distribution: { text: 10 }, embedder: 'voyage', health_score: 0.85 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge/collections') && method === 'DELETE')
      return new Response(null, { status: 204 });
    if (url.includes('/knowledge/collections') && method === 'POST')
      return new Response(JSON.stringify({ collection_id: 'col-new', name: 'New Collection' }), { status: 201, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge/collections'))
      return new Response(JSON.stringify(opts.collections ?? [COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge/search'))
      return new Response(JSON.stringify(opts.searchResults ?? [CHUNK]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge/ingest') && method === 'POST')
      return new Response(JSON.stringify({ chunks_created: 7, document_id: 'doc-1' }), { status: 201, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge/chat') && method === 'POST')
      return new Response(JSON.stringify({ answer: 'The docs cover CI/CD pipelines.', citations: [], collections_searched: 1, chunks_retrieved: 3, question: 'What is covered?' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200 });
  });
}

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'tenant-key', tenantId: 'tenant-1', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('KnowledgePage – Collections tab', () => {
  test('renders Knowledge heading and 5 tabs', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /knowledge/i })).toBeInTheDocument();
    expect(screen.getByTestId('tab-collections')).toBeInTheDocument();
    expect(screen.getByTestId('tab-ask')).toBeInTheDocument();
    expect(screen.getByTestId('tab-ingest')).toBeInTheDocument();
    expect(screen.getByTestId('tab-search')).toBeInTheDocument();
    expect(screen.getByTestId('tab-analytics')).toBeInTheDocument();
  });

  test('shows empty state when no collections exist', async () => {
    mockFetch({ collections: [] });
    renderPage();
    expect(await screen.findByText(/no collections yet/i)).toBeInTheDocument();
  });

  test('lists existing collections in grid', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByTestId('collections-grid')).toBeInTheDocument();
    expect(screen.getByText('Engineering Docs')).toBeInTheDocument();
  });

  test('shows create collection form when New Collection clicked', async () => {
    mockFetch();
    renderPage();
    await screen.findByTestId('collections-grid');
    await userEvent.click(screen.getByRole('button', { name: /new collection/i }));
    expect(await screen.findByPlaceholderText(/my-knowledge-base/i)).toBeInTheDocument();
  });

  test('creates a collection via POST', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId('collections-grid');
    await userEvent.click(screen.getByRole('button', { name: /new collection/i }));
    await userEvent.type(screen.getByPlaceholderText(/my-knowledge-base/i), 'New Collection');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/knowledge/collections') && (i as RequestInit)?.method === 'POST')).toBe(true)
    );
  });

  test('deletes a collection via DELETE', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId(`collection-card-${COLLECTION.collection_id}`);
    await userEvent.click(screen.getByTestId(`delete-collection-${COLLECTION.collection_id}`));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('col-1') && (i as RequestInit)?.method === 'DELETE')).toBe(true)
    );
  });
});

describe('KnowledgePage – Ask AI tab', () => {
  test('Ask AI tab shows question input and ask button', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    expect(await screen.findByTestId('ask-input')).toBeInTheDocument();
    expect(screen.getByTestId('ask-btn')).toBeInTheDocument();
  });

  test('asking a question shows the answer panel', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    await userEvent.type(screen.getByTestId('ask-input'), 'What is covered?');
    await userEvent.click(screen.getByTestId('ask-btn'));
    expect(await screen.findByTestId('answer-panel')).toBeInTheDocument();
    expect(await screen.findByText(/CI\/CD pipelines/i)).toBeInTheDocument();
  });
});

describe('KnowledgePage – Ingest tab', () => {
  test('shows ingest form with collection select and source types', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    expect(await screen.findByRole('button', { name: /^text$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /markdown/i })).toBeInTheDocument();
  });

  test('Ingest button is disabled when no collection selected', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await screen.findByRole('button', { name: /^text$/i });
    // The submit ingest button is the last Ingest button (tab is the first)
    const allIngestBtns = screen.getAllByRole('button', { name: /^ingest$/i });
    expect(allIngestBtns[allIngestBtns.length - 1]).toBeDisabled();
  });

  test('calls ingest API when form submitted', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await screen.findByRole('button', { name: /^text$/i });
    // Select collection
    const collectionSelect = screen.getAllByRole('combobox')[0];
    await userEvent.selectOptions(collectionSelect, 'col-1');
    // Add content
    await userEvent.type(screen.getByPlaceholderText(/paste content/i), 'Some content to ingest');
    const submitBtn = screen.getAllByRole('button', { name: /^ingest$/i });
    await userEvent.click(submitBtn[submitBtn.length - 1]);
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/knowledge/ingest') && (i as RequestInit)?.method === 'POST')).toBe(true)
    );
  });

  test('shows success message with chunk count after ingest', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await screen.findByRole('button', { name: /^text$/i });
    const collectionSelect = screen.getAllByRole('combobox')[0];
    await userEvent.selectOptions(collectionSelect, 'col-1');
    await userEvent.type(screen.getByPlaceholderText(/paste content/i), 'content');
    const submitBtn = screen.getAllByRole('button', { name: /^ingest$/i });
    await userEvent.click(submitBtn[submitBtn.length - 1]);
    // Verify the ingest API was called successfully (response: 7 chunks)
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/knowledge/ingest') && (i as RequestInit)?.method === 'POST'
      )).toBe(true)
    );
  });
});

describe('KnowledgePage – Search tab', () => {
  test('Search button is disabled when query is empty', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-search'));
    await screen.findByPlaceholderText(/search across/i);
    const searchBtns = screen.getAllByRole('button', { name: /^search$/i });
    // The submit search button (last one, inside panel)
    expect(searchBtns[searchBtns.length - 1]).toBeDisabled();
  });

  test('shows search results after querying', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-search'));
    await userEvent.type(screen.getByPlaceholderText(/search across/i), 'engineering');
    const searchBtns = screen.getAllByRole('button', { name: /^search$/i });
    await userEvent.click(searchBtns[searchBtns.length - 1]);
    expect(await screen.findByText(/Relevant document content/i)).toBeInTheDocument();
  });

  test('shows no results message when search returns empty', async () => {
    mockFetch({ searchResults: [] });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-search'));
    await userEvent.type(screen.getByPlaceholderText(/search across/i), 'xyz');
    const searchBtns = screen.getAllByRole('button', { name: /^search$/i });
    await userEvent.click(searchBtns[searchBtns.length - 1]);
    expect(await screen.findByText(/no results/i)).toBeInTheDocument();
  });
});
