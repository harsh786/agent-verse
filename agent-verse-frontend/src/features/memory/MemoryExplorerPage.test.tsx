import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { MemoryExplorerPage } from './MemoryExplorerPage';

// ConfirmModal (delete / clear-all flows) uses framer-motion's AnimatePresence.
// jsdom has no real rAF/animation completion, so stub framer-motion the same
// way sibling feature tests in this repo do: a memoized per-tag stub (not a
// fresh component per render, which would remount and lose state).
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => React.ReactElement>();
  const makeStub = (tag: string) => {
    let stub = stubCache.get(tag);
    if (!stub) {
      stub = ({ children, ...props }) => React.createElement(tag, props as Record<string, unknown>, children);
      stubCache.set(tag, stub);
    }
    return stub;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

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

  test('goal id filter form requests /memory/records with goalId, and clears', async () => {
    const spy = mockRecordsFetch();
    renderPage();
    await screen.findByText(/Recovered from a failed deploy/);

    await userEvent.type(screen.getByPlaceholderText(/filter by goal id/i), 'goal-abc123');
    await userEvent.keyboard('{Enter}');

    await waitFor(() => {
      const called = spy.mock.calls.some(([u]) => /\/memory\/records\?.*goal_id=goal-abc123/.test(String(u)));
      expect(called).toBe(true);
    });

    // Clear button appears once a goal filter is active.
    await userEvent.click(screen.getByRole('button', { name: /clear goal filter/i }));
    expect(screen.getByPlaceholderText(/filter by goal id/i)).toHaveValue('');
  });

  test('renders records missing optional fields (no goal link, no expiry)', async () => {
    mockRecordsFetch((url) => {
      if (!url.includes('/memory/records')) return null;
      return new Response(
        JSON.stringify({
          records: [
            {
              memory_id: 'r9', memory_kind: 'prospective', content: 'A record with no goal or expiry',
              source_goal_id: null, source_execution_id: null, classification: 'internal',
              confidence: 5000, lifecycle_state: 'active', evidence_refs: [],
              recall_count: 0, helpful_count: 0, harmful_count: 0,
              expires_at: null, created_at: '2026-06-01T00:00:00Z', updated_at: '2026-06-01T00:00:00Z',
            },
          ],
          total: 1,
          kinds: { prospective: 1 },
        }),
        { status: 200 },
      );
    });
    renderPage();
    expect(await screen.findByText(/A record with no goal or expiry/)).toBeInTheDocument();
    expect(screen.queryByText(/goal:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/expires /)).not.toBeInTheDocument();
  });
});

// ── Edit Memory flow ────────────────────────────────────────────────────────

describe('MemoryExplorerPage — Edit Memory', () => {
  test('opens the edit modal pre-filled, edits and saves successfully', async () => {
    const fetchSpy = mockFetch([MOCK_MEMORY]);
    renderPage();
    await screen.findByText(/API key rotates monthly/);

    await userEvent.click(screen.getByRole('button', { name: /edit memory/i }));
    const heading = screen.getByRole('heading', { name: /edit memory/i });
    expect(heading).toBeInTheDocument();

    const contentBox = screen.getByLabelText(/content/i) as HTMLTextAreaElement;
    expect(contentBox.value).toBe(MOCK_MEMORY.content);

    await userEvent.clear(contentBox);
    await userEvent.type(contentBox, 'Updated memory content');

    const tagsBox = screen.getByLabelText(/tags/i) as HTMLInputElement;
    expect(tagsBox.value).toBe('ops');
    await userEvent.clear(tagsBox);
    await userEvent.type(tagsBox, 'ops, deploy');

    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      const patchCall = fetchSpy.mock.calls.find(
        ([u, i]) => /\/memory\/m1$/.test(String(u)) && ((i as RequestInit)?.method === 'PUT' || (i as RequestInit)?.method === 'PATCH')
      );
      expect(patchCall).toBeTruthy();
    });
    expect(screen.queryByRole('heading', { name: /edit memory/i })).not.toBeInTheDocument();
  });

  test('shows an error toast when updating a memory fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method;
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (method && method !== 'GET' && /\/memory\/m1$/.test(url))
        return new Response('boom', { status: 500 });
      if (url.match(/\/memory\?/)) return new Response(JSON.stringify([MOCK_MEMORY]), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await screen.findByText(/API key rotates monthly/);

    await userEvent.click(screen.getByRole('button', { name: /edit memory/i }));
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true);
    });
    // Modal stays open on failure.
    expect(screen.getByRole('heading', { name: /edit memory/i })).toBeInTheDocument();
  });

  test('closes the edit modal via the close (X) button without saving', async () => {
    mockFetch([MOCK_MEMORY]);
    renderPage();
    await screen.findByText(/API key rotates monthly/);

    await userEvent.click(screen.getByRole('button', { name: /edit memory/i }));
    await userEvent.click(screen.getByRole('button', { name: /^close$/i }));
    expect(screen.queryByRole('heading', { name: /edit memory/i })).not.toBeInTheDocument();
  });

  test('edit modal type select and confidence slider can be changed', async () => {
    mockFetch([MOCK_MEMORY]);
    renderPage();
    await screen.findByText(/API key rotates monthly/);
    await userEvent.click(screen.getByRole('button', { name: /edit memory/i }));

    const typeSelect = screen.getByLabelText(/^type$/i) as HTMLSelectElement;
    await userEvent.selectOptions(typeSelect, 'skill');
    expect(typeSelect.value).toBe('skill');

    expect(screen.getByLabelText(/confidence:/i)).toBeInTheDocument();
    expect(screen.getByText(/confidence: 90%/i)).toBeInTheDocument();
  });
});

