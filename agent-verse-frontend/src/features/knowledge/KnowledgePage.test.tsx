import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
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

function mockFetch(opts: {
  collections?: unknown[]; searchResults?: unknown[]; documents?: unknown[];
  analyticsBulk?: unknown; rpaResult?: unknown;
} = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET');
    if (url.includes('/knowledge/ingest/rpa-url') && method === 'POST')
      return new Response(JSON.stringify(opts.rpaResult ?? {
        collection_id: 'col-1', source_type: 'rpa-web', urls_processed: 1, urls_succeeded: 1,
        total_chunks_ingested: 5, playwright_available: true,
        results: [{ url: 'https://example.com', success: true, chunks_ingested: 5, total_chars: 1200, playwright_used: true }],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge/ingest/file') && method === 'POST')
      return new Response(JSON.stringify({ chunks_created: 3, filename: 'notes.txt' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/sync') && method === 'POST')
      return new Response(JSON.stringify({ status: 'started' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/documents') && url.includes('/knowledge/collections/'))
      return new Response(JSON.stringify({ documents: opts.documents ?? [], total: (opts.documents ?? []).length }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge/cache/stats'))
      return new Response(JSON.stringify({ hits: 5, misses: 3 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/knowledge/analytics'))
      return new Response(JSON.stringify(opts.analyticsBulk ?? {}), { status: 200, headers: { 'Content-Type': 'application/json' } });
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

  test('USR-3: the card shows the real embedder and create sends no embedder label', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/knowledge/collections') && init?.method === 'POST')
        return new Response(JSON.stringify({ collection_id: 'col-new', name: 'N', embedder: 'all-mpnet-base-v2', embedding_dim: 768 }), { status: 201, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([{ ...COLLECTION, embedder: 'all-mpnet-base-v2', embedding_dim: 768 }]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    const card = await screen.findByTestId(`collection-card-${COLLECTION.collection_id}`);
    expect(card).toHaveTextContent('all-mpnet-base-v2 · 768d');
    expect(card).not.toHaveTextContent('voyage');
    await userEvent.click(screen.getByRole('button', { name: /new collection/i }));
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText(/my-knowledge-base/i), 'N');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));
    await waitFor(() => {
      const post = spy.mock.calls.find(([u, i]) => String(u).includes('/knowledge/collections') && (i as RequestInit)?.method === 'POST');
      expect(post).toBeDefined();
      expect(JSON.parse(String((post![1] as RequestInit).body))).toEqual({ name: 'N' });
    });
  });

  test('deletes a collection via DELETE', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId(`collection-card-${COLLECTION.collection_id}`);
    await userEvent.click(screen.getByTestId(`delete-collection-${COLLECTION.collection_id}`));
    await userEvent.click(screen.getByRole('button', { name: /delete collection/i }));
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

  test('inline citation markers hover-link to their source chunk', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET');
      if (url.includes('/knowledge/collections')) return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/chat') && method === 'POST')
        return new Response(JSON.stringify({
          answer: 'CI/CD pipelines run on every push [1]. Secrets rotate monthly [2].',
          citations: [
            { index: 1, chunk_id: 'c1', collection_id: 'col-1', score: 0.92, source_url: '', page_number: null, excerpt: 'Pipelines trigger on push to main.' },
            { index: 2, chunk_id: 'c2', collection_id: 'col-1', score: 0.55, source_url: 'https://example.com/secrets', page_number: null, excerpt: 'Secrets rotation policy: 30 days.' },
          ],
          collections_searched: 1, chunks_retrieved: 2, question: 'How does CI/CD work?',
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    await userEvent.type(screen.getByTestId('ask-input'), 'How does CI/CD work?');
    await userEvent.click(screen.getByTestId('ask-btn'));

    const marker1 = await screen.findByTestId('citation-marker-1');
    expect(marker1).toHaveTextContent('[1]');

    // No tooltip/highlight until hovered.
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    expect(screen.getByTestId('citation-source-1')).not.toHaveClass('ring-violet-300');

    await userEvent.hover(marker1);
    expect(await screen.findByRole('tooltip')).toHaveTextContent(/Pipelines trigger on push to main/);
    expect(screen.getByTestId('citation-source-1')).toHaveClass('ring-violet-300');

    await userEvent.unhover(marker1);
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument());

    // Clicking scrolls the matching source into view (jsdom-polyfilled no-op) and re-activates it.
    await userEvent.click(await screen.findByTestId('citation-marker-2'));
    expect(await screen.findByRole('tooltip')).toHaveTextContent(/Secrets rotation policy/);
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
    await userEvent.click(screen.getByRole('button', { name: /^text$/i }));
    // Select collection — find the standard form select (outside RPA section)
    const allSelects = screen.getAllByRole('combobox');
    // Second select is the standard ingestion form's collection select
    const standardSelect = allSelects[allSelects.length - 1];
    await userEvent.selectOptions(standardSelect, 'col-1');
    // Add content
    await userEvent.type(screen.getByPlaceholderText(/paste content/i), 'Some content to ingest');
    const submitBtn = screen.getAllByRole('button', { name: /^ingest$/i });
    await userEvent.click(submitBtn[submitBtn.length - 1]);
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/knowledge/ingest') && !(i as RequestInit)?.body?.toString().includes('rpa') && (i as RequestInit)?.method === 'POST')).toBe(true)
    );
  });

  test('shows success message with chunk count after ingest', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await screen.findByRole('button', { name: /^text$/i });
    await userEvent.click(screen.getByRole('button', { name: /^text$/i }));
    const allSelects = screen.getAllByRole('combobox');
    const standardSelect = allSelects[allSelects.length - 1];
    await userEvent.selectOptions(standardSelect, 'col-1');
    await userEvent.type(screen.getByPlaceholderText(/paste content/i), 'content');
    const submitBtn = screen.getAllByRole('button', { name: /^ingest$/i });
    await userEvent.click(submitBtn[submitBtn.length - 1]);
    // Verify the ingest API was called successfully
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/knowledge/ingest') && (i as RequestInit)?.method === 'POST'
      )).toBe(true)
    );
  });

  test('KB-37: a 429 document-quota refusal of a text ingest says the limit was reached', async () => {
    useToastStore.setState({ toasts: [] });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/knowledge/ingest') && init?.method === 'POST')
        return new Response(JSON.stringify({ detail: "documents quota exceeded for plan 'free': 100/100" }), { status: 429, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await userEvent.click(await screen.findByRole('button', { name: /^text$/i }));
    const allSelects = screen.getAllByRole('combobox');
    await userEvent.selectOptions(allSelects[allSelects.length - 1], 'col-1');
    await userEvent.type(screen.getByPlaceholderText(/paste content/i), 'content');
    const submitBtn = screen.getAllByRole('button', { name: /^ingest$/i });
    await userEvent.click(submitBtn[submitBtn.length - 1]);
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some(
        (t) => t.kind === 'error' && /not ingested, limit reached/i.test(t.message),
      )).toBe(true),
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

describe('KnowledgePage – Documents tab (WS-13: source-provenance filtering)', () => {
  const DOCS = [
    { id: 'd1', title: 'Runbook.md', source_type: 'text', chunk_count: 4, created_at: '2026-08-01T00:00:00Z' },
    { id: 'd2', title: 'scraped-page', source_type: 'rpa-web', chunk_count: 2, created_at: '2026-08-02T00:00:00Z' },
    { id: 'd3', title: 'invoice-scan', source_type: 'ocr', chunk_count: 1, created_at: '2026-08-03T00:00:00Z' },
  ];

  test('renders real source_type badges for every document, including rpa-web and ocr', async () => {
    mockFetch({ documents: DOCS });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));

    expect(await screen.findByText('Runbook.md')).toBeInTheDocument();
    expect(screen.getByText('scraped-page')).toBeInTheDocument();
    expect(screen.getByText('invoice-scan')).toBeInTheDocument();
    // "rpa-web"/"ocr" appear twice (filter chip + document badge) — both real.
    expect(screen.getAllByText('rpa-web').length).toBeGreaterThan(0);
    expect(screen.getAllByText('ocr').length).toBeGreaterThan(0);
  });

  test('filtering by source shows only documents with that real source_type', async () => {
    mockFetch({ documents: DOCS });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Runbook.md');

    await userEvent.click(screen.getByRole('button', { name: 'ocr' }));

    expect(screen.getByText('invoice-scan')).toBeInTheDocument();
    expect(screen.queryByText('Runbook.md')).not.toBeInTheDocument();
    expect(screen.queryByText('scraped-page')).not.toBeInTheDocument();

    // Back to "All" restores every real document.
    await userEvent.click(screen.getByRole('button', { name: 'All' }));
    expect(await screen.findByText('Runbook.md')).toBeInTheDocument();
  });

  test('does not show a source filter when there is only one collection with no documents', async () => {
    mockFetch({ documents: [] });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));

    expect(await screen.findByText(/no documents in this collection/i)).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /filter by source/i })).not.toBeInTheDocument();
  });
});

describe('KnowledgePage – Documents tab (extended actions)', () => {
  const DOCS = [
    { id: 'd1', title: 'Runbook.md', source_type: 'text', chunk_count: 4, created_at: '2026-08-01T00:00:00Z', content: 'Full runbook content here.' },
  ];
  const DOC_NO_CONTENT = [{ id: 'd9', title: 'NoContent', source_type: 'text', chunk_count: 1 }];

  test('reingests a document and shows a success toast', async () => {
    const spy = mockFetch({ documents: DOCS });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Runbook.md');
    await userEvent.click(screen.getByTitle('Re-ingest document from source'));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/reingest') && (i as RequestInit)?.method === 'POST')).toBe(true)
    );
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message.includes('queued for re-ingestion'))).toBe(true)
    );
  });

  test('deletes a document via DELETE', async () => {
    const spy = mockFetch({ documents: DOCS });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Runbook.md');
    await userEvent.click(screen.getByTitle('Delete'));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/d1') && (i as RequestInit)?.method === 'DELETE')).toBe(true)
    );
  });

  test('starts a collection sync and shows a success toast', async () => {
    mockFetch({ documents: DOCS });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Runbook.md');
    await userEvent.click(screen.getByRole('button', { name: /sync all/i }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message.includes('Collection sync started'))).toBe(true)
    );
  });

  test('shows an info toast when sync is not available for the collection type', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/documents') && url.includes('/knowledge/collections/'))
        return new Response(JSON.stringify({ documents: DOCS, total: DOCS.length }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/sync') && method === 'POST') return new Response('nope', { status: 503 });
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Runbook.md');
    await userEvent.click(screen.getByRole('button', { name: /sync all/i }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'info' && t.message.includes('Sync not available'))).toBe(true)
    );
  });

  test('opens the preview modal and closes it via the backdrop', async () => {
    mockFetch({ documents: DOCS });
    const { container } = renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Runbook.md');
    await userEvent.click(screen.getByTitle('Preview'));
    expect(await screen.findByText('Full runbook content here.')).toBeInTheDocument();
    const backdrop = container.querySelector('.backdrop-blur-sm') as HTMLElement;
    await userEvent.click(backdrop);
    await waitFor(() => expect(screen.queryByText('Full runbook content here.')).not.toBeInTheDocument());
  });

  test('closes the preview modal via the close (X) button', async () => {
    mockFetch({ documents: DOCS });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Runbook.md');
    await userEvent.click(screen.getByTitle('Preview'));
    await screen.findByText('Full runbook content here.');
    const closeBtn = document.querySelector('svg.lucide-x')?.closest('button') as HTMLElement;
    await userEvent.click(closeBtn);
    await waitFor(() => expect(screen.queryByText('Full runbook content here.')).not.toBeInTheDocument());
  });

  test('preview modal falls back to JSON when a document has no content or preview field', async () => {
    mockFetch({ documents: DOC_NO_CONTENT });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('NoContent');
    await userEvent.click(screen.getByTitle('Preview'));
    expect(await screen.findByText(/"id":\s*"d9"/)).toBeInTheDocument();
  });

  test('shows pagination and requests the next page when total exceeds the page size', async () => {
    const manyDocs = Array.from({ length: 20 }, (_, i) => ({ id: `d${i}`, title: `Doc ${i}`, source_type: 'text', chunk_count: 1 }));
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/documents') && url.includes('/knowledge/collections/'))
        return new Response(JSON.stringify({ documents: manyDocs, total: 45 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Doc 0');
    expect(screen.getByText(/45 documents/)).toBeInTheDocument();
    expect(await screen.findByRole('navigation', { name: /pagination/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /next page/i }));
    await waitFor(() => expect(spy.mock.calls.some(([u]) => String(u).includes('offset=20'))).toBe(true));
  });
});

describe('KnowledgePage – Collections tab (extended)', () => {
  test('expands and collapses collection stats', async () => {
    mockFetch();
    renderPage();
    await screen.findByTestId(`collection-card-${COLLECTION.collection_id}`);
    await userEvent.click(screen.getByRole('button', { name: /view stats/i }));
    expect(await screen.findByText(/Chunks:/)).toBeInTheDocument();
    expect(screen.getByText(/Embed coverage:/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /hide stats/i }));
    expect(screen.queryByText(/Chunks:/)).not.toBeInTheDocument();
  });

  test('cancels the create-collection form', async () => {
    mockFetch();
    renderPage();
    await screen.findByTestId('collections-grid');
    await userEvent.click(screen.getByRole('button', { name: /new collection/i }));
    await screen.findByPlaceholderText(/my-knowledge-base/i);
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByPlaceholderText(/my-knowledge-base/i)).not.toBeInTheDocument();
  });
});

describe('KnowledgePage – Ask AI tab (extended)', () => {
  test('fills the question box from an example prompt', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    await userEvent.click(screen.getByText('What are the key technical decisions made?'));
    expect((screen.getByTestId('ask-input') as HTMLTextAreaElement).value).toBe('What are the key technical decisions made?');
  });

  test('toggles a collection filter chip on and off', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    const chip = await screen.findByRole('button', { name: 'Engineering Docs' });
    await userEvent.click(chip);
    expect(chip).toHaveClass('bg-primary');
    await userEvent.click(chip);
    expect(chip).not.toHaveClass('bg-primary');
  });

  test('submits the question with Cmd+Enter', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    const input = screen.getByTestId('ask-input');
    await userEvent.type(input, 'quick question');
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/knowledge/chat') && (i as RequestInit)?.method === 'POST')).toBe(true)
    );
  });

  test.each([
    [429, { detail: 'LLM budget exhausted for this tenant' }, /budget is exhausted/i],
    [503, { detail: 'Answer synthesis is unavailable' }, /unavailable or timed out/i],
    [504, { detail: 'Gateway Timeout' }, /unavailable or timed out/i],
  ])('a %s from /knowledge/chat shows a specific message', async (status, body, expected) => {
    useToastStore.setState({ toasts: [] });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/chat') && method === 'POST')
        return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    await userEvent.type(screen.getByTestId('ask-input'), 'a question');
    await userEvent.click(screen.getByTestId('ask-btn'));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error' && expected.test(t.message))).toBe(true),
    );
    // One clear message, not an extra generic "Server error" toast.
    expect(useToastStore.getState().toasts.some((t) => /^Server error/.test(t.message))).toBe(false);
  });

  test('shows an error toast when asking fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/chat') && method === 'POST') return new Response('fail', { status: 500 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    await userEvent.type(screen.getByTestId('ask-input'), 'a question');
    await userEvent.click(screen.getByTestId('ask-btn'));
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true));
  });

  test('a single-collection question goes to /knowledge/chat scoped to that collection', async () => {
    // Regression: it first POSTed a non-existent
    // /knowledge/collections/{id}/query/stream (always 404) before falling back.
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/chat') && method === 'POST')
        return new Response(JSON.stringify({ answer: 'scoped answer', citations: [], collections_searched: 1, chunks_retrieved: 1, question: 'q' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    await userEvent.click(await screen.findByRole('button', { name: 'Engineering Docs' }));
    await userEvent.type(screen.getByTestId('ask-input'), 'q');
    await userEvent.click(screen.getByTestId('ask-btn'));
    expect(await screen.findByText(/scoped answer/)).toBeInTheDocument();
    expect(spy.mock.calls.some(([u]) => String(u).includes('/query/stream'))).toBe(false);
    const chat = spy.mock.calls.find(([u]) => String(u).includes('/knowledge/chat'));
    expect(JSON.parse(String((chat?.[1] as RequestInit).body)).collection_ids).toEqual([COLLECTION.collection_id]);
  });
});

describe('KnowledgePage – Ingest tab (extended source types & uploads)', () => {
  test('shows URL field for the url source type', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await userEvent.click(screen.getByRole('button', { name: /^url$/i }));
    expect(await screen.findByPlaceholderText('https://example.com/page')).toBeInTheDocument();
  });

  test('shows repo/branch fields for the github source type', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await userEvent.click(screen.getByRole('button', { name: /^github$/i }));
    expect(await screen.findByPlaceholderText('owner/repo')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/branch/i)).toBeInTheDocument();
  });

  test('shows base url/space key/token fields for confluence and jira', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await userEvent.click(screen.getByRole('button', { name: /^confluence$/i }));
    expect(await screen.findByPlaceholderText('Space key')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^jira$/i }));
    expect(await screen.findByPlaceholderText('Project key')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('API token')).toBeInTheDocument();
  });

  test('shows bot token/channels fields for the slack source type', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await userEvent.click(screen.getByRole('button', { name: /^slack$/i }));
    expect(await screen.findByPlaceholderText('Bot token')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/channels/i)).toBeInTheDocument();
  });

  test('uploads a file via the file input when a collection is selected', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await screen.findByRole('button', { name: /^text$/i });
    const allSelects = screen.getAllByRole('combobox');
    await userEvent.selectOptions(allSelects[allSelects.length - 1], 'col-1');
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['hello'], 'notes.txt', { type: 'text/plain' });
    await userEvent.upload(fileInput, file);
    // Picking only queues the file — the Ingest button sends it.
    await screen.findByTestId('queued-file');
    const submitBtns = screen.getAllByRole('button', { name: /^ingest$/i });
    await userEvent.click(submitBtns[submitBtns.length - 1]);
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/knowledge/ingest/file') && (i as RequestInit)?.method === 'POST')).toBe(true)
    );
  });

  test('shows an error toast when dropping a file with no collection selected', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    const dropzone = (await screen.findByText(/drag & drop a file here/i)).closest('div') as HTMLElement;
    const file = new File(['hi'], 'a.txt', { type: 'text/plain' });
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message.includes('Select a collection first'))).toBe(true)
    );
  });

  test('uploads a dropped file when a collection is selected', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await screen.findByRole('button', { name: /^text$/i });
    const allSelects = screen.getAllByRole('combobox');
    await userEvent.selectOptions(allSelects[allSelects.length - 1], 'col-1');
    const dropzone = screen.getByText(/drag & drop a file here/i).closest('div') as HTMLElement;
    const file = new File(['hi'], 'a.txt', { type: 'text/plain' });
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });
    await screen.findByTestId('queued-file');
    const submitBtns = screen.getAllByRole('button', { name: /^ingest$/i });
    await userEvent.click(submitBtns[submitBtns.length - 1]);
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/knowledge/ingest/file') && (i as RequestInit)?.method === 'POST')).toBe(true)
    );
  });

  test('shows an error toast when standard ingest fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/knowledge/collections') && method === 'GET')
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/ingest') && method === 'POST')
        return new Response(JSON.stringify({ detail: 'boom' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await screen.findByRole('button', { name: /^text$/i });
    await userEvent.click(screen.getByRole('button', { name: /^text$/i }));
    const allSelects = screen.getAllByRole('combobox');
    await userEvent.selectOptions(allSelects[allSelects.length - 1], 'col-1');
    await userEvent.type(screen.getByPlaceholderText(/paste content/i), 'x');
    const submitBtns = screen.getAllByRole('button', { name: /^ingest$/i });
    await userEvent.click(submitBtns[submitBtns.length - 1]);
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true));
  });
});

