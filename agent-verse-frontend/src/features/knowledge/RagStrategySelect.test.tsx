import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { KnowledgePage } from './KnowledgePage';
import { RagStrategySelect } from './RagStrategySelect';
import { strategiesPath } from './ragStrategies';

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

const COLLECTION = { collection_id: 'col-1', name: 'Engineering Docs', doc_count: 3 };
const RAFT_NOTE =
  'Beta: RAFT fine-tuning supports only OpenAI-compatible fine-tune providers ' +
  '(OpenAI or a vendor exposing the OpenAI fine-tuning API). Real provider fine-tune runs are not CI-verified.';

/** Backend readiness: raptor / agentic_chunking need a collection; col-1 has a RAPTOR index only. */
function strategiesFor(collectionId: string | null) {
  const collectionBound = (indexed: boolean, reason: string) =>
    collectionId === null
      ? { available: false, unavailable_reason: 'collection_index_required' }
      : indexed
        ? { available: true, unavailable_reason: null }
        : { available: false, unavailable_reason: reason };
  return {
    collection_id: collectionId,
    strategies: [
      { id: 'hybrid', name: 'Hybrid', available: true, unavailable_reason: null },
      { id: 'raptor', name: 'Raptor', ...collectionBound(true, 'requires RAPTOR indexing') },
      {
        id: 'agentic_chunking',
        name: 'Agentic Chunking',
        ...collectionBound(false, 'requires agentic-chunking indexing'),
      },
      { id: 'web_augmented', name: 'Web Augmented', available: false, unavailable_reason: 'web_search_backend_outage' },
      {
        id: 'raft',
        name: 'Raft',
        available: collectionId === 'col-1',
        unavailable_reason: collectionId === 'col-1' ? null : 'raft_model_not_deployed',
        stability: 'beta',
        stability_note: RAFT_NOTE,
      },
    ],
  };
}

function mockBackend() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    const method = init?.method ?? 'GET';
    if (url.pathname.endsWith('/rag/strategies')) return json(strategiesFor(url.searchParams.get('collection_id')));
    if (url.pathname.endsWith('/knowledge/collections')) return json([COLLECTION]);
    if (url.pathname.endsWith('/knowledge/search')) return json([]);
    if (url.pathname.endsWith('/knowledge/chat') && method === 'POST')
      return json({ answer: 'ok [1]', citations: [], collections_searched: 1, chunks_retrieved: 1, question: 'q' });
    return json({});
  });
}

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function option(name: RegExp): HTMLOptionElement {
  return screen.getByRole('option', { name }) as HTMLOptionElement;
}

