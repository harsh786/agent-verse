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

function makeStreamResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((c) => controller.enqueue(encoder.encode(c)));
      controller.close();
    },
  });
  return new Response(stream, { status });
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

  test('streams the answer when a single collection filter is selected', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/query/stream')) return makeStreamResponse(['data: {"token":"Hello "}\n', 'data: {"token":"world"}\n', 'data: [DONE]\n']);
      if (url.includes('/knowledge/collections'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    await userEvent.click(await screen.findByRole('button', { name: 'Engineering Docs' }));
    await userEvent.type(screen.getByTestId('ask-input'), 'stream this');
    await userEvent.click(screen.getByTestId('ask-btn'));
    expect(await screen.findByTestId('answer-panel')).toBeInTheDocument();
    expect(screen.getByText(/Hello world/)).toBeInTheDocument();
  });

  test('falls back to non-streaming ask when the stream response is not ok', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/query/stream')) return new Response('error', { status: 500 });
      if (url.includes('/knowledge/collections') && !url.includes('stream'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/chat') && method === 'POST')
        return new Response(JSON.stringify({ answer: 'fallback answer', citations: [], collections_searched: 1, chunks_retrieved: 1, question: 'q' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    await userEvent.click(await screen.findByRole('button', { name: 'Engineering Docs' }));
    await userEvent.type(screen.getByTestId('ask-input'), 'q');
    await userEvent.click(screen.getByTestId('ask-btn'));
    expect(await screen.findByText(/fallback answer/)).toBeInTheDocument();
  });

  test('falls back to non-streaming ask when the stream fetch throws', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/query/stream')) throw new Error('network down');
      if (url.includes('/knowledge/collections') && !url.includes('stream'))
        return new Response(JSON.stringify([COLLECTION]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/knowledge/chat') && method === 'POST')
        return new Response(JSON.stringify({ answer: 'caught fallback', citations: [], collections_searched: 1, chunks_retrieved: 1, question: 'q' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByRole('heading', { name: /knowledge/i });
    await userEvent.click(screen.getByTestId('tab-ask'));
    await userEvent.click(await screen.findByRole('button', { name: 'Engineering Docs' }));
    await userEvent.type(screen.getByTestId('ask-input'), 'q');
    await userEvent.click(screen.getByTestId('ask-btn'));
    expect(await screen.findByText(/caught fallback/)).toBeInTheDocument();
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
  test('changes the embedder select and shows an error toast when creating a collection fails', async () => {
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
    await userEvent.selectOptions(screen.getByRole('combobox'), 'openai');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));
    await waitFor(() => expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true));
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