// ── File queue: pick/drop stores the file; the Ingest button uploads it ──────

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

/** fetch mock whose /knowledge/ingest/file handler is supplied per test. */
function mockFetchWithUpload(upload: (callNo: number) => Response) {
  let n = 0;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (url.includes('/knowledge/ingest/file') && method === 'POST') { n += 1; return upload(n); }
    if (url.includes('/knowledge/ingest') && method === 'POST') return jsonResponse({ chunks_created: 7, document_id: 'doc-1' }, 201);
    if (url.includes('/knowledge/collections')) return jsonResponse([COLLECTION]);
    return jsonResponse({});
  });
}

async function openIngest() {
  renderPage();
  await screen.findByRole('heading', { name: /knowledge/i });
  await userEvent.click(screen.getByTestId('tab-ingest'));
  await screen.findByRole('button', { name: /^text$/i });
  // wait until collections have loaded into the collection selects
  await screen.findAllByRole('option', { name: 'Engineering Docs' });
}

async function selectCollection() {
  const selects = screen.getAllByRole('combobox');
  await userEvent.selectOptions(selects[selects.length - 1], 'col-1');
}

function ingestButton() {
  return screen.getByTestId('ingest-submit');
}

function fileInput() {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}

type FetchSpy = ReturnType<typeof mockFetchWithUpload>;

