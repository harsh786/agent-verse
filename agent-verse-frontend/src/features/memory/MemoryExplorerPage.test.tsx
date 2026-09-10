import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MemoryExplorerPage } from './MemoryExplorerPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MemoryExplorerPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const MOCK_MEMORY = {
  id: 'm1',
  content: 'Remember the API key rotates monthly',
  memory_type: 'fact',
  confidence: 0.9,
  tags: ['ops'],
  created_at: '2026-06-01T00:00:00Z',
};

function mockFetch(memories: typeof MOCK_MEMORY[] = [MOCK_MEMORY]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/memory/tool-reliability'))
      return new Response('[]', { status: 200 });
    if (url.includes('/memory/recall'))
      return new Response(
        JSON.stringify({ query: 'test', results: [{ content: 'recalled result', confidence: 0.8, memory_type: 'fact', source: '' }] }),
        { status: 200 }
      );
    if (url.includes('/memory/execution'))
      return new Response(
        JSON.stringify([{ goal_text: 'Deploy the service', success: true, recorded_at: '2026-06-01T00:00:00Z' }]),
        { status: 200 }
      );
    if (url.match(/\/memory\?/))
      return new Response(JSON.stringify(memories), { status: 200 });
    return new Response('[]', { status: 200 });
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  sessionStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MemoryExplorerPage', () => {
  test('renders page heading and all main sections', async () => {
    mockFetch([]);
    renderPage();
    expect(screen.getByRole('heading', { name: /memory explorer/i })).toBeInTheDocument();
    // Section headings (h2) must be present
    await waitFor(() => {
      const headings = screen.getAllByRole('heading');
      const texts = headings.map(h => h.textContent?.toLowerCase() ?? '');
      expect(texts.some(t => t.includes('semantic recall'))).toBe(true);
      expect(texts.some(t => t.includes('long-term memories'))).toBe(true);
      expect(texts.some(t => t.includes('tool reliability'))).toBe(true);
    });
  });

  test('lists memories from /memory with content and type badge', async () => {
    mockFetch([MOCK_MEMORY]);
    renderPage();
    expect(await screen.findByText(/API key rotates monthly/)).toBeInTheDocument();
    // 'fact' appears as both a filter pill and a type badge — check at least one instance
    expect(screen.getAllByText('fact').length).toBeGreaterThanOrEqual(1);
  });

  test('shows tags as chips', async () => {
    mockFetch([MOCK_MEMORY]);
    renderPage();
    await screen.findByText(/API key rotates monthly/);
    expect(screen.getByText('#ops')).toBeInTheDocument();
  });

  test('shows empty state when no memories', async () => {
    mockFetch([]);
    renderPage();
    expect(await screen.findByText(/no memories yet/i)).toBeInTheDocument();
  });

  test('recall queries /memory/recall and shows results', async () => {
    mockFetch();
    renderPage();
    await userEvent.type(screen.getByPlaceholderText(/recall memories/i), 'keys');
    await userEvent.click(screen.getByRole('button', { name: /recall/i }));
    expect(await screen.findByText('recalled result')).toBeInTheDocument();
  });

  test('delete calls DELETE /memory/{id}', async () => {
    const fetchSpy = mockFetch([MOCK_MEMORY]);
    renderPage();
    await screen.findByText(/API key rotates monthly/);
    await userEvent.click(screen.getByRole('button', { name: /delete memory/i }));
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));
    await waitFor(() => {
      const delCall = fetchSpy.mock.calls.find(
        ([u, i]) => /\/memory\/m1$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE'
      );
      expect(delCall).toBeTruthy();
    });
  });

  test('type filter pills render', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /^all$/i })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /^fact$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^skill$/i })).toBeInTheDocument();
  });

  test('Add Memory button opens create modal', async () => {
    mockFetch([]);
    renderPage();
    await screen.findByText(/no memories yet/i);
    await userEvent.click(screen.getByRole('button', { name: /add/i }));
    expect(screen.getByRole('heading', { name: /add memory/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/content/i)).toBeInTheDocument();
  });

  test('tool reliability table shows correct columns', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability'))
        return new Response(
          JSON.stringify([
            { tool_name: 'jira_search', success_count: 8, failure_count: 2, total_calls: 10, success_rate: 0.8 },
          ]),
          { status: 200 }
        );
      if (url.includes('/memory?'))
        return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText('jira_search')).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument(); // total_calls
    expect(screen.getByText('80%')).toBeInTheDocument();
  });

  test('execution memory section expands on click', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/execution memory/i)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/execution memory/i));
    expect(await screen.findByText('Deploy the service')).toBeInTheDocument();
  });

  test('clear all button opens confirm modal', async () => {
    mockFetch([MOCK_MEMORY]);
    renderPage();
    await screen.findByText(/API key rotates monthly/);
    await userEvent.click(screen.getByRole('button', { name: /clear all/i }));
    expect(screen.getByText(/clear all memories/i)).toBeInTheDocument();
  });
});

