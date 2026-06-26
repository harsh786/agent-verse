import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { KnowledgePage } from './KnowledgePage';

function renderKnowledgePage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <KnowledgePage />
    </QueryClientProvider>
  );
}

describe('KnowledgePage – Collections tab', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders page title and tab buttons', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderKnowledgePage();
    expect(screen.getByText('Knowledge')).toBeInTheDocument();
    // Tab buttons: "collections (0)", "ingest", "search"
    expect(screen.getByRole('button', { name: /^collections/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'ingest' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'search' })).toBeInTheDocument();
  });

  test('shows empty state when no collections exist', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    renderKnowledgePage();
    await waitFor(() =>
      expect(screen.getByText(/no collections yet/i)).toBeInTheDocument()
    );
  });

  test('lists existing collections in a table', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            collection_id: 'col-1',
            name: 'engineering-docs',
            doc_count: 42,
            embedder: 'openai/text-embedding-3-small',
            created_at: '2026-01-01T00:00:00Z',
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderKnowledgePage();
    await waitFor(() => expect(screen.getByText('engineering-docs')).toBeInTheDocument());
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('openai/text-embedding-3-small')).toBeInTheDocument();
  });

  test('shows create collection form when "+ New Collection" is clicked', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    );
    renderKnowledgePage();
    await waitFor(() => expect(screen.getByText(/no collections yet/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /\+ new collection/i }));
    expect(screen.getByPlaceholderText('my-knowledge-base')).toBeInTheDocument();
  });

  test('creates a collection via POST on clicking Create', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.endsWith('/knowledge/collections') && init?.method === 'POST') {
          return new Response(
            JSON.stringify({ collection_id: 'col-new', name: 'my-docs', doc_count: 0 }),
            { status: 201, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
    );

    renderKnowledgePage();
    await waitFor(() => expect(screen.getByText(/no collections yet/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /\+ new collection/i }));
    await userEvent.type(screen.getByPlaceholderText('my-knowledge-base'), 'my-docs');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/knowledge\/collections$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('deletes a collection via DELETE when Delete button is clicked', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.includes('/knowledge/collections/col-1') && init?.method === 'DELETE') {
          return new Response(null, { status: 204 });
        }
        return new Response(
          JSON.stringify([{ collection_id: 'col-1', name: 'my-docs', doc_count: 5 }]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
    );

    renderKnowledgePage();
    await waitFor(() => expect(screen.getByText('my-docs')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /delete/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/knowledge\/collections\/col-1$/),
        expect.objectContaining({ method: 'DELETE' })
      )
    );
  });
});

describe('KnowledgePage – Ingest tab', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('shows ingest form with collection select and source type', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([{ collection_id: 'col-1', name: 'my-docs' }]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderKnowledgePage();
    await userEvent.click(screen.getByText('ingest'));
    expect(screen.getByText('Ingest Document')).toBeInTheDocument();
    expect(screen.getByText('Source Type')).toBeInTheDocument();
  });

  test('Ingest button is disabled when no collection or content selected', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([{ collection_id: 'col-1', name: 'my-docs' }]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderKnowledgePage();
    await userEvent.click(screen.getByText('ingest'));
    // The form submit button has capital 'I' — distinct from tab button 'ingest'
    expect(screen.getByRole('button', { name: 'Ingest' })).toBeDisabled();
  });

  test('calls ingest API when form is submitted', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.endsWith('/knowledge/ingest') && init?.method === 'POST') {
          return new Response(
            JSON.stringify({ doc_count: 3 }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(
          JSON.stringify([{ collection_id: 'col-1', name: 'my-docs' }]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
    );

    renderKnowledgePage();
    await userEvent.click(screen.getByText('ingest'));
    // Select collection
    await waitFor(() => expect(screen.getByRole('option', { name: 'my-docs' })).toBeInTheDocument());
    await userEvent.selectOptions(
      screen.getAllByRole('combobox')[0],
      'col-1'
    );
    // Add content
    await userEvent.type(
      screen.getByPlaceholderText(/paste your document content here/i),
      'Some document content'
    );
    await userEvent.click(screen.getByRole('button', { name: 'Ingest' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/knowledge\/ingest$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('shows success message with chunk count after ingest', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/knowledge/ingest') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ doc_count: 7 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(
        JSON.stringify([{ collection_id: 'col-1', name: 'my-docs' }]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderKnowledgePage();
    await userEvent.click(screen.getByText('ingest'));
    await waitFor(() => expect(screen.getByRole('option', { name: 'my-docs' })).toBeInTheDocument());
    await userEvent.selectOptions(screen.getAllByRole('combobox')[0], 'col-1');
    await userEvent.type(
      screen.getByPlaceholderText(/paste your document content here/i),
      'Some content'
    );
    await userEvent.click(screen.getByRole('button', { name: 'Ingest' }));

    await waitFor(() =>
      expect(screen.getByText(/ingested successfully/i)).toBeInTheDocument()
    );
    expect(screen.getByText(/7 chunks indexed/i)).toBeInTheDocument();
  });
});

describe('KnowledgePage – Search tab', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('Search button is disabled when query is empty', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    );
    renderKnowledgePage();
    await userEvent.click(screen.getByText('search'));
    // Capital 'S' — distinguishes the form submit from the 'search' tab button
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled();
  });

  test('shows search results after querying', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/knowledge/search')) {
        return new Response(
          JSON.stringify([
            { doc_id: 'doc-1', content: 'Relevant document content', score: 0.9234 },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderKnowledgePage();
    await userEvent.click(screen.getByText('search'));
    await userEvent.type(
      screen.getByPlaceholderText(/search your knowledge base/i),
      'deployment guide'
    );
    await userEvent.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() =>
      expect(screen.getByText('Relevant document content')).toBeInTheDocument()
    );
    expect(screen.getByText(/0\.9234/)).toBeInTheDocument();
  });

  test('shows no results message when search returns empty', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/knowledge/search')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderKnowledgePage();
    await userEvent.click(screen.getByText('search'));
    await userEvent.type(
      screen.getByPlaceholderText(/search your knowledge base/i),
      'something obscure'
    );
    await userEvent.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() =>
      expect(screen.getByText(/no results found/i)).toBeInTheDocument()
    );
  });
});