function uploadCalls(spy: FetchSpy) {
  return spy.mock.calls.filter(([u, i]) => String(u).includes('/knowledge/ingest/file') && i?.method === 'POST');
}

function jsonIngestCalls(spy: FetchSpy) {
  return spy.mock.calls.filter(([u, i]) => /\/knowledge\/ingest$/.test(String(u)) && i?.method === 'POST');
}

describe('KnowledgePage – Ingest tab file queue', () => {
  beforeEach(() => useToastStore.setState({ toasts: [] }));

  test('picking a file queues it (name, size, remove) and does not upload', async () => {
    const spy = mockFetchWithUpload(() => jsonResponse({ chunks_created: 3, filename: 'notes.txt' }));
    await openIngest();
    await selectCollection();
    await userEvent.upload(fileInput(), new File(['hello world'], 'notes.txt', { type: 'text/plain' }));
    const queued = await screen.findByTestId('queued-file');
    expect(queued).toHaveTextContent('notes.txt');
    expect(queued).toHaveTextContent(/11 B/);
    expect(screen.getByRole('button', { name: /remove notes\.txt/i })).toBeInTheDocument();
    expect(uploadCalls(spy)).toHaveLength(0);
    expect(screen.queryByText(/uploading…/i)).not.toBeInTheDocument();
  });

  test('Ingest uploads the queued file as multipart (file + collection_id), then clears the queue and shows chunks created', async () => {
    const spy = mockFetchWithUpload(() => jsonResponse({ chunks_created: 4, filename: 'deck.pptx' }));
    await openIngest();
    await userEvent.click(screen.getByRole('button', { name: /^powerpoint$/i }));
    await selectCollection();
    const file = new File(['pptx-bytes'], 'deck.pptx', { type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' });
    await userEvent.upload(fileInput(), file);
    await screen.findByTestId('queued-file');
    await userEvent.click(ingestButton());
    await waitFor(() => expect(uploadCalls(spy)).toHaveLength(1));
    const body = uploadCalls(spy)[0][1]?.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect((body.get('file') as File).name).toBe('deck.pptx');
    expect(body.get('collection_id')).toBe('col-1');
    expect(jsonIngestCalls(spy)).toHaveLength(0);
    expect(await screen.findByTestId('ingest-result')).toHaveTextContent(/4 chunks created/i);
    expect(screen.queryByTestId('queued-file')).not.toBeInTheDocument();
  });

  test('file source types disable Ingest with a reason until a file is queued, and never send empty JSON', async () => {
    const spy = mockFetchWithUpload(() => jsonResponse({ chunks_created: 1, filename: 'a.pdf' }));
    await openIngest();
    await userEvent.click(screen.getByRole('button', { name: /^pdf$/i }));
    expect(ingestButton()).toBeDisabled();
    expect(screen.getByTestId('ingest-disabled-reason')).toHaveTextContent(/select a collection first/i);
    await selectCollection();
    expect(ingestButton()).toBeDisabled();
    expect(screen.getByTestId('ingest-disabled-reason')).toHaveTextContent(/choose a file/i);
    await userEvent.click(ingestButton());
    expect(jsonIngestCalls(spy)).toHaveLength(0);
    await userEvent.upload(fileInput(), new File(['%PDF'], 'a.pdf', { type: 'application/pdf' }));
    expect(ingestButton()).toBeEnabled();
    expect(screen.queryByTestId('ingest-disabled-reason')).not.toBeInTheDocument();
  });

  test('Text source keeps posting JSON to /knowledge/ingest and explains why it is disabled when empty', async () => {
    const spy = mockFetchWithUpload(() => jsonResponse({}));
    await openIngest();
    await selectCollection();
    expect(ingestButton()).toBeDisabled();
    expect(screen.getByTestId('ingest-disabled-reason')).toHaveTextContent(/enter some content/i);
    await userEvent.type(screen.getByPlaceholderText(/paste content/i), 'hello');
    await userEvent.click(ingestButton());
    await waitFor(() => expect(jsonIngestCalls(spy)).toHaveLength(1));
    const sent = JSON.parse(String(jsonIngestCalls(spy)[0][1]?.body)) as Record<string, unknown>;
    expect(sent).toMatchObject({ content: 'hello', source_type: 'text', collection_id: 'col-1' });
    expect(uploadCalls(spy)).toHaveLength(0);
  });

  test('resets the file input after each pick so the same file can be picked again', async () => {
    mockFetchWithUpload(() => jsonResponse({}));
    await openIngest();
    await selectCollection();
    const file = new File(['x'], 'same.md', { type: 'text/markdown' });
    await userEvent.upload(fileInput(), file);
    await screen.findByTestId('queued-file');
    expect(fileInput().value).toBe('');
    await userEvent.click(screen.getByRole('button', { name: /remove same\.md/i }));
    expect(screen.queryByTestId('queued-file')).not.toBeInTheDocument();
    await userEvent.upload(fileInput(), file);
    expect(await screen.findByTestId('queued-file')).toHaveTextContent('same.md');
  });

  test('picking a file with no collection shows "Select a collection first"', async () => {
    const spy = mockFetchWithUpload(() => jsonResponse({}));
    await openIngest();
    await userEvent.upload(fileInput(), new File(['x'], 'notes.txt', { type: 'text/plain' }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.message.includes('Select a collection first'))).toBe(true)
    );
    expect(screen.getByTestId('ingest-disabled-reason')).toHaveTextContent(/select a collection first/i);
    expect(uploadCalls(spy)).toHaveLength(0);
  });

  test('on 503 keeps the queued file, shows a persistent inline error with the server message, and retries in one click', async () => {
    const spy = mockFetchWithUpload((n) =>
      n === 1
        ? jsonResponse({ detail: 'Embedding provider is unavailable' }, 503)
        : jsonResponse({ chunks_created: 2, filename: 'notes.txt' }),
    );
    await openIngest();
    await selectCollection();
    await userEvent.upload(fileInput(), new File(['hello'], 'notes.txt', { type: 'text/plain' }));
    await userEvent.click(ingestButton());
    const alert = await screen.findByTestId('ingest-error');
    expect(alert).toHaveTextContent('Embedding provider is unavailable');
    expect(alert).toHaveAttribute('role', 'alert');
    expect(screen.getByTestId('queued-file')).toHaveTextContent('notes.txt');
    // one transient toast, not two
    expect(useToastStore.getState().toasts.filter((t) => t.kind === 'error')).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(uploadCalls(spy)).toHaveLength(2));
    expect(await screen.findByTestId('ingest-result')).toHaveTextContent(/2 chunks created/i);
    expect(screen.queryByTestId('ingest-error')).not.toBeInTheDocument();
    expect(screen.queryByTestId('queued-file')).not.toBeInTheDocument();
  });

  test('a truncated workbook upload shows a warning', async () => {
    mockFetchWithUpload(() =>
      jsonResponse({ chunks_created: 9, filename: 'big.xlsx', truncated: true, truncated_sheets: ['Orders'] }),
    );
    await openIngest();
    await selectCollection();
    await userEvent.upload(fileInput(), new File(['xlsx'], 'big.xlsx', { type: 'application/octet-stream' }));
    await userEvent.click(ingestButton());
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => /workbook truncated.*Orders/i.test(t.message))).toBe(true),
    );
  });

  test('a 429 names the file and says the quota / budget limit was reached', async () => {
    mockFetchWithUpload(() => jsonResponse({ detail: "document quota exceeded for plan 'free': 1000/1000" }, 429));
    await openIngest();
    await selectCollection();
    await userEvent.upload(fileInput(), new File(['hello'], 'notes.txt', { type: 'text/plain' }));
    await userEvent.click(ingestButton());
    const alert = await screen.findByTestId('ingest-error');
    expect(alert).toHaveTextContent(/notes\.txt: not ingested, limit reached/i);
    expect(alert).toHaveTextContent("document quota exceeded for plan 'free': 1000/1000");
    expect(screen.getByTestId('queued-file')).toHaveTextContent('notes.txt');
  });

  test('shows the server message for a 415 (e.g. legacy .ppt dropped)', async () => {
    mockFetchWithUpload(() => jsonResponse({ detail: 'Legacy .ppt is not supported; convert to .pptx' }, 415));
    await openIngest();
    await selectCollection();
    const dropzone = screen.getByText(/drag & drop a file here/i).closest('div') as HTMLElement;
    fireEvent.drop(dropzone, { dataTransfer: { files: [new File(['x'], 'old.ppt')] } });
    await screen.findByTestId('queued-file');
    await userEvent.click(ingestButton());
    expect(await screen.findByTestId('ingest-error')).toHaveTextContent(/convert to \.pptx/i);
    expect(screen.getByTestId('queued-file')).toHaveTextContent('old.ppt');
  });

  test('offers PowerPoint and Image source types and accepts .pptx and images but not .ppt', async () => {
    mockFetchWithUpload(() => jsonResponse({}));
    await openIngest();
    expect(screen.getByRole('button', { name: /^powerpoint$/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^image$/i }));
    const accept = (fileInput().getAttribute('accept') ?? '').split(',');
    for (const ext of ['.pptx', '.png', '.jpg', '.jpeg', '.webp', '.pdf', '.docx', '.xlsx', '.csv']) expect(accept).toContain(ext);
    expect(accept).not.toContain('.ppt');
    expect(screen.getByText(/save as \.pptx/i)).toBeInTheDocument();
  });

  test('an image can be queued and uploaded under the Image source type', async () => {
    const spy = mockFetchWithUpload(() => jsonResponse({ chunks_created: 1, filename: 'scan.png' }));
    await openIngest();
    await userEvent.click(screen.getByRole('button', { name: /^image$/i }));
    await selectCollection();
    await userEvent.upload(fileInput(), new File(['png'], 'scan.png', { type: 'image/png' }));
    await userEvent.click(ingestButton());
    await waitFor(() => expect(uploadCalls(spy)).toHaveLength(1));
    expect((uploadCalls(spy)[0][1]?.body as FormData).get('file')).toBeInstanceOf(File);
  });
});

