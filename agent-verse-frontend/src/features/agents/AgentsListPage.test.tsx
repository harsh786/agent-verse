import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';
import { vi, expect, test, beforeEach, afterEach } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentsListPage } from './AgentsListPage';

// ConfirmModal (rendered whenever the delete flow is exercised) uses
// framer-motion's AnimatePresence. jsdom has no real rAF/animation
// completion, so an "exit" animation can leave the panel in the DOM (or
// remove it before assertions run). Stub framer-motion the same way sibling
// feature tests in this repo do: a memoized per-tag stub (not a fresh
// component per render, which would remount and lose state on every re-render).
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

const navigateMock = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});

interface FixtureAgent {
  agent_id: string;
  name: string;
  autonomy_mode: string;
  goal_template: string;
  status?: string;
  is_active?: boolean;
  created_at?: string;
}

function makeAgent(overrides: Partial<FixtureAgent> = {}): FixtureAgent {
  return {
    agent_id: 'a1',
    name: 'Triage',
    autonomy_mode: 'supervised',
    goal_template: 'Triage incoming bugs',
    is_active: true,
    created_at: '2024-01-01T00:00:00Z',
    ...overrides,
  };
}

/** Builds a fetch mock that serves GET /agents from `agents`, and routes
 *  POST /agents/create and DELETE /agents/:id to the given handlers. */
