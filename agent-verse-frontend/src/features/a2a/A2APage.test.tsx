import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { A2APage } from './A2APage';

const AGENT_CARD = {
  agent_id: 'agentverse-platform',
  name: 'AgentVerse',
  version: '2.0',
  description: 'Multi-tenant agentic OS',
  endpoint: 'http://localhost:8000',
  authentication: { scheme: 'hmac-sha256', header: 'X-A2A-Signature', note: 'HMAC' },
  capabilities: ['goal_execution', 'multi_agent'],
  supported_task_types: ['goal', 'query'],
};

const TASK = (overrides = {}) => ({
  task_id: 'task-001',
  goal: 'Deploy the service',
  status: 'accepted',
  created_at: new Date().toISOString(),
  ...overrides,
});

function mockFetch(tasks = [TASK()]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    if (url.includes('/.well-known/agent.json'))
      return new Response(JSON.stringify(AGENT_CARD), { status: 200 });
    if (url.includes('/a2a/tasks') && method === 'POST')
      return new Response(JSON.stringify({ task_id: 'new-task', status: 'accepted', message: 'Task accepted' }), { status: 202 });
    if (url.includes('/a2a/tasks'))
      return new Response(JSON.stringify(tasks), { status: 200 });
    return new Response('[]', { status: 200 });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <A2APage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  sessionStorage.setItem('av_api_key', 'test-key');
  localStorage.setItem('av_api_key', 'test-key');
  localStorage.removeItem('a2a_remote_agents');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('A2APage', () => {
  test('renders heading', () => {
    mockFetch([]);
    renderPage();
    expect(screen.getByRole('heading', { name: /a2a network/i })).toBeInTheDocument();
  });

  test('shows three tabs', () => {
    mockFetch([]);
    renderPage();
    expect(screen.getByRole('tab', { name: /tasks/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /agent card/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /remote agents/i })).toBeInTheDocument();
  });

  test('Tasks tab is default', () => {
    mockFetch([]);
    renderPage();
    expect(screen.getByRole('tab', { name: /tasks/i })).toHaveAttribute('aria-selected', 'true');
  });

  test('dispatch form renders on Tasks tab', async () => {
    mockFetch([]);
    renderPage();
    // The h3 heading "Dispatch Task" and the button
    await waitFor(() => expect(screen.getAllByText(/dispatch task/i).length).toBeGreaterThanOrEqual(1));
    expect(screen.getByLabelText(/goal/i)).toBeInTheDocument();
  });

  test('lists tasks from /a2a/tasks', async () => {
    mockFetch([TASK()]);
    renderPage();
    await waitFor(() => expect(screen.getByText('Deploy the service')).toBeInTheDocument());
    expect(screen.getByTestId('task-row')).toBeInTheDocument();
  });

  test('empty state shown when no tasks', async () => {
    mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/no tasks yet/i)).toBeInTheDocument());
  });

  test('dispatching a task calls POST /a2a/tasks', async () => {
    const fetchSpy = mockFetch([]);
    renderPage();
    await userEvent.type(screen.getByLabelText(/goal/i), 'Test task goal');
    await userEvent.click(screen.getByRole('button', { name: /dispatch task/i }));
    await waitFor(() => {
      const post = fetchSpy.mock.calls.find(([u, i]) => String(u).includes('/a2a/tasks') && (i as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
    });
  });

  test('Agent Card tab shows card data', async () => {
    mockFetch([]);
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /agent card/i }));
    await waitFor(() => expect(screen.getByText('AgentVerse')).toBeInTheDocument());
    expect(screen.getByText(/v2\.0/)).toBeInTheDocument();
  });

  test('Remote Agents tab shows empty state', async () => {
    mockFetch([]);
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /remote agents/i }));
    expect(screen.getByText(/no remote agents registered/i)).toBeInTheDocument();
  });

  // ── Tasks tab: form fields, live badge, refetch, expand/collapse ────────────

  test('priority select and requester/callback inputs update on change', async () => {
    mockFetch([]);
    renderPage();
    const prioritySelect = screen.getByLabelText(/priority/i) as HTMLSelectElement;
    await userEvent.selectOptions(prioritySelect, 'critical');
    expect(prioritySelect.value).toBe('critical');

    const requesterInput = screen.getByLabelText(/requester agent id/i);
    await userEvent.type(requesterInput, 'agent-42');
    expect(requesterInput).toHaveValue('agent-42');

    const callbackInput = screen.getByLabelText(/callback url/i);
    await userEvent.type(callbackInput, 'https://cb.example.com');
    expect(callbackInput).toHaveValue('https://cb.example.com');
  });

  test('dispatch button disabled until goal has text, and shows submitted task id after success', async () => {
    mockFetch([]);
    renderPage();
    const dispatchBtn = screen.getByRole('button', { name: /dispatch task/i });
    expect(dispatchBtn).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/goal/i), 'Do the thing');
    expect(dispatchBtn).not.toBeDisabled();

    await userEvent.click(dispatchBtn);
    await waitFor(() => expect(screen.getByText(/task dispatched!/i)).toBeInTheDocument());
    expect(screen.getByText(/new-task/)).toBeInTheDocument();

    await waitFor(() => {
      const successToast = useToastStore.getState().toasts.find((t) => t.kind === 'success');
      expect(successToast?.message).toMatch(/dispatched/i);
    });
  });

  test('dispatch failure shows an error toast', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method ?? 'GET';
      if (url.includes('/a2a/tasks') && method === 'POST') {
        return new Response(JSON.stringify({ detail: 'nope' }), { status: 500 });
      }
      if (url.includes('/a2a/tasks')) return new Response('[]', { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByLabelText(/goal/i), 'Will fail');
    await userEvent.click(screen.getByRole('button', { name: /dispatch task/i }));
    await waitFor(() => {
      const errorToast = useToastStore.getState().toasts.find((t) => t.kind === 'error');
      expect(errorToast?.message).toBeTruthy();
    });
  });

  test('shows live badge and faster polling when a task is running', async () => {
    mockFetch([TASK({ status: 'running', task_id: 'task-running' })]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/live/i)).toBeInTheDocument());
  });

  test('refresh button triggers a refetch', async () => {
    const fetchSpy = mockFetch([TASK()]);
    renderPage();
    await waitFor(() => expect(screen.getByText('Deploy the service')).toBeInTheDocument());
    const callsBefore = fetchSpy.mock.calls.length;
    await userEvent.click(screen.getByRole('button', { name: /refresh tasks/i }));
    await waitFor(() => expect(fetchSpy.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  test('task row expands to show result and collapses again', async () => {
    mockFetch([TASK({ result: 'The deploy succeeded.' })]);
    renderPage();
    await waitFor(() => expect(screen.getByText('Deploy the service')).toBeInTheDocument());

    const row = screen.getByTestId('task-row');
    expect(within(row).queryByText('The deploy succeeded.')).not.toBeInTheDocument();

    const toggle = within(row).getByRole('button');
    await userEvent.click(toggle);
    expect(within(row).getByText('The deploy succeeded.')).toBeInTheDocument();

    await userEvent.click(toggle);
    expect(within(row).queryByText('The deploy succeeded.')).not.toBeInTheDocument();
  });

  test('task row formats older timestamps in minutes and hours', async () => {
    const minutesAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    const hoursAgo = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
    mockFetch([
      TASK({ task_id: 'task-min', goal: 'Minutes old task', created_at: minutesAgo }),
      TASK({ task_id: 'task-hr', goal: 'Hours old task', created_at: hoursAgo }),
    ]);
    renderPage();
    await waitFor(() => expect(screen.getByText('Minutes old task')).toBeInTheDocument());
    expect(screen.getByText(/5m ago/)).toBeInTheDocument();
    expect(screen.getByText(/3h ago/)).toBeInTheDocument();
  });

  test('copy button on submitted task id shows a checkmark after copying', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    mockFetch([]);
    renderPage();
    await userEvent.type(screen.getByLabelText(/goal/i), 'Copy me');
    await userEvent.click(screen.getByRole('button', { name: /dispatch task/i }));
    await waitFor(() => expect(screen.getByText(/task dispatched!/i)).toBeInTheDocument());

    const copyBtn = screen.getByRole('button', { name: /copy/i });
    await userEvent.click(copyBtn);
    expect(writeText).toHaveBeenCalledWith('new-task');
    await waitFor(() => expect(copyBtn.querySelector('.text-green-500')).toBeTruthy());
  });

  test('task row without a result has no expand toggle', async () => {
    mockFetch([TASK({ result: undefined })]);
    renderPage();
    await waitFor(() => expect(screen.getByText('Deploy the service')).toBeInTheDocument());
    const row = screen.getByTestId('task-row');
    expect(within(row).queryByRole('button')).not.toBeInTheDocument();
  });

  // ── Agent Card tab: loading, error, connectivity test, copy JSON ────────────

  test('Agent Card tab shows a loading spinner before data resolves', async () => {
    let resolveFetch: (r: Response) => void = () => {};
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/.well-known/agent.json')) {
        return new Promise<Response>((resolve) => { resolveFetch = resolve; });
      }
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /agent card/i }));
    expect(document.querySelector('.animate-spin')).toBeTruthy();
    resolveFetch(new Response(JSON.stringify(AGENT_CARD), { status: 200 }));
    await waitFor(() => expect(screen.getByText('AgentVerse')).toBeInTheDocument());
  });

  test('Agent Card tab shows error state when card fails to load', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/.well-known/agent.json')) return new Response('nope', { status: 500 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /agent card/i }));
    await waitFor(() => expect(screen.getByText(/failed to load agent card/i)).toBeInTheDocument());
  });

  test('Test Connectivity succeeds and shows check icon', async () => {
    mockFetch([]);
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /agent card/i }));
    await waitFor(() => expect(screen.getByText('AgentVerse')).toBeInTheDocument());

    const testBtn = screen.getByRole('button', { name: /test connectivity/i });
    await userEvent.click(testBtn);
    await waitFor(() => expect(testBtn.querySelector('.text-green-500')).toBeTruthy());
  });

  test('Test Connectivity failure shows error icon', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/.well-known/agent.json')) {
        // First call (page load) succeeds, second (connectivity check) fails.
        if (!(globalThis as { __a2aCardLoaded?: boolean }).__a2aCardLoaded) {
          (globalThis as { __a2aCardLoaded?: boolean }).__a2aCardLoaded = true;
          return new Response(JSON.stringify(AGENT_CARD), { status: 200 });
        }
        return new Response('nope', { status: 500 });
      }
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /agent card/i }));
    await waitFor(() => expect(screen.getByText('AgentVerse')).toBeInTheDocument());

    const testBtn = screen.getByRole('button', { name: /test connectivity/i });
    await userEvent.click(testBtn);
    await waitFor(() => expect(testBtn.querySelector('.text-red-500')).toBeTruthy());
  });

  test('Test Connectivity handles thrown network error', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/.well-known/agent.json')) {
        if (!(globalThis as { __a2aCardLoaded2?: boolean }).__a2aCardLoaded2) {
          (globalThis as { __a2aCardLoaded2?: boolean }).__a2aCardLoaded2 = true;
          return new Response(JSON.stringify(AGENT_CARD), { status: 200 });
        }
        throw new Error('network down');
      }
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /agent card/i }));
    await waitFor(() => expect(screen.getByText('AgentVerse')).toBeInTheDocument());

    const testBtn = screen.getByRole('button', { name: /test connectivity/i });
    await userEvent.click(testBtn);
    await waitFor(() => expect(testBtn.querySelector('.text-red-500')).toBeTruthy());
  });

  test('Copy JSON button copies the agent card and shows a success toast', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    mockFetch([]);
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /agent card/i }));
    await waitFor(() => expect(screen.getByText('AgentVerse')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /copy json/i }));
    expect(writeText).toHaveBeenCalledWith(JSON.stringify(AGENT_CARD, null, 2));
    await waitFor(() => {
      const successToast = useToastStore.getState().toasts.find((t) => t.message.includes('Agent card JSON copied'));
      expect(successToast).toBeTruthy();
    });
  });

  // ── Remote Agents tab: server-side registry (/a2a/remote-agents) ─────────

  type Row = { id: string; name: string; url: string; card: typeof AGENT_CARD | null; last_error: string | null };

  /** A fake tenant registry: the page must never fetch agent cards itself. */
  function remoteBackend(initial: Row[] = [], opts: { registerStatus?: number; registerDetail?: string; pingError?: string; listStatus?: number } = {}) {
    const rows = [...initial];
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase();
      const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
      const ping = url.match(/\/a2a\/remote-agents\/([^/]+)\/ping$/);
      if (ping && method === 'POST') {
        const r = rows.find((x) => x.id === ping[1])!;
        if (opts.pingError) r.last_error = opts.pingError;
        else { r.card = AGENT_CARD; r.last_error = null; }
        return json(r);
      }
      const one = url.match(/\/a2a\/remote-agents\/([^/]+)$/);
      if (one && method === 'DELETE') {
        rows.splice(rows.findIndex((x) => x.id === one[1]), 1);
        return new Response(null, { status: 204 });
      }
      if (url.endsWith('/a2a/remote-agents') && method === 'POST') {
        if (opts.registerStatus) return json({ detail: opts.registerDetail }, opts.registerStatus);
        const body = JSON.parse(String((init as RequestInit).body));
        const row = { id: `ra-${rows.length + 1}`, name: body.name || AGENT_CARD.name, url: body.url, card: AGENT_CARD, last_error: null };
        rows.unshift(row);
        return json(row, 201);
      }
      if (url.endsWith('/a2a/remote-agents')) {
        if (opts.listStatus) return json({ detail: 'Registry unavailable' }, opts.listStatus);
        return json({ agents: rows });
      }
      if (url.includes('/.well-known/agent.json') && !url.startsWith('https://')) return json(AGENT_CARD);
      if (url.includes('/a2a/tasks')) return json([]);
      if (url.startsWith('https://')) throw new Error('the browser must not fetch remote agent cards');
      return json([]);
    });
    return { rows, spy };
  }

  test('opens and cancels the register-agent modal', async () => {
    remoteBackend();
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /remote agents/i }));
    await userEvent.click(screen.getByRole('button', { name: /register agent/i }));
    expect(screen.getByText(/register remote agent/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(screen.queryByText(/register remote agent/i)).not.toBeInTheDocument();
  });

  test('registers via POST /a2a/remote-agents (server validates the card) and lists it', async () => {
    const { spy } = remoteBackend();
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /remote agents/i }));
    await userEvent.click(screen.getByRole('button', { name: /register agent/i }));

    await userEvent.type(screen.getByPlaceholderText(/agent.example.com\/.well-known/i), 'https://remote.example.com/.well-known/agent.json');
    await userEvent.type(screen.getByPlaceholderText(/my agent/i), 'Remote One');
    await userEvent.click(screen.getByRole('button', { name: /^register$/i }));

    await waitFor(() => expect(screen.getByText('Remote One')).toBeInTheDocument());
    expect(screen.getByText(/v2\.0/)).toBeInTheDocument();
    expect(screen.getByText(/2 task types/i)).toBeInTheDocument();
    const post = spy.mock.calls.find(([u, i]) => String(u).endsWith('/a2a/remote-agents') && (i as RequestInit)?.method === 'POST');
    expect(JSON.parse(String((post![1] as RequestInit).body))).toEqual({
      url: 'https://remote.example.com/.well-known/agent.json', name: 'Remote One',
    });
    // Nothing is kept in browser storage any more.
    expect(localStorage.getItem('a2a_remote_agents')).toBeNull();
  });

  test('a refused registration (422) shows the reason and stores nothing', async () => {
    remoteBackend([], { registerStatus: 422, registerDetail: 'Agent card must be a JSON object with a non-empty name' });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /remote agents/i }));
    await userEvent.click(screen.getByRole('button', { name: /register agent/i }));
    await userEvent.type(screen.getByPlaceholderText(/agent.example.com\/.well-known/i), 'https://bad-agent.example.com/card.json');
    await userEvent.click(screen.getByRole('button', { name: /^register$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/non-empty name/);
    expect(screen.getByText(/register remote agent/i)).toBeInTheDocument();
  });

  test('a failing registry list renders an error, not the empty state', async () => {
    remoteBackend([], { listStatus: 503 });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /remote agents/i }));
    expect(await screen.findByText(/could not load remote agents/i)).toBeInTheDocument();
    expect(screen.queryByText(/no remote agents registered/i)).not.toBeInTheDocument();
  });

  test('pings a registered agent through the server and removes it', async () => {
    const { rows } = remoteBackend([{ id: 'ra-1', name: 'Existing Agent', url: 'https://existing.example.com/card.json', card: null, last_error: null }]);
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /remote agents/i }));
    expect(await screen.findByText('Existing Agent')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /ping/i }));
    await waitFor(() => expect(screen.getByText(/v2\.0/)).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /remove/i }));
    await waitFor(() => expect(screen.queryByText('Existing Agent')).not.toBeInTheDocument());
    expect(rows).toHaveLength(0);
  });

  test('a failed ping shows the recorded server error', async () => {
    remoteBackend([{ id: 'ra-1', name: 'Flaky Agent', url: 'https://flaky.example.com/card.json', card: null, last_error: null }], { pingError: 'Agent card could not be fetched from that URL' });
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /remote agents/i }));
    await screen.findByText('Flaky Agent');
    await userEvent.click(screen.getByRole('button', { name: /ping/i }));
    await waitFor(() => expect(screen.getByText(/could not be fetched/i)).toBeInTheDocument());
  });

  test('dispatching from a remote agent card switches to the Tasks tab', async () => {
    remoteBackend([{ id: 'ra-1', name: 'Dispatch Target', url: 'https://dispatch.example.com/card.json', card: AGENT_CARD, last_error: null }]);
    renderPage();
    await userEvent.click(screen.getByRole('tab', { name: /remote agents/i }));
    expect(await screen.findByText('Dispatch Target')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /dispatch/i }));
    expect(screen.getByRole('tab', { name: /tasks/i })).toHaveAttribute('aria-selected', 'true');
  });
});