describe('KnowledgePage – RPA web scraper section', () => {
  test('disables the scrape button until a collection and URLs are provided, and updates the valid-URL count', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    const btn = await screen.findByTestId('rpa-scrape-btn');
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByTestId('rpa-urls-input'), 'https://a.com\nnot-a-url\nhttps://b.com');
    expect(screen.getByText(/2 valid URLs detected/i)).toBeInTheDocument();
    const allSelects = screen.getAllByRole('combobox');
    await userEvent.selectOptions(allSelects[0], 'col-1');
    expect(btn).not.toBeDisabled();
  });

  test('toggles the screenshot and extract-links checkboxes', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await screen.findByTestId('rpa-scrape-section');
    const screenshotCb = screen.getByRole('checkbox', { name: /capture screenshot/i });
    const linksCb = screen.getByRole('checkbox', { name: /extract page links/i });
    await userEvent.click(screenshotCb);
    await userEvent.click(linksCb);
    expect(screenshotCb).toBeChecked();
    expect(linksCb).toBeChecked();
  });

  test('scrapes URLs via RPA and shows results', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await screen.findByTestId('rpa-scrape-section');
    const allSelects = screen.getAllByRole('combobox');
    await userEvent.selectOptions(allSelects[0], 'col-1');
    await userEvent.type(screen.getByTestId('rpa-urls-input'), 'https://example.com');
    await userEvent.click(screen.getByTestId('rpa-scrape-btn'));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/knowledge/ingest/rpa-url') && (i as RequestInit)?.method === 'POST')).toBe(true)
    );
    expect(await screen.findByTestId('rpa-results')).toBeInTheDocument();
    expect(screen.getByText(/Playwright active/i)).toBeInTheDocument();
  });

  test('shows an error toast when the RPA scrape fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/knowledge/collections') && method === 'GET')
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/ingest/rpa-url'))
        return new Response(JSON.stringify({ detail: 'scrape failed' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await screen.findByTestId('rpa-scrape-section');
    const allSelects = screen.getAllByRole('combobox');
    await userEvent.selectOptions(allSelects[0], 'col-1');
    await userEvent.type(screen.getByTestId('rpa-urls-input'), 'https://example.com');
    await userEvent.click(screen.getByTestId('rpa-scrape-btn'));
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true));
  });
});

