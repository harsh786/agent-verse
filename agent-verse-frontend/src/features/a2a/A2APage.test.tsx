import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
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
});