// ── Governed Records (canonical memory_records) ────────────────────────────────

const RECORDS_RESPONSE = {
  records: [
    {
      memory_id: 'r1', memory_kind: 'episodic', content: 'Recovered from a failed deploy',
      source_goal_id: 'goal-abc123', source_execution_id: 'e1', classification: 'internal',
      confidence: 8500, lifecycle_state: 'active', evidence_refs: ['ev://1'],
      recall_count: 0, helpful_count: 0, harmful_count: 0,
      expires_at: '2026-12-01T00:00:00Z', created_at: '2026-06-01T00:00:00Z', updated_at: '2026-06-01T00:00:00Z',
    },
    {
      memory_id: 'r2', memory_kind: 'procedural', content: 'jira → github tool sequence works',
      source_goal_id: 'goal-abc123', source_execution_id: 'e2', classification: 'internal',
      confidence: 9000, lifecycle_state: 'active', evidence_refs: ['ev://2'],
      recall_count: 0, helpful_count: 0, harmful_count: 0,
      expires_at: '2026-12-01T00:00:00Z', created_at: '2026-06-01T00:00:00Z', updated_at: '2026-06-01T00:00:00Z',
    },
    {
      memory_id: 'r3', memory_kind: 'reflexion', content: 'Add stronger evidence next time',
      source_goal_id: 'goal-xyz', source_execution_id: 'e3', classification: 'internal',
      confidence: 7000, lifecycle_state: 'active', evidence_refs: ['ev://3'],
      recall_count: 0, helpful_count: 0, harmful_count: 0,
      expires_at: null, created_at: '2026-06-01T00:00:00Z', updated_at: '2026-06-01T00:00:00Z',
    },
  ],
  total: 3,
  kinds: { episodic: 1, procedural: 1, reflexion: 1 },
};

function mockRecordsFetch(handler?: (url: string) => Response | null) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const custom = handler?.(url);
    if (custom) return custom;
    if (url.includes('/memory/records'))
      return new Response(JSON.stringify(RECORDS_RESPONSE), { status: 200 });
    if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
    if (url.match(/\/memory\?/)) return new Response('[]', { status: 200 });
    return new Response('[]', { status: 200 });
  });
}

describe('MemoryExplorerPage — Governed Records', () => {
  test('renders categorized records with kind badges and goal-linkage', async () => {
    mockRecordsFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /governed records/i })).toBeInTheDocument();
    // Real content from the read API.
    expect(await screen.findByText(/Recovered from a failed deploy/)).toBeInTheDocument();
    expect(screen.getByText(/jira → github tool sequence/)).toBeInTheDocument();
    // Goal-linkage rendered (source_goal_id).
    expect(screen.getAllByText(/goal:goal-abc123/).length).toBeGreaterThanOrEqual(1);
  });

  test('filtering by kind requests /memory/records with that kind', async () => {
    const spy = mockRecordsFetch();
    renderPage();
    await screen.findByText(/Recovered from a failed deploy/);
    // Click the "procedural" kind pill inside the governed records filter row.
    await userEvent.click(screen.getByRole('button', { name: /^procedural$/i }));
    await waitFor(() => {
      const called = spy.mock.calls.some(([u]) => /\/memory\/records\?.*kind=procedural/.test(String(u)));
      expect(called).toBe(true);
    });
  });

  test('shows honest empty state when no governed records', async () => {
    mockRecordsFetch((url) =>
      url.includes('/memory/records')
        ? new Response(JSON.stringify({ records: [], total: 0, kinds: {} }), { status: 200 })
        : null,
    );
    renderPage();
    expect(await screen.findByText(/no governed records yet/i)).toBeInTheDocument();
  });

  test('shows error state when the records API fails', async () => {
    mockRecordsFetch((url) =>
      url.includes('/memory/records') ? new Response('nope', { status: 500 }) : null,
    );
    renderPage();
    expect(await screen.findByText(/failed to load governed records/i)).toBeInTheDocument();
  });
});