describe('KnowledgePage – Search tab (extended)', () => {
  test('shows an error toast when search fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/knowledge/collections') && method === 'GET')
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/search')) return new Response('boom', { status: 500 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-search'));
    await userEvent.type(screen.getByPlaceholderText(/search across/i), 'test');
    const searchBtns = screen.getAllByRole('button', { name: /^search$/i });
    await userEvent.click(searchBtns[searchBtns.length - 1]);
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true));
  });

  test('copies a result to the clipboard', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    mockFetch();
    const { container } = renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-search'));
    await userEvent.type(screen.getByPlaceholderText(/search across/i), 'engineering');
    const searchBtns = screen.getAllByRole('button', { name: /^search$/i });
    await userEvent.click(searchBtns[searchBtns.length - 1]);
    await screen.findByText(/Relevant document content/i);
    const copyBtn = container.querySelector('svg.lucide-clipboard-copy')?.closest('button') as HTMLElement;
    await userEvent.click(copyBtn);
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(CHUNK.content));
  });
});

describe('KnowledgePage – Collections tab (error paths & inputs)', () => {
  test('shows an error toast when creating a collection fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/knowledge/collections') && method === 'POST')
        return new Response(JSON.stringify({ detail: 'name taken' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByTestId('collections-grid');
    await userEvent.click(screen.getByRole('button', { name: /new collection/i }));
    await userEvent.type(screen.getByPlaceholderText(/my-knowledge-base/i), 'Dup');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true));
  });

  test('a plan-limit 429 on create shows the plan-limit message (RATE-01)', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/knowledge/collections') && method === 'POST')
        return new Response(
          JSON.stringify({ error: { code: 'PLAN_LIMIT_EXCEEDED', message: "Knowledge collection limit (1) reached for plan 'free'." } }),
          { status: 429, headers: { 'Content-Type': 'application/json' } },
        );
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByTestId('collections-grid');
    await userEvent.click(screen.getByRole('button', { name: /new collection/i }));
    await userEvent.type(screen.getByPlaceholderText(/my-knowledge-base/i), 'Second');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));
    const expected =
      "Plan limit reached: Knowledge collection limit (1) reached for plan 'free'. Upgrade your plan or delete a collection.";
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message === expected)).toBe(true),
    );
  });

  test('cancels the delete-collection confirmation without deleting', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByTestId(`collection-card-${COLLECTION.collection_id}`);
    await userEvent.click(screen.getByTestId(`delete-collection-${COLLECTION.collection_id}`));
    await userEvent.click(await screen.findByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByText(/delete collection\?/i)).not.toBeInTheDocument();
    expect(spy.mock.calls.some(([u, i]) => String(u).includes('col-1') && (i as RequestInit)?.method === 'DELETE')).toBe(false);
  });

  test('KB-33: a 409 from collection delete says a document is under legal hold', async () => {
    useToastStore.setState({ toasts: [] });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/knowledge/collections/col-1') && method === 'DELETE')
        return new Response(JSON.stringify({ detail: 'held' }), { status: 409, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByTestId(`collection-card-${COLLECTION.collection_id}`);
    await userEvent.click(screen.getByTestId(`delete-collection-${COLLECTION.collection_id}`));
    await userEvent.click(screen.getByRole('button', { name: /delete collection/i }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some(
        (t) => t.kind === 'error' && /document in this collection is under legal hold/i.test(t.message),
      )).toBe(true),
    );
  });
});