// ── Add Memory error / cancel paths ─────────────────────────────────────────

describe('MemoryExplorerPage — Add Memory error & cancel', () => {
  test('shows an error toast when creating a memory fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method;
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (method === 'POST' && url.match(/\/memory\/?$/)) return new Response('nope', { status: 500 });
      if (url.match(/\/memory\?/)) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await screen.findByText(/no memories yet/i);
    await userEvent.click(screen.getByRole('button', { name: /add memory/i }));
    await userEvent.type(screen.getByLabelText(/content/i), 'Something to remember');
    await userEvent.click(screen.getByRole('button', { name: /create memory/i }));

    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true);
    });
    expect(screen.getByRole('heading', { name: /add memory/i })).toBeInTheDocument();
  });

  test('cancel button and backdrop click close the add modal', async () => {
    mockFetch([]);
    renderPage();
    await screen.findByText(/no memories yet/i);

    await userEvent.click(screen.getByRole('button', { name: /add memory/i }));
    expect(screen.getByRole('heading', { name: /add memory/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByRole('heading', { name: /add memory/i })).not.toBeInTheDocument();
  });

  test('add modal disables submit until content is entered, and edits type/tags/confidence', async () => {
    mockFetch([]);
    renderPage();
    await screen.findByText(/no memories yet/i);
    await userEvent.click(screen.getByRole('button', { name: /add memory/i }));

    const createButton = screen.getByRole('button', { name: /create memory/i });
    expect(createButton).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/content/i), 'A new fact');
    expect(createButton).not.toBeDisabled();

    const typeSelect = screen.getByLabelText(/^type$/i) as HTMLSelectElement;
    await userEvent.selectOptions(typeSelect, 'preference');
    expect(typeSelect.value).toBe('preference');

    await userEvent.type(screen.getByLabelText(/tags/i), 'ops, api');
    expect(screen.getByLabelText(/tags/i)).toHaveValue('ops, api');
  });
});

// ── Delete / Clear-all cancel and error paths ───────────────────────────────

describe('MemoryExplorerPage — Delete & Clear-all', () => {
  test('cancelling the delete confirm modal keeps the memory', async () => {
    mockFetch([MOCK_MEMORY]);
    renderPage();
    await screen.findByText(/API key rotates monthly/);
    await userEvent.click(screen.getByRole('button', { name: /delete memory/i }));
    expect(screen.getByText(/delete memory\?/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByText(/delete memory\?/i)).not.toBeInTheDocument();
    expect(await screen.findByText(/API key rotates monthly/)).toBeInTheDocument();
  });

  test('shows an error toast when deleting a memory fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method;
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (method === 'DELETE' && /\/memory\/m1$/.test(url)) return new Response('nope', { status: 500 });
      if (url.match(/\/memory\?/)) return new Response(JSON.stringify([MOCK_MEMORY]), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await screen.findByText(/API key rotates monthly/);
    await userEvent.click(screen.getByRole('button', { name: /delete memory/i }));
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));

    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true);
    });
  });

  test('cancelling clear-all keeps memories, and a failed clear-all shows an error toast', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method;
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (method === 'DELETE' && url.match(/\/memory\/?$/)) return new Response('nope', { status: 500 });
      if (url.match(/\/memory\?/)) return new Response(JSON.stringify([MOCK_MEMORY]), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await screen.findByText(/API key rotates monthly/);

    await userEvent.click(screen.getByRole('button', { name: /clear all/i }));
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByText(/clear all memories\?/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /clear all/i }));
    await userEvent.click(screen.getByRole('button', { name: /^clear all$/i }));

    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true);
    });
  });
});

// ── Recall error / clear paths ───────────────────────────────────────────────