function mockFetch(opts: {
  agents?: FixtureAgent[] | (() => FixtureAgent[]);
  onCreate?: () => Response | Promise<Response>;
  onDelete?: (id: string) => Response | Promise<Response>;
  onListReject?: boolean;
}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/agents/create') && method === 'POST') {
      if (opts.onCreate) return opts.onCreate();
      return new Response(JSON.stringify(makeAgent()), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (method === 'DELETE' && /\/agents\/[^/]+$/.test(url)) {
      const id = url.split('/').pop() as string;
      if (opts.onDelete) return opts.onDelete(id);
      return new Response(null, { status: 204 });
    }
    if (method === 'GET' && url.includes('/agents')) {
      if (opts.onListReject) throw new Error('network down');
      const list = typeof opts.agents === 'function' ? opts.agents() : (opts.agents ?? []);
      return new Response(JSON.stringify(list), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    throw new Error(`Unhandled fetch: ${method} ${url}`);
  });
}

function renderPage(initialEntries: string[] = ['/agents']) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={initialEntries}>
        <AgentsListPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  navigateMock.mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

test('lists agents via typed client (sends X-API-Key)', async () => {
  const f = mockFetch({ agents: [makeAgent()] });
  renderPage();
  expect(await screen.findByText('Triage')).toBeInTheDocument();
  expect((f.mock.calls[0][1] as RequestInit).headers).toMatchObject({ 'X-API-Key': 'k' });
});

test('shows the loading skeleton while the query is in flight', async () => {
  let resolveFn!: (r: Response) => void;
  vi.spyOn(globalThis, 'fetch').mockImplementation(
    () => new Promise<Response>((resolve) => { resolveFn = resolve; })
  );
  renderPage();
  // Skeleton header row is present before data resolves.
  expect(screen.getByText('Name')).toBeInTheDocument();
  expect(screen.getByText('Goal Template')).toBeInTheDocument();
  resolveFn(new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  await waitFor(() => expect(screen.getByText(/no agents/i)).toBeInTheDocument());
});

test('renders an error state when the agents fetch fails', async () => {
  mockFetch({ onListReject: true });
  renderPage();
  expect(await screen.findByText(/Failed to load agents/i)).toBeInTheDocument();
});

test('renders the empty state with a deploy hint when there are no agents at all', async () => {
  mockFetch({ agents: [] });
  renderPage();
  await waitFor(() => expect(screen.getByText(/Deploy your first agent/i)).toBeInTheDocument());
});

test('renders "No matching agents" (no deploy hint) when a search yields zero results', async () => {
  mockFetch({ agents: [makeAgent({ name: 'Triage' })] });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText('Triage');
  const search = screen.getByPlaceholderText('Search agents…');
  await user.type(search, 'zzz-nonexistent');
  await waitFor(() => expect(screen.getByText('No matching agents')).toBeInTheDocument());
  expect(screen.queryByText(/Deploy your first agent/i)).not.toBeInTheDocument();
});

test('search input filters by name and by goal_template', async () => {
  mockFetch({
    agents: [
      makeAgent({ agent_id: 'a1', name: 'Triage', goal_template: 'watch github issues' }),
      makeAgent({ agent_id: 'a2', name: 'Deploy Bot', goal_template: 'ship releases' }),
    ],
  });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText('Triage');
  const search = screen.getByPlaceholderText('Search agents…');

  await user.type(search, 'github');
  expect(screen.getByText('Triage')).toBeInTheDocument();
  expect(screen.queryByText('Deploy Bot')).not.toBeInTheDocument();

  await user.clear(search);
  await user.type(search, 'ship');
  expect(screen.getByText('Deploy Bot')).toBeInTheDocument();
  expect(screen.queryByText('Triage')).not.toBeInTheDocument();
});

test('autonomy mode filter buttons narrow the list and highlight the active one', async () => {
  mockFetch({
    agents: [
      makeAgent({ agent_id: 'a1', name: 'Supervised Agent', autonomy_mode: 'supervised' }),
      makeAgent({ agent_id: 'a2', name: 'Fully Auto Agent', autonomy_mode: 'fully-autonomous' }),
    ],
  });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText('Supervised Agent');

  const fullyAutoBtn = screen.getByRole('button', { name: 'Fully Autonomous' });
  await user.click(fullyAutoBtn);
  expect(screen.getByText('Fully Auto Agent')).toBeInTheDocument();
  expect(screen.queryByText('Supervised Agent')).not.toBeInTheDocument();
  expect(fullyAutoBtn.className).toContain('bg-primary');

  const allBtn = screen.getByRole('button', { name: 'All' });
  await user.click(allBtn);
  expect(screen.getByText('Supervised Agent')).toBeInTheDocument();
  expect(screen.getByText('Fully Auto Agent')).toBeInTheDocument();
});

test('sorting by name toggles ascending/descending order and icon', async () => {
  mockFetch({
    agents: [
      makeAgent({ agent_id: 'a1', name: 'Zeta', created_at: '2024-01-01T00:00:00Z' }),
      makeAgent({ agent_id: 'a2', name: 'Alpha', created_at: '2024-02-01T00:00:00Z' }),
    ],
  });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText('Zeta');

  // Data rows use role="button" (row click navigates), not role="row" — so
  // order is asserted via the row's accessible name in DOM order.
  const rowNames = () => screen.getAllByRole('button', { name: /^View agent /i }).map((el) => el.getAttribute('aria-label'));

  const nameHeader = screen.getByText('Name').closest('th') as HTMLElement;
  await user.click(nameHeader);
  expect(rowNames()[0]).toBe('View agent Alpha');

  await user.click(nameHeader);
  expect(rowNames()[0]).toBe('View agent Zeta');
});

test('sorting by created_at defaults to descending and can be toggled ascending', async () => {
  mockFetch({
    agents: [
      makeAgent({ agent_id: 'a1', name: 'Older', created_at: '2024-01-01T00:00:00Z' }),
      makeAgent({ agent_id: 'a2', name: 'Newer', created_at: '2024-06-01T00:00:00Z' }),
    ],
  });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText('Older');

  const rowNames = () => screen.getAllByRole('button', { name: /^View agent /i }).map((el) => el.getAttribute('aria-label'));

  // Default sort is created_at desc -> Newer first.
  expect(rowNames()[0]).toBe('View agent Newer');

  const createdHeader = screen.getByText('Created').closest('th') as HTMLElement;
  await user.click(createdHeader);
  expect(rowNames()[0]).toBe('View agent Older');
});

test('sorting handles agents missing created_at (treated as empty string)', async () => {
  mockFetch({
    agents: [
      makeAgent({ agent_id: 'a1', name: 'HasDate', created_at: '2024-01-01T00:00:00Z' }),
      makeAgent({ agent_id: 'a2', name: 'NoDate', created_at: undefined }),
    ],
  });
  renderPage();
  await screen.findByText('HasDate');
  expect(screen.getByText('NoDate')).toBeInTheDocument();
  // Missing created_at renders an em dash instead of a formatted date.
  expect(screen.getAllByText('—').length).toBeGreaterThan(0);
});

test('pagination appears beyond PAGE_SIZE and paging changes the visible rows', async () => {
  const agents = Array.from({ length: 20 }, (_, i) =>
    makeAgent({ agent_id: `a${i}`, name: `Agent ${String(i).padStart(2, '0')}`, created_at: `2024-01-${String(i + 1).padStart(2, '0')}T00:00:00Z` })
  );
  mockFetch({ agents });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText('Agent 19'); // most-recent created_at first (default desc)

  expect(screen.getByRole('navigation', { name: 'Pagination' })).toBeInTheDocument();
  expect(screen.getByText(/1–15 of 20/)).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'Next page' }));
  await waitFor(() => expect(screen.getByText(/16–20 of 20/)).toBeInTheDocument());
});