function strategyCalls(spy: ReturnType<typeof mockBackend>): string[] {
  return spy.mock.calls.map(([u]) => String(u)).filter((u) => u.includes('/rag/strategies'));
}

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'tenant-key', tenantId: 'tenant-1', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('RagStrategySelect', () => {
  test('builds the per-collection readiness path', () => {
    expect(strategiesPath(null)).toBe('/rag/strategies');
    expect(strategiesPath('a b')).toBe('/rag/strategies?collection_id=a%20b');
  });

  test('without a collection, index-bound strategies say "needs a collection", not broken', async () => {
    mockBackend();
    wrap(<RagStrategySelect collectionId={null} value="hybrid" onChange={() => {}} />);
    expect(await screen.findByRole('option', { name: 'Raptor (needs a collection)' })).toBeDisabled();
    expect(option(/Agentic Chunking/)).toHaveTextContent('Agentic Chunking (needs a collection)');
    expect(option(/Web Augmented/)).toHaveTextContent('Web Augmented (unavailable)');
    expect(option(/^Hybrid$/)).not.toBeDisabled();
  });

  test('with an indexed collection, raptor is available and asked for with collection_id', async () => {
    const spy = mockBackend();
    wrap(<RagStrategySelect collectionId="col-1" value="hybrid" onChange={() => {}} />);
    expect(await screen.findByRole('option', { name: 'Raptor' })).not.toBeDisabled();
    expect(option(/Agentic Chunking/)).toHaveTextContent('collection not indexed for it');
    const calls = strategyCalls(spy);
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.every((u) => u.endsWith('/rag/strategies?collection_id=col-1'))).toBe(true);
  });

  test('RAFT is labelled Beta, and a Beta badge with its limits shows when it is selected', async () => {
    mockBackend();
    const { unmount } = wrap(<RagStrategySelect collectionId="col-1" value="hybrid" onChange={() => {}} />);
    expect(await screen.findByRole('option', { name: 'Raft (Beta)' })).not.toBeDisabled();
    expect(screen.queryByTestId('rag-strategy-beta')).toBeNull(); // hybrid selected: no badge
    unmount();

    wrap(<RagStrategySelect collectionId="col-1" value="raft" onChange={() => {}} />);
    const badge = await screen.findByTestId('rag-strategy-beta');
    expect(badge).toHaveTextContent('Beta');
    expect(badge).toHaveTextContent('only OpenAI-compatible fine-tune providers');
    expect(badge).toHaveTextContent('not CI-verified');
  });

  test('an unavailable RAFT keeps both the Beta marker and the reason', async () => {
    mockBackend();
    wrap(<RagStrategySelect collectionId={null} value="hybrid" onChange={() => {}} />);
    expect(await screen.findByRole('option', { name: 'Raft (Beta, unavailable)' })).toBeDisabled();
  });

  test('a value the backend does not offer (legacy made-up id) falls back to hybrid', async () => {
    mockBackend();
    const onChange = vi.fn();
    wrap(<RagStrategySelect collectionId={null} value="vector" onChange={onChange} />);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('hybrid'));
  });

  test('before strategies load, the current value is left alone', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>(() => {}));
    const onChange = vi.fn();
    wrap(<RagStrategySelect collectionId={null} value="hyde" onChange={onChange} />);
    expect(onChange).not.toHaveBeenCalled();
  });

  test('a strategy that becomes unavailable falls back to hybrid', async () => {
    mockBackend();
    const onChange = vi.fn();
    wrap(<RagStrategySelect collectionId={null} value="raptor" onChange={onChange} />);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('hybrid'));
  });
});

describe('KnowledgePage strategy pickers pass the selected collection', () => {
  test('Search tab: picking a collection re-checks readiness and searches with the strategy', async () => {
    const spy = mockBackend();
    const user = userEvent.setup();
    wrap(<KnowledgePage />);
    await user.click(await screen.findByTestId('tab-search'));
    expect(await screen.findByRole('option', { name: 'Raptor (needs a collection)' })).toBeInTheDocument();

    await user.selectOptions(screen.getByDisplayValue('All collections'), 'col-1');
    const raptor = await screen.findByRole('option', { name: 'Raptor' });
    expect(raptor).not.toBeDisabled();
    expect(strategyCalls(spy).some((u) => u.endsWith('/rag/strategies?collection_id=col-1'))).toBe(true);

    await user.selectOptions(screen.getByTestId('rag-strategy-select'), 'raptor');
    await user.type(screen.getByPlaceholderText(/search across your knowledge base/i), 'tariff{Enter}');
    await waitFor(() => {
      const search = spy.mock.calls.map(([u]) => String(u)).find((u) => u.includes('/knowledge/search'));
      expect(search).toContain('strategy=raptor');
      expect(search).toContain('collection_id=col-1');
    });
  });

  test('Ask AI tab: one selected collection scopes readiness; the strategy goes in the chat body', async () => {
    const spy = mockBackend();
    const user = userEvent.setup();
    wrap(<KnowledgePage />);
    await user.click(await screen.findByTestId('tab-ask'));
    expect(await screen.findByRole('option', { name: 'Raptor (needs a collection)' })).toBeInTheDocument();

    await user.click(await screen.findByRole('button', { name: 'Engineering Docs' }));
    await user.selectOptions(
      await screen.findByLabelText('Retrieval strategy'),
      (await screen.findByRole('option', { name: 'Raptor' })) as HTMLOptionElement,
    );
    await user.type(screen.getByTestId('ask-input'), 'What is the tariff?');
    await user.click(screen.getByTestId('ask-btn'));
    await waitFor(() => {
      const chat = spy.mock.calls.find(([u]) => String(u).includes('/knowledge/chat'));
      expect(JSON.parse(String((chat?.[1] as RequestInit).body))).toMatchObject({
        collection_ids: ['col-1'],
        strategy: 'raptor',
      });
    });
  });
});