describe('KnowledgePage – Ask AI tab (hover on source row)', () => {
  test('hovering a source row (not the marker) highlights it', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/knowledge/collections')) return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/chat') && method === 'POST')
        return new Response(JSON.stringify({
          answer: 'Pipelines run on every push [1].',
          citations: [{ index: 1, chunk_id: 'c1', collection_id: 'col-1', score: 0.92, source_url: '', page_number: null, excerpt: 'Pipelines trigger on push to main.' }],
          collections_searched: 1, chunks_retrieved: 1, question: 'How does CI/CD work?',
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    await userEvent.type(screen.getByTestId('ask-input'), 'How does CI/CD work?');
    await userEvent.click(screen.getByTestId('ask-btn'));
    const sourceRow = await screen.findByTestId('citation-source-1');
    await userEvent.hover(sourceRow);
    expect(sourceRow).toHaveClass('ring-violet-300');
    await userEvent.unhover(sourceRow);
    await waitFor(() => expect(sourceRow).not.toHaveClass('ring-violet-300'));
  });
});

describe('KnowledgePage – Ingest tab (remaining field inputs & drag-over)', () => {
  test('typing into the url/git field updates its value', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await userEvent.click(screen.getByRole('button', { name: /^url$/i }));
    const input = await screen.findByPlaceholderText('https://example.com/page');
    await userEvent.type(input, 'https://docs.example.com');
    expect(input).toHaveValue('https://docs.example.com');
  });

  test('typing into github repo/branch fields updates their values', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await userEvent.click(screen.getByRole('button', { name: /^github$/i }));
    const repoInput = await screen.findByPlaceholderText('owner/repo');
    const branchInput = screen.getByPlaceholderText(/branch/i);
    await userEvent.type(repoInput, 'acme/widgets');
    await userEvent.type(branchInput, 'main');
    expect(repoInput).toHaveValue('acme/widgets');
    expect(branchInput).toHaveValue('main');
  });

  test('typing into confluence base url/space key/token fields updates their values', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await userEvent.click(screen.getByRole('button', { name: /^confluence$/i }));
    const baseUrl = await screen.findByPlaceholderText('Base URL');
    const spaceKey = screen.getByPlaceholderText('Space key');
    const apiToken = screen.getByPlaceholderText('API token');
    await userEvent.type(baseUrl, 'https://acme.atlassian.net');
    await userEvent.type(spaceKey, 'ENG');
    await userEvent.type(apiToken, 'secret-token');
    expect(baseUrl).toHaveValue('https://acme.atlassian.net');
    expect(spaceKey).toHaveValue('ENG');
    expect(apiToken).toHaveValue('secret-token');
  });

  test('typing into jira project key field updates its value', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await userEvent.click(screen.getByRole('button', { name: /^jira$/i }));
    const projectKey = await screen.findByPlaceholderText('Project key');
    await userEvent.type(projectKey, 'ENG');
    expect(projectKey).toHaveValue('ENG');
  });

  test('typing into slack bot token/channels fields updates their values', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await userEvent.click(screen.getByRole('button', { name: /^slack$/i }));
    const botToken = await screen.findByPlaceholderText('Bot token');
    const channels = screen.getByPlaceholderText(/channels/i);
    await userEvent.type(botToken, 'xoxb-secret');
    await userEvent.type(channels, '#eng,#general');
    expect(botToken).toHaveValue('xoxb-secret');
    expect(channels).toHaveValue('#eng,#general');
  });

  test('fires the dragover handler on the dropzone without throwing', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    const dropzone = (await screen.findByText(/drag & drop a file here/i)).closest('div') as HTMLElement;
    fireEvent.dragOver(dropzone);
    expect(dropzone).toBeInTheDocument();
  });

  test('shows an error toast when the dropped-file upload fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/knowledge/collections') && method === 'GET')
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/ingest/file') && method === 'POST')
        return new Response(JSON.stringify({ detail: 'bad file' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ingest'));
    await screen.findByRole('button', { name: /^text$/i });
    const allSelects = screen.getAllByRole('combobox');
    await userEvent.selectOptions(allSelects[allSelects.length - 1], 'col-1');
    const dropzone = screen.getByText(/drag & drop a file here/i).closest('div') as HTMLElement;
    const file = new File(['hi'], 'a.txt', { type: 'text/plain' });
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true));
  });
});

