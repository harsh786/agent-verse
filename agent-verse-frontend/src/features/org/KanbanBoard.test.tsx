import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { KanbanBoard } from './KanbanBoard';

const TASKS = [
  { id: 'task-aaaaaa', title: 'Draft the spec', status: 'queued', priority: 'high' },
  { id: 'task-bbbbbb', title: 'Ship the build', status: 'running', priority: 'critical' },
  { id: 'task-cccccc', title: 'Review the PR', status: 'review', priority: 'medium' },
  { id: 'task-dddddd', title: 'Close the loop', status: 'completed', priority: 'low' },
  { id: 'task-eeeeee', title: 'Signed off task', status: 'completed', priority: 'low', approved_by: 'ceo-agent' },
];

function mockTasks(tasks: unknown[] = TASKS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tasks'))
      return new Response(JSON.stringify({ data: tasks }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderBoard(props: { orgId: string; missionId?: string } = { orgId: 'org-1' }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><KanbanBoard {...props} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('KanbanBoard', () => {
  test('renders all five columns and the task titles from the tasks endpoint', async () => {
    mockTasks();
    renderBoard();
    expect(await screen.findByText('Draft the spec')).toBeInTheDocument();
    expect(screen.getByText('Ship the build')).toBeInTheDocument();
    expect(screen.getByText('Review the PR')).toBeInTheDocument();
    // Column headers
    expect(screen.getByText('TODO')).toBeInTheDocument();
    expect(screen.getByText('IN PROGRESS')).toBeInTheDocument();
    expect(screen.getByText('REVIEW')).toBeInTheDocument();
    expect(screen.getByText('DONE')).toBeInTheDocument();
    expect(screen.getByText('APPROVED')).toBeInTheDocument();
  });

  test('an approved task surfaces in the APPROVED column (filtered by approved_by)', async () => {
    mockTasks();
    renderBoard();
    // The approved task is 'completed' AND approved — so its card renders in both
    // the DONE column and the (approved_by-filtered) APPROVED column: two articles.
    const approved = await screen.findAllByRole('article', { name: 'Task: Signed off task' });
    expect(approved).toHaveLength(2);
  });

  test('shows a loading spinner while the tasks request is in flight', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {}));
    renderBoard();
    expect(screen.getByLabelText('Loading tasks')).toBeInTheDocument();
  });

  test('every column shows the empty placeholder when there are no tasks', async () => {
    mockTasks([]);
    renderBoard();
    await waitFor(() => expect(screen.getAllByText('No tasks')).toHaveLength(5));
  });

  test('forwards mission_id as a query param when a missionId is provided', async () => {
    const spy = mockTasks();
    renderBoard({ orgId: 'org-1', missionId: 'mission-42' });
    await screen.findByText('Draft the spec');
    expect(spy.mock.calls.some(([u]) => String(u).includes('mission_id=mission-42'))).toBe(true);
  });
});