describe('MemoryExplorerPage — Recall extra paths', () => {
  test('shows an error toast when recall fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (url.includes('/memory/recall')) return new Response('boom', { status: 500 });
      if (url.match(/\/memory\?/)) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByPlaceholderText(/recall memories/i), 'keys');
    await userEvent.click(screen.getByRole('button', { name: /recall/i }));

    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true);
    });
  });

  test('recall with no matches shows the "no relevant memories" message, and clear resets it', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (url.includes('/memory/recall'))
        return new Response(JSON.stringify({ query: 'x', results: [] }), { status: 200 });
      if (url.match(/\/memory\?/)) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    const input = screen.getByPlaceholderText(/recall memories/i);
    await userEvent.type(input, 'nothing here');
    await userEvent.click(screen.getByRole('button', { name: /recall/i }));
    expect(await screen.findByText(/no relevant memories found/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /clear results/i }));
    expect(screen.queryByText(/no relevant memories found/i)).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText(/recall memories/i)).toHaveValue('');
  });

  test('recall result renders its source snippet', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (url.includes('/memory/recall'))
        return new Response(
          JSON.stringify({
            query: 'x',
            results: [{ content: 'recalled with source', confidence: 0.65, memory_type: 'skill', source: 'goal-123456789' }],
          }),
          { status: 200 },
        );
      if (url.match(/\/memory\?/)) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByPlaceholderText(/recall memories/i), 'src test');
    await userEvent.click(screen.getByRole('button', { name: /recall/i }));
    expect(await screen.findByText('recalled with source')).toBeInTheDocument();
    expect(screen.getByText(/src: goal-123456/)).toBeInTheDocument();
  });
});

// ── Pagination, legacy responses, and remaining branches ────────────────────

describe('MemoryExplorerPage — Pagination & response shapes', () => {
  test('shows pagination controls when total exceeds the page size', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (url.match(/\/memory\?/)) {
        const items = Array.from({ length: 20 }, (_, i) => ({
          ...MOCK_MEMORY,
          id: `m${i}`,
          content: `Memory number ${i}`,
        }));
        return new Response(JSON.stringify({ items, total: 45 }), { status: 200 });
      }
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await screen.findByText('Memory number 0');
    expect(screen.getByRole('navigation', { name: /pagination/i })).toBeInTheDocument();
  });

  test('supports legacy flat-array /memory responses (no items/total envelope)', async () => {
    mockFetch([MOCK_MEMORY]);
    renderPage();
    expect(await screen.findByText(/API key rotates monthly/)).toBeInTheDocument();
  });

  test('handles a non-array, non-enveloped /memory response gracefully (empty state)', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (url.match(/\/memory\?/)) return new Response(JSON.stringify({ weird: true }), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText(/no memories yet/i)).toBeInTheDocument();
  });

  test('memory row renders even without tags or created_at', async () => {
    mockFetch([{ id: 'm2', content: 'Bare memory', memory_type: 'observation', confidence: 0.5, tags: [], created_at: '' }]);
    renderPage();
    expect(await screen.findByText('Bare memory')).toBeInTheDocument();
  });
});

// ── Tool reliability color thresholds & execution memory failure branch ─────

describe('MemoryExplorerPage — Tool reliability thresholds & exec memory failure', () => {
  test('renders low (<50%) and mid (50-69%) reliability rows with failure counts', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability'))
        return new Response(
          JSON.stringify([
            { tool_name: 'flaky_tool', success_count: 3, failure_count: 7, total_calls: 10, success_rate: 0.3 },
            { tool_name: 'mid_tool', success_count: 6, failure_count: 4, total_calls: 10, success_rate: 0.6 },
            { tool_name: 'clean_tool', success_count: 10, failure_count: 0, total_calls: 10, success_rate: 1 },
          ]),
          { status: 200 },
        );
      if (url.includes('/memory?')) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText('flaky_tool')).toBeInTheDocument();
    expect(within(screen.getByText('flaky_tool').closest('tr')!).getByText('30%')).toBeInTheDocument();
    expect(screen.getByText('mid_tool')).toBeInTheDocument();
    expect(within(screen.getByText('clean_tool').closest('tr')!).getByText('0')).toBeInTheDocument();
  });

  test('execution memory list shows a failed plan with the failed badge', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (url.includes('/memory/execution'))
        return new Response(
          JSON.stringify([{ goal_text: 'Rollback the release', success: false, recorded_at: '2026-06-01T00:00:00Z' }]),
          { status: 200 },
        );
      if (url.match(/\/memory\?/)) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByText(/execution memory/i));
    expect(await screen.findByText('Rollback the release')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  test('execution memory panel collapses again when toggled twice', async () => {
    mockFetch([]);
    renderPage();
    const toggle = screen.getByText(/execution memory/i);
    await userEvent.click(toggle);
    expect(await screen.findByText('Deploy the service')).toBeInTheDocument();
    await userEvent.click(toggle);
    await waitFor(() => {
      expect(screen.queryByText('Deploy the service')).not.toBeInTheDocument();
    });
  });

  test('shows empty state when there are no execution memories', async () => {
    mockFetch([]);
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/memory/tool-reliability')) return new Response('[]', { status: 200 });
      if (url.includes('/memory/execution')) return new Response('[]', { status: 200 });
      if (url.match(/\/memory\?/)) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByText(/execution memory/i));
    expect(await screen.findByText(/no execution memories/i)).toBeInTheDocument();
  });
});