describe('KnowledgePage – Search tab (collection filter & top-k input)', () => {
  test('selects a collection filter and changes the results slider', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-search'));
    const collectionSelect = await screen.findByDisplayValue('All collections');
    await userEvent.selectOptions(collectionSelect, 'col-1');
    expect(collectionSelect).toHaveValue('col-1');
    const slider = screen.getByRole('slider');
    fireEvent.change(slider, { target: { value: '15' } });
    expect(screen.getByText(/Results: 15/)).toBeInTheDocument();
  });
});

describe('KnowledgePage – Documents tab (remaining error paths & inputs)', () => {
  const DOCS = [
    { id: 'd1', title: 'Runbook.md', source_type: 'text', chunk_count: 4, created_at: '2026-08-01T00:00:00Z', content: 'Full runbook content here.' },
  ];
  const COLLECTION_2 = { collection_id: 'col-2', name: 'Product Docs', doc_count: 5, embedder: 'voyage' };

  test('shows an error toast when document deletion fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/documents/d1') && method === 'DELETE') return new Response('nope', { status: 500 });
      if (url.includes('/documents') && url.includes('/knowledge/collections/'))
        return new Response(JSON.stringify({ documents: DOCS, total: DOCS.length }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Runbook.md');
    await userEvent.click(screen.getByTitle('Delete'));
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.message === 'Delete failed')).toBe(true));
  });

  test('a document under legal hold (409) says so instead of a generic failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/documents/d1') && method === 'DELETE')
        return new Response(JSON.stringify({ detail: 'Resource is under legal hold and cannot be deleted' }), { status: 409, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/documents') && url.includes('/knowledge/collections/'))
        return new Response(JSON.stringify({ documents: DOCS, total: DOCS.length }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Runbook.md');
    await userEvent.click(screen.getByTitle('Delete'));
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => /under legal hold/i.test(t.message))).toBe(true));
  });

  test('shows an error toast when document reingest fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/reingest') && method === 'POST') return new Response('nope', { status: 500 });
      if (url.includes('/documents') && url.includes('/knowledge/collections/'))
        return new Response(JSON.stringify({ documents: DOCS, total: DOCS.length }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Runbook.md');
    await userEvent.click(screen.getByTitle('Re-ingest document from source'));
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.message === 'Re-ingest failed')).toBe(true));
  });

  test('switches the selected collection and types into the document search box', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/documents') && url.includes('/knowledge/collections/'))
        return new Response(JSON.stringify({ documents: DOCS, total: DOCS.length }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION, COLLECTION_2]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByText('Runbook.md');
    const collectionSelect = screen.getByDisplayValue(/Engineering Docs/);
    await userEvent.selectOptions(collectionSelect, 'col-2');
    expect(collectionSelect).toHaveValue('col-2');
    await userEvent.type(screen.getByPlaceholderText(/search documents/i), 'runbook');
    expect(screen.getByPlaceholderText(/search documents/i)).toHaveValue('runbook');
  });
});