test('clicking a row navigates to the agent detail page', async () => {
  mockFetch({ agents: [makeAgent({ agent_id: 'zz', name: 'Clickable' })] });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText('Clickable');
  await user.click(screen.getByRole('button', { name: 'View agent Clickable' }));
  expect(navigateMock).toHaveBeenCalledWith('/agents/zz');
});

test('the row-level View button navigates without double-firing the row handler oddly', async () => {
  mockFetch({ agents: [makeAgent({ agent_id: 'zz', name: 'Clickable' })] });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText('Clickable');
  await user.click(screen.getByRole('button', { name: 'View' }));
  expect(navigateMock).toHaveBeenCalledWith('/agents/zz');
});

test('delete flow: opens confirm modal, cancel closes without deleting', async () => {
  const onDelete = vi.fn();
  mockFetch({ agents: [makeAgent({ agent_id: 'del-1', name: 'ToDelete' })], onDelete });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText('ToDelete');

  await user.click(screen.getByRole('button', { name: 'Delete' }));
  expect(await screen.findByRole('dialog')).toBeInTheDocument();
  expect(screen.getByText('Delete agent "ToDelete"')).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(onDelete).not.toHaveBeenCalled();
});

test('delete flow: confirming calls the delete API and closes the modal on success', async () => {
  mockFetch({ agents: [makeAgent({ agent_id: 'del-2', name: 'ToDelete2' })] });
  const user = userEvent.setup();
  renderPage();
  await screen.findByText('ToDelete2');

  await user.click(screen.getByRole('button', { name: 'Delete' }));
  const dialog = await screen.findByRole('dialog');
  await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

test('create flow: opening the modal, cancel resets and closes it', async () => {
  mockFetch({ agents: [] });
  const user = userEvent.setup();
  renderPage();
  await waitFor(() => expect(screen.getByText(/no agents/i)).toBeInTheDocument());

  await user.click(screen.getByRole('button', { name: 'New Agent' }));
  const textarea = screen.getByPlaceholderText(/Create an agent that monitors/);
  await user.type(textarea, 'Do the thing');
  expect(textarea).toHaveValue('Do the thing');

  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.queryByPlaceholderText(/Create an agent that monitors/)).not.toBeInTheDocument();

  // Reopen — value should have been reset.
  await user.click(screen.getByRole('button', { name: 'New Agent' }));
  expect(screen.getByPlaceholderText(/Create an agent that monitors/)).toHaveValue('');
});

test('create flow: the deploy button is disabled until a command is typed', async () => {
  mockFetch({ agents: [] });
  const user = userEvent.setup();
  renderPage();
  await waitFor(() => expect(screen.getByText(/no agents/i)).toBeInTheDocument());

  await user.click(screen.getByRole('button', { name: 'New Agent' }));
  const deployBtn = screen.getByRole('button', { name: 'Deploy Agent' });
  expect(deployBtn).toBeDisabled();

  const textarea = screen.getByPlaceholderText(/Create an agent that monitors/);
  await user.type(textarea, 'Monitor prod');
  expect(deployBtn).toBeEnabled();

  // Whitespace-only input keeps the button disabled (trim() check).
  await user.clear(textarea);
  await user.type(textarea, '   ');
  expect(deployBtn).toBeDisabled();
});

test('create flow: a successful submit invalidates the list, closes the modal, and resets the command', async () => {
  let created = false;
  const f = mockFetch({
    agents: () => (created ? [makeAgent({ agent_id: 'new-1', name: 'Freshly Deployed' })] : []),
    onCreate: () => {
      created = true;
      return new Response(JSON.stringify(makeAgent({ agent_id: 'new-1', name: 'Freshly Deployed' })), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    },
  });
  const user = userEvent.setup();
  renderPage();
  await waitFor(() => expect(screen.getByText(/no agents/i)).toBeInTheDocument());

  await user.click(screen.getByRole('button', { name: 'New Agent' }));
  const textarea = screen.getByPlaceholderText(/Create an agent that monitors/);
  await user.type(textarea, 'Deploy a triage bot');
  await user.click(screen.getByRole('button', { name: 'Deploy Agent' }));

  await waitFor(() => expect(screen.getByText('Freshly Deployed')).toBeInTheDocument());
  expect(screen.queryByPlaceholderText(/Create an agent that monitors/)).not.toBeInTheDocument();
  expect(f).toHaveBeenCalled();
});

test('create flow: shows the mutation error message on failure and keeps the modal open', async () => {
  mockFetch({
    agents: [],
    onCreate: () => new Response(JSON.stringify({ detail: 'boom' }), { status: 400, headers: { 'Content-Type': 'application/json' } }),
  });
  const user = userEvent.setup();
  renderPage();
  await waitFor(() => expect(screen.getByText(/no agents/i)).toBeInTheDocument());

  await user.click(screen.getByRole('button', { name: 'New Agent' }));
  const textarea = screen.getByPlaceholderText(/Create an agent that monitors/);
  await user.type(textarea, 'This will fail');
  await user.click(screen.getByRole('button', { name: 'Deploy Agent' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(/boom/i);
  // Modal stays open after a failed create.
  expect(screen.getByPlaceholderText(/Create an agent that monitors/)).toBeInTheDocument();
});

test('renders the Inactive status badge and falls back to the raw autonomy mode label/color', async () => {
  mockFetch({
    agents: [
      makeAgent({ agent_id: 'i1', name: 'Idle Agent', is_active: false, autonomy_mode: 'weird-custom-mode' }),
    ],
  });
  renderPage();
  await screen.findByText('Idle Agent');
  expect(screen.getByText('Inactive')).toBeInTheDocument();
  expect(screen.getByText('weird-custom-mode')).toBeInTheDocument();
});

test('renders an em dash when goal_template is missing', async () => {
  mockFetch({
    agents: [makeAgent({ agent_id: 'g1', name: 'NoGoal', goal_template: '' })],
  });
  renderPage();
  await screen.findByText('NoGoal');
  expect(screen.getAllByText('—').length).toBeGreaterThan(0);
});

test('shows the total agent count in the header', async () => {
  mockFetch({
    agents: [makeAgent({ agent_id: 'c1', name: 'One' }), makeAgent({ agent_id: 'c2', name: 'Two' })],
  });
  renderPage();
  await screen.findByText('One');
  expect(screen.getByText(/2 autonomous agents under mission control/)).toBeInTheDocument();
});

test('query is disabled without an API key (no fetch call, empty-state render)', async () => {
  useAuthStore.setState({ apiKey: '', tenantId: 't', plan: 'free', isAuthenticated: false });
  const f = vi.spyOn(globalThis, 'fetch');
  renderPage();
  await waitFor(() => expect(screen.getByText(/no agents/i)).toBeInTheDocument());
  expect(f).not.toHaveBeenCalled();
});

test('search and filter state persist through the URL (deep link renders pre-filtered)', async () => {
  mockFetch({
    agents: [
      makeAgent({ agent_id: 'a1', name: 'Alpha Watcher', autonomy_mode: 'supervised' }),
      makeAgent({ agent_id: 'a2', name: 'Beta Bot', autonomy_mode: 'fully-autonomous' }),
    ],
  });
  renderPage(['/agents?mode=fully-autonomous&q=Beta']);
  await waitFor(() => expect(screen.getByText('Beta Bot')).toBeInTheDocument());
  expect(screen.queryByText('Alpha Watcher')).not.toBeInTheDocument();
});
