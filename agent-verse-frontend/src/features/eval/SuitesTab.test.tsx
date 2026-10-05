/**
 * SuitesTab — versioned golden datasets (MEM-54): the suite shows its dataset
 * version, golden tasks can be edited / removed (each a new version), and every
 * run says which dataset version it executed.
 */
import React, { type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { SuitesTab } from './SuitesTab';
import { runsRefetchInterval } from './runStatus';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const cache = new Map<string, (p: { children?: ReactNode; [k: string]: unknown }) => React.ReactElement>();
  const stub = (tag: string) => {
    let s = cache.get(tag);
    if (!s) {
      s = ({ children, ...props }) => React.createElement(tag, props as Record<string, unknown>, children);
      cache.set(tag, s);
    }
    return s;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (t, k: string) => (k in t ? t[k] : stub(k)),
    }),
  };
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const TASK = {
  task_id: 'task-a', goal: 'Find the open issues', expected_tools: ['jira.search'],
  forbidden_tools: [], expected_output_contains: ['issues'], expected_output: '',
  min_score: 0.8, max_iterations: 15, tags: [], revision: 2,
};

function mockApi() {
  const calls: Array<{ url: string; method: string; body?: unknown }> = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith('/intelligence/eval-suites') && method === 'GET') {
      return json([{ suite_id: 's1', name: 'Regression', task_count: 1, dataset_version: 2 }]);
    }
    if (url.endsWith('/intelligence/eval-suites/s1') && method === 'GET') {
      return json({ suite_id: 's1', name: 'Regression', task_count: 1, dataset_version: 2, tasks: [TASK] });
    }
    if (url.endsWith('/results')) {
      return json([{ run_id: 'r1', passed: 1, failed: 0, dataset_version: 2, task_results: [] }]);
    }
    if (url.includes('/tasks/task-a') && method === 'PATCH') {
      return json({ dataset_version: 3, task: { ...TASK, goal: 'Find open bugs', revision: 3 } });
    }
    if (url.includes('/tasks/task-a') && method === 'DELETE') {
      return json({ dataset_version: 3 });
    }
    return json({});
  });
  vi.spyOn(globalThis, 'fetch').mockImplementation(impl as unknown as typeof fetch);
  return calls;
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SuitesTab apiKey="k" />
    </QueryClientProvider>,
  );
}

describe('SuitesTab — versioned golden datasets', () => {
  beforeEach(() => {
    useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
    useToastStore.setState({ toasts: [] });
  });
  afterEach(() => vi.restoreAllMocks());

  test('the suite, its golden tasks and its runs show the dataset version', async () => {
    mockApi();
    renderTab();
    expect(await screen.findByText(/1 tasks · dataset v2/)).toBeInTheDocument();
    await userEvent.click(screen.getByText('Regression'));
    expect(await screen.findByText(/Golden Tasks · dataset v2/)).toBeInTheDocument();
    expect(screen.getByText('Find the open issues')).toBeInTheDocument();
    expect(await screen.findByTestId('run-version-r1')).toHaveTextContent('dataset v2');
  });

  test('editing a task pre-fills the form and PATCHes only through the API', async () => {
    const calls = mockApi();
    renderTab();
    await userEvent.click(await screen.findByText('Regression'));
    await userEvent.click(await screen.findByRole('button', { name: 'Edit task: Find the open issues' }));

    expect(screen.getByText('Edit Golden Task')).toBeInTheDocument();
    const goal = screen.getByLabelText('Goal') as HTMLInputElement;
    expect(goal.value).toBe('Find the open issues');
    expect((screen.getByLabelText(/expected tools/i) as HTMLInputElement).value).toBe('jira.search');

    await userEvent.clear(goal);
    await userEvent.type(goal, 'Find open bugs');
    await userEvent.click(screen.getByRole('button', { name: 'Save Task' }));

    await waitFor(() => {
      const patch = calls.find((c) => c.method === 'PATCH');
      expect(patch?.url).toMatch(/\/intelligence\/eval-suites\/s1\/tasks\/task-a$/);
      expect(patch?.body).toMatchObject({ goal: 'Find open bugs', expected_tools: ['jira.search'] });
    });
    await waitFor(() => expect(screen.queryByText('Edit Golden Task')).not.toBeInTheDocument());
  });

  test('removing a task asks for confirmation, then DELETEs it', async () => {
    const calls = mockApi();
    renderTab();
    await userEvent.click(await screen.findByText('Regression'));
    await userEvent.click(await screen.findByRole('button', { name: 'Delete task: Find the open issues' }));
    expect(screen.getByText('Remove golden task?')).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Remove Task' }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/tasks/task-a'))).toBe(true);
    });
  });
});

describe('SuitesTab — durable run progress (MEM-53)', () => {
  beforeEach(() => {
    useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
    useToastStore.setState({ toasts: [] });
  });
  afterEach(() => vi.restoreAllMocks());

  const PROGRESS = { total: 10, done: 3, passed: 2, failed: 1, unscored: 0, running: 4, pending: 3 };

  function mockRuns(runs: unknown[]) {
    vi.spyOn(globalThis, 'fetch').mockImplementation((async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/intelligence/eval-suites')) {
        return json([{ suite_id: 's1', name: 'Regression', task_count: 10, dataset_version: 2 }]);
      }
      if (url.endsWith('/intelligence/eval-suites/s1')) {
        return json({ suite_id: 's1', name: 'Regression', task_count: 1, dataset_version: 2, tasks: [TASK] });
      }
      if (url.endsWith('/results')) return json(runs);
      return json({});
    }) as unknown as typeof fetch);
  }

  test('a running run shows its live per-task progress, newest run first', async () => {
    mockRuns([
      { run_id: 'new', status: 'running', passed: 0, failed: 0, total: 10, dataset_version: 2,
        agent_id: 'agent-7', progress: PROGRESS, task_results: [] },
      { run_id: 'old', status: 'completed', passed: 9, failed: 1, total: 10, dataset_version: 1,
        task_results: [] },
    ]);
    renderTab();
    await userEvent.click(await screen.findByText('Regression'));
    const status = await screen.findByTestId('run-status-new');
    expect(status).toHaveTextContent('running · 3/10 done · 4 in flight');
    expect(screen.getByTestId('run-agent-new')).toHaveTextContent('agent-7');
    expect(screen.getByTestId('run-status-old')).toHaveTextContent('completed · 9/10 pass');
    const order = screen.getAllByTestId(/^run-status-/).map((el) => el.dataset.testid);
    expect(order).toEqual(['run-status-new', 'run-status-old']);
  });

  test('a stalled run is shown as stalled, a failed run with its error', async () => {
    mockRuns([
      { run_id: 'st', status: 'abandoned', passed: 0, failed: 0, total: 10, task_results: [] },
      { run_id: 'er', status: 'failed', passed: 0, failed: 0, total: 0, error: 'no tasks',
        task_results: [] },
    ]);
    renderTab();
    await userEvent.click(await screen.findByText('Regression'));
    expect(await screen.findByTestId('run-status-st')).toHaveTextContent(/stalled/i);
    expect(screen.getByTestId('run-status-er')).toHaveTextContent('failed · no tasks');
  });

  test('results are polled only while a run is in progress', () => {
    expect(runsRefetchInterval([{ run_id: 'a', status: 'running', passed: 0, failed: 0 }])).toBe(5000);
    expect(runsRefetchInterval([{ run_id: 'a', status: 'completed', passed: 1, failed: 0 }])).toBe(false);
    expect(runsRefetchInterval(undefined)).toBe(false);
  });
});