describe('KnowledgePage – Analytics tab', () => {
  test('shows cache stats and per-collection health from the bulk analytics endpoint', async () => {
    mockFetch({
      analyticsBulk: [{
        collection_id: 'col-1', name: 'Engineering Docs', doc_count: 42, chunk_count: 200,
        embedding_coverage_pct: 95, avg_chunk_length: 350, source_type_distribution: { text: 10 },
        embedder: 'voyage', health_score: 0.9,
      }],
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-analytics'));
    expect(await screen.findByText('Cache Hits')).toBeInTheDocument();
    expect(screen.getByText('Cache Misses')).toBeInTheDocument();
    expect(screen.getByText('Hit Rate')).toBeInTheDocument();
    expect(await screen.findByText('Engineering Docs')).toBeInTheDocument();
    expect(screen.getByText(/200 chunks/i)).toBeInTheDocument();
  });

  test('falls back to per-collection stats when the bulk analytics endpoint returns no array', async () => {
    mockFetch();
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-analytics'));
    expect(await screen.findByText('Engineering Docs')).toBeInTheDocument();
    expect(screen.getByText(/200 chunks/i)).toBeInTheDocument();
  });

  test('shows an empty state when there are no collections to analyze', async () => {
    mockFetch({ collections: [] });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-analytics'));
    expect(await screen.findByText(/no collections to analyze/i)).toBeInTheDocument();
  });
});

describe('KnowledgePage – Documents tab (KB-UI-HIDES-ERRORS)', () => {
  function failDocuments(status: number, detail: string) {
    const spy = mockFetch({ documents: [] });
    const original = spy.getMockImplementation()!;
    spy.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/documents') && url.includes('/knowledge/collections/') && (init?.method ?? 'GET') === 'GET')
        return new Response(JSON.stringify({ detail }), { status, headers: { 'Content-Type': 'application/json' } });
      return original(input, init);
    });
    return spy;
  }

  test('a failed document listing shows an error, never an empty collection', async () => {
    failDocuments(503, 'Knowledge persistence is unavailable');
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/could not load documents/i);
    expect(alert).toHaveTextContent(/knowledge persistence is unavailable/i);
    expect(screen.queryByText(/no documents in this collection/i)).not.toBeInTheDocument();
    // The count does not pretend to be zero.
    expect(screen.queryByText(/^0 documents$/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  test('retry refetches the listing and shows the documents once it succeeds', async () => {
    const spy = failDocuments(503, 'Knowledge persistence is unavailable');
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-documents'));
    await screen.findByRole('alert');

    spy.mockRestore();
    mockFetch({ documents: [{ id: 'd9', title: 'Recovered.md', source_type: 'text', chunk_count: 1 }] });
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));

    expect(await screen.findByText('Recovered.md')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
